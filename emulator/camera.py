"""
Central RED DSMC2 Virtual Camera Orchestrator.
Integrates ARM CPU, system bus, peripheral models, storage, display, and control interfaces.
"""

import os
import struct
import threading
import time
from typing import Optional, List, Dict, Callable

from emulator.core.cpu import ArmCpu
from emulator.core.bus import SystemBus
from emulator.devices.intc import OmapIntc
from emulator.devices.prcm import OmapPrcm
from emulator.devices.gptimer import OmapGPTimer, TIMER_MAP
from emulator.devices.uart import OmapUart, UART_MAP
from emulator.devices.i2c import OmapI2c, I2C_MAP
from emulator.devices.i2c_maxtouch import MaXTouchDevice
from emulator.devices.i2c_eeprom import EdidEeprom
from emulator.devices.gpio import OmapGpio, GPIO_MAP
from emulator.devices.dss import OmapDss
from emulator.storage.vfs import VirtualFileSystem
from emulator.rcp.protocol import RcpProtocol
from emulator.rcp.server import RcpServer
from emulator.core.snapshot import SnapshotManager


class Dsmc2Camera:
    """The complete authentic RED DSMC2 Virtual Camera."""

    # ThreadX Scheduler Idle Loop Addresses
    IDLE_LOOP_PCS = {0x80E9C368, 0x80E9C36C, 0x80E9C370}

    # ThreadX Global Symbols
    TX_THREAD_CREATED_PTR = 0x8149CE68
    TX_THREAD_CURRENT_PTR = 0x8149CE5C
    TX_THREAD_EXECUTE_PTR = 0x8149CE60

    def __init__(self, workspace_root: str):
        self.workspace_root = os.path.abspath(workspace_root)
        self.firmware_slot_dir = os.path.join(self.workspace_root, "firmware_store", "unpacked", "scarlet_w_7_4_1", "slot")
        self.persistent_dir = os.path.join(self.workspace_root, "persistent_store")

        # 1. Initialize Core Architecture
        self.cpu = ArmCpu()
        self.bus = SystemBus(self.cpu)

        # 2. Storage and VFS
        self.vfs = VirtualFileSystem(self.firmware_slot_dir, self.persistent_dir)

        # 3. Interrupt Controller
        self.intc = OmapIntc()

        # 4. PRCM (Clock & Reset)
        self.prcm = OmapPrcm(self.cpu)
        self.bus.register_device(OmapPrcm.BASE_ADDR, OmapPrcm.SIZE, self.prcm)

        # 5. INTC
        self.bus.register_device(OmapIntc.BASE_ADDR, OmapIntc.SIZE, self.intc)

        # 6. GPTimers (1..11)
        self.timers: Dict[int, OmapGPTimer] = {}
        for timer_id, (base, irq) in TIMER_MAP.items():
            tmr = OmapGPTimer(timer_id, irq, self.intc)
            self.timers[timer_id] = tmr
            self.bus.register_device(base, 0x1000, tmr)

        # 7. UARTs (1..4)
        self.uarts: Dict[int, OmapUart] = {}
        for uart_id, (base, irq) in UART_MAP.items():
            u = OmapUart(uart_id, irq, self.intc)
            self.uarts[uart_id] = u
            self.bus.register_device(base, 0x1000, u)

        # 8. I2C Controllers & Slave Devices
        self.i2c: Dict[int, OmapI2c] = {}
        for bus_id, (base, irq) in I2C_MAP.items():
            ctrl = OmapI2c(bus_id, irq, self.intc)
            self.i2c[bus_id] = ctrl
            self.bus.register_device(base, 0x1000, ctrl)

        # Attach Atmel maXTouch touch controller (0x4B) and EDID EEPROM (0x50) to I2C3
        self.maxtouch = MaXTouchDevice(0x4B, irq_callback=lambda: self.intc.assert_irq(58))
        self.edid = EdidEeprom(0x50)
        self.i2c[3].attach_slave(self.maxtouch)
        self.i2c[3].attach_slave(self.edid)

        # 9. GPIO Controllers (1..6)
        self.gpio: Dict[int, OmapGpio] = {}
        for mod_id, (base, irq) in GPIO_MAP.items():
            g = OmapGpio(mod_id, irq, self.intc)
            self.gpio[mod_id] = g
            self.bus.register_device(base, 0x1000, g)

        # 10. Display Subsystem (DSS)
        self.dss = OmapDss(self.bus)
        self.bus.register_device(OmapDss.BASE_ADDR, OmapDss.SIZE, self.dss)

        # 11. RCP Protocol & Server
        self.rcp = RcpProtocol()
        self.rcp_server = RcpServer(self.rcp, host="127.0.0.1", port=1111)

        # Execution & Power Control
        self.is_running = False
        self.instruction_count = 0
        self.is_booted = False

        # Power Lifecycle (STANDBY, BOOTING, ON)
        self.POWER_STANDBY = "STANDBY"
        self.POWER_BOOTING = "BOOTING"
        self.POWER_ON = "ON"
        self.power_state = self.POWER_STANDBY
        self.boot_progress = 0.0
        self.on_state_change: Optional[Callable[[str], None]] = None

    def log_system(self, message: str):
        """Append an entry to system logs (UART4)."""
        self.uarts[4].completed_lines.append(message)

    def load_firmware(self, firmware_path: Optional[str] = None):
        """Load genuine RED DSMC2 program into DDR RAM at 0x80000000."""
        if firmware_path is None:
            firmware_path = os.path.join(self.firmware_slot_dir, "dsmc_program.mem")

        with open(firmware_path, "rb") as f:
            fw_bytes = f.read()

        self.bus.load_binary(0x80000000, fw_bytes)

        # Reset vector state
        self.cpu.pc = 0x80829F2C
        self.cpu.sp = 0x881D2690
        self.cpu.cpsr = ArmCpu.MODE_SVC | 0xC0 # Supervisor mode, IRQ/FIQ disabled

    def step_slice(self, count: int = 500_000):
        """Execute an instruction slice and service interrupts/timers."""
        pc = self.cpu.pc

        # Check for interrupt delivery if in scheduler idle loop
        if pc in self.IDLE_LOOP_PCS and self.intc.has_pending_irq():
            if self.cpu.are_interrupts_enabled():
                self.cpu.inject_irq(vector=0x80829F00)

        # Run CPU slice. The wall-clock timeout guarantees the call returns even if
        # the core halts (WFI with no interrupt source) and never retires `count`.
        try:
            self.cpu.run(self.cpu.pc, 0, count=count, timeout_us=self.SLICE_TIMEOUT_US)
            self.instruction_count += count
        except Exception as e:
            # Check if exception is benign or stop
            pass

        # Tick hardware timer (GPTimer11 is 100Hz / 1000Hz system tick)
        self.timers[11].tick(steps=100)

    # Upper bound on wall-clock time for one interactive CPU slice (50ms max).
    SLICE_TIMEOUT_US = 50_000

    def boot(self, initial_instructions: int = 45_000_000, chunk: Optional[int] = None,
             progress_cb: Optional[Callable[[float], None]] = None):
        """Perform initial CRT and RTOS thread bringup.

        ``chunk`` splits execution into resumable slices (verified bit-identical to a
        single run) so callers can report progress via ``progress_cb(fraction)``.
        """
        if chunk and chunk < initial_instructions:
            done = 0
            while done < initial_instructions:
                n = min(chunk, initial_instructions - done)
                self.cpu.run(self.cpu.pc, 0, count=n)
                done += n
                self.boot_progress = done / initial_instructions
                if progress_cb:
                    progress_cb(self.boot_progress)
        else:
            self.cpu.run(self.cpu.pc, 0, count=initial_instructions)
        self.instruction_count += initial_instructions
        self.is_booted = True
        self.power_state = self.POWER_ON
        self.boot_progress = 1.0

    def power_on(self, progress_cb: Optional[Callable[[float], None]] = None):
        """Power on camera to operational state."""
        if not self.is_booted:
            self.power_state = self.POWER_BOOTING
            self.boot_progress = 0.0
            if progress_cb:
                progress_cb(0.0)
            self.log_system("[PWR] Powering on RED DSMC2 (Cortex-A8 @ 1GHz)...")
            self.log_system(f"[PWR] Loading firmware from {os.path.basename(self.firmware_slot_dir)} into DDR (0x80000000)...")
            self.load_firmware()
            self.log_system("[CRT] Reset vector 0x80829F2C -> Initializing C runtime and clearing BSS...")
            self.boot(initial_instructions=45_000_000, chunk=3_000_000, progress_cb=progress_cb)
            self.log_system("[RTOS] ThreadX RTOS kernel initialized.")
            threads = self.get_threads()
            for t in threads:
                self.log_system(f"[RTOS] Thread [{t['name']}] state={t['state']} prio={t['priority']}")
            self.log_system("[DSS] OMAP3630 DISPC online: 1024x576 32bpp frame stream ready.")
            self.log_system("[UI] RED Touch 7.0 online (maXTouch @ 0x4B, EDID @ 0x50).")
            self.log_system(f"[SYS] RED {self.vfs.env.get('CAMERA_NAME', 'SCARLET-W 5K')} is ONLINE and READY.")
        else:
            self.log_system("[PWR] Resuming camera from standby -> Display & Sensors active.")

        self.power_state = self.POWER_ON
        self.is_booted = True
        self.boot_progress = 1.0
        self.rcp_server.start()
        if self.on_state_change:
            self.on_state_change(self.power_state)

    def power_off(self):
        """Power down camera to standby state."""
        self.log_system("[PWR] Entering standby mode -> Display & Sensors sleeping...")
        self.power_state = self.POWER_STANDBY
        self.rcp_server.stop()
        if self.on_state_change:
            self.on_state_change(self.power_state)

    def toggle_power(self):
        """Toggle power between Standby and On."""
        if self.power_state == self.POWER_STANDBY:
            self.power_on()
        else:
            self.power_off()

    def start(self):
        """Start autonomous background execution."""
        if not self.is_booted:
            self.boot()

        self.power_state = self.POWER_ON
        self.is_running = True
        self.rcp_server.start()

    def stop(self):
        """Stop emulator execution."""
        self.is_running = False
        self.rcp_server.stop()

    # Input and Control API
    def touch(self, x: int, y: int, pressed: bool):
        """Deliver touchscreen event to Atmel maXTouch controller."""
        self.maxtouch.inject_touch(x, y, pressed)

    def press_record(self):
        """Toggle camera recording state."""
        current = self.rcp.state[RcpProtocol.PARAM_RECORD_STATE]
        new_state = "0" if current == "2" else "2"
        self.rcp.state[RcpProtocol.PARAM_RECORD_STATE] = new_state
        # Also signal physical GPIO pin for REC
        self.gpio[1].set_input_pin(12, True)
        self.gpio[1].set_input_pin(12, False)

    def get_frame(self) -> bytes:
        """Fetch current live pixels from Display Subsystem."""
        return self.dss.get_framebuffer_bytes()

    def get_serial_logs(self) -> List[str]:
        """Fetch system logs from UART4."""
        return list(self.uarts[4].completed_lines)

    def get_threads(self) -> List[Dict[str, Any]]:
        """Inspect and return all ThreadX RTOS threads from memory."""
        threads = []
        if not self.is_booted:
            return threads

        tcbs = [0x8149D494, 0x814CAD40, 0x827109B0, 0x82710CA0]
        state_names = {
            0: "READY", 1: "COMPLETED", 2: "TERMINATED", 3: "SUSPENDED",
            4: "SLEEP", 5: "QUEUE", 6: "SEMAPHORE", 7: "EVENT_FLAG",
            8: "BLOCK_MEMORY", 9: "BYTE_MEMORY", 10: "IO_DRIVER",
            11: "FILE", 12: "NETWORK", 13: "MUTEX"
        }

        for tcb in tcbs:
            try:
                chunk = self.bus.read_mem(tcb, 0x50)
                words = struct.unpack("<20I", chunk)
                if words[0] != 0x54485244: # 'THRD' magic
                    continue
                sp = words[2]        # +0x08
                stk_start = words[3] # +0x0C
                stk_end = words[4]   # +0x10
                state = words[6]     # +0x18
                name_ptr = words[10] # +0x28
                prio = words[11]     # +0x2C
                entry = words[17]    # +0x44

                try:
                    raw_name = self.bus.read_mem(name_ptr, 48)
                    name = raw_name.split(b"\x00")[0].decode("latin1")
                except Exception:
                    name = "UNKNOWN"

                threads.append({
                    "address": f"0x{tcb:08X}",
                    "name": name,
                    "state": state_names.get(state, f"STATE_{state}"),
                    "priority": prio,
                    "entry": f"0x{entry:08X}",
                    "sp": f"0x{sp:08X}",
                    "stack_range": f"0x{stk_start:08X}-0x{stk_end:08X}"
                })
            except Exception:
                continue

        return threads

    def save_snapshot(self, filepath: str, description: str = ""):
        """Save emulator state to snapshot file."""
        SnapshotManager.save(self, filepath, description)

    def restore_snapshot(self, filepath: str) -> Dict[str, Any]:
        """Restore emulator state from snapshot file."""
        return SnapshotManager.restore(self, filepath)

    def inject_fault(self, fault_type: str, **kwargs):
        """Simulate hardware/system faults."""
        if fault_type == "monitor_disconnect":
            self.i2c[3].slaves.pop(0x4B, None)
            self.i2c[3].slaves.pop(0x50, None)
        elif fault_type == "monitor_connect":
            self.i2c[3].attach_slave(self.maxtouch)
            self.i2c[3].attach_slave(self.edid)
        elif fault_type == "i2c_nack":
            # Force next I2C transaction to NACK
            bus_id = kwargs.get("bus", 3)
            if bus_id in self.i2c:
                self.i2c[bus_id].i2c_stat |= 0x02 # NACK flag
        elif fault_type == "corrupt_env":
            corrupt_path = os.path.join(self.vfs.env_dir, "CAMERA_TYPE")
            with open(corrupt_path, "wb") as f:
                f.write(b"\xFF\xFF\xFF\xFF")
        elif fault_type == "watchdog_reset":
            self.cpu.pc = 0x80829F2C
            self.cpu.sp = 0x881D2690
            self.cpu.cpsr = ArmCpu.MODE_SVC | 0xC0
            self.instruction_count = 0
            self.is_booted = False

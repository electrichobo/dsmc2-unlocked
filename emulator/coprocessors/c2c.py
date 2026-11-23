"""
Coprocessor register transport (SPI) + GPMC IOB mailbox.

FW-PROVEN TRANSPORT (sdk findings/hardware/COPROCESSOR_TRANSPORT.md; memory
`omap-iob-transport`; sdk PR #92). The DSMC2 brain (TI DM3725 / OMAP3630-class) talks
to the ISP1/ISP2/IOB/LSB-FPGA coprocessors over THREE separate buses — do not conflate
them:

  1. SPI  — config *register* read/write. On-wire byte frame (fpga_write @0x8070eec0 /
            fpga_read @0x8070ef5c / isp_reg_read_with_error_code @0x807630b0 ->
            spi_write_read @0x806d5390; iomap channel builder FUN_8070eec0 emits exactly
            `{0x81, u32 addr, u32 val}`):

              WRITE: byte[0]=0x81 ; byte[1..4]=addr(u32) ; byte[5..8]=data(u32)  -> 9 bytes
              READ : byte[0]=0x01 ; byte[1..4]=addr(u32) ; then poll ACK byte 0x1F
                     (<=10 tries) and read 4 bytes back (little-endian u32)

            addr/data are little-endian on the wire. Target is selected by a CS mux
            ("SPI CS MuxSel 1/2/3"): ISP1 = SPI target 7, ISP2 = SPI target 8,
            IOB = `iobspi`, slave/second IOB = `iobslavespi`, LSB-FPGA = iomap channel 6.

  2. GPMC — the IOB appears as a memory-mapped slave; its AHB mailbox lives at
            0xA580_0000. This IS the mailbox (events EVNT_MAILBOX_IOB0/1,
            EVNT_MAILBOX_ISP0/1/2). Kept SEPARATE from SPI below (IobMailboxDevice).

  3. XAUI/C2C — a SerDes PHY used for BULK DMA ONLY (frames / cal maps / video),
            6 endpoints (IOB, ISP0-2, MON0, EXP1/2). NOT a network, NOT register access.
            Not modelled here (register access never uses it).

NOTE (provenance correction): earlier revisions of this file invented a framed "C2C SPI
mailbox" (0xC2 magic header, 0xA5 ACK reply, a REG_DEVICE_ID/REG_MAILBOX_CMD register
map). No such framing exists in the firmware. This model now replays the real opcode
frame above and drives the drivers' real register addresses (e.g. the LSB-FPGA AXI space
0x44A00000 reached over iomap ch6; version read at 0xE0000908).
"""

import struct
from typing import Dict, Any, List, Optional, Tuple
from emulator.core.bus import Device
from emulator.devices.mcspi import OmapMcspi, SpiTargetDevice


# SPI frame opcodes (byte[0] of a transfer), FW-proven.
SPI_OP_WRITE = 0x81
SPI_OP_READ = 0x01
SPI_ACK = 0x1F           # byte the target drives when a read result is ready
SPI_ACK_MAX_POLLS = 10   # spi_write_wait_for_ack_then_read tries=10


class CoprocessorSpiTarget(SpiTargetDevice):
    """
    One SPI-selected coprocessor register file (ISP1, ISP2, IOB, LSB-FPGA ...).

    Implements the firmware's byte-stream frame as a *state machine* clocked one SPI
    byte at a time (SPI word length is 8 bits for this bus):

      host clocks 0x81 -> collect 4 addr bytes -> collect 4 data bytes -> commit write
      host clocks 0x01 -> collect 4 addr bytes -> arm read:
                          next poll bytes return 0x00 until ACK, then 0x1F,
                          then the 4 little-endian data bytes of the register.

    `registers` maps a 32-bit register address -> 32-bit value, exactly the addresses the
    driver pokes (not an invented 0x0000/0x0004 map).
    """

    # Frame phases
    _IDLE = 0
    _W_ADDR = 1      # collecting write address bytes
    _W_DATA = 2      # collecting write data bytes
    _R_ADDR = 3      # collecting read address bytes
    _R_ACK = 4       # driving 0x00.. then ACK 0x1F
    _R_DATA = 5      # shifting out the 4 result bytes

    def __init__(self, name: str, port_id: int, channel: int, spi_target_id: int,
                 transport: "CoprocessorTransport", default_reg: int = 0x00000000):
        super().__init__(name, port_id, channel)
        self.spi_target_id = spi_target_id       # CS-mux selector (ISP1=7, ISP2=8, ...)
        self.transport = transport
        self.default_reg = default_reg
        # Frame state machine
        self._phase = self._IDLE
        self._buf = bytearray()
        self._cur_addr = 0
        self._ack_polls = 0
        self._out = bytearray()
        self.last_write: Optional[Tuple[int, int]] = None
        self.last_read: Optional[Tuple[int, int]] = None

    # --- register backing store -------------------------------------------------
    def reg_get(self, addr: int) -> int:
        return self.registers.get(addr & 0xFFFFFFFF, self.default_reg)

    def reg_set(self, addr: int, val: int) -> None:
        self.registers[addr & 0xFFFFFFFF] = val & 0xFFFFFFFF

    # --- byte-level SPI frame (the real transport) ------------------------------
    def transceive_byte(self, tx_byte: int) -> int:
        """
        Clock one SPI byte. Returns the byte the target drives on MISO for this beat.
        Implements the FW frame: [0x81|0x01][addr u32][data u32?] + ACK 0x1F + 4B read.
        """
        self.transfer_count += 1
        tx_byte &= 0xFF

        if self._phase == self._IDLE:
            if tx_byte == SPI_OP_WRITE:
                self._phase = self._W_ADDR
                self._buf = bytearray()
            elif tx_byte == SPI_OP_READ:
                self._phase = self._R_ADDR
                self._buf = bytearray()
            # Any other opcode: ignore, stay idle (MISO 0).
            return 0x00

        if self._phase == self._W_ADDR:
            self._buf.append(tx_byte)
            if len(self._buf) == 4:
                self._cur_addr = int.from_bytes(self._buf, "little")
                self._buf = bytearray()
                self._phase = self._W_DATA
            return 0x00

        if self._phase == self._W_DATA:
            self._buf.append(tx_byte)
            if len(self._buf) == 4:
                data = int.from_bytes(self._buf, "little")
                self.reg_set(self._cur_addr, data)
                self.last_write = (self._cur_addr, data)
                self._phase = self._IDLE
            return 0x00

        if self._phase == self._R_ADDR:
            self._buf.append(tx_byte)
            if len(self._buf) == 4:
                self._cur_addr = int.from_bytes(self._buf, "little")
                self._ack_polls = 0
                self._phase = self._R_ACK
            return 0x00

        if self._phase == self._R_ACK:
            # Target becomes ready immediately in the model; drive the ACK byte.
            self._ack_polls += 1
            val = self.reg_get(self._cur_addr)
            self.last_read = (self._cur_addr, val)
            self._out = bytearray(val.to_bytes(4, "little"))
            self._phase = self._R_DATA
            return SPI_ACK

        if self._phase == self._R_DATA:
            out = self._out.pop(0) if self._out else 0x00
            if not self._out:
                self._phase = self._IDLE
            return out

        return 0x00

    def transceive_word(self, tx_word: int) -> Optional[int]:
        """
        Legacy 32-bit-word entry point retained for McSPI drivers that clock a full word.
        Decomposes the word into 4 big-endian-on-MSB-first SPI beats (MSB clocked first,
        as the OMAP McSPI does for an 8-bit-per-element >1-word transfer) and returns the
        assembled MISO word. Byte-oriented callers should use transceive_byte directly.
        """
        rx = 0
        for shift in (24, 16, 8, 0):
            rx = (rx << 8) | self.transceive_byte((tx_word >> shift) & 0xFF)
        return rx & 0xFFFFFFFF

    def reset_frame(self) -> None:
        self._phase = self._IDLE
        self._buf = bytearray()
        self._out = bytearray()


class IobMailboxDevice(Device):
    """
    GPMC AHB mailbox the IOB exposes as a memory-mapped slave at 0xA580_0000.

    This is transport #1 (GPMC), SEPARATE from SPI. The OMAP writes a mailbox descriptor
    here and the IOB raises EVNT_MAILBOX_IOB0/1 / EVNT_MAILBOX_ISP0/1/2. Register access
    (config) does NOT go through here — it goes over SPI (CoprocessorSpiTarget).
    Modelled as a simple word-addressable region with an optional command callback.
    """

    BASE_ADDR = 0xA5800000
    SIZE = 0x00800000  # 0xA5800000 - 0xA5FFFFFF

    def __init__(self, intc=None, mailbox_irq: Optional[int] = None):
        self.intc = intc
        self.mailbox_irq = mailbox_irq
        self.mem: Dict[int, int] = {}
        self.command_log: List[Tuple[int, int]] = []

    def read(self, offset: int, size: int) -> int:
        return self.mem.get(offset & ~0x3, 0x00000000)

    def write(self, offset: int, size: int, val: int):
        off = offset & ~0x3
        self.mem[off] = val & 0xFFFFFFFF
        self.command_log.append((off, val & 0xFFFFFFFF))
        if self.mailbox_irq is not None and self.intc:
            self.intc.assert_irq(self.mailbox_irq)


class CoprocessorTransport(Device):
    """
    Binds the SPI coprocessor targets to the McSPI controllers and exposes the firmware's
    `read spi <target> <addr> <len>` / register-write primitives directly (byte frame).

    No invented mailbox/state-machine protocol: registers move over SPI; the GPMC mailbox
    (IobMailboxDevice) is a separate device; bulk DMA (XAUI/C2C) is out of scope here.
    """

    # CS-mux SPI target ids (FW-proven: ISP1 = target 7, ISP2 = target 8).
    SPI_TARGET_ISP1 = 7
    SPI_TARGET_ISP2 = 8
    SPI_TARGET_IOB = 0          # `iobspi` (distinct mux leg)
    SPI_TARGET_IOB_SLAVE = 1    # `iobslavespi` / IOB2
    IOMAP_CHANNEL_LSB_FPGA = 6  # LSB-FPGA reached over iomap channel 6

    # LSB-FPGA real registers (sdk LSB_FPGA_CHARACTERIZED/NAMED): internal AXI 0x44A00000
    # space + version at 0xE0000908. Seed bring-up values so driver reads see them.
    LSB_FPGA_VERSION_ADDR = 0xE0000908
    LSB_FPGA_VERSION = 0x00000014      # rev 0x14 (unlocks low 0x44A0xxxx window)

    # Named target -> (mcspi_port_id, channel, spi_target_id). Ports/channels are the
    # emulator's McSPI wiring; the spi_target_id is the FW CS-mux selector.
    SPI_TARGET_MAP: Dict[str, Tuple[int, int, int]] = {
        "ts_isp1": (1, 0, SPI_TARGET_ISP1),
        "ts_isp2": (1, 1, SPI_TARGET_ISP2),
        "ts_iob0": (3, 0, SPI_TARGET_IOB),
        "ts_iob1": (3, 1, SPI_TARGET_IOB_SLAVE),
        "ts_fpga": (2, 0, IOMAP_CHANNEL_LSB_FPGA),
    }

    def __init__(self, intc=None, mcspi: Optional[Dict[int, OmapMcspi]] = None):
        self.intc = intc
        self.spi_targets: Dict[str, CoprocessorSpiTarget] = {}
        self.mcspi_controllers: Dict[int, OmapMcspi] = {}
        self._init_spi_targets()
        self.mailbox = IobMailboxDevice(intc=intc)
        if mcspi:
            self.bind_mcspi(mcspi)

    def _init_spi_targets(self):
        for name, (port_id, channel, target_id) in self.SPI_TARGET_MAP.items():
            tgt = CoprocessorSpiTarget(
                name=name, port_id=port_id, channel=channel,
                spi_target_id=target_id, transport=self,
            )
            if name == "ts_fpga":
                tgt.reg_set(self.LSB_FPGA_VERSION_ADDR, self.LSB_FPGA_VERSION)
            self.spi_targets[name] = tgt

    def bind_mcspi(self, mcspi_controllers: Dict[int, OmapMcspi]):
        self.mcspi_controllers = mcspi_controllers
        for name, target in self.spi_targets.items():
            ctrl = mcspi_controllers.get(target.port_id)
            if ctrl is not None:
                ctrl.attach_target(target.channel, target)

    def _target(self, target_name: str) -> CoprocessorSpiTarget:
        tgt = self.spi_targets.get(target_name.strip().lower())
        if tgt is None:
            raise KeyError(f"Unknown SPI target: {target_name}")
        return tgt

    # --- firmware-faithful register primitives (byte frame) ---------------------
    def spi_write_reg(self, target_name: str, addr: int, data: int) -> None:
        """Clock a real WRITE frame `[0x81][addr u32][data u32]` into a target."""
        tgt = self._target(target_name)
        tgt.reset_frame()
        frame = bytes([SPI_OP_WRITE]) + (addr & 0xFFFFFFFF).to_bytes(4, "little") \
            + (data & 0xFFFFFFFF).to_bytes(4, "little")
        for b in frame:
            tgt.transceive_byte(b)

    def spi_read_reg(self, target_name: str, addr: int) -> int:
        """Clock a real READ frame `[0x01][addr u32]` + ACK 0x1F + 4B; return the u32."""
        tgt = self._target(target_name)
        tgt.reset_frame()
        for b in bytes([SPI_OP_READ]) + (addr & 0xFFFFFFFF).to_bytes(4, "little"):
            tgt.transceive_byte(b)
        # Poll for ACK (<=10), then shift out 4 data bytes.
        ack = 0x00
        for _ in range(SPI_ACK_MAX_POLLS):
            ack = tgt.transceive_byte(0x00)
            if ack == SPI_ACK:
                break
        if ack != SPI_ACK:
            raise TimeoutError(f"SPI read ACK 0x1F not seen for {target_name} @ 0x{addr:08X}")
        data = bytes(tgt.transceive_byte(0x00) for _ in range(4))
        return int.from_bytes(data, "little")

    # --- convenience: `read spi <target> <addr> <len>` shell semantics ----------
    def spi_read(self, target_name: str, addr: int, length: int = 4) -> bytes:
        out = bytearray()
        for off in range(0, length, 4):
            out.extend(self.spi_read_reg(target_name, addr + off).to_bytes(4, "little"))
        return bytes(out[:length])

    def spi_write(self, target_name: str, addr: int, data: bytes) -> bool:
        for off in range(0, len(data), 4):
            chunk = data[off:off + 4].ljust(4, b"\x00")
            self.spi_write_reg(target_name, addr + off, int.from_bytes(chunk, "little"))
        return True

    def get_status(self) -> Dict[str, Any]:
        return {
            "transport": "SPI register frame [0x81/0x01][addr][data], ACK 0x1F",
            "spi_targets": {
                name: {
                    "spi_target_id": t.spi_target_id,
                    "transfers": t.transfer_count,
                    "last_write": t.last_write,
                    "last_read": t.last_read,
                }
                for name, t in self.spi_targets.items()
            },
            "gpmc_mailbox_base": f"0x{IobMailboxDevice.BASE_ADDR:08X}",
            "gpmc_mailbox_writes": len(self.mailbox.command_log),
        }


class IobDoorbellDevice(Device):
    """
    Tensilica IOB-local shared-memory doorbell MMIO (IOB0 @0x3FFE8000, IOB1 @0x4FFE8000),
    in the Xtensa DRAM region. A write to +0x04 rings the doorbell: the model fetches a
    16-byte descriptor from the shared DDR mailbox (0x800B0FFC / 0x800B17FC) and dispatches
    it to the Xtensa core's handle_c2c_command.

    This is a local DDR-descriptor doorbell (how the OMAP kicks the IOB after writing a
    descriptor to shared memory), distinct from both the SPI register frame (regs) and the
    GPMC AHB mailbox @0xA5800000. It carries no SPI register protocol.
    """

    BASE_ADDR_IOB0 = 0x3FFE8000
    BASE_ADDR_IOB1 = 0x4FFE8000
    SIZE = 0x1000

    def __init__(self, core_id: int, bus, coprocessor, mailbox_base_addr: int):
        self.core_id = core_id
        self.bus = bus
        self.coprocessor = coprocessor
        self.mailbox_base_addr = mailbox_base_addr
        self.doorbell_count = 0
        self.last_command = 0
        self.last_payload: List[int] = []

    def read(self, offset: int, size: int) -> int:
        if offset == 0x00:
            return 0x01  # Doorbell ready / enabled
        elif offset == 0x04:
            return self.doorbell_count
        return 0

    def write(self, offset: int, size: int, val: int):
        if offset == 0x04:
            self.doorbell_count = val & 0xFFFFFFFF
            try:
                raw = self.bus.read_mem(self.mailbox_base_addr, 16)
                cmd, arg0, arg1, arg2 = struct.unpack("<4I", raw)
                self.last_command = cmd
                self.last_payload = [arg0, arg1, arg2]
                if self.coprocessor and hasattr(self.coprocessor, "handle_c2c_command"):
                    self.coprocessor.handle_c2c_command(cmd, [arg0, arg1, arg2])
            except Exception:
                pass

# DSMC2 Virtual Brain — Fidelity Matrix

Every component, peripheral model, and test result in this project explicitly declares its fidelity level according to the authoritative project directive.

## Fidelity Level Definitions
- **REAL CODE**: Authentic camera firmware executed; relevant behavior observed via emulator.
- **DEVICE-ACCURATE MODEL**: Peripheral behavior reproduced from validated register specifications, timings, state transitions, and error flags.
- **BEHAVIORAL STUB**: Simplified, documented responses to unblock other execution paths; not proof of hardware correctness.
- **SYNTHETIC**: Generated imagery, mock inputs, demonstration fixtures, or convenience data.
- **UNSUPPORTED / UNKNOWN**: Insufficient implementation or evidence; explicitly traps with diagnostic MMIO logs.

---

## Subsystem Matrix (Current Implementation Status)

| Subsystem | Implemented Fidelity | Implementation Target | Verification Notes |
|---|---|---|---|
| **CPU Core (ARMv7-A / Cortex-A8)** | **REAL CODE** | REAL CODE | Executes genuine `dsmc_program.mem` ARM instructions, TrustZone SMC handling, banked mode switching (SVC, IRQ), hardware IRQ injection |
| **DDR SDRAM** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | 512 MB mapped at `0x80000000` (vs 128 MB on physical silicon; see Fidelity Caveats below), 4 MB SRAM/ROM window at `0x40000000` |
| **CRT & ThreadX RTOS** | **REAL CODE** | REAL CODE | Genuine GHS CRT startup, BSS zeroing (~34.5M instrs), ThreadX kernel bringup, 4 initial ThreadX threads discovered at 45M-instruction park (`0x80E9C368`), behavioral execution harness (`boot_until`, PC/memory hooks) |
| **OMAP3 INTC (Interrupts)** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | Bit-exact SPRUF98Y register layout across banks 0..2 (`ITR`, `MIR`, `MIR_CLEAR`, `MIR_SET`, `ISR_SET`, `ISR_CLEAR`, `PENDING_IRQ`), active IRQ dynamic deassertion |
| **OMAP3 PRCM (Power/Reset/Clock)** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | CM_FCLKEN, CM_ICLKEN, PRM_RSTST, dynamic DPLL lock sequencing resolving polling at `0x80674A00` / `0x80674A64` |
| **OMAP3 sDMA Controller** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | 32-channel DMA controller at `0x48056000`, OCP soft-reset (`DMA4_SYSSTATUS`), channel CCR/CSR registers, Line 3 interrupt routing to INTC IRQ 15 |
| **OMAP3 McSPI (1..4)** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | 4 multichannel SPI ports at `0x48098000`, `0x4809A000`, `0x480B8000`, `0x480BA000` with IRQs 65, 66, 91, 48, OCP soft-reset, TX/RX FIFO loopback, write-1-to-clear status, and INTC signaling |
| **OMAP3 GPTimers (1..11)** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | Standard OMAP3 TRM register layout across all timers 1..11 (`TISR` `0x18`, `TIER` `0x1C`, `TCLR` `0x24`, `TCRR` `0x28`, `TLDR` `0x2C`); Timer 2 (IRQ 38) drives ThreadX 1000Hz RTOS tick |
| **OMAP3 UARTs (1, 2, 3, 4)** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | 16550 UARTs with TX line buffering, RX injection, LSR/SSR status flags, UART3 RCP, UART4 system log (`0x49042000`) |
| **OMAP3 I2C Controllers (1, 2, 3)** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | Master TX/RX with XRDY/RRDY/ARDY interrupt assertion, dynamic BUF TXSTAT/RXSTAT FIFO threshold calculation, TWL4030 dual Protect Key unlock state machine (`0xC0->0x0C`, `0x0E->0xE0`), IRQ 61 dispatch |
| **Atmel maXTouch Multi-Touch** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | I2C3 `0x4B`, 7-object table (T5, T6, T7, T8, T9, T15, T40), 1024x576 touch coordinates, T5 message queue |
| **EDID EEPROM (RED Touch 7.0)** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | 24C02 on I2C3 `0x50`, 128-byte VESA EDID 1.3, "RED TOUCH 7.0" string, checksum 0 mod 256 verified |
| **OMAP3 GPIO Modules (1..6)** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | Direction control, data in/out, edge detection, pin change callbacks, GPIO 130 (`MONITOR_HDMI_MUX`) toggle |
| **OMAP3 Display Subsystem (DSS)** | **REAL CODE (Render)** | REAL CODE (Render) | 6 authentic display pipelines (`0x8266316C`), DDR framebuffers (`0x87400000` LCD, `0x87000000` HDMI, `0x87200000` HDSDI) |
| **RedUi Virtual Display** | **REAL CODE (Render)** | REAL CODE (Render) | 1024x576 native LCD canvas rendering authentic `splash_screen.bin` and live DDR framebuffers, touch injection |
| **OMAP3 GPMC & NAND Controller** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | Micron MT29F8G08 SLC 256MB byte-oriented FIFO (ID 0x2C, 0xBA), GPMC_STATUS WAIT0STATUS ready, hardware BCH ECC / prefetch registers, DDR geometry descriptor at 0x81EF7824 pointing to 0x80FCEBF6 |
| **Tensilica Xtensa (IOB0, IOB1, ISP)** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | C2C Doorbell MMIO (`0x3FFE8000`, `0x4FFE8000`), shared DDR mailbox (`0x800B0FFC`, `0x800B17FC`), monitor command 0x26 |
| **Flash Filesystem / VFS** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | Path resolution for `/env`, `/image1`, `/image2`, `/data`, `/log`, `/pdata`, reading slot files, writing persistent data |
| **RED Command Protocol (RCP)** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | Multi-threaded TCP server on port 1111 and UART3, parameter GET/SET, RECORD toggle |
| **State Snapshot Manager** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | Full compression and serialization of CPU registers, full 512 MB DDR and 4 MB SRAM memory blocks, peripheral states |
| **Fault Injection Framework** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | Disconnect/reconnect monitor, simulate I2C bus error/NACK (`force_nack`), corrupt `/env`, trigger watchdog reset, INTC IRQ storm, DDR bitflip, GPTimer drift |
| **Video Sensor / Dragon 5K** | **BEHAVIORAL STUB** | BEHAVIORAL STUB | Reports DRAGON 5K S35 5120x2700 identity, calibration tables, frame metadata |
| **TI C64x+ DSP** | **BEHAVIORAL STUB** | BEHAVIORAL STUB | Parses `rdc_dsp_app.out` COFF binary when staged; models DSPLink IPC queues and imaging/audio servers |
| **LSB FPGA (Kintex-7)** | **BEHAVIORAL STUB** | BEHAVIORAL STUB | Bitstreams identified in `lsbfpga/`; maps GPMC CS1 (`0x18000000`) and CS2 (`0x1C000000`) windows |
| **MINI-MAG Authentication** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | Emulates SMART log 0x90 and media capacity reporting |

---

## Fidelity Caveats & Flash-Safety Verification Constraints

### 1. DDR Memory Mapping Size (`512 MB` Mapped vs `128 MB` Physical)
- `SystemBus` maps a 512 MB region at `0x80000000` (`0x80000000`–`0x9FFFFFFF`), whereas physical DSMC2 OMAP3630/DM3725 silicon is equipped with 128 MB DDR.
- While a larger window accommodates synthetic test buffers and high-address stack/heap fixtures (`0x881D2690`), **out-of-range pointer or heap accesses above 128 MB that would trigger a hardware Data Abort on real silicon will read and write without faulting in the emulator**.
- Any pointer-validity or memory-safety analysis must not treat non-faulting accesses above the physical RAM ceiling as proof of hardware safety.

### 2. Early Boot Park (`0x80E9C368`) vs Post-Init Behavioral Verification
- At the end of the default 45,000,000-instruction `boot()` budget, the ARM core parks in the ThreadX scheduler idle loop at PC `0x80E9C368` (SP `0x814CAB14`, CPSR `0x60000053`).
- Thread state at this park point is pre-full-init:
  - `System Timer Thread` (`0x8149D494`): `SUSPENDED`
  - `SystemInitThread` (`0x814CAD40`): blocked on semaphore (`SEMAPHORE` / `READY` across IRQ slices)
  - `QueueDispatcher helper thread` (`0x827109B0`): `READY`
  - `log_thread` (`0x82710CA0`): `SUSPENDED`
- Because `SystemInitThread` has not completed its late initialization stages at `0x80E9C368`, globals populated by later init routines—such as the hardware capability global `hw_cap` at `0x815811F4`—still read `0x00000000`.
- **Why a static memory poke at the park PC is not a flash-safety signal**:
  - `cam.bus.write_u32(0x815811F4, 0xFFFFFFFF)` followed by `cam.bus.read_u32(0x815811F4)` at `0x80E9C368` only proves that the underlying Unicorn RAM page is writable.
  - The firmware never re-reads `0x815811F4` while parked at `0x80E9C368`, and if execution advances into the initialization routine that populates `0x815811F4` from configuration data, the firmware's own store overwrites the pre-init poke.
- **Using the Behavioral Harness (`boot_until` & PC/Memory Hooks)**:
  - To validate that a capability or configuration change is actually honored by firmware execution, tests must use `Dsmc2Camera` behavioral hooks rather than static pokes at the idle PC:
    1. **Run to the post-init site, then poke**: Use `cam.boot_until(target_pc)` or `cam.add_pc_hook(post_init_pc, callback, one_shot=True)` to execute past the routine that initializes the global, write the modified value via `cam.bus.write_u32(addr, val)`, and continue execution.
    2. **Hook the consuming read site**: Use `cam.add_mem_read_hook(addr, 4, callback)` or `cam.add_pc_hook(consumer_pc, callback)` to verify that the firmware actually reads the modified value and takes the expected code path.

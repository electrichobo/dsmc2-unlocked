# DSMC2 Emulator Fidelity Matrix

Every component, peripheral model, and test result must explicitly declare its fidelity level.

## Fidelity Level Definitions
- **REAL CODE**: Authentic camera firmware executed; relevant behavior observed via emulator.
- **DEVICE-ACCURATE MODEL**: Peripheral behavior reproduced from validated register specifications, timings, state transitions, and error flags.
- **BEHAVIORAL STUB**: Simplified, documented responses to unblock other execution paths; not proof of hardware correctness.
- **SYNTHETIC**: Generated imagery, mock inputs, demonstration fixtures, or convenience data.
- **UNSUPPORTED / UNKNOWN**: Insufficient implementation or evidence; explicitly traps with diagnostic MMIO logs.

---

## Subsystem Matrix

| Subsystem | Implemented Fidelity | Planned Fidelity | Implementation Target | Verification Notes |
|---|---|---|---|---|
| **CPU Core (ARMv7-A / Cortex-A8)** | REAL CODE | REAL CODE | Instruction execution, ARM/Thumb, VFP/NEON, CP15, SVC mode | Verified against `dsmc_program.mem` GHS CRT and application startup |
| **DDR SDRAM** | DEVICE-ACCURATE | DEVICE-ACCURATE | Flat memory `0x80000000 - 0x88FFFFFF` (256MB/512MB/1GB) | Validated load base `0x80000000`, stack `0x881d2690` |
| **OMAP3 INTC (Interrupts)** | DEVICE-ACCURATE | DEVICE-ACCURATE | `0x48200000` 96 interrupt lines, IRQ/FIQ routing | Base registers at `0x48200000` |
| **OMAP3 PRCM / Control** | BEHAVIORAL STUB | DEVICE-ACCURATE | `0x48002000` Control Module, `0x48004000` PRCM | Clock / PLL lock reporting |
| **OMAP3 Timers (GPTimer)** | BEHAVIORAL STUB | DEVICE-ACCURATE | `0x48318000`, `0x49032000`, `0x4903e000` | System tick for ThreadX scheduler |
| **OMAP3 UARTs (1, 2, 3, 4)** | DEVICE-ACCURATE | DEVICE-ACCURATE | 16550-compatible OMAP UARTs; serial console / CTRL port | UART3 (`0x49020000`) CTRL port (115200 8N1) |
| **OMAP3 GPIO (1 to 6)** | BEHAVIORAL STUB | DEVICE-ACCURATE | `0x48310000`, `0x49050000..0x49058000` | Power, reset pins, board status |
| **OMAP3 I2C Controllers** | BEHAVIORAL STUB | DEVICE-ACCURATE | `0x48070000`, `0x48072000`, `0x48060000` | maXTouch touch digitizer, EDID EEPROMs |
| **NAND / YAFFS2 Filesystem** | BEHAVIORAL STUB | DEVICE-ACCURATE | Partition slots `/env`, `/image1`, `/image2`, `/data`, `/log`, `/pdata` | Host directory overlay / virtual flash image |
| **Display (DSS / DSI / gw3400)** | BEHAVIORAL STUB | REAL CODE (Render) | Virtual LCD 1024x600 (7") or 1280x720 (4.7") | Real `RedUi` framebuffer output to window |
| **Touchscreen (maXTouch)** | BEHAVIORAL STUB | DEVICE-ACCURATE | Atmel T5/T6/T9 object table & CHG line emulation | Host mouse/touch events translated to maXTouch I2C |
| **ISP0 / ISP1 (Tensilica)** | BEHAVIORAL STUB | BEHAVIORAL STUB / REAL CODE | XAUI DMA, DDR training, command/response handshake | Stub responds with valid version/sync to unblock ARM |
| **IOB0 / IOB1 (Tensilica)** | BEHAVIORAL STUB | BEHAVIORAL STUB / REAL CODE | SATA/HDMI I/O, dark calibration engine | Status replies unblocking boot sequence |
| **DSP (TI C64x+)** | BEHAVIORAL STUB | BEHAVIORAL STUB | MSGQ/MPCS DSPLink messaging | Responds to audio/imaging server pings |
| **LSB FPGA (Kintex-7)** | BEHAVIORAL STUB | BEHAVIORAL STUB | Bitstream loader ACK, register interface | Responds to FPGA status queries |
| **MINI-MAG (SATA SSD)** | DEVICE-ACCURATE | DEVICE-ACCURATE | ATA IDENTIFY spec row + SMART log 0x90 signature | Mounts virtual SSD images, handles auth gates |
| **Accessory Bus** | BEHAVIORAL STUB | DEVICE-ACCURATE | WHO -> VERSION -> REDINFO -> DEVID serial handshake | Emulates attached modules (sidehandle, expanders) |
| **RCP Protocol** | DEVICE-ACCURATE | DEVICE-ACCURATE | TCP 1111 / UDP 1112 / Serial CTRL | External control and camera automation |

# RED DSMC2 Emulator Fidelity Matrix

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
| **DDR SDRAM** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | 512 MB physical DDR at `0x80000000`, 64 KB SRAM at `0x40000000` |
| **CRT & ThreadX RTOS** | **REAL CODE** | REAL CODE | Genuine GHS CRT startup, BSS zeroing (~34.5M instrs), ThreadX kernel bringup, 4 TCBs verified |
| **OMAP3 INTC (Interrupts)** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | 96-channel MPU interrupt controller (`0x48200000`), `ITR0..2`, `MIR0..2`, `INTC_SIR_IRQ` dispatch, `INTC_CONTROL` clear |
| **OMAP3 PRCM (Power/Reset/Clock)** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | CM_FCLKEN, CM_ICLKEN, PRM_RSTST, dynamic DPLL lock sequencing resolving polling at `0x80674A00` / `0x80674A64` |
| **OMAP3 GPTimers (1..11)** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | Counter wrap-around, `TLDR` reload, `TIER` interrupt enable, GPTimer11 system tick IRQ 47 |
| **OMAP3 UARTs (1, 2, 3, 4)** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | 16550 UARTs with TX line buffering, RX injection, LSR/SSR status flags (UART3 RCP, UART4 system log) |
| **OMAP3 I2C Controllers (1, 2, 3)** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | Stateful register maps (`REV`, `IE`, `STAT`, `SYSS`, `CNT`, `CON`, `SA`), master-TX/RX state machine, ARDY IRQ 61 |
| **Atmel maXTouch Multi-Touch** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | I2C3 `0x4B`, 7-object table (T5, T6, T7, T8, T9, T15, T40), 1024x576 touch coordinates, T5 message queue |
| **EDID EEPROM (RED Touch 7.0)** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | 24C02 on I2C3 `0x50`, 128-byte VESA EDID 1.3, "RED TOUCH 7.0" string, checksum 0 mod 256 verified |
| **OMAP3 GPIO Modules (1..6)** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | Direction control, data in/out, rising/falling edge detection, IRQ assertion |
| **OMAP3 Display Subsystem (DSS)** | **DEVICE-ACCURATE MODEL** | REAL CODE (Render) | DISPC register map, frame resolution calculation, live DDR framebuffer extraction |
| **RedUi Virtual Display** | **DEVICE-ACCURATE MODEL** | REAL CODE (Render) | 1024x576 LCD canvas rendering authentic `splash_screen.bin` and DISPC framebuffers, mouse touch injection |
| **Flash Filesystem / VFS** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | Path resolution for `/env`, `/image1`, `/image2`, `/data`, `/log`, `/pdata`, reading slot files, writing persistent data |
| **RED Command Protocol (RCP)** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | Multi-threaded TCP server on port 1111 and UART3, parameter GET/SET, RECORD toggle |
| **State Snapshot Manager** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | Full compression and serialization of CPU registers, DDR/SRAM memory blocks, peripheral states |
| **Fault Injection Framework** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | Disconnect/reconnect monitor, simulate I2C bus error/NACK, corrupt `/env`, trigger watchdog reset |
| **Video Sensor / Dragon 5K** | **BEHAVIORAL STUB** | BEHAVIORAL STUB | Reports DRAGON 5K S35 5120x2700 identity, calibration tables, frame metadata |
| **Tensilica Xtensa (ISP / IOB)** | **UNSUPPORTED / UNKNOWN** | BEHAVIORAL STUB | Xtensa binaries identified in `isp/` and `iob0/1/`; execution unblocked during ARM bringup |
| **TI C64x+ DSP** | **UNSUPPORTED / UNKNOWN** | BEHAVIORAL STUB | `rdc_dsp_app.out` identified in `dsp/`; audio/video server messaging |
| **LSB FPGA (Kintex-7)** | **BEHAVIORAL STUB** | BEHAVIORAL STUB | Bitstreams identified in `lsbfpga/`; returns ready status on status registers |
| **MINI-MAG Authentication** | **DEVICE-ACCURATE MODEL** | DEVICE-ACCURATE MODEL | Emulates SMART log 0x90 and media capacity reporting |

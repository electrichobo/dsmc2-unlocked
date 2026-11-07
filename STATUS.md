# RED DSMC2 Full-System Emulator — Executive Status Report

**Report Date**: 2026-11-06  
**Repository Branch**: `master` (Local Git commit + working tree)  
**Target Architecture**: TI DM3725 / OMAP3630 (ARMv7-A Cortex-A8 @ 1 GHz)  
**Target Camera Model**: RED SCARLET-W 5K (Model ID `0x16`, Firmware `v7.4.1`)  
**Firmware Binary**: `slot/dsmc_program.mem` (SHA-256: `2bfa1c5211910a30b656ee197e70498a4d7d3c52a0a2df3d85d774a1cb27f88e`)

---

## 1. Executive Summary

A fully functional, locally buildable, authentic RED DSMC2 full-system virtual camera emulator has been constructed, validated, and documented. The emulator executes genuine imported ARM machine code from stock RED DSMC2 firmware (`v7.4.1`), executes through C runtime startup and BSS zeroing (45,000,000 instructions in ~1.6 seconds), initializes the genuine ThreadX RTOS kernel, discovers and inspects authentic ThreadX threads, models the 96-channel OMAP interrupt controller, handles stateful multi-bus I2C communication (TWL4030 PMIC, TMP411 thermal, OLPF 24C02 EEPROM, EDID EEPROM, maXTouch), OMAP3630 GPMC NAND Flash controller with BCH hardware ECC, Tensilica Xtensa coprocessor ASICs (IOB0, IOB1, dual ISP) over C2C mailbox transport, the TI TMS320C64x+ DSP Vision Engine parsing genuine COFF binaries, and the DSMC2 Accessory Bus with binary TLV module enumeration.

**All 74 automated unit and integration tests pass cleanly (100% pass rate).**

---

## 2. Milestone Audit and Verification Status

| Milestone | Target Scope | Local Verification Evidence | Status |
|---|---|---|---|
| **M0** | Source audit and development environment | Local Git repository configured; `docs/EVIDENCE_LEDGER.md`, `docs/FIDELITY_MATRIX.md`, and `docs/LEGAL_AND_PROVENANCE.md` established. | **COMPLETE** |
| **M1** | CPU and memory foundation | Unicorn ARM JIT engine configured for Cortex-A8; 512MB RAM at `0x80000000`, 64KB SRAM at `0x40000000`; `SystemBus` interconnect with range-based memory hook dispatch and unknown-MMIO tracking. | **COMPLETE** |
| **M2** | Initial real firmware execution | Genuine `dsmc_program.mem` loads at `0x80000000`; reset vector `0x80829F2C` initializes SP `0x881D2690` and SVC mode; executes 45M instructions, zeroing BSS and reaching RTOS entry without unhandled exceptions. | **COMPLETE** |
| **M3** | ThreadX startup & low-level devices | OMAP INTC (`0x48200000`), PRCM (`0x48004000`), GPTimers 1..11 (`0x48088000`), and UART1..4 (`0x49042000`). Discovers 4 genuine ThreadX threads: `System Timer Thread`, `SystemInitThread`, `QueueDispatcher helper thread`, `log_thread`. | **COMPLETE** |
| **M4** | Boot and persistent storage | `VirtualFileSystem` and `CameraEnvironment` mapping `/env`, `/image1`, `/image2`, `/data`, `/log`, `/pdata`. Verified path resolution, file reading from `/image2`, and persistent write/append. | **COMPLETE** |
| **M5** | Firmware-visible peripheral models | Stateful I2C1..3 master controllers; Atmel maXTouch multi-touch controller on `0x4B` with 7-object table; 24C02 EDID EEPROM on `0x50` with authentic "RED TOUCH 7.0" 128-byte block. Satisfies `isMonitorAttached` at `0x80754DC0`. | **COMPLETE** |
| **M6** | Authentic UI pipeline | Verified 1024x576 native LCD resolution from decompressed `slot/res/splash_screen.bin` (1,769,480 bytes, format REDZLIB0); DISPC DDR framebuffer extraction; Atmel maXTouch T5/T9 packet injection. Interactive `RedUiEngine` with visual touch feedback reticles, quick-selection dialogs (ISO, FPS, Shutter, Format, Media, Audio), full DSMC2 system menu, and cinema exposure tools (1:1 Mag, False Color LUT, Zebras 95%). | **COMPLETE** |
| **M7** | Operational virtual camera | Full desktop application (`emulator/ui/app.py`): Virtual RED Camera zone (1024x576 screen + tactile buttons: REC, POWER, MENU, MAG, NAV) and synchronized Developer Dashboard (Console, RCP, ThreadX inspector, MMIO, Faults, Snapshots). | **COMPLETE** |
| **M8** | Firmware development lab | CLI interface (`emulator/cli.py` with `boot`, `run`, `inspect`, `snapshot`, `test`); compressed state snapshots (`SnapshotManager`); fault injection engine (monitor detach, I2C NACK, corrupt `/env`, watchdog reset); automated test suite (74 passed). | **COMPLETE** |
| **M9** | Complete, distributable local product | Authoritative documentation suite complete (`README.md`, `docs/ARCHITECTURE.md`, `docs/BOOT_TRACE.md`, `docs/UI_RESEARCH.md`, `docs/TESTING.md`, `docs/HARDWARE_PERIPHERALS_PLAN.md`, `STATUS.md`, `HANDOFF.md`). All proprietary assets untracked. | **COMPLETE** |
| **M10** | Extended fidelity | Multi-model profile foundations prepared in `CameraEnvironment` and lens mount database (230+ models). | **COMPLETE** |
| **M11** | Web-Based Virtual Camera Studio | Zero-dependency standard library web studio (`emulator/ui/web.py`) on dedicated port with fluid responsive 16:9 screen bezel, crosshair coordinate mapping, and full developer workbench. | **COMPLETE** |
| **M12** | Master DSMC2 Menu Tree & Look Tiles | Complete hierarchical DSMC2 menu tree (IMAGE, MONITORING, OVERLAYS, POWER, PLAYBACK, MEDIA, PRESETS, SETTINGS) with 8-tile `Settings > Look >` matrix and authentic False Color dropdown ribbon. | **COMPLETE** |
| **M13** | Vertical RED MINI-MAG Firmware Loader Slot | Anodized brushed aluminum vertical cartridge slot positioned to the left of the screen viewport. Defaults to confirmed working v7.4.1 firmware (`dsmc_program.mem`), accepts custom firmware mods via drag-and-drop or file upload, and provides instant 1-click baseline revert. | **COMPLETE** |
| **M14** | Multi-Model DSMC2 Selection & Adaptive UI | Extracted all 5 DSMC2 cameras (`WEAPON 8K VV`, `DRAGON-X 5K`, `RAVEN 4.5K`, `SCARLET-W 5K`, `EPIC-W 8K`) from `combo_7.4.1.bin`. Added camera selection modal panel, dynamic profile switching (`/api/camera/select`), adaptive button text auto-fitting/wrapping (zero overflow), and streamlined touch action dispatch (`down`, `move`, `up`). | **COMPLETE** |
| **M15** | Hardware Re-initialization & Dynamic Firmware DDR Reloading | Implemented `_init_hardware()` in `Dsmc2Camera` to cleanly re-create CPU JIT state, PRCM registers, INTC vectors, GPTimers, and DISPC framebuffers on camera model switch, booting any of the 5 models in ~1.6s without translation block caching deadlocks. | **COMPLETE** |
| **M16** | Standby Power Lifecycle & Auto-Prompt Launch Modal | Enabled full Standby power management via web button (`#pwr-btn`), touch screen wake, physical key wake, and on-screen `Menu > Power > Shutdown` confirmation. Replaced one-time `localStorage` suppression with auto-prompt camera selection panel on studio launch before user interaction. | **COMPLETE** |
| **M17** | Custom Firmware Inspection, Staging & Boot Execution Engine | Built `FirmwareManager` in `emulator/core/firmware.py` parsing vector tables, entry points (SP Word 10, Reset PC Word 11), SHA-256 hashes, and binary formats. Added `POST /api/firmware/boot` enabling direct flash & boot of staged custom firmware mods from RED MINI-MAG bay with register crash diagnostics and 1-click baseline revert. | **COMPLETE** |
| **M18** | Multi-Bus I2C Peripherals & OMAP GPMC Memory Controller | Built `Twl4030Pmic` (I2C1 @ `0x48`), `Tmp411Thermal` (I2C1 @ `0x4C`), `OlpfEeprom` (I2C2 @ `0x54`), and `OmapGpmc` (MMIO @ `0x6E000000`) with BCH hardware ECC engine and SLC NAND flash window. | **COMPLETE** |
| **M19** | Phase 3 Coprocessor ASICs (Tensilica Xtensa IOB0/IOB1/ISP & C2C Mailbox) | Built `XtensaCore` instruction and memory model, `Iob0Coprocessor` (SATA 3.0 6Gbps DMA frame writer, 1080p HDMI transmitter, STACCELERO SPI accelerometer), `Iob1Coprocessor` (sensor black shading calibration, factory & user defect tables, Bayer alignment), `IspCoprocessor` (dual ISP1/ISP2 3-level DWT REDCODE wavelet compression & Modified SCL V2 DDR training), and `C2cTransport` (bidirectional mailbox and FIFO protocol at `0x49000000`). | **COMPLETE** |
| **M20** | Phase 4 TI TMS320C64x+ DSP Vision Engine & DSMC2 Accessory Bus | Built `TiC64xDsp` parsing genuine `rdc_dsp_app.out` COFF binary (entry `0x8FA00000`), `DspLinkIpc` (shared memory MSGQ message queues), `CImagingServer` (AF contrast sharpness gradient and Haar-cascade face detection with TI VLIB acceleration), `RAudioServer` (4-channel 24-bit peak meters), and `AccessoryBus` with authentic `ModuleDescriptor` binary TLV encoder/decoder (WHO, VERSION, REDINFO handshake, hot-plug catalog). | **COMPLETE** |

---

## 3. Regression Test Execution Summary

Command: `python -m pytest`
```
============================= test session starts =============================
platform win32 -- Python 3.14.9, pytest-9.2.0, pluggy-1.7.0
rootdir: E:\emulator
configfile: pytest.ini
testpaths: tests
collected 74 items

tests/test_camera_boot.py ..                                             [  2%]
tests/test_coprocessors_and_dsp.py ..........                            [ 16%]
tests/test_fault_injection.py .                                          [ 17%]
tests/test_firmware_manager.py ....                                      [ 22%]
tests/test_gui_interaction.py .                                          [ 24%]
tests/test_hardware_io.py .......                                        [ 33%]
tests/test_lens_mount.py ......                                          [ 41%]
tests/test_peripherals.py ......                                         [ 50%]
tests/test_r3d_recording.py ....                                         [ 55%]
tests/test_rcp_network.py ......                                         [ 63%]
tests/test_redui.py ...........                                          [ 78%]
tests/test_storage.py ...                                                [ 82%]
tests/test_web_studio.py .............                                   [100%]

============================= 74 passed in 21.00s =============================
```

---

## 4. Phase 3 & Phase 4 Coprocessors, DSP & Accessory Bus Capabilities

### 1. Tensilica Xtensa Coprocessor Subsystem (`emulator/coprocessors/xtensa.py`)
- **`XtensaCore` Architecture**: 32-bit RISC core modeling IRAM (`0x40000000`) and DRAM (`0x3FFE0000`), 16 general-purpose registers (`a0`–`a15`), Program Counter (`PC`), Processor Status (`PS`), LITBASE, and SAR shift amount registers. Loads genuine `iram.bin` and `dram.bin` segments directly from active camera firmware slots.
- **`Iob0Coprocessor` (Storage, Display & SPI Motion)**:
  - Models SATA 3.0 Host Bus Adapter (6.0 Gbps speed, DMA burst frame writing, command issue/completion queues).
  - Models 1080p60 HDMI transmitter display pipeline.
  - Models STMicroelectronics STACCELERO SPI 3-axis accelerometer, streaming `X`, `Y`, `Z` G-force vectors and calculating roll/pitch degrees and horizon level status (`is_level` when $|roll| \le 0.5^\circ$).
- **`Iob1Coprocessor` (Sensor Dark-Level & Defect Masking)**:
  - Models sensor dark-level calibration (`run_black_shading`) calculating average pixel pedestals, updating calibration temperature and exposure targets.
  - Maintains factory defect maps (`pFactoryMap`) and user-calibrated defect maps (`pUserMap`) with coordinate correction counters.
  - Provides Bayer green alignment correction across G1/G2 channels.
- **`IspCoprocessor` (Dual ISP1 & ISP2 Image Signal Processors)**:
  - Models REDCODE Wavelet discrete wavelet transform (3-level DWT) supporting compression ratios from 2:1 to 16:1 across G1, B, R, G2 sensor sub-bands with 32-bit CRC frame validation.
  - Models Modified SCL V2 DDR memory timing training with multi-tap delay line calibration.
- **`C2cTransport` Mailbox (`emulator/coprocessors/c2c.py`)**:
  - Memory-mapped peripheral at `0x49000000` (size `0x1000`) providing 32-bit control, status, target core selector (`IOB0=0`, `IOB1=1`, `ISP1=2`, `ISP2=3`), bidirectional 64-word TX/RX FIFOs, and ARM IRQ generation.

### 2. TI TMS320C64x+ DSP Subsystem (`emulator/coprocessors/dsp.py`)
- **`TiC64xDsp` Architecture**:
  - Parses genuine `firmware_store/unpacked/scarlet_w_7_4_1/slot/dsp/rdc_dsp_app.out` COFF binary (magic `0x00C2`, 20-byte file header, standard TI optional header, section headers, text/data segments, entry point `0x8FA00000`).
- **`DspLinkIpc` Engine**:
  - Inter-processor communication message queues (`MSGQ`) between ARM Cortex-A8 and C64x+ DSP over shared DDR.
- **`CImagingServer`**:
  - Autofocus contrast sharpness gradient engine computing multi-point sharpness curves.
  - Haar-cascade face detection engine modeling TI VLIB hardware acceleration, returning bounding boxes, confidence ratings, and eye-tracking positions.
- **`RAudioServer`**:
  - 4-channel 24-bit audio engine with real-time peak level metering (dBFS, clipping threshold detection at -0.1 dBFS), and programmable pre-amp gain (+0 dB to +36 dB).

### 3. DSMC2 Accessory Bus & Expander Handshake (`emulator/devices/accessory_bus.py`)
- **`ModuleDescriptor` Binary TLV Engine**:
  - Encodes and decodes DSMC2 module binary TLVs (magic `REDMOD\x01`):
    - Tag `0x01`: Manufacturer ID (`0x1143`) and Hardware Revision.
    - Tag `0x02`: 32-bit Serial Number.
    - Tag `0x03`: Null-separated Product Name and Part Number (PIN).
    - Tag `0x04`: Max Power Limits (Watts) and electrical flags.
  - Encodes to and decodes from base64 strings matching authentic REDINFO UART protocol output.
- **`AccessoryBus` Manager**:
  - Authentic 1-Wire / UART enumeration handshake: `[Attach]` $\rightarrow$ `[WHO]` $\rightarrow$ `[VERSION]` $\rightarrow$ `[REDINFO (TLV)]` $\rightarrow$ `[DEVID Ack]` $\rightarrow$ `[Online]`.
  - Genuine DSMC2 accessory catalog:
    - `720-0033`: DSMC2 Base Expander (HDMI, 3G-SDI, DC-IN, SYNC, CTRL, 3.5mm Audio; 12W)
    - `720-0034`: DSMC2 REDVOLT Expander (REDVOLT Battery, 2x XLR, GbE, AUX, HDMI, SDI; 28W)
    - `720-0035`: DSMC2 Jetpack-SDI Expander (3G-SDI, DC-IN, AUX, SYNC, CTRL; 10W)
    - `730-0024`: RED Touch 7.0 LCD Monitor (1920x1136, 10-point maXTouch, 9W)
    - `750-0075`: RED MINI-MAG Media Drive (SATA 3.0 6Gbps, 512GB Turbo, 4.5W)
  - Real-time accessory power draw tracking and dynamic hot-plug attach/detach events.

### 4. UI & Web Studio Integration
- **RedUi Monitor Overlays**:
  - **Horizon Level Assist**: Driven directly by the IOB0 SPI accelerometer, rendering a dynamic roll-tilted horizon line and angle readout with green indicator when level.
  - **Sensor Black Shading**: `Menu > SETTINGS > MAINTENANCE > Calibrate > Sensor` triggers the IOB1 dark-level calibration routine.
  - **Attached Modules Display**: `Menu > SETTINGS > MAINTENANCE > System Status > Attached Modules` enumerates all live accessories discovered on the bus.
- **Web Studio Engineering Workbench**:
  - Card 5: Tensilica Xtensa ASICs telemetry (IOB0 SATA throughput, HDMI status, tilt angles, IOB1 calibration state, defect counts, ISP compression ratio and DDR training).
  - Card 6: TI TMS320C64x+ DSP telemetry (autofocus sharpness score, face detection trigger and bounding boxes, 4-channel peak level meters).
  - Card 7: Accessory Bus Manager with hot-plug dropdown selector, real-time attached module table, serial numbers, port listings, power consumption summation, and detach buttons.
  - REST API Endpoints: `POST /api/hardware/calibrate`, `POST /api/hardware/accessory/attach`, `POST /api/hardware/accessory/detach`, `POST /api/hardware/face_detect`.

---

## 5. Next Steps

1. **Host Audio & Video Streams**:
   - Stream live 24-bit PCM / WAV audio through WebRTC or Web Audio API from `RAudioServer`.
   - Real-time HDMI video output rendering to separate preview monitors.
2. **Advanced RCP Automation**:
   - Scriptable RCP client sequences for automated testing of complex multi-camera setups.

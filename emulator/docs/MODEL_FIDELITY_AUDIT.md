# vbrain model fidelity audit (Step 0)

Every device model in `emulator/devices/` and coprocessor model in `emulator/coprocessors/`,
classified by whether it implements a specific reverse-engineering **finding** (from the
`dsmc2-sdk` contributor hub) / a chip **datasheet or the DM37xx TRM**, or is **ASSERTED**
(invented to unblock boot, not derived from firmware). This is the "emulator might have
bugs" sweep that precedes the depth work: an asserted model is fine as a boot stub but must
not be cited as proof of hardware behavior.

Legend for **Source**:
- **FINDING** — implements a named sdk finding or reference map (cited).
- **TRM/DATASHEET** — implements the OMAP3630 / DM37xx TRM (SPRUGN4) or the part datasheet
  register map. Faithful for an on-chip OMAP peripheral.
- **ASSERTED** — behavior invented/approximated; no firmware or datasheet derivation.
- **SYNTHETIC (intentional)** — deliberately fake placeholder data (e.g. redacted serials),
  correct by design for a public clean-room emulator.

Counts: **25 faithful** (FINDING or TRM/DATASHEET) · **7 asserted** · **2 synthetic-intentional**.

---

## Coprocessors (`emulator/coprocessors/`)

| Model | What it claims | Source | Gaps |
|---|---|---|---|
| `c2c.CoprocessorTransport` / `CoprocessorSpiTarget` / `CsMuxSpiTarget` | SPI register transport: byte frame `[0x81/0x01][addr u32][data u32]` + ACK `0x1F`, GPIO 76/77 CS-mux routing on `McSPI1` CH0 (`ISP1`=7, `ISP2`=8), `LSB-FPGA` on `McSPI1` CH2; LSB-FPGA regs at AXI `0x44A00000` + `0xE0000904=0x00030031`, `0xE0000908=0x00000014`, `0x40010008=0xC03007E0`. | **FINDING** (frame + CS-mux topology) + **ASSERTED** (seeded FPGA status bits) — `findings/hardware/COPROCESSOR_TRANSPORT.md`; `findings/peripherals/LSB_FPGA_{CHARACTERIZED,NAMED}.md`. | Register side-effects beyond XAUI/bring-up status bits not dynamically simulated. |
| `c2c.IobXauiAhbDevice` | GPMC CS2 IOB/FPGA AHB window @`0x1C000000` (size `0x04000000`), mapped via `FUN_80727028`; serves XAUI link/PCS/PHY ready regs (`+0x0010`, `+0x002C`, `+0x0248`), ready flag at `+0xB0020=0x4`, toggling handshake (`0x000B0000..0x000B0FFF`), HBA0 SATA3 AHCI direct/shadow SREGs (`0x00060000..0x00070010`, `0x000B0680..0x000B06CC`), and is paired with `MarvellSideSataBridge` (`W<reg><data>`/`SM2246EN`) + LSB-FPGA `0x44A0000C` SSD detect. | **ASSERTED** (status/AHCI readback at **FINDING**-identified offsets). | Models ready/AHCI link state without full dynamic DMA completion interrupts. |
| `c2c.IobMailboxDevice` | GPMC AHB IOB mailbox memory-slave @`0xA5800000`, separate from SPI. | **FINDING** — `COPROCESSOR_TRANSPORT.md` §1 (GPMC). **Added in this PR.** | Mailbox event/IRQ semantics (EVNT_MAILBOX_IOB0/1, ISP0/1/2) and descriptor format not modeled; plain word store. |
| `c2c.IobDoorbellDevice` | Xtensa-local doorbell MMIO @`0x3FFE8000`/`0x4FFE8000`; +0x04 write fetches a 16-byte DDR descriptor (`0x800B0FFC`/`0x800B17FC`) and dispatches to the core. | **ASSERTED** (shape plausible; descriptor layout + the `handle_c2c_command` opcodes are invented). Decoupled from SPI in this PR. | Real doorbell register layout and descriptor schema unverified against fw; dispatched command set is asserted. |
| `xtensa.Iob0Coprocessor` | SATA 3.0 link status, HDMI 1080p tx, STACCELERO accelerometer tilt. | **ASSERTED** — fabricated state (fixed accel vector, HDCP flag, tilt math). Topology contradicts findings (SATA controller is in the IOB and HBA0 goes via SATA3 FPGA; side-SATA is a Marvell serial bridge — memories `marvell-side-sata-bridge`, `dsmc2-two-sata-hbas-one-active`). | Not real Xtensa fw; `handle_c2c_command` opcodes (0x10/0x20/0x26) invented. Depth target: co-sim real `iob0` fw. |
| `xtensa.Iob1Coprocessor` | Black-shading dark cal, defect-pixel tables, Bayer green align. | **ASSERTED** — invented cal results and defect coordinates. (Real cal subsystem is characterized in `findings/subsystems/CALIBRATION_SUBSYSTEM_CHARACTERIZED.md`, not implemented here.) | No real algorithm; defect lists hardcoded. Depth target: co-sim real `iob1` fw. |
| `xtensa.IspCoprocessor` (ISP1/ISP2) | REDCODE wavelet (DWT), sub-band slicing, DDR training. | **ASSERTED** — `zlib.crc32` stand-in for REDCODE; compression ratio is a string. | No real wavelet/encode; the real encode pipeline is in `findings/peripherals/ISP_ENCODE_PIPELINE.md` / `ISP_IOB_PIPELINE.md`. Depth target: co-sim real `isp` fw (QEMU-xtensa). |
| `xtensa.XtensaCore` (base) | Windowed-register core, IRAM/DRAM load, fake `step()`. | **ASSERTED** — `step()` advances PC by a constant; does not execute Xtensa. (ABI facts right: windowed, IRAM `0x40000000`, memory `mcu-toolchains`, `findings/peripherals/XTENSA_AND_CALLINGCONV.md`.) | No ISA execution. Depth target: replace with QEMU-xtensa co-sim. |
| `dsp.TiC64xDsp` | Parses `rdc_dsp_app.out` TI COFF header/sections; DSP/BIOS tick counter. | **FINDING (partial)** — COFF parse is real and matches `findings/subsystems/DSP_C64X_CHARACTERIZED.md`'s premise; no C64x execution. | DSPLink protocol modeled only as queues; no ISA. |
| `dsp.DspLinkIpc` | ARM↔DSP MSGQ shared-memory queues. | **ASSERTED** — generic deque IPC; message ids/format not from fw. | Real DSPLink MSGQ layout unverified. |
| `dsp.CImagingServer` | Autofocus contrast curve, Haar face detection. | **ASSERTED** — Gaussian sharpness + hardcoded face box; demonstration only. | Not a real algorithm or server protocol. |
| `dsp.RAudioServer` | 4-ch audio preamp/peak meter, PCM/WAV synth. | **ASSERTED** — synthesized tone/hum; plausible but not fw-derived. | Preamp/metering values invented. |
| `dsp.OmapMailbox` | OMAP3630 Inter-Processor Mailbox @`0x48094000` (IRQ 26), 8 FIFOs, `SYSCONFIG`/`SYSSTATUS`, IRQ status/enable. | **TRM/DATASHEET** (SPRUGN4 ch.13) + fw DSPLink `OMAP3530_halIntCtrl` (`0x80EB63C0`). | Interrupt assertion back to DSP side not simulated. |
| `dsp.OmapIva2Mmu` | OMAP3630 IVA2.2 MMU @`0x5D000000` (`MMU_SYSCONFIG`/`SYSSTATUS`, TTB, TLB load/flush) + `IVA2_MEM` @`0x5C000000`. | **TRM/DATASHEET** (SPRUGN4 ch.5) + fw DSPLink `OMAP3530_halMmuCtrl` (`0x80EB7148`). | Page-table translation not enforced for host-side DSP stub. |

## Devices (`emulator/devices/`)

| Model | What it claims | Source | Gaps |
|---|---|---|---|
| `intc.OmapIntc` | OMAP3 MPU INTC @`0x48200000`, banks 0..2, MIR/ISR/PENDING. | **TRM/DATASHEET** (SPRUGN4). | — |
| `prcm.OmapPrcm` | CM/PRM clock+reset @`0x48004000`/`0x48005000`, DPLL lock. | **TRM/DATASHEET** + fw addrs `0x80674A00`. | — |
| `sdma.OmapSdma` | 32-ch sDMA @`0x48056000`, CCR/CSR, IRQ 15; GPMC NAND prefetch stream + McSPI TX/RX byte-frame DMA. | **TRM/DATASHEET** + fw `spi_transfer` (`0x806D588C`). | Transfers complete instantaneously on CCR enable. |
| `mcspi.OmapMcspi` | 4× McSPI, CHCONF/CHSTAT/CHCTRL/TX/RX; 8-bit byte path + `rx_dma_fifo` for sDMA transfers with sub-transfer phase preservation across `CHCTRL` toggles. | **TRM/DATASHEET** (SPRUGN4 ch.20). | Full FIFO-threshold pacing simplified; loopback default for unattached targets. |
| `mcbsp.OmapMcBSP` | McBSP 1..5, DRR/DXR, SPCR DLB loopback. | **TRM/DATASHEET** + fw offsets (omap_regs.tsv). | — |
| `gptimer.OmapGpTimer` | GPTimers 1..11, TISR/TCLR/TCRR; timer 2 → ThreadX tick. | **TRM/DATASHEET**. | — |
| `uart.OmapUart` | 16550 UARTs 1-4, LSR/SSR, UART3 RCP, UART4 logs. | **TRM/DATASHEET** + fw role mapping. | — |
| `i2c.OmapI2c` | I2C 1-3 master, `BUF`/`BUFSTAT`, multi-chunk (>64B) `XRDY`/`RRDY`/`ARDY` interrupts, TWL4030 protect-key. | **TRM/DATASHEET**. | — |
| `gpio.OmapGpio` | GPIO 1..6 dir/data/edge, pin callbacks, GPIO130 mux. | **TRM/DATASHEET** + fw pin roles. | — |
| `gpmc.OmapGpmc` | GPMC + Micron MT29F8G08 NAND, geometry descriptor, backing store (program/read/erase), Micron on-die ECC feature (0x90) round-trip, Hamming ECC-result window. | **TRM/DATASHEET** + fw descriptor `0x81EF7824` + fw NAND driver (`_nand_read_sector` @0x8067ccc8, `_nand_set_internal_ecc` @0x8067b984, `_nand_set_feature`/`_nand_get_feature`). | NAND DMA source is modeled via the SDMA "instant 0xFF / dest fill" (`sdma.py`), not a true GPMC-prefetch-FIFO→DDR stream; GPMC Hamming ECC result not computed (on-die ECC path doesn't cross-check it). A fully mountable synthesized YAFFS2 image is NOT yet produced — see `nand_image.py`. |
| `hdq_onewire.OmapHdq1Wire` | HDQ/1-Wire @`0x480B2000`; Maxim ROM cmds, CRC-8, MD5 salt, signature offsets. | **FINDING** — `findings/peripherals/ONEWIRE_SIGNATURE_AND_SENSOR_ID.md` (salt, record offsets). | Board ROM IDs/serials are synthetic (see below) — correct by design. |
| `hdq_onewire` board data | 5 synthetic DSMC2 boards (LSB `131-0727`, RSB `131-0726`). | **SYNTHETIC (intentional)** — placeholder ROM IDs/serials; real serials are unit-identifying and must never be committed (per RED redaction rule). | By design; part numbers (`131-07xx`) are public-OK, suffixes faked. |
| `cam_isp.OmapCamIsp` | OMAP Camera ISP @`0x480BC000` + SCM_ID `0x4830A000`. | **TRM/DATASHEET** + fw regs (omap_regs.tsv); DIE_ID synthetic. | — |
| `l4_interconnect` (`OmapL4Ap`/`OmapScm`) | L4 AP/Link Agent, SCM/PADCONF; `0x49000000` = L4-PER AP (Sonics S3220 `0x40000800`). | **TRM/DATASHEET** (Table 2-3/2-4). Correctly replaces the old synthetic C2C at `0x49000000`. | — |
| `i2c_dsmc2_chips.Si5346ClockSynth` | Si5346 (`0x6C`) ISP clock synth, page map. | **FINDING** — `findings/peripherals/SI5346_CLOCK.md`; `reference/I2C_DEVICE_MAP.md`; memory `si5346-isp-clock`. | 378-entry register map not fully loaded; ACK-level only. |
| `i2c_dsmc2_chips` (DAC7578, FT5x06) | DAC7578 (`0x49`/`0x4B`), FocalTech FT5x06 (`0x3E`). | **DATASHEET** + `reference/I2C_DEVICE_MAP.md` (IDs synthetic). | — |
| `i2c_ucd.*` | TI UCD9090/UCD90160 PMBus rail monitors, MFR_GPO. | **FINDING** — `reference/UCD_REGISTER_MAP.md`, `clean/ucd90160.c`; `reference/POWER_RAIL_MAP.md`. | Rail sample tables are representative, not a live power model. |
| `i2c_pmic.*` (TWL4030) | TWL4030 PMIC protect-key unlock. | **TRM/DATASHEET** (TWL4030 TRM). | — |
| `i2c_thermal.*` | Thermal sensor(s) on I2C. | **DATASHEET** + `reference/I2C_DEVICE_MAP.md`. | Temperature values representative. |
| `i2c_maxtouch.MaxTouch` | Atmel maXTouch (`0x4B`), T5..T40 objects, 1024×576. | **DATASHEET** (Atmel maXTouch obj protocol). | Touch stream synthetic. |
| `i2c_eeprom.*` (EDID) | 24C02 EDID @`0x50`, VESA EDID 1.3 for RED Touch 7.0. | **DATASHEET** (VESA EDID). | — |
| `i2c_olpf.*` | OLPF / lens-related I2C device. | **DATASHEET** + `reference/I2C_DEVICE_MAP.md`. | Representative. |
| `dss.OmapDss` | OMAP DSS, 6 pipelines, DDR framebuffers (LCD/HDMI/HDSDI). | **TRM/DATASHEET** + fw fb addresses (`0x87400000` etc.). | Renders from DDR; no real DISPC timing. |
| `accessory_bus.AccessoryBus` | TLV module enumeration, WHO/VERSION/REDINFO handshake. | **FINDING (partial)** — module/descriptor idea from `findings/subsystems/SIDE_MODULE_CHARACTERIZED.md`, memories `rsb-sidekick-connector`, `serial2-capability-surface`; serials synthetic. | Handshake strings asserted; real serial-2 RCP/flat-UI protocol not modeled. |
| `lens_mount.LensMount` | Canon EF electronic lens state. | **ASSERTED** — representative lens metadata. | Not from fw lens protocol. |
| `rig_manager.RigManager` | Connector/accessory topology + presets. | **TRM/DATASHEET-adjacent** (physical topology; detect GPIOs match `rsb-sidekick-connector`). | Orchestration helper, not a hardware register model. |

---

## Follow-up depth work (NOT in this PR — listed per the design doc)

These are the depth milestones from `research/docs/build/VBRAIN_DEPTH_DESIGN.md` (RED-local).
They are the path from "boots to the ThreadX park" to "exercises the source build's late
init / UI / record / playback on models we trust." Each item names the finding that is the
source of truth.

1. **ISP/IOB Xtensa co-simulation (biggest lever).** Replace the asserted `xtensa.*`
   behavioural models with the *real* ISP1/ISP2/IOB0/IOB1 firmware (`image2/{isp,iob0,iob1}`,
   plaintext, already decompiled) running under **QEMU-xtensa** (core config from the fw;
   windowed ABI), bridged to vbrain at the SPI-register / GPMC-mailbox / XAUI-DMA boundary.
   Source of truth: `findings/peripherals/PERIPHERAL_FIRMWARE.md`, `ISP_IOB_PIPELINE.md`,
   `{ISP,IOB}_XTENSA_NAMED.md`, `XTENSA_AND_CALLINGCONV.md`.

2. **FPGA behavioural register models** (LSB / ISP / IOB FPGAs). Model what the app reads/
   writes incl. status/ready bits, the rev-`0x14` low-window gate, the XAUI-up high-window
   gate, and the ProRes-proxy status, so `lsb_fpga_init`/`iob.c` driver sequences complete.
   The transport this PR fixed is the substrate; the register *contents/side-effects* are the
   next layer. Source: `findings/peripherals/LSB_FPGA_{CHARACTERIZED,NAMED}.md`,
   `clean/lsb_fpga.c`, `iob.c`.

   **Milestone 1 (`0x03300FF7` board-bring-up barrier) — CLEARED** (verified by `boot_to_ui_bringup` /
   `test_ui_bringup.py`): with OMAP sDMA McSPI transfers, GPIO-76/77 `CsMuxSpiTarget`, seeded
   LSB-FPGA status/version registers on `McSPI1` CH2 (`0xE0000904=0x00030031`,
   `0xE0000908=0x00000014`, `0x40010008=0xC03007E0`), `IobXauiAhbDevice` @`0x1C000000`, and
   `slice_size=12_000` supplying sufficient GPTimer2 ticks for `Fan Init`, `weapon_boards_init`
   (@0x809B378C) and `weapon_left_side_board_init` (@0x809B5354) complete: all four
   board-dependent bits (Boards 0x400, Fan 0x800, XAUI 0x200000, Mailbox 0x1000000) set,
   reaching `0xC3300FFF` and populating `hw_cap` (@0x815811F4) to `0x00041182`. Continuing past
   `0xC3300FFF` (with `0x1C0B0018`/`0x1C0B001C` ready for `wait_for_iob_status` @0x807B3050)
   advances through SATA (`0x4000000`), Media (`0x8000000`), IOB/ISP Binary Download (`0x1000`),
   Monitor Pipeline (`0x2000`), HDMI (`0x4000`), and HDSDI (`0x8000`) to `0xCF30FFFF`.
   **Milestone 2 (`0xCF3CF7FF` full driver cascade → `run_application` / `Camera State Manager`) — CLEARED** (verified by `boot_to_application` / `test_ui_bringup.py`):
   with toggling IOB AHB status (`0x1C0B0000..0x1C0B0FFF`, `+0xB0020=0x4`), `EdidEeprom(0x50)` on `I2C2` and `I2C3`, `OmapMailbox` (`0x48094000`) + `OmapIva2Mmu` (`0x5D000000`) + `IVA2_MEM` (`0x5C000000`), and multi-chunk (>64B) Master TX in `OmapI2c`, `Driver thread` completes SATA (`0x4000000`), Media (`0x8000000`), IOB/ISP Binary Download (`0x1000`), Monitor Pipeline (`0x2000`), HDMI (`0x4000`), HDSDI (`0x8000`), Monitor Splash Screen, DSP (`0x40000`), Contiguous Media Space (`0x10000000`), Audio (`0x80000`), and Cal Load (`0x800000`) -> `0xDFBCFFFF`, then puts `0x81E53618` (@`0x8066D3C8`) so `Starter Thread` enters `run_application` (@`0x80008CB4`), creates `APP_SEMAPHORE` (@`0x814C6D1C`), and spawns `Camera State Manager`.

   **Milestone 3 (SATA3 HBA0 / UDF Media Mount & N-frame `.R3D` Recording) — CLEARED** (verified by `test_milestones_3_and_4.py`):
   added `IobXauiAhbDevice` HBA0 AHCI (`0x00060000..0x00070010`) and `SATADriver.c` shadow SREGs (`0x000B0680..0x000B06CC`, `PxSSTS = 0x133`), `LSB-FPGA` SSD detect (`0x44A0000C` bit 18), `MarvellSideSataBridge` (`"W<reg><data>"`, `SM2246EN` SMART `0x90`), ECMA-167 / UDF 2.01 volume image (`minimag_udf.img` + `verify_udf_filesystem()`), and deterministic N-frame dual-ISP (`ISP1`/`ISP2`) wavelet `.R3D` recording (`record_clip_frames` / `inspect_r3d_clip` with `REDf` boxes and `r3di` offsets).

   **Milestone 4 (In-Camera Clip Playback, Persistent Look/Presets & Redpine Wi-Fi / RFTP) — CLEARED** (verified by `test_milestones_3_and_4.py`):
   implemented in-camera clip playback transport (`PLAYING`/`PAUSED`/`SHUTTLE_2X`/`SHUTTLE_4X`, frame stepping, In/Out markers, metadata inspector) in `RedUiEngine`, persistent user presets (`/data/presets/` and MINI-MAG export), `.cube` 3D LUT import, ASC-CDL import/export, reticle/overlay/key-profile persistence, `RedpineRs9113Wifi` SPI host commands, and `RftpServer` TCP/local file transfers (`LIST`/`GET`/`PUT`/`STAT`/`DELETE`).

3. **Subsystem register models to verify/upgrade from ASSERTED → FINDING**: the SATA HBA0/
   HBA1 topology (AHCI HBA0 via SATA3-FPGA + side Marvell serial "W<reg><data>" bridge),
   mini-mag SM2246EN, Redpine RS9113 Wi-Fi SPI host interface, the C64x+ DSPLink protocol,
   the side-handle/expander STM32F303 serial-2 (RCP / flat-UI). Sources: memories
   `marvell-side-sata-bridge`, `dsmc2-two-sata-hbas-one-active`; `findings/peripherals/
   MINIMAG_SM2246EN_*`, `findings/subsystems/{WIFI_REDPINE,DSP_C64X,SIDE_MODULE}_CHARACTERIZED.md`.

4. **The 4 depth milestones** (each a `wb.sh movetest` level — stock vs source build diffed
   under one instruction budget): (1) boot past late driver init (hw_cap populated, all
   driver threads up); (2) UI up (DSS framebuffer frames match stock) + RCP GET/SET over TCP;
   (3) media mount (UDF/FileX on a disk image) + clip record of N frames (R3D bytes vs stock);
   (4) playback, menus/presets, Wi-Fi RFTP transfer. That diff is the acceptance test for
   swapping source-build modules from asm to C.

**Out of scope entirely**: gate-level FPGA / wavelet / REDCODE bitstream emulation; sensor
photon simulation (vbrain uses a reference frame).

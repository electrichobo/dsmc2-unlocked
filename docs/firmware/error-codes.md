# RED DSMC2 Error-Code Dictionary & Log Triage Guide

This document is the authoritative dictionary and technical reference for numeric error codes, system states, and coprocessor fault signatures found in RED DSMC2 logs (`dsmcdbg log`). 

The codes documented here were reverse-engineered directly from **Firmware 7.4.1** binaries (`dsmc_program.mem`, `iob0/dram.bin`, `isp/dram.bin`).

---

## 1. Fast Reference: The Common Codes

| Log Code / Line | Subsystem | Meaning & Physical Cause | Recommended Action |
|---|---|---|---|
| `SYSTEM_ERROR (34)` | System State Machine | Camera entered fatal fault state (State 34). Halts recording/preview. | Walk previous log lines for the trigger event (`C2C_ERROR`, `X12`, `FAILED_IOB`, etc.). |
| `Error: 0x01` | Hardware Boot / IOB & ISP | Primary board initialization failure during boot. Main ARM got no response from IOB or ISP coprocessor. Most common failure on SCARLET-X / EPIC-X. | Strip to bare brain, use official AC adapter, re-seat board-to-board flex cables, check IOB/ISP rails. |
| `Error: 0x1D` / `0x11D` | Peripheral / Accessory Bus | Accessory or external module communication timeout or pin short on startup. | Detach handles, top plates, and lens mounts; inspect gold leaf pins. |
| `PCS error 103: 0x...` | XAUI SerDes Link | Physical Coding Sublayer code 103 (`0x67`): 8b/10b lane sync loss or disparity error between IOB and ISP coprocessors during DMA. | Run `dsmcdbg log health`. Check ISP/IOB power rails and board flex seating. |
| `ISP1_XAUI_PCS_STATUS = 0xf` | ISP XAUI Receiver | All 4 XAUI lanes unaligned / lost bit lock. Zero video data can transfer to ISP. | Check ISP clocks (0x88900034) and power supply rails. |
| `SYSTEM_ERROR_X12` | Sensor Interface | 12-lane high-speed SERDES link between sensor and ISP failed to lock or align. | Re-seat sensor flexible interconnect cable; inspect connector pins. |
| `SYSTEM_ERROR_FAILED_IOB` | I/O Board Coprocessor | The IOB Xtensa coprocessor (SATA, HDMI, SPI) hung or missed heartbeats. | Check IOB 1.0V rail (`IOB/Vcc 1.0`) and peripheral bus activity. |
| `SYSTEM_ERROR_C2C_ERROR` | Inter-Processor Mailbox | Chip-to-Chip mailbox communication timed out between OMAP ARM and coprocessors. | Inspect coprocessor heartbeat registers (`debug heartbeat all`). |
| `SYSTEM_ERROR_RECORDING_ERROR` | Storage / Media | Unrecoverable write failure to the RED MINI-MAG media drive. | Inspect MINI-MAG connector pins; test media format status. |
| `Media authentication error` | Security / Media | Media drive failed hardware cryptographic signature validation. | Ensure media is genuine RED MINI-MAG or that custom media patch is loaded. |

---

## 2. The System State Machine (States 0..35)

The camera application manages operation through a finite state machine (`system_state_2_if.cpp`, table located at `0x80f8a4b8` in `dsmc_program.mem`). When querying `get system_state` in the debug shell or inspecting state transitions in boot logs, states are indexed 0 to 35:

| State ID | Name | Role & Description |
|:---:|---|---|
| **0** | `NONE` | Uninitialized starting state. |
| **1** | `INITIALIZE` | Hardware low-level boot initialization in progress. |
| **2** | `RUN` | Core OS and driver background threads running. |
| **3** | `CAPTURE` | Sensor capture pipeline active. |
| **4** | `ACCEPT_SLA` | Waiting for software license agreement confirmation. |
| **5** | `WAIT_FOR_CAL_AND_MONITOR_LUT_LOAD` | Loading sensor black shading calibration and monitor 3D LUTs. |
| **6** | `PREVIEW_START` | Initializing live preview pipeline and display buffers. |
| **7** | `WAIT_FOR_UPGRADES` | Checking attached peripherals for pending firmware upgrades. |
| **8** | `UPGRADES_COMPLETE` | All peripherals and accessory firmware verified up to date. |
| **9** | `PREVIEW` | Live viewfinder and monitor preview active. |
| **10** | `READY_TO_RECORD` | Camera fully armed, calibrated, and ready to record footage. |
| **11** | `PREVIEW_RESTART` | Restarting preview pipeline after format/resolution change. |
| **12** | `REAPPLY_MONITOR_MAPPINGS` | Reapplying display LUTs and output color routing. |
| **13** | `ALGO_START` | Auto-exposure or autofocus algorithm running. |
| **14** | `ALGO_ABORT` | Auto-algorithm aborted. |
| **15** | `RECORD` | Recording stream engaged. |
| **16** | `RECORD_INIT` | Pre-allocating frame buffers and initializing media writer. |
| **17** | `PRE_RECORD_ACTIVE` | Pre-record rolling memory buffer active. |
| **18** | `BEGIN_RECORD` | Committing clip header and opening R3D container. |
| **19** | `RECORD_ACTIVE` | Actively encoding wavelet frames and writing to MINI-MAG. |
| **20** | `FINALIZE` | Flushing remaining frames and closing clip container. |
| **21** | `FINALIZE_FOR_SHUTDOWN` | Emergency clip flush before power-down. |
| **22** | `STREAM_3DLUT` | Streaming 3D LUT payload to Left Side Board (LSB) FPGA. |
| **23** | `CALIBRATE` | Executing sensor black shading calibration routine. |
| **24** | `POWER_SAVE` | Standard power save standby. |
| **25** | `POWER_SAVE_LEVEL_1` | Display backlight dimmed. |
| **26** | `POWER_SAVE_LEVEL_2` | Displays turned off, sensor idle. |
| **27** | `POWER_SAVE_LEVEL_3` | Deep standby. |
| **28** | `PLAYBACK` | Playback mode engaged. |
| **29** | `PLAYBACK_INIT` | Parsing clip metadata and index on media. |
| **30** | `PLAYBACK_ACTIVE` | Actively decoding and displaying recorded clip. |
| **31** | `PLAYBACK_STOP` | Stopping playback stream. |
| **32** | `PLAYBACK_STOP_FOR_SHUTDOWN` | Halting playback stream to allow clean shutdown. |
| **33** | `UPGRADE` | Firmware flashing or package installation in progress. |
| **34** | `SYSTEM_ERROR` | **System fault state.** Critical failure halted operation. |
| **35** | `SHUTDOWN` | Clean system power-down sequence. |

---

## 3. Fatal Fault Triggers: Transition Events to State 34

State 34 is not entered spontaneously; it is triggered by an event message dispatched to `system_state_2_if::handle_event`. The firmware defines the following event codes that transition the camera into `SYSTEM_ERROR`:

- **Event 25 — `SYSTEM_ERROR_C2C_ERROR`**: Chip-to-Chip inter-core mailbox packet timeout or CRC error between OMAP and ISP/IOB.
- **Event 26 — `SYSTEM_ERROR_RDS_ERROR`**: Corrupted packet sequencing in RED Data Stream from sensor to wavelet encoder.
- **Event 27 — `SYSTEM_ERROR_DDR_ERROR`**: DDR memory training, write leveling, or memory test failure.
- **Event 28 — `SYSTEM_ERROR_DROPPED_FRAMES`**: Recording buffer overrun due to media write throughput falling below camera data rate.
- **Event 29 — `SYSTEM_ERROR_FAILED_IOB`**: I/O Board Xtensa coprocessor crashed or stopped responding to heartbeats.
- **Event 30 — `SYSTEM_ERROR_RECORDING_ERROR`**: Unrecoverable write error to the MINI-MAG storage medium.
- **Event 31 — `SYSTEM_ERROR_IO_ERROR`**: Storage I/O failure accessing internal NAND Flash partitions.
- **Event 32 — `SYSTEM_ERROR_AUDIO_OVERFLOW`**: Audio DSP DMA buffer overflow.
- **Event 33 — `SYSTEM_ERROR_SENS_INIT`**: Image sensor power-on sequencing, clock distribution, or register initialization timed out.
- **Event 39 — `SYSTEM_ERROR_X12`**: 12-lane high-speed SERDES sensor link failure.
- **Event 45 — `SYSTEM_ERROR_UPGRADE_ERROR`**: Firmware package verification or flashing failed.
- **Event 56 — `CLEAR_SYSTEM_ERROR`**: Administrative command to clear system fault state (usually rejected if hardware condition persists).

---

## 4. XAUI PCS Errors (`PCS error 103`)

The DSMC2 architecture uses a 4-lane XAUI interface (running at 3.125 Gbps per lane, 8b/10b encoded) to move raw image and control data between the **IOB (I/O Board)** and **ISP (Image Signal Processor)** coprocessors.

In `slot/iob0/dram.bin`, the IOB firmware contains four specific diagnostic strings:
```
E: isp_%d PCS error : 0x%x
E: isp_%d PCS error %d: 0x%x
E: iob (isp_%d) PCS error %d: 0x%x avoided
E: iob (isp_%d) PCS error %d: 0x%x
```

### What is Code 103?
When `dsmcdbg` logs show `iob (isp_1) PCS error 103: 0x...`, code **103** (`0x67` in hex) represents the transceiver status register condition where:
1. One or more 8b/10b lanes lost comma (`/K28.5/`) character alignment.
2. An unexpected running disparity error occurred during high-speed DMA transfers across the inter-board bridge.
3. The register value following the colon (e.g. `0x8004002c`) gives the specific PCS lane status register address.

---

## 5. Startup Modal Dialog Codes (`Error: 0x01`, `Error: 0x1D`)

There is an important distinction between **rolling debug log telemetry** (`log.data`, `dsmcdbg log`) and **on-screen bootloader modal dialogs**:
- Rolling debug logs record fine-grained runtime events and driver messages (e.g., `SYSTEM_ERROR (34)`, `PCS error 103`).
- When hardware fails early during power-on self-test (POST) before the Linux OS or user interface runtime finishes starting, the camera halts immediately and renders a fatal modal dialog on the attached display:
  ```
  Startup Error
  Error: 0x01
  Camera will shut down.
  ```

### `Error: 0x01` — Primary Board / Coprocessor Init Failure
- **Applicable Bodies**: SCARLET-X, EPIC-X (DSMC1), and early DSMC2 brains.
- **Root Cause**: During early boot, the main ARM supervisor processor performs a power-good and communication ping over the inter-board bus (SPI/I2C/mailbox) to the secondary coprocessor boards:
  - **IOB (I/O Board)**: Manages SSD/SATA storage, HDMI/SDI video outputs, and external peripheral control.
  - **ISP (Image Signal Processing Board)**: Houses the FPGA/ASIC pipeline for debayering and sensor timing.
  If either coprocessor fails to assert its `POWER_GOOD` rail or fails to respond to the initial handshake ping within the timeout window, the boot sequence halts with `Error: 0x01`.

#### Physical & Hardware Causes
1. **Unseated or Corroded Board-to-Board Interconnect Cables**: The internal multi-pin flex cables bridging the main board to the IOB/ISP boards can become loose from vibration or thermal cycles.
2. **Defective or Collapsed Voltage Rail**: A burned LDO or DC-DC step-down regulator on the IOB (e.g., 1.0V core rail, 1.8V I/O, or 3.3V rail) preventing coprocessor boot.
3. **Power Supply Voltage Sag / Battery Plate Fault**: High contact resistance on third-party V-Mount / Gold-Mount battery plates or an undervoltage battery dropping below threshold under boot surge.
4. **Corrupted Coprocessor SPI Flash**: The firmware image for the Xtensa or FPGA coprocessor stored in dedicated SPI NOR flash is corrupted.

#### Triage Procedure for `Error: 0x01`
1. **Strip to Bare Brain**: Remove all accessories—lens mount, side handle, top handles, battery plates, modules, and RED MINI-MAG / 1.8" SSD.
2. **Use Official RED AC Adapter**: Disconnect all batteries. Plug the official RED AC power supply directly into the DC IN jack to eliminate battery voltage sag.
3. **Connect Only a Known-Good RED Touch/LCD**: Boot with only the display connected to observe the splash sequence.
4. **Hardware Flex Inspection (If Persistent)**: If bare-brain AC boot still produces `Error: 0x01`, open the brain chassis and inspect, clean (with 99% isopropyl alcohol), and reseat the high-density board-to-board flex cables. Verify power rail test points against schematic voltages.

---

### `Error: 0x1D` / `Error: 0x11D` — Peripheral & Accessory Bus Fault
- **Root Cause**: Communication timeout or electrical short on the external accessory pin bus (e.g., RED Side Handle, top plate, or electronic smart lens mount).
- **Triage**:
  1. Remove the lens mount and any handles or modules.
  2. Inspect the spring-loaded gold pogo pins and contact pads for dirt, bent pins, or bridging metal debris.
  3. Clean contacts with contact cleaner or isopropyl alcohol.

---

## 6. Tooling: Analyzing and Enriching Logs

### Frequency Analysis & Bloat Elimination (`dsmcdbg log stats`)

Camera logs often contain tens of thousands of lines where the same informational or polling messages repeat constantly across multiple boot sessions. The `stats` command solves this by parsing all sessions, counting error frequencies, and clustering repeated bloat:

```sh
# View error frequencies and bloat across all sessions
cargo run --manifest-path tools/dsmcdbg/Cargo.toml -- log stats <decoded.txt>

# Filter to a specific session
cargo run --manifest-path tools/dsmcdbg/Cargo.toml -- log stats <decoded.txt> --session 1

# Limit bloat line list to top 10
cargo run --manifest-path tools/dsmcdbg/Cargo.toml -- log stats <decoded.txt> --top 10

# Emit structured JSON report
cargo run --manifest-path tools/dsmcdbg/Cargo.toml -- log stats <decoded.txt> --json
```

### Sample Output:
```
RED DSMC2 Log Statistics: cam_fault_log.txt
==============================================================================
Sessions:   3   |   Lines:   24,850   |   Errors:    18   |   Warnings:     5

ERROR FREQUENCY ACROSS SESSIONS (Enriched via Dictionary):
------------------------------------------------------------------------------
  [Fatal] System Fault State (State 34)     Count:     2  |  Sessions: 2/3   
      Subsystem : System Control / RTOS
      Meaning   : The camera state machine transitioned to state 34 (SYSTEM_ERROR)...
      Sample    : [0:00:05:000] ( 1%/1%) E: SYSTEM_ERROR (34)

  [Error] XAUI PCS Lane Sync Loss (Code 103) Count:    14  |  Sessions: 1/3   
      Subsystem : XAUI SerDes / IOB-ISP Link
      Meaning   : Physical Coding Sublayer error 103 on the XAUI interconnect...
      Sample    : [0:00:02:150] ( 1%/1%) E: iob (isp_1) PCS error 103: 0x8004002c

TOP REPEATED / BLOAT LINES (Timestamps Normalized):
------------------------------------------------------------------------------
   COUNT  SESSIONS    NORMALIZED LOG LINE
  --------------------------------------------------------------------------
   1,420  3/3         i2c_read i2c.c timeout
     850  3/3         cal state machine state: 4
     312  2/3         KEY_MAPPING_12 pressed
```

### Inline Diagnostic Enrichment (`dsmcdbg log digest`)

When running `dsmcdbg log digest <decoded.txt>`, any error matched in the dictionary is automatically decorated inline with its human explanation:

```
===== SESSION 1  (lines=1420, dur=0d 00:01:14, err=1, wall=11/05/2026 14:22:01) =====
  furthest state : PREVIEW_START
  milestones     : BOOT > NAND_FS > PLL_LOCK > LSB_INIT > PREVIEW_START
  failures       : {'SYSTEM_ERROR': 1}
  --- denoised event lines ---
    [0:00:05:000] ( 1%/1%) E: SYSTEM_ERROR (34)
       ↳ [Fatal] System Fault State (State 34) (System Control / RTOS): The camera state machine transitioned to state 34 (SYSTEM_ERROR). This halts camera operation and prevents preview/recording.
```

---

## 7. Binary Offset Provenance (Firmware 7.4.1)

For verification against raw firmware binaries using Ghidra or radare2:

- **State machine string table**: `dsmc_program.mem` offset `0x00f8a4b8` (VA `0x80f8a4b8`).
- **State machine event table**: `dsmc_program.mem` offset `0x00f8a6f8` (VA `0x80f8a6f8`).
- **`system_state_2_if` implementation**: `dsmc_program.mem` offset `0x00f88700` (VA `0x80f88700`).
- **IOB PCS error strings**: `iob0/dram.bin` offsets `0x0d7c`, `0x0db4`, `0x0e3c`, `0x0e9c` (DRAM base `0x3ffe0000`).
- **IOB pointer message catalogue**: `iob0/dram.bin` offset `0x0568`..`0x05ac`.

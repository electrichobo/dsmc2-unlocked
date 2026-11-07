# RED DSMC2 Full-System Virtual Camera Emulator

A faithful, full-system hardware and operating system emulator for the **RED DSMC2** cinema camera platform, executing genuine user-supplied ARM machine code and modeling authentic camera hardware, operating system structures, display pipelines, and control interfaces.

---

## 1. Quick Start

### Prerequisites
- Python 3.10+ (tested on Python 3.14 x64 on Windows)
- Required packages:
  ```powershell
  pip install unicorn pillow pytest
  ```

### Running the Web-Based Virtual Camera Studio (Freeze-Proof Modern UI)
```powershell
python -m emulator.cli run
```
This automatically boots the camera and opens the studio in your default browser at `http://127.0.0.1:8765/`.
You can customize the port or disable browser auto-launch:
```powershell
python -m emulator.cli run --web-port 8765 --no-browser
```
Or directly via the web module:
```powershell
python -m emulator.ui.web
```

### Running Headless Virtual Camera with RCP TCP Server (Port 1111)
```powershell
python -m emulator.cli run --headless --port 1111
```

### Executing Firmware Boot Trace
```powershell
python -m emulator.cli boot --instructions 45000000 --json
```

### Running Automated Test Suite
```powershell
python -m pytest
```
Or via CLI with structured JSON report generation:
```powershell
python -m emulator.cli test -o test_report.json
```

---

## 2. Key Capabilities & Verified Milestones

| Capability | Status | Implementation Details |
|---|---|---|
| **ARMv7-A Cortex-A8 Execution** | **VERIFIED** | High-performance Unicorn JIT engine executing genuine `dsmc_program.mem` at `0x80000000`. |
| **TrustZone Monitor Emulation** | **VERIFIED** | Intercepts OMAP3 PPA SMC calls (`smc #0`), maintaining secure state flow. |
| **ThreadX RTOS Scheduler** | **VERIFIED** | Dynamic scheduler tracking with verified TCB discovery (`System Timer Thread`, `SystemInitThread`, `QueueDispatcher`, `log_thread`). |
| **Hardware Interrupt Controller (INTC)** | **VERIFIED** | Complete 96-channel MPU interrupt controller (`0x48200000`), vector dispatch, and ISR acknowledgment. |
| **Power, Reset, Clock (PRCM)** | **VERIFIED** | Smart dynamic DPLL lock sequencing resolving polling loops at `0x80674A00` / `0x80674A64`. |
| **I2C & Touchscreen Digitizer** | **VERIFIED** | I2C1..3 with stateful register maps, Atmel maXTouch multi-touch controller on `0x4B`, and 128-byte VESA EDID 1.3 EEPROM on `0x50`. |
| **Display Pipeline & RedUi** | **VERIFIED** | 1024x576 native LCD panel frame rendering from authentic `splash_screen.bin` and OMAP DISPC framebuffers. |
| **Virtual Storage & Partitions** | **VERIFIED** | Full partition mapping for `/env`, `/image1`, `/image2`, `/data`, `/log`, `/pdata`. |
| **RED Command Protocol (RCP)** | **VERIFIED** | Multi-threaded RCP TCP server on port 1111 and UART3 bridging camera parameter control. |
| **Fault Injection & Snapshots** | **VERIFIED** | Disconnect/reconnect monitor, simulate I2C bus errors, corrupt `/env`, trigger watchdog resets, and full compressed snapshot save/restore. |
| **Web Camera Studio (Zero Freeze)** | **VERIFIED** | Standard library multi-threaded HTTP/MJPEG streaming UI on port 8765 with 16:9 responsive scaling and coordinate mapping. |
| **Vertical MINI-MAG Loader Slot** | **VERIFIED** | Authentic left-bay vertical cartridge slot with active LED, custom firmware mod drag-and-drop, and 1-click baseline revert. |
| **Master DSMC2 Menu Hierarchy** | **VERIFIED** | Complete 8-category menu tree, 8-tile `Settings > Look >` matrix, and authentic `Overlays > Tools` ribbon & dropdown. |

---

## 3. Web Virtual Camera Studio & Studio Interfaces

### Web Virtual Camera Studio (`http://127.0.0.1:8765`)
The primary modern, freeze-proof interface built entirely with Python standard libraries (no Node.js/pip web frameworks required):
- **Vertical RED MINI-MAG Media Slot**:
  - Positioned to the left of the virtual monitor screen, authentic to DSMC2 BRAIN ergonomics.
  - Active status LED: Green (baseline ready), Amber/Cyan (custom mod staged), Off (ejected).
  - Drag-and-drop dropzone supporting user-supplied firmware binary mods (`.bin`, `.mem`, `.pkg`, `.zip`, `.tar`).
  - Staging storage in `firmware_store/custom/` with instant 1-click baseline revert button.
- **Multi-Model DSMC2 Camera Selection**:
  - Interactive selection modal to launch into any extracted DSMC2 camera personality:
    - **SCARLET-W 5K** (`0x16`, DRAGON 5K S35, 5120x2700, 60 FPS)
    - **RED RAVEN 4.5K** (`0x15`, DRAGON 4.5K S35, 4608x2160, 120 FPS)
    - **WEAPON 8K VV** (`0x11`, MONSTRO 8K VV, 8192x4320, 60 FPS)
    - **DSMC2 DRAGON-X 5K** (`0x14`, DRAGON 5K S35, 5120x2700, 96 FPS)
    - **EPIC-W 8K S35** (`0x17`, HELIUM 8K S35, 8192x4320, 60 FPS)
  - One-click model switching in the studio header dynamically updates `/env`, RCP state, default recording format, and slot assets.
- **Virtual LCD Screen (1024x576 Native 16:9 Aspect)**:
  - Responsive recursive viewport scaling fitting any browser window dimensions without aspect distortion.
  - Renders live sensor feed (`res/sensor_feed_cinema.jpg`), authentic splash screen, and OMAP framebuffers.
  - Interactive touchscreen with exact pixel coordinate mapping to the virtual Atmel maXTouch digitizer.
  - Authentic HUD indicators: Timecode, Camera Model, FPS, Shutter Angle, ISO, Format, Iris Aperture, and Media Gauge.
- **Authentic DSMC2 Menu & Tool Overlays**:
  - Complete 8-category master menu tree (`IMAGE`, `MONITORING`, `OVERLAYS`, `POWER`, `PLAYBACK`, `MEDIA`, `PRESETS`, `SETTINGS`).
  - Adaptive button text formatting: automatically scales font from 12pt down to 9pt and wraps to 2 lines to ensure labels never clip or overflow button boundaries.
  - Touch event streamline: distinct `down`, `move`, and `up` action dispatching eliminates mouse drag push-through activations.
  - 8-tile Look matrix (`Menu > Settings > Look >`) with active red tile selection.
  - Exposure Tools ribbon with False Color mode drop-down popup (`Off`, `Exposure`, `Video`, `Focus`, `Edge`, `Gio Scope`) and crosshair reticle.
- **Physical Camera Controls & Developer Workbench**:
  - Distinctive RED **● REC** button, **POWER / RESET**, tactile function keys (**MENU**, **1:1 MAG**, **USER 1**, **USER 2**), and D-pad.
  - Live tabs: Console / Serial Log, RCP Parameters, Lens & Optics (electronic iris, zoom, ultrasonic AF), Media Clips & R3D/RMD Generation, ThreadX RTOS Inspector, MMIO Registers, Fault Injection, and Snapshots.

### Synchronized Desktop Application
A synchronized desktop GUI is also available for offline development environments:
- Dual-zone interface sharing identical camera backend state.
- Live canvas rendering and physical tactile controls.

---

## 4. CLI Interface Reference

The `dsmc2` command line interface provides scriptable commands:

```
usage: dsmc2 [-h] {boot,run,inspect,snapshot,test} ...

Commands:
  boot        Execute firmware boot sequence and output timing/status
  run         Run virtual camera (default: GUI, or --headless with RCP server)
  inspect     Inspect RTOS threads, unknown MMIO registers, VFS, or RCP parameters
  snapshot    Manage full-system state snapshots (save / restore)
  test        Run automated regression test suite and export JSON report
```

### Examples:
```powershell
# Inspect RTOS threads in JSON format
python -m emulator.cli inspect --threads --json

# Inspect unknown MMIO registers
python -m emulator.cli inspect --mmio

# Save a snapshot to file
python -m emulator.cli snapshot save snapshots/baseline.dsmc2 --desc "Booted 45M state"

# Restore a snapshot from file
python -m emulator.cli snapshot restore snapshots/baseline.dsmc2
```

---

## 5. Repository Structure

```
├── emulator/                  # Core virtual camera package
│   ├── core/                  # CPU (Unicorn JIT), SystemBus, Snapshots
│   ├── devices/               # INTC, PRCM, GPTimers, UARTs, I2C, maXTouch, EDID, GPIO, DSS
│   ├── storage/               # CameraEnvironment (/env), VirtualFileSystem
│   ├── rcp/                   # RED Command Protocol parser & TCP server
│   ├── ui/                    # Virtual camera screen, tactile buttons, developer dashboard
│   ├── camera.py              # Dsmc2Camera top-level orchestrator
│   └── cli.py                 # Command line interface
├── tests/                     # Automated regression test suite
│   ├── test_camera_boot.py    # Boot, RTOS, IRQ, and RCP tests
│   ├── test_storage.py        # VFS, partition, and /env tests
│   ├── test_peripherals.py    # Peripheral unit tests
│   └── test_fault_injection.py# Hardware fault and snapshot tests
├── docs/                      # Authoritative architecture and research documentation
│   ├── ARCHITECTURE.md        # System architecture and memory map
│   ├── BOOT_TRACE.md          # Chronological boot trace
│   ├── EVIDENCE_LEDGER.md     # Hardware facts and verified citations
│   ├── FIDELITY_MATRIX.md     # Subsystem fidelity classification
│   ├── UI_RESEARCH.md         # Display, touchscreen, and overlay research
│   ├── TESTING.md             # Testing methodology and results
│   └── LEGAL_AND_PROVENANCE.md# Licensing, provenance, and IP boundaries
├── firmware_store/            # Local, user-supplied proprietary firmware (untracked)
├── persistent_store/          # Virtual camera persistent storage (/data, /log, /pdata)
├── STATUS.md                  # Executive status and milestone completion audit
└── HANDOFF.md                 # Resumption guide and context
```

---

## 6. Safety, Provenance, and Intellectual Property Notice

This repository strictly contains **clean-room emulator source code, test suites, and documentation**.

- **No Proprietary Firmware Included**: Genuine RED binary assets (`dsmc_program.mem`, `bootloader_0x16.bin`, slot files) must be supplied locally by the authorized user and remain strictly untracked via `.gitignore`.
- **Local Development Policy**: All commits, branches, and artifacts remain strictly local to this workspace. No public uploads or remote pushes are authorized.

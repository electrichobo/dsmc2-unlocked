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

### Running the Desktop Application (Virtual Camera + Developer Tools)
```powershell
python -m emulator.cli run
```
Or directly via the app module:
```powershell
python -m emulator.ui.app
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

---

## 3. Desktop Application Overview

The engineering application provides two synchronized zones sharing the exact same emulator backend state:

### Left Zone: Virtual RED Camera
- **Virtual LCD Screen (1024x576 Native Aspect)**:
  - Renders live firmware pixels and authentic RED splash screen.
  - Interactive touchscreen: click and drag mouse gestures are mapped to native coordinates and dispatched to the Atmel maXTouch controller.
  - Real-time HUD indicators: Record status badge, Camera Model (`SCARLET-W 5K`), FPS, Shutter, ISO, Format, SMPTE timecode, and media gauge.
- **Physical Camera Controls**:
  - Distinctive RED **● REC** button (toggles recording and updates HUD state).
  - **POWER / STANDBY** button (triggers hardware reset).
  - Tactile function keys: **MENU**, **1:1 MAG**, **USER 1**, **USER 2**.
  - Directional navigation D-pad (**▲, ◀, ●, ▶, ▼**).

### Right Zone: Developer Dashboard
- **Console / Serial**: Real-time UART4 system log stream with auto-scroll and clear.
- **RCP & Parameters**: Live camera parameter inspector and interactive RED Command Protocol command prompt.
- **ThreadX RTOS Inspector**: Live table displaying all active threads, thread names, states (`READY`, `SUSPENDED`), priorities, entry PCs, and stack pointers.
- **MMIO & Diagnostics**: Unknown MMIO register tracker with access counts, last values, and address classification.
- **Fault Injection**: One-click triggers for LCD disconnection, I2C bus NACK, corrupt `/env`, and watchdog reset.
- **Snapshots**: Save and restore full compressed emulator state (`.dsmc2` files).

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

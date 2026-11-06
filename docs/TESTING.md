# RED DSMC2 Automated Testing and Regression Report

This document outlines the testing strategy, test suite structure, execution commands, and verified results for the RED DSMC2 full-system emulator.

---

## 1. Test Suite Architecture

The test suite is built on `pytest` and structured to validate each implementation milestone without requiring physical camera hardware:

```
tests/
├── test_camera_boot.py        # M2 (ARM JIT), M3 (ThreadX Bringup), M5 (I2C/ARDY), RCP
├── test_storage.py            # M4 (VFS, Flash Partitions, /env Configuration)
├── test_peripherals.py        # M3, M5 (INTC, GPTimer, UART, GPIO, EDID, maXTouch)
└── test_fault_injection.py    # M8 (Hardware Faults, Sensor Failures, Snapshots)
```

---

## 2. Test Execution Commands

### Running with Pytest
```powershell
python -m pytest
```
Output:
```
============================= test session starts =============================
platform win32 -- Python 3.14.9, pytest-9.2.0, pluggy-1.7.0
rootdir: E:\emulator
configfile: pytest.ini
testpaths: tests
collected 12 items

tests\test_camera_boot.py ..                                             [ 16%]
tests\test_fault_injection.py .                                          [ 25%]
tests\test_peripherals.py ......                                         [ 75%]
tests\test_storage.py ...                                                [100%]

============================= 12 passed in 3.92s ==============================
```

### Running via CLI with Structured JSON Report
```powershell
python -m emulator.cli test -o test_report.json
```
This produces `test_report.json` with machine-readable exit codes and timing metrics for CI/CD pipelines.

---

## 3. Test Cases Inventory

### 3.1 Core Execution & Boot (`test_camera_boot.py`)
- `test_rcp_protocol`: Verifies RED Command Protocol parsing for `GET`, `SET`, `RECORD START`, parameter updates, and string serialization.
- `test_camera_subsystems_and_boot`: Initializes `Dsmc2Camera`, verifies subsystem registry, touch packet generation, loads authentic `dsmc_program.mem`, executes 45,000,000 instructions through CRT BSS zeroing, initializes ThreadX, and verifies all 4 RTOS threads.

### 3.2 Persistent Storage & VFS (`test_storage.py`)
- `test_camera_environment_defaults`: Verifies `/env` configuration attributes (model `0x16` = SCARLET-W 5K, sensor DRAGON 5K 5120x2700, serial `016-10042`).
- `test_camera_environment_serialization_roundtrip`: Verifies serialization into null-terminated key=value pairs for flash blocks and subsequent clean deserialization.
- `test_vfs_path_resolution_and_mounts`: Verifies virtual-to-host path resolution for `/env`, `/image1`, `/image2`, `/data`, `/log`, `/pdata`, reading slot files, writing persistent settings, and appending log files.

### 3.3 Hardware Peripherals (`test_peripherals.py`)
- `test_intc_irq_assertion_and_masking`: Verifies 96-channel MPU interrupt controller assertion, masking (`MIR`), active IRQ dispatch (`INTC_SIR_IRQ`), and acknowledgment via `INTC_CONTROL`.
- `test_gptimer_tick_and_overflow_irq`: Verifies GPTimer11 counter ticks, wrap-around reload (`TLDR`), interrupt enable (`TIER`), and overflow IRQ 47 assertion.
- `test_uart_fifo_and_line_buffering`: Verifies 16550 UART line status (`LSR`), transmitter empty flag (`THRE`), transmission line buffering, and receiver data injection (`write_rx`).
- `test_gpio_pin_transitions_and_edge_detection`: Verifies GPIO direction (`OE`), level input, rising/falling edge detection, and IRQ generation.
- `test_edid_eeprom_128_byte_structure`: Verifies 24C02 EEPROM at I2C address `0x50`, VESA EDID 1.3 header, "RED TOUCH 7.0" descriptor string, and checksum modulo 256.
- `test_maxtouch_touch_controller_protocol`: Verifies Atmel maXTouch 7-object table, touch coordinate encoding, and T5 message queue generation.

### 3.4 Fault Injection and Resilience (`test_fault_injection.py`)
- `test_camera_fault_injection_and_snapshots`:
  - **Monitor Detachment**: Detaches I2C3 slaves `0x4B` and `0x50`, verifying that monitor detection fails gracefully without crashing.
  - **Monitor Reconnection**: Re-attaches slaves and verifies detection.
  - **I2C Bus Error**: Injects NACK error flag into `i2c_stat` to verify timeout / bus error handling.
  - **Corrupt Environment**: Injects corrupted binary data into `/env` and verifies safe recovery.
  - **Watchdog Reset**: Simulates PRM watchdog reset, asserting that PC resets to `0x80829F2C`, SP to `0x881D2690`, and instruction counter clears.
  - **Snapshot Roundtrip**: Captures state snapshot before fault, injects watchdog reset, restores snapshot, and verifies exact restoration of PC, SP, memory, and instruction counter.

---

## 4. Verification Evidence Matrix

| Claim | Required Evidence | Measured Local Status | Result |
|---|---|---|---|
| CPU executes image | Repeatable ARM PC progression | Reset vector `0x80829F2C` -> `0x80814380` -> `0x80E9C368` (45M instructions) | **VERIFIED** |
| ThreadX starts | Observable RTOS tasks from real binary | 4 distinct ThreadX TCBs discovered (`System Timer Thread`, `SystemInitThread`, `QueueDispatcher`, `log_thread`) | **VERIFIED** |
| Peripherals initialized | Stateful request/response sequence | I2C ARDY IRQ 61 triggers ISR `0x80688D70`, resuming `tx_semaphore_get` | **VERIFIED** |
| Monitor handshaking | Authenticated EDID & maXTouch | `isMonitorAttached` evaluates to True via I2C `0x4B` & `0x50` | **VERIFIED** |
| RED UI works | Screen pixels generated from firmware | 1024x576 LCD canvas rendering authentic `splash_screen.bin` & DISPC DDR framebuffers | **VERIFIED** |
| Fault recovery | Graceful handling of hardware faults | Monitor disconnect, I2C NACK, corrupt `/env`, watchdog reset, and snapshots verified | **VERIFIED** |

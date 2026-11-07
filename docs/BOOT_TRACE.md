# DSMC2 Virtual Brain ? Firmware Execution and Boot Trace

This document details the chronological execution trace of the genuine user-supplied RED DSMC2 firmware binary (`dsmc_program.mem`, SHA-256: `2bfa1c5211910a30b656ee197e70498a4d7d3c52a0a2df3d85d774a1cb27f88e`) in the virtual emulator environment.

---

## 1. Initial Reset and C Runtime (CRT) Bringup

| Phase | Instruction Range | PC Range | Description / Real Firmware Actions |
|---|---|---|---|
| Reset Vector | 0 | `0x80829F2C` | CPU enters Supervisor mode (`0x13`), interrupts disabled (`0xC0`). LR set to `0x80829F34`. |
| Stack Setup | 1 - 10 | `0x80829F30` | Reads initial SP `0x881D2690` from literal pool (`0x80829FE0`) and sets `SP_svc`. |
| SCTLR Config | 11 - 25 | `0x80829F50 - 0x80829F68` | Disables MMU (bit 0), D-cache (bit 2), and I-cache (bit 12) via Coprocessor 15 (`mrc/mcr p15, 0, r0, c1, c0, 0`). |
| TrustZone SMC | ~120 | `0x80A294AC - 0x80A294B0` | Executes `smc #0` to invoke OMAP3 PPA ROM TrustZone monitor. Handled by emulator returning R0=0. |
| PRCM Clock Setup | ~5,000 | `0x806749F0 - 0x80674A70` | Configures DPLL clock multipliers. Polls `0x48004024` for unlock (bit 0=0) and then polls for lock (bit 0=1). Dynamic model resolves loop. |
| BSS Clear Loop | ~25,000 - 34,500,000 | `0x80814380 - 0x8081439C` | GHS CRT loops clearing uninitialized `.bss` memory from `0x814A1FA0` through `0x881D0000`. Executes ~34.5M instructions. |
| CRT Main Call | 34,500,000 | `0x80829FD8` | Branches to application entry point `starter_entry` at `0x8000941C`. |

---

## 2. ThreadX RTOS Initialization and Thread Creation

Between 34.5M and 45.0M instructions, the firmware invokes ThreadX kernel setup routines:

1. **Kernel Initialization (`_tx_initialize_kernel_setup`)**:
   - Clears scheduler globals: `_tx_thread_created_ptr` (`0x8149CE68`), `_tx_thread_current_ptr` (`0x8149CE5C`), `_tx_thread_execute_ptr` (`0x8149CE60`).
   - Sets up timer tick structures.

2. **Thread Creation**:
   The firmware creates four initial RTOS threads with distinct Thread Control Blocks (TCB magic `0x54485244` = `'THRD'`):

| Thread Name | TCB Address | Priority | Stack Range | Entry PC | Role |
|---|---|---|---|---|---|
| **System Timer Thread** | `0x8149D494` | 0 | `0x8149DF78 - 0x814A1F77` | `0x80E9EEA4` | High-resolution ThreadX software timer tick scheduler. |
| **SystemInitThread** | `0x814CAD40` | 1 | `0x814C6D40 - 0x814CAD3F` | `0x8000941C` | Primary hardware bringup orchestrator (I2C, GPIO, display, accessories). |
| **QueueDispatcher helper thread** | `0x827109B0` | 2 | `0x8270F9AC - 0x827109AB` | `0x80830508` | Inter-thread IPC message queue manager. |
| **log_thread** | `0x82710CA0` | 20 | `0x82710E94 - 0x82714E93` | `0x80843260` | Formatting and transmitting system diagnostic logs to UART4. |

---

## 3. Peripheral Probing and Interrupt Vector Dispatch

1. **Interrupt Vector Table**:
   - The firmware sets the ARM exception vector at `0x80000044` (IRQ vector) to point to `0x80829F00`.
   - `0x80829F00` saves context via `_tx_thread_context_save` (`0x80E9C29C`), calls the OMAP INTC dispatcher at `0x80672B18`, and restores context via `0x80E9C16C`.
   - The dispatcher reads `0x48200040` (`INTC_SIR_IRQ`) and indexes table `0x81406D1C + (irq * 4)` to execute the registered ISR.

2. **I2C Monitor Detection (`isMonitorAttached`)**:
   - `SystemInitThread` initiates an I2C transaction on I2C3 targeting address `0x50` (EDID EEPROM) and `0x4B` (Atmel maXTouch).
   - Firmware calls `tx_semaphore_get` (`0x806886E8`), suspending `SystemInitThread`.
   - The emulator I2C model processes the transaction, sets `I2C_STAT` ARDY bit (`0x04`), and asserts IRQ 61.
   - The INTC routes IRQ 61 to the I2C ISR at `0x80688D70`, which calls `tx_semaphore_put`, resuming `SystemInitThread`.
   - With valid EDID and maXTouch responses returned, `isMonitorAttached(port)` evaluates to True.

3. **ThreadX Scheduler Idle Loop**:
   - At 45M instructions, the RTOS reaches `_tx_thread_schedule` idling at:
     ```
     0x80E9C368: ldr r1, [r2]      ; r2 = _tx_thread_execute_ptr
     0x80E9C36C: cmp r1, #0
     0x80E9C370: beq 0x80E9C368    ; Wait for ready thread or interrupt
     ```
   - Hardware GPTimer11 ticks (IRQ 47) and I2C interrupts seamlessly awaken the scheduler, executing application tasks.

# DSMC2 peripheral / expander MCUs (STM32F303)

Every RED DSMC2 accessory, expander, handle and rear-device board runs an **STM32F303**
(Cortex-M4). Each module's firmware ships inside the camera firmware package, and the camera
flashes the module over the accessory link during an upgrade.

## Firmware image format
- RED header 18–22 bytes, then a raw Cortex-M image. STM32 group: **22-byte header**
  (`payload = filesize − 22`), payload starts at the vector table.
- **Flash base `0x08020000`** — a bootloader region at `0x08000000` precedes the app.
  **Reset handler `0x0802019d`** is the same on every device = shared RED bootloader + startup.
- Full STM32F303 interrupt vector populated (USART1/2/3 ISRs live) → interrupt-driven serial.
- Family confirmed by GPIO base `0x48000000` (STM32F3). Matches the `STM32F303` chip marking.
- To inspect: strip the 22-byte header, load the rest as ARM Cortex-M (ARMv7E-M **Thumb**),
  image base `0x08020000`.

## Comm model
Each board talks to the camera over **USART1**, and uses some of USART2/USART3/UART4/UART5 for
its own sub-functions (extra ports, battery-gauge links). A board with two serial ports (e.g. a
handle or a port expander) typically bridges a camera-facing USART to a downstream one — which is
why a control expander shows one USART to the external connector's RS-232 transceiver and another
to the camera.

## Device roster (role from firmware strings)
| Firmware | Board | Notes |
|---|---|---|
| `sidehandle/sidehd.1.bin` | Side Handle | smallest; USART1 + USART2 only, plus ADC/SPI/timers |
| `reardevices/portx.1/.2.bin` | Port Expander | HDMI + battery + multi-port; `Running PortX application` |
| `reardevices/jetpack.1.bin` | JetPack | HD-SDI / HDMI, aux-out ports, battery |
| `reardevices/vmexp.1.bin` | V-mount Expander | battery / power |
| `reardevices/hdk.1.bin` | HDK | HDMI↔HD-SDI; exposes a `port# A–F / pin# 0–15` command set |
| `flatui/flatui.1-3.bin` | FlatUI display controller | no UART (display/GPIO only) |

## Repair / identification note
A control (CTRL) expander board using an RS-232 transceiver (e.g. MAX3221) to the camera's serial
connector is one of these STM32F303 units: the transceiver's logic side goes to the STM32. How the
STM32 then carries the link to the camera (which UART, pass-through vs re-framed) is board-specific
and best confirmed by tracing. The STM32 I/O is 3.3 V; the camera's own SoC
UART is 1.8 V, so any direct SoC-side tap needs a 1.8↔3.3 V level shifter, never an RS-232 driver.

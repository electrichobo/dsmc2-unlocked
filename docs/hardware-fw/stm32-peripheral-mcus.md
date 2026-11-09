# DSMC2 peripheral / expander MCUs

DSMC2 accessories ship their own MCU firmware inside the camera package, and the camera flashes each
module over the accessory link during an upgrade. **The fleet is a mix of cores — not all one part:**

- **ARM Cortex-M (STM32-class)** — the power/video/expander group below (Redvolt, HDK, port/JetPack/
  V-mount expanders, FlatUI, the side-handle display). This page details that group.
- **TI MSP430 (20-bit MSP430X)** — the battery modules, the SSD-mag controller, Pro-I/O, the LCD/power
  "meizler" board, and the side handles.
- **8051** — `redlink`, a sub-GHz SimpliciTI radio module (the radio chip is the MCU).
- **FPGA / sealed** — the motor-mount boards and the REDmote (decoded by their own bootloaders).
- **Wi-Fi bridge** — the RCom / Redpine board runs an ARM Cortex-M (see the note at the end).

The sections below describe the **Cortex-M / STM32 group** specifically.

## STM32 group — firmware image format
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

## Wi-Fi bridge (RCom / Redpine) board — the J12 / J13 debug header

The RCom / Wi-Fi board runs its own **ARM Cortex-M** bridge MCU that drives the RSI RS9113 (Redpine)
radio and carries RCP over Wi-Fi. (The camera talks to this MCU over SPI, not UART.)

The two pins labelled **J12 / J13**, just above the Redpine radio, are a **debug UART** — the bridge
MCU's own firmware console. Its firmware runs this port at **9600 baud, 8N1**, and prints ASCII log
text (the Wi-Fi association / scan / join state machine). Probe J12/J13 at 9600 8N1 to read it; it is
useful for diagnosing Wi-Fi bring-up on the module. (The board's second internal debug UART, to the
radio module itself, runs at 57600 and is not this external header.)

This is a **separate thing from the camera's own low-level SoC UART** described above — J12/J13 belong
to the Wi-Fi module, not to the camera brain.

# Connecting to a DSMC2 over serial

Two separate serial surfaces, different electronics. Don't mix them up.

## 1. CTRL port (RS-232) — normal control / RCP / shell
The CTRL connector is **RS-232** (bipolar levels), carried to the outside by a transceiver
on the expander. To talk to it you need an **RS-232 transceiver** plus a USB-serial adapter.

| You have | Role |
|---|---|
| **MAX3232** | RS-232 ↔ TTL transceiver (the piece that makes it RS-232). **Power its Vcc from 5V** — at 3.3V the charge pump can't drive clean edges at 115200 and TX corrupts. |
| **FT232 / CP2102** | USB ↔ TTL bridge to the PC, on the MAX3232's TTL side. |
| **RP2040** | Can replace the FT232/CP2102 as a USB-serial bridge (needs bridge firmware). |

**Working combinations (all equivalent):**
- MAX3232 (@5V) + FT232
- MAX3232 (@5V) + CP2102
- MAX3232 (@5V) + RP2040 (bridge firmware)

Wiring: CTRL-RX ↔ MAX3232 ↔ adapter, CTRL-TX ↔ MAX3232 ↔ adapter, GND common. **115200 8N1.**
A bare FT232/CP2102 on the CTRL connector does **not** work — that port is RS-232, not TTL;
you must have the MAX3232. (FT232 alone works only if you tap the TTL side internally.)

## 2. Internal UART (1.8V TTL) — low-level / bench
The camera SoC's own UART is plain **1.8 V TTL** at the silicon — **no RS-232, no MAX3232.**
To reach it:
- A USB-serial adapter (FT232 preferred) **plus a 1.8 ↔ 3.3 V level translation.**
- **Never put 3.3 V straight on the SoC RX pin — it damages the SoC.**

Cheapest level fixes (no dedicated shifter chip):
- **Adapter VCCIO = 1.8 V** (if your FT232 breakout exposes VCCIO): wire both lines direct.
- **Series resistor** (~4.7 kΩ) on the adapter-TX → SoC-RX line (relies on the SoC input clamp,
  current-limited, safe), or a 2-resistor divider (3.3→1.8). SoC-TX → adapter-RX usually direct.
- GND common. Adapter at 3.3 V logic (never 5 V).

This internal UART is for bench / recovery work; the exact pads and the boot/recovery procedure
are board-specific and covered separately. For normal use, the CTRL port above is the one you want.

# RED DSMC2 UI, Display, and Touchscreen Pipeline Research

This document analyzes the display hardware, rendering stack, touch input digitizer, EDID handshaking, and graphical overlays used by RED DSMC2 cameras.

---

## 1. Display Hardware Architecture

### 1.1 Resolution and Panel Specifications
- **Native Resolution**: **1024 x 576** pixels (16:9 widescreen aspect ratio).
- **Verification**: Proven directly from the authentic firmware asset `slot/res/splash_screen.bin`.
  - Compressed file size: 505,674 bytes.
  - Header: `b"REDZLIB0\x08\x00\x1b\x00"` followed by standard zlib compressed stream at offset 12.
  - Decompressed size: exactly **1,769,480 bytes** = 8-byte header + **1,769,472 bytes** of raw pixel data (1024 * 576 * 3 bytes, 24-bit RGB).
  - Unpacked header words: `(768, 768)` indicating internal raster stride configuration.

### 1.2 OMAP3630 Display Subsystem (DSS) & DISPC
The SoC includes an integrated Display Controller (DISPC) mapped at `0x48050000`:
- `0x4805047C` (`DISPC_SIZE_LCD`): Configured by firmware for 1024x576 (`(575 << 16) | 1023`).
- `0x48050480` (`DISPC_GFX_BA0`): Base address of graphics framebuffer in DDR SDRAM (default `0x8C000000`).
- `0x480504A0` (`DISPC_GFX_ATTRIBUTES`): Pixel format descriptor (RGB24 or ARGB32).
- `gw3400` Bridge: Firmware communicates with the MIPI DSI / panel bridge at `0x81027E4B`.

### 1.3 Monitor Attachment Handshake (`isMonitorAttached`)
Firmware function `isMonitorAttached(port_id)` located at `0x80754DC0` queries whether an LCD monitor (such as the RED Touch 7.0 or RED Touch 4.7) is connected to the camera brain:
- Queries monitor port descriptor table at `0x82666934 + (port * 0x90)`.
- Probes I2C3 for:
  1. **EDID EEPROM** at address `0x50` (returns 128-byte VESA EDID 1.3 identifying "RED TOUCH 7.0").
  2. **Atmel maXTouch Controller** at address `0x4B` (returns family ID `0xA4` and object table).
- When both devices respond, the firmware sets the monitor attached flag, configures DISPC timing, and activates the `RedUi` display pipeline.

---

## 2. Touch Digitizer Protocol: Atmel maXTouch (mXT)

### 2.1 I2C Slave Addressing
- Primary RED Touch LCD: Address `0x4B` on I2C3.
- Secondary / Auxiliary Display: Address `0x4A` on I2C3.

### 2.2 Information Block and Object Table
Located at word address `0x0000`:
- Family ID: `0xA4`
- Variant ID: `0x01`
- Version: `0x10`
- Build: `0x01`
- Number of objects: 7

| Object Type | Name | Address | Size | Instances | Description |
|---|---|---|---|---|---|
| **T5** | Message Processor | `0x0030` | 10 | 1 | Message FIFO queue holding touch reports. |
| **T6** | Command Processor | `0x0040` | 6 | 1 | Calibration, backup, and reset commands. |
| **T7** | Power Config | `0x0050` | 3 | 1 | Active and idle scan sleep intervals. |
| **T8** | Acquisition Config | `0x0060` | 10 | 1 | Matrix sensitivity and charge time. |
| **T9** | Touchscreen | `0x0070` | 31 | 1 | Multi-touch detector supporting up to 10 fingers. |
| **T15** | Key Array | `0x00A0` | 11 | 1 | Touch-sensitive bezel keys. |
| **T40** | High Definition Touch | `0x00B0` | 5 | 1 | High-precision position filtering. |

### 2.3 Touch Message Packet Format (T5 Buffer)
When touch events occur, 7-byte packets are appended to the T5 message queue at `0x0030`:
- **Byte 0**: Report ID (`1 + touch_id`).
- **Byte 1**: Status byte (`0xC0` = Detect + Press, `0x80` = Detect + Move, `0x20` = Release).
- **Byte 2**: X position high 8 bits (`X [9:2]`).
- **Byte 3**: Y position high 8 bits (`Y [9:2]`).
- **Byte 4**: Low bits (`(X[1:0] << 6) | (Y[1:0] << 4) | 0x0A`).
- **Byte 5**: Area (`0x05`).
- **Byte 6**: Amplitude / Pressure (`0x80`).

---

## 3. Graphical Overlays and HUD Layout

In `slot/overlays/`, the genuine RED firmware provides 8 pre-compiled overlay templates:
- `Standard (RED).overlay`
- `Overview (RED).overlay`
- `Guides (RED).overlay`
- `Basic (RED).overlay`
- `Stills (RED).overlay`
- `3D Left (RED).overlay`
- `3D Right (RED).overlay`
- `None (RED).overlay`

### HUD Layout Elements Driven by Emulator State:
- **Top Bar (0..40 px)**:
  - REC / STBY Badge (toggles red when recording).
  - Camera Model (`RED SCARLET-W 5K`).
  - FPS (`24.00`, `23.98`, `60.00`).
  - Shutter Angle / Speed (`1/48`, `180.0°`).
  - ISO (`800`).
  - Format (`5K 16:9`).
  - Power Indicator (`DC IN 14.8V`).
- **Bottom Bar (540..576 px)**:
  - SMPTE Timecode (`01:00:00:00`).
  - RED MINI-MAG Status (`RED MINI-MAG 512GB [78% FREE]`).
  - Audio VU Meters (`CH1 -18dB  CH2 -18dB`).
  - Instruction Counter (`INSTR: 45.0M`).

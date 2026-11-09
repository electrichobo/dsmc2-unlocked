# Coprocessor and Peripheral Firmware

The DSMC2 brain loads firmware into a set of non-OMAP processors and accessories
at boot (see [platform.md](../platform.md) for the processor list). A full filesystem
pull ([extraction.md](../firmware/extraction.md)) includes all of these images under
`/image2/`. This page says what they are and which are readable. The serial
protocol that attaches and identifies these modules — and why no accessory is
cryptographically gated — is in [accessory-bus.md](accessory-bus.md).

Most of them are **plaintext, uncompressed, and unencrypted** — openable with no
key. That is useful on its own: the ISP and IOB images carry the complete message
catalogue for everything in the boot log, which is what lets you attribute a log
line to the exact processor that emitted it.

## The main coprocessor images

| image | what it is |
|---|---|
| `isp/iram.bin`, `isp/dram.bin` | ISP code + data. `dram.bin` is a pointer table plus the ISP's format strings |
| `iob0/iram.bin`, `iob0/dram.bin` | IOB0 (SATA / HDMI / SPI / C2C / XAUI-DMA / DDR test) |
| `iob1/iram.bin`, `iob1/dram.bin` | IOB1: the black-shading / bad-pixel calibration engine |
| `proio_iob0/` | PRO I/O (its data section is a zero-filled stub) |
| `lsbfpga/wlsbfpga.4.bin`, `.5.bin` | **Left Side Board FPGA** bitstreams (Xilinx **Kintex-7 XC7K325T**, ~5 MB zlib on disk / ~11 MiB config). The LSB is the camera's video-routing + I/O hub — XAUI links to ISP/IOB, monitor outputs (MON0/EXP1/SDI), colour LUTs, SSD detect, audio clocks. It is **configuration, not CPU code**, but the camera drives it through a well-defined register interface and can reconfigure it in-system. |
| `dsp/rdc_dsp_app.out` | TI **TMS320C64x+** DSP app (~1.2 MB): TI C6000 COFF (magic `0x00c2`), 56 sections, 7,951 symbols, `c6x/6.0.14` toolchain, **TI DSP/BIOS** kernel. The brain-module vision/audio DSP: Haar **face detection** (`FaceDetection/fd_algorithm`, TI VLIB pyramid), DoG **autofocus / video measurements** (`af_algorithm`), an on-DSP **audio server** (`RAudioServer`), an **imaging command server** (`CImagingServer`), and **sleep/power** coordination (`CSleepMgr`). IPC to the ARM over DSP/BIOS MSGQ/MPCS (DSPLink). Not disassembled (Ghidra has no C6x module) — role from COFF symbols + source paths |
| `redpine/redpine-boot.1.bin`, `redpine.1.bin` | WiFi/BT module firmware — **RSI RS9113** (Redpine), fw `1.0.10MM`; boot sends `RS9113.NBZ…rps` |

A `dram.bin` begins with a table of pointers into its own image — for the ISP, the
first word points exactly at the string `isp%d: component size overflow …`. That
confirms the load base (`0x40000000`) self-consistently and identifies the first
words as a `char*[]` message catalogue.

### ISA, fabric, and load map

All four cores run **Xtensa**, confirmed by disassembly: each `iram.bin` loads as
`Xtensa:LE:32:default` in Ghidra (12.1.4 ships an Xtensa module) and decodes cleanly,
with a textbook Xtensa reset vector (`wsr WindowBase` / `WindowStart`, the
windowed-register ABI, `wsr INTENABLE / CCOUNT / EXCSAVE1-7 / PS`, `l32r`, `jx`):

| core | instructions | functions |
|---|---|---|
| ISP | ~12,081 | 277 |
| IOB0 | ~15,068 | 290 |
| IOB1 | ~10,703 | 159 |
| proio IOB0 | ~2,650 | 167 |

A wrong ISA would decode to garbage, so this proves the `TENSILICA_*` config name is
an Xtensa core (not just a vendor label).

**Load map:** IRAM at `0x40000000`, DRAM at `0x3FFE0000` (IRAM − 0x20000) for the
ISP, IOB0 and IOB1 — each confirmed by resolution, not assumed (mapping `dram.bin`
at `0x3FFE0000` makes the code's references land on the data block's string starts;
ISP 2,421 refs, IOB0 79 refs/23 funcs, IOB1 91 refs/16 funcs). `proio_iob0` is the
exception: its `dram.bin` holds **no strings** (a register/parameter image), so no
string base can be pinned and no function anchoring is possible there.

With DRAM mapped, functions anchor to their roles by the strings they reference:
- **ISP** — XAUI DMA, DDR bring-up (`Modified SCL V2`, `check_window`), the R3D
  wavelet encoder (tile / component / metadata TX, CRC, `G1/B/R/G2` checks), DDR
  self-test.
- **IOB0** — SATA/HBA (`SATA device %s on HBA%d`, `PxSSTS`), ISP↔C2C transport
  (`C2C RX ERROR`, `iob iomap_write/read … isp_%d timeout`), SPI to the
  accelerometer (`STACCELERO`), XDMA, a DDR test.
- **IOB1** — sensor calibration: `analyze_slope`, `using old dark level`,
  factory/user **bad-pixel maps** (`pFactoryMap`, `pUserMap`), Bayer `green align
  odd rows`.

**Fabric (ASIC vs FPGA) is not determinable from the firmware.** Xtensa runs as a
hardened ASIC core and as an FPGA soft core, so the ISA does not decide it, and the
image uses both words for these subsystems — `ASIC` (`IOB ASIC Reg`, `Load ASIC bins
from host`, `ASIC Temperature`, `Monitor Asic(IOB)`) and `FPGA` (`FPGA_ISP1 / ISP2 /
IOB status`). The only files RED *names* as FPGA images are the LSB
(`wlsbfpga.*.bin`) and EVF (`wevf_fpga.1.bin`), not the ISP/IOB. Settling it would
take a physical method (decap / IR microscopy), not the firmware.

### Why the strings matter

The ISP image holds its DDR-training and XAUI bring-up messages
(`Modified SCL V2 routine passed for ISP%d!!`, `xaui status:0x%X`, …). This is the
*same* text that appears in a decoded boot log. Because the string only exists in
the ISP's own image, seeing it in the log proves the ISP itself ran that code —
not a brain-side guess. The IOB image likewise shows that `IOB Version` / `ISP
Version` reads are issued *by the IOB across the link*, so `ISP Version 0x0` in a
log is a statement about the ISP's ability to answer, not about the brain.

This is the backbone of fault localization on a sick body: a log string that
*should* be there and is missing tells you which processor never reached that code
path.

## Accessory / rear-device firmware

The rear devices, displays, side handles, lens mounts, FlatUI and EVF each carry
their own image. They share one container header (`01 | version | 00 00 00 | …`),
and **most are plaintext** (entropy 5.3–6.9) — readable with no key:

```
reardevices: hdk, hdkp, hdkpwv, jetpack, mmoDEMUX, mmotor, portx, rama, rcom,
             rssd, rvexp, rvxl, battery modules, ...
lcd:         gw3400 (7"), gw3400_4_7in (4.7")
lensmount:   canon, canon-s, nikon, pl, pl-s
sidehandle, flatui, evf (wevf_fpga)
```

The MCU family is identifiable from the images: **STM32** strings appear in
`jetpack`, `hdk`, `hdkp`, `hdkpwv`, `portx`, `rvxl`, `rvexp`, `vmexp` (e.g. a
`stm32>` prompt and `HW Version / SW Version`), and `rcomPSOC` is a **Cypress
PSoC**.

A few are **encrypted on the accessory side** (entropy ~8.0) — the motorised and
FPGA-loading images (`mtrx`, `mproxy`, `mtrtfpg`, `mwrles`) and the REDmote
(`redmote.1`, its own `REDMOTE` container). The brain does not decrypt these; it
streams them verbatim to the accessory, whose own bootloader does the decrypt. So
their keys are not in the camera and they cannot be opened from this corpus.

## How to use the plaintext images

- **Attribute a log line.** Grep a decoded log against each image's strings:
  `isp/` strings → ISP, `iob0/` → IOB, `iob1/` → calibration.
- **Identify a missing step.** A string that should appear and does not means the
  emitting processor never reached that code — the core inference for localizing a
  hardware fault.
- **Anchor a diff.** These strings are stable across the 7.x firmware series, so
  they work as reference points when comparing builds or bodies.

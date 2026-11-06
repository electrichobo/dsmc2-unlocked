# DSMC1 — The Earlier Generation

This repository's primary subject is the **DSMC2** platform. This folder is the
companion reference for the earlier **DSMC1** cameras, so an owner with a DSMC1
fleet can use one set of notes across both generations.

"DSMC1" is really **two different machines** under one marketing name, and they
share almost nothing internally. Knowing which one you hold decides everything —
firmware format, keys, processor, tooling.

| Branch | Bodies | Processor / OS | Firmware file | Page |
|---|---|---|---|---|
| **MX / Dragon** | EPIC (M-X), SCARLET (M-X), **EPIC DRAGON**, SCARLET Dragon | TI OMAP-class **ARM**, same family as DSMC2 | v2 `DSMC` package (`.bin`) | [mx-dragon.md](mx-dragon.md) |
| **RED ONE** | RED ONE, RED ONE MX | **PowerPC 405** + VxWorks + Xilinx IOFPGA | `redone.su` (or `su.tar` on old builds) | [red-one.md](red-one.md) |

The **MX/Dragon** branch is one generation behind DSMC2 and nearly identical to
it — same container, keys, processor, coprocessors, shell. Most of this repository
reads across to it directly. The **RED ONE** branch is older and unrelated — a
separate platform that happens to share the DSMC1 label. Both are now fully opened;
they just need different tools.

## Which body do I have?

| You see… | Branch | Firmware |
|---|---|---|
| EPIC, SCARLET, or DRAGON badge; takes a MINI-MAG or REDMAG 1.8; menu like a DSMC2 | **MX / Dragon** | v2 `.bin`, [fw-crypto](../../tools/fw-crypto) |
| RED ONE badge; CF / RED-DRIVE / RAM media; older boxy body | **RED ONE** | `redone.su`, [redone-su](../../tools/redone-su) |

Unsure from the outside? Check the firmware file the body accepts: a `DSMC`-magic
`.bin` is MX/Dragon; a `redone.su` (or `su.tar`) is RED ONE.

## Status

Both DSMC1 branches are on RED's no-longer-repairable list, which is the reason
their firmware is documented here for preservation. MX/Dragon is covered by the
whole repository (same platform as DSMC2); RED ONE is covered for firmware
unpacking and study. The custom-firmware work in [builds.md](../custom-fw/builds.md) targets
**DSMC2**; it is not built or verified for DSMC1 bodies — do not apply DSMC2 build
material to a DSMC1 camera.

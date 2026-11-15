# Subsystem Map — what each part allows

A per-subsystem summary of the DSMC2 brain: what it does, and whether its limits are a
**software gate** (changeable) or a **hardware limit** (not). Methods and addresses are
in the other docs and the private research notes; this is the outcome-level map.

| Subsystem | Software lever (changeable) | Hardware limit (fixed) |
|---|---|---|
| **Feature flags** (`HW_CAP_*`) | ProRes, IPP2, 3D LUT, anamorphic, extra gain modes — flip one bitmap; the licence check is a PIN compare, no signature. See [feature-unlock.md](feature-unlock.md). | A flag only enables what the hardware already supports. |
| **Record / codec** | REDCODE compression-ratio clamp is a software limit. ProRes offered via the flag above. | REDCODE wavelet runs on the ISP; ProRes proxy encode runs in the LSB FPGA fabric, 4K-capped by its fabric + single-DDR3L budget. |
| **Media / SSD** | Model-string + signature + IDENTIFY gates are all reproduced offline; `media-any` patch accepts any SATA SSD. See [media](../../media/). | A drive still needs the raw speed (~300 MB/s for R3D). |
| **Sensor** | Extra gain modes behind the sensitivity flag. | Resolution and exposure floor are the physical sensor. |
| **Frame rate** | — | Pixel-clock ÷ readout timing; no software FPS unlock. See [whats-possible.md](../whats-possible.md). |
| **Calibration** | Regenerate in-camera, back up, or transplant `.cal` (plaintext). See [calibration.md](calibration.md). | — |
| **Colour / LUT** | IPP2, 3D LUT behind flags; standard `.cube` LUT import. | ISO range physical. |
| **Monitor out** | Return/multi-feed behind flags. | HDMI brain port is 1080p-class; 4K only over SDI. |
| **Power** | Three power-save levels. Third-party batteries are **not** blocked (the "authorization" is logging only). | — |
| **Lens / FIZ** | Preston FIZ behind flags. | Needs the physical connector. |
| **Control plane (RCP)** | Fully mapped; a DIY serial/USB handle is feasible. | Latency-bound; not a throughput path. |
| **Wi-Fi** | Radio is not capability-gated. | Redpine host-offload module. |
| **Bootloader** | Boots a modified application image (no image signature check); partition table is extendable. | The signed environment block uses a key we do not hold — configuration goes through the unsigned config DB instead. |
| **Display panel** | Force the panel-ID to accept a replacement 7-inch LCD. See [hardware/display-repair](../../hardware/display-repair). | LVDS timing per panel. |

## The one-line summary

The software-gated layer — features, media, calibration, codec ratio, custom firmware —
is open. The hardware layer — sensor resolution, frame rate, encode/decode throughput,
the HDMI port class, and the signed environment block — is not something a firmware change
moves. Crossgrades only make sense inside one sensor family.

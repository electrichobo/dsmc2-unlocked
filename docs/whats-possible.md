# What Is Possible

This page states what the reverse-engineering enables — the outcomes. The methods,
keys, and tools behind them are in [docs/keys.md](firmware/keys.md),
[docs/serial-shell.md](access/serial-shell.md), [docs/log-decode.md](firmware/log-decode.md), the
[tools/](../tools) folder, and the [media](../media/) folder.

## Confirmed

- **The firmware is understood.** The packaging and the per-model structure are
  mapped. You can analyse and compare builds offline. You can produce modified
  firmware in a form the camera accepts.
- **Camera behaviour can be changed.** Features and limits that are gated in
  software can be unlocked. This works both as a lasting change and as a per-boot
  change.
  - The software-gated features sit behind one capability bitmap (`HW_CAP_*`). The
    check that loads them from a licence file is a plain PIN string-compare (no
    cryptographic signature), and the same bits can also be set per-boot from the
    debug shell or persistently from the unsigned config DB. The genuinely
    software-only features — ProRes, IPP2 colour science, 3D LUT, anamorphic
    de-squeeze — flip on this way on any DSMC2 body.
  - **ProRes is present on every DSMC2.** The encoder is compiled into all builds
    and runs on the IOB coprocessor; the only gate is the capability bit. It is
    **4K-capped** by IOB codec throughput — a hardware ceiling, not a flag — so the
    win is 4K ProRes on any body, not higher.
- **Media authentication is reverse-engineered.** The scheme is three checks:
  model-string whitelist, an MD5 SMART-log signature, and a ~29-field ATA IDENTIFY
  match. All three are reproduced offline against a genuine magazine. A drive
  mounts once it reports a whitelisted model string and a readable signature page.
  **Full data rate additionally requires passing the signature and the IDENTIFY
  match** — a retail drive typically fails the IDENTIFY capability words and caps
  at ~30 MB/s unless given the `media-any` camera patch. The end-to-end hardware
  path (adapter board) is not yet tested. Full detail and exact conditions are in
  the [media](../media/) folder.

## Bounded by hardware

- **Model identity is configuration.** You can change the camera-type value without
  swapping the binary. **Capability is not only configuration.** It is bounded by
  the sensor and by per-body hardware-capability flags.
- **Sensor resolution is fixed hardware.** A 5K body cannot become 8K. The lineup
  uses genuinely different sensors across models.
- **Frame rate and resolution are sensor-physical, not software gates.** Traced in
  firmware: max frame rate = sensor pixel clock (320/250 MHz) ÷ readout timing per
  format (`tg_getFormatMaximumFramerate`); the headline caps (e.g. 400/200/120) are
  rails *above* the real per-format limits, not a lock holding back a higher number.
  Likewise resolution is keyed off the physical 1-wire sensor-module part number, not
  a flag. So **no software FPS or resolution unlock exists** — a given body does what
  its sensor allows. Crossgrades only make sense inside one sensor family, and only
  for the genuinely software-gated layer (capability flags below).
- **HDMI monitor output tops out at 1080p.** The firmware's display-preset set
  offers only `1080P (HD)` and `720P (HD)` on the brain HDMI port; the only UHD
  display presets are `DISPLAY_PRESET_UHDSDI_*` and the rear `UHDSDI` channel —
  i.e. 4K monitoring exists **only over SDI**, never HDMI. The `BRAIN_HDMI` preset
  family has no UHD variant. This is not a software gate: the HDMI path runs
  through IOB0 and the OMAP display subsystem ([platform.md](platform.md)), so the
  port is a 1080p-class link. To get the camera's 4K picture onto an HDMI display,
  convert its 4K SDI externally — which is exactly what RED's own HDK board does
  (SDI→HDMI, [peripheral-firmware.md](hardware-fw/peripheral-firmware.md)).

## In development — to be announced

A **custom firmware** build is in progress ([builds.md](custom-fw/builds.md)). The platform is
fully understood — package format, bootloader, the whole application, the capability
system, media, calibration, and the code-injection method ([code-injection.md](custom-fw/code-injection.md))
are all mapped, so nothing unknown stands in the way. A custom image has now **booted and
run on a camera**, so the method is proven on hardware; what remains is finishing and
hardening the shippable build. Planned additions:

- **New anamorphic de-squeeze ratios** beyond the stock set.
- **In-camera IPP2 on original-DRAGON bodies** (e.g. SCARLET-W) — the colour
  pipeline gate is one capability bit, not a sensor lock; see [ipp2.md](features/ipp2.md).
- **Universal media** — any compatible SATA SSD usable as a mag.
- **Reliability fixes** — e.g. SATA write-retry ([reliability.md](features/reliability.md)).
- A **production** image (safe fixes only) and a **dev** image (engineering tools).

These will be **announced here when ready**. Until then, please do **not** attempt to
patch or flash your own camera from this material — the build is not finished or verified,
and an early-boot change applied wrongly can stop a body booting. The safe, recoverable
build and its procedure will be published when it ships.

## Where the details are

Everything above is backed by published material in this repository: the keys and
what they open ([keys.md](firmware/keys.md)), the debug-shell access path
([serial-shell.md](access/serial-shell.md)), the package format and decryptor
([firmware.md](firmware/firmware.md), [tools/fw-crypto](../tools/fw-crypto)), the log
decoder ([log-decode.md](firmware/log-decode.md)), the serial bridge
([hardware/serial-bridge](../hardware/serial-bridge)), and the full media path
([media](../media/)).

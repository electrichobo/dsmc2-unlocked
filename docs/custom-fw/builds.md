# Custom firmware — status and what is coming

A custom DSMC2 firmware **has booted and run on a camera** — the method is proven on
hardware. The *release* is still in development. This page states where it stands and what
it will do. It is a status page, **not a build-it-yourself guide** — see the safety note
at the bottom.

## Why it is possible

The application binary is identical across DSMC2 models (model identity is a camera-type
value plus two config-DB rows, [feature-unlock.md](../features/feature-unlock.md)), and the
bootloader does not verify the installed application image ([keys.md](../firmware/keys.md)). So a
modified firmware is the stock image plus a controlled set of changes, and it boots on
any body. The method behind the changes is documented in
[code-injection.md](code-injection.md).

## What the build will provide

Two flavours over the same base:

- **Production** — stock firmware plus only safe, repair-positive fixes (log-spam fix,
  the SATA write-retry hardening in [reliability.md](../features/reliability.md)). Behaviour the
  owner did not ask for is left alone; the engineering shell commands stay absent, as
  shipped. This is the image meant to actually run a camera.
- **Dev** — production plus engineering conveniences: the advanced shell commands
  reimplemented as clean C, optional feature unlocks, and extra diagnostics (for example
  a per-frame image-processor CRC log that pinpoints which coprocessor fails a frame — a
  real dead-body/marginal-link tool). A workbench image, not a shoot image.

## Status

The platform is fully mapped: the package format, the loader behaviour, the whole
application, the capability system, media, calibration, and the injection method are all
understood — there are no unknowns left in the way of the build. **On-camera verification
has now happened: a custom image booted and ran on a real body.** So the method is proven,
not theoretical. What remains is **finishing and hardening** — the tooling that makes a
flash safe and recoverable by design for an owner who is not the author. The build will be
announced here when it is ready.

## Please do not patch your own camera yet

This is the important part. **Do not attempt to patch or flash your own body from the
material in this repository.** That a custom image has booted on a camera does not make it
finished — a proven method is not a safe, recoverable release. An early-boot change applied
the wrong way can still leave a camera that will not start. The safe, recoverable build —
with the tooling that enforces that safety — is still in progress. When it is ready it will
be published here, with a clear procedure. Until then, treat these pages as *what is
understood*, not as instructions to run.

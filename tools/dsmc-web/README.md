# dsmc-web

WebAssembly bindings for the [DSMC Workbench](../../web). Each export is a thin
adapter over `dsmc-core` / `dsmc-fw-core`: bytes in, plain JS objects out. The
public FW package keys from [`fw-crypto`](../fw-crypto) are embedded.

- `src/files.rs`: logs, firmware packages, FPGA/REDONE images, presets.
- `src/camera.rs`: the `dsh>` session over a page-supplied `JsTransport`
  (WebSerial), driving `dsmc_core`'s async shell driver and capture.

Build with `pnpm wasm` from `web/` (see its README for the clang note).

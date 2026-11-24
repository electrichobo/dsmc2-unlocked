//! WebAssembly bindings for the DSMC2 web tool.
//!
//! Every function here is a thin adapter over `dsmc-core` / `dsmc-fw-core`:
//! bytes in, plain JS objects out. Offline file tools live in [`files`]; the
//! live camera session (over a WebSerial transport the page supplies) lives in
//! [`camera`].

use serde::Serialize;
use wasm_bindgen::prelude::*;

pub mod camera;
pub mod files;

#[wasm_bindgen(start)]
pub fn start() {
    console_error_panic_hook::set_once();
}

/// Serialize to a plain JS value (maps become objects, bytes Uint8Arrays).
pub(crate) fn to_js<T: Serialize + ?Sized>(v: &T) -> Result<JsValue, JsError> {
    v.serialize(&serde_wasm_bindgen::Serializer::new().serialize_maps_as_objects(true))
        .map_err(|e| JsError::new(&e.to_string()))
}

pub(crate) fn js_err(e: impl std::fmt::Display) -> JsError {
    JsError::new(&e.to_string())
}

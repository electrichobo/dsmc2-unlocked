//! Live camera session over a page-supplied transport (WebSerial).
//!
//! The page implements [`JsTransport`] on top of a `SerialPort`: a background
//! read loop buffers incoming bytes, `read()` drains that buffer without
//! waiting, `write()` sends, `clear()` drops the buffer, `sleep()` is a timer.
//! The shell logic itself is `dsmc_core`'s async driver, unchanged.

use std::cell::{Cell, RefCell};
use std::io;
use std::rc::Rc;
use std::time::Duration;

use dsmc_core::baseline::{self, Capture, Golden};
use dsmc_core::serial::AsyncTransport;
use dsmc_core::{capture, parse, AsyncShell};
use js_sys::{Function, Promise, Uint8Array};
use serde::Serialize;
use wasm_bindgen::prelude::*;
use wasm_bindgen_futures::future_to_promise;

use crate::{js_err, to_js};

/// The reference golden `dsmcdbg selftest` uses by default (a healthy
/// SCARLET-W 5K).
const DEFAULT_GOLDEN: &str = include_str!("../../dsmcdbg/golden_59c.json");

#[wasm_bindgen(typescript_custom_section)]
const TS_TRANSPORT: &str = r#"
export interface JsTransport {
  /** Send bytes. Copy `data` before awaiting: it is a view into wasm memory. */
  write(data: Uint8Array): Promise<void>;
  /** Whatever has arrived since the last call; empty if nothing. Never waits. */
  read(): Promise<Uint8Array>;
  /** Drop buffered input. */
  clear(): Promise<void>;
  sleep(ms: number): Promise<void>;
}
"#;

#[wasm_bindgen]
extern "C" {
    #[wasm_bindgen(typescript_type = "JsTransport")]
    pub type JsTransport;

    #[wasm_bindgen(method, catch)]
    async fn write(this: &JsTransport, data: &[u8]) -> Result<JsValue, JsValue>;
    #[wasm_bindgen(method, catch)]
    async fn read(this: &JsTransport) -> Result<JsValue, JsValue>;
    #[wasm_bindgen(method, catch)]
    async fn clear(this: &JsTransport) -> Result<JsValue, JsValue>;
    #[wasm_bindgen(method, catch)]
    async fn sleep(this: &JsTransport, ms: f64) -> Result<JsValue, JsValue>;

    #[wasm_bindgen(js_namespace = performance, js_name = now)]
    fn perf_now() -> f64;
}

struct WebTransport(JsTransport);

fn io_err(e: JsValue) -> io::Error {
    io::Error::other(e.as_string().unwrap_or_else(|| format!("{e:?}")))
}

impl AsyncTransport for WebTransport {
    async fn write_all(&mut self, data: &[u8]) -> io::Result<()> {
        self.0.write(data).await.map(|_| ()).map_err(io_err)
    }
    async fn read_available(&mut self) -> io::Result<Vec<u8>> {
        let v = self.0.read().await.map_err(io_err)?;
        Ok(Uint8Array::new(&v).to_vec())
    }
    async fn clear_input(&mut self) -> io::Result<()> {
        self.0.clear().await.map(|_| ()).map_err(io_err)
    }
    async fn sleep(&mut self, d: Duration) {
        let _ = self.0.sleep(d.as_secs_f64() * 1000.0).await;
    }
    fn now(&self) -> Duration {
        Duration::from_secs_f64(perf_now() / 1000.0)
    }
}

type Session = Rc<RefCell<AsyncShell<WebTransport>>>;

/// A connected `dsh>` session. Methods return promises; one runs at a time
/// (a second call while one is in flight rejects with "camera busy").
#[wasm_bindgen]
pub struct Camera {
    inner: Session,
    busy: Rc<Cell<bool>>,
}

fn ms(v: u32) -> Duration {
    Duration::from_millis(v.into())
}

/// Run `f` on the session, one call at a time. `busy` is claimed synchronously,
/// before the future is first polled, so two calls made in the same tick cannot
/// both get through; the session's RefCell borrow inside `f` is then never
/// contended even though it is held across awaits.
fn with_session<F, Fut>(cam: &Camera, f: F) -> Promise
where
    F: FnOnce(Session) -> Fut + 'static,
    Fut: std::future::Future<Output = Result<JsValue, JsError>> + 'static,
{
    if cam.busy.replace(true) {
        return Promise::reject(&JsError::new("camera busy: another command is still running").into());
    }
    let (s, busy) = (cam.inner.clone(), cam.busy.clone());
    future_to_promise(async move {
        let r = f(s).await.map_err(JsValue::from);
        busy.set(false);
        r
    })
}

fn progress_fn(cb: Option<Function>) -> impl FnMut(&str) {
    move |step: &str| {
        if let Some(cb) = &cb {
            let _ = cb.call1(&JsValue::NULL, &JsValue::from_str(step));
        }
    }
}

#[derive(Serialize)]
struct Selftest {
    capture: Capture,
    checks: Vec<baseline::SignalCheck>,
    faults: usize,
}

#[wasm_bindgen]
#[allow(clippy::await_holding_refcell_ref)] // serialized by `busy`, see with_session
impl Camera {
    /// Wrap a transport and wait for the `dsh>` prompt. Resolves to a Camera.
    pub fn open(transport: JsTransport, prompt_timeout_ms: u32) -> Promise {
        future_to_promise(async move {
            let sh = AsyncShell::open(WebTransport(transport), ms(prompt_timeout_ms))
                .await
                .map_err(js_err)?;
            Ok(Camera { inner: Rc::new(RefCell::new(sh)), busy: Rc::new(Cell::new(false)) }.into())
        })
    }

    /// Run one shell command. `wait_prompt` waits for the real prompt (slow
    /// tests) instead of the silence cutoff. Resolves to the raw reply.
    pub fn run(&self, cmd: String, timeout_ms: u32, wait_prompt: bool) -> Promise {
        with_session(self, move |s| async move {
            let mut sh = s.borrow_mut();
            let r = if wait_prompt {
                sh.run_wait(&cmd, ms(timeout_ms)).await
            } else {
                sh.run_t(&cmd, ms(timeout_ms)).await
            };
            r.map(|t| JsValue::from_str(&t)).map_err(js_err)
        })
    }

    /// Full per-board capture (the `dsmcdbg baseline --capture` signal set).
    pub fn capture(&self, deep: bool, date: String, on_progress: Option<Function>) -> Promise {
        with_session(self, move |s| async move {
            let mut sh = s.borrow_mut();
            let cap = capture::capture(&mut sh, "dsmc-web", "webserial", &date, deep, progress_fn(on_progress)).await;
            to_js(&cap)
        })
    }

    /// Capture, then assert against a golden (default: the embedded SCARLET-W
    /// reference) — `dsmcdbg selftest`.
    pub fn selftest(
        &self,
        golden_json: Option<String>,
        deep: bool,
        date: String,
        on_progress: Option<Function>,
    ) -> Promise {
        with_session(self, move |s| async move {
            let golden = Golden::from_json(golden_json.as_deref().unwrap_or(DEFAULT_GOLDEN)).map_err(js_err)?;
            let mut sh = s.borrow_mut();
            let cap = capture::capture(&mut sh, "dsmc-web", "webserial", &date, deep, progress_fn(on_progress)).await;
            let checks = baseline::assert_capture(&golden, &cap);
            let faults = baseline::fault_count(&checks);
            to_js(&Selftest { capture: cap, checks, faults })
        })
    }

    /// UCD power-sequencer STATUS summary.
    pub fn ucd(&self) -> Promise {
        with_session(self, |s| async move {
            let mut sh = s.borrow_mut();
            to_js(&capture::capture_ucd(&mut sh).await)
        })
    }

    /// `test i2c scan` on buses 1-3, labeled from the FW device map.
    pub fn i2c_scan(&self) -> Promise {
        with_session(self, |s| async move {
            let mut sh = s.borrow_mut();
            to_js(&capture::capture_i2c(&mut sh).await)
        })
    }
}

/// The embedded default golden (JSON text).
#[wasm_bindgen]
pub fn default_golden() -> String {
    DEFAULT_GOLDEN.to_string()
}

/// Compare two captures (JSON text) signal by signal.
#[wasm_bindgen]
pub fn diff_captures(reference_json: &str, measured_json: &str) -> Result<JsValue, JsError> {
    let a = Capture::from_json(reference_json).map_err(js_err)?;
    let b = Capture::from_json(measured_json).map_err(js_err)?;
    to_js(&baseline::diff_captures(&a, &b))
}

/// Assert a saved capture (JSON) against a golden (JSON; default if absent).
#[wasm_bindgen]
pub fn assert_capture(capture_json: &str, golden_json: Option<String>) -> Result<JsValue, JsError> {
    let cap = Capture::from_json(capture_json).map_err(js_err)?;
    let golden = Golden::from_json(golden_json.as_deref().unwrap_or(DEFAULT_GOLDEN)).map_err(js_err)?;
    let checks = baseline::assert_capture(&golden, &cap);
    let faults = baseline::fault_count(&checks);
    to_js(&Selftest { capture: cap, checks, faults })
}

#[wasm_bindgen]
pub fn parse_voltages(reply: &str) -> Result<JsValue, JsError> {
    to_js(&parse::parse_voltages(reply))
}

#[wasm_bindgen]
pub fn parse_temperatures(reply: &str) -> Result<JsValue, JsError> {
    to_js(&parse::parse_temperatures(reply))
}

#[wasm_bindgen]
pub fn parse_system_state(reply: &str) -> Result<JsValue, JsError> {
    to_js(&parse::parse_system_state(reply))
}

#[wasm_bindgen]
pub fn parse_lane_status(reply: &str) -> Result<JsValue, JsError> {
    to_js(&parse::parse_lane_status(reply))
}

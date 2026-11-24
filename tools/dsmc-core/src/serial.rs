//! Serial shell driver for the DSMC2 `dsh>` shell.
//!
//! The control logic (prompt waiting, the silence cutoff, command send/read) is
//! written once, async, against [`AsyncTransport`], which also owns the clock
//! and sleeping. That is what lets the same driver run natively and in a
//! browser (WebSerial, where reads are promises and `std::time::Instant` does
//! not exist).
//!
//! The synchronous [`Transport`] + [`DshDriver`] API is kept on top: a
//! [`Blocking`] adapter gives any sync transport the async interface (real
//! `Instant` + `thread::sleep`), and `DshDriver` drives it with
//! `pollster::block_on`. Tests use a canned in-memory sync transport.
//! [`SerialTransport`] (feature `serial`) is the real `serialport` backend.
//!
//! Behavior mirrors `tools/dsh.py`:
//!   * 115200 8N1, no flow control.
//!   * `wait_prompt`: poke CR every ~0.5s, wait for `dsh>` anywhere in the tail.
//!   * `run_cmd`: send `cmd\r`, read until prompt-at-end, else break after 0.6s
//!     of silence once some output arrived (3.0s if nothing arrived yet).
//!   * `run_cmd_prompt` (`-w`): read until prompt-at-end only, no silence cutoff.

use std::io;
use std::time::Duration;

use crate::reply::has_prompt;

/// Default port, matching `dsh.py`'s `PORT`.
pub const DEFAULT_PORT: &str = "/dev/cu.usbmodem101";
pub const BAUD: u32 = 115200;

/// A synchronous byte transport to the shell. The real one is a serial port;
/// tests use a scripted in-memory fake.
pub trait Transport {
    /// Write all bytes (and flush).
    fn write_all(&mut self, data: &[u8]) -> io::Result<()>;
    /// Non-blocking-ish read of whatever is available right now; returns the
    /// bytes read (may be empty). Must NOT block for long - the driver handles
    /// its own waiting and timing.
    fn read_available(&mut self) -> io::Result<Vec<u8>>;
    /// Discard any buffered input (mirror of pyserial reset_input_buffer).
    fn clear_input(&mut self) -> io::Result<()>;
}

/// The async transport the driver core runs on. Besides bytes it supplies the
/// clock and sleep, so the core never touches `std::time::Instant` or threads.
#[allow(async_fn_in_trait)]
pub trait AsyncTransport {
    async fn write_all(&mut self, data: &[u8]) -> io::Result<()>;
    /// Whatever is available now, or after a short wait; may be empty.
    async fn read_available(&mut self) -> io::Result<Vec<u8>>;
    async fn clear_input(&mut self) -> io::Result<()>;
    async fn sleep(&mut self, d: Duration);
    /// Monotonic time since an arbitrary origin.
    fn now(&self) -> Duration;
}

/// Timing knobs, pulled out so tests can zero them for speed.
#[derive(Debug, Clone, Copy)]
pub struct Timing {
    pub poll: Duration,
    pub quiet_cutoff: Duration,     // 0.6s in dsh.py run_cmd
    pub no_output_cutoff: Duration, // 3.0s in dsh.py run_cmd
    pub settle: Duration,           // 0.05s
    pub poke_every: Duration,       // 0.5s in wait_prompt
}

impl Default for Timing {
    fn default() -> Self {
        Timing {
            poll: Duration::from_millis(10),
            quiet_cutoff: Duration::from_millis(600),
            no_output_cutoff: Duration::from_millis(3000),
            settle: Duration::from_millis(50),
            poke_every: Duration::from_millis(500),
        }
    }
}

impl Timing {
    /// Zeroed timing for tests - no real waiting.
    pub fn instant() -> Self {
        Timing {
            poll: Duration::from_millis(0),
            quiet_cutoff: Duration::from_millis(0),
            no_output_cutoff: Duration::from_millis(0),
            settle: Duration::from_millis(0),
            poke_every: Duration::from_millis(0),
        }
    }
}

async fn sleep_nonzero<T: AsyncTransport>(t: &mut T, d: Duration) {
    if !d.is_zero() {
        t.sleep(d).await;
    }
}

/// Wait for the `dsh>` prompt, poking CR periodically so an already-printed
/// silent prompt answers. `timeout` caps the wait. Returns true if seen.
pub async fn wait_prompt<T: AsyncTransport>(
    t: &mut T,
    timing: &Timing,
    timeout: Duration,
) -> io::Result<bool> {
    let t0 = t.now();
    let mut last_poke: Option<Duration> = None;
    let mut buf = String::new();
    while t.now() - t0 < timeout {
        let chunk = t.read_available().await?;
        if !chunk.is_empty() {
            buf.push_str(&String::from_utf8_lossy(&chunk));
            if has_prompt(&buf, false) {
                return Ok(true);
            }
        } else {
            sleep_nonzero(t, timing.poll).await;
            if last_poke.is_none_or(|p| t.now() - p > timing.poke_every) {
                t.write_all(b"\r").await?;
                last_poke = Some(t.now());
            }
        }
    }
    Ok(false)
}

/// Send `cmd` and read the reply using the silence-cutoff heuristic
/// (equivalent to `dsh.py`'s `run_cmd`).
pub async fn run_cmd<T: AsyncTransport>(
    t: &mut T,
    timing: &Timing,
    cmd: &str,
    timeout: Duration,
) -> io::Result<String> {
    send(t, timing, cmd).await?;
    let t0 = t.now();
    let mut last = t.now();
    let mut buf = String::new();
    while t.now() - t0 < timeout {
        let chunk = t.read_available().await?;
        if !chunk.is_empty() {
            buf.push_str(&String::from_utf8_lossy(&chunk));
            last = t.now();
            if has_prompt(&buf, true) {
                break;
            }
            continue;
        }
        // Judge silence only right after a read came back empty. A sleep can
        // overrun badly (a background browser tab clamps timers to ~1 s); data
        // that arrived during it must be read before the line counts as quiet.
        let quiet = t.now() - last;
        if !buf.is_empty() && quiet > timing.quiet_cutoff {
            break;
        }
        if buf.is_empty() && quiet > timing.no_output_cutoff {
            break;
        }
        sleep_nonzero(t, timing.poll).await;
    }
    Ok(buf)
}

/// Send `cmd` and read until the prompt only - no silence cutoff
/// (equivalent to `dsh.py`'s `run_cmd_prompt`, the `-w` mode).
pub async fn run_cmd_prompt<T: AsyncTransport>(
    t: &mut T,
    timing: &Timing,
    cmd: &str,
    timeout: Duration,
) -> io::Result<String> {
    send(t, timing, cmd).await?;
    let t0 = t.now();
    let mut buf = String::new();
    while t.now() - t0 < timeout {
        let chunk = t.read_available().await?;
        if !chunk.is_empty() {
            buf.push_str(&String::from_utf8_lossy(&chunk));
            if has_prompt(&buf, true) {
                break;
            }
        } else {
            sleep_nonzero(t, timing.poll).await;
        }
    }
    Ok(buf)
}

async fn send<T: AsyncTransport>(t: &mut T, timing: &Timing, cmd: &str) -> io::Result<()> {
    t.clear_input().await?;
    sleep_nonzero(t, timing.settle).await;
    let mut line = cmd.as_bytes().to_vec();
    line.push(b'\r');
    t.write_all(&line).await
}

/// Gives a sync [`Transport`] the [`AsyncTransport`] interface, using the real
/// clock and blocking sleeps. Its futures never pend, so `pollster::block_on`
/// drives them.
pub struct Blocking<T> {
    pub inner: T,
    origin: std::time::Instant,
}

impl<T> Blocking<T> {
    pub fn new(inner: T) -> Self {
        Blocking {
            inner,
            origin: std::time::Instant::now(),
        }
    }
}

impl<T: Transport> AsyncTransport for Blocking<T> {
    async fn write_all(&mut self, data: &[u8]) -> io::Result<()> {
        self.inner.write_all(data)
    }
    async fn read_available(&mut self) -> io::Result<Vec<u8>> {
        self.inner.read_available()
    }
    async fn clear_input(&mut self) -> io::Result<()> {
        self.inner.clear_input()
    }
    async fn sleep(&mut self, d: Duration) {
        std::thread::sleep(d);
    }
    fn now(&self) -> Duration {
        self.origin.elapsed()
    }
}

/// Synchronous driver over any [`Transport`].
pub struct DshDriver<T: Transport> {
    pub transport: T,
    pub timing: Timing,
}

impl<T: Transport> DshDriver<T> {
    pub fn new(transport: T) -> Self {
        DshDriver {
            transport,
            timing: Timing::default(),
        }
    }

    pub fn with_timing(transport: T, timing: Timing) -> Self {
        DshDriver { transport, timing }
    }

    fn with_async<R>(&mut self, f: impl AsyncFnOnce(&mut Blocking<&mut T>, &Timing) -> R) -> R {
        let timing = self.timing;
        let mut b = Blocking::new(&mut self.transport);
        pollster::block_on(f(&mut b, &timing))
    }

    /// See [`wait_prompt`].
    pub fn wait_prompt(&mut self, timeout: Duration) -> io::Result<bool> {
        self.with_async(async |t, timing| wait_prompt(t, timing, timeout).await)
    }

    /// See [`run_cmd`].
    pub fn run_cmd(&mut self, cmd: &str, timeout: Duration) -> io::Result<String> {
        self.with_async(async |t, timing| run_cmd(t, timing, cmd, timeout).await)
    }

    /// See [`run_cmd_prompt`].
    pub fn run_cmd_prompt(&mut self, cmd: &str, timeout: Duration) -> io::Result<String> {
        self.with_async(async |t, timing| run_cmd_prompt(t, timing, cmd, timeout).await)
    }
}

impl<T: Transport> Transport for &mut T {
    fn write_all(&mut self, data: &[u8]) -> io::Result<()> {
        (**self).write_all(data)
    }
    fn read_available(&mut self) -> io::Result<Vec<u8>> {
        (**self).read_available()
    }
    fn clear_input(&mut self) -> io::Result<()> {
        (**self).clear_input()
    }
}

/// Real serial-port transport, backed by the `serialport` crate.
#[cfg(feature = "serial")]
pub struct SerialTransport {
    port: Box<dyn serialport::SerialPort>,
}

#[cfg(feature = "serial")]
impl SerialTransport {
    /// Open `port` at 115200 8N1, no flow control, with a short read timeout so
    /// `read_available` returns promptly when the line is quiet.
    pub fn open(port: &str) -> io::Result<Self> {
        let p = serialport::new(port, BAUD)
            .data_bits(serialport::DataBits::Eight)
            .parity(serialport::Parity::None)
            .stop_bits(serialport::StopBits::One)
            .flow_control(serialport::FlowControl::None)
            .timeout(Duration::from_millis(50))
            .open()
            .map_err(|e| io::Error::other(format!("open {port}: {e}")))?;
        Ok(SerialTransport { port: p })
    }
}

#[cfg(feature = "serial")]
impl Transport for SerialTransport {
    fn write_all(&mut self, data: &[u8]) -> io::Result<()> {
        use std::io::Write;
        Write::write_all(&mut self.port, data)?;
        self.port.flush()
    }

    fn read_available(&mut self) -> io::Result<Vec<u8>> {
        use std::io::Read;
        let n = self.port.bytes_to_read().unwrap_or(0) as usize;
        if n == 0 {
            return Ok(Vec::new());
        }
        let mut buf = vec![0u8; n];
        match self.port.read(&mut buf) {
            Ok(got) => {
                buf.truncate(got);
                Ok(buf)
            }
            Err(ref e) if e.kind() == io::ErrorKind::TimedOut => Ok(Vec::new()),
            Err(e) => Err(e),
        }
    }

    fn clear_input(&mut self) -> io::Result<()> {
        self.port
            .clear(serialport::ClearBuffer::Input)
            .map_err(io::Error::other)
    }
}

#[cfg(test)]
pub mod mock {
    use super::*;
    use std::collections::VecDeque;

    /// Scripted transport: each queued step is the bytes the port will "produce"
    /// on the next read (empty = quiet poll). Writes are recorded.
    pub struct MockTransport {
        pub reads: VecDeque<Vec<u8>>,
        pub writes: Vec<Vec<u8>>,
    }

    impl MockTransport {
        pub fn new(reads: Vec<&str>) -> Self {
            MockTransport {
                reads: reads.into_iter().map(|s| s.as_bytes().to_vec()).collect(),
                writes: Vec::new(),
            }
        }
    }

    impl Transport for MockTransport {
        fn write_all(&mut self, data: &[u8]) -> io::Result<()> {
            self.writes.push(data.to_vec());
            Ok(())
        }
        fn read_available(&mut self) -> io::Result<Vec<u8>> {
            Ok(self.reads.pop_front().unwrap_or_default())
        }
        fn clear_input(&mut self) -> io::Result<()> {
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::mock::MockTransport;
    use super::*;

    #[test]
    fn wait_prompt_sees_prompt() {
        let t = MockTransport::new(vec!["(72%/13%) dsh>"]);
        let mut d = DshDriver::with_timing(t, Timing::instant());
        assert!(d.wait_prompt(Duration::from_secs(1)).unwrap());
    }

    #[test]
    fn wait_prompt_pokes_cr_then_sees_prompt() {
        // first read empty (triggers a CR poke), then the prompt
        let t = MockTransport::new(vec!["", "(72%/13%) dsh>"]);
        let mut d = DshDriver::with_timing(t, Timing::instant());
        assert!(d.wait_prompt(Duration::from_secs(1)).unwrap());
        assert!(d.transport.writes.iter().any(|w| w == b"\r"));
    }

    #[test]
    fn wait_prompt_times_out() {
        let t = MockTransport::new(vec!["still booting"]);
        let mut d = DshDriver::with_timing(t, Timing::instant());
        // zero timeout -> loop body never runs -> false
        assert!(!d.wait_prompt(Duration::from_millis(0)).unwrap());
    }

    #[test]
    fn run_cmd_reads_until_prompt_at_end() {
        let t = MockTransport::new(vec![
            "Register Address 815811F4: 00000123\n",
            "(72%/13%) dsh>",
        ]);
        let mut d = DshDriver::with_timing(t, Timing::instant());
        let out = d
            .run_cmd("read reg 0x815811f4", Duration::from_secs(5))
            .unwrap();
        assert!(out.contains("00000123"));
        assert!(out.trim_end().ends_with("dsh>"));
        // the command was sent with a trailing CR
        assert_eq!(d.transport.writes[0], b"read reg 0x815811f4\r");
    }

    #[test]
    fn run_cmd_breaks_on_silence_after_output() {
        // output arrives, then endless quiet (no prompt) -> silence cutoff ends it
        let t = MockTransport::new(vec!["some output no prompt\n", "", "", ""]);
        let mut d = DshDriver::with_timing(t, Timing::instant());
        let out = d.run_cmd("x", Duration::from_secs(5)).unwrap();
        assert!(out.contains("some output no prompt"));
    }

    #[test]
    fn run_cmd_prompt_waits_for_prompt_only() {
        let t = MockTransport::new(vec!["working...\n", "", "", "done\n(0%/0%) dsh>"]);
        let mut d = DshDriver::with_timing(t, Timing::instant());
        let out = d
            .run_cmd_prompt("nand zlib", Duration::from_secs(5))
            .unwrap();
        assert!(out.contains("done"));
        assert!(out.trim_end().ends_with("dsh>"));
    }

    /// Sleeps overrun to 1 s (a background browser tab clamps timers) while the
    /// rest of the reply arrives 5 ms after the first chunk.
    struct ThrottledTab {
        t: Duration,
        chunks: Vec<(Duration, &'static str)>,
    }

    impl AsyncTransport for ThrottledTab {
        async fn write_all(&mut self, _: &[u8]) -> io::Result<()> {
            Ok(())
        }
        async fn read_available(&mut self) -> io::Result<Vec<u8>> {
            let now = self.t;
            let (ready, later): (Vec<_>, Vec<_>) = self.chunks.drain(..).partition(|(at, _)| *at <= now);
            self.chunks = later;
            Ok(ready.into_iter().flat_map(|(_, c)| c.bytes()).collect())
        }
        async fn clear_input(&mut self) -> io::Result<()> {
            Ok(())
        }
        async fn sleep(&mut self, _: Duration) {
            self.t += Duration::from_secs(1);
        }
        fn now(&self) -> Duration {
            self.t
        }
    }

    #[test]
    fn run_cmd_reads_data_that_arrived_during_an_overlong_sleep() {
        let mut t = ThrottledTab {
            t: Duration::ZERO,
            chunks: vec![
                (Duration::ZERO, "Checking - IOB\n"),
                (Duration::from_millis(5), "  Vcc  1.0  = 1.006\nDone\n(97%/97%) dsh>"),
            ],
        };
        let out = pollster::block_on(run_cmd(&mut t, &Timing::default(), "test voltage all", Duration::from_secs(15))).unwrap();
        assert!(out.contains("1.006"), "reply cut off: {out:?}");
        assert!(out.trim_end().ends_with("dsh>"));
    }
}

//! High-level shell runner: wait for the `dsh>` prompt, run one command and
//! capture its reply. [`AsyncShell`] works over any [`AsyncTransport`] (native
//! or WebSerial); [`Shell`] (feature `serial`) is the blocking front-end that
//! opens a real serial port, so every CLI subcommand has the same one-liner.

use std::time::Duration;

use crate::serial::{self, AsyncTransport, Timing};

const DEFAULT_TIMEOUT: Duration = Duration::from_secs(12);
const NO_PROMPT: &str =
    "no dsh> prompt — camera off, shutoff window, or shell not enabled (reopen and retry)";

/// A connected camera shell session over any async transport.
pub struct AsyncShell<T: AsyncTransport> {
    pub transport: T,
    pub timing: Timing,
    pub(crate) default_timeout: Duration,
}

impl<T: AsyncTransport> AsyncShell<T> {
    /// Wrap `transport` and wait up to `prompt_timeout` for the `dsh>` prompt.
    pub async fn open(transport: T, prompt_timeout: Duration) -> Result<Self, String> {
        let mut sh = AsyncShell {
            transport,
            timing: Timing::default(),
            default_timeout: DEFAULT_TIMEOUT,
        };
        match serial::wait_prompt(&mut sh.transport, &sh.timing, prompt_timeout).await {
            Ok(true) => Ok(sh),
            Ok(false) => Err(NO_PROMPT.into()),
            Err(e) => Err(format!("serial error waiting for prompt: {e}")),
        }
    }

    /// Set the default per-command timeout.
    pub fn with_timeout(mut self, d: Duration) -> Self {
        self.default_timeout = d;
        self
    }

    /// Run one command, silence-cutoff heuristic (good for most reads).
    pub async fn run(&mut self, cmd: &str) -> Result<String, String> {
        let to = self.default_timeout;
        self.run_t(cmd, to).await
    }

    /// Run one command with an explicit timeout.
    pub async fn run_t(&mut self, cmd: &str, timeout: Duration) -> Result<String, String> {
        serial::run_cmd(&mut self.transport, &self.timing, cmd, timeout)
            .await
            .map_err(|e| format!("run {cmd:?}: {e}"))
    }

    /// Run one command, waiting for the real prompt (for slow/bursty tests like
    /// `test ram`, `test x12`), no silence cutoff.
    pub async fn run_wait(&mut self, cmd: &str, timeout: Duration) -> Result<String, String> {
        serial::run_cmd_prompt(&mut self.transport, &self.timing, cmd, timeout)
            .await
            .map_err(|e| format!("run {cmd:?}: {e}"))
    }

    /// Read one register via `read reg 0xADDR`, returning the u32 value.
    pub async fn read_reg(&mut self, addr: u32) -> Result<Option<u32>, String> {
        let reply = self.run(&format!("read reg 0x{addr:08x}")).await?;
        Ok(crate::reply::extract_read_reg(&reply, Some(addr)))
    }

    /// Read `len` bytes from an I2C device: `read i2c BUS ADDR REG LEN`.
    pub async fn read_i2c(
        &mut self,
        bus: u8,
        addr: u8,
        reg: u8,
        len: u8,
    ) -> Result<Option<Vec<u8>>, String> {
        let reply = self
            .run(&format!("read i2c {bus} 0x{addr:02x} 0x{reg:02x} {len}"))
            .await?;
        Ok(crate::parse::parse_i2c_read(&reply))
    }
}

#[cfg(feature = "serial")]
pub use blocking::Shell;

#[cfg(feature = "serial")]
mod blocking {
    use super::*;
    use crate::serial::{Blocking, SerialTransport};

    /// A connected camera shell session on a real serial port (blocking).
    pub struct Shell {
        inner: AsyncShell<Blocking<SerialTransport>>,
    }

    impl Shell {
        /// Open `port` and wait up to `prompt_timeout` for the `dsh>` prompt.
        pub fn open(port: &str, prompt_timeout: Duration) -> Result<Self, String> {
            let t = SerialTransport::open(port).map_err(|e| format!("open {port}: {e}"))?;
            let inner = pollster::block_on(AsyncShell::open(Blocking::new(t), prompt_timeout))?;
            Ok(Shell { inner })
        }

        /// The async session underneath, for the shared async flows
        /// ([`crate::capture`]); drive them with [`Shell::block_on`].
        pub fn as_async(&mut self) -> &mut AsyncShell<Blocking<SerialTransport>> {
            &mut self.inner
        }

        /// Run a future to completion. The blocking transport never pends.
        pub fn block_on<F: std::future::Future>(f: F) -> F::Output {
            pollster::block_on(f)
        }

        /// Set the default per-command timeout.
        pub fn with_timeout(mut self, d: Duration) -> Self {
            self.inner = self.inner.with_timeout(d);
            self
        }

        pub fn run(&mut self, cmd: &str) -> Result<String, String> {
            pollster::block_on(self.inner.run(cmd))
        }

        pub fn run_t(&mut self, cmd: &str, timeout: Duration) -> Result<String, String> {
            pollster::block_on(self.inner.run_t(cmd, timeout))
        }

        pub fn run_wait(&mut self, cmd: &str, timeout: Duration) -> Result<String, String> {
            pollster::block_on(self.inner.run_wait(cmd, timeout))
        }

        pub fn read_reg(&mut self, addr: u32) -> Result<Option<u32>, String> {
            pollster::block_on(self.inner.read_reg(addr))
        }

        pub fn read_i2c(
            &mut self,
            bus: u8,
            addr: u8,
            reg: u8,
            len: u8,
        ) -> Result<Option<Vec<u8>>, String> {
            pollster::block_on(self.inner.read_i2c(bus, addr, reg, len))
        }
    }
}

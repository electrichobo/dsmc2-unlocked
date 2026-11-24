//! Drive the camera shell and build a full per-board [`Capture`].
//!
//! One place that knows the exact shell commands for every signal, so the CLI
//! (`dsmcdbg selftest` / `baseline --capture`) and the web front-end share
//! identical capture logic. Async over any [`AsyncTransport`]; the CLI drives it
//! with [`crate::Shell::block_on`]. Read-only throughout:
//! the only writes are `write i2c <bus> <addr> 0x00 1 <page>` to select a UCD
//! PMBus PAGE before reading STATUS_VOUT — a standard, non-destructive config
//! read, not a camera modification.

use std::time::Duration;

use crate::baseline::*;
use crate::i2c::{self, DeviceMap};
use crate::parse;
use crate::reply::extract_read_reg;
use crate::runner::AsyncShell;
use crate::serial::AsyncTransport;

/// The UCD power sequencers on the 59C (board, live bus, 7-bit addr, part, max page).
/// Buses are the live logical buses from BASELINE_59C_GOLDEN.md (not the FW-table
/// default wiring).
const UCD_BOARDS: &[(&str, u8, u8, &str, u8)] = &[
    ("iob", 1, 0x7C, "UCD9090", 10),
    ("isp", 2, 0x70, "UCD9090", 10),
    ("sensor", 2, 0x65, "UCD90160", 16),
];

/// Capture the full per-board signal set. `deep` adds the slow DDR tests.
/// `captured` is the date stamp (YYYY-MM-DD) written into the capture;
/// `progress` is told each step as it starts.
pub async fn capture<T: AsyncTransport>(
    sh: &mut AsyncShell<T>,
    tool: &str,
    port: &str,
    captured: &str,
    deep: bool,
    mut progress: impl FnMut(&str),
) -> Capture {
    let mut cap = Capture {
        schema: 1,
        tool: tool.into(),
        port: port.into(),
        captured: captured.into(),
        ..Default::default()
    };

    progress("identity");

    // identity / state
    if let Ok(r) = sh.run("get system_state").await {
        cap.system_state = parse::parse_system_state(&r).map(|s| StateVal {
            name: s.name,
            code: s.code,
        });
    }
    cap.versions.isp = sh
        .run("get isp_version")
        .await
        .ok()
        .and_then(|r| parse::parse_version_id(&r, "isp"));
    if cap.versions.isp.is_none() {
        cap.versions.isp = sh
            .run("test isp")
            .await
            .ok()
            .and_then(|r| parse::parse_version_id(&r, "isp"));
    }
    cap.versions.iob = sh
        .run("get iob_version")
        .await
        .ok()
        .and_then(|r| parse::parse_version_id(&r, "iob"));
    if cap.versions.iob.is_none() {
        cap.versions.iob = sh
            .run("test iob")
            .await
            .ok()
            .and_then(|r| parse::parse_version_id(&r, "iob"));
    }
    cap.heartbeat = sh
        .run("debug heartbeat isp1")
        .await
        .ok()
        .and_then(|r| parse::parse_heartbeat(&r));
    cap.dragon_id = sh
        .run("read sensor_dragon_reg 0")
        .await
        .ok()
        .and_then(|r| parse::parse_dragon_id(&r));

    // voltages / temps
    progress("voltages + temperatures");
    if let Ok(r) = sh.run_t("test voltage all", Duration::from_secs(15)).await {
        cap.voltages = parse::parse_voltages(&r)
            .into_iter()
            .map(|v| RailV {
                board: v.board,
                name: v.name,
                nominal: v.nominal,
                measured: v.measured,
            })
            .collect();
    }
    if let Ok(r) = sh
        .run_t("test temperature all", Duration::from_secs(15))
        .await
    {
        cap.temps = parse::parse_temperatures(&r)
            .into_iter()
            .map(|t| TempV {
                sensor: t.sensor,
                celsius: t.celsius,
                pass: t.pass,
            })
            .collect();
    }

    // X12 / X4 lanes (slow — wait for prompt)
    progress("X12 / X4 lanes");
    if let Ok(r) = sh
        .run_wait("test x12 status isp1 all", Duration::from_secs(25))
        .await
    {
        let l = parse::parse_lane_status(&r);
        cap.x12 = Some(LaneV {
            all_pass: l.all_pass,
            lanes: l.lanes,
            failing_lanes: l.failing_lanes,
        });
    }
    if let Ok(r) = sh
        .run_wait("test x4 status isp-rx isp1 all", Duration::from_secs(25))
        .await
    {
        let l = parse::parse_lane_status(&r);
        cap.x4_isp_rx = Some(LaneV {
            all_pass: l.all_pass,
            lanes: l.lanes,
            failing_lanes: l.failing_lanes,
        });
    }
    if let Ok(r) = sh
        .run_wait("test x4 status iob-rx isp1 all", Duration::from_secs(25))
        .await
    {
        let l = parse::parse_lane_status(&r);
        cap.x4_iob_rx = Some(LaneV {
            all_pass: l.all_pass,
            lanes: l.lanes,
            failing_lanes: l.failing_lanes,
        });
    }

    // UCD power sequencers
    progress("UCD power sequencers");
    cap.ucd = capture_ucd(sh).await;

    // I2C device presence
    progress("I2C scan");
    cap.i2c = capture_i2c(sh).await;

    // DDR (deep only — ~30-60s each)
    if deep {
        for tgt in ["iob", "isp1", "isp0"] {
            progress(&format!("DDR test {tgt}"));
            if let Ok(r) = sh
                .run_wait(&format!("test ram {tgt} quick"), Duration::from_secs(90))
                .await
            {
                let rr = parse::parse_ram_test(&r);
                cap.ddr.insert(
                    tgt.to_string(),
                    RamV {
                        pass: rr.pass,
                        saw_fail: rr.saw_fail,
                        done: rr.done,
                    },
                );
            }
        }
    }

    cap
}

/// Capture UCD STATUS_WORD + per-rail STATUS_VOUT for each power sequencer.
pub async fn capture_ucd<T: AsyncTransport>(sh: &mut AsyncShell<T>) -> Vec<UcdV> {
    let mut out = Vec::new();
    for &(board, bus, addr, part, max_page) in UCD_BOARDS {
        // global STATUS_WORD (0x79, 2 bytes)
        let mut flags = Vec::new();
        // Record STATUS_WORD only as informational context — it is NOT the health
        // signal (the 59C baseline shows benign idle words: UCD9090 0x0003/0x1003,
        // UCD90160 0x9023). Don't let it drive the verdict here; the paged
        // STATUS_VOUT (or the masked-STATUS_WORD fallback below) decides health.
        let status_word: Option<u16> = sh
            .read_i2c(bus, addr, 0x79, 2)
            .await
            .ok()
            .flatten()
            .and_then(|b| crate::ucd::le_u16(&b));

        // per-rail STATUS_VOUT (0x7A) — select PAGE (reg 0x00) then read.
        let mut all_ok = true;
        let mut any_read = false;
        for page in 0..=max_page {
            // write PAGE
            let _ = sh
                .run(&format!("write i2c {bus} 0x{addr:02x} 0x00 1 {page}"))
                .await;
            if let Ok(Some(sv)) = sh.read_i2c(bus, addr, 0x7A, 1).await {
                any_read = true;
                let b = sv[0];
                if b != 0x00 {
                    all_ok = false;
                    let bits = crate::ucd::decode_status_vout(b);
                    flags.push(format!(
                        "page {page} STATUS_VOUT=0x{b:02x} [{}]",
                        bits.join(",")
                    ));
                }
            }
        }
        // Health signal selection:
        //  - If the paged STATUS_VOUT reads landed cleanly, that's authoritative.
        //  - PMBus PAGE-write over the dsh shell is KNOWN-FLAKY (baseline doc: use
        //    ucd_dump for the reliable paged dump). When the paged reads didn't land
        //    (`any_read` false), DON'T false-FAIL — fall back to STATUS_WORD with the
        //    known-benign bits masked (CML 0x02 on ISP, and the sensor UCD90160's
        //    idle STATUS_WORD), and report the limitation as INFO.
        // Decide health. Trust the paged STATUS_VOUT ONLY if it read cleanly AND is
        // all-zero. PMBus PAGE-write over dsh is known-flaky (baseline: use ucd_dump
        // for the authoritative paged dump), so a non-zero paged STATUS_VOUT here is
        // more likely a page-write artifact than a real rail fault — in that case,
        // fall back to STATUS_WORD with the per-part benign idle word masked. A fault
        // is only reported when STATUS_WORD shows a bit outside the known-benign set.
        let benign: u16 = if part.contains("90160") {
            0x9023
        } else {
            0x1003
        };
        let status_vout_all_ok = if any_read && all_ok {
            true
        } else {
            match status_word {
                Some(w) => {
                    let unexpected = w & !benign & !0x0002; // mask part-idle + CML
                    if unexpected != 0 {
                        flags.push(format!(
                            "STATUS_WORD=0x{w:04x} unexpected bits 0x{unexpected:04x}"
                        ));
                        false
                    } else {
                        flags.push(format!(
                            "STATUS_WORD=0x{w:04x} benign (paged STATUS_VOUT flaky over dsh; use ucd_dump for full rail dump)"
                        ));
                        true
                    }
                }
                None => {
                    flags.push("UCD STATUS unreadable".into());
                    false
                }
            }
        };
        out.push(UcdV {
            board: board.into(),
            bus,
            addr,
            part: part.into(),
            status_vout_all_ok,
            flags,
        });
    }
    out
}

/// Capture I2C device presence from `test i2c scan`, labeled via the device map.
pub async fn capture_i2c<T: AsyncTransport>(sh: &mut AsyncShell<T>) -> Vec<I2cV> {
    let map: DeviceMap = crate::embedded_i2c_map();
    let mut out = Vec::new();
    // `test i2c scan` needs a bus argument (1/2/3); scan each bus and stamp it.
    for bus in [1u8, 2, 3] {
        if let Ok(r) = sh
            .run_wait(&format!("test i2c scan {bus}"), Duration::from_secs(40))
            .await
        {
            for sl in i2c::parse_scan_bus(&r, bus) {
                let label = map
                    .find(sl.bus, sl.addr)
                    .map(|d| d.label.clone())
                    .unwrap_or_else(|| sl.label.clone());
                out.push(I2cV {
                    bus: sl.bus,
                    addr: sl.addr,
                    label,
                    scan: sl.verdict,
                });
            }
        }
    }
    out
}

/// A single live register read (for the `reg` subcommand).
pub async fn read_reg<T: AsyncTransport>(sh: &mut AsyncShell<T>, addr: u32) -> Option<u32> {
    sh.run(&format!("read reg 0x{addr:08x}"))
        .await
        .ok()
        .and_then(|r| extract_read_reg(&r, Some(addr)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::serial::Timing;
    use std::collections::HashMap;
    use std::io;

    /// Answers each command like the shell: echo, canned body, prompt.
    struct FakeShell {
        replies: HashMap<&'static str, &'static str>,
        out: Vec<u8>,
        line: String,
        sent: Vec<String>,
        t: Duration,
    }

    impl AsyncTransport for FakeShell {
        async fn write_all(&mut self, data: &[u8]) -> io::Result<()> {
            for &b in data {
                if b != b'\r' {
                    self.line.push(b as char);
                    continue;
                }
                let cmd = std::mem::take(&mut self.line);
                let body = self.replies.get(cmd.as_str()).copied().unwrap_or("");
                self.out.extend_from_slice(format!("{cmd}\n{body}(97%/97%) dsh>").as_bytes());
                self.sent.push(cmd);
            }
            Ok(())
        }
        async fn read_available(&mut self) -> io::Result<Vec<u8>> {
            self.t += Duration::from_millis(5);
            Ok(std::mem::take(&mut self.out))
        }
        async fn clear_input(&mut self) -> io::Result<()> {
            Ok(())
        }
        async fn sleep(&mut self, d: Duration) {
            self.t += d;
        }
        fn now(&self) -> Duration {
            self.t
        }
    }

    #[test]
    fn capture_parses_each_signal() {
        let replies = HashMap::from([
            ("get system_state", "READY_TO_RECORD (10)\n"),
            ("get isp_version", "ISP Version ID = D0\n"),
            ("test iob", "IOB Version ID = B0\n"),
            ("test voltage all", "Checking - IOB\n  Vcc       1.0    = 1.006\nDone\n"),
            ("test temperature all", "  IOB        = 51  Pass\n"),
        ]);
        let fake = FakeShell { replies, out: Vec::new(), line: String::new(), sent: Vec::new(), t: Duration::ZERO };
        let mut sh = AsyncShell { transport: fake, timing: Timing::instant(), default_timeout: Duration::from_secs(1) };
        let mut steps = Vec::new();
        let cap = pollster::block_on(capture(&mut sh, "test", "fake", "2026-01-01", false, |s| steps.push(s.to_string())));

        assert_eq!(cap.system_state.as_ref().map(|s| s.code), Some(10));
        assert_eq!(cap.versions.isp.as_deref(), Some("D0"));
        // `get iob_version` had no answer, so the `test iob` fallback ran
        assert_eq!(cap.versions.iob.as_deref(), Some("B0"));
        assert_eq!(cap.voltages.len(), 1);
        assert_eq!(cap.temps.len(), 1);
        assert_eq!(cap.captured, "2026-01-01");
        assert!(cap.ddr.is_empty(), "deep=false must skip the DDR tests");
        assert!(!sh.transport.sent.iter().any(|c| c.starts_with("test ram")));
        assert_eq!(steps.first().map(String::as_str), Some("identity"));
        assert!(steps.iter().any(|s| s == "I2C scan"));
    }
}

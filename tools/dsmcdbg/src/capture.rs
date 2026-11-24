//! CLI glue over [`dsmc_core::capture`]: the blocking session + the date stamp.

use dsmc_core::baseline::{Capture, UcdV};
use dsmc_core::Shell;

pub fn capture(sh: &mut Shell, port: &str, deep: bool) -> Capture {
    let date = now_date();
    Shell::block_on(dsmc_core::capture::capture(sh.as_async(), "dsmcdbg", port, &date, deep, |_| {}))
}

pub fn capture_ucd_public(sh: &mut Shell) -> Vec<UcdV> {
    Shell::block_on(dsmc_core::capture::capture_ucd(sh.as_async()))
}

pub fn read_reg(sh: &mut Shell, addr: u32) -> Option<u32> {
    Shell::block_on(dsmc_core::capture::read_reg(sh.as_async(), addr))
}

fn now_date() -> String {
    // date only, no extra deps — read from the system `date` is overkill; use a
    // fixed-format stamp from std time.
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    // days since epoch -> y-m-d (civil calendar, Howard Hinnant's algorithm)
    let days = (secs / 86400) as i64;
    let (y, m, d) = civil_from_days(days);
    format!("{y:04}-{m:02}-{d:02}")
}

fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = (z - era * 146097) as i64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = (if mp < 10 { mp + 3 } else { mp - 9 }) as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

//! RED DSMC2 `.preset` codec + read/build — Rust port of `tools/preset/red_preset.py`.
//!
//! A `.preset` is a wxSQLite3 AES-256 encrypted SQLite database using the
//! universal RED key. This module reproduces the codec directly, so it needs no
//! RED app / lldb / macOS.
//!
//! Scheme (recovered from a known plaintext/ciphertext pair, round-trip verified):
//!   - AES key   = the ASCII key bytes repeated to 32 bytes, AES-256 in ECB.
//!   - page size = 1024 bytes; the last 12 bytes of each page are reserved and
//!                 hold a per-page nonce in the clear (SQLite reserved bytes = 12).
//!   - keystream = AES(counter_block), then each next block = AES(previous
//!                 keystream block); counter_block = u32 page-number (1-based,
//!                 little-endian) + the 12 nonce bytes. XORed over the first 1012.
//!   - page 1 only: bytes 0x10..0x18 are XORed with the keystream a second time
//!                 (the wxSQLite3 header-area fixup).
//!
//! PUBLIC scope note: preset build/read is part of the public base (option-2:
//! decrypt/read/preset/recovery). It is config data, not firmware code — no
//! patching/signing/flashing here.
//!
//! Replaces: `tools/preset/red_preset.py` (python, to be retired once this is proven).

use aes::cipher::generic_array::GenericArray;
use aes::cipher::{BlockEncrypt, KeyInit};
use aes::Aes256;

/// Universal RED preset key (ASCII), repeated to 32 bytes for AES-256.
pub const KEY_ASCII: &[u8] = b"ABk530WAcrRbvaoKAjc3qoD9BGAhiotq";
pub const PS: usize = 1024; // page size
pub const RV: usize = 12; // reserved bytes (nonce) per page
pub const DL: usize = PS - RV; // encrypted data length per page (1012)

fn cipher() -> Aes256 {
    let mut key32 = [0u8; 32];
    for (i, b) in key32.iter_mut().enumerate() {
        *b = KEY_ASCII[i % KEY_ASCII.len()];
    }
    Aes256::new(GenericArray::from_slice(&key32))
}

fn aes_block(c: &Aes256, input: &[u8; 16]) -> [u8; 16] {
    let mut blk = GenericArray::clone_from_slice(input);
    c.encrypt_block(&mut blk);
    let mut out = [0u8; 16];
    out.copy_from_slice(&blk);
    out
}

/// SQLite page size of a preset image. Header bytes 0x10..0x18 are readable in
/// the ciphertext too (page 1 XORs them with the keystream twice), so this works
/// on either side. Most presets use 1024; e.g. `Broadcast (RED).preset` uses 2048.
pub fn page_size(img: &[u8]) -> usize {
    match img.get(16..18).map(|b| u16::from_be_bytes([b[0], b[1]])) {
        Some(1) => 65536,
        Some(n) if n >= 512 && n.is_power_of_two() => n as usize,
        _ => PS,
    }
}

/// Build the `dl`-byte keystream for a page (1-based page_no) + 12-byte nonce.
fn keystream(c: &Aes256, page_no: u32, nonce: &[u8], dl: usize) -> Vec<u8> {
    let mut block = [0u8; 16];
    block[0..4].copy_from_slice(&page_no.to_le_bytes());
    block[4..16].copy_from_slice(&nonce[..12]);
    let mut ks = aes_block(c, &block).to_vec();
    while ks.len() < dl {
        let last: [u8; 16] = ks[ks.len() - 16..].try_into().unwrap();
        ks.extend_from_slice(&aes_block(c, &last));
    }
    ks
}

/// Decrypt a whole `.preset` ciphertext into the plaintext SQLite image.
pub fn decrypt(ct: &[u8]) -> Vec<u8> {
    let c = cipher();
    let ps = page_size(ct);
    let dl = ps - RV;
    let mut out = ct.to_vec();
    for p in 0..(ct.len() / ps) {
        let off = p * ps;
        let nonce = &ct[off + dl..off + ps];
        let ks = keystream(&c, (p + 1) as u32, nonce, dl);
        for j in 0..dl {
            out[off + j] = ct[off + j] ^ ks[j];
        }
        if p == 0 {
            for j in 0x10..0x18 {
                out[off + j] ^= ks[j];
            }
        }
    }
    out
}

/// Encrypt a plaintext SQLite image into a `.preset` ciphertext.
/// `nonce_fn(page_index)` supplies the 12 reserved nonce bytes per page (random
/// in production; a fixed generator in tests for determinism).
pub fn encrypt_with<F: FnMut(usize) -> [u8; RV]>(pt: &[u8], mut nonce_fn: F) -> Vec<u8> {
    let c = cipher();
    let ps = page_size(pt);
    let dl = ps - RV;
    let mut out = pt.to_vec();
    for p in 0..(pt.len() / ps) {
        let off = p * ps;
        let nonce = nonce_fn(p);
        let mut page = pt[off..off + ps].to_vec();
        page[dl..ps].copy_from_slice(&nonce);
        let ks = keystream(&c, (p + 1) as u32, &nonce, dl);
        for j in 0..dl {
            out[off + j] = page[j] ^ ks[j];
        }
        if p == 0 {
            for j in 0x10..0x18 {
                out[off + j] ^= ks[j];
            }
        }
        out[off + dl..off + ps].copy_from_slice(&nonce);
    }
    out
}

/// One parsed preset entry.
#[derive(Debug, Clone, PartialEq)]
pub struct Preset {
    pub version: i64,
    pub params: Vec<(String, String)>,
}

/// Read a `.preset` ciphertext: decrypt, open the SQLite image, pull version +
/// the parameters table. Replaces `red_preset.py read`.
pub fn read(ct: &[u8]) -> Result<Preset, String> {
    let pt = decrypt(ct);
    read_plaintext_db(&pt)
}

/// Open a decrypted SQLite image (bytes) and pull version + parameters.
pub fn read_plaintext_db(pt: &[u8]) -> Result<Preset, String> {
    let mut con = rusqlite::Connection::open_in_memory().map_err(|e| e.to_string())?;
    con.deserialize_read_exact(rusqlite::MAIN_DB, pt, pt.len(), true)
        .map_err(|e| e.to_string())?;
    let version: i64 = con
        .query_row("SELECT version FROM info", [], |r| r.get(0))
        .map_err(|e| e.to_string())?;
    let mut stmt = con
        .prepare("SELECT name, value FROM parameters")
        .map_err(|e| e.to_string())?;
    let rows = stmt
        .query_map([], |r| {
            let name: String = r.get(0)?;
            // value may be int or text; read as string
            let value: String = match r.get_ref(1)? {
                rusqlite::types::ValueRef::Integer(i) => i.to_string(),
                rusqlite::types::ValueRef::Real(f) => f.to_string(),
                rusqlite::types::ValueRef::Text(t) => String::from_utf8_lossy(t).into_owned(),
                rusqlite::types::ValueRef::Null => String::new(),
                rusqlite::types::ValueRef::Blob(_) => String::from("<blob>"),
            };
            Ok((name, value))
        })
        .map_err(|e| e.to_string())?;
    let mut params = Vec::new();
    for row in rows {
        params.push(row.map_err(|e| e.to_string())?);
    }
    Ok(Preset { version, params })
}

/// Build the plaintext preset SQLite image (page_size=1024, reserved_bytes=12).
/// Mirrors `red_preset.py _build_plaintext_db` via rusqlite + the raw
/// SQLITE_FCNTL_RESERVE_BYTES file-control. Replaces `red_preset.py make`.
/// Built in memory and serialized, so it needs no filesystem (runs in WASM).
pub fn build_plaintext_db(version: i64, pairs: &[(String, String)]) -> Result<Vec<u8>, String> {
    let mut con = rusqlite::Connection::open_in_memory().map_err(|e| e.to_string())?;
    con.pragma_update(None, "page_size", 1024i64)
        .map_err(|e| e.to_string())?;
    // reserved_bytes = 12 via the raw file control on the main db.
    unsafe {
        let h = con.handle();
        let mut nbytes: i32 = RV as i32;
        let rc = rusqlite::ffi::sqlite3_file_control(
            h,
            c"main".as_ptr(),
            rusqlite::ffi::SQLITE_FCNTL_RESERVE_BYTES,
            &mut nbytes as *mut i32 as *mut std::ffi::c_void,
        );
        if rc != rusqlite::ffi::SQLITE_OK {
            return Err(format!("file_control RESERVE_BYTES failed rc={rc}"));
        }
    }
    con.execute_batch(
        "CREATE TABLE info(version, delete_after_apply);\
         CREATE TABLE parameters(name UNIQUE, value);",
    )
    .map_err(|e| e.to_string())?;
    con.execute("INSERT INTO info VALUES(?1, 0)", [version])
        .map_err(|e| e.to_string())?;
    for (name, value) in pairs {
        // numeric values go in as integers, else as text — matches python.
        let is_int = {
            let t = value.strip_prefix('-').unwrap_or(value);
            !t.is_empty() && t.chars().all(|c| c.is_ascii_digit())
        };
        if is_int {
            let v: i64 = value.parse().map_err(|_| "bad int".to_string())?;
            con.execute("INSERT INTO parameters VALUES(?1, ?2)", rusqlite::params![name, v])
                .map_err(|e| e.to_string())?;
        } else {
            con.execute("INSERT INTO parameters VALUES(?1, ?2)", rusqlite::params![name, value])
                .map_err(|e| e.to_string())?;
        }
    }
    con.execute_batch("VACUUM").map_err(|e| e.to_string())?;
    let mut data = con
        .serialize(rusqlite::MAIN_DB)
        .map_err(|e| e.to_string())?
        .to_vec();
    if data.get(20) != Some(&(RV as u8)) {
        return Err(format!(
            "sqlite did not apply reserved_bytes=12 (byte 20 = {:?})",
            data.get(20)
        ));
    }
    if data.len() % PS != 0 {
        data.resize(data.len() + (PS - data.len() % PS), 0);
    }
    // An in-memory DB never bumps the header bookkeeping a file-backed write
    // does; stamp it so the image looks like an ordinary on-disk database:
    // file change counter (24) == version-valid-for (92), and the writer's
    // SQLITE_VERSION_NUMBER (96).
    let version_number = unsafe { rusqlite::ffi::sqlite3_libversion_number() } as u32;
    data[24..28].copy_from_slice(&1u32.to_be_bytes());
    data[92..96].copy_from_slice(&1u32.to_be_bytes());
    data[96..100].copy_from_slice(&version_number.to_be_bytes());
    Ok(data)
}

/// Full build: plaintext DB -> encrypt into a `.preset` ciphertext.
/// `nonce_fn` supplies per-page nonces (random in production).
pub fn make_with<F: FnMut(usize) -> [u8; RV]>(
    version: i64,
    pairs: &[(String, String)],
    nonce_fn: F,
) -> Result<Vec<u8>, String> {
    let pt = build_plaintext_db(version, pairs)?;
    Ok(encrypt_with(&pt, nonce_fn))
}

#[cfg(test)]
mod tests {
    use super::*;

    // deterministic nonce for round-trip testing
    fn fixed_nonce(p: usize) -> [u8; RV] {
        let mut n = [0u8; RV];
        n[0] = p as u8;
        n[1] = 0xA5;
        n
    }

    #[test]
    fn page_codec_roundtrips() {
        // one page of recognizable plaintext (leave reserved tail to the codec)
        let mut pt = vec![0u8; PS];
        for i in 0..DL {
            pt[i] = (i & 0xff) as u8;
        }
        let ct = encrypt_with(&pt, fixed_nonce);
        let back = decrypt(&ct);
        // the DL data region must round-trip exactly
        assert_eq!(&back[..DL], &pt[..DL]);
        // nonce stored in the clear in both
        assert_eq!(&ct[DL..PS], &fixed_nonce(0));
    }

    #[test]
    fn ciphertext_differs_from_plaintext() {
        let mut pt = vec![0u8; PS];
        pt[0x100] = 0x42;
        let ct = encrypt_with(&pt, fixed_nonce);
        assert_ne!(&ct[..DL], &pt[..DL]); // actually encrypted
    }

    #[test]
    fn build_read_roundtrip() {
        let pairs = vec![
            ("DEBUG_ENABLE_SHELL".to_string(), "1".to_string()),
            ("SOME_TEXT".to_string(), "hello".to_string()),
        ];
        let ct = make_with(7, &pairs, fixed_nonce).expect("build");
        let got = read(&ct).expect("read");
        assert_eq!(got.version, 7);
        // both params survive the build->encrypt->decrypt->read loop
        let names: Vec<_> = got.params.iter().map(|(n, _)| n.clone()).collect();
        assert!(names.contains(&"DEBUG_ENABLE_SHELL".to_string()));
        assert!(names.contains(&"SOME_TEXT".to_string()));
        let shell = got.params.iter().find(|(n, _)| n == "DEBUG_ENABLE_SHELL").unwrap();
        assert_eq!(shell.1, "1");
    }

    #[test]
    fn page_size_from_header() {
        let mut img = vec![0u8; 32];
        img[16..18].copy_from_slice(&2048u16.to_be_bytes());
        assert_eq!(page_size(&img), 2048);
        img[16..18].copy_from_slice(&1u16.to_be_bytes());
        assert_eq!(page_size(&img), 65536);
        img[16..18].copy_from_slice(&0u16.to_be_bytes());
        assert_eq!(page_size(&img), PS);
    }

    #[test]
    fn non_1024_page_preset_roundtrips() {
        // e.g. RED's own `Broadcast (RED).preset` is a 2048-byte-page database.
        let mut con = rusqlite::Connection::open_in_memory().unwrap();
        con.pragma_update(None, "page_size", 2048i64).unwrap();
        unsafe {
            let mut n: i32 = RV as i32;
            rusqlite::ffi::sqlite3_file_control(
                con.handle(),
                c"main".as_ptr(),
                rusqlite::ffi::SQLITE_FCNTL_RESERVE_BYTES,
                &mut n as *mut i32 as *mut std::ffi::c_void,
            );
        }
        con.execute_batch(
            "CREATE TABLE info(version, delete_after_apply);\
             CREATE TABLE parameters(name UNIQUE, value);\
             INSERT INTO info VALUES(16, 0);\
             INSERT INTO parameters VALUES('MONITOR_HDSDI_SYSTEM_MODE', 1);\
             VACUUM;",
        )
        .unwrap();
        let pt = con.serialize(rusqlite::MAIN_DB).unwrap().to_vec();
        assert_eq!(page_size(&pt), 2048);
        let ct = encrypt_with(&pt, fixed_nonce);
        assert_eq!(page_size(&ct), 2048);
        let p = read(&ct).unwrap();
        assert_eq!(p.version, 16);
        assert_eq!(p.params, vec![("MONITOR_HDSDI_SYSTEM_MODE".to_string(), "1".to_string())]);
    }
}

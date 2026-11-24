// Smoke tests for the wasm bindings the page calls. Synthetic inputs only, so
// CI needs no firmware or camera files. Run `pnpm wasm` first.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as w from "../src/wasm/dsmc_web.js";

w.initSync({ module: readFileSync(new URL("../src/wasm/dsmc_web_bg.wasm", import.meta.url)) });

test("preset build/read round-trips", () => {
  const params = [
    ["DEBUG_ENABLE_SHELL", "1"],
    ["COMM_SERIAL_PORT_PROTOCOL", "4"],
    ["SOME_TEXT", "hello"],
  ];
  const ct = w.preset_build(7, params);
  assert.equal(ct.length % 1024, 0);
  const p = w.preset_read(ct);
  assert.equal(p.version, 7);
  assert.deepEqual(p.params, params);
});

test("preset_read rejects garbage", () => {
  assert.throws(() => w.preset_read(new Uint8Array(2048)));
});

test("log decode: XOR-scrambled and plaintext logs both come out as text", () => {
  const pad = readFileSync(new URL("../../tools/red-log-decoder/log_xor_table.bin", import.meta.url));
  const text = "[0:00:01:234] ( 7%/ 8%) I: system state 1: BOOT\n".repeat(20);
  const plain = new TextEncoder().encode(text);
  const scrambled = plain.map((b, i) => b ^ pad[i & 0x3ff]);
  const dec = new TextDecoder();
  assert.equal(dec.decode(w.log_decode(scrambled)), text);
  assert.equal(dec.decode(w.log_decode(plain)), text);
  assert.throws(() => w.log_triage(new Uint8Array(0)), /no log sessions/);
});

test("error_explain finds dictionary entries", () => {
  const hits = w.error_explain("fan");
  assert.ok(Array.isArray(hits) && hits.length > 0);
  assert.ok(hits.every((h) => typeof h.title === "string" && typeof h.action === "string"));
});

test("firmware calls reject non-DSMC input with a clear message", () => {
  assert.throws(() => w.fw_inspect(new Uint8Array(4096)), /not a DSMC firmware package/);
  assert.throws(() => w.fw_extract(new Uint8Array(4096)), /not a DSMC firmware package/);
});

test("shell reply parsers", () => {
  const v = w.parse_voltages("Checking - IOB\n  Vcc       1.0    = 1.006\nDone\n(97%/97%) dsh>");
  assert.deepEqual(v, [{ board: "IOB", name: "Vcc 1.0", nominal: 1.0, measured: 1.006 }]);
  assert.deepEqual(w.parse_system_state("get system_state\n\nREADY_TO_RECORD (10)\n"), { name: "READY_TO_RECORD", code: 10 });
});

test("the embedded golden parses and checks a capture", () => {
  const golden = JSON.parse(w.default_golden());
  assert.equal(golden.schema, 1);
  const cap = JSON.stringify({ schema: 1, system_state: { name: "READY_TO_RECORD", code: 10 } });
  const r = w.assert_capture(cap);
  assert.ok(r.checks.some((c) => c.signal === "system_state" && c.verdict === "Pass"));
  assert.deepEqual(w.diff_captures(cap, cap), []);
});

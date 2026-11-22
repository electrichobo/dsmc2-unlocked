"""
Relinked-image support: ``--addr-map`` (stock -> moved section addresses), image-derived
reset/IRQ entry, and the lockstep divergence finder. Synthetic images only (no RED firmware):
a stock-layout image and the same code moved +0x10000 behind a base stub, as
``dsmc-fw relink symbolize`` lays a shifted build out.
"""

import json
import os
import struct
import subprocess
import sys

import pytest

from emulator.camera import Dsmc2Camera
from emulator.core.addrmap import AddrMap
from emulator.core.diverge import find_divergence
from emulator.core.firmware import FirmwareManager

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = 0x80000000
MOVE = 0x10000
RESET_OFF = 0x100
IRQ_OFF = 0x200
# mov r0, #0 ; 1: add r0, r0, #1 ; cmp r0, #3 ; bne 1b ; b .
LOOP = [0xE3A00000, 0xE2800001, 0xE3500003, 0x1AFFFFFC, 0xEAFFFFFE]


def vectors(at: int) -> bytes:
    """DSMC2 header at ``at``: b +0x20, 7x ldr pc,[pc,#0x24], ldr sp / ldr pc, SP, reset, handlers."""
    handlers = [at + 0x300 + 4 * k for k in range(7)]
    handlers[5] = at + IRQ_OFF                       # word 17 = IRQ handler
    words = [0xEA000006] + [0xE59FF024] * 7 + [0xE59FD000, 0xE59FF000, 0x881D2690, at + RESET_OFF] + handlers
    return struct.pack(f"<{len(words)}I", *words)


def image(at: int, loop=LOOP, stub: bool = False) -> bytes:
    off = at - BASE
    buf = bytearray(off + 0x1000)
    if stub:
        buf[0:8] = struct.pack("<II", 0xE51FF004, at)  # ldr pc, [pc, #-4] ; .word vectors
    hdr = vectors(at)
    buf[off:off + len(hdr)] = hdr
    code = struct.pack(f"<{len(loop)}I", *loop)
    buf[off + RESET_OFF:off + RESET_OFF + len(code)] = code
    return bytes(buf)


@pytest.fixture
def files(tmp_path):
    stock = tmp_path / "stock.bin"
    stock.write_bytes(image(BASE))
    moved = tmp_path / "moved.bin"
    moved.write_bytes(image(BASE + MOVE, stub=True))
    amap = tmp_path / "addrmap.tsv"
    amap.write_text(
        "# stock -> shifted layout\n# stock_start\tstock_end\tlayout_start\tsection\n"
        f"0x{BASE:08x}\t0x{BASE + 0x1000:08x}\t0x{BASE + MOVE:08x}\ts00_code\n"
        "0x80e9c000\t0x80e9d000\t0x80f9c000\ts03_code\n"
        "0x81490000\t0x814a0000\t0x81590000\ts08_bss\n")
    return tmp_path, stock, moved, amap


def test_addrmap_translates_both_ways(files):
    _, _, _, amap = files
    m = AddrMap.load(str(amap))
    assert not m.is_identity
    assert m.to_fw(BASE) == BASE + MOVE
    assert m.to_fw(0x80E9C368) == 0x80F9C368
    assert m.to_stock(0x80F9C368) == 0x80E9C368
    assert m.to_fw(0x814A0000) == 0x815A0000          # last row: end inclusive (.bss end symbol)
    assert m.to_fw(0x48200040) == 0x48200040          # MMIO: fixed
    assert m.to_fw(0x881D2690) == 0x881D2690          # stack top: fixed
    assert m.to_stock(BASE) == BASE                   # the base stub is no stock address
    assert m.section_of(0x80F9C368) == "s03_code"
    ident = AddrMap()
    assert ident.is_identity and ident.to_fw(0x80829F2C) == 0x80829F2C


def test_addrmap_rejects_overlap(tmp_path):
    p = tmp_path / "bad.tsv"
    p.write_text("0x80000000 0x80002000 0x80100000 a\n0x80001000 0x80003000 0x80200000 b\n")
    with pytest.raises(ValueError):
        AddrMap.load(str(p))


def test_vectors_follow_relinked_base_stub(files):
    _, stock, moved, _ = files
    v = FirmwareManager.parse_vectors(stock.read_bytes())
    assert v == {"sp": 0x881D2690, "reset_pc": BASE + RESET_OFF, "vector_base": BASE, "irq_vector": BASE + IRQ_OFF}
    v = FirmwareManager.parse_vectors(moved.read_bytes())
    assert v["vector_base"] == BASE + MOVE
    assert v["reset_pc"] == BASE + MOVE + RESET_OFF
    assert v["irq_vector"] == BASE + MOVE + IRQ_OFF
    meta = FirmwareManager.inspect(str(moved))
    assert meta.is_runnable and "relinked" in meta.format_name


def test_camera_uses_map_and_image_entry(files):
    ws, _, moved, amap = files
    cam = Dsmc2Camera(str(ws), addr_map=AddrMap.load(str(amap)))
    cam.load_firmware(str(moved))
    assert cam.entry_from_header
    assert cam.cpu.pc == cam.reset_pc == BASE + MOVE + RESET_OFF
    assert cam.cpu.irq_vector == BASE + MOVE + IRQ_OFF
    assert cam.is_idle_pc(0x80F9C368) and not cam.is_idle_pc(0x80F9C000)

    # a TCB at the moved address of a stock TCB is found and reported with stock addresses
    tcb, name = cam.fw(0x8149D494), cam.fw(0x8149D600)
    words = [0] * 20
    words[0], words[2], words[10], words[11], words[12], words[17] = 0x54485244, cam.fw(0x8149E000), name, 7, 3, cam.fw(BASE + RESET_OFF)
    cam.bus.write_mem(tcb, struct.pack("<20I", *words))
    cam.bus.write_mem(name, b"Synth Thread\x00")
    cam.is_booted = True
    t = [x for x in cam.get_threads() if x["name"] == "Synth Thread"]
    assert len(t) == 1
    assert t[0]["address"] == f"0x{tcb:08X}" and t[0]["stock"]["address"] == "0x8149D494"
    assert t[0]["stock"]["entry"] == f"0x{BASE + RESET_OFF:08X}"


def test_identity_without_map_unchanged(files):
    ws, stock, _, _ = files
    cam = Dsmc2Camera(str(ws))
    cam.load_firmware(str(stock))
    assert cam.addr_map.is_identity
    assert cam.fw(0x8149DDE8) == 0x8149DDE8
    assert cam.cpu.pc == BASE + RESET_OFF and cam.cpu.irq_vector == BASE + IRQ_OFF


def test_cli_boot_reports_stock_pc(files):
    ws, _, moved, amap = files
    out = subprocess.run(
        [sys.executable, "-m", "emulator.cli", "boot", "-f", str(moved), "--addr-map", str(amap), "-n", "2000", "--json"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    j = json.loads(out.stdout[out.stdout.index("{\n"):])
    assert j["status"] == "SUCCESS"
    assert j["reset_pc"] == f"0x{BASE + MOVE + RESET_OFF:08X}"
    assert j["stock"]["reset_pc"] == f"0x{BASE + RESET_OFF:08X}"
    assert j["stock"]["pc"] == f"0x{BASE + RESET_OFF + 16:08X}"   # parked on `b .`


def _maker(ws, path, amap=None):
    def make():
        cam = Dsmc2Camera(str(ws), addr_map=AddrMap.load(str(amap)) if amap else None)
        cam.load_firmware(str(path))
        return cam
    return make


def test_diverge_moved_image_matches(files):
    ws, stock, moved, amap = files
    r = find_divergence(_maker(ws, stock), _maker(ws, moved, amap), 5000, step=1000)
    assert r["diverged"] is False and r["instructions"] == 5000


def test_diverge_finds_first_differing_instruction(files):
    ws, stock, _, amap = files
    bad = ws / "bad.bin"
    loop = list(LOOP)
    loop[2] = 0xE3500004                                   # cmp r0, #4: one more round
    bad.write_bytes(image(BASE + MOVE, loop=loop, stub=True))
    r = find_divergence(_maker(ws, stock), _maker(ws, bad, amap), 5000, step=1000)
    assert r["diverged"] is True
    assert r["instruction_index"] == 8                     # the third cmp sets different flags
    assert r["at_pc_stock"] == f"0x{BASE + RESET_OFF + 8:08X}"
    assert any(d.startswith("cpsr ") for d in r["differences"])


def test_expect_compares_in_stock_addresses():
    from emulator.cli import compare_boot

    def run(pc, entry, state="QUEUE", stock=True):
        t = {"address": "0x81580000", "name": "T", "state": state, "priority": 2, "entry": entry,
             "sp": "0x81580100", "stack_range": "0x81570000-0x8157FFFF", "run_count": 1}
        r = {"status": "SUCCESS", "pc": pc, "sp": "0x83571454", "threads": [t]}
        if stock:
            t["stock"] = {"address": "0x81480000", "entry": "0x80830508", "sp": "0x81480100",
                          "stack_range": "0x81470000-0x8147FFFF"}
            r["stock"] = {"pc": "0x80E9C368", "sp": "0x83571454"}
        return r

    ref = {"status": "SUCCESS", "pc": "0x80E9C368", "sp": "0x83571454", "threads": [
        {"address": "0x81480000", "name": "T", "state": "QUEUE", "priority": 2, "entry": "0x80830508",
         "sp": "0x81480100", "stack_range": "0x81470000-0x8147FFFF", "run_count": 1}]}
    assert compare_boot(run("0x80FCC368", "0x80950508"), ref) == []
    bad = compare_boot(run("0x80FCC368", "0x80950508", state="READY"), ref)
    assert bad == ["thread 'T' state: QUEUE expected, got READY"]

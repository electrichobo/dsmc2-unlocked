"""
Regression tests for emulator/docs/DEPTH_ROADMAP.md Priorities 1, 2, and 3:
  1. YAFFS2 multi-object /image2 partition staging (cdb2/factory.db + Xtensa blobs)
  2. Real-FW CDB2 GET & SET round-trip through /image2/cdb2/factory.db and /data/cdb2/user.db
  3. TraceProbe runtime milestone capture & real-HW stolen-instruction trampoline export
  4. Real-FW ISP/IOB IRAM & DRAM blob upload into emulated XtensaCore instances
  5. Sidekick MCU core + 220x176 monochrome OLED + rotary/button peripherals
"""

import struct
import sqlite3
import tempfile
import os
import pytest

from conftest import requires_firmware
from emulator.camera import Dsmc2Camera
from emulator.core.trace_probe import (
    TraceProbe,
    encode_arm_branch,
    build_trampoline_words,
)
from emulator.devices.gpmc import OmapGpmc
from emulator.devices.nand_image import (
    PAGE_SIZE,
    PAGES_PER_BLOCK,
    PARTITIONS,
    build_default_xtensa_blobs,
    build_minimal_factory_db_blob,
    build_yaffs_tree_chunks,
    write_image_partition,
)
from emulator.devices.sidekick import SidekickMcu


# ---------------------------------------------------------------------------
# Priority 1 — YAFFS2 multi-object /image2 stager & compact SQLite DB
# ---------------------------------------------------------------------------

def test_minimal_factory_db_valid_sqlite_schema():
    blob = build_minimal_factory_db_blob()
    assert blob.startswith(b"SQLite format 3\x00")
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(blob)
        db = sqlite3.connect(tmp)
        tables = {
            r[0]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"parameters", "key_sources", "key_events", "key_actions"} <= tables
        val = db.execute(
            "SELECT default_value FROM parameters WHERE name='COMM_IP_ADDRESS'"
        ).fetchone()
        assert val == (168430090,)
        db.close()
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def test_yaffs_tree_chunks_advances_seq_per_block():
    big_blob = b"A" * (PAGES_PER_BLOCK * PAGE_SIZE + 512)
    chunks = build_yaffs_tree_chunks({"dir": {"big.bin": big_blob}}, seq_base=0x1000)
    # chunk 0 is dir hdr, chunk 1 is file hdr, data chunks span into block 1
    first_seq = struct.unpack("<I", chunks[0][2][4:8])[0]
    last_seq = struct.unpack("<I", chunks[-1][2][4:8])[0]
    assert first_seq == 0x1000
    assert last_seq == 0x1001


def test_write_image_partition_programs_image2_block():
    gpmc = OmapGpmc()
    info = write_image_partition(
        gpmc, image_start_block=PARTITIONS[2][1], include_xtensa_blobs=True
    )
    assert info["pages"] > 6
    page0 = gpmc.nand_image[PARTITIONS[2][1] * PAGES_PER_BLOCK]
    # Dir header obj_type == 3 ("cdb2" at OH+0x0A)
    assert struct.unpack("<I", page0[:4])[0] == 3
    assert page0[0x0A:0x0E] == b"cdb2"


# ---------------------------------------------------------------------------
# Priority 3, Tip #1 — TraceProbe & real-HW trampoline generation
# ---------------------------------------------------------------------------

def test_trampoline_encoding_roundtrip():
    target_pc = 0x8007FB58
    tramp_addr = 0x80F00000
    logger_addr = 0x808438C4
    stolen = 0xE92D4FF0  # push {r4-r11, lr}

    hook_b = encode_arm_branch(target_pc, tramp_addr, link=False)
    assert (hook_b >> 24) == 0xEA

    words = build_trampoline_words(
        target_pc=target_pc,
        probe_id=3,
        displaced_insn=stolen,
        trampoline_addr=tramp_addr,
        logger_fn_addr=logger_addr,
    )
    assert len(words) == 6
    assert words[0] == 0xE92D500F
    assert words[1] == 0xE3A00003
    assert (words[2] >> 24) == 0xEB
    assert words[3] == 0xE8BD500F
    assert words[4] == stolen
    assert (words[5] >> 24) == 0xEA


# ---------------------------------------------------------------------------
# Priority 3, Tip #2 — Xtensa upload + Sidekick MCU core
# ---------------------------------------------------------------------------

def test_xtensa_upload_segment_tracks_iram_and_dram(tmp_path):
    cam = Dsmc2Camera(str(tmp_path))
    blobs = build_default_xtensa_blobs()
    cam.isp1.upload_segment(cam.isp1.DRAM_BASE, blobs["isp"]["dram.bin"])
    cam.isp1.upload_segment(cam.isp1.IRAM_BASE, blobs["isp"]["iram.bin"])
    cam.isp1.release_reset()

    status = cam.isp1.get_status()
    assert status["launched_by_fw"] is True
    assert status["state"] == "RUNNING"
    assert status["dram_uploaded_bytes"] == len(blobs["isp"]["dram.bin"])
    assert status["iram_uploaded_bytes"] == len(blobs["isp"]["iram.bin"])
    assert bytes(cam.isp1.iram[:12]) == b"ISP_IRAM_741"
    assert bytes(cam.isp1.dram[:12]) == b"ISP_DRAM_741"


def test_sidekick_mcu_controls_and_oled(tmp_path):
    cam = Dsmc2Camera(str(tmp_path))
    sk: SidekickMcu = cam.sidekick
    assert sk.OLED_WIDTH == 220
    assert sk.OLED_HEIGHT == 176
    assert len(sk.framebuffer) == 220 * 176

    before_pixels = bytes(sk.framebuffer)
    res_rot = sk.press_button("ROTARY_CW")
    assert res_rot["cmd"] == "$S:ISO:1000"
    assert sk.telemetry["iso"] == 1000
    assert bytes(sk.framebuffer) != before_pixels

    res_menu = sk.press_button("MENU")
    assert res_menu["active_page"] == "ISO"
    assert sk.get_status()["oled_lit_pixels"] > 100

    cam.rcp.state["iso"] = "1600"
    sk.sync_from_camera(cam)
    assert sk.telemetry["iso"] == 1600


# ---------------------------------------------------------------------------
# Live firmware-backed tests (Priorities 1, 2, 3 end-to-end on Scarlet-W 7.4.1)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def booted_late_init(tmp_path_factory):
    root = tmp_path_factory.mktemp("depth_fw")
    cam = Dsmc2Camera(str(root))
    fw_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "firmware_store",
        "unpacked",
        "scarlet_w_7_4_1",
        "slot",
        "dsmc_program.mem",
    )
    cam.load_firmware(fw_path)
    probe = cam.install_trace_probe()
    cam.boot_to_late_init()
    return cam, probe


@requires_firmware
def test_live_fw_trace_probe_and_hw_patches(booted_late_init):
    cam, probe = booted_late_init
    hits = probe.milestones_hit()
    assert "mount_all_partitions" in hits
    assert "cdb2_init" in hits
    patches = probe.build_hw_patch_table(cam)
    assert len(patches) == len(probe.points)
    cdb2_patch = next(p for p in patches if p.label == "cdb2_init")
    assert cdb2_patch.displaced_insn != 0
    assert len(cdb2_patch.trampoline_words) == 6


@requires_firmware
def test_live_fw_cdb2_get_and_set_roundtrip(booted_late_init):
    cam, _ = booted_late_init
    assert cam.bus.read_u32(cam.fw(cam.CDB2_READY_FLAG_ADDR)) != 0
    assert cam.bus.read_u32(cam.fw(cam.CDB2_ATTACH_STMT_ADDR)) != 0
    assert cam.bus.read_u32(cam.fw(cam.CDB2_GET_STMT_ADDR)) != 0
    assert cam.bus.read_u32(cam.fw(cam.CDB2_SET_STMT_ADDR)) != 0

    rc_ip, ip_val = cam.fw_cdb2_get_uint32("COMM_IP_ADDRESS")
    assert rc_ip == 0
    assert ip_val == 168430090

    rc_set = cam.fw_cdb2_set_uint32("DEBUG_ENABLE_SHELL", 1)
    assert rc_set == 0
    rc_get, new_val = cam.fw_cdb2_get_uint32("DEBUG_ENABLE_SHELL")
    assert rc_get == 0
    assert new_val == 1


@requires_firmware
def test_live_fw_asic_xtensa_upload(booted_late_init):
    cam, _ = booted_late_init
    # Trigger real firmware's isp_load_from_embedded & iob_load_from_embedded directly
    rc_isp = cam.call_function(cam.ISP_LOAD_EMBEDDED_ADDR, [], max_instructions=2_000_000)
    rc_iob0 = cam.call_function(cam.IOB_LOAD_EMBEDDED_ADDR, [0], max_instructions=2_000_000)
    rc_iob1 = cam.call_function(cam.IOB_LOAD_EMBEDDED_ADDR, [1], max_instructions=2_000_000)
    assert rc_isp == 1
    assert rc_iob0 == 1
    assert rc_iob1 == 1
    assert cam.isp1.iram_uploaded_bytes >= 64
    assert cam.isp1.dram_uploaded_bytes >= 64
    assert cam.isp1.launched_by_fw is True
    assert cam.iob0.iram_uploaded_bytes >= 64
    assert cam.iob0.dram_uploaded_bytes >= 64
    assert cam.iob0.launched_by_fw is True
    assert cam.iob1.iram_uploaded_bytes >= 64
    assert cam.iob1.dram_uploaded_bytes >= 64
    assert cam.iob1.launched_by_fw is True

"""
Tests for Milestone 3 (SATA3-FPGA AHCI HBA0, Marvell Side-SATA, UDF Volume Image,
and N-frame Dual-ISP Wavelet .R3D Recording) and Milestone 4 (In-Camera Clip Playback,
Persistent Presets / 3D LUT / ASC-CDL / Reticle / Overlay Profiles, Redpine RS9113 Wi-Fi,
and RFTP File Transfer).
"""

import json
import os
import socket
import struct
import zlib
import base64
import pytest
from PIL import Image

from emulator.camera import Dsmc2Camera
from emulator.ui.redui import RedUiEngine
from emulator.rcp.protocol import RcpProtocol


@pytest.fixture
def cam(tmp_path):
    slot_dir = tmp_path / "firmware_store" / "unpacked" / "scarlet_w_7_4_1" / "slot"
    slot_dir.mkdir(parents=True)
    (slot_dir / "dsmc_program.mem").write_bytes(b"\x00" * 4096)
    (slot_dir / "system.env").write_text("CAMERA_NAME=SCARLET-W 5K\nPIN=10042\n")
    return Dsmc2Camera(str(tmp_path))


# ---------------------------------------------------------------------------
# Milestone 3: SATA3 AHCI HBA0, Marvell Bridge, UDF Mount & Wavelet R3D
# ---------------------------------------------------------------------------


def test_sata3_ahci_hba0_shadow_regs_and_marvell_bridge(cam):
    """Verify SATA3-FPGA HBA0 AHCI registers, LSB-FPGA SSD detect, and Marvell bridge."""
    cam.c2c.set_ssd_present(True)
    # Direct AHCI HBA0 / Port 0 / SATA3 FPGA status regs @ 0x1C06xxxx / 0x1C070010
    assert cam.bus.read_u32(0x1C060000) == 0x4034FF00  # CAP
    assert cam.bus.read_u32(0x1C060128) == 0x00000133  # PxSSTS: Gen3 6.0Gbps, DET=3
    assert cam.bus.read_u32(0x1C070010) == 0x00000000  # Link healthy (bit 2 clear)

    # Shadow SREGs @ 0x1C0B0680..0x1C0B06CC (SATADriver.c idx 0x1A0..0x1B3)
    assert cam.bus.read_u32(0x1C0B0680) == 0x4034FF00
    assert cam.bus.read_u32(0x1C0B06B8) == 0x00000133
    assert cam.bus.read_u32(0x1C0B06CC) == 0x0000000C

    # LSB-FPGA detect_ssd @ 0x44A0000C (bit 18 clear when SSD present)
    assert (cam.c2c.spi_read_reg("ts_fpga", 0x44A0000C) & 0x00040000) == 0

    # Eject sets SSD detect bit 18 & clears PxSSTS; remount restores
    cam.recorder.eject_media()
    assert cam.bus.read_u32(0x1C0B06B8) == 0x00000000
    assert (cam.c2c.spi_read_reg("ts_fpga", 0x44A0000C) & 0x00040000) == 0x00040000

    cam.recorder.mount_media()
    assert cam.bus.read_u32(0x1C0B06B8) == 0x00000133
    assert (cam.c2c.spi_read_reg("ts_fpga", 0x44A0000C) & 0x00040000) == 0

    # Side-SATA Marvell serial bridge + SM2246EN SMART 0x90
    bridge = cam.c2c.side_sata_bridge
    assert bridge.execute_serial_command("R0000") == "1B4B9230"
    assert bridge.execute_serial_command("W0010A5A51234") == "OK"
    assert bridge.execute_serial_command("R0010") == "A5A51234"
    smart = bridge.get_smart_log_90()
    assert smart["controller"] == "SM2246EN"
    assert smart["auth_signature"] == "RED_SM2246EN_SMART90_OK"


def test_udf_volume_image_and_n_frame_wavelet_r3d_recording(cam):
    """Verify UDF 2.01 volume descriptor image and deterministic N-frame ISP1/ISP2 .R3D recording."""
    udf = cam.recorder.verify_udf_filesystem()
    assert udf["valid"] is True
    assert udf["filesystem"] == "UDF 2.01 (ECMA-167)"
    assert udf["volume_id"] == "RED_MINIMAG"
    assert os.path.isfile(udf["udf_image_path"])

    # Deterministic 6-frame record through dual ISP1/ISP2 + IOB0 SATA DMA
    rec_res = cam.recorder.record_clip_frames(num_frames=6)
    assert rec_res["frames_recorded"] == 6
    assert cam.isp1.frames_compressed == 3
    assert cam.isp2.frames_compressed == 3
    assert cam.iob0.sata_dma_frames_written == 6

    # Inspect the parsed .R3D container on disk
    inspected = cam.recorder.inspect_r3d_clip(rec_res["clip_name"])
    assert inspected is not None
    assert inspected["brand"] == "RED2"
    assert inspected["frame_count"] == 6
    assert "FRAMES=6" in inspected["index_footer"]
    assert "OFFSETS=" in inspected["index_footer"]
    for idx, frame in enumerate(inspected["frames"]):
        assert frame["frame_index"] == idx
        assert frame["compressed_size"] >= 64
        assert set(frame["slice_crcs"].keys()) == {"G1", "B", "R", "G2"}


# ---------------------------------------------------------------------------
# Milestone 4: Playback, Presets/LUTs/CDLs, Wi-Fi & RFTP
# ---------------------------------------------------------------------------


def test_in_camera_playback_and_clip_inspector_ui(cam):
    """Verify RedUi playback browsing, transport, stepping, markers, inspector, and HUD render."""
    rec = cam.recorder.record_clip_frames(num_frames=5)
    ui = RedUiEngine(cam)

    # Enter playback browser via DSMC2 menu tree
    ui.menu_path = ["Menu", "PLAYBACK", "Playback Screen"]
    ui._handle_menu_item_selected("Browse Recorded Clips")
    assert ui.active_alert is None
    assert ui.is_playback_active is True
    assert ui.active_clip_info is not None
    assert ui.active_clip_info["clip_name"] == rec["clip_name"]
    assert ui.playback_frame_index == 0

    # Play / Pause, Frame Step Forward/Backward, Shuttle, In/Out Marker, Inspector
    ui._handle_menu_item_selected("Play / Pause")
    assert ui.playback_state == "PLAYING"
    ui._handle_menu_item_selected("Frame Step Forward")
    ui._handle_menu_item_selected("Frame Step Forward")
    assert ui.playback_frame_index == 2
    ui._handle_menu_item_selected("In/Out Marker")
    assert ui.playback_in_frame == 2
    assert ui.playback_out_frame == 4
    ui._handle_menu_item_selected("Shuttle 2x / 4x")
    assert ui.playback_state == "SHUTTLE_2X"
    ui._handle_menu_item_selected("Frame Step Backward")
    assert ui.playback_frame_index == 1
    ui._handle_menu_item_selected("Clip Metadata Inspector")
    assert ui.playback_inspector_open is True

    # Render HUD in playback mode
    img = Image.new("RGB", (1024, 576), (30, 40, 50))
    rendered = ui.render(img)
    assert rendered.size == (1024, 576)


def test_presets_cube_lut_cdl_reticle_and_overlay_persistence(cam):
    """Verify user presets, .cube 3D LUTs, CDLs, reticles, overlays, and keys persist cleanly."""
    ui = RedUiEngine(cam)

    # 1. User preset save/modify/reload roundtrip via menu options
    cam.rcp.state[RcpProtocol.PARAM_ISO] = "1280"
    cam.rcp.state[RcpProtocol.PARAM_FPS] = "48.00"
    ui.menu_path = ["Menu", "PRESETS", "Camera Presets"]
    ui._handle_menu_item_selected("Save User Preset to Memory")
    ui._handle_menu_item_selected("Export Preset to MINI-MAG")
    assert ui.active_alert is None

    cam.rcp.state[RcpProtocol.PARAM_ISO] = "250"
    cam.rcp.state[RcpProtocol.PARAM_FPS] = "23.98"
    ui._handle_menu_item_selected("Load User Preset")
    assert ui.active_alert is None
    assert cam.rcp.state[RcpProtocol.PARAM_ISO] == "1280"
    assert cam.rcp.state[RcpProtocol.PARAM_FPS] == "48.00"

    # 2. Import 3D LUT (.cube) from MINI-MAG
    ui.menu_path = ["Menu", "IMAGE", "3D LUT", "Creative 3D LUT"]
    ui._handle_menu_item_selected("Import 3D LUT (.cube)")
    assert ui.active_alert is None
    assert ui.loaded_cube_lut is not None
    assert ui.loaded_cube_lut["size"] == 2
    assert ui.loaded_cube_lut["entries_count"] == 8
    assert os.path.isfile(ui.loaded_cube_lut["path"])

    # 3. Export & Import CDL
    ui.cdl_params["saturation"] = 1.15
    ui.menu_path = ["Menu", "IMAGE", "CDL", "CDLs"]
    ui._handle_menu_item_selected("Export CDL")
    ui.cdl_params["saturation"] = 1.0
    ui._handle_menu_item_selected("Import CDL")
    assert ui.cdl_params["saturation"] == 1.15
    assert ui.active_alert is None

    # 4. Reticle & Overlays persistence
    ui.focus_point = (320, 180)
    ui.guide_mode = "2.39:1"
    ui.menu_path = ["Menu", "OVERLAYS", "CUSTOM", "In Camera"]
    ui._handle_menu_item_selected("Save Current Reticle")
    ui.menu_path = ["Menu", "OVERLAYS", "CUSTOM", "On Media, overlays"]
    ui._handle_menu_item_selected("Save Overlays to RED MINI-MAG")
    ui.focus_point = (512, 288)
    ui.guide_mode = "16:9"
    ui.load_reticle()
    assert ui.focus_point == (320, 180)
    ui.load_overlays_from_media()
    assert ui.guide_mode == "2.39:1"
    assert ui.active_alert is None


def test_redpine_rs9113_wifi_and_rftp_transfer(cam):
    """Verify Redpine RS9113 Wi-Fi SPI commands, modes, and RFTP TCP GET/PUT/LIST/STAT."""
    wifi = cam.wifi
    status = wifi.get_status()
    assert status["chipset"] == "Redpine Signals RS9113"
    assert status["mode"] == "INFRASTRUCTURE"

    # SPI MAC & Status queries
    mac_pkt = wifi.spi_transceive_command(wifi.CMD_GET_MAC)
    assert mac_pkt[:2] == bytes([0x58, wifi.CMD_GET_MAC])
    assert len(mac_pkt) == 10
    stat_pkt = wifi.spi_transceive_command(wifi.CMD_GET_STATUS)
    assert stat_pkt[4] == 0x01  # connected

    wifi.set_mode("WiFi Ad-Hoc")
    assert wifi.mode == "ADHOC"
    wifi.set_mode("WiFi OFF")
    assert wifi.connected is False
    wifi.set_mode("WiFi Infrastructure")
    assert wifi.connected is True

    # Record a clip and upload a LUT, then transfer over RFTP TCP server
    rec = cam.recorder.record_clip_frames(num_frames=3)
    port = cam.rftp.start(port=0)
    try:
        def _rpc(line: str):
            with socket.create_connection(("127.0.0.1", port), timeout=3.0) as s:
                s.sendall((line + "\n").encode("utf-8"))
                chunks = []
                while True:
                    b = s.recv(65536)
                    if not b:
                        break
                    chunks.append(b)
                    if b.endswith(b"\n"):
                        break
                return json.loads(b"".join(chunks).decode("utf-8"))

        lst = _rpc("LIST /media")
        assert lst["status"] == "OK"
        names = [e["name"] for e in lst["entries"]]
        r3d_name = os.path.basename(rec["r3d_path"])
        assert r3d_name in names

        got = _rpc(f"GET /media/{r3d_name}")
        assert got["status"] == "OK"
        raw = base64.b64decode(got["data_b64"])
        with open(rec["r3d_path"], "rb") as f:
            disk_bytes = f.read()
        assert raw == disk_bytes
        assert got["crc32"] == (zlib.crc32(disk_bytes) & 0xFFFFFFFF)

        # PUT custom .cube LUT into /data/luts via RFTP and verify with STAT + DELETE
        lut_bytes = b'TITLE "RFTP Test Look"\nLUT_3D_SIZE 2\n'
        put_res = _rpc(f"PUT /data/luts/rftp.cube {base64.b64encode(lut_bytes).decode('ascii')}")
        assert put_res["status"] == "OK"
        st = _rpc("STAT /data/luts/rftp.cube")
        assert st["exists"] is True
        assert st["size"] == len(lut_bytes)
        dele = _rpc("DELETE /data/luts/rftp.cube")
        assert dele["deleted"] is True
    finally:
        cam.rftp.stop()

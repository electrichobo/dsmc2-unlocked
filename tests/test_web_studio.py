"""
Unit and integration tests for RED DSMC2 Web-Based Virtual Camera Studio.
"""

import json
import os
import threading
import urllib.request
import pytest

from emulator.ui.web import WebStudio, ThreadedHTTPServer, create_handler, find_free_port

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def web_server():
    """Start an isolated WebStudio test server on a dynamic free port."""
    test_port = find_free_port(8900)
    studio = WebStudio(REPO_ROOT, host="127.0.0.1", port=test_port)
    studio.start_camera()

    handler = create_handler(studio)
    server = ThreadedHTTPServer(("127.0.0.1", test_port), handler)
    srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
    srv_thread.start()

    base_url = f"http://127.0.0.1:{test_port}"
    yield {"studio": studio, "server": server, "url": base_url}

    server.shutdown()
    studio.stop()


def test_web_studio_serves_html(web_server):
    """Verify GET / returns authentic RED DSMC2 Web Studio HTML."""
    url = web_server["url"] + "/"
    resp = urllib.request.urlopen(url)
    assert resp.status == 200
    html = resp.read().decode("utf-8")
    assert "DSMC2 VIRTUAL CAMERA STUDIO" in html
    assert "RED TOUCH 7.0 LCD MONITOR" in html
    assert "touchCanvas" in html


def test_web_studio_serves_frame(web_server):
    """Verify GET /api/frame returns valid JPEG frame bytes."""
    url = web_server["url"] + "/api/frame"
    resp = urllib.request.urlopen(url)
    assert resp.status == 200
    data = resp.read()
    # JPEG magic bytes
    assert data[:2] == b"\xff\xd8"
    assert len(data) > 10_000


def test_web_studio_status_api(web_server):
    """Verify GET /api/status returns live telemetry and camera state."""
    url = web_server["url"] + "/api/status"
    resp = urllib.request.urlopen(url)
    assert resp.status == 200
    status = json.loads(resp.read().decode("utf-8"))
    assert status["power_state"] == "ON"
    assert "rcp" in status
    assert "threads" in status
    assert len(status["threads"]) >= 4


def test_web_studio_touch_interaction(web_server):
    """Verify POST /api/touch dispatches to maXTouch and opens ISO parameter dialog."""
    studio = web_server["studio"]
    url = web_server["url"] + "/api/touch"

    # Touch ISO badge at (633, 16)
    payload = json.dumps({"x": 633, "y": 16, "pressed": True}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req)
    assert resp.status == 200
    res_data = json.loads(resp.read().decode("utf-8"))
    assert res_data["status"] == "ok"
    assert studio.compositor.redui.active_dialog == "ISO"

    # Touch ISO 1600 inside dialog (x=618, y=283)
    payload2 = json.dumps({"x": 618, "y": 283, "pressed": True}).encode("utf-8")
    req2 = urllib.request.Request(url, data=payload2, headers={"Content-Type": "application/json"})
    resp2 = urllib.request.urlopen(req2)
    assert resp2.status == 200
    assert studio.camera.rcp.state["iso"] == "1600"
    assert studio.compositor.redui.active_dialog is None


def test_web_studio_physical_buttons(web_server):
    """Verify POST /api/button dispatches physical hardware controls."""
    studio = web_server["studio"]
    url = web_server["url"] + "/api/button"

    # MENU button
    assert studio.compositor.redui.is_menu_open is False
    req = urllib.request.Request(url, data=json.dumps({"button": "MENU"}).encode("utf-8"), headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req)
    assert studio.compositor.redui.is_menu_open is True

    # 1:1 MAG button
    req = urllib.request.Request(url, data=json.dumps({"button": "1:1 MAG"}).encode("utf-8"), headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req)
    assert studio.compositor.redui.is_magnify_on is True

    # REC button
    req = urllib.request.Request(url, data=json.dumps({"button": "REC"}).encode("utf-8"), headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req)
    assert studio.camera.rcp.state["record_state"] == "2"

    req2 = urllib.request.Request(url, data=json.dumps({"button": "REC"}).encode("utf-8"), headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req2)
    assert studio.camera.rcp.state["record_state"] == "0"


def test_web_studio_logs_stream(web_server):
    """Verify GET /api/logs returns completed lines from UART4."""
    url = web_server["url"] + "/api/logs"
    resp = urllib.request.urlopen(url)
    assert resp.status == 200
    data = json.loads(resp.read().decode("utf-8"))
    assert "logs" in data
    assert len(data["logs"]) > 0


def test_web_studio_lens_api(web_server):
    """Verify GET /api/lens and POST /api/lens/aperture controls."""
    url = web_server["url"] + "/api/lens"
    resp = urllib.request.urlopen(url)
    assert resp.status == 200
    data = json.loads(resp.read().decode("utf-8"))
    assert "status" in data
    assert "catalog" in data
    assert data["status"]["attached"] is True

    # Adjust aperture via POST /api/lens/aperture
    set_url = web_server["url"] + "/api/lens/aperture"
    req = urllib.request.Request(set_url, data=json.dumps({"aperture": 4.0}).encode("utf-8"), headers={"Content-Type": "application/json"})
    resp2 = urllib.request.urlopen(req)
    assert resp2.status == 200
    assert web_server["studio"].camera.lens_mount.current_aperture == 4.0


def test_web_studio_media_api(web_server):
    """Verify GET /api/media/clips returns clip history."""
    url = web_server["url"] + "/api/media/clips"
    resp = urllib.request.urlopen(url)
    assert resp.status == 200
    data = json.loads(resp.read().decode("utf-8"))
    assert "clips" in data
    assert isinstance(data["clips"], list)


def test_web_studio_sensor_feed_cinema(web_server):
    """Verify compositor loads cinema sensor frame and serves valid frame."""
    compositor = web_server["studio"].compositor
    assert compositor.sensor_feed_image is not None
    assert compositor.sensor_feed_image.size == (1024, 576)

    # Compose frame when camera is powered ON
    frame = compositor.compose_frame()
    assert frame.size == (1024, 576)


def test_web_studio_responsive_layout(web_server):
    """Verify HTML serves top-screen layout with responsive 16:9 aspect ratio and full-width workbench."""
    url = web_server["url"] + "/"
    resp = urllib.request.urlopen(url)
    html = resp.read().decode("utf-8")
    assert "aspect-ratio: 16 / 9" in html
    assert "tactile-deck" in html
    assert "workbench" in html
    assert "minimag-slot-bay" in html
    assert "mag-cartridge" in html
    assert "mag-dropzone" in html


def test_web_studio_firmware_loader_lifecycle(web_server):
    """Verify RED MINI-MAG firmware loader slot status, custom upload staging, and baseline reset."""
    base_url = web_server["url"]

    # 1. Initial firmware status (confirmed working baseline)
    resp = urllib.request.urlopen(base_url + "/api/firmware/status")
    assert resp.status == 200
    info = json.loads(resp.read().decode("utf-8"))
    assert info["status"] == "ok"
    assert "SCARLET-W 5K (v7.4.1)" in info["active_name"]
    assert info["is_custom"] is False
    assert info["slot_mounted"] is True

    # 2. Upload custom firmware image
    import base64
    test_fw_bytes = b"RED_CUSTOM_FIRMWARE_HEADER\x00\x01\x02\x03THREADX_IMAGE_DATA"
    b64_content = base64.b64encode(test_fw_bytes).decode("ascii")
    upload_url = base_url + "/api/firmware/upload"
    payload = json.dumps({"filename": "custom_mod_build101.bin", "data": b64_content}).encode("utf-8")
    req = urllib.request.Request(upload_url, data=payload, headers={"Content-Type": "application/json"})
    upload_resp = urllib.request.urlopen(req)
    assert upload_resp.status == 200
    res_data = json.loads(upload_resp.read().decode("utf-8"))
    assert res_data["status"] == "ok"
    assert "custom_mod_build101.bin" in res_data["active_name"]

    # Verify /api/status telemetry reflects custom firmware
    status_resp = urllib.request.urlopen(base_url + "/api/status")
    status_json = json.loads(status_resp.read().decode("utf-8"))
    assert status_json["firmware"]["is_custom"] is True
    assert "custom_mod_build101.bin" in status_json["firmware"]["active_name"]

    # Verify custom firmware file exists on disk
    dest_path = os.path.join(REPO_ROOT, "firmware_store", "custom", "custom_mod_build101.bin")
    assert os.path.exists(dest_path)

    # 3. Reset to confirmed baseline
    reset_url = base_url + "/api/firmware/reset"
    req_reset = urllib.request.Request(reset_url, data=b"{}", headers={"Content-Type": "application/json"})
    reset_resp = urllib.request.urlopen(req_reset)
    assert reset_resp.status == 200
    reset_json = json.loads(reset_resp.read().decode("utf-8"))
    assert reset_json["status"] == "ok"
    assert "SCARLET-W 5K (v7.4.1)" in reset_json["active_name"]

    # Verify status polled afterwards shows confirmed baseline
    status_resp2 = urllib.request.urlopen(base_url + "/api/status")
    status_json2 = json.loads(status_resp2.read().decode("utf-8"))
    assert status_json2["firmware"]["is_custom"] is False


"""
Unit and regression tests for RedUiEngine interactive touchscreen and menu system.
"""

import pytest
from PIL import Image
from emulator.camera import Dsmc2Camera
from emulator.ui.redui import RedUiEngine
from emulator.rcp.protocol import RcpProtocol


@pytest.fixture
def camera(tmp_path):
    cam = Dsmc2Camera(workspace_root=str(tmp_path))
    return cam


@pytest.fixture
def redui(camera):
    return RedUiEngine(camera)


def test_touch_iso_badge_opens_dialog(redui, camera):
    """Verify touching the ISO badge (x=633, y=16) opens the ISO selection dialog."""
    # User's exact reported touch coordinate
    handled = redui.handle_touch(633, 16, pressed=True)
    assert handled is True
    assert redui.active_dialog == "ISO"

    # Verify rendering includes the dialog
    base = Image.new("RGB", (1024, 576), (20, 20, 20))
    rendered = redui.render(base)
    assert rendered.size == (1024, 576)

    # Touch ISO 1280 inside dialog
    # ISO options are at dx1 + 30, dy1 + 60. Index 7 is "1280".
    # dx1 = 512 - 350 = 162. dy1 = 288 - 140 = 148.
    # col = 7 % 5 = 2. row = 7 // 5 = 1.
    # bx = 162 + 30 + 2 * (120 + 12) = 192 + 264 = 456
    # by = 148 + 60 + 1 * (45 + 10) = 208 + 55 = 263
    redui.handle_touch(460, 270, pressed=True)
    assert camera.rcp.state[RcpProtocol.PARAM_ISO] == "1280"
    assert redui.active_dialog is None


def test_touch_fps_badge_opens_dialog(redui, camera):
    """Verify touching FPS badge (x=350, y=20) opens FPS dialog and allows selecting 60.00 fps."""
    redui.handle_touch(350, 20, pressed=True)
    assert redui.active_dialog == "FPS"

    # Select 60.00 FPS (index 7: col=1, row=2)
    # dx1 = 162, dy1 = 148. start_x = 192, start_y = 208.
    # col = 1, row = 2. bx = 192 + 1 * (200 + 12) = 404, by = 208 + 2 * 55 = 318
    redui.handle_touch(410, 325, pressed=True)
    assert camera.rcp.state[RcpProtocol.PARAM_FPS] == "60.00"
    assert redui.active_dialog is None


def test_menu_toggle_and_tabs(redui):
    """Verify toggling DSMC2 System Menu and navigating tabs."""
    assert redui.is_menu_open is False
    redui.toggle_menu()
    assert redui.is_menu_open is True

    # Touch OVERLAYS tab
    # my1 = 50. tab_y for OVERLAYS (index 3) = 50 + 60 + 3 * 46 = 248
    redui.handle_touch(120, 255, pressed=True)
    assert redui.menu_active_tab == "OVERLAYS"

    # Toggle False Color inside OVERLAYS tab
    # rx1 = 80 + 220 = 300, ry = 50 + 70 = 120
    assert redui.is_false_color_on is False
    redui.handle_touch(350, 130, pressed=True)
    assert redui.is_false_color_on is True


def test_physical_buttons_router(redui):
    """Verify physical buttons trigger tools and navigation."""
    redui.handle_button_press("1:1 MAG")
    assert redui.is_magnify_on is True

    redui.handle_button_press("USER 1")
    assert redui.is_false_color_on is True

    redui.handle_button_press("USER 2")
    assert redui.is_zebras_on is True

    redui.handle_button_press("MENU")
    assert redui.is_menu_open is True

    # Test D-pad down
    redui.handle_button_press("NAV DOWN")
    assert redui.menu_active_tab == "MEDIA"


def test_touch_reticle_feedback(redui):
    """Verify touch reticle is drawn when a touch occurs."""
    redui.handle_touch(500, 300, pressed=True)
    assert redui.last_touch_pos == (500, 300)
    assert redui.is_touching is True

    base = Image.new("RGB", (1024, 576), (0, 0, 0))
    rendered = redui.render(base)
    # The pixel at reticle ring (500 + 22, 300) should be colored (red outline)
    pixel = rendered.getpixel((522, 300))
    assert pixel[0] > 100  # Red channel active

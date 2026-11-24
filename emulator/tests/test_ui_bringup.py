"""
Milestone 1 (UI / driver bring-up) verification: drive real boot past the NAND-FS mount
and clear the board-bring-up barrier ``wait_for_init_flags(0x03300FF7)`` (@0x8066C7D8).

With peripheral-synchronised McSPI1-over-sDMA, GPIO-76/77 CS-mux routing, seeded LSB-FPGA
status/version registers on McSPI1 CH2, ``MaxFanController`` @ I2C1 0x4D + fan-tach spin-up
sleep compression, and the GPMC CS2 IOB/XAUI AHB window (``IobXauiAhbDevice`` backed by
``IobXauiWindow`` at 0x1C000000..0x1FFFFFFF), ``weapon_boards_init`` (@0x809B378C),
``weapon_left_side_board_init`` (@0x809B5354), and ``fan_init`` (@0x8067049C) all complete: all
four ``UI_BRINGUP_BOARD_BITS`` (Boards 0x400 | Fan 0x800 | XAUI 0x200000 | Mailbox 0x1000000) set
-> ``0xC3300FFF`` and ``hw_cap`` (@0x815811F4) is populated to ``0x00041182``. Milestone 2
(``boot_to_application``) then completes the full ``0xCF3CF7FF`` cascade into ``run_application``
and ``Camera State Manager``.
"""

import pytest
from emulator.camera import Dsmc2Camera
from conftest import requires_firmware

pytestmark = requires_firmware

FAN_BIT = 0x800


@pytest.fixture(scope="module")
def bringup():
    """Boot once through the board-bring-up barrier (deep boot; reused across assertions)."""
    cam = Dsmc2Camera(workspace_root=".")
    cam.load_firmware()
    res = cam.boot_to_ui_bringup()
    return cam, res


def test_boot_reaches_board_bringup_barrier(bringup):
    """Boot advances past the FS mount AND the whole board bring-up barrier (0xC3300FFF)."""
    cam, res = bringup
    assert res["init_event_flags"] == Dsmc2Camera.UI_BRINGUP_FLAGS_REACHABLE == 0xC3300FFF
    # The NAND-FS mount (milestone 3 depth) is retained -- no regression.
    assert res["init_event_flags"] & 0x8  # NAND FS
    assert res["init_event_flags"] & 0x7 == 0x7  # RTC|FileX|CRYPT
    # Boards + Fan + XAUI + Mailbox all complete -> hw_cap populated.
    assert cam.bus.read_u32(cam.fw(0x815811F4)) == 0x00041182


def test_board_bringup_completes_including_fan(bringup):
    """Boards/XAUI/Mailbox set over SPI-over-sDMA AND Fan 0x800 now sets via the fan-tach model."""
    _, res = bringup
    assert res["barrier_passed"] is True
    assert res["milestone"] == "ui_bringup"
    # The SPI-over-sDMA transport carried the board-init coprocessor reads (non-zero frame count).
    assert res["spi_over_sdma_frames"] > 0
    # Boards 0x400, XAUI 0x200000 and Mailbox 0x1000000 are set.
    assert res["init_event_flags"] & 0x400        # Boards
    assert res["init_event_flags"] & 0x200000     # XAUI
    assert res["init_event_flags"] & 0x1000000    # Mailbox
    # Fan 0x800 is now SET too -- no board-dependent bit remains blocked.
    assert res["init_event_flags"] & FAN_BIT      # Fan
    assert res["blocked_board_bits"] == 0
    assert res["missing_flags"] == 0
    assert res["fan_tach_modeled"] is True


def test_next_blocker_is_sata_media_storage(bringup):
    """With Fan modelled, the board barrier passes; the next blocker is the SATA/Media storage init."""
    _, res = bringup
    assert res["next_blocker"] is not None
    nb = res["next_blocker"]
    assert "0xC3300FFF" in nb
    # The board barrier (incl. Fan) is no longer the blocker; storage init is next.
    assert "SATA" in nb and "Media" in nb
    assert "0x0C000000" in nb


def test_xaui_link_model_is_installed(bringup):
    """The IOB/LSB-FPGA XAUI link-up register model is installed, and the IOB window was reached."""
    _, res = bringup
    assert res["xaui_link_modeled"] is True
    # The LSB worker ran its XAUI link check, so the IOB AHB window was polled this boot.
    assert res["xaui_window_reached"] is True


def test_driver_thread_advances_past_fan_barrier(bringup):
    """The Driver thread crossed the board barrier (incl. Fan); the UI app is not yet released."""
    _, res = bringup
    names = {t["name"]: t for t in res["threads"]}
    driver = names.get("Driver thread")
    assert driver is not None and driver["state"] in ("READY", "EVENT_FLAG")
    starter = names.get("Starter Thread")
    assert starter is not None and starter["state"] == "SEMAPHORE"


def test_boot_to_application_releases_starter_thread_and_csm(bringup):
    """Milestone 2: full 0xCF3CF7FF cascade completes Driver thread and spawns Camera State Manager."""
    cam, _ = bringup
    app_res = cam.boot_to_application(_already_at_ui_bringup=True)
    assert app_res["barrier_passed"] is True
    assert app_res["milestone"] == "application_running"
    assert (app_res["init_event_flags"] & Dsmc2Camera.APPLICATION_FLAGS_BARRIER) == Dsmc2Camera.APPLICATION_FLAGS_BARRIER
    assert app_res["init_event_flags"] == Dsmc2Camera.APPLICATION_FLAGS_REACHABLE
    assert app_res["driver_thread_completed"] is True
    assert app_res["app_semaphore_created"] is True
    assert app_res["camera_state_manager_spawned"] is True

    names = {t["name"]: t for t in app_res["threads"]}
    assert names["Driver thread"]["state"] == "COMPLETED"
    assert "Camera State Manager" in names
    assert cam.bus.read_u32(cam.fw(Dsmc2Camera.APP_SEMAPHORE_ADDR)) == Dsmc2Camera.SEMAPHORE_MAGIC

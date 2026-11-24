"""
Milestone 1 (UI / driver bring-up) verification: drive real boot past the NAND-FS mount
and clear the board-bring-up barrier ``wait_for_init_flags(0x03300FF7)`` (@0x8066C7D8).

With OMAP sDMA McSPI transfers, GPIO-76/77 CS-mux routing, seeded LSB-FPGA status/version
registers on McSPI1 CH2, and the GPMC CS2 IOB/XAUI AHB window (``IobXauiAhbDevice`` at
0x1C000000..0x1FFFFFFF), ``weapon_boards_init`` (@0x809B378C), ``weapon_left_side_board_init``
(@0x809B5354), and ``Fan Init`` (@0x809B2790) all complete when ``slice_size=12_000`` supplies
sufficient GPTimer2 ticks. All four ``UI_BRINGUP_BOARD_BITS`` (Boards 0x400 | Fan 0x800 |
XAUI 0x200000 | Mailbox 0x1000000) set -> ``0xC3300FFF`` and ``hw_cap`` (@0x815811F4) is populated
to ``0x00041182``.
"""

import pytest
from emulator.camera import Dsmc2Camera
from conftest import requires_firmware

pytestmark = requires_firmware


@pytest.fixture(scope="module")
def bringup():
    """Boot once through the board-bring-up barrier (deep boot; reused across assertions)."""
    cam = Dsmc2Camera(workspace_root=".")
    cam.load_firmware()
    res = cam.boot_to_ui_bringup()
    return cam, res


def test_boot_clears_board_bringup_barrier(bringup):
    """Boot passes the 0x03300FF7 board-bring-up barrier and retains NAND-FS + early init bits."""
    cam, res = bringup
    assert res["barrier_passed"] is True
    assert res["milestone"] == "ui_bringup"
    assert (res["init_event_flags"] & Dsmc2Camera.UI_BRINGUP_FLAGS_BARRIER) == Dsmc2Camera.UI_BRINGUP_FLAGS_BARRIER
    assert res["init_event_flags"] == Dsmc2Camera.UI_BRINGUP_FLAGS_REACHABLE
    # The NAND-FS mount (late-init depth) is retained -- no regression.
    assert res["init_event_flags"] & 0x8  # NAND FS
    assert res["init_event_flags"] & 0x7 == 0x7  # RTC|FileX|CRYPT
    # Boards + Fan + XAUI + Mailbox all complete -> hw_cap populated.
    assert cam.bus.read_u32(cam.fw(0x815811F4)) == 0x00041182


def test_board_bringup_bits_cleared(bringup):
    """All four UI_BRINGUP_BOARD_BITS (Boards|Fan|XAUI|Mailbox) are set with no blocker."""
    _, res = bringup
    assert res["missing_flags"] == 0
    assert res["blocked_board_bits"] == 0
    assert res["next_blocker"] is None


def test_driver_thread_past_barrier(bringup):
    """Driver thread is active past the board barrier; Starter Thread awaits full 0xCF3CF7FF cascade."""
    _, res = bringup
    names = {t["name"]: t for t in res["threads"]}
    driver = names.get("Driver thread")
    assert driver is not None and driver["state"] != "EVENT_FLAG"
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

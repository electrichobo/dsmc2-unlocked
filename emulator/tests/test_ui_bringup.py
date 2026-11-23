"""
Milestone 2 (UI / driver bring-up) depth probe: drive boot past the NAND-FS mount toward the
UI/DSS/RCP bring-up and pin down the next blocker.

After the filesystem mounts (milestone 3, init event flags ``0xC21000FF``) the Driver thread runs a
second ``init_parallel`` cascade and blocks on the board-bring-up barrier
``wait_for_init_flags(0x03300FF7)`` (@0x8066C7D8). Boot reaches ``0xC21003FF`` and then stalls on the
four board-dependent bits -- Boards 0x400, Fan 0x800, XAUI 0x200000, Mailbox 0x1000000 -- because the
Boards child ``weapon_boards_init`` (@0x809B378C) deadlocks: its six async board workers all run but
the LSB worker ``weapon_left_side_board_init`` (@0x809B5354) never completes, stuck in the LSB-FPGA /
IOB **XAUI link bring-up** (``FUN_809BCF60`` polling the XAUI PCS status it never sees go UP).

Past this barrier lie ``run_application`` -> Camera State Manager -> UI / DSS framebuffer / firmware
RCP server, all released only when the Driver thread finishes the cascade. Modelling the live XAUI
link-up is the IOB/LSB-FPGA coprocessor model (fidelity audit ASSERTED stub) -- the next milestone.

Source of truth: sdk findings/boot/BOOT_FLOW_MAP.md (init_parallel cascade + board barrier),
RED research/docs/build/VBRAIN_DEPTH_DESIGN.md (milestone 2). This test documents the reached depth
and the identified blocker so the regression is visible when the XAUI model lands.
"""

import pytest
from emulator.camera import Dsmc2Camera
from conftest import requires_firmware

pytestmark = requires_firmware


@pytest.fixture(scope="module")
def bringup():
    """Boot once to the board-bring-up barrier (deep boot; reused across assertions)."""
    cam = Dsmc2Camera(workspace_root=".")
    cam.load_firmware()
    return cam.boot_to_ui_bringup()


def test_boot_reaches_board_bringup_barrier(bringup):
    """Boot advances past the FS mount to the board-bring-up barrier, deterministically."""
    res = bringup
    # Everything the current peripheral models satisfy is set (RTC|FileX|CRYPT|NAND + driver
    # cascade + HAL/Power); only the XAUI-blocked board bits remain clear.
    assert res["init_event_flags"] == Dsmc2Camera.UI_BRINGUP_FLAGS_REACHABLE
    # The NAND-FS mount (milestone 3 depth) is retained -- no regression.
    assert res["init_event_flags"] & 0x8  # NAND FS
    assert res["init_event_flags"] & 0x7 == 0x7  # RTC|FileX|CRYPT


def test_next_blocker_is_lsb_fpga_xaui(bringup):
    """The barrier is blocked exactly on the four board-dependent bits (XAUI link bring-up)."""
    res = bringup
    assert res["barrier_passed"] is False
    assert res["milestone"] == "board_bringup_barrier"
    # Precisely Boards | Fan | XAUI | Mailbox -- all gated on the LSB XAUI link coming up.
    assert res["blocked_board_bits"] == Dsmc2Camera.UI_BRINGUP_BOARD_BITS
    assert res["missing_flags"] == Dsmc2Camera.UI_BRINGUP_BOARD_BITS
    assert res["next_blocker"] is not None
    assert "XAUI" in res["next_blocker"]


def test_driver_thread_still_blocked_before_run_application(bringup):
    """The Driver thread is event-flag blocked on the barrier; the UI app has not been released."""
    res = bringup
    names = {t["name"]: t for t in res["threads"]}
    driver = names.get("Driver thread")
    assert driver is not None and driver["state"] == "EVENT_FLAG"
    # The Starter Thread (thread_0_entry -> run_application) is still semaphore-blocked: the UI /
    # DSS / RCP app is released only when the whole driver cascade completes.
    starter = names.get("Starter Thread")
    assert starter is not None and starter["state"] == "SEMAPHORE"

"""
Milestone 2 (UI / driver bring-up) depth probe: drive boot past the NAND-FS mount toward the
UI/DSS/RCP bring-up and pin down the next blocker.

After the filesystem mounts (milestone 3, init event flags ``0xC21000FF``) the Driver thread runs a
second ``init_parallel`` cascade and blocks on the board-bring-up barrier
``wait_for_init_flags(0x03300FF7)`` (@0x8066C7D8). The board-dependent bits -- Boards 0x400,
Fan 0x800, XAUI 0x200000, Mailbox 0x1000000 -- are gated on the Boards child
``weapon_boards_init`` (@0x809B378C) and its downstream workers completing.

MILESTONE (this PR): the peripheral-synchronised **McSPI1-over-sDMA** coprocessor transport is now
modelled (``sdma.py``: a sDMA channel wired to a McSPI TX/RX FIFO clocks the SPI frame through the
``CoprocessorSpiTarget`` byte state machine, full-duplex, instead of a mem->mem copy). The six board
workers ``weapon_boards_init`` dispatches now run to completion: batch 1 (IOB/ISP-pre) no longer
strands on its SPI reads, batch 2 dispatches, the LSB worker ``weapon_left_side_board_init``
(@0x809B5354) runs, and its XAUI link check (``lsb_fpga_report_xaui_status`` @0x809BCF64 -> IOB AHB
window @0x1C000000 + ``fpga_read``/ts_fpga) passes against the installed link-up model
(``IobXauiWindow`` + ts_fpga seeds; verified in ``test_xaui_link.py``). The Boards, XAUI and Mailbox
bits all set and boot reaches ``0xC33007FF``.

The only board-dependent bit still clear is **Fan 0x800**: its worker ``fan_init`` (@0x8067049C) ->
``HAL_Temp_Fan_Control_Init`` (@0x809B09A8) runs but strands in the fan TACHOMETER speed test
(``detect_fan_type`` -> ``HAL_Temp_SpeedTest``), a timed fan-RPM measurement no peripheral model
drives. Past the barrier lie ``run_application`` -> Camera State Manager -> UI / DSS framebuffer /
firmware RCP server, released only when the Driver thread finishes the cascade. The fan /
tach-capture register model is the next depth milestone (NOT the SPI transport, NOT the IOB Xtensa).

Source of truth: sdk findings/boot/BOOT_FLOW_MAP.md (init_parallel cascade + board barrier),
clean/weapon_boards.c / weapon_left_side_board.c / lsb_fpga.c / spi.c / fpga_utils.c,
RED research/docs/build/vbrain_ui_bringup_findings.md (milestone 2). This test documents the reached
depth, the SPI-over-sDMA transport, the XAUI model, and the corrected next blocker (Fan tach).
"""

import pytest
from emulator.camera import Dsmc2Camera
from conftest import requires_firmware

pytestmark = requires_firmware

FAN_BIT = 0x800


@pytest.fixture(scope="module")
def bringup():
    """Boot once to the board-bring-up barrier (deep boot; reused across assertions)."""
    cam = Dsmc2Camera(workspace_root=".")
    cam.load_firmware()
    return cam.boot_to_ui_bringup()


def test_boot_reaches_board_bringup_barrier(bringup):
    """Boot advances past the FS mount AND the board bring-up to the Fan-tach barrier (0xC33007FF)."""
    res = bringup
    assert res["init_event_flags"] == Dsmc2Camera.UI_BRINGUP_FLAGS_REACHABLE == 0xC33007FF
    # The NAND-FS mount (milestone 3 depth) is retained -- no regression.
    assert res["init_event_flags"] & 0x8  # NAND FS
    assert res["init_event_flags"] & 0x7 == 0x7  # RTC|FileX|CRYPT


def test_board_bringup_completes_except_fan(bringup):
    """Boards/XAUI/Mailbox now set over the modelled SPI-over-sDMA transport; only Fan 0x800 clear."""
    res = bringup
    assert res["barrier_passed"] is False
    assert res["milestone"] == "board_bringup_barrier"
    # The SPI-over-sDMA transport carried the board-init coprocessor reads (non-zero frame count).
    assert res["spi_over_sdma_frames"] > 0
    # Boards 0x400, XAUI 0x200000 and Mailbox 0x1000000 are now SET (no longer blocked).
    assert res["init_event_flags"] & 0x400        # Boards
    assert res["init_event_flags"] & 0x200000     # XAUI
    assert res["init_event_flags"] & 0x1000000    # Mailbox
    # The only board-dependent bit still clear is Fan 0x800.
    assert res["blocked_board_bits"] == FAN_BIT
    assert res["missing_flags"] == FAN_BIT


def test_next_blocker_is_fan_tach(bringup):
    """The corrected next blocker is the fan tachometer speed test, not the SPI transport/XAUI."""
    res = bringup
    assert res["next_blocker"] is not None
    nb = res["next_blocker"]
    assert "Fan 0x800" in nb
    assert "HAL_Temp_Fan_Control_Init" in nb or "HAL_Temp_SpeedTest" in nb
    # Board bring-up / SPI transport is no longer the blocker.
    assert "0xC33007FF" in nb


def test_xaui_link_model_is_installed(bringup):
    """The IOB/LSB-FPGA XAUI link-up register model is installed, and the IOB window was reached."""
    res = bringup
    assert res["xaui_link_modeled"] is True
    # The LSB worker ran its XAUI link check, so the IOB AHB window was polled this boot.
    assert res["xaui_window_reached"] is True


def test_driver_thread_still_blocked_before_run_application(bringup):
    """The Driver thread is event-flag blocked on the (fan) barrier; the UI app is not yet released."""
    res = bringup
    names = {t["name"]: t for t in res["threads"]}
    driver = names.get("Driver thread")
    assert driver is not None and driver["state"] == "EVENT_FLAG"
    # The Starter Thread (thread_0_entry -> run_application) is still semaphore-blocked: the UI /
    # DSS / RCP app is released only when the whole driver cascade completes.
    starter = names.get("Starter Thread")
    assert starter is not None and starter["state"] == "SEMAPHORE"

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

The last board-dependent bit, **Fan 0x800**, is now modelled too (see ``test_fan_tach.py``): its
worker ``fan_init`` (@0x8067049C) -> ``HAL_Temp_Fan_Control_Init`` (@0x809B09A8) -> ``detect_fan_type``
(@0x809B08C4) -> ``HAL_Temp_SpeedTest`` (@0x809B061C) spins the BRAIN fan up and samples its
tachometer over I2C1 @ 0x4D (``MaxFanController``); with the fan-tach spin-up sleep compressed the
worker proceeds to the tach read, measures a valid RPM, and sets Fan 0x800. The board-bring-up
barrier therefore now PASSES and boot reaches ``0xC3300FFF``. The next blocker is the post-barrier
SATA / Media storage init (``boot_to_run_application`` / ``test_run_application.py``).

Source of truth: sdk findings/boot/BOOT_FLOW_MAP.md (init_parallel cascade + board barrier),
clean/weapon_boards.c / weapon_left_side_board.c / lsb_fpga.c / spi.c / fpga_utils.c,
temp_hal_weapon.c (fan HAL), RED research/docs/build/vbrain_ui_bringup_findings.md. This test
documents the reached depth, the SPI-over-sDMA transport, the XAUI model, the fan-tach model, and
the corrected next blocker (SATA/Media storage init).
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
    """Boot advances past the FS mount AND the whole board bring-up barrier (0xC3300FFF)."""
    res = bringup
    assert res["init_event_flags"] == Dsmc2Camera.UI_BRINGUP_FLAGS_REACHABLE == 0xC3300FFF
    # The NAND-FS mount (milestone 3 depth) is retained -- no regression.
    assert res["init_event_flags"] & 0x8  # NAND FS
    assert res["init_event_flags"] & 0x7 == 0x7  # RTC|FileX|CRYPT


def test_board_bringup_completes_including_fan(bringup):
    """Boards/XAUI/Mailbox set over SPI-over-sDMA AND Fan 0x800 now sets via the fan-tach model."""
    res = bringup
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
    res = bringup
    assert res["next_blocker"] is not None
    nb = res["next_blocker"]
    assert "0xC3300FFF" in nb
    # The board barrier (incl. Fan) is no longer the blocker; storage init is next.
    assert "SATA" in nb and "Media" in nb
    assert "0x0C000000" in nb


def test_xaui_link_model_is_installed(bringup):
    """The IOB/LSB-FPGA XAUI link-up register model is installed, and the IOB window was reached."""
    res = bringup
    assert res["xaui_link_modeled"] is True
    # The LSB worker ran its XAUI link check, so the IOB AHB window was polled this boot.
    assert res["xaui_window_reached"] is True


def test_driver_thread_advances_past_fan_barrier(bringup):
    """The Driver thread crossed the board barrier (incl. Fan); the UI app is not yet released.

    After the board barrier passes the Driver thread dispatches the SATA/Media init_parallel
    stages and blocks again on wait_for_init_flags(0x0C000000) for them (the new blocker), so it is
    still EVENT_FLAG. The Starter Thread (thread_0_entry -> run_application) therefore remains
    semaphore-blocked: the UI / DSS / RCP app is released only when the whole driver cascade
    completes.
    """
    res = bringup
    names = {t["name"]: t for t in res["threads"]}
    driver = names.get("Driver thread")
    # At the instant the barrier passes the Driver thread has just been readied by the Fan bit and
    # is mid-cascade (dispatching SATA/Media), so it is READY or already re-blocked (EVENT_FLAG) on
    # the storage wait -- either way it is past the fan barrier, not blocked on it.
    assert driver is not None and driver["state"] in ("READY", "EVENT_FLAG")
    # The UI app is still gated: run_application's Starter Thread remains semaphore-blocked until
    # the whole driver cascade (storage init included) completes.
    starter = names.get("Starter Thread")
    assert starter is not None and starter["state"] == "SEMAPHORE"

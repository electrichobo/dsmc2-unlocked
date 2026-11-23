"""
Milestone 2 (UI / driver bring-up) depth probe: drive boot past the NAND-FS mount toward the
UI/DSS/RCP bring-up and pin down the next blocker.

After the filesystem mounts (milestone 3, init event flags ``0xC21000FF``) the Driver thread runs a
second ``init_parallel`` cascade and blocks on the board-bring-up barrier
``wait_for_init_flags(0x03300FF7)`` (@0x8066C7D8). Boot reaches ``0xC21003FF`` and then stalls on the
four board-dependent bits -- Boards 0x400, Fan 0x800, XAUI 0x200000, Mailbox 0x1000000 -- because the
Boards child ``weapon_boards_init`` (@0x809B378C) never completes.

CORRECTED DIAGNOSIS (emulation, this PR): the stall is EARLIER than the LSB/XAUI worker. The six
board workers run as async ThreadX threads (``async_group_run_ext``); batch 1 (IOB/ISP-pre) is
entered but strands on its **McSPI1-over-sDMA coprocessor transfers** -- the peripheral-synchronised
SPI DMA completion semaphore is not posted by the current sDMA model -- so the Boards child parks on
the first ``_tx_event_flags_get`` and the LSB/XAUI worker ``weapon_left_side_board_init``
(@0x809B5358) is never reached. The IOB/LSB-FPGA **XAUI link-up register model** (``IobXauiWindow``
@0x1C000000 + ``ts_fpga`` link-up seeds) is installed and verified at the firmware's own poll
predicate (``lsb_fpga_report_xaui_status`` @0x809BCF64 reads the IOB AHB window at the polled
offsets; see ``test_xaui_link.py``); it carries the LSB worker to link-UP once the SPI-over-sDMA
transport completes. That transport (McSPI1-ch2 <-> ts_fpga over sDMA) is the next milestone.

Past this barrier lie ``run_application`` -> Camera State Manager -> UI / DSS framebuffer / firmware
RCP server, all released only when the Driver thread finishes the cascade.

Source of truth: sdk findings/boot/BOOT_FLOW_MAP.md (init_parallel cascade + board barrier),
clean/weapon_boards.c / weapon_left_side_board.c / lsb_fpga.c / spi.c,
RED research/docs/build/VBRAIN_DEPTH_DESIGN.md (milestone 2). This test documents the reached depth,
the installed XAUI model, and the corrected next blocker.
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


def test_next_blocker_is_board_worker_spi_dma(bringup):
    """The barrier is blocked on the four board-dependent bits; the corrected root cause is the
    async board workers stranding on their McSPI-over-sDMA transfers (not the XAUI predicate)."""
    res = bringup
    assert res["barrier_passed"] is False
    assert res["milestone"] == "board_bringup_barrier"
    # Precisely Boards | Fan | XAUI | Mailbox -- all gated on weapon_boards_init completing.
    assert res["blocked_board_bits"] == Dsmc2Camera.UI_BRINGUP_BOARD_BITS
    assert res["missing_flags"] == Dsmc2Camera.UI_BRINGUP_BOARD_BITS
    assert res["next_blocker"] is not None
    # Corrected diagnosis: the SPI-over-sDMA board-worker transport, with the XAUI model in place.
    assert "sDMA" in res["next_blocker"]
    assert "weapon_boards_init" in res["next_blocker"]


def test_xaui_link_model_is_installed(bringup):
    """The IOB/LSB-FPGA XAUI link-up register model is installed (and its next_blocker note says
    the XAUI link state is no longer the blocker)."""
    res = bringup
    assert res["xaui_link_modeled"] is True


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

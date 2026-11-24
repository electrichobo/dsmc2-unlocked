"""
Milestone (run_application / storage bring-up + DSS reach) depth probe: with the board barrier
crossed (Fan 0x800 modelled), drive boot across the SATA/Media storage-init barrier and pin the
next blocker on the DSS/display init batch.

Once the board-bring-up barrier passes (init event flags 0xC3300FFF), the Driver thread
(@0x8066C92C) dispatches two more ``init_parallel`` stages and then blocks waiting for them:

    init_parallel("SATA",  worker @0x80670504)   -> completion bit 0x04000000
    init_parallel("Media", worker @0x8067060C)   -> completion bit 0x08000000
    wait_for_init_flags(0x0C000000) @0x8066D000
        -> wait_for_init_flags @0x8066C7D8 -> red_event_flags_get @0x8070E748

Both now complete from the firmware's own code: the IOB-AHB SATA3 shadow-register block
(``IobXauiWindow``, devices/iob_xaui.py) presents an up FPGA/HBA link so ``sata_init`` posts
0x04000000, and the main media bay is modelled EMPTY (PxSSTS DET=0) so ``media_init`` takes its
"No media is present" success branch (@0x8067060C log 0xa18) and posts 0x08000000 -- without
emulating the AHCI IDENTIFY/command engine an attached SSD's SATA_DevAttach (@0x806985E4) needs.

Past the barrier the IOB/ISP binary download (0x1000) also completes, but the DSS/display + DSP/
audio init batch (0x000CE000: Monitor Pipeline 0x2000 @0x8074C9E4, HDMI 0x4000, HDSDI 0x8000,
DSP 0x40000 @0x80670FFC, Audio 0x80000) is not dispatched -- those init_parallel children are not
resumed by the current thread scheduler, so boot stalls at 0xCF301FFF with the Driver parked in
the ThreadX scheduler and the run_application ``Starter Thread`` (thread_0_entry @0x80008E24 ->
run_application @0x80008CB4 -> "Camera State Manager" / DSS framebuffer / RCP server) still
semaphore-blocked. The DSS/DSP display-init batch dispatch is the next depth milestone. This test
documents the crossed storage barrier and the pinned next (display-batch) blocker.
"""

import pytest
from emulator.camera import Dsmc2Camera
from conftest import requires_firmware

pytestmark = requires_firmware

SATA_BIT = 0x04000000
MEDIA_BIT = 0x08000000


@pytest.fixture(scope="module")
def runapp():
    cam = Dsmc2Camera(workspace_root=".")
    cam.load_firmware()
    return cam.boot_to_run_application()


def test_board_barrier_retained(runapp):
    """The board barrier (incl. Fan 0x800) remains passed while driving toward run_application."""
    res = runapp
    assert res["board_barrier_passed"] is True
    assert res["fan_bit_set"] is True
    assert res["init_event_flags"] & Dsmc2Camera.UI_BRINGUP_FLAGS_BARRIER == Dsmc2Camera.UI_BRINGUP_FLAGS_BARRIER


def test_storage_barrier_now_crosses(runapp):
    """The SATA (0x04000000) and Media (0x08000000) init bits now set from the firmware's own
    sata_init/media_init code (IOB-AHB SATA3 shadow-register model + empty-bay media path)."""
    res = runapp
    assert res["barrier_passed"] is True
    assert res["milestone"] == "run_application"
    assert res["sata_bit_set"] is True
    assert res["media_bit_set"] is True
    # The storage barrier 0x0C000000 is fully satisfied.
    assert res["missing_flags"] == 0
    assert res["init_event_flags"] & (SATA_BIT | MEDIA_BIT) == (SATA_BIT | MEDIA_BIT)


def test_iob_isp_download_completes(runapp):
    """Past the storage barrier the IOB/ISP binary-download stage (0x1000) also completes."""
    res = runapp
    assert res["iob_isp_download_bit_set"] is True
    assert res["init_event_flags"] & 0x1000 == 0x1000


def test_dss_display_batch_is_next_blocker(runapp):
    """The new blocker is the DSS/display + DSP/audio init batch (0x000CE000) not dispatching;
    run_application's Starter Thread stays semaphore-blocked behind the Driver's final wait."""
    res = runapp
    # Boot stalls at 0xCF301FFF: storage + IOB/ISP done, display/DSP batch still missing.
    assert res["display_bits_missing"] == 0x000CE000
    assert res["run_application_released"] is False
    names = {t["name"]: t for t in res["threads"]}
    starter = names.get("Starter Thread")
    assert starter is not None and starter["state"] == "SEMAPHORE"
    # The Camera State Manager (spawned by run_application) is not up yet.
    assert "Camera State Manager" not in names


def test_next_blocker_pins_dss_batch(runapp):
    """The reported next blocker names the DSS/display init batch with its firmware addresses."""
    res = runapp
    nb = res["next_blocker"]
    assert nb is not None
    assert "storage-init barrier crossed" in nb
    assert "Monitor Pipeline" in nb and "0x8074C9E4" in nb
    assert "0xCF3CF7FF" in nb
    assert "0x80008E24" in nb

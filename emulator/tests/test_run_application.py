"""
Milestone 3 (run_application / storage bring-up) depth probe: with the board barrier now crossed
(Fan 0x800 modelled), drive boot toward ``run_application`` and pin the next blocker.

Once the board-bring-up barrier passes (init event flags 0xC3300FFF), the Driver thread
(@0x8066C92C) dispatches two more ``init_parallel`` stages and then blocks waiting for them:

    init_parallel("SATA",  worker @0x80670504)   -> completion bit 0x04000000
    init_parallel("Media", worker @0x8067060C)   -> completion bit 0x08000000
    wait_for_init_flags(0x0C000000) @0x8066D000
        -> wait_for_init_flags @0x8066C7D8 -> red_event_flags_get @0x8070E748

(The requested 0x0C000000 and the caller 0x8066D000/0x8066C808 were captured from the running
firmware.) The UI ``Starter Thread`` (thread_0_entry @0x80008E24 -> run_application -> Camera State
Manager / DSS framebuffer / RCP server) stays semaphore-blocked until the Driver cascade finishes.

Under the current models the SATA/Media storage-init workers do not post their completion bits, so
0x04000000 / 0x08000000 stay clear: the SATA/AHCI + media storage bring-up is the next depth
milestone. This test documents the reached depth and the pinned next blocker.
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


def test_sata_media_init_is_next_blocker(runapp):
    """The SATA (0x04000000) and Media (0x08000000) init bits stay clear -- the new blocker."""
    res = runapp
    assert res["barrier_passed"] is False
    assert res["milestone"] == "storage_init_barrier"
    assert res["sata_bit_set"] is False
    assert res["media_bit_set"] is False
    # Both requested bits are reported missing.
    assert res["missing_flags"] == (SATA_BIT | MEDIA_BIT)


def test_run_application_not_yet_released(runapp):
    """run_application (the Starter Thread) is still semaphore-blocked behind the storage init."""
    res = runapp
    assert res["run_application_released"] is False
    names = {t["name"]: t for t in res["threads"]}
    starter = names.get("Starter Thread")
    assert starter is not None and starter["state"] == "SEMAPHORE"


def test_next_blocker_pins_sata_media(runapp):
    """The reported next blocker names the SATA/Media storage init with its firmware address."""
    res = runapp
    nb = res["next_blocker"]
    assert nb is not None
    assert "SATA" in nb and "Media" in nb
    assert "0x8066D000" in nb
    assert "0x0C000000" in nb

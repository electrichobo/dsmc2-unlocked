"""
Late driver-init bring-up: drive the ThreadX scheduler past the idle park (0x80E9C368).

``boot()`` retires a fixed instruction budget in coarse slices and delivers the GPTimer2
1000 Hz tick IRQ at most once per slice, so the kernel sees far too few ticks to ready the
threads blocked after ``SystemInitThread``. ``boot_to_late_init()`` keeps that cadence faithful
but fine-grained, so the scheduler advances normally into late driver init:
``SystemInitThread`` COMPLETED, the ``Driver thread`` (thread_DRIVER_entry @0x8066C92C) and
``Starter Thread`` spawned, and the CRYPT/RTC/FileX ``init_parallel`` children run with their
event-flag bits set (0x1|0x2|0x4) in the primary init event-flag group (@0x81E54834).

Source of truth: sdk findings/boot/BOOT_FLOW_MAP.md (driver-thread init_parallel sequence,
event-flag bits), reference GLOBALS.tsv (event-flag group address).
"""

import pytest
from emulator.camera import Dsmc2Camera
from conftest import requires_firmware

pytestmark = requires_firmware


def _boot_late(ws="."):
    cam = Dsmc2Camera(workspace_root=ws)
    cam.load_firmware()
    res = cam.boot_to_late_init()
    return cam, res


@pytest.fixture(scope="module")
def late():
    """Boot once past the idle park; reused across assertions (each boot is ~tens of seconds)."""
    return _boot_late()[1]


def test_boot_reaches_late_driver_init(late):
    """The scheduler advances out of the idle park into late driver init, deterministically."""
    res = late

    assert res["reached"] is True
    assert res["milestone"] == "late_driver_init"
    # CRYPT (0x4) + RTC (0x1) + FileX (0x2) parallel-init bits are set.
    assert res["init_event_flags"] & 0x7 == 0x7
    assert res["driver_thread_spawned"] is True
    # Real post-kernel progress, well past the bare idle park.
    assert res["late_init_instructions"] >= 20_000_000


def test_late_init_spawns_driver_and_starter_threads(late):
    """Threads that never exist at the bare idle park are created and SystemInitThread completes."""
    res = late
    names = {t["name"]: t for t in res["threads"]}

    assert "Driver thread" in names
    assert "Starter Thread" in names
    # SystemInitThread runs to completion (it is SEMAPHORE-blocked at the bare idle park).
    sys_init = names.get("SystemInitThread")
    assert sys_init is not None and sys_init["state"] == "COMPLETED"
    # The Driver thread is blocked in wait_for_init_flags on the NAND-FS barrier (next milestone).
    assert names["Driver thread"]["state"] == "EVENT_FLAG"


def test_late_init_is_deterministic():
    """Two runs reach the same event-flag state and thread set (no wall-clock dependence)."""
    _, a = _boot_late()
    _, b = _boot_late()
    assert a["init_event_flags"] == b["init_event_flags"]
    assert a["reached"] == b["reached"] is True
    assert sorted(t["name"] for t in a["threads"]) == sorted(t["name"] for t in b["threads"])


def test_init_event_flags_zero_before_kernel():
    """init_event_flags() reads 0 before the kernel populates the event-flag group."""
    cam = Dsmc2Camera(workspace_root=".")
    cam.load_firmware()
    assert cam.init_event_flags() == 0

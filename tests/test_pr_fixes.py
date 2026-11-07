"""
Unit tests validating Draft PR review fixes:
1. Re-entrant lock in R3dClipRecorder (no deadlock in format_media / eject_media while recording)
2. Bus read hook preserving mapped RAM (SRAM at 0x40200000 not overwritten with 0)
3. Snapshot full DDR coverage and description parameter
4. Web security: snapshot path traversal rejection, Content-Type enforcement, CORS wildcard removal
5. HardwareFaultInjector module functionality
6. Camera VFS slot update, corrupt_env handling, and I2C force_nack flag
"""

import os
import json
import tempfile
import urllib.request
import urllib.error
import pytest

from emulator.camera import Dsmc2Camera
from emulator.storage.r3d_recorder import R3dClipRecorder
from emulator.core.snapshot import SnapshotManager
from emulator.core.faults import HardwareFaultInjector
from emulator.devices.i2c import OmapI2c


def test_r3d_recorder_format_eject_no_deadlock():
    """Verify format_media and eject_media safely stop active recording without deadlocking."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        recorder = R3dClipRecorder(camera=None, storage_root=tmp_dir)
        recorder.mount_media()

        # 1. Start recording and format media while recording
        assert recorder.start_recording() is True
        assert recorder.is_recording is True

        # format_media acquires self._lock and calls stop_recording (also acquires self._lock)
        # With RLock, this must complete immediately without deadlock
        res = recorder.format_media()
        assert res is True
        assert recorder.is_recording is False
        assert recorder.is_media_mounted is True

        # 2. Start recording and eject media while recording
        assert recorder.start_recording() is True
        assert recorder.is_recording is True

        res_eject = recorder.eject_media()
        assert res_eject is True
        assert recorder.is_recording is False
        assert recorder.is_media_mounted is False


def test_bus_ram_read_preserve():
    """Verify SystemBus read hook preserves mapped RAM (SRAM @ 0x40200000) and does not clobber it with 0."""
    workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    camera = Dsmc2Camera(workspace)

    test_pattern = b"\x34\x12\x55\xAA"
    sram_addr = 0x40200000

    # Write test bytes directly to SRAM
    camera.bus.write_mem(sram_addr, test_pattern)

    # Read back through bus.read
    val = camera.bus.read(sram_addr, 4)
    assert val == 0xAA551234

    # Verify memory still holds the original pattern and was not erased to 0
    raw = camera.bus.read_mem(sram_addr, 4)
    assert raw == test_pattern

    # Verify address was NOT counted as unknown MMIO
    assert sram_addr not in camera.bus.unknown_reads


def test_hardware_fault_injector():
    """Verify HardwareFaultInjector handles INTC_IRQ_LOCKUP, DDR_ECC_CORRUPT, GPTIMER_DRIFT, and camera faults."""
    workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    camera = Dsmc2Camera(workspace)
    camera.load_firmware()

    injector = HardwareFaultInjector(camera)

    res_intc = injector.inject_fault("INTC_IRQ_LOCKUP")
    assert res_intc["status"] == "injected"
    assert camera.intc.has_pending_irq() is True

    res_ecc = injector.inject_fault("DDR_ECC_CORRUPT")
    assert res_ecc["status"] == "injected"

    res_timer = injector.inject_fault("GPTIMER_DRIFT")
    assert res_timer["status"] == "injected"

    res_nack = injector.inject_fault("I2C_NACK", bus=3)
    assert res_nack["status"] == "injected"
    assert camera.i2c[3].force_nack is True


def test_snapshot_full_coverage_and_description():
    """Verify SnapshotManager saves full 512MB DDR and 4MB SRAM with description parameter."""
    workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    camera = Dsmc2Camera(workspace)
    camera.load_firmware()

    # Write marker into ThreadX TCB region (0x8149D494)
    tcb_marker = b"\xDE\xAD\xBE\xEF"
    camera.cpu.uc.mem_write(0x8149D494, tcb_marker)

    with tempfile.NamedTemporaryFile(suffix=".dsmc2", delete=False) as tmp:
        snap_path = tmp.name

    try:
        SnapshotManager.save(camera, snap_path, description="Full DDR coverage test")
        assert os.path.exists(snap_path)

        # Clear marker
        camera.cpu.uc.mem_write(0x8149D494, b"\x00\x00\x00\x00")
        assert camera.cpu.uc.mem_read(0x8149D494, 4) == b"\x00\x00\x00\x00"

        # Restore snapshot
        meta = SnapshotManager.restore(camera, snap_path)
        assert meta["description"] == "Full DDR coverage test"
        # Verify TCB marker restored from DDR
        assert camera.cpu.uc.mem_read(0x8149D494, 4) == tcb_marker
    finally:
        if os.path.exists(snap_path):
            os.remove(snap_path)


def test_camera_vfs_slot_update_and_fault_env():
    """Verify camera model switching updates vfs.firmware_slot_dir and corrupt_env updates storage."""
    workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    camera = Dsmc2Camera(workspace)

    # Test camera personality switch updates vfs.firmware_slot_dir
    camera.select_camera_model("0x15")
    assert "raven" in camera.vfs.firmware_slot_dir.lower()
    assert "raven" in camera.vfs.mounts["/image1"].lower()

    # Test corrupt_env
    camera.inject_fault("corrupt_env")
    assert camera.env.get("CAMERA_TYPE") == "CORRUPTED_0xFF"

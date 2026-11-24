"""
Tests for Phase 3 and Phase 4: Dedicated Coprocessors (IOB0, IOB1, ISP1, ISP2),
TI TMS320C64x+ DSP Subsystem, C2C Transport, and Accessory Bus TLV Enumeration.
"""

import os
import json
import urllib.request
import threading
import pytest

from emulator.camera import Dsmc2Camera
from emulator.coprocessors.c2c import CoprocessorTransport, IobXauiAhbDevice
from emulator.coprocessors.xtensa import XtensaCore, Iob0Coprocessor, Iob1Coprocessor, IspCoprocessor
from emulator.coprocessors.dsp import (
    TiC64xDsp,
    DspLinkIpc,
    CImagingServer,
    RAudioServer,
    OmapMailbox,
    OmapIva2Mmu,
)
from emulator.devices.accessory_bus import AccessoryBus, ModuleDescriptor
from emulator.ui.web import WebStudio, ThreadedHTTPServer, create_handler, find_free_port
from conftest import requires_firmware


@pytest.fixture
def workspace_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture
def camera(workspace_root):
    cam = Dsmc2Camera(workspace_root)
    return cam


def test_xtensa_core_and_iob0_binaries(workspace_root):
    """Verify Xtensa coprocessor model loads genuine IOB0 binaries from firmware slot."""
    iob0 = Iob0Coprocessor()
    assert iob0.core_id == 0
    assert iob0.state == XtensaCore.STATE_UNINITIALIZED

    iram_path = os.path.join(workspace_root, "firmware_store", "unpacked", "scarlet_w_7_4_1", "slot", "iob0", "iram.bin")
    dram_path = os.path.join(workspace_root, "firmware_store", "unpacked", "scarlet_w_7_4_1", "slot", "iob0", "dram.bin")
    if not (os.path.exists(iram_path) and os.path.exists(dram_path)):
        pytest.skip("User-supplied IOB0 binaries (slot/iob0/iram.bin, dram.bin) not staged")

    success = iob0.load_binaries(iram_path, dram_path)
    assert success is True
    assert iob0.state == XtensaCore.STATE_READY
    assert iob0.pc == XtensaCore.IRAM_BASE

    # Step execution
    iob0.step(10)
    assert iob0.instructions_executed == 10
    assert iob0.cycles_executed == 20
    assert iob0.state == XtensaCore.STATE_RUNNING


def test_iob0_sata_and_accelerometer():
    """Verify IOB0 SATA 3.0 DMA accounting and STACCELERO SPI horizon tilt calculations."""
    iob0 = Iob0Coprocessor()

    # SATA 3.0 HBA
    assert iob0.sata_link_up is True
    assert iob0.sata_link_speed_gbps == 6.0
    iob0.record_frame_dma(8 * 1024 * 1024) # 8 MB frame
    assert iob0.sata_dma_frames_written == 1
    assert iob0.sata_bytes_transferred == 8 * 1024 * 1024

    # STACCELERO Accelerometer
    # Level camera: x=0, y=0, z=1.0
    iob0.set_accelerometer(0.0, 0.0, 1.0)
    pitch, roll, is_level = iob0.get_tilt_angles()
    assert pitch == 0.0
    assert roll == 0.0
    assert is_level is True

    # Tilted camera: roll = 15°
    iob0.set_accelerometer(-0.2588, 0.0, 0.9659)
    pitch, roll, is_level = iob0.get_tilt_angles()
    assert abs(roll - 15.0) < 0.5
    assert is_level is False


def test_iob1_black_shading_and_defects(workspace_root):
    """Verify IOB1 sensor calibration coprocessor and defect table processing."""
    iob1 = Iob1Coprocessor()
    iram_path = os.path.join(workspace_root, "firmware_store", "unpacked", "scarlet_w_7_4_1", "slot", "iob1", "iram.bin")
    dram_path = os.path.join(workspace_root, "firmware_store", "unpacked", "scarlet_w_7_4_1", "slot", "iob1", "dram.bin")
    if not (os.path.exists(iram_path) and os.path.exists(dram_path)):
        pytest.skip("User-supplied IOB1 binaries (slot/iob1/iram.bin, dram.bin) not staged")
    assert iob1.load_binaries(iram_path, dram_path) is True

    # Factory defect map
    assert len(iob1.factory_defects) == 3

    # Execute black shading calibration
    res = iob1.run_black_shading(sensor_temp_c=40.0, exposure="1/48")
    assert res["status"] == "CALIBRATED"
    assert res["target_temp_c"] == 40.0
    assert res["target_exposure"] == "1/48"
    assert res["defects_masked"] == 5 # 3 factory + 2 user
    assert len(iob1.user_defects) == 2

    # Process sensor frame defect replacement
    corrected = iob1.process_sensor_frame(5120, 2700)
    assert corrected == 5
    assert iob1.pixels_corrected_total == 5


def test_isp_wavelet_compression(workspace_root):
    """Verify ISP1/ISP2 REDCODE wavelet compression engine and DDR training."""
    isp = IspCoprocessor(core_id=2)
    iram_path = os.path.join(workspace_root, "firmware_store", "unpacked", "scarlet_w_7_4_1", "slot", "isp", "iram.bin")
    dram_path = os.path.join(workspace_root, "firmware_store", "unpacked", "scarlet_w_7_4_1", "slot", "isp", "dram.bin")
    if not (os.path.exists(iram_path) and os.path.exists(dram_path)):
        pytest.skip("User-supplied ISP binaries (slot/isp/iram.bin, dram.bin) not staged")
    assert isp.load_binaries(iram_path, dram_path) is True

    # DDR training status
    assert isp.ddr_training_passed is True

    # Simulate raw frame compression at 8:1
    raw_frame = b"\x80" * (1024 * 1024) # 1 MB raw Bayer data
    res = isp.compress_frame(raw_frame, ratio="8:1")
    assert res["ratio"] == "8:1"
    assert res["compressed_size_bytes"] == len(raw_frame) // 8
    assert "slice_crcs" in res
    assert len(res["slice_crcs"]) == 4 # G1, B, R, G2
    assert isp.frames_compressed == 1


def test_coprocessor_spi_register_frame():
    """
    Replay the firmware's own SPI register frame against the transport model
    (sdk findings/hardware/COPROCESSOR_TRANSPORT.md). WRITE is
    `[0x81][addr u32][data u32]` (9 bytes); READ is `[0x01][addr u32]` then poll
    ACK byte 0x1F and read back 4 little-endian data bytes.
    """
    c2c = CoprocessorTransport()

    # ISP1 = SPI CS-mux target 7, ISP2 = target 8 (FW-proven).
    assert c2c.spi_targets["ts_isp1"].spi_target_id == 7
    assert c2c.spi_targets["ts_isp2"].spi_target_id == 8

    # High-level primitive: write then read a register on ts_iob0.
    c2c.spi_write_reg("ts_iob0", 0x00001234, 0xDEADBEEF)
    assert c2c.spi_read_reg("ts_iob0", 0x00001234) == 0xDEADBEEF

    # Byte-exact frame replay: clock the real WRITE frame one byte at a time.
    tgt = c2c.spi_targets["ts_fpga"]
    tgt.reset_frame()
    addr, data = 0x44A00020, 0x00000001  # LSB-FPGA "go" register (real address)
    write_frame = bytes([0x81]) + addr.to_bytes(4, "little") + data.to_bytes(4, "little")
    assert len(write_frame) == 9
    for b in write_frame:
        assert tgt.transceive_byte(b) == 0x00  # target drives 0 during a write
    assert tgt.last_write == (addr, data)

    # Byte-exact READ frame: opcode + addr, then ACK 0x1F, then 4 data bytes.
    tgt.reset_frame()
    for b in bytes([0x01]) + addr.to_bytes(4, "little"):
        assert tgt.transceive_byte(b) == 0x00
    assert tgt.transceive_byte(0x00) == 0x1F  # ACK byte
    got = bytes(tgt.transceive_byte(0x00) for _ in range(4))
    assert int.from_bytes(got, "little") == data

    # LSB-FPGA version register is seeded at its real address 0xE0000908.
    assert c2c.spi_read_reg("ts_fpga", 0xE0000908) == 0x00000014

    # GPMC IOB mailbox is a SEPARATE device at 0xA5800000, not SPI.
    assert c2c.mailbox.BASE_ADDR == 0xA5800000


def test_mcspi_sdma_tx_rx_and_chctrl_subtransfers(camera):
    """Verify OMAP sDMA McSPI TX/RX byte-frame transfers and CHCTRL sub-transfer phase preservation."""
    mcspi1 = camera.mcspi[1]
    ch2_base = mcspi1.base_addr + mcspi1.CH_BASE + (2 * mcspi1.CH_STRIDE)
    ch0_dma = 0x48056000 + 0x80  # DMA4 channel 0 (TX)
    ch1_dma = 0x48056000 + 0xE0  # DMA4 channel 1 (RX)

    tx_cmd_buf = 0x40201000
    tx_zero_buf = 0x40201100
    rx_buf = 0x40201200
    camera.bus.write_mem(tx_zero_buf, b"\x00")

    # Configure McSPI1 CH2 for 8-bit TX+RX (WL=7, TRM=0).
    camera.bus.write_u32(ch2_base + 0x00, 7 << 7)

    for reg_addr, expected in (
        (0xE0000904, 0x00030031),
        (0xE0000908, 0x00000014),
        (0x40010008, 0xC03007E0),
    ):
        camera.c2c.spi_targets["ts_fpga"].reset_frame()
        camera.bus.write_mem(tx_cmd_buf, bytes([0x01]) + reg_addr.to_bytes(4, "little"))

        # Sub-transfer 1: TX 5-byte READ command (linear src_amode=1), leaving MISO 0x00s in RX FIFO.
        camera.bus.write_u32(ch2_base + 0x08, 1)  # CHCTRL EN=1
        camera.bus.write_u32(ch0_dma + 0x10, 0)  # CSDP: 8-bit element
        camera.bus.write_u32(ch0_dma + 0x14, 5)  # CEN
        camera.bus.write_u32(ch0_dma + 0x18, 1)  # CFN
        camera.bus.write_u32(ch0_dma + 0x1C, tx_cmd_buf)
        camera.bus.write_u32(ch0_dma + 0x20, ch2_base + 0x0C)  # MCSPI_TX2
        camera.bus.write_u32(ch0_dma + 0x00, (0x01 << 12) | 0x80)  # CCR EN=1, src_amode=1

        # Toggle CHCTRL EN (matches FUN_806D588C between sub-transfers): flushes RX FIFO, keeps frame state.
        camera.bus.write_u32(ch2_base + 0x08, 0)
        camera.bus.write_u32(ch2_base + 0x08, 1)

        # Sub-transfer 2: poll ACK byte 0x1F (constant src_amode=0 zero TX + 1B RX DMA).
        camera.bus.write_u32(ch0_dma + 0x14, 1)
        camera.bus.write_u32(ch0_dma + 0x1C, tx_zero_buf)
        camera.bus.write_u32(ch0_dma + 0x20, ch2_base + 0x0C)
        camera.bus.write_u32(ch0_dma + 0x00, 0x80)  # constant src_amode=0

        camera.bus.write_u32(ch1_dma + 0x10, 0)
        camera.bus.write_u32(ch1_dma + 0x14, 1)
        camera.bus.write_u32(ch1_dma + 0x18, 1)
        camera.bus.write_u32(ch1_dma + 0x1C, ch2_base + 0x10)  # MCSPI_RX2
        camera.bus.write_u32(ch1_dma + 0x20, rx_buf)
        camera.bus.write_u32(ch1_dma + 0x00, 0x80)
        assert camera.bus.read_mem(rx_buf, 1)[0] == 0x1F

        camera.bus.write_u32(ch2_base + 0x08, 0)
        camera.bus.write_u32(ch2_base + 0x08, 1)

        # Sub-transfer 3: read 4-byte little-endian register value.
        camera.bus.write_u32(ch0_dma + 0x14, 4)
        camera.bus.write_u32(ch0_dma + 0x1C, tx_zero_buf)
        camera.bus.write_u32(ch0_dma + 0x20, ch2_base + 0x0C)
        camera.bus.write_u32(ch0_dma + 0x00, 0x80)

        camera.bus.write_u32(ch1_dma + 0x14, 4)
        camera.bus.write_u32(ch1_dma + 0x1C, ch2_base + 0x10)
        camera.bus.write_u32(ch1_dma + 0x20, rx_buf + 4)
        camera.bus.write_u32(ch1_dma + 0x00, 0x80)
        assert int.from_bytes(camera.bus.read_mem(rx_buf + 4, 4), "little") == expected


def test_cs_mux_spi_target_gpio_routing(camera):
    """Verify GPIO 76/77 CS-mux routes McSPI1 CH0 frames between ts_isp1 and ts_isp2."""
    mcspi1 = camera.mcspi[1]
    ch0_base = mcspi1.base_addr + mcspi1.CH_BASE
    gpio3_dataout = 0x49052000 + 0x3C

    # 8-bit word length on McSPI1 CH0.
    camera.bus.write_u32(ch0_base + 0x00, 7 << 7)

    # GPIO 76=0, GPIO 77=1 (bank 3 pins 12, 13) -> ts_isp1 (target 7).
    camera.bus.write_u32(gpio3_dataout, 1 << 13)
    for b in bytes([0x81]) + (0x12340000).to_bytes(4, "little") + (0x11223344).to_bytes(4, "little"):
        camera.bus.write_u32(ch0_base + 0x0C, b)
    assert camera.c2c.spi_targets["ts_isp1"].reg_get(0x12340000) == 0x11223344
    assert camera.c2c.spi_targets["ts_isp2"].reg_get(0x12340000) != 0x11223344

    # GPIO 76=1, GPIO 77=0 -> ts_isp2 (target 8).
    camera.bus.write_u32(gpio3_dataout, 1 << 12)
    for b in bytes([0x81]) + (0x12340004).to_bytes(4, "little") + (0x55667788).to_bytes(4, "little"):
        camera.bus.write_u32(ch0_base + 0x0C, b)
    assert camera.c2c.spi_targets["ts_isp2"].reg_get(0x12340004) == 0x55667788
    assert camera.c2c.spi_targets["ts_isp1"].reg_get(0x12340004) != 0x55667788


def test_iob_xaui_ahb_device_window(camera):
    """Verify IobXauiAhbDevice (0x1C000000..0x1FFFFFFF) serves XAUI link and IOB status handshake."""
    assert camera.bus.read_u32(0x1C02002C) == 0x03FFC02F
    assert camera.bus.read_u32(0x1C020010) == 0x00200000
    assert (camera.bus.read_u32(0x1C020248) & (1 << 20)) != 0
    assert camera.bus.read_u32(0x1C020068) == 0
    assert camera.bus.read_u32(0x1C0200BC) == 0

    # +0xB0020 returns ready status bit 2 (0x4)
    assert camera.bus.read_u32(0x1C0B0020) == 0x00000004
    # +0xB0000..+0xB0FFF toggles 0xFFFFFFFF / 0x00000000 for wait_for_iob_status_set/clear
    assert camera.bus.read_u32(0x1C0B0018) == 0xFFFFFFFF
    assert camera.bus.read_u32(0x1C0B0018) == 0x00000000
    assert camera.bus.read_u32(0x1C0B001C) == 0xFFFFFFFF
    assert camera.bus.read_u32(0x1C0B001C) == 0x00000000

    # With XAUI link down, IOB status window reports 0
    down_dev = IobXauiAhbDevice(xaui_link_up=False)
    assert down_dev.read(0x000B0018, 4) == 0x00000000


def test_omap_mailbox_and_iva2_mmu_soft_reset(camera):
    """Verify OmapMailbox (0x48094000) and OmapIva2Mmu (0x5D000000) soft-reset & TLB/FIFO paths."""
    # OMAP3630 Inter-Processor Mailbox (0x48094000)
    mb_base = OmapMailbox.BASE_ADDR
    assert camera.bus.read_u32(mb_base + OmapMailbox.REG_SYSSTATUS) & 0x1 == 0x1
    camera.bus.write_u32(mb_base + OmapMailbox.REG_SYSCONFIG, 0x2)  # SOFTRESET
    assert camera.bus.read_u32(mb_base + OmapMailbox.REG_SYSSTATUS) & 0x1 == 0x1
    camera.bus.write_u32(mb_base + 0x40, 0xCAFEBABE)
    assert camera.bus.read_u32(mb_base + 0xC0) == 1
    assert camera.bus.read_u32(mb_base + 0x40) == 0xCAFEBABE

    # IVA2.2 MMU (0x5D000000) + IVA2.2 SRAM/IRAM window (0x5C000000..0x5CFFFFFF)
    mmu_base = OmapIva2Mmu.BASE_ADDR
    camera.bus.write_u32(mmu_base + OmapIva2Mmu.REG_SYSCONFIG, 0x2)  # SOFTRESET
    assert camera.bus.read_u32(mmu_base + OmapIva2Mmu.REG_SYSSTATUS) & 0x1 == 0x1
    camera.bus.write_u32(mmu_base + OmapIva2Mmu.REG_TTB, 0x89E00000)
    assert camera.bus.read_u32(mmu_base + OmapIva2Mmu.REG_TTB) == 0x89E00000
    camera.bus.write_u32(0x5C7F8000, 0x12345678)
    assert camera.bus.read_u32(0x5C7F8000) == 0x12345678


def test_omap_i2c_multi_chunk_master_transmit(camera):
    """Verify OmapI2c completes >64-byte Master Transmit via clamped BUF/BUFSTAT and XRDY chunks."""
    i2c3 = camera.i2c[3]
    captured = bytearray()

    class BulkSink:
        def __init__(self, address: int):
            self.address = address

        def read(self, reg_addr: int, length: int) -> bytes:
            return b"\x00" * length

        def write(self, reg_addr: int, data: bytes) -> bool:
            captured.append(reg_addr)
            captured.extend(data)
            return True

    i2c3.attach_slave(BulkSink(0x3B))
    total_len = 130  # > 64B so tx_stat clamps to 0x3F initially
    base = 0x48060000
    camera.bus.write_u32(base + 0x2C, 0x3B)        # SA
    camera.bus.write_u32(base + 0x18, total_len)   # CNT
    camera.bus.write_u32(base + 0x14, 0x0404)      # BUF
    camera.bus.write_u32(base + 0x04, 0x4017)      # IE: XDR|XRDY|ARDY|NACK|AL
    camera.bus.write_u32(base + 0x24, 0x8603)      # CON: EN|MST|TRX|STP|STT

    # Initially tx_remaining=130 -> tx_stat clamped to 0x3F (63)
    assert (camera.bus.read_u32(base + 0x40) & 0x3F) == 0x3F
    assert (camera.bus.read_u32(base + 0x08) & 0x10) != 0  # XRDY asserted

    payload = bytes((i & 0xFF) for i in range(total_len))
    idx = 0
    while idx < total_len:
        stat = camera.bus.read_u32(base + 0x08)
        assert stat & 0x10
        chunk = min(64, total_len - idx)
        for _ in range(chunk):
            camera.bus.write_u32(base + 0x1C, payload[idx])
            idx += 1
        camera.bus.write_u32(base + 0x08, 0x10)  # Clear XRDY; re-asserts until cnt reached

    stat = camera.bus.read_u32(base + 0x08)
    assert (stat & 0x04) != 0  # ARDY set on completion
    assert (stat & 0x10) == 0  # XRDY cleared
    assert bytes(captured) == payload


def test_dsp_coff_parser_and_dsplink(workspace_root):
    """Verify TI TMS320C64x+ DSP parses rdc_dsp_app.out COFF binary and DSPLink IPC queues."""
    dsp = TiC64xDsp()
    coff_path = os.path.join(workspace_root, "firmware_store", "unpacked", "scarlet_w_7_4_1", "slot", "dsp", "rdc_dsp_app.out")
    if not os.path.exists(coff_path):
        pytest.skip("User-supplied DSP COFF binary (slot/dsp/rdc_dsp_app.out) not staged")

    success = dsp.load_coff(coff_path)
    assert success is True
    assert dsp.is_loaded is True
    assert dsp.dsp_state == "RUNNING"
    assert dsp.symbols_count > 0
    assert len(dsp.sections) > 0

    # DSPLink IPC
    dsplink = DspLinkIpc(dsp)
    dsplink.send_to_dsp(0x10, {"cmd": "INIT"})
    assert dsplink.messages_exchanged == 1
    assert dsp.bios_tick_count > 0


def test_dsp_imaging_and_audio_servers():
    """Verify CImagingServer (Autofocus contrast & Haar face detection) and RAudioServer."""
    dsp = TiC64xDsp()
    dsplink = DspLinkIpc(dsp)
    imaging = CImagingServer(dsplink)
    audio = RAudioServer(dsplink)

    # 1. Autofocus contrast calculation
    score_near = imaging.compute_focus_contrast((400, 200, 600, 400), focus_distance_m=2.5)
    score_far = imaging.compute_focus_contrast((400, 200, 600, 400), focus_distance_m=10.0)
    assert score_near > score_far
    assert score_near > 90.0

    # 2. Haar Face Detection
    faces = imaging.detect_faces(1024, 576)
    assert len(faces) >= 1
    assert faces[0]["confidence"] > 0.9

    # 3. Audio Server 4-Channel Peak Metering
    audio.set_preamp_gain(0, 18.0)
    audio.set_preamp_gain(1, 18.0)
    audio.process_audio_frame(-24.0, -25.0)
    assert audio.peak_dbfs[0] == -6.0
    assert audio.peak_dbfs[1] == -7.0
    assert audio.is_clipping[0] is False


def test_accessory_bus_tlv_enumeration():
    """Verify ModuleDescriptor binary TLV serialization and AccessoryBus enumeration."""
    # 1. Binary TLV Serialization
    desc = ModuleDescriptor("DSMC2 Base Expander", "720-0033", 104921, max_power_w=12)
    data = desc.serialize()
    assert data[:7] == b"REDMOD\x01"

    # Base64 UART transmission
    b64_str = desc.to_base64()
    decoded_desc = ModuleDescriptor.from_base64(b64_str)
    assert decoded_desc is not None
    assert decoded_desc.name == "DSMC2 Base Expander"
    assert decoded_desc.pin == "720-0033"
    assert decoded_desc.serial == 104921
    assert decoded_desc.max_power_w == 12

    # 2. Accessory Bus
    bus = AccessoryBus()
    assert "720-0033" in bus.attached_modules
    assert "730-0024" in bus.attached_modules # RED Touch 7.0
    assert "750-0075" in bus.attached_modules # MINI-MAG 512GB

    # UART Protocol Handshake: WHO, VERSION, REDINFO
    assert bus.handle_uart_handshake("WHO") == "BASE_EXPANDER"
    assert "7.4.1" in bus.handle_uart_handshake("VERSION")
    assert bus.handle_uart_handshake("REDINFO") != "NONE"

    # Hot-plug attach REDVOLT Expander
    res = bus.attach_module("720-0034", serial=501234)
    assert res is not None
    assert res["name"] == "DSMC2 REDVOLT Expander"
    assert "720-0034" in bus.attached_modules

    # Detach module
    detached = bus.detach_module("720-0034")
    assert detached is True
    assert "720-0034" not in bus.attached_modules


def test_camera_coprocessors_and_dsp_integration(camera):
    """Verify camera initializes and orchestrates all coprocessors, DSP, and accessories."""
    assert hasattr(camera, "c2c")
    assert hasattr(camera, "iob0")
    assert hasattr(camera, "iob1")
    assert hasattr(camera, "isp1")
    assert hasattr(camera, "isp2")
    assert hasattr(camera, "dsp")
    assert hasattr(camera, "imaging_server")
    assert hasattr(camera, "audio_server")
    assert hasattr(camera, "accessory_bus")

    slot = camera.firmware_slot_dir
    required_binaries = [
        os.path.join(slot, "dsmc_program.mem"),
        os.path.join(slot, "iob0", "iram.bin"),
        os.path.join(slot, "iob1", "iram.bin"),
        os.path.join(slot, "dsp", "rdc_dsp_app.out"),
    ]
    if not all(os.path.exists(p) for p in required_binaries):
        pytest.skip("User-supplied coprocessor/DSP binaries not staged in firmware slot")

    # Camera power on loads and boots coprocessors
    camera.power_on()
    assert camera.iob0.state == XtensaCore.STATE_READY
    assert camera.iob1.state == XtensaCore.STATE_READY
    assert camera.dsp.is_loaded is True

    # Camera helper methods
    shading_res = camera.run_black_shading()
    assert shading_res["status"] == "CALIBRATED"

    faces = camera.detect_faces()
    assert len(faces) >= 1

    contrast = camera.compute_focus_contrast()
    assert contrast > 50.0

    # Attached modules
    status = camera.accessory_bus.get_status()
    assert status["count"] >= 3


@requires_firmware
def test_web_studio_coprocessors_endpoints(workspace_root):
    """Verify Web Studio REST API handles coprocessors, calibration, and accessories."""
    test_port = find_free_port(9100)
    studio = WebStudio(workspace_root, host="127.0.0.1", port=test_port)
    studio.start_camera()

    handler = create_handler(studio)
    server = ThreadedHTTPServer(("127.0.0.1", test_port), handler)
    srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
    srv_thread.start()

    base_url = f"http://127.0.0.1:{test_port}"

    try:
        # 1. GET /api/status telemetry contains coprocessors and accessories
        resp = urllib.request.urlopen(f"{base_url}/api/status")
        assert resp.status == 200
        status = json.loads(resp.read().decode("utf-8"))
        assert "coprocessors" in status
        assert "accessories" in status
        assert status["coprocessors"]["iob0"]["sata"]["speed_gbps"] == 6.0
        assert status["accessories"]["count"] >= 3

        # 2. POST /api/hardware/calibrate
        req = urllib.request.Request(f"{base_url}/api/hardware/calibrate", data=b"{}", headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req)
        assert resp.status == 200
        cal_data = json.loads(resp.read().decode("utf-8"))
        assert cal_data["status"] == "CALIBRATED"

        # 3. POST /api/hardware/face_detect
        req = urllib.request.Request(f"{base_url}/api/hardware/face_detect", data=b"{}", headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req)
        assert resp.status == 200
        face_data = json.loads(resp.read().decode("utf-8"))
        assert len(face_data["faces"]) >= 1

        # 4. POST /api/hardware/accessory/attach
        req = urllib.request.Request(
            f"{base_url}/api/hardware/accessory/attach",
            data=json.dumps({"pin": "720-0035"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        resp = urllib.request.urlopen(req)
        assert resp.status == 200
        attach_data = json.loads(resp.read().decode("utf-8"))
        assert attach_data["status"] == "ok"
        assert attach_data["module"]["name"] == "DSMC2 Jetpack-SDI Expander"

        # 5. POST /api/hardware/accessory/detach
        req = urllib.request.Request(
            f"{base_url}/api/hardware/accessory/detach",
            data=json.dumps({"pin": "720-0035"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        resp = urllib.request.urlopen(req)
        assert resp.status == 200
        detach_data = json.loads(resp.read().decode("utf-8"))
        assert detach_data["detached"] is True

    finally:
        server.shutdown()
        studio.stop()

"""
Unit tests for OMAP3630 System DMA (sDMA) controller model.
"""

import struct

import pytest
import unicorn

from emulator.devices.sdma import OmapSdma
from emulator.devices.intc import OmapIntc
from emulator.devices.mcspi import OmapMcspi, MCSPI_MAP
from emulator.coprocessors.c2c import CoprocessorTransport


def test_sdma_reset_and_revision():
    """Verify revision and reset status."""
    sdma = OmapSdma()
    assert sdma.read(OmapSdma.REG_REVISION, 4) == 0x40
    assert (sdma.read(OmapSdma.REG_SYSSTATUS, 4) & 0x01) == 0x01

    # Soft reset
    sdma.write(OmapSdma.REG_OCP_SYSCONFIG, 4, 0x02)
    assert (sdma.read(OmapSdma.REG_SYSSTATUS, 4) & 0x01) == 0x01


def test_sdma_channel_registers():
    """Verify read/write access to DMA channel registers."""
    sdma = OmapSdma()
    ch0_base = OmapSdma.CHANNEL_BASE # 0x80
    ch1_base = OmapSdma.CHANNEL_BASE + OmapSdma.CHANNEL_STRIDE # 0x80 + 0x60 = 0xE0

    # Write source / destination to channel 0
    sdma.write(ch0_base + 0x18, 4, 0x80000000) # CSSA
    sdma.write(ch0_base + 0x1C, 4, 0x81000000) # CDSA
    assert sdma.read(ch0_base + 0x18, 4) == 0x80000000
    assert sdma.read(ch0_base + 0x1C, 4) == 0x81000000

    # Channel 1 should still be 0
    assert sdma.read(ch1_base + 0x18, 4) == 0


def test_sdma_irq_line3_and_channel_completion():
    """Verify DMA completion triggers Line 3 IRQ 15 when enabled."""
    intc = OmapIntc()
    intc.write(0x88, 4, 1 << 15) # Unmask IRQ 15 in MIR_CLEAR0

    sdma = OmapSdma(intc=intc)

    # Enable Line 3 interrupt for Channel 0 (bit 0 = 1)
    sdma.write(OmapSdma.REG_IRQENABLE_L3, 4, 0x01)
    assert sdma.read(OmapSdma.REG_IRQENABLE_L3, 4) == 0x01

    # Trigger Channel 0 enable (CCR bit 7 = 0x80)
    ch0_ccr = OmapSdma.CHANNEL_BASE + 0x00
    ch0_csr = OmapSdma.CHANNEL_BASE + 0x0C
    sdma.write(ch0_ccr, 4, 0x80)

    # Channel CSR bit 5 (BLOCK_DONE = 0x20) should be set
    assert (sdma.read(ch0_csr, 4) & 0x20) == 0x20

    # IRQSTATUS_L3 should have bit 0 set
    assert (sdma.read(OmapSdma.REG_IRQSTATUS_L3, 4) & 0x01) == 0x01

    # INTC should have pending IRQ 15
    assert intc.has_pending_irq()
    assert intc.get_active_irq() == 15

    # Write-1-to-clear IRQSTATUS_L3 bit 0
    sdma.write(OmapSdma.REG_IRQSTATUS_L3, 4, 0x01)
    assert (sdma.read(OmapSdma.REG_IRQSTATUS_L3, 4) & 0x01) == 0
    assert not intc.has_pending_irq()


# --- peripheral-synchronised SPI-over-sDMA (the board-init coprocessor transport) --------------

class _FakeCpu:
    """Minimal CPU with a Unicorn memory space for DMA buffer read/write."""

    def __init__(self):
        self.mu = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_ARM)
        self.mu.mem_map(0x80000000, 0x1000000)
        self.pc = 0


def _ch_base(ch):
    return OmapSdma.CHANNEL_BASE + ch * OmapSdma.CHANNEL_STRIDE


def _prog_channel(sdma, ch, cssa, cdsa, n_bytes):
    """Program a byte-element channel: CSSA @0x1C, CDSA @0x20, CEN @0x14, CFN @0x18, CSDP @0x10."""
    base = _ch_base(ch)
    sdma.write(base + 0x10, 4, 0)       # CSDP: elem = byte
    sdma.write(base + 0x14, 4, n_bytes)  # CEN
    sdma.write(base + 0x18, 4, 1)        # CFN
    sdma.write(base + 0x1C, 4, cssa)     # CSSA (source)
    sdma.write(base + 0x20, 4, cdsa)     # CDSA (dest)


def _make_spi_dma_rig():
    cpu = _FakeCpu()
    mcspi = {pid: OmapMcspi(pid, base, irq) for pid, (base, irq) in MCSPI_MAP.items()}
    c2c = CoprocessorTransport(mcspi=mcspi)
    sdma = OmapSdma(cpu=cpu, mcspi=mcspi)
    sdma.bind_mcspi(mcspi)
    return cpu, mcspi, c2c, sdma


def test_spi_over_sdma_clocks_read_frame_through_target():
    """A McSPI-FIFO-wired sDMA channel clocks the FW read frame [0x01][addr], then ACK 0x1F, 4B.

    This is the firmware's fpga_read(6, addr) -> spi_write_wait_for_ack_then_read (@0x806D5DE4)
    transport, which the McSPI driver runs as a sequence of full-duplex `_spi_write_and_read`
    (@0x806D588C) calls, each a TX sDMA channel into MCSPI_TXn paired with an RX channel out of
    MCSPI_RXn over the SAME beats (SPI is full-duplex):

      1. write-only: TX [0x01][addr LE] (5 bytes), no RX drain
      2. ACK poll:   TX 1 dummy byte + RX 1 byte  -> ACK 0x1F
      3. data read:  TX 4 dummy bytes + RX 4 bytes -> the little-endian register value

    The target is clocked exactly once per beat (on TX); the RX channel drains the MISO captured on
    the paired TX beats, so the CoprocessorSpiTarget frame stays in sync.
    """
    cpu, mcspi, c2c, sdma = _make_spi_dma_rig()
    tgt = c2c.spi_targets["ts_fpga"]  # McSPI port 1, channel 2
    # ts_fpga is seeded 0x40010008 -> 0x7E0 (XAUI link-up PCS, (v>>3)&0xFF == 0xFC).
    assert tgt.reg_get(0x40010008) == 0x7E0

    mcspi1 = mcspi[1]
    tx_fifo = mcspi1.tx_fifo_addr(2)
    rx_fifo = mcspi1.rx_fifo_addr(2)

    def tx(frame_bytes):
        cpu.mu.mem_write(0x80100000, frame_bytes)
        _prog_channel(sdma, 0, 0x80100000, tx_fifo, len(frame_bytes))
        sdma.write(_ch_base(0) + 0x00, 4, 0x80)

    def rx(nbytes):
        _prog_channel(sdma, 1, rx_fifo, 0x80101000, nbytes)
        sdma.write(_ch_base(1) + 0x00, 4, 0x80)
        return bytes(cpu.mu.mem_read(0x80101000, nbytes))

    # 1. write-only request frame
    tx(bytes([0x01]) + (0x40010008).to_bytes(4, "little"))
    # 2. ACK poll (1 full-duplex beat)
    tx(b"\x00")
    ack = rx(1)
    assert ack[0] == 0x1F, f"ACK 0x1F not clocked, got {ack.hex()}"
    # 3. data read (4 full-duplex beats)
    tx(b"\x00\x00\x00\x00")
    data = rx(4)
    assert struct.unpack("<I", data)[0] == 0x7E0, f"reg value wrong: {data.hex()}"


def test_spi_over_sdma_write_frame_commits_register():
    """A TX-only SPI DMA ([0x81][addr][data]) commits the register in the target (fpga_write)."""
    cpu, mcspi, c2c, sdma = _make_spi_dma_rig()
    tgt = c2c.spi_targets["ts_fpga"]
    mcspi1 = mcspi[1]
    tx_fifo = mcspi1.tx_fifo_addr(2)

    frame = bytes([0x81]) + (0x44A00100).to_bytes(4, "little") + (0xCAFEBABE).to_bytes(4, "little")
    cpu.mu.mem_write(0x80100000, frame)
    _prog_channel(sdma, 0, 0x80100000, tx_fifo, len(frame))
    sdma.write(_ch_base(0) + 0x00, 4, 0x80)
    assert sdma.spi_dma_transfers == 1
    assert tgt.reg_get(0x44A00100) == 0xCAFEBABE
    assert tgt.last_write == (0x44A00100, 0xCAFEBABE)


def test_spi_over_sdma_completion_irq_still_posts():
    """The SPI DMA path still sets CSR BLOCK_DONE and asserts the channel completion IRQ 15.

    The firmware's `_spi_write_and_read` waits on the DMA-completion semaphore the channel IRQ/ISR
    posts; the SPI-FIFO routing must not bypass that signalling.
    """
    intc = OmapIntc()
    intc.write(0x88, 4, 1 << 15)  # unmask IRQ 15
    cpu, mcspi, c2c, sdma = _make_spi_dma_rig()
    sdma.intc = intc
    sdma.write(OmapSdma.REG_IRQENABLE_L3, 4, 0x01)  # ch0 line-3 IRQ enable

    mcspi1 = mcspi[1]
    frame = bytes([0x01]) + (0x40010008).to_bytes(4, "little")
    cpu.mu.mem_write(0x80100000, frame)
    _prog_channel(sdma, 0, 0x80100000, mcspi1.tx_fifo_addr(2), len(frame))
    sdma.write(_ch_base(0) + 0x00, 4, 0x80)

    assert (sdma.read(_ch_base(0) + 0x28, 4) & 0x28) == 0x28  # CSR FRAME_DONE|BLOCK_DONE
    assert (sdma.read(OmapSdma.REG_IRQSTATUS_L3, 4) & 0x01) == 0x01
    assert intc.has_pending_irq()


def test_non_spi_dma_unaffected_by_binding():
    """A plain memory DMA (no McSPI FIFO endpoint) is not routed through the SPI path."""
    cpu, mcspi, c2c, sdma = _make_spi_dma_rig()
    _prog_channel(sdma, 3, 0x80200000, 0x80201000, 16)
    sdma.write(_ch_base(3) + 0x00, 4, 0x80)
    assert sdma.spi_dma_transfers == 0  # not a McSPI FIFO endpoint

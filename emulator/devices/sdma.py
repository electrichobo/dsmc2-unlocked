"""
OMAP3630 System DMA (sDMA) Controller Model.
Implements the 32-channel DMA controller mapped at 0x48056000 (size 0x1000).
Supports OCP reset handshake, global configuration, interrupt enable/status,
and channel configuration registers.
"""

from typing import Any, Dict, Optional, Tuple
from emulator.core.bus import Device


class OmapSdma(Device):
    """OMAP3630 System DMA (sDMA) Controller."""

    BASE_ADDR = 0x48056000
    SIZE = 0x1000

    # Common System Registers
    REG_REVISION = 0x00
    REG_IRQSTATUS_L0 = 0x08
    REG_IRQSTATUS_L1 = 0x0C
    REG_IRQSTATUS_L2 = 0x10
    REG_IRQSTATUS_L3 = 0x14
    REG_IRQENABLE_L0 = 0x18
    REG_IRQENABLE_L1 = 0x1C
    REG_IRQENABLE_L2 = 0x20
    REG_IRQENABLE_L3 = 0x24
    REG_SYSSTATUS = 0x28
    REG_OCP_SYSCONFIG = 0x2C
    REG_CAPS_0 = 0x64
    REG_CAPS_2 = 0x6C
    REG_CAPS_3 = 0x70
    REG_CAPS_4 = 0x74
    REG_GCR = 0x78

    NUM_CHANNELS = 32
    CHANNEL_STRIDE = 0x60
    CHANNEL_BASE = 0x80

    def __init__(self, intc=None, cpu=None, gpmc=None, mcspi: Optional[Dict[int, Any]] = None):
        self.intc = intc
        self.cpu = cpu
        self.gpmc = gpmc  # GPMC model: NAND reads stream through its prefetch FIFO (not 0xFF fill)
        # McSPI controllers (port_id -> OmapMcspi). When an sDMA channel's endpoint is a McSPI
        # TX/RX FIFO register (peripheral-synchronised SPI DMA), the bytes are clocked through the
        # McSPI<->coprocessor-target byte state machine instead of a mem->mem copy.
        self.mcspi = mcspi if mcspi is not None else {}
        self.spi_dma_transfers = 0  # instrumentation: SPI frames clocked over sDMA
        # Per-McSPI-channel MISO stash: the TX channel captures the MISO bytes of each full-duplex
        # beat; the paired RX channel drains them (see _try_spi_over_sdma).
        self._spi_miso: Dict[tuple, list] = {}
        self.revision = 0x00000040  # OMAP3 sDMA revision 4.0
        self.sysstatus = 0x00000001 # bit 0: RESETDONE = 1
        self.ocp_sysconfig = 0x00000015 # AUTOIDLE=1, SIDLEMODE=smart, MIDLEMODE=smart
        self.gcr = 0x00010010
        self.irqstatus = [0, 0, 0, 0]
        self.irqenable = [0, 0, 0, 0]

        # 32 DMA channels, each with a register dict
        self.channels: Dict[int, Dict[int, int]] = {
            ch: {} for ch in range(self.NUM_CHANNELS)
        }
        self.generic_regs: Dict[int, int] = {}

    def bind_mcspi(self, mcspi: Dict[int, "object"]):
        """Attach the McSPI controllers so the peripheral-synchronised SPI DMA can clock frames."""
        self.mcspi = mcspi

    def _match_mcspi_fifo(self, addr: int, which: str):
        """Return (controller, channel) if ``addr`` is a McSPI TX ('tx') or RX ('rx') FIFO reg."""
        for ctrl in self.mcspi.values():
            if which == "tx":
                ch = ctrl.channel_for_tx_fifo(addr)
            else:
                ch = ctrl.channel_for_rx_fifo(addr)
            if ch is not None:
                return ctrl, ch
        return None, None

    def _try_spi_over_sdma(self, ch_num: int, cssa: int, cdsa: int, total_bytes: int) -> bool:
        """Clock a peripheral-synchronised SPI frame through the McSPI target for this channel.

        The firmware's SPI master (`_spi_write_and_read` @0x806D588C) runs two separate sDMA
        channels per transfer: a TX channel whose DEST (CDSA) is MCSPI_TXn, sourcing the frame
        bytes from a DDR buffer (CSSA), and -- for a read -- an RX channel whose SOURCE (CSSA) is
        MCSPI_RXn, draining the shift register into an SRAM buffer (CDSA @ 0x40200800+ch*0x800).
        Both channels drive the SAME McSPI channel, i.e. the same `CoprocessorSpiTarget` frame
        state machine, so TX advances the frame ([0x81/0x01][addr][data]) and RX shifts out the
        ACK 0x1F + 4 data bytes (c2c.CoprocessorSpiTarget). This models the real McSPI DMA-request
        handshake (CHxCONF DMAR/DMAW enable + the 8-bit FIFO beats) rather than a mem->mem copy.

        Returns True when the channel was a McSPI FIFO endpoint and was handled here.
        """
        if not self.mcspi or total_bytes <= 0 or self.cpu is None or not hasattr(self.cpu, "mu"):
            return False
        n = min(total_bytes, 1 << 16)

        # SPI is full-duplex: one McSPI beat shifts a TX byte out (MOSI) and an RX byte in (MISO)
        # at the same clock. The firmware's `_spi_write_and_read` (@0x806D588C) models each
        # transfer as a TX sDMA channel (MOSI bytes, from DDR, into MCSPI_TXn) and -- for a read --
        # a *separate* RX sDMA channel (MISO bytes, out of MCSPI_RXn, into SRAM); both channels
        # cover the SAME beats and are enabled TX-first. So we clock the target EXACTLY ONCE per
        # beat, on the TX channel, and stash the MISO bytes in a per-McSPI-channel FIFO; the RX
        # channel drains that stash instead of re-clocking (which would double the beats and
        # desync the `CoprocessorSpiTarget` frame state machine). Reads with no preceding TX fall
        # back to idle (0x00 MOSI) beats.
        # TX DMA: dest is a McSPI TX FIFO; source is the DDR frame buffer.
        ctrl, ch = self._match_mcspi_fifo(cdsa, "tx")
        if ctrl is not None:
            try:
                frame = bytes(self.cpu.mu.mem_read(cssa, n))
            except Exception:
                frame = b"\x00" * n
            # Each TX burst pairs with the RX burst that immediately follows it (the firmware runs
            # TX-then-RX per `_spi_write_and_read` call), so start a fresh MISO stash per TX burst;
            # a write-only transfer simply leaves its (ignored) MISO undrained and discarded next.
            key = (id(ctrl), ch)
            fifo = []
            self._spi_miso[key] = fifo
            for b in frame:
                fifo.append(ctrl.dma_transceive_byte(ch, b))
            self.spi_dma_transfers += 1
            return True

        # RX DMA: source is a McSPI RX FIFO; dest is the SRAM/DDR drain buffer. Drain the MISO
        # bytes captured on the paired TX beats (full-duplex); clock idle beats only if the TX
        # stash is short (a pure read with no modelled TX channel).
        ctrl, ch = self._match_mcspi_fifo(cssa, "rx")
        if ctrl is not None:
            key = (id(ctrl), ch)
            fifo = self._spi_miso.get(key, [])
            out = bytearray()
            for _ in range(n):
                if fifo:
                    out.append(fifo.pop(0) & 0xFF)
                else:
                    out.append(ctrl.dma_transceive_byte(ch, 0x00) & 0xFF)
            try:
                self.cpu.mu.mem_write(cdsa, bytes(out))
            except Exception:
                pass
            self.spi_dma_transfers += 1
            return True

        return False

    def read(self, offset: int, size: int) -> int:
        if offset == self.REG_REVISION:
            return self.revision
        elif offset == self.REG_SYSSTATUS:
            # Bit 0 is RESETDONE
            return self.sysstatus
        elif offset == self.REG_OCP_SYSCONFIG:
            return self.ocp_sysconfig
        elif offset == self.REG_GCR:
            return self.gcr
        elif self.REG_IRQSTATUS_L0 <= offset <= self.REG_IRQSTATUS_L3:
            idx = (offset - self.REG_IRQSTATUS_L0) // 4
            return self.irqstatus[idx]
        elif self.REG_IRQENABLE_L0 <= offset <= self.REG_IRQENABLE_L3:
            idx = (offset - self.REG_IRQENABLE_L0) // 4
            return self.irqenable[idx]
        elif offset >= self.CHANNEL_BASE:
            ch_offset = offset - self.CHANNEL_BASE
            ch_num = ch_offset // self.CHANNEL_STRIDE
            reg_offset = ch_offset % self.CHANNEL_STRIDE
            if 0 <= ch_num < self.NUM_CHANNELS:
                return self.channels[ch_num].get(reg_offset, 0)

        return self.generic_regs.get(offset, 0)

    def write(self, offset: int, size: int, value: int):
        val = value & 0xFFFFFFFF
        if offset == self.REG_OCP_SYSCONFIG:
            self.ocp_sysconfig = val
            if val & 0x02: # SOFTRESET
                # Reset performed instantly, RESETDONE asserted
                self.sysstatus |= 0x01
        elif offset == self.REG_SYSSTATUS:
            # Read-only status
            pass
        elif offset == self.REG_GCR:
            self.gcr = val
        elif self.REG_IRQSTATUS_L0 <= offset <= self.REG_IRQSTATUS_L3:
            idx = (offset - self.REG_IRQSTATUS_L0) // 4
            # Write 1 to clear
            self.irqstatus[idx] &= ~val
            if idx == 3:
                # Line 3 maps to INTC IRQ 15
                if not (self.irqstatus[3] & self.irqenable[3]) and self.intc:
                    self.intc.deassert_irq(15)
        elif self.REG_IRQENABLE_L0 <= offset <= self.REG_IRQENABLE_L3:
            idx = (offset - self.REG_IRQENABLE_L0) // 4
            self.irqenable[idx] = val
            if idx == 3 and (self.irqstatus[3] & self.irqenable[3]) and self.intc:
                self.intc.assert_irq(15)
        elif offset >= self.CHANNEL_BASE:
            ch_offset = offset - self.CHANNEL_BASE
            ch_num = ch_offset // self.CHANNEL_STRIDE
            reg_offset = ch_offset % self.CHANNEL_STRIDE
            if 0 <= ch_num < self.NUM_CHANNELS:
                if reg_offset == 0x00: # DMA4_CCR (Channel Control)
                    self.channels[ch_num][reg_offset] = val
                    if val & 0x80: # Channel Enable
                        # Complete transfer instantly for McSPI / NAND / memory DMA
                        if self.cpu and hasattr(self.cpu, "mu"):
                            cssa = self.channels[ch_num].get(0x1C, 0) # DMA4_CSSA (source)
                            cdsa = self.channels[ch_num].get(0x20, 0) # DMA4_CDSA (dest)
                            cen = self.channels[ch_num].get(0x14, 0) & 0xFFFF # DMA4_CEN
                            cfn = self.channels[ch_num].get(0x18, 0) & 0xFFFF # DMA4_CFN
                            csdp = self.channels[ch_num].get(0x10, 0) # DMA4_CSDP
                            elem_size = 1 << (csdp & 3)
                            total_bytes = (cen if cen else 1) * (cfn if cfn else 1) * elem_size
                            # Peripheral-synchronised SPI DMA: if either endpoint is a McSPI TX/RX
                            # FIFO register, clock the SPI frame through the McSPI target's
                            # transceive byte state machine instead of a mem->mem copy.
                            handled_spi = self._try_spi_over_sdma(ch_num, cssa, cdsa, total_bytes)
                            is_nand_read = (
                                self.gpmc is not None
                                and self.gpmc.prefetch_armed
                                and 0x10000000 <= cssa < 0x18000000
                            )
                            if (not handled_spi) and total_bytes > 0 and 0x40000000 <= cdsa < 0x90000000:
                                if is_nand_read:
                                    n = min(total_bytes, 1 << 20)
                                    payload = self.gpmc.drain_prefetch(n)
                                else:
                                    # Preserve the prior behavior for non-NAND / memory DMA: an
                                    # erased (0xFF) fill, which a blank source would also produce.
                                    payload = b"\xFF" * min(total_bytes, 8192)
                                try:
                                    self.cpu.mu.mem_write(cdsa, payload)
                                except Exception:
                                    pass

                        # Signal transfer complete in the per-channel status register DMA4_CSRi.
                        # On OMAP3630 that register is at channel offset 0x28 (NOT 0x0C -- 0x0C is
                        # CLNK_CTRL); the DMA ISR reads CSRi to learn which event completed before
                        # posting the channel's completion semaphore. Set FRAME_DONE (0x08) and
                        # BLOCK_DONE (0x20) and clear the active bit; also keep 0x0C updated for any
                        # reader of the legacy offset. Without CSRi set, the ISR clears IRQSTATUS but
                        # never posts the semaphore, so the waiting NAND/FileX thread never wakes.
                        self.channels[ch_num][0x28] = (self.channels[ch_num].get(0x28, 0) | 0x28)
                        self.channels[ch_num][0x0C] = (self.channels[ch_num].get(0x0C, 0) | 0x20) & ~0x80
                        # Assert IRQ on Line 3 (IRQ 15)
                        self.irqstatus[3] |= (1 << ch_num)
                        if (self.irqenable[3] & (1 << ch_num)) and self.intc:
                            self.intc.assert_irq(15)
                elif reg_offset in (0x0C, 0x28): # DMA4_CSR status (write 1 to clear)
                    self.channels[ch_num][reg_offset] = \
                        self.channels[ch_num].get(reg_offset, 0) & ~val
                else:
                    self.channels[ch_num][reg_offset] = val
        else:
            self.generic_regs[offset] = val

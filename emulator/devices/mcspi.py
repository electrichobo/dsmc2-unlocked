"""
OMAP3630 / DM3725 Multichannel Serial Port Interface (McSPI) Controller Model.
TRM Reference: SPRUGN4 Chapter 20 (McSPI), Table 2-3 (L4-Core Memory Space Mapping).
Implements the 4 McSPI controllers at:
  - McSPI 1: 0x48098000 (IRQ 65) — Coprocessor SPI bus (ts_isp1, ts_isp2)
  - McSPI 2: 0x4809A000 (IRQ 66) — Sensor & FPGA SPI bus (ts_fpga, ts_sensor)
  - McSPI 3: 0x480B8000 (IRQ 91) — IOB & Accelerometer SPI bus (ts_iob0, ts_iob1)
  - McSPI 4: 0x480BA000 (IRQ 48) — Coprocessor Mailbox / Auxiliary SPI bus
"""

from typing import Dict, Optional, Callable, Any
from emulator.core.bus import Device


class SpiTargetDevice:
    """Base class for SPI target devices attached to an OmapMcspi channel."""

    def __init__(self, name: str, port_id: int, channel: int):
        self.name = name
        self.port_id = port_id
        self.channel = channel
        self.registers: Dict[int, int] = {}
        self.transfer_count = 0

    def transceive_byte(self, tx_byte: int) -> Optional[int]:
        """
        Handle an 8-bit SPI byte transfer on this chip-select channel. Byte-oriented
        targets (the coprocessor register frame [0x81/0x01][addr][data]+ACK) override
        this. Return None to fall back to loopback.
        """
        return None

    def transceive_word(self, tx_word: int) -> Optional[int]:
        """
        Handle a 32-bit SPI word transfer on this chip-select channel.
        Return None to fall back to standard McSPI TX->RX loopback.
        """
        self.transfer_count += 1
        return None

    def read_reg(self, addr: int, length: int = 4) -> bytes:
        """Read register bytes for `read spi <target> <addr> <len>`."""
        out = bytearray()
        for off in range(0, length, 4):
            val = self.registers.get((addr + off) & 0xFFFFFFFF, 0x00000001)
            out.extend(val.to_bytes(4, "little"))
        return bytes(out[:length])

    def write_reg(self, addr: int, data: bytes) -> bool:
        """Write register bytes for SPI register write operations."""
        for off in range(0, len(data), 4):
            chunk = data[off:off + 4].ljust(4, b"\x00")
            val = int.from_bytes(chunk, "little")
            self.registers[(addr + off) & 0xFFFFFFFF] = val
        self.transfer_count += 1
        return True


class OmapMcspi(Device):
    """OMAP3630 / DM3725 McSPI Master Controller."""

    SIZE = 0x1000

    # Register Offsets (TRM Table 20-16)
    REG_REVISION = 0x00
    REG_SYSCONFIG = 0x10
    REG_SYSSTATUS = 0x14
    REG_IRQSTATUS = 0x18
    REG_IRQENABLE = 0x1C
    REG_WAKEUPENABLE = 0x20
    REG_SYST = 0x24
    REG_MODULCTRL = 0x28

    CH_BASE = 0x2C
    CH_STRIDE = 0x14
    NUM_CHANNELS = 4

    def __init__(self, port_id: int, base_addr: int, irq_num: int, intc=None):
        self.port_id = port_id
        self.base_addr = base_addr
        self.irq_num = irq_num
        self.intc = intc

        self.revision = 0x00000021
        self.sysconfig = 0x00000015
        self.sysstatus = 0x00000001  # Bit 0: RESETDONE = 1
        self.irqstatus = 0x00000000
        self.irqenable = 0x00000000
        self.wakeupenable = 0x00000000
        self.syst = 0x00000000
        self.modulctrl = 0x00000000

        # Channel state (4 channels per McSPI)
        # Each channel has: chconf, chstat, chctrl, tx, rx
        self.chconf = [0] * self.NUM_CHANNELS
        self.chstat = [0x00000006] * self.NUM_CHANNELS  # Bit 1 (TXS=1: TX empty), Bit 2 (EOT=1: End of transfer)
        self.chctrl = [0] * self.NUM_CHANNELS
        self.tx = [0] * self.NUM_CHANNELS
        self.rx = [0] * self.NUM_CHANNELS
        self.rx_dma_fifo = [bytearray() for _ in range(self.NUM_CHANNELS)]

        # Attached SPI targets per channel (0..3)
        self.targets: Dict[int, Any] = {}
        self.on_word_tx: Optional[Callable[[int, int, int], Optional[int]]] = None
        self.generic_regs: Dict[int, int] = {}

    def attach_target(self, channel: int, target: Any):
        """Attach an SPI target device to a chip-select channel (0..3)."""
        if 0 <= channel < self.NUM_CHANNELS:
            self.targets[channel] = target

    def _clock_byte(self, ch_num: int, tx_byte: int) -> int:
        """Clock one 8-bit SPI beat through the channel's attached target."""
        target = self.targets.get(ch_num)
        rx_resp = None
        b = tx_byte & 0xFF
        if target is not None:
            if hasattr(target, "transceive_byte"):
                rx_resp = target.transceive_byte(b)
            if rx_resp is None and hasattr(target, "transceive_word"):
                rx_resp = target.transceive_word(b)
        if rx_resp is None and self.on_word_tx is not None:
            rx_resp = self.on_word_tx(self.port_id, ch_num, b)
        return (rx_resp if rx_resp is not None else b) & 0xFF

    # --- channel FIFO register addresses (for the sDMA peripheral-synchronised path) ---
    def tx_fifo_addr(self, channel: int) -> int:
        return self.base_addr + self.CH_BASE + channel * self.CH_STRIDE + 0x0C

    def rx_fifo_addr(self, channel: int) -> int:
        return self.base_addr + self.CH_BASE + channel * self.CH_STRIDE + 0x10

    def channel_for_tx_fifo(self, addr: int) -> Optional[int]:
        for ch in range(self.NUM_CHANNELS):
            if addr == self.tx_fifo_addr(ch):
                return ch
        return None

    def channel_for_rx_fifo(self, addr: int) -> Optional[int]:
        for ch in range(self.NUM_CHANNELS):
            if addr == self.rx_fifo_addr(ch):
                return ch
        return None

    def dma_transceive_byte(self, channel: int, tx_byte: int) -> int:
        """Clock one SPI byte through the channel's attached target for a DMA beat."""
        b = tx_byte & 0xFF
        rx = self._clock_byte(channel, b)
        self.tx[channel] = b
        self.rx[channel] = rx & 0xFF
        self.chstat[channel] |= 0x07  # RXS | TXS | EOT
        return rx & 0xFF

    def dma_tx_bytes(self, ch_num: int, tx_bytes: bytes) -> None:
        """Clock a DMA TX payload through an SPI channel and stage MISO bytes when RX is active."""
        if not (0 <= ch_num < self.NUM_CHANNELS):
            return
        trm = (self.chconf[ch_num] >> 12) & 0x03  # 0=TX+RX, 1=RX-only, 2=TX-only
        rx_stage = bytearray()
        for b in tx_bytes:
            rx_b = self._clock_byte(ch_num, b)
            rx_stage.append(rx_b)
            self.rx[ch_num] = rx_b
        if trm != 0x02:
            self.rx_dma_fifo[ch_num].extend(rx_stage)
            self.chstat[ch_num] |= 0x01  # RXS = 1
        self.chstat[ch_num] |= 0x06  # TXS = 1, EOT = 1

    def dma_rx_bytes(self, ch_num: int, count: int) -> bytes:
        """Drain `count` received MISO bytes for an sDMA RX transfer on `ch_num`."""
        if not (0 <= ch_num < self.NUM_CHANNELS) or count <= 0:
            return b""
        fifo = self.rx_dma_fifo[ch_num]
        if fifo:
            out = bytes(fifo[:count])
            del fifo[:count]
            if len(out) < count:
                out += bytes(self._clock_byte(ch_num, 0x00) for _ in range(count - len(out)))
        else:
            out = bytes(self._clock_byte(ch_num, 0x00) for _ in range(count))
        if out:
            self.rx[ch_num] = out[-1]
        if not fifo:
            self.chstat[ch_num] &= ~0x01
        return out

    def read(self, offset: int, size: int) -> int:
        if offset == self.REG_REVISION:
            return self.revision
        elif offset == self.REG_SYSCONFIG:
            return self.sysconfig
        elif offset == self.REG_SYSSTATUS:
            return self.sysstatus
        elif offset == self.REG_IRQSTATUS:
            return self.irqstatus
        elif offset == self.REG_IRQENABLE:
            return self.irqenable
        elif offset == self.REG_WAKEUPENABLE:
            return self.wakeupenable
        elif offset == self.REG_SYST:
            return self.syst
        elif offset == self.REG_MODULCTRL:
            return self.modulctrl
        elif self.CH_BASE <= offset < self.CH_BASE + (self.NUM_CHANNELS * self.CH_STRIDE):
            ch_off = offset - self.CH_BASE
            ch_num = ch_off // self.CH_STRIDE
            reg_off = ch_off % self.CH_STRIDE
            if reg_off == 0x00:  # MCSPI_CHCONF
                return self.chconf[ch_num]
            elif reg_off == 0x04:  # MCSPI_CHSTAT
                return self.chstat[ch_num]
            elif reg_off == 0x08:  # MCSPI_CHCTRL
                return self.chctrl[ch_num]
            elif reg_off == 0x0C:  # MCSPI_TX
                return self.tx[ch_num]
            elif reg_off == 0x10:  # MCSPI_RX
                # Reading RX clears RXS bit 0 in CHSTAT
                val = self.rx[ch_num]
                self.chstat[ch_num] &= ~0x01
                return val

        return self.generic_regs.get(offset, 0)

    def write(self, offset: int, size: int, value: int):
        val = value & 0xFFFFFFFF
        if offset == self.REG_SYSCONFIG:
            self.sysconfig = val
            if val & 0x02:  # SOFTRESET
                self.sysstatus |= 0x01  # Instant reset done
        elif offset == self.REG_SYSSTATUS:
            pass  # Read-only
        elif offset == self.REG_IRQSTATUS:
            self.irqstatus &= ~val  # Write-1-to-clear
            if not (self.irqstatus & self.irqenable) and self.intc:
                self.intc.deassert_irq(self.irq_num)
        elif offset == self.REG_IRQENABLE:
            self.irqenable = val
            if (self.irqstatus & self.irqenable) and self.intc:
                self.intc.assert_irq(self.irq_num)
        elif offset == self.REG_WAKEUPENABLE:
            self.wakeupenable = val
        elif offset == self.REG_SYST:
            self.syst = val
        elif offset == self.REG_MODULCTRL:
            self.modulctrl = val
        elif self.CH_BASE <= offset < self.CH_BASE + (self.NUM_CHANNELS * self.CH_STRIDE):
            ch_off = offset - self.CH_BASE
            ch_num = ch_off // self.CH_STRIDE
            reg_off = ch_off % self.CH_STRIDE
            if reg_off == 0x00:  # MCSPI_CHCONF
                self.chconf[ch_num] = val
            elif reg_off == 0x04:  # MCSPI_CHSTAT
                pass  # Read-only status
            elif reg_off == 0x08:  # MCSPI_CHCTRL
                prev = self.chctrl[ch_num]
                self.chctrl[ch_num] = val
                # Sub-transfers inside a multi-part read toggle CHCTRL EN without releasing
                # CS-mux/frame state, so clear leftover FIFO bytes but preserve target phase.
                if (prev & 0x01) != (val & 0x01):
                    self.rx_dma_fifo[ch_num].clear()
            elif reg_off == 0x0C:  # MCSPI_TX
                self.tx[ch_num] = val
                rx_resp = None
                target = self.targets.get(ch_num)
                # CHCONF WL field (bits [11:7]) = SPI word length - 1. WL==7 => 8-bit
                # transfers, the mode the coprocessor register frame uses; clock the low
                # byte through the byte-level state machine.
                wl = (self.chconf[ch_num] >> 7) & 0x1F
                if target is not None and wl == 0x07 and hasattr(target, "transceive_byte"):
                    rx_resp = target.transceive_byte(val & 0xFF)
                if rx_resp is None and target is not None and hasattr(target, "transceive_word"):
                    rx_resp = target.transceive_word(val)
                if rx_resp is None and self.on_word_tx is not None:
                    rx_resp = self.on_word_tx(self.port_id, ch_num, val)
                # Default to loopback when target returns None
                self.rx[ch_num] = (rx_resp if rx_resp is not None else val) & 0xFFFFFFFF
                self.chstat[ch_num] |= 0x01  # RXS = 1 (RX full)
                self.chstat[ch_num] |= 0x06  # TXS = 1, EOT = 1
                # Assert channel TX/RX IRQ status bits (TX0_EMPTY = bit 0, RX0_FULL = bit 2, stride 4 bits per ch)
                ch_irq_bits = (0x05 << (ch_num * 4))
                self.irqstatus |= ch_irq_bits
                if (self.irqstatus & self.irqenable) and self.intc:
                    self.intc.assert_irq(self.irq_num)
            elif reg_off == 0x10:  # MCSPI_RX
                self.rx[ch_num] = val
        else:
            self.generic_regs[offset] = val


MCSPI_MAP = {
    1: (0x48098000, 65),
    2: (0x4809A000, 66),
    3: (0x480B8000, 91),
    4: (0x480BA000, 48),
}

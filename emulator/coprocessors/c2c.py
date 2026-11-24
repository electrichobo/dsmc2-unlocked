"""
Coprocessor register transport (SPI) + GPMC IOB mailbox.

FW-PROVEN TRANSPORT (sdk findings/hardware/COPROCESSOR_TRANSPORT.md; memory
`omap-iob-transport`; sdk PR #92). The DSMC2 brain (TI DM3725 / OMAP3630-class) talks
to the ISP1/ISP2/IOB/LSB-FPGA coprocessors over THREE separate buses — do not conflate
them:

  1. SPI  — config *register* read/write. On-wire byte frame (fpga_write @0x8070eec0 /
            fpga_read @0x8070ef5c / isp_reg_read_with_error_code @0x807630b0 ->
            spi_write_read @0x806d5390; iomap channel builder FUN_8070eec0 emits exactly
            `{0x81, u32 addr, u32 val}`):

              WRITE: byte[0]=0x81 ; byte[1..4]=addr(u32) ; byte[5..8]=data(u32)  -> 9 bytes
              READ : byte[0]=0x01 ; byte[1..4]=addr(u32) ; then poll ACK byte 0x1F
                     (<=10 tries) and read 4 bytes back (little-endian u32)

            addr/data are little-endian on the wire. Target is selected by a CS mux
            ("SPI CS MuxSel 1/2/3"): ISP1 = SPI target 7, ISP2 = SPI target 8,
            IOB = `iobspi`, slave/second IOB = `iobslavespi`, LSB-FPGA = iomap channel 6.

  2. GPMC — the IOB appears as a memory-mapped slave; its AHB mailbox lives at
            0xA580_0000. This IS the mailbox (events EVNT_MAILBOX_IOB0/1,
            EVNT_MAILBOX_ISP0/1/2). Kept SEPARATE from SPI below (IobMailboxDevice).

  3. XAUI/C2C — a SerDes PHY used for BULK DMA ONLY (frames / cal maps / video),
            6 endpoints (IOB, ISP0-2, MON0, EXP1/2). NOT a network, NOT register access.
            Not modelled here (register access never uses it).

NOTE (provenance correction): earlier revisions of this file invented a framed "C2C SPI
mailbox" (0xC2 magic header, 0xA5 ACK reply, a REG_DEVICE_ID/REG_MAILBOX_CMD register
map). No such framing exists in the firmware. This model now replays the real opcode
frame above and drives the drivers' real register addresses (e.g. the LSB-FPGA AXI space
0x44A00000 reached over iomap ch6; version read at 0xE0000908).
"""

import struct
from typing import Dict, Any, List, Optional, Tuple
from emulator.core.bus import Device
from emulator.devices.mcspi import OmapMcspi, SpiTargetDevice


# SPI frame opcodes (byte[0] of a transfer), FW-proven.
SPI_OP_WRITE = 0x81
SPI_OP_READ = 0x01
SPI_ACK = 0x1F           # byte the target drives when a read result is ready
SPI_ACK_MAX_POLLS = 10   # spi_write_wait_for_ack_then_read tries=10


class CoprocessorSpiTarget(SpiTargetDevice):
    """
    One SPI-selected coprocessor register file (ISP1, ISP2, IOB, LSB-FPGA ...).

    Implements the firmware's byte-stream frame as a *state machine* clocked one SPI
    byte at a time (SPI word length is 8 bits for this bus):

      host clocks 0x81 -> collect 4 addr bytes -> collect 4 data bytes -> commit write
      host clocks 0x01 -> collect 4 addr bytes -> arm read:
                          next poll bytes return 0x00 until ACK, then 0x1F,
                          then the 4 little-endian data bytes of the register.

    `registers` maps a 32-bit register address -> 32-bit value, exactly the addresses the
    driver pokes (not an invented 0x0000/0x0004 map).
    """

    # Frame phases
    _IDLE = 0
    _W_ADDR = 1      # collecting write address bytes
    _W_DATA = 2      # collecting write data bytes
    _R_ADDR = 3      # collecting read address bytes
    _R_ACK = 4       # driving 0x00.. then ACK 0x1F
    _R_DATA = 5      # shifting out the 4 result bytes

    def __init__(self, name: str, port_id: int, channel: int, spi_target_id: int,
                 transport: "CoprocessorTransport", default_reg: int = 0x00000000):
        super().__init__(name, port_id, channel)
        self.spi_target_id = spi_target_id       # CS-mux selector (ISP1=7, ISP2=8, ...)
        self.transport = transport
        self.default_reg = default_reg
        # Frame state machine
        self._phase = self._IDLE
        self._buf = bytearray()
        self._cur_addr = 0
        self._ack_polls = 0
        self._out = bytearray()
        self.last_write: Optional[Tuple[int, int]] = None
        self.last_read: Optional[Tuple[int, int]] = None

    # --- register backing store -------------------------------------------------
    def reg_get(self, addr: int) -> int:
        return self.registers.get(addr & 0xFFFFFFFF, self.default_reg)

    def reg_set(self, addr: int, val: int) -> None:
        self.registers[addr & 0xFFFFFFFF] = val & 0xFFFFFFFF

    # --- byte-level SPI frame (the real transport) ------------------------------
    def transceive_byte(self, tx_byte: int) -> int:
        """
        Clock one SPI byte. Returns the byte the target drives on MISO for this beat.
        Implements the FW frame: [0x81|0x01][addr u32][data u32?] + ACK 0x1F + 4B read.
        """
        self.transfer_count += 1
        tx_byte &= 0xFF

        if self._phase == self._IDLE:
            if tx_byte == SPI_OP_WRITE:
                self._phase = self._W_ADDR
                self._buf = bytearray()
            elif tx_byte == SPI_OP_READ:
                self._phase = self._R_ADDR
                self._buf = bytearray()
            # Any other opcode: ignore, stay idle (MISO 0).
            return 0x00

        if self._phase == self._W_ADDR:
            self._buf.append(tx_byte)
            if len(self._buf) == 4:
                self._cur_addr = int.from_bytes(self._buf, "little")
                self._buf = bytearray()
                self._phase = self._W_DATA
            return 0x00

        if self._phase == self._W_DATA:
            self._buf.append(tx_byte)
            if len(self._buf) == 4:
                data = int.from_bytes(self._buf, "little")
                self.reg_set(self._cur_addr, data)
                self.last_write = (self._cur_addr, data)
                self._phase = self._IDLE
            return 0x00

        if self._phase == self._R_ADDR:
            self._buf.append(tx_byte)
            if len(self._buf) == 4:
                self._cur_addr = int.from_bytes(self._buf, "little")
                self._ack_polls = 0
                self._phase = self._R_ACK
            return 0x00

        if self._phase == self._R_ACK:
            # Target becomes ready immediately in the model; drive the ACK byte.
            self._ack_polls += 1
            val = self.reg_get(self._cur_addr)
            self.last_read = (self._cur_addr, val)
            self._out = bytearray(val.to_bytes(4, "little"))
            self._phase = self._R_DATA
            return SPI_ACK

        if self._phase == self._R_DATA:
            out = self._out.pop(0) if self._out else 0x00
            if not self._out:
                self._phase = self._IDLE
            return out

        return 0x00

    def transceive_word(self, tx_word: int) -> Optional[int]:
        """
        Legacy 32-bit-word entry point retained for McSPI drivers that clock a full word.
        Decomposes the word into 4 big-endian-on-MSB-first SPI beats (MSB clocked first,
        as the OMAP McSPI does for an 8-bit-per-element >1-word transfer) and returns the
        assembled MISO word. Byte-oriented callers should use transceive_byte directly.
        """
        rx = 0
        for shift in (24, 16, 8, 0):
            rx = (rx << 8) | self.transceive_byte((tx_word >> shift) & 0xFF)
        return rx & 0xFFFFFFFF

    def reset_frame(self) -> None:
        self._phase = self._IDLE
        self._buf = bytearray()
        self._out = bytearray()


class CsMuxSpiTarget:
    """
    GPIO-multiplexed SPI channel target for McSPI1 CH0 (`ISP1` target 7 vs `ISP2` target 8).

    The firmware CS mux (`FUN_806D5500`) selects targets 7/8/9 on bus 0 channel 0 via
    GPIO 76 (bank 3 pin 12) and GPIO 77 (bank 3 pin 13):
      - GPIO76=0, GPIO77=1 -> target 7 (ts_isp1)
      - GPIO76=1, GPIO77=0 -> target 8 (ts_isp2)
    """

    def __init__(self, targets_by_sel: Dict[Tuple[int, int], CoprocessorSpiTarget], gpio=None,
                 fallback: Optional[CoprocessorSpiTarget] = None):
        self.targets_by_sel = targets_by_sel
        self.gpio = gpio
        self.fallback = fallback

    def _active(self) -> Optional[CoprocessorSpiTarget]:
        if self.gpio and 3 in self.gpio:
            out = self.gpio[3].dataout
            g76 = (out >> 12) & 1
            g77 = (out >> 13) & 1
            if (g76, g77) in self.targets_by_sel:
                return self.targets_by_sel[(g76, g77)]
        return self.fallback

    def transceive_byte(self, tx_byte: int) -> int:
        tgt = self._active()
        return tgt.transceive_byte(tx_byte) if tgt else 0x00

    def transceive_word(self, tx_word: int) -> Optional[int]:
        tgt = self._active()
        return tgt.transceive_word(tx_word) if tgt else 0

    def reset_frame(self) -> None:
        for tgt in self.targets_by_sel.values():
            tgt.reset_frame()
        if self.fallback:
            self.fallback.reset_frame()


class IobXauiAhbDevice(Device):
    """
    GPMC CS2 AHB FPGA/IOB register window mapped at 0x1C000000 (size 0x04000000).

    The firmware's `FUN_80727028` maps 0x8002xxxx / 0x8006xxxx / 0x8007xxxx / 0x800Axxxx /
    0x800Bxxxx onto `(addr & ~0xFC000000) | 0x1C000000` (with bank selector written to
    0x18000018). Models the XAUI PCS/link/PHY status registers polled by `FUN_809BCF60`,
    `FUN_809BC3A8`, and `FUN_809BCD28` during `weapon_left_side_board_init`, plus the IOB
    ready status registers at 0x000B0018/0x000B001C polled by `wait_for_iob_status`
    (@0x807B3050, xsys_iob_tools.c) during `monitor_pipeline_init` (@0x8074C9E4).
    """

    BASE_ADDR = 0x1C000000
    SIZE = 0x04000000  # Full GPMC CS2 FPGA/IOB window (0x1C000000..0x1FFFFFFF)

    # IOB XAUI register low-16 offsets and link-up readback values
    PCS_STATUS_OK = 0x03FFC02F     # +0x002C: all 4 XAUI lanes synced/aligned + link up
    LINK_READY_BIT = 0x20000000    # +0x0010: bit 29 PHY ready (bit 18 RX-fault clear)
    LINK_ERR_MASK = 0x00040000
    PHY_READY_BIT = 0x00100000     # +0x0248: bit 20 PHY locked/ready
    IOB_STATUS_READY_MASK = 0x007FFFFF  # +0xB0018/+0xB001C: IOB readiness bits

    def __init__(
        self,
        xaui_link_up: bool = True,
        sata_media_present: bool = False,
        iob_status_handshake_enabled: bool = True,
        xaui_window: Optional[Any] = None,
    ):
        self.xaui_link_up = bool(xaui_link_up)
        self.sata_media_present: bool = bool(sata_media_present)
        self.sata_shadow_sregs_enabled: bool = False
        self.iob_status_handshake_enabled: bool = bool(iob_status_handshake_enabled)
        self.xaui_window = xaui_window
        self.regs: Dict[int, int] = {
            0x000B0018: self.IOB_STATUS_READY_MASK,
            0x000B001C: self.IOB_STATUS_READY_MASK,
            0x000B06CC: 0x0000000C,
        }
        self._status_poll_toggle: Dict[int, bool] = {}

    def _read_sata_ahci_reg(self, aligned_off: int) -> Optional[int]:
        """Serve HBA0 SATA3-FPGA / AHCI direct (0x5006xxxx/0x5007xxxx) and shadow SREGs (0xB0680..)."""
        px_ssts = 0x00000133 if self.sata_media_present else 0x00000000  # Gen3 6.0Gbps, DET=3
        if not self.sata_shadow_sregs_enabled:
            # Before full SATA3/AHCI mode is enabled, expose only the firmware's two gated
            # SATA3 shadow indices (0x1A0 @ 0xB0680 and 0x1AE @ 0xB06B8) so boot_to_run_application
            # sees FPGA-running when xaui_link_up and PxSSTS=0 (empty bay) when not media_present.
            if aligned_off == 0x000B0680:
                return 0x800B0680 if self.xaui_link_up else 0x00000000
            if aligned_off == 0x000B06B8:
                return px_ssts if self.xaui_link_up else 0x00000000
            return None
        direct_ahci_map = {
            0x00060000: 0x4034FF00,  # AHCI CAP
            0x00060004: 0x80000000,  # AHCI GHC (AE=1)
            0x00060008: 0x00000000,  # AHCI IS
            0x0006000C: 0x00000001,  # AHCI PI (Port 0 implemented)
            0x00060010: 0x00010300,  # AHCI VS (v1.3)
            0x00060118: 0x00014017,  # PxCMD
            0x00060120: 0x00000050,  # PxTFD (DRDY|DSC)
            0x00060128: px_ssts,     # PxSSTS
            0x00060130: 0x00000000,  # PxSERR
            0x00060134: 0x00000000,  # PxSACT
            0x00060138: 0x00000000,  # PxCI
            0x00070010: 0x00000000 if self.sata_media_present else 0x00000004,
        }
        if aligned_off in direct_ahci_map:
            return direct_ahci_map[aligned_off]
        shadow_sreg_map = {
            # Shadow SREGs @ 0x800B0680..0x800B06CC (SATADriver.c idx 0x1A0..0x1B3)
            0x000B0680: 0x4034FF00,
            0x000B0684: 0x80000000,
            0x000B0688: 0x00000000,
            0x000B068C: 0x00000001,
            0x000B0690: 0x00010300,
            0x000B06B0: 0x00014017,
            0x000B06B4: 0x00000050,
            0x000B06B8: px_ssts,
            0x000B06C0: 0x00000000,
            0x000B06C4: 0x00000000,
            0x000B06C8: 0x00000000,
        }
        if aligned_off in shadow_sreg_map:
            return shadow_sreg_map[aligned_off]
        if aligned_off == 0x000B06CC:
            return self.regs.get(0x000B06CC, 0x0000000C)
        return None

    def _read_word(self, aligned_off: int) -> int:
        sata_val = self._read_sata_ahci_reg(aligned_off)
        if sata_val is not None:
            return sata_val
        val = self.regs.get(aligned_off, 0x00000000)
        if 0x000B0000 <= aligned_off < 0x000B1000:
            if not self.xaui_link_up or not self.iob_status_handshake_enabled:
                return 0x00000000
            if aligned_off == 0x000B0020:
                return 0x00000004
            cur = not self._status_poll_toggle.get(aligned_off, False)
            self._status_poll_toggle[aligned_off] = cur
            return 0xFFFFFFFF if cur else 0x00000000
        reg = aligned_off & 0xFFFF
        if self.xaui_link_up:
            if reg == 0x002C:
                return self.PCS_STATUS_OK
            if reg == 0x0010:
                return (val | self.LINK_READY_BIT) & ~self.LINK_ERR_MASK
            if reg in (0x0068, 0x00BC):
                return 0x00000000
            if reg == 0x0248:
                return val | self.PHY_READY_BIT
        return val

    def read(self, offset: int, size: int) -> int:
        aligned_off = offset & ~0x3
        if self.xaui_window is not None:
            self.xaui_window.read_count += 1
            if self.xaui_window.is_status_reg(aligned_off):
                reg_off = aligned_off & 0xFFFF
                self.xaui_window.status_reads[reg_off] = (
                    self.xaui_window.status_reads.get(reg_off, 0) + 1
                )
        word = self._read_word(aligned_off)
        shift = (offset & 0x3) * 8
        mask = (1 << (size * 8)) - 1
        return (word >> shift) & mask

    def write(self, offset: int, size: int, val: int):
        aligned = offset & ~0x3
        shift = (offset & 0x3) * 8
        mask = ((1 << (size * 8)) - 1) << shift
        cur = self.regs.get(aligned, 0x00000000)
        self.regs[aligned] = ((cur & ~mask) | ((val << shift) & mask)) & 0xFFFFFFFF


class IobMailboxDevice(Device):
    """
    GPMC AHB mailbox the IOB exposes as a memory-mapped slave at 0xA580_0000.

    This is transport #1 (GPMC), SEPARATE from SPI. The OMAP writes a mailbox descriptor
    here and the IOB raises EVNT_MAILBOX_IOB0/1 / EVNT_MAILBOX_ISP0/1/2. Register access
    (config) does NOT go through here — it goes over SPI (CoprocessorSpiTarget).
    Modelled as a simple word-addressable region with an optional command callback.
    """

    BASE_ADDR = 0xA5800000
    SIZE = 0x00800000  # 0xA5800000 - 0xA5FFFFFF

    def __init__(self, intc=None, mailbox_irq: Optional[int] = None):
        self.intc = intc
        self.mailbox_irq = mailbox_irq
        self.mem: Dict[int, int] = {}
        self.command_log: List[Tuple[int, int]] = []

    def read(self, offset: int, size: int) -> int:
        return self.mem.get(offset & ~0x3, 0x00000000)

    def write(self, offset: int, size: int, val: int):
        off = offset & ~0x3
        self.mem[off] = val & 0xFFFFFFFF
        self.command_log.append((off, val & 0xFFFFFFFF))
        if self.mailbox_irq is not None and self.intc:
            self.intc.assert_irq(self.mailbox_irq)


class CoprocessorTransport(Device):
    """
    Binds the SPI coprocessor targets to the McSPI controllers and exposes the firmware's
    `read spi <target> <addr> <len>` / register-write primitives directly (byte frame).

    No invented mailbox/state-machine protocol: registers move over SPI; the GPMC mailbox
    (IobMailboxDevice) is a separate device; bulk DMA (XAUI/C2C) is out of scope here.
    """

    # CS-mux SPI target ids (FW-proven: ISP1 = target 7, ISP2 = target 8).
    SPI_TARGET_ISP1 = 7
    SPI_TARGET_ISP2 = 8
    SPI_TARGET_IOB = 0          # `iobspi` (distinct mux leg)
    SPI_TARGET_IOB_SLAVE = 1    # `iobslavespi` / IOB2
    IOMAP_CHANNEL_LSB_FPGA = 6  # LSB-FPGA reached over iomap channel 6

    # LSB-FPGA real registers (sdk LSB_FPGA_CHARACTERIZED/NAMED): internal AXI 0x44A00000
    # space + version at 0xE0000908 (and FW 0x8070EE88 reads 0xE0000904). Seed bring-up
    # values so driver reads see them.
    LSB_FPGA_VERSION_ALT_ADDR = 0xE0000904
    LSB_FPGA_VERSION_ALT = 0x00030031  # major 3, rev 0x31
    LSB_FPGA_VERSION_ADDR = 0xE0000908
    LSB_FPGA_VERSION = 0x00000014      # rev 0x14 (unlocks low 0x44A0xxxx window)
    LSB_FPGA_BITSTREAM_STAT_ADDR = 0xE00003FC
    LSB_FPGA_BITSTREAM_READY = 0x81000000  # bit 31 done + bit 24 configured
    LSB_FPGA_XAUI_STAT_ADDR = 0x40010008
    LSB_FPGA_XAUI_READY = 0x000007E0   # XAUI PCS link-up ((0xFC << 3) = 0x7E0)
    LSB_FPGA_SSD_DETECT_ADDR = 0x44A0000C  # bit 18 (0x40000) clear => SSD present
    LSB_FPGA_SIDE_SATA_CTRL_ADDR = 0xC2000074
    LSB_FPGA_ERR_LEN_ADDR = 0xC2000010
    LSB_FPGA_ERR_CRC_ADDR = 0xC20000BC

    # LSB-FPGA XAUI link-status registers read by lsb_fpga_report_xaui_status (@0x809BCF64)
    # over fpga_read(6, addr) -> FUN_809ba680: present an UP link so the firmware's own
    # left-side-board bring-up (weapon_left_side_board_init @0x809B5358) completes. Values and
    # provenance live in devices/iob_xaui.py (FPGA_LINK_UP_REGS), shared so the IOB AHB-window
    # and the FPGA-SPI halves of the link agree.

    # Named target -> (mcspi_port_id, channel, spi_target_id). Ports/channels are the
    # emulator's McSPI wiring; the spi_target_id is the FW CS-mux selector.
    # ts_fpga: the LSB-FPGA (iomap channel 6 / CS-mux target 6) is driven over McSPI1
    # channel 2 -- verified by tracing the firmware's fpga_read(6,...) -> FUN_806d50c0 ->
    # spi_write_wait_for_ack_then_read, which programs MCSPI1 (0x48098000) CH2CONF @0x54 /
    # CH2CTRL @0x5C (memory `omap-iob-transport` "ISP1=t7 ISP2=t8 IOB=iobspi FPGA via CS mux";
    # #70's "McSPI1-ch2"). Binding it here lets the firmware's own frame reach the seeded
    # link-up registers.
    SPI_TARGET_MAP: Dict[str, Tuple[int, int, int]] = {
        "ts_isp1": (1, 0, SPI_TARGET_ISP1),
        "ts_isp2": (1, 1, SPI_TARGET_ISP2),
        "ts_iob0": (3, 0, SPI_TARGET_IOB),
        "ts_iob1": (3, 1, SPI_TARGET_IOB_SLAVE),
        "ts_fpga": (1, 2, IOMAP_CHANNEL_LSB_FPGA),
    }

    def __init__(self, intc=None, mcspi: Optional[Dict[int, OmapMcspi]] = None, gpio=None):
        self.intc = intc
        self.gpio = gpio
        self.spi_targets: Dict[str, CoprocessorSpiTarget] = {}
        self.mcspi_controllers: Dict[int, OmapMcspi] = {}
        self._init_spi_targets()
        self.mailbox = IobMailboxDevice(intc=intc)
        self.iob_ahb = IobXauiAhbDevice(xaui_link_up=True)
        self.side_sata_bridge = MarvellSideSataBridge()
        if mcspi:
            self.bind_mcspi(mcspi, gpio=gpio)

    def _init_spi_targets(self):
        for name, (port_id, channel, target_id) in self.SPI_TARGET_MAP.items():
            tgt = CoprocessorSpiTarget(
                name=name, port_id=port_id, channel=channel,
                spi_target_id=target_id, transport=self,
            )
            if name == "ts_fpga":
                tgt.reg_set(self.LSB_FPGA_VERSION_ALT_ADDR, self.LSB_FPGA_VERSION_ALT)
                tgt.reg_set(self.LSB_FPGA_VERSION_ADDR, self.LSB_FPGA_VERSION)
                # Seed the XAUI link-up status regs (fpga side of the IOB<->LSB-FPGA link).
                from emulator.devices.iob_xaui import FPGA_LINK_UP_REGS
                for addr, val in FPGA_LINK_UP_REGS.items():
                    tgt.reg_set(addr, val)
                tgt.reg_set(self.LSB_FPGA_BITSTREAM_STAT_ADDR, self.LSB_FPGA_BITSTREAM_READY)
                tgt.reg_set(self.LSB_FPGA_SSD_DETECT_ADDR, 0x00000000)
                tgt.reg_set(self.LSB_FPGA_SIDE_SATA_CTRL_ADDR, 0x00000001)
            self.spi_targets[name] = tgt

    def set_ssd_present(self, present: bool) -> None:
        """Update HBA0 SATA3 PxSSTS + LSB-FPGA SSD detect bit 18 (0x44A0000C)."""
        p = bool(present)
        self.iob_ahb.sata_shadow_sregs_enabled = True
        self.iob_ahb.sata_media_present = p
        self.side_sata_bridge.media_present = p
        fpga = self.spi_targets.get("ts_fpga")
        if fpga is not None:
            cur = fpga.reg_get(self.LSB_FPGA_SSD_DETECT_ADDR)
            new_val = (cur & ~0x00040000) if p else (cur | 0x00040000)
            fpga.reg_set(self.LSB_FPGA_SSD_DETECT_ADDR, new_val)

    def bind_mcspi(self, mcspi_controllers: Dict[int, OmapMcspi], gpio=None):
        self.mcspi_controllers = mcspi_controllers
        if gpio is not None:
            self.gpio = gpio
        for name, target in self.spi_targets.items():
            ctrl = mcspi_controllers.get(target.port_id)
            if ctrl is not None:
                ctrl.attach_target(target.channel, target)
        # Firmware descriptor table @ 0x814743D8 routes SPI target 6 (LSB-FPGA) to
        # McSPI1 (bus 0) channel 2, and targets 7/8 (ISP1/ISP2) to McSPI1 channel 0
        # muxed by GPIO 76/77. Keep the legacy bindings above and also attach these FW paths.
        mcspi1 = mcspi_controllers.get(1)
        if mcspi1 is not None:
            if "ts_fpga" in self.spi_targets:
                mcspi1.attach_target(2, self.spi_targets["ts_fpga"])
            if "ts_isp1" in self.spi_targets and "ts_isp2" in self.spi_targets:
                mux = CsMuxSpiTarget(
                    targets_by_sel={
                        (0, 1): self.spi_targets["ts_isp1"],
                        (1, 0): self.spi_targets["ts_isp2"],
                    },
                    gpio=self.gpio,
                    fallback=self.spi_targets["ts_isp1"],
                )
                mcspi1.attach_target(0, mux)

    def _target(self, target_name: str) -> CoprocessorSpiTarget:
        tgt = self.spi_targets.get(target_name.strip().lower())
        if tgt is None:
            raise KeyError(f"Unknown SPI target: {target_name}")
        return tgt

    # --- firmware-faithful register primitives (byte frame) ---------------------
    def spi_write_reg(self, target_name: str, addr: int, data: int) -> None:
        """Clock a real WRITE frame `[0x81][addr u32][data u32]` into a target."""
        tgt = self._target(target_name)
        tgt.reset_frame()
        frame = bytes([SPI_OP_WRITE]) + (addr & 0xFFFFFFFF).to_bytes(4, "little") \
            + (data & 0xFFFFFFFF).to_bytes(4, "little")
        for b in frame:
            tgt.transceive_byte(b)

    def spi_read_reg(self, target_name: str, addr: int) -> int:
        """Clock a real READ frame `[0x01][addr u32]` + ACK 0x1F + 4B; return the u32."""
        tgt = self._target(target_name)
        tgt.reset_frame()
        for b in bytes([SPI_OP_READ]) + (addr & 0xFFFFFFFF).to_bytes(4, "little"):
            tgt.transceive_byte(b)
        # Poll for ACK (<=10), then shift out 4 data bytes.
        ack = 0x00
        for _ in range(SPI_ACK_MAX_POLLS):
            ack = tgt.transceive_byte(0x00)
            if ack == SPI_ACK:
                break
        if ack != SPI_ACK:
            raise TimeoutError(f"SPI read ACK 0x1F not seen for {target_name} @ 0x{addr:08X}")
        data = bytes(tgt.transceive_byte(0x00) for _ in range(4))
        return int.from_bytes(data, "little")

    # --- convenience: `read spi <target> <addr> <len>` shell semantics ----------
    def spi_read(self, target_name: str, addr: int, length: int = 4) -> bytes:
        out = bytearray()
        for off in range(0, length, 4):
            out.extend(self.spi_read_reg(target_name, addr + off).to_bytes(4, "little"))
        return bytes(out[:length])

    def spi_write(self, target_name: str, addr: int, data: bytes) -> bool:
        for off in range(0, len(data), 4):
            chunk = data[off:off + 4].ljust(4, b"\x00")
            self.spi_write_reg(target_name, addr + off, int.from_bytes(chunk, "little"))
        return True

    def get_status(self) -> Dict[str, Any]:
        return {
            "transport": "SPI register frame [0x81/0x01][addr][data], ACK 0x1F",
            "spi_targets": {
                name: {
                    "spi_target_id": t.spi_target_id,
                    "transfers": t.transfer_count,
                    "last_write": t.last_write,
                    "last_read": t.last_read,
                }
                for name, t in self.spi_targets.items()
            },
            "gpmc_mailbox_base": f"0x{IobMailboxDevice.BASE_ADDR:08X}",
            "gpmc_mailbox_writes": len(self.mailbox.command_log),
        }


class IobDoorbellDevice(Device):
    """
    Tensilica IOB-local shared-memory doorbell MMIO (IOB0 @0x3FFE8000, IOB1 @0x4FFE8000),
    in the Xtensa DRAM region. A write to +0x04 rings the doorbell: the model fetches a
    16-byte descriptor from the shared DDR mailbox (0x800B0FFC / 0x800B17FC) and dispatches
    it to the Xtensa core's handle_c2c_command.

    This is a local DDR-descriptor doorbell (how the OMAP kicks the IOB after writing a
    descriptor to shared memory), distinct from both the SPI register frame (regs) and the
    GPMC AHB mailbox @0xA5800000. It carries no SPI register protocol.
    """

    BASE_ADDR_IOB0 = 0x3FFE8000
    BASE_ADDR_IOB1 = 0x4FFE8000
    SIZE = 0x1000

    def __init__(self, core_id: int, bus, coprocessor, mailbox_base_addr: int):
        self.core_id = core_id
        self.bus = bus
        self.coprocessor = coprocessor
        self.mailbox_base_addr = mailbox_base_addr
        self.doorbell_count = 0
        self.last_command = 0
        self.last_payload: List[int] = []

    def read(self, offset: int, size: int) -> int:
        if offset == 0x00:
            return 0x01  # Doorbell ready / enabled
        elif offset == 0x04:
            return self.doorbell_count
        return 0

    def write(self, offset: int, size: int, val: int):
        if offset == 0x04:
            self.doorbell_count = val & 0xFFFFFFFF
            try:
                raw = self.bus.read_mem(self.mailbox_base_addr, 16)
                cmd, arg0, arg1, arg2 = struct.unpack("<4I", raw)
                self.last_command = cmd
                self.last_payload = [arg0, arg1, arg2]
                if self.coprocessor and hasattr(self.coprocessor, "handle_c2c_command"):
                    self.coprocessor.handle_c2c_command(cmd, [arg0, arg1, arg2])
            except Exception:
                pass


class MarvellSideSataBridge:
    """
    Side-SATA Marvell serial register bridge + Silicon Motion SM2246EN MINI-MAG identity.

    Accepts the `"W<reg:04X><data:08X>"` / `"R<reg:04X>"` command framing used on the
    HBA1 side-SATA serial bridge and exposes ATA IDENTIFY / SMART log `0x90` metadata.
    """

    def __init__(self, model: str = "RED MINI-MAG 512GB", controller: str = "SM2246EN"):
        self.model = model
        self.controller = controller
        self.media_present: bool = True
        self.regs: Dict[int, int] = {
            0x0000: 0x1B4B9230,  # Marvell bridge vendor/device ID
            0x0004: 0x00000133,  # SATA Gen3 link status
        }

    def execute_serial_command(self, cmd: str) -> str:
        """Handle Marvell `'W<reg:04X><data:08X>'` or `'R<reg:04X>'` bridge command."""
        s = cmd.strip().upper()
        if s.startswith("W") and len(s) >= 13:
            reg = int(s[1:5], 16)
            val = int(s[5:13], 16)
            self.regs[reg] = val & 0xFFFFFFFF
            return "OK"
        if s.startswith("R") and len(s) >= 5:
            reg = int(s[1:5], 16)
            val = self.regs.get(reg, 0x00000133 if reg == 0x0004 and self.media_present else 0)
            return f"{val:08X}"
        return "ERR"

    def get_smart_log_90(self) -> Dict[str, Any]:
        return {
            "controller": self.controller,
            "model": self.model,
            "media_present": self.media_present,
            "auth_signature": "RED_SM2246EN_SMART90_OK",
            "health_pct": 100,
        }


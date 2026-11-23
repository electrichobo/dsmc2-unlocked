"""
Texas Instruments OMAP3630 / DM3725 General Purpose Memory Controller (GPMC).
Base address: 0x6E000000, Size: 0x1000.
Interfaces external NAND flash and parallel FPGA registers on the RED DSMC2 platform.
"""

from typing import Dict, Any, Optional
from collections import deque
from emulator.core.bus import Device


class OmapGpmc(Device):
    """
    OMAP3630 GPMC Controller.
    Handles CS0 NAND flash command, address, and data windows with BCH ECC calculation,
    and CS1/CS2 FPGA register spaces.
    """

    BASE_ADDR = 0x6E000000
    SIZE = 0x1000

    # Micron MT29F8G08 geometry (matches the firmware RAM descriptor @0x80FCEBF6 that vbrain
    # seeds: page=2048, spare=64, 64 pages/block, 2048 blocks = 256MB SLC).
    PAGE_SIZE = 2048
    SPARE_SIZE = 64
    PAGE_FRAME = PAGE_SIZE + SPARE_SIZE   # 2112: one physical page (data + OOB) as read/programmed
    PAGES_PER_BLOCK = 64
    TOTAL_BLOCKS = 2048

    # Key GPMC Register Offsets
    REG_SYSCONFIG = 0x10
    REG_SYSSTATUS = 0x14
    REG_IRQSTATUS = 0x18
    REG_IRQENABLE = 0x1C
    REG_TIMEOUT_CONTROL = 0x40
    REG_ERR_ADDRESS = 0x44
    REG_ERR_TYPE = 0x48

    # Chip Select 0 (NAND) Registers
    REG_CONFIG1_0 = 0x60
    REG_CONFIG2_0 = 0x64
    REG_CONFIG3_0 = 0x68
    REG_CONFIG4_0 = 0x6C
    REG_CONFIG5_0 = 0x70
    REG_CONFIG6_0 = 0x74
    REG_CONFIG7_0 = 0x78
    REG_NAND_CMD_0 = 0x7C
    REG_NAND_ADDRESS_0 = 0x80
    REG_NAND_DATA_0 = 0x84

    # Prefetch and Write-Posting Engine
    REG_PREFETCH_CONFIG1 = 0x1E0
    REG_PREFETCH_CONFIG2 = 0x1E4
    REG_PREFETCH_CONTROL = 0x1EC
    REG_PREFETCH_STATUS = 0x1F0

    # ECC Hardware Engine
    REG_ECC_CONFIG = 0x1F4
    REG_ECC_CONTROL = 0x1F8
    REG_ECC_SIZE_CONFIG = 0x1FC
    REG_ECC1_RESULT = 0x200

    def __init__(self, intc=None):
        self.intc = intc
        self.sysconfig = 0x00
        self.sysstatus = 0x01  # RDONE = 1 (Reset complete)
        self.irqstatus = 0x00
        self.irqenable = 0x00
        self.timeout_control = 0x00

        # CS0 Configuration (NAND 256MB window at 0x08000000)
        self.cs_configs: Dict[int, int] = {
            self.REG_CONFIG1_0: 0x00000800, # 16-bit, asynchronous
            self.REG_CONFIG7_0: 0x00000F48  # CS0 valid, base=0x08000000
        }

        # NAND Flash transaction state
        self.nand_cmd = 0x00
        self.nand_addr = 0x00
        self.nand_buffer: deque = deque()
        # ONFI/Micron feature registers (indexed by feature address). Feature 0x01 = timing mode.
        # Feature 0x90 = Micron on-die ECC enable/status: byte0 bit3 (0x08) = ECC enabled. The
        # firmware's _nand_set_internal_ecc (@0x8067b984) issues SET FEATURES(0x90)=[8,..] then
        # GET FEATURES(0x90) and requires the readback to equal what it wrote, else every
        # _nand_read_sector returns 0x702c and the YAFFS mount fails (bit 0x8 never set). So SET
        # FEATURES must latch the written bytes (see _handle_nand_command / write()).
        self.features: Dict[int, list] = {
            0x01: [0x00, 0x00, 0x00, 0x00], # Timing mode feature
            0x90: [0x00, 0x00, 0x00, 0x00], # Micron on-die ECC (disabled until firmware enables)
        }
        # Pending SET FEATURES: after a 0xEF command + address cycle, the next data-register
        # byte writes carry the 4 feature bytes to latch into ``features[set_feature_addr]``.
        self._set_feature_pending = False
        self._set_feature_addr = 0x00
        self._set_feature_bytes: list = []

        # Address-cycle accumulator. The firmware issues a column/row address as a sequence of
        # single-byte writes to GPMC_NAND_ADDRESS_0 between the setup (0x00/0x05/0x85/0xEF/0xEE)
        # and confirm commands. _nand_read_sector (@0x8067ccc8) sends 2 column + 3 row (page)
        # bytes; the row selects the page in the backing store.
        self._addr_cycles: list = []

        # Backing NAND array (Micron MT29F8G08: 2048 blocks x 64 pages x (2048+64) bytes). A real
        # erased chip (and a freshly formatted partition) reads all-0xFF, which YAFFS2 reads as
        # unwritten chunks (packed_tags2 seqNumber == 0xFFFFFFFF). Stored sparsely: only
        # programmed pages consume memory; unmapped pages serve 0xFF on read. Writes land here so
        # the firmware's own nand_media_format / YAFFS writeback are faithful round-trips.
        self.nand_image: Dict[int, bytearray] = {}
        self._active_page = 0          # page index selected by the last read/program address
        self._active_col = 0           # column offset within the (data+spare) page frame

        # Prefetch engine state
        self.prefetch_config1 = 0x00
        self.prefetch_config2 = 0x00
        self.prefetch_control = 0x00

        # Hardware ECC state
        self.ecc_config = 0x00
        self.ecc_control = 0x00
        self.ecc_size_config = 0x00
        self.ecc1_result = 0x00000000
        # GPMC Hamming ECC result registers (0x6E000200 + i*4). The firmware reads these via
        # FUN_8067aab0 only on the raw (param_4!=0) read path used to re-check an already-failed
        # sector; the primary read path (param_4==0) trusts Micron on-die ECC, so these default
        # to 0. Kept as a 4-entry window so FUN_8067aab0's accesses are backed.
        self.ecc_result: list = [0x00000000, 0x00000000, 0x00000000, 0x00000000]

    def _page_frame(self, page: int) -> bytearray:
        """Return the mutable (data+spare) 2112-byte frame for ``page`` (0xFF-filled if unwritten)."""
        frame = self.nand_image.get(page)
        if frame is None:
            frame = bytearray(b"\xFF" * self.PAGE_FRAME)
            self.nand_image[page] = frame
        return frame

    def program_page(self, page: int, data: bytes, spare: Optional[bytes] = None):
        """Write a full page (and optional spare/OOB) into the backing store.

        Used by the NAND image builder to stage a mountable YAFFS2 layout, and by the firmware's
        own page-program path. NAND programming only clears bits, but the builder writes fresh
        frames, so this stores the bytes verbatim over an erased (0xFF) frame.
        """
        frame = bytearray(b"\xFF" * self.PAGE_FRAME)
        frame[0 : min(len(data), self.PAGE_SIZE)] = data[: self.PAGE_SIZE]
        if spare is not None:
            frame[self.PAGE_SIZE : self.PAGE_SIZE + min(len(spare), self.SPARE_SIZE)] = \
                spare[: self.SPARE_SIZE]
        self.nand_image[page] = frame

    def erase_block(self, block: int):
        """Erase a block: drop its 64 pages back to the erased (all-0xFF) state."""
        base = block * self.PAGES_PER_BLOCK
        for p in range(base, base + self.PAGES_PER_BLOCK):
            self.nand_image.pop(p, None)

    def _load_read_frame(self):
        """Stage the active page's (data+spare) frame into the serial read FIFO from ``_active_col``."""
        frame = self.nand_image.get(self._active_page)
        if frame is None:
            # Unwritten/erased page reads all-0xFF (YAFFS2 sees an empty chunk).
            tail = self.PAGE_FRAME - (self._active_col % self.PAGE_FRAME)
            self.nand_buffer.extend([0xFF] * tail)
        else:
            self.nand_buffer.extend(frame[self._active_col :])

    def drain_prefetch(self, nbytes: int) -> bytes:
        """Pop ``nbytes`` from the NAND serial read FIFO for a prefetch+DMA transfer.

        The firmware reads NAND through the GPMC prefetch engine feeding sDMA (``_nand_dma_copy``
        @0x8067cb4c: ``GPMC_PREFETCH_CONFIG2 = byteCount; GPMC_PREFETCH_CONTROL = 1``; a DMA channel
        then drains the prefetch FIFO into SRAM). The FIFO was staged by the ``0x30`` read-confirm
        at the active page/column, so this returns exactly those page/OOB bytes; a short/empty FIFO
        (unwritten page) is padded with 0xFF, matching an erased chip.
        """
        out = bytearray()
        for _ in range(nbytes):
            if self.nand_buffer:
                out.append(self.nand_buffer.popleft())
            else:
                out.append(0xFF)
        # The prefetch engine services one armed transfer then stops (CONTROL start bit self-clears
        # when the programmed count is exhausted). Clear it so a later non-NAND sDMA transfer is not
        # misrouted to the (now empty) NAND FIFO -- leaving it armed starved FileX/media DMA of its
        # own fill and regressed the FileX init-flag (0x2).
        self.prefetch_control &= ~0x01
        return bytes(out)

    @property
    def prefetch_armed(self) -> bool:
        """True while a NAND prefetch read transfer is armed (CONTROL start bit set).

        Set by ``GPMC_PREFETCH_CONTROL = 1`` in ``_nand_dma_copy`` and cleared once the transfer's
        bytes are drained, so only the NAND read DMA (not unrelated sDMA channels) pulls from the
        GPMC FIFO.
        """
        return bool(self.prefetch_control & 0x01)

    def read(self, offset: int, size: int) -> int:
        """Handle 8/16/32-bit MMIO reads from GPMC register space."""
        if offset == self.REG_SYSSTATUS:
            return self.sysstatus
        elif offset == 0x54: # GPMC_STATUS (WAIT0STATUS = 1, EMPTYWRITEBUFFER = 1)
            return 0x00000101
        elif offset == self.REG_SYSCONFIG:
            return self.sysconfig
        elif offset == self.REG_IRQSTATUS:
            return self.irqstatus
        elif offset == self.REG_IRQENABLE:
            return self.irqenable
        elif offset == self.REG_NAND_DATA_0:
            # Read Status (0x70) serves the ready/pass byte without draining the data/feature FIFO.
            # The firmware reads status as a single byte (FUN_8067bd20: GPMC_NAND_DATA_0._0_1_),
            # so the status lives in the low byte regardless of access width.
            if self.nand_cmd == 0x70:
                return 0xE0  # Ready(b6)=1, !WP(b7)=1, Pass(b0)=0 — last op succeeded (on-die ECC OK)
            val = 0
            for i in range(size):
                if self.nand_buffer:
                    b = self.nand_buffer.popleft()
                elif self.nand_cmd in (0x00, 0x30, 0x23, 0x05, 0xE0):
                    b = 0xFF # Blank unprogrammed NAND flash reads 0xFF
                else:
                    b = 0x00
                val |= (b << (i * 8))
            return val
        elif offset == self.REG_PREFETCH_CONFIG1:
            return self.prefetch_config1
        elif offset == self.REG_PREFETCH_CONFIG2:
            return self.prefetch_config2
        elif offset == self.REG_PREFETCH_CONTROL:
            return self.prefetch_control
        elif offset == self.REG_PREFETCH_STATUS:
            return 0x00000000
        elif offset == self.REG_ECC_CONFIG:
            return self.ecc_config
        elif offset == self.REG_ECC_CONTROL:
            return self.ecc_control
        elif offset == self.REG_ECC_SIZE_CONFIG:
            return self.ecc_size_config
        elif offset == self.REG_ECC1_RESULT:
            return self.ecc1_result
        elif self.REG_ECC1_RESULT <= offset < self.REG_ECC1_RESULT + 0x24:
            # GPMC_ECCi_RESULT window (0x200..0x220); firmware FUN_8067aab0 reads these as the
            # per-512B Hamming code on the raw re-check path.
            idx = (offset - self.REG_ECC1_RESULT) // 4
            return self.ecc_result[idx] if 0 <= idx < len(self.ecc_result) else 0x00000000
        elif offset in self.cs_configs:
            return self.cs_configs[offset]

        return 0x00000000

    def write(self, offset: int, size: int, val: int):
        """Handle 8/16/32-bit MMIO writes to GPMC register space."""
        if offset == self.REG_SYSCONFIG:
            self.sysconfig = val
            if val & 0x02: # Soft reset
                self.sysstatus = 0x01
        elif offset == self.REG_IRQSTATUS:
            self.irqstatus &= ~val # Write 1 to clear
        elif offset == self.REG_IRQENABLE:
            self.irqenable = val
        elif offset == self.REG_NAND_CMD_0:
            self.nand_cmd = val & 0xFF
            self._handle_nand_command(self.nand_cmd)
        elif offset == self.REG_NAND_ADDRESS_0:
            # Address cycles arrive as a run of single-byte writes. Keep ``nand_addr`` as the last
            # byte (feature/ID commands use it) and accumulate the full column/row sequence.
            self.nand_addr = val & 0xFF
            for i in range(size):
                self._addr_cycles.append((val >> (i * 8)) & 0xFF)
            if self._set_feature_pending:
                self._set_feature_addr = self._addr_cycles[0]
        elif offset == self.REG_NAND_DATA_0:
            if self._set_feature_pending:
                # SET FEATURES parameter bytes (P1..P4) follow the 0xEF command + address cycle.
                for i in range(size):
                    self._set_feature_bytes.append((val >> (i * 8)) & 0xFF)
                if len(self._set_feature_bytes) >= 4:
                    self.features[self._set_feature_addr] = self._set_feature_bytes[:4]
                    self._set_feature_pending = False
                return
            for i in range(size):
                b = (val >> (i * 8)) & 0xFF
                self.nand_buffer.append(b)
            # Update ECC calculation
            self.ecc1_result ^= val
        elif offset == self.REG_PREFETCH_CONFIG1:
            self.prefetch_config1 = val
        elif offset == self.REG_PREFETCH_CONFIG2:
            self.prefetch_config2 = val
        elif offset == self.REG_PREFETCH_CONTROL:
            self.prefetch_control = val
        elif offset == self.REG_ECC_CONFIG:
            self.ecc_config = val
        elif offset == self.REG_ECC_CONTROL:
            self.ecc_control = val
            if val & 0x01: # Clear ECC
                self.ecc1_result = 0x00000000
        elif offset == self.REG_ECC_SIZE_CONFIG:
            self.ecc_size_config = val
        elif offset in (self.REG_CONFIG1_0, self.REG_CONFIG2_0, self.REG_CONFIG3_0,
                        self.REG_CONFIG4_0, self.REG_CONFIG5_0, self.REG_CONFIG6_0,
                        self.REG_CONFIG7_0):
            self.cs_configs[offset] = val

    def _page_from_addr_cycles(self):  # -> (page, byte_column)
        """Decode the accumulated address cycles into (page, byte_column).

        _nand_read_sector (@0x8067ccc8) issues 2 column bytes then 3 row (page) bytes. The row is
        the absolute page number. The DSMC2 NAND is a **16-bit (x16)** part (GPMC CONFIG1_0 =
        0x800), so the column address is in **16-bit WORDS**, not bytes: the firmware reads the OOB
        by addressing word ``pageBytes>>1`` (``2048>>1 = 1024``), which is byte offset ``2048`` --
        the start of the spare area. We convert the decoded word column to a byte offset (``<< 1``)
        so it indexes correctly into the byte-addressed (data+spare) frame; without this an OOB
        read at word 1024 would wrongly serve data bytes at offset 1024 (mid-page), the tags would
        decode as erased (0xFFFFFFFF), and every YAFFS2 header/data chunk would be discarded.
        """
        cyc = self._addr_cycles
        word_col = 0
        page = 0
        if len(cyc) >= 2:
            word_col = cyc[0] | (cyc[1] << 8)
        if len(cyc) >= 5:
            page = cyc[2] | (cyc[3] << 8) | (cyc[4] << 16)
        elif len(cyc) >= 3:  # program/erase may send fewer column bytes
            page = cyc[-3] | (cyc[-2] << 8) | (cyc[-1] << 16)
        return page, word_col << 1

    def _handle_nand_command(self, cmd: int):
        """Service standard ONFI / Micron NAND commands."""
        if cmd == 0x90: # Read ID
            # Returns Manufacturer ID 0x2C (Micron) and Device ID 0xBA (256MB SLC)
            self._addr_cycles = []
            self.nand_buffer.clear()
            self.nand_buffer.extend([0x2C, 0xBA, 0x90, 0xA5, 0x15])
        elif cmd == 0x70: # Read Status
            # Status (0xE0: Ready bit6, !write-protect bit7, Pass bit0=0) is served from a
            # dedicated 1-byte path (see read()); it must NOT clear a pending data/feature FIFO,
            # because FUN_8067bd20 issues 0x70 mid-sequence inside GET FEATURES and page reads.
            pass
        elif cmd == 0xEE: # Get Features
            self.nand_buffer.clear()
            feat = self.features.get(self.nand_addr, [0x00, 0x00, 0x00, 0x00])
            self.nand_buffer.extend(feat)
        elif cmd == 0xEF: # Set Features
            # Begin latching: the feature address (next ADDRESS write) and 4 parameter bytes
            # (next DATA writes) are captured into ``features`` so GET FEATURES round-trips.
            self.nand_buffer.clear()
            self._addr_cycles = []
            self._set_feature_pending = True
            self._set_feature_addr = 0x00
            self._set_feature_bytes = []
        elif cmd == 0xEC: # Read Parameter Page (ONFI)
            self._addr_cycles = []
            self.nand_buffer.clear()
            onfi_page = bytearray(256)
            onfi_page[0:4] = b"ONFI"
            onfi_page[4:6] = (2).to_bytes(2, "little") # Revision 2.0
            onfi_page[80:84] = (2048).to_bytes(4, "little") # 2048 bytes per page
            onfi_page[84:86] = (64).to_bytes(2, "little") # 64 OOB bytes per page
            onfi_page[92:96] = (64).to_bytes(4, "little") # 64 pages per block
            onfi_page[96:100] = (2048).to_bytes(4, "little") # 2048 blocks per LUN
            self.nand_buffer.extend(onfi_page)
        elif cmd == 0xFF: # Reset
            self._addr_cycles = []
            self._set_feature_pending = False
            self.nand_buffer.clear()
        elif cmd == 0x00:
            # 0x00 is both "read setup" (followed by address + 0x30 confirm) and the firmware's
            # idle "COMMAND = 0" after a status/feature op. Only reset the address accumulator; do
            # NOT clear the FIFO, so GET FEATURES bytes staged by 0xEE survive the FUN_8067bd20
            # status poll + the trailing COMMAND=0 before they are read.
            self._addr_cycles = []
        elif cmd in (0x30, 0x23, 0x05, 0xE0): # Read Confirm / Random Data Read / Change Read Column
            # Resolve the selected page and column, then stage its data+spare frame into the FIFO.
            self._active_page, self._active_col = self._page_from_addr_cycles()
            self.nand_buffer.clear()
            self._load_read_frame()
        elif cmd in (0x60, 0xD0): # Block Erase Setup / Confirm
            if cmd == 0xD0 and len(self._addr_cycles) >= 1:
                # Erase address is a row (page) address; map to its block.
                page, _ = self._page_from_addr_cycles()
                if page == 0 and len(self._addr_cycles) >= 3:
                    page = self._addr_cycles[0] | (self._addr_cycles[1] << 8) | (self._addr_cycles[2] << 16)
                self.erase_block(page // self.PAGES_PER_BLOCK)
            self._addr_cycles = []
            self.nand_buffer.clear()
        elif cmd == 0x80: # Page Program Setup: address + data bytes will follow
            self._addr_cycles = []
            self.nand_buffer.clear()
        elif cmd == 0x10: # Page Program Confirm: commit the buffered data to the active page
            self._active_page, self._active_col = self._page_from_addr_cycles()
            frame = self._page_frame(self._active_page)
            pos = self._active_col
            while self.nand_buffer and pos < self.PAGE_FRAME:
                frame[pos] &= self.nand_buffer.popleft()  # NAND program only clears bits
                pos += 1
            self._addr_cycles = []
            self.nand_buffer.clear()

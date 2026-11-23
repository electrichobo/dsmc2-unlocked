"""
IOB / LSB-FPGA XAUI link model (the "left-side board" high-speed link bring-up).

Models the two register files the firmware polls to decide the IOB<->LSB-FPGA XAUI link is
UP, so the real ``weapon_left_side_board_init`` (@0x809B5358) bring-up sequence completes and
its async-group bit (0x10) sets, unblocking ``weapon_boards_init`` and the board-dependent
init-event flags (Boards | Fan | XAUI | Mailbox).

This is a *register-state* model, faithful to the reconstructed C and the sdk findings; it does
NOT execute the IOB Xtensa firmware. The firmware's own PHY/PCS bring-up sequence drives the
registers and then polls these status words, so presenting link-up values makes that sequence
succeed without faking any predicate.

Two transports carry the status (ground truth: sdk ``clean/lsb_fpga.c`` +
``clean/weapon_left_side_board.c``; ``findings/peripherals/LSB_FPGA_{CHARACTERIZED,NAMED}.md``;
memory ``omap-iob-transport`` / ``xaui-lane-sharing-sata-mon0``):

  1. IOB side -- an AHB aperture. ``FUN_80727028(addr)`` reads ``*((addr & 0x3FFFFFF) |
     0x1C000000)``: a 1:1 window onto the IOB register space, so a port base of 0x80020000
     (EXP1) or 0x80060000 (MON0) lands at window offset 0x20000 / 0x60000. The XAUI PCS/status
     block within each port (offsets relative to the port base):

        +0x010  IOB XAUI status   -- PHY-ready bit 0x20000000 set; RX-fault bit 0x40000 clear
        +0x02C  IOB PCS status    -- link-up constant 0x03FFC02F (``pcs != 0x3ffc02f`` => bad)
        +0x068  IOB-RX pkt-len err-- 0 when healthy
        +0x0BC  IOB-RX CRC err    -- 0 when healthy
        +0x248  PHY_CLK_CTRL      -- PLL-ready bit 0x00100000 set (``lsb_fpga_xaui_phy_initx``)

     ``report_xaui_status`` (@0x809BCF64) reads +0x2C/+0x10/+0x68/+0xBC; ``lsb_fpga_xaui_phy_initx``
     additionally polls +0x248 bit 0x100000 (PLL ready) and +0x10 bit 0x20000000 (PHY ready).

  2. FPGA side -- the LSB-FPGA register file over the McSPI1-ch2 / CS-mux-6 ``fpga_read``
     byte frame ([0x81][addr][data] write / [0x01][addr]+ACK 0x1F read; sdk COPROCESSOR_TRANSPORT,
     memory ``omap-iob-transport``, PR #66). ``report_xaui_status`` reads three FPGA regs via
     ``FUN_809ba680`` -> ``fpga_read(6, addr)``:

        0x40010008  FPGA PCS/link status -- (v>>3 & 0xff) must == 0xFC (LinkUp/Aligned/Sync)
        0xC2000010  FPGA XAUI status     -- RX-fault bit 0x40000 clear
        0xC20000BC  FPGA-RX CRC err      -- 0

     These are seeded on the ``ts_fpga`` SPI target (see ``coprocessors/c2c.py``).

Only the IOB AHB-window half lives here; the FPGA-side seed values are constants shared with the
SPI transport (``FPGA_LINK_UP_REGS``).
"""

from typing import Dict


# ---- IOB AHB-window XAUI port register block (offsets within a 0x40000 port) ----------------
# Each XAUI port (EXP1 @ window 0x20000, MON0 @ window 0x60000) exposes the same block.
IOB_PCS_LINK_UP = 0x03FFC02F        # +0x2C: PCS status when the link is up
IOB_XAUI_PHY_READY = 0x20000000     # +0x10: PHY-ready set, RX-fault (0x40000) clear
IOB_PHYCLK_PLL_READY = 0x00100000   # +0x248: PLL-ready bit polled during phy_init

# Per-port register offset -> value presented when the link is UP. Any offset not listed reads 0
# (the firmware only gates on these; other reads in the block are logged/ignored).
_PORT_LINK_UP_REGS: Dict[int, int] = {
    0x010: IOB_XAUI_PHY_READY,
    0x02C: IOB_PCS_LINK_UP,
    0x068: 0x00000000,
    0x0BC: 0x00000000,
    0x248: IOB_PHYCLK_PLL_READY,
}

# The two XAUI port blocks the firmware may select (``lsb_fpga_using_MON0``): EXP1 (non-0x18
# cameras with IOB board rev < 0x33) and MON0 (CAMERA_TYPE 0x18 or rev >= 0x33). Modelling both
# makes the link come up regardless of which base the running firmware picks.
WINDOW_BASE = 0x1C000000
WINDOW_SIZE = 0x00100000
PORT_EXP1_OFF = 0x20000            # 0x80020000 & 0x3FFFFFF (EXP1 port base in the AHB window)
PORT_MON0_OFF = 0x60000            # 0x80060000 & 0x3FFFFFF (MON0 port base in the AHB window)
# The XAUI register block sits in the low part of each port base; all polled regs are <= 0x248,
# so an offset belongs to a port when it is within PORT_BLOCK_SPAN of that base.
PORT_BLOCK_SPAN = 0x1000            # register block size within a port (regs up to 0x248)
_XAUI_PORT_BASES = (PORT_EXP1_OFF, PORT_MON0_OFF)


# ---- FPGA-side (McSPI ch6) link-up register seeds, shared with the SPI transport -----------
# 0x40010008: (v>>3 & 0xff) must == 0xFC  ->  0x7E0 gives (0x7E0>>3)&0xff = 0xFC
FPGA_PCS_LINK_UP = 0x000007E0
FPGA_LINK_UP_REGS: Dict[int, int] = {
    0x40010008: FPGA_PCS_LINK_UP,   # LinkUp=1 Aligned=1 Sync=1111 RxFault=0 TxFault=0
    0xC2000010: 0x00000000,         # FPGA XAUI status: RX-fault bit 0x40000 clear
    0xC20000BC: 0x00000000,         # FPGA-RX CRC errors = 0
}


class IobXauiWindow:
    """IOB AHB XAUI aperture @0x1C000000 (0x100000), presenting an UP link on both port blocks.

    Installed as a Unicorn MEM_READ hook (the window is below the bus device-hook range), it
    answers the exact status words ``FUN_80727028`` reads so the firmware's XAUI PHY/PCS
    bring-up and ``lsb_fpga_report_xaui_status`` see the link come up. Writes into the window
    (PHY config / latch-clear) are left to RAM: the model reports ready/good independently of
    them, exactly as a brought-up link would.
    """

    def __init__(self, link_up: bool = True):
        self.link_up = link_up
        self.read_count = 0
        self.status_reads: Dict[int, int] = {}

    def _reg_off(self, window_offset: int):
        """Map a window offset to (port_base, reg_off) if it lands on a modelled XAUI port
        block's status area, else (None, None)."""
        for base in _XAUI_PORT_BASES:
            if base <= window_offset < base + PORT_BLOCK_SPAN:
                return base, window_offset - base
        return None, None

    def value_for(self, window_offset: int) -> int:
        """Return the link-up register value for an offset within the 0x1C000000 window.

        Only the XAUI status registers of the two modelled port blocks (EXP1/MON0) are answered;
        anything else reads 0 (handled as RAM by the caller via ``is_status_reg``)."""
        port_base, reg_off = self._reg_off(window_offset)
        if port_base is None or not self.link_up:
            return 0x00000000
        return _PORT_LINK_UP_REGS.get(reg_off, 0x00000000)

    def is_status_reg(self, window_offset: int) -> bool:
        port_base, reg_off = self._reg_off(window_offset)
        return port_base is not None and reg_off in _PORT_LINK_UP_REGS

    def read(self, window_offset: int, size: int) -> int:
        self.read_count += 1
        _, reg_off = self._reg_off(window_offset)
        if reg_off is not None:
            self.status_reads[reg_off] = self.status_reads.get(reg_off, 0) + 1
        return self.value_for(window_offset)

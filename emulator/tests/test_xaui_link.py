"""
IOB / LSB-FPGA XAUI link model (milestone 2 board bring-up).

Validates the register-state model that presents an UP IOB<->LSB-FPGA XAUI link on exactly the
status words the firmware's ``lsb_fpga_report_xaui_status`` (@0x809BCF64) polls, so the real
``weapon_left_side_board_init`` (@0x809B5358) XAUI bring-up completes once it runs.

Two halves (ground truth: sdk ``clean/lsb_fpga.c`` + ``clean/weapon_left_side_board.c``;
``findings/peripherals/LSB_FPGA_{CHARACTERIZED,NAMED}.md``; memory ``omap-iob-transport``):

  * IOB side -- the AHB aperture at 0x1C000000 (``FUN_80727028`` reads
    ``*((addr & 0x3FFFFFF) | 0x1C000000)``). ``IobXauiWindow`` answers the PCS/PHY status
    registers of whichever port block the firmware selects (EXP1 @ window 0x20000 or MON0 @
    0x60000).
  * FPGA side -- the LSB-FPGA regs read over ``fpga_read(6, addr)``; seeded on the ``ts_fpga``
    SPI target (McSPI1-ch2) by the coprocessor transport.

The firmware-level predicate check (``test_report_xaui_status_sees_link_up``) is firmware-gated;
the model-logic checks run without firmware.
"""

import struct
import pytest

from emulator.devices.iob_xaui import (
    IobXauiWindow,
    FPGA_LINK_UP_REGS,
    IOB_PCS_LINK_UP,
    PORT_EXP1_OFF,
    PORT_MON0_OFF,
)
from emulator.coprocessors.c2c import CoprocessorTransport
from emulator.camera import Dsmc2Camera
from conftest import requires_firmware


# ---- model-logic checks (no firmware) --------------------------------------------------------

def test_iob_window_presents_link_up_on_both_ports():
    """The IOB AHB window answers the link-up PCS/PHY status on both EXP1 and MON0 port blocks.

    ``lsb_fpga_using_MON0`` selects EXP1 (window off 0x20000) or MON0 (0x60000) by CAMERA_TYPE /
    IOB board rev; the model must report UP for whichever the running firmware picks.
    """
    win = IobXauiWindow(link_up=True)
    for port_off in (PORT_EXP1_OFF, PORT_MON0_OFF):
        # +0x2C PCS status == 0x03FFC02F (the ``pcs != 0x3ffc02f`` link-good constant)
        assert win.read(port_off + 0x2C, 4) == 0x03FFC02F == IOB_PCS_LINK_UP
        # +0x10 XAUI status: RX-fault bit 0x40000 CLEAR (PHY-ready 0x20000000 set)
        assert win.read(port_off + 0x10, 4) & 0x40000 == 0
        assert win.read(port_off + 0x10, 4) & 0x20000000 == 0x20000000
        # +0x68 RX pkt-len errors == 0 ; +0xBC RX CRC errors == 0
        assert win.read(port_off + 0x68, 4) == 0
        assert win.read(port_off + 0xBC, 4) == 0
        # +0x248 PHY_CLK_CTRL: PLL-ready bit 0x100000 set (polled by lsb_fpga_xaui_phy_initx)
        assert win.read(port_off + 0x248, 4) & 0x100000 == 0x100000


def test_report_xaui_status_predicate_is_satisfied():
    """Re-evaluate lsb_fpga_report_xaui_status's exact link-good condition against the model.

    From clean/lsb_fpga.c @0x809BCF64 the link is UP iff NOT(crc_err || len_err || pcs_bad) and
    the FPGA side is OK: pcs == 0x3ffc02f, len(+0x68)==0, crc(+0xbc)==0, FPGA 0x40010008
    (v>>3 & 0xff)==0xfc, FPGA 0xC2000010 bit 0x40000 clear, FPGA 0xC20000BC == 0.
    """
    win = IobXauiWindow(link_up=True)
    base = PORT_EXP1_OFF
    pcs = win.read(base + 0x2C, 4)
    len_err = win.read(base + 0x68, 4) != 0
    crc_err = win.read(base + 0xBC, 4) != 0
    pcs_bad = pcs != 0x3FFC02F
    assert not (pcs_bad or len_err or crc_err)

    fpga_pcs = FPGA_LINK_UP_REGS[0x40010008]
    fpga_xaui = FPGA_LINK_UP_REGS[0xC2000010]
    fpga_crc = FPGA_LINK_UP_REGS[0xC20000BC]
    fpga_ok = ((fpga_pcs >> 3) & 0xFF) == 0xFC
    fpga_ok = fpga_ok and (fpga_xaui & 0x40000) == 0
    fpga_ok = fpga_ok and fpga_crc == 0
    assert fpga_ok

    # => lsb_fpga_report_xaui_status returns 0 (link OK) and sets DAT_827559e5 = 1.
    link_ok = not ((crc_err or len_err or pcs_bad) or not fpga_ok)
    assert link_ok


def test_link_down_reports_not_up():
    """A model with link_up=False must NOT present the link-good constant (no silent faking)."""
    win = IobXauiWindow(link_up=False)
    assert win.read(PORT_EXP1_OFF + 0x2C, 4) != 0x03FFC02F


def test_transport_seeds_fpga_link_regs_on_ts_fpga():
    """The coprocessor transport seeds the FPGA-side link-up regs on the ts_fpga SPI target, and
    ts_fpga is bound to McSPI1 channel 2 (the firmware's fpga_read(6,...) transport)."""
    c2c = CoprocessorTransport()
    assert c2c.SPI_TARGET_MAP["ts_fpga"][0] == 1   # McSPI port 1
    assert c2c.SPI_TARGET_MAP["ts_fpga"][1] == 2   # channel 2
    tgt = c2c.spi_targets["ts_fpga"]
    for addr, val in FPGA_LINK_UP_REGS.items():
        assert tgt.reg_get(addr) == val
    # (v>>3 & 0xff) == 0xFC for the FPGA PCS/link status register
    assert ((tgt.reg_get(0x40010008) >> 3) & 0xFF) == 0xFC


# ---- firmware-level predicate check (firmware-gated) ------------------------------------------

@requires_firmware
def test_report_xaui_status_reads_iob_window_at_polled_offsets():
    """Drive the real ``lsb_fpga_report_xaui_status`` and confirm it reads the IOB AHB window at
    the exact XAUI PCS/status offsets the firmware polls, served by IobXauiWindow as link-up.

    This exercises the IOB-side half of the link model end-to-end against the firmware's own
    poll code (the FPGA-side half travels the McSPI1-ch2-over-sDMA transport, whose completion is
    a separate milestone; see boot_to_ui_bringup's next_blocker)."""
    cam = Dsmc2Camera(workspace_root=".")
    cam.load_firmware()
    cam.boot_to_late_init()
    fw = cam.fw
    mu = cam.cpu.mu

    offsets = set()

    def win_read(uc, access, address, size, value, user_data):
        offsets.add((address - 0x1C000000) & 0x3FFFF)

    mu.hook_add(__import__("unicorn").UC_HOOK_MEM_READ, win_read, None, 0x1C000000, 0x1C0FFFFF)

    # Stub the sleep so the predicate runs without the RTOS timer thread.
    sleep_addr = fw(0x80E9E32C)
    saved = bytes(cam.bus.read_mem(sleep_addr, 8))
    cam.bus.write_mem(sleep_addr, struct.pack("<II", 0xE3A00000, 0xE12FFF1E))

    sentinel = fw(cam.IDLE_LOOP_ENTRY)
    cam.cpu.pc = fw(0x809BCF64)  # lsb_fpga_report_xaui_status
    cam.cpu.sp = 0x881D0000
    cam.cpu.lr = sentinel
    cam.cpu.set_r(0, 1)  # quiet=1
    cam.cpu.set_r(1, 0)  # clear=0
    mu.hook_add(__import__("unicorn").UC_HOOK_CODE,
                lambda uc, a, s, u: uc.emu_stop(), None, sentinel, sentinel)
    try:
        cam.cpu.run(cam.cpu.pc, 0, count=3_000_000)
    except Exception:
        pass
    cam.bus.write_mem(sleep_addr, saved)

    # The firmware read the IOB XAUI PCS (+0x2C), status (+0x10), len-err (+0x68) and
    # crc-err (+0xBC) through the AHB window -- the IobXauiWindow served them as link-up.
    port = PORT_EXP1_OFF
    assert (port + 0x2C) in offsets, f"PCS status not polled; offsets={sorted(hex(o) for o in offsets)}"
    assert (port + 0x10) in offsets
    assert cam.iob_xaui.read_count > 0
    # The PCS status served to the firmware was the link-up constant.
    assert cam.iob_xaui.value_for(port + 0x2C) == 0x03FFC02F

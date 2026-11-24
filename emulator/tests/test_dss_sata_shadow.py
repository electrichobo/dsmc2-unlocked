"""
Fast, boot-free unit tests for the SATA3 shadow-register block in ``IobXauiWindow``
(devices/iob_xaui.py) -- the model that lets the firmware's own storage-init code cross the
SATA/Media barrier (sata_init @0x80670504 / media_init @0x8067060C) toward run_application.

The DSMC2 main SATA path has no OMAP AHCI controller: the LSB-FPGA presents shadow SATA registers
in the IOB AHB aperture (0x1C000000), read through FUN_80727028 (@0x80727028) as
``*((addr & 0x3FFFFFF) | 0x1C000000)``. For HBA0 (pHBAInfo SATA3 flag hba[8]=1) the driver routes
every register read to a fixed shadow index at base 0x800B0000:

    SATA_ReadHBARegister(hba0, 0)  @0x8069A798 -> idx 0x1A0 -> window off 0x0B0680 (FPGA running)
    SATA_ReadPortRegister(dev,0x28)@0x8069AA38 -> idx 0x1AE -> window off 0x0B06B8 (PxSSTS)

SATA_MediaPresent (@0x806A2ACC, SATA3 branch) returns "present" when PxSSTS has (v & 0xF0) != 0
and (v & 0xF) == 3 (AHCI DET=3). These tests assert the window answers those two gated indices
with firmware-correct values and honours the empty-bay (media_present=False) path.
"""

from emulator.devices.iob_xaui import (
    IobXauiWindow,
    SATA_SHADOW_BLOCK_OFF,
    SATA_PXSSTS_PRESENT,
    SATA_HBA_FPGA_RUNNING,
)

# Window offsets the firmware actually reads (idx*4 + 0x0B0000):
OFF_HBA_REG0 = SATA_SHADOW_BLOCK_OFF + (0x1A0 << 2)   # 0x0B0680
OFF_PXSSTS = SATA_SHADOW_BLOCK_OFF + (0x1AE << 2)     # 0x0B06B8


def test_shadow_block_offsets_match_firmware():
    """The shadow indices map to the exact window offsets the SATA driver reads."""
    # 0x800B0000 & 0x3FFFFFF == 0x0B0000, idx*4 added.
    assert OFF_HBA_REG0 == 0x0B0680
    assert OFF_PXSSTS == 0x0B06B8


def test_hba_fpga_running_is_nonzero():
    """SATA_ReadHBARegister(hba0,0) must be non-zero for SATA_WaitForFPGARunning to succeed."""
    win = IobXauiWindow(link_up=True, media_present=False)
    assert win.is_status_reg(OFF_HBA_REG0) is True
    v = win.value_for(OFF_HBA_REG0)
    assert v == SATA_HBA_FPGA_RUNNING
    assert v != 0  # "FPGA running" / HBA alive


def test_pxssts_present_encodes_det3():
    """With media present, PxSSTS reports DET=3 (device present + PHY comm), SPD/IPM non-zero."""
    win = IobXauiWindow(link_up=True, media_present=True)
    v = win.value_for(OFF_PXSSTS)
    assert v == SATA_PXSSTS_PRESENT
    # The two predicates SATA_MediaPresent (SATA3 branch) gates on:
    assert (v & 0xF0) != 0    # speed negotiated
    assert (v & 0xF) == 3     # AHCI DET=3


def test_pxssts_empty_bay_is_det0():
    """With the bay modelled empty, PxSSTS reports no device (DET=0) so media_init takes the
    'No media is present' success branch -- the HBA/FPGA-running register stays up."""
    win = IobXauiWindow(link_up=True, media_present=False)
    assert win.value_for(OFF_PXSSTS) == 0x00000000
    # The controller register is unaffected by media presence.
    assert win.value_for(OFF_HBA_REG0) == SATA_HBA_FPGA_RUNNING


def test_link_down_zeroes_sata_block():
    """A down XAUI/FPGA link zeroes the whole SATA shadow block (no spurious present)."""
    win = IobXauiWindow(link_up=False, media_present=True)
    assert win.value_for(OFF_HBA_REG0) == 0x00000000
    assert win.value_for(OFF_PXSSTS) == 0x00000000


def test_non_gated_shadow_reads_are_zero():
    """Only the two gated indices are answered; other offsets in the block read 0 (RAM)."""
    win = IobXauiWindow(link_up=True, media_present=True)
    other = SATA_SHADOW_BLOCK_OFF + (0x1A1 << 2)  # idx 0x1A1, not gated
    assert win.is_status_reg(other) is False
    assert win.value_for(other) == 0x00000000


def test_xaui_block_still_answered():
    """The SATA block addition must not disturb the existing XAUI port status answers."""
    win = IobXauiWindow(link_up=True)
    # EXP1 port PCS status (+0x2C) still reports link-up.
    assert win.is_status_reg(0x20000 + 0x02C) is True
    assert win.value_for(0x20000 + 0x02C) == 0x03FFC02F

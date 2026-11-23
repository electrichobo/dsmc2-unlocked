"""
GPMC NAND on-die-ECC feature round-trip and backing-store faithfulness.

Root cause of the YAFFS2 mount never completing (``init_filesystem`` @0x80008310 never setting
its event-flag bit 0x8): the firmware's ``_nand_set_internal_ecc`` (@0x8067b984) enables Micron
on-die ECC with SET FEATURES(0x90)=[8,..], then reads it back with GET FEATURES(0x90) and
requires the readback to equal what it wrote. If SET FEATURES does not latch the written bytes,
the readback is zero, ``_nand_set_internal_ecc`` returns 0x702c, and every ``_nand_read_sector``
(@0x8067ccc8) fails -> the YAFFS mount reads nothing and the NAND-FS init child never succeeds.

These tests exercise the GPMC model directly (no firmware image needed) by replaying the exact
register command sequences the firmware issues:

* ``_nand_set_feature`` (@0x8067b83c): CMD 0xEF, ADDR <feat>, 4x DATA <params>.
* ``_nand_get_feature`` (@0x8067b8bc): CMD 0xEE, ADDR <feat>, FUN_8067bd20 (status 0x70), CMD 0x00,
  then 4x DATA reads. The intervening status/idle commands must NOT clobber the feature FIFO.
* page read: CMD 0x00, 2 column + 3 row address bytes, CMD 0x30, then data + spare streamed from
  GPMC_NAND_DATA_0.
"""

from emulator.devices.gpmc import OmapGpmc
from emulator.devices import nand_image

C = OmapGpmc.REG_NAND_CMD_0       # 0x7C
AD = OmapGpmc.REG_NAND_ADDRESS_0  # 0x80
D = OmapGpmc.REG_NAND_DATA_0      # 0x84


def _set_feature(g, feat, params):
    g.write(C, 1, 0xEF)
    g.write(AD, 1, feat)
    for b in params:
        g.write(D, 1, b)


def _get_feature(g, feat):
    # Mirror _nand_get_feature: 0xEE, addr, (FUN_8067bd20: 0x70 + status read), 0x00, read 4.
    g.write(C, 1, 0xEE)
    g.write(AD, 1, feat)
    g.write(C, 1, 0x70)
    assert (g.read(D, 1) & 0x01) == 0            # status PASS, and must not drain the FIFO
    g.write(C, 1, 0x00)
    return [g.read(D, 1) for _ in range(4)]


def test_set_features_round_trips_through_status_poll():
    """GET FEATURES after SET FEATURES returns the written bytes despite the mid-sequence poll."""
    g = OmapGpmc()
    _set_feature(g, 0x90, [0x08, 0x00, 0x00, 0x00])   # Micron on-die ECC enable
    assert g.features[0x90] == [0x08, 0x00, 0x00, 0x00]
    assert _get_feature(g, 0x90) == [0x08, 0x00, 0x00, 0x00]
    # Disable round-trips too.
    _set_feature(g, 0x90, [0x00, 0x00, 0x00, 0x00])
    assert _get_feature(g, 0x90) == [0x00, 0x00, 0x00, 0x00]


def test_read_status_does_not_drain_data_fifo():
    """A 0x70 status read mid-read must not consume staged page bytes (FUN_8067bd20 behaviour)."""
    g = OmapGpmc()
    g.program_page(10, bytes([0x11] * OmapGpmc.PAGE_SIZE), bytes([0x22] * OmapGpmc.SPARE_SIZE))
    g.write(C, 1, 0x00)
    for b in (0, 0, 10, 0, 0):
        g.write(AD, 1, b)
    g.write(C, 1, 0x30)
    first = g.read(D, 1)
    g.write(C, 1, 0x70)                 # status poll in the middle of streaming
    assert g.read(D, 1) == 0xE0
    g.write(C, 1, 0x00)
    rest = bytes(g.read(D, 1) for _ in range(OmapGpmc.PAGE_SIZE - 1))
    assert first == 0x11 and rest == bytes([0x11] * (OmapGpmc.PAGE_SIZE - 1))


def test_backing_store_program_read_and_erase():
    """Programmed pages read back their data+spare; erased/blank pages read 0xFF (empty YAFFS2)."""
    g = OmapGpmc()
    page = 45 * OmapGpmc.PAGES_PER_BLOCK  # first page of /image1
    data = bytes((i * 3) & 0xFF for i in range(OmapGpmc.PAGE_SIZE))
    spare = bytes([0x5A] * OmapGpmc.SPARE_SIZE)
    g.program_page(page, data, spare)

    def read_page(p):
        g.write(C, 1, 0x00)
        for b in (0, 0, p & 0xFF, (p >> 8) & 0xFF, (p >> 16) & 0xFF):
            g.write(AD, 1, b)
        g.write(C, 1, 0x30)
        d = bytes(g.read(D, 1) for _ in range(OmapGpmc.PAGE_SIZE))
        s = bytes(g.read(D, 1) for _ in range(OmapGpmc.SPARE_SIZE))
        return d, s

    d, s = read_page(page)
    assert d == data and s == spare
    # An unwritten page is all-0xFF (YAFFS2 reads this as an unwritten chunk, seq == 0xFFFFFFFF).
    d2, s2 = read_page(page + 1)
    assert d2 == bytes([0xFF] * OmapGpmc.PAGE_SIZE) and s2 == bytes([0xFF] * OmapGpmc.SPARE_SIZE)
    # Erase the block and the programmed page returns to 0xFF.
    g.erase_block(page // OmapGpmc.PAGES_PER_BLOCK)
    d3, _ = read_page(page)
    assert d3 == bytes([0xFF] * OmapGpmc.PAGE_SIZE)


def test_partition_table_blob_matches_shipped_layout():
    """The synthesized PARTITION_TABLE serializes version 6 and the shipped 8-partition layout."""
    blob = nand_image.build_partition_table_blob()
    import struct
    ver, count = struct.unpack_from("<II", blob, 0)
    assert ver == 6
    assert count == len(nand_image.PARTITIONS) == 8
    # First entry is /env starting at block 1 for 44 blocks (firmware hardcodes this in yaffs_start_up).
    name = blob[8:24].rstrip(b"\x00").decode()
    start, numblk = struct.unpack_from("<II", blob, 24)
    assert name == "/env" and start == 1 and numblk == 44


def test_env_image_programs_partition_table_into_nand():
    """write_env_image stages a PARTITION_TABLE object header + data into the /env partition pages."""
    g = OmapGpmc()
    nand_image.write_env_image(g, env_start_block=1)
    base = 1 * OmapGpmc.PAGES_PER_BLOCK  # /env starts at block 1
    hdr = g.nand_image.get(base)
    assert hdr is not None
    import struct
    assert struct.unpack_from("<I", hdr, 0)[0] == nand_image.YAFFS_OBJECT_TYPE_FILE  # type @ 0x00
    assert struct.unpack_from("<I", hdr, 0x04)[0] == nand_image.YAFFS_OBJECTID_ROOT   # parent @ 0x04
    # The object's short name lives at OH offset 0x0A (the scan applies it via
    # FUN_80a179d8 -> FUN_80a1795c(obj, OH + 0x0A); yaffs_find_by_name then matches its sum).
    assert hdr[0x0A:0x0A + 15] == b"PARTITION_TABLE"
    assert hdr[0x0A + 15] == 0x00  # NUL-terminated (<= 15 chars)
    # yst_mode @ 0x10C carries the read bit (0x100) that red_open gates file access on.
    assert struct.unpack_from("<I", hdr, 0x10C)[0] & 0x100
    # OH spare tags: packed_tags2 words at OOB offsets 4/20/36/52 (one per 16-byte sub-spare).
    spare = g.nand_image[base][OmapGpmc.PAGE_SIZE:OmapGpmc.PAGE_SIZE + OmapGpmc.SPARE_SIZE]
    seq, w1, w2, bc = [struct.unpack_from("<I", spare, 4 + i * 16)[0] for i in range(4)]
    assert w2 & 0x80000000                     # header-chunk marker
    assert (w1 >> 28) == nand_image.YAFFS_OBJECT_TYPE_FILE  # object type in tag word 1
    assert (w2 & 0x0FFFFFFF) == nand_image.YAFFS_OBJECTID_ROOT  # parent object id

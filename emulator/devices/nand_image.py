"""
Synthesized YAFFS2 NAND image builder for the DSMC2 ``/env`` partition.

The firmware's ``init_filesystem`` (@0x80008310) mounts ``/env`` and reads
``/env/PARTITION_TABLE`` via ``env_get_partition_table`` (@0x80706b84). If that file is
absent, the firmware falls back to a built-in **version-0** partition table
(``&DAT_8140898c``) and then ``env_upgrade_partition_table`` (@0x80707470) runs the
v0->v1->...->v6 upgrade path, which reformats/relocates partitions in an unbounded NAND
read/write loop that never completes under emulation.

To let the mount complete cleanly, this module synthesizes a minimal, generic ``/env``
containing a single ``PARTITION_TABLE`` file whose version is the shipped **6**, so every
``env_upgrade_partition_table(1..6)`` call sees ``current >= requested`` and returns
immediately (no upgrade, no reformat). The filesystem is otherwise empty — no serials, no
calibration, no environment variables (which the firmware re-derives / signature-checks
non-fatally; see ``env_verify_signature`` @0x80706300 and ``raise_startup_error`` which only
sets a logged flag).

Formats are matched to the firmware's own readers (verified against the Ghidra decompile and
sdk ``clean/nand_utils.c`` / ``env_var.c``):

* **packed_tags2** (16-byte OOB tags), as unpacked by FUN_80a160a8:
  ``[u32 seqNumber, u32 objectId(+flags), u32 chunkId(+bit31 header), u32 byteCount]``.
  An unwritten chunk has ``seqNumber == 0xFFFFFFFF``.
* **yaffs_obj_hdr** (standard yaffs2 object header in the 2048-byte data area of chunk 0):
  ``u32 type; u32 parent_obj_id; u16 sum; char name[256]; ...`` — field offsets type@0,
  parent@4 confirmed from yaffs_verify_oh (@0x80a131d8).
* **PARTITION_TABLE on-disk layout**, from env_get_partition_table reads:
  ``u32 version; u32 count; count*(char name[16]; u32 startBlock; u32 numBlocks);
  u32 symVersion; u32 symCount; symCount*(u32 linkLen; char link[]; u32 tgtLen; char tgt[])``.

The image is written into ``OmapGpmc`` via ``program_page`` at the absolute pages of the
``/env`` partition, so the firmware's real GPMC NAND read path (on-die ECC enabled) serves it.
"""

import struct
from typing import Dict, List, Tuple

PAGE_SIZE = 2048
SPARE_SIZE = 64
PAGES_PER_BLOCK = 64

# yaffs2 object types
YAFFS_OBJECT_TYPE_FILE = 1
YAFFS_OBJECT_TYPE_SYMLINK = 2
YAFFS_OBJECT_TYPE_DIRECTORY = 3

# yaffs2 well-known object ids
YAFFS_OBJECTID_ROOT = 1
YAFFS_OBJECTID_LOSTNFOUND = 2
YAFFS_OBJECTID_UNLINKED = 3
YAFFS_OBJECTID_DELETED = 4
YAFFS_FIRST_FREE_OBJECTID = 0x100

# The shipped DSMC2 partition table (block layout from the camera's own
# ``nand show-partition-table``, version 6). Generic: no per-unit data.
PARTITION_TABLE_VERSION = 6
PARTITIONS: List[Tuple[str, int, int]] = [
    ("/env", 1, 44),
    ("/image1", 45, 560),
    ("/image2", 605, 560),
    ("/data", 1165, 280),
    ("/", 1500, 44),
    ("/log", 1808, 240),
    ("/rodata", 2048, 1200),
    ("/pdata", 3248, 4944),
]
# Symlink table (version 13) exactly as the camera reports it. Pure path strings; generic.
SYMLINK_TABLE_VERSION = 13
SYMLINKS: List[Tuple[str, str]] = [
    ("/force_upgrade", "/pdata/force_upgrade"),
    ("/force_preset", "/pdata/force_preset"),
    ("/data/luts", "/pdata/data/luts"),
    ("/data/overlays", "/pdata/data/overlays"),
    ("/data/looks", "/pdata/data/looks"),
    ("/data/cdb2/presets", "/pdata/data/presets"),
    ("/cal2", "/pdata/cal2"),
    ("/cal", "/pdata/cal"),
    ("/cal/col_cal", "/rodata/cal/col_cal"),
    ("/cal/rts_cal", "/rodata/cal/rts_cal"),
    ("/cal2/factory_0", "/rodata/cal2/factory_0"),
    ("/cal/nand", "/rodata/cal/nand"),
    ("/data/cdls", "/pdata/data/cdls"),
    ("/data/licenses", "/rodata/licenses"),
]


def build_partition_table_blob() -> bytes:
    """Serialize the PARTITION_TABLE file exactly as env_get_partition_table reads it."""
    out = bytearray()
    out += struct.pack("<I", PARTITION_TABLE_VERSION)
    out += struct.pack("<I", len(PARTITIONS))
    for name, start, numblk in PARTITIONS:
        nb = name.encode("ascii")[:16]
        nb = nb + b"\x00" * (16 - len(nb))
        out += nb
        out += struct.pack("<II", start, numblk)
    out += struct.pack("<I", SYMLINK_TABLE_VERSION)
    out += struct.pack("<I", len(SYMLINKS))
    for link, target in SYMLINKS:
        lb = link.encode("ascii")
        tb = target.encode("ascii")
        out += struct.pack("<I", len(lb)) + lb
        out += struct.pack("<I", len(tb)) + tb
    return bytes(out)


def _pack_tags2(seq: int, obj_id: int, chunk_id: int, byte_count: int) -> bytes:
    """Build the 16-byte packed_tags2 OOB tags FUN_80a160a8 unpacks (seq/obj/chunk/byteCount)."""
    return struct.pack("<IIII", seq & 0xFFFFFFFF, obj_id & 0xFFFFFFFF,
                       chunk_id & 0xFFFFFFFF, byte_count & 0xFFFFFFFF)


def _make_spare(tags: bytes) -> bytes:
    """Place the 16-byte tags at the start of the 64-byte OOB; remainder erased (0xFF)."""
    spare = bytearray(b"\xFF" * SPARE_SIZE)
    spare[0:16] = tags
    return bytes(spare)


def _build_obj_header(obj_type: int, parent_obj_id: int, name: str,
                      file_size: int = 0) -> bytes:
    """Build a 2048-byte yaffs2 object header (chunk 0 of an object).

    Standard yaffs2 ``yaffs_obj_hdr``: type@0, parent_obj_id@4, sum@8 (u16, unused -> 0xFFFF),
    name[256]@10, then POSIX-ish attributes; file size is stored as the low/high 32-bit split
    used by yaffs2 (``file_size_low`` / ``file_size_high``). Unused trailing bytes are 0xFF to
    match a NAND-written header.
    """
    hdr = bytearray(b"\x00" * PAGE_SIZE)
    struct.pack_into("<I", hdr, 0x00, obj_type)        # type: file/dir/symlink (yaffs_verify_oh @0)
    struct.pack_into("<I", hdr, 0x04, parent_obj_id)   # parent obj id (yaffs_verify_oh @4)
    struct.pack_into("<H", hdr, 0x08, 0xFFFF)          # checksum: no longer used
    # name lives at OH offset 0x12C: the scan copies it via FUN_80a19e7c(oh + 0x12C) (0x4b words),
    # max 0x9F bytes. (RE: yaffs2_scan_chunk @0x80a115bc.)
    nb = name.encode("ascii")[:0x9E]
    hdr[0x12C : 0x12C + len(nb)] = nb
    hdr[0x12C + len(nb)] = 0x00
    # POSIX-ish attribute block precedes the name (yst_mode etc.). Keep a plausible mode; yaffs
    # keys registration off type + name, so exact attrs are not load-bearing for the mount.
    struct.pack_into("<I", hdr, 0x10C,
                     0x000081A4 if obj_type == YAFFS_OBJECT_TYPE_FILE else 0x000041ED)  # st_mode
    # file size (yaffs2 32-bit low / high split) sits just after the name field region.
    struct.pack_into("<I", hdr, 0x228, file_size & 0xFFFFFFFF)   # file_size_low
    struct.pack_into("<I", hdr, 0x22C, 0)                        # file_size_high
    return bytes(hdr)


def build_env_chunks(seq_base: int = 0x00001000) -> List[Tuple[int, bytes, bytes]]:
    """Build the (relative_page, data, spare) chunks for a minimal ``/env`` with PARTITION_TABLE.

    Relative page 0 is the first page of the ``/env`` partition. Chunk-per-page. Returns the
    object-header chunk for the PARTITION_TABLE file followed by its data chunks. The root
    directory (object id 1) is implicit in yaffs2 and needs no on-disk header.
    """
    chunks: List[Tuple[int, bytes, bytes]] = []
    blob = build_partition_table_blob()
    obj_id = YAFFS_FIRST_FREE_OBJECTID
    seq = seq_base

    rel_page = 0
    # Object-header chunk tags. A yaffs2 header chunk (unpacked by FUN_80a160a8) is NOT a plain
    # (seq,obj,chunk,bytes) tuple: the chunkId word has bit31 set, and the tag words carry the
    # "extra" object info the scan reads instead of a separate header read:
    #   objId word  : bits[27:0] = object id,  bits[31:28] = object type (1=file,3=dir,4=symlink)
    #   chunkId word: bit31 = 1 (header marker), bits[27:0] = parent object id
    #   byteCount   : extra file size (object types != symlink)
    hdr = _build_obj_header(YAFFS_OBJECT_TYPE_FILE, YAFFS_OBJECTID_ROOT,
                            "PARTITION_TABLE", file_size=len(blob))
    obj_word = (obj_id & 0x0FFFFFFF) | (YAFFS_OBJECT_TYPE_FILE << 28)
    chunk_word = 0x80000000 | (YAFFS_OBJECTID_ROOT & 0x0FFFFFFF)  # header marker + parent = root
    hdr_tags = struct.pack("<IIII", seq & 0xFFFFFFFF, obj_word, chunk_word, len(blob))
    chunks.append((rel_page, hdr, _make_spare(hdr_tags)))
    rel_page += 1

    # Data chunks (chunkId starts at 1). Last chunk's byteCount is the tail length.
    offset = 0
    chunk_id = 1
    while offset < len(blob):
        piece = blob[offset : offset + PAGE_SIZE]
        data = piece + b"\xFF" * (PAGE_SIZE - len(piece))
        tags = _pack_tags2(seq, obj_id, chunk_id, len(piece))
        chunks.append((rel_page, data, _make_spare(tags)))
        rel_page += 1
        chunk_id += 1
        offset += PAGE_SIZE

    return chunks


def write_env_image(gpmc, env_start_block: int = 1):
    """Program the synthesized ``/env`` YAFFS2 image into ``gpmc``'s backing NAND store.

    ``env_start_block`` is the absolute first block of ``/env`` (block 1 in the shipped
    layout; the firmware's yaffs_start_up hardcodes start=1, 44 blocks). Pages are written at
    their absolute page index so the firmware's partition-relative reads resolve correctly.
    """
    base_page = env_start_block * PAGES_PER_BLOCK
    for rel_page, data, spare in build_env_chunks():
        gpmc.program_page(base_page + rel_page, data, spare)

"""
Firmware address map: stock 7.4.1 address -> address in the image actually loaded.

vbrain knows a handful of firmware addresses (reset/IRQ handlers, ThreadX globals and TCBs,
the idle loop, seeded RAM cells, stubbed helpers). They are all written as stock 7.4.1
addresses. A relinked image with its sections moved (dsmc2-sdk `dsmc-fw relink symbolize`)
ships an ``addrmap.tsv``; loading it translates every one of those addresses through
``to_fw`` and maps PCs back with ``to_stock``. Without a map both are the identity.

``addrmap.tsv``: ``stock_start  stock_end  layout_start  [section]`` per line (hex, ``#``
comments). Each row moves ``[stock_start, stock_end)`` rigidly; the end of the last row is
inclusive (the ``.bss`` end symbol). Addresses in no row are fixed (MMIO, heap, DMA pool, stack).
"""

import bisect
from typing import Iterable, List, Optional, Tuple

Range = Tuple[int, int, int, str]


class AddrMap:
    def __init__(self, ranges: Iterable[Range] = ()):
        rs = sorted((int(s), int(e), int(n), str(name)) for s, e, n, name in ranges)
        for (s0, e0, _, n0), (s1, _, _, n1) in zip(rs, rs[1:]):
            if s1 < e0:
                raise ValueError(f"addrmap: {n0} and {n1} overlap")
        self.ranges: List[Range] = rs
        self._starts = [r[0] for r in rs]
        back = sorted((n, n + (e - s), s, name) for s, e, n, name in rs)
        self._back = back
        self._back_starts = [r[0] for r in back]
        self.path: Optional[str] = None

    @classmethod
    def load(cls, path: str) -> "AddrMap":
        rows = []
        with open(path, "r", encoding="utf-8") as f:
            for ln, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                cols = line.split()
                if len(cols) < 3:
                    raise ValueError(f"{path}:{ln}: need stock_start stock_end layout_start")
                s, e, n = (int(c, 16) for c in cols[:3])
                if e < s:
                    raise ValueError(f"{path}:{ln}: end before start")
                rows.append((s, e, n, cols[3] if len(cols) > 3 else f"row{ln}"))
        m = cls(rows)
        m.path = path
        return m

    @property
    def is_identity(self) -> bool:
        return all(s == n for s, _, n, _ in self.ranges)

    @staticmethod
    def _find(starts: List[int], ranges: List[Range], a: int) -> Optional[Range]:
        k = bisect.bisect_right(starts, a)
        if k == 0:
            return None
        r = ranges[k - 1]
        if a < r[1] or (k == len(ranges) and a == r[1]):
            return r
        return None

    def to_fw(self, stock_addr: int) -> int:
        """Stock 7.4.1 address -> address in the loaded image."""
        r = self._find(self._starts, self.ranges, stock_addr)
        return stock_addr if r is None else stock_addr - r[0] + r[2]

    def to_stock(self, fw_addr: int) -> int:
        """Address in the loaded image -> stock 7.4.1 address (identity outside every row)."""
        r = self._find(self._back_starts, self._back, fw_addr)
        return fw_addr if r is None else fw_addr - r[0] + r[2]

    def section_of(self, fw_addr: int) -> Optional[str]:
        r = self._find(self._back_starts, self._back, fw_addr)
        return None if r is None else r[3]

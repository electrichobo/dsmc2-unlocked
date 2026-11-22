"""
Lockstep divergence finder: a reference image (stock layout) and a relinked image (with its
``AddrMap``) boot side by side; report the first instruction where they stop doing the same
thing.

Both run with the same slicing as ``Dsmc2Camera.boot`` (IRQ injection + timer ticks between
slices); the state is compared at every slice boundary, and a slice that ends different is
replayed from reset and single-stepped in lockstep.

Divergence = control flow or peripherals: the relinked PC no longer maps back to the reference
PC, CPSR differs, or the MMIO writes differ (value neither equal nor mapping back). Register
values that are neither equal nor map back (``orr`` of a heap and a data pointer, a hash of an
address) are reported as the soft differences seen in the stepped slice before that point. An address
the symbolizer left literal therefore shows up once it is used -- the load through it returns
different bytes, or a peripheral is programmed differently -- which is the instruction reported,
together with the trail of PCs that led there.
"""

from typing import Any, Callable, Dict, List, Optional, Tuple

import unicorn
from unicorn import arm_const

from emulator.core.addrmap import AddrMap

REGS = [getattr(arm_const, f"UC_ARM_REG_R{i}") for i in range(13)] + [
    arm_const.UC_ARM_REG_SP, arm_const.UC_ARM_REG_LR]
REG_NAMES = [f"r{i}" for i in range(13)] + ["sp", "lr"]


MMIO_LO, MMIO_HI = 0x40000000, 0x7FFFFFFF


def trace_mmio_writes(cam) -> List[Tuple[int, int, int, int]]:
    """Record every MMIO write as (pc, address, size, value); returns the live list."""
    log: List[Tuple[int, int, int, int]] = []

    def hook(uc, access, address, size, value, user_data):
        log.append((uc.reg_read(arm_const.UC_ARM_REG_PC), address, size, value & 0xFFFFFFFF))

    cam.cpu.mu.hook_add(unicorn.UC_HOOK_MEM_WRITE, hook, None, MMIO_LO, MMIO_HI)
    return log


def diff_mmio(ref_log, rel_log, m: AddrMap) -> List[str]:
    for k, (a, b) in enumerate(zip(ref_log, rel_log)):
        if a[1:3] != b[1:3] or (a[3] != b[3] and m.to_stock(b[3]) != a[3]):
            return [f"mmio write #{k}: 0x{a[1]:08X}<-0x{a[3]:08X} @0x{a[0]:08X} vs "
                    f"0x{b[1]:08X}<-0x{b[3]:08X} @stock 0x{m.to_stock(b[0]):08X}"]
    if len(ref_log) != len(rel_log):
        return [f"mmio write count {len(ref_log)} vs {len(rel_log)}"]
    return []


def snapshot(cam) -> Dict[str, Any]:
    mu = cam.cpu.mu
    return {
        "pc": cam.cpu.pc,
        "cpsr": cam.cpu.cpsr,
        "regs": [mu.reg_read(r) for r in REGS],
    }


def diff_control(ref: Dict[str, Any], rel: Dict[str, Any], m: AddrMap) -> List[str]:
    out = []
    if m.to_stock(rel["pc"]) != ref["pc"]:
        out.append(f"pc 0x{ref['pc']:08X} vs 0x{rel['pc']:08X} (stock 0x{m.to_stock(rel['pc']):08X})")
    if rel["cpsr"] != ref["cpsr"]:
        out.append(f"cpsr 0x{ref['cpsr']:08X} vs 0x{rel['cpsr']:08X}")
    return out


def diff_regs(ref: Dict[str, Any], rel: Dict[str, Any], m: AddrMap) -> List[str]:
    out = []
    for n, a, b in zip(REG_NAMES, ref["regs"], rel["regs"]):
        if a != b and m.to_stock(b) != a:
            out.append(f"{n} 0x{a:08X} vs 0x{b:08X} (stock 0x{m.to_stock(b):08X})")
    return out


def _run(cam, n: int) -> List[str]:
    try:
        cam.cpu.run(cam.cpu.pc, 0, count=n)
        return []
    except unicorn.UcError as e:
        return [f"cpu fault at 0x{cam.cpu.pc:08X} (stock 0x{cam.to_stock(cam.cpu.pc):08X}): {e}"]


def _slice(cam, n: int) -> List[str]:
    """One boot() slice: IRQ delivery at the idle loop, run n, tick the timers."""
    _wake(cam)
    err = _run(cam, n)
    _tick(cam)
    return err


def _wake(cam):
    if cam.is_idle_pc(cam.cpu.pc) and cam.intc.has_pending_irq() and cam.cpu.are_interrupts_enabled():
        cam.cpu.inject_irq()


def _tick(cam):
    for tmr in cam.timers.values():
        if tmr.tclr & 0x01:
            tmr.tick(steps=32 if tmr.timer_id == 2 else 100)


def find_divergence(make_ref: Callable[[], Any], make_rel: Callable[[], Any], max_instructions: int,
                    step: int = 1_000_000, trail: int = 32) -> Dict[str, Any]:
    """``make_ref`` / ``make_rel`` return freshly loaded cameras (firmware in, reset state).

    Slices run at full speed; a slice whose end state differs in any way (control, MMIO writes
    or a register) is replayed from reset and single-stepped. If stepping finds only soft
    register differences the scan continues from there.
    """
    ref, rel = make_ref(), make_rel()
    m = rel.addr_map
    wa, wb = trace_mmio_writes(ref), trace_mmio_writes(rel)
    soft: List[Dict[str, Any]] = []
    replays = 0
    done = 0
    while done < max_instructions:
        n = min(step, max_instructions - done)
        fault = _slice(ref, n) + _slice(rel, n)
        a, b = snapshot(ref), snapshot(rel)
        clean = not (fault or diff_control(a, b, m) or diff_mmio(wa, wb, m) or diff_regs(a, b, m))
        wa.clear()
        wb.clear()
        if clean:
            done += n
            continue
        # replay to the start of this slice, then step it
        replays += 1
        ref, rel = make_ref(), make_rel()
        k = 0
        while k < done:
            j = min(step, done - k)
            _slice(ref, j)
            _slice(rel, j)
            k += j
        _wake(ref)
        _wake(rel)
        wa, wb = trace_mmio_writes(ref), trace_mmio_writes(rel)
        pcs: List[int] = []
        prev = (snapshot(ref), snapshot(rel))
        for i in range(n):
            a, b = prev
            pcs.append(a["pc"])
            wa.clear()
            wb.clear()
            fault = ["ref: " + x for x in _run(ref, 1)] + ["relinked: " + x for x in _run(rel, 1)]
            cur = (snapshot(ref), snapshot(rel))
            d = fault + diff_control(cur[0], cur[1], m) + diff_mmio(wa, wb, m)
            r = diff_regs(cur[0], cur[1], m)
            if d:
                insn = bytes(ref.bus.read_mem(a["pc"], 4))
                return {
                    "diverged": True,
                    "instruction_index": done + i,
                    "slice_start": done,
                    "at_pc_stock": f"0x{a['pc']:08X}",
                    "at_pc_relinked": f"0x{b['pc']:08X}",
                    "insn": f"0x{int.from_bytes(insn, 'little'):08X}",
                    "before": {"ref": _fmt(a), "rel": _fmt(b)},
                    "after": {"ref": _fmt(cur[0]), "rel": _fmt(cur[1])},
                    "differences": d + r,
                    "soft_register_differences_before": soft[-8:],
                    "trail_stock_pcs": [f"0x{p:08X}" for p in pcs[-trail:]],
                    "replays": replays,
                }
            if r and (not soft or soft[-1]["differences"] != r):
                soft.append({"instruction_index": done + i, "at_pc_stock": f"0x{a['pc']:08X}", "differences": r})
            prev = cur
        _tick(ref)
        _tick(rel)
        wa.clear()
        wb.clear()
        done += n
    return {"diverged": False, "instructions": done, "pc": f"0x{ref.cpu.pc:08X}",
            "soft_register_differences": soft[-8:], "replays": replays}


def _fmt(s: Dict[str, Any]) -> Dict[str, str]:
    d = {"pc": f"0x{s['pc']:08X}", "cpsr": f"0x{s['cpsr']:08X}"}
    d.update({n: f"0x{v:08X}" for n, v in zip(REG_NAMES, s["regs"])})
    return d

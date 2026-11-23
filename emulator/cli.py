"""
DSMC2 Virtual Brain Command Line Interface (CLI - dsmc2-vbrain).
Provides scriptable commands for booting, running, inspecting, snapshotting, and testing.
"""

import argparse
import json
import os
import sys
import time

from emulator.camera import Dsmc2Camera
from emulator.core.addrmap import AddrMap
from emulator.core.snapshot import SnapshotManager


def get_workspace_root() -> str:
    """Determine root directory containing firmware_store and persistent_store."""
    # Look for current directory or parent directory containing firmware_store
    cwd = os.getcwd()
    if os.path.isdir(os.path.join(cwd, "firmware_store")):
        return cwd
    parent = os.path.dirname(cwd)
    if os.path.isdir(os.path.join(parent, "firmware_store")):
        return parent
    return cwd


def make_camera(args) -> Dsmc2Camera:
    """Camera for a CLI command: workspace, optional --addr-map, firmware loaded."""
    cam = new_camera(args)
    cam.load_firmware()
    return cam


def new_camera(args) -> Dsmc2Camera:
    """Camera honouring -f/--firmware and --addr-map, firmware not loaded yet."""
    path = getattr(args, "addr_map", None)
    cam = Dsmc2Camera(get_workspace_root(), addr_map=AddrMap.load(path) if path else None)
    cam.firmware_path = getattr(args, "firmware", None)
    return cam


def load_boot_json(path: str) -> dict:
    """A saved ``boot --json`` result (log lines before the JSON are skipped)."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return json.loads(text[text.index("{\n") if "{\n" in text else text.index("{"):])


def stock_view(result: dict) -> dict:
    """The parts of a boot result that must match across layouts, in stock addresses."""
    st = result.get("stock", {})
    threads = []
    for t in result["threads"]:
        ts = t.get("stock", {})
        threads.append({k: ts.get(k, t[k]) if k in ("address", "entry", "sp", "stack_range") else t[k]
                        for k in ("name", "state", "priority", "run_count", "address", "entry", "sp", "stack_range")})
    return {"status": result["status"], "pc": st.get("pc", result["pc"]),
            "sp": st.get("sp", result["sp"]), "threads": threads}


def compare_boot(result: dict, expected: dict) -> list:
    a, b = stock_view(result), stock_view(expected)
    out = [f"{k}: {b[k]} expected, got {a[k]}" for k in ("status", "pc", "sp") if a[k] != b[k]]
    if len(a["threads"]) != len(b["threads"]):
        out.append(f"threads: {len(b['threads'])} expected, got {len(a['threads'])}")
    for x, y in zip(a["threads"], b["threads"]):
        for k in y:
            if x[k] != y[k]:
                out.append(f"thread {y['name']!r} {k}: {y[k]} expected, got {x[k]}")
    return out


def cmd_boot(args):
    """Execute firmware boot sequence and report status."""
    cam = make_camera(args)
    reset_pc = cam.cpu.pc

    t0 = time.time()
    late = None
    if getattr(args, "late_init", False):
        late = cam.boot_to_late_init(kernel_instructions=args.instructions)
    else:
        cam.boot(initial_instructions=args.instructions)
    duration = time.time() - t0

    threads = cam.get_threads()
    success = cam.is_idle_pc(cam.cpu.pc) or (cam.instruction_count >= args.instructions)

    result = {
        "status": "SUCCESS" if success else "FAILED",
        "instructions_executed": cam.instruction_count,
        "duration_seconds": round(duration, 3),
        "pc": f"0x{cam.cpu.pc:08X}",
        "sp": f"0x{cam.cpu.sp:08X}",
        "cpsr": f"0x{cam.cpu.cpsr:08X}",
        "threads_detected": len(threads),
        "threads": threads,
        "unknown_mmio_count": len(cam.bus.unknown_mmio)
    }
    if late is not None:
        result["late_init"] = {
            "milestone": late["milestone"],
            "reached": late["reached"],
            "init_event_flags": f"0x{late['init_event_flags']:08X}",
            "late_init_instructions": late["late_init_instructions"],
            "driver_thread_spawned": late["driver_thread_spawned"],
            "stock_pc": late["pc"],
        }
    if not cam.addr_map.is_identity:
        result["addr_map"] = cam.addr_map.path
        result["reset_pc"] = f"0x{reset_pc:08X}"
        result["entry_from_header"] = cam.entry_from_header
        result["stock"] = {
            "pc": f"0x{cam.to_stock(cam.cpu.pc):08X}",
            "sp": f"0x{cam.to_stock(cam.cpu.sp):08X}",
            "reset_pc": f"0x{cam.to_stock(reset_pc):08X}",
        }

    if args.expect:
        expected = load_boot_json(args.expect)
        mismatches = compare_boot(result, expected)
        result["expect"] = {"reference": args.expect, "match": not mismatches, "mismatches": mismatches}
        success = success and not mismatches

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"=== RED DSMC2 Boot Result: {result['status']} ===")
        print(f"Instructions: {result['instructions_executed']:,} in {result['duration_seconds']}s")
        print(f"PC: {result['pc']} | SP: {result['sp']} | CPSR: {result['cpsr']}")
        if "stock" in result:
            print(f"Stock-mapped PC: {result['stock']['pc']} (addr map {result['addr_map']})")
        if "late_init" in result:
            li = result["late_init"]
            print(f"Late init: milestone={li['milestone']} reached={li['reached']} "
                  f"flags={li['init_event_flags']} driver_thread={li['driver_thread_spawned']} "
                  f"(+{li['late_init_instructions']:,} insns)")
        print(f"Active ThreadX Threads ({result['threads_detected']}):")
        for t in threads:
            print(f"  - [{t['name']}] state={t['state']} prio={t['priority']} SP={t['sp']}")
        print(f"Unknown MMIO Accesses: {result['unknown_mmio_count']}")
        if "expect" in result:
            ref = stock_view(load_boot_json(args.expect))
            got = stock_view(result)
            print(f"--- vs {args.expect}: {'MATCH' if result['expect']['match'] else 'MISMATCH'} (stock addresses) ---")
            print(f"  {'':<32} {'expected':<24} {'this run':<24}")
            for k in ("status", "pc", "sp"):
                print(f"  {k:<32} {ref[k]:<24} {got[k]:<24}")
            for x, y in zip(got["threads"], ref["threads"]):
                print(f"  {y['name'][:32]:<32} {y['state'] + ' ' + str(y['run_count']):<24} {x['state'] + ' ' + str(x['run_count']):<24}")
            for line in result["expect"]["mismatches"]:
                print(f"  MISMATCH {line}")

    sys.exit(0 if success else 1)


def cmd_run(args):
    """Run virtual camera either with Web Studio, desktop GUI, or headlessly."""
    workspace = get_workspace_root()

    if args.headless:
        # Headless mode
        cam = make_camera(args)
        print(f"[*] Booting RED DSMC2 Headless...")
        cam.boot()
        print(f"[*] DSMC2 Booted to PC: 0x{cam.cpu.pc:08X}")
        print(f"[*] Starting RCP TCP Server on {args.host}:{args.port}...")
        cam.rcp_server.host = args.host
        cam.rcp_server.port = args.port
        cam.start()
        print("[*] Camera running. Press Ctrl+C to terminate.")
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            print("\n[*] Stopping virtual camera...")
            cam.stop()
            sys.exit(0)
    elif getattr(args, "ui", "web") == "tk":
        from emulator.ui.app import launch_gui
        launch_gui(workspace, camera=new_camera(args))
    else:
        # Launch modern Web-Based Virtual Camera Studio (default, freeze-proof)
        from emulator.ui.web import launch_web_studio
        web_port = getattr(args, "web_port", 8765)
        open_browser = not getattr(args, "no_browser", False)
        launch_web_studio(workspace, host=args.host, port=web_port, open_browser=open_browser,
                          camera=new_camera(args))


def cmd_inspect(args):
    """Inspect ThreadX RTOS, MMIO registers, VFS, or camera state."""
    cam = make_camera(args)
    cam.boot()

    data = {}

    if args.threads or args.all:
        data["threads"] = cam.get_threads()

    if args.mmio or args.all:
        data["unknown_mmio"] = {
            f"0x{addr:08X}": info for addr, info in cam.bus.unknown_mmio.items()
        }

    if args.vfs or args.all:
        data["vfs"] = {
            "env": dict(cam.vfs.env.config),
            "mounts": cam.vfs.mounts
        }

    if args.rcp or args.all:
        data["rcp_state"] = dict(cam.rcp.state)

    if args.json:
        print(json.dumps(data, indent=2))
    else:
        if "threads" in data:
            print(f"\n--- ThreadX RTOS Threads ({len(data['threads'])}) ---")
            for t in data["threads"]:
                print(f"  [{t['name']:<30}] State: {t['state']:<10} Prio: {t['priority']:<3} SP: {t['sp']} Entry: {t.get('entry')}")

        if "vfs" in data:
            print(f"\n--- Camera Environment (/env) ---")
            for k, v in data["vfs"]["env"].items():
                print(f"  {k:<20} = {v}")

        if "rcp_state" in data:
            print(f"\n--- Active Camera Parameters (RCP) ---")
            for k, v in data["rcp_state"].items():
                print(f"  {k:<20} = {v}")

        if "unknown_mmio" in data:
            print(f"\n--- Unknown MMIO Registers ({len(data['unknown_mmio'])}) ---")
            for addr, info in list(data["unknown_mmio"].items())[:20]:
                print(f"  {addr}: {info['count']} hits (last: 0x{info['last_val']:08X})")


def cmd_diverge(args):
    """Lockstep a reference image against a relinked one (with its addr map); first difference."""
    from emulator.core.diverge import find_divergence
    ws = get_workspace_root()
    rel_map = AddrMap.load(args.addr_map)

    def make(fw, m):
        def f():
            cam = Dsmc2Camera(ws, addr_map=m)
            cam.load_firmware(fw)
            return cam
        return f

    res = find_divergence(make(args.reference, None), make(args.firmware, rel_map),
                          args.instructions, step=args.step)
    print(json.dumps(res, indent=2))
    sys.exit(1 if res["diverged"] else 0)


def cmd_snapshot(args):
    """Manage emulator state snapshots."""
    workspace = get_workspace_root()
    cam = Dsmc2Camera(workspace)
    cam.load_firmware()

    if args.action == "save":
        cam.boot()
        SnapshotManager.save(cam, args.file, description=args.desc or "CLI Snapshot")
        print(f"[*] Snapshot successfully saved to: {args.file}")
    elif args.action == "restore":
        meta = SnapshotManager.restore(cam, args.file)
        print(f"[*] Snapshot successfully restored from: {args.file}")
        print(f"    Restored PC: 0x{cam.cpu.pc:08X}")
        print(f"    Instructions: {cam.instruction_count:,}")
        print(f"    Description: {meta.get('description', '')}")


def cmd_test(args):
    """Run automated regression test suite and export JSON report."""
    import pytest
    report_file = args.output_json or "test_report.json"

    pytest_args = ["-c", os.path.join(os.path.dirname(os.path.abspath(__file__)), "pytest.ini"), "-q"]
    if args.suite and args.suite != "all":
        pytest_args.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", f"test_{args.suite}.py"))
    else:
        pytest_args.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests"))

    t0 = time.time()
    exit_code = pytest.main(pytest_args)
    duration = time.time() - t0

    report = {
        "timestamp": time.time(),
        "suite": args.suite or "all",
        "exit_code": int(exit_code),
        "status": "PASSED" if exit_code == 0 else "FAILED",
        "duration_seconds": round(duration, 3)
    }

    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"\n[*] Test execution finished with status {report['status']} in {duration:.2f}s")
    print(f"[*] Structured report exported to: {report_file}")
    sys.exit(int(exit_code))


ADDR_MAP_HELP = ("addrmap.tsv of a relinked image (stock -> moved section addresses, "
                 "from dsmc2-sdk `dsmc-fw relink symbolize`); default: stock layout")


def main():
    parser = argparse.ArgumentParser(
        prog="dsmc2-vbrain",
        description="DSMC2 Virtual Brain ? Virtual Camera & Engineering Workbench"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: boot
    p_boot = subparsers.add_parser("boot", help="Execute firmware boot sequence")
    p_boot.add_argument("-n", "--instructions", type=int, default=45_000_000, help="Initial instructions to execute")
    p_boot.add_argument("-f", "--firmware", type=str, default=None, help="Path to dsmc_program.mem")
    p_boot.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_boot.add_argument("--late-init", action="store_true",
                        help="After the kernel budget, drive the scheduler past the idle park into "
                             "late driver init (Driver/Starter threads spawned, CRYPT/RTC/FileX init run)")
    p_boot.add_argument("--addr-map", type=str, default=None, help=ADDR_MAP_HELP)
    p_boot.add_argument("--expect", type=str, default=None,
                        help="Reference boot --json result; exit 1 unless status, stock-mapped pc/sp and every thread "
                             "(name, state, priority, run count, TCB/entry/stack in stock addresses) match")
    p_boot.set_defaults(func=cmd_boot)

    # Subcommand: run
    p_run = subparsers.add_parser("run", help="Run virtual camera")
    p_run.add_argument("--headless", action="store_true", help="Run without graphical display")
    p_run.add_argument("-f", "--firmware", type=str, default=None, help="Path to dsmc_program.mem")
    p_run.add_argument("--addr-map", type=str, default=None, help=ADDR_MAP_HELP)
    p_run.add_argument("--host", type=str, default="127.0.0.1", help="Server bind host")
    p_run.add_argument("-p", "--port", type=int, default=1111, help="RCP TCP server port (default: 1111)")
    p_run.add_argument("--web-port", type=int, default=8765, help="Web Studio HTTP port (default: 8765)")
    p_run.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")
    p_run.add_argument("--ui", choices=["web", "tk"], default="web", help="UI interface: web (default) or tk")
    p_run.set_defaults(func=cmd_run)

    # Subcommand: inspect
    p_inspect = subparsers.add_parser("inspect", help="Inspect RTOS, MMIO, VFS, or camera state")
    p_inspect.add_argument("--threads", action="store_true", help="Dump ThreadX RTOS threads")
    p_inspect.add_argument("--mmio", action="store_true", help="Dump unknown MMIO accesses")
    p_inspect.add_argument("--vfs", action="store_true", help="Dump VFS mounts and environment")
    p_inspect.add_argument("--rcp", action="store_true", help="Dump camera parameters (RCP)")
    p_inspect.add_argument("--all", action="store_true", help="Dump all inspection categories")
    p_inspect.add_argument("--json", action="store_true", help="Output as JSON")
    p_inspect.add_argument("-f", "--firmware", type=str, default=None, help="Path to dsmc_program.mem")
    p_inspect.add_argument("--addr-map", type=str, default=None, help=ADDR_MAP_HELP)
    p_inspect.set_defaults(func=cmd_inspect)

    # Subcommand: diverge
    p_div = subparsers.add_parser("diverge", help="First instruction where a relinked image stops matching the reference")
    p_div.add_argument("-f", "--firmware", type=str, required=True, help="Relinked image (e.g. shifted.bin)")
    p_div.add_argument("--addr-map", type=str, required=True, help=ADDR_MAP_HELP)
    p_div.add_argument("-r", "--reference", type=str, default=None, help="Reference image (default: the slot's dsmc_program.mem)")
    p_div.add_argument("-n", "--instructions", type=int, default=1_000_000_000, help="Instruction budget")
    p_div.add_argument("--step", type=int, default=1_000_000, help="Slice size (same as boot)")
    p_div.set_defaults(func=cmd_diverge)

    # Subcommand: snapshot
    p_snap = subparsers.add_parser("snapshot", help="Manage state snapshots")
    p_snap.add_argument("action", choices=["save", "restore"], help="Snapshot action")
    p_snap.add_argument("file", type=str, help="Snapshot file path (.dsmc2)")
    p_snap.add_argument("--desc", type=str, default="", help="Description string for snapshot")
    p_snap.set_defaults(func=cmd_snapshot)

    # Subcommand: test
    p_test = subparsers.add_parser("test", help="Run automated regression test suite")
    p_test.add_argument("--suite", choices=["all", "camera_boot", "storage", "peripherals", "fault_injection"], default="all")
    p_test.add_argument("-o", "--output-json", type=str, default="test_report.json", help="Path to write JSON report")
    p_test.set_defaults(func=cmd_test)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

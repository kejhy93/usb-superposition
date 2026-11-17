"""Command line entry point: `usbinspect`."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__, portmap
from .collect import collect
from .model import State
from .render import render, use_color
from .sysfs import FixtureSysfs, RecordingSysfs, Sysfs


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="usbinspect",
        description="Show USB devices, USB-C ports, Power Delivery and negotiated vs. possible modes.",
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--json", action="store_true", help="print machine-readable JSON")
    ap.add_argument("-a", "--all", action="store_true", help="include internal devices and empty ports")
    ap.add_argument("--fixture", metavar="FILE", help="read from a recorded snapshot instead of /sys")
    ap.add_argument("--map", metavar="FILE", help=f"bay map to use (default: {portmap.config_path()})")
    ap.add_argument("--no-map", action="store_true", help="ignore bay maps, group by kernel ports")
    ap.add_argument("--root", default="/", help=argparse.SUPPRESS)
    ap.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    sub = ap.add_subparsers(dest="cmd")

    snap = sub.add_parser("snapshot", help="record everything read from /sys into a fixture file")
    snap.add_argument("out", help="output JSON file")
    snap.add_argument("--no-redact", action="store_true", help="keep serial numbers / unique ids")

    sub.add_parser("calibrate", help="interactively map physical bays to ports")
    sub.add_parser("tui", help="interactive live view (needs the 'tui' extra: textual)")

    ports = sub.add_parser("ports", help="show the bay map and what is plugged into each bay")
    ports.add_argument("--export", action="store_true", help="print the map as TOML (for a built-in profile)")
    return ap


def _state(args, fs) -> State:
    state = collect(fs)
    if not args.no_map:
        portmap.apply(state, portmap.resolve(state.machine, args.map))
    return state


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    fs = FixtureSysfs.load(args.fixture) if args.fixture else Sysfs(args.root)

    if args.cmd == "snapshot":
        rec = RecordingSysfs(args.root)
        collect(rec)
        rec.dump(args.out, redact=not args.no_redact)
        print(f"wrote {len(rec.log)} entries to {args.out}", file=sys.stderr)
        return 0

    if args.cmd == "calibrate":
        return _calibrate(args, fs)

    if args.cmd == "ports":
        return _ports(args, fs)

    if args.cmd == "tui":
        return _tui(args, fs)

    state = _state(args, fs)
    if args.json:
        json.dump(state.to_dict(), sys.stdout, indent=2)
        print()
    else:
        color = args.color == "always" or (args.color == "auto" and use_color())
        print(render(state, show_internal=args.all, color=color))
    return 0


def _calibrate(args, fs) -> int:
    from . import calibrate

    if not sys.stdin.isatty():
        print("calibrate is interactive; run it in a terminal", file=sys.stderr)
        return 2
    first = collect(fs)
    try:
        existing = portmap.resolve(first.machine, args.map)
    except (OSError, ValueError):
        existing = None
    try:
        pm = calibrate.run(lambda: collect(fs), existing=existing)
    except (KeyboardInterrupt, EOFError):
        print("\naborted, nothing saved")
        return 130
    if pm is None:
        print("aborted, nothing saved")
        return 1
    path = portmap.config_path() if not args.map else args.map
    from pathlib import Path

    if Path(path).exists() and input(f"Overwrite {path}? [y/N] ").strip().lower() != "y":
        print(portmap.to_toml(pm))
        print("not saved (map printed above)")
        return 1
    portmap.save(pm, path)
    print(f"saved to {path}")
    print("Tip: `usbinspect ports --export` prints it in a form that can become a built-in profile.")
    return 0


def _tui(args, fs) -> int:
    try:
        from . import tui
    except ImportError:
        print(
            f"the TUI needs Textual, which {sys.executable} doesn't have.\n"
            "  install it:  pip install 'usb-superposition[tui]'\n"
            "  or, in a checkout:  python3 -m venv --system-site-packages .venv && "
            ".venv/bin/pip install -e '.[tui]'",
            file=sys.stderr,
        )
        return 2

    def load_map(state: State):
        if args.no_map:
            return None
        try:
            return portmap.resolve(state.machine, args.map)
        except (OSError, ValueError) as e:
            print(f"ignoring bay map: {e}", file=sys.stderr)
            return None

    tui.run(lambda: collect(fs), load_map, live=not args.fixture, show_internal=args.all)
    return 0


def _ports(args, fs) -> int:
    state = collect(fs)
    pm = None if args.no_map else portmap.resolve(state.machine, args.map)
    if args.export:
        if not pm:
            print("no bay map; run `usbinspect calibrate` first", file=sys.stderr)
            return 1
        pm.machine = pm.machine or state.machine
        pm.match = pm.match or state.machine
        print(portmap.to_toml(pm), end="")
        return 0

    portmap.apply(state, pm)
    print(f"Machine: {state.machine or 'unknown'}")
    print(f"Map:     {state.portmap_source or 'none (run `usbinspect calibrate`)'}")
    from .render import _find  # small internal helper

    for b in state.bays:
        print(f"\n{b.name} [{b.id}]")
        print(f"  USB ports: {', '.join(b.usb_ports) or '—'}")
        print(f"  USB-C:     {', '.join(b.typec) or '—'}")
        for n in b.devices:
            d = _find(state, n)
            if d:
                print(f"  • {d.product or d.vid + ':' + d.pid}  ({d.port_key})")

    mapped = {k for b in state.bays for k in b.usb_ports}
    loose = [
        k
        for rp in state.root_ports
        if rp.connect_type == "hotplug"
        for k in rp.keys
        if not any(k == m or m.startswith(k + "/") for m in mapped)
    ]
    if loose:
        print(f"\nHotplug root ports not in any bay: {', '.join(loose)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

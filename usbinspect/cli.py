"""Command line entry point: `usbinspect`."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .collect import collect
from .render import render, use_color
from .sysfs import FixtureSysfs, RecordingSysfs, Sysfs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="usbinspect",
        description="Show USB devices, USB-C ports, Power Delivery and negotiated vs. possible modes.",
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--json", action="store_true", help="print machine-readable JSON")
    ap.add_argument("-a", "--all", action="store_true", help="include internal devices and empty ports")
    ap.add_argument("--fixture", metavar="FILE", help="read from a recorded snapshot instead of /sys")
    ap.add_argument("--root", default="/", help=argparse.SUPPRESS)
    ap.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    sub = ap.add_subparsers(dest="cmd")
    snap = sub.add_parser("snapshot", help="record everything read from /sys into a fixture file")
    snap.add_argument("out", help="output JSON file")
    snap.add_argument("--no-redact", action="store_true", help="keep serial numbers / unique ids")
    args = ap.parse_args(argv)

    if args.cmd == "snapshot":
        rec = RecordingSysfs(args.root)
        collect(rec)
        rec.dump(args.out, redact=not args.no_redact)
        print(f"wrote {len(rec.log)} entries to {args.out}", file=sys.stderr)
        return 0

    fs = FixtureSysfs.load(args.fixture) if args.fixture else Sysfs(args.root)
    state = collect(fs)
    if args.json:
        json.dump(state.to_dict(), sys.stdout, indent=2)
        print()
    else:
        color = args.color == "always" or (args.color == "auto" and use_color())
        print(render(state, show_internal=args.all, color=color))
    return 0


if __name__ == "__main__":
    sys.exit(main())

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

    dmn = sub.add_parser("daemon", help="watch for changes: notifications, history, state.json")
    dmn.add_argument("--no-notify", action="store_true", help="don't send desktop notifications")

    hist = sub.add_parser("history", help="show the event history recorded by the daemon")
    hist.add_argument("--since", metavar="WHEN", help="e.g. 30m, 2h, 1d, 1w or an ISO date")
    hist.add_argument("--kind", action="append", help="only this event kind (repeatable)")
    hist.add_argument("--device", metavar="TEXT", help="VID:PID, sysfs name or part of the product name")
    hist.add_argument("-n", type=int, default=50, help="show the last N events (default 50, 0 = all)")
    hist.add_argument("--stats", action="store_true", help="per-device and charger statistics")
    hist.add_argument(
        "--clear",
        metavar="PORT",
        help="delete the history of one bay or port (bay id or name, port0, ...), "
        "to start a clean comparison of cables or chargers",
    )
    hist.add_argument("-y", "--yes", action="store_true", help="with --clear: don't ask for confirmation")

    diag = sub.add_parser(
        "diagnose", help="why is a port slower or charging less than it could? (cable, port, device, charger)"
    )
    diag.add_argument("port", nargs="?", help="bay id or name, or port0 ... (default: every bay)")
    diag.add_argument("--since", metavar="WHEN", help="only use history newer than this (e.g. 1h)")

    svc = sub.add_parser("service", help="manage the systemd user service running the daemon")
    svc.add_argument("action", choices=("install", "uninstall", "status", "print"))

    cfg = sub.add_parser("config", help="show the config file location; --init writes an example")
    cfg.add_argument("--init", action="store_true")

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

    if args.cmd == "daemon":
        from . import daemon

        if args.fixture:
            print("the daemon watches the live system; --fixture is not supported", file=sys.stderr)
            return 2
        return daemon.run(
            lambda low_w: collect(fs, low_charger_w=low_w), args.map, args.no_map, notify=not args.no_notify
        )

    if args.cmd == "history":
        if args.clear:
            return _history_clear(args, fs)
        return _history(args)

    if args.cmd == "diagnose":
        return _diagnose(args, fs)

    if args.cmd == "service":
        from . import service

        if args.action == "print":
            print(service.render_unit(), end="")
            return 0
        return {"install": service.install, "uninstall": service.uninstall, "status": service.status}[
            args.action
        ]()

    if args.cmd == "config":
        return _config(args)

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


def _history(args) -> int:
    from .history import History, parse_since, stats

    h = History()
    filters = {
        "since": parse_since(args.since) if args.since else None,
        "kinds": set(args.kind) if args.kind else None,
        "match": args.device,
        "include_internal": args.all,
    }
    if not h.files():
        print(f"no history yet ({h.path}); it is written by `usbinspect daemon`", file=sys.stderr)
        return 1
    if args.stats:
        st = stats(list(h.read(**filters)))
        if args.json:
            json.dump(st, sys.stdout, indent=2)
            print()
            return 0
        print("Events: " + ", ".join(f"{k} {v}" for k, v in sorted(st["events"].items())))
        if st["charger_watts"]:
            print(
                "Charger contracts: "
                + ", ".join(f"{w:g} W ×{n}" for w, n in sorted(st["charger_watts"].items()))
            )
        for ident, d in sorted(st["devices"].items(), key=lambda kv: -kv[1]["connects"]):
            line = f"  {d['label']} ({ident}): connected {d['connects']}×"
            if d["speeds_mbps"]:
                line += " at " + ", ".join(f"{s:g} Mbps ×{n}" for s, n in d["speeds_mbps"].items())
            if d["warnings"]:
                line += " · warnings: " + ", ".join(f"{k} ×{n}" for k, n in d["warnings"].items())
            print(line)
        return 0
    recs = list(h.read(**filters)) if args.n == 0 else h.tail(args.n, **filters)
    for r in recs:
        if args.json:
            print(r.to_json())
        else:
            mark = "⚠" if r.severity == "warning" else " "
            print(f"{r.ts.astimezone():%Y-%m-%d %H:%M:%S} {mark} {r.message}")
    return 0


def _history_clear(args, fs) -> int:
    from . import diagnose
    from .history import History

    state = _state(args, fs)
    sel = diagnose.select(state, args.clear)
    if sel is None:
        known = [s.label for s in diagnose.selections(state)]
        print(f"unknown bay or port: {args.clear!r} (try one of: {', '.join(known)})", file=sys.stderr)
        return 2
    h = History()
    port_bay = diagnose.port_bays(state)
    count = sum(1 for r in h.read() if diagnose.record_matches(sel, r, port_bay))
    if not count:
        print(f"no history for {sel.label}")
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            print("refusing to delete without confirmation; add --yes", file=sys.stderr)
            return 2
        if input(f"Delete {count} event(s) of {sel.label} from {h.path}? [y/N] ").strip().lower() != "y":
            print("nothing deleted")
            return 1
    removed = h.clear(lambda r: diagnose.record_matches(sel, r, port_bay))
    print(f"deleted {removed} event(s) of {sel.label}")
    _reload_daemon()
    return 0


def _reload_daemon() -> None:
    """Best effort: the daemon re-reads its recent events, so the widgets drop the cleared ones."""
    import contextlib
    import subprocess

    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            ["systemctl", "--user", "reload", "usbinspect.service"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )


def _diagnose(args, fs) -> int:
    from . import diagnose
    from .history import History, parse_since

    state = _state(args, fs)
    if args.port:
        sel = diagnose.select(state, args.port)
        if sel is None:
            known = [s.label for s in diagnose.selections(state)]
            print(f"unknown bay or port: {args.port!r} (try one of: {', '.join(known)})", file=sys.stderr)
            return 2
        sels = [sel]
    else:
        sels = diagnose.selections(state)
    since = parse_since(args.since) if args.since else None
    records = [] if args.fixture else list(History().read(since=since))
    reports = [diagnose.diagnose(state, s, records) for s in sels]
    if not args.port and not args.all:
        # only bays with something to say: connected now, or with history
        reports = [r for r in reports if r.devices or r.power or r.timeline] or reports
    if not state.bays:
        print("No bay map: history can't follow a physical bay. Run `usbinspect calibrate` first.\n")
    print("\n\n".join(diagnose.render_report(r) for r in reports))
    print(
        "\nTo compare cables: `usbinspect history --clear <bay>`, then plug the same device in with "
        "each cable in turn and run `usbinspect diagnose <bay>`."
    )
    return 0


def _config(args) -> int:
    from .config import EXAMPLE, config_path

    p = config_path()
    if args.init:
        if p.exists():
            print(f"{p} already exists", file=sys.stderr)
            return 1
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(EXAMPLE)
        print(f"wrote {p}")
        return 0
    print(p if p.exists() else f"{p} (not created; defaults in use — `usbinspect config --init`)")
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

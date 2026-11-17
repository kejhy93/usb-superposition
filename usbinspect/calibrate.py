"""Interactive bay calibration: plug something into each bay, we watch what changes."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .model import State, UsbDevice
from .portmap import BayDef, PortMap, default_bays

POLL_S = 0.3
SETTLE_S = 1.5  # keep watching this long after the last change
MAX_SETTLE_S = 8.0


@dataclass(frozen=True)
class Observation:
    devices: dict[str, str | None]  # device name -> port key
    partners: frozenset[str]  # Type-C ports with a partner
    powered: frozenset[str]  # Type-C ports with an online supply
    labels: dict[str, str] = field(default_factory=dict, compare=False)

    def same(self, other: Observation) -> bool:
        return (self.devices, self.partners, self.powered) == (other.devices, other.partners, other.powered)


@dataclass
class Evidence:
    usb: set[str] = field(default_factory=set)
    typec: set[str] = field(default_factory=set)
    seen: list[str] = field(default_factory=list)  # human-readable descriptions

    def __bool__(self) -> bool:
        return bool(self.usb or self.typec)

    def merge(self, other: Evidence) -> None:
        self.usb |= other.usb
        self.typec |= other.typec
        self.seen += other.seen


def _walk(devs: list[UsbDevice]):
    for d in devs:
        yield d
        yield from _walk(d.children)


def observe(state: State) -> Observation:
    devs = [d for rp in state.root_ports for d in _walk(rp.devices)]
    return Observation(
        devices={d.name: d.port_key for d in devs},
        partners=frozenset(p.id for p in state.typec_ports if p.partner),
        powered=frozenset(p.id for p in state.typec_ports if p.power and p.power.online),
        labels={d.name: d.product or f"{d.vid}:{d.pid}" for d in devs},
    )


def peer_map(state: State) -> dict[str, list[str]]:
    """root port key -> keys of its USB2/USB3 peers"""
    out: dict[str, list[str]] = {}
    for rp in state.root_ports:
        for k in rp.keys:
            out[k] = [o for o in rp.keys if o != k]
    return out


def with_peers(key: str, peers: dict[str, list[str]]) -> set[str]:
    """'XHC0.PRT2/1' -> {'XHC0.PRT2/1', 'XHC0.PRT7/1'}: a bay owns both halves."""
    root, sep, rest = key.partition("/")
    return {key} | {p + sep + rest for p in peers.get(root, [])}


def _parent(name: str) -> str | None:
    return name.rsplit(".", 1)[0] if "." in name else None


def evidence(before: Observation, after: Observation, peers: dict[str, list[str]]) -> Evidence:
    ev = Evidence()
    added = {n for n in after.devices if n not in before.devices}
    removed = {n for n in before.devices if n not in after.devices}
    changed = added | removed
    for n in sorted(changed):
        if _parent(n) in changed:  # only the top-most device of a plugged tree
            continue
        key = after.devices.get(n) or before.devices.get(n)
        if not key:
            continue
        ev.usb |= with_peers(key, peers)
        label = after.labels.get(n) or before.labels.get(n) or n
        ev.seen.append(f"{'+' if n in added else '-'} {label} at {key}")
    for p in sorted((before.partners ^ after.partners) | (before.powered ^ after.powered)):
        ev.typec.add(p)
        what = "partner" if p in (before.partners ^ after.partners) else "power"
        ev.seen.append(f"~ USB-C {p} ({what} changed)")
    return ev


def watch(
    snapshot: Callable[[], Observation],
    peers: dict[str, list[str]],
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Evidence:
    """Block until something changes, let it settle, return what changed."""
    base = snapshot()
    while True:
        sleep(POLL_S)
        cur = snapshot()
        if not cur.same(base):
            break
    first = last_change = clock()
    last = cur
    while clock() - last_change < SETTLE_S and clock() - first < MAX_SETTLE_S:
        sleep(POLL_S)
        cur = snapshot()
        if not cur.same(last):
            last, last_change = cur, clock()
    return evidence(base, last, peers)


def run(
    collect_state: Callable[[], State],
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    existing: PortMap | None = None,
) -> PortMap | None:
    """The wizard. Returns the new map, or None if the user quit."""
    state = collect_state()
    peers = peer_map(state)
    machine = state.machine

    names = default_bays(machine)
    if names:
        say(f"Machine: {machine}. Calibrating {len(names)} bays.")
    else:
        say("Unknown machine. Enter bay names one per line (empty line to finish).")
        names = []
        while (n := ask(f"  bay {len(names) + 1} name: ").strip()) != "":
            names.append((f"B{len(names) + 1}", n))
        if not names:
            return None

    say("")
    say("For each bay: plug any USB device into it, or pull out and re-insert its expansion card.")
    say("A USB-C device through a USB-C card also identifies the bay's USB-C (PD) connector.")
    say("Press Ctrl+C while waiting to skip a bay.")

    old = {b.id: b for b in (existing.bays if existing else [])}
    bays: list[BayDef] = []
    for i, (bid, bname) in enumerate(names, 1):
        say("")
        say(f"[{i}/{len(names)}] {bname}")
        ev = Evidence()
        while True:
            say("  waiting for a change…")
            try:
                got = watch(lambda: observe(collect_state()), peers, sleep=sleep)
            except KeyboardInterrupt:
                say("  skipped")
                break
            ev.merge(got)
            for line in got.seen:
                say(f"    {line}")
            say(f"  → USB {', '.join(sorted(ev.usb)) or '—'} · USB-C {', '.join(sorted(ev.typec)) or '—'}")
            for b in bays:
                clash = (set(b.usb) & ev.usb) | (set(b.typec) & ev.typec)
                if clash:
                    say(f"  ! {', '.join(sorted(clash))} already assigned to {b.name}")
            a = ask("  [Enter] accept  [a] add more evidence  [r] redo  [s] skip  [q] quit: ").strip().lower()
            if a == "q":
                return None
            if a == "s":
                ev = Evidence()
                break
            if a == "r":
                ev = Evidence()
                continue
            if a == "a":
                say("  unplug/replug again (e.g. the expansion card, or a USB-C device for the PD connector)")
                continue
            break
        if ev:
            # a port claimed by this bay moves away from any earlier bay
            for b in bays:
                b.usb = [k for k in b.usb if k not in ev.usb]
                b.typec = [k for k in b.typec if k not in ev.typec]
            bays.append(BayDef(bid, bname, sorted(ev.usb), sorted(ev.typec)))
        elif bid in old:
            say("  keeping previous mapping for this bay")
            bays.append(old[bid])

    say("")
    if not bays:
        say("No bay was calibrated.")
        return None
    say("Result:")
    for b in bays:
        say(f"  {b.name:<14} USB {', '.join(b.usb) or '—'} · USB-C {', '.join(b.typec) or '—'}")
    return PortMap(bays=bays, machine=machine)

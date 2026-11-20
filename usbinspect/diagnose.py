"""Why is a port slower, or charging at less, than it could? Is it the cable?

The kernel never says why a link trained at a given speed, and on the Framework 16 the
firmware doesn't report cable e-markers either. So the evidence is comparative: what the
device, the port and the charger can do, and what this bay has managed before (the
daemon's history, since the last `usbinspect history --clear` for the bay). Swapping
cables and re-running `usbinspect diagnose` is the experiment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .events import _walk
from .history import Record
from .model import State, TypecPort, UsbDevice
from .names import short_speed

FIVE_AMP_MA = 3000  # PD sources offer more than 3 A only after reading a 5 A e-marker


# ---------------------------------------------------------------- selecting a bay / port


@dataclass
class Selection:
    label: str
    bay: str | None  # bay id, when there is a bay map
    typec: list[str]  # Type-C port ids
    usb_keys: list[str]  # stable USB port keys


def selections(state: State) -> list[Selection]:
    """Every bay; without a bay map, every Type-C port and hotplug root port."""
    if state.bays:
        return [Selection(f"{b.name} [{b.id}]", b.id, list(b.typec), list(b.usb_ports)) for b in state.bays]
    out = [Selection(p.id, None, [p.id], []) for p in state.typec_ports]
    out += [
        Selection(rp.id, None, [], list(rp.keys)) for rp in state.root_ports if rp.connect_type == "hotplug"
    ]
    return out


def select(state: State, text: str) -> Selection | None:
    """A bay id or name, a Type-C port id (port0) or a USB port key / root port id."""
    t = text.strip().lower()
    for b in state.bays:
        if t in (b.id.lower(), b.name.lower()) or t in (x.lower() for x in b.typec):
            return Selection(f"{b.name} [{b.id}]", b.id, list(b.typec), list(b.usb_ports))
    for p in state.typec_ports:
        if p.id.lower() == t:
            return Selection(p.id, None, [p.id], [])
    for rp in state.root_ports:
        if t == rp.id.lower() or t in (k.lower() for k in rp.keys):
            return Selection(rp.id, None, [], list(rp.keys))
    return None


def port_bays(state: State) -> dict[str, str]:
    """Type-C port id -> bay id (older pd_* records carry only the port id)."""
    return {p.id: p.bay for p in state.typec_ports if p.bay}


def _under(key: str | None, keys: list[str]) -> bool:
    return bool(key) and any(key == k or key.startswith(k + "/") for k in keys)


def record_matches(sel: Selection, rec: Record, port_bay: dict[str, str]) -> bool:
    d = rec.details or {}
    if sel.bay:
        bay = d.get("bay") or (port_bay.get(rec.ref) if rec.ref else None)
        return bay == sel.bay
    return (rec.ref in sel.typec) or _under(d.get("port_key"), sel.usb_keys)


def devices_in(state: State, sel: Selection) -> list[UsbDevice]:
    """Non-internal devices plugged into the selection, top-down."""
    out = []
    for rp in state.root_ports:
        for d in _walk(rp.devices):
            inside = d.bay == sel.bay if sel.bay else _under(d.port_key, sel.usb_keys)
            if inside and not d.internal:
                out.append(d)
    return out


# ---------------------------------------------------------------- verdicts


@dataclass
class Verdict:
    kind: str  # ok | upstream | cable | bay_or_cable | cable_or_device | untested | charger | charger_or_cable | laptop
    text: str


def power_limit(p: TypecPort) -> Verdict | None:
    """What limits the PD contract on a charging port, from the charger's offers."""
    pw = p.power
    if not (pw and pw.online and p.power_role == "sink" and pw.watts):
        return None
    pd = p.partner.pd if p.partner else None
    offers = [o for o in (pd.source if pd else []) if o.watts]
    if not offers:
        return None
    best = max(offers, key=lambda o: o.watts)
    if pw.watts < best.watts - 0.5:
        return Verdict(
            "laptop",
            f"the charger offers up to {best.watts:g} W; the laptop asked for {pw.watts:g} W "
            "(battery nearly full, or its own power limit)",
        )
    if (best.current_ma or 0) == FIVE_AMP_MA and (best.voltage_mv or best.max_voltage_mv or 0) >= 20000:
        return Verdict(
            "charger_or_cable",
            f"the charger's best offer over this cable is {best.watts:g} W (3 A); if the charger "
            "is rated higher, the cable is the limit: chargers offer more than 3 A only over a 5 A cable",
        )
    return Verdict("charger", f"the charger is the limit: its best offer is {best.watts:g} W")


def _ident(d: dict[str, Any] | UsbDevice) -> str:
    if isinstance(d, UsbDevice):
        return f"{d.vid}:{d.pid}"
    return f"{d.get('vid')}:{d.get('pid')}"


@dataclass
class Best:
    mbps: float
    ts: datetime
    label: str
    bay: str | None


def _best_speeds(records: list[Record]) -> dict[str, Best]:
    """Per VID:PID, the fastest connection in these records."""
    out: dict[str, Best] = {}
    for r in records:
        d = r.details or {}
        if r.kind != "device_added" or not d.get("speed_mbps"):
            continue
        ident = _ident(d)
        if ident not in out or d["speed_mbps"] > out[ident].mbps:
            out[ident] = Best(d["speed_mbps"], r.ts, d.get("product") or ident, d.get("bay"))
    return out


def data_verdict(
    d: UsbDevice, here: list[Record], everywhere: list[Record], bay_names: dict[str, str]
) -> Verdict:
    """Is this device's link held back, and by what? `here` = this bay's records."""
    if not d.downgraded:
        return Verdict("ok", f"running at its best ({short_speed(d.speed_mbps)})")
    reason = d.downgrade_reason or ""
    if reason.startswith("limited by upstream"):
        return Verdict("upstream", f"{reason}: the hub or port above it, not the cable")
    speed = d.speed_mbps or 0
    ident = _ident(d)
    same_here = _best_speeds(here).get(ident)
    if same_here and same_here.mbps > speed:
        return Verdict(
            "cable",
            f"this device ran at {short_speed(same_here.mbps)} in this bay on "
            f"{same_here.ts.astimezone():%b %d %H:%M}, so bay and device are fine: "
            "the cable (or a loose plug) is the likely cause",
        )
    elsewhere = [
        r
        for r in everywhere
        if r.kind == "device_added" and _ident(r.details or {}) == ident and r not in here
    ]
    other = _best_speeds(elsewhere).get(ident)
    if other and other.mbps > speed:
        where = bay_names.get(other.bay or "", "another port")
        return Verdict(
            "bay_or_cable",
            f"this device ran at {short_speed(other.mbps)} in {where}, so the device is fine: "
            f"it's this bay or this cable. Try the same cable in {where} to tell them apart",
        )
    fastest_here = max(_best_speeds(here).values(), key=lambda b: b.mbps, default=None)
    if fastest_here and fastest_here.mbps > speed:
        return Verdict(
            "cable_or_device",
            f"this bay has done {short_speed(fastest_here.mbps)} ({fastest_here.label}), so the bay is "
            "fine: it's the cable or the device. Try another cable",
        )
    return Verdict(
        "untested",
        f"{reason or 'slower than it could be'}. No comparison yet: plug it in with another "
        "cable, or into another bay, and run `usbinspect diagnose` again",
    )


# ---------------------------------------------------------------- report


@dataclass
class Report:
    selection: Selection
    since: datetime | None  # first record considered (after the last clear)
    devices: list[tuple[UsbDevice, Verdict]] = field(default_factory=list)
    power: list[tuple[TypecPort, Verdict | None]] = field(default_factory=list)
    best_speed: Best | None = None
    best_watts: tuple[float, datetime] | None = None
    timeline: list[Record] = field(default_factory=list)


def diagnose(state: State, sel: Selection, records: list[Record], timeline: int = 12) -> Report:
    port_bay = port_bays(state)
    names = {b.id: b.name for b in state.bays}
    here = [r for r in records if record_matches(sel, r, port_bay) and not r.internal]
    rep = Report(sel, here[0].ts if here else None)
    for d in devices_in(state, sel):
        if d.companion and (d.speed_mbps or 0) <= 480:
            continue  # the USB 2 half of a hub/dock: its USB 3 half tells the story
        rep.devices.append((d, data_verdict(d, here, records, names)))
    for p in state.typec_ports:
        if p.id in sel.typec and p.power and p.power.online and p.power_role == "sink":
            rep.power.append((p, power_limit(p)))
    rep.best_speed = max(_best_speeds(here).values(), key=lambda b: b.mbps, default=None)
    watts = [
        (r.details["watts"], r.ts) for r in here if r.kind == "pd_contract" and (r.details or {}).get("watts")
    ]
    rep.best_watts = max(watts, default=None)
    rep.timeline = [r for r in here if r.kind in ("device_added", "pd_contract") or r.severity == "warning"][
        -timeline:
    ]
    return rep


def render_report(rep: Report) -> str:
    out = [rep.selection.label]
    if not rep.devices and not rep.power:
        out.append("  nothing connected")
    for d, v in rep.devices:
        maxs = f" of {short_speed(d.max_mbps)}" if d.max_mbps and d.max_mbps != d.speed_mbps else ""
        mark = "⚠" if v.kind not in ("ok", "upstream") else "✓" if v.kind == "ok" else "·"
        out.append(f"  {mark} {d.product or _ident(d)}: {short_speed(d.speed_mbps)}{maxs}")
        if v.kind != "ok":
            out.append(f"      {v.text}")
    for p, v in rep.power:
        pw = p.power
        out.append(
            f"  ⚡ charging {pw.watts:g} W ({(pw.voltage_mv or 0) / 1000:g} V × {(pw.current_ma or 0) / 1000:g} A)"
        )
        if v:
            out.append(f"      {v.text}")
    seen = []
    if rep.best_speed:
        seen.append(f"fastest {short_speed(rep.best_speed.mbps)} ({rep.best_speed.label})")
    if rep.best_watts:
        seen.append(f"most power {rep.best_watts[0]:g} W")
    if rep.since:
        out.append(
            f"  history since {rep.since.astimezone():%b %d %H:%M}: " + (", ".join(seen) or "no connections")
        )
    else:
        out.append("  no history for this bay yet")
    for r in rep.timeline:
        mark = "⚠" if r.severity == "warning" else " "
        out.append(f"    {r.ts.astimezone():%b %d %H:%M} {mark} {r.message}")
    return "\n".join(out)

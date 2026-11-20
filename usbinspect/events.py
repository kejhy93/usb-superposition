"""Turn two consecutive States into a list of human-meaningful events.

Shared by the TUI (toasts + Events tab) and the daemon (notifications + history).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import State, TypecPort, UsbDevice
from .names import short_speed


@dataclass
class Event:
    kind: str  # device_added | device_removed | pd_contract | pd_lost | <issue kind>
    severity: str  # info | warning
    message: str
    ref: str | None = None  # device name or Type-C port id
    internal: bool = False  # concerns an internal device (frontends usually hide these)
    details: dict[str, Any] = field(default_factory=dict)


def _walk(devs: list[UsbDevice]):
    for d in devs:
        yield d
        yield from _walk(d.children)


def _devices(state: State) -> dict[tuple, UsbDevice]:
    # A sysfs name can be reused by a different device after a replug, so identity
    # includes the ids.
    return {(d.name, d.vid, d.pid, d.serial): d for rp in state.root_ports for d in _walk(rp.devices)}


def _bay_names(state: State) -> dict[str, str]:
    return {b.id: b.name for b in state.bays}


def device_label(d: UsbDevice, state: State) -> str:
    label = d.product or f"{d.vid}:{d.pid}"
    bay = _bay_names(state).get(d.bay or "")
    return f"{label} ({bay})" if bay else label


def port_label(p: TypecPort, state: State) -> str:
    bay = _bay_names(state).get(p.bay or "")
    return f"{p.id} ({bay})" if bay else p.id


def _device_details(d: UsbDevice) -> dict[str, Any]:
    return {
        "name": d.name,
        "vid": d.vid,
        "pid": d.pid,
        "product": d.product,
        "speed_mbps": d.speed_mbps,
        "max_mbps": d.max_mbps,
        "port_key": d.port_key,
        "bay": d.bay,
    }


def diff(old: State | None, new: State) -> list[Event]:
    if old is None:
        return []
    out: list[Event] = []

    before, after = _devices(old), _devices(new)
    for key in sorted(after.keys() - before.keys(), key=lambda k: k[0]):
        d = after[key]
        if any(p in after and p not in before for p in _ancestors(key, after)):
            continue  # report only the top of a newly plugged tree
        speed = short_speed(d.speed_mbps)
        out.append(
            Event(
                "device_added",
                "info",
                f"Connected: {device_label(d, new)} · {speed}",
                d.name,
                d.internal,
                _device_details(d),
            )
        )
    for key in sorted(before.keys() - after.keys(), key=lambda k: k[0]):
        d = before[key]
        if any(p in before and p not in after for p in _ancestors(key, before)):
            continue
        out.append(
            Event(
                "device_removed",
                "info",
                f"Disconnected: {device_label(d, old)}",
                d.name,
                d.internal,
                _device_details(d),
            )
        )

    old_ports = {p.id: p for p in old.typec_ports}
    for p in new.typec_ports:
        o = old_ports.get(p.id)
        was = o.power if o and o.power and o.power.online and o.power_role == "sink" else None
        now = p.power if p.power and p.power.online and p.power_role == "sink" else None
        if now and (not was or (was.watts, was.usb_type) != (now.watts, now.usb_type)):
            v = (now.voltage_mv or 0) / 1000
            a = (now.current_ma or 0) / 1000
            verb = "Charging" if not was else "Charging changed to"
            out.append(
                Event(
                    "pd_contract",
                    "info",
                    f"{verb} {now.watts:g} W via {port_label(p, new)} ({v:g} V × {a:g} A, {now.usb_type})",
                    p.id,
                    details={
                        "watts": now.watts,
                        "mv": now.voltage_mv,
                        "ma": now.current_ma,
                        "type": now.usb_type,
                        "offer_w": p.partner.pd.max_source_w if p.partner and p.partner.pd else None,
                        "bay": p.bay,
                    },
                )
            )
        elif was and not now:
            out.append(
                Event(
                    "pd_lost",
                    "info",
                    f"Charger removed from {port_label(p, new)}",
                    p.id,
                    details={"bay": p.bay},
                )
            )

    old_issues = {(i.kind, i.ref, i.message) for i in old.issues}
    bays = {p.id: p.bay for p in new.typec_ports}
    bays.update({d.name: d.bay for rp in new.root_ports for d in _walk(rp.devices)})
    for i in new.issues:
        if (i.kind, i.ref, i.message) not in old_issues:
            bay = bays.get(i.ref or "")
            out.append(Event(i.kind, i.severity, i.message, i.ref, details={"bay": bay} if bay else {}))
    return out


def _ancestors(key: tuple, table: dict[tuple, UsbDevice]):
    """Keys of the parent chain of a device key, looked up by sysfs name."""
    name = key[0]
    by_name = {k[0]: k for k in table}
    while "." in name:
        name = name.rsplit(".", 1)[0]
        if name in by_name:
            yield by_name[name]

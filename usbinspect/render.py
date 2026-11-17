"""Plain-text rendering of a State for the terminal."""

from __future__ import annotations

import os
import sys

from .model import Pdo, RootPort, State, TypecPort, UsbDevice
from .names import short_speed


class _C:
    def __init__(self, enabled: bool) -> None:
        self.on = enabled

    def _w(self, code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.on else s

    def bold(self, s):
        return self._w("1", s)

    def dim(self, s):
        return self._w("2", s)

    def red(self, s):
        return self._w("31", s)

    def green(self, s):
        return self._w("32", s)

    def yellow(self, s):
        return self._w("33", s)

    def cyan(self, s):
        return self._w("36", s)


def use_color(stream=sys.stdout) -> bool:
    return stream.isatty() and "NO_COLOR" not in os.environ


def visible(d: UsbDevice, show_internal: bool) -> bool:
    """Internal devices are hidden unless something external hangs below them."""
    return show_internal or not d.internal or any(visible(c, False) for c in d.children)


def visible_unbayed(d: UsbDevice, show_internal: bool) -> bool:
    """For the 'Other' section when a bay map exists: skip anything already shown in a bay."""
    if d.bay is not None:
        return False
    return show_internal or not d.internal or any(visible_unbayed(c, False) for c in d.children)


def _bay_names(state: State) -> dict[str, str]:
    return {b.id: b.name for b in state.bays}


def _find(state: State, name: str) -> UsbDevice | None:
    stack = [d for rp in state.root_ports for d in rp.devices]
    while stack:
        d = stack.pop()
        if d.name == name:
            return d
        stack.extend(d.children)
    return None


def fmt_pdo(p: Pdo) -> str:
    v = p.voltage_mv
    if v is None and p.min_voltage_mv and p.max_voltage_mv:
        volts = f"{p.min_voltage_mv / 1000:g}–{p.max_voltage_mv / 1000:g} V"
    else:
        volts = f"{(v or 0) / 1000:g} V"
    amps = f" {p.current_ma / 1000:g} A" if p.current_ma else ""
    watts = f" ({p.watts:g} W)" if p.watts else ""
    kind = "" if p.type == "fixed_supply" else f" [{p.type.replace('_supply', '')}]"
    return f"{volts}{amps}{watts}{kind}"


def render(state: State, show_internal: bool = False, color: bool = False) -> str:
    c = _C(color)
    out: list[str] = []
    s = state.summary

    if s.charging_w is not None:
        port = next(p for p in state.typec_ports if p.id == s.charging_port)
        pw = port.power
        where = port.id + (f" · {_bay_names(state)[port.bay]}" if port.bay else "")
        out.append(
            c.bold(c.green(f"⚡ Charging {s.charging_w:g} W"))
            + f" via {where} ({pw.voltage_mv / 1000:g} V × {pw.current_ma / 1000:g} A, {pw.usb_type})"
        )
    else:
        out.append(c.dim("⚡ Not charging over USB-C"))
    out.append(f"{s.external_devices} external device(s), {s.warnings} warning(s)")
    out.append("")

    out.append(c.bold("USB-C ports"))
    bay_names = _bay_names(state)
    for p in state.typec_ports:
        out.extend(_typec(p, c, bay_names.get(p.bay or "")))
    if not state.typec_ports:
        out.append(c.dim("  (none found)"))
    out.append("")

    if state.bays:
        out.extend(_bays(state, c, show_internal))
    else:
        out.append(c.bold("USB") + c.dim("  (run `usbinspect calibrate` to name the physical bays)"))
        for rp in state.root_ports:
            out.extend(_root_port(rp, c, lambda d: visible(d, show_internal), show_internal))

    for dom in state.thunderbolt:
        links = [f"{r.id}:{k}={v}" for r in dom.routers for k, v in r.usb4_ports.items()]
        devs = [r for r in dom.routers if not r.id.endswith("-0")]
        if devs or show_internal:
            out.append("")
            out.append(
                c.bold(f"USB4 {dom.id}") + c.dim(f"  security={dom.security} links: {', '.join(links)}")
            )
            for r in devs:
                out.append(
                    f"  {r.vendor or '?'} {r.device or ''}  gen{r.generation} "
                    f"rx {r.rx_speed}×{r.rx_lanes} tx {r.tx_speed}×{r.tx_lanes}"
                )

    if state.issues:
        out.append("")
        out.append(c.bold("Issues"))
        for i in state.issues:
            mark = c.yellow("⚠") if i.severity == "warning" else c.cyan("ℹ")
            out.append(f"  {mark} {i.message}")
    return "\n".join(out)


def _bays(state: State, c: _C, show_internal: bool) -> list[str]:
    src = (state.portmap_source or "").replace(os.path.expanduser("~"), "~", 1)
    out = [c.bold("Bays") + c.dim(f"  (map: {src})")]

    def shown(d: UsbDevice) -> bool:
        return visible(d, show_internal)

    for b in state.bays:
        tags = ", ".join(b.typec + b.usb_ports) or "not calibrated"
        devs = [d for n in b.devices if (d := _find(state, n)) and shown(d)]
        head = f"  {c.cyan(b.name)} {c.dim(f'[{tags}]')}"
        typec = next((p for p in state.typec_ports if p.bay == b.id and p.power and p.power.online), None)
        if typec and typec.power.watts:
            head += " " + c.green(f"⚡ {typec.power.watts:g} W")
        out.append(head)
        if not devs:
            out.append(c.dim("    (empty)"))
        for i, d in enumerate(devs):
            out.extend(_device(d, c, shown, "    ", i == len(devs) - 1))
    other = []
    for rp in state.root_ports:
        other.extend(_root_port(rp, c, lambda d: visible_unbayed(d, show_internal), show_empty=False))
    if other:
        out.append("")
        out.append(c.bold("Other USB ports"))
        out.extend(other)
    return out


def _typec(p: TypecPort, c: _C, bay_name: str | None = None) -> list[str]:
    roles = f"{p.data_role or '?'} · {p.power_role or '?'}"
    head = f"  {c.bold(p.id)}" + (f" {c.cyan(bay_name)}" if bay_name else "") + f"  {roles}"
    pt = p.partner
    if not pt:
        return [head + c.dim("  — nothing connected")]
    lines = [head]
    pw = p.power
    if pw and pw.online and pw.watts:
        lines.append(
            f"      power: {c.green(f'{pw.watts:g} W')} contract "
            f"({pw.voltage_mv / 1000:g} V × {pw.current_ma / 1000:g} A, {pw.usb_type})"
        )
    if pt.pd and pt.pd.source:
        lines.append("      partner offers: " + " · ".join(fmt_pdo(x) for x in pt.pd.source))
    if pt.pd and pt.pd.sink:
        lines.append("      partner wants:  " + " · ".join(fmt_pdo(x) for x in pt.pd.sink))
    if pt.altmodes:
        modes = ", ".join(
            f"{a.name}" + ("" if a.active is None else (c.green(" ●") if a.active else c.dim(" ○")))
            for a in pt.altmodes
        )
        lines.append(f"      alt modes: {modes}" + c.dim("  (● active ○ inactive)"))
    if pt.accessory_mode:
        lines.append(f"      accessory: {pt.accessory_mode}")
    if pt.identity and pt.identity.product_type:
        lines.append(f"      partner type: {pt.identity.product_type}")
    if len(lines) == 1:
        lines.append(c.dim("      partner connected (no PD, no alt modes reported)"))
    cab = p.cable
    if cab:
        bits = [
            x
            for x in (
                cab.type,
                cab.usb_speed,
                f"{cab.current_ma / 1000:g} A" if cab.current_ma else None,
                f"{cab.max_voltage_v} V" if cab.max_voltage_v else None,
            )
            if x
        ]
        lines.append(f"      cable: {', '.join(bits) or 'no e-marker data'}")
    return lines


def _root_port(rp: RootPort, c: _C, pred, show_empty: bool) -> list[str]:
    devs = [d for d in rp.devices if pred(d)]
    if not devs and not (show_empty and rp.connect_type != "not used"):
        return []
    tag = c.dim(f"({rp.connect_type}, up to {short_speed(rp.max_mbps)})")
    lines = [f"  {c.cyan(rp.id)} {tag}"]
    if not devs:
        lines.append(c.dim("    (empty)"))
    for i, d in enumerate(devs):
        lines.extend(_device(d, c, pred, "    ", i == len(devs) - 1))
    return lines


def _device(d: UsbDevice, c: _C, pred, prefix: str, last: bool) -> list[str]:
    branch = "└─ " if last else "├─ "
    name = d.product or "Unknown device"
    if d.internal:
        name = c.dim(name)
    speed = short_speed(d.speed_mbps)
    if d.downgraded:
        speed = c.yellow(f"{speed} of {short_speed(d.max_mbps)}")
    elif d.max_mbps and d.max_source == "bos":
        speed = c.green(speed)
    extra = []
    if d.kind not in ("device", "hub"):
        extra.append(d.kind.replace("_", " "))
    if d.companion:
        extra.append(f"USB{'2' if (d.speed_mbps or 0) <= 480 else '3'} half, pair {d.companion}")
    drivers = sorted({i.driver for i in d.interfaces if i.driver})
    if drivers:
        extra.append(",".join(drivers))
    line = f"{prefix}{branch}{name}  {c.dim(f'{d.vid}:{d.pid}')}  {speed}"
    if extra:
        line += c.dim(f"  [{'; '.join(extra)}]")
    lines = [line]
    sub = prefix + ("   " if last else "│  ")
    for m in d.billboard or []:
        state = c.green(m.state) if m.state == "configured" else c.yellow(m.state)
        lines.append(f"{sub}{c.dim('alt mode')} {m.name}: {state}")
    kids = [k for k in d.children if pred(k)]
    for i, k in enumerate(kids):
        lines.extend(_device(k, c, pred, sub, i == len(kids) - 1))
    return lines

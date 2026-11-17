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
        out.append(
            c.bold(c.green(f"⚡ Charging {s.charging_w:g} W"))
            + f" via {port.id} ({pw.voltage_mv / 1000:g} V × {pw.current_ma / 1000:g} A, {pw.usb_type})"
        )
    else:
        out.append(c.dim("⚡ Not charging over USB-C"))
    out.append(f"{s.external_devices} external device(s), {s.warnings} warning(s)")
    out.append("")

    out.append(c.bold("USB-C ports"))
    for p in state.typec_ports:
        out.extend(_typec(p, c))
    if not state.typec_ports:
        out.append(c.dim("  (none found)"))
    out.append("")

    out.append(c.bold("USB"))
    for rp in state.root_ports:
        lines = _root_port(rp, c, show_internal)
        if lines:
            out.extend(lines)

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


def _typec(p: TypecPort, c: _C) -> list[str]:
    roles = f"{p.data_role or '?'} · {p.power_role or '?'}"
    head = f"  {c.bold(p.id)}  {roles}"
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


def _root_port(rp: RootPort, c: _C, show_internal: bool) -> list[str]:
    devs = [d for d in rp.devices if visible(d, show_internal)]
    if not devs and not (show_internal and rp.connect_type != "not used"):
        return []
    tag = c.dim(f"({rp.connect_type}, up to {short_speed(rp.max_mbps)})")
    lines = [f"  {c.cyan(rp.id)} {tag}"]
    if not devs:
        lines.append(c.dim("    (empty)"))
    for i, d in enumerate(devs):
        lines.extend(_device(d, c, show_internal, "    ", i == len(devs) - 1))
    return lines


def _device(d: UsbDevice, c: _C, show_internal: bool, prefix: str, last: bool) -> list[str]:
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
    kids = [k for k in d.children if visible(k, show_internal)]
    for i, k in enumerate(kids):
        lines.extend(_device(k, c, show_internal, sub, i == len(kids) - 1))
    return lines

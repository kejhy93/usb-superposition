"""Textual TUI: `usbinspect tui`."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from rich.console import Group, RenderableType
from rich.json import JSON
from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Header, RichLog, Static, TabbedContent, TabPane, Tree
from textual.widgets.tree import TreeNode

from . import portmap
from .events import Event, diff
from .model import (
    AltMode,
    Bay,
    Issue,
    Pdo,
    RootPort,
    State,
    TbDomain,
    TbRouter,
    TypecPort,
    UsbDevice,
)
from .names import short_speed
from .render import visible, visible_unbayed

POLL_S = 3.0  # PD contracts can change without a udev event
DEBOUNCE_S = 0.4
UDEV_SUBSYSTEMS = ("usb", "typec", "power_supply", "thunderbolt", "usb_power_delivery")

NodeData = tuple[str, Any]  # (stable node id, model object)


# ---------------------------------------------------------------- labels


def _speed_text(d: UsbDevice) -> Text:
    s = short_speed(d.speed_mbps)
    if d.downgraded:
        return Text(f"{s} of {short_speed(d.max_mbps)}", style="yellow")
    if d.max_mbps and d.max_source == "bos" and d.speed_mbps and d.speed_mbps >= d.max_mbps:
        return Text(s, style="green")
    return Text(s, style="default")


def device_label(d: UsbDevice) -> Text:
    t = Text()
    if d.downgraded:
        t.append("⚠ ", style="yellow")
    t.append(d.product or f"{d.vid}:{d.pid}", style="dim" if d.internal else "bold")
    t.append("  ")
    t.append_text(_speed_text(d))
    if d.kind not in ("device", "hub"):
        t.append(f"  {d.kind.replace('_', ' ')}", style="dim cyan")
    if d.companion:
        half = "USB2" if (d.speed_mbps or 0) <= 480 else "USB3"
        t.append(f"  {half} half", style="dim")
    for m in d.billboard or []:
        t.append(f"  {m.name}:{m.state}", style="green" if m.state == "configured" else "yellow")
    return t


def typec_label(p: TypecPort, bay_name: str | None) -> Text:
    t = Text()
    t.append(p.id, style="bold")
    if bay_name:
        t.append(f" {bay_name}", style="cyan")
    t.append(f"  {p.data_role or '?'}·{p.power_role or '?'}", style="dim")
    if p.power and p.power.online and p.power.watts:
        t.append(f"  ⚡ {p.power.watts:g} W", style="green")
    if not p.partner:
        t.append("  empty", style="dim")
    elif p.partner.altmodes:
        t.append("  " + ", ".join(a.name for a in p.partner.altmodes), style="dim cyan")
    return t


# ---------------------------------------------------------------- detail views


def _kv(rows: list[tuple[str, Any]]) -> Table:
    t = Table.grid(padding=(0, 2))
    t.add_column(style="dim", no_wrap=True)
    t.add_column()
    for k, v in rows:
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, bool):
            v = "yes" if v else "no"
        t.add_row(k, v if isinstance(v, Text) else str(v))
    return t


def _pdo_table(title: str, pdos: list[Pdo]) -> Table:
    t = Table(title=title, title_justify="left", expand=False, show_edge=False)
    for col in ("#", "type", "voltage", "current", "power"):
        t.add_column(col)
    for p in pdos:
        if p.voltage_mv is not None:
            volts = f"{p.voltage_mv / 1000:g} V"
        elif p.min_voltage_mv is not None and p.max_voltage_mv is not None:
            volts = f"{p.min_voltage_mv / 1000:g}–{p.max_voltage_mv / 1000:g} V"
        else:
            volts = "?"
        t.add_row(
            str(p.index),
            p.type.replace("_supply", ""),
            volts,
            f"{p.current_ma / 1000:g} A" if p.current_ma else "",
            f"{p.watts:g} W" if p.watts else "",
        )
    return t


def _altmode_table(title: str, modes: list[AltMode]) -> Table:
    t = Table(title=title, title_justify="left", show_edge=False)
    for col in ("SVID", "name", "mode", "active", "details"):
        t.add_column(col)
    for a in modes:
        active = "?" if a.active is None else ("yes" if a.active else "no")
        det = ", ".join(f"{k}={v}" for k, v in a.details.items() if v not in (None, [], ""))
        t.add_row(a.svid, a.name, str(a.mode or ""), active, det)
    return t


def detail_view(obj: Any, state: State) -> RenderableType:
    bays = {b.id: b.name for b in state.bays}
    if isinstance(obj, UsbDevice):
        return _device_view(obj, bays)
    if isinstance(obj, TypecPort):
        return _typec_view(obj, bays)
    if isinstance(obj, RootPort):
        return _kv(
            [
                ("Root port", Text(obj.id, style="bold")),
                ("Kernel ports", ", ".join(obj.ports)),
                ("Port keys", ", ".join(obj.keys)),
                ("Controller", obj.controller),
                ("PCI", obj.pci),
                ("ACPI", "\n".join(obj.acpi)),
                ("Connect type", obj.connect_type),
                ("Max speed", short_speed(obj.max_mbps)),
            ]
        )
    if isinstance(obj, Bay):
        return _kv(
            [
                ("Bay", Text(f"{obj.name} [{obj.id}]", style="bold")),
                ("USB port keys", ", ".join(obj.usb_ports) or "—"),
                ("USB-C ports", ", ".join(obj.typec) or "—"),
                ("Devices", ", ".join(obj.devices) or "empty"),
                ("Map", state.portmap_source),
            ]
        )
    if isinstance(obj, TbDomain):
        return _kv(
            [
                ("USB4 domain", Text(obj.id, style="bold")),
                ("PCI", obj.pci),
                ("Security", obj.security),
                ("IOMMU DMA protection", obj.iommu_dma_protection),
                ("Routers", ", ".join(r.id for r in obj.routers)),
            ]
        )
    if isinstance(obj, TbRouter):
        return _kv(
            [
                ("Router", Text(obj.id, style="bold")),
                ("Vendor / device", f"{obj.vendor or '?'} / {obj.device or '?'}"),
                ("Generation", obj.generation),
                ("RX", f"{obj.rx_speed} × {obj.rx_lanes}" if obj.rx_speed else None),
                ("TX", f"{obj.tx_speed} × {obj.tx_lanes}" if obj.tx_speed else None),
                ("Authorized", obj.authorized),
                ("USB4 ports", ", ".join(f"{k}: {v}" for k, v in obj.usb4_ports.items())),
            ]
        )
    if isinstance(obj, Issue):
        style = "yellow" if obj.severity == "warning" else "cyan"
        return _kv(
            [
                ("Severity", Text(obj.severity, style=style)),
                ("Kind", obj.kind),
                ("Ref", obj.ref),
                ("", obj.message),
            ]
        )
    if isinstance(obj, State):
        s = obj.summary
        return _kv(
            [
                ("Machine", obj.machine),
                ("Charging", f"{s.charging_w:g} W via {s.charging_port}" if s.charging_w else "no"),
                ("External devices", s.external_devices),
                ("Warnings", s.warnings),
                ("Bay map", obj.portmap_source or "none (usbinspect calibrate)"),
                ("Updated", obj.timestamp),
            ]
        )
    return Text("Select something on the left.", style="dim")


def _device_view(d: UsbDevice, bays: dict[str, str]) -> RenderableType:
    speed = _speed_text(d)
    speed.append(f"  ({d.speed_label})", style="dim")
    if d.rx_lanes and d.rx_lanes > 1:
        speed.append(f"  {d.rx_lanes} lanes", style="dim")
    maxs = f"{short_speed(d.max_mbps)} (from {d.max_source})" if d.max_mbps else "unknown (USB 2 device)"
    rows: list[tuple[str, Any]] = [
        ("Device", Text(d.product or "Unknown device", style="bold")),
        ("Manufacturer", d.manufacturer),
        ("ID", f"{d.vid}:{d.pid}"),
        ("Serial", d.serial),
        ("Class", d.device_class),
        ("Kind", d.kind + (" (internal)" if d.internal else "")),
        ("USB version", d.usb_version),
        ("Negotiated", speed),
        ("Device max", maxs),
        ("Upstream limit", short_speed(d.upstream_mbps) if d.upstream_mbps else None),
        ("Downgraded", Text(d.downgrade_reason, style="yellow") if d.downgraded else None),
        ("Max power", f"{d.max_power_ma} mA" if d.max_power_ma is not None else None),
        ("Bay", bays.get(d.bay or "")),
        ("Port key", d.port_key),
        ("Sysfs", f"/sys/bus/usb/devices/{d.name}"),
        ("Companion", d.companion),
        ("Container ID", d.container_id),
    ]
    parts: list[RenderableType] = [_kv(rows)]
    if d.interfaces:
        t = Table(title="Interfaces", title_justify="left", show_edge=False)
        for col in ("#", "class", "driver"):
            t.add_column(col)
        for i in d.interfaces:
            t.add_row(i.number, i.cls or "", i.driver or "—")
        parts += [Text(""), t]
    if d.billboard:
        t = Table(title="Billboard (alt modes the device tried)", title_justify="left", show_edge=False)
        for col in ("SVID", "name", "mode", "result"):
            t.add_column(col)
        for m in d.billboard:
            style = "green" if m.state == "configured" else "yellow"
            t.add_row(m.svid, m.name, str(m.mode), Text(m.state, style=style))
        parts += [Text(""), t]
    return Group(*parts)


def _typec_view(p: TypecPort, bays: dict[str, str]) -> RenderableType:
    rows: list[tuple[str, Any]] = [
        ("USB-C port", Text(p.id + (f"  {bays[p.bay]}" if p.bay in bays else ""), style="bold")),
        ("Data role", f"{p.data_role}  (supports {', '.join(p.data_roles)})"),
        ("Power role", f"{p.power_role}  (supports {', '.join(p.power_roles)})"),
        ("Power mode", p.power_operation_mode),
        (
            "USB capability",
            f"{p.usb_capability}  ({', '.join(p.usb_capabilities)})" if p.usb_capabilities else None,
        ),
        ("Type-C rev", p.typec_revision),
        ("PD rev", p.pd_revision),
        ("Orientation", p.orientation),
        ("VCONN source", p.vconn_source),
    ]
    pw = p.power
    if pw and pw.online:
        rows.append(
            (
                "Contract",
                Text(
                    f"{pw.watts:g} W  ({(pw.voltage_mv or 0) / 1000:g} V × {(pw.current_ma or 0) / 1000:g} A, "
                    f"{pw.usb_type}, {pw.status})",
                    style="green",
                ),
            )
        )
    pt = p.partner
    if pt:
        rows += [
            ("Partner", "connected"),
            ("Partner PD", pt.pd_revision or ("no" if pt.pd_supported is False else None)),
            ("Accessory", pt.accessory_mode),
            ("Partner type", pt.identity.product_type if pt.identity else None),
        ]
    else:
        rows.append(("Partner", Text("nothing connected", style="dim")))
    parts: list[RenderableType] = [_kv(rows)]
    if pt and pt.pd and pt.pd.source:
        parts += [Text(""), _pdo_table("Partner offers (source PDOs)", pt.pd.source)]
    if pt and pt.pd and pt.pd.sink:
        parts += [Text(""), _pdo_table("Partner wants (sink PDOs)", pt.pd.sink)]
    if pt and pt.altmodes:
        parts += [Text(""), _altmode_table("Partner alt modes", pt.altmodes)]
    if p.cable:
        c = p.cable
        parts += [
            Text(""),
            _kv(
                [
                    ("Cable", c.type or "present"),
                    ("Plug", c.plug_type),
                    ("USB speed", c.usb_speed or "no e-marker data"),
                    ("Current", f"{c.current_ma / 1000:g} A" if c.current_ma else None),
                    ("Max voltage", f"{c.max_voltage_v} V" if c.max_voltage_v else None),
                ]
            ),
        ]
    if p.supported_altmodes:
        parts += [Text(""), _altmode_table("Port supports", p.supported_altmodes)]
    if p.pd and p.pd.source:
        parts += [Text(""), _pdo_table("This port can source", p.pd.source)]
    return Group(*parts)


def raw_view(obj: Any) -> RenderableType:
    try:
        return JSON(json.dumps(asdict(obj), default=str))
    except TypeError:
        return Text(repr(obj))


# ---------------------------------------------------------------- the app


class UsbInspectApp(App):
    TITLE = "usb-superposition"
    CSS = """
    #tree { width: 1fr; min-width: 40; border-right: solid $panel; }
    #side { width: 1fr; }
    #details, #raw { padding: 0 1; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("i", "toggle_internal", "Internal"),
        Binding("m", "toggle_map", "Bays/ports"),
        Binding("e", "expand_all", "Expand"),
        Binding("d", "tab('details')", "Details"),
        Binding("w", "tab('raw')", "Raw"),
        Binding("h", "tab('events')", "Events"),
        Binding("j", "save_json", "Save JSON"),
    ]

    def __init__(
        self,
        collect_state: Callable[[], State],
        load_map: Callable[[State], portmap.PortMap | None],
        live: bool = True,
        show_internal: bool = False,
    ) -> None:
        super().__init__()
        self._collect = collect_state
        self._load_map = load_map
        self.live = live
        self.show_internal = show_internal
        self.use_map = True
        self.state: State | None = None
        self._fingerprint: str | None = None
        self._objects: dict[str, Any] = {}
        self._selected: str | None = None
        self._debounce = None
        self._stop = threading.Event()
        self.events: list[tuple[datetime, Event]] = []  # this session's events

    # -- layout

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield Tree("system", id="tree")
            with TabbedContent(id="side", initial="details"):
                with TabPane("Details", id="details"), VerticalScroll():
                    yield Static(id="details-body")
                with TabPane("Raw", id="raw"), VerticalScroll():
                    yield Static(id="raw-body")
                with TabPane("Events", id="events"):
                    yield RichLog(id="events-log", markup=False, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        tree = self.query_one(Tree)
        tree.show_root = False
        tree.guide_depth = 3
        self.refresh_state(initial=True)
        if self.live:
            self.set_interval(POLL_S, self.refresh_state)
            self._start_udev()
        tree.focus()

    def on_unmount(self) -> None:
        self._stop.set()

    # -- data

    def _load(self) -> State:
        state = self._collect()
        if self.use_map:
            portmap.apply(state, self._load_map(state))
        return state

    def refresh_state(self, initial: bool = False, force: bool = False) -> None:
        new = self._load()
        d = new.to_dict()
        d.pop("timestamp", None)
        fp = json.dumps(d, sort_keys=True, default=str)
        if fp == self._fingerprint and not force:
            return
        events = diff(self.state, new) if not initial else []
        self.state, self._fingerprint = new, fp
        self._rebuild_tree()
        self._update_header()
        for ev in events:
            self._record_event(ev)

    def _record_event(self, ev: Event) -> None:
        now = datetime.now()
        self.events.append((now, ev))
        log = self.query_one("#events-log", RichLog)
        stamp = now.strftime("%H:%M:%S")
        style = "yellow" if ev.severity == "warning" else ("dim" if ev.internal else "default")
        log.write(Text(f"{stamp}  {ev.message}", style=style))
        if not ev.internal or self.show_internal:
            self.notify(ev.message, severity="warning" if ev.severity == "warning" else "information")

    def _update_header(self) -> None:
        s = self.state.summary
        bits = []
        if s.charging_w is not None:
            bits.append(f"⚡ {s.charging_w:g} W")
        bits.append(f"{s.external_devices} devices")
        if s.warnings:
            bits.append(f"⚠ {s.warnings}")
        if not self.live:
            bits.append("snapshot")
        self.sub_title = " · ".join(bits)

    # -- tree

    def _add(
        self, parent: TreeNode, node_id: str, label: Text | str, obj: Any, leaf: bool = False
    ) -> TreeNode:
        self._objects[node_id] = obj
        data: NodeData = (node_id, obj)
        if leaf:
            return parent.add_leaf(label, data=data)
        return parent.add(label, data=data)

    def _rebuild_tree(self) -> None:
        tree = self.query_one(Tree)
        expanded = {n.data[0] for n in _iter_nodes(tree.root) if n.data and n.is_expanded}
        first_build = not self._objects
        selected = self._selected
        tree.clear()
        self._objects = {}
        st = self.state
        bay_names = {b.id: b.name for b in st.bays}

        ports = self._add(tree.root, "sec:typec", Text("USB-C ports", style="bold"), st)
        for p in st.typec_ports:
            self._add(ports, f"typec:{p.id}", typec_label(p, bay_names.get(p.bay or "")), p, leaf=True)

        def shown(d: UsbDevice) -> bool:
            return visible(d, self.show_internal)

        if st.bays:
            sec = self._add(tree.root, "sec:bays", Text("Bays", style="bold"), st)
            for b in st.bays:
                bn = self._add(sec, f"bay:{b.id}", Text(b.name, style="cyan"), b)
                tops = [d for d in _all_devices(st) if d.name in b.devices]
                for d in tops:
                    if shown(d):
                        self._add_device(bn, d, shown)
            others = [
                (rp, [d for d in rp.devices if visible_unbayed(d, self.show_internal)])
                for rp in st.root_ports
            ]
            others = [(rp, ds) for rp, ds in others if ds]
            if others:
                sec = self._add(tree.root, "sec:other", Text("Other USB ports", style="bold"), st)
                for rp, ds in others:
                    rn = self._add(sec, f"rp:{rp.id}", Text(rp.id, style="cyan"), rp)
                    for d in ds:
                        self._add_device(rn, d, lambda x: visible_unbayed(x, self.show_internal))
        else:
            sec = self._add(tree.root, "sec:usb", Text("USB root ports", style="bold"), st)
            for rp in st.root_ports:
                ds = [d for d in rp.devices if shown(d)]
                if not ds and not self.show_internal:
                    continue
                tag = Text(f"  {rp.connect_type}, ≤{short_speed(rp.max_mbps)}", style="dim")
                rn = self._add(sec, f"rp:{rp.id}", Text(rp.id, style="cyan") + tag, rp)
                for d in ds:
                    self._add_device(rn, d, shown)

        if st.thunderbolt:
            sec = self._add(tree.root, "sec:tb", Text("USB4 / Thunderbolt", style="bold"), st)
            for dom in st.thunderbolt:
                dn = self._add(sec, f"tb:{dom.id}", Text(dom.id), dom)
                for r in dom.routers:
                    label = f"{r.id} {r.vendor or ''} {r.device or ''}".strip()
                    self._add(dn, f"tbr:{r.id}", Text(label), r, leaf=True)

        if st.issues:
            sec = self._add(
                tree.root, "sec:issues", Text(f"Issues ({len(st.issues)})", style="bold yellow"), st
            )
            for n, i in enumerate(st.issues):
                mark = "⚠ " if i.severity == "warning" else "ℹ "
                self._add(sec, f"issue:{n}:{i.kind}:{i.ref}", Text(mark + i.message), i, leaf=True)

        for node in _iter_nodes(tree.root):
            if not node.data:
                continue
            nid = node.data[0]
            # first build: expand sections, bays and hubs; later: keep what the user had open
            if (first_build and not nid.startswith(("tb:", "sec:tb"))) or nid in expanded:
                node.expand()
        tree.root.expand()

        # Clearing the tree emits its own NodeHighlighted for line 0 once lines are
        # recomputed; restore the cursor after that so the user's selection wins.
        self.call_after_refresh(self._restore_cursor, selected)

    def _restore_cursor(self, selected: str | None) -> None:
        tree = self.query_one(Tree)
        target = next((n for n in _iter_nodes(tree.root) if n.data and n.data[0] == selected), None)
        if target is not None:
            tree.move_cursor(target)
            self._selected = selected
        self._show(self._selected)

    def _add_device(self, parent: TreeNode, d: UsbDevice, pred) -> None:
        kids = [k for k in d.children if pred(k)]
        node = self._add(parent, f"dev:{d.name}:{d.vid}:{d.pid}", device_label(d), d, leaf=not kids)
        for k in kids:
            self._add_device(node, k, pred)

    # -- selection / panes

    @on(Tree.NodeHighlighted)
    def _highlighted(self, event: Tree.NodeHighlighted) -> None:
        if event.node.data:
            self._selected = event.node.data[0]
            self._show(self._selected)

    def _show(self, node_id: str | None) -> None:
        obj = self._objects.get(node_id) if node_id else self.state
        self.query_one("#details-body", Static).update(detail_view(obj, self.state))
        self.query_one("#raw-body", Static).update(raw_view(obj))

    # -- actions

    def action_refresh(self) -> None:
        self.refresh_state(force=True)
        self.notify("refreshed", timeout=1)

    def action_toggle_internal(self) -> None:
        self.show_internal = not self.show_internal
        self._rebuild_tree()
        self.notify(f"internal devices {'shown' if self.show_internal else 'hidden'}", timeout=2)

    def action_toggle_map(self) -> None:
        self.use_map = not self.use_map
        self.refresh_state(force=True)
        self.notify("grouped by bays" if self.use_map else "grouped by kernel root ports", timeout=2)

    def action_expand_all(self) -> None:
        self.query_one(Tree).root.expand_all()

    def action_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab

    def action_save_json(self) -> None:
        name = f"usbinspect-{datetime.now():%Y%m%d-%H%M%S}.json"
        Path(name).write_text(json.dumps(self.state.to_dict(), indent=2) + "\n")
        self.notify(f"saved {Path(name).resolve()}")

    # -- live updates

    def _start_udev(self) -> None:
        try:
            import pyudev
        except ImportError:
            return  # polling only
        ctx = pyudev.Context()
        mon = pyudev.Monitor.from_netlink(ctx)
        for s in UDEV_SUBSYSTEMS:
            mon.filter_by(s)

        def loop() -> None:
            while not self._stop.is_set():
                dev = mon.poll(timeout=0.5)
                if dev is not None:
                    self.call_from_thread(self._udev_event)

        threading.Thread(target=loop, name="udev", daemon=True).start()

    def _udev_event(self) -> None:
        if self._debounce is not None:
            self._debounce.stop()
        self._debounce = self.set_timer(DEBOUNCE_S, self.refresh_state)


def _iter_nodes(node: TreeNode):
    for child in node.children:
        yield child
        yield from _iter_nodes(child)


def _all_devices(state: State):
    stack = [d for rp in state.root_ports for d in rp.devices]
    while stack:
        d = stack.pop(0)
        yield d
        stack.extend(d.children)


def run(collect_state, load_map, live: bool = True, show_internal: bool = False) -> None:
    UsbInspectApp(collect_state, load_map, live=live, show_internal=show_internal).run()

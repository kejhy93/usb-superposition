import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("textual")

from textual.widgets import Static, Tree

from usbinspect import portmap
from usbinspect.collect import collect
from usbinspect.sysfs import FixtureSysfs
from usbinspect.tui import UsbInspectApp, _iter_nodes

FIXTURE = Path(__file__).parent / "fixtures" / "fw16-charger-dock-hdmi.json"
USB = "/sys/bus/usb/devices"
MAP = portmap.parse(
    '[[bay]]\nid = "L3"\nname = "Left front"\nusb = ["XHC0.PRT2/1", "XHC0.PRT7/1"]\n'
    '[[bay]]\nid = "L1"\nname = "Left rear"\nusb = ["XHC3.PRT1", "XHC3.PRT2"]\ntypec = ["port0"]\n',
    source="test",
)


class World:
    """Mutable fake /sys so tests can plug and unplug things."""

    def __init__(self):
        self.base = json.loads(FIXTURE.read_text())
        self.hidden: set[str] = set()

    def collect(self):
        d = dict(self.base)
        key = f"listdir:{USB}"
        d[key] = [e for e in d[key] if not any(e == h or e.startswith(h + ".") for h in self.hidden)]
        return collect(FixtureSysfs(d))


def node_ids(app):
    return [n.data[0] for n in _iter_nodes(app.query_one(Tree).root) if n.data]


def labels(app):
    return [str(n.label) for n in _iter_nodes(app.query_one(Tree).root)]


def run(coro):
    asyncio.run(coro)


def test_tree_shows_bays_and_hides_internal():
    async def go():
        app = UsbInspectApp(World().collect, lambda s: MAP, live=False)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            ls = labels(app)
            assert any("Left front" in x for x in ls)
            assert any("HyperX QuadCast" in x for x in ls)
            assert not any("Keyboard Module" in x for x in ls)
            assert "sec:other" in node_ids(app)  # HDMI card + LAN aren't in the test map
            assert "75 W" in app.sub_title
            await pilot.press("i")
            await pilot.pause()
            assert any("Keyboard Module" in x for x in labels(app))

    run(go())


def test_selection_details_and_persistence():
    async def go():
        app = UsbInspectApp(World().collect, lambda s: MAP, live=False)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            tree = app.query_one(Tree)
            target = next(n for n in _iter_nodes(tree.root) if n.data and n.data[0].startswith("dev:1-2.1:"))
            tree.move_cursor(target)
            await pilot.pause()
            details = app.query_one("#details-body", Static).render()
            assert "HyperX QuadCast" in str(details) or app._selected.startswith("dev:1-2.1:")
            sel = app._selected
            await pilot.press("i")  # rebuild
            await pilot.pause()
            await pilot.pause()
            assert app._selected == sel
            assert tree.cursor_node.data[0] == sel

    run(go())


def test_map_toggle_switches_to_root_ports():
    async def go():
        app = UsbInspectApp(World().collect, lambda s: MAP, live=False)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            await pilot.press("m")
            await pilot.pause()
            ids = node_ids(app)
            assert "sec:usb" in ids and "sec:bays" not in ids
            assert "rp:XHC3.PRT1+PRT2" in ids

    run(go())


def test_live_change_produces_event_and_tree_update():
    async def go():
        world = World()
        app = UsbInspectApp(world.collect, lambda s: MAP, live=False)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            world.hidden.add("1-2.1")
            app.refresh_state()
            await pilot.pause()
            assert not any("HyperX QuadCast" in x for x in labels(app))
            assert [e.message for _, e in app.events] == ["Disconnected: HyperX QuadCast (Left front)"]
            world.hidden.clear()
            app.refresh_state()
            await pilot.pause()
            assert app.events[-1][1].message == "Connected: HyperX QuadCast (Left front) · 12 Mbps"
            assert any("HyperX QuadCast" in x for x in labels(app))

    run(go())


def test_unchanged_state_does_not_rebuild():
    async def go():
        app = UsbInspectApp(World().collect, lambda s: MAP, live=False)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            first = app.query_one(Tree).root.children[0]
            app.refresh_state()
            await pilot.pause()
            assert app.query_one(Tree).root.children[0] is first

    run(go())

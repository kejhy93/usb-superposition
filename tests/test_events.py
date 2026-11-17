import json
from pathlib import Path

from usbinspect import portmap
from usbinspect.collect import collect
from usbinspect.events import diff
from usbinspect.sysfs import FixtureSysfs

FIXTURE = Path(__file__).parent / "fixtures" / "fw16-charger-dock-hdmi.json"
USB = "/sys/bus/usb/devices"
PSY = "/sys/class/power_supply/ucsi-source-psy-USBC000:001"


def data():
    return json.loads(FIXTURE.read_text())


def without(d, *devices):
    """Simulate unplugging: hide devices (and their subtrees) from the USB listing."""
    d = dict(d)
    key = f"listdir:{USB}"
    d[key] = [e for e in d[key] if not any(e == x or e.startswith(x + ".") for x in devices)]
    return d


def st(d):
    return collect(FixtureSysfs(d))


def test_no_events_without_change_or_first_state():
    a = st(data())
    assert diff(None, a) == []
    assert diff(a, st(data())) == []


def test_plug_reports_only_top_of_tree():
    before, after = st(without(data(), "5-1", "6-1")), st(data())
    ev = diff(before, after)
    added = [e for e in ev if e.kind == "device_added"]
    assert [e.ref for e in added] == ["5-1", "6-1"]  # the dock's two hub halves, not its children
    assert "USB2.0 Hub" in added[0].message


def test_unplug():
    ev = diff(st(data()), st(without(data(), "1-2.1")))
    assert [(e.kind, e.ref) for e in ev] == [("device_removed", "1-2.1")]
    assert "HyperX QuadCast" in ev[0].message


def test_internal_flag():
    ev = diff(st(without(data(), "1-4.2")), st(data()))
    assert ev[0].internal is True


def test_bay_name_in_message():
    pm = portmap.parse('[[bay]]\nid = "L3"\nname = "Left front"\nusb = ["XHC0.PRT2/1"]\n')
    a = portmap.apply(st(without(data(), "1-2.1")), pm)
    b = portmap.apply(st(data()), pm)
    assert diff(a, b)[0].message == "Connected: HyperX QuadCast (Left front) · 12 Mbps"


def test_charger_connect_change_remove():
    d = data()
    off = dict(d, **{f"read:{PSY}/online": "0"})
    assert [e.kind for e in diff(st(off), st(d))] == ["pd_contract"]
    assert "Charging 75 W via port0" in diff(st(off), st(d))[0].message
    low = dict(d, **{f"read:{PSY}/current_max": "2250000"})
    kinds = [e.kind for e in diff(st(d), st(low))]
    assert kinds == ["pd_contract", "charger_low"]
    assert [e.kind for e in diff(st(d), st(off))] == ["pd_lost"]


def test_new_issue_becomes_event():
    slow = dict(data(), **{f"read:{USB}/2-1/speed": "480"})
    ev = diff(st(data()), st(slow))
    assert [e.kind for e in ev] == ["usb_fallback"]
    assert ev[0].severity == "warning"

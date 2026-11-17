import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from usbinspect import calibrate, portmap
from usbinspect.calibrate import Evidence, Observation
from usbinspect.collect import collect
from usbinspect.render import render
from usbinspect.sysfs import FixtureSysfs

FIXTURE = Path(__file__).parent / "fixtures" / "fw16-charger-dock-hdmi.json"
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)

MAP = """
machine = "Framework Laptop 16 (AMD Ryzen 7040 Series)"

[[bay]]
id = "L1"
name = "Left rear"
usb = ["XHC3.PRT1", "XHC3.PRT2"]
typec = ["port0"]

[[bay]]
id = "L3"
name = "Left front"
usb = ["XHC0.PRT2/1", "XHC0.PRT7/1"]

[[bay]]
id = "R1"
name = "Right rear"
usb = ["XHC4.PRT1", "XHC4.PRT2"]
typec = ["port3"]

[[bay]]
id = "R2"
name = "Right middle"
usb = ["XHC0.PRT1", "XHC0.PRT6"]
"""


def state():
    return collect(FixtureSysfs(json.loads(FIXTURE.read_text())), now=NOW)


def devices(s):
    def walk(ds):
        for d in ds:
            yield d
            yield from walk(d.children)

    return {d.name: d for rp in s.root_ports for d in walk(rp.devices)}


# ---- port keys -----------------------------------------------------------


def test_machine_from_dmi():
    assert state().machine == "Framework Laptop 16 (AMD Ryzen 7040 Series)"


def test_port_keys_use_acpi_names_and_hub_chain():
    d = devices(state())
    assert d["2-1"].port_key == "XHC0.PRT6"
    assert d["5-1"].port_key == "XHC3.PRT1" and d["6-1"].port_key == "XHC3.PRT2"
    assert d["1-2"].port_key == "XHC0.PRT2"
    assert d["1-2.1"].port_key == "XHC0.PRT2/1"
    assert d["5-1.5"].port_key == "XHC3.PRT1/5"


def test_root_port_keys():
    rp = {r.id: r for r in state().root_ports}
    assert rp["XHC0.PRT2+PRT7"].keys == ["XHC0.PRT2", "XHC0.PRT7"]


# ---- map files -----------------------------------------------------------


def test_parse_and_roundtrip(tmp_path):
    pm = portmap.parse(MAP)
    assert [b.id for b in pm.bays] == ["L1", "L3", "R1", "R2"]
    assert pm.bays[1].typec == []
    f = tmp_path / "ports.toml"
    portmap.save(pm, f)
    again = portmap.load_file(f)
    assert [(b.id, b.name, b.usb, b.typec) for b in again.bays] == [
        (b.id, b.name, b.usb, b.typec) for b in pm.bays
    ]
    assert again.machine == pm.machine


def test_toml_escaping(tmp_path):
    pm = portmap.PortMap(bays=[portmap.BayDef("X", 'Odd "name" \\ here', ["a"], [])])
    assert portmap.parse(portmap.to_toml(pm)).bays[0].name == 'Odd "name" \\ here'


def test_bay_requires_name():
    with pytest.raises(ValueError):
        portmap.parse('[[bay]]\nid = "x"\n')


def test_bay_for_key_prefers_longest_match():
    pm = portmap.PortMap(
        bays=[portmap.BayDef("hub", "Hub", ["XHC0.PRT2"]), portmap.BayDef("f", "Front", ["XHC0.PRT2/1"])]
    )
    assert pm.bay_for_key("XHC0.PRT2/1/3").id == "f"
    assert pm.bay_for_key("XHC0.PRT2/2").id == "hub"
    assert pm.bay_for_key("XHC0.PRT20") is None


def test_resolve_order(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert portmap.resolve("Some Other Laptop") is None
    user = tmp_path / "usbinspect" / "ports.toml"
    user.parent.mkdir()
    user.write_text(MAP)
    assert portmap.resolve("whatever").source == str(user)
    explicit = tmp_path / "x.toml"
    explicit.write_text('[[bay]]\nname = "Only"\n')
    assert portmap.resolve("whatever", explicit).bays[0].name == "Only"


# ---- applying ------------------------------------------------------------


def test_apply_assigns_devices_and_typec():
    s = portmap.apply(state(), portmap.parse(MAP, source="test"))
    d = devices(s)
    bays = {b.id: b for b in s.bays}
    assert d["5-1"].bay == d["6-1"].bay == d["5-1.1"].bay == "L1"
    assert bays["L1"].devices == ["5-1", "6-1"]  # top-most only
    assert d["1-2.1"].bay == "L3" and d["1-2"].bay is None  # internal hub is not the bay
    assert d["7-1"].bay == "R1"
    assert d["1-4.2"].bay is None
    assert {p.id: p.bay for p in s.typec_ports} == {
        "port0": "L1",
        "port1": None,
        "port2": None,
        "port3": "R1",
    }
    assert s.portmap_source == "test"


def test_render_with_bays():
    s = portmap.apply(state(), portmap.parse(MAP, source="test"))
    text = render(s)
    assert "Bays" in text and "Left rear" in text
    assert "via port0 · Left rear" in text
    left_front = text.split("Left front", 1)[1].split("Right rear", 1)[0]
    assert "HyperX QuadCast" in left_front
    assert "Keyboard" not in text
    # every external device lives in a bay, so no "Other" section
    assert "Other USB ports" not in text


def test_render_other_section_for_unmapped():
    pm = portmap.parse('[[bay]]\nid = "L1"\nname = "Left rear"\nusb = ["XHC3.PRT1", "XHC3.PRT2"]\n')
    text = render(portmap.apply(state(), pm))
    other = text.split("Other USB ports", 1)[1]
    assert "HDMI Expansion Card" in other and "USB Receiver" not in other


# ---- calibration logic ---------------------------------------------------

PEERS = {"XHC0.PRT2": ["XHC0.PRT7"], "XHC0.PRT7": ["XHC0.PRT2"], "XHC3.PRT1": ["XHC3.PRT2"]}


def obs(devices=None, partners=(), powered=()):
    return Observation(devices or {}, frozenset(partners), frozenset(powered))


def test_peer_map_from_state():
    peers = calibrate.peer_map(state())
    assert peers["XHC0.PRT2"] == ["XHC0.PRT7"]
    assert peers["XHC0.PRT1"] == ["XHC0.PRT6"]


def test_evidence_top_most_device_with_peers():
    before = obs({"1-2": "XHC0.PRT2"})
    after = obs({"1-2": "XHC0.PRT2", "1-2.1": "XHC0.PRT2/1", "1-2.1.1": "XHC0.PRT2/1/1"})
    ev = calibrate.evidence(before, after, PEERS)
    assert ev.usb == {"XHC0.PRT2/1", "XHC0.PRT7/1"}
    assert ev.typec == set()


def test_evidence_removal_and_typec():
    before = obs({"5-1": "XHC3.PRT1"}, partners={"port0"}, powered={"port0"})
    after = obs({}, partners=set(), powered=set())
    ev = calibrate.evidence(before, after, PEERS)
    assert ev.usb == {"XHC3.PRT1", "XHC3.PRT2"}
    assert ev.typec == {"port0"}


def test_watch_waits_and_settles():
    seq = iter(
        [obs(), obs(), obs({"5-1": "XHC3.PRT1"}), obs({"5-1": "XHC3.PRT1"}, partners={"port0"})]
        + [obs({"5-1": "XHC3.PRT1"}, partners={"port0"})] * 50
    )
    t = [0.0]

    def sleep(s):
        t[0] += s

    ev = calibrate.watch(lambda: next(seq), PEERS, sleep=sleep, clock=lambda: t[0])
    assert ev.usb == {"XHC3.PRT1", "XHC3.PRT2"} and ev.typec == {"port0"}


def test_wizard_scripted(monkeypatch):
    results = iter(
        [
            Evidence({"XHC3.PRT1", "XHC3.PRT2"}, set(), ["+ dock"]),  # L1: usb...
            Evidence(set(), {"port0"}, ["~ port0"]),  # ...plus more evidence
            Evidence({"XHC0.PRT2/1", "XHC0.PRT7/1"}, set(), []),  # L2 (redo)
            Evidence({"XHC0.PRT1", "XHC0.PRT6"}, set(), []),  # L2 final
            Evidence({"XHC3.PRT1", "XHC3.PRT2"}, set(), []),  # L3 steals L1's ports
        ]
    )

    def fake_watch(*a, **k):
        ev = next(results, None)
        if ev is None:
            raise KeyboardInterrupt  # remaining bays: skip
        return ev

    monkeypatch.setattr(calibrate, "watch", fake_watch)
    answers = iter(["a", "", "r", "", ""])
    said = []
    pm = calibrate.run(state, ask=lambda _: next(answers), say=said.append, sleep=lambda s: None)
    by = {b.id: b for b in pm.bays}
    assert set(by) == {"L1", "L2", "L3"}
    assert by["L1"].usb == [] and by["L1"].typec == ["port0"]
    assert by["L2"].usb == ["XHC0.PRT1", "XHC0.PRT6"]
    assert by["L3"].usb == ["XHC3.PRT1", "XHC3.PRT2"]
    assert any("already assigned to Left rear" in s for s in said)


def test_wizard_all_skipped_saves_nothing(monkeypatch):
    def skip(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(calibrate, "watch", skip)
    assert calibrate.run(state, ask=lambda _: "", say=lambda s: None) is None


def test_wizard_quit(monkeypatch):
    monkeypatch.setattr(calibrate, "watch", lambda *a, **k: Evidence({"X"}, set(), []))
    assert calibrate.run(state, ask=lambda _: "q", say=lambda s: None) is None

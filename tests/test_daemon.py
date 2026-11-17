import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from usbinspect import config, portmap, service
from usbinspect.collect import collect
from usbinspect.daemon import Daemon
from usbinspect.events import Event
from usbinspect.history import History, Record, parse_since, stats
from usbinspect.notify import Notifier
from usbinspect.sysfs import FixtureSysfs

FIXTURE = Path(__file__).parent / "fixtures" / "fw16-charger-dock-hdmi.json"
USB = "/sys/bus/usb/devices"
PSY = "/sys/class/power_supply/ucsi-source-psy-USBC000:001"
MAP = portmap.parse('[[bay]]\nid = "L3"\nname = "Left front"\nusb = ["XHC0.PRT2/1", "XHC0.PRT7/1"]\n')
T0 = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


class World:
    def __init__(self):
        self.base = json.loads(FIXTURE.read_text())
        self.hidden: set[str] = set()
        self.overrides: dict = {}

    def collect(self, cfg):
        d = dict(self.base, **self.overrides)
        key = f"listdir:{USB}"
        d[key] = [e for e in d[key] if not any(e == h or e.startswith(h + ".") for h in self.hidden)]
        return collect(FixtureSysfs(d), low_charger_w=cfg.low_charger_w)


class FakeBus:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.next_id = 7

    def __call__(self, cmd):
        self.calls.append(cmd)
        return f"(uint32 {self.next_id},)\n"


@pytest.fixture
def env(tmp_path):
    world, bus = World(), FakeBus()
    clock = {"t": T0}
    d = Daemon(
        world.collect,
        lambda s: MAP,
        config.Config(),
        History(tmp_path / "events.jsonl"),
        Notifier(run=bus, clock=lambda: clock["t"].timestamp()),
        tmp_path / "run" / "state.json",
        now=lambda: clock["t"],
        log=lambda s: None,
    )
    return d, world, bus, clock, tmp_path


def lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


# ---- daemon ----------------------------------------------------------------


def test_start_writes_snapshot_and_state(env):
    d, world, bus, clock, tmp = env
    d.start()
    recs = lines(tmp / "events.jsonl")
    assert recs[0]["kind"] == "daemon_start"
    snap = recs[0]["details"]
    assert snap["machine"].startswith("Framework")
    assert {x["product"] for x in snap["devices"]} >= {"HyperX QuadCast", "HDMI Expansion Card"}
    assert snap["power"] == [{"port": "port0", "bay": None, "watts": 75.0, "type": "PD"}]
    st = json.loads((tmp / "run" / "state.json").read_text())
    assert st["summary"]["charging_w"] == 75.0 and st["recent_events"] == [] and st["daemon"]["version"]
    assert bus.calls == []  # no notifications for the initial state


def test_no_change_no_events(env):
    d, *_ = env
    d.start()
    assert d.tick() == []


def test_unplug_logged_but_not_notified_by_default(env):
    d, world, bus, clock, tmp = env
    d.start()
    world.hidden.add("1-2.1")
    evs = d.tick()
    assert [e.kind for e in evs] == ["device_removed"]
    assert lines(tmp / "events.jsonl")[-1]["message"] == "Disconnected: HyperX QuadCast (Left front)"
    assert bus.calls == []  # device_removed is off by default
    st = json.loads((tmp / "run" / "state.json").read_text())
    assert st["recent_events"][-1]["kind"] == "device_removed"


def test_warning_and_charger_are_notified(env):
    d, world, bus, clock, tmp = env
    d.start()
    world.overrides[f"read:{USB}/2-1/speed"] = "480"
    world.overrides[f"read:{PSY}/current_max"] = "2250000"  # 45 W
    kinds = [e.kind for e in d.tick()]
    assert set(kinds) == {"pd_contract", "usb_fallback", "charger_low"}
    bodies = [c[10] for c in bus.calls]
    assert any("45 W" in b for b in bodies)
    assert any("USB 2" in b for b in bodies)


def test_config_disables_and_internal(env):
    d, world, bus, clock, tmp = env
    d.config = config.parse("[notify]\ndevice_added = true\n")
    d.start()
    world.hidden.add("1-2.1")
    d.tick()
    world.hidden.clear()
    d.tick()
    assert len(bus.calls) == 1 and "Connected: HyperX QuadCast" in bus.calls[0][10]
    world.hidden.add("1-4.2")  # internal keyboard: still not notified
    d.tick()
    world.hidden.clear()
    d.tick()
    assert len(bus.calls) == 1


def test_low_charger_threshold_from_config(env):
    d, world, bus, clock, tmp = env
    d.config = config.parse("[charger]\nlow_watts = 100\n")
    d.start()
    assert [i.kind for i in d.state.issues] == ["charger_low"]


def test_recent_events_survive_restart(env):
    d, world, bus, clock, tmp = env
    d.start()
    world.hidden.add("1-2.1")
    d.tick()
    d.stop()
    d2 = Daemon(
        world.collect, lambda s: MAP, config.Config(), d.history, None, d.state_file, log=lambda s: None
    )
    d2.start()
    assert [r["kind"] for r in d2.recent] == ["daemon_start", "device_removed", "daemon_stop"]


# ---- notifier ----------------------------------------------------------------


def test_notifier_command_quotes_and_types():
    cmd = Notifier(run=lambda c: None).command("Title's", "a (b) × c", "icon", 2, 0)
    assert cmd[6:11] == ["'usb-superposition'", "0", "'icon'", "'Title\\'s'", "'a (b) × c'"]
    assert cmd[-1] == "int32 -1" and cmd[11] == "@as []"
    assert "<byte 2>" in cmd[12]


def test_notifier_dedupes_and_replaces():
    bus, t = FakeBus(), {"t": 0.0}
    n = Notifier(run=bus, clock=lambda: t["t"], dedupe_s=30)
    ev = Event("pd_contract", "info", "Charging 65 W via port0")
    assert n.send(ev) is True
    assert n.send(ev) is False  # duplicate within 30 s
    t["t"] = 31
    assert n.send(ev) is True
    assert bus.calls[-1][7] == "7"  # replaces previous power popup
    assert n.send(Event("usb_fallback", "warning", "x")) is True
    assert bus.calls[-1][7] == "0" and bus.calls[-1][8] == "'dialog-warning'"


def test_notifier_reports_failure():
    assert Notifier(run=lambda c: None).send(Event("pd_lost", "info", "gone")) is False


# ---- history -----------------------------------------------------------------


def rec(i, kind="device_added", **kw):
    return Record(
        T0 + timedelta(minutes=i), kind, kw.pop("severity", "info"), kw.pop("message", f"m{i}"), **kw
    )


def test_history_roundtrip_and_filters(tmp_path):
    h = History(tmp_path / "e.jsonl")
    h.append(rec(0, details={"vid": "03f0", "pid": "0491", "product": "HyperX QuadCast"}, ref="1-2.1"))
    h.append(rec(1, "pd_contract", details={"watts": 65.0}))
    h.append(rec(2, "device_removed", internal=True, ref="1-4.2"))
    assert len(list(h.read())) == 3
    assert [r.kind for r in h.read(kinds={"pd_contract"})] == ["pd_contract"]
    assert [r.ts.minute for r in h.read(since=T0 + timedelta(minutes=1))] == [1, 2]
    assert len(list(h.read(include_internal=False))) == 2
    assert len(list(h.read(match="03f0:0491"))) == 1
    assert len(list(h.read(match="quadcast"))) == 1
    assert len(list(h.read(match="1-4.2"))) == 1
    assert [r.minute for r in [x.ts for x in h.tail(2)]] == [1, 2]


def test_history_rotation(tmp_path):
    h = History(tmp_path / "e.jsonl", max_bytes=300, keep=2)
    for i in range(40):
        h.append(rec(i, message="x" * 40))
    files = h.files()
    assert [f.name for f in files] == ["e.jsonl.2", "e.jsonl.1", "e.jsonl"]
    times = [r.ts for r in h.read()]
    assert times == sorted(times)  # oldest file first
    assert times[-1] == T0 + timedelta(minutes=39)


def test_history_tolerates_torn_line(tmp_path):
    p = tmp_path / "e.jsonl"
    h = History(p)
    h.append(rec(0))
    with p.open("a") as f:
        f.write('{"ts": "2026-11-17T12:0')
    assert len(list(h.read())) == 1


def test_parse_since():
    now = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    assert parse_since("90m", now) == now - timedelta(minutes=90)
    assert parse_since("2d", now) == now - timedelta(days=2)
    assert parse_since("2026-11-01T00:00:00+00:00", now) == datetime(2026, 11, 1, tzinfo=UTC)


def test_stats():
    dev = {"vid": "0bda", "pid": "8153", "product": "LAN", "speed_mbps": 5000}
    recs = [
        rec(0, details=dev, ref="2-1"),
        rec(1, "device_removed", details=dev, ref="2-1"),
        rec(2, details=dict(dev, speed_mbps=480), ref="2-1"),
        rec(3, "usb_fallback", severity="warning", ref="2-1"),
        rec(4, "pd_contract", details={"watts": 65.0}),
        rec(5, "pd_contract", details={"watts": 65.0}),
    ]
    s = stats(recs)
    lan = s["devices"]["0bda:8153"]
    assert (lan["connects"], lan["disconnects"]) == (2, 1)
    assert lan["speeds_mbps"] == {5000: 1, 480: 1}
    assert lan["warnings"] == {"usb_fallback": 1}
    assert s["charger_watts"] == {65.0: 2}


# ---- config / service ----------------------------------------------------------


def test_config_defaults_and_example_parses():
    c = config.Config()
    assert c.wants("pd_contract") and not c.wants("device_added")
    assert not c.wants("pd_contract", internal=True)
    ex = config.parse(config.EXAMPLE)
    assert ex.notify == config.DEFAULT_NOTIFY and ex.low_charger_w == 60


def test_config_missing_file(tmp_path):
    assert config.load(tmp_path / "nope.toml").notify == config.DEFAULT_NOTIFY


def test_service_unit():
    unit = service.render_unit("/home/u/.local/bin/usbinspect")
    assert "ExecStart=/home/u/.local/bin/usbinspect daemon" in unit
    assert "WantedBy=graphical-session.target" in unit

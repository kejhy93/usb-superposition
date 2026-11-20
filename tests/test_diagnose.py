"""`usbinspect diagnose` and `history --clear`: is it the cable, the bay, the device or the charger?"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from usbinspect import cli, diagnose, portmap
from usbinspect.collect import collect
from usbinspect.events import _walk, diff
from usbinspect.history import History, Record
from usbinspect.sysfs import FixtureSysfs

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "fw16-charger-dock-hdmi.json"
MAP = FIXTURES / "fw16-ports.toml"
PD = "/sys/devices/platform/USBC000:00/typec/port0/port0-partner/pd2/source-capabilities"
PSY = "/sys/class/power_supply/ucsi-source-psy-USBC000:001"
T0 = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def load(bays=True, **overrides):
    data = json.loads(FIXTURE.read_text())
    data.update(overrides)
    state = collect(FixtureSysfs(data), now=T0)
    if bays:
        portmap.apply(state, portmap.load_file(MAP))
    return state


def find(state, name):
    return next(d for rp in state.root_ports for d in _walk(rp.devices) if d.name == name)


def added(i, d, speed, bay):
    return Record(
        T0 + timedelta(minutes=i),
        "device_added",
        "info",
        f"Connected: {d.product}",
        d.name,
        details={"vid": d.vid, "pid": d.pid, "product": d.product, "speed_mbps": speed, "bay": bay},
    )


# ---------------------------------------------------------------- power


def test_power_charger_is_the_limit():
    v = diagnose.power_limit(load().typec_ports[0])  # 75 W contract = best offer 20 V 3.75 A
    assert v.kind == "charger" and "75 W" in v.text


def test_power_laptop_asked_for_less():
    state = load(**{f"read:{PSY}/current_now": "2250000"})  # 45 W of the 75 W offered
    v = diagnose.power_limit(state.typec_ports[0])
    assert v.kind == "laptop" and "75 W" in v.text


def test_power_3a_at_20v_may_be_the_cable():
    state = load(
        **{f"read:{PD}/4:fixed_supply/maximum_current": "3000mA", f"read:{PSY}/current_now": "3000000"}
    )
    v = diagnose.power_limit(state.typec_ports[0])
    assert v.kind == "charger_or_cable" and "5 A cable" in v.text


def test_low_charger_issue_says_why():
    state = load(
        bays=False,
        **{f"read:{PD}/4:fixed_supply/maximum_current": "2250mA", f"read:{PSY}/current_now": "2250000"},
    )
    msg = next(i.message for i in state.issues if i.kind == "charger_low")
    assert msg == "port0: charging at only 45 W — the charger is the limit: its best offer is 45 W"


# ---------------------------------------------------------------- data speed


@pytest.fixture
def slow_lan():
    """The 5 Gbps LAN adapter (2-1) stuck at USB 2 speed."""
    state = load(**{"read:/sys/bus/usb/devices/2-1/speed": "480"})
    lan = find(state, "2-1")
    assert lan.downgraded and lan.bay
    return state, lan


def test_cable_when_same_device_was_faster_in_this_bay(slow_lan):
    state, lan = slow_lan
    recs = [added(0, lan, 5000.0, lan.bay), added(5, lan, 480.0, lan.bay)]
    sel = diagnose.select(state, lan.bay)
    rep = diagnose.diagnose(state, sel, recs)
    (d, v) = next((d, v) for d, v in rep.devices if d.name == "2-1")
    assert v.kind == "cable" and "5 Gbps" in v.text
    assert rep.best_speed.mbps == 5000.0
    assert "the cable (or a loose plug)" in diagnose.render_report(rep)


def test_bay_or_cable_when_device_was_faster_elsewhere(slow_lan):
    state, lan = slow_lan
    recs = [added(0, lan, 5000.0, "R1")]
    v = diagnose.data_verdict(lan, [], recs, {"R1": "Right rear"})
    assert v.kind == "bay_or_cable" and "Right rear" in v.text


def test_cable_or_device_when_bay_was_faster_with_another_device(slow_lan):
    state, lan = slow_lan
    other = find(state, "1-2.1")  # some other device, recorded at 10 Gbps in this bay
    here = [added(0, other, 10000.0, lan.bay)]
    assert diagnose.data_verdict(lan, here, here, {}).kind == "cable_or_device"


def test_untested_without_history(slow_lan):
    state, lan = slow_lan
    v = diagnose.data_verdict(lan, [], [], {})
    assert v.kind == "untested" and "another" in v.text


def test_full_speed_is_ok():
    state = load()
    assert diagnose.data_verdict(find(state, "2-1"), [], [], {}).kind == "ok"


# ---------------------------------------------------------------- selection & history


def test_select_by_bay_id_name_or_port():
    state = load()
    assert diagnose.select(state, "r1").bay == "R1"
    assert diagnose.select(state, "Right Rear").bay == "R1"
    assert diagnose.select(state, "port0").bay == "R1"  # a port in a bay selects the bay
    assert diagnose.select(state, "port1").bay is None  # not in any bay
    assert diagnose.select(state, "nope") is None
    unmapped = load(bays=False)
    assert diagnose.select(unmapped, "XHC3.PRT1").usb_keys == ["XHC3.PRT1", "XHC3.PRT2"]


def test_old_pd_records_match_their_bay_through_the_port():
    state = load()
    sel = diagnose.select(state, "R1")
    old = Record(T0, "pd_contract", "info", "Charging 60 W via port0", "port0", details={"watts": 60.0})
    other = Record(T0, "pd_contract", "info", "Charging via port3", "port3", details={"watts": 30.0})
    pb = diagnose.port_bays(state)
    assert diagnose.record_matches(sel, old, pb)
    assert not diagnose.record_matches(sel, other, pb)


def test_events_carry_the_bay():
    old, new = load(), load()
    new.typec_ports[0].power.watts = 60.0  # contract changed
    ev = next(e for e in diff(old, new) if e.kind == "pd_contract")
    assert ev.details["bay"] == "R1" and ev.details["offer_w"] == 75.0


def test_history_clear_removes_only_matching(tmp_path):
    h = History(tmp_path / "events.jsonl", max_bytes=200, keep=2)
    for i in range(6):  # small max_bytes: spread over rotated files
        h.append(
            Record(T0 + timedelta(minutes=i), "device_added", "info", f"m{i}", details={"bay": "AB"[i % 2]})
        )
    with h.path.open("a") as f:
        f.write("{torn\n")
    assert len(h.files()) > 1
    assert h.clear(lambda r: (r.details or {}).get("bay") == "A") == 3
    assert [r.message for r in h.read()] == ["m1", "m3", "m5"]
    assert "{torn" in h.path.read_text()  # unparsable lines are kept, not silently dropped


def test_cli_history_clear(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    reloads = []
    monkeypatch.setattr(cli, "_reload_daemon", lambda: reloads.append(1))
    h = History()
    h.append(Record(T0, "pd_contract", "info", "Charging 60 W via port0", "port0", details={"watts": 60.0}))
    h.append(Record(T0, "device_added", "info", "Connected: X", "3-1", details={"bay": "L1"}))
    args = ["--fixture", str(FIXTURE), "--map", str(MAP), "history", "--clear"]
    assert cli.main([*args, "Right rear"]) == 2  # stdin isn't a tty and no --yes
    assert cli.main([*args, "Right rear", "--yes"]) == 0
    assert "deleted 1 event(s) of Right rear [R1]" in capsys.readouterr().out
    assert [r.message for r in History().read()] == ["Connected: X"]
    assert reloads == [1]
    assert cli.main([*args, "bogus", "--yes"]) == 2


def test_cli_diagnose_runs_on_fixture(capsys):
    assert cli.main(["--fixture", str(FIXTURE), "--map", str(MAP), "diagnose"]) == 0
    out = capsys.readouterr().out
    assert "Right rear [R1]" in out and "the charger is the limit: its best offer is 75 W" in out

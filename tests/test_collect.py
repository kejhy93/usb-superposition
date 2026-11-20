import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from usbinspect.collect import collect
from usbinspect.render import render
from usbinspect.sysfs import FixtureSysfs

FIXTURE = Path(__file__).parent / "fixtures" / "fw16-charger-dock-hdmi.json"
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
USB = "/sys/bus/usb/devices"
PSY = "/sys/class/power_supply/ucsi-source-psy-USBC000:001"


def load(**overrides):
    data = json.loads(FIXTURE.read_text())
    data.update(overrides)
    return collect(FixtureSysfs(data), now=NOW)


def devices(state):
    def walk(ds):
        for d in ds:
            yield d
            yield from walk(d.children)

    return {d.name: d for rp in state.root_ports for d in walk(rp.devices)}


@pytest.fixture(scope="module")
def state():
    return load()


def test_charging_contract(state):
    assert state.summary.charging_port == "port0"
    assert state.summary.charging_w == 75.0
    p0 = state.typec_ports[0]
    assert p0.power_role == "sink" and p0.data_role == "host"
    assert (p0.power.voltage_mv, p0.power.current_ma, p0.power.usb_type) == (20000, 3750, "PD")


def test_charger_offers(state):
    pd = state.typec_ports[0].partner.pd
    fixed = [(p.voltage_mv, p.current_ma) for p in pd.source]
    assert (20000, 3750) in fixed and (9000, 1330) in fixed
    assert pd.max_source_w == 75.0
    # the snapshot has the UCSI artifact: PDO 5 is a copy of PDO 1, which PD forbids
    assert [p.index for p in pd.source] == [1, 2, 3, 4]


def test_partner_altmodes_listed(state):
    names = {a.name for a in state.typec_ports[0].partner.altmodes}
    assert names == {"DisplayPort", "Thunderbolt"}


def test_offline_supply_has_no_bogus_numbers(state):
    p1 = state.typec_ports[1].power
    assert p1.online is False
    assert p1.watts is None and p1.voltage_mv is None


def test_negotiated_vs_max(state):
    d = devices(state)
    assert d["2-1"].speed_mbps == 5000 and d["2-1"].max_mbps == 5000
    assert d["2-2"].speed_mbps == 10000 and d["2-2"].max_mbps == 10000
    assert d["2-2"].max_source == "bos"
    assert not any(x.downgraded for x in d.values())


def test_usb3_hub_halves_are_companions_not_downgrades(state):
    d = devices(state)
    assert d["5-1"].companion == "6-1" and d["6-1"].companion == "5-1"
    assert d["5-1"].container_id == d["6-1"].container_id
    assert d["5-1"].downgraded is False


def test_internal_vs_external(state):
    d = devices(state)
    assert d["1-4.2"].internal and d["1-4.2"].kind == "input_module"  # keyboard
    assert d["1-4.1"].internal  # fingerprint
    assert d["1-5"].internal  # wifi/bt
    assert not d["1-2.1"].internal  # QuadCast
    assert d["7-1"].kind == "expansion_card" and not d["7-1"].internal
    assert state.summary.external_devices == 4


def test_billboard_altmode(state):
    bb = devices(state)["5-1.5"].billboard
    assert [(m.name, m.state) for m in bb] == [("DisplayPort", "configured")]


def test_root_ports_paired(state):
    rp = {r.id: r for r in state.root_ports}
    assert rp["XHC0.PRT1+PRT6"].ports == ["usb1-port1", "usb2-port1"]
    assert rp["XHC0.PRT1+PRT6"].connect_type == "hotplug"
    assert rp["XHC0.PRT1+PRT6"].max_mbps == 10000


def test_thunderbolt_domains(state):
    assert [d.id for d in state.thunderbolt] == ["domain0", "domain1"]
    assert state.thunderbolt[0].routers[0].generation == 4


def test_no_issues_on_healthy_setup(state):
    assert state.issues == []


def test_json_serialisable(state):
    json.dumps(state.to_dict())


# ---- simulated problems -------------------------------------------------


def test_superspeed_device_on_usb2_link_warns():
    s = load(**{f"read:{USB}/2-1/speed": "480"})
    lan = devices(s)["2-1"]
    assert lan.downgraded and "USB 2" in lan.downgrade_reason
    assert [i.kind for i in s.issues] == ["usb_fallback"]
    assert s.summary.warnings == 1


def test_gen2_device_at_gen1_warns_slow_link():
    s = load(**{f"read:{USB}/2-2/speed": "5000"})
    hub = devices(s)["2-2"]
    assert hub.downgraded and "cable" in hub.downgrade_reason
    assert any(i.kind == "usb_slow_link" for i in s.issues)


def test_non_pd_charger_warns():
    s = load(
        **{
            f"read:{PSY}/usb_type": "[C] PD PD_PPS",
            f"read:{PSY}/voltage_now": "5000000",
            f"read:{PSY}/current_max": "3000000",
        }
    )
    assert s.summary.charging_w == 15.0
    assert [i.kind for i in s.issues] == ["charger_not_pd"]


def test_low_pd_charger_warns():
    s = load(**{f"read:{PSY}/current_max": "2250000"})  # 20 V × 2.25 A = 45 W
    assert s.summary.charging_w == 45.0
    assert [i.kind for i in s.issues] == ["charger_low"]


def test_contract_uses_negotiated_current_not_max_pdo_current():
    """A 45 W charger (20 V PDO at 2.25 A) whose 5-15 V PDOs allow 3 A: current_max is 3 A."""
    s = load(**{f"read:{PSY}/current_max": "3000000", f"read:{PSY}/current_now": "2250000"})
    assert s.typec_ports[0].power.current_ma == 2250
    assert s.summary.charging_w == 45.0
    assert [i.kind for i in s.issues] == ["charger_low"]


def test_failed_altmode_warns():
    import base64

    key = f"read_bytes:{USB}/5-1.5/bos_descriptors"
    raw = bytearray(base64.b64decode(json.loads(FIXTURE.read_text())[key]))
    raw[5 + 8] = 0x02
    s = load(**{key: base64.b64encode(bytes(raw)).decode()})
    assert [i.kind for i in s.issues] == ["altmode_failed"]


# ---- rendering ----------------------------------------------------------


def test_render_hides_internal_by_default(state):
    text = render(state)
    assert "HyperX QuadCast" in text
    assert "Keyboard Module" not in text
    assert "Keyboard Module" in render(state, show_internal=True)
    assert "Charging 75 W" in text

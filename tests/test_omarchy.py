"""The Omarchy bar widget (omarchy/): manifest, strings, PlainText safety and the JS view model.

Model.js is pure JavaScript, so it runs under node here against a state.json built
from the recorded Framework 16 snapshot exactly the way the daemon builds it.
"""

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from usbinspect import portmap
from usbinspect.collect import collect
from usbinspect.sysfs import FixtureSysfs

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "omarchy"
FIXTURES = Path(__file__).parent / "fixtures"
QML = sorted(PLUGIN.glob("*.qml"))
EN = json.loads((PLUGIN / "i18n" / "en.json").read_text())
DEVICE_KINDS = ("input_module", "expansion_card", "billboard")  # model.UsbDevice.kind besides hub/device


def test_manifest_points_at_real_files():
    m = json.loads((PLUGIN / "manifest.json").read_text())
    assert m["schemaVersion"] == 1
    assert m["kinds"] == ["bar-widget"]
    assert (PLUGIN / m["entryPoints"]["barWidget"]).is_file()
    assert m["barWidget"]["allowMultiple"] is False
    # moduleName / ipcTarget in the QML must match the manifest id
    for f in QML:
        for name in re.findall(r'(?:moduleName|ipcTarget):\s*"([^"]+)"', f.read_text()):
            assert name == m["id"], f.name


def _static_keys() -> set[str]:
    keys = set()
    for f in [*QML, PLUGIN / "Model.js"]:
        keys |= set(re.findall(r'\b(?:strings\.)?t\("([^"]+)"\)', f.read_text()))
    return keys


def test_every_string_key_exists_in_en_json():
    missing = _static_keys() - EN.keys()
    assert not missing


def test_no_unused_strings():
    used = _static_keys() | {f"kind.{k}" for k in DEVICE_KINDS}
    assert not (EN.keys() - used)


def _blocks(src: str, opener: str):
    """Bodies of `opener {` blocks, with nested blocks blanked out."""
    for m in re.finditer(opener, src):
        i, depth = m.end(), 1
        body = []
        while depth:
            ch = src[i]
            depth += ch == "{"
            depth -= ch == "}"
            if depth == 1 or (depth == 2 and ch == "{"):
                body.append(ch)
            i += 1
        yield "".join(body)


def test_every_text_is_plain_text():
    """Descriptor strings are untrusted: no Text may fall back to AutoText/RichText."""
    for f in QML:
        src = f.read_text()
        for body in _blocks(src, r"(?<![\w.])Text\s*\{"):
            assert "textFormat: Text.PlainText" in body, f"{f.name}: Text without PlainText"
        assert "RichText" not in src and "StyledText" not in src


def test_qml_runs_only_the_fixed_refresh_command():
    """The only command is the daemon reload, with a literal argv (no state values)."""
    commands = []
    for f in QML:
        src = f.read_text()
        assert "execDetached" not in src, f.name
        commands += re.findall(r"command:\s*(.+)", src)
    assert commands == ['["systemctl", "--user", "reload", "usbinspect.service"]']


# ---------- Model.js under node ----------

NODE_RUNNER = r"""
const fs = require("fs");
const [model, docPath, enPath, showInternal] = process.argv.slice(2);
// Qt's String.prototype.arg: replace the lowest-numbered %N.
String.prototype.arg = function (v) {
  const ns = (this.match(/%\d+/g) || []).map((x) => +x.slice(1));
  if (!ns.length) return String(this);
  return this.split("%" + Math.min(...ns)).join(String(v));
};
const src = fs.readFileSync(model, "utf8").replace(/^\.pragma.*$/m, "");
const M = new Function(src + "\nreturn { parseState, buildView, speedText };")();
const en = JSON.parse(fs.readFileSync(enPath, "utf8"));
const missing = [];
const t = (k) => { if (!(k in en)) missing.push(k); return en[k] || k; };
const doc = M.parseState(fs.readFileSync(docPath, "utf8"));
const view = M.buildView(doc, showInternal === "1", t);
process.stdout.write(JSON.stringify({ view, missing, speeds: [M.speedText(480), M.speedText(5000), M.speedText(null)] }));
"""


def state_doc(**mutate) -> dict:
    data = json.loads((FIXTURES / "fw16-charger-dock-hdmi.json").read_text())
    state = collect(FixtureSysfs(data), now=datetime(2026, 11, 17, 12, 0, tzinfo=UTC))
    if mutate.get("bays", True):
        portmap.apply(state, portmap.load_file(FIXTURES / "fw16-ports.toml"))
    doc = state.to_dict()
    doc["recent_events"] = [
        {
            "ts": "2026-11-17T11:58:00+00:00",
            "kind": "device_added",
            "severity": "info",
            "message": "LAN connected",
        },
        {
            "ts": "2026-11-17T11:59:00+00:00",
            "kind": "pd_contract",
            "severity": "warning",
            "message": "45 W only",
        },
    ]
    doc["daemon"] = {"pid": 1, "version": "test", "history": "/dev/null"}
    return doc


@pytest.fixture
def run_model(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    runner = tmp_path / "runner.js"
    runner.write_text(NODE_RUNNER)

    def run(doc, show_internal=False):
        p = tmp_path / "state.json"
        p.write_text(doc if isinstance(doc, str) else json.dumps(doc, default=str))
        out = subprocess.run(
            [
                node,
                str(runner),
                str(PLUGIN / "Model.js"),
                str(p),
                str(PLUGIN / "i18n" / "en.json"),
                "1" if show_internal else "0",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        res = json.loads(out.stdout)
        assert not res["missing"], res["missing"]
        return res

    return run


def test_model_summary_and_ports(run_model):
    res = run_model(state_doc())
    v = res["view"]
    assert res["speeds"] == ["480 Mbps", "5 Gbps", "?"]
    assert v["badge"] == "75W"
    assert v["warning"] is False
    assert v["tooltip"] == "Charging 75 W · 4 external USB devices"
    assert v["charging"] == "75 W · 20 V × 3.75 A · PD"
    assert v["chargingWhere"] == "Right rear"
    p0 = v["ports"][0]
    assert (p0["label"], p0["sublabel"], p0["roles"]) == ("Right rear", "port0", "host · sink")
    assert p0["offers"] == "75 W"
    assert [a["name"] for a in p0["altmodes"]] == ["Thunderbolt", "DisplayPort"]
    assert v["ports"][1]["label"] == "port1"  # no bay


def test_model_groups_by_bay_and_hides_internal(run_model):
    v = run_model(state_doc())["view"]
    assert v["mapped"] is True
    titles = [g["title"] for g in v["groups"]]
    assert titles == ["Right rear", "Left rear", "Left front", "Other USB ports"]
    rear = v["groups"][0]
    assert rear["watts"] == "75 W"
    assert [(r["depth"], r["name"]) for r in rear["rows"]] == [
        (0, "USB2.0 Hub"),
        (1, "USB Receiver"),
        (1, "USB Billboard Device"),
        (0, "USB3.0 Hub"),
    ]
    # the USB 2 half of the dock hub is not a downgrade, so no "/ max" for it
    assert (rear["rows"][0]["speed"], rear["rows"][0]["maxSpeed"]) == ("480 Mbps", "")
    assert rear["rows"][0]["ratio"] == -1  # ...and no speed bar either
    assert "USB 2 half" in rear["rows"][0]["tags"]
    assert v["chargingW"] == "75 W"
    assert v["updated"] == 1794916800000  # state timestamp, 2026-11-17 12:00 UTC
    billboard = rear["rows"][2]
    assert billboard["modes"] == [{"name": "DisplayPort", "ok": True, "state": "configured"}]
    assert "billboard" in billboard["tags"]
    # the internal hub is shown only because the external QuadCast hangs below it
    other = [r["name"] for r in v["groups"][3]["rows"]]
    assert other == ["USB2.1 Hub", "HyperX QuadCast"]
    names = {r["name"] for g in v["groups"] for r in g["rows"]}
    assert "Laptop 16 Keyboard Module - ANSI" not in names


def test_model_show_internal(run_model):
    v = run_model(state_doc(), show_internal=True)["view"]
    names = {r["name"] for g in v["groups"] for r in g["rows"]}
    assert {"Laptop 16 Keyboard Module - ANSI", "Goodix Fingerprint USB Device", "USB3.2 Hub"} <= names


def test_model_without_bays_groups_by_root_port(run_model):
    v = run_model(state_doc(bays=False))["view"]
    assert v["mapped"] is False
    first = v["groups"][0]
    assert first["title"] == "XHC0.PRT1+PRT6"
    assert first["subtitle"] == "up to 10 Gbps"


def test_model_downgrade_and_issues(run_model):
    doc = state_doc()
    lan = next(d for rp in doc["root_ports"] for d in rp["devices"] if d["name"] == "2-1")
    lan.update(speed_mbps=480.0, downgraded=True, downgrade_reason="bad cable?")
    doc["issues"] = [
        {"severity": "warning", "kind": "speed_downgrade", "message": "LAN at 480 Mbps", "ref": "2-1"}
    ]
    doc["summary"]["warnings"] = 1
    v = run_model(doc)["view"]
    row = next(r for g in v["groups"] for r in g["rows"] if r["key"] == "2-1")
    assert (row["speed"], row["maxSpeed"], row["downgraded"], row["reason"]) == (
        "480 Mbps",
        "5 Gbps",
        True,
        "bad cable?",
    )
    assert row["ratio"] == pytest.approx(480 / 5000)
    assert v["warning"] is True
    assert v["issues"] == [{"warning": True, "message": "LAN at 480 Mbps"}]
    assert v["tooltip"].endswith("· 1 warning")


def test_model_events_newest_first(run_model):
    ev = run_model(state_doc())["view"]["events"]
    assert [e["message"] for e in ev] == ["45 W only", "LAN connected"]
    assert ev[0]["warning"] is True
    assert ev[0]["time"] == 1794916740000


def test_model_badge_device_count_when_not_charging(run_model):
    doc = state_doc()
    doc["summary"].update(charging_w=None, charging_port=None)
    v = run_model(doc)["view"]
    assert v["badge"] == "4"
    assert v["charging"] == ""


def test_model_sanitises_descriptor_strings(run_model):
    doc = state_doc()
    lan = next(d for rp in doc["root_ports"] for d in rp["devices"] if d["name"] == "2-1")
    lan["product"] = "<b>evil</b>\n" + "x" * 200
    row = next(r for g in run_model(doc)["view"]["groups"] for r in g["rows"] if r["key"] == "2-1")
    assert "\n" not in row["name"]
    assert len(row["name"]) == 64 and row["name"].endswith("…")


@pytest.mark.parametrize("text", ["", "not json", "[]", '{"schema": 1}', '{"schema": 2}'])
def test_model_rejects_bad_state(run_model, text):
    assert run_model(text)["view"] is None

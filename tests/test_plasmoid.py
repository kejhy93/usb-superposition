"""The Plasma 6 plasmoid (plasmoid/): package metadata, strings, PlainText safety, commands.

The view model is the Omarchy widget's Model.js (symlinked), tested in test_omarchy.py.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "plasmoid"
UI = PKG / "contents" / "ui"
QML = sorted(UI.glob("*.qml"))
MODEL = UI / "Model.js"
DEVICE_KINDS = ("input_module", "expansion_card", "billboard")


def strings() -> dict[str, str]:
    """The `en` object from Strings.js (it's plain JSON inside a JS file)."""
    src = (UI / "Strings.js").read_text()
    body = re.search(r"var en = (\{.*?\n\})", src, re.S).group(1)
    return json.loads(body)


EN = strings()


def test_metadata():
    m = json.loads((PKG / "metadata.json").read_text())
    assert m["KPackageStructure"] == "Plasma/Applet"
    assert m["X-Plasma-API-Minimum-Version"] == "6.0"
    assert m["KPlugin"]["Id"] == "org.hejnaluk.usbsuperposition"
    assert (UI / "main.qml").is_file()  # the default main script for Plasma/Applet
    # install.sh links the package under the same id
    assert f'PLASMOID_ID="{m["KPlugin"]["Id"]}"' in (ROOT / "install.sh").read_text()


def test_model_is_the_shared_omarchy_model():
    assert MODEL.is_symlink()
    assert MODEL.resolve() == (ROOT / "omarchy" / "Model.js").resolve()
    # relative, so it survives the repo being cloned or the package being linked elsewhere
    assert not Path(MODEL.readlink()).is_absolute()


def test_showinternal_config_entry():
    xml = (PKG / "contents" / "config" / "main.xml").read_text()
    assert '<entry name="showInternal" type="Bool">' in xml
    assert "Plasmoid.configuration.showInternal" in (UI / "main.qml").read_text()


def _static_keys() -> set[str]:
    keys = set()
    for f in [*QML, MODEL]:
        keys |= set(re.findall(r'\bt\("([^"]+)"\)', f.read_text()))
    return keys


def test_every_string_key_exists():
    assert not (_static_keys() - EN.keys())


def test_no_unused_strings():
    used = _static_keys() | {f"kind.{k}" for k in DEVICE_KINDS}
    assert not (EN.keys() - used)


def test_model_strings_match_omarchy():
    """Model.js output reads the same in both widgets."""
    omarchy = json.loads((ROOT / "omarchy" / "i18n" / "en.json").read_text())
    model_keys = set(re.findall(r'\bt\("([^"]+)"\)', MODEL.read_text())) | {f"kind.{k}" for k in DEVICE_KINDS}
    for key in model_keys:
        assert EN[key] == omarchy[key], key


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


def test_every_label_is_plain_text():
    """Descriptor strings are untrusted: no label may fall back to AutoText/RichText."""
    opener = r"(?<![\w.])(?:Text|PlasmaComponents3\.Label|Kirigami\.Heading)\s*\{"
    found = 0
    for f in QML:
        src = f.read_text()
        for body in _blocks(src, opener):
            found += 1
            assert "textFormat: Text.PlainText" in body, f"{f.name}: label without PlainText"
        assert "RichText" not in src and "StyledText" not in src
    assert found
    assert "toolTipTextFormat: Text.PlainText" in (UI / "main.qml").read_text()


def test_runs_only_two_fixed_commands():
    """The executable engine runs a shell, so only literal commands may reach it."""
    main = (UI / "main.qml").read_text()
    cmds = dict(re.findall(r'readonly property string (\w+Cmd):\s*"((?:[^"\\]|\\.)*)"', main))
    assert cmds == {
        "readCmd": r"cat -- \"${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/usbinspect/state.json\"",
        "refreshCmd": "systemctl --user reload usbinspect.service",
    }
    for f in QML:
        src = f.read_text()
        for arg in re.findall(r"(?<!dis)connectSource\(([^)]*)\)", src):
            assert arg in ("root.readCmd", "root.refreshCmd"), f"{f.name}: connectSource({arg})"
        assert "execDetached" not in src and "Qt.openUrlExternally" not in src


def test_strings_js_under_node(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    runner = tmp_path / "r.js"
    runner.write_text(
        "const src = require('fs').readFileSync(process.argv[2], 'utf8').replace(/^\\.pragma.*$/m, '');\n"
        "const S = new Function(src + '\\nreturn { t };')();\n"
        "process.stdout.write(JSON.stringify([S.t('group.other'), S.t('nope'), S.t('toString')]));\n"
    )
    out = subprocess.run(
        [node, str(runner), str(UI / "Strings.js")], capture_output=True, text=True, check=True
    )
    assert json.loads(out.stdout) == ["Other USB ports", "nope", "toString"]

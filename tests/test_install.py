"""install.sh against a throwaway HOME, with systemctl / omarchy / the venv stubbed out.

The stubs log every call to calls.log, so the tests check what the script would run
without touching the real session, the real bar or the network.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "install.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

STUB = """#!/bin/sh
echo "$(basename "$0") $*" >> "$CALLS"
{extra}
exit 0
"""


def _stub(path: Path, extra: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(STUB.format(extra=extra))
    path.chmod(0o755)


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "home"
    (home / ".config" / "omarchy" / "plugins").mkdir(parents=True)
    stubs = tmp_path / "stubs"
    # Only the tools install.sh needs, so the host's real omarchy / systemctl are never found.
    for tool in [
        "sh",
        "bash",
        "dirname",
        "basename",
        "readlink",
        "ln",
        "mkdir",
        "rm",
        "printf",
        "cat",
        "sleep",
    ]:
        found = shutil.which(tool)
        if found:
            (stubs / tool).parent.mkdir(parents=True, exist_ok=True)
            (stubs / tool).symlink_to(found)
    for name in ("systemctl", "omarchy", "omarchy-shell"):
        _stub(stubs / name)
    venv = tmp_path / "venv"
    _stub(venv / "bin" / "python")
    _stub(venv / "bin" / "usbinspect")
    calls = tmp_path / "calls.log"
    calls.touch()
    e = {
        "HOME": str(home),
        "PATH": f"{stubs}:{home / '.local' / 'bin'}",
        "USBINSPECT_VENV": str(venv),
        "CALLS": str(calls),
        "TERM": "dumb",
        "RETRY_DELAY": "0",
    }
    return e, home, venv, calls, stubs


def run(e, *args, check=True):
    r = subprocess.run([shutil.which("bash"), str(SCRIPT), *args], env=e, capture_output=True, text=True)
    if check:
        assert r.returncode == 0, r.stderr
    return r


def lines(calls: Path) -> list[str]:
    return calls.read_text().splitlines()


def test_install_links_command_service_and_widget(env):
    e, home, venv, calls, _ = env
    run(e)
    bin_link = home / ".local" / "bin" / "usbinspect"
    plugin = home / ".config" / "omarchy" / "plugins" / "usb-superposition"
    assert bin_link.is_symlink() and bin_link.resolve() == (venv / "bin" / "usbinspect").resolve()
    assert plugin.is_symlink() and plugin.resolve() == (ROOT / "omarchy").resolve()
    log = lines(calls)
    assert f"python -m pip install --quiet --disable-pip-version-check -e {ROOT}[tui,daemon]" in log
    assert "usbinspect service install" in log
    assert "omarchy bar put usb.superposition --before omarchy.bluetooth" in log
    assert "omarchy plugin enable usb.superposition" in log
    # the widget is placed before it's enabled, so an existing spot on the bar is kept
    assert log.index("omarchy bar put usb.superposition --before omarchy.bluetooth") < log.index(
        "omarchy plugin enable usb.superposition"
    )


def test_unresponsive_shell_warns_instead_of_failing(env):
    e, home, venv, calls, stubs = env
    _stub(stubs / "omarchy", extra='[ "$1" = bar ] && exit 1')
    r = run(e)
    assert "plugin enable usb.superposition" in r.stderr
    assert sum(line.startswith("omarchy bar put") for line in lines(calls)) == 5


def test_install_is_idempotent(env):
    e, home, venv, calls, _ = env
    run(e)
    r = run(e)
    assert "linked" not in r.stdout  # links already right, nothing re-created


def test_skip_flags_and_dev_extras(env):
    e, home, venv, calls, _ = env
    run(e, "--no-service", "--no-widget", "--dev")
    log = lines(calls)
    assert any(line.endswith("[tui,daemon,dev]") for line in log)
    assert not any("service" in line or line.startswith("omarchy") for line in log)
    assert not (home / ".config" / "omarchy" / "plugins" / "usb-superposition").exists()


def test_no_omarchy_skips_widget(env):
    e, home, venv, calls, stubs = env
    (stubs / "omarchy").unlink()
    r = run(e)
    assert "skipping the bar widget" in r.stdout
    assert not (home / ".config" / "omarchy" / "plugins" / "usb-superposition").exists()


def test_refuses_to_replace_a_real_file(env):
    e, home, venv, calls, _ = env
    bin_link = home / ".local" / "bin" / "usbinspect"
    bin_link.parent.mkdir(parents=True)
    bin_link.write_text("mine")
    r = run(e, check=False)
    assert r.returncode != 0
    assert "not a symlink" in r.stderr
    assert bin_link.read_text() == "mine"


def test_uninstall_removes_only_our_links(env):
    e, home, venv, calls, _ = env
    run(e)
    calls.write_text("")
    run(e, "--uninstall")
    assert not (home / ".local" / "bin" / "usbinspect").exists()
    assert not (home / ".config" / "omarchy" / "plugins" / "usb-superposition").exists()
    assert venv.exists()
    log = lines(calls)
    assert "omarchy plugin disable usb.superposition" in log
    assert "usbinspect service uninstall" in log


def test_uninstall_leaves_foreign_symlink(env):
    e, home, venv, calls, _ = env
    bin_link = home / ".local" / "bin" / "usbinspect"
    bin_link.parent.mkdir(parents=True)
    os.symlink("/usr/bin/true", bin_link)
    run(e, "--uninstall")
    assert bin_link.is_symlink()


def test_unknown_option(env):
    e, *_ = env
    r = run(e, "--bogus", check=False)
    assert r.returncode == 2
    assert "unknown option" in r.stderr

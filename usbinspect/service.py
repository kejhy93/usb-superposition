"""Install / remove the systemd user service for the daemon."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

UNIT_NAME = "usbinspect.service"

UNIT_TEMPLATE = """\
[Unit]
Description=usb-superposition: USB / USB-C monitor (notifications, history)
Documentation=https://github.com/kejhy93/usb-superposition
After=graphical-session.target
PartOf=graphical-session.target

[Service]
ExecStart={exec_start} daemon
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
"""


def unit_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "systemd" / "user" / UNIT_NAME


def executable() -> str:
    """Absolute path of the `usbinspect` command to put into ExecStart."""
    found = shutil.which("usbinspect")
    if found:
        return found
    return f"{sys.executable} -m usbinspect"


def render_unit(exec_start: str | None = None) -> str:
    return UNIT_TEMPLATE.format(exec_start=exec_start or executable())


def _systemctl(*args: str) -> int:
    return subprocess.run(["systemctl", "--user", *args], check=False).returncode


def install(enable: bool = True) -> int:
    p = unit_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_unit())
    print(f"wrote {p}")
    rc = _systemctl("daemon-reload")
    if enable and rc == 0:
        rc = _systemctl("enable", "--now", UNIT_NAME)
        if rc == 0:
            print("enabled and started (it starts with your graphical session)")
    return rc


def uninstall() -> int:
    p = unit_path()
    _systemctl("disable", "--now", UNIT_NAME)
    if p.exists():
        p.unlink()
        print(f"removed {p}")
    return _systemctl("daemon-reload")


def status() -> int:
    return _systemctl("status", "--no-pager", UNIT_NAME)

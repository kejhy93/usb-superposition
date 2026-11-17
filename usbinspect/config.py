"""User configuration: ~/.config/usbinspect/config.toml (every key optional)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# event kind -> notify by default?
DEFAULT_NOTIFY: dict[str, bool] = {
    "device_added": False,  # noisy; the tray/TUI shows it anyway
    "device_removed": False,
    "pd_contract": True,  # "Charging 65 W via Left rear"
    "pd_lost": False,
    "usb_fallback": True,  # SuperSpeed device stuck at USB 2
    "usb_slow_link": True,
    "usb_upstream_limited": False,
    "altmode_failed": True,
    "charger_not_pd": True,
    "charger_low": True,
}

EXAMPLE = """\
# usb-superposition configuration. Every key is optional.

[notify]
# which events pop up a desktop notification
device_added = false
device_removed = false
pd_contract = true          # charger connected / contract changed
pd_lost = false
usb_fallback = true         # SuperSpeed device stuck at USB 2 speed
usb_slow_link = true        # link slower than device and upstream allow (cable?)
usb_upstream_limited = false
altmode_failed = true       # e.g. DisplayPort over USB-C failed
charger_not_pd = true
charger_low = true
internal_devices = false    # also notify about keyboard, fingerprint reader, ...

[charger]
low_watts = 60              # warn when a PD charger negotiates less than this

[history]
max_bytes = 5000000         # rotate events.jsonl beyond this size
keep = 3                    # rotated files to keep
"""


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "usbinspect"


def config_path() -> Path:
    return config_dir() / "config.toml"


@dataclass
class Config:
    notify: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_NOTIFY))
    notify_internal: bool = False
    low_charger_w: float = 60
    history_max_bytes: int = 5_000_000
    history_keep: int = 3

    def wants(self, kind: str, internal: bool = False) -> bool:
        if internal and not self.notify_internal:
            return False
        return self.notify.get(kind, False)


def parse(text: str) -> Config:
    data = tomllib.loads(text)
    cfg = Config()
    n = data.get("notify", {})
    for k, v in n.items():
        if k == "internal_devices":
            cfg.notify_internal = bool(v)
        else:
            cfg.notify[k] = bool(v)
    cfg.low_charger_w = float(data.get("charger", {}).get("low_watts", cfg.low_charger_w))
    h = data.get("history", {})
    cfg.history_max_bytes = int(h.get("max_bytes", cfg.history_max_bytes))
    cfg.history_keep = int(h.get("keep", cfg.history_keep))
    return cfg


def load(path: str | Path | None = None) -> Config:
    p = Path(path) if path else config_path()
    try:
        return parse(p.read_text())
    except FileNotFoundError:
        return Config()

"""Desktop notifications via org.freedesktop.Notifications, using `gdbus` (ships with GLib)."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections.abc import Callable

from .events import Event

APP_NAME = "usb-superposition"

ICONS = {
    "device_added": "drive-removable-media-usb",
    "device_removed": "drive-removable-media-usb",
    "pd_contract": "battery-full-charging",
    "pd_lost": "battery-discharging",
    "altmode_failed": "video-display",
}
WARNING_ICON = "dialog-warning"

TITLES = {
    "device_added": "USB device connected",
    "device_removed": "USB device disconnected",
    "pd_contract": "USB-C charging",
    "pd_lost": "USB-C charger removed",
    "usb_fallback": "USB device running at USB 2 speed",
    "usb_slow_link": "USB link slower than it could be",
    "usb_upstream_limited": "USB device limited by upstream port",
    "altmode_failed": "USB-C alternate mode failed",
    "charger_not_pd": "Charger without USB Power Delivery",
    "charger_low": "Low-power charger",
}

# events of these kinds replace each other's popup instead of stacking
REPLACE_GROUPS = {"pd_contract": "power", "pd_lost": "power"}


def _gvariant_str(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


class Notifier:
    def __init__(
        self,
        run: Callable[[list[str]], str | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        dedupe_s: float = 30.0,
    ) -> None:
        self._run = run or _run_gdbus
        self._clock = clock
        self._dedupe_s = dedupe_s
        self._recent: dict[str, float] = {}
        self._replace_ids: dict[str, int] = {}

    def command(self, summary: str, body: str, icon: str, urgency: int, replaces: int) -> list[str]:
        hints = f"{{'urgency': <byte {urgency}>, 'desktop-entry': <'usb-superposition'>}}"
        return [
            "gdbus",
            "call",
            "--session",
            "--dest=org.freedesktop.Notifications",
            "--object-path=/org/freedesktop/Notifications",
            "--method=org.freedesktop.Notifications.Notify",
            # gdbus parses every argument as GVariant text, so strings must be quoted
            _gvariant_str(APP_NAME),
            str(replaces),
            _gvariant_str(icon),
            _gvariant_str(summary),
            _gvariant_str(body),
            "@as []",
            hints,
            "int32 -1",  # bare "-1" would be parsed as a command-line option
        ]

    def send(self, ev: Event) -> bool:
        """Show a notification for `ev`. False if suppressed as a duplicate or sending failed."""
        now = self._clock()
        last = self._recent.get(ev.message)
        if last is not None and now - last < self._dedupe_s:
            return False
        self._recent[ev.message] = now
        warning = ev.severity == "warning"
        icon = WARNING_ICON if warning else ICONS.get(ev.kind, "drive-removable-media-usb")
        title = TITLES.get(ev.kind, "USB")
        group = REPLACE_GROUPS.get(ev.kind)
        replaces = self._replace_ids.get(group, 0) if group else 0
        out = self._run(self.command(title, ev.message, icon, 2 if warning else 1, replaces))
        if out is None:
            return False
        m = re.search(r"\(uint32 (\d+),\)", out)
        if group and m:
            self._replace_ids[group] = int(m.group(1))
        return True


def _run_gdbus(cmd: list[str]) -> str | None:
    if not shutil.which(cmd[0]):
        return None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None

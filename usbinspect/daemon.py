"""`usbinspect daemon`: watch for changes, notify, keep history, publish state.json."""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__, portmap
from .config import Config
from .config import load as load_config
from .events import Event, diff
from .history import History, Record, snapshot_details
from .model import State
from .notify import Notifier

POLL_S = 3.0  # cheap check of USB-C power supplies (PD changes emit no udev event)
FULL_S = 60.0  # full re-read as a safety net
DEBOUNCE_S = 0.5
RECENT_EVENTS = 10
UDEV_SUBSYSTEMS = ("usb", "typec", "power_supply", "thunderbolt", "usb_power_delivery")


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/usbinspect-{os.getuid()}"
    return Path(base) / "usbinspect"


def state_path() -> Path:
    return runtime_dir() / "state.json"


def power_fingerprint(root: str = "/") -> str:
    """A few sysfs reads that change when a PD contract changes."""
    base = Path(root) / "sys/class/power_supply"
    parts = []
    try:
        names = sorted(n for n in os.listdir(base) if n.startswith("ucsi-"))
    except OSError:
        return ""
    for n in names:
        for attr in ("online", "voltage_now", "current_max", "usb_type"):
            try:
                parts.append((base / n / attr).read_text().strip())
            except OSError:
                parts.append("")
    return "|".join(parts)


def _fingerprint(state: State) -> str:
    d = state.to_dict()
    d.pop("timestamp", None)
    return json.dumps(d, sort_keys=True, default=str)


class Daemon:
    """The logic, free of I/O loops so it can be tested by calling `tick()`."""

    def __init__(
        self,
        collect_state: Callable[[Config], State],
        load_map: Callable[[State], portmap.PortMap | None],
        config: Config,
        history: History,
        notifier: Notifier | None,
        state_file: Path,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        log: Callable[[str], None] = lambda s: print(s, file=sys.stderr, flush=True),
    ) -> None:
        self.collect_state = collect_state
        self.load_map = load_map
        self.config = config
        self.history = history
        self.notifier = notifier
        self.state_file = state_file
        self.now = now
        self.log = log
        self.state: State | None = None
        self._fp: str | None = None
        self.recent: list[dict[str, Any]] = []

    def _load(self) -> State:
        state = self.collect_state(self.config)
        try:
            pm = self.load_map(state)
        except (OSError, ValueError) as e:
            self.log(f"ignoring bay map: {e}")
            pm = None
        return portmap.apply(state, pm)

    def start(self) -> None:
        self.state = self._load()
        self._fp = _fingerprint(self.state)
        self.recent = [_recent_dict(r) for r in self.history.tail(RECENT_EVENTS, include_internal=False)]
        rec = Record(
            self.now(),
            "daemon_start",
            "info",
            f"usbinspect {__version__} daemon started",
            details=snapshot_details(self.state),
        )
        self.history.append(rec)
        self._publish()
        self.log(f"started; {len(self.state.issues)} issue(s); state at {self.state_file}")

    def tick(self, force: bool = False) -> list[Event]:
        """Re-read the system; handle and return what changed.

        `force` (a manual refresh) republishes state.json even when nothing changed,
        so the widgets see a fresh timestamp.
        """
        new = self._load()
        fp = _fingerprint(new)
        if fp == self._fp:
            if force:
                self.state = new
                self._publish()
            return []
        events = diff(self.state, new)
        self.state, self._fp = new, fp
        ts = self.now()
        for ev in events:
            rec = Record.from_event(ev, ts)
            self.history.append(rec)
            if not ev.internal:
                self.recent = [*self.recent, _recent_dict(rec)][-RECENT_EVENTS:]
            if self.notifier and self.config.wants(ev.kind, ev.internal):
                sent = self.notifier.send(ev)
                self.log(f"{ev.kind}: {ev.message}{'' if sent else ' (not notified)'}")
            else:
                self.log(f"{ev.kind}: {ev.message}")
        self._publish()
        return events

    def stop(self) -> None:
        self.history.append(Record(self.now(), "daemon_stop", "info", "usbinspect daemon stopped"))
        # A stale state.json would make the widgets show old data as if it were live.
        # Only remove our own: another daemon instance may have taken it over.
        try:
            owner = json.loads(self.state_file.read_text()).get("daemon", {}).get("pid")
        except (OSError, ValueError, AttributeError):
            return
        if owner == os.getpid():
            self.state_file.unlink(missing_ok=True)

    def _publish(self) -> None:
        doc = self.state.to_dict()
        doc["recent_events"] = self.recent
        doc["daemon"] = {"pid": os.getpid(), "version": __version__, "history": str(self.history.path)}
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, separators=(",", ":"), default=str))
        tmp.replace(self.state_file)  # atomic for readers like the plasmoid


def _recent_dict(r: Record) -> dict[str, Any]:
    return {
        "ts": r.ts.isoformat(timespec="seconds"),
        "kind": r.kind,
        "severity": r.severity,
        "message": r.message,
    }


def run(collect_fn: Callable[[float], State], map_arg: str | None, no_map: bool, notify: bool = True) -> int:
    """The real loop: udev (if pyudev is available) + periodic polling."""

    def collect_state(cfg: Config) -> State:
        return collect_fn(cfg.low_charger_w)

    def load_map(state: State):
        return None if no_map else portmap.resolve(state.machine, map_arg)

    cfg = load_config()
    d = Daemon(
        collect_state,
        load_map,
        cfg,
        History(max_bytes=cfg.history_max_bytes, keep=cfg.history_keep),
        Notifier() if notify else None,
        state_path(),
    )

    stopping = False
    refresh_requested = False

    def on_term(signum, frame):
        nonlocal stopping
        stopping = True

    def on_hup(signum, frame):
        # `systemctl --user reload usbinspect` (the widgets' refresh button): re-read the
        # config and the bay map, re-scan now and republish.
        nonlocal refresh_requested
        d.config = load_config()
        refresh_requested = True
        d.log("configuration reloaded, refreshing")

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    signal.signal(signal.SIGHUP, on_hup)

    d.start()
    monitor = _udev_monitor()
    if monitor is None:
        d.log("pyudev not available: polling only")
    next_poll = time.monotonic() + POLL_S
    next_full = time.monotonic() + FULL_S
    power_fp = power_fingerprint()
    pending: float | None = None
    try:
        while not stopping:
            now = time.monotonic()
            wake = min(next_poll, pending or next_poll)
            timeout = max(0.05, min(0.5, wake - now))
            if monitor is not None:
                if monitor.poll(timeout=timeout) is not None:
                    pending = time.monotonic() + DEBOUNCE_S
            else:
                time.sleep(timeout)
            now = time.monotonic()
            due = (pending is not None and now >= pending) or now >= next_full
            if now >= next_poll:
                next_poll = now + POLL_S
                fp = power_fingerprint()
                if fp != power_fp:
                    power_fp, due = fp, True
            force, refresh_requested = refresh_requested, False
            if due or force:
                pending = None
                next_full = now + FULL_S
                try:
                    d.tick(force=force)
                except Exception as e:  # never die on one bad read
                    d.log(f"error during refresh: {e!r}")
    finally:
        d.stop()
        d.log("stopped")
    return 0


def _udev_monitor():
    try:
        import pyudev
    except ImportError:
        return None
    mon = pyudev.Monitor.from_netlink(pyudev.Context())
    for s in UDEV_SUBSYSTEMS:
        mon.filter_by(s)
    mon.start()
    return mon

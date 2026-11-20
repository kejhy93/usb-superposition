"""Persistent event log: one JSON object per line in $XDG_STATE_HOME/usbinspect/events.jsonl."""

from __future__ import annotations

import fcntl
import json
import os
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .events import Event
from .model import State


def history_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "usbinspect" / "events.jsonl"


@dataclass
class Record:
    ts: datetime
    kind: str
    severity: str
    message: str
    ref: str | None = None
    internal: bool = False
    details: dict[str, Any] | None = None

    def to_json(self) -> str:
        d = {
            "ts": self.ts.isoformat(timespec="seconds"),
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
        }
        if self.ref:
            d["ref"] = self.ref
        if self.internal:
            d["internal"] = True
        if self.details:
            d["details"] = self.details
        return json.dumps(d, separators=(",", ":"), default=str)

    @classmethod
    def from_json(cls, line: str) -> Record:
        d = json.loads(line)
        return cls(
            ts=datetime.fromisoformat(d["ts"]),
            kind=d["kind"],
            severity=d.get("severity", "info"),
            message=d.get("message", ""),
            ref=d.get("ref"),
            internal=d.get("internal", False),
            details=d.get("details"),
        )

    @classmethod
    def from_event(cls, ev: Event, ts: datetime) -> Record:
        return cls(ts, ev.kind, ev.severity, ev.message, ev.ref, ev.internal, ev.details or None)


def snapshot_details(state: State) -> dict[str, Any]:
    """Compact picture of what's connected, so the log makes sense without the daemon."""
    from .events import _walk  # local: avoid widening the public API

    devices = [
        {
            "name": d.name,
            "id": f"{d.vid}:{d.pid}",
            "product": d.product,
            "speed_mbps": d.speed_mbps,
            "max_mbps": d.max_mbps,
            "bay": d.bay,
        }
        for rp in state.root_ports
        for d in _walk(rp.devices)
        if not d.internal and d.kind != "hub"
    ]
    power = [
        {"port": p.id, "bay": p.bay, "watts": p.power.watts, "type": p.power.usb_type}
        for p in state.typec_ports
        if p.power and p.power.online
    ]
    return {"machine": state.machine, "devices": devices, "power": power, "issues": len(state.issues)}


class History:
    def __init__(self, path: str | Path | None = None, max_bytes: int = 5_000_000, keep: int = 3) -> None:
        self.path = Path(path) if path else history_path()
        self.max_bytes = max_bytes
        self.keep = keep

    @contextmanager
    def _locked(self):
        """Serialise the daemon's appends with `clear()` from the CLI."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_name(self.path.name + ".lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def append(self, rec: Record) -> None:
        with self._locked():
            self._rotate_if_needed()
            with self.path.open("a", encoding="utf-8") as f:
                f.write(rec.to_json() + "\n")

    def clear(self, drop: Callable[[Record], bool]) -> int:
        """Remove the records `drop` selects from every file (rotated ones too).

        Each file is rewritten and atomically replaced; lines that don't parse are kept.
        Returns how many records were removed.
        """
        removed = 0
        with self._locked():
            for p in self.files():
                kept: list[str] = []
                with p.open(encoding="utf-8", errors="replace") as f:
                    for line in f:
                        try:
                            if line.strip() and drop(Record.from_json(line)):
                                removed += 1
                                continue
                        except (ValueError, KeyError):
                            pass
                        kept.append(line)
                tmp = p.with_name(p.name + ".tmp")
                tmp.write_text("".join(kept), encoding="utf-8")
                tmp.replace(p)
        return removed

    def _rotate_if_needed(self) -> None:
        try:
            if self.path.stat().st_size < self.max_bytes:
                return
        except FileNotFoundError:
            return
        for i in range(self.keep, 0, -1):
            src = self._rotated(i - 1) if i > 1 else self.path
            if src.exists():
                if i == self.keep:
                    self._rotated(i).unlink(missing_ok=True)
                src.replace(self._rotated(i))

    def _rotated(self, i: int) -> Path:
        return self.path.with_name(f"{self.path.name}.{i}")

    def files(self) -> list[Path]:
        """Oldest first."""
        rotated = [self._rotated(i) for i in range(self.keep, 0, -1)]
        return [p for p in [*rotated, self.path] if p.exists()]

    def read(
        self,
        since: datetime | None = None,
        kinds: set[str] | None = None,
        match: str | None = None,
        include_internal: bool = True,
    ) -> Iterator[Record]:
        needle = match.lower() if match else None
        for p in self.files():
            with p.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = Record.from_json(line)
                    except (ValueError, KeyError):
                        continue  # tolerate a torn last line
                    if since and r.ts < since:
                        continue
                    if kinds and r.kind not in kinds:
                        continue
                    if not include_internal and r.internal:
                        continue
                    if needle and not _matches(r, needle):
                        continue
                    yield r

    def tail(self, n: int, **filters) -> list[Record]:
        out: list[Record] = []
        for r in self.read(**filters):
            out.append(r)
            if len(out) > n:
                out.pop(0)
        return out


def _matches(r: Record, needle: str) -> bool:
    if needle in r.message.lower() or needle == (r.ref or "").lower():
        return True
    d = r.details or {}
    ident = f"{d.get('vid')}:{d.get('pid')}".lower()
    return needle == ident or needle in str(d.get("product") or "").lower()


_REL = re.compile(r"^(\d+)\s*([smhdw])$")


def parse_since(s: str, now: datetime | None = None) -> datetime:
    """'90m', '2h', '1d', '1w' or an ISO date/time."""
    now = now or datetime.now(UTC)
    m = _REL.match(s.strip().lower())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        secs = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
        return now - timedelta(seconds=n * secs)
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.astimezone()


@dataclass
class DeviceStats:
    label: str
    connects: int = 0
    disconnects: int = 0
    warnings: Counter | None = None
    speeds: Counter | None = None


def stats(records: list[Record]) -> dict[str, Any]:
    devices: dict[str, DeviceStats] = {}
    by_ref: dict[str, str] = {}  # last device id seen at a sysfs name, to attribute warnings
    chargers: Counter = Counter()
    kinds: Counter = Counter()
    for r in records:
        kinds[r.kind] += 1
        d = r.details or {}
        if r.kind in ("device_added", "device_removed"):
            ident = f"{d.get('vid')}:{d.get('pid')}"
            s = devices.setdefault(
                ident, DeviceStats(d.get("product") or ident, warnings=Counter(), speeds=Counter())
            )
            if r.ref:
                by_ref[r.ref] = ident
            if r.kind == "device_added":
                s.connects += 1
                if d.get("speed_mbps"):
                    s.speeds[d["speed_mbps"]] += 1
            else:
                s.disconnects += 1
        elif r.kind == "pd_contract" and d.get("watts"):
            chargers[d["watts"]] += 1
        elif r.severity == "warning" and r.ref in by_ref:
            devices[by_ref[r.ref]].warnings[r.kind] += 1
    per_device = defaultdict(dict)
    for ident, s in devices.items():
        per_device[ident] = {
            "label": s.label,
            "connects": s.connects,
            "disconnects": s.disconnects,
            "speeds_mbps": dict(s.speeds),
            "warnings": dict(s.warnings),
        }
    return {"events": dict(kinds), "devices": dict(per_device), "charger_watts": dict(chargers)}

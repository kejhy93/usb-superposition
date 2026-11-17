"""Thin sysfs access layer.

Everything the collector reads goes through a `Sysfs` object so we can
record a live system into a JSON snapshot and replay it in tests.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path


class Sysfs:
    """Reads from the live filesystem (optionally under an alternate root)."""

    def __init__(self, root: str = "/") -> None:
        self.root = Path(root)

    def _p(self, path: str) -> Path:
        return self.root / path.lstrip("/")

    def read(self, path: str) -> str | None:
        """Return stripped text content, or None if missing/unreadable."""
        try:
            return self._p(path).read_text(errors="replace").strip()
        except OSError:
            return None

    def read_bytes(self, path: str) -> bytes | None:
        try:
            return self._p(path).read_bytes()
        except OSError:
            return None

    def listdir(self, path: str) -> list[str]:
        try:
            return sorted(os.listdir(self._p(path)))
        except OSError:
            return []

    def realpath(self, path: str) -> str | None:
        """Resolve symlinks; returns an absolute path in sysfs terms."""
        p = self._p(path)
        if not p.exists():
            return None
        resolved = Path(os.path.realpath(p))
        try:
            return "/" + str(resolved.relative_to(self.root.resolve()))
        except ValueError:
            return str(resolved)

    def exists(self, path: str) -> bool:
        return self._p(path).exists()


class RecordingSysfs(Sysfs):
    """Live Sysfs that remembers every answer, for creating test fixtures."""

    def __init__(self, root: str = "/") -> None:
        super().__init__(root)
        self.log: dict[str, object] = {}

    def read(self, path):
        v = super().read(path)
        self.log[f"read:{path}"] = v
        return v

    def read_bytes(self, path):
        v = super().read_bytes(path)
        self.log[f"read_bytes:{path}"] = None if v is None else base64.b64encode(v).decode()
        return v

    def listdir(self, path):
        v = super().listdir(path)
        self.log[f"listdir:{path}"] = v
        return v

    def realpath(self, path):
        v = super().realpath(path)
        self.log[f"realpath:{path}"] = v
        return v

    def exists(self, path):
        v = super().exists(path)
        self.log[f"exists:{path}"] = v
        return v

    REDACT_SUFFIXES = ("/serial", "/unique_id")

    def dump(self, out: str | Path, redact: bool = True) -> None:
        """Write the log. By default device serials and USB4 unique ids are masked
        so fixtures can be committed to a public repo."""
        log = dict(self.log)
        if redact:
            for k, v in log.items():
                if v and k.startswith("read:") and k.endswith(self.REDACT_SUFFIXES):
                    log[k] = "REDACTED"
        Path(out).write_text(json.dumps(log, indent=1, sort_keys=True) + "\n")


class FixtureSysfs(Sysfs):
    """Replays a snapshot written by RecordingSysfs. Unknown paths look absent."""

    def __init__(self, data: dict[str, object]) -> None:
        self.data = data

    @classmethod
    def load(cls, path: str | Path) -> FixtureSysfs:
        return cls(json.loads(Path(path).read_text()))

    def read(self, path):
        return self.data.get(f"read:{path}")

    def read_bytes(self, path):
        v = self.data.get(f"read_bytes:{path}")
        return None if v is None else base64.b64decode(v)

    def listdir(self, path):
        return list(self.data.get(f"listdir:{path}") or [])

    def realpath(self, path):
        return self.data.get(f"realpath:{path}")

    def exists(self, path):
        return bool(self.data.get(f"exists:{path}", False))

"""Map stable kernel port keys to physical bays ("Left rear", ...).

Lookup order: --map FILE, then ~/.config/usbinspect/ports.toml, then a built-in
profile shipped in usbinspect/profiles/ whose `match` string is contained in the
machine's DMI vendor + product name.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from .model import Bay, State, UsbDevice


@dataclass
class BayDef:
    id: str
    name: str
    usb: list[str] = field(default_factory=list)
    typec: list[str] = field(default_factory=list)


@dataclass
class PortMap:
    bays: list[BayDef]
    machine: str | None = None
    match: str | None = None  # for built-in profiles
    source: str | None = None  # where it was loaded from

    def bay_for_key(self, key: str | None) -> BayDef | None:
        """Bay whose USB port is `key` or an ancestor of it (devices behind hubs)."""
        if not key:
            return None
        best, best_len = None, -1
        for b in self.bays:
            for k in b.usb:
                if (key == k or key.startswith(k + "/")) and len(k) > best_len:
                    best, best_len = b, len(k)
        return best

    def bay_for_typec(self, port: str) -> BayDef | None:
        return next((b for b in self.bays if port in b.typec), None)


# ------------------------------------------------------------------ defaults

FRAMEWORK_16_BAYS = [
    ("L1", "Left rear"),
    ("L2", "Left middle"),
    ("L3", "Left front"),
    ("R1", "Right rear"),
    ("R2", "Right middle"),
    ("R3", "Right front"),
]
FRAMEWORK_13_BAYS = [
    ("L1", "Left rear"),
    ("L2", "Left front"),
    ("R1", "Right rear"),
    ("R2", "Right front"),
]


def default_bays(machine: str | None) -> list[tuple[str, str]] | None:
    m = (machine or "").lower()
    if "framework" in m and "laptop 16" in m:
        return FRAMEWORK_16_BAYS
    if "framework" in m and "laptop" in m:
        return FRAMEWORK_13_BAYS
    return None


# ------------------------------------------------------------------ files


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "usbinspect" / "ports.toml"


def parse(text: str, source: str | None = None) -> PortMap:
    data = tomllib.loads(text)
    bays = []
    for i, b in enumerate(data.get("bay", [])):
        if not isinstance(b, dict) or "name" not in b:
            raise ValueError(f"{source or 'port map'}: bay #{i + 1} needs a name")
        bays.append(
            BayDef(
                id=str(b.get("id") or f"B{i + 1}"),
                name=str(b["name"]),
                usb=[str(x) for x in b.get("usb", [])],
                typec=[str(x) for x in b.get("typec", [])],
            )
        )
    return PortMap(bays=bays, machine=data.get("machine"), match=data.get("match"), source=source)


def load_file(path: str | Path) -> PortMap:
    p = Path(path)
    return parse(p.read_text(), source=str(p))


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _qlist(xs: list[str]) -> str:
    return "[" + ", ".join(_q(x) for x in xs) + "]"


def to_toml(pm: PortMap) -> str:
    lines = ["# usb-superposition bay map (written by `usbinspect calibrate`)"]
    if pm.machine:
        lines.append(f"machine = {_q(pm.machine)}")
    if pm.match:
        lines.append(f"match = {_q(pm.match)}")
    for b in pm.bays:
        lines += [
            "",
            "[[bay]]",
            f"id = {_q(b.id)}",
            f"name = {_q(b.name)}",
            f"usb = {_qlist(b.usb)}",
            f"typec = {_qlist(b.typec)}",
        ]
    return "\n".join(lines) + "\n"


def save(pm: PortMap, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(to_toml(pm))
    tmp.replace(p)


def builtin_profiles() -> list[PortMap]:
    out = []
    pkg = resources.files("usbinspect") / "profiles"
    if not pkg.is_dir():
        return out
    for f in sorted(pkg.iterdir(), key=lambda x: x.name):
        if f.name.endswith(".toml"):
            out.append(parse(f.read_text(), source=f"builtin:{f.name.removesuffix('.toml')}"))
    return out


def builtin_for(machine: str | None) -> PortMap | None:
    if not machine:
        return None
    for pm in builtin_profiles():
        if pm.match and pm.match.lower() in machine.lower():
            return pm
    return None


def resolve(machine: str | None, explicit: str | Path | None = None) -> PortMap | None:
    if explicit:
        return load_file(explicit)
    user = config_path()
    if user.is_file():
        return load_file(user)
    return builtin_for(machine)


# ------------------------------------------------------------------ apply


def _walk(devs: list[UsbDevice]):
    for d in devs:
        yield d
        yield from _walk(d.children)


def apply(state: State, pm: PortMap | None) -> State:
    """Annotate devices and Type-C ports with their bay and fill `state.bays`."""
    if pm is None:
        return state
    bays = {b.id: Bay(id=b.id, name=b.name, usb_ports=list(b.usb), typec=list(b.typec)) for b in pm.bays}
    for rp in state.root_ports:
        for top in rp.devices:
            _assign(top, pm, bays, parent_bay=None)
    for p in state.typec_ports:
        b = pm.bay_for_typec(p.id)
        p.bay = b.id if b else None
    state.bays = list(bays.values())
    state.portmap_source = pm.source
    return state


def _assign(d: UsbDevice, pm: PortMap, bays: dict[str, Bay], parent_bay: str | None) -> None:
    b = pm.bay_for_key(d.port_key)
    d.bay = b.id if b else None
    if d.bay and d.bay != parent_bay:
        bays[d.bay].devices.append(d.name)
    for c in d.children:
        _assign(c, pm, bays, d.bay)

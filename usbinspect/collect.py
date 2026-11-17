"""Read sysfs and build a `State`. No side effects, no root required."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from . import names
from .bos import parse_bos
from .model import (
    SCHEMA_VERSION,
    AltMode,
    BillboardMode,
    Cable,
    Identity,
    Interface,
    Issue,
    Partner,
    PdCaps,
    Pdo,
    PowerContract,
    RootPort,
    State,
    Summary,
    TbDomain,
    TbRouter,
    TypecPort,
    UsbDevice,
)
from .sysfs import Sysfs

USB = "/sys/bus/usb/devices"
TYPEC = "/sys/class/typec"
PSY = "/sys/class/power_supply"
TB = "/sys/bus/thunderbolt/devices"
DMI = "/sys/class/dmi/id"

LOW_CHARGER_W = 60  # below this a laptop charger is worth mentioning

FRAMEWORK_VID = "32ac"
_DEVICE_RE = re.compile(r"^\d+-\d+(\.\d+)*$")
_ROOT_HUB_RE = re.compile(r"^usb(\d+)$")


# ---------------------------------------------------------------- helpers


def _int(s: str | None, base: int = 10) -> int | None:
    if s is None:
        return None
    try:
        return int(s, base)
    except ValueError:
        return None


def _float(s: str | None) -> float | None:
    try:
        return float(s) if s is not None else None
    except ValueError:
        return None


def _bool(s: str | None) -> bool | None:
    if s is None:
        return None
    return s.strip().lower() in ("1", "yes", "y", "true")


def _bracketed(s: str | None) -> tuple[str | None, list[str]]:
    """'[host] device' -> ('host', ['host', 'device'])"""
    if not s:
        return None, []
    selected, options = None, []
    for tok in s.split():
        if tok.startswith("[") and tok.endswith("]"):
            tok = tok[1:-1]
            selected = tok
        options.append(tok)
    if selected is None and len(options) == 1:
        selected = options[0]
    return selected, options


def _unit(s: str | None) -> int | None:
    """'5000mV' -> 5000. Normalises V/A/W and uV/uA to milli-units."""
    if s is None:
        return None
    m = re.match(r"^\s*(-?\d+)\s*(mV|mA|mW|uV|uA|V|A|W)?\s*$", s)
    if not m:
        return None
    v, u = int(m.group(1)), m.group(2)
    if u in ("V", "A", "W"):
        v *= 1000
    elif u in ("uV", "uA"):
        v //= 1000
    return v


def _base(p: str | None) -> str | None:
    return p.rsplit("/", 1)[-1] if p else None


# ---------------------------------------------------------------- USB


class _UsbCollector:
    def __init__(self, fs: Sysfs) -> None:
        self.fs = fs

    def collect(self) -> list[RootPort]:
        entries = self.fs.listdir(USB)
        root_hubs = {int(m.group(1)): e for e in entries if (m := _ROOT_HUB_RE.match(e))}
        hub_speed = {bus: _float(self.fs.read(f"{USB}/{h}/speed")) for bus, h in root_hubs.items()}

        # Root ports, paired with their USB2/USB3 peer.
        port_info: dict[str, dict] = {}
        for bus, hub in sorted(root_hubs.items()):
            iface = f"{USB}/{hub}/{bus}-0:1.0"
            for p in self.fs.listdir(iface):
                if not p.startswith(f"usb{bus}-port"):
                    continue
                path = f"{iface}/{p}"
                port_info[p] = {
                    "bus": bus,
                    "num": int(p.rsplit("port", 1)[1]),
                    "peer": _base(self.fs.realpath(f"{path}/peer")),
                    "connect_type": self.fs.read(f"{path}/connect_type"),
                    "acpi": self.fs.read(f"{path}/firmware_node/path"),
                    "pci": _base(_parent_pci(self.fs.realpath(f"{USB}/{hub}"))),
                }

        groups: dict[str, RootPort] = {}
        port_to_group: dict[str, RootPort] = {}
        for p, info in sorted(port_info.items(), key=lambda kv: (kv[1]["bus"], kv[1]["num"])):
            if p in port_to_group:
                continue
            members = [p] + ([info["peer"]] if info["peer"] in port_info else [])
            acpi = [port_info[m]["acpi"] for m in members if port_info[m]["acpi"]]
            keys = [_port_key(m, port_info[m]["acpi"]) for m in members]
            rp = RootPort(
                id=_root_port_id(acpi) or "+".join(members),
                ports=members,
                controller=_controller(acpi),
                pci=info["pci"],
                acpi=acpi,
                connect_type=next(
                    (
                        port_info[m]["connect_type"]
                        for m in members
                        if port_info[m]["connect_type"] not in (None, "not used")
                    ),
                    info["connect_type"],
                ),
                max_mbps=max((hub_speed.get(port_info[m]["bus"]) or 0) for m in members) or None,
                keys=keys,
            )
            for m in members:
                port_to_group[m] = rp
            groups[rp.id] = rp

        # Devices, built parent-first so children can attach.
        names_ = sorted((e for e in entries if _DEVICE_RE.match(e)), key=lambda n: (n.count("."), n))
        built: dict[str, UsbDevice] = {}
        for n in names_:
            bus, _, path = n.partition("-")
            if "." in path:
                parent_name = n.rsplit(".", 1)[0]
                parent = built.get(parent_name)
                upstream = (
                    min(filter(None, [parent.upstream_mbps, parent.speed_mbps]), default=None)
                    if parent
                    else None
                )
                dev = self._device(n, upstream)
                if parent:
                    parent.children.append(dev)
                    if parent.port_key:
                        dev.port_key = f"{parent.port_key}/{path.rsplit('.', 1)[1]}"
                built[n] = dev
            else:
                upstream = hub_speed.get(int(bus))
                dev = self._device(n, upstream)
                built[n] = dev
                root_port = f"usb{bus}-port{path}"
                if root_port in port_info:
                    dev.port_key = _port_key(root_port, port_info[root_port]["acpi"])
                rp = port_to_group.get(root_port)
                if rp:
                    rp.devices.append(dev)
        _pair_companions(list(built.values()))
        return list(groups.values())

    def _device(self, n: str, upstream: float | None) -> UsbDevice:
        fs, d = self.fs, f"{USB}/{n}"
        vid, pid = fs.read(f"{d}/idVendor"), fs.read(f"{d}/idProduct")
        product = fs.read(f"{d}/product") or names.lookup_product(vid, pid)
        manufacturer = fs.read(f"{d}/manufacturer") or names.lookup_vendor(vid)
        version = fs.read(f"{d}/version")
        speed = _float(fs.read(f"{d}/speed"))
        rx, tx = _int(fs.read(f"{d}/rx_lanes")), _int(fs.read(f"{d}/tx_lanes"))
        dclass = _int(fs.read(f"{d}/bDeviceClass"), 16)
        removable = fs.read(f"{d}/removable")
        bos = parse_bos(fs.read_bytes(f"{d}/bos_descriptors"))

        interfaces = []
        for e in fs.listdir(d):
            if e.startswith(f"{n}:"):
                interfaces.append(
                    Interface(
                        number=e.split(":", 1)[1],
                        cls=names.class_name(_int(fs.read(f"{d}/{e}/bInterfaceClass"), 16)),
                        driver=_base(fs.realpath(f"{d}/{e}/driver")),
                    )
                )

        # What can it do?
        vnum = _float(version) or 0.0
        if bos and bos.max_mbps:
            max_mbps, src = bos.max_mbps, "bos"
        elif vnum >= 3.0:
            max_mbps, src = 5000, "bcdUSB"
        elif speed is not None and speed >= 480:
            max_mbps, src = 480, "bcdUSB"
        elif vnum and vnum < 2.0:
            max_mbps, src = 12, "bcdUSB"
        else:
            max_mbps, src = None, "unknown"  # USB 2 device at FS: can't tell if it does HS

        downgraded, reason = False, None
        if max_mbps and speed is not None and speed < max_mbps:
            downgraded = True
            if speed <= 480 and max_mbps >= 5000:
                reason = (
                    "SuperSpeed device connected at USB 2 speed: the cable, port or a hub "
                    "in between lacks SuperSpeed lanes"
                )
            elif upstream is not None and speed >= upstream:
                reason = f"limited by upstream link ({names.short_speed(upstream)})"
            else:
                reason = (
                    f"negotiated {names.short_speed(speed)} although device and upstream "
                    f"support {names.short_speed(min(max_mbps, upstream or max_mbps))} "
                    "(cable?)"
                )

        is_billboard = dclass == 0x11 or any(i.cls == "Billboard" for i in interfaces)
        prod_l = (product or "").lower()
        if dclass == 0x09:
            kind = "hub"
        elif vid == FRAMEWORK_VID and "expansion card" in prod_l:
            kind = "expansion_card"
        elif is_billboard:
            kind = "billboard"
        elif vid == FRAMEWORK_VID and "laptop 16" in prod_l:
            kind = "input_module"
        else:
            kind = "device"
        internal = removable == "fixed" or kind == "input_module"

        billboard = None
        if bos and bos.billboard is not None:
            billboard = [
                BillboardMode(m.svid, names.svid_name(m.svid), m.mode, m.state) for m in bos.billboard
            ]

        return UsbDevice(
            name=n,
            vid=vid,
            pid=pid,
            manufacturer=manufacturer,
            product=product,
            serial=fs.read(f"{d}/serial"),
            usb_version=version,
            speed_mbps=speed,
            speed_label=names.speed_label(speed, rx or 1),
            rx_lanes=rx,
            tx_lanes=tx,
            max_mbps=max_mbps,
            max_source=src,
            upstream_mbps=upstream,
            downgraded=downgraded,
            downgrade_reason=reason,
            device_class=names.class_name(dclass),
            interfaces=interfaces,
            max_power_ma=_unit(fs.read(f"{d}/bMaxPower")),
            removable=removable,
            kind=kind,
            internal=internal,
            container_id=bos.container_id if bos else None,
            companion=None,
            billboard=billboard,
        )


def _pair_companions(devs: list[UsbDevice]) -> None:
    """A USB3 hub/device enumerates twice: a SuperSpeed half and a USB 2 half sharing
    a Container ID. The USB 2 half is not a downgrade, it's by design."""
    by_cid: dict[str, list[UsbDevice]] = {}
    for d in devs:
        if d.container_id:
            by_cid.setdefault(d.container_id, []).append(d)
    for group in by_cid.values():
        fast = [d for d in group if (d.speed_mbps or 0) >= 5000]
        slow = [d for d in group if (d.speed_mbps or 0) <= 480]
        if not fast or not slow:
            continue
        for d in slow:
            d.companion = fast[0].name
            d.downgraded, d.downgrade_reason = False, None
        for d in fast:
            d.companion = slow[0].name


def _parent_pci(path: str | None) -> str | None:
    """/sys/devices/pci0000:00/0000:00:08.1/0000:c5:00.3/usb1 -> .../0000:c5:00.3"""
    return path.rsplit("/", 1)[0] if path else None


def _controller(acpi: list[str]) -> str | None:
    for a in acpi:
        parts = a.strip("\\").split(".")
        if "RHUB" in parts:
            return parts[parts.index("RHUB") - 1]
    return None


def _port_key(port: str, acpi: str | None) -> str:
    """Stable name for a root port: 'XHC0.PRT1' from ACPI, else the kernel name.
    Bus numbers (usbN) can change between boots; ACPI names don't."""
    if acpi:
        parts = acpi.strip("\\").split(".")
        if len(parts) >= 3 and parts[-2] == "RHUB":
            return f"{parts[-3]}.{parts[-1]}"
    return port


def _root_port_id(acpi: list[str]) -> str | None:
    if not acpi:
        return None
    ctrl = _controller(acpi)
    ports = [a.rsplit(".", 1)[-1] for a in acpi]
    return f"{ctrl}.{'+'.join(ports)}" if ctrl else "+".join(ports)


# ---------------------------------------------------------------- Type-C / PD

_UFP_TYPES = {1: "hub", 2: "peripheral", 3: "psd", 5: "alt_mode_adapter", 6: "vconn_powered_device"}
_DFP_TYPES = {1: "hub", 2: "host", 3: "power_brick"}
_CABLE_TYPES = {3: "passive_cable", 4: "active_cable"}
_CABLE_SPEED = {
    0: "USB 2.0",
    1: "USB 3.2 Gen1 (5 Gbps)",
    2: "USB 3.2 / USB4 Gen2 (10/20 Gbps)",
    3: "USB4 Gen3 (40 Gbps)",
    4: "USB4 Gen4 (80 Gbps)",
}
_CABLE_CURRENT = {0: 3000, 1: 3000, 2: 5000}
_CABLE_VOLTAGE = {0: 20, 1: 30, 2: 40, 3: 50}


class _TypecCollector:
    def __init__(self, fs: Sysfs) -> None:
        self.fs = fs

    def collect(self) -> list[TypecPort]:
        ports = [e for e in self.fs.listdir(TYPEC) if re.fullmatch(r"port\d+", e)]
        supplies = self._ucsi_supplies()
        out = []
        for p in sorted(ports, key=lambda x: int(x[4:])):
            out.append(self._port(p, supplies))
        return out

    def _port(self, p: str, supplies: dict[tuple[str, int], str]) -> TypecPort:
        fs, d = self.fs, f"{TYPEC}/{p}"
        idx = int(p[4:])
        data_role, data_roles = _bracketed(fs.read(f"{d}/data_role"))
        power_role, power_roles = _bracketed(fs.read(f"{d}/power_role"))
        usb_cap, usb_caps = _bracketed(fs.read(f"{d}/usb_capability"))
        orientation = fs.read(f"{d}/orientation")

        dev = fs.realpath(f"{d}/device")
        supply = supplies.get((dev or "", idx + 1))
        power = self._power(supply) if supply else None

        partner = None
        if fs.exists(f"{TYPEC}/{p}-partner"):
            partner = self._partner(f"{TYPEC}/{p}-partner", f"{p}-partner")

        cable = None
        if fs.exists(f"{TYPEC}/{p}-cable"):
            cable = self._cable(p)

        return TypecPort(
            id=p,
            index=idx,
            data_role=data_role,
            data_roles=data_roles,
            power_role=power_role,
            power_roles=power_roles,
            power_operation_mode=fs.read(f"{d}/power_operation_mode"),
            usb_capability=usb_cap,
            usb_capabilities=usb_caps,
            typec_revision=fs.read(f"{d}/usb_typec_revision"),
            pd_revision=fs.read(f"{d}/usb_power_delivery_revision"),
            orientation=orientation if orientation not in ("", "unknown") else None,
            vconn_source=_bool(fs.read(f"{d}/vconn_source")),
            supported_altmodes=self._altmodes(d, p),
            pd=self._pd(f"{d}/usb_power_delivery"),
            partner=partner,
            cable=cable,
            power=power,
        )

    def _ucsi_supplies(self) -> dict[tuple[str, int], str]:
        """(connector device path, 1-based connector number) -> supply name"""
        out = {}
        for s in self.fs.listdir(PSY):
            m = re.match(r"^ucsi-source-psy-.+:(\d+)$", s)
            if m:
                dev = self.fs.realpath(f"{PSY}/{s}/device") or ""
                out[(dev, int(m.group(1)))] = s
        return out

    def _power(self, supply: str) -> PowerContract:
        fs, d = self.fs, f"{PSY}/{supply}"
        online = bool(_bool(fs.read(f"{d}/online")))
        usb_type, usb_types = _bracketed(fs.read(f"{d}/usb_type"))
        uv, ua = _int(fs.read(f"{d}/voltage_now")), _int(fs.read(f"{d}/current_max"))
        mv = uv // 1000 if uv is not None else None
        ma = ua // 1000 if ua is not None else None
        vmax = _int(fs.read(f"{d}/voltage_max"))
        return PowerContract(
            supply=supply,
            online=online,
            status=fs.read(f"{d}/status"),
            usb_type=usb_type,
            usb_types=usb_types,
            voltage_mv=mv if online else None,
            current_ma=ma if online else None,
            voltage_max_mv=vmax // 1000 if vmax is not None and online else None,
            watts=round(mv * ma / 1e6, 1) if online and mv and ma else None,
        )

    def _pd(self, link: str) -> PdCaps | None:
        real = self.fs.realpath(link)
        if not real:
            return None
        return PdCaps(
            name=_base(real) or "",
            revision=self.fs.read(f"{real}/revision"),
            source=self._pdos(f"{real}/source-capabilities"),
            sink=self._pdos(f"{real}/sink-capabilities"),
        )

    def _pdos(self, d: str) -> list[Pdo]:
        out = []
        for e in self.fs.listdir(d):
            idx, _, typ = e.partition(":")
            if not idx.isdigit():
                continue
            attrs: dict[str, object] = {}
            for a in self.fs.listdir(f"{d}/{e}"):
                if a in ("uevent", "power", "subsystem", "device"):
                    continue
                raw = self.fs.read(f"{d}/{e}/{a}")
                if raw is None:
                    continue
                attrs[a] = _unit(raw) if re.search(r"m[VAW]$", raw) else (_int(raw) if raw.isdigit() else raw)
            pdo = Pdo(index=int(idx), type=typ, attrs=attrs)
            v = attrs.get("voltage")
            vmin, vmax = attrs.get("minimum_voltage"), attrs.get("maximum_voltage")
            cur = attrs.get("maximum_current") or attrs.get("operational_current")
            pwr = attrs.get("maximum_power") or attrs.get("operational_power")
            pdo.voltage_mv = v if isinstance(v, int) else None
            pdo.min_voltage_mv = vmin if isinstance(vmin, int) else None
            pdo.max_voltage_mv = vmax if isinstance(vmax, int) else None
            pdo.current_ma = cur if isinstance(cur, int) else None
            pdo.power_mw = pwr if isinstance(pwr, int) else None
            top_v = pdo.voltage_mv or pdo.max_voltage_mv
            if pdo.power_mw:
                pdo.watts = round(pdo.power_mw / 1000, 1)
            elif top_v and pdo.current_ma:
                pdo.watts = round(top_v * pdo.current_ma / 1e6, 1)
            out.append(pdo)
        return sorted(out, key=lambda x: x.index)

    def _altmodes(self, d: str, prefix: str) -> list[AltMode]:
        out = []
        for e in self.fs.listdir(d):
            if not re.fullmatch(re.escape(prefix) + r"\.\d+", e):
                continue
            a = f"{d}/{e}"
            svid = (self.fs.read(f"{a}/svid") or "").lower()
            vdo = self.fs.read(f"{a}/vdo")
            active = self.fs.read(f"{a}/active")
            details: dict[str, object] = {}
            if svid == "ff01":
                details.update(_decode_dp_vdo(_int(vdo, 16) if vdo else None))
                for f in ("configuration", "pin_assignment", "hpd"):
                    v = self.fs.read(f"{a}/displayport/{f}")
                    if v is not None:
                        details[f] = v
            out.append(
                AltMode(
                    svid=svid,
                    name=names.svid_name(svid),
                    mode=_int(self.fs.read(f"{a}/mode")),
                    vdo=vdo,
                    active=_bool(active) if active is not None else None,
                    description=self.fs.read(f"{a}/description") or None,
                    details=details,
                )
            )
        return out

    def _identity(self, d: str, cable: bool) -> Identity | None:
        idd = f"{d}/identity"
        if not self.fs.exists(idd):
            return None
        hdr = self.fs.read(f"{idd}/id_header")
        vdos = [v for i in (1, 2, 3) if (v := self.fs.read(f"{idd}/product_type_vdo{i}")) is not None]
        ident = Identity(
            id_header=hdr,
            cert_stat=self.fs.read(f"{idd}/cert_stat"),
            product=self.fs.read(f"{idd}/product"),
            product_type_vdos=vdos,
        )
        h = _int(hdr, 16) if hdr else None
        if not h:  # all-zero identity = not discovered (common under UCSI)
            return ident
        ident.vid = f"{h & 0xFFFF:04x}"
        ufp = (h >> 27) & 0x7
        if cable:
            ident.product_type = _CABLE_TYPES.get(ufp)
        else:
            dfp = (h >> 23) & 0x7
            ident.product_type = _UFP_TYPES.get(ufp) or _DFP_TYPES.get(dfp)
            ident.decoded["usb_host_capable"] = bool(h & (1 << 31))
            ident.decoded["usb_device_capable"] = bool(h & (1 << 30))
        ident.decoded["modal_operation"] = bool(h & (1 << 26))
        return ident

    def _partner(self, d: str, name: str) -> Partner:
        fs = self.fs
        return Partner(
            pd_supported=_bool(fs.read(f"{d}/supports_usb_power_delivery")),
            pd_revision=(
                r if (r := fs.read(f"{d}/usb_power_delivery_revision")) not in (None, "0.0") else None
            ),
            accessory_mode=(a if (a := fs.read(f"{d}/accessory_mode")) not in (None, "none") else None),
            altmodes=self._altmodes(d, name),
            identity=self._identity(d, cable=False),
            pd=self._pd(f"{d}/usb_power_delivery"),
        )

    def _cable(self, p: str) -> Cable:
        fs, d = self.fs, f"{TYPEC}/{p}-cable"
        ident = self._identity(d, cable=True)
        speed = cur = volt = None
        if ident and ident.product_type_vdos:
            v = _int(ident.product_type_vdos[0], 16)
            if v:
                speed = _CABLE_SPEED.get(v & 0x7)
                cur = _CABLE_CURRENT.get((v >> 5) & 0x3)
                volt = _CABLE_VOLTAGE.get((v >> 9) & 0x3)
        altmodes: list[AltMode] = []
        for plug in fs.listdir(TYPEC):
            if plug.startswith(f"{p}-plug"):
                altmodes += self._altmodes(f"{TYPEC}/{plug}", plug)
        return Cable(
            type=fs.read(f"{d}/type"),
            plug_type=fs.read(f"{d}/plug_type"),
            identity=ident,
            altmodes=altmodes,
            usb_speed=speed,
            current_ma=cur,
            max_voltage_v=volt,
        )


def _decode_dp_vdo(vdo: int | None) -> dict[str, object]:
    if not vdo:
        return {}

    def pins(b: int) -> list[str]:
        return [chr(ord("A") + i) for i in range(8) if b & (1 << i)]

    caps = {1: "UFP_D", 2: "DFP_D", 3: "UFP_D+DFP_D"}.get(vdo & 0x3)
    sig = []
    if vdo & (1 << 2):
        sig.append("DP 1.3")
    if vdo & (1 << 3):
        sig.append("Gen2")
    return {
        "capability": caps,
        "signaling": sig,
        "receptacle": bool(vdo & (1 << 6)),
        "usb2_not_used": bool(vdo & (1 << 7)),
        "dfp_d_pins": pins((vdo >> 8) & 0xFF),
        "ufp_d_pins": pins((vdo >> 16) & 0xFF),
    }


# ---------------------------------------------------------------- Thunderbolt / USB4


def _collect_thunderbolt(fs: Sysfs) -> list[TbDomain]:
    entries = fs.listdir(TB)
    domains = []
    for e in entries:
        if not re.fullmatch(r"domain\d+", e):
            continue
        num = e[6:]
        d = f"{TB}/{e}"
        routers = []
        for r in entries:
            if not re.fullmatch(rf"{num}-[0-9a-f]+", r):
                continue
            rd = f"{TB}/{r}"
            ports = {}
            for sub in fs.listdir(rd):
                if sub.startswith("usb4_port"):
                    ports[sub] = fs.read(f"{rd}/{sub}/link") or "unknown"
            routers.append(
                TbRouter(
                    id=r,
                    vendor=fs.read(f"{rd}/vendor_name") or None,
                    device=fs.read(f"{rd}/device_name") or None,
                    generation=_int(fs.read(f"{rd}/generation")),
                    rx_speed=fs.read(f"{rd}/rx_speed") or None,
                    rx_lanes=_int(fs.read(f"{rd}/rx_lanes")),
                    tx_speed=fs.read(f"{rd}/tx_speed") or None,
                    tx_lanes=_int(fs.read(f"{rd}/tx_lanes")),
                    authorized=_bool(fs.read(f"{rd}/authorized")),
                    usb4_ports=ports,
                )
            )
        real = fs.realpath(d)
        domains.append(
            TbDomain(
                id=e,
                pci=_base(_parent_pci(real)),
                security=fs.read(f"{d}/security"),
                iommu_dma_protection=_bool(fs.read(f"{d}/iommu_dma_protection")),
                routers=routers,
            )
        )
    return domains


# ---------------------------------------------------------------- issues


def _walk(devs: list[UsbDevice]):
    for d in devs:
        yield d
        yield from _walk(d.children)


def _issues(typec: list[TypecPort], roots: list[RootPort]) -> list[Issue]:
    out: list[Issue] = []
    for d in (d for rp in roots for d in _walk(rp.devices)):
        label = d.product or f"{d.vid}:{d.pid}"
        if d.downgraded and d.downgrade_reason:
            fallback = (d.speed_mbps or 0) <= 480
            upstream_only = d.downgrade_reason.startswith("limited by upstream")
            out.append(
                Issue(
                    severity="info" if upstream_only else "warning",
                    kind="usb_fallback"
                    if fallback
                    else ("usb_upstream_limited" if upstream_only else "usb_slow_link"),
                    message=f"{label}: {names.short_speed(d.speed_mbps)} of {names.short_speed(d.max_mbps)} "
                    f"— {d.downgrade_reason}",
                    ref=d.name,
                )
            )
        for m in d.billboard or []:
            if m.state in ("failed", "error"):
                out.append(
                    Issue("warning", "altmode_failed", f"{label}: {m.name} alternate mode {m.state}", d.name)
                )
    for p in typec:
        pw = p.power
        if pw and pw.online and p.power_role == "sink":
            if pw.usb_type not in ("PD", "PD_PPS"):
                out.append(
                    Issue(
                        "warning",
                        "charger_not_pd",
                        f"{p.id}: charging without USB PD ({pw.usb_type}, {pw.watts} W)",
                        p.id,
                    )
                )
            elif pw.watts is not None and pw.watts < LOW_CHARGER_W:
                out.append(
                    Issue("warning", "charger_low", f"{p.id}: charger negotiated only {pw.watts:g} W", p.id)
                )
    return out


# ---------------------------------------------------------------- entry point


def collect(fs: Sysfs | None = None, now: datetime | None = None) -> State:
    fs = fs or Sysfs()
    typec = _TypecCollector(fs).collect()
    roots = _UsbCollector(fs).collect()
    tb = _collect_thunderbolt(fs)
    issues = _issues(typec, roots)

    charging = next((p for p in typec if p.power and p.power.online and p.power_role == "sink"), None)
    external = sum(
        1 for rp in roots for d in _walk(rp.devices) if not d.internal and d.kind not in ("hub", "billboard")
    )
    vendor, product = fs.read(f"{DMI}/sys_vendor"), fs.read(f"{DMI}/product_name")
    return State(
        machine=" ".join(x for x in (vendor, product) if x) or None,
        schema=SCHEMA_VERSION,
        timestamp=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
        typec_ports=typec,
        root_ports=roots,
        thunderbolt=tb,
        issues=issues,
        summary=Summary(
            charging_w=charging.power.watts if charging and charging.power else None,
            charging_port=charging.id if charging else None,
            external_devices=external,
            warnings=sum(1 for i in issues if i.severity == "warning"),
        ),
    )

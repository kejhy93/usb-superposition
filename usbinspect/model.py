"""Data model. Everything is a plain dataclass so it serialises to JSON trivially."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = 1


@dataclass
class Interface:
    number: str
    cls: str | None
    driver: str | None


@dataclass
class BillboardMode:
    svid: str
    name: str
    mode: int
    state: str  # configured | failed | not_attempted | error


@dataclass
class UsbDevice:
    name: str                      # sysfs name, e.g. "1-2.1"
    vid: str | None
    pid: str | None
    manufacturer: str | None
    product: str | None
    serial: str | None
    usb_version: str | None        # bcdUSB, e.g. "3.20"
    speed_mbps: float | None       # negotiated
    speed_label: str
    rx_lanes: int | None
    tx_lanes: int | None
    max_mbps: int | None           # best the device advertises (per lane)
    max_source: str                # "bos" | "bcdUSB" | "unknown"
    upstream_mbps: float | None    # slowest link between this device and the host
    downgraded: bool
    downgrade_reason: str | None
    device_class: str | None
    interfaces: list[Interface]
    max_power_ma: int | None
    removable: str | None          # kernel's view: fixed | removable | unknown
    kind: str                      # hub | device | input_module | expansion_card | billboard
    internal: bool
    container_id: str | None       # same value = same physical device (USB2+USB3 halves)
    companion: str | None          # sysfs name of the other half of the same physical device
    billboard: list[BillboardMode] | None
    children: list["UsbDevice"] = field(default_factory=list)


@dataclass
class RootPort:
    id: str                        # e.g. "XHC0.PRT1+PRT6"
    ports: list[str]               # kernel port names, e.g. ["usb1-port1", "usb2-port1"]
    controller: str | None         # e.g. "XHC0"
    pci: str | None
    acpi: list[str]
    connect_type: str | None       # hotplug | hardwired | not used
    max_mbps: float | None
    devices: list[UsbDevice] = field(default_factory=list)


@dataclass
class Pdo:
    index: int
    type: str                      # fixed_supply | variable_supply | battery | programmable_supply ...
    voltage_mv: int | None = None
    min_voltage_mv: int | None = None
    max_voltage_mv: int | None = None
    current_ma: int | None = None
    power_mw: int | None = None
    watts: float | None = None
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class PdCaps:
    name: str                      # pdN
    revision: str | None
    source: list[Pdo]
    sink: list[Pdo]

    @property
    def max_source_w(self) -> float | None:
        ws = [p.watts for p in self.source if p.watts]
        return max(ws) if ws else None


@dataclass
class AltMode:
    svid: str
    name: str
    mode: int | None
    vdo: str | None
    active: bool | None
    description: str | None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class Identity:
    id_header: str | None
    cert_stat: str | None
    product: str | None
    product_type_vdos: list[str]
    vid: str | None = None
    product_type: str | None = None
    decoded: dict[str, Any] = field(default_factory=dict)


@dataclass
class Cable:
    type: str | None               # passive | active
    plug_type: str | None
    identity: Identity | None
    altmodes: list[AltMode]
    usb_speed: str | None          # decoded from e-marker
    current_ma: int | None
    max_voltage_v: int | None


@dataclass
class Partner:
    pd_supported: bool | None
    pd_revision: str | None
    accessory_mode: str | None
    altmodes: list[AltMode]
    identity: Identity | None
    pd: PdCaps | None


@dataclass
class PowerContract:
    supply: str
    online: bool
    status: str | None
    usb_type: str | None           # active type, e.g. "PD"
    usb_types: list[str]
    voltage_mv: int | None
    current_ma: int | None
    voltage_max_mv: int | None
    watts: float | None


@dataclass
class TypecPort:
    id: str                        # port0
    index: int
    data_role: str | None
    data_roles: list[str]
    power_role: str | None
    power_roles: list[str]
    power_operation_mode: str | None
    usb_capability: str | None
    usb_capabilities: list[str]
    typec_revision: str | None
    pd_revision: str | None
    orientation: str | None
    vconn_source: bool | None
    supported_altmodes: list[AltMode]
    pd: PdCaps | None
    partner: Partner | None
    cable: Cable | None
    power: PowerContract | None


@dataclass
class TbRouter:
    id: str
    vendor: str | None
    device: str | None
    generation: int | None
    rx_speed: str | None
    rx_lanes: int | None
    tx_speed: str | None
    tx_lanes: int | None
    authorized: bool | None
    usb4_ports: dict[str, str]     # usb4_portN -> link (none | usb4 | tbt ...)


@dataclass
class TbDomain:
    id: str
    pci: str | None
    security: str | None
    iommu_dma_protection: bool | None
    routers: list[TbRouter]


@dataclass
class Issue:
    severity: str                  # info | warning
    kind: str
    message: str
    ref: str | None = None         # device or port id


@dataclass
class Summary:
    charging_w: float | None
    charging_port: str | None
    external_devices: int
    warnings: int


@dataclass
class State:
    schema: int
    timestamp: str
    typec_ports: list[TypecPort]
    root_ports: list[RootPort]
    thunderbolt: list[TbDomain]
    issues: list[Issue]
    summary: Summary

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

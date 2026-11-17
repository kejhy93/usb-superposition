"""Human-readable names for USB ids, classes, SVIDs and speeds."""

from __future__ import annotations

import functools
from pathlib import Path

USB_IDS_PATHS = ("/usr/share/hwdata/usb.ids", "/usr/share/misc/usb.ids", "/usr/share/usb.ids")

USB_CLASSES = {
    0x00: "Per-interface",
    0x01: "Audio",
    0x02: "Communications",
    0x03: "HID",
    0x05: "Physical",
    0x06: "Imaging",
    0x07: "Printer",
    0x08: "Mass Storage",
    0x09: "Hub",
    0x0A: "CDC Data",
    0x0B: "Smart Card",
    0x0D: "Content Security",
    0x0E: "Video",
    0x0F: "Healthcare",
    0x10: "Audio/Video",
    0x11: "Billboard",
    0x12: "USB-C Bridge",
    0xDC: "Diagnostic",
    0xE0: "Wireless",
    0xEF: "Miscellaneous",
    0xFE: "Application Specific",
    0xFF: "Vendor Specific",
}

SVIDS = {
    "ff00": "USB PD",
    "ff01": "DisplayPort",
    "8087": "Thunderbolt",
    "05ac": "Apple",
    "1d5c": "Fresco/USB4",
    "2109": "VIA Labs",
}


def class_name(code: int | None) -> str | None:
    if code is None:
        return None
    return USB_CLASSES.get(code, f"0x{code:02x}")


def svid_name(svid: str) -> str:
    return SVIDS.get(svid.lower(), f"SVID {svid}")


def speed_label(mbps: float | None, lanes: int = 1) -> str:
    """Marketing-ish name for a negotiated USB speed."""
    if mbps is None:
        return "unknown"
    if mbps < 2:
        return "USB 1.x Low Speed · 1.5 Mbps"
    if mbps <= 12:
        return "USB 1.x Full Speed · 12 Mbps"
    if mbps <= 480:
        return "USB 2.0 High Speed · 480 Mbps"
    if mbps <= 5000:
        return "USB 3.2 Gen 1 · 5 Gbps"
    if mbps <= 10000:
        return "USB 3.2 Gen 1x2 · 10 Gbps" if lanes >= 2 else "USB 3.2 Gen 2 · 10 Gbps"
    if mbps <= 20000:
        return "USB 3.2 Gen 2x2 · 20 Gbps"
    return f"{mbps / 1000:g} Gbps"


def short_speed(mbps: float | None) -> str:
    if mbps is None:
        return "?"
    if mbps < 1000:
        return f"{mbps:g} Mbps"
    return f"{mbps / 1000:g} Gbps"


@functools.cache
def _usb_ids() -> dict[str, tuple[str, dict[str, str]]]:
    """vendor_id -> (vendor_name, {product_id: product_name})"""
    db: dict[str, tuple[str, dict[str, str]]] = {}
    for p in USB_IDS_PATHS:
        try:
            lines = Path(p).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        vendor = None
        for line in lines:
            if not line or line.startswith("#"):
                continue
            if line.startswith("C "):  # class section starts; vendors are done
                break
            if not line.startswith("\t"):
                vid, _, name = line.partition("  ")
                vendor = vid.lower()
                db[vendor] = (name.strip(), {})
            elif not line.startswith("\t\t") and vendor:
                pid, _, name = line.strip().partition("  ")
                db[vendor][1][pid.lower()] = name.strip()
        break
    return db


def lookup_vendor(vid: str | None) -> str | None:
    if not vid:
        return None
    entry = _usb_ids().get(vid.lower())
    return entry[0] if entry else None


def lookup_product(vid: str | None, pid: str | None) -> str | None:
    if not vid or not pid:
        return None
    entry = _usb_ids().get(vid.lower())
    return entry[1].get(pid.lower()) if entry else None

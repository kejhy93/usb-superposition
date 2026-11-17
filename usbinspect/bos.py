"""Parser for the USB Binary Object Store (BOS) descriptor.

The kernel exposes it unprivileged at /sys/bus/usb/devices/<dev>/bos_descriptors.
It tells us what a device *can* do, as opposed to what it negotiated.
"""

from __future__ import annotations

import contextlib
import struct
import uuid
from dataclasses import dataclass, field

BOS_DESCRIPTOR = 0x0F
DEVICE_CAPABILITY = 0x10

CAP_USB20_EXT = 0x02
CAP_SUPERSPEED = 0x03
CAP_CONTAINER_ID = 0x04
CAP_SUPERSPEED_PLUS = 0x0A
CAP_BILLBOARD = 0x0D
CAP_BILLBOARD_ALTMODE = 0x0F

# Billboard bmConfigured 2-bit states (USB Billboard Device Class spec 1.2x).
BILLBOARD_STATES = {
    0: "error",  # unspecified error
    1: "not_attempted",  # configuration not attempted or exited
    2: "failed",  # attempted but unsuccessful (e.g. cable can't carry it)
    3: "configured",  # alternate mode configuration successful
}

_LSE = {0: 1, 1: 1_000, 2: 1_000_000, 3: 1_000_000_000}


@dataclass
class BillboardAltMode:
    svid: str
    mode: int
    state: str


@dataclass
class BosInfo:
    lpm: bool = False  # USB 2.0 Link Power Management
    ss_speeds: list[int] = field(default_factory=list)  # Mbps values from SuperSpeed cap
    ssp_lane_mbps: list[int] = field(default_factory=list)  # SuperSpeedPlus sublink speeds
    ssp_min_rx_lanes: int | None = None
    ssp_min_tx_lanes: int | None = None
    container_id: str | None = None
    billboard: list[BillboardAltMode] | None = None
    billboard_failure_info: int | None = None

    @property
    def max_mbps(self) -> int | None:
        """Highest single-lane speed the device advertises, if any SuperSpeed cap."""
        speeds = self.ssp_lane_mbps + self.ss_speeds
        return max(speeds) if speeds else None


def _ss_speeds(mask: int) -> list[int]:
    # wSpeedsSupported: bit0 low, bit1 full, bit2 high, bit3 5 Gbps
    table = {0: 1, 1: 12, 2: 480, 3: 5000}  # low speed rounded to 1 for int-ness
    return [mbps for bit, mbps in table.items() if mask & (1 << bit)]


def parse_bos(data: bytes | None) -> BosInfo | None:
    if not data or len(data) < 5 or data[1] != BOS_DESCRIPTOR:
        return None
    total = struct.unpack_from("<H", data, 2)[0]
    data = data[: min(total, len(data))]
    info = BosInfo()
    off = data[0]
    while off + 3 <= len(data):
        length, dtype, cap = data[off], data[off + 1], data[off + 2]
        if length < 3 or off + length > len(data):
            break
        body = data[off : off + length]
        if dtype == DEVICE_CAPABILITY:
            # a malformed capability is skipped; the rest is still parsed
            with contextlib.suppress(struct.error, IndexError):
                _parse_cap(info, cap, body)
        off += length
    return info


def _parse_cap(info: BosInfo, cap: int, b: bytes) -> None:
    if cap == CAP_USB20_EXT:
        attrs = struct.unpack_from("<I", b, 3)[0]
        info.lpm = bool(attrs & 0x2)
    elif cap == CAP_SUPERSPEED:
        mask = struct.unpack_from("<H", b, 4)[0]
        info.ss_speeds = [s for s in _ss_speeds(mask) if s >= 5000]
    elif cap == CAP_CONTAINER_ID:
        info.container_id = str(uuid.UUID(bytes_le=bytes(b[4:20])))
    elif cap == CAP_SUPERSPEED_PLUS:
        attrs, func = struct.unpack_from("<IH", b, 4)
        count = (attrs & 0x1F) + 1  # SSAC is zero-based
        info.ssp_min_rx_lanes = (func >> 8) & 0xF
        info.ssp_min_tx_lanes = (func >> 12) & 0xF
        speeds = set()
        for i in range(count):
            a = struct.unpack_from("<I", b, 12 + 4 * i)[0]
            lse = (a >> 4) & 0x3
            mantissa = a >> 16
            speeds.add(mantissa * _LSE[lse] // 1_000_000)
        info.ssp_lane_mbps = sorted(speeds)
    elif cap == CAP_BILLBOARD:
        n_modes = b[4]
        configured = b[8:40]
        info.billboard_failure_info = b[42] if len(b) > 42 else None
        modes = []
        for i in range(n_modes):
            o = 44 + 4 * i
            if o + 3 > len(b):
                break
            svid, mode = struct.unpack_from("<HB", b, o)
            state = (configured[i // 4] >> (2 * (i % 4))) & 0x3
            modes.append(BillboardAltMode(f"{svid:04x}", mode, BILLBOARD_STATES[state]))
        info.billboard = modes

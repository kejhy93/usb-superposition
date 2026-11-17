# usb-superposition

[![CI](https://github.com/kejhy93/usb-superposition/actions/workflows/ci.yml/badge.svg)](https://github.com/kejhy93/usb-superposition/actions/workflows/ci.yml)

> A USB plug is in a superposition of "wrong way up" until observed. This tool observes
> the rest: what's connected, how it's connected, and what it *could* have negotiated.

Shows connected USB devices, USB-C ports, Power Delivery contracts and alternate
modes on Linux. Reads only `/sys`, so it needs no root. Built for a Framework
Laptop 16 on KDE Plasma 6, but the collector is generic.

Status: **milestone 2** (collector, CLI, physical bay mapping). TUI, notification daemon
with event history, and the Plasma widget are next. See [PLAN.md](PLAN.md).

## Usage

```sh
python3 -m usbinspect            # human-readable overview (internal devices hidden)
python3 -m usbinspect --all      # include keyboard, fingerprint reader, empty ports…
python3 -m usbinspect --json     # full machine-readable state
python3 -m usbinspect calibrate  # name the physical bays: plug something into each one
python3 -m usbinspect ports      # bay map and what's plugged into each bay
python3 -m usbinspect snapshot out.json   # record /sys for a test fixture (serials redacted)
python3 -m usbinspect --fixture out.json  # render from a recorded snapshot
```

Example:

```
⚡ Charging 75 W via port0 (20 V × 3.75 A, PD)
4 external device(s), 0 warning(s)

USB-C ports
  port0  host · sink
      power: 75 W contract (20 V × 3.75 A, PD)
      partner offers: 5 V 0.1 A (0.5 W) · 9 V 1.33 A (12 W) · 15 V 2 A (30 W) · 20 V 3.75 A (75 W)
      alt modes: Thunderbolt ○, DisplayPort ○  (● active ○ inactive)
...
USB
  XHC3.PRT1+PRT2 (hotplug, up to 10 Gbps)
    ├─ USB2.0 Hub  2109:2817  480 Mbps  [USB2 half, pair 6-1; hub]
    │  └─ USB Billboard Device  2109:8817  480 Mbps  [billboard]
    │     alt mode DisplayPort: configured
    └─ USB3.0 Hub  2109:0817  5 Gbps  [USB3 half, pair 5-1; hub]
```

## Physical bays

The kernel doesn't know which USB or USB-C port is which physical socket. On the
Framework 16 the firmware's `physical_location` data is the same placeholder for every
port. `usbinspect calibrate` walks you through each bay: plug any USB device into it,
or pull out and re-insert its expansion card. It records which port changed and saves
`~/.config/usbinspect/ports.toml`:

```toml
[[bay]]
id = "L1"
name = "Left rear"
usb = ["XHC3.PRT1", "XHC3.PRT2"]   # USB2 + USB3 halves, by ACPI name (stable across boots)
typec = ["port0"]                   # the bay's USB-C / PD connector
```

Ports behind an internal hub get keys like `XHC0.PRT2/1`. Built-in profiles in
`usbinspect/profiles/` are used when you have no map of your own.

## What it knows (all unprivileged)

| Information | Source |
|---|---|
| Negotiated USB speed and lanes | `/sys/bus/usb/devices/*/{speed,rx_lanes,tx_lanes}` |
| Max speed the device supports | BOS descriptor (`bos_descriptors`): SuperSpeed / SuperSpeedPlus caps |
| USB2 + USB3 halves of one device | BOS Container ID |
| Alt mode success/failure reported by the device | BOS Billboard capability |
| USB-C roles, PD revision | `/sys/class/typec/portN` |
| PD contract (V × A) | `/sys/class/power_supply/ucsi-source-psy-*` |
| Charger / partner PDOs | `/sys/class/usb_power_delivery/pdN` |
| Alt modes offered by partner/cable | `/sys/class/typec/portN-partner/*.N` |
| Cable e-marker (speed, 3/5 A, voltage) | `/sys/class/typec/portN-cable/identity` (often absent under UCSI) |
| USB4 / Thunderbolt routers and links | `/sys/bus/thunderbolt/devices` |

Warnings: SuperSpeed device stuck at USB 2, link slower than device and upstream allow,
charger without PD, charger under 60 W, alt mode failed.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

CI (GitHub Actions) runs ruff, then pytest on Python 3.11–3.14, then builds the
sdist and wheel. Every run also renders the recorded Framework 16 snapshot and the
runner's own `/sys`.

### Releasing

1. Bump `__version__` in `usbinspect/__init__.py` and commit.
2. `git tag v0.2.0 && git push origin v0.2.0`
3. The Release workflow checks that the tag matches the version, runs the checks, and
   publishes a GitHub release with the sdist and wheel attached.

## License

MIT, see [LICENSE](LICENSE).

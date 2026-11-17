# usb-superposition — plan

Show connected USB devices, Type-C port state, and negotiated vs. possible modes
on a Framework Laptop 16 (KDE Plasma 6, Wayland, Bazzite). Two frontends: a Plasma
plasmoid and a TUI. Unprivileged, sysfs only.

## Decisions (from Q&A)

| Topic | Decision |
|---|---|
| Frontends | Plasma 6 plasmoid + terminal TUI |
| Scope | USB data speed, Power Delivery, alt modes (DP / TBT / USB4), cable e-marker |
| Privilege | sysfs only — no root, no `framework_tool` |
| Internal devices | hidden by default (toggle to show) |
| Notifications | yes, desktop notifications on notable events |
| History | persistent event log (plug/unplug, PD contracts, alt modes, downgrades) |
| Location | `~/projects/usb-superposition`, installed user-local |

## Architecture

```
            sysfs  (/sys/bus/usb, /sys/class/typec, /sys/class/usb_power_delivery,
              │     /sys/class/power_supply/ucsi-*, /sys/bus/thunderbolt)
              ▼
   usbinspect/collect.py   ── pure functions: read sysfs → dataclasses → JSON
              │
   ┌──────────┼──────────────────────────┐
   ▼          ▼                          ▼
 CLI        daemon (systemd --user)      TUI (Textual)
 `usbinspect --json`                     `usbinspect tui`
            │ udev monitor (pyudev) + 3 s PD poll
            │ diff old/new state → notifications (D-Bus org.freedesktop.Notifications)
            │ writes $XDG_RUNTIME_DIR/usbinspect/state.json (atomic rename)
            │ appends events to ~/.local/state/usbinspect/events.jsonl
            ▼
        plasmoid (QML) reads state.json  (falls back to running `usbinspect --json`)
```

Why a daemon: notifications must fire even when the plasmoid popup is closed and
when no TUI is running; it also means the plasmoid never runs heavy Python itself.

## Data model (JSON)

```jsonc
{
  "timestamp": "...",
  "ports": [                       // Type-C ports from /sys/class/typec
    {
      "id": "port0", "label": "Left rear",     // label from mapping file
      "data_role": "host", "power_role": "sink",
      "pd_revision": "3.1",
      "partner": {
        "pd_revision": "3.0",
        "identity": {...},                      // VDOs when exposed
        "altmodes": [{"svid": "ff01", "name": "DisplayPort", "active": false}],
        "source_caps": [{"type": "fixed", "mv": 20000, "ma": 3250}, ...],
        "sink_caps": [...]
      },
      "cable": {"speed": "USB4 Gen3", "current_ma": 5000, "active": false} | null,
      "power": {"online": true, "mv": 20000, "ma": 3750, "w": 75.0, "type": "PD"},
      "usb_devices": [ ...device tree under the matching root hubs... ]
    }
  ],
  "other_devices": [ ... ],        // USB devices not mapped to a Type-C port
  "internal_devices": [ ... ],     // keyboard, touchpad, fingerprint, BT, input modules
  "thunderbolt": [ {"domain": 0, "link_speed": 20, "lanes": 2, "devices": [...]} ]
}
```

Per USB device: bus/port path, VID:PID, manufacturer, product, class, driver,
`speed` (negotiated), `bcdUSB` + BOS SuperSpeed caps (max possible), `rx/tx_lanes`,
`max_power` (bMaxPower), and a computed `downgraded: bool`.

## Hard parts / open technical risks

1. **Mapping USB devices → Type-C port.** UCSI gives 4 ports, Framework 16 has 6
   expansion bays (expansion cards are themselves USB devices — HDMI card, LAN card,
   etc.). Strategy:
   - try `/sys/class/typec/portN/usbN-portM` symlinks / `physical_location` /
     ACPI `firmware_node` path matching with `/sys/bus/usb/devices/usbX/X-Y/port/firmware_node`;
   - otherwise a user mapping file `~/.config/usbinspect/ports.toml`
     with a guided `usbinspect calibrate` (plug a device into each bay in turn).
2. **Internal device detection.** Use `removable` attribute on the USB port
   (`fixed` = internal), plus a VID:PID allowlist for Framework input modules
   (keyboard, macropad, numpad, LED matrix — these are "hot-pluggable" but internal).
3. **Cable e-marker.** Often absent under UCSI → show "unknown" not an error.
4. **"Possible" vs "negotiated"** — what we can know without root:
   - data: device max (bcdUSB/BOS) vs. negotiated `speed`; port/hub max from root hub.
   - power: all partner source PDOs vs. active contract (`voltage_now`/`current_max`).
   - alt modes: listed vs. `active`.
   - USB4: thunderbolt `rx_speed/tx_speed/rx_lanes/tx_lanes` vs. generation.
5. **PD changes don't always emit uevents** → poll `power_supply/ucsi-*` every 3 s
   in the daemon (cheap).

## Notifications (debounced, each toggleable in config)

- Device connected / disconnected (off by default — noisy)
- Device negotiated below its capability (e.g. 10 Gbps device at 480 Mbps → bad cable/port)
- Charger connected: "65 W PD (20 V 3.25 A)"; warn if < 60 W or non-PD
- DisplayPort / Thunderbolt alt mode entered or failed
- USB4 link at reduced width/speed

## Event history

- The daemon diffs each new state against the previous one and appends one JSON line
  per event to `~/.local/state/usbinspect/events.jsonl` (XDG_STATE_HOME):
  `{"ts", "kind", "port", "device": {vid, pid, product, serial}, "details": {...}}`.
- Kinds: `device_added`, `device_removed`, `speed_downgrade`, `pd_contract`
  (V, A, W, PDO list), `pd_lost`, `altmode_entered`, `altmode_exited`,
  `usb4_link`, `daemon_start` (with a full state snapshot, so the log is self-contained).
- Rotation: when the file exceeds 5 MB, rename to `events.jsonl.1` (keep 3).
- Uses: the TUI gets a History tab (filter by port/device/kind); the plasmoid shows
  the last 10 events; `usbinspect history [--since 1d] [--device VID:PID]` in the CLI.
- Bonus: per-device stats derived from the log ("this SSD fell back to USB 2 4× this week",
  "charger X always negotiates 45 W").

## Plasmoid

- Plasma 6 QML package `org.hejnaluk.usbsuperposition` in `plasmoid/`.
- Compact rep: USB icon + badge (charging watts if charging, else external device count;
  warning tint if any downgrade).
- Full rep: one card per port (label, role chips, PD contract, alt modes,
  device tree with speed bar "negotiated / max"), section for unmapped devices,
  "Show internal" toggle.
- Data: read `state.json` via `Plasma5Support.DataSource` executable engine
  (`cat` the file) on a 2 s timer while expanded, 10 s while collapsed.
- Install: `kpackagetool6 -t Plasma/Applet -i plasmoid/` (user-local).

## TUI

- Textual app: left = port tree, right = detail pane for selected node
  (all raw sysfs attrs available via a "raw" tab). Keys: `i` toggle internal,
  `r` refresh, `j` dump JSON, `h` history tab, `q` quit.
- Uses the collector directly with its own pyudev monitor (works without the daemon).

## Layout

```
usb-superposition/
  pyproject.toml            # entry point `usbinspect`
  usbinspect/
    collect.py  model.py  usbids.py  portmap.py
    daemon.py   notify.py  history.py  tui.py  cli.py
  plasmoid/  metadata.json  contents/ui/main.qml ...
  systemd/usbinspect.service
  tests/fixtures/sysfs-*/   # captured sysfs snapshots for offline tests
  install.sh
```

## Dependencies

- Python 3.14 (system), `pyudev` (already present system-wide).
- `textual` — installed into a project venv (`.venv`, `--system-site-packages`
  so pyudev is reused). Nothing layered onto the immutable image.
- Vendor/product names: sysfs strings first, `/usr/share/hwdata/usb.ids` fallback.

## Milestones

1. Collector + `usbinspect --json` + sysfs snapshot fixtures & tests.
2. Port mapping (auto + calibrate).
3. TUI.
4. Daemon + notifications + event history + systemd user unit.
5. Plasmoid.
6. install.sh, README.

# usb-superposition — plan

Show connected USB devices, Type-C port state, and negotiated vs. possible modes
on a Framework Laptop 16 (Wayland). Frontends: a TUI, an Omarchy shell bar widget, and
a KDE Plasma 6 plasmoid. Unprivileged, sysfs only.

## Decisions (from Q&A)

| Topic | Decision |
|---|---|
| Frontends | terminal TUI + Omarchy shell bar widget; Plasma 6 plasmoid |
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
   desktop widgets (QML) read state.json:
     Omarchy bar widget (Quickshell)  ·  Plasma plasmoid
```

Why a daemon: notifications must fire even when no widget popup is open and
when no TUI is running; it also means the widgets never run heavy Python themselves.

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

## Findings from milestone 1 (Framework 16, kernel 6.19)

- `bos_descriptors` is world-readable in sysfs, so the device's real max speed
  (SuperSpeedPlus sublink speeds) is available without root.
- BOS **Container ID** pairs the USB2 and USB3 halves of hubs/docks, which keeps
  the USB2 half from being reported as a downgrade.
- BOS **Billboard** capability reports alt mode results (e.g. DisplayPort "configured").
  UCSI reports partner alt modes as `active=no` even while DP works through the dock,
  so Billboard is the better signal.
- No kernel `connector` links exist between USB ports and Type-C ports, and
  `physical_location` is identical for every port. Mapping has to be ACPI/PCI
  heuristics + calibration (milestone 2). Root ports are grouped by `peer` links
  and named by ACPI path, e.g. `XHC0.PRT1+PRT6`.
- USB4 domains live on the same PCI device as XHC3/XHC4 (`0000:c7:00.x`), which is a
  hint for which root ports are USB4-capable.
- Ports 1 and 2 have a Type-C partner with no PD and no alt modes, probably the
  expansion cards themselves.
- The package is `usbinspect`, stdlib-only. Fixtures are recorded with
  `usbinspect snapshot` (serials and USB4 unique ids redacted).

## Findings from milestone 2

- `physical_location` holds firmware placeholders on the Framework 16 (every Type-C port says
  top/upper/left, every USB port says left/center/center), so it's useless. Calibration is required.
- Port keys: root ports use their ACPI name (`XHC0.PRT1`), and hub ports append the hub port
  number (`XHC0.PRT2/1`). Bus numbers (`usbN`) aren't used because they can change between boots.
- A bay owns both USB2 and USB3 keys. Calibration adds the peer automatically (root port peers,
  with the same hub-port suffix).
- XHC0.PRT2 (hardwired) leads to an internal Genesys hub (USB2 half 1-2 with 4 ports, USB3 half
  2-2 with 3 ports). Some bays are probably behind it. To be confirmed by calibration.
- Calibration takes a full `collect()` snapshot every 0.3 s (about 23 ms each) and diffs USB
  devices, Type-C partners and online supplies.

## Findings from milestone 4

- Notifications go through `gdbus call ... Notify` (GLib ships it everywhere; no Python D-Bus
  dependency). Every argument is parsed as GVariant text, so strings must be quoted and the
  timeout written as `int32 -1`, because a bare `-1` is taken as a command-line option.
- Re-collecting the whole state every 3 s cost about 0.8% of a core. The 3 s poll now only reads
  the `ucsi-*` power supplies (0.35 ms), with a full re-read on udev events or every 60 s:
  about 0.05% of a core idle.
- Plasma 6 runs under systemd (`graphical-session.target`), so the user unit is
  `WantedBy=graphical-session.target`. Its ExecStart is the absolute path of `usbinspect`
  (currently `~/.local/bin/usbinspect` → project venv).
- `state.json` = `State.to_dict()` + `recent_events` (last 10, non-internal) + `daemon` info,
  written atomically. This is the plasmoid's data source.

## Findings from milestone 6

- `install.sh` is idempotent: links are only replaced when they are symlinks, never real files,
  and `omarchy bar put` keeps a widget that is already on the bar. Uninstall removes only links
  that point into this repo and keeps the venv, config and history.
- The service is installed with `~/.local/bin` first on `PATH`, so ExecStart is the stable
  symlink rather than the venv path.
- Right after `omarchy-shell shell rescanPlugins` the shell drops calls ("omarchy-shell is not
  responding") for a moment, so `bar put` / `plugin enable` are retried 5 × 1 s.
- `omarchy plugin enable <id>` with a placement moves an existing widget, hence `bar put` first
  (with placement) and then `enable` without one.

## Findings from milestone 7

- The plasmoid reuses the Omarchy widget's `Model.js` through a relative symlink
  (`plasmoid/contents/ui/Model.js`). KPackage follows a symlinked package directory, so
  `install.sh` links `plasmoid/` into `~/.local/share/plasma/plasmoids/<id>` instead of
  `kpackagetool6 -i`, which copies and would break the inner link.
- Strings live in `Strings.js` under the same keys as `omarchy/i18n/en.json` (a test keeps the
  Model.js ones identical). Not `i18n()`: KI18n marks templates whose `%N` are filled
  later with `.arg()` as missing arguments.
- Data: the `executable` DataSource runs `cat` on `state.json` through a shell, so both commands
  (read, daemon reload) are fixed strings, and a test checks that only those reach `connectSource`.
  A non-zero exit means "daemon not running"; a parse failure keeps the last good state.
- `deviceRow.ratio` (negotiated / max, or -1) was added to Model.js for the speed bar; the
  Omarchy panel ignores it.
- `plasmawindowed <id>` runs the applet in a window, handy for testing without touching the panel.

## Findings from milestone 5

- The plugin is `BarWidget.qml` (owns the `FileView`, the view model and the bar button) +
  `UsbPanel.qml` (presentation only) + `Model.js` (pure JS, `.pragma library`). Model.js has no
  Qt calls, so pytest runs it under node against a state built from the FW16 fixture with
  `tests/fixtures/fw16-ports.toml`. Device trees are flattened to rows with a depth, so the
  QML has no recursive delegates.
- `FileView { watchChanges: true }` + `reload()` in `onFileChanged` follows the daemon's
  atomic rename. When the file is missing, `onLoadFailed` retries every 5 s.
- The daemon now deletes `state.json` on a clean stop, so the widget says "daemon not running"
  instead of showing stale data.
- The shell runs with `QS_DISABLE_FILE_WATCHER=1` and caches compiled plugin QML: the
  "Local plugin changed, reloading" message does not pick up code edits. `omarchy-restart-shell`
  does. Disabling and re-enabling a plugin removes it from the bar layout.
- `Qt.formatDateTime(date, locale, fmt)` ignores the format; use
  `date.toLocaleString(Qt.locale(...), fmt)` like the clock widget.
- The USB 2 half of a hub (it has a companion) shows no "/ max": its 480 Mbps is by design.
- This machine is now a Framework Laptop 12 (13th Gen Intel); the user map in
  `~/.config/usbinspect/ports.toml` is for it.

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
- Uses: the TUI gets a History tab (filter by port/device/kind); the widgets show
  the last 10 events; `usbinspect history [--since 1d] [--device VID:PID]` in the CLI.
- Bonus: per-device stats derived from the log ("this SSD fell back to USB 2 4× this week",
  "charger X always negotiates 45 W").

## Omarchy bar widget

The machine now runs Omarchy (Hyprland + Quickshell-based `omarchy-shell`, no Waybar), so
this desktop widget comes first. It is a user shell plugin, same shape as the other plugins
in `~/.config/omarchy/plugins/`.

- Plugin in `omarchy/` in this repo: `manifest.json` (`schemaVersion: 1`,
  `kinds: ["bar-widget"]`, `entryPoints.barWidget: "BarWidget.qml"`), `BarWidget.qml`,
  `UsbPanel.qml`, `i18n/en.json`.
- Bar item: USB glyph + badge (charging watts if charging, else external device count;
  warning colour if any issue). Click opens the panel.
- Panel (built on `qs.Ui` `KeyboardPanel`, so motion and keyboard handling match the shell):
  a USB-C port section (roles, PD contract, alt modes), bays with their device trees and a
  "negotiated / max" speed per device, issues, and the last 10 events. "Show internal" toggle.
- Data: Quickshell `FileView` on `$XDG_RUNTIME_DIR/usbinspect/state.json` with change
  watching, so no polling; the daemon rewrites it atomically. If the file is missing,
  show "daemon not running" with the `usbinspect service install` hint.
- Safety: device names and serials come from USB descriptors and are untrusted, so every
  `Text` uses `textFormat: Text.PlainText`. Never interpolate state values into commands.
- Theming, motion and strings follow the shell: `qs.Commons` `Style` tokens and durations,
  `Commons.Translations` with `i18n/en.json`.
- Install: symlink `omarchy/` to `~/.config/omarchy/plugins/usb-superposition`, then add
  the widget to a bar section (`omarchy bar` / `shell.json`). Plugin code hot-reloads on save.
- Tests (`tests/test_omarchy.py`): manifest, every `t()` key exists in `en.json` and none
  is unused, every `Text` is PlainText, no `Process` in the QML, and the view model under node.

## Plasmoid

- Plasma 6 QML package `org.hejnaluk.usbsuperposition` in `plasmoid/`.
- Compact rep: USB icon + badge (charging watts if charging, else external device count;
  warning tint if any downgrade).
- Full rep: one card per port (label, role chips, PD contract, alt modes,
  device tree with speed bar "negotiated / max"), section for unmapped devices,
  "Show internal" toggle.
- Data: read `state.json` via `Plasma5Support.DataSource` executable engine
  (`cat` the file) on a 2 s timer while expanded, 10 s while collapsed.
- Install: `install.sh` symlinks `plasmoid/` into `~/.local/share/plasma/plasmoids/` (user-local).

## TUI

- Textual app: left = tree (USB-C ports, bays or root ports, USB4, issues), right = tabs
  Details (rich tables: PDOs, alt modes, interfaces, Billboard), Raw (JSON of the node),
  Events (this session; milestone 4 will add the persistent history here).
  Keys: `i` internal, `m` bays/root ports, `e` expand, `d`/`w`/`h` tabs, `j` save JSON, `r`, `q`.
- Change detection lives in `usbinspect/events.py` (`diff(old, new)`): device added/removed
  (top of the plugged tree only), PD contract/lost, newly appearing issues. The daemon will reuse it.
- Rebuilds only when the state changes (fingerprint without timestamp). Expanded nodes and
  the selection survive rebuilds.
- Uses the collector directly with its own pyudev monitor (works without the daemon).

## Layout

```
usb-superposition/
  pyproject.toml            # entry point `usbinspect`
  usbinspect/
    sysfs.py  bos.py  names.py  model.py  collect.py  render.py  portmap.py
    daemon.py   notify.py  history.py  tui.py  cli.py
  omarchy/   manifest.json  BarWidget.qml  UsbPanel.qml  Model.js  i18n/en.json
  plasmoid/  metadata.json  contents/ui/main.qml ...
  systemd/usbinspect.service
  tests/fixtures/sysfs-*/   # captured sysfs snapshots for offline tests
  install.sh                # user-local install / --uninstall
```

## Dependencies

- Python 3.14 (system), `pyudev` (already present system-wide).
- `textual` — installed into a project venv (`.venv`, `--system-site-packages`
  so pyudev is reused). Nothing layered onto the immutable image.
- Vendor/product names: sysfs strings first, `/usr/share/hwdata/usb.ids` fallback.

## Milestones

1. ✅ Collector + `usbinspect --json` + sysfs snapshot fixtures & tests.
2. ✅ Port mapping: stable port keys, `ports.toml`, `usbinspect calibrate` and `usbinspect ports`. Built-in Framework 16 profile still to come from a real calibration.
3. ✅ TUI (`usbinspect tui`): live tree, Details/Raw/Events tabs, toasts, udev + 3 s poll.
4. ✅ Daemon + notifications + event history + systemd user unit (`usbinspect daemon|history|service|config`).
5. ✅ Omarchy shell bar widget (`omarchy/`), reading the daemon's `state.json`.
6. ✅ `install.sh` (venv, `~/.local/bin` link, service, Omarchy widget; `--uninstall`), README install section.
7. ✅ Plasma 6 plasmoid (`plasmoid/`, shares `omarchy/Model.js`; installed by `install.sh`).

.pragma library

// English strings for the plasmoid, keyed like omarchy/i18n/en.json so the shared
// Model.js can call t(key) here too. Placeholders are %1, %2 for QString-style .arg().
// (Plain strings rather than i18n(): KI18n flags templates whose %N are filled later.)

var en = {
  "tooltip.noDaemon": "usbinspect daemon not running",
  "tooltip.charging": "Charging %1",
  "tooltip.oneDevice": "1 external USB device",
  "tooltip.devices": "%1 external USB devices",
  "tooltip.oneWarning": "1 warning",
  "tooltip.warnings": "%1 warnings",
  "panel.title": "USB",
  "panel.noDaemon": "Daemon not running",
  "panel.noDaemonHelp": "This widget shows what the usbinspect daemon publishes. Start it with your session:",
  "panel.charging": "Charging %1 · %2",
  "panel.notCharging": "Not charging over USB-C",
  "panel.showInternal": "Internal",
  "panel.showInternalAction": "Show internal devices",
  "panel.speedLegend": "negotiated / max",
  "panel.calibrateHint": "Grouped by kernel port. Run `usbinspect calibrate` to name the physical bays.",
  "panel.noDevices": "No external USB devices",
  "panel.refresh": "Refresh: re-scan USB now",
  "panel.updated": "Updated %1",
  "section.issues": "Issues",
  "section.ports": "USB-C ports",
  "section.bays": "Bays",
  "section.usb": "USB",
  "section.events": "Recent events",
  "port.empty": "Nothing connected",
  "port.contract": "⚡ %1",
  "port.offers": "Charger offers up to %1",
  "port.altmodes": "Alt modes: %1",
  "port.cable": "Cable: %1",
  "port.cableUnknown": "no e-marker data",
  "port.partnerOnly": "Partner connected (no PD, no alt modes reported)",
  "group.other": "Other USB ports",
  "group.upTo": "up to %1",
  "group.empty": "Empty",
  "device.usb2Half": "USB 2 half",
  "device.usb3Half": "USB 3 half",
  "device.altmode": "Alt mode %1: %2",
  "kind.input_module": "input module",
  "kind.expansion_card": "expansion card",
  "kind.billboard": "billboard"
}

function t(key) {
  return Object.prototype.hasOwnProperty.call(en, key) ? en[key] : key
}

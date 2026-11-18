.pragma library

// Pure view model for the bar widget: state.json document in, plain objects out.
// No Qt APIs here, so tests/test_omarchy.py can run it under node. Every string
// that reaches the screen comes from the daemon (descriptor strings are untrusted)
// or from `t(key)`; QML renders all of it with Text.PlainText.

var SCHEMA = 2
var MAX_NAME = 64

function parseState(text) {
  var doc = null
  try { doc = JSON.parse(String(text || "")) } catch (e) { return null }
  if (!doc || typeof doc !== "object" || Array.isArray(doc)) return null
  if (doc.schema !== SCHEMA) return null
  if (!Array.isArray(doc.typec_ports) || !Array.isArray(doc.root_ports)) return null
  return doc
}

function num(v) {
  var n = Number(v)
  return v === null || v === undefined || !isFinite(n) ? null : n
}

// 2 decimals at most, no trailing zeros, like Python's :g for our ranges.
function fmt(n) {
  return String(Math.round(n * 100) / 100)
}

function clip(s, max) {
  s = String(s === null || s === undefined ? "" : s).replace(/[\u0000-\u001f\u007f]/g, " ")
  max = max || MAX_NAME
  return s.length > max ? s.slice(0, max - 1) + "…" : s
}

function speedText(mbps) {
  var n = num(mbps)
  if (n === null) return "?"
  return n < 1000 ? fmt(n) + " Mbps" : fmt(n / 1000) + " Gbps"
}

function wattsText(w) {
  var n = num(w)
  return n === null ? "" : fmt(n) + " W"
}

function contractText(power) {
  if (!power || !power.online || num(power.watts) === null) return ""
  var bits = [wattsText(power.watts)]
  var v = num(power.voltage_mv), a = num(power.current_ma)
  if (v !== null && a !== null) bits.push(fmt(v / 1000) + " V × " + fmt(a / 1000) + " A")
  if (power.usb_type) bits.push(clip(power.usb_type, 16))
  return bits.join(" · ")
}

function maxOfferW(partner) {
  var src = partner && partner.pd && Array.isArray(partner.pd.source) ? partner.pd.source : []
  var best = null
  for (var i = 0; i < src.length; i++) {
    var w = num(src[i].watts)
    if (w !== null && (best === null || w > best)) best = w
  }
  return best
}

function deviceName(d) {
  if (d.product) return clip(d.product)
  if (d.vid && d.pid) return clip(d.vid + ":" + d.pid)
  return clip(d.name || "?")
}

// Internal devices are hidden unless something external hangs below them
// (same rule as render.visible).
function visible(d, showInternal) {
  if (showInternal || !d.internal) return true
  var kids = d.children || []
  for (var i = 0; i < kids.length; i++) if (visible(kids[i], false)) return true
  return false
}

// For "Other USB" when a bay map exists: skip anything already shown in a bay.
function visibleUnbayed(d, showInternal) {
  if (d.bay !== null && d.bay !== undefined) return false
  if (showInternal || !d.internal) return true
  var kids = d.children || []
  for (var i = 0; i < kids.length; i++) if (visibleUnbayed(kids[i], false)) return true
  return false
}

function deviceRow(d, depth, t) {
  var downgraded = d.downgraded === true
  var speed = speedText(d.speed_mbps)
  var maxKnown = num(d.max_mbps) !== null && d.max_source !== "unknown"
  var usb2Half = !!d.companion && num(d.speed_mbps) !== null && num(d.speed_mbps) <= 480
  var tags = []
  if (d.kind && d.kind !== "device" && d.kind !== "hub") tags.push(t("kind." + d.kind))
  if (d.companion) tags.push(usb2Half ? t("device.usb2Half") : t("device.usb3Half"))
  var modes = []
  var bb = Array.isArray(d.billboard) ? d.billboard : []
  for (var i = 0; i < bb.length; i++) {
    modes.push({ name: clip(bb[i].name, 24), ok: bb[i].state === "configured", state: clip(bb[i].state, 24) })
  }
  return {
    key: String(d.name || ""),
    depth: depth,
    name: deviceName(d),
    ids: d.vid && d.pid ? clip(d.vid + ":" + d.pid, 16) : "",
    speed: speed,
    // "negotiated / max" only when it adds something. The USB 2 half of a hub or
    // dock always runs at 480 Mbps by design; its USB 3 companion carries the rest.
    maxSpeed: maxKnown && num(d.max_mbps) !== num(d.speed_mbps) && !(usb2Half && !downgraded) ? speedText(d.max_mbps) : "",
    downgraded: downgraded,
    reason: downgraded && d.downgrade_reason ? clip(d.downgrade_reason, 120) : "",
    internal: d.internal === true,
    tags: tags.join(" · "),
    modes: modes
  }
}

function flatten(devs, pred, depth, t, out) {
  for (var i = 0; i < devs.length; i++) {
    var d = devs[i]
    if (!pred(d)) continue
    out.push(deviceRow(d, depth, t))
    flatten(d.children || [], pred, depth + 1, t, out)
  }
  return out
}

function allDevices(doc) {
  var out = {}
  var stack = []
  for (var i = 0; i < doc.root_ports.length; i++) stack = stack.concat(doc.root_ports[i].devices || [])
  while (stack.length) {
    var d = stack.pop()
    out[d.name] = d
    stack = stack.concat(d.children || [])
  }
  return out
}

function bayNames(doc) {
  var names = {}
  var bays = Array.isArray(doc.bays) ? doc.bays : []
  for (var i = 0; i < bays.length; i++) names[bays[i].id] = clip(bays[i].name, 32)
  return names
}

function ports(doc, t) {
  var names = bayNames(doc)
  var out = []
  for (var i = 0; i < doc.typec_ports.length; i++) {
    var p = doc.typec_ports[i]
    var partner = p.partner
    var altmodes = []
    var am = partner && Array.isArray(partner.altmodes) ? partner.altmodes : []
    for (var j = 0; j < am.length; j++) altmodes.push({ name: clip(am[j].name, 24), active: am[j].active === true })
    var cable = ""
    if (p.cable) {
      var bits = []
      if (p.cable.type) bits.push(clip(p.cable.type, 16))
      if (p.cable.usb_speed) bits.push(clip(p.cable.usb_speed, 24))
      if (num(p.cable.current_ma) !== null) bits.push(fmt(p.cable.current_ma / 1000) + " A")
      if (num(p.cable.max_voltage_v) !== null) bits.push(fmt(p.cable.max_voltage_v) + " V")
      cable = bits.length ? bits.join(" · ") : t("port.cableUnknown")
    }
    var offer = maxOfferW(partner)
    out.push({
      id: String(p.id),
      label: p.bay && names[p.bay] ? names[p.bay] : String(p.id),
      sublabel: p.bay && names[p.bay] ? String(p.id) : "",
      roles: clip((p.data_role || "?") + " · " + (p.power_role || "?"), 32),
      connected: !!partner,
      charging: !!(p.power && p.power.online && num(p.power.watts) !== null),
      contract: contractText(p.power),
      offers: offer !== null ? wattsText(offer) : "",
      altmodes: altmodes,
      cable: cable
    })
  }
  return out
}

function groups(doc, showInternal, t) {
  var out = []
  var bays = Array.isArray(doc.bays) ? doc.bays : []
  var shown = function(d) { return visible(d, showInternal) }
  if (bays.length) {
    var byName = allDevices(doc)
    for (var i = 0; i < bays.length; i++) {
      var b = bays[i]
      var tops = []
      var names = b.devices || []
      for (var j = 0; j < names.length; j++) if (byName[names[j]]) tops.push(byName[names[j]])
      var watts = ""
      for (var k = 0; k < doc.typec_ports.length; k++) {
        var p = doc.typec_ports[k]
        if (p.bay === b.id && p.power && p.power.online && num(p.power.watts) !== null) watts = wattsText(p.power.watts)
      }
      out.push({ title: clip(b.name, 32), subtitle: "", watts: watts, rows: flatten(tops, shown, 0, t, []) })
    }
    var otherPred = function(d) { return visibleUnbayed(d, showInternal) }
    var other = []
    for (var r = 0; r < doc.root_ports.length; r++) flatten(doc.root_ports[r].devices || [], otherPred, 0, t, other)
    if (other.length) out.push({ title: t("group.other"), subtitle: "", watts: "", rows: other })
    return out
  }
  for (var n = 0; n < doc.root_ports.length; n++) {
    var rp = doc.root_ports[n]
    var rows = flatten(rp.devices || [], shown, 0, t, [])
    if (!rows.length) continue
    out.push({
      title: clip(rp.id, 32),
      subtitle: num(rp.max_mbps) !== null ? t("group.upTo").arg(speedText(rp.max_mbps)) : "",
      watts: "",
      rows: rows
    })
  }
  return out
}

function issues(doc) {
  var out = []
  var list = Array.isArray(doc.issues) ? doc.issues : []
  for (var i = 0; i < list.length; i++) {
    out.push({ warning: list[i].severity === "warning", message: clip(list[i].message, 200) })
  }
  return out
}

// Newest first. `time` is epoch ms (QML formats it in the user's locale).
function events(doc) {
  var out = []
  var list = Array.isArray(doc.recent_events) ? doc.recent_events : []
  for (var i = list.length - 1; i >= 0; i--) {
    var e = list[i]
    var ms = Date.parse(e.ts)
    out.push({
      time: isFinite(ms) ? ms : 0,
      kind: String(e.kind || ""),
      warning: e.severity === "warning",
      message: clip(e.message, 200)
    })
  }
  return out
}

// Bar badge: charging watts while charging, else the external device count.
function badge(doc) {
  var s = doc.summary || {}
  var w = num(s.charging_w)
  if (w !== null) return wattsText(w).replace(" ", "")
  var n = num(s.external_devices)
  return n !== null && n > 0 ? String(n) : ""
}

function hasWarning(doc) {
  var s = doc.summary || {}
  if (num(s.warnings) > 0) return true
  var list = Array.isArray(doc.issues) ? doc.issues : []
  for (var i = 0; i < list.length; i++) if (list[i].severity === "warning") return true
  return false
}

function tooltip(doc, t) {
  var s = doc.summary || {}
  var bits = []
  var w = num(s.charging_w)
  if (w !== null) bits.push(t("tooltip.charging").arg(wattsText(w)))
  var n = num(s.external_devices) || 0
  bits.push(n === 1 ? t("tooltip.oneDevice") : t("tooltip.devices").arg(n))
  var warn = num(s.warnings) || 0
  if (warn > 0) bits.push(warn === 1 ? t("tooltip.oneWarning") : t("tooltip.warnings").arg(warn))
  return bits.join(" · ")
}

function buildView(doc, showInternal, t) {
  if (!doc) return null
  var s = doc.summary || {}
  var chargingPort = null
  for (var i = 0; i < doc.typec_ports.length; i++) {
    if (doc.typec_ports[i].id === s.charging_port) chargingPort = doc.typec_ports[i]
  }
  var names = bayNames(doc)
  return {
    machine: clip(doc.machine || "", 80),
    updated: isFinite(Date.parse(doc.timestamp)) ? Date.parse(doc.timestamp) : 0,
    badge: badge(doc),
    warning: hasWarning(doc),
    tooltip: tooltip(doc, t),
    charging: chargingPort ? contractText(chargingPort.power) : "",
    chargingW: chargingPort ? wattsText(chargingPort.power.watts) : "",
    chargingWhere: chargingPort ? (chargingPort.bay && names[chargingPort.bay] ? names[chargingPort.bay] : String(chargingPort.id)) : "",
    externalDevices: num(s.external_devices) || 0,
    mapped: Array.isArray(doc.bays) && doc.bays.length > 0,
    ports: ports(doc, t),
    groups: groups(doc, showInternal, t),
    issues: issues(doc),
    events: events(doc)
  }
}

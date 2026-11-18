import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model

// Bar item for usb-superposition. Reads the state the `usbinspect daemon` publishes
// to $XDG_RUNTIME_DIR/usbinspect/state.json (rewritten atomically on every change),
// so nothing here polls sysfs or runs commands.
BarWidget {
  id: root
  moduleName: "usb.superposition"

  readonly property string statePath: (Quickshell.env("XDG_RUNTIME_DIR") || ("/run/user/" + Quickshell.env("UID"))) + "/usbinspect/state.json"
  readonly property bool showInternal: setting("showInternal", false) === true
  property var doc: null
  property bool missing: true
  readonly property var view: Model.buildView(root.doc, root.showInternal, function(key) { return strings.t(key) })
  readonly property string glyph: "\u{f0553}"

  Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }

  property bool refreshing: false

  function reload() { stateFile.reload() }
  // Manual refresh: ask the daemon to re-scan now (SIGHUP via the unit's ExecReload);
  // it republishes state.json, which the FileView picks up. Fixed argv, no state values.
  function refresh() {
    if (refreshProc.running) return
    root.refreshing = true
    refreshProc.running = true
  }
  function togglePanel() { if (panelLoader.item) panelLoader.item.toggle() }
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened : false
  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing : false
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }

  function toggleInternal() {
    root.settings = Object.assign({}, root.settings, { showInternal: !root.showInternal })
    if (root.bar && root.bar.shell) root.bar.shell.updateEntryInline(root.moduleName, root.settings)
  }

  function load(text) {
    var parsed = Model.parseState(text)
    // Keep the last good state if a read races the daemon's rename.
    if (parsed) root.doc = parsed
    root.missing = !parsed && root.doc === null
  }

  FileView {
    id: stateFile
    path: root.statePath
    watchChanges: true
    printErrors: false
    // The daemon replaces the file with rename(), which drops the inotify watch on
    // the old inode; reloading re-arms it on the new one.
    onFileChanged: reload()
    onLoaded: root.load(text())
    onLoadFailed: function(error) {
      root.doc = null
      root.missing = true
      retry.restart()
    }
  }

  Process {
    id: refreshProc
    command: ["systemctl", "--user", "reload", "usbinspect.service"]
    onExited: function(exitCode) {
      // Not running as a service (or not at all): re-read whatever is there.
      if (exitCode !== 0) root.broadcast("reload")
      settle.restart()
    }
  }

  // Keep the button busy until the daemon's re-scan has had time to land.
  Timer {
    id: settle
    interval: 800
    onTriggered: { root.refreshing = false; root.broadcast("reload") }
  }

  // Only while the file is missing (daemon not started yet, or restarting).
  Timer {
    id: retry
    interval: 5000
    repeat: false
    onTriggered: stateFile.reload()
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function injectPanel() {
    var p = panelLoader.item
    if (!p) return
    p.bar = root.bar
    p.anchorItem = button
    p.widget = root
  }
  onBarChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("UsbPanel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  TextMetrics {
    id: badgeMetrics
    font.family: button.fontFamily
    font.pixelSize: button.fontSize
    text: button.labelText
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.glyph
    labelText: !vertical && root.view ? root.view.badge : ""
    slotSize: Style.bar.iconSlot + (labelText !== "" ? badgeMetrics.advanceWidth + Style.space(4) : 0)
    // Urgent tint when the daemon reports a warning (downgrade, weak charger...).
    active: !!root.view && root.view.warning
    dimmed: root.missing
    tooltipText: root.missing ? strings.t("tooltip.noDaemon") : (root.view ? root.view.tooltip : "")
    onPressed: function(b) {
      if (b === Qt.MiddleButton) root.refresh()
      else root.togglePanel()
    }
  }
}

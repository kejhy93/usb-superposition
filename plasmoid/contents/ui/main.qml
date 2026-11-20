import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents3
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as P5Support
import org.kde.plasma.plasmoid
import "Model.js" as Model
import "Strings.js" as Strings

// Plasma 6 widget for usb-superposition. Reads the state the `usbinspect daemon`
// publishes to $XDG_RUNTIME_DIR/usbinspect/state.json, the same file and the same
// view model (Model.js) as the Omarchy bar widget. Nothing here touches sysfs.
PlasmoidItem {
  id: root

  // The only two commands this widget runs. Both are fixed strings: no state values
  // ever reach a shell.
  readonly property string readCmd: "cat -- \"${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/usbinspect/state.json\""
  readonly property string refreshCmd: "systemctl --user reload usbinspect.service"

  readonly property bool showInternal: Plasmoid.configuration.showInternal === true
  property var doc: null
  property bool missing: true
  property bool refreshing: false
  readonly property var view: Model.buildView(root.doc, root.showInternal, Strings.t)

  function t(key) { return Strings.t(key) }

  function load(text, ok) {
    var parsed = ok ? Model.parseState(text) : null
    // Keep the last good state if a read races the daemon's rename.
    if (parsed) root.doc = parsed
    else if (!ok) root.doc = null
    root.missing = root.doc === null
  }

  function reload() {
    if (exec.connectedSources.indexOf(root.readCmd) < 0) exec.connectSource(root.readCmd)
  }

  // Ask the daemon to re-scan now (SIGHUP via the unit's ExecReload); it republishes
  // state.json, which the next read picks up.
  function refresh() {
    if (root.refreshing) return
    root.refreshing = true
    exec.connectSource(root.refreshCmd)
  }

  function toggleInternal() {
    Plasmoid.configuration.showInternal = !root.showInternal
  }

  P5Support.DataSource {
    id: exec
    engine: "executable"
    connectedSources: []
    onNewData: function(source, data) {
      exec.disconnectSource(source)
      if (source === root.readCmd) {
        root.load(data["stdout"], data["exit code"] === 0)
      } else if (source === root.refreshCmd) {
        settle.restart()
      }
    }
  }

  // Keep the refresh button busy until the daemon's re-scan has had time to land.
  Timer {
    id: settle
    interval: 800
    onTriggered: { root.refreshing = false; root.reload() }
  }

  // state.json is ~20 kB and `cat` is cheap; poll faster only while the popup is open.
  Timer {
    interval: root.expanded ? 2000 : 10000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.reload()
  }

  onExpandedChanged: if (expanded) reload()

  Plasmoid.icon: "drive-removable-media-usb-symbolic"
  Plasmoid.status: {
    if (root.missing || !root.view) return PlasmaCore.Types.PassiveStatus
    if (root.view.warning) return PlasmaCore.Types.NeedsAttentionStatus
    if (root.view.charging !== "" || root.view.externalDevices > 0) return PlasmaCore.Types.ActiveStatus
    return PlasmaCore.Types.PassiveStatus
  }

  toolTipMainText: root.t("panel.title")
  toolTipSubText: root.missing ? root.t("tooltip.noDaemon") : (root.view ? root.view.tooltip : "")
  toolTipTextFormat: Text.PlainText

  Plasmoid.contextualActions: [
    PlasmaCore.Action {
      text: root.t("panel.refresh")
      icon.name: "view-refresh-symbolic"
      enabled: !root.refreshing
      onTriggered: root.refresh()
    },
    PlasmaCore.Action {
      text: root.t("panel.showInternalAction")
      checkable: true
      checked: root.showInternal
      onTriggered: root.toggleInternal()
    }
  ]

  compactRepresentation: CompactRepresentation {}
  fullRepresentation: FullRepresentation {}
}

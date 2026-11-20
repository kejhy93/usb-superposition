import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents3
import org.kde.plasma.extras as PlasmaExtras

// Popup: charging summary, issues, one card per USB-C port, device trees per bay (or
// per kernel root port before calibration) with negotiated / max speed bars, and the
// daemon's recent events. Presentation only; the view model comes from main.qml.
// Every label is PlainText: device names come from USB descriptors and are untrusted.
PlasmaExtras.Representation {
  id: full

  readonly property var view: root.view
  readonly property bool missing: root.missing

  Layout.minimumWidth: Kirigami.Units.gridUnit * 22
  Layout.preferredWidth: Kirigami.Units.gridUnit * 26
  Layout.minimumHeight: Kirigami.Units.gridUnit * 14
  Layout.preferredHeight: Kirigami.Units.gridUnit * 30

  collapseMarginsHint: true

  function t(key) { return root.t(key) }

  function timeText(ms) {
    if (!ms) return ""
    var d = new Date(ms)
    var sameDay = d.toDateString() === new Date().toDateString()
    return d.toLocaleString(Qt.locale(), sameDay ? "HH:mm" : "ddd HH:mm")
  }

  component PlainLabel: PlasmaComponents3.Label {
    textFormat: Text.PlainText
    elide: Text.ElideRight
    Layout.fillWidth: true
  }
  component Dim: PlainLabel { opacity: 0.7 }
  component Faint: PlainLabel { opacity: 0.5; font: Kirigami.Theme.smallFont }
  component Section: Kirigami.Heading {
    textFormat: Text.PlainText
    level: 5
    opacity: 0.7
    Layout.fillWidth: true
    Layout.topMargin: Kirigami.Units.smallSpacing
  }

  header: PlasmaExtras.PlasmoidHeading {
    RowLayout {
      anchors.fill: parent
      anchors.leftMargin: Kirigami.Units.smallSpacing
      spacing: Kirigami.Units.smallSpacing

      ColumnLayout {
        Layout.fillWidth: true
        spacing: 0
        Kirigami.Heading {
          textFormat: Text.PlainText
          level: 3
          text: full.t("panel.title")
          Layout.fillWidth: true
        }
        PlainLabel {
          text: {
            if (full.missing) return full.t("panel.noDaemon")
            if (!full.view) return ""
            if (full.view.charging !== "")
              return full.t("panel.charging").arg(full.view.chargingW).arg(full.view.chargingWhere)
            return full.t("panel.notCharging")
          }
          color: full.view && full.view.charging !== "" ? Kirigami.Theme.positiveTextColor : Kirigami.Theme.disabledTextColor
          font: Kirigami.Theme.smallFont
        }
      }

      PlasmaComponents3.Switch {
        visible: !full.missing
        text: full.t("panel.showInternal")
        checked: root.showInternal
        onToggled: root.toggleInternal()
      }

      PlasmaComponents3.ToolButton {
        icon.name: "view-refresh-symbolic"
        enabled: !root.refreshing
        display: PlasmaComponents3.AbstractButton.IconOnly
        text: full.t("panel.refresh")
        PlasmaComponents3.ToolTip.text: text
        PlasmaComponents3.ToolTip.visible: hovered
        PlasmaComponents3.ToolTip.delay: Kirigami.Units.toolTipDelay
        onClicked: root.refresh()
      }
    }
  }

  PlasmaComponents3.ScrollView {
    id: scroll
    anchors.fill: parent
    PlasmaComponents3.ScrollBar.horizontal.policy: PlasmaComponents3.ScrollBar.AlwaysOff
    contentWidth: availableWidth

    ColumnLayout {
      id: body
      width: scroll.availableWidth
      spacing: Kirigami.Units.smallSpacing

      // ---------- Daemon not running ----------
      ColumnLayout {
        visible: full.missing
        Layout.fillWidth: true
        Layout.margins: Kirigami.Units.largeSpacing
        PlainLabel {
          text: full.t("panel.noDaemonHelp")
          wrapMode: Text.Wrap
        }
        PlainLabel {
          text: "usbinspect service install"
          font.family: "monospace"
        }
      }

      ColumnLayout {
        visible: !!full.view
        Layout.fillWidth: true
        Layout.margins: Kirigami.Units.largeSpacing
        Layout.topMargin: Kirigami.Units.smallSpacing
        spacing: Kirigami.Units.smallSpacing

        // ---------- Issues ----------
        Section {
          visible: !!full.view && full.view.issues.length > 0
          text: full.t("section.issues")
        }
        Repeater {
          model: full.view ? full.view.issues : []
          PlainLabel {
            required property var modelData
            text: (modelData.warning ? "⚠ " : "ℹ ") + modelData.message
            color: modelData.warning ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.textColor
            wrapMode: Text.Wrap
          }
        }

        // ---------- USB-C ports ----------
        Section {
          visible: !!full.view && full.view.ports.length > 0
          text: full.t("section.ports")
        }
        Repeater {
          model: full.view ? full.view.ports : []

          ColumnLayout {
            id: port
            required property var modelData
            Layout.fillWidth: true
            spacing: 0

            RowLayout {
              Layout.fillWidth: true
              PlainLabel {
                text: port.modelData.label
                font.bold: port.modelData.connected
                opacity: port.modelData.connected ? 1 : 0.7
                Layout.fillWidth: false
              }
              Faint {
                text: port.modelData.sublabel
                visible: text !== ""
              }
              Item { Layout.fillWidth: true }
              Faint {
                text: port.modelData.roles
                Layout.fillWidth: false
              }
            }
            Faint {
              visible: !port.modelData.connected
              text: full.t("port.empty")
              leftPadding: Kirigami.Units.gridUnit
            }
            PlainLabel {
              visible: port.modelData.contract !== ""
              text: full.t("port.contract").arg(port.modelData.contract)
              color: Kirigami.Theme.positiveTextColor
              leftPadding: Kirigami.Units.gridUnit
            }
            Dim {
              visible: port.modelData.offers !== ""
              text: full.t("port.offers").arg(port.modelData.offers)
              leftPadding: Kirigami.Units.gridUnit
            }
            Dim {
              visible: port.modelData.altmodes.length > 0
              text: {
                var parts = []
                var modes = port.modelData.altmodes
                for (var i = 0; i < modes.length; i++) parts.push(modes[i].name + (modes[i].active ? " ●" : " ○"))
                return full.t("port.altmodes").arg(parts.join(", "))
              }
              leftPadding: Kirigami.Units.gridUnit
            }
            Dim {
              visible: port.modelData.cable !== ""
              text: full.t("port.cable").arg(port.modelData.cable)
              leftPadding: Kirigami.Units.gridUnit
            }
            Faint {
              visible: port.modelData.connected && port.modelData.contract === "" && port.modelData.offers === ""
                && port.modelData.altmodes.length === 0
              text: full.t("port.partnerOnly")
              leftPadding: Kirigami.Units.gridUnit
            }
          }
        }

        // ---------- Bays (or kernel root ports) with device trees ----------
        RowLayout {
          Layout.fillWidth: true
          Section {
            text: full.view && full.view.mapped ? full.t("section.bays") : full.t("section.usb")
          }
          Faint {
            text: full.t("panel.speedLegend")
            horizontalAlignment: Text.AlignRight
            Layout.fillWidth: false
          }
        }
        Faint {
          visible: !!full.view && !full.view.mapped
          text: full.t("panel.calibrateHint")
          wrapMode: Text.Wrap
        }
        Faint {
          visible: !!full.view && full.view.groups.length === 0
          text: full.t("panel.noDevices")
        }

        Repeater {
          model: full.view ? full.view.groups : []

          ColumnLayout {
            id: group
            required property var modelData
            Layout.fillWidth: true
            spacing: Kirigami.Units.smallSpacing / 2

            RowLayout {
              Layout.fillWidth: true
              PlainLabel {
                text: group.modelData.title
                font.bold: true
                Layout.fillWidth: false
              }
              Faint { text: group.modelData.subtitle }
              PlainLabel {
                text: group.modelData.watts !== "" ? "⚡ " + group.modelData.watts : ""
                color: Kirigami.Theme.positiveTextColor
                horizontalAlignment: Text.AlignRight
                Layout.fillWidth: false
              }
            }
            Faint {
              visible: group.modelData.rows.length === 0
              text: full.t("group.empty")
              leftPadding: Kirigami.Units.gridUnit
            }

            Repeater {
              model: group.modelData.rows
              DeviceRow {
                Layout.fillWidth: true
                t: full.t
              }
            }
          }
        }

        // ---------- Recent events ----------
        Section {
          visible: !!full.view && full.view.events.length > 0
          text: full.t("section.events")
        }
        Repeater {
          model: full.view ? full.view.events : []
          RowLayout {
            id: event
            required property var modelData
            Layout.fillWidth: true
            Faint {
              text: full.timeText(event.modelData.time)
              Layout.fillWidth: false
              Layout.preferredWidth: Kirigami.Units.gridUnit * 3
            }
            PlainLabel {
              text: event.modelData.message
              color: event.modelData.warning ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.textColor
            }
          }
        }

        Faint {
          Layout.topMargin: Kirigami.Units.smallSpacing
          horizontalAlignment: Text.AlignRight
          text: {
            if (!full.view) return ""
            var bits = []
            if (full.view.updated)
              bits.push(full.t("panel.updated").arg(new Date(full.view.updated).toLocaleString(Qt.locale(), "HH:mm:ss")))
            if (full.view.machine !== "") bits.push(full.view.machine)
            return bits.join(" · ")
          }
        }
      }
    }
  }
}

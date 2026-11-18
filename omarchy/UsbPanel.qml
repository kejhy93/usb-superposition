import QtQuick
import Quickshell
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// Popup for the usb-superposition bar widget. Presentation only: the view model is
// built in BarWidget.qml (from Model.js) and read here through `widget.view`.
// Every Text is PlainText: device names come from USB descriptors and are untrusted.
Panel {
  id: root
  moduleName: "usb.superposition"
  ipcTarget: "usb.superposition"

  property var anchorItem: null
  property var widget: null
  readonly property var view: widget ? widget.view : null
  readonly property bool missing: widget ? widget.missing : true
  readonly property bool showInternal: widget ? widget.showInternal : false

  readonly property color fg: root.bar ? root.bar.foreground : Commons.Color.foreground
  readonly property color dim: Qt.darker(fg, 1.4)
  readonly property color faint: Qt.darker(fg, 1.8)
  readonly property color warn: root.bar ? root.bar.urgent : Commons.Color.urgent
  readonly property color good: Commons.Color.accent
  readonly property string family: root.bar ? root.bar.fontFamily : Style.font.family
  readonly property int maxPanelHeight: popup.screen ? Math.max(Style.space(280), popup.screen.height - Style.gapsOut * 4) : Style.space(640)

  Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }

  function timeText(ms) {
    if (!ms) return ""
    var d = new Date(ms)
    var today = new Date()
    var sameDay = d.toDateString() === today.toDateString()
    return d.toLocaleString(Qt.locale(Commons.I18n.formatLocale), sameDay ? "HH:mm" : "ddd HH:mm")
  }

  function scrollBy(dy) {
    var step = Style.space(48) * dy
    flick.contentY = Math.max(0, Math.min(flick.contentHeight - flick.height, flick.contentY + step))
  }

  onOpenedChanged: if (opened && widget) widget.reload()

  component Label: Text {
    textFormat: Text.PlainText
    color: root.fg
    font.family: root.family
    font.pixelSize: Style.font.bodySmall
    elide: Text.ElideRight
  }

  KeyboardPanel {
    id: popup
    anchorItem: root.anchorItem
    owner: root
    bar: root.bar
    open: root.opened
    centerOnBar: false
    focusTarget: keyCatcher
    contentWidth: popup.fittedContentWidth(Style.space(460))
    contentHeight: popup.fittedContentHeight(Math.min(column.implicitHeight, root.maxPanelHeight))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onMoveRequested: function(dx, dy) { if (dy !== 0) root.scrollBy(dy) }
      onActivateRequested: if (root.widget) root.widget.toggleInternal()

      Flickable {
        id: flick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height

        Column {
          id: column
          width: parent.width
          spacing: Style.space(14)

          // ---------- Hero: glyph · title / charging line · internal toggle ----------
          Item {
            width: parent.width
            implicitHeight: Math.max(heroIcon.implicitHeight, heroLabels.implicitHeight, internalToggle.implicitHeight)

            Text {
              id: heroIcon
              textFormat: Text.PlainText
              text: root.widget ? root.widget.glyph : ""
              color: root.view && root.view.warning ? root.warn : root.fg
              font.family: root.family
              font.pixelSize: Style.font.display
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
            }

            Column {
              id: heroLabels
              anchors.left: heroIcon.right
              anchors.leftMargin: Style.space(14)
              anchors.right: internalToggle.left
              anchors.rightMargin: Style.space(10)
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(2)

              Label {
                width: parent.width
                text: strings.t("panel.title")
                font.pixelSize: Style.font.title
                font.bold: true
              }
              Label {
                width: parent.width
                text: {
                  if (root.missing) return strings.t("panel.noDaemon").toUpperCase()
                  if (!root.view) return ""
                  if (root.view.charging !== "")
                    return strings.t("panel.charging").arg(root.view.chargingW).arg(root.view.chargingWhere).toUpperCase()
                  return strings.t("panel.notCharging").toUpperCase()
                }
                color: root.view && root.view.charging !== "" ? root.good : root.dim
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 1.2
              }
            }

            Row {
              id: internalToggle
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(8)
              visible: !root.missing

              Label {
                anchors.verticalCenter: parent.verticalCenter
                text: strings.t("panel.showInternal")
                color: root.dim
                font.pixelSize: Style.font.caption
              }
              ToggleSwitch {
                anchors.verticalCenter: parent.verticalCenter
                checked: root.showInternal
                foreground: root.fg
                onToggled: if (root.widget) root.widget.toggleInternal()
              }
            }
          }

          // ---------- Daemon not running ----------
          Column {
            width: parent.width
            spacing: Style.space(6)
            visible: root.missing

            Label {
              width: parent.width
              text: strings.t("panel.noDaemonHelp")
              color: root.dim
              wrapMode: Text.Wrap
            }
            Label {
              width: parent.width
              text: "usbinspect service install"
              font.bold: true
            }
          }

          // ---------- Issues ----------
          Column {
            width: parent.width
            spacing: Style.space(6)
            visible: !!root.view && root.view.issues.length > 0

            PanelSeparator { width: parent.width; foreground: root.fg }
            PanelSectionHeader { text: strings.t("section.issues"); foreground: root.fg; fontFamily: root.family }
            Repeater {
              model: root.view ? root.view.issues : []
              Label {
                required property var modelData
                width: parent.width
                text: (modelData.warning ? "⚠ " : "ℹ ") + modelData.message
                color: modelData.warning ? root.warn : root.fg
                wrapMode: Text.Wrap
              }
            }
          }

          // ---------- USB-C ports ----------
          Column {
            width: parent.width
            spacing: Style.space(10)
            visible: !!root.view && root.view.ports.length > 0

            PanelSeparator { width: parent.width; foreground: root.fg }
            PanelSectionHeader { text: strings.t("section.ports"); foreground: root.fg; fontFamily: root.family }

            Repeater {
              model: root.view ? root.view.ports : []

              Column {
                id: portCard
                required property var modelData
                width: parent.width
                spacing: Style.space(2)

                Item {
                  width: parent.width
                  implicitHeight: portName.implicitHeight

                  Label {
                    id: portName
                    anchors.left: parent.left
                    anchors.right: portRoles.left
                    anchors.rightMargin: Style.space(8)
                    text: portCard.modelData.label + (portCard.modelData.sublabel !== "" ? "  " + portCard.modelData.sublabel : "")
                    font.pixelSize: Style.font.body
                    font.bold: portCard.modelData.connected
                    color: portCard.modelData.connected ? root.fg : root.dim
                  }
                  Label {
                    id: portRoles
                    anchors.right: parent.right
                    text: portCard.modelData.roles
                    color: root.faint
                    font.pixelSize: Style.font.caption
                  }
                }

                Label {
                  visible: !portCard.modelData.connected
                  text: strings.t("port.empty")
                  color: root.faint
                  leftPadding: Style.space(12)
                }
                Label {
                  visible: portCard.modelData.contract !== ""
                  width: parent.width
                  text: strings.t("port.contract").arg(portCard.modelData.contract)
                  color: root.good
                  leftPadding: Style.space(12)
                }
                Label {
                  visible: portCard.modelData.offers !== ""
                  width: parent.width
                  text: strings.t("port.offers").arg(portCard.modelData.offers)
                  color: root.dim
                  leftPadding: Style.space(12)
                }
                Label {
                  visible: portCard.modelData.altmodes.length > 0
                  width: parent.width
                  text: {
                    var parts = []
                    var modes = portCard.modelData.altmodes
                    for (var i = 0; i < modes.length; i++) parts.push(modes[i].name + (modes[i].active ? " ●" : " ○"))
                    return strings.t("port.altmodes").arg(parts.join(", "))
                  }
                  color: root.dim
                  leftPadding: Style.space(12)
                }
                Label {
                  visible: portCard.modelData.cable !== ""
                  width: parent.width
                  text: strings.t("port.cable").arg(portCard.modelData.cable)
                  color: root.dim
                  leftPadding: Style.space(12)
                }
                Label {
                  visible: portCard.modelData.connected && portCard.modelData.contract === "" && portCard.modelData.offers === ""
                    && portCard.modelData.altmodes.length === 0
                  text: strings.t("port.partnerOnly")
                  color: root.faint
                  leftPadding: Style.space(12)
                }
              }
            }
          }

          // ---------- Bays (or kernel root ports) with device trees ----------
          Column {
            width: parent.width
            spacing: Style.space(10)
            visible: !!root.view

            PanelSeparator { width: parent.width; foreground: root.fg }
            Item {
              width: parent.width
              implicitHeight: devicesHeader.implicitHeight
              PanelSectionHeader {
                id: devicesHeader
                text: root.view && root.view.mapped ? strings.t("section.bays") : strings.t("section.usb")
                foreground: root.fg
                fontFamily: root.family
              }
              Label {
                anchors.right: parent.right
                anchors.verticalCenter: devicesHeader.verticalCenter
                text: strings.t("panel.speedLegend")
                color: root.faint
                font.pixelSize: Style.font.caption
              }
            }
            Label {
              visible: !!root.view && !root.view.mapped
              width: parent.width
              text: strings.t("panel.calibrateHint")
              color: root.faint
              font.pixelSize: Style.font.caption
              wrapMode: Text.Wrap
            }
            Label {
              visible: !!root.view && root.view.groups.length === 0
              text: strings.t("panel.noDevices")
              color: root.faint
            }

            Repeater {
              model: root.view ? root.view.groups : []

              Column {
                id: groupCol
                required property var modelData
                width: parent.width
                spacing: Style.space(4)

                Item {
                  width: parent.width
                  implicitHeight: groupTitle.implicitHeight
                  Label {
                    id: groupTitle
                    anchors.left: parent.left
                    anchors.right: groupWatts.left
                    anchors.rightMargin: Style.space(8)
                    text: groupCol.modelData.title + (groupCol.modelData.subtitle !== "" ? "  " + groupCol.modelData.subtitle : "")
                    font.pixelSize: Style.font.body
                    font.bold: true
                  }
                  Label {
                    id: groupWatts
                    anchors.right: parent.right
                    text: groupCol.modelData.watts !== "" ? "⚡ " + groupCol.modelData.watts : ""
                    color: root.good
                  }
                }

                Label {
                  visible: groupCol.modelData.rows.length === 0
                  text: strings.t("group.empty")
                  color: root.faint
                  leftPadding: Style.space(12)
                }

                Repeater {
                  model: groupCol.modelData.rows

                  Column {
                    id: deviceRow
                    required property var modelData
                    width: parent.width
                    spacing: 0
                    readonly property real indent: Style.space(12) + modelData.depth * Style.space(14)

                    Item {
                      width: parent.width
                      implicitHeight: deviceName.implicitHeight

                      Label {
                        id: deviceName
                        x: deviceRow.indent
                        width: Math.max(0, deviceSpeed.x - x - Style.space(8))
                        text: (deviceRow.modelData.depth > 0 ? "└ " : "") + deviceRow.modelData.name
                        color: deviceRow.modelData.internal ? root.dim : root.fg
                      }
                      Label {
                        id: deviceSpeed
                        anchors.right: parent.right
                        text: deviceRow.modelData.speed + (deviceRow.modelData.maxSpeed !== "" ? " / " + deviceRow.modelData.maxSpeed : "")
                        color: deviceRow.modelData.downgraded ? root.warn : root.dim
                        font.bold: deviceRow.modelData.downgraded
                      }
                    }
                    Label {
                      x: deviceRow.indent + Style.space(14)
                      width: parent.width - x
                      visible: text !== ""
                      text: {
                        var bits = []
                        if (deviceRow.modelData.tags !== "") bits.push(deviceRow.modelData.tags)
                        if (deviceRow.modelData.ids !== "") bits.push(deviceRow.modelData.ids)
                        return bits.join(" · ")
                      }
                      color: root.faint
                      font.pixelSize: Style.font.caption
                    }
                    Label {
                      x: deviceRow.indent + Style.space(14)
                      width: parent.width - x
                      visible: deviceRow.modelData.reason !== ""
                      text: "⚠ " + deviceRow.modelData.reason
                      color: root.warn
                      font.pixelSize: Style.font.caption
                      wrapMode: Text.Wrap
                    }
                    Repeater {
                      model: deviceRow.modelData.modes
                      Label {
                        required property var modelData
                        x: deviceRow.indent + Style.space(14)
                        width: deviceRow.width - x
                        text: strings.t("device.altmode").arg(modelData.name).arg(modelData.state)
                        color: modelData.ok ? root.good : root.warn
                        font.pixelSize: Style.font.caption
                      }
                    }
                  }
                }
              }
            }
          }

          // ---------- Recent events ----------
          Column {
            width: parent.width
            spacing: Style.space(4)
            visible: !!root.view && root.view.events.length > 0

            PanelSeparator { width: parent.width; foreground: root.fg }
            PanelSectionHeader { text: strings.t("section.events"); foreground: root.fg; fontFamily: root.family }

            Repeater {
              model: root.view ? root.view.events : []
              Item {
                id: eventRow
                required property var modelData
                width: parent.width
                implicitHeight: Math.max(eventTime.implicitHeight, eventMessage.implicitHeight)

                Label {
                  id: eventTime
                  width: Style.space(78)
                  text: root.timeText(eventRow.modelData.time)
                  color: root.faint
                  font.pixelSize: Style.font.caption
                  anchors.verticalCenter: eventMessage.verticalCenter
                }
                Label {
                  id: eventMessage
                  anchors.left: eventTime.right
                  anchors.right: parent.right
                  text: eventRow.modelData.message
                  color: eventRow.modelData.warning ? root.warn : root.fg
                }
              }
            }
          }

          Label {
            visible: !!root.view && root.view.machine !== ""
            width: parent.width
            text: root.view ? root.view.machine : ""
            color: root.faint
            font.pixelSize: Style.font.caption
            horizontalAlignment: Text.AlignRight
          }
        }
      }
    }
  }
}

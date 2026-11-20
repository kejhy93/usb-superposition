import QtQuick
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents3
import org.kde.plasma.plasmoid

// Panel icon: USB glyph (red while the daemon reports a warning) with a badge that
// shows the charging watts, or else the external device count. Middle click re-scans.
MouseArea {
  id: compact

  readonly property var view: root.view
  readonly property string badgeText: view ? view.badge : ""
  property bool wasExpanded: false

  hoverEnabled: true
  acceptedButtons: Qt.LeftButton | Qt.MiddleButton
  onPressed: function(mouse) { compact.wasExpanded = root.expanded }
  onClicked: function(mouse) {
    if (mouse.button === Qt.MiddleButton) root.refresh()
    else root.expanded = !compact.wasExpanded
  }

  Kirigami.Icon {
    id: icon
    anchors.fill: parent
    source: Plasmoid.icon
    active: compact.containsMouse
    isMask: true
    color: compact.view && compact.view.warning ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.textColor
    opacity: root.missing ? 0.5 : 1
  }

  Rectangle {
    id: badge
    visible: compact.badgeText !== ""
    anchors.right: parent.right
    anchors.bottom: parent.bottom
    width: Math.max(height, badgeLabel.implicitWidth + Kirigami.Units.smallSpacing)
    height: Math.max(badgeLabel.implicitHeight, Math.round(compact.height * 0.45))
    radius: height / 2
    color: Kirigami.Theme.backgroundColor
    border.width: 1
    border.color: compact.view && compact.view.charging !== "" ? Kirigami.Theme.positiveTextColor : Kirigami.Theme.disabledTextColor

    PlasmaComponents3.Label {
      id: badgeLabel
      anchors.centerIn: parent
      textFormat: Text.PlainText
      text: compact.badgeText
      font.pixelSize: Math.max(8, Math.round(Math.min(compact.height * 0.32, compact.width * 0.3)))
      font.bold: true
      color: compact.view && compact.view.charging !== "" ? Kirigami.Theme.positiveTextColor : Kirigami.Theme.textColor
    }
  }
}

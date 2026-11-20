import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents3

// One row of a flattened device tree (Model.js deviceRow): name, a negotiated / max
// speed bar, tags and ids, the downgrade reason and Billboard alt mode results.
ColumnLayout {
  id: row

  required property var modelData
  property var t: function(key) { return key }
  readonly property real indent: Kirigami.Units.gridUnit * (0.5 + modelData.depth * 0.8)
  readonly property color speedColor: modelData.downgraded ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.textColor

  spacing: 0

  component PlainLabel: PlasmaComponents3.Label {
    textFormat: Text.PlainText
    elide: Text.ElideRight
  }

  RowLayout {
    Layout.fillWidth: true
    Layout.leftMargin: row.indent
    spacing: Kirigami.Units.smallSpacing

    PlainLabel {
      text: (row.modelData.depth > 0 ? "└ " : "") + row.modelData.name
      opacity: row.modelData.internal ? 0.7 : 1
      Layout.fillWidth: true
    }

    // negotiated / max: full when the link runs at the device's best, short and red
    // when it was downgraded. Hidden when there's nothing to compare against.
    Rectangle {
      visible: row.modelData.ratio >= 0
      Layout.preferredWidth: Kirigami.Units.gridUnit * 3
      Layout.preferredHeight: Math.round(Kirigami.Units.smallSpacing * 1.5)
      Layout.alignment: Qt.AlignVCenter
      radius: height / 2
      color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.15)

      Rectangle {
        width: Math.max(parent.height, parent.width * Math.max(0, row.modelData.ratio))
        height: parent.height
        radius: parent.radius
        color: row.modelData.downgraded ? Kirigami.Theme.negativeTextColor : Kirigami.Theme.positiveTextColor
      }
    }

    PlainLabel {
      text: row.modelData.speed + (row.modelData.maxSpeed !== "" ? " / " + row.modelData.maxSpeed : "")
      color: row.speedColor
      opacity: row.modelData.downgraded ? 1 : 0.7
      font.bold: row.modelData.downgraded
      horizontalAlignment: Text.AlignRight
    }
  }

  PlainLabel {
    readonly property string detail: {
      var bits = []
      if (row.modelData.tags !== "") bits.push(row.modelData.tags)
      if (row.modelData.ids !== "") bits.push(row.modelData.ids)
      return bits.join(" · ")
    }
    visible: detail !== ""
    text: detail
    opacity: 0.5
    font: Kirigami.Theme.smallFont
    Layout.fillWidth: true
    Layout.leftMargin: row.indent + Kirigami.Units.gridUnit
  }

  PlainLabel {
    visible: row.modelData.reason !== ""
    text: "⚠ " + row.modelData.reason
    color: Kirigami.Theme.negativeTextColor
    font: Kirigami.Theme.smallFont
    wrapMode: Text.Wrap
    Layout.fillWidth: true
    Layout.leftMargin: row.indent + Kirigami.Units.gridUnit
  }

  Repeater {
    model: row.modelData.modes
    PlainLabel {
      required property var modelData
      text: row.t("device.altmode").arg(modelData.name).arg(modelData.state)
      color: modelData.ok ? Kirigami.Theme.positiveTextColor : Kirigami.Theme.negativeTextColor
      font: Kirigami.Theme.smallFont
      Layout.fillWidth: true
      Layout.leftMargin: row.indent + Kirigami.Units.gridUnit
    }
  }
}

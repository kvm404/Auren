// A segmented control: one of a few choices. options: [{ value, label }]; stretch: the parts share the width.
import QtQuick

Rectangle {
  id: seg
  property var options: []
  property string value: ""
  property bool stretch: false
  signal picked(string value)
  readonly property real part: stretch ? (width - 4) / Math.max(1, options.length) : 0
  implicitWidth: row.implicitWidth + 4
  implicitHeight: 26
  radius: 7
  color: Theme.control
  opacity: enabled ? 1 : 0.45
  Row {
    id: row
    x: 2; y: 2
    Repeater {
      model: seg.options
      Rectangle {
        id: part
        required property var modelData
        required property int index
        readonly property bool chosen: String(modelData.value) === seg.value
        width: seg.stretch ? seg.part : t.implicitWidth + 24
        height: 22
        radius: 5
        color: chosen ? Theme.segment : ma.pressed ? Theme.pressed : "transparent"
        border.width: chosen && !Theme.dark ? 0.5 : 0
        border.color: "#d1d1d6"
        Rectangle {                  // the line between two parts that aren't chosen
          visible: part.index > 0 && !part.chosen && String(seg.options[part.index - 1].value) !== seg.value
          width: 1; height: 12; anchors.verticalCenter: parent.verticalCenter; color: Theme.faint; opacity: 0.6
        }
        Text { id: t; anchors.centerIn: parent; text: part.modelData.label; color: Theme.text
               font.pixelSize: 12; font.weight: part.chosen ? Font.DemiBold : Font.Normal; elide: Text.ElideRight
               width: Math.min(implicitWidth, part.width - 8); horizontalAlignment: Text.AlignHCenter }
        MouseArea { id: ma; anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                    onClicked: seg.picked(String(part.modelData.value)) }
      }
    }
  }
}

import QtQuick
import qs.Commons
import qs.Ui

// A label with its value on the right, and a slider under it.
Column {
  id: row

  property QtObject bar: null
  property string label: ""
  property string valueText: ""
  property real value: 0
  property real minimum: 0
  property real maximum: 1
  property real step: 0.05
  property bool hasCursor: false
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family
  readonly property real liveValue: slider.liveValue

  signal moved(real value)
  signal released(real value)
  signal hovered()

  spacing: Style.space(2)
  opacity: enabled ? 1 : 0.5

  Item {
    width: parent.width
    height: labelText.implicitHeight
    Text {
      id: labelText
      text: (row.hasCursor ? "› " : "") + row.label
      color: row.hasCursor ? row.foreground : Qt.darker(row.foreground, 1.2)
      font.family: row.fontFamily
      font.pixelSize: Style.font.bodySmall
      font.bold: row.hasCursor
    }
    Text {
      anchors.right: parent.right
      text: row.valueText
      color: Qt.darker(row.foreground, 1.45)
      font.family: row.fontFamily
      font.pixelSize: Style.font.bodySmall
    }
  }

  PanelSlider {
    id: slider
    bar: row.bar
    width: parent.width
    value: row.value
    minimum: row.minimum
    maximum: row.maximum
    step: row.step
    onMoved: function(v) { row.moved(v) }
    onReleased: function(v) { row.released(v) }
  }

  HoverHandler { onHoveredChanged: if (hovered) row.hovered() }
}

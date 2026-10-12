import QtQuick
import qs.Commons
import qs.Ui

// A small-caps title with the value on the right, and a slider under it (Omarchy's settings style).
// With the keyboard cursor on it, a frame shows and left/right change it (the panel does that).
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

  spacing: Style.space(6)
  opacity: enabled ? 1 : 0.5

  Item {
    width: parent.width
    implicitHeight: header.implicitHeight
    PanelSectionHeader {
      id: header
      text: (row.hasCursor ? "› " : "") + row.label.toUpperCase()
      foreground: row.foreground
      fontFamily: row.fontFamily
    }
    Text {
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      text: row.valueText
      color: row.foreground
      font.family: row.fontFamily
      font.pixelSize: Style.font.bodySmall
      font.bold: true
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
    Rectangle {
      anchors.fill: parent
      anchors.margins: -2
      visible: row.hasCursor
      color: "transparent"
      border.color: row.foreground
      radius: Style.cornerRadius
    }
  }

  HoverHandler { onHoveredChanged: if (hovered) row.hovered() }
}

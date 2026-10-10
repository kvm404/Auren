import QtQuick
import qs.Commons
import qs.Ui

// A row of equal-width buttons with one selected (a segmented control).
//   options: [{ value, label, icon?, tip? }]
Row {
  id: choice

  property var options: []
  property string value: ""
  property int cursorIndex: -1
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family
  property bool iconsOnly: false

  signal picked(string value, int index)
  signal hoveredIndex(int index)

  spacing: Style.space(6)
  readonly property real cell: options.length ? (width - spacing * (options.length - 1)) / options.length : width

  Repeater {
    model: choice.options
    Button {
      required property var modelData
      required property int index
      width: choice.cell
      text: choice.iconsOnly ? "" : (modelData.label || "")
      iconText: modelData.icon || ""
      tooltipText: modelData.tip || ""
      fontSize: Style.font.bodySmall
      iconSize: Style.font.title
      foreground: choice.foreground
      fontFamily: choice.fontFamily
      bordered: true
      selected: String(modelData.value) === choice.value
      hasCursor: choice.cursorIndex === index
      onClicked: choice.picked(String(modelData.value), index)
      onHovered: function(h) { if (h) choice.hoveredIndex(index) }
    }
  }
}

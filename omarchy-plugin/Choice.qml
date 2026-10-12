import QtQuick
import qs.Commons
import qs.Ui

// A setting picked from a few choices, as in Omarchy's settings panels: a small-caps title, then the
// choices as buttons that wrap, the chosen one filled. Also a plain row of action buttons (no value).
//   options: [{ value, label, icon?, tip?, enabled?, on? (shown as chosen: a switch that's on) }]
Column {
  id: choice

  property string title: ""
  property var options: []
  property string value: ""
  property int cursorIndex: -1
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family

  signal picked(string value, int index)
  signal hoveredIndex(int index)

  spacing: Style.space(8)
  opacity: enabled ? 1 : 0.5

  PanelSectionHeader {
    visible: choice.title !== ""
    text: (choice.cursorIndex >= 0 ? "› " : "") + choice.title
    foreground: choice.foreground
    fontFamily: choice.fontFamily
  }

  Flow {
    width: parent.width
    spacing: Style.space(6)
    Repeater {
      model: choice.options
      Button {
        required property var modelData
        required property int index
        text: modelData.label || ""
        iconText: modelData.icon || ""
        tooltipText: modelData.tip || ""
        fontSize: Style.font.bodySmall
        iconSize: Style.font.body
        foreground: choice.foreground
        fontFamily: choice.fontFamily
        bordered: true
        enabled: modelData.enabled !== false
        opacity: enabled ? 1 : 0.5
        selected: modelData.on === true || (choice.value !== "" && String(modelData.value) === choice.value)
        hasCursor: choice.cursorIndex === index
        onClicked: choice.picked(String(modelData.value), index)
        onHovered: function(h) { if (h) choice.hoveredIndex(index) }
      }
    }
  }
}

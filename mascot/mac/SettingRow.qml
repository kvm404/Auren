// One row of a Group: its name and a line about it on the left, its control on the right. `last`: no line
// under it. `below`: more under the text (a field, a slider). `lead`: a mark or a picture before the text,
// `indent` wide.
import QtQuick

Item {
  id: row
  property string title
  property string detail
  property color detailColor: Theme.dim
  property bool last: false
  property real indent: 0                       // room on the left for a mark or a picture
  property bool big: false                      // the title larger: a page's main row
  default property alias control: slot.data
  property alias below: under.data
  property alias lead: leftSlot.data
  width: parent ? parent.width : 0
  implicitHeight: Math.max(44, texts.implicitHeight + 22, slot.height + 16)
  Item { id: leftSlot; x: 14; width: row.indent; height: parent.height }
  Column {
    id: texts
    x: 14 + row.indent + (row.indent > 0 ? 12 : 0)
    anchors.verticalCenter: parent.verticalCenter
    width: parent.width - x - (slot.width > 0 ? slot.width + 26 : 14)
    spacing: 2
    Text { width: parent.width; text: row.title; color: Theme.text; visible: text !== ""; wrapMode: Text.WordWrap
           font.pixelSize: row.big ? 15 : Theme.body; font.weight: row.big ? Font.DemiBold : Font.Normal }
    Text { width: parent.width; text: row.detail; color: row.detailColor; font.pixelSize: Theme.small + 1
           wrapMode: Text.WordWrap; visible: text !== "" }
    Column { id: under; width: parent.width }
  }
  Item {
    id: slot
    anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter
    width: childrenRect.width; height: childrenRect.height
  }
  Rectangle { visible: !row.last; x: 14; anchors.bottom: parent.bottom; width: parent.width - 28; height: 1; color: Theme.line }
}

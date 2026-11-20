// A button: the main one in Auren's orange, the others grey.
import QtQuick

Rectangle {
  id: pill
  property string label
  property bool primary: false
  property bool small: false
  signal clicked()
  implicitWidth: t.implicitWidth + (small ? 20 : 28)
  implicitHeight: small ? 24 : 28
  radius: height / 2
  opacity: enabled ? 1 : 0.45
  color: primary ? (ma.pressed ? Qt.darker(Theme.accent, 1.15) : Theme.accent) : (ma.pressed ? Theme.pressed : Theme.control)
  Text { id: t; anchors.centerIn: parent; text: pill.label; font.pixelSize: pill.small ? 12 : Theme.body
         font.weight: Font.Medium; color: pill.primary ? "white" : Theme.text }
  MouseArea { id: ma; anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: pill.clicked() }
}

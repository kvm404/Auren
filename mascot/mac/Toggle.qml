// A switch, as macOS draws one: on in Auren's orange.
import QtQuick

Item {
  id: sw
  property bool checked: false
  signal toggled()
  implicitWidth: 38
  implicitHeight: 22
  opacity: enabled ? 1 : 0.45
  Rectangle {
    anchors.fill: parent
    radius: height / 2
    color: sw.checked ? Theme.accent : Theme.off
    Behavior on color { ColorAnimation { duration: 120 } }
    Rectangle {
      width: parent.height - 4; height: width; radius: width / 2
      y: 2; x: sw.checked ? parent.width - width - 2 : 2
      color: "white"
      border.width: Theme.dark ? 0 : 0.5
      border.color: "#c7c7cc"
      Behavior on x { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
    }
  }
  MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: sw.toggled() }
}

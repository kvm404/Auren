import QtQuick
// Preview stand-in for the bar button.
Item {
  id: b
  property QtObject bar: null
  property string tooltipText: ""
  property Component iconComponent: null
  property color foreground: "white"
  signal pressed(int b)
  implicitWidth: 24; implicitHeight: 24
  Loader { anchors.fill: parent; sourceComponent: b.iconComponent }
}

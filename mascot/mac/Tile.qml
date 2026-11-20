// A page's icon in the sidebar: a white symbol (icons/<name>.svg) on a coloured rounded square.
import QtQuick

Rectangle {
  property string icon
  property real size: 22
  width: size; height: size; radius: size * 0.26
  Image { anchors.centerIn: parent; width: parent.width * 0.7; height: width; sourceSize: Qt.size(64, 64)
          source: parent.icon ? "icons/" + parent.icon + ".svg" : ""; smooth: true }
}

// A QR code on white, whatever the theme.
import QtQuick

Rectangle {
  property string source
  width: 168; height: 168; radius: 10
  color: "white"
  border.color: Theme.line
  Image { anchors.fill: parent; anchors.margins: 6; source: parent.source; cache: false; smooth: false
          fillMode: Image.PreserveAspectFit }
}

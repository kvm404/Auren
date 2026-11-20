// A line of grey text that wraps.
import QtQuick

Text {
  width: parent ? parent.width : 0
  leftPadding: 4
  rightPadding: 4
  color: Theme.dim
  font.pixelSize: Theme.small + 1
  wrapMode: Text.WordWrap
}

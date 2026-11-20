// A rounded card of rows (SettingRow), as System Settings groups its controls.
import QtQuick

Rectangle {
  default property alias rows: col.data
  width: parent ? parent.width : 0
  height: col.implicitHeight
  radius: 10
  color: Theme.card
  border.color: Theme.line
  Column { id: col; width: parent.width }
}

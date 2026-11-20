// A one-line text field. accepted() on Return.
import QtQuick

Rectangle {
  id: f
  property alias text: input.text
  property string placeholder
  property bool password: false
  signal accepted()
  signal edited()
  function focusIt() { input.forceActiveFocus() }
  implicitWidth: 240
  implicitHeight: 28
  radius: 7
  color: Theme.field
  border.color: input.activeFocus ? Theme.accent : Theme.line
  border.width: input.activeFocus ? 2 : 1
  TextInput {
    id: input
    anchors.fill: parent; anchors.leftMargin: 9; anchors.rightMargin: 9
    verticalAlignment: TextInput.AlignVCenter
    echoMode: f.password ? TextInput.Password : TextInput.Normal
    color: Theme.text; font.pixelSize: Theme.body; clip: true
    selectByMouse: true
    selectionColor: Theme.accent
    onAccepted: f.accepted()
    onTextEdited: f.edited()
    Text { anchors.verticalCenter: parent.verticalCenter; text: f.placeholder; color: Theme.faint
           font.pixelSize: Theme.body; visible: input.text === "" }
  }
}

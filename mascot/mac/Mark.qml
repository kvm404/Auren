// A setup step's state: done (a green check), todo (it needs you), optional, or busy (turning on).
import QtQuick

Rectangle {
  id: mark
  property string state: "todo"   // done, todo, optional, busy
  width: 22; height: 22; radius: 11
  color: state === "done" ? Theme.ok : "transparent"
  border.width: state === "done" ? 0 : 1.5
  border.color: state === "todo" ? Theme.warn : state === "busy" ? Theme.accent : Theme.dim
  Text { anchors.centerIn: parent; visible: mark.state !== "busy"
         text: mark.state === "done" ? "✓" : mark.state === "todo" ? "!" : "–"
         color: mark.state === "done" ? "white" : mark.state === "todo" ? Theme.warn : Theme.dim
         font.pixelSize: 12; font.weight: Font.Bold }
  Rectangle {                    // busy: a dot that pulses
    visible: mark.state === "busy"
    anchors.centerIn: parent; width: 8; height: 8; radius: 4; color: Theme.accent
    SequentialAnimation on opacity { running: mark.state === "busy"; loops: Animation.Infinite
      NumberAnimation { from: 1; to: 0.25; duration: 600 } NumberAnimation { from: 0.25; to: 1; duration: 600 } }
  }
}

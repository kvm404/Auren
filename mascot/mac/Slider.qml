// A slider: from..to in steps. `live` follows the drag; released(value) when it's let go.
import QtQuick

Item {
  id: sl
  property real from: 0
  property real to: 1
  property real step: 0.05
  property real value: 0
  property real live: value
  readonly property bool dragging: ma.pressed
  signal released(real value)
  onValueChanged: if (!ma.pressed) live = value
  implicitWidth: 200
  implicitHeight: 22
  opacity: enabled ? 1 : 0.45
  readonly property real frac: Math.max(0, Math.min(1, (live - from) / Math.max(0.0001, to - from)))
  function at(x) {
    var v = from + Math.max(0, Math.min(1, (x - knob.width / 2) / (width - knob.width))) * (to - from)
    return Math.round(v / step) * step
  }
  Rectangle { id: track; x: knob.width / 2; width: parent.width - knob.width; height: 4; radius: 2
              anchors.verticalCenter: parent.verticalCenter; color: Theme.off }
  Rectangle { x: track.x; width: track.width * sl.frac; height: 4; radius: 2; anchors.verticalCenter: parent.verticalCenter
              color: Theme.accent }
  Rectangle {
    id: knob
    width: 18; height: 18; radius: 9
    anchors.verticalCenter: parent.verticalCenter
    x: (sl.width - width) * sl.frac
    color: "white"
    border.width: 0.5; border.color: Theme.dark ? "#2c2c2e" : "#c7c7cc"
  }
  MouseArea {
    id: ma
    anchors.fill: parent
    cursorShape: Qt.PointingHandCursor
    onPressed: (mouse) => sl.live = sl.at(mouse.x)
    onPositionChanged: (mouse) => { if (pressed) sl.live = sl.at(mouse.x) }
    onReleased: sl.released(sl.live)
  }
}

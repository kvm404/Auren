import QtQuick

// Asleep: two z's drifting up and to the right from where this item is placed.
// Smooth with sleepAnimation; otherwise stepped, three moves a second (cheap, pixel-art-like).
Item {
  id: zz

  required property Item m          // the mascot (a MascotBase)
  property color color: m.accent

  Repeater {
    model: 2
    Text {
      id: zed
      required property int index
      visible: zz.m.wSleep > 0.5
      text: "z"
      color: zz.color
      font.family: "monospace"
      font.bold: true
      font.pixelSize: zz.m.u * (index ? 2.6 : 2)
      property int step: index * 6
      property real smooth: 0
      readonly property real p: zz.m.sleepAnimation ? smooth : (step % 16) / 10
      x: p * 2.5 * zz.m.u
      y: -p * 4 * zz.m.u
      opacity: p <= 1 ? Math.sin(Math.PI * p) * zz.m.wSleep : 0
      Timer { running: zed.visible && !zz.m.sleepAnimation; interval: 330; repeat: true; onTriggered: zed.step++ }
      SequentialAnimation on smooth {
        running: zed.visible && zz.m.sleepAnimation
        loops: Animation.Infinite
        PauseAnimation { duration: zed.index * 1400 }
        NumberAnimation { from: 0; to: 1; duration: 2600 }
        PauseAnimation { duration: 2600 - zed.index * 1400 }
      }
    }
  }
}

import QtQuick

// One of Clawd's eyes, drawn around its centre (this item has no size).
// Shapes: normal (a tall block, as in the logo), wide, focused, sleepy, closed, happy (^), x (dizzy).
Item {
  id: eye

  property real u: 6
  property color ink: "#1d1310"
  property string shape: "normal"
  property real blink: 0          // 0 open .. 1 shut
  property real lookX: 0          // -1..1
  property real lookY: 0

  readonly property bool block: shape === "normal" || shape === "wide" || shape === "focused"
                                || shape === "sleepy" || shape === "closed"
  readonly property real openH: shape === "wide" ? 2.5 : shape === "focused" ? 1.4 : shape === "sleepy" ? 0.7
                                : shape === "closed" ? 0.3 : 2

  Rectangle {
    visible: eye.block
    color: eye.ink
    antialiasing: false
    width: eye.u * (eye.shape === "wide" ? 1.25 : 1)
    height: Math.max(eye.u * 0.3, eye.u * eye.openH * (1 - eye.blink * 0.85))
    x: -width / 2 + eye.lookX * eye.u * 0.55
    y: -height / 2 + eye.lookY * eye.u * 0.5 + (eye.shape === "sleepy" || eye.shape === "closed" ? eye.u * 0.5 : 0)
    Behavior on width { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
  }

  // ^ : happy
  Repeater {
    model: eye.shape === "happy" ? [-1, 1] : []
    Rectangle {
      required property var modelData
      color: eye.ink
      width: eye.u * 0.95
      height: eye.u * 0.42
      radius: height / 3
      x: modelData < 0 ? -width + eye.u * 0.12 : -eye.u * 0.12
      y: -eye.u * 0.2
      rotation: modelData < 0 ? -40 : 40
      transformOrigin: modelData < 0 ? Item.Right : Item.Left
    }
  }

  // x : dizzy (an error)
  Repeater {
    model: eye.shape === "x" ? [45, -45] : []
    Rectangle {
      required property var modelData
      color: eye.ink
      width: eye.u * 1.6
      height: eye.u * 0.42
      x: -width / 2
      y: -height / 2
      rotation: modelData
    }
  }
}

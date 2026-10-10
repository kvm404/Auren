import QtQuick
import QtQuick.Shapes

// One of Auren's LED eyes on his visor, drawn around its centre (this item has no size): a glowing
// pill with a soft halo and a hot core.
// Shapes: normal, wide, focused (a scanning slit), sleepy, closed (a dim line), happy (^), x (dizzy).
Item {
  id: eye

  property real u: 6
  property color led: "#FFD15C"
  property string shape: "normal"
  property real blink: 0          // 0 open .. 1 shut
  property real lookX: 0          // -1..1
  property real lookY: 0

  readonly property bool block: shape === "normal" || shape === "wide" || shape === "focused"
                                || shape === "sleepy" || shape === "closed"
  readonly property real w: (shape === "wide" ? 1.35 : shape === "focused" ? 1.55 : shape === "sleepy" ? 1.4
                             : shape === "closed" ? 1.5 : 1.1) * u
  readonly property real h: Math.max(0.2 * u, (shape === "wide" ? 2.1 : shape === "focused" ? 0.7 : shape === "sleepy" ? 0.55
                             : shape === "closed" ? 0.22 : 1.75) * u * (1 - blink * 0.85))
  readonly property real dx: lookX * u * 0.7
  readonly property real dy: lookY * u * 0.45 + (shape === "sleepy" || shape === "closed" ? u * 0.45 : 0)
  readonly property real bright: shape === "closed" ? 0.45 : 1

  Rectangle {      // halo
    visible: eye.block
    width: eye.w + 0.9 * eye.u
    height: eye.h + 0.9 * eye.u
    radius: Math.min(width, height) / 2
    x: -width / 2 + eye.dx
    y: -height / 2 + eye.dy
    color: eye.led
    opacity: 0.25 * eye.bright
    antialiasing: true
  }
  Rectangle {      // the LED
    visible: eye.block
    width: eye.w
    height: eye.h
    radius: Math.min(width, height) / 2
    x: -width / 2 + eye.dx
    y: -height / 2 + eye.dy
    color: eye.led
    opacity: eye.bright
    antialiasing: true
    Behavior on width { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
    Rectangle {    // hot core
      width: parent.width * 0.45
      height: parent.height * 0.55
      radius: Math.min(width, height) / 2
      anchors.centerIn: parent
      color: "#FFF6D6"
      opacity: 0.75
      antialiasing: true
    }
  }

  // ^ happy
  Shape {
    visible: eye.shape === "happy"
    x: -eye.u
    y: -eye.u
    width: 2 * eye.u
    height: 2 * eye.u
    preferredRendererType: Shape.CurveRenderer
    ShapePath {
      fillColor: "transparent"
      strokeColor: eye.led
      strokeWidth: eye.u * 0.38
      capStyle: ShapePath.RoundCap
      joinStyle: ShapePath.RoundJoin
      startX: 0.25 * eye.u; startY: 1.35 * eye.u
      PathLine { x: eye.u; y: 0.55 * eye.u }
      PathLine { x: 1.75 * eye.u; y: 1.35 * eye.u }
    }
  }

  // x dizzy (an error)
  Repeater {
    model: eye.shape === "x" ? [45, -45] : []
    Rectangle {
      required property var modelData
      color: eye.led
      width: eye.u * 1.5
      height: eye.u * 0.36
      radius: height / 2
      antialiasing: true
      x: -width / 2
      y: -height / 2
      rotation: modelData
    }
  }
}

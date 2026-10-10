import QtQuick
import QtQuick.Shapes

// One of Veyra's eyes, drawn around its centre (this item has no size): a glossy oval with two
// shines and a lash flicking outward.
// Shapes: normal, wide (sparkly), focused, sleepy (half shut), closed (‿), happy (^), x (dizzy).
Item {
  id: eye

  property real u: 6
  property color ink: "#2B1D4D"
  property string shape: "normal"
  property real blink: 0          // 0 open .. 1 shut
  property real lookX: 0          // -1..1
  property real lookY: 0
  property int side: -1           // -1 the left eye, 1 the right one (the lash flicks outward)

  readonly property bool arcUp: shape === "happy"
  readonly property bool open: shape === "normal" || shape === "wide" || shape === "focused" || shape === "sleepy"
  readonly property real w: (shape === "wide" ? 1.75 : 1.5) * u
  readonly property real h: (shape === "wide" ? 2.3 : shape === "focused" ? 1.35 : shape === "sleepy" ? 0.85 : 2.0)
                             * u * (1 - blink * 0.88)
  readonly property real dx: lookX * u * 0.35
  readonly property real dy: lookY * u * 0.3 + (shape === "sleepy" ? u * 0.45 : 0)

  Rectangle {
    id: ball
    visible: eye.open
    width: eye.w
    height: Math.max(eye.u * 0.25, eye.h)
    radius: Math.min(width, height) / 2
    x: -width / 2 + eye.dx
    y: -height / 2 + eye.dy
    color: eye.ink
    antialiasing: true
    Behavior on width { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }

    Rectangle {      // the big shine
      visible: ball.height > eye.u
      width: eye.u * (eye.shape === "wide" ? 0.7 : 0.55)
      height: width
      radius: width / 2
      color: "white"
      antialiasing: true
      x: ball.width * 0.16
      y: ball.height * 0.13
    }
    Rectangle {      // the small one
      visible: ball.height > eye.u * 1.2
      width: eye.u * 0.26
      height: width
      radius: width / 2
      color: "white"
      opacity: 0.85
      antialiasing: true
      x: ball.width * 0.6
      y: ball.height * 0.62
    }
  }

  // the lash
  Rectangle {
    visible: eye.open && eye.blink < 0.6 && eye.shape !== "sleepy"
    width: eye.u * 0.8
    height: eye.u * 0.26
    radius: height / 2
    color: eye.ink
    antialiasing: true
    x: eye.side < 0 ? ball.x - width * 0.62 : ball.x + ball.width - width * 0.38
    y: ball.y + ball.height * 0.1
    rotation: eye.side < 0 ? 32 : -32
  }

  // ‿ closed, ^ happy
  Shape {
    visible: eye.shape === "closed" || eye.shape === "happy"
    x: -eye.u
    y: -eye.u
    width: 2 * eye.u
    height: 2 * eye.u
    preferredRendererType: Shape.CurveRenderer
    ShapePath {
      fillColor: "transparent"
      strokeColor: eye.ink
      strokeWidth: eye.u * 0.32
      capStyle: ShapePath.RoundCap
      startX: 0.2 * eye.u; startY: (eye.arcUp ? 1.3 : 1.0) * eye.u
      PathQuad { x: 1.8 * eye.u; y: (eye.arcUp ? 1.3 : 1.0) * eye.u; controlX: eye.u; controlY: (eye.arcUp ? 0.2 : 1.8) * eye.u }
    }
  }

  // x : dizzy (an error)
  Repeater {
    model: eye.shape === "x" ? [45, -45] : []
    Rectangle {
      required property var modelData
      color: eye.ink
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

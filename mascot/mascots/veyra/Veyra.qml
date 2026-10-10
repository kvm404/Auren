import QtQuick
import QtQuick.Shapes
import "../common"

// Veyra, a small lavender fox spirit: a round body, big ears that show how she feels, glossy eyes,
// a gold star on her forehead and a fluffy tail with a cream tip.
//
//   listening   ears perk up and twitch with your voice, eyes wide, the star glows
//   thinking    looks up, tail curls, sparkles twinkle over her head
//   working     paws tapping, tail wagging fast
//   speaking    mouth opens with the voice, little hops
//   yes/no      head tilt, one ear folded, "?"
//   error       ears flat, dizzy eyes
//   asleep      ears drooping, eyes ‿ ‿, tail wrapped down, z's
//
// The stage is 16 x 12 units; the body sits on its bottom edge. Clock, moods and blinking come from
// ../common/MascotBase.qml.
MascotBase {
  id: c

  readonly property color fur: off ? "#9a95a8" : "#A08BF7"
  readonly property color furLight: off ? "#aaa5b6" : "#B9A9FF"
  readonly property color furDark: off ? "#77728a" : "#7A64D8"
  readonly property color cream: off ? "#d8d4dc" : "#FFF3FA"
  readonly property color pink: off ? "#bcb3bf" : "#FF9EC7"
  readonly property color gold: off ? "#c9c3cf" : "#FFD27A"
  readonly property color ink: "#2B1D4D"

  accent: "#A08BF7"
  padX: 4
  padTop: 5
  implicitWidth: (16 + 2 * padX) * u
  implicitHeight: (12 + padTop + 1) * u
  hitbox: hit
  bubbleAnchorY: (mood === "thinking" || awaiting) ? (padTop - 4.4) * u : (padTop + 0.2) * u
  extraLively: happy            // the happy tail wag needs the clock

  // ---- the pose ----------------------------------------------------------------------------
  readonly property real hop: hopA + wSpeak * lv * 0.8 + wWork * Math.abs(osc(2.2)) * 0.25
  readonly property real stretchY: 1 + 0.03 * breath * wBreath + 0.06 * wTall + 0.08 * lv * wSpeak
                                   + 0.04 * lv * wListen - 0.15 * squashA - 0.07 * wSleep
  readonly property real stretchX: 1 - 0.02 * wTall - 0.03 * lv * wSpeak + 0.15 * squashA + 0.05 * wSleep
  readonly property real lean: wSway * 4 * osc(0.45) - wAsk * 8 + wSpeak * lv * 2.5 * osc(0.8)

  // ears: degrees of outward droop (negative = perked up)
  property real twitchL: 0
  property real twitchR: 0
  readonly property real earBase: wSleep * 15 + wError * 40 + (drowsy && !hovered ? 8 : 0)
                                  - wListen * (5 + lv * 10) - (happy ? 6 : 0) + wSpeak * lv * 5 * osc(1.6)
  readonly property real earL: earBase + twitchL + wSway * 5 * osc(0.45)
  readonly property real earR: earBase + twitchR - wSway * 5 * osc(0.45) + wAsk * 38
                               + wListen * lv * 6 * osc(3.1)

  readonly property real tailAngle: 8 * osc(0.3) * (1 - wWork) + wWork * 16 * osc(2.6) + (happy ? 18 * osc(3.2) : 0)
                                    - wSway * 16 + wSpeak * lv * 10 * osc(1.3) + wSleep * 58 + wError * 26 - wAsk * 8
  function pawLift(i) {
    return wWork * Math.max(0, osc(5, i ? 0.5 : 0)) * 0.6 + (happy ? 0.7 : 0) + (i ? wSpeak * lv * 0.6 : 0)
  }
  readonly property real mouthOpen: wSpeak * lv

  // a quick ear flick now and then while idle, like a real cat
  SequentialAnimation {
    id: twitchLAnim
    NumberAnimation { target: c; property: "twitchL"; to: 16; duration: 70 }
    NumberAnimation { target: c; property: "twitchL"; to: 0; duration: 220; easing.type: Easing.OutBack }
  }
  SequentialAnimation {
    id: twitchRAnim
    NumberAnimation { target: c; property: "twitchR"; to: 16; duration: 70 }
    NumberAnimation { target: c; property: "twitchR"; to: 0; duration: 220; easing.type: Easing.OutBack }
  }
  Timer {
    running: !c.off && !c.asleep && c.mood === "idle"
    interval: 6000
    repeat: true
    onTriggered: {
      interval = 4000 + Math.random() * 8000
      if (Math.random() < 0.5) twitchLAnim.restart()
      else twitchRAnim.restart()
      if (Math.random() < 0.15) twitchLAnim.restart()
    }
  }

  // ---- drawing -----------------------------------------------------------------------------
  Item {
    id: stage
    x: c.padX * c.u
    y: c.padTop * c.u
    width: 16 * c.u
    height: 12 * c.u

    // a soft shadow on the ground, smaller while she's up in the air
    Rectangle {
      readonly property real k: Math.max(0.55, 1 - c.hop * 0.08)
      width: 7.5 * c.u * k
      height: 0.8 * c.u
      radius: height / 2
      x: 8 * c.u - width / 2
      y: 11.25 * c.u
      color: "black"
      opacity: c.off ? 0.08 : 0.16 * k
      antialiasing: true
    }

    // listening: rings spreading from the body
    Repeater {
      model: 3
      Rectangle {
        required property int index
        readonly property real p: (c.t * 0.9 + index / 3) % 1
        visible: c.wListen > 0.01
        x: 3 * c.u
        y: 3.8 * c.u
        width: 10 * c.u
        height: 7.4 * c.u
        radius: 3.7 * c.u
        color: "transparent"
        border.color: c.fur
        border.width: Math.max(1, c.u * 0.3)
        scale: 1 + p * (0.4 + 0.35 * c.lv)
        opacity: c.wListen * (1 - p) * (0.25 + 0.75 * c.lv)
      }
    }

    Item {
      id: rig
      width: parent.width
      height: parent.height
      transform: [
        Rotation { origin.x: 8 * c.u; origin.y: 11.2 * c.u; angle: c.lean },
        Translate { x: c.shakeA * c.u; y: -c.hop * c.u }
      ]

      // the tail, behind the body: a thick curve with a cream tip, swinging from its base
      Item {
        x: 12 * c.u
        y: 9.4 * c.u
        transform: Rotation { angle: c.tailAngle }
        Shape {
          x: -2 * c.u
          y: -7 * c.u
          width: 8 * c.u
          height: 9 * c.u
          preferredRendererType: Shape.CurveRenderer
          ShapePath {
            fillColor: "transparent"
            strokeColor: c.fur
            strokeWidth: 2.5 * c.u
            capStyle: ShapePath.RoundCap
            startX: 2 * c.u; startY: 7 * c.u
            PathQuad { x: 4.9 * c.u; y: 2.6 * c.u; controlX: 5.4 * c.u; controlY: 6.4 * c.u }
          }
        }
        Rectangle {
          width: 2.5 * c.u
          height: 2.7 * c.u
          radius: width / 2
          x: (2.9 - 1.25) * c.u
          y: (-4.4 - 1.35) * c.u
          color: c.cream
          antialiasing: true
        }
      }

      // ears, behind the head, pivoting at their base
      Repeater {
        model: [-1, 1]
        Item {
          id: ear
          required property var modelData
          readonly property real s: modelData       // -1 left, 1 right (mirrored)
          x: (8 + 2.6 * s) * c.u
          y: 4.9 * c.u
          transform: Rotation { angle: ear.s < 0 ? -c.earL : c.earR }
          Shape {
            x: -3 * c.u
            y: -5 * c.u
            width: 6 * c.u
            height: 7.5 * c.u
            preferredRendererType: Shape.CurveRenderer
            ShapePath {      // the ear
              fillColor: c.fur
              strokeColor: c.fur
              strokeWidth: 0.6 * c.u
              joinStyle: ShapePath.RoundJoin
              startX: (3 - 1.9 * ear.s) * c.u; startY: 6.3 * c.u
              PathLine { x: (3 + 0.6 * ear.s) * c.u; y: 0.6 * c.u }
              PathLine { x: (3 + 1.9 * ear.s) * c.u; y: 6.3 * c.u }
              PathLine { x: (3 - 1.9 * ear.s) * c.u; y: 6.3 * c.u }
            }
            ShapePath {      // the pink inside
              fillColor: c.pink
              strokeColor: c.pink
              strokeWidth: 0.3 * c.u
              joinStyle: ShapePath.RoundJoin
              startX: (3 - 1.0 * ear.s) * c.u; startY: 5.0 * c.u
              PathLine { x: (3 + 0.5 * ear.s) * c.u; y: 1.9 * c.u }
              PathLine { x: (3 + 0.95 * ear.s) * c.u; y: 5.0 * c.u }
              PathLine { x: (3 - 1.0 * ear.s) * c.u; y: 5.0 * c.u }
            }
          }
        }
      }

      // body and face squash and stretch together, sitting on the ground
      Item {
        id: torso
        width: 16 * c.u
        height: 12 * c.u
        transform: Scale { origin.x: 8 * c.u; origin.y: 11.2 * c.u; xScale: c.stretchX; yScale: c.stretchY }

        Rectangle {    // body
          x: 3 * c.u
          y: 3.8 * c.u
          width: 10 * c.u
          height: 7.4 * c.u
          radius: 3.7 * c.u
          antialiasing: true
          gradient: Gradient {
            GradientStop { position: 0; color: c.furLight }
            GradientStop { position: 0.55; color: c.fur }
            GradientStop { position: 1; color: c.furDark }
          }
        }
        Rectangle {    // cream muzzle
          x: 5.3 * c.u
          y: 7.85 * c.u
          width: 5.4 * c.u
          height: 3.1 * c.u
          radius: 1.55 * c.u
          color: c.cream
          antialiasing: true
        }

        // the star on her forehead, glowing while she listens or thinks
        Sparkle {
          x: 8 * c.u
          y: 5.15 * c.u
          size: 2.8 * c.u * (1 + 0.3 * c.lv * (c.wListen + c.wSpeak))
          color: c.gold
          opacity: c.off ? 0 : 0.18 + 0.4 * c.wListen * (0.4 + c.lv) + 0.35 * c.wSway * c.pulse(1.2)
        }
        Sparkle {
          x: 8 * c.u
          y: 5.15 * c.u
          size: 1.35 * c.u * (1 + 0.15 * c.lv * c.wListen)
          color: c.gold
        }

        VeyraEye { u: c.u; ink: c.ink; side: -1; x: 6.15 * c.u; y: 7.0 * c.u; shape: c.eyeShape; blink: c.blink; lookX: c.lookX; lookY: c.lookY }
        VeyraEye { u: c.u; ink: c.ink; side: 1; x: 9.85 * c.u; y: 7.0 * c.u; shape: c.eyeShape; blink: c.blink; lookX: c.lookX; lookY: c.lookY }

        // blush: always a little, more when happy
        Repeater {
          model: [4.75, 11.25]
          Rectangle {
            required property var modelData
            color: "#FF8FB8"
            opacity: c.off ? 0 : (c.happy ? 0.85 : 0.4)
            x: (modelData - 0.75) * c.u
            y: 8.35 * c.u
            width: 1.5 * c.u
            height: 0.75 * c.u
            radius: height / 2
            antialiasing: true
            Behavior on opacity { NumberAnimation { duration: 250 } }
          }
        }

        Rectangle {    // nose
          x: 7.65 * c.u
          y: 8.1 * c.u
          width: 0.7 * c.u
          height: 0.45 * c.u
          radius: height / 2
          color: "#6B3A5E"
          antialiasing: true
        }
        Shape {        // mouth: a little "w"
          visible: c.mouthOpen < 0.08
          x: 7 * c.u
          y: 8.5 * c.u
          width: 2 * c.u
          height: 1 * c.u
          preferredRendererType: Shape.CurveRenderer
          ShapePath {
            fillColor: "transparent"
            strokeColor: "#6B3A5E"
            strokeWidth: 0.2 * c.u
            capStyle: ShapePath.RoundCap
            startX: 0.25 * c.u; startY: 0.05 * c.u
            PathQuad { x: 1.0 * c.u; y: 0.1 * c.u; controlX: 0.62 * c.u; controlY: 0.6 * c.u }
            PathQuad { x: 1.75 * c.u; y: 0.05 * c.u; controlX: 1.38 * c.u; controlY: 0.6 * c.u }
          }
        }
        Rectangle {    // speaking: an open mouth that follows the voice
          visible: c.mouthOpen >= 0.08
          width: 0.95 * c.u
          height: (0.3 + c.mouthOpen * 1.0) * c.u
          radius: width / 2
          x: 8 * c.u - width / 2
          y: 8.6 * c.u
          color: "#6B2A4E"
          antialiasing: true
        }

        // paws
        Repeater {
          model: [6.3, 9.7]
          Rectangle {
            required property var modelData
            required property int index
            width: 1.8 * c.u
            height: 1.0 * c.u
            radius: height / 2
            x: (modelData - 0.9) * c.u
            y: (10.55 - c.pawLift(index)) * c.u
            color: c.cream
            border.color: Qt.rgba(c.furDark.r, c.furDark.g, c.furDark.b, 0.5)
            border.width: Math.max(1, 0.12 * c.u)
            antialiasing: true
          }
        }
      }

      // using the mouse: a little cursor by her right side
      Pointer {
        m: c
        ink: c.ink
        visible: c.mouse && c.mood === "working"
        x: 13.4 * c.u
        y: (7.6 - c.pawLift(1)) * c.u
      }
    }

    // thinking: sparkles twinkling over her head
    Repeater {
      model: [[12.4, -0.4], [14.2, -2.2], [12.0, -3.6]]
      Sparkle {
        required property var modelData
        required property int index
        readonly property real tw: Math.max(0, c.osc(1.1, -index * 0.33))
        visible: c.wSway > 0.01
        x: modelData[0] * c.u
        y: modelData[1] * c.u - c.hop * c.u
        size: (1.0 + 0.9 * tw) * c.u
        color: c.gold
        opacity: c.wSway * (0.3 + 0.7 * tw)
      }
    }

    // waiting for a yes or no: a question mark
    Text {
      visible: c.wAsk > 0.01
      opacity: c.wAsk
      text: "?"
      color: c.fur
      font.family: "monospace"
      font.bold: true
      font.pixelSize: c.u * 4
      x: 12.8 * c.u
      y: -4.4 * c.u + c.osc(0.7) * c.u * 0.35 - c.hop * c.u
      rotation: 12
    }

    Zzz { m: c; color: c.fur; x: 12.4 * c.u; y: 1.6 * c.u }

    // the input area: body, ears and tail
    Item {
      id: hit
      x: 2.5 * c.u
      y: 0
      width: 13.5 * c.u
      height: 12 * c.u
    }
  }
}

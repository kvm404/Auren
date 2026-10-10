import QtQuick
import "../common"

// Clawd, the Claude Code mascot, drawn from the terminal logo's blocks:
//
//    ▐▛███▜▌      a 12x8 body with two tall eyes,
//   ▝▜█████▛▘     little arms on both sides,
//     ▘▘ ▝▝       and four legs.
//
// One quadrant block of the logo is 1 unit wide and 2 tall; `u` is the unit in pixels.
// The clock, moods, blinking and the contract with shell.qml live in ../common/MascotBase.qml.
MascotBase {
  id: c

  readonly property color skin: off ? "#8f8781" : "#D77757"
  readonly property color skinDark: off ? "#6f6863" : "#B8603F"
  readonly property color ink: "#1d1310"

  accent: "#D77757"
  padX: 4
  padTop: 6
  implicitWidth: (16 + 2 * padX) * u
  implicitHeight: (10 + padTop + 2) * u
  hitbox: hit
  // where a speech bubble ends: above the thinking dots and the "?" when they show, otherwise near the head
  bubbleAnchorY: (mood === "thinking" || awaiting) ? 0.6 * u : (padTop - 2.2) * u

  // ---- the pose ----------------------------------------------------------------------------
  readonly property real hop: hopA + wSpeak * lv * 0.9 + wWork * Math.abs(osc(2.2)) * 0.35
  readonly property real squash: squashA
  readonly property real stretchY: 1 + 0.035 * breath * wBreath + 0.08 * wTall + 0.10 * lv * wSpeak
                                   + 0.05 * lv * wListen - 0.15 * squash - 0.03 * wSleep
  readonly property real stretchX: 1 - 0.02 * wTall - 0.035 * lv * wSpeak + 0.15 * squash + 0.02 * wSleep
  readonly property real lean: wSway * 4 * osc(0.45) + wAsk * 7 + wSpeak * lv * 2.5 * osc(0.8)
  readonly property real armL: wTall * 1.0 + wWork * (osc(5) > 0 ? 0.9 : 0) + wSpeak * lv * 1.4
                               + (happy ? 1.2 : 0)
  readonly property real armR: wTall * 1.0 + wWork * (osc(5) > 0 ? 0 : 0.9) + wSway * 1.3
                               + wSpeak * lv * 1.4 * pulse(1.1) + wAsk * 1.3 + (happy ? 1.2 : 0)
  function legLift(i) { return wWork * Math.max(0, osc(4.4, i % 2 ? 0.5 : 0)) * 0.8 }

  // ---- drawing -----------------------------------------------------------------------------
  Item {
    id: stage
    x: c.padX * c.u
    y: c.padTop * c.u
    width: 16 * c.u
    height: 10 * c.u

    // listening: rings spreading from the body, stronger with the voice
    Repeater {
      model: 3
      Rectangle {
        required property int index
        readonly property real p: (c.t * 0.9 + index / 3) % 1
        visible: c.wListen > 0.01
        x: 2 * c.u
        y: 0
        width: 12 * c.u
        height: 8 * c.u
        radius: c.u * 2
        color: "transparent"
        border.color: c.skin
        border.width: Math.max(1, c.u * 0.35)
        scale: 1 + p * (0.45 + 0.35 * c.lv)
        opacity: c.wListen * (1 - p) * (0.25 + 0.75 * c.lv)
      }
    }

    // the character
    Item {
      id: rig
      width: parent.width
      height: parent.height
      transform: [
        Rotation { origin.x: 8 * c.u; origin.y: 10 * c.u; angle: c.lean },
        Translate { x: c.shakeA * c.u; y: -c.hop * c.u }
      ]

      // legs
      Repeater {
        model: [3, 5, 10, 12]
        Rectangle {
          required property var modelData
          required property int index
          color: c.skinDark
          antialiasing: true
          x: modelData * c.u
          width: c.u
          height: 2 * c.u * (1 - c.legLift(index) * 0.35)
          y: 8 * c.u - c.legLift(index) * c.u * 0.6
        }
      }

      // body, arms and face squash and stretch together, standing on the legs
      Item {
        id: torso
        width: 16 * c.u
        height: 8 * c.u
        transform: Scale { origin.x: 8 * c.u; origin.y: 8 * c.u; xScale: c.stretchX; yScale: c.stretchY }

        Rectangle {    // left arm
          color: c.skin
          antialiasing: true
          x: 0
          y: (4 - c.armL) * c.u
          width: 2 * c.u
          height: 2 * c.u
        }
        Rectangle {    // right arm
          color: c.skin
          antialiasing: true
          x: 14 * c.u
          y: (4 - c.armR) * c.u
          width: 2 * c.u
          height: 2 * c.u
        }
        Rectangle {    // body
          color: c.skin
          antialiasing: true
          x: 2 * c.u
          width: 12 * c.u
          height: 8 * c.u
        }
        Rectangle {    // a soft shade along the bottom, for depth
          color: c.skinDark
          opacity: 0.35
          antialiasing: true
          x: 2 * c.u
          y: 7 * c.u
          width: 12 * c.u
          height: c.u
        }

        Eye { u: c.u; ink: c.ink; x: 4.5 * c.u; y: 3 * c.u; shape: c.eyeShape; blink: c.blink; lookX: c.lookX; lookY: c.lookY }
        Eye { u: c.u; ink: c.ink; x: 11.5 * c.u; y: 3 * c.u; shape: c.eyeShape; blink: c.blink; lookX: c.lookX; lookY: c.lookY }

        // blush when happy
        Repeater {
          model: c.happy ? [3.2, 11.8] : []
          Rectangle {
            required property var modelData
            color: "#F2A48A"
            opacity: 0.8
            x: (modelData - 0.6) * c.u
            y: 4.9 * c.u
            width: 1.2 * c.u
            height: 0.6 * c.u
            radius: height / 2
          }
        }
      }

      // using the mouse: a little cursor by the right hand, clicking
      Pointer {
        m: c
        ink: c.ink
        visible: c.mouse && c.mood === "working"
        x: 15.2 * c.u
        y: (2.6 - c.armR) * c.u
      }
    }

    // thinking: three dots rising over the head
    Row {
      visible: c.wSway > 0.01
      opacity: c.wSway
      spacing: c.u * 0.6
      x: 9.5 * c.u
      y: -2.6 * c.u - c.hop * c.u
      Repeater {
        model: 3
        Rectangle {
          required property int index
          width: c.u
          height: c.u
          color: c.skin
          antialiasing: true
          y: -Math.max(0, c.osc(1.4, -index * 0.18)) * c.u * 0.9
          opacity: 0.45 + 0.55 * Math.max(0, c.osc(1.4, -index * 0.18))
        }
      }
    }

    // waiting for a yes or no: a question mark
    Text {
      visible: c.wAsk > 0.01
      opacity: c.wAsk
      text: "?"
      color: c.skin
      font.family: "monospace"
      font.bold: true
      font.pixelSize: c.u * 4
      x: 13.2 * c.u
      y: -5.2 * c.u + c.osc(0.7) * c.u * 0.35 - c.hop * c.u
      rotation: 12
    }

    Zzz { m: c; color: c.skin; x: 13 * c.u; y: -0.5 * c.u }

    // the input area: the body and arms (not the empty margin around it)
    Item {
      id: hit
      x: -0.5 * c.u
      y: -1.5 * c.u
      width: 17 * c.u
      height: 12 * c.u
    }
  }
}

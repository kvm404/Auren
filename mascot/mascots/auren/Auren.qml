import QtQuick
import QtQuick.Shapes
import "../common"

// Auren, a little hover-bot: a cobalt head with a dark glass visor, gold LED eyes, an antenna with a
// glowing tip, two floating hands and a thruster glow underneath.
//
//   listening   antenna sends out radio rings with your voice, a hand cupped at his ear
//   thinking    looks up, hand on his chin, two sparks orbit the antenna
//   working     types on a hologram keyboard (or holds a mouse pointer), eyes narrow to a scan
//   speaking    the mouth is a little equalizer bouncing with the voice, hands gesture
//   yes/no      head tilt, scratching his head, "?"
//   error       red visor, dizzy eyes, sparks from the antenna
//   asleep      sinks to the ground, thruster off, eyes dim lines, z's
//
// The stage is 16 x 13 units. Clock, moods and blinking come from ../common/MascotBase.qml.
MascotBase {
  id: c

  readonly property color metal: off ? "#8a909c" : "#4E73B8"
  readonly property color metalLight: off ? "#a0a6b0" : "#7097DA"
  readonly property color metalDark: off ? "#666b75" : "#2F4B82"
  readonly property color edge: off ? "#50545c" : "#1E2F55"
  readonly property color glass: "#0C1222"
  readonly property color gold: off ? "#9a9488" : "#F5B83D"
  readonly property color led: wError > 0.5 ? "#FF5D5D" : off ? "#5a5f6a" : "#FFD15C"
  readonly property color holo: "#7FDBFF"

  accent: "#F5B83D"
  padX: 4
  padTop: 5
  implicitWidth: (16 + 2 * padX) * u
  implicitHeight: (13 + padTop + 1) * u
  hitbox: hit
  bubbleAnchorY: (mood === "thinking" || awaiting) ? (padTop - 3.8) * u : (padTop - 1.6) * u
  extraLively: wHappy > 0.01      // waving needs the clock

  property real wHappy: happy ? 1 : 0
  Behavior on wHappy { NumberAnimation { duration: 250 } }

  // ---- the pose ----------------------------------------------------------------------------
  // how high he floats (units): a slow hover, hops, sinking to the ground asleep
  readonly property real hover: hopA + wSpeak * lv * 0.7 + wTall * 0.4 + (1 - wSleep) * 0.3 * osc(0.42) - wSleep * 1.0
  readonly property real stretchY: 1 + 0.025 * breath * wBreath + 0.05 * wTall + 0.06 * lv * wSpeak
                                   - 0.15 * squashA - 0.03 * wSleep
  readonly property real stretchX: 1 - 0.02 * wTall - 0.03 * lv * wSpeak + 0.15 * squashA + 0.02 * wSleep
  readonly property real lean: wSway * 4 * osc(0.45) + wAsk * 7 + wSpeak * lv * 2.5 * osc(0.8)
  readonly property real antennaAngle: wSway * 6 * osc(0.9) + wSpeak * lv * 8 * osc(1.7) + wSleep * 28
                                       - squashA * 12 + wError * 12 * osc(6)
  readonly property real mouthOpen: wSpeak * lv
  readonly property real flame: off ? 0 : (1 - wSleep) * (0.75 + 0.25 * pulse(7))

  // where the hands are (centres, in units): rest by the sides, then each mood pulls them somewhere
  function hand(side) {        // side -1 left, 1 right
    var r = side > 0
    var x = r ? 14.95 : 1.05
    var y = 7.4 + 0.28 * osc(0.42, r ? 0.45 : 0.2) * (1 - wSleep)
    x = mix(x, r ? 14.0 : 2.0, wSleep); y = mix(y, 10.3, wSleep)
    if (r) { x = mix(x, 13.95, wListen); y = mix(y, 5.3, wListen) }
    if (r) { x = mix(x, 10.4, wSway); y = mix(y, 9.7 + 0.15 * Math.max(0, osc(2)), wSway) }
    // typing on the hologram keyboard, or (using the mouse) the right hand holds the pointer, the left rests
    var wx = mouse ? (r ? 14.4 : 1.6) : (r ? 9.8 : 6.2)
    var wy = mouse ? (r ? 9.6 : 8.6) : 10.7 - 0.45 * Math.max(0, osc(5, r ? 0.5 : 0))
    x = mix(x, wx, wWork); y = mix(y, wy, wWork)
    x += side * wSpeak * lv * 0.4
    y -= wSpeak * lv * 1.6 * pulse(1.1, r ? 0.5 : 0)
    if (r) { x = mix(x, 12.4, wAsk); y = mix(y, 1.9 + 0.2 * osc(2.5), wAsk) }
    x = mix(x, r ? 15.4 : 0.6, wError); y = mix(y, 6.2 + 0.8 * osc(5, r ? 0.5 : 0), wError)
    x = mix(x, r ? 15.2 : 0.8, wHappy); y = mix(y, 4.6 + 0.3 * osc(3, r ? 0.5 : 0), wHappy)
    return Qt.point(x, y)
  }
  readonly property point handL: hand(-1)
  readonly property point handR: hand(1)

  // ---- drawing -----------------------------------------------------------------------------
  Item {
    id: stage
    x: c.padX * c.u
    y: c.padTop * c.u
    width: 16 * c.u
    height: 13 * c.u

    // shadow on the ground: smaller and fainter the higher he floats
    Rectangle {
      readonly property real k: Math.max(0.5, 1 - (c.hover + 1) * 0.12)
      width: 7 * c.u * k
      height: 0.8 * c.u
      radius: height / 2
      x: 8 * c.u - width / 2
      y: 12.1 * c.u
      color: "black"
      opacity: c.off ? 0.08 : 0.18 * k
      antialiasing: true
    }

    Item {
      id: rig
      width: parent.width
      height: parent.height
      transform: [
        Rotation { origin.x: 8 * c.u; origin.y: 9.8 * c.u; angle: c.lean },
        Translate { x: c.shakeA * c.u; y: -c.hover * c.u }
      ]

      // thruster glow under the body: a soft wide halo with a bright core
      Repeater {
        model: [[3.6, 1.1, 0.22], [2.4, 0.75, 0.4], [1.1, 0.4, 0.9]]
        Rectangle {
          required property var modelData
          width: modelData[0] * (0.85 + 0.15 * c.flame) * c.u
          height: modelData[1] * (0.7 + 0.3 * c.flame) * c.u
          radius: height / 2
          x: 8 * c.u - width / 2
          y: 10.15 * c.u
          color: modelData[2] > 0.8 ? "white" : c.holo
          opacity: modelData[2] * c.flame
          antialiasing: true
        }
      }
      Rectangle {      // the nozzle
        width: 2.2 * c.u
        height: 0.55 * c.u
        radius: height / 2
        x: 8 * c.u - width / 2
        y: 9.65 * c.u
        color: c.edge
        antialiasing: true
      }

      // the head, squashing and stretching
      Item {
        id: torso
        width: 16 * c.u
        height: 13 * c.u
        transform: Scale { origin.x: 8 * c.u; origin.y: 9.8 * c.u; xScale: c.stretchX; yScale: c.stretchY }

        // antenna, wobbling from its base
        Item {
          id: antenna
          x: 8 * c.u
          y: 2.3 * c.u
          transform: Rotation { angle: c.antennaAngle }

          Rectangle {      // stem
            x: -0.18 * c.u
            y: -2.3 * c.u
            width: 0.36 * c.u
            height: 2.4 * c.u
            color: c.edge
            antialiasing: true
          }
          // listening: radio rings from the tip
          Repeater {
            model: 3
            Rectangle {
              required property int index
              readonly property real p: (c.t * 1.1 + index / 3) % 1
              visible: c.wListen > 0.01
              width: (1.4 + p * (2.6 + 1.6 * c.lv)) * c.u * 2
              height: width
              radius: width / 2
              x: -width / 2
              y: -2.45 * c.u - height / 2
              color: "transparent"
              border.color: c.gold
              border.width: Math.max(1, 0.22 * c.u)
              opacity: c.wListen * (1 - p) * (0.3 + 0.7 * c.lv)
              antialiasing: true
            }
          }
          Rectangle {      // glow around the tip
            readonly property real g: c.off ? 0 : 0.12 + c.wListen * (0.3 + 0.6 * c.lv) + c.wSpeak * 0.6 * c.lv
                                      + c.wSway * 0.5 * c.pulse(1.6) + c.wWork * 0.3 * c.pulse(3) + c.wError * 0.5
            width: (2.4 + 0.8 * c.lv) * c.u
            height: width
            radius: width / 2
            x: -width / 2
            y: -2.45 * c.u - height / 2
            color: c.wError > 0.5 ? "#FF5D5D" : c.gold
            opacity: Math.min(0.8, g)
            antialiasing: true
          }
          Rectangle {      // the tip
            width: 1.3 * c.u
            height: width
            radius: width / 2
            x: -width / 2
            y: -2.45 * c.u - height / 2
            color: c.wError > 0.5 ? "#FF5D5D" : c.gold
            border.color: Qt.darker(color, 1.4)
            border.width: Math.max(1, 0.12 * c.u)
            antialiasing: true
          }
          // thinking: two sparks orbiting the tip
          Repeater {
            model: 2
            Rectangle {
              required property int index
              readonly property real a: 2 * Math.PI * (c.t * 0.8 + index / 2)
              visible: c.wSway > 0.01
              width: 0.5 * c.u
              height: width
              radius: width / 2
              x: Math.cos(a) * 1.7 * c.u - width / 2
              y: -2.45 * c.u + Math.sin(a) * 0.65 * c.u - height / 2
              color: c.gold
              opacity: c.wSway * (0.55 + 0.45 * Math.sin(a))
              antialiasing: true
            }
          }
          // error: sparks flickering around the tip
          Repeater {
            model: [[-1.4, -3.4, -35], [1.5, -3.2, 40], [0.2, -4.2, 0]]
            Rectangle {
              required property var modelData
              required property int index
              visible: c.wError > 0.01 && c.osc(9, index * 0.3) > 0
              width: 0.25 * c.u
              height: 0.8 * c.u
              x: modelData[0] * c.u
              y: modelData[1] * c.u
              rotation: modelData[2]
              color: "#FFE27A"
              antialiasing: true
            }
          }
        }

        // ear pods (gold), brighter while listening
        Repeater {
          model: [2.35, 12.85]
          Rectangle {
            required property var modelData
            x: modelData * c.u
            y: 4.5 * c.u
            width: 0.8 * c.u
            height: 2.8 * c.u
            radius: 0.4 * c.u
            color: c.wListen > 0.01 ? Qt.lighter(c.gold, 1 + 0.25 * c.lv * c.wListen) : c.gold
            border.color: Qt.darker(c.gold, 1.5)
            border.width: Math.max(1, 0.12 * c.u)
            antialiasing: true
          }
        }

        Rectangle {    // head
          x: 3 * c.u
          y: 2.2 * c.u
          width: 10 * c.u
          height: 7.6 * c.u
          radius: 2.4 * c.u
          border.color: c.edge
          border.width: Math.max(1, 0.2 * c.u)
          antialiasing: true
          gradient: Gradient {
            GradientStop { position: 0; color: c.metalLight }
            GradientStop { position: 0.5; color: c.metal }
            GradientStop { position: 1; color: c.metalDark }
          }
        }
        Rectangle {    // a highlight along the top
          x: 4.4 * c.u
          y: 2.75 * c.u
          width: 5.2 * c.u
          height: 0.45 * c.u
          radius: height / 2
          color: "white"
          opacity: 0.28
          antialiasing: true
        }

        Rectangle {    // visor
          id: visor
          x: 4.0 * c.u
          y: 3.6 * c.u
          width: 8.0 * c.u
          height: 4.8 * c.u
          radius: 1.9 * c.u
          color: c.glass
          border.color: c.edge
          border.width: Math.max(1, 0.18 * c.u)
          antialiasing: true
          Rectangle {  // red tint on errors
            anchors.fill: parent
            radius: parent.radius
            color: "#FF3B3B"
            opacity: 0.22 * c.wError
          }
          Rectangle {  // glass reflection
            x: 0.6 * c.u
            y: 0.45 * c.u
            width: 1.7 * c.u
            height: 0.32 * c.u
            radius: height / 2
            color: "white"
            opacity: 0.16
            antialiasing: true
          }
        }

        AurenEye { u: c.u; led: c.led; x: 6.45 * c.u; y: 5.75 * c.u; shape: c.eyeShape; blink: c.blink; lookX: c.lookX; lookY: c.lookY }
        AurenEye { u: c.u; led: c.led; x: 9.55 * c.u; y: 5.75 * c.u; shape: c.eyeShape; blink: c.blink; lookX: c.lookX; lookY: c.lookY }

        // the LED mouth: a dim line, a smile when happy, an equalizer while speaking
        Rectangle {
          visible: c.wSpeak < 0.05 && !c.happy
          width: 1.3 * c.u
          height: 0.22 * c.u
          radius: height / 2
          x: 8 * c.u - width / 2
          y: 7.45 * c.u - height / 2
          color: c.led
          opacity: c.off ? 0.3 : 0.5
          antialiasing: true
        }
        Shape {
          visible: c.happy && c.wSpeak < 0.05
          x: 7 * c.u
          y: 6.9 * c.u
          width: 2 * c.u
          height: 1.2 * c.u
          preferredRendererType: Shape.CurveRenderer
          ShapePath {
            fillColor: "transparent"
            strokeColor: c.led
            strokeWidth: 0.26 * c.u
            capStyle: ShapePath.RoundCap
            startX: 0.15 * c.u; startY: 0.35 * c.u
            PathQuad { x: 1.85 * c.u; y: 0.35 * c.u; controlX: c.u; controlY: 1.3 * c.u }
          }
        }
        Row {
          visible: c.wSpeak >= 0.05
          opacity: c.wSpeak
          spacing: 0.2 * c.u
          x: 8 * c.u - width / 2
          y: 7.45 * c.u - 0.75 * c.u
          height: 1.5 * c.u
          Repeater {
            model: 5
            Rectangle {
              required property int index
              readonly property real hh: Math.min(1.45, 0.22 + c.mouthOpen * (0.7 + 0.9 * c.pulse(2.3 + index * 0.7, index * 0.21))
                                                   * (index === 2 ? 1.2 : index % 2 ? 1.0 : 0.75))
              width: 0.32 * c.u
              height: hh * c.u
              radius: width / 2
              y: (1.5 * c.u - height) / 2
              color: c.led
              antialiasing: true
            }
          }
        }
      }

      // working: a hologram keyboard under the hands
      Rectangle {
        visible: c.wWork > 0.01 && !c.mouse
        opacity: c.wWork
        x: 4.6 * c.u
        y: 11.0 * c.u
        width: 6.8 * c.u
        height: 1.0 * c.u
        radius: 0.3 * c.u
        color: Qt.rgba(0.5, 0.86, 1, 0.22)
        border.color: Qt.rgba(0.5, 0.86, 1, 0.75)
        border.width: Math.max(1, 0.12 * c.u)
        antialiasing: true
        Row {
          anchors.centerIn: parent
          spacing: 0.35 * c.u
          Repeater {
            model: 7
            Rectangle {
              required property int index
              width: 0.55 * c.u
              height: 0.3 * c.u
              radius: height / 2
              color: c.holo
              opacity: 0.45 + 0.55 * Math.max(0, c.osc(5, index * 0.37))
            }
          }
        }
      }

      // using the mouse: a pointer held in his right hand
      Pointer {
        m: c
        ink: c.edge
        visible: c.mouse && c.mood === "working"
        x: (c.handR.x - 0.5) * c.u
        y: (c.handR.y - 2.4) * c.u
      }

      // floating hands, in front of the head
      Repeater {
        model: [-1, 1]
        Rectangle {
          required property var modelData
          readonly property point p: modelData < 0 ? c.handL : c.handR
          width: 2.0 * c.u
          height: 1.8 * c.u
          radius: 0.9 * c.u
          x: (p.x - 1.0) * c.u
          y: (p.y - 0.9) * c.u
          border.color: c.edge
          border.width: Math.max(1, 0.18 * c.u)
          antialiasing: true
          gradient: Gradient {
            GradientStop { position: 0; color: c.metalLight }
            GradientStop { position: 1; color: c.metal }
          }
          Rectangle {    // a gold band at the wrist
            width: parent.width * 0.5
            height: 0.28 * c.u
            radius: height / 2
            x: (parent.width - width) / 2
            y: parent.height - height - 0.25 * c.u
            color: c.gold
            opacity: 0.9
          }
        }
      }
    }

    // waiting for a yes or no: a question mark
    Text {
      visible: c.wAsk > 0.01
      opacity: c.wAsk
      text: "?"
      color: c.gold
      font.family: "monospace"
      font.bold: true
      font.pixelSize: c.u * 4
      x: 13.6 * c.u
      y: -4.6 * c.u + c.osc(0.7) * c.u * 0.35 - c.hover * c.u
      rotation: 12
    }

    Zzz { m: c; color: c.gold; x: 12.6 * c.u; y: 1.4 * c.u }

    // the input area: head, antenna and hands
    Item {
      id: hit
      x: 0
      y: -1.2 * c.u
      width: 16 * c.u
      height: 13 * c.u
    }
  }
}

import QtQuick
import QtQuick.Shapes

// Clawd, the Claude Code mascot, drawn from the terminal logo's blocks:
//
//    ▐▛███▜▌      a 12x8 body with two tall eyes,
//   ▝▜█████▛▘     little arms on both sides,
//     ▘▘ ▝▝       and four legs.
//
// One quadrant block of the logo is 1 unit wide and 2 tall; `u` is the unit in pixels.
// Every pose is a sum of oscillators (driven by one clock) times mood weights that
// fade in and out, so moods blend into each other instead of jumping.
//
// The contract every mascot follows (shell.qml relies on it):
//   in:  u, mood, level, drowsy, asleep, mouse, awaiting, hovered, pointerX/Y, off, sleepAnimation
//   out: implicitWidth/Height, hitbox (an Item for the input mask), bubbleAnchorY
//   fn:  poke(), cheer()
Item {
  id: c

  property real u: 6
  property string mood: "idle"      // starting, idle, listening, thinking, working, speaking, error
  property real level: 0            // mic or voice loudness, 0..1
  property bool drowsy: false
  property bool asleep: false
  property bool sleepAnimation: true   // smooth breathing and drifting z's when faded or asleep (costs a bit of CPU)
  property bool mouse: false
  property bool awaiting: false
  property bool hovered: false
  property real pointerX: 0.5       // pointer over the body, 0..1
  property real pointerY: 0.5
  property bool off: false

  readonly property color skin: off ? "#8f8781" : "#D77757"
  readonly property color skinDark: off ? "#6f6863" : "#B8603F"
  readonly property color ink: "#1d1310"

  // The body sits inside a margin for effects (rings, dots, z, the hop).
  readonly property real padX: 4
  readonly property real padTop: 6
  implicitWidth: (16 + 2 * padX) * u
  implicitHeight: (10 + padTop + 2) * u
  readonly property Item hitbox: hit
  // where a speech bubble ends: above the thinking dots and the "?" when they show, otherwise near the head
  readonly property real bubbleAnchorY: (mood === "thinking" || awaiting) ? 0.6 * u : (padTop - 2.2) * u

  // ---- mood weights (0..1, eased) ----------------------------------------------------
  property real wBreath: off ? 0 : (mood === "idle" || mood === "speaking" || mood === "starting") ? 1 : 0.4
  property real wTall: mood === "listening" ? 1 : 0
  property real wListen: mood === "listening" ? 1 : 0
  property real wSway: mood === "thinking" ? 1 : 0
  property real wWork: mood === "working" ? 1 : 0
  property real wSpeak: mood === "speaking" ? 1 : 0
  property real wAsk: awaiting && mood === "idle" ? 1 : 0
  property real wSleep: asleep ? 1 : 0
  Behavior on wBreath { NumberAnimation { duration: 400 } }
  Behavior on wTall { NumberAnimation { duration: 220; easing.type: Easing.OutBack } }
  Behavior on wListen { NumberAnimation { duration: 250 } }
  Behavior on wSway { NumberAnimation { duration: 350 } }
  Behavior on wWork { NumberAnimation { duration: 250 } }
  Behavior on wSpeak { NumberAnimation { duration: 250 } }
  Behavior on wAsk { NumberAnimation { duration: 400; easing.type: Easing.OutBack } }
  Behavior on wSleep { NumberAnimation { duration: 900 } }

  // Smoothed loudness: quick to rise, slower to fall, like a VU meter.
  property real lv: 0
  onLevelChanged: if (level > lv) lv = level

  // ---- the clock -------------------------------------------------------------------------
  // Smooth (every frame) while auren is doing something, and always when sleepAnimation is on (Clawd keeps
  // breathing while faded and asleep). With it off: stepped at 10 fps for idle breathing, stopped once faded
  // or asleep, so a quiet mascot costs almost nothing.
  property real t: 0
  readonly property bool lively: !off && (mood !== "idle" || pokeAnim.running || shakeAnim.running
                                 || wTall > 0.01 || wSway > 0.01 || wWork > 0.01 || wSpeak > 0.01 || wAsk > 0.01 && wAsk < 0.99)
  function tick(dt) {
    t += dt
    lv = Math.max(level, lv - dt * 2.2)
  }
  FrameAnimation { running: c.lively || (c.sleepAnimation && !c.off); onTriggered: c.tick(frameTime) }
  Timer {
    running: !c.lively && !c.sleepAnimation && !c.off && (!c.drowsy || c.awaiting) && !c.asleep
    interval: 100
    repeat: true
    onTriggered: c.tick(0.1)
  }
  function osc(hz, phase) { return Math.sin(2 * Math.PI * (t * hz + (phase || 0))) }
  function pulse(hz, phase) { return 0.5 + 0.5 * osc(hz, phase) }

  // ---- one-off moves -----------------------------------------------------------------------
  property real hopA: 0        // in u
  property real squashA: 0     // + = squashed
  property real shakeA: 0      // in u
  property bool happy: false

  function poke() {
    if (off) return
    pokeAnim.restart()
    happyTimer.restart()
  }
  function cheer() { happyTimer.interval = 1100; happyTimer.restart() }

  Timer { id: happyTimer; interval: 1300; onRunningChanged: c.happy = running }

  SequentialAnimation {
    id: pokeAnim
    NumberAnimation { target: c; property: "squashA"; to: 0.55; duration: 90; easing.type: Easing.OutQuad }
    ParallelAnimation {
      NumberAnimation { target: c; property: "squashA"; to: -0.35; duration: 170; easing.type: Easing.OutQuad }
      NumberAnimation { target: c; property: "hopA"; to: 2.6; duration: 220; easing.type: Easing.OutQuad }
    }
    ParallelAnimation {
      NumberAnimation { target: c; property: "squashA"; to: 0; duration: 160 }
      NumberAnimation { target: c; property: "hopA"; to: 0; duration: 230; easing.type: Easing.InQuad }
    }
    NumberAnimation { target: c; property: "squashA"; to: 0.45; duration: 70; easing.type: Easing.OutQuad }
    NumberAnimation { target: c; property: "squashA"; to: 0; duration: 260; easing.type: Easing.OutBack }
  }

  SequentialAnimation {
    id: shakeAnim
    loops: 3
    NumberAnimation { target: c; property: "shakeA"; to: 0.8; duration: 45 }
    NumberAnimation { target: c; property: "shakeA"; to: -0.8; duration: 90 }
    NumberAnimation { target: c; property: "shakeA"; to: 0; duration: 45 }
  }

  // A little hop when a reply starts, a happy face when it ends, a shake on errors.
  property string lastMood: mood
  onMoodChanged: {
    if (mood === "speaking" && lastMood !== "speaking") pokeAnim.restart()
    if (lastMood === "speaking" && mood === "idle") cheer()
    if (lastMood === "starting" && mood === "idle") poke()
    if (mood === "error") shakeAnim.restart()
    lastMood = mood
  }

  // ---- blinking and glancing ---------------------------------------------------------------
  property real blink: 0
  SequentialAnimation {
    id: blinkAnim
    NumberAnimation { target: c; property: "blink"; to: 1; duration: 55 }
    PauseAnimation { duration: 40 }
    NumberAnimation { target: c; property: "blink"; to: 0; duration: 90 }
  }
  Timer {
    running: !c.off && c.mood !== "error"
    interval: 3200
    repeat: true
    onTriggered: {
      interval = 2200 + Math.random() * 4200
      if (!c.asleep) {
        blinkAnim.restart()
        if (Math.random() < 0.18) doubleBlink.start()
      }
    }
  }
  Timer { id: doubleBlink; interval: 260; onTriggered: blinkAnim.restart() }

  property real glanceX: 0
  property real glanceY: 0
  Timer {
    running: !c.off && !c.asleep && c.mood === "idle"
    interval: 4000
    repeat: true
    onTriggered: {
      interval = 2500 + Math.random() * 5500
      var r = Math.random()
      if (r < 0.45) { c.glanceX = 0; c.glanceY = 0 }
      else { c.glanceX = (Math.random() * 2 - 1); c.glanceY = (Math.random() * 1.2 - 0.6) }
    }
  }

  // Where the eyes look: the pointer when hovered, otherwise by mood.
  readonly property real lookXTarget: hovered ? (pointerX - 0.5) * 2
                                    : mood === "thinking" ? (osc(0.22) > 0 ? 0.8 : -0.8)
                                    : mood === "working" ? osc(0.35) * 0.6
                                    : mood === "listening" ? 0 : glanceX
  readonly property real lookYTarget: hovered ? (pointerY - 0.5) * 2
                                    : mood === "thinking" ? -0.9
                                    : mood === "working" ? 0.7
                                    : mood === "listening" ? -0.35 : glanceY
  property real lookX: lookXTarget
  property real lookY: lookYTarget
  Behavior on lookX { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
  Behavior on lookY { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }

  readonly property string eyeShape: off ? "closed"
                                   : mood === "error" ? "x"
                                   : happy ? "happy"
                                   : asleep ? "closed"
                                   : mood === "listening" ? "wide"
                                   : mood === "working" ? "focused"
                                   : drowsy && !hovered ? "sleepy"
                                   : "normal"

  // ---- the pose ----------------------------------------------------------------------------
  readonly property real breath: pulse(asleep ? 0.22 : 0.36)
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
        x: 2 * c.u - width * (scale - 1) / 2 * 0
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
          y: 8 * c.u - c.legLift(index) * c.u * 0.6 - (1 - c.stretchY) * 0
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
      Shape {
        visible: c.mouse && c.mood === "working"
        x: 15.2 * c.u
        y: (2.6 - c.armR) * c.u
        width: 3 * c.u
        height: 4 * c.u
        scale: 1 - 0.18 * Math.max(0, c.osc(1.6))
        transformOrigin: Item.TopLeft
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
          fillColor: "#ffffff"
          strokeColor: c.ink
          strokeWidth: Math.max(1, c.u * 0.25)
          joinStyle: ShapePath.RoundJoin
          startX: 0; startY: 0
          PathLine { x: 0; y: 3.4 * c.u }
          PathLine { x: 0.9 * c.u; y: 2.6 * c.u }
          PathLine { x: 1.5 * c.u; y: 3.9 * c.u }
          PathLine { x: 2.1 * c.u; y: 3.6 * c.u }
          PathLine { x: 1.5 * c.u; y: 2.4 * c.u }
          PathLine { x: 2.6 * c.u; y: 2.4 * c.u }
          PathLine { x: 0; y: 0 }
        }
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

    // asleep: z's drifting up
    Repeater {
      model: 2
      Text {
        id: zed
        required property int index
        visible: c.wSleep > 0.5
        text: "z"
        color: c.skin
        font.family: "monospace"
        font.bold: true
        font.pixelSize: c.u * (index ? 2.6 : 2)
        // smooth with sleepAnimation; otherwise stepped, three moves a second (cheap, pixel-art-like)
        property int step: index * 6
        property real smooth: 0
        readonly property real p: c.sleepAnimation ? smooth : (step % 16) / 10
        x: (13 + p * 2.5) * c.u
        y: (-0.5 - p * 4) * c.u
        opacity: p <= 1 ? Math.sin(Math.PI * p) * c.wSleep : 0
        Timer { running: zed.visible && !c.sleepAnimation; interval: 330; repeat: true; onTriggered: zed.step++ }
        SequentialAnimation on smooth {
          running: zed.visible && c.sleepAnimation
          loops: Animation.Infinite
          PauseAnimation { duration: zed.index * 1400 }
          NumberAnimation { from: 0; to: 1; duration: 2600 }
          PauseAnimation { duration: 2600 - zed.index * 1400 }
        }
      }
    }

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

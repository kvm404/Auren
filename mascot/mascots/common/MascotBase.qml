import QtQuick

// What every mascot shares: the contract with shell.qml and the bar plugin, the clock, mood weights,
// one-off moves (poke, shake, cheer), blinking, glancing and where the eyes look. A mascot is a
// MascotBase plus its own drawing (see auren/, veyra/).
//
// The contract (shell.qml and the bar plugin rely on it):
//   in:  u, mood, level, drowsy, asleep, sleepAnimation, mouse, awaiting, hovered, pointerX/Y, off
//   out: implicitWidth/Height, padX/padTop (where the stage starts), hitbox (an Item placed in stage
//        coordinates, for the input mask), bubbleAnchorY, accent (the mascot's colour)
//   fn:  poke(), cheer()
//
// Every pose is a sum of oscillators (driven by one clock, t) times mood weights that fade in and
// out, so moods blend into each other instead of jumping.
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

  // set by each mascot
  property real padX: 4             // the stage (the character's own grid) starts at (padX, padTop) units
  property real padTop: 6
  property Item hitbox: null
  property real bubbleAnchorY: 0
  property color accent: "#D77757"
  property bool extraLively: false  // a mascot's own one-off animation is running: keep the clock smooth

  // ---- mood weights (0..1, eased) ----------------------------------------------------
  property real wBreath: off ? 0 : (mood === "idle" || mood === "speaking" || mood === "starting") ? 1 : 0.4
  property real wTall: mood === "listening" ? 1 : 0
  property real wListen: mood === "listening" ? 1 : 0
  property real wSway: mood === "thinking" ? 1 : 0
  property real wWork: mood === "working" ? 1 : 0
  property real wSpeak: mood === "speaking" ? 1 : 0
  property real wAsk: awaiting && mood === "idle" ? 1 : 0
  property real wSleep: asleep ? 1 : 0
  property real wError: mood === "error" ? 1 : 0
  Behavior on wBreath { NumberAnimation { duration: 400 } }
  Behavior on wTall { NumberAnimation { duration: 220; easing.type: Easing.OutBack } }
  Behavior on wListen { NumberAnimation { duration: 250 } }
  Behavior on wSway { NumberAnimation { duration: 350 } }
  Behavior on wWork { NumberAnimation { duration: 250 } }
  Behavior on wSpeak { NumberAnimation { duration: 250 } }
  Behavior on wAsk { NumberAnimation { duration: 400; easing.type: Easing.OutBack } }
  Behavior on wSleep { NumberAnimation { duration: 900 } }
  Behavior on wError { NumberAnimation { duration: 250 } }

  // Smoothed loudness: quick to rise, slower to fall, like a VU meter.
  property real lv: 0
  onLevelChanged: if (level > lv) lv = level

  // ---- the clock -------------------------------------------------------------------------
  // Smooth (every frame) while auren is doing something, and always when sleepAnimation is on (the
  // mascot keeps breathing while faded and asleep). With it off: stepped at 10 fps for idle breathing,
  // stopped once faded or asleep, so a quiet mascot costs almost nothing.
  property real t: 0
  readonly property bool lively: !off && (mood !== "idle" || pokeAnim.running || shakeAnim.running || extraLively
                                 || wTall > 0.01 || wSway > 0.01 || wWork > 0.01 || wSpeak > 0.01 || wError > 0.01
                                 || wAsk > 0.01 && wAsk < 0.99)
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
  function mix(a, b, w) { return a + (b - a) * w }

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

  // Where the eyes look (-1..1): the pointer when hovered, otherwise by mood.
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

  // normal, wide, focused, sleepy, closed, happy (^), x (dizzy)
  readonly property string eyeShape: off ? "closed"
                                   : mood === "error" ? "x"
                                   : happy ? "happy"
                                   : asleep ? "closed"
                                   : mood === "listening" ? "wide"
                                   : mood === "working" ? "focused"
                                   : drowsy && !hovered ? "sleepy"
                                   : "normal"

  readonly property real breath: pulse(asleep ? 0.22 : 0.36)
}

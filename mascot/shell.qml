// auren's mascot: a small character floating over the desktop that shows what auren is doing.
//
//   qs -p ~/.local/share/auren/mascot      (the auren-mascot systemd user service runs this)
//
// It reads $XDG_RUNTIME_DIR/auren/state.json (what auren is doing) and .../level (how loud the
// mic or the voice is), and shows a speech bubble with the live transcript, what auren is
// doing, and the sentence being spoken.
//
//   drag            move it anywhere (the place is remembered)
//   click           talk to auren (like Super+A): click again to send; while auren speaks, stop it
//   right-click     a poke
//   scroll          make it bigger or smaller
//
// Faded when idle, asleep after a while. Which character shows is `mascot` in ~/.config/auren/mascot.json:
// auren or veyra (folders under mascots/, all built on mascots/common/MascotBase.qml); it switches live.
// Remote control for the bar plugin:
//   qs -p ~/.local/share/auren/mascot ipc call mascot toggle|show|hide|poke|reset|size <0.6..2.2>
import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import "mascots/veyra"
import "mascots/auren"

ShellRoot {
  id: root

  readonly property string runtimeDir: Quickshell.env("AUREN_RUNTIME_DIR") || Quickshell.env("XDG_RUNTIME_DIR") + "/auren"
  readonly property string home: Quickshell.env("HOME")
  readonly property string configDir: (Quickshell.env("XDG_CONFIG_HOME") || home + "/.config") + "/auren"
  readonly property string stateDir: (Quickshell.env("XDG_STATE_HOME") || home + "/.local/state") + "/auren"
  readonly property string dataDir: (Quickshell.env("XDG_DATA_HOME") || home + "/.local/share") + "/auren"
  readonly property string ctl: dataDir + "/bin/auren-ctl"

  // ---- preferences (~/.config/auren/mascot.json, optional) and place (~/.local/state/auren/mascot.json)
  FileView {
    path: root.configDir + "/mascot.json"
    watchChanges: true
    onFileChanged: reload()
    printErrors: false
    onLoaded: { root.prefsReady = true; Qt.callLater(root.restorePosition) }
    onLoadFailed: { root.prefsReady = true; Qt.callLater(root.restorePosition) }
    JsonAdapter {
      id: prefs
      property real idleOpacity: 0.45     // how faded it gets when nothing is happening
      property int fadeAfter: 12          // seconds of quiet before fading
      property int sleepAfter: 240        // seconds of quiet before falling asleep
      property bool bubble: true          // speech bubble with transcript and captions
      property bool sleepAnimation: true  // keep breathing (and drifting z's) when faded or asleep; false saves CPU
      property string mascot: "auren"     // auren or veyra
    }
  }

  FileView {
    id: placeFile
    path: root.stateDir + "/mascot.json"
    printErrors: false
    atomicWrites: true
    onAdapterUpdated: if (root.placeReady) Qt.callLater(placeFile.writeAdapter)
    onLoaded: { root.placeReady = true; Qt.callLater(root.restorePosition) }
    onLoadFailed: { root.placeReady = true; Qt.callLater(root.restorePosition) }
    JsonAdapter {
      id: place
      property real x: -1
      property real y: -1
      property real size: 1
      property bool hidden: false
    }
  }

  // ---- auren's state ---------------------------------------------------------------------------
  property var st: ({ state: "off", detail: "" })
  readonly property string mood: daemonAlive ? (st.state || "idle") : "off"
  property bool daemonAlive: false

  FileView {
    id: stateFile
    path: root.runtimeDir + "/state.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    property string last: ""
    onLoaded: {
      if (text() === last) return       // re-read every second: only act on a change
      last = text()
      try { root.st = JSON.parse(last) } catch (e) {}
      root.daemonAlive = root.st.state !== "off"
      liveness.check()
    }
    onLoadFailed: { root.st = { state: "off", detail: "" }; root.daemonAlive = false }
  }

  // The watch sees the daemon's write-then-rename updates; this slow re-read is only a safety net.
  Timer { interval: 10000; running: true; repeat: true; onTriggered: stateFile.reload() }

  // Is the daemon really running (it could have crashed without writing "off")?
  FileView {
    id: liveness
    path: root.st.pid ? "/proc/" + root.st.pid + "/cmdline" : ""
    printErrors: false
    function check() { if (path) reload() }
    onLoaded: root.daemonAlive = root.st.state !== "off" && text().indexOf("auren") >= 0
    onLoadFailed: root.daemonAlive = false
  }
  Timer { interval: 5000; running: true; repeat: true; onTriggered: liveness.check() }

  property real level: 0
  FileView {
    id: levelFile
    path: root.runtimeDir + "/level"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.level = Math.max(0, Math.min(1, parseFloat(text()) || 0))
  }

  // ---- theme colours for the bubble (Omarchy's current theme) -------------------------------
  property color bg: "#101315"
  property color fg: "#cacccc"
  property color accent: "#D77757"
  FileView {
    path: root.home + "/.local/state/omarchy/current/theme/colors.toml"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      var lines = text().split("\n")
      for (var i = 0; i < lines.length; i++) {
        var m = lines[i].match(/^\s*(background|foreground|accent)\s*=\s*"(#[0-9a-fA-F]{6})"/)
        if (m) root[m[1] === "background" ? "bg" : m[1] === "foreground" ? "fg" : "accent"] = m[2]
      }
    }
  }

  // ---- idle: fade, then sleep --------------------------------------------------------------------
  property real quietSince: Date.now() / 1000
  property real now: Date.now() / 1000
  Timer { interval: 1000; running: true; repeat: true; onTriggered: root.now = Date.now() / 1000 }
  onMoodChanged: if (mood !== "idle") quietSince = Date.now() / 1000
  onHoveredChanged: quietSince = Date.now() / 1000     // touching it wakes it up for a while
  onDraggingChanged: quietSince = Date.now() / 1000
  readonly property bool active: mood !== "idle" && mood !== "off"
  readonly property bool awaiting: mood === "idle" && st.awaiting === true && (st.until || 0) > now
  readonly property bool handsfree: st.handsfree === "on"      // the name works instead of the key
  readonly property bool quiet: !active && !awaiting && !hovered && !dragging
  readonly property bool drowsy: quiet && now - quietSince > prefs.fadeAfter
  readonly property bool asleep: (quiet && now - quietSince > prefs.sleepAfter) || mood === "off"

  // ---- the window ------------------------------------------------------------------------------
  property bool hovered: false
  readonly property bool dragging: area.dragging
  readonly property real u: Math.round(6 * Math.max(0.6, Math.min(2.2, place.size)) * 2) / 2
  property real posX: 0
  property real posY: 0
  property bool prefsReady: false
  property bool placeReady: false
  property bool componentReady: false
  property bool positionReady: false
  readonly property real viewportWidth: win.screen ? win.screen.width : 0
  readonly property real viewportHeight: win.screen ? win.screen.height : 0

  function clampPos() {
    if (!positionReady) return
    posX = Math.max(0, Math.min(Math.max(0, viewportWidth - mascot.implicitWidth), posX))
    posY = Math.max(0, Math.min(Math.max(0, viewportHeight - mascot.implicitHeight), posY))
  }
  function savePos() {
    if (!positionReady) return
    place.x = Math.round(posX)
    place.y = Math.round(posY)
  }
  function restorePosition() {
    if (positionReady || !componentReady || !prefsReady || !placeReady || !mascotLoader.item || viewportWidth <= 0 || viewportHeight <= 0) return
    posX = isFinite(place.x) && place.x >= 0 ? place.x : viewportWidth - mascot.implicitWidth - 40
    posY = isFinite(place.y) && place.y >= 0 ? place.y : viewportHeight - mascot.implicitHeight - 40
    positionReady = true
    clampPos()
    savePos()
  }
  function geometryChanged() {
    area.cancelDrag()
    if (viewportWidth <= 0 || viewportHeight <= 0) return
    if (!positionReady) { restorePosition(); return }
    clampPos()
    savePos()
  }
  Component.onCompleted: { componentReady = true; Qt.callLater(root.restorePosition) }
  onViewportWidthChanged: Qt.callLater(root.geometryChanged)
  onViewportHeightChanged: Qt.callLater(root.geometryChanged)
  onUChanged: Qt.callLater(root.geometryChanged)
  onKindChanged: Qt.callLater(root.geometryChanged)

  // ---- the character -------------------------------------------------------------------------
  readonly property string kind: ["auren", "veyra"].indexOf(prefs.mascot) >= 0 ? prefs.mascot : "auren"
  readonly property Item mascot: mascotLoader.item || placeholder
  Item { id: placeholder; property real u: 6; property real padX: 4; property real padTop: 6; implicitWidth: 144; implicitHeight: 108
         property Item hitbox: placeholder; property real bubbleAnchorY: 0; property string mood: "idle"
         function poke() {} }
  Component { id: veyraComponent; Veyra {} }
  Component { id: aurenComponent; Auren {} }
  function bindMascot(m) {
    m.u = Qt.binding(() => root.u)
    m.mood = Qt.binding(() => root.mood === "off" ? "idle" : root.mood)
    m.off = Qt.binding(() => root.mood === "off")
    m.level = Qt.binding(() => root.level)
    m.drowsy = Qt.binding(() => root.drowsy)
    m.asleep = Qt.binding(() => root.asleep)
    m.sleepAnimation = Qt.binding(() => prefs.sleepAnimation)
    m.mouse = Qt.binding(() => root.st.mouse === true)
    m.awaiting = Qt.binding(() => root.awaiting)
    m.hovered = Qt.binding(() => root.hovered)
    m.pointerX = Qt.binding(() => area.mouseX / area.width)
    m.pointerY = Qt.binding(() => area.mouseY / area.height)
    Qt.callLater(root.restorePosition)
    Qt.callLater(root.geometryChanged)
  }
  Connections {
    target: root.mascot
    function onImplicitWidthChanged() { Qt.callLater(root.geometryChanged) }
    function onImplicitHeightChanged() { Qt.callLater(root.geometryChanged) }
  }

  PanelWindow {
    id: win
    visible: root.positionReady && !place.hidden
    onVisibleChanged: if (!visible && area) area.cancelDrag()
    onScreenChanged: Qt.callLater(root.geometryChanged)
    color: "transparent"
    WlrLayershell.namespace: "auren-mascot"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    exclusionMode: ExclusionMode.Ignore

    // A fixed surface avoids layer-shell reconfiguration and ambiguous event coordinates at drag
    // start/end. Everything outside the hitbox is click-through, except while a button is held.
    anchors { left: true; top: true; right: true; bottom: true }
    margins { left: 0; top: 0; right: 0; bottom: 0 }
    mask: Region {
      x: area.held ? 0 : Math.floor(root.posX + area.x)
      y: area.held ? 0 : Math.floor(root.posY + area.y)
      width: area.held ? win.width : Math.ceil(root.posX + area.x + area.width) - x
      height: area.held ? win.height : Math.ceil(root.posY + area.y + area.height) - y
    }

    Item {
      id: holder
      x: root.posX
      y: root.posY
      width: mascot.implicitWidth
      height: mascot.implicitHeight
      opacity: root.mood === "off" ? 0.4 : root.drowsy ? prefs.idleOpacity : 1
      Behavior on opacity { NumberAnimation { duration: 700; easing.type: Easing.InOutQuad } }
      layer.enabled: opacity < 0.999      // fade as one picture, so overlapping parts don't show through

      Loader {
        id: mascotLoader
        sourceComponent: root.kind === "veyra" ? veyraComponent : aurenComponent
        onLoaded: root.bindMascot(item)
      }

      MascotDragArea {
        id: area
        x: mascot.hitbox.x + mascot.padX * mascot.u
        y: mascot.hitbox.y + mascot.padTop * mascot.u
        width: mascot.hitbox.width
        height: mascot.hitbox.height
        coordinateSpace: win.contentItem
        targetX: root.posX
        targetY: root.posY
        maximumX: root.viewportWidth - mascot.implicitWidth
        maximumY: root.viewportHeight - mascot.implicitHeight
        onEntered: root.hovered = true
        onExited: root.hovered = false
        onMoved: (x, y) => { root.posX = x; root.posY = y }
        onDropped: root.savePos()
        onTapped: (button) => {
          if (button === Qt.LeftButton) root.clicked()
          else if (button === Qt.RightButton) mascot.poke()
        }
        onWheel: (wheel) => {
          if (held || wheel.angleDelta.y === 0) return
          var f = wheel.angleDelta.y > 0 ? 1.1 : 1 / 1.1
          place.size = Math.max(0.6, Math.min(2.2, place.size * f))
        }
      }

    }
  }

  function clicked() {
    if (mood === "off") {
      Quickshell.execDetached(["systemctl", "--user", "start", "auren"])
      mascot.poke()
    } else if (mood === "speaking" || mood === "thinking" || mood === "working") {
      Quickshell.execDetached([ctl, "cancel"])      // stop talking, or stop the answer in progress
    } else if (mood === "idle" || mood === "listening") {
      Quickshell.execDetached([ctl, "talk"])        // start listening, or stop and send
    } else {
      mascot.poke()                                  // starting or error: nothing to toggle
    }
  }

  // ---- the speech bubble ----------------------------------------------------------------------
  property string lastCaption: ""
  property real captionUntil: 0
  readonly property string bubbleText: {
    if (!prefs.bubble || dragging) return ""
    var d = st.detail || ""
    var stop = handsfree ? "\nSay Auren or Super+A to stop" : "\nPress Super+A to stop"
    switch (mood) {
      case "listening":
        if (st.wake) return (d || (st.followUp ? "Go on, I'm listening…" : "Listening…")) + "\nPause when you're done"
        return d || (st.fresh ? "Fresh start · listening…" : "Listening…") + "\nPress again to send"
      case "thinking": return (st.transcript || d || "Thinking…") + stop
      case "working": return (d || "Working…") + stop
      case "speaking": return d
      case "error": return d ? "Oops: " + d : "Something went wrong"
      case "starting": return "Waking up…"
    }
    if (awaiting) return (st.question || "Waiting for your answer")
                         + (handsfree ? "\nSay Auren, then yes or no" : "\nPress Super+A to answer yes or no")
    if (mood === "idle" && d === "heard nothing" && now - (st.since || 0) < 3) return "Didn't catch that"
    if (mood === "idle" && d === "cancelled" && now - (st.since || 0) < 2.5) return "Stopped"
    if (mood === "idle" && now < captionUntil) return lastCaption
    if (hovered && hoverHint.shown) return mood === "off" ? "Auren is off: click to start"
                                         : (handsfree ? "Say “Auren” or click to talk" : "Click to talk") + " · drag · scroll to resize"
    return ""
  }
  // the control hints that bubbleText appends; shown pinned under the scrolling text
  readonly property var bubbleHints: ["Press again to send", "Press Super+A to stop", "Press Super+A to answer yes or no",
                                      "Pause when you're done", "Say Auren or Super+A to stop", "Say Auren, then yes or no"]
  readonly property string bubbleHint: {
    for (var i = 0; i < bubbleHints.length; i++)
      if (bubbleText.endsWith("\n" + bubbleHints[i])) return bubbleHints[i]
    return ""
  }
  readonly property string bubbleBody: bubbleHint ? bubbleText.slice(0, bubbleText.length - bubbleHint.length - 1) : bubbleText
  readonly property bool bubbleMuted: mood === "thinking" || mood === "working" || mood === "starting"
                                     || (mood === "listening" && !st.detail) || (hovered && !active && !awaiting)
  // keep the last spoken sentence up for a moment after auren stops talking
  property string spokeLast: ""
  Connections {
    target: root
    function onStChanged() {
      if (root.st.state === "speaking" && root.st.detail) root.spokeLast = root.st.detail
      if (root.st.state === "idle" && root.spokeLast) {
        root.lastCaption = root.spokeLast
        root.captionUntil = Date.now() / 1000 + 2.5
        root.spokeLast = ""
      }
    }
  }
  Timer { id: hoverHint; property bool shown: false; interval: 700; running: root.hovered; onRunningChanged: if (running) shown = false; onTriggered: shown = true }

  PanelWindow {
    id: bubbleWin
    visible: win.visible && root.bubbleText !== "" && win.screen !== null
    screen: win.screen
    color: "transparent"
    WlrLayershell.namespace: "auren-mascot-bubble"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    exclusionMode: ExclusionMode.Ignore
    mask: Region {}                        // clicks go through to whatever is below

    readonly property real mascotCenter: root.posX + mascot.implicitWidth / 2
    readonly property bool above: root.posY + mascot.bubbleAnchorY - implicitHeight > 8
    anchors { left: true; top: true }
    margins {
      left: Math.max(6, Math.min((screen ? screen.width : 1920) - implicitWidth - 6, mascotCenter - implicitWidth / 2))
      top: above ? root.posY + mascot.bubbleAnchorY - implicitHeight : root.posY + mascot.implicitHeight - mascot.u
    }
    implicitWidth: card.width
    implicitHeight: card.height + tail.height

    Rectangle {
      id: card
      y: bubbleWin.above ? 0 : tail.height
      width: Math.min(300, measure.implicitWidth + 24)
      height: column.implicitHeight + 16
      radius: 10
      color: Qt.rgba(root.bg.r, root.bg.g, root.bg.b, 0.97)
      border.color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.8)
      border.width: 1

      readonly property color textColor: root.bubbleMuted ? Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.7) : root.fg
      readonly property int maxLines: 5

      // unwrapped copy of the text: gives the natural width, capped at the wrap width
      Text {
        id: measure
        visible: false
        text: root.bubbleText
        font: body.font
      }

      Column {
        id: column
        x: 12
        y: 8
        width: Math.min(276, measure.implicitWidth)

        // the hint ("Press ... ") stays pinned under the text; only the body scrolls
        Item {
          id: viewport
          width: parent.width
          clip: true
          // the body shows at most maxLines (minus the hint line); longer text slides up so the newest words stay in view
          height: Math.min(body.contentHeight, Math.max(1, card.maxLines - (hint.visible ? 1 : 0)) * body.lineHeightPx)

          Text {
            id: body
            width: parent.width
            // lineCount * line height: contentHeight is the wrapped height of the full text
            readonly property real lineHeightPx: lineCount > 0 ? contentHeight / lineCount : 16
            text: root.bubbleBody
            color: card.textColor
            font.family: "monospace"
            font.pixelSize: 13
            font.italic: root.bubbleMuted
            wrapMode: Text.Wrap
            readonly property real targetY: viewport.height - contentHeight
            // slide only while the same text grows; a new/shorter text snaps so it never starts off-screen
            property string prevText: ""
            property bool growing: false
            onTextChanged: {
              var t = body.text
              growing = prevText !== "" && t.length > prevText.length && t.startsWith(prevText)
              prevText = t
              Qt.callLater(body.updatePosition)
            }
            onTargetYChanged: Qt.callLater(body.updatePosition)
            Component.onCompleted: Qt.callLater(body.updatePosition)
            // Wait for wrapping and the text-change handler before deciding whether to slide.
            // A Behavior can start with the previous message's growing flag during layout.
            function updatePosition() {
              if (growing && y !== targetY) {
                if (slide.running && slide.to === targetY) return
                slide.stop()
                slide.to = targetY
                slide.start()
              } else {
                slide.stop()
                y = targetY
              }
            }
            NumberAnimation { id: slide; target: body; property: "y"; duration: 140; easing.type: Easing.OutCubic }
          }
        }

        Text {
          id: hint
          visible: text !== ""
          width: parent.width
          text: root.bubbleHint
          color: card.textColor
          font: body.font
          wrapMode: Text.Wrap
        }
      }
    }

    // the little tail pointing at the mascot
    Rectangle {
      id: tail
      width: 10
      height: 10
      rotation: 45
      color: card.color
      border.color: card.border.color
      border.width: 1
      x: Math.max(12, Math.min(card.width - 22, bubbleWin.mascotCenter - bubbleWin.margins.left - 5))
      y: bubbleWin.above ? card.height - 5 : 5
      z: -1
    }
  }

  // ---- remote control (the bar plugin) ------------------------------------------------------------
  IpcHandler {
    target: "mascot"
    function show(): string { place.hidden = false; return "shown" }
    function hide(): string { place.hidden = true; return "hidden" }
    function toggle(): string { place.hidden = !place.hidden; return place.hidden ? "hidden" : "shown" }
    function poke(): string { mascot.poke(); return "poked" }
    function size(value: real): string { place.size = Math.max(0.6, Math.min(2.2, value)); return String(place.size) }
    function reset(): string {
      area.cancelDrag()
      place.size = 1
      if (!win.screen) return "no screen"
      root.posX = win.screen.width - mascot.implicitWidth - 40
      root.posY = win.screen.height - mascot.implicitHeight - 40
      root.savePos()
      return "reset"
    }
    function state(): string { return JSON.stringify({ hidden: place.hidden, size: place.size, x: root.posX, y: root.posY, mood: mascot.mood, mascot: root.kind }) }
  }
}

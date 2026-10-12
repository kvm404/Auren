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
// clawd, veyra or auren (folders under mascots/, all built on mascots/common/MascotBase.qml); it switches live.
// Remote control for the bar plugin:
//   qs -p ~/.local/share/auren/mascot ipc call mascot toggle|show|hide|poke|reset|size <0.6..2.2>
import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import "mascots/clawd"
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
    JsonAdapter {
      id: prefs
      property real idleOpacity: 0.45     // how faded it gets when nothing is happening
      property int fadeAfter: 12          // seconds of quiet before fading
      property int sleepAfter: 240        // seconds of quiet before falling asleep
      property bool bubble: true          // speech bubble with transcript and captions
      property bool sleepAnimation: true  // keep breathing (and drifting z's) when faded or asleep; false saves CPU
      property string mascot: "clawd"     // clawd, veyra or auren
    }
  }

  FileView {
    id: placeFile
    path: root.stateDir + "/mascot.json"
    printErrors: false
    atomicWrites: true
    onAdapterUpdated: writeAdapter()
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
  readonly property bool quiet: !active && !awaiting && !hovered && !dragging
  readonly property bool drowsy: quiet && now - quietSince > prefs.fadeAfter
  readonly property bool asleep: (quiet && now - quietSince > prefs.sleepAfter) || mood === "off"

  // ---- the window ------------------------------------------------------------------------------
  property bool hovered: false
  property bool dragging: false
  readonly property real u: Math.round(6 * Math.max(0.6, Math.min(2.2, place.size)) * 2) / 2
  property real posX: 0
  property real posY: 0

  function clampPos() {
    var s = win.screen
    if (!s) return
    posX = Math.max(0, Math.min(s.width - mascot.implicitWidth, posX))
    posY = Math.max(0, Math.min(s.height - mascot.implicitHeight, posY))
  }
  function savePos() {
    place.x = Math.round(posX)
    place.y = Math.round(posY)
  }
  Component.onCompleted: placeTimer.start()
  Timer {       // the place file loads asynchronously; position once it has (or defaults bottom-right)
    id: placeTimer
    interval: 150
    onTriggered: {
      var s = win.screen
      root.posX = place.x >= 0 ? place.x : s.width - mascot.implicitWidth - 40
      root.posY = place.y >= 0 ? place.y : s.height - mascot.implicitHeight - 40
      root.clampPos()
    }
  }
  onUChanged: clampPos()

  // ---- the character -------------------------------------------------------------------------
  readonly property string kind: ["clawd", "veyra", "auren"].indexOf(prefs.mascot) >= 0 ? prefs.mascot : "clawd"
  readonly property Item mascot: mascotLoader.item || placeholder
  Item { id: placeholder; property real u: 6; property real padX: 4; property real padTop: 6; implicitWidth: 144; implicitHeight: 108
         property Item hitbox: placeholder; property real bubbleAnchorY: 0; property string mood: "idle"
         function poke() {} }
  Component { id: clawdComponent; Clawd {} }
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
    Qt.callLater(root.clampPos)
  }

  PanelWindow {
    id: win
    visible: !place.hidden
    color: "transparent"
    WlrLayershell.namespace: "auren-mascot"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    exclusionMode: ExclusionMode.Ignore

    // While dragging, the window covers the whole screen and the mascot moves inside it (smooth,
    // no compositor round trip per mouse move). Otherwise it is just the mascot's size.
    anchors { left: true; top: true; right: root.dragging; bottom: root.dragging }
    margins { left: root.dragging ? 0 : root.posX; top: root.dragging ? 0 : root.posY }
    implicitWidth: mascot.implicitWidth
    implicitHeight: mascot.implicitHeight
    readonly property bool full: width > mascot.implicitWidth + 2

    mask: Region { item: mascot.hitbox }

    Item {
      id: holder
      x: win.full ? root.posX : 0
      y: win.full ? root.posY : 0
      width: mascot.implicitWidth
      height: mascot.implicitHeight
      opacity: root.mood === "off" ? 0.4 : root.drowsy ? prefs.idleOpacity : 1
      Behavior on opacity { NumberAnimation { duration: 700; easing.type: Easing.InOutQuad } }
      layer.enabled: opacity < 0.999      // fade as one picture, so overlapping parts don't show through

      Loader {
        id: mascotLoader
        sourceComponent: root.kind === "veyra" ? veyraComponent : root.kind === "auren" ? aurenComponent : clawdComponent
        onLoaded: root.bindMascot(item)
      }

      MouseArea {
        id: area
        x: mascot.hitbox.x + mascot.padX * mascot.u
        y: mascot.hitbox.y + mascot.padTop * mascot.u
        width: mascot.hitbox.width
        height: mascot.hitbox.height
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        cursorShape: root.dragging ? Qt.ClosedHandCursor : Qt.PointingHandCursor

        property real pressX: 0
        property real pressY: 0
        property real startX: 0
        property real startY: 0
        property real lastX: 0          // the pointer's last position on screen
        property real lastY: 0

        // The pointer's position on screen. Events come in window coordinates, and while a drag starts the
        // window is either still mascot-sized at (startX, startY) or already full screen at (0, 0): the
        // compositor applies the switch a moment after Qt learns the new size, so Qt can't tell which one an
        // event belongs to. Take the reading that continues smoothly from the last one.
        function screenPos(mouse) {
          var w = area.mapToItem(null, mouse.x, mouse.y)
          var ax = w.x + startX, ay = w.y + startY
          var small = Math.abs(ax - lastX) + Math.abs(ay - lastY)
          var full = Math.abs(w.x - lastX) + Math.abs(w.y - lastY)
          return small <= full ? Qt.point(ax, ay) : Qt.point(w.x, w.y)
        }

        onEntered: root.hovered = true
        onExited: root.hovered = false
        onPositionChanged: (mouse) => {
          mascot.pointerX = mouse.x / width
          mascot.pointerY = mouse.y / height
          if (!pressed) return
          var wp = area.mapToItem(null, mouse.x, mouse.y)
          if (!root.dragging && Math.abs(wp.x - pressX) + Math.abs(wp.y - pressY) > 6) {
            root.dragging = true
          }
          if (root.dragging) {
            var p = screenPos(mouse)
            lastX = p.x; lastY = p.y
            root.posX = p.x - pressX
            root.posY = p.y - pressY
            root.clampPos()
          }
        }
        onPressed: (mouse) => {
          startX = root.posX; startY = root.posY
          var w = area.mapToItem(null, mouse.x, mouse.y)     // the window is mascot-sized: window + its place
          pressX = w.x; pressY = w.y                          // where in the mascot it was grabbed
          lastX = startX + w.x; lastY = startY + w.y
        }
        onReleased: (mouse) => {
          if (root.dragging) {
            root.dragging = false
            root.savePos()
          } else if (mouse.button === Qt.LeftButton) {
            root.clicked()
          } else if (mouse.button === Qt.RightButton) {
            mascot.poke()
          }
        }
        onWheel: (wheel) => {
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
    } else if (mood === "speaking") {
      Quickshell.execDetached([ctl, "cancel"])
    } else if (mood === "idle" || mood === "listening") {
      Quickshell.execDetached([ctl, "talk"])        // start listening, or stop and send
    } else {
      mascot.poke()                                  // thinking or working: nothing to toggle
    }
  }

  // ---- the speech bubble ----------------------------------------------------------------------
  property string lastCaption: ""
  property real captionUntil: 0
  readonly property string bubbleText: {
    if (!prefs.bubble || dragging) return ""
    var d = st.detail || ""
    switch (mood) {
      case "listening": return d || (st.fresh ? "Fresh start · listening…" : "Listening…") + "\nPress again to send"
      case "thinking": return st.transcript || d || "Thinking…"
      case "working": return d || "Working…"
      case "speaking": return d
      case "error": return d ? "Oops: " + d : "Something went wrong"
      case "starting": return "Waking up…"
    }
    if (awaiting) return (st.question || "Waiting for your answer") + "\nPress Super+A to answer yes or no"
    if (mood === "idle" && d === "heard nothing" && now - (st.since || 0) < 3) return "Didn't catch that"
    if (mood === "idle" && now < captionUntil) return lastCaption
    if (hovered && hoverHint.shown) return mood === "off" ? "Auren is off: click to start" : "Click to talk · drag · scroll to resize"
    return ""
  }
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
    visible: !place.hidden && root.bubbleText !== "" && win.screen !== null
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
      width: Math.min(300, label.implicitWidth + 24)
      height: label.implicitHeight + 16
      radius: 10
      color: Qt.rgba(root.bg.r, root.bg.g, root.bg.b, 0.97)
      border.color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.8)
      border.width: 1

      Text {
        id: label
        x: 12
        y: 8
        width: Math.min(276, implicitWidth)
        text: root.bubbleText
        color: root.bubbleMuted ? Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.7) : root.fg
        font.family: "monospace"
        font.pixelSize: 13
        font.italic: root.bubbleMuted
        wrapMode: Text.Wrap
        maximumLineCount: 5
        elide: Text.ElideLeft
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
      place.size = 1
      root.posX = win.screen.width - mascot.implicitWidth - 40
      root.posY = win.screen.height - mascot.implicitHeight - 40
      root.savePos()
      return "reset"
    }
    function state(): string { return JSON.stringify({ hidden: place.hidden, size: place.size, x: root.posX, y: root.posY, mood: mascot.mood, mascot: root.kind }) }
  }
}

// Auren's mascot on macOS: the same characters as shell.qml (Omarchy's Quickshell version), in two
// plain Qt windows hosted by src/auren/mac/mascot.py (PySide6). That host gives this file `auren`:
//
//   auren.state        the daemon's state.json, parsed    auren.level      mic or voice loudness, 0..1
//   auren.alive        the daemon is running              auren.prefs      ~/.config/auren/mascot.json
//   auren.place        position, size, hidden (~/.local/state/auren/mascot.json)
//   auren.talkKey      the talk key as macOS writes it (⌃⌥A)
//   auren.ctl(cmd)     run an auren-ctl command           auren.savePlace(x, y, size, hidden)
//   auren.startDaemon()                                   auren.pin(window)  float on every Space
//
// The mascot's window is just its own size and moves when it's dragged (macOS lets clicks through the
// transparent parts). The bubble is a second window beside it that ignores the mouse, except while it
// shows Yes and No. Changes to the bubble's look belong in shell.qml too.
import QtQuick
import QtQuick.Window
import "mascots/veyra"
import "mascots/auren"

Item {
  id: root

  readonly property var prefs: auren.prefs
  readonly property var st: auren.state
  readonly property bool daemonAlive: auren.alive
  readonly property string mood: daemonAlive ? (st.state || "idle") : "off"
  readonly property real level: auren.level
  readonly property string key: auren.talkKey

  // the macOS look: dark card, Auren's orange
  property color bg: "#1c1c1e"
  property color fg: "#e5e5e7"
  property color accent: "#D77757"

  // ---- idle: fade, then sleep --------------------------------------------------------------------
  property real quietSince: Date.now() / 1000
  property real now: Date.now() / 1000
  Timer { interval: 1000; running: true; repeat: true; onTriggered: root.now = Date.now() / 1000 }
  onMoodChanged: if (mood !== "idle") quietSince = Date.now() / 1000
  onHoveredChanged: quietSince = Date.now() / 1000
  onDraggingChanged: quietSince = Date.now() / 1000
  readonly property bool active: mood !== "idle" && mood !== "off"
  readonly property bool awaiting: mood === "idle" && st.awaiting === true && (st.until || 0) > now
  readonly property bool quiet: !active && !awaiting && !hovered && !dragging
  readonly property bool drowsy: quiet && now - quietSince > (prefs.fadeAfter || 12)
  readonly property bool asleep: (quiet && now - quietSince > (prefs.sleepAfter || 240)) || mood === "off"

  // ---- the character -------------------------------------------------------------------------
  property bool hovered: false
  property bool dragging: false
  readonly property real size: Math.max(0.6, Math.min(2.2, auren.place.size || 1))
  readonly property real u: Math.round(6 * size * 2) / 2
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
    m.sleepAnimation = Qt.binding(() => prefs.sleepAnimation !== false)
    m.mouse = Qt.binding(() => root.st.mouse === true)
    m.awaiting = Qt.binding(() => root.awaiting)
    m.hovered = Qt.binding(() => root.hovered)
    m.pointerX = Qt.binding(() => area.mouseX / Math.max(1, area.width))
    m.pointerY = Qt.binding(() => area.mouseY / Math.max(1, area.height))
  }

  // ---- where it is -----------------------------------------------------------------------------
  readonly property rect desk: Qt.rect(win.screen ? win.screen.virtualX : 0, win.screen ? win.screen.virtualY : 0,
                                       win.screen ? win.screen.desktopAvailableWidth : 1440,
                                       win.screen ? win.screen.desktopAvailableHeight : 900)
  function clamp(x, y) {
    var top = 30                                        // under the menu bar
    return Qt.point(Math.max(desk.x, Math.min(desk.x + desk.width - win.width, x)),
                    Math.max(desk.y + top, Math.min(desk.y + desk.height - win.height, y)))
  }
  function home() {
    var p = auren.place
    if (isFinite(p.x) && p.x >= 0 && isFinite(p.y) && p.y >= 0) return clamp(p.x, p.y)
    return clamp(desk.x + desk.width - win.width - 40, desk.y + desk.height - win.height - 40)
  }
  function save() { auren.savePlace(win.x, win.y, root.size, auren.place.hidden === true) }

  Window {
    id: win
    title: "Auren mascot"
    transientParent: null      // shown by itself: the root is an Item, not a window
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
           | Qt.NoDropShadowWindowHint
    color: "transparent"
    width: root.mascot.implicitWidth
    height: root.mascot.implicitHeight
    visible: auren.place.hidden !== true
    Component.onCompleted: { var p = root.home(); x = p.x; y = p.y; auren.pin(win) }
    onVisibleChanged: if (visible) auren.pin(win)
    onWidthChanged: if (!root.dragging) { var p = root.clamp(x, y); x = p.x; y = p.y }
    onHeightChanged: if (!root.dragging) { var p = root.clamp(x, y); x = p.x; y = p.y }

    Item {
      id: holder
      anchors.fill: parent
      opacity: root.mood === "off" ? 0.4 : root.drowsy ? (root.prefs.idleOpacity || 0.45) : 1
      Behavior on opacity { NumberAnimation { duration: 700; easing.type: Easing.InOutQuad } }
      layer.enabled: opacity < 0.999

      Loader {
        id: mascotLoader
        sourceComponent: root.kind === "veyra" ? veyraComponent : aurenComponent
        onLoaded: root.bindMascot(item)
      }

      MouseArea {
        id: area
        x: root.mascot.hitbox.x + root.mascot.padX * root.mascot.u
        y: root.mascot.hitbox.y + root.mascot.padTop * root.mascot.u
        width: root.mascot.hitbox.width
        height: root.mascot.hitbox.height
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        cursorShape: root.dragging ? Qt.ClosedHandCursor : Qt.PointingHandCursor
        property point pressAt: Qt.point(0, 0)
        property point startAt: Qt.point(0, 0)
        onEntered: root.hovered = true
        onExited: root.hovered = false
        onPressed: (mouse) => {
          pressAt = mapToGlobal(mouse.x, mouse.y)
          startAt = Qt.point(win.x, win.y)
        }
        onPositionChanged: (mouse) => {
          if (!(mouse.buttons & Qt.LeftButton)) return
          var g = mapToGlobal(mouse.x, mouse.y)
          var dx = g.x - pressAt.x, dy = g.y - pressAt.y
          if (!root.dragging && dx * dx + dy * dy <= 36) return
          root.dragging = true
          var p = root.clamp(startAt.x + dx, startAt.y + dy)
          win.x = p.x
          win.y = p.y
        }
        onReleased: (mouse) => {
          if (root.dragging) { root.dragging = false; root.save(); return }
          if (mouse.button === Qt.LeftButton) root.clicked()
          else root.mascot.poke()
        }
        onCanceled: if (root.dragging) { root.dragging = false; root.save() }
        onWheel: (wheel) => {
          if (wheel.angleDelta.y === 0) return
          var f = wheel.angleDelta.y > 0 ? 1.1 : 1 / 1.1
          auren.savePlace(win.x, win.y, Math.max(0.6, Math.min(2.2, root.size * f)), false)
        }
      }
    }
  }

  function answer(yes) { auren.ctl(yes ? "yes" : "no") }

  function clicked() {
    if (mood === "off") {
      auren.startDaemon()
      mascot.poke()
    } else if (mood === "speaking" || mood === "thinking" || mood === "working") {
      auren.ctl("cancel")
    } else if (mood === "idle" || mood === "listening") {
      auren.ctl("talk")
    } else {
      mascot.poke()
    }
  }

  // ---- the speech bubble (as in shell.qml, with this Mac's talk key) ---------------------------------
  property string lastCaption: ""
  property real captionUntil: 0
  readonly property string bubbleText: {
    if (prefs.bubble === false || dragging) return ""
    var d = st.detail || ""
    switch (mood) {
      case "listening":
        if (st.remind) return (d || "Still listening…") + "\nPress " + key + " to send"
        return d || (st.fresh ? "Fresh start · listening…" : "Listening…")
                    + (st.autosend ? "\nPause or press again to send" : "\nPress again to send")
      case "thinking": return (st.transcript || d || "Thinking…") + "\nPress " + key + " to stop"
      case "working": return (d || "Working…") + "\nPress " + key + " to stop"
      case "speaking": return d
      case "error": return d ? "Oops: " + d : "Something went wrong"
      case "starting": return "Waking up…"
    }
    if (awaiting) return (st.question || "Waiting for your answer") + "\nOr " + key + ", then say yes or no"
    if (mood === "idle" && d === "heard nothing" && now - (st.since || 0) < 3) return "Didn't catch that"
    if (mood === "idle" && d === "cancelled" && now - (st.since || 0) < 2.5) return "Stopped"
    if (mood === "idle" && now < captionUntil) return lastCaption
    if (hovered && hoverHint.shown) return mood === "off" ? "Auren is off: click to start" : "Click to talk · drag · scroll to resize"
    return ""
  }
  readonly property var bubbleHints: ["Press again to send", "Pause or press again to send", "Press " + key + " to send",
                                      "Press " + key + " to stop", "Or " + key + ", then say yes or no"]
  readonly property string bubbleHint: {
    for (var i = 0; i < bubbleHints.length; i++)
      if (bubbleText.endsWith("\n" + bubbleHints[i])) return bubbleHints[i]
    return ""
  }
  readonly property string bubbleBody: bubbleHint ? bubbleText.slice(0, bubbleText.length - bubbleHint.length - 1) : bubbleText
  readonly property bool bubbleMuted: mood === "thinking" || mood === "working" || mood === "starting"
                                     || (mood === "listening" && !st.detail && !st.remind) || (hovered && !active && !awaiting)
  property string spokeLast: ""
  onStChanged: {
    if (st.state === "speaking" && st.detail) spokeLast = st.detail
    if (st.state === "idle" && spokeLast) {
      lastCaption = spokeLast
      captionUntil = Date.now() / 1000 + 2.5
      spokeLast = ""
    }
  }
  Timer { id: hoverHint; property bool shown: false; interval: 700; running: root.hovered; onRunningChanged: if (running) shown = false; onTriggered: shown = true }

  Window {
    id: bubbleWin
    transientParent: null
    title: "Auren bubble"
    visible: win.visible && root.bubbleText !== ""
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
           | Qt.NoDropShadowWindowHint | (root.awaiting ? 0 : Qt.WindowTransparentForInput)
    color: "transparent"
    onVisibleChanged: if (visible) auren.pin(bubbleWin)
    onFlagsChanged: if (visible) Qt.callLater(() => auren.pin(bubbleWin))   // new flags reset the window's level

    readonly property real mascotCenter: win.x + root.mascot.implicitWidth / 2
    readonly property bool above: win.y + root.mascot.bubbleAnchorY - height > root.desk.y + 30
    readonly property QtObject margins: QtObject { readonly property real left: bubbleWin.x }
    x: Math.max(root.desk.x + 6, Math.min(root.desk.x + root.desk.width - width - 6, mascotCenter - width / 2))
    y: above ? win.y + root.mascot.bubbleAnchorY - height : win.y + root.mascot.implicitHeight - root.mascot.u
    width: card.width
    height: card.height + tail.height

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

        // Yes and No while it waits for an answer. Everywhere else the bubble lets clicks through, so
        // outside the buttons the row has no size.
        Item {
          width: parent.width
          height: root.awaiting ? answerRow.height + 8 : 0
          visible: root.awaiting

          Row {
            id: answerRow
            y: 8
            spacing: 8
            width: root.awaiting ? implicitWidth : 0
            height: root.awaiting ? 24 : 0
            Repeater {
              model: [{ label: "Yes", yes: true }, { label: "No", yes: false }]
              Rectangle {
                required property var modelData
                width: 56
                height: 24
                radius: 6
                color: modelData.yes ? root.accent : "transparent"
                border.color: root.accent
                border.width: 1
                Text {
                  anchors.centerIn: parent
                  text: parent.modelData.label
                  color: parent.modelData.yes ? root.bg : root.fg
                  font.family: "monospace"
                  font.pixelSize: 12
                  font.bold: true
                }
                MouseArea {
                  anchors.fill: parent
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.answer(parent.modelData.yes)
                }
              }
            }
          }
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
}

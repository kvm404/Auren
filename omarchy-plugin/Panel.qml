import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// auren in the Omarchy bar: a tiny Clawd that shows what auren is doing, and a panel to control it.
//
//   bar icon      bar colour when idle, orange and bobbing while auren listens/thinks/works/speaks,
//                 dim with shut eyes when auren is off
//   click         the panel: live Clawd (click it to talk), status, last exchange, switches
//                 (auren, voice replies, mascot, sleep animation), voice (F1-F5, M1-M5), brain (auto/Claude/Codex),
//                 actions (start fresh, stop, restart, log) and today's numbers
//   right-click   start a fresh conversation
//
// It reads $XDG_RUNTIME_DIR/auren/state.json (written by the daemon) and talks to the daemon through
// bin/auren-ctl; the services are started and stopped with systemctl --user.
Panel {
  id: root
  moduleName: "krvm.auren"
  ipcTarget: "krvm.auren"

  readonly property string home: Quickshell.env("HOME")
  readonly property string aurenDir: String(setting("aurenDir", "~/Projects/auren")).replace(/^~/, home)
  readonly property string ctl: aurenDir + "/bin/auren-ctl"
  readonly property string runtimeDir: Quickshell.env("XDG_RUNTIME_DIR") + "/auren"

  readonly property color fg: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(fg, 1.55)
  readonly property string font: bar ? bar.fontFamily : Style.font.family
  readonly property color orange: "#D77757"

  // ---- auren's state ------------------------------------------------------------------------
  property var st: ({ state: "off" })
  property bool daemonAlive: false
  property bool daemonActive: false     // systemd says auren.service is active (may still be starting)
  property bool mascotActive: false
  readonly property string mood: !daemonAlive ? "off" : (st.state || "idle")
  readonly property bool awaiting: mood === "idle" && st.awaiting === true && (st.until || 0) > Date.now() / 1000
  readonly property bool voiceOn: st.voice !== false
  readonly property string brainMode: st.mode || "auto"
  readonly property string voiceName: st.voiceName || "F2"
  // The voices picked by ear in decision 0001: F2 (chosen), M3 (normal pace), F1 and M1 (slower, calmer).
  readonly property var voiceChoices: [
    { name: "F2", tip: "Auren's voice (chosen)" },
    { name: "M3", tip: "Male, normal pace" },
    { name: "F1", tip: "Female, slower and calmer" },
    { name: "M1", tip: "Male, slower and calmer" }
  ]

  FileView {
    id: stateFile
    path: root.runtimeDir + "/state.json"
    watchChanges: true
    printErrors: false
    property string last: ""
    onFileChanged: reload()
    onLoaded: {
      if (text() === last) return
      last = text()
      try { root.st = JSON.parse(last) } catch (e) {}
      root.daemonAlive = root.st.state !== "off"
      liveness.check()
    }
    onLoadFailed: { root.st = { state: "off" }; root.daemonAlive = false }
  }
  // The watch sees the daemon's write-then-rename updates; this slow re-read is only a safety net.
  Timer { interval: 10000; running: true; repeat: true; onTriggered: stateFile.reload() }

  FileView {      // the daemon may have died without writing "off"
    id: liveness
    path: root.st.pid ? "/proc/" + root.st.pid + "/cmdline" : ""
    printErrors: false
    function check() { if (path) reload() }
    onLoaded: root.daemonAlive = root.st.state !== "off" && text().indexOf("auren") >= 0
    onLoadFailed: root.daemonAlive = false
  }
  Timer { interval: 5000; running: true; repeat: true; onTriggered: liveness.check() }

  Process {
    id: services
    command: ["systemctl", "--user", "is-active", "auren", "auren-mascot"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var lines = String(text || "").trim().split("\n")
        root.daemonActive = lines[0] === "active"
        root.mascotActive = lines[1] === "active"
      }
    }
  }
  function refreshServices() { services.running = false; services.running = true }
  Timer { interval: 3000; running: root.opened; repeat: true; triggeredOnStart: true; onTriggered: root.refreshServices() }
  Timer { id: afterAction; interval: 900; onTriggered: { root.refreshServices(); stateFile.reload() } }

  // the mascot's preferences (shared with mascot/shell.qml, which reloads them on change)
  FileView {
    id: mascotPrefsFile
    path: root.home + "/.config/auren/mascot.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    JsonAdapter {
      id: mascotPrefs
      property real idleOpacity: 0.45
      property int fadeAfter: 12
      property int sleepAfter: 240
      property bool bubble: true
      property bool sleepAnimation: true
    }
  }

  // today's numbers and the last exchange, from the history log
  property var lastExchange: null
  property int todayCount: 0
  property real todayAvg: 0
  Process {
    id: historyProc
    command: ["tail", "-n", "300", root.home + "/.local/state/auren/history.jsonl"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var lines = String(text || "").trim().split("\n")
        var start = new Date(); start.setHours(0, 0, 0, 0)
        var count = 0, total = 0, last = null
        for (var i = 0; i < lines.length; i++) {
          var r
          try { r = JSON.parse(lines[i]) } catch (e) { continue }
          if (r.source === "forget") continue
          last = r
          if (r.at * 1000 >= start.getTime()) { count++; total += r.think_s || 0 }
        }
        root.lastExchange = last
        root.todayCount = count
        root.todayAvg = count ? total / count : 0
      }
    }
  }
  onOpenedChanged: if (opened) { historyProc.running = false; historyProc.running = true; cursorActive = false }
  onMoodChanged: if (opened && mood === "idle") { historyProc.running = false; historyProc.running = true }

  // ---- actions --------------------------------------------------------------------------------
  // Commands run as argument lists (no shell). The plugin bar API has run(command) but no shellQuote.
  function ctlRun(args) {
    Quickshell.execDetached([root.ctl].concat(String(args).split(" ")))
    afterAction.restart()
  }
  function systemctl(verb, unit) {
    Quickshell.execDetached(["systemctl", "--user", verb, unit])
    afterAction.restart()
  }
  function toggleAuren() { systemctl(root.daemonActive || root.daemonAlive ? "stop" : "start", "auren") }
  function toggleVoice() { ctlRun("voice " + (root.voiceOn ? "off" : "on")) }
  function toggleMascot() { systemctl(root.mascotActive ? "stop" : "start", "auren-mascot") }
  function toggleSleepAnimation() {
    mascotPrefs.sleepAnimation = !mascotPrefs.sleepAnimation
    mascotPrefsFile.writeAdapter()
  }
  function setBrain(mode) { ctlRun("brain " + mode) }
  function setVoiceName(name) { ctlRun("voice-name " + name) }
  function startFresh() { ctlRun("forget") }
  function stopTalking() { ctlRun("cancel") }
  function restartAuren() { systemctl("restart", "auren") }
  function openLog() {
    Quickshell.execDetached(["omarchy-launch-or-focus-tui", "less +G " + root.home + "/.local/state/auren/daemon.log"])
    root.close()
  }

  readonly property string statusText: {
    if (!root.daemonAlive) return root.daemonActive ? "Starting…" : "Off"
    if (root.awaiting) return "Waiting for your yes or no"
    switch (root.mood) {
      case "starting": return "Waking up…"
      case "listening": return "Listening…"
      case "thinking": return "Thinking…"
      case "working": return st.detail || "Working…"
      case "speaking": return "Speaking"
      case "error": return "Error: " + (st.detail || "")
    }
    return "Ready"
  }
  readonly property string brainText: brainMode === "codex" ? "Codex (GPT-6.1 Sol)"
                                    : brainMode === "claude" ? "Claude Haiku only"
                                    : st.brain === "codex" ? "Codex for now (Claude's limit)" : "Claude Haiku"

  // ---- keyboard cursor --------------------------------------------------------------------------
  // rows: 0-3 the switches, 4 voices (4 buttons), 5 brain (3 buttons), 6 actions (4 buttons)
  property bool cursorActive: false
  property int cursorRow: 0
  property int cursorCol: 0
  readonly property var rowLengths: [1, 1, 1, 1, 4, 3, 4]
  function has(row, col) { return cursorActive && cursorRow === row && (col === undefined || cursorCol === col) }
  function moveCursor(dx, dy) {
    if (!cursorActive) { cursorActive = true; return }
    cursorRow = Math.max(0, Math.min(rowLengths.length - 1, cursorRow + dy))
    cursorCol = Math.max(0, Math.min(rowLengths[cursorRow] - 1, cursorCol + dx))
  }
  function activate() {
    if (!cursorActive) return
    var r = cursorRow, c = cursorCol
    if (r === 0) toggleAuren()
    else if (r === 1) toggleVoice()
    else if (r === 2) toggleMascot()
    else if (r === 3) toggleSleepAnimation()
    else if (r === 4) setVoiceName(voiceChoices[c].name)
    else if (r === 5) setBrain(["auto", "claude", "codex"][c])
    else if (r === 6) [startFresh, stopTalking, restartAuren, openLog][c]()
  }
  function hover(row, col) { cursorActive = true; cursorRow = row; cursorCol = col || 0 }

  // ---- the bar icon ---------------------------------------------------------------------------
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    tooltipText: root.opened ? "" : "Auren: " + root.statusText
    iconComponent: Component {
      MiniClawd {
        mood: root.awaiting ? "awaiting" : root.mood
        foreground: button.foreground
      }
    }
    onPressed: function(b) {
      if (b === Qt.RightButton) root.startFresh()
      else root.toggle()
    }
  }

  // ---- the panel ------------------------------------------------------------------------------
  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(360))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) { root.moveCursor(dx, dy) }
      onActivateRequested: root.activate()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Column {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Style.space(14)

        // ---------- hero: live Clawd · name · status ----------
        Item {
          width: parent.width
          implicitHeight: Math.max(clawdBox.height, heroText.implicitHeight)

          Item {
            id: clawdBox
            width: Style.space(84)
            height: Style.space(60)
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter

            Loader {
              id: liveClawd
              active: root.opened
              anchors.centerIn: parent
              source: "file://" + root.aurenDir + "/mascot/mascots/clawd/Clawd.qml"
              onLoaded: item.u = Math.floor(clawdBox.width / 24 * 2) / 2
            }
            Binding { target: liveClawd.item; property: "mood"; value: root.mood === "off" ? "idle" : root.mood; when: liveClawd.status === Loader.Ready }
            Binding { target: liveClawd.item; property: "off"; value: root.mood === "off"; when: liveClawd.status === Loader.Ready }
            Binding { target: liveClawd.item; property: "awaiting"; value: root.awaiting; when: liveClawd.status === Loader.Ready }
            Binding { target: liveClawd.item; property: "mouse"; value: root.st.mouse === true; when: liveClawd.status === Loader.Ready }
            Binding { target: liveClawd.item; property: "hovered"; value: clawdMouse.containsMouse; when: liveClawd.status === Loader.Ready }

            MouseArea {        // click to talk (click again to send), right-click to poke
              id: clawdMouse
              anchors.fill: parent
              hoverEnabled: true
              acceptedButtons: Qt.LeftButton | Qt.RightButton
              cursorShape: Qt.PointingHandCursor
              onPositionChanged: (mouse) => {
                if (liveClawd.item) { liveClawd.item.pointerX = mouse.x / width; liveClawd.item.pointerY = mouse.y / height }
              }
              onClicked: (mouse) => {
                if (mouse.button === Qt.RightButton) { if (liveClawd.item) liveClawd.item.poke(); return }
                if (!root.daemonAlive) root.toggleAuren()
                else if (root.mood === "speaking") root.stopTalking()
                else if (root.mood === "idle" || root.mood === "listening") root.ctlRun("talk")
                else if (liveClawd.item) liveClawd.item.poke()
              }
            }
          }

          Column {
            id: heroText
            anchors.left: clawdBox.right
            anchors.leftMargin: Style.space(12)
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(2)

            Text {
              text: "Auren"
              color: root.fg
              font.family: root.font
              font.pixelSize: Style.font.title
              font.bold: true
            }
            Text {
              width: parent.width
              text: root.statusText.toUpperCase()
              color: root.mood === "off" ? root.dim : (root.mood === "idle" && !root.awaiting ? Qt.darker(root.fg, 1.3) : root.orange)
              font.family: root.font
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
              elide: Text.ElideRight
            }
            Text {
              width: parent.width
              visible: root.daemonAlive
              text: root.brainText + (root.voiceOn ? "" : " · voice off")
              color: root.dim
              font.family: root.font
              font.pixelSize: Style.font.caption
              elide: Text.ElideRight
            }
            Text {
              width: parent.width
              text: !root.daemonAlive ? "Click Clawd to start Auren"
                    : root.mood === "listening" ? "Press Super+A again to send"
                    : "Super+A to talk · Super+Shift+A fresh"
              color: root.dim
              font.family: root.font
              font.pixelSize: Style.font.caption
              font.italic: true
              elide: Text.ElideRight
            }
          }
        }

        // ---------- the last exchange ----------
        Column {
          width: parent.width
          spacing: Style.space(3)
          visible: root.lastExchange !== null
          Text {
            width: parent.width
            text: "You: " + (root.lastExchange ? root.lastExchange.transcript : "")
            color: root.dim
            font.family: root.font
            font.pixelSize: Style.font.bodySmall
            elide: Text.ElideRight
          }
          Text {
            width: parent.width
            text: root.lastExchange ? root.lastExchange.reply : ""
            color: root.fg
            font.family: root.font
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.Wrap
            maximumLineCount: 2
            elide: Text.ElideRight
          }
        }

        PanelSeparator { foreground: root.fg }

        // ---------- switches ----------
        Column {
          width: parent.width
          spacing: Style.space(6)

          Toggle {
            width: parent.width
            label: "Auren"
            description: root.daemonAlive ? "On: listening for Super+A" : (root.daemonActive ? "Starting…" : "Off")
            checked: root.daemonAlive || root.daemonActive
            foreground: root.fg; fontFamily: root.font
            hasCursor: root.has(0)
            onClicked: root.toggleAuren()
            onHovered: function(h) { if (h) root.hover(0) }
          }
          Toggle {
            width: parent.width
            label: "Voice replies"
            description: root.voiceOn ? "Auren speaks its answers" : "Answers only in Clawd's bubble"
            checked: root.voiceOn
            enabled: root.daemonAlive
            opacity: enabled ? 1 : 0.5
            foreground: root.fg; fontFamily: root.font
            hasCursor: root.has(1)
            onClicked: root.toggleVoice()
            onHovered: function(h) { if (h) root.hover(1) }
          }
          Toggle {
            width: parent.width
            label: "Mascot"
            description: root.mascotActive ? "Clawd on screen" : "Hidden (frees ~100 MB)"
            checked: root.mascotActive
            foreground: root.fg; fontFamily: root.font
            hasCursor: root.has(2)
            onClicked: root.toggleMascot()
            onHovered: function(h) { if (h) root.hover(2) }
          }
          Toggle {
            width: parent.width
            label: "Sleep animation"
            description: mascotPrefs.sleepAnimation ? "Breathes while asleep (~6% CPU)" : "Still while asleep (saves CPU)"
            checked: mascotPrefs.sleepAnimation
            foreground: root.fg; fontFamily: root.font
            hasCursor: root.has(3)
            onClicked: root.toggleSleepAnimation()
            onHovered: function(h) { if (h) root.hover(3) }
          }
        }

        // ---------- voice ----------
        Column {
          width: parent.width
          spacing: Style.space(8)
          opacity: root.daemonAlive && root.voiceOn ? 1 : 0.5

          PanelSectionHeader { text: "VOICE  ·  " + root.voiceName; foreground: root.fg; fontFamily: root.font }

          Row {
            id: voiceRow
            width: parent.width
            spacing: Style.space(6)
            readonly property real cell: (width - spacing * 3) / 4
            Repeater {
              model: root.voiceChoices
              Button {
                required property var modelData
                required property int index
                width: voiceRow.cell
                text: modelData.name
                tooltipText: modelData.tip + ": click to hear it"
                fontSize: Style.font.bodySmall
                foreground: root.fg
                fontFamily: root.font
                bordered: true
                active: root.voiceName === modelData.name
                enabled: root.daemonAlive
                hasCursor: root.has(4, index)
                onClicked: root.setVoiceName(modelData.name)
                onHovered: function(h) { if (h) root.hover(4, index) }
              }
            }
          }
        }

        // ---------- brain ----------
        Column {
          width: parent.width
          spacing: Style.space(8)

          PanelSectionHeader { text: "BRAIN"; foreground: root.fg; fontFamily: root.font }

          Row {
            id: brainRow
            width: parent.width
            spacing: Style.space(6)
            readonly property real cell: (width - spacing * 2) / 3
            Repeater {
              model: [
                { mode: "auto", label: "Auto", icon: "󰁨", tip: "Claude Haiku; Codex when Claude's limit is hit" },
                { mode: "claude", label: "Claude", icon: "󰚩", tip: "Claude Haiku only" },
                { mode: "codex", label: "Codex", icon: "󰘦", tip: "GPT-6.1 Sol via Codex (saves Claude's allowance)" }
              ]
              Button {
                required property var modelData
                required property int index
                width: brainRow.cell
                text: modelData.label
                iconText: modelData.icon
                tooltipText: modelData.tip
                fontSize: Style.font.bodySmall
                iconSize: Style.font.title
                foreground: root.fg
                fontFamily: root.font
                bordered: true
                active: root.brainMode === modelData.mode
                enabled: root.daemonAlive
                opacity: enabled ? 1 : 0.5
                hasCursor: root.has(5, index)
                onClicked: root.setBrain(modelData.mode)
                onHovered: function(h) { if (h) root.hover(5, index) }
              }
            }
          }
        }

        // ---------- actions ----------
        Column {
          width: parent.width
          spacing: Style.space(8)

          PanelSectionHeader { text: "ACTIONS"; foreground: root.fg; fontFamily: root.font }

          Row {
            id: actionRow
            width: parent.width
            spacing: Style.space(6)
            readonly property real cell: (width - spacing * 3) / 4
            Repeater {
              model: [
                { label: "Fresh", icon: "󰑓", tip: "Start a fresh conversation (forget this one)" },
                { label: "Stop", icon: "󰓛", tip: "Stop listening or talking" },
                { label: "Restart", icon: "󰜉", tip: "Restart Auren" },
                { label: "Log", icon: "󰦪", tip: "Open Auren's log" }
              ]
              Button {
                required property var modelData
                required property int index
                width: actionRow.cell
                text: modelData.label
                iconText: modelData.icon
                tooltipText: modelData.tip
                fontSize: Style.font.bodySmall
                iconSize: Style.font.title
                foreground: root.fg
                fontFamily: root.font
                bordered: true
                enabled: index === 3 || root.daemonAlive
                opacity: enabled ? 1 : 0.5
                hasCursor: root.has(6, index)
                onClicked: [root.startFresh, root.stopTalking, root.restartAuren, root.openLog][index]()
                onHovered: function(h) { if (h) root.hover(6, index) }
              }
            }
          }
        }

        // ---------- today ----------
        Text {
          width: parent.width
          visible: root.todayCount > 0
          text: "Today: " + root.todayCount + (root.todayCount === 1 ? " request" : " requests")
                + " · average " + root.todayAvg.toFixed(1) + " s to answer"
          color: root.dim
          font.family: root.font
          font.pixelSize: Style.font.caption
          horizontalAlignment: Text.AlignHCenter
        }
      }
    }
  }
}

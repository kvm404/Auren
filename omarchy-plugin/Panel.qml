import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// auren in the Omarchy bar: the chosen mascot in pixels, showing what auren is doing, and a panel.
//
//   bar icon      bar colour when idle; the mascot's colour, bobbing, while auren listens/thinks/works/speaks;
//                 dim with shut eyes when auren is off
//   click         the panel, four pages:
//                   Home    the live mascot (click it to talk), status, last exchange, quick switches
//                   Mascot  Clawd, Veyra or Auren; speech bubble, sleep animation, voice follows mascot, fade, size
//                   Voice   spoken replies, reply language (English, Hindi), voice (F2, M3, F1, M1), speed,
//                           quality, volume, pause, test
//                   Brain   auto / Claude / Codex, Claude's effort, restart, log, today's numbers
//   right-click   start a fresh conversation
//
// Keys: arrows or hjkl move (left/right on the top row changes page, on a slider changes it), Enter acts,
// Esc closes, Tab goes to the next bar panel.
//
// It reads $XDG_RUNTIME_DIR/auren/state.json (written by the daemon, including the voice settings) and
// ~/.config/auren/mascot.json (the mascot's preferences, which it also writes), and talks to the daemon
// through ~/.local/share/auren/bin/auren-ctl; the services are started and stopped with systemctl --user.
Panel {
  id: root
  moduleName: "krvm.auren"
  ipcTarget: "krvm.auren"

  readonly property string home: Quickshell.env("HOME")
  readonly property string configDir: (Quickshell.env("XDG_CONFIG_HOME") || home + "/.config") + "/auren"
  readonly property string stateDir: (Quickshell.env("XDG_STATE_HOME") || home + "/.local/state") + "/auren"
  readonly property string dataDir: (Quickshell.env("XDG_DATA_HOME") || home + "/.local/share") + "/auren"
  readonly property string ctl: dataDir + "/bin/auren-ctl"
  readonly property string runtimeDir: Quickshell.env("XDG_RUNTIME_DIR") + "/auren"

  readonly property color fg: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(fg, 1.55)
  readonly property string font: bar ? bar.fontFamily : Style.font.family

  // ---- the mascots ----------------------------------------------------------------------------
  readonly property var mascots: [
    { kind: "clawd", name: "Clawd", tip: "the original", color: "#D77757", voice: "F2", hello: "Hi, it's Clawd." },
    { kind: "veyra", name: "Veyra", tip: "fox spirit", color: "#A08BF7", voice: "F2", hello: "Hi, I'm Veyra." },
    { kind: "auren", name: "Auren", tip: "hover-bot", color: "#F5B83D", voice: "M3", hello: "Hey, I'm Auren." }
  ]
  readonly property string kind: ["clawd", "veyra", "auren"].indexOf(mascotPrefs.mascot) >= 0 ? mascotPrefs.mascot : "clawd"
  readonly property var mascot: mascots[["clawd", "veyra", "auren"].indexOf(kind)]
  readonly property color accent: mascot.color
  function mascotFile(k) {
    return "file://" + dataDir + "/mascot/mascots/" + k + "/" + k.charAt(0).toUpperCase() + k.slice(1) + ".qml"
  }

  // ---- auren's state ------------------------------------------------------------------------
  property var st: ({ state: "off" })
  property bool daemonAlive: false
  property bool daemonActive: false     // systemd says auren.service is active (may still be starting)
  property bool mascotActive: false
  readonly property string mood: !daemonAlive ? "off" : (st.state || "idle")
  readonly property bool awaiting: mood === "idle" && st.awaiting === true && (st.until || 0) > Date.now() / 1000
  readonly property bool voiceOn: st.voice !== false
  readonly property string brainMode: st.mode || "auto"
  readonly property string effort: st.effort || "low"
  readonly property string language: st.language || "en"
  readonly property string voiceName: st.voiceName || "F2"
  readonly property var tts: st.tts || ({ speed: 1.05, steps: 5, volume: 1.0, pause: 0.0 })
  // The voices picked by ear in decision 0001: F2 (chosen), M3 (normal pace), F1 and M1 (slower, calmer).
  readonly property var voiceChoices: [
    { value: "F2", label: "F2", tip: "Auren's voice (chosen): click to hear it" },
    { value: "M3", label: "M3", tip: "Male, normal pace: click to hear it" },
    { value: "F1", label: "F1", tip: "Female, slower and calmer: click to hear it" },
    { value: "M1", label: "M1", tip: "Male, slower and calmer: click to hear it" }
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
      root.pendingTts = ({})
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
    path: root.configDir + "/mascot.json"
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
      property string mascot: "clawd"
      property bool matchVoice: true      // picking a mascot also picks its voice (Auren speaks with M3)
    }
  }
  function savePrefs() { mascotPrefsFile.writeAdapter() }

  // the mascot's place and size (written by the mascot; changed through its IPC)
  FileView {
    id: placeFile
    path: root.stateDir + "/mascot.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    JsonAdapter {
      id: place
      property real size: 1
      property bool hidden: false
    }
  }

  // today's numbers and the last exchange, from the history log
  property var lastExchange: null
  property int todayCount: 0
  property real todayAvg: 0
  property int todayCodex: 0
  Process {
    id: historyProc
    command: ["tail", "-n", "300", root.stateDir + "/history.jsonl"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var lines = String(text || "").trim().split("\n")
        var start = new Date(); start.setHours(0, 0, 0, 0)
        var count = 0, total = 0, codex = 0, last = null
        for (var i = 0; i < lines.length; i++) {
          var r
          try { r = JSON.parse(lines[i]) } catch (e) { continue }
          if (r.source === "forget") continue
          last = r
          if (r.at * 1000 >= start.getTime()) { count++; total += r.think_s || 0; if (r.brain === "codex") codex++ }
        }
        root.lastExchange = last
        root.todayCount = count
        root.todayAvg = count ? total / count : 0
        root.todayCodex = codex
      }
    }
  }
  onOpenedChanged: if (opened) { historyProc.running = false; historyProc.running = true; cursorActive = false }
  onMoodChanged: if (opened && mood === "idle") { historyProc.running = false; historyProc.running = true }

  // ---- actions --------------------------------------------------------------------------------
  // Commands run as argument lists (no shell). The plugin bar API has run(command) but no shellQuote.
  function ctlRun(args) {
    Quickshell.execDetached([root.ctl].concat(args))
    afterAction.restart()
  }
  function systemctl(verb, unit) {
    Quickshell.execDetached(["systemctl", "--user", verb, unit])
    afterAction.restart()
  }
  function mascotIpc(args) {
    Quickshell.execDetached(["qs", "-p", root.dataDir + "/mascot", "ipc", "call", "mascot"].concat(args))
  }
  function toggleAuren() { systemctl(root.daemonActive || root.daemonAlive ? "stop" : "start", "auren") }
  function toggleVoice() { ctlRun(["voice", root.voiceOn ? "off" : "on"]) }
  function toggleMascot() { systemctl(root.mascotActive ? "stop" : "start", "auren-mascot") }
  function setBrain(mode) { ctlRun(["brain", mode]) }
  function setLanguage(lang) { if (lang !== root.language) ctlRun(["language", lang]) }
  function setEffort(level) { if (level !== root.effort) ctlRun(["effort", level]) }
  function setVoiceName(name) { ctlRun(["voice-name", name]) }
  function startFresh() { ctlRun(["forget"]) }
  function stopTalking() { ctlRun(["cancel"]) }
  function restartAuren() { systemctl("restart", "auren") }
  function openLog() {
    Quickshell.execDetached(["omarchy-launch-or-focus-tui", "less +G " + root.stateDir + "/daemon.log"])
    root.close()
  }
  function togglePref(name) { mascotPrefs[name] = !mascotPrefs[name]; savePrefs() }
  function setMascot(k) {
    var m = root.mascots[["clawd", "veyra", "auren"].indexOf(k)]
    if (!m) return
    if (k !== root.kind) { mascotPrefs.mascot = k; savePrefs() }
    if (mascotPrefs.matchVoice && root.daemonAlive && root.voiceOn && root.mood === "idle")
      ctlRun(["voice-name", m.voice, m.hello])         // switch to its voice and say hello
  }
  function talk() {
    if (!root.daemonAlive) root.toggleAuren()
    else if (root.mood === "speaking") root.stopTalking()
    else if (root.mood === "idle" || root.mood === "listening") root.ctlRun(["talk"])
    else if (hero.item) hero.item.poke()
  }

  // Voice settings: a changed slider shows its new value right away; the daemon's state confirms it.
  property var pendingTts: ({})
  function ttsValue(key) { return root.pendingTts[key] !== undefined ? root.pendingTts[key] : root.tts[key] }
  function setTts(key, value) {
    var p = Object.assign({}, root.pendingTts); p[key] = value; root.pendingTts = p
    ctlRun(["tts", key, String(value)])
  }
  function round(v, step) { return Math.round(v / step) * step }
  readonly property var sliders: ({
    speed: { min: 0.8, max: 1.4, step: 0.05 },
    volume: { min: 0.3, max: 1.0, step: 0.05 },
    pause: { min: 0, max: 0.6, step: 0.05 },
    opacity: { min: 0.15, max: 1.0, step: 0.05 },
    size: { min: 0.6, max: 2.2, step: 0.1 }
  })
  function nudge(name, dx) {          // keyboard: one step left or right
    var s = root.sliders[name]
    var cur = name === "opacity" ? mascotPrefs.idleOpacity : name === "size" ? place.size : root.ttsValue(name)
    var v = Math.max(s.min, Math.min(s.max, root.round(cur + dx * s.step, s.step)))
    if (name === "opacity") { mascotPrefs.idleOpacity = v; savePrefs() }
    else if (name === "size") root.mascotIpc(["size", v.toFixed(2)])
    else root.setTts(name, v.toFixed(2))
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
                                    : (brainMode === "claude" ? "Claude Haiku only" : st.brain === "codex" ? "Codex for now (Claude's limit)" : "Claude Haiku")
                                      + (st.brain === "codex" ? "" : ", " + effort + " effort")

  // ---- pages and the keyboard cursor --------------------------------------------------------------
  readonly property var pages: [
    { value: "0", label: "Home", icon: "󰋜" },
    { value: "1", label: "Mascot", icon: "󰏩" },
    { value: "2", label: "Voice", icon: "󰗋" },
    { value: "3", label: "Brain", icon: "󰧑" }
  ]
  property int page: 0
  function showPage(p) { page = Math.max(0, Math.min(pages.length - 1, p)); cursorRow = Math.min(cursorRow, rows.length - 1) }

  // Each page is rows of controls: { n: how many across, act(col), adjust(dx) for sliders }. Row 0 is the tabs.
  readonly property var rows: {
    var tabs = { n: 4, act: function(c) { root.showPage(c) } }
    if (page === 1) return [tabs,
      { n: 3, act: function(c) { root.setMascot(root.mascots[c].kind) } },
      { n: 1, act: function() { root.togglePref("bubble") } },
      { n: 1, act: function() { root.togglePref("sleepAnimation") } },
      { n: 1, act: function() { root.togglePref("matchVoice") } },
      { n: 1, adjust: function(dx) { root.nudge("opacity", dx) } },
      { n: 1, adjust: function(dx) { root.nudge("size", dx) } },
      { n: 2, act: function(c) { root.mascotIpc([c ? "poke" : "reset"]) } }]
    if (page === 2) return [tabs,
      { n: 1, act: function() { root.toggleVoice() } },
      { n: 2, act: function(c) { root.setLanguage(root.languages[c].value) } },
      { n: 4, act: function(c) { root.setVoiceName(root.voiceChoices[c].value) } },
      { n: 1, adjust: function(dx) { root.nudge("speed", dx) } },
      { n: 3, act: function(c) { root.setTts("steps", root.qualities[c].value) } },
      { n: 1, adjust: function(dx) { root.nudge("volume", dx) } },
      { n: 1, adjust: function(dx) { root.nudge("pause", dx) } },
      { n: 2, act: function(c) { if (c) root.ctlRun(["tts", "reset"]); else root.ctlRun(["say", root.sample]) } }]
    if (page === 3) return [tabs,
      { n: 3, act: function(c) { root.setBrain(["auto", "claude", "codex"][c]) } },
      { n: 5, act: function(c) { root.setEffort(root.efforts[c].value) } },
      { n: 3, act: function(c) { [root.restartAuren, root.openLog, root.toggleAuren][c]() } }]
    return [tabs, { n: 5, act: function(c) { [root.toggleAuren, root.toggleVoice, root.toggleMascot, root.startFresh, root.stopTalking][c]() } }]
  }
  property bool cursorActive: false
  property int cursorRow: 0
  property int cursorCol: 0
  function has(row, col) { return cursorActive && cursorRow === row && (col === undefined || cursorCol === col) }
  function moveCursor(dx, dy) {
    if (!cursorActive) { cursorActive = true; cursorRow = 0; cursorCol = page; return }
    if (dy !== 0) {
      cursorRow = Math.max(0, Math.min(rows.length - 1, cursorRow + dy))
      cursorCol = cursorRow === 0 ? page : Math.min(cursorCol, rows[cursorRow].n - 1)
      return
    }
    var r = rows[cursorRow]
    if (r.adjust) { r.adjust(dx); return }
    cursorCol = Math.max(0, Math.min(r.n - 1, cursorCol + dx))
    if (cursorRow === 0) showPage(cursorCol)
  }
  function activate() {
    if (!cursorActive) return
    var r = rows[cursorRow]
    if (r.act) r.act(cursorCol)
  }
  function hover(row, col) { cursorActive = true; cursorRow = row; cursorCol = col || 0 }

  readonly property var qualities: [
    { value: "3", label: "Fast", tip: "3 steps: least CPU" },
    { value: "5", label: "Balanced", tip: "5 steps: Auren's default (decision 0001)" },
    { value: "8", label: "Best", tip: "8 steps: smoother, ~45% more CPU" }
  ]
  // Claude Code's --effort for the brain (Haiku 5.5). Low is decision 0003's: higher is slower and uses
  // more of the Claude allowance. Changing it restarts the brain, so the conversation starts fresh.
  readonly property var efforts: [
    { value: "low", label: "Low", tip: "Fastest; fine for most requests (the default)" },
    { value: "medium", label: "Med", tip: "A bit more care, a bit slower" },
    { value: "high", label: "High", tip: "Harder requests; noticeably slower" },
    { value: "xhigh", label: "XHigh", tip: "Much slower; uses more of the Claude allowance" },
    { value: "max", label: "Max", tip: "Slowest and most expensive" }
  ]
  readonly property var languages: [
    { value: "en", label: "English", tip: "Replies in English" },
    { value: "hi", label: "Hindi · हिंदी", tip: "Replies in Hindi (Devanagari), names stay in English; spoken in Hindi" }
  ]
  // Hindi verbs agree with the speaker: the F voices are female, the M voices male.
  readonly property string sample: language !== "hi" ? "Hi, this is how I sound now. Does this pace work for you?"
                                 : voiceName.charAt(0) === "M" ? "नमस्ते! अब मैं ऐसे बोल रहा हूँ। क्या यह रफ़्तार ठीक है?"
                                 : "नमस्ते! अब मैं ऐसे बोल रही हूँ। क्या यह रफ़्तार ठीक है?"

  // ---- the bar icon ---------------------------------------------------------------------------
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    tooltipText: root.opened ? "" : "Auren: " + root.statusText
    iconComponent: Component {
      MiniMascot {
        kind: root.kind
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
    contentWidth: panel.fittedContentWidth(Style.space(356))
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

        Choice {
          width: parent.width
          options: root.pages
          value: String(root.page)
          iconsOnly: false
          foreground: root.fg
          fontFamily: root.font
          cursorIndex: root.has(0) ? root.cursorCol : -1
          onPicked: function(v) { root.showPage(Number(v)) }
          onHoveredIndex: function(i) { root.hover(0, i) }
        }

        // ================= Home =================
        Column {
          width: parent.width
          spacing: Style.space(14)
          visible: root.page === 0

          Item {        // the live mascot · name · status
            width: parent.width
            implicitHeight: Math.max(heroBox.height, heroText.implicitHeight)

            Item {
              id: heroBox
              width: Style.space(84)
              height: Style.space(66)
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter

              Loader {
                id: hero
                active: root.opened && root.page === 0
                anchors.centerIn: parent
                source: root.mascotFile(root.kind)
                onLoaded: item.u = Math.floor(heroBox.width / 24 * 2) / 2
              }
              Binding { target: hero.item; property: "mood"; value: root.mood === "off" ? "idle" : root.mood; when: hero.status === Loader.Ready }
              Binding { target: hero.item; property: "off"; value: root.mood === "off"; when: hero.status === Loader.Ready }
              Binding { target: hero.item; property: "awaiting"; value: root.awaiting; when: hero.status === Loader.Ready }
              Binding { target: hero.item; property: "mouse"; value: root.st.mouse === true; when: hero.status === Loader.Ready }
              Binding { target: hero.item; property: "hovered"; value: heroMouse.containsMouse; when: hero.status === Loader.Ready }

              MouseArea {      // click to talk (click again to send), right-click to poke
                id: heroMouse
                anchors.fill: parent
                hoverEnabled: true
                acceptedButtons: Qt.LeftButton | Qt.RightButton
                cursorShape: Qt.PointingHandCursor
                onPositionChanged: (mouse) => {
                  if (hero.item) { hero.item.pointerX = mouse.x / width; hero.item.pointerY = mouse.y / height }
                }
                onClicked: (mouse) => {
                  if (mouse.button === Qt.RightButton) { if (hero.item) hero.item.poke() }
                  else root.talk()
                }
              }
            }

            Column {
              id: heroText
              anchors.left: heroBox.right
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
                color: root.mood === "off" ? root.dim : (root.mood === "idle" && !root.awaiting ? Qt.darker(root.fg, 1.3) : root.accent)
                font.family: root.font
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 1.2
                elide: Text.ElideRight
              }
              Text {
                width: parent.width
                text: !root.daemonAlive ? "Click " + root.mascot.name + " to start"
                      : root.mood === "listening" ? "Press Super+A again to send"
                      : "Super+A to talk · Shift fresh"
                color: root.dim
                font.family: root.font
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
              }
            }
          }

          Column {      // the last exchange
            width: parent.width
            spacing: Style.space(3)
            visible: root.lastExchange !== null
            Text {
              width: parent.width
              text: root.lastExchange ? root.lastExchange.transcript : ""
              color: root.dim
              font.family: root.font
              font.pixelSize: Style.font.bodySmall
              font.italic: true
              elide: Text.ElideRight
            }
            Text {
              width: parent.width
              text: root.lastExchange ? root.lastExchange.reply : ""
              color: root.fg
              font.family: root.font
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.Wrap
              maximumLineCount: 3
              elide: Text.ElideRight
            }
          }

          Row {         // quick switches and actions
            id: quick
            width: parent.width
            spacing: Style.space(6)
            readonly property real cell: (width - spacing * 4) / 5
            Repeater {
              model: [
                { icon: "󰐥", label: "On", tip: "Auren on or off", on: root.daemonAlive || root.daemonActive, always: true },
                { icon: root.voiceOn ? "󰕾" : "󰖁", label: "Voice", tip: "Spoken replies (off: only in the bubble)", on: root.voiceOn },
                { icon: "󰏩", label: "Mascot", tip: "Show or hide the mascot (hidden frees ~100 MB)", on: root.mascotActive, always: true },
                { icon: "󰑓", label: "Fresh", tip: "Start a fresh conversation" },
                { icon: "󰓛", label: "Stop", tip: "Stop listening or talking" }
              ]
              Button {
                required property var modelData
                required property int index
                width: quick.cell
                text: modelData.label
                iconText: modelData.icon
                tooltipText: modelData.tip
                fontSize: Style.font.caption
                iconSize: Style.font.title
                foreground: root.fg
                fontFamily: root.font
                bordered: true
                selected: modelData.on === true
                enabled: modelData.always === true || root.daemonAlive
                opacity: enabled ? 1 : 0.5
                hasCursor: root.has(1, index)
                onClicked: [root.toggleAuren, root.toggleVoice, root.toggleMascot, root.startFresh, root.stopTalking][index]()
                onHovered: function(h) { if (h) root.hover(1, index) }
              }
            }
          }
        }

        // ================= Mascot =================
        Column {
          width: parent.width
          spacing: Style.space(10)
          visible: root.page === 1

          Row {         // the three mascots, live
            id: cards
            width: parent.width
            spacing: Style.space(6)
            readonly property real cell: (width - spacing * 2) / 3
            Repeater {
              model: root.mascots
              Rectangle {
                id: card
                required property var modelData
                required property int index
                readonly property bool chosen: root.kind === modelData.kind
                readonly property bool hot: cardMouse.containsMouse || root.has(1, index)
                width: cards.cell
                height: preview.height + names.implicitHeight + Style.space(10)
                radius: Style.cornerRadius
                color: chosen ? Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.07) : "transparent"
                border.width: chosen ? 2 : 1
                border.color: chosen ? modelData.color : hot ? root.fg : Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.18)

                Item {
                  id: preview
                  width: parent.width
                  height: width * 0.72
                  Loader {
                    id: previewLoader
                    active: root.opened && root.page === 1
                    anchors.centerIn: parent
                    source: root.mascotFile(card.modelData.kind)
                    onLoaded: { item.u = Math.floor(preview.width / 25 * 2) / 2; item.sleepAnimation = false }
                  }
                  Binding { target: previewLoader.item; property: "hovered"; value: cardMouse.containsMouse; when: previewLoader.status === Loader.Ready }
                }
                Column {
                  id: names
                  anchors.top: preview.bottom
                  anchors.horizontalCenter: parent.horizontalCenter
                  Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: card.modelData.name
                    color: card.chosen ? root.fg : Qt.darker(root.fg, 1.2)
                    font.family: root.font
                    font.pixelSize: Style.font.bodySmall
                    font.bold: card.chosen
                  }
                  Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: card.modelData.tip
                    color: root.dim
                    font.family: root.font
                    font.pixelSize: Style.font.caption
                  }
                }
                MouseArea {
                  id: cardMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onEntered: root.hover(1, card.index)
                  onPositionChanged: (mouse) => {
                    var it = previewLoader.item
                    if (it) { it.pointerX = mouse.x / width; it.pointerY = mouse.y / preview.height }
                  }
                  onClicked: {
                    if (previewLoader.item) previewLoader.item.poke()
                    root.setMascot(card.modelData.kind)
                  }
                }
              }
            }
          }

          Toggle {
            width: parent.width
            label: "Speech bubble"
            description: mascotPrefs.bubble ? "Transcript, activity and captions" : "Off: the mascot only"
            checked: mascotPrefs.bubble
            foreground: root.fg; fontFamily: root.font
            hasCursor: root.has(2)
            onClicked: root.togglePref("bubble")
            onHovered: function(h) { if (h) root.hover(2) }
          }
          Toggle {
            width: parent.width
            label: "Sleep animation"
            description: mascotPrefs.sleepAnimation ? "Breathes while asleep (~6% CPU)" : "Still while asleep (saves CPU)"
            checked: mascotPrefs.sleepAnimation
            foreground: root.fg; fontFamily: root.font
            hasCursor: root.has(3)
            onClicked: root.togglePref("sleepAnimation")
            onHovered: function(h) { if (h) root.hover(3) }
          }
          Toggle {
            width: parent.width
            label: "Voice follows mascot"
            description: root.mascots[2].name + " speaks with " + root.mascots[2].voice + ", the others with F2"
            checked: mascotPrefs.matchVoice
            foreground: root.fg; fontFamily: root.font
            hasCursor: root.has(4)
            onClicked: root.togglePref("matchVoice")
            onHovered: function(h) { if (h) root.hover(4) }
          }
          SliderRow {
            id: fadeRow
            width: parent.width
            bar: root.bar
            label: "Faded when idle"
            valueText: Math.round(fadeRow.liveValue * 100) + "%"
            minimum: root.sliders.opacity.min; maximum: root.sliders.opacity.max; step: root.sliders.opacity.step
            value: mascotPrefs.idleOpacity
            foreground: root.fg; fontFamily: root.font
            hasCursor: root.has(5)
            onReleased: function(v) { mascotPrefs.idleOpacity = root.round(v, 0.05); root.savePrefs() }
            onHovered: root.hover(5)
          }
          SliderRow {
            id: sizeRow
            width: parent.width
            bar: root.bar
            label: "Size"
            valueText: sizeRow.liveValue.toFixed(1) + "×"
            minimum: root.sliders.size.min; maximum: root.sliders.size.max; step: root.sliders.size.step
            value: place.size
            enabled: root.mascotActive
            foreground: root.fg; fontFamily: root.font
            hasCursor: root.has(6)
            onReleased: function(v) { root.mascotIpc(["size", root.round(v, 0.1).toFixed(2)]) }
            onHovered: root.hover(6)
          }
          Choice {
            width: parent.width
            enabled: root.mascotActive
            opacity: enabled ? 1 : 0.5
            options: [
              { value: "reset", label: "Reset place", icon: "󰦛", tip: "Back to the bottom-right corner, normal size" },
              { value: "poke", label: "Poke", icon: "󰏩", tip: "Say hi" }
            ]
            foreground: root.fg; fontFamily: root.font
            cursorIndex: root.has(7) ? root.cursorCol : -1
            onPicked: function(v) { root.mascotIpc([v]) }
            onHoveredIndex: function(i) { root.hover(7, i) }
          }
        }

        // ================= Voice =================
        Column {
          width: parent.width
          spacing: Style.space(10)
          visible: root.page === 2
          enabled: root.daemonAlive

          Toggle {
            width: parent.width
            label: "Voice replies"
            description: !root.daemonAlive ? "Auren is off" : root.voiceOn ? "Auren speaks its answers" : "Answers only in the bubble"
            checked: root.voiceOn
            foreground: root.fg; fontFamily: root.font
            hasCursor: root.has(1)
            onClicked: root.toggleVoice()
            onHovered: function(h) { if (h) root.hover(1) }
          }

          Column {
            width: parent.width
            spacing: Style.space(4)
            Text {
              text: (root.has(2) ? "› " : "") + "Reply language"
              color: root.has(2) ? root.fg : Qt.darker(root.fg, 1.2)
              font.family: root.font
              font.pixelSize: Style.font.bodySmall
              font.bold: root.has(2)
            }
            Choice {
              width: parent.width
              options: root.languages
              value: root.language
              foreground: root.fg; fontFamily: root.font
              cursorIndex: root.has(2) ? root.cursorCol : -1
              onPicked: function(v) { root.setLanguage(v) }
              onHoveredIndex: function(i) { root.hover(2, i) }
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(10)
            opacity: root.voiceOn ? 1 : 0.5

            PanelSectionHeader { text: "VOICE"; foreground: root.fg; fontFamily: root.font }
            Choice {
              width: parent.width
              options: root.voiceChoices
              value: root.voiceName
              foreground: root.fg; fontFamily: root.font
              cursorIndex: root.has(3) ? root.cursorCol : -1
              onPicked: function(v) { root.setVoiceName(v) }
              onHoveredIndex: function(i) { root.hover(3, i) }
            }
            SliderRow {
              id: speedRow
              width: parent.width
              bar: root.bar
              label: "Speed"
              valueText: speedRow.liveValue.toFixed(2) + "×"
              minimum: root.sliders.speed.min; maximum: root.sliders.speed.max; step: root.sliders.speed.step
              value: root.ttsValue("speed")
              foreground: root.fg; fontFamily: root.font
              hasCursor: root.has(4)
              onReleased: function(v) { root.setTts("speed", root.round(v, 0.05).toFixed(2)) }
              onHovered: root.hover(4)
            }
            Column {
              width: parent.width
              spacing: Style.space(4)
              Text {
                text: (root.has(5) ? "› " : "") + "Quality"
                color: root.has(5) ? root.fg : Qt.darker(root.fg, 1.2)
                font.family: root.font
                font.pixelSize: Style.font.bodySmall
                font.bold: root.has(5)
              }
              Choice {
                width: parent.width
                options: root.qualities
                value: String(root.ttsValue("steps"))
                foreground: root.fg; fontFamily: root.font
                cursorIndex: root.has(5) ? root.cursorCol : -1
                onPicked: function(v) { root.setTts("steps", v) }
                onHoveredIndex: function(i) { root.hover(5, i) }
              }
            }
            SliderRow {
              id: volumeRow
              width: parent.width
              bar: root.bar
              label: "Volume"
              valueText: Math.round(volumeRow.liveValue * 100) + "%"
              minimum: root.sliders.volume.min; maximum: root.sliders.volume.max; step: root.sliders.volume.step
              value: root.ttsValue("volume")
              foreground: root.fg; fontFamily: root.font
              hasCursor: root.has(6)
              onReleased: function(v) { root.setTts("volume", root.round(v, 0.05).toFixed(2)) }
              onHovered: root.hover(6)
            }
            SliderRow {
              id: pauseRow
              width: parent.width
              bar: root.bar
              label: "Pause between sentences"
              valueText: pauseRow.liveValue.toFixed(2) + " s"
              minimum: root.sliders.pause.min; maximum: root.sliders.pause.max; step: root.sliders.pause.step
              value: root.ttsValue("pause")
              foreground: root.fg; fontFamily: root.font
              hasCursor: root.has(7)
              onReleased: function(v) { root.setTts("pause", root.round(v, 0.05).toFixed(2)) }
              onHovered: root.hover(7)
            }
            Choice {
              width: parent.width
              options: [
                { value: "test", label: "Test", icon: "󰐊", tip: "Say a sample sentence" },
                { value: "reset", label: "Defaults", icon: "󰦛", tip: "Speed 1.05, balanced, full volume, no pause" }
              ]
              foreground: root.fg; fontFamily: root.font
              cursorIndex: root.has(8) ? root.cursorCol : -1
              onPicked: function(v) { if (v === "test") root.ctlRun(["say", root.sample]); else root.ctlRun(["tts", "reset"]) }
              onHoveredIndex: function(i) { root.hover(8, i) }
            }
          }
        }

        // ================= Brain =================
        Column {
          width: parent.width
          spacing: Style.space(10)
          visible: root.page === 3

          Column {
            width: parent.width
            spacing: Style.space(8)
            enabled: root.daemonAlive
            opacity: enabled ? 1 : 0.5
            PanelSectionHeader { text: "WHO ANSWERS"; foreground: root.fg; fontFamily: root.font }
            Choice {
              width: parent.width
              options: [
                { value: "auto", label: "Auto", icon: "󰁨", tip: "Claude Haiku; Codex when Claude's limit is hit" },
                { value: "claude", label: "Claude", icon: "󰚩", tip: "Claude Haiku only" },
                { value: "codex", label: "Codex", icon: "󰘦", tip: "GPT-6.1 Sol via Codex (saves Claude's allowance)" }
              ]
              value: root.brainMode
              foreground: root.fg; fontFamily: root.font
              cursorIndex: root.has(1) ? root.cursorCol : -1
              onPicked: function(v) { root.setBrain(v) }
              onHoveredIndex: function(i) { root.hover(1, i) }
            }
            Text {
              width: parent.width
              text: "Now: " + root.brainText
              color: root.dim
              font.family: root.font
              font.pixelSize: Style.font.caption
              elide: Text.ElideRight
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(8)
            enabled: root.daemonAlive && root.brainMode !== "codex"
            opacity: enabled ? 1 : 0.5
            PanelSectionHeader { text: "CLAUDE EFFORT"; foreground: root.fg; fontFamily: root.font }
            Choice {
              width: parent.width
              options: root.efforts
              value: root.effort
              foreground: root.fg; fontFamily: root.font
              cursorIndex: root.has(2) ? root.cursorCol : -1
              onPicked: function(v) { root.setEffort(v) }
              onHoveredIndex: function(i) { root.hover(2, i) }
            }
            Text {
              width: parent.width
              text: root.brainMode === "codex" ? "Codex only: Claude isn't used"
                    : "Higher thinks harder but answers slower. Changing it starts a fresh conversation."
              color: root.dim
              font.family: root.font
              font.pixelSize: Style.font.caption
              wrapMode: Text.Wrap
            }
          }

          PanelSectionHeader { text: "AUREN"; foreground: root.fg; fontFamily: root.font }
          Choice {
            width: parent.width
            options: [
              { value: "restart", label: "Restart", icon: "󰜉", tip: "Restart Auren" },
              { value: "log", label: "Log", icon: "󰦪", tip: "Open Auren's log" },
              { value: "power", label: root.daemonAlive || root.daemonActive ? "Turn off" : "Turn on", icon: "󰐥", tip: "Start or stop the voice daemon" }
            ]
            foreground: root.fg; fontFamily: root.font
            cursorIndex: root.has(3) ? root.cursorCol : -1
            onPicked: function(v, i) { [root.restartAuren, root.openLog, root.toggleAuren][i]() }
            onHoveredIndex: function(i) { root.hover(3, i) }
          }

          Text {
            width: parent.width
            text: root.todayCount === 0 ? "No requests today yet"
                  : "Today: " + root.todayCount + (root.todayCount === 1 ? " request" : " requests")
                    + " · " + root.todayAvg.toFixed(1) + " s average"
                    + (root.todayCodex ? " · " + root.todayCodex + " by Codex" : "")
            color: root.dim
            font.family: root.font
            font.pixelSize: Style.font.caption
            horizontalAlignment: Text.AlignHCenter
          }
        }
      }
    }
  }
}

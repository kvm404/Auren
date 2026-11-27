import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// auren in the Omarchy bar: the chosen mascot in pixels, showing what auren is doing, and a panel.
//
//   bar icon      bar colour when idle; the mascot's colour, bobbing, while auren listens/thinks/works/speaks;
//                 dim with shut eyes when auren is off; a dot when an update is waiting
//   click         the panel, laid out like Omarchy's settings panels: a header (status, Close), the
//                 pages on the left, the page on the right, and Auren's on/off, restart and log below:
//                   Home     the live mascot (click it to talk), status, Yes/No when it asks, last exchange, quick switches
//                   Mascot   Auren or Veyra; speech bubble, sleep animation, voice follows mascot, fade, size
//                   Voice    spoken replies in English, voice (voiceChoices), speed, volume,
//                            pause, test; listening: auto-send and its delay
//                   Brain    auto / Claude / Codex, Claude's effort, how much it asks before risky actions, today's numbers
//                   Vocabulary  words Auren should listen for
//                   Phone    the phone remote: where it listens, pair a phone (its QR code), the paired phones (unpair)
//                   Updates  the installed version, a check for a newer one, and the update (auren update)
//   right-click   start a fresh conversation
//
// Keys: arrows or hjkl. Up/down on the pages changes page; right (or Enter) goes into the page, and left
// from its first column comes back. In a page, up/down moves between rows, left/right between buttons
// or along a slider; Enter acts, Esc closes, Tab goes to the next bar panel.
//
// It reads $XDG_RUNTIME_DIR/auren/state.json (written by the daemon, including the voice settings), phone.json
// (its phone server: where it listens, who's connected; pairing runs auren phone pair --json),
// $XDG_RUNTIME_DIR/auren/update.json (written by auren update) and ~/.config/auren/mascot.json (the
// mascot's preferences, which it also writes), and talks to the daemon through
// ~/.local/share/auren/bin/auren-ctl; the services are started and stopped with systemctl --user.
Panel {
  id: root
  moduleName: "kvm404.auren"
  ipcTarget: "kvm404.auren"

  readonly property string home: Quickshell.env("HOME")
  readonly property string configDir: (Quickshell.env("XDG_CONFIG_HOME") || home + "/.config") + "/auren"
  readonly property string stateDir: (Quickshell.env("XDG_STATE_HOME") || home + "/.local/state") + "/auren"
  readonly property string dataDir: (Quickshell.env("XDG_DATA_HOME") || home + "/.local/share") + "/auren"
  readonly property string ctl: dataDir + "/bin/auren-ctl"
  readonly property string aurenBin: dataDir + "/bin/auren"   // install.sh's venv, or the package's /usr/bin/auren
  readonly property string runtimeDir: Quickshell.env("XDG_RUNTIME_DIR") + "/auren"

  readonly property color fg: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(fg, 1.55)
  readonly property string font: bar ? bar.fontFamily : Style.font.family

  // ---- the mascots ----------------------------------------------------------------------------
  readonly property var mascots: [
    { kind: "auren", name: "Auren", tip: "hover-bot", color: "#F5B83D", voice: "Trevor", hello: "Hey, I'm Auren." },
    { kind: "veyra", name: "Veyra", tip: "fox spirit", color: "#A08BF7", voice: "Juniper", hello: "Hi, I'm Veyra." }
  ]
  function mascotOf(k) { return mascots.find(function(m) { return m.kind === k }) }
  readonly property string kind: mascotOf(mascotPrefs.mascot) ? mascotPrefs.mascot : "auren"
  readonly property var mascot: mascotOf(kind)
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
  readonly property string codexEffort: st.codexEffort || "low"
  // Codex answers now (Codex only, or Auto after Claude's limit): the effort row is Codex's then
  readonly property bool codexAnswers: brainMode === "codex" || st.brain === "codex"
  // Codex's model as people say it: gpt-6-luna → GPT-6 Luna; other names as they are ("" from an older daemon)
  readonly property string codexName: {
    const parts = (st.codexModel || "").split("-")
    if (parts.length < 3 || parts[0] !== "gpt") return st.codexModel || ""
    return "GPT-" + parts[1] + " " + parts.slice(2).map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ")
  }
  readonly property string safety: st.safety || "ask"
  readonly property string voiceName: st.voiceName || voiceChoices[0].value
  // Auto-send: the daemon sends by itself once no new words come for autosendAfter seconds. Off, it reminds
  // the user to press Super+A after 3 s of quiet and sends after 12 s (daemon.REMIND_AFTER, SEND_AFTER).
  readonly property bool autosend: st.autosend === true
  readonly property real autosendAfter: st.autosendAfter || 5
  readonly property var autosendDelays: [
    { value: "2", label: "2 s", tip: "Quick: a short pause sends" },
    { value: "3", label: "3 s", tip: "Room for a breath" },
    { value: "5", label: "5 s", tip: "Room to think mid-sentence (the default)" },
    { value: "8", label: "8 s", tip: "Long pauses; slower to send" }
  ]
  readonly property var tts: st.tts || ({ speed: 1.0, volume: 1.0, pause: 0.0 })
  // Phonon's voices Auren offers, the default first: the same list as VOICES in src/auren/voice.py.
  readonly property var voiceChoices: [
    { value: "Everly", label: "Everly", tip: "US female, the default: click to hear her" },
    { value: "Juniper", label: "Juniper", tip: "US female, Veyra's voice: click to hear her" },
    { value: "Hugh", label: "Hugh", tip: "UK male: click to hear him" },
    { value: "Trevor", label: "Trevor", tip: "US male, Auren's voice: click to hear him" }
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
      property string mascot: "auren"
      property bool matchVoice: true      // picking a mascot also picks its voice (Auren speaks with Trevor)
    }
  }
  function savePrefs() { mascotPrefsFile.writeAdapter() }

  // the panel's own preferences
  FileView {
    id: panelPrefsFile
    path: root.configDir + "/panel.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    JsonAdapter {
      id: panelPrefs
      property bool notifyUpdates: true   // a notification when a new version is out (once per version)
    }
  }

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
  onOpenedChanged: {
    if (!opened) { confirmingUpdate = false; unpairAsk = ""; appShown = false; cancelPairing(); return }
    historyProc.running = false; historyProc.running = true
    cursorActive = false
    now = Date.now() / 1000
    if (page === 6) checkIfStale()
    if (page === 5) loadPhones()
    if (page === 4) loadVocab()
  }
  onMoodChanged: if (opened && mood === "idle") { historyProc.running = false; historyProc.running = true }
  property real now: Date.now() / 1000
  Timer { interval: 30000; running: root.opened; repeat: true; onTriggered: root.now = Date.now() / 1000 }

  // ---- the phone remote (auren phone) --------------------------------------------------------------
  // phone.json is the daemon's phone server: on (listening) or idle (no phone paired, so nothing listens),
  // where, why a path is missing, and the phones connected now. The paired phones come from auren phone devices.
  property var phoneSt: ({})
  FileView {
    id: phoneFile
    path: root.runtimeDir + "/phone.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      try { root.phoneSt = JSON.parse(text()) } catch (e) { root.phoneSt = ({}) }
      if (root.opened && root.page === 5) root.loadPhones()     // a phone came or went: its "last seen" moved
    }
    onLoadFailed: root.phoneSt = ({})
  }
  property var phones: []
  Process {
    id: phonesProc
    command: [root.aurenBin, "phone", "devices", "--json"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try { root.phones = JSON.parse(String(text || "").trim() || "[]") } catch (e) { root.phones = [] }
      }
    }
  }
  function loadPhones() { phonesProc.running = false; phonesProc.running = true }
  function viaName(v) { return v === "tailscale" ? "Tailscale" : "Wi-Fi" }
  function connectedVia(id) {
    var c = (root.phoneSt.on ? root.phoneSt.connected || [] : []).find(function(x) { return x.id === id })
    return c ? c.via : ""
  }
  readonly property string phoneStatus: {
    if (!root.daemonAlive) return "Auren is off, so the phone can't reach it."
    if (root.phoneSt.on) return "Listening on " + (root.phoneSt.addrs || []).filter(function(a) { return a.ok })
                                  .map(function(a) { return root.viaName(a.via) + " (" + a.ip + ")" }).join(" and ") + "."
    if (root.phoneSt.idle || root.phoneSt.on === undefined) return "Not listening: no phone is paired. Pair one to start."
    return "Not listening: " + (root.phoneSt.why || "the phone server is off") + "."
  }
  // Tailscale is installed but off: a phone then reaches the PC only on this Wi-Fi, and one paired now gets
  // no Tailscale address (auren phone pair says the same).
  readonly property string tailscaleWarn: {
    var why = (root.phoneSt.missing || {}).tailscale || ""
    return why && why.indexOf("isn't installed") < 0
           ? "Tailscale is off on this PC: phones reach it only on this Wi-Fi for now. Turn it on (tailscale up)"
             + " to use the phone from anywhere; phones find the PC there by themselves."
           : ""
  }

  // Pairing: auren phone pair --json writes the QR code (a PNG) and prints a line for it, then one when a
  // phone paired or the code expired. Cancel (or closing the panel) stops it, and the code stops working.
  property var pairing: ({})          // { uri, qr, expires, where, warn, firewall } while the code is up
  property string pairStage: ""       // "", "starting", "showing", "paired", "expired", "error"
  property string pairNote: ""
  Process {
    id: pairProc
    command: [root.aurenBin, "phone", "pair", "--json"]
    stdout: SplitParser {
      onRead: function(line) {
        var m
        try { m = JSON.parse(line) } catch (e) { return }
        if (m.uri) { root.pairing = m; root.pairStage = "showing"; root.now = Date.now() / 1000 }
        else if (m.paired) { root.pairStage = "paired"; root.pairNote = "Paired " + m.paired.name + "."; root.loadPhones() }
        else if (m.expired) { root.pairStage = "expired"; root.pairNote = "The code expired: pair again for a new one." }
        else if (m.error) { root.pairStage = "error"; root.pairNote = m.error }
      }
    }
    onExited: {
      if (root.pairStage === "starting" || root.pairStage === "showing") {
        root.pairStage = "error"
        root.pairNote = "Pairing stopped. auren doctor says why."
      }
      root.pairing = ({})
    }
  }
  function startPairing() {
    if (pairProc.running) return
    appShown = false
    pairing = ({}); pairNote = ""; if (!allowProc.running) allowNote = ""; pairStage = "starting"
    pairProc.running = true
  }

  // Let phones in: ufw blocks the phone's port (pairing says so in `firewall`). auren phone allow opens just that
  // port on just the blocked paths, through pkexec, so Omarchy's password prompt asks first.
  // The answer belongs to the pairing it was asked from (`allowFor`, its code): one that ended meanwhile keeps
  // its note, and a newer pairing keeps its own warning.
  property string allowNote: ""
  property string allowFor: ""
  property bool allowWaiting: false
  Process {
    id: allowProc
    command: [root.aurenBin, "phone", "allow", "--json"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.allowDone(String(text || ""))
    }
    // A command that can't start ends without any output: say so after the output had its chance.
    onRunningChanged: if (!running) Qt.callLater(function() { if (root.allowWaiting) root.allowDone("") })
  }
  function allowPhones() {
    if (allowProc.running) return
    allowNote = ""; allowFor = pairing.uri || ""; allowWaiting = true
    allowProc.running = true
  }
  function allowDone(out) {
    if (!allowWaiting) return
    allowWaiting = false
    var r
    try { r = JSON.parse(out.trim().split("\n").pop()) } catch (e) { r = { error: "auren phone allow didn't answer: auren doctor says why." } }
    if (r.ok && pairing.uri && pairing.uri === allowFor) { var p = Object.assign({}, pairing); p.firewall = ""; pairing = p }
    allowNote = r.ok ? "Phones can get in now." : (r.error || "That didn't work: auren doctor says why.")
  }
  function cancelPairing() {
    if (pairStage === "starting" || pairStage === "showing") pairStage = ""
    if (pairProc.running) pairProc.signal(15)
  }
  readonly property int pairLeft: Math.max(0, Math.round((root.pairing.expires || 0) - root.now))
  Timer { interval: 1000; running: root.pairStage === "showing"; repeat: true; onTriggered: root.now = Date.now() / 1000 }
  readonly property var pairActions: pairProc.running || pairStage === "starting" || pairStage === "showing"
    ? [{ label: "Cancel", icon: "󰅖", tip: "The code stops working at once", run: root.cancelPairing }].concat(
        root.pairing.firewall ? [{ label: allowProc.running ? "Asking…" : "Let phones in", icon: "󰒃",
          tip: "Opens Auren's phone port in the firewall (ufw), only where it's blocked. Asks for your password",
          enabled: !allowProc.running, run: root.allowPhones }] : [])
    : [{ label: root.phones.length ? "Pair another phone" : "Pair a phone", icon: "󰄜",
         tip: "A one-time QR code for the Auren app (works for 5 minutes)", enabled: root.daemonAlive, run: root.startPairing },
       { label: root.appShown ? "Hide the app code" : "Get the app", icon: "󰀲",
         tip: "A QR code to download the Auren app: scan it with the phone's camera", run: root.toggleApp }]

  // Get the app: auren phone app --json gives the download link (this version's APK on its GitHub release)
  // and a PNG of its QR code, for a phone without the app yet.
  property var appLink: ({})          // { url, qr, version }
  property bool appShown: false
  Process {
    id: appProc
    command: [root.aurenBin, "phone", "app", "--json"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try { root.appLink = JSON.parse(String(text || "").trim()) } catch (e) { root.appLink = ({}) }
        root.appShown = !!root.appLink.qr
      }
    }
  }
  function toggleApp() {
    if (appShown) { appShown = false; return }
    if (appLink.qr) { appShown = true; return }
    appProc.running = true
  }

  // Unpair asks once more (the phone has to pair again), then runs auren phone revoke.
  property string unpairAsk: ""
  Process { id: revokeProc; onExited: root.loadPhones() }
  function unpair(id) {
    if (unpairAsk !== id) { unpairAsk = id; return }
    unpairAsk = ""
    revokeProc.command = [root.aurenBin, "phone", "revoke", id]
    revokeProc.running = true
  }
  function phoneDetail(p) {
    var via = root.connectedVia(p.id)
    return via ? "Connected over " + root.viaName(via) : "Last seen " + root.ago(p.lastSeen)
  }

  // ---- vocabulary (auren vocab) -----------------------------------------------------------------
  // The user's own boost words. `auren vocab` checks them against the built-in list and tells the daemon,
  // which uses them from the next press.
  property var vocabMine: []
  property var vocabDefaults: []
  property int vocabLimit: 0
  property string vocabMessage: ""
  property bool vocabOk: true
  property string vocabAction: ""
  property string vocabSent: ""
  Process {
    id: vocabProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var r
        try { r = JSON.parse(String(text || "").trim().split("\n").pop()) }
        catch (e) { root.vocabMessage = "Couldn't reach Auren's vocabulary (is Auren installed?)."; root.vocabOk = false; return }
        root.vocabMine = r.mine || []
        root.vocabDefaults = r.defaults || []
        root.vocabLimit = r.limit || 0
        if (r.message) { root.vocabMessage = r.message; root.vocabOk = r.ok }
        if (r.ok && root.vocabAction === "add" && vocabField.text.trim() === root.vocabSent) vocabField.text = ""
      }
    }
  }
  function vocabRun(args) {
    if (vocabProc.running) return
    vocabAction = args[0]
    vocabProc.command = [root.aurenBin, "vocab"].concat(args).concat(["--json"])
    vocabProc.running = true
  }
  function loadVocab() { vocabRun(["list"]) }
  function addVocab() {
    var w = vocabField.text.trim()
    if (w === "") { root.vocabMessage = "Type a word first."; root.vocabOk = false; return }
    vocabSent = w
    vocabRun(["add", w])
  }
  function removeVocab(w) { vocabRun(["remove", w]) }

  // ---- updates (auren update) -------------------------------------------------------------------
  property var upd: ({})
  FileView {
    id: updateFile
    path: root.runtimeDir + "/update.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: { try { root.upd = JSON.parse(text()) } catch (e) {} }
    onLoadFailed: root.upd = ({})
  }
  readonly property string updState: upd.state || ""
  readonly property bool updateAvailable: updState === "available"
  readonly property bool updating: updState === "updating"
  property bool checking: false
  property bool confirmingUpdate: false
  Process {
    id: checkProc
    command: [root.aurenBin, "update", "check"].concat(panelPrefs.notifyUpdates ? ["--notify"] : [])
    // running, not exited: a command that can't start never exits
    onRunningChanged: if (!running) { root.checking = false; updateFile.reload() }
  }
  function checkUpdates() {
    if (checkProc.running || root.updating) return
    root.checking = true
    checkProc.running = true
  }
  function checkIfStale() { if (!root.updating && now - (upd.checked_at || 0) > 30 * 60) checkUpdates() }
  function startUpdate() {
    root.confirmingUpdate = false
    Quickshell.execDetached([root.aurenBin, "update", "apply"])
  }
  function openUpdateLog() {
    Quickshell.execDetached(["omarchy-launch-or-focus-tui", "less +G " + (upd.log || root.stateDir + "/update.log")])
    root.close()
  }
  // Quietly: a few minutes after the bar starts, then every 6 hours.
  Timer { interval: 3 * 60 * 1000; running: true; onTriggered: root.checkUpdates() }
  Timer { interval: 6 * 3600 * 1000; running: true; repeat: true; onTriggered: root.checkUpdates() }

  function ago(t) {
    if (!t) return "never"
    var s = Math.max(0, root.now - t)
    if (s < 60) return "just now"
    if (s < 3600) return Math.round(s / 60) + " min ago"
    if (s < 86400) return Math.round(s / 3600) + " h ago"
    return Math.round(s / 86400) + " days ago"
  }
  function day(t) { return t ? new Date(t * 1000).toLocaleDateString(Qt.locale(), "d MMM yyyy") : "" }
  // what's installed, as install.sh (or auren setup, for the package) recorded it (known before any check)
  property var installedInfo: ({})
  FileView {
    path: root.dataDir + "/install.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: { try { root.installedInfo = JSON.parse(text()) } catch (e) {} }
    onLoadFailed: root.installedInfo = ({})
  }
  // the package: pacman can update it after setup wrote install.json, so the last check's version wins
  readonly property bool packaged: installedInfo.mode === "package"
  readonly property string installedVersion: (packaged && (upd.installed || {}).version) || installedInfo.version || ""
  readonly property string versionText: "Auren" + (installedVersion ? " " + installedVersion : "")
                                         + (installedInfo.commit ? " · " + installedInfo.commit.slice(0, 7) : "")
  readonly property string updateHeadline: {
    if (root.updating) return upd.step || "Updating…"
    if (root.checking) return "Checking for updates…"
    switch (root.updState) {
      case "available": return "Release " + ((upd.latest || {}).tag || "update") + " available"
      case "current": return "You're up to date with releases"
      case "done": return upd.message || "Updated"
      case "failed": return "The update didn't finish"
      case "error": return "Couldn't check for updates"
      case "unknown": return "Version unknown"
    }
    return "Not checked yet"
  }
  readonly property string updateDetail: {
    if (root.updating) return "Auren restarts when it's done. You can close this panel."
    if (root.updState === "failed" || root.updState === "error" || root.updState === "unknown") return upd.message || ""
    if (root.updState === "done") return "Finished " + root.ago(upd.finished_at)
    return upd.checked_at ? "Checked " + root.ago(upd.checked_at) : ""
  }
  readonly property var updateActions: {
    if (root.confirmingUpdate) return [
      root.packaged
        ? { label: "Update in a terminal", icon: "󰚰", tip: "Runs yay there, which asks for your password", run: root.startUpdate }
        : { label: "Update and restart", icon: "󰚰", tip: "Waits until Auren is idle, installs, starts it again", run: root.startUpdate },
      { label: "Cancel", icon: "", run: function() { root.confirmingUpdate = false } }
    ]
    var list = []
    if (root.updateAvailable) list.push({ label: "Update now…", icon: "󰚰", tip: "See what happens, then update", run: function() { root.confirmingUpdate = true } })
    list.push({ label: root.checking ? "Checking…" : "Check for updates", icon: "󰑓", tip: "Look on GitHub for a newer version",
                enabled: !root.checking && !root.updating, run: root.checkUpdates })
    if (root.updating || root.updState === "failed" || root.updState === "done") list.push({ label: "Update log", icon: "󰦪", run: root.openUpdateLog })
    return list
  }

  // ---- actions --------------------------------------------------------------------------------
  // Commands run as argument lists (no shell). The plugin bar API has run(command) but no shellQuote.
  function ctlRun(args) {
    Quickshell.execDetached([root.ctl].concat(args))
    afterAction.restart()
  }
  function systemctl(verb, unit) {
    if (root.updating) return              // the update stops and starts Auren itself
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
  function setEffort(level) { if (level !== root.effort) ctlRun(["effort", level]) }
  function setCodexEffort(level) { if (level !== root.codexEffort) ctlRun(["codex-effort", level]) }
  function pickEffort(i) { if (codexAnswers) setCodexEffort(codexEfforts[i].value); else setEffort(efforts[i].value) }
  function setSafety(level) { if (level !== root.safety) ctlRun(["safety", level]) }
  function toggleAutosend() { ctlRun(["autosend", root.autosend ? "off" : "on"]) }
  function setAutosendAfter(v) { ctlRun(["autosend", "after", v]) }
  function answer(yes) { ctlRun([yes ? "yes" : "no"]) }
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
    var m = root.mascotOf(k)
    if (!m) return
    if (k !== root.kind) { mascotPrefs.mascot = k; savePrefs() }
    if (mascotPrefs.matchVoice && root.daemonAlive && root.voiceOn && root.mood === "idle")
      ctlRun(["voice-name", m.voice, m.hello])         // switch to its voice and say hello
  }
  function talk() {
    if (!root.daemonAlive) root.toggleAuren()
    else if (root.mood === "speaking" || root.mood === "thinking" || root.mood === "working") root.stopTalking()
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
    speed: { min: 0.8, max: 1.25, step: 0.05 },
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
      case "listening": return st.remind ? "Still listening" : "Listening…"
      case "thinking": return "Thinking…"
      case "working": return st.detail || "Working…"
      case "speaking": return "Speaking"
      case "error": return "Error: " + (st.detail || "")
    }
    return "Ready"
  }
  readonly property string hintText: !root.daemonAlive ? (root.daemonActive ? "Starting up" : "Click " + root.mascot.name + " or Turn on to start")
                                   : root.mood === "listening" ? (root.autosend && !st.remind ? "Pause or press Super+A to send" : "Press Super+A again to send")
                                   : root.mood === "thinking" || root.mood === "working" ? "Super+A to stop"
                                   : "Super+A to talk · Shift+Super+A for a fresh start"
  readonly property string brainText: (brainMode === "codex" ? (codexName ? "Codex (" + codexName + ")" : "Codex")
                                       : brainMode === "claude" ? "Claude Haiku only"
                                       : st.brain === "codex" ? "Codex for now (Claude's limit)" : "Claude Haiku")
                                      + ", " + (codexAnswers ? codexEffort : effort) + " effort"

  // Claude Code's --effort for the brain (Haiku 5.5). Low is decision 0003's: higher is slower and uses
  // more of the Claude allowance. Changing it restarts the brain, so the conversation starts fresh.
  readonly property var efforts: [
    { value: "low", label: "Low", tip: "Fastest; fine for most requests (the default)" },
    { value: "medium", label: "Medium", tip: "A bit more care, a bit slower" },
    { value: "high", label: "High", tip: "Harder requests; noticeably slower" },
    { value: "xhigh", label: "XHigh", tip: "Much slower; uses more of the Claude allowance" },
    { value: "max", label: "Max", tip: "Slowest and most expensive" }
  ]
  // Codex's reasoning effort (brain.codex_effort; low is decision 0003's). It applies from the next
  // request: Codex starts a process per request, so nothing restarts.
  readonly property var codexEfforts: [
    { value: "minimal", label: "Minimal", tip: "Fastest; barely thinks" },
    { value: "low", label: "Low", tip: "Quick; fine for most requests (the default)" },
    { value: "medium", label: "Medium", tip: "A bit more care, a bit slower" },
    { value: "high", label: "High", tip: "Harder requests; noticeably slower" },
    { value: "xhigh", label: "XHigh", tip: "Much slower; uses more of the ChatGPT allowance" }
  ]
  // How much Auren asks before a risky action (like Claude Code's permission modes). Publishing, root,
  // packages, secrets, buying and wiping ask at every level but Full access; sending asks even at Auto.
  readonly property var safeties: [
    { value: "ask", label: "Ask first", icon: "󰒃", tip: "Asks before every risky action; one yes answers everything waiting (the default)" },
    { value: "task", label: "Once per task", icon: "󰒓", tip: "Asks the first time; a yes covers the rest of that task" },
    { value: "free", label: "Auto", icon: "󰒙", tip: "Windows and files just run; sending, publishing, root, packages, secrets, buying and wiping still ask" },
    { value: "never", label: "Full access", icon: "󰦞", tip: "Nothing asks: everything just runs, even sending, buying and wiping" }
  ]
  readonly property string safetyText: safety === "task" ? "Asks the first time; a yes covers the rest of that task."
                                     : safety === "free" ? "Auto. Windows and files just run; sending, publishing, root, packages, secrets, buying and wiping ask."
                                     : safety === "never" ? "Full access. Nothing asks: everything just runs, even sending, publishing, root, buying and wiping."
                                     : "Asks before every risky action. One yes answers everything waiting."
  readonly property var brains: [
    { value: "auto", label: "Auto", icon: "󰁨", tip: "Claude Haiku; Codex when Claude's limit is hit" },
    { value: "claude", label: "Claude", icon: "󰚩", tip: "Claude Haiku only" },
    { value: "codex", label: "Codex", icon: "󰘦", tip: (root.codexName ? root.codexName + " via Codex" : "Codex") + " (saves Claude's allowance)" }
  ]
  readonly property string sample: "Hi, this is how I sound now. Does this pace work for you?"

  // Rows of buttons, each { label, icon, tip, run, on?, enabled? }: drawn by a Repeater and driven by the
  // keyboard through the same list.
  readonly property var quickActions: [
    { label: "On", icon: "󰐥", tip: "Auren on or off", on: root.daemonAlive || root.daemonActive,
      enabled: !root.updating, run: root.toggleAuren },
    { label: "Voice", icon: root.voiceOn ? "󰕾" : "󰖁", tip: "Spoken replies (off: only in the bubble)", on: root.voiceOn,
      enabled: root.daemonAlive, run: root.toggleVoice },
    { label: "Mascot", icon: "󰏩", tip: "Show or hide the mascot (hidden frees ~100 MB)", on: root.mascotActive,
      enabled: !root.updating, run: root.toggleMascot },
    { label: "Fresh", icon: "󰑓", tip: "Start a fresh conversation", enabled: root.daemonAlive, run: root.startFresh },
    { label: "Stop", icon: "󰓛", tip: "Stop listening, thinking or talking", enabled: root.daemonAlive, run: root.stopTalking }
  ]
  readonly property var mascotActions: [
    { label: "Reset place", icon: "󰦛", tip: "Back to the bottom-right corner, normal size", enabled: root.mascotActive, run: function() { root.mascotIpc(["reset"]) } },
    { label: "Poke", icon: "󰏩", tip: "Say hi", enabled: root.mascotActive, run: function() { root.mascotIpc(["poke"]) } }
  ]
  readonly property var voiceActions: [
    { label: "Test", icon: "󰐊", tip: "Say a sample sentence", run: function() { root.ctlRun(["say", root.sample]) } },
    { label: "Defaults", icon: "󰦛", tip: "Speed 1.0, full volume, no pause", run: function() { root.ctlRun(["tts", "reset"]) } }
  ]
  readonly property var footerActions: [
    { label: root.daemonAlive || root.daemonActive ? "Turn off" : "Turn on", icon: "󰐥", tip: "Start or stop Auren",
      enabled: !root.updating, run: root.toggleAuren },
    { label: "Restart", icon: "󰜉", tip: "Restart Auren", enabled: !root.updating, run: root.restartAuren },
    { label: "Log", icon: "󰦪", tip: "Open Auren's log", run: root.openLog }
  ]
  function asOptions(list) {
    return list.map(function(a, i) { return { value: String(i), label: a.label, icon: a.icon, tip: a.tip || "", enabled: a.enabled, on: a.on } })
  }
  function runAction(list, i) { var a = list[i]; if (a && a.enabled !== false) a.run() }

  // ---- pages and the keyboard cursor --------------------------------------------------------------
  readonly property var pages: [
    { label: "Home", icon: "󰋜" },
    { label: "Mascot", icon: "󰏩" },
    { label: "Voice", icon: "󰗋" },
    { label: "Brain", icon: "󰧑" },
    { label: "Vocabulary", icon: "󰈙" },
    { label: "Phone", icon: "󰄜" },
    { label: "Updates", icon: "󰚰" }
  ]
  property int page: 0
  function showPage(p) {
    page = Math.max(0, Math.min(pages.length - 1, p))
    confirmingUpdate = false
    unpairAsk = ""
    pagesBox.contentY = 0
    keyCatcher.forceActiveFocus()
    if (page === 4) { vocabMessage = ""; loadVocab() }
    if (page === 5) loadPhones()
    if (page === 6) checkIfStale()
  }

  // Each page is rows of controls: { n: how many across, act(col), adjust(dx) for sliders }; the footer
  // is the last row of every page.
  readonly property var pageRows: {
    if (page === 1) return [
      { n: root.mascots.length, act: function(c) { root.setMascot(root.mascots[c].kind) } },
      { n: 1, act: function() { root.togglePref("bubble") } },
      { n: 1, act: function() { root.togglePref("sleepAnimation") } },
      { n: 1, act: function() { root.togglePref("matchVoice") } },
      { n: 1, adjust: function(dx) { root.nudge("opacity", dx) } },
      { n: 1, adjust: function(dx) { root.nudge("size", dx) } },
      { n: root.mascotActions.length, act: function(c) { root.runAction(root.mascotActions, c) } }]
    if (page === 2) return [
      { n: 1, act: function() { root.toggleVoice() } },
      { n: root.voiceChoices.length, act: function(c) { root.setVoiceName(root.voiceChoices[c].value) } },
      { n: 1, adjust: function(dx) { root.nudge("speed", dx) } },
      { n: 1, adjust: function(dx) { root.nudge("volume", dx) } },
      { n: 1, adjust: function(dx) { root.nudge("pause", dx) } },
      { n: root.voiceActions.length, act: function(c) { root.runAction(root.voiceActions, c) } },
      { n: 1, act: function() { root.toggleAutosend() } }
    ].concat(root.autosend ? [{ n: root.autosendDelays.length, act: function(c) { root.setAutosendAfter(root.autosendDelays[c].value) } }] : [])
    if (page === 3) return [
      { n: 3, act: function(c) { root.setBrain(root.brains[c].value) } },
      { n: (root.codexAnswers ? root.codexEfforts : root.efforts).length, act: function(c) { root.pickEffort(c) } },
      { n: root.safeties.length, act: function(c) { root.setSafety(root.safeties[c].value) } }]
    if (page === 4) return [
      { n: 2, act: function(c) { if (c === 0) vocabField.forceActiveFocus(); else root.addVocab() } }
    ].concat(root.vocabMine.length ? [{ n: root.vocabMine.length, act: function(c) { root.removeVocab(root.vocabMine[c]) } }] : [])
    if (page === 5) return [
      { n: root.pairActions.length, act: function(c) { root.runAction(root.pairActions, c) } }
    ].concat(root.phones.length ? [{ n: root.phones.length, act: function(c) { root.unpair(root.phones[c].id) } }] : [])
    if (page === 6) return [
      { n: root.updateActions.length, act: function(c) { root.runAction(root.updateActions, c) } },
      { n: 1, act: function() { panelPrefs.notifyUpdates = !panelPrefs.notifyUpdates; panelPrefsFile.writeAdapter() } }]
    return [{ n: 1, act: function() { root.talk() } }]
      .concat(root.awaiting ? [{ n: 2, act: function(c) { root.answer(c === 0) } }] : [])
      .concat([{ n: root.quickActions.length, act: function(c) { root.runAction(root.quickActions, c) } }])
  }
  readonly property int quickRow: awaiting ? 2 : 1       // Home's rows: the mascot, [Yes/No while it asks], quick actions
  readonly property var rows: pageRows.concat([{ n: footerActions.length, act: function(c) { root.runAction(root.footerActions, c) } }])
  readonly property int footerRow: rows.length - 1
  onRowsChanged: {
    cursorRow = Math.min(cursorRow, rows.length - 1)
    cursorCol = Math.min(cursorCol, rows[cursorRow].n - 1)
  }
  property bool cursorActive: false
  property bool inNav: true
  property int cursorRow: 0
  property int cursorCol: 0
  function has(row, col) { return cursorActive && !inNav && cursorRow === row && (col === undefined || cursorCol === col) }
  function enterPage() { inNav = false; cursorRow = 0; cursorCol = 0 }
  function moveCursor(dx, dy) {
    if (!cursorActive) { cursorActive = true; inNav = true; return }
    if (inNav) {
      if (dy !== 0) showPage(page + dy)
      else if (dx > 0) enterPage()
      return
    }
    if (dy !== 0) {
      cursorRow = Math.max(0, Math.min(rows.length - 1, cursorRow + dy))
      cursorCol = Math.min(cursorCol, rows[cursorRow].n - 1)
      // on a short screen, scroll along with the cursor (roughly: the first row at the top, the last at
      // the bottom)
      if (cursorRow < pageRows.length && pageRows.length > 1)
        pagesBox.contentY = Math.max(0, pagesBox.contentHeight - pagesBox.height) * cursorRow / (pageRows.length - 1)
      else if (cursorRow >= pageRows.length)
        pagesBox.contentY = Math.max(0, pagesBox.contentHeight - pagesBox.height)
      vocabScroll()
      return
    }
    var r = rows[cursorRow]
    if (r.adjust) { r.adjust(dx); return }
    if (dx < 0 && cursorCol === 0) { inNav = true; return }
    cursorCol = Math.max(0, Math.min(r.n - 1, cursorCol + dx))
    vocabScroll()
  }
  // On the Vocabulary page the chips wrap over several lines: scroll to the one the cursor is on.
  function vocabScroll() {
    if (page !== 4 || inNav) return
    if (cursorRow !== 1) { pagesBox.contentY = 0; return }
    var chip = chipRepeater.itemAt(cursorCol)
    if (!chip) return
    var y = chip.mapToItem(vocabPage, 0, 0).y
    if (y < pagesBox.contentY) pagesBox.contentY = y
    else if (y + chip.height > pagesBox.contentY + pagesBox.height) pagesBox.contentY = y + chip.height - pagesBox.height
  }
  function activate() {
    if (!cursorActive) return
    if (inNav) { enterPage(); return }
    var r = rows[cursorRow]
    if (r.act) r.act(cursorCol)
  }
  function hover(row, col) { cursorActive = true; inNav = false; cursorRow = row; cursorCol = col || 0 }

  // ---- the bar icon ---------------------------------------------------------------------------
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    tooltipText: root.opened ? "" : "Auren: " + root.statusText + (root.updateAvailable ? " · update available" : "")
    iconComponent: Component {
      MiniMascot {
        kind: root.kind
        mood: root.awaiting ? "awaiting" : root.mood
        foreground: button.foreground
        Rectangle {       // an update is waiting
          visible: root.updateAvailable
          width: Math.max(4, Math.round(parent.width / 4))
          height: width
          radius: width / 2
          anchors.right: parent.right
          anchors.top: parent.top
          anchors.rightMargin: -width / 2
          anchors.topMargin: -width / 3
          color: Color.accent
        }
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
    contentWidth: panel.fittedContentWidth(Style.space(600))
    contentHeight: panel.fittedContentHeight(body.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: vocabField.activeFocus
      onMoveRequested: function(dx, dy) { root.moveCursor(dx, dy) }
      onActivateRequested: root.activate()
      onCloseRequested: { if (root.confirmingUpdate) root.confirmingUpdate = false; else root.close() }
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Item {
        id: body
        anchors.fill: parent
        implicitHeight: header.implicitHeight + Style.space(16) + Math.max(nav.implicitHeight, pagesBox.tallest)
                        + Style.space(16) + footer.implicitHeight

        // ================= header =================
        Column {
          id: header
          width: parent.width
          spacing: Style.space(12)
          Item {
            width: parent.width
            implicitHeight: Math.max(heading.implicitHeight, closeButton.implicitHeight)
            Column {
              id: heading
              anchors.left: parent.left
              anchors.right: closeButton.left
              anchors.rightMargin: Style.space(12)
              spacing: Style.space(3)
              Text {
                text: "Auren"
                color: root.fg
                font.family: root.font
                font.pixelSize: Style.font.title
                font.bold: true
              }
              Text {
                width: parent.width
                text: root.statusText + " · " + root.hintText
                color: root.fg
                opacity: 0.7
                font.family: root.font
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
              }
            }
            Button {
              id: closeButton
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: "Close"
              foreground: root.fg
              fontFamily: root.font
              fontSize: Style.font.bodySmall
              onClicked: root.close()
            }
          }
          PanelSeparator { foreground: root.fg }
        }

        // ================= pages =================
        Column {
          id: nav
          anchors.top: header.bottom
          anchors.topMargin: Style.space(16)
          width: Style.space(126)
          spacing: Style.space(6)
          Repeater {
            model: root.pages
            Button {
              required property var modelData
              required property int index
              width: nav.width
              leftAlign: true
              text: modelData.label + (index === 6 && root.updateAvailable ? "  ●" : "")
              iconText: modelData.icon
              iconSize: Style.font.body
              selected: root.page === index
              hasCursor: root.cursorActive && root.inNav && root.page === index
              bordered: true
              foreground: root.fg
              fontFamily: root.font
              fontSize: Style.font.bodySmall
              onClicked: { root.showPage(index); root.inNav = true; root.cursorRow = 0; root.cursorCol = 0 }
            }
          }
        }

        Flickable {
          id: pagesBox
          anchors.top: header.bottom
          anchors.topMargin: Style.space(16)
          anchors.left: nav.right
          anchors.leftMargin: Style.space(20)
          anchors.right: parent.right
          anchors.bottom: footer.top
          anchors.bottomMargin: Style.space(16)
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          contentWidth: width
          // the panel is as tall as the tallest page, so it keeps its size from page to page; on a short
          // screen it's capped and the page scrolls
          readonly property real tallest: Math.max(homePage.implicitHeight, mascotPage.implicitHeight,
                                                   voicePage.implicitHeight, brainPage.implicitHeight,
                                                   Math.min(vocabPage.implicitHeight, Style.space(380)),
                                                   Math.min(phonePage.implicitHeight, Style.space(420)),
                                                   updatesPage.implicitHeight)
          contentHeight: [homePage, mascotPage, voicePage, brainPage, vocabPage, phonePage, updatesPage][root.page].implicitHeight

          // ---------------- Home ----------------
          Column {
            id: homePage
            width: parent.width
            spacing: Style.space(12)
            visible: root.page === 0

            SectionTitle { text: "HOME" }

            Card {      // the live mascot · name · status
              id: heroCard
              hot: heroMouse.containsMouse || root.has(0)
              implicitHeight: Math.max(heroBox.height, heroText.implicitHeight) + Style.space(20)

              Item {
                id: heroBox
                width: Style.space(84)
                height: Style.space(66)
                anchors.left: parent.left
                anchors.leftMargin: Style.space(10)
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
              }

              Column {
                id: heroText
                anchors.left: heroBox.right
                anchors.leftMargin: Style.space(14)
                anchors.right: parent.right
                anchors.rightMargin: Style.space(12)
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.space(3)
                Text {
                  text: root.mascot.name
                  color: root.fg
                  font.family: root.font
                  font.pixelSize: Style.font.subtitle
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
                  text: !root.daemonAlive ? "Click to start" : root.mood === "idle" || root.mood === "listening" ? "Click to talk" : "Click to stop"
                  color: root.dim
                  font.family: root.font
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }
              }

              MouseArea {      // click to talk (click again to send), right-click to poke
                id: heroMouse
                anchors.fill: parent
                hoverEnabled: true
                acceptedButtons: Qt.LeftButton | Qt.RightButton
                cursorShape: Qt.PointingHandCursor
                onEntered: root.hover(0)
                onPositionChanged: (mouse) => {
                  var p = mapToItem(heroBox, mouse.x, mouse.y)
                  if (hero.item) { hero.item.pointerX = p.x / heroBox.width; hero.item.pointerY = p.y / heroBox.height }
                }
                onClicked: (mouse) => {
                  if (mouse.button === Qt.RightButton) { if (hero.item) hero.item.poke() }
                  else root.talk()
                }
              }
            }

            Column {      // a question is waiting: one click answers all of it
              width: parent.width
              spacing: Style.space(6)
              visible: root.awaiting
              Choice {
                width: parent.width
                title: "WAITING FOR YOUR ANSWER"
                options: [{ value: "yes", label: "Yes", icon: "󰄬" }, { value: "no", label: "No", icon: "󰅖" }]
                value: ""
                foreground: root.fg; fontFamily: root.font
                cursorIndex: root.has(1) ? root.cursorCol : -1
                onPicked: function(v) { root.answer(v === "yes") }
                onHoveredIndex: function(i) { root.hover(1, i) }
              }
              Text {
                width: parent.width
                text: root.st.question || ""
                color: root.fg
                font.family: root.font
                font.pixelSize: Style.font.bodySmall
                wrapMode: Text.Wrap
                maximumLineCount: 14          // a batch is at most 6 questions (safety.MAX_BATCH)
                elide: Text.ElideRight
              }
            }

            Choice {
              width: parent.width
              title: "QUICK ACTIONS"
              options: root.asOptions(root.quickActions)
              value: ""
              foreground: root.fg; fontFamily: root.font
              cursorIndex: root.has(root.quickRow) ? root.cursorCol : -1
              onPicked: function(v, i) { root.runAction(root.quickActions, i) }
              onHoveredIndex: function(i) { root.hover(root.quickRow, i) }
            }

            Column {      // the last exchange
              width: parent.width
              spacing: Style.space(6)
              visible: root.lastExchange !== null
              PanelSectionHeader { text: "LAST EXCHANGE"; foreground: root.fg; fontFamily: root.font }
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
          }

          // ---------------- Mascot ----------------
          Column {
            id: mascotPage
            width: parent.width
            spacing: Style.space(12)
            visible: root.page === 1

            SectionTitle { text: "MASCOT" }

            Row {         // the mascots, live
              id: cards
              width: parent.width
              spacing: Style.space(8)
              readonly property real cell: (width - spacing * (root.mascots.length - 1)) / root.mascots.length
              Repeater {
                model: root.mascots
                Rectangle {
                  id: card
                  required property var modelData
                  required property int index
                  readonly property bool chosen: root.kind === modelData.kind
                  readonly property bool hot: cardMouse.containsMouse || root.has(0, index)
                  width: cards.cell
                  height: preview.height + names.implicitHeight + Style.space(10)
                  radius: Style.cornerRadius
                  color: chosen ? Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.07) : "transparent"
                  border.width: chosen ? 2 : 1
                  border.color: chosen ? modelData.color : hot ? root.fg : Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.18)

                  Item {
                    id: preview
                    width: parent.width
                    height: Style.space(92)
                    Loader {
                      id: previewLoader
                      active: root.opened && root.page === 1
                      anchors.centerIn: parent
                      source: root.mascotFile(card.modelData.kind)
                      onLoaded: { item.u = Math.floor(preview.height / 18 * 2) / 2; item.sleepAnimation = false }
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
                      text: card.modelData.tip + " · " + card.modelData.voice
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
                    onEntered: root.hover(0, card.index)
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
              hasCursor: root.has(1)
              onClicked: root.togglePref("bubble")
              onHovered: function(h) { if (h) root.hover(1) }
            }
            Toggle {
              width: parent.width
              label: "Sleep animation"
              description: mascotPrefs.sleepAnimation ? "Breathes while asleep (~6% CPU)" : "Still while asleep (saves CPU)"
              checked: mascotPrefs.sleepAnimation
              foreground: root.fg; fontFamily: root.font
              hasCursor: root.has(2)
              onClicked: root.togglePref("sleepAnimation")
              onHovered: function(h) { if (h) root.hover(2) }
            }
            Toggle {
              width: parent.width
              label: "Voice follows mascot"
              description: root.mascots.map(function(m) { return m.name + " speaks with " + m.voice }).join(", ")
              checked: mascotPrefs.matchVoice
              foreground: root.fg; fontFamily: root.font
              hasCursor: root.has(3)
              onClicked: root.togglePref("matchVoice")
              onHovered: function(h) { if (h) root.hover(3) }
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
              hasCursor: root.has(4)
              onReleased: function(v) { mascotPrefs.idleOpacity = root.round(v, 0.05); root.savePrefs() }
              onHovered: root.hover(4)
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
              hasCursor: root.has(5)
              onReleased: function(v) { root.mascotIpc(["size", root.round(v, 0.1).toFixed(2)]) }
              onHovered: root.hover(5)
            }
            Choice {
              width: parent.width
              options: root.asOptions(root.mascotActions)
              foreground: root.fg; fontFamily: root.font
              cursorIndex: root.has(6) ? root.cursorCol : -1
              onPicked: function(v, i) { root.runAction(root.mascotActions, i) }
              onHoveredIndex: function(i) { root.hover(6, i) }
            }
          }

          // ---------------- Voice ----------------
          Column {
            id: voicePage
            width: parent.width
            spacing: Style.space(12)
            visible: root.page === 2
            enabled: root.daemonAlive

            SectionTitle { text: "VOICE" }

            Toggle {
              width: parent.width
              label: "Voice replies"
              description: !root.daemonAlive ? "Auren is off" : root.voiceOn ? "Auren speaks its answers" : "Answers only in the bubble"
              checked: root.voiceOn
              foreground: root.fg; fontFamily: root.font
              hasCursor: root.has(0)
              onClicked: root.toggleVoice()
              onHovered: function(h) { if (h) root.hover(0) }
            }
            Column {
              width: parent.width
              spacing: Style.space(12)
              opacity: root.voiceOn ? 1 : 0.5

              Choice {
                width: parent.width
                title: "VOICE"
                options: root.voiceChoices
                value: root.voiceName
                foreground: root.fg; fontFamily: root.font
                cursorIndex: root.has(1) ? root.cursorCol : -1
                onPicked: function(v) { root.setVoiceName(v) }
                onHoveredIndex: function(i) { root.hover(1, i) }
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
                hasCursor: root.has(2)
                onReleased: function(v) { root.setTts("speed", root.round(v, 0.05).toFixed(2)) }
                onHovered: root.hover(2)
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
                hasCursor: root.has(3)
                onReleased: function(v) { root.setTts("volume", root.round(v, 0.05).toFixed(2)) }
                onHovered: root.hover(3)
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
                hasCursor: root.has(4)
                onReleased: function(v) { root.setTts("pause", root.round(v, 0.05).toFixed(2)) }
                onHovered: root.hover(4)
              }
              Choice {
                width: parent.width
                options: root.asOptions(root.voiceActions)
                foreground: root.fg; fontFamily: root.font
                cursorIndex: root.has(5) ? root.cursorCol : -1
                onPicked: function(v, i) { root.runAction(root.voiceActions, i) }
                onHoveredIndex: function(i) { root.hover(5, i) }
              }
            }

            SectionTitle { text: "LISTENING" }

            Toggle {
              width: parent.width
              label: "Auto-send"
              description: root.autosend ? "Sends after " + root.autosendAfter + " s without new words"
                                         : "Reminds you to press Super+A after 3 s of quiet; sends after 12 s"
              checked: root.autosend
              foreground: root.fg; fontFamily: root.font
              hasCursor: root.has(6)
              onClicked: root.toggleAutosend()
              onHovered: function(h) { if (h) root.hover(6) }
            }
            Choice {
              width: parent.width
              visible: root.autosend
              title: "SEND AFTER"
              options: root.autosendDelays
              value: String(root.autosendAfter)
              foreground: root.fg; fontFamily: root.font
              cursorIndex: root.has(7) ? root.cursorCol : -1
              onPicked: function(v) { root.setAutosendAfter(v) }
              onHoveredIndex: function(i) { root.hover(7, i) }
            }
          }

          // ---------------- Brain ----------------
          Column {
            id: brainPage
            width: parent.width
            spacing: Style.space(12)
            visible: root.page === 3

            SectionTitle { text: "BRAIN" }

            Choice {
              width: parent.width
              title: "WHO ANSWERS"
              enabled: root.daemonAlive
              options: root.brains
              value: root.brainMode
              foreground: root.fg; fontFamily: root.font
              cursorIndex: root.has(0) ? root.cursorCol : -1
              onPicked: function(v) { root.setBrain(v) }
              onHoveredIndex: function(i) { root.hover(0, i) }
            }
            Caption { text: "Now: " + root.brainText }

            Choice {
              width: parent.width
              title: root.codexAnswers ? "CODEX EFFORT" : "CLAUDE EFFORT"
              enabled: root.daemonAlive
              options: root.codexAnswers ? root.codexEfforts : root.efforts
              value: root.codexAnswers ? root.codexEffort : root.effort
              foreground: root.fg; fontFamily: root.font
              cursorIndex: root.has(1) ? root.cursorCol : -1
              onPicked: function(v) { if (root.codexAnswers) root.setCodexEffort(v); else root.setEffort(v) }
              onHoveredIndex: function(i) { root.hover(1, i) }
            }
            Caption {
              text: root.brainMode === "codex" ? "Codex only: Claude isn't used. Higher thinks harder but answers slower."
                    : root.codexAnswers ? "Codex answers until Claude's limit resets. Higher thinks harder but answers slower."
                    : "Higher thinks harder but answers slower. Changing it starts a fresh conversation."
            }

            Choice {
              width: parent.width
              title: "RISKY ACTIONS"
              enabled: root.daemonAlive
              options: root.safeties
              value: root.safety
              foreground: root.fg; fontFamily: root.font
              cursorIndex: root.has(2) ? root.cursorCol : -1
              onPicked: function(v) { root.setSafety(v) }
              onHoveredIndex: function(i) { root.hover(2, i) }
            }
            Caption { text: root.safetyText }

            PanelSectionHeader { text: "TODAY"; foreground: root.fg; fontFamily: root.font }
            Card {
              implicitHeight: today.implicitHeight + Style.space(20)
              Text {
                id: today
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.margins: Style.space(12)
                anchors.verticalCenter: parent.verticalCenter
                text: root.todayCount === 0 ? "No requests yet"
                      : root.todayCount + (root.todayCount === 1 ? " request" : " requests")
                        + " · " + root.todayAvg.toFixed(1) + " s on average"
                        + (root.todayCodex ? " · " + root.todayCodex + " answered by Codex" : "")
                color: root.fg
                font.family: root.font
                font.pixelSize: Style.font.bodySmall
                wrapMode: Text.Wrap
              }
            }
          }

          // ---------------- Vocabulary ----------------
          Column {
            id: vocabPage
            width: parent.width
            spacing: Style.space(12)
            visible: root.page === 4

            SectionTitle { text: "VOCABULARY" }
            Caption {
              text: "Names and words Auren should listen for: projects, tools, people. A new word is used the next time"
                    + " you talk to Auren; no restart needed."
            }

            Row {
              width: parent.width
              spacing: Style.space(8)
              TextField {
                id: vocabField
                width: parent.width - addButton.width - parent.spacing
                placeholderText: "Add a word or name"
                font.family: root.font
                font.pixelSize: Style.font.bodySmall
                foreground: root.fg
                hasCursor: root.has(0, 0)
                onAccepted: root.addVocab()
                onTextEdited: root.vocabMessage = ""
                Keys.onEscapePressed: keyCatcher.forceActiveFocus()
                Keys.onTabPressed: { keyCatcher.forceActiveFocus(); root.switchPanel(1) }
                Keys.onBacktabPressed: { keyCatcher.forceActiveFocus(); root.switchPanel(-1) }
                onHoveredChanged: if (hovered) root.hover(0, 0)
              }
              Button {
                id: addButton
                anchors.verticalCenter: vocabField.verticalCenter
                text: "Add"
                iconText: "󰐕"
                iconSize: Style.font.body
                fontSize: Style.font.bodySmall
                foreground: root.fg
                fontFamily: root.font
                bordered: true
                enabled: !vocabProc.running
                opacity: enabled ? 1 : 0.5
                hasCursor: root.has(0, 1)
                onClicked: root.addVocab()
                onHovered: function(h) { if (h) root.hover(0, 1) }
              }
            }
            Text {
              width: parent.width
              visible: root.vocabMessage !== ""
              text: root.vocabMessage
              color: root.vocabOk ? root.fg : Color.accent
              wrapMode: Text.WordWrap
              font.family: root.font
              font.pixelSize: Style.font.bodySmall
            }

            PanelSectionHeader {
              text: "YOUR WORDS · " + root.vocabMine.length + (root.vocabLimit ? " OF " + root.vocabLimit : "")
              foreground: root.fg
              fontFamily: root.font
            }
            Caption {
              visible: root.vocabMine.length === 0
              text: "None yet. Add the names Auren keeps mishearing."
            }
            Flow {
              width: parent.width
              spacing: Style.space(6)
              Repeater {
                id: chipRepeater
                model: root.vocabMine
                Button {
                  required property string modelData
                  required property int index
                  text: modelData
                  iconText: "󰅖"
                  tooltipText: "Remove"
                  iconSize: Style.font.caption
                  fontSize: Style.font.bodySmall
                  foreground: root.fg
                  fontFamily: root.font
                  bordered: true
                  hasCursor: root.has(1, index)
                  onClicked: root.removeVocab(modelData)
                  onHovered: function(h) { if (h) root.hover(1, index) }
                }
              }
            }

            PanelSectionHeader {
              text: "BUILT IN · " + root.vocabDefaults.length
              foreground: root.fg
              fontFamily: root.font
            }
            Caption { text: root.vocabDefaults.join(", ") }
          }

          // ---------------- Phone ----------------
          Column {
            id: phonePage
            width: parent.width
            spacing: Style.space(12)
            visible: root.page === 5

            SectionTitle { text: "PHONE" }
            Caption { text: "Talk to Auren from the Auren app on your Android phone: on this Wi-Fi, or anywhere over Tailscale." }

            Card {
              implicitHeight: phoneStatusText.implicitHeight + Style.space(20)
              Text {
                id: phoneStatusText
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.margins: Style.space(12)
                anchors.verticalCenter: parent.verticalCenter
                text: root.phoneStatus
                color: root.fg
                font.family: root.font
                font.pixelSize: Style.font.bodySmall
                wrapMode: Text.Wrap
              }
            }
            Caption {
              visible: root.tailscaleWarn !== ""
              text: root.tailscaleWarn
              color: Color.accent
            }

            Choice {
              width: parent.width
              title: "PAIR"
              options: root.asOptions(root.pairActions)
              foreground: root.fg; fontFamily: root.font
              cursorIndex: root.has(0) ? root.cursorCol : -1
              onPicked: function(v, i) { root.runAction(root.pairActions, i) }
              onHoveredIndex: function(i) { root.hover(0, i) }
            }
            Caption {
              visible: root.pairStage === "starting"
              text: "Opening a pairing…"
            }
            Caption {         // Let phones in's answer: outside the QR code, so it stays when the pairing ends
              visible: root.allowNote !== ""
              text: root.allowNote
            }
            Row {         // the QR code, while it works
              width: parent.width
              spacing: Style.space(14)
              visible: root.pairStage === "showing" && !!root.pairing.qr
              Rectangle {
                id: qrBox
                width: Style.space(176)
                height: width
                radius: Style.cornerRadius
                color: "white"
                Image {
                  anchors.fill: parent
                  anchors.margins: Style.space(4)
                  // the same path each time: the expiry in the URL makes a new code a new image
                  source: root.pairing.qr ? "file://" + root.pairing.qr + "?" + root.pairing.expires : ""
                  cache: false
                  smooth: false
                  fillMode: Image.PreserveAspectFit
                }
              }
              Column {
                width: parent.width - qrBox.width - parent.spacing
                spacing: Style.space(6)
                Text {
                  width: parent.width
                  text: "Scan it in the Auren app (Pair)."
                  color: root.fg
                  font.family: root.font
                  font.pixelSize: Style.font.bodySmall
                  font.bold: true
                  wrapMode: Text.Wrap
                }
                Caption {
                  text: "It works once, for " + Math.floor(root.pairLeft / 60) + ":" + ("0" + root.pairLeft % 60).slice(-2)
                        + " more, from a phone on "
                        + (root.pairing.where || []).map(function(a) { return root.viaName(a.via) + " (" + a.ip + ")" }).join(" or ")
                        + "."
                }
                Caption {
                  visible: !!root.pairing.warn
                  text: root.pairing.warn || ""
                  color: Color.accent
                }
                Caption {
                  visible: !!root.pairing.firewall
                  text: (root.pairing.firewall || "") + " Let phones in opens Auren's port (it asks for your password)."
                  color: Color.accent
                }
              }
            }
            Row {         // Get the app: the download's QR code, for the phone's camera
              width: parent.width
              spacing: Style.space(14)
              visible: root.appShown && !!root.appLink.qr
              Rectangle {
                id: appQrBox
                width: Style.space(176)
                height: width
                radius: Style.cornerRadius
                color: "white"
                Image {
                  anchors.fill: parent
                  anchors.margins: Style.space(4)
                  source: root.appLink.qr ? "file://" + root.appLink.qr : ""
                  cache: false
                  smooth: false
                  fillMode: Image.PreserveAspectFit
                }
              }
              Column {
                width: parent.width - appQrBox.width - parent.spacing
                spacing: Style.space(6)
                Text {
                  width: parent.width
                  text: "Scan it with your phone's camera."
                  color: root.fg
                  font.family: root.font
                  font.pixelSize: Style.font.bodySmall
                  font.bold: true
                  wrapMode: Text.Wrap
                }
                Caption {
                  text: (root.appLink.version ? "It downloads the Auren app " + root.appLink.version + " for Android."
                                              : "It opens Auren's latest release: download the auren-phone APK there.")
                        + " Open the download and allow installing from your browser when Android asks. Then pair it here."
                }
                Caption {
                  text: root.appLink.url || ""
                  elide: Text.ElideMiddle
                  wrapMode: Text.NoWrap
                }
              }
            }
            Text {
              width: parent.width
              visible: root.pairNote !== "" && root.pairStage !== "showing" && root.pairStage !== "starting"
              text: root.pairNote
              color: root.pairStage === "paired" ? root.fg : Color.accent
              wrapMode: Text.WordWrap
              font.family: root.font
              font.pixelSize: Style.font.bodySmall
            }

            PanelSectionHeader {
              text: (root.has(1) ? "› " : "") + "PAIRED PHONES · " + root.phones.length
              foreground: root.fg
              fontFamily: root.font
            }
            Caption {
              visible: root.phones.length === 0
              text: "None yet. Get the app on your phone (scan its code with the camera), then pair it here."
            }
            Repeater {
              model: root.phones
              Card {
                id: phoneRow
                required property var modelData
                required property int index
                hot: root.has(1, index)
                implicitHeight: Math.max(phoneText.implicitHeight, unpairButton.implicitHeight) + Style.space(16)
                Column {
                  id: phoneText
                  anchors.left: parent.left
                  anchors.right: unpairButton.left
                  anchors.margins: Style.space(12)
                  anchors.verticalCenter: parent.verticalCenter
                  spacing: Style.space(2)
                  Text {
                    width: parent.width
                    text: phoneRow.modelData.name
                    color: root.fg
                    font.family: root.font
                    font.pixelSize: Style.font.bodySmall
                    font.bold: true
                    elide: Text.ElideRight
                  }
                  Text {
                    width: parent.width
                    text: root.phoneDetail(phoneRow.modelData) + " · paired " + root.ago(phoneRow.modelData.created)
                    color: root.connectedVia(phoneRow.modelData.id) ? root.accent : root.dim
                    font.family: root.font
                    font.pixelSize: Style.font.caption
                    elide: Text.ElideRight
                  }
                }
                Button {
                  id: unpairButton
                  anchors.right: parent.right
                  anchors.rightMargin: Style.space(10)
                  anchors.verticalCenter: parent.verticalCenter
                  text: root.unpairAsk === phoneRow.modelData.id ? "Unpair? Click again" : "Unpair"
                  iconText: "󰅖"
                  iconSize: Style.font.body
                  fontSize: Style.font.bodySmall
                  foreground: root.fg
                  fontFamily: root.font
                  bordered: true
                  selected: root.unpairAsk === phoneRow.modelData.id
                  hasCursor: root.has(1, phoneRow.index)
                  tooltipText: "It stops working at once; it has to pair again to come back"
                  onClicked: root.unpair(phoneRow.modelData.id)
                  onHovered: function(h) { if (h) root.hover(1, phoneRow.index) }
                }
              }
            }
          }

          // ---------------- Updates ----------------
          Column {
            id: updatesPage
            width: parent.width
            spacing: Style.space(12)
            visible: root.page === 6

            SectionTitle { text: "UPDATES" }

            Card {
              implicitHeight: updateText.implicitHeight + Style.space(24)
              Row {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.margins: Style.space(14)
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.space(12)
                Text {
                  id: updateIcon
                  anchors.verticalCenter: parent.verticalCenter
                  text: root.updating || root.checking ? "󰑓" : root.updateAvailable ? "󰚰"
                        : root.updState === "current" || root.updState === "done" ? "󰄬"
                        : root.updState === "failed" || root.updState === "error" ? "󰀦" : "󰚰"
                  color: root.updateAvailable ? Color.accent : root.fg
                  font.family: root.font
                  font.pixelSize: Style.font.title + 4
                  RotationAnimation on rotation {
                    running: root.updating || root.checking
                    from: 0; to: 360; duration: 1100; loops: Animation.Infinite
                    onRunningChanged: if (!running) updateIcon.rotation = 0
                  }
                }
                Column {
                  id: updateText
                  width: parent.width - updateIcon.width - parent.spacing
                  spacing: Style.space(3)
                  Text {
                    width: parent.width
                    text: root.updateHeadline
                    color: root.fg
                    font.family: root.font
                    font.pixelSize: Style.font.subtitle
                    font.bold: true
                    wrapMode: Text.Wrap
                  }
                  Text {
                    width: parent.width
                    visible: text !== ""
                    text: root.updateDetail
                    color: Qt.darker(root.fg, 1.5)
                    font.family: root.font
                    font.pixelSize: Style.font.caption
                    wrapMode: Text.Wrap
                    maximumLineCount: 4
                    elide: Text.ElideRight
                  }
                }
              }
            }

            Column {      // what's new
              width: parent.width
              spacing: Style.space(6)
              visible: (root.updateAvailable || root.updating) && (root.upd.commits || []).length > 0
              PanelSectionHeader { text: "WHAT'S NEW"; foreground: root.fg; fontFamily: root.font }
              Caption { visible: !!root.upd.note; text: root.upd.note || "" }
              Repeater {
                model: (root.upd.commits || []).slice(0, 5)
                Text {
                  required property var modelData
                  width: updatesPage.width
                  text: "•  " + modelData.subject
                  color: root.fg
                  font.family: root.font
                  font.pixelSize: Style.font.bodySmall
                  elide: Text.ElideRight
                }
              }
              Caption {
                visible: (root.upd.count || 0) > 5
                text: "and " + ((root.upd.count || 0) - 5) + " more"
              }
            }

            Text {
              width: parent.width
              visible: root.confirmingUpdate
              wrapMode: Text.WordWrap
              text: root.packaged
                    ? "A terminal opens: Auren waits until it's idle, stops, and yay installs the new version (it asks"
                      + " for your password there). Then Auren starts again, and the bar too if its plugin changed."
                    : "Auren waits until it's idle, stops, installs the new version and starts again (about a minute)."
                      + " If the bar plugin changed, the bar restarts too. A failed install puts the current version back."
              color: root.fg
              font.family: root.font
              font.pixelSize: Style.font.bodySmall
            }
            Choice {
              width: parent.width
              options: root.asOptions(root.updateActions)
              foreground: root.fg; fontFamily: root.font
              cursorIndex: root.has(0) ? root.cursorCol : -1
              onPicked: function(v, i) { root.runAction(root.updateActions, i) }
              onHoveredIndex: function(i) { root.hover(0, i) }
            }
            Toggle {
              width: parent.width
              label: "Tell me about updates"
              description: panelPrefs.notifyUpdates ? "A notification when a new version is out (checked every 6 hours)"
                                                    : "Off: check here when you like"
              checked: panelPrefs.notifyUpdates
              foreground: root.fg; fontFamily: root.font
              hasCursor: root.has(1)
              onClicked: { panelPrefs.notifyUpdates = !panelPrefs.notifyUpdates; panelPrefsFile.writeAdapter() }
              onHovered: function(h) { if (h) root.hover(1) }
            }
            Caption {
              text: root.versionText + (root.installedInfo.committed ? " from " + root.day(root.installedInfo.committed) : "")
                    + ". Updates install published releases only."
            }
          }
        }

        // ================= footer =================
        Column {
          id: footer
          anchors.bottom: parent.bottom
          width: parent.width
          spacing: Style.space(10)
          PanelSeparator { foreground: root.fg }
          Item {
            width: parent.width
            implicitHeight: footerButtons.implicitHeight
            Choice {
              id: footerButtons
              width: parent.width - versionLabel.implicitWidth - Style.space(12)
              options: root.asOptions(root.footerActions)
              foreground: root.fg; fontFamily: root.font
              cursorIndex: root.has(root.footerRow) ? root.cursorCol : -1
              onPicked: function(v, i) { root.runAction(root.footerActions, i) }
              onHoveredIndex: function(i) { root.hover(root.footerRow, i) }
            }
            Text {
              id: versionLabel
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: root.updateAvailable ? "Update available" : root.versionText
              color: root.updateAvailable ? Color.accent : root.dim
              font.family: root.font
              font.pixelSize: Style.font.caption
              font.bold: root.updateAvailable
              MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: root.showPage(6)
              }
            }
          }
        }
      }
    }
  }

  component SectionTitle: Text {
    color: root.fg
    font.family: root.font
    font.pixelSize: Style.font.subtitle
    font.bold: true
  }

  component Caption: Text {
    width: parent ? parent.width : 0
    color: root.dim
    font.family: root.font
    font.pixelSize: Style.font.caption
    wrapMode: Text.Wrap
  }

  component Card: Rectangle {
    property bool hot: false
    width: parent ? parent.width : 0
    radius: Style.cornerRadius
    color: hot ? Style.hoverFillFor(root.fg, Color.accent) : "transparent"
    border.width: 1
    border.color: hot ? root.fg : Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.25)
  }
}

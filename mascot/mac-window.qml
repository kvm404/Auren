// The Auren window on macOS: the bar panel's pages (omarchy-plugin/Panel.qml) in a window of its own, laid
// out like System Settings: the pages in a sidebar on the left, the page on the right. Opened from the menu
// bar icon (Open Auren…, Phone…) and by opening Auren.app once setup is done. Hosted by
// src/auren/mac/mascot.py, next to mac.qml and mac-setup.qml; the pieces it shares with the setup window
// are in mac/.
//
//   Home        the live mascot (click it to talk), what Auren is doing, Setup needs you, Yes/No when it
//               asks, quick switches (Auren, spoken replies, auto-send, the mascot), the last exchange
//   Mascot      Auren or Veyra; speech bubble, sleep animation, voice follows mascot, fade, size, reset
//   Voice       spoken replies, the voice, speed, volume, pause, test; auto-send and its delay
//   Brain       auto / Claude / Codex, Claude's or Codex's effort, how much it asks first, today's numbers
//   Vocabulary  words Auren should listen for
//   Phone       where Auren listens for the phone, pair a phone (its QR code), the paired phones
//   Updates     the installed version, a check, the update (auren update), its log
//   below them  the settings file, the log, restart
//
// `settings` (src/auren/mac/settings_window.py) gives it:
//
//   settings.open, page                   whether it shows, and which page ("home", "mascot", "voice", ...)
//   settings.values                       the daemon's settings: voice, voiceName, tts {speed, volume, pause},
//                                         mode, answering, effort, codexEffort, codexModel, safety, autosend,
//                                         autosendAfter
//   settings.statusLine, serviceOn        what Auren is doing; whether its launch agent runs
//   settings.history                      {last: {transcript, reply} | null, count, avg, codex}
//   settings.vocab, vocabBusy             {mine, defaults, limit, message, ok}
//   settings.upd, checking                update.json; a check is running
//   settings.updateHeadline, updateDetail, updatePlan, versionText, notifyUpdates, codexName
//   settings.setPage(p), close(), focused(), toggleAuren(), restartAuren(), openSetup(), openConfig(), openLog()
//   settings.setPref(name, value), setMascot(kind), setSize(s), setShown(b), resetPlace()
//   settings.vocabRun("list"|"add"|"remove", word), checkUpdates(), startUpdate(), openUpdateLog(),
//   settings.setNotifyUpdates(b)
//   settings.raiseWindow                  (signal) asked for again while it's open: come forward
//
// and it reads `auren` (state, alive, prefs, place, talkKey, setup, setupNeeds, ctl(cmd)) as mac.qml and
// mac-setup.qml do, and `phone` (src/auren/mac/phone_window.py) on the Phone page, as listed there.
import QtQuick
import QtQuick.Window
import "mac"
import "mascots/auren"
import "mascots/veyra"

Window {
  id: w
  title: "Auren"
  width: 820
  height: 640
  minimumWidth: 760
  minimumHeight: 520
  visible: settings.open
  color: Theme.bg
  onClosing: settings.close()
  onActiveChanged: if (active) settings.focused()     // back from System Settings, maybe: look again
  Connections { target: settings; function onRaiseWindow() { if (w.visible) { w.raise(); w.requestActivate() } } }

  // ---- what Auren is doing --------------------------------------------------------------------------
  readonly property var st: auren.state
  readonly property bool alive: auren.alive
  readonly property string mood: alive ? (st.state || "idle") : "off"
  property real now: Date.now() / 1000
  Timer { interval: 1000; repeat: true; running: w.visible; onTriggered: w.now = Date.now() / 1000 }
  readonly property bool awaiting: mood === "idle" && st.awaiting === true && (st.until || 0) > now
  readonly property bool busy: ["listening", "thinking", "working", "speaking"].indexOf(mood) >= 0
  readonly property var v: settings.values
  readonly property var prefs: auren.prefs
  readonly property string key: auren.talkKey
  readonly property bool updating: settings.upd.state === "updating"
  readonly property bool updateAvailable: settings.upd.state === "available"
  readonly property bool setupNeeds: auren.setupNeeds !== ""

  // A changed slider shows its new value at once; the daemon's state confirms it
  property var pendingTts: ({})
  onStChanged: pendingTts = ({})
  function tts(name) { return pendingTts[name] !== undefined ? pendingTts[name] : (v.tts || {})[name] || 0 }
  function setTts(name, value) {
    var p = Object.assign({}, pendingTts); p[name] = value; pendingTts = p
    auren.ctl("tts " + name + " " + value.toFixed(2))
  }

  readonly property var pages: [
    { id: "home", label: "Home", icon: "home", color: "#0A84FF" },
    { id: "mascot", label: "Mascot", icon: "mascot", color: Theme.accent },
    { id: "voice", label: "Voice", icon: "voice", color: "#FF375F" },
    { id: "brain", label: "Brain", icon: "brain", color: "#AF52DE" },
    { id: "vocab", label: "Vocabulary", icon: "vocab", color: "#30B0C7" },
    { id: "phone", label: "Phone", icon: "phone", color: "#34C759" },
    { id: "updates", label: "Updates", icon: "updates", color: "#8E8E93" }
  ]
  readonly property var mascots: [
    { kind: "auren", name: "Auren", tip: "hover-bot", voice: "Trevor" },
    { kind: "veyra", name: "Veyra", tip: "fox spirit", voice: "Juniper" }
  ]
  readonly property string kind: prefs.mascot === "veyra" ? "veyra" : "auren"
  readonly property string mascotName: kind === "veyra" ? "Veyra" : "Auren"
  // Phonon's voices Auren offers, the default first (VOICES in src/auren/voice.py)
  readonly property var voices: [
    { value: "Everly", label: "Everly" }, { value: "Juniper", label: "Juniper" },
    { value: "Hugh", label: "Hugh" }, { value: "Trevor", label: "Trevor" }
  ]
  readonly property var delays: [{ value: "2", label: "2 s" }, { value: "3", label: "3 s" },
                                 { value: "5", label: "5 s" }, { value: "8", label: "8 s" }]
  readonly property var brains: [{ value: "auto", label: "Auto" }, { value: "claude", label: "Claude" },
                                 { value: "codex", label: "Codex" }]
  readonly property var efforts: [{ value: "low", label: "Low" }, { value: "medium", label: "Medium" },
                                  { value: "high", label: "High" }, { value: "xhigh", label: "XHigh" },
                                  { value: "max", label: "Max" }]
  readonly property var codexEfforts: [{ value: "minimal", label: "Minimal" }, { value: "low", label: "Low" },
                                       { value: "medium", label: "Medium" }, { value: "high", label: "High" },
                                       { value: "xhigh", label: "XHigh" }]
  readonly property var safeties: [{ value: "ask", label: "Ask first" }, { value: "task", label: "Once per task" },
                                   { value: "free", label: "Auto" }, { value: "never", label: "Full access" }]
  // Codex answers now (Codex only, or Auto after Claude's limit): the effort row is Codex's then
  readonly property bool codexAnswers: v.mode === "codex" || v.answering === "codex"
  readonly property string brainText: (v.mode === "codex" ? (settings.codexName ? "Codex (" + settings.codexName + ")" : "Codex")
                                       : v.mode === "claude" ? "Claude Haiku only"
                                       : v.answering === "codex" ? "Codex for now (Claude's limit)" : "Claude Haiku")
                                      + ", " + (codexAnswers ? v.codexEffort : v.effort) + " effort"
  readonly property string safetyText: v.safety === "task" ? "Asks the first time; a yes covers the rest of that task."
      : v.safety === "free" ? "Windows and files just run. Sending, publishing, admin rights, installing, secrets, buying and wiping still ask."
      : v.safety === "never" ? "Nothing asks: everything just runs, even sending, buying and wiping."
      : "Asks before every risky action. One yes answers everything waiting."

  function talk() {
    if (!alive) settings.toggleAuren()
    else if (mood === "speaking" || mood === "thinking" || mood === "working") auren.ctl("cancel")
    else auren.ctl("talk")
  }

  // ---- the sidebar ------------------------------------------------------------------------------------
  component SideItem: Rectangle {
    id: si
    property string label
    property string icon
    property color tint: "#8E8E93"
    property bool chosen: false
    property bool badge: false
    signal clicked()
    width: parent ? parent.width : 0
    height: 30
    radius: 6
    color: chosen ? Theme.selected : ma.containsMouse ? Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, 0.05) : "transparent"
    Tile { id: tile; x: 7; anchors.verticalCenter: parent.verticalCenter; icon: si.icon; color: si.tint }
    Text { anchors.left: tile.right; anchors.leftMargin: 9; anchors.right: dot.left; anchors.rightMargin: 6
           anchors.verticalCenter: parent.verticalCenter; text: si.label; color: Theme.text; font.pixelSize: Theme.body
           elide: Text.ElideRight }
    Rectangle { id: dot; visible: si.badge; width: 8; height: 8; radius: 4; color: Theme.warn
                anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter }
    MouseArea { id: ma; anchors.fill: parent; hoverEnabled: true; onClicked: si.clicked() }
  }

  Rectangle {
    id: side
    width: 212
    height: parent.height
    color: Theme.sidebar

    Column {
      x: 10; y: 14
      width: parent.width - 20
      spacing: 2

      Item {                       // who's here: the mascot's face, its name, what it's doing
        width: parent.width
        height: 58
        Item {
          id: face
          x: 4; width: 44; height: 44; anchors.verticalCenter: parent.verticalCenter
          Rectangle { anchors.fill: parent; radius: 22; color: Theme.dark ? "#34343a" : "#ffffff"; border.color: Theme.line }
          Loader {
            anchors.centerIn: parent; anchors.verticalCenterOffset: -2
            active: w.visible
            sourceComponent: w.kind === "veyra" ? veyraFace : aurenFace
          }
          Component { id: aurenFace; Auren { u: 1.6; mood: w.mood === "off" ? "idle" : w.mood; off: w.mood === "off"; sleepAnimation: false } }
          Component { id: veyraFace; Veyra { u: 1.6; mood: w.mood === "off" ? "idle" : w.mood; off: w.mood === "off"; sleepAnimation: false } }
        }
        Column {
          anchors.left: face.right; anchors.leftMargin: 10; anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          spacing: 1
          Text { text: w.mascotName; color: Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold }
          Text { width: parent.width; text: settings.statusLine; color: Theme.dim; font.pixelSize: Theme.small
                 elide: Text.ElideRight }
        }
      }
      Item { width: 1; height: 8 }
      Repeater {
        model: w.pages
        SideItem {
          required property var modelData
          label: modelData.label; icon: modelData.icon; tint: modelData.color
          chosen: settings.page === modelData.id
          badge: (modelData.id === "updates" && w.updateAvailable) || (modelData.id === "home" && w.setupNeeds)
          onClicked: settings.setPage(modelData.id)
        }
      }
    }

    Column {
      x: 10
      anchors.bottom: parent.bottom; anchors.bottomMargin: 12
      width: parent.width - 20
      spacing: 2
      SideItem { label: "Settings file"; icon: "file"; onClicked: settings.openConfig() }
      SideItem { label: "Log"; icon: "log"; onClicked: settings.openLog() }
      SideItem { label: "Restart Auren"; icon: "restart"; enabled: !w.updating; opacity: enabled ? 1 : 0.45
                 onClicked: settings.restartAuren() }
      Text { x: 8; topPadding: 6; text: settings.versionText; color: Theme.faint; font.pixelSize: Theme.small }
    }
    Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.line }
  }

  // ---- the page ---------------------------------------------------------------------------------------
  Item {
    id: content
    anchors.left: side.right; anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom

    Text {
      id: pageTitle
      x: 28; y: 18
      text: (w.pages.find(p => p.id === settings.page) || w.pages[0]).label
      color: Theme.text; font.pixelSize: Theme.title; font.weight: Font.Bold
    }

    Flickable {
      id: scroller
      anchors.top: pageTitle.bottom; anchors.topMargin: 14
      anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
      contentWidth: width
      contentHeight: pagesCol.implicitHeight + 28
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      Connections { target: settings; function onPageChanged() { scroller.contentY = 0 } }

      Column {
        id: pagesCol
        x: 28
        width: scroller.width - 56

        // ---------------- Home ----------------
        Column {
          width: parent.width
          spacing: 10
          visible: settings.page === "home"

          Group {                 // the live mascot: click it to talk (again to send), right-click to poke it
            Item {
              width: parent.width
              height: 112
              Item {
                id: heroBox
                x: 10; width: 96; height: parent.height
                Loader {
                  id: heroLoader
                  anchors.centerIn: parent; anchors.verticalCenterOffset: -4
                  active: w.visible && settings.page === "home"
                  sourceComponent: w.kind === "veyra" ? veyraHero : aurenHero
                }
                Component { id: aurenHero; Auren { u: 3.2; mood: w.mood === "off" ? "idle" : w.mood; off: w.mood === "off"
                                                   awaiting: w.awaiting; hovered: heroMouse.containsMouse; level: auren.level
                                                   sleepAnimation: w.prefs.sleepAnimation !== false } }
                Component { id: veyraHero; Veyra { u: 3.2; mood: w.mood === "off" ? "idle" : w.mood; off: w.mood === "off"
                                                   awaiting: w.awaiting; hovered: heroMouse.containsMouse; level: auren.level
                                                   sleepAnimation: w.prefs.sleepAnimation !== false } }
                MouseArea {
                  id: heroMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  acceptedButtons: Qt.LeftButton | Qt.RightButton
                  onClicked: (mouse) => { if (mouse.button === Qt.RightButton) { if (heroLoader.item) heroLoader.item.poke() } else w.talk() }
                }
              }
              Column {
                anchors.left: heroBox.right; anchors.leftMargin: 8
                anchors.right: talkButton.left; anchors.rightMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                spacing: 3
                Text { text: w.mascotName; color: Theme.text; font.pixelSize: 18; font.weight: Font.Bold }
                Text { width: parent.width; text: settings.statusLine; elide: Text.ElideRight
                       font.pixelSize: Theme.body; font.weight: Font.DemiBold
                       color: w.mood === "off" ? Theme.dim : w.mood === "idle" && !w.awaiting ? Theme.ok : Theme.accent }
                Text { width: parent.width; wrapMode: Text.WordWrap; color: Theme.dim; font.pixelSize: Theme.small + 1
                       text: w.mood === "off" ? "Turn Auren on to talk to it."
                             : w.mood === "listening" ? (w.v.autosend ? "Pause, or press " + w.key + ", to send." : "Press " + w.key + " again to send.")
                             : w.busy ? "Press " + w.key + " to stop."
                             : "Press " + w.key + " and talk, or click " + w.mascotName + "." }
              }
              Pill {
                id: talkButton
                anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter
                primary: true; enabled: !w.updating
                label: !w.alive ? "Turn on" : w.mood === "listening" ? "Send" : w.busy ? "Stop" : "Talk"
                onClicked: w.talk()
              }
            }
          }

          Group {                 // setup isn't finished: one click away
            visible: w.setupNeeds
            SettingRow {
              last: true
              indent: 22
              lead: Mark { anchors.verticalCenter: parent.verticalCenter; state: auren.setup.turningText ? "busy" : "todo" }
              title: auren.setup.turningText ? "Almost there" : "Setup needs you"
              detail: auren.setupNeeds
              Pill { visible: !auren.setup.turningText; primary: true; label: "Open Setup"; onClicked: settings.openSetup() }
            }
          }

          Group {                 // a question is waiting: one click answers all of it
            visible: w.awaiting
            SettingRow {
              last: true
              title: "Auren is asking"
              detail: w.st.question || ""
              detailColor: Theme.text
              Row {
                spacing: 8
                Pill { label: "Yes"; primary: true; onClicked: auren.ctl("yes") }
                Pill { label: "No"; onClicked: auren.ctl("no") }
              }
            }
          }

          Heading { text: "Quick settings" }
          Group {
            SettingRow {
              title: "Auren"
              detail: w.alive ? "On: listening for " + w.key : settings.serviceOn ? "Starting…" : "Off until you turn it on, or the next login"
              Toggle { checked: settings.serviceOn; enabled: !w.updating; onToggled: settings.toggleAuren() }
            }
            SettingRow {
              title: "Spoken replies"
              detail: !w.alive ? "Auren is off" : w.v.voice ? "Auren speaks its answers" : "Answers show only in the bubble"
              Toggle { checked: w.v.voice; enabled: w.alive; onToggled: auren.ctl("voice " + (w.v.voice ? "off" : "on")) }
            }
            SettingRow {
              title: "Send when I stop talking"
              detail: w.v.autosend ? "After " + w.v.autosendAfter + " s without new words" : "Off: press " + w.key + " again to send"
              Toggle { checked: w.v.autosend; enabled: w.alive; onToggled: auren.ctl("autosend " + (w.v.autosend ? "off" : "on")) }
            }
            SettingRow {
              title: "Show the mascot"
              detail: auren.place.hidden === true ? "Hidden: Auren still listens for " + w.key : "In a corner of the screen, on every Space"
              Toggle { checked: auren.place.hidden !== true; onToggled: settings.setShown(auren.place.hidden === true) }
            }
            SettingRow {
              last: true
              title: "Conversation"
              detail: "Start over, or stop what Auren is doing"
              Row {
                spacing: 8
                Pill { label: "Fresh start"; enabled: w.alive; onClicked: auren.ctl("forget") }
                Pill { label: "Stop"; enabled: w.alive && w.busy; onClicked: auren.ctl("cancel") }
              }
            }
          }

          Heading { text: "Last exchange"; visible: settings.history.last !== null }
          Group {
            visible: settings.history.last !== null
            SettingRow {
              last: true
              title: settings.history.last ? "“" + settings.history.last.transcript + "”" : ""
              detail: settings.history.last ? settings.history.last.reply : ""
            }
          }
        }

        // ---------------- Mascot ----------------
        Column {
          width: parent.width
          spacing: 10
          visible: settings.page === "mascot"

          Row {                   // the mascots, live: click one to pick it
            id: cards
            width: parent.width
            spacing: 10
            Repeater {
              model: w.mascots
              Rectangle {
                id: card
                required property var modelData
                readonly property bool chosen: w.kind === modelData.kind
                width: (cards.width - cards.spacing) / 2
                height: 156
                radius: 10
                color: Theme.card
                border.width: chosen ? 2 : 1
                border.color: chosen ? Theme.accent : cardMouse.containsMouse ? Theme.faint : Theme.line
                Item {
                  id: stage
                  width: parent.width; height: 112
                  Loader {
                    id: cardLoader
                    anchors.centerIn: parent; anchors.verticalCenterOffset: 4
                    active: w.visible && settings.page === "mascot"
                    sourceComponent: card.modelData.kind === "veyra" ? veyraCard : aurenCard
                  }
                  Component { id: aurenCard; Auren { u: 3.6; sleepAnimation: false; hovered: cardMouse.containsMouse } }
                  Component { id: veyraCard; Veyra { u: 3.6; sleepAnimation: false; hovered: cardMouse.containsMouse } }
                }
                Column {
                  anchors.top: stage.bottom; anchors.horizontalCenter: parent.horizontalCenter
                  spacing: 1
                  Text { anchors.horizontalCenter: parent.horizontalCenter; text: card.modelData.name; color: Theme.text
                         font.pixelSize: Theme.body; font.weight: card.chosen ? Font.DemiBold : Font.Normal }
                  Text { anchors.horizontalCenter: parent.horizontalCenter; color: Theme.dim; font.pixelSize: Theme.small
                         text: card.modelData.tip + " · speaks with " + card.modelData.voice }
                }
                MouseArea {
                  id: cardMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: { if (cardLoader.item) cardLoader.item.poke(); settings.setMascot(card.modelData.kind) }
                }
              }
            }
          }

          Group {
            SettingRow {
              title: "Speech bubble"
              detail: w.prefs.bubble !== false ? "What Auren hears, does and says, next to it" : "Off: only the mascot"
              Toggle { checked: w.prefs.bubble !== false; onToggled: settings.setPref("bubble", !checked) }
            }
            SettingRow {
              title: "Sleep animation"
              detail: w.prefs.sleepAnimation !== false ? "Breathes while asleep (uses a little power)" : "Still while asleep"
              Toggle { checked: w.prefs.sleepAnimation !== false; onToggled: settings.setPref("sleepAnimation", !checked) }
            }
            SettingRow {
              last: true
              title: "Voice follows mascot"
              detail: "Picking Auren picks Trevor's voice, Veyra Juniper's"
              Toggle { checked: w.prefs.matchVoice !== false; onToggled: settings.setPref("matchVoice", !checked) }
            }
          }

          Group {
            SettingRow {
              title: "Show the mascot"
              detail: "Drag it anywhere; scroll on it to resize it"
              Toggle { checked: auren.place.hidden !== true; onToggled: settings.setShown(auren.place.hidden === true) }
            }
            SettingRow {
              title: "Faded when idle"
              detail: "How see-through it gets when it has nothing to do"
              Row {
                spacing: 10
                Slider { id: fade; width: 160; anchors.verticalCenter: parent.verticalCenter; from: 0.15; to: 1; step: 0.05
                         value: w.prefs.idleOpacity || 0.45; onReleased: (x) => settings.setPref("idleOpacity", Math.round(x * 100) / 100) }
                Text { width: 40; anchors.verticalCenter: parent.verticalCenter; horizontalAlignment: Text.AlignRight
                       text: Math.round(fade.live * 100) + "%"; color: Theme.dim; font.pixelSize: Theme.body }
              }
            }
            SettingRow {
              title: "Size"
              Row {
                spacing: 10
                Slider { id: size; width: 160; anchors.verticalCenter: parent.verticalCenter; from: 0.6; to: 2.2; step: 0.1
                         value: auren.place.size || 1; enabled: auren.place.hidden !== true
                         onReleased: (x) => settings.setSize(x) }
                Text { width: 40; anchors.verticalCenter: parent.verticalCenter; horizontalAlignment: Text.AlignRight
                       text: size.live.toFixed(1) + "×"; color: Theme.dim; font.pixelSize: Theme.body }
              }
            }
            SettingRow {
              last: true
              title: "Place"
              detail: "Back to the bottom-right corner, at the normal size"
              Pill { label: "Reset"; onClicked: settings.resetPlace() }
            }
          }
        }

        // ---------------- Voice ----------------
        Column {
          width: parent.width
          spacing: 10
          visible: settings.page === "voice"

          Caption { visible: !w.alive; text: "Auren is off: turn it on (Home) to change how it speaks." }
          Group {
            enabled: w.alive
            SettingRow {
              title: "Spoken replies"
              detail: w.v.voice ? "Auren speaks its answers" : "Answers show only in the bubble"
              Toggle { checked: w.v.voice; onToggled: auren.ctl("voice " + (w.v.voice ? "off" : "on")) }
            }
            SettingRow {
              title: "Voice"
              Segmented { options: w.voices; value: w.v.voiceName; onPicked: (name) => auren.ctl("voice-name " + name) }
            }
            SettingRow {
              title: "Speed"
              Row {
                spacing: 10
                Slider { id: speed; width: 160; anchors.verticalCenter: parent.verticalCenter; from: 0.8; to: 1.25; step: 0.05
                         value: w.tts("speed"); onReleased: (x) => w.setTts("speed", x) }
                Text { width: 44; anchors.verticalCenter: parent.verticalCenter; horizontalAlignment: Text.AlignRight
                       text: speed.live.toFixed(2) + "×"; color: Theme.dim; font.pixelSize: Theme.body }
              }
            }
            SettingRow {
              title: "Volume"
              Row {
                spacing: 10
                Slider { id: volume; width: 160; anchors.verticalCenter: parent.verticalCenter; from: 0.3; to: 1; step: 0.05
                         value: w.tts("volume"); onReleased: (x) => w.setTts("volume", x) }
                Text { width: 44; anchors.verticalCenter: parent.verticalCenter; horizontalAlignment: Text.AlignRight
                       text: Math.round(volume.live * 100) + "%"; color: Theme.dim; font.pixelSize: Theme.body }
              }
            }
            SettingRow {
              title: "Pause between sentences"
              Row {
                spacing: 10
                Slider { id: pause; width: 160; anchors.verticalCenter: parent.verticalCenter; from: 0; to: 0.6; step: 0.05
                         value: w.tts("pause"); onReleased: (x) => w.setTts("pause", x) }
                Text { width: 44; anchors.verticalCenter: parent.verticalCenter; horizontalAlignment: Text.AlignRight
                       text: pause.live.toFixed(2) + " s"; color: Theme.dim; font.pixelSize: Theme.body }
              }
            }
            SettingRow {
              last: true
              title: "Try it"
              detail: "Hear how Auren sounds now, or go back to the usual speed and volume"
              Row {
                spacing: 8
                Pill { label: "Test"; onClicked: auren.ctl("say Hi, this is how I sound now. Does this pace work for you?") }
                Pill { label: "Defaults"; onClicked: auren.ctl("tts reset") }
              }
            }
          }

          Heading { text: "Listening" }
          Group {
            enabled: w.alive
            SettingRow {
              last: !w.v.autosend
              title: "Send when I stop talking"
              detail: w.v.autosend ? "Sends after " + w.v.autosendAfter + " s without new words"
                                   : "Off: after 3 s of quiet Auren reminds you to press " + w.key + "; it sends after 12 s"
              Toggle { checked: w.v.autosend; onToggled: auren.ctl("autosend " + (w.v.autosend ? "off" : "on")) }
            }
            SettingRow {
              visible: w.v.autosend
              last: true
              title: "Send after"
              detail: "Longer leaves room to think mid-sentence"
              Segmented { options: w.delays; value: String(w.v.autosendAfter); onPicked: (s) => auren.ctl("autosend after " + s) }
            }
          }
        }

        // ---------------- Brain ----------------
        Column {
          width: parent.width
          spacing: 10
          visible: settings.page === "brain"

          Caption { visible: !w.alive; text: "Auren is off: turn it on (Home) to change its brain." }
          Group {
            enabled: w.alive
            SettingRow {
              title: "Who answers"
              detail: "Now: " + w.brainText
              Segmented { options: w.brains; value: w.v.mode; onPicked: (m) => auren.ctl("brain " + m) }
            }
            SettingRow {
              last: true
              title: w.codexAnswers ? "Codex's effort" : "Claude's effort"
              detail: w.v.mode === "codex" ? "Codex only: Claude isn't used. Higher thinks harder but answers slower."
                      : w.codexAnswers ? "Codex answers until Claude's limit resets. Higher thinks harder but answers slower."
                      : "Higher thinks harder but answers slower. Changing it starts a fresh conversation."
              below: Item {
                width: parent.width; height: 36
                Segmented { y: 8; width: parent.width; stretch: true
                            options: w.codexAnswers ? w.codexEfforts : w.efforts
                            value: w.codexAnswers ? w.v.codexEffort : w.v.effort
                            onPicked: (e) => auren.ctl((w.codexAnswers ? "codex-effort " : "effort ") + e) }
              }
            }
          }

          Heading { text: "Risky actions" }
          Group {
            enabled: w.alive
            SettingRow {
              last: true
              title: "How much Auren asks first"
              detail: w.safetyText
              below: Item {
                width: parent.width; height: 36
                Segmented { y: 8; width: parent.width; stretch: true; options: w.safeties; value: w.v.safety
                            onPicked: (s) => auren.ctl("safety " + s) }
              }
            }
          }

          Heading { text: "Today" }
          Group {
            SettingRow {
              last: true
              readonly property var h: settings.history
              title: h.count === 0 ? "No requests yet" : h.count + (h.count === 1 ? " request" : " requests")
                     + " · " + h.avg.toFixed(1) + " s on average"
              detail: h.codex ? h.codex + " answered by Codex" : ""
            }
          }
        }

        // ---------------- Vocabulary ----------------
        Column {
          id: vocabPage
          width: parent.width
          spacing: 10
          visible: settings.page === "vocab"

          Caption { text: "Names and words Auren should listen for: projects, tools, people. A new word is used the"
                          + " next time you talk to Auren." }
          Group {
            SettingRow {
              last: true
              detail: settings.vocab.message || ""
              detailColor: settings.vocab.ok ? Theme.dim : Theme.warn
              title: ""
              below: Item {
                width: parent.width; height: 28
                Field { id: word; width: parent.width - add.width - 8; placeholder: "Add a word or name"
                        onAccepted: settings.vocabRun("add", text) }
                Pill { id: add; anchors.right: parent.right; primary: true; label: "Add"; enabled: !settings.vocabBusy
                       onClicked: settings.vocabRun("add", word.text) }
              }
            }
          }
          Connections {           // a word that went in leaves the field
            target: settings
            function onChanged() { if (settings.vocab.ok && settings.vocab.mine.indexOf(word.text.trim()) >= 0) word.text = "" }
          }

          Heading { text: "Your words · " + settings.vocab.mine.length + (settings.vocab.limit ? " of " + settings.vocab.limit : "") }
          Caption { visible: settings.vocab.mine.length === 0; text: "None yet. Add the names Auren keeps mishearing." }
          Flow {
            width: parent.width
            spacing: 6
            Repeater {
              model: settings.vocab.mine
              Rectangle {
                id: chip
                required property string modelData
                height: 26; radius: 13
                width: chipText.implicitWidth + 40
                color: chipMouse.containsMouse ? Theme.pressed : Theme.card
                border.color: Theme.line
                Text { id: chipText; x: 12; anchors.verticalCenter: parent.verticalCenter; text: chip.modelData
                       color: Theme.text; font.pixelSize: Theme.body }
                Text { anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter
                       text: "✕"; color: chipMouse.containsMouse ? Theme.text : Theme.dim; font.pixelSize: 10 }
                MouseArea { id: chipMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                            onClicked: settings.vocabRun("remove", chip.modelData) }
              }
            }
          }
          Heading { text: "Built in · " + settings.vocab.defaults.length }
          Caption { text: settings.vocab.defaults.join(", ") }
        }

        // ---------------- Phone ----------------
        Column {
          width: parent.width
          spacing: 10
          visible: settings.page === "phone"

          id: phonePage
          readonly property bool pairing: phone.pairStage === "starting" || phone.pairStage === "showing"
          // The countdown: seconds the code still works
          property real now: Date.now() / 1000
          Timer { interval: 1000; repeat: true; running: phone.pairStage === "showing"; onTriggered: phonePage.now = Date.now() / 1000 }
          readonly property int secsLeft: Math.max(0, Math.round(phone.pairExpires - now))

          Caption { text: "Talk to Auren from the Auren app on your Android phone: on this Wi-Fi, or anywhere over Tailscale." }
          Group {
            SettingRow {
              indent: 22
              lead: Rectangle { anchors.verticalCenter: parent.verticalCenter; x: 6; width: 10; height: 10; radius: 5
                                color: phone.status.indexOf("Listening") === 0 ? Theme.ok : Theme.faint }
              title: phone.status
              detail: phone.warn
              detailColor: Theme.warn
            }
            SettingRow {
              title: phonePage.pairing ? "Pairing" : phone.phones.length ? "Pair another phone" : "Pair a phone"
              detail: phonePage.pairing ? "The code below works once. Cancel ends it at once."
                      : "A one-time QR code for the Auren app; it works for 5 minutes"
              Pill { visible: !phonePage.pairing; primary: true; enabled: phone.alive && !phone.stopping
                     label: phone.stopping ? "Stopping…" : "Pair"; onClicked: phone.startPairing() }
              Pill { visible: phonePage.pairing; label: "Cancel"; onClicked: phone.cancelPairing() }
            }
            SettingRow {
              last: true
              title: "Get the app"
              detail: "A QR code for your phone's camera: it downloads the Auren app"
              Pill { label: phone.appShown ? "Hide" : "Show code"; enabled: !phonePage.pairing; onClicked: phone.toggleApp() }
            }
          }

          Caption { visible: phone.pairStage === "starting"; text: "Opening a pairing…" }

          Group {                 // the pairing code, while it works
            visible: phone.pairStage === "showing" && phone.pairQr !== ""
            Row {
              x: 14; topPadding: 14; bottomPadding: 14
              width: parent.width - 28
              spacing: 16
              QrCode { id: pairCode; source: phone.pairQr }
              Column {
                width: parent.width - pairCode.width - parent.spacing
                anchors.verticalCenter: parent.verticalCenter
                spacing: 6
                Text { width: parent.width; text: "Scan it in the Auren app (Pair)."; color: Theme.text; font.pixelSize: Theme.body
                       font.weight: Font.DemiBold; wrapMode: Text.WordWrap }
                Caption { leftPadding: 0; text: "It works once, for " + Math.floor(phonePage.secsLeft / 60) + ":"
                          + ("0" + phonePage.secsLeft % 60).slice(-2) + " more, from a phone on " + phone.pairWhere + "." }
                Caption { leftPadding: 0; visible: phone.pairWarn !== ""; text: phone.pairWarn; color: Theme.warn }
              }
            }
          }

          Group {                 // Get the app: the download's QR code, for the phone's camera
            visible: phone.appShown
            Row {
              x: 14; topPadding: 14; bottomPadding: 14
              width: parent.width - 28
              spacing: 16
              QrCode { id: appCode; source: phone.appQr }
              Column {
                width: parent.width - appCode.width - parent.spacing
                anchors.verticalCenter: parent.verticalCenter
                spacing: 6
                Text { width: parent.width; text: "Scan it with your phone's camera."; color: Theme.text; font.pixelSize: Theme.body
                       font.weight: Font.DemiBold; wrapMode: Text.WordWrap }
                Caption { leftPadding: 0; text: phone.appText }
                Caption { leftPadding: 0; text: phone.appUrl; elide: Text.ElideMiddle; wrapMode: Text.NoWrap }
              }
            }
          }

          Caption {
            visible: phone.pairNote !== "" && !phonePage.pairing
            text: phone.pairNote
            color: phone.pairStage === "paired" ? Theme.ok : Theme.warn
            font.pixelSize: Theme.body
          }

          Heading { text: "Paired phones · " + phone.phones.length }
          Caption { visible: phone.phones.length === 0
                    text: "None yet. Get the app on your phone (scan its code with the camera), then pair it here." }
          Group {
            visible: phone.phones.length > 0
            Repeater {
              model: phone.phones
              SettingRow {
                required property var modelData
                required property int index
                last: index === phone.phones.length - 1
                title: modelData.name
                detail: modelData.detail
                detailColor: modelData.connected ? Theme.accent : Theme.dim
                Pill { label: phone.unpairAsk === modelData.id ? "Unpair? Click again" : "Unpair"
                       primary: phone.unpairAsk === modelData.id; onClicked: phone.unpair(modelData.id) }
              }
            }
          }
          Caption { text: "Unpairing stops a phone at once: it has to pair again to come back." }
        }

        // ---------------- Updates ----------------
        Column {
          id: updatesPage
          width: parent.width
          spacing: 10
          visible: settings.page === "updates"
          property bool confirming: false
          Connections { target: settings; function onPageChanged() { updatesPage.confirming = false } }

          Group {
            SettingRow {
              last: true
              indent: 40
              implicitHeight: 76
              big: true
              lead: Tile {
                id: updTile
                anchors.verticalCenter: parent.verticalCenter
                size: 40; icon: "updates"
                color: w.updateAvailable ? Theme.accent : settings.upd.state === "failed" || settings.upd.state === "error"
                       ? Theme.warn : "#8E8E93"
                RotationAnimation on rotation { running: w.updating || settings.checking; from: 0; to: 360; duration: 1100
                                                loops: Animation.Infinite; onRunningChanged: if (!running) updTile.rotation = 0 }
              }
              title: settings.updateHeadline
              detail: settings.updateDetail
            }
          }

          Heading { text: "What's new"; visible: whatsNew.visible }
          Group {
            id: whatsNew
            visible: (w.updateAvailable || w.updating) && (settings.upd.commits || []).length > 0
            Column {
              x: 14; topPadding: 10; bottomPadding: 10
              width: parent.width - 28
              spacing: 6
              Repeater {
                model: (settings.upd.commits || []).slice(0, 5)
                Text { required property var modelData; width: parent.width; text: "•  " + modelData.subject
                       color: Theme.text; font.pixelSize: Theme.body; elide: Text.ElideRight }
              }
            }
          }
          Caption { visible: whatsNew.visible && (settings.upd.count || 0) > 5; text: "and " + ((settings.upd.count || 0) - 5) + " more" }

          Caption { visible: updatesPage.confirming; text: settings.updatePlan; color: Theme.text }
          Row {
            spacing: 8
            Pill { visible: updatesPage.confirming; primary: true; label: "Update and restart"
                   onClicked: { updatesPage.confirming = false; settings.startUpdate() } }
            Pill { visible: updatesPage.confirming; label: "Cancel"; onClicked: updatesPage.confirming = false }
            Pill { visible: !updatesPage.confirming && w.updateAvailable; primary: true; label: "Update now…"
                   onClicked: updatesPage.confirming = true }
            Pill { visible: !updatesPage.confirming; label: settings.checking ? "Checking…" : "Check for updates"
                   enabled: !settings.checking && !w.updating; onClicked: settings.checkUpdates() }
            Pill { visible: !updatesPage.confirming && (w.updating || settings.upd.state === "failed" || settings.upd.state === "done")
                   label: "Update log"; onClicked: settings.openUpdateLog() }
          }

          Group {
            SettingRow {
              last: true
              title: "Tell me about updates"
              detail: settings.notifyUpdates ? "A notification when a new version is out (Auren looks every 6 hours)"
                                             : "Off: check here when you like"
              Toggle { checked: settings.notifyUpdates; onToggled: settings.setNotifyUpdates(!checked) }
            }
          }
          Caption { text: settings.versionText + ". Updates install published releases only." }
        }
      }
    }
  }
}

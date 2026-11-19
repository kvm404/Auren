// Auren's setup window on macOS: the steps before Auren can work, each with its state and one button.
// Hosted by src/auren/mac/mascot.py, next to mac.qml. `auren` gives it:
//
//   auren.setupOpen          whether the window shows          auren.setup   the steps (mac/setup.py check())
//   auren.brainText          the brain row's line              auren.brainAction  "", "copy", "claude", "codex"
//   auren.talkKey            ⌃⌥A
//   auren.allow(name)        ask for a permission (microphone, accessibility, screen)
//   auren.saveKey(text)      returns "" or why not             auren.useBrain(mode), auren.copyInstall()
//   auren.restart(), auren.startDaemon(), auren.openUrl(url), auren.closeSetup()
//   auren.raiseSetup               (signal) the window is asked for again while it's open: come forward
import QtQuick
import QtQuick.Window
import "mascots/auren"

Window {
  id: w
  title: "Set up Auren"
  width: 540
  readonly property real fit: col.implicitHeight + 48
  height: fit
  minimumWidth: 540; maximumWidth: 540
  minimumHeight: fit; maximumHeight: fit
  visible: auren.setupOpen
  onClosing: auren.closeSetup()
  Connections { target: auren; function onRaiseSetup() { if (w.visible) { w.raise(); w.requestActivate() } } }
  color: pal.bg

  readonly property var s: auren.setup
  readonly property bool dark: Application.styleHints.colorScheme === Qt.ColorScheme.Dark
  QtObject {
    id: pal
    readonly property color bg: w.dark ? "#1d1d1f" : "#f5f5f7"
    readonly property color card: w.dark ? "#2a2a2d" : "#ffffff"
    readonly property color line: w.dark ? "#3a3a3d" : "#e5e5ea"
    readonly property color text: w.dark ? "#f5f5f7" : "#1d1d1f"
    readonly property color dim: w.dark ? "#98989d" : "#6e6e73"
    readonly property color field: w.dark ? "#1d1d1f" : "#f5f5f7"
    readonly property color accent: "#D77757"
    readonly property color ok: "#34C759"
    readonly property color warn: "#FF9F0A"
  }

  // ---- pieces -----------------------------------------------------------------------------------
  component Pill: Rectangle {
    id: pill
    property string label
    property bool primary: false
    signal clicked()
    implicitWidth: t.implicitWidth + 28
    implicitHeight: 28
    radius: 14
    color: primary ? (ma.pressed ? Qt.darker(pal.accent, 1.15) : pal.accent)
                   : (ma.pressed ? pal.line : (w.dark ? "#3a3a3d" : "#e8e8ed"))
    Text { id: t; anchors.centerIn: parent; text: pill.label; font.pixelSize: 13; font.weight: Font.Medium
           color: pill.primary ? "white" : pal.text }
    MouseArea { id: ma; anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: pill.clicked() }
  }

  component Mark: Rectangle {      // the step's state: done, needs you, optional
    property string state: "todo"   // done, todo, optional
    width: 22; height: 22; radius: 11
    color: state === "done" ? pal.ok : "transparent"
    border.width: state === "done" ? 0 : 1.5
    border.color: state === "todo" ? pal.warn : pal.dim
    Text { anchors.centerIn: parent; text: parent.state === "done" ? "✓" : parent.state === "todo" ? "!" : "–"
           color: parent.state === "done" ? "white" : parent.state === "todo" ? pal.warn : pal.dim
           font.pixelSize: 12; font.weight: Font.Bold }
  }

  component Step: Item {
    id: step
    property string title
    property string detail
    property string mark: "todo"
    property bool last: false
    default property alias action: slot.data
    property alias below: under.data            // more, under the text (the key's field)
    width: parent ? parent.width : 0
    implicitHeight: Math.max(64, body.implicitHeight + 28)
    Mark { id: m; state: step.mark; x: 16; anchors.verticalCenter: parent.verticalCenter }
    Column {
      id: body
      x: 52; anchors.verticalCenter: parent.verticalCenter
      width: parent.width - x - slot.width - 32
      spacing: 3
      Text { text: step.title; color: pal.text; font.pixelSize: 14; font.weight: Font.DemiBold }
      Text { text: step.detail; color: pal.dim; font.pixelSize: 12; wrapMode: Text.WordWrap; width: parent.width
             visible: text !== "" }
      Column { id: under; width: parent.width }
    }
    Item {
      id: slot
      anchors.right: parent.right; anchors.rightMargin: 16; anchors.verticalCenter: parent.verticalCenter
      width: childrenRect.width; height: childrenRect.height
    }
    Rectangle { visible: !step.last; x: 52; anchors.bottom: parent.bottom; width: parent.width - x; height: 1; color: pal.line }
  }

  // ---- the window -------------------------------------------------------------------------------
  Rectangle { anchors.fill: parent; color: pal.bg }
  property bool screenAsked: false        // Screen Recording takes effect after a restart: offer one then

  Column {
    id: col
    x: 24; y: 24
    width: w.width - 48
    spacing: 18

    Row {
      spacing: 14
      Item {
        width: 64; height: 56
        Auren { u: 2.7; mood: w.s.ready ? "speaking" : "idle"; sleepAnimation: false
                x: (parent.width - implicitWidth) / 2; y: (parent.height - implicitHeight) / 2 - 4 }
      }
      Column {
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2
        Text { text: w.s.ready && !w.s.restart ? "Auren is ready" : "Set up Auren"; color: pal.text; font.pixelSize: 22; font.weight: Font.Bold }
        Text { text: w.s.ready && !w.s.restart ? "Press " + auren.talkKey + ", talk, and press it again to send."
                               : "A few things first. Each one takes a click."
               color: pal.dim; font.pixelSize: 13 }
      }
    }

    // Auren isn't running: nothing below can be checked
    Rectangle {
      visible: !w.s.running
      width: parent.width; height: 52; radius: 12
      color: pal.card; border.color: pal.line
      Text { x: 16; anchors.verticalCenter: parent.verticalCenter; text: "Auren isn't running."
             color: pal.text; font.pixelSize: 13 }
      Pill { label: "Start Auren"; primary: true; anchors.right: parent.right; anchors.rightMargin: 12
             anchors.verticalCenter: parent.verticalCenter; onClicked: auren.startDaemon() }
    }

    Rectangle {
      width: parent.width
      height: steps.implicitHeight
      radius: 12
      color: pal.card
      border.color: pal.line
      opacity: w.s.running ? 1 : 0.5

      Column {
        id: steps
        width: parent.width

        Step {
          title: "Microphone"
          mark: w.s.microphone === "granted" ? "done" : "todo"
          detail: w.s.microphone === "granted" ? "Auren can hear you." : "So Auren hears you after you press its key."
          Pill { visible: w.s.microphone !== "granted"; primary: true
                 label: w.s.microphone === "not asked" ? "Allow" : "Open Settings"
                 onClicked: auren.allow("microphone") }
        }
        Step {
          title: "Accessibility"
          mark: w.s.accessibility ? "done" : "todo"
          detail: w.s.accessibility ? "The talk key works, and Auren can click and type for you."
                                    : "For the talk key, and to click and type for you. Turn Auren on in the list."
          Pill { visible: !w.s.accessibility; primary: true; label: "Allow"; onClicked: auren.allow("accessibility") }
        }
        Step {
          title: "Screen Recording"
          mark: w.s.screen ? "done" : "optional"
          detail: w.s.screen ? "Auren can see other apps when it needs to."
                             : "Optional: lets Auren look at other apps' windows. Restart Auren after turning it on."
          Pill { visible: !w.s.screen; label: "Allow"; onClicked: { w.screenAsked = true; auren.allow("screen") } }
        }
        Step {
          id: keyStep
          title: "Speech to text"
          mark: w.s.key ? "done" : "todo"
          property bool editing: !w.s.key
          property string error: ""
          detail: error !== "" ? error : w.s.key && !editing ? "Your AssemblyAI key is saved."
                                       : "Auren uses AssemblyAI to turn your voice into text."
          Row {
            Pill { visible: !keyStep.editing; label: "Change"; onClicked: keyStep.editing = true }
            Pill { visible: keyStep.editing && !w.s.key; label: "Get a key"
                   onClicked: auren.openUrl("https://www.assemblyai.com/dashboard/signup") }
          }
          below: Row {
            visible: keyStep.editing
            topPadding: 6
            spacing: 8
            Rectangle {
              width: 240; height: 28; radius: 7
              color: pal.field; border.color: input.activeFocus ? pal.accent : pal.line
              TextInput {
                id: input
                anchors.fill: parent; anchors.leftMargin: 9; anchors.rightMargin: 9
                verticalAlignment: TextInput.AlignVCenter
                echoMode: TextInput.Password; color: pal.text; font.pixelSize: 13; clip: true
                selectByMouse: true
                onAccepted: save.clicked()
                Text { anchors.verticalCenter: parent.verticalCenter; text: "Paste your API key"; color: pal.dim
                       font.pixelSize: 13; visible: input.text === "" && !input.activeFocus }
              }
            }
            Pill { id: save; primary: true; label: "Save"
                   onClicked: { keyStep.error = auren.saveKey(input.text); if (keyStep.error === "") { input.text = ""; keyStep.editing = false } } }
          }
        }
        Step {
          title: "Brain"
          last: true
          mark: w.s.brainReady ? "done" : "todo"
          detail: auren.brainText
          Row {
            spacing: 6
            Pill { visible: auren.brainAction === "copy"; primary: true; label: copied.running ? "Copied" : "Copy install command"
                   onClicked: { auren.copyInstall(); copied.restart() } }
            Pill { visible: auren.brainAction === "claude" || auren.brainAction === "codex"; primary: true
                   label: auren.brainAction === "claude" ? "Use Claude Code" : "Use Codex"
                   onClicked: auren.useBrain(auren.brainAction) }
            Repeater {      // both installed: pick one
              model: auren.brainAction === "" && w.s.claude && w.s.codex ? [["claude", "Claude"], ["codex", "Codex"]] : []
              Pill { required property var modelData; label: modelData[1]
                     primary: w.s.brain === modelData[0] || (w.s.brain === "auto" && modelData[0] === "claude")
                     onClicked: auren.useBrain(modelData[0]) }
            }
            Timer { id: copied; interval: 2000 }
          }
        }
      }
    }

    // ---- what's next ----------------------------------------------------------------------------
    Item {
      width: parent.width
      height: 32
      Text {
        anchors.left: parent.left; anchors.right: buttons.left; anchors.rightMargin: 12
        anchors.verticalCenter: parent.verticalCenter
        wrapMode: Text.WordWrap; font.pixelSize: 12; color: pal.dim
        text: w.s.restart || (w.screenAsked && !w.s.screen) ? "Restart Auren to turn on what you allowed."
            : w.s.ready ? "Try: press " + auren.talkKey + " and say “open Safari”."
            : "Auren lives in the menu bar. Setup is there too."
      }
      Row {
        id: buttons
        anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
        spacing: 8
        Pill { visible: w.s.restart || (w.screenAsked && !w.s.screen); label: "Restart Auren"; primary: w.s.restart
               onClicked: auren.restart() }
        Pill { label: w.s.ready ? "Done" : "Later"; primary: w.s.ready && !w.s.restart; onClicked: auren.closeSetup() }
      }
    }
  }
}

import QtQuick
import Quickshell
import "plugin" as Plugin

// The bar panel off-screen, for tests/panel/preview.sh: opens each page listed in $SHOTS (page[:state]),
// fakes the update state it names, and saves a picture of it to $OUT/<page>[-<state>].png.
ShellRoot {
  FloatingWindow {
    id: win
    implicitWidth: 700
    implicitHeight: 860
    color: "#05070a"
    QtObject {
      id: fakeBar
      property color foreground: "#e6e6e6"
      property color barForeground: "#e6e6e6"
      property color background: "#0b0d12"
      property string fontFamily: "JetBrainsMono Nerd Font"
      property string position: "top"
    }
    Rectangle {
      id: stage
      anchors.fill: parent
      color: "#05070a"
      Plugin.Panel { id: p; bar: fakeBar; x: 660; y: 820 }
    }

    property var shots: Quickshell.env("SHOTS").split(",")
    property int i: 0
    Timer { id: tick; interval: 1500; running: true; onTriggered: win.next() }
    function next() {
      if (i >= shots.length) { Qt.quit(); return }
      var name = shots[i]
      var parts = name.split(":")
      p.open()
      p.showPage(Number(parts[0]))
      p.confirmingUpdate = false
      p.cursorActive = false
      shotTimer.fake = parts[1] || ""
      i++
      shotTimer.name = name.replace(":", "-")
      shotTimer.restart()
    }
    readonly property var installed: ({ version: "0.1.0", short: "54ebe81" })
    readonly property var commits: [
      { subject: "Redesign the bar panel" }, { subject: "Check for updates from the panel" },
      { subject: "Fix the bubble after a stop" }, { subject: "Faster first reply" },
      { subject: "Voice tuning tweaks" }, { subject: "Docs" }, { subject: "Tests" }
    ]
    function fake(kind) {
      var now = Date.now() / 1000
      if (kind === "available") p.upd = { state: "available", count: 7, channel: "main", checked_at: now - 300, installed: installed, commits: commits }
      if (kind === "confirm") { p.upd = { state: "available", count: 1, channel: "main", installed: installed, commits: commits.slice(1, 2) }; p.confirmingUpdate = true }
      if (kind === "updating") p.upd = { state: "updating", step: "Installing…", count: 1, installed: installed, commits: commits.slice(1, 2) }
      if (kind === "failed") p.upd = { state: "failed", installed: installed,
        message: "install.sh failed (exit 3): see ~/.local/state/auren/update.log. Auren is back on 54ebe81." }
      if (kind === "cursor") { p.cursorActive = true; p.inNav = false; p.cursorRow = 1; p.cursorCol = 1 }
      if (kind === "nav") { p.cursorActive = true; p.inNav = true }
      if (kind === "down") {      // the keyboard to the last row of the page: it scrolls on a short screen
        p.cursorActive = true; p.inNav = false; p.cursorRow = 0; p.cursorCol = 0
        for (var i = 0; i < p.pageRows.length - 1; i++) p.moveCursor(0, 1)
      }
    }
    Timer {
      id: shotTimer
      property string name: ""
      property string fake: ""
      interval: 1000
      onTriggered: { win.fake(fake); grabTimer.restart() }
    }
    Timer {
      id: grabTimer
      interval: 500
      onTriggered: stage.grabToImage(function(r) {
        r.saveToFile(Quickshell.env("OUT") + "/" + shotTimer.name + ".png"); tick.restart()
      })
    }
  }
}

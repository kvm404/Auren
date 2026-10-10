// A gallery of every mascot in every mood, for drawing and checking mascots.
//
//   qs -p ~/Projects/auren/mascot/gallery.qml                        # on screen (right-click a mascot to poke it)
//   GALLERY_OUT=/tmp/g.png qs -p ~/Projects/auren/mascot/gallery.qml # save a PNG and quit
//   GALLERY_BG=#f4f1ea ...                                            # try a light background
//   GALLERY_U=10 GALLERY_ONLY=idle,speaking ...                       # bigger, only some moods
import QtQuick
import Quickshell
import Quickshell.Wayland
import "mascots/clawd"
import "mascots/veyra"
import "mascots/auren"

ShellRoot {
  id: root

  readonly property string out: Quickshell.env("GALLERY_OUT") || ""
  readonly property real u: Number(Quickshell.env("GALLERY_U") || 5)
  readonly property var kinds: ["clawd", "veyra", "auren"]
  readonly property var only: (Quickshell.env("GALLERY_ONLY") || "").split(",").filter(function(x) { return x })
  readonly property var moods: allMoods.filter(function(m) { return only.length === 0 || only.indexOf(m.label) >= 0 })
  readonly property var allMoods: [
    { label: "idle", mood: "idle" },
    { label: "listening", mood: "listening", level: 0.7 },
    { label: "thinking", mood: "thinking" },
    { label: "working", mood: "working" },
    { label: "mouse", mood: "working", mouse: true },
    { label: "speaking", mood: "speaking", level: 0.8 },
    { label: "yes / no?", mood: "idle", awaiting: true },
    { label: "error", mood: "error" },
    { label: "happy", mood: "idle", cheer: true },
    { label: "drowsy", mood: "idle", drowsy: true },
    { label: "asleep", mood: "idle", asleep: true },
    { label: "off", mood: "idle", off: true }
  ]

  PanelWindow {
    id: win
    color: "transparent"
    WlrLayershell.namespace: "auren-mascot-gallery"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    exclusionMode: ExclusionMode.Ignore
    anchors { left: true; top: true }
    margins { left: 10; top: 40 }
    implicitWidth: sheet.width
    implicitHeight: sheet.height

    Rectangle {
      id: sheet
      width: grid.width + 20
      height: grid.height + 20
      color: Quickshell.env("GALLERY_BG") || "#1b1d23"

      Grid {
        id: grid
        x: 10
        y: 10
        columns: root.moods.length + 1
        spacing: 2

        Repeater {
          model: (root.kinds.length + 1) * (root.moods.length + 1)
          Item {
            id: cell
            required property int index
            readonly property int row: Math.floor(index / (root.moods.length + 1))
            readonly property int col: index % (root.moods.length + 1)
            readonly property var spec: col > 0 ? root.moods[col - 1] : null
            readonly property string kind: row > 0 ? root.kinds[row - 1] : ""
            width: col === 0 ? 60 : 24 * root.u + 4
            height: row === 0 ? 20 : 20 * root.u

            Text {
              visible: cell.row === 0 || cell.col === 0
              anchors.centerIn: parent
              text: cell.row === 0 ? (cell.spec ? cell.spec.label : "") : cell.kind
              color: "#9aa0aa"
              font.family: "monospace"
              font.pixelSize: 12
            }

            Loader {
              id: loader
              active: cell.row > 0 && cell.col > 0
              anchors.centerIn: parent
              source: active ? "mascots/" + cell.kind + "/" + cell.kind.charAt(0).toUpperCase() + cell.kind.slice(1) + ".qml" : ""
              onLoaded: {
                var s = cell.spec
                item.u = root.u
                item.mood = s.mood
                item.level = s.level || 0
                item.mouse = !!s.mouse
                item.awaiting = !!s.awaiting
                item.drowsy = !!s.drowsy || !!s.asleep
                item.asleep = !!s.asleep
                item.off = !!s.off
                item.opacity = s.off ? 0.4 : s.drowsy ? 0.45 : 1
                item.layer.enabled = item.opacity < 1     // faded as one picture, not part by part
                if (s.cheer) cheerTimer.start()
              }
              Timer { id: cheerTimer; interval: 400; onTriggered: loader.item.cheer() }
              Timer {     // keep the voice level up so the pose shows it
                running: loader.status === Loader.Ready && (cell.spec.level || 0) > 0
                interval: 50
                repeat: true
                onTriggered: loader.item.level = cell.spec.level * (0.75 + 0.25 * Math.random())
              }
            }
            MouseArea {
              anchors.fill: parent
              acceptedButtons: Qt.RightButton
              onClicked: if (loader.item) loader.item.poke()
            }
          }
        }
      }
    }
  }

  Timer {
    running: root.out !== ""
    interval: 1100
    onTriggered: sheet.grabToImage(function(result) {
      result.saveToFile(root.out)
      Qt.quit()
    })
  }
}

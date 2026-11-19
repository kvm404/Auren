// Auren.app's icon: the Auren robot on a dark rounded square, drawn by the mascot's own QML so the app
// and the robot on screen are the same character. `python -m auren.mac.app icon` renders it at 1024 px
// and writes src/auren/mac/app/Auren.icns (it needs PySide6, so run it on a Mac or with PySide6 installed).
import QtQuick
import QtQuick.Window
import "mascots/auren"

Window {
  id: w
  width: 1024
  height: 1024
  color: "transparent"
  visible: true

  signal saved()
  function save(path) { icon.grabToImage(r => { r.saveToFile(path); w.saved() }) }

  Item {
    id: icon
    objectName: "icon"
    anchors.fill: parent

    // macOS's icon grid: an 824 px rounded square in the 1024 px canvas, a soft shadow under it
    Rectangle {
      x: 100; y: 112; width: 824; height: 824; radius: 186
      color: "#000000"; opacity: 0.28
    }
    Rectangle {
      id: tile
      x: 100; y: 100; width: 824; height: 824; radius: 186
      gradient: Gradient {
        GradientStop { position: 0; color: "#2C3446" }
        GradientStop { position: 1; color: "#141823" }
      }
      border.color: "#3A4458"
      border.width: 2
    }
    Auren {
      id: bot
      u: 36
      mood: "idle"
      sleepAnimation: false
      x: (w.width - implicitWidth) / 2
      y: (w.height - implicitHeight) / 2 - 40
    }
  }
}

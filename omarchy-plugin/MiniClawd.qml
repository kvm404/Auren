import QtQuick

// Clawd at bar size: the logo's blocks at one pixel each (16x10 on a 16 px canvas).
// Bar colour when idle, Claude orange while auren works, dim with shut eyes when off.
Item {
  id: m

  property string mood: "idle"        // off, idle, listening, thinking, working, speaking, error, awaiting
  property color foreground: "white"
  readonly property bool active: mood !== "idle" && mood !== "off"
  readonly property color skin: mood === "off" ? Qt.darker(foreground, 1.9)
                              : mood === "error" ? "#e05d5d"
                              : active || mood === "awaiting" ? "#D77757" : foreground
  readonly property real u: Math.max(1, Math.floor(width / 16))

  // a small bob while working, quicker while listening
  property real bob: 0
  SequentialAnimation on bob {
    running: m.active
    loops: Animation.Infinite
    NumberAnimation { to: -1; duration: m.mood === "listening" ? 180 : 320; easing.type: Easing.OutQuad }
    NumberAnimation { to: 0; duration: m.mood === "listening" ? 180 : 320; easing.type: Easing.InQuad }
    onRunningChanged: if (!running) m.bob = 0
  }

  Item {
    width: 16 * m.u
    height: 10 * m.u
    anchors.centerIn: parent
    anchors.verticalCenterOffset: Math.round(m.bob) * m.u

    Rectangle { x: 2 * m.u; y: 0; width: 12 * m.u; height: 8 * m.u; color: m.skin }                          // body
    Rectangle { x: 0; y: (m.mood === "listening" ? 3 : 4) * m.u; width: 2 * m.u; height: 2 * m.u; color: m.skin }   // arms
    Rectangle { x: 14 * m.u; y: (m.mood === "listening" ? 3 : 4) * m.u; width: 2 * m.u; height: 2 * m.u; color: m.skin }
    Repeater {                                                                                                // legs
      model: [3, 5, 10, 12]
      Rectangle { required property var modelData; x: modelData * m.u; y: 8 * m.u; width: m.u; height: 2 * m.u; color: m.skin }
    }
    Repeater {                                                                                                // eyes
      model: [4, 11]
      Rectangle {
        required property var modelData
        x: modelData * m.u
        y: (m.mood === "off" ? 3 : 2) * m.u
        width: m.u
        height: (m.mood === "off" ? 1 : 2) * m.u
        color: "#1d1310"
      }
    }
  }
}

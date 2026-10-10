import QtQuick

// The mascot at bar size, in pixels (one unit = 1 px on a 16 px canvas): Clawd, Veyra or Auren.
// Bar colour when idle, the mascot's own colour while auren works, red on errors, dim with shut eyes
// when off. Bobs while active, quicker while listening.
Item {
  id: m

  property string kind: "clawd"       // clawd, veyra, auren
  property string mood: "idle"        // off, idle, listening, thinking, working, speaking, error, awaiting
  property color foreground: "white"
  readonly property bool active: mood !== "idle" && mood !== "off"
  readonly property bool lit: active || mood === "awaiting"
  readonly property bool off: mood === "off"
  readonly property color brand: kind === "veyra" ? "#A08BF7" : kind === "auren" ? "#6F92D8" : "#D77757"
  readonly property color skin: off ? Qt.darker(foreground, 1.9) : mood === "error" ? "#e05d5d" : lit ? brand : foreground
  readonly property real u: Math.max(1, Math.floor(width / 16))

  // [x, y, w, h, role]. Roles: s skin, e eye, v visor, l LED (Auren's eyes), g gold (antenna), t tail tip, h thruster
  readonly property var pixels: {
    var listen = mood === "listening"
    if (kind === "veyra") return [
      [3, 0, 1, 1, "s"], [3, 1, 2, 1, "s"], [3, 2, 3, 1, "s"], [12, 0, 1, 1, "s"], [11, 1, 2, 1, "s"], [10, 2, 3, 1, "s"],
      [3, 3, 10, 1, "s"], [2, 4, 12, 5, "s"], [3, 9, 10, 1, "s"],
      [14, 6, 1, 3, "s"], [15, 3, 1, 4, "t"],
      off ? [5, 6, 2, 1, "e"] : [5, 5, 2, 2, "e"], off ? [9, 6, 2, 1, "e"] : [9, 5, 2, 2, "e"],
      [5, 10, 2, 1, "s"], [9, 10, 2, 1, "s"]
    ]
    if (kind === "auren") return [
      [7, 0, 2, 2, "g"],
      [4, 2, 8, 1, "s"], [3, 3, 10, 6, "s"], [4, 9, 8, 1, "s"],
      [5, 4, 6, 3, "v"],
      off ? [6, 6, 1, 1, "v"] : [6, 4, 1, 2, "l"], off ? [9, 6, 1, 1, "v"] : [9, 4, 1, 2, "l"],
      [0, 5, 2, 2, "s"], listen ? [14, 3, 2, 2, "s"] : [14, 5, 2, 2, "s"],
      [7, 11, 2, 1, "h"]
    ]
    var arm = listen ? 3 : 4
    return [
      [2, 0, 12, 8, "s"], [0, arm, 2, 2, "s"], [14, arm, 2, 2, "s"],
      [3, 8, 1, 2, "s"], [5, 8, 1, 2, "s"], [10, 8, 1, 2, "s"], [12, 8, 1, 2, "s"],
      off ? [4, 3, 1, 1, "e"] : [4, 2, 1, 2, "e"], off ? [11, 3, 1, 1, "e"] : [11, 2, 1, 2, "e"]
    ]
  }
  readonly property int rows: kind === "auren" ? 12 : kind === "veyra" ? 11 : 10

  function colorOf(role) {
    switch (role) {
      case "e": return "#1d1310"
      case "v": return "#151a26"
      case "l": return lit ? "#FFD15C" : off ? "#151a26" : foreground
      case "g": return lit ? "#F5B83D" : skin
      case "t": return lit ? "#FFF3FA" : skin
      case "h": return active ? "#7FDBFF" : "transparent"
    }
    return skin
  }

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
    height: m.rows * m.u
    anchors.centerIn: parent
    anchors.verticalCenterOffset: Math.round(m.bob) * m.u
    Repeater {
      model: m.pixels
      Rectangle {
        required property var modelData
        x: modelData[0] * m.u
        y: modelData[1] * m.u
        width: modelData[2] * m.u
        height: modelData[3] * m.u
        color: m.colorOf(modelData[4])
      }
    }
  }
}

// The look Auren's Mac windows share (mac-setup.qml, mac-window.qml): macOS's own greys, light or dark as
// the system is, with Auren's orange for what's on or chosen. Sizes follow System Settings.
pragma Singleton
import QtQuick

QtObject {
  readonly property bool dark: Application.styleHints.colorScheme === Qt.ColorScheme.Dark

  readonly property color bg: dark ? "#1e1e20" : "#f5f5f7"
  readonly property color sidebar: dark ? "#262628" : "#ebebee"
  readonly property color card: dark ? "#2b2b2e" : "#ffffff"
  readonly property color line: dark ? "#3a3a3d" : "#e5e5ea"
  readonly property color text: dark ? "#f5f5f7" : "#1d1d1f"
  readonly property color dim: dark ? "#98989d" : "#6e6e73"
  readonly property color faint: dark ? "#6e6e73" : "#aeaeb2"
  readonly property color field: dark ? "#1e1e20" : "#f5f5f7"
  readonly property color control: dark ? "#3a3a3d" : "#e8e8ed"      // a button that isn't the main one
  readonly property color pressed: dark ? "#48484b" : "#d8d8dd"
  readonly property color selected: dark ? "#3a3a3e" : "#dcdce1"     // the sidebar's chosen page
  readonly property color segment: dark ? "#636366" : "#ffffff"      // a segmented control's chosen part
  readonly property color off: dark ? "#48484b" : "#e2e2e7"          // a switch that's off, a slider's track
  readonly property color accent: "#D77757"
  readonly property color ok: "#34C759"
  readonly property color warn: "#FF9F0A"

  readonly property int body: 13
  readonly property int small: 11
  readonly property int title: 20
}

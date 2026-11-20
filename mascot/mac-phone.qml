// Auren's Phone window on macOS (menu bar icon → Phone…): the bar panel's Phone page on Omarchy. Hosted by
// src/auren/mac/mascot.py, next to mac.qml. `phone` (src/auren/mac/phone_window.py) gives it:
//
//   phone.open           whether the window shows            phone.alive    whether Auren runs
//   phone.status         where Auren listens                 phone.warn     Tailscale is off ("" if not)
//   phone.pairStage      "", starting, showing, paired, expired, error        phone.pairNote  what happened
//   phone.pairQr, pairExpires, pairWhere, pairWarn            the code while it shows
//   phone.appShown, appQr, appUrl, appText                    Get the app's QR code
//   phone.phones         [{id, name, detail, connected}]      phone.unpairAsk  the phone whose Unpair asks again
//   phone.stopping       a cancelled pairing is still stopping (Pair waits for it)
//   phone.startPairing(), cancelPairing(), toggleApp(), unpair(id), closeWindow()
//   phone.raiseWindow    (signal) asked for again while open: come forward
import QtQuick
import QtQuick.Window

Window {
  id: w
  title: "Auren Phone"
  width: 500
  readonly property real fit: col.implicitHeight + 48
  height: fit
  minimumWidth: 500; maximumWidth: 500
  minimumHeight: fit; maximumHeight: fit
  visible: phone.open
  onClosing: phone.closeWindow()
  Connections { target: phone; function onRaiseWindow() { if (w.visible) { w.raise(); w.requestActivate() } } }
  color: pal.bg

  readonly property bool dark: Application.styleHints.colorScheme === Qt.ColorScheme.Dark
  readonly property bool pairing: phone.pairStage === "starting" || phone.pairStage === "showing"
  QtObject {
    id: pal
    readonly property color bg: w.dark ? "#1d1d1f" : "#f5f5f7"
    readonly property color card: w.dark ? "#2a2a2d" : "#ffffff"
    readonly property color line: w.dark ? "#3a3a3d" : "#e5e5ea"
    readonly property color text: w.dark ? "#f5f5f7" : "#1d1d1f"
    readonly property color dim: w.dark ? "#98989d" : "#6e6e73"
    readonly property color accent: "#D77757"
    readonly property color ok: "#34C759"
    readonly property color warn: "#FF9F0A"
  }

  // The countdown: seconds the code still works
  property real now: Date.now() / 1000
  Timer { interval: 1000; repeat: true; running: phone.pairStage === "showing"; onTriggered: w.now = Date.now() / 1000 }
  readonly property int left: Math.max(0, Math.round(phone.pairExpires - w.now))

  // ---- pieces -----------------------------------------------------------------------------------
  component Pill: Rectangle {
    id: pill
    property string label
    property bool primary: false
    signal clicked()
    implicitWidth: t.implicitWidth + 28
    implicitHeight: 28
    radius: 14
    opacity: enabled ? 1 : 0.45
    color: primary ? (ma.pressed ? Qt.darker(pal.accent, 1.15) : pal.accent)
                   : (ma.pressed ? pal.line : (w.dark ? "#3a3a3d" : "#e8e8ed"))
    Text { id: t; anchors.centerIn: parent; text: pill.label; font.pixelSize: 13; font.weight: Font.Medium
           color: pill.primary ? "white" : pal.text }
    MouseArea { id: ma; anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: pill.clicked() }
  }

  component Code: Rectangle {      // a QR code on white, whatever the theme
    property string source
    width: 168; height: 168; radius: 10
    color: "white"
    border.color: pal.line
    Image { anchors.fill: parent; anchors.margins: 6; source: parent.source; cache: false; smooth: false
            fillMode: Image.PreserveAspectFit }
  }

  component Caption: Text {
    width: parent ? parent.width : 0
    color: pal.dim; font.pixelSize: 12; wrapMode: Text.WordWrap
  }

  // ---- the window -------------------------------------------------------------------------------
  Rectangle { anchors.fill: parent; color: pal.bg }

  Column {
    id: col
    x: 24; y: 24
    width: w.width - 48
    spacing: 16

    Column {
      width: parent.width
      spacing: 4
      Text { text: "Phone"; color: pal.text; font.pixelSize: 22; font.weight: Font.Bold }
      Caption { text: "Talk to Auren from the Auren app on your Android phone: on this Wi-Fi, or anywhere over Tailscale." }
    }

    // Where Auren listens, and Tailscale being off
    Rectangle {
      width: parent.width
      height: where.implicitHeight + 24
      radius: 12
      color: pal.card; border.color: pal.line
      Column {
        id: where
        x: 16; width: parent.width - 32
        anchors.verticalCenter: parent.verticalCenter
        spacing: 6
        Text { width: parent.width; text: phone.status; color: pal.text; font.pixelSize: 13; wrapMode: Text.WordWrap }
        Text { width: parent.width; visible: phone.warn !== ""; text: phone.warn; color: pal.warn; font.pixelSize: 12
               wrapMode: Text.WordWrap }
      }
    }

    Row {
      spacing: 8
      Pill { visible: !w.pairing; primary: true; enabled: phone.alive && !phone.stopping
             label: phone.stopping ? "Stopping…" : phone.phones.length ? "Pair another phone" : "Pair a phone"
             onClicked: phone.startPairing() }
      Pill { visible: w.pairing; primary: true; label: "Cancel"; onClicked: phone.cancelPairing() }
      Pill { visible: !w.pairing; label: phone.appShown ? "Hide the app code" : "Get the app"; onClicked: phone.toggleApp() }
    }

    Caption { visible: phone.pairStage === "starting"; text: "Opening a pairing…" }

    Row {         // the pairing code, while it works
      width: parent.width
      spacing: 16
      visible: phone.pairStage === "showing" && phone.pairQr !== ""
      Code { id: pairCode; source: phone.pairQr }
      Column {
        width: parent.width - pairCode.width - parent.spacing
        spacing: 6
        Text { width: parent.width; text: "Scan it in the Auren app (Pair)."; color: pal.text; font.pixelSize: 13
               font.weight: Font.DemiBold; wrapMode: Text.WordWrap }
        Caption { text: "It works once, for " + Math.floor(w.left / 60) + ":" + ("0" + w.left % 60).slice(-2)
                        + " more, from a phone on " + phone.pairWhere + "." }
        Caption { visible: phone.pairWarn !== ""; text: phone.pairWarn; color: pal.warn }
      }
    }

    Row {         // Get the app: the download's QR code, for the phone's camera
      width: parent.width
      spacing: 16
      visible: phone.appShown
      Code { id: appCode; source: phone.appQr }
      Column {
        width: parent.width - appCode.width - parent.spacing
        spacing: 6
        Text { width: parent.width; text: "Scan it with your phone's camera."; color: pal.text; font.pixelSize: 13
               font.weight: Font.DemiBold; wrapMode: Text.WordWrap }
        Caption { text: phone.appText }
        Caption { text: phone.appUrl; elide: Text.ElideMiddle; wrapMode: Text.NoWrap }
      }
    }

    Text {
      width: parent.width
      visible: phone.pairNote !== "" && !w.pairing
      text: phone.pairNote
      color: phone.pairStage === "paired" ? pal.ok : pal.warn
      font.pixelSize: 13; wrapMode: Text.WordWrap
    }

    // ---- the paired phones ------------------------------------------------------------------------
    Text { text: "PAIRED PHONES · " + phone.phones.length; color: pal.dim; font.pixelSize: 11; font.weight: Font.DemiBold
           font.letterSpacing: 0.6 }
    Caption { visible: phone.phones.length === 0
              text: "None yet. Get the app on your phone (scan its code with the camera), then pair it here." }
    Rectangle {
      visible: phone.phones.length > 0
      width: parent.width
      height: list.implicitHeight
      radius: 12
      color: pal.card; border.color: pal.line
      Column {
        id: list
        width: parent.width
        Repeater {
          model: phone.phones
          Item {
            id: row
            required property var modelData
            required property int index
            width: list.width
            implicitHeight: Math.max(56, info.implicitHeight + 24)
            Column {
              id: info
              x: 16; anchors.verticalCenter: parent.verticalCenter
              width: parent.width - x - unpair.width - 32
              spacing: 3
              Text { width: parent.width; text: row.modelData.name; color: pal.text; font.pixelSize: 14
                     font.weight: Font.DemiBold; elide: Text.ElideRight }
              Text { width: parent.width; text: row.modelData.detail; font.pixelSize: 12; elide: Text.ElideRight
                     color: row.modelData.connected ? pal.accent : pal.dim }
            }
            Pill { id: unpair; anchors.right: parent.right; anchors.rightMargin: 16; anchors.verticalCenter: parent.verticalCenter
                   label: phone.unpairAsk === row.modelData.id ? "Unpair? Click again" : "Unpair"
                   primary: phone.unpairAsk === row.modelData.id
                   onClicked: phone.unpair(row.modelData.id) }
            Rectangle { visible: row.index < phone.phones.length - 1; x: 16; anchors.bottom: parent.bottom
                        width: parent.width - x; height: 1; color: pal.line }
          }
        }
      }
    }

    Item {
      width: parent.width
      height: 32
      Text { anchors.left: parent.left; anchors.right: done.left; anchors.rightMargin: 12
             anchors.verticalCenter: parent.verticalCenter
             color: pal.dim; font.pixelSize: 12; wrapMode: Text.WordWrap
             text: "Unpairing stops a phone at once: it has to pair again to come back." }
      Pill { id: done; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; label: "Done"
             onClicked: phone.closeWindow() }
    }
  }
}

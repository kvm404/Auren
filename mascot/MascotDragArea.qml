import QtQuick

// The layer surface never moves. Measure every event in that fixed coordinate space,
// keeping the original grab offset even when the pointer outruns the mascot or hits an edge.
MouseArea {
  id: root
  required property Item coordinateSpace
  required property real targetX
  required property real targetY
  required property real maximumX
  required property real maximumY

  property bool dragging: false
  readonly property bool held: gestureButton !== Qt.NoButton
  property int gestureButton: Qt.NoButton
  property point pressPosition: Qt.point(0, 0)
  property point grabOffset: Qt.point(0, 0)

  signal moved(real x, real y)
  signal dropped()
  signal tapped(int button)

  hoverEnabled: true
  acceptedButtons: Qt.LeftButton | Qt.RightButton
  preventStealing: true
  cursorShape: dragging ? Qt.ClosedHandCursor : Qt.PointingHandCursor

  function updatePosition(mouse) {
    if (gestureButton !== Qt.LeftButton) return
    var p = mapToItem(coordinateSpace, mouse.x, mouse.y)
    var dx = p.x - pressPosition.x, dy = p.y - pressPosition.y
    if (!dragging && dx * dx + dy * dy <= drag.threshold * drag.threshold) return
    dragging = true
    moved(Math.max(0, Math.min(Math.max(0, maximumX), p.x - grabOffset.x)),
          Math.max(0, Math.min(Math.max(0, maximumY), p.y - grabOffset.y)))
  }

  function cancelDrag() {
    var wasDragging = dragging
    dragging = false
    gestureButton = Qt.NoButton
    if (wasDragging) dropped()       // keep the last valid position; cancellation is never a click
  }

  onPressed: (mouse) => {
    if (held) return                 // another button mustn't overwrite an active gesture
    gestureButton = mouse.button
    if (mouse.button !== Qt.LeftButton) return
    pressPosition = mapToItem(coordinateSpace, mouse.x, mouse.y)
    grabOffset = Qt.point(pressPosition.x - targetX, pressPosition.y - targetY)
  }
  onPositionChanged: (mouse) => updatePosition(mouse)
  onReleased: (mouse) => {
    if (mouse.button !== gestureButton) return
    updatePosition(mouse)            // include motion delivered with the release event
    var wasDragging = dragging
    cancelDrag()
    if (!wasDragging && mouse.x >= 0 && mouse.x < width && mouse.y >= 0 && mouse.y < height)
      tapped(mouse.button)
  }
  onCanceled: cancelDrag()
  onEnabledChanged: if (!enabled) cancelDrag()
  onVisibleChanged: if (!visible) cancelDrag()
}

import QtQuick
import qs.Commons
// Preview stand-in: the popup's card drawn in place, without a layer-shell window or keyboard grab.
Rectangle {
  id: root
  property Item anchorItem: null
  property var owner: null
  property QtObject bar: null
  property bool open: false
  property Item focusTarget: null
  property int contentWidth: 280
  property int contentHeight: 200
  property int padding: Style.spacing.popupPadding
  default property alias contentItem: holder.children
  function fittedContentWidth(w) { return Math.round(w) }
  function fittedContentHeight(h) { return Math.round(h + padding * 2) }
  parent: owner ? owner.parent : null
  x: 20; y: 20
  width: contentWidth + padding * 2
  height: contentHeight
  visible: open
  color: "#0b0d12"
  border.color: "#3fbf7f"
  border.width: 2
  Item { id: holder; anchors.fill: parent; anchors.margins: root.padding }
}

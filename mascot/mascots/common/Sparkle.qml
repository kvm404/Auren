import QtQuick
import QtQuick.Shapes

// A four-pointed star, drawn around its centre (this item has no size). size = tip to tip.
Item {
  id: s

  property real size: 6
  property color color: "#FFD580"
  readonly property real r: size / 2
  readonly property real k: size * 0.09      // how far the waist is from the centre

  Shape {
    x: -s.size / 2
    y: -s.size / 2
    width: s.size
    height: s.size
    preferredRendererType: Shape.CurveRenderer
    ShapePath {
      fillColor: s.color
      strokeColor: "transparent"
      startX: s.r; startY: 0
      PathQuad { x: s.size; y: s.r; controlX: s.r + s.k; controlY: s.r - s.k }
      PathQuad { x: s.r; y: s.size; controlX: s.r + s.k; controlY: s.r + s.k }
      PathQuad { x: 0; y: s.r; controlX: s.r - s.k; controlY: s.r + s.k }
      PathQuad { x: s.r; y: 0; controlX: s.r - s.k; controlY: s.r - s.k }
    }
  }
}

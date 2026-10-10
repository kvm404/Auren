import QtQuick
import QtQuick.Shapes

// Using the mouse: a little arrow pointer that clicks (shrinks a bit, ~1.6 times a second).
// Placed by its tip; about 2.6 x 4 units.
Shape {
  id: p

  required property Item m          // the mascot (a MascotBase)
  property color ink: "#1d1310"
  readonly property real u: m.u

  width: 3 * u
  height: 4 * u
  scale: 1 - 0.18 * Math.max(0, m.osc(1.6))
  transformOrigin: Item.TopLeft
  preferredRendererType: Shape.CurveRenderer
  ShapePath {
    fillColor: "#ffffff"
    strokeColor: p.ink
    strokeWidth: Math.max(1, p.u * 0.25)
    joinStyle: ShapePath.RoundJoin
    startX: 0; startY: 0
    PathLine { x: 0; y: 3.4 * p.u }
    PathLine { x: 0.9 * p.u; y: 2.6 * p.u }
    PathLine { x: 1.5 * p.u; y: 3.9 * p.u }
    PathLine { x: 2.1 * p.u; y: 3.6 * p.u }
    PathLine { x: 1.5 * p.u; y: 2.4 * p.u }
    PathLine { x: 2.6 * p.u; y: 2.4 * p.u }
    PathLine { x: 0; y: 0 }
  }
}

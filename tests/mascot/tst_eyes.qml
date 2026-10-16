import QtQuick
import QtTest
import "../../mascot/mascots/auren"
import "../../mascot/mascots/veyra"

// The bar panel measures the pointer from a box bigger than the mascot (its card), so pointerX/Y can
// leave 0..1. The eyes must stay on the face anyway.
Item {
  width: 400; height: 400

  Auren { id: auren; hovered: true }
  Veyra { id: veyra; hovered: true }

  TestCase {
    name: "MascotEyes"
    when: windowShown

    function look(m, x, y) {
      m.pointerX = x
      m.pointerY = y
      tryCompare(m, "lookX", Math.max(-1, Math.min(1, (x - 0.5) * 2)))
      tryCompare(m, "lookY", Math.max(-1, Math.min(1, (y - 0.5) * 2)))
    }
    function test_pointer_outside_the_body_keeps_eyes_in_range_data() {
      return [{ tag: "auren", m: auren }, { tag: "veyra", m: veyra }]
    }
    function test_pointer_outside_the_body_keeps_eyes_in_range(d) {
      look(d.m, -0.4, 0.5)      // left of the body
      compare(d.m.lookX, -1)
      look(d.m, 3.5, 0.5)       // right of the body
      compare(d.m.lookX, 1)
      look(d.m, 0.5, -2)
      compare(d.m.lookY, -1)
      look(d.m, 0.5, 2)
      compare(d.m.lookY, 1)
      look(d.m, 0.25, 0.5)      // inside: unchanged
      fuzzyCompare(d.m.lookX, -0.5, 0.001)
    }
  }
}

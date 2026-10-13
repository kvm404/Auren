import QtQuick
import QtTest
import "../../mascot"

Item {
  id: scene
  width: 800; height: 600

  Item {
    id: holder
    x: 300; y: 200
    width: 144; height: 114
    MascotDragArea {
      id: area
      x: 24; y: 23
      width: 96; height: 78
      coordinateSpace: scene
      targetX: holder.x; targetY: holder.y
      maximumX: scene.width - holder.width
      maximumY: scene.height - holder.height
      onMoved: (x, y) => { holder.x = x; holder.y = y }
    }
  }
  SignalSpy { id: taps; target: area; signalName: "tapped" }
  SignalSpy { id: drops; target: area; signalName: "dropped" }

  TestCase {
    name: "MascotDrag"
    when: windowShown

    function init() {
      area.cancelDrag()
      area.enabled = true; holder.visible = true
      scene.width = 800; scene.height = 600
      holder.x = 300; holder.y = 200
      taps.clear(); drops.clear()
      mouseMove(scene, 10, 10)
    }
    function cleanup() {
      mouseRelease(scene, 10, 10, Qt.LeftButton)
      mouseRelease(scene, 10, 10, Qt.RightButton)
      area.cancelDrag()
    }
    function press() { mousePress(scene, 370, 250, Qt.LeftButton) }

    function test_left_click_and_jitter() {
      press()
      mouseMove(scene, 372, 251)
      compare(holder.x, 300); compare(holder.y, 200)
      verify(!area.dragging)
      mouseRelease(scene, 372, 251, Qt.LeftButton)
      compare(taps.count, 1); compare(taps.signalArguments[0][0], Qt.LeftButton)
      compare(drops.count, 0); verify(!area.held)
    }
    function test_fast_drag_keeps_grab_offset_and_does_not_click() {
      press()
      mouseMove(scene, 600, 400)
      compare(holder.x, 530); compare(holder.y, 350)
      verify(area.dragging)
      mouseMove(scene, 410, 280)
      compare(holder.x, 340); compare(holder.y, 230)
      mouseRelease(scene, 415, 284, Qt.LeftButton)
      compare(holder.x, 345); compare(holder.y, 234)
      compare(taps.count, 0); compare(drops.count, 1)
      verify(!area.held); verify(!area.dragging)
    }
    function test_edges_keep_offset_when_reversing() {
      press()
      mouseMove(scene, 5, 5)
      compare(holder.x, 0); compare(holder.y, 0)
      mouseMove(scene, 100, 90)
      compare(holder.x, 30); compare(holder.y, 40)
      mouseMove(scene, 799, 599)
      compare(holder.x, 656); compare(holder.y, 486)
      mouseMove(scene, 500, 300)
      compare(holder.x, 430); compare(holder.y, 250)
      mouseRelease(scene, 500, 300, Qt.LeftButton)
      compare(taps.count, 0); compare(drops.count, 1)
    }
    function test_cancelled_grab_never_clicks_and_next_drag_works() {
      press()
      mouseMove(scene, 500, 300)
      area.cancelDrag()
      compare(holder.x, 430); compare(holder.y, 250)
      verify(!area.dragging); verify(!area.held)
      mouseRelease(scene, 500, 300, Qt.LeftButton)
      compare(taps.count, 0); compare(drops.count, 1)
      mousePress(scene, 500, 300, Qt.LeftButton)
      mouseMove(scene, 550, 350)
      compare(holder.x, 480); compare(holder.y, 300)
      mouseRelease(scene, 550, 350, Qt.LeftButton)
      compare(taps.count, 0); compare(drops.count, 2)
    }
    function test_hide_cancels_the_gesture() {
      press()
      mouseMove(scene, 500, 300)
      holder.visible = false
      verify(!area.dragging); verify(!area.held)
      mouseRelease(scene, 500, 300, Qt.LeftButton)
      compare(taps.count, 0); compare(drops.count, 1)
    }
    function test_right_button_cannot_drag() {
      mousePress(scene, 370, 250, Qt.RightButton)
      mouseMove(scene, 390, 265)
      compare(holder.x, 300); compare(holder.y, 200)
      verify(!area.dragging)
      mouseRelease(scene, 390, 265, Qt.RightButton)
      compare(taps.count, 1); compare(taps.signalArguments[0][0], Qt.RightButton)
      compare(drops.count, 0)
    }
    function test_right_release_outside_the_body_does_not_poke() {
      mousePress(scene, 370, 250, Qt.RightButton)
      mouseMove(scene, 600, 400)
      mouseRelease(scene, 600, 400, Qt.RightButton)
      compare(taps.count, 0); compare(drops.count, 0)
      verify(!area.held); verify(!area.dragging)
    }
    function test_screen_smaller_than_mascot_never_creates_negative_coordinates() {
      press()
      scene.width = 100; scene.height = 90
      mouseMove(scene, 60, 60)
      compare(holder.x, 0); compare(holder.y, 0)
      mouseRelease(scene, 60, 60, Qt.LeftButton)
      compare(taps.count, 0); compare(drops.count, 1)
    }
    function test_disabled_area_cancels_and_release_does_not_click() {
      press()
      mouseMove(scene, 500, 300)
      area.enabled = false
      verify(!area.dragging); verify(!area.held)
      mouseRelease(scene, 500, 300, Qt.LeftButton)
      compare(taps.count, 0); compare(drops.count, 1)
    }
    function test_second_button_does_not_reset_the_grab_position() {
      press()
      mouseMove(scene, 500, 300)
      mousePress(scene, 500, 300, Qt.RightButton)
      mouseMove(scene, 550, 350)
      compare(holder.x, 480); compare(holder.y, 300)
      mouseRelease(scene, 550, 350, Qt.RightButton)
      mouseRelease(scene, 550, 350, Qt.LeftButton)
      compare(taps.count, 0); compare(drops.count, 1)
    }
    function test_fractional_coordinates() {
      holder.x = 300.5; holder.y = 200.25
      press()
      mouseMove(scene, 500, 300)
      compare(holder.x, 430.5); compare(holder.y, 250.25)
      mouseRelease(scene, 500, 300, Qt.LeftButton)
      compare(drops.count, 1)
    }
    function test_hover_does_not_drag() {
      mouseMove(scene, 370, 250)
      mouseMove(scene, 400, 280)
      compare(holder.x, 300); compare(holder.y, 200)
      verify(!area.dragging); verify(!area.held)
      compare(taps.count, 0); compare(drops.count, 0)
    }
    function test_repeated_drags() {
      for (var i = 0; i < 40; i++) {
        holder.x = 300; holder.y = 200
        press()
        mouseMove(scene, 500, 300)
        mouseRelease(scene, 500, 300, Qt.LeftButton)
        compare(holder.x, 430); compare(holder.y, 250)
        verify(!area.held); verify(!area.dragging)
      }
      compare(taps.count, 0); compare(drops.count, 40)
    }
    function test_stationary_pointer_does_not_move_the_mascot_again() {
      press()
      mouseMove(scene, 500, 300)
      mouseMove(scene, 500, 300)
      compare(holder.x, 430); compare(holder.y, 250)
      mouseRelease(scene, 500, 300, Qt.LeftButton)
      compare(drops.count, 1)
    }
  }
}

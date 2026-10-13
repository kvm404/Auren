"""Render the production bubble card in Qt to catch overflow and interrupted slides."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

QT_RUNNER = "/usr/lib/qt6/bin/qmltestrunner"
if not Path(QT_RUNNER).is_file():
    QT_RUNNER = shutil.which("qmltestrunner")

SCENE = """import QtQuick
import QtTest
Item {
  id: root
  width: 600; height: 600
  property string bubbleText: ""
  property bool bubbleMuted: false
  property color bg: "#101315"
  property color fg: "#cacccc"
  property color accent: "#D77757"
  QtObject { id: bubbleWin; property bool above: true }
  Item { id: tail; height: 10 }
"""

CHECKS = r"""
  TestCase {
    name: "MascotBubble"
    when: windowShown
    function init() { root.bubbleText = ""; wait(180) }
    function longText() { return "One two three four five six seven eight nine ten. ".repeat(15) + "LATEST WORDS" }
    function settled() { wait(180) }
    function checkLastVisible() {
      compare(body.y + body.contentHeight, viewport.height)
      verify(viewport.height >= body.lineHeightPx, "viewport must show at least one full line")
    }
    function test_long_text() {
      root.bubbleText = longText(); settled()
      verify(body.lineCount > 5)
      compare(viewport.height, 5 * body.lineHeightPx)
      compare(body.y, viewport.height - body.contentHeight)
      verify(!hint.visible); checkLastVisible()
    }
    function test_hint_pinned() {
      for (var h of root.bubbleHints) {
        root.bubbleText = longText() + "\n" + h; settled()
        compare(hint.text, h); verify(hint.visible); compare(hint.lineCount, 1)
        compare(body.text, longText())
        compare(viewport.height, 4 * body.lineHeightPx)
        compare(hint.y, viewport.height); checkLastVisible()
      }
    }
    function test_shrink_during_animation() {
      root.bubbleText = longText(); settled()
      root.bubbleText += longText(); wait(20)
      root.bubbleText = "Short"; wait(0)
      compare(body.y, 0); verify(body.contentHeight <= viewport.height); checkLastVisible()
    }
    function test_replace_during_animation() {
      root.bubbleText = longText(); settled()
      root.bubbleText += longText(); wait(20)
      root.bubbleText = "Different " + longText(); wait(0)
      compare(body.y, viewport.height - body.contentHeight); checkLastVisible()
    }
    function test_exact_hint_suffix() {
      root.bubbleText = "Press some other key\nordinary speech"; settled()
      compare(root.bubbleBody, root.bubbleText); verify(!hint.visible)
    }
    function test_growth() {
      root.bubbleText = longText(); settled()
      root.bubbleText += "\nMore new words"; wait(0)
      verify(slide.running, "appended lines should slide")
      verify(body.y > body.targetY, "slide should start from the previous position")
      settled()
      checkLastVisible()
    }
  }

"""


@unittest.skipUnless(QT_RUNNER, "requires the Qt QML test runner")
class TestMascotBubble(unittest.TestCase):
    def test_overflow_hints_and_interrupted_slides(self):
        qml = (Path(__file__).resolve().parents[1] / "mascot/shell.qml").read_text()
        # Exercise the production bindings and card, keeping the test independent of
        # Quickshell/Wayland so the same regression checks run in the headless CI job.
        hints = qml[
            qml.index("  readonly property var bubbleHints:") : qml.index("  readonly property bool bubbleMuted:")
        ]
        card = qml[
            qml.index("    Rectangle {\n      id: card") : qml.index("    // the little tail pointing at the mascot")
        ]
        with tempfile.TemporaryDirectory(prefix="auren-bubble-test-") as folder:
            (Path(folder) / "tst_bubble.qml").write_text(SCENE + hints + card + CHECKS + "\n}")
            result = subprocess.run(
                [QT_RUNNER, "-input", folder],
                capture_output=True,
                text=True,
                timeout=20,
                env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software"},
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

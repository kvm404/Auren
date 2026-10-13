"""Exercise actual FileView load ordering in an isolated mascot, when Wayland/Quickshell are available."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not shutil.which("qs") or not os.environ.get("WAYLAND_DISPLAY"),
    reason="requires Quickshell and a Wayland session",
)


@pytest.mark.parametrize("preferences_exist", [True, False])
def test_position_waits_for_preferences_loading_or_failing(tmp_path, preferences_exist):
    source = Path(__file__).resolve().parents[1] / "mascot"
    shell = tmp_path / "shell"
    shutil.copytree(source, shell)
    qml = (shell / "shell.qml").read_text()
    # Hold the preferences read until saved state and the default character have loaded. This
    # deterministically exercises the ordering that a slower preferences read can produce.
    qml = qml.replace("  id: root", "  id: root\n  property bool auditLoadPreferences: false", 1)
    qml = qml.replace(
        '    path: root.configDir + "/mascot.json"',
        '    path: root.auditLoadPreferences ? root.configDir + "/mascot.json" : ""',
        1,
    )
    start = qml.index("  function clicked() {")
    end = qml.index("  // ---- the speech bubble", start)
    qml = qml[:start] + "  function clicked() {}\n\n" + qml[end:]  # never contact the real daemon
    qml = qml.replace('    target: "mascot"', '''    target: "mascot"
    function startupAudit(): string {
      return JSON.stringify({ ready: root.positionReady, placeReady: root.placeReady,
        loadedCharacter: mascotLoader.item !== null, visible: win.visible,
        height: root.viewportHeight, characterHeight: mascot.implicitHeight,
        kind: root.kind, y: root.posY })
    }
    function loadAuditPreferences(y: real): string {
      place.y = y
      root.auditLoadPreferences = true
      return "ok"
    }
''', 1)
    (shell / "shell.qml").write_text(qml)
    env = dict(os.environ)
    for key, folder in (("XDG_CONFIG_HOME", "config"), ("XDG_STATE_HOME", "state"),
                        ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache")):
        env[key] = str(tmp_path / folder)
        (tmp_path / folder / "auren").mkdir(parents=True)
    env["AUREN_RUNTIME_DIR"] = str(tmp_path / "runtime")
    if preferences_exist:
        (tmp_path / "config/auren/mascot.json").write_text(json.dumps({"mascot": "veyra", "bubble": False}))
    state_file = tmp_path / "state/auren/mascot.json"
    state_file.write_text(json.dumps({"x": 0, "y": 0, "size": 1, "hidden": False}))

    with (tmp_path / "qs.log").open("w") as log:
        proc = subprocess.Popen(["qs", "-p", str(shell)], env=env, stdout=log, stderr=log)
        try:
            def call(name, *args):
                return subprocess.check_output(
                    ["qs", "-p", str(shell), "ipc", "call", "--", "mascot", name, *map(str, args)],
                    text=True, stderr=subprocess.DEVNULL, timeout=3,
                )

            def audit():
                return json.loads(call("startupAudit"))

            def wait_for(predicate):
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    assert proc.poll() is None, (tmp_path / "qs.log").read_text()
                    try:
                        value = predicate()
                    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
                        value = False
                    if value:
                        return value
                    time.sleep(0.02)
                pytest.fail("mascot startup timed out: " + (tmp_path / "qs.log").read_text())

            before = wait_for(lambda: (s if (s := audit())["placeReady"] and s["loadedCharacter"] and s["height"] else False))
            time.sleep(0.05)  # let pending callLater restoration attempts run
            assert not audit()["ready"] and not audit()["visible"]
            desired_y = before["height"] - 108  # Veyra's bottom-edge position at size 1
            call("loadAuditPreferences", desired_y)
            after = wait_for(lambda: (s if (s := audit())["ready"] and s["visible"] else False))
            assert after["kind"] == ("veyra" if preferences_exist else "auren")
            expected_y = min(desired_y, after["height"] - after["characterHeight"])
            assert after["y"] == expected_y
            wait_for(lambda: json.loads(state_file.read_text())["y"] == expected_y)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)

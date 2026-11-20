"""A smoke test for the macOS port, run on a macOS runner in CI (python tests/mac_smoke.py).

It imports every module (so each Linux module's switch to its mac/ counterpart runs, with the Mac
dependencies), checks the switches took, builds Auren.app and runs Python through its launcher, and starts
the mascot's host for a few seconds to see mac.qml, mac-setup.qml and mac-window.qml load, then opens every
page of the Auren window (and the setup window) offscreen with the real bridges. All in throwaway folders.
Nothing needs a permission: no microphone, keys or screen.
"""

import importlib
import os
import pkgutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).parents[1]


def imports() -> None:
    import auren
    for m in pkgutil.walk_packages(auren.__path__, "auren."):
        importlib.import_module(m.name)
    from auren.hands import browsers, clipboard, desktop, obu, screen, spotify
    for mod in (browsers, clipboard, desktop, obu, screen, spotify):
        mac = importlib.import_module(f"auren.mac.{mod.__name__.rsplit('.', 1)[1]}")
        for name in mac.__all__:
            assert getattr(mod, name) is getattr(mac, name), f"{mod.__name__}.{name} isn't the Mac one"
    assert screen.MOD == "cmd"
    print("imports: ok")


def _env(tmp: str) -> dict:
    return {**os.environ, "HOME": tmp, "XDG_CONFIG_HOME": f"{tmp}/config", "XDG_DATA_HOME": f"{tmp}/data",
            "XDG_STATE_HOME": f"{tmp}/state", "XDG_CACHE_HOME": f"{tmp}/cache"}


def app() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env = _env(tmp)
        run = lambda *cmd, **kw: subprocess.run(cmd, env=env, capture_output=True, text=True, **kw)  # noqa: E731
        p = run(sys.executable, "-m", "auren.cli", "app", "install")
        assert p.returncode == 0 and "built" in p.stdout, p.stdout + p.stderr
        bundle = Path(tmp) / "Applications/Auren.app"
        p = run("codesign", "--verify", "--strict", "--verbose=2", str(bundle))
        assert p.returncode == 0, p.stderr
        p = run(sys.executable, "-m", "auren.cli", "app", "install")
        assert "up to date" in p.stdout, p.stdout          # unchanged: not rebuilt, the permissions stay
        # The launcher runs Python with what it was given, and tells it the app's path
        p = subprocess.run([str(bundle / "Contents/MacOS/Auren"), "app", "status"], capture_output=True, text=True,
                           env={**env, "AUREN_PYTHON": sys.executable}, timeout=60)
        assert p.returncode == 0 and "up to date" in p.stdout, p.stdout + p.stderr
        p = subprocess.run([str(bundle / "Contents/MacOS/Auren"), "nonsense-command"], capture_output=True,
                           text=True, env={**env, "AUREN_PYTHON": sys.executable}, timeout=60)
        assert p.returncode == 2, (p.returncode, p.stderr)      # argparse's exit code comes back through it
    print("app: ok")


def mascot() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env = {**_env(tmp), "AUREN_MASCOT_DIR": str(ROOT / "mascot"), "QT_QPA_PLATFORM": "offscreen"}
        # As `auren open` asks: the setup window loads (whatever the first run decides)
        p = subprocess.run([sys.executable, "-c", "from auren.mac.app import SETUP_REQUEST as r; print(r)"],
                           env=env, capture_output=True, text=True, check=True)
        request = Path(p.stdout.strip())
        request.parent.mkdir(parents=True, exist_ok=True)
        request.write_text(f"{time.time():.0f}\n")
        p = subprocess.Popen([sys.executable, "-m", "auren.cli", "mascot"], env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
        time.sleep(6)
        alive = p.poll() is None
        p.kill()
        out = p.communicate()[0]
        print(out)
        assert alive, f"the mascot's host exited ({p.returncode})"
        assert "couldn't load" not in out and ".qml:" not in out, out      # QML warnings name file:line
    print("mascot: ok")


# Every page of the Auren window, then the setup window, with the real bridges (no daemon: the setup check
# says it isn't running). Fails on any QML warning: they name file:line.
PAGES = r"""
import sys, time
from pathlib import Path
from PySide6.QtCore import QtMsgType, QUrl, qInstallMessageHandler
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication
problems = []
qInstallMessageHandler(lambda kind, ctx, msg: kind != QtMsgType.QtDebugMsg and ".qml" in f"{ctx.file}{msg}"
                       and problems.append(f"{ctx.file}:{ctx.line}: {msg}"))
app = QApplication([])
from auren.mac import mascot, phone_window, settings_window
bridge = mascot.make_bridge()
phone_ui = phone_window.bridge(lambda: bridge._alive)
window_ui = settings_window.bridge(bridge, phone_ui, lambda command: None, bridge.write_prefs)
engine = QQmlApplicationEngine()
for name, obj in (("auren", bridge), ("phone", phone_ui), ("settings", window_ui)):
    engine.rootContext().setContextProperty(name, obj)
phone_ui.setParent(engine)
window_ui.setParent(engine)
root = Path(sys.argv[1])
for qml in ("mac-window.qml", "mac-setup.qml"):
    engine.load(QUrl.fromLocalFile(str(root / qml)))
assert len(engine.rootObjects()) == 2, "a window didn't load"
def settle(secs):
    end = time.time() + secs
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)
for page in settings_window.PAGES:
    window_ui.openPage(page)
    settle(0.8)
    assert window_ui.page == page
bridge.openSetup()
settle(1.5)
window_ui.close()
bridge.closeSetup()
phone_ui.stop()
settle(0.3)
print("\n".join(problems) or "no QML warnings")
sys.exit(1 if problems else 0)
"""


def window() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env = {**_env(tmp), "QT_QPA_PLATFORM": "offscreen"}
        p = subprocess.run([sys.executable, "-c", PAGES, str(ROOT / "mascot")], env=env, capture_output=True,
                           text=True, timeout=120)
        print(p.stdout + p.stderr)
        assert p.returncode == 0, "the Auren window's pages have QML warnings"
    print("window: ok")


if __name__ == "__main__":
    assert sys.platform == "darwin", "this checks the macOS port: run it on a Mac"
    imports()
    app()
    mascot()
    window()

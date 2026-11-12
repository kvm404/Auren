"""A smoke test for the macOS port, run on a macOS runner in CI (python tests/mac_smoke.py).

It imports every module (so each Linux module's switch to its mac/ counterpart runs, with the Mac
dependencies), checks the switches took, and starts the mascot's host for a few seconds in throwaway
folders to see mac.qml load. Nothing needs a permission: no microphone, keys or screen.
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


def mascot() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "HOME": tmp, "XDG_CONFIG_HOME": f"{tmp}/config", "XDG_DATA_HOME": f"{tmp}/data",
               "XDG_STATE_HOME": f"{tmp}/state", "XDG_CACHE_HOME": f"{tmp}/cache",
               "AUREN_MASCOT_DIR": str(ROOT / "mascot"), "QT_QPA_PLATFORM": "offscreen"}
        p = subprocess.Popen([sys.executable, "-m", "auren.cli", "mascot"], env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
        time.sleep(6)
        alive = p.poll() is None
        p.kill()
        out = p.communicate()[0]
        print(out)
        assert alive, "the mascot's host exited"
        assert "couldn't load" not in out and ".qml:" not in out, out      # QML warnings name file:line
    print("mascot: ok")


if __name__ == "__main__":
    assert sys.platform == "darwin", "this checks the macOS port: run it on a Mac"
    imports()
    mascot()

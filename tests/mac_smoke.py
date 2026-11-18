"""A smoke test for the macOS port, run on a macOS runner in CI (python tests/mac_smoke.py).

It imports every module (so each Linux module's switch to its mac/ counterpart runs, with the Mac
dependencies), checks the switches took, builds Auren.app and runs Python through its launcher, and starts
the mascot's host for a few seconds to see mac.qml and mac-setup.qml load. All in throwaway folders.
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


def selftest() -> None:
    """`auren selftest --quick` with no daemon running: each check fails or passes, and the report is written."""
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.txt"
        p = subprocess.run([sys.executable, "-m", "auren.cli", "selftest", "--quick", "--out", str(report)],
                           env=_env(tmp), capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
        text = report.read_text() if report.exists() else ""
        assert "== Inside Auren" in text and "daemon: not answering" in text, p.stdout + p.stderr
        assert "Traceback" not in p.stdout + p.stderr, p.stdout + p.stderr
    print("selftest: ok")


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


if __name__ == "__main__":
    assert sys.platform == "darwin", "this checks the macOS port: run it on a Mac"
    imports()
    app()
    selftest()
    mascot()

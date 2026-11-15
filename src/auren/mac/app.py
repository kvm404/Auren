"""Auren.app: the Mac app around Auren, in ~/Applications.

    Auren.app/Contents/MacOS/Auren       app/launcher.c, compiled here with the Command Line Tools' clang
    Auren.app/Contents/Info.plist        app/Info.plist: the name, the icon, the permission prompts' text
    Auren.app/Contents/Resources/Auren.icns

The launch agents run Auren through it (`Auren daemon`, `Auren mascot`), so macOS asks for, and lists, the
permissions as Auren's. Opening it from Finder, Spotlight or Launchpad runs `auren open`: Auren starts if
it isn't running, and its setup window comes up.

The bundle is ad-hoc signed. macOS ties a permission to that signature, so `install()` rebuilds only when
the launcher's sources change (a hash of them is kept in DATA_HOME/app.sha256); updating Auren's Python
code never touches the app, and the permissions stay.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from ..config import DATA_HOME, RUNTIME_DIR

APP = Path.home() / "Applications/Auren.app"
SOURCE = Path(__file__).parent / "app"
FILES = ("launcher.c", "Info.plist", "Auren.icns")
BUNDLE_ID = "com.auren.app"
STAMP = DATA_HOME / "app.sha256"
SETUP_REQUEST = RUNTIME_DIR / "setup.request"     # the mascot's process opens the setup window when it appears


class AppError(Exception):
    pass


def executable(app: Path = APP) -> Path:
    return app / "Contents/MacOS/Auren"


def installed(app: Path = APP) -> bool:
    return os.access(executable(app), os.X_OK)


def digest(source: Path = SOURCE) -> str:
    h = hashlib.sha256()
    for name in FILES:
        h.update(name.encode() + b"\0" + (source / name).read_bytes())
    return h.hexdigest()


def current(app: Path = APP, stamp: Path = STAMP) -> bool:
    """The app is there and built from these sources."""
    try:
        return installed(app) and stamp.read_text().strip() == digest()
    except OSError:
        return False


def _run(*cmd: str) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise AppError(f"{cmd[0]} failed: {(p.stderr or p.stdout).strip()[-400:]}")


def build(dest: Path, source: Path = SOURCE) -> None:
    """Make Auren.app at `dest` (which must not exist)."""
    clang = shutil.which("clang")
    if not clang or subprocess.run(["xcode-select", "-p"], capture_output=True).returncode != 0:
        raise AppError("building Auren.app needs Apple's Command Line Tools: run xcode-select --install, then "
                       "./install-macos.sh again")
    macos = dest / "Contents/MacOS"
    resources = dest / "Contents/Resources"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)
    _run(clang, "-O2", "-Wall", "-mmacosx-version-min=14.0", "-arch", "arm64", "-o", str(macos / "Auren"),
         str(source / "launcher.c"))
    shutil.copyfile(source / "Info.plist", dest / "Contents/Info.plist")
    shutil.copyfile(source / "Auren.icns", resources / "Auren.icns")
    (dest / "Contents/PkgInfo").write_text("APPL????")
    _run("codesign", "--force", "--sign", "-", "--identifier", BUNDLE_ID, "--timestamp=none", str(dest))


def install(force: bool = False, app: Path = APP, stamp: Path = STAMP) -> bool:
    """Build Auren.app into ~/Applications if it's missing or its sources changed. True if it was (re)built:
    macOS then sees a new app, and the permissions have to be given again."""
    if not force and current(app, stamp):
        return False
    app.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=app.parent, prefix=".Auren-") as tmp:
        new = Path(tmp) / "Auren.app"
        build(new)
        old = Path(tmp) / "old.app"
        if app.exists():
            app.rename(old)
        new.rename(app)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(digest() + "\n")
    subprocess.run(["/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/"
                    "Support/lsregister", "-f", str(app)], capture_output=True)       # Spotlight and Launchpad
    return True


def uninstall(app: Path = APP, stamp: Path = STAMP) -> None:
    shutil.rmtree(app, ignore_errors=True)
    stamp.unlink(missing_ok=True)


def open_app() -> int:
    """`auren open`, what opening Auren.app does: start Auren if it isn't running, then show its setup."""
    from . import service
    for name in service.LABELS:
        if service.plist_path(name).exists() and not service.running(name):
            service.start(name)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    SETUP_REQUEST.write_text(str(os.getpid()))
    return 0


# --- the icon (for developers: `python -m auren.mac.app icon`, with PySide6) ------------------------------

ICNS_TYPES = ((b"ic10", 1024), (b"ic09", 512), (b"ic14", 512), (b"ic08", 256), (b"ic13", 256), (b"ic07", 128),
              (b"ic12", 64), (b"ic11", 32))


def icns(pngs: dict[int, bytes]) -> bytes:
    """An .icns file from PNGs by pixel size (each type above takes the PNG of its size)."""
    body = b"".join(kind + struct.pack(">I", 8 + len(pngs[px])) + pngs[px] for kind, px in ICNS_TYPES)
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


def make_icon(out: Path = SOURCE / "Auren.icns") -> Path:
    """Render mascot/app-icon.qml and write the .icns."""
    from PySide6.QtCore import Q_ARG, QBuffer, QMetaObject, Qt, QTimer, QUrl
    from PySide6.QtGui import QGuiApplication, QImage
    from PySide6.QtQml import QQmlApplicationEngine

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    engine = QQmlApplicationEngine()
    engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[3] / "mascot/app-icon.qml")))
    if not engine.rootObjects():
        raise AppError("couldn't load mascot/app-icon.qml")
    win = engine.rootObjects()[0]
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "icon.png"
        win.saved.connect(app.quit)
        QTimer.singleShot(1500, lambda: QMetaObject.invokeMethod(win, "save", Q_ARG("QVariant", str(png))))
        app.exec()
        big = QImage(str(png))
    if big.isNull():
        raise AppError("the icon didn't render")
    pngs = {}
    for _, px in ICNS_TYPES:
        if px not in pngs:
            buf = QBuffer()
            buf.open(QBuffer.OpenModeFlag.WriteOnly)
            big.scaled(px, px, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
                       ).save(buf, "PNG")
            pngs[px] = bytes(buf.data())
    out.write_bytes(icns(pngs))
    return out


if __name__ == "__main__":
    if sys.argv[1:] == ["icon"]:
        print(make_icon())
    else:
        print("python -m auren.mac.app icon     render the app icon into src/auren/mac/app/Auren.icns")

"""Baut PopWin: python build.py

Ergebnis in release/: PopWin Setup.exe (eigener Installer) und PopWin Portable.zip.
Einstellungen, API-Keys, Novas Erinnerungen und Tests kommen nie ins Paket.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
# Immer frisch: Ein offener Ordner im Explorer blockiert sonst das Aufräumen eines festen Ausgabeordners.
DIST = Path(tempfile.mkdtemp(prefix="popwin_dist_"))
RELEASE = ROOT / "release"
APP_MODULES = ("explorer.app", "editor.app", "terminal.app", "studio.app", "stream.app", "wallpaper.app", "core.cleanup", "core.taskbar_watchdog",
               "core.nova_tool_runner", "keyring.backends.Windows", "win32timezone", "win32com.propsys.propsys", "win32com.propsys.pscon", "comtypes.stream", "pycaw.pycaw")
# ovh: Damit Werkzeuge, die Nova für jemanden schreibt, OVH ansprechen können. Die fertige Anbindung bleibt in devtools.
COLLECT = ("winsdk", "winpty", "pyte", "wcwidth", "google.genai", "pyvirtualcam", "mss", "sounddevice", "ovh")
EXCLUDE = ("PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick",
           "PySide6.QtQml", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf",
           "PySide6.QtSql", "PySide6.QtTest", "tkinter", "unittest", "pytest", "devtools")
FORBIDDEN = ("settings.json", "editor_session.json", ".env", "popwin.log")


def pyinstaller(*arguments: str) -> None:
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--log-level", "WARN",
                    "--workpath", str(BUILD), "--distpath", str(DIST), "--specpath", str(BUILD), *arguments],
                   cwd=ROOT, check=True)


def build_app() -> Path:
    arguments = ["--windowed", "--name", "PopWin", "--icon", str(ROOT / "assets" / "popwin.ico"),
                 "--add-data", f"{ROOT / 'assets'};assets", "--add-data", f"{ROOT / 'themes'};themes",
                 "--add-data", f"{ROOT / 'CHANGELOG.md'};."]  # für „Was ist neu“ nach einem Update
    for module in APP_MODULES:
        arguments += ["--hidden-import", module]
    for package in COLLECT:
        arguments += ["--collect-all", package]
    for module in EXCLUDE:
        arguments += ["--exclude-module", module]
    pyinstaller(*arguments, str(ROOT / "main.py"))
    return DIST / "PopWin"


def check_clean(folder: Path) -> None:
    """Ins Paket darf nichts Persönliches. Lieber der Build scheitert als ein Key geht mit raus."""
    found = [path for path in folder.rglob("*") if path.name in FORBIDDEN]
    found += [path for name in ("nova", "tests", "devtools", "config/settings.json", "config/nova_artifacts")
              for path in folder.glob(f"_internal/{name}")]
    if found:
        raise SystemExit(f"Gehört nicht ins Paket: {found[:5]}")


def zip_folder(folder: Path, target: Path, prefix: str = "", extra: dict[str, str] | None = None) -> None:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in folder.rglob("*"):
            if path.is_file():
                archive.write(path, str(Path(prefix) / path.relative_to(folder)))
        for name, text in (extra or {}).items():
            archive.writestr(str(Path(prefix) / name), text)


def build_installer(payload: Path) -> Path:
    arguments = ["--windowed", "--onefile", "--name", "PopWin Setup", "--icon", str(ROOT / "assets" / "popwin.ico"),
                 "--add-data", f"{payload};.", "--add-data", f"{ROOT / 'assets' / 'popwin.png'};.",
                 "--add-data", f"{ROOT / 'assets' / 'popwin.ico'};.", "--hidden-import", "win32com.client"]
    for module in EXCLUDE + ("PySide6.QtNetwork", "google", "winsdk", "numpy"):
        arguments += ["--exclude-module", module]
    pyinstaller(*arguments, str(ROOT / "installer" / "setup.py"))
    return DIST / "PopWin Setup.exe"


def check_versions() -> None:
    """App und Setup müssen dieselbe Version tragen, sonst meldet das Update-Popup nach dem Update wieder ein Update."""
    import re
    app = re.search(r'VERSION = "([^"]+)"', (ROOT / "core" / "version.py").read_text(encoding="utf-8")).group(1)
    setup = re.search(r'VERSION = "([^"]+)"', (ROOT / "installer" / "setup.py").read_text(encoding="utf-8")).group(1)
    if app != setup:
        raise SystemExit(f"Versionen passen nicht: core/version.py {app}, installer/setup.py {setup}")


def main() -> None:
    check_versions()
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_icons.py")], check=True)
    RELEASE.mkdir(exist_ok=True)
    BUILD.mkdir(exist_ok=True)
    app = build_app()
    check_clean(app)
    payload = BUILD / "payload.zip"
    zip_folder(app, payload)
    setup = build_installer(payload)
    shutil.copy2(setup, RELEASE / setup.name)
    zip_folder(app, RELEASE / "PopWin Portable.zip", "PopWin", {"portable.txt": "Einstellungen liegen im Ordner data.\n"})
    shutil.rmtree(DIST, ignore_errors=True)
    for path in RELEASE.iterdir():
        print(f"{path.name}: {path.stat().st_size / 1024 ** 2:.0f} MB")


if __name__ == "__main__":
    main()

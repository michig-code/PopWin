"""Baut PopWin: python build.py            (öffentlich, ohne Dev-Teil)
             python build.py --private  (eigene Fassung mit Dev-Menü, nur auf dem Entwickler-PC)

Ergebnis in release/:
- öffentlich: PopWin Setup.exe und PopWin Portable.zip
- privat:     PopWin Dev Setup.exe und PopWin Dev Portable.zip
Einstellungen, API-Keys, Novas Erinnerungen und Tests kommen nie ins Paket, auch nicht in den privaten Build.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RELEASE = ROOT / "release"

REQUIRED_THEMES = (
    "_base.qss",
    "aurora.qss",
    "liquid_glass.qss",
    "pop_basic.qss",
    "clean.qss",
    "minimalist.qss",
    "nova_classic.qss",
)
REQUIRED_ASSETS = (
    "popwin.ico",
    "popwin.png",
    "nova_persona.md",
    "lang/en.json",
    "lang/es.json",
    "lang/fr.json",
)
REQUIRED_APP_ICONS = tuple(
    f"apps/{name}.{ext}"
    for name in ("editor", "explorer", "nova", "settings", "stream", "studio", "terminal", "widgets")
    for ext in ("ico", "png")
)
AURORA_REQUIRED_MARKERS = (
    "/* popwin:",
    '[popMode="light"] #Results::item:selected',
    'QMenu[popMode="light"]::item:selected',
)

APP_MODULES = (
    "explorer.app",
    "editor.app",
    "editor.widget",
    "terminal.app",
    "terminal.widget",
    "studio.app",
    "studio.debugger",
    "studio.formatter",
    "studio.panels",
    "studio.settings",
    "stream.app",
    "wallpaper.app",
    "core.cleanup",
    "core.paths",
    "core.taskbar_watchdog",
    "core.theme_manager",
    "core.win_api",
    "core.nova_chess",
    "ui.animation",
    "ui.window_frame",
    "ui.settings_window",
    "ui.widgets.base",
    "ui.widgets.gallery",
    "ui.widgets.notes",
    "extensions.discord",
    "extensions.tts_for_windows",
    "tts.app",
    "tts.audio",
    "core.nova_tool_runner",
    "shiboken6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtNetwork",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "keyring",
    "keyring.backends",
    "keyring.backends.Windows",
    "psutil",
    "pywintypes",
    "pythoncom",
    "win32api",
    "win32clipboard",
    "win32gui",
    "win32pdh",
    "win32timezone",
    "win32com.client",
    "win32com.shell",
    "win32com.shell.shell",
    "win32com.shell.shellcon",
    "win32com.propsys",
    "win32com.propsys.propsys",
    "win32com.propsys.pscon",
    "comtypes",
    "comtypes.client",
    "comtypes.stream",
    "pycaw.pycaw",
    "pycaw.constants",
    "chess",
    "numpy",
)
# ovh: Damit Werkzeuge, die Nova für jemanden schreibt, OVH ansprechen können.
COLLECT = ("winsdk", "winpty", "pyte", "wcwidth", "google.genai", "pyvirtualcam", "mss", "sounddevice", "ovh")
OPTIONAL_COLLECT = ("_sounddevice_data",)
INSTALLER_MODULES = (
    "shiboken6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "psutil",
    "pythoncom",
    "pywintypes",
    "win32com.client",
    "win32com.shell",
    "win32com.shell.shell",
    "win32com.shell.shellcon",
)
EXCLUDE = (
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.Qt3DCore",
    "PySide6.QtQuick",
    "PySide6.QtQml",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtPdf",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "tkinter",
    "unittest",
    "pytest",
    "devtools",
    "_private",
    "_private.devmenu",
    "PySide6.QtWebEngine",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebView",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "pywebview",
    "webview",
    "tests",
    "tools",
    "nova",
    "nova.copilot_pro",
)
INSTALLER_EXCLUDE = EXCLUDE + (
    "PySide6.QtNetwork",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "google",
    "winsdk",
    "numpy",
    "sounddevice",
    "mss",
    "pyvirtualcam",
    "chess",
    "winpty",
    "pyte",
    "pycaw",
    "keyring",
)

FORBIDDEN = (
    "settings.json",
    "settings.session.json",
    "settings.broken.json",
    "editor_session.json",
    "explorer.json",
    "network_stats.json",
    "studio_breakpoints.json",
    "studio_commands.json",
    "studio_session.json",
    "studio_settings.json",
    "studio_state.json",
    "studio_check.ps1",
    "studio_host.ps1",
    "chat.json",
    "memory.json",
    "pin.json",
    ".env",
    "popwin.log",
    "popwin.log.1",
    "popwin.pid",
    "portable.txt",
    "Thumbs.db",
    ".DS_Store",
)
FORBIDDEN_SUFFIXES = (".log", ".pid", ".tmp", ".broken.json")
FORBIDDEN_LOG_BACKUP_RE = re.compile(r"\.log\.\d+$", re.IGNORECASE)
FORBIDDEN_WEB_MARKERS = ("qtwebengine", "qt6webengine", "qtwebview", "qt6webview", "webview2", "pywebview")
FORBIDDEN_DIRS_ANYWHERE = (
    "copilot_pro",
    "ebwebview",
    "pywebview",
    "webview",
    "nova_artifacts",
    "editor_closed",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
)
FORBIDDEN_ROOT_OR_INTERNAL_DIRS = ("nova", "tests", "devtools", "_private", "tools", "docs", "installer", "build", "release")
FORBIDDEN_CONFIG_SUFFIXES = (".json", ".ps1", ".txt", ".env", ".log", ".tmp", ".pid")
FORBIDDEN_ASSET_SUFFIXES = (".html", ".htm", ".js", ".pyc", ".pyo")

# Privater Dev-Teil (nie eingecheckt, nie öffentlich gebaut)
PRIVATE_DIR = ROOT / "_private" / "devmenu"
PRIVATE_LANG_LIST = PRIVATE_DIR / "lang" / "SCHLUESSEL.txt"  # eine Zeile je Dev-Schlüssel
PRIVATE_MARKER = "privater-build.txt"  # nur der private Build legt diese Datei ins Paket, siehe core/dev.py
PRIVATE_NAME = "PopWin Dev"
# Aus dem Dev-Ordner landen nur Daten im Paket: Sprachen, Stil, Bilder. Code kommt über die Modulliste, PIN und Notizen nie.
PRIVATE_STAGE_SKIP_NAMES = ("__pycache__", "pin.json")
PRIVATE_STAGE_SKIP_SUFFIXES = (".py", ".pyc", ".md", ".txt", ".bak")
# Dev-Dateinamen, die im öffentlichen Paket nie vorkommen dürfen
DEV_FILE_NAMES = ("dev.qss", "SCHLUESSEL.txt", "next_changes.md", "prompt_nova_artefakte.md", PRIVATE_MARKER)
# Die einzigen Dateien, die den Dev-Teil ansprechen dürfen (Einhängepunkte, keine Oberfläche)
DEV_HOOK_FILES = ("core/dev.py", "core/nova_tools.py", "core/nova_controller.py")


def pyinstaller(*arguments: str, dist: Path) -> None:
    """Führt PyInstaller mit konsistenten Arbeits- und Ausgabeordnern aus."""
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--log-level",
            "WARN",
            "--workpath",
            str(dist / "_work"),
            "--distpath",
            str(dist),
            "--specpath",
            str(dist / "_work"),
            *arguments,
        ],
        cwd=ROOT,
        check=True,
    )


def stage_private_dev(dist: Path) -> Path:
    """Kopiert nur die Daten des Dev-Teils in einen Zwischenordner und setzt die Build-Markierung.

    Der Ordner wird nie direkt eingepackt: PIN (pin.json), Notizen und Quelltext bleiben draußen.
    Der Code selbst kommt über die Modulliste (--collect-submodules _private) ins Bundle.
    """
    if not (PRIVATE_DIR / "__init__.py").is_file():
        raise SystemExit(f"Der private Dev-Ordner fehlt: {PRIVATE_DIR}. --private geht nur auf dem Entwickler-PC.")

    def ignore(_directory: str, names: list[str]) -> list[str]:
        return [
            name
            for name in names
            if name in PRIVATE_STAGE_SKIP_NAMES or Path(name).suffix.lower() in PRIVATE_STAGE_SKIP_SUFFIXES
        ]

    stage = dist / "_stage" / "_private"
    target = stage / "devmenu"
    shutil.copytree(PRIVATE_DIR, target, ignore=ignore)
    (target / PRIVATE_MARKER).write_text("Dieser Build enthält den privaten Dev-Teil. Nicht veröffentlichen.\n", encoding="utf-8")
    return stage


def build_app(dist: Path, private: bool = False) -> Path:
    """Erstellt das PopWin-App-Bundle im temporären Dist-Ordner."""
    arguments = [
        "--windowed",
        "--name",
        "PopWin",
        "--icon",
        str(ROOT / "assets" / "popwin.ico"),
        "--add-data",
        f"{ROOT / 'assets'};assets",
        "--add-data",
        f"{ROOT / 'themes'};themes",
        "--add-data",
        f"{ROOT / 'CHANGELOG.md'};.",  # für „Was ist neu“ nach einem Update
    ]
    if private:
        stage = stage_private_dev(dist)
        arguments += ["--add-data", f"{stage};_private", "--collect-submodules", "_private"]
    for module in APP_MODULES:
        arguments += ["--hidden-import", module]
    for package in COLLECT:
        arguments += ["--collect-all", package]
    for package in OPTIONAL_COLLECT:
        if importlib.util.find_spec(package) is not None:
            arguments += ["--collect-all", package]
    for module in EXCLUDE:
        if private and module.startswith("_private"):
            continue  # im privaten Build gehört der Dev-Teil ins Bundle
        arguments += ["--exclude-module", module]
    pyinstaller(*arguments, str(ROOT / "main.py"), dist=dist)
    return dist / "PopWin"


def _read_version(path: Path) -> str:
    """Liest die VERSION-Konstante aus einer Quelldatei oder bricht verständlich ab."""
    if not path.is_file():
        raise SystemExit(f"Versionsdatei fehlt: {path}")
    match = re.search(r'^VERSION = "([^"]+)"', path.read_text(encoding="utf-8"), re.M)
    if match is None:
        raise SystemExit(f"VERSION-Eintrag fehlt in {path}")
    return match.group(1)


def check_versions() -> None:
    """App und Setup müssen dieselbe Version tragen, sonst meldet das Update-Popup nach dem Update wieder ein Update."""
    app = _read_version(ROOT / "core" / "version.py")
    setup = _read_version(ROOT / "installer" / "setup.py")
    if app != setup:
        raise SystemExit(f"Versionen passen nicht: core/version.py {app}, installer/setup.py {setup}")


def _check_required_files(base_dir: Path, relative_paths: tuple[str, ...], label: str) -> None:
    """Prüft, dass das Basisverzeichnis sowie alle geforderten nicht-leeren Dateien existieren."""
    if not base_dir.is_dir():
        raise SystemExit(f"Pflichtordner fehlt ({label}): {base_dir}")
    for rel in relative_paths:
        target = base_dir / rel
        if not target.is_file() or target.stat().st_size <= 0:
            raise SystemExit(f"Pflichtdatei fehlt oder ist leer ({label}): {target}")


def _check_theme_markers(
    themes_dir: Path,
    requirements: tuple[tuple[str, tuple[str, ...]], ...],
    label: str,
) -> None:
    """Prüft, dass definierte Theme-Dateien alle geforderten Marker enthalten."""
    for theme_name, markers in requirements:
        theme_text = (themes_dir / theme_name).read_text(encoding="utf-8")
        for marker in markers:
            if marker not in theme_text:
                raise SystemExit(f"{label} {theme_name} unvollständig, Pflichtmarker fehlt: {marker}")


def check_no_dev_keys(assets_dir: Path) -> None:
    """Öffentliche Sprachdateien tragen keinen Dev-Schlüssel. Die Liste liegt nur im privaten Ordner."""
    if not PRIVATE_LANG_LIST.is_file():
        # Fail-closed (F-5): ohne Liste gäbe es keine Prüfung, also wird nicht gebaut.
        raise SystemExit(f"Dev-Schlüsselliste fehlt ({PRIVATE_LANG_LIST}). Ohne sie kann der öffentliche Build "
                         "nicht prüfen, ob Dev-Texte in den Sprachdateien stehen. Abbruch.")
    dev_keys = {line.strip() for line in PRIVATE_LANG_LIST.read_text(encoding="utf-8").splitlines() if line.strip()}
    leaks = []
    for lang_file in sorted((assets_dir / "lang").glob("*.json")):
        table = json.loads(lang_file.read_text(encoding="utf-8"))
        hits = sorted(key for key in table if key in dev_keys)
        if hits:
            leaks.append(f"{lang_file.name} ({len(hits)} Schlüssel, z. B. {hits[0]!r})")
    if leaks:
        raise SystemExit(f"Dev-Sprachschlüssel in öffentlichen Sprachdateien gefunden: {', '.join(leaks)}")


def check_source_dev_hooks() -> None:
    """Im öffentlichen Code zeigt nur der Einhängepunkt auf den Dev-Teil. Jeder weitere Verweis bricht den Build ab."""
    allowed = {(ROOT / name).resolve() for name in DEV_HOOK_FILES} | {Path(__file__).resolve()}
    skipped = {"_private", "tests", "build", "release", "venv", ".venv", "__pycache__"}
    pattern = re.compile(r"\b(?:_private|devtools)\b")
    found: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if any(part in skipped for part in rel.parts) or path.resolve() in allowed:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if pattern.search(line):
                found.append(f"{rel.as_posix()}:{number}")
    if found:
        raise SystemExit(f"Dev-Verweis außerhalb der Einhängepunkte: {', '.join(found[:10])}")


def check_prerequisites(private: bool = False) -> None:
    """Prüft vor dem Build alle nötigen Skripte, Themes, Assets und Web-Ausschlüsse."""
    if importlib.util.find_spec("PyInstaller") is None:
        raise SystemExit("PyInstaller fehlt. Bitte installieren mit: python -m pip install -r requirements.txt")

    for script in (ROOT / "main.py", ROOT / "installer" / "setup.py", ROOT / "tools" / "make_icons.py"):
        if not script.is_file():
            raise SystemExit(f"Erforderliche Build-Datei fehlt: {script}")

    themes_dir = ROOT / "themes"
    assets_dir = ROOT / "assets"
    _check_required_files(themes_dir, REQUIRED_THEMES, "Theme")
    _check_required_files(assets_dir, REQUIRED_ASSETS, "Asset")
    _check_theme_markers(
        themes_dir,
        (("aurora.qss", AURORA_REQUIRED_MARKERS), ("liquid_glass.qss", AURORA_REQUIRED_MARKERS)),
        "Theme",
    )

    for folder in (themes_dir, assets_dir):
        for path in folder.rglob("*"):
            if path.is_file() and path.suffix.lower() in (".html", ".htm", ".js"):
                raise SystemExit(f"Unerlaubte Web-Datei unter {folder.name}/ gefunden: {path}")

    if private:
        if not (PRIVATE_DIR / "__init__.py").is_file():
            raise SystemExit(f"Der private Dev-Ordner fehlt: {PRIVATE_DIR}")
    else:
        check_no_dev_keys(assets_dir)
        check_source_dev_hooks()


def check_clean(folder: Path, private: bool = False) -> None:
    """Ins Paket darf nichts Persönliches oder Überflüssiges. Lieber der Build scheitert als ein Key geht mit raus.

    Der öffentliche Build lässt den Dev-Teil gar nicht zu. Der private Build darf nur den Dev-Ordner enthalten, PIN
    und Quelltext bleiben trotzdem draußen.
    """
    forbidden_names = {name.lower() for name in FORBIDDEN}
    forbidden_dirs = {name.lower() for name in FORBIDDEN_DIRS_ANYWHERE}
    forbidden_root_dirs = {name.lower() for name in FORBIDDEN_ROOT_OR_INTERNAL_DIRS}
    if private:
        forbidden_root_dirs.discard("_private")
    else:
        forbidden_names |= {name.lower() for name in DEV_FILE_NAMES}
    found: list[Path] = []

    for path in folder.rglob("*"):
        name_lower = path.name.lower()
        if any(marker in name_lower for marker in FORBIDDEN_WEB_MARKERS):
            found.append(path)
            continue
        if path.is_file():
            if (
                name_lower in forbidden_names
                or name_lower.startswith(".env.")
                or name_lower.startswith("settings.locked-")
                or any(name_lower.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES)
                or FORBIDDEN_LOG_BACKUP_RE.search(name_lower)
            ):
                found.append(path)
        elif path.is_dir() and name_lower in forbidden_dirs:
            found.append(path)

    for base in (folder, folder / "_internal"):
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            if entry.is_dir() and entry.name.lower() in forbidden_root_dirs:
                found.append(entry)

        config_dir = base / "config"
        if config_dir.is_dir():
            for entry in config_dir.rglob("*"):
                entry_lower = entry.name.lower()
                if entry.is_file() and (
                    entry_lower == ".env"
                    or entry_lower.startswith(".env.")
                    or entry.suffix.lower() in FORBIDDEN_CONFIG_SUFFIXES
                ):
                    found.append(entry)
                elif entry.is_dir() and entry_lower in ("nova_artifacts", "editor_closed", "backups", "extensions", "presets", "__pycache__"):
                    found.append(entry)

        for asset_root_name in ("themes", "assets"):
            asset_root = base / asset_root_name
            if not asset_root.is_dir():
                continue
            for entry in asset_root.rglob("*"):
                if entry.is_dir() and entry.name.lower() == "__pycache__":
                    found.append(entry)
                elif entry.is_file() and entry.suffix.lower() in FORBIDDEN_ASSET_SUFFIXES:
                    found.append(entry)

    unique_found = sorted(set(found), key=lambda p: p.relative_to(folder).as_posix().lower())
    if unique_found:
        rel = [str(p.relative_to(folder)) for p in unique_found[:15]]
        raise SystemExit(f"Gehört nicht ins Paket: {rel}")


def _bundled_dir(folder: Path, name: str) -> Path:
    """Ermittelt ein eingebettetes Ressourcenverzeichnis bevorzugt unter _internal/."""
    internal = folder / "_internal" / name
    return internal if internal.is_dir() else folder / name


def check_bundle(folder: Path) -> None:
    """Prüft nach dem Build, dass PopWin.exe sowie alle Pflicht-Themes und Pflicht-Assets im Paket liegen."""
    exe = folder / "PopWin.exe"
    if not exe.is_file() or exe.stat().st_size <= 0:
        raise SystemExit(f"PopWin.exe fehlt oder ist leer: {exe}")
    bundled_themes = _bundled_dir(folder, "themes")
    bundled_assets = _bundled_dir(folder, "assets")
    _check_required_files(bundled_themes, REQUIRED_THEMES, "Bundle-Theme")
    _check_theme_markers(
        bundled_themes,
        (("aurora.qss", AURORA_REQUIRED_MARKERS), ("liquid_glass.qss", ("/* popwin:",))),
        "Bundle-Theme",
    )
    _check_required_files(bundled_assets, REQUIRED_ASSETS, "Bundle-Asset")
    _check_required_files(bundled_assets, REQUIRED_APP_ICONS, "Bundle-App-Icon")


def zip_folder(folder: Path, target: Path, prefix: str = "", extra: dict[str, str] | None = None) -> None:
    """Packt einen Ordner samt optionaler Zusatzdateien sauber mit POSIX-Pfaden ins ZIP-Archiv."""
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                rel = path.relative_to(folder)
                arcname = (Path(prefix) / rel).as_posix() if prefix else rel.as_posix()
                archive.write(path, arcname)
        for name, text in (extra or {}).items():
            arcname = (Path(prefix) / name).as_posix() if prefix else Path(name).as_posix()
            archive.writestr(arcname, text)


def build_installer(payload: Path, dist: Path, name: str = "PopWin Setup") -> Path:
    """Erstellt die eigenständige Setup-EXE (name: PopWin Setup oder PopWin Dev Setup) aus dem App-Payload."""
    arguments = [
        "--windowed",
        "--onefile",
        "--name",
        name,
        "--icon",
        str(ROOT / "assets" / "popwin.ico"),
        "--add-data",
        f"{payload};.",
        "--add-data",
        f"{ROOT / 'assets' / 'popwin.png'};.",
        "--add-data",
        f"{ROOT / 'assets' / 'popwin.ico'};.",
    ]
    for module in INSTALLER_MODULES:
        arguments += ["--hidden-import", module]
    for module in INSTALLER_EXCLUDE:
        arguments += ["--exclude-module", module]
    pyinstaller(*arguments, str(ROOT / "installer" / "setup.py"), dist=dist)
    return dist / f"{name}.exe"


def _copy_to_release(src: Path, dst: Path) -> None:
    """Kopiert ein Build-Artefakt atomar nach release/ und fängt temporäre OneDrive-Sperren ab."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=dst.parent,
            prefix=f".{dst.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
            with src.open("rb") as src_file:
                shutil.copyfileobj(src_file, tmp_file)
        try:
            shutil.copystat(src, tmp_path)
        except OSError:
            pass
        for attempt in range(5):
            try:
                os.replace(tmp_path, dst)
                return
            except (PermissionError, OSError):
                if attempt == 4:
                    raise
                time.sleep(0.2 * (attempt + 1))
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def main() -> None:
    unknown = set(sys.argv[1:]) - {"--private"}
    if unknown:
        raise SystemExit(f"Unbekannte Option: {', '.join(sorted(unknown))}. Erlaubt: --private")
    private = "--private" in sys.argv[1:]
    prefix = PRIVATE_NAME if private else "PopWin"  # Dateinamen und Ordner im Portable-Paket unterscheiden die Fassungen
    check_versions()
    check_prerequisites(private)
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_icons.py")], cwd=ROOT, check=True)
    RELEASE.mkdir(exist_ok=True)
    # Immer frisch: Ein offener Ordner im Explorer blockiert sonst das Aufräumen eines festen Ausgabeordners.
    # Auch die PyInstaller-Zwischendateien liegen dort und nicht im Projekt: In OneDrive würden hunderte MB
    # synchronisiert, und eine kurze Sperre durch den Sync ließe den Build mittendrin scheitern.
    dist = Path(tempfile.mkdtemp(prefix="popwin_dist_"))
    try:
        app = build_app(dist, private)
        check_clean(app, private)
        check_bundle(app)
        payload = dist / "payload.zip"
        zip_folder(app, payload)
        setup = build_installer(payload, dist, f"{prefix} Setup")
        portable_tmp = dist / f"{prefix} Portable.zip"
        zip_folder(
            app,
            portable_tmp,
            prefix,
            {"portable.txt": "Einstellungen liegen im Ordner data.\n"},
        )
        _copy_to_release(setup, RELEASE / setup.name)
        _copy_to_release(portable_tmp, RELEASE / portable_tmp.name)
    finally:
        shutil.rmtree(dist, ignore_errors=True)
    for path in sorted(RELEASE.iterdir()):
        if path.is_file():
            print(f"{path.name}: {path.stat().st_size / 1024 ** 2:.0f} MB")


if __name__ == "__main__":
    main()

"""Liest die in der Windows-Taskleiste angehefteten Apps."""
from __future__ import annotations

import logging
import os
import re
import winreg
from pathlib import Path

log = logging.getLogger(__name__)

_TASKBAND = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Taskband"
_STORE_APP = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]{2,}_[a-z0-9]{13}![A-Za-z0-9.\-_]+")
APPS_FOLDER = "shell:::{4234d49b-0245-4df3-b780-3893943456e1}"
UNPIN_NAMES = ("von taskleiste lösen", "unpin from taskbar")


def pinned_folder() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft/Internet Explorer/Quick Launch/User Pinned/TaskBar"


def favorites_text() -> str:
    """Die Reihenfolge steht in einem Binärwert; je nach Lage der Texte sind beide Ausrichtungen nötig."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _TASKBAND) as key:
            blob = winreg.QueryValueEx(key, "Favorites")[0]
    except OSError as exc:
        log.info("Keine Taskband-Daten: %s", exc)
        return ""
    return blob.decode("utf-16-le", "ignore") + blob[1:].decode("utf-16-le", "ignore")


def store_apps(text: str) -> list[str]:
    """App-IDs angehefteter Store-Apps, zum Beispiel SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify."""
    return list(dict.fromkeys(_STORE_APP.findall(text)))


def store_pin(app_id: str) -> dict:
    package = app_id.split("_", 1)[0].rsplit(".", 1)[-1]
    name = re.sub(r"^(?:Spotify)Music$", "Spotify", package)
    return {"name": name, "target": f"shell:AppsFolder\\{app_id}", "exe": app_id.rsplit("!", 1)[-1].lower() + ".exe"}


def ordered_shortcuts(names: list[str], text: str) -> list[str]:
    """Sortiert wie in Windows; was im Binärwert fehlt, kommt ans Ende."""
    position = {name: text.find(name) for name in names}
    return sorted(names, key=lambda name: (position[name] < 0, position[name], name.lower()))


def pinned_paths() -> tuple[list[str], list[str]]:
    """(Verknüpfungen in Windows-Reihenfolge, Store-App-IDs)"""
    text = favorites_text()
    folder = pinned_folder()
    names = [p.name for p in folder.glob("*.lnk")] if folder.is_dir() else []
    return [str(folder / name) for name in ordered_shortcuts(names, text)], store_apps(text)


def unpin_verb(verbs):
    """Der Befehl hängt an der Sprache von Windows; einen festen Namen gibt es nicht."""
    for verb in verbs:
        name = str(verb.Name).replace("&", "").lower()
        if name in UNPIN_NAMES:
            return verb
    return None


def unpin(target: str) -> bool:
    """Löst eine App wie der Rechtsklick in Windows. Anheften lässt Windows nur den Explorer selbst."""
    import pywintypes
    from win32com.client import Dispatch
    shell = Dispatch("Shell.Application")
    try:
        if target.lower().endswith(".lnk"):
            path = Path(target)
            item = shell.NameSpace(str(path.parent)).ParseName(path.name)
        else:
            item = shell.NameSpace(APPS_FOLDER).ParseName(target.split("\\", 1)[1])
        verb = unpin_verb(item.Verbs())
        if verb is None:
            return False
        verb.DoIt()
    except (AttributeError, pywintypes.com_error) as exc:
        log.warning("Lösen von %s fehlgeschlagen: %s", target, exc)
        return False
    return True

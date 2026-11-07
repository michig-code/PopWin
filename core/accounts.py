"""Benutzerkonten für den Benutzer-Bereich der Topbar: wer ist angemeldet, wer gibt es noch, Profilbild."""
from __future__ import annotations

import ctypes
import getpass
import logging
import os
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)

UF_ACCOUNTDISABLE = 0x0002
UF_NORMAL_ACCOUNT = 0x0200
# Konten, die Windows selbst anlegt und die niemand zum Anmelden nutzt
HIDDEN_ACCOUNTS = {"defaultaccount", "wdagutilityaccount", "guest", "gast", "administrator", "defaultuser0"}
NAME_DISPLAY = 3  # GetUserNameExW: der volle Name, bei Microsoft-Konten etwa „Michael G.“


@dataclass(frozen=True)
class Account:
    name: str  # Anmeldename
    full_name: str = ""

    @property
    def label(self) -> str:
        return self.full_name or self.name


def login_name() -> str:
    try:
        return os.getlogin()
    except OSError:
        return getpass.getuser()


def display_name() -> str:
    """Voller Name des angemeldeten Benutzers; ohne einen den Anmeldenamen."""
    try:
        size = ctypes.c_ulong(256)
        buffer = ctypes.create_unicode_buffer(size.value)
        if ctypes.WinDLL("secur32").GetUserNameExW(NAME_DISPLAY, buffer, ctypes.byref(size)) and buffer.value.strip():
            return buffer.value.strip()
    except (OSError, AttributeError):
        pass
    return login_name()


def initials(name: str) -> str:
    """Ein oder zwei Buchstaben für das runde Profilbild ohne Foto."""
    parts = [part for part in name.replace(".", " ").replace("_", " ").split() if part[:1].isalnum()]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][0].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def visible_accounts(entries: list[dict]) -> list[Account]:
    """Aus der Liste von NetUserEnum nur echte, eingeschaltete Konten, alphabetisch."""
    accounts = []
    for entry in entries:
        name = str(entry.get("name", ""))
        flags = int(entry.get("flags", 0))
        if not name or name.lower() in HIDDEN_ACCOUNTS or name.endswith("$"):
            continue
        if flags & UF_ACCOUNTDISABLE or not flags & UF_NORMAL_ACCOUNT:
            continue
        accounts.append(Account(name, str(entry.get("full_name", "")).strip()))
    return sorted(accounts, key=lambda account: account.label.lower())


def local_accounts() -> list[Account]:
    try:
        import win32net
        entries, _total, _resume = win32net.NetUserEnum(None, 2)
        return visible_accounts(entries)
    except Exception as exc:  # ohne Rechte oder ohne pywin32 bleibt nur der eigene Benutzer
        log.debug("Benutzer nicht lesbar: %s", exc)
        return [Account(login_name(), display_name())]


def account_picture() -> str:
    """Pfad zum Profilbild aus den Windows-Einstellungen, leer ohne Bild."""
    try:
        import winreg
        import win32api
        import win32security
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
        sid = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
        key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\AccountPicture\Users\{sid}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as pictures:
            for size in ("Image192", "Image208", "Image240", "Image96", "Image448"):
                try:
                    path = winreg.QueryValueEx(pictures, size)[0]
                except OSError:
                    continue
                if path and os.path.isfile(path):
                    return path
    except Exception as exc:
        log.debug("Kein Profilbild: %s", exc)
    return ""


def switch_user() -> None:
    """Zum Anmeldebildschirm, ohne abzumelden: Dort wählt man das andere Konto, die eigenen Apps laufen weiter."""
    subprocess.Popen(["tsdiscon"], creationflags=subprocess.CREATE_NO_WINDOW)

"""Führt ein Werkzeug-Artefakt von Nova in einem eigenen Prozess aus: Hängt oder stürzt es, bleibt PopWin heil.

Aufruf: nova_tool_runner.py SKRIPT. Gehört das Werkzeug zu einer Verbindung, liefert `secret("feld")` deren Werte,
nur die dieser einen Verbindung. Bei der Verbindung „ovh“ gibt es dazu `ovh_client()`.
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CONNECTION_FLAG = "POPWIN_TOOL_CONNECTION"


def helpers(connection: str) -> dict:
    if not connection:
        return {}
    import keyring

    from config.config_manager import KEYRING_SERVICE
    from core.nova_connections import keyring_user

    def secret(field: str) -> str:
        value = keyring.get_password(KEYRING_SERVICE, keyring_user(connection, str(field).strip().lower()))
        if not value:
            raise KeyError(f"In der Verbindung {connection} fehlt {field}. Trag es in PopWin ein.")
        return value

    found = {"secret": secret}
    if connection == "ovh":
        def ovh_client():
            import ovh
            endpoint = keyring.get_password(KEYRING_SERVICE, keyring_user("ovh", "endpoint")) or "ovh-eu"
            return ovh.Client(endpoint=endpoint, application_key=secret("application_key"),
                              application_secret=secret("application_secret"), consumer_key=secret("consumer_key"))
        found["ovh_client"] = ovh_client
    return found


def main() -> int:
    if len(sys.argv) < 2:
        print("Kein Skript angegeben.")
        return 2
    runpy.run_path(sys.argv[1], init_globals=helpers(os.environ.get(CONNECTION_FLAG, "")), run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())

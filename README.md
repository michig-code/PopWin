# PopWin

Eine anpassbare Desktop-Oberfläche für Windows 10/11 im Stil von Pop!_OS. Reines Python mit PySide6 und QSS.

## Starten und beenden

- **Starten:** Doppelklick auf `PopWin starten.bat`. Beim allerersten Mal richtet sich alles selbst ein und die Setup-Seite öffnet sich.
- **Finden:** PopWin steht im Windows-Startmenü und in der Windows-Suche. Läuft es schon, öffnet ein Klick die Einstellungen.
- **Beenden:** Startmenü → *PopWin deaktivieren*, oder Notfall-Kürzel **Strg + Alt + Umschalt + K**.
- **Taskleiste fehlt?** Doppelklick auf `Taskleiste wiederherstellen.bat`. Normalerweise ist das nicht nötig: Ein eigener Wächter-Prozess bringt sie auch nach einem Absturz zurück.

## Bedienung

| Was | Wie |
| --- | --- |
| Windows-Taste | Einmal tippen öffnet das Home-Menü, dort lostippen startet die Suche. Kombinationen wie Win+D bleiben unverändert. Umstellbar unter Einstellungen → Startmenü |
| Launcher | Aussehen unter Einstellungen → Launcher. Alt + Leertaste, oder die Lupe in der Taskbar. Ist das Kürzel belegt, weicht PopWin auf Alt + Umschalt + Leertaste aus; aktiv ist, was unter *Einstellungen → Launcher* steht |
| Darkmode | `darkmode aktivieren`, `whitemode aktivieren`, `… deaktivieren` im Launcher |
| Win+R | `cmd`, `regedit`, `mspaint`, `shell:startup` im Launcher |
| KI | `? Frage` oder `ai: Frage` im Launcher, abgeschickt mit Enter |
| Lautstärke | Mausrad auf dem Lautsprecher-Symbol; Klick zeigt den Regler |
| Papierkorb | Dateien draufziehen; Linksklick öffnet ihn; Rechtsklick zeigt Inhalt und *Leeren* |
| Widgets | Rechtsklick auf ein Widget öffnet die Galerie: 13 Widgets hinzufügen und jedes einzeln anpassen (Darstellung, Farbe, Größe, Ebene, Deckkraft). Ohne Widget über Schnelleinstellungen → Widget-Einstellungen |
| Windows-Apps übernehmen | Beim ersten Start einmal automatisch; später unter Einstellungen → Leisten |
| Mehrere Monitore | Einstellungen → Monitore: Topbar und Taskbar getrennt auf allen, nur dem Hauptmonitor oder gezielt angekreuzten Monitoren. Das Aussehen ist entweder synchron oder pro Monitor einzeln (Stil, Position, Inhalte, Icon-Größe) |
| Taskbar-Größe | Einstellungen → Monitore: nur so breit wie die Icons (wächst mit den Apps), Ausrichtung, Höhe, Icon-Größe und Abstand zwischen den Icons |
| Taskbar-Indikator | Markierung unter dem Icon: kleiner blasser Punkt = minimiert, kurzer Strich = Fenster ist offen, langer farbiger Strich = aktive App, nichts = läuft nicht |
| Schnelleinstellungen | Regler-Symbol links neben der Uhr: Dark Mode, Nicht stören, Benachrichtigungen, Bluetooth, Widgets, Animationen, Fokus-Timer, PC schlafen legen |
| Ausrichtung | Einstellungen → Monitore: Taskbar links, mittig oder rechts, in der vollen Breite wie in der schmalen Form |
| Animationen | Einstellungen → Design: an oder aus, Dauer 80 bis 600 ms |
| Anheften | Datei, Programm oder Verknüpfung auf die Taskbar ziehen, oder Rechtsklick auf eine laufende App → *An Taskbar anheften* |
| Auto-Hide | Die Taskbar erscheint, wenn die Maus an den Bildschirmrand kommt. Abschaltbar unter Einstellungen → Monitore |
| Vorschau | Auf einem Task verweilen zeigt Live-Bild, Schließen und „Task beenden“. Tasks lassen sich mit der Maus verschieben |
| KI-Aktionen | Findet die Suche nichts, schlägt die KI Aktionen vor. Sie laufen erst nach deinem Ja, mehrere als Aufgabenliste nacheinander |
| Spracheingabe | Mikrofon-Knopf in der Suche. Die Aufnahme geht zur Erkennung an Google |
| PopWin Explorer | Über die Suche oder das Ordner-Symbol im Home-Menü |
| Nova | Knopf in der Taskbar oder Strg + Alt + N, siehe [nova/README.md](nova/README.md) |
| Sprachen | Einstellungen → Design → Sprache: Deutsch, English, Español, Français oder automatisch wie Windows. Neue Sprache: Datei in `assets/lang`, siehe `core/i18n.py` |
| Hardware | PopWin erkennt Bluetooth, WLAN, Akku, Mikrofon, Lautsprecher und Kamera und zeigt nur, was vorhanden ist. Abschaltbar unter Einstellungen → Topbar → Dieser PC |
| Datenschutz | Einstellungen → Datenschutz: was PopWin verschickt, Novas Gedächtnis ein- oder ausschalten und löschen |
| Hintergrund | Einstellungen → Hintergrund: Wallpaper Engine wird laufend erkannt; ohne sie spielt PopWin eigene Videos und GIFs hinter den Symbolen |
| Netzwerk | Fenster an der Netzwerk-Anzeige, Einstellungen → Netzwerk: Speedtest mit Verlauf |
| Performance | Einstellungen → Performance: Arbeitsspeicher von PopWin als Diagramm, Datei- und RAM-Cache leeren |
| Audio | Einstellungen → Audio: Lautstärke jeder App mit Live-Pegel |
| Beta | Einstellungen → Beta: Desktop-Gruppen, PopWin-Dateifenster und PopWin-Apps einzeln umschalten |
| Audio-Ausgabe | Lautsprecher oder Headset wechseln: Lautstärke-Knopf in der Topbar, Schnelleinstellungen oder Suche nach „Headset“ |
| PowerShell Studio | Suche nach „PowerShell Studio“ oder „PowerShell ISE“. Debugger mit Haltepunkten, Bedingungen und Logpunkten, frei andockbare Bereiche, eigene Farben und Tasten, Befehlspalette mit Strg + Umschalt + P, Formatieren mit Umschalt + Alt + F |
| Fenster in der Taskbar | Der Hauptmonitor zeigt alle laufenden Fenster, Nebenmonitore nur die Fenster, die auf ihnen liegen, auch minimierte und maximierte. Änderbar unter Einstellungen → Leisten |
| Windows-Taskleiste | Verschwindet, solange PopWin mit eigener Taskbar läuft, und kommt beim Beenden zurück. Abschaltbar in Setup-Schritt 3 |
| Eigene Styles | Dateien in `themes/` bearbeiten, Änderungen wirken beim Speichern |

## Was bewusst anders funktioniert

- **Versteckte Tray-Symbole:** Windows lässt fremde Programme nicht in eine eigene Leiste einhängen. Der Pfeil öffnet daher den echten Windows-Überlauf.
- **Rechtsklick auf Dateien:** Das eigene Menü gilt für die freie Desktop-Fläche und die PopWin-Leisten. Auf Dateien bleibt das Windows-Menü, weil nur Windows die Einträge installierter Programme kennt.
- **Vollbild:** Füllt das aktive Fenster einen Monitor komplett aus (Spiel, Video, F11), gehen die Leisten dort nach hinten und kommen beim Verlassen zurück. Andere Monitore bleiben unberührt. Overlay-Widgets bleiben absichtlich sichtbar, aber nur bei randlosem Vollbild; exklusives Vollbild lässt keine Fenster darüber zu.
- **Gemini-Key:** liegt im Windows-Anmeldeinformationsspeicher, nicht in `settings.json`.
- **Netzwerkprüfung:** Alle 30 Sekunden wird eine Verbindung zu 1.1.1.1:443 aufgebaut, um Internetzugang zu erkennen. Es werden keine Daten übertragen.

## Aufbau

```text
main.py                   Start, Zusammenspiel, Safe-Exit
config/                   Einstellungen laden, prüfen, speichern
themes/                   Reine QSS-Dateien, je Theme dunkel und hell
core/win_api.py           Alle Windows-Aufrufe
core/shell_hooks.py       Hotkeys, Desktop-Rechtsklick, Win+S
core/system_status.py     Mikrofon, Netzwerk, Bluetooth, Cloud, Lautstärke, CPU/RAM/GPU
core/media_controller.py  Now Playing
core/gemini_client.py     KI-Anfragen im Hintergrund
core/taskbar_watchdog.py  Stellt die Taskleiste nach einem Absturz wieder her
ui/                       Topbar, Taskbar, Launcher, Menüs, Einstellungen, Widgets
tests/                    test_logic (Logik), quiet_render (Aussehen), system_check (Systemeingriffe)
```

Fehler landen in `popwin.log`. Python 3.12 wird empfohlen, weil `winsdk` für neuere Versionen nicht zuverlässig verfügbar ist.

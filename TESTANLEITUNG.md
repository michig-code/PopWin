# PopWin 4.5.0 – Testanleitung

Diese Anleitung ist für dich zum Selbsttesten. Bisher wurde **nichts davon gestartet**. Alles wurde nur gelesen und statisch geprüft. Ob die Fixes unter Windows wirken, zeigt erst dein Test.

**Vorbereitung:** Beende vor dem Test das Original-PopWin und alle weiteren PopWin-Instanzen (Tray-Symbol, Task-Manager). Für Teil 5 brauchst du zwei Monitore. Für Teil 2 (^) ideal ist ein Windows-10- und ein Windows-11-PC. Notiere bei jedem Fehler: Schritt, was du gesehen hast, was du erwartet hast.

---

## 1. Erster Start

**Wo liegt die Python-Umgebung?** Seit 4.5.0 liegt sie nicht mehr im Projektordner `PopWin`, sondern außerhalb davon unter:

`%LOCALAPPDATA%\PopWin\venv-<8 Zeichen>`

Den Ordnernamen siehst du, wenn du `%LOCALAPPDATA%\PopWin` in die Adresszeile des Explorers eingibst. Der Teil `<8 Zeichen>` ist ein Kennzeichen des Projektpfads. Verschiebst du den PopWin-Ordner, entsteht daneben eine neue Umgebung. Der Grund: Im langen Pfad innerhalb von OneDrive scheiterte die Paket-Installation mit dem Fehler `Errno 2` („zu langer Dateipfad“).

1. Beende das Original-PopWin (Rechtsklick auf die Leiste bzw. Task-Manager → `PopWin.exe` / `python.exe` beenden).
2. **Alte Umgebung im Projektordner:** Liegt im Ordner `PopWin` noch ein Ordner `.venv` aus einer früheren Fassung, entfernt ihn die `.bat` beim Start automatisch. Du kannst ihn vorher auch selbst löschen.
3. Öffne den Ordner `PopWin` im Projekt und starte per Doppelklick `PopWin starten.bat`.
4. **Erwartet:** Ein Konsolenfenster zeigt „PopWin wird eingerichtet. Das dauert beim ersten Start ein paar Minuten ...“. Die Pakete werden aus dem Internet geladen. **Das dauert einige Minuten, das ist normal.** Danach startet PopWin: Topbar und Taskbar erscheinen.
5. Beende PopWin und starte die `.bat` erneut. **Erwartet:** Keine Einrichtung mehr, PopWin startet in wenigen Sekunden.
6. Optional: Lösche den Ordner `%LOCALAPPDATA%\PopWin\venv-<8 Zeichen>` komplett und starte die `.bat` noch einmal. **Erwartet:** Die Einrichtung läuft automatisch wieder durch.
7. **Falls doch der Fehler „zu langer Dateipfad“ oder „Errno 2“ erscheint:** Das sollte mit dem neuen Ort nicht mehr passieren. Notiere den Text und den vollständigen Pfad aus der Meldung. Als Abhilfe kannst du Langpfade in Windows aktivieren. Dafür sind Administratorrechte nötig, und danach muss der PC neu starten:
   - Windows-PowerShell **als Administrator** öffnen und ausführen:
     `Set-ItemProperty -Path HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem -Name LongPathsEnabled -Value 1`
   - PC neu starten, dann `PopWin starten.bat` erneut starten.
   - Ohne Administratorrechte: Den PopWin-Ordner an einen kurzen Ort verschieben (z. B. `C:\PopWin`) und dort die `.bat` starten.

**Hinweis für Teil 6:** Der Build braucht die Python-Umgebung aus `%LOCALAPPDATA%\PopWin\venv-<8 Zeichen>`. Die `.bat` legt keine Umgebung mehr im Projektordner an.

## 2. Die vier Bugs

**2.1 Einstellungsfenster**
- Öffne die Einstellungen. **Erwartet:** Normale Windows-Titelleiste (Minimieren, Maximieren, Schließen), Fenster lässt sich verschieben, am Rand skalieren und mit Win+Pfeiltasten einrasten. Seitenleiste mit 6 Kategorien, „Einrichtung“ oben, Suchfeld in der Kopfzeile.

**2.2 ^-Symbol (versteckte Symbole), Windows 10 und 11**
- Blende die Windows-Taskleiste aus (PopWin-Standard). Klicke 20-mal hintereinander auf das ^ in der Topbar, auch schnell, auch direkt nach dem Start.
- **Erwartet:** Jedes Mal erscheint der Überlauf mit den versteckten Symbolen unter dem Pfeil. **Das Windows-Startmenü darf nie aufgehen.**
- Wenn nichts erscheint: Das ist der wichtigste Punkt, der noch offen ist (siehe Restrisiken). Gib mit an, ob Windows 10 oder 11.

**2.3 Taskbar-Klickbarkeit (schwacher PC)**
- Öffne 10–15 Fenster, lasse Videos oder Spiele im Hintergrund laufen. Klicke Taskbar-Symbole, wechsle Fenster, öffne die Vorschau.
- **Erwartet:** Alle Klicks kommen an. Die Windows-Taskleiste legt sich nicht über das Dock. Falls doch, notiere wie lange es dauert (sollte unter 0,5 s wieder verschwinden).

**2.4 Notizen-Widget**
- Füge ein Notizen-Widget hinzu. Ziehe es größer und kleiner (Griff an der Breite). **Erwartet:** Textfeld wächst und schrumpft mit, nie Höhe 0, kein Flackern.
- Ändere in den Widget-Optionen die Schriftgröße. **Erwartet:** Sie wirkt sofort (nicht immer 13 px). Alte Notizen sind nicht kleiner geworden.
- Schließe und öffne PopWin: Text und Größe sind erhalten.

## 3. Speichern

1. **Ständiges Speichern:** Ändere mehrere Einstellungen, beende PopWin über den Task-Manager (hart). Starte neu. **Erwartet:** Die Änderungen sind da (höchstens die letzte Sekunde fehlt).
2. **Gesperrte `settings.json`:** Beende PopWin. Öffne `PopWin\config\settings.json` in einem Programm, das die Datei exklusiv sperrt (oder sperre sie per PowerShell mit `$f=[IO.File]::Open("<Pfad>","Open","Read","None")`, Fenster offen lassen). Starte PopWin.
   - **Erwartet:** PopWin startet, aber deine echte `settings.json` wird **nicht** mit Standardwerten überschrieben. Änderungen landen in `settings.session.json`.
   - Gib die Datei frei. **Erwartet:** Es erscheint eine einmalige Sicherung `settings.locked-<zeit>.json`. Beim nächsten Start gilt wieder die echte Datei, Leisten, Widgets und Pins sind da.

## 4. Design

1. Einstellungen → **Design**. **Erwartet:** „Aurora“ ist Standard, „Klassisch (Liquid Glass)“ ist wählbar, dazu die übrigen Themes.
2. Wechsle zwischen beiden und prüfe Topbar, Taskbar, Einstellungen, Menüs, Notizen, Tooltips.
3. Wechsle zwischen Dunkel und Hell. **Erwartet:** Alles lesbar, Hell-Tooltips sind hell, ausgewählte Menü- und Suchtreffer gut sichtbar.
4. Schalte **„Reduzierte Bewegung“** ein. **Erwartet:** Nur dezente Überblendungen, keine Verschiebe-Animationen. Schalte sie wieder aus: Animationen sind zurück.
5. Das Klassisch-Design muss aussehen wie vor der Überarbeitung.

## 5. Panel an zwei Monitoren

- Öffne ein Panel an Monitor 1, schließe es und öffne es **sofort** an Monitor 2.
- **Erwartet:** Es erscheint an Monitor 2 und springt nicht an die alte Stelle zurück. Wiederhole schnell mehrmals.

## 6. Build, Installer, Deinstallation

1. Beende PopWin. Suche im Explorer nach `%LOCALAPPDATA%\PopWin` und öffne den Unterordner `venv-<8 Zeichen>`. Öffne im Ordner `PopWin` (Projekt) ein Terminal und führe aus:
   `"%LOCALAPPDATA%\PopWin\venv-<8 Zeichen>\Scripts\python.exe" build.py`
   Ersetze `<8 Zeichen>` durch den Ordnernamen aus dem Explorer, oder gib den Pfad vollständig an. Liegt dort nur ein einziger `venv-…`-Ordner, reicht dieser.
   **Erwartet:** Der Build läuft durch (Zwischendateien liegen in `%TEMP%`), im Ordner `release\` liegen `PopWin Setup.exe` und `PopWin Portable.zip`. Der Build bricht ab, wenn Benutzerdaten im Paket wären.
2. Starte `release\PopWin Setup.exe`. Wähle „Desktop-Verknüpfung“ und „Mit Windows starten“. **Erwartet:** Aurora-Design im Installer, Version 4.5.0, Installation ohne Fehler, PopWin startet.
3. Prüfe: Startmenü-Eintrag „PopWin“, Desktop-Verknüpfung, Eintrag in Einstellungen → Apps, Autostart im Task-Manager, Neustart von Windows (PopWin startet, Windows-Taskleiste verschwindet nicht dauerhaft).
4. **Kontextmenüs:** Rechtsklick auf einen Ordner und auf eine Textdatei. **Erwartet:** „Im PopWin Explorer öffnen“ und „Öffnen mit → PopWin Editor“ funktionieren.
5. Installiere das Setup ein zweites Mal (Update-Fall). **Erwartet:** Es erkennt die vorhandene Version, keine doppelten Einträge.
6. **Deinstallieren** über Einstellungen → Apps. **Erwartet:** Startmenü, Desktop, Autostart und die beiden Kontextmenü-Einträge sind weg, die Windows-Taskleiste ist wieder da, `%LOCALAPPDATA%\Programs\PopWin` ist nach ein paar Sekunden gelöscht. Das Deinstallationsfenster schließt sich nicht vorzeitig.
7. Teste auch einen selbst gewählten Ordner (z. B. `D:\Tools\Test`): Fremde Dateien darin müssen erhalten bleiben.

## 7. Notfall

Ist die Windows-Taskleiste nach einem Absturz weg oder lässt sich nichts anklicken:
- Doppelklick auf `Taskleiste wiederherstellen.bat` im Ordner `PopWin`. Das beendet PopWin und holt die Windows-Taskleiste zurück.
- Hilft das nicht: `Strg+Umschalt+Esc` → Task-Manager → „Windows-Explorer“ neu starten.

## 8. Übernommene Features aus der Vorgängerausgabe

Diese Funktionen sind neu in PopWin. Teste jede einmal. Notiere bei einem Fehler den Schritt und was passiert ist. Zu den Seitennamen: Sie stehen in der Einstellungs-Seitenleiste.

**8.1 Designs:** Einstellungen → Themes. Wähle nacheinander Kacheln Obsidian, Glas Rauch und Neon-Nacht. **Erwartet:** Jedes Design wirkt sofort auf Topbar und Taskbar, Symbole sind lesbar, auch im Hell-Modus.

**8.2 Theme-Editor:** Einstellungen → Themes → eigenes Theme anlegen und eine Farbe ändern. **Erwartet:** Die Live-Vorschau reagiert sofort. Das eigene Theme steht danach in der Auswahl und bleibt nach einem Neustart erhalten.

**8.3 Teilen & Presets und Sicherungen:** Einstellungen → Teilen & Presets. Speichere ein Preset, ändere eine Einstellung, lade das Preset. Exportiere eine Einstellungsdatei und importiere sie wieder. **Erwartet:** Jeder Schritt wirkt, der alte Stand kommt zurück. In der Sicherungsliste steht mindestens ein Eintrag, und „Wiederherstellen“ stellt den Stand her.

**8.4 Leisten-Designer:** Einstellungen → Taskbar (bzw. Topbar) → Designer öffnen. Ziehe ein Symbol an eine andere Stelle. Schalte das schwebende Dock ein und verändere den Abstand zum Rand. **Erwartet:** Die Reihenfolge bleibt nach einem Neustart erhalten. Das schwebende Dock steht über der Taskbar-Mitte, der Abstand ändert sich.

**8.5 Taskbar-Funktionen:**
- Mittelklick und Mausrad über einem Taskbar-Symbol, Rechtsklick → „Fenster anordnen“ und „Stummschalten“. **Erwartet:** Jede Aktion tut, was ihr Menütext sagt. Nichts stürzt ab.
- Ziehe eine Datei auf ein Taskbar-Symbol und halte kurz. **Erwartet:** Die Vorschau dieser App öffnet sich, beim Loslassen landet die Datei dort.
- Einstellungen → Taskbar → „Taskbar-Icons wachsen unter der Maus“ einschalten. **Erwartet:** Die Symbole vergrößern sich beim Drüberfahren (Beta).
- Eine App mit Zahl-Badge (z. B. Teams oder Discord, falls vorhanden). **Erwartet:** Die Zahl erscheint am Symbol.
- Taskbar links oder rechts setzen. **Erwartet:** Die Leiste steht senkrecht, alle Symbole sind sichtbar.

**8.6 Suche:** Öffne die Suche und tippe:
- `2+3*4`. **Erwartet:** Ergebnis 14 als Treffer.
- Einen Umrechnungsfall wie `100 km in mi`. **Erwartet:** Ein Ergebnis erscheint.
- Einen Timer (prüfe, was die Suche für „Timer“ anbietet). **Erwartet:** Der Timer startet.
- Einen Suchbegriff ohne App-Treffer. **Erwartet:** Ein Web-Treffer erscheint und öffnet den Browser.
- Zwischenablage-Verlauf: Einstellungen → Datenschutz & Erweitert → Seite „Datenschutz“, Schalter „Verlauf der Zwischenablage merken“ einschalten. Kopiere zwei Texte, öffne die Suche und tippe `v ` (v und Leerzeichen). **Erwartet:** Beide Texte erscheinen in der Suche.
- Starte dieselbe App mehrmals über die Suche. **Erwartet:** Sie rutscht in der Trefferliste nach oben.

**8.7 Topbar-Menüs:** Klicke nacheinander auf den Benutzernamen (Konto-Menü), das Sprachkürzel, das Mikrofon, und löse „Projektion“ und „Screenshot“ aus. **Erwartet:** Jeder Klick öffnet das passende Menü oder die Funktion, ohne Fehler.

**8.8 Leistung und Spielmodus:** Einstellungen → Leistung. Schalte die Stufen „Sparsam“, „Ausgewogen“ und „Schön“ durch. Starte dann ein Vollbild-Spiel oder ein Video im Vollbild. **Erwartet:** Im Vollbild treten die Leisten zurück, nichts verdeckt das Spiel. „Sparsam“ zeigt keine Animationen.

**8.9 Startmenü-Varianten:** Einstellungen → Startmenü. Schalte alle fünf Ansichten durch und öffne jede einmal. **Erwartet:** Jede Ansicht öffnet sich, die Suche im Startmenü funktioniert in jeder.

**8.10 Setup und „Was ist neu“:** Der Setup-Bildschirm erscheint nur bei einer frischen Installation, also beim ersten Start. Prüfe das mit dem Setup aus Teil 6. Nach dem Update zeigt „Was ist neu“ die Einträge der Version 4.5.0. **Erwartet:** Setup nur beim ersten Start, „Was ist neu“ einmal nach dem Update.

**8.11 Hintergründe:**
- Einstellungen → Hintergrund: Wechselintervall auf „Alle 5 Minuten“ stellen und fünf Minuten warten. **Erwartet:** Das Bild wechselt danach von selbst.
- Ein Fenster im Vollbild über das Hintergrundbild legen. **Erwartet:** Das Video pausiert, es läuft wieder, wenn das Fenster weg ist.
- Automatik „Hell/Dunkel nach Uhrzeit“ einschalten und die Uhrzeiten dicht an die aktuelle Zeit legen. **Erwartet:** Der Modus wechselt zur eingestellten Zeit.
- Die drei neuen Videohintergründe „Glut“, „Polarlicht“ und „Raster“ auswählen. **Erwartet:** Sie laufen. Läuft keines, siehe Restrisiko 10.

**8.12 Nova-Persönlichkeit:** Nova muss eingerichtet sein. Ändere unter Einstellungen → Nova → „Persönlichkeit & Charakter“ den Text, speichere ihn, stelle eine kurze Frage und setze die Persönlichkeit danach zurück. **Erwartet:** Die Antwort passt zur Einstellung, „Zurücksetzen“ stellt die Standard-Persönlichkeit wieder her.

**8.13 Explorer:**
- Ziehe eine Datei von einem Ordner in einen anderen. Drücke danach Strg+Z. **Erwartet:** Die Datei kehrt an den alten Platz zurück.
- Ziehe einen Tab aus der Tab-Leiste heraus. **Erwartet:** Er wird zu einem eigenen Fenster.
- Öffne „KI-Sortieren“ in einem Ordner mit mehreren Dateien. **Erwartet:** Der Dialog öffnet sich. Mit Internet sortiert er, ohne Internet notiere die Meldung.

**8.14 TexttoSpeech for Windows:** Öffne die Erweiterung über die Suche (Eintrag „TexttoSpeech“). Ist sie dort nicht zu finden, notiere das, der Startweg ist noch nicht festgelegt. Tippe einen Satz und drücke Alt+Y. **Erwartet:** Der Satz wird gesprochen. Für Mikrofon-Durchleitung brauchst du ein virtuelles Mikrofon (z. B. VB-Cable), das separat installiert werden muss.

**8.15 PopWin Lite:** Beende das normale PopWin. Starte im Ordner `PopWin`:
`"%LOCALAPPDATA%\PopWin\venv-<8 Zeichen>\Scripts\pythonw.exe" popwin_lite.py`
**Erwartet:** Taskleiste, Uhr, Startmenü und Suche erscheinen. Nova, Widgets und der Explorer fehlen absichtlich.

---

## 9. Sprachen

Seit 4.5.0 gibt es elf Sprachen: Deutsch, Englisch, Spanisch, Französisch, Italienisch, Niederländisch, Polnisch, Portugiesisch (Brasilien), Türkisch, Japanisch und Chinesisch (vereinfacht). Die Auswahl steht unter **Einstellungen → Design → Sprache und Region**.

**9.1 PopWin folgt der Windows-Sprache:**
1. Stelle in Windows die Anzeigesprache um (Einstellungen → Zeit und Sprache → Sprache). Je nach Windows-Version musst du dich dafür ab- und wieder anmelden.
2. Beende PopWin und starte es neu. PopWin liest die Sprache beim Start.
3. **Erwartet:** PopWin erscheint in der neuen Sprache. Bei „Sprache“ steht „Systemsprache (automatisch)“.

**9.2 Manuelle Auswahl:**
1. Einstellungen → Design → Sprache und Region → Dropdown „Sprache“ → z. B. „Français“.
2. **Erwartet:** PopWin startet die Oberfläche kurz neu, danach stehen die Texte auf Französisch. Der Hinweistext darunter sagt das ebenfalls.
3. Wiederhole mit „English“ und „日本語“. Bei Japanisch und Chinesisch zusätzlich Teil 9.4 prüfen.
4. Starte PopWin neu. **Erwartet:** Die gewählte Sprache bleibt erhalten.

**9.3 Zurück zur Systemsprache:**
1. Dropdown „Sprache“ → „Systemsprache (automatisch)“.
2. **Erwartet:** PopWin wechselt zurück in die Windows-Anzeigesprache. Die Einstellung bleibt nach einem Neustart erhalten.

**9.4 Lange Texte und Schriftzeichen:**
- Stelle nacheinander Niederländisch, Polnisch, Türkisch und Deutsch ein, weil dort die Wörter lang sind. Prüfe jeweils die Einstellungen (Seitenleiste mit den Kategorien, Kopfzeile mit Suche), die Menüs von Topbar und Taskbar (Rechtsklick), das Startmenü und den Dialog „Was ist neu“.
- Stelle danach Japanisch und Chinesisch ein und prüfe dieselben Stellen.
- **Erwartet:** Kein Text ist abgeschnitten oder überlappt. Es erscheinen keine Kästchen (□) statt Zeichen. Japanische und chinesische Zeichen sind lesbar, im Dunkel- und im Hell-Modus, in Aurora und im Klassisch-Design. Notiere jede Stelle mit Screenshot.
- Fehlt ein Text in einer Sprache, erscheint er auf Englisch. Das ist so gewollt. Notiere es trotzdem, damit die Übersetzung ergänzt werden kann.

**9.5 Installer-Sprache:**
- Starte `release\PopWin Setup.exe` bei einer anderen Windows-Anzeigesprache als Deutsch.
- **Erwartet:** Der Installer bleibt auf Deutsch. Das ist der bekannte Stand, kein Fehler. Die Sprache von PopWin richtet sich danach beim ersten Start nach Windows.

---

## Änderungsnotizen (Version 4.5.0)

**Neu**
- Neues Standard-Design „Aurora“ (Dunkel und Hell). Das alte Design bleibt als „Klassisch (Liquid Glass)“ erhalten.
- Einstellungen in einem echten Windows-Fenster, 20 Seiten in 6 Kategorien, feste Kopfzeile mit Suche, Theme-Vorschau.
- Modus „Reduzierte Bewegung“ für schwächere PCs.
- Start-Skripte portabel: Die Python-Umgebung liegt unter `%LOCALAPPDATA%\PopWin\venv-<Hash>` und wird bei Bedarf neu angelegt, Pakete nur bei Änderung nachinstalliert.
- Installer im Aurora-Design, entfernt beim Deinstallieren auch die Kontextmenü-Einträge.
- Das alte Web-Nova (HTML/JavaScript) ist entfernt. PopWin enthält kein HTML und kein JavaScript.
- Sprache folgt der Windows-Anzeigesprache. Neu dazu kommen Italienisch, Niederländisch, Polnisch, Portugiesisch (Brasilien), Türkisch, Japanisch und Chinesisch (vereinfacht). Siehe Teil 9.

**Behoben**
- Einstellungsfenster ohne Standard-Titelleiste.
- ^-Symbol öffnete selten das Startmenü (Win+B/Enter-Trick entfernt, Öffnen im Hintergrund mit Bestätigung).
- Taskbar auf schwachen PCs nicht klickbar (ein gemeinsamer Timer, Zwischenspeicher, Wächter alle 250/500 ms).
- Notizen-Widget beim Skalieren, Schriftgröße wirkt wieder.
- Speichern: gesammelt, mit Wiederholung, blockiert die Oberfläche nicht, überschreibt gesperrte `settings.json` nie mit Standardwerten.
- Fensterkennung (Dock-Gruppen), Panel-Position beim schnellen Wiederöffnen, Zahlen-Badges, Uhrgröße beim Theme-Wechsel, gelöschte Menü-Animation.
- Deinstaller löschte den Programmordner nie und konnte sich selbst beenden. Beide Fehler behoben.
- `PopWin starten.bat` brach bei Einrichtungsfehlern falsch ab, jetzt mit Windows-Zeilenenden.
- Paket-Installation scheiterte mit „Errno 2“ (zu langer Pfad in OneDrive): Umgebung liegt jetzt außerhalb des Projektordners, `PySide6` als `PySide6-Essentials`.
- Versionsnummer war kleiner als der Changelog, jetzt 4.5.0.

## Restrisiken

1. **Nichts wurde ausgeführt.** Alle Prüfungen waren statisch (Lesen, Kompilieren, pyflakes). Die automatischen Tests (411) wurden nach den Änderungen nicht erneut gelaufen.
2. **^ unter Windows 11 bei ausgeblendeter Taskleiste:** Der neue Weg (UIA) ist nicht erprobt. Es gibt einen Rückfallweg, aber keine Garantie.
3. **Build:** PyInstaller-Lauf, Größe und Startfähigkeit der `.exe` ungetestet.
4. **Installer/Deinstaller:** Registry-Einträge, Autostart und Aufräumen nur durch Codelesen geprüft. Unter UAC/Nicht-Admin bitte beide Fälle testen.
5. **`tests/test_logic.py`** kennt die übernommenen Features nicht und kann noch alte Annahmen enthalten (z. B. Version 3.6, Titelleiste). Ein Testlauf kann daher Fehlalarme melden und deckt Teil 8 nicht ab.
6. **Persönliche Daten im Quellordner:** `nova/data/.env` (API-Key), `chat.json`, `memory.json`, `config/*.json` liegen im Ordner. Im Release sind sie ausgeschlossen. Wird der Ordner als ZIP geteilt, vorher entfernen und den Key erneuern.
7. **OneDrive:** Der Projektordner liegt in OneDrive. Die Python-Umgebung liegt seit 4.5.0 bewusst außerhalb davon. Sync-Sperren können den Projektordner trotzdem verlangsamen. Für den Dauerbetrieb den Ordner außerhalb von OneDrive nutzen.
8. **Schwache PCs, Mehr-Monitor-DPI und die neue Design-Optik** sind nur am echten Gerät beurteilbar.
9. Hat ein PC schon eine PopWin-Ausgabe über 4.5.0 installiert, fragt das Setup nach „Trotzdem installieren“.
10. **Video-Hintergrund:** Das Modul `wallpaper/app.py` braucht `PySide6-Addons` (QtMultimedia). Das Paket steht seit dieser Fassung in `requirements.txt`. Bleibt ein Video-Hintergrund ohne Wirkung oder mit Fehlermeldung, ist die Umgebung unvollständig. Dann `%LOCALAPPDATA%\PopWin\venv-<8 Zeichen>` löschen und `PopWin starten.bat` erneut starten.
11. **Große Zusammenführungen** in `ui/taskbar.py`, `ui/base.py`, `ui/topbar.py` und `main.py` sind nur statisch geprüft. Fehler dort zeigen sich erst im Test, besonders in Teil 2, 5 und 8.5.
12. **Hell-Modus, Theme „Pop Basic“** (`themes/pop_basic.qss`): Die markierte Zeile in der Trefferliste hat dort keine eigene Regel. Sie ist im Hell-Modus evtl. schlecht lesbar. Das war schon vor der Überarbeitung so.
13. **Zweiter Start:** Ein zweiter Start von PopWin soll das laufende PopWin nach vorn holen. Das ist nach der Feature-Übernahme nicht geprüft. Bitte beim Start aus Teil 1 mitprüfen.
14. **Autostart nach Task-Manager-Sperre:** Hat jemand PopWin im Task-Manager abgeschaltet und ist ein alter Autostart-Eintrag vorhanden, schaltet der Start den Autostart wieder ein. Beim Test von Teil 6 prüfen.
15. **Alte `.venv` im Projektordner** wird beim Start ohne Rückfrage gelöscht.
16. **Fehlermeldung „zu langer Pfad“** erscheint bei jedem Fehler mit „Errno 2“, nicht nur bei echten Langpfaden. Die vollständige pip-Ausgabe steht vorher im Konsolenfenster.
17. **Sprachen:** Die Übersetzungen sind nicht von Muttersprachlern geprüft. Lange Texte können abgeschnitten werden, und in Japanisch und Chinesisch kann die Schrift fehlen. Der Installer bleibt auf Deutsch.

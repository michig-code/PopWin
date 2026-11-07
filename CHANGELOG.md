# Änderungen

## 3.8.5

- Explorer: KI-gestützte Dateisortierung mit interaktiver Vorschau, Ordner-Verschiebung (mit Strg+Z Rückgängig) und virtueller Themen-Gruppierung
- Nova: Anpassbare KI-Persönlichkeit (System-Prompt) in den Einstellungen mit Zurücksetzen-Funktion
- Nova: Zuverlässiges Weiterreden bei langen Persönlichkeiten und Folgefragen durch Gemini-3.8-Live-Upgrade und automatischen Sprach-Fallback
- Explorer KI-Sortieren: Ordnernamen der KI werden bereinigt (keine Pfade wie „..\“, keine verbotenen Zeichen), jede Datei nur einmal, gesperrte Dateien brechen die Sortierung nicht mehr ab, Rückgängig überschreibt nie neue Dateien, Ansicht-Gruppen gelten nur für ihren Ordner.
- Nova-Stimme: Antwortet „gemini-3.8-live“ nicht, verbindet Nova automatisch mit dem bewährten „gemini-3.1-flash-live-preview“.
- 8 neue UI-Stile, jeder mit eigener Designsprache und Kachel-Effekt: Dunkles Glas (Liquid Glass), Obsidian (Neubrutalismus), Nova Galaxie (Aurora), Frostglas (weiches Milchglas), Neon-Kontur (Cyberpunk), Bernstein (Skeuomorph, Messing), Graphit rund (Neumorphismus), Ozean (fließende Wellenform). Neue Einstellungen „Kacheln um die Icons“ und „Form der Kacheln“ (Komplett rund) unter Design. Frostglas bleibt im Dunkel-Modus auch auf hellen Hintergründen lesbar.
- Kachel-Stile: Taskbar mit runderen Ecken als die 8 px von Windows (Fenster samt Blur wird rund zugeschnitten), nur bei den neuen Stilen.
- Glas-Familie im Stil von Liquid Glass: Klarglas, Rosé-Glas, Smaragd-Glas, Prisma-Glas (Regenbogen-Kante), Rauchglas und Flüssigglas (Apple-ähnliche Lichtkante, gewölbte Kacheln mit hellem Kern). Kachel-Stile überarbeitet: nur Neon-Kontur hat noch eine volle Umrandung.

## 3.8.4

- **Suchleisten-Navigation per TAB:** Mit TAB und Shift+TAB (sowie den Pfeiltasten) kann nach einer Suche direkt durch die angezeigten Treffer navigiert werden.
- **Taskbar als Seitenleiste (links/rechts):** Die Taskleiste kann nun flexibel am linken oder rechten Bildschirmrand als vertikale Seitenleiste platziert werden (inkl. automatischer Layout-Anpassung und Bildschirmplatz-Reservierung).
- **Nova-Artefakte Fehlerbehebung:** Verhindert das optische Glitchen und Überlappen des „Neues Artefakt“-Buttons mit dem Zurück-Button und dem Titel beim Wechsel in die Artefakt-Detailansicht.
- **Notiz-Widget frei vergrößerbar:** Das Notizzettel-Widget lässt sich über den Eckgriff unten rechts stufenlos nach rechts und nach unten vergrößern und verkleinern, wobei sich das Textfeld dynamisch mit anpasst.
- **Messwert- & Netz-Tempo-Widgets frei skalierbar & sauberes Layout:** Behebt das automatische Anwachsen der Widgets beim Ziehen am Eckgriff; Download- und Upload-Raten sind beim Netz-Tempo nun übersichtlich zusammen unten unter dem freien Verlauf platziert.

## 3.8.3

- **Neuer Hintergrund „Null“:** ein riesiger kalter Mond hinter dreidimensionalen Stacheln, ein weicher Schein, Filmkorn und leichte Farbsäume. „Scherben“ bleibt, wie er ist.
- **Taskbar-Stabilität & Neu laden per Rechtsklick:** verhindert hängende Maus-, Drag- und Fokus-Zustände auf der Taskbar, holt minimierte Fenster zuverlässig zurück in den Vordergrund und setzt die Leiste bei Rechtsklick sowie über „Taskbar neu laden“ sofort zurück.
- **Sidebar-Unterstützung (links/rechts):** ermöglicht im Dev-Build das Platzieren der Taskbar als vertikale Seitenleiste am linken oder rechten Bildschirmrand inkl. AppBar-Reservierung, Rand-Erkennung und vertikalem Drag & Drop.
- **Such-Ranking nach Nutzungshäufigkeit & Claude-Knopf umschaltbar:** oft genutzte Apps mit Suchbegriff im Namen (z. B. „Minecraft Launcher“) werden vor weniger genutzten exakten Treffern priorisiert; der Claude-Knopf kann in Taskbar-Kontextmenü und Einstellungen wie die anderen Hauptbuttons ein- und ausgeschaltet werden.

## 3.8.2

- **Updates laufen durch:** Nach „Jetzt aktualisieren“ installiert das Setup sofort, bleibt dabei sichtbar im Vordergrund und startet PopWin danach von selbst neu. Vorher wartete es versteckt hinter den Leisten auf einen Klick.

## 3.8.1

- **Update-Suche sagt, woran es liegt:** Statt immer „Keine Verbindung“ unterscheidet PopWin jetzt zwischen fehlendem Internet, einer Update-Quelle, die es nicht gibt, zu vielen Anfragen und Störungen bei GitHub. Gibt es noch keine neue Version, heißt es „Kein Update gefunden“. Für Updates braucht niemand ein GitHub-Konto.

## 3.8

- **Uhr an genau einem Platz:** Neuer Tab „Uhr“ in den Einstellungen. Ein Klick ins Bild legt fest, wo Uhrzeit und Datum stehen: Topbar rechts oder Mitte, Taskbar links oder rechts oder schwebend über der Taskbar-Mitte.
- **Taskbar-Icons wie bei Windows:** Store-Apps, Java-Spiele wie Minecraft, Python-Programme und Apps ohne eigenes Icon zeigen jetzt dasselbe Symbol wie in der Windows-Taskleiste. Auch Fenster ohne Titel erscheinen jetzt.
- **Einstellungen als echtes Windows-Fenster:** Einrasten mit Win + Pfeiltasten, Animationen beim Öffnen, Minimieren und Maximieren. Nach dem Maximieren springt das Fenster wieder genau an seinen alten Platz.
- **Win-Taste im Vollbild** holt Taskbar und Home-Menü nach vorn.
- **Autostart repariert:** Die Windows-Taskleiste verschwindet beim Anmelden nicht mehr, und PopWin startet ohne die Verzögerung von Windows.
- **Updates:** PopWin sucht beim Start nach einer neuen Version und zeigt, was sich geändert hat.
- Animierte Hintergründe in voller Schärfe, neue Galaxie- und Geometry-Dash-artige Hintergründe; Wallpaper Engine wird nie mehr von selbst beendet.
- Edge-Web-Apps wie Crunchyroll haben ihr eigenes Icon in der Taskbar.
- **Nova-Artefakte:** Nova merkt sich längere Aufgaben selbst: Aufgaben mit Checkliste und Fälligkeit, Notizen, Überwachungen mit Zeitplan und kleine Werkzeuge. Neuer Tab „Artefakte“ im Nova-Panel mit Karten, Fortschritt, Verlauf und Code; Artefakte lassen sich auch selbst anlegen und bearbeiten. Findet eine Überwachung etwas Neues, zeigt PopWin einen Hinweis.
- **Verbindungen:** Nova richtet mit dir Anbindungen an Dienste wie OVH ein. Die Keys trägst du in PopWin ein, sie bleiben im Windows-Anmeldespeicher; Nova sieht sie nie. Werkzeuge laufen in einem eigenen Prozess, und vor jeder neuen Code-Fassung siehst du den Code.
- **Nova spricht wieder live:** Mit Stimme läuft Nova über Gemini Live, wie früher: Das Mikrofon hört, solange du sprichst, stoppt bei einer Pause von selbst und zeigt live, was es verstanden hat. Stimme und Sprechweise sind wieder einstellbar, und du kannst Nova einfach sagen, wie sie klingen soll.
- **Symbolleiste über der Taskbar** ist jetzt eine Beta für alle. Schwebt auch die Uhr über der Taskbar-Mitte, wählst du, wer unten sitzt, auch direkt im Bild des Uhr-Tabs.
- **Topbar:** Das Akku-Symbol zeigt den echten Füllstand, der Lautsprecher je nach Lautstärke weniger oder mehr Wellen.
- **Widgets** bleiben bei Win + D stehen. Das Notiz-Widget lässt sich wieder sauber größer und kleiner ziehen.
- **PopWin-Apps sind jetzt Beta und ab Werk aus:** Explorer, Editor, Terminal und PowerShell Studio ersetzen die Windows-Apps erst, wenn du sie unter Einstellungen → Beta einschaltest.
- **Ruhiger Start:** PopWin öffnet beim Start keine Fenster mehr, auch nicht beim zweiten Start oder nach einem Neustart. Nur beim allerersten Mal erscheinen die Einstellungen auf der Setup-Seite.
- **Notausgang im Startmenü:** Das Setup legt den Eintrag „Windows-Taskleiste wiederherstellen“ an. Er holt die Windows-Taskleiste zurück, falls sie einmal fehlt.

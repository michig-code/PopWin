"""Rendert die mitgelieferten Hintergründe als nahtlose Video-Schleifen nach assets/wallpapers.

Fertige Videos dekodiert die Grafikkarte, das kostet viel weniger Kraft als jedes Bild live zu berechnen. Jede Bewegung ist
eine ganze Zahl von Schwingungen pro Schleife, deshalb passt das letzte Bild genau wieder ans erste. Braucht ffmpeg im PATH.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen, QPolygonF

OUT = Path(__file__).resolve().parent.parent / "assets" / "wallpapers"
WIDTH, HEIGHT = 2560, 1440  # direkt in voller Größe berechnet und ohne Filter: Hochskalieren machte alles verschmiert
FPS, SECONDS = 30, 20
FRAMES = FPS * SECONDS
HALF = WIDTH // 2
TAU = np.pi * 2
COLUMN = np.arange(WIDTH, dtype=np.float32)
SIDE = np.abs(COLUMN / (WIDTH - 1) * 2 - 1)  # von der Mitte aus gezählt: Alles ist spiegelgleich, auch auf breiten Monitoren
ROW = np.arange(HEIGHT, dtype=np.float32)[:, None]
RANDOM = np.random.default_rng(7)


def flat(colour):
    return np.full((HEIGHT, WIDTH, 3), colour, np.float32)


def add_pixels(picture, x, y, colour):
    """Zeichnet auf zwei benachbarte Zeilen verteilt, das ergibt saubere, glatte Kanten."""
    row = np.floor(y).astype(np.int32)
    part = (y - row)[:, None]
    keep = (row >= 0) & (row < HEIGHT - 1) & (x >= 0) & (x < WIDTH)
    x, row, part = x[keep], row[keep], part[keep]
    amount = colour[keep] if colour.ndim == 2 else colour
    picture[row, x] += amount * (1 - part)
    picture[row + 1, x] += amount * part


def lines(phase):
    picture = flat((6, 8, 20))
    columns = np.arange(WIDTH)
    for number in range(64):
        share = number / 63
        level = HEIGHT * (0.22 + 0.58 * share) + 90 * np.sin(TAU * (1.1 + share) * SIDE + phase + share * 4) \
            + 34 * np.sin(TAU * (2.7 - share) * SIDE - 2 * phase + share * 9)
        colour = np.array((40 + 170 * share, 220 - 150 * share, 190 + 60 * share), np.float32)
        for share_of_step in (0, 1 / 3, 2 / 3):  # steile Stellen bleiben so durchgehende Linien
            add_pixels(picture, columns, level + (np.roll(level, -1) - level) * share_of_step, colour * 0.7)
    return picture


def waves(phase):
    picture = flat((8, 30, 58))
    for layer in range(7):
        share = layer / 6
        edge = HEIGHT * (0.3 + 0.1 * layer) + 46 * np.sin(TAU * (1 + share) * SIDE + (layer + 1) * phase + layer) \
            + 16 * np.sin(TAU * (3 + layer) * SIDE - phase + layer * 2)
        cover = np.clip(ROW - edge[None, :], 0, 1)[..., None]
        colour = np.array((14, 74, 118), np.float32) * (1 - share * 0.85)
        picture = picture * (1 - cover) + colour * cover
    return picture


def cells(seeds):
    """Teilt das Bild in Flächen mit scharfen Kanten. Links gerechnet und gespiegelt, damit beide Seiten genau gleich sind."""
    owner = np.zeros((HEIGHT, HALF), np.int32)
    border = np.zeros((HEIGHT, HALF), np.float32)
    x = np.arange(HALF, dtype=np.float32)[None, :, None]
    for row in range(HEIGHT):
        distance = np.sqrt((x - seeds[:, 0][None, None, :]) ** 2 + (row - seeds[:, 1][None, None, :]) ** 2)[0]
        nearest = np.partition(distance, 1, axis=1)
        owner[row] = distance.argmin(axis=1)
        border[row] = np.clip(2.2 - (nearest[:, 1] - nearest[:, 0]), 0, 1)
    return np.hstack((owner, owner[:, ::-1])), np.hstack((border, border[:, ::-1]))[..., None]


def honeycomb_seeds():
    size = 86
    points = [(HALF - column * size * 1.732 - (row % 2) * size * 0.866, row * size * 1.5)
              for row in range(-1, HEIGHT // 120 + 2) for column in range(-1, HALF // 148 + 3)]
    return np.array(points, np.float32)


COMB, COMB_LINE = None, None
GEM, GEM_LINE = None, None


def honeycomb(phase):
    global COMB, COMB_LINE, COMB_RING
    if COMB is None:
        seeds = honeycomb_seeds()
        COMB, COMB_LINE = cells(seeds)
        COMB_RING = np.hypot(HALF - seeds[:, 0], HEIGHT / 2 - seeds[:, 1]) / 170
    value = ((np.sin(COMB_RING - 2 * phase) + 1) / 2) ** 3
    colours = np.array((9, 13, 30), np.float32) + value[:, None] * np.array((10, 56, 92), np.float32)
    return colours[COMB] * (1 - COMB_LINE) + np.array((26, 38, 66), np.float32) * COMB_LINE


def crystal(phase):
    global GEM, GEM_LINE, GEM_CYCLES, GEM_OFFSET, GEM_LIGHT
    if GEM is None:
        count = 170
        seeds = np.column_stack((RANDOM.uniform(-60, HALF + 60, count), RANDOM.uniform(-60, HEIGHT + 60, count))).astype(np.float32)
        GEM, GEM_LINE = cells(seeds)
        GEM_CYCLES, GEM_OFFSET, GEM_LIGHT = RANDOM.integers(1, 4, count), RANDOM.uniform(0, TAU, count), RANDOM.uniform(0.25, 1, count)
    value = GEM_LIGHT * (0.5 + 0.5 * np.sin(GEM_CYCLES * phase + GEM_OFFSET)) ** 2
    colours = np.array((26, 8, 14), np.float32) + value[:, None] * np.array((190, 62, 34), np.float32)
    return colours[GEM] * (1 - GEM_LINE) + np.array((12, 4, 8), np.float32) * GEM_LINE


STAR_COUNT = 1400
STAR_ANGLE = RANDOM.uniform(0, TAU, STAR_COUNT)
STAR_START = RANDOM.uniform(0, 1, STAR_COUNT)
STAR_CYCLES = RANDOM.integers(1, 4, STAR_COUNT)
STAR_LENGTH = np.hypot(WIDTH, HEIGHT) * 0.56


def stars(phase):
    """Fliegen von der Mitte nach außen. Ein kurzer Strich aus mehreren Punkten macht die Bewegung scharf statt wischig."""
    picture = flat((4, 5, 13))
    progress = (STAR_START + STAR_CYCLES * phase / TAU) % 1
    for step in range(6):
        reach = (progress ** 2.4) * STAR_LENGTH + step * 2 * (0.2 + progress * 2)
        x = (WIDTH / 2 + np.cos(STAR_ANGLE) * reach).astype(np.int32)
        y = HEIGHT / 2 + np.sin(STAR_ANGLE) * reach
        light = np.clip(0.35 + progress * 2.4, 0, 1)[:, None] * np.array((205, 218, 255), np.float32) * (step + 1) / 6
        add_pixels(picture, x, y, light)
        add_pixels(picture, x + 1, y, light)
    return picture


ARMS = 4  # nach einer Vierteldrehung sieht die Galaxie genau gleich aus: So schließt die Schleife nahtlos
DUST = 150000
GALAXY_RANDOM = np.random.default_rng(11)
GALAXY_RADIUS = (14 + GALAXY_RANDOM.exponential(340, DUST // ARMS) % 1180).astype(np.float32)
GALAXY_ANGLE = np.log(GALAXY_RADIUS / 14) * 1.75 + GALAXY_RANDOM.normal(0, 0.1, DUST // ARMS) * (1 + 110 / GALAXY_RADIUS)
GALAXY_TONE = GALAXY_RANDOM.uniform(0, 1, DUST // ARMS)
GALAXY_ANGLE = np.concatenate([GALAXY_ANGLE + arm * TAU / ARMS for arm in range(ARMS)])
GALAXY_RADIUS = np.tile(GALAXY_RADIUS, ARMS)
GALAXY_TONE = np.tile(GALAXY_TONE, ARMS)
VIOLET, CYAN, PINK = np.array((139, 108, 255), np.float32), np.array((0, 183, 255), np.float32), np.array((255, 63, 164), np.float32)  # Novas Farben
GALAXY_COLOUR = VIOLET + (CYAN - VIOLET) * np.clip(GALAXY_RADIUS / 900, 0, 1)[:, None]
GALAXY_COLOUR = np.where((GALAXY_TONE > 0.86)[:, None], PINK, GALAXY_COLOUR)
GALAXY_COLOUR = GALAXY_COLOUR + (255 - GALAXY_COLOUR) * np.clip(1 - GALAXY_RADIUS / 170, 0, 1)[:, None]
GALAXY_LIGHT = (0.55 + 0.45 * GALAXY_TONE ** 3)[:, None]
SKY_COUNT = 900
SKY_X = GALAXY_RANDOM.integers(0, WIDTH - 1, SKY_COUNT)
SKY_Y = GALAXY_RANDOM.uniform(0, HEIGHT - 2, SKY_COUNT)
SKY_CYCLES = GALAXY_RANDOM.integers(1, 5, SKY_COUNT)
SKY_OFFSET = GALAXY_RANDOM.uniform(0, TAU, SKY_COUNT)
SKY_LIGHT = GALAXY_RANDOM.uniform(0.3, 1, SKY_COUNT)
CORE = np.exp(-(((COLUMN - WIDTH / 2) / 92) ** 2)[None, :] - (((ROW - HEIGHT / 2) / 54) ** 2))[..., None]


def galaxy(phase):
    """Lauter einzelne, scharfe Sterne in Novas Farben. Nur der Kern leuchtet weich, wie im alten Nova-Design."""
    picture = flat((5, 4, 16)) + CORE * np.array((255, 235, 255), np.float32) * 1.15
    twinkle = SKY_LIGHT * (0.6 + 0.4 * np.sin(SKY_CYCLES * phase + SKY_OFFSET))
    add_pixels(picture, SKY_X, SKY_Y, twinkle[:, None] * np.array((215, 220, 255), np.float32))
    add_pixels(picture, SKY_X + 1, SKY_Y, twinkle[:, None] * np.array((215, 220, 255), np.float32))
    angle = GALAXY_ANGLE + phase / ARMS
    x = (WIDTH / 2 + np.cos(angle) * GALAXY_RADIUS).astype(np.int32)
    y = HEIGHT / 2 + np.sin(angle) * GALAXY_RADIUS * 0.56
    add_pixels(picture, x, y, GALAXY_COLOUR * GALAXY_LIGHT)
    return picture


def canvas(fill):
    image = QImage(WIDTH, HEIGHT, QImage.Format.Format_RGB888)
    image.fill(QColor(*fill))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    return image, painter


def pixels(image, painter):
    painter.end()
    return np.frombuffer(image.constBits(), np.uint8).reshape(HEIGHT, image.bytesPerLine())[:, :WIDTH * 3].copy()


def polygon(points):
    return QPolygonF([QPointF(x, y) for x, y in points])


def beat(phase, count=20):
    """Ein Puls wie im Takt eines Levels: springt hoch und klingt schnell ab."""
    return (0.5 + 0.5 * np.cos(count * phase)) ** 6


SHARD_RANDOM = np.random.default_rng(23)
SHARD_COUNT = 64
SHARDS = list(zip(SHARD_RANDOM.uniform(0, WIDTH, SHARD_COUNT), SHARD_RANDOM.uniform(0, 1, SHARD_COUNT), SHARD_RANDOM.integers(1, 3, SHARD_COUNT),
                  SHARD_RANDOM.uniform(16, 74, SHARD_COUNT), SHARD_RANDOM.uniform(0, TAU, SHARD_COUNT), SHARD_RANDOM.choice((-1, 1), SHARD_COUNT)))
SPECKS = list(zip(SHARD_RANDOM.uniform(0, WIDTH, 90), SHARD_RANDOM.uniform(0, HEIGHT * 0.8, 90), SHARD_RANDOM.integers(1, 4, 90), SHARD_RANDOM.uniform(0, TAU, 90)))
SUN = QPointF(WIDTH / 2, HEIGHT * 0.66)
SUN_RADIUS = 300


def grass_blades(count, tall, seed):
    random = np.random.default_rng(seed)
    return list(zip(np.linspace(-80, WIDTH + 80, count) + random.uniform(-40, 40, count), random.uniform(46, 120, count), random.uniform(0.55, 1, count) * tall,
                    random.uniform(-110, 110, count), random.integers(1, 3, count), random.uniform(0, TAU, count)))


GRASS = ((grass_blades(90, 420, 3), QColor(56, 66, 92)), (grass_blades(120, 330, 4), QColor(34, 42, 66)), (grass_blades(150, 230, 5), QColor(16, 20, 36)))


def shard_points(x, y, size, turn):
    return [(x + np.cos(turn + angle) * size * reach, y + np.sin(turn + angle) * size * reach)
            for angle, reach in ((0, 1.0), (2.75, 0.38), (3.6, 0.5))]


def beam(phase, width):
    wander = [(y, SUN.x() + 46 * np.sin(y / 300 + phase) + 22 * np.sin(y / 120 - 2 * phase)) for y in range(-20, int(SUN.y()) + 40, 30)]
    return polygon([(x - width, y) for y, x in wander] + [(x + width, y) for y, x in reversed(wander)])


MOON = QPointF(WIDTH / 2, HEIGHT * 0.6)
MOON_RADIUS = 540


def spike_field(count, tall, seed):
    """Lange, dünne Stacheln, die schräg aus dem Boden ragen; vorn höher und dunkler, hinten heller."""
    random = np.random.default_rng(seed)
    near_center = random.normal(0, 0.22, count)  # die meisten stehen vor dem Mond
    looks = list(zip(random.uniform(0.7, 1.3, count), random.uniform(-0.45, 0.45, count), random.uniform(0, 1, count) < 0.25,
                     random.uniform(-0.07, 0.07, count), random.uniform(0.45, 1.15, count)))  # Farbton und Sättigung je Stachel
    return list(zip(WIDTH * (0.5 + np.clip(near_center, -0.62, 0.62)), random.uniform(80, 190, count), random.uniform(0.55, 1, count) * tall,
                    random.uniform(-0.62, 0.62, count), random.integers(1, 3, count), random.uniform(0, TAU, count), looks))


SPIKES = ((spike_field(16, 520, 31), QColor(44, 54, 104), QColor(120, 140, 220)),
          (spike_field(14, 700, 32), QColor(24, 30, 66), QColor(96, 116, 200)),
          (spike_field(12, 860, 33), QColor(10, 13, 32), QColor(70, 88, 170)))
VIGNETTE = np.clip(1.15 - 0.95 * np.hypot((COLUMN[None, :] - WIDTH / 2) / (WIDTH * 0.62), (ROW - HEIGHT * 0.56) / (HEIGHT * 0.66)) ** 1.6,
                   0.08, 1)[..., None].astype(np.float32)
GRAIN = [np.random.default_rng(40 + index).normal(0, 6, (HEIGHT // 2, WIDTH // 2, 1)).repeat(2, 0).repeat(2, 1).astype(np.float32)
         for index in range(1)]  # ein festes Muster: Wechselndes Korn machte das Video neunmal so groß
EDGE = (SIDE ** 3)[None, :].astype(np.float32)  # Farbsäume nur zum Rand hin, wie bei einem alten Objektiv


GLOW_SCALE = 8  # der Schein wird klein berechnet und weich hochgezogen: weich und schnell
GLOW_COLOUR = np.array((150, 175, 255), np.float32)
GLOW_STRENGTH = 0.42


def box_blur(array, radius):
    """Weichzeichnen ohne scipy: Mittelwert über ein Fenster, je Achse über kumulierte Summen."""
    for axis in (0, 1):
        padded = np.pad(array, [(radius + 1, radius) if index == axis else (0, 0) for index in range(2)], mode="edge")
        summed = np.cumsum(padded, axis=axis)
        array = (np.take(summed, range(2 * radius + 1, summed.shape[axis]), axis=axis)
                 - np.take(summed, range(0, summed.shape[axis] - 2 * radius - 1), axis=axis)) / (2 * radius + 1)
    return array


def moon_glow(phase, pulse):
    """Der weiche Schein, der nur von Mond und Lichtstrom ausgeht. Dreimal weichgezeichnet sieht es aus wie ein echtes Leuchten."""
    small = QImage(WIDTH // GLOW_SCALE, HEIGHT // GLOW_SCALE, QImage.Format.Format_Grayscale8)
    small.fill(0)
    painter = QPainter(small)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(1 / GLOW_SCALE, 1 / GLOW_SCALE)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 255, 255))
    painter.drawEllipse(MOON, MOON_RADIUS + 5 * pulse, MOON_RADIUS + 5 * pulse)
    painter.drawPolygon(beam(phase, 92))
    painter.end()
    mask = np.frombuffer(small.constBits(), np.uint8).reshape(small.height(), small.bytesPerLine())[:, :small.width()]
    mask = mask.astype(np.float32) / 255
    for _ in range(3):
        mask = box_blur(mask, 20)
    large = QImage(np.ascontiguousarray((mask * 255).astype(np.uint8)).tobytes(), small.width(), small.height(), small.width(),
                   QImage.Format.Format_Grayscale8).scaled(WIDTH, HEIGHT, Qt.AspectRatioMode.IgnoreAspectRatio,
                                                           Qt.TransformationMode.SmoothTransformation)
    full = np.frombuffer(large.constBits(), np.uint8).reshape(HEIGHT, large.bytesPerLine())[:, :WIDTH].astype(np.float32) / 255
    return full[..., None] * GLOW_COLOUR * GLOW_STRENGTH


def tinted(colour, hue, saturation, value):
    """Dieselbe Grundfarbe, je Stachel etwas mehr ins Violette, Stahlgraue oder Cyan, heller oder dunkler."""
    h, s, v, _a = QColor(colour).getHsvF()
    return QColor.fromHsvF((h + hue) % 1, min(1.0, s * saturation), min(1.0, v * value))


def paint_spike(painter, x, width, tall, lean, sway, body, rim, look):
    """Eine Pyramide aus zwei Flächen: Die zum Mond liegt im Licht und wird zur Spitze hin heller, die andere im Schatten.
    Jeder Stachel hat seine eigene Helligkeit, Gratlage und manchmal die Lichtseite vertauscht, damit nicht alle gleich aussehen."""
    tone, offset, flipped, hue, saturation = look
    tip = QPointF(x + np.sin(lean) * tall + sway, HEIGHT - np.cos(lean) * tall)
    near = max(0.0, 1 - abs(tip.x() - MOON.x()) / (MOON_RADIUS * 1.3))  # vor dem Mond: Lichtseite fast weiß
    body, rim = tinted(body, hue, saturation, tone), tinted(rim, hue, saturation * (1 - 0.6 * near), tone * (1 + 0.45 * near))
    ridge = QPointF(x + width * offset, HEIGHT + 6)
    left, right = QPointF(x - width, HEIGHT + 6), QPointF(x + width, HEIGHT + 6)
    lit_left = (x > MOON.x()) != flipped  # rechts vom Mond fällt das Licht meist auf die linke Fläche
    shade = QLinearGradient(tip, ridge)
    shade.setColorAt(0, body.darker(115))
    shade.setColorAt(1, body.darker(170))
    light = QLinearGradient(tip, ridge)
    light.setColorAt(0, rim.lighter(118))
    light.setColorAt(0.45, rim)
    light.setColorAt(1, body)
    lit, dark = ((left, ridge), (ridge, right)) if lit_left else ((ridge, right), (left, ridge))
    painter.setBrush(shade)
    painter.drawPolygon(QPolygonF([dark[0], tip, dark[1]]))
    painter.setBrush(light)
    painter.drawPolygon(QPolygonF([lit[0], tip, lit[1]]))


def shards(phase):
    """Ein Lichtstrahl fällt auf eine Sonne, Splitter steigen auf, und davor wiegt sich gezacktes Gras. Harte Ringe statt Weichzeichnen."""
    pulse = beat(phase, 10)
    image, painter = canvas((30, 37, 58))
    painter.setPen(Qt.PenStyle.NoPen)
    for ring in range(14, 0, -1):
        shade = 1 - ring / 14
        painter.setBrush(QColor(int(32 + 92 * shade ** 2), int(40 + 92 * shade ** 2), int(62 + 88 * shade ** 2)))
        radius = SUN_RADIUS + ring * 118 + 10 * pulse
        painter.drawEllipse(SUN, radius, radius)
    for ray in range(16):
        angle = ray * TAU / 16 + phase / 8  # eine Sechzehntelrunde: danach steht der Kranz wieder genau gleich
        spread = 0.035
        painter.setBrush(QColor(255, 255, 255, 22))
        painter.drawPolygon(polygon((((SUN.x(), SUN.y())), (SUN.x() + np.cos(angle - spread) * 1500, SUN.y() + np.sin(angle - spread) * 1500),
                                     (SUN.x() + np.cos(angle + spread) * 1500, SUN.y() + np.sin(angle + spread) * 1500))))
    painter.setBrush(QColor(255, 255, 255, 60))
    painter.drawPolygon(beam(phase, 190))
    painter.setBrush(QColor(246, 242, 228))
    painter.drawEllipse(SUN, SUN_RADIUS + 6 * pulse, SUN_RADIUS + 6 * pulse)
    painter.drawPolygon(beam(phase, 104))
    for x, y, cycles, offset in SPECKS:
        painter.setBrush(QColor(235, 238, 248, int(120 + 110 * np.sin(cycles * phase + offset) ** 2)))
        painter.drawEllipse(QPointF(float(x), float(y)), 3, 3)
    painter.setBrush(QColor(11, 14, 26))
    for x, start, cycles, size, turn, direction in SHARDS:
        rise = (start + cycles * phase / TAU) % 1
        y = HEIGHT + 120 - rise * (HEIGHT + 240)
        painter.drawPolygon(polygon(shard_points(x + 34 * np.sin(phase + turn), y, size, turn + direction * phase)))
    for blades, colour in GRASS:
        painter.setBrush(colour)
        for x, width, tall, lean, cycles, offset in blades:
            sway = 26 * np.sin(cycles * phase + offset)
            painter.drawPolygon(polygon(((x - width, HEIGHT + 4), (x + lean + sway, HEIGHT - tall), (x + width, HEIGHT + 4))))
    return pixels(image, painter)


def moon(phase):
    """Wie „scherben“, nur düsterer: Ein riesiger kalter Mond hinter schwarzen Stacheln, ein Lichtstrom fällt hinein, Splitter treiben auf. Dazu Vignette,
    Filmkorn und feine Farbsäume. Alle Bewegungen schließen nach einer Schleife nahtlos."""
    pulse = beat(phase, 10)
    image, painter = canvas((8, 11, 28))
    painter.setPen(Qt.PenStyle.NoPen)
    for ring in range(12, 0, -1):  # blauer Schein um den Mond, in harten Stufen statt Weichzeichnen
        glow = (1 - ring / 12) ** 2.4
        painter.setBrush(QColor(int(10 + 70 * glow), int(14 + 86 * glow), int(34 + 150 * glow)))
        radius = MOON_RADIUS + ring * 70 + 8 * pulse
        painter.drawEllipse(MOON, radius, radius)
    painter.setBrush(QColor(170, 190, 255, 70))
    painter.drawPolygon(beam(phase, 150))
    painter.setBrush(QColor(236, 240, 255))
    painter.drawEllipse(MOON, MOON_RADIUS + 5 * pulse, MOON_RADIUS + 5 * pulse)
    painter.drawPolygon(beam(phase, 92))
    for x, y, cycles, offset in SPECKS:
        painter.setBrush(QColor(200, 214, 255, int(60 + 120 * np.sin(cycles * phase + offset) ** 2)))
        painter.drawEllipse(QPointF(float(x), float(y)), 2.5, 2.5)
    painter.setBrush(QColor(12, 16, 40))
    for x, start, cycles, size, turn, direction in SHARDS:
        rise = (start + cycles * phase / TAU) % 1
        y = HEIGHT + 120 - rise * (HEIGHT + 240)
        painter.drawPolygon(polygon(shard_points(x + 34 * np.sin(phase + turn), y, size * 1.4, turn + direction * phase)))
    painter.setBrush(QColor(14, 18, 44))  # dunkler Grund: Vom Mond bleibt unten nichts zu sehen, wie hinter einem Hügel aus Stacheln
    painter.drawPolygon(polygon([(0, HEIGHT + 10)] + [(x, HEIGHT - 330 - 90 * np.cos(x / WIDTH * TAU * 3) + 60 * np.cos(x / WIDTH * TAU * 7))
                                                       for x in np.linspace(0, WIDTH, 40)] + [(WIDTH, HEIGHT + 10)]))
    for spikes, body, rim in SPIKES:
        for x, width, tall, lean, cycles, offset, look in spikes:
            paint_spike(painter, x, width, tall, lean, 18 * np.sin(cycles * phase + offset), body, rim, look)
    picture = (pixels(image, painter).reshape(HEIGHT, WIDTH, 3).astype(np.float32) + moon_glow(phase, pulse)) * VIGNETTE
    shift = np.round(2 + EDGE * 4).astype(np.int32)[0]  # Rot nach außen, Blau nach innen: überall leicht, am Rand stärker
    columns = np.arange(WIDTH)
    outward = np.where(columns < HALF, 1, -1)
    picture[..., 0] = picture[:, np.clip(columns + outward * shift, 0, WIDTH - 1), 0]
    picture[..., 2] = picture[:, np.clip(columns - outward * shift, 0, WIDTH - 1), 2]
    frame = int(round(phase / TAU * FRAMES)) % len(GRAIN)
    return picture + GRAIN[frame]


def mountains(count, tile, low, high, seed):
    """Zweiseitige Gipfel, die sich nach einer Kachel wiederholen. So läuft die Landschaft ohne Sprung durch."""
    random = np.random.default_rng(seed)
    return [(x, random.uniform(low, high), random.uniform(260, 520), random.uniform(-0.2, 0.2)) for x in np.linspace(0, tile, count, endpoint=False)]


TILES = ((WIDTH // 2, mountains(5, WIDTH // 2, 300, 520, 8), (178, 180, 182), (146, 148, 150)),
         (WIDTH, mountains(7, WIDTH, 210, 430, 9), (138, 140, 142), (104, 106, 108)),
         (WIDTH * 2, mountains(9, WIDTH * 2, 120, 300, 10), (92, 94, 96), (58, 60, 62)))
HORIZON = HEIGHT * 0.78
PILLAR_TILE = WIDTH * 2
PILLARS = list(zip(np.linspace(0, PILLAR_TILE, 11, endpoint=False), (760, 420, 900, 330, 640, 980, 520, 300, 820, 460, 700), (0, 1, 0, 1, 0, 0, 1, 1, 0, 1, 0)))
PILLAR_BLOCK = 92


def paint_pillar(painter, x, length, hanging):
    """Schwarze Pfeiler mit Pfeilspitze, von unten oder von oben."""
    w = PILLAR_BLOCK / 2
    painter.setBrush(QColor(8, 8, 10))
    if hanging:
        painter.drawRect(QRectF(x - 7, -4, 14, length))
        painter.drawPolygon(polygon(((x - w, length - 60), (x + w, length - 60), (x + w, length + 12), (x, length + 48), (x - w, length + 12))))
    else:
        top = HEIGHT - length
        painter.drawRect(QRectF(x - 7, top, 14, length))
        painter.drawPolygon(polygon(((x - w, top + 60), (x + w, top + 60), (x + w, top - 12), (x, top - 48), (x - w, top - 12))))


def summits(phase):
    """Graue Berge in drei Ebenen, davor ziehen schwarze Pfeiler langsam vorbei."""
    image, painter = canvas((214, 216, 218))
    sky = QLinearGradient(0, 0, 0, HORIZON)
    sky.setColorAt(0, QColor(226, 228, 232))
    sky.setColorAt(1, QColor(190, 192, 196))
    painter.fillRect(0, 0, WIDTH, int(HORIZON), sky)
    painter.setPen(Qt.PenStyle.NoPen)
    for tile, peaks, light, dark in TILES:
        shift = phase / TAU * tile
        for repeat in range(-1, WIDTH // tile + 2):
            for x, tall, reach, lean in peaks:
                peak_x = x + repeat * tile - shift
                top = (peak_x + lean * tall, HORIZON - tall)
                foot = (peak_x + lean * tall * 0.3, HORIZON + 4)
                painter.setBrush(QColor(*light))
                painter.drawPolygon(polygon(((peak_x - reach, HORIZON + 4), top, foot)))
                painter.setBrush(QColor(*dark))
                painter.drawPolygon(polygon((top, (peak_x + reach, HORIZON + 4), foot)))
    ground = QLinearGradient(0, HORIZON, 0, HEIGHT)
    ground.setColorAt(0, QColor(60, 60, 62))
    ground.setColorAt(0.45, QColor(10, 10, 12))
    painter.fillRect(0, int(HORIZON), WIDTH, HEIGHT - int(HORIZON), ground)
    shift = phase / TAU * PILLAR_TILE
    for repeat in range(-1, 2):
        for x, length, hanging in PILLARS:
            paint_pillar(painter, x + repeat * PILLAR_TILE - shift, length, hanging)
    return pixels(image, painter)


BLOCK = 90
DASH_LOOP = BLOCK * 32
GROUND = 1120
DASH_SPIKES = (5, 6, 13, 21, 22, 23, 29)
DASH_BLOCKS = ((9, 1), (10, 1), (10, 2), (17, 1), (18, 1), (18, 2), (19, 1), (26, 1), (26, 2), (26, 3))


def spike(x, ground, height, direction=1):
    return polygon(((x, ground), (x + height * 0.5, ground - direction * height), (x + height, ground)))


def dash(phase):
    """Ein eigenes kleines Level ohne Spieler: Boden, Stacheln und Blöcke ziehen im Takt vorbei."""
    pulse = beat(phase)
    image, painter = canvas((6, 10, 44))
    sky = QLinearGradient(0, 0, 0, GROUND)
    sky.setColorAt(0, QColor(4, 8, 34))
    sky.setColorAt(1, QColor(14, 44, 120))
    painter.fillRect(0, 0, WIDTH, GROUND, sky)
    shift = phase / TAU * DASH_LOOP
    painter.setPen(QPen(QColor(50, 90, 200, int(70 + 120 * pulse)), 3))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    square = BLOCK * 3
    for column in range(-1, WIDTH // square + 2):
        for row in range(0, GROUND // square + 1):
            painter.drawRect(QRectF(column * square - (shift * 3 / 16) % square, row * square, square - 14, square - 14))  # genau zwei Felder je Schleife
    painter.setPen(QPen(QColor(255, 255, 255), 4))
    for block in range(-DASH_LOOP // BLOCK, (WIDTH + DASH_LOOP) // BLOCK + 1):
        index = block % 32
        x = block * BLOCK - shift
        if index in DASH_SPIKES:
            painter.setBrush(QColor(0, 0, 0))
            painter.drawPolygon(spike(x, GROUND, BLOCK))
        for place, height in DASH_BLOCKS:
            if place == index:
                painter.setBrush(QColor(8, 8, 26))
                painter.drawRect(QRectF(x, GROUND - height * BLOCK, BLOCK, BLOCK))
    painter.fillRect(0, GROUND, WIDTH, HEIGHT - GROUND, QColor(5, 10, 40))
    painter.setPen(QPen(QColor(90, 120, 220, 110), 3))
    for column in range(-1, WIDTH // (BLOCK * 2) + 2):
        x = column * BLOCK * 2 - shift % (BLOCK * 2)
        painter.drawLine(QPointF(x, GROUND), QPointF(x - 120, HEIGHT))
    painter.setPen(QPen(QColor(0, 220, 255), 5 + 4 * pulse))
    painter.drawLine(0, GROUND, WIDTH, GROUND)
    return pixels(image, painter)


def saw(painter, x, y, radius, turn, colour):
    points = []
    for tooth in range(14):
        angle = turn + tooth * TAU / 14
        points.append((x + np.cos(angle) * radius, y + np.sin(angle) * radius))
        points.append((x + np.cos(angle + 0.14) * radius * 0.78, y + np.sin(angle + 0.14) * radius * 0.78))
    painter.setPen(QPen(colour, 4))
    painter.setBrush(QColor(8, 0, 2))
    painter.drawPolygon(polygon(points))
    painter.drawEllipse(QPointF(x, y), radius * 0.46, radius * 0.46)
    painter.setBrush(colour)
    painter.drawEllipse(QPointF(x, y), radius * 0.12, radius * 0.12)


def saws(phase):
    """Rot und Schwarz: Sägeblätter, und oben und unten ziehen gezackte Spitzen vorbei."""
    pulse = beat(phase)
    image, painter = canvas((0, 0, 0))
    sky = QLinearGradient(0, 0, 0, HEIGHT)
    for place, shade in ((0, 16), (0.5, int(70 + 60 * pulse)), (1, 16)):
        sky.setColorAt(place, QColor(shade, 0, 6))
    painter.fillRect(0, 0, WIDTH, HEIGHT, sky)
    for layer, (step, tall, cycles, colour) in enumerate(((240, 300, 1, QColor(46, 0, 8)), (160, 200, -2, QColor(6, 0, 2)))):
        offset = cycles * phase / TAU * step * 5  # die Zacken wiederholen sich alle fünf: So passt das Ende genau zum Anfang
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        for tooth in range(-12, WIDTH // step + 12):
            height = tall * (0.6 + 0.4 * abs(np.sin((tooth % 5) * 2.1 + layer)))
            x = tooth * step + offset
            painter.drawPolygon(polygon(((x, HEIGHT), (x + step / 2, HEIGHT - height), (x + step, HEIGHT))))
            painter.drawPolygon(polygon(((x, 0), (x + step / 2, height), (x + step, 0))))
    red = QColor(255, 36, 48)
    for place, radius, cycles in ((0.5, 300, 1), (0.2, 190, -2), (0.8, 190, 2)):
        saw(painter, WIDTH * place, HEIGHT / 2, radius * (1 + 0.03 * pulse), cycles * phase * 2 / 14 * 7, red)
    return pixels(image, painter)


LOOP_FRAMES = {"galaxie": FPS * 30, "gipfel": FPS * 30}  # eine Vierteldrehung darf ruhig länger dauern
SCENES = {"galaxie": galaxy, "linien": lines, "wellen": waves, "waben": honeycomb, "kristall": crystal, "sterne": stars, "scherben": shards, "gipfel": summits, "dash": dash, "saegen": saws, "null": moon}


def ffmpeg_path() -> str:
    """ffmpeg aus dem PATH, sonst das aus dem Paket imageio-ffmpeg (pip install imageio-ffmpeg)."""
    import shutil
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def render(name: str, scene) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{name}.mp4"
    command = [ffmpeg_path(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-",
               "-c:v", "libx264", "-preset", "slow", "-tune", "animation", "-crf", "25", "-g", "60",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(target)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    frames = LOOP_FRAMES.get(name, FRAMES)
    for frame in range(frames):
        picture = scene(TAU * frame / frames)
        process.stdin.write(np.clip(picture, 0, 255).astype(np.uint8).tobytes())
    process.stdin.close()
    process.wait()
    print(name, round(target.stat().st_size / 1024 ** 2, 1), "MB", flush=True)


if __name__ == "__main__":
    for key in sys.argv[1:] or SCENES:
        render(key, SCENES[key])

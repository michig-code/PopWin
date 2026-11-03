"""Novas Augen fürs Schachbrett. Sie lesen auf diesem PC und kosten deshalb keine Tokens.

Früher ging jede Stellung als Bild an die KI; nach einiger Zeit war das Tageslimit erreicht und das Lesen blieb stehen.
Jetzt sucht Nova das Brett selbst, merkt sich die Stellung und erkennt Züge am Vergleich der Felder. Die KI sieht das Brett
nur einmal, wenn eine Partie mitten im Spiel beginnt oder ein Zug wirklich nicht zu erkennen ist.
"""
from __future__ import annotations

import json
import time

import chess
import numpy as np

FIRST_STEP = 640  # so breit wird das Bild für die grobe Suche
MIN_CONTRAST = 10
SIGN_SIZE = 16
PIECE_DELTA = 26  # so weit weicht eine Figur vom Feld ab
OCCUPIED = 0.05
CHANGED = 0.03
FADED = 0.2  # die Markierung des letzten Zugs verschwindet: Die Figur bleibt, nur ein paar Randpunkte ändern sich
MAX_MOVES = 2  # so viele Züge dürfen zwischen zwei Lesungen liegen
SETTLE_SECONDS = 0.12
SETTLE_TRIES = 8
VISION_SIZE = 640
VISION_TIMEOUT_MS = 15000
PROMPT = ("This is a 2D chessboard screenshot. Report every square exactly as displayed, from the top row to the bottom row, "
          "left to right. Use FEN letters (uppercase white, lowercase black) and '.' for empty. "
          "Also say which color's home side is at the bottom.")


def to_gray(bgra: bytes, width: int, height: int) -> np.ndarray:
    pixels = np.frombuffer(bgra, np.uint8).reshape(height, width, 4)
    return pixels[..., 0] * 0.114 + pixels[..., 1] * 0.587 + pixels[..., 2] * 0.299


def _integral(gray: np.ndarray) -> np.ndarray:
    return np.pad(gray.astype(np.float64).cumsum(0).cumsum(1), ((1, 0), (1, 0)))


def _lattice_score(table: np.ndarray, xs: np.ndarray, ys: np.ndarray, size: int) -> tuple[float, int, int]:
    """Wie sehr sehen die Felder an diesen Stellen nach Schachbrett aus? Figuren stört der Median nicht."""
    offsets = np.round(np.arange(9) * size / 8).astype(int)
    grid_y, grid_x = np.meshgrid(ys, xs, indexing="ij")
    corners = table[grid_y[None, None] + offsets[:, None, None, None], grid_x[None, None] + offsets[None, :, None, None]]
    areas = (offsets[1:, None] - offsets[:-1, None]) * (offsets[None, 1:] - offsets[None, :-1])
    means = (corners[1:, 1:] - corners[:-1, 1:] - corners[1:, :-1] + corners[:-1, :-1]) / areas[..., None, None]
    light = np.add.outer(np.arange(8), np.arange(8)) % 2 == 0
    first, second = means[light], means[~light]
    low, high = np.median(first, axis=0), np.median(second, axis=0)
    spread = (np.abs(first - low).mean(axis=0) + np.abs(second - high).mean(axis=0)) / 2
    scores = np.abs(low - high) - spread
    scores[np.abs(low - high) < MIN_CONTRAST] = -1
    row, column = np.unravel_index(int(scores.argmax()), scores.shape)
    return float(scores[row, column]), int(xs[column]), int(ys[row])


def _ranges(limit: int, stride: int) -> np.ndarray:
    return np.arange(0, max(1, limit), max(1, stride))


def find_board(gray: np.ndarray) -> tuple[int, int, int] | None:
    """(x, y, Seitenlänge) des größten Bretts im Bild, sonst None."""
    step = max(1, gray.shape[1] // FIRST_STEP)
    small = gray[::step, ::step]
    table = _integral(small)
    height, width = small.shape
    best = (0.0, 0, 0, 0)
    for size in range(96, min(height, width) + 1, 8):
        stride = max(1, size // 32)
        score, x, y = _lattice_score(table, _ranges(width - size + 1, stride), _ranges(height - size + 1, stride), size)
        if score > best[0]:
            best = (score, x, y, size)
    if best[0] <= 0:
        return None
    _, x, y, size = best
    x, y, size = x * step, y * step, size * step
    margin = 8 * step + size // 32
    left, top = max(0, x - margin), max(0, y - margin)
    crop = gray[top:y + size + margin + 16 * step, left:x + size + margin + 16 * step]
    table = _integral(crop)
    finest = (0.0, x, y, size)
    for fine in range(max(96, size - 8 * step), size + 8 * step + 1):
        room_x, room_y = crop.shape[1] - fine + 1, crop.shape[0] - fine + 1
        if room_x <= 0 or room_y <= 0:
            continue
        score, found_x, found_y = _lattice_score(table, _ranges(room_x, 1), _ranges(room_y, 1), fine)
        if score > finest[0]:
            finest = (score, found_x + left, found_y + top, fine)
    return finest[1:]


def still_there(gray: np.ndarray, box: tuple[int, int, int]) -> bool:
    x, y, size = box
    if x + size >= gray.shape[1] or y + size >= gray.shape[0]:
        return False
    score, _, _ = _lattice_score(_integral(gray[y:y + size + 1, x:x + size + 1]), np.array([0]), np.array([0]), size)
    return score > 0


def read_cells(gray: np.ndarray, box: tuple[int, int, int]) -> np.ndarray:
    """Pro Feld, von oben links, die Form dessen, was auf ihm steht. Die Feldfarbe zählt nicht, Markierungen des letzten
    Zugs färben also nur den Hintergrund und gelten nicht als Änderung."""
    x, y, size = box
    edges = np.round(np.arange(9) * size / 8).astype(int)
    signs = np.zeros((8, 8, SIGN_SIZE, SIGN_SIZE), bool)
    for row in range(8):
        for column in range(8):
            cell = gray[y + edges[row]:y + edges[row + 1], x + edges[column]:x + edges[column + 1]]
            height, width = cell.shape
            ground = np.median([cell[1, 1], cell[1, -2], cell[-2, 1], cell[-2, -2]])
            inner_y = np.linspace(height * 0.1, height * 0.9 - 1, SIGN_SIZE).astype(int)
            inner_x = np.linspace(width * 0.1, width * 0.9 - 1, SIGN_SIZE).astype(int)
            signs[row, column] = np.abs(cell[np.ix_(inner_y, inner_x)] - ground) > PIECE_DELTA
    return signs


def occupied(signs: np.ndarray) -> np.ndarray:
    return signs.mean(axis=(2, 3)) > OCCUPIED


def square_at(row: int, column: int, white_below: bool) -> int:
    return chess.square(column, 7 - row) if white_below else chess.square(7 - column, row)


def looks_like_start(signs: np.ndarray) -> bool:
    filled = occupied(signs)
    return bool(filled[[0, 1, 6, 7]].all() and not filled[2:6].any())


def bright_below(signs: np.ndarray, gray: np.ndarray, box: tuple[int, int, int]) -> bool:
    """Stehen die helleren Figuren unten? In der Grundstellung zeigt das, welche Seite man spielt."""
    x, y, size = box

    def brightness(row: int) -> float:
        """Nur auf den dunklen Feldern: Auf den hellen ginge eine weiße Figur im Hintergrund unter."""
        seen = []
        for column in range(8):
            left, top = x + column * size // 8, y + row * size // 8
            cell = gray[top:top + size // 8, left:left + size // 8]
            ground = float(np.median([cell[1, 1], cell[1, -2], cell[-2, 1], cell[-2, -2]]))
            seen.append((ground, float(cell[np.abs(cell - ground) > PIECE_DELTA].mean())))
        return sum(value for _, value in sorted(seen)[:4])
    return brightness(7) > brightness(0)


def touched(board: chess.Board, move: chess.Move) -> set[int]:
    squares = {move.from_square, move.to_square}
    if board.is_castling(move):
        rank = chess.square_rank(move.from_square)
        kingside = chess.square_file(move.to_square) > chess.square_file(move.from_square)
        squares |= {chess.square(7 if kingside else 0, rank), chess.square(5 if kingside else 3, rank)}
    if board.is_en_passant(move):
        squares.add(chess.square(chess.square_file(move.to_square), chess.square_rank(move.from_square)))
    return squares


def find_moves(board: chess.Board, changed: set[int], filled: set[int]) -> list[list[chess.Move]]:
    """Alle Zugfolgen, die genau diese Änderung erklären. Kürzere gewinnen; mehr als eine heißt: nicht eindeutig."""
    matches: dict[int, list[list[chess.Move]]] = {}

    def walk(position: chess.Board, path: list[chess.Move], seen: set[int]) -> None:
        if path and changed <= seen and set(position.piece_map()) == filled:
            matches.setdefault(len(path), []).append(path)
        if len(path) == MAX_MOVES:
            return
        for move in position.legal_moves:
            squares = touched(position, move)
            if move.promotion in (None, chess.QUEEN) and move.from_square in changed | seen and squares & changed:
                following = position.copy(stack=False)
                following.push(move)
                walk(following, path + [move], seen | squares)
    walk(board, [], set())
    return matches[min(matches)] if matches else []


def ascii_board(board: chess.Board, white_below: bool) -> str:
    rows = [" ".join(row.split()) for row in str(board).splitlines()]
    return "\n".join(rows if white_below else [row[::-1] for row in reversed(rows)])


class ChessEyes:
    def __init__(self, grab, vision):
        self.grab = grab  # () -> Graustufenbild eines Monitors; nacheinander alle, bis ein Brett gefunden ist
        self.vision = vision  # (JPEG-Bytes) -> dict mit rows und bottom; nur für den seltenen Notfall
        self.box: tuple[int, int, int] | None = None
        self.frame: np.ndarray | None = None
        self.board: chess.Board | None = None
        self.white_below = True
        self.signs: np.ndarray | None = None
        self.vision_calls = 0
        self.recent: set[int] = set()  # dort leuchtet noch die Markierung des letzten Zugs

    def forget(self) -> None:
        self.box = self.board = self.signs = None

    def _locate(self) -> np.ndarray | None:
        for gray in self.grab():
            if self.box is not None and still_there(gray, self.box):
                return gray
            box = find_board(gray)
            if box is not None:
                self.box = box
                self.board = self.signs = None
                return gray
        self.forget()
        return None

    def _settled(self) -> np.ndarray | None:
        """Wartet, bis keine Figur mehr unterwegs ist."""
        gray = self._locate()
        previous = read_cells(gray, self.box) if gray is not None else None
        for _ in range(SETTLE_TRIES):
            if gray is None:
                return None
            time.sleep(SETTLE_SECONDS)
            gray = self._locate()
            if gray is None:
                return None
            signs = read_cells(gray, self.box)
            if (signs ^ previous).mean() < 0.002:
                self.frame = gray
                return signs
            previous = signs
        self.frame = gray
        return previous

    def _changed(self, signs: np.ndarray) -> set[int]:
        difference = (signs ^ self.signs).mean(axis=(2, 3))
        stayed = occupied(signs) & occupied(self.signs)
        changed = set()
        for row, column in zip(*np.nonzero(difference > CHANGED)):
            square = square_at(row, column, self.white_below)
            if not (square in self.recent and stayed[row, column] and difference[row, column] < FADED):
                changed.add(square)
        return changed

    def _filled(self, signs: np.ndarray) -> set[int]:
        return {square_at(row, column, self.white_below) for row, column in zip(*np.nonzero(occupied(signs)))}

    def _ask_vision(self, side: str) -> None:
        self.vision_calls += 1
        answer = self.vision(self.frame, self.box)
        rows, bottom = answer["rows"], answer.get("bottom", "white")
        if len(rows) != 8 or any(len(row) != 8 for row in rows):
            raise ValueError("Das Brett kam nicht vollständig zurück. Versuch es bitte noch einmal.")
        self.white_below = bottom != "black"
        board = chess.Board(None)
        for row, text in enumerate(rows):
            for column, letter in enumerate(text):
                if letter != ".":
                    board.set_piece_at(square_at(row, column, self.white_below), chess.Piece.from_symbol(letter))
        board.turn = (side or ("white" if self.white_below else "black")) == "white"
        board.clean_castling_rights()
        self.board = board

    def read(self, new_game: bool = False, side_to_move: str = "") -> dict:
        if new_game:
            self.forget()
        signs = self._settled()
        if signs is None:
            return {"found": False}
        moves: list[str] = []
        how = "lokal, ohne KI"
        if self.board is None or self.signs is None:
            if looks_like_start(signs):
                self.white_below = bright_below(signs, self.frame, self.box)
                self.board = chess.Board()
            else:
                self._ask_vision(side_to_move)
                how = "einmal per KI-Bild"
        else:
            options = find_moves(self.board, self._changed(signs), self._filled(signs))
            if not options and self._changed(signs):  # Zugrecht falsch geraten, etwa nach Einstieg mitten im Spiel
                flipped = self.board.copy(stack=False)
                flipped.turn = not flipped.turn
                options = find_moves(flipped, self._changed(signs), self._filled(signs))
                if len(options) == 1:
                    self.board = flipped
            if len(options) == 1:
                self.recent = set()
                for move in options[0]:
                    moves.append(self.board.san(move))
                    self.recent = {move.from_square, move.to_square}
                    self.board.push(move)
            elif self._changed(signs):
                self._ask_vision(side_to_move)
                how = "einmal per KI-Bild, der Zug war nicht eindeutig"
        self.signs = signs
        return {"found": True, "fen": self.board.fen(), "moves": moves, "how": how,
                "white_to_move": self.board.turn == chess.WHITE, "white_below": self.white_below,
                "board": ascii_board(self.board, self.white_below), "check": self.board.is_check(),
                "over": self.board.is_game_over()}


def describe(result: dict) -> str:
    if not result["found"]:
        return "Ich sehe auf keinem Monitor ein Schachbrett. Es muss als flaches 2D-Brett sichtbar sein."
    lines = [result["board"], f"FEN: {result['fen']}", f"Am Zug: {'Weiß' if result['white_to_move'] else 'Schwarz'}",
             f"Unten steht: {'Weiß' if result['white_below'] else 'Schwarz'}"]
    if result["moves"]:
        lines.append("Neue Züge: " + ", ".join(result["moves"]))
    if result["check"]:
        lines.append("Schach!")
    if result["over"]:
        lines.append("Die Partie ist zu Ende.")
    lines.append(f"Gelesen: {result['how']}.")
    return "\n".join(lines)


def ask_gemini(client_factory, models: list[str], jpeg: bytes) -> dict:
    """Der Notfall: wenig Pixel, kein Nachdenken, feste Antwortform. Fällt ein Modell aus, kommt das nächste."""
    from google.genai import types
    schema = types.Schema(type="OBJECT", required=["rows", "bottom"], properties={
        "rows": types.Schema(type="ARRAY", items=types.Schema(type="STRING")),
        "bottom": types.Schema(type="STRING", enum=["white", "black"])})
    config = types.GenerateContentConfig(temperature=0, response_mime_type="application/json", response_schema=schema,
                                         http_options=types.HttpOptions(timeout=VISION_TIMEOUT_MS))
    client = client_factory()
    last: Exception | None = None
    for model in models:
        try:
            parts = [types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"), PROMPT]
            return json.loads(client.models.generate_content(model=model, contents=parts, config=config).text)
        except Exception as exc:
            last = exc
    raise last or ValueError("Kein Modell eingestellt.")

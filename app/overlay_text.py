"""Emoji-aware text layout for the image overlay engine.

Pillow does not fall back between fonts, so a text line is split into plain
text runs (drawn with the configured font) and emoji clusters (drawn with
Noto Color Emoji).  Emoji are rendered at the font's native bitmap size and
scaled down to the requested line height.

The module deliberately has no dependency on the rest of the application so
it can be unit-tested without ffmpeg or a live stream.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

EMOJI_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/truetype/noto-emoji/NotoColorEmoji.ttf",
)
EMOJI_NATIVE_SIZE = 109
EMOJI_CANVAS_SIZE = 256
EMOJI_SCALE = 1.06
EMOJI_CACHE_LIMIT = 512
ELLIPSIS = "…"

# Emoji presentation ranges.  ZWJ sequences, skin tone modifiers, variation
# selectors, keycaps and flags are kept together as one cluster so Raqm can
# render them as a single glyph.
_EMOJI_RE = re.compile(
    "(?:"
    r"[\U0001F1E6-\U0001F1FF]{2}"
    r"|[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF]"
    r"(?:[\uFE0E\uFE0F]|\U0001F3FB-\U0001F3FF|"
    r"\u200D[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF]"
    r"(?:[\uFE0E\uFE0F]|\U0001F3FB-\U0001F3FF)?)*"
    r"|[0-9#*]\uFE0F?\u20E3"
    r")"
)

_WHITESPACE_RE = re.compile(r"\s+")


def find_emoji_font(explicit: str = "") -> str | None:
    candidates: list[str] = []
    if explicit.strip():
        candidates.append(explicit.strip())
    candidates.extend(EMOJI_FONT_CANDIDATES)
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path)
    return None


def find_regular_font(path: str) -> str:
    """Return the regular variant of a guessed bold font."""
    candidate = Path(path).expanduser()
    if not candidate.is_file():
        return path
    stem = candidate.stem
    names: list[str] = []
    for old, new in (
        ("-Bold", "-Regular"),
        ("Bold", "Regular"),
        ("-Bold", ""),
        ("Bold", ""),
    ):
        if old in stem:
            names.append(stem.replace(old, new) + candidate.suffix)
    for name in names:
        regular = candidate.with_name(name)
        if regular.is_file():
            return str(regular)
    return path


def find_bold_font(path: str) -> str:
    """Return a bold variant of ``path`` when one can be guessed."""
    candidate = Path(path).expanduser()
    if not candidate.is_file():
        return path
    names: list[str] = []
    stem = candidate.stem
    for old, new in (
        ("-Regular", "-Bold"),
        ("Regular", "Bold"),
        ("-Book", "-Bold"),
        ("Book", "Bold"),
    ):
        if old in stem:
            names.append(stem.replace(old, new) + candidate.suffix)
    names.append(stem + "-Bold" + candidate.suffix)
    names.append(stem.replace("Sans", "Sans-Bold", 1) + candidate.suffix)
    for name in names:
        bold = candidate.with_name(name)
        if bold.is_file():
            return str(bold)
    return path


@dataclass(frozen=True)
class FontSet:
    regular_path: str
    bold_path: str
    size: int
    regular: ImageFont.FreeTypeFont
    bold: ImageFont.FreeTypeFont
    ascent: int
    descent: int

    @property
    def line_height(self) -> int:
        return self.ascent + self.descent


def load_font_set(path: str, size: int) -> FontSet | None:
    """Load a regular and a bold font at ``size`` points."""
    size = max(8, int(size))
    try:
        regular = ImageFont.truetype(path, size)
    except (OSError, ValueError):
        return None
    bold_path = find_bold_font(path)
    try:
        bold = ImageFont.truetype(bold_path, size)
    except (OSError, ValueError):
        bold = regular
    ascent, descent = regular.getmetrics()
    return FontSet(
        regular_path=path,
        bold_path=bold_path,
        size=size,
        regular=regular,
        bold=bold,
        ascent=ascent,
        descent=descent,
    )


class EmojiRenderer:
    """Render color emoji clusters at arbitrary pixel heights."""

    def __init__(self, font_path: str | None, enabled: bool = True) -> None:
        self.font_path = font_path if enabled else None
        self._font: ImageFont.FreeTypeFont | None = None
        self._cache: dict[tuple[str, int], Image.Image] = {}
        if self.font_path:
            try:
                self._font = ImageFont.truetype(
                    self.font_path, EMOJI_NATIVE_SIZE
                )
            except (OSError, ValueError):
                self._font = None

    @property
    def available(self) -> bool:
        return self._font is not None

    def image(self, cluster: str, height: int) -> Image.Image | None:
        if self._font is None:
            return None
        height = max(8, int(height))
        key = (cluster, height)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        canvas = Image.new(
            "RGBA", (EMOJI_CANVAS_SIZE, EMOJI_CANVAS_SIZE), (0, 0, 0, 0)
        )
        draw = ImageDraw.Draw(canvas)
        try:
            draw.text(
                (0, 0),
                cluster,
                font=self._font,
                embedded_color=True,
                fill=(255, 255, 255, 255),
            )
        except (OSError, ValueError):
            return None
        bbox = canvas.getbbox()
        if bbox is None:
            return None
        image = canvas.crop(bbox)
        if image.height <= 0:
            return None
        ratio = height / image.height
        width = max(1, round(image.width * ratio))
        image = image.resize((width, height), Image.LANCZOS)
        self._cache[key] = image
        while len(self._cache) > EMOJI_CACHE_LIMIT:
            self._cache.pop(next(iter(self._cache)))
        return image


@dataclass(frozen=True)
class Run:
    kind: str  # "text" or "emoji"
    text: str
    width: float
    image: Image.Image | None = None


@dataclass(frozen=True)
class TextLine:
    runs: tuple[Run, ...]
    width: float


class RichText:
    """Wrap and draw text that may contain color emoji."""

    def __init__(self, fonts: FontSet, emoji: EmojiRenderer | None) -> None:
        self.fonts = fonts
        self.emoji = emoji
        self._ellipsis_width = fonts.regular.getlength(ELLIPSIS)
        self._space_width = fonts.regular.getlength(" ")
        self._emoji_px = max(8, round(fonts.size * EMOJI_SCALE))

    # ---------------------------------------------------------------- measuring

    def _emoji_image(self, cluster: str) -> Image.Image | None:
        if self.emoji is None:
            return None
        return self.emoji.image(cluster, self._emoji_px)

    def _run(self, kind: str, text: str) -> Run:
        if kind == "emoji":
            image = self._emoji_image(text)
            if image is not None:
                return Run("emoji", text, float(image.width), image)
            # Fall back to the regular font if the emoji font is missing.
            kind = "text"
        return Run("text", text, self.fonts.regular.getlength(text))

    def _segments(self, text: str) -> list[Run]:
        runs: list[Run] = []
        position = 0
        for match in _EMOJI_RE.finditer(text):
            if match.start() > position:
                runs.append(
                    self._run("text", text[position : match.start()])
                )
            runs.append(self._run("emoji", match.group(0)))
            position = match.end()
        if position < len(text):
            runs.append(self._run("text", text[position:]))
        return runs

    # ------------------------------------------------------------------ layout

    def _words(self, text: str) -> list[list[Run]]:
        """Split a paragraph into words while preserving emoji clusters."""
        words: list[list[Run]] = []
        current: list[Run] = []
        for segment in self._segments(text):
            if segment.kind != "text":
                current.append(segment)
                continue
            parts = re.split(r"(\s+)", segment.text)
            for part in parts:
                if not part:
                    continue
                if part.isspace():
                    if current:
                        words.append(current)
                        current = []
                else:
                    current.append(
                        Run(
                            "text",
                            part,
                            self.fonts.regular.getlength(part),
                        )
                    )
        if current:
            words.append(current)
        return words

    @staticmethod
    def _width(runs: list[Run] | tuple[Run, ...]) -> float:
        return sum(run.width for run in runs)

    def _split_long_word(
        self, word: list[Run], max_width: float
    ) -> list[list[Run]]:
        chunks: list[list[Run]] = []
        chunk: list[Run] = []
        width = 0.0
        for run in word:
            if run.kind == "emoji" or run.width <= max_width:
                pieces = [run]
            else:
                pieces = []
                for char in run.text:
                    pieces.append(
                        Run(
                            "text",
                            char,
                            self.fonts.regular.getlength(char),
                        )
                    )
            for piece in pieces:
                if chunk and width + piece.width > max_width:
                    chunks.append(chunk)
                    chunk = []
                    width = 0.0
                chunk.append(piece)
                width += piece.width
        if chunk:
            chunks.append(chunk)
        return chunks

    def _append_ellipsis(
        self, lines: list[list[Run]], max_width: float
    ) -> None:
        if not lines:
            lines.append([Run("text", ELLIPSIS, self._ellipsis_width)])
            return
        line = lines[-1]
        while line:
            width = self._width(line) + self._ellipsis_width
            if width <= max_width:
                break
            line.pop()
        line.append(Run("text", ELLIPSIS, self._ellipsis_width))

    def layout(
        self, text: str, max_width: float, max_lines: int
    ) -> list[TextLine]:
        """Wrap ``text`` into at most ``max_lines`` lines."""
        max_width = max(1.0, float(max_width))
        max_lines = max(1, int(max_lines))
        paragraphs = str(text or "").splitlines() or [""]
        lines: list[list[Run]] = []
        overflow = False

        for paragraph in paragraphs:
            words = self._words(paragraph)
            if not words:
                if len(lines) < max_lines:
                    lines.append([])
                continue
            current: list[Run] = []
            current_width = 0.0
            for word in words:
                word_width = self._width(word)
                spacer = (
                    [Run("text", " ", self._space_width)] if current else []
                )
                needed = current_width + self._width(spacer) + word_width
                if needed <= max_width:
                    current.extend(spacer)
                    current.extend(word)
                    current_width = needed
                    continue
                if current:
                    lines.append(current)
                    current = []
                    current_width = 0.0
                    if len(lines) >= max_lines:
                        overflow = True
                        break
                if word_width <= max_width:
                    current = list(word)
                    current_width = word_width
                    continue
                for chunk in self._split_long_word(word, max_width):
                    if current:
                        lines.append(current)
                        current = []
                        if len(lines) >= max_lines:
                            overflow = True
                            break
                    current = chunk
                    current_width = self._width(chunk)
                if overflow:
                    break
            if overflow:
                break
            if current:
                if len(lines) >= max_lines:
                    overflow = True
                    break
                lines.append(current)

        if overflow:
            lines = lines[:max_lines]
            self._append_ellipsis(lines, max_width)
        if not lines:
            lines = [[]]
        return [TextLine(tuple(line), self._width(line)) for line in lines]

    def measure(self, text: str) -> float:
        """Return the width of a single-line text without wrapping."""
        width = 0.0
        for run in self._segments(_WHITESPACE_RE.sub(" ", str(text or ""))):
            width += run.width
        return width

    @staticmethod
    def _composite_clipped(
        base: Image.Image,
        overlay: Image.Image,
        x: int,
        y: int,
    ) -> None:
        left = max(0, x)
        top = max(0, y)
        right = min(base.width, x + overlay.width)
        bottom = min(base.height, y + overlay.height)
        if right <= left or bottom <= top:
            return
        crop = overlay.crop((left - x, top - y, right - x, bottom - y))
        base.alpha_composite(crop, dest=(left, top))

    def draw_line(
        self,
        image: Image.Image,
        x: float,
        y: float,
        line: TextLine,
        fill: tuple[int, int, int, int],
        shadow: tuple[int, int, int, int] | None = None,
        shadow_offset: int = 1,
    ) -> None:
        if shadow is not None:
            self._draw_runs(
                image, x + shadow_offset, y + shadow_offset, line, shadow
            )
        self._draw_runs(image, x, y, line, fill)

    def _draw_runs(
        self,
        image: Image.Image,
        x: float,
        y: float,
        line: TextLine,
        fill: tuple[int, int, int, int],
    ) -> None:
        drawer = ImageDraw.Draw(image)
        cursor = x
        for run in line.runs:
            if run.kind == "emoji" and run.image is not None:
                top = y + (self.fonts.line_height - run.image.height) / 2
                self._composite_clipped(
                    image, run.image, round(cursor), round(top)
                )
            else:
                drawer.text(
                    (cursor, y), run.text, font=self.fonts.regular, fill=fill
                )
            cursor += run.width

    def draw_text(
        self,
        image: Image.Image,
        x: float,
        y: float,
        text: str,
        fill: tuple[int, int, int, int],
        max_width: float,
        max_lines: int,
        line_spacing: int = 0,
        shadow: tuple[int, int, int, int] | None = None,
    ) -> int:
        """Draw wrapped text and return the used pixel height."""
        lines = self.layout(text, max_width, max_lines)
        height = 0
        for line in lines:
            self.draw_line(
                image,
                x,
                y + height,
                line,
                fill,
                shadow=shadow,
                shadow_offset=max(1, self.fonts.size // 20),
            )
            height += self.fonts.line_height + line_spacing
        return height

"""Local AI visuals adapted for TV. Original copyright 2026 1Z99, MIT.

All SDL resources belong to the supplied Display's renderer. Close Intro and
Cursor (and destroy load_rgba's returned texture) before closing that Display.
"""

import ctypes as C
import json
import math
from pathlib import Path
import struct
import sys
import time
import zipfile
import zlib


W, H = 640, 480
BG = (4, 8, 6, 255)
PANEL = (7, 15, 11, 255)
PANEL_2 = (9, 20, 15, 255)
PANEL_3 = (12, 27, 20, 255)
BORDER = (18, 66, 40, 255)
EDGE = (34, 124, 74, 255)
TEXT = (196, 240, 215, 255)
MUTED = (86, 146, 116, 255)
GREEN = (51, 255, 119, 255)
GREEN_DIM = (28, 176, 96, 255)
ERROR = (255, 82, 96, 255)
SHADOW = (0, 0, 0, 255)
LOGO_W, LOGO_H = 108, 36
KEYS = ('1234567890-+*/=:', 'qwertyuiop', 'asdfghjkl', 'zxcvbnm.,!?',
        ('SPACE', 'DEL', 'CLEAR', 'FIND', 'CLOSE'))
KEY_TOP, KEY_STEP, KEY_HEIGHT = 258, 38, 30
KEY_GAP, KEY_MAX_W = 4, 52
CONTENT_X, CONTENT_W = 12, 616
SCRAMBLE_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!<>[]{}/\\|#$%&*+=?-_"
SCRAMBLE_TICK = 0.05
SCRAMBLE_MIN, SCRAMBLE_SPEED, SCRAMBLE_DRAIN = 8, 40., 1.2


def scramble_text(text, salt):
    """The same deterministic glyph scrambling used by 227 Local AI."""
    pieces = []
    for index, char in enumerate(text):
        if char.isspace():
            pieces.append(char)
        else:
            mixed = (index * 2654435761 + salt * 40503 + 12345) & 0xFFFFFFFF
            pieces.append(SCRAMBLE_CHARS[mixed % len(SCRAMBLE_CHARS)])
    return "".join(pieces)


def advance_scramble(item, elapsed):
    reveal = item.get("reveal")
    if reveal is None:
        return False
    received = len(item.get("content", ""))
    if reveal >= received and not item.get("pending"):
        return False
    ceiling = received-SCRAMBLE_MIN if item.get("pending") and received >= SCRAMBLE_MIN else received
    speed = max(SCRAMBLE_SPEED, (received-reveal)/SCRAMBLE_DRAIN)
    item["reveal"] = min(reveal+max(1, int(speed*elapsed)), ceiling)
    return True


def _bind_sdl(display):
    """Bind additional functions on this Display, never load a global SDL."""
    p, i = C.c_void_p, C.c_int
    for name, args, result in (
        ('SDL_RWFromConstMem', [p, i], p),
        ('SDL_LoadBMP_RW', [p, i], p),
        ('SDL_CreateTextureFromSurface', [p, p], p),
        ('SDL_FreeSurface', [p], None),
        ('SDL_DestroyTexture', [p], None),
        ('SDL_UpdateTexture', [p, p, p, i], i),
        ('SDL_SetTextureBlendMode', [p, i], i),
        ('SDL_SetTextureAlphaMod', [p, C.c_uint8], i),
        ('SDL_GetRenderTarget', [p], p),
        ('SDL_SetRenderTarget', [p, p], i),
        ('SDL_SetRenderDrawColor', [p] + [C.c_uint8] * 4, i),
        ('SDL_RenderClear', [p], i),
        ('SDL_RenderPresent', [p], None),
    ):
        fn = getattr(display.sdl, name)
        fn.argtypes, fn.restype = args, result


def load_rgba(display, path, width, height):
    """Upload byte-order RGBA pixels; the caller owns the returned texture."""
    if width <= 0 or height <= 0:
        raise ValueError('Invalid RGBA dimensions')
    raw = Path(path).read_bytes()
    if len(raw) != width * height * 4:
        raise ValueError('RGBA asset size does not match its dimensions')
    _bind_sdl(display)
    texture = display.texture(width, height)
    try:
        buffer = C.create_string_buffer(raw)
        if display.sdl.SDL_UpdateTexture(texture, None, buffer, width * 4):
            raise RuntimeError('Cannot upload RGBA texture')
        if display.sdl.SDL_SetTextureBlendMode(texture, 1):
            raise RuntimeError('Cannot enable RGBA blending')
        return texture
    except BaseException:
        display.sdl.SDL_DestroyTexture(texture)
        raise


class Intro:
    """Preloaded silent BMP animation with Local AI's timing and fade.

    Frame one is presented during preload, without starting the playback clock.
    Subsequent drawing/presentation and UI composition belong to the caller.
    """

    def __init__(self, display, assets, *, clock=None):
        self.d = display
        self.clock = time.monotonic if clock is None else clock
        self.textures = []
        self.texture = None
        self.active = False
        self.started = None
        self.frame_index = 0
        self.duration, self.fade_seconds = 1.67, 0.8
        self.error = ''
        pack = Path(assets) / 'intro-frames.zip'
        if not pack.is_file():
            return
        _bind_sdl(display)
        sdl = display.sdl
        try:
            with zipfile.ZipFile(pack) as archive:
                if archive.getinfo('manifest.json').file_size > 65536:
                    raise ValueError('Oversized intro manifest')
                manifest = json.loads(archive.read('manifest.json'))
                if not isinstance(manifest, dict):
                    raise ValueError('Invalid intro manifest')
                names = manifest.get('frames')
                self.duration = float(manifest['duration'])
                self.fade_seconds = float(manifest.get('fade_seconds', 0.8))
                if (manifest.get('version') != 1 or manifest.get('width') != W
                        or manifest.get('height') != H or not isinstance(names, list)
                        or not 1 <= len(names) <= 128
                        or not all(isinstance(name, str) for name in names)
                        or not math.isfinite(self.duration) or not 0 < self.duration <= 30
                        or not math.isfinite(self.fade_seconds)
                        or not 0 <= self.fade_seconds <= self.duration):
                    raise ValueError('Invalid intro dimensions or timing')
                for name in names:
                    info = archive.getinfo(name)
                    if not 54 <= info.file_size <= W * H * 4 + 4096:
                        raise ValueError('Invalid intro frame size')
                    raw = archive.read(info)
                    width, height = struct.unpack_from('<ii', raw, 18)
                    if raw[:2] != b'BM' or width != W or abs(height) != H:
                        raise ValueError('Intro frame dimensions do not match the screen')
                    buffer = C.create_string_buffer(raw)
                    stream = sdl.SDL_RWFromConstMem(buffer, len(raw))
                    if not stream:
                        raise RuntimeError('Cannot open intro frame')
                    surface = sdl.SDL_LoadBMP_RW(stream, 1)
                    if not surface:
                        raise RuntimeError('Cannot decode intro bitmap')
                    try:
                        texture = sdl.SDL_CreateTextureFromSurface(display.renderer, surface)
                    finally:
                        sdl.SDL_FreeSurface(surface)
                    if not texture:
                        raise RuntimeError('Cannot create intro frame texture')
                    self.textures.append(texture)
                    if sdl.SDL_SetTextureBlendMode(texture, 0):
                        raise RuntimeError('Cannot set intro blending')
                    if len(self.textures) == 1:
                        display.copy(texture, (0, 0, W, H))
                        sdl.SDL_RenderPresent(display.renderer)
            self.active = True
        except (OSError, ValueError, OverflowError, KeyError, TypeError, struct.error,
                zipfile.BadZipFile, RuntimeError, zlib.error) as exc:
            self.error = str(exc)
            print('Intro unavailable: ' + self.error, file=sys.stderr)
            self.close()
        except BaseException:
            self.close()
            raise

    def position(self):
        return 0.0 if self.started is None else self.clock() - self.started

    @property
    def covers_ui(self):
        return self.active and self.position() < self.duration - self.fade_seconds - 0.05

    def draw(self):
        if not self.active:
            return
        now = self.clock()
        if self.started is None:
            self.started = now
        elapsed = now - self.started
        if elapsed >= self.duration:
            self.close()
            return
        self.frame_index = min(len(self.textures) - 1,
                               int(elapsed * len(self.textures) / self.duration))
        self.texture = self.textures[self.frame_index]
        alpha = (min(1.0, (self.duration - elapsed) / self.fade_seconds)
                 if self.fade_seconds else 1.0)
        self.d.sdl.SDL_SetTextureBlendMode(self.texture, 0 if alpha == 1.0 else 1)
        self.d.sdl.SDL_SetTextureAlphaMod(self.texture, round(255 * alpha))
        self.d.copy(self.texture, (0, 0, W, H))

    def close(self):
        self.active = False
        for texture in self.textures:
            self.d.sdl.SDL_DestroyTexture(texture)
        self.textures.clear()
        self.texture = None


def key_geometry(row):
    """Return (x, y, width, height) hitboxes using Local AI's spaced layout."""
    count = len(KEYS[row])
    width = min(KEY_MAX_W, (CONTENT_W - (count - 1) * KEY_GAP) // count)
    slack = CONTENT_W - (count * width + (count - 1) * KEY_GAP)
    gap = KEY_GAP + slack / (count - 1)
    x = float(CONTENT_X)
    result = []
    for _ in KEYS[row]:
        result.append((int(round(x)), KEY_TOP + row * KEY_STEP, width, KEY_HEIGHT))
        x += width + gap
    return result


def keyboard(display, row, col):
    for row_index, keys in enumerate(KEYS):
        for column, (key, box) in enumerate(zip(keys, key_geometry(row_index))):
            x, y, width, height = box
            selected = row_index == row and column == col
            display.rect(*box, GREEN if selected else PANEL_3)
            display.rect(*box, GREEN if selected else BORDER, outline=True)
            display.text(key, x, y + (height - 17) // 2, BG if selected else TEXT,
                         size=15, width=width, bold=True, align='center')


class Cursor:
    """Rasterize the original polygon once; subsequent draws are texture copies."""

    def __init__(self, display):
        self.d, self.texture = display, None
        _bind_sdl(display)
        sdl = display.sdl
        previous = sdl.SDL_GetRenderTarget(display.renderer)
        self.texture = display.texture(21, 25, target=True)
        try:
            if sdl.SDL_SetRenderTarget(display.renderer, self.texture):
                raise RuntimeError('Cannot select cursor render target')
            sdl.SDL_SetRenderDrawColor(display.renderer, 0, 0, 0, 0)
            if sdl.SDL_RenderClear(display.renderer):
                raise RuntimeError('Cannot clear cursor texture')
            points = [(0, 0), (0, 20), (5, 15), (10, 23),
                      (14, 21), (9, 13), (18, 13)]
            pairs = list(zip(points, points[1:] + points[:1]))
            for offset_x, offset_y, color in ((2, 2, SHADOW), (0, 0, GREEN)):
                for row in range(23):
                    scan_y = row + 0.5
                    intersections = sorted(
                        x1 + (scan_y - y1) * (x2 - x1) / (y2 - y1)
                        for (x1, y1), (x2, y2) in pairs
                        if y1 != y2 and min(y1, y2) <= scan_y < max(y1, y2))
                    for left, right in zip(intersections[::2], intersections[1::2]):
                        left, right = math.ceil(left), math.floor(right)
                        if right >= left:
                            display.line(offset_x + left, offset_y + row,
                                         offset_x + right, offset_y + row, color)
            for start, end in pairs:
                display.line(*start, *end, SHADOW)
            if sdl.SDL_SetTextureBlendMode(self.texture, 1):
                raise RuntimeError('Cannot enable cursor blending')
        except BaseException:
            sdl.SDL_SetRenderTarget(display.renderer, previous)
            self.close()
            raise
        else:
            sdl.SDL_SetRenderTarget(display.renderer, previous)

    def draw(self, x, y):
        if self.texture:
            self.d.copy(self.texture, (int(x), int(y), 21, 25))

    def close(self):
        if self.texture:
            self.d.sdl.SDL_DestroyTexture(self.texture)
            self.texture = None


def cursor(display, x, y):
    """Draw a per-Display cached cursor and return its closeable owner."""
    cached = getattr(display, '_tv_style_cursor', None)
    if cached is None or cached.texture is None:
        cached = display._tv_style_cursor = Cursor(display)
    cached.draw(x, y)
    return cached


def pointer_motion(raw):
    magnitude = abs(raw)
    if magnitude <= 5000:
        return 0.0
    motion = (magnitude - 5000) / (32767 - 5000)
    return -motion if raw < 0 else motion


def pointer_speed(mx, my, keyboard=False):
    return ((210 + 800 * max(abs(mx), abs(my))) if keyboard
            else (260 + 1000 * max(abs(mx), abs(my))))

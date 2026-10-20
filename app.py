#!/usr/bin/env python3
"""MELKOR 227: standalone SDL live television atlas for the R36S."""

import argparse
from collections import OrderedDict
import ctypes as C
import json
import math
import os
from pathlib import Path
import queue
import random
import struct
import threading
import time
import zlib

from tv_backend import (Catalog, Health, State, country_at, country_center,
                        filter_stations, project, station_location, GLOBE_RESOLUTIONS, GLOBE_ROTATION_SPEEDS)
from tv_media import MediaError, Player
from tv_globe import Globe
from tv_quality import resolve_station, quality_warning
from tv_style import (Intro, Cursor, KEYS, key_geometry, keyboard, load_rgba,
                      pointer_motion, pointer_speed, scramble_text, advance_scramble, SCRAMBLE_TICK)

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
BG = (4, 8, 6, 255)
PANEL = (7, 15, 11, 255)
TEXT = (196, 240, 215, 255)
DIM = (86, 146, 116, 255)
GREEN = (51, 255, 119, 255)
EDGE = (36, 122, 67, 255)
RED = (255, 55, 80, 255)
GOLD = (242, 201, 76, 255)
GREY = (151, 160, 155, 255)
LIST_X, LIST_Y, ROW_HEIGHT, VISIBLE_ROWS = 422, 88, 42, 8
MAP_WIDTH, MAP_CENTER_X, MAP_CENTER_Y = 414, 207, 263
MAP_VIEWPORT = (4, 60, 406, 406)
MAP_RADIUS = 150*406/350
MENU_Y, MENU_STEP, MENU_ROWS = 110, 35, 9
TABS = ("World", "Favorites", "Recent", "Archive")
BUTTONS = {0: "back", 1: "ok", 2: "search", 3: "favorite", 4: "prev", 5: "next",
           6: "minus", 7: "plus", 8: "up", 9: "down", 10: "left", 11: "right",
           12: "select", 13: "menu", 14: "random", 15: "pause", 16: "menu"}
KEYBOARD = {13: "ok", 27: "back", 1073741906: "up", 1073741905: "down",
            1073741904: "left", 1073741903: "right", 9: "select", 120: "search",
            121: "favorite", 113: "prev", 101: "next", 45: "minus", 61: "plus",
            32: "pause", 114: "random", 1073741882: "menu"}


def display_owner(pid=None, proc_root=Path("/proc")):
    """Identify the actual EmulationStation ancestor, not its service wrapper."""
    pid = os.getpid() if pid is None else pid
    seen = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        process = Path(proc_root) / str(pid)
        try:
            fields = (process / "stat").read_text().rsplit(")", 1)[1].split()
            executable = Path(os.readlink(process / "exe")).name
            if executable == "emulationstation":
                return pid, fields[19]  # PID plus start time protects against PID reuse.
            pid = int(fields[1])
        except (OSError, ValueError, IndexError):
            return None
    return None


class Rect(C.Structure):
    _fields_ = [(n, C.c_int) for n in ("x", "y", "w", "h")]


class Color(C.Structure):
    _fields_ = [(n, C.c_uint8) for n in ("r", "g", "b", "a")]


class Display:
    def __init__(self, desktop=False):
        os.environ["SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] = "0"
        self.sdl = C.CDLL("libSDL2-2.0.so.0")
        self.ttf = C.CDLL("libSDL2_ttf-2.0.so.0")
        self.window = self.renderer = self.joy = None
        self.logo = self.backdrop = self.pointer = None
        self.fonts, self.textures = {}, OrderedDict()
        p, i, u = C.c_void_p, C.c_int, C.c_uint32
        signatures = (
            (self.sdl, "SDL_Init", [u], i),
            (self.sdl, "SDL_CreateWindow", [C.c_char_p, i, i, i, i, u], p),
            (self.sdl, "SDL_CreateRenderer", [p, i, u], p),
            (self.sdl, "SDL_SetRenderDrawColor", [p] + [C.c_uint8] * 4, i),
            (self.sdl, "SDL_SetRenderDrawBlendMode", [p, i], i),
            (self.sdl, "SDL_RenderFillRect", [p, C.POINTER(Rect)], i),
            (self.sdl, "SDL_RenderDrawRect", [p, C.POINTER(Rect)], i),
            (self.sdl, "SDL_RenderDrawLine", [p, i, i, i, i], i),
            (self.sdl, "SDL_RenderClear", [p], i),
            (self.sdl, "SDL_RenderPresent", [p], None),
            (self.sdl, "SDL_CreateTexture", [p, u, i, i, i], p),
            (self.sdl, "SDL_UpdateTexture", [p, p, p, i], i),
            (self.sdl, "SDL_SetTextureBlendMode", [p, i], i),
            (self.sdl, "SDL_SetRenderTarget", [p, p], i),
            (self.sdl, "SDL_RenderSetClipRect", [p, C.POINTER(Rect)], i),
            (self.sdl, "SDL_CreateTextureFromSurface", [p, p], p),
            (self.sdl, "SDL_QueryTexture", [p, p, p, C.POINTER(i), C.POINTER(i)], i),
            (self.sdl, "SDL_RenderCopy", [p, p, p, C.POINTER(Rect)], i),
            (self.sdl, "SDL_PollEvent", [p], i),
            (self.sdl, "SDL_NumJoysticks", [], i),
            (self.sdl, "SDL_JoystickOpen", [i], p),
            (self.sdl, "SDL_JoystickGetAxis", [p, i], C.c_int16),
            (self.sdl, "SDL_JoystickNumAxes", [p], i),
            (self.sdl, "SDL_GetError", [], C.c_char_p),
            (self.sdl, "SDL_RenderReadPixels", [p, p, u, p, i], i),
            (self.sdl, "SDL_ShowCursor", [i], i),
            (self.sdl, "SDL_WarpMouseInWindow", [p, i, i], None),
            (self.sdl, "SDL_StartTextInput", [], None),
            (self.ttf, "TTF_Init", [], i),
            (self.ttf, "TTF_OpenFont", [C.c_char_p, i], p),
            (self.ttf, "TTF_RenderUTF8_Blended", [p, C.c_char_p, Color], p),
            (self.ttf, "TTF_SizeUTF8", [p, C.c_char_p, C.POINTER(i), C.POINTER(i)], i),
        )
        for lib, name, args, result in signatures:
            fn = getattr(lib, name)
            fn.argtypes, fn.restype = args, result
        for name in ("SDL_DestroyTexture", "SDL_FreeSurface", "SDL_DestroyRenderer",
                     "SDL_DestroyWindow", "SDL_JoystickClose"):
            getattr(self.sdl, name).argtypes = [p]
        self.ttf.TTF_CloseFont.argtypes = [p]
        try:
            if self.sdl.SDL_Init(0x20 | 0x200) or self.ttf.TTF_Init():
                raise RuntimeError(self.error())
            self.window = self.sdl.SDL_CreateWindow(b"MELKOR 227 // TV ATLAS", 0x2FFF0000,
                                                    0x2FFF0000, 640, 480, 0 if desktop else 1)
            if not self.window:
                raise RuntimeError(self.error())
            self.renderer = self.sdl.SDL_CreateRenderer(self.window, -1, 1)
            if not self.renderer:
                raise RuntimeError(self.error())
            self.sdl.SDL_SetRenderDrawBlendMode(self.renderer, 1)
            for size in (11, 13, 15, 18, 22):
                self.fonts[size] = self.ttf.TTF_OpenFont(os.fsencode(ASSETS / "DejaVuSansMono.ttf"), size)
                if not self.fonts[size]:
                    raise RuntimeError("Bundled font could not be opened")
            self.fonts["bold"] = self.ttf.TTF_OpenFont(os.fsencode(ASSETS / "DejaVuSansMono-Bold.ttf"), 15)
            if not self.fonts["bold"]:
                raise RuntimeError("Bundled bold font could not be opened")
            self.joy = self.sdl.SDL_JoystickOpen(0) if self.sdl.SDL_NumJoysticks() else None
            self.axes = self.sdl.SDL_JoystickNumAxes(self.joy) if self.joy else 0
            self.sdl.SDL_ShowCursor(0)
            self.sdl.SDL_StartTextInput()
            self.logo = load_rgba(self, ASSETS / "logo.rgba", 108, 36)
            self.pointer = Cursor(self)
            self.rect(0, 0, 640, 480, BG)
            self.copy(self.logo, (266, 222, 108, 36))
            self.sdl.SDL_RenderPresent(self.renderer)
            background = load_rgba(self, ASSETS / "intro-background.rgba", 640, 480)
            try:
                self.backdrop = self.texture(640, 480, target=True)
                self.sdl.SDL_SetRenderTarget(self.renderer, self.backdrop)
                self.rect(0, 0, 640, 480, BG)
                self.sdl.SDL_SetTextureAlphaMod(background, 35)
                self.copy(background, (0, 0, 640, 480))
            finally:
                self.sdl.SDL_SetRenderTarget(self.renderer, None)
                self.sdl.SDL_DestroyTexture(background)
        except BaseException:
            self.close()
            raise

    def error(self):
        return self.sdl.SDL_GetError().decode("utf-8", "replace")

    def rect(self, x, y, w, h, color=BG, outline=False):
        self.sdl.SDL_SetRenderDrawColor(self.renderer, *color)
        fn = self.sdl.SDL_RenderDrawRect if outline else self.sdl.SDL_RenderFillRect
        fn(self.renderer, C.byref(Rect(int(x), int(y), int(w), int(h))))

    def line(self, x1, y1, x2, y2, color):
        self.sdl.SDL_SetRenderDrawColor(self.renderer, *color)
        self.sdl.SDL_RenderDrawLine(self.renderer, int(x1), int(y1), int(x2), int(y2))

    def measure(self, value, size=13):
        width, height = C.c_int(), C.c_int()
        self.ttf.TTF_SizeUTF8(self.fonts[size], str(value).encode(), C.byref(width), C.byref(height))
        return width.value, height.value

    def text(self, value, x, y, color=TEXT, size=15, width=620, bold=False, align="left"):
        value = " ".join(str(value).split())
        if not value or width <= 0:
            return
        font = self.fonts["bold" if bold else size]
        key = (value, size, color, width, bold)
        if key not in self.textures:
            w, h = C.c_int(), C.c_int()
            self.ttf.TTF_SizeUTF8(font, value.encode(), C.byref(w), C.byref(h))
            if w.value > width:
                while value:
                    value = value[:-1]
                    self.ttf.TTF_SizeUTF8(font, (value + "...").encode(), C.byref(w), C.byref(h))
                    if w.value <= width:
                        break
                value += "..."
            surface = self.ttf.TTF_RenderUTF8_Blended(font, value.encode(), Color(*color))
            if not surface:
                return
            texture = self.sdl.SDL_CreateTextureFromSurface(self.renderer, surface)
            self.sdl.SDL_FreeSurface(surface)
            if not texture:
                return
            self.sdl.SDL_QueryTexture(texture, None, None, C.byref(w), C.byref(h))
            self.textures[key] = texture, w.value, h.value
            if len(self.textures) > 256:
                self.sdl.SDL_DestroyTexture(self.textures.popitem(last=False)[1][0])
        self.textures.move_to_end(key)
        texture, w, h = self.textures[key]
        if align == "center":
            x += (width-w)//2
        elif align == "right":
            x += width-w
        self.copy(texture, (x, y, w, h))

    def texture(self, width, height, target=False):
        texture = self.sdl.SDL_CreateTexture(self.renderer, 0x16762004,
                                            2 if target else 1, width, height)
        if not texture:
            raise RuntimeError(self.error())
        self.sdl.SDL_SetTextureBlendMode(texture, 0)
        return texture

    def copy(self, texture, rect):
        self.sdl.SDL_RenderCopy(self.renderer, texture, None, C.byref(Rect(*map(int, rect))))

    def screenshot(self, path):
        buf = (C.c_ubyte * (640 * 480 * 4))()
        area = Rect(0, 0, 640, 480)
        if self.sdl.SDL_RenderReadPixels(self.renderer, C.byref(area), 0x16762004, buf, 640 * 4):
            raise RuntimeError(self.error())
        pixels = bytes(buf)
        rows = b"".join(b"\0" + pixels[y*2560:(y+1)*2560] for y in range(480))

        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

        path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 640, 480, 8, 6, 0, 0, 0))
                         + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))

    def close(self):
        if self.pointer:
            self.pointer.close()
        for texture in (self.logo, self.backdrop):
            if texture:
                self.sdl.SDL_DestroyTexture(texture)
        for tex, _, _ in self.textures.values():
            self.sdl.SDL_DestroyTexture(tex)
        self.textures.clear()
        for font in self.fonts.values():
            self.ttf.TTF_CloseFont(font)
        self.fonts.clear()
        if self.joy:
            self.sdl.SDL_JoystickClose(self.joy)
        if self.renderer:
            self.sdl.SDL_DestroyRenderer(self.renderer)
        if self.window:
            self.sdl.SDL_DestroyWindow(self.window)
        self.ttf.TTF_Quit()
        self.sdl.SDL_Quit()


class App:
    def __init__(self, display, data_dir, demo=False):
        self.d, self.data_dir, self.demo = display, Path(data_dir), demo
        self.features = json.loads((ASSETS / "globe.json").read_text())["features"]
        self.by_country = {f["properties"]["code"]: f for f in self.features}
        self.globe = Globe(display, self.features)
        self.catalog, self.state, self.health = Catalog(data_dir, ASSETS), State(data_dir), Health(data_dir)
        self.globe.set_resolution(self.state.data["globe_resolution"])
        self.stations = []
        self.rows, self.markers = [], []
        self.country, self.query, self.tab, self.index = "", "", 0, 0
        self.focus, self.view = "list", "browse"
        self.modal, self.menu_index, self.keyboard_row, self.keyboard_col = "", 0, 0, 0
        self.draft = ""
        self.players, self.video_textures, self.playing, self.statuses = {}, {}, {}, {}
        self.slots, self.cell, self.pending_audio = [], 0, None
        self.events, self.workers = queue.Queue(), set()
        self.cancel = threading.Event()
        self.stream_cancel = threading.Event()
        self.stream_generation, self.pending_streams = 0, {}
        self.frame_cells, self.warned_cells = set(), set()
        self.playback_notices = {}
        self.quality_rows = []
        self.positions = {}
        self.channel_reveals, self.reveal_salt = {}, 0
        self.reveal_tick = 0.
        self.pointer_x, self.pointer_y, self.pointer_active = 320., 438., False
        self.drag_origin, self.dragged = None, False
        self.scroll_fraction = 0.
        self.redraw_until, self.timer_signature = time.monotonic()+.75, None
        self.running, self.dirty = True, True
        self.input_active, self.handheld = True, False
        self.reported_focus_events = set()
        self.notice, self.notice_until, self.last_input = "", 0, time.monotonic()
        self.last_globe_touch = self.last_input
        self.last_spin_frame, self.idle_spinning = self.last_input, False
        self.spin_started = self.last_input
        self.error = ""
        if demo:
            self.stations = [dict(uuid="demo-%s" % i, name=name, url="", countryCode=code,
                                  country=self.catalog.countries.get(code, code), language=lang, tags=tag)
                             for i, (name, code, lang, tag) in enumerate((
                                 ("Syria TV", "SY", "Arabic", "general"),
                                 ("Al Jazeera", "QA", "Arabic", "news"),
                                 ("DW English", "DE", "English", "news"),
                                 ("NASA TV", "US", "English", "science"),
                                 ("France 24", "FR", "French", "news"),
                                 ("NHK World", "JP", "English", "culture"),
                                 ("BBC News", "GB", "English", "news"),
                                 ("TRT World", "TR", "English", "news"))) ]
            self.toast("OFFLINE PREVIEW // sample stations")
        self.refilter()

    def toast(self, text):
        self.notice, self.notice_until = str(text), time.monotonic()+5
        self.dirty = True

    def playback_notice(self, cell, text):
        size = str(text).split(" | ", 1)[-1].split(" - ", 1)[0]
        self.playback_notices[cell] = ("High-quality broadcast / " + size,
                                       "Playback may be slower on this device", time.monotonic()+5)
        self.dirty = True

    def work(self, name, fn):
        if name in self.workers:
            return
        self.workers.add(name)

        def run():
            try:
                self.events.put((name, fn(), ""))
            except Exception as exc:
                self.events.put((name, None, str(exc)))

        threading.Thread(target=run, name="tv-"+name, daemon=True).start()
        self.dirty = True

    def refilter(self):
        old = self.selected().get("uuid") if self.rows else None
        self.rows = filter_stations(self.stations, self.query, self.country, TABS[self.tab], self.state, self.health)
        if not self.demo and not self.country and self.tab in (0, 3):
            self.rows = []
        self.index = next((i for i, row in enumerate(self.rows) if row["uuid"] == old), 0)
        self.markers = []
        if self.country in self.by_country:
            feature = self.by_country[self.country]
            # A bounded sample keeps the small globe legible and responsive.
            sample = self.rows[:120]
            selected = self.selected()
            if selected and selected not in sample:
                sample = sample[:119] + [selected]
            for row in sample:
                key = (self.country, row["uuid"])
                if key not in self.positions:
                    self.positions[key] = station_location(feature, row["uuid"])
                self.markers.append((row["uuid"], *self.positions[key]))
        self.dirty = True

    def selected(self):
        return self.rows[self.index] if self.rows else {}

    def select_country(self, code, animate=True):
        self.country, self.query, self.tab = code, "", 0
        if not self.demo and animate:
            self.stations = []
        self.refilter()
        self.channel_reveals.clear()
        feature = self.by_country.get(code)
        if animate:
            self.globe.fly_to(feature)
            if feature:
                self.start_channel_reveal()
                if not self.demo:
                    self.request_country()
        else:
            self.globe.cancel_flight()
            if feature:
                self.globe.lat, self.globe.lon = country_center(feature)

    def request_country(self, refresh=False):
        if not self.country:
            self.toast("Choose a country first")
            return
        code = self.country
        self.stations = []
        self.channel_reveals.clear()
        self.refilter()
        self.work("country:"+code, lambda: self.catalog.country(code, refresh=refresh))

    def start_channel_reveal(self):
        now = time.monotonic()
        self.channel_reveals.clear()
        top = max(0, self.index-(VISIBLE_ROWS-1))
        for i, row in enumerate(self.rows[top:top+VISIBLE_ROWS]):
            start = now if i == 0 else None
            self.channel_reveals[row["uuid"]] = dict(content=row["name"][:15], reveal=0,
                                                      pending=False, start=start, last=start)
        self.reveal_tick, self.reveal_salt = now, 0
        self.dirty = True

    def stop(self):
        self.stream_cancel.set()
        self.stream_cancel = threading.Event()
        self.stream_generation += 1
        self.pending_streams.clear()
        self.frame_cells.clear()
        self.warned_cells.clear()
        self.playback_notices.clear()
        self.quality_rows = []
        for player in self.players.values():
            player.close()
        for tex in self.video_textures.values():
            self.d.sdl.SDL_DestroyTexture(tex)
        self.players.clear()
        self.video_textures.clear()
        self.playing.clear()
        self.statuses.clear()
        self.pending_audio = None

    def start_player(self, cell, station, matrix=False):
        if self.demo:
            self.toast("Preview only. Launch without --demo to tune live TV.")
            return
        generation, cancel = self.stream_generation, self.stream_cancel
        self.pending_streams[cell] = (dict(station), matrix)
        self.playing[cell] = dict(station)
        self.statuses[cell] = {"buffering": True}
        self.work("stream:%s:%s" % (generation, cell),
                  lambda: resolve_station(station, cancel=cancel, max_height=240 if matrix else 480))

    def finish_player(self, cell, station, matrix=False):
        width, height = (298, 218) if matrix else (640, 480)
        player = Player(width, height, self.state.data["volume"], audible=not matrix or cell == self.cell)
        try:
            player.play(station)
            texture = self.d.texture(width, height)
            black = (C.c_ubyte * (width*height*4))()
            self.d.sdl.SDL_UpdateTexture(texture, None, black, width*4)
        except BaseException:
            player.close()
            raise
        self.players[cell], self.video_textures[cell], self.playing[cell] = player, texture, station
        if station.get("quality_warning"):
            self.playback_notice(cell, station["quality_warning"])
            self.warned_cells.add(cell)

    def browse(self):
        self.stop()
        self.view, self.dirty = "browse", True

    def play(self, station):
        if not station:
            return
        if self.demo:
            self.toast("Sample stations are not live streams")
            return
        current = self.players.get(0)
        status = current.poll() if current else {}
        old = self.playing.get(0, {})
        if (current and len(self.players) == 1 and current.width == 640 and current.height == 480
                and all(old.get(key) == station.get(key) for key in ("uuid", "url", "user_agent", "referrer"))
                and not status.get("error") and not status.get("ended")):
            self.cell = 0
            self.view = "video"
            return
        self.stop()
        self.cell = 0
        try:
            self.start_player(0, station)
            self.state.played(station)
            self.view = "video"
            if station.get("countryCode") in self.by_country:
                self.select_country(station["countryCode"], animate=False)
        except (MediaError, ValueError, OSError) as exc:
            self.stop()
            self.error, self.modal = str(exc), "error"

    def matrix(self):
        if not self.slots:
            self.toast("Add stations to the matrix from the menu first")
            return
        self.stop()
        self.cell = 0
        try:
            for i, station in enumerate(self.slots):
                self.start_player(i, station, matrix=True)
            self.view = "matrix"
        except (MediaError, ValueError, OSError) as exc:
            self.stop()
            self.error, self.modal = str(exc), "error"

    def audio_focus(self, cell):
        if cell == self.cell:
            return
        for player in self.players.values():
            player.set_audible(False)
        self.cell, self.pending_audio = cell, cell

    def menu_items(self):
        s = self.state.data
        items = ["Browse countries", "World / clear filters", "Random station",
                 "Add selected to matrix (%s/4)" % len(self.slots), "Watch matrix", "Clear matrix",
                 "Globe resolution: %s px" % self.state.data["globe_resolution"],
                "CRT overlay: " + ("ON" if s["crt"] else "OFF"),
                "CRT intensity: " + ("HIGH" if s["intense"] else "NORMAL"),
                 "CRT animation: " + ("ON" if s["animated"] else "OFF"),
                 "Earth rotation speed: " + ("OFF" if not s["globe_rotation_speed"] else
                                            "%gx" % s["globe_rotation_speed"]),
                 "Scan 12 stations for archive", "Refresh selected country", "Stop playback", "Exit TV"]
        return (["Resolution / frame rate"] + items) if self.has_playback() else items

    def has_playback(self):
        return self.view in ("video", "matrix") and bool(self.playing.get(self.cell))

    def open_quality(self):
        station = self.playing.get(self.cell, {})
        choices = {}
        for row in station.get("renditions", []):
            if row.get("height") or row.get("fps"):
                choices[row["url"]] = dict(row)
        status = self.statuses.get(self.cell, {})
        width = status.get("source_width") or station.get("quality_width")
        height = status.get("source_height") or station.get("quality_height")
        fps = status.get("source_fps") or station.get("quality_fps")
        url = station.get("selected_variant_url") or station.get("url")
        if url and url not in choices:
            choices[url] = dict(url=station.get("url"), width=width, height=height, fps=fps,
                                user_agent=station.get("user_agent", ""), referrer=station.get("referrer", ""),
                                **({"playlist": station["playlist"]} if station.get("playlist") else {}))
        self.quality_rows = [None] + sorted(choices.values(), key=lambda row: (
            row.get("height") or float("inf"), row.get("fps") or 0, row.get("bandwidth") or 0))
        self.modal, self.menu_index = "quality", 0

    def quality_label(self, row):
        if row is None:
            return "Auto / up to %sp" % (240 if self.view == "matrix" else 480)
        height, fps = row.get("height"), row.get("fps")
        label = "%sp" % height if height else "Current source"
        if fps:
            label += " / %g fps" % fps
        if not height and not fps and row.get("bandwidth"):
            label = "%g kbps" % (row["bandwidth"]/1000)
        return label

    def choose_quality(self):
        if not self.has_playback():
            self.modal = ""
            return
        row = self.quality_rows[self.menu_index]
        cell, matrix = self.cell, self.view == "matrix"
        station = dict(self.playing[cell])
        self.pending_streams.pop(cell, None)
        player = self.players.pop(cell, None)
        if player:
            player.close()
        texture = self.video_textures.pop(cell, None)
        if texture:
            self.d.sdl.SDL_DestroyTexture(texture)
        self.frame_cells.discard(cell)
        self.warned_cells.discard(cell)
        self.playback_notices.pop(cell, None)
        self.statuses[cell] = {"buffering": True}
        self.modal = ""
        if row is None:
            station["url"] = station.get("source_url", station["url"])
            station["user_agent"] = station.get("source_user_agent", station.get("user_agent", ""))
            station["referrer"] = station.get("source_referrer", station.get("referrer", ""))
            station.pop("playlist", None)
            self.start_player(cell, station, matrix)
        else:
            station.update({key: row[key] for key in ("url", "user_agent", "referrer", "playlist") if key in row})
            if "playlist" not in row:
                station.pop("playlist", None)
            station.update(selected_url=row["url"], selected_variant_url=row["url"],
                           quality_width=row.get("width"), quality_height=row.get("height"), quality_fps=row.get("fps"),
                           quality_warning=quality_warning(row.get("width"), row.get("height")))
            try:
                self.finish_player(cell, station, matrix)
            except (MediaError, OSError, ValueError) as exc:
                self.statuses[cell] = {"error": str(exc)}
                raise
        self.dirty = True

    def menu_action(self):
        index = self.menu_index
        if self.has_playback():
            if index == 0:
                self.open_quality()
                return
            index -= 1
        self.modal = ""
        if index == 0:
            self.modal, self.menu_index = "countries", 0
        elif index == 1:
            self.select_country("")
            self.browse()
        elif index == 2:
            self.random_station()
        elif index == 3:
            station = self.selected()
            if station and len(self.slots) < 4:
                self.slots.append(dict(station))
                self.toast("Station added to matrix slot %s" % len(self.slots))
            else:
                self.toast("Select a station first, or clear the full matrix")
        elif index == 4:
            self.matrix()
        elif index == 5:
            self.slots = []
        elif index in (6, 7, 8, 9, 10):
            self.modal = "menu"
            self.adjust_menu_setting()
        elif index == 11:
            if not self.demo:
                self.work("health", lambda: self.health.scan(self.stations, cancel=self.cancel))
            self.toast("Checking HTTP reachability, not full video decoding")
        elif index == 12:
            if not self.demo:
                self.request_country(refresh=True)
        elif index == 13:
            self.stop()
            self.view = "browse"
        elif index == 14:
            self.modal, self.menu_index = "exit", 0

    def adjust_menu_setting(self, direction=None):
        index = self.menu_index-int(self.has_playback())
        if index == 6:
            choices = GLOBE_RESOLUTIONS
            old = self.state.data["globe_resolution"]
            value = choices[(choices.index(old)+(direction or 1)) % len(choices)]
            self.globe.set_resolution(value)
            try:
                self.setting("globe_resolution", value)
            except (OSError, ValueError):
                self.globe.set_resolution(old)
                raise
        elif index in (7, 8, 9):
            key = ("crt", "intense", "animated")[index-7]
            self.setting(key, not self.state.data[key] if direction is None else direction > 0)
        elif index == 10:
            old = self.state.data["globe_rotation_speed"]
            choices = GLOBE_ROTATION_SPEEDS
            self.setting("globe_rotation_speed", choices[(choices.index(old)+(direction or 1)) % len(choices)])
        else:
            return False
        self.dirty = True
        return True

    def random_station(self):
        if self.rows:
            recent = {row["uuid"] for row in self.state.data["recent"]}
            self.play(random.choice([row for row in self.rows if row["uuid"] not in recent] or self.rows))
        else:
            self.toast("Choose a country first")

    def country_items(self):
        return sorted(self.catalog.countries.items(), key=lambda item: item[1])

    def setting(self, key, value):
        old = self.state.data[key]
        self.state.data[key] = value
        try:
            self.state.save()
        except (OSError, ValueError):
            self.state.data[key] = old
            raise

    def action(self, action):
        if not self.input_active:
            return
        try:
            self.handle_action(action)
        except (OSError, ValueError, MediaError) as exc:
            self.error, self.modal, self.dirty = str(exc), "error", True

    def handle_action(self, action):
        self.last_input, self.dirty = time.monotonic(), True
        if action in ("minus", "plus") or (self.focus == "globe" and action in ("up", "down", "left", "right")):
            self.last_globe_touch = self.last_input
        if action in ("menu", "search", "select", "back"):
            self.globe.cancel_flight()
        if self.modal == "error":
            if action in ("back", "ok"):
                self.modal = ""
            return
        if self.modal == "exit":
            if action in ("up", "down", "left", "right"):
                self.menu_index = 1-self.menu_index
            elif action == "back":
                self.modal = ""
            elif action == "ok":
                if self.menu_index:
                    self.running = False
                else:
                    self.modal = ""
            return
        if self.modal == "search":
            if action == "back":
                if self.draft:
                    self.draft = self.draft[:-1]
                else:
                    self.modal = ""
            elif action == "favorite":
                self.draft = (self.draft+" ")[:80]
            elif action in ("up", "down"):
                self.keyboard_row = max(0, min(len(KEYS)-1, self.keyboard_row + (1 if action == "down" else -1)))
                self.keyboard_col = min(self.keyboard_col, len(KEYS[self.keyboard_row])-1)
            elif action in ("left", "right"):
                self.keyboard_col = max(0, min(len(KEYS[self.keyboard_row])-1, self.keyboard_col + (1 if action == "right" else -1)))
            elif action == "ok":
                key = KEYS[self.keyboard_row][self.keyboard_col]
                if key == "FIND":
                    self.query, self.modal = self.draft, ""
                    self.channel_reveals.clear()
                    self.refilter()
                elif key == "DEL":
                    self.draft = self.draft[:-1]
                elif key == "CLEAR":
                    self.draft = ""
                elif key == "CLOSE":
                    self.modal = ""
                elif len(self.draft) < 80:
                    self.draft += " " if key == "SPACE" else key
            elif action == "search":
                self.query, self.modal = self.draft, ""
                self.channel_reveals.clear()
                self.refilter()
            if action in ("up", "down", "left", "right"):
                x, y, w, h = key_geometry(self.keyboard_row)[self.keyboard_col]
                self.pointer_x, self.pointer_y = x+w/2, y+h/2
            return
        if self.modal in ("menu", "countries", "quality"):
            rows = (self.country_items() if self.modal == "countries" else
                    self.quality_rows if self.modal == "quality" else self.menu_items())
            self.menu_index = min(self.menu_index, max(0, len(rows)-1))
            if action == "back":
                self.modal, self.menu_index = ("menu", 0) if self.modal == "quality" else ("", 0)
            elif action in ("up", "down") and rows:
                self.menu_index = (self.menu_index + (1 if action == "down" else -1)) % len(rows)
            elif action in ("left", "right") and self.modal == "menu":
                if not self.adjust_menu_setting(1 if action == "right" else -1) and rows:
                    self.menu_index = (self.menu_index+(1 if action == "right" else -1)) % len(rows)
            elif action == "ok" and rows:
                if self.modal == "quality":
                    self.choose_quality()
                elif self.modal == "countries":
                    self.select_country(rows[self.menu_index][0])
                    self.browse()
                    self.modal, self.view, self.focus = "", "browse", "list"
                else:
                    self.menu_action()
            return
        if action == "menu":
            self.modal, self.menu_index = "menu", 0
        elif action == "back":
            if self.view in ("video", "matrix"):
                self.browse()
            elif self.country or self.query:
                self.select_country("")
            else:
                self.modal, self.menu_index = "exit", 0
        elif action == "pause":
            if self.cell in self.players:
                self.players[self.cell].pause()
        elif self.view in ("video", "matrix"):
            if action in ("minus", "plus"):
                self.setting("volume", max(0, min(100, self.state.data["volume"] + (5 if action == "plus" else -5))))
                for player in self.players.values():
                    player.set_volume(self.state.data["volume"])
            elif self.view == "matrix" and action in ("up", "down", "left", "right", "next", "prev"):
                if action in ("up", "down"):
                    cell = (self.cell+2) % 4
                elif action in ("left", "right"):
                    cell = self.cell ^ 1
                else:
                    cell = (self.cell+(1 if action == "next" else -1)) % 4
                self.audio_focus(cell)
            elif action == "ok":
                if self.cell in self.players:
                    self.players[self.cell].pause()
        elif action in ("prev", "next"):
            self.channel_reveals.clear()
            self.tab = (self.tab + (1 if action == "next" else -1)) % len(TABS)
            self.refilter()
        elif action == "select":
            self.focus = "globe" if self.focus == "list" else "list"
        elif action in ("minus", "plus"):
            self.globe.zoom_by(1.2 if action == "plus" else 1/1.2)
        elif action == "search":
            self.modal, self.draft = "search", self.query
            self.pointer_x, self.pointer_y = 320., 425.
        elif action == "favorite" and self.selected():
            self.state.favorite(self.selected())
            self.refilter()
        elif action == "random":
            self.random_station()
        elif self.focus == "globe":
            if action in ("up", "down", "left", "right"):
                self.globe.rotate({"left": -6, "right": 6}.get(action, 0), {"up": 5, "down": -5}.get(action, 0))
            elif action == "ok":
                props = country_at(self.features, self.globe.lat, self.globe.lon)
                if props:
                    self.select_country(props["code"])
                    self.focus = "list"
                else:
                    self.toast("Ocean at crosshair. Rotate to land or use country menu.")
        elif action in ("up", "down", "left", "right") and self.rows:
            delta = {"up": -1, "down": 1, "left": -VISIBLE_ROWS, "right": VISIBLE_ROWS}[action]
            self.index = max(0, min(len(self.rows)-1, self.index+delta))
            row = self.selected()
            if self.country in self.by_country and not any(m[0] == row["uuid"] for m in self.markers):
                self.markers = self.markers[:119] + [(row["uuid"], *station_location(self.by_country[self.country], row["uuid"]))]
        elif action == "ok":
            self.play(self.selected())

    def update(self):
        while True:
            try:
                name, value, error = self.events.get_nowait()
            except queue.Empty:
                break
            self.workers.discard(name)
            if name.startswith("country:"):
                code = name.split(":", 1)[1]
                if code != self.country:
                    continue
                if error:
                    self.error, self.modal = error, "error"
                else:
                    self.stations = value
                    self.refilter()
                    if self.view == "browse" and self.tab == 0:
                        self.start_channel_reveal()
                self.dirty = True
                continue
            if name.startswith("stream:"):
                _, generation, cell = name.split(":")
                cell = int(cell)
                if int(generation) != self.stream_generation or cell not in self.pending_streams:
                    continue
                station, matrix = self.pending_streams.pop(cell)
                if error:
                    self.statuses[cell] = {"error": error}
                else:
                    try:
                        self.finish_player(cell, value, matrix)
                        if self.modal == "quality" and cell == self.cell:
                            self.open_quality()
                    except (MediaError, ValueError, OSError) as exc:
                        self.statuses[cell] = {"error": str(exc)}
                self.dirty = True
                continue
            if error:
                self.toast("%s: %s" % (name, error))
                self.error, self.modal = error, "error"
            else:
                self.refilter()
                self.toast("Archive scan complete")
        changed = False
        for cell, player in self.players.items():
            status = player.poll()
            visible = ("paused", "buffering", "error", "ended", "source_width", "source_height", "source_fps")
            if any(status.get(k) != self.statuses.get(cell, {}).get(k) for k in visible):
                changed = True
                refresh_quality = self.modal == "quality" and cell == self.cell
            else:
                refresh_quality = False
            self.statuses[cell] = status
            if refresh_quality:
                self.open_quality()
            if status.get("quality_warning") and cell not in self.warned_cells:
                self.playback_notice(cell, status["quality_warning"])
                self.warned_cells.add(cell)
            frame = player.frame()
            if frame is not None:
                self.d.sdl.SDL_UpdateTexture(self.video_textures[cell], None, frame, player.width*4)
                self.frame_cells.add(cell)
                changed = True
        if self.pending_audio is not None and not any(s.get("audio_active") for s in self.statuses.values()):
            if self.pending_audio in self.players:
                self.players[self.pending_audio].set_audible(True)
            self.pending_audio = None
        now = time.monotonic()
        over_globe = self.pointer_on_globe(self.pointer_x, self.pointer_y)
        can_spin = (bool(self.state.data["globe_rotation_speed"])
                    and self.view == "browse" and not self.modal and not self.country and self.input_active
                    and self.drag_origin is None and not over_globe and now-self.last_globe_touch > 2
                    and self.globe.flight is None and self.globe.zoom <= 1.5)
        if can_spin and not self.idle_spinning:
            self.spin_started = now
        if not can_spin or not self.idle_spinning:
            self.last_spin_frame = now
        self.idle_spinning = bool(can_spin)
        self.globe.spinning = self.idle_spinning
        if can_spin and now-self.last_spin_frame >= 1/15:
            ramp = min(1., max(0., (now-self.spin_started)/.8))
            speed = 2.4*self.state.data["globe_rotation_speed"]*ramp*ramp*(3-2*ramp)
            self.globe.lon = (self.globe.lon+speed*min(.15, now-self.last_spin_frame)+180) % 360-180
            self.last_spin_frame, changed = now, True
        if self.view == "browse" and not self.modal:
            if self.globe.advance_flight(now):
                changed = True
            revealing = any(item["reveal"] < len(item["content"]) for item in self.channel_reveals.values())
            if revealing and now-self.reveal_tick >= SCRAMBLE_TICK:
                for item in self.channel_reveals.values():
                    if item["reveal"] >= len(item["content"]):
                        continue
                    if item["start"] is None:
                        item["start"] = item["last"] = now
                    else:
                        advance_scramble(item, now-item["last"])
                        item["last"] = now
                    break
                self.reveal_salt += 1
                self.reveal_tick, changed = now, True
        until = getattr(self.globe, "moving_until", 0.)
        moving = isinstance(until, (int, float)) and now < until
        loading = bool(self.pending_streams) or any(
            not s.get("error") and not s.get("ended") and (s.get("buffering") or cell not in self.frame_cells)
            for cell, s in self.statuses.items())
        notices = tuple(cell for cell, note in self.playback_notices.items() if now < note[2])
        fade_until = getattr(self.globe, "fade_until", 0.)
        fading = isinstance(fade_until, (int, float)) and now < fade_until
        tracing = self.view == "browse" and not self.modal and bool(self.country) and self.globe.trace_active is True
        signature = (now < self.notice_until, notices, self.view != "browse" and now-self.last_input < 2.5, moving,
                     int(now*20) if fading else None, int(now*12) if tracing else None,
                     int(now*16) if loading or (self.view != "browse" and self.state.data["crt"] and self.state.data["animated"]) else None)
        if signature != self.timer_signature:
            self.timer_signature, changed = signature, True
        if changed or now < self.redraw_until:
            self.dirty = True

    def window_event(self, event):
        if event in (2, 7, 13):  # Hidden, minimized, or focus lost.
            if self.handheld:
                # KMS has no window manager. Queued initialization focus events
                # do not mean TV lost the display; the ES owner check is authoritative.
                if event not in self.reported_focus_events:
                    print("KMS focus event %s ignored; EmulationStation ownership remains enforced." % event, flush=True)
                    self.reported_focus_events.add(event)
                return
            self.input_active = False
            self.drag_origin = None
            return
        if event in (1, 9, 12):
            self.input_active = True
        if event in (1, 3, 5, 6, 9, 12):
            self.redraw_until = time.monotonic()+.35
            self.dirty = True

    def pointer_on_globe(self, x=None, y=None):
        if x is None:
            x, y = self.pointer_x, self.pointer_y
        radius = MAP_RADIUS*self.globe.zoom
        return (0 <= x < MAP_WIDTH and 84 <= y < 438
                and (x-MAP_CENTER_X)**2+(y-MAP_CENTER_Y)**2 <= radius*radius)

    def move_pointer(self, x, y):
        if not self.input_active:
            return
        x = max(0, min(619, x))
        y = max(240 if self.modal == "search" else 0, min(455, y))
        dx, dy = x-self.pointer_x, y-self.pointer_y
        if not dx and not dy:
            return
        self.pointer_x, self.pointer_y, self.pointer_active = x, y, True
        now = time.monotonic()
        self.last_input, self.dirty = now, True
        if self.drag_origin is not None:
            self.last_globe_touch = now
            self.globe.rotate(-dx*.35, dy*.3)
            if math.hypot(x-self.drag_origin[0], y-self.drag_origin[1]) > 3:
                self.dragged = True
        elif self.pointer_on_globe(x, y):
            self.last_globe_touch = now
        elif self.modal == "search" and y >= 250:
            self.keyboard_row = max(0, min(4, round((y-273)/38)))
            boxes = key_geometry(self.keyboard_row)
            self.keyboard_col = min(range(len(boxes)), key=lambda i: abs(boxes[i][0]+boxes[i][2]/2-x))
        elif self.modal in ("menu", "countries", "quality") and 20 <= x < 620 and MENU_Y <= y < MENU_Y+MENU_ROWS*MENU_STEP:
            items = (self.country_items() if self.modal == "countries" else
                     self.quality_rows if self.modal == "quality" else self.menu_items())
            self.menu_index = min(max(0, len(items)-1), max(0, self.menu_index-(MENU_ROWS-1))+int((y-MENU_Y)//MENU_STEP))
        elif self.view == "browse" and not self.modal and LIST_X <= x < 632 and LIST_Y <= y < LIST_Y+ROW_HEIGHT*VISIBLE_ROWS and self.rows:
            self.index = min(len(self.rows)-1, max(0, self.index-(VISIBLE_ROWS-1))+int((y-LIST_Y)//ROW_HEIGHT))

    def pointer_down(self):
        if not self.input_active:
            return
        x, y = self.pointer_x, self.pointer_y
        if self.view == "browse" and not self.modal and x < MAP_WIDTH and 84 <= y < 438:
            self.drag_origin, self.dragged = (x, y), False
            self.last_globe_touch = time.monotonic()
        else:
            self.click_pointer()

    def pointer_up(self):
        if not self.input_active:
            return
        if self.drag_origin is not None:
            if not self.dragged:
                x = (self.pointer_x-MAP_CENTER_X)/(MAP_RADIUS*self.globe.zoom)
                y = (MAP_CENTER_Y-self.pointer_y)/(MAP_RADIUS*self.globe.zoom)
                from tv_backend import unproject
                position = unproject(x, y, self.globe.lat, self.globe.lon)
                if position:
                    country = country_at(self.features, *position)
                    if country:
                        self.select_country(country["code"])
                        self.focus = "list"
            self.drag_origin = None

    def click_pointer(self):
        x, y = self.pointer_x, self.pointer_y
        if self.modal:
            self.action("ok")
        elif self.view == "browse":
            if 44 <= y < 79:
                self.channel_reveals.clear()
                self.tab = min(3, int(x//160))
                self.refilter()
            elif x >= LIST_X and LIST_Y <= y < LIST_Y+VISIBLE_ROWS*ROW_HEIGHT:
                self.play(self.selected())
        elif self.view == "matrix":
            cell = int(x >= 326)+2*int(y >= 246)
            self.audio_focus(cell)
        else:
            self.action("pause")

    def footer(self, items):
        d = self.d
        # Explicit boxes keep spacing and alignment independent of whitespace.
        widths = [max(20, d.measure(key, 11)[0]+10)+d.measure(label, 13)[0]+22 for key, label in items]
        scale = min(1., 616/sum(widths))
        widths = [int(w*scale) for w in widths]
        x = (640-sum(widths))//2
        d.rect(0, 445, 640, 35, PANEL)
        d.line(12, 445, 628, 445, EDGE)
        for (key, label), width in zip(items, widths):
            key_width = max(20, d.measure(key, 11)[0]+10)
            d.rect(x+3, 452, key_width, 21, (16, 46, 29, 255))
            d.text(key, x+3, 455, GREEN, 11, key_width, align="center")
            d.text(label, x+key_width+8, 455, TEXT, 13, width-key_width-10)
            x += width

    def loading(self, rect, note=None, animate=True):
        x, y, w, h = rect
        d = self.d
        d.copy(d.logo, (int(x+w/2-34), int(y+h/2-24), 67, 22))
        phase = int(time.monotonic()*12) % 12
        for i in range(12):
            if animate:
                d.rect(x+w/2-48+i*8, y+h/2+14, 5, 3,
                       GREEN if (i-phase) % 12 < 3 else (20, 54, 35, 255))
        if note:
            for i, line in enumerate(note[:2]):
                d.text(line, x+12, y+h/2+32+i*17, GREY, 11, w-24, align="center")

    def header_status(self):
        country_job = "country:"+self.country
        if country_job in self.workers:
            return "Loading " + self.catalog.countries.get(self.country, self.country)
        workers = [name for name in sorted(self.workers) if not name.startswith(("stream:", "country:"))]
        if workers:
            return " / ".join({"catalog": "Loading catalog", "health": "Checking channels"}.get(name, name.title()) for name in workers)
        return self.notice if time.monotonic() < self.notice_until else ""

    def header(self, label="TV", detail=None):
        d = self.d
        d.rect(0, 0, 640, 43, BG)
        d.text(label, 14, 16, DIM, 13, 250)
        d.copy(d.logo, (286, 10, 67, 22))
        status = self.header_status()
        if status:
            d.text(status, 367, 17, GREY, 11, 259)
        else:
            d.text(detail or ("Preview" if self.demo else "Live"), 521, 16, GREEN, 13, 104, align="right")
        d.line(0, 42, 640, 42, EDGE)

    def draw_browse(self):
        d = self.d
        d.rect(0, 44, 640, 35, PANEL)
        for i, tab in enumerate(TABS):
            x = 12 + i*157
            if i == self.tab:
                d.rect(x-4, 47, 149, 28, (14, 54, 29, 255))
                d.rect(x-4, 73, 149, 2, GREEN)
            d.text(tab.upper(), x+6, 53, GREEN if i == self.tab else DIM, 13)
        self.globe.draw(self.country, self.markers, self.selected().get("uuid", ""),
                        viewport=MAP_VIEWPORT, bounds=(0, 84, MAP_WIDTH, 354))
        if self.focus == "globe":
            d.line(MAP_CENTER_X-12, MAP_CENTER_Y, MAP_CENTER_X+12, MAP_CENTER_Y, GREEN)
            d.line(MAP_CENTER_X, MAP_CENTER_Y-12, MAP_CENTER_X, MAP_CENTER_Y+12, GREEN)
        d.text("%+.1f / %+.1f" % (self.globe.lat, self.globe.lon), 12, 91, DIM, 11)
        d.text("x%.1f" % self.globe.zoom, 366, 91, DIM, 11)
        name = self.catalog.countries.get(self.country, "THE WORLD")
        d.text(name.upper(), 18, 401, GREEN, 18, MAP_WIDTH-36)
        d.text("%s stations / COUNTRY SIGNALS" % len(self.rows) if self.country else "Choose a country to load channels",
               18, 424, DIM, 11, MAP_WIDTH-36)
        d.line(MAP_WIDTH, 87, MAP_WIDTH, 440, EDGE)
        top = max(0, self.index-(VISIBLE_ROWS-1))
        for i, row in enumerate(self.rows[top:top+VISIBLE_ROWS], top):
            y = LIST_Y+(i-top)*ROW_HEIGHT
            selected = i == self.index
            if selected:
                d.rect(LIST_X, y, 210, 39, (14, 50, 29, 255))
                d.rect(LIST_X, y, 210, 39, GREEN if self.focus == "list" else EDGE, True)
            if row["uuid"] in self.state.data["favorites"]:
                d.text("*", 615, y+2, GOLD, 15)
            name = row["name"][:15]
            reveal = self.channel_reveals.get(row["uuid"])
            if reveal and reveal["content"] == name and reveal["reveal"] < len(name):
                if reveal["start"] is None:
                    d.text("...", LIST_X+9, y+4, DIM, 13, 177)
                    continue
                cut = reveal["reveal"]
                if cut:
                    d.text(name[:cut], LIST_X+9, y+4, TEXT, 13, 177)
                offset = d.measure(name[:cut], 13)[0] if cut else 0
                d.text(scramble_text(name[cut:], self.reveal_salt+i*17), LIST_X+9+offset,
                       y+4, GREEN, 13, 177-offset)
            else:
                d.text(name, LIST_X+9, y+4, TEXT, 13, 177)
            detail = "%s / %s" % (row.get("countryCode") or "--", row.get("language") or row.get("tags") or "LIVE TV")
            if self.tab == 3:
                record = self.health.data.get(row["uuid"], {})
                stamp = record.get("deadSince")
                detail = "DARK SINCE " + time.strftime("%d %b", time.localtime(stamp)) if stamp else "UNREACHABLE"
            d.text(detail, LIST_X+9, y+23, DIM, 11, 190)
        if not self.rows:
            loading = "country:"+self.country in self.workers
            label = "Choose a country" if not self.country and self.tab == 0 else "Loading channels" if loading else "No matching stations"
            d.text(label, LIST_X+9, 190, TEXT, 13, 190)
            d.text("Globe or START menu", LIST_X+9, 216, DIM, 11, 190)
        d.text("%s / %s" % (self.index+1 if self.rows else 0, len(self.rows)), LIST_X+5, 428, DIM, 11, 100)
        if self.query:
            d.text(self.query, 532, 428, GREEN, 11, 96, align="right")

    def crt(self, rect):
        if not self.state.data["crt"]:
            return
        d = self.d
        x, y, w, h = rect
        intense = self.state.data["intense"]
        d.rect(x, y, w, h, (92, 255, 133, 51 if intense else 15))
        for line_y in range(y, y+h, 2):
            d.rect(x, line_y, w, 1, (0, 0, 0, 116 if intense else 35))
        for i in range(10):
            alpha = int((10-i) * (6 if intense else 3))
            d.rect(x+i, y+i, w-2*i, h-2*i, (0, 0, 0, alpha), True)
        if self.state.data["animated"]:
            band = int((time.monotonic() % 9)/9*(h+100))-50
            if 0 <= band < h-5:
                d.rect(x, y+band, w, 5, (124, 252, 0, 22 if intense else 8))

    def draw_video(self):
        d = self.d
        matrix = self.view == "matrix"
        for cell in range(4 if matrix else 1):
            rect = (16+(cell % 2)*310, 16+(cell//2)*230, 298, 218) if matrix else (0, 0, 640, 480)
            x, y, w, h = rect
            if cell in self.video_textures:
                d.copy(self.video_textures[cell], rect)
            else:
                d.rect(*rect, (0, 0, 0, 255))
            self.crt(rect)
            status = self.statuses.get(cell, {})
            note = self.playback_notices.get(cell)
            loading = (cell in self.pending_streams or status.get("buffering")
                       or (cell in self.players and cell not in self.frame_cells))
            if status.get("error"):
                d.text("SIGNAL UNAVAILABLE", x+12, y+60, RED, 13, w-24)
                d.text(status["error"], x+12, y+84, DIM, 11, w-24)
            elif loading or (note and time.monotonic() < note[2]):
                self.loading(rect, note, animate=bool(loading))
            elif status.get("ended"):
                d.text("STREAM ENDED", x+12, y+60, DIM, 13, w-24)
            if matrix:
                d.rect(x, y+h-30, w, 30, (0, 0, 0, 220))
                d.text(("> " if cell == self.cell else "  ") + self.playing.get(cell, {}).get("name", "EMPTY SLOT"),
                       x+7, y+h-24, GREEN if cell == self.cell else DIM, 13, w-14)
                d.rect(*rect, GREEN if cell == self.cell else EDGE, True)
        if time.monotonic()-self.last_input < 2.5 or self.modal:
            station = self.playing.get(self.cell if matrix else 0, {})
            self.header("Watch matrix" if matrix else station.get("name", "Live TV"),
                        "Vol %s%%" % self.state.data["volume"])
            self.footer((("A", "Pause"), ("B", "Stop"), ("L2/R2", "Volume"), ("START", "Options")))

    def draw_modal(self):
        if not self.modal:
            return
        d = self.d
        if self.modal == "search":
            d.copy(d.backdrop, (0, 0, 640, 480))
            d.copy(d.logo, (286, 17, 67, 22))
            d.text("Find your next channel", 12, 100, GREEN, 22, 616, align="center")
            hint = ("Search channels in " + self.catalog.countries.get(self.country, self.country)
                    if self.country else "Choose a country, or search Favorites / Recent")
            d.text(hint, 12, 137, DIM, 13, 616, align="center")
            d.rect(12, 187, 616, 46, PANEL)
            d.rect(12, 187, 616, 46, EDGE, True)
            d.text(self.draft + "_", 25, 199, TEXT, 18, 590)
            keyboard(d, self.keyboard_row, self.keyboard_col)
            self.footer((("A", "Type"), ("B", "Delete"), ("X", "Find"), ("Y", "Space")))
            return
        if self.modal in ("menu", "countries", "quality"):
            d.copy(d.backdrop, (0, 0, 640, 480))
            self.header("Settings" if self.modal != "countries" else "Countries")
            if self.modal == "countries":
                title, items = "Browse countries", ["%s   %s" % pair for pair in self.country_items()]
                caption = "Choose a country to explore its broadcasts"
            elif self.modal == "quality":
                title, items = "Resolution / frame rate", [self.quality_label(row) for row in self.quality_rows]
                caption = ("Choose an available broadcast format" if len(items) > 2 else
                           "This channel offers no other advertised formats")
            else:
                title, items = "Settings", self.menu_items()
                station = self.playing.get(self.cell, {})
                caption = station.get("name", "Make yourself at home")
            d.text(title, 20, 57, GREEN, 22, 600, align="center")
            d.text(caption, 20, 87, GREY, 11, 600, align="center")
            top = max(0, self.menu_index-(MENU_ROWS-1))
            for i, item in enumerate(items[top:top+MENU_ROWS], top):
                y = MENU_Y+(i-top)*MENU_STEP
                if i == self.menu_index:
                    d.rect(20, y, 600, MENU_STEP-3, (14, 50, 29, 255))
                    d.rect(20, y, 3, MENU_STEP-3, GREEN)
                d.text(item, 34, y+7, TEXT, 15, 570)
            self.footer((("A", "Select"), ("B", "Back"), ("D-PAD", "Navigate / adjust")))
            return
        d.rect(0, 0, 640, 480, (0, 0, 0, 190))
        if self.modal == "exit":
            items, title = ["Keep watching", "Exit to EmulationStation"], "LEAVE THE ATLAS?"
        elif self.modal == "error":
            title, items = "TV DIAGNOSTIC", []
        else:
            title, items = "TV DIAGNOSTIC", []
        d.rect(49, 49, 542, 382, BG)
        d.rect(49, 49, 542, 382, EDGE, True)
        d.text(title, 69, 65, GREEN, 22)
        if self.modal == "error":
            words, lines, line = self.error.split(), [], ""
            for word in words:
                if len(line)+len(word) > 54:
                    lines.append(line)
                    line = ""
                line += (" " if line else "") + word
            lines.append(line)
            for i, line in enumerate(lines[:12]):
                d.text(line, 69, 112+i*21, TEXT, 13, 500)
        else:
            top = max(0, self.menu_index-8)
            for i, item in enumerate(items[top:top+9], top):
                y = 108+(i-top)*30
                if i == self.menu_index:
                    d.rect(63, y, 514, 28, (14, 54, 29, 255))
                    d.rect(63, y, 514, 28, GREEN, True)
                d.text(item, 73, y+5, TEXT, 15, 496)
        d.text("A SELECT   B BACK", 69, 402, DIM, 13)

    def draw(self):
        d = self.d
        if self.modal in ("search", "menu", "countries", "quality"):
            self.draw_modal()
            d.pointer.draw(self.pointer_x, self.pointer_y)
            return
        if self.view == "browse":
            d.copy(d.backdrop, (0, 0, 640, 480))
            self.draw_browse()
            self.header()
            items = (("A", "Watch"), ("B", "Back"), ("X", "Find"), ("Y", "Save"), ("START", "Menu"))
            if self.focus == "globe":
                items = (("D-PAD", "Rotate"), ("A", "Country"), ("L2/R2", "Zoom"), ("SELECT", "List"))
            self.footer(items)
        else:
            if self.view == "matrix":
                d.rect(0, 0, 640, 480, (0, 0, 0, 255))
            self.draw_video()
        self.draw_modal()
        if self.view == "browse" or self.modal or time.monotonic()-self.last_input < 2.5:
            d.pointer.draw(self.pointer_x, self.pointer_y)

    def close(self):
        self.cancel.set()
        self.stop()
        self.globe.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desktop", action="store_true")
    parser.add_argument("--demo", action="store_true", help="Offline sample catalog; never tunes streams")
    parser.add_argument("--screenshot", type=Path, help="Render one frame and exit (requires --demo)")
    parser.add_argument("--scene", choices=("browse", "country", "search", "matrix", "settings", "quality", "loading"), default="browse")
    parser.add_argument("--skip-intro", action="store_true")
    parser.add_argument("--run-seconds", type=float, help="Exit after a bounded display check")
    parser.add_argument("--device-test", action="store_true", help="Allow a foreground diagnostic lasting at most 60 seconds")
    parser.add_argument("--capture-after", type=Path, help="Capture the visible UI after startup")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("TV227_DATA", str(Path.home()/".local/share/melkor227-tv"))))
    args = parser.parse_args(argv)
    if args.screenshot and (not args.demo or not args.screenshot.parent.is_dir()):
        parser.error("Screenshots require --demo and an existing output directory")
    if args.device_test and (args.run_seconds is None or not 0 < args.run_seconds <= 60):
        parser.error("--device-test requires --run-seconds in the range 0..60")
    handheld = not (args.desktop or args.screenshot) and os.environ.get("SDL_VIDEODRIVER", "kmsdrm") == "kmsdrm"
    owner = display_owner() if handheld and not args.device_test else None
    if handheld and not args.device_test and owner is None:
        parser.error("Open TV from EmulationStation's Ports or Tools menu, not from SSH or a background process. "
                     "Bounded diagnostics require --device-test --run-seconds N.")
    if owner is not None:
        print("Handheld display owned by EmulationStation PID %s." % owner[0], flush=True)
    display, app, intro = Display(args.desktop or bool(args.screenshot)), None, None
    try:
        if not args.skip_intro and not args.screenshot:
            intro = Intro(display, ASSETS)
        # Preview must never touch live favorites, health state or catalog.
        import tempfile
        with tempfile.TemporaryDirectory(prefix="melkor227-preview-") as temp:
            app = App(display, Path(temp) if args.demo else args.data_dir, args.demo)
            app.handheld = handheld
            if args.demo and args.scene == "search":
                app.modal, app.draft = "search", "arabic"
            elif args.demo and args.scene == "country":
                app.select_country("SY")
            elif args.demo and args.scene == "matrix":
                app.view = "matrix"
                app.playing = dict(enumerate(app.stations[:4]))
                app.statuses = {i: {"error": "Offline preview / no video"} for i in range(4)}
            elif args.demo and args.scene in ("settings", "quality", "loading"):
                app.view = "video"
                app.playing[0] = dict(app.stations[0], renditions=[
                    dict(url="https://example.test/%s-%s.m3u8" % (h, fps), height=h, fps=fps)
                    for h, fps in ((240, 25), (360, 25), (480, 25), (720, 25), (720, 50))])
                if args.scene == "quality":
                    app.open_quality()
                elif args.scene == "settings":
                    app.modal = "menu"
                else:
                    app.statuses[0] = {"buffering": True}
                    app.playback_notice(0, "HD SOURCE | 1280x720 - lowest available; playback may be slower")
            event = C.create_string_buffer(64)
            held, repeat_at = {}, {}
            began = previous = time.monotonic()
            owner_check_at = began+.5
            captured = False
            while app.running:
                started = time.monotonic()
                if owner is not None and started >= owner_check_at:
                    if display_owner() != owner:
                        print("EmulationStation launch session ended; closing TV.", flush=True)
                        break
                    owner_check_at = started+.5
                delta = min(.05, started-previous)
                previous = started
                if args.run_seconds and started-began >= args.run_seconds:
                    break
                if args.capture_after and not captured and started-began > 3:
                    app.dirty = True
                while display.sdl.SDL_PollEvent(event):
                    raw = event.raw
                    kind = struct.unpack_from("<I", raw)[0]
                    if kind == 0x100:
                        app.running = False
                    elif kind == 0x200:
                        app.window_event(raw[12])
                        if not app.running:
                            break
                    elif kind == 0x2000:
                        app.globe.key = None
                        app.dirty = True
                    elif kind in (0x603, 0x604):
                        # Consume raw joystick events only, never duplicate controller events.
                        button = raw[12]
                        action = BUTTONS.get(button)
                        if kind == 0x603 and action:
                            if intro and intro.active and button in (0, 1, 13):
                                intro.close()
                                app.dirty = True
                                continue
                            if action == "ok" and app.pointer_active:
                                app.pointer_down()
                            else:
                                if action in ("up", "down", "left", "right"):
                                    app.pointer_active = False
                                app.action(action)
                            held[button], repeat_at[button] = action, started+.35
                        else:
                            if action == "ok":
                                app.pointer_up()
                            held.pop(button, None)
                            repeat_at.pop(button, None)
                    elif kind == 0x602 and raw[13]:
                        for mask, action in ((1, "up"), (2, "right"), (4, "down"), (8, "left")):
                            if raw[13] & mask:
                                app.action(action)
                    elif kind == 0x300 and not raw[13]:
                        key = struct.unpack_from("<i", raw, 20)[0]
                        if intro and intro.active and key in (13, 27, 32):
                            intro.close()
                            app.dirty = True
                        elif app.modal == "search":
                            if key == 13:
                                app.action("search")
                            elif key == 27:
                                app.modal, app.dirty = "", True
                            elif key == 8:
                                app.action("back")
                            elif key in KEYBOARD and KEYBOARD[key] in ("up", "down", "left", "right"):
                                app.pointer_active = False
                                app.action(KEYBOARD[key])
                        elif key in KEYBOARD:
                            app.action(KEYBOARD[key])
                    elif kind == 0x303 and app.modal == "search":
                        app.draft = (app.draft+raw[12:44].split(b"\0", 1)[0].decode("utf-8", "replace"))[:80]
                        app.dirty = True
                    elif kind == 0x400:
                        if args.desktop or any(struct.unpack_from("<ii", raw, 28)):
                            x, y = struct.unpack_from("<ii", raw, 20)
                            app.move_pointer(x, y)
                    elif kind in (0x401, 0x402) and raw[16] == 1:
                        app.move_pointer(*struct.unpack_from("<ii", raw, 20))
                        if intro and intro.active:
                            intro.close()
                        elif kind == 0x401:
                            app.pointer_down()
                        else:
                            app.pointer_up()
                        app.dirty = True
                for button, action in list(held.items()):
                    if not app.input_active:
                        held.clear()
                        repeat_at.clear()
                        break
                    if app.focus == "globe" and app.view == "browse" and not app.modal and action in ("up", "down", "left", "right"):
                        app.last_globe_touch = started
                        app.globe.rotate({"left": -80, "right": 80}.get(action, 0)*delta,
                                         {"up": 65, "down": -65}.get(action, 0)*delta)
                        app.dirty = True
                        continue
                    if action in ("up", "down", "left", "right", "minus", "plus") and started >= repeat_at[button]:
                        app.action(action)
                        repeat_at[button] = started+.10
                if not app.running:
                    break
                if app.input_active and display.joy and display.axes >= 2:
                    x, y = [pointer_motion(display.sdl.SDL_JoystickGetAxis(display.joy, axis)) for axis in (0, 1)]
                    if x or y:
                        speed = pointer_speed(x, y, app.modal == "search")
                        app.move_pointer(app.pointer_x+x*speed*delta, app.pointer_y+y*speed*delta)
                        display.sdl.SDL_WarpMouseInWindow(display.window, int(app.pointer_x), int(app.pointer_y))
                    if display.axes >= 4 and app.view == "browse" and app.modal != "search":
                        scroll = pointer_motion(display.sdl.SDL_JoystickGetAxis(display.joy, 3))
                        app.scroll_fraction += scroll*delta*12
                        if abs(app.scroll_fraction) >= 1:
                            app.action("down" if app.scroll_fraction > 0 else "up")
                            app.scroll_fraction = 0
                app.update()
                intro_active = bool(intro and intro.active)
                if app.dirty or args.screenshot or intro_active:
                    if not intro_active or not intro.covers_ui:
                        app.draw()
                    if intro_active:
                        intro.draw()
                    if args.screenshot:
                        display.screenshot(args.screenshot)
                        break
                    if args.capture_after and not captured and started-began > 3:
                        display.screenshot(args.capture_after)
                        captured = True
                    display.sdl.SDL_RenderPresent(display.renderer)
                    app.dirty = False
                    if intro_active:
                        app.dirty = True
                time.sleep(max(.001, 1/30-(time.monotonic()-started)))
    finally:
        if intro:
            intro.close()
        if app:
            app.close()
        display.close()


if __name__ == "__main__":
    main()

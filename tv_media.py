"""Windowless, synchronized libmpv audio/video for an SDL software renderer.

Pass a station with a ``url`` string (HTTP(S), file://, or a local filename).
Optional ``user_agent`` and ``referrer`` strings are applied to stream requests.
Call poll() and frame() regularly from one UI thread, including while paused.
Controls and loading are asynchronous; poll() reports their eventual state.
No Python callbacks, subprocesses, SDL calls, GPU context, or DRM access are used.

frame() returns None when no redraw is needed, otherwise a reusable ctypes
c_ubyte array: top-to-bottom rows, left-to-right pixels, exactly R,G,B,A bytes
per pixel (mpv's "rgba", NOT native-endian SDL_PIXELFORMAT_RGBA8888). Use SDL's
SDL_PIXELFORMAT_RGBA32 with pitch width * 4 and SDL_BLENDMODE_NONE (mpv may
give black letterbox pixels alpha=0). The buffer has no row padding;
width and height never change. Copy/upload it before the next frame() call,
which overwrites the same storage. Letterboxing is performed by mpv.

Nonfocused players must use audible=False. This selects aid=no, disabling the
audio track/decoder/output rather than merely muting an open ALSA device. When
changing focus, disable the old player's audio and wait for poll()['audio_active']
to become False before enabling the new one. TV_MEDIA_AO=null selects mpv's
clocked null audio output for host/headless tests; unset it for real sound.
Explicitly close players before shutting down the UI. Initialization failures
raise MediaError; stream/runtime errors are retained in poll()['error'] until
the next play(). availability() probes the real SW context and RGBA renderer.
"""

import ctypes as C
import ctypes.util
from functools import lru_cache
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

from tv_quality import quality_warning


class MediaError(RuntimeError):
    """libmpv is unavailable, unsupported, or could not perform an operation."""


class _RenderParam(C.Structure):
    _fields_ = [("type", C.c_int), ("data", C.c_void_p)]


class _Event(C.Structure):
    _fields_ = [("event_id", C.c_int), ("error", C.c_int),
                ("reply_userdata", C.c_uint64), ("data", C.c_void_p)]


class _Property(C.Structure):
    _fields_ = [("name", C.c_char_p), ("format", C.c_int),
                ("data", C.c_void_p)]


class _EndFile(C.Structure):
    # Only the stable prefix is needed, including on libmpv.so.1.
    _fields_ = [("reason", C.c_int), ("error", C.c_int)]


class _Log(C.Structure):
    _fields_ = [("prefix", C.c_char_p), ("level", C.c_char_p),
                ("text", C.c_char_p), ("log_level", C.c_int)]


_STRING, _FLAG, _INT64, _DOUBLE = 1, 3, 4, 5
_UPDATE_FRAME = 1
_PROPERTIES = {b"pause": _FLAG, b"paused-for-cache": _FLAG,
               b"time-pos": _DOUBLE, b"duration": _DOUBLE, b"current-ao": _STRING,
               b"width": _INT64, b"height": _INT64,
               b"dwidth": _INT64, b"dheight": _INT64,
               b"container-fps": _DOUBLE,
               b"frame-drop-count": _INT64, b"decoder-frame-drop-count": _INT64}


@lru_cache(maxsize=1)
def _library():
    signatures = {
        "mpv_create": (C.c_void_p, []),
        "mpv_initialize": (C.c_int, [C.c_void_p]),
        "mpv_terminate_destroy": (None, [C.c_void_p]),
        "mpv_error_string": (C.c_char_p, [C.c_int]),
        "mpv_set_option_string": (C.c_int, [C.c_void_p, C.c_char_p, C.c_char_p]),
        "mpv_set_property_async": (C.c_int, [C.c_void_p, C.c_uint64,
                                            C.c_char_p, C.c_int, C.c_void_p]),
        "mpv_command_async": (C.c_int, [C.c_void_p, C.c_uint64,
                                      C.POINTER(C.c_char_p)]),
        "mpv_observe_property": (C.c_int, [C.c_void_p, C.c_uint64,
                                         C.c_char_p, C.c_int]),
        "mpv_request_log_messages": (C.c_int, [C.c_void_p, C.c_char_p]),
        "mpv_wait_event": (C.POINTER(_Event), [C.c_void_p, C.c_double]),
        "mpv_render_context_create": (C.c_int, [C.POINTER(C.c_void_p),
                                              C.c_void_p, C.POINTER(_RenderParam)]),
        "mpv_render_context_update": (C.c_uint64, [C.c_void_p]),
        "mpv_render_context_render": (C.c_int, [C.c_void_p, C.POINTER(_RenderParam)]),
        "mpv_render_context_free": (None, [C.c_void_p]),
    }
    failures = []
    candidates = ["libmpv.so.2", "libmpv.so.1"]
    found = ctypes.util.find_library("mpv")
    if found and found not in candidates:
        candidates.append(found)
    for name in candidates:
        try:
            lib = C.CDLL(name)
            for symbol, (result, args) in signatures.items():
                fn = getattr(lib, symbol)
                fn.restype, fn.argtypes = result, args
            return lib
        except (OSError, AttributeError) as exc:
            failures.append("{}: {}".format(name, exc))
    raise MediaError("libmpv unavailable or missing render API; install libmpv "
                     "with software-render support. " + "; ".join(failures))


class Player:
    """One mpv engine and fixed-size RGBA surface. Use from one thread only."""

    def __init__(self, width=640, height=480, volume=70, audible=True):
        if (not isinstance(width, int) or not isinstance(height, int)
                or isinstance(width, bool) or isinstance(height, bool)
                or not 1 <= width <= 8192 or not 1 <= height <= 8192):
            raise ValueError("width and height must be integers in 1..8192")
        self._width, self._height = width, height
        self._handle = None
        self._context = C.c_void_p()
        self._closed = False
        self._generation = 1
        self._serial = 0
        self._pending = {}
        self._audio_request = None
        self._props = {}
        self._loading = self._loaded = self._ended = False
        self._error = self._last_log = ""
        self._quality_warning = ""
        self._playlists = None
        self._volume = max(0, min(100, int(volume)))
        self._audible = bool(audible)
        self._lib = _library()

        # Retain all pointer targets, including the overallocated aligned owner.
        self._storage = (C.c_ubyte * (width * height * 4 + 63))()
        address = (C.addressof(self._storage) + 63) & ~63
        self._buffer = (C.c_ubyte * (width * height * 4)).from_buffer(
            self._storage, address - C.addressof(self._storage))
        self._size = (C.c_int * 2)(width, height)
        self._stride = C.c_size_t(width * 4)
        self._format = C.create_string_buffer(b"rgba")
        self._block_for_target_time = C.c_int(0)
        self._params = (_RenderParam * 6)(
            _RenderParam(17, C.addressof(self._size)),
            _RenderParam(18, C.addressof(self._format)),
            _RenderParam(19, C.addressof(self._stride)),
            _RenderParam(20, address),
            _RenderParam(12, C.addressof(self._block_for_target_time)), _RenderParam())
        try:
            self._handle = self._lib.mpv_create()
            if not self._handle:
                raise MediaError("libmpv: mpv_create failed")
            options = {
                "config": "no", "load-scripts": "no", "scripts": "",
                "ytdl": "no", "osc": "no", "osd-level": "0", "terminal": "no",
                "input-terminal": "no", "input-default-bindings": "no",
                "input-vo-keyboard": "no", "vo": "libmpv",
                "force-window": "no", "audio-display": "no", "hwdec": "no",
                "vd-lavc-threads": "2", "vd-lavc-dr": "no",
                "sid": "no", "sub-auto": "no", "audio-file-auto": "no",
                "idle": "yes", "keep-open": "no", "gapless-audio": "no",
                "video-sync": "audio", "video-timing-offset": "0",
                "cache": "yes", "cache-secs": "5", "cache-on-disk": "no",
                "demuxer-max-bytes": "8MiB", "demuxer-max-back-bytes": "1MiB",
                "network-timeout": "10", "tls-verify": "yes",
                "aid": "auto" if self._audible else "no",
                "volume": str(self._volume),
            }
            # Length quoting preserves commas in mpv's key/value option parser.
            protocols = "file,http,https,tcp,tls,crypto"
            whitelist = "protocol_whitelist=%{}%{}".format(len(protocols), protocols)
            options["demuxer-lavf-o"] = whitelist
            options["stream-lavf-o"] = whitelist
            if os.environ.get("TV_MEDIA_AO"):
                options["ao"] = os.environ["TV_MEDIA_AO"]
            for name, value in options.items():
                self._check(self._lib.mpv_set_option_string(
                    self._handle, name.encode(), value.encode()), "option " + name)
            # These optimizations are optional on older handheld libmpv builds.
            for name, value in ((b"hls-bitrate", b"min"), (b"sws-scaler", b"fast-bilinear"),
                                (b"sws-fast", b"yes")):
                code = self._lib.mpv_set_option_string(self._handle, name, value)
                if code not in (-5, -6):  # option not found / unsupported value
                    self._check(code, "option " + name.decode())
            self._check(self._lib.mpv_initialize(self._handle), "initialize")
            api = C.create_string_buffer(b"sw")
            params = (_RenderParam * 2)(_RenderParam(1, C.addressof(api)), _RenderParam())
            code = self._lib.mpv_render_context_create(
                C.byref(self._context), self._handle, params)
            self._check(code, "software renderer unsupported (api_type=sw)")
            self._check(self._lib.mpv_render_context_render(self._context, self._params),
                        "software renderer does not support rgba")
            for name, fmt in _PROPERTIES.items():
                self._check(self._lib.mpv_observe_property(self._handle, 0, name, fmt),
                            "observe " + name.decode())
            self._check(self._lib.mpv_request_log_messages(self._handle, b"error"),
                        "request error logs")
        except BaseException:
            self.close()
            raise

    @property
    def width(self):
        return self._width

    @property
    def height(self):
        return self._height

    @property
    def source_width(self):
        return self._props.get(b"width") or self._props.get(b"dwidth")

    @property
    def source_height(self):
        return self._props.get(b"height") or self._props.get(b"dheight")

    @property
    def dropped_frames(self):
        return self._props.get(b"frame-drop-count") or 0

    @property
    def decoder_dropped_frames(self):
        return self._props.get(b"decoder-frame-drop-count") or 0

    @property
    def quality_warning(self):
        if self.source_width or self.source_height:
            return quality_warning(self.source_width, self.source_height)
        return self._quality_warning

    def _message(self, code):
        return self._lib.mpv_error_string(code).decode("utf-8", "replace")

    def _check(self, code, operation):
        if code < 0:
            raise MediaError("libmpv {}: {} ({})".format(operation, self._message(code), code))

    def _require_open(self):
        if self._closed:
            raise MediaError("Player is closed")

    def _command(self, *args):
        values = (C.c_char_p * (len(args) + 1))(*(arg.encode("utf-8") for arg in args), None)
        self._serial += 1
        self._check(self._lib.mpv_command_async(self._handle, self._serial, values),
                    args[0])
        self._pending[self._serial] = (self._generation, args[0])

    def _set(self, name, value, fmt):
        self._require_open()
        self._serial += 1
        self._check(self._lib.mpv_set_property_async(
            self._handle, self._serial, name, fmt, C.byref(value)), name.decode())
        self._pending[self._serial] = (self._generation, name.decode())
        return self._serial

    def play(self, station):
        """Replace playback with station['url']; invalid input raises ValueError."""
        self._require_open()
        url = station.get("url") if isinstance(station, dict) else None
        if not isinstance(url, str) or not url.strip() or "\0" in url:
            raise ValueError("station must contain a nonempty 'url' string")
        scheme = urlsplit(url).scheme.lower()
        if scheme not in ("", "file", "http", "https"):
            raise ValueError("Only local files and HTTP(S) station URLs are supported")
        headers = ((b"user-agent", station.get("user_agent") or "R36S-TV/1.0"),
                   (b"referrer", station.get("referrer") or ""))
        for name, value in headers:
            if not isinstance(value, str) or any(char in value for char in "\r\n\0"):
                raise ValueError("Invalid station " + name.decode())
        playlist = station.get("playlist")
        if playlist is not None:
            if (not isinstance(playlist, str) or not playlist.startswith("#EXTM3U")
                    or "\0" in playlist or len(playlist.encode()) > 1024*1024):
                raise ValueError("Invalid pinned HLS master")
            if self._playlists is None:
                self._playlists = tempfile.TemporaryDirectory(prefix="tv227-quality-")
            path = Path(self._playlists.name) / ("master-%s.m3u8" % self._generation)
            path.write_text(playlist, encoding="utf-8")
            url = path.as_uri()
        self.poll()  # Consume the previous station's pending errors first.
        self._generation += 1
        self._error = self._last_log = ""
        self._quality_warning = station.get("quality_warning") or ""
        self._props = {b"current-ao": self._props.get(b"current-ao")}
        self._loading, self._loaded, self._ended = True, False, False
        try:
            for name, value in headers:
                self._set(name, C.c_char_p(value.encode("utf-8")), _STRING)
            self._command("loadfile", url, "replace")
            self._set(b"pause", C.c_int(0), _FLAG)
        except MediaError as exc:
            self._error = str(exc)
            self._loading = False

    def frame(self):
        """Return the reusable packed RGBA ctypes array on redraw, else None."""
        if self._closed or self._error:
            return None
        if not self._lib.mpv_render_context_update(self._context) & _UPDATE_FRAME:
            return None
        code = self._lib.mpv_render_context_render(self._context, self._params)
        if code < 0:
            self._error = "libmpv software rgba render: " + self._message(code)
            self._loading = False
            return None
        return self._buffer

    def poll(self):
        """Nonblocking status snapshot; time/duration are seconds or None.

        error is an empty string on success. audio_active is conservative: True
        also means an audio control is pending, so focus handoff must wait.
        source_width/source_height describe decoded video, not the RGBA surface.
        quality_warning is UI text, not an error; unknown quality is silent.
        ended distinguishes EOF (including servers closing a live stream
        without an mpv error) from buffering.
        """
        if not self._closed:
            # Bound work even if a broken stream floods the log/event queue.
            for _ in range(256):
                event = self._lib.mpv_wait_event(self._handle, 0).contents
                kind = event.event_id
                if kind == 0:
                    break
                if kind == 22 and event.data:
                    prop = C.cast(event.data, C.POINTER(_Property)).contents
                    if prop.name in _PROPERTIES:
                        value = None
                        if prop.data and prop.format == _FLAG:
                            value = bool(C.cast(prop.data, C.POINTER(C.c_int)).contents.value)
                        elif prop.data and prop.format == _DOUBLE:
                            value = C.cast(prop.data, C.POINTER(C.c_double)).contents.value
                        elif prop.data and prop.format == _INT64:
                            value = C.cast(prop.data, C.POINTER(C.c_int64)).contents.value
                        elif prop.data and prop.format == _STRING:
                            raw = C.cast(prop.data, C.POINTER(C.c_char_p)).contents.value
                            value = (raw or b"").decode("utf-8", "replace")
                        self._props[prop.name] = value
                elif kind == 6:
                    self._loading, self._loaded, self._ended = True, False, False
                    self._error = self._last_log = ""
                elif kind == 8:
                    self._loading, self._loaded = False, True
                elif kind == 7 and event.data:
                    end = C.cast(event.data, C.POINTER(_EndFile)).contents
                    if end.reason in (0, 4):
                        self._loading = self._loaded = False
                        self._ended = True
                        if end.reason == 4:
                            self._error = "Playback failed: " + self._message(end.error)
                        if self._last_log:
                            self._error = (self._error + ": " + self._last_log).lstrip(": ")
                elif kind == 2 and event.data:
                    log = C.cast(event.data, C.POINTER(_Log)).contents
                    self._last_log = (log.text or b"").decode("utf-8", "replace").strip()[:1000]
                elif kind in (4, 5):
                    request = self._pending.pop(event.reply_userdata, None)
                    if event.reply_userdata == self._audio_request:
                        self._audio_request = None
                    if request and request[0] == self._generation and event.error < 0:
                        self._error = "Playback {} failed: {}".format(
                            request[1], self._message(event.error))
                        self._loading = False
                elif kind in (1, 24):
                    self._error = ("libmpv shut down" if kind == 1
                                   else "libmpv event queue overflow; restart playback")
                    self._loading = False
        return {
            "paused": bool(self._props.get(b"pause", False)),
            "buffering": bool(not self._closed and not self._error and not self._ended
                              and (self._loading or self._props.get(b"paused-for-cache"))),
            "error": self._error,
            "time": self._props.get(b"time-pos"),
            "duration": self._props.get(b"duration"),
            "source_width": self.source_width, "source_height": self.source_height,
            "source_fps": self._props.get(b"container-fps"),
            "quality_warning": self.quality_warning,
            "dropped_frames": self.dropped_frames,
            "decoder_dropped_frames": self.decoder_dropped_frames,
            "playing": bool(self._loaded and not self._closed and not self._error),
            "ended": self._ended, "closed": self._closed,
            "volume": self._volume, "audible": self._audible and not self._closed,
            "audio_active": bool(not self._closed and
                                 (self._props.get(b"current-ao") or self._audio_request)),
        }

    def pause(self):
        """Toggle pause; poll() reflects the result once mpv processes it."""
        self._require_open()
        self._command("cycle", "pause")

    def set_volume(self, volume):
        """Set volume, clamped to 0..100; this does not change audio focus."""
        volume = max(0, min(100, int(volume)))
        self._set(b"volume", C.c_double(volume), _DOUBLE)
        self._volume = volume

    def set_audible(self, audible):
        """Select aid=auto/no, opening/releasing audio output asynchronously."""
        audible = bool(audible)
        value = C.c_char_p(b"auto" if audible else b"no")
        # MPV_FORMAT_STRING takes char **; wait for this reply during handoff.
        self._audio_request = self._set(b"aid", value, _STRING)
        self._audible = audible

    def close(self):
        """Idempotently free the render context BEFORE destroying its mpv core."""
        if self._closed:
            return
        self._closed = True
        if self._context:
            self._lib.mpv_render_context_free(self._context)
            self._context = C.c_void_p()
        if self._handle:
            self._lib.mpv_terminate_destroy(self._handle)
            self._handle = None
        if self._playlists is not None:
            self._playlists.cleanup()
            self._playlists = None


def availability():
    """Return (available, diagnostic), probing SW/RGBA without opening audio."""
    player = None
    try:
        player = Player(16, 16, audible=False)
        return True, "libmpv software renderer available (rgba; no window/DRM required)"
    except (MediaError, OSError) as exc:
        return False, str(exc)
    finally:
        if player is not None:
            player.close()

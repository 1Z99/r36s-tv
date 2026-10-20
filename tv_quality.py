"""Bounded, pre-decode HLS selection. Call resolve_station off the UI thread.

The returned ``url`` is playable by Player, not a scaled HD stream. Source
identity/headers and retry alternatives are retained; unknown dimensions stay
None until Player observes the video. No media segments are requested here.
"""

from http.client import HTTPException
import math
import queue
import re
import threading
import time
from urllib import error, parse, request


MAX_DOWNLOAD = 1024 * 1024
IDLE_TIMEOUT = 3.0
DEADLINE = 6.0
_ATTR = re.compile(r'([A-Z0-9-]+)=(?:"([^"]*)"|([^,]*))(?:,|$)')


def quality_warning(width, height, max_height=480):
    """UI-ready text, never a playback error; dimensions are source dimensions."""
    if (height and height > max_height) or (width and width > 640):
        size = ("{}x{}".format(width, height) if width and height else
                "{}p".format(height) if height else "{}px wide".format(width))
        return "HD SOURCE | {} - lowest available; playback may be slower".format(size)
    return ""


def _number(value):
    try:
        return max(0, int(value)) or None
    except (TypeError, ValueError, OverflowError):
        return None


def _fps(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and 0 < value <= 240 else None
    except (TypeError, ValueError):
        return None


def _rank(row, max_height=480):
    height = row.get("height")
    tier = 1 if not height else 0 if height <= max_height else 2
    return (tier, -height if tier == 0 else height or 0,
            row.get("fps") or 0,
            row.get("bandwidth") or float("inf"),
            0 if "avc" in row.get("codecs", "").lower() else 1)


def _variants(text, base, headers):
    rows, external, pending = [], set(), None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(("#EXT-X-MEDIA:", "#EXT-X-STREAM-INF:")):
            attrs = {m[1]: m[2] if m[2] is not None else m[3]
                     for m in _ATTR.finditer(line.split(":", 1)[1])}
            if line.startswith("#EXT-X-MEDIA:"):
                if attrs.get("TYPE") == "AUDIO" and attrs.get("URI"):
                    external.add(attrs.get("GROUP-ID"))
            else:
                pending = attrs
                stream_info = line
        elif line and not line.startswith("#") and pending is not None:
            url = parse.urljoin(base, line)
            resolution = re.fullmatch(r"(\d+)[xX](\d+)", pending.get("RESOLUTION", ""))
            codecs = pending.get("CODECS", "")
            audio_only = (not resolution and codecs and all(
                codec.strip().lower().startswith(("mp4a", "ac-3", "ec-3", "opus"))
                for codec in codecs.split(",")))
            if parse.urlsplit(url).scheme in ("http", "https") and not audio_only:
                rows.append(dict(headers, url=url,
                                 width=_number(resolution[1]) if resolution else None,
                                 height=_number(resolution[2]) if resolution else None,
                                 fps=_fps(pending.get("FRAME-RATE")),
                                 bandwidth=_number(pending.get("BANDWIDTH")),
                                 codecs=codecs, audio_group=pending.get("AUDIO"),
                                 stream_info=stream_info))
            pending = None
    external_audio = any(row["audio_group"] in external for row in rows)
    if external_audio:
        # Pin a video rendition without losing its separate audio/subtitles.
        # Child paths must be absolute in the private master opened by Player.
        preamble = ["#EXTM3U"]
        for line in text.splitlines():
            if line.startswith(("#EXT-X-MEDIA:", "#EXT-X-SESSION-", "#EXT-X-VERSION:",
                                "#EXT-X-INDEPENDENT-SEGMENTS")):
                def absolute(match):
                    url = parse.urljoin(base, match[1])
                    if parse.urlsplit(url).scheme not in ("http", "https"):
                        raise ValueError("Unsupported rendition URI")
                    return 'URI="' + url + '"'
                preamble.append(re.sub(r'URI="([^"]*)"', absolute, line))
        for row in rows:
            row["playlist"] = "\n".join(preamble + [row["stream_info"], row["url"], ""])
    for row in rows:
        row.pop("stream_info", None)
    return rows, external_audio


def _resolve(station, cancel, deadline, publish, max_height=480):
    remaining, requests = MAX_DOWNLOAD, 0
    discovered = []

    def check():
        if cancel.is_set() or time.monotonic() >= deadline:
            raise TimeoutError("Quality inspection cancelled or timed out")

    class Redirects(request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            # urllib normally drains the entire redirect body without a limit.
            # Follow redirects ourselves, closing their bodies unread instead.
            return None

    opener = request.build_opener(Redirects())

    def probe(row):
        nonlocal remaining, requests
        headers = {"User-Agent": row.get("user_agent") or "R36S-TV/1.0",
                   "Accept-Encoding": "identity"}
        if row.get("referrer"):
            headers["Referer"] = row["referrer"]
        if any(not isinstance(v, str) or any(c in v for c in "\r\n\0")
               for v in headers.values()):
            raise ValueError("Invalid stream headers")
        url = row["url"]
        for _ in range(5):
            check()
            if requests >= 16 or remaining <= 0:
                raise ValueError("Playlist inspection budget exhausted")
            if parse.urlsplit(url).scheme not in ("http", "https"):
                raise ValueError("Unsupported playlist redirect")
            requests += 1
            req = request.Request(url, headers=headers)
            try:
                response = opener.open(req, timeout=min(IDLE_TIMEOUT, deadline - time.monotonic()))
                break
            except error.HTTPError as exc:
                exc.close()
                if exc.code not in (301, 302, 303, 307, 308):
                    raise
                location = exc.headers.get("Location")
                if not location:
                    raise ValueError("Redirect without Location") from exc
                url = parse.urljoin(url, location)
        else:
            raise ValueError("Too many playlist redirects")
        with response:
            check()
            length = _number(response.headers.get("Content-Length"))
            kind = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("Compressed playlist inspection is unsupported")
            if kind in ("text/html", "application/json"):
                raise ValueError("Not a stream or playlist")
            # Reject known media immediately; sniff small, mislabeled responses
            # before committing the remaining budget to playlist text.
            if kind.startswith(("video/", "audio/", "image/")) and "mpegurl" not in kind:
                return response.geturl(), None
            if length and length > remaining:
                raise ValueError("Playlist is too large")
            body = bytearray()
            while True:
                check()
                chunk = response.read1(min(512 if len(body) < 16 else 16384, remaining))
                check()
                if not chunk:
                    break
                remaining -= len(chunk)
                body.extend(chunk)
                prefix = bytes(body).lstrip(b"\xef\xbb\xbf \r\n\t")
                if len(prefix) >= 7 and not prefix.startswith(b"#EXTM3U"):
                    return response.geturl(), None
                if remaining <= 0:
                    raise ValueError("Playlist download budget exhausted")
            text = body.decode("utf-8-sig")
            if not text.lstrip().startswith("#EXTM3U"):
                raise ValueError("Empty or invalid playlist")
            return response.geturl(), text

    failures = (OSError, ValueError, error.URLError, HTTPException)

    def inspect(row, depth=0, seen=()):
        if depth > 3 or row["url"] in seen:
            return None
        try:
            url, text = probe(row)
            selected = dict(row, url=url)
            if text is None:
                playlist = parse.urlsplit(row["url"]).path.lower().endswith((".m3u8", ".m3u"))
                return selected if depth == 0 and not playlist else None
            variants, external_audio = _variants(text, url, {
                key: row.get(key, "") for key in ("user_agent", "referrer")})
            if not variants:
                return selected if "#EXTINF:" in text or "#EXT-X-TARGETDURATION:" in text else None
            discovered.extend(variants)
            for variant in sorted(variants, key=lambda value: _rank(value, max_height))[:4]:
                child = inspect(variant, depth + 1, seen + (row["url"],))
                if child is not None:
                    if external_audio:
                        selected.update(width=child.get("width"), height=child.get("height"), fps=child.get("fps"),
                                        selected_variant_url=child["url"], playlist=variant["playlist"])
                        return selected
                    return child
            if external_audio:
                return dict(selected, width=None, height=None)
        except failures:
            pass
        return None

    original = {key: station.get(key, "") for key in ("url", "user_agent", "referrer")}
    original.update(height=_number(station.get("height")), width=_number(station.get("width")))
    candidates = [original]
    for alternative in station.get("alternatives", []):
        if isinstance(alternative, dict) and isinstance(alternative.get("url"), str):
            row = dict(original, **alternative)
            row["height"], row["width"] = _number(row.get("height")), _number(row.get("width"))
            # Dimensions of another source are not hints for this alternative.
            if "height" not in alternative:
                row["height"] = None
            if "width" not in alternative:
                row["width"] = None
            candidates.append(row)
    candidates.sort(key=lambda row: _rank(row, max_height))

    # This is an official, verified sibling, not a generic bitrate URL guess.
    parts = parse.urlsplit(original["url"])
    official = (parts.scheme == "https" and parts.netloc == "live.france24.com"
                and re.fullmatch(r"/.+/master_5000\.m3u8", parts.path))
    if official:
        low = dict(original, url=parse.urlunsplit(parts._replace(
            path=parts.path[:-len("master_5000.m3u8")] + "master_500.m3u8")),
                   height=None, width=None)
        selected = inspect(low)
        if selected is not None:
            return selected, [low] + candidates

    choices, tried = [], set()
    for row in candidates:
        if len(tried) >= 4 or cancel.is_set() or time.monotonic() >= deadline:
            break
        key = (row["url"], row.get("user_agent"), row.get("referrer"))
        if key in tried or parse.urlsplit(row["url"]).scheme not in ("http", "https"):
            continue
        tried.add(key)
        selected = inspect(row)
        if selected is not None:
            choices.append(selected)
            publish((min(choices, key=lambda row: _rank(row, max_height)), candidates + discovered))
    return (min(choices, key=lambda row: _rank(row, max_height)) if choices else None), candidates + discovered


def resolve_station(station, cancel=None, max_height=480):
    """Return a new station with selected URL, source headers, and quality metadata.

    Inspects at most four supplied sources plus the verified France24 sibling,
    four variants per master, 1 MiB total and six seconds wall time (three
    seconds idle per network operation).
    ``cancel`` is a threading.Event; cancellation returns the untouched source
    with unknown quality. The caller owns generation checks and Player calls.
    Prefer the highest available resolution at or below max_height. If no source
    fits, use the smallest known higher-resolution source. Pin external-audio
    masters to the selected video rendition without losing audio groups.
    """
    if not isinstance(station, dict) or not isinstance(station.get("url"), str):
        raise ValueError("station must contain a 'url' string")
    if type(max_height) is not int or max_height < 1:
        raise ValueError("max_height must be a positive integer")
    source = dict(station)
    source["alternatives"] = [dict(row) for row in station.get("alternatives", [])
                              if isinstance(row, dict)]
    result = dict(source, source_url=station["url"],
                  source_user_agent=station.get("user_agent", ""),
                  source_referrer=station.get("referrer", ""),
                  selected_url=station["url"], quality_height=None,
                  quality_width=None, quality_fps=None, quality_warning="", renditions=[])
    result.pop("selected_variant_url", None)
    if parse.urlsplit(station["url"]).scheme not in ("http", "https"):
        return result
    cancel = cancel if cancel is not None else threading.Event()
    if cancel.is_set():
        return result
    fallback = dict(result)
    deadline = time.monotonic() + DEADLINE
    done, stop = queue.Queue(maxsize=8), threading.Event()

    def work():
        try:
            selected, alternatives = _resolve(
                source, stop, deadline, lambda update: done.put((*update, False)), max_height)
            done.put((selected, alternatives, True))
        except (OSError, ValueError, HTTPException):
            done.put((None, source["alternatives"], True))

    # Socket timeouts cannot bound DNS or a server trickling HTTP headers.
    # Isolate I/O so neither those nor cancellation can hold the UI worker open.
    threading.Thread(target=work, daemon=True).start()
    try:
        while not cancel.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return result
            try:
                selected, alternatives, finished = done.get(timeout=min(.05, remaining))
            except queue.Empty:
                continue
            if cancel.is_set():
                return fallback
            if selected is not None:
                result.pop("selected_variant_url", None)
                result.pop("playlist", None)
                result.update({key: value for key, value in selected.items()
                               if key in ("url", "user_agent", "referrer", "selected_variant_url", "playlist")})
                result.update(selected_url=selected["url"], quality_height=selected.get("height"),
                              quality_width=selected.get("width"), quality_fps=selected.get("fps"))
                result["quality_warning"] = quality_warning(
                    result["quality_width"], result["quality_height"], max_height)
                result["alternatives"] = [dict(row) for row in alternatives]
                options = {}
                for row in alternatives:
                    if row.get("height") or row.get("fps"):
                        key = (row["url"], row.get("user_agent", ""), row.get("referrer", ""))
                        options[key] = dict(row)
                result["renditions"] = sorted(options.values(), key=lambda row: (
                    row.get("height") or float("inf"), row.get("fps") or 0, row.get("bandwidth") or 0))
            if finished:
                return result
        return fallback
    finally:
        stop.set()

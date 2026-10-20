"""Standard-library catalog, persistence, health checks and globe geometry.

Network methods are synchronous: callers should run refresh/scan off the UI thread.
Favorites are a UUID-keyed mapping of complete station snapshots; recent is a
most-recent-first list of snapshots. Invalid persistent state raises ValueError
rather than replacing the user's data with defaults.
"""

import copy
import hashlib
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import random
import re
import queue
import tempfile
import threading
import time
from urllib import error, parse, request


CHANNELS_URL = "https://iptv-org.github.io/api/channels.json"
STREAMS_URL = "https://iptv-org.github.io/api/streams.json"
FEEDS_URL = "https://iptv-org.github.io/api/feeds.json"
M3U_URL = "https://iptv-org.github.io/iptv/index.m3u"
COUNTRY_URL = "https://iptv-org.github.io/iptv/countries/{}.m3u"
MAX_DOWNLOAD = 32 * 1024 * 1024
RECENT_LIMIT = 50
GLOBE_RESOLUTIONS = (128, 160, 192, 224, 256, 320, 384, 448, 512)
GLOBE_ROTATION_SPEEDS = (0, 0.5, 1, 2, 4, 8)
STATION_FIELDS = ("uuid", "name", "url", "countryCode", "country", "language",
                  "tags", "user_agent", "referrer", "favicon", "alternatives")
LANGUAGE_NAMES = dict(eng="English", deu="German", ger="German", fra="French",
                      fre="French", spa="Spanish", por="Portuguese", ara="Arabic",
                      rus="Russian", ukr="Ukrainian", ita="Italian", tur="Turkish",
                      zho="Chinese", chi="Chinese", jpn="Japanese", kor="Korean",
                      hin="Hindi", ben="Bengali", urd="Urdu", fas="Persian",
                      per="Persian", nld="Dutch", dut="Dutch", pol="Polish",
                      ell="Greek", gre="Greek", heb="Hebrew", vie="Vietnamese",
                      tha="Thai", ind="Indonesian", swe="Swedish", nor="Norwegian",
                      dan="Danish", fin="Finnish", ces="Czech", cze="Czech",
                      ron="Romanian", rum="Romanian", hun="Hungarian")


def _read_json(path, default):
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return default
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid JSON in %s" % path) from exc


def _atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=path.parent, prefix=path.name + ".",
                                         suffix=".tmp", delete=False) as stream:
            name = stream.name
            json.dump(data, stream, ensure_ascii=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name is not None:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass


class _HTTPSRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if parse.urlsplit(newurl).scheme != "https":
            raise ValueError("Catalog redirect must use HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url, timeout=10, maximum=MAX_DOWNLOAD):
    if parse.urlsplit(url).scheme != "https":
        raise ValueError("Catalog downloads must use HTTPS")
    req = request.Request(url, headers={"User-Agent": "R36S-TV/1.0",
                                        "Accept-Encoding": "identity"})
    deadline = time.monotonic() + timeout
    with request.build_opener(_HTTPSRedirect()).open(req, timeout=timeout) as response:
        length = response.headers.get("Content-Length")
        if length and int(length) > maximum:
            raise ValueError("Catalog download is too large")
        chunks, size = [], 0
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError("Catalog download timed out")
            # read1 makes one socket read, rather than waiting for a full buffer
            # indefinitely while a slow server drips bytes below its idle timeout.
            chunk = response.read1(min(65536, maximum + 1 - size))
            if not chunk:
                return b"".join(chunks)
            size += len(chunk)
            if size > maximum:
                raise ValueError("Catalog download is too large")
            chunks.append(chunk)


def _names(path):
    raw = _read_json(path, {})
    if isinstance(raw, dict):
        return {str(code).upper(): (value.get("name", code)
                                   if isinstance(value, dict) else str(value))
                for code, value in raw.items()}
    if isinstance(raw, list):
        return {str(row.get("code") or row.get("iso_3166_1") or row.get("id", "")).upper():
                row["name"] for row in raw if isinstance(row, dict) and "name" in row}
    raise ValueError("Expected country/language name mapping in %s" % path)


def _station_valid(station):
    return (isinstance(station, dict) and isinstance(station.get("uuid"), str)
            and bool(station["uuid"]) and isinstance(station.get("name"), str)
            and isinstance(station.get("url"), str))


class Catalog:
    def __init__(self, data_dir: Path, assets_dir: Path):
        self.path = Path(data_dir) / "catalog.json"
        self.countries = _names(Path(assets_dir) / "countries.json")
        self.languages = {code.upper(): name for code, name in LANGUAGE_NAMES.items()}
        self.languages.update(_names(Path(assets_dir) / "languages.json"))

    def load_cached(self):
        rows = _read_json(self.path, [])
        if not isinstance(rows, list) or any(not _station_valid(row) for row in rows):
            raise ValueError("Invalid catalog in %s" % self.path)
        return list({row["uuid"]: row for row in rows}.values())

    def _country_path(self, code):
        code = str(code).upper()
        if not re.fullmatch(r"[A-Z]{2}", code) or code not in self.countries:
            raise ValueError("Unknown country: " + code)
        return self.path.parent / "countries" / (code + ".json")

    def country(self, code, refresh=False):
        """Fetch/cache one country playlist, without reading the world catalog."""
        code = str(code).upper()
        path = self._country_path(code)
        cached = _read_json(path, [])
        if not isinstance(cached, list) or any(
                not _station_valid(row) or row.get("countryCode") != code for row in cached):
            raise ValueError("Invalid country cache: " + str(path))
        if not refresh and path.exists() and time.time()-path.stat().st_mtime < 86400:
            return cached
        try:
            streams = self._m3u(_download(COUNTRY_URL.format(code.lower()), maximum=8*1024*1024).decode("utf-8-sig"))
        except (OSError, ValueError, error.URLError, HTTPException) as exc:
            if isinstance(exc, error.HTTPError):
                exc.close()
            if path.exists():
                return cached
            if isinstance(exc, error.HTTPError) and exc.code == 404:
                _atomic_json(path, [])
                return []
            raise
        feeds = {}
        for row in streams:
            feeds.setdefault(row.get("channel"), set()).add(row.get("feed"))
        stations = {}
        for stream in streams:
            url = stream.get("url", "")
            if parse.urlsplit(url).scheme not in ("http", "https"):
                continue
            channel, feed = stream.get("channel"), stream.get("feed")
            primary = "SD" if "SD" in feeds.get(channel, ()) else "HD"
            key = (channel + "::" + feed if channel and feed and feed != primary else channel)
            key = key or hashlib.sha256(url.encode()).hexdigest()
            source = {name: stream.get(name) or "" for name in ("url", "user_agent", "referrer")}
            if stream.get("height"):
                source["height"] = stream["height"]
            if key in stations:
                row = stations[key]
                if url != row["url"] and not any(a["url"] == url for a in row["alternatives"]) and len(row["alternatives"]) < 8:
                    row["alternatives"].append(source)
                continue
            languages = re.split(r"[;,]", stream.get("language") or "")
            row = dict(uuid=key, name=stream["name"] or key, url=url, countryCode=code,
                       country=self.countries[code], language=", ".join(
                           self.languages.get(lang.strip().upper(), lang.strip()) for lang in languages if lang.strip()),
                       tags=(stream.get("tags") or "").replace(";", ", ").lower(),
                       user_agent=source["user_agent"], referrer=source["referrer"],
                       favicon=stream.get("favicon") or "", alternatives=[])
            if stream.get("height"):
                row["height"] = stream["height"]
            stations[key] = row
        result = sorted(stations.values(), key=lambda row: (row["name"].casefold(), row["uuid"]))
        _atomic_json(path, result)
        return result

    def refresh(self):
        channels = json.loads(_download(CHANNELS_URL))
        if not isinstance(channels, list):
            raise ValueError("Expected channels array")
        by_id = {row["id"]: row for row in channels
                 if isinstance(row, dict) and isinstance(row.get("id"), str)}
        try:
            streams = json.loads(_download(STREAMS_URL))
            if not isinstance(streams, list):
                raise ValueError("Expected streams array")
        except (OSError, ValueError, error.URLError, HTTPException):
            streams = self._m3u(_download(M3U_URL).decode("utf-8-sig"))
        feeds = {}
        if any(isinstance(row, dict) and row.get("feed") for row in streams):
            try:
                raw_feeds = json.loads(_download(FEEDS_URL))
                if isinstance(raw_feeds, list):
                    feeds = {(row.get("channel"), row.get("id")): row for row in raw_feeds
                             if isinstance(row, dict)}
            except (OSError, ValueError, error.URLError, HTTPException):
                pass
        stations = {}
        for stream in streams:
            if not isinstance(stream, dict):
                continue
            url = stream.get("url")
            if not isinstance(url, str) or parse.urlsplit(url).scheme not in ("http", "https"):
                continue
            channel_id = stream.get("channel")
            channel = by_id.get(channel_id, {})
            feed_id = stream.get("feed")
            feed = feeds.get((channel_id, feed_id), {})
            key = (channel_id + "::" + feed_id if channel_id and feed_id and not feed.get("is_main")
                   else channel_id) or hashlib.sha256(url.encode()).hexdigest()
            if key in stations:
                alternative = {"url": url, "user_agent": stream.get("user_agent") or "",
                               "referrer": stream.get("referrer") or ""}
                quality = re.search(r"\b(\d{3,4})[pi]\b", stream.get("title") or "", re.I)
                if quality:
                    alternative["height"] = int(quality[1])
                row = stations[key]
                if url != row["url"] and not any(a["url"] == url for a in row["alternatives"]) and len(row["alternatives"]) < 8:
                    row["alternatives"].append(alternative)
                continue
            code = str(channel.get("country") or stream.get("country") or "").upper()
            languages = feed.get("languages") or channel.get("languages") or []
            if isinstance(languages, str):
                languages = [languages]
            name = channel.get("name") or stream.get("name") or key
            if feed_id and not feed.get("is_main"):
                name += " (" + (feed.get("name") or feed_id) + ")"
            stations[key] = dict(
                uuid=key, name=name,
                url=url, countryCode=code, country=self.countries.get(code, code),
                language=", ".join(self.languages.get(str(lang).upper(), str(lang))
                                    for lang in languages),
                tags=", ".join(channel.get("categories") or []),
                user_agent=stream.get("user_agent") or "",
                referrer=stream.get("referrer") or "",
                favicon=channel.get("logo") or "", alternatives=[])
        result = sorted(stations.values(), key=lambda row: (row["name"].casefold(), row["uuid"]))
        if not result:
            raise ValueError("Catalog contains no playable stations; keeping cache")
        _atomic_json(self.path, result)
        return result

    @staticmethod
    def _m3u(text):
        if not text.lstrip().startswith("#EXTM3U"):
            raise ValueError("Invalid M3U catalog")
        rows, pending = [], None
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#EXTINF:"):
                attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', line))
                channel, _, feed = attrs.get("tvg-id", "").partition("@")
                name = line.rsplit(",", 1)[-1].strip()
                resolution = re.search(r"\((\d{3,4})[pi]\)", name, re.I)
                pending = {"channel": channel or None, "feed": feed or None,
                           "name": re.sub(r"\s*\(\d{3,4}[pi]\)|\s*\[[^]]*\]", "", name, flags=re.I).strip(),
                           "favicon": attrs.get("tvg-logo", ""), "tags": attrs.get("group-title", ""),
                           "language": attrs.get("tvg-language", ""), "title": name}
                if resolution:
                    pending["height"] = int(resolution[1])
            elif pending is not None and line.startswith("#EXTVLCOPT:"):
                key, _, value = line[len("#EXTVLCOPT:"):].partition("=")
                if key in ("http-user-agent", "http-referrer"):
                    pending[{"http-user-agent": "user_agent", "http-referrer": "referrer"}[key]] = value
            elif line and not line.startswith("#") and pending is not None:
                rows.append(dict(pending, url=line))
                pending = None
        return rows


class State:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "state.json"
        self.data = _read_json(self.path, {})
        if not isinstance(self.data, dict):
            raise ValueError("State must be an object")
        defaults = dict(favorites={}, recent=[], volume=70, crt=False,
                        intense=False, animated=False, globe_resolution=224, globe_rotation_speed=1)
        for key, value in defaults.items():
            self.data.setdefault(key, value)
        self._validate()

    def _validate(self):
        data = self.data
        if (not isinstance(data.get("favorites"), dict)
                or any(not _station_valid(row) or key != row["uuid"]
                       for key, row in data["favorites"].items())
                or not isinstance(data.get("recent"), list)
                or any(not _station_valid(row) for row in data["recent"])
                or type(data.get("volume")) not in (int, float)
                or not 0 <= data["volume"] <= 100
                or type(data.get("globe_resolution")) is not int
                or data["globe_resolution"] not in GLOBE_RESOLUTIONS
                or type(data.get("globe_rotation_speed")) not in (int, float)
                or data["globe_rotation_speed"] not in GLOBE_ROTATION_SPEEDS
                or any(type(data.get(key)) is not bool for key in ("crt", "intense", "animated"))):
            raise ValueError("Invalid state in %s" % self.path)

    def save(self):
        self._validate()
        _atomic_json(self.path, self.data)

    def favorite(self, station):
        if not _station_valid(station):
            raise ValueError("Invalid station")
        previous = copy.deepcopy(self.data)
        favorites = self.data["favorites"]
        key = station["uuid"]
        enabled = key not in favorites
        if enabled:
            favorites[key] = dict(station)
        else:
            del favorites[key]
        try:
            self.save()
        except (OSError, ValueError):
            self.data = previous
            raise
        return enabled

    def played(self, station):
        if not _station_valid(station):
            raise ValueError("Invalid station")
        previous = self.data["recent"]
        self.data["recent"] = ([dict(station)] + [row for row in self.data["recent"]
                                if row["uuid"] != station["uuid"]])[:RECENT_LIMIT]
        try:
            self.save()
        except (OSError, ValueError):
            self.data["recent"] = previous
            raise


def filter_stations(stations, query="", country="", tab="World", state=None, health=None):
    """Filter snapshots; favorites/history remain available after catalog removals."""
    state = state.data if isinstance(state, State) else (state or {})
    health = health.data if isinstance(health, Health) else (health or {})
    current = {row["uuid"]: row for row in stations}
    if tab == "Favorites":
        source = [current.get(key, row) for key, row in state.get("favorites", {}).items()]
    elif tab == "Recent":
        source = [current.get(row["uuid"], row) for row in state.get("recent", [])]
    else:
        source = list(current.values())
    words = query.casefold().split()
    result, seen = [], set()
    for row in source:
        key = row["uuid"]
        dead = health.get(key, {}).get("status") == "dead"
        if key in seen or (tab == "Archive" and not dead) or (tab == "World" and dead):
            continue
        if country and country.casefold() not in (row.get("countryCode", "").casefold(),
                                                  row.get("country", "").casefold()):
            continue
        text = " ".join(str(row.get(field, "")) for field in
                        ("name", "countryCode", "country", "language", "tags")).casefold()
        if all(word in text for word in words):
            result.append(row)
            seen.add(key)
    return result


class Health:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "health.json"
        self.data = _read_json(self.path, {})
        if not isinstance(self.data, dict) or any(
                not isinstance(key, str) or not isinstance(row, dict)
                or row.get("status") not in ("alive", "dead")
                or type(row.get("checked")) not in (int, float)
                or not math.isfinite(row["checked"])
                or (row.get("deadSince") is not None and
                    (type(row["deadSince"]) not in (int, float)
                     or not math.isfinite(row["deadSince"])))
                or ("resurrected" in row and type(row["resurrected"]) is not bool)
                for key, row in self.data.items()):
            raise ValueError("Invalid health state in %s" % self.path)

    @staticmethod
    def _probe(station):
        try:
            url = station["url"]
            if parse.urlsplit(url).scheme not in ("http", "https"):
                return False, "Unsupported stream URL"
            headers = {"Range": "bytes=0-1023", "User-Agent": station.get("user_agent") or "R36S-TV/1.0"}
            if station.get("referrer"):
                headers["Referer"] = station["referrer"]
            with request.urlopen(request.Request(url, headers=headers), timeout=5) as response:
                if not 200 <= response.status < 300:
                    return False, "HTTP %s" % response.status
                if not response.read1(1024):
                    return False, "Empty response"
                return True, ""
        except (OSError, ValueError, error.URLError, HTTPException) as exc:
            return False, str(exc)[:240]

    def scan(self, stations, limit=12, cancel=None):
        """Check at most limit unique stations, oldest checks first, with four workers."""
        unique = {row["uuid"]: row for row in stations}
        selected = sorted(unique.values(), key=lambda row:
                          self.data.get(row["uuid"], {}).get("checked", 0))[:max(0, limit)]
        cancel = cancel or threading.Event()
        pending, results = queue.Queue(), queue.Queue()
        for station in selected:
            pending.put(station)

        def probe():
            while not cancel.is_set():
                try:
                    station = pending.get_nowait()
                except queue.Empty:
                    return
                try:
                    results.put((station, self._probe(station)))
                except Exception as exc:
                    results.put((station, (False, str(exc)[:240])))

        # Unlike ThreadPoolExecutor, these bounded network workers do not hold
        # interpreter shutdown open when the handheld returns to its launcher.
        for _ in range(min(4, len(selected))):
            threading.Thread(target=probe, daemon=True).start()
        updates = {}
        while len(updates) < len(selected) and not cancel.is_set():
            try:
                station, (alive, reason) = results.get(timeout=.1)
            except queue.Empty:
                continue
            now = time.time()
            old = self.data.get(station["uuid"], {})
            row = dict(old, name=station["name"], url=station["url"],
                       status="alive" if alive else "dead", checked=now,
                       deadSince=None if alive else (old.get("deadSince") or now),
                       resurrected=bool(old.get("resurrected")), error=reason)
            if alive and old.get("status") == "dead":
                row.update(resurrected=True, resurrectedAt=now)
            updates[station["uuid"]] = row
        if cancel.is_set():
            return self.data
        if updates:
            data = dict(self.data, **updates)
            _atomic_json(self.path, data)
            self.data = data
        return self.data


def project(lat, lon, center_lat, center_lon):
    """Orthographic unit globe: x right, y up, z positive on the visible side."""
    lat, delta, center = map(math.radians, (lat, lon - center_lon, center_lat))
    return (math.cos(lat) * math.sin(delta),
            math.cos(center) * math.sin(lat) - math.sin(center) * math.cos(lat) * math.cos(delta),
            math.sin(center) * math.sin(lat) + math.cos(center) * math.cos(lat) * math.cos(delta))


def unproject(x, y, clat, clon):
    radius2 = x*x + y*y
    if not math.isfinite(radius2) or radius2 > 1 + 1e-12:
        return None
    z = math.sqrt(max(0, 1 - radius2))
    center = math.radians(clat)
    lat = math.asin(max(-1, min(1, y * math.cos(center) + z * math.sin(center))))
    lon = math.radians(clon) + math.atan2(x, z * math.cos(center) - y * math.sin(center))
    return math.degrees(lat), (math.degrees(lon) + 180) % 360 - 180


def _polygons(feature):
    geometry = feature.get("geometry") or {}
    if geometry.get("type") == "Polygon":
        return [geometry["coordinates"]]
    if geometry.get("type") == "MultiPolygon":
        return geometry["coordinates"]
    return []


def _unwrap(ring, anchor=None):
    result = []
    for lon, lat, *unused in ring:
        if result:
            lon += 360 * round((result[-1][0] - lon) / 360)
        elif anchor is not None:
            lon += 360 * round((anchor - lon) / 360)
        result.append((lon, lat))
    return result


def _in_ring(ring, lat, lon):
    points = _unwrap(ring)
    if not points:
        return False
    lon += 360 * round((points[0][0] - lon) / 360)
    inside = False
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        cross = (lon - x1) * (y2 - y1) - (lat - y1) * (x2 - x1)
        if abs(cross) < 1e-10 and min(x1, x2) - 1e-10 <= lon <= max(x1, x2) + 1e-10 and min(y1, y2) - 1e-10 <= lat <= max(y1, y2) + 1e-10:
            return True
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat-y1) * (x2-x1) / (y2-y1):
            inside = not inside
    return inside


def _in_polygon(polygon, lat, lon):
    return bool(polygon) and _in_ring(polygon[0], lat, lon) and not any(
        _in_ring(hole, lat, lon) for hole in polygon[1:])


def country_at(features, lat, lon):
    if isinstance(features, dict):
        features = features.get("features", [features])
    for feature in features:
        if any(_in_polygon(polygon, lat, lon) for polygon in _polygons(feature)):
            return feature.get("properties", {})
    return None


def _area(polygon):
    points = _unwrap(polygon[0]) if polygon else []
    return abs(sum(x1*y2 - x2*y1 for (x1, y1), (x2, y2)
                   in zip(points, points[1:] + points[:1]))) / 2


def station_location(feature, uuid):
    """Stable location within the largest polygon, excluding lakes and enclaves."""
    polygons = [polygon for polygon in _polygons(feature) if polygon and polygon[0]]
    if not polygons:
        raise ValueError("Country has no polygon")
    polygon = max(polygons, key=_area)
    ring = _unwrap(polygon[0])
    xmin, xmax = min(p[0] for p in ring), max(p[0] for p in ring)
    ymin, ymax = min(p[1] for p in ring), max(p[1] for p in ring)
    rng = random.Random(int(hashlib.sha256(str(uuid).encode()).hexdigest(), 16))
    for _ in range(2048):
        lon, lat = rng.uniform(xmin, xmax), rng.uniform(ymin, ymax)
        if _in_polygon(polygon, lat, lon):
            return lat, (lon + 180) % 360 - 180
    # Scanline intervals find an interior point even in very thin polygons.
    levels = sorted(set(point[1] for point in ring))
    for low, high in zip(levels, levels[1:]):
        lat = (low + high) / 2
        crossings = []
        for boundary in polygon:
            points = _unwrap(boundary, ring[0][0])
            for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
                if (y1 > lat) != (y2 > lat):
                    crossings.append(x1 + (lat-y1) * (x2-x1) / (y2-y1))
        crossings.sort()
        for left, right in zip(crossings[::2], crossings[1::2]):
            lon = (left + right) / 2
            if right > left and _in_polygon(polygon, lat, lon):
                return lat, (lon + 180) % 360 - 180
    raise ValueError("Country polygon has no interior")


def country_center(feature):
    polygons = [polygon for polygon in _polygons(feature) if polygon and polygon[0]]
    if not polygons:
        raise ValueError("Country has no polygon")
    polygon = max(polygons, key=_area)
    points = _unwrap(polygon[0])
    area, cx, cy = 0, 0, 0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        cross = x1*y2 - x2*y1
        area += cross
        cx += (x1+x2) * cross
        cy += (y1+y2) * cross
    if abs(area) > 1e-12:
        lon, lat = cx / (3*area), cy / (3*area)
        if _in_polygon(polygon, lat, lon):
            return lat, (lon + 180) % 360 - 180
    # Concave countries and countries with holes can have offshore centroids.
    return station_location(feature, "country-center")

"""Fast SDL2 globe mesh with cached vector close-ups and native-pixel outlines."""

import ctypes as C
import json
import math
from pathlib import Path
import struct
import time
import zlib

from tv_backend import GLOBE_RESOLUTIONS, country_center, project, _polygons, _area
from tv_filter import Scaler

FADE_SECONDS = .20
TRACE_COLORS = ((120, 84, 18), (160, 116, 26), (203, 152, 34), (242, 201, 76))


class Rect(C.Structure):
    _fields_ = [(name, C.c_int) for name in ("x", "y", "w", "h")]


class Point(C.Structure):
    _fields_ = [(name, C.c_int) for name in ("x", "y")]


class Color(C.Structure):
    _fields_ = [(name, C.c_uint8) for name in ("r", "g", "b", "a")]


def load_atlas(path):
    raw = zlib.decompress(Path(path).read_bytes())
    magic, width, height, length = struct.unpack_from(">4sHHH", raw)
    if magic != b"GAT1" or not (4 <= width <= 4096 and 2 <= height <= 2048):
        raise ValueError("Invalid globe atlas header")
    offset = 10 + length
    codes = json.loads(raw[10:offset])
    ids, styles = raw[offset:offset+width*height], raw[offset+width*height:]
    if (len(ids) != width*height or len(styles) != len(ids) or not codes
            or len(codes) > 256 or max(ids) >= len(codes) or max(styles) > 4):
        raise ValueError("Invalid globe atlas planes")
    return width, height, codes, ids, styles


class Globe:
    """Front hemisphere mesh, duplicated longitude texture, and a 350px cache."""

    def __init__(self, display, features):
        self.d, self.features = display, features
        self.lat, self.lon, self.zoom = 18.0, -20.0, .8
        self.resolution, self.motion_size = 224, 160
        self.key = self.cached = self.atlas_texture = None
        self.motion_texture = None
        self.moving_until = 0.
        self.flight = None
        self.spinning = False
        self.scaler = Scaler()
        self.source_pixels, self.source_size = None, 0
        self.surfaces, self.surface_size = [], None
        self.surface_index, self.surface_mode, self.surface_zoom = 0, None, self.zoom
        self.fade_from, self.fade_started, self.fade_until, self.fade_zoom = None, 0., 0., self.zoom
        self.selection_key, self.selection_lines = None, []
        self.trace_segments, self.trace_length, self.trace_active = [], 0., False
        self._country = None
        self.atlas_pixels = None
        self.vector_geometry = []
        self.world_borders_cached = False
        self.world_line_key, self.world_lines = None, []
        self.vector_paths, self.vector_lines = [], []
        self.vector_line_key = self.rim_key = None
        self.rim_points = None
        for feature in features:
            for polygon in _polygons(feature):
                rings = [[project(lat, lon, 0, 0) for lon, lat in ring] for ring in polygon if len(ring) >= 3]
                if not rings:
                    continue
                center = tuple(sum(point[i] for point in rings[0]) for i in range(3))
                length = math.sqrt(sum(value*value for value in center))
                if length < 1e-9:
                    center, angle = (0., 0., 1.), math.pi
                else:
                    center = tuple(value/length for value in center)
                    angle = max(math.acos(max(-1., min(1., sum(a*b for a, b in zip(center, point)))))
                                for point in rings[0])
                self.vector_geometry.append((feature["properties"]["code"], center, angle, rings))
        segments = []
        for _, _, _, rings in self.vector_geometry:
            for ring in rings:
                for first, last in zip(ring, ring[1:]+ring[:1]):
                    if first != last:
                        segments.extend((*first, *last))
        self.border_segments = (C.c_float*len(segments))(*segments)
        sdl = display.sdl
        p, i, f = C.c_void_p, C.c_int, C.POINTER(C.c_float)
        # Use void* for rects to avoid changing Display's ctypes Rect signature.
        signatures = (
            ("SDL_RenderGeometryRaw", [p, p, f, i, C.POINTER(Color), i, f, i, i, p, i, i], i),
            ("SDL_GetRenderTarget", [p], p),
            ("SDL_RenderDrawLines", [p, C.POINTER(Point), i], i),
            ("SDL_RenderFillRects", [p, C.POINTER(Rect), i], i),
            ("SDL_RenderSetClipRect", [p, p], i),
            ("SDL_RenderGetClipRect", [p, p], None),
            ("SDL_RenderIsClipEnabled", [p], i),
            ("SDL_SetTextureScaleMode", [p, i], i),
            ("SDL_SetTextureBlendMode", [p, i], i),
            ("SDL_SetTextureAlphaMod", [p, C.c_uint8], i),
            ("SDL_RenderReadPixels", [p, p, C.c_uint32, p, i], i),
        )
        for name, args, result in signatures:
            fn = getattr(sdl, name)
            fn.argtypes, fn.restype = args, result
        self.width, self.height, self.codes, self.ids, self.styles = load_atlas(
            Path(__file__).resolve().parent / "assets/globe-atlas.z")
        # Borders are overlaid at native screen resolution after smooth scaling,
        # rather than magnifying the atlas's tiny border/grid texels.
        palette = ((5, 19, 12, 255), (13, 62, 34, 255), (13, 62, 34, 255),
                   (5, 19, 12, 255), (13, 62, 34, 255))
        pixels = bytearray(len(self.ids)*4)
        for channel in range(4):
            table = bytes([color[channel] for color in palette] + [0]*251)
            pixels[channel::4] = self.styles.translate(table)
        self._base = bytes(pixels)
        # Equal angular rings give extra screen-space resolution near the limb.
        slices, rings = 32, 8
        self._slices, self._rings = slices, rings
        self._detail = 224/350
        self.normals = [(0., 0., 1.)]
        for ring in range(1, rings+1):
            angle = math.pi/2*ring/rings
            rho, depth = math.sin(angle), math.cos(angle)
            for section in range(slices):
                phi = math.tau*section/slices
                x, y = rho*math.cos(phi), rho*math.sin(phi)
                self.normals.append((0. if abs(x) < 1e-14 else x,
                                     0. if abs(y) < 1e-14 else y, depth))
        self.triangles = []
        for section in range(slices):
            self.triangles.append((0, 1+section, 1+(section+1) % slices))
        for ring in range(rings-1):
            for section in range(slices):
                a, b = 1+ring*slices+section, 1+ring*slices+(section+1) % slices
                c, d = a+slices, b+slices
                self.triangles.extend(((a, c, b), (b, c, d)))
        capacity = len(self.normals)+3*len(self.triangles)+16
        self._xy = (C.c_float * (capacity*2))()
        self._uv = (C.c_float * (capacity*2))()
        self._indices = (C.c_int * (3*(len(self.triangles)+8)))()
        self._base_indices = (C.c_int * (3*len(self.triangles)))(
            *(vertex for triangle in self.triangles for vertex in triangle))
        self._pole_edges = {}
        for section in (slices//4, slices*3//4):
            spoke = [0] + [1+ring*slices+section for ring in range(rings)]
            for a, b in zip(spoke, spoke[1:]):
                self._pole_edges[a, b] = [(index, next(v for v in triangle if v != a and v != b))
                    for index, triangle in enumerate(self.triangles) if a in triangle and b in triangle]
        self._longitudes = [0.] * (len(self.normals)+1)
        self._radius = None
        self._white = Color(255, 255, 255, 255)
        try:
            self.atlas_texture = display.texture(self.width*2, self.height)
            self._check(sdl.SDL_SetTextureScaleMode(self.atlas_texture, 0))
            # Rasterize geography at handheld detail, then scale only the globe.
            # UI text, markers and the pointer remain at native resolution.
            self.cached = display.texture(224, 224, target=True)
            self._check(sdl.SDL_SetTextureScaleMode(self.cached, 0))
            self.motion_texture = display.texture(160, 160, target=True)
            self._check(sdl.SDL_SetTextureScaleMode(self.motion_texture, 0))
        except BaseException:
            self.close()
            raise

    def _check(self, result):
        if result < 0:
            raise RuntimeError(self.d.error())

    def rotate(self, dx, dy):
        self.cancel_flight()
        self.lon = (self.lon + dx/self.zoom + 180) % 360 - 180
        self.lat = max(-78, min(78, self.lat + dy/self.zoom))
        self.moving_until = time.monotonic()+.18

    def cancel_flight(self):
        self.flight = None

    def zoom_by(self, factor, now=None):
        now = time.monotonic() if now is None else now
        target = self.flight["target"][2] if self.flight and self.flight.get("kind") == "zoom" else self.zoom
        self.advance_flight(now)
        self.flight = dict(start=now, duration=.24, kind="zoom", origin=(self.lat, self.lon, self.zoom),
                           target=(self.lat, self.lon, max(.7, min(8., target*factor))),
                           longitude_delta=0., pullback=False)

    def fly_to(self, feature=None, now=None):
        now = time.monotonic() if now is None else now
        self.advance_flight(now)
        if feature is None:
            latitude, longitude, zoom = 18., -20., .8
        else:
            latitude, longitude = country_center(feature)
            latitude = max(-78., min(78., latitude))
            # Frame the largest landmass, not distant overseas islands. Picking
            # still uses exact polygons; this only determines the camera framing.
            polygon = max(_polygons(feature), key=_area)
            extent = max(max(abs(x), abs(y)) for x, y, _ in
                         (project(lat, lon, latitude, longitude) for lon, lat in polygon[0]))
            zoom = max(.8, min(8., .64/max(.01, extent)))
        distance = math.degrees(math.acos(max(-1., min(1., project(
            latitude, longitude, self.lat, self.lon)[2]))))
        self.flight = dict(start=now, duration=1.2+.4*distance/180,
                           origin=(self.lat, self.lon, self.zoom),
                           target=(latitude, longitude, zoom),
                           longitude_delta=(longitude-self.lon+180) % 360-180,
                           pullback=distance > 40 and self.zoom > 1.)

    def advance_flight(self, now=None):
        if self.flight is None:
            return False
        now = time.monotonic() if now is None else now
        flight = self.flight
        t = max(0., min(1., (now-flight["start"])/flight["duration"]))
        if t >= 1.-1e-9:
            self.lat, self.lon, self.zoom = flight["target"]
            self.flight = None
        else:
            def ease(value):
                return value*value*(3.-2.*value)

            latitude, longitude, zoom = flight["origin"]
            target_lat, _, target_zoom = flight["target"]
            progress = ease(t)
            self.lat = latitude+(target_lat-latitude)*progress
            self.lon = (longitude+flight["longitude_delta"]*progress+180) % 360-180
            if flight["pullback"]:
                if t < .28:
                    start_zoom, end_zoom, progress = zoom, .8, ease(t/.28)
                else:
                    start_zoom, end_zoom, progress = .8, target_zoom, ease((t-.28)/.72)
            else:
                start_zoom, end_zoom = zoom, target_zoom
                progress = ease(t) if flight.get("kind") == "zoom" else ease(max(0., (t-.1)/.9))
            self.zoom = math.exp(math.log(start_zoom)+(math.log(end_zoom)-math.log(start_zoom))*progress)
        # Use the existing fast motion renderer throughout the flight, then
        # let the idle timer restore the selected stationary detail afterwards.
        self.moving_until = now+.18
        return True

    def set_resolution(self, resolution):
        if type(resolution) is not int or resolution not in GLOBE_RESOLUTIONS:
            raise ValueError("Unsupported globe resolution: %s" % resolution)
        if resolution == self.resolution:
            return
        textures = []
        # Higher stationary detail must not make dragging increasingly expensive.
        motion = min(224, max(96, (resolution*5//7//16)*16))
        try:
            for size in (resolution, motion):
                texture = self.d.texture(size, size, target=True)
                textures.append(texture)
                self._check(self.d.sdl.SDL_SetTextureScaleMode(texture, 0))
        except BaseException:
            for texture in textures:
                self.d.sdl.SDL_DestroyTexture(texture)
            raise
        self.d.sdl.SDL_DestroyTexture(self.cached)
        self.d.sdl.SDL_DestroyTexture(self.motion_texture)
        self.cached, self.motion_texture = textures
        self.resolution, self.motion_size = resolution, motion
        self.key, self._radius = None, None

    def _colorize(self, country):
        pixels = bytearray(self._base)
        if country and country in self.codes:
            number = self.codes.index(country)
            selected = (b"\x05\x13\x0c\xff", b"\x9e\x78\x20\xff", b"\xf2\xc9\x4c\xff",
                         b"\x05\x13\x0c\xff", b"\x9e\x78\x20\xff")
            # Only country changes touch atlas pixels; rotations never enter here.
            start = self.ids.find(bytes([number]))
            needle = bytes([number])
            while start >= 0:
                pixels[start*4:start*4+4] = selected[self.styles[start]]
                start = self.ids.find(needle, start+1)
        row = self.width*4
        self.atlas_pixels = (C.c_ubyte*len(pixels)).from_buffer_copy(pixels)
        doubled = b"".join(pixels[y:y+row]*2 for y in range(0, len(pixels), row))
        self._check(self.d.sdl.SDL_UpdateTexture(self.atlas_texture, None, doubled, row*2))
        self._country = country

    def _ensure_surfaces(self, width, height):
        if self.surface_size == (width, height):
            return
        textures = []
        try:
            for _ in range(2):
                textures.append(self.d.texture(width, height, target=True))
            # Background pixels carry alpha; blend them over the app backdrop.
            for texture in textures:
                self.d.sdl.SDL_SetTextureBlendMode(texture, 1)
        except BaseException:
            for texture in textures:
                self.d.sdl.SDL_DestroyTexture(texture)
            raise
        for texture in self.surfaces:
            self.d.sdl.SDL_DestroyTexture(texture)
        self.surfaces, self.surface_size = textures, (width, height)
        self.surface_index, self.surface_mode, self.fade_from = 0, None, None
        self.fade_until, self.key = 0., None

    def _smooth_surface(self, source, size, index, projected=None, borders=False):
        sdl, d = self.d.sdl, self.d
        width, height = self.surface_size
        pixels = None
        if self.scaler.available:
            if projected is None:
                if size != self.source_size:
                    self.source_pixels = (C.c_ubyte*(size*size*4))()
                    self.source_size = size
                area = Rect(0, 0, size, size)
                self._check(sdl.SDL_RenderReadPixels(d.renderer, C.byref(area), 0x16762004, self.source_pixels, size*4))
            pixels = self.scaler.scale(projected if projected is not None else self.source_pixels,
                                       size, size, width, height)
        self.world_borders_cached = False
        if pixels is not None:
            if borders:
                self.world_borders_cached = self.scaler.overlay_borders(
                    pixels, width, height, self.border_segments, self.lat, self.lon, self.zoom)
            self._check(sdl.SDL_UpdateTexture(self.surfaces[index], None, pixels, width*4))
        else:
            # Desktop previews can use SDL when the bundled ARM filter cannot load.
            if projected is not None:
                self._check(sdl.SDL_UpdateTexture(source, None, projected, size*4))
            self._check(sdl.SDL_SetRenderTarget(d.renderer, self.surfaces[index]))
            try:
                self._check(sdl.SDL_SetTextureScaleMode(source, 1))
                d.copy(source, (0, 0, width, height))
            finally:
                sdl.SDL_SetTextureScaleMode(source, 0)
                sdl.SDL_SetRenderTarget(d.renderer, source)

    def _mesh(self, sinlat, coslat, sinlon, coslon, radius):
        xy, uv = self._xy, self._uv
        longitudes, poles = self._longitudes, set()
        if radius != self._radius:
            for index, (x, y, _) in enumerate(self.normals):
                xy[index*2], xy[index*2+1] = (175+x*radius)*self._detail, (175-y*radius)*self._detail
            self._radius = radius
        for index, (x, y, z) in enumerate(self.normals):
            north, depth = coslat*y+sinlat*z, -sinlat*y+coslat*z
            east, forward = coslon*x+sinlon*depth, -sinlon*x+coslon*depth
            longitude = math.atan2(east, forward)/math.tau+.5
            latitude = math.acos(1. if north > 1 else -1. if north < -1 else north)/math.pi
            if north > 1-1e-12 or north < -1+1e-12:
                poles.add(index)
            longitudes[index] = longitude
            uv[index*2], uv[index*2+1] = longitude/2, latitude
        triangles = self.triangles
        used = len(self.normals)
        C.memmove(self._indices, self._base_indices, C.sizeof(self._base_indices))
        # The visible geographic pole lies on a vertical mesh spoke. Insert it
        # into the two adjacent triangles so no triangle interpolates over a pole.
        if not poles:
            pole_y = math.copysign(coslat, sinlat)
            spoke = [0] + [1+ring*self._slices+(self._slices//4 if pole_y >= 0 else self._slices*3//4)
                           for ring in range(self._rings)]
            for a, b in zip(spoke, spoke[1:]):
                if min(self.normals[a][1], self.normals[b][1]) < pole_y < max(self.normals[a][1], self.normals[b][1]):
                    pole = used
                    used += 1
                    poles.add(pole)
                    longitudes[pole] = .5
                    uv[pole*2+1] = 0. if sinlat >= 0 else 1.
                    xy[pole*2], xy[pole*2+1] = 175*self._detail, (175-pole_y*radius)*self._detail
                    triangles = self.triangles.copy()
                    for index, other in self._pole_edges[a, b]:
                        triangles[index] = (a, pole, other)
                        triangles.append((pole, b, other))
                    break
        for index, triangle in enumerate(triangles):
            a, b, c = triangle
            ua, ub, uc = longitudes[a], longitudes[b], longitudes[c]
            low = ua if ua < ub else ub
            high = ua if ua > ub else ub
            low = uc if uc < low else low
            high = uc if uc > high else high
            pole_triangle = a in poles or b in poles or c in poles
            if high-low <= .5 and not pole_triangle:
                continue
            values = [longitudes[v] for v in triangle if v not in poles]
            seam = max(values)-min(values) > .5
            pole_u = sum(u+1 if seam and u < .5 else u for u in values)/len(values)
            count = index*3
            for vertex in triangle:
                u = longitudes[vertex]
                if vertex in poles or (seam and u < .5):
                    xy[used*2], xy[used*2+1] = xy[vertex*2], xy[vertex*2+1]
                    uv[used*2] = (pole_u if vertex in poles else u+1)/2
                    uv[used*2+1] = uv[vertex*2+1]
                    vertex, used = used, used+1
                self._indices[count] = vertex
                count += 1
        self._vertex_count, self._index_count = used, len(triangles)*3
        if radius > 175:
            # At country zoom most of the hemisphere is outside the target.
            # Avoid submitting those triangles to SDL's software rasterizer.
            edge, count = 350*self._detail, 0
            for index in range(0, self._index_count, 3):
                a, b, c = self._indices[index:index+3]
                ax, ay = xy[a*2], xy[a*2+1]
                bx, by = xy[b*2], xy[b*2+1]
                cx, cy = xy[c*2], xy[c*2+1]
                if ((ax < 0 and bx < 0 and cx < 0) or (ax > edge and bx > edge and cx > edge)
                        or (ay < 0 and by < 0 and cy < 0) or (ay > edge and by > edge and cy > edge)):
                    continue
                self._indices[count:count+3] = (a, b, c)
                count += 3
            self._index_count = count

    def _vector_map(self, size, country, sinlat, coslat, sinlon, coslon, radius):
        """Rasterize only nearby polygons once for a settled close-up."""
        camera = (coslat*sinlon, sinlat, coslat*coslon)
        view_angle = math.asin(min(1., math.sqrt(2)*175/radius))
        fills = {False: [], True: []}
        self.vector_paths = []
        self.vector_line_key = None
        scale, center = radius*size/350, size/2
        for code, cap, angle, rings in self.vector_geometry:
            if sum(a*b for a, b in zip(camera, cap)) < math.cos(min(math.pi, angle+view_angle)):
                continue
            projected = []
            for ring in rings:
                points = []
                for x, y, z in ring:
                    depth = sinlon*x+coslon*z
                    points.append((coslon*x-sinlon*z, coslat*y-sinlat*depth, sinlat*y+coslat*depth))
                visible = []
                for previous, point in zip(points[-1:]+points[:-1], points):
                    if (previous[2] >= 0) != (point[2] >= 0):
                        t = previous[2]/(previous[2]-point[2])
                        x, y = previous[0]+t*(point[0]-previous[0]), previous[1]+t*(point[1]-previous[1])
                        length = math.hypot(x, y) or 1.
                        visible.append((x/length, y/length))
                    if point[2] >= 0:
                        visible.append(point[:2])
                if len(visible) >= 3:
                    projected.append(visible)
            if not projected:
                continue
            pixels = [[(center+x*scale, center-y*scale) for x, y in ring] for ring in projected]
            all_points = [point for ring in pixels for point in ring]
            if (max(x for x, _ in all_points) < 0 or min(x for x, _ in all_points) >= size
                    or max(y for _, y in all_points) < 0 or min(y for _, y in all_points) >= size):
                continue
            self.vector_paths.append((code, projected))
            edges = [(a, b) for ring in pixels for a, b in zip(ring, ring[1:]+ring[:1])]
            low = max(0, math.ceil(min(y for _, y in all_points)-.5))
            high = min(size, math.ceil(max(y for _, y in all_points)-.5))
            spans = fills[code == country]
            # Even/odd filling retains lakes, enclaves and other polygon holes.
            for y in range(low, high):
                scan = y+.5
                xs = sorted(x1+(scan-y1)*(x2-x1)/(y2-y1)
                            for (x1, y1), (x2, y2) in edges if (y1 > scan) != (y2 > scan))
                for left, right in zip(xs[::2], xs[1::2]):
                    start, end = max(0, math.ceil(left-.5)), min(size, math.ceil(right-.5))
                    if end > start:
                        spans.append(Rect(start, y, end-start, 1))
        for selected, spans in fills.items():
            if spans:
                color = (158, 120, 32, 255) if selected else (13, 62, 34, 255)
                self._check(self.d.sdl.SDL_SetRenderDrawColor(self.d.renderer, *color))
                self._check(self.d.sdl.SDL_RenderFillRects(self.d.renderer, (Rect*len(spans))(*spans), len(spans)))

    def _selected_border(self, viewport, country, refined, clip, alpha):
        key = (self.key, viewport, (clip.x, clip.y, clip.w, clip.h), refined)
        if key != self.selection_key:
            paths = [points for selected, points in self.vector_lines if selected] if refined else []
            if country and not refined:
                vx, vy, vw, vh = viewport
                lat, lon = math.radians(self.lat), math.radians(self.lon)
                slat, clat, slon, clon = math.sin(lat), math.cos(lat), math.sin(lon), math.cos(lon)
                radius = 150*self.zoom
                for code, _, _, rings in self.vector_geometry:
                    if code != country:
                        continue
                    for ring in rings:
                        points = []
                        for x, y, z in ring+ring[:1]:
                            depth = slon*x+clon*z
                            if slat*y+clat*depth < 0:
                                if len(points) > 1:
                                    paths.append((Point*len(points))(*(Point(*p) for p in points)))
                                points = []
                                continue
                            points.append((round(vx+vw/2+(clon*x-slon*z)*radius*vw/350),
                                           round(vy+vh/2-(clat*y-slat*depth)*radius*vh/350)))
                        if len(points) > 1:
                            paths.append((Point*len(points))(*(Point(*p) for p in points)))
            self.selection_lines = paths
            self.trace_segments, self.trace_length = [], 0.
            for points in paths:
                for first, last in zip(points, points[1:]):
                    dx, dy = last.x-first.x, last.y-first.y
                    low, high = 0., 1.
                    for p, q in ((-dx, first.x-clip.x), (dx, clip.x+clip.w-1-first.x),
                                 (-dy, first.y-clip.y), (dy, clip.y+clip.h-1-first.y)):
                        if not p:
                            if q < 0:
                                high = -1
                                break
                        elif p < 0:
                            low = max(low, q/p)
                        else:
                            high = min(high, q/p)
                    if low > high:
                        continue
                    x, y = first.x+dx*low, first.y+dy*low
                    end_x, end_y = first.x+dx*high, first.y+dy*high
                    length = math.hypot(end_x-x, end_y-y)
                    if length:
                        self.trace_segments.append((self.trace_length, self.trace_length+length, x, y, end_x, end_y))
                        self.trace_length += length
            self.trace_active = bool(country and self.trace_length)
            self.selection_key = key
        sdl, renderer = self.d.sdl, self.d.renderer
        self._check(sdl.SDL_SetRenderDrawColor(renderer, 242, 201, 76, alpha))
        for points in self.selection_lines:
            self._check(sdl.SDL_RenderDrawLines(renderer, points, len(points)))
        if not self.trace_active or not alpha:
            return
        head = (int(time.monotonic()*12)/12*36) % self.trace_length
        band = min(32., self.trace_length*.4)/4
        for i, color in enumerate(TRACE_COLORS):
            start = (head-(4-i)*band) % self.trace_length
            end = start+band
            intervals = [(start, min(end, self.trace_length))]
            if end > self.trace_length:
                intervals.append((0., end-self.trace_length))
            for left, right in intervals:
                for a, b, x, y, end_x, end_y in self.trace_segments:
                    low, high = max(left, a), min(right, b)
                    if low < high:
                        t, u = (low-a)/(b-a), (high-a)/(b-a)
                        self.d.line(x+(end_x-x)*t, y+(end_y-y)*t,
                                    x+(end_x-x)*u, y+(end_y-y)*u, (*color, alpha))

    def _native_outlines(self, viewport, country, refined, clip, opacity=1.):
        vx, vy, vw, vh = viewport
        radius = 150*self.zoom
        if refined:
            key = (self.key, viewport)
            if key != self.vector_line_key:
                self.vector_lines = []
                for code, rings in sorted(self.vector_paths, key=lambda row: row[0] == country):
                    for ring in rings:
                        points = [(round(vx+vw/2+x*radius*vw/350), round(vy+vh/2-y*radius*vh/350))
                                  for x, y in ring]
                        points.append(points[0])
                        self.vector_lines.append((code == country, (Point*len(points))(*(Point(*p) for p in points))))
                self.vector_line_key = key
            for selected, points in self.vector_lines:
                if selected:
                    continue
                color = (64, 196, 104, round(255*opacity))
                self._check(self.d.sdl.SDL_SetRenderDrawColor(self.d.renderer, *color))
                self._check(self.d.sdl.SDL_RenderDrawLines(self.d.renderer, points, len(points)))
        elif not self.world_borders_cached:
            # Optional-library fallback for desktop previews: cache projected
            # line paths rather than hiding borders below the close-up threshold.
            key = (self.key, viewport)
            if self.world_line_key != key:
                self.world_lines = []
                lat, lon = math.radians(self.lat), math.radians(self.lon)
                slat, clat, slon, clon = math.sin(lat), math.cos(lat), math.sin(lon), math.cos(lon)
                for _, _, _, rings in self.vector_geometry:
                    for ring in rings:
                        path = []
                        for x, y, z in ring+ring[:1]:
                            depth = slon*x+clon*z
                            if slat*y+clat*depth < 0:
                                if len(path) > 1:
                                    self.world_lines.append((Point*len(path))(*(Point(*p) for p in path)))
                                path = []
                                continue
                            path.append((round(vx+vw/2+(clon*x-slon*z)*radius*vw/350),
                                         round(vy+vh/2-(clat*y-slat*depth)*radius*vh/350)))
                        if len(path) > 1:
                            self.world_lines.append((Point*len(path))(*(Point(*p) for p in path)))
                self.world_line_key = key
            self._check(self.d.sdl.SDL_SetRenderDrawColor(self.d.renderer, 64, 196, 104, 255))
            for points in self.world_lines:
                self._check(self.d.sdl.SDL_RenderDrawLines(self.d.renderer, points, len(points)))
        self._selected_border(viewport, country, refined, clip, round(255*opacity) if refined else 255)
        if self.zoom < 2:
            key = (self.zoom, viewport)
            if key != self.rim_key:
                self.rim_points = (Point*129)(*(Point(round(vx+vw/2+radius*vw/350*math.cos(i*math.tau/128)),
                                                      round(vy+vh/2+radius*vh/350*math.sin(i*math.tau/128)))
                                               for i in range(129)))
                self.rim_key = key
            self._check(self.d.sdl.SDL_SetRenderDrawColor(self.d.renderer, 64, 196, 104, 255))
            self._check(self.d.sdl.SDL_RenderDrawLines(self.d.renderer, self.rim_points, 129))

    def draw(self, country="", label=None, viewport=(0, 88, 350, 350), bounds=None):
        d, sdl = self.d, self.d.sdl
        self._ensure_surfaces(viewport[2], viewport[3])
        previous = Rect()
        clipped = sdl.SDL_RenderIsClipEnabled(d.renderer)
        sdl.SDL_RenderGetClipRect(d.renderer, C.byref(previous))
        key = (self.lat, self.lon, self.zoom, country)
        moving = time.monotonic() < self.moving_until or self.spinning
        refined = self.zoom >= 2 and not moving and bool(self.vector_geometry)
        size = self.motion_size if moving else self.resolution
        texture = self.motion_texture if moving else self.cached
        detail = size/350
        if self._detail != detail:
            self._detail, self._radius, self.key = detail, None, None
        lat, lon = math.radians(self.lat), math.radians(self.lon)
        sinlat, coslat, sinlon, coslon = math.sin(lat), math.cos(lat), math.sin(lon), math.cos(lon)
        radius = 150*self.zoom
        if key != self.key:
            mode = (refined, size)
            switching = self.surface_mode is not None and mode != self.surface_mode
            index = 1-self.surface_index if switching else self.surface_index
            if not refined and country != self._country:
                self._colorize(country)
            projected = (self.scaler.project(self.atlas_pixels, self.width, self.height, size,
                                             self.lat, self.lon, self.zoom) if not refined else None)
            if not refined and projected is None:
                self._mesh(sinlat, coslat, sinlon, coslon, radius)
            target = sdl.SDL_GetRenderTarget(d.renderer)
            if projected is None:
                self._check(sdl.SDL_SetRenderTarget(d.renderer, texture))
            try:
                if projected is not None:
                    self._smooth_surface(texture, size, index, projected, borders=True)
                elif refined:
                    d.rect(0, 0, size, size, (5, 19, 12, 255))
                    self._vector_map(size, country, sinlat, coslat, sinlon, coslon, radius)
                    self._smooth_surface(texture, size, index)
                else:
                    # Replace leftovers from the previous frame, then paint a
                    # translucent square so the app scanline backdrop shows.
                    sdl.SDL_SetRenderDrawBlendMode(d.renderer, 0)
                    d.rect(0, 0, size, size, (0, 0, 0, 0))
                    sdl.SDL_SetRenderDrawBlendMode(d.renderer, 1)
                    d.rect(0, 0, size, size, (9, 10, 12, 90))
                    self._check(sdl.SDL_RenderGeometryRaw(d.renderer, self.atlas_texture,
                        self._xy, 8, C.byref(self._white), 0, self._uv, 8,
                        self._vertex_count, self._indices, self._index_count, 4))
                    self._smooth_surface(texture, size, index, borders=True)
            finally:
                if sdl.SDL_GetRenderTarget(d.renderer) != target:
                    self._check(sdl.SDL_SetRenderTarget(d.renderer, target))
                self._check(sdl.SDL_RenderSetClipRect(d.renderer, C.byref(previous) if clipped else None))
            self.key = key
            if switching and FADE_SECONDS:
                self.fade_from, self.fade_zoom = self.surface_index, self.surface_zoom
                self.fade_started = time.monotonic()
                self.fade_until = self.fade_started+FADE_SECONDS
            elif switching:
                self.fade_from, self.fade_until = None, 0.
            self.surface_index, self.surface_mode, self.surface_zoom = index, mode, self.zoom
        vx, vy, vw, vh = viewport
        clip = Rect(*(bounds or viewport))
        if clipped:
            right, bottom = min(clip.x+clip.w, previous.x+previous.w), min(clip.y+clip.h, previous.y+previous.h)
            clip.x, clip.y = max(clip.x, previous.x), max(clip.y, previous.y)
            clip.w, clip.h = max(0, right-clip.x), max(0, bottom-clip.y)
        self._check(sdl.SDL_RenderSetClipRect(d.renderer, C.byref(clip)))
        try:
            d.copy(self.surfaces[self.surface_index], viewport)
            opacity = 1.
            now = time.monotonic()
            if self.fade_from is not None and now < self.fade_until:
                progress = max(0., min(1., (now-self.fade_started)/max(.001, FADE_SECONDS)))
                opacity = progress*progress*(3-2*progress)
                old = self.surfaces[self.fade_from]
                ratio = self.zoom/self.fade_zoom
                old_view = (vx+vw*(1-ratio)/2, vy+vh*(1-ratio)/2, vw*ratio, vh*ratio)
                self._check(sdl.SDL_SetTextureBlendMode(old, 1))
                self._check(sdl.SDL_SetTextureAlphaMod(old, round(255*(1-opacity))))
                try:
                    d.copy(old, old_view)
                finally:
                    sdl.SDL_SetTextureAlphaMod(old, 255)
                    sdl.SDL_SetTextureBlendMode(old, 1)
            else:
                self.fade_from = None
            self._native_outlines(viewport, country, refined, clip, opacity)
            if label:
                text, label_lat, label_lon = label
                mlat, mlon = math.radians(label_lat), math.radians(label_lon)
                delta, c = mlon-lon, math.cos(mlat)
                depth = math.sin(mlat)*sinlat+c*coslat*math.cos(delta)
                if depth >= 0:
                    x = vx+vw/2+radius*c*math.sin(delta)*vw/350
                    y = vy+vh/2-radius*(math.sin(mlat)*coslat-c*sinlat*math.cos(delta))*vh/350
                    if clip.x-30 <= x < clip.x+clip.w+30 and clip.y-24 <= y < clip.y+clip.h+24:
                        # Plain amber numeral matching the selected country fill.
                        tw, th = self.d.measure(text, 18)
                        d.text(text, round(x-tw/2), round(y-th/2), (242, 201, 76, 255), 18)
        finally:
            self._check(sdl.SDL_RenderSetClipRect(d.renderer, C.byref(previous) if clipped else None))

    def close(self):
        for texture in self.surfaces:
            self.d.sdl.SDL_DestroyTexture(texture)
        self.surfaces = []
        for name in ("cached", "motion_texture", "atlas_texture"):
            texture = getattr(self, name, None)
            if texture:
                self.d.sdl.SDL_DestroyTexture(texture)
                setattr(self, name, None)
        self.key = None

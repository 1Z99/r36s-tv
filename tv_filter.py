"""Optional native RGBA scaling, projection and borders; no runtime build or SDL."""

import ctypes as C
import math
import os
from pathlib import Path


class Scaler:
    """Separate reusable scale/project output buffers; not thread-safe.

    Upload the returned tightly packed RGBA ctypes byte array before the next
    call to the same operation. Missing/incompatible libraries and invalid calls
    return None and set error. Invalid calls do not disable an available library.
    overlay_borders() modifies its supplied output in place and returns a bool.
    """

    def __init__(self):
        self.available = False
        self.projection_available = False
        self.borders_available = False
        self.error = ""
        self._library = self._scale = self._destination = None
        self._project = self._projection_destination = None
        self._projection_error = "Globe projection unavailable"
        self._borders = None
        self._borders_error = "Globe borders unavailable"
        path = os.environ.get("TV227_GLOBE_FILTER") or str(
            Path(__file__).resolve().parent / "native" / "libtvglobe.so")
        try:
            self._library = C.CDLL(path)
            self._scale = self._library.tv_globe_scale
            self._scale.argtypes = [C.c_void_p, C.c_int, C.c_int, C.c_int,
                                    C.c_void_p, C.c_int, C.c_int, C.c_int]
            self._scale.restype = C.c_int
        except (OSError, AttributeError, TypeError, ValueError) as exc:
            self.error = "Cannot load globe filter {!r}: {}".format(path, exc)
            return
        self.available = True
        try:
            abi = self._library.tv_globe_project_abi
            abi.argtypes, abi.restype = [], C.c_int
            if abi() != 1:
                raise ValueError("unsupported globe projector ABI")
            self._project = self._library.tv_globe_project
            self._project.argtypes = [C.c_void_p, C.c_int, C.c_int, C.c_int,
                                      C.c_void_p, C.c_int, C.c_int,
                                      C.c_float, C.c_float, C.c_float]
            self._project.restype = C.c_int
        except (OSError, AttributeError, TypeError, ValueError) as exc:
            self._projection_error = "Globe projection unavailable: {}".format(exc)
        else:
            self.projection_available = True
        try:
            abi = self._library.tv_globe_borders_abi
            abi.argtypes, abi.restype = [], C.c_int
            if abi() != 1:
                raise ValueError("unsupported globe borders ABI")
            self._borders = self._library.tv_globe_borders
            self._borders.argtypes = [C.POINTER(C.c_float), C.c_int, C.c_void_p,
                                      C.c_int, C.c_int, C.c_int,
                                      C.c_float, C.c_float, C.c_float]
            self._borders.restype = C.c_int
        except (OSError, AttributeError, TypeError, ValueError) as exc:
            self._borders_error = "Globe borders unavailable: {}".format(exc)
        else:
            self.borders_available = True

    def scale(self, source, sw, sh, dw, dh, src_pitch=None):
        """Scale a sized ctypes array; src_pitch defaults to sw * 4 bytes.

        Dimensions must be integers in 1..8192. A pointer without a known buffer
        size is deliberately rejected. Source rows may have padding; the final
        row need only contain its actual pixels, not trailing padding.
        """
        if not self.available:
            return None
        try:
            if any(not isinstance(value, int) or isinstance(value, bool)
                   or not 1 <= value <= 8192 for value in (sw, sh, dw, dh)):
                raise ValueError("dimensions must be integers in 1..8192")
            if src_pitch is None:
                src_pitch = sw * 4
            if (not isinstance(src_pitch, int) or isinstance(src_pitch, bool)
                    or not sw * 4 <= src_pitch <= 2147483647):
                raise ValueError("src_pitch must be an integer >= sw * 4 and <= INT_MAX")
            if not isinstance(source, C.Array):
                raise TypeError("source must be a sized ctypes array, not a pointer")
            needed = (sh - 1) * src_pitch + sw * 4
            if needed > (1 << (C.sizeof(C.c_void_p) * 8 - 1)) - 1:
                raise ValueError("source stride span exceeds PTRDIFF_MAX")
            if C.sizeof(source) < needed:
                raise ValueError("source buffer too small: need {} bytes, have {}".format(
                    needed, C.sizeof(source)))
            size = dw * dh * 4
            address = C.addressof(source)
            destination = self._destination
            if (destination is None or C.sizeof(destination) != size
                    or (address < C.addressof(destination) + size
                        and C.addressof(destination) < address + needed)):
                destination = (C.c_uint8 * size)()
                self._destination = destination
            result = self._scale(source, sw, sh, src_pitch, destination, dw, dh, dw * 4)
            if result != 0:
                raise ValueError("native globe filter rejected arguments (status {})".format(result))
        except (TypeError, ValueError, OverflowError, MemoryError, C.ArgumentError) as exc:
            self.error = "Globe scale failed: {}".format(exc)
            return None
        self.error = ""
        return destination

    def project(self, source, width, height, size, latitude, longitude, zoom):
        """Project a tightly packed, undoubled equirectangular RGBA ctypes array.

        Atlas dimensions: 1..8192; square output: 1..2048. Angles are degrees,
        latitude -90..90, any finite longitude, zoom > 0 and <= 64. Outside the
        globe is opaque (9, 10, 12, 255). Output remains valid across scale calls.
        """
        if not self.projection_available:
            if self.available:
                self.error = self._projection_error
            return None
        try:
            if any(not isinstance(value, int) or isinstance(value, bool)
                   or not 1 <= value <= 8192 for value in (width, height)):
                raise ValueError("atlas dimensions must be integers in 1..8192")
            if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= 2048:
                raise ValueError("projection size must be an integer in 1..2048")
            if any(not isinstance(value, (int, float)) or isinstance(value, bool)
                   or not math.isfinite(value) for value in (latitude, longitude, zoom)):
                raise ValueError("latitude, longitude and zoom must be finite numbers")
            if not -90 <= latitude <= 90 or not 0 < zoom <= 64:
                raise ValueError("latitude must be in -90..90 and zoom in (0, 64]")
            longitude = math.remainder(longitude, 360.0)
            if C.c_float(zoom).value == 0:
                raise ValueError("zoom is too small for the native float ABI")
            if not isinstance(source, C.Array):
                raise TypeError("source must be a sized ctypes array, not a pointer")
            needed = width * height * 4
            if C.sizeof(source) < needed:
                raise ValueError("source buffer too small: need {} bytes, have {}".format(
                    needed, C.sizeof(source)))
            length = size * size * 4
            address = C.addressof(source)
            destination = self._projection_destination
            if (destination is None or C.sizeof(destination) != length
                    or (address < C.addressof(destination) + length
                        and C.addressof(destination) < address + needed)):
                destination = (C.c_uint8 * length)()
                self._projection_destination = destination
            result = self._project(source, width, height, width * 4, destination, size, size * 4,
                                   latitude, longitude, zoom)
            if result != 0:
                raise ValueError("native globe projector rejected arguments (status {})".format(result))
        except (TypeError, ValueError, OverflowError, MemoryError, C.ArgumentError) as exc:
            self.error = "Globe project failed: {}".format(exc)
            return None
        self.error = ""
        return destination

    def overlay_borders(self, destination, width, height, segments, latitude, longitude, zoom):
        """Paint one-native-pixel AA borders onto tightly packed mutable RGBA.

        Call after scale(), before upload. Segments must be a flat ctypes.c_float
        array of XYZ endpoint pairs (six floats each), at most 200000 segments.
        Camera ranges match project(); dimensions are 1..8192. No output buffer
        is allocated or replaced. False means unavailable or invalid input.
        """
        if not self.borders_available:
            if self.available:
                self.error = self._borders_error
            return False
        try:
            if any(not isinstance(value, int) or isinstance(value, bool)
                   or not 1 <= value <= 8192 for value in (width, height)):
                raise ValueError("dimensions must be integers in 1..8192")
            if not isinstance(destination, C.Array):
                raise TypeError("destination must be a sized mutable ctypes array")
            needed = width * height * 4
            if C.sizeof(destination) < needed:
                raise ValueError("destination buffer too small: need {} bytes, have {}".format(
                    needed, C.sizeof(destination)))
            if not isinstance(segments, C.Array) or segments._type_ is not C.c_float:
                raise TypeError("segments must be a flat ctypes.c_float array")
            if len(segments) % 6 or len(segments) > 200000 * 6:
                raise ValueError("segments must contain six floats per segment, at most 200000 segments")
            if any(not isinstance(value, (int, float)) or isinstance(value, bool)
                   or not math.isfinite(value) for value in (latitude, longitude, zoom)):
                raise ValueError("latitude, longitude and zoom must be finite numbers")
            if not -90 <= latitude <= 90 or not 0 < zoom <= 64:
                raise ValueError("latitude must be in -90..90 and zoom in (0, 64]")
            if C.c_float(zoom).value == 0:
                raise ValueError("zoom is too small for the native float ABI")
            result = self._borders(segments, len(segments) // 6, destination, width, height,
                                   width * 4, latitude, math.remainder(longitude, 360.0), zoom)
            if result != 0:
                raise ValueError("native globe borders rejected arguments (status {})".format(result))
        except (TypeError, ValueError, OverflowError, MemoryError, C.ArgumentError) as exc:
            self.error = "Globe borders failed: {}".format(exc)
            return False
        self.error = ""
        return True

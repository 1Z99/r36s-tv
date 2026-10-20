#!/usr/bin/env python3
"""Offline, standard-library Natural Earth rasterizer for assets/globe-atlas.z."""

import argparse
import json
import math
from pathlib import Path
import struct
import zlib


def rasterize(features, width=1024, height=512, grid=True):
    """Return country IDs and style bytes, sampling at equirectangular pixel centers.

    Styles: ocean, land, border, ocean grid, land grid. Rings use even/odd
    filling, including holes; longitude is unwrapped before scan conversion.
    """
    if width < 4 or height < 2 or width > 4096 or height > 2048:
        raise ValueError("Invalid atlas dimensions")
    codes = [""] + sorted({f["properties"]["code"] for f in features})
    if len(codes) > 256:
        raise ValueError("Country IDs must fit in one byte")
    numbers = {code: i for i, code in enumerate(codes)}
    ids = bytearray(width * height)
    for feature in sorted(features, key=lambda f: f["properties"]["code"]):
        geo = feature["geometry"]
        polygons = [geo["coordinates"]] if geo["type"] == "Polygon" else geo["coordinates"]
        value = bytes([numbers[feature["properties"]["code"]]])
        for polygon in polygons:
            rings = []
            for ring in polygon:
                points = []
                for lon, lat in ring:
                    if points:
                        previous = points[-1][0] * 360 / width - 180
                        # The horizontal edge across a polar cap is intentional.
                        if not (abs(lat) >= 89.999 and abs(90 - points[-1][1]*180/height) >= 89.999):
                            lon += 360 * math.floor((previous - lon + 180) / 360)
                    points.append(((lon + 180) * width / 360, (90 - lat) * height / 180))
                if len(points) < 3:
                    continue
                if rings:
                    shift = round((sum(x for x, _ in rings[0])/len(rings[0])
                                   - sum(x for x, _ in points)/len(points))/width)*width
                    points = [(x + shift, y) for x, y in points]
                # A ring winding around a pole closes along that pole, not across land.
                if abs(points[-1][0] - points[0][0]) > width / 2:
                    pole = height if sum(y for _, y in points)/len(points) > height/2 else 0
                    points += [(points[-1][0], pole), (points[0][0], pole)]
                rings.append(points)
            if not rings:
                continue
            edges = [(a, b) for ring in rings for a, b in zip(ring, ring[1:] + ring[:1])]
            low = max(0, math.ceil(min(y for ring in rings for _, y in ring) - .5))
            high = min(height, math.ceil(max(y for ring in rings for _, y in ring) - .5))
            for y in range(low, high):
                scan = y + .5
                xs = sorted(x1 + (scan-y1)*(x2-x1)/(y2-y1)
                            for (x1, y1), (x2, y2) in edges if (y1 > scan) != (y2 > scan))
                for left, right in zip(xs[::2], xs[1::2]):
                    start, end = math.ceil(left-.5), math.ceil(right-.5)
                    remaining = min(width, end-start)
                    start %= width
                    while remaining > 0:
                        count = min(remaining, width-start)
                        ids[y*width+start:y*width+start+count] = value * count
                        remaining -= count
                        start = 0
    styles = bytearray(len(ids))
    for y in range(height):
        for x in range(width):
            i = y*width+x
            country = ids[i]
            # Mark only one side of each shared edge, not a two-pixel band.
            border = (country > ids[y*width+(x-1) % width]
                      or country > ids[y*width+(x+1) % width]
                      or country > ids[max(0, y-1)*width+x]
                      or country > ids[min(height-1, y+1)*width+x])
            styles[i] = 2 if border else int(bool(country))
    if grid:
        columns = {round(i*width/12) % width for i in range(12)}
        rows = {round(i*height/6) for i in range(1, 6)}
        for y in range(height):
            for x in range(width):
                i = y*width+x
                if styles[i] != 2 and (x in columns or y in rows):
                    styles[i] = 4 if ids[i] else 3
    return width, height, codes, bytes(ids), bytes(styles)


def encode_atlas(atlas):
    width, height, codes, ids, styles = atlas
    names = json.dumps(codes, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return zlib.compress(struct.pack(">4sHHH", b"GAT1", width, height, len(names))
                         + names + ids + styles, 9)


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=root / "assets/globe.json")
    parser.add_argument("--output", type=Path, default=root / "assets/globe-atlas.z")
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=512)
    args = parser.parse_args()
    features = json.loads(args.source.read_text())["features"]
    data = encode_atlas(rasterize(features, args.width, args.height))
    args.output.write_bytes(data)
    print("%s: %dx%d, %d bytes" % (args.output, args.width, args.height, len(data)))


if __name__ == "__main__":
    main()

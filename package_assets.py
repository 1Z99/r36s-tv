#!/usr/bin/env python3
"""Bundle the upstream map, notices and font without third-party packages."""

import argparse
import json
from pathlib import Path
import shutil
from generate_globe import encode_atlas, rasterize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Upstream Melkor desktop plugin")
    parser.add_argument("--font-dir", type=Path, required=True, help="DejaVu fonts and copyright notice")
    parser.add_argument("--ai-source", type=Path, required=True, help="227 AI source/assets checkout")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    assets = root / "assets"
    copies = {
        args.source / "assets/countries.json": assets / "globe.json",
        args.source / "LICENSE": root / "LICENSE",
        args.source / "assets/NOTICE.md": assets / "NOTICE.md",
        args.font_dir / "DejaVuSansMono.ttf": assets / "DejaVuSansMono.ttf",
        args.font_dir / "copyright": assets / "FONT-LICENSE",
        args.ai_source / "LICENSE": assets / "AI-LICENSE",
    }
    for name in ("intro-frames.zip", "intro-background.rgba", "logo.rgba", "DejaVuSansMono-Bold.ttf"):
        copies[args.ai_source / "assets" / name] = assets / name
    for source in copies:
        if not source.is_file():
            parser.error("Missing asset: {}".format(source))
    with (args.source / "assets/countries.json").open(encoding="utf-8") as stream:
        globe = json.load(stream)
    countries = {feature["properties"]["code"].upper(): feature["properties"]["name"]
                 for feature in globe["features"] if feature["properties"].get("code")}
    if not countries:
        parser.error("GeoJSON contains no country codes")
    assets.mkdir(exist_ok=True)
    for source, target in copies.items():
        shutil.copyfile(source, target)
        print("Bundled {}".format(target.relative_to(root)))
    with (assets / "countries.json").open("w", encoding="utf-8") as stream:
        json.dump(countries, stream, ensure_ascii=True, sort_keys=True, indent=2)
        stream.write("\n")
    print("Generated assets/countries.json ({} countries)".format(len(countries)))
    (assets / "globe-atlas.z").write_bytes(encode_atlas(rasterize(globe["features"])))


if __name__ == "__main__":
    main()

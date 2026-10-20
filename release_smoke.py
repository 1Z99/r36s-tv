#!/usr/bin/env python3
"""Install a generated release in a private test card. No GUI or package writes."""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--modules", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.modules))
    from tv_installer import Installer, extract_bundle, verify_tree
    from tv_dependencies import ensure
    release = json.loads((args.release_dir / "release.json").read_text())
    with tempfile.TemporaryDirectory(prefix="tv227-release-check-") as directory:
        root = Path(directory)
        payload = root / "payload"
        extract_bundle(args.release_dir / release["bundle"]["file"], payload,
                       release["bundle"]["unpacked_bytes"])
        verify_tree(payload)
        status = ensure(payload, allow_install=False)
        if not status["ok"]:
            raise RuntimeError("Read-only hardware dependency check failed: "+json.dumps(status, indent=2))
        card = root / "test card with spaces"
        card.mkdir()
        config = root / "es_systems.cfg"
        worker = Installer(card, base=args.release_dir.as_uri(), release=release,
                           es_cfg=config, development=True)
        worker.run("install")
        system = card / "227ai"
        assert not (card / "ports").exists()
        assert (system / "Melkor 227 TV.sh").is_file()
        tree = ET.parse(system / "gamelist.xml")
        game = ET.SubElement(tree.getroot(), "game")
        for key, value in (("path", "./227 AI.sh"), ("name", "227 AI"), ("playcount", "42")):
            ET.SubElement(game, key).text = value
        tree.write(system / "gamelist.xml", encoding="utf-8", xml_declaration=True)
        (system / "227 AI.sh").write_text("test AI launcher\n")
        config_before = config.read_bytes()
        worker.run("update")
        assert b"<playcount>42</playcount>" in (system / "gamelist.xml").read_bytes()
        assert config.read_bytes() == config_before
        worker.run("uninstall")
        assert (system / "227 AI.sh").read_text() == "test AI launcher\n"
        assert b"<playcount>42</playcount>" in (system / "gamelist.xml").read_bytes()
        assert not (system / "Melkor 227 TV.sh").exists()
        assert not (card / "tools/melkor227-tv").exists()
        assert config.read_bytes() == config_before
        print(json.dumps(dict(dependency_preflight="passed, no package changes",
                              verified_release=release["version"], install="passed", update="passed",
                              uninstall="passed", shared_ai_entry="preserved", system="227",
                              physical_display_opened=False, actual_es_config_changed=False), indent=2))


if __name__ == "__main__":
    main()

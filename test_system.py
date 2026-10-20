"""Offline shared-menu tests. No installer, device or ES process is invoked."""

from contextlib import ExitStack
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import tv_system
from tv_system import ART, LAUNCHER, SystemMenu


SOURCE = Path(__file__).resolve().parent


class SystemMenuTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tv227-system-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        (self.source / "assets").mkdir(parents=True)
        for name in (LAUNCHER, "assets/melkor227.svg"):
            shutil.copyfile(SOURCE / name, self.source / name)
        self.prefix = self.root / "card with spaces"
        self.prefix.mkdir()
        self.cfg = self.root / "es_systems.cfg"
        self.themes = self.root / "themes"
        self.themes.mkdir()
        self.menu = SystemMenu(self.source, self.prefix, self.cfg, self.themes)
        self.directory = self.prefix / "227ai"
        self.lock_patch = patch.object(tv_system, "LOCK", self.root / "227-menu.lock")
        self.lock_patch.start()
        self.addCleanup(self.lock_patch.stop)

    def snapshot(self):
        return {str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mtime_ns,
                                                  path.stat().st_ino, stat.S_IMODE(path.stat().st_mode))
                for path in self.root.rglob("*") if path.is_file() and not path.is_symlink()
                and path.name != "227-menu.lock"}

    def transaction_snapshot(self):
        # Recovery restores bytes/modes/absence, not inodes or timestamps, and
        # deliberately retains any XML backups made before the failure.
        return {name: (info[0], info[3]) for name, info in self.snapshot().items()
                if ".bak-" not in Path(name).name}

    def assert_rejected(self, exception=ValueError):
        before = self.snapshot()
        for action in (self.menu.validate, self.menu.install):
            with self.subTest(action=action.__name__):
                with self.assertRaises(exception):
                    action()
                self.assertEqual(self.snapshot(), before)

    def ai(self, directory=None):
        self.directory = directory or self.directory
        self.directory.mkdir()
        self.cfg.write_text(
            '<?xml version="1.0"?><systemList><!-- systems stay -->'
            '<system><name>ports</name><path>/unrelated</path><command>keep</command></system>'
            '<system custom="yes"><name>227</name><fullname>227 AI family</fullname>'
            f'<path>{self.directory}/</path><extension>.sh .SH .py</extension>'
            '<command>custom &amp; command %ROM%</command><platform>custom</platform>'
            '<theme>227</theme><metadata>keep me</metadata></system></systemList>')
        (self.directory / "227 AI.sh").write_text("AI launcher, leave unchanged\n")
        (self.directory / "227-art.jpg").write_bytes(b"AI cover")
        (self.directory / "gamelist.xml").write_text(
            '<?xml version="1.0"?><gameList><!-- AI stats stay -->'
            '<game id="ai"><path>./227 AI.sh</path><name>227 AI</name>'
            '<image>./227-art.jpg</image><playcount>42</playcount>'
            '<lastplayed>20261019T123456</lastplayed><favorite>true</favorite>'
            '<custom foo="bar">AI metadata</custom></game>'
            '<folder><path>./other</path><name>Other apps</name></folder></gameList>')
        data = self.prefix / "tools/melkor227-tv/data"
        data.mkdir(parents=True)
        (data / "favorites.json").write_text('["user favorite"]')
        (data.parent / "app.py").write_text("# app stays\n")
        (self.prefix / "tools" / LAUNCHER).write_bytes((self.source / LAUNCHER).read_bytes())
        (self.prefix / "tools/unrelated.sh").write_text("other app")

    def legacy(self, owned=True):
        ports = self.prefix / "ports"
        (ports / "images").mkdir(parents=True)
        (ports / LAUNCHER).write_bytes((self.source / LAUNCHER).read_bytes()
                                       if owned else b"#!/bin/sh\n# someone else's script\n")
        shutil.copyfile(self.source / "assets/melkor227.svg", ports / ART)
        (ports / "unrelated.sh").write_text("other app")
        (ports / "gamelist.xml").write_text(
            '<gameList><!-- Ports comment -->'
            f'<game><path>./{LAUNCHER}</path><name>Old TV</name><image>./{ART}</image></game>'
            '<game id="other"><path>./unrelated.sh</path><name>Melkor 227 TV</name>'
            '<favorite>true</favorite><playcount>19</playcount></game></gameList>')
        return ports

    def theme(self, name="family", version="4"):
        theme = self.themes / name
        theme.mkdir()
        (theme / "theme.xml").write_text(f'<theme><formatVersion>{version}</formatVersion></theme>')
        return theme

    def test_fresh_validation_and_install(self):
        before = self.snapshot()
        self.assertEqual(self.menu.validate(), self.directory)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.cfg.exists())
        result = self.menu.install()
        self.assertEqual(set(result), {"system_dir", "launcher", "changed"})
        self.assertEqual(result["system_dir"], str(self.directory))
        self.assertEqual(result["launcher"], str(self.directory / LAUNCHER))
        self.assertEqual(set(result["changed"]), {str(self.cfg), str(self.directory / LAUNCHER),
                                                 str(self.directory / ART), str(self.directory / "gamelist.xml")})
        system = ET.parse(self.cfg).getroot().find("system")
        self.assertEqual(system.findtext("name"), "227")
        self.assertEqual(system.findtext("path"), str(self.directory))
        self.assertEqual(system.findtext("command"), "%ROM%")
        self.assertEqual(system.findtext("extension"), ".sh .SH")
        self.assertEqual(system.findtext("theme"), "227")
        game = ET.parse(self.directory / "gamelist.xml").getroot().find("game")
        self.assertEqual(game.findtext("name"), "227 TV")
        self.assertEqual(game.findtext("image"), "./images/melkor227.svg")
        self.assertEqual((self.directory / LAUNCHER).read_bytes(), (self.source / LAUNCHER).read_bytes())
        self.assertEqual(stat.S_IMODE((self.directory / LAUNCHER).stat().st_mode), 0o755)
        self.assertFalse((self.prefix / "ports").exists())
        self.assertFalse(list(self.root.rglob("*.bak-*")))

    def test_existing_ai_survives_install_update_uninstall(self):
        self.ai()
        original = self.snapshot()
        self.menu.install()
        games = self.directory / "gamelist.xml"
        root = ET.parse(games, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))).getroot()
        tv = root.findall("game")[1]
        ET.SubElement(tv, "playcount").text = "7"
        ET.SubElement(tv, "favorite").text = "true"
        ET.SubElement(tv, "lastplayed").text = "20261020T101010"
        ET.SubElement(tv, "custom", {"setting": "yes"}).text = "user metadata"
        tv.find("name").text = "outdated title"
        games.write_bytes(ET.tostring(root))
        games.chmod(0o640)
        self.menu.install()
        root = ET.parse(games).getroot()
        tv = root.findall("game")[1]
        self.assertEqual(tv.findtext("name"), "227 TV")
        self.assertEqual(tv.findtext("playcount"), "7")
        self.assertEqual(tv.findtext("favorite"), "true")
        self.assertEqual(tv.findtext("lastplayed"), "20261020T101010")
        self.assertEqual(tv.find("custom").attrib, {"setting": "yes"})
        self.assertEqual(stat.S_IMODE(games.stat().st_mode), 0o640)
        result = self.menu.uninstall()
        self.assertIn(str(self.directory / LAUNCHER), result["changed"])
        self.assertFalse((self.directory / LAUNCHER).exists())
        self.assertFalse((self.directory / ART).exists())
        ai = ET.parse(games).getroot().find("game")
        self.assertEqual(ai.attrib, {"id": "ai"})
        self.assertEqual(ai.findtext("playcount"), "42")
        self.assertEqual(ai.findtext("lastplayed"), "20261019T123456")
        self.assertEqual(ai.findtext("favorite"), "true")
        self.assertEqual(ai.find("custom").attrib, {"foo": "bar"})
        self.assertIn(b"<!-- AI stats stay -->", games.read_bytes())
        self.assertIsNotNone(ET.parse(games).getroot().find("folder"))
        for relative, contents in original.items():
            if relative != str(games.relative_to(self.root)):
                self.assertEqual(self.snapshot()[relative], contents, relative)
        before = self.snapshot()
        self.assertEqual(self.menu.uninstall()["changed"], [])
        self.assertEqual(self.snapshot(), before)

    def test_idempotent_no_rewrite_or_backup(self):
        self.ai()
        self.menu.install()
        before = self.snapshot()
        self.assertEqual(self.menu.install()["changed"], [])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(list(self.root.rglob("*.bak-*"))), 1)

    def test_no_rewrite_of_equivalent_xml(self):
        self.menu.install()
        games = self.directory / "gamelist.xml"
        games.write_text(games.read_text().replace("<game>", "\n  <game>\n"))
        before = self.snapshot()
        self.assertEqual(self.menu.install()["changed"], [])
        self.assertEqual(self.snapshot(), before)

    def test_last_app_uninstall_keeps_system_and_themes(self):
        theme = self.theme()
        self.menu.install()
        cfg = self.cfg.read_bytes()
        themedata = (theme / "227/theme.xml").read_bytes()
        self.menu.uninstall()
        self.assertEqual(self.cfg.read_bytes(), cfg)
        self.assertEqual((theme / "227/theme.xml").read_bytes(), themedata)
        self.assertEqual(ET.parse(self.directory / "gamelist.xml").getroot().findall("game"), [])
        self.assertTrue(self.directory.is_dir())

    def test_uninstall_without_install_or_source_is_noop(self):
        shutil.rmtree(self.source)
        before = self.snapshot()
        result = self.menu.uninstall()
        self.assertEqual(result["changed"], [])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.cfg.exists())
        self.assertFalse(self.directory.exists())

    def test_uninstall_installed_menu_without_source_or_app_directory(self):
        self.ai()
        self.menu.install()
        shutil.rmtree(self.source)
        shutil.rmtree(self.prefix / "tools/melkor227-tv")
        fallback = self.prefix / "tools" / LAUNCHER
        original_fallback = fallback.read_bytes()
        self.menu.uninstall()
        self.assertFalse((self.directory / LAUNCHER).exists())
        self.assertFalse((self.directory / ART).exists())
        self.assertEqual(fallback.read_bytes(), original_fallback)
        self.assertEqual(ET.parse(self.directory / "gamelist.xml").getroot().find("game").findtext("name"),
                         "227 AI")
        self.assertEqual(self.menu.uninstall()["changed"], [])

    def test_existing_custom_direct_child_and_exact_name_only(self):
        self.ai(self.prefix / "custom227")
        self.cfg.write_text(self.cfg.read_text().replace('<name>ports</name>', '<name>227 TV</name>'))
        original = self.cfg.read_bytes()
        self.assertEqual(self.menu.validate(), self.directory)
        self.menu.install()
        self.assertEqual(self.cfg.read_bytes(), original)

    def test_nonexact_227_does_not_prevent_fresh_system(self):
        self.cfg.write_text('<systemList><system><name>227tv</name><path>/other</path></system></systemList>')
        self.menu.install()
        self.assertEqual([node.findtext("name") for node in ET.parse(self.cfg).getroot()], ["227tv", "227"])
        backups = list(self.root.glob("es_systems.cfg.bak-*"))
        self.assertEqual(len(backups), 1)
        self.assertIn(b"/other", backups[0].read_bytes())

    def test_bad_xml_and_wrong_roots_fail_before_writes(self):
        for payload in ("<broken", "<gameList/>", "<!DOCTYPE systemList [<!ENTITY a 'x'>]><systemList/>"):
            with self.subTest(payload=payload):
                self.cfg.write_text(payload)
                self.assert_rejected((ValueError, ET.ParseError))

    def test_utf16_dtd_is_rejected_before_entity_expansion(self):
        self.cfg.write_bytes("<!DOCTYPE systemList [<!ENTITY a 'x'>]><systemList>&a;</systemList>".encode("utf-16"))
        self.assert_rejected()

    def test_document_header_footer_comments_and_processing_instructions_survive(self):
        self.cfg.write_text('<?xml version="1.0"?><!-- config header --><?notes preserve?>'
                            '<systemList><!-- inside --></systemList><!-- config footer -->')
        self.directory.mkdir()
        games = self.directory / "gamelist.xml"
        games.write_text('<!-- games header --><gameList/><!-- games footer -->')
        self.menu.install()
        for text in (b"<!-- config header -->", b"<?notes preserve?>", b"<!-- inside -->", b"<!-- config footer -->"):
            self.assertIn(text, self.cfg.read_bytes())
        self.menu.uninstall()
        for text in (b"<!-- games header -->", b"<!-- games footer -->"):
            self.assertIn(text, games.read_bytes())

    def test_bad_gamelists_fail_before_writes(self):
        for directory in (self.directory, self.prefix / "ports"):
            directory.mkdir(exist_ok=True)
            gamelist = directory / "gamelist.xml"
            for payload in ("<broken", "<systemList/>"):
                with self.subTest(directory=directory, payload=payload):
                    gamelist.write_text(payload)
                    self.assert_rejected((ValueError, ET.ParseError))
            gamelist.unlink()

    def test_duplicate_system_rejected(self):
        self.ai()
        text = self.cfg.read_text().replace("</systemList>",
            f'<system><name>227</name><path>{self.directory}</path></system></systemList>')
        self.cfg.write_text(text)
        self.assert_rejected()

    def test_unsafe_registered_paths_rejected(self):
        self.ai()
        original = self.cfg.read_text()
        for directory in (self.root / "other card/227ai", Path("227ai"), self.prefix,
                          self.prefix / "nested/227ai", self.prefix / "tools", self.prefix / "ports",
                          self.prefix / "../227ai"):
            with self.subTest(directory=directory):
                self.cfg.write_text(original.replace(str(self.directory) + "/", str(directory)))
                self.assert_rejected()

    def test_missing_or_ambiguous_system_path(self):
        for content in ("", "<path/>", f"<path>{self.directory}</path><path>{self.directory}</path>",
                        f"<name>other</name><path>{self.directory}</path>"):
            self.cfg.write_text(f"<systemList><system><name>227</name>{content}</system></systemList>")
            self.assert_rejected()

    def test_required_roots_and_relative_paths_rejected(self):
        for menu in (SystemMenu(self.source, self.root / "absent", self.cfg),
                     SystemMenu(self.source, self.prefix, self.root / "absent/es.cfg"),
                     SystemMenu(self.source, Path("relative"), self.cfg),
                     SystemMenu(self.source, self.prefix, Path("relative.cfg")),
                     SystemMenu(self.source, self.prefix, self.cfg, self.root / "missing-themes")):
            with self.subTest(menu=menu):
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    menu.install()
                self.assertEqual(self.snapshot(), before)

    def test_symlinks_rejected_before_writes(self):
        outside = self.root / "outside"
        outside.mkdir()
        for target in (self.cfg, self.directory, self.directory / LAUNCHER,
                       self.directory / "gamelist.xml", self.directory / "images",
                       self.directory / ART, self.prefix / "tools",
                       self.prefix / "tools/melkor227-tv",
                       self.prefix / "tools/melkor227-tv/app.py",
                       self.prefix / "ports", self.prefix / "ports" / LAUNCHER,
                       self.prefix / "ports/gamelist.xml"):
            with self.subTest(target=target):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(outside / "missing")
                self.assert_rejected()
                target.unlink()

    def test_symlink_prefix_cfg_parent_and_source_rejected(self):
        for target, replacement in ((self.prefix, "card-real"), (self.source, "source-real")):
            target.rename(self.root / replacement)
            target.symlink_to(self.root / replacement, target_is_directory=True)
            self.assert_rejected()
            target.unlink()
            (self.root / replacement).rename(target)
        link = self.root / "linked-parent"
        link.symlink_to(self.root, target_is_directory=True)
        self.menu.es_cfg = link / "es.cfg"
        self.assert_rejected()

    def test_hijacked_launcher_or_art_rejected(self):
        self.directory.mkdir()
        for target in (self.directory / LAUNCHER, self.directory / ART):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("unrelated file")
            self.assert_rejected()
            target.unlink()

    def test_hardlinked_launcher_rejected(self):
        self.directory.mkdir()
        outside = self.root / "other-app.sh"
        outside.write_bytes((self.source / LAUNCHER).read_bytes())
        (self.directory / LAUNCHER).hardlink_to(outside)
        self.assert_rejected()

    def test_overlapping_config_and_launcher_rejected(self):
        self.directory.mkdir()
        self.menu.es_cfg = self.directory / LAUNCHER
        self.menu.es_cfg.write_text('<systemList><!-- melkor227-tv --></systemList>')
        self.assert_rejected()

    def test_hijacked_entry_and_duplicates_rejected(self):
        self.directory.mkdir()
        game = f'<game><path>./{LAUNCHER}</path><name>Other app</name></game>'
        gamelist = self.directory / "gamelist.xml"
        gamelist.write_text(f"<gameList>{game}</gameList>")
        self.assert_rejected()
        gamelist.write_text(f"<gameList>{game}{game}</gameList>")
        self.assert_rejected()

    def test_missing_source_fails_before_writes(self):
        (self.source / "assets/melkor227.svg").unlink()
        self.assert_rejected()

    def test_fresh_themes_inherit_parent_versions_preserve_posters(self):
        old = self.theme("old", "4")
        new = self.theme("new", "9")
        (old / "227").mkdir()
        (old / "227/poster.png").write_bytes(b"do not overwrite")
        existing = self.theme("existing")
        (existing / "227").mkdir()
        (existing / "227/theme.xml").write_text("existing custom theme, never parse or replace")
        before = self.snapshot()
        self.menu.install()
        for theme, version in ((old, "4"), (new, "9")):
            root = ET.parse(theme / "227/theme.xml").getroot()
            self.assertEqual(root.findtext("formatVersion"), version)
            self.assertEqual(root.findtext("include"), "../theme.xml")
        after = self.snapshot()
        for path, contents in before.items():
            self.assertEqual(after[path], contents)
        self.assertEqual(self.menu.install()["changed"], [])

    def test_bad_theme_fails_before_any_menu_writes(self):
        theme = self.theme()
        for payload in ("<broken", "<theme><formatVersion>invalid</formatVersion></theme>"):
            (theme / "theme.xml").write_text(payload)
            self.assert_rejected((ValueError, ET.ParseError))

    def test_symlink_theme_target_or_parent_rejected(self):
        theme = self.theme()
        (theme / "227").symlink_to(self.root / "missing")
        self.assert_rejected()
        (theme / "227").unlink()
        (theme / "227").mkdir()
        (theme / "227/theme.xml").symlink_to(self.root / "missing")
        self.assert_rejected()

    def test_legacy_migration_removes_only_exact_owned_paths(self):
        self.ai()
        ports = self.legacy()
        original = (ports / "gamelist.xml").read_bytes()
        self.menu.install()
        self.assertFalse((ports / LAUNCHER).exists())
        self.assertFalse((ports / ART).exists())
        self.assertTrue((ports / "unrelated.sh").exists())
        remaining = ET.parse(ports / "gamelist.xml").getroot().findall("game")
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].attrib, {"id": "other"})
        self.assertEqual(remaining[0].findtext("name"), "Melkor 227 TV")
        self.assertEqual(remaining[0].findtext("playcount"), "19")
        self.assertIn(b"<!-- Ports comment -->", (ports / "gamelist.xml").read_bytes())
        self.assertEqual(next(ports.glob("gamelist.xml.bak-*")).read_bytes(), original)
        self.assertEqual(self.menu.install()["changed"], [])

    def test_legacy_unrelated_launcher_refused(self):
        self.legacy(owned=False)
        self.assert_rejected()

    def test_legacy_changed_script_hash_is_identified_by_app_text(self):
        ports = self.legacy()
        (ports / LAUNCHER).write_text('#!/bin/sh\nexec python3 ../tools/melkor227-tv/app.py\n')
        self.menu.install()
        self.assertFalse((ports / LAUNCHER).exists())

    def test_legacy_unrelated_art_is_not_deleted(self):
        ports = self.legacy()
        (ports / ART).write_bytes(b"another app's image")
        self.menu.install()
        self.assertFalse((ports / LAUNCHER).exists())
        self.assertEqual((ports / ART).read_bytes(), b"another app's image")

    def test_legacy_image_symlink_rejected_before_writes(self):
        ports = self.legacy()
        (ports / ART).unlink()
        (ports / ART).symlink_to(self.source / "assets/melkor227.svg")
        self.assert_rejected()

    def test_legacy_entry_without_owned_launcher_is_preserved(self):
        ports = self.legacy()
        (ports / LAUNCHER).unlink()
        before = {path.name: path.read_bytes() for path in ports.rglob("*") if path.is_file()}
        self.menu.install()
        after = {path.name: path.read_bytes() for path in ports.rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_legacy_shared_image_and_empty_gamelist_preserved(self):
        ports = self.legacy()
        gamelist = ports / "gamelist.xml"
        gamelist.write_text(gamelist.read_text().replace('<favorite>', f'<image>./{ART}</image><favorite>'))
        self.menu.install()
        self.assertTrue((ports / ART).exists())
        # A second legacy install containing only TV keeps an empty gameList.
        (ports / LAUNCHER).write_bytes((self.source / LAUNCHER).read_bytes())
        gamelist.write_text(f'<gameList><game><path>./{LAUNCHER}</path></game></gameList>')
        self.menu.install()
        self.assertEqual(ET.parse(gamelist).getroot().findall("game"), [])
        self.assertTrue(ports.is_dir())

    def test_uninstall_does_not_delete_art_used_by_other_apps(self):
        self.menu.install()
        gamelist = self.directory / "gamelist.xml"
        games = ET.parse(gamelist).getroot()
        other = ET.SubElement(games, "game")
        ET.SubElement(other, "path").text = "./other.sh"
        ET.SubElement(other, "image").text = "./" + ART.as_posix()
        gamelist.write_bytes(ET.tostring(games))
        self.menu.uninstall()
        self.assertTrue((self.directory / ART).exists())

    def test_changed_xml_backups_are_unique_and_preserve_original_bytes(self):
        self.ai()
        games = self.directory / "gamelist.xml"
        original = games.read_bytes()
        self.menu.install()
        self.menu.uninstall()
        self.menu.install()
        backups = list(self.directory.glob("gamelist.xml.bak-*"))
        self.assertEqual(len(backups), 3)
        self.assertIn(original, [path.read_bytes() for path in backups])

    def test_atomic_same_parent_fsync_and_no_es_restart(self):
        replacements = []
        replace = os.replace

        def tracked_replace(origin, destination):
            self.assertEqual(Path(origin).parent, Path(destination).parent)
            replacements.append(Path(destination))
            replace(origin, destination)

        with ExitStack() as stack:
            stack.enter_context(patch("subprocess.run", side_effect=AssertionError("No subprocesses")))
            stack.enter_context(patch("subprocess.Popen", side_effect=AssertionError("No subprocesses")))
            stack.enter_context(patch("os.system", side_effect=AssertionError("No shell")))
            stack.enter_context(patch.object(tv_system.os, "replace", side_effect=tracked_replace))
            fsync = stack.enter_context(patch.object(tv_system.os, "fsync", wraps=os.fsync))
            self.menu.install()
            self.assertEqual(len(replacements), 4)
            self.assertGreaterEqual(fsync.call_count, 8)
            self.menu.uninstall()
        self.assertFalse([path for path in self.root.rglob(".*") if path.is_file()])

    def test_atomic_failure_keeps_original_xml_and_cleans_temp(self):
        self.ai()
        gamelist = self.directory / "gamelist.xml"
        original = gamelist.read_bytes()
        before = self.transaction_snapshot()
        replace = os.replace

        def fail_xml(origin, target):
            if target == gamelist:
                raise OSError("simulated replace failure")
            replace(origin, target)

        with patch.object(tv_system.os, "replace", side_effect=fail_xml):
            with self.assertRaises(OSError):
                self.menu.install()
        self.assertEqual(gamelist.read_bytes(), original)
        self.assertEqual(self.transaction_snapshot(), before)
        self.assertFalse(list(self.directory.glob(".gamelist.xml-*")))

    def test_install_rolls_back_failure_after_each_planned_write(self):
        self.ai()
        # Include an ES registration change as well as AI metadata, legacy
        # migration, existing owned files, and a newly created theme include.
        self.cfg.write_text('<systemList><!-- keep --><system><name>ports</name></system></systemList>')
        theme = self.theme()
        (theme / "227").mkdir()
        (theme / "227/poster.png").write_bytes(b"unrelated poster")
        ports = self.legacy()
        (self.directory / "images").mkdir()
        (self.directory / LAUNCHER).write_bytes(b"#!/bin/sh\n# melkor227-tv old launcher\n")
        (self.directory / ART).write_bytes((self.source / "assets/melkor227.svg").read_bytes() + b"\n<!-- old -->")
        for path in (self.cfg, self.directory / LAUNCHER, self.directory / ART,
                     self.directory / "gamelist.xml", ports / LAUNCHER, ports / ART):
            path.chmod(0o640)
        before = self.transaction_snapshot()
        _, writes, _ = self.menu._prepare(install=True)
        write = tv_system._write
        for index, target in enumerate(writes):
            with self.subTest(target=target):
                failure = OSError(f"persistent write failure at {target}")
                failed = False

                def fail_write(path, *args):
                    nonlocal failed
                    if failed:
                        raise failure
                    write(path, *args)
                    if path == target:
                        failed = True
                        raise failure

                with patch.object(tv_system, "_write", side_effect=fail_write) as writer:
                    with self.assertRaises(OSError) as caught:
                        self.menu.install()
                self.assertIs(caught.exception, failure)
                self.assertEqual(writer.call_count, index + 1)
                self.assertEqual(self.transaction_snapshot(), before)
                self.assertFalse(list(self.root.rglob(".*-rollback-*")))
        self.assertTrue(list(self.root.rglob("*.bak-*")))

    def test_permanently_failing_writer_leaves_files_unchanged(self):
        self.ai()
        before = self.snapshot()
        failure = OSError("writer unavailable")
        with patch.object(tv_system, "_write", side_effect=failure) as writer:
            with self.assertRaises(OSError) as caught:
                self.menu.install()
        self.assertIs(caught.exception, failure)
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(self.snapshot(), before)

    def test_fresh_install_failure_restores_absence_of_menu_config_and_themes(self):
        first = self.theme("a-first")
        last = self.theme("z-last")
        before = self.transaction_snapshot()
        write = tv_system._write
        failure = OSError("last theme failed after replacement")

        def fail_theme(path, *args):
            write(path, *args)
            if path == last / "227/theme.xml":
                raise failure

        with patch.object(tv_system, "_write", side_effect=fail_theme):
            with self.assertRaises(OSError) as caught:
                self.menu.install()
        self.assertIs(caught.exception, failure)
        self.assertEqual(self.transaction_snapshot(), before)
        for path in (self.cfg, self.directory / LAUNCHER, self.directory / ART,
                     self.directory / "gamelist.xml", first / "227/theme.xml", last / "227/theme.xml"):
            self.assertFalse(path.exists(), path)

    def test_install_and_uninstall_roll_back_failure_after_each_removal(self):
        self.ai()
        self.menu.install()
        ports = self.legacy()
        for path in (self.directory / LAUNCHER, self.directory / ART, ports / LAUNCHER, ports / ART):
            path.chmod(0o750)
        before = self.transaction_snapshot()
        unlink = Path.unlink
        for install in (True, False):
            _, _, removes = self.menu._prepare(install)
            for target in removes:
                with self.subTest(install=install, target=target):
                    failure = OSError(f"removal failed after deleting {target}")

                    def fail_remove(path, *args, **kwargs):
                        result = unlink(path, *args, **kwargs)
                        if path == target and not kwargs.get("missing_ok", False):
                            raise failure
                        return result

                    with patch.object(Path, "unlink", new=fail_remove):
                        with self.assertRaises(OSError) as caught:
                            (self.menu.install if install else self.menu.uninstall)()
                    self.assertIs(caught.exception, failure)
                    self.assertEqual(self.transaction_snapshot(), before)

    def test_chmod_failure_restores_original_mode(self):
        self.menu.install()
        launcher = self.directory / LAUNCHER
        launcher.chmod(0o700)
        before = self.transaction_snapshot()
        chmod = Path.chmod
        failure = OSError("chmod failed after changing mode")

        def fail_chmod(path, mode, *args, **kwargs):
            chmod(path, mode, *args, **kwargs)
            if path == launcher:
                raise failure

        with patch.object(Path, "chmod", new=fail_chmod):
            with self.assertRaises(OSError) as caught:
                self.menu.install()
        self.assertIs(caught.exception, failure)
        self.assertEqual(self.transaction_snapshot(), before)

    def test_rollback_errors_report_paths_and_continue_under_lock(self):
        self.ai()
        original_games = (self.directory / "gamelist.xml").read_bytes()
        write = tv_system._write
        restore = tv_system._restore
        launcher = self.directory / LAUNCHER
        art = self.directory / ART
        failure = OSError("initial write failed")
        recovered = []

        def fail_write(path, *args):
            write(path, *args)
            if path == self.directory / "gamelist.xml":
                raise failure

        def fail_restore(path, original):
            # A second descriptor cannot acquire the lock during recovery.
            with tv_system.LOCK.open("r+b") as lock:
                with self.assertRaises(BlockingIOError):
                    tv_system.fcntl.flock(lock.fileno(), tv_system.fcntl.LOCK_EX | tv_system.fcntl.LOCK_NB)
            recovered.append(path)
            if path in (launcher, art):
                raise OSError("recovery storage unavailable")
            restore(path, original)

        with patch.object(tv_system, "_write", side_effect=fail_write), \
                patch.object(tv_system, "_restore", side_effect=fail_restore):
            with self.assertRaises(OSError) as caught:
                self.menu.install()
        self.assertIs(caught.exception, failure)
        self.assertIsInstance(caught.exception.__cause__, RuntimeError)
        report = str(caught.exception.__cause__)
        self.assertIn("Menu rollback incomplete", report)
        for path in (launcher, art):
            self.assertIn(str(path), report)
            self.assertIn(path, recovered)
        self.assertIn("recovery storage unavailable", report)
        self.assertEqual((self.directory / "gamelist.xml").read_bytes(), original_games)

    def test_install_and_uninstall_use_exclusive_shared_lock(self):
        calls = []
        flock = tv_system.fcntl.flock

        def tracked_flock(descriptor, operation):
            calls.append(operation)
            return flock(descriptor, operation)

        with patch.object(tv_system.fcntl, "flock", side_effect=tracked_flock):
            self.menu.validate()
            self.assertEqual(calls, [])
            self.menu.install()
            self.menu.uninstall()
        self.assertEqual(calls, [tv_system.fcntl.LOCK_EX, tv_system.fcntl.LOCK_EX])

    def test_symlink_lock_rejected(self):
        tv_system.LOCK.symlink_to(self.root / "missing")
        before = self.snapshot()
        with self.assertRaises(OSError):
            self.menu.install()
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()

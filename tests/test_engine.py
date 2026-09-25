"""Schema and edit-log checks. Run from the tool root: python -m unittest tests.test_engine"""

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from backend.changelog import EditLog
from backend.demo_data import demo_paths, ensure_demo_prefabs
from backend.indexer import AssetIndexer
from backend.repair import apply_safe_fixes
from backend.validator import PlayfieldValidator
from backend.yaml_engine import PlayfieldAST


ROOT = Path(__file__).resolve().parent.parent


def _types(issues):
    return {issue["type"] for issue in issues}


class EngineTests(unittest.TestCase):
    def setUp(self):
        ensure_demo_prefabs()
        self.paths = demo_paths()
        self.indexer = AssetIndexer(
            self.paths["scenario_playfields"],
            self.paths["scenario_prefabs"],
            self.paths["game_playfields"],
            self.paths["game_prefabs"],
        )
        self.indexer.build()

    def test_scan_does_not_touch_bytes(self):
        static = ROOT / "demo" / "Scenario" / "Playfields" / "Akua" / "playfield_static.yaml"
        dup = ROOT / "demo" / "Scenario" / "Playfields" / "DupKey" / "playfield.yaml"
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (static, dup)}
        for path in before:
            PlayfieldAST(str(path))
            PlayfieldValidator(PlayfieldAST(str(path)).data or {}, self.indexer, str(path)).validate()
        for path, digest in before.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def test_index_reads_group_from_epb_header(self):
        self.assertEqual(self.indexer.known_group("JunkT1"), "JunkT1")
        self.assertEqual(self.indexer.known_group("junkt1"), "JunkT1")
        self.assertIsNone(self.indexer.known_group("JunkT1Typo"))
        self.assertEqual(self.indexer.known_prefab("BA_Outpost"), "BA_Outpost")
        self.assertIsNone(self.indexer.known_prefab("BA_DoesNotExist"))
        self.assertIsNotNone(self.indexer.known_compound("CompoundJunkYard"))

    def test_akua_finds_real_problems_and_keeps_legal_keys(self):
        path = ROOT / "demo" / "Scenario" / "Playfields" / "Akua" / "playfield_static.yaml"
        ast = PlayfieldAST(str(path))
        text = path.read_text(encoding="utf-8")
        for needle in ("DronesMinMax", "TroopTransport", "SpawnPOINearRange", "Compound:", "WeirdKey"):
            self.assertIn(needle, text)
        issues = PlayfieldValidator(ast.data, self.indexer, str(path)).validate()
        kinds = _types(issues)
        self.assertIn("missing_usefixed", kinds)
        self.assertIn("missing_group", kinds)
        self.assertIn("missing_prefab", kinds)
        self.assertIn("invalid_biome", kinds)
        self.assertIn("unknown_key", kinds)
        self.assertIn("broken_spawn_ref", kinds)
        self.assertIn("spawn_order", kinds)
        self.assertIn("orphan_drone_setup", kinds)
        self.assertIn("bad_spawn_resource", kinds)
        self.assertIn("missing_compound_name", kinds)
        # Volcanic comes from the base-game playfield. Desert is local. Neither is an error.
        messages = " ".join(issue["message"] for issue in issues)
        self.assertNotIn("Volcanic", messages)
        self.assertNotIn("'Desert'", messages)
        self.assertTrue(any(issue.get("lookup") == "junkt1" for issue in issues))
        self.assertTrue(any("NotARealBiome" in issue["message"] or "NotARealBiome" in str(issue.get("bad_biomes")) for issue in issues))

    def test_safe_fix_is_limited_and_logged(self):
        src = ROOT / "demo" / "Scenario" / "Playfields" / "Akua"
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "Akua"
            shutil.copytree(src, folder)
            # Sibling dynamic yaml is required for biomes. Copy it too — copytree already did if src is the folder.
            static = folder / "playfield_static.yaml"
            log = EditLog(Path(tmp))
            ast = PlayfieldAST(str(static), log)
            issues = PlayfieldValidator(ast.data, self.indexer, str(static)).validate()
            summary = apply_safe_fixes(ast, issues, self.indexer, log)
            ast.load()
            text = static.read_text(encoding="utf-8")
            self.assertIn("UseFixed: true", text)
            self.assertIn("GroupName: JunkT1", text)
            self.assertNotIn("GroupName: junkt1", text)
            self.assertIn("JunkT1Typo", text)
            self.assertIn("WeirdKey", text)
            self.assertIn("BA_DoesNotExist", text)
            self.assertTrue(any("junkt1" in line or "JunkT1" in line for line in summary["repaired"]))
            self.assertTrue(any("skipped" in line or "pick" in line or "left" in line for line in summary["skipped"]))
            edits = (Path(tmp) / "logs" / "epd-edits.log").read_text(encoding="utf-8")
            self.assertIn("set-use-fixed", edits)
            self.assertIn("correct-biome", edits)
            self.assertIn("playfield_static.yaml", edits)

    def test_duplicate_key_is_reported_until_an_edit(self):
        path = ROOT / "demo" / "Scenario" / "Playfields" / "DupKey" / "playfield.yaml"
        ast = PlayfieldAST(str(path))
        self.assertIsNotNone(ast.duplicate_key_info)
        self.assertIsNone(ast.data)


if __name__ == "__main__":
    unittest.main()

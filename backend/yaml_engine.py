"""Load and edit playfield YAML.

Opening a file is read-only. Nothing here rewrites the playfield until an
edit method is called, and every edit is appended to the session log.
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.constructor import DuplicateKeyError

from backend.schema import RANDOM_POI_KEYS

yaml = YAML()
yaml.preserve_quotes = True
yaml.indent(mapping=2, sequence=4, offset=2)
yaml.width = 4096


class PlayfieldAST:
    def __init__(self, file_path: str, log=None):
        self.path = Path(file_path)
        self.log = log
        self.data = None
        self.parse_error = None
        self.duplicate_key_info = None
        self.load()

    def load(self):
        self.parse_error = None
        self.duplicate_key_info = None
        self.data = None
        try:
            with open(self.path, "r", encoding="utf-8-sig") as handle:
                self.data = yaml.load(handle)
        except DuplicateKeyError as exc:
            self.parse_error = str(exc)
            key_match = re.search(r'duplicate key "([^"]+)"', str(exc))
            line_match = re.search(r"line (\d+)", str(exc))
            self.duplicate_key_info = {
                "key": key_match.group(1) if key_match else "",
                "line": int(line_match.group(1)) if line_match else -1,
                "error": str(exc),
            }
        except Exception as exc:
            self.parse_error = str(exc)

    def backup(self) -> str:
        backup_dir = self.path.parent / ".epd_backups"
        backup_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        dest = backup_dir / f"{self.path.name}_{stamp}.bak"
        shutil.copy2(self.path, dest)
        return str(dest)

    def save_atomic(self):
        backup = self.backup()
        with open(self.path, "w", encoding="utf-8", newline="\n") as handle:
            yaml.dump(self.data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        return backup

    def _log(self, action: str, *, target: str = "", reason: str = "", before: str = "", after: str = ""):
        if self.log:
            self.log.record(
                action,
                file=str(self.path),
                target=target,
                reason=reason,
                before=before,
                after=after,
            )

    def remove_duplicate_key_line(self, line_number: int, key_name: str) -> bool:
        """Comment out the duplicate key. Prefers the reported line, then the
        next occurrence — never the earlier (original) key."""
        if not self.path.exists() or line_number <= 0:
            return False
        original = self.path.read_text(encoding="utf-8-sig")
        lines = original.splitlines(keepends=True)
        key_re = re.compile(rf"^\s*{re.escape(key_name)}\s*:", re.IGNORECASE) if key_name else None

        def is_key(line: str) -> bool:
            return bool(key_re and key_re.search(line))

        target = line_number - 1
        chosen = None
        if 0 <= target < len(lines) and (not key_name or is_key(lines[target]) or key_name in lines[target]):
            chosen = target
        if chosen is None and key_name:
            for idx in range(max(0, target), min(len(lines), target + 6)):
                if is_key(lines[idx]):
                    chosen = idx
                    break
        if chosen is None:
            return False

        raw = lines[chosen].rstrip("\r\n")
        lines[chosen] = f"# [EPD removed duplicate]: {raw}\n"
        self.backup()
        self.path.write_text("".join(lines), encoding="utf-8", newline="")
        self._log(
            "comment-duplicate-key",
            target=f"line {chosen + 1}",
            reason=f"Duplicate YAML key '{key_name}' — YamlDotNet will not load the playfield.",
            before=raw,
            after=lines[chosen].rstrip("\r\n"),
        )
        self.load()
        return True

    def auto_resolve_all_duplicate_keys(self) -> int:
        resolved = 0
        for _ in range(25):
            if not self.duplicate_key_info:
                break
            info = self.duplicate_key_info
            if not self.remove_duplicate_key_line(info["line"], info["key"]):
                break
            resolved += 1
        return resolved

    def get_container(self, source: str):
        if not isinstance(self.data, dict):
            return None
        if source == "Fixed":
            pois = self.data.get("POIs")
            return pois.get("Fixed") if isinstance(pois, dict) else None
        if source == "Random":
            pois = self.data.get("POIs")
            return pois.get("Random") if isinstance(pois, dict) else None
        if source == "DroneBaseSetup":
            block = self.data.get("DroneBaseSetup")
            return block.get("Random") if isinstance(block, dict) else None
        return None

    def _poi(self, source: str, index: int):
        container = self.get_container(source)
        if not isinstance(container, list) or not (0 <= index < len(container)):
            return None
        item = container[index]
        return item if isinstance(item, dict) else None

    def correct_biome(self, source: str, index: int, new_biome: str, bad_biomes=None) -> bool:
        item = self._poi(source, index)
        if item is None:
            return False
        bad = {str(b).lower() for b in (bad_biomes or [])}
        current = item.get("Biome", [])
        before = repr(current)
        if isinstance(current, list):
            updated = []
            replaced = False
            for entry in current:
                if str(entry).lower() in bad:
                    if not replaced:
                        updated.append(new_biome)
                        replaced = True
                else:
                    updated.append(entry)
            if not updated:
                updated = [new_biome]
            item["Biome"] = updated
        else:
            item["Biome"] = [new_biome]
        self.save_atomic()
        self._log(
            "correct-biome",
            target=f"{source}[{index}].Biome",
            reason="Biome filter did not match a BiomeClusterData name on this playfield.",
            before=before,
            after=repr(item.get("Biome")),
        )
        self.load()
        return True

    def replace_target(self, source: str, index: int, new_value: str, is_compound: bool = False) -> bool:
        item = self._poi(source, index)
        if item is None:
            return False
        if is_compound and isinstance(item.get("Compound"), dict):
            before = repr(item["Compound"].get("Name"))
            item["Compound"]["Name"] = new_value
            field = "Compound.Name"
            after = repr(item["Compound"].get("Name"))
        elif is_compound or "CompoundPOI" in item:
            before = str(item.get("CompoundPOI", ""))
            item["CompoundPOI"] = new_value
            field = "CompoundPOI"
            after = new_value
        elif source == "Random":
            before = str(item.get("GroupName", ""))
            item["GroupName"] = new_value
            field = "GroupName"
            after = new_value
        else:
            before = str(item.get("Prefab", ""))
            item["Prefab"] = new_value
            field = "Prefab"
            after = new_value
        self.save_atomic()
        self._log(
            "replace-field",
            target=f"{source}[{index}].{field}",
            reason="Replaced from the issue card.",
            before=before,
            after=after,
        )
        self.load()
        return True

    def remove_target(self, source: str, index: int) -> bool:
        container = self.get_container(source)
        if not isinstance(container, list) or not (0 <= index < len(container)):
            return False
        removed = container[index]
        before = repr(removed)[:500]
        del container[index]
        self.save_atomic()
        self._log(
            "remove-entry",
            target=f"{source}[{index}]",
            reason="Removed from the issue card.",
            before=before,
            after="(deleted)",
        )
        self.load()
        return True

    def strip_key(self, source: str, index: int, key_name: str) -> bool:
        item = self._poi(source, index)
        if item is None or not key_name:
            return False
        real = next((k for k in list(item.keys()) if str(k).lower() == key_name.lower()), None)
        if real is None:
            return False
        before = f"{real}: {item.get(real)!r}"[:500]
        del item[real]
        self.save_atomic()
        self._log(
            "strip-unknown-key",
            target=f"{source}[{index}].{real}",
            reason="Key is not on the Empyrion POI schema. YamlDotNet can refuse to load the playfield.",
            before=before,
            after="(removed)",
        )
        self.load()
        return True

    def set_use_fixed(self) -> bool:
        if not isinstance(self.data, dict):
            return False
        before = repr(self.data.get("UseFixed", None))
        if hasattr(self.data, "insert") and "UseFixed" not in self.data:
            self.data.insert(0, "UseFixed", True)
        else:
            self.data["UseFixed"] = True
        self.save_atomic()
        self._log(
            "set-use-fixed",
            target="UseFixed",
            reason="Fixed POIs do not spawn in Survival unless UseFixed is true.",
            before=before,
            after="True",
        )
        self.load()
        return True


def unknown_random_keys(item: dict) -> list[str]:
    if not isinstance(item, dict):
        return []
    return [str(k) for k in item.keys() if str(k).lower().strip() not in RANDOM_POI_KEYS]

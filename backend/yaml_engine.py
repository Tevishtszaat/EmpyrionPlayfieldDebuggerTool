import os
import re
import random
import shutil
from datetime import datetime
from pathlib import Path
from ruamel.yaml import YAML
from ruamel.yaml.constructor import DuplicateKeyError

yaml = YAML()
yaml.preserve_quotes = True
yaml.indent(mapping=2, sequence=4, offset=2)

class PlayfieldAST:
    def __init__(self, file_path: str):
        self.path = Path(file_path)
        self.data = None
        self.parse_error = None
        self.duplicate_key_info = None
        self.description_fixed = False
        self.sanitize_description_block()
        self.load()

    def sanitize_description_block(self) -> bool:
        if not self.path.exists():
            return False

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                content = f.read()

            desc_pattern = re.compile(r'(^[ \t]*Description:\s*")([^"]*?)("\s*$)', re.MULTILINE | re.DOTALL)
            
            def flatten_match(match):
                prefix = match.group(1)
                body = match.group(2)
                suffix = match.group(3)

                if '\n' in body:
                    self.description_fixed = True
                    lines = [line.strip() for line in body.splitlines()]
                    cleaned_lines = []
                    for line in lines:
                        if line.startswith('\\n'):
                            line = line[2:].strip()
                        cleaned_lines.append(line)
                    flattened = "\\n".join([c for c in cleaned_lines if c != ""])
                    return f'{prefix}{flattened}{suffix}'
                return match.group(0)

            new_content = desc_pattern.sub(flatten_match, content)

            if self.description_fixed and new_content != content:
                self.backup()
                with open(self.path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                    f.flush()
                    os.fsync(f.fileno())
                return True
        except Exception:
            pass
        return False

    def load(self):
        self.parse_error = None
        self.duplicate_key_info = None
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = yaml.load(f)
        except DuplicateKeyError as e:
            self.parse_error = str(e)
            key_match = re.search(r'duplicate key "([^"]+)"', str(e))
            line_match = re.search(r'line (\d+)', str(e))
            key_name = key_match.group(1) if key_match else ""
            line_num = int(line_match.group(1)) if line_match else -1
            self.duplicate_key_info = {
                "key": key_name,
                "line": line_num,
                "error": str(e)
            }
        except Exception as e:
            self.parse_error = str(e)

    def backup(self):
        backup_dir = self.path.parent / ".epd_backups"
        backup_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"{self.path.name}_{timestamp}.bak"
        shutil.copy2(self.path, backup_path)
        return str(backup_path)

    def save_atomic(self):
        self.backup()
        with open(self.path, "w", encoding="utf-8") as f:
            yaml.dump(self.data, f)
            f.flush()
            os.fsync(f.fileno())

    def remove_duplicate_key_line(self, line_number: int, key_name: str) -> bool:
        if not self.path.exists() or line_number <= 0:
            return False

        self.backup()
        with open(self.path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        target_idx = line_number - 1
        removed = False

        search_range = range(max(0, target_idx - 2), min(len(lines), target_idx + 3))
        for idx in search_range:
            if key_name and key_name in lines[idx]:
                lines[idx] = f"# [EPD Auto-Removed Duplicate]: {lines[idx]}"
                removed = True
                break

        if not removed and 0 <= target_idx < len(lines):
            lines[target_idx] = f"# [EPD Auto-Removed Duplicate]: {lines[target_idx]}"
            removed = True

        if removed:
            with open(self.path, "w", encoding="utf-8") as f:
                f.writelines(lines)
                f.flush()
                os.fsync(f.fileno())
            self.load()
            return True

        return False

    def get_container(self, source: str):
        if not self.data or not isinstance(self.data, dict):
            return None
        if source == "Objects":
            return self.data.get("Objects", [])
        elif source == "Fixed":
            return self.data.get("POIs", {}).get("Fixed", [])
        elif source == "DroneSpawns":
            return self.data.get("DroneSpawns", [])
        else:
            pois = self.data.get("POIs", {}).get("Random", [])
            if not pois and "POIs" in self.data and "Fixed" in self.data["POIs"]:
                pois = self.data["POIs"]["Fixed"]
            return pois

    def correct_biome(self, source: str, index: int, new_biome: str):
        container = self.get_container(source)
        if container is not None and 0 <= index < len(container):
            item = container[index]
            if isinstance(item, dict):
                # Replace bad biome with single valid biome or list
                item["Biome"] = [new_biome]
                self.save_atomic()
                return True
        return False

    def replace_target(self, source: str, index: int, new_value: str, is_compound: bool = False):
        container = self.get_container(source)
        if container is not None and 0 <= index < len(container):
            item = container[index]
            if isinstance(item, dict):
                if is_compound or "CompoundPOI" in item:
                    item["CompoundPOI"] = new_value
                    if "GroupName" in item:
                        item["GroupName"] = new_value
                else:
                    item["Prefab"] = new_value
                    if "GroupName" in item:
                        item["GroupName"] = new_value

                self.save_atomic()
                return True
        return False

    def remove_target(self, source: str, index: int):
        container = self.get_container(source)
        if container is not None and 0 <= index < len(container):
            del container[index]
            self.save_atomic()
            return True
        return False

    def autocomplete_all_issues(self, issues, indexer):
        self.sanitize_description_block()

        if not issues:
            return {"repaired": 0, "pruned": 0}

        repaired = 0
        pruned = 0
        sorted_issues = sorted(issues, key=lambda x: x.get("index", -1), reverse=True)

        for issue in sorted_issues:
            source = issue.get("source", "Random")
            idx = issue.get("index", -1)
            suggestions = issue.get("suggestions", [])
            itype = issue.get("type")

            # 1. Biome Correction
            if itype == "invalid_biome":
                if suggestions:
                    self.correct_biome(source, idx, suggestions[0])
                    repaired += 1
                continue

            # 2. Compound POI / Prefab Replacement
            is_compound = (itype == "missing_compound_poi")
            if suggestions:
                chosen = random.choice(suggestions)
                if self.replace_target(source, idx, chosen, is_compound):
                    repaired += 1
            else:
                if self.remove_target(source, idx):
                    pruned += 1

        self.save_atomic()
        return {"repaired": repaired, "pruned": pruned}

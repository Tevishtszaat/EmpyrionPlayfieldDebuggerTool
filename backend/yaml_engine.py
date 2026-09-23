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
        # 1. Sanitize all Description formats (quoted or unquoted multi-line) into single "..." line
        self.sanitize_description_block()
        # 2. Load AST
        self.load()

    def sanitize_description_block(self) -> bool:
        """Finds ANY multi-line Description (quoted or unquoted with indentation) and collapses it into a single line wrapped in double quotes."""
        if not self.path.exists():
            return False

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            modified = False
            new_lines = []
            i = 0
            n = len(lines)

            while i < n:
                line = lines[i]
                # Match start of a Description line (e.g., 'Description:' or '  Description:')
                match = re.match(r'^([ \t]*Description:)\s*(.*)$', line)
                if match:
                    prefix = match.group(1) # 'Description:'
                    first_val = match.group(2).rstrip('\r\n')

                    desc_parts = []
                    # Check if it starts with an open quote
                    starts_with_quote = first_val.startswith('"') or first_val.startswith("'")
                    quote_char = first_val[0] if starts_with_quote else None

                    # If it starts with quote and also closes on the same line (and not empty)
                    if starts_with_quote and len(first_val) > 1 and first_val.endswith(quote_char) and not first_val.endswith('\\' + quote_char):
                        # Already on a single quoted line! Clean up if needed
                        new_lines.append(line)
                        i += 1
                        continue

                    # Multi-line detected (either unquoted indented, or unclosed quote spanning multiple lines)
                    if starts_with_quote:
                        desc_parts.append(first_val[1:].rstrip(quote_char))
                    elif first_val:
                        desc_parts.append(first_val)

                    # Gather continuation lines
                    j = i + 1
                    while j < n:
                        next_line = lines[j]
                        # Stop if we hit another top-level or sibling YAML key (e.g., 'PlanetType:', 'Gravity:', 'POIs:')
                        if re.match(r'^[ \t]*[A-Za-z0-9_-]+:', next_line) and not next_line.strip().startswith("-"):
                            break
                        # Stop if unquoted line is completely unindented
                        if not starts_with_quote and next_line.strip() and not next_line.startswith(" ") and not next_line.startswith("\t"):
                            break

                        stripped = next_line.strip()
                        if starts_with_quote and quote_char in stripped:
                            # Reached the closing quote
                            part = stripped.split(quote_char)[0].strip()
                            if part:
                                desc_parts.append(part)
                            j += 1
                            break
                        else:
                            if stripped:
                                # Clean up leading literal \n or stray hyphens
                                if stripped.startswith('\\n'):
                                    stripped = stripped[2:].strip()
                                desc_parts.append(stripped)
                        j += 1

                    # Combine all sentence parts cleanly into ONE single line
                    # Join with single space, preserving punctuation and literal \n where intended
                    full_text = " ".join([p for p in desc_parts if p])
                    # Clean double spaces
                    full_text = re.sub(r'[ \t]+', ' ', full_text).strip()
                    # Escape internal double quotes so it's strictly valid YAML
                    clean_inner = full_text.replace('"', '\\"')

                    # Produce guaranteed single line enclosed in double quotes: Description: "..."
                    sanitized_line = f'{prefix} "{clean_inner}"\n'
                    new_lines.append(sanitized_line)
                    modified = True
                    self.description_fixed = True
                    i = j
                    continue
                else:
                    new_lines.append(line)
                    i += 1

            if modified:
                self.backup()
                with open(self.path, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)
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

            if itype == "invalid_biome":
                if suggestions:
                    self.correct_biome(source, idx, suggestions[0])
                    repaired += 1
                continue

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

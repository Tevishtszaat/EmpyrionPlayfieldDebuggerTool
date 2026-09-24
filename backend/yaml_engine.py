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
yaml.width = 100000

def enforce_strict_single_lines_on_disk(file_path: Path) -> bool:
    """Guarantees Description, Biome, and Value statements stay on ONE SINGLE LINE."""
    if not file_path.exists():
        return False

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        modified = False
        new_lines = []
        i = 0
        n = len(lines)

        while i < n:
            line = lines[i]

            # 1. Description: ...
            desc_match = re.match(r'^([ \t]*Description:)\s*(.*)$', line)
            if desc_match:
                prefix = desc_match.group(1)
                first_val = desc_match.group(2).rstrip('\r\n')
                starts_quote = first_val.startswith('"') or first_val.startswith("'")
                qchar = first_val[0] if starts_quote else None

                # Single-line check (respecting escaped quotes)
                if starts_quote and len(first_val) > 1 and first_val.endswith(qchar):
                    escaped = False
                    for c in reversed(first_val[:-1]):
                        if c == '\\':
                            escaped = not escaped
                        else:
                            break
                    if not escaped:
                        new_lines.append(line)
                        i += 1
                        continue

                parts = [first_val.lstrip('"\'').rstrip('"\'')] if first_val else []
                j = i + 1
                while j < n:
                    next_line = lines[j]
                    if re.match(r'^[ \t]*[A-Za-z0-9_-]+:', next_line) and not next_line.strip().startswith("-"):
                        break
                    stripped = next_line.strip()
                    if starts_quote and qchar in stripped:
                        # Find unescaped quote delimiter
                        idx_q = -1
                        for idx_c, char in enumerate(stripped):
                            if char == qchar:
                                num_slashes = 0
                                k = idx_c - 1
                                while k >= 0 and stripped[k] == '\\':
                                    num_slashes += 1
                                    k -= 1
                                if num_slashes % 2 == 0:
                                    idx_q = idx_c
                                    break
                        if idx_q != -1:
                            p = stripped[:idx_q].strip()
                            if p:
                                parts.append(p)
                            j += 1
                            break
                        else:
                            parts.append(stripped)
                    else:
                        if stripped:
                            parts.append(stripped)
                    j += 1

                merged = " ".join([p for p in parts if p])
                merged = re.sub(r'[ \t]+', ' ', merged).strip().replace('"', '\\"')
                new_lines.append(f'{prefix} "{merged}"\n')
                modified = True
                i = j
                continue

            # 2. Biome: [ ... ] multi-line wrap
            biome_match = re.match(r'^([ \t]*Biome:[ \t]*\[)(.*)$', line)
            if biome_match and not line.rstrip().endswith("]"):
                prefix = biome_match.group(1)
                first_val = biome_match.group(2).rstrip('\r\n')
                parts = [first_val.strip()] if first_val.strip() else []

                j = i + 1
                while j < n:
                    next_line = lines[j]
                    stripped = next_line.strip()
                    if "]" in stripped:
                        before_bracket = stripped.split("]")[0].strip()
                        if before_bracket:
                            parts.append(before_bracket)
                        j += 1
                        break
                    else:
                        if stripped:
                            parts.append(stripped)
                    j += 1

                combined_biomes = " ".join(parts)
                tokens = [t.strip().strip(',').strip() for t in combined_biomes.split(',') if t.strip()]
                clean_biome_str = ", ".join(tokens)
                new_lines.append(f'{prefix}{clean_biome_str}]\n')
                modified = True
                i = j
                continue

            # 3. Value: ... multi-line wrap
            value_match = re.match(r'^([ \t]*Value:)\s*(.*)$', line)
            if value_match:
                prefix = value_match.group(1)
                first_val = value_match.group(2).rstrip('\r\n')

                j = i + 1
                is_wrapped = False
                parts = [first_val.strip()] if first_val.strip() else []

                while j < n:
                    next_line = lines[j]
                    if re.match(r'^[ \t]*[A-Za-z0-9_-]+:', next_line) or next_line.strip().startswith("-"):
                        break
                    if next_line.startswith(" ") or next_line.startswith("\t"):
                        stripped = next_line.strip()
                        if stripped:
                            parts.append(stripped)
                            is_wrapped = True
                        j += 1
                    else:
                        break

                if is_wrapped:
                    combined_val = " ".join(parts)
                    combined_val = re.sub(r'[ \t]*,[ \t]*', ', ', combined_val)
                    combined_val = re.sub(r'[ \t]+', ' ', combined_val).strip()
                    new_lines.append(f'{prefix} {combined_val}\n')
                    modified = True
                    i = j
                    continue
                else:
                    new_lines.append(line)
                    i += 1
                    continue

            new_lines.append(line)
            i += 1

        if modified:
            with open(file_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
                f.flush()
                os.fsync(f.fileno())
            return True
    except Exception:
        pass
    return False

class PlayfieldAST:
    def __init__(self, file_path: str):
        self.path = Path(file_path)
        self.data = None
        self.parse_error = None
        self.duplicate_key_info = None
        self.sanitize_lines()
        self.load()

    def sanitize_lines(self) -> bool:
        return enforce_strict_single_lines_on_disk(self.path)

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
        enforce_strict_single_lines_on_disk(self.path)

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
        """Strict container resolution: Random never mutates Fixed, and vice versa."""
        if not self.data or not isinstance(self.data, dict):
            return None
        if source == "Objects":
            return self.data.get("Objects", [])
        elif source == "Fixed":
            pois = self.data.get("POIs", {})
            return pois.get("Fixed", []) if isinstance(pois, dict) else []
        elif source == "Random":
            pois = self.data.get("POIs", {})
            return pois.get("Random", []) if isinstance(pois, dict) else []
        elif source == "DroneSpawns":
            return self.data.get("DroneSpawns", [])
        return None

    def correct_biome(self, source: str, index: int, new_biome: str, bad_biomes=None, save_immediately=True):
        """Replaces only invalid biomes within a multi-biome array, preserving valid ones."""
        container = self.get_container(source)
        if container is not None and 0 <= index < len(container):
            item = container[index]
            if isinstance(item, dict):
                current = item.get("Biome", [])
                if isinstance(current, list):
                    bad_set = {b.lower() for b in (bad_biomes or [])}
                    updated = []
                    replaced = False
                    for b in current:
                        if str(b).lower() in bad_set:
                            if not replaced:
                                updated.append(new_biome)
                                replaced = True
                        else:
                            updated.append(b)
                    if not updated:
                        updated = [new_biome]
                    item["Biome"] = updated
                else:
                    item["Biome"] = [new_biome]

                if save_immediately:
                    self.save_atomic()
                return True
        return False

    def replace_target(self, source: str, index: int, new_value: str, is_compound: bool = False, save_immediately=True):
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

                if save_immediately:
                    self.save_atomic()
                return True
        return False

    def remove_target(self, source: str, index: int, save_immediately=True):
        container = self.get_container(source)
        if container is not None and 0 <= index < len(container):
            del container[index]
            if save_immediately:
                self.save_atomic()
            return True
        return False

    def autocomplete_all_issues(self, issues, indexer):
        """Batch-processes mutations by source container to prevent index corruption and I/O thrashing."""
        self.sanitize_lines()

        if not issues:
            return {"repaired": 0, "pruned": 0}

        repaired = 0
        pruned = 0

        # Group issues by source container to isolate array shift operations
        grouped = {}
        for issue in issues:
            src = issue.get("source", "Random")
            grouped.setdefault(src, []).append(issue)

        # Mutate in-memory with descending indices within each container
        for src, src_issues in grouped.items():
            sorted_src_issues = sorted(src_issues, key=lambda x: x.get("index", -1), reverse=True)
            for issue in sorted_src_issues:
                idx = issue.get("index", -1)
                suggestions = issue.get("suggestions", [])
                itype = issue.get("type")

                if itype == "invalid_biome":
                    if suggestions:
                        if self.correct_biome(src, idx, suggestions[0], issue.get("bad_biomes", []), save_immediately=False):
                            repaired += 1
                    continue

                is_compound = (itype == "missing_compound_poi")
                if suggestions:
                    chosen = random.choice(suggestions)
                    if self.replace_target(src, idx, chosen, is_compound, save_immediately=False):
                        repaired += 1
                else:
                    if self.remove_target(src, idx, save_immediately=False):
                        pruned += 1

        # Perform one atomic disk write and backup at the end
        self.save_atomic()
        return {"repaired": repaired, "pruned": pruned}

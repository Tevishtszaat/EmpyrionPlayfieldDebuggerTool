import random, shutil
from datetime import datetime
from pathlib import Path
from ruamel.yaml import YAML

yaml = YAML()
yaml.preserve_quotes = True
yaml.indent(mapping=2, sequence=4, offset=2)

class PlayfieldAST:
    def __init__(self, file_path: str):
        self.path = Path(file_path)
        self.data = None
        self.load()

    def load(self):
        with open(self.path, "r", encoding="utf-8") as f:
            self.data = yaml.load(f)

    def backup(self):
        backup_dir = self.path.parent / ".epd_backups"
        backup_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"{self.path.name}_{timestamp}.bak"
        shutil.copy2(self.path, backup_path)
        return str(backup_path)

    def get_pois_list(self):
        if not self.data or not isinstance(self.data, dict):
            return None
        pois = self.data.get("POIs", {}).get("Random", [])
        if not pois and "POIs" in self.data and "Fixed" in self.data["POIs"]:
            return self.data["POIs"]["Fixed"]
        return pois

    def replace_poi_prefab(self, poi_index: int, new_prefab: str):
        self.backup()
        pois = self.get_pois_list()
        if pois is not None and 0 <= poi_index < len(pois):
            pois[poi_index]["Prefab"] = new_prefab
            pois[poi_index]["GroupName"] = new_prefab
            with open(self.path, "w", encoding="utf-8") as f:
                yaml.dump(self.data, f)
            return True
        return False

    def remove_poi_entry(self, poi_index: int):
        """Tier 3: Cleanly removes an unfixable POI from the sequence without leaving syntax artifacts."""
        self.backup()
        pois = self.get_pois_list()
        if pois is not None and 0 <= poi_index < len(pois):
            del pois[poi_index]
            with open(self.path, "w", encoding="utf-8") as f:
                yaml.dump(self.data, f)
            return True
        return False

    def autocomplete_all_issues(self, issues, indexer):
        self.backup()
        pois = self.get_pois_list()
        if pois is None:
            return {"repaired": 0, "pruned": 0}

        repaired = 0
        pruned_indices = []

        # Process in reverse order so index deletion doesn't shift remaining targets
        sorted_issues = sorted(issues, key=lambda x: x.get("index", -1), reverse=True)

        for issue in sorted_issues:
            idx = issue.get("index", -1)
            if idx < 0 or idx >= len(pois):
                continue

            candidates = issue.get("suggestions", [])
            if candidates:
                # Tier 1 / Tier 2: Replace with context-matched candidate
                chosen = random.choice(candidates)
                pois[idx]["Prefab"] = chosen
                pois[idx]["GroupName"] = chosen
                repaired += 1
            else:
                # Tier 3: Unknown type and no group match -> Clean deletion
                pruned_indices.append(idx)

        for idx in pruned_indices:
            del pois[idx]

        with open(self.path, "w", encoding="utf-8") as f:
            yaml.dump(self.data, f)

        return {"repaired": repaired, "pruned": len(pruned_indices)}

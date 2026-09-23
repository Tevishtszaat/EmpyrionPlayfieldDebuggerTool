import re
from pathlib import Path
from typing import List, Dict, Set
from ruamel.yaml import YAML

yaml = YAML()
yaml.preserve_quotes = True

class AssetIndexer:
    def __init__(self, scenario_playfields: str, scenario_prefabs: str, game_playfields: str, game_prefabs: str):
        self.scenario_playfields = Path(scenario_playfields) if scenario_playfields else None
        self.scenario_prefabs = Path(scenario_prefabs) if scenario_prefabs else None
        self.game_playfields = Path(game_playfields) if game_playfields else None
        self.game_prefabs = Path(game_prefabs) if game_prefabs else None

        self.group_to_prefabs: Dict[str, List[str]] = {}
        self.type_to_prefabs: Dict[str, List[str]] = {"BA": [], "CV": [], "SV": [], "HV": [], "OTHER": []}
        self.all_prefabs: List[Dict] = []
        self.file_names_lower: Set[str] = set()
        self.valid_compound_pois: Set[str] = set()
        self.valid_eclasses: Set[str] = set()

    def detect_type(self, name: str) -> str:
        if not name:
            return "OTHER"
        upper = str(name).upper()
        for t in ["BA", "CV", "SV", "HV"]:
            if upper.startswith(f"{t}_") or upper.startswith(t):
                return t
        return "OTHER"

    def load_eclass_config(self):
        """Scans scenario and vanilla configuration for EClassConfig.ecf and EClassConfig.yaml."""
        self.valid_eclasses.clear()
        
        # Hardcoded engine defaults that are always valid
        engine_defaults = [
            "asteroid field", "asteroid", "asteroidv2", "spacefog", "gascloud",
            "spacedebris", "spacemine", "lasermine", "plasmamine", "orbitalstation"
        ]
        for d in engine_defaults:
            self.valid_eclasses.add(d)

        candidates = []
        for base in [self.scenario_prefabs, self.game_prefabs]:
            if base and base.parent:
                cfg = base.parent / "Configuration"
                candidates.extend([
                    cfg / "EClassConfig.ecf",
                    cfg / "EClassConfig.yaml",
                    base.parent / "EClassConfig.ecf",
                    base.parent / "EClassConfig.yaml"
                ])
                if base.parent.parent:
                    candidates.append(base.parent.parent / "Content" / "Configuration" / "EClassConfig.ecf")
                    candidates.append(base.parent.parent / "Content" / "Configuration" / "EClassConfig.yaml")

        for c in set(candidates):
            if c.exists():
                try:
                    with open(c, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            line = line.strip()
                            # ECF format: { EntityClass Id: 123, Name: Asteroid Field ... }
                            if "Name:" in line:
                                parts = line.split("Name:")
                                if len(parts) > 1:
                                    ename = parts[1].split(",")[0].split("}")[0].strip(' "\'')
                                    if ename:
                                        self.valid_eclasses.add(ename.lower())
                            # YAML format: - Name: Asteroid Field
                            elif line.startswith("- Name:") or line.startswith("Name:"):
                                ename = line.split("Name:")[1].strip(' "\'')
                                if ename:
                                    self.valid_eclasses.add(ename.lower())
                except Exception:
                    pass

    def load_compound_pois(self):
        self.valid_compound_pois.clear()
        candidates = []
        for base in [self.scenario_prefabs, self.game_prefabs]:
            if base and base.parent:
                cfg = base.parent / "Configuration"
                candidates.extend([
                    cfg / "CompoundPOIs.yaml",
                    cfg / "CompoundEntities.yaml",
                    base.parent / "CompoundPOIs.yaml"
                ])
                if base.parent.parent:
                    candidates.append(base.parent.parent / "Content" / "Configuration" / "CompoundPOIs.yaml")

        for c in set(candidates):
            if c.exists():
                try:
                    with open(c, "r", encoding="utf-8") as f:
                        data = yaml.load(f)
                    if isinstance(data, dict):
                        for k in data.keys():
                            self.valid_compound_pois.add(str(k).lower())
                    elif isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                name = item.get("Name") or item.get("CompoundPOI") or item.get("GroupName")
                                if name:
                                    self.valid_compound_pois.add(str(name).lower())
                except Exception:
                    pass

    def load_def_prefabs_yaml(self):
        candidates = []
        if self.scenario_prefabs and self.scenario_prefabs.exists():
            candidates.extend(list(self.scenario_prefabs.rglob("DefPrefabs.yaml")))
            if self.scenario_prefabs.parent:
                candidates.extend(list(self.scenario_prefabs.parent.rglob("DefPrefabs.yaml")))

        if self.game_prefabs and self.game_prefabs.exists():
            candidates.extend(list(self.game_prefabs.rglob("DefPrefabs.yaml")))
            if self.game_prefabs.parent:
                candidates.extend(list(self.game_prefabs.parent.rglob("Configuration/DefPrefabs.yaml")))

        for path in set(candidates):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.load(f)
                if isinstance(data, dict):
                    for group_name, info in data.items():
                        g_lower = str(group_name).lower()
                        if isinstance(info, list):
                            for item in info:
                                p_name = item.get("Prefab") if isinstance(item, dict) else str(item)
                                if p_name:
                                    self.group_to_prefabs.setdefault(g_lower, []).append(p_name)
                        elif isinstance(info, dict) and "Prefab" in info:
                            self.group_to_prefabs.setdefault(g_lower, []).append(info["Prefab"])
            except Exception:
                pass

    def get_available_prefabs(self) -> List[Dict]:
        self.all_prefabs = []
        self.file_names_lower.clear()
        self.group_to_prefabs.clear()
        self.type_to_prefabs = {"BA": [], "CV": [], "SV": [], "HV": [], "OTHER": []}

        # Load all reference catalogs
        self.load_eclass_config()
        self.load_compound_pois()
        self.load_def_prefabs_yaml()

        search_dirs = []
        if self.scenario_prefabs and self.scenario_prefabs.exists():
            search_dirs.append((self.scenario_prefabs, "Scenario"))
        if self.game_prefabs and self.game_prefabs.exists():
            search_dirs.append((self.game_prefabs, "Base Game"))

        for root_dir, source in search_dirs:
            for epb in root_dir.rglob("*.epb"):
                stem = epb.stem
                stem_lower = stem.lower()
                if stem_lower not in self.file_names_lower:
                    self.file_names_lower.add(stem_lower)
                    ptype = self.detect_type(stem)
                    self.type_to_prefabs[ptype].append(stem)
                    self.all_prefabs.append({"name": stem, "source": source, "type": ptype})

        self.all_prefabs.sort(key=lambda x: x["name"])
        return self.all_prefabs

    def get_contextual_replacements(self, group_name: str, current_value: str) -> List[str]:
        is_compound = any(x in (group_name or current_value).lower() for x in ["compound", "wreck", "debris", "gascloud", "asteroid"])
        if is_compound and self.valid_compound_pois:
            return sorted(list(self.valid_compound_pois))

        if group_name and group_name.lower() in self.group_to_prefabs:
            matches = list(set(self.group_to_prefabs[group_name.lower()]))
            if matches:
                return sorted(matches)

        detected_type = self.detect_type(current_value or group_name)
        if detected_type != "OTHER" and self.type_to_prefabs[detected_type]:
            return sorted(self.type_to_prefabs[detected_type])

        return [p["name"] for p in self.all_prefabs]

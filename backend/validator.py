import re
from pathlib import Path
from ruamel.yaml import YAML

yaml = YAML()
yaml.preserve_quotes = True

class PlayfieldValidator:
    def __init__(self, playfield_data, indexer, file_path=None):
        self.data = playfield_data
        self.indexer = indexer
        self.file_path = Path(file_path) if file_path else None
        
        if hasattr(indexer, 'get_available_prefabs'):
            prefabs = indexer.get_available_prefabs()
            self.valid_prefabs = {p['name'].lower() for p in prefabs}
        elif isinstance(indexer, list):
            self.valid_prefabs = {p['name'].lower() if isinstance(p, dict) else str(p).lower() for p in indexer}
        else:
            self.valid_prefabs = set()

        self.valid_compounds = getattr(indexer, 'valid_compound_pois', set())
        self.valid_eclasses = getattr(indexer, 'valid_eclasses', set())
        self.planet_biomes = self.extract_planet_biomes()

        # Engine-level origin anchors & dummy POIs (Never missing, always valid)
        self.null_poi_whitelist = {
            "nullpoi", "null_poi", "emptypoi", "empty_poi", 
            "null", "nullorigin", "originpoi", "dummy_poi", "dummypoi"
        }

    def is_null_poi(self, identifier: str) -> bool:
        if not identifier:
            return False
        clean = identifier.lower().strip()
        if clean in self.null_poi_whitelist:
            return True
        if clean.startswith("nullpoi") or clean.startswith("null_poi"):
            return True
        return False

    def extract_planet_biomes(self) -> set:
        biomes = set()
        if not self.data or not isinstance(self.data, dict):
            return biomes

        b_section = self.data.get("Biome", [])
        if isinstance(b_section, list):
            for b in b_section:
                if isinstance(b, dict):
                    name = b.get("Name")
                    if name:
                        biomes.add(str(name).strip())

        if self.file_path and self.file_path.parent:
            companion_files = [
                self.file_path.parent / "playfield_dynamic.yaml",
                self.file_path.parent / "playfield_dynamic.yml",
                self.file_path.parent / "playfield.yaml"
            ]
            for comp in companion_files:
                if comp.exists() and comp != self.file_path:
                    try:
                        with open(comp, "r", encoding="utf-8") as f:
                            cdata = yaml.load(f)
                        if isinstance(cdata, dict):
                            cb = cdata.get("Biome", [])
                            if isinstance(cb, list):
                                for b in cb:
                                    if isinstance(b, dict):
                                        name = b.get("Name")
                                        if name:
                                            biomes.add(str(name).strip())
                    except Exception:
                        pass

        biomes.update({"Any", "Global", "Space"})
        return biomes

    def validate(self):
        issues = []
        if not self.data or not isinstance(self.data, dict):
            return issues

        targets = []

        # 1. Random and Fixed POIs
        raw_pois = self.data.get("POIs", {})
        if isinstance(raw_pois, dict):
            r_list = raw_pois.get("Random", [])
            f_list = raw_pois.get("Fixed", [])
            if isinstance(r_list, list):
                for idx, item in enumerate(r_list):
                    if isinstance(item, dict):
                        targets.append({"source": "Random", "index": idx, "data": item})
            if isinstance(f_list, list):
                for idx, item in enumerate(f_list):
                    if isinstance(item, dict):
                        targets.append({"source": "Fixed", "index": idx, "data": item})

        # 2. Objects sequence
        raw_objects = self.data.get("Objects", [])
        if isinstance(raw_objects, list):
            for idx, item in enumerate(raw_objects):
                if isinstance(item, dict):
                    targets.append({"source": "Objects", "index": idx, "data": item})

        # 3. Drones and Spawners
        raw_drone_spawns = self.data.get("DroneSpawns", [])
        if isinstance(raw_drone_spawns, list):
            for idx, item in enumerate(raw_drone_spawns):
                if isinstance(item, dict):
                    targets.append({"source": "DroneSpawns", "index": idx, "data": item})

        for entry in targets:
            source = entry["source"]
            idx = entry["index"]
            poi = entry["data"]

            prefab = str(poi.get("Prefab") or poi.get("Name") or poi.get("Model") or "").strip()
            if prefab.lower().endswith(".epb"):
                prefab = prefab[:-4]

            group_name = str(poi.get("GroupName", "")).strip()
            compound_name = str(poi.get("CompoundPOI", "")).strip()
            faction = str(poi.get("Faction", "Unknown")).strip()

            target_id = compound_name or group_name or prefab

            # CHECK 0: Is this a NullPOI origin anchor placeholder? (Skip immediately!)
            if self.is_null_poi(prefab) or self.is_null_poi(group_name) or self.is_null_poi(target_id):
                continue

            # Check Biomes
            if self.planet_biomes and len(self.planet_biomes) > 3:
                raw_biome = poi.get("Biome", [])
                assigned_biomes = []
                if isinstance(raw_biome, list):
                    assigned_biomes = [str(b).strip() for b in raw_biome]
                elif isinstance(raw_biome, str):
                    assigned_biomes = [raw_biome.strip()]

                invalid_biomes = []
                for ab in assigned_biomes:
                    if ab and ab not in self.planet_biomes and ab.lower() not in {b.lower() for b in self.planet_biomes}:
                        invalid_biomes.append(ab)

                if invalid_biomes:
                    valid_biome_suggestions = sorted([b for b in self.planet_biomes if b not in {"Any", "Global", "Space"}])
                    issues.append({
                        "id": f"biome_{source}_{idx}",
                        "source": source,
                        "index": idx,
                        "type": "invalid_biome",
                        "severity": "WARNING",
                        "message": f"Assigned Biome '{', '.join(invalid_biomes)}' does not exist on this planet!",
                        "current_value": f"Entity: '{target_id}' | Bad Biome: {invalid_biomes}",
                        "faction": faction,
                        "group_name": group_name,
                        "bad_biomes": invalid_biomes,
                        "suggestions": valid_biome_suggestions
                    })

            if not target_id:
                continue

            target_lower = target_id.lower()

            # Check EClass (Asteroid Field, Fog, Gas Clouds, etc.)
            if target_lower in self.valid_eclasses or any(target_lower.startswith(ec) for ec in ["asteroid", "gascloud", "spacefog"]):
                continue

            # Check Compound POIs
            is_compound = bool(compound_name or target_lower.startswith("compound") or "wreck" in target_lower or "debris" in target_lower)
            if is_compound:
                if target_lower not in self.valid_compounds:
                    suggestions = self.indexer.get_contextual_replacements(target_id, target_id) if hasattr(self.indexer, 'get_contextual_replacements') else []
                    issues.append({
                        "id": f"compound_{source}_{idx}",
                        "source": source,
                        "index": idx,
                        "type": "missing_compound_poi",
                        "severity": "ERROR",
                        "message": f"Compound POI '{target_id}' not found in CompoundPOIs.yaml!",
                        "current_value": target_id,
                        "faction": faction,
                        "group_name": group_name,
                        "suggestions": suggestions
                    })
                    continue

            # Check Prefab & GroupName
            prefab_valid = bool(prefab and prefab.lower() in self.valid_prefabs)
            group_valid = False

            if group_name:
                g_lower = group_name.lower()
                if hasattr(self.indexer, 'group_to_prefabs'):
                    if g_lower in self.indexer.group_to_prefabs and len(self.indexer.group_to_prefabs[g_lower]) > 0:
                        group_valid = True
                if not group_valid and g_lower in self.valid_prefabs:
                    group_valid = True

            if not prefab_valid and not group_valid:
                suggestions = self.indexer.get_contextual_replacements(group_name, prefab) if hasattr(self.indexer, 'get_contextual_replacements') else []
                issues.append({
                    "id": f"poi_{source}_{idx}",
                    "source": source,
                    "index": idx,
                    "type": "missing_prefab",
                    "severity": "ERROR",
                    "message": f"Asset '{group_name or prefab}' not found in Prefabs folder.",
                    "current_value": f"Name/Prefab: '{prefab}' | Group: '{group_name}'",
                    "faction": faction,
                    "group_name": group_name,
                    "suggestions": suggestions
                })

        return issues

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

        playfield_type = str(self.data.get("PlayfieldType", "")).lower()
        is_space = (playfield_type == "space") or ("space" in (self.file_path.stem.lower() if self.file_path else ""))

        # -------------------------------------------------------------
        # CHECK A: Instance Header Audit (ExampleInstance rule)
        # -------------------------------------------------------------
        if self.file_path and "instance" in self.file_path.parent.name.lower():
            is_instance_flagged = bool(self.data.get("Instance") is True)
            if not is_instance_flagged:
                issues.append({
                    "id": "hdr_instance_missing",
                    "source": "Header",
                    "index": 0,
                    "type": "missing_instance_flag",
                    "severity": "WARNING",
                    "message": "Instance folder detected but top-level 'Instance: true' header is missing!",
                    "current_value": f"Folder: {self.file_path.parent.name} | Instance: None",
                    "suggestions": []
                })

        # -------------------------------------------------------------
        # CHECK B: Space Fog & SunFlare Verification (ExampleSpace rule)
        # -------------------------------------------------------------
        if is_space:
            space_fog = self.data.get("SpaceFog")
            if space_fog and isinstance(space_fog, str):
                sf_clean = space_fog.strip().lower()
                if sf_clean not in self.valid_eclasses and not sf_clean.startswith("spacefog"):
                    issues.append({
                        "id": "space_fog_missing",
                        "source": "Header",
                        "index": 0,
                        "type": "invalid_space_fog",
                        "severity": "WARNING",
                        "message": f"SpaceFog '{space_fog}' not found in engine EntityClasses!",
                        "current_value": space_fog,
                        "suggestions": ["SpaceFog", "SpaceFogRed", "SpaceFogBlue", "SpaceFogGreen"]
                    })

        targets = []
        existing_poi_names = set()

        # 1. Random & Fixed POIs
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

        # Pre-collect all active POI names to audit drone hives
        for entry in targets:
            poi = entry["data"]
            g_name = str(poi.get("GroupName", "")).strip().lower()
            p_name = str(poi.get("Prefab", "")).strip().lower()
            n_name = str(poi.get("Name", "")).strip().lower()
            for n in [g_name, p_name, n_name]:
                if n:
                    existing_poi_names.add(n)

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

            # Check 0: NullPOI Origin Anchor (Skip)
            if self.is_null_poi(prefab) or self.is_null_poi(group_name) or self.is_null_poi(target_id):
                continue

            # -------------------------------------------------------------
            # CHECK C: Space Sector Coordinate Boundary (ExampleSpace rule)
            # -------------------------------------------------------------
            if is_space:
                pos = poi.get("Pos")
                if isinstance(pos, list) and len(pos) >= 3:
                    try:
                        coords = [abs(float(c)) for c in pos[:3]]
                        if any(c > 25000 for c in coords):
                            issues.append({
                                "id": f"bound_{source}_{idx}",
                                "source": source,
                                "index": idx,
                                "type": "out_of_bounds_pos",
                                "severity": "WARNING",
                                "message": f"Object '{target_id}' coordinates exceed sector radius (>25,000m)!",
                                "current_value": f"Pos: {pos}",
                                "faction": faction,
                                "group_name": group_name,
                                "suggestions": []
                            })
                    except (ValueError, TypeError):
                        pass

            # -------------------------------------------------------------
            # CHECK D: Orphan Drone Base Hive Audit (ExamplePlanet/Space rule)
            # -------------------------------------------------------------
            if source == "DroneSpawns":
                drone_base = str(poi.get("Base", "")).strip()
                if drone_base and drone_base.lower() not in existing_poi_names and not self.is_null_poi(drone_base):
                    issues.append({
                        "id": f"orphan_drone_{source}_{idx}",
                        "source": source,
                        "index": idx,
                        "type": "orphan_drone_base",
                        "severity": "WARNING",
                        "message": f"Drone hive Base '{drone_base}' does not exist in POIs list on this playfield!",
                        "current_value": f"Base: {drone_base}",
                        "faction": faction,
                        "group_name": group_name,
                        "suggestions": []
                    })

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

            # Check EClass (Asteroids, clouds, hazards)
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

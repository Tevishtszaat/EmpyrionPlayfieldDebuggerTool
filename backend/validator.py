class PlayfieldValidator:
    def __init__(self, playfield_data, indexer):
        self.data = playfield_data
        self.indexer = indexer
        
        if hasattr(indexer, 'get_available_prefabs'):
            prefabs = indexer.get_available_prefabs()
            self.valid_prefabs = {p['name'].lower() for p in prefabs}
        elif isinstance(indexer, list):
            self.valid_prefabs = {p['name'].lower() if isinstance(p, dict) else str(p).lower() for p in indexer}
        else:
            self.valid_prefabs = set()

        self.valid_compounds = getattr(indexer, 'valid_compound_pois', set())

    def validate(self):
        issues = []
        if not self.data or not isinstance(self.data, dict):
            return issues

        # Collect targets safely without modifying original dictionary
        targets = []

        # 1. Standard POIs (Random and Fixed)
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

        # 2. Orbital Objects (Asteroid fields, wrecks, space stations)
        raw_objects = self.data.get("Objects", [])
        if isinstance(raw_objects, list):
            for idx, item in enumerate(raw_objects):
                if isinstance(item, dict):
                    targets.append({"source": "Objects", "index": idx, "data": item})

        for entry in targets:
            idx = entry["index"]
            poi = entry["data"]

            prefab = str(poi.get("Prefab", "")).strip()
            group_name = str(poi.get("GroupName", "")).strip()
            compound_name = str(poi.get("CompoundPOI", "")).strip()
            faction = str(poi.get("Faction", "Unknown")).strip()

            target_id = compound_name or group_name or prefab
            if not target_id:
                continue

            target_lower = target_id.lower()

            # Check A: Compound POI (Asteroid clusters, wrecks, gas clouds)
            is_compound = bool(compound_name or target_lower.startswith("compound") or "wreck" in target_lower or "debris" in target_lower)
            if is_compound:
                if target_lower not in self.valid_compounds:
                    suggestions = self.indexer.get_contextual_replacements(target_id, target_id) if hasattr(self.indexer, 'get_contextual_replacements') else []
                    issues.append({
                        "id": f"compound_{entry['source']}_{idx}",
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

            # Check B: Prefab and Blueprint Group validation
            prefab_valid = bool(prefab and prefab.lower() in self.valid_prefabs)
            group_valid = False

            if group_name:
                g_lower = group_name.lower()
                if hasattr(self.indexer, 'group_to_prefabs'):
                    if g_lower in self.indexer.group_to_prefabs and len(self.indexer.group_to_prefabs[g_lower]) > 0:
                        group_valid = True
                if not group_valid and g_lower in self.valid_prefabs:
                    group_valid = True

            # If neither Prefab nor Group exists
            if not prefab_valid and not group_valid:
                suggestions = self.indexer.get_contextual_replacements(group_name, prefab) if hasattr(self.indexer, 'get_contextual_replacements') else []
                issues.append({
                    "id": f"poi_{entry['source']}_{idx}",
                    "index": idx,
                    "type": "missing_prefab",
                    "severity": "ERROR",
                    "message": f"Blueprint group or prefab '{group_name or prefab}' not found in Prefabs folder.",
                    "current_value": f"Group: '{group_name}' | Prefab: '{prefab}'",
                    "faction": faction,
                    "group_name": group_name,
                    "suggestions": suggestions
                })

        return issues

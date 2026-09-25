"""Playfield checks aligned with how Empyrion loads the YAML.

Random spawn → GroupName (blueprint group inside the .epb).
Fixed spawn → Prefab filename, and UseFixed: True or Survival ignores them.
Biome filters → BiomeClusterData names in this file, its sibling dynamic/static
yaml, or the same folder under the base-game playfields.
DroneBaseSetup on a POI → a matching entry under DroneBaseSetup.Random.
SpawnPOINear / SpawnPOIAvoid → another GroupName in this file. Near-targets
must already appear higher in the list (the game walks top to bottom).
"""

from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML

from backend.schema import BIOME_TOKENS, FIXED_POI_KEYS, RANDOM_POI_KEYS, SPAWN_TOKENS

yaml = YAML()
yaml.preserve_quotes = True

_NULL = {
    "nullpoi", "null_poi", "emptypoi", "empty_poi", "null",
    "nullorigin", "originpoi", "dummy_poi", "dummypoi",
}
_YAML_NAMES = (
    "playfield.yaml",
    "playfield.yml",
    "playfield_dynamic.yaml",
    "playfield_static.yaml",
)


def _line(node) -> int | None:
    lc = getattr(node, "lc", None)
    if lc is not None and getattr(lc, "line", None) is not None:
        return int(lc.line) + 1
    return None


def _load(path: Path):
    try:
        return yaml.load(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _harvest_biomes(node, acc: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {"BiomeClusterData", "Biomes"} and isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and item.get("Name"):
                        acc.add(str(item["Name"]).strip())
            elif key == "Biome" and isinstance(value, list) and value and all(isinstance(i, dict) for i in value):
                for item in value:
                    if isinstance(item, dict) and item.get("Name"):
                        acc.add(str(item["Name"]).strip())
            else:
                _harvest_biomes(value, acc)
    elif isinstance(node, list):
        for item in node:
            _harvest_biomes(item, acc)


def _harvest_resources(node, acc: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {"RandomResources", "FixedResources", "AsteroidResources"} and isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and item.get("Name"):
                        acc.add(str(item["Name"]).strip())
            else:
                _harvest_resources(value, acc)
    elif isinstance(node, list):
        for item in node:
            _harvest_resources(item, acc)


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _is_null(name: str) -> bool:
    clean = (name or "").lower().strip()
    return clean in _NULL or clean.startswith("nullpoi") or clean.startswith("null_poi")


class PlayfieldValidator:
    def __init__(self, playfield_data, indexer, file_path=None):
        self.data = playfield_data
        self.indexer = indexer
        self.file_path = Path(file_path) if file_path else None
        indexer.build()
        self.biomes = self._collect_biomes()
        self.resources = self._collect_resources()

    def _sibling_docs(self):
        docs = []
        if isinstance(self.data, dict):
            docs.append(self.data)
        paths = []
        if self.file_path and self.file_path.parent:
            for name in _YAML_NAMES:
                candidate = self.file_path.parent / name
                if candidate.exists() and candidate.resolve() != self.file_path.resolve():
                    paths.append(candidate)
            game_root = getattr(self.indexer, "game_playfields", None)
            if game_root and Path(game_root).exists():
                game_dir = Path(game_root) / self.file_path.parent.name
                if game_dir.is_dir():
                    for name in _YAML_NAMES:
                        candidate = game_dir / name
                        if candidate.exists():
                            paths.append(candidate)
        for path in paths:
            loaded = _load(path)
            if loaded is not None:
                docs.append(loaded)
        return docs

    def _collect_biomes(self) -> set[str]:
        names: set[str] = set()
        for doc in self._sibling_docs():
            _harvest_biomes(doc, names)
        return names

    def _collect_resources(self) -> set[str]:
        names: set[str] = set()
        for doc in self._sibling_docs():
            _harvest_resources(doc, names)
        return names

    def _issue(self, **kwargs) -> dict:
        kwargs.setdefault("suggestions", [])
        kwargs.setdefault("index", -1)
        kwargs.setdefault("source", "Header")
        return kwargs

    def validate(self) -> list[dict]:
        issues = []
        if not isinstance(self.data, dict):
            return issues

        self._check_instance(issues)
        self._check_space_fog(issues)
        self._check_use_fixed(issues)

        pois = self.data.get("POIs")
        if not isinstance(pois, dict):
            self._check_drone_links(issues, [])
            return issues

        order: list[str] = []
        entries = []
        for source in ("Random", "Fixed"):
            rows = pois.get(source)
            if not isinstance(rows, list):
                continue
            for index, item in enumerate(rows):
                if not isinstance(item, dict):
                    continue
                group = str(item.get("GroupName") or "").strip()
                if source == "Random" and group:
                    order.append(group)
                entries.append((source, index, item, len(order) - 1 if source == "Random" and group else None))

        for source, index, item, position in entries:
            self._check_poi(issues, source, index, item, order, position)

        self._check_drone_links(issues, order)
        return issues

    def _check_instance(self, issues: list[dict]) -> None:
        if not self.file_path:
            return
        if "instance" not in self.file_path.parent.name.lower():
            return
        if self.data.get("Instance") is True:
            return
        issues.append(self._issue(
            id="hdr_instance_missing",
            type="missing_instance_flag",
            severity="WARNING",
            message="Folder name looks like an instance playfield, but 'Instance: true' is not set.",
            current_value=f"Folder: {self.file_path.parent.name}",
        ))

    def _check_space_fog(self, issues: list[dict]) -> None:
        kind = str(self.data.get("PlayfieldType", "")).lower()
        folder = self.file_path.parent.name.lower() if self.file_path else ""
        if kind != "space" and "orbit" not in folder and "space" not in folder:
            return
        fog = self.data.get("SpaceFog")
        if not isinstance(fog, str) or not fog.strip():
            return
        if self.indexer.known_eclass(fog.strip()):
            return
        issues.append(self._issue(
            id="space_fog",
            type="invalid_space_fog",
            severity="WARNING",
            message=f"SpaceFog '{fog}' is not an entity class in EClassConfig.",
            current_value=fog,
            suggestions=self.indexer.suggest(fog, "eclass"),
        ))

    def _check_use_fixed(self, issues: list[dict]) -> None:
        pois = self.data.get("POIs")
        if not isinstance(pois, dict):
            return
        fixed = pois.get("Fixed")
        if not isinstance(fixed, list) or not any(isinstance(row, dict) for row in fixed):
            return
        flag = self.data.get("UseFixed")
        if flag is True or str(flag).strip().lower() == "true":
            return
        issues.append(self._issue(
            id="missing_usefixed",
            type="missing_usefixed",
            severity="ERROR",
            message="POIs.Fixed is not empty, but UseFixed is not true. Survival will ignore these fixed POIs.",
            current_value=f"UseFixed: {flag!r}",
            suggestions=["True"],
        ))

    def _check_poi(self, issues, source, index, item, order, position) -> None:
        group = str(item.get("GroupName") or "").strip()
        prefab = str(item.get("Prefab") or "").strip()
        if prefab.lower().endswith(".epb"):
            prefab = prefab[:-4]
        if _is_null(group) or _is_null(prefab):
            return

        line = _line(item)
        allowed = RANDOM_POI_KEYS if source == "Random" else FIXED_POI_KEYS
        unknown = [str(k) for k in item.keys() if str(k).lower().strip() not in allowed]
        if unknown:
            issues.append(self._issue(
                id=f"key_{source}_{index}",
                source=source,
                index=index,
                type="unknown_key",
                severity="WARNING",
                message=f"Unknown {source} POI key(s) {unknown}. Empyrion's YAML loader can reject the whole playfield.",
                current_value=", ".join(unknown),
                unknown_keys=unknown,
                line=line,
                suggestions=[],
            ))

        self._check_biome(issues, source, index, item, line)
        self._check_pos(issues, source, index, item, line)
        self._check_spawn_lists(issues, source, index, item, order, position, line)
        self._check_resources(issues, source, index, item, line)
        self._check_compound(issues, source, index, item, line)

        if source == "Random":
            if not group:
                known = self.indexer.known_group(prefab) if prefab else None
                issues.append(self._issue(
                    id=f"grp_{source}_{index}",
                    source=source,
                    index=index,
                    type="random_needs_group",
                    severity="ERROR",
                    message="Random POI has no GroupName. The game spawns these by blueprint group, not by Prefab filename.",
                    current_value=f"Prefab: '{prefab}'" if prefab else "(empty)",
                    lookup=prefab,
                    line=line,
                    suggestions=[known] if known else self.indexer.suggest(prefab, "group"),
                ))
                return
            canonical = self.indexer.known_group(group)
            if canonical == group:
                return
            issues.append(self._issue(
                id=f"grp_{source}_{index}",
                source=source,
                index=index,
                type="missing_group",
                severity="ERROR",
                message=(
                    f"GroupName '{group}' is not a blueprint group or prefab filename. "
                    "Random POIs use the group stored in the .epb (F2 dialog)."
                    if canonical is None
                    else f"GroupName '{group}' does not match the indexed spelling '{canonical}'."
                ),
                current_value=group,
                lookup=group,
                line=line,
                suggestions=([canonical] if canonical else []) + [
                    s for s in self.indexer.suggest(group, "group") if s != canonical
                ],
            ))
            return

        # Fixed
        if not prefab:
            issues.append(self._issue(
                id=f"pf_{source}_{index}",
                source=source,
                index=index,
                type="missing_prefab",
                severity="ERROR",
                message="Fixed POI has no Prefab. GroupName is ignored for fixed spawns.",
                current_value=f"GroupName: '{group}'",
                lookup=group,
                line=line,
                suggestions=self.indexer.suggest(group, "prefab"),
            ))
            return
        canonical = self.indexer.known_prefab(prefab)
        if canonical == prefab:
            return
        issues.append(self._issue(
            id=f"pf_{source}_{index}",
            source=source,
            index=index,
            type="missing_prefab",
            severity="ERROR",
            message=(
                f"Prefab '{prefab}' has no matching .epb."
                if canonical is None
                else f"Prefab '{prefab}' does not match the file spelling '{canonical}'."
            ),
            current_value=prefab,
            lookup=prefab,
            line=line,
            suggestions=([canonical] if canonical else []) + [
                s for s in self.indexer.suggest(prefab, "prefab") if s != canonical
            ],
        ))

    def _check_biome(self, issues, source, index, item, line) -> None:
        if not self.biomes:
            return
        assigned = [str(b).strip() for b in _as_list(item.get("Biome")) if str(b).strip()]
        known = {b.lower(): b for b in self.biomes}
        bad = []
        for name in assigned:
            if name.lower() in BIOME_TOKENS:
                continue
            canonical = known.get(name.lower())
            if canonical is None or canonical != name:
                bad.append(name)
        if not bad:
            return
        issues.append(self._issue(
            id=f"biome_{source}_{index}",
            source=source,
            index=index,
            type="invalid_biome",
            severity="WARNING",
            message=f"Biome filter {bad} is not a BiomeClusterData name on this playfield.",
            current_value=", ".join(bad),
            bad_biomes=bad,
            line=line,
            suggestions=sorted(self.biomes, key=str.lower),
        ))

    def _check_pos(self, issues, source, index, item, line) -> None:
        if "Pos" not in item:
            return
        pos = item.get("Pos")
        ok = isinstance(pos, list) and len(pos) >= 3
        if ok:
            try:
                [float(c) for c in pos[:3]]
            except (TypeError, ValueError):
                ok = False
        if ok:
            return
        issues.append(self._issue(
            id=f"pos_{source}_{index}",
            source=source,
            index=index,
            type="malformed_pos",
            severity="ERROR",
            message="Pos must be a list of three numbers.",
            current_value=repr(pos),
            line=line,
        ))

    def _check_spawn_lists(self, issues, source, index, item, order, position, line) -> None:
        if source != "Random":
            return
        order_l = [name.lower() for name in order]

        def refs(key):
            return [str(v).strip() for v in _as_list(item.get(key)) if str(v).strip()]

        for key in ("SpawnPOINear", "SpawnPOIAvoid"):
            for ref in refs(key):
                if ref.lower() in SPAWN_TOKENS:
                    continue
                if ref.lower() not in order_l:
                    issues.append(self._issue(
                        id=f"ref_{source}_{index}_{key}_{ref}",
                        source=source,
                        index=index,
                        type="broken_spawn_ref",
                        severity="ERROR",
                        message=f"{key} references '{ref}', which is not a GroupName in this playfield.",
                        current_value=ref,
                        line=line,
                        suggestions=[n for n in order if n.lower() != ref.lower()][:20],
                    ))
                    continue
                if key == "SpawnPOINear" and position is not None:
                    target_at = order_l.index(ref.lower())
                    if target_at > position:
                        issues.append(self._issue(
                            id=f"ord_{source}_{index}_{ref}",
                            source=source,
                            index=index,
                            type="spawn_order",
                            severity="WARNING",
                            message=(
                                f"SpawnPOINear '{ref}' is listed lower than this POI. "
                                "The game places POIs top to bottom, so the target will not exist yet."
                            ),
                            current_value=ref,
                            line=line,
                        ))

    def _check_resources(self, issues, source, index, item, line) -> None:
        if not self.resources:
            return
        known = {name.lower() for name in self.resources}
        bad = []
        for raw in _as_list(item.get("SpawnResource")):
            token = str(raw).split(":", 1)[0].strip()
            if token and token.lower() not in known:
                bad.append(str(raw))
        if not bad:
            return
        issues.append(self._issue(
            id=f"res_{source}_{index}",
            source=source,
            index=index,
            type="bad_spawn_resource",
            severity="WARNING",
            message=f"SpawnResource {bad} does not match a deposit name in this playfield.",
            current_value=", ".join(bad),
            line=line,
            suggestions=sorted(self.resources),
        ))

    def _check_compound(self, issues, source, index, item, line) -> None:
        names = []
        compound = item.get("Compound")
        if isinstance(compound, dict):
            raw = compound.get("Name")
            names.extend(str(n).strip() for n in _as_list(raw) if str(n).strip())
        raw_poi = item.get("CompoundPOI")
        if isinstance(raw_poi, str) and raw_poi.strip():
            names.append(raw_poi.strip())
        if not names or not self.indexer.compound_canonical:
            return
        for name in names:
            if self.indexer.known_compound(name):
                continue
            issues.append(self._issue(
                id=f"cmp_{source}_{index}_{name}",
                source=source,
                index=index,
                type="missing_compound_name",
                severity="ERROR",
                message=f"Compound name '{name}' is not in the compound config.",
                current_value=name,
                lookup=name,
                line=line,
                suggestions=self.indexer.suggest(name, "compound"),
            ))

    def _check_drone_links(self, issues, order: list[str]) -> None:
        setups = []
        block = self.data.get("DroneBaseSetup")
        if isinstance(block, dict) and isinstance(block.get("Random"), list):
            for index, item in enumerate(block["Random"]):
                if isinstance(item, dict) and item.get("GroupName"):
                    setups.append((index, str(item["GroupName"]).strip()))

        setup_names = {name.lower() for _, name in setups}
        pois = self.data.get("POIs")
        rows = pois.get("Random") if isinstance(pois, dict) else None
        if isinstance(rows, list):
            for index, item in enumerate(rows):
                if not isinstance(item, dict):
                    continue
                ref = item.get("DroneBaseSetup")
                if not isinstance(ref, str) or not ref.strip():
                    continue
                if ref.strip().lower() in setup_names:
                    continue
                issues.append(self._issue(
                    id=f"drone_{index}",
                    source="Random",
                    index=index,
                    type="orphan_drone_setup",
                    severity="ERROR",
                    message=f"DroneBaseSetup '{ref}' has no matching entry under DroneBaseSetup.Random.",
                    current_value=ref,
                    line=_line(item),
                    suggestions=[name for _, name in setups],
                ))

        order_l = {name.lower() for name in order}
        for index, name in setups:
            if name.lower() in order_l:
                continue
            issues.append(self._issue(
                id=f"drone_base_{index}",
                source="DroneBaseSetup",
                index=index,
                type="orphan_drone_setup",
                severity="WARNING",
                message=f"Drone base setup '{name}' is not a GroupName in POIs.Random, so the base itself may never spawn.",
                current_value=name,
                line=None,
            ))

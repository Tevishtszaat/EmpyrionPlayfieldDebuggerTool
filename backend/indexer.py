"""Prefab, group, compound, and entity-class index.

Random POIs spawn by the blueprint *group name* stored in the .epb header
(the F2 dialog), not by a DefPrefabs.yaml list. Fixed POIs spawn by the
file stem (`Prefab: BA_Outpost` → `BA_Outpost.epb`). Scenario prefabs
override the base game when both spell the same name.
"""

from __future__ import annotations

import re
from pathlib import Path

from ruamel.yaml import YAML

yaml = YAML()
yaml.preserve_quotes = True

_IDENT = re.compile(rb"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_\-]{3,48})(?![A-Za-z0-9_\-])")

# Words that show up in EPB headers and are not group names.
_STOP = {
    "prefab", "blueprint", "version", "steam", "creator", "survival", "creative",
    "entity", "block", "base", "small", "capital", "hover", "voxel", "true",
    "false", "null", "name", "group", "offset", "ground", "spawn", "device",
    "signal", "color", "index", "count", "size", "width", "height", "depth",
    "light", "triangle", "empyrion", "galactic", "header", "build", "player",
    "public", "private", "neutral", "faction", "value", "type", "mode",
}


def _lev(a: str, b: str) -> int:
    if a == b:
        return 0
    if abs(len(a) - len(b)) > 12:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def harvest_header_names(blob: bytes) -> list[str]:
    """Group and spawn names are stored as plain ASCII/UTF-8 in the EPB header,
    before the compressed block grid."""
    found: list[str] = []
    seen = set()
    for match in _IDENT.finditer(blob[:3072]):
        text = match.group(1).decode("ascii")
        key = text.lower()
        if key in _STOP or key in seen:
            continue
        seen.add(key)
        found.append(text)
    return found


class AssetIndexer:
    def __init__(self, scenario_playfields: str, scenario_prefabs: str, game_playfields: str, game_prefabs: str):
        self.scenario_playfields = Path(scenario_playfields) if scenario_playfields else None
        self.scenario_prefabs = Path(scenario_prefabs) if scenario_prefabs else None
        self.game_playfields = Path(game_playfields) if game_playfields else None
        self.game_prefabs = Path(game_prefabs) if game_prefabs else None

        self.group_canonical: dict[str, str] = {}
        self.prefab_canonical: dict[str, str] = {}
        self.compound_canonical: dict[str, str] = {}
        self.eclass_canonical: dict[str, str] = {}
        self.prefab_source: dict[str, str] = {}
        self.ready = False
        self.notes: list[str] = []

    def detect_type(self, name: str) -> str:
        upper = str(name or "").upper()
        for kind in ("BA", "CV", "SV", "HV"):
            if upper.startswith(kind + "_") or upper.startswith(kind + "-"):
                return kind
        return "OTHER"

    def build(self) -> None:
        if self.ready:
            return
        self._load_eclasses()
        self._load_compounds()
        self._load_prefabs()
        self.ready = True

    def get_available_prefabs(self):
        """Backward-compatible name. Does not rescan once built."""
        self.build()
        return [
            {"name": name, "source": self.prefab_source.get(key, ""), "type": self.detect_type(name)}
            for key, name in sorted(self.prefab_canonical.items(), key=lambda kv: kv[1].lower())
        ]

    def _remember(self, table: dict[str, str], name: str, overwrite: bool) -> None:
        if not name:
            return
        key = name.lower()
        if overwrite or key not in table:
            table[key] = name

    def _config_candidates(self, filename: str) -> list[Path]:
        found: list[Path] = []
        for base in (self.scenario_prefabs, self.game_prefabs):
            if not base:
                continue
            parent = base.parent
            found.append(parent / "Configuration" / filename)
            found.append(parent / filename)
            if parent.parent:
                found.append(parent.parent / "Content" / "Configuration" / filename)
        return found

    def _load_eclasses(self) -> None:
        for default in (
            "Asteroid", "AsteroidField", "AsteroidResource", "SpaceFog",
            "GasCloud", "SpaceDebris", "SpaceMine",
        ):
            self._remember(self.eclass_canonical, default, overwrite=False)

        seen_files = set()
        for path in self._config_candidates("EClassConfig.ecf") + self._config_candidates("EClassConfig.yaml"):
            if not path.exists() or path in seen_files:
                continue
            seen_files.add(path)
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for raw in text.splitlines():
                line = raw.split("#", 1)[0].strip()
                if not line or "Name:" not in line:
                    continue
                name = line.split("Name:", 1)[1].split(",")[0].split("}")[0].strip(" \"'")
                if name and " " not in name:
                    self._remember(self.eclass_canonical, name, overwrite=False)

    def _consume_compound_obj(self, data) -> None:
        if isinstance(data, dict):
            # Map of name → body, or a document with a list under a key.
            list_keys = [k for k in data if isinstance(data[k], list)]
            if list_keys and not any(isinstance(v, (dict, str)) for v in data.values() if not isinstance(v, list)):
                pass
            for key, value in data.items():
                if isinstance(value, (dict, list)) and key.lower() in {"compounds", "compoundpois", "poi"}:
                    self._consume_compound_obj(value)
                elif isinstance(value, dict):
                    self._remember(self.compound_canonical, str(key), overwrite=False)
                    inner = value.get("Name") or value.get("CompoundPOI")
                    if isinstance(inner, str):
                        self._remember(self.compound_canonical, inner, overwrite=False)
                elif isinstance(value, list):
                    self._consume_compound_obj(value)
                elif isinstance(value, str) and key.lower() in {"name", "compoundpoi", "groupname"}:
                    self._remember(self.compound_canonical, value, overwrite=False)
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    self._remember(self.compound_canonical, item, overwrite=False)
                elif isinstance(item, dict):
                    name = item.get("Name") or item.get("CompoundPOI") or item.get("GroupName")
                    if isinstance(name, str):
                        self._remember(self.compound_canonical, name, overwrite=False)
                    elif isinstance(name, list):
                        for part in name:
                            self._remember(self.compound_canonical, str(part), overwrite=False)

    def _load_compounds(self) -> None:
        names = ("CompoundPOIs.yaml", "CompoundPOI.yaml", "CompoundEntities.yaml", "Compounds.yaml")
        seen = set()
        for filename in names:
            for path in self._config_candidates(filename):
                if not path.exists() or path in seen:
                    continue
                seen.add(path)
                try:
                    data = yaml.load(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                self._consume_compound_obj(data)
        if not self.compound_canonical:
            self.notes.append("No CompoundPOIs.yaml found. Compound.Name values were not verified.")

    def _load_prefabs(self) -> None:
        # Game first, scenario overwrites canonical spelling.
        roots = []
        if self.game_prefabs and self.game_prefabs.exists():
            roots.append((self.game_prefabs, "Base Game", False))
        if self.scenario_prefabs and self.scenario_prefabs.exists():
            roots.append((self.scenario_prefabs, "Scenario", True))

        for root, source, overwrite in roots:
            for epb in root.rglob("*.epb"):
                if any(part.startswith(".") for part in epb.parts):
                    continue
                stem = epb.stem
                key = stem.lower()
                self._remember(self.prefab_canonical, stem, overwrite=overwrite)
                self.prefab_source[key] = source
                # A group is often the filename itself when the blueprint has one member.
                self._remember(self.group_canonical, stem, overwrite=overwrite)
                try:
                    blob = epb.read_bytes()
                except OSError:
                    continue
                for name in harvest_header_names(blob):
                    self._remember(self.group_canonical, name, overwrite=overwrite)

    def known_group(self, name: str) -> str | None:
        if not name:
            return None
        return self.group_canonical.get(name.lower())

    def known_prefab(self, name: str) -> str | None:
        if not name:
            return None
        clean = name[:-4] if name.lower().endswith(".epb") else name
        return self.prefab_canonical.get(clean.lower())

    def known_compound(self, name: str) -> str | None:
        return self.compound_canonical.get((name or "").lower())

    def known_eclass(self, name: str) -> str | None:
        return self.eclass_canonical.get((name or "").lower())

    def suggest(self, current: str, kind: str, limit: int = 20) -> list[str]:
        self.build()
        current_l = (current or "").lower()
        if kind == "compound":
            pool = list(self.compound_canonical.values())
        elif kind == "prefab":
            pool = list(self.prefab_canonical.values())
        elif kind == "eclass":
            pool = [n for n in self.eclass_canonical.values() if "fog" in n.lower() or "space" in n.lower()]
            if not pool:
                pool = list(self.eclass_canonical.values())
        else:
            pool = list(self.group_canonical.values())

        def rank(name: str):
            low = name.lower()
            return (_lev(low, current_l), 0 if low.startswith(current_l[:3]) else 1, low)

        ordered = sorted(set(pool), key=rank)
        if current_l:
            close = [n for n in ordered if _lev(n.lower(), current_l) <= 3]
            rest = [n for n in ordered if n not in close]
            ordered = close + rest
        return ordered[:limit]

"""Safe autofix. Only unambiguous edits. Everything else is skipped and logged."""

from __future__ import annotations

from backend.schema import SPAWN_TOKENS
from backend.yaml_engine import unknown_random_keys


def _lev(a: str, b: str) -> int:
    if a == b:
        return 0
    if abs(len(a) - len(b)) > 8:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _unique_close(current: str, suggestions: list[str], max_dist: int = 1) -> str | None:
    if not current or not suggestions:
        return None
    low = current.lower()
    case_hits = [s for s in suggestions if s.lower() == low and s != current]
    if len(case_hits) == 1:
        return case_hits[0]
    if any(s == current or s.lower() == low for s in suggestions):
        # Already canonical, or more than one case variant.
        exact = [s for s in suggestions if s.lower() == low]
        if len(exact) == 1 and exact[0] != current:
            return exact[0]
        return None
    ranked = sorted(suggestions, key=lambda s: _lev(s.lower(), low))
    best = ranked[0]
    dist = _lev(best.lower(), low)
    if dist > max_dist or dist == 0:
        return None
    ties = [s for s in ranked if _lev(s.lower(), low) == dist]
    if len(ties) != 1:
        return None
    return best


def apply_safe_fixes(ast, issues: list[dict], indexer, log=None) -> dict:
    repaired = []
    skipped = []

    dupes = ast.auto_resolve_all_duplicate_keys()
    if dupes:
        repaired.append(f"commented {dupes} duplicate key(s)")

    if not isinstance(ast.data, dict):
        return {"repaired": repaired, "skipped": skipped}

    # File-level flags first (indexes won't move).
    for issue in issues:
        if issue.get("type") == "missing_usefixed":
            if ast.set_use_fixed():
                repaired.append("set UseFixed: True")
            else:
                skipped.append("could not set UseFixed")

    # Per-entry edits from the bottom so deletions (we don't delete here) stay stable.
    # Case/near fixes don't delete, but a GroupName change doesn't shift indexes.
    entry_issues = [i for i in issues if i.get("type") not in {"missing_usefixed", "duplicate_key", "parse_halt"}]
    for issue in sorted(entry_issues, key=lambda i: i.get("index", -1), reverse=True):
        kind = issue.get("type")
        source = issue.get("source")
        index = issue.get("index", -1)
        suggestions = issue.get("suggestions") or []

        if kind == "random_needs_group":
            if len(suggestions) == 1:
                if ast.replace_target(source, index, suggestions[0], False):
                    repaired.append(f"{source}[{index}] GroupName set to {suggestions[0]}")
            else:
                skipped.append(f"{source}[{index}] needs a GroupName — pick one")
            continue

        if kind == "invalid_biome":
            bad = issue.get("bad_biomes") or []
            if len(bad) != 1:
                skipped.append(f"{source}[{index}] biome needs a manual choice ({bad})")
                continue
            match = _unique_close(bad[0], suggestions, max_dist=1)
            if not match:
                skipped.append(f"{source}[{index}] biome '{bad[0]}' has no unique match")
                continue
            if ast.correct_biome(source, index, match, bad):
                repaired.append(f"{source}[{index}] biome {bad[0]} → {match}")
            continue

        if kind in {"missing_group", "missing_prefab"}:
            current = issue.get("lookup") or ""
            match = _unique_close(current, suggestions, max_dist=1)
            if not match:
                skipped.append(f"{source}[{index}] '{current}' skipped — pick a replacement (not guessing)")
                continue
            if ast.replace_target(source, index, match, False):
                repaired.append(f"{source}[{index}] {current} → {match}")
            continue

        if kind == "missing_compound_name":
            current = issue.get("lookup") or ""
            match = _unique_close(current, suggestions, max_dist=1)
            if not match:
                skipped.append(f"{source}[{index}] compound '{current}' skipped — pick a name")
                continue
            if ast.replace_target(source, index, match, True):
                repaired.append(f"{source}[{index}] compound {current} → {match}")
            continue

        if kind == "unknown_key":
            keys = issue.get("unknown_keys") or []
            skipped.append(
                f"{source}[{index}] left unknown key(s) {keys} in place — strip them from the card if YamlDotNet rejects the file"
            )
            continue

        if kind in {"broken_spawn_ref", "spawn_order", "orphan_drone_setup", "malformed_pos", "invalid_space_fog", "missing_instance_flag", "bad_spawn_resource"}:
            skipped.append(f"{source}[{index}] {kind} left unchanged")
            continue

        skipped.append(f"{source}[{index}] {kind} not auto-edited")

    if log and (repaired or skipped):
        log.record(
            "safe-fix-summary",
            file=str(ast.path),
            reason=f"{len(repaired)} applied, {len(skipped)} left for you",
            before="; ".join(skipped)[:800],
            after="; ".join(repaired)[:800] or "(no changes)",
            level="INFO",
        )
    return {"repaired": repaired, "skipped": skipped, "unknown_check": unknown_random_keys, "tokens": SPAWN_TOKENS}

"""Tiny demo galaxy so the studio can be opened without a Steam install."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "demo"


def demo_paths() -> dict:
    return {
        "scenario_playfields": str(ROOT / "Scenario" / "Playfields"),
        "scenario_prefabs": str(ROOT / "Scenario" / "Prefabs"),
        "game_playfields": str(ROOT / "Game" / "Playfields"),
        "game_prefabs": str(ROOT / "Game" / "Prefabs"),
    }


def ensure_demo_prefabs() -> None:
    """Write ASCII-header .epb stand-ins. Real blueprints also store the group
    name as a plain string in the header, which is what the indexer harvests."""
    files = {
        ROOT / "Scenario" / "Prefabs" / "BA_Outpost.epb": b"EPB\x00BA_Outpost\x00JunkT1\x00",
        ROOT / "Scenario" / "Prefabs" / "BA_LateParent.epb": b"EPB\x00LateParent\x00",
        ROOT / "Scenario" / "Prefabs" / "BA_Child.epb": b"EPB\x00ChildTooSoon\x00",
        ROOT / "Game" / "Prefabs" / "CV_Ghost.epb": b"EPB\x00CV_Ghost\x00",
    }
    for path, payload in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(payload)

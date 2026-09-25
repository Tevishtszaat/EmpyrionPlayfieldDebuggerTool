"""Human-readable scan reports and edit transcripts.

Scan never writes playfields. Edits append one block per change to
logs/epd-edits.log so a bad autofix can be found and reversed from the
.epd_backups copy beside the playfield.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class EditLog:
    def __init__(self, base_dir: Path | None = None):
        root = Path(base_dir) if base_dir else Path.cwd()
        self.dir = root / "logs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.edits_path = self.dir / "epd-edits.log"
        self.edits_json = self.dir / "epd-edits.jsonl"
        self.scan_path = self.dir / "epd-last-scan.txt"

    def record(
        self,
        action: str,
        *,
        file: str = "",
        target: str = "",
        reason: str = "",
        before: str = "",
        after: str = "",
        level: str = "EDIT",
    ) -> dict:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [f"[{ts}] {level:<5} {action}"]
        if file:
            lines.append(f"  file:   {file}")
        if target:
            lines.append(f"  target: {target}")
        if reason:
            lines.append(f"  reason: {reason}")
        if before != "":
            lines.append(f"  before: {before}")
        if after != "":
            lines.append(f"  after:  {after}")
        lines.append("")
        block = "\n".join(lines)
        with open(self.edits_path, "a", encoding="utf-8") as handle:
            handle.write(block)
        rec = {
            "ts": ts,
            "level": level,
            "action": action,
            "file": file,
            "target": target,
            "reason": reason,
            "before": before,
            "after": after,
        }
        with open(self.edits_json, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def write_scan(self, text: str) -> None:
        self.scan_path.write_text(text, encoding="utf-8")

    def read_scan(self) -> str:
        if not self.scan_path.exists():
            return "No scan yet. Scan is read-only — it will not modify playfields."
        return self.scan_path.read_text(encoding="utf-8", errors="replace")

    def read_edits(self) -> str:
        if not self.edits_path.exists():
            return "No edits yet. Apply, Auto-fix safe, or Master Complete writes a block here for every change."
        return self.edits_path.read_text(encoding="utf-8", errors="replace")


def format_scan_report(files: list[dict]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "Empyrion Playfield Studio — scan report",
        now,
        "Writes during scan: none",
        "",
    ]
    totals: dict[str, int] = {}
    dirty = 0
    for item in files:
        issues = item.get("issues") or []
        if not issues:
            continue
        dirty += 1
        lines.append(f"{item.get('name', '?')}    {item.get('path', '')}")
        lines.append(f"  {len(issues)} issue(s)")
        for issue in issues:
            kind = issue.get("type") or "issue"
            totals[kind] = totals.get(kind, 0) + 1
            sev = issue.get("severity") or "INFO"
            source = issue.get("source") or ""
            index = issue.get("index")
            where = source if index in (None, -1) else f"{source}[{index}]"
            lines.append(f"  {sev:<7} {kind:<22} {where}")
            lines.append(f"          {issue.get('message', '')}")
        lines.append("")

    if not dirty:
        lines.append("No issues in the scanned playfields.")
        lines.append("")

    lines.append(f"Files scanned: {len(files)}")
    lines.append(f"Files with issues: {dirty}")
    lines.append(f"Files clean: {len(files) - dirty}")
    if totals:
        lines.append("Counts:")
        for kind in sorted(totals):
            lines.append(f"  {kind}: {totals[kind]}")
    lines.append("")
    return "\n".join(lines)

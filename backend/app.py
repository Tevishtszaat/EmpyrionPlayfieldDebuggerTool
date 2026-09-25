import asyncio
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.changelog import EditLog, format_scan_report
from backend.demo_data import demo_paths, ensure_demo_prefabs
from backend.indexer import AssetIndexer
from backend.repair import apply_safe_fixes
from backend.validator import PlayfieldValidator
from backend.yaml_engine import PlayfieldAST

APP_VERSION = "1.3.0"
ROOT = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    ROOT = Path(getattr(sys, "_MEIPASS", ROOT))

if platform.system() != "Windows":
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (min(hard, 65536), hard))
    except Exception:
        pass

app = FastAPI()
LOG = EditLog(ROOT if not getattr(sys, "frozen", False) else Path.cwd())
STATE = {
    "indexer": None,
    "ast": None,
    "active_file": "",
    "batch_files": [],
}

ensure_demo_prefabs()


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()},
    )


class ScanMasterRequest(BaseModel):
    scenario_playfields: str
    scenario_prefabs: str = ""
    game_playfields: str = ""
    game_prefabs: str = ""


class SelectFileRequest(BaseModel):
    file_path: str


class ReplaceRequest(BaseModel):
    source: str = "Random"
    poi_index: int
    new_prefab: str
    is_compound: bool = False


class PruneRequest(BaseModel):
    source: str = "Random"
    poi_index: int


class OpenFileRequest(BaseModel):
    file_path: str
    editor_choice: str = "default"
    custom_editor_cmd: str = ""


class FixDuplicateKeyRequest(BaseModel):
    line: int
    key: str


class ListDirRequest(BaseModel):
    current_path: str = ""
    os_mode: str = "win"


class FixBiomeRequest(BaseModel):
    source: str
    poi_index: int
    new_biome: str
    bad_biomes: list[str] = []


class StripKeyRequest(BaseModel):
    source: str
    poi_index: int
    key: str


def _active_ast() -> PlayfieldAST | None:
    return STATE.get("ast")


def _issues_for(ast: PlayfieldAST, path: str) -> list[dict]:
    if ast.duplicate_key_info:
        dup = ast.duplicate_key_info
        return [{
            "id": f"dup_{dup['line']}",
            "source": "Header",
            "index": -1,
            "type": "duplicate_key",
            "severity": "FATAL",
            "message": f"Duplicate key '{dup['key']}' on line {dup['line']}. The playfield will not load.",
            "current_value": f"Key: {dup['key']} (Line {dup['line']})",
            "line": dup["line"],
            "key_name": dup["key"],
            "suggestions": [],
        }]
    if ast.parse_error:
        return [{
            "id": "parse",
            "source": "Header",
            "index": -1,
            "type": "parse_halt",
            "severity": "FATAL",
            "message": f"YAML parse halt: {ast.parse_error}",
            "current_value": "Syntax halt",
            "suggestions": [],
        }]
    if not STATE["indexer"] or not isinstance(ast.data, dict):
        return []
    return PlayfieldValidator(ast.data, STATE["indexer"], path).validate()


def _remember(path: str, issues: list[dict], halt: str | None = None) -> None:
    for item in STATE["batch_files"]:
        if item["path"] == path:
            item["issues"] = issues
            item["issue_count"] = len(issues)
            item["halt_error"] = halt
            return


def find_all_playfields(root_dir: Path) -> list[Path]:
    valid = {"playfield.yaml", "playfield.yml", "playfield_dynamic.yaml", "playfield_static.yaml"}
    found = []
    seen = set()
    for dirpath, dirnames, filenames in os.walk(str(root_dir), topdown=True, followlinks=False):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != ".epd_backups"]
        for fname in filenames:
            if fname.lower() in valid:
                full = Path(dirpath) / fname
                key = str(full.resolve())
                if key not in seen:
                    seen.add(key)
                    found.append(full)
    found.sort(key=lambda p: (p.parent.name.lower(), p.name.lower()))
    return found


@app.get("/api/system/version")
def check_version():
    return {
        "current_version": APP_VERSION,
        "remote_version": APP_VERSION,
        "update_available": False,
        "commits_behind": 0,
    }


@app.get("/api/system/demo-paths")
def get_demo_paths():
    ensure_demo_prefabs()
    return demo_paths()


@app.get("/api/log/scan", response_class=PlainTextResponse)
def read_scan_log():
    return LOG.read_scan()


@app.get("/api/log/edits", response_class=PlainTextResponse)
def read_edit_log():
    return LOG.read_edits()


@app.post("/api/system/self-update")
def perform_self_update():
    try:
        if not (shutil.which("git") and Path(".git").exists()):
            raise HTTPException(status_code=400, detail="Not a Git repository.")
        pull = subprocess.run(["git", "pull"], capture_output=True, text=True, timeout=30)
        if pull.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Git pull failed: {pull.stderr}")
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], capture_output=True, timeout=60)
        LOG.record("self-update", reason=(pull.stdout or "git pull")[:500], level="INFO")
        script = str(Path("main.py").resolve())
        if platform.system() == "Windows":
            subprocess.Popen(f'cmd /c timeout /t 2 /nobreak >nul & "{sys.executable}" "{script}"', shell=True)
        else:
            subprocess.Popen(["bash", "-c", f'sleep 2 && "{sys.executable}" "{script}"'])
        threading.Timer(1.0, lambda: os._exit(0)).start()
        return {"status": "ok", "message": "Update complete. Relaunching."}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Update failed: {exc}") from exc


@app.post("/api/filesystem/list")
def list_filesystem(req: ListDirRequest):
    is_win = (req.os_mode == "win") or (platform.system() == "Windows")
    target = req.current_path.strip()
    if not target:
        if is_win:
            import string
            drives = []
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if os.path.exists(drive):
                    drives.append({"name": f"{letter}:", "path": drive, "is_dir": True})
            return {"current_path": "", "parent_path": "", "items": drives, "separator": "\\"}
        target = "/"
    path = Path(target)
    if not path.exists():
        path = Path.home()
    items = []
    try:
        with os.scandir(path) as scan:
            for entry in scan:
                try:
                    if entry.is_dir() and not entry.name.startswith(".") and entry.name != ".epd_backups":
                        items.append({"name": entry.name, "path": entry.path, "is_dir": True})
                except OSError:
                    pass
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Permission denied: {exc}") from exc
    items.sort(key=lambda row: row["name"].lower())
    parent = str(path.parent) if path.parent != path else ""
    return {
        "current_path": str(path),
        "parent_path": parent,
        "items": items,
        "separator": "\\" if is_win else "/",
    }


@app.post("/api/open-file")
def open_local_file(req: OpenFileRequest):
    path = Path(req.file_path).resolve()
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Target file does not exist.")
    is_win = platform.system() == "Windows"
    choice = req.editor_choice.lower().strip()
    custom = req.custom_editor_cmd.strip()
    try:
        if choice == "custom" and custom:
            exe = shutil.which(custom) or (str(Path(custom).resolve()) if Path(custom).is_file() else None)
            if not exe:
                raise HTTPException(status_code=400, detail=f"Executable '{custom}' not found.")
            subprocess.Popen([str(exe), str(path)])
            return {"status": "ok", "app": custom}
        if is_win:
            if choice == "notepad++":
                for candidate in (
                    r"C:\Program Files\Notepad++\notepad++.exe",
                    r"C:\Program Files (x86)\Notepad++\notepad++.exe",
                    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Notepad++\notepad++.exe"),
                ):
                    if os.path.exists(candidate):
                        args = [candidate]
                        line = _line_hint(req.file_path)
                        if line:
                            args.append(f"-n{line}")
                        args.append(str(path))
                        subprocess.Popen(args)
                        return {"status": "ok", "app": "Notepad++"}
            elif choice == "code":
                code = shutil.which("code.cmd") or shutil.which("code")
                if code:
                    args = [code]
                    line = _line_hint(req.file_path)
                    if line:
                        args.append("--goto")
                        args.append(f"{path}:{line}")
                    else:
                        args.append(str(path))
                    subprocess.Popen(args)
                    return {"status": "ok", "app": "VS Code"}
            elif choice == "notepad":
                subprocess.Popen(["notepad.exe", str(path)])
                return {"status": "ok", "app": "Notepad"}
            os.startfile(str(path))
            return {"status": "ok", "app": "System Default"}
        if choice == "code":
            code = shutil.which("code")
            if code:
                subprocess.Popen([code, str(path)])
                return {"status": "ok", "app": "VS Code"}
        if choice in {"gedit", "kate", "mousepad", "subl"}:
            exe = shutil.which(choice)
            if exe:
                subprocess.Popen([exe, str(path)])
                return {"status": "ok", "app": choice}
        subprocess.Popen(["xdg-open", str(path)])
        return {"status": "ok", "app": "xdg-open"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not launch editor: {exc}") from exc


def _line_hint(path: str) -> int | None:
    ast = STATE.get("ast")
    if not ast or STATE.get("active_file") != path:
        return None
    return None


@app.post("/api/fix-duplicate-key")
def fix_duplicate_key(req: FixDuplicateKeyRequest):
    ast = _active_ast()
    if not ast:
        raise HTTPException(status_code=400, detail="No playfield is open.")
    ast.log = LOG
    ast.remove_duplicate_key_line(req.line, req.key)
    ast.auto_resolve_all_duplicate_keys()
    issues = _issues_for(ast, STATE["active_file"])
    _remember(STATE["active_file"], issues, ast.parse_error)
    return {"status": "ok", "issues": issues}


@app.post("/api/batch/stream-scan")
async def stream_scan(req: ScanMasterRequest):
    root = Path(req.scenario_playfields.strip().strip('"'))
    if not root.exists():
        raise HTTPException(status_code=404, detail="Scenario Playfields directory does not exist.")
    indexer = AssetIndexer(
        str(root),
        req.scenario_prefabs.strip().strip('"'),
        req.game_playfields.strip().strip('"'),
        req.game_prefabs.strip().strip('"'),
    )
    indexer.build()
    STATE["indexer"] = indexer
    found = find_all_playfields(root)
    total = len(found)

    async def event_generator():
        summary = []
        for idx, path in enumerate(found):
            halt = None
            try:
                ast = PlayfieldAST(str(path))
                issues = _issues_for(ast, str(path))
                if ast.parse_error and not ast.duplicate_key_info:
                    halt = ast.parse_error[:160]
            except Exception as exc:
                halt = str(exc)[:160]
                issues = [{
                    "id": f"err_{idx}",
                    "type": "runtime_halt",
                    "severity": "FATAL",
                    "message": f"Halting error: {halt}",
                    "current_value": "OS halt",
                    "suggestions": [],
                }]
            item = {
                "path": str(path),
                "name": path.parent.name,
                "filename": path.name,
                "issue_count": len(issues),
                "halt_error": halt,
                "issues": issues,
            }
            summary.append(item)
            yield f"data: {json.dumps({'type': 'progress', 'current': idx + 1, 'total': total, 'file': item})}\n\n"
            await asyncio.sleep(0.002)

        STATE["batch_files"] = summary
        report = format_scan_report(summary)
        LOG.write_scan(report)
        dirty = sum(1 for row in summary if row["issue_count"])
        LOG.record(
            "scan",
            reason=f"{total} playfields, {dirty} with issues, 0 files written",
            level="SCAN",
        )
        first = next((row for row in summary if row["issue_count"] > 0), summary[0] if summary else None)
        if first:
            STATE["active_file"] = first["path"]
            try:
                STATE["ast"] = PlayfieldAST(first["path"], LOG)
            except Exception:
                STATE["ast"] = None
        yield f"data: {json.dumps({'type': 'complete', 'active_file': STATE['active_file']})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/batch/stream-master-complete")
async def stream_master_complete():
    if not STATE["indexer"] or not STATE["batch_files"]:
        raise HTTPException(status_code=400, detail="Scan directories before running Master Complete.")
    total = len(STATE["batch_files"])

    async def complete_generator():
        changed_files = 0
        for idx, item in enumerate(STATE["batch_files"]):
            try:
                before_text = Path(item["path"]).read_text(encoding="utf-8-sig")
                ast = PlayfieldAST(item["path"], LOG)
                if ast.duplicate_key_info or (isinstance(ast.data, dict)):
                    issues = _issues_for(ast, item["path"])
                    apply_safe_fixes(ast, issues, STATE["indexer"], LOG)
                ast.load()
                issues = _issues_for(ast, item["path"])
                after_text = Path(item["path"]).read_text(encoding="utf-8-sig")
                if before_text != after_text:
                    changed_files += 1
                item["issues"] = issues
                item["issue_count"] = len(issues)
                item["halt_error"] = ast.parse_error
            except Exception as exc:
                LOG.record(
                    "master-complete-failed",
                    file=item["path"],
                    reason=f"{type(exc).__name__}: {exc}",
                    level="ERROR",
                )
            yield f"data: {json.dumps({'type': 'progress', 'current': idx + 1, 'total': total, 'remaining': total - (idx + 1), 'name': item['name'], 'repaired_count': changed_files})}\n\n"
            await asyncio.sleep(0.002)

        if STATE["active_file"]:
            try:
                STATE["ast"] = PlayfieldAST(STATE["active_file"], LOG)
            except Exception:
                STATE["ast"] = None
        active = []
        if STATE["ast"]:
            active = _issues_for(STATE["ast"], STATE["active_file"])
            _remember(STATE["active_file"], active, STATE["ast"].parse_error)
        LOG.write_scan(format_scan_report(STATE["batch_files"]))
        LOG.record(
            "master-complete",
            reason=f"{changed_files} file(s) changed. Remaining issues were left in place — see the scan report.",
            level="INFO",
        )
        yield f"data: {json.dumps({'type': 'complete', 'repaired_count': changed_files, 'active_issues': active, 'batch_files': STATE['batch_files']})}\n\n"

    return StreamingResponse(
        complete_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/batch/select")
def select_file(req: SelectFileRequest):
    STATE["active_file"] = req.file_path
    ast = PlayfieldAST(req.file_path, LOG)
    STATE["ast"] = ast
    issues = _issues_for(ast, req.file_path)
    _remember(req.file_path, issues, ast.parse_error)
    return {
        "active_file": req.file_path,
        "name": Path(req.file_path).parent.name,
        "issues": issues,
    }


def _refresh_active() -> list[dict]:
    ast = _active_ast()
    if not ast:
        return []
    ast.load()
    issues = _issues_for(ast, STATE["active_file"])
    _remember(STATE["active_file"], issues, ast.parse_error)
    return issues


@app.post("/api/replace-poi")
def replace_poi(req: ReplaceRequest):
    ast = _active_ast()
    if not ast:
        return {"issues": []}
    ast.log = LOG
    ast.replace_target(req.source, req.poi_index, req.new_prefab, req.is_compound)
    return {"issues": _refresh_active()}


@app.post("/api/remove-poi")
def remove_poi(req: PruneRequest):
    ast = _active_ast()
    if not ast:
        return {"issues": []}
    ast.log = LOG
    ast.remove_target(req.source, req.poi_index)
    return {"issues": _refresh_active()}


@app.post("/api/fix-biome")
def fix_biome(req: FixBiomeRequest):
    ast = _active_ast()
    if not ast:
        return {"issues": []}
    ast.log = LOG
    ast.correct_biome(req.source, req.poi_index, req.new_biome, req.bad_biomes)
    return {"issues": _refresh_active()}


@app.post("/api/strip-key")
def strip_key(req: StripKeyRequest):
    ast = _active_ast()
    if not ast:
        return {"issues": []}
    ast.log = LOG
    ast.strip_key(req.source, req.poi_index, req.key)
    return {"issues": _refresh_active()}


@app.post("/api/set-use-fixed")
def set_use_fixed():
    ast = _active_ast()
    if not ast:
        return {"issues": []}
    ast.log = LOG
    ast.set_use_fixed()
    return {"issues": _refresh_active()}


@app.post("/api/autocomplete")
def autocomplete():
    ast = _active_ast()
    if not ast or not STATE["indexer"]:
        return {"issues": [], "repaired": [], "skipped": []}
    ast.log = LOG
    if ast.parse_error and not ast.duplicate_key_info:
        return {"issues": _issues_for(ast, STATE["active_file"]), "repaired": [], "skipped": ["parse error"]}
    issues = _issues_for(ast, STATE["active_file"])
    summary = apply_safe_fixes(ast, issues, STATE["indexer"], LOG)
    fresh = _refresh_active()
    return {"issues": fresh, "repaired": summary["repaired"], "skipped": summary["skipped"]}


app.mount("/", StaticFiles(directory=str(ROOT / "frontend" / "static"), html=True), name="static")

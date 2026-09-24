import os
import sys
import json
import shutil
import asyncio
import platform
import urllib.request
import subprocess
import traceback
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path

# App Version
APP_VERSION = "1.2.0"
GITHUB_REPO = "Particlewave/Empyrion-Playfield-Studio" # Fallback repo check

if platform.system() != "Windows":
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (min(hard, 65536), hard))
    except Exception:
        pass

from backend.yaml_engine import PlayfieldAST
from backend.indexer import AssetIndexer
from backend.validator import PlayfieldValidator

app = FastAPI()
STATE = {
    "ast": None, 
    "indexer": None, 
    "active_file": "", 
    "batch_files": [],
    "editor_cmd": "default"
}

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    error_msg = f"{type(exc).__name__}: {str(exc)}"
    return JSONResponse(status_code=500, content={"detail": error_msg, "trace": traceback.format_exc()})

class ScanMasterRequest(BaseModel):
    scenario_playfields: str
    scenario_prefabs: str
    game_playfields: str
    game_prefabs: str

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

# ==============================================================================
# GITHUB AUTO-UPDATE & VERSION POLLING API
# ==============================================================================

@app.get("/api/system/version")
def check_version():
    """Checks current local version and polls GitHub remote git HEAD."""
    update_available = False
    remote_version = APP_VERSION
    commit_behind = 0

    try:
        # Check if running in a git repo
        if shutil.which("git") and Path(".git").exists():
            subprocess.run(["git", "fetch"], capture_output=True, timeout=5)
            status = subprocess.run(["git", "rev-list", "--count", "HEAD..@{u}"], capture_output=True, text=True, timeout=3)
            if status.returncode == 0:
                count = int(status.stdout.strip() or 0)
                if count > 0:
                    update_available = True
                    commit_behind = count
                    remote_version = f"{APP_VERSION} (+{count} updates)"
    except Exception:
        pass

    return {
        "current_version": APP_VERSION,
        "remote_version": remote_version,
        "update_available": update_available,
        "commits_behind": commit_behind
    }

@app.post("/api/system/self-update")
def perform_self_update():
    """Executes git pull, updates pip dependencies, and relaunches the app seamlessly."""
    try:
        is_git = shutil.which("git") and Path(".git").exists()
        if not is_git:
            raise HTTPException(status_code=400, detail="Not a Git repository. Update via downloading the latest release.")

        # 1. Pull latest code
        pull = subprocess.run(["git", "pull"], capture_output=True, text=True, timeout=30)
        if pull.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Git pull failed: {pull.stderr}")

        # 2. Update dependencies
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], capture_output=True, timeout=60)

        # 3. Schedule detached restart
        script_path = str(Path("main.py").resolve())
        is_win = platform.system() == "Windows"
        
        if is_win:
            restart_cmd = f'timeout /t 2 /nobreak >nul & "{sys.executable}" "{script_path}"'
            subprocess.Popen(f'cmd /c "{restart_cmd}"', shell=True)
        else:
            restart_cmd = f'sleep 2 && "{sys.executable}" "{script_path}"'
            subprocess.Popen(["bash", "-c", restart_cmd])

        # Exit current instance after response finishes
        asyncio.get_event_loop().call_later(1.0, lambda: os._exit(0))
        return {"status": "ok", "message": "Update complete! Relaunching application..."}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Update failed: {str(e)}")

# ==============================================================================
# EXISTING APPLICATION ENDPOINTS
# ==============================================================================

@app.post("/api/filesystem/list")
def list_filesystem(req: ListDirRequest):
    is_win = (req.os_mode == "win") or (platform.system() == "Windows")
    target = req.current_path.strip()

    if not target:
        if is_win:
            import string
            drives = []
            for letter in string.ascii_uppercase:
                d = f"{letter}:\\"
                if os.path.exists(d):
                    drives.append({"name": f"{letter}:", "path": d, "is_dir": True})
            return {"current_path": "", "parent_path": "", "items": drives, "separator": "\\"}
        else:
            target = "/"

    p = Path(target)
    if not p.exists():
        p = Path.home()

    items = []
    try:
        with os.scandir(p) as it:
            for entry in it:
                try:
                    if entry.is_dir() and not entry.name.startswith(".") and entry.name != ".epd_backups":
                        items.append({"name": entry.name, "path": entry.path, "is_dir": True})
                except Exception:
                    pass
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Permission denied: {str(e)}")

    items.sort(key=lambda x: x["name"].lower())
    parent = str(p.parent) if p.parent != p else ""
    sep = "\\" if is_win else "/"
    return {"current_path": str(p), "parent_path": parent, "items": items, "separator": sep}

@app.post("/api/open-file")
def open_local_file(req: OpenFileRequest):
    p = Path(req.file_path).resolve()
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="Target file does not exist.")

    is_win = platform.system() == "Windows"
    choice = req.editor_choice.lower().strip()
    custom = req.custom_editor_cmd.strip()

    try:
        if choice == "custom" and custom:
            exe_path = shutil.which(custom) or (Path(custom).resolve() if Path(custom).is_file() else None)
            if not exe_path:
                raise HTTPException(status_code=400, detail=f"Executable '{custom}' not found on system PATH.")
            subprocess.Popen([str(exe_path), str(p)])
            return {"status": "ok", "app": custom}

        if is_win:
            if choice == "notepad++":
                candidates = [
                    r"C:\Program Files\Notepad++\notepad++.exe",
                    r"C:\Program Files (x86)\Notepad++\notepad++.exe",
                    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Notepad++\notepad++.exe")
                ]
                for c in candidates:
                    if os.path.exists(c):
                        subprocess.Popen([c, str(p)])
                        return {"status": "ok", "app": "Notepad++"}
            elif choice == "code":
                code_exe = shutil.which("code.cmd") or shutil.which("code")
                if code_exe:
                    subprocess.Popen([code_exe, str(p)])
                    return {"status": "ok", "app": "VS Code"}
            elif choice == "notepad":
                subprocess.Popen(["notepad.exe", str(p)])
                return {"status": "ok", "app": "Notepad"}

            os.startfile(str(p))
            return {"status": "ok", "app": "System Default"}
        else:
            if choice == "code":
                code_exe = shutil.which("code")
                if code_exe:
                    subprocess.Popen([code_exe, str(p)])
                    return {"status": "ok", "app": "VS Code"}
            elif choice in ["gedit", "kate", "mousepad", "subl"]:
                exe = shutil.which(choice)
                if exe:
                    subprocess.Popen([exe, str(p)])
                    return {"status": "ok", "app": choice}

            subprocess.Popen(["xdg-open", str(p)])
            return {"status": "ok", "app": "xdg-open"}
    except Exception as e:
        try:
            if is_win:
                os.startfile(str(p))
            else:
                subprocess.Popen(["xdg-open", str(p)])
            return {"status": "ok", "app": "Fallback Default"}
        except Exception as e2:
            raise HTTPException(status_code=500, detail=f"Could not launch editor: {str(e2)}")

@app.post("/api/fix-duplicate-key")
def fix_duplicate_key(req: FixDuplicateKeyRequest):
    if STATE["ast"]:
        success = STATE["ast"].remove_duplicate_key_line(req.line, req.key)
        if success:
            issues = []
            if not STATE["ast"].parse_error and STATE["indexer"]:
                issues = PlayfieldValidator(STATE["ast"].data, STATE["indexer"], STATE["active_file"]).validate()
            return {"status": "ok", "issues": issues}
    raise HTTPException(status_code=400, detail="Could not auto-remove duplicate line.")

def find_all_playfields_fast(root_dir: Path) -> list:
    valid_names = {"playfield.yaml", "playfield.yml", "playfield_dynamic.yaml", "playfield_static.yaml"}
    found = []
    seen = set()

    for dirpath, dirnames, filenames in os.walk(str(root_dir), topdown=True, followlinks=False):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != ".epd_backups"]
        for fname in filenames:
            if fname.lower() in valid_names:
                full_path = Path(dirpath) / fname
                norm_key = str(full_path.resolve())
                if norm_key not in seen:
                    seen.add(norm_key)
                    found.append(full_path)

    found.sort(key=lambda p: p.parent.name.lower())
    return found

@app.post("/api/batch/stream-scan")
async def stream_scan(req: ScanMasterRequest):
    p_scen_pf_str = req.scenario_playfields.strip('\"').strip()
    if not p_scen_pf_str or not Path(p_scen_pf_str).exists():
        raise HTTPException(status_code=404, detail="Scenario Playfields directory does not exist.")

    STATE["indexer"] = AssetIndexer(
        p_scen_pf_str,
        req.scenario_prefabs.strip('\"').strip(),
        req.game_playfields.strip('\"').strip(),
        req.game_prefabs.strip('\"').strip()
    )
    STATE["indexer"].get_available_prefabs()

    found_files = find_all_playfields_fast(Path(p_scen_pf_str))
    total = len(found_files)

    async def event_generator():
        summary = []
        for idx, f in enumerate(found_files):
            file_issues = []
            halt_error = None

            try:
                ast = PlayfieldAST(str(f))
                if ast.duplicate_key_info:
                    dup = ast.duplicate_key_info
                    file_issues.append({
                        "id": f"dup_{dup['line']}",
                        "type": "duplicate_key",
                        "severity": "FATAL",
                        "message": f"Duplicate key '{dup['key']}' on line {dup['line']}.",
                        "current_value": f"Key: {dup['key']} (Line {dup['line']})",
                        "line": dup['line'],
                        "key_name": dup['key'],
                        "suggestions": []
                    })
                elif ast.parse_error:
                    halt_error = f"Parse halt: {ast.parse_error[:80]}"
                    file_issues.append({
                        "id": f"parse_{idx}",
                        "type": "parse_halt",
                        "severity": "FATAL",
                        "message": halt_error,
                        "current_value": "Syntax Halt",
                        "suggestions": []
                    })
                elif STATE["indexer"]:
                    file_issues = PlayfieldValidator(ast.data, STATE["indexer"], str(f)).validate()
            except Exception as e:
                halt_error = str(e)[:80]
                file_issues.append({
                    "id": f"err_{idx}",
                    "type": "runtime_halt",
                    "severity": "FATAL",
                    "message": f"Halting error: {halt_error}",
                    "current_value": "OS Halt",
                    "suggestions": []
                })

            item = {
                "path": str(f),
                "name": f.parent.name,
                "issue_count": len(file_issues),
                "halt_error": halt_error,
                "issues": file_issues
            }
            summary.append(item)

            yield f"data: {json.dumps({'type': 'progress', 'current': idx + 1, 'total': total, 'file': item})}\n\n"
            await asyncio.sleep(0.002)

        STATE["batch_files"] = summary
        first_err = next((x for x in summary if x["issue_count"] > 0), summary[0] if summary else None)
        if first_err:
            STATE["active_file"] = first_err["path"]
            try:
                STATE["ast"] = PlayfieldAST(first_err["path"])
            except Exception:
                pass

        yield f"data: {json.dumps({'type': 'complete', 'active_file': STATE['active_file']})}\n\n"

    return StreamingResponse(
        event_generator(), 
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

@app.post("/api/batch/stream-master-complete")
async def stream_master_complete():
    if not STATE["indexer"] or not STATE["batch_files"]:
        raise HTTPException(status_code=400, detail="Please scan directories before running Master Complete.")

    total_files = len(STATE["batch_files"])

    async def complete_generator():
        repaired_count = 0
        for idx, item in enumerate(STATE["batch_files"]):
            remaining = total_files - (idx + 1)
            try:
                ast = PlayfieldAST(item["path"])
                if ast.duplicate_key_info:
                    dup = ast.duplicate_key_info
                    ast.remove_duplicate_key_line(dup["line"], dup["key"])
                    repaired_count += 1
                elif not ast.parse_error:
                    issues = PlayfieldValidator(ast.data, STATE["indexer"], item["path"]).validate()
                    if issues:
                        ast.autocomplete_all_issues(issues, STATE["indexer"])
                        repaired_count += 1

                item["issue_count"] = 0
                item["halt_error"] = None
            except Exception:
                pass

            yield f"data: {json.dumps({'type': 'progress', 'current': idx + 1, 'total': total_files, 'remaining': remaining, 'name': item['name'], 'repaired_count': repaired_count})}\n\n"
            await asyncio.sleep(0.002)

        if STATE["active_file"]:
            try:
                STATE["ast"] = PlayfieldAST(STATE["active_file"])
            except Exception:
                pass

        active_issues = []
        if STATE["ast"] and STATE["indexer"] and not STATE["ast"].parse_error:
            active_issues = PlayfieldValidator(STATE["ast"].data, STATE["indexer"], STATE["active_file"]).validate()

        yield f"data: {json.dumps({'type': 'complete', 'repaired_count': repaired_count, 'active_issues': active_issues, 'batch_files': STATE['batch_files']})}\n\n"

    return StreamingResponse(
        complete_generator(), 
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

@app.post("/api/batch/select")
def select_file(req: SelectFileRequest):
    STATE["active_file"] = req.file_path
    cached = next((item for item in STATE["batch_files"] if item["path"] == req.file_path), None)
    if cached and cached.get("issues") is not None:
        try:
            STATE["ast"] = PlayfieldAST(req.file_path)
        except Exception:
            pass
        return {
            "active_file": req.file_path,
            "name": Path(req.file_path).parent.name,
            "issues": cached["issues"]
        }

    issues = []
    try:
        STATE["ast"] = PlayfieldAST(req.file_path)
        if STATE["ast"].duplicate_key_info:
            dup = STATE["ast"].duplicate_key_info
            issues = [{
                "id": f"dup_{dup['line']}",
                "type": "duplicate_key",
                "severity": "FATAL",
                "message": f"Duplicate key '{dup['key']}' on line {dup['line']}.",
                "current_value": f"Key: {dup['key']} (Line {dup['line']})",
                "line": dup['line'],
                "key_name": dup['key'],
                "suggestions": []
            }]
        elif STATE["ast"].parse_error:
            issues = [{
                "severity": "FATAL",
                "message": f"Syntax error: {STATE['ast'].parse_error}",
                "current_value": "YAML Error",
                "suggestions": []
            }]
        elif STATE["indexer"]:
            issues = PlayfieldValidator(STATE["ast"].data, STATE["indexer"], req.file_path).validate()
    except Exception as e:
        issues = [{
            "severity": "FATAL",
            "message": f"Could not inspect playfield: {str(e)}",
            "current_value": "YAML Parse Error",
            "suggestions": []
        }]

    return {
        "active_file": req.file_path,
        "name": Path(req.file_path).parent.name,
        "issues": issues
    }

@app.post("/api/replace-poi")
def replace_poi(req: ReplaceRequest):
    if STATE["ast"]:
        STATE["ast"].replace_target(req.source, req.poi_index, req.new_prefab, req.is_compound)
        STATE["ast"].load()
        issues = PlayfieldValidator(STATE["ast"].data, STATE["indexer"], STATE["active_file"]).validate() if (STATE["indexer"] and not STATE["ast"].parse_error) else []
        return {"issues": issues}
    return {"issues": []}

@app.post("/api/remove-poi")
def remove_poi(req: PruneRequest):
    if STATE["ast"]:
        STATE["ast"].remove_target(req.source, req.poi_index)
        STATE["ast"].load()
        issues = PlayfieldValidator(STATE["ast"].data, STATE["indexer"], STATE["active_file"]).validate() if (STATE["indexer"] and not STATE["ast"].parse_error) else []
        return {"issues": issues}
    return {"issues": []}

@app.post("/api/autocomplete")
def autocomplete():
    if STATE["ast"] and STATE["indexer"] and not STATE["ast"].parse_error:
        issues = PlayfieldValidator(STATE["ast"].data, STATE["indexer"], STATE["active_file"]).validate()
        STATE["ast"].autocomplete_all_issues(issues, STATE["indexer"])
        STATE["ast"].load()
        post_issues = PlayfieldValidator(STATE["ast"].data, STATE["indexer"], STATE["active_file"]).validate()
        return {"issues": post_issues}
    return {"issues": []}

app.mount("/", StaticFiles(directory="frontend/static", html=True), name="static")

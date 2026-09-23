import os
import json
import asyncio
import subprocess
import traceback
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
from backend.yaml_engine import PlayfieldAST
from backend.indexer import AssetIndexer
from backend.validator import PlayfieldValidator

app = FastAPI()
STATE = {"ast": None, "indexer": None, "active_file": "", "batch_files": []}

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

class FixDuplicateKeyRequest(BaseModel):
    line: int
    key: str

def find_notepad_plus_plus() -> str:
    candidates = [
        r"C:\Program Files\Notepad++\notepad++.exe",
        r"C:\Program Files (x86)\Notepad++\notepad++.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Notepad++\notepad++.exe")
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return ""

@app.post("/api/open-file")
def open_local_file(req: OpenFileRequest):
    p = Path(req.file_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"File does not exist: {p}")

    npp = find_notepad_plus_plus()
    try:
        if npp:
            subprocess.Popen([npp, str(p)])
            return {"status": "ok", "app": "Notepad++"}
        else:
            os.startfile(str(p))
            return {"status": "ok", "app": "System Default"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not open editor: {str(e)}")

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
                norm = str(full_path).lower()
                if norm not in seen:
                    seen.add(norm)
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
                    "current_value": "Crash",
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
            await asyncio.sleep(0.005)

        STATE["batch_files"] = summary
        first_err = next((x for x in summary if x["issue_count"] > 0), summary[0] if summary else None)
        if first_err:
            STATE["active_file"] = first_err["path"]
            STATE["ast"] = PlayfieldAST(first_err["path"])

        yield f"data: {json.dumps({'type': 'complete', 'active_file': STATE['active_file']})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/api/batch/stream-master-complete")
async def stream_master_complete():
    """Streams Master Complete repair progress with countdown from total files."""
    if not STATE["indexer"] or not STATE["batch_files"]:
        raise HTTPException(status_code=400, detail="Please scan directories before running Master Complete.")

    total_files = len(STATE["batch_files"])

    async def complete_generator():
        repaired_count = 0
        for idx, item in enumerate(STATE["batch_files"]):
            remaining = total_files - (idx + 1)
            try:
                ast = PlayfieldAST(item["path"])
                # 1. Deduplicate lines if needed
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
            await asyncio.sleep(0.005)

        if STATE["active_file"]:
            STATE["ast"] = PlayfieldAST(STATE["active_file"])

        active_issues = []
        if STATE["ast"] and STATE["indexer"] and not STATE["ast"].parse_error:
            active_issues = PlayfieldValidator(STATE["ast"].data, STATE["indexer"], STATE["active_file"]).validate()

        yield f"data: {json.dumps({'type': 'complete', 'repaired_count': repaired_count, 'active_issues': active_issues, 'batch_files': STATE['batch_files']})}\n\n"

    return StreamingResponse(complete_generator(), media_type="text/event-stream")

@app.post("/api/batch/select")
def select_file(req: SelectFileRequest):
    STATE["active_file"] = req.file_path
    cached = next((item for item in STATE["batch_files"] if item["path"] == req.file_path), None)
    if cached and cached.get("issues") is not None:
        STATE["ast"] = PlayfieldAST(req.file_path)
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

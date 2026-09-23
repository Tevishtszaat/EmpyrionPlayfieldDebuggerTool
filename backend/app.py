import traceback
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
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
    """Guarantees a clean JSON response on any unexpected error instead of plain-text 500."""
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
    poi_index: int
    new_prefab: str
    group_name: str = ""

class PruneRequest(BaseModel):
    poi_index: int

@app.post("/api/batch/scan")
def scan_master(req: ScanMasterRequest):
    p_scen_pf_str = req.scenario_playfields.strip('\"').strip()
    if not p_scen_pf_str:
        raise HTTPException(status_code=400, detail="Scenario Playfields path cannot be empty.")

    p_scen_pf = Path(p_scen_pf_str)
    if not p_scen_pf.exists():
        raise HTTPException(status_code=404, detail=f"Scenario Playfields directory does not exist: {p_scen_pf}")

    STATE["indexer"] = AssetIndexer(
        p_scen_pf_str,
        req.scenario_prefabs.strip('\"').strip(),
        req.game_playfields.strip('\"').strip(),
        req.game_prefabs.strip('\"').strip()
    )
    prefabs = STATE["indexer"].get_available_prefabs()
    fallback_suggestions = [p["name"] for p in prefabs]

    found_playfields = list(p_scen_pf.rglob("playfield.yaml"))
    if not found_playfields:
        raise HTTPException(status_code=404, detail=f"No playfield.yaml files found in: {p_scen_pf}")

    summary = []
    for f in found_playfields:
        try:
            ast = PlayfieldAST(str(f))
            issues = PlayfieldValidator(ast.data, STATE["indexer"]).validate()
            summary.append({
                "path": str(f),
                "name": f.parent.name,
                "issue_count": len(issues),
                "issues": issues
            })
        except Exception as e:
            summary.append({
                "path": str(f),
                "name": f.parent.name,
                "issue_count": 1,
                "issues": [{
                    "severity": "FATAL",
                    "message": f"Syntax error: {str(e)}",
                    "type": "err",
                    "suggestions": fallback_suggestions
                }]
            })

    STATE["batch_files"] = summary

    first_with_errors = next((item for item in summary if item["issue_count"] > 0), None)
    if first_with_errors:
        STATE["active_file"] = first_with_errors["path"]
        STATE["ast"] = PlayfieldAST(first_with_errors["path"])
    elif summary:
        STATE["active_file"] = summary[0]["path"]
        STATE["ast"] = PlayfieldAST(summary[0]["path"])

    return {"files": summary, "active_file": STATE["active_file"]}

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

    STATE["ast"] = PlayfieldAST(req.file_path)
    issues = PlayfieldValidator(STATE["ast"].data, STATE["indexer"]).validate() if STATE["indexer"] else []
    return {"active_file": req.file_path, "name": Path(req.file_path).parent.name, "issues": issues}

@app.post("/api/replace-poi")
def replace_poi(req: ReplaceRequest):
    if STATE["ast"]:
        STATE["ast"].replace_poi_prefab(req.poi_index, req.new_prefab)
        issues = PlayfieldValidator(STATE["ast"].data, STATE["indexer"]).validate() if STATE["indexer"] else []
        return {"issues": issues}
    return {"issues": []}

@app.post("/api/remove-poi")
def remove_poi(req: PruneRequest):
    if STATE["ast"]:
        STATE["ast"].remove_poi_entry(req.poi_index)
        issues = PlayfieldValidator(STATE["ast"].data, STATE["indexer"]).validate() if STATE["indexer"] else []
        return {"issues": issues}
    return {"issues": []}

@app.post("/api/autocomplete")
def autocomplete():
    if STATE["ast"] and STATE["indexer"]:
        issues = PlayfieldValidator(STATE["ast"].data, STATE["indexer"]).validate()
        STATE["ast"].autocomplete_all_issues(issues, STATE["indexer"])
        post_issues = PlayfieldValidator(STATE["ast"].data, STATE["indexer"]).validate()
        return {"issues": post_issues}
    return {"issues": []}

@app.post("/api/batch/master-complete")
def master_complete():
    if STATE["indexer"]:
        for item in STATE["batch_files"]:
            if item["issue_count"] > 0:
                ast = PlayfieldAST(item["path"])
                issues = PlayfieldValidator(ast.data, STATE["indexer"]).validate()
                ast.autocomplete_all_issues(issues, STATE["indexer"])
                post_issues = PlayfieldValidator(ast.data, STATE["indexer"]).validate()
                item["issue_count"] = len(post_issues)
                item["issues"] = post_issues
        if STATE["active_file"]:
            STATE["ast"] = PlayfieldAST(STATE["active_file"])
    return {
        "batch_files": STATE["batch_files"],
        "active_issues": PlayfieldValidator(STATE["ast"].data, STATE["indexer"]).validate() if (STATE["ast"] and STATE["indexer"]) else []
    }

app.mount("/", StaticFiles(directory="frontend/static", html=True), name="static")

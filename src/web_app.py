"""Toolsmith web app — runs inside the CreateOS micro-VM, served over CreateOS ingress.

    uvicorn src.web_app:app --host 0.0.0.0 --port 8080

Pages:  /            landing
        /forge.html  the orchestrator: give the agent a goal, watch it forge
        /use.html    use any tool the agent has forged
"""
from __future__ import annotations

import json
import secrets
import subprocess
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config, forge
from .llm import LLM

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
MODELS = {
    "anthropic/claude-sonnet-5.5": "Claude Sonnet 5.5 — most reliable",
    "google/gemini-3.5-flash-lite": "Gemini 3.5 Flash Lite — fast, fixes itself more",
}
EGRESS = ["api.tokenrouter.com", "api.stripe.com", "pypi.org", "files.pythonhosted.org"]

app = FastAPI(title="Toolsmith")
app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")

JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
SLOTS = threading.Semaphore(2)          # 1 vCPU VM: at most two forges at once
RECENT: list[float] = []                 # public URL: cap forges per hour
MAX_PER_HOUR = 40


def _page(name: str) -> HTMLResponse:
    return HTMLResponse((WEB / name).read_text(encoding="utf-8"))


@app.get("/")
def home():
    return _page("index.html")


@app.get("/index.html")
def home2():
    return _page("index.html")


@app.get("/forge.html")
def forge_page():
    return _page("forge.html")


@app.get("/use.html")
def use_page():
    return _page("use.html")


@app.get("/app.html")
def old_app():
    return RedirectResponse("/forge.html")


@app.get("/api/meta")
def meta():
    return {"models": MODELS, "default_model": config.MODEL if config.MODEL in MODELS
            else "anthropic/claude-sonnet-5.5", "egress": EGRESS,
            "tools": len(forge.list_tools()), "approval_cap_cents": config.APPROVAL_CAP_CENTS}


# ---------- forging ----------
@app.post("/api/forge")
async def start_forge(request: Request):
    body = await request.json()
    goal = (body.get("goal") or "").strip()
    model = body.get("model") or "anthropic/claude-sonnet-5.5"
    if not goal or len(goal) > 400:
        raise HTTPException(400, "Describe the tool in 1–400 characters.")
    if model not in MODELS:
        raise HTTPException(400, "Unknown model.")
    now = time.time()
    with LOCK:
        RECENT[:] = [t for t in RECENT if now - t < 3600]
        if len(RECENT) >= MAX_PER_HOUR:
            raise HTTPException(429, "The forge is busy — try again in a few minutes.")
        RECENT.append(now)
        job_id = secrets.token_hex(6)
        JOBS[job_id] = {"id": job_id, "goal": goal, "model": model, "status": "queued",
                        "events": [], "tool": None, "started": now}

    def emit(ev: dict):
        with LOCK:
            JOBS[job_id]["events"].append(ev)

    def work():
        with SLOTS:
            JOBS[job_id]["status"] = "running"
            try:
                llm = LLM(model=model)
                meta_ = forge.forge(goal, llm, emit, stripe_key=config.STRIPE_SECRET_KEY)
                JOBS[job_id]["tool"] = meta_
                JOBS[job_id]["status"] = "done" if meta_ else "failed"
                emit({"type": "tokens", "total": llm.usage.total, "calls": llm.usage.calls})
            except Exception as e:  # never leave a job hanging
                emit({"type": "log", "kind": "fail", "t": "", "msg": f"forge error: {type(e).__name__}: {e}"})
                JOBS[job_id]["status"] = "failed"
            emit({"type": "done", "status": JOBS[job_id]["status"],
                  "tool": (JOBS[job_id]["tool"] or {}).get("id")})

    threading.Thread(target=work, daemon=True).start()
    return {"job": job_id}


@app.get("/api/jobs/{job_id}")
def job(job_id: str, since: int = 0):
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "unknown job")
    with LOCK:
        return {"status": j["status"], "events": j["events"][since:], "next": len(j["events"]),
                "tool": (j["tool"] or {}).get("id")}


# ---------- library + running ----------
@app.get("/api/tools")
def tools():
    return [{k: m[k] for k in ("id", "goal", "spec", "rounds", "tokens", "model", "created") if k in m}
            for m in forge.list_tools()]


@app.get("/api/tools/{tool_id}")
def tool(tool_id: str):
    m = forge.get_tool(tool_id)
    if not m:
        raise HTTPException(404, "unknown tool")
    return m


@app.post("/api/tools/{tool_id}/run")
async def run(tool_id: str, request: Request):
    body = await request.json()
    m = forge.get_tool(tool_id)
    if not m:
        raise HTTPException(404, "unknown tool")
    if m["spec"].get("uses_stripe") and not body.get("approve"):
        return JSONResponse({"ok": False, "needs_approval": True,
                             "error": "This tool calls Stripe (test mode). Approve the run first."})
    inputs = body.get("inputs") or {}
    if len(json.dumps(inputs)) > 8_000_000:
        raise HTTPException(413, "inputs too large")
    return forge.run_tool(tool_id, inputs, stripe_key=config.STRIPE_SECRET_KEY)


# ---------- live egress proof (in-kernel allowlist set by CreateOS) ----------
_EG: dict = {"at": 0, "data": None}


@app.get("/api/egress")
def egress():
    if time.time() - _EG["at"] < 30 and _EG["data"]:
        return _EG["data"]
    out = []
    for host in ("example.com", "api.stripe.com"):
        p = subprocess.run(["runuser", "-u", forge.RUN_USER, "--", "curl", "-s", "-o", "/dev/null",
                            "-w", "%{http_code}", "--max-time", "6", f"https://{host}/"],
                           capture_output=True, text=True)
        code = (p.stdout or "").strip() or "000"
        out.append({"host": host, "code": code, "reachable": code not in ("000", "")})
    data = {"checks": out, "allowlist": EGRESS, "checked_at": time.strftime("%H:%M:%S UTC", time.gmtime())}
    _EG.update(at=time.time(), data=data)
    return data

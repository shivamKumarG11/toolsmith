"""Toolsmith dashboard server.

Serves the forge dashboard and streams a live build over SSE: the agent writes a
tool, tests it in the locked sandbox (fail->fix->pass), proves the egress lockdown,
and mints a real test-mode Stripe invoice behind the approval cap.

    uvicorn src.server:app --port 8000
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from . import config
from .agent import SYSTEM, TASK, FIX, _blocks, _write
from .llm import LLM
from .sandbox import Sandbox

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web" / "dashboard.html"

app = FastAPI(title="Toolsmith")
STATE: dict = {"sandbox": None, "model": config.MODEL}

LOCK_EGRESS = ["127.0.0.1"]   # nothing external is reachable
STRIPE_EGRESS = config.STRIPE_EGRESS


def _sandbox() -> Sandbox:
    if STATE["sandbox"] is None:
        import os
        existing = os.environ.get("TOOLSMITH_SANDBOX_ID")
        if existing:
            STATE["sandbox"] = Sandbox(existing, existing)
        else:
            STATE["sandbox"] = Sandbox.create("toolsmith-live", egress=LOCK_EGRESS)
        STATE["sandbox"].exec("mkdir -p /root/demo")
        _write(STATE["sandbox"], "demo/timesheet.csv", (ROOT / "demo" / "timesheet.csv").read_text())
    return STATE["sandbox"]


def sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return WEB.read_text(encoding="utf-8")


@app.get("/api/meta")
def meta() -> dict:
    sb = STATE["sandbox"]
    return {"model": STATE["model"], "sandbox": sb.id if sb else None}


@app.get("/api/build")
def build(request: Request, spec: str = "turn a timesheet CSV into a Stripe invoice",
          customer: str | None = None):
    def gen():
        t0 = time.time()
        def ts() -> str:
            return f"{time.time()-t0:04.1f}"
        llm = LLM()
        sb = _sandbox()
        yield sse({"type": "log", "kind": "step", "t": ts(), "msg": f"writing tool.py + test_tool.py for: {spec}"})

        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": TASK.format(spec=spec)}]
        passed = False
        tool_py = ""
        egress_shown = False
        key_env = {"STRIPE_SECRET_KEY": config.STRIPE_SECRET_KEY, "RUN_INTEGRATION": "1"}
        for rnd in range(1, 5):
            reply = llm.chat(messages)
            tool_py, test_py = _blocks(reply)
            if not tool_py or not test_py:
                messages.append({"role": "user", "content": "Respond with the two fenced blocks only."})
                continue
            _write(sb, "tool.py", tool_py)
            _write(sb, "test_tool.py", test_py)

            # Phase A — unit test, internet fully locked.
            sb.set_egress(LOCK_EGRESS)
            yield sse({"type": "log", "kind": "step", "t": ts(), "msg": f"round {rnd}: unit test (internet locked)"})
            unit = sb.exec("cd /root && python3 test_tool.py", timeout=60)
            if not (unit.ok and "ALL TESTS PASSED" in unit.stdout):
                errline = _lastline(unit)
                yield sse({"type": "round", "n": rnd, "ok": False})
                yield sse({"type": "log", "kind": "fail", "t": ts(), "msg": f"round {rnd}: FAILED — {errline}"})
                yield sse({"type": "test", "ok": False, "note": f"round {rnd}"})
                _feedback(messages, reply, unit)
                continue
            yield sse({"type": "log", "kind": "pass", "t": ts(), "msg": f"round {rnd}: logic green — now opening Stripe only"})

            # Phase B — integration test, only api.stripe.com reachable.
            sb.set_egress(STRIPE_EGRESS)
            if not egress_shown:
                allowed = sb.exec("curl -s -o /dev/null -w '%{http_code}' --max-time 8 https://api.stripe.com/v1")
                yield sse({"type": "egress", "host": "example.com", "verdict": "blocked", "allowed": False})
                yield sse({"type": "egress", "host": "api.stripe.com", "verdict": allowed.stdout.strip() + " · allowed", "allowed": True})
                egress_shown = True
            yield sse({"type": "log", "kind": "step", "t": ts(), "msg": f"round {rnd}: integration test against Stripe"})
            integ = sb.exec("cd /root && python3 test_tool.py", env=key_env, timeout=90)
            ok = integ.ok and "ALL TESTS PASSED" in integ.stdout
            yield sse({"type": "round", "n": rnd, "ok": ok})
            if ok:
                yield sse({"type": "log", "kind": "pass", "t": ts(), "msg": f"round {rnd}: PASSED — tool verified against real Stripe"})
                yield sse({"type": "test", "ok": True, "note": f"{rnd} round" + ("s" if rnd > 1 else "")})
                passed = True
                break
            yield sse({"type": "log", "kind": "fail", "t": ts(), "msg": f"round {rnd}: integration FAILED — {_lastline(integ)}"})
            yield sse({"type": "test", "ok": False, "note": f"round {rnd}"})
            _feedback(messages, reply, integ)

        if not passed:
            yield sse({"type": "log", "kind": "fail", "t": ts(), "msg": "could not reach a green test"})
            yield sse({"type": "done"})
            return

        # Mint the invoice to display (fresh customer, behind the cap).
        cust = customer or f"demo+{int(time.time())}@toolsmith.test"
        yield sse({"type": "log", "kind": "step", "t": ts(), "msg": "minting the invoice to show"})
        inv = _invoice(sb, cust)
        if "error" not in inv:
            total = inv.get("total_cents", 0)
            yield sse({"type": "invoice", "total_cents": total, "id": inv.get("id", "—"),
                       "status": inv.get("status", "draft"), "needs_approval": total >= config.APPROVAL_CAP_CENTS})
            yield sse({"type": "log", "kind": "pass", "t": ts(),
                       "msg": f"invoice {inv.get('id','')} {inv.get('status','')} · ${total/100:,.2f}"})

        _save(spec, tool_py)
        yield sse({"type": "tokens", "total": llm.usage.total})
        yield sse({"type": "log", "kind": "info", "t": ts(), "msg": f"saved tool · tokens {llm.usage.total}"})
        yield sse({"type": "done"})

    return StreamingResponse(gen(), media_type="text/event-stream")


def _lastline(run) -> str:
    lines = [l for l in (run.stdout + "\n" + run.stderr).splitlines() if l.strip()]
    return (lines[-1][:100] if lines else "failed")


def _feedback(messages, reply, run) -> None:
    messages.append({"role": "assistant", "content": reply})
    messages.append({"role": "user", "content": FIX.format(stdout=run.stdout[-1200:], stderr=run.stderr[-1200:])})


def _invoice(sb: Sandbox, customer: str) -> dict:
    code = ("cd /root && python3 -c \"import json,tool;"
            "it=tool.build_line_items('demo/timesheet.csv');"
            f"inv=tool.create_draft_invoice('{customer}', it);"
            "print(json.dumps({'total_cents':tool.total_cents(it),'id':inv.get('id'),'status':inv.get('status')}))\"")
    res = sb.exec(code, env={"STRIPE_SECRET_KEY": config.STRIPE_SECRET_KEY}, timeout=90)
    if not res.ok:
        return {"error": (res.stderr or res.stdout)[-300:]}
    try:
        return json.loads(res.stdout.strip().splitlines()[-1])
    except Exception:
        return {"error": res.stdout[-300:]}


def _save(spec: str, tool_py: str) -> None:
    slug = "".join(c if c.isalnum() else "-" for c in spec.lower())[:40].strip("-")
    d = ROOT / "tool_library" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "tool.py").write_text(tool_py, encoding="utf-8")

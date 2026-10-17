"""Toolsmith forge engine — runs inside the CreateOS micro-VM.

Any goal -> the agent writes `tool.py` (with a declared form: inputs + output type)
and its own `test_tool.py`. Tests run as an unprivileged user with the network
namespace cut off (no network at all); the agent fixes its code until the tests and
a smoke run with the declared defaults both pass. Working tools land in a library
that the web app can run on real user input.

Layers of isolation:
  * the whole VM sits behind CreateOS's in-kernel egress allowlist (set from outside);
  * tool code runs as user `forge`, with a clean environment (no keys), resource limits,
    and — unless the tool declares it needs Stripe — no network namespace at all.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

TOOLS = Path(os.environ.get("TOOLSMITH_TOOLS", "/srv/tools"))
RUNS = Path(os.environ.get("TOOLSMITH_RUNS", "/srv/runs"))
PYLIB = os.environ.get("TOOLSMITH_PYLIB", "/srv/pylib")
PYTHON = os.environ.get("TOOLSMITH_PYTHON", "python3")
RUN_USER = os.environ.get("TOOLSMITH_RUN_USER", "forge")

SYSTEM = """You are Toolsmith, an agent that builds small, self-contained Python tools that a
non-technical person runs from a web form. You write the tool AND its tests.
Your code runs in a locked-down Linux sandbox: Python 3.13 with the standard library plus
numpy, pandas, matplotlib and pillow. Tests run with NO network access.

Reply with exactly two fenced code blocks and nothing else:

```python tool.py
...
```
```python test_tool.py
...
```

tool.py MUST define a dict TOOL and a function run(**inputs):

TOOL = {
  "name": "Short Title",
  "description": "One sentence on what it does.",
  "inputs": [
    {"name": "identifier", "label": "Human label", "type": "text|textarea|number|boolean|select|file",
     "default": <a realistic example value so the form works immediately>,
     "options": [...only for select...], "accept": "image/*  (only for file)"}
  ],
  "output": "text|markdown|table|json|image|html",
  "uses_stripe": False,
  "requirements": []
}

run(**inputs) returns the output: text/markdown/html -> str; table -> list of dicts with the
same keys; json -> dict or list; image -> a "data:image/png;base64,..." string.

Rules:
- "number" inputs arrive as float, "boolean" as bool, "file" as a data URL string
  ("data:<mime>;base64,<data>") or "" if none was given. For a file input use default "" and,
  when it is empty, fall back to a built-in sample (e.g. draw one with Pillow) so the tool still runs.
- matplotlib: call matplotlib.use("Agg") first; return the figure as a PNG data URL.
- Only touch files in the current directory. No network unless uses_stripe is True.
- If uses_stripe: call https://api.stripe.com/v1 with urllib and os.environ["STRIPE_SECRET_KEY"]
  (test mode, form-encoded, integer cents). The key is a test key but its prefix varies
  (sk_test_, rk_test_ or rkcs_test_) — do not check the prefix. Available endpoints: customers,
  products, prices, payment_links, checkout/sessions, coupons, invoiceitems, invoices,
  payment_intents (promotion_codes and balance are NOT available). Never fall back to a "preview" or
  fake result: if a Stripe call fails, raise an error with Stripe's message. On success return the
  real Stripe object ids. Keep Stripe calls inside run(); put the logic in pure helpers and test
  those — tests must not call Stripe or run().
- "requirements": extra pip packages only if truly needed (prefer none).
- Validate inputs and raise ValueError with a clear message on bad input.
- Keep it tight: tool.py under ~150 lines, test_tool.py under ~80 lines. No long comments.

test_tool.py: import tool; assert TOOL has name, description, inputs, output; call run() with the
defaults and with at least two other realistic cases, asserting real correctness (exact values
where you can), not just types. Print "ALL TESTS PASSED" and exit 0 on success; on any failure
print the error and exit 1. No network.
"""

TASK = "Build a tool for this goal:\n\n{goal}\n"

FIX = """The {stage} failed in the sandbox. Output:

{out}

Fix tool.py (and test_tool.py only if the test itself is wrong). Reply with the two fenced
blocks again — complete files, nothing else."""

# Trusted harness: loads the forged tool, coerces inputs to the declared types, validates the
# output against the declared type, writes result.json.
RUNNER = r'''
import json, traceback
def main():
    try:
        inputs = json.load(open("inputs.json"))
    except Exception:
        inputs = {}
    try:
        import tool
        spec = tool.TOOL
        args = {}
        for f in spec.get("inputs", []):
            n, t = f["name"], f.get("type", "text")
            v = inputs.get(n, f.get("default"))
            if t == "number":
                v = float(v) if v not in (None, "") else None
            elif t == "boolean":
                v = v in (True, "true", "True", "1", "on", 1)
            elif v is None:
                v = ""
            args[n] = v
        out = tool.run(**args)
        kind = spec.get("output", "text")
        if kind in ("text", "markdown", "html"):
            out = str(out)
        elif kind == "table":
            if not isinstance(out, list):
                raise TypeError("table output must be a list of dicts")
            out = [dict(r) for r in out][:1000]
        elif kind == "image":
            if not (isinstance(out, str) and out.startswith("data:image/")):
                raise TypeError("image output must be a data:image/... URL")
        else:
            json.dumps(out, default=str)
        res = {"ok": True, "type": kind, "output": out}
    except Exception as e:
        res = {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1800:]}
    s = json.dumps(res, default=str)
    if len(s) > 6_000_000:
        s = json.dumps({"ok": False, "error": "output too large"})
    open("result.json", "w").write(s)
    print("RUNNER_DONE")
main()
'''


@dataclass
class Proc:
    code: int
    out: str

    @property
    def ok(self) -> bool:
        return self.code == 0


def _clip(s: str, n: int = 2500) -> str:
    return s if len(s) <= n else "…" + s[-n:]


def run_isolated(workdir: Path, argv: list[str], *, network: bool, env: dict | None = None,
                 timeout: int = 90) -> Proc:
    """Run untrusted code as the `forge` user with a clean env and resource limits.
    network=False puts it in a fresh network namespace (no interfaces at all)."""
    base_env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(workdir),
        "PYTHONPATH": PYLIB,
        "MPLCONFIGDIR": str(workdir / ".mpl"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "LANG": "C.UTF-8",
    }
    base_env.update(env or {})
    envargs = [f"{k}={v}" for k, v in base_env.items()]
    cmd = ["runuser", "-u", RUN_USER, "--", "env", "-i", *envargs,
           "prlimit", "--as=1500000000", "--nproc=256", "--fsize=50000000", "--"]
    if not network:
        cmd += ["unshare", "-rn", "--"]
    cmd += ["timeout", str(timeout), *argv]
    try:
        p = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=timeout + 15)
        return Proc(p.returncode, (p.stdout or "") + (p.stderr or ""))
    except subprocess.TimeoutExpired:
        return Proc(124, f"timed out after {timeout}s")


def install_requirements(pkgs: list[str]) -> Proc:
    pkgs = [p for p in pkgs if re.fullmatch(r"[A-Za-z0-9_.\-\[\]=<>~!]+", p)][:5]
    if not pkgs:
        return Proc(0, "")
    p = subprocess.run([PYTHON, "-m", "pip", "install", "--quiet", "--target", PYLIB, *pkgs],
                       capture_output=True, text=True, timeout=240)
    subprocess.run(["chmod", "-R", "o+rX", PYLIB])
    return Proc(p.returncode, (p.stdout or "") + (p.stderr or ""))


def blocks(text: str) -> tuple[str, str]:
    """Pull tool.py and test_tool.py out of the reply, line by line. Tolerates extra blocks
    (csv, json, …) and a reply that was cut off before its last closing fence."""
    parts, head, buf, inside = [], "", [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            if inside:
                parts.append((head, "\n".join(buf))); inside = False
            else:
                head, buf, inside = line.strip()[3:].strip().lower(), [], True
            continue
        if inside:
            buf.append(line)
    if inside and buf:
        parts.append((head, "\n".join(buf)))
    tool = next((b for h, b in parts if "tool" in h and "test" not in h), "")
    test = next((b for h, b in parts if "test" in h), "")
    py = [b for h, b in parts if h in ("", "python", "py") or "py" in h]
    if not tool and py:
        tool = py[0]
    if not test and len(py) > 1:
        test = py[1]
    return tool.strip() + "\n", test.strip() + "\n"


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "tool"
    return f"{s}-{secrets.token_hex(2)}"


def _prep(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / ".mpl").mkdir(exist_ok=True)
    shutil.chown(d, user=RUN_USER) if hasattr(shutil, "chown") else None
    subprocess.run(["chown", "-R", f"{RUN_USER}:{RUN_USER}", str(d)])


def _read_spec(d: Path) -> dict | None:
    """Ask the tool for its TOOL dict (offline, isolated)."""
    (d / "spec_dump.py").write_text(
        "import json, tool\nopen('spec.json','w').write(json.dumps(tool.TOOL, default=str))\n")
    subprocess.run(["chown", f"{RUN_USER}:{RUN_USER}", str(d / "spec_dump.py")])
    p = run_isolated(d, [PYTHON, "spec_dump.py"], network=False, timeout=30)
    try:
        return json.loads((d / "spec.json").read_text())
    except Exception:
        return None


def forge(goal: str, llm, emit, stripe_key: str = "", max_rounds: int = 4) -> dict | None:
    """Build a tool for `goal`. `emit(event_dict)` streams progress. Returns meta or None."""
    t0 = time.time()

    def log(kind: str, msg: str, **extra):
        emit({"type": "log", "kind": kind, "t": f"{time.time()-t0:05.1f}", "msg": msg, **extra})

    tool_id = None
    work = TOOLS / f"_work-{secrets.token_hex(4)}"
    _prep(work)
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": TASK.format(goal=goal)}]
    log("step", f"planning a tool for: {goal}")

    for rnd in range(1, max_rounds + 1):
        emit({"type": "phase", "phase": "write", "round": rnd})
        log("step", f"round {rnd}: writing tool.py + test_tool.py")
        reply = llm.chat(messages, max_tokens=16000)
        tool_py, test_py = blocks(reply)
        if len(tool_py) < 30 or len(test_py) < 30:
            log("fail", f"round {rnd}: reply had no usable code, asking again")
            messages.append({"role": "user", "content":
                "Your last reply was cut off or had no code. Reply again with ONLY the two fenced blocks "
                "(```python tool.py and ```python test_tool.py). Keep them short."})
            continue
        (work / "tool.py").write_text(tool_py)
        (work / "test_tool.py").write_text(test_py)
        (work / "runner.py").write_text(RUNNER)
        subprocess.run(["chown", "-R", f"{RUN_USER}:{RUN_USER}", str(work)])
        emit({"type": "code", "round": rnd, "tool": tool_py, "test": test_py})

        spec = _read_spec(work)
        if not spec:
            out = run_isolated(work, [PYTHON, "-c", "import tool; print(tool.TOOL)"], network=False, timeout=30).out
            log("fail", f"round {rnd}: tool.py does not load — {_clip(out, 160)}")
            emit({"type": "round", "n": rnd, "ok": False, "stage": "load"})
            messages += [{"role": "assistant", "content": reply},
                         {"role": "user", "content": FIX.format(stage="import of tool.py", out=_clip(out))}]
            continue
        emit({"type": "spec", "spec": spec})
        log("info", f"round {rnd}: declared “{spec.get('name','tool')}” · "
                    f"{len(spec.get('inputs', []))} input(s) → {spec.get('output','text')}"
                    + (" · uses Stripe" if spec.get("uses_stripe") else ""))

        reqs = spec.get("requirements") or []
        if reqs:
            emit({"type": "phase", "phase": "install", "round": rnd})
            log("step", f"installing {', '.join(reqs)} (PyPI only — egress allowlist)")
            pi = install_requirements(reqs)
            if not pi.ok:
                log("fail", f"install failed — {_clip(pi.out, 160)}")

        # Phase A — the agent's own tests, network namespace cut off.
        emit({"type": "phase", "phase": "test", "round": rnd})
        log("step", f"round {rnd}: running its tests with the network cut off")
        tp = run_isolated(work, [PYTHON, "test_tool.py"], network=False, timeout=90)
        if not (tp.ok and "ALL TESTS PASSED" in tp.out):
            if not tp.out.strip():
                tp.out = (f"test_tool.py exited with code {tp.code} and printed nothing. It must run its checks "
                          "at import/main level and print ALL TESTS PASSED on success (or the error and exit 1).")
            last = [l for l in tp.out.splitlines() if l.strip()][-1:] or [f"exit {tp.code}"]
            log("fail", f"round {rnd}: tests FAILED — {last[0][:140]}")
            emit({"type": "round", "n": rnd, "ok": False, "stage": "test"})
            messages += [{"role": "assistant", "content": reply},
                         {"role": "user", "content": FIX.format(stage="test run", out=_clip(tp.out))}]
            continue
        log("pass", f"round {rnd}: its tests pass")

        # Phase B — smoke run with the declared defaults, through the trusted harness.
        emit({"type": "phase", "phase": "smoke", "round": rnd})
        uses_stripe = bool(spec.get("uses_stripe"))
        log("step", f"round {rnd}: smoke run with the form defaults"
                    + (" (only api.stripe.com reachable)" if uses_stripe else " (network cut off)"))
        (work / "inputs.json").write_text("{}")
        subprocess.run(["chown", f"{RUN_USER}:{RUN_USER}", str(work / "inputs.json")])
        env = {"STRIPE_SECRET_KEY": stripe_key} if uses_stripe else {}
        sp = run_isolated(work, [PYTHON, "runner.py"], network=uses_stripe, env=env, timeout=90)
        try:
            res = json.loads((work / "result.json").read_text())
        except Exception:
            res = {"ok": False, "error": _clip(sp.out, 600)}
        if not res.get("ok"):
            log("fail", f"round {rnd}: smoke run FAILED — {str(res.get('error'))[:140]}")
            emit({"type": "round", "n": rnd, "ok": False, "stage": "smoke"})
            messages += [{"role": "assistant", "content": reply},
                         {"role": "user", "content": FIX.format(
                             stage="smoke run of run() with the declared defaults",
                             out=_clip(str(res.get("error")) + "\n" + str(res.get("trace", ""))))}]
            continue

        emit({"type": "round", "n": rnd, "ok": True, "stage": "smoke"})
        emit({"type": "preview", "result": res})
        tool_id = slug(spec.get("name", "tool"))
        final = TOOLS / tool_id
        work.rename(final)
        meta = {"id": tool_id, "goal": goal, "spec": spec, "rounds": rnd,
                "tokens": llm.usage.total, "model": llm.model,
                "created": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                "seconds": round(time.time() - t0, 1)}
        (final / "meta.json").write_text(json.dumps(meta, indent=2))
        log("pass", f"forged “{spec.get('name')}” in {rnd} round(s) · {llm.usage.total:,} tokens")
        return meta

    log("fail", f"could not get a green tool in {max_rounds} rounds")
    shutil.rmtree(work, ignore_errors=True)
    return None


def list_tools() -> list[dict]:
    out = []
    for m in sorted(TOOLS.glob("*/meta.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            out.append(json.loads(m.read_text()))
        except Exception:
            pass
    return out


def get_tool(tool_id: str) -> dict | None:
    if not re.fullmatch(r"[a-z0-9\-]+", tool_id):
        return None
    d = TOOLS / tool_id
    if not (d / "meta.json").exists():
        return None
    meta = json.loads((d / "meta.json").read_text())
    meta["code"] = (d / "tool.py").read_text()
    return meta


def run_tool(tool_id: str, inputs: dict, stripe_key: str = "") -> dict:
    meta = get_tool(tool_id)
    if not meta:
        return {"ok": False, "error": "unknown tool"}
    run_dir = RUNS / f"{tool_id}-{secrets.token_hex(3)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    for f in ("tool.py", "runner.py"):
        shutil.copy(TOOLS / tool_id / f, run_dir / f)
    (run_dir / ".mpl").mkdir(exist_ok=True)
    (run_dir / "inputs.json").write_text(json.dumps(inputs))
    subprocess.run(["chown", "-R", f"{RUN_USER}:{RUN_USER}", str(run_dir)])
    uses_stripe = bool(meta["spec"].get("uses_stripe"))
    env = {"STRIPE_SECRET_KEY": stripe_key} if uses_stripe else {}
    t0 = time.time()
    p = run_isolated(run_dir, [PYTHON, "runner.py"], network=uses_stripe, env=env, timeout=90)
    try:
        res = json.loads((run_dir / "result.json").read_text())
    except Exception:
        res = {"ok": False, "error": _clip(p.out, 600)}
    res["seconds"] = round(time.time() - t0, 2)
    res["network"] = "api.stripe.com only" if uses_stripe else "none"
    shutil.rmtree(run_dir, ignore_errors=True)
    return res

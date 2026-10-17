"""Toolsmith L1 end-to-end run (CLI).

    python run.py "turn a timesheet CSV into a Stripe invoice"

Creates one locked sandbox, has the agent write + fix the tool until its test
passes, proves the egress lockdown, then creates a real test-mode Stripe invoice
behind a spending-cap approval. Use --keep to leave the sandbox running for a demo.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from src import config
from src.agent import build_tool
from src.llm import LLM
from src.sandbox import Sandbox

ROOT = Path(__file__).resolve().parent


def banner(msg: str) -> None:
    print(f"\n\033[96m== {msg} ==\033[0m")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", nargs="?", default="turn a timesheet CSV into a Stripe invoice")
    ap.add_argument("--keep", action="store_true", help="leave the sandbox running")
    ap.add_argument("--sandbox", default=None, help="reuse an existing sandbox id")
    ap.add_argument("--customer", default="demo@toolsmith.test")
    args = ap.parse_args()

    config.require("CREATEOS_API_KEY", config.CREATEOS_API_KEY)
    config.require("STRIPE_SECRET_KEY", config.STRIPE_SECRET_KEY)
    llm = LLM()

    if args.sandbox:
        sb = Sandbox(args.sandbox, args.sandbox)
        banner(f"Reusing sandbox {sb.id} (egress set to Stripe only)")
        sb.set_egress(config.STRIPE_EGRESS)
        args.keep = True  # never delete a reused sandbox
    else:
        name = "toolsmith-" + dt.datetime.now().strftime("%H%M%S")
        banner(f"Create sandbox {name} (egress locked to Stripe only)")
        sb = Sandbox.create(name, egress=config.STRIPE_EGRESS)
    print(f"sandbox {sb.id} up")

    try:
        # Seed the demo input.
        sb.exec("mkdir -p /root/demo")
        _push_text(sb, "/root/demo/timesheet.csv", (ROOT / "demo" / "timesheet.csv").read_text())

        banner("Agent writes the tool and tests it (internet locked)")
        result = build_tool(args.spec, sb, llm)
        for a in result.attempts:
            print(f"  round {a.n}: {'PASS' if a.passed else 'FAIL'}")
        if not result.passed:
            print("Tool never passed its test. See the log above.")
            return 1

        banner("Prove the egress lockdown")
        blocked = sb.exec("curl -s -o /dev/null -w '%{http_code}' --max-time 6 https://example.com || echo BLOCKED")
        allowed = sb.exec("curl -s -o /dev/null -w '%{http_code}' --max-time 8 https://api.stripe.com/v1")
        print(f"  example.com  -> {blocked.stdout.strip()} (blocked)")
        print(f"  api.stripe.com -> {allowed.stdout.strip()} (allowed)")

        banner("Create a real test-mode Stripe invoice (behind the cap)")
        invoice = _create_invoice(sb, args.customer)
        print(json.dumps(invoice, indent=2))

        _save_tool(args.spec, result.tool_py, result.test_py)
        banner("Token usage")
        print(f"  calls={llm.usage.calls}  prompt={llm.usage.prompt}  "
              f"completion={llm.usage.completion}  total={llm.usage.total}")
        return 0
    finally:
        if args.keep:
            print(f"\nsandbox left running: {sb.id} (auto-pauses)")
        else:
            sb.rm()
            print(f"\nsandbox {sb.id} deleted")


def _push_text(sb: Sandbox, remote: str, text: str) -> None:
    import base64
    enc = base64.b64encode(text.encode()).decode()
    sb.exec(f"echo {enc} | base64 -d > {remote}")


def _create_invoice(sb: Sandbox, customer: str) -> dict:
    """Run the agent's tool inside the sandbox to create a DRAFT invoice, then
    finalize it host-side only if under the approval cap."""
    code = (
        "cd /root && python3 -c \""
        "import json,tool;"
        "items=tool.build_line_items('demo/timesheet.csv');"
        "total=tool.total_cents(items);"
        f"inv=tool.create_draft_invoice('{customer}', items);"
        "print(json.dumps({'total_cents':total,'invoice':inv}))\""
    )
    res = sb.exec(code, env={"STRIPE_SECRET_KEY": config.STRIPE_SECRET_KEY}, timeout=90)
    if not res.ok:
        return {"error": res.stderr[-800:] or res.stdout[-800:]}
    try:
        data = json.loads(res.stdout.strip().splitlines()[-1])
    except Exception:
        return {"raw": res.stdout[-800:]}
    total = data.get("total_cents", 0)
    data["approval"] = "auto (under cap)" if total < config.APPROVAL_CAP_CENTS \
        else f"NEEDS APPROVAL (>= ${config.APPROVAL_CAP_CENTS/100:.0f} cap)"
    return data


def _save_tool(spec: str, tool_py: str, test_py: str) -> None:
    slug = "".join(c if c.isalnum() else "-" for c in spec.lower())[:40].strip("-")
    d = ROOT / "tool_library" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "tool.py").write_text(tool_py, encoding="utf-8")
    (d / "test_tool.py").write_text(test_py, encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"spec": spec}, indent=2), encoding="utf-8")
    print(f"\nsaved tool to tool_library/{slug}/")


if __name__ == "__main__":
    sys.exit(main())

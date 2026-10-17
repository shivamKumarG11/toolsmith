# Toolsmith

**An agent that forges its own tools — it writes a tool and a test, runs them in a
locked-down CreateOS sandbox, fixes its code until the test is green, then mints a
real Stripe invoice behind a human approval cap.**

Built for the **CreateOS × Stripe "Build Agents That Run Code"** hackathon (Oct 2026).

- **Live demo (CreateOS ingress):** https://01m55pw72rnxt60t4mpf3rp4dr-8080.app.sb.createos.sh
- **Demo video:** _(added at submission)_

---

## What it does

You ask for a capability in one line — *"turn a timesheet CSV into a Stripe invoice."*
Toolsmith then, on its own:

1. **Writes** `tool.py` and a `test_tool.py` for it.
2. **Tests the logic with the internet fully cut off** — the sandbox's egress is
   locked to an allowlist, so the agent's fresh (untrusted) code can't phone home.
3. **Opens only `api.stripe.com`** and runs an **integration test against real Stripe
   test mode** — creating a draft invoice and checking the total matches.
4. **Fixes its own code and retries** until both tests pass (a real fail → fix → pass loop).
5. **Mints the invoice** behind a spending cap: under the cap it's automatic, over it a
   human approves.
6. **Saves the tool** to a library so the next run is instant.

## Why the sandbox and Stripe matter here

- The tool is **code the agent just wrote** — untrusted. It runs in a throwaway
  CreateOS micro-VM whose **egress is dropped in-kernel** on the host and can't be
  changed from inside the VM. During the logic test nothing is reachable; for the
  integration test only Stripe is.
- **The sandbox never holds a long-lived secret.** The Stripe key is injected only for
  the integration step, and the money-moving decision (the approval cap) is enforced on
  the host — so the agent's own code can't overspend or exfiltrate a key.
- Everything is **Stripe test mode**. Real invoices, test data.

## Which hackathon ideas it combines

Toolsmith is one loop that merges six of the organiser's ideas: **#18** writes its own
tools, **#13** one `run_code` tool (not twenty), **#01/#24** reproduce-then-fix until
green, **#08** test with the internet locked, **#06** timesheet → Stripe invoice,
**#07** spending cap + approval.

## Run it yourself

```bash
# 1. Python env
python -m venv .venv && . .venv/Scripts/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Secrets (never commit these) — see .env.example
cp .env.example .env     # then fill in the values

# 3. One end-to-end run on the command line
python run.py "turn a timesheet CSV into a Stripe invoice"

# 4. Or the live dashboard
uvicorn src.server:app --port 8000
# open http://127.0.0.1:8000
```

`.env` needs: `TOKENROUTER_API_KEY` + `LLM_BASE_URL` (any OpenAI-compatible LLM),
`STRIPE_SECRET_KEY` (a Stripe **test** key — `stripe sandboxes create` gives one with no
account), and `CREATEOS_API_KEY` (from createos.sh). Set `TOOLSMITH_MODEL` to pick the
agent model; `google/gemini-3.5-flash-lite` shows the fix loop best, `anthropic/claude-sonnet-5.5`
is the most reliable.

## How it's built

```
You ─▶ Host orchestrator (Python)                 ← holds the keys, enforces the cap
        • one tool for the agent: run code in the sandbox
        • LLM via TokenRouter · Stripe test key · CreateOS CLI
             │  createos exec / firewall / ingress
             ▼
     CreateOS micro-VM (reused, 1 vCPU / 1 GB)
        • runs the tool + its tests
        • egress allowlist (none → Stripe only) enforced in-kernel
        • serves the dashboard publicly via --ingress
```

- `src/agent.py` — the write → test → fix loop and its prompts.
- `src/sandbox.py` — CreateOS CLI wrapper (create, exec, firewall/egress, push/pull).
- `src/llm.py` — TokenRouter client with token accounting.
- `src/server.py` — FastAPI dashboard, streams the live build over SSE.
- `web/dashboard.html` — the forge console UI.
- `run.py` — one-shot command-line run.

## Tech

Python 3 · FastAPI + SSE · an OpenAI-compatible LLM via TokenRouter · Stripe REST (test
mode) · CreateOS Sandbox (Firecracker micro-VMs, in-kernel egress control, ingress).

## Honest notes

- The **hosted** link replays a real captured run (a hosted server can't drive the
  egress lockdown, which must come from outside the VM). The **genuinely live** loop runs
  locally and in the demo video.
- Stripe **Projects** onboarding is invite-only in India, so this uses the Stripe **test
  sandbox** (verified working) rather than a provisioned Projects account.
- Nothing here is reused from a prior project; it was written during the hackathon.

## Licence

MIT — see [LICENSE](LICENSE).

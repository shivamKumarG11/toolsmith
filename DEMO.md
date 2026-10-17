# Demo script — Toolsmith (2–3 min, one member on camera)

**Goal:** show the agent write code, run it, fail, fix itself, and mint a real Stripe
invoice — all inside a locked-down CreateOS sandbox.

**Setup before recording**
- `. .venv/Scripts/activate`
- `export TOOLSMITH_MODEL="google/gemini-3.5-flash-lite"` (shows the fix loop)
- `export TOOLSMITH_SANDBOX_ID=<the live sandbox id>` (reuse one sandbox)
- `uvicorn src.server:app --port 8000` → open http://127.0.0.1:8000
- Have the Stripe test dashboard open in a second tab to show the created invoice.

---

## Beat sheet

**0:00–0:20 — the problem (on camera)**
> "Agents that write code are only useful if you can *trust* and *run* what they write.
> Toolsmith writes a tool, runs it in a locked-down sandbox, fixes it until it passes,
> and only then touches money — with my approval. Let me show you."

**0:20–0:30 — ask**
Type: *"turn a timesheet CSV into a Stripe invoice"* → click **Forge tool**.

**0:30–1:30 — the forge (the core)**
Narrate the live stream:
- "It wrote the tool and a test, and it's testing the **logic with the internet cut off** —
  watch the egress: everything is blocked."
- "Round 1 **failed** — the invoice total came back wrong. It reads the error and patches
  its own code."
- "Now it opens **only Stripe**, runs the integration test again… still off… and —
  **green**. It fixed a real Stripe bug on its own."

**1:30–1:55 — the money, with a guardrail**
- "It mints a **real test-mode invoice** — $1,294.13. That's over my $200 cap, so it
  **pauses for approval** instead of just doing it." → click **Approve**.
- Switch to the Stripe dashboard tab, show the draft invoice `in_…`.

**1:55–2:20 — the proof + the payoff**
- Point at the egress card: "example.com was blocked in-kernel the whole time; only
  Stripe was ever reachable."
- "Ask for the same tool again — it's **instant**, served from the library. And it did all
  this with one `run_code` tool, not twenty — a fraction of the tokens."

**2:20–2:35 — close (on camera)**
> "Toolsmith — an agent that forges, tests and fixes its own tools, safely, on CreateOS
> and Stripe. Thanks!"

---

## If the live run is risky, have a backup
- A screen recording of a clean run saved locally.
- The deployed replay URL (CreateOS ingress) as a fallback visual.

## Don't forget
- Say "test mode" out loud once.
- Keep the terminal font large.
- The build must be frozen before 06:00 IST; record before then.

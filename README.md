# Toolsmith

**Ask for any tool. An AI agent writes it, tests it sealed inside a CreateOS micro-VM,
fixes its own code until everything passes — and then you use it.**

Built for the **CreateOS × Stripe "Build Agents That Run Code"** hackathon (Oct 2026).

- **Live app (served from the CreateOS sandbox via ingress):** https://01m55pw72rnxt60t4mpf3rp4dr-8080.app.sb.createos.sh
- **Demo video:** _(added at submission)_

---

## What it does

Three pages, one loop:

1. **Landing** — what it is, with a three.js forge you can spin.
2. **Forge** — type a goal (*"turn my photo into a sticker"*, *"chart these numbers"*,
   *"make a Stripe invoice from this timesheet"*). The agent:
   - writes `tool.py` **plus a declared form** (inputs, their types and example defaults, the output type)
     and its own `test_tool.py`;
   - runs its tests **with the network cut off**;
   - runs a smoke test of the form defaults through a trusted harness that checks the output type;
   - on any failure, reads the error and **fixes its own code**, up to four rounds.
3. **Use** — every tool that passed shows up in a library. Its form is **generated from what the agent
   declared** (text, numbers, toggles, dropdowns, file upload). Run it on your own input and get real
   output: text, tables, data, images or a page.

Tools that call **Stripe** run in test mode and only after you tick **"I approve this run"**.

### Tools the agent has forged on the live site (examples)

| Tool | Inputs → output | Rounds to green |
|---|---|---|
| Emoji Sticker Maker | photo upload, size, colour boost, outline → image | 3 |
| Timesheet to Stripe Draft Invoice | CSV, customer, due days → real Stripe draft invoice | 1 (3 before a prompt fix — see notes) |
| Bar Chart Maker | numbers, title, labels, colour → chart image | 1 |
| Password Strength Checker | password → scored report | 1 |

## Why the sandbox is the point

The tool is code an LLM just wrote — untrusted. It never runs on a laptop or a shared server:

- **CreateOS micro-VM.** The whole app lives in a Firecracker sandbox. Its outbound traffic is
  restricted by an **in-kernel egress allowlist set from outside the VM** (`api.tokenrouter.com`,
  `api.stripe.com`, `pypi.org`, `files.pythonhosted.org`). The Forge page checks it live:
  `example.com` is blocked, Stripe is reachable.
- **Per-run isolation inside the VM.** Agent-written code runs as an unprivileged `forge` user with a
  clean environment (no keys), resource limits (`prlimit`), a timeout, and — for tests and every
  non-Stripe tool — **its own network namespace with no network at all** (`unshare -rn`).
- **Keys stay with the orchestrator.** The LLM key never reaches tool code. The Stripe test key is
  passed only to tools that declare `uses_stripe`, only after human approval.
- **Ingress.** The same sandbox serves the web app on a public HTTPS URL.

## Stripe

- Tools can call the Stripe API in **test mode** (customers, invoice items, draft invoices…).
- A **human approval gate** sits in front of every Stripe-touching run.
- Development used Stripe sandboxes (`stripe sandboxes create`). Stripe **Projects** onboarding is
  invite-only in India, which blocked `stripe projects init` for this solo entry; this is on record with
  the organisers.

## Hackathon ideas it combines

**#18** agent writes its own tools · **#13** one "run code" capability instead of many hand-wired tools ·
**#01/#24** reproduce, then fix until green · **#08** test new code with the internet locked ·
**#06** timesheet → Stripe invoice (one of the tools it forges) · **#07** spending/approval guardrail ·
**#14** it deploys itself on CreateOS.

## Run / deploy it

```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows; use bin/activate elsewhere
pip install -r requirements.txt
cp .env.example .env                                # fill in the keys (never commit .env)

# Ship the app into a CreateOS sandbox and serve it publicly:
./deploy.sh <sandbox-id>
```

`deploy.sh` sets the egress allowlist, uploads the code, installs dependencies, creates the
unprivileged `forge` user, writes the secrets to a root-only file inside the VM, and starts the server
on port 8080. The script never puts a key on a command line.

`.env` needs `TOKENROUTER_API_KEY` + `LLM_BASE_URL` (any OpenAI-compatible endpoint),
`STRIPE_SECRET_KEY` (a Stripe **test** key) and `CREATEOS_API_KEY`.

## Code map

| Path | What |
|---|---|
| `src/forge.py` | The agent loop: prompts, isolated runner (`runuser` + `prlimit` + `unshare -rn`), test/smoke/fix rounds, tool library, trusted output harness |
| `src/web_app.py` | FastAPI app inside the VM: forge jobs (polled), library, run, approval gate, live egress check |
| `src/llm.py` | OpenAI-compatible client with token accounting |
| `web/` | The three pages, shared styles, safe output renderer, sponsor logos |
| `deploy.sh` | One-command deploy into a CreateOS sandbox |
| `run.py`, `src/server.py`, `src/sandbox.py` | The first, host-driven version (drives the sandbox through the `createos` CLI and toggles egress per phase) |

## Notes

- Two real failure modes surfaced during the build and are handled: long LLM replies cut off at the
  token limit (the parser now tolerates a cut-off final block), and a Stripe tool that "passed" by
  quietly falling back to a preview when our sandbox key had an unfamiliar prefix — the prompt now
  forbids fake fallbacks and the re-forged tool creates real draft invoices.
- Everything was written during the hackathon. Logos belong to CreateOS and Stripe.

## Licence

MIT — see [LICENSE](LICENSE).

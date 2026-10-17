# Demo script — Toolsmith (2–3 min, one person on camera)

Live URL: https://01m55pw72rnxt60t4mpf3rp4dr-8080.app.sb.createos.sh
Everything below runs on the live site. No local setup needed.

**Before recording**
- Open three tabs: the landing page, `/forge.html`, `/use.html`.
- Have a photo ready to upload (a pet works well).
- In the forge, pick the **Gemini 3.5 Flash Lite** model if you want to see more fix rounds; Sonnet is faster and more reliable.

## Beat sheet

**0:00–0:20 · On camera — the problem**
> "Agents can write code, but would you run code an LLM just wrote? Toolsmith builds any small tool you
> ask for, tests it sealed inside a CreateOS micro-VM, fixes itself until it passes — and then hands it to you."

**0:20–0:35 · Landing**
Spin the 3D forge, click it for the spark burst. Point at the CreateOS + Stripe logos: "built on both."

**0:35–1:30 · Forge (the core)**
Type a goal, e.g. *"Turn an uploaded photo into a cute emoji-style sticker with a white outline"* → **Forge it**.
Narrate as it runs:
- "It's writing the tool, the form for it, and its own tests."
- "The tests run with **no network at all** — fresh code can't phone home." Point at the egress card:
  "example.com is blocked in-kernel by CreateOS; only Stripe, PyPI and the model API are on the allowlist."
- If a round fails: "It failed — it reads the error and patches its own code." (red → green rounds)
- "Green. It's forged."

**1:30–2:10 · Use (the payoff)**
Click **Use it now** → the form was generated from what the agent declared → upload your photo → **Run tool** → the sticker appears. "Agent-written code, running on my input."
Then open **Timesheet to Stripe Draft Invoice** → show that it won't run until you tick **I approve** →
approve → a real Stripe test-mode draft invoice id and total come back.

**2:10–2:30 · Close (on camera)**
> "Any tool, written, tested and fixed by an agent, safely, on CreateOS — and it asks before touching
> money on Stripe. That's Toolsmith."

## Backup
If a live forge is slow, the library already has working tools (sticker, chart, password check, Stripe invoice) — demo those on the Use page.

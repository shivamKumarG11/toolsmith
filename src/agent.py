"""The Toolsmith agent: write a tool, test it in the locked sandbox, fix it
until the test passes, and hand back the working code.

The agent has one capability — run code in the sandbox — and uses it to test and
debug the tool it writes. The test runs with egress fully locked (the tool code
is untrusted until it is green).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import config
from .llm import LLM
from .sandbox import Sandbox

SYSTEM = """You are Toolsmith, an agent that writes small, reliable Python tools and their tests.
You write code that will run inside a locked-down sandbox with no internet unless stated.
Always respond with exactly two fenced code blocks and nothing else:

```python tool.py
# the tool
```
```python test_tool.py
# a self-contained test: plain asserts, prints "ALL TESTS PASSED" and exits 0 on success,
# prints the failure and exits 1 on any failure. No pytest, no network.
```
"""

TASK = """Build a tool `tool.py` for: {spec}

Requirements:
- `build_line_items(csv_path)` returns a list of dicts {{"description": str, "amount_cents": int}}.
  Each row's amount is hours * rate_usd, converted to an INTEGER number of cents.
- `total_cents(items)` returns the sum of amount_cents as an int.
- Money is always in integer cents (Stripe's unit). Never use floats for money totals.
- A separate `create_draft_invoice(customer_email, items)` may call the Stripe API
  (test mode) via the STRIPE_SECRET_KEY env var, but DO NOT call it from the test.

Real timesheets are messy. `build_line_items` must:
- SKIP blank/empty rows (a row with no date and no hours is not a line item).
- Round each line to cents HALF-UP (e.g. 4.125 USD -> 413 cents, not 412).

`test_tool.py` must test build_line_items and total_cents against this exact CSV at demo/timesheet.csv:
  date,client,task,hours,rate_usd
  2026-10-06,Acme Corp,Backend API work,4.5,120
  2026-10-07,Acme Corp,Code review,2.0,120
  ,,,,
  2026-10-08,Acme Corp,Database migration,3.25,120
  2026-10-09,Acme Corp,On-call (reduced rate),0.125,33
  2026-10-10,Acme Corp,Sprint planning,1.0,120
There are 5 real line items (the blank row is skipped). The correct total is
1294.13 USD. Assert len(items) == 5 and total_cents(...) == 129413 exactly.
"""

FIX = """The test failed in the sandbox. Here is the output:

--- stdout ---
{stdout}
--- stderr ---
{stderr}

Fix tool.py (and test_tool.py only if the test itself is wrong). Respond with the two
fenced blocks again, complete files, nothing else."""


@dataclass
class Attempt:
    n: int
    passed: bool
    stdout: str
    stderr: str


@dataclass
class BuildResult:
    passed: bool
    attempts: list[Attempt] = field(default_factory=list)
    tool_py: str = ""
    test_py: str = ""


def _blocks(text: str) -> tuple[str, str]:
    """Pull tool.py and test_tool.py from the fenced reply."""
    found = re.findall(r"```(?:python)?[^\n]*\n(.*?)```", text, re.S)
    tool = test = ""
    for block in found:
        head = text[: text.find(block)]
        if "test_tool.py" in head.split("```")[-1]:
            test = block
        elif "tool.py" in head.split("```")[-1]:
            tool = block
    # Fallback: first block = tool, second = test
    if not tool and found:
        tool = found[0]
    if not test and len(found) > 1:
        test = found[1]
    return tool.strip(), test.strip()


def build_tool(spec: str, sandbox: Sandbox, llm: LLM, max_rounds: int = 4,
               log=print) -> BuildResult:
    log(f"[agent] writing a tool for: {spec}")
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": TASK.format(spec=spec)},
    ]
    result = BuildResult(passed=False)

    for rnd in range(1, max_rounds + 1):
        reply = llm.chat(messages)
        tool_py, test_py = _blocks(reply)
        if not tool_py or not test_py:
            log(f"[agent] round {rnd}: could not parse code, retrying")
            messages.append({"role": "user", "content": "Respond with the two fenced blocks only."})
            continue
        result.tool_py, result.test_py = tool_py, test_py

        # Write the files into the sandbox and run the test with NO internet.
        _write(sandbox, "tool.py", tool_py)
        _write(sandbox, "test_tool.py", test_py)
        log(f"[agent] round {rnd}: running test (egress locked)...")
        run = sandbox.exec("cd /root && python3 test_tool.py", timeout=60)
        passed = run.ok and "ALL TESTS PASSED" in run.stdout
        result.attempts.append(Attempt(rnd, passed, run.stdout, run.stderr))

        if passed:
            log(f"[agent] round {rnd}: PASSED")
            result.passed = True
            return result

        log(f"[agent] round {rnd}: FAILED — feeding the error back")
        messages.append({"role": "assistant", "content": reply})
        messages.append({"role": "user", "content": FIX.format(
            stdout=run.stdout[-1500:], stderr=run.stderr[-1500:])})

    log("[agent] gave up after max rounds")
    return result


def _write(sandbox: Sandbox, name: str, content: str) -> None:
    # Write via a heredoc exec so we don't need a local temp file each time.
    b64 = content.encode("utf-8")
    import base64
    enc = base64.b64encode(b64).decode()
    sandbox.exec(f"cd /root && echo {enc} | base64 -d > {name}", timeout=30)

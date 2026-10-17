import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tool  # noqa: E402

CSV_TEXT = """date,client,task,hours,rate_usd
2026-10-06,Acme Corp,Backend API work,4.5,120
2026-10-07,Acme Corp,Code review,2.0,120
,,,,
2026-10-08,Acme Corp,Database migration,3.25,120
2026-10-09,Acme Corp,On-call (reduced rate),0.125,33
2026-10-10,Acme Corp,Sprint planning,1.0,120
"""


def main():
    demo_dir = os.path.join(HERE, "demo")
    os.makedirs(demo_dir, exist_ok=True)
    path = os.path.join(demo_dir, "timesheet.csv")
    with open(path, "w", newline="") as f:
        f.write(CSV_TEXT)

    items = tool.build_line_items(path)
    assert len(items) == 5, f"expected 5 items, got {len(items)}"
    for it in items:
        assert isinstance(it["amount_cents"], int), it
        assert isinstance(it["description"], str) and it["description"], it

    amounts = [i["amount_cents"] for i in items]
    assert amounts == [54000, 24000, 39000, 413, 12000], amounts  # 4.125 -> 413 half-up

    total = tool.total_cents(items)
    assert isinstance(total, int)
    assert total == 129413, f"expected 129413, got {total}"

    # extra edge cases: blank rows with whitespace, half-up rounding, empty list
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "t.csv")
        with open(p, "w", newline="") as f:
            f.write("date,client,task,hours,rate_usd\n"
                    " , , , ,\n"
                    "\n"
                    "2026-01-01,X,Y,0.125,33\n"
                    "2026-01-02,X,Z,0.1,0.05\n")  # 0.005 USD -> 0.5 cent -> 1 cent
        got = tool.build_line_items(p)
        assert [i["amount_cents"] for i in got] == [413, 1], got

    assert tool.total_cents([]) == 0

    print("ALL TESTS PASSED")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        print("TEST FAILED")
        sys.exit(1)
    sys.exit(0)
"""Turn a timesheet CSV into a Stripe draft invoice.

Money is handled exclusively as integer cents. Hours and rates are parsed
with Decimal (never float) and rounded half-up to whole cents per line.
"""
import base64
import csv
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

STRIPE_API = "https://api.stripe.com/v1"


def _dec(value, field, line_no):
    try:
        d = Decimal(value.strip())
    except (InvalidOperation, AttributeError):
        raise ValueError(f"line {line_no}: invalid {field}: {value!r}")
    if not d.is_finite():
        raise ValueError(f"line {line_no}: invalid {field}: {value!r}")
    return d


def build_line_items(csv_path):
    """Return [{"description": str, "amount_cents": int}, ...].

    Rows with no date and no hours (including fully blank rows) are skipped.
    Each amount = hours * rate_usd, rounded half-up to integer cents.
    """
    items = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            line_no = reader.line_num
            row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            date = row.get("date", "")
            hours_s = row.get("hours", "")
            if not date and not hours_s:
                continue  # blank / empty row
            if not hours_s:
                raise ValueError(f"line {line_no}: missing hours")
            rate_s = row.get("rate_usd", "")
            if not rate_s:
                raise ValueError(f"line {line_no}: missing rate_usd")
            hours = _dec(hours_s, "hours", line_no)
            rate = _dec(rate_s, "rate_usd", line_no)
            cents = (hours * rate * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            client = row.get("client", "")
            task = row.get("task", "")
            head = " ".join(p for p in (date, client) if p)
            description = f"{head}: {task}" if head and task else (head or task)
            if hours_s:
                description = f"{description} ({hours_s} h @ ${rate_s}/h)"
            items.append({"description": description, "amount_cents": int(cents)})
    return items


def total_cents(items):
    """Sum of amount_cents as an int."""
    return int(sum(int(i["amount_cents"]) for i in items))


def _stripe(method, path, params=None):
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY is not set")
    url = STRIPE_API + path
    data = None
    if params:
        encoded = urllib.parse.urlencode(params).encode()
        if method == "GET":
            url += "?" + encoded.decode()
        else:
            data = encoded
    req = urllib.request.Request(url, data=data, method=method)
    token = base64.b64encode(f"{key}:".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Stripe error {e.code}: {e.read().decode()}") from e


def create_draft_invoice(customer_email, items):
    """Create a draft Stripe invoice (use a test-mode key). Returns invoice dict."""
    found = _stripe("GET", "/customers", {"email": customer_email, "limit": 1})
    if found.get("data"):
        customer_id = found["data"][0]["id"]
    else:
        customer_id = _stripe("POST", "/customers", {"email": customer_email})["id"]

    for item in items:
        _stripe("POST", "/invoiceitems", {
            "customer": customer_id,
            "amount": int(item["amount_cents"]),
            "currency": "usd",
            "description": item["description"],
        })

    return _stripe("POST", "/invoices", {
        "customer": customer_id,
        "auto_advance": "false",
        "collection_method": "send_invoice",
        "days_until_due": 30,
        "pending_invoice_items_behavior": "include",
    })


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("usage: tool.py timesheet.csv [customer_email]")
        sys.exit(2)
    line_items = build_line_items(sys.argv[1])
    total = total_cents(line_items)
    print(f"{len(line_items)} items, total {total // 100}.{total % 100:02d} USD")
    if len(sys.argv) > 2:
        inv = create_draft_invoice(sys.argv[2], line_items)
        print("Created draft invoice:", inv.get("id"))
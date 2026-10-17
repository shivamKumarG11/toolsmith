import csv
import urllib.request
import urllib.parse
import json
import os
from decimal import Decimal, ROUND_HALF_UP

STRIPE_API_BASE = "https://api.stripe.com/v1"

def _get_api_key():
    return os.environ.get("STRIPE_SECRET_KEY", "")

def _stripe_post(endpoint, data):
    url = f"{STRIPE_API_BASE}/{endpoint}"
    encoded_data = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=encoded_data, method="POST")
    api_key = _get_api_key()
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        raise RuntimeError(f"Stripe API error {e.code}: {error_body}") from e

def build_line_items(csv_path):
    """
    Reads a timesheet CSV and returns a list of line items:
    [{"description": str, "amount_cents": int}, ...]
    Skips blank/empty rows.
    Calculates amount using Decimal, rounded to cents HALF-UP.
    """
    items = []
    with open(csv_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row or not any(row.values()):
                continue
            
            if not row.get("date") and not row.get("client") and not row.get("task") and not row.get("hours") and not row.get("rate_usd"):
                continue

            date = row.get("date", "").strip()
            task = row.get("task", "").strip()
            hours_str = row.get("hours", "").strip()
            rate_str = row.get("rate_usd", "").strip()

            if not hours_str or not rate_str:
                continue

            hours = Decimal(hours_str)
            rate = Decimal(rate_str)
            
            amount_dollars = hours * rate
            amount_cents = int((amount_dollars * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

            description = f"{date}: {task}" if date else task
            if not description:
                description = "Timesheet entry"

            items.append({
                "description": description,
                "amount_cents": amount_cents
            })

    return items

def total_cents(items):
    """
    Returns the integer sum of amount_cents across all items.
    """
    return sum(item["amount_cents"] for item in items)

def create_draft_invoice(customer_email, items):
    """
    Creates a fresh customer, adds invoice items, and creates a DRAFT invoice.
    Returns dict with keys: id, status, total.
    """
    # 1. Create a fresh customer
    customer_res = _stripe_post("customers", {"email": customer_email})
    customer_id = customer_res["id"]

    # 2. Create the draft invoice directly with items parameter, 
    # as creating pending invoiceitems and then pulling them can be timing-sensitive or deprecated in Stripe API.
    # Alternatively, we can pass line items directly in the invoice creation or use invoice items with explicit invoice creation.
    # Wait, the prompt states: "add one invoice item per line (amount=amount_cents, currency=usd, description), then create a DRAFT invoice for that customer".
    # Let's check how invoice items are added: POST /v1/invoiceitems with customer, amount, currency, description.
    # If Stripe doesn't automatically pull pending invoice items when creating an invoice without specifying `pending_invoice_items_behavior`, 
    # we can pass `pending_invoice_items_behavior=include` to the invoices API!
    
    for item in items:
        _stripe_post("invoiceitems", {
            "customer": customer_id,
            "amount": str(item["amount_cents"]),
            "currency": "usd",
            "description": item["description"]
        })

    # 3. Create a draft invoice for that customer, explicitly instructing Stripe to include pending invoice items
    invoice_data = {
        "customer": customer_id,
        "collection_method": "send_invoice",
        "days_until_due": "7",
        "auto_advance": "false",
        "pending_invoice_items_behavior": "include"
    }

    invoice_res = _stripe_post("invoices", invoice_data)

    return {
        "id": invoice_res["id"],
        "status": invoice_res["status"],
        "total": invoice_res["total"]
    }
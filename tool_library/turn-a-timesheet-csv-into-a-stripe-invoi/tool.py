import csv
import urllib.request
import urllib.parse
import json
import os
from decimal import Decimal, ROUND_HALF_UP

def build_line_items(csv_path):
    line_items = []
    with open(csv_path, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Skip empty or blank rows
            if not row or not any(row.values()):
                continue
            
            hours_str = row.get('hours', '').strip()
            rate_str = row.get('rate_usd', '').strip()
            task_str = row.get('task', '').strip()
            date_str = row.get('date', '').strip()
            
            if not hours_str or not rate_str:
                continue
                
            hours = Decimal(hours_str)
            rate = Decimal(rate_str)
            
            # amount in dollars
            total_usd = hours * rate
            # convert to cents using Decimal and ROUND_HALF_UP
            cents_decimal = (total_usd * Decimal('100')).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
            amount_cents = int(cents_decimal)
            
            description = f"{date_str}: {task_str}" if date_str and task_str else (task_str or date_str or "Timesheet Entry")
            
            line_items.append({
                "description": description,
                "amount_cents": amount_cents
            })
            
    return line_items

def total_cents(items):
    return sum(item["amount_cents"] for item in items)

def _stripe_request(endpoint, data=None):
    api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    url = f"https://api.stripe.com/v1/{endpoint}"
    
    encoded_data = None
    if data is not None:
        encoded_data = urllib.parse.urlencode(data, doseq=True).encode('utf-8')
        
    req = urllib.request.Request(url, data=encoded_data, method="POST" if data is not None else "GET")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    
    try:
        with urllib.request.urlopen(req) as response:
            body = response.read().decode('utf-8')
            return json.loads(body)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        raise RuntimeError(f"Stripe API error {e.code}: {error_body}") from e

def create_draft_invoice(customer_email, items):
    # 1. Create a FRESH customer
    customer_data = {
        "email": customer_email
    }
    customer_res = _stripe_request("customers", customer_data)
    customer_id = customer_res["id"]
    
    # 2. Add each item directly as invoice lines via invoiceitems, but also explicitly 
    # attach them or use the invoice items endpoint properly.
    # Note: In Stripe API, creating invoice items with `customer` creates *pending* invoice items.
    # When creating an invoice, by default Stripe automatically includes pending invoice items 
    # IF they aren't explicitly scoped or if auto-inclusion is handled, OR we can pass `invoice_items` 
    # directly when creating the invoice, or explicitly populate the invoice.
    # Let's create the invoice items and explicitly pass their IDs or let Stripe pull them,
    # but to be 100% robust against Stripe API behavior regarding pending invoice items,
    # let's create invoice items and check if we need to pass them or if creating an invoice 
    # with `customer` automatically pulls pending items. Wait, why did total come out as 0?
    # Because in newer Stripe API versions, pending invoice items are NOT automatically pulled 
    # into a manual invoice creation unless `invoice_items` array is specified or they are added via invoice lines!
    # Let's add them as invoice items AND pass them via `invoice_items` parameter or create invoice items with `invoice` parameter, 
    # OR construct the invoice with individual line items via invoice items directly attached to the invoice.
    
    # Alternatively, create the invoice first (empty draft), then add invoice items specifying `invoice`.
    invoice_data = {
        "customer": customer_id,
        "collection_method": "send_invoice",
        "days_until_due": 7,
        "auto_advance": "false"
    }
    invoice_res = _stripe_request("invoices", invoice_data)
    invoice_id = invoice_res["id"]
    
    for item in items:
        inv_item_data = {
            "customer": customer_id,
            "invoice": invoice_id,
            "amount": item["amount_cents"],
            "currency": "usd",
            "description": item["description"]
        }
        _stripe_request("invoiceitems", inv_item_data)
        
    # Retrieve the updated invoice to get the correct computed total and status
    invoice_res = _stripe_request(f"invoices/{invoice_id}")
    
    return {
        "id": invoice_res["id"],
        "status": invoice_res["status"],
        "total": invoice_res["total"]
    }
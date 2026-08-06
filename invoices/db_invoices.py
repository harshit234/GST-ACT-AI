"""
db_invoices.py — Database operations for Customer Invoice Generator
===================================================================
All Supabase queries for sales invoices, merchant profiles, and HSN cache.
Reuses the existing bills table with bill_type='sale'.
"""

import os
import re
import sys
from datetime import datetime
from db import get_supabase_client, get_or_create_merchant


# ════════════════════════════════════════════════════════════════
# FISCAL YEAR & INVOICE NUMBERING
# ════════════════════════════════════════════════════════════════

def get_fiscal_year() -> str:
    """
    Get current Indian fiscal year string (e.g., '2026-27').
    Indian FY runs April 1 to March 31.
    """
    now = datetime.now()
    if now.month >= 4:  # April onwards
        return f"{now.year}-{str(now.year + 1)[-2:]}"
    else:  # January to March
        return f"{now.year - 1}-{str(now.year)[-2:]}"


def get_next_invoice_number(whatsapp_number: str) -> str:
    """
    Generate the next auto-incrementing invoice number for a merchant.
    Format: INV/2026-27/001, INV/2026-27/002, ...
    Scoped per merchant (whatsapp_number) and fiscal year.
    """
    client = get_supabase_client()
    fy = get_fiscal_year()
    prefix = f"INV/{fy}/"

    # Count existing sales invoices for this merchant in this fiscal year
    result = (
        client.table("bills")
        .select("invoice_number")
        .eq("whatsapp_number", whatsapp_number)
        .eq("bill_type", "sale")
        .like("invoice_number", f"{prefix}%")
        .execute()
    )

    existing = result.data or []

    # Find the max sequence number
    max_seq = 0
    for row in existing:
        inv_num = row.get("invoice_number", "")
        try:
            seq = int(inv_num.replace(prefix, ""))
            if seq > max_seq:
                max_seq = seq
        except (ValueError, TypeError):
            pass

    next_seq = max_seq + 1
    return f"{prefix}{next_seq:03d}"


# ════════════════════════════════════════════════════════════════
# SALES INVOICE CRUD
# ════════════════════════════════════════════════════════════════

def save_sales_invoice(invoice_data: dict, whatsapp_number: str) -> dict:
    """
    Save a sales invoice to the bills table with bill_type='sale'.

    Input:  Invoice data dict + merchant WhatsApp number
    Output: Dict with bill_id and invoice_number
    """
    client = get_supabase_client()

    # Get or create merchant record
    merchant_id = get_or_create_merchant(
        client, whatsapp_number,
        vendor_name=invoice_data.get("merchant_name"),
        vendor_gstin=invoice_data.get("merchant_gstin")
    )

    invoice_number = invoice_data.get("invoice_number")
    if not invoice_number:
        invoice_number = get_next_invoice_number(whatsapp_number)

    # Check for duplicate invoice number
    dup_check = (
        client.table("bills")
        .select("id")
        .eq("whatsapp_number", whatsapp_number)
        .eq("bill_type", "sale")
        .eq("invoice_number", invoice_number)
        .execute()
    )
    if dup_check.data and len(dup_check.data) > 0:
        raise ValueError(f"Invoice number {invoice_number} already exists.")

    bill_record = {
        "merchant_id": merchant_id,
        "bill_type": "sale",
        "invoice_number": invoice_number,
        "invoice_date": invoice_data.get("invoice_date", datetime.now().strftime("%Y-%m-%d")),
        # Merchant details stored in vendor fields for PDF consistency
        "vendor_name": invoice_data.get("merchant_name", ""),
        "vendor_gstin": invoice_data.get("merchant_gstin", ""),
        # Customer details
        "customer_name": invoice_data.get("customer_name", ""),
        "customer_gstin": invoice_data.get("customer_gstin", ""),
        "customer_phone": invoice_data.get("customer_phone", ""),
        "customer_email": invoice_data.get("customer_email", ""),
        "customer_address": invoice_data.get("customer_address", ""),
        "customer_state": invoice_data.get("customer_state", ""),
        # Financial data
        "line_items": invoice_data.get("line_items", []),
        "subtotal": float(invoice_data.get("subtotal", 0)),
        "cgst": float(invoice_data.get("cgst", 0)),
        "sgst": float(invoice_data.get("sgst", 0)),
        "igst": float(invoice_data.get("igst", 0)),
        "total_amount": float(invoice_data.get("total_amount", 0)),
        "amount_in_words": invoice_data.get("amount_in_words", ""),
        # Metadata
        "whatsapp_number": whatsapp_number,
        "status": "active",
    }

    print(f"Saving sales invoice {invoice_number} to Supabase...")
    insert_result = client.table("bills").insert(bill_record).execute()
    bill_id = insert_result.data[0]["id"]
    print(f"Sales invoice saved. Bill ID: {bill_id}")

    return {"bill_id": bill_id, "invoice_number": invoice_number}


def update_invoice_pdf_url(bill_id: str, pdf_url: str) -> None:
    """Update the PDF URL for a saved invoice."""
    client = get_supabase_client()
    client.table("bills").update({"pdf_url": pdf_url}).eq("id", bill_id).execute()
    print(f"PDF URL updated for bill {bill_id}")


def delete_invoice(bill_id: str) -> None:
    """Hard-delete an invoice (used for rollback on PDF failure)."""
    client = get_supabase_client()
    client.table("bills").delete().eq("id", bill_id).execute()
    print(f"Invoice {bill_id} deleted (rollback)")


def soft_delete_invoice(bill_id: str, whatsapp_number: str) -> bool:
    """Soft-delete an invoice by setting status to 'deleted'."""
    client = get_supabase_client()
    result = (
        client.table("bills")
        .update({"status": "deleted"})
        .eq("id", bill_id)
        .eq("whatsapp_number", whatsapp_number)
        .eq("bill_type", "sale")
        .execute()
    )
    return bool(result.data)


def get_sales_invoices(whatsapp_number: str, search: str = "", status: str = "") -> list:
    """
    Get all sales invoices for a merchant.
    Supports search by customer name, invoice number.
    Supports status filter.
    """
    client = get_supabase_client()
    query = (
        client.table("bills")
        .select("id, invoice_number, invoice_date, customer_name, customer_phone, "
                "customer_gstin, subtotal, cgst, sgst, igst, total_amount, "
                "pdf_url, status, created_at")
        .eq("whatsapp_number", whatsapp_number)
        .eq("bill_type", "sale")
        .neq("status", "deleted")
        .order("created_at", desc=True)
    )

    if status and status != "all":
        query = query.eq("status", status)

    result = query.execute()
    invoices = result.data or []

    # Client-side search filter (Supabase free tier has limited text search)
    if search:
        search_lower = search.lower()
        invoices = [
            inv for inv in invoices
            if search_lower in (inv.get("customer_name") or "").lower()
            or search_lower in (inv.get("invoice_number") or "").lower()
        ]

    return invoices


def get_sales_invoice(bill_id: str, whatsapp_number: str = "") -> dict:
    """Get a single sales invoice by ID."""
    client = get_supabase_client()
    result = (
        client.table("bills")
        .select("*")
        .eq("id", bill_id)
        .eq("bill_type", "sale")
        .execute()
    )
    invoices = result.data or []
    if not invoices:
        return None

    invoice = invoices[0]
    # Verify ownership if phone provided
    if whatsapp_number and invoice.get("whatsapp_number") != whatsapp_number:
        return None

    return invoice


# ════════════════════════════════════════════════════════════════
# MERCHANT PROFILE
# ════════════════════════════════════════════════════════════════

def get_merchant_profile(whatsapp_number: str) -> dict:
    """Fetch merchant business profile for invoice header."""
    client = get_supabase_client()
    result = (
        client.table("merchants")
        .select("id, name, gstin, whatsapp_number, business_name, business_gstin, "
                "business_address, business_state, business_phone, business_email")
        .eq("whatsapp_number", whatsapp_number)
        .execute()
    )
    if result.data and len(result.data) > 0:
        return result.data[0]
    return {}


def update_merchant_profile(whatsapp_number: str, profile: dict) -> dict:
    """Save/update merchant business profile details."""
    client = get_supabase_client()

    update_data = {}
    allowed_fields = [
        "business_name", "business_gstin", "business_address",
        "business_state", "business_phone", "business_email"
    ]
    for field in allowed_fields:
        if field in profile:
            update_data[field] = profile[field]

    if not update_data:
        return get_merchant_profile(whatsapp_number)

    result = (
        client.table("merchants")
        .update(update_data)
        .eq("whatsapp_number", whatsapp_number)
        .execute()
    )
    if result.data and len(result.data) > 0:
        return result.data[0]

    # If no merchant exists yet, create one
    merchant_id = get_or_create_merchant(client, whatsapp_number)
    client.table("merchants").update(update_data).eq("id", merchant_id).execute()
    return get_merchant_profile(whatsapp_number)


# ════════════════════════════════════════════════════════════════
# HSN CACHE
# ════════════════════════════════════════════════════════════════

def normalize_item_name(item_name: str) -> str:
    """
    Normalize item name for HSN cache lookup.
    Lowercase, trim spaces, remove punctuation.
    """
    if not item_name:
        return ""
    normalized = item_name.lower().strip()
    normalized = re.sub(r'[^\w\s]', '', normalized)  # Remove punctuation
    normalized = re.sub(r'\s+', ' ', normalized)       # Collapse whitespace
    return normalized


def hsn_cache_lookup(item_name: str) -> dict:
    """
    Look up HSN code from local cache.
    Returns dict with hsn_code, gst_rate, unit or None if not cached.
    """
    normalized = normalize_item_name(item_name)
    if not normalized:
        return None

    client = get_supabase_client()
    result = (
        client.table("hsn_cache")
        .select("hsn_code, gst_rate, unit")
        .eq("item_name_normalized", normalized)
        .execute()
    )

    if result.data and len(result.data) > 0:
        print(f"[HSN Cache] Hit for '{normalized}'")
        return result.data[0]

    return None


def hsn_cache_save(item_name: str, hsn_code: str, gst_rate: float, unit: str) -> None:
    """Save HSN lookup result to cache. Upsert to avoid duplicates."""
    normalized = normalize_item_name(item_name)
    if not normalized:
        return

    client = get_supabase_client()
    try:
        client.table("hsn_cache").upsert({
            "item_name_normalized": normalized,
            "hsn_code": hsn_code,
            "gst_rate": gst_rate,
            "unit": unit,
        }, on_conflict="item_name_normalized").execute()
        print(f"[HSN Cache] Saved '{normalized}' → {hsn_code}")
    except Exception as e:
        # Cache save failures are non-critical
        print(f"[HSN Cache] Save error: {e}", file=sys.stderr)

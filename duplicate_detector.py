"""
duplicate_detector.py -- Fuzzy Duplicate Invoice Detection
=========================================================
Detects duplicate invoices even when OCR introduces character-level
errors (e.g. GSTIN "P" misread as "F").

Matching rules (ALL three must pass):
  1. Invoice Number  -- normalized exact match (strips slashes/dashes/spaces)
  2. Total Amount    -- exact match (no tolerance)
  3. Vendor Name     -- difflib similarity > 80%

If all three match, returns (existing_bill_id, existing_invoice_date).
"""

import re
from difflib import SequenceMatcher


# ── Helpers ──────────────────────────────────────────────────────

def normalize_invoice_number(inv_no: str) -> str:
    """
    Normalizes invoice number for comparison.
    Strips slashes, dashes, spaces and upper-cases so that
    PDP26-27-026, PDP/26-27/026, PDP26-27/026 all compare equal.
    """
    if not inv_no:
        return ""
    return re.sub(r"[^A-Z0-9]", "", inv_no.upper())


def vendor_name_similarity(name_a: str, name_b: str) -> float:
    """
    Returns a similarity ratio (0.0–1.0) between two vendor names
    using difflib.SequenceMatcher.

    Case-insensitive, strips extra whitespace.
    """
    if not name_a or not name_b:
        return 0.0
    a = " ".join(name_a.lower().split())
    b = " ".join(name_b.lower().split())
    return SequenceMatcher(None, a, b).ratio()


def amounts_match_exact(amount_a: float, amount_b: float) -> bool:
    """Returns True only when two amounts are exactly equal."""
    return amount_a == amount_b


# ── Main detector ────────────────────────────────────────────────

VENDOR_NAME_THRESHOLD = 0.80  # 80% similarity


def check_fuzzy_duplicate(
    client,
    invoice_number: str,
    total_amount: float,
    vendor_name: str,
    whatsapp_number: str,
) -> tuple[str, str] | None:
    """
    Checks whether a new invoice is a fuzzy duplicate of an existing one.

    Queries all bills for the same whatsapp_number (merchant) and compares:
      * Normalized invoice number  (exact match after stripping punctuation)
      * Total amount               (exact match)
      * Vendor name                (>80% difflib similarity)

    Returns a (bill_id, invoice_date) tuple if a fuzzy duplicate is found,
    or None if no duplicate exists.
    """
    if not invoice_number:
        return None

    normalized_new = normalize_invoice_number(invoice_number)
    if not normalized_new:
        return None

    # Fetch candidate bills for this merchant
    existing_bills = (
        client.table("bills")
        .select("id, invoice_number, invoice_date, total_amount, vendor_name")
        .eq("whatsapp_number", whatsapp_number)
        .eq("bill_type", "purchase")
        .execute()
    )

    for existing in (existing_bills.data or []):
        # Signal 1: Invoice number must match (normalized exact)
        existing_inv = normalize_invoice_number(existing.get("invoice_number", ""))
        if existing_inv != normalized_new:
            continue

        # Signal 2: Total amount must match exactly
        existing_amount = float(existing.get("total_amount") or 0)
        if not amounts_match_exact(total_amount, existing_amount):
            continue

        # Signal 3: Vendor name must be sufficiently similar (>80%)
        existing_vendor = existing.get("vendor_name", "")
        similarity = vendor_name_similarity(vendor_name, existing_vendor)
        if similarity < VENDOR_NAME_THRESHOLD:
            continue

        # All three signals match -- this is a fuzzy duplicate
        existing_id   = existing["id"]
        existing_date = existing.get("invoice_date") or "N/A"
        print(
            f"Fuzzy duplicate detected: "
            f"invoice={invoice_number}, "
            f"vendor similarity={similarity:.0%}, "
            f"amount=Rs.{existing_amount}. "
            f"Existing Bill ID: {existing_id} (date: {existing_date})"
        )
        return existing_id, existing_date

    return None

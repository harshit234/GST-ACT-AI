"""
duplicate_detector.py — Fuzzy Duplicate Invoice Detection
=========================================================
Detects duplicate invoices even when OCR introduces character-level
errors (e.g. GSTIN "P" misread as "F").

Instead of relying on exact GSTIN matching, compares three signals:
  1. Invoice Number  — normalized exact match
  2. Invoice Amount  — match within ₹1 tolerance
  3. Vendor Name     — difflib similarity > 80%

If ALL three match, the invoice is flagged as a potential duplicate.
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


def amounts_match(amount_a: float, amount_b: float, tolerance: float = 1.0) -> bool:
    """Returns True if two amounts are within the given tolerance (default ₹1)."""
    return abs(amount_a - amount_b) <= tolerance


# ── Main detector ────────────────────────────────────────────────

VENDOR_NAME_THRESHOLD = 0.80  # 80% similarity


def check_fuzzy_duplicate(
    client,
    invoice_number: str,
    total_amount: float,
    vendor_name: str,
    whatsapp_number: str,
) -> str | None:
    """
    Checks whether a new invoice is a fuzzy duplicate of an existing one.

    Queries all bills for the same whatsapp_number (merchant) and compares:
      • Normalized invoice number  (exact match)
      • Total amount               (within ₹1)
      • Vendor name                (>80% similarity)

    Returns the existing bill ID if a fuzzy duplicate is found, else None.
    """
    if not invoice_number:
        return None

    normalized_new = normalize_invoice_number(invoice_number)
    if not normalized_new:
        return None

    # Fetch candidate bills for this merchant
    existing_bills = (
        client.table("bills")
        .select("id, invoice_number, total_amount, vendor_name")
        .eq("whatsapp_number", whatsapp_number)
        .execute()
    )

    for existing in (existing_bills.data or []):
        # Signal 1: Invoice number must match (normalized)
        existing_inv = normalize_invoice_number(existing.get("invoice_number", ""))
        if existing_inv != normalized_new:
            continue

        # Signal 2: Total amount must match (within tolerance)
        existing_amount = float(existing.get("total_amount") or 0)
        if not amounts_match(total_amount, existing_amount):
            continue

        # Signal 3: Vendor name must be sufficiently similar
        existing_vendor = existing.get("vendor_name", "")
        similarity = vendor_name_similarity(vendor_name, existing_vendor)
        if similarity < VENDOR_NAME_THRESHOLD:
            continue

        # All three signals match — this is a fuzzy duplicate
        existing_id = existing["id"]
        print(
            f"Fuzzy duplicate detected: "
            f"invoice={invoice_number}, "
            f"vendor similarity={similarity:.0%}, "
            f"amount diff=₹{abs(total_amount - existing_amount):.2f}. "
            f"Existing Bill ID: {existing_id}"
        )
        return existing_id

    return None

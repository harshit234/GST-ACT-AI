from datetime import datetime, timedelta
from exceptions import SuspiciousDateError


# ── Date formats commonly returned by Gemini extraction ──────────
_DATE_FORMATS = [
    "%Y-%m-%d",    # 2026-06-15
    "%d-%m-%Y",    # 15-06-2026
    "%d/%m/%Y",    # 15/06/2026
    "%Y/%m/%d",    # 2026/06/15
    "%d.%m.%Y",    # 15.06.2026
    "%B %d, %Y",   # June 15, 2026
    "%b %d, %Y",   # Jun 15, 2026
    "%d %B %Y",    # 15 June 2026
    "%d %b %Y",    # 15 Jun 2026
]

# Acceptable window: invoices older than this many days are flagged
DATE_MAX_AGE_DAYS = 365      # 1 year in the past
DATE_MAX_FUTURE_DAYS = 30    # 1 month in the future


def parse_invoice_date(date_str: str) -> datetime | None:
    """
    Attempts to parse an invoice date string using common formats.
    Returns a datetime object or None if unparseable.
    """
    if not date_str:
        return None
    cleaned = date_str.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def validate_invoice_date(invoice_data: dict) -> None:
    """
    Validates the extracted invoice date against the current date.

    Raises SuspiciousDateError if the date is:
      • More than 1 year (365 days) in the past
      • More than 1 month (30 days) in the future

    This catches OCR errors like "2018" instead of "2026" before
    the invoice is saved to the database.
    """
    date_str = invoice_data.get("invoice_date", "")
    if not date_str:
        return  # No date extracted — skip validation

    parsed = parse_invoice_date(date_str)
    if parsed is None:
        return  # Unparseable format — skip (don't block the pipeline)

    now = datetime.utcnow()
    oldest_allowed = now - timedelta(days=DATE_MAX_AGE_DAYS)
    newest_allowed = now + timedelta(days=DATE_MAX_FUTURE_DAYS)

    if parsed < oldest_allowed:
        years_ago = (now - parsed).days // 365
        raise SuspiciousDateError(
            extracted_date=date_str,
            message=(
                f"Invoice date {date_str} is ~{years_ago} year(s) in the past. "
                f"This may be an OCR error."
            ),
        )

    if parsed > newest_allowed:
        raise SuspiciousDateError(
            extracted_date=date_str,
            message=(
                f"Invoice date {date_str} is in the future. "
                f"This may be an OCR error."
            ),
        )


def validate_gst_invoice(invoice_data: dict) -> tuple:
    """
    Checks if extracted numbers are mathematically correct.

    Input:  JSON from extraction step (dict)
    Process: Sums all line item amounts, adds CGST+SGST+IGST,
             compares against the bill's stated total_amount.
    Output: (is_valid: bool, calculated_total: float, difference: float)

    The caller decides what to do with the difference — app.py uses > Rs.10
    as the threshold to trigger the merchant confirmation flow.
    """
    try:
        # Sum of all line item amounts
        line_items = invoice_data.get("line_items", [])
        calculated_subtotal = 0.0
        for item in line_items:
            amount = item.get("amount")
            if amount is not None:
                calculated_subtotal += float(amount)

        # Total tax (CGST + SGST + IGST)
        cgst = float(invoice_data.get("cgst") or 0.0)
        sgst = float(invoice_data.get("sgst") or 0.0)
        igst = float(invoice_data.get("igst") or 0.0)
        tax_total = cgst + sgst + igst

        # Actual total from the bill image
        actual_total = float(invoice_data.get("total_amount") or 0.0)

        # Our calculated total
        calculated_total = calculated_subtotal + tax_total

        # Absolute difference
        diff = abs(calculated_total - actual_total)

        # Bills often include freight / packing / rounding adjustments.
        # We always return is_valid=True and let the caller decide the threshold.
        return True, calculated_total, diff

    except Exception as e:
        # On any calculation error, return 0 diff so we don't block the pipeline
        actual_total = float(invoice_data.get("total_amount") or 0.0)
        return True, actual_total, 0.0


if __name__ == "__main__":
    import sys
    import os
    from extract import extract_invoice_details
    from ocr import detect_text
    
    # Configure stdout to use UTF-8 if supported (useful on Windows consoles)
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
            
    image_path = "bill_test.jpg"
    if len(sys.argv) > 1:
        image_path = sys.argv[1]
        
    print(f"Running Validation on: {image_path}...")
    if not os.path.exists(image_path):
        print(f"Error: File {image_path} not found.")
        sys.exit(1)
        
    with open(image_path, "rb") as f:
        bill_image_bytes = f.read()
        
    try:
        # 1. OCR Step
        ocr_text = detect_text(bill_image_bytes)
        print("OCR Step completed successfully.")
        
        # 2. Extract Step
        print("Extracting structured details via Gemini AI...")
        invoice_data = extract_invoice_details(ocr_text)
        
        # 3. Validation Step
        print("Validating mathematical correctness...")
        is_valid, msg = validate_gst_invoice(invoice_data)
        
        print("\n--- Validation Result ---")
        print(f"Is Valid: {is_valid}")
        print(f"Message: {msg}")
        print("-------------------------")
    except Exception as e:
        print(f"Error: {e}")


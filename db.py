import os
import json
from datetime import datetime
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

from exceptions import DuplicateInvoiceError

def get_supabase_client() -> Client:
    """Initializes and returns the Supabase client."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise ValueError("SUPABASE_URL or SUPABASE_KEY not set.")
    return create_client(url, key)

def get_or_create_merchant(client: Client, whatsapp_number: str, vendor_name: str = None, vendor_gstin: str = None) -> str:
    """
    Looks up merchant by WhatsApp number.
    If not found, creates a new merchant entry.
    Returns the merchant UUID.
    """
    # Look up existing merchant by WhatsApp number
    result = client.table("merchants").select("id").eq("whatsapp_number", whatsapp_number).execute()
    
    if result.data and len(result.data) > 0:
        merchant_id = result.data[0]["id"]
        print(f"Found existing merchant: {merchant_id}")
        return merchant_id
    
    # Merchant not found — create new entry
    new_merchant = {
        "whatsapp_number": whatsapp_number,
        "name": vendor_name or "Unknown Vendor",
        "gstin": vendor_gstin or None,
    }
    insert_result = client.table("merchants").insert(new_merchant).execute()
    merchant_id = insert_result.data[0]["id"]
    print(f"Created new merchant: {merchant_id}")
    return merchant_id



def save_invoice(invoice_data: dict, whatsapp_number: str) -> str:
    """
    Saves extracted bill data into Supabase.
    
    Input: JSON from File 2 (invoice_data) + WhatsApp number of sender
    Process: Looks up merchant by WhatsApp number,
             creates new merchant entry if not found,
             checks for duplicates using normalized invoice number,
             saves all bill fields to bills table,
             links bill to correct merchant automatically.
    Output: Bill ID (UUID) confirming save was successful.
    """
    client = get_supabase_client()
    
    # Step 1: Get or create merchant
    vendor_name = invoice_data.get("vendor_name")
    vendor_gstin = invoice_data.get("vendor_gstin")
    merchant_id = get_or_create_merchant(client, whatsapp_number, vendor_name, vendor_gstin)
    
    # Step 2: Build bill record
    bill_record = {
        "merchant_id": merchant_id,
        "vendor_name": vendor_name,
        "vendor_gstin": vendor_gstin,
        "invoice_number": invoice_data.get("invoice_number"),
        "invoice_date": invoice_data.get("invoice_date"),
        "cgst": invoice_data.get("cgst", 0.0),
        "sgst": invoice_data.get("sgst", 0.0),
        "igst": invoice_data.get("igst", 0.0),
        "total_amount": invoice_data.get("total_amount", 0.0),
        "line_items": invoice_data.get("line_items", []),  # stored as JSONB
        "whatsapp_number": whatsapp_number,
    }
    
    # Step 3: Fuzzy duplicate detection
    # Instead of exact GSTIN matching (which fails on OCR errors like P→F),
    # compare invoice number + amount + vendor name similarity (>80%).
    from duplicate_detector import check_fuzzy_duplicate
    existing_id = check_fuzzy_duplicate(
        client=client,
        invoice_number=bill_record.get("invoice_number", ""),
        total_amount=float(bill_record.get("total_amount") or 0),
        vendor_name=bill_record.get("vendor_name", ""),
        whatsapp_number=whatsapp_number,
    )
    if existing_id:
        raise DuplicateInvoiceError(existing_id)

    # Step 4: Insert bill into bills table
    print("Saving bill to Supabase...")
    insert_result = client.table("bills").insert(bill_record).execute()
    bill_id = insert_result.data[0]["id"]
    print(f"Bill saved successfully. Bill ID: {bill_id}")
    return bill_id

def get_monthly_summary(whatsapp_number: str) -> dict:
    """
    Returns the monthly GST total for a merchant identified by their WhatsApp number.

    Input:  WhatsApp number of the merchant
    Process: Queries the bills table for the current calendar month,
             sums up CGST, SGST, IGST, and total_amount across all bills.
    Output: Dict with month, bill_count, total_amount, cgst, sgst, igst, grand_tax_total
    """
    client = get_supabase_client()
    now = datetime.utcnow()
    # First and last day of current month in ISO format
    month_start = datetime(now.year, now.month, 1).isoformat() + "Z"
    if now.month == 12:
        month_end = datetime(now.year + 1, 1, 1).isoformat() + "Z"
    else:
        month_end = datetime(now.year, now.month + 1, 1).isoformat() + "Z"

    result = (
        client.table("bills")
        .select("cgst, sgst, igst, total_amount")
        .eq("whatsapp_number", whatsapp_number)
        .gte("created_at", month_start)
        .lt("created_at", month_end)
        .execute()
    )

    bills = result.data or []
    total_amount = sum(float(b.get("total_amount") or 0) for b in bills)
    cgst          = sum(float(b.get("cgst")         or 0) for b in bills)
    sgst          = sum(float(b.get("sgst")         or 0) for b in bills)
    igst          = sum(float(b.get("igst")         or 0) for b in bills)
    grand_tax     = cgst + sgst + igst

    return {
        "month":           now.strftime("%B %Y"),
        "bill_count":      len(bills),
        "total_amount":    total_amount,
        "cgst":            cgst,
        "sgst":            sgst,
        "igst":            igst,
        "grand_tax_total": grand_tax,
    }


if __name__ == "__main__":
    import sys
    from extract import extract_invoice_details
    from ocr import detect_text
    
    # Configure stdout to use UTF-8 if supported (useful on Windows consoles)
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    
    # Default test values
    image_path = sys.argv[1] if len(sys.argv) > 1 else "bill_test.jpg"
    whatsapp_number = sys.argv[2] if len(sys.argv) > 2 else "+919999999999"
    
    print(f"Running full pipeline on: {image_path} | WhatsApp: {whatsapp_number}")
    if not os.path.exists(image_path):
        print(f"Error: File {image_path} not found.")
        sys.exit(1)
    
    with open(image_path, "rb") as f:
        bill_image_bytes = f.read()
    
    try:
        # Step 1: OCR
        ocr_text = detect_text(bill_image_bytes)
        print("OCR Step completed successfully.")
        
        # Step 2: Extract
        print("Extracting structured details via Gemini AI...")
        invoice_data = extract_invoice_details(ocr_text)
        print(f"Extracted: {invoice_data.get('vendor_name')} | Total: {invoice_data.get('total_amount')}")
        
        # Step 3: Save to Supabase
        bill_id = save_invoice(invoice_data, whatsapp_number)
        
        print("\n--- Result ---")
        print(f"Bill ID (UUID): {bill_id}")
        print("--------------")
    except Exception as e:
        print(f"Error: {e}")

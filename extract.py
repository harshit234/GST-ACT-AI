import os
import json
import re
import urllib.request
import google.generativeai as genai
from dotenv import load_dotenv
from exceptions import NotAnInvoiceError, LowConfidenceError
from db import get_hsn_from_cache, save_hsn_to_cache

load_dotenv()

# Configure Gemini
api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)


# ── OpenRouter HSN lookup (cache-aware) ────────────────────────────────

def lookup_hsn_for_item(description: str) -> dict:
    """
    Returns {hsn_code, gst_rate, unit} for a given item description.

    Strategy:
      1. Check Supabase hsn_cache — if hit, return immediately (no API call).
      2. On cache miss, call OpenRouter (google/gemini-flash-1.5) to infer
         the HSN code, GST rate, and common unit of measure.
      3. Save the API result to cache before returning.

    Failures are non-fatal: returns empty strings / None on any error.
    """
    description = (description or "").strip()
    if not description:
        return {"hsn_code": None, "gst_rate": None, "unit": None}

    # ── 1. Cache check ────────────────────────────────────────────
    cached = get_hsn_from_cache(description)
    if cached:
        print(f"[HSN Cache] HIT: '{description}' -> HSN {cached.get('hsn_code')}")
        return cached  # {hsn_code, gst_rate, unit}

    # ── 2. OpenRouter API call ───────────────────────────────────────
    print(f"[HSN Cache] MISS: '{description}' -- calling OpenRouter...")
    or_key = os.getenv("OPENROUTERAPI_KEY", "").strip()
    if not or_key:
        print("[HSN] OPENROUTERAPI_KEY not set -- skipping HSN lookup.")
        return {"hsn_code": None, "gst_rate": None, "unit": None}

    prompt = (
        f"For the following product/service item name from an Indian GST invoice, "
        f"return a JSON object with exactly these keys:\n"
        f"  hsn_code  - 4-8 digit HSN or SAC code (string, e.g. \"4412\")\n"
        f"  gst_rate  - applicable GST rate as an integer (0/5/12/18/28)\n"
        f"  unit      - common unit of measure (e.g. PCS, KG, MTR, SQF, NOS)\n\n"
        f"Item: {description}\n\n"
        f"Return ONLY the raw JSON object, no markdown, no explanation."
    )

    payload = json.dumps({
        "model": "google/gemini-2.5-flash",
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
    }).encode("utf-8")

    req = urllib.request.Request(
        url="https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {or_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        # Strip potential markdown fences
        content = re.sub(r"^```[a-z]*\n?", "", content.strip())
        content = re.sub(r"\n?```$", "", content.strip())
        hsn_data = json.loads(content)
    except Exception as e:
        print(f"[HSN] OpenRouter error for '{description}': {e}")
        return {"hsn_code": None, "gst_rate": None, "unit": None}

    hsn_code = str(hsn_data.get("hsn_code") or "").strip() or None
    gst_rate = hsn_data.get("gst_rate")
    unit     = str(hsn_data.get("unit") or "").strip() or None

    # ── 3. Save to cache ────────────────────────────────────────────
    save_hsn_to_cache(description, hsn_code, gst_rate, unit)

    return {"hsn_code": hsn_code, "gst_rate": gst_rate, "unit": unit}


def extract_invoice_details(ocr_text: str) -> dict:
    """
    Extracts structured GST invoice details from raw OCR text using Gemini.
    
    Input: Raw OCR text
    Process: Gemini AI reads and understands the text, identifying vendor name,
             GSTIN, line items, HSN codes, GST amounts, and total.
    Output: Clean JSON with all bill fields structured.
    """
    # Reload environment variables to catch runtime changes to the API key
    load_dotenv(override=True)
    current_key = os.getenv("GEMINI_API_KEY")
    if not current_key:
        raise ValueError("GEMINI_API_KEY not set in environment.")
        
    # Configure/re-configure genai with the latest key
    genai.configure(api_key=current_key)
        
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    prompt = f"""
    Analyze the following raw OCR text from a bill/invoice and extract key structured fields.
    Return a clean JSON object containing the following keys:
    - is_invoice: true if this text is from a bill/invoice/receipt, false if it appears to be
      something else (menu, letter, random text, selfie description, etc.)
    - low_confidence: true ONLY if you detect that:
      a) The bill is handwritten (not printed) or a carbon copy — printed/typed bills are always fine.
      b) The bill represents a credit note or debit note (not a tax invoice or purchase invoice).
      c) The OCR text is severely cut off, blurry, or garbled such that vendor name, GSTIN, or total amount cannot be determined at all.
      Otherwise, set this to false. Bills with multiple GST rates, freight charges, packing charges, cess, or rounding adjustments are PERFECTLY VALID — do NOT flag them as low_confidence. If you can read the key fields, set low_confidence to false.
    - vendor_name: The name of the vendor (e.g. Pooja Decorative Plywoods)
    - vendor_gstin: Vendor's GSTIN/UIN number
    - invoice_number: Invoice number
    - invoice_date: Invoice date (format as YYYY-MM-DD or keep original format if unsure)
    - cgst: Total CGST amount (numeric, 0.0 if not present)
    - sgst: Total SGST amount (numeric, 0.0 if not present)
    - igst: Total IGST amount (numeric, 0.0 if not present)
    - total_amount: Total bill amount (numeric, including GST)
    - line_items: A list of objects, each containing:
        - si_no: Serial number / serial ID (integer or string)
        - description: Description of goods / services
        - hsn_sac: HSN or SAC code (string or null)
        - quantity: Quantity purchased (numeric or null)
        - rate: Rate per unit (numeric or null)
        - amount: Total amount for this item (numeric or null)
        - gst_rate: GST rate applied to this item in percentage (numeric or null, e.g. 5, 12, 18, 28)
        - gst_amount: GST amount for this item (numeric or null)

    Raw OCR Text:
    {ocr_text}
    """
    
    response = model.generate_content(
        prompt,
        generation_config={"response_mime_type": "application/json"}
    )
    
    try:
        result = json.loads(response.text.strip())
    except Exception as e:
        # Fallback regex parsing if JSON format is wrapped/improper
        import re
        json_match = re.search(r'\{.*\}', response.text, re.DOTALL)
        if json_match:
            try:
                result = json.loads(json_match.group(0))
            except Exception:
                raise e
        else:
            raise e

    # ── Non-bill photo gate ──────────────────────────────────────
    if not result.get("is_invoice", True):
        raise NotAnInvoiceError("The image does not appear to be a GST invoice.")

    # ── Low confidence or unsupported format gate ────────────────
    if result.get("low_confidence", False):
        raise LowConfidenceError("Unable to process this bill completely. Please upload a clearer image or enter it manually.")

    # Also catch cases where Gemini says is_invoice=true but every field is empty
    has_vendor = bool(result.get("vendor_name"))
    has_total  = bool(result.get("total_amount"))
    has_gstin  = bool(result.get("vendor_gstin"))
    if not has_vendor and not has_total and not has_gstin:
        raise NotAnInvoiceError("No invoice fields could be identified in the image.")

    # ── HSN Cache enrichment ─────────────────────────────────────────────────
    # For every line item, check the HSN cache (and call OpenRouter on miss).
    # We only overwrite hsn_sac / gst_rate when the AI extraction left them blank,
    # so real values on the invoice always take priority.
    line_items = result.get("line_items") or []
    for item in line_items:
        description = (item.get("description") or "").strip()
        if not description:
            continue
        hsn_info = lookup_hsn_for_item(description)
        # Backfill only missing fields — don't overwrite values already found
        if not item.get("hsn_sac") and hsn_info.get("hsn_code"):
            item["hsn_sac"] = hsn_info["hsn_code"]
        if item.get("gst_rate") is None and hsn_info.get("gst_rate") is not None:
            item["gst_rate"] = hsn_info["gst_rate"]
        if not item.get("unit") and hsn_info.get("unit"):
            item["unit"] = hsn_info["unit"]
    result["line_items"] = line_items

    return result

if __name__ == "__main__":
    import sys
    from ocr import detect_text
    
    # Configure stdout to use UTF-8 if supported (useful on Windows consoles)
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
            
    # Check if a file path is provided via command line arguments
    if len(sys.argv) > 1:
        image_path = sys.argv[1]
    else:
        # Default fallback test image
        image_path = "bill_test.jpg"
        if not os.path.exists(image_path):
            image_path = os.path.join(os.path.dirname(__file__), "tests", "test_image.png")
            
    print(f"Running end-to-end OCR and extraction for: {image_path}...")
    if not os.path.exists(image_path):
        print(f"Error: File {image_path} not found.")
        sys.exit(1)
        
    with open(image_path, "rb") as f:
        bill_image_bytes = f.read()
        
    try:
        # 1. OCR Step
        ocr_text = detect_text(bill_image_bytes)
        print("OCR Step completed successfully.")
        
        # 2. Extraction Step
        print("Extracting structured details via Gemini AI...")
        details = extract_invoice_details(ocr_text)
        
        print("\n--- Structured JSON Output ---")
        print(json.dumps(details, indent=2))
        print("------------------------------")
        
        # Print summary formatted as requested
        vendor = details.get("vendor_name", "N/A")
        items_count = len(details.get("line_items", []))
        
        def format_indian_currency(val):
            if val is None:
                return "N/A"
            try:
                # Convert to integer and round
                val_int = int(round(float(val)))
                s = str(val_int)
                if len(s) <= 3:
                    return s
                last_three = s[-3:]
                remaining = s[:-3]
                out = []
                while len(remaining) > 0:
                    if len(remaining) >= 2:
                        out.insert(0, remaining[-2:])
                        remaining = remaining[:-2]
                    else:
                        out.insert(0, remaining)
                        remaining = ""
                return ",".join(out) + "," + last_three
            except Exception:
                return str(val)
                
        print(f"Vendor: {vendor}")
        print(f"Items: {items_count}")
        print(f"Total: Rs.{format_indian_currency(details.get('total_amount'))}")
        print(f"IGST: Rs.{format_indian_currency(details.get('igst'))}")
    except Exception as e:
        print(f"Error: {e}")



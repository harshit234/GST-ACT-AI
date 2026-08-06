import os, json, sys
from dotenv import load_dotenv
load_dotenv()
import google.generativeai as genai
from ocr import detect_text

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

api_key = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=api_key)

# Use the test bill image
image_path = sys.argv[1] if len(sys.argv) > 1 else "bill_test.jpg"
print(f"Testing image: {image_path}")

with open(image_path, "rb") as f:
    image_bytes = f.read()

# Step 1: OCR
print("\n--- OCR OUTPUT ---")
ocr_text = detect_text(image_bytes)
print(ocr_text[:2000])  # Print first 2000 chars

# Step 2: Ask Gemini what it thinks
model = genai.GenerativeModel('gemini-2.5-flash')
prompt = f"""
Analyze the following raw OCR text from a bill/invoice and extract key structured fields.
Return a clean JSON object containing the following keys:
- is_invoice: true if this text is from a bill/invoice/receipt
- low_confidence: true if you detect that:
  a) The bill is handwritten (not printed) or a carbon copy.
  b) The bill represents a credit/debit note.
  c) The bill contains multiple different GST rates applied to items, freight/packing adjustments, or cess items.
  d) The OCR text is cut off, blurry, garbled, or critical fields are missing/ambiguous.
  Otherwise, set this to false.
- low_confidence_reason: explain WHY you set low_confidence (even if false, say "none")
- vendor_name: vendor name
- total_amount: total bill amount

Raw OCR Text:
{ocr_text}
"""

response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
result = json.loads(response.text.strip())

print("\n--- GEMINI DIAGNOSIS ---")
print(f"is_invoice:          {result.get('is_invoice')}")
print(f"low_confidence:      {result.get('low_confidence')}")
print(f"low_confidence_reason: {result.get('low_confidence_reason')}")
print(f"vendor_name:         {result.get('vendor_name')}")
print(f"total_amount:        {result.get('total_amount')}")

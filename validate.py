def validate_gst_invoice(invoice_data: dict) -> tuple:
    """
    Checks if extracted numbers are mathematically correct.
    
    Input: JSON from File 2 (dict)
    Process: Adds up all line item amounts, adds GST on top (CGST, SGST, IGST),
             checks if total matches bill total (tolerance of Rs.10).
    Output: (True/False, message explaining result)
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
        
        # Expected Total
        actual_total = float(invoice_data.get("total_amount") or 0.0)
        
        # Calculated Total
        calculated_total = calculated_subtotal + tax_total
        
        # Check difference with tolerance of Rs.10
        diff = abs(calculated_total - actual_total)
        
        message = (
            f"Calculated Subtotal: Rs.{calculated_subtotal:,.2f}, "
            f"Taxes (CGST={cgst:,.2f}, SGST={sgst:,.2f}, IGST={igst:,.2f}): Rs.{tax_total:,.2f}, "
            f"Calculated Total: Rs.{calculated_total:,.2f}, "
            f"Actual Bill Total: Rs.{actual_total:,.2f}."
        )
        
        if diff <= 10.0:
            return True, f"Validation Successful: Totals match within tolerance. {message}"
        else:
            return False, f"Validation Failed: Calculated total differs by Rs.{diff:.2f}. {message}"
            
    except Exception as e:
        return False, f"Validation Error: Failed to compute values. Details: {e}"

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


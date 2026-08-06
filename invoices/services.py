"""
services.py — Business Logic for Customer Invoice Generator
============================================================
Handles:
  - AI-powered HSN code lookup via Gemini 2.5 Flash (with local caching)
  - Professional PDF invoice generation via ReportLab
  - PDF upload to Supabase Storage
  - GSTIN validation and amount-to-words conversion
"""

import io
import os
import re
import sys
import json
from datetime import datetime

import google.generativeai as genai
from dotenv import load_dotenv

from invoices.db_invoices import (
    hsn_cache_lookup, hsn_cache_save, normalize_item_name, get_supabase_client
)

load_dotenv()


# ════════════════════════════════════════════════════════════════
# GSTIN VALIDATION
# ════════════════════════════════════════════════════════════════

# Indian GST state codes mapping
STATE_CODES = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana",
    "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh",
    "13": "Nagaland", "14": "Manipur", "15": "Mizoram",
    "16": "Tripura", "17": "Meghalaya", "18": "Assam",
    "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "25": "Daman & Diu", "26": "Dadra & Nagar Haveli & Daman & Diu",
    "27": "Maharashtra", "29": "Karnataka", "30": "Goa",
    "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman & Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh", "97": "Other Territory",
}

VALID_GST_RATES = [0, 5, 12, 18, 28]


def validate_gstin(gstin: str) -> tuple:
    """
    Validate Indian GSTIN format.
    Returns (is_valid: bool, message: str).
    """
    if not gstin:
        return True, "Empty GSTIN (B2C invoice)"
    gstin = gstin.strip().upper()
    if len(gstin) != 15:
        return False, "GSTIN must be exactly 15 characters"
    pattern = r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'
    if not re.match(pattern, gstin):
        return False, "Invalid GSTIN format"
    state_code = gstin[:2]
    if state_code not in STATE_CODES:
        return False, f"Invalid state code '{state_code}' in GSTIN"
    return True, "Valid GSTIN"


def get_state_code_from_gstin(gstin: str) -> str:
    """Extract 2-digit state code from GSTIN."""
    if gstin and len(gstin) >= 2:
        return gstin[:2].strip()
    return ""


# ════════════════════════════════════════════════════════════════
# AMOUNT TO WORDS (Indian English)
# ════════════════════════════════════════════════════════════════

def amount_to_words(amount) -> str:
    """
    Convert numeric amount to Indian English words for GST invoices.
    e.g., 12345.50 → 'Rupees Twelve Thousand Three Hundred Forty Five and Fifty Paise Only'
    Uses Indian numbering: Thousand, Lakh, Crore.
    """
    if amount is None or float(amount) == 0:
        return "Zero Rupees Only"

    amount = float(amount)
    ones = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven',
            'Eight', 'Nine', 'Ten', 'Eleven', 'Twelve', 'Thirteen',
            'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen']
    tens_words = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty',
                  'Sixty', 'Seventy', 'Eighty', 'Ninety']

    def _words(n):
        if n == 0:
            return ''
        elif n < 20:
            return ones[n]
        elif n < 100:
            return tens_words[n // 10] + (' ' + ones[n % 10] if n % 10 else '')
        elif n < 1000:
            return ones[n // 100] + ' Hundred' + (' ' + _words(n % 100) if n % 100 else '')
        elif n < 100000:
            return _words(n // 1000) + ' Thousand' + (' ' + _words(n % 1000) if n % 1000 else '')
        elif n < 10000000:
            return _words(n // 100000) + ' Lakh' + (' ' + _words(n % 100000) if n % 100000 else '')
        else:
            return _words(n // 10000000) + ' Crore' + (' ' + _words(n % 10000000) if n % 10000000 else '')

    rupees = int(amount)
    paise = round((amount - rupees) * 100)

    result = 'Rupees ' + _words(rupees)
    if paise > 0:
        result += ' and ' + _words(paise) + ' Paise'
    result += ' Only'
    return result


# ════════════════════════════════════════════════════════════════
# AI HSN CODE LOOKUP (Gemini 2.5 Flash + Local Cache)
# ════════════════════════════════════════════════════════════════

def lookup_hsn(item_name: str) -> dict:
    """
    Look up HSN code for an item.
    1. First check local hsn_cache table
    2. If not cached, call Gemini 2.5 Flash
    3. Cache the result for future lookups
    Never calls Gemini twice for the same normalized item name.
    """
    if not item_name or len(item_name.strip()) < 2:
        return {"success": False, "error": "Item name too short"}

    # Step 1: Check local cache
    cached = hsn_cache_lookup(item_name)
    if cached:
        return {
            "success": True,
            "hsn_code": cached.get("hsn_code", ""),
            "gst_rate": float(cached.get("gst_rate", 18)),
            "unit": cached.get("unit", "NOS"),
            "source": "cache"
        }

    # Step 2: Call Gemini 2.5 Flash
    try:
        load_dotenv(override=True)
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return {"success": False, "error": "GEMINI_API_KEY not configured"}

        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-2.5-flash')

        prompt = f"""You are a GST (Goods and Services Tax) expert for India.
Given the following item/product name, return the correct HSN code, applicable GST rate, and standard unit of measurement.

Item: {item_name}

Respond with ONLY a valid JSON object, no markdown, no explanation:
{{
    "hsn_code": "4412",
    "gst_rate": 18,
    "unit": "SQM"
}}

Rules:
- hsn_code: 4 or 8 digit HSN/SAC code as a string
- gst_rate: Must be one of 0, 5, 12, 18, or 28 (integer)
- unit: Standard unit abbreviation (NOS, KG, SQM, MTR, LTR, SET, BOX, PCS, BAG, PKT, TON, QTL, DOZ, PAR, BDL, ROL, SHT)
- Use current Indian GST rates as of 2026
- If unsure, use the most common classification"""

        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )

        result = json.loads(response.text.strip())
        hsn_code = str(result.get("hsn_code", ""))
        gst_rate = float(result.get("gst_rate", 18))
        unit = str(result.get("unit", "NOS"))

        # Validate gst_rate is in allowed set
        if int(gst_rate) not in VALID_GST_RATES:
            gst_rate = 18.0

        # Step 3: Cache the result
        hsn_cache_save(item_name, hsn_code, gst_rate, unit)

        return {
            "success": True,
            "hsn_code": hsn_code,
            "gst_rate": gst_rate,
            "unit": unit,
            "source": "gemini"
        }

    except Exception as e:
        print(f"[HSN Lookup] Gemini error: {e}", file=sys.stderr)
        return {"success": False, "error": f"AI lookup failed: {str(e)[:200]}"}


# ════════════════════════════════════════════════════════════════
# PDF INVOICE GENERATION (ReportLab)
# ════════════════════════════════════════════════════════════════

def generate_invoice_pdf(invoice_data: dict) -> bytes:
    """
    Generate a professional GST-compliant invoice PDF using ReportLab.

    Input:  Invoice data dict with merchant, customer, line items, taxes.
    Output: PDF file as bytes.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph,
        Spacer, HRFlowable
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm
    )

    styles = getSampleStyleSheet()
    elements = []

    # ── Custom styles ──
    style_title = ParagraphStyle(
        'InvoiceTitle', parent=styles['Heading1'],
        fontSize=20, textColor=colors.HexColor("#1a56db"),
        spaceAfter=2 * mm, alignment=TA_LEFT, fontName='Helvetica-Bold'
    )
    style_subtitle = ParagraphStyle(
        'InvoiceSubtitle', parent=styles['Normal'],
        fontSize=9, textColor=colors.HexColor("#6b7280"),
        spaceAfter=1 * mm, fontName='Helvetica'
    )
    style_header_label = ParagraphStyle(
        'HeaderLabel', parent=styles['Normal'],
        fontSize=8, textColor=colors.HexColor("#6b7280"),
        fontName='Helvetica'
    )
    style_header_value = ParagraphStyle(
        'HeaderValue', parent=styles['Normal'],
        fontSize=9, textColor=colors.HexColor("#111827"),
        fontName='Helvetica-Bold'
    )
    style_section_title = ParagraphStyle(
        'SectionTitle', parent=styles['Heading3'],
        fontSize=10, textColor=colors.HexColor("#1a56db"),
        spaceBefore=4 * mm, spaceAfter=2 * mm,
        fontName='Helvetica-Bold'
    )
    style_body = ParagraphStyle(
        'BodyText2', parent=styles['Normal'],
        fontSize=9, textColor=colors.HexColor("#374151"),
        fontName='Helvetica'
    )
    style_footer = ParagraphStyle(
        'FooterText', parent=styles['Normal'],
        fontSize=7, textColor=colors.HexColor("#9ca3af"),
        alignment=TA_CENTER, fontName='Helvetica'
    )
    style_amount_words = ParagraphStyle(
        'AmountWords', parent=styles['Normal'],
        fontSize=9, textColor=colors.HexColor("#111827"),
        fontName='Helvetica-BoldOblique', spaceBefore=2 * mm
    )

    # ── Extract data ──
    merchant_name = invoice_data.get("merchant_name", "")
    merchant_gstin = invoice_data.get("merchant_gstin", "")
    merchant_address = invoice_data.get("merchant_address", "")
    merchant_state = invoice_data.get("merchant_state", "")
    merchant_phone = invoice_data.get("merchant_phone", "")
    merchant_email = invoice_data.get("merchant_email", "")

    customer_name = invoice_data.get("customer_name", "")
    customer_gstin = invoice_data.get("customer_gstin", "")
    customer_address = invoice_data.get("customer_address", "")
    customer_state = invoice_data.get("customer_state", "")
    customer_phone = invoice_data.get("customer_phone", "")

    invoice_number = invoice_data.get("invoice_number", "")
    invoice_date = invoice_data.get("invoice_date", "")
    line_items = invoice_data.get("line_items", [])
    subtotal = float(invoice_data.get("subtotal", 0))
    cgst_total = float(invoice_data.get("cgst", 0))
    sgst_total = float(invoice_data.get("sgst", 0))
    igst_total = float(invoice_data.get("igst", 0))
    grand_total = float(invoice_data.get("total_amount", 0))
    words = invoice_data.get("amount_in_words", amount_to_words(grand_total))

    # Format date for display
    try:
        dt = datetime.strptime(invoice_date, "%Y-%m-%d")
        display_date = dt.strftime("%d-%m-%Y")
    except Exception:
        display_date = invoice_date

    # Determine place of supply
    merchant_state_name = STATE_CODES.get(get_state_code_from_gstin(merchant_gstin), merchant_state)
    customer_state_name = STATE_CODES.get(get_state_code_from_gstin(customer_gstin), customer_state)
    is_igst = igst_total > 0

    # ════════════════════════════════════════════════
    # HEADER: Company Details + Tax Invoice Badge
    # ════════════════════════════════════════════════

    # Tax Invoice header bar
    header_data = [[
        Paragraph(f"<b>{merchant_name or 'Your Business Name'}</b>", style_title),
        Paragraph("<b>TAX INVOICE</b>",
                  ParagraphStyle('TaxBadge', parent=styles['Normal'],
                                 fontSize=14, textColor=colors.white,
                                 backColor=colors.HexColor("#1a56db"),
                                 fontName='Helvetica-Bold',
                                 alignment=TA_CENTER,
                                 borderPadding=(4, 8, 4, 8)))
    ]]
    header_table = Table(header_data, colWidths=[doc.width * 0.65, doc.width * 0.35])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
    ]))
    elements.append(header_table)

    # Merchant info line
    merchant_info_parts = []
    if merchant_address:
        merchant_info_parts.append(merchant_address)
    if merchant_state:
        merchant_info_parts.append(f"State: {merchant_state_name or merchant_state}")
    if merchant_phone:
        merchant_info_parts.append(f"Ph: {merchant_phone}")
    if merchant_email:
        merchant_info_parts.append(f"Email: {merchant_email}")
    if merchant_gstin:
        merchant_info_parts.append(f"<b>GSTIN: {merchant_gstin}</b>")

    if merchant_info_parts:
        elements.append(Paragraph(" | ".join(merchant_info_parts), style_subtitle))

    elements.append(Spacer(1, 3 * mm))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e5e7eb")))
    elements.append(Spacer(1, 3 * mm))

    # ════════════════════════════════════════════════
    # INVOICE DETAILS + CUSTOMER DETAILS (side by side)
    # ════════════════════════════════════════════════

    # Left: Bill To
    bill_to_lines = [Paragraph("<b>Bill To:</b>", style_header_label)]
    bill_to_lines.append(Paragraph(f"<b>{customer_name}</b>", style_header_value))
    if customer_address:
        bill_to_lines.append(Paragraph(customer_address, style_body))
    if customer_state_name or customer_state:
        bill_to_lines.append(Paragraph(f"State: {customer_state_name or customer_state}", style_body))
    if customer_gstin:
        bill_to_lines.append(Paragraph(f"GSTIN: {customer_gstin}", style_body))
    if customer_phone:
        bill_to_lines.append(Paragraph(f"Phone: {customer_phone}", style_body))

    left_cell = bill_to_lines

    # Right: Invoice details
    right_lines = []
    right_lines.append(Paragraph(f"<b>Invoice No:</b> {invoice_number}", style_body))
    right_lines.append(Paragraph(f"<b>Date:</b> {display_date}", style_body))
    if customer_state_name or customer_state:
        right_lines.append(Paragraph(
            f"<b>Place of Supply:</b> {customer_state_name or customer_state}", style_body))

    right_cell = right_lines

    info_data = [[left_cell, right_cell]]
    info_table = Table(info_data, colWidths=[doc.width * 0.55, doc.width * 0.45])
    info_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 4 * mm))

    # ════════════════════════════════════════════════
    # LINE ITEMS TABLE
    # ════════════════════════════════════════════════

    # Build table header based on GST type
    if is_igst:
        table_headers = ['#', 'Description', 'HSN', 'Qty', 'Unit', 'Rate (₹)',
                         'Taxable (₹)', 'IGST %', 'IGST (₹)', 'Total (₹)']
        col_widths = [
            doc.width * 0.04, doc.width * 0.20, doc.width * 0.08,
            doc.width * 0.06, doc.width * 0.06, doc.width * 0.10,
            doc.width * 0.12, doc.width * 0.08, doc.width * 0.12, doc.width * 0.14
        ]
    else:
        table_headers = ['#', 'Description', 'HSN', 'Qty', 'Unit', 'Rate (₹)',
                         'Taxable (₹)', 'CGST %', 'CGST (₹)', 'SGST %', 'SGST (₹)', 'Total (₹)']
        col_widths = [
            doc.width * 0.03, doc.width * 0.16, doc.width * 0.07,
            doc.width * 0.05, doc.width * 0.05, doc.width * 0.08,
            doc.width * 0.10, doc.width * 0.06, doc.width * 0.10,
            doc.width * 0.06, doc.width * 0.10, doc.width * 0.14
        ]

    header_paras = [
        Paragraph(f"<b>{h}</b>",
                  ParagraphStyle('TH', fontSize=7, textColor=colors.white,
                                 fontName='Helvetica-Bold', alignment=TA_CENTER))
        for h in table_headers
    ]
    table_data = [header_paras]

    # Build item rows
    for idx, item in enumerate(line_items, 1):
        gst_rate = float(item.get("gst_rate", 0))
        qty = float(item.get("quantity", 0))
        rate = float(item.get("rate", 0))
        taxable = float(item.get("taxable_amount", qty * rate))
        item_cgst = float(item.get("cgst", 0))
        item_sgst = float(item.get("sgst", 0))
        item_igst = float(item.get("igst", 0))
        total = float(item.get("total", taxable + item_cgst + item_sgst + item_igst))

        ps = ParagraphStyle('TD', fontSize=8, fontName='Helvetica', alignment=TA_CENTER)
        ps_left = ParagraphStyle('TD_L', fontSize=8, fontName='Helvetica', alignment=TA_LEFT)
        ps_right = ParagraphStyle('TD_R', fontSize=8, fontName='Helvetica', alignment=TA_RIGHT)

        if is_igst:
            row = [
                Paragraph(str(idx), ps),
                Paragraph(str(item.get("item_name", "")), ps_left),
                Paragraph(str(item.get("hsn_code", "")), ps),
                Paragraph(f"{qty:g}", ps),
                Paragraph(str(item.get("unit", "")), ps),
                Paragraph(f"{rate:,.2f}", ps_right),
                Paragraph(f"{taxable:,.2f}", ps_right),
                Paragraph(f"{gst_rate:g}%", ps),
                Paragraph(f"{item_igst:,.2f}", ps_right),
                Paragraph(f"{total:,.2f}", ps_right),
            ]
        else:
            half_rate = gst_rate / 2
            row = [
                Paragraph(str(idx), ps),
                Paragraph(str(item.get("item_name", "")), ps_left),
                Paragraph(str(item.get("hsn_code", "")), ps),
                Paragraph(f"{qty:g}", ps),
                Paragraph(str(item.get("unit", "")), ps),
                Paragraph(f"{rate:,.2f}", ps_right),
                Paragraph(f"{taxable:,.2f}", ps_right),
                Paragraph(f"{half_rate:g}%", ps),
                Paragraph(f"{item_cgst:,.2f}", ps_right),
                Paragraph(f"{half_rate:g}%", ps),
                Paragraph(f"{item_sgst:,.2f}", ps_right),
                Paragraph(f"{total:,.2f}", ps_right),
            ]

        table_data.append(row)

    items_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    items_table.setStyle(TableStyle([
        # Header style
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1a56db")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        # Alternating row colors
        *[('BACKGROUND', (0, i), (-1, i), colors.HexColor("#f0f4ff"))
          for i in range(2, len(table_data), 2)],
        # Grid
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
    ]))
    elements.append(items_table)
    elements.append(Spacer(1, 4 * mm))

    # ════════════════════════════════════════════════
    # TOTALS SECTION
    # ════════════════════════════════════════════════

    def fmt(val):
        return f"₹ {val:,.2f}"

    totals_data = [
        ["Subtotal (Taxable Value)", fmt(subtotal)],
    ]
    if is_igst:
        totals_data.append(["Total IGST", fmt(igst_total)])
    else:
        totals_data.append(["Total CGST", fmt(cgst_total)])
        totals_data.append(["Total SGST", fmt(sgst_total)])

    totals_data.append(["", ""])  # Separator row
    totals_data.append(["Grand Total", fmt(grand_total)])

    totals_paras = []
    for row in totals_data:
        is_grand = row[0] == "Grand Total"
        label_style = ParagraphStyle(
            'TotLabel', fontSize=9 if not is_grand else 11,
            fontName='Helvetica-Bold' if is_grand else 'Helvetica',
            textColor=colors.HexColor("#111827"), alignment=TA_RIGHT
        )
        val_style = ParagraphStyle(
            'TotVal', fontSize=9 if not is_grand else 12,
            fontName='Helvetica-Bold',
            textColor=colors.HexColor("#1a56db") if is_grand else colors.HexColor("#111827"),
            alignment=TA_RIGHT
        )
        totals_paras.append([
            Paragraph(row[0], label_style),
            Paragraph(row[1], val_style)
        ])

    totals_table = Table(
        totals_paras,
        colWidths=[doc.width * 0.70, doc.width * 0.30]
    )
    totals_table.setStyle(TableStyle([
        ('LINEABOVE', (0, -1), (-1, -1), 1.5, colors.HexColor("#1a56db")),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor("#eff6ff")),
    ]))
    elements.append(totals_table)

    # ════════════════════════════════════════════════
    # AMOUNT IN WORDS
    # ════════════════════════════════════════════════

    elements.append(Spacer(1, 3 * mm))
    elements.append(Paragraph(f"<b>Amount in Words:</b> {words}", style_amount_words))
    elements.append(Spacer(1, 3 * mm))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e5e7eb")))

    # ════════════════════════════════════════════════
    # DECLARATION & TERMS
    # ════════════════════════════════════════════════

    elements.append(Spacer(1, 3 * mm))
    style_small = ParagraphStyle(
        'SmallText', fontSize=7.5, textColor=colors.HexColor("#6b7280"),
        fontName='Helvetica', leading=10
    )
    elements.append(Paragraph(
        "<b>Declaration:</b> We declare that this invoice shows the actual price of the goods "
        "described and that all particulars are true and correct.",
        style_small
    ))
    elements.append(Spacer(1, 2 * mm))
    elements.append(Paragraph(
        "<b>Terms & Conditions:</b> Goods once sold will not be taken back or exchanged. "
        "Subject to local jurisdiction. E & O.E.",
        style_small
    ))

    # ════════════════════════════════════════════════
    # SIGNATURE SECTION
    # ════════════════════════════════════════════════

    elements.append(Spacer(1, 10 * mm))
    sig_data = [[
        "",
        Paragraph(
            f"<b>For {merchant_name or 'Authorized Signatory'}</b><br/><br/><br/>"
            f"Authorized Signatory",
            ParagraphStyle('Sig', fontSize=9, fontName='Helvetica', alignment=TA_RIGHT,
                          textColor=colors.HexColor("#374151"))
        )
    ]]
    sig_table = Table(sig_data, colWidths=[doc.width * 0.55, doc.width * 0.45])
    sig_table.setStyle(TableStyle([
        ('LINEABOVE', (1, 0), (1, 0), 0.5, colors.HexColor("#d1d5db")),
        ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
    ]))
    elements.append(sig_table)

    # ════════════════════════════════════════════════
    # FOOTER
    # ════════════════════════════════════════════════

    elements.append(Spacer(1, 5 * mm))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e5e7eb")))
    elements.append(Spacer(1, 2 * mm))
    elements.append(Paragraph(
        "This is a computer-generated invoice and does not require a physical signature.",
        style_footer
    ))
    elements.append(Paragraph(
        f"Generated by GST ACT AI on {datetime.now().strftime('%d-%m-%Y %H:%M')}",
        style_footer
    ))

    # ── Build PDF ──
    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


# ════════════════════════════════════════════════════════════════
# SUPABASE STORAGE UPLOAD
# ════════════════════════════════════════════════════════════════

def upload_pdf_to_storage(pdf_bytes: bytes, filename: str) -> str:
    """
    Upload invoice PDF to Supabase Storage bucket 'invoices'.

    Input:  PDF bytes and filename (e.g., 'INV_2026-27_001.pdf')
    Output: Public URL of the uploaded PDF
    """
    client = get_supabase_client()
    bucket_name = "invoices"

    # Sanitize filename
    safe_name = re.sub(r'[^\w\-.]', '_', filename)
    storage_path = f"pdfs/{safe_name}"

    try:
        # Upload to Supabase Storage
        client.storage.from_(bucket_name).upload(
            path=storage_path,
            file=pdf_bytes,
            file_options={"content-type": "application/pdf", "upsert": "true"}
        )

        # Get the public URL
        public_url = client.storage.from_(bucket_name).get_public_url(storage_path)
        print(f"[Storage] PDF uploaded: {public_url}")
        return public_url

    except Exception as e:
        print(f"[Storage] Upload error: {e}", file=sys.stderr)
        # If bucket doesn't exist, provide helpful error
        if "not found" in str(e).lower() or "bucket" in str(e).lower():
            raise RuntimeError(
                f"Supabase Storage bucket '{bucket_name}' not found. "
                f"Please create it in Supabase Dashboard → Storage. Error: {e}"
            )
        raise

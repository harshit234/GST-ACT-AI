"""
routes.py — Flask routes for Customer Invoice Generator
========================================================
Handles page rendering, API endpoints for HSN lookup,
invoice creation, history, detail view, and PDF download.
"""

import sys
from flask import (
    render_template, request, jsonify, redirect,
    url_for, send_file, Response
)
import io

from invoices import invoices_bp
from invoices.db_invoices import (
    get_next_invoice_number, save_sales_invoice, update_invoice_pdf_url,
    delete_invoice, soft_delete_invoice, get_sales_invoices,
    get_sales_invoice, get_merchant_profile, update_merchant_profile
)
from invoices.services import (
    lookup_hsn, generate_invoice_pdf, upload_pdf_to_storage,
    validate_gstin, amount_to_words, VALID_GST_RATES, STATE_CODES
)


# ════════════════════════════════════════════════════════════════
# PAGE ROUTES (Server-rendered Jinja2 templates)
# ════════════════════════════════════════════════════════════════

@invoices_bp.route("/new-invoice")
def new_invoice_page():
    """Render the invoice creation form page."""
    return render_template("invoices/new_invoice.html", state_codes=STATE_CODES)


@invoices_bp.route("/invoices")
def invoice_history_page():
    """Render the invoice history/listing page."""
    return render_template("invoices/history.html")


@invoices_bp.route("/invoice/<invoice_id>")
def invoice_detail_page(invoice_id):
    """Render the invoice detail/view page."""
    return render_template("invoices/detail.html", invoice_id=invoice_id)


# ════════════════════════════════════════════════════════════════
# API: HSN LOOKUP
# ════════════════════════════════════════════════════════════════

@invoices_bp.route("/api/hsn-lookup", methods=["POST"])
def api_hsn_lookup():
    """
    POST /api/hsn-lookup
    Body: { "item_name": "Plywood Sheet" }
    Returns: { "success": true, "hsn_code": "4412", "gst_rate": 18, "unit": "SQM" }

    First checks local hsn_cache table.
    If not found, calls Gemini 2.5 Flash and caches the result.
    """
    data = request.get_json(silent=True) or {}
    item_name = str(data.get("item_name", "")).strip()

    if not item_name or len(item_name) < 2:
        return jsonify({"success": False, "error": "Item name is required (min 2 chars)"}), 400

    result = lookup_hsn(item_name)
    if result.get("success"):
        return jsonify(result)
    else:
        return jsonify(result), 500


# ════════════════════════════════════════════════════════════════
# API: CREATE INVOICE
# ════════════════════════════════════════════════════════════════

@invoices_bp.route("/api/invoice", methods=["POST"])
def api_create_invoice():
    """
    POST /api/invoice
    Creates a sales invoice, generates PDF, uploads to storage.

    Body: {
        "phone": "+91...",
        "merchant_name": "...", "merchant_gstin": "...",
        "merchant_address": "...", "merchant_state": "...",
        "merchant_phone": "...", "merchant_email": "...",
        "customer_name": "...", "customer_gstin": "...",
        "customer_phone": "...", "customer_email": "...",
        "customer_address": "...", "customer_state": "...",
        "invoice_number": "INV/2026-27/001" (optional, auto-generated if empty),
        "invoice_date": "2026-07-14",
        "line_items": [ { "item_name", "hsn_code", "unit", "quantity", "rate",
                          "gst_rate", "taxable_amount", "cgst", "sgst", "igst", "total" } ],
        "subtotal": 6500, "cgst": 585, "sgst": 585, "igst": 0,
        "total_amount": 7670, "amount_in_words": "..."
    }
    """
    data = request.get_json(silent=True) or {}
    phone = str(data.get("phone", "")).strip()

    # ── Validation ──
    errors = []

    if not phone:
        errors.append("Phone number is required")

    customer_name = str(data.get("customer_name", "")).strip()
    if not customer_name:
        errors.append("Customer name is required")

    customer_gstin = str(data.get("customer_gstin", "")).strip().upper()
    if customer_gstin:
        is_valid, msg = validate_gstin(customer_gstin)
        if not is_valid:
            errors.append(f"Customer GSTIN: {msg}")

    merchant_gstin = str(data.get("merchant_gstin", "")).strip().upper()
    if merchant_gstin:
        is_valid, msg = validate_gstin(merchant_gstin)
        if not is_valid:
            errors.append(f"Merchant GSTIN: {msg}")

    line_items = data.get("line_items", [])
    if not line_items or len(line_items) == 0:
        errors.append("At least one line item is required")

    for i, item in enumerate(line_items, 1):
        qty = float(item.get("quantity", 0))
        rate = float(item.get("rate", 0))
        gst_rate = int(float(item.get("gst_rate", 0)))

        if qty <= 0:
            errors.append(f"Item {i}: Quantity must be positive")
        if rate <= 0:
            errors.append(f"Item {i}: Rate must be positive")
        if gst_rate not in VALID_GST_RATES:
            errors.append(f"Item {i}: GST rate must be 0, 5, 12, 18, or 28")

    invoice_date = str(data.get("invoice_date", "")).strip()
    if not invoice_date:
        from datetime import datetime
        invoice_date = datetime.now().strftime("%Y-%m-%d")

    if errors:
        return jsonify({"success": False, "errors": errors}), 400

    # ── Save invoice to database ──
    bill_id = None
    try:
        invoice_data = {
            "merchant_name": data.get("merchant_name", ""),
            "merchant_gstin": merchant_gstin,
            "merchant_address": data.get("merchant_address", ""),
            "merchant_state": data.get("merchant_state", ""),
            "merchant_phone": data.get("merchant_phone", ""),
            "merchant_email": data.get("merchant_email", ""),
            "customer_name": customer_name,
            "customer_gstin": customer_gstin,
            "customer_phone": data.get("customer_phone", ""),
            "customer_email": data.get("customer_email", ""),
            "customer_address": data.get("customer_address", ""),
            "customer_state": data.get("customer_state", ""),
            "invoice_number": data.get("invoice_number", ""),
            "invoice_date": invoice_date,
            "line_items": line_items,
            "subtotal": float(data.get("subtotal", 0)),
            "cgst": float(data.get("cgst", 0)),
            "sgst": float(data.get("sgst", 0)),
            "igst": float(data.get("igst", 0)),
            "total_amount": float(data.get("total_amount", 0)),
            "amount_in_words": data.get("amount_in_words", ""),
        }

        # Compute amount_in_words if not provided
        if not invoice_data["amount_in_words"]:
            invoice_data["amount_in_words"] = amount_to_words(invoice_data["total_amount"])

        result = save_sales_invoice(invoice_data, phone)
        bill_id = result["bill_id"]
        invoice_number = result["invoice_number"]

        # Update invoice_data with generated number for PDF
        invoice_data["invoice_number"] = invoice_number

    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve)}), 400
    except Exception as e:
        print(f"[Invoice] DB save error: {e}", file=sys.stderr)
        return jsonify({"success": False, "error": f"Could not save invoice: {str(e)[:200]}"}), 500

    # ── Generate PDF ──
    pdf_url = ""
    try:
        pdf_bytes = generate_invoice_pdf(invoice_data)

        # Upload to Supabase Storage
        safe_inv = invoice_number.replace("/", "_").replace(" ", "_")
        filename = f"{safe_inv}.pdf"
        pdf_url = upload_pdf_to_storage(pdf_bytes, filename)

        # Update bill record with PDF URL
        update_invoice_pdf_url(bill_id, pdf_url)

    except Exception as e:
        # Rollback: delete the saved invoice if PDF generation/upload fails
        print(f"[Invoice] PDF/Upload error: {e}", file=sys.stderr)
        if bill_id:
            try:
                delete_invoice(bill_id)
            except Exception:
                pass
        return jsonify({
            "success": False,
            "error": f"Invoice saved but PDF generation failed: {str(e)[:200]}"
        }), 500

    # ── Also save merchant profile for future invoices ──
    try:
        update_merchant_profile(phone, {
            "business_name": data.get("merchant_name", ""),
            "business_gstin": merchant_gstin,
            "business_address": data.get("merchant_address", ""),
            "business_state": data.get("merchant_state", ""),
            "business_phone": data.get("merchant_phone", ""),
            "business_email": data.get("merchant_email", ""),
        })
    except Exception as e:
        # Non-critical — don't fail the invoice creation
        print(f"[Invoice] Profile save warning: {e}", file=sys.stderr)

    return jsonify({
        "success": True,
        "bill_id": bill_id,
        "invoice_number": invoice_number,
        "pdf_url": pdf_url,
        "message": f"Invoice {invoice_number} created successfully"
    })


# ════════════════════════════════════════════════════════════════
# API: LIST INVOICES
# ════════════════════════════════════════════════════════════════

@invoices_bp.route("/api/invoices", methods=["GET"])
def api_list_invoices():
    """
    GET /api/invoices?phone=+91...&search=...&status=...
    Returns list of sales invoices for a merchant.
    """
    phone = request.args.get("phone", "").strip()
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()

    if not phone:
        return jsonify({"success": False, "error": "phone parameter required"}), 400

    try:
        invoices = get_sales_invoices(phone, search=search, status=status)
        total_amount = sum(float(inv.get("total_amount", 0)) for inv in invoices)

        return jsonify({
            "success": True,
            "invoices": invoices,
            "summary": {
                "total_invoices": len(invoices),
                "total_amount": total_amount,
            }
        })
    except Exception as e:
        print(f"[API/invoices] Error: {e}", file=sys.stderr)
        return jsonify({"success": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════════
# API: GET SINGLE INVOICE
# ════════════════════════════════════════════════════════════════

@invoices_bp.route("/api/invoice/<invoice_id>", methods=["GET"])
def api_get_invoice(invoice_id):
    """
    GET /api/invoice/<id>?phone=+91...
    Returns full invoice detail including line_items and pdf_url.
    """
    phone = request.args.get("phone", "").strip()

    try:
        invoice = get_sales_invoice(invoice_id, phone)
        if not invoice:
            return jsonify({"success": False, "error": "Invoice not found"}), 404

        return jsonify({"success": True, "invoice": invoice})
    except Exception as e:
        print(f"[API/invoice/{invoice_id}] Error: {e}", file=sys.stderr)
        return jsonify({"success": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════════
# API: DELETE INVOICE
# ════════════════════════════════════════════════════════════════

@invoices_bp.route("/api/invoice/<invoice_id>", methods=["DELETE"])
def api_delete_invoice(invoice_id):
    """
    DELETE /api/invoice/<id>
    Body: { "phone": "+91..." }
    Soft-deletes an invoice (sets status to 'deleted').
    """
    data = request.get_json(silent=True) or {}
    phone = str(data.get("phone", "")).strip()

    if not phone:
        return jsonify({"success": False, "error": "phone is required"}), 400

    try:
        success = soft_delete_invoice(invoice_id, phone)
        if success:
            return jsonify({"success": True, "message": "Invoice deleted"})
        else:
            return jsonify({"success": False, "error": "Invoice not found or unauthorized"}), 404
    except Exception as e:
        print(f"[API/invoice/delete] Error: {e}", file=sys.stderr)
        return jsonify({"success": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════════
# API: GET NEXT INVOICE NUMBER
# ════════════════════════════════════════════════════════════════

@invoices_bp.route("/api/next-invoice-number", methods=["GET"])
def api_next_invoice_number():
    """
    GET /api/next-invoice-number?phone=+91...
    Returns the next auto-generated invoice number.
    """
    phone = request.args.get("phone", "").strip()
    if not phone:
        return jsonify({"success": False, "error": "phone parameter required"}), 400

    try:
        inv_number = get_next_invoice_number(phone)
        return jsonify({"success": True, "invoice_number": inv_number})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════════
# API: MERCHANT PROFILE
# ════════════════════════════════════════════════════════════════

@invoices_bp.route("/api/merchant-profile", methods=["GET"])
def api_get_merchant_profile():
    """GET /api/merchant-profile?phone=+91..."""
    phone = request.args.get("phone", "").strip()
    if not phone:
        return jsonify({"success": False, "error": "phone parameter required"}), 400

    try:
        profile = get_merchant_profile(phone)
        return jsonify({"success": True, "profile": profile})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@invoices_bp.route("/api/merchant-profile", methods=["POST"])
def api_update_merchant_profile():
    """POST /api/merchant-profile — Save/update merchant business profile."""
    data = request.get_json(silent=True) or {}
    phone = str(data.get("phone", "")).strip()

    if not phone:
        return jsonify({"success": False, "error": "phone is required"}), 400

    try:
        profile = update_merchant_profile(phone, data)
        return jsonify({"success": True, "profile": profile})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════════
# PDF DOWNLOAD / VIEW
# ════════════════════════════════════════════════════════════════

@invoices_bp.route("/invoice/pdf/<invoice_id>")
def invoice_pdf_view(invoice_id):
    """
    GET /invoice/pdf/<id>?phone=+91...
    Redirects to the PDF URL or regenerates if not available.
    """
    phone = request.args.get("phone", "").strip()

    try:
        invoice = get_sales_invoice(invoice_id, phone)
        if not invoice:
            return jsonify({"success": False, "error": "Invoice not found"}), 404

        pdf_url = invoice.get("pdf_url")
        if pdf_url:
            return redirect(pdf_url)

        # PDF URL not available — regenerate
        invoice_data = {
            "merchant_name": invoice.get("vendor_name", ""),
            "merchant_gstin": invoice.get("vendor_gstin", ""),
            "customer_name": invoice.get("customer_name", ""),
            "customer_gstin": invoice.get("customer_gstin", ""),
            "customer_phone": invoice.get("customer_phone", ""),
            "customer_address": invoice.get("customer_address", ""),
            "customer_state": invoice.get("customer_state", ""),
            "invoice_number": invoice.get("invoice_number", ""),
            "invoice_date": invoice.get("invoice_date", ""),
            "line_items": invoice.get("line_items", []),
            "subtotal": invoice.get("subtotal", 0),
            "cgst": invoice.get("cgst", 0),
            "sgst": invoice.get("sgst", 0),
            "igst": invoice.get("igst", 0),
            "total_amount": invoice.get("total_amount", 0),
            "amount_in_words": invoice.get("amount_in_words", ""),
        }

        pdf_bytes = generate_invoice_pdf(invoice_data)
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype='application/pdf',
            as_attachment=False,
            download_name=f"Invoice_{invoice.get('invoice_number', invoice_id)}.pdf"
        )

    except Exception as e:
        print(f"[PDF View] Error: {e}", file=sys.stderr)
        return jsonify({"success": False, "error": str(e)}), 500

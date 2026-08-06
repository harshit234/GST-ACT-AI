"""
app.py — WhatsApp Bot Webhook + Dashboard API
=============================================
Connects everything together.

WhatsApp Bot (POST /webhook):
  Photo  → OCR → Extract → Validate → Save in background, reply via Twilio outbound API
  SUMMARY → monthly GST total (fast, inline)
  Text   → Instructions

Dashboard API:
  POST /api/send-otp      → Send real 6-digit OTP via Twilio SMS
  POST /api/verify-otp    → Verify OTP, return session token
  GET  /api/bills         → All bills for merchant from Supabase
  GET  /api/bills/<id>    → Single bill with full line_items
  GET  /api/summary       → Monthly GST summary + 6-month trend
  GET  /api/whatsapp-info → Twilio WhatsApp number + wa.me link for QR
  GET  /dashboard/        → Serve dashboard HTML
"""

import os
import sys
import random
import threading
import time
import requests

from flask import Flask, request, Response, jsonify, send_from_directory, redirect
from dotenv import load_dotenv
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse

from ocr import detect_text
from extract import extract_invoice_details
from validate import validate_gst_invoice, validate_invoice_date
from db import save_invoice, get_monthly_summary, get_supabase_client, save_pending_bill, get_pending_bill, delete_pending_bill
from exceptions import BlurryImageError, NotAnInvoiceError, DuplicateInvoiceError, LowConfidenceError, SuspiciousDateError

load_dotenv()

# ─── Flask app — serve dashboard as static files ────────────────────────────
app = Flask(__name__, static_folder="dashboard", static_url_path="/dashboard")

from invoices import invoices_bp
app.register_blueprint(invoices_bp)

try:
    from flask_cors import CORS
    CORS(app, resources={r"/api/*": {"origins": "*"}})
except ImportError:
    pass  # flask-cors optional; install with: pip install flask-cors

# ─── Twilio credentials ──────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID   = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN    = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = (
    os.getenv("TWILIO_WHATSAPP_FROM")
    or os.getenv("TWILIO_WHATSAPP_NUMBER")
    or "whatsapp:+14155238886"
)
# A Twilio SMS-capable number — add TWILIO_SMS_NUMBER=+1xxx to .env if you have one
TWILIO_SMS_FROM = os.getenv("TWILIO_SMS_NUMBER") or os.getenv("TWILIO_PHONE_NUMBER") or ""

twilio_client = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# ─── In-memory OTP store  {phone: {otp, expires_at}} ───────────────────────
# Replace with Redis for production multi-process deployments
_otp_store: dict = {}
OTP_TTL = 600  # 10 minutes
# Dev mode: True when no TWILIO_SMS_NUMBER is set — OTP shown in response
DEV_MODE = not bool(os.getenv("TWILIO_SMS_NUMBER") or os.getenv("TWILIO_PHONE_NUMBER"))

# ─── Pending bills store  {phone: {invoice_data, whatsapp_number, wa_from, expires_at}} ─
# Holds invoices with suspicious dates awaiting merchant confirmation
_pending_bills: dict = {}
PENDING_TTL = 600  # 10 minutes


# ════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════

def format_db_error(e: Exception) -> str:
    """Returns a user-friendly error message for database connection issues."""
    msg = str(e)
    if "getaddrinfo failed" in msg or "ConnectError" in msg or "gaierror" in msg:
        return "Database connection failed: Unable to reach Supabase. Please check if your Supabase project is active/unpaused or update SUPABASE_URL in .env."
    return msg


def fmt_inr(val) -> str:
    """Format numeric value as Indian comma-separated string (e.g. 1243709 → '12,43,709')."""
    if val is None:
        return "N/A"
    try:
        val_int = int(round(float(val)))
        s = str(val_int)
        if len(s) <= 3:
            return s
        last_three = s[-3:]
        remaining  = s[:-3]
        parts = []
        while remaining:
            parts.insert(0, remaining[-2:])
            remaining = remaining[:-2]
        return ",".join(p for p in parts if p) + "," + last_three
    except Exception:
        return str(val)


def normalise_phone(raw: str) -> str:
    """Normalise any Indian phone input to E.164 (+91XXXXXXXXXX)."""
    p = raw.strip().replace(" ", "").replace("-", "").replace("+", "")
    if p.startswith("0"):
        p = p[1:]
    if len(p) == 10:
        return "+91" + p
    elif len(p) == 12 and p.startswith("91"):
        return "+" + p
    else:
        return "+" + p


def send_sms_otp(to: str, otp: str) -> tuple:
    """Send OTP via Twilio SMS. Returns (ok: bool, err: str)."""
    if not twilio_client or not TWILIO_SMS_FROM:
        # Dev mode — print and succeed silently (no SMS sent)
        print(f"[OTP-DEV] {to}: {otp}")
        return True, ""
    try:
        msg = twilio_client.messages.create(
            from_=TWILIO_SMS_FROM,
            to=to,
            body=(
                f"Your GST ACT AI OTP is: {otp}\n"
                f"Valid for 5 minutes. Do not share this code."
            )
        )
        print(f"[OTP] Sent to {to}, SID: {msg.sid}")
        return True, ""
    except Exception as e:
        print(f"[OTP] ERROR: {e}", file=sys.stderr)
        return False, str(e)


def send_whatsapp(to: str, body: str, from_number: str = None):
    """Send WhatsApp message via Twilio outbound API."""
    if not twilio_client:
        print(f"[WA] Twilio not configured. Would send to {to}:\n{body}")
        return
    try:
        sender = from_number or TWILIO_WHATSAPP_FROM
        msg = twilio_client.messages.create(
            from_=sender,
            to=f"whatsapp:{to}",
            body=body
        )
        print(f"[WA] Sent to {to}, SID: {msg.sid}")
    except Exception as e:
        print(f"[WA] ERROR to {to}: {e}", file=sys.stderr)


def twiml_ack(message: str = "") -> Response:
    """Return an immediate TwiML ACK so Twilio doesn't time out."""
    resp = MessagingResponse()
    if message:
        resp.message(message)
    return Response(
        str(resp).encode("utf-8"),
        mimetype="text/xml",
        headers={"Content-Type": "text/xml; charset=utf-8"}
    )


def download_media(media_url: str) -> bytes:
    """Download Twilio media with auth credentials."""
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
        r = requests.get(media_url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=30)
    else:
        r = requests.get(media_url, timeout=30)
    r.raise_for_status()
    return r.content


# ════════════════════════════════════════════════════════════════
# BACKGROUND BILL PROCESSOR
# ════════════════════════════════════════════════════════════════

def process_bill_in_background(whatsapp_number: str, media_url: str, wa_from: str):
    """Full OCR → Extract → Validate → Save pipeline. Runs in a background thread."""
    print(f"[bg] Processing bill for {whatsapp_number}")
    invoice_data = {}
    try:
        image_bytes   = download_media(media_url)
        ocr_text      = detect_text(image_bytes)
        invoice_data  = extract_invoice_details(ocr_text)
        _, calc_total, diff = validate_gst_invoice(invoice_data)
        validate_invoice_date(invoice_data)  # Raises SuspiciousDateError if date is off

        vendor = invoice_data.get("vendor_name",    "N/A")
        inv_no = invoice_data.get("invoice_number", "N/A")
        inv_dt = invoice_data.get("invoice_date",   "N/A")
        items  = invoice_data.get("line_items", [])
        total  = float(invoice_data.get("total_amount") or 0)

        # ── Math-validation gate ──────────────────────────────────────
        # If our calculated total differs from the bill total by more than
        # Rs.500, hold the bill and ask the merchant to confirm.
        # Rs.500 allows for freight, packing, rounding differences on
        # real-world invoices without triggering a false mismatch alert.
        VALIDATION_THRESHOLD = 500.0
        if diff > VALIDATION_THRESHOLD:
            save_pending_bill(
                whatsapp_number=whatsapp_number,
                invoice_data=invoice_data,
                wa_from=wa_from,
                bill_total=total,
                calculated_total=calc_total,
                difference=diff,
            )
            reply = (
                f"\u26a0\ufe0f Total Mismatch — Confirm Required\n\n"
                f"\U0001f3ea Vendor  : {vendor}\n"
                f"\U0001f4c4 Invoice : {inv_no}\n"
                f"\U0001f4c5 Date    : {inv_dt}\n\n"
                f"\U0001f4b0 Bill Total (from image) : Rs.{fmt_inr(total)}\n"
                f"\U0001f9ee Our Calculated Total   : Rs.{fmt_inr(calc_total)}\n"
                f"\U0001f4ca Difference             : Rs.{fmt_inr(diff)}\n\n"
                f"This may be due to freight, packing or rounding charges.\n\n"
                f"Reply:\n"
                f"  *1* — Confirm and save this bill\n"
                f"  *2* — Discard and send a clearer photo"
            )
            send_whatsapp(whatsapp_number, reply, wa_from)
            print(f"[bg] Validation mismatch Rs.{diff:.2f} — pending confirmation for {whatsapp_number}")
            return
        # ── End math-validation gate ──────────────────────────────────

        bill_id = save_invoice(invoice_data, whatsapp_number)

        cgst = invoice_data.get("cgst", 0)
        sgst = invoice_data.get("sgst", 0)
        igst = invoice_data.get("igst", 0)
        dashboard_url = os.getenv("DASHBOARD_URL", "")

        reply = (
            f"**Bill Processed \u2705**\n\n"
            f"\U0001f3ea Vendor     : {vendor}\n"
            f"\U0001f4c4 Invoice No : {inv_no}\n"
            f"\U0001f4c5 Date       : {inv_dt}\n"
            f"\U0001f4e6 Items      : {len(items)}\n\n"
            f"Invoice Total    : Rs.{fmt_inr(total)}\n"
            f"Calculated Total : Rs.{fmt_inr(calc_total)}\n"
            f"Difference       : Rs.{fmt_inr(diff)}\n\n"
            f"\U0001f194 Bill ID: {bill_id}"
            + (f"\n\n\U0001f4ca View dashboard: {dashboard_url}" if dashboard_url else "")
        )


        # Chunked item-wise GST details
        if items:
            LIMIT = 1500
            chunks, cur = [], "📋 Item-wise GST Details:\n"
            for idx, item in enumerate(items, 1):
                line = (
                    f"\n{idx}. {item.get('description', 'N/A')}\n"
                    f"   Amount: Rs.{fmt_inr(item.get('amount'))}\n"
                    f"   GST {item.get('gst_rate', 'N/A')}%"
                    f" | Rs.{fmt_inr(item.get('gst_amount'))}\n"
                )
                if len(cur) + len(line) > LIMIT:
                    chunks.append(cur)
                    cur = f"📋 Items (cont.):\n{line}"
                else:
                    cur += line
            chunks.append(cur)
            for c in chunks:
                send_whatsapp(whatsapp_number, c, wa_from)

    except BlurryImageError:
        reply = (
            "📷 Photo Too Blurry\n\n"
            "Please retry with:\n"
            "  • Better lighting\n  • Steady hands\n  • Full bill in frame"
        )
    except NotAnInvoiceError:
        reply = (
            "🚫 Not a GST Bill\n\n"
            "Please send a valid GST invoice with:\n"
            "  • GSTIN visible\n  • Invoice number & date\n  • Tax breakdown"
        )
    except LowConfidenceError as e:
        reply = f"⚠️ {str(e)}"
    except SuspiciousDateError as sde:
        # Hold the invoice in pending store and ask merchant to confirm
        _pending_bills[whatsapp_number] = {
            "invoice_data": invoice_data,
            "wa_from": wa_from,
            "expires_at": time.time() + PENDING_TTL,
        }
        vendor = invoice_data.get('vendor_name', 'N/A')
        inv_no = invoice_data.get('invoice_number', 'N/A')
        reply = (
            f"⚠️ Date Verification Required\n\n"
            f"🏪 Vendor: {vendor}\n"
            f"📄 Invoice: {inv_no}\n"
            f"📅 Detected Date: {sde.extracted_date}\n\n"
            f"{sde.message}\n\n"
            f"Please verify and reply:\n"
            f"• *CONFIRM DATE* — if the date is correct\n"
            f"• Send a new photo to re-process"
        )
    except DuplicateInvoiceError as dup:
        reply = (
            f"ℹ️ Duplicate Invoice\n\n"
            f"🏪 {invoice_data.get('vendor_name', 'N/A')}\n"
            f"📄 {invoice_data.get('invoice_number', 'N/A')}\n"
            f"📅 Originally saved on: {dup.existing_invoice_date}\n\n"
            f"This bill was already recorded.\n"
            f"🆔 Existing Bill ID: {dup.existing_bill_id}"
        )
    except Exception as e:
        print(f"[bg] ERROR: {e}", file=sys.stderr)
        reply = (
            f"❌ Processing Failed\n\n"
            f"Please ensure photo is clear and shows a valid GST bill.\n"
            f"Error: {str(e)[:200]}"
        )

    send_whatsapp(whatsapp_number, reply, wa_from)
    print(f"[bg] Done for {whatsapp_number}")


# ════════════════════════════════════════════════════════════════
# DASHBOARD API ROUTES
# ════════════════════════════════════════════════════════════════

@app.route("/dashboard")
def serve_dashboard_redirect():
    # Redirect to ensure relative script/style paths (js/data.js, etc.) resolve correctly
    return redirect("/dashboard/")

@app.route("/dashboard/")
def serve_dashboard():
    return send_from_directory("dashboard", "index.html")


# ── POST /api/send-otp ───────────────────────────────────────
@app.route("/api/send-otp", methods=["POST"])
def api_send_otp():
    """
    Body: { "phone": "9876543210" }
    Generates a 6-digit OTP, saves it in-memory, sends via Twilio SMS.
    """
    data  = request.get_json(silent=True) or {}
    phone = normalise_phone(str(data.get("phone", "")))

    if len(phone) < 12:
        return jsonify({"success": False, "error": "Invalid phone number"}), 400

    otp = str(random.randint(100000, 999999))
    _otp_store[phone] = {"otp": otp, "expires_at": time.time() + OTP_TTL}

    ok, err = send_sms_otp(phone, otp)
    if not ok:
        return jsonify({"success": False, "error": f"Could not send SMS: {err}"}), 500

    resp = {"success": True, "message": f"OTP sent to {phone}"}
    if DEV_MODE:
        # In dev mode (no TWILIO_SMS_NUMBER), return OTP in response so UI can autofill it
        resp["dev_otp"] = otp
        resp["dev_note"] = "Dev mode: SMS not sent. OTP shown here for testing."
    return jsonify(resp)


# ── POST /api/verify-otp ─────────────────────────────────────
@app.route("/api/verify-otp", methods=["POST"])
def api_verify_otp():
    """
    Body: { "phone": "9876543210", "otp": "123456" }
    Returns { "success": true, "token": "...", "whatsapp_number": "+91..." }
    """
    data      = request.get_json(silent=True) or {}
    phone     = normalise_phone(str(data.get("phone", "")))
    otp_input = str(data.get("otp", "")).strip()

    # Dev bypass: accept '000000' when no SMS number configured
    if DEV_MODE and otp_input == "000000":
        _otp_store.pop(phone, None)
        token = f"tok_{phone}_{int(time.time())}"
        return jsonify({"success": True, "token": token, "whatsapp_number": phone, "dev_bypass": True})

    record = _otp_store.get(phone)
    if not record:
        return jsonify({"success": False, "error": "No OTP found. Please request a new one."}), 400
    if time.time() > record["expires_at"]:
        _otp_store.pop(phone, None)
        return jsonify({"success": False, "error": "OTP expired. Please request a new one."}), 400
    if record["otp"] != otp_input:
        return jsonify({"success": False, "error": "Incorrect OTP. Please try again."}), 400

    _otp_store.pop(phone, None)
    token = f"tok_{phone}_{int(time.time())}"
    return jsonify({"success": True, "token": token, "whatsapp_number": phone})


# ── GET /api/bills ───────────────────────────────────────────
@app.route("/api/bills", methods=["GET"])
def api_get_bills():
    """
    Query params: phone=+91XXXXXXXXXX&month=2026-06 (optional)
    Returns all bills + summary totals.
    """
    phone = request.args.get("phone", "").strip()
    month = request.args.get("month", "")

    if not phone:
        return jsonify({"success": False, "error": "phone parameter required"}), 400

    try:
        from datetime import datetime
        client = get_supabase_client()
        query = (
            client.table("bills")
            .select("id, vendor_name, vendor_gstin, invoice_number, invoice_date, "
                    "cgst, sgst, igst, total_amount, line_items, created_at")
            .eq("whatsapp_number", phone)
            .eq("bill_type", "purchase")
            .order("created_at", desc=True)
        )
        if month:
            try:
                yr, mo = month.split("-")
                start  = datetime(int(yr), int(mo), 1).isoformat() + "Z"
                end_mo = 1 if int(mo) == 12 else int(mo) + 1
                end_yr = int(yr) + 1 if int(mo) == 12 else int(yr)
                end    = datetime(end_yr, end_mo, 1).isoformat() + "Z"
                query  = query.gte("created_at", start).lt("created_at", end)
            except Exception:
                pass

        result = query.execute()
        bills  = result.data or []

        for b in bills:
            # derive a status field — bills table may not have it yet
            b.setdefault("status", "processed")

        total_purchases = sum(float(b.get("total_amount") or 0) for b in bills)
        total_igst      = sum(float(b.get("igst") or 0) for b in bills)

        return jsonify({
            "success": True,
            "bills": bills,
            "summary": {
                "total_bills": len(bills),
                "total_purchases": total_purchases,
                "total_igst": total_igst,
            }
        })

    except Exception as e:
        print(f"[api/bills] ERROR: {e}", file=sys.stderr)
        return jsonify({"success": False, "error": str(e)}), 500


# ── GET /api/bills/<id> ──────────────────────────────────────
@app.route("/api/bills/<bill_id>", methods=["GET"])
def api_get_bill(bill_id):
    """
    Query params: phone=+91XXXXXXXXXX
    Returns full bill record including line_items JSON.
    """
    phone = request.args.get("phone", "").strip()

    try:
        client = get_supabase_client()
        result = (
            client.table("bills")
            .select("*")
            .eq("id", bill_id)
            .eq("bill_type", "purchase")
            .execute()
        )
        bills = result.data or []
        if not bills:
            return jsonify({"success": False, "error": "Bill not found"}), 404

        bill = bills[0]
        if phone and bill.get("whatsapp_number") != phone:
            return jsonify({"success": False, "error": "Unauthorized"}), 403

        bill.setdefault("status", "processed")
        return jsonify({"success": True, "bill": bill})

    except Exception as e:
        print(f"[api/bills/{bill_id}] ERROR: {e}", file=sys.stderr)
        return jsonify({"success": False, "error": str(e)}), 500


# ── GET /api/summary ─────────────────────────────────────────
@app.route("/api/summary", methods=["GET"])
def api_get_summary():
    """
    Query params: phone=+91XXXXXXXXXX
    Returns current month summary + 6-month trend for charts.
    Optimized to fetch all data in a single Supabase query to improve latency.
    """
    phone = request.args.get("phone", "").strip()
    if not phone:
        return jsonify({"success": False, "error": "phone parameter required"}), 400

    try:
        from datetime import datetime
        client  = get_supabase_client()
        now     = datetime.utcnow()

        # Calculate range start: start of the month 5 months ago
        raw_mo_start = now.month - 5
        yr_start = now.year + (raw_mo_start - 1) // 12
        mo_start = (raw_mo_start - 1) % 12 + 1
        range_start = datetime(yr_start, mo_start, 1).isoformat() + "Z"

        # Fetch all bills from the last 6 months in a single query
        res = (
            client.table("bills")
            .select("cgst, sgst, igst, total_amount, created_at")
            .eq("whatsapp_number", phone)
            .eq("bill_type", "purchase")
            .gte("created_at", range_start)
            .execute()
        )
        all_rows = res.data or []

        # Process monthly trend in-memory
        labels, purchases, igst_trend = [], [], []
        for i in range(5, -1, -1):
            raw_mo = now.month - i
            yr     = now.year + (raw_mo - 1) // 12
            mo     = (raw_mo - 1) % 12 + 1
            
            labels.append(datetime(yr, mo, 1).strftime("%b"))
            
            target_prefix = f"{yr:04d}-{mo:02d}"
            month_rows = [r for r in all_rows if r.get("created_at", "").startswith(target_prefix)]
            
            purchases.append(sum(float(r.get("total_amount") or 0) for r in month_rows))
            igst_trend.append(sum(float(r.get("igst") or 0) for r in month_rows))

        # Calculate current month summary from our cached rows
        current_month_prefix = f"{now.year:04d}-{now.month:02d}"
        current_month_rows = [r for r in all_rows if r.get("created_at", "").startswith(current_month_prefix)]
        
        curr_total_amount = sum(float(r.get("total_amount") or 0) for r in current_month_rows)
        curr_cgst = sum(float(r.get("cgst") or 0) for r in current_month_rows)
        curr_sgst = sum(float(r.get("sgst") or 0) for r in current_month_rows)
        curr_igst = sum(float(r.get("igst") or 0) for r in current_month_rows)
        curr_grand_tax = curr_cgst + curr_sgst + curr_igst

        summary = {
            "month":           now.strftime("%B %Y"),
            "bill_count":      len(current_month_rows),
            "total_amount":    curr_total_amount,
            "cgst":            curr_cgst,
            "sgst":            curr_sgst,
            "igst":            curr_igst,
            "grand_tax_total": curr_grand_tax,
            "gstr4":           curr_igst * 3
        }

        return jsonify({
            "success": True,
            "summary": summary,
            "trend": {"labels": labels, "purchases": purchases, "igst": igst_trend}
        })

    except Exception as e:
        print(f"[api/summary] ERROR: {e}", file=sys.stderr)
        return jsonify({"success": False, "error": str(e)}), 500


# ── GET /api/whatsapp-info ───────────────────────────────────
@app.route("/api/whatsapp-info", methods=["GET"])
def api_whatsapp_info():
    """Returns Twilio WhatsApp number and wa.me link for QR code display."""
    raw     = TWILIO_WHATSAPP_FROM.replace("whatsapp:", "").strip()
    wa_link = f"https://wa.me/{raw.lstrip('+')}"
    return jsonify({
        "success":        True,
        "whatsapp_number": raw,
        "wa_link":        wa_link,
        "display":        raw,
        "instructions":   [
            "Save this number in your contacts as 'GST ACT AI'",
            "Send any message to say hello",
            "Send a clear photo of your GST bill",
            "Type SUMMARY any time to see your monthly totals"
        ]
    })


# ════════════════════════════════════════════════════════════════
# WHATSAPP WEBHOOK
# ════════════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "active",
        "message": "GST ACT AI Bot is running.",
        "dashboard": "/dashboard"
    })


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    POST /webhook — Twilio WhatsApp webhook.
    Must respond within 15 s — ACK immediately, heavy work in background thread.
    """
    from_raw       = request.form.get("From", "")
    body_text      = request.form.get("Body", "").strip().upper()
    num_media      = int(request.form.get("NumMedia", 0))
    media_url      = request.form.get("MediaUrl0", "")
    media_type     = request.form.get("MediaContentType0", "")
    media_filename = request.form.get("MediaFilename0", "").lower()

    whatsapp_number = from_raw.replace("whatsapp:", "").strip()
    print(f"[webhook] From={whatsapp_number}, NumMedia={num_media}, Body='{request.form.get('Body', '')}'")

    # ── Reply 1: Confirm pending bill (math-validation) ───────────
    if body_text == "1":
        pending = get_pending_bill(whatsapp_number)
        if not pending:
            # No pending bill — ignore silently (could be a reply to something else)
            return twiml_ack("")

        wa_from      = pending.get("wa_from", TWILIO_WHATSAPP_FROM)
        invoice_data = pending["invoice_data"]

        def confirm_pending_bill():
            try:
                delete_pending_bill(whatsapp_number)
                bill_id = save_invoice(invoice_data, whatsapp_number)
                vendor  = invoice_data.get("vendor_name", "N/A")
                inv_no  = invoice_data.get("invoice_number", "N/A")
                inv_dt  = invoice_data.get("invoice_date", "N/A")
                total   = invoice_data.get("total_amount", 0)
                reply = (
                    f"\u2705 Bill Confirmed and Saved\n\n"
                    f"\U0001f3ea Vendor  : {vendor}\n"
                    f"\U0001f4c4 Invoice : {inv_no}\n"
                    f"\U0001f4c5 Date    : {inv_dt}\n"
                    f"\U0001f4b0 Total   : Rs.{fmt_inr(total)}\n\n"
                    f"\U0001f194 Bill ID: {bill_id}"
                )
            except DuplicateInvoiceError as dup:
                reply = (
                    f"\u2139\ufe0f Duplicate Invoice\n\n"
                    f"\U0001f4c5 Originally saved on: {dup.existing_invoice_date}\n"
                    f"\U0001f194 Existing Bill ID: {dup.existing_bill_id}"
                )
            except Exception as e:
                reply = f"\u274c Could not save bill.\nError: {str(e)[:200]}"
            send_whatsapp(whatsapp_number, reply, wa_from)

        threading.Thread(target=confirm_pending_bill, daemon=True).start()
        return twiml_ack("\u23f3 Saving your confirmed bill...")

    # ── Reply 2: Discard pending bill (math-validation) ───────────
    if body_text == "2":
        pending = get_pending_bill(whatsapp_number)
        if not pending:
            return twiml_ack("")

        wa_from = pending.get("wa_from", TWILIO_WHATSAPP_FROM)
        delete_pending_bill(whatsapp_number)
        send_whatsapp(
            whatsapp_number,
            (
                "\U0001f4f7 Please send a clearer photo of the bill.\n\n"
                "Tips for a better scan:\n"
                "  - Good lighting, no shadows\n"
                "  - Full bill visible in frame\n"
                "  - Hold camera steady"
            ),
            wa_from,
        )
        return twiml_ack("")

    # ── CONFIRM DATE command (human-in-the-loop date validation) ──
    if body_text == "CONFIRM DATE":
        pending = _pending_bills.pop(whatsapp_number, None)
        if not pending:
            return twiml_ack("ℹ️ No pending bill to confirm.\nSend a bill photo to get started.")

        if time.time() > pending["expires_at"]:
            return twiml_ack(
                "⏰ Confirmation expired.\n\n"
                "Please re-send the bill photo to process it again."
            )

        # Save the confirmed invoice in a background thread
        wa_from = pending["wa_from"]
        inv_data = pending["invoice_data"]

        def save_confirmed_bill():
            try:
                bill_id = save_invoice(inv_data, whatsapp_number)
                vendor = inv_data.get('vendor_name', 'N/A')
                inv_no = inv_data.get('invoice_number', 'N/A')
                inv_dt = inv_data.get('invoice_date', 'N/A')
                total  = inv_data.get('total_amount', 0)
                reply = (
                    f"✅ Date Confirmed — Bill Saved\n\n"
                    f"🏪 Vendor: {vendor}\n"
                    f"📄 Invoice: {inv_no}\n"
                    f"📅 Date: {inv_dt}\n"
                    f"💰 Total: Rs.{fmt_inr(total)}\n\n"
                    f"🆔 Bill ID: {bill_id}"
                )
            except DuplicateInvoiceError as dup:
                reply = (
                    f"ℹ️ Duplicate Invoice\n\n"
                    f"📅 Originally saved on: {dup.existing_invoice_date}\n"
                    f"🆔 Existing Bill ID: {dup.existing_bill_id}"
                )
            except Exception as e:
                reply = f"❌ Could not save bill.\nError: {str(e)[:200]}"
            send_whatsapp(whatsapp_number, reply, wa_from)

        threading.Thread(target=save_confirmed_bill, daemon=True).start()
        return twiml_ack("⏳ Saving confirmed bill...")

    # ── SUMMARY command ───────────────────────────────────────
    if body_text == "SUMMARY":
        try:
            s = get_monthly_summary(whatsapp_number)
            dashboard_url = os.getenv("DASHBOARD_URL", "")
            if s["bill_count"] == 0:
                reply = (
                    f"📊 GST Summary — {s['month']}\n\n"
                    f"No bills recorded this month yet.\n"
                    f"Send a bill photo to get started!"
                )
            else:
                reply = (
                    f"📊 GST Summary — {s['month']}\n\n"
                    f"🧾 Bills Processed : {s['bill_count']}\n"
                    f"💰 Total Purchase  : Rs.{fmt_inr(s['total_amount'])}\n\n"
                    f"Tax Breakdown:\n"
                    f"  CGST      : Rs.{fmt_inr(s['cgst'])}\n"
                    f"  SGST      : Rs.{fmt_inr(s['sgst'])}\n"
                    f"  IGST      : Rs.{fmt_inr(s['igst'])}\n"
                    f"  Total Tax : Rs.{fmt_inr(s['grand_tax_total'])}"
                    + (f"\n\n📊 Dashboard: {dashboard_url}" if dashboard_url else "")
                )
        except Exception as e:
            reply = f"⚠️ Could not fetch summary.\n\nError: {e}"
        return twiml_ack(reply)

    # ── No photo ──────────────────────────────────────────────
    if num_media == 0 or not media_url:
        return twiml_ack(
            "👋 Welcome to GST Bill Tracker!\n\n"
            "📸 To process a bill:\nSend a clear photo of your GST invoice.\n\n"
            "📊 Monthly summary:\nType SUMMARY\n\n"
            + (f"🌐 Dashboard: {os.getenv('DASHBOARD_URL', '')}" if os.getenv("DASHBOARD_URL") else "")
        )

    # ── Wrong file type ───────────────────────────────────────
    IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif')
    is_image_doc = (
        media_type in ("application/octet-stream", "")
        and media_filename.endswith(IMAGE_EXTS)
    )
    if not media_type.startswith("image/") and not is_image_doc:
        return twiml_ack(
            "❌ Unsupported file type.\n\n"
            "Please send a photo (JPG or PNG) of your GST bill."
        )

    # ── Image: ACK immediately, process in background ────────
    wa_from = request.form.get("To", TWILIO_WHATSAPP_FROM)
    threading.Thread(
        target=process_bill_in_background,
        args=(whatsapp_number, media_url, wa_from),
        daemon=True
    ).start()

    return twiml_ack("⏳ Got your bill! Processing now... you'll receive the result in 1-2 minutes.")


# ════════════════════════════════════════════════════════════════
# ENTRY POINT
# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"Starting GST ACT AI on port {port} ...")
    print(f"Dashboard : http://localhost:{port}/dashboard")
    print(f"Webhook   : POST http://localhost:{port}/webhook")
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)

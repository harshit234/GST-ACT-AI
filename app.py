"""
app.py — WhatsApp Bot Webhook
=============================
The actual WhatsApp bot — connects everything together.

Input  → WhatsApp message from Twilio webhook (POST /webhook)
Process → Immediately ACKs Twilio (within 15s timeout), then:
          If photo: runs OCR → Extract → Validate → Save in background thread,
                    sends result via Twilio outbound API
          If SUMMARY: sends monthly GST total (fast, no background needed)
          If no photo: sends instructions
Output → WhatsApp reply back to merchant via Twilio outbound API
"""

import os
import sys
import threading
import requests

from flask import Flask, request, Response
from dotenv import load_dotenv
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse

from ocr import detect_text
from extract import extract_invoice_details
from validate import validate_gst_invoice
from db import save_invoice, get_monthly_summary
from exceptions import BlurryImageError, NotAnInvoiceError, DuplicateInvoiceError

load_dotenv()

app = Flask(__name__)

# ─────────────────────────────────────────────
# Twilio outbound client (for sending replies from background thread)
# ─────────────────────────────────────────────
TWILIO_ACCOUNT_SID   = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN    = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM") or os.getenv("TWILIO_WHATSAPP_NUMBER") or "whatsapp:+14155238886"

twilio_client = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


# ─────────────────────────────────────────────
# Helper: Indian currency formatter  (e.g. 843709 → "8,43,709")
# ─────────────────────────────────────────────
def fmt_inr(val) -> str:
    """Format a numeric value in Indian comma style (lakhs/crores)."""
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


# ─────────────────────────────────────────────
# Helper: Send WhatsApp message via Twilio outbound API
# ─────────────────────────────────────────────
def send_whatsapp(to: str, body: str, from_number: str = None):
    """
    Send a WhatsApp message to a number using Twilio outbound API.
    `to` should be like '+919999999999' (without whatsapp: prefix).
    """
    if not twilio_client:
        print(f"[send_whatsapp] Twilio client not configured. Would have sent to {to}:\n{body}")
        return
    try:
        sender = from_number or TWILIO_WHATSAPP_FROM
        msg = twilio_client.messages.create(
            from_=sender,
            to=f"whatsapp:{to}",
            body=body
        )
        print(f"[send_whatsapp] Sent to {to} from {sender}, SID: {msg.sid}")
    except Exception as e:
        print(f"[send_whatsapp] ERROR sending to {to} from {from_number or TWILIO_WHATSAPP_FROM}: {e}", file=sys.stderr)


# ─────────────────────────────────────────────
# Helper: Immediate TwiML empty ACK (so Twilio doesn't time out)
# ─────────────────────────────────────────────
def twiml_ack(message: str = "") -> Response:
    """Return a quick TwiML response with an optional short acknowledgement."""
    resp = MessagingResponse()
    if message:
        resp.message(message)
    return Response(
        str(resp).encode("utf-8"),
        mimetype="text/xml",
        headers={"Content-Type": "text/xml; charset=utf-8"}
    )


# ─────────────────────────────────────────────
# Helper: Download image bytes from Twilio URL
# ─────────────────────────────────────────────
def download_media(media_url: str) -> bytes:
    """Download media from Twilio's media URL with credentials."""
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
        resp = requests.get(media_url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=30)
    else:
        resp = requests.get(media_url, timeout=30)
    resp.raise_for_status()
    return resp.content


# ─────────────────────────────────────────────
# Background worker: full pipeline after ACK
# ─────────────────────────────────────────────
def process_bill_in_background(whatsapp_number: str, media_url: str, twilio_whatsapp_from: str):
    """
    Runs in a background thread AFTER Twilio webhook has been ACK'd.
    Sends the final reply using Twilio outbound API.
    """
    print(f"[bg] Starting background processing for {whatsapp_number}")
    try:
        # Step 1: Download image
        print(f"[bg] Downloading image ...")
        image_bytes = download_media(media_url)
        print(f"[bg] Downloaded: {len(image_bytes)} bytes")

        # Step 2: OCR
        print("[bg] Running OCR ...")
        ocr_text = detect_text(image_bytes)
        print("[bg] OCR complete.")

        # Step 3: Extract
        print("[bg] Extracting invoice details ...")
        invoice_data = extract_invoice_details(ocr_text)
        print(f"[bg] Extracted: {invoice_data.get('vendor_name')} | Total: {invoice_data.get('total_amount')}")

        # Step 4: Validate
        print("[bg] Validating ...")
        is_valid, validation_msg = validate_gst_invoice(invoice_data)
        print(f"[bg] Validation: {is_valid}")

        # Step 5: Save
        print("[bg] Saving to Supabase ...")
        bill_id = save_invoice(invoice_data, whatsapp_number)
        print(f"[bg] Saved. Bill ID: {bill_id}")

        # Build success reply — Message 1: Invoice summary
        vendor      = invoice_data.get("vendor_name",    "N/A")
        inv_no      = invoice_data.get("invoice_number", "N/A")
        inv_date    = invoice_data.get("invoice_date",   "N/A")
        line_items  = invoice_data.get("line_items", [])
        items_count = len(line_items)
        total       = invoice_data.get("total_amount", 0)
        cgst        = invoice_data.get("cgst", 0)
        sgst        = invoice_data.get("sgst", 0)
        igst        = invoice_data.get("igst", 0)

        reply = (
            f"✅ Bill Processed Successfully!\n\n"
            f"🏪 Vendor     : {vendor}\n"
            f"📄 Invoice No : {inv_no}\n"
            f"📅 Date       : {inv_date}\n"
            f"📦 Items      : {items_count}\n\n"
            f"Amount Breakdown:\n"
            f"  CGST : Rs.{fmt_inr(cgst)}\n"
            f"  SGST : Rs.{fmt_inr(sgst)}\n"
            f"  IGST : Rs.{fmt_inr(igst)}\n"
            f"  Total: Rs.{fmt_inr(total)}\n\n"
            f"🆔 Bill ID: {bill_id}"
        )

        # Message 2: Item-wise GST details (chunked to stay within Twilio's 1600-char limit)
        if line_items:
            CHUNK_LIMIT = 1500
            chunks = []
            current_chunk = "📋 Item-wise GST Details:\n"

            for idx, item in enumerate(line_items, 1):
                desc       = item.get("description", "N/A")
                amount     = item.get("amount")
                gst_rate   = item.get("gst_rate")
                gst_amount = item.get("gst_amount")

                item_line  = f"\n{idx}. {desc}\n"
                item_line += f"   Amount: Rs.{fmt_inr(amount)}\n"
                item_line += f"   GST Rate: {gst_rate}%" if gst_rate is not None else "   GST Rate: N/A"
                item_line += f" | GST: Rs.{fmt_inr(gst_amount)}\n" if gst_amount is not None else " | GST: N/A\n"

                # If adding this item would exceed the limit, flush current chunk and start a new one
                if len(current_chunk) + len(item_line) > CHUNK_LIMIT:
                    chunks.append(current_chunk)
                    current_chunk = f"📋 GST Details (cont.):\n{item_line}"
                else:
                    current_chunk += item_line

            if current_chunk:
                chunks.append(current_chunk)

            for chunk in chunks:
                send_whatsapp(whatsapp_number, chunk, twilio_whatsapp_from)

    except BlurryImageError:
        print(f"[bg] Blurry image detected for {whatsapp_number}")
        reply = (
            "\U0001f4f7 Photo Too Blurry\n\n"
            "We couldn't read the text on your bill.\n\n"
            "Please try again with:\n"
            "  \u2022 Better lighting\n"
            "  \u2022 Steady hands (no shaking)\n"
            "  \u2022 The full bill in frame"
        )

    except NotAnInvoiceError:
        print(f"[bg] Non-invoice photo detected for {whatsapp_number}")
        reply = (
            "\U0001f6ab Not a GST Bill\n\n"
            "The photo you sent doesn't appear to be a GST invoice.\n\n"
            "Please send a clear photo of a valid GST bill with:\n"
            "  \u2022 GSTIN number visible\n"
            "  \u2022 Invoice number & date\n"
            "  \u2022 Tax breakdown (CGST/SGST/IGST)"
        )

    except DuplicateInvoiceError as dup:
        vendor = "N/A"
        inv_no = "N/A"
        try:
            vendor = invoice_data.get("vendor_name",    "N/A")
            inv_no = invoice_data.get("invoice_number", "N/A")
        except Exception:
            pass
        reply = (
            f"\u2139\ufe0f Duplicate Invoice Detected\n\n"
            f"This invoice has already been recorded.\n\n"
            f"\U0001f3ea Vendor     : {vendor}\n"
            f"\U0001f4c4 Invoice No : {inv_no}\n\n"
            f"\U0001f194 Existing Bill ID:\n{dup.existing_bill_id}\n\n"
            f"No duplicate entry was created."
        )

    except Exception as e:
        print(f"[bg] ERROR: {e}", file=sys.stderr)
        reply = (
            f"\u274c Processing Failed\n\n"
            f"Sorry, we couldn't process your bill.\n\n"
            f"Please make sure:\n"
            f"  \u2022 The photo is clear and well-lit\n"
            f"  \u2022 The full bill is visible\n"
            f"  \u2022 It is a valid GST invoice\n\n"
            f"Error: {str(e)[:200]}"
        )

    # Send the final result back via outbound Twilio API
    send_whatsapp(whatsapp_number, reply, twilio_whatsapp_from)
    print(f"[bg] Done for {whatsapp_number}")


# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────
@app.route("/", methods=["GET"])
def home():
    return {"status": "active", "message": "GST ACT AI Bot is running."}


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    POST /webhook — Twilio WhatsApp webhook handler.
    IMPORTANT: Must respond within 15 seconds or Twilio drops the reply.
    Strategy: ACK immediately, process in background thread, reply via outbound API.
    """
    # ── Parse Twilio POST fields ──────────────────────────────────────────
    from_raw   = request.form.get("From", "")
    body_text  = request.form.get("Body", "").strip().upper()
    num_media      = int(request.form.get("NumMedia", 0))
    media_url      = request.form.get("MediaUrl0", "")
    media_type     = request.form.get("MediaContentType0", "")
    media_filename = request.form.get("MediaFilename0", "").lower()

    whatsapp_number = from_raw.replace("whatsapp:", "").strip()
    print(f"[webhook] From={whatsapp_number}, NumMedia={num_media}, Body='{request.form.get('Body', '')}'")

    # ── SUMMARY command (fast — no background needed) ─────────────────────
    if body_text == "SUMMARY":
        try:
            summary = get_monthly_summary(whatsapp_number)
            if summary["bill_count"] == 0:
                reply = (
                    f"📊 GST Summary — {summary['month']}\n\n"
                    f"No bills recorded this month yet.\n"
                    f"Send a bill photo to get started!"
                )
            else:
                reply = (
                    f"📊 GST Summary — {summary['month']}\n\n"
                    f"🧾 Bills Processed : {summary['bill_count']}\n"
                    f"💰 Total Purchase  : Rs.{fmt_inr(summary['total_amount'])}\n\n"
                    f"Tax Breakdown:\n"
                    f"  CGST      : Rs.{fmt_inr(summary['cgst'])}\n"
                    f"  SGST      : Rs.{fmt_inr(summary['sgst'])}\n"
                    f"  IGST      : Rs.{fmt_inr(summary['igst'])}\n"
                    f"  Total Tax : Rs.{fmt_inr(summary['grand_tax_total'])}"
                )
        except Exception as e:
            reply = f"⚠️ Could not fetch summary.\n\nError: {e}"
        return twiml_ack(reply)

    # ── No photo ──────────────────────────────────────────────────────────
    if num_media == 0 or not media_url:
        return twiml_ack(
            "👋 Welcome to GST Bill Tracker!\n\n"
            "📸 To process a bill:\nSend a clear photo of your GST invoice.\n\n"
            "📊 To see monthly summary:\nType SUMMARY"
        )

    # ── Wrong file type ───────────────────────────────────────────────────
    # Also accept image files sent as document attachments (e.g. from WhatsApp Desktop)
    IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif')
    is_image_doc = (
        media_type in ("application/octet-stream", "")
        and media_filename.endswith(IMAGE_EXTENSIONS)
    )
    if not media_type.startswith("image/") and not is_image_doc:
        return twiml_ack(
            "❌ Unsupported file type.\n\n"
            "Please send a photo (JPG or PNG) of your GST bill."
        )

    # ── Image received: ACK immediately, process in background ───────────
    print(f"[webhook] Image received. media_type={media_type!r} filename={media_filename!r}. ACKing Twilio, starting background thread ...")
    twilio_whatsapp_from = request.form.get("To", TWILIO_WHATSAPP_FROM)
    thread = threading.Thread(
        target=process_bill_in_background,
        args=(whatsapp_number, media_url, twilio_whatsapp_from),
        daemon=True
    )
    thread.start()

    # Immediate ACK back to Twilio — within the 15s timeout
    return twiml_ack("⏳ Got your bill! Processing now... you'll receive the result in 1-2 minutes.")


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"Starting GST ACT AI Bot on port {port} ...")
    # use_reloader=False is important — prevents Flask from killing background threads
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)

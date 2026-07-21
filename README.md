# 📊 GST-ACT-AI

An AI-powered WhatsApp assistant designed to convert GST invoices into structured, accounting-ready database records in seconds. 

Built with **Flask**, **Google Cloud Vision API**, **Gemini (gemini-2.5-flash)**, **Twilio WhatsApp API**, and **Supabase**, this bot automates the intake, OCR, parsing, mathematical validation, and recording of retail and wholesale purchase invoices.

📐 **[View the High-Level Design Architecture →](ARCHITECTURE.md)** — Complete system design with component diagrams, data flow, database schema, API surface, and design trade-offs.

---

## 🚀 Key Features

- **Automated Document Parsing**: Extracts vendor details, invoice numbers, invoice dates, CGST/SGST/IGST, total amounts, and itemized lists with HSN/SAC codes.
- **Fast Webhook Acknowledgement**: Uses an asynchronous background worker thread to immediately ACK Twilio’s webhook within 15 seconds, preventing timeouts while processing heavy OCR/AI jobs in the background.
- **OCR Redundancy / Fallback**: Attempts text extraction using **Google Cloud Vision API** first and falls back to **Gemini OCR** if the Vision API is unavailable.
- **Quality Gates**: Rejects blurry images, non-invoice images, and files with empty fields automatically.
- **Mathematical Validation**: Checks if itemized sums + tax totals equal the bill's total amount within a tolerance of ₹10.
- **Duplicate Prevention**: Normalizes invoice characters (e.g. `INV/24-25/001` vs `INV2425001`) to detect and block duplicate entries.
- **Monthly Aggregate Reports**: Allows users to retrieve their monthly purchase summary instantly via a simple WhatsApp message `SUMMARY`.

---

## 🛠️ Architecture and Data Flow

```mermaid
sequenceDiagram
    autonumber
    actor User as Merchant (WhatsApp)
    participant Twilio as Twilio Webhook
    participant Flask as Flask Server (app.py)
    participant OCR as OCR Module (Google Vision / Gemini)
    participant Gemini as Gemini AI (gemini-2.5-flash)
    participant Supabase as Supabase Database

    User->>Twilio: Sends Bill Image / "SUMMARY"
    Twilio->>Flask: Forward webhook POST /webhook
    alt Command is "SUMMARY"
        Flask->>Supabase: Query monthly totals
        Supabase-->>Flask: Returns aggregates
        Flask-->>Twilio: Synchronous TwiML Response
        Twilio-->>User: Sends Monthly Report
    else User sends image
        Flask->>Flask: Spawn background thread
        Flask-->>Twilio: Immediate "R
        OCR-->>Flask: Raw extracted text
        Flask->>Gemini: Parse structured JSON
        Gemini-->>Flask: Structured invoice dictionary
        Flask->>Flask: Mathematically validate totals
        Flask->>Supabase: Duplicate check & insert record
        Supabase-->>Flask: Created Bill UUID
        Flask->>Twilio: Outbound WhatsApp API call
        Twilio-->>User: Detailed summary + itemized breakdown
    end
```Got your bill!" ACK (TwiML)
        Twilio-->>User: "Got your bill! Processing..."
        Note over Flask, Supabase: Asynchronous Processing Pipeline
        Flask->>OCR: Download Image & run OC

---

## 📋 Environment Variables

Create a `.env` file in the root directory and configure the following variables:

```bash
# Server Port
PORT=5000

# Google AI Studio (Gemini)
GEMINI_API_KEY=your_gemini_api_key

# Google Cloud Vision
GOOGLE_VISION_API_KEY=your_google_cloud_vision_api_key

# Twilio Credentials
TWILIO_ACCOUNT_SID=your_twilio_account_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886

# Supabase Credentials
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_anon_key
```

---

## 📦 Database Schema (Supabase)

The bot operates on two tables: `merchants` and `bills`.

### 1. `merchants` Table
- `id` (UUID, Primary Key)
- `whatsapp_number` (text, Unique)
- `name` (text)
- `gstin` (text, Nullable)
- `created_at` (timestamp)

### 2. `bills` Table
- `id` (UUID, Primary Key)
- `merchant_id` (UUID, Foreign Key → `merchants.id`)
- `whatsapp_number` (text)
- `vendor_name` (text)
- `vendor_gstin` (text)
- `invoice_number` (text)
- `invoice_date` (text)
- `cgst` (numeric)
- `sgst` (numeric)
- `igst` (numeric)
- `total_amount` (numeric)
- `line_items` (JSONB)
- `created_at` (timestamp)

---

## 💻 Setup and Installation

### 1. Clone the repository and navigate to it:
```bash
git clone https://github.com/harshit234/GST-ACT-AI.git
cd GST-ACT-AI
```

### 2. Create and activate a Virtual Environment:
- **Windows**:
  ```powershell
  python -m venv venv
  venv\Scripts\activate
  ```
- **macOS/Linux**:
  ```bash
  python3 -m venv venv
  source venv/bin/activate
  ```

### 3. Install the dependencies:
```bash
pip install -r requirements.txt
```

### 4. Run the application locally:
```bash
python app.py
```

---

## 🧪 Testing Files

You can test individual components of the pipeline using their CLI entry points:

### Test OCR Detection:
```bash
python ocr.py <path_to_bill_image>
```

### Test Structured Extraction:
```bash
python extract.py <path_to_bill_image>
```

### Test Mathematical Validation:
```bash
python validate.py <path_to_bill_image>
```

---

## 🚀 Deployment

The project contains a `startup.sh` script pre-configured for **Azure App Service**:
```bash
gunicorn --bind=0.0.0.0:8000 --timeout 120 --workers 2 app:app
```
To run the server with multiple workers in a production environment, bind `gunicorn` accordingly.

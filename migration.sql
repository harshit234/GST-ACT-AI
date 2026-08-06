-- ============================================
-- GST ACT AI — Invoice Generator Migration
-- ============================================
-- Run this in your Supabase SQL Editor before
-- using the invoice generation feature.
-- ============================================

-- 1. Add columns to bills table for sales invoices
ALTER TABLE bills ADD COLUMN IF NOT EXISTS bill_type TEXT DEFAULT 'purchase';
ALTER TABLE bills ADD COLUMN IF NOT EXISTS customer_name TEXT;
ALTER TABLE bills ADD COLUMN IF NOT EXISTS customer_gstin TEXT;
ALTER TABLE bills ADD COLUMN IF NOT EXISTS customer_phone TEXT;
ALTER TABLE bills ADD COLUMN IF NOT EXISTS customer_email TEXT;
ALTER TABLE bills ADD COLUMN IF NOT EXISTS customer_address TEXT;
ALTER TABLE bills ADD COLUMN IF NOT EXISTS customer_state TEXT;
ALTER TABLE bills ADD COLUMN IF NOT EXISTS pdf_url TEXT;
ALTER TABLE bills ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active';
ALTER TABLE bills ADD COLUMN IF NOT EXISTS subtotal NUMERIC(12,2) DEFAULT 0;
ALTER TABLE bills ADD COLUMN IF NOT EXISTS amount_in_words TEXT;

-- 2. Add merchant business profile fields
ALTER TABLE merchants ADD COLUMN IF NOT EXISTS business_name TEXT;
ALTER TABLE merchants ADD COLUMN IF NOT EXISTS business_gstin TEXT;
ALTER TABLE merchants ADD COLUMN IF NOT EXISTS business_address TEXT;
ALTER TABLE merchants ADD COLUMN IF NOT EXISTS business_state TEXT;
ALTER TABLE merchants ADD COLUMN IF NOT EXISTS business_phone TEXT;
ALTER TABLE merchants ADD COLUMN IF NOT EXISTS business_email TEXT;

-- 3. HSN Cache table for AI lookup caching
CREATE TABLE IF NOT EXISTS hsn_cache (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  item_name_normalized TEXT UNIQUE NOT NULL,
  hsn_code TEXT,
  gst_rate NUMERIC,
  unit TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Create Supabase Storage bucket for invoice PDFs
-- Run this OR create the bucket manually in Supabase Dashboard → Storage:
-- INSERT INTO storage.buckets (id, name, public) VALUES ('invoices', 'invoices', true);

-- 5. Storage policy: allow public reads and authenticated uploads
-- CREATE POLICY "Public invoice read" ON storage.objects FOR SELECT USING (bucket_id = 'invoices');
-- CREATE POLICY "Allow invoice upload" ON storage.objects FOR INSERT WITH CHECK (bucket_id = 'invoices');

-- 6. Performance indexes
CREATE INDEX IF NOT EXISTS idx_bills_bill_type ON bills(bill_type);
CREATE INDEX IF NOT EXISTS idx_bills_whatsapp_bill_type ON bills(whatsapp_number, bill_type);
CREATE INDEX IF NOT EXISTS idx_hsn_cache_normalized ON hsn_cache(item_name_normalized);

-- 7. Pending bills table — holds invoices awaiting merchant math-validation confirmation
--    Merchant replies "1" to confirm save, "2" to discard and resend photo.
--    UNIQUE on whatsapp_number: each merchant has at most one pending bill at a time.
CREATE TABLE IF NOT EXISTS pending_bills (
  id               UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  whatsapp_number  TEXT NOT NULL,
  invoice_data     JSONB NOT NULL,
  wa_from          TEXT,
  bill_total       NUMERIC(12,2),
  calculated_total NUMERIC(12,2),
  difference       NUMERIC(12,2),
  created_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_bills_phone
  ON pending_bills(whatsapp_number);

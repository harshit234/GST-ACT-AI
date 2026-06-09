import os, re, sys
from dotenv import load_dotenv
from supabase import create_client

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

load_dotenv()
client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

def norm(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())

# Get all Pooja Decorative bills
r = client.table("bills").select("id, invoice_number, created_at").eq("vendor_gstin", "06AAAFP9141P1ZU").order("created_at").execute()
print("All PDP bills found:")
for b in r.data:
    inv = b["invoice_number"]
    print(f"  {b['created_at']} | {inv} | norm={norm(inv)} | id={b['id']}")

# Delete all for a clean test slate
ids = [b["id"] for b in r.data]
if ids:
    client.table("bills").delete().in_("id", ids).execute()
    print(f"\nDeleted {len(ids)} record(s). Clean slate for testing.")
else:
    print("No records found — already clean.")

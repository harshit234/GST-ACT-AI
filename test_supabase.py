import os
from dotenv import load_dotenv
load_dotenv()
from supabase import create_client

url = os.getenv('SUPABASE_URL')
key = os.getenv('SUPABASE_KEY')
print(f'URL: {url}')
print(f'KEY: {key[:30]}...' if key else 'KEY: None')

client = create_client(url, key)

tables = ['merchants', 'bills', 'pending_bills', 'hsn_cache']
for table in tables:
    try:
        r = client.table(table).select('id').limit(1).execute()
        print(f'[OK] {table} - {len(r.data)} row(s) returned')
    except Exception as e:
        print(f'[FAIL] {table}: {e}')

print('Supabase check complete.')

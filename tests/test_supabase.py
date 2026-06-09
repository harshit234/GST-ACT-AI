import os
from supabase import create_client, Client
from postgrest.exceptions import APIError
from dotenv import load_dotenv

# Load environment variables from the root directory .env file
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")

if not url or not key:
    print("Error: SUPABASE_URL or SUPABASE_KEY not found in environment variables.")
    exit(1)

try:
    supabase: Client = create_client(url, key)
    
    try:
        supabase.table("health_check_dummy").select("*").limit(1).execute()
        print("Supabase: connected")
    except APIError as e:
        # Extract code and message safely
        code = getattr(e, "code", None)
        message = getattr(e, "message", "")
        
        # If they are none/empty, try to parse from str(e)
        err_str = str(e)
        
        if code in ["42P01", "PGRST205", "PGRST116"] or "PGRST205" in err_str or "schema cache" in message or "schema cache" in err_str:
            print("Supabase: connected")
        else:
            print(f"Supabase Connection Failed: {e}")
except Exception as e:
    print(f"Supabase Connection Failed: {e}")

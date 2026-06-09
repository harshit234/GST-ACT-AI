import os
from twilio.rest import Client
from dotenv import load_dotenv

# Load environment variables from the root directory .env file
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

account_sid = os.getenv("TWILIO_ACCOUNT_SID")
auth_token = os.getenv("TWILIO_AUTH_TOKEN")

if not account_sid or not auth_token:
    print("Error: TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN not found in environment variables.")
    exit(1)

try:
    client = Client(account_sid, auth_token)
    # Fetch account details to verify credentials
    account = client.api.accounts(account_sid).fetch()
    print("Twilio: connected")
except Exception as e:
    print(f"Twilio Connection Failed: {e}")

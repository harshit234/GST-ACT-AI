import os
import google.generativeai as genai
from dotenv import load_dotenv

# Load environment variables from the root directory .env file
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("Error: GEMINI_API_KEY not found in environment variables.")
    exit(1)

genai.configure(api_key=api_key)

try:
    # Use gemini-3.5-flash as supported by the listed models
    model = genai.GenerativeModel('gemini-3.5-flash')
    response = model.generate_content("Say 'connected'")
    print(f"Gemini: {response.text.strip()}")
except Exception as e:
    print(f"Gemini Connection Failed: {e}")

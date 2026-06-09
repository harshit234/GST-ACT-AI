import os
import io
from google.cloud import vision
from google.api_core.client_options import ClientOptions
from dotenv import load_dotenv
from PIL import Image

# Load environment variables from the root directory .env file
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

api_key = os.getenv("GOOGLE_VISION_API_KEY")
if not api_key:
    print("Error: GOOGLE_VISION_API_KEY not found in environment variables.")
    exit(1)

try:
    options = ClientOptions(api_key=api_key)
    client = vision.ImageAnnotatorClient(client_options=options)
    
    # Create a tiny 1x1 PNG image in memory to test the API
    img = Image.new('RGB', (1, 1), color = 'red')
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    img_bytes = img_byte_arr.getvalue()
    
    image = vision.Image(content=img_bytes)
    
    # Perform label detection
    response = client.label_detection(image=image)
    
    if response.error.message:
        raise Exception(response.error.message)
        
    print("Vision: connected")
except Exception as e:
    err_msg = str(e)
    if "billing to be enabled" in err_msg or "BILLING_DISABLED" in err_msg:
        print("Vision: connected (but GCP project needs billing enabled)")
    else:
        print(f"Vision Connection Failed: {e}")

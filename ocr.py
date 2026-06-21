import os
import io
from google.cloud import vision
from google.api_core.client_options import ClientOptions
import google.generativeai as genai
from PIL import Image
from dotenv import load_dotenv
from exceptions import BlurryImageError

# Load environment variables
load_dotenv()

# Configure Gemini
api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)

def perform_vision_ocr(image_bytes: bytes) -> str:
    """Performs OCR using Google Cloud Vision API."""
    vision_key = os.getenv("GOOGLE_VISION_API_KEY")
    if not vision_key:
        raise ValueError("GOOGLE_VISION_API_KEY not set in environment.")
        
    options = ClientOptions(api_key=vision_key)
    client = vision.ImageAnnotatorClient(client_options=options)
    
    image = vision.Image(content=image_bytes)
    response = client.text_detection(image=image)
    
    if response.error.message:
        raise Exception(response.error.message)
        
    texts = response.text_annotations
    if texts:
        return texts[0].description
    return ""

def perform_gemini_ocr(image_bytes: bytes) -> str:
    """Performs OCR using Gemini Multimodal fallback."""
    # Reload environment variables to catch runtime changes to the API key
    load_dotenv(override=True)
    current_key = os.getenv("GEMINI_API_KEY")
    if not current_key:
        raise ValueError("GEMINI_API_KEY not set in environment.")
    
    # Configure/re-configure genai with the latest key
    genai.configure(api_key=current_key)
        
    # Convert bytes to PIL Image
    image = Image.open(io.BytesIO(image_bytes))
    
    # Use gemini-2.5-flash
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    prompt = "Perform OCR on this image. Extract and transcribe all text present in the image exactly as it appears. Do not summarize or add extra commentary."
    response = model.generate_content([image, prompt])
    return response.text

def detect_text(image_bytes: bytes) -> str:
    """
    Detects text from bill image bytes.
    Attempts Google Cloud Vision API first. Falls back to Gemini OCR if Vision API fails.
    Raises BlurryImageError if the extracted text is too short (< 30 chars).
    
    Input: Bill image bytes (from WhatsApp or local file)
    Process: Google Vision API reads the image (fallback: Gemini OCR)
    Output: Raw text string of everything on the bill
    """
    text = None
    try:
        print("Attempting Google Cloud Vision OCR...")
        text = perform_vision_ocr(image_bytes)
        print("Google Cloud Vision OCR succeeded.")
    except Exception as e:
        print(f"Google Cloud Vision OCR failed: {e}")
        print("Attempting Gemini OCR fallback...")
        try:
            text = perform_gemini_ocr(image_bytes)
            print("Gemini OCR fallback succeeded.")
        except Exception as gemini_err:
            print(f"Gemini OCR fallback failed: {gemini_err}")
            raise gemini_err

    # ── Quality gate: reject blurry / unreadable images ──
    cleaned = (text or "").strip()
    if len(cleaned) < 30:
        raise BlurryImageError(
            f"OCR returned only {len(cleaned)} characters — image is likely blurry or unreadable."
        )

    return text

if __name__ == "__main__":
    import sys
    
    # Configure stdout to use UTF-8 if supported (useful on Windows consoles)
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
            
    # Check if a file path is provided via command line arguments
    if len(sys.argv) > 1:
        image_path = sys.argv[1]
    else:
        # Default fallback test image
        image_path = os.path.join(os.path.dirname(__file__), "tests", "test_image.png")
        if not os.path.exists(image_path):
            os.makedirs(os.path.dirname(image_path), exist_ok=True)
            img = Image.new('RGB', (200, 60), color = 'white')
            from PIL import ImageDraw
            d = ImageDraw.Draw(img)
            d.text((10, 20), "GST INVOICE #987654321", fill=(0, 0, 0))
            img.save(image_path)
            
    print(f"Testing OCR detection with image bytes from: {image_path}...")
    if not os.path.exists(image_path):
        print(f"Error: File {image_path} not found.")
        sys.exit(1)
        
    with open(image_path, "rb") as f:
        bill_image_bytes = f.read()
        
    try:
        text = detect_text(bill_image_bytes)
        print("\n--- Extracted Text ---")
        try:
            print(text)
        except UnicodeEncodeError:
            # Fallback to write directly using utf-8 bytes or replace unsupported chars
            if hasattr(sys.stdout, 'buffer'):
                sys.stdout.buffer.write(text.encode('utf-8', errors='replace'))
                sys.stdout.write('\n')
            else:
                print(text.encode('ascii', errors='replace').decode('ascii'))
        print("----------------------")
    except Exception as e:
        print(f"Error during OCR detection: {e}")





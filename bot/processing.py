# ------------------ bot/processing.py ------------------
import re
import io
import os
from datetime import datetime
from PIL import Image, ImageEnhance
import pytesseract
import spacy
from openai import AsyncOpenAI
from dotenv import load_dotenv

# Load environment variables to get the API key
load_dotenv()
# Instantiate the new client
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# For the first run, you'll need to download the spaCy model:
# python -m spacy download en_core_web_sm
try:
    nlp = spacy.load("en_core_web_sm")
    print("✅ spaCy model loaded.")
except OSError:
    print("❌ spaCy model 'en_core_web_sm' not found. Please run: python -m spacy download en_core_web_sm")
    nlp = None

# --- List of common merchants for keyword matching (can be expanded) ---
COMMON_MERCHANTS = [
    "7-eleven", "starbucks", "mcdonald's", "kfc", "fairprice", "ntuc", 
    "cold storage", "giant", "sheng siong", "guardian", "watsons", 
    "grab", "gojek", "foodpanda", "deliveroo", "koufu", "toast box",
    "ya kun kaya toast", "subway", "ikea", "daiso", "singtel", "starhub",
    "m1", "shopee", "lazada", "amazon"
]

async def get_category_from_openai(merchant: str, amount: float) -> str:
    """
    Uses OpenAI to categorize a transaction based on the merchant.
    """
    if not client.api_key:
        print("⚠️ OpenAI API key not found. Skipping categorization.")
        return "Uncategorized"

    categories = ["Food & Drinks", "Transport", "Shopping", "Groceries", "Bills & Utilities", "Entertainment", "Health", "Services", "Other"]
    
    try:
        print(f"🤖 Asking OpenAI to categorize transaction at '{merchant}'...")
        response = await client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that categorizes expenses. Respond with only one category from this list: " + ", ".join(categories)},
                {"role": "user", "content": f"What is the category for a transaction of SGD {amount:.2f} at '{merchant}'?"}
            ],
            temperature=0,
            max_tokens=20,
            timeout=10 # Add a timeout to prevent long waits
        )
        category = response.choices[0].message.content.strip()
        print(f"🧠 OpenAI suggested category: {category}")
        return category if category in categories else "Other"
    except Exception as e:
        print(f"❌ OpenAI Error: {e}")
        return "Uncategorized"

async def extract_from_text(text: str, with_category: bool = True) -> dict:
    """
    Extracts transaction details. Can optionally skip AI categorization.
    """
    parsed_data = {}
    text = text.strip()

    # 1. Amount Extraction
    amount_str = None
    match_full = re.search(r"(?:SGD|S\$|\$)\s*(\d+(?:\.\d{1,2})?)", text, re.IGNORECASE)
    if match_full:
        amount_str = match_full.group(1)
    if not amount_str:
        found_numbers = re.findall(r"(\d+(?:\.\d{1,2})?)", text)
        if found_numbers:
            amount_str = found_numbers[-1]

    if amount_str:
        parsed_data["amount"] = float(amount_str)
        parsed_data["currency"] = "SGD"

    # 2. Merchant Extraction
    merchant = "Unknown"
    text_for_merchant = text
    if amount_str:
        text_for_merchant = re.sub(r'\b' + re.escape(amount_str) + r'\b', '', text_for_merchant, 1).strip()

    if not text_for_merchant:
        parsed_data["merchant"] = "Unknown"
    else:
        lower_text = text_for_merchant.lower()
        for known_merchant in COMMON_MERCHANTS:
            if known_merchant in lower_text:
                merchant = known_merchant.title()
                break
        if merchant == "Unknown":
            caps_match = re.search(r"\b([A-Z][A-Z\s]{2,})\b", text_for_merchant)
            if caps_match:
                merchant = caps_match.group(1).strip()
        if merchant == "Unknown" and nlp:
            doc = nlp(text_for_merchant)
            entities = [ent.text.strip() for ent in doc.ents if ent.label_ in ("ORG", "GPE", "PERSON")]
            if entities:
                merchant = entities[0]
        if merchant == "Unknown":
            noun_matches = re.findall(r"\b([a-zA-Z]{3,})\b", text_for_merchant)
            if noun_matches:
                for word in noun_matches:
                    if word.lower() not in ['sgd', 'spent', 'at', 'to', 'from']:
                        merchant = word.title()
                        break

    parsed_data["merchant"] = merchant.strip().title()
    if parsed_data["merchant"].lower() in ['sgd', '$', 's$']:
        parsed_data["merchant"] = "Unknown"
        
    print(f"Found amount: {parsed_data.get('amount')}")
    print(f"Found merchant: {parsed_data['merchant']}")

    # 3. AI-powered Categorization (optional)
    if with_category and parsed_data.get("amount") and parsed_data.get("merchant") != "Unknown":
        parsed_data["category"] = await get_category_from_openai(parsed_data["merchant"], parsed_data["amount"])
    else:
        parsed_data["category"] = "Uncategorized"

    parsed_data["date"] = datetime.now().date().isoformat()
    return parsed_data

def preprocess_image_for_ocr(image_bytes: bytearray) -> Image.Image:
    """Cleans up an image to improve OCR accuracy."""
    image = Image.open(io.BytesIO(image_bytes))
    image = image.convert('L')
    enhancer = ImageEnhance.Contrast(image)
    image = enhancer.enhance(2)
    return image

async def extract_from_image(image_bytes: bytearray, with_category: bool = True) -> tuple[str, dict]:
    """Performs OCR and then extracts transaction details."""
    try:
        processed_image = preprocess_image_for_ocr(image_bytes)
        custom_config = r'--oem 3 --psm 6'
        ocr_text = pytesseract.image_to_string(processed_image, config=custom_config)
        print(f"🔍 OCR Result:\n---\n{ocr_text}\n---")
        if not ocr_text.strip():
            return "No text found in image.", {}
        parsed_data = await extract_from_text(ocr_text, with_category)
        return ocr_text, parsed_data
    except Exception as e:
        print(f"❌ OCR Error: {e}")
        return f"Error during OCR: {e}", {}

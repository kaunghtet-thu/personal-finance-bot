# ------------------ database/connection.py ------------------
import os
from datetime import datetime, timedelta
import pymongo
import certifi
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Database Configuration ---
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME")

# --- Establish Connection ---
try:
    ca = certifi.where()
    client = pymongo.MongoClient(MONGO_URI, tlsCAFile=ca)
    db = client[MONGO_DB_NAME]
    transactions_collection = db["transactions"]
    client.admin.command('ping')
    print("✅ MongoDB connection successful.")
except Exception as e:
    print(f"❌ Could not connect to MongoDB: {e}")
    client = None
    transactions_collection = None

def save_transaction(raw_text: str, parsed_data: dict, image_url: str = None, source: str = "text"):
    """
    Saves a new transaction document with the new keywords schema.
    The 'merchant' field is now the first keyword.
    """
    if transactions_collection is None:
        print("❌ Cannot save transaction, database not connected.")
        return None

    # Standardize keywords: ensure they are clean and in a list.
    keywords = parsed_data.get('keywords', [])
    if not isinstance(keywords, list):
        keywords = [keywords]

    document = {
        "rawText": raw_text,
        "parsedData": {
            "amount": round(float(parsed_data.get("amount", 0.0)), 2),
            "currency": parsed_data.get("currency", "SGD"),
            "keywords": keywords # Save the new keywords array
        },
        "source": source,
        "imageUrl": image_url,
        "category": parsed_data.get("category", "Uncategorized"),
        "createdAt": datetime.now()
    }

    try:
        result = transactions_collection.insert_one(document)
        print(f"✅ Transaction saved with ID: {result.inserted_id}")
        return result.inserted_id
    except Exception as e:
        print(f"❌ Error saving transaction: {e}")
        return None

def get_spending_data(timeframe: str = 'week', filter_type: str = None, filter_value: str = None):
    """
    Fetches transactions based on dynamic filters. Now also searches keywords.
    """
    if transactions_collection is None:
        return None

    now = datetime.now()
    start_date = None
    if timeframe == 'day':
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif timeframe == 'week':
        start_date = now - timedelta(days=now.weekday())
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    elif timeframe == 'month':
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    pipeline = []
    
    if start_date:
        pipeline.append({"$match": {"createdAt": {"$gte": start_date}}})
        
    if filter_type and filter_value:
        # Search both category and the new keywords array
        field_to_filter = "category" if filter_type == "category" else "parsedData.keywords"
        pipeline.append({"$match": {field_to_filter: {"$regex": f"^{filter_value}$", "$options": "i"}}})

    pipeline.append({
        "$group": {
            "_id": "$category",
            "totalAmount": {"$sum": "$parsedData.amount"},
            "count": {"$sum": 1}
        }
    })
    
    pipeline.append({"$sort": {"totalAmount": -1}})

    try:
        results = list(transactions_collection.aggregate(pipeline))
        print(f"🔍 Found {len(results)} aggregated results for query: {pipeline}")
        return results
    except Exception as e:
        print(f"❌ Error fetching spending data: {e}")
        return None

# ------------------ database/connection.py ------------------
import os
from datetime import datetime, timedelta
import pymongo
import certifi
from dotenv import load_dotenv
from bson import ObjectId

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
    """Saves a new transaction document with the new keywords schema."""
    if transactions_collection is None:
        print("❌ Cannot save transaction, database not connected.")
        return None

    keywords = parsed_data.get('keywords', [])
    if not isinstance(keywords, list):
        keywords = [keywords]

    document = {
        "rawText": raw_text,
        "parsedData": {
            "amount": round(float(parsed_data.get("amount", 0.0)), 2),
            "currency": parsed_data.get("currency", "SGD"),
            "keywords": keywords
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

def _get_base_match_conditions(timeframe: str):
    """Helper function to build the base MongoDB match conditions with timeframe."""
    now = datetime.now()
    start_date = None
    if timeframe == 'day':
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif timeframe == 'week':
        start_date = now - timedelta(days=now.weekday())
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    elif timeframe == 'month':
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    match_conditions = {}
    if start_date:
        match_conditions["createdAt"] = {"$gte": start_date}
    return match_conditions

def _get_match_pipeline(timeframe: str, filter_type: str, filter_value: str):
    """Helper function to build the MongoDB $match pipeline."""
    match_conditions = _get_base_match_conditions(timeframe)
        
    if filter_type and filter_value and filter_value != 'none':
        # Create a case-insensitive regex for flexible matching
        regex_filter = {"$regex": f"{filter_value}", "$options": "i"}
        # --- FIX: Always search both category and keywords for any filter. ---
        # This makes the query robust against AI misclassifying a keyword as a category.
        match_conditions["$or"] = [
            {"category": regex_filter},
            {"parsedData.keywords": regex_filter}
        ]
    # If filter_type/filter_value is none or 'none', do not add $or filter at all
    
    return [{"$match": match_conditions}] if match_conditions else []

def _get_match_pipeline_all_keywords(timeframe: str, filter_value: str):
    """Builds a pipeline to match keywords in any position."""
    match_conditions = _get_base_match_conditions(timeframe)
    # Match if keyword is in any position
    regex_filter = {"$regex": f"{filter_value}", "$options": "i"}
    match_conditions["parsedData.keywords"] = regex_filter
    return [{"$match": match_conditions}]

def get_spending_data(timeframe: str = 'week', filter_type: str = None, filter_value: str = None):
    """Fetches transactions based on dynamic filters and aggregates them for summaries."""
    if transactions_collection is None: return None
    if filter_type == 'keywords' and filter_value and filter_value != 'none':
        pipeline = _get_match_pipeline_all_keywords(timeframe, filter_value)
        pipeline.extend([
            {"$unwind": "$parsedData.keywords"},
            {"$match": {"parsedData.keywords": {"$regex": f"{filter_value}", "$options": "i"}}},
            {"$group": {"_id": "$parsedData.keywords", "totalAmount": {"$sum": "$parsedData.amount"}, "count": {"$sum": 1}}},
            {"$sort": {"totalAmount": -1}}
        ])
    else:
        pipeline = _get_match_pipeline(timeframe, filter_type, filter_value)
        pipeline_for_list = list(pipeline)  # Copy for debugging
        pipeline.extend([
            {"$group": {"_id": None, "totalAmount": {"$sum": "$parsedData.amount"}, "count": {"$sum": 1}}}
        ])
    try:
        results = list(transactions_collection.aggregate(pipeline))
        print(f"🔍 Found {len(results)} aggregated results for query: {pipeline}")
        if results and isinstance(results, list):
            return {
                "total_amount": results[0].get("totalAmount", 0),
                "count": results[0].get("count", 0)
            }
        return {"total_amount": 0, "count": 0}
    except Exception as e:
        print(f"❌ Error fetching spending summary: {e}")
        return {"total_amount": 0, "count": 0}

def get_raw_transactions(timeframe: str = 'week', filter_type: str = None, filter_value: str = None):
    """Fetches a raw list of transactions based on dynamic filters."""
    if transactions_collection is None: return None
    if filter_type == 'keywords' and filter_value and filter_value != 'none':
        pipeline = _get_match_pipeline_all_keywords(timeframe, filter_value)
    else:
        pipeline = _get_match_pipeline(timeframe, filter_type, filter_value)
    pipeline.append({"$sort": {"createdAt": -1}}) # Sort by most recent
    try:
        results = list(transactions_collection.aggregate(pipeline))
        print(f"🔍 Found {len(results)} raw transactions for query: {pipeline}")
        return results
    except Exception as e:
        print(f"❌ Error fetching raw transactions: {e}")
        return None

def delete_transaction_by_id(transaction_id: str) -> bool:
    """Deletes a transaction by its MongoDB _id. Returns True if deleted, False otherwise."""
    if transactions_collection is None:
        print("❌ Cannot delete transaction, database not connected.")
        return False
    try:
        result = transactions_collection.delete_one({"_id": ObjectId(transaction_id)})
        if result.deleted_count == 1:
            print(f"🗑️ Transaction {transaction_id} deleted.")
            return True
        else:
            print(f"❌ Transaction {transaction_id} not found.")
            return False
    except Exception as e:
        print(f"❌ Error deleting transaction: {e}")
        return False

def update_transaction_keywords_by_id(transaction_id: str, new_keywords: list) -> bool:
    """Appends new keywords to the keywords array for a transaction by its _id. Returns True if updated, False otherwise."""
    if transactions_collection is None:
        print("❌ Cannot update transaction, database not connected.")
        return False
    try:
        result = transactions_collection.update_one(
            {"_id": ObjectId(transaction_id)},
            {"$addToSet": {"parsedData.keywords": {"$each": new_keywords}}}
        )
        if result.modified_count == 1:
            print(f"✅ Added keywords to transaction {transaction_id}: {new_keywords}")
            return True
        else:
            print(f"❌ Transaction {transaction_id} not found or no new keywords added.")
            return False
    except Exception as e:
        print(f"❌ Error updating transaction: {e}")
        return False

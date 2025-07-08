# app/services.py
import asyncio
import json
import re
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timedelta
from bson import ObjectId
from openai import AsyncOpenAI
import spacy
from PIL import Image, ImageEnhance
import pytesseract
import io
import os

from app.config import settings
from app.models import Transaction, Category, Currency, TransactionSource, TimeFrame, FilterType
from database import connection

# Windows Tesseract Configuration
if os.name == 'nt':
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# Common merchants for keyword matching
COMMON_MERCHANTS = [
    "7-eleven", "starbucks", "mcdonald's", "kfc", "fairprice", "ntuc", 
    "cold storage", "giant", "sheng siong", "guardian", "watsons", 
    "grab", "gojek", "foodpanda", "deliveroo", "koufu", "toast box",
    "ya kun kaya toast", "subway", "ikea", "daiso", "singtel", "starhub",
    "m1", "shopee", "lazada", "amazon"
]

class AIService:
    """Service for AI-related operations using OpenAI."""
    
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.categories = [cat.value for cat in Category]
    
    async def categorize_transaction(self, merchant: str, amount: float) -> Category:
        """Categorize a transaction based on merchant name and amount."""
        try:
            print(f"Categorizing transaction at '{merchant}' for ${amount:.2f}")
            
            response = await self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "system", 
                        "content": f"You are a helpful assistant that categorizes expenses. Respond with only one category from this list: {', '.join(self.categories)}"
                    },
                    {
                        "role": "user", 
                        "content": f"What is the category for a transaction of SGD {amount:.2f} at '{merchant}'?"
                    }
                ],
                temperature=0,
                max_tokens=20,
                timeout=10
            )
            
            category_name = response.choices[0].message.content.strip()
            print(f"AI suggested category: {category_name}")
            
            # Find the matching category enum
            for category in Category:
                if category.value == category_name:
                    return category
            
            return Category.OTHER
            
        except Exception as e:
            print(f"OpenAI categorization error: {e}")
            return Category.UNCATEGORIZED
    
    async def parse_recap_query(self, query_text: str) -> Dict[str, Any]:
        """Parse a natural language recap query into structured data."""
        try:
            parsing_prompt = (
                "You are a query parsing expert. Analyze the user's request and extract information into a JSON object. "
                "The JSON should have: 'action' ('summarize' or 'list'), 'timeframe' (day, today, week, this week, month, all), "
                "'filter_type' (category, keywords, none), and 'filter_value' (the specific name or 'none'). "
                "If the user asks to 'show', 'list', or 'see' transactions, the action is 'list'. Otherwise, it's 'summarize'. "
                
                "IMPORTANT: For filter_type classification:\n"
                "- Use 'category' ONLY for general spending categories like 'food', 'transport', 'shopping', 'entertainment', 'health', 'bills', 'groceries'\n"
                "- Use 'keywords' for ANY specific names, places, brands, merchants, or single words that are not general categories\n"
                "- Examples of keywords: 'starbucks', 'jem', 'ntuc', 'fairprice', 'grab', 'mala', 'coffee', 'lunch'\n"
                "- Examples of categories: 'food', 'transport', 'shopping', 'groceries', 'entertainment'\n"
                "- If unsure, default to 'keywords'\n\n"
                
                "If the user says 'today', set timeframe to 'day'. If 'this week' or 'week', set timeframe to 'week'. "
                "If 'this month' or 'month', set timeframe to 'month'. If 'all', set timeframe to 'all'.\n\n"
                f"User request: \"{query_text}\""
            )
            
            response = await self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": parsing_prompt}],
                response_format={"type": "json_object"}
            )
            
            parsed_query = json.loads(response.choices[0].message.content)
            print(f"AI parsed recap query: {parsed_query}")
            
            return parsed_query
            
        except Exception as e:
            print(f"OpenAI query parsing error: {e}")
            raise Exception(f"Failed to parse query: {e}")
    
    async def generate_summary(self, query_text: str, data: Dict[str, Any]) -> str:
        """Generate a natural language summary based on spending data."""
        try:
            summary_prompt = (
                "You are a smart financial assistant who says only necessary information. "
                "Based on the following JSON data, write a short, simple-easy-to-read summary. "
                "Address the user's original query directly. Mention the total amount and number of transactions if relevant.\n\n"
                f"User's Original Query: \"{query_text}\"\n"
                f"Data: {json.dumps(data)}"
            )
            
            response = await self.client.chat.completions.create(
                model="gpt-4o", 
                messages=[{"role": "user", "content": summary_prompt}], 
                temperature=0.7, 
                max_tokens=300
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            print(f"OpenAI summary generation error: {e}")
            raise Exception(f"Failed to generate summary: {e}")

class OCRService:
    """Service for OCR and text processing operations."""
    
    def __init__(self):
        # Load spaCy model for NLP
        try:
            self.nlp = spacy.load("en_core_web_sm")
            print("✅ spaCy model loaded.")
        except OSError:
            print("❌ spaCy model 'en_core_web_sm' not found. Please run: python -m spacy download en_core_web_sm")
            self.nlp = None
    
    def preprocess_image_for_ocr(self, image_bytes: bytes) -> Image.Image:
        """Preprocess image for better OCR results."""
        try:
            # Convert bytes to PIL Image
            image = Image.open(io.BytesIO(image_bytes))
            
            # Convert to grayscale
            if image.mode != 'L':
                image = image.convert('L')
            
            # Enhance contrast
            enhancer = ImageEnhance.Contrast(image)
            image = enhancer.enhance(2.0)
            
            # Enhance sharpness
            enhancer = ImageEnhance.Sharpness(image)
            image = enhancer.enhance(2.0)
            
            return image
            
        except Exception as e:
            print(f"Image preprocessing error: {e}")
            raise Exception(f"Failed to preprocess image: {e}")
    
    def extract_text_from_image(self, image_bytes: bytes) -> str:
        """Extract text from image using OCR."""
        try:
            processed_image = self.preprocess_image_for_ocr(image_bytes)
            custom_config = r'--oem 3 --psm 6'
            ocr_text = pytesseract.image_to_string(processed_image, config=custom_config)
            
            print(f"OCR Result:\n---\n{ocr_text}\n---")
            
            if not ocr_text.strip():
                raise Exception("No text found in image.")
            
            return ocr_text.strip()
            
        except Exception as e:
            print(f"OCR Error: {e}")
            raise Exception(f"Failed to extract text from image: {e}")
    
    def extract_transaction_details(self, text: str) -> Dict[str, Any]:
        """Extract transaction details from text."""
        try:
            parsed_data = {}
            text = text.strip()
            
            # 1. Amount Extraction
            amount_str = self._extract_amount(text)
            if amount_str:
                parsed_data["amount"] = round(float(amount_str), 2)
                parsed_data["currency"] = "SGD"
            else:
                raise Exception("Could not find amount in text.")
            
            # 2. Merchant/Keyword Extraction
            merchant = self._extract_merchant(text, amount_str)
            parsed_data["keywords"] = [merchant] if merchant != "Unknown" else []
            
            print(f"Extracted transaction details: {parsed_data}")
            return parsed_data
            
        except Exception as e:
            print(f"Transaction details extraction error: {e}")
            raise Exception(f"Failed to extract transaction details: {e}")
    
    def _extract_amount(self, text: str) -> str:
        """Extract amount from text."""
        # Try to find amount with currency symbol
        match_full = re.search(r"(?:SGD|S\$|\$)\s*(\d+(?:\.\d{1,2})?)", text, re.IGNORECASE)
        if match_full:
            return match_full.group(1)
        
        # Try to find any number (take the last one)
        found_numbers = re.findall(r"(\d+(?:\.\d{1,2})?)", text)
        if found_numbers:
            return found_numbers[-1]
        
        return None
    
    def _extract_merchant(self, text: str, amount_str: str) -> str:
        """Extract merchant name from text."""
        # Remove amount from text for merchant extraction
        text_for_merchant = text
        if amount_str:
            text_for_merchant = re.sub(r'\b' + re.escape(amount_str) + r'\b', '', text_for_merchant, 1).strip()
        
        if not text_for_merchant:
            return "Unknown"
        
        # Try to match known merchants
        lower_text = text_for_merchant.lower()
        for known_merchant in COMMON_MERCHANTS:
            if known_merchant in lower_text:
                return known_merchant.title()
        
        # Try to find capitalized words (likely merchant names)
        caps_match = re.search(r"\b([A-Z][A-Z\s]{2,})\b", text_for_merchant)
        if caps_match:
            return caps_match.group(1).strip()
        
        # Use NLP to find entities
        if self.nlp:
            doc = self.nlp(text_for_merchant)
            entities = [ent.text.strip() for ent in doc.ents if ent.label_ in ("ORG", "GPE", "PERSON")]
            if entities:
                return entities[0]
        
        # Fallback: find nouns
        noun_matches = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b", text_for_merchant)
        if noun_matches:
            return noun_matches[0]
        
        return "Unknown"
    
    async def process_image_transaction(self, image_bytes: bytes) -> Tuple[str, float]:
        """Process image and extract only the amount."""
        try:
            # Extract text from image
            ocr_text = self.extract_text_from_image(image_bytes)
            # Extract amount from text
            amount = self._extract_amount(ocr_text)
            if not amount:
                raise Exception("Could not extract amount from image")
            return ocr_text, float(amount)
        except Exception as e:
            print(f"Image transaction processing error: {e}")
            raise Exception(f"Failed to process image transaction: {e}")

class TransactionService:
    """Service for transaction business logic operations."""
    
    def __init__(self, ai_service: AIService, ocr_service: OCRService):
        self.ai_service = ai_service
        self.ocr_service = ocr_service
    
    async def create_transaction_from_text(self, raw_text: str, amount: float, 
                                         keywords: List[str], source: TransactionSource = TransactionSource.TEXT) -> Transaction:
        """Create a transaction from text input."""
        try:
            # Validate input
            if amount <= 0:
                raise Exception("Amount must be greater than 0")
            
            if not keywords:
                raise Exception("At least one keyword is required")
            
            # Categorize transaction using AI
            first_keyword = keywords[0]
            category = await self.ai_service.categorize_transaction(first_keyword, amount)
            
            # Create transaction
            transaction = Transaction(
                amount=amount,
                currency=Currency.SGD,
                keywords=keywords,
                category=category,
                raw_text=raw_text,
                source=source
            )
            
            # Save to database using existing connection schema
            doc = {
                "rawText": transaction.raw_text,
                "parsedData": {
                    "amount": transaction.amount,
                    "currency": transaction.currency.value,
                    "keywords": transaction.keywords
                },
                "source": transaction.source.value,
                "imageUrl": transaction.image_url,
                "category": transaction.category.value,
                "createdAt": transaction.created_at
            }
            result = connection.transactions_collection.insert_one(doc)
            transaction.id = str(result.inserted_id)
            
            print(f"Created transaction: {transaction.id}")
            return transaction
            
        except Exception as e:
            print(f"Failed to create transaction from text: {e}")
            raise Exception(f"Failed to create transaction: {e}")
    
    async def create_transaction_from_image(self, image_bytes: bytes) -> Transaction:
        """Create a transaction from image input."""
        try:
            # Process image to extract text and details
            ocr_text, amount = await self.ocr_service.process_image_transaction(image_bytes)
            
            # Create transaction using the extracted data
            return await self.create_transaction_from_text(
                raw_text=ocr_text,
                amount=amount,
                keywords=[],
                source=TransactionSource.IMAGE
            )
            
        except Exception as e:
            print(f"Failed to create transaction from image: {e}")
            raise Exception(f"Failed to create transaction from image: {e}")
    
    async def delete_transaction(self, transaction_id: str) -> bool:
        """Delete a transaction by ID."""
        try:
            # Delete transaction using existing connection
            result = connection.transactions_collection.delete_one({"_id": ObjectId(transaction_id)})
            success = result.deleted_count > 0
            
            if success:
                print(f"Deleted transaction: {transaction_id}")
            else:
                print(f"Failed to delete transaction: {transaction_id}")
            
            return success
            
        except Exception as e:
            print(f"Failed to delete transaction {transaction_id}: {e}")
            raise Exception(f"Failed to delete transaction: {e}")
    
    async def add_keywords_to_transaction(self, transaction_id: str, new_keywords: List[str]) -> Transaction:
        """Add keywords to an existing transaction."""
        try:
            # Validate input
            if not new_keywords:
                raise Exception("At least one keyword is required")
            
            # Add keywords using existing connection schema
            result = connection.transactions_collection.update_one(
                {"_id": ObjectId(transaction_id)},
                {"$addToSet": {"parsedData.keywords": {"$each": new_keywords}}}
            )
            
            if result.modified_count == 0:
                raise Exception("Failed to add keywords to transaction")
            
            # Get updated transaction
            doc = connection.transactions_collection.find_one({"_id": ObjectId(transaction_id)})
            if not doc:
                raise Exception("Transaction not found")
            
            transaction = Transaction.from_dict(doc)
            print(f"Added keywords to transaction {transaction_id}: {new_keywords}")
            return transaction
            
        except Exception as e:
            print(f"Failed to add keywords to transaction {transaction_id}: {e}")
            raise Exception(f"Failed to add keywords: {e}")
    
    async def get_spending_summary(self, timeframe: TimeFrame, 
                                 filter_type: Optional[FilterType] = None,
                                 filter_value: Optional[str] = None) -> Dict[str, Any]:
        """Get spending summary for a given timeframe and filters."""
        try:
            # Use existing connection function
            spending_data = connection.get_spending_data(
                timeframe.value, 
                filter_type.value if filter_type else None, 
                filter_value
            )
            return spending_data or {"total_amount": 0, "count": 0}
        except Exception as e:
            print(f"Failed to get spending summary: {e}")
            raise Exception(f"Failed to get spending summary: {e}")
    
    async def get_transactions_by_timeframe(self, timeframe: TimeFrame,
                                          filter_type: Optional[FilterType] = None,
                                          filter_value: Optional[str] = None) -> List[Transaction]:
        """Get transactions for a given timeframe and filters."""
        try:
            # Use existing connection function
            raw_transactions = connection.get_raw_transactions(
                timeframe.value,
                filter_type.value if filter_type else None,
                filter_value
            )
            
            if not raw_transactions:
                return []
            
            # Convert to Transaction objects
            transactions = []
            for doc in raw_transactions:
                transaction = Transaction.from_dict(doc)
                transactions.append(transaction)
            
            return transactions
        except Exception as e:
            print(f"Failed to get transactions by timeframe: {e}")
            raise Exception(f"Failed to get transactions: {e}")

class AnalyticsService:
    """Service for spending analytics and reporting."""
    
    def __init__(self, transaction_service: TransactionService, ai_service: AIService):
        self.transaction_service = transaction_service
        self.ai_service = ai_service
    
    async def analyze_spending_query(self, query_text: str) -> Dict[str, Any]:
        """Analyze a natural language spending query and return results."""
        try:
            # Parse the query using AI
            parsed_query = await self.ai_service.parse_recap_query(query_text)
            
            # Extract parameters
            action = parsed_query.get('action', 'summarize')
            timeframe_str = parsed_query.get('timeframe', 'week').lower()
            filter_type_str = parsed_query.get('filter_type', 'none')
            filter_value = parsed_query.get('filter_value', 'none')
            
            # Force filter_type to 'keywords' for single-word or 'at X' queries
            import re
            single_word = bool(re.fullmatch(r'\w+', query_text.strip()))
            at_x = bool(re.search(r'\bat\s+\w+', query_text.strip(), re.IGNORECASE))
            
            if filter_type_str == 'keywords' or single_word or at_x:
                # All keyword queries: search all keywords (both first and secondary)
                filter_type = FilterType.KEYWORDS  # Use explicit keyword search
            else:
                filter_type = self._normalize_filter_type(filter_type_str)
            timeframe = self._normalize_timeframe(timeframe_str)
            
            # Get data based on action
            if action == 'list':
                transactions = await self.transaction_service.get_transactions_by_timeframe(
                    timeframe, filter_type, filter_value
                )
                return {
                    'action': 'list',
                    'transactions': transactions,
                    'total_amount': sum(t.amount for t in transactions),
                    'count': len(transactions)
                }
            else:  # summarize
                summary = await self.transaction_service.get_spending_summary(
                    timeframe, filter_type, filter_value
                )
                return {
                    'action': 'summarize',
                    'summary': summary,
                    'timeframe': timeframe,
                    'filter_type': filter_type,
                    'filter_value': filter_value
                }
                
        except Exception as e:
            print(f"Failed to analyze spending query: {e}")
            raise Exception(f"Failed to analyze spending query: {e}")
    
    async def generate_spending_report(self, query_text: str) -> str:
        """Generate a natural language spending report."""
        try:
            # Analyze the query
            analysis = await self.analyze_spending_query(query_text)
            
            if analysis['action'] == 'list':
                # Generate list report
                transactions = analysis['transactions']
                total_amount = analysis['total_amount']
                count = analysis['count']
                
                if not transactions:
                    return "I couldn't find any matching transactions for your request."
                
                report = f"📋 <b>Transactions for '{query_text}':</b>\n\n"
                for tx in transactions:
                    date_str = tx.created_at.strftime('%d %b %I:%M %p')  # Add time with AM/PM
                    keywords = ", ".join(tx.keywords) if tx.keywords else "No keywords"
                    category = tx.category.value
                    report += f"🗓️ <b>{date_str}</b>\n"
                    report += f"💰 <b>SGD {tx.amount:.2f}</b>\n"
                    report += f"🏷️ {keywords}\n"
                    report += f"📂 {category}\n\n"
                
                report += f"📊 <b>Summary:</b>\n"
                report += f"• Total Amount: <b>SGD {total_amount:.2f}</b>\n"
                report += f"• Number of Transactions: <b>{count}</b>\n"
                report += f"• Average per Transaction: <b>SGD {total_amount/count:.2f}</b>" if count > 0 else "• No transactions found"
                return report
            else:
                # Generate summary report using AI
                summary_data = analysis['summary']
                return await self.ai_service.generate_summary(query_text, summary_data)
                
        except Exception as e:
            print(f"Failed to generate spending report: {e}")
            raise Exception(f"Failed to generate spending report: {e}")
    
    def _normalize_timeframe(self, timeframe_str: str) -> TimeFrame:
        """Normalize timeframe string to TimeFrame enum."""
        timeframe_map = {
            'today': TimeFrame.DAY,
            'day': TimeFrame.DAY,
            'this week': TimeFrame.WEEK,
            'week': TimeFrame.WEEK,
            'this month': TimeFrame.MONTH,
            'month': TimeFrame.MONTH,
            'all': TimeFrame.ALL
        }
        return timeframe_map.get(timeframe_str.lower(), TimeFrame.WEEK)
    
    def _normalize_filter_type(self, filter_type_str: str) -> Optional[FilterType]:
        """Normalize filter type string to FilterType enum."""
        if filter_type_str.lower() in ['category', 'keywords']:
            return FilterType(filter_type_str.lower())
        return FilterType.NONE 
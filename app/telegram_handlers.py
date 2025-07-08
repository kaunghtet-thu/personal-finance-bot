# app_simple/telegram_handlers.py
import logging
from typing import Dict, Any, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

from app.config import settings
from app.services import AIService, OCRService, TransactionService, AnalyticsService
from app.models import Category, TransactionSource

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
WAITING_FOR_AMOUNT, WAITING_FOR_KEYWORDS, WAITING_FOR_CONFIRMATION = range(3)

class TelegramHandlers:
    """Simplified Telegram bot handlers."""
    
    def __init__(self):
        # Initialize services
        self.ai_service = AIService()
        self.ocr_service = OCRService()
        self.transaction_service = TransactionService(self.ai_service, self.ocr_service)
        self.analytics_service = AnalyticsService(self.transaction_service, self.ai_service)
        
        # Store temporary data during conversation
        self.temp_data: Dict[int, Dict[str, Any]] = {}
    
    def _is_authorized(self, user_id: int) -> bool:
        """Check if user is authorized."""
        if not settings.allowed_user_ids:
            return True  # Allow all if no restrictions
        return user_id in settings.allowed_user_ids
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        user_id = update.effective_user.id
        
        if not self._is_authorized(user_id):
            await update.message.reply_text("❌ You are not authorized to use this bot.")
            return
        
        welcome_message = (
            "🤖 Welcome to your Personal Finance Bot!\n\n"
            "I can help you track your expenses. Here's what I can do:\n\n"
            "📝 <b>Record Transactions</b>\n"
            "• Send me a message like: \"$5.50 coffee at Starbucks\"\n"
            "• Or send me a photo of a receipt\n\n"
            "📊 <b>View Spending</b>\n"
            "• Ask me: \"How much did I spend this week?\"\n"
            "• Or: \"Show me my food expenses this month\"\n\n"
            "💡 <b>Examples</b>\n"
            "• \"$12.80 lunch at Koufu\"\n"
            "• \"$25.50 groceries at NTUC\"\n"
            "• \"How much did I spend on transport this week?\"\n\n"
            "Just send me a message to get started!"
        )
        
        await update.message.reply_text(welcome_message, parse_mode=ParseMode.HTML)
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
        help_message = (
            "📚 <b>How to use this bot:</b>\n\n"
            "💳 <b>Recording Transactions:</b>\n"
            "• Text format: \"$amount description at merchant\"\n"
            "• Photo: Send a receipt image\n\n"
            "📊 <b>Viewing Spending:</b>\n"
            "• \"How much did I spend today?\"\n"
            "• \"Show my food expenses this week\"\n"
            "• \"What did I spend on transport this month?\"\n\n"
            "🔧 <b>Commands:</b>\n"
            "/start - Welcome message\n"
            "/help - This help message\n"
            "/cancel - Cancel current operation\n\n"
            "💡 <b>Tips:</b>\n"
            "• Be specific with amounts and merchants\n"
            "• Use natural language for queries\n"
            "• Photos work best with clear text"
        )
        
        await update.message.reply_text(help_message, parse_mode=ParseMode.HTML)
    
    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle /cancel command."""
        user_id = update.effective_user.id
        
        # Clear temporary data
        if user_id in self.temp_data:
            del self.temp_data[user_id]
        
        await update.message.reply_text(
            "❌ Operation cancelled. You can start over by sending me a transaction or asking about your spending."
        )
        
        return ConversationHandler.END
    
    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle text messages - either transaction recording or spending queries."""
        user_id = update.effective_user.id
        text = update.message.text.strip()
        
        if not self._is_authorized(user_id):
            await update.message.reply_text("❌ You are not authorized to use this bot.")
            return ConversationHandler.END
        
        # Check if user is in keywords input state
        if user_id in self.temp_data and 'transaction_id' in self.temp_data[user_id]:
            return await self.handle_keywords_input(update, context)
        
        # Check if this looks like a transaction
        if self._looks_like_transaction(text):
            return await self._handle_transaction_recording(update, context, text)
        else:
            return await self._handle_spending_query(update, context, text)
    
    def _looks_like_transaction(self, text: str) -> bool:
        """Check if text looks like a transaction."""
        import re
        
        # Look for amount patterns
        amount_patterns = [
            r'\$\d+(?:\.\d{1,2})?',  # $5.50
            r'SGD\s*\d+(?:\.\d{1,2})?',  # SGD 5.50
            r'\d+(?:\.\d{1,2})?\s*(?:dollars?|bucks?)',  # 5.50 dollars
        ]
        
        for pattern in amount_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        
        return False
    
    async def _handle_transaction_recording(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> int:
        """Handle transaction recording flow."""
        user_id = update.effective_user.id
        
        try:
            # Extract amount and keywords from text
            amount, keywords = self._parse_transaction_text(text)
            
            if not amount or amount <= 0:
                await update.message.reply_text(
                    "❌ I couldn't find a valid amount in your message. "
                    "Please use format: \"$5.50 coffee at Starbucks\""
                )
                return ConversationHandler.END
            
            if not keywords:
                await update.message.reply_text(
                    "❌ I couldn't find any keywords/merchant in your message. "
                    "Please include what you bought and where."
                )
                return ConversationHandler.END
            
            # Store temporary data
            self.temp_data[user_id] = {
                'amount': amount,
                'keywords': keywords,
                'raw_text': text
            }
            
            # Show confirmation
            confirmation_text = (
                f"📝 <b>Transaction Details:</b>\n\n"
                f"💰 Amount: <b>SGD {amount:.2f}</b>\n"
                f"🏷️ Keywords: {', '.join(keywords)}\n\n"
                f"Is this correct? I'll categorize it automatically."
            )
            
            keyboard = [
                [
                    InlineKeyboardButton("✅ Confirm", callback_data="confirm_transaction"),
                    InlineKeyboardButton("❌ Cancel", callback_data="cancel_transaction")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                confirmation_text, 
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup
            )
            
            return WAITING_FOR_CONFIRMATION
            
        except Exception as e:
            logger.error(f"Error handling transaction recording: {e}")
            await update.message.reply_text(
                "❌ Sorry, I couldn't process your transaction. "
                "Please try again with format: \"$5.50 coffee at Starbucks\""
            )
            return ConversationHandler.END
    
    def _parse_transaction_text(self, text: str) -> tuple[float, list[str]]:
        """Parse transaction text to extract amount and keywords."""
        import re
        
        # Extract amount
        amount = None
        
        # Try different amount patterns
        amount_patterns = [
            r'\$(\d+(?:\.\d{1,2})?)',  # $5.50
            r'SGD\s*(\d+(?:\.\d{1,2})?)',  # SGD 5.50
            r'(\d+(?:\.\d{1,2})?)\s*(?:dollars?|bucks?)',  # 5.50 dollars
        ]
        
        for pattern in amount_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                amount = float(match.group(1))
                break
        
        if not amount:
            raise ValueError("No amount found")
        
        # Extract keywords (remove amount and common words)
        # Remove amount from text
        text_without_amount = re.sub(r'\$\d+(?:\.\d{1,2})?', '', text, flags=re.IGNORECASE)
        text_without_amount = re.sub(r'SGD\s*\d+(?:\.\d{1,2})?', '', text_without_amount, flags=re.IGNORECASE)
        
        # Split into words and filter
        words = text_without_amount.split()
        keywords = []
        
        # Common words to exclude
        exclude_words = {
            'at', 'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'to', 'for', 'of', 'with',
            'by', 'from', 'up', 'about', 'into', 'through', 'during', 'before', 'after',
            'above', 'below', 'between', 'among', 'bought', 'purchased', 'spent', 'paid'
        }
        
        for word in words:
            word = word.strip('.,!?').lower()
            if word and word not in exclude_words and len(word) > 1:
                keywords.append(word)
        
        return amount, keywords
    
    async def _handle_spending_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> int:
        """Handle spending query."""
        try:
            await update.message.reply_text("🔍 Analyzing your spending query...")
            
            # Generate report
            report = await self.analytics_service.generate_spending_report(text)
            
            await update.message.reply_text(report, parse_mode=ParseMode.HTML)
            
        except Exception as e:
            logger.error(f"Error handling spending query: {e}")
            await update.message.reply_text(
                "❌ Sorry, I couldn't process your spending query. "
                "Try asking something like \"How much did I spend this week?\""
            )
        
        return ConversationHandler.END
    
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle photo messages (receipts)."""
        user_id = update.effective_user.id
        
        if not self._is_authorized(user_id):
            await update.message.reply_text("❌ You are not authorized to use this bot.")
            return ConversationHandler.END
        
        try:
            await update.message.reply_text("📸 Processing your receipt...")
            
            # Get the largest photo
            photo = update.message.photo[-1]
            
            # Download photo
            file = await context.bot.get_file(photo.file_id)
            image_bytes = await file.download_as_bytearray()
            
            # Process image to extract only amount
            ocr_text, amount = await self.ocr_service.process_image_transaction(image_bytes)
            if not amount:
                await update.message.reply_text("❌ Could not extract amount from the receipt. Please try again.")
                return ConversationHandler.END
            
            # Store amount and raw text, wait for keywords
            self.temp_data[user_id] = {
                'amount': amount,
                'raw_text': ocr_text,
                'keywords': []
            }
            
            await update.message.reply_text(
                f"💰 Detected amount: <b>SGD {amount:.2f}</b>\n\n"
                "Please enter keywords for this transaction (e.g. merchant, place, tags):\n"
                "Example: Starbucks, Jem, coffee",
                parse_mode=ParseMode.HTML
            )
            return WAITING_FOR_KEYWORDS
        except Exception as e:
            logger.error(f"Error handling photo: {e}")
            await update.message.reply_text(
                "❌ Sorry, I couldn't process your receipt. "
                "Please make sure the text is clear and try again."
            )
        
        return ConversationHandler.END
    
    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle callback queries from inline buttons."""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        
        if not self._is_authorized(user_id):
            await query.edit_message_text("❌ You are not authorized to use this bot.")
            return ConversationHandler.END
        
        callback_data = query.data
        
        try:
            if callback_data == "confirm_transaction":
                return await self._handle_confirm_transaction(update, context)
            elif callback_data == "cancel_transaction":
                return await self._handle_cancel_transaction(update, context)
            elif callback_data.startswith("delete_transaction:"):
                return await self._handle_delete_transaction(update, context, callback_data)
            elif callback_data.startswith("add_keywords:"):
                return await self._handle_add_keywords(update, context, callback_data)
            else:
                await query.edit_message_text("❌ Unknown action.")
                return ConversationHandler.END
                
        except Exception as e:
            logger.error(f"Error handling callback query: {e}")
            await query.edit_message_text("❌ An error occurred. Please try again.")
            return ConversationHandler.END
    
    async def _handle_confirm_transaction(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle transaction confirmation."""
        query = update.callback_query
        user_id = query.from_user.id
        
        if user_id not in self.temp_data:
            await query.answer("❌ No transaction data found. Please start over.")
            return ConversationHandler.END
        
        try:
            data = self.temp_data[user_id]
            
            # Create transaction
            transaction = await self.transaction_service.create_transaction_from_text(
                raw_text=data['raw_text'],
                amount=data['amount'],
                keywords=data['keywords']
            )
            
            # Show success message with action buttons
            time_str = transaction.created_at.strftime('%d %b %I:%M %p')
            success_text = (
                f"✅ <b>Transaction Recorded!</b>\n\n"
                f"🗓️ <b>{time_str}</b>\n"
                f"💰 Amount: <b>SGD {transaction.amount:.2f}</b>\n"
                f"🏷️ Keywords: {', '.join(transaction.keywords)}\n"
                f"📂 Category: {transaction.category.value}\n\n"
                f"What would you like to do?"
            )
            
            keyboard = [
                [
                    InlineKeyboardButton("🗑️ Delete Transaction", 
                                       callback_data=f"delete_transaction:{transaction.id}"),
                    InlineKeyboardButton("➕ Add Keywords", 
                                       callback_data=f"add_keywords:{transaction.id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.message.reply_text(
                success_text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup
            )
            
            # Clear temporary data
            del self.temp_data[user_id]
            
        except Exception as e:
            logger.error(f"Error confirming transaction: {e}")
            await query.answer("❌ Failed to record transaction. Please try again.")
        
        return ConversationHandler.END
    
    async def _handle_cancel_transaction(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle transaction cancellation."""
        query = update.callback_query
        user_id = query.from_user.id
        
        # Clear temporary data
        if user_id in self.temp_data:
            del self.temp_data[user_id]
        
        await query.message.reply_text("❌ Transaction cancelled. Send me another transaction when ready!")
        return ConversationHandler.END
    
    async def _handle_delete_transaction(self, update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data: str) -> int:
        """Handle transaction deletion."""
        query = update.callback_query
        
        try:
            transaction_id = callback_data.split(":")[1]
            
            # Delete transaction
            success = await self.transaction_service.delete_transaction(transaction_id)
            
            if success:
                await query.message.reply_text("🗑️ Transaction deleted successfully!")
            else:
                await query.message.reply_text("❌ Failed to delete transaction.")
            
        except Exception as e:
            logger.error(f"Error deleting transaction: {e}")
            await query.message.reply_text("❌ Failed to delete transaction.")
        
        return ConversationHandler.END
    
    async def _handle_add_keywords(self, update: Update, context: ContextTypes.DEFAULT_TYPE, callback_data: str) -> int:
        """Handle adding keywords to transaction."""
        query = update.callback_query
        
        try:
            transaction_id = callback_data.split(":")[1]
            
            # Store transaction ID for keyword addition
            user_id = query.from_user.id
            self.temp_data[user_id] = {'transaction_id': transaction_id}
            
            await query.message.reply_text(
                "➕ Please send me the keywords you'd like to add to this transaction.\n\n"
                "Example: \"coffee, breakfast, morning\""
            )
            
            return WAITING_FOR_KEYWORDS
            
        except Exception as e:
            logger.error(f"Error handling add keywords: {e}")
            await query.message.reply_text("❌ Failed to process request.")
            return ConversationHandler.END
    
    async def handle_keywords_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle keywords input for adding to transaction or after receipt."""
        user_id = update.effective_user.id
        text = update.message.text.strip()
        
        if user_id not in self.temp_data:
            await update.message.reply_text("❌ No transaction found. Please start over.")
            return ConversationHandler.END
        
        try:
            # If this is after a receipt, use stored amount and raw_text
            data = self.temp_data[user_id]
            if 'amount' in data and 'raw_text' in data:
                keywords = [kw.strip() for kw in text.split(',') if kw.strip()]
                if not keywords:
                    await update.message.reply_text("❌ Please provide at least one keyword.")
                    return WAITING_FOR_KEYWORDS
                # Create transaction
                transaction = await self.transaction_service.create_transaction_from_text(
                    raw_text=data['raw_text'],
                    amount=data['amount'],
                    keywords=keywords,
                    source=TransactionSource.IMAGE
                )
                # Show success message with action buttons
                time_str = transaction.created_at.strftime('%d %b %I:%M %p')
                success_text = (
                    f"✅ <b>Transaction Recorded!</b>\n\n"
                    f"🗓️ <b>{time_str}</b>\n"
                    f"💰 Amount: <b>SGD {transaction.amount:.2f}</b>\n"
                    f"🏷️ Keywords: {', '.join(transaction.keywords)}\n"
                    f"📂 Category: {transaction.category.value}\n\n"
                    f"What would you like to do?"
                )
                keyboard = [
                    [
                        InlineKeyboardButton("🗑️ Delete Transaction", 
                                           callback_data=f"delete_transaction:{transaction.id}"),
                        InlineKeyboardButton("➕ Add Keywords", 
                                           callback_data=f"add_keywords:{transaction.id}")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(success_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
                del self.temp_data[user_id]
                return ConversationHandler.END
            # Otherwise, this is the add keywords flow for an existing transaction
            transaction_id = data.get('transaction_id')
            if transaction_id:
                keywords = [kw.strip() for kw in text.split(',') if kw.strip()]
                if not keywords:
                    await update.message.reply_text("❌ Please provide at least one keyword.")
                    return WAITING_FOR_KEYWORDS
                transaction = await self.transaction_service.add_keywords_to_transaction(transaction_id, keywords)
                success_text = (
                    f"✅ <b>Keywords Added!</b>\n\n"
                    f"💰 Amount: <b>SGD {transaction.amount:.2f}</b>\n"
                    f"🏷️ Keywords: {', '.join(transaction.keywords)}\n"
                    f"📂 Category: {transaction.category.value}\n\n"
                    f"Transaction updated successfully!"
                )
                await update.message.reply_text(success_text, parse_mode=ParseMode.HTML)
                del self.temp_data[user_id]
                return ConversationHandler.END
        except Exception as e:
            logger.error(f"Error adding keywords: {e}")
            await update.message.reply_text("❌ Failed to add keywords. Please try again.")
        
        return ConversationHandler.END 
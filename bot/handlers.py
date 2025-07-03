# ------------------ bot/handlers.py ------------------
import os
import json
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode
from openai import AsyncOpenAI

from . import processing
from database import connection

# Load environment variables
load_dotenv()
ALLOWED_USER_IDS = [int(uid.strip()) for uid in os.getenv("ALLOWED_USER_IDS", "").split(',') if uid]
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- Define states for conversations ---
MAIN_MENU, GETTING_TRANSACTION_DETAILS, GETTING_MERCHANT, GETTING_RECAP_QUERY = range(4)

# --- Security Decorator ---
def restricted(func):
    """Decorator to restrict access to allowed user IDs."""
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user or (ALLOWED_USER_IDS and user.id not in ALLOWED_USER_IDS):
            print(f"⚠️ Unauthorized access denied for {user.id if user else 'Unknown User'}.")
            if update.callback_query:
                await update.callback_query.answer("Sorry, you are not authorized.", show_alert=True)
            elif update.message:
                await update.message.reply_text("Sorry, you are not authorized to use this bot.")
            return ConversationHandler.END
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- Main Menu ---
@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the main menu and sets the state."""
    keyboard = [
        [InlineKeyboardButton("✍️ Record Transaction", callback_data="record_transaction")],
        [InlineKeyboardButton("📊 View Recap", callback_data="view_recap")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # If the function is called via a command, send a new message.
    # If it's called via a button press (from another part of the conversation), edit the existing message.
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "Main Menu:",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_html(
            f"👋 Hello {update.effective_user.first_name}! What would you like to do?",
            reply_markup=reply_markup
        )
    return MAIN_MENU

# --- Transaction Recording Flow ---
@restricted
async def start_transaction_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks the user for transaction details."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        text="Please send me the transaction details.\n\n(e.g., `caifan 8.50`, just `12`, or a photo of a receipt)\n\nOr use /cancel to return to the main menu."
    )
    return GETTING_TRANSACTION_DETAILS

async def handle_transaction_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles text or photo, then asks for merchant if needed."""
    if update.message.text:
        text = update.message.text
        parsed_data = await processing.extract_from_text(text, with_category=False)
        raw_text = text
        source = 'text'
    elif update.message.photo:
        photo_file = await update.message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
        raw_text, parsed_data = await processing.extract_from_image(image_bytes, with_category=False)
        source = 'image'
    else:
        await update.message.reply_text("Sorry, I only understand text and photos. Please try again or use /cancel.")
        return GETTING_TRANSACTION_DETAILS

    if not parsed_data.get("amount"):
        await update.message.reply_text("I couldn't find an amount. Please try again or use /cancel.")
        return GETTING_TRANSACTION_DETAILS

    if parsed_data.get("merchant", "Unknown").lower() == "unknown":
        context.user_data['transaction'] = {'parsed_data': parsed_data, 'raw_text': raw_text, 'source': source}
        await update.message.reply_text(f"I see a transaction of SGD {parsed_data['amount']:.2f}. What was the merchant?")
        return GETTING_MERCHANT
    else:
        await save_and_confirm_transaction(update, context, parsed_data, raw_text, source)
        return await start(update, context) # Return to main menu

async def received_merchant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the user's reply with the merchant name."""
    merchant_name = update.message.text
    transaction_info = context.user_data.get('transaction')
    if not transaction_info:
        await update.message.reply_text("Sorry, something went wrong. Please start over with /start.")
        return ConversationHandler.END
        
    transaction_info['parsed_data']['merchant'] = merchant_name
    await save_and_confirm_transaction(update, context, transaction_info['parsed_data'], transaction_info['raw_text'], transaction_info['source'])
    context.user_data.clear()
    return await start(update, context) # Return to main menu

# --- Recap Flow ---
@restricted
async def start_recap_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks the user what they want a recap of."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        text="What would you like a recap of?\n\n(e.g., `this week`, `food last month`, `Starbucks spendings`)\n\nOr use /cancel to return to the main menu."
    )
    return GETTING_RECAP_QUERY

async def handle_recap_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Parses the user's recap query with AI, fetches data, and generates a summary."""
    query_text = update.message.text
    await update.message.reply_text(f"Got it! Analyzing your request: \"{query_text}\"...")

    try:
        parsing_prompt = (
            "You are a query parsing expert. Analyze the user's request and extract the following information into a JSON object: "
            "'timeframe' (day, week, month, all), 'filter_type' (category, merchant, none), and 'filter_value' (the specific name or 'none'). "
            "Default timeframe is 'week'. If you can't determine a filter, use 'none'.\n\n"
            f"User request: \"{query_text}\""
        )
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": parsing_prompt}],
            response_format={"type": "json_object"}
        )
        parsed_query = json.loads(response.choices[0].message.content)
        print(f"🧠 AI parsed recap query: {parsed_query}")

        timeframe = parsed_query.get('timeframe', 'week')
        filter_type = parsed_query.get('filter_type', 'none')
        filter_value = parsed_query.get('filter_value', 'none')
        
        spending_data = connection.get_spending_data(
            timeframe, 
            None if filter_type == 'none' else filter_type, 
            None if filter_value == 'none' else filter_value
        )

        if not spending_data:
            await update.message.reply_text("I couldn't find any matching spending data for your request.")
            return await start(update, context) # Return to main menu

        summary_prompt = (
            "You are a friendly financial assistant. Based on the following JSON data, write a short, easy-to-read summary. "
            "Address the user's original query directly. Mention the total amount and number of transactions if relevant. "
            "Use bullet points or emojis for readability.\n\n"
            f"User's Original Query: \"{query_text}\"\n"
            f"Data: {json.dumps(spending_data)}"
        )
        summary_response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": summary_prompt}],
            temperature=0.7,
            max_tokens=300
        )
        await update.message.reply_text(summary_response.choices[0].message.content)

    except Exception as e:
        print(f"❌ AI recap error: {e}")
        await update.message.reply_text("Sorry, I had trouble understanding or processing your recap request. Please try rephrasing it.")

    return await start(update, context) # Return to main menu

# --- Utility Functions ---
async def save_and_confirm_transaction(update: Update, context: ContextTypes.DEFAULT_TYPE, parsed_data: dict, raw_text: str, source: str):
    """Finalizes the transaction, saves it, and sends a confirmation."""
    if parsed_data.get("amount") and parsed_data.get("merchant") != "Unknown":
        parsed_data["category"] = await processing.get_category_from_openai(parsed_data["merchant"], parsed_data["amount"])
    else:
        parsed_data["category"] = "Uncategorized"
    connection.save_transaction(raw_text=raw_text, parsed_data=parsed_data, source=source)
    
    confirmation_message = (
        "✅ Transaction logged!\n"
        f"<b>Amount:</b> {parsed_data.get('amount')} {parsed_data.get('currency', 'SGD')}\n"
        f"<b>Merchant:</b> {parsed_data.get('merchant', 'Unknown')}\n"
        f"<b>Category:</b> {parsed_data.get('category', 'Uncategorized')}"
    )
    if parsed_data.get('category') == 'Uncategorized' and parsed_data.get('merchant') != 'Unknown':
        confirmation_message += " 🤖 (AI categorization failed or was skipped)"
    else:
        confirmation_message += " 🤖"
    
    await context.bot.send_message(chat_id=update.effective_chat.id, text=confirmation_message, parse_mode=ParseMode.HTML)

@restricted
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels the current operation and shows the main menu."""
    await update.message.reply_text("Operation cancelled.")
    return await start(update, context)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log Errors caused by Updates."""
    print(f"❌ An error occurred: {context.error}")

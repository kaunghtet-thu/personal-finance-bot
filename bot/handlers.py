# ------------------ bot/handlers.py ------------------
import os
import json
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CallbackQueryHandler
from telegram.constants import ParseMode
from openai import AsyncOpenAI

from . import processing
from database import connection

# Load environment variables
load_dotenv()
ALLOWED_USER_IDS = [int(uid.strip()) for uid in os.getenv("ALLOWED_USER_IDS", "").split(',') if uid]
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- Define states for conversations ---
MAIN_MENU, GETTING_TRANSACTION_DETAILS, GETTING_MERCHANT, GETTING_KEYWORDS = range(4)
GETTING_RECAP_QUERY = 4 # Shared state number, but different conversation

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
    context.user_data.clear()
    
    message_text = f"👋 Hello {update.effective_user.first_name}! What would you like to do?"
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Main Menu:", reply_markup=reply_markup)
    else:
        await update.message.reply_html(message_text, reply_markup=reply_markup)
        
    return MAIN_MENU

# --- Transaction Recording Flow ---
@restricted
async def start_transaction_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks the user for transaction details."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="Please send me the transaction details.\n\n(e.g., `caifan 8.50`, just `12`, or a photo of a receipt)")
    return GETTING_TRANSACTION_DETAILS

async def handle_transaction_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles text or photo, then asks for merchant if needed."""
    if update.message.text:
        text = update.message.text
        parsed_data = await processing.extract_initial_details(text)
        raw_text = text
        source = 'text'
    elif update.message.photo:
        photo_file = await update.message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
        raw_text, parsed_data = await processing.extract_from_image(image_bytes)
        source = 'image'
    else:
        await update.message.reply_text("Sorry, I only understand text and photos.")
        return GETTING_TRANSACTION_DETAILS

    if not parsed_data.get("amount"):
        await update.message.reply_text("I couldn't find an amount. Please try again.")
        return GETTING_TRANSACTION_DETAILS

    context.user_data['transaction'] = {'parsed_data': parsed_data, 'raw_text': raw_text, 'source': source}

    if not parsed_data.get("keywords"):
        await update.message.reply_text(f"I see a transaction of SGD {parsed_data['amount']:.2f}. What was the merchant/main keyword?")
        return GETTING_MERCHANT
    else:
        return await ask_for_more_keywords(update, context)

async def received_merchant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the user's reply with the merchant name, which becomes the first keyword."""
    merchant_name = update.message.text.strip().title()
    transaction_info = context.user_data.get('transaction')
    if not transaction_info:
        await update.message.reply_text("Sorry, something went wrong. Please start over with /start.")
        return ConversationHandler.END
        
    transaction_info['parsed_data']['keywords'] = [merchant_name]
    return await ask_for_more_keywords(update, context)

async def ask_for_more_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks the user if they want to add more keywords."""
    transaction_info = context.user_data.get('transaction')
    first_keyword = transaction_info['parsed_data']['keywords'][0]
    amount = transaction_info['parsed_data']['amount']
    
    category = await processing.get_category_from_openai(first_keyword, amount)
    transaction_info['parsed_data']['category'] = category

    keyboard = [[InlineKeyboardButton("Skip", callback_data="skip_keywords")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    sent_message = await update.message.reply_html(
        f"Logged: SGD {amount:.2f} at <b>{first_keyword}</b> (Category: {category} 🤖)\n\n"
        "Do you want to add more keywords or tags? (e.g., `lunch, project meeting`). "
        "Send your keywords or press Skip.",
        reply_markup=reply_markup
    )
    context.user_data['prompt_message_id'] = sent_message.message_id
    return GETTING_KEYWORDS

async def received_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receives additional keywords and finalizes the transaction."""
    transaction_info = context.user_data.get('transaction')
    if not transaction_info:
        await update.message.reply_text("Sorry, something went wrong. Please start over with /start.")
        return ConversationHandler.END

    new_keywords = [kw.strip().lower() for kw in update.message.text.split(',')]
    transaction_info['parsed_data']['keywords'].extend(new_keywords)
    
    prompt_message_id = context.user_data.get('prompt_message_id')
    if prompt_message_id:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=update.effective_chat.id,
                message_id=prompt_message_id,
                reply_markup=None
            )
        except Exception as e:
            print(f"Could not edit message reply markup: {e}")
    
    await save_and_confirm_transaction(update, context, transaction_info)
    return await start(update, context)

async def skip_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Skips adding extra keywords and finalizes the transaction."""
    query = update.callback_query
    await query.answer()

    transaction_info = context.user_data.get('transaction')
    if not transaction_info:
        print("No transaction info found. Restarting flow.")
        return await start(update, context)

    # Just skip adding more keywords and finalize the transaction as is
    # Save and send confirmation, then show main menu as a new message (not edit)
    await save_and_confirm_transaction(update, context, transaction_info)

    # Always send the main menu as a new message, not as an edit, to preserve order
    keyboard = [
        [InlineKeyboardButton("✍️ Record Transaction", callback_data="record_transaction")],
        [InlineKeyboardButton("📊 View Recap", callback_data="view_recap")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    message_text = f"👋 Hello {update.effective_user.first_name}! What would you like to do?"
    await context.bot.send_message(chat_id=update.effective_chat.id, text=message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    return MAIN_MENU

# --- Recap Flow ---
@restricted
async def start_recap_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks the user what they want a recap of."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="What would you like a recap of?\n\n(e.g., `this week`, `food last month`, `Starbucks spendings`)")
    return GETTING_RECAP_QUERY

async def handle_recap_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Parses the user's recap query with AI, fetches data, and generates a summary."""
    query_text = update.message.text
    await update.message.reply_text(f"Got it! Analyzing your request: \"{query_text}\"...")
    try:
        parsing_prompt = (
            "You are a query parsing expert. Analyze the user's request and extract 'timeframe' (day, week, month, all), "
            "'filter_type' (category, keywords, none), and 'filter_value' (the specific name or 'none') into a JSON object. "
            "Default timeframe is 'week'. If you can't determine a filter, use 'none'.\n\n"
            f"User request: \"{query_text}\""
        )
        response = await client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": parsing_prompt}], response_format={"type": "json_object"})
        parsed_query = json.loads(response.choices[0].message.content)
        print(f"🧠 AI parsed recap query: {parsed_query}")

        spending_data = connection.get_spending_data(
            parsed_query.get('timeframe', 'week'), 
            None if parsed_query.get('filter_type') == 'none' else parsed_query.get('filter_type'), 
            None if parsed_query.get('filter_value') == 'none' else parsed_query.get('filter_value')
        )

        if not spending_data:
            await update.message.reply_text("I couldn't find any matching spending data for your request.")
            return await start(update, context)

        summary_prompt = (
            "You are a friendly financial assistant. Based on the following JSON data, write a short, easy-to-read summary. "
            "Address the user's original query directly. Mention the total amount and number of transactions if relevant.\n\n"
            f"User's Original Query: \"{query_text}\"\n"
            f"Data: {json.dumps(spending_data)}"
        )
        summary_response = await client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": summary_prompt}], temperature=0.7, max_tokens=300)
        await update.message.reply_text(summary_response.choices[0].message.content)
    except Exception as e:
        print(f"❌ AI recap error: {e}")
        await update.message.reply_text("Sorry, I had trouble understanding or processing your recap request.")
    
    return await start(update, context)

# --- Utility Functions ---
async def save_and_confirm_transaction(update: Update, context: ContextTypes.DEFAULT_TYPE, transaction_info: dict):
    """Finalizes the transaction, saves it, and sends a confirmation."""
    parsed_data = transaction_info['parsed_data']
    connection.save_transaction(
        raw_text=transaction_info['raw_text'], 
        parsed_data=parsed_data, 
        source=transaction_info['source']
    )
    
    keywords_str = ", ".join(parsed_data.get('keywords', []))
    confirmation_message = (
        "✅ Transaction logged!\n"
        f"<b>Amount:</b> {parsed_data.get('amount'):.2f} {parsed_data.get('currency', 'SGD')}\n"
        f"<b>Category:</b> {parsed_data.get('category', 'Uncategorized')} 🤖\n"
        f"<b>Keywords:</b> {keywords_str}"
    )
    # Send the confirmation as a new message.
    await context.bot.send_message(chat_id=update.effective_chat.id, text=confirmation_message, parse_mode=ParseMode.HTML)

@restricted
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels the current operation and shows the main menu."""
    await update.message.reply_text("Operation cancelled.")
    return await start(update, context)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log Errors caused by Updates."""
    print(f"❌ An error occurred: {context.error}")

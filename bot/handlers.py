# ------------------ bot/handlers.py ------------------
import os
import json
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
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


# --- Main Menu & Keyboard Utility ---
def get_main_menu_keyboard():
    from telegram import ReplyKeyboardMarkup, KeyboardButton
    reply_keyboard = [
        [KeyboardButton("/record"), KeyboardButton("/recap")],
        [KeyboardButton("/cancel")]
    ]
    return ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)

@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the main menu and sets the state."""
    context.user_data.clear()
    message_text = f"👋 Hellooo {update.effective_user.first_name}!\nI'm ready to help you track your expenses.\nChoose an action below to get started!"
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=message_text,
        reply_markup=get_main_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )
    return MAIN_MENU

# --- Transaction Recording Flow ---
@restricted
async def start_transaction_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks the user for transaction details."""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(text="Ready to record!!!\nJust gimme amount and keywords\n(e.g., `10.50 lunch, project meeting`)\nor send a photo of the receipt.")
    else:
        await update.message.reply_text("Ready to record!!!\nJust gimme amount and keywords\n(e.g., `10.50 lunch, project meeting`)\nor send a photo of the receipt.")
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
        await update.message.reply_text(f"I see a transaction of SGD {parsed_data['amount']:.2f}. What did you spend it on?")
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
        return await start(update, context)

    # Save and send confirmation, then show main menu as a new message (not edit)
    await save_and_confirm_transaction(update, context, transaction_info)
    # Show main menu with reply keyboard
    return await start(update, context)

# --- Recap Flow ---
@restricted
async def start_recap_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asks the user what they want a recap of."""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(text="Ask me anything with keywords like summarize, list, show, or how much. ")
    else:
        await update.message.reply_text("Ask me anything with keywords like summarize, list, show, or how much. ")
    return GETTING_RECAP_QUERY

async def handle_recap_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Parses the user's recap query with AI, fetches data, and generates a summary or list."""
    query_text = update.message.text
    await update.message.reply_text(f"Got it! Analyzing your request: \"{query_text}\"...")


    try:
        # 1. Use AI to parse the user's query into structured data
        parsing_prompt = (
            "You are a query parsing expert. Analyze the user's request and extract information into a JSON object. "
            "The JSON should have: 'action' ('summarize' or 'list'), 'timeframe' (day, today, week, this week, month, all), "
            "'filter_type' (category, keywords, none), and 'filter_value' (the specific name or 'none'). "
            "If the user asks to 'show', 'list', or 'see' transactions, the action is 'list'. Otherwise, it's 'summarize'. "
            "If the filter value is a general category like 'food', 'transport', set filter_type to 'category'. "
            "If it's a specific item or brand like 'caifang', 'cig' or 'starbucks', set filter_type to 'keywords'.\n\n"
            "If the user says 'today', set timeframe to 'day'. If 'this week' or 'week', set timeframe to 'week'. If 'this month' or 'month', set timeframe to 'month'. If 'all', set timeframe to 'all'.\n\n"
            f"User request: \"{query_text}\""
        )
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": parsing_prompt}],
            response_format={"type": "json_object"}
        )
        parsed_query = json.loads(response.choices[0].message.content)
        print(f"🧠 AI parsed recap query: {parsed_query}")

        action = parsed_query.get('action', 'summarize')
        # Normalize timeframe for today/this week/this month
        timeframe = parsed_query.get('timeframe', 'week').lower()
        if timeframe in ['today']:
            timeframe = 'day'
        elif timeframe in ['this week']:
            timeframe = 'week'
        elif timeframe in ['this month']:
            timeframe = 'month'
        filter_type = parsed_query.get('filter_type', 'none')
        filter_value = parsed_query.get('filter_value', 'none')

        if action == 'list':
            # --- New Logic to List Transactions ---
            raw_transactions = connection.get_raw_transactions(
                timeframe,
                None if filter_type == 'none' else filter_type,
                None if filter_value == 'none' else filter_value
            )
            if not raw_transactions:
                await update.message.reply_text("I couldn't find any matching transactions for your request.")
                return await start(update, context)

            response_message = f"Here are the transactions for '<b>{query_text}</b>':\n\n"
            total_amount = 0
            for tx in raw_transactions:
                date_str = tx['createdAt'].strftime('%d %b')
                amount = tx['parsedData']['amount']
                total_amount += amount
                keywords = ", ".join(tx['parsedData'].get('keywords', []))
                response_message += f"🗓️ {date_str} - <b>SGD {amount:.2f}</b> ({keywords})\n"
            
            response_message += f"\n<b>Total: SGD {total_amount:.2f}</b>"
            await update.message.reply_html(response_message)

        else: # Default action is 'summarize'
            # --- Existing Logic to Summarize ---
            spending_data = connection.get_spending_data(timeframe, filter_type, filter_value)
            if not spending_data:
                await update.message.reply_text("I couldn't find any matching spending data for your request.")
                return await start(update, context)

            summary_prompt = (
                "You are an smart financial assistant who says only necessary information. Based on the following JSON data, write a short, simple-easy-to-read summary. "
                "Address the user's original query directly. Mention the total amount and number of transactions if relevant.\n\n"
                "If asked to list the transactions, provide a concise summary of the total amount and number of transactions in a readable list.\n\n"
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
    await context.bot.send_message(chat_id=update.effective_chat.id, text=confirmation_message, parse_mode=ParseMode.HTML)

@restricted
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels the current operation and shows the main menu."""
    await update.message.reply_text("Operation cancelled.", reply_markup=get_main_menu_keyboard())
    return MAIN_MENU

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log Errors caused by Updates."""
    print(f"❌ An error occurred: {context.error}")

async def set_my_commands(application):
    commands = [
        BotCommand("start", "Start the bot"),
        BotCommand("record", "Record a transaction"),
        BotCommand("recap", "View a recap"),
        BotCommand("cancel", "Cancel current operation"),
    ]
    await application.bot.set_my_commands(commands)

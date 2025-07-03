# ------------------ main.py ------------------
import os
from dotenv import load_dotenv
from telegram.ext import (
    ApplicationBuilder, 
    CommandHandler, 
    MessageHandler, 
    filters,
    ConversationHandler,
    CallbackQueryHandler,
    PicklePersistence
)
from bot import handlers

def main():
    """Run the bot."""
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not found in .env file!")

    persistence = PicklePersistence(filepath="conversation_persistence")
    application = ApplicationBuilder().token(token).persistence(persistence).build()

    # --- Set up the main ConversationHandler for menu navigation ---
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", handlers.start)],
        states={
            handlers.MAIN_MENU: [
                CallbackQueryHandler(handlers.start_transaction_flow, pattern="^record_transaction$"),
                CallbackQueryHandler(handlers.start_recap_flow, pattern="^view_recap$"),
            ],
            # States for recording a transaction
            handlers.GETTING_TRANSACTION_DETAILS: [
                MessageHandler(filters.TEXT | filters.PHOTO, handlers.handle_transaction_details)
            ],
            handlers.GETTING_MERCHANT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.received_merchant)
            ],
            handlers.GETTING_KEYWORDS: [
                CallbackQueryHandler(handlers.skip_keywords, pattern="^skip_keywords$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.received_keywords)
            ],
            # State for getting a recap query
            handlers.GETTING_RECAP_QUERY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_recap_query)
            ],
        },
        fallbacks=[CommandHandler("cancel", handlers.cancel), CommandHandler("start", handlers.start)],
        allow_reentry=True,
        persistent=True,
        name="main_conversation",
        # --- FIX: Explicitly set per_message to silence the warning ---
        per_message=False
    )

    application.add_handler(conv_handler)
    application.add_error_handler(handlers.error_handler)
    # app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()
    # app.add_handler(conv_handler)
    # app.add_error_handler(error_handler)


    print("🚀 Bot is starting in polling mode with a new keyword-based interface...")
    application.run_polling()

if __name__ == "__main__":
    main()

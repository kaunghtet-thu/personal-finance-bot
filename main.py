# ------------------ main.py ------------------
import os
from dotenv import load_dotenv
from telegram.ext import (
    ApplicationBuilder, 
    CommandHandler, 
    MessageHandler, 
    filters,
    ConversationHandler,
    CallbackQueryHandler
)
from bot import handlers

def main():
    """Run the bot."""
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not found in .env file!")

    application = ApplicationBuilder().token(token).build()

    # --- Set up the main ConversationHandler for menu navigation ---
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", handlers.start)],
        states={
            # State for when the main menu is displayed
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
            # State for getting a recap query
            handlers.GETTING_RECAP_QUERY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_recap_query)
            ],
        },
        fallbacks=[CommandHandler("cancel", handlers.cancel), CommandHandler("start", handlers.start)],
        # Allow re-entry to the start command to show the menu again
        allow_reentry=True
    )

    # Add the main conversation handler to the application
    application.add_handler(conv_handler)
    
    # Error handler
    application.add_error_handler(handlers.error_handler)

    print("🚀 Bot is starting in polling mode with a new menu-driven interface...")
    application.run_polling()

if __name__ == "__main__":
    main()

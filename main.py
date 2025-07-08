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
from telegram import BotCommand
from bot import handlers

async def set_my_commands(application):
    commands = [
        BotCommand("record", "Record a transaction"),
        BotCommand("recap", "View a recap"),
        BotCommand("cancel", "Cancel current operation"),
    ]
    await application.bot.set_my_commands(commands)

def main():
    """Run the bot."""
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not found in .env file!")

    persistence = PicklePersistence(filepath="conversation_persistence")
    application = ApplicationBuilder().token(token).persistence(persistence).build()

    # Set bot commands for BotFather-like interface
    import asyncio
    asyncio.get_event_loop().run_until_complete(set_my_commands(application))

    # --- Set up the main ConversationHandler for menu navigation ---
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", handlers.start),
                     CommandHandler("record", handlers.start_transaction_flow),
                     CommandHandler("recap", handlers.start_recap_flow)
        ],
        states={
            handlers.MAIN_MENU: [
                CallbackQueryHandler(handlers.start_transaction_flow, pattern="^record_transaction$"),
                CallbackQueryHandler(handlers.start_recap_flow, pattern="^view_recap$"),
                # If user sends a text in main menu, treat as record
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.start_transaction_flow),
            ],
            # States for recording a transaction
            handlers.GETTING_TRANSACTION_DETAILS: [
                MessageHandler(filters.TEXT | filters.PHOTO, handlers.handle_transaction_details)
            ],
            handlers.GETTING_MERCHANT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.received_merchant)
            ],
            handlers.GETTING_KEYWORDS: [
                CallbackQueryHandler(handlers.transaction_options_handler, pattern="^(delete_transaction|add_keywords)$"),
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


    print("🚀 Bot has started...")
    application.run_polling()

if __name__ == "__main__":
    main()

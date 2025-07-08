#!/usr/bin/env python3
"""
Personal Finance Bot - Simplified Version
A Telegram bot for tracking personal expenses with AI-powered categorization.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add the current directory to Python path
sys.path.append(str(Path(__file__).parent))

from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ConversationHandler
from telegram import BotCommand

from app.config import settings
from app.telegram_handlers import TelegramHandlers

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO if not settings.debug else logging.DEBUG
)
logger = logging.getLogger(__name__)

# Conversation states
WAITING_FOR_AMOUNT, WAITING_FOR_KEYWORDS, WAITING_FOR_CONFIRMATION = range(3)

async def main():
    """Main function to run the bot."""
    try:
        # Validate configuration
        if not settings.telegram_token:
            logger.error("❌ TELEGRAM_BOT_TOKEN not found in environment variables!")
            return
        
        if not settings.openai_api_key:
            logger.error("❌ OPENAI_API_KEY not found in environment variables!")
            return
        
        if not settings.mongo_uri:
            logger.error("❌ MONGO_URI not found in environment variables!")
            return
        
        logger.info("✅ Configuration validated successfully!")
        
        # Initialize handlers
        handlers = TelegramHandlers()
        
        # Create application
        application = Application.builder().token(settings.telegram_token).build()
        
        # Reset and set commands
        await application.bot.set_my_commands([])  # Clear old menu
        await application.bot.set_my_commands([
            BotCommand("start", "Start the bot"),
            BotCommand("cancel", "Cancel current operation"),
        ])
        
        # Add conversation handler for transaction recording
        conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_text_message),
                MessageHandler(filters.PHOTO, handlers.handle_photo)
            ],
            states={
                WAITING_FOR_CONFIRMATION: [
                    CallbackQueryHandler(handlers.handle_callback_query)
                ],
                WAITING_FOR_KEYWORDS: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_keywords_input)
                ]
            },
            fallbacks=[
                CommandHandler("cancel", handlers.cancel_command),
                CommandHandler("start", handlers.start_command),
                CommandHandler("help", handlers.help_command)
            ]
        )
        
        # Add handlers
        application.add_handler(CommandHandler("start", handlers.start_command))
        application.add_handler(CommandHandler("help", handlers.help_command))
        application.add_handler(CommandHandler("list", handlers.list_command))
        application.add_handler(conv_handler)
        
        # Add callback query handler for non-conversation callbacks
        application.add_handler(CallbackQueryHandler(handlers.handle_callback_query))
        
        logger.info("🤖 Starting Personal Finance Bot...")
        logger.info(f"📊 Debug mode: {settings.debug}")
        logger.info(f"🔒 Allowed users: {settings.allowed_user_ids if settings.allowed_user_ids else 'All users'}")
        
        # Start the bot
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        
        logger.info("✅ Bot is running! Press Ctrl+C to stop.")
        
        # Keep the bot running
        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            logger.info("🛑 Shutting down bot...")
        finally:
            await application.updater.stop()
            await application.stop()
            await application.shutdown()
            
    except Exception as e:
        logger.error(f"❌ Failed to start bot: {e}")
        raise

if __name__ == "__main__":
    # Suppress httpx info logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user.")
    except Exception as e:
        logger.error(f"❌ Bot crashed: {e}")
        sys.exit(1) 
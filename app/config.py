# app_simple/config.py
import os
from typing import List
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class Settings:
    """Simple configuration management."""
    
    # Telegram Configuration
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    
    # OpenAI Configuration
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    
    # Database Configuration
    mongo_uri: str = os.getenv("MONGO_URI", "")
    mongo_db_name: str = os.getenv("MONGO_DB_NAME", "")
    
    # Security Configuration
    @property
    def allowed_user_ids(self) -> List[int]:
        user_ids_str = os.getenv("ALLOWED_USER_IDS", "")
        if not user_ids_str:
            return []
        return [int(uid.strip()) for uid in user_ids_str.split(',') if uid.strip()]
    
    # Optional Configuration
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

# Global settings instance
settings = Settings() 
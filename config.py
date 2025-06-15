# config.py
import os
from dataclasses import dataclass

@dataclass
class BotConfig:
    # Telegram Bot Configuration
    BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    
    # OpenAI Configuration
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    
    # Database Configuration
    DATABASE_PATH: str = "finance_bot.db"
    
    # Feature Flags
    ENABLE_IMAGE_PROCESSING: bool = True
    ENABLE_GROUP_FEATURES: bool = True
    ENABLE_EXPORT: bool = True
    
    # Limits
    MAX_TRANSACTIONS_PER_EXPORT: int = 1000
    MAX_IMAGE_SIZE_MB: int = 10
    
    def validate(self):
        """Validate configuration"""
        if not self.BOT_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        if not self.OPENAI_API_KEY and self.ENABLE_IMAGE_PROCESSING:
            raise ValueError("OPENAI_API_KEY is required for image processing")
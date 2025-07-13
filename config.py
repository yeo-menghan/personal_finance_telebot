# config.py
import os
from dataclasses import dataclass

@dataclass
class BotConfig:
    # Telegram Bot Configuration
    BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    
    # OpenAI Configuration
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    
    # Supabase Configuration
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")
    
    def validate(self):
        """Validate configuration"""
        if not self.BOT_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        if not self.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is required for image processing")
        if not self.SUPABASE_URL or not self.SUPABASE_KEY:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY are required for Supabase database")

# Usage example:
config = BotConfig()
config.validate()
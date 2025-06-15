import os
import logging
import sqlite3
import json
import base64
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import pandas as pd
from io import BytesIO
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Updated imports for python-telegram-bot v20+
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class FinanceBot:
    def __init__(self, bot_token: str, openai_api_key: str):
        self.bot_token = bot_token
        self.openai_api_key = openai_api_key
        self.db_path = "finance_bot.db"
        self.init_database()
        
    def init_database(self):
        """Initialize SQLite database with required tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Transactions table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                type TEXT CHECK(type IN ('expense', 'income')),
                amount REAL,
                category TEXT,
                description TEXT,
                date DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        # Categories table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                type TEXT CHECK(type IN ('expense', 'income')),
                emoji TEXT DEFAULT ''
            )
        ''')
        
        # Insert default categories
        default_categories = [
            ('Food & Dining', 'expense', '🍽️'),
            ('Transportation', 'expense', '🚗'),
            ('Shopping', 'expense', '🛒'),
            ('Entertainment', 'expense', '🎬'),
            ('Bills & Utilities', 'expense', '💡'),
            ('Healthcare', 'expense', '🏥'),
            ('Travel', 'expense', '✈️'),
            ('Other Expenses', 'expense', '💸'),
            ('Salary', 'income', '💰'),
            ('Freelance', 'income', '💼'),
            ('Investment', 'income', '📈'),
            ('Other Income', 'income', '💵')
        ]
        
        cursor.executemany('''
            INSERT OR IGNORE INTO categories (name, type, emoji) VALUES (?, ?, ?)
        ''', default_categories)
        
        conn.commit()
        conn.close()

    def add_user(self, user_id: int, username: str, first_name: str):
        """Add or update user in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
        ''', (user_id, username, first_name))
        conn.commit()
        conn.close()

    def add_transaction(self, user_id: int, chat_id: int, transaction_type: str, 
                       amount: float, category: str, description: str, date: str = None):
        """Add a new transaction to the database"""
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
            
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO transactions (user_id, chat_id, type, amount, category, description, date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, chat_id, transaction_type, amount, category, description, date))
        conn.commit()
        conn.close()

    def get_categories(self, transaction_type: str) -> List[tuple]:
        """Get available categories for a transaction type"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT name, emoji FROM categories WHERE type = ?', (transaction_type,))
        categories = cursor.fetchall()
        conn.close()
        return categories

    def get_user_transactions(self, user_id: int, chat_id: int = None, days: int = 30) -> List[dict]:
        """Get user transactions for specified period"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        date_limit = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        if chat_id:
            cursor.execute('''
                SELECT * FROM transactions 
                WHERE user_id = ? AND chat_id = ? AND date >= ?
                ORDER BY date DESC
            ''', (user_id, chat_id, date_limit))
        else:
            cursor.execute('''
                SELECT * FROM transactions 
                WHERE user_id = ? AND date >= ?
                ORDER BY date DESC
            ''', (user_id, date_limit))
            
        transactions = cursor.fetchall()
        conn.close()
        
        # Convert to list of dictionaries
        columns = ['id', 'user_id', 'chat_id', 'type', 'amount', 'category', 'description', 'date', 'created_at']
        return [dict(zip(columns, transaction)) for transaction in transactions]

    async def process_image_with_gpt4v(self, image_data: bytes) -> Dict:
        """Process receipt image using GPT-4V to extract transaction details"""
        if not self.openai_api_key:
            return {"error": "OpenAI API key not configured"}
            
        # Encode image to base64
        base64_image = base64.b64encode(image_data).decode('utf-8')
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.openai_api_key}"
        }
        
        payload = {
            "model": "gpt-4o",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": """Analyze this receipt/bill image and extract the following information in JSON format:
                            {
                                "amount": <total_amount_as_number>,
                                "merchant": "<merchant_name>",
                                "category": "<best_matching_category>",
                                "date": "<date_in_YYYY-MM-DD_format>",
                                "items": ["<item1>", "<item2>"],
                                "currency": "<currency_symbol_or_code>"
                            }
                            
                            For category, choose from: Food & Dining, Transportation, Shopping, Entertainment, Bills & Utilities, Healthcare, Travel, Other Expenses
                            If you can't determine a field, use null or empty string.
                            """
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            "max_tokens": 300
        }
        
        try:
            response = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            
            result = response.json()
            content = result['choices'][0]['message']['content']
            
            # Extract JSON from the response
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            else:
                return {"error": "Could not parse receipt"}
                
        except Exception as e:
            logger.error(f"Error processing image with GPT-4V: {e}")
            return {"error": str(e)}

# Bot command handlers
class BotHandlers:
    def __init__(self, finance_bot: FinanceBot):
        self.finance_bot = finance_bot

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start command handler"""
        user = update.effective_user
        self.finance_bot.add_user(user.id, user.username or "", user.first_name or "")
        
        welcome_text = f"""
🏦 **Finance Tracker Bot** 

Hello {user.first_name}! I'm here to help you track your finances.

**Available Commands:**
💰 `/add_expense` - Add an expense
💵 `/add_income` - Add income
📊 `/summary` - View financial summary
📋 `/recent` - Show recent transactions

**Image Recognition:**
📸 Just send me a photo of your receipt/bill and I'll extract the details automatically!

**Group Usage:**
Add me to groups to track shared expenses and split bills among members.
        """
        
        await update.message.reply_text(welcome_text, parse_mode='Markdown')

    async def add_expense(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle add expense command"""
        await self._add_transaction(update, context, 'expense')

    async def add_income(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle add income command"""
        await self._add_transaction(update, context, 'income')

    async def _add_transaction(self, update: Update, context: ContextTypes.DEFAULT_TYPE, transaction_type: str):
        """Generic handler for adding transactions"""
        # Get categories for this transaction type
        categories = self.finance_bot.get_categories(transaction_type)
        
        # Create inline keyboard with categories
        keyboard = []
        for i in range(0, len(categories), 2):
            row = []
            for j in range(2):
                if i + j < len(categories):
                    cat_name, emoji = categories[i + j]
                    row.append(InlineKeyboardButton(
                        f"{emoji} {cat_name}",
                        callback_data=f"category_{transaction_type}_{cat_name}"
                    ))
            keyboard.append(row)
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Store transaction type in user data
        context.user_data['transaction_type'] = transaction_type
        context.user_data['awaiting'] = 'category'
        
        await update.message.reply_text(
            f"💰 Adding {transaction_type}. Please select a category:",
            reply_markup=reply_markup
        )

    async def handle_category_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle category selection from inline keyboard"""
        query = update.callback_query
        await query.answer()
        
        # Parse callback data
        _, transaction_type, category = query.data.split('_', 2)
        
        context.user_data['category'] = category
        context.user_data['transaction_type'] = transaction_type
        context.user_data['awaiting'] = 'amount'
        
        await query.edit_message_text(
            f"Category selected: {category}\n\n💵 Please enter the amount:"
        )

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text messages based on current state"""
        if 'awaiting' not in context.user_data:
            return
            
        if context.user_data['awaiting'] == 'amount':
            try:
                amount = float(update.message.text)
                context.user_data['amount'] = amount
                context.user_data['awaiting'] = 'description'
                
                await update.message.reply_text(
                    f"Amount: ${amount:.2f}\n\n📝 Please enter a description (or send /skip):"
                )
            except ValueError:
                await update.message.reply_text("❌ Invalid amount. Please enter a number.")
                
        elif context.user_data['awaiting'] == 'description':
            description = update.message.text
            
            # Save transaction
            user = update.effective_user
            chat_id = update.effective_chat.id
            
            transaction_type = context.user_data['transaction_type']
            category = context.user_data['category']
            amount = context.user_data['amount']
            
            self.finance_bot.add_transaction(
                user_id=user.id,
                chat_id=chat_id,
                transaction_type=transaction_type,
                amount=amount,
                category=category,
                description=description
            )
            
            transaction_emoji = "💸" if transaction_type == 'expense' else "💰"
            
            await update.message.reply_text(
                f"✅ {transaction_emoji} Transaction added successfully!\n\n"
                f"**Category:** {category}\n"
                f"**Amount:** ${amount:.2f}\n"
                f"**Description:** {description}",
                parse_mode='Markdown'
            )
            
            # Clear user data
            context.user_data.clear()

    async def skip_description(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Skip description step"""
        if context.user_data.get('awaiting') == 'description':
            # Save transaction without description
            user = update.effective_user
            chat_id = update.effective_chat.id
            
            transaction_type = context.user_data['transaction_type']
            category = context.user_data['category']
            amount = context.user_data['amount']
            
            self.finance_bot.add_transaction(
                user_id=user.id,
                chat_id=chat_id,
                transaction_type=transaction_type,
                amount=amount,
                category=category,
                description="No description"
            )
            
            transaction_emoji = "💸" if transaction_type == 'expense' else "💰"
            
            await update.message.reply_text(
                f"✅ {transaction_emoji} Transaction added successfully!\n\n"
                f"**Category:** {category}\n"
                f"**Amount:** ${amount:.2f}",
                parse_mode='Markdown'
            )
            
            # Clear user data
            context.user_data.clear()

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle photo messages for receipt processing"""
        await update.message.reply_text("📸 Processing your receipt... This may take a moment.")
        
        try:
            # Get the photo
            photo = update.message.photo[-1]  # Get highest resolution
            file = await context.bot.get_file(photo.file_id)
            
            # Download image data
            image_data = BytesIO()
            await file.download_to_memory(image_data)
            image_bytes = image_data.getvalue()
            
            # Process with GPT-4V
            result = await self.finance_bot.process_image_with_gpt4v(image_bytes)
            
            if "error" in result:
                await update.message.reply_text(f"❌ Error processing receipt: {result['error']}")
                return
            
            # Create confirmation message
            confirmation_text = f"""
📋 **Receipt Analysis Results:**

💰 **Amount:** ${result.get('amount', 'Unknown')}
🏪 **Merchant:** {result.get('merchant', 'Unknown')}
📂 **Category:** {result.get('category', 'Other Expenses')}
📅 **Date:** {result.get('date', datetime.now().strftime('%Y-%m-%d'))}
🛍️ **Items:** {', '.join(result.get('items', [])[:3])}

Would you like to save this transaction?
            """
            
            # Store extracted data
            context.user_data['extracted_data'] = result
            
            # Create confirmation keyboard
            keyboard = [
                [InlineKeyboardButton("✅ Save Transaction", callback_data="save_extracted")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_extracted")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(confirmation_text, reply_markup=reply_markup, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Error handling photo: {e}")
            await update.message.reply_text("❌ Error processing image. Please try again.")

    async def handle_extracted_transaction(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle confirmation of extracted transaction data"""
        query = update.callback_query
        await query.answer()
        
        if query.data == "save_extracted":
            extracted_data = context.user_data.get('extracted_data', {})
            user = update.effective_user
            chat_id = update.effective_chat.id
            
            try:
                # Save the transaction
                self.finance_bot.add_transaction(
                    user_id=user.id,
                    chat_id=chat_id,
                    transaction_type='expense',  # Receipts are typically expenses
                    amount=float(extracted_data.get('amount', 0)),
                    category=extracted_data.get('category', 'Other Expenses'),
                    description=f"{extracted_data.get('merchant', 'Receipt')} - {', '.join(extracted_data.get('items', [])[:2])}"
                )
                
                await query.edit_message_text("✅ Transaction saved successfully from receipt!")
                
            except Exception as e:
                logger.error(f"Error saving extracted transaction: {e}")
                await query.edit_message_text("❌ Error saving transaction. Please try manually.")
                
        elif query.data == "cancel_extracted":
            await query.edit_message_text("❌ Receipt processing cancelled.")
            
        # Clear extracted data
        context.user_data.pop('extracted_data', None)

    async def summary(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show financial summary"""
        user = update.effective_user
        chat_id = update.effective_chat.id
        
        # Get transactions for last 30 days
        transactions = self.finance_bot.get_user_transactions(user.id, chat_id, days=30)
        
        if not transactions:
            await update.message.reply_text("📊 No transactions found for the last 30 days.")
            return
        
        # Calculate summary
        total_income = sum(t['amount'] for t in transactions if t['type'] == 'income')
        total_expenses = sum(t['amount'] for t in transactions if t['type'] == 'expense')
        net_balance = total_income - total_expenses
        
        # Group expenses by category
        expense_by_category = {}
        for t in transactions:
            if t['type'] == 'expense':
                category = t['category']
                expense_by_category[category] = expense_by_category.get(category, 0) + t['amount']
        
        # Create summary text
        summary_text = f"""
📊 **Financial Summary (Last 30 Days)**

💰 **Total Income:** ${total_income:.2f}
💸 **Total Expenses:** ${total_expenses:.2f}
📈 **Net Balance:** ${net_balance:.2f}

**Top Expense Categories:**
"""
        
        # Add top categories
        sorted_categories = sorted(expense_by_category.items(), key=lambda x: x[1], reverse=True)
        for category, amount in sorted_categories[:5]:
            percentage = (amount / total_expenses * 100) if total_expenses > 0 else 0
            summary_text += f"• {category}: ${amount:.2f} ({percentage:.1f}%)\n"
        
        await update.message.reply_text(summary_text, parse_mode='Markdown')

    async def recent_transactions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show recent transactions"""
        user = update.effective_user
        chat_id = update.effective_chat.id
        
        transactions = self.finance_bot.get_user_transactions(user.id, chat_id, days=7)
        
        if not transactions:
            await update.message.reply_text("📋 No recent transactions found.")
            return
        
        recent_text = "📋 **Recent Transactions (Last 7 Days):**\n\n"
        
        for t in transactions[:10]:  # Show last 10
            emoji = "💸" if t['type'] == 'expense' else "💰"
            recent_text += f"{emoji} ${t['amount']:.2f} - {t['category']}\n"
            recent_text += f"   📝 {t['description']}\n"
            recent_text += f"   📅 {t['date']}\n\n"
        
        await update.message.reply_text(recent_text, parse_mode='Markdown')

def main():
    """Main function to run the bot"""
    # Get tokens from environment variables
    BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    
    if not BOT_TOKEN:
        print("❌ Error: TELEGRAM_BOT_TOKEN environment variable not set!")
        print("Please create a .env file with your bot token.")
        return
    
    if not OPENAI_API_KEY:
        print("⚠️  Warning: OPENAI_API_KEY not set. Image processing will be disabled.")
    
    print(f"🔑 Using bot token: {BOT_TOKEN[:10]}...")
    
    # Initialize finance bot
    finance_bot = FinanceBot(BOT_TOKEN, OPENAI_API_KEY)
    handlers = BotHandlers(finance_bot)
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("add_expense", handlers.add_expense))
    application.add_handler(CommandHandler("add_income", handlers.add_income))
    application.add_handler(CommandHandler("summary", handlers.summary))
    application.add_handler(CommandHandler("recent", handlers.recent_transactions))
    application.add_handler(CommandHandler("skip", handlers.skip_description))
    
    # Callback query handlers
    application.add_handler(CallbackQueryHandler(
        handlers.handle_category_selection, 
        pattern=r"^category_"
    ))
    application.add_handler(CallbackQueryHandler(
        handlers.handle_extracted_transaction,
        pattern=r"^(save_extracted|cancel_extracted)$"
    ))
    
    # Message handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handlers.handle_photo))
    
    # Start the bot
    print("🤖 Finance Bot is starting...")
    print("✅ Bot is running! Send /start to your bot to begin.")
    application.run_polling()

if __name__ == "__main__":
    main()
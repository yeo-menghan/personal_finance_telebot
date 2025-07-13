import os
import logging
import json
import base64
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import pandas as pd
from io import BytesIO
from dotenv import load_dotenv
import re

from supabase import create_client, Client as SupabaseClient

# Load environment variables
load_dotenv()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

from export_handler import generate_weekly_summary, export_csv

from config import BotConfig

config = BotConfig()
config.validate()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class FinanceBot:
    def __init__(self, config: BotConfig):
        self.config = config
        self.supabase: SupabaseClient = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    
    def add_user(self, user_id: int, username: str):
        self.supabase.table("users").upsert(
            {
                "telegram_id": user_id,
                "username": username,
            },
            on_conflict=["telegram_id"] 
        ).execute()

    def add_transaction(self, user_id: int, amount: float, category: str, description: str, date: str = None):
        """Add a new transaction to Supabase using category_id"""
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        # Fetch user uuid
        user = self.supabase.table("users").select("id").eq("telegram_id", user_id).single().execute().data
        if not user:
            logger.error(f"Supabase user not found for telegram_id {user_id}")
            return
        category_id = self.get_category_id(category)
        if not category_id:
            logger.error(f"Category '{category}' not found in base_categories.")
            return
        # Insert expenditure with category_id
        self.supabase.table("expenditures").insert({
            "user_id": user["id"],
            "amount": amount,
            "currency": "SGD",
            "date": date,
            "category_id": category_id,
            "description": description,
            "input_method": "manual",
        }).execute()

    def get_category_id(self, category_name: str) -> int:
        """Get category_id for a given category name from base_categories table."""
        cat = self.supabase.table("base_categories").select("id").eq("name", category_name).single().execute().data
        return cat["id"] if cat else None

    def get_categories(self) -> List[tuple]:
        cats = self.supabase.table("base_categories").select("name,icon,id").execute().data
        return [(c['name'], c.get('icon', ''), c['id']) for c in cats]

    def get_user_transactions(self, user_id: int, days: int = 30) -> List[dict]:
        """Get user transactions from Supabase for specified period"""
        user = self.supabase.table("users").select("id").eq("telegram_id", user_id).single().execute().data
        if not user:
            return []
        user_uuid = user['id']
        date_limit = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        query = self.supabase.table("expenditures").select("*").eq("user_id", user_uuid).gte("date", date_limit)
        data = query.order("date", desc=True).limit(1000).execute().data
        return data

    async def process_image_with_gpt4v(self, image_data: bytes) -> Dict:
        """Process receipt image using GPT-4V to extract transaction details"""
        if not self.config.OPENAI_API_KEY:
            return {"error": "OpenAI API key not configured"}
        base64_image = base64.b64encode(image_data).decode('utf-8')
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.OPENAI_API_KEY}"
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
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            else:
                return {"error": "Could not parse receipt"}
        except Exception as e:
            logger.error(f"Error processing image with GPT-4V: {e}")
            return {"error": str(e)}

class BotHandlers:
    def __init__(self, finance_bot: FinanceBot):
        self.finance_bot = finance_bot

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.finance_bot.add_user(user.id, user.username or "")
        welcome_text = (
            "<b>🏦 Finance Tracker Bot</b>\n\n"
            f"Hello <b>{user.username or 'there'}</b>! I'm here to help you track your finances.\n\n"
            "<b>Available Commands:</b>\n"
            "💵 <code>/add_transaction</code> - Add a transaction\n"
            "📋 <code>/recent</code> - Show recent transactions\n"
            "📊 <code>/export</code> - Export weekly summaries as csv\n"
            "📊 <code>/weekly_summary</code> - View weekly financial summary\n"
            "<b>Image Recognition:</b>\n"
            "📸 Just send me a photo of your receipt/bill and I'll extract the details automatically!\n\n"
            "<b>Group Usage:</b>\n"
            "Add me to groups to track shared expenses and split bills among members."
        )
        await update.message.reply_text(welcome_text, parse_mode='HTML')

    async def add_transaction(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._add_transaction(update, context)

    async def _add_transaction(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        categories = self.finance_bot.get_categories()
        keyboard = []
        for i in range(0, len(categories), 2):
            row = []
            for j in range(2):
                if i + j < len(categories):
                    cat_name, emoji, _ = categories[i + j]
                    row.append(InlineKeyboardButton(
                        f"{emoji} {cat_name}",
                        callback_data=f"category_{cat_name}"
                    ))
            keyboard.append(row)
        reply_markup = InlineKeyboardMarkup(keyboard)
        context.user_data['awaiting'] = 'category'
        await update.message.reply_text(
            f"💵 Adding a transaction. Please select a category:",
            reply_markup=reply_markup,
            parse_mode='HTML'
        )

    async def handle_category_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        _, category = query.data.split('_', 1)
        context.user_data['category'] = category
        context.user_data['awaiting'] = 'amount'
        await query.edit_message_text(
            f"Category selected: <b>{category}</b>\n\n💵 Please enter the amount:",
            parse_mode='HTML'
        )

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if 'awaiting' not in context.user_data:
            return
        if context.user_data['awaiting'] == 'amount':
            try:
                amount = float(update.message.text)
                context.user_data['amount'] = amount
                context.user_data['awaiting'] = 'description'
                await update.message.reply_text(
                    f"Amount: <b>${amount:.2f}</b>\n\n📝 Please enter a description (or send /skip):",
                    parse_mode='HTML'
                )
            except ValueError:
                await update.message.reply_text("❌ Invalid amount. Please enter a number.")
        elif context.user_data['awaiting'] == 'description':
            description = update.message.text
            user = update.effective_user
            category = context.user_data['category']
            amount = context.user_data['amount']
            self.finance_bot.add_transaction(
                user_id=user.id,
                amount=amount,
                category=category,
                description=description
            )
            transaction_emoji = "💵"
            await update.message.reply_text(
                f"✅ {transaction_emoji} Transaction added successfully!\n\n"
                f"<b>Category:</b> {category}\n"
                f"<b>Amount:</b> ${amount:.2f}\n"
                f"<b>Description:</b> {description}",
                parse_mode='HTML'
            )
            context.user_data.clear()

    async def skip_description(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if context.user_data.get('awaiting') == 'description':
            user = update.effective_user
            category = context.user_data['category']
            amount = context.user_data['amount']
            self.finance_bot.add_transaction(
                user_id=user.id,
                amount=amount,
                category=category,
                description="No description"
            )
            transaction_emoji = "💵"
            await update.message.reply_text(
                f"✅ {transaction_emoji} Transaction added successfully!\n\n"
                f"<b>Category:</b> {category}\n"
                f"<b>Amount:</b> ${amount:.2f}",
                parse_mode='HTML'
            )
            context.user_data.clear()

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("📸 Processing your receipt... This may take a moment.")
        try:
            photo = update.message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            image_data = BytesIO()
            await file.download_to_memory(image_data)
            image_bytes = image_data.getvalue()
            result = await self.finance_bot.process_image_with_gpt4v(image_bytes)
            if "error" in result:
                await update.message.reply_text(f"❌ Error processing receipt: {result['error']}")
                return
            confirmation_text = (
                "<b>📋 Receipt Analysis Results:</b>\n\n"
                f"💰 <b>Amount:</b> ${result.get('amount', 'Unknown')}\n"
                f"🏪 <b>Merchant:</b> {result.get('merchant', 'Unknown')}\n"
                f"📂 <b>Category:</b> {result.get('category', 'Other Expenses')}\n"
                f"📅 <b>Date:</b> {result.get('date', datetime.now().strftime('%Y-%m-%d'))}\n"
                f"🛍️ <b>Items:</b> {', '.join(result.get('items', [])[:3])}\n\n"
                "Would you like to save this transaction?"
            )
            context.user_data['extracted_data'] = result
            keyboard = [
                [InlineKeyboardButton("✅ Save Transaction", callback_data="save_extracted")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_extracted")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(confirmation_text, reply_markup=reply_markup, parse_mode='HTML')
        except Exception as e:
            logger.error(f"Error handling photo: {e}")
            await update.message.reply_text("❌ Error processing image. Please try again.")

    async def handle_extracted_transaction(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if query.data == "save_extracted":
            extracted_data = context.user_data.get('extracted_data', {})
            user = update.effective_user
            try:
                self.finance_bot.add_transaction(
                    user_id=user.id,
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
        context.user_data.pop('extracted_data', None)

    async def recent_transactions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        transactions = self.finance_bot.get_user_transactions(user.id, days=7)
        if not transactions:
            await update.message.reply_text("📋 No recent transactions found.")
            return
        recent_text = "<b>📋 Recent Transactions (Last 7 Days):</b>\n\n"

        # Fetch all categories for mapping
        cat_records = self.finance_bot.supabase.table("base_categories").select("id,name").execute().data
        cat_map = {c["id"]: c["name"] for c in cat_records}
        for t in transactions[:10]:
            emoji = "💵"
            # Map category_id to category name
            cat_name = cat_map.get(t.get('category_id'), 'Unknown')
            recent_text += f"{emoji} ${t['amount']:.2f} - {cat_name}\n"
            recent_text += f"   📝 {t.get('description', '')}\n"
            recent_text += f"   📅 {t.get('date', '')}\n\n"
        await update.message.reply_text(recent_text, parse_mode='HTML')
    
    async def export(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_row = self.finance_bot.supabase.table("users").select("id").eq("telegram_id", user.id).single().execute().data
        if not user_row:
            await update.message.reply_text("User not found. Please /start first.")
            return
        user_id = user_row["id"]
        csv_path = export_csv(self.finance_bot.supabase, user_id)
        if not csv_path:
            await update.message.reply_text("No data found for export.")
            return
        with open(csv_path, "rb") as f:
            await update.message.reply_document(document=f, filename=os.path.basename(csv_path))
        os.remove(csv_path)  # Clean up after sending

    async def weekly_summary(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        user_row = self.finance_bot.supabase.table("users").select("id").eq("telegram_id", user.id).single().execute().data
        if not user_row:
            await update.message.reply_text("User not found. Please /start first.")
            return
        user_id = user_row["id"]
        trend_img_path, summary_text, pie_img_path = generate_weekly_summary(self.finance_bot.supabase, user_id)
        if trend_img_path and pie_img_path:
            # Send both images as an album, with summary as caption on the pie chart
            media = [
                {"type": "photo", "media": open(trend_img_path, "rb")},
                {"type": "photo", "media": open(pie_img_path, "rb"), "caption": summary_text, "parse_mode": "HTML"}
            ]
            # Telegram API does not accept dicts, but PTB has InputMediaPhoto, so you may need:
            from telegram import InputMediaPhoto
            await update.message.reply_media_group([
                InputMediaPhoto(open(trend_img_path, "rb")),
                InputMediaPhoto(open(pie_img_path, "rb"), caption=summary_text, parse_mode="HTML")
            ])
            # Clean up
            os.remove(trend_img_path)
            os.remove(pie_img_path)
        elif trend_img_path:
            with open(trend_img_path, "rb") as img:
                await update.message.reply_photo(img, caption=summary_text, parse_mode="HTML")
            os.remove(trend_img_path)
        else:
            await update.message.reply_text(summary_text)

def main():
    finance_bot = FinanceBot(config)
    handlers = BotHandlers(finance_bot)
    application = Application.builder().token(config.BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("add_transaction", handlers.add_transaction))
    application.add_handler(CommandHandler("recent", handlers.recent_transactions))
    application.add_handler(CommandHandler("skip", handlers.skip_description))
    application.add_handler(CommandHandler("export", handlers.export))
    application.add_handler(CommandHandler("weekly_summary", handlers.weekly_summary))  
    application.add_handler(CallbackQueryHandler(
        handlers.handle_category_selection,
        pattern=r"^category_"
    ))
    application.add_handler(CallbackQueryHandler(
        handlers.handle_extracted_transaction,
        pattern=r"^(save_extracted|cancel_extracted)$"
    ))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handlers.handle_photo))
    
    print("🤖 Finance Bot is starting...")
    print("✅ Bot is running! Send /start to your bot to begin.")
    application.run_polling()

if __name__ == "__main__":
    main()
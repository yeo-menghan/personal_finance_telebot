# export_handler.py
import pandas as pd
from io import BytesIO
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime, timedelta

class ExportHandler:
    def __init__(self, finance_bot):
        self.finance_bot = finance_bot
    
    async def export_csv(self, update, context):
        """Export transactions to CSV"""
        user = update.effective_user
        chat_id = update.effective_chat.id
        
        # Get all transactions for user
        transactions = self.finance_bot.get_user_transactions(user.id, chat_id, days=365)
        
        if not transactions:
            await update.message.reply_text("📊 No transactions to export.")
            return
        
        # Create DataFrame
        df = pd.DataFrame(transactions)
        
        # Create CSV in memory
        csv_buffer = BytesIO()
        df.to_csv(csv_buffer, index=False)
        csv_buffer.seek(0)
        
        # Send file
        filename = f"transactions_{user.id}_{datetime.now().strftime('%Y%m%d')}.csv"
        await context.bot.send_document(
            chat_id=chat_id,
            document=csv_buffer,
            filename=filename,
            caption=f"📊 Your transaction export ({len(transactions)} records)"
        )
    
    async def generate_chart(self, update, context):
        """Generate expense chart"""
        user = update.effective_user
        chat_id = update.effective_chat.id
        
        transactions = self.finance_bot.get_user_transactions(user.id, chat_id, days=30)
        expenses = [t for t in transactions if t['type'] == 'expense']
        
        if not expenses:
            await update.message.reply_text("📊 No expenses to chart.")
            return
        
        # Group by category
        df = pd.DataFrame(expenses)
        category_totals = df.groupby('category')['amount'].sum().sort_values(ascending=False)
        
        # Create pie chart
        plt.figure(figsize=(10, 8))
        plt.pie(category_totals.values, labels=category_totals.index, autopct='%1.1f%%')
        plt.title('Expenses by Category (Last 30 Days)')
        
        # Save to buffer
        img_buffer = BytesIO()
        plt.savefig(img_buffer, format='png', dpi=300, bbox_inches='tight')
        img_buffer.seek(0)
        plt.close()
        
        # Send chart
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=img_buffer,
            caption="📊 Your expense breakdown for the last 30 days"
        )
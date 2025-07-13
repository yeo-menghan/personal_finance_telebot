# export_handler.py
import pandas as pd
import matplotlib.pyplot as plt
import os
from datetime import datetime, timedelta

def fetch_user_expenditures(supabase, user_id, days=7):
    from datetime import datetime, timedelta

    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days)

    # Fetch all categories for lookup
    cat_records = supabase.table("base_categories").select("id,name").execute().data
    cat_map = {c["id"]: c["name"] for c in cat_records}

    # Fetch expenditures for user
    res = supabase.table("expenditures") \
        .select("*") \
        .eq("user_id", user_id) \
        .gte("date", str(start_date)) \
        .lte("date", str(end_date)) \
        .execute()
    records = res.data if hasattr(res, "data") else res
    df = pd.DataFrame(records)

    # Map category_id to name
    if not df.empty:
        df['category'] = df['category_id'].map(cat_map).fillna('Unknown')
    else:
        df['category'] = []

    return df

def generate_weekly_summary(supabase, user_id):
    df = fetch_user_expenditures(supabase, user_id, days=7)
    if df.empty:
        return None, "No expenditures found for the past 7 days.", None

    # Trendline (daily spending)
    df['date'] = pd.to_datetime(df['date'])
    daily = df.groupby('date')['amount'].sum().reset_index()
    trend_img_path = f"trend_{user_id}.png"
    plt.figure(figsize=(6, 3))
    plt.plot(daily['date'], daily['amount'], marker='o')
    plt.title('Weekly Expenditure Trend')
    plt.xlabel('Date')
    plt.ylabel('Amount')
    plt.tight_layout()
    plt.savefig(trend_img_path)
    plt.close()

    # Pie chart (category breakdown)
    cat_sum = df.groupby('category')['amount'].sum()
    pie_img_path = f"pie_{user_id}.png"
    plt.figure(figsize=(4.5, 4.5))
    cat_sum.plot.pie(autopct='%1.1f%%', startangle=90, counterclock=False)
    plt.title('Expenditure by Category')
    plt.ylabel('')
    plt.tight_layout()
    plt.savefig(pie_img_path)
    plt.close()

    # Summary text
    total = df['amount'].sum()
    summary_text = f"Total spent this week: <b>{total:.2f}</b> SGD\n\n"
    summary_text += "\n".join([f"<b>{cat}:</b> {amt:.2f}" for cat, amt in cat_sum.items()])

    # Return both images: trend and pie
    return trend_img_path, summary_text, pie_img_path


def export_csv(supabase, user_id, days=7):
    df = fetch_user_expenditures(supabase, user_id, days=days)
    if df.empty:
        return None
    csv_path = f"weekly_summary_{user_id}.csv"
    df.to_csv(csv_path, index=False)
    return csv_path
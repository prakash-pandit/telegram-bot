from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultCachedPhoto
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, InlineQueryHandler
import random
import time
import json
import logging
import asyncio
import html
import os
import uuid
from datetime import datetime
from pymongo import MongoClient
from flask import Flask, request, jsonify
import threading

logging.basicConfig(level=logging.INFO)

# MongoDB Setup
MONGO_URI = os.environ.get("MONGO_URI")
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["botdb"]
col = db["gamedata"]

# Data storage
users = {}
last_daily = {}
last_weekly = {}
daily_streak = {}
summon_cooldowns = {}
active_wordle = {}
OWNER_ID = 6199312233
GROUP_ID = -1003865556551
LOG_CHANNEL_ID = -1003995927727
uploaders = set()

group_message_counts = {}
active_bid = {}
SPAWN_EVERY = 50
BID_DURATION = 60
BID_EXTEND = 10

active_trades = {}
TRADE_TIMEOUT = 120

active_guess = {}
last_checkin = {}
checkin_streak = {}

last_message_time = {}
last_message_text = {}
USER_MSG_COOLDOWN = 5
MIN_MSG_LENGTH = 4

ITEMS_PER_PAGE = 5
SHOP_PRICES = {1: 50000, 2: 100000, 3: 200000, 4: 400000, 5: 1000000}
SHOP_PAGE_SIZE = 5
CHECKIN_BASE_REWARD = 2000
CHECKIN_STREAK_BONUS = 500

characters = []

# Helper Functions
def save_data():
    col.replace_one(
        {"_id": "main"},
        {
            "_id": "main",
            "users": users,
            "uploaders": list(uploaders),
            "last_daily": last_daily,
            "daily_streak": daily_streak,
            "last_weekly": last_weekly,
            "active_wordle": active_wordle,
            "characters": characters,
            "last_checkin": last_checkin,
            "checkin_streak": checkin_streak,
        },
        upsert=True
    )

def load_data():
    global users, uploaders, last_daily, last_weekly, daily_streak, active_wordle, characters, last_checkin, checkin_streak
    try:
        data = col.find_one({"_id": "main"})
        if data:
            users = data.get("users", {})
            uploaders = set(map(str, data.get("uploaders", [])))
            last_daily = data.get("last_daily", {})
            last_weekly = data.get("last_weekly", {})
            daily_streak = data.get("daily_streak", {})
            active_wordle = data.get("active_wordle", {})
            characters = data.get("characters", [])
            last_checkin = data.get("last_checkin", {})
            checkin_streak = data.get("checkin_streak", {})
    except Exception as e:
        print("Load error:", e)

def ensure_user(user_id, update=None):
    if user_id not in users:
        users[user_id] = {
            "characters": [],
            "coins": 100,
            "joined": time.time(),
            "name": update.effective_user.first_name if update else "Unknown",
            "username": update.effective_user.username if update else "",
            "wordle_wins": {"today": 0, "week": 0, "month": 0, "alltime": 0},
            "wordle_last_date": {},
            "wordle_stats": {
                "played": 0, "won": 0, "lost": 0,
                "total_guesses": 0, "best_win": 0,
                "coins_earned": 0, "current_streak": 0, "best_streak": 0
            }
        }
    save_data()

def format_time(seconds):
    seconds = int(seconds)
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    if days > 0: return f"{days}d {hours}h"
    if hours > 0: return f"{hours}h {minutes}m"
    return f"{minutes}m"

def rarity_label(rarity):
    return {1: "⚪ Common", 2: "🔵 Rare", 3: "🟣 Epic", 4: "🟡 Legendary", 5: "⚡ Celebrity"}.get(rarity, "Unknown")

# Command Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    await update.message.reply_text(
        "🎮 Welcome!\n\n"
        "Commands:\n"
        "/summon — spend coins for a card\n"
        "/bonus — collect daily/weekly coins\n"
        "/collection — see your cards\n"
        "/profile — your profile\n"
        "/wordle — play wordle\n"
        "/shop — buy cards\n"
        "/trade — trade with others"
    )

async def bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    now = time.time()
    
    daily_text = "💰 Daily (75,000)"
    if user_id in last_daily:
        remaining = 86400 - (now - last_daily[user_id])
        if remaining > 0:
            daily_text = f"❌ Daily ({format_time(remaining)})"
    
    weekly_text = "📅 Weekly (6,25,000)"
    if user_id in last_weekly:
        remaining = 604800 - (now - last_weekly[user_id])
        if remaining > 0:
            weekly_text = f"❌ Weekly ({format_time(remaining)})"
    
    keyboard = [[InlineKeyboardButton(daily_text, callback_data="bonus_daily"), InlineKeyboardButton(weekly_text, callback_data="bonus_weekly")]]
    await update.message.reply_text("🎁 Bonus Panel", reply_markup=InlineKeyboardMarkup(keyboard))

async def bonus_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    ensure_user(user_id)
    now = time.time()
    
    if query.data == "bonus_daily":
        if user_id in last_daily and now - last_daily[user_id] < 86400:
            await query.answer("Already claimed!", show_alert=True)
            return
        users[user_id]["coins"] += 75000
        last_daily[user_id] = now
        await query.answer("💰 +75,000 coins!", show_alert=True)
    elif query.data == "bonus_weekly":
        if user_id in last_weekly and now - last_weekly[user_id] < 604800:
            await query.answer("Already claimed!", show_alert=True)
            return
        users[user_id]["coins"] += 625000
        last_weekly[user_id] = now
        await query.answer("📅 +625,000 coins!", show_alert=True)
    
    save_data()
    await query.message.delete()

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    user_data = users[user_id]
    await update.message.reply_text(
        f"👤 Profile\n\n"
        f"💰 Coins: {user_data.get('coins', 0)}\n"
        f"🎴 Cards: {len(user_data.get('characters', []))}"
    )

async def mycards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    collection = users[user_id].get("characters", [])
    if not collection:
        await update.message.reply_text("No characters yet 😢")
        return
    text = f"📚 Your Collection ({len(collection)} cards)\n\n"
    for c in collection[:20]:
        text += f"🎴 {c.get('name', '?')} - {rarity_label(c.get('rarity', 1))}\n"
    await update.message.reply_text(text)

async def summon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    cost = 7000
    
    if users[user_id]["coins"] < cost:
        await update.message.reply_text("Not enough coins ❌")
        return
    
    users[user_id]["coins"] -= cost
    if characters:
        character = random.choice(characters)
        users[user_id]["characters"].append(character)
        save_data()
        await update.message.reply_text(f"🎰 You summoned: {character.get('name', '?')}!")
    else:
        await update.message.reply_text("No characters available!")

async def wordle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Wordle game coming soon!")

async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Shop coming soon!")

async def trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Trade system coming soon!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass

# Flask app for webhook
flask_app = Flask(__name__)
application = None

@flask_app.route('/webhook', methods=['POST'])
async def webhook():
    if request.is_json:
        update = Update.de_json(request.get_json(), application.bot)
        await application.process_update(update)
    return 'OK'

@flask_app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port)

async def main():
    global application
    load_data()
    
    TOKEN = os.environ.get("BOT_TOKEN")
    application = Application.builder().token(TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("bonus", bonus))
    application.add_handler(CommandHandler("profile", profile))
    application.add_handler(CommandHandler("collection", mycards))
    application.add_handler(CommandHandler("summon", summon))
    application.add_handler(CommandHandler("wordle", wordle))
    application.add_handler(CommandHandler("shop", shop))
    application.add_handler(CommandHandler("trade", trade))
    application.add_handler(CallbackQueryHandler(bonus_callback, pattern="^bonus_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    await application.initialize()
    
    # Set webhook
    webhook_url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/webhook"
    await application.bot.set_webhook(webhook_url)
    print(f"Webhook set to {webhook_url}")
    
    # Start Flask
    threading.Thread(target=run_flask, daemon=True).start()
    
    print("Bot started!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
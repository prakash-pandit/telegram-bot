from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import random
import time
import logging
import asyncio
import os
from pymongo import MongoClient
from flask import Flask, request, jsonify
import threading

logging.basicConfig(level=logging.INFO)

MONGO_URI = os.environ.get("MONGO_URI")
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["botdb"]
col = db["gamedata"]

users = {}
last_daily = {}
last_weekly = {}
daily_streak = {}
active_wordle = {}
uploaders = set()
characters = []
last_checkin = {}
checkin_streak = {}

def save_data():
    col.replace_one({"_id": "main"}, {
        "_id": "main", "users": users, "uploaders": list(uploaders),
        "last_daily": last_daily, "daily_streak": daily_streak,
        "last_weekly": last_weekly, "active_wordle": active_wordle,
        "characters": characters, "last_checkin": last_checkin,
        "checkin_streak": checkin_streak,
    }, upsert=True)

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
            "characters": [], "coins": 100, "joined": time.time(),
            "name": update.effective_user.first_name if update else "Unknown",
            "username": update.effective_user.username if update else "",
            "wordle_stats": {"played": 0, "won": 0, "lost": 0, "coins_earned": 0, "current_streak": 0, "best_streak": 0}
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    await update.message.reply_text(
        "🎮 Welcome!\n\nCommands:\n/summon — spend coins for a card\n"
        "/bonus — collect daily/weekly coins\n/collection — see your cards\n"
        "/profile — your profile\n/wordle — play wordle\n/shop — buy cards\n/trade — trade with others"
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
    keyboard = [[InlineKeyboardButton(daily_text, callback_data="bonus_daily"),
                 InlineKeyboardButton(weekly_text, callback_data="bonus_weekly")]]
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
    d = users[user_id]
    await update.message.reply_text(f"👤 Profile\n\n💰 Coins: {d.get('coins',0)}\n🎴 Cards: {len(d.get('characters',[]))}")

async def mycards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    collection = users[user_id].get("characters", [])
    if not collection:
        await update.message.reply_text("No characters yet 😢")
        return
    text = f"📚 Your Collection ({len(collection)} cards)\n\n"
    for c in collection[:20]:
        text += f"🎴 {c.get('name','?')} - {rarity_label(c.get('rarity',1))}\n"
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
        await update.message.reply_text(f"🎰 You summoned: {character.get('name','?')}!")
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

# Flask
flask_app = Flask(__name__)
application = None
bot_loop = None

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    # ✅ Return OK immediately so Telegram doesn't timeout
    # Then process update in background
    if application and request.is_json:
        data = request.get_json()
        update = Update.de_json(data, application.bot)
        asyncio.run_coroutine_threadsafe(
            application.process_update(update), bot_loop
        )
    return jsonify({"ok": True}), 200

@flask_app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200

async def run_bot():
    global application, bot_loop
    bot_loop = asyncio.get_event_loop()
    load_data()
    TOKEN = os.environ.get("BOT_TOKEN")
    application = Application.builder().token(TOKEN).build()
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
    await application.start()
    print("✅ Bot started!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8080))
    flask_thread = threading.Thread(
        target=lambda: flask_app.run(host='0.0.0.0', port=PORT),
        daemon=True
    )
    flask_thread.start()
    print(f"✅ Flask running on port {PORT}")
    asyncio.run(run_bot())

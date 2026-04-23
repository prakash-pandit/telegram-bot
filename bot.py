from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters
import random
import time
import logging
import os
from pymongo import MongoClient
from flask import Flask, request, jsonify
import threading

logging.basicConfig(level=logging.INFO)

# MongoDB
MONGO_URI = os.environ.get("MONGO_URI")
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["botdb"]
col = db["gamedata"]

# Data
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

# Handlers
def start(update, context):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    update.message.reply_text(
        "🎮 Welcome!\n\nCommands:\n/summon — spend coins for a card\n"
        "/bonus — collect daily/weekly coins\n/collection — see your cards\n"
        "/profile — your profile\n/wordle — play wordle\n/shop — buy cards\n/trade — trade with others"
    )

def bonus(update, context):
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
    update.message.reply_text("🎁 Bonus Panel", reply_markup=InlineKeyboardMarkup(keyboard))

def bonus_callback(update, context):
    query = update.callback_query
    query.answer()
    user_id = str(query.from_user.id)
    ensure_user(user_id)
    now = time.time()
    if query.data == "bonus_daily":
        if user_id in last_daily and now - last_daily[user_id] < 86400:
            query.answer("Already claimed!", show_alert=True)
            return
        users[user_id]["coins"] += 75000
        last_daily[user_id] = now
        query.answer("💰 +75,000 coins!", show_alert=True)
    elif query.data == "bonus_weekly":
        if user_id in last_weekly and now - last_weekly[user_id] < 604800:
            query.answer("Already claimed!", show_alert=True)
            return
        users[user_id]["coins"] += 625000
        last_weekly[user_id] = now
        query.answer("📅 +625,000 coins!", show_alert=True)
    save_data()
    query.message.delete()

def profile(update, context):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    d = users[user_id]
    update.message.reply_text(f"👤 Profile\n\n💰 Coins: {d.get('coins',0)}\n🎴 Cards: {len(d.get('characters',[]))}")

def mycards(update, context):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    collection = users[user_id].get("characters", [])
    if not collection:
        update.message.reply_text("No characters yet 😢")
        return
    text = f"📚 Your Collection ({len(collection)} cards)\n\n"
    for c in collection[:20]:
        text += f"🎴 {c.get('name','?')} - {rarity_label(c.get('rarity',1))}\n"
    update.message.reply_text(text)

def summon(update, context):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    cost = 7000
    if users[user_id]["coins"] < cost:
        update.message.reply_text("Not enough coins ❌")
        return
    users[user_id]["coins"] -= cost
    if characters:
        character = random.choice(characters)
        users[user_id]["characters"].append(character)
        save_data()
        update.message.reply_text(f"🎰 You summoned: {character.get('name','?')}!")
    else:
        update.message.reply_text("No characters available!")

def wordle(update, context):
    update.message.reply_text("Wordle game coming soon!")

def shop(update, context):
    update.message.reply_text("Shop coming soon!")

def trade(update, context):
    update.message.reply_text("Trade system coming soon!")

def handle_message(update, context):
    pass

# Flask
flask_app = Flask(__name__)
updater = None

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    if updater and request.is_json:
        update = Update.de_json(request.get_json(), updater.bot)
        updater.dispatcher.process_update(update)
    return 'OK'

@flask_app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200

def main():
    global updater
    load_data()

    TOKEN = os.environ.get("BOT_TOKEN")
    PORT = int(os.environ.get("PORT", 8080))

    updater = Updater(TOKEN)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("bonus", bonus))
    dp.add_handler(CommandHandler("profile", profile))
    dp.add_handler(CommandHandler("collection", mycards))
    dp.add_handler(CommandHandler("summon", summon))
    dp.add_handler(CommandHandler("wordle", wordle))
    dp.add_handler(CommandHandler("shop", shop))
    dp.add_handler(CommandHandler("trade", trade))
    dp.add_handler(CallbackQueryHandler(bonus_callback, pattern="^bonus_"))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    # Start Flask first
    flask_thread = threading.Thread(
        target=lambda: flask_app.run(host='0.0.0.0', port=PORT),
        daemon=True
    )
    flask_thread.start()
    print(f"✅ Flask running on port {PORT}")

    # Set webhook
    webhook_url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/webhook"
    updater.bot.set_webhook(webhook_url)
    print(f"✅ Webhook set to {webhook_url}")

    print("✅ Bot started!")
    updater.idle()

if __name__ == "__main__":
    main()

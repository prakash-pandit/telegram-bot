from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import random
import time
import logging
import asyncio
import os
import html
from datetime import datetime
from pymongo import MongoClient

logging.basicConfig(level=logging.INFO)

# MongoDB setup
MONGO_URI = os.environ.get("MONGO_URI")
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["botdb"]
col = db["gamedata"]

# Global variables
users = {}
last_daily = {}
last_weekly = {}
active_wordle = {}
characters = []
last_checkin = {}
checkin_streak = {}
summon_cooldowns = {}

# Config
OWNER_ID = int(os.environ.get("OWNER_ID", "6199312233"))
GROUP_ID = int(os.environ.get("GROUP_ID", "-1003865556551"))
CHECKIN_BASE_REWARD = 2000
CHECKIN_STREAK_BONUS = 500
SHOP_PRICES = {1: 50000, 2: 100000, 3: 200000, 4: 400000, 5: 1000000}

def save_data():
    try:
        col.replace_one({"_id": "main"}, {
            "_id": "main",
            "users": users,
            "last_daily": last_daily,
            "last_weekly": last_weekly,
            "active_wordle": active_wordle,
            "characters": characters,
            "last_checkin": last_checkin,
            "checkin_streak": checkin_streak,
            "summon_cooldowns": summon_cooldowns
        }, upsert=True)
    except Exception as e:
        print(f"Save error: {e}")

def load_data():
    global users, last_daily, last_weekly, active_wordle, characters, last_checkin, checkin_streak, summon_cooldowns
    try:
        data = col.find_one({"_id": "main"})
        if data:
            users = data.get("users", {})
            last_daily = data.get("last_daily", {})
            last_weekly = data.get("last_weekly", {})
            active_wordle = data.get("active_wordle", {})
            characters = data.get("characters", [])
            last_checkin = data.get("last_checkin", {})
            checkin_streak = data.get("checkin_streak", {})
            summon_cooldowns = data.get("summon_cooldowns", {})
            
            # Fix user data structure
            for uid, udata in users.items():
                if "characters" not in udata:
                    udata["characters"] = []
                if "coins" not in udata:
                    udata["coins"] = 100
                if "name" not in udata:
                    udata["name"] = "Unknown"
                if "wordle_stats" not in udata:
                    udata["wordle_stats"] = {"played": 0, "won": 0, "lost": 0, "current_streak": 0, "best_streak": 0, "coins_earned": 0}
    except Exception as e:
        print(f"Load error: {e}")

def ensure_user(user_id, update=None):
    uid = str(user_id)
    if uid not in users:
        users[uid] = {
            "characters": [],
            "coins": 100,
            "joined": time.time(),
            "name": update.effective_user.first_name if update else "Unknown",
            "username": update.effective_user.username if update else "",
            "wordle_stats": {"played": 0, "won": 0, "lost": 0, "current_streak": 0, "best_streak": 0, "coins_earned": 0}
        }
        save_data()
    elif update:
        users[uid]["name"] = update.effective_user.first_name
        users[uid]["username"] = update.effective_user.username or ""

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

def load_words():
    try:
        with open("word.txt", "r") as f:
            words = [w.strip().lower() for w in f.readlines() if len(w.strip()) == 5]
            if words:
                return words
    except:
        pass
    return ["apple", "brain", "crane", "dance", "eagle", "flame", "grape", "heart", "igloo", "jelly"]

def get_wordle_emoji(guess, answer):
    answer_list = list(answer)
    guess_list = list(guess)
    colored = ['⬜'] * 5
    for i in range(5):
        if guess_list[i] == answer_list[i]:
            colored[i] = '🟩'
            answer_list[i] = None
    for i in range(5):
        if colored[i] == '🟩':
            continue
        for j in range(5):
            if answer_list[j] and guess_list[i] == answer_list[j]:
                colored[i] = '🟨'
                answer_list[j] = None
                break
    return ''.join(colored)

# ==================== COMMAND HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    await update.message.reply_text(
        "🎮 **Welcome to Anime Card Bot!**\n\n"
        "📋 **Commands:**\n"
        "/bonus - Daily/Weekly rewards\n"
        "/profile - Your stats\n"
        "/collection - View your cards\n"
        "/summon - Get random card (7000 coins)\n"
        "/wordle - Play Wordle\n"
        "/wstats - Wordle stats\n"
        "/checkin - Daily check-in\n"
        "/help - All commands",
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 **All Commands:**\n\n"
        "💰 **Economy:**\n"
        "/bonus - Daily (75k) & Weekly (625k)\n"
        "/profile - View your profile\n\n"
        "🎴 **Cards:**\n"
        "/summon - Get random card (7000)\n"
        "/collection - Your cards\n\n"
        "🎮 **Games:**\n"
        "/wordle - Play Wordle\n"
        "/wstats - Wordle stats\n"
        "/checkin - Daily check-in (groups only)",
        parse_mode="Markdown"
    )

async def bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    now = time.time()
    
    daily_text = "💰 Daily (75,000)"
    if user_id in last_daily:
        remaining = 86400 - (now - last_daily[user_id])
        if remaining > 0:
            daily_text = f"⏳ Daily ({format_time(remaining)})"
    
    weekly_text = "📅 Weekly (625,000)"
    if user_id in last_weekly:
        remaining = 604800 - (now - last_weekly[user_id])
        if remaining > 0:
            weekly_text = f"⏳ Weekly ({format_time(remaining)})"
    
    keyboard = [[
        InlineKeyboardButton(daily_text, callback_data="bonus_daily"),
        InlineKeyboardButton(weekly_text, callback_data="bonus_weekly")
    ]]
    await update.message.reply_text("🎁 **Bonus Panel**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def bonus_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    ensure_user(user_id)
    now = time.time()
    
    if query.data == "bonus_daily":
        if user_id in last_daily and now - last_daily[user_id] < 86400:
            await query.answer("❌ Already claimed today!", show_alert=True)
            return
        users[user_id]["coins"] = users[user_id].get("coins", 0) + 75000
        last_daily[user_id] = now
        save_data()
        await query.answer("✅ +75,000 coins!", show_alert=True)
    elif query.data == "bonus_weekly":
        if user_id in last_weekly and now - last_weekly[user_id] < 604800:
            await query.answer("❌ Already claimed this week!", show_alert=True)
            return
        users[user_id]["coins"] = users[user_id].get("coins", 0) + 625000
        last_weekly[user_id] = now
        save_data()
        await query.answer("✅ +625,000 coins!", show_alert=True)
    
    await query.message.delete()

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    d = users[user_id]
    stats = d.get("wordle_stats", {})
    
    await update.message.reply_text(
        f"👤 **Profile**\n\n"
        f"🏷 Name: {update.effective_user.first_name}\n"
        f"💰 Coins: {d.get('coins', 0):,}\n"
        f"🎴 Cards: {len(d.get('characters', []))}\n"
        f"🔥 Wordle Streak: {stats.get('current_streak', 0)}\n"
        f"⭐ Best Streak: {stats.get('best_streak', 0)}",
        parse_mode="Markdown"
    )

async def mycards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    collection = users[user_id].get("characters", [])
    
    if not collection:
        await update.message.reply_text("📭 You don't have any cards yet! Use /summon to get one.")
        return
    
    text = f"📚 **Your Collection** ({len(collection)} cards)\n\n"
    for i, c in enumerate(collection[:15], 1):
        text += f"{i}. {c.get('name', '?')} - {rarity_label(c.get('rarity', 1))}\n"
    
    if len(collection) > 15:
        text += f"\n... and {len(collection) - 15} more cards"
    
    await update.message.reply_text(text, parse_mode="Markdown")

async def summon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    now = time.time()
    ensure_user(user_id, update)
    
    if user_id in summon_cooldowns and now - summon_cooldowns[user_id] < 30:
        remaining = 30 - (now - summon_cooldowns[user_id])
        await update.message.reply_text(f"⏳ Please wait {int(remaining)+1} seconds!")
        return
    
    cost = 7000
    if users[user_id].get("coins", 0) < cost:
        await update.message.reply_text(f"❌ Not enough coins! You need {cost:,} coins.", parse_mode="Markdown")
        return
    
    if not characters:
        await update.message.reply_text("❌ No characters available!")
        return
    
    users[user_id]["coins"] -= cost
    summon_cooldowns[user_id] = now
    
    character = random.choice(characters)
    users[user_id]["characters"].append(character)
    save_data()
    
    await update.message.reply_text(
        f"✨ **SUMMON!** ✨\n\n"
        f"🎴 **{character.get('name', '?')}**\n"
        f"⭐ {rarity_label(character.get('rarity', 1))}\n"
        f"💸 Cost: {cost:,} coins",
        parse_mode="Markdown"
    )

async def wordle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    
    if user_id in active_wordle:
        await update.message.reply_text("⚠️ You already have an active Wordle game! Type /cwordle to cancel.")
        return
    
    words = load_words()
    secret = random.choice(words)
    active_wordle[user_id] = {"answer": secret, "guesses": []}
    save_data()
    
    await update.message.reply_text(
        "🎮 **WORDLE STARTED!**\n\n"
        "📝 Type any 5-letter word to guess\n"
        "🟩 = Correct letter, correct position\n"
        "🟨 = Correct letter, wrong position\n"
        "⬜ = Letter not in word\n\n"
        "You have 6 attempts!\n"
        "Type /cwordle to cancel",
        parse_mode="Markdown"
    )

async def cwordle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    if user_id in active_wordle:
        answer = active_wordle[user_id]["answer"]
        del active_wordle[user_id]
        save_data()
        await update.message.reply_text(f"❌ Game cancelled!\nThe word was: **{answer.upper()}**", parse_mode="Markdown")
    else:
        await update.message.reply_text("No active Wordle game!")

async def wstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    stats = users[user_id].get("wordle_stats", {})
    
    await update.message.reply_text(
        f"📊 **Your Wordle Stats**\n\n"
        f"🎮 Played: {stats.get('played', 0)}\n"
        f"🏆 Won: {stats.get('won', 0)}\n"
        f"❌ Lost: {stats.get('lost', 0)}\n"
        f"🔥 Current Streak: {stats.get('current_streak', 0)}\n"
        f"⭐ Best Streak: {stats.get('best_streak', 0)}",
        parse_mode="Markdown"
    )

async def checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_type = update.effective_chat.type
    if chat_type not in ["group", "supergroup"]:
        await update.message.reply_text("❌ Check-in only works in groups!")
        return
    
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    
    now = time.time()
    today = datetime.now().strftime("%Y-%m-%d")
    
    last = last_checkin.get(user_id, {})
    last_date = last.get("date", "")
    
    if last_date == today:
        await update.message.reply_text("⏳ You already checked in today! Come back tomorrow.")
        return
    
    # Calculate streak
    yesterday = datetime.now().timestamp() - 86400
    yesterday_str = datetime.fromtimestamp(yesterday).strftime("%Y-%m-%d")
    
    streak = checkin_streak.get(user_id, 0)
    if last_date == yesterday_str:
        streak += 1
    else:
        streak = 1
    
    checkin_streak[user_id] = streak
    bonus = CHECKIN_STREAK_BONUS * (streak - 1)
    total = CHECKIN_BASE_REWARD + bonus
    
    users[user_id]["coins"] = users[user_id].get("coins", 0) + total
    last_checkin[user_id] = {"date": today, "timestamp": now}
    save_data()
    
    await update.message.reply_text(
        f"✅ **Check-in Successful!**\n\n"
        f"💰 +{total:,} coins\n"
        f"📅 Streak: {streak} days\n"
        f"⚡ Bonus: +{bonus:,}",
        parse_mode="Markdown"
    )

# ==================== MESSAGE HANDLER ====================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    
    user_id = str(update.effective_user.id)
    text = update.message.text.strip().lower()
    
    # Wordle handling
    if len(text) == 5 and text.isalpha() and user_id in active_wordle:
        ensure_user(user_id, update)
        game = active_wordle[user_id]
        answer = game["answer"]
        
        emoji = get_wordle_emoji(text, answer)
        game["guesses"].append((text, emoji))
        save_data()
        
        board = "\n".join(f"{e}  {g.upper()}" for g, e in game["guesses"])
        
        if text == answer:
            guess_count = len(game["guesses"])
            coins_map = {1: 25000, 2: 15000, 3: 10000, 4: 5000, 5: 2000, 6: 1000}
            coins_earned = coins_map.get(guess_count, 1000)
            
            stats = users[user_id]["wordle_stats"]
            stats["played"] = stats.get("played", 0) + 1
            stats["won"] = stats.get("won", 0) + 1
            stats["current_streak"] = stats.get("current_streak", 0) + 1
            if stats["current_streak"] > stats.get("best_streak", 0):
                stats["best_streak"] = stats["current_streak"]
            
            users[user_id]["coins"] = users[user_id].get("coins", 0) + coins_earned
            stats["coins_earned"] = stats.get("coins_earned", 0) + coins_earned
            
            del active_wordle[user_id]
            save_data()
            
            await update.message.reply_text(
                f"🎉 **WORDLE COMPLETE!** 🎉\n\n"
                f"{board}\n\n"
                f"🏆 Guesses: {guess_count}\n"
                f"💰 +{coins_earned:,} coins\n"
                f"🔥 Streak: {stats['current_streak']}",
                parse_mode="Markdown"
            )
        elif len(game["guesses"]) >= 6:
            stats = users[user_id]["wordle_stats"]
            stats["played"] = stats.get("played", 0) + 1
            stats["lost"] = stats.get("lost", 0) + 1
            stats["current_streak"] = 0
            
            del active_wordle[user_id]
            save_data()
            
            await update.message.reply_text(
                f"❌ **GAME OVER!**\n\n"
                f"{board}\n\n"
                f"The word was: **{answer.upper()}**",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                f"{board}\n\n📝 Attempts left: {6 - len(game['guesses'])}",
                parse_mode="Markdown"
            )

# ==================== MAIN ====================

async def main():
    print("Loading data from MongoDB...")
    load_data()
    
    TOKEN = os.environ.get("BOT_TOKEN")
    if not TOKEN:
        print("ERROR: BOT_TOKEN environment variable not set!")
        return
    
    print("Starting bot...")
    
    # Create application (NO Updater - using Application directly)
    application = Application.builder().token(TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("bonus", bonus))
    application.add_handler(CommandHandler("profile", profile))
    application.add_handler(CommandHandler("collection", mycards))
    application.add_handler(CommandHandler("summon", summon))
    application.add_handler(CommandHandler("wordle", wordle))
    application.add_handler(CommandHandler("cwordle", cwordle))
    application.add_handler(CommandHandler("wstats", wstats))
    application.add_handler(CommandHandler("checkin", checkin))
    application.add_handler(CallbackQueryHandler(bonus_callback, pattern="^bonus_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Start polling
    print("Bot is running with polling...")
    await application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    asyncio.run(main())
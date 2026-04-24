from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import random
import time
import logging
import asyncio
import os
import html
import json
from datetime import datetime, timedelta
from pymongo import MongoClient
from flask import Flask, request, jsonify
import threading
import re

logging.basicConfig(level=logging.INFO)

# ─── CONFIG ───
TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")
OWNER_ID = int(os.environ.get("OWNER_ID", "6199312233"))
GROUP_ID = int(os.environ.get("GROUP_ID", "-1003865556551"))
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", "-1003995927727"))

CHECKIN_BASE_REWARD = 2000
CHECKIN_STREAK_BONUS = 500
SHOP_PRICES = {1: 50000, 2: 100000, 3: 200000, 4: 400000, 5: 1000000}
SPAWN_EVERY = 20
BID_DURATION = 60
BID_EXTEND = 10
TRADE_TIMEOUT = 120
USER_MSG_COOLDOWN = 5
MIN_MSG_LENGTH = 1
ITEMS_PER_PAGE = 5
SHOP_ITEMS_PER_PAGE = 3

# ─── MONGODB ───
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["botdb"]
col = db["gamedata"]

# ─── GLOBAL STATE ───
users = {}
last_daily = {}
last_weekly = {}
active_wordle = {}
characters = []
last_checkin = {}
checkin_streak = {}
summon_cooldowns = {}
uploaders = set()
active_bid = {}
active_trades = {}
active_guess = {}
group_message_counts = {}
last_message_time = {}
last_message_text = {}
user_locks = {}
spawn_counter = 0

# ─── WORD LIST FOR SECRET WORDS ONLY ───
WORD_LIST = None

def load_words_once():
    global WORD_LIST
    if WORD_LIST is not None:
        return WORD_LIST
    try:
        with open("word.txt", "r") as f:
            words = [w.strip().lower() for w in f if len(w.strip()) == 5 and w.strip().isalpha()]
        if words:
            WORD_LIST = words
            return WORD_LIST
    except Exception:
        pass
    WORD_LIST = ["apple", "brain", "crane", "dance", "eagle", "flame",
                 "grape", "heart", "igloo", "jelly", "knife", "lemon",
                 "mango", "night", "ocean", "piano", "queen", "river",
                 "stone", "tiger", "umbra", "vigor", "water", "xenon", "yacht", "zebra"]
    return WORD_LIST

# ─── SAVE / LOAD with JSON Backup ───
def save_data():
    global users, last_daily, last_weekly, active_wordle, characters
    global last_checkin, checkin_streak, uploaders, spawn_counter
    
    data = {
        "_id": "main",
        "users": users,
        "last_daily": last_daily,
        "last_weekly": last_weekly,
        "active_wordle": active_wordle,
        "characters": characters,
        "last_checkin": last_checkin,
        "checkin_streak": checkin_streak,
        "uploaders": list(uploaders),
        "spawn_counter": spawn_counter
    }
    
    try:
        col.replace_one({"_id": "main"}, data, upsert=True)
        logging.info("Data saved to MongoDB")
    except Exception as e:
        logging.error(f"MongoDB save error: {e}")
        try:
            with open("backup.json", "w") as f:
                json.dump(data, f, default=str)
            logging.info("Data saved to JSON backup")
        except Exception as e2:
            logging.error(f"JSON backup failed: {e2}")

def load_data():
    global users, last_daily, last_weekly, active_wordle, characters
    global last_checkin, checkin_streak, uploaders, spawn_counter
    
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
            uploaders = set(map(str, data.get("uploaders", [])))
            spawn_counter = data.get("spawn_counter", 0)

            for uid, udata in users.items():
                udata.setdefault("characters", [])
                udata.setdefault("coins", 100)
                udata.setdefault("name", "Unknown")
                udata.setdefault("username", "")
                udata.setdefault("joined", time.time())
                udata.setdefault("wordle_stats", {
                    "played": 0, "won": 0, "lost": 0,
                    "current_streak": 0, "best_streak": 0,
                    "coins_earned": 0, "total_guesses": 0, "best_win": 0
                })
                udata.setdefault("wordle_wins", {"today": 0, "week": 0, "month": 0, "alltime": 0})
                udata.setdefault("wordle_last_date", {})
            logging.info("Data loaded from MongoDB")
        else:
            try:
                with open("backup.json", "r") as f:
                    data = json.load(f)
                if data:
                    users = data.get("users", {})
                    characters = data.get("characters", [])
                    spawn_counter = data.get("spawn_counter", 0)
                    logging.info("Data loaded from JSON backup")
            except Exception:
                pass
    except Exception as e:
        logging.error(f"Load error: {e}")

# ─── USER LOCK HELPER ───
async def get_user_lock(user_id):
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]

# ─── CLEANUP OLD DATA ───
async def cleanup_old_data():
    while True:
        await asyncio.sleep(3600)
        now = time.time()
        keys_to_delete = []
        for key, last_time in last_message_time.items():
            if now - last_time > 3600:
                keys_to_delete.append(key)
        for key in keys_to_delete:
            del last_message_time[key]
            if key in last_message_text:
                del last_message_text[key]
        if keys_to_delete:
            logging.info(f"Cleaned {len(keys_to_delete)} old message entries")

# ─── HELPERS ───

def ensure_user(user_id, update=None):
    uid = str(user_id)
    if uid not in users:
        users[uid] = {
            "characters": [], "coins": 100, "joined": time.time(),
            "name": update.effective_user.first_name if update else "Unknown",
            "username": (update.effective_user.username or "") if update else "",
            "wordle_stats": {
                "played": 0, "won": 0, "lost": 0, "current_streak": 0,
                "best_streak": 0, "coins_earned": 0, "total_guesses": 0, "best_win": 0
            },
            "wordle_wins": {"today": 0, "week": 0, "month": 0, "alltime": 0},
            "wordle_last_date": {}
        }
    elif update:
        users[uid]["name"] = update.effective_user.first_name
        users[uid]["username"] = update.effective_user.username or ""

    users[uid].setdefault("wordle_stats", {
        "played": 0, "won": 0, "lost": 0, "current_streak": 0,
        "best_streak": 0, "coins_earned": 0, "total_guesses": 0, "best_win": 0
    })
    users[uid].setdefault("wordle_wins", {"today": 0, "week": 0, "month": 0, "alltime": 0})
    users[uid].setdefault("wordle_last_date", {})

def format_time(seconds):
    seconds = int(seconds)
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    if days > 0: return f"{days}d {hours}h"
    if hours > 0: return f"{hours}h {minutes}m"
    return f"{minutes}m"

def rarity_label(rarity):
    return {1: "⚪ Common", 2: "🔵 Rare", 3: "🟣 Epic",
            4: "🟡 Legendary", 5: "⚡ Celebrity"}.get(rarity, "Unknown")

def next_card_id():
    if not characters:
        return "0001"
    ids = []
    for c in characters:
        try:
            ids.append(int(c.get("card_id", "0")))
        except (ValueError, TypeError):
            pass
    return str(max(ids) + 1).zfill(4) if ids else "0001"

def get_wordle_emoji_list(guess, answer):
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
    return colored

def format_wordle_board(guesses):
    lines = []
    for word, emojis in guesses:
        word_upper = word.upper()
        emoji_line = ' '.join(emojis)
        letter_line = ' '.join(word_upper)
        lines.append(f"{emoji_line}\n{letter_line}")
    return '\n\n'.join(lines)

def get_user_rank(user_id, type="coins"):
    if type == "coins":
        sorted_users = sorted(users.items(), key=lambda x: x[1].get("coins", 0), reverse=True)
    else:
        sorted_users = sorted(users.items(), key=lambda x: x[1].get("wordle_wins", {}).get("alltime", 0), reverse=True)
    
    for i, (uid, _) in enumerate(sorted_users, 1):
        if uid == str(user_id):
            return i
    return None

# ─── SHOP FUNCTIONS ───
async def send_shop_page(chat_id, context, user_id):
    page = context.user_data.get("shop_page", 0)
    all_chars = context.user_data.get("shop_list", characters)
    start = page * SHOP_ITEMS_PER_PAGE
    page_items = all_chars[start:start + SHOP_ITEMS_PER_PAGE]
    total_pages = max(1, (len(all_chars) - 1) // SHOP_ITEMS_PER_PAGE + 1)
    
    coins = users[user_id].get("coins", 0)
    rarity_map = {1: "⚪ Common", 2: "🔵 Rare", 3: "🟣 Epic", 4: "🟡 Legendary", 5: "⚡ Celebrity"}
    
    text = f"🏪 <b>CHARACTER SHOP</b>\n💰 Your coins: <b>{coins:,}</b>\n📄 Page {page+1}/{total_pages}\n\n"
    
    keyboard = []
    for char in page_items:
        rarity = char.get("rarity", 1)
        price = SHOP_PRICES.get(rarity, 50000)
        rl = rarity_map.get(rarity, "?")
        mark = "✅" if coins >= price else "❌"
        text += f"{mark} <b>{html.escape(char.get('name','?'))}</b> — {rl}\n   💰 {price:,} | 🪪 #{char.get('card_id','?')}\n\n"
        keyboard.append([InlineKeyboardButton(
            f"Buy {char.get('name', '?')[:15]} ({price:,})",
            callback_data=f"shop_buy_{char.get('card_id','')}"
        )])
    
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Previous", callback_data="shop_prev"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data="shop_next"))
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("❌ Close", callback_data="shop_close")])
    
    try:
        if page_items and page_items[0].get("file_id"):
            await context.bot.send_photo(
                chat_id=chat_id, photo=page_items[0]["file_id"],
                caption=text, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id, text=text, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    except Exception:
        await context.bot.send_message(
            chat_id=chat_id, text=text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# ─── WORDLE LEADERBOARD WITH CATEGORIES ───
async def wtop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📅 Daily", callback_data="wtop_daily"),
         InlineKeyboardButton("📆 Weekly", callback_data="wtop_weekly")],
        [InlineKeyboardButton("🏆 All Time", callback_data="wtop_alltime"),
         InlineKeyboardButton("❌ Close", callback_data="wtop_close")]
    ]
    await update.message.reply_text(
        "🏆 <b>Wordle Leaderboard</b>\n\nSelect a time period:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def wtop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "wtop_close":
        await query.message.delete()
        return
    
    period = data.split("_")[1]
    
    user_wins = []
    now = datetime.now()
    
    for uid, udata in users.items():
        wins = udata.get("wordle_wins", {})
        if period == "daily":
            last_date = udata.get("wordle_last_date", {}).get("daily", "")
            if last_date == now.strftime("%Y-%m-%d"):
                count = wins.get("today", 0)
            else:
                count = 0
        elif period == "weekly":
            count = wins.get("week", 0)
        else:
            count = wins.get("alltime", 0)
        
        if count > 0:
            name = udata.get("name", "Unknown")
            username = udata.get("username", "")
            display = f"@{username}" if username else html.escape(name)
            user_wins.append((display, count))
    
    user_wins.sort(key=lambda x: x[1], reverse=True)
    
    period_names = {"daily": "📅 Daily", "weekly": "📆 Weekly", "alltime": "🏆 All Time"}
    text = f"{period_names[period]} Wordle Leaderboard\n\n"
    
    for i, (display, wins) in enumerate(user_wins[:10], 1):
        text += f"{i}. {display} — {wins} wins\n"
    
    if not user_wins:
        text += "No wins recorded yet!"
    
    keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data=f"wtop_{period}"),
                 InlineKeyboardButton("❌ Close", callback_data="wtop_close")]]
    
    await query.edit_message_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ─── COMMANDS ───

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    save_data()
    await update.message.reply_text(
        "🎮 <b>Welcome to Anime Card Bot!</b>\n\n"
        "📋 <b>Commands:</b>\n"
        "/bonus - Daily/Weekly rewards\n"
        "/profile - Your stats\n"
        "/collection - View your cards\n"
        "/summon - Get random card (7000 coins)\n"
        "/shop - Buy cards\n"
        "/trade - Trade cards\n"
        "/wordle - Play Wordle\n"
        "/wstats - Wordle stats\n"
        "/wtop - Wordle leaderboard\n"
        "/ctop - Coin leaderboard\n"
        "/checkin - Daily group check-in\n"
        "/checkintop - Check-in streak leaderboard\n"
        "/help - All commands",
        parse_mode="HTML"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 <b>All Commands:</b>\n\n"
        "💰 <b>Economy:</b>\n"
        "/bonus - Daily (75k) &amp; Weekly (625k)\n"
        "/profile - View profile\n"
        "/checkin - Daily check-in (group only)\n"
        "/checkintop - Check-in streak leaderboard\n\n"
        "🎴 <b>Cards:</b>\n"
        "/summon - Get random card (7000)\n"
        "/collection - Your cards\n"
        "/shop - Buy cards\n"
        "/favourite [id] - Favourite a card\n"
        "/favorites - View favourites\n"
        "/cardinfo [id] - Card details\n"
        "/trade [id] - Trade a card\n"
        "/offer [id] - Counter-offer in trade\n"
        "/canceltrade - Cancel active trade\n\n"
        "🎮 <b>Games:</b>\n"
        "/wordle - Play Wordle (ANY 5-letter word works!)\n"
        "/cwordle - Cancel Wordle\n"
        "/wstats - Wordle stats\n"
        "/wtop - Wordle leaderboard\n"
        "/ctop - Coin leaderboard\n\n"
        "👑 <b>Admin:</b>\n"
        "/upload - Upload character (uploaders)\n"
        "/adduploader - Add uploader (owner)\n"
        "/give - Give cards (owner)\n"
        "/broadcast - Announce (owner)",
        parse_mode="HTML"
    )

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    d = users[user_id]
    stats = d.get("wordle_stats", {})
    
    coin_rank = get_user_rank(user_id, "coins")
    wordle_rank = get_user_rank(user_id, "wordle")
    
    text = (
        f"👤 <b>Profile</b>\n\n"
        f"🏷 Name: {html.escape(update.effective_user.first_name)}\n"
        f"💰 Coins: {d.get('coins', 0):,} (Rank #{coin_rank})\n"
        f"🎴 Cards: {len(d.get('characters', []))}\n"
        f"🔥 Wordle Streak: {stats.get('current_streak', 0)}\n"
        f"⭐ Best Streak: {stats.get('best_streak', 0)}\n"
        f"🏆 Wordle Rank: #{wordle_rank if wordle_rank else 'N/A'}"
    )
    
    try:
        photos = await context.bot.get_user_profile_photos(update.effective_user.id, limit=1)
        if photos.total_count > 0:
            photo = photos.photos[0][-1].file_id
            await update.message.reply_photo(photo=photo, caption=text, parse_mode="HTML")
        else:
            await update.message.reply_text(text, parse_mode="HTML")
    except Exception:
        await update.message.reply_text(text, parse_mode="HTML")

async def send_collection_page(chat_id, context, user_id):
    collection = context.user_data.get("col_list", [])
    page = context.user_data.get("col_page", 0)
    start = page * ITEMS_PER_PAGE
    page_items = collection[start:start + ITEMS_PER_PAGE]
    total_pages = max(1, (len(collection) - 1) // ITEMS_PER_PAGE + 1)
    rarity_map = {1: "⚪", 2: "🔵", 3: "🟣", 4: "🟡", 5: "⚡"}

    text = f"<b>📚 {html.escape(users[user_id]['name'])}'s Collection</b>\n"
    text += f"Page {page+1}/{total_pages} | Total: {len(collection)} cards\n\n"

    for char in page_items:
        if not isinstance(char, dict):
            continue
        rl = rarity_map.get(char.get("rarity", 1), "❓")
        name = html.escape(char.get("name", "?"))
        anime = html.escape(char.get("anime", "?"))
        cid = char.get("card_id", "????")
        fav = "⭐ " if char.get("favourite") else ""
        text += f"{fav}{rl} <b>{name}</b>\n🎬 {anime} | 🪪 #{cid}\n\n"

    if not page_items:
        await context.bot.send_message(chat_id=chat_id, text="No cards on this page.")
        return

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data="col_prev"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️ Next", callback_data="col_next"))
    keyboard = []
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("❌ Close", callback_data="col_close")])

    photo = page_items[0].get("file_id")
    try:
        await context.bot.send_photo(
            chat_id=chat_id, photo=photo, caption=text,
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception:
        await context.bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def collection_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    if query.data == "col_close":
        await query.message.delete()
        return
    elif query.data == "col_next":
        context.user_data["col_page"] = context.user_data.get("col_page", 0) + 1
    elif query.data == "col_prev":
        context.user_data["col_page"] = max(0, context.user_data.get("col_page", 0) - 1)
    total = len(context.user_data.get("col_list", []))
    max_page = max(0, (total - 1) // ITEMS_PER_PAGE)
    context.user_data["col_page"] = min(context.user_data["col_page"], max_page)
    await query.message.delete()
    await send_collection_page(query.message.chat_id, context, user_id)

async def mycards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    collection = users[user_id].get("characters", [])
    if not collection:
        await update.message.reply_text("📭 You have no cards yet! Use /summon to get one.")
        return
    query_text = " ".join(context.args).lower() if context.args else ""
    if query_text and query_text not in ["common", "rare", "epic", "legendary", "celebrity"]:
        collection = [c for c in collection if isinstance(c, dict) and query_text in c.get("name", "").lower()]
    elif query_text in ["common", "rare", "epic", "legendary", "celebrity"]:
        r = {"common": 1, "rare": 2, "epic": 3, "legendary": 4, "celebrity": 5}[query_text]
        collection = [c for c in collection if isinstance(c, dict) and c.get("rarity") == r]
    if not collection:
        await update.message.reply_text("No matching cards found!")
        return
    context.user_data["col_list"] = collection
    context.user_data["col_page"] = 0
    await send_collection_page(update.effective_chat.id, context, user_id)

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
    name = html.escape(update.effective_user.first_name)
    await update.message.reply_text(
        f"🎁 <b>Bonus Panel</b>\n\n👤 {name}\n\nChoose your reward:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

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

    name = html.escape(query.from_user.first_name)
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
    try:
        await query.edit_message_text(
            f"🎁 <b>Bonus Panel</b>\n\n👤 {name}\n\nChoose your reward:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception:
        pass

async def summon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    now = time.time()
    ensure_user(user_id, update)
    cost = 7000

    if user_id in summon_cooldowns and now - summon_cooldowns[user_id] < 30:
        remaining = 30 - (now - summon_cooldowns[user_id])
        await update.message.reply_text(f"⏳ Please wait {int(remaining)+1} seconds!")
        return

    async with await get_user_lock(user_id):
        if users[user_id].get("coins", 0) < cost:
            coins = users[user_id].get("coins", 0)
            await update.message.reply_text(
                f"❌ <b>Not enough coins!</b>\n\n"
                f"💰 Need: {cost:,} coins\n"
                f"💳 You have: {coins:,} coins\n"
                f"💡 Tip: Use /bonus or /checkin to earn free coins!",
                parse_mode="HTML"
            )
            return

        if not characters:
            await update.message.reply_text("❌ No characters available yet!")
            return

        users[user_id]["coins"] -= cost
        summon_cooldowns[user_id] = now

        rarity_weights = {1: 55, 2: 25, 3: 10, 4: 8, 5: 2}
        rarity = random.choices(list(rarity_weights.keys()), weights=rarity_weights.values())[0]
        pool = [c for c in characters if c.get("rarity") == rarity]
        if not pool:
            pool = characters

        character = random.choice(pool)
        users[user_id]["characters"].append(character)
        save_data()

    rl = rarity_label(character.get("rarity", 1))
    caption = (
        f"✨ <b>NEW SUMMON!</b> ✨\n\n"
        f"🎴 <b>{html.escape(character.get('name','?'))}</b>\n"
        f"🎬 {html.escape(character.get('anime','?'))}\n"
        f"⭐ {rl}\n"
        f"🪪 #{character.get('card_id','????')}\n"
        f"💸 Cost: {cost:,} coins"
    )

    try:
        await update.message.reply_photo(photo=character["file_id"], caption=caption, parse_mode="HTML")
    except Exception:
        await update.message.reply_text(caption, parse_mode="HTML")

# ─── SHOP COMMAND ───
async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)

    if not characters:
        await update.message.reply_text("🏪 Shop is empty right now!")
        return

    context.user_data["shop_page"] = 0
    context.user_data["shop_list"] = characters.copy()
    await send_shop_page(update.effective_chat.id, context, user_id)

async def shop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    ensure_user(user_id)

    if query.data == "shop_close":
        await query.message.delete()
        return
    
    if query.data == "shop_next":
        context.user_data["shop_page"] = context.user_data.get("shop_page", 0) + 1
        await query.message.delete()
        await send_shop_page(query.message.chat_id, context, user_id)
        return
    
    if query.data == "shop_prev":
        context.user_data["shop_page"] = max(0, context.user_data.get("shop_page", 0) - 1)
        await query.message.delete()
        await send_shop_page(query.message.chat_id, context, user_id)
        return

    if query.data.startswith("shop_buy_"):
        card_id = query.data[len("shop_buy_"):]
        char = next((c for c in characters if str(c.get("card_id")) == str(card_id)), None)
        if not char:
            await query.answer("❌ Character not found!", show_alert=True)
            return
        price = SHOP_PRICES.get(char.get("rarity", 1), 50000)
        
        async with await get_user_lock(user_id):
            coins = users[user_id].get("coins", 0)
            if coins < price:
                await query.answer(
                    f"❌ Need {price:,}, you have {coins:,}. Use /bonus or /checkin!",
                    show_alert=True
                )
                return
            users[user_id]["coins"] -= price
            users[user_id]["characters"].append(char.copy())
            save_data()
        
        await query.message.reply_text(
            f"✅ <b>PURCHASE COMPLETE!</b>\n\n"
            f"🎴 {html.escape(char.get('name','?'))}\n"
            f"⭐ {rarity_label(char.get('rarity',1))}\n"
            f"🪪 #{char.get('card_id','?')}\n"
            f"💸 Paid: {price:,} coins",
            parse_mode="HTML"
        )
        await query.answer(f"✅ Bought {char.get('name', '?')}!", show_alert=True)

# ─── WORDLE ───

async def wordle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)

    if user_id in active_wordle:
        game = active_wordle[user_id]
        board = format_wordle_board(game["guesses"])
        await update.message.reply_text(
            f"⚠️ You already have an active game!\n\n{board}\n\n"
            f"Attempts left: {6 - len(game['guesses'])}\n"
            "Type /cwordle to cancel.",
            parse_mode="HTML"
        )
        return

    words = load_words_once()
    secret = random.choice(words).lower()
    active_wordle[user_id] = {"answer": secret, "guesses": []}
    save_data()

    await update.message.reply_text(
        "🎮 <b>WORDLE STARTED!</b>\n\n"
        "⚠️ <b>ANY 5-letter word works!</b> (ABCDE, ZZZZZ, HELLO, etc.)\n\n"
        "Type any 5-letter combination to guess!\n\n"
        "🟩 = Right letter, right spot\n"
        "🟨 = Right letter, wrong spot\n"
        "⬜ = Not in word\n\n"
        "6 attempts. Type /cwordle to cancel.",
        parse_mode="HTML"
    )

async def cwordle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id in active_wordle:
        answer = active_wordle.pop(user_id)["answer"]
        save_data()
        await update.message.reply_text(f"❌ Game cancelled! The word was: <b>{answer.upper()}</b>", parse_mode="HTML")
    else:
        await update.message.reply_text("You have no active Wordle game!")

async def wstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
        user_id = str(target.id)
        name = html.escape(target.first_name)
    else:
        user_id = str(update.effective_user.id)
        name = html.escape(update.effective_user.first_name)

    if user_id not in users:
        await update.message.reply_text("No stats yet!")
        return

    ensure_user(user_id)
    stats = users[user_id].get("wordle_stats", {})
    played = stats.get("played", 0)
    won = stats.get("won", 0)
    win_rate = round((won / played) * 100) if played > 0 else 0

    await update.message.reply_text(
        f"📊 <b>Wordle Stats — {name}</b>\n\n"
        f"🎮 Played: {played}\n"
        f"🏆 Won: {won}\n"
        f"❌ Lost: {stats.get('lost', 0)}\n"
        f"🎯 Win Rate: {win_rate}%\n"
        f"🔥 Current Streak: {stats.get('current_streak', 0)}\n"
        f"⭐ Best Streak: {stats.get('best_streak', 0)}\n"
        f"💰 Coins Earned: {stats.get('coins_earned', 0):,}",
        parse_mode="HTML"
    )

async def ctop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not users:
        await update.message.reply_text("No players yet!")
        return
    sorted_users = sorted(users.items(), key=lambda x: x[1].get("coins", 0), reverse=True)
    text = "💰 <b>Coin Leaderboard</b>\n\n"
    for i, (uid, d) in enumerate(sorted_users[:10], 1):
        name = d.get("name") or "Unknown"
        username = d.get("username", "")
        display = f"@{username}" if username else html.escape(name)
        text += f"{i}. {display} — 💰 {d.get('coins',0):,}\n"
    await update.message.reply_text(text, parse_mode="HTML")

# ─── CHECK-IN ───

async def checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("❌ Check-in only works in groups!")
        return

    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    now = time.time()
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday_str = datetime.fromtimestamp(now - 86400).strftime("%Y-%m-%d")

    last = last_checkin.get(user_id, {})
    last_date = last.get("date", "")

    if last_date == today:
        await update.message.reply_text("⏳ Already checked in today! Come back tomorrow.")
        return

    streak = checkin_streak.get(user_id, 0)
    streak = streak + 1 if last_date == yesterday_str else 1
    checkin_streak[user_id] = streak

    bonus_coins = CHECKIN_STREAK_BONUS * (streak - 1)
    total = CHECKIN_BASE_REWARD + bonus_coins
    users[user_id]["coins"] = users[user_id].get("coins", 0) + total
    last_checkin[user_id] = {"date": today, "timestamp": now}
    save_data()

    name = html.escape(update.effective_user.first_name)
    await update.message.reply_text(
        f"✅ <b>{name} checked in!</b>\n\n"
        f"💰 +{total:,} coins\n"
        f"📅 Streak: {streak} days\n"
        f"⚡ Streak bonus: +{bonus_coins:,}",
        parse_mode="HTML"
    )

async def checkintop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show leaderboard of users with highest check-in streaks"""
    if not checkin_streak:
        await update.message.reply_text("📅 No check-in records yet!\nUse /checkin to start your streak!")
        return
    
    sorted_streaks = sorted(checkin_streak.items(), key=lambda x: x[1], reverse=True)
    
    text = "📅 <b>CHECK-IN STREAK LEADERBOARD</b>\n\n"
    text += "🏆 <b>Top Streaks:</b>\n"
    
    rank = 1
    added = 0
    for uid, streak in sorted_streaks:
        if streak > 0 and added < 10:
            user_data = users.get(uid, {})
            name = user_data.get("name", "Unknown")
            username = user_data.get("username", "")
            
            last = last_checkin.get(uid, {})
            last_date = last.get("date", "Never")
            
            display = f"@{username}" if username else html.escape(name)
            text += f"{rank}. {display} — 🔥 {streak} days (Last: {last_date})\n"
            rank += 1
            added += 1
    
    if added == 0:
        text += "No active streaks yet!\n"
    
    user_id = str(update.effective_user.id)
    user_streak = checkin_streak.get(user_id, 0)
    if user_streak > 0:
        user_rank = 1
        for uid, streak in sorted_streaks:
            if uid == user_id:
                break
            user_rank += 1
        
        text += f"\n📌 <b>Your Rank:</b> #{user_rank} with {user_streak} day streak!"
    else:
        text += f"\n📌 <b>You:</b> No active streak. Use /checkin to start!"
    
    text += f"\n\n💡 <b>Streak Rewards:</b>\n"
    text += f"• Base: +{CHECKIN_BASE_REWARD:,} coins\n"
    text += f"• Bonus: +{CHECKIN_STREAK_BONUS:,} coins per day\n"
    text += f"• Example: 7-day streak = {CHECKIN_BASE_REWARD + (CHECKIN_STREAK_BONUS * 6):,} coins!"
    
    await update.message.reply_text(text, parse_mode="HTML")

# ─── FAVOURITE ───

async def favourite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    if not context.args:
        await update.message.reply_text("Usage: /favourite [card_id]")
        return
    card_id = context.args[0].strip().lstrip("#").zfill(4)
    
    async with await get_user_lock(user_id):
        collection = users[user_id].get("characters", [])
        card = next((c for c in collection if str(c.get("card_id","")).zfill(4) == card_id), None)
        if not card:
            await update.message.reply_text(f"❌ Card #{card_id} not found in your collection.")
            return
        card["favourite"] = not card.get("favourite", False)
        save_data()
    
    status = "added to ⭐ favourites" if card["favourite"] else "removed from favourites"
    await update.message.reply_text(f"<b>{html.escape(card.get('name','?'))}</b> {status}!", parse_mode="HTML")

async def myfavourites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    favs = [c for c in users[user_id].get("characters", []) if isinstance(c, dict) and c.get("favourite")]
    if not favs:
        await update.message.reply_text("No favourited cards yet! Use /favourite [card_id]")
        return
    text = f"⭐ <b>Your Favourites ({len(favs)})</b>\n\n"
    for c in favs:
        text += f"🎴 {html.escape(c.get('name','?'))} — #{c.get('card_id','?')} [{rarity_label(c.get('rarity',1))}]\n"
    await update.message.reply_text(text, parse_mode="HTML")

# ─── CARDINFO ───

async def cardinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /cardinfo [card_id]")
        return
    card_id = context.args[0].strip().lstrip("#").zfill(4)
    char = next((c for c in characters if str(c.get("card_id","")).zfill(4) == card_id), None)
    if not char:
        await update.message.reply_text(f"❌ Card #{card_id} not found.")
        return
    owners = sum(
        1 for uid, udata in users.items()
        if any(str(c.get("card_id","")) == card_id for c in udata.get("characters",[]) if isinstance(c, dict))
    )
    caption = (
        f"🎴 <b>Card Info</b>\n\n"
        f"🪪 ID: #{card_id}\n"
        f"👤 Name: {html.escape(char.get('name','?'))}\n"
        f"🎬 Anime: {html.escape(char.get('anime','?'))}\n"
        f"⭐ Rarity: {rarity_label(char.get('rarity',1))}\n"
        f"💰 Shop Price: {SHOP_PRICES.get(char.get('rarity',1),0):,}\n"
        f"👥 Owners: {owners}"
    )
    try:
        await update.message.reply_photo(photo=char["file_id"], caption=caption, parse_mode="HTML")
    except Exception:
        await update.message.reply_text(caption, parse_mode="HTML")

# ─── TRADE ───

async def trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import uuid
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)

    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to the person you want to trade with and use /trade [card_id]")
        return
    if not context.args:
        await update.message.reply_text("Usage: /trade [your_card_id]")
        return

    target_user = update.message.reply_to_message.from_user
    target_id = str(target_user.id)
    if target_id == user_id:
        await update.message.reply_text("❌ Can't trade with yourself!")
        return

    offer_id = context.args[0].strip().lstrip("#").zfill(4)
    
    async with await get_user_lock(user_id):
        sender_col = users[user_id].get("characters", [])
        offer_card = next((c for c in sender_col if isinstance(c, dict) and str(c.get("card_id","")).zfill(4) == offer_id), None)
        if not offer_card:
            await update.message.reply_text(f"❌ You don't have card #{offer_id}.")
            return
        if offer_card.get("favourite"):
            await update.message.reply_text("❌ Can't trade a favourited card!")
            return

    ensure_user(target_id)
    tid = str(uuid.uuid4())[:8]
    active_trades[tid] = {
        "sender_id": user_id, "receiver_id": target_id,
        "offer_card": offer_card, "want_card": None,
        "sender_confirmed": False, "receiver_confirmed": False,
        "chat_id": update.effective_chat.id
    }

    asyncio.create_task(expire_trade(tid, context))
    sender_name = html.escape(update.effective_user.first_name)
    target_name = html.escape(target_user.first_name)

    await update.message.reply_text(
        f"🔄 <b>Trade Request!</b>\n\n"
        f"👤 <b>{sender_name}</b> → <b>{target_name}</b>\n"
        f"📤 Offering: {html.escape(offer_card.get('name','?'))} | #{offer_id}\n\n"
        f"<b>{target_name}</b>, reply /offer [card_id] to accept!\n"
        f"🆔 Trade ID: <code>{tid}</code>",
        parse_mode="HTML"
    )

async def offer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)

    tid, session = next(
        ((t, s) for t, s in active_trades.items() if s["receiver_id"] == user_id),
        (None, None)
    )
    if not session:
        await update.message.reply_text("❌ No incoming trade request.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /offer [card_id]")
        return

    want_id = context.args[0].strip().lstrip("#").zfill(4)
    
    async with await get_user_lock(user_id):
        rec_col = users[user_id].get("characters", [])
        want_card = next((c for c in rec_col if isinstance(c, dict) and str(c.get("card_id","")).zfill(4) == want_id), None)
        if not want_card:
            await update.message.reply_text(f"❌ You don't have card #{want_id}.")
            return

    session["want_card"] = want_card
    offer_card = session["offer_card"]

    keyboard = [[
        InlineKeyboardButton("✅ Accept", callback_data=f"trade_accept_{tid}"),
        InlineKeyboardButton("❌ Decline", callback_data=f"trade_decline_{tid}")
    ]]
    await update.message.reply_text(
        f"🔄 <b>Trade Ready!</b>\n\n"
        f"📤 {html.escape(offer_card.get('name','?'))} (#{offer_card.get('card_id','?')})\n"
        f"📥 {html.escape(want_card.get('name','?'))} (#{want_card.get('card_id','?')})\n\n"
        f"Both players must confirm! 🆔 <code>{tid}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def trade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    data = query.data

    if data.startswith("trade_decline_"):
        tid = data[len("trade_decline_"):]
        active_trades.pop(tid, None)
        await query.edit_message_text("❌ <b>Trade declined.</b>", parse_mode="HTML")
        return

    if data.startswith("trade_accept_"):
        tid = data[len("trade_accept_"):]
        session = active_trades.get(tid)
        if not session:
            await query.edit_message_text("❌ Trade expired.")
            return
        if user_id not in (session["sender_id"], session["receiver_id"]):
            await query.answer("❌ Not your trade!", show_alert=True)
            return
        if session["want_card"] is None:
            await query.answer("⚠️ Receiver hasn't offered yet!", show_alert=True)
            return

        if user_id == session["sender_id"]:
            session["sender_confirmed"] = True
        else:
            session["receiver_confirmed"] = True

        if session["sender_confirmed"] and session["receiver_confirmed"]:
            sid, rid = session["sender_id"], session["receiver_id"]
            oc, wc = session["offer_card"], session["want_card"]
            
            async with await get_user_lock(sid), await get_user_lock(rid):
                for uid, card in [(sid, oc), (rid, wc)]:
                    col_ref = users[uid].get("characters", [])
                    cid = str(card.get("card_id","")).zfill(4)
                    for i, c in enumerate(col_ref):
                        if isinstance(c, dict) and str(c.get("card_id","")).zfill(4) == cid:
                            col_ref.pop(i)
                            break
                oc_copy = dict(oc); oc_copy["favourite"] = False
                wc_copy = dict(wc); wc_copy["favourite"] = False
                users[rid]["characters"].append(oc_copy)
                users[sid]["characters"].append(wc_copy)
                active_trades.pop(tid, None)
                save_data()
            
            await query.edit_message_text(
                f"🎉 <b>Trade Complete!</b>\n\n"
                f"Cards swapped successfully! ✅",
                parse_mode="HTML"
            )
        else:
            name = html.escape(query.from_user.first_name)
            try:
                await query.edit_message_text(
                    query.message.text + f"\n\n✅ <b>{name}</b> confirmed! Waiting for other player...",
                    parse_mode="HTML",
                    reply_markup=query.message.reply_markup
                )
            except Exception:
                pass

async def canceltrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    tid, _ = next(
        ((t, s) for t, s in active_trades.items()
         if s["sender_id"] == user_id or s["receiver_id"] == user_id),
        (None, None)
    )
    if not tid:
        await update.message.reply_text("❌ No active trade to cancel.")
        return
    active_trades.pop(tid, None)
    await update.message.reply_text("✅ Trade cancelled.")

async def expire_trade(tid, context):
    await asyncio.sleep(TRADE_TIMEOUT)
    if tid in active_trades:
        session = active_trades.pop(tid, None)
        if session:
            chat_id = session.get("chat_id")
            if chat_id:
                try:
                    await context.bot.send_message(chat_id=chat_id, text=f"⏰ Trade <code>{tid}</code> expired.", parse_mode="HTML")
                except Exception:
                    pass

# ─── UPLOAD ───

async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in uploaders and user_id != str(OWNER_ID):
        await update.message.reply_text("❌ You are not an uploader.")
        return
    if not update.message.reply_to_message or not update.message.reply_to_message.photo:
        await update.message.reply_text("Reply to a photo with: /upload Name | Anime | Rarity(1-5)")
        return
    try:
        data = " ".join(context.args)
        parts = data.split("|")
        if len(parts) < 3:
            raise ValueError
        name = parts[0].strip()
        rarity = int(parts[-1].strip())
        anime = "|".join(parts[1:-1]).strip()
        if not name or not anime or rarity not in (1,2,3,4,5):
            raise ValueError
    except Exception:
        await update.message.reply_text("Format: /upload Name | Anime | 1-5\n1=Common 2=Rare 3=Epic 4=Legendary 5=Celebrity")
        return

    file_id = update.message.reply_to_message.photo[-1].file_id
    card_id = next_card_id()
    characters.append({"card_id": card_id, "name": name, "anime": anime, "rarity": rarity, "file_id": file_id})
    save_data()

    rl = rarity_label(rarity)
    await update.message.reply_text(
        f"✅ <b>Uploaded!</b>\n🪪 #{card_id}\n🎴 {html.escape(name)}\n🎬 {html.escape(anime)}\n⭐ {rl}",
        parse_mode="HTML"
    )
    try:
        await context.bot.send_photo(
            chat_id=LOG_CHANNEL_ID,
            photo=file_id,
            caption=f"📥 New card #{card_id}: {html.escape(name)} | {html.escape(anime)} | {rl}",
            parse_mode="HTML"
        )
    except Exception:
        pass

async def adduploader(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(OWNER_ID):
        await update.message.reply_text("❌ Owner only.")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user.")
        return
    target = update.message.reply_to_message.from_user
    uploaders.add(str(target.id))
    save_data()
    await update.message.reply_text(f"✅ {target.first_name} is now an uploader.")

async def removeuploader(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(OWNER_ID):
        await update.message.reply_text("❌ Owner only.")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user.")
        return
    target_id = str(update.message.reply_to_message.from_user.id)
    uploaders.discard(target_id)
    save_data()
    await update.message.reply_text("✅ Removed from uploaders.")

async def give(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(OWNER_ID):
        await update.message.reply_text("❌ Owner only.")
        return
    if not update.message.reply_to_message or not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Reply to user + /give [amount]")
        return
    amount = int(context.args[0])
    target = update.message.reply_to_message.from_user
    target_id = str(target.id)
    ensure_user(target_id)
    if not characters:
        await update.message.reply_text("No characters to give!")
        return
    
    async with await get_user_lock(target_id):
        given = []
        for _ in range(amount):
            char = random.choice(characters)
            users[target_id]["characters"].append(char)
            given.append(char.get("name","?"))
        save_data()
    
    await update.message.reply_text(f"🎁 Gave {amount} cards to {html.escape(target.first_name)}!\n{', '.join(given[:5])}")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(OWNER_ID):
        await update.message.reply_text("❌ Owner only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /broadcast [message]")
        return
    text = f"📢 <b>Announcement</b>\n\n{html.escape(' '.join(context.args))}"
    sent = 0
    for uid in users:
        try:
            await context.bot.send_message(chat_id=int(uid), text=text, parse_mode="HTML")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await update.message.reply_text(f"✅ Sent to {sent} users.")

async def deletecard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(OWNER_ID):
        await update.message.reply_text("❌ Owner only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /deletecard [card_id]")
        return
    card_id = context.args[0].strip().lstrip("#").zfill(4)
    before = len(characters)
    characters[:] = [c for c in characters if str(c.get("card_id","")).zfill(4) != card_id]
    if len(characters) == before:
        await update.message.reply_text(f"❌ Card #{card_id} not found.")
        return
    removed = 0
    for udata in users.values():
        orig = len(udata.get("characters",[]))
        udata["characters"] = [c for c in udata.get("characters",[]) if not (isinstance(c,dict) and str(c.get("card_id","")).zfill(4) == card_id)]
        if len(udata["characters"]) < orig:
            removed += 1
    save_data()
    await update.message.reply_text(f"🗑 Card #{card_id} deleted from {removed} collections.")

# ─── BID SYSTEM ───

async def spawn_character(chat_id, context):
    global spawn_counter
    if not characters or chat_id in active_bid:
        return
    
    spawn_counter += 1
    character = random.choice(characters)
    rarity = character.get("rarity", 1)
    start_price = {1: 10000, 2: 30000, 3: 80000, 4: 200000, 5: 500000}.get(rarity, 10000)
    active_bid[chat_id] = {
        "character": character, "top_bidder": None,
        "top_bid": start_price, "end_time": time.time() + BID_DURATION,
        "spawn_number": spawn_counter
    }
    rl = rarity_label(rarity)
    caption = (
        f"🌟 <b>CHARACTER APPEARED! (#{spawn_counter})</b>\n\n"
        f"🎴 {html.escape(character.get('name','?'))}\n"
        f"🎬 {html.escape(character.get('anime','?'))}\n"
        f"⭐ {rl} | 🪪 #{character.get('card_id','?')}\n\n"
        f"💰 Starting Bid: {start_price:,}\n"
        f"⏳ {BID_DURATION}s | Use /bid [amount]"
    )
    try:
        await context.bot.send_photo(chat_id=chat_id, photo=character["file_id"], caption=caption, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Spawn error: {e}")
        active_bid.pop(chat_id, None)
        return
    asyncio.create_task(end_bid_after(chat_id, context))

async def end_bid_after(chat_id, context):
    while True:
        session = active_bid.get(chat_id)
        if not session:
            return
        if time.time() >= session["end_time"]:
            break
        await asyncio.sleep(2)
    await resolve_bid(chat_id, context)

async def resolve_bid(chat_id, context):
    session = active_bid.pop(chat_id, None)
    if not session:
        return
    character = session["character"]
    top_bidder = session["top_bidder"]
    top_bid = session["top_bid"]
    spawn_num = session.get("spawn_number", "?")
    
    if not top_bidder:
        await context.bot.send_message(chat_id=chat_id, text=f"⌛ No bids for <b>{html.escape(character.get('name','?'))}</b> (Spawn #{spawn_num})! Character lost.", parse_mode="HTML")
        return
    
    uid = top_bidder
    ensure_user(uid)
    
    async with await get_user_lock(uid):
        if users[uid]["coins"] < top_bid:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Winner didn't have enough coins! Card lost. (Spawn #{spawn_num})", parse_mode="HTML")
            return
        users[uid]["coins"] -= top_bid
        users[uid]["characters"].append(character)
        save_data()
    
    name = html.escape(users[uid].get("name","Unknown"))
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🎉 <b>BID OVER! (Spawn #{spawn_num})</b>\n🏆 Winner: {name}\n🎴 {html.escape(character.get('name','?'))}\n💰 Paid: {top_bid:,}",
        parse_mode="HTML"
    )

async def bid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    if chat_id not in active_bid:
        await update.message.reply_text("❌ No active bid in this group!")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /bid [amount]")
        return
    session = active_bid[chat_id]
    amount = int(context.args[0])
    
    if amount <= session["top_bid"]:
        await update.message.reply_text(f"❌ Must bid more than {session['top_bid']:,}!")
        return
    
    async with await get_user_lock(user_id):
        if users[user_id]["coins"] < amount:
            coins = users[user_id]["coins"]
            await update.message.reply_text(
                f"❌ <b>Not enough coins!</b>\n\n"
                f"💰 Need: {amount:,} coins\n"
                f"💳 You have: {coins:,} coins\n"
                f"💡 Use /bonus or /checkin to earn more!",
                parse_mode="HTML"
            )
            return
    
    if session["top_bidder"] == user_id:
        await update.message.reply_text("❌ You're already the top bidder!")
        return
    
    session["top_bidder"] = user_id
    session["top_bid"] = amount
    session["end_time"] = max(session["end_time"], time.time()) + BID_EXTEND
    name = html.escape(update.effective_user.first_name)
    await update.message.reply_text(f"⚡ <b>{name}</b> bids {amount:,} coins! +{BID_EXTEND}s added.", parse_mode="HTML")

# ─── GUESS GAME ───

async def guess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != GROUP_ID:
        await update.message.reply_text("❌ Only works in the official group!")
        return
    chat_id = update.effective_chat.id
    if chat_id in active_guess:
        await update.message.reply_text("⚠️ A game is already running!")
        return
    prize = 5000
    if context.args and context.args[0].isdigit():
        prize = max(100, int(context.args[0]))
    answer = random.randint(1, 100)
    active_guess[chat_id] = {"answer": answer, "prize": prize}
    await update.message.reply_text(
        f"🎯 <b>GUESS THE NUMBER (1-100)!</b>\n💰 Prize: {prize:,} coins\nJust type a number!",
        parse_mode="HTML"
    )

async def handle_guess_attempt(chat_id, user_id, text, update, context):
    session = active_guess.get(chat_id)
    if not session or not text.isdigit():
        return False
    num = int(text)
    if num < 1 or num > 100:
        return False
    answer = session["answer"]
    prize = session["prize"]
    ensure_user(user_id, update)
    name = html.escape(update.effective_user.first_name)
    
    if num == answer:
        async with await get_user_lock(user_id):
            users[user_id]["coins"] += prize
            active_guess.pop(chat_id, None)
            save_data()
        await update.message.reply_text(f"🎉 <b>{name}</b> got it! The number was <b>{answer}</b>!\n💰 +{prize:,} coins!", parse_mode="HTML")
        return True
    elif num < answer:
        await update.message.reply_text(f"📈 {num} is too low!", parse_mode="HTML")
    else:
        await update.message.reply_text(f"📉 {num} is too high!", parse_mode="HTML")
    return True

# ─── MESSAGE HANDLER ───

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()

    # Spawn counter for group chats
    if update.effective_chat.type in ("group", "supergroup"):
        now = time.time()
        key = (chat_id, user_id)
        
        last_t = last_message_time.get(key, 0)
        last_txt = last_message_text.get(key, "")
        
        if now - last_t >= USER_MSG_COOLDOWN and text.lower() != last_txt:
            last_message_time[key] = now
            last_message_text[key] = text.lower()
            group_message_counts[chat_id] = group_message_counts.get(chat_id, 0) + 1
            
            if group_message_counts[chat_id] >= SPAWN_EVERY:
                group_message_counts[chat_id] = 0
                if characters and chat_id not in active_bid:
                    asyncio.create_task(spawn_character(chat_id, context))

    # Guess game
    if chat_id == GROUP_ID and chat_id in active_guess:
        handled = await handle_guess_attempt(chat_id, user_id, text.strip(), update, context)
        if handled:
            return

    # WORDLE - ACCEPTS ANY 5-LETTER WORD (ABCDE, ZZZZZ, etc.)
    text_lower = text.lower().strip()
    
    # Check if it's a 5-letter word and user has active game
    if len(text_lower) == 5 and text_lower.isalpha() and user_id in active_wordle:
        ensure_user(user_id, update)
        game = active_wordle[user_id]
        answer = game["answer"]
        emoji_list = get_wordle_emoji_list(text_lower, answer)
        game["guesses"].append((text_lower, emoji_list))
        save_data()

        board = format_wordle_board(game["guesses"])

        if text_lower == answer:
            guess_count = len(game["guesses"])
            coins_map = {1: 25000, 2: 15000, 3: 10000, 4: 5000, 5: 2000, 6: 1000}
            coins_earned = coins_map.get(guess_count, 1000)

            async with await get_user_lock(user_id):
                stats = users[user_id]["wordle_stats"]
                stats["played"] = stats.get("played", 0) + 1
                stats["won"] = stats.get("won", 0) + 1
                stats["current_streak"] = stats.get("current_streak", 0) + 1
                if stats["current_streak"] > stats.get("best_streak", 0):
                    stats["best_streak"] = stats["current_streak"]
                users[user_id]["coins"] = users[user_id].get("coins", 0) + coins_earned
                stats["coins_earned"] = stats.get("coins_earned", 0) + coins_earned

                wins = users[user_id].setdefault("wordle_wins", {"today":0,"week":0,"month":0,"alltime":0})
                wins["alltime"] = wins.get("alltime", 0) + 1
                wins["today"] = wins.get("today", 0) + 1
                wins["week"] = wins.get("week", 0) + 1
                
                users[user_id]["wordle_last_date"]["daily"] = datetime.now().strftime("%Y-%m-%d")

                del active_wordle[user_id]
                save_data()

            await update.message.reply_text(
                f"🎉 <b>WORDLE COMPLETE!</b>\n\n{board}\n\n"
                f"🏆 Guesses: {guess_count}\n"
                f"💰 +{coins_earned:,} coins\n"
                f"🔥 Streak: {stats['current_streak']}",
                parse_mode="HTML"
            )

        elif len(game["guesses"]) >= 6:
            async with await get_user_lock(user_id):
                stats = users[user_id]["wordle_stats"]
                stats["played"] = stats.get("played", 0) + 1
                stats["lost"] = stats.get("lost", 0) + 1
                stats["current_streak"] = 0
                del active_wordle[user_id]
                save_data()
            await update.message.reply_text(
                f"❌ <b>GAME OVER!</b>\n\n{board}\n\nThe word was: <b>{answer.upper()}</b>",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(
                f"{board}\n\n📝 Attempts left: {6 - len(game['guesses'])}",
                parse_mode="HTML"
            )

# ─── FLASK + WEBHOOK ───

flask_app = Flask(__name__)
application = None
bot_loop = None

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    global application, bot_loop
    if application and bot_loop and request.is_json:
        data = request.get_json()
        update = Update.de_json(data, application.bot)
        asyncio.run_coroutine_threadsafe(application.process_update(update), bot_loop)
    return jsonify({"ok": True}), 200

@flask_app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"}), 200

# ─── MAIN ───

async def run_bot():
    global application, bot_loop

    if not TOKEN:
        print("ERROR: BOT_TOKEN not set!")
        return

    load_words_once()
    load_data()
    print("✅ Data loaded from MongoDB")
    print(f"✅ Word list loaded: {len(WORD_LIST)} words (for secret words only)")
    print("✅ Wordle accepts ANY 5-letter combination (ABCDE, ZZZZZ, etc.)")

    asyncio.create_task(cleanup_old_data())

    application = ApplicationBuilder().token(TOKEN).build()

    # All handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("bonus", bonus))
    application.add_handler(CommandHandler("profile", profile))
    application.add_handler(CommandHandler("collection", mycards))
    application.add_handler(CommandHandler("summon", summon))
    application.add_handler(CommandHandler("wordle", wordle))
    application.add_handler(CommandHandler("cwordle", cwordle))
    application.add_handler(CommandHandler("wstats", wstats))
    application.add_handler(CommandHandler("wtop", wtop))
    application.add_handler(CommandHandler("ctop", ctop))
    application.add_handler(CommandHandler("checkin", checkin))
    application.add_handler(CommandHandler("checkintop", checkintop))
    application.add_handler(CommandHandler("shop", shop))
    application.add_handler(CommandHandler("favourite", favourite))
    application.add_handler(CommandHandler("favorites", myfavourites))
    application.add_handler(CommandHandler("cardinfo", cardinfo))
    application.add_handler(CommandHandler("trade", trade))
    application.add_handler(CommandHandler("offer", offer))
    application.add_handler(CommandHandler("canceltrade", canceltrade))
    application.add_handler(CommandHandler("upload", upload))
    application.add_handler(CommandHandler("adduploader", adduploader))
    application.add_handler(CommandHandler("removeuploader", removeuploader))
    application.add_handler(CommandHandler("give", give))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CommandHandler("deletecard", deletecard))
    application.add_handler(CommandHandler("bid", bid))
    application.add_handler(CommandHandler("guess", guess))
    application.add_handler(CallbackQueryHandler(collection_buttons, pattern="^col_"))
    application.add_handler(CallbackQueryHandler(bonus_callback, pattern="^bonus_"))
    application.add_handler(CallbackQueryHandler(shop_callback, pattern="^shop_"))
    application.add_handler(CallbackQueryHandler(trade_callback, pattern="^trade_"))
    application.add_handler(CallbackQueryHandler(wtop_callback, pattern="^wtop_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await application.initialize()
    await application.start()
    bot_loop = asyncio.get_running_loop()
    print("✅ Bot running!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8080))
    flask_thread = threading.Thread(
        target=lambda: flask_app.run(host="0.0.0.0", port=PORT, use_reloader=False, threaded=True),
        daemon=True
    )
    flask_thread.start()
    print(f"✅ Flask on port {PORT}")
    time.sleep(2)
    asyncio.run(run_bot())
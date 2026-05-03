from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultCachedPhoto
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, InlineQueryHandler, filters, ContextTypes
import random
import time
import logging
import asyncio
import os
import html
import json
import sys
from datetime import datetime, timedelta
from pymongo import MongoClient
from flask import Flask, jsonify
import threading
import uuid

logging.basicConfig(level=logging.INFO)

# ─── CONFIG ───
TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URI = os.environ.get("MONGO_URI")

if not TOKEN:
    logging.error("❌ BOT_TOKEN environment variable not set!")
    sys.exit(1)

if not MONGO_URI:
    logging.error("❌ MONGO_URI environment variable not set!")
    sys.exit(1)

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
ITEMS_PER_PAGE = 5
SHOP_ITEMS_PER_PAGE = 1
SHOP_MAX_PAGES = 3

# Crash Game settings
CRASH_INCREMENT = 0.05
CRASH_UPDATE_INTERVAL = 0.5
CRASH_MAX_MULTIPLIER = 10.0

# Gift system
MAX_GIFT_DAILY = 50000
GIFT_COOLDOWN = 300

# Custom Titles
PRESET_TITLES = {
    1: {"name": "The Legend", "price": 10000},
    2: {"name": "Card Master", "price": 10000},
    3: {"name": "Anime God", "price": 10000},
    4: {"name": "Collector King", "price": 10000},
    5: {"name": "Wealthy Lord", "price": 10000}
}
CUSTOM_TITLE_PRICE = 25000
MAX_TITLE_LENGTH = 20

# Game difficulty settings
RPS_DIFFICULTY = {
    "easy": {"bet": 5000, "win_multiplier": 2, "color": "🟢", "name": "Easy"},
    "medium": {"bet": 25000, "win_multiplier": 2, "color": "🟡", "name": "Medium"},
    "hard": {"bet": 100000, "win_multiplier": 2, "color": "🔴", "name": "Hard"}
}

HL_DIFFICULTY = {
    "easy": {"bet": 5000, "max_multiplier": 8, "color": "🟢", "name": "Easy"},
    "medium": {"bet": 25000, "max_multiplier": 16, "color": "🟡", "name": "Medium"},
    "hard": {"bet": 100000, "max_multiplier": 32, "color": "🔴", "name": "Hard"}
}

MINES_DIFFICULTY = {
    "easy": {"bet": 5000, "bombs": 3, "max_multiplier": 10, "color": "🟢", "name": "Easy", "grid_size": 4},
    "medium": {"bet": 25000, "bombs": 5, "max_multiplier": 20, "color": "🟡", "name": "Medium", "grid_size": 5},
    "hard": {"bet": 100000, "bombs": 8, "max_multiplier": 40, "color": "🔴", "name": "Hard", "grid_size": 6}
}

# ─── MONGODB CONNECTION ───
try:
    mongo_client = MongoClient(
        MONGO_URI, 
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=5000
    )
    mongo_client.admin.command('ping')
    logging.info("✅ MongoDB connected successfully")
    db = mongo_client["botdb"]
    col = db["gamedata"]
except Exception as e:
    logging.error(f"❌ MongoDB connection failed: {e}")
    sys.exit(1)

# ─── GLOBAL STATE ───
users = {}
last_daily = {}
last_weekly = {}
characters = []
last_checkin = {}
checkin_streak = {}
summon_cooldowns = {}
uploaders = set()
active_bid = {}
active_trades = {}
active_guess = {}
active_games = {}
active_crash_games = {}
group_message_counts = {}
last_message_time = {}
last_message_text = {}
user_locks = {}
spawn_counter = 0
group_spawn_counters = {}
group_msg_lock = asyncio.Lock()
data_lock = asyncio.Lock()
thread_lock = threading.Lock()
bot_start_time = time.time()

# New global stores
user_titles = {}
user_gift_cooldown = {}
user_gift_daily = {}
completed_sets = {}
anime_sets_cache = {}

# Feature 3 & 4: Daily shop rotation
daily_shop_chars = []       # 3 random chars chosen each day
daily_shop_date = ""        # date string YYYY-MM-DD when shop was last rotated
SHOP_REFRESH_COST = 5000    # cost to press the refresh button
DAILY_SHOP_SIZE = 3         # how many cards appear in the daily rotating shop

# Feature 7: Group tracking
known_groups = {}           # {chat_id: {"title": ..., "username": ..., "first_seen": ...}}

# ═══════════════════════════════════════════════════════════════
# SAVE / LOAD FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def save_data():
    global users, last_daily, last_weekly, characters
    global last_checkin, checkin_streak, uploaders, spawn_counter
    global user_titles, user_gift_cooldown, user_gift_daily, completed_sets
    global daily_shop_chars, daily_shop_date, known_groups
    
    data = {
        "_id": "main",
        "users": users,
        "last_daily": last_daily,
        "last_weekly": last_weekly,
        "characters": characters,
        "last_checkin": last_checkin,
        "checkin_streak": checkin_streak,
        "uploaders": list(uploaders),
        "spawn_counter": spawn_counter,
        "user_titles": user_titles,
        "user_gift_cooldown": user_gift_cooldown,
        "user_gift_daily": user_gift_daily,
        "completed_sets": completed_sets,
        "daily_shop_chars": daily_shop_chars,
        "daily_shop_date": daily_shop_date,
        "known_groups": {str(k): v for k, v in known_groups.items()},
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
        except Exception as backup_error:
            logging.error(f"Backup save failed: {backup_error}")

def load_data():
    global users, last_daily, last_weekly, characters
    global last_checkin, checkin_streak, uploaders, spawn_counter
    global user_titles, user_gift_cooldown, user_gift_daily, completed_sets
    global daily_shop_chars, daily_shop_date, known_groups
    
    try:
        data = col.find_one({"_id": "main"})
        if data:
            users = data.get("users", {})
            last_daily = data.get("last_daily", {})
            last_weekly = data.get("last_weekly", {})
            characters = data.get("characters", [])
            last_checkin = data.get("last_checkin", {})
            checkin_streak = data.get("checkin_streak", {})
            uploaders = set(map(str, data.get("uploaders", [])))
            spawn_counter = data.get("spawn_counter", 0)
            user_titles = data.get("user_titles", {})
            user_gift_cooldown = data.get("user_gift_cooldown", {})
            user_gift_daily = data.get("user_gift_daily", {})
            completed_sets = data.get("completed_sets", {})
            daily_shop_chars = data.get("daily_shop_chars", [])
            daily_shop_date = data.get("daily_shop_date", "")
            known_groups = {int(k): v for k, v in data.get("known_groups", {}).items()}

            for uid, udata in users.items():
                udata.setdefault("characters", [])
                udata.setdefault("coins", 100)
                udata.setdefault("name", "Unknown")
                udata.setdefault("username", "")
                udata.setdefault("joined", time.time())
                udata.setdefault("title", None)
                udata.setdefault("owned_titles", [])
                udata.setdefault("completed_sets", [])
                udata.setdefault("game_stats", {
                    "rps": {"played": 0, "won": 0, "lost": 0, "profit": 0, "best_streak": 0, "current_streak": 0},
                    "hl": {"played": 0, "won": 0, "lost": 0, "profit": 0, "biggest_win": 0, "best_multiplier": 0},
                    "mines": {"played": 0, "won": 0, "lost": 0, "profit": 0, "best_tiles": 0, "best_multiplier": 0},
                    "guess": {"played": 0, "won": 0, "lost": 0, "profit": 0, "best_streak": 0, "current_streak": 0, "biggest_win": 0},
                    "crash": {"played": 0, "won": 0, "profit": 0, "biggest_win": 0}
                })
            
            for uid in users:
                if "title" not in users[uid]:
                    users[uid]["title"] = None
                if "owned_titles" not in users[uid]:
                    users[uid]["owned_titles"] = []
                if "completed_sets" not in users[uid]:
                    users[uid]["completed_sets"] = []
                if "game_stats" not in users[uid]:
                    users[uid]["game_stats"] = {}
                if "crash" not in users[uid]["game_stats"]:
                    users[uid]["game_stats"]["crash"] = {"played": 0, "won": 0, "profit": 0, "biggest_win": 0}
            
            logging.info("Data loaded from MongoDB")
        else:
            try:
                with open("backup.json", "r") as f:
                    data = json.load(f)
                if data:
                    users = data.get("users", {})
                    characters = data.get("characters", [])
                    spawn_counter = data.get("spawn_counter", 0)
                    user_titles = data.get("user_titles", {})
                    logging.info("Data loaded from JSON backup")
            except Exception:
                pass
    except Exception as e:
        logging.error(f"Load error: {e}")

# ═══════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════

async def get_user_lock(user_id):
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]

# ═══════════════════════════════════════════════════════════════
# DAILY SHOP ROTATION  (Features 3 & 4)
# ═══════════════════════════════════════════════════════════════

def rotate_daily_shop():
    """Pick DAILY_SHOP_SIZE random characters for today's shop."""
    global daily_shop_chars, daily_shop_date
    today = datetime.now().strftime("%Y-%m-%d")
    if daily_shop_date == today and daily_shop_chars:
        return  # already rotated today
    if not characters:
        daily_shop_chars = []
        daily_shop_date = today
        return
    pool = characters.copy()
    random.shuffle(pool)
    daily_shop_chars = pool[:DAILY_SHOP_SIZE]
    daily_shop_date = today
    save_data()
    logging.info(f"Daily shop rotated: {[c.get('name') for c in daily_shop_chars]}")

def refresh_shop_manual():
    """Re-pick a fresh random selection (called when user pays to refresh)."""
    global daily_shop_chars
    if not characters:
        return
    pool = characters.copy()
    random.shuffle(pool)
    daily_shop_chars = pool[:DAILY_SHOP_SIZE]
    save_data()

async def daily_shop_rotation_task():
    """Background task: rotate shop every night at midnight."""
    while True:
        now = datetime.now()
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
        wait_seconds = (next_midnight - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        rotate_daily_shop()
        logging.info("🏪 Daily shop auto-rotated at midnight!")


def init_anime_sets():
    """Build anime sets from database characters"""
    global anime_sets_cache
    anime_sets_cache = {}
    
    for char in characters:
        anime = char.get("anime", "Unknown")
        card_id = char.get("card_id")
        if not card_id:
            continue
        if anime not in anime_sets_cache:
            anime_sets_cache[anime] = []
        if card_id not in anime_sets_cache[anime]:
            anime_sets_cache[anime].append(card_id)
    
    logging.info(f"Built {len(anime_sets_cache)} anime sets")

def get_set_reward(total_cards):
    """Calculate reward based on number of cards in set"""
    if total_cards >= 20:
        return 500000
    elif total_cards >= 15:
        return 400000
    elif total_cards >= 10:
        return 250000
    elif total_cards >= 5:
        return 100000
    else:
        return 50000

async def check_anime_completion(user_id, context):
    """Check if user completed any anime set and give reward"""
    uid = str(user_id)
    user_completed = users[uid].get("completed_sets", [])
    user_cards = users[uid].get("characters", [])
    user_card_ids = [str(c.get("card_id")) for c in user_cards if c.get("card_id")]
    
    newly_completed = []
    
    for anime, required_cards in anime_sets_cache.items():
        if anime in user_completed:
            continue
        
        has_all = all(card_id in user_card_ids for card_id in required_cards)
        
        if has_all:
            newly_completed.append(anime)
            reward = get_set_reward(len(required_cards))
            
            async with await get_user_lock(uid):
                users[uid]["coins"] = users[uid].get("coins", 0) + reward
                users[uid]["completed_sets"].append(anime)
                save_data()
            
            try:
                await context.bot.send_message(
                    chat_id=int(uid),
                    text=f"🎉 <b>COMPLETE ANIME SET!</b> 🎉\n\n"
                         f"📺 Anime: {anime}\n"
                         f"🎴 Cards collected: {len(required_cards)}/{len(required_cards)}\n"
                         f"💰 Reward: {reward:,} coins!",
                    parse_mode="HTML"
                )
            except Exception:
                pass
    
    return newly_completed

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

async def cleanup_stale_games():
    while True:
        await asyncio.sleep(3600)
        now = time.time()
        stale_games = []
        for user_id, game in active_games.items():
            game_time = game.get("timestamp", 0)
            if now - game_time > 172800:
                stale_games.append(user_id)
        for user_id in stale_games:
            del active_games[user_id]
            logging.info(f"Cleaned up stale game for user {user_id}")

async def cleanup_stale_trades():
    while True:
        await asyncio.sleep(60)
        now = time.time()
        stale_trades = []
        for trade_id, trade in active_trades.items():
            expires_at = trade.get("expires_at", 0)
            if now > expires_at:
                stale_trades.append(trade_id)
        for trade_id in stale_trades:
            active_trades.pop(trade_id, None)
            logging.info(f"Cleaned up stale trade: {trade_id}")

async def monitor_stuck_bids():
    while True:
        await asyncio.sleep(60)
        now = time.time()
        for chat_id, session in list(active_bid.items()):
            if session.get("start_time", 0) and now - session["start_time"] > 600:
                logging.warning(f"Stuck bid detected in chat {chat_id} — removing silently")
                active_bid.pop(chat_id, None)

def ensure_user(user_id, update=None):
    uid = str(user_id)
    if uid not in users:
        users[uid] = {
            "characters": [], "coins": 100, "joined": time.time(),
            "name": update.effective_user.first_name if update else "Unknown",
            "username": (update.effective_user.username or "") if update else "",
            "title": None,
            "owned_titles": [],
            "completed_sets": [],
            "game_stats": {
                "rps": {"played": 0, "won": 0, "lost": 0, "profit": 0, "best_streak": 0, "current_streak": 0},
                "hl": {"played": 0, "won": 0, "lost": 0, "profit": 0, "biggest_win": 0, "best_multiplier": 0},
                "mines": {"played": 0, "won": 0, "lost": 0, "profit": 0, "best_tiles": 0, "best_multiplier": 0},
                "guess": {"played": 0, "won": 0, "lost": 0, "profit": 0, "best_streak": 0, "current_streak": 0, "biggest_win": 0},
                "crash": {"played": 0, "won": 0, "profit": 0, "biggest_win": 0},
                "wordle": {"played": 0, "won": 0, "lost": 0, "coins_earned": 0, "best_guesses": 0, "current_streak": 0, "best_streak": 0}
            }
        }
    elif update:
        users[uid]["name"] = update.effective_user.first_name
        users[uid]["username"] = update.effective_user.username or ""
    
    users[uid].setdefault("title", None)
    users[uid].setdefault("owned_titles", [])
    users[uid].setdefault("completed_sets", [])
    users[uid].setdefault("game_stats", {
        "rps": {"played": 0, "won": 0, "lost": 0, "profit": 0, "best_streak": 0, "current_streak": 0},
        "hl": {"played": 0, "won": 0, "lost": 0, "profit": 0, "biggest_win": 0, "best_multiplier": 0},
        "mines": {"played": 0, "won": 0, "lost": 0, "profit": 0, "best_tiles": 0, "best_multiplier": 0},
        "guess": {"played": 0, "won": 0, "lost": 0, "profit": 0, "best_streak": 0, "current_streak": 0, "biggest_win": 0},
        "crash": {"played": 0, "won": 0, "profit": 0, "biggest_win": 0},
        "wordle": {"played": 0, "won": 0, "lost": 0, "coins_earned": 0, "best_guesses": 0, "current_streak": 0, "best_streak": 0}
    })
    users[uid]["game_stats"].setdefault("wordle", {
        "played": 0, "won": 0, "lost": 0, "coins_earned": 0,
        "best_guesses": 0, "current_streak": 0, "best_streak": 0
    })

def format_time(seconds):
    seconds = int(seconds)
    if seconds <= 0:
        return "0s"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 and not parts:
        parts.append(f"{secs}s")
    return " ".join(parts) if parts else "0s"

def rarity_label(rarity):
    return {1: "⚪ Common", 2: "🔵 Rare", 3: "🟣 Epic",
            4: "🟡 Legendary", 5: "⚡ Celebrity"}.get(rarity, "Unknown")

def next_card_id():
    if not characters:
        return "0001"
    ids = []
    for c in characters:
        card_id = c.get("card_id")
        if card_id is None:
            continue
        try:
            ids.append(int(str(card_id)))
        except (ValueError, TypeError):
            continue
    return str(max(ids) + 1).zfill(4) if ids else "0001"

def get_user_rank(user_id, rank_type="coins"):
    if rank_type == "coins":
        sorted_users = sorted(users.items(), key=lambda x: x[1].get("coins", 0), reverse=True)
    else:
        return None
    for i, (uid, _) in enumerate(sorted_users, 1):
        if uid == str(user_id):
            return i
    return None

async def send_collection_page(chat_id, context, user_id):
    collection = context.user_data.get("col_list", [])
    page = context.user_data.get("col_page", 0)
    start = page * ITEMS_PER_PAGE
    page_items = collection[start:start + ITEMS_PER_PAGE]
    total_pages = max(1, (len(collection) - 1) // ITEMS_PER_PAGE + 1)
    total_cards = len(collection)
    rarity_map = {1: "⚪", 2: "🔵", 3: "🟣", 4: "🟡", 5: "⚡"}

    text = f"<b>📚 {html.escape(users[user_id]['name'])}'s Collection</b>\n"
    text += f"Page {page+1}/{total_pages} | Total: {total_cards} cards\n\n"

    first_card = None
    for char in page_items:
        if not isinstance(char, dict):
            continue
        rl    = rarity_map.get(char.get("rarity", 1), "❓")
        name  = html.escape(char.get("name", "?"))
        anime = html.escape(char.get("anime", "?"))
        cid   = char.get("card_id", "????")
        fav   = "⭐ " if char.get("favourite") else ""
        qty   = get_card_qty(user_id, cid)
        price = get_card_price(char.get("rarity", 1))
        text += f"{fav}{rl} <b>{name}</b>\n🎬 {anime} | 🪪 #{cid}\n💰 {price:,} coins | 🗂 Owned: {qty}x\n\n"
        if first_card is None:
            first_card = char

    if not page_items or first_card is None:
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

    first_cid = str(first_card.get("card_id", "")).zfill(4)
    keyboard.append([InlineKeyboardButton(
        f"📤 Send {html.escape(first_card.get('name','Card'))[:15]}",
        callback_data=f"col_send_{first_cid}"
    )])
    keyboard.append([InlineKeyboardButton(f"🖼️ Gallery [{total_cards}]", callback_data=f"col_gallery_{user_id}_0")])
    keyboard.append([InlineKeyboardButton("❌ Close", callback_data="col_close")])

    photo = first_card.get("file_id")
    try:
        await context.bot.send_photo(
            chat_id=chat_id, photo=photo, caption=text,
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logging.error(f"Failed to send collection photo: {e}")
        await context.bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
        )

    photo = page_items[0].get("file_id")
    try:
        await context.bot.send_photo(
            chat_id=chat_id, photo=photo, caption=text,
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logging.error(f"Failed to send collection photo: {e}")
        await context.bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
        )

GALLERY_PER_PAGE = 9  # 3x3 grid

def get_card_qty(user_id, card_id):
    """Count how many copies of a card a user owns."""
    uid = str(user_id)
    return sum(
        1 for c in users.get(uid, {}).get("characters", [])
        if str(c.get("card_id", "")).zfill(4) == str(card_id).zfill(4)
    )

def get_card_price(rarity):
    return SHOP_PRICES.get(rarity, 50000)

def apply_gallery_filters(collection, search="", rarity=0):
    """Filter a collection by search text and/or rarity."""
    result = []
    for c in collection:
        if not isinstance(c, dict):
            continue
        if rarity and c.get("rarity") != rarity:
            continue
        if search and search not in c.get("name", "").lower() and search not in c.get("anime", "").lower():
            continue
        result.append(c)
    return result

async def send_gallery_page(chat_id, context, user_id, gallery_page):
    """
    Send a 3x3 grid of card thumbnails (as inline buttons with emoji labels).
    Tapping a card goes to its detail view.
    Also shows search + rarity filter buttons.
    """
    raw_collection = context.user_data.get("col_list", [])
    search   = context.user_data.get("gal_search", "")
    rarity_f = context.user_data.get("gal_rarity", 0)

    collection = apply_gallery_filters(raw_collection, search, rarity_f)

    # Deduplicate: show each unique card once
    seen = {}
    for c in collection:
        cid = str(c.get("card_id", "")).zfill(4)
        if cid not in seen:
            seen[cid] = c
    unique = list(seen.values())

    total_cards = len(unique)
    total_pages = max(1, (total_cards - 1) // GALLERY_PER_PAGE + 1)
    gallery_page = max(0, min(gallery_page, total_pages - 1))

    start = gallery_page * GALLERY_PER_PAGE
    page_items = unique[start:start + GALLERY_PER_PAGE]

    rarity_map = {1: "⚪", 2: "🔵", 3: "🟣", 4: "🟡", 5: "⚡"}
    rar_labels  = {0: "All ✨", 1: "⚪ Common", 2: "🔵 Rare", 3: "🟣 Epic", 4: "🟡 Legend", 5: "⚡ Celeb"}

    # ── Build 3-column card grid as inline buttons ──
    keyboard = []
    row = []
    for i, char in enumerate(page_items):
        cid  = str(char.get("card_id", "")).zfill(4)
        name = char.get("name", "?")[:10]
        rl   = rarity_map.get(char.get("rarity", 1), "❓")
        qty  = get_card_qty(user_id, cid)
        label = f"{rl}{name} x{qty}"
        row.append(InlineKeyboardButton(label, callback_data=f"gal_view_{cid}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # ── Navigation ──
    nav = []
    if gallery_page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"gal_page_{gallery_page - 1}"))
    nav.append(InlineKeyboardButton(f"{gallery_page+1}/{total_pages}", callback_data="gal_noop"))
    if gallery_page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"gal_page_{gallery_page + 1}"))
    keyboard.append(nav)

    # ── Rarity filter row ──
    next_rar = (rarity_f % 5) + 1 if rarity_f < 5 else 0
    cur_label = rar_labels.get(rarity_f, "All ✨")
    keyboard.append([
        InlineKeyboardButton(f"🎚 Filter: {cur_label}", callback_data=f"gal_rar_{next_rar}"),
        InlineKeyboardButton("🔍 Search", callback_data="gal_search_prompt"),
    ])

    if search:
        keyboard.append([InlineKeyboardButton(f"❌ Clear search: '{search}'", callback_data="gal_clear_search")])

    keyboard.append([InlineKeyboardButton("🔙 Back to List", callback_data="col_back")])

    filter_info = ""
    if search:
        filter_info += f" | 🔍 '{search}'"
    if rarity_f:
        filter_info += f" | {rar_labels[rarity_f]}"

    text = (
        f"🖼️ <b>Gallery</b>{filter_info}\n"
        f"📦 {total_cards} cards | Page {gallery_page+1}/{total_pages}\n\n"
        f"Tap a card name to view details + Send 👇"
    )

    if not page_items:
        text = "😕 No cards match your filter. Try a different search or rarity!"
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="col_back")]]

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logging.error(f"Gallery send error: {e}")

async def send_gallery_card_detail(chat_id, context, user_id, card_id):
    """Show full detail of one card with Send button, from gallery."""
    collection = context.user_data.get("col_list", [])
    char = next(
        (c for c in collection if isinstance(c, dict) and str(c.get("card_id","")).zfill(4) == card_id),
        None
    )
    if not char:
        await context.bot.send_message(chat_id=chat_id, text="❌ Card not found!")
        return

    rarity_full = {1: "⚪ Common", 2: "🔵 Rare", 3: "🟣 Epic", 4: "🟡 Legendary", 5: "⚡ Celebrity"}
    rl    = rarity_full.get(char.get("rarity", 1), "❓")
    name  = html.escape(char.get("name", "?"))
    anime = html.escape(char.get("anime", "?"))
    cid   = str(char.get("card_id", "????")).zfill(4)
    qty   = get_card_qty(user_id, cid)
    price = get_card_price(char.get("rarity", 1))
    fav   = "⭐ Favourite\n" if char.get("favourite") else ""

    caption = (
        f"{fav}{rl}\n"
        f"🎴 <b>{name}</b>\n"
        f"📺 <b>Anime:</b> {anime}\n"
        f"🪪 <b>ID:</b> #{cid}\n"
        f"💰 <b>Price:</b> {price:,} coins\n"
        f"🗂 <b>You own:</b> {qty}x"
    )

    keyboard = [
        [InlineKeyboardButton(f"📤 Send {name[:20]}", callback_data=f"col_send_{cid}")],
        [InlineKeyboardButton("🔙 Back to Gallery", callback_data="gal_back")],
    ]

    try:
        if char.get("file_id"):
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=char["file_id"],
                caption=caption,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id, text=caption,
                parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
            )
    except Exception as e:
        logging.error(f"Gallery card detail error: {e}")

async def send_shop_page(chat_id, context, user_id):
    # Use daily rotating shop; fall back to first 3 characters if empty
    shop_list = daily_shop_chars if daily_shop_chars else (characters[:DAILY_SHOP_SIZE] if characters else [])
    page = context.user_data.get("shop_page", 0)
    total_pages = len(shop_list)

    if not shop_list:
        await context.bot.send_message(chat_id=chat_id, text="🏪 No characters in shop!")
        return

    if page >= total_pages:
        page = total_pages - 1
    if page < 0:
        page = 0

    char = shop_list[page]
    rarity = char.get("rarity", 1)
    price = SHOP_PRICES.get(rarity, 50000)
    rl = rarity_label(rarity)
    coins = users[user_id].get("coins", 0)
    mark = "✅" if coins >= price else "❌"

    today = datetime.now().strftime("%Y-%m-%d")
    text = f"🏪 <b>CHARACTER SHOP</b> — <i>{today}</i>\n"
    text += f"💰 Your coins: <b>{coins:,}</b>\n"
    text += f"📄 Page {page+1}/{total_pages}\n\n"
    text += f"{mark} <b>{html.escape(char.get('name','?'))}</b>\n"
    text += f"⭐ {rl}\n"
    text += f"🎬 {html.escape(char.get('anime','?'))}\n"
    text += f"💰 Price: {price:,}\n"
    text += f"🪪 #{char.get('card_id','?')}\n"
    text += f"\n🔄 Refresh shop costs {SHOP_REFRESH_COST:,} coins"

    keyboard = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Previous", callback_data="shop_prev"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data="shop_next"))
    if nav:
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton(
        f"Buy {char.get('name', '?')[:15]} ({price:,})",
        callback_data=f"shop_buy_{char.get('card_id','')}"
    )])
    keyboard.append([InlineKeyboardButton(
        f"🔄 Refresh Shop ({SHOP_REFRESH_COST:,} coins)",
        callback_data="shop_refresh"
    )])
    keyboard.append([InlineKeyboardButton("❌ Close", callback_data="shop_close")])

    try:
        if char.get("file_id"):
            await context.bot.send_photo(
                chat_id=chat_id, photo=char["file_id"],
                caption=text, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id, text=text, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    except Exception as e:
        logging.error(f"Failed to send shop page: {e}")
        await context.bot.send_message(
            chat_id=chat_id, text=text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# ═══════════════════════════════════════════════════════════════
# CRASH GAME
# ═══════════════════════════════════════════════════════════════

async def crash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /crash [amount]\nExample: /crash 10000")
        return
    
    bet = int(args[0])
    
    if bet < 1000:
        await update.message.reply_text("❌ Minimum bet is 1,000 coins!")
        return
    
    if users[user_id].get("coins", 0) < bet:
        await update.message.reply_text(f"❌ You don't have enough coins! Need {bet:,} coins.")
        return
    
    if user_id in active_crash_games:
        await update.message.reply_text("❌ You already have an active crash game! Finish it first.")
        return
    
    crash_point = random.uniform(1.01, CRASH_MAX_MULTIPLIER)
    if crash_point > 5:
        crash_point = random.uniform(1.01, 5)
        if random.random() > 0.3:
            crash_point = random.uniform(1.01, 3)
    
    active_crash_games[user_id] = {
        "bet": bet,
        "multiplier": 1.0,
        "crash_point": crash_point,
        "running": True,
        "message_id": None,
        "chat_id": update.effective_chat.id
    }
    
    keyboard = [
        [InlineKeyboardButton("📈 HOLD", callback_data="crash_hold")],
        [InlineKeyboardButton("💰 CASH OUT", callback_data="crash_cashout")]
    ]
    
    msg = await update.message.reply_text(
        f"🚀 <b>CRASH GAME STARTED!</b> 🚀\n\n"
        f"💰 Bet: {bet:,} coins\n"
        f"📊 Multiplier: 1.00x\n"
        f"🎯 Potential win: {bet:,} coins\n\n"
        f"Click CASH OUT before it crashes!",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    active_crash_games[user_id]["message_id"] = msg.message_id
    asyncio.create_task(run_crash_game(user_id, context))

async def run_crash_game(user_id, context):
    game = active_crash_games.get(user_id)
    if not game:
        return
    
    multiplier = 1.0
    
    while game["running"] and multiplier < game["crash_point"]:
        await asyncio.sleep(CRASH_UPDATE_INTERVAL)
        
        if not game["running"]:
            break
        
        multiplier += CRASH_INCREMENT
        game["multiplier"] = multiplier
        
        if multiplier >= game["crash_point"]:
            game["running"] = False
            
            try:
                await context.bot.edit_message_text(
                    chat_id=game["chat_id"],
                    message_id=game["message_id"],
                    text=f"💥 <b>CRASHED AT {game['crash_point']:.2f}x!</b> 💥\n\n"
                         f"💰 Bet: {game['bet']:,} coins\n"
                         f"📊 Final multiplier: {game['crash_point']:.2f}x\n"
                         f"❌ You lost {game['bet']:,} coins!",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            
            async with await get_user_lock(user_id):
                users[user_id]["coins"] -= game["bet"]
                save_data()
            
            del active_crash_games[user_id]
            return
        
        potential_win = int(game["bet"] * multiplier)
        try:
            keyboard = [
                [InlineKeyboardButton("📈 HOLD", callback_data="crash_hold")],
                [InlineKeyboardButton("💰 CASH OUT", callback_data="crash_cashout")]
            ]
            await context.bot.edit_message_text(
                chat_id=game["chat_id"],
                message_id=game["message_id"],
                text=f"🚀 <b>CRASH GAME</b> 🚀\n\n"
                     f"💰 Bet: {game['bet']:,} coins\n"
                     f"📊 Multiplier: {multiplier:.2f}x\n"
                     f"🎯 Potential win: {potential_win:,} coins\n\n"
                     f"Click CASH OUT before it crashes!",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception:
            pass

async def crash_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    ensure_user(user_id)
    
    game = active_crash_games.get(user_id)
    if not game:
        await query.edit_message_text("❌ No active crash game! Use /crash to start.")
        return
    
    if query.data == "crash_hold":
        pass  # Already answered at top, nothing else to do
    
    elif query.data == "crash_cashout":
        if not game["running"]:
            await query.answer("❌ Game already ended!", show_alert=True)
            return
        
        game["running"] = False
        win_amount = int(game["bet"] * game["multiplier"])
        
        async with await get_user_lock(user_id):
            users[user_id]["coins"] += win_amount - game["bet"]
            stats = users[user_id]["game_stats"]["crash"]
            stats["played"] = stats.get("played", 0) + 1
            stats["won"] = stats.get("won", 0) + 1
            stats["profit"] = stats.get("profit", 0) + (win_amount - game["bet"])
            if win_amount > stats.get("biggest_win", 0):
                stats["biggest_win"] = win_amount
            save_data()
        
        await query.edit_message_text(
            f"🎉 <b>YOU CASHED OUT!</b> 🎉\n\n"
            f"💰 Bet: {game['bet']:,} coins\n"
            f"📊 Multiplier: {game['multiplier']:.2f}x\n"
            f"🏆 You won: {win_amount:,} coins!\n"
            f"📈 Profit: +{win_amount - game['bet']:,} coins",
            parse_mode="HTML"
        )
        
        del active_crash_games[user_id]

# ═══════════════════════════════════════════════════════════════
# CARD SETS COMMAND
# ═══════════════════════════════════════════════════════════════

async def sets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    
    user_cards = users[user_id].get("characters", [])
    user_card_ids = [str(c.get("card_id")) for c in user_cards if c.get("card_id")]
    user_completed = users[user_id].get("completed_sets", [])
    
    if not anime_sets_cache:
        await update.message.reply_text("No anime sets available yet!")
        return
    
    text = "📚 <b>ANIME CARD SETS</b>\n\n"
    text += "Complete all cards from an anime to win BIG rewards!\n\n"
    
    for anime, required_cards in list(anime_sets_cache.items())[:15]:
        owned_count = sum(1 for card_id in required_cards if card_id in user_card_ids)
        reward = get_set_reward(len(required_cards))
        
        if anime in user_completed:
            status = "✅"
        elif owned_count == len(required_cards):
            status = "🎉"
        else:
            status = "📦"
        
        text += f"{status} <b>{anime}</b>\n"
        text += f"   Cards: {owned_count}/{len(required_cards)} | Reward: {reward:,} coins\n\n"
    
    text += f"\n💡 <b>Tip:</b> Collect all cards from one anime to get big bonuses!\n"
    text += f"🎯 Use /summon to get more cards!"
    
    await update.message.reply_text(text, parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════
# GIFT COMMAND
# ═══════════════════════════════════════════════════════════════

async def gift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "❌ Reply to the person you want to gift!\n\n"
            "Usage:\n"
            "/gift 5000 - Send coins\n"
            "/gift card CARD123 - Send specific card\n"
            "/gift random - Send random card"
        )
        return
    
    target_user = update.message.reply_to_message.from_user
    target_id = str(target_user.id)
    
    if target_id == user_id:
        await update.message.reply_text("❌ You cannot gift yourself!")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /gift [amount] or /gift card [card_id] or /gift random")
        return
    
    ensure_user(target_id)
    
    last_gift = user_gift_cooldown.get(user_id, 0)
    if time.time() - last_gift < GIFT_COOLDOWN:
        remaining = int(GIFT_COOLDOWN - (time.time() - last_gift))
        await update.message.reply_text(f"⏳ Please wait {remaining} seconds before gifting again!")
        return
    
    daily_gifted = user_gift_daily.get(user_id, 0)
    today = datetime.now().strftime("%Y-%m-%d")
    if user_gift_daily.get(f"{user_id}_date") != today:
        user_gift_daily[user_id] = 0
        user_gift_daily[f"{user_id}_date"] = today
        daily_gifted = 0
    
    if context.args[0].lower() == "card" and len(context.args) > 1:
        card_id = context.args[1].strip().lstrip("#").zfill(4)
        
        async with await get_user_lock(user_id), await get_user_lock(target_id):
            sender_cards = users[user_id].get("characters", [])
            card = None
            for c in sender_cards:
                if str(c.get("card_id", "")).zfill(4) == card_id:
                    card = c
                    break
            
            if not card:
                await update.message.reply_text(f"❌ You don't have card #{card_id}!")
                return
            
            if card.get("favourite"):
                await update.message.reply_text("❌ Cannot gift a favourited card!")
                return
            
            # Remove only ONE copy (important now that multi-copies are allowed)
            removed = False
            new_chars = []
            for c in sender_cards:
                if not removed and str(c.get("card_id", "")).zfill(4) == card_id and not c.get("favourite"):
                    removed = True
                else:
                    new_chars.append(c)
            users[user_id]["characters"] = new_chars
            card_copy = dict(card)
            card_copy["favourite"] = False
            users[target_id]["characters"].append(card_copy)
            save_data()
        
        await update.message.reply_text(
            f"🎁 <b>GIFT SENT!</b>\n\n"
            f"📤 From: {html.escape(update.effective_user.first_name)}\n"
            f"📥 To: {html.escape(target_user.first_name)}\n"
            f"🎴 Card: {html.escape(card.get('name', '?'))} (#{card_id})",
            parse_mode="HTML"
        )
        
        user_gift_cooldown[user_id] = time.time()
        await check_anime_completion(target_id, context)
        
    elif context.args[0].lower() == "random":
        async with await get_user_lock(user_id), await get_user_lock(target_id):
            sender_cards = users[user_id].get("characters", [])
            available_cards = [c for c in sender_cards if not c.get("favourite")]
            
            if not available_cards:
                await update.message.reply_text("❌ You have no cards to gift!")
                return
            
            card = random.choice(available_cards)
            card_id = str(card.get("card_id", "")).zfill(4)
            
            # Remove only ONE copy
            removed = False
            new_chars = []
            for c in sender_cards:
                if not removed and str(c.get("card_id", "")).zfill(4) == card_id:
                    removed = True
                else:
                    new_chars.append(c)
            users[user_id]["characters"] = new_chars
            card_copy = dict(card)
            card_copy["favourite"] = False
            users[target_id]["characters"].append(card_copy)
            save_data()
        
        await update.message.reply_text(
            f"🎁 <b>RANDOM GIFT SENT!</b>\n\n"
            f"📤 From: {html.escape(update.effective_user.first_name)}\n"
            f"📥 To: {html.escape(target_user.first_name)}\n"
            f"🎴 Card: {html.escape(card.get('name', '?'))} (#{card_id})",
            parse_mode="HTML"
        )
        
        user_gift_cooldown[user_id] = time.time()
        await check_anime_completion(target_id, context)
        
    else:
        if not context.args[0].isdigit():
            await update.message.reply_text("❌ Invalid amount! Usage: /gift 5000")
            return
        
        amount = int(context.args[0])
        
        if amount < 100:
            await update.message.reply_text("❌ Minimum gift is 100 coins!")
            return
        
        if daily_gifted + amount > MAX_GIFT_DAILY:
            remaining = MAX_GIFT_DAILY - daily_gifted
            await update.message.reply_text(f"❌ Daily gift limit reached! You can only gift {remaining:,} more coins today.")
            return
        
        async with await get_user_lock(user_id), await get_user_lock(target_id):
            if users[user_id].get("coins", 0) < amount:
                await update.message.reply_text(f"❌ You don't have enough coins! Need {amount:,} coins.")
                return
            
            users[user_id]["coins"] -= amount
            users[target_id]["coins"] = users[target_id].get("coins", 0) + amount
            user_gift_daily[user_id] = daily_gifted + amount
            save_data()
        
        await update.message.reply_text(
            f"🎁 <b>GIFT SENT!</b>\n\n"
            f"📤 From: {html.escape(update.effective_user.first_name)}\n"
            f"📥 To: {html.escape(target_user.first_name)}\n"
            f"💰 Amount: {amount:,} coins",
            parse_mode="HTML"
        )
        
        user_gift_cooldown[user_id] = time.time()
        
        try:
            await context.bot.send_message(
                chat_id=int(target_id),
                text=f"🎁 <b>GIFT RECEIVED!</b>\n\n"
                     f"📤 From: {html.escape(update.effective_user.first_name)}\n"
                     f"💰 Amount: {amount:,} coins\n"
                     f"💳 Your new balance: {users[target_id].get('coins', 0):,} coins",
                parse_mode="HTML"
            )
        except Exception:
            pass

# ═══════════════════════════════════════════════════════════════
# CUSTOM TITLE COMMANDS
# ═══════════════════════════════════════════════════════════════

async def title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    
    if not context.args:
        current_title = users[user_id].get("title")
        title_text = f"Current title: <b>{html.escape(current_title)}</b>" if current_title else "No title equipped"
        
        await update.message.reply_text(
            f"👤 <b>CUSTOM TITLE SYSTEM</b>\n\n"
            f"{title_text}\n\n"
            f"📋 <b>Commands:</b>\n"
            f"/title shop - View available titles\n"
            f"/title buy [number] - Buy preset title\n"
            f"/title buy custom [text] - Buy custom title (max {MAX_TITLE_LENGTH} chars)\n"
            f"/title set [title name] - Equip a title you own\n"
            f"/title remove - Remove your current title (FREE)\n\n"
            f"💰 Preset titles: 10,000 coins each\n"
            f"✏️ Custom title: {CUSTOM_TITLE_PRICE:,} coins",
            parse_mode="HTML"
        )
        return
    
    subcommand = context.args[0].lower()
    
    if subcommand == "shop":
        text = "🏪 <b>TITLE SHOP</b>\n\n"
        text += "<b>Preset Titles (10,000 coins each):</b>\n"
        
        for num, title_info in PRESET_TITLES.items():
            text += f"{num}. {title_info['name']}\n"
        
        text += f"\n<b>Custom Title ({CUSTOM_TITLE_PRICE:,} coins):</b>\n"
        text += f"• Write your own title (max {MAX_TITLE_LENGTH} characters)\n"
        text += f"• Use: /title buy custom Your Title Here\n\n"
        text += f"💡 /title remove - Remove title (FREE)"
        
        await update.message.reply_text(text, parse_mode="HTML")
    
    elif subcommand == "buy":
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /title buy [number] or /title buy custom [text]")
            return
        
        if context.args[1].lower() == "custom":
            custom_text = " ".join(context.args[2:])
            if not custom_text:
                await update.message.reply_text("❌ Please provide a custom title text!")
                return
            
            if len(custom_text) > MAX_TITLE_LENGTH:
                await update.message.reply_text(f"❌ Title too long! Max {MAX_TITLE_LENGTH} characters.")
                return
            
            bad_words = ["fuck", "shit", "ass", "bitch", "damn", "cunt", "nigga", "whore", "slut"]
            if any(word in custom_text.lower() for word in bad_words):
                await update.message.reply_text("❌ Title contains inappropriate language!")
                return
            
            async with await get_user_lock(user_id):
                if users[user_id].get("coins", 0) < CUSTOM_TITLE_PRICE:
                    await update.message.reply_text(f"❌ Need {CUSTOM_TITLE_PRICE:,} coins for custom title!")
                    return
                
                users[user_id]["coins"] -= CUSTOM_TITLE_PRICE
                if "custom_titles" not in users[user_id]:
                    users[user_id]["custom_titles"] = []
                users[user_id]["custom_titles"].append(custom_text)
                users[user_id]["owned_titles"].append(custom_text)
                save_data()
            
            await update.message.reply_text(
                f"✅ Custom title <b>{html.escape(custom_text)}</b> purchased!\n"
                f"Use /title set {html.escape(custom_text)} to equip it.",
                parse_mode="HTML"
            )
        
        else:
            try:
                title_num = int(context.args[1])
                if title_num not in PRESET_TITLES:
                    await update.message.reply_text("❌ Invalid title number! Use /title shop to see available titles.")
                    return
                
                title_info = PRESET_TITLES[title_num]
                title_name = title_info["name"]
                price = title_info["price"]
                
                async with await get_user_lock(user_id):
                    if users[user_id].get("coins", 0) < price:
                        await update.message.reply_text(f"❌ Need {price:,} coins for {title_name}!")
                        return
                    
                    if title_name in users[user_id].get("owned_titles", []):
                        await update.message.reply_text(f"❌ You already own {title_name}!")
                        return
                    
                    users[user_id]["coins"] -= price
                    users[user_id].setdefault("owned_titles", []).append(title_name)
                    save_data()
                
                await update.message.reply_text(
                    f"✅ Title <b>{title_name}</b> purchased!\n"
                    f"Use /title set {title_name} to equip it.",
                    parse_mode="HTML"
                )
            except ValueError:
                await update.message.reply_text("❌ Invalid title number!")
    
    elif subcommand == "set":
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /title set [title name]")
            return
        
        title_name = " ".join(context.args[1:])
        owned_titles = users[user_id].get("owned_titles", [])
        
        if title_name not in owned_titles:
            preset_names = [t["name"] for t in PRESET_TITLES.values()]
            if title_name in preset_names:
                await update.message.reply_text(f"❌ You don't own {title_name}. Use /title buy to purchase it first!")
            else:
                await update.message.reply_text(f"❌ Title '{title_name}' not found or you don't own it!")
            return
        
        async with await get_user_lock(user_id):
            users[user_id]["title"] = title_name
            save_data()
        
        await update.message.reply_text(
            f"✅ Title changed to: <b>{html.escape(title_name)}</b>",
            parse_mode="HTML"
        )
    
    elif subcommand == "remove":
        async with await get_user_lock(user_id):
            users[user_id]["title"] = None
            save_data()
        
        await update.message.reply_text("✅ Title removed! (FREE)")

# ═══════════════════════════════════════════════════════════════
# CREDITS CALLBACK
# ═══════════════════════════════════════════════════════════════

async def credits_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.answer(
        "🌸 Bot made by @Giggaa_niggaa\n\nThanks for using this bot! ❤️",
        show_alert=True
    )

# ═══════════════════════════════════════════════════════════════
# START COMMAND
# ═══════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    if str(user_id) != str(OWNER_ID):
        is_new_user = user_id not in users
        ensure_user(user_id, update)

        # ── Feature 1: Notify owner when someone starts the bot ──
        if is_new_user:
            try:
                u = update.effective_user
                uname = f"@{u.username}" if u.username else "no username"
                await context.bot.send_message(
                    chat_id=OWNER_ID,
                    text=(
                        f"🆕 <b>New user started the bot!</b>\n\n"
                        f"👤 Name: {html.escape(u.first_name)}\n"
                        f"🔗 Username: {uname}\n"
                        f"🆔 User ID: <code>{u.id}</code>"
                    ),
                    parse_mode="HTML"
                )
            except Exception as e:
                logging.error(f"Failed to notify owner of new user: {e}")
        user_data = users[user_id]
        coins = user_data.get("coins", 0)
        cards = len(user_data.get("characters", []))
        streak = checkin_streak.get(user_id, 0)
        current_title = user_data.get("title")
        
        title_line = f"\n🏷️ Title: <b>{html.escape(current_title)}</b>" if current_title else ""
        name = html.escape(update.effective_user.first_name)

        # GROUP: show old simple text, no buttons
        if update.effective_chat.type in ("group", "supergroup"):
            welcome_text = (
                f"🎮 <b>Welcome to Anime Card Bot!</b>\n\n"
                f"👤 <b>Your Stats:</b>\n"
                f"├ 💰 Coins: {coins:,}\n"
                f"├ 🎴 Cards: {cards}\n"
                f"├ 📅 Streak: {streak} days{title_line}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📋 <b>Quick Start:</b>\n"
                f"• /bonus - Free daily coins\n"
                f"• /summon - Get random card\n"
                f"• /checkin - Daily streak\n"
                f"• /rps - Play games\n"
                f"• /crash - Play Crash game\n"
                f"• /sets - View anime sets\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"❓ Use /help for all commands"
            )
            await update.message.reply_text(welcome_text, parse_mode="HTML")
            return

        # DM: show welcome + buttons
        welcome_text = (
            f"✨ <b>Welcome, {name}!</b> ✨\n\n"
            f"👤 <b>Your Stats:</b>\n"
            f"├ 💰 Coins: {coins:,}\n"
            f"├ 🎴 Cards: {cards}\n"
            f"├ 📅 Streak: {streak} days{title_line}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 <b>Quick Start:</b>\n"
            f"• /bonus - Free daily coins\n"
            f"• /summon - Get random card\n"
            f"• /checkin - Daily streak\n"
            f"• /collection - View your cards\n\n"
            f"❓ Use /help for all commands"
        )
        
        keyboard = [
            [InlineKeyboardButton("💬 Support Chat", url="https://t.me/Baddiescapture")],
            [InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{context.bot.username}?startgroup=true")],
            [InlineKeyboardButton("⭐ Credits", callback_data="show_credits")]
        ]
        
        await update.message.reply_text(
            welcome_text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    ensure_user(user_id, update)
    save_data()
    
    bot_status = "✅ ONLINE"
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    
    db_status = "✅ CONNECTED"
    try:
        mongo_client.admin.command('ping')
    except:
        db_status = "❌ DISCONNECTED"
    
    total_users = len(users)
    total_cards = len(characters)
    total_chars_in_system = sum(len(u.get("characters", [])) for u in users.values())
    
    active_rps = sum(1 for g in active_games.values() if g.get("game") == "rps")
    active_hl = sum(1 for g in active_games.values() if g.get("game") == "hl")
    active_mines = sum(1 for g in active_games.values() if g.get("game") == "mines")
    active_crash = len(active_crash_games)
    active_bids_count = len(active_bid)
    active_trades_count = len(active_trades)
    
    uptime_seconds = time.time() - bot_start_time
    uptime_str = format_time(uptime_seconds)
    
    status_text = f"""
🤖 <b>BOT STATUS REPORT</b>

━━━━━━━━━━━━━━━━━━━━━
📊 <b>SYSTEM INFO</b>
├ Bot Status: {bot_status}
├ Python: v{python_version}
└ Uptime: {uptime_str}

━━━━━━━━━━━━━━━━━━━━━
💾 <b>DATABASE</b>
├ MongoDB: {db_status}
├ Total Users: {total_users:,}
├ Total Cards in DB: {total_cards:,}
└ Cards Owned: {total_chars_in_system:,}

━━━━━━━━━━━━━━━━━━━━━
🎮 <b>ACTIVE GAMES</b>
├ RPS: {active_rps}
├ Higher/Lower: {active_hl}
├ Minesweeper: {active_mines}
├ Crash: {active_crash}
├ Bids: {active_bids_count}
└ Trades: {active_trades_count}

━━━━━━━━━━━━━━━━━━━━━
⚙️ <b>CONFIGURATION</b>
├ Spawn Every: {SPAWN_EVERY} messages
├ Bid Duration: {BID_DURATION}s
├ Trade Timeout: {TRADE_TIMEOUT}s
└ Shop Pages: {SHOP_MAX_PAGES}

━━━━━━━━━━━━━━━━━━━━━
✨ <b>NEW FEATURES</b>
├ 🚀 Crash Game - /crash
├ 🎁 Gift System - /gift
├ 📚 Anime Sets - /sets
└ 🏷️ Custom Titles - /title

━━━━━━━━━━━━━━━━━━━━━
💡 <b>QUICK COMMANDS</b>
Use /help for all commands
    """
    
    await update.message.reply_text(status_text, parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════
# HELP COMMAND
# ═══════════════════════════════════════════════════════════════

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
        "/rps - Rock Paper Scissors\n"
        "/hl - Higher or Lower\n"
        "/mines - Minesweeper\n"
        "/crash [amount] - Crash game (cash out before crash!)\n"
        "/guess [amount] - Guess the number game\n"
        "/gleader - Guess game leaderboard\n"
        "/gstats - Your guess stats\n"
        "/stats - All game stats\n\n"
        "🎁 <b>Social:</b>\n"
        "/gift [amount] - Send coins to replied user\n"
        "/gift card [id] - Send card to replied user\n"
        "/gift random - Send random card to replied user\n\n"
        "📚 <b>Collections:</b>\n"
        "/sets - View anime card sets and progress\n\n"
        "🏷️ <b>Customization:</b>\n"
        "/title - View your title\n"
        "/title shop - Buy titles\n"
        "/title set [name] - Equip title\n"
        "/title remove - Remove title (FREE)\n\n"
        "🏆 <b>Leaderboard:</b>\n"
        "/ctop - Coin leaderboard\n\n"
        "👑 <b>Admin:</b>\n"
        "/upload - Upload character (uploaders)\n"
        "/adduploader - Add uploader (owner)\n"
        "/give - Give coins (owner)\n"
        "/broadcast - Announce (owner)\n"
        "/debugspawn - Check spawn status (owner)\n"
        "/forcespawn - Force spawn (owner)\n"
        "/resetspawn - Reset spawn counter (owner)",
        parse_mode="HTML"
    )

# ═══════════════════════════════════════════════════════════════
# PROFILE
# ═══════════════════════════════════════════════════════════════

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    d = users[user_id]
    coin_rank = get_user_rank(user_id, "coins")
    current_title = d.get("title")
    
    title_line = f"\n🏷️ Title: <b>{html.escape(current_title)}</b>" if current_title else ""
    
    text = (
        f"👤 <b>Profile</b>\n\n"
        f"🏷 Name: {html.escape(update.effective_user.first_name)}{title_line}\n"
        f"💰 Coins: {d.get('coins', 0):,} (Rank #{coin_rank if coin_rank else 'N/A'})\n"
        f"🎴 Cards: {len(d.get('characters', []))}\n"
        f"📅 Check-in streak: {checkin_streak.get(user_id, 0)} days"
    )
    await update.message.reply_text(text, parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════
# COLLECTION BUTTONS
# ═══════════════════════════════════════════════════════════════

async def collection_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    ensure_user(user_id)
    chat_id = query.message.chat_id

    # ── Basic collection nav ──────────────────────────────────
    if query.data == "col_close":
        await query.message.delete()
        return

    elif query.data == "col_back":
        await query.message.delete()
        await send_collection_page(chat_id, context, user_id)
        return

    elif query.data == "col_next":
        context.user_data["col_page"] = context.user_data.get("col_page", 0) + 1
        total = len(context.user_data.get("col_list", []))
        max_page = max(0, (total - 1) // ITEMS_PER_PAGE)
        context.user_data["col_page"] = min(context.user_data["col_page"], max_page)
        await query.message.delete()
        await send_collection_page(chat_id, context, user_id)
        return

    elif query.data == "col_prev":
        context.user_data["col_page"] = max(0, context.user_data.get("col_page", 0) - 1)
        await query.message.delete()
        await send_collection_page(chat_id, context, user_id)
        return

    # ── Open gallery ──────────────────────────────────────────
    elif query.data.startswith("col_gallery_"):
        context.user_data["gal_search"] = ""
        context.user_data["gal_rarity"] = 0
        await query.message.delete()
        await send_gallery_page(chat_id, context, user_id, 0)
        return

    # ── Send a card to chat ───────────────────────────────────
    elif query.data.startswith("col_send_"):
        card_id = query.data[len("col_send_"):]
        collection = users[user_id].get("characters", [])
        char = next(
            (c for c in collection if str(c.get("card_id","")).zfill(4) == card_id.zfill(4)),
            None
        )
        if not char:
            await query.answer("❌ Card not found!", show_alert=True)
            return
        rarity_full = {1:"⚪ Common",2:"🔵 Rare",3:"🟣 Epic",4:"🟡 Legendary",5:"⚡ Celebrity"}
        rl    = rarity_full.get(char.get("rarity",1),"❓")
        name  = html.escape(char.get("name","?"))
        anime = html.escape(char.get("anime","?"))
        cid   = str(char.get("card_id","????")).zfill(4)
        qty   = get_card_qty(user_id, cid)
        price = get_card_price(char.get("rarity",1))
        owner = html.escape(users[user_id].get("name","Unknown"))
        caption = (
            f"🎴 <b>{name}</b>\n"
            f"📺 <b>Anime:</b> {anime}\n"
            f"{rl}\n"
            f"🪪 <b>ID:</b> #{cid}\n"
            f"💰 <b>Price:</b> {price:,} coins\n"
            f"🗂 <b>Owned by {owner}:</b> {qty}x"
        )
        try:
            if char.get("file_id"):
                await context.bot.send_photo(chat_id=chat_id, photo=char["file_id"],
                                             caption=caption, parse_mode="HTML")
            else:
                await context.bot.send_message(chat_id=chat_id, text=caption, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Send card error: {e}")
            await query.answer("❌ Failed to send!", show_alert=True)
        return

    # ── Gallery pagination ────────────────────────────────────
    elif query.data.startswith("gal_page_"):
        page = int(query.data[len("gal_page_"):])
        await query.message.delete()
        await send_gallery_page(chat_id, context, user_id, page)
        return

    elif query.data == "gal_noop":
        return

    # ── Gallery: tap a card to see detail ────────────────────
    elif query.data.startswith("gal_view_"):
        card_id = query.data[len("gal_view_"):]
        await query.message.delete()
        await send_gallery_card_detail(chat_id, context, user_id, card_id)
        return

    # ── Gallery: back from detail to grid ────────────────────
    elif query.data == "gal_back":
        await query.message.delete()
        page = context.user_data.get("gal_last_page", 0)
        await send_gallery_page(chat_id, context, user_id, page)
        return

    # ── Gallery: cycle rarity filter ─────────────────────────
    elif query.data.startswith("gal_rar_"):
        rarity = int(query.data[len("gal_rar_"):])
        context.user_data["gal_rarity"] = rarity
        await query.message.delete()
        await send_gallery_page(chat_id, context, user_id, 0)
        return

    # ── Gallery: search prompt ────────────────────────────────
    elif query.data == "gal_search_prompt":
        context.user_data["awaiting_gal_search"] = True
        await query.answer("Type your search in the chat now 👇", show_alert=True)
        return

    elif query.data == "gal_clear_search":
        context.user_data["gal_search"] = ""
        context.user_data["awaiting_gal_search"] = False
        await query.message.delete()
        await send_gallery_page(chat_id, context, user_id, 0)
        return



# ═══════════════════════════════════════════════════════════════
# BONUS
# ═══════════════════════════════════════════════════════════════

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
    except Exception as e:
        logging.error(f"Failed to edit bonus message: {e}")

# ═══════════════════════════════════════════════════════════════
# MYCARDS
# ═══════════════════════════════════════════════════════════════

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

# ═══════════════════════════════════════════════════════════════
# SUMMON (with auto-set check)
# ═══════════════════════════════════════════════════════════════

async def summon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    now = time.time()
    ensure_user(user_id, update)
    cost = 7000

    if update.effective_chat.id != GROUP_ID:
        await update.message.reply_text(
            "❌ <b>Summon only works in the official group!</b>\n\n"
            "👇 Join here and try again:\n"
            "https://t.me/Baddiescapture",
            parse_mode="HTML"
        )
        return

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

        rarity_weights = {1: 55, 2: 25, 3: 10, 4: 8, 5: 2}
        rarity = random.choices(list(rarity_weights.keys()), weights=rarity_weights.values())[0]
        pool = [c for c in characters if c.get("rarity") == rarity]
        if not pool:
            pool = characters

        character = random.choice(pool)
        
        users[user_id]["coins"] -= cost
        summon_cooldowns[user_id] = now
        users[user_id]["characters"].append(dict(character))
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
    except Exception as e:
        logging.error(f"Failed to send summon photo: {e}")
        await update.message.reply_text(caption, parse_mode="HTML")
    
    await check_anime_completion(user_id, context)

# ═══════════════════════════════════════════════════════════════
# SHOP
# ═══════════════════════════════════════════════════════════════

async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)

    if not characters:
        await update.message.reply_text("🏪 Shop is empty right now!")
        return

    rotate_daily_shop()  # ensure today's shop is ready
    context.user_data["shop_page"] = 0
    await send_shop_page(update.effective_chat.id, context, user_id)

async def shop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    ensure_user(user_id)

    if query.data == "shop_close":
        await query.message.delete()
        return
    
    if query.data == "shop_refresh":
        # Feature 4: pay coins to refresh shop
        async with await get_user_lock(user_id):
            coins = users[user_id].get("coins", 0)
            if coins < SHOP_REFRESH_COST:
                await query.answer(f"❌ Need {SHOP_REFRESH_COST:,} coins to refresh!", show_alert=True)
                return
            users[user_id]["coins"] -= SHOP_REFRESH_COST
            refresh_shop_manual()
            save_data()
        context.user_data["shop_page"] = 0
        await query.message.delete()
        await send_shop_page(query.message.chat_id, context, user_id)
        await query.answer(f"🔄 Shop refreshed! (-{SHOP_REFRESH_COST:,} coins)", show_alert=True)
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
        # search in daily shop first, then full list
        char = next((c for c in daily_shop_chars if str(c.get("card_id")) == str(card_id)), None)
        if not char:
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
        
        await check_anime_completion(user_id, context)

# ═══════════════════════════════════════════════════════════════
# FAVOURITE
# ═══════════════════════════════════════════════════════════════

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

# ═══════════════════════════════════════════════════════════════
# MYFAVOURITES
# ═══════════════════════════════════════════════════════════════

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

# ═══════════════════════════════════════════════════════════════
# CARD INFO
# ═══════════════════════════════════════════════════════════════

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
        if any(str(c.get("card_id","")).zfill(4) == card_id for c in udata.get("characters",[]) if isinstance(c, dict))
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
    except Exception as e:
        logging.error(f"Failed to send cardinfo photo: {e}")
        await update.message.reply_text(caption, parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════
# TRADE SYSTEM
# ═══════════════════════════════════════════════════════════════

async def trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        "chat_id": update.effective_chat.id,
        "created_at": time.time(),
        "expires_at": time.time() + TRADE_TIMEOUT
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
    ensure_user(user_id)
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
                # Remove ONE copy of offer card from sender
                oc_cid = str(oc.get("card_id", "")).zfill(4)
                removed = False
                new_sid_chars = []
                for c in users[sid].get("characters", []):
                    if not removed and str(c.get("card_id", "")).zfill(4) == oc_cid:
                        removed = True
                    else:
                        new_sid_chars.append(c)
                users[sid]["characters"] = new_sid_chars

                # Remove ONE copy of want card from receiver
                wc_cid = str(wc.get("card_id", "")).zfill(4)
                removed = False
                new_rid_chars = []
                for c in users[rid].get("characters", []):
                    if not removed and str(c.get("card_id", "")).zfill(4) == wc_cid:
                        removed = True
                    else:
                        new_rid_chars.append(c)
                users[rid]["characters"] = new_rid_chars
                
                oc_copy = dict(oc)
                oc_copy["favourite"] = False
                wc_copy = dict(wc)
                wc_copy["favourite"] = False
                users[rid]["characters"].append(oc_copy)
                users[sid]["characters"].append(wc_copy)
                active_trades.pop(tid, None)
                save_data()
            
            await query.edit_message_text(
                f"🎉 <b>Trade Complete!</b>\n\n"
                f"Cards swapped successfully! ✅",
                parse_mode="HTML"
            )
            
            await check_anime_completion(sid, context)
            await check_anime_completion(rid, context)
        else:
            name = html.escape(query.from_user.first_name)
            try:
                await query.edit_message_text(
                    query.message.text + f"\n\n✅ <b>{name}</b> confirmed! Waiting for other player...",
                    parse_mode="HTML",
                    reply_markup=query.message.reply_markup
                )
            except Exception as e:
                logging.error(f"Failed to update trade message: {e}")

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
                except Exception as e:
                    logging.error(f"Failed to send trade expiry message: {e}")

# ═══════════════════════════════════════════════════════════════
# UPLOAD, ADDUPLOADER, GIVE, BROADCAST, CTOP
# ═══════════════════════════════════════════════════════════════

async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in uploaders and user_id != str(OWNER_ID):
        await update.message.reply_text("❌ You are not an uploader.")
        return
    
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ Reply to a photo with: /upload Name | Anime | Rarity(1-5)")
        return
    
    if not update.message.reply_to_message.photo:
        await update.message.reply_text("❌ The replied message must contain a PHOTO!")
        return
    
    if not context.args:
        await update.message.reply_text("Format: /upload Name | Anime | 1-5\n1=Common 2=Rare 3=Epic 4=Legendary 5=Celebrity")
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

    photo = update.message.reply_to_message.photo[-1]
    file_id = photo.file_id
    
    card_id = next_card_id()
    characters.append({"card_id": card_id, "name": name, "anime": anime, "rarity": rarity, "file_id": file_id})
    save_data()
    init_anime_sets()

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
    except Exception as e:
        logging.error(f"Failed to send to log channel: {e}")

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
    
    async with await get_user_lock(target_id):
        users[target_id]["coins"] = users[target_id].get("coins", 0) + amount
        save_data()
    
    await update.message.reply_text(f"💰 Gave {amount:,} coins to {html.escape(target.first_name)}!")

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

async def ctop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not users:
        await update.message.reply_text("No players yet!")
        return
    sorted_users = sorted(users.items(), key=lambda x: x[1].get("coins", 0), reverse=True)
    players = []
    for uid, d in sorted_users[:10]:
        name = d.get("name") or "Unknown"
        username = d.get("username", "")
        display = f"@{username}" if username else name
        players.append((display, f"💰 {d.get('coins',0):,}"))
    
    text = "💰 <b>Coin Leaderboard</b>\n\n"
    for i, (name, val) in enumerate(players, 1):
        text += f"{i}. {html.escape(name)} — {val}\n"
    await update.message.reply_text(text, parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════
# CHECK-IN COMMANDS
# ═══════════════════════════════════════════════════════════════

async def checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("❌ Check-in only works in groups!")
        return

    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    now = time.time()
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = datetime.now() - timedelta(days=1)
    yesterday_str = yesterday.strftime("%Y-%m-%d")

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

# ═══════════════════════════════════════════════════════════════
# BID/SPAWN SYSTEM
# ═══════════════════════════════════════════════════════════════

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

async def spawn_character(chat_id, context):
    global spawn_counter
    if not characters:
        logging.warning(f"Attempted to spawn but no characters available!")
        return
    
    if chat_id in active_bid:
        return
    
    valid_chars = [c for c in characters if c.get("file_id")]
    if not valid_chars:
        logging.error("No valid characters with file_id found!")
        return
    
    if chat_id not in group_spawn_counters:
        group_spawn_counters[chat_id] = 0
    
    group_spawn_counters[chat_id] += 1
    spawn_number = group_spawn_counters[chat_id]
    
    character = random.choice(valid_chars)
    rarity = character.get("rarity", 1)
    start_price = {1: 10000, 2: 30000, 3: 80000, 4: 200000, 5: 500000}.get(rarity, 10000)
    
    active_bid[chat_id] = {
        "character": character,
        "top_bidder": None,
        "top_bid": start_price,
        "end_time": time.time() + BID_DURATION,
        "start_time": time.time(),
        "spawn_number": spawn_number
    }
    
    rl = rarity_label(rarity)
    caption = (
        f"🌟 <b>CHARACTER APPEARED! (#{spawn_number})</b>\n\n"
        f"🎴 {html.escape(character.get('name','?'))}\n"
        f"🎬 {html.escape(character.get('anime','?'))}\n"
        f"⭐ {rl} | 🪪 #{character.get('card_id','?')}\n\n"
        f"💰 Starting Bid: {start_price:,}\n"
        f"⏳ {BID_DURATION}s | Use /bid [amount]"
    )
    
    try:
        await context.bot.send_photo(chat_id=chat_id, photo=character["file_id"], caption=caption, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Spawn error in chat {chat_id}: {e}")
        active_bid.pop(chat_id, None)
        return
    
    asyncio.create_task(end_bid_after(chat_id, context))

async def end_bid_after(chat_id, context):
    try:
        while True:
            session = active_bid.get(chat_id)
            if not session:
                return
            
            remaining = session["end_time"] - time.time()
            if remaining <= 0:
                break
            
            await asyncio.sleep(min(5, remaining))
        
        await resolve_bid(chat_id, context)
        
    except asyncio.CancelledError:
        logging.info(f"Bid ending task cancelled for chat {chat_id}")
    except Exception as e:
        logging.error(f"Error in end_bid_after for chat {chat_id}: {e}")
        if chat_id in active_bid:
            active_bid.pop(chat_id)
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Bid system had an error. New character will spawn soon!"
            )
        except Exception:
            pass

async def resolve_bid(chat_id, context):
    session = active_bid.pop(chat_id, None)
    if not session:
        return
    character = session["character"]
    top_bidder = session["top_bidder"]
    top_bid = session["top_bid"]
    spawn_num = session.get("spawn_number", "?")
    
    if not top_bidder:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⌛ No bids for <b>{html.escape(character.get('name','?'))}</b> (Spawn #{spawn_num})! Character lost.",
            parse_mode="HTML"
        )
        return
    
    uid = top_bidder
    ensure_user(uid)
    
    try:
        chat_member = await context.bot.get_chat_member(chat_id, int(uid))
        real_name = chat_member.user.first_name
        users[uid]["name"] = real_name
    except Exception:
        pass
    
    async with await get_user_lock(uid):
        if users[uid]["coins"] < top_bid:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Winner didn't have enough coins! Card lost. (Spawn #{spawn_num})",
                parse_mode="HTML"
            )
            return
        
        users[uid]["coins"] -= top_bid
        users[uid]["characters"].append(dict(character))
        save_data()
    
    name = html.escape(users[uid].get("name", "Unknown"))
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🎉 <b>BID OVER! (Spawn #{spawn_num})</b>\n🏆 Winner: {name}\n🎴 {html.escape(character.get('name','?'))}\n💰 Paid: {top_bid:,}",
        parse_mode="HTML"
    )
    
    await check_anime_completion(uid, context)

# ═══════════════════════════════════════════════════════════════
# GUESS GAME, GLEADER, GSTATS, STATS
# ═══════════════════════════════════════════════════════════════

async def guess_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("❌ Only works in groups!")
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

async def gleader(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not users:
        await update.message.reply_text("No players yet!")
        return
    
    user_wins = []
    for uid, udata in users.items():
        guess_stats = udata.get("game_stats", {}).get("guess", {})
        wins = guess_stats.get("won", 0)
        profit = guess_stats.get("profit", 0)
        if wins > 0:
            name = udata.get("name", "Unknown")
            username = udata.get("username", "")
            display = f"@{username}" if username else html.escape(name)
            user_wins.append((display, wins, profit))
    
    user_wins.sort(key=lambda x: x[1], reverse=True)
    
    text = "🏆 <b>GUESS GAME LEADERBOARD</b> 🏆\n\n"
    
    if user_wins:
        text += "🎯 Top 10 Winners:\n\n"
        for i, (display, wins, profit) in enumerate(user_wins[:10], 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            text += f"{medal} {display} — {wins} wins (💰 {profit:+,} coins)\n"
    else:
        text += "No one has won the guess game yet!\n\n"
        text += "Start a game with /guess [amount]\n"
        text += "Example: /guess 5000"
    
    await update.message.reply_text(text, parse_mode="HTML")

async def gstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    
    guess_stats = users[user_id].get("game_stats", {}).get("guess", {})
    
    played = guess_stats.get("played", 0)
    won = guess_stats.get("won", 0)
    lost = guess_stats.get("lost", 0)
    profit = guess_stats.get("profit", 0)
    best_streak = guess_stats.get("best_streak", 0)
    current_streak = guess_stats.get("current_streak", 0)
    biggest_win = guess_stats.get("biggest_win", 0)
    win_rate = round((won / played) * 100) if played > 0 else 0
    
    text = f"🎯 <b>YOUR GUESS GAME STATS</b>\n\n"
    text += f"📊 Games Played: {played}\n"
    text += f"🏆 Wins: {won} ({win_rate}%)\n"
    text += f"❌ Losses: {lost}\n"
    text += f"💰 Total Profit: {profit:+,} coins\n"
    text += f"🎲 Biggest Win: {biggest_win:,} coins\n"
    text += f"🔥 Current Streak: {current_streak}\n"
    text += f"⭐ Best Streak: {best_streak}\n\n"
    text += f"💡 Play with: /guess [amount]"
    
    await update.message.reply_text(text, parse_mode="HTML")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    
    stats_data = users[user_id].get("game_stats", {})
    rps = stats_data.get("rps", {})
    hl = stats_data.get("hl", {})
    mines = stats_data.get("mines", {})
    guess = stats_data.get("guess", {})
    crash_stats = stats_data.get("crash", {})
    
    coin_rank = get_user_rank(user_id, "coins")
    checkin_streak_count = checkin_streak.get(user_id, 0)
    
    text = f"📊 <b>PLAYER STATS</b> - {html.escape(update.effective_user.first_name)}\n\n"
    text += f"💰 <b>Wallet:</b>\n"
    text += f"└ {users[user_id].get('coins', 0):,} coins (Rank #{coin_rank if coin_rank else 'N/A'})\n\n"
    text += f"🎴 <b>Collection:</b>\n"
    text += f"└ {len(users[user_id].get('characters', []))} cards\n\n"
    text += f"📅 <b>Activity:</b>\n"
    text += f"└ Check-in streak: {checkin_streak_count} days\n\n"
    text += f"🎮 <b>Game Stats:</b>\n\n"
    
    rps_played = rps.get('played', 0)
    rps_won = rps.get('won', 0)
    rps_wr = round((rps_won / rps_played) * 100) if rps_played > 0 else 0
    text += f"✊ <b>Rock Paper Scissors</b>\n"
    text += f"├ Played: {rps_played}\n"
    text += f"├ Wins: {rps_won} ({rps_wr}%)\n"
    text += f"├ Losses: {rps.get('lost', 0)}\n"
    text += f"├ Profit: {rps.get('profit', 0):+,} coins\n"
    text += f"└ Best Streak: {rps.get('best_streak', 0)}\n\n"
    
    hl_played = hl.get('played', 0)
    hl_won = hl.get('won', 0)
    hl_wr = round((hl_won / hl_played) * 100) if hl_played > 0 else 0
    text += f"🃏 <b>Higher/Lower</b>\n"
    text += f"├ Played: {hl_played}\n"
    text += f"├ Wins: {hl_won} ({hl_wr}%)\n"
    text += f"├ Losses: {hl.get('lost', 0)}\n"
    text += f"├ Profit: {hl.get('profit', 0):+,} coins\n"
    text += f"├ Biggest Win: {hl.get('biggest_win', 0):,} coins\n"
    text += f"└ Best Multiplier: {hl.get('best_multiplier', 0)}x\n\n"
    
    mines_played = mines.get('played', 0)
    mines_won = mines.get('won', 0)
    mines_wr = round((mines_won / mines_played) * 100) if mines_played > 0 else 0
    text += f"💣 <b>Minesweeper</b>\n"
    text += f"├ Played: {mines_played}\n"
    text += f"├ Wins: {mines_won} ({mines_wr}%)\n"
    text += f"├ Losses: {mines.get('lost', 0)}\n"
    text += f"├ Profit: {mines.get('profit', 0):+,} coins\n"
    text += f"├ Best Tiles: {mines.get('best_tiles', 0)}\n"
    text += f"└ Best Multiplier: {mines.get('best_multiplier', 0)}x\n\n"
    
    guess_played = guess.get('played', 0)
    guess_won = guess.get('won', 0)
    guess_wr = round((guess_won / guess_played) * 100) if guess_played > 0 else 0
    text += f"🎯 <b>Guess Game</b>\n"
    text += f"├ Played: {guess_played}\n"
    text += f"├ Wins: {guess_won} ({guess_wr}%)\n"
    text += f"├ Losses: {guess.get('lost', 0)}\n"
    text += f"├ Profit: {guess.get('profit', 0):+,} coins\n"
    text += f"├ Biggest Win: {guess.get('biggest_win', 0):,} coins\n"
    text += f"└ Best Streak: {guess.get('best_streak', 0)}\n\n"
    
    crash_played = crash_stats.get('played', 0)
    crash_won = crash_stats.get('won', 0)
    crash_wr = round((crash_won / crash_played) * 100) if crash_played > 0 else 0
    text += f"🚀 <b>Crash Game</b>\n"
    text += f"├ Played: {crash_played}\n"
    text += f"├ Wins: {crash_won} ({crash_wr}%)\n"
    text += f"├ Profit: {crash_stats.get('profit', 0):+,} coins\n"
    text += f"└ Biggest Win: {crash_stats.get('biggest_win', 0):,} coins"
    
    await update.message.reply_text(text, parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════
# RPS, HL, MINES GAMES
# ═══════════════════════════════════════════════════════════════

async def rps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    
    keyboard = [
        [
            InlineKeyboardButton(f"{RPS_DIFFICULTY['easy']['color']} Easy (5,000)", callback_data="rps_easy"),
            InlineKeyboardButton(f"{RPS_DIFFICULTY['medium']['color']} Medium (25,000)", callback_data="rps_medium"),
            InlineKeyboardButton(f"{RPS_DIFFICULTY['hard']['color']} Hard (100,000)", callback_data="rps_hard")
        ]
    ]
    
    await update.message.reply_text(
        "✊📄✂️ <b>ROCK PAPER SCISSORS</b> ✊📄✂️\n\n"
        "Choose your difficulty:\n\n"
        "🟢 Easy: Bet 5,000 - Win 10,000\n"
        "🟡 Medium: Bet 25,000 - Win 50,000\n"
        "🔴 Hard: Bet 100,000 - Win 200,000",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def rps_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    ensure_user(user_id)
    
    if query.data.startswith("rps_move_"):
        move = query.data.split("_")[2]
        game = active_games.get(user_id)
        
        if not game or game.get("game") != "rps":
            await query.edit_message_text("❌ Game expired! Use /rps to start new game.")
            return
        
        moves = {"rock": "✊", "paper": "📄", "scissors": "✂️"}
        bot_move = random.choice(["rock", "paper", "scissors"])
        
        if move == bot_move:
            result = "tie"
            win_amount = 0
        elif (move == "rock" and bot_move == "scissors") or \
             (move == "paper" and bot_move == "rock") or \
             (move == "scissors" and bot_move == "paper"):
            result = "win"
            win_amount = game["bet"] * game["multiplier"]
        else:
            result = "lose"
            win_amount = game["bet"]
        
        async with await get_user_lock(user_id):
            if result == "win":
                users[user_id]["coins"] += win_amount
                stats = users[user_id]["game_stats"]["rps"]
                stats["played"] += 1
                stats["won"] += 1
                stats["profit"] += win_amount
                stats["current_streak"] += 1
                if stats["current_streak"] > stats["best_streak"]:
                    stats["best_streak"] = stats["current_streak"]
            elif result == "lose":
                users[user_id]["coins"] -= game["bet"]
                stats = users[user_id]["game_stats"]["rps"]
                stats["played"] += 1
                stats["lost"] += 1
                stats["profit"] -= game["bet"]
                stats["current_streak"] = 0
            save_data()
        
        if result == "win":
            text = f"🎉 <b>YOU WIN!</b> 🎉\n\n"
        elif result == "lose":
            text = f"❌ <b>YOU LOSE!</b> ❌\n\n"
        else:
            text = f"🤝 <b>TIE!</b> 🤝\n\n"
        
        text += f"Your move: {moves[move]}\n"
        text += f"Bot move: {moves[bot_move]}\n\n"
        
        if result == "win":
            text += f"💰 +{win_amount:,} coins!"
        elif result == "lose":
            text += f"💰 -{game['bet']:,} coins!"
        else:
            text += f"💰 Money back!"
        
        keyboard = [
            [
                InlineKeyboardButton("🟢 Easy", callback_data="rps_easy"),
                InlineKeyboardButton("🟡 Medium", callback_data="rps_medium"),
                InlineKeyboardButton("🔴 Hard", callback_data="rps_hard")
            ],
            [InlineKeyboardButton("❌ Exit", callback_data="rps_exit")]
        ]
        
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        del active_games[user_id]

    elif query.data == "rps_cancel":
        if user_id in active_games:
            del active_games[user_id]
        await query.edit_message_text("❌ Game cancelled. No coins taken!")

    elif query.data == "rps_exit":
        await query.edit_message_text("👋 Thanks for playing! Use /rps to play again.")

    elif query.data.startswith("rps_"):
        difficulty = query.data.split("_")[1]
        
        if difficulty not in RPS_DIFFICULTY:
            return
        
        settings = RPS_DIFFICULTY[difficulty]
        
        if users[user_id].get("coins", 0) < settings["bet"]:
            await query.edit_message_text(
                f"❌ You don't have enough coins!\n"
                f"Need: {settings['bet']:,} coins\n"
                f"You have: {users[user_id].get('coins', 0):,} coins",
                parse_mode="HTML"
            )
            return
        
        active_games[user_id] = {
            "game": "rps",
            "difficulty": difficulty,
            "bet": settings["bet"],
            "multiplier": settings["win_multiplier"],
            "timestamp": time.time()
        }
        
        keyboard = [
            [
                InlineKeyboardButton("✊ ROCK", callback_data="rps_move_rock"),
                InlineKeyboardButton("📄 PAPER", callback_data="rps_move_paper"),
                InlineKeyboardButton("✂️ SCISSORS", callback_data="rps_move_scissors")
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="rps_cancel")]
        ]
        
        await query.edit_message_text(
            f"{settings['color']} <b>ROCK PAPER SCISSORS - {settings['name']}</b> {settings['color']}\n\n"
            f"💰 Bet: {settings['bet']:,} coins\n"
            f"🏆 Win: {settings['bet'] * settings['win_multiplier']:,} coins\n\n"
            f"Choose your move:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# HL and MINES games (keeping existing structure)
async def hl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    
    keyboard = [
        [
            InlineKeyboardButton(f"{HL_DIFFICULTY['easy']['color']} Easy (5,000)", callback_data="hl_easy"),
            InlineKeyboardButton(f"{HL_DIFFICULTY['medium']['color']} Medium (25,000)", callback_data="hl_medium"),
            InlineKeyboardButton(f"{HL_DIFFICULTY['hard']['color']} Hard (100,000)", callback_data="hl_hard")
        ]
    ]
    
    await update.message.reply_text(
        "🃏 <b>HIGHER OR LOWER</b> 🃏\n\n"
        "Choose your difficulty:\n\n"
        "🟢 Easy: Bet 5,000 - Max win 40,000 (8x)\n"
        "🟡 Medium: Bet 25,000 - Max win 400,000 (16x)\n"
        "🔴 Hard: Bet 100,000 - Max win 3,200,000 (32x)",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def hl_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    ensure_user(user_id)
    
    if query.data == "hl_higher" or query.data == "hl_lower":
        game = active_games.get(user_id)
        if not game or game.get("game") != "hl":
            await query.edit_message_text("❌ Game expired! Use /hl to start new game.")
            return
        
        user_choice = "higher" if query.data == "hl_higher" else "lower"
        card_values = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
        card_numbers = {"A": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "10": 10, "J": 11, "Q": 12, "K": 13}
        
        current_num = card_numbers[game["current_card"]]
        new_card = random.choice(card_values)
        new_num = card_numbers[new_card]
        
        is_higher = new_num > current_num
        is_equal = new_num == current_num
        
        if is_equal:
            # Tie = lose
            async with await get_user_lock(user_id):
                current_coins = users[user_id].get("coins", 0)
                deduct = min(game["bet"], current_coins)
                users[user_id]["coins"] -= deduct
                stats = users[user_id]["game_stats"]["hl"]
                stats["played"] += 1
                stats["lost"] += 1
                stats["profit"] -= deduct
                save_data()
            await query.edit_message_text(
                f"🤝 <b>SAME CARD — COUNTS AS LOSS!</b>\n\n"
                f"🃏 Card was: {new_card}\n\n"
                f"💰 You lost {game['bet']:,} coins!",
                parse_mode="HTML"
            )
            del active_games[user_id]
            return
        
        if (user_choice == "higher" and is_higher) or (user_choice == "lower" and not is_higher):
            game["current_multiplier"] *= 2
            game["current_card"] = new_card
            
            if game["current_multiplier"] >= game["max_multiplier"]:
                win_amount = game["bet"] * game["current_multiplier"]
                
                async with await get_user_lock(user_id):
                    users[user_id]["coins"] += win_amount
                    stats = users[user_id]["game_stats"]["hl"]
                    stats["played"] += 1
                    stats["won"] += 1
                    stats["profit"] += win_amount
                    if win_amount > stats.get("biggest_win", 0):
                        stats["biggest_win"] = win_amount
                    if game["current_multiplier"] > stats.get("best_multiplier", 0):
                        stats["best_multiplier"] = game["current_multiplier"]
                    save_data()
                
                await query.edit_message_text(
                    f"🎉 <b>MAX MULTIPLIER REACHED! AUTO CASHOUT!</b> 🎉\n\n"
                    f"🃏 Card was: {new_card}\n"
                    f"✅ You were correct!\n\n"
                    f"💰 Final win: {win_amount:,} coins!",
                    parse_mode="HTML"
                )
                del active_games[user_id]
                return
            
            keyboard = [
                [
                    InlineKeyboardButton("📈 HIGHER", callback_data="hl_higher"),
                    InlineKeyboardButton("📉 LOWER", callback_data="hl_lower")
                ],
                [InlineKeyboardButton("💰 CASH OUT", callback_data="hl_cashout")],
                [InlineKeyboardButton("❌ Cancel", callback_data="hl_cancel")]
            ]
            
            await query.edit_message_text(
                f"✅ <b>CORRECT!</b>\n\n"
                f"🃏 Card was: {new_card}\n"
                f"📊 Multiplier: {game['current_multiplier']}x\n"
                f"💰 Current winnings: {int(game['bet'] * game['current_multiplier']):,} coins\n\n"
                f"🃏 Current card: <b>{new_card}</b>\n\n"
                f"Continue?",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            async with await get_user_lock(user_id):
                current_coins = users[user_id].get("coins", 0)
                deduct = min(game["bet"], current_coins)
                users[user_id]["coins"] -= deduct
                stats = users[user_id]["game_stats"]["hl"]
                stats["played"] += 1
                stats["lost"] += 1
                stats["profit"] -= deduct
                save_data()
            
            await query.edit_message_text(
                f"❌ <b>WRONG!</b>\n\n"
                f"🃏 Card was: {new_card}\n"
                f"You guessed: {user_choice.upper()}\n\n"
                f"💰 You lost {game['bet']:,} coins!",
                parse_mode="HTML"
            )
            del active_games[user_id]

    elif query.data == "hl_cashout":
        game = active_games.get(user_id)
        if not game or game.get("game") != "hl":
            await query.edit_message_text("❌ No active game!")
            return
        
        if game["current_multiplier"] <= 1:
            await query.answer("❌ Make at least one correct guess before cashing out!", show_alert=True)
            return
        
        win_amount = int(game["bet"] * game["current_multiplier"])
        
        async with await get_user_lock(user_id):
            users[user_id]["coins"] += win_amount
            stats = users[user_id]["game_stats"]["hl"]
            stats["played"] += 1
            stats["won"] += 1
            stats["profit"] += win_amount
            if win_amount > stats.get("biggest_win", 0):
                stats["biggest_win"] = win_amount
            if game["current_multiplier"] > stats.get("best_multiplier", 0):
                stats["best_multiplier"] = game["current_multiplier"]
            save_data()
        
        await query.edit_message_text(
            f"💰 <b>YOU CASHED OUT!</b> 💰\n\n"
            f"🎯 Final multiplier: {game['current_multiplier']}x\n"
            f"🏆 You won: {win_amount:,} coins!",
            parse_mode="HTML"
        )
        del active_games[user_id]

    elif query.data == "hl_cancel":
        if user_id in active_games:
            del active_games[user_id]
        await query.edit_message_text("❌ Game cancelled. No coins taken!")

    elif query.data.startswith("hl_"):
        difficulty = query.data.split("_")[1]
        
        if difficulty not in HL_DIFFICULTY:
            return
        
        settings = HL_DIFFICULTY[difficulty]
        
        if users[user_id].get("coins", 0) < settings["bet"]:
            await query.edit_message_text(
                f"❌ You don't have enough coins!\n"
                f"Need: {settings['bet']:,} coins\n"
                f"You have: {users[user_id].get('coins', 0):,} coins",
                parse_mode="HTML"
            )
            return
        
        card_values = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
        card = random.choice(card_values)
        
        active_games[user_id] = {
            "game": "hl",
            "difficulty": difficulty,
            "bet": settings["bet"],
            "current_multiplier": 1,
            "current_card": card,
            "max_multiplier": settings["max_multiplier"],
            "settings": settings,
            "timestamp": time.time()
        }
        
        keyboard = [
            [
                InlineKeyboardButton("📈 HIGHER", callback_data="hl_higher"),
                InlineKeyboardButton("📉 LOWER", callback_data="hl_lower")
            ],
            [InlineKeyboardButton("💰 CASH OUT", callback_data="hl_cashout")],
            [InlineKeyboardButton("❌ Cancel", callback_data="hl_cancel")]
        ]
        
        await query.edit_message_text(
            f"{settings['color']} <b>HIGHER OR LOWER - {settings['name']}</b> {settings['color']}\n\n"
            f"💰 Current bet: {settings['bet']:,} coins\n"
            f"📊 Current multiplier: 1x\n"
            f"🏆 Max multiplier: {settings['max_multiplier']}x\n"
            f"🎯 Potential win: {settings['bet'] * settings['max_multiplier']:,} coins\n\n"
            f"🃏 Current card: <b>{card}</b>\n\n"
            f"Will the next card be HIGHER or LOWER?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def mines(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    
    keyboard = [
        [
            InlineKeyboardButton(f"{MINES_DIFFICULTY['easy']['color']} Easy (5,000)", callback_data="mines_easy"),
            InlineKeyboardButton(f"{MINES_DIFFICULTY['medium']['color']} Medium (25,000)", callback_data="mines_medium"),
            InlineKeyboardButton(f"{MINES_DIFFICULTY['hard']['color']} Hard (100,000)", callback_data="mines_hard")
        ]
    ]
    
    await update.message.reply_text(
        "💣 <b>MINESWEEPER</b> 💣\n\n"
        "Choose your difficulty:\n\n"
        "🟢 Easy: Bet 5,000 - 4x4 grid (3 bombs) - Max win 50,000 (10x)\n"
        "🟡 Medium: Bet 25,000 - 5x5 grid (5 bombs) - Max win 500,000 (20x)\n"
        "🔴 Hard: Bet 100,000 - 6x6 grid (8 bombs) - Max win 4,000,000 (40x)",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def create_mines_grid(size, bomb_count):
    total_cells = size * size
    bomb_positions = set()
    while len(bomb_positions) < bomb_count:
        bomb_positions.add(random.randint(0, total_cells - 1))
    
    grid = []
    for i in range(total_cells):
        grid.append({"is_bomb": i in bomb_positions, "revealed": False})
    
    return grid, bomb_positions

async def mines_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    ensure_user(user_id)
    
    if query.data.startswith("mines_cell_"):
        game = active_games.get(user_id)
        if not game or game.get("game") != "mines":
            await query.edit_message_text("❌ Game expired! Use /mines to start new game.")
            return
        
        cell_index = int(query.data.split("_")[2])
        
        if game["grid"][cell_index]["revealed"]:
            await query.answer("Already revealed!", show_alert=True)
            return
        
        if game["grid"][cell_index]["is_bomb"]:
            async with await get_user_lock(user_id):
                current_coins = users[user_id].get("coins", 0)
                deduct = min(game["bet"], current_coins)
                users[user_id]["coins"] -= deduct
                stats = users[user_id]["game_stats"]["mines"]
                stats["played"] += 1
                stats["lost"] += 1
                stats["profit"] -= deduct
                save_data()
            
            grid_display = []
            size = game["grid_size"]
            for i in range(size * size):
                if game["grid"][i]["is_bomb"]:
                    grid_display.append("💣")
                else:
                    grid_display.append("⬜")
            
            grid_text = ""
            for row in range(size):
                start = row * size
                grid_text += " ".join(grid_display[start:start + size]) + "\n"
            
            keyboard = [[InlineKeyboardButton("🔄 Play Again", callback_data=f"mines_{game['difficulty']}")],
                       [InlineKeyboardButton("❌ Exit", callback_data="mines_exit")]]
            
            await query.edit_message_text(
                f"💥 <b>BOOM! YOU HIT A MINE!</b> 💥\n\n"
                f"{grid_text}\n"
                f"💰 You lost {game['bet']:,} coins!\n\n"
                f"📊 Safe tiles found: {game['safe_count']}/{game['total_safe']}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            del active_games[user_id]
        
        else:
            game["grid"][cell_index]["revealed"] = True
            game["safe_count"] += 1
            
            progress = game["safe_count"] / game["total_safe"]
            game["current_multiplier"] = 1 + (game["max_multiplier"] - 1) * progress
            game["current_multiplier"] = round(game["current_multiplier"], 1)
            
            current_win = int(game["bet"] * game["current_multiplier"])
            
            if game["safe_count"] >= game["total_safe"]:
                async with await get_user_lock(user_id):
                    users[user_id]["coins"] += current_win
                    stats = users[user_id]["game_stats"]["mines"]
                    stats["played"] += 1
                    stats["won"] += 1
                    stats["profit"] += current_win
                    if game["safe_count"] > stats.get("best_tiles", 0):
                        stats["best_tiles"] = game["safe_count"]
                    if game["current_multiplier"] > stats.get("best_multiplier", 0):
                        stats["best_multiplier"] = game["current_multiplier"]
                    save_data()
                
                keyboard = [[InlineKeyboardButton("🔄 Play Again", callback_data=f"mines_{game['difficulty']}")],
                           [InlineKeyboardButton("❌ Exit", callback_data="mines_exit")]]
                
                await query.edit_message_text(
                    f"🎉 <b>YOU CLEARED ALL SAFE TILES!</b> 🎉\n\n"
                    f"💰 You won: {current_win:,} coins!\n"
                    f"📊 Multiplier: {game['current_multiplier']}x\n"
                    f"🎯 Safe tiles: {game['safe_count']}/{game['total_safe']}",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                del active_games[user_id]
            else:
                await update_mines_display(query, user_id)

    elif query.data == "mines_cashout":
        game = active_games.get(user_id)
        if not game or game.get("game") != "mines":
            await query.edit_message_text("❌ No active game!")
            return
        
        if game["safe_count"] == 0:
            await query.answer("❌ Reveal at least one tile before cashing out!", show_alert=True)
            return
        
        win_amount = int(game["bet"] * game["current_multiplier"])
        
        async with await get_user_lock(user_id):
            users[user_id]["coins"] += win_amount
            stats = users[user_id]["game_stats"]["mines"]
            stats["played"] += 1
            stats["won"] += 1
            stats["profit"] += win_amount
            if game["safe_count"] > stats.get("best_tiles", 0):
                stats["best_tiles"] = game["safe_count"]
            if game["current_multiplier"] > stats.get("best_multiplier", 0):
                stats["best_multiplier"] = game["current_multiplier"]
            save_data()
        
        keyboard = [[InlineKeyboardButton("🔄 Play Again", callback_data=f"mines_{game['difficulty']}")],
                   [InlineKeyboardButton("❌ Exit", callback_data="mines_exit")]]
        
        await query.edit_message_text(
            f"💰 <b>YOU CASHED OUT!</b> 💰\n\n"
            f"🎯 Safe tiles found: {game['safe_count']}/{game['total_safe']}\n"
            f"📊 Multiplier: {game['current_multiplier']}x\n"
            f"🏆 You won: {win_amount:,} coins!",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        del active_games[user_id]

    elif query.data == "mines_cancel":
        game = active_games.get(user_id)
        if game and game.get("game") == "mines":
            del active_games[user_id]
        await query.edit_message_text("❌ Game cancelled. No coins were taken!")

    elif query.data == "mines_exit":
        await query.edit_message_text("👋 Thanks for playing Minesweeper! Use /mines to play again.")

    elif query.data.startswith("mines_"):
        difficulty = query.data.split("_")[1]
        
        if difficulty not in MINES_DIFFICULTY:
            return
        
        settings = MINES_DIFFICULTY[difficulty]
        
        if users[user_id].get("coins", 0) < settings["bet"]:
            await query.edit_message_text(
                f"❌ You don't have enough coins!\n"
                f"Need: {settings['bet']:,} coins\n"
                f"You have: {users[user_id].get('coins', 0):,} coins",
                parse_mode="HTML"
            )
            return
        
        grid, bomb_positions = create_mines_grid(settings["grid_size"], settings["bombs"])
        
        active_games[user_id] = {
            "game": "mines",
            "difficulty": difficulty,
            "bet": settings["bet"],
            "current_multiplier": 1,
            "grid": grid,
            "bomb_positions": bomb_positions,
            "grid_size": settings["grid_size"],
            "max_multiplier": settings["max_multiplier"],
            "safe_count": 0,
            "total_safe": (settings["grid_size"] * settings["grid_size"]) - settings["bombs"],
            "settings": settings,
            "timestamp": time.time()
        }
        
        await update_mines_display(query, user_id)

async def update_mines_display(query, user_id):
    game = active_games.get(user_id)
    if not game:
        return
    
    size = game["grid_size"]
    current_win = int(game["bet"] * game["current_multiplier"])
    
    keyboard = []
    for row in range(size):
        row_buttons = []
        for col in range(size):
            idx = row * size + col
            if game["grid"][idx]["revealed"]:
                row_buttons.append(InlineKeyboardButton("✅", callback_data=f"mines_cell_{idx}"))
            else:
                row_buttons.append(InlineKeyboardButton("⬜", callback_data=f"mines_cell_{idx}"))
        keyboard.append(row_buttons)
    
    keyboard.append([InlineKeyboardButton("💰 CASH OUT", callback_data="mines_cashout")])
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="mines_cancel")])
    
    await query.edit_message_text(
        f"{game['settings']['color']} <b>MINESWEEPER - {game['settings']['name']}</b> {game['settings']['color']}\n\n"
        f"💰 Bet: {game['bet']:,} coins\n"
        f"📊 Current multiplier: {game['current_multiplier']}x\n"
        f"🏆 Potential win: {current_win:,} coins\n"
        f"✅ Safe tiles: {game['safe_count']}/{game['total_safe']}\n\n"
        f"Click on tiles to reveal:\n",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ═══════════════════════════════════════════════════════════════
# DEBUG SPAWN COMMANDS
# ═══════════════════════════════════════════════════════════════

async def debug_spawn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(OWNER_ID):
        await update.message.reply_text("❌ Owner only.")
        return
    
    chat_id = update.effective_chat.id
    msg_count = group_message_counts.get(chat_id, 0)
    
    text = f"🔍 <b>Spawn Debug Info</b>\n\n"
    text += f"📊 Messages in this group: {msg_count}/{SPAWN_EVERY}\n"
    text += f"🎮 Active bid in this group: {'Yes' if chat_id in active_bid else 'No'}\n"
    text += f"📝 Total characters available: {len(characters)}\n"
    text += f"👥 Chat ID: {chat_id}\n"
    text += f"🤖 Group ID in env: {GROUP_ID}\n\n"
    text += f"💡 Next spawn in: {SPAWN_EVERY - msg_count} messages"
    
    await update.message.reply_text(text, parse_mode="HTML")

async def force_spawn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(OWNER_ID):
        await update.message.reply_text("❌ Owner only.")
        return
    
    chat_id = update.effective_chat.id
    if chat_id in active_bid:
        await update.message.reply_text("⚠️ There's already an active bid in this group!")
        return
    
    if not characters:
        await update.message.reply_text("❌ No characters available to spawn!")
        return
    
    await spawn_character(chat_id, context)
    await update.message.reply_text("✅ Force spawned a character!")

async def reset_spawn_counter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(OWNER_ID):
        await update.message.reply_text("❌ Owner only.")
        return
    
    chat_id = update.effective_chat.id
    group_message_counts[chat_id] = 0
    group_spawn_counters[chat_id] = 0
    
    await update.message.reply_text(f"✅ Spawn counter reset for this group!")

# ═══════════════════════════════════════════════════════════════
# MESSAGE HANDLER
# ═══════════════════════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()

    # ── Gallery search handler ──
    if context.user_data.get("awaiting_gal_search"):
        context.user_data["awaiting_gal_search"] = False
        context.user_data["gal_search"] = text.lower()
        await send_gallery_page(chat_id, context, user_id, 0)
        return

    if update.effective_chat.type in ("group", "supergroup"):
        # ── Feature 7: Track groups ──
        if chat_id not in known_groups:
            known_groups[chat_id] = {
                "title": update.effective_chat.title or "Unknown",
                "username": update.effective_chat.username or "",
                "first_seen": datetime.now().strftime("%Y-%m-%d %H:%M")
            }
            save_data()
        else:
            # keep title fresh
            known_groups[chat_id]["title"] = update.effective_chat.title or known_groups[chat_id].get("title", "Unknown")

        now = time.time()
        key = (chat_id, user_id)
        
        last_t = last_message_time.get(key, 0)
        last_txt = last_message_text.get(key, "")
        
        if now - last_t >= USER_MSG_COOLDOWN and text.lower() != last_txt:
            last_message_time[key] = now
            last_message_text[key] = text.lower()
            
            async with group_msg_lock:
                group_message_counts[chat_id] = group_message_counts.get(chat_id, 0) + 1
                current_count = group_message_counts[chat_id]
            
            logging.info(f"Group {chat_id} message count: {current_count}/{SPAWN_EVERY}")
            
            if current_count >= SPAWN_EVERY:
                async with group_msg_lock:
                    group_message_counts[chat_id] = 0
                if characters and chat_id not in active_bid:
                    logging.info(f"🎯 Spawning character in group {chat_id}!")
                    asyncio.create_task(spawn_character(chat_id, context))

    # ── Wordle guess handler ──
    if user_id in active_wordle_games:
        await handle_wordle_guess(update, context, user_id, text)
        return

    if chat_id in active_guess:
        session = active_guess.get(chat_id)
        if session and text.isdigit():
            num = int(text)
            if 1 <= num <= 100:
                answer = session["answer"]
                prize = session["prize"]
                ensure_user(user_id, update)
                name = html.escape(update.effective_user.first_name)
                
                if num == answer:
                    async with await get_user_lock(user_id):
                        users[user_id]["coins"] += prize
                        stats = users[user_id]["game_stats"]["guess"]
                        stats["played"] += 1
                        stats["won"] += 1
                        stats["profit"] += prize
                        stats["current_streak"] += 1
                        if stats["current_streak"] > stats["best_streak"]:
                            stats["best_streak"] = stats["current_streak"]
                        if prize > stats.get("biggest_win", 0):
                            stats["biggest_win"] = prize
                        active_guess.pop(chat_id, None)
                        save_data()
                    await update.message.reply_text(f"🎉 <b>{name}</b> got it! The number was <b>{answer}</b>!\n💰 +{prize:,} coins!", parse_mode="HTML")
                    return
                else:
                    async with await get_user_lock(user_id):
                        stats = users[user_id]["game_stats"]["guess"]
                        stats["played"] += 1
                        stats["lost"] += 1
                        stats["current_streak"] = 0
                        save_data()
                    
                    if num < answer:
                        await update.message.reply_text(f"📈 {num} is too low!", parse_mode="HTML")
                    else:
                        await update.message.reply_text(f"📉 {num} is too high!", parse_mode="HTML")
                    return



# ═══════════════════════════════════════════════════════════════
# WORDLE GAME
# ═══════════════════════════════════════════════════════════════

def load_wordle_words():
    try:
        with open("word.txt", "r") as f:
            words = [w.strip().lower() for w in f.readlines() if len(w.strip()) == 5 and w.strip().isalpha()]
        logging.info(f"Loaded {len(words)} Wordle words")
        return words
    except Exception as e:
        logging.error(f"Failed to load word.txt: {e}")
        return ["crane", "stone", "heart", "light", "brave", "dance", "flame", "ghost", "knife", "smile"]

WORDLE_WORDS = load_wordle_words()

WORDLE_REWARDS = {1: 50000, 2: 30000, 3: 20000, 4: 15000, 5: 12000, 6: 10000}

active_wordle_games = {}

def wordle_check_guess(secret, guess):
    result = [None] * 5
    secret_remaining = list(secret)
    for i in range(5):
        if guess[i] == secret[i]:
            result[i] = (guess[i], "correct")
            secret_remaining[i] = None
    for i in range(5):
        if result[i] is not None:
            continue
        if guess[i] in secret_remaining:
            result[i] = (guess[i], "present")
            secret_remaining[secret_remaining.index(guess[i])] = None
        else:
            result[i] = (guess[i], "absent")
    return result

def wordle_render_board(guesses_results, max_guesses=6):
    STATUS = {"correct": "🟩", "present": "🟨", "absent": "⬛"}
    lines = []
    for result in guesses_results:
        squares = "".join(STATUS[s] for _, s in result)
        word = "".join(l.upper() for l, _ in result)
        lines.append(f"{squares}  {word}")
    for _ in range(max_guesses - len(guesses_results)):
        lines.append("⬜⬜⬜⬜⬜")
    return "\n".join(lines)

async def wordle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)

    if user_id in active_wordle_games:
        game = active_wordle_games[user_id]
        board = wordle_render_board(game["guesses_results"])
        used = ", ".join(sorted(game["used_letters"])).upper() if game["used_letters"] else "None"
        attempts_left = 6 - len(game["guesses_results"])
        await update.message.reply_text(
            f"🟩 <b>WORDLE</b>\n\n{board}\n\n"
            f"🚫 Used letters: <b>{used}</b>\n"
            f"🎯 Attempts left: <b>{attempts_left}</b>\n\n"
            f"You already have an active game! Keep guessing or use /cwordle to quit.",
            parse_mode="HTML"
        )
        return

    word = random.choice(WORDLE_WORDS)
    active_wordle_games[user_id] = {
        "word": word,
        "guesses_results": [],
        "used_letters": set(),
        "timestamp": time.time()
    }

    board = wordle_render_board([])
    await update.message.reply_text(
        f"🟩 <b>WORDLE</b>\n\n{board}\n\n"
        f"🎯 Attempts left: <b>6</b>\n"
        f"💰 Win up to <b>50,000</b> coins!\n\n"
        f"Type your 5-letter guess!",
        parse_mode="HTML"
    )

async def cwordle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in active_wordle_games:
        await update.message.reply_text("❌ You don't have an active Wordle game.")
        return
    game = active_wordle_games.pop(user_id)
    await update.message.reply_text(
        f"🟩 Wordle ended.\nThe word was: <b>{game['word'].upper()}</b>\n"
        f"Better luck next time! Use /wordle to play again.",
        parse_mode="HTML"
    )

async def wstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    ws = users[user_id]["game_stats"].get("wordle", {})
    played = ws.get("played", 0)
    won = ws.get("won", 0)
    lost = ws.get("lost", 0)
    coins = ws.get("coins_earned", 0)
    streak = ws.get("current_streak", 0)
    best_streak = ws.get("best_streak", 0)
    best_guess = ws.get("best_guesses", 0)
    win_rate = round((won / played) * 100) if played > 0 else 0
    name = html.escape(update.effective_user.first_name)
    best_guess_str = "—" if best_guess == 0 else f"{best_guess}/6"

    await update.message.reply_text(
        f"🟩 <b>WORDLE STATS — {name}</b>\n\n"
        f"🎮 Games Played: <b>{played}</b>\n"
        f"✅ Wins: <b>{won}</b>\n"
        f"❌ Losses: <b>{lost}</b>\n"
        f"📊 Win Rate: <b>{win_rate}%</b>\n"
        f"💰 Coins Earned: <b>{coins:,}</b>\n"
        f"🔥 Current Streak: <b>{streak}</b>\n"
        f"🏆 Best Streak: <b>{best_streak}</b>\n"
        f"⚡ Best Solve: <b>{best_guess_str}</b>",
        parse_mode="HTML"
    )

async def wrank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[
        InlineKeyboardButton("📅 Daily", callback_data="wrank_daily"),
        InlineKeyboardButton("📆 Weekly", callback_data="wrank_weekly"),
        InlineKeyboardButton("🗓️ Monthly", callback_data="wrank_monthly"),
    ]]
    await update.message.reply_text(
        "🟩 <b>WORDLE LEADERBOARD</b>\n\nChoose a time period:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def wrank_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    period = query.data.split("_")[1]

    now = time.time()
    if period == "daily":
        cutoff = now - 86400
        label = "📅 Daily"
    elif period == "weekly":
        cutoff = now - 604800
        label = "📆 Weekly"
    else:
        cutoff = now - 2592000
        label = "🗓️ Monthly"

    scores = {}
    for uid, udata in users.items():
        history = udata.get("wordle_history", [])
        wins = sum(1 for h in history if h.get("won") and h.get("time", 0) >= cutoff)
        if wins > 0:
            scores[uid] = wins

    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:10]

    keyboard = [[
        InlineKeyboardButton("📅 Daily", callback_data="wrank_daily"),
        InlineKeyboardButton("📆 Weekly", callback_data="wrank_weekly"),
        InlineKeyboardButton("🗓️ Monthly", callback_data="wrank_monthly"),
    ]]

    if not sorted_scores:
        await query.edit_message_text(
            f"🟩 <b>WORDLE LEADERBOARD — {label}</b>\n\nNo winners yet in this period!",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    medals = ["🥇", "🥈", "🥉"]
    text = f"🟩 <b>WORDLE LEADERBOARD — {label}</b>\n\n"
    for i, (uid, wins) in enumerate(sorted_scores):
        medal = medals[i] if i < 3 else f"{i + 1}."
        uname = html.escape(users.get(uid, {}).get("name", "Unknown"))
        text += f"{medal} <b>{uname}</b> — {wins} win{'s' if wins != 1 else ''}\n"

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_wordle_guess(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str, text: str):
    game = active_wordle_games.get(user_id)
    if not game:
        return

    guess = text.lower().strip()
    secret = game["word"]
    name = html.escape(update.effective_user.first_name)

    if len(guess) != 5 or not guess.isalpha():
        return

    # Block grey letters
    grey_used = list(set(l for l in guess if l in game["used_letters"]))
    if grey_used:
        used_str = ", ".join(sorted(grey_used)).upper()
        plural = "they are" if len(grey_used) > 1 else "it's"
        await update.message.reply_text(
            f"❌ You already used the letter(s) <b>{used_str}</b> — {plural} not in the word!\n"
            f"Try a different word. (Attempt not counted)",
            parse_mode="HTML"
        )
        return

    result = wordle_check_guess(secret, guess)
    game["guesses_results"].append(result)

    # Only block letters that are purely absent (never green or yellow in this guess)
    non_blocked = {l for l, s in result if s in ("correct", "present")}
    for letter, status in result:
        if status == "absent" and letter not in non_blocked:
            game["used_letters"].add(letter)

    board = wordle_render_board(game["guesses_results"])
    attempts_used = len(game["guesses_results"])
    attempts_left = 6 - attempts_used
    used_display = ", ".join(sorted(game["used_letters"])).upper() if game["used_letters"] else "None"

    # WIN
    if guess == secret:
        active_wordle_games.pop(user_id, None)
        reward = WORDLE_REWARDS.get(attempts_used, 10000)

        async with await get_user_lock(user_id):
            users[user_id]["coins"] = users[user_id].get("coins", 0) + reward
            ws = users[user_id]["game_stats"]["wordle"]
            ws["played"] += 1
            ws["won"] += 1
            ws["coins_earned"] = ws.get("coins_earned", 0) + reward
            ws["current_streak"] = ws.get("current_streak", 0) + 1
            if ws["current_streak"] > ws.get("best_streak", 0):
                ws["best_streak"] = ws["current_streak"]
            if ws.get("best_guesses", 0) == 0 or attempts_used < ws["best_guesses"]:
                ws["best_guesses"] = attempts_used
            users[user_id].setdefault("wordle_history", [])
            users[user_id]["wordle_history"].append({"won": True, "guesses": attempts_used, "time": time.time()})
            if len(users[user_id]["wordle_history"]) > 500:
                users[user_id]["wordle_history"] = users[user_id]["wordle_history"][-500:]
            save_data()

        keyboard = [[InlineKeyboardButton("▶️ Play Again", callback_data="wordle_play_again")]]
        current_streak = users[user_id]["game_stats"]["wordle"]["current_streak"]
        await update.message.reply_text(
            f"🎉 <b>{name}</b> solved the Wordle!\n\n{board}\n\n"
            f"🟩 Word: <b>{secret.upper()}</b>\n"
            f"🎯 Solved in: <b>{attempts_used}/6</b>\n"
            f"💰 Reward: <b>+{reward:,} coins!</b>\n"
            f"🔥 Streak: <b>{current_streak}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # LOSS
    if attempts_left == 0:
        active_wordle_games.pop(user_id, None)

        async with await get_user_lock(user_id):
            ws = users[user_id]["game_stats"]["wordle"]
            ws["played"] += 1
            ws["lost"] += 1
            ws["current_streak"] = 0
            users[user_id].setdefault("wordle_history", [])
            users[user_id]["wordle_history"].append({"won": False, "guesses": 6, "time": time.time()})
            if len(users[user_id]["wordle_history"]) > 500:
                users[user_id]["wordle_history"] = users[user_id]["wordle_history"][-500:]
            save_data()

        keyboard = [[InlineKeyboardButton("▶️ Play Again", callback_data="wordle_play_again")]]
        await update.message.reply_text(
            f"💔 <b>{name}</b> ran out of attempts!\n\n{board}\n\n"
            f"🟩 The word was: <b>{secret.upper()}</b>\n"
            f"Better luck next time!",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # CONTINUE
    await update.message.reply_text(
        f"🟩 <b>WORDLE</b>\n\n{board}\n\n"
        f"🚫 Used letters: <b>{used_display}</b>\n"
        f"🎯 Attempts left: <b>{attempts_left}</b>",
        parse_mode="HTML"
    )

async def wordle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    ensure_user(user_id)

    if query.data == "wordle_play_again":
        if user_id in active_wordle_games:
            await query.answer("You already have an active game!", show_alert=True)
            return
        word = random.choice(WORDLE_WORDS)
        active_wordle_games[user_id] = {
            "word": word,
            "guesses_results": [],
            "used_letters": set(),
            "timestamp": time.time()
        }
        board = wordle_render_board([])
        await query.edit_message_text(
            f"🟩 <b>WORDLE</b>\n\n{board}\n\n"
            f"🎯 Attempts left: <b>6</b>\n"
            f"💰 Win up to <b>50,000</b> coins!\n\n"
            f"Type your 5-letter guess!",
            parse_mode="HTML"
        )

async def cleanup_stale_wordle_games():
    while True:
        await asyncio.sleep(3600)
        now = time.time()
        stale = [uid for uid, g in list(active_wordle_games.items()) if now - g.get("timestamp", 0) > 86400]
        for uid in stale:
            active_wordle_games.pop(uid, None)
            logging.info(f"Cleaned up stale Wordle game for {uid}")


# ═══════════════════════════════════════════════════════════════
# FEATURE 2: /transfer — move all data from one user to another
# ═══════════════════════════════════════════════════════════════

async def transfer_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(OWNER_ID):
        await update.message.reply_text("❌ Owner only.")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /transfer [old_user_id] [new_user_id]\n"
            "This moves ALL data (coins, cards, streak, titles, stats) to the new ID."
        )
        return

    old_id = context.args[0].strip()
    new_id = context.args[1].strip()

    if old_id not in users:
        await update.message.reply_text(f"❌ User ID {old_id} not found in database.")
        return

    ensure_user(new_id)

    async with await get_user_lock(old_id), await get_user_lock(new_id):
        old_data = users[old_id]
        # Merge: add coins, append cards, keep highest streak, etc.
        users[new_id]["coins"] = users[new_id].get("coins", 0) + old_data.get("coins", 0)
        users[new_id]["characters"] = users[new_id].get("characters", []) + old_data.get("characters", [])
        users[new_id]["title"] = old_data.get("title") or users[new_id].get("title")
        users[new_id]["owned_titles"] = list(set(
            users[new_id].get("owned_titles", []) + old_data.get("owned_titles", [])
        ))
        users[new_id]["completed_sets"] = list(set(
            users[new_id].get("completed_sets", []) + old_data.get("completed_sets", [])
        ))
        # Transfer streaks if old was higher
        if checkin_streak.get(old_id, 0) > checkin_streak.get(new_id, 0):
            checkin_streak[new_id] = checkin_streak.get(old_id, 0)
            last_checkin[new_id] = last_checkin.get(old_id, {})
        # Merge game stats (pick max values)
        old_stats = old_data.get("game_stats", {})
        new_stats = users[new_id].get("game_stats", {})
        for game_key in old_stats:
            if game_key not in new_stats:
                new_stats[game_key] = old_stats[game_key]
            else:
                for stat_key, stat_val in old_stats[game_key].items():
                    if isinstance(stat_val, (int, float)):
                        new_stats[game_key][stat_key] = new_stats[game_key].get(stat_key, 0) + stat_val
        users[new_id]["game_stats"] = new_stats
        # Remove old user
        del users[old_id]
        checkin_streak.pop(old_id, None)
        last_checkin.pop(old_id, None)
        last_daily.pop(old_id, None)
        last_weekly.pop(old_id, None)
        save_data()

    await update.message.reply_text(
        f"✅ <b>Transfer Complete!</b>\n\n"
        f"All data from <code>{old_id}</code> has been moved to <code>{new_id}</code>.",
        parse_mode="HTML"
    )


# ═══════════════════════════════════════════════════════════════
# FEATURE 5: /editcard and /deletecard
# ═══════════════════════════════════════════════════════════════

async def editcard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in uploaders and user_id != str(OWNER_ID):
        await update.message.reply_text("❌ You are not an uploader.")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /editcard [card_id] Name | Anime | Rarity(1-5)\n"
            "Example: /editcard 0012 Naruto | Naruto | 3"
        )
        return

    card_id = context.args[0].strip().lstrip("#").zfill(4)
    rest = " ".join(context.args[1:])

    try:
        parts = rest.split("|")
        if len(parts) < 3:
            raise ValueError
        name = parts[0].strip()
        rarity = int(parts[-1].strip())
        anime = "|".join(parts[1:-1]).strip()
        if not name or not anime or rarity not in (1, 2, 3, 4, 5):
            raise ValueError
    except Exception:
        await update.message.reply_text("Format: /editcard [card_id] Name | Anime | 1-5")
        return

    char = next((c for c in characters if str(c.get("card_id", "")).zfill(4) == card_id), None)
    if not char:
        await update.message.reply_text(f"❌ Card #{card_id} not found.")
        return

    old_name = char.get("name", "?")
    char["name"] = name
    char["anime"] = anime
    char["rarity"] = rarity

    # Also update in all users' collections
    for uid, udata in users.items():
        for c in udata.get("characters", []):
            if str(c.get("card_id", "")).zfill(4) == card_id:
                c["name"] = name
                c["anime"] = anime
                c["rarity"] = rarity

    save_data()
    init_anime_sets()

    await update.message.reply_text(
        f"✅ <b>Card #{card_id} Updated!</b>\n\n"
        f"🔄 Old name: {html.escape(old_name)}\n"
        f"🎴 New name: {html.escape(name)}\n"
        f"🎬 Anime: {html.escape(anime)}\n"
        f"⭐ Rarity: {rarity_label(rarity)}",
        parse_mode="HTML"
    )


async def deletecard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(OWNER_ID):
        await update.message.reply_text("❌ Owner only.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /deletecard [card_id]")
        return

    card_id = context.args[0].strip().lstrip("#").zfill(4)
    char = next((c for c in characters if str(c.get("card_id", "")).zfill(4) == card_id), None)
    if not char:
        await update.message.reply_text(f"❌ Card #{card_id} not found.")
        return

    char_name = char.get("name", "?")
    characters.remove(char)

    # Also remove from all users' collections
    removed_count = 0
    for uid, udata in users.items():
        before = len(udata.get("characters", []))
        udata["characters"] = [
            c for c in udata.get("characters", [])
            if str(c.get("card_id", "")).zfill(4) != card_id
        ]
        removed_count += before - len(udata["characters"])

    # Remove from daily shop if present
    global daily_shop_chars
    daily_shop_chars = [c for c in daily_shop_chars if str(c.get("card_id", "")).zfill(4) != card_id]

    save_data()
    init_anime_sets()

    await update.message.reply_text(
        f"🗑️ <b>Card #{card_id} Deleted!</b>\n\n"
        f"🎴 Name: {html.escape(char_name)}\n"
        f"👥 Removed from {removed_count} user collection(s).",
        parse_mode="HTML"
    )


# ═══════════════════════════════════════════════════════════════
# FEATURE 6: /list — owner sees all users
# ═══════════════════════════════════════════════════════════════

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(OWNER_ID):
        await update.message.reply_text("❌ Owner only.")
        return

    if not users:
        await update.message.reply_text("No users yet.")
        return

    lines = [f"👥 Total users: {len(users)}\n"]
    for i, (uid, udata) in enumerate(users.items(), 1):
        name = udata.get("name", "Unknown")
        username = udata.get("username", "")
        uname_str = f"@{username}" if username else "no username"
        coins = udata.get("coins", 0)
        cards = len(udata.get("characters", []))
        lines.append(f"{i}. {html.escape(name)} ({uname_str}) | ID: {uid} | 💰{coins:,} | 🎴{cards}")

    full_text = "\n".join(lines)

    if len(full_text) > 4000:
        # Send as a document
        import io
        doc = io.BytesIO(full_text.encode("utf-8"))
        doc.name = "user_list.txt"
        await update.message.reply_document(document=doc, filename="user_list.txt", caption=f"👥 {len(users)} users")
    else:
        await update.message.reply_text(f"<pre>{full_text}</pre>", parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════
# FEATURE 7: /groups — list all groups the bot has been in
# ═══════════════════════════════════════════════════════════════

async def groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(OWNER_ID):
        await update.message.reply_text("❌ Owner only.")
        return

    if not known_groups:
        await update.message.reply_text("📭 No groups recorded yet.")
        return

    import io
    lines = [f"📋 Groups the bot has been in ({len(known_groups)} total)\n"]
    for chat_id, info in known_groups.items():
        title = info.get("title", "Unknown")
        username = info.get("username", "")
        uname_str = f"@{username}" if username else "no username"
        first_seen = info.get("first_seen", "?")
        lines.append(f"• {title} ({uname_str}) | ID: {chat_id} | First seen: {first_seen}")

    full_text = "\n".join(lines)
    doc = io.BytesIO(full_text.encode("utf-8"))
    doc.name = "groups_list.txt"
    await update.message.reply_document(document=doc, filename="groups_list.txt",
                                        caption=f"📋 {len(known_groups)} groups")


# ═══════════════════════════════════════════════════════════════
# FEATURE 8: /wspy — peek at someone's Wordle game
# ═══════════════════════════════════════════════════════════════

async def wspy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(OWNER_ID):
        await update.message.reply_text("❌ Owner only.")
        return

    target_id = None

    # Try reply first
    if update.message.reply_to_message:
        target_id = str(update.message.reply_to_message.from_user.id)
    elif context.args:
        arg = context.args[0].lstrip("@")
        # Search by username or numeric ID
        for uid, udata in users.items():
            if uid == arg or udata.get("username", "").lower() == arg.lower():
                target_id = uid
                break
        if not target_id and arg.isdigit():
            target_id = arg

    if not target_id:
        await update.message.reply_text(
            "Usage: /wspy @username  or  /wspy [user_id]  or reply to a message"
        )
        return

    game = active_wordle_games.get(target_id)
    if not game:
        udata = users.get(target_id, {})
        name = udata.get("name", target_id)
        await update.message.reply_text(f"🔍 <b>{html.escape(name)}</b> has no active Wordle game.", parse_mode="HTML")
        return

    board = wordle_render_board(game["guesses_results"])
    attempts_left = 6 - len(game["guesses_results"])
    used = ", ".join(sorted(game["used_letters"])).upper() if game["used_letters"] else "None"
    secret = game["word"].upper()
    udata = users.get(target_id, {})
    name = udata.get("name", target_id)

    await update.message.reply_text(
        f"🔍 <b>Wordle Spy — {html.escape(name)}</b>\n\n"
        f"{board}\n\n"
        f"🟩 Secret word: <b>{secret}</b>\n"
        f"🚫 Used letters: <b>{used}</b>\n"
        f"🎯 Attempts left: <b>{attempts_left}</b>",
        parse_mode="HTML"
    )


# ═══════════════════════════════════════════════════════════════
# INLINE QUERY  —  @bot_username  shows ALL bot characters
# ═══════════════════════════════════════════════════════════════

async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.inline_query
    user_id = str(query.from_user.id)
    search  = query.query.strip().lower()

    ensure_user(user_id)

    # Show full bot character database (not just user's collection)
    pool = characters  # all uploaded cards

    if search:
        pool = [
            c for c in pool
            if isinstance(c, dict) and (
                search in c.get("name", "").lower() or
                search in c.get("anime", "").lower()
            )
        ]

    if not pool:
        await query.answer(
            [],
            switch_pm_text="😕 No cards found — try a different name!",
            switch_pm_parameter="start",
            cache_time=5,
        )
        return

    rarity_full = {1:"⚪ Common",2:"🔵 Rare",3:"🟣 Epic",4:"🟡 Legendary",5:"⚡ Celebrity"}
    user_card_ids = [
        str(c.get("card_id","")).zfill(4)
        for c in users.get(user_id, {}).get("characters", [])
        if isinstance(c, dict)
    ]

    results = []
    for char in pool[:50]:   # Telegram hard cap = 50
        if not isinstance(char, dict) or not char.get("file_id"):
            continue
        cid   = str(char.get("card_id","")).zfill(4)
        name  = char.get("name","?")
        anime = char.get("anime","?")
        rl    = rarity_full.get(char.get("rarity",1),"❓")
        price = get_card_price(char.get("rarity",1))
        qty   = get_card_qty(user_id, cid)
        owned_line = f"🗂 You own: {qty}x\n" if qty > 0 else "🗂 You don't own this card\n"

        caption = (
            f"🎴 <b>{html.escape(name)}</b>\n"
            f"📺 <b>Anime:</b> {html.escape(anime)}\n"
            f"{rl}\n"
            f"🪪 <b>ID:</b> #{cid}\n"
            f"💰 <b>Price:</b> {price:,} coins\n"
            f"{owned_line}"
        )

        results.append(
            InlineQueryResultCachedPhoto(
                id=str(uuid.uuid4()),
                photo_file_id=char["file_id"],
                title=name,
                description=f"{anime} | {rl} | {price:,} coins | Owned: {qty}x",
                caption=caption,
                parse_mode="HTML",
            )
        )

    await query.answer(results, cache_time=10, is_personal=True)


# ═══════════════════════════════════════════════════════════════
# FLASK + MAIN
# ═══════════════════════════════════════════════════════════════

flask_app = Flask(__name__)

@flask_app.route('/')
def index():
    with thread_lock:
        user_count = len(users)
        card_count = len(characters)
    return jsonify({"status": "Bot is running!", "users": user_count, "cards": card_count})

@flask_app.route('/health')
def health():
    return jsonify({"status": "ok"})

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False)

async def main():
    load_data()
    init_anime_sets()
    rotate_daily_shop()   # Feature 3: ensure shop is ready on startup
    logging.info(f"✅ Data loaded - {len(users)} users, {len(characters)} cards")
    logging.info(f"✅ Anime sets built - {len(anime_sets_cache)} sets available")
    
    asyncio.create_task(cleanup_old_data())
    asyncio.create_task(cleanup_stale_games())
    asyncio.create_task(cleanup_stale_trades())
    asyncio.create_task(monitor_stuck_bids())
    asyncio.create_task(cleanup_stale_wordle_games())
    asyncio.create_task(daily_shop_rotation_task())   # Feature 3: midnight rotation
    
    application = ApplicationBuilder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("bonus", bonus))
    application.add_handler(CommandHandler("profile", profile))
    application.add_handler(CommandHandler("collection", mycards))
    application.add_handler(CommandHandler("summon", summon))
    application.add_handler(CommandHandler("shop", shop))
    application.add_handler(CommandHandler("favourite", favourite))
    application.add_handler(CommandHandler("favorites", myfavourites))
    application.add_handler(CommandHandler("cardinfo", cardinfo))
    application.add_handler(CommandHandler("trade", trade))
    application.add_handler(CommandHandler("offer", offer))
    application.add_handler(CommandHandler("canceltrade", canceltrade))
    application.add_handler(CommandHandler("upload", upload))
    application.add_handler(CommandHandler("adduploader", adduploader))
    application.add_handler(CommandHandler("give", give))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CommandHandler("ctop", ctop))
    application.add_handler(CommandHandler("checkin", checkin))
    application.add_handler(CommandHandler("checkintop", checkintop))
    application.add_handler(CommandHandler("bid", bid))
    application.add_handler(CommandHandler("guess", guess_game))
    application.add_handler(CommandHandler("gleader", gleader))
    application.add_handler(CommandHandler("gstats", gstats))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("rps", rps))
    application.add_handler(CommandHandler("hl", hl))
    application.add_handler(CommandHandler("mines", mines))
    application.add_handler(CommandHandler("debugspawn", debug_spawn))
    application.add_handler(CommandHandler("forcespawn", force_spawn))
    application.add_handler(CommandHandler("resetspawn", reset_spawn_counter))
    application.add_handler(CommandHandler("crash", crash))
    application.add_handler(CommandHandler("sets", sets))
    application.add_handler(CommandHandler("gift", gift))
    application.add_handler(CommandHandler("title", title))
    application.add_handler(CommandHandler("wordle", wordle))
    application.add_handler(CommandHandler("cwordle", cwordle))
    application.add_handler(CommandHandler("wstats", wstats))
    application.add_handler(CommandHandler("wrank", wrank))
    # ── New commands ──
    application.add_handler(CommandHandler("transfer", transfer_data))    # Feature 2
    application.add_handler(CommandHandler("editcard", editcard))         # Feature 5
    application.add_handler(CommandHandler("deletecard", deletecard))     # Feature 5
    application.add_handler(CommandHandler("list", list_users))           # Feature 6
    application.add_handler(CommandHandler("groups", groups_command))     # Feature 7
    application.add_handler(CommandHandler("wspy", wspy))                 # Feature 8
    
    application.add_handler(InlineQueryHandler(inline_query_handler))
    application.add_handler(CallbackQueryHandler(credits_callback, pattern="^show_credits$"))
    application.add_handler(CallbackQueryHandler(collection_buttons, pattern="^(col_|gal_)"))
    application.add_handler(CallbackQueryHandler(bonus_callback, pattern="^bonus_"))
    application.add_handler(CallbackQueryHandler(shop_callback, pattern="^shop_"))
    application.add_handler(CallbackQueryHandler(trade_callback, pattern="^trade_"))
    application.add_handler(CallbackQueryHandler(rps_callback, pattern="^rps_"))
    application.add_handler(CallbackQueryHandler(hl_callback, pattern="^hl_"))
    application.add_handler(CallbackQueryHandler(mines_callback, pattern="^mines_"))
    application.add_handler(CallbackQueryHandler(crash_callback, pattern="^crash_"))
    application.add_handler(CallbackQueryHandler(wordle_callback, pattern="^wordle_"))
    application.add_handler(CallbackQueryHandler(wrank_callback, pattern="^wrank_"))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    logging.info("✅ Bot is running with long polling!")
    
    await asyncio.Event().wait()

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    asyncio.run(main())
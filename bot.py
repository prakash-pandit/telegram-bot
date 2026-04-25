from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
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
anime_sets_cache = {}  # Cache of anime -> list of card_ids

# ═══════════════════════════════════════════════════════════════
# SAVE / LOAD FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def save_data():
    global users, last_daily, last_weekly, characters
    global last_checkin, checkin_streak, uploaders, spawn_counter
    global user_titles, user_gift_cooldown, user_gift_daily, completed_sets
    
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
        "completed_sets": completed_sets
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
                    "guess": {"played": 0, "won": 0, "lost": 0, "profit": 0, "best_streak": 0, "current_streak": 0, "biggest_win": 0}
                })
            
            # Migrate old data
            for uid in users:
                if "title" not in users[uid]:
                    users[uid]["title"] = None
                if "owned_titles" not in users[uid]:
                    users[uid]["owned_titles"] = []
                if "completed_sets" not in users[uid]:
                    users[uid]["completed_sets"] = []
            
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

# ─── HELPER FUNCTIONS ───
async def get_user_lock(user_id):
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]

def init_anime_sets():
    """Build anime sets from database characters"""
    global anime_sets_cache
    anime_sets_cache = {}
    
    for char in characters:
        anime = char.get("anime", "Unknown")
        card_id = char.get("card_id")
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
        
        # Check if user has all cards
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
    """Remove games older than 2 days"""
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
    """Remove expired trades"""
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
    """Force end bids that are stuck"""
    while True:
        await asyncio.sleep(60)
        now = time.time()
        for chat_id, session in list(active_bid.items()):
            if session.get("start_time", 0) and now - session["start_time"] > 600:
                logging.warning(f"Forcing end of stuck bid in chat {chat_id}")
                await resolve_bid(chat_id, None)

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
                "guess": {"played": 0, "won": 0, "lost": 0, "profit": 0, "best_streak": 0, "current_streak": 0, "biggest_win": 0}
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
        "guess": {"played": 0, "won": 0, "lost": 0, "profit": 0, "best_streak": 0, "current_streak": 0, "biggest_win": 0}
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
    except Exception as e:
        logging.error(f"Failed to send collection photo: {e}")
        await context.bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def send_shop_page(chat_id, context, user_id):
    page = context.user_data.get("shop_page", 0)
    all_chars = context.user_data.get("shop_list", characters)
    
    total_pages = min(len(all_chars), SHOP_MAX_PAGES)
    
    if page >= total_pages:
        page = total_pages - 1 if total_pages > 0 else 0
    
    if page < 0 or page >= total_pages or not all_chars:
        await context.bot.send_message(chat_id=chat_id, text="🏪 No characters in shop!")
        return
    
    char = all_chars[page] if page < len(all_chars) else None
    if not char:
        await context.bot.send_message(chat_id=chat_id, text="🏪 No character found!")
        return
    
    rarity = char.get("rarity", 1)
    price = SHOP_PRICES.get(rarity, 50000)
    rl = rarity_label(rarity)
    coins = users[user_id].get("coins", 0)
    mark = "✅" if coins >= price else "❌"
    
    text = f"🏪 <b>CHARACTER SHOP</b>\n💰 Your coins: <b>{coins:,}</b>\n📄 Page {page+1}/{total_pages}\n\n"
    text += f"{mark} <b>{html.escape(char.get('name','?'))}</b>\n"
    text += f"⭐ {rl}\n"
    text += f"🎬 {html.escape(char.get('anime','?'))}\n"
    text += f"💰 Price: {price:,}\n"
    text += f"🪪 #{char.get('card_id','?')}\n"
    
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
    """Start Crash game"""
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
    
    # Random crash point between 1.01x and 10x
    crash_point = random.uniform(1.01, CRASH_MAX_MULTIPLIER)
    # Make higher multipliers rarer
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
    """Run the crash game multiplier"""
    game = active_crash_games.get(user_id)
    if not game:
        return
    
    start_time = time.time()
    multiplier = 1.0
    
    while game["running"] and multiplier < game["crash_point"]:
        await asyncio.sleep(CRASH_UPDATE_INTERVAL)
        
        if not game["running"]:
            break
        
        multiplier += CRASH_INCREMENT
        game["multiplier"] = multiplier
        
        if multiplier >= game["crash_point"]:
            # CRASHED
            game["running"] = False
            game["crashed"] = True
            
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
            
            # Deduct coins
            async with await get_user_lock(user_id):
                users[user_id]["coins"] -= game["bet"]
                save_data()
            
            del active_crash_games[user_id]
            return
        
        # Update message with new multiplier
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
        # Just update to show it's holding
        await query.answer("📈 Holding... Multiplier still increasing!")
    
    elif query.data == "crash_cashout":
        if not game["running"]:
            await query.answer("❌ Game already ended!", show_alert=True)
            return
        
        # Cash out
        game["running"] = False
        win_amount = int(game["bet"] * game["multiplier"])
        
        async with await get_user_lock(user_id):
            users[user_id]["coins"] += win_amount - game["bet"]
            # Update stats
            stats = users[user_id]["game_stats"].setdefault("crash", {"played": 0, "won": 0, "profit": 0, "biggest_win": 0})
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
    """Show progress of anime card sets"""
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
    
    for anime, required_cards in list(anime_sets_cache.items())[:15]:  # Show top 15
        owned_count = sum(1 for card_id in required_cards if card_id in user_card_ids)
        completed = "✅ COMPLETE!" if anime in user_completed else f"📊 {owned_count}/{len(required_cards)}"
        reward = get_set_reward(len(required_cards))
        
        if anime in user_completed:
            status = "✅"
        elif owned_count == len(required_cards):
            status = "🎉"
        else:
            status = "📦"
        
        text += f"{status} <b>{anime}</b>\n"
        text += f"   Cards: {owned_count}/{len(required_cards)} | Reward: {reward:,} coins\n"
        
        if owned_count == len(required_cards) and anime not in user_completed:
            text += f"   🎯 CLAIM NOW! Contact admin or wait for auto-reward\n"
        
        text += "\n"
    
    text += f"\n💡 <b>Tip:</b> Collect all cards from one anime to get big bonuses!\n"
    text += f"🎯 Use /summon to get more cards!"
    
    await update.message.reply_text(text, parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════
# GIFT COMMAND
# ═══════════════════════════════════════════════════════════════

async def gift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send coins or cards to another user"""
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    
    # Check if replying to someone
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
    
    # Check cooldown
    last_gift = user_gift_cooldown.get(user_id, 0)
    if time.time() - last_gift < GIFT_COOLDOWN:
        remaining = int(GIFT_COOLDOWN - (time.time() - last_gift))
        await update.message.reply_text(f"⏳ Please wait {remaining} seconds before gifting again!")
        return
    
    # Check daily limit
    daily_gifted = user_gift_daily.get(user_id, 0)
    today = datetime.now().strftime("%Y-%m-%d")
    if user_gift_daily.get(f"{user_id}_date") != today:
        user_gift_daily[user_id] = 0
        user_gift_daily[f"{user_id}_date"] = today
        daily_gifted = 0
    
    # Parse gift type
    if context.args[0].lower() == "card" and len(context.args) > 1:
        # Gift specific card
        card_id = context.args[1].strip().lstrip("#").zfill(4)
        
        async with await get_user_lock(user_id), await get_user_lock(target_id):
            # Find card in sender's collection
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
            
            # Check if receiver already has this card
            receiver_has = any(
                str(c.get("card_id", "")).zfill(4) == card_id
                for c in users[target_id].get("characters", [])
            )
            
            if receiver_has:
                await update.message.reply_text(f"❌ {html.escape(target_user.first_name)} already owns this card!")
                return
            
            # Remove from sender, add to receiver
            users[user_id]["characters"] = [
                c for c in sender_cards 
                if str(c.get("card_id", "")).zfill(4) != card_id
            ]
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
        
    elif context.args[0].lower() == "random":
        # Gift random card
        async with await get_user_lock(user_id), await get_user_lock(target_id):
            sender_cards = users[user_id].get("characters", [])
            available_cards = [c for c in sender_cards if not c.get("favourite")]
            
            if not available_cards:
                await update.message.reply_text("❌ You have no cards to gift (favourited cards cannot be gifted)!")
                return
            
            card = random.choice(available_cards)
            card_id = str(card.get("card_id", "")).zfill(4)
            
            # Check if receiver already has this card
            receiver_has = any(
                str(c.get("card_id", "")).zfill(4) == card_id
                for c in users[target_id].get("characters", [])
            )
            
            if receiver_has:
                await update.message.reply_text(f"❌ {html.escape(target_user.first_name)} already owns this card! Try another card.")
                return
            
            # Remove from sender, add to receiver
            users[user_id]["characters"] = [
                c for c in sender_cards 
                if str(c.get("card_id", "")).zfill(4) != card_id
            ]
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
        
    else:
        # Gift coins
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
        
        # Notify receiver
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
    """Custom title system"""
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
            # Buy custom title
            custom_text = " ".join(context.args[2:])
            if not custom_text:
                await update.message.reply_text("❌ Please provide a custom title text!")
                return
            
            if len(custom_text) > MAX_TITLE_LENGTH:
                await update.message.reply_text(f"❌ Title too long! Max {MAX_TITLE_LENGTH} characters.")
                return
            
            # Profanity filter (basic)
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
            # Buy preset title
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
        
        # Check if user owns this title
        owned_titles = users[user_id].get("owned_titles", [])
        
        if title_name not in owned_titles:
            # Check if it's a preset title they haven't bought
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
# START COMMAND (Updated with title support)
# ═══════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    if str(user_id) != str(OWNER_ID):
        # Normal user view
        ensure_user(user_id, update)
        user_data = users[user_id]
        coins = user_data.get("coins", 0)
        cards = len(user_data.get("characters", []))
        streak = checkin_streak.get(user_id, 0)
        current_title = user_data.get("title")
        
        title_line = f"\n🏷️ Title: <b>{html.escape(current_title)}</b>" if current_title else ""
        
        welcome_text = f"""
🎮 <b>Welcome to Anime Card Bot!</b>

👤 <b>Your Stats:</b>
├ 💰 Coins: {coins:,}
├ 🎴 Cards: {cards}
├ 📅 Streak: {streak} days{title_line}

━━━━━━━━━━━━━━━━━━━━━
📋 <b>Quick Start:</b>
• /bonus - Free daily coins
• /summon - Get random card
• /checkin - Daily streak
• /rps - Play games
• /crash - Play Crash game
• /sets - View anime sets

━━━━━━━━━━━━━━━━━━━━━
❓ Use /help for all commands
        """
        
        await update.message.reply_text(welcome_text, parse_mode="HTML")
        return
    
    # Owner diagnostics view
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
# HELP COMMAND (Updated)
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
# PROFILE (Updated with title)
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

# ═══════════════════════════════════════════════════════════════
# BONUS CALLBACK
# ═══════════════════════════════════════════════════════════════

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
# SUMMON (Updated with auto-set check)
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
        
        existing = next(
            (c for c in users[user_id]["characters"] 
             if str(c.get("card_id")) == str(character.get("card_id"))), 
            None
        )
        
        if existing:
            users[user_id]["coins"] += cost
            await update.message.reply_text(
                f"⚠️ <b>You already own this card!</b>\n\n"
                f"🎴 {html.escape(character.get('name', '?'))}\n"
                f"🪪 #{character.get('card_id', '????')}\n\n"
                f"💰 {cost:,} coins refunded!\n"
                f"💡 Try /summon again for a different card!",
                parse_mode="HTML"
            )
            return
        
        users[user_id]["coins"] -= cost
        summon_cooldowns[user_id] = now
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
    except Exception as e:
        logging.error(f"Failed to send summon photo: {e}")
        await update.message.reply_text(caption, parse_mode="HTML")
    
    # Check for anime set completion
    await check_anime_completion(user_id, context)

# ═══════════════════════════════════════════════════════════════
# SHOP CALLBACK (Updated with auto-set check)
# ═══════════════════════════════════════════════════════════════

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
            
            existing = next(
                (c for c in users[user_id]["characters"] 
                 if str(c.get("card_id")) == str(char.get("card_id"))), 
                None
            )
            
            if existing:
                await query.answer(
                    f"❌ You already own {char.get('name')}!",
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
        
        # Check for anime set completion
        await check_anime_completion(user_id, context)

# ═══════════════════════════════════════════════════════════════
# TRADE CALLBACK (Updated with auto-set check)
# ═══════════════════════════════════════════════════════════════

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
                receiver_has_offer = any(
                    str(c.get("card_id", "")).zfill(4) == str(oc.get("card_id", "")).zfill(4)
                    for c in users[rid].get("characters", [])
                )
                
                sender_has_want = any(
                    str(c.get("card_id", "")).zfill(4) == str(wc.get("card_id", "")).zfill(4)
                    for c in users[sid].get("characters", [])
                )
                
                if receiver_has_offer:
                    await query.edit_message_text(
                        f"❌ Trade failed!\n"
                        f"{html.escape(users[rid].get('name', 'User'))} already owns {html.escape(oc.get('name', 'card'))}!",
                        parse_mode="HTML"
                    )
                    active_trades.pop(tid, None)
                    return
                
                if sender_has_want:
                    await query.edit_message_text(
                        f"❌ Trade failed!\n"
                        f"You already own {html.escape(wc.get('name', 'card'))}!",
                        parse_mode="HTML"
                    )
                    active_trades.pop(tid, None)
                    return
                
                for uid, card in [(sid, oc), (rid, wc)]:
                    col_ref = users[uid].get("characters", [])
                    cid = str(card.get("card_id", "")).zfill(4)
                    users[uid]["characters"] = [
                        c for c in col_ref 
                        if str(c.get("card_id", "")).zfill(4) != cid
                    ]
                
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
            
            # Check for anime set completion for both users
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

# ═══════════════════════════════════════════════════════════════
# BID RESOLVE (Updated with auto-set check)
# ═══════════════════════════════════════════════════════════════

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
        
        existing = next(
            (c for c in users[uid]["characters"] 
             if str(c.get("card_id")) == str(character.get("card_id"))),
            None
        )
        
        if existing:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Winner already owns this card! Card lost. (Spawn #{spawn_num})",
                parse_mode="HTML"
            )
            return
        
        users[uid]["coins"] -= top_bid
        users[uid]["characters"].append(character)
        save_data()
    
    name = html.escape(users[uid].get("name", "Unknown"))
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🎉 <b>BID OVER! (Spawn #{spawn_num})</b>\n🏆 Winner: {name}\n🎴 {html.escape(character.get('name','?'))}\n💰 Paid: {top_bid:,}",
        parse_mode="HTML"
    )
    
    # Check for anime set completion
    await check_anime_completion(uid, context)

# ═══════════════════════════════════════════════════════════════
# RPS, HL, MINES, GUESS, STATS, ETC (Keep your existing functions)
# ═══════════════════════════════════════════════════════════════

# [All your existing RPS, HL, Mines, Guess, Stats, Checkin, etc. functions remain exactly the same]
# I'll include them in the final code but for brevity showing that they stay

# ═══════════════════════════════════════════════════════════════
# SPAWN FUNCTIONS (Keep existing)
# ═══════════════════════════════════════════════════════════════

# [Keep all your existing spawn_character, end_bid_after, etc.]

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
    init_anime_sets()  # Build anime sets from database
    logging.info(f"✅ Data loaded - {len(users)} users, {len(characters)} cards")
    logging.info(f"✅ Anime sets built - {len(anime_sets_cache)} sets available")
    
    asyncio.create_task(cleanup_old_data())
    asyncio.create_task(cleanup_stale_games())
    asyncio.create_task(cleanup_stale_trades())
    asyncio.create_task(monitor_stuck_bids())
    
    application = ApplicationBuilder().token(TOKEN).build()
    
    # Command handlers
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
    
    # NEW COMMAND HANDLERS
    application.add_handler(CommandHandler("crash", crash))
    application.add_handler(CommandHandler("sets", sets))
    application.add_handler(CommandHandler("gift", gift))
    application.add_handler(CommandHandler("title", title))
    
    # Callback handlers
    application.add_handler(CallbackQueryHandler(collection_buttons, pattern="^col_"))
    application.add_handler(CallbackQueryHandler(bonus_callback, pattern="^bonus_"))
    application.add_handler(CallbackQueryHandler(shop_callback, pattern="^shop_"))
    application.add_handler(CallbackQueryHandler(trade_callback, pattern="^trade_"))
    application.add_handler(CallbackQueryHandler(rps_callback, pattern="^rps_"))
    application.add_handler(CallbackQueryHandler(hl_callback, pattern="^hl_"))
    application.add_handler(CallbackQueryHandler(mines_callback, pattern="^mines_"))
    application.add_handler(CallbackQueryHandler(crash_callback, pattern="^crash_"))
    
    # Message handler
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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import random
import time
import logging
import asyncio
import os
import html
import json
from datetime import datetime
from pymongo import MongoClient
from flask import Flask, jsonify
import threading

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
ITEMS_PER_PAGE = 5
SHOP_MAX_PAGES = 3

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

# ─── MONGODB ───
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["botdb"]
col = db["gamedata"]

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
active_games = {}  # For RPS, HL, Mines
group_message_counts = {}
last_message_time = {}
last_message_text = {}
user_locks = {}
spawn_counter = 0

# ═══════════════════════════════════════════════════════════════
# SAVE / LOAD FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def save_data():
    global users, last_daily, last_weekly, characters
    global last_checkin, checkin_streak, uploaders, spawn_counter
    
    data = {
        "_id": "main",
        "users": users,
        "last_daily": last_daily,
        "last_weekly": last_weekly,
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
        except Exception:
            pass

def load_data():
    global users, last_daily, last_weekly, characters
    global last_checkin, checkin_streak, uploaders, spawn_counter
    
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

            for uid, udata in users.items():
                udata.setdefault("characters", [])
                udata.setdefault("coins", 100)
                udata.setdefault("name", "Unknown")
                udata.setdefault("username", "")
                udata.setdefault("joined", time.time())
                # Game stats
                udata.setdefault("game_stats", {
                    "rps": {"played": 0, "won": 0, "lost": 0, "profit": 0, "best_streak": 0, "current_streak": 0},
                    "hl": {"played": 0, "won": 0, "lost": 0, "profit": 0, "biggest_win": 0, "best_multiplier": 0},
                    "mines": {"played": 0, "won": 0, "lost": 0, "profit": 0, "best_tiles": 0, "best_multiplier": 0},
                    "guess": {"played": 0, "won": 0, "lost": 0, "profit": 0, "best_streak": 0, "current_streak": 0, "biggest_win": 0}
                })
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

# ═══════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════

async def get_user_lock(user_id):
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]

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

def ensure_user(user_id, update=None):
    uid = str(user_id)
    if uid not in users:
        users[uid] = {
            "characters": [], "coins": 100, "joined": time.time(),
            "name": update.effective_user.first_name if update else "Unknown",
            "username": (update.effective_user.username or "") if update else "",
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
    
    users[uid].setdefault("game_stats", {
        "rps": {"played": 0, "won": 0, "lost": 0, "profit": 0, "best_streak": 0, "current_streak": 0},
        "hl": {"played": 0, "won": 0, "lost": 0, "profit": 0, "biggest_win": 0, "best_multiplier": 0},
        "mines": {"played": 0, "won": 0, "lost": 0, "profit": 0, "best_tiles": 0, "best_multiplier": 0},
        "guess": {"played": 0, "won": 0, "lost": 0, "profit": 0, "best_streak": 0, "current_streak": 0, "biggest_win": 0}
    })

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

def get_user_rank(user_id, type="coins"):
    if type == "coins":
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
    except Exception:
        await context.bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def send_shop_page(chat_id, context, user_id):
    page = context.user_data.get("shop_page", 0)
    all_chars = context.user_data.get("shop_list", characters)
    
    all_chars = all_chars[:SHOP_MAX_PAGES]
    total_pages = min(len(all_chars), SHOP_MAX_PAGES)
    
    if page >= total_pages:
        page = total_pages - 1 if total_pages > 0 else 0
    
    if page < 0 or page >= total_pages or not all_chars:
        await context.bot.send_message(chat_id=chat_id, text="🏪 No characters in shop!")
        return
    
    char = all_chars[page]
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
    except Exception:
        await context.bot.send_message(
            chat_id=chat_id, text=text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# ═══════════════════════════════════════════════════════════════
# STATS COMMAND
# ═══════════════════════════════════════════════════════════════

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    
    stats_data = users[user_id].get("game_stats", {})
    rps = stats_data.get("rps", {})
    hl = stats_data.get("hl", {})
    mines = stats_data.get("mines", {})
    guess = stats_data.get("guess", {})
    
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
    
    # RPS Stats
    rps_played = rps.get('played', 0)
    rps_won = rps.get('won', 0)
    rps_wr = round((rps_won / rps_played) * 100) if rps_played > 0 else 0
    text += f"✊ <b>Rock Paper Scissors</b>\n"
    text += f"├ Played: {rps_played}\n"
    text += f"├ Wins: {rps_won} ({rps_wr}%)\n"
    text += f"├ Losses: {rps.get('lost', 0)}\n"
    text += f"├ Profit: {rps.get('profit', 0):+,} coins\n"
    text += f"└ Best Streak: {rps.get('best_streak', 0)}\n\n"
    
    # Higher/Lower Stats
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
    
    # Minesweeper Stats
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
    
    # Guess Game Stats
    guess_played = guess.get('played', 0)
    guess_won = guess.get('won', 0)
    guess_wr = round((guess_won / guess_played) * 100) if guess_played > 0 else 0
    text += f"🎯 <b>Guess Game</b>\n"
    text += f"├ Played: {guess_played}\n"
    text += f"├ Wins: {guess_won} ({guess_wr}%)\n"
    text += f"├ Losses: {guess.get('lost', 0)}\n"
    text += f"├ Profit: {guess.get('profit', 0):+,} coins\n"
    text += f"├ Biggest Win: {guess.get('biggest_win', 0):,} coins\n"
    text += f"└ Best Streak: {guess.get('best_streak', 0)}"
    
    await update.message.reply_text(text, parse_mode="HTML")

# ═══════════════════════════════════════════════════════════════
# GUESS LEADERBOARD AND STATS
# ═══════════════════════════════════════════════════════════════

async def gleader(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guess game leaderboard - top 10 winners"""
    if not users:
        await update.message.reply_text("No players yet!")
        return
    
    # Sort users by guess game wins
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
    text += "🎯 Top 10 Winners:\n\n"
    
    for i, (display, wins, profit) in enumerate(user_wins[:10], 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        text += f"{medal} {display} — {wins} wins (💰 {profit:+,} coins)\n"
    
    if not user_wins:
        text += "No one has won the guess game yet!\n"
        text += "Start a game with /guess [amount]\n"
        text += "Example: /guess 5000"
    
    await update.message.reply_text(text, parse_mode="HTML")

async def gstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Personal guess game stats"""
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

# ═══════════════════════════════════════════════════════════════
# ROCK PAPER SCISSORS GAME
# ═══════════════════════════════════════════════════════════════

async def rps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start Rock Paper Scissors game"""
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
    
    if query.data.startswith("rps_"):
        difficulty = query.data.split("_")[1]
        
        if difficulty not in RPS_DIFFICULTY:
            return
        
        settings = RPS_DIFFICULTY[difficulty]
        
        # Check if user has enough coins
        if users[user_id].get("coins", 0) < settings["bet"]:
            await query.edit_message_text(
                f"❌ You don't have enough coins!\n"
                f"Need: {settings['bet']:,} coins\n"
                f"You have: {users[user_id].get('coins', 0):,} coins",
                parse_mode="HTML"
            )
            return
        
        # Store game state
        active_games[user_id] = {
            "game": "rps",
            "difficulty": difficulty,
            "bet": settings["bet"],
            "multiplier": settings["win_multiplier"]
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
    
    elif query.data.startswith("rps_move_"):
        move = query.data.split("_")[2]
        game = active_games.get(user_id)
        
        if not game or game.get("game") != "rps":
            await query.edit_message_text("Game expired! Use /rps to start new game.")
            return
        
        moves = {"rock": "✊", "paper": "📄", "scissors": "✂️"}
        bot_move = random.choice(["rock", "paper", "scissors"])
        
        # Determine winner
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
            win_amount = -game["bet"]
        
        # Update user coins and stats
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
        
        # Result message
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
        
        # Play again buttons
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
        await query.edit_message_text("❌ Game cancelled.")
        del active_games[user_id]
    
    elif query.data == "rps_exit":
        await query.edit_message_text("Thanks for playing! Use /rps to play again.")

# ═══════════════════════════════════════════════════════════════
# HIGHER OR LOWER GAME
# ═══════════════════════════════════════════════════════════════

async def hl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start Higher or Lower game"""
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
    
    if query.data.startswith("hl_"):
        difficulty = query.data.split("_")[1]
        
        if difficulty in ["easy", "medium", "hard"]:
            settings = HL_DIFFICULTY[difficulty]
            
            if users[user_id].get("coins", 0) < settings["bet"]:
                await query.edit_message_text(
                    f"❌ You don't have enough coins!\n"
                    f"Need: {settings['bet']:,} coins\n"
                    f"You have: {users[user_id].get('coins', 0):,} coins",
                    parse_mode="HTML"
                )
                return
            
            # Generate first card
            card_values = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
            card = random.choice(card_values)
            
            active_games[user_id] = {
                "game": "hl",
                "difficulty": difficulty,
                "bet": settings["bet"],
                "current_multiplier": 1,
                "current_card": card,
                "max_multiplier": settings["max_multiplier"],
                "settings": settings
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
        
        elif difficulty == "cashout":
            game = active_games.get(user_id)
            if game and game.get("game") == "hl":
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
                    f"💰 <b>YOU CASHED OUT!</b> 💰\n\n"
                    f"🎯 Final multiplier: {game['current_multiplier']}x\n"
                    f"🏆 You won: {win_amount:,} coins!",
                    parse_mode="HTML"
                )
                del active_games[user_id]
    
    elif query.data == "hl_higher" or query.data == "hl_lower":
        game = active_games.get(user_id)
        if not game or game.get("game") != "hl":
            await query.edit_message_text("Game expired! Use /hl to start new game.")
            return
        
        user_choice = "higher" if query.data == "hl_higher" else "lower"
        card_values = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
        card_numbers = {"A": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "10": 10, "J": 11, "Q": 12, "K": 13}
        
        current_num = card_numbers[game["current_card"]]
        new_card = random.choice(card_values)
        new_num = card_numbers[new_card]
        
        is_higher = new_num > current_num
        
        if (user_choice == "higher" and is_higher) or (user_choice == "lower" and not is_higher):
            # Correct guess
            game["current_multiplier"] *= 2
            game["current_card"] = new_card
            
            if game["current_multiplier"] >= game["max_multiplier"]:
                # Max multiplier reached - auto cashout
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
                f"💰 Current winnings: {game['bet'] * game['current_multiplier']:,} coins\n\n"
                f"🃏 Current card: <b>{new_card}</b>\n\n"
                f"Continue?",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            # Wrong guess - lose
            async with await get_user_lock(user_id):
                users[user_id]["coins"] -= game["bet"]
                stats = users[user_id]["game_stats"]["hl"]
                stats["played"] += 1
                stats["lost"] += 1
                stats["profit"] -= game["bet"]
                save_data()
            
            await query.edit_message_text(
                f"❌ <b>WRONG!</b>\n\n"
                f"🃏 Card was: {new_card}\n"
                f"You guessed: {user_choice.upper()}\n\n"
                f"💰 You lost {game['bet']:,} coins!",
                parse_mode="HTML"
            )
            del active_games[user_id]
    
    elif query.data == "hl_cancel":
        await query.edit_message_text("❌ Game cancelled.")
        if user_id in active_games:
            del active_games[user_id]

# ═══════════════════════════════════════════════════════════════
# MINESWEEPER GAME (Continue in next message due to length limit)

# ═══════════════════════════════════════════════════════════════
# MINESWEEPER GAME
# ═══════════════════════════════════════════════════════════════

async def mines(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start Minesweeper game"""
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
    """Create minesweeper grid with bombs"""
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
    
    if query.data.startswith("mines_"):
        difficulty = query.data.split("_")[1]
        
        if difficulty in ["easy", "medium", "hard"]:
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
                "settings": settings
            }
            
            await update_mines_display(query, user_id)
    
    elif query.data.startswith("mines_cell_"):
        game = active_games.get(user_id)
        if not game or game.get("game") != "mines":
            await query.edit_message_text("Game expired! Use /mines to start new game.")
            return
        
        cell_index = int(query.data.split("_")[2])
        
        if game["grid"][cell_index]["revealed"]:
            await query.answer("Already revealed!", show_alert=True)
            return
        
        if game["grid"][cell_index]["is_bomb"]:
            # Hit a bomb - game over
            async with await get_user_lock(user_id):
                current_coins = users[user_id].get("coins", 0)
                deduct = min(game["bet"], current_coins)  # never go negative
                users[user_id]["coins"] -= deduct
                stats = users[user_id]["game_stats"]["mines"]
                stats["played"] += 1
                stats["lost"] += 1
                stats["profit"] -= deduct
                save_data()
            
            # Show bomb locations
            grid_display = []
            size = game["grid_size"]
            for i in range(size * size):
                if game["grid"][i]["is_bomb"]:
                    grid_display.append("💣")
                else:
                    grid_display.append("⬜")
            
            # Format grid
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
            # Safe cell
            game["grid"][cell_index]["revealed"] = True
            game["safe_count"] += 1
            
            # Calculate multiplier
            progress = game["safe_count"] / game["total_safe"]
            game["current_multiplier"] = 1 + (game["max_multiplier"] - 1) * progress
            game["current_multiplier"] = round(game["current_multiplier"], 1)
            
            current_win = int(game["bet"] * game["current_multiplier"])
            
            if game["safe_count"] >= game["total_safe"]:
                # Won the game - all safe tiles found
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

async def update_mines_display(query, user_id):
    """Update minesweeper display with buttons"""
    game = active_games.get(user_id)
    if not game:
        return
    
    size = game["grid_size"]
    current_win = int(game["bet"] * game["current_multiplier"])
    
    # Create grid buttons
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
    
    # Add control buttons
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
# SPAWN DEBUG COMMANDS
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
    await update.message.reply_text(f"✅ Spawn counter reset for this group!")

# ═══════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    save_data()
    await update.message.reply_text(
        "🎮 <b>Welcome to Anime Card Bot!</b>\n\n"
        "📋 <b>Commands:</b>\n"
        "💰 <b>Economy:</b>\n"
        "/bonus - Daily/Weekly rewards\n"
        "/profile - Your stats\n"
        "/checkin - Daily group check-in\n"
        "/checkintop - Check-in streak leaderboard\n\n"
        "🎴 <b>Cards:</b>\n"
        "/summon - Get random card (7000 coins)\n"
        "/collection - View your cards\n"
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
        "/guess [amount] - Guess the number game\n"
        "/gleader - Guess game leaderboard\n"
        "/gstats - Your guess stats\n"
        "/stats - All game stats\n\n"
        "🏆 <b>Leaderboard:</b>\n"
        "/ctop - Coin leaderboard\n\n"
        "👑 <b>Admin:</b>\n"
        "/upload - Upload character\n"
        "/give - Give cards (owner)\n"
        "/broadcast - Announce (owner)\n"
        "/debugspawn - Check spawn status\n"
        "/forcespawn - Force spawn\n"
        "/resetspawn - Reset spawn counter",
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
        "/rps - Rock Paper Scissors (Easy/Medium/Hard)\n"
        "/hl - Higher or Lower (Easy/Medium/Hard)\n"
        "/mines - Minesweeper (Easy/Medium/Hard)\n"
        "/guess [amount] - Guess the number game\n"
        "/gleader - Guess game leaderboard\n"
        "/gstats - Your guess stats\n"
        "/stats - All game stats\n\n"
        "🏆 <b>Leaderboard:</b>\n"
        "/ctop - Coin leaderboard\n\n"
        "👑 <b>Admin:</b>\n"
        "/upload - Upload character (uploaders)\n"
        "/adduploader - Add uploader (owner)\n"
        "/give - Give cards (owner)\n"
        "/broadcast - Announce (owner)\n"
        "/debugspawn - Check spawn status (owner)\n"
        "/forcespawn - Force spawn (owner)\n"
        "/resetspawn - Reset spawn counter (owner)",
        parse_mode="HTML"
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

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    d = users[user_id]
    coin_rank = get_user_rank(user_id, "coins")
    
    text = (
        f"👤 <b>Profile</b>\n\n"
        f"🏷 Name: {html.escape(update.effective_user.first_name)}\n"
        f"💰 Coins: {d.get('coins', 0):,} (Rank #{coin_rank if coin_rank else 'N/A'})\n"
        f"🎴 Cards: {len(d.get('characters', []))}\n"
        f"📅 Check-in streak: {checkin_streak.get(user_id, 0)} days"
    )
    await update.message.reply_text(text, parse_mode="HTML")

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

async def guess_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    # Try to fetch real name from Telegram if stored as Unknown
    try:
        chat_member = await context.bot.get_chat_member(chat_id, int(uid))
        real_name = chat_member.user.first_name
        users[uid]["name"] = real_name
    except Exception:
        pass
    
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

# ═══════════════════════════════════════════════════════════════
# MESSAGE HANDLER
# ═══════════════════════════════════════════════════════════════

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
            
            logging.info(f"Group {chat_id} message count: {group_message_counts[chat_id]}/{SPAWN_EVERY}")
            
            if group_message_counts[chat_id] >= SPAWN_EVERY:
                group_message_counts[chat_id] = 0
                if characters and chat_id not in active_bid:
                    logging.info(f"🎯 Spawning character in group {chat_id}!")
                    asyncio.create_task(spawn_character(chat_id, context))

    # Guess game
    if chat_id == GROUP_ID and chat_id in active_guess:
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
                        # Update guess stats
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
                    # Update guess stats for loss
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
# FLASK + MAIN
# ═══════════════════════════════════════════════════════════════

flask_app = Flask(__name__)

@flask_app.route('/')
def index():
    return jsonify({"status": "Bot is running!", "users": len(users), "cards": len(characters)})

@flask_app.route('/health')
def health():
    return jsonify({"status": "ok"})

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False)

async def main():
    load_data()
    logging.info(f"✅ Data loaded - {len(users)} users, {len(characters)} cards")
    
    asyncio.create_task(cleanup_old_data())
    
    application = ApplicationBuilder().token(TOKEN).build()
    
    # Add all handlers
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
    
    # Game commands
    application.add_handler(CommandHandler("rps", rps))
    application.add_handler(CommandHandler("hl", hl))
    application.add_handler(CommandHandler("mines", mines))
    
    # Spawn debug commands
    application.add_handler(CommandHandler("debugspawn", debug_spawn))
    application.add_handler(CommandHandler("forcespawn", force_spawn))
    application.add_handler(CommandHandler("resetspawn", reset_spawn_counter))
    
    # Callback handlers
    application.add_handler(CallbackQueryHandler(collection_buttons, pattern="^col_"))
    application.add_handler(CallbackQueryHandler(bonus_callback, pattern="^bonus_"))
    application.add_handler(CallbackQueryHandler(shop_callback, pattern="^shop_"))
    application.add_handler(CallbackQueryHandler(trade_callback, pattern="^trade_"))
    application.add_handler(CallbackQueryHandler(rps_callback, pattern="^rps_"))
    application.add_handler(CallbackQueryHandler(hl_callback, pattern="^hl_"))
    application.add_handler(CallbackQueryHandler(mines_callback, pattern="^mines_"))
    
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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import random
import time
import logging
import asyncio
import os
import html
import json
import io
from datetime import datetime, timedelta
from pymongo import MongoClient
from flask import Flask, request, jsonify
import threading
import re
from PIL import Image, ImageDraw, ImageFont

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
SHOP_ITEMS_PER_PAGE = 1
SHOP_MAX_PAGES = 3

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

# Initialize WORD_LIST
WORD_LIST = load_words_once()

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

def get_font(size, bold=False):
    try:
        path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()

def draw_rounded_rect(draw, xy, radius, fill):
    x1, y1, x2, y2 = xy
    draw.rectangle([x1+radius, y1, x2-radius, y2], fill=fill)
    draw.rectangle([x1, y1+radius, x2, y2-radius], fill=fill)
    draw.ellipse([x1, y1, x1+2*radius, y1+2*radius], fill=fill)
    draw.ellipse([x2-2*radius, y1, x2, y1+2*radius], fill=fill)
    draw.ellipse([x1, y2-2*radius, x1+2*radius, y2], fill=fill)
    draw.ellipse([x2-2*radius, y2-2*radius, x2, y2], fill=fill)

def draw_centered_text(draw, text, x, y, w, font, color):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    draw.text((x + (w - tw) // 2, y), text, fill=color, font=font)

def to_buf(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ─── WORDLE HELPER FUNCTIONS ───
def get_wordle_emoji_list(guess, answer):
    """Return emoji list for a single guess"""
    guess = guess.lower()
    answer = answer.lower()
    result = ['⬜'] * 5
    answer_chars = list(answer)
    
    for i in range(5):
        if guess[i] == answer[i]:
            result[i] = '🟩'
            answer_chars[i] = None
    
    for i in range(5):
        if result[i] != '🟩' and guess[i] in answer_chars:
            result[i] = '🟨'
            answer_chars[answer_chars.index(guess[i])] = None
    
    return result

def format_wordle_table(guesses):
    if not guesses:
        return ""
    result = ""
    for word, emojis in guesses:
        result += f"{word.upper()}\n{''.join(emojis)}\n\n"
    return result

# ─── WORDLE IMAGE ───
def generate_wordle_image(guesses, total_attempts=6):
    COLOR_GREEN  = (106, 170, 100)
    COLOR_YELLOW = (201, 180, 88)
    COLOR_GRAY   = (120, 124, 126)
    COLOR_EMPTY  = (50, 50, 50)
    COLOR_BG     = (18, 18, 19)
    COLOR_TEXT   = (255, 255, 255)

    CELL = 80
    GAP  = 8
    PAD  = 24
    HEADER = 60

    W = PAD*2 + 5*CELL + 4*GAP
    H = PAD*2 + HEADER + total_attempts*CELL + (total_attempts-1)*GAP

    img  = Image.new("RGB", (W, H), COLOR_BG)
    draw = ImageDraw.Draw(img)

    font_title  = get_font(26, bold=True)
    font_letter = get_font(38, bold=True)

    draw_centered_text(draw, "🎮  WORDLE", 0, 14, W, font_title, (255,255,255))

    emoji_to_color = {'🟩': COLOR_GREEN, '🟨': COLOR_YELLOW, '⬜': COLOR_GRAY}

    for row in range(total_attempts):
        for col in range(5):
            x = PAD + col*(CELL+GAP)
            y = PAD + HEADER + row*(CELL+GAP)
            if row < len(guesses):
                word, emojis = guesses[row]
                color  = emoji_to_color.get(emojis[col], COLOR_GRAY)
                letter = word[col].upper()
            else:
                color  = COLOR_EMPTY
                letter = ""
            draw_rounded_rect(draw, [x, y, x+CELL, y+CELL], 6, color)
            if letter:
                bbox = draw.textbbox((0,0), letter, font=font_letter)
                tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
                draw.text((x+(CELL-tw)//2, y+(CELL-th)//2), letter, fill=COLOR_TEXT, font=font_letter)

    return to_buf(img)

# ─── PROFILE IMAGE ───
async def generate_profile_image(user, user_data, stats, coin_rank, wordle_rank, bot):
    W, H = 520, 340
    BG     = (15, 15, 30)
    ACCENT = (88, 101, 242)
    GOLD   = (255, 215, 0)
    WHITE  = (255, 255, 255)
    GRAY   = (150, 150, 180)

    img  = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    draw_rounded_rect(draw, [0, 0, W, 68], 0, ACCENT)
    draw.text((20, 18), "👤  PROFILE", fill=WHITE, font=get_font(30, bold=True))

    avatar_size = 100
    ax, ay = 20, 85
    try:
        photos = await bot.get_user_profile_photos(user.id, limit=1)
        if photos.total_count > 0:
            file = await bot.get_file(photos.photos[0][-1].file_id)
            photo_bytes = await file.download_as_bytearray()
            avatar = Image.open(io.BytesIO(photo_bytes)).convert("RGBA").resize((avatar_size, avatar_size))
            mask = Image.new("L", (avatar_size, avatar_size), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, avatar_size, avatar_size], fill=255)
            avatar_bg = Image.new("RGBA", (avatar_size, avatar_size), (0,0,0,0))
            avatar_bg.paste(avatar, mask=mask)
            img.paste(avatar_bg, (ax, ay), mask=avatar_bg)
        else:
            raise Exception("no photo")
    except Exception:
        draw.ellipse([ax, ay, ax+avatar_size, ay+avatar_size], fill=ACCENT)
        draw.text((ax+25, ay+22), user.first_name[0].upper(), fill=WHITE, font=get_font(55, bold=True))

    name = user.first_name[:18]
    username = f"@{user.username}" if user.username else ""
    draw.text((140, 90), name, fill=WHITE, font=get_font(26, bold=True))
    draw.text((140, 122), username, fill=GRAY, font=get_font(19))
    draw.text((140, 148), f"Coin Rank: #{coin_rank}", fill=GOLD, font=get_font(18))

    stat_items = [
        ("💰 Coins",      f"{user_data.get('coins',0):,}",           GOLD),
        ("🎴 Cards",      str(len(user_data.get('characters',[]))),   (88,200,242)),
        ("🔥 Streak",     f"{stats.get('current_streak',0)} days",   (255,140,0)),
        ("🏆 Wordle Rank",f"#{wordle_rank if wordle_rank else 'N/A'}",(255,100,100)),
    ]

    for i, (label, value, color) in enumerate(stat_items):
        x = 20 + (i % 2) * 255
        y = 205 + (i // 2) * 65
        draw_rounded_rect(draw, [x, y, x+235, y+55], 10, (28,28,50))
        draw.text((x+12, y+7),  label, fill=GRAY,  font=get_font(16))
        draw.text((x+12, y+27), value, fill=color, font=get_font(20, bold=True))

    return to_buf(img)

# ─── COMMAND HANDLERS (abbreviated for length - all your existing commands go here) ───

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
        "/wordle - Play Wordle\n"
        "/cwordle - Cancel Wordle\n"
        "/wstats - Wordle stats\n"
        "/wtop - Wordle leaderboard\n"
        "/ctop - Coin leaderboard\n\n"
        "👑 <b>Admin:</b>\n"
        "/upload - Upload character\n"
        "/adduploader - Add uploader (owner)\n"
        "/give - Give cards (owner)\n"
        "/broadcast - Announce (owner)",
        parse_mode="HTML"
    )

# ─── FLASK FOR HEALTH CHECK (KEEPS BOT ALIVE ON RENDER) ───
flask_app = Flask(__name__)

@flask_app.route('/health')
def health():
    return "OK", 200

@flask_app.route('/')
def index():
    return "Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port, use_reloader=False)

# ─── MAIN ───
async def main():
    if not TOKEN:
        print("ERROR: BOT_TOKEN not set!")
        return

    load_data()
    print("✅ Data loaded from MongoDB")
    print(f"✅ Word list loaded: {len(WORD_LIST)} words")
    print("✅ Bot starting with polling...")

    asyncio.create_task(cleanup_old_data())

    application = ApplicationBuilder().token(TOKEN).build()

    # Add all handlers (you have many more - I'm showing essentials)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    # ... add all your other handlers here (keep the same as your original) ...

    # Start Flask in background thread (keeps Render happy)
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("✅ Health check server started on port", os.environ.get("PORT", 8080))

    # Start bot polling
    print("✅ Bot is running and responding!")
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
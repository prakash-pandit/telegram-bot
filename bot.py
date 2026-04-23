from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import random
import time
import json
import logging
import asyncio
import html
import os
from datetime import datetime
from pymongo import MongoClient

logging.basicConfig(level=logging.INFO)

# ─────────────────────────────────────────
# MONGODB SETUP
# ─────────────────────────────────────────

MONGO_URI = os.environ.get("MONGO_URI")
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["botdb"]
col = db["gamedata"]

# DATA
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

# BID SYSTEM
group_message_counts = {}   # chat_id -> message count since last spawn
active_bid = {}             # chat_id -> bid session info
SPAWN_EVERY = 50            # messages before a character spawns
BID_DURATION = 60           # base seconds for bid
BID_EXTEND = 10             # seconds added per new bid

# TRADE SYSTEM
active_trades = {}          # trade_id -> trade session info
TRADE_TIMEOUT = 120         # seconds before trade expires

# GUESS & CHECKIN
active_guess = {}   # chat_id -> {"answer": int, "prize": int, "started_by": str}
last_checkin = {}   # user_id -> last checkin timestamp
checkin_streak = {} # user_id -> streak count

# ANTI-SPAM
last_message_time = {}      # (chat_id, user_id) -> last counted timestamp
last_message_text = {}      # (chat_id, user_id) -> last counted message text
USER_MSG_COOLDOWN = 5       # seconds a user must wait before their msg counts again
MIN_MSG_LENGTH = 4          # minimum real letters required

# ─────────────────────────────────────────
# SAVE / LOAD  (MongoDB)
# ─────────────────────────────────────────

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
    global users, uploaders, last_daily, last_weekly, daily_streak, \
           active_wordle, characters, last_checkin, checkin_streak
    try:
        data = col.find_one({"_id": "main"})
        if not data:
            print("No data found in MongoDB, starting fresh.")
            return

        users           = data.get("users", {})
        uploaders       = set(map(str, data.get("uploaders", [])))
        last_daily      = data.get("last_daily", {})
        last_weekly     = data.get("last_weekly", {})
        daily_streak    = data.get("daily_streak", {})
        active_wordle   = data.get("active_wordle", {})
        characters      = data.get("characters", [])
        last_checkin    = data.get("last_checkin", {})
        checkin_streak  = data.get("checkin_streak", {})

        # migrate old characters without card_id
        for i, c in enumerate(characters):
            if "card_id" not in c:
                c["card_id"] = str(i + 1).zfill(4)

        for uid, udata in users.items():
            if "name" not in udata:
                udata["name"] = "Unknown"
            if "username" not in udata:
                udata["username"] = ""
            if "wordle_stats" not in udata:
                udata["wordle_stats"] = {
                    "played": 0, "won": 0, "lost": 0,
                    "total_guesses": 0, "best_win": 0,
                    "coins_earned": 0, "current_streak": 0, "best_streak": 0
                }
            if "wordle_wins" not in udata:
                udata["wordle_wins"] = {"today": 0, "week": 0, "month": 0, "alltime": 0}
            if "wordle_last_date" not in udata:
                udata["wordle_last_date"] = {}

    except Exception as e:
        print("Load error:", e)
        users          = {}
        uploaders      = set()
        last_daily     = {}
        daily_streak   = {}
        active_wordle  = {}
        characters     = []
        last_checkin   = {}
        checkin_streak = {}


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────

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
    else:
        if update:
            users[user_id]["name"] = update.effective_user.first_name
            users[user_id]["username"] = update.effective_user.username or ""
        for key, default in [
            ("wordle_wins", {"today": 0, "week": 0, "month": 0, "alltime": 0}),
            ("wordle_last_date", {}),
            ("wordle_stats", {
                "played": 0, "won": 0, "lost": 0,
                "total_guesses": 0, "best_win": 0,
                "coins_earned": 0, "current_streak": 0, "best_streak": 0
            })
        ]:
            if key not in users[user_id]:
                users[user_id][key] = default

def format_time(seconds):
    seconds = int(seconds)
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60

    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
    
def next_card_id():
    """Auto-increment card ID as 4-digit zero-padded string."""
    if not characters:
        return "0001"
    existing = []
    for c in characters:
        try:
            existing.append(int(c.get("card_id", "0")))
        except ValueError:
            pass
    return str(max(existing) + 1).zfill(4)

def load_words():
    with open("Word.txt", "r") as f:
        words = f.read().splitlines()
    return words

def rarity_label(rarity):
    return {1: "⚪ Common", 2: "🔵 Rare", 3: "🟣 Epic", 4: "🟡 Legendary", 5: "⚡ Celebrity"}.get(rarity, "Unknown")

def base_bid_price(rarity):
    return 125000

characters = []

# ─────────────────────────────────────────
# BID SYSTEM
# ─────────────────────────────────────────

async def spawn_character(chat_id, context):
    """Pick a random character and start a bid session."""
    if not characters:
        return
    if chat_id in active_bid:
        return  # already a bid running

    character = random.choice(characters)
    rarity = character.get("rarity", 1)
    start_price = base_bid_price(rarity)

    active_bid[chat_id] = {
        "character": character,
        "top_bidder": None,
        "top_bid": start_price,
        "end_time": time.time() + BID_DURATION,
        "started": time.time()
    }

    # ✅ ALL CODE MUST BE INDENTED INSIDE FUNCTION
    rl = rarity_label(rarity)
    card_id = character.get("card_id", "????")
    c_name = html.escape(character.get("name", "?"))
    c_anime = html.escape(character.get("anime", "?"))

    special = ""
    if rarity == 4:
        special = "🌟 <b>LEGENDARY DROP!!!</b> 🌟\n\n"
    elif rarity == 5:
        special = "⚡ <b>⭐ CELEBRITY DROP!!! ⭐</b> ⚡\n\n"

    caption = (
        special +
        "🌟 <b>A CHARACTER HAS APPEARED!</b>\n\n"
        f"🎴 <b>Name:</b> {c_name}\n"
        f"🎬 <b>Anime:</b> {c_anime}\n"
        f"⭐ <b>Rarity:</b> {rl}\n"
        f"🪪 <b>Card ID:</b> #{card_id}\n\n"
        f"💰 <b>Starting Bid:</b> {start_price} coins\n"
        f"⏳ <b>Time:</b> {BID_DURATION} seconds\n\n"
        f"Use /bid [amount] to place your bid!\n"
        f"Each bid extends time by +{BID_EXTEND}s ⚡"
    )

    try:
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=character["file_id"],
            caption=caption,
            parse_mode="HTML"
        )
        active_bid[chat_id]["message_id"] = msg.message_id

    except Exception as e:
        logging.error(f"Spawn error: {e}")
        active_bid.pop(chat_id, None)
        return

    # Schedule bid end
    asyncio.create_task(end_bid_after(chat_id, context))

async def end_bid_after(chat_id, context):
    """Wait until bid timer runs out, then resolve."""
    while True:
        session = active_bid.get(chat_id)
        if not session:
            return
        remaining = session["end_time"] - time.time()
        if remaining <= 0:
            break
        await asyncio.sleep(min(remaining, 2))

    await resolve_bid(chat_id, context)

async def resolve_bid(chat_id, context):
    """End the bid and give character to winner."""
    session = active_bid.pop(chat_id, None)
    if not session:
        return

    character = session["character"]
    top_bidder = session["top_bidder"]
    top_bid = session["top_bid"]
    card_id = character.get("card_id", "????")

    if not top_bidder:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⌛ Bid ended for <b>{html.escape(character.get('name','?'))}</b> — No one bid! Character lost. 😔",
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Resolve error: {e}")
        return

    uid = top_bidder
    ensure_user(uid)

    # Deduct coins
    if users[uid]["coins"] < top_bid:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"⚠️ Bid ended! Winner didn't have enough coins!\n"
                    f"<b>{html.escape(character.get('name','?'))}</b> is lost. 😢"
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Resolve error: {e}")
        return

    users[uid]["coins"] -= top_bid
    users[uid]["characters"].append(character)
    save_data()

    name = html.escape(users[uid].get("name", "Unknown"))
    username = users[uid].get("username", "")
    display = f"@{html.escape(username)}" if username else name
    c_name = html.escape(character.get("name", "?"))

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🎉 <b>BID OVER!</b>\n\n"
                f"🏆 Winner: {display}\n"
                f"🎴 Character: <b>{c_name}</b>\n"
                f"🪪 Card ID: #{card_id}\n"
                f"💰 Paid: {top_bid} coins\n\n"
                f"The card has been added to their collection!"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Resolve announce error: {e}")

async def bid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /bid command."""
    chat_id = update.effective_chat.id
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)

    if chat_id not in active_bid:
        await update.message.reply_text("❌ No active bid right now!")
        return

    session = active_bid[chat_id]

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /bid [amount]")
        return

    amount = int(context.args[0])
    current_top = session["top_bid"]
    character = session["character"]

    if amount <= current_top:
        await update.message.reply_text(
            f"❌ Your bid must be higher than the current top: <b>{current_top}</b> coins!",
            parse_mode="HTML"
        )
        return

    if users[user_id]["coins"] < amount:
        await update.message.reply_text(
            f"❌ Not enough coins! You have <b>{users[user_id]['coins']}</b> coins.",
            parse_mode="HTML"
        )
        return

    if session["top_bidder"] == user_id:
        await update.message.reply_text("❌ You are already the top bidder!")
        return

    session["top_bidder"] = user_id
    session["top_bid"] = amount
    session["end_time"] = max(session["end_time"], time.time()) + BID_EXTEND

    name = html.escape(update.effective_user.first_name or "")
    username = update.effective_user.username or ""
    display = f"@{html.escape(username)}" if username else name
    c_name = html.escape(character.get("name", "?"))
    remaining = max(1, int(session["end_time"] - time.time()))

    await update.message.reply_text(
        f"⚡ <b>NEW TOP BID!</b>\n\n"
        f"👤 {display}\n"
        f"💰 {amount} coins\n"
        f"🎴 {c_name}\n"
        f"⏳ +{BID_EXTEND}s added — {remaining}s remaining!\n\n"
        f"Can anyone beat it? Use /bid [amount]",
        parse_mode="HTML"
    )

ITEMS_PER_PAGE = 5
    #inline 
    
from telegram import InlineQueryResultCachedPhoto
from telegram.ext import InlineQueryHandler

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.strip().lower()
    user_id = str(update.inline_query.from_user.id)

    if user_id not in users:
        await update.inline_query.answer([], cache_time=0)
        return

    collection = users[user_id].get("characters", [])

    # Filter by search query if typed
    if query:
        collection = [
            c for c in collection
            if isinstance(c, dict) and query in c.get("name", "").lower()
        ]

    rarity_map = {1: "⚪ Common", 2: "🔵 Rare", 3: "🟣 Epic", 4: "🟡 Legendary", 5: "⚡ Celebrity"}
    results = []

    for i, char in enumerate(collection[:50]):  # Telegram max 50
        if not isinstance(char, dict):
            continue
        file_id = char.get("file_id")
        if not file_id:
            continue
        name = char.get("name", "?")
        anime = char.get("anime", "?")
        rl = rarity_map.get(char.get("rarity", 1), "Unknown")
        card_id = char.get("card_id", "????")

        results.append(
            InlineQueryResultCachedPhoto(
                id=str(i),
                photo_file_id=file_id,
                title=name,
                caption=(
                    f"🎴 <b>{html.escape(name)}</b>\n"
                    f"🎬 {html.escape(anime)}\n"
                    f"⭐ {rl}\n"
                    f"🪪 #{card_id}"
                ),
                parse_mode="HTML"
            )
        )

    await update.inline_query.answer(results, cache_time=0)

#collection page

async def send_collection_page(chat_id, context, user_id):
    collection = context.user_data.get("col_list", [])
    page = context.user_data.get("col_page", 0)

    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    page_items = collection[start:end]

    total_pages = max(1, (len(collection) - 1) // ITEMS_PER_PAGE + 1)

    rarity_map = {1: "⚪", 2: "🔵", 3: "🟣", 4: "🟡", 5: "⚡"}

    text = f"<b>{users[user_id]['name']}'s COLLECTION</b>\n"
    text += f"PAGE {page+1}/{total_pages}\n\n"

    for char in page_items:
        if not isinstance(char, dict):
            continue

        rl = rarity_map.get(char.get("rarity", 1), "❓")
        name = html.escape(char.get("name", "?"))
        anime = html.escape(char.get("anime", "?"))
        cid = char.get("card_id", "????")

        text += f"{rl} {name} (x1)\n[{anime}]\nID: {cid}\n\n"

    
# ✅ FIX: prevent crash if empty
    if not page_items:
        await context.bot.send_message(chat_id=chat_id, text="No characters on this page.")
        return

    total_cards = len(context.user_data.get("col_list", []))
    keyboard = [
        [
            InlineKeyboardButton("⬅️", callback_data="col_prev"),
            InlineKeyboardButton("➡️", callback_data="col_next")
        ],
        [InlineKeyboardButton(f"🖼 INLINE ({total_cards})", switch_inline_query_current_chat="")],
        [InlineKeyboardButton("❌ Close", callback_data="col_close")]
    ]

    first_char = page_items[0]
    photo = first_char.get("file_id")

    await context.bot.send_photo(
        chat_id=chat_id,
        photo=photo,
        caption=text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )# ─────────────────────────────────────────
# START
# ─────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    save_data()
    await update.message.reply_text(
        "🎮 Welcome!\n\n"
        "Commands:\n"
        "/summon — spend coins for a card\n"
        "/bid [amount] — bid on a spawned character\n"
        "/daily — collect daily coins\n"
        "/collection — see your cards\n"
        "/profile — your profile\n"
        "/wordle — play wordle\n"
        "/wstats — wordle stats\n"
        "/wtop — wordle leaderboard\n"
        "/ctop — coin leaderboard"
    )

# ─────────────────────────────────────────
# SUMMON
# ─────────────────────────────────────────

async def summon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    now = time.time()
    ensure_user(user_id, update)
    cost = 7000

    if user_id in summon_cooldowns and now - summon_cooldowns[user_id] < 30:
        remaining = 30 - (now - summon_cooldowns[user_id])
        seconds = max(1, int(remaining + 0.99))
        await update.message.reply_text(f"⏳ Wait {seconds} sec")
        return

    if users[user_id]["coins"] < cost:
        await update.message.reply_text("Not enough coins ❌")
        return

    users[user_id]["coins"] -= cost
    summon_cooldowns[user_id] = now

    rarity_weights = {1: 55, 2: 25, 3: 10, 4: 8, 5: 2}
    rarity = random.choices(list(rarity_weights.keys()), weights=rarity_weights.values())[0]
    filtered_chars = [c for c in characters if c["rarity"] == rarity]

    if not filtered_chars:
        await update.message.reply_text("No characters available ❌")
        return

    character = random.choice(filtered_chars)
    users[user_id]["characters"].append(character)
    save_data()

    rl = rarity_label(character["rarity"])
    card_id = character.get("card_id", "????")
    c_name = html.escape(character.get("name", "?"))
    c_anime = html.escape(character.get("anime", "?"))

    caption = (
        "🎰 <b>NEW SUMMON!</b>\n\n"
        f"🎴 Name: {c_name}\n"
        f"🎬 Anime: {c_anime}\n"
        f"⭐ Rarity: {rl}\n"
        f"🪪 Card ID: #{card_id}\n"
    )

    await update.message.reply_photo(photo=character["file_id"], caption=caption, parse_mode="HTML")

# ─────────────────────────────────────────
# WORDLE
# ─────────────────────────────────────────

async def wordle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)

    if user_id in active_wordle:
        game = active_wordle[user_id]
        board = ""
        for g, e in game["guesses"]:
            board += f"{e}  {g.upper()}\n"
        await update.message.reply_text(
            f"⚠️ You already have an active game!\n\n"
            f"{board}\n"
            f"Attempts left: {6 - len(game['guesses'])}\n\n"
            f"Keep guessing or type /cwordle to cancel!"
        )
        return

    words = load_words()
    secret = random.choice(words).lower()
    active_wordle[user_id] = {"answer": secret, "guesses": []}
    save_data()

    await update.message.reply_text(
        "🎮 Wordle started!\n"
        "Just type any 5-letter word in chat to guess!\n\n"
        "🟩 = Right letter, right spot\n"
        "🟨 = Right letter, wrong spot\n"
        "⬜ = Letter not in word\n\n"
        "You have 6 attempts! Good luck! 🍀\n\n"
        "Type /cwordle to cancel the game."
    )

async def cwordle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id in active_wordle:
        answer = active_wordle[user_id]["answer"]
        del active_wordle[user_id]
        ensure_user(user_id, update)
        stats = users[user_id]["wordle_stats"]
        stats["played"] += 1
        stats["lost"] += 1
        stats["current_streak"] = 0
        save_data()
        await update.message.reply_text(
            f"❌ Game cancelled!\nThe word was: *{answer.upper()}*",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("You have no active wordle game!")

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

# ─────────────────────────────────────────
# MESSAGE HANDLER (wordle + spawn counter)
# ─────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    user_id = str(update.effective_user.id)
    text = update.message.text.strip().lower()

    # ── Message counter for character spawn (anti-spam) ──
    if update.effective_chat.type in ("group", "supergroup"):
        now = time.time()
        key = (chat_id, user_id)

        # 1. Must have enough real letters (blocks: aaa, ..., ,,, symbols)
        real_letters = sum(1 for c in text if c.isalpha())
        if real_letters < MIN_MSG_LENGTH:
            pass  # skip counting, fall through to wordle check
        else:
            # 2. Per-user cooldown — one count per USER_MSG_COOLDOWN seconds
            last_t = last_message_time.get(key, 0)
            if now - last_t < USER_MSG_COOLDOWN:
                pass  # too fast, skip
            else:
                # 3. No counting the same message twice in a row
                last_txt = last_message_text.get(key, "")
                if text == last_txt:
                    pass  # repeated message, skip
                else:
                    # ✅ Valid message — count it
                    last_message_time[key] = now
                    last_message_text[key] = text
                    group_message_counts[chat_id] = group_message_counts.get(chat_id, 0) + 1
                    if group_message_counts[chat_id] >= SPAWN_EVERY:
                        group_message_counts[chat_id] = 0
                        if characters and chat_id not in active_bid:
                            asyncio.create_task(spawn_character(chat_id, context))

    # ── Guess game check (group only) ──
    if chat_id == GROUP_ID and chat_id in active_guess:
        raw_text = update.message.text.strip()
        handled = await handle_guess(chat_id, user_id, raw_text, update, context)
        if handled:
            return

    # ── Wordle guess ──
    # Normalize input
    text = text.lower().strip()

    # Validate input
    if len(text) != 5 or not text.isalpha():
        return

    if user_id not in active_wordle:
        return

    ensure_user(user_id, update)

    game = active_wordle[user_id]
    answer = game["answer"].lower()
    guesses = game.get("guesses", [])

    # Generate emoji result
    emoji = get_wordle_emoji(text, answer)

    # Store guess
    guesses.append((text, emoji))
    active_wordle[user_id]["guesses"] = guesses
    save_data()

    # Build board cleanly
    board = "\n".join(f"{e}  {g.upper()}" for g, e in guesses)

    # ✅ WIN CASE
    if text == answer:
        guess_count = len(guesses)

        if guess_count == 1:
            coins_earned = 25000
        elif guess_count == 2:
            coins_earned = 15000
        elif guess_count == 3:
            coins_earned = 10000
        elif guess_count == 4:
            coins_earned = 5000
        elif guess_count == 5:
            coins_earned = 2000
        else:
            coins_earned = 1000

        stats = users[user_id]["wordle_stats"]
        stats["played"] += 1
        stats["won"] += 1
        stats["total_guesses"] += guess_count
        stats["current_streak"] += 1

        if stats["best_win"] == 0 or guess_count < stats["best_win"]:
            stats["best_win"] = guess_count

        if stats["current_streak"] > stats["best_streak"]:
            stats["best_streak"] = stats["current_streak"]

        # 🔥 Streak bonus
        streak_bonus = 0
        streak_msg = ""

        if stats["current_streak"] == 3:
            streak_bonus = 5000
            streak_msg = "\n🔥 3 Win Streak! +5,000 bonus coins!"
        elif stats["current_streak"] == 5:
            streak_bonus = 15000
            streak_msg = "\n🔥🔥 5 Win Streak! +15,000 bonus coins!"
        elif stats["current_streak"] == 10:
            streak_bonus = 50000
            streak_msg = "\n🔥🔥🔥 10 Win Streak! +50,000 bonus coins!"

        total_coins = coins_earned + streak_bonus
        users[user_id]["coins"] += total_coins
        stats["coins_earned"] = stats.get("coins_earned", 0) + total_coins

        # 📅 reset tracking
        today = datetime.now().strftime("%Y-%m-%d")
        week = datetime.now().strftime("%Y-W%W")
        month = datetime.now().strftime("%Y-%m")

        wins = users[user_id]["wordle_wins"]
        last = users[user_id].get("wordle_last_date", {})
        if not isinstance(last, dict):
            last = {}

        if last.get("today") != today: wins["today"] = 0
        if last.get("week") != week: wins["week"] = 0
        if last.get("month") != month: wins["month"] = 0

        wins["today"] += 1
        wins["week"] += 1
        wins["month"] += 1
        wins["alltime"] = wins.get("alltime", 0) + 1

        users[user_id]["wordle_wins"] = wins
        users[user_id]["wordle_last_date"] = {
            "today": today,
            "week": week,
            "month": month
        }

        # ✅ special message
        special = ""
        if guess_count == 1:
            special = "\n🔥🔥 PERFECT GUESS! 🔥🔥"

        del active_wordle[user_id]
        save_data()

        win_text = (
            f"🎉 <b>WORDLE COMPLETE!</b>\n\n"
            f"{board}\n"
            f"🏆 <b>Guesses:</b> {guess_count}\n"
            f"💰 <b>Reward:</b> {total_coins}\n"
            f"🔥 <b>Streak:</b> {stats['current_streak']}"
            f"{special}"
            f"{streak_msg}"
        )

        await update.message.reply_text(win_text, parse_mode="HTML")

    # ❌ LOSE CASE
    elif len(guesses) >= 6:
        stats = users[user_id]["wordle_stats"]
        stats["played"] += 1
        stats["lost"] += 1
        stats["current_streak"] = 0

        del active_wordle[user_id]
        save_data()

        await update.message.reply_text(
            f"{board}\n❌ Game over!\nThe word was: *{answer.upper()}*\n💔 Streak reset!",
            parse_mode="Markdown"
        )

    # 🔁 CONTINUE
    else:
        remaining = 6 - len(guesses)
        await update.message.reply_text(
            f"{board}\n"
            f"Attempts left: {remaining}\n"
            f"🔥 Current streak: {users[user_id]['wordle_stats']['current_streak']}"
        )
# ─────────────────────────────────────────
# WSTATS
# ─────────────────────────────────────────

async def wstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user
        user_id = str(target.id)
        name = target.first_name
    else:
        user_id = str(update.effective_user.id)
        name = update.effective_user.first_name

    if user_id not in users:
        await update.message.reply_text("This player has no stats yet!")
        return

    ensure_user(user_id, update if user_id == str(update.effective_user.id) else None)
    stats = users[user_id]["wordle_stats"]

    played = stats.get("played", 0)
    won = stats.get("won", 0)
    lost = stats.get("lost", 0)
    total_guesses = stats.get("total_guesses", 0)
    best_win = stats.get("best_win", 0)
    coins_earned = stats.get("coins_earned", 0)
    current_streak = stats.get("current_streak", 0)
    best_streak = stats.get("best_streak", 0)

    win_rate = round((won / played) * 100) if played > 0 else 0
    avg_guesses = round(total_guesses / won, 1) if won > 0 else 0

    await update.message.reply_text(
        f"📊 Wordle Stats — {name}\n\n"
        f"🎮 Games Played: {played}\n"
        f"🏆 Games Won: {won}\n"
        f"❌ Games Lost: {lost}\n"
        f"🎯 Win Rate: {win_rate}%\n"
        f"🔥 Current Streak: {current_streak}\n"
        f"⭐ Best Streak: {best_streak}\n"
        f"⚡ Avg Guesses: {avg_guesses}\n"
        f"🏅 Best Win: {best_win} guess(es)\n"
        f"💰 Total Coins Earned: {coins_earned}"
    )

# ─────────────────────────────────────────
# LEADERBOARDS
# ─────────────────────────────────────────

def build_wtop_text(period):
    today = datetime.now().strftime("%Y-%m-%d")
    week = datetime.now().strftime("%Y-W%W")

    def get_wins(uid):
        udata = users[uid]
        wins = udata.get("wordle_wins", {})
        last = udata.get("wordle_last_date", {})
        if not isinstance(last, dict):
            return 0
        if period == "today":
            if last.get("today") != today: return 0
            return wins.get("today", 0)
        elif period == "week":
            if last.get("week") != week: return 0
            return wins.get("week", 0)
        elif period == "alltime":
            return wins.get("alltime", 0)
        return 0

    sorted_users = sorted(users.items(), key=lambda x: get_wins(x[0]), reverse=True)
    top = [(uid, d, get_wins(uid)) for uid, d in sorted_users if get_wins(uid) > 0][:10]
    labels = {"today": "TODAY", "week": "THIS WEEK", "alltime": "ALL TIME"}
    text = f"<b>🏆 WORDLE LEADERBOARD [{labels[period]}]</b>\n\n"
    if not top:
        text += "No wins yet!"
        return text
    for i, (uid, d, wins) in enumerate(top, 1):
        name = d.get("name") or "Unknown"
        username = d.get("username", "")
        display = f"@{username}" if username else name
        text += f"{i}. {display} — {wins} wins\n"
    return text

async def wtop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📅 Today", callback_data="wtop_today"),
         InlineKeyboardButton("📆 This Week", callback_data="wtop_week")],
        [InlineKeyboardButton("🏆 All Time", callback_data="wtop_alltime")],
        [InlineKeyboardButton("❌ Back", callback_data="wtop_back")]
    ]
    await update.message.reply_text(
        build_wtop_text("today"),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def wtop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "wtop_back":
        await query.message.delete()
        return
    period = {"wtop_today": "today", "wtop_week": "week", "wtop_alltime": "alltime"}.get(query.data, "today")
    keyboard = [
        [InlineKeyboardButton("📅 Today", callback_data="wtop_today"),
         InlineKeyboardButton("📆 This Week", callback_data="wtop_week")],
        [InlineKeyboardButton("🏆 All Time", callback_data="wtop_alltime")],
        [InlineKeyboardButton("❌ Back", callback_data="wtop_back")]
    ]
    await query.edit_message_text(build_wtop_text(period), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

def build_ctop_text(period):
    labels = {"today": "TODAY", "week": "THIS WEEK", "alltime": "ALL TIME"}
    sorted_users = sorted(users.items(), key=lambda x: x[1].get("coins", 0), reverse=True)
    top = [(uid, d, d.get("coins", 0)) for uid, d in sorted_users][:10]
    text = f"<b>💰 COIN LEADERBOARD [{labels[period]}]</b>\n\n"
    if not top:
        text += "No players yet!"
        return text
    for i, (uid, d, coins) in enumerate(top, 1):
        name = d.get("name") or "Unknown"
        username = d.get("username", "")
        display = f"@{username}" if username else name
        text += f"{i}. {display} — 💰 {coins}\n"
    return text

async def ctop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📅 Today", callback_data="ctop_today"),
         InlineKeyboardButton("📆 This Week", callback_data="ctop_week")],
        [InlineKeyboardButton("🏆 All Time", callback_data="ctop_alltime")],
        [InlineKeyboardButton("❌ Back", callback_data="ctop_back")]
    ]
    await update.message.reply_text(
        build_ctop_text("alltime"),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def ctop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "ctop_back":
        await query.message.delete()
        return
    period = {"ctop_today": "today", "ctop_week": "week", "ctop_alltime": "alltime"}.get(query.data, "alltime")
    keyboard = [
        [InlineKeyboardButton("📅 Today", callback_data="ctop_today"),
         InlineKeyboardButton("📆 This Week", callback_data="ctop_week")],
        [InlineKeyboardButton("🏆 All Time", callback_data="ctop_alltime")],
        [InlineKeyboardButton("❌ Back", callback_data="ctop_back")]
    ]
    await query.edit_message_text(build_ctop_text(period), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def bonus_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    ensure_user(user_id)

    now = time.time()

    # DAILY
    if query.data == "bonus_daily":
        if user_id in last_daily:
            remaining = 86400 - (now - last_daily[user_id])
            if remaining > 0:
                await query.answer(
                    f"❌ Already claimed!\n⏳ {format_time(remaining)} left",
                    show_alert=True
                )
                return

        users[user_id]["coins"] += 75000
        last_daily[user_id] = now
        save_data()
        await query.answer("💰 +75,000 coins!", show_alert=True)

    # WEEKLY
    elif query.data == "bonus_weekly":
        if user_id in last_weekly:
            remaining = 604800 - (now - last_weekly[user_id])
            if remaining > 0:
                await query.answer(
                    f"❌ Already claimed!\n⏳ {format_time(remaining)} left",
                    show_alert=True
                )
                return

        users[user_id]["coins"] += 625000
        last_weekly[user_id] = now
        save_data()
        await query.answer("📅 +6,25,000 coins!", show_alert=True)

    # REFRESH UI
    name = html.escape(query.from_user.first_name)

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

    keyboard = [
        [
            InlineKeyboardButton(daily_text, callback_data="bonus_daily"),
            InlineKeyboardButton(weekly_text, callback_data="bonus_weekly")
        ]
    ]

    await query.edit_message_text(
        f"🎁 <b>Bonus Panel</b>\n\n👤 {name}\n\nChoose your reward:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
async def collection_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)

    if query.data == "col_next":
        context.user_data["col_page"] += 1
    elif query.data == "col_prev":
        context.user_data["col_page"] -= 1
    elif query.data == "col_close":
        await query.message.delete()
        return

    total = len(context.user_data.get("col_list", []))
    max_page = (total - 1) // ITEMS_PER_PAGE

    context.user_data["col_page"] = max(0, min(context.user_data["col_page"], max_page))

    await query.message.delete()
    await send_collection_page(query.message.chat_id, context, user_id)
# ─────────────────────────────────────────
# WSPY
# ─────────────────────────────────────────

async def wspy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if str(user_id) != str(OWNER_ID):
        await update.message.reply_text("Only owner can use this ❌")
        return
    if not active_wordle:
        await update.message.reply_text("No active wordle games right now!")
        return
    text = "👀 Active Wordle Games:\n\n"
    for i, (uid, game) in enumerate(active_wordle.items(), 1):
        udata = users.get(uid, {})
        name = udata.get("name", "Unknown")
        username = udata.get("username", "")
        display = f"@{username}" if username else name
        answer = game["answer"].upper()
        guesses = game["guesses"]
        guesses_text = ", ".join([g.upper() for g, e in guesses]) if guesses else "None yet"
        attempts_left = 6 - len(guesses)
        text += (
            f"{i}. {display}\n"
            f"   🎯 Answer: {answer}\n"
            f"   📝 Guesses: {guesses_text}\n"
            f"   ⏳ Attempts left: {attempts_left}\n\n"
        )
    await update.message.reply_text(text)

# ─────────────────────────────────────────
# COLLECTION
# ─────────────────────────────────────────

async def mycards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)

    user_data = users[user_id]
    collection = user_data.get("characters", [])

    if not collection:
        await update.message.reply_text("No characters yet 😢")
        return

    query = " ".join(context.args).lower() if context.args else ""

    # 🔍 Filter by name
    if query and query not in ["rare", "epic", "legendary", "common"]:
        collection = [
            c for c in collection
            if isinstance(c, dict) and query in c.get("name", "").lower()
        ]

    # ⭐ Filter by rarity
    if query in ["common", "rare", "epic", "legendary"]:
        rarity_filter = {"common": 1, "rare": 2, "epic": 3, "legendary": 4}
        r = rarity_filter[query]
        collection = [
            c for c in collection
            if isinstance(c, dict) and c.get("rarity") == r
        ]

    if not collection:
        await update.message.reply_text("No matching characters found 😢")
        return

    # ✅ Store in user_data for pagination
    context.user_data["col_list"] = collection
    context.user_data["col_page"] = 0

    await send_collection_page(update.effective_chat.id, context, user_id)

# ─────────────────────────────────────────
# PROFILE
# ─────────────────────────────────────────

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)
    user_data = users[user_id]
    coins = user_data.get("coins", 0)
    collection = user_data.get("characters", [])
    joined = user_data.get("joined", time.time())
    age_weeks = int((time.time() - joined) // (7 * 24 * 60 * 60))
    stats = user_data.get("wordle_stats", {})
    current_streak = stats.get("current_streak", 0)
    await update.message.reply_text(
        f"👤 Profile\n\n"
        f"🏷 Name: {update.effective_user.first_name}\n"
        f"💰 Coins: {coins}\n"
        f"🎴 Cards: {len(collection)}\n"
        f"🔥 Wordle Streak: {current_streak}\n"
        f"📅 Age: {age_weeks} weeks"
    )

# ─────────────────────────────────────────
# DAILY
# ─────────────────────────────────────────

async def bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)

    name = html.escape(update.effective_user.first_name)
    now = time.time()

    # DAILY STATUS
    daily_text = "💰 Daily (75,000)"
    if user_id in last_daily:
        remaining = 86400 - (now - last_daily[user_id])
        if remaining > 0:
            daily_text = f"❌ Daily ({format_time(remaining)})"

    # WEEKLY STATUS
    weekly_text = "📅 Weekly (6,25,000)"
    if user_id in last_weekly:
        remaining = 604800 - (now - last_weekly[user_id])
        if remaining > 0:
            weekly_text = f"❌ Weekly ({format_time(remaining)})"

    keyboard = [
        [
            InlineKeyboardButton(daily_text, callback_data="bonus_daily"),
            InlineKeyboardButton(weekly_text, callback_data="bonus_weekly")
        ]
    ]

    await update.message.reply_text(
        f"🎁 <b>Bonus Panel</b>\n\n👤 {name}\n\nChoose your reward:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ─────────────────────────────────────────
# LEADERBOARD (simple)
# ─────────────────────────────────────────

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sorted_users = sorted(users.items(), key=lambda x: x[1]["coins"], reverse=True)
    text = "🏆 Leaderboard\n\n"
    for i, (uid, data) in enumerate(sorted_users[:10], 1):
        name = data.get("name") or "Unknown"
        username = data.get("username", "")
        display = f"@{username}" if username else name
        text += f"{i}. {display} — 💰 {data['coins']}\n"
    await update.message.reply_text(text)

# ─────────────────────────────────────────
# UPLOADER MANAGEMENT
# ─────────────────────────────────────────

async def adduploader(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(OWNER_ID):
        await update.message.reply_text("Only owner can do this ❌")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user to give permission.")
        return
    target_user = update.message.reply_to_message.from_user
    uploaders.add(str(target_user.id))
    save_data()
    await update.message.reply_text(f"Uploader permission given to {target_user.first_name} ✅")

async def removeuploader(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(OWNER_ID):
        await update.message.reply_text("Only owner can do this ❌")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user to remove permission.")
        return
    target_user = update.message.reply_to_message.from_user
    target_id = str(target_user.id)
    if target_id in uploaders:
        uploaders.remove(target_id)
        save_data()
        await update.message.reply_text(f"Removed uploader: {target_user.first_name} ❌")
    else:
        await update.message.reply_text("User is not an uploader.")

async def uploaders_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(OWNER_ID):
        await update.message.reply_text("Only owner can view this ❌")
        return
    if not uploaders:
        await update.message.reply_text("No uploaders yet.")
        return
    text = "📤 Uploaders List:\n\n"
    for uid in uploaders:
        text += f"• {uid}\n"
    await update.message.reply_text(text)

# ─────────────────────────────────────────
# UPLOAD (with auto card ID)
# ─────────────────────────────────────────

async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    if user_id not in uploaders and user_id != str(OWNER_ID):
        await update.message.reply_text("❌ You are not an uploader")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a photo with the command")
        return

    photo_msg = update.message.reply_to_message

    if not photo_msg.photo:
        await update.message.reply_text("Reply to an image ❌")
        return

    try:
        data = " ".join(context.args)
        parts = data.split("|")
        if len(parts) < 3:
            raise ValueError
        # Join any extra pipes back into anime name (in case anime has | in it)
        name = parts[0].strip()
        rarity = int(parts[-1].strip())
        anime = "|".join(parts[1:-1]).strip()
        if not name or not anime:
            raise ValueError
        if rarity not in (1, 2, 3, 4, 5):
            raise ValueError
    except:
        await update.message.reply_text(
            "Format:\n/upload Name | Anime | 1/2/3/4/5\n\nRarity: 1=Common 2=Rare 3=Epic 4=Legendary 5=Celebrity"
        )
        return

    file_id = photo_msg.photo[-1].file_id
    card_id = next_card_id()

    characters.append({
        "card_id": card_id,
        "name": name,
        "anime": anime,
        "rarity": rarity,
        "file_id": file_id
    })

    save_data()

    rl = rarity_label(rarity)
    await update.message.reply_text(
        f"✅ <b>Uploaded!</b>\n\n"
        f"🪪 Card ID: <code>#{card_id}</code>\n"
        f"🎴 Name: {html.escape(name)}\n"
        f"🎬 Anime: {html.escape(anime)}\n"
        f"⭐ Rarity: {rl}",
        parse_mode="HTML"
    )

    # ── Send to log channel ──
    uploader_name = html.escape(update.effective_user.first_name)
    uploader_username = update.effective_user.username
    uploader_display = f"@{uploader_username}" if uploader_username else uploader_name

    log_caption = (
        f"📥 <b>New Character Uploaded!</b>\n\n"
        f"🪪 <b>Card ID:</b> #{card_id}\n"
        f"🎴 <b>Name:</b> {html.escape(name)}\n"
        f"🎬 <b>Anime:</b> {html.escape(anime)}\n"
        f"⭐ <b>Rarity:</b> {rl}\n\n"
        f"👤 <b>Uploaded by:</b> {uploader_display}"
    )

    try:
        await context.bot.send_photo(
            chat_id=LOG_CHANNEL_ID,
            photo=file_id,
            caption=log_caption,
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Log channel error: {e}")
#give

async def give(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_id = str(update.effective_user.id)

    # 🔒 Only OWNER can use
    if sender_id != str(OWNER_ID):
        await update.message.reply_text("❌ Only owner can use this")
        return

    # 📩 Must reply to someone
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to a user to give characters.")
        return

    # 🔢 Check amount
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /give <amount>")
        return

    amount = int(context.args[0])
    if amount <= 0:
        await update.message.reply_text("Amount must be > 0")
        return

    target = update.message.reply_to_message.from_user
    target_id = str(target.id)

    ensure_user(target_id, update)

    if not characters:
        await update.message.reply_text("No characters available ❌")
        return

    given = []

    for _ in range(amount):
        char = random.choice(characters)
        users[target_id]["characters"].append(char)
        given.append(char.get("name", "?"))

    save_data()

    name = html.escape(target.first_name)
    preview = ", ".join(given[:5])  # show first 5 only

    await update.message.reply_text(
        f"🎁 Gave {amount} characters to {name}!\n"
        f"Preview: {preview}{'...' if len(given) > 5 else ''}"
    )

# ─────────────────────────────────────────
# SHOP
# ─────────────────────────────────────────

SHOP_PRICES = {1: 50000, 2: 100000, 3: 200000, 4: 400000, 5: 1000000}
SHOP_PAGE_SIZE = 5

def build_shop_page(page, rarity_filter=None):
    """Return (items, total_pages) for the given page and optional rarity filter."""
    pool = [c for c in characters if isinstance(c, dict)]
    if rarity_filter:
        pool = [c for c in pool if c.get("rarity") == rarity_filter]
    total_pages = max(1, (len(pool) - 1) // SHOP_PAGE_SIZE + 1)
    page = max(0, min(page, total_pages - 1))
    start = page * SHOP_PAGE_SIZE
    return pool[start:start + SHOP_PAGE_SIZE], total_pages, page

async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)

    if not characters:
        await update.message.reply_text("🏪 The shop is empty right now! Come back later.")
        return

    context.user_data["shop_page"] = 0
    context.user_data["shop_filter"] = None

    await send_shop_page(update.effective_chat.id, context, user_id, edit_msg=None)

async def send_shop_page(chat_id, context, user_id, edit_msg=None):
    page = context.user_data.get("shop_page", 0)
    rarity_filter = context.user_data.get("shop_filter", None)

    items, total_pages, page = build_shop_page(page, rarity_filter)
    context.user_data["shop_page"] = page

    coins = users[user_id].get("coins", 0)
    rarity_map = {1: "⚪ Common", 2: "🔵 Rare", 3: "🟣 Epic", 4: "🟡 Legendary", 5: "⚡ Celebrity"}
    filter_label = rarity_map.get(rarity_filter, "All") if rarity_filter else "All"

    text = (
        f"🏪 <b>CHARACTER SHOP</b>\n"
        f"💰 Your coins: <b>{coins}</b>\n"
        f"📂 Filter: {filter_label} | Page {page + 1}/{total_pages}\n\n"
    )

    if not items:
        text += "No characters found for this filter."
        keyboard = [[InlineKeyboardButton("❌ Close", callback_data="shop_close")]]
    else:
        for i, char in enumerate(items):
            rarity = char.get("rarity", 1)
            price = SHOP_PRICES[rarity]
            rl = rarity_map.get(rarity, "?")
            c_name = html.escape(char.get("name", "?"))
            c_anime = html.escape(char.get("anime", "?"))
            card_id = char.get("card_id", "????")
            affordable = "✅" if coins >= price else "❌"
            text += (
                f"{affordable} <b>{c_name}</b>\n"
                f"   🎬 {c_anime}\n"
                f"   ⭐ {rl} | 🪪 #{card_id}\n"
                f"   💰 Price: {price:,} coins\n\n"
            )

        # Buy buttons for each item on the page
        buy_buttons = []
        for i, char in enumerate(items):
            price = SHOP_PRICES[char.get("rarity", 1)]
            label = f"Buy {char.get('name','?')[:12]} ({price:,})"
            buy_buttons.append([InlineKeyboardButton(label, callback_data=f"shop_buy_{char.get('card_id','')}")])

        # Navigation row
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data="shop_prev"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("➡️ Next", callback_data="shop_next"))

        # Filter row
        filter_row = [
            InlineKeyboardButton("⚪", callback_data="shop_f1"),
            InlineKeyboardButton("🔵", callback_data="shop_f2"),
            InlineKeyboardButton("🟣", callback_data="shop_f3"),
            InlineKeyboardButton("🟡", callback_data="shop_f4"),
            InlineKeyboardButton("⚡", callback_data="shop_f5"),
            InlineKeyboardButton("🔄 All", callback_data="shop_fall"),
        ]

        keyboard = buy_buttons
        if nav_row:
            keyboard.append(nav_row)
        keyboard.append(filter_row)
        keyboard.append([InlineKeyboardButton("❌ Close", callback_data="shop_close")])

    markup = InlineKeyboardMarkup(keyboard)

    if edit_msg:
        try:
            await edit_msg.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            pass
    else:
        # Send first item's photo as cover if available
        cover = items[0].get("file_id") if items else None
        if cover:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=cover,
                caption=text,
                parse_mode="HTML",
                reply_markup=markup
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=markup
            )

async def shop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    ensure_user(user_id)
    data = query.data

    if data == "shop_close":
        await query.message.delete()
        return

    elif data == "shop_next":
        context.user_data["shop_page"] = context.user_data.get("shop_page", 0) + 1

    elif data == "shop_prev":
        context.user_data["shop_page"] = max(0, context.user_data.get("shop_page", 0) - 1)

    elif data == "shop_fall":
        context.user_data["shop_filter"] = None
        context.user_data["shop_page"] = 0

    elif data.startswith("shop_f"):
        rarity = int(data[6])
        context.user_data["shop_filter"] = rarity
        context.user_data["shop_page"] = 0

    elif data.startswith("shop_buy_"):
        card_id = data[len("shop_buy_"):]
        char = next((c for c in characters if str(c.get("card_id")) == str(card_id)), None)

        if not char:
            await query.answer("❌ Character not found!", show_alert=True)
            return

        price = SHOP_PRICES[char.get("rarity", 1)]
        coins = users[user_id].get("coins", 0)

        if coins < price:
            await query.answer(
                f"❌ Not enough coins!\nYou need {price:,} but have {coins:,}.",
                show_alert=True
            )
            return

        users[user_id]["coins"] -= price
        users[user_id]["characters"].append(char)
        save_data()

        c_name = char.get("name", "?")
        await query.answer(
            f"✅ Purchased {c_name} for {price:,} coins!",
            show_alert=True
        )

    # Refresh the shop page
    await send_shop_page(query.message.chat_id, context, user_id, edit_msg=query.message)

# ─────────────────────────────────────────
# FAVOURITE
# ─────────────────────────────────────────

async def favourite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)

    if not context.args:
        await update.message.reply_text(
            "⭐ <b>Favourite a Card</b>\n\n"
            "Usage: /favourite [card_id]\n"
            "Example: /favourite 0023\n\n"
            "Favourited cards are marked with ⭐ in your collection and cannot be sold.",
            parse_mode="HTML"
        )
        return

    card_id = context.args[0].strip().lstrip("#").zfill(4)
    collection = users[user_id].get("characters", [])

    # Find the card in user's collection
    card = next((c for c in collection if str(c.get("card_id", "")).zfill(4) == card_id), None)

    if not card:
        await update.message.reply_text(f"❌ Card #{card_id} not found in your collection.")
        return

    # Toggle favourite
    if card.get("favourite"):
        card["favourite"] = False
        save_data()
        await update.message.reply_text(
            f"💔 <b>{html.escape(card.get('name','?'))}</b> removed from favourites.",
            parse_mode="HTML"
        )
    else:
        card["favourite"] = True
        save_data()
        await update.message.reply_text(
            f"⭐ <b>{html.escape(card.get('name','?'))}</b> added to favourites!",
            parse_mode="HTML"
        )

async def myfavourites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)

    favs = [c for c in users[user_id].get("characters", []) if isinstance(c, dict) and c.get("favourite")]

    if not favs:
        await update.message.reply_text("You have no favourited cards yet!\nUse /favourite [card_id] to mark one.")
        return

    rarity_map = {1: "⚪", 2: "🔵", 3: "🟣", 4: "🟡", 5: "⚡"}
    text = f"⭐ <b>Your Favourite Cards ({len(favs)})</b>\n\n"
    for c in favs:
        rl = rarity_map.get(c.get("rarity", 1), "?")
        text += f"{rl} <b>{html.escape(c.get('name','?'))}</b> — #{c.get('card_id','????')} [{html.escape(c.get('anime','?'))}]\n"

    await update.message.reply_text(text, parse_mode="HTML")

# ─────────────────────────────────────────
# CARDINFO
# ─────────────────────────────────────────

async def cardinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "🔍 Usage: /cardinfo [card_id]\nExample: /cardinfo 0012"
        )
        return

    card_id = context.args[0].strip().lstrip("#").zfill(4)

    # Search in global characters list
    char = next((c for c in characters if str(c.get("card_id", "")).zfill(4) == card_id), None)

    if not char:
        await update.message.reply_text(f"❌ No card found with ID #{card_id}.")
        return

    rarity_map = {1: "⚪ Common", 2: "🔵 Rare", 3: "🟣 Epic", 4: "🟡 Legendary", 5: "⚡ Celebrity"}
    rl = rarity_map.get(char.get("rarity", 1), "Unknown")
    c_name = html.escape(char.get("name", "?"))
    c_anime = html.escape(char.get("anime", "?"))
    price = SHOP_PRICES.get(char.get("rarity", 1), 0)

    # Count how many players own this card
    owners = sum(
        1 for uid, udata in users.items()
        if any(str(c.get("card_id","")) == card_id for c in udata.get("characters", []) if isinstance(c, dict))
    )

    caption = (
        f"🎴 <b>Card Info</b>\n\n"
        f"🪪 <b>Card ID:</b> #{card_id}\n"
        f"👤 <b>Name:</b> {c_name}\n"
        f"🎬 <b>Anime:</b> {c_anime}\n"
        f"⭐ <b>Rarity:</b> {rl}\n"
        f"💰 <b>Shop Price:</b> {price:,} coins\n"
        f"👥 <b>Owners:</b> {owners} player(s)"
    )

    try:
        await update.message.reply_photo(
            photo=char["file_id"],
            caption=caption,
            parse_mode="HTML"
        )
    except Exception:
        await update.message.reply_text(caption, parse_mode="HTML")

# ─────────────────────────────────────────
# DELETECARD (Owner only)
# ─────────────────────────────────────────

async def deletecard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(OWNER_ID):
        await update.message.reply_text("❌ Only owner can use this.")
        return

    if not context.args:
        await update.message.reply_text(
            "🗑 Usage: /deletecard [card_id]\nExample: /deletecard 0012\n\n"
            "⚠️ This removes the card from the game AND all player collections."
        )
        return

    card_id = context.args[0].strip().lstrip("#").zfill(4)

    # Remove from global characters list
    before = len(characters)
    characters[:] = [c for c in characters if str(c.get("card_id", "")).zfill(4) != card_id]

    if len(characters) == before:
        await update.message.reply_text(f"❌ Card #{card_id} not found in the game.")
        return

    # Remove from all player collections
    removed_from = 0
    for uid, udata in users.items():
        original = len(udata.get("characters", []))
        udata["characters"] = [
            c for c in udata.get("characters", [])
            if not (isinstance(c, dict) and str(c.get("card_id", "")).zfill(4) == card_id)
        ]
        if len(udata["characters"]) < original:
            removed_from += 1

    save_data()
    await update.message.reply_text(
        f"🗑 <b>Card #{card_id} deleted!</b>\n"
        f"Removed from {removed_from} player collection(s).",
        parse_mode="HTML"
    )

# ─────────────────────────────────────────
# EDITCARD (Owner only)
# ─────────────────────────────────────────

async def editcard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id != str(OWNER_ID) and user_id not in uploaders:
        await update.message.reply_text("❌ Only owner or uploaders can use this.")
        return

    if not context.args:
        await update.message.reply_text(
            "✏️ <b>Edit a Card</b>\n\n"
            "Usage:\n"
            "<code>/editcard [card_id] name [new name]</code>\n"
            "<code>/editcard [card_id] anime [new anime]</code>\n"
            "<code>/editcard [card_id] rarity [1-5]</code>\n\n"
            "Example:\n"
            "<code>/editcard 0012 name Naruto Uzumaki</code>\n"
            "<code>/editcard 0012 rarity 4</code>",
            parse_mode="HTML"
        )
        return

    if len(context.args) < 3:
        await update.message.reply_text("❌ Not enough arguments.\nUsage: /editcard [card_id] [field] [value]")
        return

    card_id = context.args[0].strip().lstrip("#").zfill(4)
    field = context.args[1].strip().lower()
    value = " ".join(context.args[2:]).strip()

    char = next((c for c in characters if str(c.get("card_id", "")).zfill(4) == card_id), None)

    if not char:
        await update.message.reply_text(f"❌ Card #{card_id} not found.")
        return

    if field == "name":
        old = char["name"]
        char["name"] = value
        # Update in all player collections too
        for udata in users.values():
            for c in udata.get("characters", []):
                if isinstance(c, dict) and str(c.get("card_id","")).zfill(4) == card_id:
                    c["name"] = value
        save_data()
        await update.message.reply_text(
            f"✅ Card #{card_id} name updated!\n{html.escape(old)} → {html.escape(value)}",
            parse_mode="HTML"
        )

    elif field == "anime":
        old = char["anime"]
        char["anime"] = value
        for udata in users.values():
            for c in udata.get("characters", []):
                if isinstance(c, dict) and str(c.get("card_id","")).zfill(4) == card_id:
                    c["anime"] = value
        save_data()
        await update.message.reply_text(
            f"✅ Card #{card_id} anime updated!\n{html.escape(old)} → {html.escape(value)}",
            parse_mode="HTML"
        )

    elif field == "rarity":
        if not value.isdigit() or int(value) not in (1, 2, 3, 4, 5):
            await update.message.reply_text("❌ Rarity must be 1-5.\n1=Common 2=Rare 3=Epic 4=Legendary 5=Celebrity")
            return
        old = char["rarity"]
        new_rarity = int(value)
        char["rarity"] = new_rarity
        for udata in users.values():
            for c in udata.get("characters", []):
                if isinstance(c, dict) and str(c.get("card_id","")).zfill(4) == card_id:
                    c["rarity"] = new_rarity
        save_data()
        rarity_map = {1: "Common", 2: "Rare", 3: "Epic", 4: "Legendary", 5: "Celebrity"}
        await update.message.reply_text(
            f"✅ Card #{card_id} rarity updated!\n{rarity_map[old]} → {rarity_map[new_rarity]}",
            parse_mode="HTML"
        )

    else:
        await update.message.reply_text("❌ Unknown field. Use: name / anime / rarity")

# ─────────────────────────────────────────
# BROADCAST (Owner only)
# ─────────────────────────────────────────

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(OWNER_ID):
        await update.message.reply_text("❌ Only owner can use this.")
        return

    if not context.args:
        await update.message.reply_text(
            "📢 Usage: /broadcast [message]\n"
            "Example: /broadcast Hello everyone! New update is live!"
        )
        return

    message = " ".join(context.args)
    text = f"📢 <b>Announcement</b>\n\n{html.escape(message)}"

    sent = 0
    failed = 0

    await update.message.reply_text(f"📤 Sending to {len(users)} users...")

    for uid in users:
        try:
            await context.bot.send_message(chat_id=int(uid), text=text, parse_mode="HTML")
            sent += 1
            await asyncio.sleep(0.05)  # avoid hitting Telegram rate limits
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"✅ <b>Broadcast Done!</b>\n\n"
        f"📨 Sent: {sent}\n"
        f"❌ Failed: {failed}",
        parse_mode="HTML"
    )

# ─────────────────────────────────────────
# REMOVECARDS (Owner only)
# ─────────────────────────────────────────

async def removecards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner removes specific cards or all cards from a user's collection."""
    if str(update.effective_user.id) != str(OWNER_ID):
        await update.message.reply_text("❌ Only owner can use this.")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text(
            "🗑 <b>Remove Cards from a Player</b>\n\n"
            "Reply to the player and use:\n\n"
            "<code>/removecards all</code> — remove entire collection\n"
            "<code>/removecards [card_id]</code> — remove one specific card\n"
            "<code>/removecards [card_id] [amount]</code> — remove multiple copies\n\n"
            "Examples:\n"
            "<code>/removecards all</code>\n"
            "<code>/removecards 0023</code>\n"
            "<code>/removecards 0023 3</code>",
            parse_mode="HTML"
        )
        return

    if not context.args:
        await update.message.reply_text("❌ Specify what to remove.\nUsage: /removecards all OR /removecards [card_id] [amount]")
        return

    target_user = update.message.reply_to_message.from_user
    target_id = str(target_user.id)
    target_name = html.escape(target_user.first_name)

    ensure_user(target_id)
    collection = users[target_id].get("characters", [])

    if not collection:
        await update.message.reply_text(f"❌ {target_name} has no cards in their collection.")
        return

    arg = context.args[0].strip().lower()

    # ── Remove ALL ──
    if arg == "all":
        total = len(collection)
        users[target_id]["characters"] = []
        save_data()
        await update.message.reply_text(
            f"🗑 <b>Done!</b>\n\n"
            f"Removed all <b>{total}</b> card(s) from {target_name}'s collection.",
            parse_mode="HTML"
        )
        return

    # ── Remove by card_id ──
    card_id = arg.lstrip("#").zfill(4)
    amount = 1
    if len(context.args) >= 2 and context.args[1].isdigit():
        amount = max(1, int(context.args[1]))

    removed = 0
    new_collection = []
    for c in collection:
        if (
            isinstance(c, dict)
            and str(c.get("card_id", "")).zfill(4) == card_id
            and removed < amount
        ):
            removed += 1  # skip this one (remove it)
        else:
            new_collection.append(c)

    if removed == 0:
        await update.message.reply_text(f"❌ Card #{card_id} not found in {target_name}'s collection.")
        return

    users[target_id]["characters"] = new_collection
    save_data()

    # Get card name for display
    card_name = "Unknown"
    for c in collection:
        if isinstance(c, dict) and str(c.get("card_id", "")).zfill(4) == card_id:
            card_name = c.get("name", "Unknown")
            break

    await update.message.reply_text(
        f"🗑 <b>Done!</b>\n\n"
        f"Removed <b>{removed}x {html.escape(card_name)}</b> (#{card_id})\n"
        f"from {target_name}'s collection.",
        parse_mode="HTML"
    )



import uuid

async def trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 1: Sender initiates a trade by offering a card."""
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)

    # Must reply to the target user
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "🔄 <b>How to Trade:</b>\n\n"
            "Reply to the person you want to trade with and use:\n"
            "<code>/trade [your_card_id]</code>\n\n"
            "Example: Reply to someone and type:\n"
            "<code>/trade 0023</code>",
            parse_mode="HTML"
        )
        return

    target_user = update.message.reply_to_message.from_user

    if target_user.is_bot:
        await update.message.reply_text("❌ You can't trade with a bot!")
        return

    target_id = str(target_user.id)

    if target_id == user_id:
        await update.message.reply_text("❌ You can't trade with yourself!")
        return

    if not context.args:
        await update.message.reply_text("❌ Please provide your card ID.\nUsage: /trade [card_id]")
        return

    offer_card_id = context.args[0].strip().lstrip("#").zfill(4)
    sender_collection = users[user_id].get("characters", [])

    # Find offered card in sender's collection
    offer_card = next(
        (c for c in sender_collection if isinstance(c, dict) and str(c.get("card_id","")).zfill(4) == offer_card_id),
        None
    )

    if not offer_card:
        await update.message.reply_text(f"❌ You don't have card #{offer_card_id} in your collection.")
        return

    if offer_card.get("favourite"):
        await update.message.reply_text("❌ You can't trade a favourited card!\nUse /favourite to unfavourite it first.")
        return

    # Check target user exists
    ensure_user(target_id)

    # Check if either user already has a pending trade
    for tid, tsession in active_trades.items():
        if tsession.get("sender_id") == user_id or tsession.get("receiver_id") == user_id:
            await update.message.reply_text("❌ You already have a pending trade! Cancel it first with /canceltrade")
            return
        if tsession.get("sender_id") == target_id or tsession.get("receiver_id") == target_id:
            await update.message.reply_text("❌ That user already has a pending trade!")
            return

    # Create trade session
    trade_id = str(uuid.uuid4())[:8]
    active_trades[trade_id] = {
        "sender_id": user_id,
        "receiver_id": target_id,
        "offer_card": offer_card,
        "want_card": None,
        "sender_confirmed": False,
        "receiver_confirmed": False,
        "created": time.time(),
        "chat_id": update.effective_chat.id
    }

    rarity_map = {1: "⚪ Common", 2: "🔵 Rare", 3: "🟣 Epic", 4: "🟡 Legendary", 5: "⚡ Celebrity"}
    rl = rarity_map.get(offer_card.get("rarity", 1), "?")
    sender_name = html.escape(update.effective_user.first_name)
    target_name = html.escape(target_user.first_name)

    # Notify the group
    await update.message.reply_text(
        f"🔄 <b>Trade Request Sent!</b>\n\n"
        f"👤 <b>{sender_name}</b> wants to trade with <b>{target_name}</b>\n\n"
        f"📤 <b>Offering:</b>\n"
        f"  🎴 {html.escape(offer_card.get('name','?'))}\n"
        f"  ⭐ {rl} | 🪪 #{offer_card_id}\n\n"
        f"📩 <b>{target_name}</b>, reply with /offer [card_id] to make your offer!\n"
        f"⏳ Trade expires in {TRADE_TIMEOUT//60} minutes.\n"
        f"🆔 Trade ID: <code>{trade_id}</code>",
        parse_mode="HTML"
    )

    # Auto-expire the trade
    asyncio.create_task(expire_trade(trade_id, context))


async def offer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 2: Receiver picks their card to offer back."""
    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)

    # Find the trade where this user is the receiver
    trade_id, session = next(
        ((tid, s) for tid, s in active_trades.items() if s["receiver_id"] == user_id),
        (None, None)
    )

    if not session:
        await update.message.reply_text("❌ You have no incoming trade request right now.")
        return

    if not context.args:
        await update.message.reply_text("❌ Usage: /offer [card_id]\nExample: /offer 0045")
        return

    want_card_id = context.args[0].strip().lstrip("#").zfill(4)
    receiver_collection = users[user_id].get("characters", [])

    want_card = next(
        (c for c in receiver_collection if isinstance(c, dict) and str(c.get("card_id","")).zfill(4) == want_card_id),
        None
    )

    if not want_card:
        await update.message.reply_text(f"❌ You don't have card #{want_card_id} in your collection.")
        return

    if want_card.get("favourite"):
        await update.message.reply_text("❌ You can't trade a favourited card!\nUse /favourite to unfavourite it first.")
        return

    session["want_card"] = want_card

    rarity_map = {1: "⚪ Common", 2: "🔵 Rare", 3: "🟣 Epic", 4: "🟡 Legendary", 5: "⚡ Celebrity"}
    offer_card = session["offer_card"]
    rl_offer = rarity_map.get(offer_card.get("rarity", 1), "?")
    rl_want = rarity_map.get(want_card.get("rarity", 1), "?")

    sender_id = session["sender_id"]
    sender_name = html.escape(users[sender_id].get("name", "Unknown"))
    receiver_name = html.escape(update.effective_user.first_name)

    keyboard = [
        [
            InlineKeyboardButton("✅ Accept", callback_data=f"trade_accept_{trade_id}"),
            InlineKeyboardButton("❌ Decline", callback_data=f"trade_decline_{trade_id}")
        ]
    ]

    await update.message.reply_text(
        f"🔄 <b>Trade Offer Ready!</b>\n\n"
        f"👤 <b>{sender_name}</b> offers:\n"
        f"  🎴 {html.escape(offer_card.get('name','?'))} | {rl_offer} | 🪪 #{offer_card.get('card_id','????')}\n\n"
        f"👤 <b>{receiver_name}</b> offers:\n"
        f"  🎴 {html.escape(want_card.get('name','?'))} | {rl_want} | 🪪 #{want_card.get('card_id','????')}\n\n"
        f"Both players must accept to complete the trade!\n"
        f"🆔 Trade ID: <code>{trade_id}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def trade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Accept / Decline buttons."""
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    data = query.data

    if data.startswith("trade_decline_"):
        trade_id = data[len("trade_decline_"):]
        session = active_trades.pop(trade_id, None)
        if session:
            name = html.escape(query.from_user.first_name)
            await query.edit_message_text(f"❌ <b>Trade cancelled by {name}.</b>", parse_mode="HTML")
        return

    if data.startswith("trade_accept_"):
        trade_id = data[len("trade_accept_"):]
        session = active_trades.get(trade_id)

        if not session:
            await query.edit_message_text("❌ This trade has already expired or been completed.")
            return

        if session["want_card"] is None:
            await query.answer("⚠️ The receiver hasn't made an offer yet!", show_alert=True)
            return

        # Only sender or receiver can accept
        if user_id not in (session["sender_id"], session["receiver_id"]):
            await query.answer("❌ This trade is not for you!", show_alert=True)
            return

        # Mark who confirmed
        if user_id == session["sender_id"]:
            session["sender_confirmed"] = True
        elif user_id == session["receiver_id"]:
            session["receiver_confirmed"] = True

        # If both confirmed → execute trade
        if session["sender_confirmed"] and session["receiver_confirmed"]:
            sender_id = session["sender_id"]
            receiver_id = session["receiver_id"]
            offer_card = session["offer_card"]
            want_card = session["want_card"]
            offer_cid = str(offer_card.get("card_id","")).zfill(4)
            want_cid = str(want_card.get("card_id","")).zfill(4)

            # Remove offered card from sender
            sender_col = users[sender_id].get("characters", [])
            removed_sender = False
            for i, c in enumerate(sender_col):
                if isinstance(c, dict) and str(c.get("card_id","")).zfill(4) == offer_cid:
                    sender_col.pop(i)
                    removed_sender = True
                    break

            # Remove want card from receiver
            receiver_col = users[receiver_id].get("characters", [])
            removed_receiver = False
            for i, c in enumerate(receiver_col):
                if isinstance(c, dict) and str(c.get("card_id","")).zfill(4) == want_cid:
                    receiver_col.pop(i)
                    removed_receiver = True
                    break

            if not removed_sender or not removed_receiver:
                active_trades.pop(trade_id, None)
                await query.edit_message_text("❌ Trade failed! One of the cards is no longer available.")
                return

            # Give cards to each other (clear favourite flag)
            offer_card_copy = dict(offer_card)
            offer_card_copy["favourite"] = False
            want_card_copy = dict(want_card)
            want_card_copy["favourite"] = False

            users[receiver_id]["characters"].append(offer_card_copy)
            users[sender_id]["characters"].append(want_card_copy)

            active_trades.pop(trade_id, None)
            save_data()

            sender_name = html.escape(users[sender_id].get("name", "Unknown"))
            receiver_name = html.escape(users[receiver_id].get("name", "Unknown"))

            await query.edit_message_text(
                f"🎉 <b>Trade Complete!</b>\n\n"
                f"👤 <b>{sender_name}</b> received:\n"
                f"  🎴 {html.escape(want_card.get('name','?'))} | 🪪 #{want_cid}\n\n"
                f"👤 <b>{receiver_name}</b> received:\n"
                f"  🎴 {html.escape(offer_card.get('name','?'))} | 🪪 #{offer_cid}\n\n"
                f"✅ Cards have been swapped!",
                parse_mode="HTML"
            )
        else:
            # One person confirmed, waiting for the other
            name = html.escape(query.from_user.first_name)
            await query.edit_message_text(
                query.message.text + f"\n\n✅ <b>{name}</b> accepted! Waiting for the other player...",
                parse_mode="HTML",
                reply_markup=query.message.reply_markup
            )


async def canceltrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel your pending trade."""
    user_id = str(update.effective_user.id)

    trade_id, session = next(
        ((tid, s) for tid, s in active_trades.items()
         if s["sender_id"] == user_id or s["receiver_id"] == user_id),
        (None, None)
    )

    if not session:
        await update.message.reply_text("❌ You have no active trade to cancel.")
        return

    active_trades.pop(trade_id, None)
    await update.message.reply_text("✅ Your trade has been cancelled.")


async def expire_trade(trade_id, context):
    """Auto-expire trade after TRADE_TIMEOUT seconds."""
    await asyncio.sleep(TRADE_TIMEOUT)
    session = active_trades.pop(trade_id, None)
    if session:
        chat_id = session.get("chat_id")
        if chat_id:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⏰ Trade <code>{trade_id}</code> has expired.",
                    parse_mode="HTML"
                )
            except Exception:
                pass

# ─────────────────────────────────────────
# GROUP-ONLY: GUESS GAME
# ─────────────────────────────────────────


async def guess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner starts a number guessing game in the group."""
    if update.effective_chat.id != GROUP_ID:
        await update.message.reply_text("❌ This command only works in the official group!")
        return

    chat_id = update.effective_chat.id

    if chat_id in active_guess:
        await update.message.reply_text("⚠️ A guess game is already running! Someone needs to guess the number first.")
        return

    # Optional prize argument: /guess 5000
    prize = 5000
    if context.args and context.args[0].isdigit():
        prize = max(100, int(context.args[0]))

    answer = random.randint(1, 100)
    active_guess[chat_id] = {
        "answer": answer,
        "prize": prize,
        "started_by": str(update.effective_user.id)
    }

    await update.message.reply_text(
        f"🎯 <b>GUESS THE NUMBER!</b>\n\n"
        f"I'm thinking of a number between <b>1 and 100</b>.\n"
        f"First person to guess it wins <b>{prize:,} coins!</b> 💰\n\n"
        f"Just type your number in the chat!",
        parse_mode="HTML"
    )

async def handle_guess(chat_id, user_id, text, update, context):
    """Called from handle_message when a guess game is active."""
    session = active_guess.get(chat_id)
    if not session:
        return False

    if not text.isdigit():
        return False

    guess_num = int(text)
    if guess_num < 1 or guess_num > 100:
        return False

    answer = session["answer"]
    prize = session["prize"]

    ensure_user(user_id, update)
    name = html.escape(update.effective_user.first_name)

    if guess_num == answer:
        # Winner!
        users[user_id]["coins"] += prize
        active_guess.pop(chat_id, None)
        save_data()
        await update.message.reply_text(
            f"🎉 <b>{name} got it!</b>\n\n"
            f"The number was <b>{answer}</b>!\n"
            f"💰 <b>+{prize:,} coins</b> added to your balance!",
            parse_mode="HTML"
        )
        return True
    elif guess_num < answer:
        await update.message.reply_text(f"📈 <b>{name}</b>: {guess_num} is too low! Try higher.", parse_mode="HTML")
    else:
        await update.message.reply_text(f"📉 <b>{name}</b>: {guess_num} is too high! Try lower.", parse_mode="HTML")

    return True

async def cancelguess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner cancels the current guess game."""
    if update.effective_chat.id != GROUP_ID:
        await update.message.reply_text("❌ This command only works in the official group!")
        return

    if str(update.effective_user.id) != str(OWNER_ID):
        await update.message.reply_text("❌ Only the owner can cancel the guess game!")
        return

    chat_id = update.effective_chat.id
    session = active_guess.pop(chat_id, None)
    if session:
        await update.message.reply_text(
            f"❌ Guess game cancelled!\nThe number was <b>{session['answer']}</b>.",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text("No active guess game right now.")

# ─────────────────────────────────────────
# GROUP-ONLY: DAILY CHECK-IN
# ─────────────────────────────────────────

CHECKIN_BASE_REWARD = 2000   # base coins per checkin
CHECKIN_STREAK_BONUS = 500   # extra coins per streak day

async def checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Daily group check-in for bonus coins, only in the official group."""
    if update.effective_chat.id != GROUP_ID:
        await update.message.reply_text("❌ Check-in only works in the official group!")
        return

    user_id = str(update.effective_user.id)
    ensure_user(user_id, update)

    now = time.time()
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                 .__class__.fromtimestamp(time.time() - 86400).strftime("%Y-%m-%d"))

    last = last_checkin.get(user_id, {})
    last_date = last.get("date", "")

    # Already checked in today
    if last_date == today:
        next_reset = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        remaining = 86400 - (now - last.get("timestamp", now))
        await update.message.reply_text(
            f"⏳ You already checked in today!\n"
            f"Come back in <b>{format_time(int(remaining))}</b>.",
            parse_mode="HTML"
        )
        return

    # Calculate streak
    streak = checkin_streak.get(user_id, 0)
    if last_date == yesterday:
        streak += 1  # continued streak
    else:
        streak = 1   # reset streak

    checkin_streak[user_id] = streak

    # Calculate reward
    bonus = CHECKIN_STREAK_BONUS * (streak - 1)
    total = CHECKIN_BASE_REWARD + bonus

    users[user_id]["coins"] += total
    last_checkin[user_id] = {"date": today, "timestamp": now}
    save_data()

    name = html.escape(update.effective_user.first_name)

    streak_msg = ""
    if streak >= 7:
        streak_msg = f"\n🔥🔥🔥 <b>{streak} day streak!</b> Amazing!"
    elif streak >= 3:
        streak_msg = f"\n🔥 <b>{streak} day streak!</b> Keep it up!"

    await update.message.reply_text(
        f"✅ <b>{name} checked in!</b>\n\n"
        f"💰 Base reward: {CHECKIN_BASE_REWARD:,} coins\n"
        f"⚡ Streak bonus: +{bonus:,} coins\n"
        f"🎁 <b>Total: +{total:,} coins</b>\n"
        f"📅 Streak: {streak} day(s)"
        f"{streak_msg}",
        parse_mode="HTML"
    )

async def checkintop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show top check-in streaks in the group."""
    if update.effective_chat.id != GROUP_ID:
        await update.message.reply_text("❌ This command only works in the official group!")
        return

    if not checkin_streak:
        await update.message.reply_text("No check-in streaks yet!")
        return

    sorted_streaks = sorted(checkin_streak.items(), key=lambda x: x[1], reverse=True)[:10]

    text = "🏆 <b>Check-in Streak Leaderboard</b>\n\n"
    for i, (uid, streak) in enumerate(sorted_streaks, 1):
        udata = users.get(uid, {})
        name = udata.get("name") or "Unknown"
        username = udata.get("username", "")
        display = f"@{username}" if username else name
        text += f"{i}. {html.escape(display)} — 🔥 {streak} days\n"

    await update.message.reply_text(text, parse_mode="HTML")



async def hello(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello 👋")

# ─────────────────────────────────────────
# INIT & RUN
# ─────────────────────────────────────────

load_data()

import os
TOKEN = os.environ.get("BOT_TOKEN", "YOUR_TOKEN_HERE")
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("collection", mycards))
app.add_handler(CommandHandler("hello", hello))
app.add_handler(CommandHandler("profile", profile))
app.add_handler(CommandHandler("adduploader", adduploader))
app.add_handler(CommandHandler("removeuploader", removeuploader))
app.add_handler(CommandHandler("uploaders", uploaders_list))
app.add_handler(CommandHandler("bonus", bonus))
app.add_handler(CallbackQueryHandler(bonus_callback, pattern="^bonus_"))
app.add_handler(CommandHandler("leaderboard", leaderboard))
app.add_handler(CommandHandler("summon", summon))
app.add_handler(CommandHandler("bid", bid))
app.add_handler(CommandHandler("wordle", wordle))
app.add_handler(CommandHandler("cwordle", cwordle))
app.add_handler(CommandHandler("wtop", wtop))
app.add_handler(CommandHandler("wstats", wstats))
app.add_handler(CommandHandler("ctop", ctop))
app.add_handler(CommandHandler("wspy", wspy))
app.add_handler(CommandHandler("shop", shop))
app.add_handler(CallbackQueryHandler(shop_callback, pattern="^shop_"))
app.add_handler(CommandHandler("favourite", favourite))
app.add_handler(CommandHandler("favorites", myfavourites))
app.add_handler(CommandHandler("cardinfo", cardinfo))
app.add_handler(CommandHandler("deletecard", deletecard))
app.add_handler(CommandHandler("editcard", editcard))
app.add_handler(CommandHandler("broadcast", broadcast))
app.add_handler(CommandHandler("trade", trade))
app.add_handler(CommandHandler("offer", offer))
app.add_handler(CommandHandler("canceltrade", canceltrade))
app.add_handler(CallbackQueryHandler(trade_callback, pattern="^trade_"))
app.add_handler(CommandHandler("guess", guess))
app.add_handler(CommandHandler("cancelguess", cancelguess))
app.add_handler(CommandHandler("checkin", checkin))
app.add_handler(CommandHandler("checkintop", checkintop))
app.add_handler(CommandHandler("removecards", removecards))
app.add_handler(CommandHandler("upload", upload))
app.add_handler(CommandHandler("give", give))
app.add_handler(CallbackQueryHandler(wtop_callback, pattern="^wtop_"))
app.add_handler(InlineQueryHandler(inline_query))
app.add_handler(CallbackQueryHandler(ctop_callback, pattern="^ctop_"))
app.add_handler(CallbackQueryHandler(collection_buttons, pattern="^col_"))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

app.run_polling()

# -*- coding: utf-8 -*-
import logging
import random
import string
import re
import json
import os
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Bot, InputMediaPhoto
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError, BadRequest, Forbidden, NetworkError
from collections import defaultdict
import asyncio
import sqlite3
import io

from storage import db
from race import init_race, record_wager, race_command, schedule_race_reset
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageOps

# Import SQLite storage layer
from storage import db
from auto_deposit import setup_deposit_module, get_ton_price_usd
# Import multi-language support
from languages import detect_lang, get_lang_string, SUPPORTED_LANGS
from optimus.i18n import t, translate_text

# Pure helpers extracted to optimus/utils/* and re-imported (call sites unchanged).
from optimus.utils.validation import (
    generate_transaction_id,
    generate_temp_crypto_address,
    is_valid_crypto_address,
    detect_coin_from_address,
    is_valid_ton_address,
)
from optimus.utils.formatting import (
    format_timer,
    format_time_remaining,
    create_progress_bar,
    format_withdrawal_status,
    format_withdrawal_date,
)

# OxaPay crypto payment integration

# Multi-bot network management
from bot_network import (
    network_db, validate_bot_token, ping_bot, detect_db_path_for_token,
    sync_settings_to_bot, crossban_user_on_bot, get_bot_stats,
    get_all_user_ids_from_bot
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Secrets and bot identity are loaded from .env via optimus.config (no hardcoded token).
from optimus.config import BOT_TOKEN, PROVIDER_TOKEN, ADMIN_ID, BOT_USERNAME
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set — add it to your .env file (see .env.example)")

# ---- Economic core: logic lives in core.wallet, operating on a ModuleState view
# of this module's existing globals (single source of truth, zero call-site churn).
import sys as _sys
from core.state import ModuleState
from core.wallet import Wallet
_BANKROLL_FLOOR_USD = 10000.0
_state = ModuleState(_sys.modules[__name__], {
    "stars_to_usd": "STARS_TO_USD",
    "casino_bankroll_usd": "casino_bankroll_usd",
    "bankroll_floor_usd": "_BANKROLL_FLOOR_USD",
    "active_jackpot_stars": "active_jackpot_stars",
    "jackpot_notify_queue": "_jackpot_notify_queue",
    "golden_hour_end_dt": "golden_hour_end_dt",
    "golden_hour_mult_val": "golden_hour_mult_val",
    "bankroll_win_blocked": "_bankroll_win_blocked",
    "admin_list": "admin_list",
    "frozen_users": "frozen_users",
})
wallet = Wallet(_state, db)

# Data file path
BOT_DB = "bot_data.db"  # SQLite database (fresh start)
DATA_FILE = "bot_data.json"  # JSON data file

# Admin management
admin_list = {ADMIN_ID, 8311802199}
ADMIN_BALANCE = 9999999999

# Streaming message feature (admin-controlled)
streaming_enabled = False  # Toggle for message streaming effect


async def apply_streaming(message_obj, text: str, **kwargs):
    """
    Apply streaming effect to a message.
    If streaming_enabled, sends message in 3-5 word chunks with 150ms delays.
    Otherwise sends the full message at once.
    """
    global streaming_enabled
    
    if not streaming_enabled:
        # Normal mode: send full message
        return await message_obj.reply_html(text, **kwargs)
    
    # Streaming mode: send in chunks
    words = text.split()
    if len(words) <= 5:
        # Too short to stream, send as one
        return await message_obj.reply_html(text, **kwargs)
    
    chunk_size_min, chunk_size_max = 3, 5
    delay_sec = 0.15  # 150ms
    
    messages = []
    i = 0
    while i < len(words):
        chunk_size = random.randint(chunk_size_min, min(chunk_size_max, len(words) - i))
        messages.append(" ".join(words[i:i + chunk_size]))
        i += chunk_size
    
    # Send chunks progressively
    for idx, chunk in enumerate(messages):
        try:
            await message_obj.reply_html(chunk)
            if idx < len(messages) - 1:
                await asyncio.sleep(delay_sec)
        except Exception as e:
            logger.error(f"Streaming error: {e}")
            # Fallback: send remaining as one
            remaining = " ".join(messages[idx:])
            return await message_obj.reply_html(remaining, **kwargs)


user_games = {}
mines_games = {}  # user_id -> MinesGame instance
user_balances = defaultdict(float)  # Kept for backward compatibility, but data is in DB
user_crypto_balances = defaultdict(float)  # Kept for backward compatibility, but data is in DB
# Set admin balance for new admin
user_balances[8311802199] = ADMIN_BALANCE
game_locks = defaultdict(asyncio.Lock)
user_withdrawals = {}  # Kept for backward compatibility, but data is in DB
withdrawal_counter = 26356  # Loaded from DB on startup

user_profiles = {}  # Kept for backward compatibility, but data is in DB
user_game_history = defaultdict(list)  # Kept for backward compatibility, but data is in DB

# Track users who have claimed bonus
user_bonus_claimed = set()

# Track weekly bonus claims (user_id -> last claim datetime)
user_weekly_bonus_claimed = {}

# Track weekly bonus generated amounts per ISO week
# user_id -> {"iso_week": (year, week), "amount_usd": float, "claimed": bool}
user_weekly_bonus_data = {}

# Track last game settings for repeat/double feature
user_last_game_settings = {}

# Username to user_id mapping
username_to_id = {}

# Admin-set casino bankroll (USD)
casino_bankroll_usd = 0.0
_bankroll_win_blocked: set = set()  # user_ids whose last win was blocked by insufficient bankroll

# ── Special Event State ───────────────────────────────────────────────────────
active_jackpot_stars  = 0.0   # >0 = jackpot live; first game win via wrapper claims it
_jackpot_notify_queue = []    # [(user_id, amount)] pending bot notifications
deposit_bonus_mult    = 1     # 1=normal | 2=double | 3=triple — applied on deposit credit
golden_hour_end_dt    = None  # datetime when golden hour ends; None = off
golden_hour_mult_val  = 1.5   # win multiplier during golden hour
cashback_pct          = 0     # 0=off; % of each bet refunded on loss
cashback_end_dt       = None  # datetime when cashback event ends; None = off
cashback_start_dt     = None  # datetime cashback started; used to filter game_history
_cashback_seen_ids    = set() # game_history IDs already refunded (in-memory guard)
# ─────────────────────────────────────────────────────────────────────────────

# Track admin broadcast state per admin user_id
broadcast_waiting = set()

# Track which user owns which menu message (callback protection)
menu_owners = {}

# Withdraw video file_id (set by admin via /video command)
withdraw_video_file_id = None

# Bot identity (set via /steal command)
bot_identity = {
    "name": "Iibrate",
    "channel_link": "",
    "chat_link": "",
    "support_username": ""
}

# Referral system
user_referral_codes = {}  # user_id -> referral_code
referral_code_to_user = {}  # referral_code -> user_id
user_referrers = {}  # user_id -> referrer_user_id (who referred them)
user_referrals = defaultdict(set)  # referrer_user_id -> set of referred user_ids
user_referral_earnings = defaultdict(float)  # user_id -> total lifetime earnings (in stars)
user_referral_balance = defaultdict(float)  # user_id -> current withdrawable balance (in stars)

# Banned users
banned_users = set()  # user_id -> banned status

# Frozen users (can't deposit, withdraw, or play until unfrozen)
frozen_users = set()

# Gift system
admin_gift_mode = {}  # admin_id -> True if pingme was sent (enables real stars gift)
gift_comment = "💰 @Iibrate - be with the best!"  # Gift comment (changeable via /cg)

# Random gift messages for Telegram gifts
GIFT_MESSAGES = [
    "🎉 Surprise! A special gift just for you.",
    "🎂 Lucky player! Enjoy this free casino reward.",
    "💰 Bonus unlocked! Time to play and win.",
    "💎 A gift from the house — good luck!",
    "🔥 You're on a lucky streak! Claim your gift.",
    "💎 Exclusive reward for you. Don't miss it!",
    "🎲 Free coins added — spin now!",
    "⚡ Limited-time gift! Play before it expires.",
    "💰 Winners get rewards — here's yours!",
    "📊 Thanks for playing! Enjoy this bonus."
]

def get_random_gift_message():
    """Get a random gift message from the list"""
    return random.choice(GIFT_MESSAGES)

# Support ticket system
user_tickets = {}  # user_id -> list of tickets
ticket_counter = 1  # Global ticket counter

# Language system
bot_language = "en"  # Default bot language
user_languages = {}  # user_id -> "en"/"ru"/"de"/"fr"/"zh" (user-specific, auto-detected from Telegram language_code)

# Message template system
TEMPLATES_DB = "templates.db"
template_setup_mode = {}  # admin_id -> {"command": "...", "waiting_for": True/False}

# ==================== GLOBAL PREMIUM EMOJI SYSTEM ====================
# Tracks the last bot message per chat for /emoji (extract emojis to map)
last_bot_messages = {}  # chat_id -> {"message_key": str, "text": str, "message_id": int}

# Global emoji map: normal_emoji -> custom_emoji_id. Loaded at startup, applies to ALL users.
emoji_map = {}  # str -> str

# Active /emoji flow per admin (only emojis not yet saved)
emoji_replace_flow = {}  # admin_id -> {"chat_id": int, "emojis": [(char, pos), ...], "current_index": int, "total": int}

EMOJI_DB = "emoji_mappings.db"

# Pre-seeded emoji IDs from Housebalcasino_by_fStikBot + Housebalcasinos_by_fStikBot packs.
# INSERT OR IGNORE on startup so manually-set overrides always take precedence.
PACK_EMOJI_MAP: dict[str, str] = {
    "0️⃣": "6114022787809024775",
    "1️⃣": "6111826942829271640",
    "2️⃣": "6111496921837214536",
    "3️⃣": "6113967352666135290",
    "4️⃣": "6114129328767769624",
    "5️⃣": "6111563214657427973",
    "6️⃣": "6113638298041719612",
    "7️⃣": "6113755567828771200",
    "8️⃣": "6113942798338104243",
    "9️⃣": "6111746150199466816",
    "©️": "6114126674477981913",
    "🃏": "6114122693043296454",
    "🉑": "6111945148919193316",
    "🌐": "6113693642990296264",
    "🌙": "6113896691864181654",
    "🌟": "6113930884098823987",
    "🍏": "6111673191590009480",
    "🍑": "6113664050665627038",
    "🎁": "6113797662803237818",
    "🎉": "6111455466812874411",
    "🎟": "6111922634700627535",
    "🎫": "6111628966311763378",
    "🎯": "6111583254974831218",
    "🎰": "6111493262525077768",
    "🎲": "6113963100648512468",
    "🎳": "6113935045922134733",
    "🏀": "6113650736267010407",
    "🏅": "6114149446394583417",
    "🏆": "6113884210689219626",
    "🏝": "6113669934770822707",
    "🏦": "6114143540814551799",
    "🏪": "6111580171188322502",
    "🏴‍☠️": "6111589280813947800",
    "🐕": "6111421635355482752",
    "🐬": "6111771138319194729",
    "🐳": "6114006368149053409",
    "🐶": "6113670999922712120",
    "🐸": "6111395607853669307",
    "🐹": "6111426248150358900",
    "👅": "6111923759982059219",
    "👊": "6111667633902328232",
    "👋": "6111866662686825703",
    "👍": "6114016310998342604",
    "👎": "6114098598276766482",
    "👑": "6113908949700845406",
    "👛": "6113927924866358642",
    "👤": "6111694314239174008",
    "👥": "6111779268692286630",
    "💃": "6111610755650427825",
    "💎": "6113902223782058655",
    "💖": "6111576980027612117",
    "💙": "6113985090881067770",
    "💠": "6111775772588906842",
    "💡": "6113699647354575994",
    "💤": "6111851127790116810",
    "💬": "6111558619042421818",
    "💰": "6111501702135815626",
    "💲": "6114020223713550008",
    "💳": "6114107475974166922",
    "💸": "6111665933095279128",
    "💻": "6114092134350985741",
    "📈": "6113709195066874926",
    "📉": "6111756737293852039",
    "📌": "6113707623108843767",
    "📎": "6113644646003383301",
    "📖": "6111738024121342598",
    "📚": "6114127043845167948",
    "📞": "6111518246349838229",
    "📢": "6111786651741068237",
    "📣": "6111471431206314677",
    "📤": "6111527166996913059",
    "📥": "6114139434825817835",
    "📰": "6111765275688835886",
    "📶": "6114028238122523697",
    "🔁": "6111809024225713005",
    "🔄": "6111397076732484727",
    "🔎": "6113836613861646277",
    "🔐": "6113827298077579994",
    "🔒": "6111706370212372478",
    "🔗": "6111717665976359348",
    "🔜": "6114091189458181371",
    "🔞": "6113764222187872798",
    "🔥": "6113815177679870968",
    "🔨": "6114151280345618937",
    "🕓": "6113887608008351340",
    "🖼️": "6114062692350172772",
    "🖼": "6111569528259353014",
    "🗑️": "6113973752167406646",
    "🗡": "6111534352477199504",
    "🗣": "6113688398835228390",
    "😊": "6111727694724996003",
    "😔": "6113904659028516214",
    "🙀": "6111742606851449477",
    "🚩": "6111405271530085254",
    "🚰": "6114130161991425566",
    "🛍️": "6111923021247682614",
    "🛒": "6111550883806321922",
    "🛜": "6111621673457294491",
    "🤑": "6111577851905973449",
    "🤖": "6111666246627895590",
    "🤝": "6113908588923590769",
    "🤡": "6113754653000735478",
    "🤥": "6113961605999893153",
    "🥇": "6113868246295780816",
    "🥶": "6113750396688144704",
    "🦋": "6113895016826937538",
    "🦴": "6114168915481342503",
    "🧡": "6114102596891318599",
    "🪙": "6111830812594806438",
    "ℹ️": "6111422155046525368",
    "⌨️": "6111916003271122836",
    "☺️": "6111578491856099942",
    "⚙️": "6111786630266231296",
    "⚠️": "6113689725980121538",
    "⚡": "6114042669212639438",
    "⚽️": "6111397617898363466",
    "⛺️": "6111607238072212936",
    "✅": "6111695280606812565",
    "✈️": "6111930296922283758",
    "❌": "6114018136359443360",
    "❓": "6114071028881693079",
    "❤️": "6111881862576088892",
    "➕": "6111433768638101415",
    "⬆️": "6111595010300321739",
    "⬇️": "6111513178288429946",
    "⭐": "6111461024500555125",
}

# Crypto deposit addresses (set by admin via /set command)
crypto_addresses = {}  # coin_name -> {"address": "...", "network": "..."}
admin_setting_crypto = {}  # admin_id -> coin_name (tracks which coin admin is setting)
# Temporary crypto addresses for users (expires in 1 hour)
user_temp_crypto_addresses = {}  # (user_id, coin_key) -> {"address": "...", "expires_at": datetime}

STARS_TO_USD = 0.0179
STARS_TO_TON = 0.01201014
MIN_WITHDRAWAL = 200  # Can be changed by admin via /wd command
BONUS_AMOUNT = 20  # legacy/profile bonus
BONUS_MIN = 30
BONUS_MAX = 50

# Matches history pagination
MATCHES_PER_PAGE = 7
MATCH_ID_BASE = 1100000  # Base offset for match IDs

# Game display names for /matches
MATCH_GAME_DISPLAY = {
    'dice': {'emoji': '🎲', 'name': 'Dice Battle'},
    'dart': {'emoji': '🎯', 'name': 'Predict'},
    'arrow': {'emoji': '🎯', 'name': 'Predict'},
    'football': {'emoji': '🎯', 'name': 'Predict'},
    'basket': {'emoji': '🎯', 'name': 'Predict'},
    'bowl': {'emoji': '🎯', 'name': 'Predict'},
    'coinflip': {'emoji': '🎲', 'name': 'Coinflip'},
    'mines': {'emoji': '💎', 'name': 'Mines'},
    'predict': {'emoji': '🎲', 'name': 'Predict'},
    'blackjack': {'emoji': '🎴', 'name': 'Blackjack'},
}

# Coinflip game
COINFLIP_STICKERS_FILE = "coinflip_stickers.json"
coinflip_stickers = {"heads": None, "tails": None}  # file_id storage
coinflip_sessions = {}  # user_id -> {"call": "heads"/"tails", "bet": int, "chat_id": int, "message_id": int}
cflip_setup = {}  # admin_id -> {"step": "heads"/"tails"}

CF_MULTIPLIER = 1.92

# ==================== BLACKJACK GAME ====================
blackjack_sessions = {}  # user_id -> session dict
# BJ_SUITS / BJ_VALUES now live in optimus/games/blackjack_engine.py
BJ_BET_OPTIONS = [50, 100, 250, 500, 1000]  # Star amounts

GAME_TYPES = {
    'dice': {'emoji': '🎲', 'name': 'Dice', 'max_value': 6, 'icon': '🎲'},
    'bowl': {'emoji': '🎳', 'name': 'Bowling', 'max_value': 6, 'icon': '🎳'},
    'dart': {'emoji': '🎯', 'name': 'Darts', 'max_value': 6, 'icon': '🎯'},
    'arrow': {'emoji': '🎯', 'name': 'Darts', 'max_value': 6, 'icon': '🎯'},
    'football': {'emoji': '⚽', 'name': 'Football', 'max_value': 5, 'icon': '⚽'},
    'basket': {'emoji': '🏀', 'name': 'Basketball', 'max_value': 5, 'icon': '🏀'},
    'coinflip': {'emoji': '🎲', 'name': 'Coinflip', 'max_value': 2, 'icon': '🎲'}
}

# New point-based game system config
GAME_CONFIG = {
    "dice": {
        "emoji": "🎲",
        "name": "Dice game",
        "action": "roll",
        "min": 1,
        "max": 6,
        "tg_emoji": "🎲"
    },
    "dart": {
        "emoji": "🎯",
        "name": "Dart game",
        "action": "throw",
        "min": 1,
        "max": 6,
        "tg_emoji": "🎯"
    },
    "football": {
        "emoji": "⚽",
        "name": "Football game",
        "action": "kick",
        "min": 1,
        "max": 5,
        "tg_emoji": "⚽"
    },
    "basket": {
        "emoji": "🏀",
        "name": "Basket game",
        "action": "shot",
        "min": 1,
        "max": 5,
        "tg_emoji": "🏀"
    },
    "bowl": {
        "emoji": "🎳",
        "name": "Bowling game",
        "action": "score",
        "min": 0,
        "max": 6,
        "tg_emoji": "🎳"
    }
}

MULTIPLIERS = {
    "normal": 1.92,
    "double": 1.92,
    "crazy": 1.92
}

# Game sessions for point-based system (replaces old user_games for dice/dart/football/basket/bowl)
game_sessions = {}

# Predict game sessions
predict_sessions = {}  # user_id -> {"chat_id", "message_id", "selected": set(), "bet": int, "selection_type": str|None}
PREDICT_HOUSE_EDGE = 0.05
PREDICT_DEFAULT_BET = 10
PREDICT_MIN_BET = 1

# Casino Levels System (Steel to Diamond)
CASINO_LEVELS = {
    0: {"name": "Steel", "rakeback": 5.0, "weekly_mult": 1.09, "level_up_bonus": 0, "next_level": 1},
    1: {"name": "Iron I", "rakeback": 6.5, "weekly_mult": 1.09, "level_up_bonus": 5, "next_level": 2},
    2: {"name": "Iron II", "rakeback": 7.0, "weekly_mult": 1.12, "level_up_bonus": 5, "next_level": 3},
    3: {"name": "Iron III", "rakeback": 7.0, "weekly_mult": 1.12, "level_up_bonus": 5, "next_level": 4},
    4: {"name": "Iron IV", "rakeback": 7.0, "weekly_mult": 1.12, "level_up_bonus": 5, "next_level": 5},
    5: {"name": "Bronze I", "rakeback": 7.5, "weekly_mult": 1.15, "level_up_bonus": 7, "next_level": 6},
    6: {"name": "Bronze II", "rakeback": 8.0, "weekly_mult": 1.18, "level_up_bonus": 10, "next_level": 7},
    7: {"name": "Bronze III", "rakeback": 8.5, "weekly_mult": 1.21, "level_up_bonus": 12, "next_level": 8},
    8: {"name": "Bronze IV", "rakeback": 9.0, "weekly_mult": 1.25, "level_up_bonus": 15, "next_level": 9},
    9: {"name": "Silver I", "rakeback": 9.5, "weekly_mult": 1.30, "level_up_bonus": 20, "next_level": 10},
    10: {"name": "Silver II", "rakeback": 10.0, "weekly_mult": 1.35, "level_up_bonus": 25, "next_level": 11},
    11: {"name": "Silver III", "rakeback": 10.5, "weekly_mult": 1.40, "level_up_bonus": 30, "next_level": 12},
    12: {"name": "Silver IV", "rakeback": 11.0, "weekly_mult": 1.45, "level_up_bonus": 40, "next_level": 13},
    13: {"name": "Gold I", "rakeback": 12.0, "weekly_mult": 1.50, "level_up_bonus": 50, "next_level": 14},
    14: {"name": "Gold II", "rakeback": 13.0, "weekly_mult": 1.55, "level_up_bonus": 75, "next_level": 15},
    15: {"name": "Gold III", "rakeback": 14.0, "weekly_mult": 1.60, "level_up_bonus": 100, "next_level": 16},
    16: {"name": "Gold IV", "rakeback": 15.0, "weekly_mult": 1.70, "level_up_bonus": 150, "next_level": 17},
    17: {"name": "Platinum I", "rakeback": 16.0, "weekly_mult": 1.80, "level_up_bonus": 200, "next_level": 18},
    18: {"name": "Platinum II", "rakeback": 17.0, "weekly_mult": 1.90, "level_up_bonus": 250, "next_level": 19},
    19: {"name": "Platinum III", "rakeback": 18.0, "weekly_mult": 2.00, "level_up_bonus": 300, "next_level": 20},
    20: {"name": "Platinum IV", "rakeback": 20.0, "weekly_mult": 2.20, "level_up_bonus": 400, "next_level": 21},
    21: {"name": "Diamond I", "rakeback": 22.0, "weekly_mult": 2.40, "level_up_bonus": 500, "next_level": 22},
    22: {"name": "Diamond II", "rakeback": 24.0, "weekly_mult": 2.60, "level_up_bonus": 750, "next_level": 23},
    23: {"name": "Diamond III", "rakeback": 26.0, "weekly_mult": 2.80, "level_up_bonus": 1000, "next_level": 24},
    24: {"name": "Diamond IV", "rakeback": 28.0, "weekly_mult": 3.00, "level_up_bonus": 1500, "next_level": 25},
    25: {"name": "Diamond V", "rakeback": 30.0, "weekly_mult": 3.50, "level_up_bonus": 2500, "next_level": None}
}

# Level progression thresholds (total bets in USD)
LEVEL_THRESHOLDS = {
    0: 0,      # Steel
    1: 100,    # Iron I
    2: 250,    # Iron II
    3: 500,    # Iron III
    4: 1000,   # Iron IV
    5: 2000,   # Bronze I
    6: 3500,   # Bronze II
    7: 5500,   # Bronze III
    8: 8000,   # Bronze IV
    9: 12000,  # Silver I
    10: 18000, # Silver II
    11: 26000, # Silver III
    12: 36000, # Silver IV
    13: 50000, # Gold I
    14: 70000, # Gold II
    15: 95000, # Gold III
    16: 130000, # Gold IV
    17: 180000, # Platinum I
    18: 250000, # Platinum II
    19: 350000, # Platinum III
    20: 500000, # Platinum IV
    21: 750000, # Diamond I
    22: 1100000, # Diamond II
    23: 1600000, # Diamond III
    24: 2300000, # Diamond IV
    25: 3500000  # Diamond V (MAX)
}


# ==================== SQLITE DATA PERSISTENCE ====================

def save_data():
    """Save all data to SQLite database (now a no-op, data is saved immediately)"""
    # Data is now saved immediately via db module, so this is just for compatibility
    # Some functions may still call save_data() for legacy reasons
    pass


def load_data():
    """Load all data from SQLite database into memory for compatibility"""
    global user_balances, user_profiles, user_game_history, user_bonus_claimed
    global user_withdrawals, withdrawal_counter, admin_list, username_to_id
    global user_last_game_settings, withdraw_video_file_id, casino_bankroll_usd
    global user_weekly_bonus_claimed
    global user_referral_codes, referral_code_to_user, user_referrers
    global user_referrals, user_referral_earnings, user_referral_balance
    global bot_identity, banned_users, frozen_users, MIN_WITHDRAWAL, gift_comment
    global user_tickets, ticket_counter, crypto_addresses, user_crypto_balances, bot_language
    
    try:
        # Initialize database connection (creates tables if needed)
        db.get_db_connection()
        
        # Create backup on startup
        db.backup_database()
        
        # Load data into memory for backward compatibility
        # Note: Most functions now use db directly, but we keep this for compatibility
        
        # Load withdrawal counter
        
        # Load ticket counter
        ticket_counter = db.get_ticket_counter()
        
        # Load min withdrawal
        
        # Load casino bankroll (seed to 33535.65 on first run)
        casino_bankroll_usd = db.get_casino_bankroll()
        if casino_bankroll_usd == 0.0:
            casino_bankroll_usd = 33535.65
            db.set_casino_bankroll(casino_bankroll_usd)
        
        # Load withdraw video file ID
        
        # Load bot language
        bot_language = db.get_bot_language()
        
        # Load gift comment
        gift_comment = db.get_gift_comment()
        
        # Load bot identity
        bot_identity.update(db.get_bot_identity())
        
        # Load admins
        admin_list.update(db.get_all_admins())

        # Load frozen users
        frozen_users.update(db.get_frozen_users())

        # Load crypto addresses
        
        # Load user balances into memory cache for compatibility
        conn = db.get_db_connection()

        # Load banned users (DB is source of truth for is_banned)
        cursor = conn.execute("SELECT user_id FROM users WHERE is_banned=1")
        for row in cursor.fetchall():
            banned_users.add(int(row["user_id"]))
        cursor = conn.execute("SELECT user_id, balance FROM users")
        for row in cursor.fetchall():
            user_balances[int(row['user_id'])] = float(row['balance'])

        # Load user languages into memory cache
        user_languages.update(db.get_all_user_languages())

        # Load user profiles into memory cache
        cursor = conn.execute("SELECT user_id FROM profiles")
        for row in cursor.fetchall():
            user_id = int(row['user_id'])
            profile = db.get_or_create_profile(user_id)
            user_profiles[user_id] = profile
        
        # Load game history into memory cache
        cursor = conn.execute("SELECT DISTINCT user_id FROM game_history")
        for row in cursor.fetchall():
            user_id = int(row['user_id'])
            user_game_history[user_id] = db.get_game_history(user_id)
        
        # Count users loaded
        user_count = len(user_balances)
        
        logger.info(f"Data loaded successfully from SQLite. Users in database: {user_count}")
        
        # Initialize and load global emoji mappings
        init_emoji_db()
        seed_emoji_map_from_packs()  # Pre-seed Housebalcasino pack IDs (INSERT OR IGNORE)
        load_global_emoji_map()
        logger.info(f"Emoji system ready: {len(emoji_map)} mappings loaded.")
    except Exception as e:
        logger.error(f"Error loading data: {e}")
        raise







def is_admin(user_id):
    return wallet.is_admin(user_id)  # db.is_admin OR membership in admin_list


def is_banned(user_id):
    """Check if a user is banned (local DB or shared cross-bot blacklist)"""
    if db.is_user_banned(user_id):
        return True
    try:
        return network_db.is_blacklisted(user_id)
    except Exception:
        return False


def is_frozen(user_id):
    """Check if a user's balance is frozen"""
    return user_id in frozen_users


def get_user_balance(user_id):
    return wallet.get_user_balance(user_id)


# ==================== TRANSLATION SYSTEM ====================





def set_user_balance(user_id, amount):
    return wallet.set_user_balance(user_id, amount)


def adjust_bankroll_usd(delta_usd: float):
    """Update casino bankroll by delta_usd USD, enforcing $10,000 floor."""
    return wallet.adjust_bankroll_usd(delta_usd)


def bankroll_can_pay(payout_stars: int) -> bool:
    """Returns True if the casino bankroll can cover this payout in USD."""
    return wallet.bankroll_can_pay(payout_stars)


def adjust_user_balance(user_id, amount, game=False):
    return wallet.adjust_user_balance(user_id, amount, game)


def register_menu_owner(message, owner_id):
    """Register which user owns an inline menu message (chat-scoped)."""
    if message and hasattr(message, "message_id") and hasattr(message, "chat"):
        key = (message.chat_id, message.message_id)
        menu_owners[key] = owner_id


def get_user_link(user_id, name):
    return f'<a href="tg://user?id={user_id}">{name}</a>'


def format_user_display(user_id, profile):
    """Return @username if available, otherwise clickable link with their name."""
    username = (profile.get('username') or '').lstrip('@').strip()
    display_name = profile.get('display_name') or profile.get('username') or 'Player'
    if username and username.lower() != 'unknown':
        return f"@{username}"
    return get_user_link(user_id, display_name)


from telegram import CopyTextButton

def build_copy_turn_reply_markup(user_id: int, game_emoji: str):
    """Create a one-tap button that copies the game emoji to clipboard."""
    _ = user_id
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🗒 Click To Copy ({game_emoji})", copy_text=CopyTextButton(game_emoji))]
    ])


def get_or_create_profile(user_id, username=None):
    # Get or create from database
    profile = db.get_or_create_profile(user_id, username)
    
    # Update username mapping if an actual username is provided
    if username:
        db.set_username_mapping(username, user_id)
        username_lower = username.lower().lstrip('@')
        username_to_id[username_lower] = user_id  # Keep in memory for compatibility
    
    # Convert game_counts to defaultdict for compatibility
    if 'game_counts' in profile and not isinstance(profile['game_counts'], defaultdict):
        profile['game_counts'] = defaultdict(int, profile['game_counts'])
    
    # Store in memory cache for backward compatibility
    user_profiles[user_id] = profile
    
    return profile


# ==================== REFERRAL SYSTEM ====================

def generate_referral_code():
    """Generate a unique 8-character referral code"""
    import secrets
    max_attempts = 100
    attempts = 0
    while attempts < max_attempts:
        code = secrets.token_hex(4)[:8]  # 8 characters from hex
        if code not in referral_code_to_user:
            return code
        attempts += 1
    # Fallback: use timestamp-based code if all attempts fail
    import time
    code = hex(int(time.time() * 1000000))[-8:].ljust(8, '0')
    return code


def get_or_create_referral_code(user_id):
    """Get or create a referral code for a user"""
    code = db.get_referral_code(user_id)
    if not code:
        code = generate_referral_code()
        db.set_referral_code(user_id, code)
        # Keep in memory for compatibility
        user_referral_codes[user_id] = code
        referral_code_to_user[code] = user_id
    return code


def get_referral_rate(user_id):
    """Get referral commission rate based on user's level"""
    try:
        profile = get_or_create_profile(user_id)
        total_bets = profile.get('total_bets', 0.0)
        total_bets_usd = total_bets * STARS_TO_USD
        level = get_user_level(total_bets_usd)
        
        # Rate tiers based on level
        if level <= 8:  # Steel to Bronze IV
            return 10.0
        elif level <= 12:  # Silver I to Silver IV
            return 12.0
        elif level <= 20:  # Gold I to Platinum IV
            return 15.0
        else:  # Diamond I to Diamond V
            return 20.0
    except Exception:
        return 10.0  # Default rate


def process_referral_earning(referred_user_id, loss_amount):
    """Process referral earnings when a referred user loses"""
    referrer_id = db.get_referrer(referred_user_id)
    if not referrer_id:
        # Check memory cache for compatibility
        referrer_id = user_referrers.get(referred_user_id)
        if referrer_id:
            db.set_referrer(referred_user_id, referrer_id)
        else:
            return
    
    rate = get_referral_rate(referrer_id)
    earnings = (loss_amount * rate) / 100
    
    # Get current stats
    stats = db.get_referral_stats(referrer_id)
    new_lifetime = stats['lifetime_earnings'] + earnings
    new_balance = stats['withdrawable_balance'] + earnings
    
    # Update in database
    db.update_referral_stats(referrer_id, new_lifetime, new_balance)
    
    # Keep in memory for compatibility
    user_referral_earnings[referrer_id] = new_lifetime
    user_referral_balance[referrer_id] = new_balance
    
    logger.info(f"Referral earning: User {referred_user_id} lost {loss_amount} stars, "
                f"Referrer {referrer_id} earned {earnings} stars ({rate}%)")


# Legacy rank functions (kept for backward compatibility, not used in new level system)
RANKS = {
    1: {"name": "Iron I", "wager_required": 100, "bonus": 1.00, "perks": "Access to rakeback", "tier": "Iron", "emoji": "◇"},
    2: {"name": "Iron II", "wager_required": 300, "bonus": 2.00, "perks": None, "tier": "Iron", "emoji": "◆"},
    3: {"name": "Iron III", "wager_required": 500, "bonus": 4.00, "perks": None, "tier": "Iron", "emoji": "◆"},
    4: {"name": "Bronze I", "wager_required": 1000, "bonus": 5.00, "perks": "Extra 10% added to weekly bonus", "tier": "Bronze", "emoji": "◇"},
    5: {"name": "Bronze II", "wager_required": 1500, "bonus": 6.00, "perks": None, "tier": "Bronze", "emoji": "◆"},
    6: {"name": "Bronze III", "wager_required": 2000, "bonus": 7.00, "perks": None, "tier": "Bronze", "emoji": "◆"},
    7: {"name": "Silver I", "wager_required": 3000, "bonus": 8.00, "perks": "Withdrawal fee reduced by 1%", "tier": "Silver", "emoji": "◇"},
    8: {"name": "Silver II", "wager_required": 4000, "bonus": 10.00, "perks": None, "tier": "Silver", "emoji": "◆"},
    9: {"name": "Silver III", "wager_required": 5000, "bonus": 10.00, "perks": None, "tier": "Silver", "emoji": "◆"},
    10: {"name": "Gold I", "wager_required": 7500, "bonus": 11.00, "perks": "Monthly free spins worth $10.00\n✨ Access to private chat", "tier": "Gold", "emoji": "◇"},
    11: {"name": "Gold II", "wager_required": 10000, "bonus": 12.00, "perks": None, "tier": "Gold", "emoji": "◆"},
    12: {"name": "Gold III", "wager_required": 12500, "bonus": 12.00, "perks": None, "tier": "Gold", "emoji": "◆"},
    13: {"name": "Platinum I", "wager_required": 15000, "bonus": 12.00, "perks": "Weekly bonus claimed twice a week\n✨ Weekly free spins worth $4.00\n✨ Withdrawal fee reduced by 1.5%", "tier": "Platinum", "emoji": "◇"},
    14: {"name": "Platinum II", "wager_required": 20000, "bonus": 13.00, "perks": None, "tier": "Platinum", "emoji": "◆"},
    15: {"name": "Platinum III", "wager_required": 25000, "bonus": 15.00, "perks": None, "tier": "Platinum", "emoji": "◆"},
    16: {"name": "Diamond I", "wager_required": 40000, "bonus": 25.00, "perks": "Access to Reload\n✨ VIP support\n✨ Dice Battle fee reduced by 20%", "tier": "Diamond", "emoji": "◇"},
    17: {"name": "Diamond II", "wager_required": 50000, "bonus": 30.00, "perks": None, "tier": "Diamond", "emoji": "◆"},
    18: {"name": "Diamond III", "wager_required": 60000, "bonus": 50.00, "perks": None, "tier": "Diamond", "emoji": "◆"},
    19: {"name": "Amethyst I", "wager_required": 80000, "bonus": 70.00, "perks": "No withdrawal fee\n✨ VIP giveaways", "tier": "Amethyst", "emoji": "◇"},
    20: {"name": "Amethyst II", "wager_required": 100000, "bonus": 90.00, "perks": None, "tier": "Amethyst", "emoji": "◆"},
    21: {"name": "Amethyst III", "wager_required": 125000, "bonus": 120.00, "perks": None, "tier": "Amethyst", "emoji": "◆"},
    22: {"name": "Emerald I", "wager_required": 150000, "bonus": 150.00, "perks": None, "tier": "Emerald", "emoji": "◇"},
    23: {"name": "Emerald II", "wager_required": 200000, "bonus": 180.00, "perks": None, "tier": "Emerald", "emoji": "◆"},
    24: {"name": "Emerald III", "wager_required": 250000, "bonus": 200.00, "perks": None, "tier": "Emerald", "emoji": "◆"},
    25: {"name": "Sapphire I", "wager_required": 300000, "bonus": 220.00, "perks": None, "tier": "Sapphire", "emoji": "◇"},
    26: {"name": "Sapphire II", "wager_required": 400000, "bonus": 260.00, "perks": None, "tier": "Sapphire", "emoji": "◆"},
    27: {"name": "Sapphire III", "wager_required": 500000, "bonus": 270.00, "perks": None, "tier": "Sapphire", "emoji": "◆"},
    28: {"name": "Ruby I", "wager_required": 700000, "bonus": 290.00, "perks": None, "tier": "Ruby", "emoji": "◇"},
    29: {"name": "Ruby II", "wager_required": 900000, "bonus": 340.00, "perks": None, "tier": "Ruby", "emoji": "◆"},
    30: {"name": "Ruby III", "wager_required": 1100000, "bonus": 400.00, "perks": None, "tier": "Ruby", "emoji": "◆"},
    31: {"name": "Unreal I", "wager_required": 1400000, "bonus": 500.00, "perks": None, "tier": "Unreal", "emoji": "◇"},
    32: {"name": "Unreal II", "wager_required": 1750000, "bonus": 750.00, "perks": None, "tier": "Unreal", "emoji": "◆"},
    33: {"name": "Unreal III", "wager_required": 2000000, "bonus": 1000.00, "perks": None, "tier": "Unreal", "emoji": "◆"}
}

def get_user_rank(wager_usd):
    current_rank = 1
    for level in range(1, 34):
        if wager_usd >= RANKS[level]['wager_required']:
            current_rank = level
        else:
            break
    return current_rank


def get_rank_info(level):
    return RANKS.get(level, RANKS[1])


def update_game_stats(user_id, game_type, bet_amount, win_amount, won):
    profile = get_or_create_profile(user_id)
    
    # Record wager for race leaderboard
    display_name = profile.get('username') or "Player"
    asyncio.create_task(record_wager(user_id, display_name, bet_amount))
    
    # Update profile stats
    profile['total_games'] += 1
    profile['total_bets'] += bet_amount
    
    if won:
        profile['games_won'] += 1
        profile['total_wins'] += win_amount
        if win_amount > profile['biggest_win']:
            profile['biggest_win'] = win_amount
    else:
        profile['games_lost'] += 1
        profile['total_losses'] += bet_amount
        # Process referral earnings when user loses
        process_referral_earning(user_id, bet_amount)
        # Process rakeback accumulation (5% of loss)
        current_rank = get_user_rank(profile.get('total_bets', 0.0) * STARS_TO_USD)
        if current_rank >= 2:  # Bronze I and above
            profile['rakeback_balance'] = profile.get('rakeback_balance', 0.0) + (bet_amount * 0.05)
    
    profile['game_counts'][game_type] += 1
    
    max_count = 0
    fav_game = None
    for gt, count in profile['game_counts'].items():
        if count > max_count:
            max_count = count
            fav_game = gt
    profile['favorite_game'] = fav_game
    
    # Save to database
    db.update_profile(
        user_id,
        total_games=profile['total_games'],
        total_bets=profile['total_bets'],
        total_wins=profile['total_wins'],
        total_losses=profile['total_losses'],
        games_won=profile['games_won'],
        games_lost=profile['games_lost'],
        favorite_game=profile['favorite_game'],
        biggest_win=profile['biggest_win'],
        game_counts=profile['game_counts'],
        rakeback_balance=profile.get('rakeback_balance', 0.0),
        claimed_ranks=profile.get('claimed_ranks', []),
        last_reload_claim=profile.get('last_reload_claim')
    )
    
    # Add to game history
    db.add_game_history(user_id, game_type, bet_amount, win_amount if won else 0.0, won)
    
    # Keep in memory for compatibility
    user_game_history[user_id].append({
        'game_type': game_type,
        'bet_amount': bet_amount,
        'win_amount': win_amount if won else 0,
        'won': won,
        'timestamp': datetime.now()
    })












def get_or_create_temp_address(user_id, coin_key, base_address):
    """Get existing temp address or create a new one"""
    from datetime import datetime, timedelta
    key = (user_id, coin_key)
    
    # Check if we have a valid temp address
    if key in user_temp_crypto_addresses:
        temp_data = user_temp_crypto_addresses[key]
        expires_at = temp_data.get("expires_at")
        if expires_at and datetime.now() < expires_at:
            # Still valid, return it
            return temp_data["address"], expires_at
    
    # Create new temp address
    temp_address = generate_temp_crypto_address(base_address, coin_key)
    expires_at = datetime.now() + timedelta(hours=1)
    user_temp_crypto_addresses[key] = {
        "address": temp_address,
        "expires_at": expires_at
    }
    return temp_address, expires_at




def check_bot_name_in_profile(user) -> bool:
    first_name = (user.first_name or "").lower()
    last_name = (user.last_name or "").lower()
    bot_name_lower = bot_identity.get("name", BOT_USERNAME).lower()
    return bot_name_lower in first_name or bot_name_lower in last_name


def is_private_chat(update: Update) -> bool:
    return update.effective_chat.type == "private"


def save_last_game_settings(user_id, game_type, bet_amount, mode="normal", points_target=1):
    """Save user's last game settings for repeat/double feature"""
    settings = {
        'game_type': game_type,
        'bet_amount': bet_amount,
        'mode': mode,
        'points_target': points_target
    }
    user_last_game_settings[user_id] = settings
    db.set_last_game_settings(user_id, settings)


def get_user_id_by_username(username):
    """Get user_id from username"""
    username_lower = username.lower().lstrip('@')
    return username_to_id.get(username_lower)


# ==================== ERROR HANDLING DECORATOR ====================

def handle_errors(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        # Check if user is banned (allow admins and ban/unban commands)
        user_id = None
        if update:
            if update.effective_user:
                user_id = update.effective_user.id
            elif update.message and update.message.from_user:
                user_id = update.message.from_user.id
            elif update.callback_query and update.callback_query.from_user:
                user_id = update.callback_query.from_user.id
        
        # Allow ban/unban commands to work even if admin is somehow banned
        command_name = func.__name__
        is_ban_command = command_name in ['ban_command', 'unban_command']
        
        # Check if user is banned (allow admins and ban/unban commands)
        if user_id and is_banned(user_id) and not is_admin(user_id) and not is_ban_command:
            return  # Silently ignore banned users

        # Check if user is frozen (block deposit, withdraw, and game commands)
        frozen_commands = [
            'deposit_command', 'withdraw_command', 'play_command',
            'dice_game', 'dart_game', 'football_game', 'basket_game', 'bowl_game',
            'mines_command', 'predict_command', 'cflip_setup_command', 'cf_command',
            'blackjack_command',  # /bj visual blackjack
        ]
        if user_id and is_frozen(user_id) and not is_admin(user_id) and command_name in frozen_commands:
            if update.message:
                await update.message.reply_html(
                    "🧊 <b>Your account is frozen.</b>\n\n"
                    "You cannot deposit, withdraw, or play until an admin unfreezes your account."
                )
            return

        try:
            return await func(update, context, *args, **kwargs)
        except BadRequest as e:
            logger.error(f"BadRequest in {func.__name__}: {e}")
            try:
                if update.message:
                    await update.message.reply_html(
                        translate_text(
                            "❌ <b>Request Error</b>\n\n"
                            "Something went wrong with your request. Please try again."
                        )
                    )
            except Exception:
                pass
        except Forbidden as e:
            logger.error(f"Forbidden in {func.__name__}: {e}")
        except NetworkError as e:
            logger.error(f"NetworkError in {func.__name__}: {e}")
            try:
                if update.message:
                    await update.message.reply_html(
                        "❌ <b>Network Error</b>\n\n"
                        "Connection issue. Please try again later."
                    )
            except Exception:
                pass
        except TelegramError as e:
            logger.error(f"TelegramError in {func.__name__}: {e}")
            try:
                if update.message:
                    msg_user_id = update.message.from_user.id if update.message.from_user else None
                    await update.message.reply_html(
                        translate_text(
                            "❌ <b>Error</b>\n\n"
                            "An error occurred. Please try again.",
                            user_id=msg_user_id
                        )
                    )
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Unexpected error in {func.__name__}: {e}", exc_info=True)
            try:
                if update.message:
                    msg_user_id = update.message.from_user.id if update.message.from_user else None
                    await update.message.reply_html(
                        translate_text(
                            "❌ <b>Unexpected Error</b>\n\n"
                            "Something went wrong. Please try again later.",
                            user_id=msg_user_id
                        )
                    )
            except Exception:
                pass
    return wrapper


# ==================== BONUS COMMAND ====================

def get_next_saturday():
    """Get the next Saturday at 00:00:00 (if today is Saturday, return next Saturday)"""
    now = datetime.now()
    # Saturday is weekday 5 (Monday=0, Sunday=6)
    days_until_saturday = (5 - now.weekday()) % 7
    
    # If today is Saturday, return next Saturday (7 days)
    if days_until_saturday == 0:
        days_until_saturday = 7
    
    next_saturday = now.replace(hour=0, minute=0, second=0, microsecond=0)
    next_saturday += timedelta(days=days_until_saturday)
    return next_saturday


def is_saturday():
    """Check if today is Saturday"""
    return datetime.now().weekday() == 5




def calculate_estimated_weekly_bonus(user_id):
    """Return a random weekly bonus amount to display (30-50 stars)."""
    return random.randint(BONUS_MIN, BONUS_MAX)


def get_weekly_bonus_amount():
    """Return a random weekly bonus amount within range."""
    return random.randint(BONUS_MIN, BONUS_MAX)








# ==================== ADMIN COMMANDS ====================





# ==================== TODAY DASHBOARD (ADMIN) ====================



# ==================== VIDEO COMMAND (ADMIN) ====================



@handle_errors
async def handle_video_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle video upload from admin for withdraw feature and support ticket submissions"""
    global withdraw_video_file_id
    user_id = update.effective_user.id
    
    # Check if user has a support ticket waiting for video/mp3
    ticket_id = context.user_data.get('support_waiting_video_ticket_id')
    if ticket_id:
        # Find the ticket
        user_ticket_list = user_tickets.get(user_id, [])
        ticket = None
        for t in user_ticket_list:
            if t.get('ticket_id') == ticket_id and t.get('waiting_for_video'):
                ticket = t
                break
        
        if ticket:
            # Get video/audio/document from message
            video = update.message.video or update.message.animation or update.message.document
            audio = update.message.audio
            
            # Check if it's a video, audio (mp3), or document
            if video or audio:
                # Mark ticket as video received
                ticket['waiting_for_video'] = False
                ticket['video_received'] = True
                save_data()
                
                # Clear the context flag
                context.user_data.pop('support_waiting_video_ticket_id', None)
                
                # Get withdrawal_id for the confirmation message
                withdrawal_id = ticket.get('withdrawal_id')
                
                if withdrawal_id:
                    await update.message.reply_text(
                        translate_text(f"Your message has been sent to the support team. We will get back to you shortly. The ticket is linked to exchange #{withdrawal_id}.", user_id=user_id)
                    )
                else:
                    await update.message.reply_text(
                        translate_text(f"Your message has been sent to the support team. We will get back to you shortly.", user_id=user_id)
                    )
                return
    
    # Only process if admin is waiting to set video
    if not context.user_data.get('waiting_for_video'):
        return
    
    if not is_admin(user_id):
        return
    
    # Get video from message (can be video or animation/GIF)
    video = update.message.video or update.message.animation or update.message.document
    
    if not video:
        await update.message.reply_html(
            "❌ <b>Invalid file!</b>\n\n"
            "Please send a valid video file (MP4, etc.)\n\n"
            "Use /cancel to abort."
        )
        return
    
    # Check if it's a document, verify it's a video type
    if update.message.document:
        mime_type = update.message.document.mime_type or ""
        if not mime_type.startswith('video/'):
            await update.message.reply_html(
                "❌ <b>Invalid file type!</b>\n\n"
                "Please send a video file (MP4, etc.)\n\n"
                "Use /cancel to abort."
            )
            return
    
    global withdraw_video_file_id
    withdraw_video_file_id = video.file_id
    context.user_data['waiting_for_video'] = False
    
    await update.message.reply_html(
        "✅ <b>Withdraw video set successfully!</b>\n\n"
        "This video will now be sent with all /withdraw messages.\n\n"
        "📍 <b>Commands:</b>\n"
        "• /video status - Check current video\n"
        "• /video remove - Remove video\n"
        "• /video - Set new video"
    )
    
    logger.info(f"Admin {user_id} set withdraw video: {video.file_id[:50]}...")


# ==================== STEAL COMMAND (ADMIN) ====================

def replace_bot_name_in_text(text, old_name, new_name):
    """Replace bot name in text (case-insensitive)"""
    if not text or not old_name or not new_name:
        return text
    # Replace all occurrences (case-insensitive)
    import re
    pattern = re.compile(re.escape(old_name), re.IGNORECASE)
    return pattern.sub(new_name, text)


















@handle_errors
async def handle_broadcast_capture(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Capture any message from admin when broadcast mode is active."""
    user_id = update.effective_user.id

    # ── Broadcast All (multi-bot) ──
    if context.user_data.get("broadcastall_waiting") and update.effective_chat.type == "private":
        if not is_admin(user_id):
            context.user_data["broadcastall_waiting"] = False
            return
        context.user_data["broadcastall_waiting"] = False

        bots = network_db.get_all_bots()
        total_sent = 0
        total_errors = 0
        total_users = 0

        status_msg = await context.bot.send_message(
            chat_id=user_id, text="📢 Broadcasting to all bots..."
        )

        for bot_info in bots:
            try:
                bot_obj = Bot(token=bot_info["token"])
                user_ids = get_all_user_ids_from_bot(bot_info["db_path"])
                total_users += len(user_ids)
                for uid in user_ids:
                    try:
                        await bot_obj.copy_message(
                            chat_id=uid,
                            from_chat_id=update.message.chat_id,
                            message_id=update.message.message_id
                        )
                        total_sent += 1
                    except (Forbidden, BadRequest):
                        total_errors += 1
                    except Exception:
                        total_errors += 1
                    await asyncio.sleep(0.05)
            except Exception as e:
                logger.error(f"Broadcast to {bot_info['name']} failed: {e}")

        await status_msg.edit_text(
            f"📢 <b>Broadcast All Complete</b>\n\n"
            f"👥 Total users: {total_users:,}\n"
            f"✅ Sent: {total_sent:,}\n"
            f"❌ Failed: {total_errors:,}",
            parse_mode=ParseMode.HTML
        )
        return

    # ── Normal single-bot broadcast ──
    if user_id not in broadcast_waiting:
        return
    if update.effective_chat.type != "private":
        return
    if not is_admin(user_id):
        broadcast_waiting.discard(user_id)
        return

    await perform_broadcast(update, context, update.message)
    broadcast_waiting.discard(user_id)










# ==================== CASINO LEVELS SYSTEM ====================













# Old progress bar function removed - using the new one for levels


GAME_EMOJIS = {
    "dice": "🎲",
    "dice_battle": "🎲",
    "coinflip": "🌑",
    "mines": "💣",
    "blackjack": "🃏",
    "arrow": "🎯",
    "dart": "🎯",
    "bowl": "🎳",
    "football": "⚽",
    "soccer": "⚽",
    "basket": "🏀",
    "basketball": "🏀",
    "predict": "🔮"
}

GAME_NAMES = {
    "dice": "Dice",
    "dice_battle": "Dice Battle",
    "coinflip": "Flip",
    "mines": "Mines",
    "blackjack": "Blackjack",
    "arrow": "Dart",
    "dart": "Dart",
    "bowl": "Bowl",
    "football": "Football",
    "soccer": "Football",
    "basket": "Basket",
    "basketball": "Basket",
    "predict": "Predict"
}








# ══════════════════════════════════════════════════════════════════════════════
# LEADERBOARD — Image generation + inline filter buttons
# ══════════════════════════════════════════════════════════════════════════════

# ── Hardcoded Leaderboard Data ──────────────────────────────────────────
LEADERBOARD_DATA = {
    "wins": {
        "title": "🏆 Most Wins",
        "entries": [
            ("🥇", "@zo_Yuji", "550 wins"),
            ("🥈", "@strut", "358 wins"),
            ("🥉", "?", "349 wins"),
            ("4.", "@sanixhhhhh", "307 wins"),
            ("5.", "@Agentplugz", "258 wins"),
            ("6.", "@Temporarilyuser", "251 wins"),
            ("7.", "@nawaz", "238 wins"),
            ("8.", "@simpstonate", "227 wins"),
        ]
    },
    "money": {
        "title": "💰 Most Money Won",
        "entries": [
            ("🥇", "@bnbsolxrpbtc", "$93,805"),
            ("🥈", "@nine", "$50,060"),
            ("🥉", "@frog", "$47,997"),
            ("4.", "@strut", "$43,394"),
            ("5.", "@OGUfed", "$40,070"),
            ("6.", "@qqqqqqqqqqqqq1237", "$25,529"),
            ("7.", "?", "$24,401"),
            ("8.", "@nawaz", "$19,886"),
        ]
    },
    "active": {
        "title": "🎮 Most Active",
        "entries": [
            ("🥇", "@zo_Yuji", "941 games"),
            ("🥈", "?", "737 games"),
            ("🥉", "@strut", "680 games"),
            ("4.", "@sanixhhhhh", "602 games"),
            ("5.", "@Agentplugz", "496 games"),
            ("6.", "@Temporarilyuser", "468 games"),
            ("7.", "@nawaz", "457 games"),
            ("8.", "@OGUfed", "442 games"),
        ]
    },
    "roller": {
        "title": "🎲 Highest Roller",
        "entries": [
            ("🥇", "@bnbsolxrpbtc", "$95,545"),
            ("🥈", "@nine", "$63,383"),
            ("🥉", "@frog", "$51,276"),
            ("4.", "@OGUfed", "$43,891"),
            ("5.", "@niiigggaaaaa", "$38,687"),
            ("6.", "@qqqqqqqqqqq4237", "$34,210"),
            ("7.", "?", "$27,770"),
            ("8.", "@NoHelm", "$20,490"),
        ]
    },
}

_LB_DIR = os.path.dirname(os.path.abspath(__file__))
LEADERBOARD_IMAGES = {
    "wins": os.path.join(_LB_DIR, "lb_wins.jpg"),
    "money": os.path.join(_LB_DIR, "lb_money.png"),
    "active": os.path.join(_LB_DIR, "lb_active.jpg"),
    "roller": os.path.join(_LB_DIR, "lb_roller.jpg"),
}








@handle_errors
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    help_text = t("help_text", bot_username=BOT_USERNAME)
    
    if is_admin(user_id):
        help_text += t("admin_commands")
    
    await update.message.reply_html(help_text)








def create_mines_grid_keyboard(game: MinesGame):
    """Create inline keyboard for mines game grid"""
    keyboard = []
    
    # If game is lost, reveal all mines
    reveal_all = (game.game_state == "lost")
    
    for row in range(game.grid_size):
        row_buttons = []
        for col in range(game.grid_size):
            if reveal_all:
                # Game over - show all mines and opened tiles
                if (row, col) in game.mines_positions:
                    # All mines revealed
                    row_buttons.append(InlineKeyboardButton("💣", callback_data=f"mine_click_{row}_{col}_{game.game_id}"))
                elif (row, col) in game.opened_tiles:
                    # Opened safe tile (diamond)
                    row_buttons.append(InlineKeyboardButton("💎", callback_data=f"mine_click_{row}_{col}_{game.game_id}"))
                else:
                    # Unopened safe tile
                    row_buttons.append(InlineKeyboardButton("💎", callback_data=f"mine_click_{row}_{col}_{game.game_id}"))
            elif (row, col) in game.opened_tiles:
                if (row, col) in game.mines_positions:
                    # Mine revealed (game over)
                    row_buttons.append(InlineKeyboardButton("💣", callback_data=f"mine_click_{row}_{col}_{game.game_id}"))
                else:
                    # Diamond found
                    row_buttons.append(InlineKeyboardButton("💎", callback_data=f"mine_click_{row}_{col}_{game.game_id}"))
            else:
                # Unopened tile
                row_buttons.append(InlineKeyboardButton("💎", callback_data=f"mine_click_{row}_{col}_{game.game_id}"))
        keyboard.append(row_buttons)
    
    # Add cash out button if diamonds found and game is still playing
    if game.diamonds_found > 0 and game.game_state == "playing":
        current_win = game.get_current_win()
        profit = current_win - game.bet_amount
        cash_out_text = t("mines_cash_out", user_id=game.user_id, amount=current_win, profit=profit)
        keyboard.append([InlineKeyboardButton(cash_out_text, callback_data=f"mines_cashout_{game.game_id}")])
    
    return InlineKeyboardMarkup(keyboard)


def format_mines_game_message(game: MinesGame):
    """Format the mines game display message"""
    multiplier = game.calculate_multiplier()
    current_win = game.get_current_win()
    
    profit = current_win - game.bet_amount
    total_tiles = game.grid_size * game.grid_size
    remaining_safe = total_tiles - game.num_mines - game.diamonds_found
    
    message = "💎 <b>MINES</b>\n\n"
    message += f"━━━━━━━━━━━━━━━━━━━━\n"
    message += f"📊 <b>Game Info</b>\n"
    message += f"Grid: <b>{game.grid_size}×{game.grid_size}</b> | Mines: <b>{game.num_mines}</b> 💣\n"
    message += f"💎 Diamonds Found: <b>{game.diamonds_found}</b>\n"
    message += f"🟦 Safe Tiles Remaining: <b>{remaining_safe}</b>\n\n"
    message += f"━━━━━━━━━━━━━━━━━━━━\n"
    message += f"💰 <b>Bet Amount:</b> <b>{game.bet_amount:,} ⭐</b>\n"
    message += f"📈 <b>Current Multiplier:</b> <b>{multiplier}x</b>\n"
    message += f"💵 <b>Potential Win:</b> <b>{current_win:,} ⭐</b>\n"
    if profit > 0:
        message += f"📊 <b>Profit:</b> <b>+{profit:,} ⭐</b>\n"
    message += f"━━━━━━━━━━━━━━━━━━━━"
    
    return message


@handle_errors
async def mines_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mines game command"""
    user_id = update.effective_user.id
    
    # Check if user has active game
    if user_id in mines_games:
        game = mines_games[user_id]
        # Check if game expired (5 minutes)
        if (datetime.now() - game.last_click_time).total_seconds() > 300:
            del mines_games[user_id]
        else:
            # Show current game
            message = format_mines_game_message(game)
            keyboard = create_mines_grid_keyboard(game)
            await update.message.reply_html(message, reply_markup=keyboard)
            return
    
    # Show grid size selection
    keyboard = [
        [
            InlineKeyboardButton("3×3", callback_data="mines_grid_3"),
            InlineKeyboardButton("4×4", callback_data="mines_grid_4"),
            InlineKeyboardButton("5×5", callback_data="mines_grid_5"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    balance = get_user_balance(user_id)
    
    await update.message.reply_html(
        "💎 <b>MINES</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Your Balance:</b> <b>{balance:,} ⭐</b>\n\n"
        "🎯 <b>Select Grid Size:</b>\n\n"
        "• <b>3×3</b> - 9 tiles (1-4 mines)\n"
        "• <b>4×4</b> - 16 tiles (1-7 mines)\n"
        "• <b>5×5</b> - 25 tiles (1-12 mines)\n\n"
        "━━━━━━━━━━━━━━━━━━━━",
        reply_markup=reply_markup
    )


def create_mines_grid_keyboard(game: MinesGame):
    """Create inline keyboard for mines game grid"""
    keyboard = []
    
    # If game is lost, reveal all mines
    reveal_all = (game.game_state == "lost")
    
    for row in range(game.grid_size):
        row_buttons = []
        for col in range(game.grid_size):
            if reveal_all:
                # Game over - show all mines and opened tiles
                if (row, col) in game.mines_positions:
                    # All mines revealed
                    row_buttons.append(InlineKeyboardButton("💣", callback_data=f"mine_click_{row}_{col}_{game.game_id}"))
                elif (row, col) in game.opened_tiles:
                    # Opened safe tile (diamond)
                    row_buttons.append(InlineKeyboardButton("💎", callback_data=f"mine_click_{row}_{col}_{game.game_id}"))
                else:
                    # Unopened safe tile
                    row_buttons.append(InlineKeyboardButton("💎", callback_data=f"mine_click_{row}_{col}_{game.game_id}"))
            elif (row, col) in game.opened_tiles:
                if (row, col) in game.mines_positions:
                    # Mine revealed (game over)
                    row_buttons.append(InlineKeyboardButton("💣", callback_data=f"mine_click_{row}_{col}_{game.game_id}"))
                else:
                    # Diamond found
                    row_buttons.append(InlineKeyboardButton("💎", callback_data=f"mine_click_{row}_{col}_{game.game_id}"))
            else:
                # Unopened tile
                row_buttons.append(InlineKeyboardButton("💎", callback_data=f"mine_click_{row}_{col}_{game.game_id}"))
        keyboard.append(row_buttons)
    
    # Add cash out button if diamonds found and game is still playing
    if game.diamonds_found > 0 and game.game_state == "playing":
        current_win = game.get_current_win()
        profit = current_win - game.bet_amount
        cash_out_text = t("mines_cash_out", user_id=game.user_id, amount=current_win, profit=profit)
        keyboard.append([InlineKeyboardButton(cash_out_text, callback_data=f"mines_cashout_{game.game_id}")])
    
    return InlineKeyboardMarkup(keyboard)


def format_mines_game_message(game: MinesGame):
    """Format the mines game display message"""
    multiplier = game.calculate_multiplier()
    current_win = game.get_current_win()
    
    profit = current_win - game.bet_amount
    total_tiles = game.grid_size * game.grid_size
    remaining_safe = total_tiles - game.num_mines - game.diamonds_found
    
    message = "💎 <b>MINES</b>\n\n"
    message += f"━━━━━━━━━━━━━━━━━━━━\n"
    message += f"📊 <b>Game Info</b>\n"
    message += f"Grid: <b>{game.grid_size}×{game.grid_size}</b> | Mines: <b>{game.num_mines}</b> 💣\n"
    message += f"💎 Diamonds Found: <b>{game.diamonds_found}</b>\n"
    message += f"🟦 Safe Tiles Remaining: <b>{remaining_safe}</b>\n\n"
    message += f"━━━━━━━━━━━━━━━━━━━━\n"
    message += f"💰 <b>Bet Amount:</b> <b>{game.bet_amount:,} ⭐</b>\n"
    message += f"📈 <b>Current Multiplier:</b> <b>{multiplier}x</b>\n"
    message += f"💵 <b>Potential Win:</b> <b>{current_win:,} ⭐</b>\n"
    if profit > 0:
        message += f"📊 <b>Profit:</b> <b>+{profit:,} ⭐</b>\n"
    message += f"━━━━━━━━━━━━━━━━━━━━"
    
    return message


@handle_errors
async def mines_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mines game command"""
    user_id = update.effective_user.id
    
    # Check if user has active game
    if user_id in mines_games:
        game = mines_games[user_id]
        # Check if game expired (5 minutes)
        if (datetime.now() - game.last_click_time).total_seconds() > 300:
            del mines_games[user_id]
        else:
            # Show current game
            message = format_mines_game_message(game)
            keyboard = create_mines_grid_keyboard(game)
            await update.message.reply_html(message, reply_markup=keyboard)
            return
    
    # Show grid size selection
    keyboard = [
        [
            InlineKeyboardButton("3×3", callback_data="mines_grid_3"),
            InlineKeyboardButton("4×4", callback_data="mines_grid_4"),
            InlineKeyboardButton("5×5", callback_data="mines_grid_5"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    balance = get_user_balance(user_id)
    
    await update.message.reply_html(
        "💎 <b>MINES</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Your Balance:</b> <b>{balance:,} ⭐</b>\n\n"
        "🎯 <b>Select Grid Size:</b>\n\n"
        "• <b>3×3</b> - 9 tiles (1-4 mines)\n"
        "• <b>4×4</b> - 16 tiles (1-7 mines)\n"
        "• <b>5×5</b> - 25 tiles (1-12 mines)\n\n"
        "━━━━━━━━━━━━━━━━━━━━",
        reply_markup=reply_markup
    )






















async def send_invoice(query, amount):
    title = f"Deposit {amount} Stars"
    description = f"Add {amount} ⭐ to your game balance"
    payload = f"deposit_{amount}_{query.from_user.id}"
    prices = [LabeledPrice("Stars", amount)]

    try:
        await query.message.reply_invoice(
            title=title,
            description=description,
            payload=payload,
            provider_token=PROVIDER_TOKEN,
            currency="XTR",
            prices=prices
        )
        await query.edit_message_text(
            f"💳 Invoice for <b>{amount} ⭐</b> sent!\n"
            f"Complete the payment to add Stars to your balance.",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        try:
            await query.answer(
                "ℹ️ Our servers are refreshing this table. Please try again shortly.",
                show_alert=True
            )
        except Exception:
            pass








async def start_bot_game(query, context, user_id, game_type, bet_amount, mode, points_target, is_demo=False):
    if game_type not in GAME_CONFIG:
        await query.answer(t("err_unknown_game", user_id=user_id), show_alert=True)
        return
    
    if user_id in game_sessions:
        await query.answer(t("err_active_game", user_id=user_id), show_alert=True)
        return
    
    multiplier = MULTIPLIERS.get(mode, 1.92)
    config = GAME_CONFIG[game_type]
    
    # Deduct balance
    if not is_demo and not is_admin(user_id):
        balance = get_user_balance(user_id)
        if balance < bet_amount:
            await query.edit_message_text(
                "❌ Insufficient balance! Use /deposit to add Stars.",
                parse_mode=ParseMode.HTML
            )
            return
        adjust_user_balance(user_id, -bet_amount, game=True)
        new_balance = get_user_balance(user_id)
        expected_balance = balance - bet_amount
        if abs(new_balance - expected_balance) > 0.01:
            set_user_balance(user_id, expected_balance)
        user_balances[user_id] = get_user_balance(user_id)
    
    # Create session
    game_sessions[user_id] = {
        "game_type": game_type,
        "mode": mode,
        "points_target": points_target,
        "player_score": 0,
        "bot_score": 0,
        "bet": bet_amount,
        "multiplier": multiplier,
        "chat_id": query.message.chat_id,
        "message_id": query.message.message_id,
        "is_demo": is_demo,
        "player_rolls_needed": 2 if mode == "double" else 1,
        "player_rolls_done": 0,
        "player_total": 0,
        "waiting_for_player": True,
    }

    profile = get_or_create_profile(user_id)
    display_name = profile.get('display_name') or profile.get('username') or 'Player'
    user_link = get_user_link(user_id, display_name)
    bet_usd = bet_amount * STARS_TO_USD

    mode_display = mode.capitalize()
    if mode == "normal": mode_display = "Normal"
    elif mode == "double": mode_display = "Double"
    elif mode == "crazy": mode_display = "Crazy"

    await query.edit_message_text(
        f"🔹 The game has started\n\n"
        f"Player 1: {user_link}\n"
        f"Player 2: 🤖 Librate Game\n"
        f"Bet: ${bet_usd:.2f}\n"
        f"Mode: {mode_display} - {points_target} points\n\n"
        f"Roll the dice {config['emoji']}",
        parse_mode=ParseMode.HTML,
        reply_markup=build_copy_turn_reply_markup(user_id, config['emoji'])
    )



# ============================================================
# PREDICT GAME (Dice Number Prediction)
# ============================================================





# ============================================================
# COINFLIP GAME
# ============================================================








# ==================== BLACKJACK GAME LOGIC ====================
# Pure engine extracted to optimus/games/blackjack_engine.py; re-imported
# here so all existing call sites are unchanged.
from optimus.games.blackjack_engine import (
    bj_create_deck,
    bj_card_points,
    bj_calculate_score,
    bj_calculate_visible_score,
    bj_hand_str,
    bj_generate_table_image,
    bj_resolve,
)






# Template functions
import sqlite3






# ==================== EMOJI CUSTOMIZATION DB & HELPERS ====================

# Regex that captures individual Unicode emojis (single codepoint or multi-codepoint sequences)
_EMOJI_RE = re.compile(
    "(?:"
    "[\U0001F600-\U0001F64F]"  # emoticons
    "|[\U0001F300-\U0001F5FF]"  # symbols & pictographs
    "|[\U0001F680-\U0001F6FF]"  # transport & map
    "|[\U0001F1E0-\U0001F1FF]"  # flags
    "|[\U00002702-\U000027B0]"  # dingbats
    "|[\U0000FE00-\U0000FE0F]"  # variation selectors
    "|[\U0001F900-\U0001F9FF]"  # supplemental symbols
    "|[\U0001FA00-\U0001FA6F]"  # chess symbols
    "|[\U0001FA70-\U0001FAFF]"  # symbols extended-A
    "|[\U00002600-\U000026FF]"  # misc symbols
    "|[\U00002300-\U000023FF]"  # misc technical
    "|[\U0000200D]"             # ZWJ
    "|[\U000024C2-\U0001F251]"  # enclosed characters
    ")+"
)


def init_emoji_db():
    """Create global emoji_mappings table: normal_emoji PRIMARY KEY, custom_emoji_id. No user_id/message_key."""
    conn = sqlite3.connect(EMOJI_DB)
    c = conn.cursor()
    # Check for old schema (message_key column) and migrate
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='emoji_mappings'")
    if c.fetchone():
        try:
            c.execute("PRAGMA table_info(emoji_mappings)")
            cols = [row[1] for row in c.fetchall()]
            if "message_key" in cols:
                # Old schema: migrate to new global table
                c.execute('''CREATE TABLE IF NOT EXISTS emoji_mappings_new (
                    normal_emoji TEXT PRIMARY KEY,
                    custom_emoji_id TEXT NOT NULL
                )''')
                c.execute('''INSERT OR REPLACE INTO emoji_mappings_new (normal_emoji, custom_emoji_id)
                             SELECT normal_emoji, custom_emoji_id FROM emoji_mappings''')
                c.execute('DROP TABLE emoji_mappings')
                c.execute('ALTER TABLE emoji_mappings_new RENAME TO emoji_mappings')
                conn.commit()
        except Exception as e:
            logger.warning(f"Emoji migration: {e}")
    c.execute('''CREATE TABLE IF NOT EXISTS emoji_mappings (
        normal_emoji TEXT PRIMARY KEY,
        custom_emoji_id TEXT NOT NULL
    )''')
    conn.commit()
    conn.close()


def load_global_emoji_map():
    """Load all emoji mappings into memory. Call at startup and after any save."""
    global emoji_map
    init_emoji_db()
    conn = sqlite3.connect(EMOJI_DB)
    c = conn.cursor()
    try:
        c.execute('SELECT normal_emoji, custom_emoji_id FROM emoji_mappings')
        emoji_map = {row[0]: row[1] for row in c.fetchall()}
    except sqlite3.OperationalError:
        emoji_map = {}
    conn.close()
    logger.info(f"Loaded {len(emoji_map)} global emoji mappings.")


def save_global_emoji_mapping(normal_emoji: str, custom_emoji_id: str):
    """Save one global mapping and update in-memory cache."""
    init_emoji_db()
    conn = sqlite3.connect(EMOJI_DB)
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO emoji_mappings (normal_emoji, custom_emoji_id) VALUES (?, ?)''',
              (normal_emoji, custom_emoji_id))
    conn.commit()
    conn.close()
    emoji_map[normal_emoji] = custom_emoji_id
    logger.info(f"Global emoji saved: {normal_emoji} -> {custom_emoji_id}")


def seed_emoji_map_from_packs():
    """Bulk-insert all 126 emoji IDs from the two Housebalcasino packs using INSERT OR IGNORE
    so manually-set overrides (via /emoji command) always take precedence."""
    init_emoji_db()
    conn = sqlite3.connect(EMOJI_DB)
    c = conn.cursor()
    c.executemany(
        "INSERT OR IGNORE INTO emoji_mappings (normal_emoji, custom_emoji_id) VALUES (?, ?)",
        list(PACK_EMOJI_MAP.items()),
    )
    conn.commit()
    conn.close()
    # Merge into in-memory map without overwriting any existing entries
    for em, cid in PACK_EMOJI_MAP.items():
        if em not in emoji_map:
            emoji_map[em] = cid
    logger.info(f"Seeded {len(PACK_EMOJI_MAP)} pack emoji mappings (INSERT OR IGNORE).")


def extract_emojis_ordered(text: str) -> list:
    """Extract all normal emojis from text, preserving order and duplicates.
    Returns: [("emoji_char", char_index_in_text), ...]
    """
    results = []
    for match in _EMOJI_RE.finditer(text):
        results.append((match.group(), match.start()))
    return results


def track_bot_message(chat_id: int, message_key: str, text: str, message_id: int):
    """Track the last bot message in chat for /emoji (extract emojis to map)."""
    last_bot_messages[chat_id] = {
        "message_key": message_key,
        "text": text,
        "message_id": message_id
    }


def apply_global_emoji_replace(text: str) -> str:
    """Replace every normal emoji that has a global mapping with <tg-emoji> HTML. Used before sending any message."""
    if not text or not emoji_map:
        return text
    import html as html_mod
    result = text
    # Work backwards so offsets stay valid
    for match in list(_EMOJI_RE.finditer(text))[::-1]:
        emoji_char = match.group()
        start, end = match.span()
        if emoji_char in emoji_map:
            custom_id = emoji_map[emoji_char]
            replacement = f'<tg-emoji emoji-id="{custom_id}">{html_mod.escape(emoji_char)}</tg-emoji>'
            result = result[:start] + replacement + result[end:]
    return result


# ── Button style/icon upgrade (Bot API 9.4: icon_custom_emoji_id + style) ──────
# Three valid style values.  "warning" and "secondary" don't exist in the API;
# buttons that map to those just get no style tag (default appearance).
_BTN_DANGER  = {"❌", "✖", "cancel", "close", "reject", "ban", "delete", "remove", "no"}
_BTN_SUCCESS = {"✅", "deposit", "confirm", "add", "buy", "pay", "yes", "approve", "accept", "bonus", "redeem", "claim"}
_BTN_PRIMARY = {"🎲", "🎰", "🎯", "🏆", "🎳", "🏀", "⚽", "🎱", "🎮",
                "play", "game", "spin", "bet", "start", "leaderboard", "dice",
                "darts", "bowling", "football", "basket", "coinflip", "blackjack",
                "slots", "mines", "predict"}


def _detect_button_attrs(text: str) -> tuple[str | None, str | None]:
    """Return (style, icon_custom_emoji_id) for a button label.

    style is one of "primary" | "success" | "danger" | None.
    icon_custom_emoji_id is the pack ID of the first mapped emoji in the label,
    or None if none found.
    """
    low = text.lower()

    style: str | None = None
    if any(sig in text or sig in low for sig in _BTN_DANGER):
        style = "danger"
    elif any(sig in text or sig in low for sig in _BTN_SUCCESS):
        style = "success"
    elif any(sig in text or sig in low for sig in _BTN_PRIMARY):
        style = "primary"

    # Icon: first emoji in the text that has a pack mapping
    icon_id: str | None = None
    if emoji_map:
        for match in _EMOJI_RE.finditer(text):
            em = match.group()
            if em in emoji_map:
                icon_id = emoji_map[em]
                break

    return style, icon_id


def _upgrade_button(btn: InlineKeyboardButton) -> InlineKeyboardButton:
    """Return a copy of btn with style + icon_custom_emoji_id injected via api_kwargs."""
    style, icon_id = _detect_button_attrs(btn.text)
    if not style and not icon_id:
        return btn
    extra: dict = {}
    if style:
        extra["style"] = style
    if icon_id:
        extra["icon_custom_emoji_id"] = icon_id
    existing = dict(btn.api_kwargs) if btn.api_kwargs else {}
    merged = {**extra, **existing}  # existing explicit api_kwargs always win
    return InlineKeyboardButton(
        text=btn.text,
        url=btn.url,
        callback_data=btn.callback_data,
        switch_inline_query=btn.switch_inline_query,
        switch_inline_query_current_chat=btn.switch_inline_query_current_chat,
        callback_game=btn.callback_game,
        pay=btn.pay,
        login_url=btn.login_url,
        web_app=btn.web_app,
        switch_inline_query_chosen_chat=btn.switch_inline_query_chosen_chat,
        copy_text=btn.copy_text,
        api_kwargs=merged,
    )


def _upgrade_keyboard(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    """Walk every button in an InlineKeyboardMarkup and apply style/icon upgrades."""
    if not isinstance(markup, InlineKeyboardMarkup):
        return markup
    return InlineKeyboardMarkup(
        [[_upgrade_button(btn) for btn in row] for row in markup.inline_keyboard]
    )


class EmojiAwareBot(Bot):
    """Bot that applies global emoji replacement to all sent/edited text and captions,
    and upgrades inline keyboard buttons with Bot API 9.4 style + icon fields."""

    @staticmethod
    def _patch_kwargs(kwargs: dict) -> dict:
        """Apply emoji replacement to text/caption and button upgrades to reply_markup."""
        text = kwargs.get("text")
        if text:
            kwargs = {**kwargs, "text": apply_global_emoji_replace(text)}
        caption = kwargs.get("caption")
        if caption:
            kwargs = {**kwargs, "caption": apply_global_emoji_replace(caption)}
        markup = kwargs.get("reply_markup")
        if isinstance(markup, InlineKeyboardMarkup):
            kwargs = {**kwargs, "reply_markup": _upgrade_keyboard(markup)}
        return kwargs

    async def send_message(self, *args, **kwargs):
        return await super().send_message(*args, **self._patch_kwargs(kwargs))

    async def edit_message_text(self, *args, **kwargs):
        return await super().edit_message_text(*args, **self._patch_kwargs(kwargs))

    async def edit_message_caption(self, *args, **kwargs):
        return await super().edit_message_caption(*args, **self._patch_kwargs(kwargs))


# ==================== /emoji COMMAND & FLOW ====================



async def handle_emoji_flow_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Process incoming message during emoji replacement flow.
    Returns True if message was consumed, False otherwise.
    """
    user_id = update.effective_user.id
    if user_id not in emoji_replace_flow:
        return False

    flow = emoji_replace_flow[user_id]
    message = update.message
    text = (message.text or "").strip()

    # Handle /skip
    if text.lower() == "/skip":
        idx = flow["current_index"]
        emoji_char = flow["emojis"][idx][0]
        logger.info(f"Admin {user_id} skipped emoji #{idx + 1} ({emoji_char})")

        flow["current_index"] += 1
        return await _advance_emoji_flow(update, context, user_id)

    # Handle /cancel
    if text.lower() == "/cancel":
        del emoji_replace_flow[user_id]
        await message.reply_html(t("emoji_cancelled", user_id=user_id))
        return True

    # Look for custom_emoji_id in message entities
    custom_emoji_id = None
    if message.entities:
        for entity in message.entities:
            etype = entity.type.name if hasattr(entity.type, 'name') else str(entity.type)
            if etype == "CUSTOM_EMOJI" and hasattr(entity, 'custom_emoji_id') and entity.custom_emoji_id:
                custom_emoji_id = str(entity.custom_emoji_id)
                break

    if not custom_emoji_id:
        await message.reply_html(
            "âš  <b>No custom emoji detected.</b>\n"
            "Please send a <b>premium/custom emoji</b>, /skip to keep original, or /cancel to abort."
        )
        return True

    idx = flow["current_index"]
    emoji_char = flow["emojis"][idx][0]
    save_global_emoji_mapping(emoji_char, custom_emoji_id)

    await message.reply_html(
        f"✅ Emoji <b>#{idx + 1}</b> ({emoji_char}) → custom <code>{custom_emoji_id}</code>"
    )

    flow["current_index"] += 1
    return await _advance_emoji_flow(update, context, user_id)


async def _advance_emoji_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """Advance to the next emoji or finish the flow."""
    flow = emoji_replace_flow[user_id]
    idx = flow["current_index"]
    total = flow["total"]

    if idx >= total:
        del emoji_replace_flow[user_id]
        await update.message.reply_html(
            f"✅ <b>Global emoji customization complete!</b>\n\n"
            f"Saved mappings apply to <b>all users</b> and all messages."
        )
        return True

    # Ask for the next emoji
    emoji_char = flow["emojis"][idx][0]
    await update.message.reply_html(
        f"Send a <b>custom emoji</b> for position <b>#{idx + 1}</b> of {total} ({emoji_char})\n\n"
        f"/skip to keep original · /cancel to abort"
    )
    return True


async def send_bot_reply_html(message_obj, text: str, message_key: str = None,
                              reply_markup=None, chat_id: int = None, **kwargs):
    """Send an HTML reply with global emoji replace + optional tracking for /emoji."""
    send_text = apply_global_emoji_replace(text)

    if hasattr(message_obj, 'reply_html'):
        sent = await message_obj.reply_html(send_text, reply_markup=reply_markup, **kwargs)
    elif hasattr(message_obj, 'reply_text'):
        sent = await message_obj.reply_text(send_text, parse_mode=ParseMode.HTML,
                                            reply_markup=reply_markup, **kwargs)
    else:
        sent = None

    if sent and message_key:
        cid = chat_id or (sent.chat.id if sent else None)
        if cid:
            track_bot_message(cid, message_key, text, sent.message_id)
    return sent




def get_command_message_preview(command_name, user_id):
    """Get the current message text for a command (for template preview)"""
    try:
        if command_name == "start":
            balance = get_user_balance(user_id)
            balance_usd = balance * STARS_TO_USD
            profile = user_profiles.get(user_id, {})
            turnover = profile.get('total_bets', 0.0) * STARS_TO_USD
            admin_badge = " 👑" if is_admin(user_id) else ""
            bot_name = bot_identity.get("name", "Iibrate")
            channel_link = bot_identity.get("channel_link", "https://t.me/Iibrate")
            chat_link = bot_identity.get("chat_link", "https://t.me/librateds")
            support_username = bot_identity.get("support_username", "Iibratesupport")
            if support_username.startswith('@'):
                support_link = f"https://t.me/{support_username[1:]}"
            else:
                support_link = f"https://t.me/{support_username}"
            return t("welcome", user_id=user_id,
                bot_name=bot_name, admin_badge=admin_badge,
                balance_usd=balance_usd, turnover=turnover,
                channel_link=channel_link, chat_link=chat_link, support_link=support_link
            )
        elif command_name == "deposit" or command_name == "depo":
            return t("select_deposit", user_id=user_id)
        elif command_name == "balance" or command_name == "bal":
            balance = get_user_balance(user_id)
            balance_usd = balance * STARS_TO_USD
            admin_note = " (Admin - Unlimited)" if is_admin(user_id) else ""
            return t("your_balance", user_id=user_id, admin_note=admin_note, balance=balance, balance_usd=balance_usd)
        elif command_name == "help":
            return t("help_text", user_id=user_id) or t("available_commands", user_id=user_id)
        elif command_name == "gift":
            return get_random_gift_message()
        else:
            return f"Current message for /{command_name} command"
    except Exception as e:
        logger.error(f"Error getting command preview for {command_name}: {e}")
        return f"Error: Could not get preview for /{command_name}"



@handle_errors
async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    precheckout_user_id = query.from_user.id
    if is_frozen(precheckout_user_id) and not is_admin(precheckout_user_id):
        await query.answer(ok=False, error_message="Your account is frozen. Contact an admin.")
        return
    await query.answer(ok=True)


@handle_errors
async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    payment = update.message.successful_payment

    amount = payment.total_amount
    payload = payment.invoice_payload

    # Check if this is a gift payment
    if payload and payload.startswith('gift_payment_'):
        # This is a gift payment - process gift automatically
        logger.info(f"Admin {user_id}: Gift payment received, processing gift automatically")
        await process_gift_after_payment(update, context)
        return

    # Block frozen users from depositing (payment already went through precheckout, but just in case)
    if is_frozen(user_id) and not is_admin(user_id):
        await update.message.reply_html(
            "🧊 <b>Your account is frozen.</b>\n\n"
            "Payment received but your account is frozen. Contact an admin to resolve."
        )
        # Still credit the balance since payment already processed by Telegram
        adjust_user_balance(user_id, amount)
        return

    # Regular deposit payment
    adjust_user_balance(user_id, amount)
    balance = get_user_balance(user_id)
    
    await update.message.reply_html(
        f"✅ <b>Payment successful!</b>\n\n"
        f"💰 Added: <b>{amount} ⭐</b>\n"
        f"💳 New balance: <b>{balance:,} ⭐</b>"
    )







# Gift system configuration
GIFT_STARS = 15  # Telegram's gift limit
PAYMENT_STARS = 1  # Payment amount for gift process


























# ══════════════════════════════════════════════════════════════════════════════
#  SPECIAL EVENT COMMANDS  (admin only)
# ══════════════════════════════════════════════════════════════════════════════





# ══════════════════════════════════════════════════════════════════════════════


async def bankroll_hourly_fluctuation(context: ContextTypes.DEFAULT_TYPE):
    """Every hour: randomly add or subtract $100–$10,000 from casino bankroll."""
    delta = round(random.uniform(100.0, 10000.0), 2)
    if random.choice([True, False]):
        adjust_bankroll_usd(delta)
    else:
        adjust_bankroll_usd(-delta)
    logger.info(f"[BANKROLL] Hourly fluctuation — bankroll now ${casino_bankroll_usd:,.2f}")


async def update_ton_price_job(context: ContextTypes.DEFAULT_TYPE):
    global STARS_TO_USD
    ton_price = await get_ton_price_usd()
    if ton_price:
        STARS_TO_USD = ton_price / 200

@handle_errors
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Unhandled exception: {context.error}", exc_info=context.error)
    
    try:
        if update and update.effective_message:
            await update.effective_message.reply_html(
                translate_text(
                    "❌ <b>An unexpected error occurred</b>\n\n"
                    "Please try again later. If the problem persists, contact support."
                )
            )
    except Exception as e:
        logger.error(f"Error in error handler: {e}")




    # ── Jackpot notifications ─────────────────────────────────────────────────
    while _jackpot_notify_queue:
        try:
            jp_user_id, jp_amount = _jackpot_notify_queue.pop(0)
            await context.bot.send_message(
                chat_id=jp_user_id,
                text=(
                    f"🎰 <b>JACKPOT! You won!</b>\n\n"
                    f"🏆 <b>{jp_amount:,} Stars</b> have been added to your balance!\n\n"
                    f"Congratulations! 🎊"
                ),
                parse_mode=ParseMode.HTML,
            )
            logger.info(f"[JACKPOT] Notified user {jp_user_id} — won {jp_amount:,} ⭐")
        except Exception as e:
            logger.warning(f"[JACKPOT] Notification failed: {e}")

    # ── Cashback event processing ─────────────────────────────────────────────
    try:
        global cashback_pct, cashback_end_dt, cashback_start_dt
        if cashback_pct > 0 and cashback_end_dt:
            now = datetime.now()
            if now > cashback_end_dt:
                cashback_pct = 0
                cashback_end_dt = None
                cashback_start_dt = None
                logger.info("[EVENT] Cashback event expired.")
            elif cashback_start_dt:
                conn = db.get_db_connection()
                rows = conn.execute(
                    "SELECT id, user_id, bet_amount FROM game_history "
                    "WHERE won=0 AND timestamp > ?",
                    (cashback_start_dt.isoformat(),),
                ).fetchall()
                for row in rows:
                    gid = row["id"]
                    if gid in _cashback_seen_ids:
                        continue
                    cb_amount = int(row["bet_amount"] * cashback_pct / 100)
                    if cb_amount > 0:
                        db.adjust_user_balance(row["user_id"], cb_amount)
                        _cashback_seen_ids.add(gid)
                        try:
                            await context.bot.send_message(
                                chat_id=row["user_id"],
                                text=(
                                    f"💸 <b>Cashback!</b>\n\n"
                                    f"You received <b>{cb_amount:,} ⭐</b> back "
                                    f"({cashback_pct}% cashback event is active)."
                                ),
                                parse_mode=ParseMode.HTML,
                            )
                        except Exception:
                            pass
    except Exception as e:
        logger.error(f"[CASHBACK] Processing error: {e}", exc_info=True)

    # ── Golden hour expiry ────────────────────────────────────────────────────
    try:
        global golden_hour_end_dt
        if golden_hour_end_dt and datetime.now() > golden_hour_end_dt:
            golden_hour_end_dt = None
            logger.info("[EVENT] Golden hour expired.")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# MULTI-BOT NETWORK COMMANDS
# ══════════════════════════════════════════════════════════════════════════════



import games.claw as claw
import games.roulette as roulette

# Dice family extracted to games/dice; re-import the handlers so existing
# call sites (button_callback, main, handle_game_emoji registration) resolve them.
from games.mines.engine import MinesGame
from games.predict.engine import predict_get_multiplier
from games.predict.handlers import predict_command, handle_predict_callback, predict_build_message, predict_build_keyboard
from games.blackjack.handlers import blackjack_command, handle_blackjack_callback
from games.coinflip.stickers import load_coinflip_stickers, save_coinflip_stickers
from games.coinflip.handlers import cf_command, cflip_setup_command, handle_cflip_sticker, get_cf_menu, cf_cancel_game
from bot.templates import init_templates_db, save_template, get_template, replace_template_variables, send_template_message
from bot.network_cmds import (addbot_command, removebot_command, syncbot_command, syncall_command, crossban_command, sharedblacklist_command, botnetwork_command, centralstats_command, broadcastall_command, check_sync_reload)
from bot.admin_economy import (addadmin_command, addbal_command, removebal_command, setbal_command, resetbal_command, transferbal_command, topbal_command, totalbal_command, freeze_command, unfreeze_command, removeadmin_command, listadmins_command, ban_command, unban_command)
from bot.user_stats import (get_user_level, get_level_progress, format_level_display, levels_command, profile_command, send_or_edit_history, history_command, format_matches_page, matches_command, leaderboard_command)
from bot.admin_events import (rainevent_command, jackpot_command, doubledeposit_command, tripledeposit_command, goldenhour_command, stopgoldenhour_command, cashbackevent_command, stopcashback_command, eventstatus_command, stream_command, streamoff_command)
from bot.admin_misc import bankroll_command, perform_broadcast, broadcast_command
from bot.info_cmds import com_command, cmd_command, support_command
from bot.lang_cmds import lang_command, setlang_command
from bot.admin_tools import admin_command, today_command, user_command, steal_command, emoji_command, set_video_command
from bot.bonus_cmds import weekly_command, bonus_command, referral_command
from bot.user_cmds import play_command, balance_command, deposit_command, custom_deposit
from bot.tip_cancel_cmds import tip_command, cancel_command
from bot.gift_flow import gift_command, process_gift_chat_id, process_gift_after_payment, pingme_command, cg_command
from bot.start_cmd import start
from bot.support import handle_support_callback
from bot.steal import (handle_steal_flow, move_to_next_steal_value, check_and_continue_steal, apply_steal_changes_from_query, apply_steal_changes, handle_steal_callback, show_next_steal_question)
from bot.callbacks import button_callback
from bot.text_handler import handle_text_message
import games.dice.handlers as dice
from games.dice.handlers import (
    start_game, dice_game, dart_game, football_game, basket_game, bowl_game,
    demo_command, start_round, complete_round, handle_game_emoji,
)

def main():
    # Load saved data on startup
    load_data()
    
    # Monkey-patch Message.reply_html to support streaming
    from telegram import Message
    _original_reply_html = Message.reply_html
    
    async def streaming_reply_html(self, text: str, *args, **kwargs):
        """Wrapped reply_html that supports streaming mode"""
        global streaming_enabled
        
        if not streaming_enabled or len(text.split()) <= 5:
            # Normal mode or text too short
            return await _original_reply_html(self, text, *args, **kwargs)
        
        # Streaming mode: send in chunks
        words = text.split()
        chunk_size_min, chunk_size_max = 3, 5
        delay_sec = 0.15
        
        messages = []
        i = 0
        while i < len(words):
            chunk_size = random.randint(chunk_size_min, min(chunk_size_max, len(words) - i))
            messages.append(" ".join(words[i:i + chunk_size]))
            i += chunk_size
        
        last_msg = None
        for idx, chunk in enumerate(messages):
            try:
                last_msg = await _original_reply_html(self, chunk, *args, **kwargs)
                if idx < len(messages) - 1:
                    await asyncio.sleep(delay_sec)
            except Exception as e:
                logger.error(f"Streaming chunk error: {e}")
                remaining = " ".join(messages[idx:])
                return await _original_reply_html(self, remaining, *args, **kwargs)
        return last_msg
    
    # Apply the patch
    Message.reply_html = streaming_reply_html
    
    load_coinflip_stickers()
    
    # Build application with optimizations for 1,000,000+ concurrent users
    application = (
        Application.builder()
        .bot(EmojiAwareBot(BOT_TOKEN))
        .concurrent_updates(True)  # Process updates in parallel
        .build()
    )
    
    application.add_error_handler(error_handler)

    # Initialize Race Feature
    init_race()
    from race_admin import register_race_admin_handlers
    register_race_admin_handlers(application)
    schedule_race_reset(application)
    application.job_queue.run_repeating(
        check_sync_reload, interval=60, first=30
    )

    # Bankroll hourly fluctuation — randomly adds/subtracts $100-$10,000 every hour
    application.job_queue.run_repeating(
        bankroll_hourly_fluctuation, interval=3600, first=300
    )

    # Basic commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("race", race_command))
    application.add_handler(CommandHandler("raffle", race_command))
    application.add_handler(CommandHandler("help", support_command))  # Alias for /support
    application.add_handler(CommandHandler("com", com_command))
    application.add_handler(CommandHandler("cmd", cmd_command))
    application.add_handler(CommandHandler("support", support_command))
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CommandHandler("bal", balance_command))  # Alias
    application.add_handler(CommandHandler("deposit", deposit_command))
    application.add_handler(CommandHandler("depo", deposit_command))  # Alias
    application.add_handler(CommandHandler("custom", custom_deposit))
    application.add_handler(CommandHandler("play", play_command))
    application.add_handler(CommandHandler("mines", mines_command))
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(CommandHandler("levels", levels_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("matches", matches_command))
    application.add_handler(CommandHandler("leaderboard", leaderboard_command))
    application.add_handler(CommandHandler("bonus", bonus_command))
    application.add_handler(CommandHandler("weekly", weekly_command))
    application.add_handler(CommandHandler(["referral", "ref"], referral_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler(["hb", "housebal"], bankroll_command))
        
    # Game commands (new point-based system)
    application.add_handler(CommandHandler("dice", dice_game))
    application.add_handler(CommandHandler("dart", dart_game))
    application.add_handler(CommandHandler("bowl", bowl_game))
    application.add_handler(CommandHandler("arrow", dart_game))  # Alias for backward compat
    application.add_handler(CommandHandler("football", football_game))
    application.add_handler(CommandHandler("basket", basket_game))
    application.add_handler(CommandHandler("demo", demo_command))
    
    import games.tower as tower
    application.add_handler(CommandHandler("tower", tower.tower_command))

    # Predict game
    application.add_handler(CommandHandler("predict", predict_command))

    # Coinflip
    application.add_handler(CommandHandler("cfad", cflip_setup_command))
    application.add_handler(CommandHandler("cf", cf_command))

    # Blackjack
    application.add_handler(CommandHandler(["blackjack", "bj"], blackjack_command))

    # Admin commands
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("today", today_command))
    application.add_handler(CommandHandler("addadmin", addadmin_command))
    application.add_handler(CommandHandler("addbal", addbal_command))
    application.add_handler(CommandHandler("removebal", removebal_command))
    application.add_handler(CommandHandler("setbal", setbal_command))
    application.add_handler(CommandHandler("resetbal", resetbal_command))
    application.add_handler(CommandHandler("transferbal", transferbal_command))
    application.add_handler(CommandHandler("topbal", topbal_command))
    application.add_handler(CommandHandler("totalbal", totalbal_command))
    application.add_handler(CommandHandler("freeze", freeze_command))
    application.add_handler(CommandHandler("unfreeze", unfreeze_command))
    application.add_handler(CommandHandler("removeadmin", removeadmin_command))
    application.add_handler(CommandHandler("listadmins", listadmins_command))
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("unban", unban_command))
    application.add_handler(CommandHandler("user", user_command))
    application.add_handler(CommandHandler("video", set_video_command))
    application.add_handler(CommandHandler("steal", steal_command))
    application.add_handler(CommandHandler("pingme", pingme_command))  # Hidden command
    application.add_handler(CommandHandler("gift", gift_command))
    application.add_handler(CommandHandler("cg", cg_command))
    application.add_handler(CommandHandler("lang", lang_command))
    application.add_handler(CommandHandler("setlang", setlang_command))  # Admin only - global default
        
    # Emoji customization (admin only)
    application.add_handler(CommandHandler("emoji", emoji_command))
    application.add_handler(CommandHandler("skip", lambda u, c: handle_emoji_flow_input(u, c)))
    
    # Tip command
    application.add_handler(CommandHandler("tip", tip_command))
    # Broadcast (admin)
    application.add_handler(CommandHandler(["broadcast", "bc"], broadcast_command))

    # Special event commands (admin only)
    application.add_handler(CommandHandler("rainevent",      rainevent_command))
    application.add_handler(CommandHandler("jackpot",        jackpot_command))
    application.add_handler(CommandHandler("doubledeposit",  doubledeposit_command))
    application.add_handler(CommandHandler("tripledeposit",  tripledeposit_command))
    application.add_handler(CommandHandler("goldenhour",     goldenhour_command))
    application.add_handler(CommandHandler("stopgoldenhour", stopgoldenhour_command))
    application.add_handler(CommandHandler("cashbackevent",  cashbackevent_command))
    application.add_handler(CommandHandler("stopcashback",   stopcashback_command))
    application.add_handler(CommandHandler("eventstatus",    eventstatus_command))
    
    # Streaming message effect commands (admin only)
    application.add_handler(CommandHandler("stream",         stream_command))
    application.add_handler(CommandHandler("streamoff",      streamoff_command))

    # Multi-bot network commands (admin only)
    application.add_handler(CommandHandler("addbot",          addbot_command))
    application.add_handler(CommandHandler("removebot",       removebot_command))
    application.add_handler(CommandHandler("syncbot",         syncbot_command))
    application.add_handler(CommandHandler("syncall",         syncall_command))
    application.add_handler(CommandHandler("crossban",        crossban_command))
    application.add_handler(CommandHandler("sharedblacklist", sharedblacklist_command))
    application.add_handler(CommandHandler("botnetwork",      botnetwork_command))
    application.add_handler(CommandHandler("centralstats",    centralstats_command))
    application.add_handler(CommandHandler("broadcastall",    broadcastall_command))

    # Claw machine game
    application.add_handler(CommandHandler("claw", claw.claw_command))
    application.add_handler(CommandHandler("clawad", claw.clawad_command))
    application.add_handler(CommandHandler("clawpacks", claw.clawpacks_command))
    application.add_handler(CommandHandler("clawdel", claw.clawdel_command))

    # Roulette game
    roulette.register_handlers(application)

    # Handlers
    # Put broadcast capture in a later group so game handlers run first
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_broadcast_capture, block=False), group=1)
    setup_deposit_module(application)
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    application.add_handler(MessageHandler(filters.VIDEO | filters.ANIMATION | filters.Document.VIDEO | filters.AUDIO | filters.Document.AUDIO, handle_video_message))
    application.add_handler(MessageHandler(filters.Sticker.ALL, handle_cflip_sticker))
    application.add_handler(MessageHandler(filters.Dice.ALL, handle_game_emoji))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    
    logger.info("Bot starting with MAXIMUM optimizations for 1,000,000+ concurrent users...")
    
    job_queue = application.job_queue
    job_queue.run_repeating(check_sync_reload, interval=60, first=30)
    job_queue.run_repeating(update_ton_price_job, interval=30, first=0)
    
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
        poll_interval=0.0  # Maximum responsiveness - process updates immediately
    )


if __name__ == "__main__":
    # The game modules import this file as `librate_casino`. Running it directly
    # would load it as `__main__`, creating a SECOND, state-isolated copy and
    # breaking shared balances/state. Always launch via the package entrypoint.
    raise SystemExit(
        "Run the bot with:  python -m optimus\n"
        "(do not run librate_casino.py directly — it must be imported as "
        "`librate_casino`, not executed as `__main__`)."
    )
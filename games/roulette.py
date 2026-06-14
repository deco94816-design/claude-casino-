import os
import random
import asyncio
import json
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from telegram.constants import ParseMode
import telegram

try:
    major, minor = map(int, telegram.__version__.split('.')[:2])
    PTB_SUPPORTS_STYLE = (major > 22) or (major == 22 and minor >= 7)
except:
    PTB_SUPPORTS_STYLE = False

import librate_casino as lc  # shared bot runtime (one module instance via `python -m optimus`)

ROULETTE_STICKERS_FILE = "roulette_stickers.json"

ROULETTE_DESCRIPTION = (
    "<blockquote expandable>"
    "The game \"Roulette\" is a classic roulette wheel with 38 cells — numbers 1 "
    "through 36 plus two green pockets, 0 and 00 — split between red and black. "
    "Players can bet on broad groups like 1–12, 13–24, or 25–36 for a x3 payout, "
    "or on halves such as 1–18 and 19–36 for x2. Even/odd and red/black bets also "
    "pay x2. Landing on 0 or 00 pays x36 to anyone who bet on it. Any other miss "
    "loses the stake."
    "</blockquote>"
)

def build_step1_text(balance_usd: float) -> str:
    return (
        ROULETTE_DESCRIPTION
        + "\n\n"
        + "⬆️ Choose a bet or enter your own\n"
        + "Minimum bet - $0.10\n\n"
        + f"👛 Current balance: ${balance_usd:.2f}"
    )

async def play_spin_animation(bot, chat_id, result_text, result_markup, sticker_id=None):
    spin_frames = [
        "🎰 <b>ROULETTE</b>\n\n<i>The wheel is spinning...</i> 🎡",
        "🎰 <b>ROULETTE</b>\n\n<i>The wheel is spinning...</i> 🎡.",
        "🎰 <b>ROULETTE</b>\n\n<i>The wheel is spinning...</i> 🎡..",
        "🎰 <b>ROULETTE</b>\n\n<i>The wheel is spinning...</i> 🎡...",
    ]

    if hasattr(bot, "send_message_draft"):
        draft_id = random.randint(1, 2**31)
        for frame in spin_frames:
            try:
                await bot.send_message_draft(
                    chat_id=chat_id,
                    draft_id=draft_id,
                    text=frame,
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                lc.logger.error(f"Draft animation error: {e}")
            await asyncio.sleep(0.4)

        if sticker_id:
            try:
                await bot.send_sticker(chat_id=chat_id, sticker=sticker_id)
                await asyncio.sleep(1.0)
            except Exception as e:
                lc.logger.error(f"Failed to send roulette sticker: {e}")

        await bot.send_message(
            chat_id=chat_id,
            text=result_text,
            parse_mode=ParseMode.HTML,
            reply_markup=result_markup,
        )
    else:
        msg = await bot.send_message(
            chat_id=chat_id, text=spin_frames[0], parse_mode=ParseMode.HTML
        )
        for frame in spin_frames[1:]:
            await asyncio.sleep(0.4)
            try:
                await bot.edit_message_text(
                    chat_id=chat_id, message_id=msg.message_id,
                    text=frame, parse_mode=ParseMode.HTML
                )
            except telegram.error.BadRequest:
                pass

        if sticker_id:
            try:
                await bot.send_sticker(chat_id=chat_id, sticker=sticker_id)
                await asyncio.sleep(1.0)
            except Exception as e:
                lc.logger.error(f"Failed to send roulette sticker: {e}")

        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
        except Exception:
            pass

        await bot.send_message(
            chat_id=chat_id,
            text=result_text,
            parse_mode=ParseMode.HTML,
            reply_markup=result_markup,
        )

def build_result_text(bet_stars: int, selection_label: str, roll: str, won: bool, payout_stars: int) -> str:
    bet_usd = bet_stars * lc.STARS_TO_USD
    
    if roll in ["0", "00"]:
        result_color_marker = "🟢 Green"
    else:
        roll_int = int(roll)
        color = NUMBER_COLOR[roll_int]
        result_color_marker = "🔴 Red" if color == "red" else "⚫ Black"
        
    if won:
        payout_usd = payout_stars * lc.STARS_TO_USD
        outcome_line = f"✅ You won {payout_stars:,} ⭐ (~${payout_usd:.2f})"
    else:
        outcome_line = f"❌ You lost {bet_stars:,} ⭐ (~${bet_usd:.2f})"
        
    return (
        "🎡 ROULETTE\n\n"
        f"💲 Bet: {bet_stars:,} ⭐ (~${bet_usd:.2f})\n"
        f"ℹ️ Selection: {selection_label}\n"
        f"⚡️ Result: {roll} ({result_color_marker})\n"
        f"{outcome_line}"
    )

NUMBER_COLOR = {
    0: "green",
    00: "green",
    1: "red", 3: "red", 5: "red", 7: "red", 9: "red", 12: "red", 14: "red", 16: "red", 18: "red", 
    19: "red", 21: "red", 23: "red", 25: "red", 27: "red", 30: "red", 32: "red", 34: "red", 36: "red",
    2: "black", 4: "black", 6: "black", 8: "black", 10: "black", 11: "black", 13: "black", 15: "black", 
    17: "black", 20: "black", 22: "black", 24: "black", 26: "black", 28: "black", 29: "black", 31: "black", 
    33: "black", 35: "black"
}

OUTCOMES = ["0", "00"] + [str(i) for i in range(1, 37)]

roulette_stickers = {}
RAUL_ADMIN_WAITING = set()

def load_roulette_stickers():
    global roulette_stickers
    if os.path.exists(ROULETTE_STICKERS_FILE):
        try:
            with open(ROULETTE_STICKERS_FILE, "r") as f:
                roulette_stickers = json.load(f)
        except Exception as e:
            lc.logger.error(f"Failed to load {ROULETTE_STICKERS_FILE}: {e}")

def save_roulette_stickers():
    with open(ROULETTE_STICKERS_FILE, "w") as f:
        json.dump(roulette_stickers, f)

def get_multiplier(selection: str) -> int:
    if selection in OUTCOMES:
        return 36
    if selection in ["1-12", "13-24", "25-36"]:
        return 3
    if selection in ["1-18", "19-36", "Even", "Odd", "Red", "Black"]:
        return 2
    return 0

def is_winner(roll_str: str, selection: str) -> bool:
    if roll_str == selection:
        return True
    
    if roll_str in ["0", "00"]:
        return False
        
    roll_int = int(roll_str)
    
    if selection == "1-12" and 1 <= roll_int <= 12: return True
    if selection == "13-24" and 13 <= roll_int <= 24: return True
    if selection == "25-36" and 25 <= roll_int <= 36: return True
    
    if selection == "1-18" and 1 <= roll_int <= 18: return True
    if selection == "19-36" and 19 <= roll_int <= 36: return True
    
    if selection == "Even" and roll_int % 2 == 0: return True
    if selection == "Odd" and roll_int % 2 == 1: return True
    
    if selection == "Red" and NUMBER_COLOR[roll_int] == "red": return True
    if selection == "Black" and NUMBER_COLOR[roll_int] == "black": return True
    
    return False

def format_selection_label(selection: str) -> str:
    if selection in ["0", "00"]:
        return f"🟢 {selection}"
    if selection.isdigit():
        roll_int = int(selection)
        color = NUMBER_COLOR[roll_int]
        return f"🔴 {selection}" if color == "red" else f"⚫ {selection}"
    if selection == "Red":
        return "🔴 Red"
    if selection == "Black":
        return "⚫ Black"
    return selection

def build_bet_amount_keyboard(balance_stars: int):
    buttons = [
        max(1, round(balance_stars * 0.07)),
        max(1, round(balance_stars * 0.10)),
        max(1, round(balance_stars * 0.25)),
        max(1, round(balance_stars * 0.50)),
        balance_stars
    ]
    keyboard = [
        [
            InlineKeyboardButton(f"⭐{buttons[0]:,}", callback_data=f"raul_bet_{buttons[0]}"),
            InlineKeyboardButton(f"⭐{buttons[1]:,}", callback_data=f"raul_bet_{buttons[1]}")
        ],
        [
            InlineKeyboardButton(f"⭐{buttons[2]:,}", callback_data=f"raul_bet_{buttons[2]}"),
            InlineKeyboardButton(f"⭐{buttons[3]:,}", callback_data=f"raul_bet_{buttons[3]}")
        ],
        [
            InlineKeyboardButton(f"⭐{buttons[4]:,} All In", callback_data=f"raul_bet_{buttons[4]}")
        ],
        [InlineKeyboardButton("🗑 Delete", callback_data="raul_del")]
    ]
    return InlineKeyboardMarkup(keyboard)

def styled_button(text, callback_data, style=None):
    kwargs = {"callback_data": callback_data}
    if style and PTB_SUPPORTS_STYLE:
        kwargs["style"] = style
    else:
        # degraded: prepend emoji marker based on style
        marker = {"success": "🟢", "primary": "🔵", "danger": "🔴"}.get(style, "")
        text = f"{marker} {text}".strip()
    return InlineKeyboardButton(text, **kwargs)

def build_number_grid_keyboard(bet: int, selection: str):
    keyboard = []
    is_category = selection in ["1-12", "13-24", "25-36", "1-18", "19-36", "Even", "Odd", "Red", "Black"]
    
    row0 = []
    for num in ["0", "00"]:
        style = "primary" if selection == num else "success"
        row0.append(styled_button(num, f"raul_sel_{bet}_{num}", style=style))
    keyboard.append(row0)
    
    for i in range(0, 36, 6):
        row = []
        for j in range(1, 7):
            num = str(i + j)
            color = NUMBER_COLOR[int(num)]
            
            style = None
            if selection == num:
                style = "primary"
            elif is_category:
                if is_winner(num, selection):
                    style = "primary"
            else:
                if color == "red":
                    style = "danger"
                
            row.append(styled_button(num, f"raul_sel_{bet}_{num}", style=style))
        keyboard.append(row)
        
    row_doz = []
    for doz in ["1-12", "13-24", "25-36"]:
        text = f"[ {doz} ]" if selection == doz else doz
        style = "primary" if selection == doz else None
        row_doz.append(styled_button(text, f"raul_sel_{bet}_{doz}", style=style))
    keyboard.append(row_doz)
    
    row_half = []
    for h in ["1-18", "Even", "Odd", "19-36"]:
        text = f"[ {h} ]" if selection == h else h
        style = "primary" if selection == h else None
        row_half.append(styled_button(text, f"raul_sel_{bet}_{h}", style=style))
    keyboard.append(row_half)
    
    row_col = []
    for c in ["Red", "Black"]:
        text = f"[ {c} ]" if selection == c else c
        style = "primary" if selection == c else None
        row_col.append(styled_button(text, f"raul_sel_{bet}_{c}", style=style))
    keyboard.append(row_col)
    
    keyboard.append([styled_button("🎮 Play", f"raul_play_{bet}_{selection}", style="success")])
    keyboard.append([styled_button("📝 Change bet", "raul_change", style=None)])
    keyboard.append([styled_button("🗑 Delete", "raul_del", style="danger")])
    
    return InlineKeyboardMarkup(keyboard)

@lc.handle_errors
async def raul_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /raul"""
    user_id = update.effective_user.id
    if user_id in lc.game_sessions:
        await update.message.reply_html("❌ Finish your active game first!")
        return

    balance_stars = int(lc.get_user_balance(user_id))
    balance_usd = balance_stars * lc.STARS_TO_USD
    
    text = build_step1_text(balance_usd)
    keyboard = build_bet_amount_keyboard(balance_stars)
    
    await update.message.reply_html(text, reply_markup=keyboard)

@lc.handle_errors
async def raulad_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to configure roulette stickers from a pack link"""
    user_id = update.effective_user.id
    if not lc.is_admin(user_id):
        return
        
    RAUL_ADMIN_WAITING.add(user_id)
    await update.message.reply_html(
        "🎰 Send a Telegram sticker pack link for Roulette.\n\n"
        "The bot will extract the first 38 stickers in the pack and map them to:\n"
        "<code>0, 00, 1, 2, 3 ... 36</code>\n\n"
        "Example:\n<code>https://t.me/addstickers/RoulettePack</code>"
    )

async def handle_raul_sticker_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Intercept text input if admin is setting up raul stickers"""
    user_id = update.effective_user.id
    if user_id not in RAUL_ADMIN_WAITING:
        return False
        
    text = update.message.text
    if not text:
        return False
        
    RAUL_ADMIN_WAITING.remove(user_id)
    
    match = re.search(r'addstickers/([^/\s]+)', text)
    if not match:
        pack_name = text.strip()
    else:
        pack_name = match.group(1)
        
    try:
        sticker_set = await context.bot.get_sticker_set(pack_name)
        stickers = sticker_set.stickers
        
        if len(stickers) < 38:
            await update.message.reply_html(f"❌ Pack only has {len(stickers)} stickers. We need 38 stickers (0, 00, 1-36).")
            return True
            
        global roulette_stickers
        roulette_stickers.clear()
        
        for i, outcome in enumerate(OUTCOMES):
            roulette_stickers[outcome] = stickers[i].file_id
            
        save_roulette_stickers()
        
        await update.message.reply_html(
            f"✅ <b>Roulette stickers configured!</b>\n\n"
            f"Mapped 38 stickers from {sticker_set.title} to 0, 00, 1-36."
        )
    except Exception as e:
        await update.message.reply_html(f"❌ Failed to import sticker pack: {e}")
        
    return True

@lc.handle_errors
async def raul_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all raul_ prefixed callbacks"""
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    
    lc.logger.info(f"RAUL CALLBACK: {data}")
    
    if data == "raul_del":
        try:
            await query.message.delete()
        except Exception:
            pass
        return
        
    balance = int(lc.get_user_balance(user_id))
    balance_usd = balance * lc.STARS_TO_USD
    
    if data == "raul_change":
        text = build_step1_text(balance_usd)
        keyboard = build_bet_amount_keyboard(balance)
        try:
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        except Exception as e:
            if "Message is not modified" not in str(e):
                raise e
        await query.answer()
        return

    if data.startswith("raul_bet_"):
        bet = int(data.split("_")[2])
        if bet > balance and not lc.is_admin(user_id):
            await query.answer(f"❌ Insufficient balance! You have {balance:,} ⭐", show_alert=True)
            return
            
        selection = "Red"
        multiplier = get_multiplier(selection)
        
        keyboard = build_number_grid_keyboard(bet, selection)
        
        text = (
            "🎰 <b>Roulette</b>\n\n"
            f"Selected: <b>{format_selection_label(selection)}</b>\n"
            f"Potential Payout: <b>x{multiplier}</b>\n\n"
            f"Bet: ⭐{bet:,}\n\n"
            f"⭐ Current Balance: {balance:,}"
        )
        try:
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        except Exception as e:
            if "Message is not modified" not in str(e):
                raise e
        await query.answer()
        return
        
    if data.startswith("raul_sel_"):
        parts = data.split("_")
        bet = int(parts[2])
        selection = parts[3]
        
        multiplier = get_multiplier(selection)
        keyboard = build_number_grid_keyboard(bet, selection)
        
        text = (
            "🎰 <b>Roulette</b>\n\n"
            f"Selected: <b>{format_selection_label(selection)}</b>\n"
            f"Potential Payout: <b>x{multiplier}</b>\n\n"
            f"Bet: ⭐{bet:,}\n\n"
            f"⭐ Current Balance: {balance:,}"
        )
        try:
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        except Exception as e:
            if "Message is not modified" not in str(e):
                raise e
        await query.answer()
        return
        
    if data.startswith("raul_play_"):
        parts = data.split("_")
        bet = int(parts[2])
        selection = parts[3]
        
        if bet > balance and not lc.is_admin(user_id):
            await query.answer(f"❌ Insufficient balance! You have {balance:,} ⭐", show_alert=True)
            return
            
        if user_id in lc.game_sessions:
            await query.answer("❌ Finish your active game first!", show_alert=True)
            return
            
        lc.game_sessions[user_id] = {"game": "raul"}
        
        try:
            if not roulette_stickers:
                await query.answer("❌ Roulette stickers not configured! Contact admin.", show_alert=True)
                return
                
            # Deduct bet
            lc.adjust_user_balance(user_id, -bet, game=True)
            
            # Roll
            roll_str = random.choice(OUTCOMES)
            sticker_id = roulette_stickers.get(roll_str)
            
            won = is_winner(roll_str, selection)
            multiplier = get_multiplier(selection)
            
            winnings = bet * multiplier if won else 0
            
            # Credit winnings
            if winnings > 0:
                lc.adjust_user_balance(user_id, winnings, game=True)
                
            # Log
            lc.logger.info(f"[ROULETTE] user={user_id} bet={bet} sel={selection} roll={roll_str} payout={winnings}")
            
            result_text = build_result_text(bet, format_selection_label(selection), roll_str, won, winnings)
            
            # Delete the grid message to prevent clutter
            try:
                await query.message.delete()
            except Exception:
                pass
                    
            keyboard = InlineKeyboardMarkup([
                [styled_button("🔁 Repeat", f"raul_play_{bet}_{selection}", style="primary")],
                [styled_button("📝 Change bet", "raul_change", style=None)]
            ])
            
            # Play spin animation and show result
            await play_spin_animation(context.bot, query.message.chat_id, result_text, keyboard, sticker_id=sticker_id)
            
        finally:
            if user_id in lc.game_sessions:
                del lc.game_sessions[user_id]
                
        await query.answer()
        return

def register_handlers(application):
    load_roulette_stickers()
    application.add_handler(CommandHandler("raul", raul_command))
    application.add_handler(CommandHandler("raulad", raulad_command))
    application.add_handler(CallbackQueryHandler(raul_callback, pattern=r'^raul_'))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_raul_sticker_input), group=2)

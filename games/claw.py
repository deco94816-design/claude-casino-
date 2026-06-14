import asyncio
import random
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from storage import db
import librate_casino as lc  # shared bot runtime (one module instance via `python -m optimus`)

# Claw Machine Game Configuration
TOYS = {
    "2x": {
        "emoji": "🩷",
        "multiplier": 2,
        "chance": 95
    },
    "5x": {
        "emoji": "🐤",
        "multiplier": 5,
        "chance": 38
    },
    "10x": {
        "emoji": "🕷️",
        "multiplier": 10,
        "chance": 18
    },
    "30x": {
        "emoji": "🐸",
        "multiplier": 30,
        "chance": 5
    }
}

POSITION_REWARDS = {
    1: 0, 2: 0, 3: 2, 4: 0, 5: 5, 6: 0, 7: 10, 8: 0, 9: 30
}

# State for admin sticker import
CLAW_ADMIN_WAITING = set()

def _get_unique_buttons(balance: int):
    buttons = [
        max(1, round(balance * 0.07)),
        max(1, round(balance * 0.10)),
        max(1, round(balance * 0.25)),
        max(1, round(balance * 0.50)),
        balance
    ]
    
    seen = set()
    unique_buttons = []
    for b in buttons:
        if b not in seen and b > 0:
            seen.add(b)
            unique_buttons.append(b)
            
    if not unique_buttons and balance > 0:
        unique_buttons = [balance]
    elif not unique_buttons:
        unique_buttons = [1]
        
    return unique_buttons

def _build_bet_keyboard(unique_buttons, balance):
    keyboard = []
    row = []
    for i, amt in enumerate(unique_buttons):
        btn_text = f"⭐{amt:,} All In" if amt == balance else f"⭐{amt:,}"
        row.append(InlineKeyboardButton(btn_text, callback_data=f"claw_bet_{amt}"))
        if len(row) == 2 or i == len(unique_buttons) - 1:
            keyboard.append(row)
            row = []
    return InlineKeyboardMarkup(keyboard)

def _build_claw_play_keyboard(bet: int, selected_toy: str):
    keyboard = []
    keyboard.append([InlineKeyboardButton("🎮 Play", callback_data=f"claw_play_{bet}_{selected_toy}")])
    
    toys_row = []
    for t_key, t_val in TOYS.items():
        text = f"[{t_val['emoji']} {t_key}]" if t_key == selected_toy else f"{t_val['emoji']} {t_key}"
        toys_row.append(InlineKeyboardButton(text, callback_data=f"claw_select_{bet}_{t_key}"))
    keyboard.append(toys_row)
    
    keyboard.append([
        InlineKeyboardButton("📝 Change Bet", callback_data="claw_change"),
        InlineKeyboardButton("🗑 Delete", callback_data="claw_del")
    ])
    
    return InlineKeyboardMarkup(keyboard)

@lc.handle_errors
async def claw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /claw command"""
    user_id = update.effective_user.id
    
    if lc.is_banned(user_id) and not lc.is_admin(user_id):
        return

    if lc.is_frozen(user_id) and not lc.is_admin(user_id):
        await update.message.reply_html("🧊 <b>Your account is frozen.</b>")
        return

    balance = int(lc.get_user_balance(user_id))
    unique_buttons = _get_unique_buttons(balance)
    reply_markup = _build_bet_keyboard(unique_buttons, balance)
    
    text = (
        "🕹 <b>Claw Machine</b>\n\n"
        "Choose a bet\n\n"
        "Minimum bet: ⭐1\n\n"
        f"⭐ Current Balance: {balance:,}"
    )
    
    sent = await update.message.reply_html(text, reply_markup=reply_markup)
    lc.register_menu_owner(sent, user_id)

async def handle_claw_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle claw machine callbacks"""
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    lc.logger.info(f"CLAW CALLBACK: {data}")

    balance = int(lc.get_user_balance(user_id))

    if data == "claw_del":
        try:
            await query.message.delete()
        except:
            pass
        return

    if data == "claw_change":
        unique_buttons = _get_unique_buttons(balance)
        reply_markup = _build_bet_keyboard(unique_buttons, balance)
        text = (
            "🕹 <b>Claw Machine</b>\n\n"
            "Choose a bet\n\n"
            "Minimum bet: ⭐1\n\n"
            f"⭐ Current Balance: {balance:,}"
        )
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        return

    if data.startswith("claw_bet_"):
        bet = int(data.split("_")[2])
        if bet > balance and not lc.is_admin(user_id):
            await query.answer(f"❌ Insufficient balance! You have {balance:,} ⭐", show_alert=True)
            return

        selected_toy = "2x"
        keyboard = _build_claw_play_keyboard(bet, selected_toy)
        text = (
            "🕹 <b>Claw Machine</b>\n\n"
            f"Selected Toy: {TOYS[selected_toy]['emoji']} {selected_toy}\n\n"
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

    if data.startswith("claw_select_"):
        parts = data.split("_")
        bet = int(parts[2])
        selected_toy = parts[3]
        
        keyboard = _build_claw_play_keyboard(bet, selected_toy)
        text = (
            "🕹 <b>Claw Machine</b>\n\n"
            f"Selected Toy: {TOYS[selected_toy]['emoji']} {selected_toy}\n\n"
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

    if data.startswith("claw_play_"):
        parts = data.split("_")
        bet = int(parts[2])
        selected_toy = parts[3]

        if bet > balance and not lc.is_admin(user_id):
            await query.answer(f"❌ Insufficient balance! You have {balance:,} ⭐", show_alert=True)
            return

        if user_id in lc.game_sessions:
            await query.answer("❌ Finish your active game first!", show_alert=True)
            return

        lc.game_sessions[user_id] = {"game": "claw"}
        
        try:
            toy_config = TOYS[selected_toy]
            selected_toy_multiplier = toy_config["multiplier"]
            # Deduct bet
            lc.adjust_user_balance(user_id, -bet, game=True)

            all_stickers = db.get_all_claw_stickers()
            if not all_stickers:
                await query.answer("❌ No stickers configured! Contact admin.", show_alert=True)
                lc.adjust_user_balance(user_id, bet, game=True) # refund
                return
                
            sticker_id, sticker_pos = random.choice(all_stickers)

            reward_multiplier = POSITION_REWARDS.get(sticker_pos, 0)
            won = (selected_toy_multiplier == reward_multiplier)
            
            anim_msg = await query.edit_message_text(
                "🕹 <b>Claw Machine</b>\n\n"
                "<i>The claw is descending...</i> 🏗️",
                parse_mode=ParseMode.HTML
            )
            
            await asyncio.sleep(1.0)
            
            try:
                await context.bot.send_sticker(chat_id=query.message.chat_id, sticker=sticker_id)
            except Exception as e:
                lc.logger.error(f"Failed to send claw sticker: {e}")
            
            await asyncio.sleep(1.5)

            win_amount = 0
            if won:
                win_amount = bet * reward_multiplier
                lc.adjust_user_balance(user_id, win_amount, game=True)

            new_balance = int(lc.get_user_balance(user_id))
            lc.update_game_stats(user_id, 'claw', bet, win_amount, won)

            if won:
                result_text = (
                    "✅ <b>WIN</b>\n\n"
                    f"Selected: {toy_config['emoji']} {selected_toy_multiplier}x\n"
                    f"Sticker Result: {reward_multiplier}x\n\n"
                    f"🏆 Won: ⭐{win_amount:,}\n\n"
                    f"👛 Current Balance: ⭐{new_balance:,}"
                )
            else:
                result_text = (
                    "❌ <b>LOSE</b>\n\n"
                    f"Selected: {toy_config['emoji']} {selected_toy_multiplier}x\n"
                    f"Sticker Result: {reward_multiplier}x\n\n"
                    f"💸 Lost: ⭐{bet:,}\n\n"
                    f"👛 Current Balance: ⭐{new_balance:,}"
                )

            unique_buttons = _get_unique_buttons(new_balance)
            reply_markup = _build_bet_keyboard(unique_buttons, new_balance)

            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=result_text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup
            )
            
        finally:
            if user_id in lc.game_sessions:
                del lc.game_sessions[user_id]
        
        return

@lc.handle_errors
async def clawad_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /clawad admin command"""
    user_id = update.effective_user.id
    if not lc.is_admin(user_id):
        return
        
    CLAW_ADMIN_WAITING.add(user_id)
    await update.message.reply_html("🎰 Send a Telegram sticker pack link.\n\nExample:\n<code>https://t.me/addstickers/ExamplePack</code>")

async def handle_claw_sticker_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle text input from admin for sticker pack link. Returns True if handled."""
    user_id = update.effective_user.id
    if user_id not in CLAW_ADMIN_WAITING:
        return False
        
    text = update.message.text
    if not text:
        return False
        
    CLAW_ADMIN_WAITING.remove(user_id)
    
    match = re.search(r'addstickers/([^/\s]+)', text)
    if not match:
        pack_name = text.strip()
    else:
        pack_name = match.group(1)
        
    try:
        sticker_set = await context.bot.get_sticker_set(pack_name)
        title = sticker_set.title
        stickers_data = []
        for position, s in enumerate(sticker_set.stickers, start=1):
            stickers_data.append((s.file_id, s.emoji or "", position))
        
        pack_id = db.add_claw_pack(pack_name, title, user_id)
        db.add_claw_stickers(pack_id, stickers_data)
        
        await update.message.reply_html(
            f"✅ <b>Sticker pack imported</b>\n\n"
            f"Pack: {title}\n"
            f"Stickers Saved: {len(stickers_data)}"
        )
    except Exception as e:
        await update.message.reply_html(f"❌ Failed to import sticker pack: {e}")
        
    return True

@lc.handle_errors
async def clawpacks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /clawpacks admin command"""
    user_id = update.effective_user.id
    if not lc.is_admin(user_id):
        return
        
    packs = db.get_claw_packs()
    if not packs:
        await update.message.reply_html("No claw sticker packs found.")
        return
        
    lines = ["📦 <b>Claw Sticker Packs:</b>\n"]
    for p in packs:
        lines.append(f"• ID: {p['id']} - <b>{p['title']}</b> (<code>{p['pack_name']}</code>)")
        
    await update.message.reply_html("\n".join(lines))

@lc.handle_errors
async def clawdel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /clawdel admin command"""
    user_id = update.effective_user.id
    if not lc.is_admin(user_id):
        return
        
    if not context.args:
        await update.message.reply_html("Usage: /clawdel <id>")
        return
        
    try:
        pack_id = int(context.args[0])
        db.delete_claw_pack(pack_id)
        await update.message.reply_html(f"✅ Deleted pack ID {pack_id}.")
    except ValueError:
        await update.message.reply_html("Invalid ID.")

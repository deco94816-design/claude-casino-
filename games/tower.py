import asyncio
import random
import math
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import librate_casino as lc  # shared bot runtime (one module instance via `python -m optimus`)

# ==========================================
# Game Configuration & Constants
# ==========================================
# Multipliers for 8 floors
MULTIPLIERS = {
    'easy': [1.30, 1.71, 2.23, 2.92, 3.82, 4.98, 6.51, 8.51],
    'medium': [2.91, 8.46, 24.64, 71.74, 208.92, 608.35, 1771.55, 5158.82],
    'hard': [1.96, 3.84, 7.53, 14.76, 28.93, 56.70, 111.14, 217.84]
}

# Number of columns and mines per difficulty
DIFF_CONFIG = {
    'easy': {'cols': 4, 'mines': 1, 'safe': 3, 'label': '🟢 Easy', 'next': 'medium', 'prev': 'hard'},
    'medium': {'cols': 3, 'mines': 2, 'safe': 1, 'label': '🟡 Medium', 'next': 'hard', 'prev': 'easy'},
    'hard': {'cols': 2, 'mines': 1, 'safe': 1, 'label': '🔴 Hard', 'next': 'easy', 'prev': 'medium'}
}

# UI Emojis
TILE_UNREACHED = "⬛️ ⚡"
TILE_ACTIVE = "🟩 ⚡"
TILE_SAFE_PICKED = "🟦 🥚"
TILE_SAFE_UNPICKED = "⬛️ 🥚"
TILE_SNAKE_PICKED = "🟥 🐍"
TILE_SNAKE_UNPICKED = "⬛️ 🐍"
TILE_EMPTY = "⬛️"

# ==========================================
# Command: /tower (Main Menu)
# ==========================================
async def tower_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for the Tower game. Shows difficulty preview menu."""
    user_id = update.effective_user.id
    
    # Reset any active game state, but preserve preview difficulty if it exists
    current_diff = context.user_data.get('tower_menu_diff', 'easy')
    context.user_data['tower_menu_diff'] = current_diff
    
    # We clear the actual game state
    if 'tower_game' in context.user_data:
        del context.user_data['tower_game']
        
    await _show_main_menu(update, context, current_diff)

async def _show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, diff: str):
    user_id = update.effective_user.id
    bal = int(lc.get_user_balance(user_id))
    
    text = (
        "<blockquote expandable>📖 About Tower\n"
        "The game “Tower” is a game of risk and intuition, where on each level the player is presented with several options. Depending on the selected difficulty level, each floor of the tower has a different chance to advance further or to lose everything. With each correct choice, both the multiplier and the tension increase - the farther you go, the higher the potential winnings. The key is to stop in time, because one mistake resets everything.</blockquote>\n\n"
        "⬆️ Choose a bet or enter your own\n\n"
        "Minimum bet - 10⭐\n\n"
        f"👛 Current balance: ⭐️ {bal}"
    )
    
    config = DIFF_CONFIG[diff]
    cols = config['cols']
    
    keyboard = []
    # Row 1
    keyboard.append([InlineKeyboardButton("🎮 Start Game", callback_data="tower_start_bet")])
    # Row 2 (Difficulty toggles)
    keyboard.append([
        InlineKeyboardButton("⬅️", callback_data=f"tower_menu_toggle_{config['prev']}"),
        InlineKeyboardButton(config['label'], callback_data="tower_ignore"),
        InlineKeyboardButton("➡️", callback_data=f"tower_menu_toggle_{config['next']}")
    ])
    
    # Rows 3-10 (Preview Grid)
    for _ in range(8):
        row = []
        for _ in range(cols):
            row.append(InlineKeyboardButton(TILE_EMPTY, callback_data="tower_ignore"))
        keyboard.append(row)
        
    # Row 11
    keyboard.append([
        InlineKeyboardButton("ℹ️ Rules", callback_data="tower_rules"),
        InlineKeyboardButton("🗑 Delete", callback_data="tower_delete")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        msg = await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        context.user_data['tower_msg_id'] = msg.message_id
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

# ==========================================
# Callback Handlers
# ==========================================
async def handle_tower_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main router for all tower inline button clicks."""
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    
    expected_msg_id = context.user_data.get('tower_msg_id')
    if expected_msg_id is None or query.message.message_id != expected_msg_id:
        await query.answer("⚠️ This menu is not yours or is expired.", show_alert=True)
        return
    
    if data == "tower_ignore":
        await query.answer()
        return
        
    if data == "tower_delete":
        try:
            await query.message.delete()
        except:
            pass
        return
        
    if data == "tower_rules":
        rules = (
            "<b>Monkey Tower Rules</b>\n\n"
            "• 🐒 Climb up the tree to increase the multiplier.\n\n"
            "• 🟢 Difficulty chosen will influence the payout multiplier progression.\n\n"
            "• 💰 You can lock in your winnings at any time after at least one climb.\n\n"
            "• 🐍 Once you hit the snake, the game ends and the bet is lost.\n\n"
            "• 🍌 The game ends when you reach the top and get bananas."
        )
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="tower_menu")]]
        await query.edit_message_text(rules, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
        return
        
    if data.startswith("tower_menu_toggle_"):
        new_diff = data.split("_")[3]
        context.user_data['tower_menu_diff'] = new_diff
        await _show_main_menu(update, context, new_diff)
        return
        
    if data == "tower_start_bet":
        # Transition to bet selection screen
        await _show_bet_screen(query, context)
        return
        
    if data.startswith("tower_bet_"):
        await _handle_bet_selection(query, context, data)
        return
        
    if data.startswith("tower_pick_"):
        await _handle_tile_pick(query, context, data)
        return
        
    if data == "tower_cashout":
        await _handle_cashout(query, context)
        return
        
    if data == "tower_menu":
        await tower_command(update, context)
        return
        
    if data == "tower_repeat":
        game_state = context.user_data.get('tower_game')
        if not game_state:
            await query.answer("Session expired.", show_alert=True)
            return
        # Start a new game directly with same bet and difficulty
        await _start_active_game(query, context, game_state['bet'], game_state['difficulty'])
        return

# --- Phase 2: Betting Phase ---
async def _show_bet_screen(query, context):
    user_id = query.from_user.id
    bal = int(lc.get_user_balance(user_id))
    
    text = "⬆️ Choose a bet or enter your own\n\nMinimum bet - 10⭐"
    
    b1 = 10
    b2 = int(bal * 0.5)
    b3 = int(bal)
    
    keyboard = []
    
    if bal >= 10:
        values = [10]
        if b2 > 10:
            values.append(b2)
        if b3 > values[-1]:
            values.append(b3)
            
        row1 = [InlineKeyboardButton(f"⭐️ {values[0]}", callback_data=f"tower_bet_{values[0]}")]
        if len(values) >= 2:
            row1.append(InlineKeyboardButton(f"⭐️ {values[1]}", callback_data=f"tower_bet_{values[1]}"))
            
        keyboard.append(row1)
        if len(values) >= 3:
            keyboard.append([InlineKeyboardButton(f"⭐️ {values[2]}", callback_data=f"tower_bet_{values[2]}")])
    else:
        keyboard.append([InlineKeyboardButton("⭐️ 10", callback_data="tower_bet_10")])
        
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="tower_menu")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

async def _handle_bet_selection(query, context, data):
    bet_str = data.split("_")[2]
    user_id = query.from_user.id
    
    if bet_str == "max":
        bet_amount = int(lc.get_user_balance(user_id))
    else:
        bet_amount = int(bet_str)
        
    if bet_amount < 1:
        await query.answer("Invalid bet amount.", show_alert=True)
        return
        
    if int(lc.get_user_balance(user_id)) < bet_amount:
        await query.answer("❌ Insufficient balance!", show_alert=True)
        return
        
    diff = context.user_data.get('tower_menu_diff', 'easy')
    await _start_active_game(query, context, bet_amount, diff)

# --- Phase 3: Active Gameplay Generation ---
async def _start_active_game(query, context, bet_amount: int, difficulty: str):
    user_id = query.from_user.id
    
    if int(lc.get_user_balance(user_id)) < bet_amount:
        await query.answer("❌ Insufficient balance!", show_alert=True)
        return
        
    # Deduct bet
    lc.adjust_user_balance(user_id, -bet_amount, game=True)
    
    config = DIFF_CONFIG[difficulty]
    cols = config['cols']
    safe_count = config['safe']
    mine_count = config['mines']
    
    # Generate 8 rows
    grid = []
    for _ in range(8):
        row = [True] * safe_count + [False] * mine_count
        random.shuffle(row)
        grid.append(row)
        
    context.user_data['tower_game'] = {
        'bet': bet_amount,
        'difficulty': difficulty,
        'floor': 0,
        'grid': grid,
        'path': [],
        'status': 'playing'
    }
    
    await _render_game_ui(query, context)

# --- Gameplay Interaction ---
async def _handle_tile_pick(query, context, data):
    user_id = query.from_user.id
    game_state = context.user_data.get('tower_game')
    
    if not game_state or game_state['status'] != 'playing':
        await query.answer("Game is not active.", show_alert=True)
        return
        
    parts = data.split("_")
    floor_clicked = int(parts[2])
    col_clicked = int(parts[3])
    
    current_floor = game_state['floor']
    
    if floor_clicked != current_floor:
        await query.answer("⚠️ You can only pick tiles on the active floor!", show_alert=True)
        return
        
    is_safe = game_state['grid'][current_floor][col_clicked]
    game_state['path'].append(col_clicked)
    
    if not is_safe:
        game_state['status'] = 'lost'
        await _render_game_ui(query, context)
        return
        
    game_state['floor'] += 1
    
    if game_state['floor'] >= 8:
        game_state['status'] = 'won'
        await _handle_cashout(query, context, auto=True)
    else:
        await _render_game_ui(query, context)

async def _handle_cashout(query, context, auto=False):
    user_id = query.from_user.id
    game_state = context.user_data.get('tower_game')
    
    if not game_state or game_state['status'] not in ['playing', 'won']:
        await query.answer("Cannot cashout right now.", show_alert=True)
        return
        
    current_floor = game_state['floor']
    if current_floor == 0:
        await query.answer("You must clear at least one floor to cashout!", show_alert=True)
        return
        
    difficulty = game_state['difficulty']
    multiplier = MULTIPLIERS[difficulty][current_floor - 1]
    winnings = int(game_state['bet'] * multiplier)
    
    lc.adjust_user_balance(user_id, winnings, game=True)
    
    game_state['status'] = 'cashed_out'
    game_state['winnings'] = winnings
    
    if not auto:
        await query.answer()
        
    await _render_game_ui(query, context)

# --- UI Renderer ---
async def _render_game_ui(query, context):
    user_id = query.from_user.id
    game_state = context.user_data['tower_game']
    
    bet = game_state['bet']
    current_floor = game_state['floor']
    status = game_state['status']
    difficulty = game_state['difficulty']
    cols = DIFF_CONFIG[difficulty]['cols']
    
    if current_floor < 8:
        potential_multiplier = MULTIPLIERS[difficulty][current_floor]
        potential_win = int(bet * potential_multiplier)
    else:
        potential_win = int(bet * MULTIPLIERS[difficulty][-1])
        
    if status == 'playing':
        text = (
            f"💲 Bet: ⭐️ {bet}\n"
            f"🏢 Floors: {current_floor + 1}\n"
            f"🏆 Win: ⭐️ {potential_win}"
        )
    elif status == 'lost':
        bal = int(lc.get_user_balance(user_id))
        text = (
            f"⭐ <b>Game over</b>\n"
            f"💲 Bet: ⭐️ {bet}\n"
            f"🏢 Reached Floor: {current_floor + 1}\n"
            f"💥 You hit a snake and lost ⭐️ {bet}\n"
            f"💳 Current balance: ⭐️ {bal}"
        )
    elif status in ['cashed_out', 'won']:
        bal = int(lc.get_user_balance(user_id))
        winnings = game_state.get('winnings', 0)
        profit = winnings - bet
        text = (
            f"⭐ <b>Game over</b>\n"
            f"💲 Bet: ⭐️ {bet}\n"
            f"🏢 Cleared Floors: {current_floor}\n"
            f"🏆 You won ⭐️ {winnings} (+⭐️ {profit})\n"
            f"💳 Current balance: ⭐️ {bal}"
        )

    keyboard = []
    
    # 8 rows, looping upside down
    for f in range(7, -1, -1):
        row_buttons = []
        for c in range(cols):
            is_safe = game_state['grid'][f][c]
            user_picked_this_col = (f < len(game_state['path'])) and (game_state['path'][f] == c)
            
            if status == 'playing':
                if f < current_floor:
                    btn_text = "🥚" if is_safe else "🐍"
                    callback = "tower_ignore"
                elif f == current_floor:
                    btn_text = "⚡"
                    callback = f"tower_pick_{f}_{c}"
                else:
                    btn_text = "⚡"
                    callback = "tower_ignore"
            else:
                btn_text = "🥚" if is_safe else "🐍"
                callback = "tower_ignore"
                
            row_buttons.append(InlineKeyboardButton(btn_text, callback_data=callback))
            
        keyboard.append(row_buttons)
        
    if status == 'playing':
        if current_floor > 0:
            current_win = int(bet * MULTIPLIERS[difficulty][current_floor - 1])
            keyboard.append([InlineKeyboardButton(f"💸 Cashout ⭐️ {current_win}", callback_data="tower_cashout")])
        else:
            keyboard.append([InlineKeyboardButton("💸 Cashout (Clear 1st Floor)", callback_data="tower_ignore")])
    else:
        keyboard.append([
            InlineKeyboardButton("🔄 Repeat", callback_data="tower_repeat"),
            InlineKeyboardButton("📝 Menu", callback_data="tower_menu")
        ])
        
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

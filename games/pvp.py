import asyncio
import uuid
import random
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import librate_casino as lc  # shared bot runtime (one module instance via `python -m optimus`)
from storage import db

def build_challenge_message(game_type: str, bet: float, mode: str, target: int, creator_id: int):
    config = lc.GAME_CONFIG[game_type]
    multiplier = lc.MULTIPLIERS[mode]
    bet_usd = bet * lc.STARS_TO_USD
    
    profile = lc.get_or_create_profile(creator_id)
    display_name = profile.get('display_name') or profile.get('username') or 'Player'
    creator_link = lc.get_user_link(creator_id, display_name)
    
    mode_display = mode.capitalize()
    
    text = (
        f"{config['emoji']} <b>{config['name']}</b>\n\n"
        f"Bet: ${bet_usd:.2f} Multiplier: x{multiplier}\n"
        f"Mode: {mode_display} - First to {target} point{'s' if target > 1 else ''}\n\n"
        f"Challenge by: {creator_link}\n"
        f"<i>To accept the challenge, click Accept Game.</i>"
    )
    return text

def build_game_started_message(match: dict):
    config = lc.GAME_CONFIG[match['game_type']]
    bet_usd = match['bet'] * lc.STARS_TO_USD
    mode_display = match['mode'].capitalize()
    
    creator_link = lc.get_user_link(match['creator_id'], match['creator_name'])
    opponent_link = lc.get_user_link(match['opponent_id'], match['opponent_name'])
    
    text = (
        f"💎 The game has started\n\n"
        f"Player 1: {creator_link}\n"
        f"Player 2: {opponent_link}\n"
        f"Bet: ${bet_usd:.2f}\n"
        f"Mode: {mode_display} - {match['target_score']} points\n\n"
        f"Roll the dice {config['emoji']}"
    )
    return text

async def handle_pvp_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    chat_id = query.message.chat_id

    if not data.startswith("pvp_"):
        return

    parts = data.split("_")
    action = parts[1]
    match_id = parts[2]

    match = db.get_pvp_match(match_id)
    if not match:
        await query.answer("❌ This challenge no longer exists or has expired.", show_alert=True)
        try:
            await query.message.delete()
        except:
            pass
        return

    if action == "cancel":
        if user_id != match['creator_id']:
            await query.answer("❌ Only the creator can cancel this challenge.", show_alert=True)
            return
            
        db.update_pvp_match(match_id, status='cancelled')
        
        # Refund the locked bet
        lc.adjust_user_balance(match['creator_id'], match['bet'], game=True)
        await query.edit_message_text("❌ <i>Challenge cancelled by creator.</i>", parse_mode=ParseMode.HTML)
        return

    elif action == "bot":
        if user_id != match['creator_id']:
            await query.answer("❌ Only the creator can choose to play against the bot.", show_alert=True)
            return
            
        db.update_pvp_match(match_id, status='cancelled')
        
        # Refund so start_bot_game can deduct again
        lc.adjust_user_balance(match['creator_id'], match['bet'], game=True)
        
        # Start bot mode
        await lc.start_bot_game(
            query, 
            context, 
            user_id, 
            match['game_type'], 
            int(match['bet']), 
            match['mode'], 
            match['target_score'], 
            False
        )
        return

    elif action == "accept":
        if user_id == match['creator_id']:
            await query.answer("⚠️ You cannot accept your own game", show_alert=True)
            return
            
        opponent_bal = lc.get_user_balance(user_id)
        if opponent_bal < match['bet']:
            await query.answer(f"❌ Insufficient balance! You need {match['bet']} ⭐", show_alert=True)
            return
            
        # Deduct opponent (creator was already deducted on creation)
        lc.adjust_user_balance(user_id, -match['bet'], game=True)
        
        profile = lc.get_or_create_profile(user_id)
        opponent_name = profile.get('display_name') or profile.get('username') or 'Player'
        
        db.update_pvp_match(
            match_id, 
            opponent_id=user_id, 
            opponent_name=opponent_name, 
            status="active",
            current_turn=match['creator_id'],
            rolls_this_turn=0
        )
        match = db.get_pvp_match(match_id)
        
        text = build_game_started_message(match)
        config = lc.GAME_CONFIG[match['game_type']]
        markup = lc.build_copy_turn_reply_markup(match['creator_id'], config['emoji'])
        
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
        
        creator_link = lc.get_user_link(match['creator_id'], match['creator_name'])
        turn_text = f"👉 {creator_link}, it's your turn!"
        
        await context.bot.send_message(
            chat_id=match['chat_id'],
            text=turn_text,
            parse_mode=ParseMode.HTML
        )
        
        # Schedule timeout job
        context.job_queue.run_once(
            pvp_timeout_check, 
            60, 
            data={'match_id': match_id},
            name=f"pvp_timeout_{match_id}"
        )
        return

    elif action in ["repeat", "double"]:
        if user_id not in [match['creator_id'], match['opponent_id']]:
            await query.answer("⚠️ This match is not yours.", show_alert=True)
            return
            
        new_bet = match['bet'] if action == "repeat" else match['bet'] * 2
        
        # Verify balance
        user_bal = lc.get_user_balance(user_id)
        if user_bal < new_bet:
            await query.answer("❌ You don't have enough balance.", show_alert=True)
            return
            
        # Deduct new bet
        lc.adjust_user_balance(user_id, -new_bet, game=True)
        
        # Create new challenge
        new_match_id = str(uuid.uuid4())[:8]
        profile = lc.get_or_create_profile(user_id)
        display_name = profile.get('display_name') or profile.get('username') or 'Player'
        
        db.create_pvp_match(
            match_id=new_match_id,
            game_type=match['game_type'],
            creator_id=user_id,
            creator_name=display_name,
            chat_id=chat_id,
            message_id=0, # Will update after send
            bet=new_bet,
            multiplier=match['multiplier'],
            mode=match['mode'],
            target_score=match['target_score']
        )
        
        keyboard = [
            [InlineKeyboardButton("🎲 Accept Game", callback_data=f"pvp_accept_{new_match_id}")],
            [InlineKeyboardButton("🤖 Play Against Bot", callback_data=f"pvp_bot_{new_match_id}")],
            [InlineKeyboardButton("❌ Cancel Game", callback_data=f"pvp_cancel_{new_match_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = build_challenge_message(match['game_type'], new_bet, match['mode'], match['target_score'], user_id)
        
        sent_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
        
        db.update_pvp_match(new_match_id, message_id=sent_msg.message_id)
        
        context.job_queue.run_once(
            pvp_timeout_check, 
            60, 
            data={'match_id': new_match_id},
            name=f"pvp_timeout_{new_match_id}"
        )
        await query.answer()
        return

async def pvp_timeout_check(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    match_id = job.data['match_id']
    match = db.get_pvp_match(match_id)
    
    if not match or match['status'] == 'finished':
        return
        
    # Check if 60s passed since last update
    from datetime import datetime
    now = datetime.utcnow()
    updated_at = datetime.fromisoformat(match['updated_at'])
    if (now - updated_at).total_seconds() < 55:
        # Not expired yet, reschedule
        rem = 60 - (now - updated_at).total_seconds()
        context.job_queue.run_once(pvp_timeout_check, max(1, rem), data={'match_id': match_id}, name=f"pvp_timeout_{match_id}")
        return
        
    if match['status'] == 'waiting':
        # Timeout waiting for opponent
        db.update_pvp_match(match_id, status='cancelled')
        
        # Refund the locked bet
        lc.adjust_user_balance(match['creator_id'], match['bet'], game=True)
        
        config = lc.GAME_CONFIG[match['game_type']]
        bet_usd = match['bet'] * lc.STARS_TO_USD
        creator_link = lc.get_user_link(match['creator_id'], match['creator_name'])
        
        text = f"{config['emoji']} The game by {creator_link} for ${bet_usd:.2f} was canceled — no one accepted the invitation."
        
        try:
            await context.bot.edit_message_text(
                chat_id=match['chat_id'],
                message_id=match['message_id'],
                text=text,
                parse_mode=ParseMode.HTML
            )
        except:
            pass

async def handle_pvp_roll(update: Update, context: ContextTypes.DEFAULT_TYPE, match: dict):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if user_id != match['current_turn']:
        sent = await update.message.reply_html("⚠️ It's not your turn")
        await asyncio.sleep(3)
        try:
            await sent.delete()
        except:
            pass
        return
        
    config = lc.GAME_CONFIG[match['game_type']]
    dice = update.message.dice
    
    if dice.emoji != config['tg_emoji']:
        sent = await update.message.reply_html(f"⚠️ This game uses {config['tg_emoji']} only")
        await asyncio.sleep(3)
        try:
            await sent.delete()
        except:
            pass
        return
        
    # Valid roll!
    rolls_needed = 2 if match['mode'] == "double" else 1
    new_rolls_done = match['rolls_this_turn'] + 1
    
    # Store roll value
    current_val = match['creator_roll'] if user_id == match['creator_id'] else match['opponent_roll']
    current_val = current_val or 0
    new_val = current_val + dice.value
    
    if user_id == match['creator_id']:
        db.update_pvp_match(match['match_id'], creator_roll=new_val, rolls_this_turn=new_rolls_done)
    else:
        db.update_pvp_match(match['match_id'], opponent_roll=new_val, rolls_this_turn=new_rolls_done)
        
    match = db.get_pvp_match(match['match_id'])
    
    user_link = lc.get_user_link(user_id, match['creator_name'] if user_id == match['creator_id'] else match['opponent_name'])
    
    if new_rolls_done < rolls_needed:
        # Still their turn
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"👉 {user_link}, it's your turn! (Roll 2/2)",
            parse_mode=ParseMode.HTML,
            reply_to_message_id=update.message.message_id
        )
        return
        
    # Turn complete.
    # Are we waiting for opponent?
    if match['creator_roll'] is not None and match['opponent_roll'] is not None:
        # Both have rolled! Resolve round.
        await resolve_pvp_round(context, match)
    else:
        # Switch turn
        next_turn = match['opponent_id'] if user_id == match['creator_id'] else match['creator_id']
        db.update_pvp_match(match['match_id'], current_turn=next_turn, rolls_this_turn=0)
        
        next_player_name = match['opponent_name'] if next_turn == match['opponent_id'] else match['creator_name']
        next_link = lc.get_user_link(next_turn, next_player_name)
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"👉 {next_link}, it's your turn!",
            parse_mode=ParseMode.HTML,
            reply_to_message_id=update.message.message_id
        )

async def resolve_pvp_round(context: ContextTypes.DEFAULT_TYPE, match: dict):
    creator_val = match['creator_roll']
    opponent_val = match['opponent_roll']
    mode = match['mode']
    
    creator_wins = False
    opponent_wins = False
    
    if creator_val == opponent_val:
        # Tie
        pass
    else:
        if mode == "crazy":
            if creator_val < opponent_val:
                creator_wins = True
            else:
                opponent_wins = True
        else:
            if creator_val > opponent_val:
                creator_wins = True
            else:
                opponent_wins = True
                
    new_c_score = match['creator_score'] + (1 if creator_wins else 0)
    new_o_score = match['opponent_score'] + (1 if opponent_wins else 0)
    
    # Reset rolls and set next turn to the loser, or keep it if tie
    next_turn = match['current_turn']
    if creator_wins:
        next_turn = match['opponent_id']
    elif opponent_wins:
        next_turn = match['creator_id']
        
    db.update_pvp_match(
        match['match_id'],
        creator_score=new_c_score,
        opponent_score=new_o_score,
        creator_roll=None,
        opponent_roll=None,
        rolls_this_turn=0,
        current_turn=next_turn
    )
    
    # Check for game over
    target = match['target_score']
    if new_c_score >= target or new_o_score >= target:
        winner_id = match['creator_id'] if new_c_score >= target else match['opponent_id']
        await end_pvp_game(context, match, winner_id, new_c_score, new_o_score)
        return
        
    # Send round results
    creator_link = lc.get_user_link(match['creator_id'], match['creator_name'])
    opponent_link = lc.get_user_link(match['opponent_id'], match['opponent_name'])
    
    if creator_wins:
        header = f"🏆 {match['creator_name']} wins this round!"
    elif opponent_wins:
        header = f"🏆 {match['opponent_name']} wins this round!"
    else:
        header = "🤝 It's a tie!"
        
    next_player_name = match['creator_name'] if next_turn == match['creator_id'] else match['opponent_name']
    
    scores_block = (
        f"Scores:\n"
        f"👤 {creator_link} • {new_c_score}\n"
        f"👤 {opponent_link} • {new_o_score}"
    )
    
    text = (
        f"{header}\n\n"
        f"{scores_block}\n\n"
        f"🎮 Waiting for {next_player_name}...\n"
        f"👉 Next round: {lc.get_user_link(next_turn, next_player_name)}, it's your turn."
    )
    
    await context.bot.send_message(
        chat_id=match['chat_id'],
        text=text,
        parse_mode=ParseMode.HTML
    )

async def end_pvp_game(context: ContextTypes.DEFAULT_TYPE, match: dict, winner_id: int, final_c_score: int, final_o_score: int):
    prize_stars = int(match['bet'] * match['multiplier'])
    
    # Payout
    lc.adjust_user_balance(winner_id, prize_stars, game=True)
    
    creator_link = lc.get_user_link(match['creator_id'], match['creator_name'])
    opponent_link = lc.get_user_link(match['opponent_id'], match['opponent_name'])
    
    bet_usd = match['bet'] * lc.STARS_TO_USD
    prize_usd = prize_stars * lc.STARS_TO_USD
    
    if winner_id == match['creator_id']:
        winner_link = creator_link
        loser_link = opponent_link
        win_score = final_c_score
        lose_score = final_o_score
    else:
        winner_link = opponent_link
        loser_link = creator_link
        win_score = final_o_score
        lose_score = final_c_score
    
    financial_line = f"🤑 {winner_link} wins the game and earns ${prize_usd:.2f}"
    
    text = (
        f"🔹 The game has ended\n"
        f"👑 Winner: {winner_link} • {win_score} points 👎 Loser: {loser_link} • {lose_score} points\n\n"
        f"{financial_line}"
    )
    
    db.update_pvp_match(match['match_id'], status='finished')
    
    keyboard = [
        [
            InlineKeyboardButton("🔁 Repeat", callback_data=f"pvp_repeat_{match['match_id']}"),
            InlineKeyboardButton("2X Double", callback_data=f"pvp_double_{match['match_id']}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(
        chat_id=match['chat_id'],
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )

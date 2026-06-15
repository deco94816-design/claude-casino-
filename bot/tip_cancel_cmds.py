# -*- coding: utf-8 -*-
"""User actions: /tip (transfer stars between users) and /cancel (abort flows).

Lifted verbatim except the global-state bridge: rebound globals (STARS_TO_USD,
user_balances, user_profiles, username_to_id) via ``lc.*``. Session stores and
helpers are imported (stable, mutated in place). Re-imported into librate_casino
so command registration resolves unchanged.
"""

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import librate_casino as lc
from librate_casino import (
    logger, translate_text, handle_errors, is_admin, save_data,
    adjust_user_balance, get_user_balance, get_or_create_profile,
    get_user_id_by_username, get_user_link, emoji_replace_flow,
    broadcast_waiting, cflip_setup, coinflip_sessions, game_sessions, predict_sessions,
)


@handle_errors
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel any ongoing operation"""
    user_id = update.effective_user.id
    
    cancelled = False
    
    # Cancel active game session with refund
    if user_id in game_sessions:
        session = game_sessions[user_id]
        if not session.get('is_demo', False) and not is_admin(user_id):
            adjust_user_balance(user_id, session['bet'])
            lc.user_balances[user_id] = get_user_balance(user_id)
        del game_sessions[user_id]
        cancelled = True
    
    # Cancel active predict game (no refund - bet not deducted until play)
    if user_id in predict_sessions:
        del predict_sessions[user_id]
        cancelled = True

    # Cancel active coinflip with refund
    if user_id in coinflip_sessions:
        session = coinflip_sessions[user_id]
        adjust_user_balance(user_id, session['bet'])
        lc.user_balances[user_id] = get_user_balance(user_id)
        del coinflip_sessions[user_id]
        cancelled = True
    
    # Cancel coinflip setup
    if user_id in cflip_setup:
        del cflip_setup[user_id]
        cancelled = True
    
    if context.user_data.get('waiting_for_video'):
        context.user_data['waiting_for_video'] = False
        cancelled = True
    
    if context.user_data.get('waiting_for_custom_amount'):
        context.user_data['waiting_for_custom_amount'] = False
        cancelled = True
    
    if context.user_data.get('withdraw_state'):
        context.user_data['withdraw_state'] = None
        context.user_data['withdraw_amount'] = None
        context.user_data['withdraw_address'] = None
        cancelled = True
    
    # Cancel gift process
    if context.user_data.get('gift_state'):
        context.user_data['gift_state'] = None
        context.user_data['gift_target_user_id'] = None
        context.user_data['gift_target_username'] = None
        cancelled = True

    # Cancel broadcast wait
    if user_id in broadcast_waiting:
        broadcast_waiting.discard(user_id)
        cancelled = True

    # Cancel broadcastall wait
    if context.user_data.get("broadcastall_waiting"):
        context.user_data["broadcastall_waiting"] = False
        cancelled = True

    # Cancel emoji customization flow
    if user_id in emoji_replace_flow:
        del emoji_replace_flow[user_id]
        cancelled = True
    
    if cancelled:
        await update.message.reply_html(translate_text("✅ Operation cancelled."))
    else:
        await update.message.reply_html(translate_text("â¹ï¸  Nothing to cancel."))

@handle_errors
async def tip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message = update.message
    
    # Check if using /tip amount @username format
    if context.args and len(context.args) >= 2:
        try:
            tip_amount = int(context.args[0])
            target = context.args[1]
            
            if tip_amount < 1:
                await message.reply_html(translate_text("❌ Tip amount must be at least 1 ⭐", user_id=user_id))
                return
            
            # Check if target is a username
            if target.startswith('@'):
                username = target.lstrip('@')
                recipient_id = get_user_id_by_username(username)
                
                if not recipient_id:
                    await message.reply_html(
                        translate_text(
                            f"❌ <b>User not found!</b>\n\n"
                            f"User @{username} has not interacted with the bot yet.\n"
                            f"They need to use the bot at least once before receiving tips.",
                            user_id=user_id
                        )
                    )
                    return
                
                recipient_profile = lc.user_profiles.get(recipient_id, {})
                recipient_name = recipient_profile.get('username', username)
            else:
                # Try to parse as user_id
                try:
                    recipient_id = int(target)
                    recipient_profile = lc.user_profiles.get(recipient_id, {})
                    recipient_name = recipient_profile.get('username', 'User')
                except ValueError:
                    await message.reply_html(translate_text("❌ Invalid user! Use @username or user ID.", user_id=user_id))
                    return
            
            if recipient_id == user_id:
                await message.reply_html(translate_text("❌ You can't tip yourself!", user_id=user_id))
                return
            
            sender_balance = get_user_balance(user_id)
            if sender_balance < tip_amount:
                await message.reply_html(
                    translate_text(
                        f"❌ <b>Insufficient balance!</b>\n\n"
                        f"Your balance: {sender_balance} ⭐\n"
                        f"Tip amount: {tip_amount} ⭐"
                    )
                )
                return
            
            if not is_admin(user_id):
                adjust_user_balance(user_id, -tip_amount)
                lc.user_balances[user_id] = get_user_balance(user_id)  # Sync memory cache
            
            adjust_user_balance(recipient_id, tip_amount)
            lc.user_balances[recipient_id] = get_user_balance(recipient_id)  # Sync memory cache
            
            tip_usd = tip_amount * lc.STARS_TO_USD
            sender_name = message.from_user.first_name
            
            sender_link = get_user_link(user_id, sender_name)
            recipient_link = get_user_link(recipient_id, recipient_name)
            
            await message.reply_html(
                f"✅ Tipped <b>{tip_amount}⭐</b> to {recipient_link}"
            )
            
            try:
                await context.bot.send_message(
                    chat_id=recipient_id,
                    text=(
                        f"🎂 <b>You received a tip!</b>\n\n"
                        f"👤 From: {sender_link}\n"
                        f"💰 Amount: <b>{tip_amount} ⭐</b> (${tip_usd:.2f})\n\n"
                        f"💵 Your new balance: <b>{get_user_balance(recipient_id)} ⭐</b>"
                    ),
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.warning(f"Could not notify recipient {recipient_id}: {e}")
            
            logger.info(f"Tip: {user_id} ({sender_name}) -> {recipient_id} ({recipient_name}): {tip_amount} stars")
            return
            
        except ValueError:
            pass  # Fall through to reply-based tip
    
    # Reply-based tip
    if not message.reply_to_message:
        await message.reply_html(
            "💵 To transfer, reply to the person's message with /tip &lt;amount&gt;"
        )
        return
    
    if not context.args or len(context.args) == 0:
        await message.reply_html(translate_text("❌ Please specify the amount to tip!\nExample: /tip 100", user_id=user_id))
        return
    
    try:
        tip_amount = int(context.args[0])
        
        if tip_amount < 1:
            await message.reply_html(translate_text("❌ Tip amount must be at least 1 ⭐", user_id=user_id))
            return
        
        recipient_id = message.reply_to_message.from_user.id
        recipient_name = message.reply_to_message.from_user.first_name
        sender_name = message.from_user.first_name
        
        # Update username mapping for recipient
        if message.reply_to_message.from_user.username:
            lc.username_to_id[message.reply_to_message.from_user.username.lower()] = recipient_id
            save_data()
        
        if recipient_id == user_id:
            await message.reply_html(translate_text("❌ You can't tip yourself!", user_id=user_id))
            return
        
        sender_balance = get_user_balance(user_id)
        if sender_balance < tip_amount:
            await message.reply_html(
                f"❌ <b>Insufficient balance!</b>\n\n"
                f"Your balance: {sender_balance} ⭐\n"
                f"Tip amount: {tip_amount} ⭐"
            )
            return
        
        if not is_admin(user_id):
            adjust_user_balance(user_id, -tip_amount)
            lc.user_balances[user_id] = get_user_balance(user_id)  # Sync memory cache
        
        adjust_user_balance(recipient_id, tip_amount)
        get_or_create_profile(recipient_id, recipient_name)
        
        tip_usd = tip_amount * lc.STARS_TO_USD
        
        sender_link = get_user_link(user_id, sender_name)
        recipient_link = get_user_link(recipient_id, recipient_name)
        
        await message.reply_html(
            translate_text(f"✅ Tipped <b>{tip_amount}⭐</b> to {recipient_link}", user_id=user_id)
        )
        
        try:
            await context.bot.send_message(
                chat_id=recipient_id,
                text=translate_text(
                    f"🎂 <b>You received a tip!</b>\n\n"
                    f"👤 From: {sender_link}\n"
                    f"💰 Amount: <b>{tip_amount} ⭐</b> (${tip_usd:.2f})\n\n"
                    f"💵 Your new balance: <b>{get_user_balance(recipient_id)} ⭐</b>"
                ),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.warning(f"Could not notify recipient {recipient_id}: {e}")
        
        logger.info(f"Tip: {user_id} ({sender_name}) -> {recipient_id} ({recipient_name}): {tip_amount} stars")
        
    except ValueError:
        await message.reply_html(translate_text("❌ Invalid amount! Please enter a number.", user_id=user_id))

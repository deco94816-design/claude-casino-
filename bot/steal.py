# -*- coding: utf-8 -*-
"""Steal / rebrand flow: admin reskins the bot (name, channel/chat links,
support handle). Driven by steal_* callbacks (delegated from button_callback)
and text input (handle_steal_flow, from handle_text_message).

Lifted verbatim except the bridge for bot_identity (rebound) via ``lc.*``.
Re-imported into librate_casino so both the callback delegation and the text
router resolve unchanged.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import librate_casino as lc
from librate_casino import db, logger, t, translate_text, handle_errors, is_admin


@handle_errors
async def handle_steal_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle steal command text input flow"""
    if not update.message:
        return
    
    user_id = update.effective_user.id
    text = update.message.text.strip()
    steal_state = context.user_data.get('steal_state')
    
    if not steal_state or steal_state not in ['collecting_data', 'collecting_all']:
        return
    
    # Determine which field we're waiting for
    if context.user_data.get('steal_waiting') == 'name':
        if not text or len(text) < 2:
            await update.message.reply_html(translate_text("❌ Please send a valid name (at least 2 characters)", user_id=user_id))
            return
        context.user_data['steal_new_name'] = text
        await update.message.reply_html(translate_text(f"✅ Bot name saved: <b>{text}</b>", user_id=user_id))
        # Move to next value
        await move_to_next_steal_value(update, context)
        return
    
    elif context.user_data.get('steal_waiting') == 'channel':
        if not text.startswith('http://') and not text.startswith('https://') and not text.startswith('@'):
            await update.message.reply_html(
                "❌ Please send a valid channel link or username:\n"
                "• https://t.me/channelname\n"
                "• @channelname"
            )
            return
        context.user_data['steal_channel_link'] = text
        await update.message.reply_html(translate_text(f"✅ Channel link saved: <b>{text}</b>", user_id=user_id))
        # Move to next value
        await move_to_next_steal_value(update, context)
        return
    
    elif context.user_data.get('steal_waiting') == 'chat':
        if not text.startswith('http://') and not text.startswith('https://') and not text.startswith('@'):
            await update.message.reply_html(
                "❌ Please send a valid chat link or username:\n"
                "• https://t.me/chatname\n"
                "• @chatname"
            )
            return
        context.user_data['steal_chat_link'] = text
        await update.message.reply_html(translate_text(f"✅ Chat link saved: <b>{text}</b>", user_id=user_id))
        # Move to next value
        await move_to_next_steal_value(update, context)
        return
    
    elif context.user_data.get('steal_waiting') == 'support':
        if not text or len(text) < 1:
            await update.message.reply_html(translate_text("❌ Please send a valid username", user_id=user_id))
            return
        support_username = text.replace('@', '')
        context.user_data['steal_support_username'] = support_username
        await update.message.reply_html(translate_text(f"✅ Support username saved: <b>@{support_username}</b>", user_id=user_id))
        # Move to next value
        await move_to_next_steal_value(update, context)
        return

async def move_to_next_steal_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Move to the next value that needs to be collected"""
    user_id = update.effective_user.id
    needs_name = context.user_data.get('steal_name_yes') and not context.user_data.get('steal_new_name')
    needs_channel = context.user_data.get('steal_channel_yes') and not context.user_data.get('steal_channel_link')
    needs_chat = context.user_data.get('steal_chat_yes') and not context.user_data.get('steal_chat_link')
    needs_support = context.user_data.get('steal_support_yes') and not context.user_data.get('steal_support_username')
    
    if needs_name:
        context.user_data['steal_waiting'] = 'name'
        await update.message.reply_html(translate_text("📍 <b>Now send the bot name:</b>", user_id=user_id))
    elif needs_channel:
        context.user_data['steal_waiting'] = 'channel'
        await update.message.reply_html(translate_text("📍 <b>Now send the channel link:</b>\n\nFormat: https://t.me/channelname or @channelname", user_id=user_id))
    elif needs_chat:
        context.user_data['steal_waiting'] = 'chat'
        await update.message.reply_html(translate_text("📍 <b>Now send the chat link:</b>\n\nFormat: https://t.me/chatname or @chatname", user_id=user_id))
    elif needs_support:
        context.user_data['steal_waiting'] = 'support'
        await update.message.reply_html(translate_text("📍 <b>Now send the support username:</b> (without @)", user_id=user_id))
    else:
        # All values collected, apply changes
        context.user_data['steal_waiting'] = None
        await apply_steal_changes(update, context)

async def check_and_continue_steal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check if all required data is collected and continue or finish"""
    # This function is now mainly for backward compatibility
    # The main flow uses move_to_next_steal_value
    await move_to_next_steal_value(update, context)

async def apply_steal_changes_from_query(query, context: ContextTypes.DEFAULT_TYPE):
    """Apply all steal changes from a callback query"""
    user_id = query.from_user.id
    old_name = lc.bot_identity.get("name", "Iibrate")
    
    # Update bot name if provided
    if context.user_data.get('steal_new_name'):
        lc.bot_identity["name"] = context.user_data['steal_new_name']
    
    # Update channel link if provided
    if context.user_data.get('steal_channel_link'):
        lc.bot_identity["channel_link"] = context.user_data['steal_channel_link']
    
    # Update chat link if provided
    if context.user_data.get('steal_chat_link'):
        lc.bot_identity["chat_link"] = context.user_data['steal_chat_link']
    
    # Update support username if provided
    if context.user_data.get('steal_support_username'):
        lc.bot_identity["support_username"] = context.user_data['steal_support_username']
    
    db.set_bot_identity(lc.bot_identity)
    
    # Build summary
    new_name = lc.bot_identity.get("name", old_name)
    changes = []
    if context.user_data.get('steal_new_name'):
        changes.append(f"• Name: {old_name} → {new_name}")
    if context.user_data.get('steal_channel_link'):
        changes.append(f"• Channel: {lc.bot_identity.get('channel_link', 'Not set')}")
    if context.user_data.get('steal_chat_link'):
        changes.append(f"• Chat: {lc.bot_identity.get('chat_link', 'Not set')}")
    if context.user_data.get('steal_support_username'):
        changes.append(f"• Support: @{lc.bot_identity.get('support_username', 'Not set')}")
    
    # Clear steal state
    context.user_data.pop('steal_state', None)
    context.user_data.pop('steal_new_name', None)
    context.user_data.pop('steal_channel_link', None)
    context.user_data.pop('steal_chat_link', None)
    context.user_data.pop('steal_support_username', None)
    context.user_data.pop('steal_name_yes', None)
    context.user_data.pop('steal_channel_yes', None)
    context.user_data.pop('steal_chat_yes', None)
    context.user_data.pop('steal_support_yes', None)
    context.user_data.pop('steal_waiting', None)
    
    changes_text = "\n".join(changes) if changes else "No changes made."
    
    await query.message.reply_html(
        f"✅ <b>Bot Rebranding Complete!</b>\n\n"
        f"📍 <b>Changes Applied:</b>\n"
        f"{changes_text}\n\n"
        f"All messages will now use the new identity!"
    )
    
    logger.info(f"Admin {user_id} rebranded bot: {old_name} → {new_name}")

async def apply_steal_changes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Apply all steal changes"""
    user_id = update.effective_user.id
    old_name = lc.bot_identity.get("name", "Iibrate")
    
    # Update bot name if provided
    if context.user_data.get('steal_new_name'):
        lc.bot_identity["name"] = context.user_data['steal_new_name']
    
    # Update channel link if provided
    if context.user_data.get('steal_channel_link'):
        lc.bot_identity["channel_link"] = context.user_data['steal_channel_link']
    
    # Update chat link if provided
    if context.user_data.get('steal_chat_link'):
        lc.bot_identity["chat_link"] = context.user_data['steal_chat_link']
    
    # Update support username if provided
    if context.user_data.get('steal_support_username'):
        lc.bot_identity["support_username"] = context.user_data['steal_support_username']
    
    db.set_bot_identity(lc.bot_identity)
    
    # Build summary
    new_name = lc.bot_identity.get("name", old_name)
    changes = []
    if context.user_data.get('steal_new_name'):
        changes.append(f"• Name: {old_name} → {new_name}")
    if context.user_data.get('steal_channel_link'):
        changes.append(f"• Channel: {lc.bot_identity.get('channel_link', 'Not set')}")
    if context.user_data.get('steal_chat_link'):
        changes.append(f"• Chat: {lc.bot_identity.get('chat_link', 'Not set')}")
    if context.user_data.get('steal_support_username'):
        changes.append(f"• Support: @{lc.bot_identity.get('support_username', 'Not set')}")
    
    # Clear steal state
    context.user_data.pop('steal_state', None)
    context.user_data.pop('steal_new_name', None)
    context.user_data.pop('steal_channel_link', None)
    context.user_data.pop('steal_chat_link', None)
    context.user_data.pop('steal_support_username', None)
    context.user_data.pop('steal_name_yes', None)
    context.user_data.pop('steal_channel_yes', None)
    context.user_data.pop('steal_chat_yes', None)
    context.user_data.pop('steal_support_yes', None)
    context.user_data.pop('steal_waiting', None)
    
    changes_text = "\n".join(changes) if changes else "No changes made."
    
    # Get message object (could be from update.message or update.callback_query.message)
    message = update.message
    if not message and update.callback_query:
        message = update.callback_query.message
    
    if message:
        await message.reply_html(
            f"✅ <b>Bot Rebranding Complete!</b>\n\n"
            f"📍 <b>Changes Applied:</b>\n"
            f"{changes_text}\n\n"
            f"All messages will now use the new identity!"
        )
    
    logger.info(f"Admin {user_id} rebranded bot: {old_name} → {new_name}")

@handle_errors
async def handle_steal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle steal command inline button callbacks"""
    query = update.callback_query
    if not query:
        return
    
    user_id = query.from_user.id
    data = query.data
    
    if not is_admin(user_id):
        await query.answer(t("err_admin_only_alert", user_id=user_id), show_alert=True)
        return
    
    # Handle name yes/no
    if data == "steal_name_yes":
        context.user_data['steal_name_yes'] = True
        await show_next_steal_question(query, context)
        await query.answer(translate_text("✅ Will change bot name", user_id=user_id))
        return
    
    elif data == "steal_name_no":
        context.user_data['steal_name_yes'] = False
        await show_next_steal_question(query, context)
        await query.answer(t("err_bot_name_skipped", user_id=user_id))
        return
    
    # Handle channel yes/no
    elif data == "steal_channel_yes":
        context.user_data['steal_channel_yes'] = True
        await show_next_steal_question(query, context)
        await query.answer(translate_text("✅ Will change channel link", user_id=user_id))
        return
    
    elif data == "steal_channel_no":
        context.user_data['steal_channel_yes'] = False
        await show_next_steal_question(query, context)
        await query.answer(translate_text("❌ Channel link skipped", user_id=user_id))
        return
    
    # Handle chat yes/no
    elif data == "steal_chat_yes":
        context.user_data['steal_chat_yes'] = True
        await show_next_steal_question(query, context)
        await query.answer(t("info_change_chat_link", user_id=user_id))
        return
    
    elif data == "steal_chat_no":
        context.user_data['steal_chat_yes'] = False
        await show_next_steal_question(query, context)
        await query.answer(translate_text("❌ Chat link skipped", user_id=user_id))
        return
    
    # Handle support yes/no
    elif data == "steal_support_yes":
        context.user_data['steal_support_yes'] = True
        await show_next_steal_question(query, context)
        await query.answer(translate_text("✅ Will change support username", user_id=user_id))
        return
    
    elif data == "steal_support_no":
        context.user_data['steal_support_yes'] = False
        await show_next_steal_question(query, context)
        await query.answer(translate_text("❌ Support username skipped", user_id=user_id))
        return

async def show_next_steal_question(query, context: ContextTypes.DEFAULT_TYPE):
    """Show the next yes/no question in the steal flow"""
    user_id = query.from_user.id
    try:
        if 'steal_name_yes' not in context.user_data:
            # Ask about name
            keyboard = [
                [
                    InlineKeyboardButton(translate_text("✅ Yes", user_id=user_id), callback_data="steal_name_yes"),
                    InlineKeyboardButton(translate_text("❌ No", user_id=user_id), callback_data="steal_name_no")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                translate_text(
                    "🎂­ <b>Bot Rebranding</b>\n\n"
                    "📍 <b>Do you want to change the bot name?</b>\n"
                    "(This replaces 'Iibrate' everywhere)"
                ),
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        elif 'steal_channel_yes' not in context.user_data:
            # Ask about channel
            keyboard = [
                [
                    InlineKeyboardButton(translate_text("✅ Yes", user_id=user_id), callback_data="steal_channel_yes"),
                    InlineKeyboardButton(translate_text("❌ No", user_id=user_id), callback_data="steal_channel_no")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            name_status = "✅ Name: Will change" if context.user_data.get('steal_name_yes') else "❌ Name: Skipped"
            await query.edit_message_text(
                f"{name_status}\n\n{translate_text('📍 <b>Do you want to change the channel link?</b>', user_id=user_id)}",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        elif 'steal_chat_yes' not in context.user_data:
            # Ask about chat
            keyboard = [
                [
                    InlineKeyboardButton(t("btn_yes", user_id=user_id), callback_data="steal_chat_yes"),
                    InlineKeyboardButton(t("btn_no", user_id=user_id), callback_data="steal_chat_no")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            name_status = "✅ Name: Will change" if context.user_data.get('steal_name_yes') else "❌ Name: Skipped"
            channel_status = translate_text("✅ Channel: Will change", user_id=user_id) if context.user_data.get('steal_channel_yes') else translate_text("❌ Channel: Skipped", user_id=user_id)
            await query.edit_message_text(
                f"{name_status}\n{channel_status}\n\n{translate_text('📍 <b>Do you want to change the chat link?</b>', user_id=user_id)}",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        elif 'steal_support_yes' not in context.user_data:
            # Ask about support
            keyboard = [
                [
                    InlineKeyboardButton(translate_text("✅ Yes", user_id=user_id), callback_data="steal_support_yes"),
                    InlineKeyboardButton(translate_text("❌ No", user_id=user_id), callback_data="steal_support_no")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            name_status = "✅ Name: Will change" if context.user_data.get('steal_name_yes') else "❌ Name: Skipped"
            channel_status = translate_text("✅ Channel: Will change", user_id=user_id) if context.user_data.get('steal_channel_yes') else translate_text("❌ Channel: Skipped", user_id=user_id)
            chat_status = translate_text("✅ Chat: Will change", user_id=user_id) if context.user_data.get('steal_chat_yes') else translate_text("❌ Chat: Skipped", user_id=user_id)
            await query.edit_message_text(
                f"{name_status}\n{channel_status}\n{chat_status}\n\n📍 <b>Do you want to change the support username?</b>",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        else:
            # All questions answered, start collecting data
            # Check what values we need to collect
            needs_name = context.user_data.get('steal_name_yes') and not context.user_data.get('steal_new_name')
            needs_channel = context.user_data.get('steal_channel_yes') and not context.user_data.get('steal_channel_link')
            needs_chat = context.user_data.get('steal_chat_yes') and not context.user_data.get('steal_chat_link')
            needs_support = context.user_data.get('steal_support_yes') and not context.user_data.get('steal_support_username')
            
            # If nothing needs to be collected, apply changes
            if not needs_name and not needs_channel and not needs_chat and not needs_support:
                await apply_steal_changes_from_query(query, context)
                return
            
            # Set state to collecting all values
            context.user_data['steal_state'] = 'collecting_all'
            
            # Show summary of what will be collected
            prompt_parts = []
            if needs_name:
                prompt_parts.append("📍 Bot name")
            if needs_channel:
                prompt_parts.append("📍 Channel link")
            if needs_chat:
                prompt_parts.append("📍 Chat link")
            if needs_support:
                prompt_parts.append("📍 Support username")
            
            await query.edit_message_text(
                f"✅ <b>All questions answered!</b>\n\n"
                f"<b>I need the following values:</b>\n" + "\n".join(prompt_parts) + "\n\n"
                f"<b>I'll ask for them one by one. Send the first value now:</b>",
                parse_mode=ParseMode.HTML
            )
            
            # Set waiting state for the first needed value and prompt
            if needs_name:
                context.user_data['steal_waiting'] = 'name'
                await query.message.reply_html(translate_text("📍 <b>Send the bot name:</b>", user_id=user_id))
            elif needs_channel:
                context.user_data['steal_waiting'] = 'channel'
                await query.message.reply_html(f'{t("send_channel_link", user_id=user_id)}\n\n{t("send_channel_format", user_id=user_id)}')
            elif needs_chat:
                context.user_data['steal_waiting'] = 'chat'
                await query.message.reply_html(translate_text("📍 <b>Send the chat link:</b>\n\nFormat: https://t.me/chatname or @chatname", user_id=user_id))
            elif needs_support:
                context.user_data['steal_waiting'] = 'support'
                await query.message.reply_html(translate_text("📍 <b>Send the support username:</b> (without @)", user_id=user_id))
    except Exception as e:
        logger.error(f"Error in show_next_steal_question: {e}")
        try:
            await query.answer(translate_text("❌ An error occurred. Please try again.", user_id=user_id), show_alert=True)
        except:
            pass

# -*- coding: utf-8 -*-
"""Gift flow: /gift -> chat id -> /pingme (invoice) -> payment -> send gift,
plus /cg (change gift comment). Multi-step admin flow kept together.

Lifted verbatim except the global-state bridge: gift_comment (rebound by
load_data + /cg) and username_to_id via ``lc.*``. process_gift_chat_id and
process_gift_after_payment are called from the text handler; re-imported into
librate_casino so all call sites + command registration resolve unchanged.
"""

from telegram import Update, LabeledPrice
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import librate_casino as lc
from librate_casino import (
    GIFT_STARS, PAYMENT_STARS, PROVIDER_TOKEN,
    db, logger, translate_text, handle_errors, is_admin,
    get_user_balance, get_or_create_referral_code, get_random_gift_message,
    get_template, replace_template_variables,
)


@handle_errors
async def gift_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start gift process - Step 1: Ask for chat ID or username"""
    if not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ You are not authorized"))
        return
    
    # Reset any previous state
    context.user_data['gift_state'] = 'waiting_for_chat_id'
    context.user_data['gift_target_user_id'] = None
    context.user_data['gift_target_username'] = None
    
    await update.message.reply_html(
        "📄 <b>Please send the chat ID or username of the recipient</b>"
    )
    
    logger.info(f"Admin {user_id} started gift process - waiting for chat ID")

@handle_errors
async def pingme_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hidden command - Step 3: Create payment invoice"""
    if not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        return  # Silently ignore non-admins
    
    # Delete the command message to hide it
    try:
        await update.message.delete()
    except Exception:
        pass
    
    # Check if target user is set (Step 2 completed)
    if context.user_data.get('gift_state') != 'waiting_for_pingme':
        await update.message.reply_html(
            "❌ <b>Please complete the gift process first.</b>\n\n"
            "Use /gift to start, then provide chat ID or username."
        )
        return
    
    target_user_id = context.user_data.get('gift_target_user_id')
    if not target_user_id:
        await update.message.reply_html(translate_text("❌ Target user not set. Use /gift to start.", user_id=user_id))
        return
    
    # Create payment invoice for 1 Star
    try:
        prices = [LabeledPrice("Gift Payment", PAYMENT_STARS)]
        payload = f"gift_payment_{user_id}_{target_user_id}"
        
        await update.message.reply_invoice(
            title="🎂 Gift Payment",
            description="Payment for sending Telegram gift",
            payload=payload,
            provider_token=PROVIDER_TOKEN,
            currency="XTR",  # Telegram Stars currency
            prices=prices,
            start_parameter="gift"
        )
        
        # Inform admin about "1" shortcut
        await update.message.reply_html(
            "💡 <b>Tip:</b> You can also send <b>1</b> to confirm payment and process the gift automatically."
        )
        
        context.user_data['gift_state'] = 'waiting_for_payment'
        logger.info(f"Admin {user_id} created gift payment invoice for target {target_user_id}")
    except Exception as e:
        logger.error(f"Error creating gift payment invoice: {e}", exc_info=True)
        await update.message.reply_html(
            f"❌ <b>Failed to create payment invoice.</b>\n\n"
            f"Error: {str(e)}"
        )

@handle_errors
async def cg_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Change gift comment (admin only)"""
    if not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ You are not authorized"))
        return
    
    # Check if admin provided new comment directly
    if context.args and len(context.args) > 0:
        new_comment = ' '.join(context.args)
        lc.gift_comment = new_comment
        db.set_gift_comment(new_comment)
        await update.message.reply_html(
            f"✅ <b>Gift comment updated!</b>\n\n"
            f"New comment: <b>{lc.gift_comment}</b>"
        )
        logger.info(f"Admin {user_id} changed gift comment to: {lc.gift_comment}")
        return
    
    # Show current comment and prompt for new one
    await update.message.reply_html(
        translate_text(
            f"💬 <b>Change Gift Comment</b>\n\n"
            f"Current comment: <b>{lc.gift_comment}</b>\n\n"
            f"Usage: /cg [new comment]\n\n"
            f"Example: /cg 💰 @Iibrate - be with the best!"
        )
    )

async def process_gift_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Process chat ID or username input - Step 2"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        return
    
    target_user_id = None
    target_username = None
    
    # Try to parse as user_id (numeric)
    try:
        target_user_id = int(text.strip())
        target_username = str(target_user_id)
    except ValueError:
        # Try to find by username
        username = text.strip()
        if username.startswith('@'):
            username = username[1:]
        username_lower = username.lower()
        
        if username_lower in lc.username_to_id:
            target_user_id = lc.username_to_id[username_lower]
            target_username = username
        else:
            await update.message.reply_html(
                "❌ <b>User not found!</b>\n\n"
                "Please provide a valid username or chat ID.\n\n"
                "Examples:\n"
                "• 123456789 (chat ID)\n"
                "• @username (username)\n"
                "• username (username without @)"
            )
            return
    
    # Save target user
    context.user_data['gift_target_user_id'] = target_user_id
    context.user_data['gift_target_username'] = target_username
    context.user_data['gift_state'] = 'waiting_for_pingme'
    
    await update.message.reply_html(
        f"✅ <b>Target user set: {target_username or target_user_id}</b>\n\n"
        f"Now send /pingme to create payment invoice"
    )
    
    logger.info(f"Admin {user_id} set gift target: {target_user_id} ({target_username})")

async def process_gift_after_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Automatically process gift after successful payment - Step 4"""
    user_id = update.effective_user.id
    target_user_id = context.user_data.get('gift_target_user_id')
    target_username = context.user_data.get('gift_target_username', str(target_user_id))
    
    if not target_user_id:
        logger.error(f"Gift processing failed: No target user ID for admin {user_id}")
        await update.message.reply_html(translate_text("❌ Target user not found. Gift process cancelled.", user_id=user_id))
        return
    
    try:
        # Get available gifts from Telegram API
        logger.info(f"Admin {user_id}: Getting available gifts from Telegram API")
        
        # Use get_available_gifts() method
        if hasattr(context.bot, 'get_available_gifts'):
            gifts_response = await context.bot.get_available_gifts()
        else:
            # Fallback: Use API directly
            gifts_response = await context.bot._post('getAvailableGifts', {})
        
        # Filter gifts where star_count <= 15
        available_gifts = []
        if hasattr(gifts_response, 'gifts'):
            gifts_list = gifts_response.gifts
        elif isinstance(gifts_response, dict) and 'gifts' in gifts_response:
            gifts_list = gifts_response['gifts']
        else:
            gifts_list = []
        
        for gift in gifts_list:
            star_count = getattr(gift, 'star_count', None) or gift.get('star_count', 0)
            if star_count <= GIFT_STARS:
                available_gifts.append(gift)
        
        if not available_gifts:
            logger.error(f"No suitable gifts found (all exceed {GIFT_STARS} stars)")
            await update.message.reply_html(
                f"❌ <b>No suitable gifts available.</b>\n\n"
                f"All available gifts exceed {GIFT_STARS} stars limit."
            )
            # Reset state
            context.user_data['gift_state'] = None
            context.user_data['gift_target_user_id'] = None
            context.user_data['gift_target_username'] = None
            return
        
        # Select gift closest to 15 stars (prefer highest <= 15)
        selected_gift = max(available_gifts, key=lambda g: getattr(g, 'star_count', 0) or g.get('star_count', 0))
        gift_id = getattr(selected_gift, 'id', None) or selected_gift.get('id')
        gift_stars = getattr(selected_gift, 'star_count', None) or selected_gift.get('star_count', 0)
        
        logger.info(f"Admin {user_id}: Selected gift ID {gift_id} with {gift_stars} stars")
        
        # Get template for gift command, or fallback to random message
        template_html, template_entities, template_reply_markup = get_template("gift")
        if template_html:
            # Replace variables in template
            target_user = update.effective_user if hasattr(update, 'effective_user') else None
            target_username = target_username if 'target_username' in locals() else f"User_{target_user_id}"
            gift_message = replace_template_variables(
                template_html,
                target_user_id,
                amount=gift_stars,
                balance=get_user_balance(target_user_id),
                username=target_username
            )
            logger.info(f"Using template for gift message to {target_user_id}")
        else:
            # Fallback to random gift message
            gift_message = get_random_gift_message()
            logger.info(f"Using random gift message for {target_user_id}")
        
        # Send gift to target user with gift message/note
        # Telegram Bot API uses 'message' parameter for gift notes
        gift_sent = False
        comment_sent_in_gift = False
        
        # Try with 'message' parameter first (official Telegram API parameter for gift notes)
        try:
            result = await context.bot._post(
                'sendGift',
                {
                    'user_id': target_user_id,
                    'gift_id': gift_id,
                    'message': gift_message
                }
            )
            gift_sent = True
            comment_sent_in_gift = True
            logger.info(f"✅ Sent gift with message/note (parameter: 'message') to {target_user_id}: {gift_message}")
        except Exception as e1:
            error_msg = str(e1).lower()
            logger.warning(f"Failed to send gift with 'message' parameter: {e1}")
            # Try with 'comment' parameter as fallback
            if 'message' in error_msg or 'unexpected' in error_msg or 'invalid' in error_msg:
                try:
                    result = await context.bot._post(
                        'sendGift',
                        {
                            'user_id': target_user_id,
                            'gift_id': gift_id,
                            'comment': gift_message
                        }
                    )
                    gift_sent = True
                    comment_sent_in_gift = True
                    logger.info(f"✅ Sent gift with message/note (parameter: 'comment') to {target_user_id}: {gift_message}")
                except Exception as e2:
                    logger.warning(f"Failed to send gift with 'comment' parameter: {e2}")
                    # Try with 'text' parameter as another fallback
                    try:
                        result = await context.bot._post(
                            'sendGift',
                            {
                                'user_id': target_user_id,
                                'gift_id': gift_id,
                                'text': gift_message
                            }
                        )
                        gift_sent = True
                        comment_sent_in_gift = True
                        logger.info(f"✅ Sent gift with message/note (parameter: 'text') to {target_user_id}: {gift_message}")
                    except Exception as e3:
                        # Last resort: send gift without message, then send message separately
                        logger.warning(f"None of the message parameters worked, sending gift without message: {e3}")
                        try:
                            result = await context.bot._post(
                                'sendGift',
                                {
                                    'user_id': target_user_id,
                                    'gift_id': gift_id
                                }
                            )
                            gift_sent = True
                            # Send gift message as separate message
                            try:
                                await context.bot.send_message(
                                    chat_id=target_user_id,
                                    text=gift_message
                                )
                                logger.info(f"Sent gift message as separate message to {target_user_id}: {gift_message}")
                            except Exception as msg_error:
                                logger.warning(f"Failed to send gift message separately: {msg_error}")
                        except Exception as e4:
                            logger.error(f"Error sending gift: {e4}", exc_info=True)
                            raise e4
        
        if not gift_sent:
            raise Exception("Failed to send gift after all attempts")
        
        logger.info(f"Admin {user_id}: Successfully sent gift {gift_id} ({gift_stars} stars) to {target_user_id}")
        
        # Send referral message to gift recipient IMMEDIATELY after gift is sent
        try:
            # Get or create referral code for recipient
            recipient_ref_code = get_or_create_referral_code(target_user_id)
            
            # Get bot username for referral link
            try:
                bot_info = await context.bot.get_me()
                bot_username = bot_info.username if bot_info.username else "Iibratebot"
            except Exception:
                bot_username = "Iibratebot"  # Fallback
            
            referral_link = f"t.me/{bot_username}?start=ref-{recipient_ref_code}"
            
            referral_message = (
                f"Invite your friends using your special link and receive a <b>daily gift</b> worth 10% from their activity 💝🔗\n\n"
                f"Claim your gift link:👉 {referral_link}\n\n"
                f"✅ The more friends you invite, the bigger your <b>daily gifts</b>!°\n\n"
                f"Gifts are credited every day automatically"
            )
            
            await context.bot.send_message(
                chat_id=target_user_id,
                text=referral_message,
                parse_mode=ParseMode.HTML
            )
            logger.info(f"Sent referral message immediately to gift recipient {target_user_id}")
        except Exception as ref_error:
            logger.warning(f"Failed to send referral message to {target_user_id}: {ref_error}")
            # Continue even if referral message fails
        
        # Confirm success to admin (after referral message is sent)
        await update.message.reply_html(
            translate_text(
                f"✅ <b>Payment received!</b>\n\n"
                f"🎂 <b>Processing gift...</b>\n\n"
                f"✅ <b>Gift sent successfully to user {target_username or target_user_id}!</b>\n\n"
                f"Gift ID: <code>{gift_id}</code>\n"
                f"Stars: {gift_stars} ⭐"
            )
        )
        
        # Reset state
        context.user_data['gift_state'] = None
        context.user_data['gift_target_user_id'] = None
        context.user_data['gift_target_username'] = None
        
    except Exception as e:
        logger.error(f"Error processing gift after payment: {e}", exc_info=True)
        await update.message.reply_html(
            f"❌ <b>Failed to send gift.</b>\n\n"
            f"Error: {str(e)}\n\n"
            f"{translate_text('Please try again or contact support.', user_id=user_id)}"
        )
        # Reset state on error
        context.user_data['gift_state'] = None
        context.user_data['gift_target_user_id'] = None
        context.user_data['gift_target_username'] = None

# -*- coding: utf-8 -*-
"""Message-template subsystem: a small sqlite store (templates.db) of per-command
HTML templates with variable substitution, plus send_template_message.

Lifted verbatim except the bridge for STARS_TO_USD / user_profiles (read via
``lc.*``). Re-imported into librate_casino so the call sites in command modules
(get_template / replace_template_variables / send_template_message) resolve.
"""

import json
import re
import sqlite3

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
from telegram.constants import ParseMode

import librate_casino as lc
from librate_casino import TEMPLATES_DB, logger, get_user_balance, track_bot_message


def init_templates_db():
    """Initialize the templates database"""
    conn = sqlite3.connect(TEMPLATES_DB)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS templates
                 (command_name TEXT PRIMARY KEY,
                  html_content TEXT,
                  entities TEXT,
                  reply_markup TEXT)''')
    conn.commit()
    conn.close()

def save_template(command_name, html_content, entities=None, reply_markup=None):
    """Save a template for a command"""
    try:
        init_templates_db()
        conn = sqlite3.connect(TEMPLATES_DB)
        c = conn.cursor()
        
        # Serialize entities and reply_markup to JSON
        entities_json = json.dumps(entities) if entities else None
        reply_markup_json = json.dumps(reply_markup) if reply_markup else None
        
        c.execute('''INSERT OR REPLACE INTO templates 
                     (command_name, html_content, entities, reply_markup)
                     VALUES (?, ?, ?, ?)''',
                  (command_name, html_content, entities_json, reply_markup_json))
        conn.commit()
        conn.close()
        logger.info(f"Template saved for command: /{command_name} - text length: {len(html_content)}, entities: {len(entities) if entities else 0}")
    except Exception as e:
        logger.error(f"Error saving template for /{command_name}: {e}", exc_info=True)
        raise

def get_template(command_name):
    """Get a template for a command"""
    try:
        init_templates_db()
        conn = sqlite3.connect(TEMPLATES_DB)
        c = conn.cursor()
        
        c.execute('SELECT html_content, entities, reply_markup FROM templates WHERE command_name = ?',
                  (command_name,))
        result = c.fetchone()
        conn.close()
        
        if result:
            html_content, entities_json, reply_markup_json = result
            entities = json.loads(entities_json) if entities_json else None
            reply_markup = json.loads(reply_markup_json) if reply_markup_json else None
            logger.info(f"Template retrieved for /{command_name}: text length={len(html_content) if html_content else 0}, entities={len(entities) if entities else 0}")
            return html_content, entities, reply_markup
        else:
            logger.debug(f"No template found in database for /{command_name}")
        return None, None, None
    except Exception as e:
        logger.error(f"Error retrieving template for /{command_name}: {e}", exc_info=True)
        return None, None, None

def replace_template_variables(template_html, user_id, **kwargs):
    """Replace variables in template HTML"""
    balance = get_user_balance(user_id)
    balance_usd = balance * lc.STARS_TO_USD
    profile = lc.user_profiles.get(user_id, {})
    username = profile.get('username', '')
    display_name = profile.get('display_name', '')
    
    # Default replacements
    replacements = {
        '{amount}': str(kwargs.get('amount', '')),
        '{balance}': f"{balance:,.0f}",
        '{balance_usd}': f"${balance_usd:.2f}",
        '{username}': username or display_name or f"User_{user_id}",
        '{user_id}': str(user_id)
    }
    
    # Add any additional kwargs
    for key, value in kwargs.items():
        if key not in ['amount', 'balance', 'username']:
            replacements[f'{{{key}}}'] = str(value)
    
    result = template_html
    for placeholder, value in replacements.items():
        result = result.replace(placeholder, value)
    
    return result

async def send_template_message(update_or_message, context, command_name, user_id, **kwargs):
    """Send a message using a template if available, otherwise use default"""
    from telegram import MessageEntity
    import re
    from html import unescape
    import html
    
    try:
        # Try to get template
        template_html, template_entities, template_reply_markup = get_template(command_name)
        
        if not template_html:
            logger.debug(f"No template found for /{command_name}")
            return None
        
        logger.info(f"Template found for /{command_name}")
        
        if template_html:
            logger.info(f"Template found for /{command_name}, processing...")
            # Template is saved as plain text, so use it directly
            template_plain = template_html  # It's already plain text
            
            # Replace variables in template (global emoji replace is applied by EmojiAwareBot when sending)
            message_text = replace_template_variables(template_plain, user_id, **kwargs)
            
            logger.info(f"Template text length: {len(template_plain)}, Message text length: {len(message_text)}")
            logger.info(f"Template entities count: {len(template_entities) if template_entities else 0}")
            if template_entities:
                logger.info(f"First entity: {template_entities[0] if template_entities else 'None'}")
            
            # Reconstruct entities with custom emojis
            # Need to recalculate offsets after variable replacement
            entities_list = []
            if template_entities:
                # First, find emoji positions in the original template (plain text)
                emoji_pattern = re.compile(
                    "["
                    "\U0001F600-\U0001F64F"
                    "\U0001F300-\U0001F5FF"
                    "\U0001F680-\U0001F6FF"
                    "\U0001F1E0-\U0001F1FF"
                    "\U00002702-\U000027B0"
                    "\U000024C2-\U0001F251"
                    "\U0001F900-\U0001F9FF"
                    "\U0001FA00-\U0001FA6F"
                    "\U0001FA70-\U0001FAFF"
                    "]+"
                )
                
                # Create a mapping of emoji -> custom_emoji_id from saved entities
                emoji_to_custom_id = {}
                for entity_dict in template_entities:
                    if entity_dict.get("type") == "CUSTOM_EMOJI":
                        # Find which emoji this entity refers to in original template
                        orig_offset = entity_dict.get("offset", 0)
                        orig_length = entity_dict.get("length", 0)
                        custom_emoji_id = entity_dict.get("custom_emoji_id")
                        
                        if orig_offset < len(template_plain) and custom_emoji_id:
                            emoji_in_template = template_plain[orig_offset:orig_offset + orig_length]
                            if emoji_in_template:
                                emoji_to_custom_id[emoji_in_template] = custom_emoji_id
                                logger.info(f"Mapped emoji '{emoji_in_template}' (offset {orig_offset}) to custom_emoji_id {custom_emoji_id}")
                
                logger.info(f"Created emoji mapping with {len(emoji_to_custom_id)} entries")
                
                # Now find emojis in the new message text and create entities
                matches = list(emoji_pattern.finditer(message_text))
                logger.debug(f"Found {len(matches)} emoji matches in message text")
                logger.debug(f"Emoji mapping has {len(emoji_to_custom_id)} entries: {list(emoji_to_custom_id.keys())}")
                
                for match in reversed(matches):
                    emoji = match.group()
                    start = match.start()
                    length = len(emoji)
                    
                    # Check if this emoji has a custom emoji version
                    if emoji in emoji_to_custom_id:
                        custom_emoji_id = emoji_to_custom_id[emoji]
                        try:
                            # Ensure custom_emoji_id is correct type
                            if isinstance(custom_emoji_id, str):
                                try:
                                    custom_emoji_id = int(custom_emoji_id)
                                except (ValueError, TypeError):
                                    pass
                            
                            entity = MessageEntity(
                                MessageEntity.CUSTOM_EMOJI,
                                start,
                                length,
                                custom_emoji_id=custom_emoji_id
                            )
                            entities_list.append(entity)
                            logger.debug(f"Created entity for emoji {emoji} at offset {start} with custom_emoji_id {custom_emoji_id}")
                        except Exception as e:
                            logger.error(f"Error creating entity for emoji {emoji}: {e}")
                            continue
                    else:
                        logger.debug(f"Emoji {emoji} not found in mapping")
                
                logger.info(f"Created {len(entities_list)} entities for custom emojis")
            
            # Sort entities by offset
            entities_list.sort(key=lambda e: e.offset)
            
            # Reconstruct reply_markup if present
            reply_markup = None
            if template_reply_markup:
                keyboard = []
                for row in template_reply_markup:
                    button_row = []
                    for button_dict in row:
                        text = button_dict.get("text", "")
                        if button_dict.get("callback_data"):
                            button_row.append(InlineKeyboardButton(text, callback_data=button_dict["callback_data"]))
                        elif button_dict.get("url"):
                            button_row.append(InlineKeyboardButton(text, url=button_dict["url"]))
                        else:
                            button_row.append(InlineKeyboardButton(text))
                    keyboard.append(button_row)
                reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
            
            # Send message with entities
            if entities_list:
                logger.info(f"Sending message with {len(entities_list)} custom emoji entities")
                # Use reply_text with entities parameter (parse_mode=None when using entities)
                if hasattr(update_or_message, 'reply_text'):
                    sent_msg = await update_or_message.reply_text(
                        message_text,
                        entities=entities_list,
                        reply_markup=reply_markup
                    )
                    logger.info(f"Message sent successfully with custom emojis")
                    # Track for /emoji
                    if sent_msg:
                        cid = sent_msg.chat.id if sent_msg.chat else None
                        if cid:
                            track_bot_message(cid, command_name, message_text, sent_msg.message_id)
                    return sent_msg
                else:
                    # Fallback to HTML without entities
                    logger.warning("Cannot use reply_text, falling back to HTML without entities")
                    return await update_or_message.reply_html(
                        message_text,
                        reply_markup=reply_markup
                    )
            else:
                # Send message (EmojiAwareBot applies global emoji replace)
                if hasattr(update_or_message, 'reply_html'):
                    sent = await update_or_message.reply_html(message_text, reply_markup=reply_markup)
                else:
                    sent = await update_or_message.reply_text(message_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
                if sent:
                    track_bot_message(sent.chat.id, command_name, message_text, sent.message_id)
                return sent
        
        # No template found, return None to use default message
        return None
    except Exception as e:
        logger.error(f"Error in send_template_message: {e}", exc_info=True)
        # Return None to fall back to default message
        return None

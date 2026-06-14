# -*- coding: utf-8 -*-
"""Pure blackjack engine: deck, scoring, table image, and resolution.

Extracted verbatim from librate_casino.py. These functions depend only on the
card constants below, the standard library, and Pillow — no bot state — so they
are safe to live on their own. librate_casino re-imports them, so all existing
call sites are unchanged.
"""

import random
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

BJ_SUITS = ["♠", "♣", "♥", "♦"]
BJ_VALUES = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]


def bj_create_deck():
    """Create and shuffle a full 52-card deck."""
    deck = []
    for suit in BJ_SUITS:
        for value in BJ_VALUES:
            deck.append({"suit": suit, "value": value})
    random.shuffle(deck)
    return deck


def bj_card_points(value):
    """Get point value of a card."""
    if value in ["J", "Q", "K"]:
        return 10
    elif value == "A":
        return 11
    else:
        return int(value)


def bj_calculate_score(cards):
    """Calculate hand score with ace logic."""
    score = 0
    aces = 0
    for card in cards:
        pts = bj_card_points(card["value"])
        score += pts
        if card["value"] == "A":
            aces += 1
    while score > 21 and aces:
        score -= 10
        aces -= 1
    return score


def bj_calculate_visible_score(bot_cards, hide_second=True):
    """Calculate visible score (hide second card if needed)."""
    if hide_second and len(bot_cards) > 1:
        visible = [bot_cards[0]]
    else:
        visible = bot_cards
    return bj_calculate_score(visible)


def bj_hand_str(cards):
    """Return human-readable hand string like 'K ♠ Q ♥'."""
    return " ".join(f"{c['value']}{c['suit']}" for c in cards)


def bj_generate_table_image(player_cards, bot_cards, hide_bot_second=True, result_text=None):
    """Generate a casino felt table image with cards."""
    width, height = 800, 500
    img = Image.new("RGB", (width, height), (25, 100, 50))
    draw = ImageDraw.Draw(img)

    # Felt texture - subtle gradient
    for y_pos in range(height):
        shade = int(25 + 8 * (y_pos / height))
        g = int(100 + 20 * (y_pos / height))
        for x_pos in range(0, width, 4):
            draw.point((x_pos, y_pos), fill=(shade, g, shade))

    # Gold wooden frame border
    draw.rectangle([(0, 0), (width - 1, height - 1)], outline=(139, 101, 42), width=8)
    draw.rectangle([(8, 8), (width - 9, height - 9)], outline=(184, 134, 11), width=3)
    draw.rectangle([(12, 12), (width - 13, height - 13)], outline=(218, 165, 32), width=1)

    # Try to load a nice font, fall back to default
    try:
        font_large = ImageFont.truetype("arial.ttf", 22)
        font_medium = ImageFont.truetype("arial.ttf", 18)
        font_small = ImageFont.truetype("arial.ttf", 14)
        font_suit_big = ImageFont.truetype("arial.ttf", 30)
        font_score = ImageFont.truetype("arialbd.ttf", 20)
    except (OSError, IOError):
        try:
            font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
            font_medium = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
            font_suit_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 30)
            font_score = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        except (OSError, IOError):
            font_large = ImageFont.load_default()
            font_medium = font_large
            font_small = font_large
            font_suit_big = font_large
            font_score = font_large

    def draw_card(x, y, value, suit, hidden=False, glow=False):
        card_w, card_h = 90, 120

        if glow:
            draw.rectangle(
                [(x - 4, y - 4), (x + card_w + 4, y + card_h + 4)],
                outline=(0, 255, 100), width=3
            )

        if hidden:
            # Face down card
            draw.rectangle([(x, y), (x + card_w, y + card_h)],
                           fill=(20, 40, 120), outline=(200, 200, 200), width=2)
            draw.rectangle([(x + 8, y + 8), (x + card_w - 8, y + card_h - 8)],
                           outline=(100, 100, 200), width=1)
            # Diamond pattern on back
            cx, cy_c = x + card_w // 2, y + card_h // 2
            draw.polygon([(cx, cy_c - 25), (cx + 18, cy_c), (cx, cy_c + 25), (cx - 18, cy_c)],
                         outline=(80, 80, 180), fill=(30, 50, 140))
        else:
            # Face up card - white with rounded feel
            draw.rectangle([(x, y), (x + card_w, y + card_h)],
                           fill=(255, 255, 255), outline=(180, 180, 180), width=2)

            suit_colors = {"♠": (0, 0, 0), "♣": (0, 0, 0), "♥": (200, 0, 0), "♦": (200, 0, 0)}
            rgb = suit_colors.get(suit, (0, 0, 0))

            # Value top left
            draw.text((x + 8, y + 6), value, fill=rgb, font=font_medium)
            draw.text((x + 8, y + 26), suit, fill=rgb, font=font_small)

            # Value bottom right
            draw.text((x + card_w - 24, y + card_h - 40), value, fill=rgb, font=font_medium)
            draw.text((x + card_w - 22, y + card_h - 22), suit, fill=rgb, font=font_small)

            # Large suit center
            draw.text((x + card_w // 2, y + card_h // 2), suit, fill=rgb, font=font_suit_big, anchor="mm")

    def draw_score_bubble(cx, cy, score, color=(50, 50, 50)):
        bubble_w = 70
        bubble_h = 30
        x1 = cx - bubble_w // 2
        y1 = cy - bubble_h // 2
        x2 = cx + bubble_w // 2
        y2 = cy + bubble_h // 2
        draw.rounded_rectangle([(x1, y1), (x2, y2)], radius=15, fill=color)
        draw.text((cx, cy), str(score), fill="white", font=font_score, anchor="mm")

    # "DEALER" label
    draw.text((width // 2, 30), "DEALER", fill=(200, 200, 200), font=font_medium, anchor="mm")

    # BOT CARDS - top area
    bot_start_x = width // 2 - (len(bot_cards) * 100) // 2
    for i, card in enumerate(bot_cards):
        cx = bot_start_x + i * 100
        cy = 55
        hidden = (i == 1 and hide_bot_second)
        draw_card(cx, cy, card["value"], card["suit"], hidden=hidden)

    # Bot score bubble
    visible_score = bj_calculate_visible_score(bot_cards, hide_bot_second)
    score_label = str(visible_score) if not (hide_bot_second and len(bot_cards) > 1) else f"{visible_score}+?"
    draw_score_bubble(width // 2, 195, score_label, color=(50, 50, 50))

    # "PLAYER" label
    draw.text((width // 2, 240), "PLAYER", fill=(200, 200, 200), font=font_medium, anchor="mm")

    # PLAYER CARDS - bottom area with green glow
    player_start_x = width // 2 - (len(player_cards) * 100) // 2
    for i, card in enumerate(player_cards):
        cx = player_start_x + i * 100
        cy = 260
        draw_card(cx, cy, card["value"], card["suit"], glow=True)

    # Player score bubble
    player_score = bj_calculate_score(player_cards)
    draw_score_bubble(width // 2, 400, player_score, color=(0, 150, 80))

    # Result banner if game is over
    if result_text:
        # Semi-transparent overlay band
        overlay = Image.new("RGBA", (width, 50), (0, 0, 0, 160))
        img_rgba = img.convert("RGBA")
        img_rgba.paste(overlay, (0, height // 2 - 25), overlay)
        img = img_rgba.convert("RGB")
        draw = ImageDraw.Draw(img)
        draw.text((width // 2, height // 2), result_text, fill=(255, 255, 100), font=font_large, anchor="mm")

    output = BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    return output


def bj_resolve(player_score, bot_score, bet, is_natural_bj=False):
    """Resolve blackjack game. Returns (result_type, payout).
    payout is total amount returned to player (0 = lost everything)."""
    if player_score > 21:
        return "bust", 0
    if is_natural_bj:
        if bot_score == 21:
            return "push", bet
        return "blackjack", int(bet * 2.5)
    if bot_score > 21:
        return "win", bet * 2
    if player_score > bot_score:
        return "win", bet * 2
    if player_score == bot_score:
        return "push", bet
    return "loss", 0

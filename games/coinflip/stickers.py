# -*- coding: utf-8 -*-
"""Coinflip sticker persistence (heads/tails file_id <-> coinflip_stickers.json)."""

import json

from librate_casino import coinflip_stickers, COINFLIP_STICKERS_FILE


def load_coinflip_stickers():
    global coinflip_stickers
    try:
        with open(COINFLIP_STICKERS_FILE, "r") as f:
            coinflip_stickers.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass

def save_coinflip_stickers():
    with open(COINFLIP_STICKERS_FILE, "w") as f:
        json.dump(coinflip_stickers, f)

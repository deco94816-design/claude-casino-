# -*- coding: utf-8 -*-
"""Mines engine: bomb placement, tile clicks, multiplier and cash-out math.

Pure, self-contained (stdlib only) — extracted verbatim from librate_casino and
re-imported there, so all call sites (and the mines_games store) are unchanged.
"""

import random
from datetime import datetime


class MinesGame:
    def __init__(self, user_id, grid_size, num_mines, bet_amount):
        self.user_id = user_id
        self.grid_size = grid_size
        self.num_mines = num_mines
        self.bet_amount = bet_amount
        self.diamonds_found = 0
        self.opened_tiles = set()  # (row, col) tuples
        self.mines_positions = set()  # (row, col) tuples
        self.game_id = f"{user_id}_{datetime.now().timestamp()}"
        self.game_state = "playing"  # "playing", "cashed_out", "lost"
        self.last_click_time = datetime.now()
        
        # Generate mines randomly
        total_tiles = grid_size * grid_size
        safe_tiles = list(range(total_tiles))
        random.shuffle(safe_tiles)
        self.mines_positions = set()
        for i in range(num_mines):
            row = safe_tiles[i] // grid_size
            col = safe_tiles[i] % grid_size
            self.mines_positions.add((row, col))
    
    def click_tile(self, row, col):
        """Click a tile, return True if diamond, False if mine"""
        if (row, col) in self.opened_tiles:
            return None  # Already opened
        
        self.opened_tiles.add((row, col))
        self.last_click_time = datetime.now()
        
        if (row, col) in self.mines_positions:
            self.game_state = "lost"
            return False  # Hit a mine
        
        self.diamonds_found += 1
        return True  # Found diamond
    
    def calculate_multiplier(self):
        """Calculate current multiplier based on grid size and diamonds found"""
        total_tiles = self.grid_size * self.grid_size
        total_safe = total_tiles - self.num_mines
        multiplier = (total_tiles / total_safe) ** self.diamonds_found
        return round(multiplier, 2)
    
    def get_current_win(self):
        """Get current win amount"""
        multiplier = self.calculate_multiplier()
        return round(self.bet_amount * multiplier)
    
    def cash_out(self):
        """Cash out and end game"""
        self.game_state = "cashed_out"
        return self.get_current_win()

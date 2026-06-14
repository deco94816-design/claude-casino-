# -*- coding: utf-8 -*-
"""Central session storage — one :class:`SessionStore` per game.

The 5 point-based games (dice/dart/bowl/football/basket) share ONE store; their
sessions carry a ``game_type`` field to distinguish variants. Game modules
migrate their legacy global session dicts onto these stores incrementally.
"""

from games.base import SessionStore

blackjack_store = SessionStore()   # /blackjack
mines_store     = SessionStore()   # /mines
coinflip_store  = SessionStore()   # /cf
predict_store   = SessionStore()   # /predict
dice_store      = SessionStore()   # /dice /dart /bowl /football /basket (shared)
tower_store     = SessionStore()   # /tower
claw_store      = SessionStore()   # /claw
roulette_store  = SessionStore()   # /roulette

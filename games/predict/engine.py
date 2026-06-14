# -*- coding: utf-8 -*-
"""Predict engine: payout multiplier from selection count with house edge.

Pure math extracted verbatim from librate_casino and re-imported there.
PREDICT_HOUSE_EDGE (a constant) is imported from librate_casino.
"""

from librate_casino import PREDICT_HOUSE_EDGE


def predict_get_multiplier(selected, selection_type):
    """Calculate multiplier based on selection count with house edge"""
    if selection_type in ("even", "odd", "low", "high"):
        count = 3
    else:
        count = len(selected)
    if count == 0 or count >= 6:
        return 0.0
    raw = 6.0 / count
    mult = round(raw * (1 - PREDICT_HOUSE_EDGE), 2)
    return mult

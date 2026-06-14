# -*- coding: utf-8 -*-
"""Display formatting helpers (pure; extracted verbatim).

Re-imported into librate_casino so existing call sites are unchanged.
"""

from datetime import datetime


def format_timer(expires_at):
    """Format remaining time as H:MM:SS"""
    from datetime import datetime
    now = datetime.now()
    if expires_at <= now:
        return "0:00:00"
    remaining = expires_at - now
    total_seconds = int(remaining.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours}:{minutes:02d}:{seconds:02d}"


def format_time_remaining(target_time):
    """Format time remaining as 'X Days HH:MM:SS'"""
    now = datetime.now()
    if target_time <= now:
        return "0 Days 00:00:00"
    
    delta = target_time - now
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    return f"{days} Days {hours:02d}:{minutes:02d}:{seconds:02d}"


def create_progress_bar(percentage, length=20):
    """Create a progress bar with filled and empty blocks"""
    try:
        percentage = float(percentage) if percentage else 0.0
        percentage = max(0, min(100, percentage))
        filled = int((percentage / 100) * length)
        empty = max(0, length - filled)
        return "▰" * filled + "▱" * empty
    except Exception:
        return "▱" * length


def format_withdrawal_status(status):
    """Format withdrawal status for display"""
    status_map = {
        'on_hold': 'â ³ Pending',
        'cancelled': '🚫 Cancelled',
        'completed': '✅ Completed',
        'draft': '📝 Draft'
    }
    return status_map.get(status, status)


def format_withdrawal_date(date_str):
    """Format withdrawal date for display"""
    try:
        if isinstance(date_str, str):
            dt = datetime.fromisoformat(date_str)
            return dt.strftime("%d.%m %H:%M")
        return str(date_str)
    except:
        return str(date_str)

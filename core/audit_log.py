# -*- coding: utf-8 -*-
"""Lightweight structured audit log for money-affecting events.

Additive, dependency-free. Routes to a dedicated ``optimus.audit`` logger so
financial events (balance adjustments, bankroll moves, jackpot claims,
deposits/withdrawals) can be filtered or shipped separately from app logs.
"""

from __future__ import annotations

import logging
from typing import Any

audit_logger = logging.getLogger("optimus.audit")


def log_event(event: str, user_id: int | None = None, **fields: Any) -> None:
    """Record an audit event as a single structured log line."""
    parts = [f"event={event}"]
    if user_id is not None:
        parts.append(f"user={user_id}")
    parts.extend(f"{k}={v}" for k, v in fields.items())
    audit_logger.info(" ".join(parts))

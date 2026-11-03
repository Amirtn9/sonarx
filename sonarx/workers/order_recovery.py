"""Watchdog for abandoned order state transitions."""
from __future__ import annotations


def recover_stale_orders(db, older_than_minutes: int = 15) -> dict:
    stale = db.stale_processing_orders(older_than_minutes)
    changed = db.mark_stale_orders_for_review(older_than_minutes)
    return {"found": len(stale), "moved_to_review": changed,
            "order_ids": [str(row["id"]) for row in stale]}

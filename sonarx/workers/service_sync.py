"""Synchronize service usage and lifecycle snapshots from configured VPN panels.

This worker only reads panel state and updates SonarX's local snapshot. It does not
perform destructive remote actions (disable/delete), so a transient API mismatch
cannot accidentally disconnect a customer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..pasarguard import PasarGuardClient, MarzbanClient, MarzneshinClient
from ..db import UNSET


def _as_datetime(value: Any):
    if value in (None, "", 0, "0"):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        try:
            return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError):
            return None


def _status(remote: dict, expires_at: str | None, used: int, limit: int) -> str:
    raw = str(remote.get("status") or "").lower()
    enabled = remote.get("enabled")
    if raw == "expired":
        return "expired"
    if enabled is False or raw in {"disabled", "inactive", "limited", "on_hold"}:
        return "disabled" if raw != "limited" else "limited"
    now = datetime.now(timezone.utc)
    if expires_at:
        try:
            if datetime.fromisoformat(expires_at).astimezone(timezone.utc) <= now:
                return "expired"
        except ValueError:
            pass
    if limit > 0 and used >= limit:
        return "limited"
    if raw in {"active", "online", "enabled"} or enabled is True:
        return "active"
    return "active"


def sync_services(db, secret_box, limit: int = 500, client_factory=None, verify_tls: bool = True) -> dict:
    """Sync a bounded batch and return counts; per-service errors do not abort the batch."""
    factory = client_factory or (lambda row, box: _client(row, box, verify_tls))
    result = {"checked": 0, "updated": 0, "failed": 0, "errors": []}
    rows = db.services_for_sync(limit=limit)
    for row in rows:
        result["checked"] += 1
        try:
            if not row.get("server_active"):
                continue
            client = factory(row, secret_box)
            remote = client.get_user(row["remote_username"])
            used_value = remote.get("used_traffic")
            if used_value is None:
                used_value = remote.get("used_bytes", row.get("used_bytes", 0))
            used = int(used_value or 0)
            quota_value = remote.get("data_limit")
            if quota_value is None:
                quota_value = row.get("data_limit_bytes", 0)
            quota = int(quota_value or 0)
            expiry_keys = ("expire", "expire_date", "expires_at", "expiryTime")
            expiry_entries = [(key, remote[key]) for key in expiry_keys if key in remote]
            if not expiry_entries:
                expiry = UNSET
                expiry_for_status = row.get("expires_at")
            else:
                selected = next(((key, value) for key, value in expiry_entries
                                 if value not in (None, "", 0, "0")), None)
                if selected is None:
                    expiry = None
                else:
                    expiry_key, raw_expiry = selected
                    if expiry_key == "expiryTime":
                        raw_expiry = float(raw_expiry) / 1000
                    expiry = _as_datetime(raw_expiry)
                expiry_for_status = expiry
            sub = client._sub(remote) if hasattr(client, "_sub") else None
            status = _status(remote, expiry_for_status, used, quota)
            ok = db.update_service_snapshot(
                row["id"], row["user_id"], used_bytes=used, limit_bytes=quota,
                expires_at=expiry, status=status, subscription_url=sub
            )
            if ok:
                result["updated"] += 1
            else:
                raise RuntimeError("local service snapshot could not be updated")
        except Exception as exc:
            result["failed"] += 1
            result["errors"].append({"service_id": row.get("id"), "error": str(exc)[:300]})
    return result


def _client(row, secret_box, verify_tls=True):
    classes = {
        "pasarguard": PasarGuardClient,
        "marzban": MarzbanClient,
        "marzneshin": MarzneshinClient,
    }
    cls = classes.get(row.get("panel_type") or "pasarguard")
    if cls is None:
        raise ValueError("Unsupported panel type: " + str(row.get("panel_type")))
    if not secret_box:
        raise RuntimeError("FERNET_KEY is not configured")
    client = cls(row["url"], row["panel_username"], secret_box.decrypt(row["password_enc"]), verify_tls=verify_tls)
    return client

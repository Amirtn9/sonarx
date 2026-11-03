"""Small, side-effect-free access guards and role policy for SonarX."""
from __future__ import annotations


ROLE_PERMISSIONS = {
    "owner": {"*"},
    "admin": {"*"},
    "operator": {"dashboard", "store", "users_read", "support"},
    "finance": {"dashboard", "finance", "orders_review", "users_read", "reports"},
    "support": {"dashboard", "support", "users_read"},
    "marketing": {"dashboard", "growth", "reports"},
    "viewer": {"dashboard", "reports"},
}

SECTION_PERMISSIONS = {
    "overview": "dashboard",
    "store": "store",
    "finance": "finance",
    "users": "users_read",
    "support": "support",
    "growth": "growth",
    "interface": "settings",
    "system": "orders_review",
}


def role_allows(role: str | None, permission: str) -> bool:
    permissions = ROLE_PERMISSIONS.get(str(role or "").strip().lower(), set())
    return "*" in permissions or permission in permissions


def maintenance_enabled_for_user(uid: int, feature_getter, admin_checker) -> bool:
    """Maintenance mode blocks regular users while allowing administrators through."""
    enabled = feature_getter("maintenance", "0")
    enabled = enabled if isinstance(enabled, bool) else str(enabled) == "1"
    return bool(enabled) and not bool(admin_checker(uid))


def is_blocked_user(user: dict | None, is_admin: bool = False) -> bool:
    """Blocked users cannot use the bot; an administrator is never locked out."""
    return bool(user and user.get("is_blocked")) and not bool(is_admin)


def admin_permission_for_callback(data: str) -> str | None:
    """Map admin callback payloads to a permission; user callbacks return None."""
    data = str(data or "")
    if data.startswith("admin_section:"):
        return SECTION_PERMISSIONS.get(data.split(":", 1)[1])
    exact = {
        "admin": "dashboard", "stats": "dashboard", "reports": "reports", "audit": "audit", "admin_reviews": "orders_review",
        "admin_config": "settings", "admin_config_toggles": "settings", "admin_config_texts": "settings",
        "admin_products": "store", "admin_servers": "store", "add_product": "store", "add_server": "store",
        "trial_settings": "store", "custom_plan_settings": "store", "categories": "store", "catadd": "store",
        "bulk_accounts": "store", "finance": "finance", "admin_deposits": "finance", "discounts": "finance",
        "discount_add": "finance", "gift_codes": "finance", "gift_add": "finance", "addon_prices": "finance",
        "cashback_settings": "finance", "credit": "finance", "admin_users": "users_read", "user_search": "users_read",
        "admin_tickets": "support", "channels": "support", "channel_add": "support", "channel_post": "support",
        "admin_admins": "security", "admin_add": "security", "admin_remove": "security",
        "user_block": "users_manage", "broadcast": "users_manage", "lottery_admin": "growth",
        "lottery_draw": "growth", "wheel_settings": "growth", "resellers": "growth",
        "reseller_requests": "growth", "reseller_add": "growth", "button_manager": "settings",
        "button_add": "settings",
    }
    if data in exact:
        return exact[data]
    prefixes = (
        ("product_manage:", "store"), ("pprice:", "store"), ("ptoggle:", "store"),
        ("server_details:", "store"), ("server_test:", "store"), ("stoggle:", "store"), ("catdel:", "store"),
        ("dep_review:", "finance"), ("dep:", "finance"), ("discount_toggle:", "finance"),
        ("user_credit_start:", "finance"), ("review_detail:", "orders_review"),
        ("reviewreconcile:", "orders_review"), ("reviewrefund_prepare:", "orders_review"),
        ("reviewrefund:", "orders_review"), ("user_detail:", "users_read"),
        ("user_message_start:", "support"), ("ticket:", "support"), ("ticketclose:", "support"),
        ("reseller_request_review:", "growth"), ("reseller_request_decide:", "growth"),
        ("button_manage:", "settings"), ("button_toggle:", "settings"),
        ("button_edit:", "settings"), ("button_delete:", "settings"),
        ("cfg:", "settings"), ("edittext:", "settings"), ("feature_matrix:", "settings"),
        ("fx:", "settings"), ("user_block_toggle:", "users_manage"),
    )
    for prefix, permission in prefixes:
        if data.startswith(prefix):
            return permission
    return None


def admin_permission_for_state(step: str | None) -> str | None:
    """Map stateful admin text-entry workflows to the permission they mutate."""
    return {
        "add_product": "store", "add_server": "store", "product_price": "store", "category_add": "store",
        "bulk_accounts": "store", "trial_settings": "store", "custom_plan_settings": "store",
        "credit": "finance", "addon_prices": "finance", "discount_add": "finance", "gift_add": "finance",
        "cashback_settings": "finance", "user_block": "users_manage", "broadcast": "users_manage",
        "admin_add": "security", "admin_remove": "security", "set_text": "settings",
        "button_edit": "settings", "button_add": "settings", "ticket_reply": "support",
        "channel_add": "support", "channel_post": "support", "user_message": "support",
        "user_search": "users_read", "review_deposit": "finance",
        "confirm_deposit_decision": "finance", "confirm_review_refund": "orders_review",
        "confirm_button_delete": "settings",
        "reseller_add": "growth", "wheel_settings": "growth",
    }.get(step)

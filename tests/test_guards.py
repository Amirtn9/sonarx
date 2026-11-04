import unittest
import ast
from pathlib import Path

from sonarx.guards import (
    admin_permission_for_callback, admin_permission_for_state, is_blocked_user,
    maintenance_enabled_for_user, role_allows
)


class AccessGuardTests(unittest.TestCase):
    def test_maintenance_blocks_regular_users(self):
        feature = lambda key, default: "1" if key == "maintenance" else default
        self.assertTrue(maintenance_enabled_for_user(101, feature, lambda uid: False))

    def test_maintenance_accepts_boolean_feature_helpers(self):
        feature = lambda key, default: True
        self.assertTrue(maintenance_enabled_for_user(101, feature, lambda uid: False))

    def test_maintenance_does_not_block_admins(self):
        feature = lambda key, default: "1" if key == "maintenance" else default
        self.assertFalse(maintenance_enabled_for_user(101, feature, lambda uid: True))

    def test_maintenance_is_off_by_default(self):
        feature = lambda key, default: default
        self.assertFalse(maintenance_enabled_for_user(101, feature, lambda uid: False))

    def test_bot_module_defines_the_maintenance_guard_used_by_callbacks(self):
        bot_file = Path(__file__).resolve().parents[1] / "sonarx" / "bot.py"
        tree = ast.parse(bot_file.read_text(encoding="utf-8"))
        functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        self.assertIn("maintenance", functions)

    def test_maintenance_guard_reads_setting_without_late_feature_helper(self):
        bot_file = Path(__file__).resolve().parents[1] / "sonarx" / "bot.py"
        tree = ast.parse(bot_file.read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "maintenance")
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        attributes = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        self.assertIn("db", names)
        self.assertIn("get_setting", attributes)
        self.assertNotIn("feature", names)

    def test_bot_wraps_expired_callback_acknowledgements(self):
        bot_file = Path(__file__).resolve().parents[1] / "sonarx" / "bot.py"
        source = bot_file.read_text(encoding="utf-8")
        self.assertIn("_safe_answer_callback_query", source)
        self.assertIn("query is too old", source)
        self.assertIn("query id is invalid", source)

    def test_private_and_blocked_guards_precede_feature_handlers(self):
        bot_file = Path(__file__).resolve().parents[1] / "sonarx" / "bot.py"
        tree = ast.parse(bot_file.read_text(encoding="utf-8"))
        message_handlers = []
        callback_handlers = []
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                value = ast.unparse(decorator)
                if "bot.message_handler" in value:
                    message_handlers.append(node.name)
                elif "bot.callback_query_handler" in value:
                    callback_handlers.append(node.name)
        self.assertEqual(message_handlers[:3], ["reject_non_private_message", "reject_blocked_message", "reject_admin_state_message"])
        self.assertEqual(callback_handlers[:3], ["reject_non_private_callback", "reject_blocked_callback", "reject_admin_permission"])

    def test_admin_roles_are_restricted_by_permission(self):
        self.assertTrue(role_allows("finance", "finance"))
        self.assertTrue(role_allows("finance", "orders_review"))
        self.assertFalse(role_allows("finance", "security"))
        self.assertFalse(role_allows("support", "finance"))
        self.assertTrue(role_allows("owner", "security"))
        self.assertFalse(role_allows("unknown-role", "dashboard"))

    def test_admin_callback_and_state_permissions_are_classified(self):
        self.assertEqual(admin_permission_for_callback("admin_section:finance"), "finance")
        self.assertEqual(admin_permission_for_callback("dep:yes:10"), "finance")
        self.assertEqual(admin_permission_for_callback("admin_admins"), "security")
        self.assertIsNone(admin_permission_for_callback("service_refresh:10"))
        self.assertEqual(admin_permission_for_state("ticket_reply"), "support")
        self.assertEqual(admin_permission_for_state("confirm_review_refund"), "orders_review")

    def test_every_admin_menu_action_has_an_explicit_permission(self):
        bot_file = Path(__file__).resolve().parents[1] / "sonarx" / "bot.py"
        tree = ast.parse(bot_file.read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "ADMIN_SECTIONS" for t in n.targets))
        sections = ast.literal_eval(node.value)
        for section, (_title, _description, items) in sections.items():
            for _label, callback in items:
                self.assertIsNotNone(
                    admin_permission_for_callback(callback),
                    f"unmapped admin action in {section}: {callback}"
                )

    def test_blocked_user_is_denied_but_admin_is_not_locked_out(self):
        self.assertTrue(is_blocked_user({"is_blocked": 1}, False))
        self.assertFalse(is_blocked_user({"is_blocked": 1}, True))
        self.assertFalse(is_blocked_user({"is_blocked": 0}, False))
        self.assertFalse(is_blocked_user(None, False))


    def test_blocked_guard_fails_closed_when_database_lookup_raises(self):
        bot_file = Path(__file__).resolve().parents[1] / "sonarx" / "bot.py"
        tree = ast.parse(bot_file.read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "blocked")
        handler = next(h for h in node.body if isinstance(h, ast.Try)).handlers[0]
        returns = [n for n in ast.walk(handler) if isinstance(n, ast.Return)]
        self.assertTrue(any(ast.unparse(n.value) == "not admin(uid)" for n in returns))


if __name__ == "__main__":
    unittest.main()

import os, tempfile, unittest
from datetime import datetime, timedelta, timezone
from sonarx.db import Database, PgConn
from sonarx.workers.order_recovery import recover_stale_orders
from sonarx.workers.service_sync import sync_services


class FakeCursor:
    rowcount = 1
    def fetchone(self): return None
    def fetchall(self): return []


class FakeConnection:
    def __init__(self): self.sql = []
    def execute(self, sql, params=()):
        self.sql.append(str(sql))
        return FakeCursor()


class WizWizParityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "test.sqlite3"))
        self.db.user(501, "parity", "Parity")
        self.db.add_product("Parity plan", "test", 1000, 10, 30, "[]")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_pg_adapter_does_not_return_missing_natural_ids(self):
        conn = FakeConnection()
        pg = PgConn(conn)
        pg.execute("INSERT INTO discount_codes(code,percent) VALUES(?,?)", ("X", 10))
        pg.execute("INSERT INTO gift_codes(code,amount) VALUES(?,?)", ("Y", 100))
        pg.execute("INSERT INTO resellers(user_id,commission_percent) VALUES(?,?)", (501, 5))
        self.assertTrue(all("RETURNING id" not in q for q in conn.sql))

    def test_expired_discount_is_rejected(self):
        self.db.add_discount("EXPIRED", 20, 10)
        with self.db.tx() as c:
            expired = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            c.execute("UPDATE discount_codes SET expires_at=? WHERE code=?", (expired, "EXPIRED"))
        self.assertIsNone(self.db.get_discount("EXPIRED"))
        self.assertIsNone(self.db.reserve_discount("EXPIRED"))

    def test_review_order_can_be_completed_without_duplicate_remote_create(self):
        product = self.db.product(1)
        self.db.add_credit(501, 2000, "seed-review")
        oid = self.db.begin_order(501, product)
        self.db.add_server("panel", "https://panel.invalid", "admin", "encrypted")
        server = self.db.servers()[0]
        self.db.mark_order_review(oid, "panel response uncertain", server["id"], "vpn501")
        self.assertTrue(self.db.complete_review_order(oid, "https://sub.invalid/u", 10 * 1024**3, None))
        self.assertFalse(self.db.complete_review_order(oid, "https://sub.invalid/u", 10 * 1024**3, None))
        self.assertEqual(len(self.db.services_for(501)), 1)
        self.assertEqual(self.db.orders_for(501)[0]["status"], "completed")

    def test_stale_processing_orders_move_to_review(self):
        self.db.add_credit(501, 2000, "seed-stale")
        oid = self.db.begin_order(501, self.db.product(1))
        self.db.add_server("recovery-panel", "https://panel.invalid", "admin", "encrypted")
        server = self.db.servers()[0]
        self.assertTrue(self.db.set_order_panel_target(oid, server["id"], "recovery-user"))
        with self.db.tx() as c:
            old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
            c.execute("UPDATE orders SET created_at=? WHERE id=?", (old, oid))
        report = recover_stale_orders(self.db, 10)
        self.assertEqual(report["moved_to_review"], 1)
        recovered = self.db.review_orders()[0]
        self.assertEqual(recovered["id"], oid)
        self.assertEqual(recovered["server_id"], server["id"])
        self.assertEqual(recovered["remote_username"], "recovery-user")

    def test_service_sync_preserves_expiry_when_panel_omits_expiry_field(self):
        self.db.add_credit(501, 2000, "seed-sync-missing-expiry")
        oid = self.db.begin_order(501, self.db.product(1))
        self.db.add_server("panel-missing-expiry", "https://panel.invalid", "admin", "encrypted")
        server = self.db.servers()[0]
        self.db.finish_order(oid, server["id"], "vpn502", "https://sub.invalid/vpn")
        before = self.db.services_for(501)[0]["expires_at"]

        class Box:
            def decrypt(self, value): return "password"
        class Client:
            def get_user(self, username):
                return {"used_traffic": 50, "data_limit": 10 * 1024**3,
                        "status": "active", "subscription_url": "https://sub.invalid/vpn"}
            @staticmethod
            def _sub(data): return data.get("subscription_url")

        report = sync_services(self.db, Box(), client_factory=lambda row, box: Client())
        self.assertEqual(report["updated"], 1)
        after = self.db.services_for(501)[0]["expires_at"]
        self.assertEqual(after, before)

    def test_service_sync_updates_usage_and_expiry(self):
        self.db.add_credit(501, 2000, "seed-sync")
        oid = self.db.begin_order(501, self.db.product(1))
        self.db.add_server("panel", "https://panel.invalid", "admin", "encrypted")
        server = self.db.servers()[0]
        self.db.finish_order(oid, server["id"], "vpn501", "https://sub.invalid/vpn")
        class Box:
            def decrypt(self, value): return "password"
        class Client:
            def get_user(self, username):
                return {"used_traffic": 1234, "data_limit": None, "expire": None,
                        "status": "active", "subscription_url": "https://sub.invalid/new"}
            @staticmethod
            def _sub(data): return data.get("subscription_url")
        report = sync_services(self.db, Box(), client_factory=lambda row, box: Client())
        self.assertEqual(report["updated"], 1)
        service = self.db.services_for(501)[0]
        self.assertEqual(service["used_bytes"], 1234)
        self.assertEqual(service["data_limit_bytes"], 10 * 1024**3)
        self.assertIsNone(service["expires_at"], "an explicit null expiry from the panel must clear a stale local expiry")
        self.assertEqual(service["subscription_url"], "https://sub.invalid/new")


if __name__ == "__main__":
    unittest.main()

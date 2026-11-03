import os, tempfile, unittest
from datetime import datetime, timedelta, timezone
from sonarx.db import Database, PgConn
from sonarx.workers.order_recovery import recover_stale_orders
from sonarx.workers.service_sync import sync_services, _status


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
        self.assertIsNone(self.db.services_for(501)[0]["expires_at"])
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

    def test_service_sync_reuses_one_authenticated_client_per_server(self):
        class FakeDB:
            def __init__(self):
                self.rows=[
                    {"id":1,"server_id":7,"server_active":1,"panel_type":"pasarguard","url":"https://panel",
                     "panel_username":"admin","remote_username":"one","user_id":501,"used_bytes":0,
                     "data_limit_bytes":100,"expires_at":None},
                    {"id":2,"server_id":7,"server_active":1,"panel_type":"pasarguard","url":"https://panel",
                     "panel_username":"admin","remote_username":"two","user_id":502,"used_bytes":0,
                     "data_limit_bytes":100,"expires_at":None},
                ]
                self.updated=[]
            def services_for_sync(self,limit=500): return self.rows
            def update_service_snapshot(self,*args,**kwargs):
                self.updated.append(args[0])
                return True
        class Client:
            def __init__(self): self.users=[]
            def get_user(self,username):
                self.users.append(username)
                return {"used_traffic":0,"data_limit":100,"status":"active"}
            def subscription_url(self,remote,username): return None
        db=FakeDB(); created=[]
        def factory(row,box):
            client=Client();created.append(client);return client
        report=sync_services(db,None,client_factory=factory)
        self.assertEqual(report["updated"],2)
        self.assertEqual(len(created),1)
        self.assertEqual(created[0].users,["one","two"])

    def test_service_sync_rejects_invalid_expiry_without_overwriting_snapshot(self):
        class FakeDB:
            def __init__(self):
                self.row={"id":3,"server_id":9,"server_active":1,"panel_type":"pasarguard","url":"https://panel",
                          "panel_username":"admin","remote_username":"broken","user_id":501,"used_bytes":10,
                          "data_limit_bytes":100,"expires_at":"2030-01-01T00:00:00+00:00"}
                self.updated=False
            def services_for_sync(self,limit=500): return [self.row]
            def update_service_snapshot(self,*args,**kwargs):
                self.updated=True
                return True
        class Client:
            def get_user(self,username):
                return {"used_traffic":0,"data_limit":100,"expire":"not-a-date"}
            def subscription_url(self,remote,username): return None
        db=FakeDB()
        report=sync_services(db,None,client_factory=lambda row,box:Client())
        self.assertEqual(report["failed"],1)
        self.assertEqual(report["updated"],0)
        self.assertFalse(db.updated)
        self.assertEqual(db.row["expires_at"],"2030-01-01T00:00:00+00:00")

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

    def test_service_sync_treats_integer_and_string_false_as_disabled(self):
        self.assertEqual(_status({"enabled": 0}, None, 0, 0), "disabled")
        self.assertEqual(_status({"enabled": "false"}, None, 0, 0), "disabled")
        self.assertEqual(_status({"enabled": 1}, None, 0, 0), "active")

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

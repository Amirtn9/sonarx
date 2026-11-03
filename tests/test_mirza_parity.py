import os
import tempfile
import unittest

from sonarx.db import Database


class MirzaParityCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Database(os.path.join(self.tmp.name,"parity.sqlite3"))
        self.db.user(1101,"buyer","Buyer")
        self.db.user(1102,"inviter","Inviter")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_category_filter_and_delete_fallback(self):
        self.db.add_category("Premium")
        self.db.add_product("P","desc",100,10,30,"[]",1)
        self.assertEqual(len(self.db.products_for_category(1)),1)
        self.assertTrue(self.db.delete_category(1))
        self.assertEqual(len(self.db.products_for_category(0)),1)

    def test_trial_quota_is_enforced_and_trial_order_is_free(self):
        product=self.db.trial_product(1,1,"[]")
        oid=self.db.begin_trial_order(1101,product,1)
        self.db.add_server("panel","https://panel.test","admin","enc")
        server=self.db.servers()[0]
        self.db.finish_order(oid,server["id"],"trial_user","https://panel.test/sub")
        self.assertEqual(self.db.get_user(1101)["balance"],0)
        with self.assertRaises(ValueError):
            self.db.begin_trial_order(1101,product,1)

    def test_custom_product_is_hidden_and_owner_bound(self):
        custom=self.db.add_custom_product(1101,5,30,1500,"[]")
        self.assertEqual(custom["is_custom"],1)
        self.assertEqual(custom["active"],0)
        self.assertEqual(self.db.products(),[])
        self.assertEqual(self.db.products_admin(),[])
        self.assertEqual(custom["custom_owner_id"],1101)

    def test_order_cashback_and_reseller_commission_are_ledgered(self):
        self.db.record_referral(1102,1101,0)
        self.db.set_reseller(1102,20,True)
        self.db.add_product("Plan","desc",1000,10,30,"[]")
        self.db.add_credit(1101,1000,"seed")
        self.db.add_server("panel","https://panel.test","admin","enc")
        order=self.db.begin_order(1101,self.db.product(1))
        server=self.db.servers()[0]
        self.db.finish_order(order,server["id"],"user","https://panel.test/sub",cashback_percent=10,referral_percent=0)
        self.assertEqual(self.db.get_user(1101)["balance"],100)
        self.assertEqual(self.db.get_user(1102)["balance"],200)
        self.assertEqual(self.db.referral_stats(1102)["commission"],200)

    def test_deposit_cashback_and_phone_verification(self):
        deposit=self.db.create_deposit(1101,1000,"receipt")
        self.db.decide_deposit(deposit,True,999,cashback_percent=5)
        self.assertEqual(self.db.get_user(1101)["balance"],1050)
        self.assertTrue(self.db.save_phone(1101,"+989120000000"))
        self.assertTrue(self.db.get_user(1101)["phone_verified"])

    def test_reseller_request_approval_activates_dashboard_account(self):
        request_id=self.db.create_reseller_request(1102,"I can manage a small reseller network.")
        self.assertIsNotNone(request_id)
        self.assertIsNone(self.db.create_reseller_request(1102,"duplicate pending request"))
        self.assertEqual(len(self.db.pending_reseller_requests()),1)
        approved=self.db.decide_reseller_request(request_id,True,999,15)
        self.assertIsNotNone(approved)
        self.assertEqual(self.db.get_reseller(1102)["commission_percent"],15)
        self.assertEqual(self.db.pending_reseller_requests(),[])

    def test_service_snapshot_updates_usage_status_and_expiry(self):
        self.db.add_product("Plan","desc",100,10,30,"[]")
        self.db.add_server("panel","https://panel.test","admin","enc")
        self.db.add_credit(1101,100,"snapshot-seed")
        order=self.db.begin_order(1101,self.db.product(1))
        server=self.db.servers()[0]
        self.db.finish_order(order,server["id"],"snapshot_user","https://panel.test/sub")
        service=self.db.services_for(1101)[0]
        self.assertTrue(self.db.update_service_snapshot(service["id"],1101,2*1024**3,8*1024**3,"2031-01-01T00:00:00+00:00","limited"))
        refreshed=self.db.service_for_user(service["id"],1101)
        self.assertEqual(refreshed["used_bytes"],2*1024**3)
        self.assertEqual(refreshed["data_limit_bytes"],8*1024**3)
        self.assertEqual(refreshed["status"],"limited")

    def test_upgrade_adds_new_columns_to_existing_sqlite_schema(self):
        import sqlite3
        legacy_path=os.path.join(self.tmp.name,"legacy.sqlite3")
        conn=sqlite3.connect(legacy_path)
        try:
            conn.executescript("""
                CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT,first_name TEXT,balance INTEGER NOT NULL DEFAULT 0,is_blocked INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
                CREATE TABLE products(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,description TEXT DEFAULT '',price INTEGER NOT NULL,traffic_gb INTEGER NOT NULL,days INTEGER NOT NULL,group_ids TEXT DEFAULT '[]',active INTEGER NOT NULL DEFAULT 1,sort_order INTEGER NOT NULL DEFAULT 100);
            """)
        finally:
            conn.close()
        upgraded=Database(legacy_path)
        upgraded.close()
        conn=sqlite3.connect(legacy_path)
        try:
            user_columns={r[1] for r in conn.execute("PRAGMA table_info(users)")}
            product_columns={r[1] for r in conn.execute("PRAGMA table_info(products)")}
        finally:
            conn.close()
        self.assertIn("phone",user_columns)
        self.assertIn("phone_verified",user_columns)
        self.assertIn("category_id",product_columns)
        self.assertIn("is_trial",product_columns)
        self.assertIn("custom_owner_id",product_columns)

    def test_wheel_spin_cooldown_and_reward_once(self):
        spin=self.db.claim_wheel_spin(1101,24)
        self.assertIsNotNone(spin)
        self.assertIsNone(self.db.claim_wheel_spin(1101,24))
        with self.assertRaises(ValueError):
            self.db.finish_wheel_spin(spin,6,-1)
        self.assertTrue(self.db.finish_wheel_spin(spin,6,500))
        self.assertFalse(self.db.finish_wheel_spin(spin,6,500))
        self.assertEqual(self.db.get_user(1101)["balance"],500)


if __name__=="__main__":
    unittest.main()

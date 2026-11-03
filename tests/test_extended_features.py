import tempfile, unittest, os
from sonarx.db import Database

class ExtendedFeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Database(os.path.join(self.tmp.name,"test.sqlite3"))
        self.db.user(1001,"alice","Alice")
        self.db.user(1002,"bob","Bob")
    def tearDown(self):
        self.db.close();self.tmp.cleanup()
    def test_discount_reserve_release_and_limit(self):
        self.db.add_discount("SAVE20",20,1)
        self.assertEqual(self.db.reserve_discount("save20"),20)
        self.assertIsNone(self.db.reserve_discount("SAVE20"))
        self.db.release_discount("SAVE20")
        self.assertEqual(self.db.reserve_discount("SAVE20"),20)
    def test_gift_code_wallet_credit_once(self):
        self.db.add_gift_code("WELCOME",5000,usage_limit=1)
        gift=self.db.redeem_gift("welcome",1001)
        self.assertEqual(gift["amount"],5000)
        self.assertIsNone(self.db.redeem_gift("WELCOME",1001))
        self.assertEqual(self.db.get_user(1001)["balance"],5000)
    def test_categories_resellers_channels_and_lottery(self):
        self.db.add_category("Premium")
        self.assertEqual(self.db.categories()[0]["name"],"Premium")
        self.db.set_reseller(1001,15,True)
        self.assertEqual(self.db.resellers()[0]["commission_percent"],15)
        self.db.save_channel("announcement","@sonarx_news")
        self.assertEqual(self.db.channels()[0]["chat_id"],"@sonarx_news")
        self.db.join_lottery(1001)
        self.assertEqual(self.db.lottery_winner(),1001)
    def test_referral_bonus_once(self):
        self.assertTrue(self.db.record_referral(1001,1002,1200))
        self.assertFalse(self.db.record_referral(1001,1002,1200))
        self.assertEqual(self.db.get_user(1001)["balance"],1200)
        self.assertEqual(self.db.referral_stats(1001)["n"],1)
    def test_wallet_debit_and_service_entitlement_update(self):
        self.db.add_credit(1001,5000,"seed-addon")
        self.db.add_product("Starter","test",100,10,30,"[]")
        self.db.add_server("panel","https://panel.test","admin","encrypted","pasarguard")
        product=self.db.product(1)
        order=self.db.begin_order(1001,product)
        server=self.db.servers()[0]
        self.db.finish_order(order,server["id"],"sx1001","https://panel.test/sub")
        service=self.db.services_for(1001)[0]
        detailed=self.db.service_for_user(service["id"],1001)
        self.assertEqual(detailed["remote_username"],"sx1001")
        self.db.debit_wallet(1001,500,"addon-test")
        self.assertEqual(self.db.get_user(1001)["balance"],4400)
        self.assertTrue(self.db.update_service_entitlements(service["id"],1001,15*1024**3,"2030-01-01T00:00:00+00:00"))
        updated=self.db.service_for_user(service["id"],1001)
        self.assertEqual(updated["data_limit_bytes"],15*1024**3)
        self.assertEqual(updated["expires_at"],"2030-01-01T00:00:00+00:00")
        self.assertTrue(self.db.update_service_entitlements(service["id"],1001,status="disabled"))
        self.assertEqual(self.db.service_for_user(service["id"],1001)["status"],"disabled")

    def test_top_customer_rankings(self):
        self.db.add_credit(1001,1000,"deposit-seed","deposit")
        self.db.add_credit(1002,2000,"deposit-seed-b","deposit")
        self.assertEqual(self.db.top_depositors(1)[0]["id"],1002)
        self.assertIn("purchased",self.db.top_buyers(1)[0])
        self.assertIn("active_services",self.db.top_active_services(1)[0])
    def test_user_search(self):
        self.assertEqual(self.db.search_users("alice")[0]["id"],1001)
        self.assertEqual(self.db.search_users("1002")[0]["id"],1002)

    def test_order_discount_reservation_is_released_after_automatic_refund(self):
        self.db.add_product("Discounted","test",1000,10,30,"[]")
        self.db.add_credit(1001,2000,"discount-refund-seed")
        self.db.add_discount("ORDER20",20,1)
        self.assertEqual(self.db.reserve_discount("ORDER20"),20)
        product=self.db.product(1)
        product["price"]=800
        oid=self.db.begin_order(1001,product,discount_code="order20")
        self.assertEqual(self.db.discounts()[0]["used_count"],1)
        self.assertTrue(self.db.fail_order_refund(oid,"panel unavailable"))
        self.assertEqual(self.db.discounts()[0]["used_count"],0)
        self.assertFalse(self.db.fail_order_refund(oid,"duplicate refund"))
        self.assertEqual(self.db.discounts()[0]["used_count"],0)

    def test_order_discount_reservation_is_released_after_manual_review_refund(self):
        self.db.add_product("Discounted","test",1000,10,30,"[]")
        self.db.add_credit(1001,2000,"review-discount-seed")
        self.db.add_discount("REVIEW20",20,1)
        self.assertEqual(self.db.reserve_discount("REVIEW20"),20)
        product=self.db.product(1)
        product["price"]=800
        oid=self.db.begin_order(1001,product,discount_code="review20")
        self.db.mark_order_review(oid,"uncertain")
        self.assertIsNotNone(self.db.refund_review_order(oid))
        self.assertEqual(self.db.discounts()[0]["used_count"],0)
        self.assertIsNone(self.db.refund_review_order(oid))
        self.assertEqual(self.db.discounts()[0]["used_count"],0)

    def test_successful_order_keeps_discount_usage_consumed(self):
        self.db.add_product("Discounted","test",1000,10,30,"[]")
        self.db.add_credit(1001,2000,"success-discount-seed")
        self.db.add_discount("SUCCESS20",20,1)
        self.assertEqual(self.db.reserve_discount("SUCCESS20"),20)
        product=self.db.product(1)
        product["price"]=800
        oid=self.db.begin_order(1001,product,discount_code="success20")
        self.db.add_server("panel","https://panel.test","admin","enc")
        server=self.db.servers()[0]
        self.db.finish_order(oid,server["id"],"discount_user","https://panel.test/sub")
        self.assertEqual(self.db.discounts()[0]["used_count"],1)

if __name__=="__main__":unittest.main()

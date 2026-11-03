import os,tempfile,unittest
from sonarx.db import Database
class DatabaseTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.db=Database(os.path.join(self.tmp.name,'test.sqlite3'))
  self.db.user(101,'tester','Test');self.db.add_product('Basic','desc',1000,20,30,'[1]');self.product=self.db.product(1)
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def test_deposit_approval_once(self):
  did=self.db.create_deposit(101,500,'receipt-a');self.assertIsNotNone(self.db.decide_deposit(did,True,999));self.assertIsNone(self.db.decide_deposit(did,True,999));self.assertEqual(self.db.get_user(101)['balance'],500)
 def test_order_purchase_and_service(self):
  self.db.add_credit(101,2000,'seed');oid=self.db.begin_order(101,self.product);self.db.add_server('panel','https://example.test','admin','encrypted');server=self.db.servers()[0]
  self.db.finish_order(oid,server['id'],'vpn_user','https://example.test/sub');self.assertEqual(len(self.db.services_for(101)),1);self.assertEqual(self.db.get_user(101)['balance'],1000)
 def test_order_failure_refunds_once(self):
  self.db.add_credit(101,2000,'seed');oid=self.db.begin_order(101,self.product);self.assertTrue(self.db.fail_order_refund(oid,'failed'));self.assertFalse(self.db.fail_order_refund(oid,'again'));self.assertEqual(self.db.get_user(101)['balance'],2000)
 def test_review_order_not_auto_refunded(self):
  self.db.add_credit(101,2000,'seed');oid=self.db.begin_order(101,self.product);self.db.mark_order_review(oid,'uncertain');self.assertEqual(len(self.db.review_orders()),1);self.assertEqual(self.db.get_user(101)['balance'],1000)
  self.assertIsNotNone(self.db.refund_review_order(oid));self.assertIsNone(self.db.refund_review_order(oid));self.assertEqual(self.db.get_user(101)['balance'],2000)
 def test_settings_admin_and_audit(self):
  self.db.set_setting('feature.shop_enabled','0');self.assertEqual(self.db.get_setting('feature.shop_enabled'),'0');self.db.add_admin(777,'operator');self.assertTrue(self.db.is_admin(777));self.db.remove_admin(777);self.assertFalse(self.db.is_admin(777));self.db.audit(101,'test','target','details')
 def test_ticket_lifecycle(self):
  tid=self.db.add_ticket(101,'help','message');self.assertEqual(len(self.db.open_tickets()),1);self.db.reply_ticket(tid,999,'reply');self.assertEqual(len(self.db.ticket_messages(tid)),2);self.db.close_ticket(tid);self.assertEqual(self.db.open_tickets(),[])
 def test_admin_role_is_readable_and_validated(self):
  self.db.add_admin(778,'finance')
  self.assertEqual(self.db.get_admin_role(778),'finance')
  self.assertTrue(self.db.is_admin(778))
  with self.assertRaises(ValueError):self.db.add_admin(779,'superuser')
  self.db.remove_admin(778)
  self.assertIsNone(self.db.get_admin_role(778))

 def test_user_blocking(self):
  self.assertTrue(self.db.set_user_blocked(101,True));self.assertTrue(self.db.get_user(101)['is_blocked'])
 def test_admin_plan_controls_and_pending_deposits(self):
  self.db.update_product_price(1,2500);self.assertEqual(self.db.product(1)['price'],2500)
  self.db.add_server('p','https://panel.test','admin','enc','pasarguard');self.assertEqual(self.db.servers_admin()[0]['panel_type'],'pasarguard')
  did=self.db.create_deposit(101,200,'receipt-pending');self.assertEqual(self.db.pending_deposits()[0]['id'],did)
 def test_duplicate_ledger_reference_rejected(self):
  self.db.add_credit(101,100,'unique-ref')
  with self.assertRaises(Exception):self.db.add_credit(101,100,'unique-ref')
if __name__=='__main__':unittest.main()

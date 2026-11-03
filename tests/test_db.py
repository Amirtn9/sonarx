import os,tempfile,unittest
from datetime import datetime, timezone
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
 def test_order_entitlements_are_snapshotted_at_purchase_time(self):
  self.db.add_credit(101,2000,'seed-snapshot')
  oid=self.db.begin_order(101,self.product)
  with self.db.tx() as c:
   c.execute('UPDATE products SET traffic_gb=80,days=90 WHERE id=?',(self.product['id'],))
  self.db.add_server('snapshot-panel','https://panel.test','admin','enc')
  server=self.db.servers()[0]
  self.db.finish_order(oid,server['id'],'snapshot_user','https://panel.test/sub')
  service=self.db.services_for(101)[0]
  self.assertEqual(service['data_limit_bytes'],20*1024**3)
  remaining=datetime.fromisoformat(service['expires_at'])-datetime.now(timezone.utc)
  self.assertGreater(remaining.total_seconds(),29*86400)
  self.assertLess(remaining.total_seconds(),31*86400)
 def test_trial_entitlements_are_snapshotted_before_shared_trial_product_changes(self):
  product=self.db.trial_product(2,3,'[]')
  oid=self.db.begin_trial_order(101,product,2)
  self.db.trial_product(7,14,'[]')
  self.db.add_server('trial-panel','https://panel.test','admin','enc')
  server=self.db.servers()[0]
  self.db.finish_order(oid,server['id'],'trial_user','https://panel.test/sub')
  service=self.db.services_for(101)[0]
  self.assertEqual(service['data_limit_bytes'],2*1024**3)
  remaining=datetime.fromisoformat(service['expires_at'])-datetime.now(timezone.utc)
  self.assertGreater(remaining.total_seconds(),2*86400)
  self.assertLess(remaining.total_seconds(),4*86400)
 def test_review_reconciliation_uses_order_snapshot_not_mutated_product(self):
  self.db.add_credit(101,2000,'seed-review-snapshot')
  oid=self.db.begin_order(101,self.product)
  self.db.add_server('review-panel','https://panel.test','admin','enc')
  server=self.db.servers()[0]
  self.db.set_order_panel_target(oid,server['id'],'review_user')
  self.db.mark_order_review(oid,'uncertain',server['id'],'review_user')
  with self.db.tx() as c:
   c.execute('UPDATE products SET traffic_gb=60,days=120 WHERE id=?',(self.product['id'],))
  self.assertTrue(self.db.complete_review_order(oid,'https://panel.test/sub'))
  service=self.db.services_for(101)[0]
  self.assertEqual(service['data_limit_bytes'],20*1024**3)
  remaining=datetime.fromisoformat(service['expires_at'])-datetime.now(timezone.utc)
  self.assertGreater(remaining.total_seconds(),29*86400)
  self.assertLess(remaining.total_seconds(),31*86400)
 def test_stale_review_without_remote_target_can_be_safely_refunded(self):
  self.db.add_credit(101,2000,'seed-stale-review')
  oid=self.db.begin_order(101,self.product)
  self.db.mark_order_review(oid,'watchdog moved it before panel target was saved')
  self.assertTrue(self.db.fail_order_refund(oid,'panel target was never created'))
  self.assertEqual(self.db.get_user(101)['balance'],2000)
  self.assertEqual(self.db.review_orders(),[])
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

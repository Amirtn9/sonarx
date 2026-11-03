import unittest
from unittest.mock import patch
import requests
from sonarx.pasarguard import (PasarGuardClient, PasarGuardError, MarzbanClient, MarzneshinClient)

class FakePanel(PasarGuardClient):
    def __init__(self, steps):
        super().__init__("https://panel.test", "admin", "secret")
        self.steps=list(steps); self.calls=[]
    def ensure_login(self):
        self._token="fake"
    def _request(self, method, path, **kwargs):
        self.calls.append((method,path,kwargs))
        value=self.steps.pop(0)
        if isinstance(value,Exception): raise value
        return value



class FakeMarzban(MarzbanClient):
    def __init__(self, steps):
        super().__init__("https://panel.test", "admin", "secret")
        self.steps=list(steps); self.calls=[]
    def _request(self, method, path, **kwargs):
        self.calls.append((method,path,kwargs))
        value=self.steps.pop(0)
        if isinstance(value,Exception): raise value
        return value


class FakeMarzneshin(MarzneshinClient):
    def __init__(self, steps):
        super().__init__("https://panel.test", "admin", "secret")
        self.steps=list(steps); self.calls=[]
    def _request(self, method, path, **kwargs):
        self.calls.append((method,path,kwargs))
        value=self.steps.pop(0)
        if isinstance(value,Exception): raise value
        return value


class PasarGuardTests(unittest.TestCase):
    def test_create_user_payload_and_subscription(self):
        c=FakePanel([{"subscription_url":"https://panel.test/sub/abc"}])
        result,sub=c.create_user("sx_1",10,30,[1,2])
        self.assertEqual(sub,"https://panel.test/sub/abc")
        payload=c.calls[0][2]["json"]
        self.assertEqual(payload["data_limit"],10*1024**3)
        self.assertEqual(payload["group_ids"],[1,2])

    def test_marzban_create_refreshes_expired_token_once(self):
        c=FakeMarzban([
            PasarGuardError("Marzban API 401: expired token"),
            {"access_token":"fresh"},
            {"subscription_url":"https://panel.test/sub/marzban"},
        ])
        c._token="expired"
        _,sub=c.create_user("sx_m1",5,7,[])
        self.assertEqual(sub,"https://panel.test/sub/marzban")
        self.assertEqual([call[0:2] for call in c.calls],[
            ("POST","/api/user"),("POST","/api/admin/token"),("POST","/api/user")
        ])

    def test_marzneshin_create_refreshes_expired_token_once(self):
        c=FakeMarzneshin([
            PasarGuardError("Marzneshin API 401: expired token"),
            {"access_token":"fresh"},
            {"subscription_url":"https://panel.test/sub/marzneshin"},
        ])
        c._token="expired"
        _,sub=c.create_user("sx_n1",5,7,[])
        self.assertEqual(sub,"https://panel.test/sub/marzneshin")
        self.assertEqual([call[0:2] for call in c.calls],[
            ("POST","/api/users"),("POST","/api/admins/token"),("POST","/api/users")
        ])

    def test_timeout_reconciles_existing_user(self):
        c=FakePanel([requests.Timeout("write timeout"),{"subscription_url":"https://panel.test/sub/existing"}])
        _,sub=c.create_user("sx_2",5,7,[])
        self.assertEqual(sub,"https://panel.test/sub/existing")
        self.assertEqual(c.calls[1][0:2],("GET","/api/user/sx_2"))

    def test_uncertain_timeout_is_not_silently_retried(self):
        c=FakePanel([requests.Timeout("timeout"),requests.ConnectionError("offline")])
        with self.assertRaises(PasarGuardError) as ctx:
            c.create_user("sx_3",1,1,[])
        self.assertTrue(ctx.exception.uncertain)
        self.assertEqual(len(c.calls),2)

    def test_extend_user_updates_volume_with_absolute_target(self):
        c=FakePanel([
            {"username":"sx_1","data_limit":10*1024**3,"expire":0},
            {"username":"sx_1","data_limit":15*1024**3,"expire":0},
        ])
        _,limit,expiry=c.extend_user("sx_1",add_gb=5)
        self.assertEqual(limit,15*1024**3)
        self.assertIsNone(expiry)
        method,path,kwargs=c.calls[1]
        self.assertEqual(method,"PUT")
        self.assertEqual(kwargs["json"]["data_limit"],15*1024**3)

    def test_extend_timeout_reconciles_without_second_put(self):
        c=FakePanel([
            {"username":"sx_1","data_limit":10*1024**3,"expire":0},
            requests.Timeout("write timeout"),
            {"username":"sx_1","data_limit":12*1024**3,"expire":0},
        ])
        _,limit,_=c.extend_user("sx_1",add_gb=2)
        self.assertEqual(limit,12*1024**3)
        self.assertEqual(sum(1 for method,_,_ in c.calls if method=="PUT"),1)

    def test_extend_user_updates_expiry(self):
        current="2030-01-01T00:00:00+00:00"
        c=FakePanel([
            {"username":"sx_1","data_limit":0,"expire":current},
            {"username":"sx_1","data_limit":0,"expire":"2030-01-08T00:00:00+00:00"},
        ])
        _,limit,expiry=c.extend_user("sx_1",add_days=7)
        self.assertIsNone(limit)
        self.assertEqual(expiry,"2030-01-08T00:00:00+00:00")
        self.assertIn("expire",c.calls[1][2]["json"])

    def test_extend_unlimited_volume_is_rejected(self):
        c=FakePanel([{"username":"sx_1","data_limit":0,"expire":0}])
        with self.assertRaises(ValueError):
            c.extend_user("sx_1",add_gb=1)

if __name__=="__main__":
    unittest.main()

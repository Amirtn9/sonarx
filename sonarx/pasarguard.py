import requests
from datetime import datetime, timezone
from urllib.parse import quote

class PasarGuardError(RuntimeError):
    def __init__(self,message,uncertain=False): super().__init__(message); self.uncertain=uncertain

class PasarGuardClient:
    """PasarGuard adapter using the Marzban-compatible admin API surface."""
    def __init__(self,url,username,password,verify_tls=True,timeout=18):
        self.url=url.rstrip('/'); self.username=username; self.password=password
        self.verify_tls=verify_tls; self.timeout=timeout; self.session=requests.Session()
        self.session.headers.update({'Accept':'application/json'}); self._token=None
    def _request(self,method,path,**kwargs):
        try: r=self.session.request(method,self.url+path,timeout=self.timeout,verify=self.verify_tls,**kwargs)
        except (requests.Timeout,requests.ConnectionError) as exc: raise
        if r.status_code>=400:
            try: detail=r.json()
            except ValueError: detail=r.text[:500]
            raise PasarGuardError(f'PasarGuard API {r.status_code}: {detail}',uncertain=(method.upper()=='POST' and r.status_code>=500))
        if not r.content:return {}
        try:return r.json()
        except ValueError:raise PasarGuardError('پاسخ پنل JSON معتبر نیست.',uncertain=method.upper()=='POST')
    def login(self):
        data=self._request('POST','/api/admin/token',data={'username':self.username,'password':self.password},headers={'Content-Type':'application/x-www-form-urlencoded'})
        token=data.get('access_token')
        if not token:raise PasarGuardError('توکن ورود از پنل دریافت نشد.')
        self._token=token; self.session.headers['Authorization']=f'Bearer {token}'; return True
    def ensure_login(self):
        if not self._token:self.login()
    @staticmethod
    def _sub(data):
        sub=data.get('subscription_url') or data.get('subscription_link') or data.get('sub_url') or data.get('subscription') or data.get('links')
        if isinstance(sub,list):sub='\n'.join(map(str,sub))
        return sub
    def create_user(self,username,traffic_gb,days,group_ids=None,note='SonarX order'):
        self.ensure_login()
        expires=0 if not days else datetime.now(timezone.utc).timestamp()+days*86400
        payload={'username':username,'proxy_settings':{},'data_limit':int(float(traffic_gb)*1024**3) if traffic_gb else 0,
                 'expire':0 if not expires else datetime.fromtimestamp(expires,timezone.utc).isoformat(),
                 'data_limit_reset_strategy':'no_reset','note':note}
        if group_ids:payload['group_ids']=group_ids
        try:
            try: result=self._request('POST','/api/user',json=payload)
            except PasarGuardError as exc:
                if '401' in str(exc):
                    self._token=None; self.login(); result=self._request('POST','/api/user',json=payload)
                else:raise
        except (requests.Timeout,requests.ConnectionError) as exc:
            # The panel may have committed the POST. Reconcile by username; never POST to another panel blindly.
            try: result=self._request('GET',f'/api/user/{quote(username,safe="")}')
            except Exception as check_exc: raise PasarGuardError('نتیجه ساخت اکانت پس از قطع ارتباط نامشخص است؛ نیازمند بررسی پنل.',True) from check_exc
        sub=self._sub(result)
        if not sub:
            try: sub=self._sub(self._request('GET',f'/api/user/{quote(username,safe="")}'))
            except Exception: pass
        if not sub:raise PasarGuardError('پاسخ پنل لینک اشتراک ندارد؛ وضعیت اکانت باید دستی بررسی شود.',True)
        return result,sub
    def get_user(self,username):
        self.ensure_login(); return self._request('GET',f'/api/user/{quote(username,safe="")}')

class MarzbanClient(PasarGuardClient):
    """Secondary adapter for Marzban-compatible installations."""
    def create_user(self,username,traffic_gb,days,group_ids=None,note='SonarX order'):
        self.ensure_login()
        expiry=0 if not days else int(datetime.now(timezone.utc).timestamp()+days*86400)
        payload={'username':username,'proxies':{'vless':{},'vmess':{},'trojan':{},'shadowsocks':{}},'inbounds':{},
                 'expire':expiry,'data_limit':int(float(traffic_gb)*1024**3) if traffic_gb else 0,
                 'data_limit_reset_strategy':'no_reset'}
        try:
            result=self._request('POST','/api/user',json=payload)
        except (requests.Timeout,requests.ConnectionError) as exc:
            try:result=self._request('GET',f'/api/user/{quote(username,safe="")}')
            except Exception as check:raise PasarGuardError('نتیجه ساخت اکانت Marzban نامشخص است؛ بررسی دستی لازم است.',True) from check
        sub=self._sub(result)
        if not sub:
            try:sub=self._sub(self._request('GET',f'/api/user/{quote(username,safe="")}'))
            except Exception:pass
        if not sub:raise PasarGuardError('لینک اشتراک از Marzban دریافت نشد؛ وضعیت اکانت را بررسی کنید.',True)
        return result,sub

class MarzneshinClient(PasarGuardClient):
    """Secondary adapter for Marzneshin; group_ids are interpreted as service_ids."""
    def login(self):
        data=self._request('POST','/api/admins/token',data={'username':self.username,'password':self.password},headers={'Content-Type':'application/x-www-form-urlencoded'})
        token=data.get('access_token')
        if not token:raise PasarGuardError('توکن ورود Marzneshin دریافت نشد.')
        self._token=token;self.session.headers['Authorization']=f'Bearer {token}';return True
    def create_user(self,username,traffic_gb,days,group_ids=None,note='SonarX order'):
        self.ensure_login()
        expire=datetime.now(timezone.utc).timestamp()+days*86400 if days else 0
        payload={'username':username,'data_limit':int(float(traffic_gb)*1024**3) if traffic_gb else 0,
                 'expire_date':datetime.fromtimestamp(expire,timezone.utc).isoformat() if expire else None,
                 'service_ids':group_ids or [1],'expire_strategy':'fixed_date'}
        try:result=self._request('POST','/api/users',json=payload)
        except (requests.Timeout,requests.ConnectionError) as exc:
            try:result=self._request('GET',f'/api/users/{quote(username,safe="")}')
            except Exception as check:raise PasarGuardError('نتیجه ساخت اکانت Marzneshin نامشخص است؛ بررسی دستی لازم است.',True) from check
        sub=self._sub(result)
        if not sub and result.get('key'):sub=f"{self.url}/sub/{username}/{result['key']}"
        if not sub:
            try:
                detail=self._request('GET',f'/api/users/{quote(username,safe="")}');sub=self._sub(detail)
                if not sub and detail.get('key'):sub=f"{self.url}/sub/{username}/{detail['key']}"
            except Exception:pass
        if not sub:raise PasarGuardError('لینک اشتراک از Marzneshin دریافت نشد؛ وضعیت اکانت را بررسی کنید.',True)
        return result,sub

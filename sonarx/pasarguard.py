import requests
from datetime import datetime, timezone, timedelta
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
            raise PasarGuardError(f'PasarGuard API {r.status_code}: {detail}',uncertain=(method.upper() in {'POST','PUT'} and r.status_code>=500))
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
    def _post_with_auth_retry(self,path,payload):
        """Retry a rejected POST once after refreshing a stale panel token."""
        try:
            return self._request('POST',path,json=payload)
        except PasarGuardError as exc:
            if '401' not in str(exc) or exc.uncertain:
                raise
            self._token=None
            self.login()
            return self._request('POST',path,json=payload)
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
            result=self._post_with_auth_retry('/api/user',payload)
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


    @staticmethod
    def _as_datetime(value):
        if value in (None, 0, "0", ""):
            return None
        if isinstance(value,(int,float)):
            return datetime.fromtimestamp(value,timezone.utc)
        if isinstance(value,str):
            try:
                parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
                return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
            except ValueError:
                try:return datetime.fromtimestamp(float(value),timezone.utc)
                except Exception:return None
        return None

    def extend_user(self,username,add_gb=0,add_days=0):
        """Add quota/time through PasarGuard's documented PUT /api/user/{username} endpoint.
        The operation is reconciled with GET after transport/5xx uncertainty; it never repeats
        the mutation blindly.
        """
        if self.__class__.__name__ == "MarzneshinClient":
            raise PasarGuardError("تمدید/افزایش حجم برای Marzneshin در این نسخه پشتیبانی نمی‌شود.")
        if add_gb < 0 or add_days < 0 or (add_gb == 0 and add_days == 0):
            raise ValueError("مقدار افزایش باید مثبت باشد.")
        path=f'/api/user/{quote(username,safe="")}'
        try:
            self.ensure_login()
            try:
                before=self._request("GET",path)
            except PasarGuardError as exc:
                if "401" not in str(exc): raise
                self._token=None;self.login();before=self._request("GET",path)
        except (requests.Timeout,requests.ConnectionError) as exc:
            raise PasarGuardError("اتصال پیش از اعمال تغییر برقرار نشد؛ مبلغ قابل بازگشت است.",False) from exc
        payload={}
        target_limit=int(before.get("data_limit") or 0)
        target_expire=self._as_datetime(before.get("expire"))
        if add_gb:
            if target_limit<=0: raise ValueError("حجم این سرویس نامحدود است؛ افزایش حجم برای آن معنی ندارد.")
            target_limit += int(add_gb)*1024**3
            payload["data_limit"]=target_limit
        if add_days:
            if target_expire is None: raise ValueError("اعتبار این سرویس نامحدود است؛ تمدید زمانی لازم نیست.")
            base=max(target_expire,datetime.now(timezone.utc))
            target_expire=base+timedelta(days=int(add_days))
            payload["expire"]=target_expire.isoformat()
        try:
            try:
                result=self._request("PUT",path,json=payload)
            except PasarGuardError as auth_exc:
                if "401" not in str(auth_exc) or auth_exc.uncertain: raise
                self._token=None;self.login()
                result=self._request("PUT",path,json=payload)
        except (requests.Timeout,requests.ConnectionError,PasarGuardError) as exc:
            if isinstance(exc,PasarGuardError) and not exc.uncertain:
                raise
            # A PUT may have committed. Read back once and compare the absolute target values.
            try:
                after=self._request("GET",path)
            except Exception as check_exc:
                raise PasarGuardError("نتیجه تغییر سرویس پس از قطع ارتباط نامشخص است؛ مبلغ باید تا بررسی مدیر محفوظ بماند.",True) from check_exc
            actual_limit=int(after.get("data_limit") or 0)
            actual_expire=self._as_datetime(after.get("expire"))
            limit_ok=(not add_gb or actual_limit==target_limit)
            expire_ok=(not add_days or (actual_expire is not None and abs((actual_expire-target_expire).total_seconds())<5))
            if not (limit_ok and expire_ok):
                raise PasarGuardError("تغییر سرویس در پنل تأیید نشد و نتیجه نیازمند بررسی دستی است.",True) from exc
            result=after
        return result, (target_limit if add_gb else None), (target_expire.isoformat() if add_days else None)


class MarzbanClient(PasarGuardClient):
    """Secondary adapter for Marzban-compatible installations."""
    def create_user(self,username,traffic_gb,days,group_ids=None,note='SonarX order'):
        self.ensure_login()
        expiry=0 if not days else int(datetime.now(timezone.utc).timestamp()+days*86400)
        payload={'username':username,'proxies':{'vless':{},'vmess':{},'trojan':{},'shadowsocks':{}},'inbounds':{},
                 'expire':expiry,'data_limit':int(float(traffic_gb)*1024**3) if traffic_gb else 0,
                 'data_limit_reset_strategy':'no_reset'}
        try:
            result=self._post_with_auth_retry('/api/user',payload)
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
    def get_user(self,username):
        self.ensure_login()
        return self._request('GET',f'/api/users/{quote(username,safe="")}')

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
        try:result=self._post_with_auth_retry('/api/users',payload)
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

from __future__ import annotations
import os, sqlite3, threading, uuid, re
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta


def now(): return datetime.now(timezone.utc).isoformat()

class Row(dict):
    def __getitem__(self, key):
        if isinstance(key, int): return tuple(self.values())[key]
        return super().__getitem__(key)

class PgCursor:
    def __init__(self, cur, lastrowid=None): self.cur=cur; self.lastrowid=lastrowid; self.rowcount=cur.rowcount
    def fetchone(self):
        r=self.cur.fetchone(); return Row(r) if r is not None else None
    def fetchall(self): return [Row(r) for r in self.cur.fetchall()]

class PgConn:
    ID_TABLES={'servers','products','orders','deposits','ledger','tickets','ticket_messages','audit_logs','payments','service_events','categories','discount_codes','gift_codes','resellers','lottery_entries'}
    def __init__(self, conn): self.conn=conn
    def execute(self, sql, params=()):
        q=str(sql)
        if re.match(r'\s*INSERT\s+OR\s+IGNORE\s+INTO',q,re.I):
            q=re.sub(r'INSERT\s+OR\s+IGNORE\s+INTO','INSERT INTO',q,count=1,flags=re.I)
            if not re.search(r'ON\s+CONFLICT',q,re.I): q=q.rstrip().rstrip(';')+' ON CONFLICT DO NOTHING'
        q=q.replace('MAX(0,used_count-1)','GREATEST(0,used_count-1)')
        q=q.replace('?', '%s')
        m=re.match(r'\s*INSERT\s+INTO\s+([a-zA-Z_][a-zA-Z0-9_]*)',q,re.I)
        wants_id=bool(m and m.group(1).lower() in self.ID_TABLES and not re.search(r'\bRETURNING\b',q,re.I))
        if wants_id: q=q.rstrip().rstrip(';')+' RETURNING id'
        cur=self.conn.execute(q,params or ())
        last=None
        if wants_id:
            r=cur.fetchone(); last=(r.get('id') if isinstance(r,dict) else r[0]) if r else None
        return PgCursor(cur,last)

class Database:
    def __init__(self, path=None):
        self.url=path or os.getenv('DATABASE_URL','')
        self.backend='postgresql' if self.url.startswith(('postgresql://','postgres://','postgresql+psycopg://')) else 'sqlite'
        self.lock=threading.RLock()
        if self.backend=='postgresql':
            from psycopg_pool import ConnectionPool
            from psycopg.rows import dict_row
            url=self.url.replace('postgresql+psycopg://','postgresql://',1)
            self.pool=ConnectionPool(conninfo=url,min_size=1,max_size=int(os.getenv('DB_POOL_SIZE','10')),kwargs={'row_factory':dict_row},open=True)
            self.path=None
        else:
            self.path=self.url or os.getenv('DATABASE_PATH','data/sonarx.sqlite3')
            os.makedirs(os.path.dirname(self.path) or '.',exist_ok=True)
            self.pool=None
        self.init()

    @contextmanager
    def tx(self):
        if self.backend=='postgresql':
            with self.pool.connection() as conn:
                with conn.transaction(): yield PgConn(conn)
        else:
            with self.lock:
                con=sqlite3.connect(self.path,timeout=30,isolation_level=None)
                con.row_factory=sqlite3.Row
                try:
                    con.execute('PRAGMA foreign_keys=ON'); con.execute('BEGIN IMMEDIATE')
                    yield con; con.commit()
                except Exception:
                    con.rollback(); raise
                finally: con.close()

    def init(self):
        if self.backend=='postgresql': return # Alembic owns production schema.
        with self.lock, sqlite3.connect(self.path) as c:
            c.executescript('''
            PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT,first_name TEXT,balance INTEGER NOT NULL DEFAULT 0 CHECK(balance>=0),is_blocked INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS servers(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT UNIQUE,url TEXT NOT NULL,username TEXT NOT NULL,password_enc TEXT NOT NULL,panel_type TEXT NOT NULL DEFAULT 'pasarguard',active INTEGER NOT NULL DEFAULT 1,priority INTEGER NOT NULL DEFAULT 100,created_at TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,description TEXT DEFAULT '',price INTEGER NOT NULL CHECK(price>=0),traffic_gb INTEGER NOT NULL CHECK(traffic_gb>=0),days INTEGER NOT NULL CHECK(days>=0),group_ids TEXT DEFAULT '[]',active INTEGER NOT NULL DEFAULT 1,sort_order INTEGER NOT NULL DEFAULT 100);
            CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,product_id INTEGER NOT NULL,amount INTEGER NOT NULL,status TEXT NOT NULL,server_id INTEGER,remote_username TEXT,subscription_url TEXT,error TEXT,created_at TEXT NOT NULL,FOREIGN KEY(user_id) REFERENCES users(id));
            CREATE TABLE IF NOT EXISTS services(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,order_id TEXT UNIQUE NOT NULL,product_id INTEGER NOT NULL,server_id INTEGER NOT NULL,remote_username TEXT NOT NULL,subscription_url TEXT,data_limit_bytes INTEGER NOT NULL DEFAULT 0,used_bytes INTEGER NOT NULL DEFAULT 0,expires_at TEXT,status TEXT NOT NULL DEFAULT 'active',created_at TEXT NOT NULL,FOREIGN KEY(user_id) REFERENCES users(id));
            CREATE TABLE IF NOT EXISTS deposits(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,amount INTEGER NOT NULL CHECK(amount>0),receipt_id TEXT NOT NULL UNIQUE,status TEXT NOT NULL DEFAULT 'pending',created_at TEXT NOT NULL,reviewed_at TEXT,reviewed_by INTEGER);
            CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,amount INTEGER NOT NULL,kind TEXT NOT NULL,ref TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,subject TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ticket_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,ticket_id INTEGER NOT NULL,sender_id INTEGER NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS admin_roles(user_id INTEGER PRIMARY KEY,role TEXT NOT NULL DEFAULT 'operator',active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,actor_id INTEGER,action TEXT NOT NULL,target TEXT,details TEXT,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,gateway TEXT NOT NULL,provider_ref TEXT UNIQUE,amount INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending',payload TEXT,created_at TEXT NOT NULL,paid_at TEXT);
            CREATE TABLE IF NOT EXISTS service_events(id INTEGER PRIMARY KEY AUTOINCREMENT,service_id INTEGER,user_id INTEGER NOT NULL,event_type TEXT NOT NULL,payload TEXT,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL UNIQUE,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS discount_codes(code TEXT PRIMARY KEY,percent INTEGER NOT NULL,usage_limit INTEGER NOT NULL DEFAULT 0,used_count INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1,expires_at TEXT,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS gift_codes(code TEXT PRIMARY KEY,amount INTEGER NOT NULL DEFAULT 0,days INTEGER NOT NULL DEFAULT 0,traffic_gb INTEGER NOT NULL DEFAULT 0,usage_limit INTEGER NOT NULL DEFAULT 1,used_count INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS gift_redemptions(id INTEGER PRIMARY KEY AUTOINCREMENT,code TEXT NOT NULL,user_id INTEGER NOT NULL,created_at TEXT NOT NULL,UNIQUE(code,user_id));
            CREATE TABLE IF NOT EXISTS resellers(user_id INTEGER PRIMARY KEY,commission_percent INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS referrals(inviter_id INTEGER NOT NULL,invitee_id INTEGER NOT NULL UNIQUE,bonus_amount INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS channel_settings(kind TEXT PRIMARY KEY,chat_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS lottery_entries(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL UNIQUE,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS bot_buttons(btn_key TEXT PRIMARY KEY,label TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,sort_order INTEGER NOT NULL DEFAULT 100);
            CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id,created_at);
            CREATE INDEX IF NOT EXISTS idx_services_user ON services(user_id,status);
            CREATE INDEX IF NOT EXISTS idx_deposits_status ON deposits(status);
            CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
            CREATE INDEX IF NOT EXISTS idx_ledger_user ON ledger(user_id,created_at);
            ''')
            # Upgrade databases created by older SonarX releases without deleting data.
            # Additive compatibility migration for earlier SonarX SQLite files.
            upgrades={
                'users': {'is_blocked':'INTEGER NOT NULL DEFAULT 0'},
                'servers': {'panel_type':"TEXT NOT NULL DEFAULT 'pasarguard'",'priority':'INTEGER NOT NULL DEFAULT 100','created_at':"TEXT NOT NULL DEFAULT ''"},
                'products': {'active':'INTEGER NOT NULL DEFAULT 1','sort_order':'INTEGER NOT NULL DEFAULT 100'},
                'deposits': {'reviewed_at':'TEXT','reviewed_by':'INTEGER'},
                'settings': {'updated_at':"TEXT NOT NULL DEFAULT ''"},
            }
            for table, additions in upgrades.items():
                present={r[1] for r in c.execute(f'PRAGMA table_info({table})')}
                for column, definition in additions.items():
                    if column not in present:c.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
            c.commit()

    def close(self):
        if self.pool: self.pool.close()

    def user(self,uid,username=None,first_name=None):
        with self.tx() as c:
            c.execute('INSERT INTO users(id,username,first_name,created_at) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET username=excluded.username,first_name=excluded.first_name',(uid,username,first_name,now()))
            r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); return dict(r)
    def get_user(self,uid):
        with self.tx() as c:
            r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); return dict(r) if r else None
    def products(self):
        with self.tx() as c: return [dict(r) for r in c.execute('SELECT * FROM products WHERE active=1 ORDER BY sort_order,id').fetchall()]
    def products_admin(self):
        with self.tx() as c: return [dict(r) for r in c.execute('SELECT * FROM products ORDER BY sort_order,id').fetchall()]
    def product(self,pid,active_only=True):
        with self.tx() as c:
            q='SELECT * FROM products WHERE id=?'+(' AND active=1' if active_only else '')
            r=c.execute(q,(pid,)).fetchone(); return dict(r) if r else None
    def add_product(self,name,desc,price,gb,days,groups):
        with self.tx() as c: c.execute('INSERT INTO products(name,description,price,traffic_gb,days,group_ids) VALUES(?,?,?,?,?,?)',(name,desc,price,gb,days,groups))
    def set_product_active(self,pid,active):
        with self.tx() as c: return c.execute('UPDATE products SET active=? WHERE id=?',(int(active),pid)).rowcount==1
    def update_product_price(self,pid,price):
        if price<0: raise ValueError('قیمت نامعتبر است.')
        with self.tx() as c: return c.execute('UPDATE products SET price=? WHERE id=?',(price,pid)).rowcount==1
    def servers(self):
        with self.tx() as c: return [dict(r) for r in c.execute('SELECT * FROM servers WHERE active=1 ORDER BY priority,id').fetchall()]
    def servers_admin(self):
        with self.tx() as c: return [dict(r) for r in c.execute('SELECT * FROM servers ORDER BY priority,id').fetchall()]
    def add_server(self,name,url,user,pw,panel_type='pasarguard'):
        with self.tx() as c: c.execute('INSERT INTO servers(name,url,username,password_enc,panel_type,created_at) VALUES(?,?,?,?,?,?)',(name,url.rstrip('/'),user,pw,panel_type,now()))
    def set_server_active(self,sid,active):
        with self.tx() as c: return c.execute('UPDATE servers SET active=? WHERE id=?',(int(active),sid)).rowcount==1
    def create_deposit(self,uid,amount,file_id):
        with self.tx() as c:
            cur=c.execute("INSERT INTO deposits(user_id,amount,receipt_id,created_at) VALUES(?,?,?,?)",(uid,amount,file_id,now())); return cur.lastrowid
    def deposit(self,did):
        with self.tx() as c:
            r=c.execute('SELECT * FROM deposits WHERE id=?',(did,)).fetchone(); return dict(r) if r else None
    def pending_deposits(self):
        with self.tx() as c: return [dict(r) for r in c.execute("SELECT * FROM deposits WHERE status='pending' ORDER BY created_at").fetchall()]
    def decide_deposit(self,did,approve,admin_id=None):
        with self.tx() as c:
            d=c.execute('SELECT * FROM deposits WHERE id=?',(did,)).fetchone()
            if not d or d['status']!='pending': return None
            status='approved' if approve else 'rejected'
            changed=c.execute("UPDATE deposits SET status=?,reviewed_at=?,reviewed_by=? WHERE id=? AND status='pending'",(status,now(),admin_id,did)).rowcount
            if changed!=1: return None
            if approve:
                c.execute('UPDATE users SET balance=balance+? WHERE id=?',(d['amount'],d['user_id']))
                c.execute('INSERT INTO ledger(user_id,amount,kind,ref,created_at) VALUES(?,?,?,?,?)',(d['user_id'],d['amount'],'deposit',f'deposit:{did}',now()))
            return dict(d)
    def begin_order(self,uid,p):
        oid=str(uuid.uuid4())
        with self.tx() as c:
            lock=' FOR UPDATE' if self.backend=='postgresql' else ''
            u=c.execute('SELECT balance,is_blocked FROM users WHERE id=?'+lock,(uid,)).fetchone()
            if not u: raise ValueError('ابتدا /start را بزنید.')
            if u['is_blocked']: raise ValueError('حساب کاربری شما مسدود است.')
            active=c.execute("SELECT id FROM orders WHERE user_id=? AND status IN ('processing','review') LIMIT 1"+lock,(uid,)).fetchone()
            if active: raise ValueError('یک سفارش در حال پردازش یا بررسی دارید.')
            if u['balance']<p['price']: raise ValueError('موجودی کیف پول کافی نیست.')
            c.execute('UPDATE users SET balance=balance-? WHERE id=?',(p['price'],uid))
            c.execute('INSERT INTO ledger(user_id,amount,kind,ref,created_at) VALUES(?,?,?,?,?)',(uid,-p['price'],'purchase',f'order:{oid}',now()))
            c.execute('INSERT INTO orders(id,user_id,product_id,amount,status,created_at) VALUES(?,?,?,?,?,?)',(oid,uid,p['id'],p['price'],'processing',now()))
        return oid
    def finish_order(self,oid,server_id,username,sub):
        with self.tx() as c:
            q='SELECT o.*,p.traffic_gb,p.days FROM orders o JOIN products p ON p.id=o.product_id WHERE o.id=? AND o.status=\'processing\''+(' FOR UPDATE' if self.backend=='postgresql' else '')
            o=c.execute(q,(oid,)).fetchone()
            if not o: raise ValueError('سفارش در وضعیت پردازش نیست.')
            exp=(datetime.now(timezone.utc)+timedelta(days=o['days'])).isoformat() if o['days'] else None
            changed=c.execute("UPDATE orders SET status='completed',server_id=?,remote_username=?,subscription_url=? WHERE id=? AND status='processing'",(server_id,username,sub,oid)).rowcount
            if changed!=1: raise ValueError('سفارش هم‌زمان تغییر کرده است.')
            c.execute('INSERT INTO services(user_id,order_id,product_id,server_id,remote_username,subscription_url,data_limit_bytes,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(o['user_id'],oid,o['product_id'],server_id,username,sub,int(o['traffic_gb'])*1024**3,exp,now()))
    def fail_order_refund(self,oid,error):
        with self.tx() as c:
            q='SELECT * FROM orders WHERE id=?'+(' FOR UPDATE' if self.backend=='postgresql' else '')
            o=c.execute(q,(oid,)).fetchone()
            if not o or o['status']!='processing': return False
            c.execute("UPDATE orders SET status='failed',error=? WHERE id=? AND status='processing'",(str(error)[:1000],oid))
            c.execute('UPDATE users SET balance=balance+? WHERE id=?',(o['amount'],o['user_id']))
            c.execute('INSERT INTO ledger(user_id,amount,kind,ref,created_at) VALUES(?,?,?,?,?)',(o['user_id'],o['amount'],'refund',f'refund:{oid}',now())); return True
    def mark_order_review(self,oid,error):
        with self.tx() as c: return c.execute("UPDATE orders SET status='review',error=? WHERE id=? AND status='processing'",(str(error)[:1000],oid)).rowcount==1
    def review_orders(self):
        with self.tx() as c: return [dict(r) for r in c.execute("SELECT * FROM orders WHERE status='review' ORDER BY created_at").fetchall()]
    def refund_review_order(self,oid):
        with self.tx() as c:
            q="SELECT * FROM orders WHERE id=? AND status='review'"+(' FOR UPDATE' if self.backend=='postgresql' else '')
            o=c.execute(q,(oid,)).fetchone()
            if not o:return None
            c.execute("UPDATE orders SET status='refunded' WHERE id=? AND status='review'",(oid,))
            c.execute('UPDATE users SET balance=balance+? WHERE id=?',(o['amount'],o['user_id']))
            c.execute('INSERT INTO ledger(user_id,amount,kind,ref,created_at) VALUES(?,?,?,?,?)',(o['user_id'],o['amount'],'manual_refund',f'review-refund:{oid}',now()))
            return dict(o)
    def orders_for(self,uid):
        with self.tx() as c: return [dict(r) for r in c.execute('SELECT o.*,p.name product_name FROM orders o JOIN products p ON p.id=o.product_id WHERE o.user_id=? ORDER BY o.created_at DESC LIMIT 30',(uid,)).fetchall()]
    def services_for(self,uid):
        with self.tx() as c: return [dict(r) for r in c.execute('SELECT s.*,p.name product_name,v.name server_name FROM services s JOIN products p ON p.id=s.product_id JOIN servers v ON v.id=s.server_id WHERE s.user_id=? ORDER BY s.created_at DESC',(uid,)).fetchall()]
    def add_credit(self,uid,amount,ref,kind='admin_credit'):
        if amount<=0: raise ValueError('amount must be positive')
        with self.tx() as c:
            if c.execute('UPDATE users SET balance=balance+? WHERE id=?',(amount,uid)).rowcount!=1: raise ValueError('کاربر پیدا نشد.')
            c.execute('INSERT INTO ledger(user_id,amount,kind,ref,created_at) VALUES(?,?,?,?,?)',(uid,amount,kind,ref,now()))
    def stats(self):
        with self.tx() as c:
            return {'users':c.execute('SELECT COUNT(*) FROM users').fetchone()[0], 'orders':c.execute("SELECT COUNT(*) FROM orders WHERE status='completed'").fetchone()[0], 'revenue':c.execute("SELECT COALESCE(SUM(amount),0) FROM orders WHERE status='completed'").fetchone()[0], 'pending_deposits':c.execute("SELECT COUNT(*) FROM deposits WHERE status='pending'").fetchone()[0], 'open_tickets':c.execute("SELECT COUNT(*) FROM tickets WHERE status='open'").fetchone()[0]}
    def get_setting(self,key,default=None):
        with self.tx() as c:
            r=c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone(); return r['value'] if r else default
    def set_setting(self,key,value):
        with self.tx() as c: c.execute('INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',(key,str(value),now()))
    def audit(self,actor,action,target=None,details=None):
        with self.tx() as c: c.execute('INSERT INTO audit_logs(actor_id,action,target,details,created_at) VALUES(?,?,?,?,?)',(actor,action,target,str(details)[:2000] if details is not None else None,now()))
    def list_settings(self):
        with self.tx() as c: return [dict(r) for r in c.execute('SELECT * FROM settings ORDER BY key').fetchall()]
    def list_users(self,limit=100):
        with self.tx() as c: return [dict(r) for r in c.execute('SELECT id,username,first_name,balance,is_blocked,created_at FROM users ORDER BY created_at DESC LIMIT ?',(min(max(int(limit),1),500),)).fetchall()]
    def list_orders(self,limit=100):
        with self.tx() as c: return [dict(r) for r in c.execute('SELECT * FROM orders ORDER BY created_at DESC LIMIT ?',(min(max(int(limit),1),500),)).fetchall()]
    def set_user_blocked(self,uid,blocked):
        with self.tx() as c: return c.execute('UPDATE users SET is_blocked=? WHERE id=?',(int(blocked),uid)).rowcount==1
    def is_admin(self,uid):
        with self.tx() as c:
            r=c.execute('SELECT active FROM admin_roles WHERE user_id=?',(uid,)).fetchone(); return bool(r and r['active'])
    def add_admin(self,uid,role='operator'):
        with self.tx() as c: c.execute('INSERT INTO admin_roles(user_id,role,active,created_at) VALUES(?,?,1,?) ON CONFLICT(user_id) DO UPDATE SET role=excluded.role,active=1',(uid,role,now()))
    def remove_admin(self,uid):
        with self.tx() as c: c.execute('UPDATE admin_roles SET active=0 WHERE user_id=?',(uid,))
    def list_admins(self):
        with self.tx() as c: return [dict(r) for r in c.execute('SELECT * FROM admin_roles ORDER BY created_at').fetchall()]
    def add_ticket(self,uid,subject,body):
        with self.tx() as c:
            cur=c.execute('INSERT INTO tickets(user_id,subject,status,created_at) VALUES(?,?,?,?)',(uid,subject,'open',now())); tid=cur.lastrowid
            c.execute('INSERT INTO ticket_messages(ticket_id,sender_id,body,created_at) VALUES(?,?,?,?)',(tid,uid,body,now())); return tid
    def ticket_messages(self,tid):
        with self.tx() as c: return [dict(r) for r in c.execute('SELECT * FROM ticket_messages WHERE ticket_id=? ORDER BY id',(tid,)).fetchall()]
    def open_tickets(self):
        with self.tx() as c: return [dict(r) for r in c.execute("SELECT * FROM tickets WHERE status='open' ORDER BY created_at").fetchall()]
    def reply_ticket(self,tid,admin_id,body):
        with self.tx() as c:
            t=c.execute('SELECT * FROM tickets WHERE id=?',(tid,)).fetchone()
            if not t: raise ValueError('تیکت پیدا نشد.')
            c.execute('INSERT INTO ticket_messages(ticket_id,sender_id,body,created_at) VALUES(?,?,?,?)',(tid,admin_id,body,now())); return dict(t)
    def close_ticket(self,tid):
        with self.tx() as c: c.execute("UPDATE tickets SET status='closed' WHERE id=?",(tid,))
    def users_for_broadcast(self):
        with self.tx() as c: return [r[0] for r in c.execute('SELECT id FROM users WHERE is_blocked=0').fetchall()]


    # SonarX 6.0 extended store/admin data
    def add_category(self,name):
        with self.tx() as c: c.execute("INSERT INTO categories(name,created_at) VALUES(?,?)",(name,now()))
    def categories(self):
        with self.tx() as c: return [dict(r) for r in c.execute("SELECT * FROM categories ORDER BY id").fetchall()]
    def delete_category(self,cid):
        with self.tx() as c: return c.execute("DELETE FROM categories WHERE id=?",(cid,)).rowcount==1
    def add_discount(self,code,percent,usage_limit=0):
        with self.tx() as c: c.execute("INSERT INTO discount_codes(code,percent,usage_limit,created_at) VALUES(?,?,?,?)",(code.upper(),percent,usage_limit,now()))
    def discounts(self):
        with self.tx() as c: return [dict(r) for r in c.execute("SELECT * FROM discount_codes ORDER BY created_at DESC").fetchall()]
    def toggle_discount(self,code,active):
        with self.tx() as c: return c.execute("UPDATE discount_codes SET active=? WHERE code=?",(int(active),code.upper())).rowcount==1
    def add_gift_code(self,code,amount=0,days=0,traffic_gb=0,usage_limit=1):
        with self.tx() as c: c.execute("INSERT INTO gift_codes(code,amount,days,traffic_gb,usage_limit,created_at) VALUES(?,?,?,?,?,?)",(code.upper(),amount,days,traffic_gb,usage_limit,now()))
    def gift_codes(self):
        with self.tx() as c: return [dict(r) for r in c.execute("SELECT * FROM gift_codes ORDER BY created_at DESC").fetchall()]
    def redeem_gift(self,code,uid):
        with self.tx() as c:
            q="SELECT * FROM gift_codes WHERE code=? AND active=1"+(" FOR UPDATE" if self.backend=="postgresql" else "")
            g=c.execute(q,(code.upper(),)).fetchone()
            if not g or (g["usage_limit"] and g["used_count"]>=g["usage_limit"]): return None
            inserted=c.execute("INSERT INTO gift_redemptions(code,user_id,created_at) VALUES(?,?,?) ON CONFLICT(code,user_id) DO NOTHING",(code.upper(),uid,now()))
            if inserted.rowcount != 1: return None
            c.execute("UPDATE gift_codes SET used_count=used_count+1 WHERE code=?",(code.upper(),))
            if g["amount"]>0:
                c.execute("UPDATE users SET balance=balance+? WHERE id=?",(g["amount"],uid))
                c.execute("INSERT INTO ledger(user_id,amount,kind,ref,created_at) VALUES(?,?,?,?,?)",(uid,g["amount"],"gift",f"gift:{code.upper()}:{uid}",now()))
            return dict(g)
    def set_reseller(self,uid,commission=0,active=True):
        with self.tx() as c:
            c.execute("INSERT INTO resellers(user_id,commission_percent,active,created_at) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET commission_percent=excluded.commission_percent,active=excluded.active",(uid,commission,int(active),now()))
    def resellers(self):
        with self.tx() as c: return [dict(r) for r in c.execute("SELECT * FROM resellers ORDER BY created_at DESC").fetchall()]
    def save_channel(self,kind,chat_id):
        with self.tx() as c: c.execute("INSERT INTO channel_settings(kind,chat_id) VALUES(?,?) ON CONFLICT(kind) DO UPDATE SET chat_id=excluded.chat_id",(kind,chat_id))
    def channels(self):
        with self.tx() as c: return [dict(r) for r in c.execute("SELECT * FROM channel_settings ORDER BY kind").fetchall()]
    def join_lottery(self,uid):
        with self.tx() as c:
            c.execute("INSERT INTO lottery_entries(user_id,created_at) VALUES(?,?)",(uid,now()))
    def lottery_count(self):
        with self.tx() as c: return c.execute("SELECT COUNT(*) FROM lottery_entries").fetchone()[0]
    def search_users(self,query,limit=25):
        q=str(query).strip()
        with self.tx() as c:
            if q.isdigit(): rows=c.execute("SELECT * FROM users WHERE id=? LIMIT ?",(int(q),limit)).fetchall()
            else: rows=c.execute("SELECT * FROM users WHERE username LIKE ? OR first_name LIKE ? ORDER BY created_at DESC LIMIT ?",(f"%{q}%",f"%{q}%",limit)).fetchall()
            return [dict(r) for r in rows]
    def top_users(self,limit=10):
        with self.tx() as c: return [dict(r) for r in c.execute("SELECT id,username,balance,(SELECT COUNT(*) FROM orders o WHERE o.user_id=users.id AND o.status='completed') orders FROM users ORDER BY balance DESC LIMIT ?",(limit,)).fetchall()]
    def recent_audit(self,limit=50):
        with self.tx() as c: return [dict(r) for r in c.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?",(limit,)).fetchall()]

    def get_discount(self,code):
        with self.tx() as c:
            r=c.execute("SELECT * FROM discount_codes WHERE code=? AND active=1",(code.upper(),)).fetchone()
            if not r or (r["usage_limit"] and r["used_count"]>=r["usage_limit"]): return None
            return dict(r)
    def reserve_discount(self,code):
        with self.tx() as c:
            q="SELECT * FROM discount_codes WHERE code=? AND active=1"+(" FOR UPDATE" if self.backend=="postgresql" else "")
            r=c.execute(q,(code.upper(),)).fetchone()
            if not r or (r["usage_limit"] and r["used_count"]>=r["usage_limit"]): return None
            changed=c.execute("UPDATE discount_codes SET used_count=used_count+1 WHERE code=? AND active=1 AND (usage_limit=0 OR used_count<usage_limit)",(code.upper(),)).rowcount
            return int(r["percent"]) if changed==1 else None
    def release_discount(self,code):
        with self.tx() as c: c.execute("UPDATE discount_codes SET used_count=GREATEST(0,used_count-1) WHERE code=?",(code.upper(),)) if self.backend=="postgresql" else c.execute("UPDATE discount_codes SET used_count=MAX(0,used_count-1) WHERE code=?",(code.upper(),))

    def tickets_for(self,uid):
        with self.tx() as c: return [dict(r) for r in c.execute("SELECT * FROM tickets WHERE user_id=? ORDER BY id DESC LIMIT 30",(uid,)).fetchall()]
    def lottery_winner(self):
        with self.tx() as c:
            rows=c.execute("SELECT user_id FROM lottery_entries ORDER BY id").fetchall()
            if not rows:return None
            import secrets
            return int(secrets.choice(rows)[0])

    def record_referral(self,inviter,invitee,bonus=0):
        if inviter==invitee:return False
        with self.tx() as c:
            cur=c.execute("INSERT INTO referrals(inviter_id,invitee_id,bonus_amount,created_at) VALUES(?,?,?,?) ON CONFLICT(invitee_id) DO NOTHING",(inviter,invitee,bonus,now()))
            if cur.rowcount!=1:return False
            if bonus>0:
                c.execute("UPDATE users SET balance=balance+? WHERE id=?",(bonus,inviter))
                c.execute("INSERT INTO ledger(user_id,amount,kind,ref,created_at) VALUES(?,?,?,?,?)",(inviter,bonus,"referral",f"referral:{invitee}",now()))
            return True
    def referral_stats(self,uid):
        with self.tx() as c:
            r=c.execute("SELECT COUNT(*) n,COALESCE(SUM(bonus_amount),0) bonus FROM referrals WHERE inviter_id=?",(uid,)).fetchone()
            return dict(r)

    def service_for_user(self,service_id,uid):
        with self.tx() as c:
            r=c.execute("SELECT s.*,v.url,v.username panel_username,v.password_enc,v.panel_type,v.active server_active,v.name server_name FROM services s JOIN servers v ON v.id=s.server_id WHERE s.id=? AND s.user_id=?",(service_id,uid)).fetchone()
            return dict(r) if r else None
    def update_service_entitlements(self,service_id,uid,data_limit_bytes=None,expires_at=None):
        with self.tx() as c:
            fields=[];params=[]
            if data_limit_bytes is not None: fields.append("data_limit_bytes=?");params.append(int(data_limit_bytes))
            if expires_at is not None: fields.append("expires_at=?");params.append(expires_at)
            if not fields:return False
            params.extend([service_id,uid])
            return c.execute("UPDATE services SET "+",".join(fields)+" WHERE id=? AND user_id=?",(tuple(params))).rowcount==1
    def debit_wallet(self,uid,amount,ref,kind="service_addon"):
        if amount<=0:raise ValueError("amount must be positive")
        with self.tx() as c:
            lock=" FOR UPDATE" if self.backend=="postgresql" else ""
            u=c.execute("SELECT balance,is_blocked FROM users WHERE id=?"+lock,(uid,)).fetchone()
            if not u:raise ValueError("کاربر پیدا نشد.")
            if u["is_blocked"]:raise ValueError("حساب کاربری مسدود است.")
            if u["balance"]<amount:raise ValueError("موجودی کیف پول کافی نیست.")
            c.execute("UPDATE users SET balance=balance-? WHERE id=?",(amount,uid))
            c.execute("INSERT INTO ledger(user_id,amount,kind,ref,created_at) VALUES(?,?,?,?,?)",(uid,-amount,kind,ref,now()))
            return int(u["balance"])-amount

    def top_buyers(self,limit=10):
        with self.tx() as c:
            return [dict(r) for r in c.execute("""SELECT u.id,u.username,COUNT(o.id) orders,COALESCE(SUM(o.amount),0) purchased
                FROM users u LEFT JOIN orders o ON o.user_id=u.id AND o.status='completed'
                GROUP BY u.id,u.username ORDER BY purchased DESC,orders DESC LIMIT ?""",(limit,)).fetchall()]
    def top_depositors(self,limit=10):
        with self.tx() as c:
            return [dict(r) for r in c.execute("""SELECT u.id,u.username,COALESCE(SUM(l.amount),0) deposited
                FROM users u LEFT JOIN ledger l ON l.user_id=u.id AND l.kind='deposit'
                GROUP BY u.id,u.username ORDER BY deposited DESC LIMIT ?""",(limit,)).fetchall()]
    def top_active_services(self,limit=10):
        with self.tx() as c:
            return [dict(r) for r in c.execute("""SELECT u.id,u.username,COUNT(s.id) active_services
                FROM users u LEFT JOIN services s ON s.user_id=u.id AND s.status='active'
                GROUP BY u.id,u.username ORDER BY active_services DESC LIMIT ?""",(limit,)).fetchall()]

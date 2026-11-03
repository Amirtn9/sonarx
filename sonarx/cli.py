import argparse, os, subprocess, sys, getpass, re, urllib.request, json
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv, dotenv_values
load_dotenv()

ROOT=Path('/opt/sonarx')
ENV_FILE=ROOT/'.env'

def run(args,check=True,env=None):
    print('+',' '.join(map(str,args)))
    return subprocess.run(args,check=check,env=env)

def database():
    from .db import Database
    return Database(os.getenv('DATABASE_URL') or os.getenv('DATABASE_PATH','data/sonarx.sqlite3'))

def rewrite_env(key,value):
    if not ENV_FILE.exists(): raise SystemExit(f'{ENV_FILE} not found')
    lines=ENV_FILE.read_text().splitlines()
    lines=[line for line in lines if not line.startswith(key+'=')]
    lines.append(f'{key}={value}')
    ENV_FILE.write_text('\n'.join(lines)+'\n')
    os.chmod(ENV_FILE,0o640)

def validate_token(token):
    req=urllib.request.Request(f'https://api.telegram.org/bot{token}/getMe')
    try:
        with urllib.request.urlopen(req,timeout=15) as response: data=json.loads(response.read())
    except Exception as exc: raise SystemExit(f'Telegram token validation failed: {exc}')
    if not data.get('ok'): raise SystemExit('Telegram rejected this bot token.')
    return data['result']

def main():
    p=argparse.ArgumentParser(prog='sonarx',description='SonarX administration CLI')
    s=p.add_subparsers(dest='cmd',required=True)
    for cmd in ['status','users','orders','admins','settings','db-upgrade','db-current','start','stop','restart','update','service-install','service-remove','doctor','sync-services','recover-orders']:
        s.add_parser(cmd)
    lg=s.add_parser('logs');lg.add_argument('-n','--lines',type=int,default=150);lg.add_argument('-f','--follow',action='store_true')
    b=s.add_parser('backup');b.add_argument('--output')
    r=s.add_parser('restore');r.add_argument('file');r.add_argument('--yes',action='store_true')
    a=s.add_parser('admin-add');a.add_argument('user_id',type=int);a.add_argument('--role',choices=['owner','admin','operator','finance','support','marketing','viewer'],default='operator')
    a=s.add_parser('admin-remove');a.add_argument('user_id',type=int)
    c=s.add_parser('user-credit');c.add_argument('user_id',type=int);c.add_argument('amount',type=int);c.add_argument('--ref',default=None)
    u=s.add_parser('user-block');u.add_argument('user_id',type=int);u.add_argument('--unblock',action='store_true')
    g=s.add_parser('setting-get');g.add_argument('key')
    t=s.add_parser('setting-set');t.add_argument('key');t.add_argument('value')
    tok=s.add_parser('token-change');tok.add_argument('--no-restart',action='store_true')
    ids=s.add_parser('admins-set');ids.add_argument('ids',help='comma-separated Telegram numeric IDs')
    sub=s.add_parser('config');sub.add_argument('action',choices=['show','get','set']);sub.add_argument('key',nargs='?');sub.add_argument('value',nargs='?')
    x=p.parse_args()
    if x.cmd=='db-upgrade':run([sys.executable,'-m','alembic','upgrade','head'])
    elif x.cmd=='db-current':run([sys.executable,'-m','alembic','current'])
    elif x.cmd in {'backup','restore'}:
        from sqlalchemy.engine import make_url
        url=os.getenv('DATABASE_URL','')
        if not url.startswith(('postgresql://','postgresql+psycopg://','postgres://')):raise SystemExit('Backup/restore requires PostgreSQL DATABASE_URL.')
        u=make_url(url.replace('postgresql+psycopg://','postgresql://',1));env=os.environ.copy();env['PGPASSWORD']=u.password or ''
        common=['-h',u.host or '127.0.0.1','-p',str(u.port or 5432),'-U',u.username or '']
        if x.cmd=='backup':
            out=x.output or f"sonarx-{datetime.now().strftime('%Y%m%d-%H%M%S')}.dump";run(['pg_dump',*common,'-d',u.database or '','-Fc','-f',out],env=env);print('Backup:',out)
        else:
            if not x.yes:raise SystemExit('Restore is destructive. Add --yes after taking a backup.')
            run(['pg_restore','--clean','--if-exists',*common,'-d',u.database or '',x.file],env=env)
    elif x.cmd=='logs':run(['journalctl','-u','sonarx','-n',str(x.lines),'-f' if x.follow else '--no-pager'])
    elif x.cmd in {'start','stop','restart','service-install','service-remove'}:
        if x.cmd=='service-install':run(['systemctl','daemon-reload']);run(['systemctl','enable','--now','sonarx']);run(['systemctl','enable','--now','sonarx-maintenance.timer'])
        elif x.cmd=='service-remove':
            run(['systemctl','disable','--now','sonarx'],check=False)
            run(['systemctl','disable','--now','sonarx-maintenance.timer'],check=False)
            for unit in ('sonarx.service','sonarx-maintenance.service','sonarx-maintenance.timer'):
                Path('/etc/systemd/system/'+unit).unlink(missing_ok=True)
            Path('/usr/local/bin/sonarx').unlink(missing_ok=True)
            run(['systemctl','daemon-reload']);print('Services removed; application data and database preserved.')
        else:run(['systemctl',x.cmd,'sonarx'])
    elif x.cmd=='update':
        if not (ROOT/'.git').exists():raise SystemExit('No Git checkout at /opt/sonarx')
        old= subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
        run(['systemctl','stop','sonarx'],check=False)
        try:
            backup_dir=Path('/var/backups/sonarx');backup_dir.mkdir(parents=True,exist_ok=True);os.chmod(backup_dir,0o700)
            backup_file=backup_dir/f"pre-update-{datetime.now().strftime('%Y%m%d-%H%M%S')}.dump"
            if os.getenv('DATABASE_URL','').startswith(('postgresql://','postgresql+psycopg://','postgres://')):
                run([sys.executable,'-m','sonarx.cli','backup','--output',str(backup_file)])
            else:
                sqlite_path=Path(os.getenv('DATABASE_PATH','data/sonarx.sqlite3'))
                if sqlite_path.exists():
                    import shutil
                    shutil.copy2(sqlite_path,backup_file.with_suffix('.sqlite3'))
            run(['git','-C',str(ROOT),'pull','--ff-only'])
            run([str(ROOT/'.venv/bin/pip'),'install','-r',str(ROOT/'requirements.txt')])
            run([str(ROOT/'.venv/bin/alembic'),'upgrade','head'])
        except Exception:
            print('Update failed. Restoring previous source revision and dependencies…',file=sys.stderr)
            run(['git','-C',str(ROOT),'reset','--hard',old],check=False)
            run([str(ROOT/'.venv/bin/pip'),'install','-r',str(ROOT/'requirements.txt')],check=False)
            raise
        finally:
            run(['systemctl','restart','sonarx'],check=False)
    elif x.cmd=='recover-orders':
        d=database()
        from .workers.order_recovery import recover_stale_orders
        report=recover_stale_orders(d,int(os.getenv('ORDER_RECOVERY_MINUTES','15')))
        for oid in report['order_ids']:
            print('Moved to review:',oid)
        print('Recovery:',report)
        d.close()
    elif x.cmd=='sync-services':
        d=database()
        from .config import settings
        from .security import SecretBox
        from .workers.service_sync import sync_services
        box=SecretBox(settings.fernet_key) if settings.fernet_key else None
        report=sync_services(d,box,limit=int(os.getenv('SERVICE_SYNC_BATCH','500')),verify_tls=settings.verify_tls)
        print(json.dumps(report,ensure_ascii=False,indent=2))
        d.close()
    elif x.cmd=='status':
        d=database();print('Database:',d.backend);print(d.stats());d.close();run(['systemctl','is-active','sonarx'],check=False)
    elif x.cmd=='doctor':
        print('Python:',sys.version.split()[0]);print('Environment file:',ENV_FILE.exists())
        print('PostgreSQL URL configured:',bool(os.getenv('DATABASE_URL')))
        d=database();print('Database backend:',d.backend);print('Database stats:',d.stats());d.close()
        run(['systemctl','is-active','postgresql'],check=False);run(['systemctl','is-active','sonarx'],check=False)
    elif x.cmd=='users':
        d=database()
        for z in d.list_users():print(z['id'],z['username'] or '-',z['balance'],'BLOCKED' if z['is_blocked'] else 'active')
        d.close()
    elif x.cmd=='orders':
        d=database()
        for z in d.list_orders():print(z['id'],z['user_id'],z['amount'],z['status'])
        d.close()
    elif x.cmd=='admins':
        d=database()
        for z in d.list_admins():print(z['user_id'],z['role'],'active' if z['active'] else 'inactive')
        d.close()
        print('Primary ADMIN_IDS:',os.getenv('ADMIN_IDS','(unset)'))
    elif x.cmd=='settings':
        d=database()
        for z in d.list_settings():print(f"{z['key']}={z['value']}")
        d.close()
    elif x.cmd=='admin-add':
        d=database();d.add_admin(x.user_id,x.role);d.audit(None,'admin.add',str(x.user_id),x.role);d.close();print('Admin added')
    elif x.cmd=='admin-remove':
        d=database();d.remove_admin(x.user_id);d.audit(None,'admin.remove',str(x.user_id));d.close();print('Admin removed')
    elif x.cmd=='admins-set':
        vals=[v.strip() for v in x.ids.split(',') if v.strip()]
        if not vals or any(not re.fullmatch(r'[0-9]{4,20}',v) for v in vals):raise SystemExit('Provide comma-separated numeric Telegram IDs.')
        rewrite_env('ADMIN_IDS',','.join(vals));print('ADMIN_IDS updated.')
        run(['systemctl','restart','sonarx'])
    elif x.cmd=='token-change':
        token=getpass.getpass('New Telegram bot token (hidden): ').strip()
        if not re.fullmatch(r'[0-9]{5,15}:[A-Za-z0-9_-]{20,}',token):raise SystemExit('Token format is invalid.')
        info=validate_token(token);rewrite_env('BOT_TOKEN',token)
        print(f"Validated bot @{info.get('username')} and saved token securely.")
        if not x.no_restart:run(['systemctl','restart','sonarx'])
    elif x.cmd=='config':
        vals=dotenv_values(ENV_FILE) if ENV_FILE.exists() else {}
        if x.action=='get':
            if not x.key:raise SystemExit('config get KEY')
            val=vals.get(x.key)
            if any(k in x.key for k in ('TOKEN','PASSWORD','KEY','SECRET','DATABASE_URL','DSN','URI')):val='[REDACTED]' if val else '(unset)'
            print(val if val is not None else '(unset)')
        elif x.action=='set':
            allowed={'TIMEZONE','CURRENCY_LABEL','SUPPORT_USERNAME','PASARGUARD_VERIFY_TLS','DB_POOL_SIZE'}
            if x.key not in allowed or x.value is None: raise SystemExit('Allowed: '+', '.join(sorted(allowed))+'; syntax: sonarx config set KEY VALUE')
            rewrite_env(x.key,x.value)
            print(x.key+' updated.')
            run(['systemctl','restart','sonarx'])
        else:
            for k,v in vals.items():
                if any(secret in k for secret in ('TOKEN','PASSWORD','KEY','SECRET','DATABASE_URL','DSN','URI')) and v:v='[REDACTED]'
                print(f'{k}={v or ""}')
    elif x.cmd=='user-credit':
        d=database();d.add_credit(x.user_id,x.amount,x.ref or f'cli:{x.user_id}:{datetime.now().timestamp()}');d.close();print('Balance updated')
    elif x.cmd=='user-block':
        d=database()
        if not d.set_user_blocked(x.user_id,not x.unblock):raise SystemExit('User not found')
        d.close();print('User updated')
    elif x.cmd=='setting-get':
        d=database();print(d.get_setting(x.key,'<not set>'));d.close()
    elif x.cmd=='setting-set':
        d=database();d.set_setting(x.key,x.value);d.audit(None,'setting.set',x.key,x.value);d.close();print('Setting updated')
if __name__=='__main__':main()

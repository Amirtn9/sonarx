import argparse, os, subprocess, sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

def run(args,check=True,env=None):
    print('+',' '.join(map(str,args)));return subprocess.run(args,check=check,env=env)
def database():
    from .db import Database
    return Database(os.getenv('DATABASE_URL') or os.getenv('DATABASE_PATH','data/sonarx.sqlite3'))
def main():
    p=argparse.ArgumentParser(prog='sonarx',description='SonarX operations CLI')
    s=p.add_subparsers(dest='cmd',required=True)
    for cmd in ['status','users','orders','admins','settings','db-upgrade','db-current','start','stop','restart','logs','update','service-install','service-remove']:s.add_parser(cmd)
    b=s.add_parser('backup');b.add_argument('--output')
    r=s.add_parser('restore');r.add_argument('file');r.add_argument('--yes',action='store_true')
    a=s.add_parser('admin-add');a.add_argument('user_id',type=int);a.add_argument('--role',default='operator')
    a=s.add_parser('admin-remove');a.add_argument('user_id',type=int)
    c=s.add_parser('user-credit');c.add_argument('user_id',type=int);c.add_argument('amount',type=int);c.add_argument('--ref',default=None)
    u=s.add_parser('user-block');u.add_argument('user_id',type=int);u.add_argument('--unblock',action='store_true')
    g=s.add_parser('setting-get');g.add_argument('key')
    t=s.add_parser('setting-set');t.add_argument('key');t.add_argument('value')
    x=p.parse_args()
    if x.cmd=='db-upgrade':run([sys.executable,'-m','alembic','upgrade','head'])
    elif x.cmd=='db-current':run([sys.executable,'-m','alembic','current'])
    elif x.cmd in {'backup','restore'}:
        from sqlalchemy.engine import make_url
        url=os.getenv('DATABASE_URL','')
        if not url.startswith(('postgresql://','postgresql+psycopg://','postgres://')):raise SystemExit('Backup/restore currently requires PostgreSQL DATABASE_URL.')
        u=make_url(url.replace('postgresql+psycopg://','postgresql://',1));env=os.environ.copy();env['PGPASSWORD']=u.password or ''
        common=['-h',u.host or '127.0.0.1','-p',str(u.port or 5432),'-U',u.username or '']
        if x.cmd=='backup':
            out=x.output or f"sonarx-{datetime.now().strftime('%Y%m%d-%H%M%S')}.dump";run(['pg_dump',*common,'-d',u.database or '','-Fc','-f',out],env=env);print('Backup:',out)
        else:
            if not x.yes:raise SystemExit('Restore is destructive. Add --yes after taking a backup.')
            run(['pg_restore','--clean','--if-exists',*common,'-d',u.database or '',x.file],env=env)
    elif x.cmd in {'start','stop','restart','logs','service-install','service-remove'}:
        if x.cmd=='logs':run(['journalctl','-u','sonarx','-n','100','-f'])
        elif x.cmd=='service-install':run(['systemctl','daemon-reload']);run(['systemctl','enable','--now','sonarx'])
        elif x.cmd=='service-remove':
            run(['systemctl','disable','--now','sonarx'],check=False);Path('/etc/systemd/system/sonarx.service').unlink(missing_ok=True);Path('/usr/local/bin/sonarx').unlink(missing_ok=True);run(['systemctl','daemon-reload']);print('Service unit and command wrapper removed; database preserved.')
        else:run(['systemctl',x.cmd,'sonarx'])
    elif x.cmd=='update':
        root=Path('/opt/sonarx')
        if not (root/'.git').exists():raise SystemExit('No Git checkout at /opt/sonarx')
        run(['systemctl','stop','sonarx'],check=False);run(['git','-C',str(root),'pull','--ff-only'])
        run([str(root/'.venv/bin/pip'),'install','-r',str(root/'requirements.txt')]);run([str(root/'.venv/bin/alembic'),'upgrade','head']);run(['systemctl','restart','sonarx'])
    elif x.cmd=='status':
        d=database();print('Database:',d.backend);print(d.stats());d.close()
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
    elif x.cmd=='settings':
        d=database()
        for z in d.list_settings():print(f"{z['key']}={z['value']}")
        d.close()
    elif x.cmd=='admin-add':
        d=database();d.add_admin(x.user_id,x.role);d.audit(None,'admin.add',str(x.user_id),x.role);d.close();print('Admin added')
    elif x.cmd=='admin-remove':
        d=database();d.remove_admin(x.user_id);d.audit(None,'admin.remove',str(x.user_id));d.close();print('Admin removed')
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

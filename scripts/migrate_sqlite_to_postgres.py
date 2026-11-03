#!/usr/bin/env python3
"""One-time migration of existing SonarX SQLite records to PostgreSQL."""
import argparse, os, sqlite3
from dotenv import load_dotenv
import psycopg
from psycopg.rows import dict_row
TABLES=['users','servers','products','orders','deposits','ledger','settings','services','tickets','ticket_messages','admin_roles','audit_logs','payments','service_events']
SEQUENCES=['servers','products','deposits','ledger','services','tickets','ticket_messages','audit_logs','payments','service_events']
def main():
    load_dotenv();ap=argparse.ArgumentParser();ap.add_argument('--sqlite',default=os.getenv('DATABASE_PATH','data/sonarx.sqlite3'));ap.add_argument('--merge',action='store_true');a=ap.parse_args()
    url=os.getenv('DATABASE_URL','')
    if not url.startswith(('postgresql://','postgresql+psycopg://')):raise SystemExit('DATABASE_URL must point to PostgreSQL')
    if not os.path.isfile(a.sqlite):print('No SQLite source found; skipping.');return
    src=sqlite3.connect(a.sqlite);src.row_factory=sqlite3.Row;existing={r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    url=url.replace('postgresql+psycopg://','postgresql://',1);count=0
    with psycopg.connect(url,row_factory=dict_row) as pg:
        with pg.transaction():
            for table in TABLES:
                if table not in existing:continue
                with pg.cursor() as cur:
                    cur.execute(f'SELECT * FROM {table} LIMIT 0');cols=[d.name for d in cur.description]
                    cur.execute(f'SELECT COUNT(*) AS n FROM {table}');n=cur.fetchone()['n']
                if n and not a.merge:raise RuntimeError(f'Target {table} is not empty; use --merge only after reviewing conflicts.')
                for row in src.execute(f'SELECT * FROM {table}'):
                    names=[k for k in row.keys() if k in cols]
                    if not names:continue
                    col_sql=','.join('"'+k+'"' for k in names);marks=','.join(['%s']*len(names))
                    pg.execute(f'INSERT INTO {table} ({col_sql}) VALUES ({marks}) ON CONFLICT DO NOTHING',[row[k] for k in names]);count+=1
            # Backfill service records for completed purchases from older SonarX versions.
            pg.execute("""INSERT INTO services(user_id,order_id,product_id,server_id,remote_username,subscription_url,data_limit_bytes,expires_at,status,created_at)
                SELECT o.user_id,o.id,o.product_id,o.server_id,o.remote_username,o.subscription_url,
                       (p.traffic_gb::bigint * 1073741824),
                       CASE WHEN p.days>0 THEN (o.created_at::timestamptz + (p.days || ' days')::interval)::text ELSE NULL END,
                       'active',o.created_at
                FROM orders o JOIN products p ON p.id=o.product_id
                WHERE o.status='completed' AND o.server_id IS NOT NULL
                ON CONFLICT(order_id) DO NOTHING""")
            for table in SEQUENCES:
                pg.execute(f"SELECT setval(pg_get_serial_sequence('{table}','id'),COALESCE((SELECT MAX(id) FROM {table}),1),EXISTS(SELECT 1 FROM {table}))")
    src.close();print(f'Migration finished. Rows examined: {count}. SQLite source was preserved.')
if __name__=='__main__':main()

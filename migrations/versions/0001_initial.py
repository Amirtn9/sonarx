from alembic import op
from pathlib import Path
revision='0001_initial'; down_revision=None; branch_labels=None; depends_on=None
def upgrade():
    sql=(Path(__file__).resolve().parents[2]/'sonarx'/'schema_postgres.sql').read_text()
    for statement in sql.split(';'):
        if statement.strip(): op.execute(statement)
def downgrade():
    for table in ['service_events','payments','audit_logs','admin_roles','ticket_messages','tickets','settings','ledger','deposits','services','orders','products','servers','users']:
        op.execute(f'DROP TABLE IF EXISTS {table} CASCADE')

"""SonarX 6.0 extended admin and store features."""
from alembic import op
revision = "0002_extended_features"
down_revision = "0001_initial"
branch_labels = None
depends_on = None
SQL = [
"""CREATE TABLE IF NOT EXISTS categories(id BIGSERIAL PRIMARY KEY,name TEXT NOT NULL UNIQUE,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL DEFAULT '')""",
"""CREATE TABLE IF NOT EXISTS discount_codes(code TEXT PRIMARY KEY,percent INTEGER NOT NULL,usage_limit INTEGER NOT NULL DEFAULT 0,used_count INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1,expires_at TEXT,created_at TEXT NOT NULL)""",
"""CREATE TABLE IF NOT EXISTS gift_codes(code TEXT PRIMARY KEY,amount BIGINT NOT NULL DEFAULT 0,days INTEGER NOT NULL DEFAULT 0,traffic_gb INTEGER NOT NULL DEFAULT 0,usage_limit INTEGER NOT NULL DEFAULT 1,used_count INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL)""",
"""CREATE TABLE IF NOT EXISTS gift_redemptions(id BIGSERIAL PRIMARY KEY,code TEXT NOT NULL REFERENCES gift_codes(code),user_id BIGINT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,UNIQUE(code,user_id))""",
"""CREATE TABLE IF NOT EXISTS resellers(user_id BIGINT PRIMARY KEY REFERENCES users(id),commission_percent INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL)""",
"""CREATE TABLE IF NOT EXISTS referrals(inviter_id BIGINT NOT NULL REFERENCES users(id),invitee_id BIGINT NOT NULL UNIQUE REFERENCES users(id),bonus_amount BIGINT NOT NULL DEFAULT 0,created_at TEXT NOT NULL)""",
"""CREATE TABLE IF NOT EXISTS channel_settings(kind TEXT PRIMARY KEY,chat_id TEXT NOT NULL)""",
"""CREATE TABLE IF NOT EXISTS lottery_entries(id BIGSERIAL PRIMARY KEY,user_id BIGINT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL)""",
"""CREATE TABLE IF NOT EXISTS bot_buttons(btn_key TEXT PRIMARY KEY,label TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,sort_order INTEGER NOT NULL DEFAULT 100)""",
"""CREATE INDEX IF NOT EXISTS idx_gift_redemptions_user ON gift_redemptions(user_id,created_at)""",
"""CREATE INDEX IF NOT EXISTS idx_resellers_active ON resellers(active)""",
]
def upgrade():
    for statement in SQL: op.execute(statement)
def downgrade():
    for table in ["bot_buttons","lottery_entries","channel_settings","referrals","resellers","gift_redemptions","gift_codes","discount_codes","categories"]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

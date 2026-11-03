"""Mirza-style core feature parity: trial, categories, phone, wheel, rewards."""
from alembic import op

revision = "0003_mirza_parity_core"
down_revision = "0002_extended_features"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone TEXT")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_verified INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS category_id BIGINT")
    op.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS is_trial INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS is_custom INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS custom_owner_id BIGINT")
    op.execute("""CREATE TABLE IF NOT EXISTS wheel_spins(
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id),
        roll_value INTEGER,
        reward BIGINT NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS reseller_requests(
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id),
        description TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        reviewed_at TEXT,
        reviewed_by BIGINT
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS idx_reseller_requests_status ON reseller_requests(status,created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id,active)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_wheel_user ON wheel_spins(user_id,created_at)")

def downgrade():
    op.execute("DROP TABLE IF EXISTS reseller_requests")
    op.execute("DROP TABLE IF EXISTS wheel_spins")
    op.execute("DROP INDEX IF EXISTS idx_wheel_user")
    op.execute("DROP INDEX IF EXISTS idx_products_category")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS custom_owner_id")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS is_custom")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS is_trial")
    op.execute("ALTER TABLE products DROP COLUMN IF EXISTS category_id")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS phone_verified")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS phone")

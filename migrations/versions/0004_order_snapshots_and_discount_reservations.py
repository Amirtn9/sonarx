"""Persist purchase snapshots and discount reservations on each order."""

from alembic import op

revision = "0004_order_snapshot_discount"
down_revision = "0003_mirza_parity_core"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS traffic_gb BIGINT")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS days INTEGER")
    op.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS discount_code TEXT")


def downgrade():
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS discount_code")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS days")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS traffic_gb")

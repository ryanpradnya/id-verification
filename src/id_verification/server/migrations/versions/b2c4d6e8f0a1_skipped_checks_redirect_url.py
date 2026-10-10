"""skipped_checks on verifications, redirect_url on capture sessions

Revision ID: b2c4d6e8f0a1
Revises: 87e9a5bc240a
Create Date: 2026-10-10 16:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2c4d6e8f0a1"
down_revision: str | Sequence[str] | None = "87e9a5bc240a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
    with op.batch_alter_table("verifications") as t:
        t.add_column(sa.Column("skipped_checks", json, nullable=False, server_default="[]"))
    with op.batch_alter_table("capture_sessions") as t:
        t.add_column(sa.Column("redirect_url", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("capture_sessions") as t:
        t.drop_column("redirect_url")
    with op.batch_alter_table("verifications") as t:
        t.drop_column("skipped_checks")

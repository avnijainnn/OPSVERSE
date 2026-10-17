"""Encrypted per-connection Razorpay webhook secret.

Revision ID: d32f0a74c85e
Revises: c8149da77621
"""
from alembic import op
import sqlalchemy as sa

revision = "d32f0a74c85e"
down_revision = "c8149da77621"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("provider_connections") as batch:
        batch.add_column(sa.Column("webhook_secret_ciphertext", sa.Text(), nullable=True))
        batch.add_column(sa.Column("webhook_previous_secret_ciphertext", sa.Text(), nullable=True))
        batch.add_column(sa.Column("webhook_secret_rotated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("provider_connections") as batch:
        batch.drop_column("webhook_secret_ciphertext")
        batch.drop_column("webhook_previous_secret_ciphertext")
        batch.drop_column("webhook_secret_rotated_at")

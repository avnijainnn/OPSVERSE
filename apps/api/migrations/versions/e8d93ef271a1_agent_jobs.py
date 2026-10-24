"""Durable agent job state and budgets.

Revision ID: e8d93ef271a1
Revises: d32f0a74c85e
"""
from alembic import op
import sqlalchemy as sa

revision = "e8d93ef271a1"
down_revision = "d32f0a74c85e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("incident_id", sa.String(36), sa.ForeignKey("live_incidents.id"), nullable=False),
        sa.Column("requested_by_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("phase", sa.String(24), nullable=False),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_by", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_model_calls", sa.Integer(), nullable=False),
        sa.Column("reserved_model_calls", sa.Integer(), nullable=False),
        sa.Column("max_cost_microusd", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_microusd", sa.Integer(), nullable=False),
        sa.Column("reserved_cost_microusd", sa.Integer(), nullable=False),
        sa.Column("investigation_id", sa.String(36), sa.ForeignKey("live_investigations.id")),
        sa.Column("last_error", sa.String(120)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "idempotency_key"),
    )
    op.create_index("ix_agent_jobs_workspace_id", "agent_jobs", ["workspace_id"])
    op.create_index("ix_agent_jobs_incident_id", "agent_jobs", ["incident_id"])
    op.create_index("ix_agent_jobs_status", "agent_jobs", ["status"])


def downgrade() -> None:
    op.drop_table("agent_jobs")

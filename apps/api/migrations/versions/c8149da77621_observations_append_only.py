"""Enforce append-only observation history in PostgreSQL.

Revision ID: c8149da77621
Revises: b1ef79a3199b
"""

from alembic import op

revision = "c8149da77621"
down_revision = "b1ef79a3199b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("""
        CREATE FUNCTION opsverse_observation_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND
               current_setting('opsverse.allow_observation_redaction', true) = 'on' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'observations are append-only';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER observations_append_only
        BEFORE UPDATE OR DELETE ON observations
        FOR EACH ROW EXECUTE FUNCTION opsverse_observation_append_only()
    """)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER observations_append_only ON observations")
    op.execute("DROP FUNCTION opsverse_observation_append_only()")

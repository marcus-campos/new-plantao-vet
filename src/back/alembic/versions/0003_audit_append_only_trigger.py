"""audit append only trigger

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-31
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADR-0003: trilha imutável imposta pelo PRÓPRIO banco — correção é
    # sempre por adendo, nunca UPDATE/DELETE.
    op.execute(
        """
        CREATE FUNCTION audit_entries_block_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_entries is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_entries_append_only
        BEFORE UPDATE OR DELETE ON audit_entries
        FOR EACH STATEMENT EXECUTE FUNCTION audit_entries_block_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_entries_append_only ON audit_entries")
    op.execute("DROP FUNCTION audit_entries_block_mutation()")

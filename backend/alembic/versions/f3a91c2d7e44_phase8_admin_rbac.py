"""phase 8: RBAC role + admin ops columns + audit log table

Adds:
- ``users.role`` (default USER; existing rows backfilled to USER)
- ``movies.is_active`` (default true)
- ``contact_messages.is_read`` (default false)
- ``admin_audit_logs`` append-only table

All additive; downgrade drops the table/columns in reverse.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a91c2d7e44"
down_revision: Union[str, Sequence[str], None] = "b84d736f1d45"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    with op.batch_alter_table("users") as batch_op:
        user_cols = {c["name"] for c in inspector.get_columns("users")}
        if "role" not in user_cols:
            batch_op.add_column(sa.Column("role", sa.String(20), nullable=False,
                                          server_default="USER"))
        batch_op.create_index(batch_op.f("ix_users_role"), ["role"], unique=False)
    conn.execute(sa.text("UPDATE users SET role = 'USER' WHERE role IS NULL OR role = ''"))

    with op.batch_alter_table("movies") as batch_op:
        movie_cols = {c["name"] for c in inspector.get_columns("movies")}
        if "is_active" not in movie_cols:
            batch_op.add_column(sa.Column("is_active", sa.Boolean(), nullable=False,
                                          server_default=sa.true()))
        batch_op.create_index(batch_op.f("ix_movies_is_active"), ["is_active"], unique=False)
    conn.execute(sa.text("UPDATE movies SET is_active = TRUE WHERE is_active IS NULL"))

    with op.batch_alter_table("contact_messages") as batch_op:
        contact_cols = {c["name"] for c in inspector.get_columns("contact_messages")}
        if "is_read" not in contact_cols:
            batch_op.add_column(sa.Column("is_read", sa.Boolean(), nullable=False,
                                          server_default=sa.false()))
        batch_op.create_index(batch_op.f("ix_contact_messages_is_read"), ["is_read"], unique=False)
    conn.execute(sa.text("UPDATE contact_messages SET is_read = FALSE WHERE is_read IS NULL"))

    if "admin_audit_logs" not in inspector.get_table_names():
        op.create_table(
            "admin_audit_logs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("admin_user_id", sa.Integer(), nullable=True),
            sa.Column("action", sa.String(60), nullable=False),
            sa.Column("resource_type", sa.String(60), nullable=False),
            sa.Column("resource_id", sa.String(120), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["admin_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_admin_audit_logs_action"), "admin_audit_logs", ["action"], unique=False)
        op.create_index(op.f("ix_admin_audit_logs_admin_user_id"), "admin_audit_logs", ["admin_user_id"], unique=False)
        op.create_index(op.f("ix_admin_audit_logs_created_at"), "admin_audit_logs", ["created_at"], unique=False)
        op.create_index(op.f("ix_admin_audit_logs_resource_type"), "admin_audit_logs", ["resource_type"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_admin_audit_logs_resource_type"), table_name="admin_audit_logs")
    op.drop_index(op.f("ix_admin_audit_logs_created_at"), table_name="admin_audit_logs")
    op.drop_index(op.f("ix_admin_audit_logs_admin_user_id"), table_name="admin_audit_logs")
    op.drop_index(op.f("ix_admin_audit_logs_action"), table_name="admin_audit_logs")
    op.drop_table("admin_audit_logs")
    with op.batch_alter_table("contact_messages") as batch_op:
        batch_op.drop_index(batch_op.f("ix_contact_messages_is_read"))
        batch_op.drop_column("is_read")
    with op.batch_alter_table("movies") as batch_op:
        batch_op.drop_index(batch_op.f("ix_movies_is_active"))
        batch_op.drop_column("is_active")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_role"))
        batch_op.drop_column("role")

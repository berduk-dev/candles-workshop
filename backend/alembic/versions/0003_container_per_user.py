"""Контейнер Code Interpreter — один на пользователя, а не на диалог

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-26 17:38:32.375162
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_containers",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("container_id", sa.String(length=100), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_containers_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_containers")),
    )
    op.add_column("chat_files", sa.Column("container_id", sa.String(length=100), nullable=True))
    op.drop_column("conversations", "container_id")
    op.drop_column("conversations", "container_used_at")


def downgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "container_used_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True
        ),
    )
    op.add_column(
        "conversations", sa.Column("container_id", sa.VARCHAR(length=100), autoincrement=False, nullable=True)
    )
    op.drop_column("chat_files", "container_id")
    op.drop_table("user_containers")

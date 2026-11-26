"""Производство (рецепты, задания, резервы, маркировка) и ассистент (диалоги, действия, память, файлы)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-24 18:03:20.431648
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.schema.CreateSequence(sa.Sequence("job_number_seq")))
    op.create_table(
        "pseudonyms",
        sa.Column("alias", sa.String(length=30), nullable=False),
        sa.Column("original", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("alias", name=op.f("pk_pseudonyms")),
    )
    op.create_table(
        "recipes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recipes")),
        sa.UniqueConstraint("name", name=op.f("uq_recipes_name")),
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("container_id", sa.String(length=100), nullable=True),
        sa.Column("container_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_conversations_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
    )
    op.create_index(op.f("ix_conversations_user_id"), "conversations", ["user_id"], unique=False)
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(length=20), nullable=False),
        sa.Column("recipe_id", sa.Integer(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("planned_date", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "planned",
                "in_progress",
                "completed",
                "cancelled",
                name="jobstatus",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("produced_qty", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("produced_qty >= 0", name=op.f("ck_jobs_produced_qty_non_negative")),
        sa.CheckConstraint("qty > 0", name=op.f("ck_jobs_qty_positive")),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_jobs_created_by_users")),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipes.id"], name=op.f("fk_jobs_recipe_id_recipes")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.UniqueConstraint("number", name=op.f("uq_jobs_number")),
    )
    op.create_index(op.f("ix_jobs_planned_date"), "jobs", ["planned_date"], unique=False)
    op.create_table(
        "recipe_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recipe_id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("qty_per_unit", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.CheckConstraint("qty_per_unit > 0", name=op.f("ck_recipe_items_qty_per_unit_positive")),
        sa.ForeignKeyConstraint(
            ["material_id"], ["materials.id"], name=op.f("fk_recipe_items_material_id_materials")
        ),
        sa.ForeignKeyConstraint(
            ["recipe_id"], ["recipes.id"], name=op.f("fk_recipe_items_recipe_id_recipes"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recipe_items")),
        sa.UniqueConstraint("recipe_id", "material_id", name=op.f("uq_recipe_items_recipe_id_material_id")),
    )
    op.create_table(
        "user_memories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("fact", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_user_memories_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_memories")),
    )
    op.create_index(op.f("ix_user_memories_user_id"), "user_memories", ["user_id"], unique=False)
    op.create_table(
        "job_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("qty_required", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("qty_actual", sa.Numeric(precision=14, scale=3), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name=op.f("fk_job_items_job_id_jobs")),
        sa.ForeignKeyConstraint(
            ["material_id"], ["materials.id"], name=op.f("fk_job_items_material_id_materials")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_items")),
        sa.UniqueConstraint("job_id", "material_id", name=op.f("uq_job_items_job_id_material_id")),
    )
    op.create_table(
        "marking_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=36), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "sent",
                "failed",
                name="markingstatus",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("external_id", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name=op.f("fk_marking_reports_job_id_jobs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marking_reports")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_marking_reports_idempotency_key")),
        sa.UniqueConstraint("job_id", name=op.f("uq_marking_reports_job_id")),
    )
    op.create_index(
        op.f("ix_marking_reports_next_attempt_at"), "marking_reports", ["next_attempt_at"], unique=False
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "streaming",
                "complete",
                "interrupted",
                "error",
                name="messagestatus",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("used_fallback", sa.Boolean(), nullable=False),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
    )
    op.create_index(op.f("ix_messages_conversation_id"), "messages", ["conversation_id"], unique=False)
    op.create_table(
        "chat_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("filename", sa.String(length=200), nullable=False),
        sa.Column("mime", sa.String(length=100), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("storage_name", sa.String(length=100), nullable=False),
        sa.Column("container_path", sa.String(length=300), nullable=True),
        sa.Column("container_file_id", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_chat_files_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("fk_chat_files_message_id_messages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_chat_files_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_files")),
        sa.UniqueConstraint("storage_name", name=op.f("uq_chat_files_storage_name")),
    )
    op.create_index(op.f("ix_chat_files_conversation_id"), "chat_files", ["conversation_id"], unique=False)
    op.create_table(
        "marking_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["report_id"], ["marking_reports.id"], name=op.f("fk_marking_attempts_report_id_marking_reports")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marking_attempts")),
    )
    op.create_index(op.f("ix_marking_attempts_report_id"), "marking_attempts", ["report_id"], unique=False)
    op.create_table(
        "pending_actions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("tool", sa.String(length=50), nullable=False),
        sa.Column("args", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("preview", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "confirmed",
                "rejected",
                "expired",
                "failed",
                name="actionstatus",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_pending_actions_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("fk_pending_actions_message_id_messages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_pending_actions_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pending_actions")),
    )
    op.create_index(
        op.f("ix_pending_actions_conversation_id"), "pending_actions", ["conversation_id"], unique=False
    )
    op.create_index(op.f("ix_pending_actions_user_id"), "pending_actions", ["user_id"], unique=False)
    op.create_table(
        "reservations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("qty", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.CheckConstraint("qty > 0", name=op.f("ck_reservations_qty_positive")),
        sa.ForeignKeyConstraint(["batch_id"], ["batches.id"], name=op.f("fk_reservations_batch_id_batches")),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name=op.f("fk_reservations_job_id_jobs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reservations")),
        sa.UniqueConstraint("job_id", "batch_id", name=op.f("uq_reservations_job_id_batch_id")),
    )
    op.create_index(op.f("ix_reservations_batch_id"), "reservations", ["batch_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_stock_movements_job_id_jobs"), "stock_movements", "jobs", ["job_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_stock_movements_job_id_jobs"), "stock_movements", type_="foreignkey")
    op.drop_index(op.f("ix_reservations_batch_id"), table_name="reservations")
    op.drop_table("reservations")
    op.drop_index(op.f("ix_pending_actions_user_id"), table_name="pending_actions")
    op.drop_index(op.f("ix_pending_actions_conversation_id"), table_name="pending_actions")
    op.drop_table("pending_actions")
    op.drop_index(op.f("ix_marking_attempts_report_id"), table_name="marking_attempts")
    op.drop_table("marking_attempts")
    op.drop_index(op.f("ix_chat_files_conversation_id"), table_name="chat_files")
    op.drop_table("chat_files")
    op.drop_index(op.f("ix_messages_conversation_id"), table_name="messages")
    op.drop_table("messages")
    op.drop_index(op.f("ix_marking_reports_next_attempt_at"), table_name="marking_reports")
    op.drop_table("marking_reports")
    op.drop_table("job_items")
    op.drop_index(op.f("ix_user_memories_user_id"), table_name="user_memories")
    op.drop_table("user_memories")
    op.drop_table("recipe_items")
    op.drop_index(op.f("ix_jobs_planned_date"), table_name="jobs")
    op.drop_table("jobs")
    op.drop_index(op.f("ix_conversations_user_id"), table_name="conversations")
    op.drop_table("conversations")
    op.drop_table("recipes")
    op.drop_table("pseudonyms")
    op.execute(sa.schema.DropSequence(sa.Sequence("job_number_seq")))

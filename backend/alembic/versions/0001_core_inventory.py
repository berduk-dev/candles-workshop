"""Учётное ядро: пользователи, материалы, поставщики, поступления, партии, движения, курсы, аудит

Revision ID: 0001
Revises:
Create Date: 2026-11-23 17:53:40.833046
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Последовательности для автономеров документов (autogenerate их не видит).
    op.execute(sa.schema.CreateSequence(sa.Sequence("receipt_number_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("batch_number_seq")))
    op.create_table(
        "fx_rates",
        sa.Column("rate_date", sa.Date(), nullable=False),
        sa.Column(
            "currency",
            sa.Enum("RUB", "USD", "EUR", name="currency", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("rate", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("rate_date", "currency", name=op.f("pk_fx_rates")),
    )
    op.create_table(
        "materials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("unit", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_materials")),
        sa.UniqueConstraint("code", name=op.f("uq_materials_code")),
    )
    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("norm_key", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_suppliers")),
        sa.UniqueConstraint("norm_key", name=op.f("uq_suppliers_norm_key")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("login", sa.String(length=50), nullable=False),
        sa.Column("password_hash", sa.String(length=100), nullable=False),
        sa.Column("full_name", sa.String(length=150), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "storekeeper",
                "technologist",
                "manager",
                name="role",
                native_enum=False,
                length=20,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("login", name=op.f("uq_users_login")),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("user_full_name", sa.String(length=150), nullable=False),
        sa.Column("entity", sa.String(length=30), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_audit_log_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index("ix_audit_log_entity", "audit_log", ["entity", "entity_id"], unique=False)
    op.create_table(
        "receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(length=20), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("received_at", sa.Date(), nullable=False),
        sa.Column("supplier_doc", sa.String(length=100), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_receipts_created_by_users")),
        sa.ForeignKeyConstraint(
            ["supplier_id"], ["suppliers.id"], name=op.f("fk_receipts_supplier_id_suppliers")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_receipts")),
        sa.UniqueConstraint("number", name=op.f("uq_receipts_number")),
    )
    op.create_table(
        "batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(length=20), nullable=False),
        sa.Column("receipt_id", sa.Integer(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("received_at", sa.Date(), nullable=False),
        sa.Column("qty_initial", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("qty_on_hand", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("qty_reserved", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("price", sa.Numeric(precision=16, scale=4), nullable=False),
        sa.Column(
            "currency",
            sa.Enum("RUB", "USD", "EUR", name="currency", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("fx_rate", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("fx_rate_date", sa.Date(), nullable=False),
        sa.Column("unit_cost_rub", sa.Numeric(precision=16, scale=4), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "on_check",
                "accepted",
                "rejected",
                name="batchstatus",
                native_enum=False,
                length=20,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("fx_rate > 0", name=op.f("ck_batches_fx_rate_positive")),
        sa.CheckConstraint("price >= 0", name=op.f("ck_batches_price_non_negative")),
        sa.CheckConstraint("qty_initial > 0", name=op.f("ck_batches_qty_initial_positive")),
        sa.CheckConstraint("qty_on_hand >= 0", name=op.f("ck_batches_qty_on_hand_non_negative")),
        sa.CheckConstraint(
            "qty_reserved >= 0 AND qty_reserved <= qty_on_hand", name=op.f("ck_batches_qty_reserved_in_range")
        ),
        sa.ForeignKeyConstraint(
            ["material_id"], ["materials.id"], name=op.f("fk_batches_material_id_materials")
        ),
        sa.ForeignKeyConstraint(["receipt_id"], ["receipts.id"], name=op.f("fk_batches_receipt_id_receipts")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_batches")),
        sa.UniqueConstraint("number", name=op.f("uq_batches_number")),
    )
    op.create_index(
        "ix_batches_fifo", "batches", ["material_id", "status", "received_at", "id"], unique=False
    )
    op.create_table(
        "stock_movements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "receipt",
                "issue",
                "overuse",
                "return",
                name="movementkind",
                native_enum=False,
                length=20,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("qty", sa.Numeric(precision=14, scale=3), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["batches.id"], name=op.f("fk_stock_movements_batch_id_batches")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_stock_movements_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_movements")),
    )
    op.create_index(op.f("ix_stock_movements_batch_id"), "stock_movements", ["batch_id"], unique=False)
    op.create_index(op.f("ix_stock_movements_job_id"), "stock_movements", ["job_id"], unique=False)


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f("ix_stock_movements_job_id"), table_name="stock_movements")
    op.drop_index(op.f("ix_stock_movements_batch_id"), table_name="stock_movements")
    op.drop_table("stock_movements")
    op.drop_index("ix_batches_fifo", table_name="batches")
    op.drop_table("batches")
    op.drop_table("receipts")
    op.drop_index("ix_audit_log_entity", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("users")
    op.drop_table("suppliers")
    op.drop_table("materials")
    op.drop_table("fx_rates")
    op.execute(sa.schema.DropSequence(sa.Sequence("batch_number_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("receipt_number_seq")))

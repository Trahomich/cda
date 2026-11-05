"""machinery: техника — dat_machine и dat_machine_job (задача MVP-2).

docs/06-data-api.md §2 + атрибуты docs/04-domain-farm.md §1.
dat_machine_job.field_operation_id → dat_field_operation (миграция 0004).

Revision ID: 0005_machinery
Revises: 0004_agronomy
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_machinery"
down_revision = "0004_agronomy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Парк техники: госномер уникален там, где задан (partial unique index)
    op.create_table(
        "dat_machine",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "machine_type",
            sa.String(length=15),
            sa.CheckConstraint(
                "machine_type IN ('tractor', 'combine', 'sprayer', 'spreader', "
                "'seeder', 'truck', 'loader', 'other')",
                name="ck_dat_machine_type",
            ),
            nullable=False,
        ),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("gov_number", sa.String(length=20), nullable=True),
        sa.Column("width_m", sa.Numeric(5, 2), nullable=True),
        sa.Column("tank_capacity_l", sa.Integer(), nullable=True),
        sa.Column("engine_hours", sa.Numeric(10, 1), nullable=False, server_default="0"),
        sa.Column(
            "status",
            sa.String(length=15),
            sa.CheckConstraint(
                "status IN ('active', 'maintenance', 'retired')",
                name="ck_dat_machine_status",
            ),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "uq_dat_machine_gov_number",
        "dat_machine",
        ["gov_number"],
        unique=True,
        postgresql_where=sa.text("gov_number IS NOT NULL"),
    )

    # Работы техники: draft создаёт телематика, подтверждает человек (RULE-TECH-04)
    op.create_table(
        "dat_machine_job",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "machine_id",
            sa.Uuid(),
            sa.ForeignKey("dat_machine.id", name="fk_dat_machine_job_machine"),
            nullable=False,
        ),
        sa.Column("field_operation_id", sa.Uuid(), nullable=True),
        sa.Column("performed_at", sa.Date(), nullable=False),
        sa.Column("area_ha", sa.Numeric(8, 2), nullable=False),
        sa.CheckConstraint("area_ha >= 0", name="ck_dat_machine_job_area"),
        sa.Column("fuel_l", sa.Numeric(8, 1), nullable=True),
        sa.Column("track_ref", sa.String(length=500), nullable=True),
        sa.Column("duration_h", sa.Numeric(5, 2), nullable=True),
        sa.Column(
            "status",
            sa.String(length=10),
            sa.CheckConstraint(
                "status IN ('draft', 'confirmed', 'rejected')",
                name="ck_dat_machine_job_status",
            ),
            nullable=False,
            server_default="draft",
        ),
        sa.Column(
            "source",
            sa.String(length=10),
            sa.CheckConstraint(
                "source IN ('manual', 'telemetry')", name="ck_dat_machine_job_source"
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_dat_machine_job_field_operation",
        "dat_machine_job",
        "dat_field_operation",
        ["field_operation_id"],
        ["id"],
    )
    op.create_index(
        "ix_dat_machine_job_machine_date", "dat_machine_job", ["machine_id", "performed_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_dat_machine_job_machine_date", table_name="dat_machine_job")
    op.drop_table("dat_machine_job")
    op.drop_index("uq_dat_machine_gov_number", table_name="dat_machine")
    op.drop_table("dat_machine")

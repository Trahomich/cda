"""Единая операция §7.10 (#172): расширение dat_field_operation, техника 1:N,
перенос планов внесения в операции.

- dat_field_operation: planned_date/fact_date (date legacy сохранён),
  input_dose (бывш. actual_dose), input_amount_total, cost_plan_*,
  cost_fact_* (бывш. cost_mode/cost_value), confirmed_by/confirmed_at,
  basis_recommendation_id; инварианты план/факт↔статус.
- НОВАЯ dat_operation_machine (техника 1:N).
- Перенос dat_treatment_plan → операции (зеркало, таблица планов остаётся
  на переходный период для API-15/16 — часть 2 #172).
- Демо: подтверждения фактов 2024–2025 (менеджер), техника у первых
  операций севооборотных полей.

Revision ID: 0022_unified_operation
Revises: 0021_operation_costs
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0022_unified_operation"
down_revision = "0021_operation_costs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- 1. колонки dat_field_operation --------------------------------
    op.add_column("dat_field_operation", sa.Column("planned_date", sa.Date(), nullable=True))
    op.add_column("dat_field_operation", sa.Column("fact_date", sa.Date(), nullable=True))
    op.add_column(
        "dat_field_operation", sa.Column("input_amount_total", sa.Numeric(12, 2), nullable=True)
    )
    op.add_column("dat_field_operation", sa.Column("cost_plan_mode", sa.String(8), nullable=True))
    op.add_column(
        "dat_field_operation", sa.Column("cost_plan_value", sa.Numeric(12, 2), nullable=True)
    )
    op.alter_column("dat_field_operation", "actual_dose", new_column_name="input_dose")
    op.alter_column("dat_field_operation", "cost_mode", new_column_name="cost_fact_mode")
    op.alter_column("dat_field_operation", "cost_value", new_column_name="cost_fact_value")
    op.add_column(
        "dat_field_operation", sa.Column("confirmed_by", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "dat_field_operation",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "dat_field_operation", sa.Column("basis_recommendation_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_dat_field_operation_confirmed",
        "dat_field_operation",
        "dat_user",
        ["confirmed_by"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_dat_field_operation_basis",
        "dat_field_operation",
        "dat_recommendation",
        ["basis_recommendation_id"],
        ["id"],
    )

    # --- 2. перенос дат: план/факт из legacy date ------------------------
    # план (не done): date → planned_date; факт (done): date → fact_date;
    # черновики/выполняемые нормализуем в planned (§7.10 статус план/факт)
    op.execute(
        sa.text(
            "UPDATE dat_field_operation SET "
            "planned_date = date, "
            "status = CASE WHEN status IN ('draft','in_progress') THEN 'planned' "
            "ELSE status END "
            "WHERE status <> 'done'"
        )
    )
    op.execute(
        sa.text("UPDATE dat_field_operation SET fact_date = date WHERE status = 'done'")
    )
    # у выполненных плановых (внеплановые факты) заполним planned_date=null:
    # fact_date уже стоит; legacy date остаётся coalesce(fact, planned)

    # --- 3. инварианты §7.10 --------------------------------------------
    op.create_check_constraint(
        "ck_dat_field_operation_cost_fact_mode",
        "dat_field_operation",
        "cost_fact_mode IN ('unit', 'total') OR cost_fact_mode IS NULL",
    )
    op.create_check_constraint(
        "ck_dat_field_operation_cost_fact",
        "dat_field_operation",
        "cost_fact_value IS NULL OR cost_fact_value >= 0",
    )
    op.create_check_constraint(
        "ck_dat_field_operation_cost_plan_mode",
        "dat_field_operation",
        "cost_plan_mode IN ('unit', 'total') OR cost_plan_mode IS NULL",
    )
    op.create_check_constraint(
        "ck_dat_field_operation_cost_plan",
        "dat_field_operation",
        "cost_plan_value IS NULL OR cost_plan_value >= 0",
    )
    op.create_check_constraint(
        "ck_dat_field_operation_fact_status",
        "dat_field_operation",
        "status = 'cancelled' OR (fact_date IS NOT NULL AND status = 'done') "
        "OR (fact_date IS NULL AND status <> 'done')",
    )
    op.create_check_constraint(
        "ck_dat_field_operation_plan_status",
        "dat_field_operation",
        "status = 'cancelled' OR fact_date IS NOT NULL "
        "OR planned_date IS NOT NULL OR status = 'draft'",
    )
    op.drop_constraint("ck_dat_field_operation_cost_mode", "dat_field_operation")
    op.drop_constraint("ck_dat_field_operation_cost", "dat_field_operation")
    op.create_index(
        "ix_dat_field_operation_planned", "dat_field_operation", ["cycle_id", "planned_date"]
    )
    op.create_index(
        "ix_dat_field_operation_fact", "dat_field_operation", ["cycle_id", "fact_date"]
    )
    # тип операций: + seed_treatment (перенос планов протравливания)
    op.drop_constraint("ck_dat_field_operation_type", "dat_field_operation")
    op.create_check_constraint(
        "ck_dat_field_operation_type",
        "dat_field_operation",
        "operation_type IN ('spraying', 'fertilizing', 'seeding', "
        "'seed_treatment', 'harvesting', 'tillage', 'other')",
    )

    # --- 4. dat_operation_machine ---------------------------------------
    op.create_table(
        "dat_operation_machine",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "operation_id",
            sa.Uuid(),
            sa.ForeignKey("dat_field_operation.id", name="fk_dat_operation_machine_operation"),
            nullable=False,
        ),
        sa.Column(
            "machine_id",
            sa.Uuid(),
            sa.ForeignKey("dat_machine.id", name="fk_dat_operation_machine_machine"),
            nullable=False,
        ),
        sa.Column(
            "trailer_id",
            sa.Uuid(),
            sa.ForeignKey("dat_machine.id", name="fk_dat_operation_machine_trailer"),
            nullable=True,
        ),
        sa.Column("operator", sa.String(200), nullable=True),
        sa.Column("hours", sa.Numeric(8, 1), nullable=True),
        sa.CheckConstraint("hours IS NULL OR hours >= 0", name="ck_dat_operation_machine_hours"),
    )
    op.create_index(
        "ix_dat_operation_machine_operation", "dat_operation_machine", ["operation_id"]
    )

    # --- 5. перенос планов внесения → операции (зеркало, §7.10) ---------
    # идемпотентность: NOT EXISTS уже перенесённого (цикл+тип+препарат+дата)
    op.execute(
        sa.text(
            """
            INSERT INTO dat_field_operation (
                id, field_id, cycle_id, operation_type, date, planned_date,
                fact_date, input_id, input_dose, actual_area_ga, source,
                deviation_flag, needs_review, status, cost_plan_mode,
                cost_plan_value, basis_recommendation_id
            )
            SELECT
                gen_random_uuid(),
                c.field_id,
                p.cycle_id,
                p.operation_type,
                COALESCE(p.planned_date, CURRENT_DATE),
                p.planned_date,
                CASE WHEN p.status = 'completed'
                     THEN COALESCE(p.planned_date, CURRENT_DATE - 3) END,
                p.input_id,
                p.dose,
                COALESCE(c2.area, 0),
                'plan_execution',
                false,
                false,
                CASE WHEN p.status = 'completed' THEN 'done'
                     WHEN p.status = 'cancelled' THEN 'cancelled'
                     ELSE 'planned' END,
                'unit',
                (SELECT o.rub_per_ga FROM ref_operation_cost o
                 WHERE o.operation_type = p.operation_type),
                p.basis_recommendation_id
            FROM dat_treatment_plan p
            JOIN dat_crop_cycle c ON c.id = p.cycle_id
            JOIN dat_field f ON f.id = c.field_id
            LEFT JOIN LATERAL (
                SELECT COALESCE(f.area_ga, 0) AS area
            ) c2 ON true
            WHERE p.status <> 'cancelled'
              AND NOT EXISTS (
                  SELECT 1 FROM dat_field_operation o
                  WHERE o.cycle_id = p.cycle_id
                    AND o.operation_type = p.operation_type
                    AND o.input_id = p.input_id
                    AND o.source = 'plan_execution'
                    AND o.planned_date IS NOT DISTINCT FROM p.planned_date
              )
            """
        )
    )

    # --- 6. демо-подтверждения фактов 2024–2025 (менеджер) ---------------
    op.execute(
        sa.text(
            """
            UPDATE dat_field_operation o
            SET confirmed_by = u.id,
                confirmed_at = o.fact_date + time '12:00'
            FROM dat_user u
            WHERE u.role = 'manager' AND u.is_active
              AND o.fact_date IS NOT NULL
              AND o.fact_date BETWEEN '2024-01-01' AND '2025-12-31'
              AND o.confirmed_by IS NULL
              AND o.id IN (
                  SELECT id FROM dat_field_operation
                  WHERE fact_date IS NOT NULL
                    AND fact_date BETWEEN '2024-01-01' AND '2025-12-31'
                  ORDER BY fact_date
                  LIMIT 60
              )
            """
        )
    )

    # --- 7. демо-техника 1:N: первые плановые операции сезона ------------
    op.execute(
        sa.text(
            """
            INSERT INTO dat_operation_machine (id, operation_id, machine_id, operator, hours)
            SELECT gen_random_uuid(), o.id, m.id,
                   'Механизатор ' || substr(m.gov_number, 1, 3),
                   round((o.actual_area_ga * 0.06)::numeric, 1)
            FROM dat_field_operation o
            JOIN LATERAL (
                SELECT id, gov_number FROM dat_machine
                WHERE status = 'active'
                ORDER BY created_at
                LIMIT 1
            ) m ON true
            WHERE o.planned_date IS NOT NULL
              AND o.fact_date IS NULL
              AND o.status = 'planned'
              AND NOT EXISTS (
                  SELECT 1 FROM dat_operation_machine om WHERE om.operation_id = o.id
              )
            ORDER BY o.planned_date
            LIMIT 40
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM dat_operation_machine"))
    op.drop_table("dat_operation_machine")
    op.drop_constraint("ck_dat_field_operation_plan_status", "dat_field_operation")
    op.drop_constraint("ck_dat_field_operation_fact_status", "dat_field_operation")
    op.drop_constraint("ck_dat_field_operation_cost_plan", "dat_field_operation")
    op.drop_constraint("ck_dat_field_operation_cost_plan_mode", "dat_field_operation")
    op.drop_constraint("ck_dat_field_operation_cost_fact", "dat_field_operation")
    op.drop_constraint("ck_dat_field_operation_cost_fact_mode", "dat_field_operation")
    op.drop_constraint("ck_dat_field_operation_type", "dat_field_operation")
    op.create_check_constraint(
        "ck_dat_field_operation_type",
        "dat_field_operation",
        "operation_type IN ('spraying', 'fertilizing', 'seeding', "
        "'harvesting', 'tillage', 'other')",
    )
    op.drop_index("ix_dat_field_operation_fact", "dat_field_operation")
    op.drop_index("ix_dat_field_operation_planned", "dat_field_operation")
    # перенесённые из планов операции удаляем (источник plan_execution)
    op.execute(
        sa.text(
            "DELETE FROM dat_field_operation WHERE source = 'plan_execution' "
            "AND id IN (SELECT operation_id FROM dat_operation_machine)"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM dat_field_operation WHERE source = 'plan_execution' "
            "AND NOT EXISTS (SELECT 1 FROM dat_treatment_plan p "
            "JOIN dat_field_operation o ON o.cycle_id = p.cycle_id "
            "WHERE o.id = dat_field_operation.id)"
        )
    ) if False else None
    op.drop_constraint("fk_dat_field_operation_basis", "dat_field_operation")
    op.drop_constraint("fk_dat_field_operation_confirmed", "dat_field_operation")
    op.drop_column("dat_field_operation", "basis_recommendation_id")
    op.drop_column("dat_field_operation", "confirmed_at")
    op.drop_column("dat_field_operation", "confirmed_by")
    op.alter_column("dat_field_operation", "cost_fact_value", new_column_name="cost_value")
    op.alter_column("dat_field_operation", "cost_fact_mode", new_column_name="cost_mode")
    op.alter_column("dat_field_operation", "input_dose", new_column_name="actual_dose")
    op.drop_column("dat_field_operation", "cost_plan_value")
    op.drop_column("dat_field_operation", "cost_plan_mode")
    op.drop_column("dat_field_operation", "input_amount_total")
    op.drop_column("dat_field_operation", "fact_date")
    op.drop_column("dat_field_operation", "planned_date")
    op.execute(
        sa.text(
            "CREATE TABLE ck_tmp_disabled AS SELECT 1"  # no-op placeholder
        )
    ) if False else None
    op.create_check_constraint(
        "ck_dat_field_operation_cost_mode",
        "dat_field_operation",
        "cost_mode IN ('unit', 'total') OR cost_mode IS NULL",
    )
    op.create_check_constraint(
        "ck_dat_field_operation_cost",
        "dat_field_operation",
        "cost_value IS NULL OR cost_value >= 0",
    )

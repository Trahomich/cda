"""agronomy: таблицы DAT ядра агрономии (задача MVP-2).

docs/06-data-api.md §2 + атрибуты из docs/03-domain-agronomy.md §1:
поля и версии контуров, планы севооборота, циклы культур, препараты,
планы внесения, журнал операций, урожайность, алерты, рекомендации.
Площадь area_ga — generated column: геодезическая площадь контура
(geography, 03 §6.1), ST_Area(geography) даёт м² → га, округление 0,01.

Revision ID: 0004_agronomy
Revises: 0003_references
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql

revision = "0004_agronomy"
down_revision = "0003_references"
branch_labels = None
depends_on = None

AREA_GA = "round((ST_Area(contour) / 10000.0)::numeric, 2)"


def upgrade() -> None:
    # Поля: UQ(name), GIST(contour); version_id — текущая версия контура
    op.create_table(
        "dat_field",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("contour", Geography(geometry_type="POLYGON", srid=4326), nullable=False),
        sa.Column(
            "area_ga", sa.Numeric(10, 2), sa.Computed(AREA_GA, persisted=True), nullable=False
        ),
        sa.Column(
            "status",
            sa.String(length=10),
            sa.CheckConstraint(
                "status IN ('active', 'merged', 'archived')", name="ck_dat_field_status"
            ),
            nullable=False,
            server_default="active",
        ),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
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
        sa.UniqueConstraint("name", name="uq_dat_field_name"),
    )
    op.create_index(
        "ix_dat_field_contour", "dat_field", ["contour"], postgresql_using="gist"
    )

    # История контуров (03 §1.1): операции прошлых сезонов остаются
    # привязанными к версии, действовавшей на дату операции [REQ-F-002]
    op.create_table(
        "dat_field_version",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "field_id",
            sa.Uuid(),
            sa.ForeignKey("dat_field.id", name="fk_dat_field_version_field"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("contour", Geography(geometry_type="POLYGON", srid=4326), nullable=False),
        sa.Column(
            "area_ga", sa.Numeric(10, 2), sa.Computed(AREA_GA, persisted=True), nullable=False
        ),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=True),
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
        sa.UniqueConstraint("field_id", "version_no", name="uq_dat_field_version_no"),
    )
    # dat_field.current_version_id → dat_field_version (добавляем после создания:
    # таблицы ссылаются друг на друга)
    op.create_foreign_key(
        "fk_dat_field_current_version",
        "dat_field",
        "dat_field_version",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # FK ref_agrochem.field_id (справочник создан в 0003 раньше dat_field)
    op.create_foreign_key(
        "fk_ref_agrochem_field", "ref_agrochem", "dat_field", ["field_id"], ["id"]
    )

    # Планы севооборота: UQ(field_id, year_from, year_to) только для не-archived
    op.create_table(
        "dat_rotation_plan",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "field_id",
            sa.Uuid(),
            sa.ForeignKey("dat_field.id", name="fk_dat_rotation_plan_field"),
            nullable=False,
        ),
        sa.Column("year_from", sa.Integer(), nullable=False),
        sa.Column("year_to", sa.Integer(), nullable=False),
        sa.CheckConstraint("year_to >= year_from", name="ck_dat_rotation_plan_years"),
        sa.Column(
            "status",
            sa.String(length=10),
            sa.CheckConstraint(
                "status IN ('draft', 'approved', 'archived')",
                name="ck_dat_rotation_plan_status",
            ),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("approved_by", sa.String(length=200), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
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
        "uq_dat_rotation_plan_active",
        "dat_rotation_plan",
        ["field_id", "year_from", "year_to"],
        unique=True,
        postgresql_where=sa.text("status <> 'archived'"),
    )

    # Циклы культур: ровно один kind=main на поле и год (RULE-AGRO-04);
    # out_of_rotation — пометка вывода года из севооборота (RULE-AGRO-03)
    op.create_table(
        "dat_crop_cycle",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "field_id",
            sa.Uuid(),
            sa.ForeignKey("dat_field.id", name="fk_dat_crop_cycle_field"),
            nullable=False,
        ),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column(
            "culture_id",
            sa.Uuid(),
            sa.ForeignKey("ref_culture.id", name="fk_dat_crop_cycle_culture"),
            nullable=False,
        ),
        sa.Column(
            "kind",
            sa.String(length=10),
            sa.CheckConstraint(
                "kind IN ('main', 'catch', 'fallow')", name="ck_dat_crop_cycle_kind"
            ),
            nullable=False,
        ),
        sa.Column("predecessor_cycle_id", sa.Uuid(), nullable=True),
        sa.Column("planned_yield_c_ha", sa.Numeric(6, 1), nullable=True),
        sa.Column("planned_harvest_date", sa.Date(), nullable=True),
        sa.Column("out_of_rotation", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column(
            "status",
            sa.String(length=15),
            sa.CheckConstraint(
                "status IN ('planned', 'in_progress', 'completed')",
                name="ck_dat_crop_cycle_status",
            ),
            nullable=False,
            server_default="planned",
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
        "fk_dat_crop_cycle_predecessor",
        "dat_crop_cycle",
        "dat_crop_cycle",
        ["predecessor_cycle_id"],
        ["id"],
    )
    op.create_index(
        "uq_dat_crop_cycle_main",
        "dat_crop_cycle",
        ["field_id", "year"],
        unique=True,
        postgresql_where=sa.text("kind = 'main'"),
    )

    # Препараты (удобрения и СЗР): UQ(kind, name); waiting_days — только для СЗР
    op.create_table(
        "dat_agro_input",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "kind",
            sa.String(length=15),
            sa.CheckConstraint(
                "kind IN ('fertilizer', 'ppp')", name="ck_dat_agro_input_kind"
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("n_pct", sa.Numeric(4, 1), nullable=True),
        sa.Column("p2o5_pct", sa.Numeric(4, 1), nullable=True),
        sa.Column("k2o_pct", sa.Numeric(4, 1), nullable=True),
        sa.Column("active_substance", sa.String(length=200), nullable=True),
        sa.Column("concentration", sa.Numeric(5, 2), nullable=True),
        sa.Column(
            "unit",
            sa.String(length=12),
            sa.CheckConstraint(
                "unit IN ('kg_ha', 'l_ha', 'kg_dv_ha')", name="ck_dat_agro_input_unit"
            ),
            nullable=False,
        ),
        sa.Column("waiting_days", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "kind <> 'fertilizer' OR waiting_days IS NULL",
            name="ck_dat_agro_input_waiting_days",
        ),
        sa.Column(
            "approved_crops",
            postgresql.ARRAY(sa.Uuid()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "max_treatments_per_season", sa.Integer(), nullable=False, server_default="2"
        ),
        sa.Column("restrictions", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("kind", "name", name="uq_dat_agro_input_kind_name"),
    )

    # Рекомендации: обязательные rule_id / rationale / data_refs (RULE-AGRO-13)
    op.create_table(
        "dat_recommendation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("rule_id", sa.String(length=40), nullable=False),
        sa.Column(
            "target_type",
            sa.String(length=20),
            sa.CheckConstraint(
                "target_type IN ('field', 'crop_cycle', 'treatment_plan')",
                name="ck_dat_recommendation_target_type",
            ),
            nullable=False,
        ),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=500), nullable=False),
        sa.Column("rationale", sa.String(length=1000), nullable=False),
        sa.Column("data_refs", postgresql.JSONB(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=10),
            sa.CheckConstraint(
                "status IN ('new', 'accepted', 'rejected', 'expired')",
                name="ck_dat_recommendation_status",
            ),
            nullable=False,
            server_default="new",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index("ix_dat_recommendation_status", "dat_recommendation", ["status"])
    op.create_index(
        "ix_dat_recommendation_target",
        "dat_recommendation",
        ["target_type", "target_id"],
    )

    # Планы внесения: статусы по автомату 03 §2.1
    op.create_table(
        "dat_treatment_plan",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "cycle_id",
            sa.Uuid(),
            sa.ForeignKey("dat_crop_cycle.id", name="fk_dat_treatment_plan_cycle"),
            nullable=False,
        ),
        sa.Column(
            "input_id",
            sa.Uuid(),
            sa.ForeignKey("dat_agro_input.id", name="fk_dat_treatment_plan_input"),
            nullable=False,
        ),
        sa.Column("dose", sa.Numeric(8, 2), nullable=False),
        sa.CheckConstraint("dose >= 0", name="ck_dat_treatment_plan_dose"),
        sa.Column(
            "operation_type",
            sa.String(length=20),
            sa.CheckConstraint(
                "operation_type IN ('spraying', 'fertilizing', 'seed_treatment')",
                name="ck_dat_treatment_plan_op_type",
            ),
            nullable=False,
        ),
        sa.Column("planned_date", sa.Date(), nullable=True),
        sa.Column("planned_phase", sa.String(length=100), nullable=True),
        sa.Column(
            "basis_recommendation_id",
            sa.Uuid(),
            sa.ForeignKey("dat_recommendation.id", name="fk_dat_treatment_plan_basis"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(length=15),
            sa.CheckConstraint(
                "status IN ('draft', 'approved', 'in_progress', 'completed', 'cancelled')",
                name="ck_dat_treatment_plan_status",
            ),
            nullable=False,
            server_default="draft",
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
        "ix_dat_treatment_plan_cycle_status", "dat_treatment_plan", ["cycle_id", "status"]
    )

    # Журнал операций: привязка к циклу nullable (пар, внеплановые работы)
    op.create_table(
        "dat_field_operation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "field_id",
            sa.Uuid(),
            sa.ForeignKey("dat_field.id", name="fk_dat_field_operation_field"),
            nullable=False,
        ),
        sa.Column("cycle_id", sa.Uuid(), nullable=True),
        sa.Column(
            "operation_type",
            sa.String(length=15),
            sa.CheckConstraint(
                "operation_type IN ('spraying', 'fertilizing', 'seeding', "
                "'harvesting', 'tillage', 'other')",
                name="ck_dat_field_operation_type",
            ),
            nullable=False,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("input_id", sa.Uuid(), nullable=True),
        sa.Column("actual_dose", sa.Numeric(8, 2), nullable=True),
        sa.Column("actual_area_ga", sa.Numeric(10, 2), nullable=False),
        sa.Column("performer", sa.String(length=200), nullable=True),
        sa.Column(
            "source",
            sa.String(length=20),
            sa.CheckConstraint(
                "source IN ('manual', 'telemetry_import', 'plan_execution')",
                name="ck_dat_field_operation_source",
            ),
            nullable=False,
        ),
        sa.Column("deviation_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "status",
            sa.String(length=15),
            sa.CheckConstraint(
                "status IN ('draft', 'planned', 'in_progress', 'done', 'cancelled')",
                name="ck_dat_field_operation_status",
            ),
            nullable=False,
            server_default="draft",
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
        "fk_dat_field_operation_cycle",
        "dat_field_operation",
        "dat_crop_cycle",
        ["cycle_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_dat_field_operation_input",
        "dat_field_operation",
        "dat_agro_input",
        ["input_id"],
        ["id"],
    )
    op.create_index(
        "ix_dat_field_operation_field_date", "dat_field_operation", ["field_id", "date"]
    )
    op.create_index("ix_dat_field_operation_cycle", "dat_field_operation", ["cycle_id"])

    # Урожайность: UQ(cycle_id, date, source)
    op.create_table(
        "dat_yield_record",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "cycle_id",
            sa.Uuid(),
            sa.ForeignKey("dat_crop_cycle.id", name="fk_dat_yield_record_cycle"),
            nullable=False,
        ),
        sa.Column("yield_c_ha", sa.Numeric(6, 1), nullable=False),
        sa.CheckConstraint("yield_c_ha > 0", name="ck_dat_yield_record_value"),
        sa.Column("gross_t", sa.Numeric(9, 2), nullable=True),
        sa.Column(
            "source",
            sa.String(length=20),
            sa.CheckConstraint(
                "source IN ('scales', 'combine_sensor', 'estimate')",
                name="ck_dat_yield_record_source",
            ),
            nullable=False,
        ),
        sa.Column("date", sa.Date(), nullable=False),
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
        sa.UniqueConstraint("cycle_id", "date", "source", name="uq_dat_yield_record"),
    )

    # Алерты: адресат — пользователь (dat_user, миграция 0002_auth)
    op.create_table(
        "dat_alert",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "type",
            sa.String(length=20),
            sa.CheckConstraint(
                "type IN ('weather_danger', 'ndvi_anomaly', 'deviation', "
                "'machine_service', 'data_source')",
                name="ck_dat_alert_type",
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            sa.String(length=10),
            sa.CheckConstraint(
                "severity IN ('info', 'warning', 'critical')", name="ck_dat_alert_severity"
            ),
            nullable=False,
        ),
        sa.Column(
            "field_id",
            sa.Uuid(),
            sa.ForeignKey("dat_field.id", name="fk_dat_alert_field"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("dat_user.id", name="fk_dat_alert_user"),
            nullable=True,
        ),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=10),
            sa.CheckConstraint(
                "status IN ('new', 'read', 'closed')", name="ck_dat_alert_status"
            ),
            nullable=False,
            server_default="new",
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
    op.create_index("ix_dat_alert_status_user", "dat_alert", ["status", "user_id"])
    op.create_index("ix_dat_alert_type", "dat_alert", ["type"])


def downgrade() -> None:
    op.drop_index("ix_dat_alert_type", table_name="dat_alert")
    op.drop_index("ix_dat_alert_status_user", table_name="dat_alert")
    op.drop_table("dat_alert")
    op.drop_table("dat_yield_record")
    op.drop_index("ix_dat_field_operation_cycle", table_name="dat_field_operation")
    op.drop_index("ix_dat_field_operation_field_date", table_name="dat_field_operation")
    op.drop_table("dat_field_operation")
    op.drop_index(
        "ix_dat_treatment_plan_cycle_status", table_name="dat_treatment_plan"
    )
    op.drop_table("dat_treatment_plan")
    op.drop_index("ix_dat_recommendation_target", table_name="dat_recommendation")
    op.drop_index("ix_dat_recommendation_status", table_name="dat_recommendation")
    op.drop_table("dat_recommendation")
    op.drop_table("dat_agro_input")
    op.drop_index("uq_dat_crop_cycle_main", table_name="dat_crop_cycle")
    op.drop_table("dat_crop_cycle")
    op.drop_index("uq_dat_rotation_plan_active", table_name="dat_rotation_plan")
    op.drop_table("dat_rotation_plan")
    op.drop_constraint("fk_ref_agrochem_field", "ref_agrochem")
    op.drop_constraint("fk_dat_field_current_version", "dat_field")
    op.drop_table("dat_field_version")
    op.drop_index("ix_dat_field_contour", table_name="dat_field")
    op.drop_table("dat_field")

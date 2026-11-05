"""rotation rules: пар в справочнике культур и возврат лет (RULE-AGRO-02/03).

Задача MVP-8 (#26). По docs/03 §1.3 культура цикла может быть «пар»
(kind=fallow) — «культура, „пар“ или „сидерат“ (справочник культур)»;
минимальный возврат культуры (RULE-AGRO-02) — параметр справочника
(default 3; рапс 4, картофель 5), «правила как данные» (03 §6.4).

Revision ID: 0008_rotation_rules
Revises: 0007_cleanup_user_login_idx
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_rotation_rules"
down_revision = "0007_cleanup_user_login_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) ref_culture.kind + fallow (пар — полноправный элемент цепочки, RULE-AGRO-03)
    op.drop_constraint("ck_ref_culture_kind", "ref_culture", type_="check")
    op.create_check_constraint(
        "ck_ref_culture_kind",
        "ref_culture",
        "kind IN ('main', 'forage', 'catch', 'fallow')",
    )
    # 2) минимальный возврат культуры, лет (RULE-AGRO-02); default 3
    op.add_column(
        "ref_culture",
        sa.Column("repeat_years", sa.Integer(), nullable=False, server_default="3"),
    )
    # 3) данные: возвраты дольше среднего (03 §3.1 RULE-AGRO-02)
    op.execute("UPDATE ref_culture SET repeat_years = 4 WHERE name = 'Рапс'")
    op.execute("UPDATE ref_culture SET repeat_years = 5 WHERE name = 'Картофель'")


def downgrade() -> None:
    op.drop_column("ref_culture", "repeat_years")
    op.drop_constraint("ck_ref_culture_kind", "ref_culture", type_="check")
    op.execute("DELETE FROM ref_culture WHERE kind = 'fallow'")
    op.create_check_constraint(
        "ck_ref_culture_kind",
        "ref_culture",
        "kind IN ('main', 'forage', 'catch')",
    )

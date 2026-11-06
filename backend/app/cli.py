"""CLI ЦДА: python -m app.cli <команда>.

Команды:
  create-admin     — создание первого администратора (MVP-4, идемпотентно);
  seed-references  — сиды справочников ref_culture / ref_source (MVP-2,
                     идемпотентно: ON CONFLICT DO NOTHING);
  seed-weather-rules — параметры погодных окон RULE-AGRO-09 в ref_weather_rule
                     (MVP-6, идемпотентный upsert по operation_type);
  seed-demo        — демо-хозяйство целиком (MVP-13: поля, циклы, операции,
                     техника, задания, алерты, демо-погода; идемпотентно,
                     --reset пересоздаёт; включает справочники и правила);
  import-fields    — импорт реальных полей хозяйства из SHP-архива (zip)
                     или GeoJSON (v1-02, IF-FIELDS); идемпотентен по
                     external_id, --replace удаляет прежние поля; печатает
                     отчёт JSON (created/updated/skipped/area_mismatch);
  seed-rotation    — распределить севооборот по полям без плана и циклов
                     (v1-10): план + циклы по годам, RULE-AGRO-01/02;
                     идемпотентен (повторный запуск ничего не меняет).
"""

import argparse
import sys

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.auth import User, UserRole
from app.modules.weather.rules import seed_weather_rules
from app.seed import seed_references
from app.seed_inputs import seed_inputs


def cmd_create_admin(args: argparse.Namespace) -> int:
    with SessionLocal() as db:
        exists = db.scalar(select(User).where(User.login == args.login))
        if exists is not None:
            print(f"Пользователь с логином «{args.login}» уже существует")
            return 1
        user = User(
            fio=args.fio or "Администратор ЦДА",
            login=args.login,
            password_hash=hash_password(args.password),
            role=UserRole.ADMIN,
            is_active=True,
        )
        db.add(user)
        db.commit()
        print(f"Администратор «{args.login}» создан (id={user.id})")
        return 0


def cmd_seed_references(_args: argparse.Namespace) -> int:
    with SessionLocal() as db:
        inserted = seed_references(db)
    for table, count in inserted.items():
        print(f"{table}: добавлено {count} записей")
    print("Справочники наполнены (повторный запуск ничего не добавит)")
    return 0


def cmd_seed_weather_rules(_args: argparse.Namespace) -> int:
    with SessionLocal() as db:
        changed = seed_weather_rules(db)
    print(f"ref_weather_rule: актуализировано {changed} правил (повторный запуск безопасен)")
    return 0


def cmd_seed_demo(args: argparse.Namespace) -> int:
    from app.seed_demo import seed_demo

    with SessionLocal() as db:
        report = seed_demo(db, reset=args.reset)
    if report.skipped:
        print(f"Демо-хозяйство уже создано ({report.extra[0]})")
        return 0
    print(
        f"Демо-хозяйство создано: полей {report.fields}, циклов {report.cycles}, "
        f"планов севооборота {report.plans}, операций {report.operations}, "
        f"машин {report.machines}, заданий {report.jobs}, алертов {report.alerts}, "
        f"препаратов {report.inputs}, планов внесения {report.treatment_plans}"
    )
    for line in report.extra:
        print(f"- {line}")
    return 0


def cmd_import_fields(args: argparse.Namespace) -> int:
    import json

    from app.modules.fields.importer import import_fields

    with SessionLocal() as db:
        report = import_fields(db, args.path, replace=args.replace)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_seed_rotation(args: argparse.Namespace) -> int:
    import json

    from app.seed_rotation import seed_rotation

    with SessionLocal() as db:
        report = seed_rotation(db, years_back=args.years_back, years_forward=args.years_forward)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    if report.fields_processed:
        print("Севооборот распределён (повторный запуск ничего не изменит)")
    else:
        print("Нет полей без плана/циклов — распределять нечего")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="CLI ИС ЦДА")
    sub = parser.add_subparsers(dest="command", required=True)

    p_admin = sub.add_parser("create-admin", help="создать первого администратора")
    p_admin.add_argument("--login", required=True, help="логин администратора")
    p_admin.add_argument("--password", required=True, help="пароль (будет захеширован)")
    p_admin.add_argument("--fio", default="Администратор ЦДА", help="ФИО")
    p_admin.set_defaults(func=cmd_create_admin)

    p_seed = sub.add_parser("seed-references", help="наполнить справочники культур и источников")
    p_seed.set_defaults(func=cmd_seed_references)

    p_rules = sub.add_parser(
        "seed-weather-rules", help="параметры погодных окон RULE-AGRO-09 (ref_weather_rule)"
    )
    p_rules.set_defaults(func=cmd_seed_weather_rules)

    p_demo = sub.add_parser(
        "seed-demo", help="демо-хозяйство (MVP-13): поля, циклы, операции, техника, алерты"
    )
    p_demo.add_argument(
        "--reset", action="store_true", help="пересоздать демо-данные (удалить и создать заново)"
    )
    p_demo.set_defaults(func=cmd_seed_demo)

    p_import = sub.add_parser(
        "import-fields",
        help="импорт полей хозяйства из SHP-архива (zip) или GeoJSON (IF-FIELDS)",
    )
    p_import.add_argument("path", help="путь к zip(SHP) или .geojson (WGS84)")
    p_import.add_argument(
        "--replace",
        action="store_true",
        help="удалить прежние поля хозяйства перед импортом (с зависимостями)",
    )
    p_import.set_defaults(func=cmd_import_fields)

    p_rot = sub.add_parser(
        "seed-rotation",
        help="распределить севооборот по полям без плана/циклов (v1-10, RULE-AGRO-01/02)",
    )
    p_rot.add_argument("--years-back", type=int, default=4, help="лет истории (по умолчанию 4)")
    p_rot.add_argument(
        "--years-forward", type=int, default=4, help="лет плана вперёд (по умолчанию 4)"
    )
    p_rot.set_defaults(func=cmd_seed_rotation)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

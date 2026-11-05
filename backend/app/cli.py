"""CLI ЦДА: python -m app.cli <команда>.

Команды:
  create-admin     — создание первого администратора (MVP-4, идемпотентно);
  seed-references  — сиды справочников ref_culture / ref_source (MVP-2,
                     идемпотентно: ON CONFLICT DO NOTHING);
  seed-weather-rules — параметры погодных окон RULE-AGRO-09 в ref_weather_rule
                     (MVP-6, идемпотентный upsert по operation_type).
"""

import argparse
import sys

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.auth import User, UserRole
from app.modules.weather.rules import seed_weather_rules
from app.seed import seed_references


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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

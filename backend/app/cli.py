"""CLI ЦДА: python -m app.cli <команда>.

MVP-4: create-admin — создание первого администратора (idempotent: существующий
логин не перезаписывается, код возврата 1).
"""

import argparse
import sys

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.auth import User, UserRole


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="CLI ИС ЦДА")
    sub = parser.add_subparsers(dest="command", required=True)

    p_admin = sub.add_parser("create-admin", help="создать первого администратора")
    p_admin.add_argument("--login", required=True, help="логин администратора")
    p_admin.add_argument("--password", required=True, help="пароль (будет захеширован)")
    p_admin.add_argument("--fio", default="Администратор ЦДА", help="ФИО")
    p_admin.set_defaults(func=cmd_create_admin)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

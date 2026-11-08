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
  seed-inputs      — справочник препаратов: удобрения и СЗР (v2-01)
  seed-operations  — история работ и планы внесения по полям (v2-02)
                     (v1-10): план + циклы по годам, RULE-AGRO-01/02;
                     идемпотентен (повторный запуск ничего не меняет);
  backfill-ndwi    — дозаполнить ndwi_mean/ndwi_p90 исторических сцен
                     (v3-12, docs/06 §7.4) по COG-активам каталога;
                     идемпотентен (кандидаты — сцены с ndwi_p90 IS NULL);
                     печатает отчёт JSON.
  backfill-history — инжест NDVI-сцен Sentinel-2 прошлых сезонов
                     (#108, docs/06 §7.5): годы current_year−N+1…текущий
                     (по умолчанию 4), конвейер каталог+NDVI+рендеры;
                     идемпотентен (UQ каталога и сцен); после — инвалидация
                     кэша insights:*/vra:peak:* полей; печатает отчёт JSON.
  advice-refresh   — генерация AI-рекомендаций «что делать сейчас»
                     (#120, docs/09 §9.4): все поля или --field-id;
                     идемпотентен (пропускает поля с advice за сегодня
                     и тем же context_hash); печатает отчёт JSON.
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


def cmd_seed_inputs(_args: argparse.Namespace) -> int:
    import json

    with SessionLocal() as db:
        report = seed_inputs(db)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    if report.inserted:
        print(f"Справочник препаратов: добавлено {report.inserted} позиций")
    else:
        print("Справочник препаратов уже заполнен — дублей не создано")
    return 0


def cmd_seed_operations(_args: argparse.Namespace) -> int:
    import json

    from app.seed_operations import seed_operations

    with SessionLocal() as db:
        report = seed_operations(db)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    if report.violations:
        print(f"ВНИМАНИЕ: {len(report.violations)} планов отклонено правилами (см. violations)")
    if report.operations_created or report.plans_created:
        print(
            f"Работы: операций +{report.operations_created}, планов +{report.plans_created} "
            "(повторный запуск ничего не изменит)"
        )
    else:
        print("История и планы уже на месте — дублей не создано")
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


def cmd_backfill_ndwi(args: argparse.Namespace) -> int:
    import json
    from datetime import date as Date

    from app.services.sentinel_ingest.ndwi_backfill import run_ndwi_backfill

    with SessionLocal() as db:
        report = run_ndwi_backfill(
            db, date_from=Date.fromisoformat(args.date_from), max_workers=args.workers
        )
    data = report.as_dict()
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if data["unavailable_count"]:
        print("ВНИМАНИЕ: часть COG недоступна — повторный запуск доберёт эти сцены")
        return 1
    return 0


def cmd_backfill_history(args: argparse.Namespace) -> int:
    import json

    from app.core.config import get_settings
    from app.integrations.stac import EarthSearchStacClient
    from app.services.sentinel_ingest.history_backfill import run_history_backfill

    settings = get_settings()
    client = EarthSearchStacClient(
        base_url=settings.stac_api_url,
        request_timeout=settings.ingest_satellite_request_timeout,
    )
    with SessionLocal() as db:
        report = run_history_backfill(
            db,
            client=client,
            years=args.years,
            batch_limit=settings.ingest_satellite_ndvi_batch,
            max_workers=args.workers or settings.ingest_satellite_ndvi_workers,
            render_dir=settings.ndvi_render_dir or None,
        )
    data = report.as_dict()
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if data["catalog_failed_count"] or data["scenes_unavailable_count"]:
        print("ВНИМАНИЕ: часть окон STAC/COG недоступна — повторный запуск доберёт")
        return 1
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

    p_inputs = sub.add_parser(
        "seed-inputs",
        help="справочник препаратов: ≥30 реальных удобрений и СЗР (v2-01, docs/06 §7.2)",
    )
    p_inputs.set_defaults(func=cmd_seed_inputs)

    p_ops = sub.add_parser(
        "seed-operations",
        help="история работ 2022–2025 и планы внесения 2026 по полям (v2-02, docs/06 §7.2)",
    )
    p_ops.set_defaults(func=cmd_seed_operations)

    p_rot = sub.add_parser(
        "seed-rotation",
        help="распределить севооборот по полям без плана/циклов (v1-10, RULE-AGRO-01/02)",
    )
    p_rot.add_argument("--years-back", type=int, default=4, help="лет истории (по умолчанию 4)")
    p_rot.add_argument(
        "--years-forward", type=int, default=4, help="лет плана вперёд (по умолчанию 4)"
    )
    p_rot.set_defaults(func=cmd_seed_rotation)

    p_ndwi = sub.add_parser(
        "backfill-ndwi",
        help="дозаполнить ndwi_mean/ndwi_p90 исторических сцен (v3-12, docs/06 §7.4)",
    )
    p_ndwi.add_argument(
        "--date-from",
        required=True,
        help="сцены с captured_at от этой даты включительно (YYYY-MM-DD)",
    )
    p_ndwi.add_argument(
        "--workers", type=int, default=4, help="потоков чтения COG (по умолчанию 4)"
    )
    p_ndwi.set_defaults(func=cmd_backfill_ndwi)

    p_hist = sub.add_parser(
        "backfill-history",
        help="инжест NDVI-сцен Sentinel-2 прошлых сезонов (#108, docs/06 §7.5)",
    )
    p_hist.add_argument(
        "--years", type=int, default=4, help="глубина истории, включая текущий год (4)"
    )
    p_hist.add_argument(
        "--workers", type=int, default=None, help="потоков чтения COG (по умолчанию из env)"
    )
    p_hist.set_defaults(func=cmd_backfill_history)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

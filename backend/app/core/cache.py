"""Мягкий кэш в Redis/Dragonfly (деплой compose): недоступность или ошибка
сериализации не влияет на ответы — просто нет кэша.

Используется для тяжёлых read-only расчётов по запросу: зоны VRA (§7.4),
«Выводы ассистента». Значения — JSON (PNG хранится base64).
"""

import json
import logging

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_redis = None
"""Ленивый клиент: создаётся при первом обращении, один на процесс."""


def client():
    """Клиент Redis/Dragonfly из настроек (redis_host/port/db, §7.4)."""
    global _redis
    if _redis is None:
        import redis

        settings = get_settings()
        _redis = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            socket_timeout=1.0,
        )
    return _redis


def cache_get(key: str):
    """Значение по ключу (JSON) или None; недоступность кэша — не ошибка."""
    try:
        raw = client().get(key)
    except Exception:  # сеть/отсутствие сервера — работаем без кэша
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("кэш: нечитаемое значение по ключу %s — игнорирую", key)
        return None


def cache_set(key: str, value, ttl_seconds: int) -> None:
    """Записать значение с TTL; недоступность кэша — не ошибка."""
    try:
        client().setex(key, int(ttl_seconds), json.dumps(value, default=str))
    except Exception:
        logger.debug("кэш: не удалось записать %s — работаем без кэша", key, exc_info=True)


def cache_delete(*patterns: str) -> int:
    """Удалить ключи по glob-паттернам (scan+delete); вернуть число удалённых.

    Недоступность кэша — не ошибка (мягкий fail, как у get/set): работаем
    дальше, ключи истекут по TTL сами.
    """
    deleted = 0
    try:
        client_local = client()
        for pattern in patterns:
            keys = list(client_local.scan_iter(match=pattern, count=100))
            if keys:
                deleted += int(client_local.delete(*keys))
    except Exception:  # сеть/отсутствие сервера — работаем без кэша
        logger.debug(
            "кэш: не удалось удалить по %s — ключи истекут по TTL", patterns, exc_info=True
        )
    return deleted


__all__ = ["cache_delete", "cache_get", "cache_set", "client"]

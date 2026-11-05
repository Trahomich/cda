"""Внутренняя шина событий (ADR-01: модульный монолит + события, 02 §8).

Коммуникация между модулями — публикация/подписка в рамках процесса:
публикация не блокирует автора: исключения обработчиков логируются
и не прерывают транзакцию модуля. События не персистятся — состояние
живёт в БД, шина только уведомляет (ADR-01: «общение через события и БД»).
"""

import logging
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

Handler = Callable[["Event"], None]


@dataclass(frozen=True, slots=True)
class Event:
    """Событие шины: тип + JSON-подобный payload (CONTRACTS §5: snake_case)."""

    event_type: str
    payload: dict
    event_id: str
    at: datetime


_handlers: dict[str, list[Handler]] = defaultdict(list)


def subscribe(event_type: str, handler: Handler) -> None:
    """Подписать обработчик на тип события (идемпотентно)."""
    if handler not in _handlers[event_type]:
        _handlers[event_type].append(handler)


def unsubscribe(event_type: str, handler: Handler) -> None:
    """Снять подписку (например, при остановке фонового сервиса)."""
    try:
        _handlers[event_type].remove(handler)
    except ValueError:
        pass


def publish(event_type: str, payload: dict) -> Event:
    """Опубликовать событие; вернуть его (для тестов и логирования).

    Ошибка обработчика не мешает остальным и не пробрасывается автору:
    публикация — уведомление, а не RPC (ADR-01).
    """
    event = Event(
        event_type=event_type,
        payload=payload,
        event_id=str(uuid.uuid4()),
        at=datetime.now(tz=UTC),
    )
    for handler in list(_handlers.get(event_type, ())):
        try:
            handler(event)
        except Exception:  # noqa: BLE001 — шина не должна ронять публикатора
            logger.exception("шина: обработчик события %s упал", event_type)
    return event


def reset_bus() -> None:
    """Снять все подписки (тесты: изоляция между тестами)."""
    _handlers.clear()


__all__ = ["Event", "Handler", "publish", "reset_bus", "subscribe", "unsubscribe"]

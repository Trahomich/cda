"""Клиент внешнего Ollama для LLM-сводок (ADR-08, docs/09-ai.md §8.1).

Единственная точка обращения к LLM: ``summarize(prompt, context) -> str | None``.
Инференс — внешний Ollama-сервер ``OLLAMA_BASE_URL`` (env, пусто по умолчанию =
ИИ-текст выключен, запросов нет вовсе). Модель — ``OLLAMA_MODEL``
(по умолчанию ``qwen3.5:9b``: корректный русский агро-текст, ~7 ток/с на CPU —
тест на сервере пользователя 192.168.99.201:11434 от 2026-11-07).

Деградация (REQ-NF-009): таймаут 60 с, 1 повтор; недоступен/таймаут/пустой
ответ → ``None`` — вызывающий код (§8.2) обойдётся без ИИ-текста.

Конфиденциальность (REQ-F-036): данные хозяйства уходят только на
``OLLAMA_BASE_URL``; промпт строит только бэкенд.
"""

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen3.5:9b"
REQUEST_TIMEOUT_SECONDS = 60.0
RETRY_ATTEMPTS = 2  # запрос + 1 повтор (09 §8.1)


def summarize(
    prompt: str,
    context: str,
    *,
    http: httpx.Client | None = None,
    base_url: str | None = None,
    model: str | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
    keep_alive: str | None = None,
    attempts: int | None = None,
    num_predict: int | None = None,
) -> str | None:
    """Свести контекст данных хозяйства в короткий текст через Ollama.

    ``None`` — Ollama не сконфигурирован (пустой OLLAMA_BASE_URL), недоступен,
    превысил таймаут после повтора или вернул пустой текст: вызывающий код
    работает дальше без ИИ-сводки (деградация, §8.1).

    Фоновые вызовы (#93, §8.1): ``timeout=600`` + ``attempts=1`` (без
    повтора) и ``keep_alive="30m"``, ``num_predict=256`` — модель не
    выгружается между сводками (холодный старт qwen3.5:9b ~114 с,
    2026-11-07), а длина генерации ограничена (CPU-инференс ~7 ток/с:
    без лимита сводка не успевает за таймаут, #112).

    ``num_predict`` — лимит генерации (токенов) в options; ``None`` =
    лимит не передаётся (значение Ollama по умолчанию).
    """
    settings = get_settings()
    url_root = (settings.ollama_base_url if base_url is None else base_url).strip()
    if not url_root:
        return None  # ИИ-текст выключен: без единого сетевого запроса
    chosen_model = (model or settings.ollama_model or DEFAULT_MODEL).strip() or DEFAULT_MODEL

    max_attempts = RETRY_ATTEMPTS if attempts is None else max(1, int(attempts))
    body: dict = {
        "model": chosen_model,
        "prompt": f"{prompt}\n\nДанные хозяйства (context):\n{context}",
        "stream": False,
        "options": {"temperature": 0.3, "num_ctx": 4096},
    }
    if keep_alive:
        body["keep_alive"] = keep_alive

    own_client = http is None
    client = http or httpx.Client(timeout=timeout)
    try:
        for attempt in range(1, max_attempts + 1):
            try:
                resp = client.post(f"{url_root.rstrip('/')}/api/generate", json=body)
                if resp.status_code >= 500:  # сервер перегружен — повтор
                    logger.warning(
                        "ollama: HTTP %d (попытка %d/%d)",
                        resp.status_code,
                        attempt,
                        max_attempts,
                    )
                    continue
                if resp.status_code != 200:
                    logger.warning("ollama: HTTP %d — без повтора", resp.status_code)
                    return None
                text = str(resp.json().get("response") or "").strip()
                return text or None
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                logger.warning(
                    "ollama: %s (попытка %d/%d)",
                    type(exc).__name__,
                    attempt,
                    max_attempts,
                )
        return None
    finally:
        if own_client:
            client.close()


__all__ = ["DEFAULT_MODEL", "REQUEST_TIMEOUT_SECONDS", "summarize"]

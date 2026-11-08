"""Клиент внешнего LLM для сводок (ADR-08, docs/09-ai.md §8.1).

Единственная точка обращения к LLM: ``summarize(prompt, context) -> str | None``.
Провайдер — env ``LLM_PROVIDER`` (#115):

- ``ollama`` (по умолчанию, обратная совместимость): внешний Ollama-сервер
  ``OLLAMA_BASE_URL`` (env, пусто = ИИ-текст выключен, запросов нет вовсе),
  модель ``OLLAMA_MODEL`` (по умолчанию ``qwen3.5:9b``: корректный русский
  агро-текст, ~7 ток/с на CPU — тест на сервере пользователя
  192.168.99.201:11434 от 2026-11-07).
- ``anthropic``: anthropic-совместимое облако (по умолчанию api.z.ai,
  ``ANTHROPIC_BASE_URL``), Messages API ``POST {base}/v1/messages``,
  модель ``LLM_MODEL`` (по умолчанию ``glm-5.3-flash``). Ключ —
  ``ANTHROPIC_API_KEY``, только из env; пустой ключ = выключено, без сети.
  glm-5.3-flash думающая: thinking-блок всегда первый, max_tokens даём
  с запасом (1024) — малые лимиты съедаются thinking (#115).

Деградация (REQ-NF-009): таймаут 60 с (синхронно), 1 повтор; провайдер
не сконфигурирован/недоступен/таймаут/пустой ответ → ``None`` — вызывающий
код (§8.2) обойдётся без ИИ-текста.

Конфиденциальность (REQ-F-036): данные хозяйства уходят только на адрес
сконфигурированного провайдера; промпт строит только бэкенд.
"""

import base64
import logging
from collections.abc import Sequence

import httpx

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen3.5:9b"
ANTHROPIC_DEFAULT_MODEL = "glm-5.3-flash"
ANTHROPIC_VERSION = "2023-06-01"
#: glm-5.3-flash думающая: текст приходит после thinking-блока, поэтому
#: max_tokens всегда с запасом — малые лимиты (как 120) съедаются thinking (#115)
ANTHROPIC_MAX_TOKENS = 1024
REQUEST_TIMEOUT_SECONDS = 60.0
RETRY_ATTEMPTS = 2  # запрос + 1 повтор (09 §8.1)


def llm_enabled(settings: Settings) -> bool:
    """Включён ли ИИ-текст при текущем провайдере (без сети, по env).

    ollama: непустой OLLAMA_BASE_URL; anthropic: непустой ANTHROPIC_API_KEY.
    Вызывающий код (service.py) решает по этому флагу, запускать ли фоновую
    доработку сводки.
    """
    provider = settings.llm_provider.strip().lower()
    if provider == "anthropic":
        return bool(settings.anthropic_api_key.strip())
    return bool(settings.ollama_base_url.strip())


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
    provider: str | None = None,
    api_key: str | None = None,
) -> str | None:
    """Свести контекст данных хозяйства в короткий текст (§8.1).

    ``None`` — провайдер не сконфигурирован, недоступен, превысил таймаут
    после повтора или вернул пустой текст: вызывающий код работает дальше
    без ИИ-сводки (деградация, §8.1).

    Провайдер — ``LLM_PROVIDER`` (ollama по умолчанию) либо явный аргумент
    ``provider``. Параметры ``keep_alive``/``num_predict`` имеют смысл
    только для ollama-ветки и в anthropic-ветке игнорируются; ``api_key`` —
    только для anthropic (по умолчанию ANTHROPIC_API_KEY из env).

    Фоновые вызовы ollama (#93, §8.1): ``timeout=600`` + ``attempts=1`` (без
    повтора) и ``keep_alive="30m"``, ``num_predict=256`` — модель не
    выгружается между сводками (холодный старт qwen3.5:9b ~114 с,
    2026-11-07), а длина генерации ограничена (CPU-инференс ~7 ток/с:
    без лимита сводка не успевает за таймаут, #112). Облако отвечает за
    секунды, общий фоновый таймаут 600 с годится и для него.

    ``num_predict`` — лимит генерации (токенов) в options; ``None`` =
    лимит не передаётся (значение Ollama по умолчанию).

    В ollama-тело запроса всегда добавляется top-level ``"think": False``
    (#113): у think-моделей (qwen3.5) блок размышлений съедает весь
    num_predict и response остаётся пустым; не-think-модели параметр
    игнорируют. В anthropic-ветке thinking-блок не отключается (glm-5.3-flash
    присылает его даже при thinking disabled) — просто берём первый
    content-блок type="text" (#115).
    """
    settings = get_settings()
    chosen_provider = (provider or settings.llm_provider or "ollama").strip().lower()
    if chosen_provider == "anthropic":
        return _summarize_anthropic(
            prompt,
            context,
            settings=settings,
            http=http,
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout=timeout,
            attempts=attempts,
        )
    return _summarize_ollama(
        prompt,
        context,
        settings=settings,
        http=http,
        base_url=base_url,
        model=model,
        timeout=timeout,
        keep_alive=keep_alive,
        attempts=attempts,
        num_predict=num_predict,
    )


def _summarize_ollama(
    prompt: str,
    context: str,
    *,
    settings: Settings,
    http: httpx.Client | None,
    base_url: str | None,
    model: str | None,
    timeout: float,
    keep_alive: str | None,
    attempts: int | None,
    num_predict: int | None,
) -> str | None:
    """Ветка Ollama: POST {OLLAMA_BASE_URL}/api/generate (stream off)."""
    url_root = (settings.ollama_base_url if base_url is None else base_url).strip()
    if not url_root:
        return None  # ИИ-текст выключен: без единого сетевого запроса
    chosen_model = (model or settings.ollama_model or DEFAULT_MODEL).strip() or DEFAULT_MODEL

    max_attempts = RETRY_ATTEMPTS if attempts is None else max(1, int(attempts))
    body: dict = {
        "model": chosen_model,
        "prompt": f"{prompt}\n\nДанные хозяйства (context):\n{context}",
        "stream": False,
        # top-level (НЕ в options): у think-моделей (qwen3.5) блок размышлений
        # съедает весь num_predict — response пуст (#113); не-think-модели
        # параметр игнорируют, сводке «подумать» не нужно (§8.1)
        "think": False,
        "options": {"temperature": 0.3, "num_ctx": 4096},
    }
    if num_predict is not None:
        body["options"]["num_predict"] = num_predict
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


def _summarize_anthropic(
    prompt: str,
    context: str,
    *,
    settings: Settings,
    http: httpx.Client | None,
    base_url: str | None,
    model: str | None,
    api_key: str | None,
    timeout: float,
    attempts: int | None,
) -> str | None:
    """Ветка anthropic-облака: POST {base}/v1/messages, glm-5.3-flash (#115).

    Ответ — content-блоки; берём первый type="text" (thinking-блок
    glm-5.3-flash пропускаем — он есть всегда, даже при disabled).
    """
    url_root = (settings.anthropic_base_url if base_url is None else base_url).strip()
    key = (settings.anthropic_api_key if api_key is None else api_key).strip()
    if not url_root or not key:
        return None  # провайдер выключен: без единого сетевого запроса
    chosen_model = (model or settings.llm_model or ANTHROPIC_DEFAULT_MODEL).strip() or (
        ANTHROPIC_DEFAULT_MODEL
    )

    max_attempts = RETRY_ATTEMPTS if attempts is None else max(1, int(attempts))
    body = {
        "model": chosen_model,
        "max_tokens": ANTHROPIC_MAX_TOKENS,
        "messages": [
            {"role": "user", "content": f"{prompt}\n\nДанные хозяйства (context):\n{context}"}
        ],
    }
    headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}

    own_client = http is None
    client = http or httpx.Client(timeout=timeout)
    try:
        for attempt in range(1, max_attempts + 1):
            try:
                resp = client.post(
                    f"{url_root.rstrip('/')}/v1/messages", json=body, headers=headers
                )
                if resp.status_code >= 500:  # сервер перегружен — повтор
                    logger.warning(
                        "anthropic: HTTP %d (попытка %d/%d)",
                        resp.status_code,
                        attempt,
                        max_attempts,
                    )
                    continue
                if resp.status_code != 200:
                    logger.warning("anthropic: HTTP %d — без повтора", resp.status_code)
                    return None
                blocks = resp.json().get("content") or []
                for block in blocks:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = str(block.get("text") or "").strip()
                        if text:
                            return text
                return None  # только thinking-блоки / пустой текст
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                logger.warning(
                    "anthropic: %s (попытка %d/%d)",
                    type(exc).__name__,
                    attempt,
                    max_attempts,
                )
        return None
    finally:
        if own_client:
            client.close()


__all__ = [
    "ANTHROPIC_DEFAULT_MODEL",
    "ANTHROPIC_MAX_TOKENS",
    "ANTHROPIC_VERSION",
    "DEFAULT_MODEL",
    "REQUEST_TIMEOUT_SECONDS",
    "llm_enabled",
    "summarize",
]

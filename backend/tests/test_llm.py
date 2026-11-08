"""Тесты LLM-клиента «Выводов ассистента» (v3-02, docs/09-ai.md §8.1).

Наружу тесты не ходят: httpx.MockTransport. Деградация: пустой
OLLAMA_BASE_URL — None без единого запроса; таймаут/недоступность после
1 повтора — None; успех — текст «response». Ветка anthropic (#115):
POST /v1/messages, thinking-блок пропускается, только thinking → None.
"""

import json

import httpx
import pytest
from app.ai_assistant.llm import (
    ANTHROPIC_DEFAULT_MODEL,
    DEFAULT_MODEL,
    llm_enabled,
    summarize,
)
from app.core.config import Settings


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_summarize_success_returns_response_text() -> None:
    """Успех: POST /api/generate → str из поля response."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"response": "  Краткая сводка.  ", "done": True})

    text = summarize(
        "Сделай сводку", "NDVI 0.45", http=_client(handler), base_url="http://ollama.local:11434"
    )
    assert text == "Краткая сводка."
    assert len(seen) == 1
    req = seen[0]
    assert str(req.url) == "http://ollama.local:11434/api/generate"
    body = json.loads(req.read().decode())
    assert body["model"] == DEFAULT_MODEL  # qwen3.5:9b (09 §8.1)
    assert body["stream"] is False
    assert body["think"] is False  # think-блок не съедает num_predict (#113)
    assert body["options"] == {"temperature": 0.3, "num_ctx": 4096}
    assert "Сделай сводку" in body["prompt"] and "NDVI 0.45" in body["prompt"]


def test_summarize_empty_base_url_returns_none_without_network() -> None:
    """Пустой OLLAMA_BASE_URL → None, запросов нет вовсе (REQ-NF-009).

    Handler кидает AssertionError — если бы запрос случился, тест упал бы
    (исключение из MockTransport не глотается деградацией).
    """

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("запрос не должен выполняться при пустом base URL")

    for empty in ("", "   "):
        assert summarize("p", "c", http=_client(handler), base_url=empty) is None


def test_summarize_timeout_retries_once_then_none() -> None:
    """Таймаут: 1 повтор, затем None (деградация)."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectTimeout("нет ответа")

    assert (
        summarize(
            "p", "c", http=_client(handler), base_url="http://ollama.local:11434", timeout=0.01
        )
        is None
    )
    assert len(calls) == 2  # запрос + 1 повтор


def test_summarize_server_error_retries_then_none() -> None:
    """HTTP 5xx → повтор → None; HTTP 4xx → None без повтора."""
    calls_5xx: list[int] = []

    def handler_5xx(request: httpx.Request) -> httpx.Response:
        calls_5xx.append(1)
        return httpx.Response(503, text="overloaded")

    assert summarize("p", "c", http=_client(handler_5xx), base_url="http://o:11434") is None
    assert len(calls_5xx) == 2

    calls_4xx: list[int] = []

    def handler_4xx(request: httpx.Request) -> httpx.Response:
        calls_4xx.append(1)
        return httpx.Response(404, json={"error": "model not found"})

    assert summarize("p", "c", http=_client(handler_4xx), base_url="http://o:11434") is None
    assert len(calls_4xx) == 1  # клиентская ошибка не повторяется


def test_summarize_empty_response_is_none() -> None:
    """Пустой ответ модели — тоже None (текста нет — деградация)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": "   ", "done": True})

    assert summarize("p", "c", http=_client(handler), base_url="http://o:11434") is None


def test_summarize_num_predict_sent_only_when_given() -> None:
    """num_predict попадает в options только если задан (#112):
    по умолчанию лимит генерации не передаётся (значение Ollama)."""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.read().decode()))
        return httpx.Response(200, json={"response": "ok"})

    kwargs = {"http": _client(handler), "base_url": "http://o:11434"}
    assert summarize("p", "c", **kwargs) == "ok"
    assert bodies[-1]["options"] == {"temperature": 0.3, "num_ctx": 4096}

    assert summarize("p", "c", num_predict=256, **kwargs) == "ok"
    assert bodies[-1]["options"] == {"temperature": 0.3, "num_ctx": 4096, "num_predict": 256}
    assert bodies[-1]["think"] is False  # top-level, не в options (#113)


def test_summarize_model_override() -> None:
    """Явная модель переопределяет настройку (проверяется в payload)."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read().decode())
        assert body["model"] == "llama3:8b"
        return httpx.Response(200, json={"response": "ok"})

    assert (
        summarize("p", "c", http=_client(handler), base_url="http://o:11434", model="llama3:8b")
        == "ok"
    )


# --- anthropic-ветка (провайдер облака, #115) ---------------------------


def _anthropic(**kw):
    """summarize с явным anthropic-провайдером и ключом (кроме переопределений)."""
    defaults = {
        "http": _client(kw.pop("handler")),
        "base_url": "http://zai.local",
        "provider": "anthropic",
        "api_key": "test-key",
    }
    defaults.update(kw)
    return summarize("p", "c", **defaults)


def test_anthropic_thinking_then_text_returns_text() -> None:
    """content = [thinking, text] → первый text-блок (thinking пропущен)."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "thinking", "thinking": "размышления…"},
                    {"type": "text", "text": "  Краткая сводка.  "},
                ],
                "stop_reason": "end_turn",
            },
        )

    assert _anthropic(handler=handler) == "Краткая сводка."
    req = seen[0]
    assert str(req.url) == "http://zai.local/v1/messages"  # Messages API
    assert req.headers["x-api-key"] == "test-key"
    assert req.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(req.read().decode())
    assert body["model"] == ANTHROPIC_DEFAULT_MODEL  # glm-5.3-flash
    assert body["max_tokens"] == 3072  # thinking glm варьируется: 1024 съедал бюджет (#116)
    assert body["messages"] == [
        {
            "role": "user",
            "content": [{"type": "text", "text": "p\n\nДанные хозяйства (context):\nc"}],
        }
    ]
    assert "think" not in body  # num_predict/think — только у ollama-ветки


def test_anthropic_only_thinking_returns_none() -> None:
    """Только thinking-блоки (лимит съеден) → None без второго запроса."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        payload = {"content": [{"type": "thinking", "thinking": "…"}], "stop_reason": "max_tokens"}
        return httpx.Response(200, json=payload)

    assert _anthropic(handler=handler, attempts=1) is None
    assert len(calls) == 1


def test_anthropic_error_returns_none() -> None:
    """HTTP 4xx → None без повтора; таймаут → повтор → None."""

    calls: list[int] = []

    def handler_4xx(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, json={"error": "bad key"})

    assert _anthropic(handler=handler_4xx) is None
    assert len(calls) == 1

    def handler_timeout(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectTimeout("нет ответа")

    assert _anthropic(handler=handler_timeout, timeout=0.01) is None
    assert len(calls) == 3  # 401 + таймаут с 1 повтором


def test_anthropic_images_as_content_blocks() -> None:
    """images → content-блоки image (base64 PNG) перед текстом (#119, §9.1)."""
    import base64

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    png = b"\x89PNG-fake-bytes"
    assert _anthropic(handler=handler, images=[png, png]) == "ok"
    # body проверим через отдельный запрос со «шпионом»
    seen: list[httpx.Request] = []

    def spy(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    assert _anthropic(handler=spy, images=[png]) == "ok"
    body = json.loads(seen[0].read().decode())
    blocks = body["messages"][0]["content"]
    assert blocks[0] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(png).decode("ascii"),
        },
    }
    assert blocks[1]["type"] == "text"


def test_ollama_branch_ignores_images() -> None:
    """images не влияют на ollama-ветку (vision — только anthropic, §9.1)."""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.read().decode()))
        return httpx.Response(200, json={"response": "ok"})

    kwargs = {"http": _client(handler), "base_url": "http://o:11434", "images": [b"\x89PNG"]}
    assert summarize("p", "c", **kwargs) == "ok"
    assert "images" not in bodies[-1]
    assert bodies[-1]["prompt"].startswith("p\n")  # тело прежнее (только prompt)


def test_anthropic_no_key_returns_none_without_network() -> None:
    """Пустой ключ (по умолчанию env пуст) → None, запросов нет вовсе."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("запрос не должен выполняться без ключа")

    assert _anthropic(handler=handler, api_key="   ") is None


@pytest.mark.parametrize(
    ("update", "expected"),
    [
        ({"llm_provider": "ollama", "ollama_base_url": ""}, False),
        ({"llm_provider": "ollama", "ollama_base_url": "http://o:11434"}, True),
        ({"llm_provider": "anthropic", "anthropic_api_key": ""}, False),
        ({"llm_provider": "anthropic", "anthropic_api_key": "k"}, True),
    ],
)
def test_llm_enabled_by_provider(update: dict, expected: bool) -> None:
    """llm_enabled: ollama — по base URL, anthropic — по ключу (#115)."""
    assert llm_enabled(Settings(**update)) is expected

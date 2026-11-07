"""Тесты Ollama-клиента «Выводов ассистента» (v3-02, docs/09-ai.md §8.1).

Наружу тесты не ходят: httpx.MockTransport. Деградация: пустой
OLLAMA_BASE_URL — None без единого запроса; таймаут/недоступность после
1 повтора — None; успех — текст «response».
"""

import json

import httpx
from app.ai_assistant.llm import DEFAULT_MODEL, summarize


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

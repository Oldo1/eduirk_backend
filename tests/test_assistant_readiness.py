from types import SimpleNamespace

import pytest

import routers.assistant as assistant
from rag_pipeline import KnowledgeBaseNotReadyError, RAGSystem


def _reset_assistant_state(monkeypatch, tmp_path, credentials: str | None = "dummy") -> None:
    if credentials is None:
        monkeypatch.delenv("GIGACHAT_CREDENTIALS", raising=False)
    else:
        monkeypatch.setenv("GIGACHAT_CREDENTIALS", credentials)

    monkeypatch.setattr(
        assistant.cfg,
        "_value",
        SimpleNamespace(
            persist_dir=str(tmp_path),
            collection_name="eduirk",
            request_timeout=30.0,
            max_retries=1,
        ),
        raising=False,
    )
    monkeypatch.setattr(assistant, "_vectorstore", None)
    monkeypatch.setattr(assistant, "_embeddings_ready", False)
    monkeypatch.setattr(assistant, "_warmup_started_at", None)
    monkeypatch.setattr(assistant, "_warmup_completed_at", None)
    monkeypatch.setattr(assistant, "_assistant_last_error", None)
    monkeypatch.setattr(assistant, "_assistant_last_request_error", None)
    monkeypatch.setattr(assistant, "_assistant_last_request_error_at", None)


def test_status_reports_missing_gigachat_key(monkeypatch, tmp_path):
    _reset_assistant_state(monkeypatch, tmp_path, credentials=None)

    status = assistant.get_assistant_status()

    assert status.ready is False
    assert status.status == "configuration_error"
    assert "отсутствует ключ GigaChat" in status.assistant_message


def test_status_reports_missing_index(monkeypatch, tmp_path):
    _reset_assistant_state(monkeypatch, tmp_path)

    status = assistant.get_assistant_status()

    assert status.ready is False
    assert status.status == "knowledge_base_not_ready"
    assert status.knowledge_base_status == "index_missing"
    assert "индекс не найден" in status.assistant_message


def test_status_reports_empty_knowledge_base(monkeypatch, tmp_path):
    class Collection:
        def count(self) -> int:
            return 0

    class Vectorstore:
        _collection = Collection()

    _reset_assistant_state(monkeypatch, tmp_path)
    monkeypatch.setattr(assistant, "_vectorstore", Vectorstore())

    status = assistant.get_assistant_status()

    assert status.ready is False
    assert status.status == "knowledge_base_not_ready"
    assert status.knowledge_base_status == "empty"
    assert "индекс пуст" in status.assistant_message


def test_rag_stream_fails_fast_when_index_is_not_loaded():
    rag = RAGSystem.__new__(RAGSystem)
    rag._vectorstore = None
    rag._base_retriever = None

    with pytest.raises(KnowledgeBaseNotReadyError, match="индекс не загружен"):
        next(rag.ask_stream("Что есть в базе знаний?"))

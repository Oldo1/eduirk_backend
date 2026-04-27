"""
Тесты для CrossEncoderReranker из rag_pipeline.

CrossEncoder мокается, чтобы не грузить 400-МБ модель для unit-тестов.
Запуск:
    python -m unittest test_reranker.py -v
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from langchain_core.documents import Document

# Мокаем CrossEncoder ДО импорта rag_pipeline, чтобы sentence_transformers
# не попытался скачать/загрузить модель при инициализации теста
with patch("sentence_transformers.CrossEncoder"):
    from rag_pipeline import CrossEncoderReranker


# ─────────────────────────────────────────────────────────────────────────────
#  Хелперы
# ─────────────────────────────────────────────────────────────────────────────

def make_doc(content: str, **meta) -> Document:
    return Document(page_content=content, metadata=meta)


def make_reranker(
    top_k: int = 5,
    threshold: float = 0.0,
    scores: list[float] | None = None,
) -> CrossEncoderReranker:
    """Reranker с замокным CrossEncoder.predict, возвращающим заданные scores."""
    with patch("rag_pipeline.CrossEncoder") as mock_ce:
        mock_model = MagicMock()
        mock_model.predict.side_effect = (
            lambda pairs: (scores or [0.5] * len(pairs))[: len(pairs)]
        )
        mock_ce.return_value = mock_model
        # Подавляем принты инициализации
        with redirect_stdout(io.StringIO()):
            r = CrossEncoderReranker(model_name="test", top_k=top_k, threshold=threshold)
        r._mock_model = mock_model  # type: ignore[attr-defined]
        return r


def rerank_silent(r: CrossEncoderReranker, query: str, docs: list[Document]):
    """Вызов rerank с подавлением stdout (чтобы вывод тестов был чистым)."""
    with redirect_stdout(io.StringIO()):
        return r.rerank(query, docs)


def rerank_with_stdout(r: CrossEncoderReranker, query: str, docs: list[Document]):
    """Вызов rerank + возврат stdout, чтобы проверить логи."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = r.rerank(query, docs)
    return result, buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
#  _normalize
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalize(unittest.TestCase):
    def test_replaces_underscores(self):
        self.assertEqual(
            CrossEncoderReranker._normalize("О_проведении_мероприятия"),
            "о проведении мероприятия",
        )

    def test_lowercases(self):
        self.assertEqual(CrossEncoderReranker._normalize("HELLO World"), "hello world")

    def test_collapses_whitespace(self):
        self.assertEqual(
            CrossEncoderReranker._normalize("  hello   world  "),
            "hello world",
        )

    def test_tabs_and_newlines_collapsed(self):
        self.assertEqual(CrossEncoderReranker._normalize("a\tb\nc"), "a b c")

    def test_empty_string(self):
        self.assertEqual(CrossEncoderReranker._normalize(""), "")

    def test_only_whitespace(self):
        self.assertEqual(CrossEncoderReranker._normalize("   \n\t  "), "")


# ─────────────────────────────────────────────────────────────────────────────
#  _enrich_payload
# ─────────────────────────────────────────────────────────────────────────────

class TestEnrichPayload(unittest.TestCase):
    def test_with_title_and_breadcrumb(self):
        doc = make_doc("content", title="Doc Title", breadcrumb="Cat > Sub")
        self.assertEqual(
            CrossEncoderReranker._enrich_payload(doc),
            "Doc Title | Cat > Sub\ncontent",
        )

    def test_only_title(self):
        doc = make_doc("content", title="Doc Title")
        self.assertEqual(
            CrossEncoderReranker._enrich_payload(doc),
            "Doc Title\ncontent",
        )

    def test_only_breadcrumb(self):
        doc = make_doc("content", breadcrumb="Home > Docs")
        self.assertEqual(
            CrossEncoderReranker._enrich_payload(doc),
            "Home > Docs\ncontent",
        )

    def test_no_metadata(self):
        doc = make_doc("content")
        self.assertEqual(CrossEncoderReranker._enrich_payload(doc), "content")

    def test_empty_title_ignored(self):
        doc = make_doc("content", title="", breadcrumb="")
        self.assertEqual(CrossEncoderReranker._enrich_payload(doc), "content")

    def test_title_whitespace_stripped(self):
        doc = make_doc("content", title="  Title  ")
        self.assertEqual(
            CrossEncoderReranker._enrich_payload(doc),
            "Title\ncontent",
        )


# ─────────────────────────────────────────────────────────────────────────────
#  _compute_boost
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeBoost(unittest.TestCase):
    def setUp(self):
        self.r = make_reranker()

    def test_header_match(self):
        doc = make_doc(
            "x",
            s3_key="О_проведении_мероприятия_ДДТ.pdf",
            is_header=True,
        )
        q = CrossEncoderReranker._normalize(
            "расскажи о о проведении мероприятия ддт"
        )
        self.assertEqual(
            self.r._compute_boost(q, doc),
            CrossEncoderReranker.HEADER_BOOST,
        )

    def test_filename_match_non_header(self):
        doc = make_doc("x", s3_key="О_проведении_мероприятия_ДДТ.pdf")
        q = CrossEncoderReranker._normalize("О_проведении_мероприятия_ДДТ")
        self.assertEqual(
            self.r._compute_boost(q, doc),
            CrossEncoderReranker.FILENAME_MATCH_BOOST,
        )

    def test_title_match(self):
        doc = make_doc("x", title="Новости науки и образования")
        q = CrossEncoderReranker._normalize(
            "расскажи про новости науки и образования подробнее"
        )
        self.assertEqual(
            self.r._compute_boost(q, doc),
            CrossEncoderReranker.TITLE_MATCH_BOOST,
        )

    def test_no_match(self):
        doc = make_doc("x", s3_key="отчёт_финансовый.pdf", title="Отчёт")
        q = CrossEncoderReranker._normalize("какой сегодня день недели")
        self.assertEqual(self.r._compute_boost(q, doc), 0.0)

    def test_short_title_not_matched(self):
        """Короткие title (≤5 символов) не участвуют — слишком шумно."""
        doc = make_doc("x", title="Home")
        q = CrossEncoderReranker._normalize("home page visit")
        self.assertEqual(self.r._compute_boost(q, doc), 0.0)

    def test_empty_s3_key_falls_back_to_title(self):
        doc = make_doc("x", s3_key="", title="Важный документ про наставничество")
        q = CrossEncoderReranker._normalize(
            "расскажи про важный документ про наставничество"
        )
        self.assertEqual(
            self.r._compute_boost(q, doc),
            CrossEncoderReranker.TITLE_MATCH_BOOST,
        )

    def test_s3_key_takes_priority_over_title(self):
        """Если s3_key совпал — используем его boost, не уходим в title."""
        doc = make_doc(
            "x",
            s3_key="Положение_о_конкурсе.pdf",
            title="Положение о конкурсе",
        )
        q = CrossEncoderReranker._normalize("положение о конкурсе")
        boost = self.r._compute_boost(q, doc)
        self.assertEqual(boost, CrossEncoderReranker.FILENAME_MATCH_BOOST)


# ─────────────────────────────────────────────────────────────────────────────
#  rerank — основная логика
# ─────────────────────────────────────────────────────────────────────────────

class TestRerankCore(unittest.TestCase):
    def test_empty_input(self):
        r = make_reranker()
        self.assertEqual(r.rerank("q", []), [])

    def test_sorting_by_score(self):
        r = make_reranker(top_k=3, scores=[0.2, 0.9, 0.5])
        docs = [make_doc(f"c{i}", source=f"u{i}") for i in range(3)]
        result = rerank_silent(r, "q", docs)
        self.assertEqual([d.page_content for d in result], ["c1", "c2", "c0"])

    def test_diversification_per_source(self):
        """3 чанка с одним source → только 2 в итоге (MAX_PER_SOURCE=2)."""
        r = make_reranker(top_k=5, scores=[0.9, 0.8, 0.7, 0.6])
        docs = [
            make_doc("c0", source="same"),
            make_doc("c1", source="same"),
            make_doc("c2", source="same"),  # должен быть отброшен
            make_doc("c3", source="other"),
        ]
        result = rerank_silent(r, "q", docs)
        self.assertEqual([d.page_content for d in result], ["c0", "c1", "c3"])

    def test_diversification_by_s3_key(self):
        """Диверсификация предпочитает s3_key > page_url > source."""
        r = make_reranker(top_k=5, scores=[0.9, 0.8, 0.7])
        docs = [
            make_doc("c0", s3_key="doc.pdf", source="diff_source_1"),
            make_doc("c1", s3_key="doc.pdf", source="diff_source_2"),
            make_doc("c2", s3_key="doc.pdf", source="diff_source_3"),
        ]
        result = rerank_silent(r, "q", docs)
        self.assertEqual(len(result), 2)
        self.assertEqual([d.page_content for d in result], ["c0", "c1"])

    def test_threshold_filter(self):
        r = make_reranker(top_k=5, threshold=0.2, scores=[0.1, 0.5, 0.05])
        docs = [make_doc(f"c{i}", source=f"u{i}") for i in range(3)]
        result = rerank_silent(r, "q", docs)
        self.assertEqual([d.page_content for d in result], ["c1"])

    def test_empty_after_threshold_returns_best(self):
        """Если все ниже порога — возвращаем хотя бы лучший."""
        r = make_reranker(top_k=5, threshold=0.5, scores=[0.01, 0.02, 0.03])
        docs = [make_doc(f"c{i}", source=f"u{i}") for i in range(3)]
        result = rerank_silent(r, "q", docs)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].page_content, "c2")

    def test_top_k_limit(self):
        r = make_reranker(top_k=2, scores=[0.9, 0.8, 0.7, 0.6])
        docs = [make_doc(f"c{i}", source=f"u{i}") for i in range(4)]
        result = rerank_silent(r, "q", docs)
        self.assertEqual(len(result), 2)

    def test_header_boost_promotes_match(self):
        """Header-чанк с совпадением имени обходит сильного конкурента."""
        # header base=0.1, после boost=+1.0 → 1.1; competitor base=0.9
        r = make_reranker(top_k=2, scores=[0.1, 0.9])
        docs = [
            make_doc(
                "header content",
                s3_key="important_doc.pdf",
                is_header=True,
                source="s3://important",
            ),
            make_doc("competitor content", source="other"),
        ]
        result = rerank_silent(r, "q", docs)
        q_norm = CrossEncoderReranker._normalize("важен important doc")
        # Убеждаемся, что boost сработает для header
        self.assertGreater(
            r._compute_boost(q_norm, docs[0]),
            0.0,
        )
        result = rerank_silent(r, "important doc pdf что там", docs)
        self.assertEqual(result[0].page_content, "header content")

    def test_enriched_payload_passed_to_model(self):
        r = make_reranker(top_k=1, scores=[0.5])
        doc = make_doc("body", title="My Doc")
        rerank_silent(r, "q", [doc])
        call_args = r._mock_model.predict.call_args[0][0]  # type: ignore[attr-defined]
        self.assertEqual(call_args[0], ("q", "My Doc\nbody"))


# ─────────────────────────────────────────────────────────────────────────────
#  Логирование
# ─────────────────────────────────────────────────────────────────────────────

class TestLogging(unittest.TestCase):
    def test_low_quality_warning(self):
        r = make_reranker(top_k=3, scores=[0.05, 0.02, 0.01])
        docs = [make_doc(f"c{i}", source=f"u{i}") for i in range(3)]
        _, out = rerank_with_stdout(r, "q", docs)
        self.assertIn("Низкое качество", out)

    def test_no_low_quality_warning_when_high_score(self):
        r = make_reranker(top_k=3, scores=[0.9, 0.5, 0.3])
        docs = [make_doc(f"c{i}", source=f"u{i}") for i in range(3)]
        _, out = rerank_with_stdout(r, "q", docs)
        self.assertNotIn("Низкое качество", out)

    def test_header_tag_shown(self):
        r = make_reranker(top_k=2, scores=[0.8, 0.3])
        docs = [
            make_doc("c0", source="u0", is_header=True),
            make_doc("c1", source="u1"),
        ]
        _, out = rerank_with_stdout(r, "q", docs)
        self.assertIn("[HDR]", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""
rag_pipeline.py — ядро RAG-системы с reranker'ом и памятью диалога

Схема работы:
    Вопрос пользователя
          │
          ▼
    ConversationMemory       ← история диалога (последние N обменов)
    _rewrite_question()      ← GigaChat делает вопрос самодостаточным
          │
          ▼
    Chroma MMR-поиск         → fetch_k кандидатов
          │
          ▼
    CrossEncoder reranker    → top_k лучших чанков
          │
          ▼
    GigaChat SDK (напрямую)  → ответ с учётом истории  ← исправляет ASCII-баг
          │
          ▼
    ConversationMemory.save()

Установка:
    pip install gigachat langchain langchain-chroma langchain-huggingface
                sentence-transformers chromadb
"""

from __future__ import annotations

import os
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from sentence_transformers import CrossEncoder

# GigaChat SDK — используем напрямую, минуя LangChain-обёртку
# (LangChain-обёртка ломает кириллицу при invoke с message-списком)
from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole


# ─────────────────────────────────────────────────────────────────────────────
#  Конфигурация
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RAGConfig:
    # ── GigaChat ──────────────────────────────────────────────────────────────
    credentials: str = field(
        default_factory=lambda: os.environ.get("GIGACHAT_CREDENTIALS", "")
    )
    scope: str             = "GIGACHAT_API_PERS"   # GIGACHAT_API_CORP — для юрлица
    model: str             = "GigaChat-Pro"        # GigaChat | GigaChat-Pro | GigaChat-Max
    verify_ssl_certs: bool = False                 # True — если установлен сертификат Минцифры
    max_tokens: int        = 1000
    temperature: float     = 0.2

    # ── Векторная база ────────────────────────────────────────────────────────
    persist_dir: str     = "./chroma_gigachat"
    collection_name: str = "eduirk"

    # ── Чанкинг ───────────────────────────────────────────────────────────────
    chunk_size: int    = 300
    chunk_overlap: int = 50

    # ── Поиск + reranker ──────────────────────────────────────────────────────
    fetch_k: int = 30   # кандидатов из Chroma
    top_k: int   = 5    # финальных чанков после rerank

    # ── Reranker ──────────────────────────────────────────────────────────────
    # DiTy — кросс-энкодер, обученный на русском MS MARCO.
    # Предыдущий cross-encoder/msmarco-MiniLM-L6-en-de-v1 — только EN/DE,
    # на русских запросах давал почти случайные оценки.
    reranker_model:     str   = "DiTy/cross-encoder-russian-msmarco"
    reranker_threshold: float = 0.0   # чанки со скором ниже этого значения отбрасываются

    # ── Память ────────────────────────────────────────────────────────────────
    memory_turns: int = 5   # сколько последних обменов помнить


# ─────────────────────────────────────────────────────────────────────────────
#  Память диалога
# ─────────────────────────────────────────────────────────────────────────────

class ConversationMemory:
    def __init__(self, max_turns: int = 5):
        self._max_turns = max_turns
        self._history: deque[dict] = deque(maxlen=max_turns)

    def save(self, question: str, answer: str) -> None:
        self._history.append({"question": question, "answer": answer})

    def clear(self) -> None:
        self._history.clear()

    def is_empty(self) -> bool:
        return len(self._history) == 0

    def as_sdk_messages(self) -> list[Messages]:
        """История в формате GigaChat SDK Messages."""
        msgs = []
        for turn in self._history:
            msgs.append(Messages(role=MessagesRole.USER,      content=turn["question"]))
            msgs.append(Messages(role=MessagesRole.ASSISTANT, content=turn["answer"]))
        return msgs

    def as_text(self) -> str:
        if self.is_empty():
            return ""
        lines = []
        for turn in self._history:
            lines.append(f"Пользователь: {turn['question']}")
            lines.append(f"Ассистент: {turn['answer']}")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._history)


# ─────────────────────────────────────────────────────────────────────────────
#  CrossEncoder Reranker
# ─────────────────────────────────────────────────────────────────────────────

class CrossEncoderReranker:
    """
    CrossEncoder-ранжировщик с 4 улучшениями поверх базового rerank:

    1. Title/breadcrumb добавляется в payload для CrossEncoder — модель видит
       имя документа, а не только голый контент чанка. Резко помогает
       вопросам, явно упоминающим имя файла.
    2. Header-чанки (is_header=True) получают boost, когда в запросе
       встречается имя документа (stem s3_key или title). Так короткие
       «документ-визитки» стабильно попадают в top при вопросах «расскажи
       про X / какой email в X / какая дата X».
    3. Диверсификация по source — не больше MAX_PER_SOURCE чанков из одного
       документа. Убирает дубли, которые забивали контекст.
    4. Сигнал низкого качества: если max(scores) очень мал — логируем warning,
       чтобы было заметно «ничего релевантного не найдено».
    """

    # Не больше N чанков из одного документа в финальном top_k
    MAX_PER_SOURCE: int = 2
    # Порог «низкого качества» для warning-сигнала
    LOW_QUALITY_THRESHOLD: float = 0.1
    # Бусты при совпадении имени документа в запросе
    HEADER_BOOST:        float = 1.0   # header-чанк + совпадение имени
    FILENAME_MATCH_BOOST: float = 0.3   # обычный чанк + совпадение имени
    TITLE_MATCH_BOOST:    float = 0.2   # совпадение title (для не-S3 страниц)

    def __init__(self, model_name: str, top_k: int, threshold: float = 0.0):
        print(f"[reranker] Загрузка модели: {model_name}")
        self._model     = CrossEncoder(model_name, max_length=512)
        self._top_k     = top_k
        self._threshold = threshold
        print(f"[reranker] Готово. top-{top_k}, порог скора: {threshold}")

    @staticmethod
    def _enrich_payload(doc: Document) -> str:
        """Склеивает title + breadcrumb + content для подачи в CrossEncoder.
        Так модель видит, из какого документа чанк, а не только голый текст."""
        title      = (doc.metadata.get("title")      or "").strip()
        breadcrumb = (doc.metadata.get("breadcrumb") or "").strip()

        header_parts = []
        if title:
            header_parts.append(title)
        if breadcrumb:
            header_parts.append(breadcrumb)

        if header_parts:
            return f"{' | '.join(header_parts)}\n{doc.page_content}"
        return doc.page_content

    @staticmethod
    def _normalize(s: str) -> str:
        """Нормализует строку для сравнения: lower, подчёркивания→пробелы,
        схлопывание пробелов. Помогает матчить «О_проведении_мероприятия» и
        «о проведении мероприятия»."""
        s = s.lower().replace("_", " ")
        return re.sub(r"\s+", " ", s).strip()

    def _compute_boost(self, query_norm: str, doc: Document) -> float:
        """Добавка к скору за совпадение имени документа/title в запросе."""
        s3_key = doc.metadata.get("s3_key") or ""
        title  = doc.metadata.get("title")  or ""

        if s3_key:
            stem_norm = self._normalize(Path(s3_key).stem)
            if stem_norm and stem_norm in query_norm:
                return self.HEADER_BOOST if doc.metadata.get("is_header") else self.FILENAME_MATCH_BOOST

        if title and len(title) > 5:
            title_norm = self._normalize(title)
            if title_norm in query_norm:
                return self.TITLE_MATCH_BOOST

        return 0.0

    def rerank(self, query: str, docs: list[Document]) -> list[Document]:
        if not docs:
            return docs

        # 1. CrossEncoder-скор с обогащённым payload
        pairs       = [(query, self._enrich_payload(doc)) for doc in docs]
        base_scores = [float(s) for s in self._model.predict(pairs)]

        # 2. Бусты за совпадение имени документа в запросе
        query_norm = self._normalize(query)
        boosted_scores = [
            base + self._compute_boost(query_norm, doc)
            for base, doc in zip(base_scores, docs)
        ]

        scored = sorted(zip(boosted_scores, docs), key=lambda x: x[0], reverse=True)

        # 3. Диверсификация: не больше MAX_PER_SOURCE чанков из одного source
        per_source: dict[str, int] = {}
        diversified: list[tuple[float, Document]] = []
        for score, doc in scored:
            key = (
                doc.metadata.get("s3_key")
                or doc.metadata.get("page_url")
                or doc.metadata.get("source", "")
            )
            if per_source.get(key, 0) >= self.MAX_PER_SOURCE:
                continue
            diversified.append((score, doc))
            per_source[key] = per_source.get(key, 0) + 1

        # 4. Фильтр по порогу (с защитой от пустого результата)
        filtered = [(s, d) for s, d in diversified if s >= self._threshold]
        if not filtered:
            filtered = diversified[:1]

        top_docs = [doc for _, doc in filtered[: self._top_k]]

        # Сигнал низкого качества ретривала
        max_score = scored[0][0] if scored else 0.0
        if max_score < self.LOW_QUALITY_THRESHOLD:
            print(
                f"[reranker] ⚠ Низкое качество (max score={max_score:.3f}) — "
                "возможно, в базе нет релевантного контента"
            )

        # Лог: показываем и base, и итоговый скор (если был буст)
        print("[reranker] Топ результаты:")
        shown = diversified[: self._top_k] if diversified else scored[: self._top_k]
        for score, doc in shown:
            mark  = "✓" if score >= self._threshold else "✗"
            title = (doc.metadata.get("title") or "")[:45]
            idx   = docs.index(doc)
            base  = base_scores[idx]
            delta = score - base
            extra = f"  (base {base:+.3f} +{delta:.2f})" if abs(delta) > 1e-6 else ""
            tag   = " [HDR]" if doc.metadata.get("is_header") else ""
            print(f"  {mark} {score:+.3f}  {title}{tag}{extra}")

        return top_docs


# ─────────────────────────────────────────────────────────────────────────────
#  RAGSystem
# ─────────────────────────────────────────────────────────────────────────────

class RAGSystem:
    def __init__(self, cfg: RAGConfig):
        self.cfg    = cfg
        self.memory = ConversationMemory(max_turns=cfg.memory_turns)

        self._vectorstore:   Optional[Chroma]               = None
        self._reranker:      Optional[CrossEncoderReranker] = None
        self._base_retriever = None

        # Валидация и очистка ключа
        self._gc_kwargs = dict(
            credentials=self._validate_credentials(cfg.credentials),
            scope=cfg.scope,
            model=cfg.model,
            verify_ssl_certs=cfg.verify_ssl_certs,
        )

    # ── Валидация ключа ───────────────────────────────────────────────────────

    @staticmethod
    def _validate_credentials(raw: str) -> str:
        """
        Проверяет ключ GigaChat и возвращает очищенную строку.
        Выбрасывает понятную ошибку если ключ некорректный.
        """
        import base64

        # Убираем пробелы, переносы строк, BOM и невидимые символы
        creds = raw.strip().strip('\ufeff').replace('\n', '').replace('\r', '').replace(' ', '')

        if not creds:
            raise ValueError(
                "\n[ключ] GIGACHAT_CREDENTIALS пустой!\n"
                "  Укажите его в cfg = RAGConfig(credentials='...')\n"
                "  Где взять: developers.sber.ru/studio → Ключи и токены → Авторизационные данные"
            )

        # Проверяем на не-ASCII символы — частая причина ошибки
        try:
            creds.encode('ascii')
        except UnicodeEncodeError as e:
            bad_pos   = e.start
            bad_char  = creds[bad_pos]
            bad_ord   = ord(bad_char)
            snippet   = creds[max(0, bad_pos-3) : bad_pos+4]
            raise ValueError(
                f"\n[ключ] В ключе обнаружен не-ASCII символ!\n"
                f"  Позиция {bad_pos}: символ '{bad_char}' (код U+{bad_ord:04X})\n"
                f"  Фрагмент вокруг: «{snippet}»\n\n"
                f"  Возможные причины:\n"
                f"  1. При копировании захватили лишний текст с кириллицей\n"
                f"  2. Скопировали Client Secret вместо Authorization Data\n"
                f"  3. Ключ скопирован с переносом строки внутри\n\n"
                f"  Где взять правильный ключ:\n"
                f"  developers.sber.ru/studio → ваш проект\n"
                f"  → вкладка «Ключи и токены»\n"
                f"  → кнопка «Сгенерировать новый Client Secret»\n"
                f"  → копируйте поле «Авторизационные данные» (длинная строка на ==)\n"
                f"  Правильный ключ содержит ТОЛЬКО латиницу, цифры, +, /, ="
            ) from e

        # Проверяем что это валидный base64
        try:
            decoded = base64.b64decode(creds + '==')  # padding для надёжности
            if len(decoded) < 20:
                raise ValueError("Слишком короткий")
        except Exception:
            raise ValueError(
                f"\n[ключ] Ключ не является валидным base64!\n"
                f"  Текущее значение ({len(creds)} символов): {creds[:30]}...\n\n"
                f"  Правильный ключ выглядит примерно так:\n"
                f"  MDE5YTRkNDct...YTYxOA==\n"
                f"  (длинная строка ~88 символов, заканчивается на '==')\n\n"
                f"  Где взять: developers.sber.ru/studio\n"
                f"  → Ключи и токены → Авторизационные данные"
            )

        return creds

    # ── Загрузка индекса с диска ──────────────────────────────────────────────

    def load_index(self, embeddings) -> None:
        self._vectorstore = Chroma(
            collection_name=self.cfg.collection_name,
            persist_directory=self.cfg.persist_dir,
            embedding_function=embeddings,
        )
        self._setup()
        count = self._vectorstore._collection.count()
        print(f"[rag] Индекс загружен. Векторов в базе: {count}")

    def set_vectorstore(self, vectorstore: Chroma) -> None:
        self._vectorstore = vectorstore
        self._setup()

    def _setup(self) -> None:
        self._reranker = CrossEncoderReranker(
            model_name=self.cfg.reranker_model,
            top_k=self.cfg.top_k,
            threshold=self.cfg.reranker_threshold,
        )
        self._base_retriever = self._vectorstore.as_retriever(
            search_type="mmr",
            search_kwargs={
                "k":           self.cfg.fetch_k,
                "fetch_k":     self.cfg.fetch_k * 2,
                "lambda_mult": 0.7,
            },
        )

    # ── Вызов GigaChat SDK (решает ASCII-баг LangChain-обёртки) ──────────────

    def _call_gigachat(self, messages: list[Messages]) -> str:
        """
        Прямой вызов GigaChat SDK.
        Избегает бага LangChain-обёртки с кириллицей в invoke(messages).
        """
        payload = Chat(
            messages=messages,
            max_tokens=self.cfg.max_tokens,
            temperature=self.cfg.temperature,
        )
        with GigaChat(**self._gc_kwargs) as client:
            response = client.chat(payload)
        return response.choices[0].message.content

    # ── Шаг 1: перефразировать вопрос с учётом истории ───────────────────────

    def _rewrite_question(self, question: str) -> str:
        if self.memory.is_empty():
            return question

        # Быстрая эвристика: если в вопросе нет явных анафорических маркеров —
        # не тратим токены на LLM-вызов, возвращаем вопрос как есть
        ANAPHORA_MARKERS = [
            "он ", "она ", "они ", "оно ", "его ", "её ", "их ",
            "им ", "ему ", "ней ", "них ", "ним ",
            "этот ", "эта ", "это ", "эти ",
            "тот ", "та ", "те ", "том ",
            "там ", "тогда ", "тут ", "здесь",
            "про него", "про неё", "про них", "про это",
            "о нём", "о ней", "о них",
            "подробнее", "ещё про", "а ещё", "а как насчёт",
            "расскажи ещё", "что ещё",
            # Ссылки на ранее упомянутый документ/файл/положение
            "в документе", "из документа", "документа?", "документе?",
            "в файле", "из файла", "файла?",
            "в положении", "положения?",
            "в тексте", "из текста",
            "в нём", "в ней", "в них",
        ]
        q_lower = question.lower()
        has_anaphora = any(marker in q_lower for marker in ANAPHORA_MARKERS)

        if not has_anaphora:
            return question   # вопрос самодостаточен — не трогаем

        messages = [
            Messages(
                role=MessagesRole.SYSTEM,
                content=(
                    "Пользователь задал вопрос, который содержит местоимение или ссылку "
                    "на предыдущее сообщение. Подставь из истории то, на что указывает "
                    "местоимение, и верни переформулированный вопрос.\n"
                    "Верни ТОЛЬКО итоговый вопрос — без пояснений, без кавычек."
                ),
            ),
            Messages(
                role=MessagesRole.USER,
                content=(
                    f"История диалога:\n{self.memory.as_text()}\n\n"
                    f"Вопрос с местоимением: {question}\n\n"
                    "Переформулированный вопрос:"
                ),
            ),
        ]

        rewritten = self._call_gigachat(messages).strip()

        if rewritten and rewritten != question:
            print(f"[memory] Вопрос переформулирован: «{rewritten}»")

        return rewritten or question

    # ── Шаг 2: поиск + rerank ────────────────────────────────────────────────

    def _retrieve_and_rerank(self, query: str) -> list[Document]:
        candidates = self._base_retriever.invoke(query)
        return self._reranker.rerank(query, candidates)

    # ── Шаг 3: форматировать чанки ───────────────────────────────────────────

    @staticmethod
    def _format_docs(docs: list[Document]) -> str:
        parts = []
        for doc in docs:
            src        = doc.metadata.get("source", "")
            title      = doc.metadata.get("title", "")
            breadcrumb = doc.metadata.get("breadcrumb", "")
            # Для навигационных чанков реальный URL живёт в page_url,
            # а в source хранится псевдо-ключ __nav__.
            if src.startswith("__"):
                src = doc.metadata.get("page_url", "")

            header_parts = []
            if title:
                header_parts.append(title)
            if src:
                header_parts.append(f"URL: {src}")
            if breadcrumb:
                header_parts.append(f"Путь: {breadcrumb}")
            header = f"[{' | '.join(header_parts)}]" if header_parts else ""
            parts.append(f"{header}\n{doc.page_content}".strip())
        return "\n\n---\n\n".join(parts)

    # ── Шаг 4: генерировать ответ с историей ─────────────────────────────────

    def _generate_answer(self, question: str, context: str) -> str:
        system_content = (
            "Ты — помощник МКУ «ИМЦРО» (Муниципальное казённое учреждение "
            "развития образования города Иркутска).\n\n"
            "Отвечай ТОЛЬКО на основе предоставленного текста. Не придумывай факты.\n"
            "Если ответа в тексте нет — честно скажи об этом и предложи позвонить в учреждение.\n"
            "Учитывай историю диалога — не повторяй то, что уже было сказано.\n"
            "Отвечай на русском языке, кратко и по делу.\n"
            "Если есть даты, телефоны, ссылки — обязательно укажи их.\n"
            "Если пользователь спрашивает как найти или перейти на страницу — "
            "укажи прямую ссылку (URL) из текста базы знаний и опиши путь навигации "
            "(хлебные крошки), если они есть.\n\n"
            f"ТЕКСТ ИЗ БАЗЫ ЗНАНИЙ:\n{context}"
        )

        messages = (
            [Messages(role=MessagesRole.SYSTEM, content=system_content)]
            + self.memory.as_sdk_messages()
            + [Messages(role=MessagesRole.USER, content=question)]
        )

        return self._call_gigachat(messages)

    # ── Основной метод ────────────────────────────────────────────────────────

    def ask(self, question: str) -> dict:
        """
        Возвращает:
            {
                "answer":             str,
                "rewritten_question": str,
                "sources":            [{"title": str, "source": str}, ...]
            }
        """
        if self._base_retriever is None:
            raise RuntimeError("Сначала вызовите load_index() или set_vectorstore().")

        rewritten = self._rewrite_question(question)
        top_docs  = self._retrieve_and_rerank(rewritten)

        print(f"[rag] Запрос: {rewritten!r}")
        print(f"[rag] В контекст попали {len(top_docs)} чанков:")
        for i, doc in enumerate(top_docs, 1):
            src   = doc.metadata.get("source", "")[:90]
            title = doc.metadata.get("title", "")[:60]
            print(f"  {i}. [{title}] {src}")

        context   = self._format_docs(top_docs)
        answer    = self._generate_answer(rewritten, context)

        self.memory.save(question, answer)

        seen, sources = set(), []
        for doc in top_docs:
            url = doc.metadata.get("source", "")
            if url.startswith("__"):
                url = doc.metadata.get("page_url", "")
            if url and url not in seen:
                seen.add(url)
                sources.append({
                    "title":  doc.metadata.get("title", ""),
                    "source": url,
                })

        return {
            "answer":             answer,
            "rewritten_question": rewritten,
            "sources":            sources,
        }

    # ── Утилиты ───────────────────────────────────────────────────────────────

    def clear_memory(self) -> None:
        self.memory.clear()
        print("[memory] История диалога очищена.")

    def print_history(self) -> None:
        if self.memory.is_empty():
            print("[memory] История пуста.")
            return
        print(f"[memory] История ({len(self.memory)} обменов):")
        print(self.memory.as_text())
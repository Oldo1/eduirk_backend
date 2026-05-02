"""
updater.py — оркестратор инкрементального обновления RAG-индекса

Координирует работу модулей:
  site_crawler  — краулинг HTML-страниц сайта
  s3_loader     — листинг и скачивание документов из Yandex Cloud
  doc_extractor — извлечение текста из PDF/DOCX/DOC
  update_state  — хранение состояния (url/key → hash/etag)

Логика обновления:
  Сайт:  url → md5(text)  — добавляем новые, обновляем изменённые, удаляем исчезнувшие
  S3:    key → ETag       — добавляем только новые/изменившиеся (по ETag без скачивания)

Ручное обновление: POST /admin/update/run
Статус:            GET  /admin/update/status
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import (
    UPDATE_INTERVAL_HOURS,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    SITE_CACHE_FILE,
    YC_ENDPOINT,
    YC_BUCKET,
)
from update_state import UpdateState
from site_crawler import crawl as crawl_site
from s3_loader import list_documents as list_s3_documents
from s3_loader import download_file, public_url
from doc_extractor import extract_text

logger = logging.getLogger("updater")

SITE_CONTEXT_SOURCE = "__site_context__"
SITE_CONTEXT_MAX_CHARS = 3500
SITE_CONTEXT_PAGE_LIMIT = 12


# ─────────────────────────────────────────────────────────────────────────────
#  Чанкинг
# ─────────────────────────────────────────────────────────────────────────────

def _make_chunks(
    source:     str,
    title:      str,
    text:       str,
    extra_meta: Optional[dict] = None,
) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    meta = {"source": source, "title": title, **(extra_meta or {})}
    return splitter.split_documents([Document(page_content=text, metadata=meta)])


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _truncate_text(text: str, max_chars: int) -> str:
    text = _compact_text(text)
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars].rsplit(" ", 1)[0].rstrip()
    return f"{truncated}..."


def _first_meaningful_lines(text: str, limit: int = 3) -> str:
    lines: list[str] = []
    for raw_line in (text or "").splitlines():
        line = _compact_text(raw_line.lstrip("#•- ").strip())
        if len(line) < 20 or line in lines:
            continue
        lines.append(line)
        if len(lines) >= limit:
            break
    return " ".join(lines)


def _build_site_context(crawled: dict[str, dict]) -> str:
    """
    Делает короткую extractive-сводку по страницам сайта.
    Этот блок используется как приоритетный контекст при ответах по документам.
    """
    if not crawled:
        return ""

    lines = [
        "Краткий контекст сайта mc.eduirk.ru. "
        "Если сведения из документа расходятся с этим контекстом, "
        "приоритет имеет контекст сайта.",
    ]

    added = 0
    for url, page in crawled.items():
        title = _compact_text(page.get("title", "")) or "Страница сайта"
        breadcrumb = _compact_text(page.get("breadcrumb", ""))
        snippet = _first_meaningful_lines(page.get("text", ""), limit=3)
        if not snippet and not breadcrumb:
            continue

        parts = [f"- {title}"]
        if breadcrumb:
            parts.append(f"путь: {breadcrumb}")
        parts.append(f"URL: {url}")
        if snippet:
            parts.append(_truncate_text(snippet, 420))

        lines.append("; ".join(parts))
        added += 1
        if added >= SITE_CONTEXT_PAGE_LIMIT:
            break

    return _truncate_text("\n".join(lines), SITE_CONTEXT_MAX_CHARS)


def _site_context_from_pending(chunks: list[Document]) -> str:
    for chunk in chunks:
        if chunk.metadata.get("source") == SITE_CONTEXT_SOURCE:
            return chunk.page_content
    return ""


def _get_existing_site_context(vectorstore: Chroma) -> str:
    try:
        existing = vectorstore.get(
            where={"source": SITE_CONTEXT_SOURCE},
            include=["documents", "metadatas"],
        )
    except TypeError:
        existing = vectorstore.get(include=["documents", "metadatas"])
    except Exception as e:
        logger.warning(f"[site-context] Не удалось прочитать контекст сайта из Chroma: {e}")
        return ""

    documents = existing.get("documents") or []
    metadatas = existing.get("metadatas") or []
    for text, meta in zip(documents, metadatas):
        if (meta or {}).get("source") == SITE_CONTEXT_SOURCE:
            return text or ""
    return ""


def _site_context_meta(site_context: str) -> dict:
    if not site_context:
        return {}
    return {
        "site_context": site_context,
        "site_context_priority": "site",
    }


def _load_site_cache() -> Optional[dict[str, dict]]:
    cache_path = Path(SITE_CACHE_FILE)
    if not cache_path.exists():
        return None

    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[site-cache] Не удалось прочитать cache {cache_path}: {e}")
        return None

    pages = payload.get("pages") if isinstance(payload, dict) else None
    if not isinstance(pages, dict) or not pages:
        logger.warning(f"[site-cache] Cache {cache_path} пустой или повреждён")
        return None

    valid_pages: dict[str, dict] = {}
    for url, page in pages.items():
        if not isinstance(url, str) or not isinstance(page, dict):
            continue
        text = page.get("text", "")
        if not isinstance(text, str) or not text.strip():
            continue
        valid_pages[url] = {
            "title": str(page.get("title", "")),
            "text": text,
            "breadcrumb": str(page.get("breadcrumb", "")),
        }

    if not valid_pages:
        logger.warning(f"[site-cache] В cache {cache_path} нет валидных страниц")
        return None

    cached_at = payload.get("cached_at", "неизвестно")
    logger.info(f"[site-cache] Загружено {len(valid_pages)} страниц из cache ({cached_at})")
    return valid_pages


def _save_site_cache(crawled: dict[str, dict]) -> None:
    if not crawled:
        return

    cache_path = Path(SITE_CACHE_FILE)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "cached_at": datetime.now().isoformat(),
            "page_count": len(crawled),
            "pages": crawled,
        }
        tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(cache_path)
        logger.info(f"[site-cache] Сохранено {len(crawled)} страниц в {cache_path}")
    except Exception as e:
        logger.warning(f"[site-cache] Не удалось сохранить cache {cache_path}: {e}")


def _get_site_pages(use_cache: bool = False) -> tuple[dict[str, dict], bool]:
    if use_cache:
        cached = _load_site_cache()
        if cached:
            return cached, True
        logger.warning("[site-cache] Cache не найден, запускаю краулер")

    crawled = crawl_site()
    _save_site_cache(crawled)
    return crawled, False


# ─────────────────────────────────────────────────────────────────────────────
#  Вспомогательная: получить ID чанков по списку source-значений
# ─────────────────────────────────────────────────────────────────────────────

def _get_chunk_ids_by_source(
    vectorstore: Chroma,
    sources:     list[str],
) -> list[str]:
    """Возвращает ID чанков из Chroma у которых metadata.source входит в sources."""
    if not sources:
        return []
    sources_set = set(sources)
    existing    = vectorstore.get(include=["metadatas"])
    return [
        doc_id
        for doc_id, meta in zip(existing["ids"], existing["metadatas"])
        if meta.get("source") in sources_set
    ]


def _get_chunk_ids_by_s3_key(
    vectorstore: Chroma,
    keys:        list[str],
) -> list[str]:
    """Возвращает ID чанков из Chroma у которых metadata.s3_key входит в keys."""
    if not keys:
        return []
    keys_set = set(keys)
    existing = vectorstore.get(include=["metadatas"])
    return [
        doc_id
        for doc_id, meta in zip(existing["ids"], existing["metadatas"])
        if meta.get("s3_key") in keys_set
    ]


def _get_indexed_metadata_values(vectorstore: Chroma, key: str) -> set[str]:
    """Возвращает значения metadata[key], реально присутствующие в Chroma."""
    try:
        existing = vectorstore.get(include=["metadatas"])
    except Exception as e:
        logger.warning(f"[index] Не удалось прочитать metadata из Chroma: {e}")
        return set()

    values: set[str] = set()
    for meta in existing.get("metadatas", []):
        if not meta:
            continue
        value = meta.get(key)
        if value:
            values.add(value)
    return values


# ─────────────────────────────────────────────────────────────────────────────
#  Обновление из сайта
# ─────────────────────────────────────────────────────────────────────────────

def _update_from_site(
    vectorstore:   Chroma,
    state:         UpdateState,
    new_chunks:    list[Document],
    ids_to_delete: list[str],
    use_cache:     bool = False,
    progress_cb:   Optional[Callable] = None,
) -> dict:
    """
    Краулит сайт и наполняет new_chunks / ids_to_delete.
    Обновляет state. Возвращает статистику.
    """
    stats = {"added": 0, "updated": 0, "removed": 0, "unchanged": 0, "from_cache": False}

    stage = "site_cache" if use_cache else "site_crawl"
    detail = "Загружаю страницы сайта из cache…" if use_cache else "Краулинг сайта…"
    if progress_cb: progress_cb(stage, 0, 0, detail)
    crawled, from_cache = _get_site_pages(use_cache=use_cache)
    stats["from_cache"] = from_cache
    source_label = "cache" if from_cache else "краулера"
    if progress_cb:
        progress_cb(stage, len(crawled), len(crawled), f"Получено страниц из {source_label}: {len(crawled)}")

    new_pages:     list[tuple] = []
    changed_pages: list[tuple] = []
    removed_urls:  list[str]   = []
    indexed_page_urls = _get_indexed_metadata_values(vectorstore, "source")

    # Определяем что изменилось
    for url, page in crawled.items():
        text_hash = UpdateState.compute_hash(page["text"])
        old_hash  = state.get_page_hash(url)

        if old_hash is None:
            new_pages.append((url, page, text_hash))
        elif old_hash != text_hash or url not in indexed_page_urls:
            changed_pages.append((url, page, text_hash))
        else:
            stats["unchanged"] += 1

    # Удалённые страницы — есть в state, но отсутствуют в свежем краулинге
    crawled_urls = set(crawled.keys())
    for url in state.all_page_urls():
        if url not in crawled_urls:
            removed_urls.append(url)

    logger.info(
        f"[site] Новых: {len(new_pages)}, "
        f"изменённых: {len(changed_pages)}, "
        f"удалённых: {len(removed_urls)}, "
        f"без изменений: {stats['unchanged']}"
    )

    # Собираем ID чанков изменённых и удалённых страниц для удаления
    urls_to_remove = [u for u, _, _ in changed_pages] + removed_urls
    ids = _get_chunk_ids_by_source(vectorstore, urls_to_remove)
    ids_to_delete.extend(ids)

    # Также помечаем к удалению все навигационные чанки изменённых/удалённых
    # страниц (они живут в отдельном псевдо-источнике __nav__).
    nav_source = "__nav__"
    if changed_pages or removed_urls:
        changed_urls_set = {u for u, _, _ in changed_pages} | set(removed_urls)
        existing = vectorstore.get(include=["metadatas"])
        for doc_id, meta in zip(existing["ids"], existing["metadatas"]):
            if meta.get("source") == nav_source and meta.get("page_url") in changed_urls_set:
                ids_to_delete.append(doc_id)

    # Контекст сайта пересобирается при каждом site-update. Это отдельный
    # приоритетный документ, который RAG подмешивает к ответам по S3-файлам.
    site_context_ids = _get_chunk_ids_by_source(vectorstore, [SITE_CONTEXT_SOURCE])
    if site_context_ids:
        ids_to_delete.extend(site_context_ids)

    site_context = _build_site_context(crawled)
    if site_context:
        new_chunks.append(
            Document(
                page_content=site_context,
                metadata={
                    "source": SITE_CONTEXT_SOURCE,
                    "title": "Краткий контекст сайта",
                    "is_site_context": True,
                },
            )
        )
        stats["site_context_chars"] = len(site_context)
        logger.info(f"[site-context] Обновлён краткий контекст сайта ({len(site_context)} симв.)")

    # Новые чанки: чистый контент страницы (без навигационного префикса — он
    # одинаковый для всех страниц и засоряет эмбеддинги).
    for url, page, text_hash in new_pages + changed_pages:
        breadcrumb = page.get("breadcrumb", "")
        chunks = _make_chunks(
            url,
            page["title"],
            page["text"],
            extra_meta={"breadcrumb": breadcrumb},
        )
        new_chunks.extend(chunks)
        state.set_page_hash(url, text_hash)

        # Отдельный навигационный мини-чанк для этой страницы.
        # Короткий, уникальный, прекрасно находится семантически по
        # запросам вида «как попасть на страницу X» / «где найти Y».
        nav_lines = [f"Страница сайта: «{page['title']}»"]
        if breadcrumb:
            nav_lines.append(f"Путь навигации: {breadcrumb}")
        nav_lines.append(f"Прямая ссылка: {url}")
        nav_text = "\n".join(nav_lines)

        nav_doc = Document(
            page_content=nav_text,
            metadata={
                "source":    nav_source,
                "title":     page["title"],
                "page_url":  url,
                "breadcrumb": breadcrumb,
            },
        )
        new_chunks.append(nav_doc)

    for url in removed_urls:
        state.remove_page(url)

    # Удаляем старый большой sitemap (если остался с предыдущих версий) —
    # его заменили per-page навигационные чанки.
    old_sitemap_ids = _get_chunk_ids_by_source(vectorstore, ["__sitemap__"])
    if old_sitemap_ids:
        ids_to_delete.extend(old_sitemap_ids)

    logger.info(
        f"[site] Добавлено навигационных чанков: "
        f"{len(new_pages) + len(changed_pages)}"
    )

    stats["added"]   = len(new_pages)
    stats["updated"] = len(changed_pages)
    stats["removed"] = len(removed_urls)
    return stats


# ─────────────────────────────────────────────────────────────────────────────
#  Обновление из Yandex Cloud S3
# ─────────────────────────────────────────────────────────────────────────────

def _update_from_s3(
    vectorstore:   Chroma,
    state:         UpdateState,
    new_chunks:    list[Document],
    ids_to_delete: list[str],
    site_context:  str = "",
    progress_cb:   Optional[Callable] = None,
) -> dict:
    """
    Проверяет S3 на новые/изменённые файлы по ETag.
    Наполняет new_chunks / ids_to_delete. Обновляет state.
    Возвращает статистику.
    """
    stats = {"added": 0, "skipped": 0, "failed": 0}

    if progress_cb: progress_cb("s3_list", 0, 0, "Запрашиваю список S3…")
    s3_current = list_s3_documents()   # key → etag (без скачивания)
    total = len(s3_current)
    if progress_cb: progress_cb("s3_list", total, total, f"Всего файлов в бакете: {total}")
    indexed_s3_keys = _get_indexed_metadata_values(vectorstore, "s3_key")

    for idx, (key, etag) in enumerate(s3_current.items(), start=1):
        filename = Path(key).name
        old_etag = state.get_s3_etag(key)

        # ETag не изменился — пропускаем
        if old_etag == etag and key in indexed_s3_keys:
            logger.debug(f"[s3] Без изменений: {filename}")
            stats["skipped"] += 1
            if progress_cb: progress_cb("s3", idx, total, f"Без изменений: {filename}")
            continue

        if progress_cb: progress_cb("s3", idx, total, f"Обработка: {filename}")

        missing_from_index = old_etag == etag and key not in indexed_s3_keys
        action = (
            "Восстановление индекса"
            if missing_from_index else
            "Новый" if old_etag is None else "Изменился"
        )
        logger.info(f"[s3] {action}: {filename} (etag: {etag[:8]}…)")

        # Скачиваем файл
        file_bytes = download_file(key)
        if file_bytes is None:
            logger.error(f"[s3] Не удалось скачать: {filename}")
            stats["failed"] += 1
            continue

        # Извлекаем текст
        text = extract_text(file_bytes, filename)
        if not text.strip():
            logger.warning(f"[s3] Пустой текст после извлечения: {filename}")
            stats["failed"] += 1
            continue

        # Если файл изменился — помечаем старые чанки на удаление
        if old_etag is not None:
            old_ids = _get_chunk_ids_by_s3_key(vectorstore, [key])
            if old_ids:
                ids_to_delete.extend(old_ids)
                logger.info(f"[s3] Помечено к удалению {len(old_ids)} старых чанков: {filename}")

        # Создаём новые чанки
        doc_url  = public_url(key)
        doc_type = Path(key).suffix.lower().lstrip(".")
        chunks   = _make_chunks(
            source=doc_url,
            title=filename,
            text=text,
            extra_meta={
                "s3_key":   key,
                "doc_type": doc_type,
                **_site_context_meta(site_context),
            },
        )
        new_chunks.extend(chunks)

        # Doc-header чанк: короткое резюме документа для ретривера.
        # Кладём имя файла и «как есть» (с подчёркиваниями — так пользователь
        # часто его ищет), и в нормализованном виде — чтобы матчился и
        # естественно-языковой запрос типа «мероприятие ДДТ №5 27 января 2026».
        stem          = Path(filename).stem
        readable_name = stem.replace("_", " ").strip()
        header_lines  = [
            f"Документ «{readable_name}»",
            f"Имя файла: {filename}",
            f"Ключ: {stem}",
            f"Тип документа: {doc_type.upper()}",
            f"Прямая ссылка: {doc_url}",
        ]
        if site_context:
            header_lines.extend([
                "",
                "Краткий контекст сайта (приоритетнее текста документа):",
                site_context,
            ])
        header_lines.extend([
            "",
            f"Содержание (начало): {text.strip()[:1400]}",
        ])
        header_doc = Document(
            page_content="\n".join(header_lines),
            metadata={
                "source":    doc_url,
                "title":     filename,
                "s3_key":    key,
                "doc_type":  doc_type,
                "is_header": True,
                **_site_context_meta(site_context),
            },
        )
        new_chunks.append(header_doc)

        state.set_s3_etag(key, etag)
        stats["added"] += 1
        logger.info(f"[s3] Проиндексировано: {filename} ({len(chunks)} чанков + header)")

    logger.info(
        f"[s3] Итого: +{stats['added']} новых, "
        f"{stats['skipped']} без изменений, "
        f"{stats['failed']} ошибок"
    )
    return stats


# ─────────────────────────────────────────────────────────────────────────────
#  Главная функция обновления
# ─────────────────────────────────────────────────────────────────────────────

def incremental_update(
    vectorstore:    Chroma,
    embeddings,
    state:          UpdateState,
    on_update_done: Optional[Callable] = None,
    sources:        Optional[list[str]] = None,
    use_site_cache: bool = False,
    progress_cb:    Optional[Callable] = None,
) -> dict:
    """
    Инкрементально обновляет Chroma-индекс из двух источников:
      1. Сайт mc.eduirk.ru
      2. Yandex Cloud S3

    Args:
        sources: список источников для обновления. Допустимые значения:
                 ["site", "s3"]. По умолчанию — оба.
        use_site_cache: брать страницы сайта из cache, если он есть.

    Все удаления и добавления применяются одним батчем в конце,
    что снижает количество обращений к Chroma.

    Returns:
        Словарь со статистикой по каждому источнику.
    """
    sources = sources or ["site", "s3"]
    logger.info("═" * 50)
    logger.info(f"[update] Начинаю инкрементальное обновление (sources={sources})")

    all_new_chunks:    list[Document] = []
    all_ids_to_delete: list[str]      = []

    stats = {
        "site": {},
        "s3":   {},
        "total_chunks_added":   0,
        "total_chunks_deleted": 0,
    }

    # ── Источник 1: Сайт ──────────────────────────────────────────────────────
    if "site" in sources:
        logger.info("[update] ── Источник 1: Сайт ──")
        try:
            stats["site"] = _update_from_site(
                vectorstore, state, all_new_chunks, all_ids_to_delete,
                use_cache=use_site_cache,
                progress_cb=progress_cb,
            )
        except Exception as e:
            logger.error(f"[update] Ошибка обновления сайта: {e}", exc_info=True)
            stats["site"] = {"error": str(e)}
    else:
        stats["site"] = {"skipped": True}

    site_context = _site_context_from_pending(all_new_chunks) or _get_existing_site_context(vectorstore)
    if site_context:
        stats["site_context_chars"] = len(site_context)
        logger.info(f"[update] Контекст сайта доступен для S3-документов ({len(site_context)} симв.)")
    else:
        logger.warning("[update] Контекст сайта не найден — S3-документы будут индексироваться без него")

    # ── Источник 2: Yandex Cloud S3 ───────────────────────────────────────────
    if "s3" in sources:
        logger.info("[update] ── Источник 2: Yandex Cloud S3 ──")
        try:
            stats["s3"] = _update_from_s3(
                vectorstore, state, all_new_chunks, all_ids_to_delete,
                site_context=site_context,
                progress_cb=progress_cb,
            )
        except Exception as e:
            logger.error(f"[update] Ошибка обновления S3: {e}", exc_info=True)
            stats["s3"] = {"error": str(e)}
    else:
        stats["s3"] = {"skipped": True}

    # ── Применяем изменения к Chroma ──────────────────────────────────────────
    if not all_ids_to_delete and not all_new_chunks:
        logger.info("[update] Изменений нет — индекс актуален")
    else:
        # Сначала удаляем, потом добавляем — чтобы не было дублей
        if all_ids_to_delete:
            # Убираем дубли ID (один чанк может быть в нескольких списках)
            unique_ids = list(set(all_ids_to_delete))
            if progress_cb:
                progress_cb("chroma_delete", 0, len(unique_ids), f"Удаляю {len(unique_ids)} чанков…")
            vectorstore.delete(ids=unique_ids)
            stats["total_chunks_deleted"] = len(unique_ids)
            logger.info(f"[update] Удалено чанков: {len(unique_ids)}")

        if all_new_chunks:
            # Батчим: Chroma падает на больших вставках (лимит ~5000 на батч,
            # плюс embeddings считаются синхронно — лучше мелкими порциями)
            BATCH = 500
            total_chunks = len(all_new_chunks)
            total_added  = 0
            logger.info(
                f"[update] Начинаю добавление {total_chunks} чанков "
                f"батчами по {BATCH}..."
            )
            if progress_cb:
                progress_cb("chroma_add", 0, total_chunks, f"Добавляю {total_chunks} чанков в индекс…")
            for i in range(0, total_chunks, BATCH):
                batch = all_new_chunks[i : i + BATCH]
                try:
                    vectorstore.add_documents(batch)
                    total_added += len(batch)
                    logger.info(
                        f"[update]   батч {i // BATCH + 1}: "
                        f"+{len(batch)} (всего добавлено {total_added}/{total_chunks})"
                    )
                    if progress_cb:
                        progress_cb("chroma_add", total_added, total_chunks,
                                    f"Добавлено {total_added}/{total_chunks} чанков")
                except Exception as e:
                    logger.error(
                        f"[update]   батч {i // BATCH + 1} упал: {e}",
                        exc_info=True,
                    )
            stats["total_chunks_added"] = total_added
            logger.info(f"[update] Добавлено чанков: {total_added}")

    # ── Сохраняем state ───────────────────────────────────────────────────────
    state.save()

    total = vectorstore._collection.count()
    logger.info(f"[update] Готово. Векторов в базе: {total}")
    logger.info("═" * 50)

    if on_update_done:
        on_update_done(stats)

    return stats


# ─────────────────────────────────────────────────────────────────────────────
#  Планировщик
# ─────────────────────────────────────────────────────────────────────────────

class RAGScheduler:
    """
    Запускает incremental_update каждые UPDATE_INTERVAL_HOURS часов
    как фоновая asyncio-задача рядом с FastAPI.
    """

    def __init__(
        self,
        vectorstore:    Chroma,
        embeddings,
        interval_hours: float              = UPDATE_INTERVAL_HOURS,
        on_update_done: Optional[Callable] = None,
        run_on_start:   bool               = False,
    ):
        self._vectorstore    = vectorstore
        self._embeddings     = embeddings
        self._interval       = interval_hours * 3600
        self._on_update_done = on_update_done
        self._run_on_start   = run_on_start
        self._state          = UpdateState()
        self._task:       Optional[asyncio.Task] = None
        self._last_run:   Optional[datetime]     = None
        self._last_stats: Optional[dict]         = None

    async def _loop(self) -> None:
        if not self._run_on_start:
            logger.info(
                f"[scheduler] Первое обновление через {self._interval / 3600:.0f} ч."
            )
            await asyncio.sleep(self._interval)

        while True:
            try:
                logger.info(f"[scheduler] Запуск ({datetime.now().isoformat()})")
                # Запускаем в thread pool чтобы не блокировать event loop
                loop  = asyncio.get_running_loop()   # fix: get_event_loop устарел
                stats = await loop.run_in_executor(
                    None,
                    lambda: incremental_update(
                        self._vectorstore,
                        self._embeddings,
                        self._state,
                        self._on_update_done,
                    ),
                )
                self._last_run   = datetime.now()
                self._last_stats = stats
            except asyncio.CancelledError:
                logger.info("[scheduler] Остановлен")
                return
            except Exception as e:
                logger.error(f"[scheduler] Ошибка: {e}", exc_info=True)

            await asyncio.sleep(self._interval)

    def start(self) -> None:
        if self._task and not self._task.done():
            logger.warning("[scheduler] Уже запущен")
            return
        self._task = asyncio.create_task(self._loop())
        logger.info(f"[scheduler] Запущен. Интервал: {self._interval / 3600:.0f} ч.")

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            logger.info("[scheduler] Задача отменена")

    def status(self) -> dict:
        next_run = None
        if self._last_run:
            next_run = datetime.fromtimestamp(
                self._last_run.timestamp() + self._interval
            ).isoformat()

        return {
            "running":        self._task is not None and not self._task.done(),
            "last_run":       self._last_run.isoformat() if self._last_run else None,
            "next_run":       next_run or "при следующем цикле",
            "interval_hours": self._interval / 3600,
            "last_stats":     self._last_stats,
        }

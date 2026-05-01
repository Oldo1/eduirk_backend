"""
site_crawler.py — краулер сайта mc.eduirk.ru

Обходит все HTML-страницы домена и возвращает словарь:
    url → {"title": str, "text": str}

Особенности:
  — HTML парсится один раз на страницу (не дважды)
  — el.name проверяется на None перед использованием (fix для текстовых узлов)
  — пропускает не-HTML ресурсы (PDF, изображения и т.д.)
  — вежливый краулер: пауза между запросами
"""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from config import (
    SITE_START_URL,
    SITE_MAX_PAGES,
    SITE_CRAWL_DELAY,
    SITE_USER_AGENT,
    SITE_SKIP_TAGS,
    SITE_MIN_TEXT_LEN,
)

logger = logging.getLogger("site_crawler")


# ─────────────────────────────────────────────────────────────────────────────
#  Утилиты URL
# ─────────────────────────────────────────────────────────────────────────────

def normalize_url(url: str) -> str:
    """Убирает фрагмент (#...) и нормализует path для единообразия."""
    p = urlparse(url)
    # Убираем trailing slash кроме корня
    path = p.path.rstrip("/") or "/"
    return p._replace(fragment="", path=path, query=p.query).geturl()


def same_domain(url: str, base: str) -> bool:
    return urlparse(url).netloc == urlparse(base).netloc


# ─────────────────────────────────────────────────────────────────────────────
#  Извлечение текста из HTML
# ─────────────────────────────────────────────────────────────────────────────

def extract_page_content(html: str) -> tuple[str, str, str]:
    """
    Извлекает (title, text, breadcrumb) из HTML.

    Возвращает:
        title — заголовок страницы без суффикса сайта
        text  — основной текст с заголовками, параграфами и списками
        breadcrumb — хлебные крошки для иерархии
    """
    soup = BeautifulSoup(html, "lxml")

    # Удаляем служебные теги
    for tag in soup(SITE_SKIP_TAGS):
        tag.decompose()

    # Заголовок страницы
    title = ""
    if soup.title:
        title = soup.title.get_text(strip=True)
        # Убираем суффикс «— МКУ развития образования...»
        title = re.sub(r'\s*[-|–—]\s*МКУ.*$', '', title).strip()

    # Извлекаем breadcrumb
    breadcrumb = ""
    breadcrumb_elem = soup.find(class_=re.compile(r"breadcrumb", re.I)) or soup.find("nav", class_=re.compile(r"breadcrumb", re.I))
    if breadcrumb_elem:
        breadcrumb = breadcrumb_elem.get_text(" > ", strip=True)

    # Ищем основной контент (Joomla-специфичные классы)
    main_node = (
        soup.find("main")
        or soup.find("article")
        or soup.find(id=re.compile(r"content|main|body", re.I))
        or soup.find(class_=re.compile(r"content|main|body|post|article", re.I))
        or soup.body
    )
    node = main_node or soup

    lines: list[str] = []
    for el in node.descendants:
        # el.name может быть None для NavigableString (текстовых узлов)
        if not isinstance(el, Tag):
            continue

        name = el.name
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            text = el.get_text(" ", strip=True)
            if text:
                level = int(name[1])
                lines.append(f"\n{'#' * level} {text}\n")
        elif name == "p":
            text = el.get_text(" ", strip=True)
            if text:
                lines.append(text + "\n")
        elif name == "li":
            text = el.get_text(" ", strip=True)
            if text:
                lines.append(f"• {text}")

    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    return title, text, breadcrumb


# ─────────────────────────────────────────────────────────────────────────────
#  Краулер
# ─────────────────────────────────────────────────────────────────────────────

def crawl(
    start_url: str   = SITE_START_URL,
    max_pages: int   = SITE_MAX_PAGES,
    delay:     float = SITE_CRAWL_DELAY,
) -> dict[str, dict]:
    """
    Обходит сайт начиная с start_url и возвращает:
        { url: {"title": str, "text": str}, ... }

    Каждая страница парсится один раз — HTML используется и для
    извлечения текста, и для сбора ссылок.
    """
    session = requests.Session()
    session.headers["User-Agent"] = SITE_USER_AGENT

    base   = normalize_url(start_url)
    queue  = [base]
    seen:   set[str]          = set()
    result: dict[str, dict]   = {}

    logger.info(f"[crawl] Старт: {start_url}  (лимит: {max_pages or '∞'} стр.)")

    while queue and (max_pages == 0 or len(result) < max_pages):
        url = normalize_url(queue.pop(0))

        if url in seen:
            continue
        seen.add(url)

        # ── Загрузка ──────────────────────────────────────────────────────────
        try:
            resp = session.get(url, timeout=10, allow_redirects=True)
        except requests.RequestException as e:
            logger.debug(f"[crawl] Сетевая ошибка {url}: {e}")
            continue

        if resp.status_code != 200:
            logger.debug(f"[crawl] HTTP {resp.status_code}: {url}")
            continue

        content_type = resp.headers.get("Content-Type", "")
        if "text/html" not in content_type:
            continue   # пропускаем PDF, изображения и т.д.

        # ── Парсинг (один раз!) ───────────────────────────────────────────────
        soup = BeautifulSoup(resp.text, "lxml")

        # Извлекаем текст
        title, text, breadcrumb = extract_page_content(resp.text)

        if len(text) < SITE_MIN_TEXT_LEN:
            logger.debug(f"[crawl] Пустой контент: {url}")
            continue

        result[url] = {"title": title, "text": text, "breadcrumb": breadcrumb}
        logger.debug(f"[crawl] ({len(result)}) {url}")

        # ── Собираем ссылки из уже распарсенного soup ─────────────────────────
        for a in soup.find_all("a", href=True):
            href = normalize_url(urljoin(url, a["href"]))
            if (
                href not in seen
                and href not in queue
                and same_domain(href, base)
                and urlparse(href).scheme in ("http", "https")
            ):
                queue.append(href)

        time.sleep(delay)

    logger.info(f"[crawl] Готово: {len(result)} страниц")
    return result

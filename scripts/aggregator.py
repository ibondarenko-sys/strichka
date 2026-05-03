#!/usr/bin/env python3
"""
aggregator.py — основний скрипт парсингу.

Що робить:
  1. Читає sources.yaml
  2. Паралельно завантажує RSS-стрічки
  3. Парсить, нормалізує, дедуплікує статті
  4. Зливає з існуючим data/articles.json
  5. Чистить старе (старше retention_days)
  6. Зберігає в data/articles.json

Запуск:
  python scripts/aggregator.py

Вимагає:
  pip install feedparser pyyaml requests beautifulsoup4 python-dateutil
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

# === Конфіг шляхів ===
ROOT = Path(__file__).resolve().parent.parent
SOURCES_FILE = ROOT / "sources.yaml"
DATA_FILE = ROOT / "data" / "articles.json"
OG_CACHE_FILE = ROOT / "data" / "og_cache.json"

# === Логування ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("aggregator")


# === Модель статті ===
@dataclass
class Article:
    id: str           # стабільний хеш (URL → SHA1)
    title: str
    summary: str      # коротке резюме (перші ~280 символів без HTML)
    url: str
    source: str       # назва джерела
    category: str
    lang: str
    published: str    # ISO 8601 UTC
    fetched: str      # коли ми це побачили (ISO 8601 UTC)
    image: str | None = None  # URL головного зображення, якщо є


# === Утиліти ===
def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_id(url: str) -> str:
    """Стабільний ID на основі URL — щоб не дублювати при наступних прогонах."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def clean_html(raw: str | None) -> str:
    """Прибирає теги, нормалізує пробіли, декодує HTML-entities."""
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "html.parser")
    text = soup.get_text(" ", strip=True)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def truncate(text: str, limit: int = 280) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + "…"


def parse_date(entry) -> str:
    """Дістаємо дату з RSS-запису — пробуємо кілька полів."""
    for field in ("published", "updated", "created"):
        val = entry.get(field)
        if val:
            try:
                dt = dateparser.parse(val)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
            except (ValueError, TypeError):
                continue
    # fallback — поточний час
    return now_utc_iso()


def extract_image(entry, base_url: str | None = None) -> str | None:
    """Шукає головне зображення в RSS-записі (різні стандарти).

    Повертає абсолютний URL (відносні шляхи розв'язує через base_url).
    """
    found: str | None = None

    # 1. media:content / media:thumbnail
    if "media_content" in entry and entry["media_content"]:
        found = entry["media_content"][0].get("url")
    if not found and "media_thumbnail" in entry and entry["media_thumbnail"]:
        found = entry["media_thumbnail"][0].get("url")

    # 2. enclosure (Atom)
    if not found:
        for link in entry.get("links", []):
            if link.get("rel") == "enclosure" and link.get("type", "").startswith("image/"):
                found = link.get("href")
                break

    # 3. перший <img> у summary/content
    if not found:
        raw = entry.get("summary", "") or ""
        if "content" in entry and entry["content"]:
            raw = entry["content"][0].get("value", "") or raw
        if raw:
            soup = BeautifulSoup(raw, "html.parser")
            img = soup.find("img")
            if img and img.get("src"):
                found = img["src"]

    if not found:
        return None

    # Робимо URL абсолютним
    if base_url and not urlparse(found).netloc:
        found = urljoin(base_url, found)
    return found


# === OG-image fallback ===
def fetch_og_image(url: str, user_agent: str, timeout: int = 10) -> str | None:
    """Завантажує HTML-сторінку статті і витягує og:image / twitter:image.

    Викликається тільки коли в RSS немає картинки. Кешується.
    """
    headers = {
        "User-Agent": user_agent,
        # Деякі сайти віддають інше тегування мобільним; залишаємо desktop
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "uk,en;q=0.5",
    }
    try:
        # stream=True + обмеження на читання — щоб не качати важкі сторінки повністю
        resp = requests.get(url, headers=headers, timeout=timeout, stream=True, allow_redirects=True)
        resp.raise_for_status()
        # Читаємо максимум 256 KB — <head> поміщається з великим запасом
        content = resp.raw.read(256 * 1024, decode_content=True)
    except (requests.RequestException, OSError):
        return None

    try:
        soup = BeautifulSoup(content, "html.parser")
    except Exception:
        return None

    # Пріоритет: og:image > og:image:secure_url > twitter:image
    selectors = [
        ("meta", {"property": "og:image"}),
        ("meta", {"property": "og:image:secure_url"}),
        ("meta", {"name": "twitter:image"}),
        ("meta", {"name": "twitter:image:src"}),
    ]
    for tag, attrs in selectors:
        el = soup.find(tag, attrs=attrs)
        if el and el.get("content"):
            img_url = el["content"].strip()
            # Робимо абсолютним
            if not urlparse(img_url).netloc:
                img_url = urljoin(url, img_url)
            return img_url

    return None


def load_og_cache() -> dict[str, str | None]:
    """Кеш: article_id -> image_url (або None якщо не знайшли — щоб не питати знову)."""
    if not OG_CACHE_FILE.exists():
        return {}
    try:
        return json.loads(OG_CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_og_cache(cache: dict[str, str | None]) -> None:
    OG_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    OG_CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def enrich_with_og_images(
    articles: list[Article],
    settings: dict,
) -> list[Article]:
    """Для статей без картинки — пробуємо витягти OG із самої сторінки.

    Кешуємо результат (включно з негативними), щоб не дублювати запити.
    """
    if not settings.get("fetch_og_images", True):
        return articles

    cache = load_og_cache()
    user_agent = settings.get("user_agent", "NewsAggregatorBot/1.0")

    # Знаходимо статті без картинки і без кешу
    todo = [a for a in articles if not a.image and a.id not in cache]

    if not todo:
        log.info("OG fallback: усі статті вже мають зображення або в кеші")
        return articles

    log.info("OG fallback: %d статей потребують перевірки...", len(todo))

    def worker(article: Article) -> tuple[str, str | None]:
        return article.id, fetch_og_image(article.url, user_agent)

    success = 0
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(worker, a): a for a in todo}
        for fut in as_completed(futures):
            try:
                article_id, img_url = fut.result()
                cache[article_id] = img_url  # None теж кешуємо
                if img_url:
                    success += 1
            except Exception as exc:
                log.debug("OG fail: %s", exc)

    log.info("OG fallback: знайдено картинок %d / %d", success, len(todo))

    # Чистимо кеш від записів про статті, яких більше немає
    valid_ids = {a.id for a in articles}
    cache = {k: v for k, v in cache.items() if k in valid_ids}
    save_og_cache(cache)

    # Застосовуємо знайдені картинки до статей
    for article in articles:
        if not article.image and article.id in cache:
            article.image = cache[article.id]

    return articles


# === Завантаження стрічки ===
def fetch_feed(source: dict, settings: dict) -> list[Article]:
    """Завантажує одну RSS-стрічку та повертає список нормалізованих статей."""
    name = source["name"]
    url = source["url"]
    category = source["category"]
    lang = source["lang"]
    max_items = settings.get("max_per_source", 15)

    log.info("→ %s", name)

    headers = {"User-Agent": settings.get("user_agent", "NewsAggregatorBot/1.0")}
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.warning("  ✗ %s: %s", name, exc)
        return []

    parsed = feedparser.parse(resp.content)
    if parsed.bozo and not parsed.entries:
        log.warning("  ✗ %s: невалідна стрічка (%s)", name, parsed.bozo_exception)
        return []

    articles: list[Article] = []
    for entry in parsed.entries[:max_items]:
        link = entry.get("link")
        title = clean_html(entry.get("title"))
        if not link or not title:
            continue

        summary_raw = entry.get("summary") or (
            entry["content"][0]["value"] if entry.get("content") else ""
        )
        summary = truncate(clean_html(summary_raw), 280)

        articles.append(
            Article(
                id=make_id(link),
                title=title,
                summary=summary,
                url=link,
                source=name,
                category=category,
                lang=lang,
                published=parse_date(entry),
                fetched=now_utc_iso(),
                image=extract_image(entry, base_url=link),
            )
        )

    log.info("  ✓ %s: %d статей", name, len(articles))
    return articles


def fetch_all(sources: list[dict], settings: dict) -> list[Article]:
    """Паралельне завантаження всіх джерел."""
    results: list[Article] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_feed, s, settings): s for s in sources}
        for fut in as_completed(futures):
            try:
                results.extend(fut.result())
            except Exception as exc:  # на випадок несподіванок
                src = futures[fut]
                log.exception("Падіння на %s: %s", src["name"], exc)
    return results


# === Зберігання ===
def load_existing() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Не вдалося прочитати %s: %s", DATA_FILE, exc)
        return []


def merge(existing: list[dict], new: Iterable[Article]) -> list[dict]:
    """Зливаємо нові статті з існуючими, дедуплікуючи за id."""
    by_id: dict[str, dict] = {a["id"]: a for a in existing}
    for art in new:
        # нову версію не перезаписуємо, лишаємо першу появу (стабільний fetched)
        by_id.setdefault(art.id, asdict(art))
    return list(by_id.values())


def prune(articles: list[dict], retention_days: int) -> list[dict]:
    """Видаляємо все старіше за retention_days (за датою публікації)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    kept = []
    for a in articles:
        try:
            pub = dateparser.parse(a["published"])
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
            if pub >= cutoff:
                kept.append(a)
        except (ValueError, TypeError, KeyError):
            kept.append(a)  # якщо не змогли розпарсити — лишаємо
    return kept


def save(articles: list[dict]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    # сортуємо за датою публікації (новіше — вище)
    articles.sort(key=lambda a: a.get("published", ""), reverse=True)
    DATA_FILE.write_text(
        json.dumps(articles, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# === Точка входу ===
def main() -> int:
    if not SOURCES_FILE.exists():
        log.error("Не знайдено %s", SOURCES_FILE)
        return 1

    config = yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8"))
    sources = config.get("sources", [])
    settings = config.get("settings", {})

    if not sources:
        log.error("У sources.yaml немає джерел")
        return 1

    log.info("Завантажую %d джерел...", len(sources))
    new_articles = fetch_all(sources, settings)
    log.info("Отримано всього: %d статей", len(new_articles))

    existing = load_existing()
    log.info("Існуючих в базі: %d", len(existing))

    merged = merge(existing, new_articles)
    log.info("Після злиття (унікальних): %d", len(merged))

    pruned = prune(merged, settings.get("retention_days", 14))
    log.info("Після очищення старого: %d", len(pruned))

    # OG-fallback: для статей без зображення дотягуємо з самої сторінки.
    # Робимо це тільки після prune — щоб не запитувати картинки для статей,
    # які зараз же будуть видалені як застарілі.
    if settings.get("fetch_og_images", True):
        # dict -> Article -> dict (бо enrich_with_og_images працює з dataclass)
        articles_obj = [Article(**{k: v for k, v in a.items() if k in Article.__dataclass_fields__}) for a in pruned]
        enriched = enrich_with_og_images(articles_obj, settings)
        pruned = [asdict(a) for a in enriched]

    save(pruned)
    log.info("Збережено в %s", DATA_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())

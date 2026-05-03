#!/usr/bin/env python3
"""
build_site.py — генерує статичний сайт із data/articles.json.

Стек:
  - Jinja2 для шаблонів
  - Чистий HTML/CSS, без фреймворків (швидко на GitHub Pages)

Вихід: site/dist/index.html + копія articles.json для клієнтського пошуку.

Запуск:
  python scripts/build_site.py
"""

from __future__ import annotations

import json
import logging
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from dateutil import parser as dateparser
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "articles.json"
TEMPLATES_DIR = ROOT / "site" / "templates"
STATIC_DIR = ROOT / "site" / "static"
DIST_DIR = ROOT / "site" / "dist"

# Локалізовані назви категорій
CATEGORY_LABELS = {
    "ukraine": "Україна",
    "world": "Світ",
    "tech": "Технології",
    "business": "Бізнес",
    "culture": "Культура",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("build")


def time_ago(iso: str) -> str:
    """'2 год тому', 'щойно' — короткий relative time українською."""
    try:
        dt = dateparser.parse(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return ""
    delta = datetime.now(timezone.utc) - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "щойно"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} хв тому"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} год тому"
    days = hours // 24
    if days < 7:
        return f"{days} дн тому"
    weeks = days // 7
    return f"{weeks} тиж тому"


def domain_of(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc.replace("www.", "")


def main() -> None:
    if not DATA_FILE.exists():
        log.error("Немає %s. Спочатку запусти aggregator.py", DATA_FILE)
        return

    articles = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    log.info("Завантажено %d статей", len(articles))

    # Готуємо метадані для UI
    categories = Counter(a["category"] for a in articles)
    sources = Counter(a["source"] for a in articles)

    # Підготовка dist
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    if STATIC_DIR.exists():
        for item in STATIC_DIR.iterdir():
            dest = DIST_DIR / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)

    # Копія articles.json для клієнтського пошуку/фільтрації
    shutil.copy2(DATA_FILE, DIST_DIR / "articles.json")

    # Jinja2
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["time_ago"] = time_ago
    env.filters["domain"] = domain_of

    template = env.get_template("index.html")
    html = template.render(
        articles=articles,
        categories=sorted(categories.items(), key=lambda kv: -kv[1]),
        category_labels=CATEGORY_LABELS,
        sources_count=len(sources),
        total=len(articles),
        updated_at=datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
    )

    out = DIST_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    log.info("Згенеровано %s (%.1f KB)", out, out.stat().st_size / 1024)


if __name__ == "__main__":
    main()

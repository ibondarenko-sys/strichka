# 📰 Стрічка — автоматичний агрегатор новин

Парсер RSS-стрічок українських та світових медіа, що автоматично оновлюється
через GitHub Actions і публікує статичний сайт на GitHub Pages.

**Стек:** Python 3.12 · feedparser · Jinja2 · GitHub Actions · GitHub Pages
**Без сервера, без бази даних, без витрат** — тільки безкоштовні плани GitHub.

---

## 🏗️ Як це працює

```
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│  RSS-стрічки     │───▶│  aggregator.py   │───▶│ data/articles.json│
│  (12+ медіа)     │    │  парсинг + dedupe│    │  (зберігається у  │
└──────────────────┘    └──────────────────┘    │   репозиторії)    │
                                                 └──────────────────┘
                                                          │
                              ┌───────────────────────────┘
                              ▼
                    ┌──────────────────┐    ┌──────────────────┐
                    │  build_site.py   │───▶│   site/dist/     │
                    │  Jinja2 → HTML   │    │   index.html     │
                    └──────────────────┘    └────────┬─────────┘
                                                     │
                                                     ▼
                                            ┌──────────────────┐
                                            │  GitHub Pages    │
                                            │  (auto-deploy)   │
                                            └──────────────────┘
```

GitHub Actions запускає весь pipeline кожні 30 хвилин (`*/30 * * * *`).

---

## 🚀 Швидкий старт

### 1. Створи репозиторій

```bash
# в папці проєкту
git init
git add .
git commit -m "init: news aggregator"

# на GitHub: створи новий публічний репо, потім:
git remote add origin https://github.com/ТВІЙ_USERNAME/news-aggregator.git
git branch -M main
git push -u origin main
```

### 2. Увімкни GitHub Pages

1. Зайди в **Settings → Pages**
2. У **Source** обери **GitHub Actions** (не "Deploy from branch")
3. Збережи

### 3. Дозволь Actions писати в репо

1. **Settings → Actions → General**
2. У **Workflow permissions** обери **Read and write permissions**
3. Збережи

### 4. Перший запуск

1. **Actions** (вкладка зверху)
2. Обери workflow **Aggregate & Deploy**
3. Натисни **Run workflow** → **Run workflow**
4. Через 1-2 хвилини сайт буде доступний на
   `https://ТВІЙ_USERNAME.github.io/news-aggregator/`

Далі — все автоматично, кожні 30 хвилин.

---

## 🧪 Локальний запуск

```bash
# Створи віртуальне середовище
python3 -m venv .venv
source .venv/bin/activate

# Встанови залежності
pip install -r requirements.txt

# Запусти агрегатор
python scripts/aggregator.py

# Згенеруй сайт
python scripts/build_site.py

# Подивись результат
open site/dist/index.html         # macOS
xdg-open site/dist/index.html     # Linux
```

---

## 📝 Як додати або змінити джерела

Усе в одному файлі — `sources.yaml`:

```yaml
sources:
  - name: "Назва видання"
    url: "https://приклад.ua/feed/"
    category: ukraine    # ukraine | world | tech | business | culture
    lang: uk             # uk | en
```

**Як знайти RSS у медіа:**

1. Подивись у footer сайту — часто є іконка RSS.
2. Спробуй стандартні шляхи: `/rss`, `/feed`, `/rss.xml`, `/feed.xml`,
   `/rss/news.xml`.
3. View Source (`Ctrl+U`), пошук по `application/rss+xml` —
   там є `href`.
4. Якщо немає RSS взагалі — використай
   [rss-bridge](https://rss-bridge.org/bridge01/) або
   [RSSHub](https://docs.rsshub.app/).

---

## 🖼️ Зображення

Скрипт пробує знайти картинку до кожної статті у такому порядку:

1. **`media:content` / `media:thumbnail`** — стандарт MediaRSS (BBC, Guardian)
2. **`<enclosure>`** — стандарт Atom (більшість блогів)
3. **Перший `<img>` у summary/content** — fallback для старих RSS
4. **Open Graph (`og:image`)** — якщо вище нічого не знайшлось, скрипт
   завантажує саму сторінку статті та витягує `<meta property="og:image">`.
   Це основний механізм Facebook/LinkedIn для прев'ю — майже всі сучасні
   сайти його мають.

Результати OG-запитів кешуються в `data/og_cache.json` — кожна стаття
перевіряється рівно один раз. Цей файл комітиться разом із `articles.json`.

**Покриття:** RSS-only ≈ 60% статей мають картинку, з OG-fallback — близько 95%.

**Якщо щось не показується:**
- Деякі сайти блокують hot-linking — картинка є, але не вантажиться у твоєму
  браузері. На це вказує мовчазний `onerror` у шаблоні (картинка просто
  ховається).
- Деякі сайти повертають 403 на запити з нашим User-Agent. Підказка: спробуй
  замінити `user_agent` у `sources.yaml` на справжній браузерний рядок —
  іноді це допомагає.
- Якщо джерело принципово блокує — можна зовсім вимкнути OG-fetch для нього
  (зараз — глобально через `fetch_og_images: false`; per-source — як апгрейд).

### Майбутній апгрейд: кешування зображень у власне сховище

Якщо hot-linking стане проблемою (картинки масово не показуються) або
важливо, щоб новини не "зникали" разом із оригіналами — можна перейти
на власне сховище:

1. Під час білду завантажувати картинки в `site/dist/images/`
2. Ресайз до 600px ширини, JPEG 80% (Pillow це робить за один виклик)
3. Лінкувати на локальні файли: `/images/{article_id}.jpg`
4. Ставити невеликий TTL — старі видаляти разом зі статтями

Плюси: контроль, швидкість, ніщо не "ламається".
Мінуси: розмір репо росте — для 500 статей × ~80KB ≈ 40 MB. Якщо стане
критично, винести в окремий orphan branch (`images-cache`) або на
Cloudflare R2 (10 GB безкоштовно).

Цей крок поки не реалізовано — почнемо з OG-fallback і подивимось, чи
вистачить його.

---



Усе в `sources.yaml` під ключем `settings`:

| Параметр           | За замовчуванням | Що робить                         |
|--------------------|------------------|-----------------------------------|
| `retention_days`   | 14               | Скільки днів зберігати статті     |
| `articles_per_page`| 30               | (зарезервовано для майбутньої пагінації) |
| `max_per_source`   | 15               | Скільки статей брати за один прогін |
| `user_agent`       | NewsAggregatorBot | Що підставляти в HTTP-запити     |
| `fetch_og_images`  | true             | Дотягувати OG-зображення зі сторінки статті, якщо в RSS немає |

Зміна частоти запуску — в `.github/workflows/aggregate.yml`:

```yaml
schedule:
  - cron: "*/30 * * * *"   # кожні 30 хв
  # - cron: "0 * * * *"    # щогодини
  # - cron: "0 */6 * * *"  # кожні 6 годин
```

⚠️ GitHub Actions має лімит — `cron` може запускатись із затримкою до
кількох хвилин на безкоштовному плані. Це нормально.

---

## 🎨 Кастомізація дизайну

Шаблон сайту: `site/templates/index.html` — все в одному файлі (CSS + HTML + JS).

Кольори (CSS-змінні зверху файлу):

```css
--bg: #f4f1ea;        /* фон — кремовий */
--ink: #0a0a0a;       /* текст */
--accent: #d63e1f;    /* акцент — теракотовий */
--serif: 'Fraunces';  /* заголовки */
--sans: 'Inter Tight';/* текст */
```

Шрифти підключаються з Google Fonts. Заміни на свої — і збережи.

---

## 🛠️ Архітектурні рішення

**Чому JSON у репо, а не БД?**
- Безкоштовно (немає окремого сервісу)
- Версіонування статей через git history
- Легко дебажити — відкрив файл і дивишся

**Чому статичний HTML, а не React/Next.js?**
- Швидко вантажиться навіть на повільному 3G
- GitHub Pages віддає статику моментально
- SEO з коробки
- Нема JS-рантайму на серверній частині

**Чому 30 хвилин, а не 5?**
- GitHub Actions має місячні ліміти (2000 хв на безкоштовному плані)
- 48 запусків × 30 днів × ~30 секунд ≈ 720 хвилин — лишається запас
- Більшість новин не оновлюються частіше за 30 хв

---

## 🧯 Траблшутинг

**Workflow падає на пуші articles.json**
→ перевір **Settings → Actions → General → Workflow permissions: Read and write**

**Сайт не оновлюється після Actions**
→ перевір **Settings → Pages → Source = GitHub Actions** (не "Deploy from branch")

**Якесь джерело не парситься**
→ Actions логи покажуть, яке саме впало. Імовірно змінили URL стрічки —
  перевір вручну в браузері.

**Картинки не показуються**
→ Деякі сайти блокують hot-linking. Це нормально, fallback зроблений
  через `onerror="this.style.display='none'"`.

**Хочу свій домен**
→ **Settings → Pages → Custom domain** + додай CNAME-запис у DNS.

---

## 📦 Структура проєкту

```
news-aggregator/
├── .github/workflows/
│   └── aggregate.yml        # CI/CD pipeline
├── scripts/
│   ├── aggregator.py        # парсер RSS
│   └── build_site.py        # генератор HTML
├── site/
│   ├── templates/
│   │   └── index.html       # Jinja2 шаблон
│   ├── static/              # (опційно) додаткові ассети
│   └── dist/                # згенероване (gitignored)
├── data/
│   ├── articles.json        # БД статей (комітиться в репо)
│   └── og_cache.json        # кеш OG-зображень (комітиться в репо)
├── sources.yaml             # конфіг джерел
├── requirements.txt
└── README.md
```

---

## 📜 Ліцензія та етика

Цей агрегатор:
- ✅ Читає **публічні RSS-стрічки** (те, що видавці самі віддають)
- ✅ Зберігає **тільки заголовок + короткий summary**
- ✅ **Завжди лінкує на оригінал** — трафік йде до видавця
- ❌ Не копіює повні тексти статей
- ❌ Не показує контент без атрибуції

Це той самий патерн, що Google News, Feedly, NewsBlur — стандартна практика
для агрегаторів. Але якщо якесь видання попросить виключити їх — просто
прибери рядок з `sources.yaml`.

Код — MIT, роби що хочеш.

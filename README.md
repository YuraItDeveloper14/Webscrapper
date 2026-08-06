# leadgen — Google Maps → Email lead scraper

Збирає бізнеси з Google Maps за пошуковим запитом, заходить на їхні сайти,
витягує контактні email і складає все в базу (SQLite) з експортом у CSV/Excel.

## Як це працює

```
запит ("dentist Kyiv")
        │
        ▼
 [scrape_maps.py]  Playwright відкриває Google Maps, гортає стрічку
        │          результатів → name, website, phone, address, rating
        ▼
 [extract_emails.py]  заходить на сайт кожного бізнесу (головна + /contact
        │             + /about + ...) і дістає email (mailto: та з тексту)
        ▼
 [db.py]  SQLite: дедуплікація по maps_url, статуси листів
        │
        ▼
   export → leads.csv (відкривається в Excel)
```

> **Важливо:** Google Maps **не показує email** — його беруть із сайту компанії.
> Тому лід з email вийде тільки в тих бізнесів, що мають сайт із вказаною поштою.

## Встановлення

```bash
py -3.12 -m pip install -r requirements.txt
py -3.12 -m playwright install chromium
```

Скрапер спершу пробує **системний браузер** (Edge → Chrome) і лише потім
bundled-Chromium. Це навмисне: на деяких Windows-машинах bundled-Chromium від
Playwright не стартує через SxS-помилку ("side-by-side configuration is
incorrect"), а системний Edge працює чисто. Edge у Windows 11 є завжди.

## Використання

```bash
# один запит
py run.py scrape "dentist Kyiv" --max 60

# багато запитів із файлу (по одному в рядку)
py run.py scrape queries.txt --max 100

# подивитись браузер вживу (для дебагу)
py run.py scrape "coffee shop Lviv" --max 20 --show

# режим діагностики: зберігає data/_debug_feed.html + _debug.png,
# щоб підлаштувати селектори під актуальний DOM Google Maps
py run.py scrape "coffee shop Lviv" --max 20 --debug

# статистика бази
py run.py stats

# експорт у CSV (за замовч. лише ліди з email)
py run.py export leads.csv
py run.py export all_leads.csv --all
```

## Веб-панель

```bash
py -3.12 webapp/app.py      # dev-сервер → http://localhost:5000
```

Панель — головний інтерфейс: збір з мапи (Країна → Регіон → міста), реєстр із
пошуком/фільтрами (зокрема **оцінка перспективності** й фільтр «без сайту»),
**карта лідів** (`/map`), статуси, масові дії, Google Maps + `tel:` посилання,
експорт CSV.

## Структура

| Файл | Призначення |
|------|-------------|
| `webapp/app.py` | Flask-панель (реєстр + карта + збір) |
| `webapp/templates/` | HTML: `leads.html`, `map.html`, `base.html` |
| `leadgen/scrape_osm.py` | **Основне джерело** — OpenStreetMap (без браузера) |
| `leadgen/geo.py` | Країни/регіони/міста для збору |
| `leadgen/score.py` | Оцінка перспективності ліда |
| `leadgen/extract_emails.py` | Витяг email + сигнали сайту |
| `leadgen/scrape_maps.py` | Опційно: Google Maps через Playwright |
| `leadgen/db.py` | SQLite-сховище + експорт CSV |
| `run.py` | CLI (OSM/Maps) |
| `serve.py` | Продакшн-сервер (waitress) |
| `data/leads.db` | База (створюється автоматично) |

## Деплой

Продакшн-сервер (waitress, без dev-режиму):

```bash
py -3.12 serve.py          # читає HOST/PORT з оточення; типово 0.0.0.0:5000
```

Готові конфіги: `Dockerfile`, `render.yaml`, `Procfile`, `requirements.txt`
(лише веб-залежності — без Playwright).

- **Render:** підключи репозиторій → Render підхопить `render.yaml`, підніме
  безкоштовний **Postgres** і задеплоїть панель, під'єднану до нього.
- **Docker:** `docker build -t leadgen . && docker run -p 8080:8080 -v ${PWD}/data:/app/data leadgen`
- **Своя мережа:** `py -3.12 serve.py` — і панель доступна на `http://<твій-IP>:5000`.

### База даних

Шар БД (`leadgen/db.py`) на **SQLAlchemy** — один код для двох рушіїв:

- **Локально:** SQLite-файл `data/leads.db` (нічого налаштовувати не треба).
- **Продакшн:** якщо є змінна `DATABASE_URL` — використовується **Postgres**
  (на Render її автоматично підставляє `render.yaml`), тож ліди зберігаються
  назавжди, а не скидаються при передеплої.

## Правове / етичне (прочитай)

- Скрапінг Google Maps **суперечить Google Terms of Service**. Робимо повільно,
  малими обсягами, для власного дослідження ринку. Для промислових обсягів
  існує офіційний платний **Google Places API**.
- **Холодні email** регулюються законами (GDPR у ЄС, CAN-SPAM у США): має бути
  законна підстава, реальна фізична адреса відправника і робоча відписка
  (unsubscribe). Не спамимо приватних осіб — працюємо з **B2B**-контактами.
- Не женемо великий трафік на чужі сайти — тут стоять таймаути й ліміт
  паралельних запитів.

## Далі за планом (етап 2)

Веб-панель (Flask): переглядати лідів, ставити статуси, слати листи по шаблону.

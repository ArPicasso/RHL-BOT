# Правила проекта

Telegram Mini App и бот для всей РХЛ (Первенство России U21), сезон 2026/27.
Начинали как бот расписания МХК «Рязань-ВДВ». Мини-апп показывает, бот присылает
напоминания (ADR-002, ADR-003).

Читай `docs/ARCHITECTURE.md` перед любой задачей крупнее однострочной правки.
Там текущая схема, целевая архитектура, план по этапам и разбор функций.
Решения обсуждаются в живом документе:
https://claude.ai/code/artifact/b73460ae-abd0-4c96-9670-c62615e1ffa5

## Стек

- Python 3.11, aiogram 3 (long polling)
- Состояние в JSON-файлах, базы пока нет
- Деплой: systemd на VPS (`ryazan-bot.service`)
- `.github/workflows/run-bot.yml` — временный стенд на раннере GitHub, не хостинг

## Структура

| Файл | Что это |
| --- | --- |
| `bot.py` | Вся логика: онбординг `/start`, хендлеры, форматирование, `reminder_loop` |
| `games.json` | Календарь сезона, 48 игр. Правится руками |
| `subscribers.json` | Подписчики на напоминания. Не в git |
| `announced.json` | Матчи, о которых бот уже написал после игры (ADR-008). Не в git |
| `hidden_players.json` | Id игроков на сайте лиги, которых не показываем по просьбе (ADR-007, ADR-008) |
| `league.py` | Загрузка и разбор протоколов матчей с сайта лиги → `results.json` (ADR-001) |
| `results.json` | Результаты: id турнира → номер матча `n` (как в `games.json`) → протокол. Не в git |
| `tests/` | Тесты на `unittest`, фикстуры — реальные страницы сайта лиги |
| `docs/adr/` | Архитектурные решения, по файлу на решение |
| `leaders.json` | Лидеры лиги по шести показателям, по 30 игроков (ADR-009). В git: сейчас НМХЛ 2025/26, после первого тура РХЛ его заменяет задание мини-аппа |
| `past_clubs.json` | Клубы прошлых сезонов, которых нет в РХЛ: написания и эмблема из `webapp/logos/past/` (ADR-009) |
| `teams.json` | 26 команд лиги: конференция, город, id на r-hockey, варианты написания, прежние названия (`former`), цвета формы (`colors`), проводник онбординга (`mascot`: имя и фразы, ADR-011) |
| `zveno/` | Движок фэнтези «Звено» (ADR-014), **паркуется** вместе с `build_zveno.py` (из задания Pages убран); туры и конструктор названий переезжают в «Наш лёд». Только stdlib: константы `rules.py`, туры и дедлайны `tours.py`, очки за матч `points.py`, стоимость `prices.py`, приоры 25/26 `prior.py` → `zveno/data/prior_2526.json`, конструктор названий `names.py`, правила менеджера для сервера `manager.py` |
| `build_zveno.py` | Собирает `webapp/data/zveno/`: `tours.json`, `pool.json`, `matches.json`, `names.json`. В Pages-задании сразу после `build_data.py` |
| `docs/zveno/contract.md` | Контракт частей «Звена»: опубликованные данные, функции движка, API сервера, бот |
| `rhockey.py` | Календарь всей лиги с r-hockey.ru — временно, до открытия rhl.fhr.ru |
| `channels.json` | Telegram-каналы клубов и лиги для листа «Главной» (ADR-015): `kind`, `scope`, `markers`, короткое имя `short`, отказ клуба `optout` (`images` — без картинок, `all` — не показываем), дата письма клубу `notified`. Правится руками |
| `tg_channels.py` | Посты каналов из `t.me/s` → `channel_posts.json` (не в git): только превью, фильтры рекламы, букмекеров, дней рождения и возраста, постов не о молодёжке |
| `feed.py` | Правила листа дня «Главной» (ADR-015): свои карточки и посты каналов, доли 60/40, лимиты, ротация клубов. `build_data.py` пишет `webapp/data/feed/<клуб>.json` и общую ленту лиги за неделю `feed/stream.json` |
| `build_data.py` | Собирает `webapp/data/league.json` (команды, матчи, результаты, таблица), `h2h.json`, разборы матчей `matches/<id>.json` (ADR-008) и `leaders.json` (ADR-009) |
| `history.py` | Матчи пяти прошлых сезонов НМХЛ с сайта лиги → `history.json` для очных встреч (ADR-006) |
| `history.json` | Прошлые сезоны, команды уже в id из `teams.json`. В git, пересобирается руками раз в сезон |
| `history_protocols.json` | Протоколы прошлых матчей из «Последних встреч» для их разбора (ADR-008). В git, докачивается `history.py --protocols` |
| `webapp/` | Мини-апп: `index.html`, `style.css`, `app.js`, без сборки. Публикуется на GitHub Pages |
| `webapp/led.js`, `webapp/led.css` | Вкладка «Лёд» — игра «Наш лёд» (ADR-017): карта зон, расстановка сил на тур, поддержка, свой клуб. Пролог считает всё на устройстве; сервер подставляется переменной Pages `LED_API` |
| `webapp/zveno.js`, `webapp/zveno.css` | Вкладка «Звено» (ADR-014) — **паркуется**: в мини-апп не подключена, лежит до возможного возврата фэнтези |
| `webapp/data/zveno/mock/` | Мок-сервер `api.js` и выдуманные данные «Звена» для разработки без сервера: `?zveno_mock=1`, `=team`, `=open`, `=prolog`, `=start`, `=none`. В git через `add -f`: `webapp/data/` в `.gitignore` |
| `webapp/brand/` | Иконка для экрана загрузки Telegram и фавиконки (`icon.svg`, `icon-512.png`) |
| `webapp/logos/` | Эмблемы всех 26 клубов, 200×200 PNG с прозрачным фоном, путь — поле `logo` в `teams.json` |
| `webapp/players/` | Стикеры игроков вместо фото, 192×192 WebP: в форме клубов — `clubs/<клуб>-skater.webp` и `-goalie.webp`, общие — `skater.webp`, `goalie.webp`; исходники — `art/players/` (ADR-009) |
| `webapp/mascots/` | Проводники онбординга: `<клуб>-<поза>.webp`, 288×288, позы `hello`, `point`, `cheer`, `shrug` (ADR-011) |
| `art/mascots/` | Проводники онбординга (ADR-011): промпты Midjourney `prompts.md`, листы `<клуб>.png`, какая фигура листа в какой позе — `poses.json` |
| `tools/mascot_stickers.py` | Нарезать листы проводников из `art/mascots/` на четыре позы → `webapp/mascots/<клуб>-<поза>.webp` (руками, после новых картинок, ADR-011) |
| `tools/fantasy_model/` | Агентная модель сезона фэнтези «Звено»: прогон каждой новой плюшки против бюджета до включения (ADR-014, раздел 14) |
| `tools/capture_model/` | Агентная модель игры «Наш лёд»: свой клуб и захват карты лиги, прогон правила против бюджета до включения (ADR-017, предложено) |
| `tools/player_kits.py` | Нарезать стикеры формы из `art/players/clubs/` и найти место номера на майке → `art/players/kits.json` (руками, после новых картинок) |
| `webapp/story.html`, `webapp/stories/` | Карточки клубов для Telegram Stories и их готовые картинки (ADR-004) |
| `tools/render_stories.js` | Перерисовать `webapp/stories/` (Playwright, запускается руками) |
| `stickers/` | Стикеры бота 512×512 и эмодзи `emoji/` 100×100, WEBP; исходник — `stickers.html` (ADR-005) |
| `tools/render_stickers.js` | Перерисовать стикеры и эмодзи (Playwright, запускается руками) |
| `tools/upload_emoji.py` | Опубликовать эмодзи набором `t.me/addemoji/rhl_u21_by_<бот>` |
| `.claude/agents/bot-logic.md` | Агент для логики бота: онбординг, хендлеры, напоминания |
| `calendar.pdf` | Календарь на печать, отдаётся по кнопке |
| `docs/research/` | Результаты разведки: источники данных, аудитория, письмо клубу |

Источник истины по календарю и результатам — лига (`nmhl.fhr.ru`, с 2026/27 —
`rhl.fhr.ru`). Агрегаторы вроде `r-hockey.ru` — только для сверки: там пропадают игры.
Исключение (ADR-002): календарь команд без официального источника берём с r-hockey до
открытия сайта РХЛ, такие матчи помечены `official: false` и так и показываются.

## Как запустить локально

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
BOT_TOKEN=... venv/bin/python bot.py
venv/bin/python -m unittest discover -s tests   # тесты
venv/bin/python league.py                        # скачать протоколы в results.json
venv/bin/python league.py --leaders              # лидеры лиги в leaders.json (ADR-009)
venv/bin/python history.py                       # прошлые сезоны в history.json (раз в сезон)
venv/bin/python history.py --protocols           # затем протоколы прошлых встреч, ~30 минут
venv/bin/python tg_channels.py                   # посты каналов клубов в channel_posts.json (ADR-015)
venv/bin/python build_data.py                    # собрать webapp/data/league.json, h2h.json и листы feed/
cd webapp && python3 -m http.server 8000         # мини-апп в браузере: localhost:8000
```

`league.py` без аргументов берёт последний регулярный чемпионат на `nmhl.fhr.ru`,
всю лигу; `--club` — только один клуб.
Для сезона 2026/27, когда откроется сайт РХЛ: `league.py --site https://rhl.fhr.ru`.
Запросы к сайту лиги идут по одному с паузой в секунду — не убирать.

## Правила

1. **Сначала решение, потом код.** Архитектурные изменения обсуждаются и
   фиксируются в `docs/adr/NNN-*.md` до реализации.
2. **Токен не коммитить.** `.env` в `.gitignore`. Секреты — в переменных
   окружения или в Actions Secrets.
3. **Время только через `ZoneInfo("Europe/Moscow")`.** Наивных `datetime`
   в коде быть не должно.
4. **Персональные данные.** Геолокацию не хранить. У любых данных о человеке
   должен быть способ удаления. Подробности — в `docs/ARCHITECTURE.md`.
5. **Тексты для болельщиков — на русском**, включая сообщения об ошибках.
   В мини-аппе всё, что пришло из данных, вставлять только через `esc()`: названия
   и фамилии скачаны со сторонних сайтов.
7. **Интерфейс — по `DESIGN.md`.** Цвета, шрифты, радиусы и правила акцента берём оттуда.
   Нужно отступить от правила — сначала меняем `DESIGN.md`.
6. Каждая задача — отдельная ветка и отдельный PR.

## Что нельзя менять без обсуждения

- Формат `games.json` — от него зависит уже развёрнутый бот
- Время напоминаний (`REMIND_TODAY_AT`, `REMIND_TOMORROW_AT`)
- Переход на вебхуки вместо polling

## Известные баги

- ~~Повторное нажатие на тот же месяц или соперника → `message is not modified`~~ —
  исправлено: `edit_text` обёрнут в `safe_edit()` (try/except `TelegramBadRequest`).

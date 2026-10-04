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
- Деплой: systemd на VPS, служба `bot` (`deploy/`, порядок — `BOT_README.md`)
- `.github/workflows/run-bot.yml` — временный стенд на раннере GitHub, не хостинг

## Структура

| Файл | Что это |
| --- | --- |
| `bot.py` | Вся логика: онбординг `/start`, хендлеры, форматирование, `reminder_loop`, зовы в «Раскат» и на прогноз (ADR-023), тревоги админам (ADR-022) |
| `games.json` | Календарь сезона, 48 игр. Правится руками |
| `subscribers.json` | Подписчики на напоминания: `{"<chat_id>": [id команд, до трёх]}`, старый список `[chat_id]` бот сам переписывает в «Рязань-ВДВ» (ADR-019). Не в git |
| `announced.json` | Матчи, о которых бот уже написал после игры (ADR-008). Не в git |
| `reminded.json` | Какое напоминание уже ушло и кому: слот (`<дата>:today`/`:tomorrow`) → `done`, `tries`, список «чат\|матч». По нему бот догоняет напоминание, пропущенное из-за выкладки или упавшего туннеля, и не пишет дважды. Три дня, не в git |
| `hidden_players.json` | Id игроков на сайте лиги, которых не показываем по просьбе (ADR-007, ADR-008) |
| `league.py` | Загрузка и разбор протоколов матчей со старого движка сайта лиги (`nmhl.fhr.ru`) → `results.json` (ADR-001) |
| `results.json` | Результаты: id турнира → номер матча `n` (как в `games.json`) → протокол. Не в git |
| `tests/` | Тесты на `unittest`, фикстуры — реальные страницы сайта лиги |
| `docs/adr/` | Архитектурные решения, по файлу на решение |
| `leaders.json` | Лидеры лиги по шести показателям, по 30 игроков (ADR-009). В git: сейчас НМХЛ 2025/26; задание мини-аппа заменяет его лидерами РХЛ с `rhl.fhr.ru` (`rhl_site.py`), как только они там есть |
| `past_clubs.json` | Клубы прошлых сезонов, которых нет в РХЛ: написания и эмблема из `webapp/logos/past/` (ADR-009) |
| `teams.json` | 26 команд лиги: конференция, город, пояс домашней арены `tz` (IANA, ADR-019), id на r-hockey, варианты написания, прежние названия (`former`), цвета формы (`colors`), проводник онбординга (`mascot`: имя и фразы, ADR-011) |
| `raskat/` | Движок игры «Раскат» (ADR-018), только stdlib: константы и формула очков `rules.py`, размер поля по дню недели `plan.py`, расклад, решатель и проверка пути `puzzle.py`, зачёты `standings.py` |
| `build_raskat.py` | Собирает `webapp/data/raskat/`: `index.json` и расклад на каждый день сезона. В Pages-задании сразу после `build_data.py` |
| `docs/raskat/contract.md` | Контракт частей «Раската»: правила поля, опубликованные данные, функции движка, очки и зачёты, API сервера, бот |
| `server.py` | API на VPS (ADR-019): `/api/live/*`, зачёт «Раската» `/api/raskat/*`, прогнозы `/api/predict/*`, `/api/health`, пульт `/api/admin/status` и счётчик открытий `/api/seen` (ADR-021). aiohttp на `127.0.0.1:8080`, подпись `initData`, CORS для Pages, сверка соли |
| `raskat_store.py`, `predict.py` | Хранилище зачёта «Раската» и голоса «Кто победит?» (ADR-020) в SQLite `state.db` (не в git); в `predict.py` ещё правила приёма и итога матча. Для зовов (ADR-023): `to_call` — кого звать в раскат дня, `voted` — кто уже голосовал. Открывают их `server.py` и бот, каждый своим соединением |
| `rhl_site.py` | Сайт лиги `rhl.fhr.ru` (новый движок, открылся 03.10.2026): календарь `/calendar/` и матч-центр `/matchcenter/<турнир>/<id>/` → `rhl_site.json` (не в git, кэш задания Pages). Номер, время МСК, счёт сыгранных, снимок идущих (ADR-019). У сыгранного — протокол `report` с вкладки `…/protocol/` (перечитывается три дня), лидеры сезона `/stat/leaders/` → `leaders.json` |
| `rhl_media.py` | «Смотреть» от лиги (ADR-019, раздел 7), только разбор: вкладка «Видео» матч-центра (плеер VK `video_ext.php?oid=-X&id=Y` → `https://vk.com/video-X_Y`) и страница «Трансляции» `/translations/`. Качает `rhl_site.update`: «Трансляции» — раз за запуск, «Видео» — матчей сегодня, завтра и только что сыгранных, пока ссылка не найдётся |
| `rhl_protocol.py` | Разбор протокола матча нового сайта лиги в тот же `league.Protocol`, что у старого: периоды, решение, голы, удаления, составы, вратари, судьи, тренеры; время МСК — поле `zone`. И таблиц лидеров (ADR-001, ADR-009, ADR-019). Без сети |
| `.github/workflows/sources-snapshot.yml` | «Снимок источников»: страницы rhl.fhr.ru, r-hockey и онлайна с раннера GitHub — в ветку `snapshots/sources`, из них фикстуры `tests/` |
| `rhockey.py` | Календарь всей лиги с r-hockey.ru — временно, до открытия rhl.fhr.ru |
| `channels.json` | Telegram-каналы клубов и лиги для листа «Главной» (ADR-015): `kind`, `scope`, `markers`, короткое имя `short`, отказ клуба `optout` (`images` — без картинок, `all` — не показываем), дата письма клубу `notified`. Правится руками |
| `tg_channels.py` | Посты каналов из `t.me/s` → `channel_posts.json` (не в git): только превью, фильтры рекламы, букмекеров (и по ссылкам), пиратских трансляций, дней рождения и возраста, постов не о молодёжке. Для матч-центра у поста внешние ссылки `links` и строки со временем `times`, короткие посты со ссылкой или со словами («ГООООЛ!») — в `extra` канала (ADR-019) |
| `feed.py` | Правила листа дня «Главной» (ADR-015): свои карточки и посты каналов, доли 60/40, лимиты, ротация клубов. `build_data.py` пишет `webapp/data/feed/<клуб>.json` и общую ленту лиги за неделю `feed/stream.json` |
| `build_data.py` | Собирает `webapp/data/league.json` (команды, матчи, результаты, таблица), `h2h.json`, разборы матчей `matches/<id>.json` (ADR-008) и `leaders.json` (ADR-009). Время матча — московское: `time`, `start`, у арены в другом поясе ещё `local`; источники — `schedule.json`, календарь сайта лиги, протокол (у `nmhl.fhr.ru` в нём местное), пост клуба. Протокол с сайта лиги (`report`) главнее счёта ленты. Плюс `online`, `watch` (первой — трансляция лиги, `apply_media`) и лента матча из каналов `events` (ADR-019) |
| `channel_events.json` | Лента матчей из постов каналов по ключу `<дата>\|<хозяева>\|<гости>`: копится между запусками задания Pages (t.me/s отдаёт только ~20 последних постов), держится три дня. Не в git, кэш задания |
| `khl_online.py` | Разбор онлайна КХЛ без сети (ADR-019): заголовок страницы матча, список дня, статус, счёт и события текстовой трансляции. Вёрстку подтверждает `tools/probe_sources.py` |
| `live.py` | Служба `live` на VPS: опрос онлайна КХЛ и календаря сайта РХЛ → `live/today.json`, `live/<дата>.json`, `live/schedule.json`, `live/sources.json` (не в git, ADR-019, раздел 5). События по ходу матча — по смене счёта и периода на странице сайта лиги, авторы — из блока авторов, когда он заполнен. Ночью и без матчей не опрашивает |
| `admin.py` | Пульт админа (ADR-021), только stdlib: счётчики служб по дням `Tracker` → `status/<служба>.json`, открытия мини-аппа `AdminStore` (в `state.db`), разбор `systemctl show` и запусков Actions, сбор ответа `build_status` и сводка `problems` |
| `status/` | Пульс и счётчики для пульта: `bot.json` (бот), `pages.json` (служба pages). Только числа, без id. Не в git |
| `webapp/admin.html`, `admin.js`, `admin.css` | Пульт админа (ADR-021): открывается из бота по `/admin`, данные — `${LIVE_API}/admin/status`, только `ADMIN_IDS`. Мок — `admin.html?admin_mock=1` и `=bad` (`webapp/data/admin/mock/`, в git через `add -f`) |
| `pages_kick.py` | Служба `pages` на VPS: раз в 15 минут запускает сборку Pages (`workflow_dispatch`), потому что cron GitHub теряет запуски. Токен `PAGES_TOKEN` в `/etc/rhl/bot.env`, нет его — молчит (ADR-015, дополнение 03.10) |
| `tools/probe_sources.py` | Запустить на VPS руками: сохранить страницы онлайна и `rhl.fhr.ru` в `probe/` (не в git) и показать, что из них разобрано — из этого делаются фикстуры `tests/` |
| `matchday.py` | «Смотреть» и время начала из постов каналов клубов и лиги в день матча (ADR-019, разделы 2 и 7): привязка поста к матчу, белый список видеохостингов, не больше трёх ссылок. Лента матча `match_events`: посты каналов обеих команд и лиги по ходу игры — события `kind: "text"` со ссылкой на пост. Без сети, его вызывает `build_data.py` |
| `schedule.json` | Время матчей и ссылки на онлайн на 14 дней — копия `$LIVE_API/live/schedule.json` с сервера (ADR-019, раздел 5). Кладёт шаг Pages, если задана переменная `LIVE_API`. Не в git; нет файла — сборка без него |
| `history.py` | Матчи пяти прошлых сезонов НМХЛ с сайта лиги → `history.json` для очных встреч (ADR-006) |
| `history.json` | Прошлые сезоны, команды уже в id из `teams.json`. В git, пересобирается руками раз в сезон |
| `history_protocols.json` | Протоколы прошлых матчей из «Последних встреч» для их разбора (ADR-008). В git, докачивается `history.py --protocols` |
| `webapp/` | Мини-апп: `index.html`, `style.css`, `app.js`, без сборки. Публикуется на GitHub Pages. Живое и прогнозы — с `window.LIVE_API` (переменная Pages `LIVE_API`), пусто — по `league.json` |
| `webapp/raskat.js`, `webapp/raskat.css` | Вкладка «Раскат» (ADR-018): поле и ведение шайбы по опубликованному раскладу дня без сервера, зачёты поверх API из `window.RASKAT_API` (переменная Pages `RASKAT_API`) |
| `webapp/data/raskat/mock/` | Мок-сервер `api.js` и выдуманные зачёты «Раската» для разработки без сервера: `?raskat_mock=1`, `=solved`, `=none`. В git через `add -f`: `webapp/data/` в `.gitignore` |
| `webapp/data/live/mock/` | Мок матч-центра (ADR-019) и прогнозов «Кто победит?» (ADR-020) без сервера: живое дня 03.10 `today.json` (`pre/`, `post/` — до и после матчей) и `api.js` с ответами `/predict/*`, как у `server.py`. `?live_mock=1` — матчи в разных статусах, `=pre`, `=post`, `=guest` — как вне Telegram, `=stale` — живое старше 5 минут, `=real` — настоящий ответ сервера 03.10 в 20:56 (`real/`), `=real-live` — он же на 17:51, два матча идут (`real-live/`). В git через `add -f` |
| `webapp/brand/` | Иконка для экрана загрузки Telegram и фавиконки (`icon.svg`, `icon-512.png`) |
| `webapp/logos/` | Эмблемы всех 26 клубов, 200×200 PNG с прозрачным фоном, путь — поле `logo` в `teams.json` |
| `webapp/players/` | Стикеры игроков вместо фото, 192×192 WebP: в форме клубов — `clubs/<клуб>-skater.webp` и `-goalie.webp`, общие — `skater.webp`, `goalie.webp`; исходники — `art/players/` (ADR-009) |
| `webapp/mascots/` | Проводники онбординга: `<клуб>-<поза>.webp`, 288×288, позы `hello`, `point`, `cheer`, `shrug` (ADR-011) |
| `art/mascots/` | Проводники онбординга (ADR-011): промпты Midjourney `prompts.md`, листы `<клуб>.png`, какая фигура листа в какой позе — `poses.json` |
| `tools/mascot_stickers.py` | Нарезать листы проводников из `art/mascots/` на четыре позы → `webapp/mascots/<клуб>-<поза>.webp` (руками, после новых картинок, ADR-011) |
| `tools/player_kits.py` | Нарезать стикеры формы из `art/players/clubs/` и найти место номера на майке → `art/players/kits.json` (руками, после новых картинок) |
| `webapp/story.html`, `webapp/stories/` | Карточки клубов для Telegram Stories и их готовые картинки (ADR-004) |
| `tools/render_stories.js` | Перерисовать `webapp/stories/` (Playwright, запускается руками) |
| `stickers/` | Стикеры бота 512×512 и эмодзи `emoji/` 100×100, WEBP; исходник — `stickers.html` (ADR-005) |
| `tools/render_stickers.js` | Перерисовать стикеры и эмодзи (Playwright, запускается руками) |
| `tools/upload_emoji.py` | Опубликовать эмодзи набором `t.me/addemoji/rhl_u21_by_<бот>` |
| `.claude/agents/bot-logic.md` | Агент для логики бота: онбординг, хендлеры, напоминания |
| `deploy/` | Сервер: `setup.sh` — первая настройка VPS, `update.sh` — выкладка (`rhl-update`), службы `bot.service`, `live.service`, `api.service`, `pages.service`, ночной бэкап состояния `backup.sh` с `backup.service` и `backup.timer` (`/var/backups/rhl`, 14 дней), `https.sh` — Caddy и HTTPS для API, `tunnel.sh` и `tg-tunnel.service` — выход в Telegram через зарубежный сервер (`TELEGRAM_PROXY`) |
| `.github/workflows/deploy.yml` | После слияния в `main` выкладывает бота на сервер по ключу, который умеет только `rhl-update` |
| `calendar.pdf` | Календарь на печать, отдаётся по кнопке |
| `docs/SYSTEM.md` | Как система работает сейчас: части, выкладка, секреты, сервер, что делать при сбоях |
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
venv/bin/python rhl_site.py                      # матчи, протоколы и лидеры с rhl.fhr.ru → rhl_site.json, leaders.json
venv/bin/python history.py                       # прошлые сезоны в history.json (раз в сезон)
venv/bin/python history.py --protocols           # затем протоколы прошлых встреч, ~30 минут
venv/bin/python tg_channels.py                   # посты каналов клубов в channel_posts.json (ADR-015)
venv/bin/python build_data.py                    # собрать webapp/data/league.json, h2h.json и листы feed/
venv/bin/python build_raskat.py                  # расклады «Раската» в webapp/data/raskat/ (ADR-018)
venv/bin/python live.py --once                   # один проход опроса живых источников в live/ (только с VPS в России)
BOT_TOKEN=... venv/bin/python server.py          # API на 127.0.0.1:8080: /api/health, зачёт, прогнозы
cd webapp && python3 -m http.server 8000         # мини-апп в браузере: localhost:8000
```

`league.py` без аргументов берёт последний регулярный чемпионат на `nmhl.fhr.ru`,
всю лигу; `--club` — только один клуб.
Сайт РХЛ `rhl.fhr.ru` открылся на другом движке: его протоколы и лидеров качает `rhl_site.py`, не `league.py`.
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

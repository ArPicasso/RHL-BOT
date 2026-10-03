# Бот расписания МХК «Рязань-ВДВ»

Деплой на VPS (Ubuntu 22.04/24.04 или Debian 12), от root на сервере:

    curl -fsSL https://raw.githubusercontent.com/ArPicasso/bogdanov/main/deploy/setup.sh -o setup.sh
    bash setup.sh

Скрипт ставит пакеты, открывает наружу только SSH, заводит пользователя `rhl`, клонирует код в
`/opt/rhl`, спрашивает токен (он живёт только в `/etc/rhl/bot.env`) и запускает службы `bot`, `live`
и `api` (служба, чьего кода ещё нет в `main`, пропускается).
Был старый `ryazan-bot` — останавливает его и переносит `subscribers.json` и `announced.json`.
Повторный запуск безопасен.

Перед запуском останови тестового бота в Actions («Запустить бота» → Cancel): две копии на
long polling мешают друг другу (`Conflict: terminated by other getUpdates request`).

В конце скрипт печатает три секрета: `DEPLOY_HOST`, `DEPLOY_KNOWN_HOSTS`, `DEPLOY_SSH_KEY`. Заведи их
в Settings → Secrets and variables → Actions — и после каждого слияния в `main` задание
«Выложить бота на сервер» само обновит код и перезапустит службы. Ключ умеет только это.
Руками то же самое: `rhl-update` на сервере.

Сервер в России: `api.telegram.org` оттуда закрыт, и бот падает с `Request timeout error`. Нужен любой
зарубежный сервер с входом по SSH, ставить на него ничего не надо. На российском, после `setup.sh`:

    bash /opt/rhl/deploy/tunnel.sh user@ЗАРУБЕЖНЫЙ-IP

Скрипт один раз спросит пароль, положит туда ключ, которому разрешён только проброс портов, поднимет
службу `tg-tunnel` (SOCKS на `127.0.0.1:1080`) и пропишет боту `TELEGRAM_PROXY`.

Логи: journalctl -u bot -f
Перезапуск: systemctl restart bot
Время напоминаний — REMIND_TODAY_AT / REMIND_TOMORROW_AT в bot.py.
Подписчики хранятся в /opt/rhl/subscribers.json.
После матча «Рязань-ВДВ» бот сам присылает подписчикам счёт с кнопкой «Как это было» (ADR-008).
Результаты он берёт из опубликованного мини-аппа (`data/league.json` по адресу WEBAPP_URL)
раз в 10 минут; с 23:00 до 9:00 МСК молчит. Уже отправленные матчи — в announced.json.

## HTTPS и зачёт

Зачёт «Раската», прогнозы «Кто победит?» и живой счёт отдаёт служба `api` (`server.py`). Она слушает
только `127.0.0.1:8080`, наружу её выводит Caddy по HTTPS. Один раз, от root на сервере:

    bash /opt/rhl/deploy/https.sh              # адрес <ip-через-дефисы>.sslip.io
    bash /opt/rhl/deploy/https.sh api.example.ru   # или свой домен, A-запись уже на сервер

Скрипт обновляет `rhl-update` и ставит службы `live` и `api`, ставит Caddy, открывает в ufw 80 и 443,
ждёт сертификат, проверяет `https://<хост>/api/health`, пишет боту `RASKAT_API` в `/etc/rhl/bot.env`
и перезапускает его. Повторный запуск безопасен. В конце он печатает две переменные — заведи их в
Settings → Secrets and variables → Actions → **Variables** и перезапусти задание «Мини-апп»:

    LIVE_API=https://<хост>/api
    RASKAT_API=https://<хост>/api/raskat

Соль раскладов: если в секретах задания Pages есть `RASKAT_SALT`, то же значение — в
`/etc/rhl/bot.env` (`RASKAT_SALT=…`, потом `systemctl restart api`). Не совпали — зачёт сам
выключается и пишет причину в `/api/health`. Бот позовёт лист ожидания зачёта в ближайшее время
напоминаний (10:00 или 19:00 МСК), так что переменные Pages лучше завести сразу.

Логи API: `journalctl -u api -f`. База зачёта и прогнозов — `/opt/rhl/state.db`.

## Тестовый запуск в GitHub Actions

Для постоянной работы нужен сервер (см. выше). Чтобы просто потестить бота с
телефона, не поднимая ничего у себя, есть workflow «Запустить бота»:

1. Settings → Secrets and variables → Actions → New repository secret,
   имя `BOT_TOKEN`, значение — токен от @BotFather.
2. Вкладка Actions → «Запустить бота» → Run workflow. В поле можно указать,
   сколько минут держать бота живым (по умолчанию 55, максимум 350).
3. Пока задание идёт, бот отвечает в Telegram. Остановить досрочно —
   Cancel workflow.

Подписки на напоминания в таком режиме не сохраняются: `subscribers.json`
живёт только внутри задания и пропадает вместе с раннером.

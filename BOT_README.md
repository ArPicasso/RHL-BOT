# Бот расписания МХК «Рязань-ВДВ»

Деплой на VPS (Ubuntu 22.04/24.04 или Debian 12), от root на сервере:

    curl -fsSL https://raw.githubusercontent.com/ArPicasso/bogdanov/main/deploy/setup.sh -o setup.sh
    bash setup.sh

Скрипт ставит пакеты, открывает наружу только SSH, заводит пользователя `rhl`, клонирует код в
`/opt/rhl`, спрашивает токен (он живёт только в `/etc/rhl/bot.env`) и запускает службу `bot`.
Был старый `ryazan-bot` — останавливает его и переносит `subscribers.json` и `announced.json`.
Повторный запуск безопасен.

Перед запуском останови тестового бота в Actions («Запустить бота» → Cancel): две копии на
long polling мешают друг другу (`Conflict: terminated by other getUpdates request`).

В конце скрипт печатает три секрета: `DEPLOY_HOST`, `DEPLOY_KNOWN_HOSTS`, `DEPLOY_SSH_KEY`. Заведи их
в Settings → Secrets and variables → Actions — и после каждого слияния в `main` задание
«Выложить бота на сервер» само обновит код и перезапустит бота. Ключ умеет только это.
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

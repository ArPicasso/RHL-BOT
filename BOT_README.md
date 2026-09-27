# Бот расписания МХК «Рязань-ВДВ»

Деплой на VPS (Ubuntu/Debian):

    scp ryazan_bot.zip root@IP:/opt/
    ssh root@IP
    cd /opt && apt install -y unzip python3-venv && unzip ryazan_bot.zip
    cd ryazan_bot && python3 -m venv venv && venv/bin/pip install -r requirements.txt
    cp ryazan-bot.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now ryazan-bot

Логи: journalctl -u ryazan-bot -f
Перезапуск после правок: systemctl restart ryazan-bot
Время напоминаний — REMIND_TODAY_AT / REMIND_TOMORROW_AT в bot.py.
Подписчики хранятся в subscribers.json.
После матча «Рязань-ВДВ» бот сам присылает подписчикам счёт с кнопкой «Как это было» (ADR-008).
Результаты он берёт из опубликованного мини-аппа (`data/league.json` по адресу WEBAPP_URL)
раз в 10 минут; с 23:00 до 9:00 МСК молчит. Уже отправленные матчи — в announced.json.

Лист ожидания фэнтези «Звено» (ADR-014) — zveno_waitlist.json: кто нажал `?start=zveno` или
написал /zveno. Выйти — кнопкой «Больше не звать». Раз в 10 минут бот читает опубликованный
`data/zveno/tours.json`; когда `status` становится `open`, один раз зовёт весь лист (ночью ждёт
9:00), лист пустеет, а сезон помечается в том же файле, чтобы не звать повторно после рестарта.
Сообщения по ходу сезона шлёт сервер «Звена», не бот.

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

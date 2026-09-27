# Сервер «Звена» и бот на своём VPS

Пошагово, для того, кто настраивает сервер впервые. Итог:

- **API «Звена»** (`zveno_api/`, ADR-014, раздел 13) — `https://api.<домен>/api/zveno`;
- **бот** (`bot.py`) переезжает сюда же с раннера GitHub;
- **nginx** отдаёт API по HTTPS, сертификат — бесплатный, от Let's Encrypt через certbot;
- **бэкап базы** — раз в сутки, хранятся 14 копий.

Мини-апп остаётся на GitHub Pages. Сервер не ходит на сайт лиги: он берёт то, что движок
публикует вместе с мини-аппом (`data/zveno/`).

```
Telegram ── мини-апп (GitHub Pages) ──HTTPS──▶ nginx :443 ──▶ zveno_api 127.0.0.1:8090 ──▶ SQLite
                                                                   │  раз в 10 минут
                                              data/zveno/*.json ◀──┘  (tours, pool, matches)
Telegram ◀── bot.py (long polling) и сообщения «Звена» (Bot API, тот же токен)
```

Команды ниже вводятся в терминале сервера. `ВАШ-ДОМЕН.ru` везде заменяй на свой домен.

## 1. Арендовать VPS

Нужен сервер в России: **Timeweb Cloud** или **Selectel**.

- Ubuntu 24.04;
- 2 vCPU, 2 ГБ памяти, диск 30–40 ГБ;
- публичный IPv4-адрес.

При создании добавь свой SSH-ключ. Нет ключа — на своём компьютере: `ssh-keygen -t ed25519`, потом
содержимое `~/.ssh/id_ed25519.pub` вставь в панели хостинга.

Вход: `ssh root@IP-СЕРВЕРА`.

## 2. Домен и A-запись

Нужен домен. У регистратора (или в DNS хостинга) добавь запись:

| Тип | Имя | Значение |
| --- | --- | --- |
| A | `api` | IP-адрес сервера |

Через 5–30 минут проверь со своего компьютера — должен вернуться IP сервера:

```bash
dig +short api.ВАШ-ДОМЕН.ru
```

## 3. Первая настройка сервера

```bash
apt update && apt upgrade -y
apt install -y git python3-venv python3-pip sqlite3 nginx certbot ufw
timedatectl set-timezone Europe/Moscow        # для удобства журналов; код и так живёт по МСК

ufw allow OpenSSH
ufw allow 'Nginx Full'                         # 80 и 443
ufw enable                                     # ответить y
```

Порт 8090 наружу не открываем: к API ходят только через nginx.

## 4. Пользователь и код

Сервисы работают от отдельного пользователя `rhl` без входа по паролю.

```bash
adduser --system --group --home /opt/rhl --shell /usr/sbin/nologin rhl
git clone https://github.com/ArPicasso/bogdanov.git /tmp/rhl && cp -a /tmp/rhl/. /opt/rhl/ && rm -rf /tmp/rhl
chown -R rhl:rhl /opt/rhl
sudo -u rhl python3 -m venv /opt/rhl/venv
sudo -u rhl /opt/rhl/venv/bin/pip install -r /opt/rhl/requirements.txt
```

Репозиторий приватный — сначала заведи deploy key: `ssh-keygen -t ed25519 -f /root/.ssh/rhl_deploy`,
публичную часть добавь в GitHub → Settings → Deploy keys (только чтение) и клонируй по
`git@github.com:ArPicasso/bogdanov.git`.

Проверка, что всё на месте (тесты — пара секунд):

```bash
cd /opt/rhl && sudo -u rhl venv/bin/python -m unittest discover -s tests
```

## 5. Переменные окружения. Токен — только здесь

Токен бота живёт **только на сервере**, в файле с правами 640. В git, в чаты и в скриншоты его
не кладём (правило 2 `CLAUDE.md`).

```bash
mkdir -p /etc/rhl
cp /opt/rhl/deploy/zveno.env.example /etc/rhl/zveno.env
cp /opt/rhl/deploy/bot.env.example /etc/rhl/bot.env
chown root:rhl /etc/rhl/*.env && chmod 640 /etc/rhl/*.env
nano /etc/rhl/zveno.env                        # вписать BOT_TOKEN=…, проверить остальное
nano /etc/rhl/bot.env                          # тот же BOT_TOKEN
```

| Переменная | Что это | По умолчанию |
| --- | --- | --- |
| `BOT_TOKEN` | Токен бота от @BotFather — им проверяется `initData` и уходят сообщения | обязателен |
| `ZVENO_DB` | Файл базы SQLite | `zveno.db` в рабочем каталоге |
| `ZVENO_DATA_URL` | Каталог опубликованных `data/zveno/`: URL или путь | `https://arpicasso.github.io/bogdanov/data/zveno/` |
| `ZVENO_ORIGIN` | Адрес Pages без пути — только ему разрешён CORS | `https://arpicasso.github.io` |
| `ZVENO_PORT` | Локальный порт API | `8090` |
| `WEBAPP_URL` | Мини-апп — для кнопки в сообщениях | `https://arpicasso.github.io/bogdanov/` |
| `ZVENO_MESSAGES` | `0` — не отправлять сообщения | `1` |

## 6. Проверить API до systemd

```bash
cd /opt/rhl
sudo -u rhl env $(grep -v '^#' /etc/rhl/zveno.env | xargs) ZVENO_DB=/tmp/zveno-check.db venv/bin/python -m zveno_api --once
```

Ответ — JSON с `"refreshed": true` и сезоном. Пока движок не опубликовал `data/zveno/`, будет
`"refreshed": false`: сервер запустится и подтянет данные сам, когда они появятся.

## 7. Запустить API как службу

```bash
cp /opt/rhl/deploy/zveno-api.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now zveno-api
systemctl status zveno-api                     # active (running)
journalctl -u zveno-api -f                     # журнал, выход — Ctrl+C
curl -s http://127.0.0.1:8090/api/zveno/health
```

## 8. nginx и HTTPS

Сначала сертификат. Сайт nginx по умолчанию уже отвечает на порту 80 из `/var/www/html` — certbot
проверит домен через него:

```bash
certbot certonly --webroot -w /var/www/html -d api.ВАШ-ДОМЕН.ru \
  --deploy-hook "systemctl reload nginx" -m ВАШ-EMAIL --agree-tos -n
```

Потом конфиг API:

```bash
cp /opt/rhl/deploy/nginx-zveno.conf /etc/nginx/sites-available/zveno
sed -i 's/api.example.ru/api.ВАШ-ДОМЕН.ru/g' /etc/nginx/sites-available/zveno
ln -s /etc/nginx/sites-available/zveno /etc/nginx/sites-enabled/zveno
nginx -t && systemctl reload nginx
```

Продление сертификата — само, таймер `certbot.timer`. Проверить: `certbot renew --dry-run`.

## 9. Проверка curl

С любого компьютера:

```bash
curl -s https://api.ВАШ-ДОМЕН.ru/api/zveno/health
# {"ok": true, "data": true, "updated": "…", "season": {…}}

curl -s https://api.ВАШ-ДОМЕН.ru/api/zveno/me
# {"error": "Открой «Звено» из Telegram: …"} — без initData нельзя, так и надо

curl -si -X OPTIONS -H "Origin: https://arpicasso.github.io" https://api.ВАШ-ДОМЕН.ru/api/zveno/team | head -5
# HTTP/2 204 и access-control-allow-origin: https://arpicasso.github.io
```

Запрос от имени пользователя — на сервере, подписанный initData печатает сам сервер (токен никуда
не уходит, подпись живёт сутки):

```bash
cd /opt/rhl
INIT=$(sudo -u rhl env $(grep -v '^#' /etc/rhl/zveno.env | xargs) venv/bin/python -m zveno_api --init-data 12345)
curl -s -H "Authorization: tma $INIT" https://api.ВАШ-ДОМЕН.ru/api/zveno/me
# {"manager": null, "season": {…}}
```

## 10. Сказать мини-аппу, где API

GitHub → репозиторий → **Settings → Secrets and variables → Actions → вкладка Variables → New
repository variable**:

- Name: `ZVENO_API`
- Value: `https://api.ВАШ-ДОМЕН.ru/api/zveno` (без слеша в конце)

Потом **Actions → Pages → Run workflow**: задание подставит адрес в `window.ZVENO_API`. Пусто — мини-апп
показывает только Пролог.

## 11. Перенести бота с раннера GitHub

Бот работает long polling: две копии одновременно мешают друг другу (`Conflict: terminated by other
getUpdates request`). Порядок такой:

1. GitHub → **Actions → «Запустить бота»** → открыть идущий запуск → **Cancel workflow run**.
2. Был старый сервер с `ryazan-bot.service` — там: `systemctl disable --now ryazan-bot`.
3. Перенести состояние бота, если оно есть: `subscribers.json`, `announced.json`,
   `zveno_waitlist.json` — в `/opt/rhl/`, владелец `rhl`:
   ```bash
   scp subscribers.json announced.json zveno_waitlist.json root@IP-СЕРВЕРА:/opt/rhl/
   chown rhl:rhl /opt/rhl/*.json
   ```
   Нет файлов (бот жил на раннере) — не страшно: подписчики нажмут «Напоминать» ещё раз, а
   `announced.json` бот создаст сам и не пришлёт старые результаты.
4. Запустить:
   ```bash
   cp /opt/rhl/deploy/bot.service /etc/systemd/system/
   systemctl daemon-reload
   systemctl enable --now bot
   journalctl -u bot -f
   ```

## 12. Бэкапы

```bash
install -d -o rhl -g rhl -m 700 /var/backups/rhl
chmod +x /opt/rhl/deploy/backup.sh
cp /opt/rhl/deploy/zveno-backup.service /opt/rhl/deploy/zveno-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now zveno-backup.timer
systemctl start zveno-backup                   # первый бэкап сразу
ls -lh /var/backups/rhl
```

Каждую ночь в 04:30 МСК — `sqlite3 .backup` (безопасно при работающем сервере), проверка целостности,
gzip. Хранятся 14 последних копий: удалённый кнопкой «Удалить моё „Звено“» менеджер исчезает и из
бэкапов не позже чем через 14 дней.

Хорошо бы раз в неделю уносить копию с сервера: `scp root@IP-СЕРВЕРА:/var/backups/rhl/zveno-*.db.gz .`

Восстановление:

```bash
systemctl stop zveno-api
gunzip -c /var/backups/rhl/zveno-2026-10-12_0430.db.gz > /var/lib/rhl/zveno.db
rm -f /var/lib/rhl/zveno.db-wal /var/lib/rhl/zveno.db-shm
chown rhl:rhl /var/lib/rhl/zveno.db
systemctl start zveno-api
```

## 13. Обновить код

```bash
cd /opt/rhl && sudo -u rhl git pull
sudo -u rhl venv/bin/pip install -r requirements.txt
systemctl restart zveno-api bot
```

Схема базы обновляется сама при запуске (`PRAGMA user_version`, `zveno_api/db.py`).

## 14. Если что-то не так

| Что видно | Что сделать |
| --- | --- |
| `curl …/health` → `"data": false` | Движок ещё не опубликовал `data/zveno/` или адрес `ZVENO_DATA_URL` неверный. `journalctl -u zveno-api` — строка `data not refreshed` |
| Мини-апп пишет «Нет связи со «Звеном»» | Переменная `ZVENO_API` в GitHub и пересборка Pages; `nginx -t`; сертификат: `certbot certificates` |
| В браузере ошибка CORS | `ZVENO_ORIGIN` — точно `https://arpicasso.github.io`, без пути и слеша |
| `401` на всё | Токен в `/etc/rhl/zveno.env` не от того бота, с которого открыт мини-апп |
| Бот молчит, в журнале `Conflict` | Где-то живёт вторая копия бота — шаг 11 |
| Сервер лежал | Дедлайн не сдвигается. Больше 6 часов за сутки до дедлайна — всем +1 бесплатный обмен автоматически |

## API

База — `https://api.<домен>/api/zveno`, контракт — `docs/zveno/contract.md`, раздел 4. Ниже —
уточнения сервера к контракту: всё, что в контракте не сказано или сказано общо. Старые поля и
пути не менялись.

**Общее**
1. `GET /health` — без авторизации: `{ok, data, updated, season}`. Для проверки и мониторинга.
2. Ошибки: `400` — неверный запрос или правило игры, `401` — initData, `404` — нет команды, лиги
   или состава на тур, `409` — команда уже есть, Пролог или сезон кончился, `413`, `500`, `503` —
   данные «Звена» ещё не загрузились. Всегда `{"error": "…"}`; у ошибок состава ещё `errors` — все
   ошибки движка списком, у ошибок оплаты — `fee_options`.
3. `POST /team` и `POST /leagues` отвечают `201`, остальное — `200`.
4. Любой запрос сначала догоняет наступившие дедлайны и закрытия: состав замораживается ровно в
   дедлайн, даже если фоновая задача опоздала.

**`GET /me`**
5. `manager`: `{name, title, fav_club, my_player, settings: {autopilot, messages, show_tg_name},
   start_tour, budget, created_at}`. `season`: `{status, tour_next, tour_now, deadline, first_tour}`,
   `null` — данные ещё не загрузились.

**Команда**
6. `POST /team` — только при `status: "open"` (в Прологе `409`). Необязательные `assistant` и
   `my_player`. Бюджет — 100 000 до дедлайна `first_tour`, позже — медиана «Отдашь за» активных
   команд (открывали «Звено» за 4 недели), не меньше 100 000.
7. «Мой игрок» (ADR-010) живёт в мини-аппе, а автопилот его не трогает только если сервер о нём
   знает: **мини-апп присылает `my_player` в `POST /team` и в `PUT /settings`, когда он меняется**.
8. Капитан — любой из основы. Ассистент — из тела запроса, если он в основе и не капитан, иначе сам:
   самая дорогая по формуле наклейка основы после капитана, без ворот. После обмена «К» и «А»
   переходят к наклейке, вставшей на место ушедшей.
9. Слот места закрепляется при покупке: если движок сменил наклейке амплуа, она остаётся на своём
   месте, а в `PUT /team/lineup` её можно ставить только на места прежнего слота.

**`Team`** — поля сверх контракта:
10. `unlimited: true` — обмены сейчас без ограничений и без оплаты: до первого своего дедлайна
    (у опоздавшего — до его первого дедлайна) и в тур с «Заливкой». Тогда `fee_options: []`, а `free`
    не тратится (до первого дедлайна он 0, после — 1).
11. `boost` — буст, включённый на этот тур (`"zalivka"` или `null`); `boosts.zalivka` — сколько
    осталось, после тура 11 — 0.
12. `hidden` — id из состава и очков, которых нет в пуле: показывать «игрок скрыт».
13. `start_tour` — первый тур команды. `points.subs` — `[[ушёл, пришёл], …]`, `points.penalty` — очки
    за платные обмены тура. У каждой наклейки в `points.by_id` — готовый `total` (с капитаном и
    сыгранностью, то же, что `points`): мини-апп правил очков не повторяет.
14. `points`: на `tour_next` — предпросмотр по текущему составу; на тур после дедлайна до закрытия —
    `provisional: true`, **без автозамен** (они — при закрытии); на закрытый тур — снимок:
    `provisional: false`, `lineup` и `bench` уже после автозамен, больше не меняется.
15. `value` — сумма «Отдашь за» всех наклеек, без кассы. `sale` и `bought` — только по своим
    наклейкам сейчас.
16. `mission` — `null` до тура 3 и в первый тур команды (альбом пуст — задание не требует действия).
    `done` на `tour_next` значит «засчитается, если в дедлайн основа будет такой». На прошедший тур —
    `{done, clubs_left: []}`.
17. `warnings[]` — `{id, text}`, где `id` может быть `null` (пустое место после скрытого игрока), а у
    предупреждений автопилота ещё `in` — кого он поставит. Тексты предупреждений и журнала — без
    склонения фамилий: «Максимов пропустил 4 матча подряд. Мишка заменит его в пн 09:00»; имя
    проводника — `mascot.name` любимого клуба из `teams.json`.
18. `GET /team?tour=N` без состава на тур N — `404`.

**Обмены**
19. `POST /team/transfer`: `out: null` — взять наклейку на пустое место её слота (после скрытого
    игрока). Порядок оплаты: без ограничений / «Заливка» / отдаёшь «отдыхающего» — бесплатно и без
    расхода обмена; иначе есть бесплатный — тратится он, какой бы ни была `pay`; иначе `pay` —
    `"points"` или `"ice"` по `fee_options`.
20. Скрытый игрок: наклейка уходит из состава сразу, в кассу — большее из цены покупки и последней
    стоимости, `free + 1` (сверх предела 5), запись в журнале. Автопилот в дедлайн заполняет пустое
    место бесплатно.
21. `POST /team/boost` `{boost: "zalivka"}`: до первого своего дедлайна не включается (и так без
    ограничений). Включённая посреди окна делает бесплатными и уже сделанные обмены тура: бесплатные
    обмены и 600 ❄ возвращаются, штраф очками снимается.
22. `POST /team/keep` `{id, keep: false}` — снять «Оставить». Ставится только на «отдыхающую»
    наклейку; перестаёт действовать, когда игрок вернулся или клуб сыграл без него ещё 4 матча.

**Лиги**
23. Id: `own:<n>`, `club:<клуб>`, `conf:west|east`, `step:<ГГГГ-ММ>:<ступень>:<группа>`,
    `month:<ГГГГ-ММ>`, `circle:1|2`, `overall`. В `GET /leagues` у своей — `code` и `owner`, у
    ступени — `step`, `group`, `month`, у месяца — `month`; у опоздавшего в общем — `since_tour`,
    `since_place`, `since_points` («с момента вступления»).
24. Очки в таблицах — только закрытые туры (снимки). Ступень — очки её месяца. Месяц — месяц
    последнего тура после дедлайна.
25. `GET /leagues/{id}` — первые 100 и своя строка. Чужая группа ступени, чужой клуб, лига без
    членства — `404`. В своей лиге у строки `tg_name`, если этот менеджер поставил галочку.
26. Код своей лиги — 6 знаков `A–Z` и `2–9` без похожих (`0/O`, `1/I`). `POST /leagues/join`
    принимает `lg-xxxxxx` в любом регистре; повторное вступление возвращает тот же `id`. Пределы:
    создать 10 своих лиг, состоять в 30, в лиге до 1000 команд.
27. Клубная лига — от 15 менеджеров с этим любимым клубом, иначе лига конференции клуба.

**Настройки и удаление**
28. `PUT /settings` — частично, любые из `autopilot`, `messages`, `show_tg_name`, `my_player`;
    отвечает объектом `manager`. Имя из Telegram хранится, только пока стоит галочка.
    `messages: true` снова включает сообщения, если бот был заблокирован.
29. `DELETE /me` стирает команду, составы, снимки очков, журнал, альбом, ступени, членство в лигах и
    останавливает сообщения. Своя лига остаётся другим участникам, пустая исчезает.

**Сообщения** (контракт, раздел 5)
30. Воскресенье 18:00–22:00 по поясу любимого клуба (`tz` в `teams.json`, без него — МСК): только
    если есть что решить — у игрока основы в туре нет матчей, у капитана нет матчей, кого заменит
    автопилот, кто «отдыхает» без автопилота, пустое место. Вторник 18:00–22:00 — история
    прошедшего тура и повышение в ступенях. Не больше двух в неделю (Пн–Вс), после трёх недель без
    открытия «Звена» — не чаще раза в две недели. Кнопка — мини-апп с `startapp=zveno`.

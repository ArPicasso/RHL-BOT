"""Бот РХЛ U21 2026/27 (@rhl_u21_bot, aiogram 3): встречает и ведёт в мини-апп, напоминает о матчах.

Весь интерфейс — в мини-аппе (ADR-003). У бота нет своей клавиатуры и меню команд: на всё он
отвечает стикером и кнопкой «Открыть РХЛ» (ADR-005). Вторым планом — матчи дня (`/today`) и
напоминания о любой команде лиги (`/team`, `/remind`) — ADR-019, раздел 8.
Для админов — счётчики и пульс в status/bot.json и `/admin` с кнопкой пульта (ADR-021),
`/replay` — опоры для повторов голов (ADR-027).
"""
import asyncio
import hashlib
import html
import json
import logging
import math
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import aiohttp
import admin
import clockrun
import cutjobs
import goalplan
import marks
import myplayer
import predict
import replay
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
                           InputMediaVideo, MenuButtonWebApp, Message, ReplyKeyboardRemove, WebAppInfo)
from raskat_store import RaskatStore

BASE = Path(__file__).parent
TZ = ZoneInfo("Europe/Moscow")
SUBS_FILE = BASE / "subscribers.json"      # {"<chat_id>": ["ryazan-vdv", …]} — кому о каких командах напоминать
ANNOUNCED_FILE = BASE / "announced.json"   # матчи, о которых уже написали после игры (ADR-008)
WAITLIST_FILE = BASE / "raskat_waitlist.json"   # кого позвать, когда в «Раскате» откроется зачёт
REMINDED_FILE = BASE / "reminded.json"     # какие напоминания уже ушли и кому: догон после простоя
# живые файлы матч-центра (ADR-019, раздел 5): пишет служба live (live.py) на том же сервере
LIVE_DIR = Path(os.environ.get("LIVE_DIR") or BASE / "live")
STICKERS = BASE / "stickers"          # стикеры бота (ADR-005), 512×512 WEBP
# мини-апп (ADR-003); переменная окружения — только чтобы подставить тестовый адрес
WEBAPP_URL = os.environ.get("WEBAPP_URL") or "https://arpicasso.github.io/RHL-BOT/"
REMIND_TODAY_AT = time(10, 0)      # утром в день игры
REMIND_TOMORROW_AT = time(19, 0)   # вечером накануне
REMIND_CATCHUP = timedelta(hours=3)   # бот стоял в свой час — догоняем, пока напоминание не устарело
# зовы (ADR-023): раскат дня — раз в день в это окно, прогноз — за это время до начала матча
STATE_DB = Path(os.environ.get("STATE_DB") or BASE / "state.db")
RASKAT_CALL_FROM, RASKAT_CALL_TO = time(11, 0), time(21, 0)
RASKAT_CALL_DAYS = 7               # не играл столько дней — не зовём: это уже не удержание
PREDICT_CALL_BEFORE = timedelta(hours=2)      # за столько до начала зовём голосовать
PREDICT_CALL_LAST = timedelta(minutes=15)     # ближе к свистку уже не успеть
GOALS_OFF_FILE = BASE / "goals_off.json"      # кто отказался от голов по ходу матча (ADR-024)
FEEDBACK_FILE = BASE / "feedback.json"        # кто нажал «написать живому человеку» (ADR-025)
FEEDBACK_WAIT = timedelta(hours=1)            # столько ждём само сообщение после нажатия
FEEDBACK_EVERY = timedelta(minutes=10)        # не чаще одного письма с чата
FEEDBACK_MAX = 1000                           # знаков: в чат админа не должна приезжать простыня
GOAL_FRESH = timedelta(minutes=10)            # гол старше — молча пропускаем: нужен счёт, а не лента
GOAL_BURST = 3                                # больше голов одного матча за проход не шлём
REMIND_TRIES = 6                   # столько раз возвращаемся к слоту, у которого были неудачи
CATCHUP_EVERY = 300                # как часто цикл напоминаний проверяет, не пропустил ли слот
REMINDED_KEEP = 3                  # дней истории напоминаний держим в reminded.json
RETRY_WAIT_MAX = 120               # «подожди столько-то» от Telegram: ждём, но не дольше этого
REMIND_TEAM = "Рязань-ВДВ"         # её календарь — games.json: запасной путь, если league.json не скачался
MAX_TEAMS = 3                      # до трёх команд на болельщика
RESULTS_POLL = 60                  # раз в минуту: live/ с диска, league.json — не чаще DATA_TTL
DATA_TTL = 600                     # опубликованные данные перечитываем не чаще раза в 10 минут
DATA_RETRY = 120                   # не скачалось — пробуем снова не раньше чем через 2 минуты
RESULTS_FRESH_DAYS = 2             # матчи старше не присылаем
LIVE_FINAL_HOLD = timedelta(minutes=10)   # «окончен» с одним счётом столько подряд — пишем финал
LIVE_STALE = timedelta(minutes=20)        # live/ без обновления дольше — ход матча не показываем
TODAY_MAX = 14                     # матчей в одном сообщении «Матчи сегодня»
STATUS_EVERY = 60                  # пульс для пульта (ADR-021): getMe через туннель и запись status/bot.json
ADMIN_IDS = frozenset(int(x) for x in re.split(r"[,\s]+", os.environ.get("ADMIN_IDS", "")) if x.isdigit())
ALERTS_FILE = admin.STATUS_DIR / admin.ALERTS_NAME   # список проблем пишет служба api (ADR-022)
ALERTED_FILE = BASE / "alerted.json"                 # о чём бот уже сказал админам: не повторяемся
TRACK = admin.Tracker("bot")       # счётчики за день для пульта: без id и имён
QUIET_FROM, QUIET_TO = time(23, 0), time(9, 0)   # ночью молчим, результат уйдёт утром
REPLAYS_FILE = LIVE_DIR / "replays.json"    # повторы голов (ADR-027): опоры админа и ссылки, отдаёт сервер API
REPLAY_DAYS = 3                    # матчи за столько дней, включая сегодня, можно разметить в /replay
REPLAY_WAIT = timedelta(minutes=30)   # столько ждём ссылку после нажатия на гол
REPLAY_NAG_AT = time(21, 0)        # раз в день после этого админам — матчи с записью лиги без разметки (ADR-028)
REPLAY_NAG_MAX = 8                 # кнопок на матчи в напоминании, дальше — «Все матчи»
# Превью голов (ADR-030, шаг 3): служба clips режет минуту-две записи до смены счёта на табло, бот присылает их
# админам и помощникам (PREVIEW_IDS — только превью, без тревог и /replay). Кто первым ответил, того и секунда
PREVIEW_IDS = frozenset(int(x) for x in re.split(r"[,\s]+", os.environ.get("PREVIEW_IDS", "")) if x.isdigit())
PREVIEWS_FILE = BASE / "previews.json"   # какие превью ушли и кому: «ключ|счёт» → сообщения, ответ. Не в git
PREVIEW_MAX = 4                    # превью за один проход пульса (раз в минуту): не заваливаем чат
PREVIEW_KEEP = timedelta(days=4)   # записи о превью держим столько
MY_PLAYER_WAIT = timedelta(hours=8)   # «Мой игрок»: клипа нет столько после начала матча — шлём гол без клипа
PREVIEW_V = 2                      # 05.10: превью уходили без длины и размера — «0:01» в чате; старые шлём заново
# Табло клуба-хозяина не размечено (ADR-030, дополнение 06.10): служба clips держит кадр с сеткой в probe/grids/,
# бот один раз присылает его админам файлом — по нему табло размечают в boards.json, и матчи клуба разбираются заново
GRIDS_FILE = BASE / "grids.json"   # кадры табло, которые ушли админам: клуб → когда и по какому матчу. Не в git
GRID_MAX = 2                       # кадров за один проход пульса
DISPUTES_FILE = BASE / "disputes.json"   # о каких спорах по отметкам уже написали (ADR-033): «ключ|счёт|секунда» → когда
DISPUTE_MAX = 4                    # споров за один проход пульса
DISPUTE_KEEP = timedelta(days=7)   # столько помним, что о споре написали
DISPUTE_CUT_WAIT = timedelta(minutes=15)   # видео спора не вырезано за столько — пишем о споре ссылками, как раньше

DOW = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def quiet(now: datetime) -> bool:
    """Ночь: ни напоминаний, ни результатов — всё уйдёт утром."""
    t = now.astimezone(TZ).time()
    return t >= QUIET_FROM or t < QUIET_TO


@dataclass(frozen=True)
class Game:
    n: int
    d: date
    opponent: str
    home: bool


GAMES = sorted(
    (Game(g["n"], date.fromisoformat(g["date"]), g["opponent"], g["home"])
     for g in json.loads((BASE / "games.json").read_text(encoding="utf-8"))),
    key=lambda g: g.d,
)
TEAM_LIST = json.loads((BASE / "teams.json").read_text(encoding="utf-8"))
# id команды → название: для диплинка /start <id> (ADR-005) и всех текстов
TEAMS = {t["id"]: t["name"] for t in TEAM_LIST}
TEAM_INFO = {t["id"]: t for t in TEAM_LIST}   # город и пояс арены (ADR-019), конференция
REMIND_TEAM_ID = next(i for i, name in TEAMS.items() if name == REMIND_TEAM)
CONFS = {"west": "Запад", "east": "Восток"}


def _norm(name: str) -> str:
    """Как build_data.norm: «МХК Белгород» и «Белгород» — одна команда."""
    name = name.lower().replace("ё", "е").replace("«", "").replace("»", "").replace('"', "")
    name = re.sub(r"\s*-\s*", "-", name)
    name = re.sub(r"^(мхк|хк)\s+", "", name.strip())
    return re.sub(r"\s+", " ", name)


TEAM_BY_NAME = {_norm(n): t["id"] for t in TEAM_LIST for n in [t["name"], *t.get("aliases", [])]}


def tname(team: str) -> str:
    """Название команды по id; не нашлось — как есть (соперник из games.json без id)."""
    return TEAMS.get(team, team)

# ---------- свои эмодзи ----------
# Набор rhl_u21_by_<бот> (tools/upload_emoji.py) в порядке stickers/emoji.json. Писать ими бот
# может, пока у владельца бота есть Telegram Premium (Bot API, MessageEntity). Не вышло —
# emoji_off() и то же сообщение обычными эмодзи.

EMOJI = json.loads((STICKERS / "emoji.json").read_text(encoding="utf-8"))   # имя → обычный эмодзи
CUSTOM: dict[str, str] = {}   # имя → custom_emoji_id, заполняет load_custom_emoji() при запуске


def custom_ids(set_stickers: list) -> dict[str, str]:
    """Сопоставить стикеры набора именам из emoji.json: порядок тот же, в каком их загрузили."""
    return {n: st.custom_emoji_id for n, st in zip(EMOJI, set_stickers) if st.custom_emoji_id}


def e(name: str) -> str:
    """Свой эмодзи, если набор загружен, иначе обычный."""
    plain = EMOJI[name]
    if name in CUSTOM:
        return f'<tg-emoji emoji-id="{CUSTOM[name]}">{plain}</tg-emoji>'
    return plain


def emoji_off(err: Exception) -> bool:
    """Telegram не принял свои эмодзи: дальше пишем обычными. True — есть смысл повторить."""
    if not CUSTOM:
        return False
    logging.warning("custom emoji off: %s", err)
    CUSTOM.clear()
    return True

# ---------- тексты и кнопки ----------

B_APP = "Открыть РХЛ"
B_RECAP = "Как это было"
B_GOALS = "Голы матча"
B_GOAL = "Гол в приложении"
B_PLAYER = "Страница игрока"
B_LEADERS = "Все лидеры"
B_RASKAT = "Собрать раскат"
B_WAIT_ON = "Позвать, когда откроется"
B_WAIT_OFF = "Больше не звать"
B_TODAY = "Матчи сегодня"
B_WRITE = "Написать живому человеку"
B_PREDICT = "Кто победит?"
B_MATCH = "Матч в приложении"
B_ONLINE = "Текстовая трансляция"

# видно в пустом чате до «Старт» и в профиле бота (до 512 и 120 символов). Что мы не лига — ADR-021
DESCRIPTION = ("Неофициальный бот болельщиков Первенства России U21 — РХЛ 2026/27.\n\n"
               "📅 Матчи дня: время начала, счёт по ходу игры, текстовые трансляции\n"
               "🔔 Напомню о матчах любой команды лиги — накануне и в день игры, пришлю счёт\n"
               "🏒 Календарь 26 команд, таблица и лидеры — в приложении\n"
               "🏑 Раскат дня: головоломка про шайбу на пару минут\n\n"
               "Сделан болельщиками, не связан с РХЛ и ФХР.\n\n"
               "Жми «Старт» 👇")
SHORT_DESCRIPTION = "Неофициальный бот болельщиков РХЛ U21: матчи дня, счёт, трансляции и напоминания о твоей команде 🏒"


def app_url(team: str | None = None, match: str | None = None, view: str | None = None,
            startapp: str | None = None) -> str:
    """Адрес мини-аппа; с командой — ?team=<id>, мини-апп выберет её, если своей ещё нет.
    С матчем — ?match=<id>, мини-апп сразу откроет его карточку (ADR-008).
    С view=leaders — сразу «Таблица → Игроки» (ADR-009), с матчем и view=goals — его голы клипами подряд (ADR-030).
    С startapp=raskat — сразу «Раскат» (контракт «Раската», раздел 6)."""
    extra = [(k, v) for k, v in (("team", team), ("match", match), ("view", view),
                                 ("startapp", startapp)) if v]
    if not extra:
        return WEBAPP_URL
    u = urlsplit(WEBAPP_URL)
    return urlunsplit(u._replace(query=urlencode(parse_qsl(u.query) + extra)))


def btn(text: str, icon: str | None = None, **kw) -> InlineKeyboardButton:
    """Кнопка со своим эмодзи-значком, если набор загружен, иначе с обычным эмодзи в тексте."""
    if icon and icon in CUSTOM:
        return InlineKeyboardButton(text=text, icon_custom_emoji_id=CUSTOM[icon], **kw)
    return InlineKeyboardButton(text=f"{EMOJI[icon]} {text}" if icon else text, **kw)


def app_kb(team: str | None = None, today: bool = False) -> InlineKeyboardMarkup:
    """Одна большая кнопка — открыть мини-апп. today — вторым планом «Матчи сегодня» (ADR-019)."""
    rows = [[btn(B_APP, "puck", web_app=WebAppInfo(url=app_url(team)))]]
    if today:
        rows.append([InlineKeyboardButton(text=f"📅 {B_TODAY}", callback_data="d:today")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def recap_kb(match: str, recap: bool = True) -> InlineKeyboardMarkup:
    """Карточка сыгранного матча в мини-аппе. С протоколом — ещё «Голы матча» (ADR-030, раздел 6): мини-апп
    открывает разбор и играет клипы голов подряд; клипов ещё нет — просто голы с «Повтором». Протокола ещё нет —
    одна кнопка «Матч в приложении»: разбора «Как это было» там пока нет, только счёт."""
    rows = [[btn(B_RECAP if recap else B_MATCH, "goal", web_app=WebAppInfo(url=app_url(match=match)))]]
    if recap:
        reel = WebAppInfo(url=app_url(match=match, view="goals"))
        rows.append([InlineKeyboardButton(text=f"🎬 {B_GOALS}", web_app=reel)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def leaders_kb() -> InlineKeyboardMarkup:
    """Одна кнопка — лидеры лиги в мини-аппе."""
    return InlineKeyboardMarkup(inline_keyboard=[[btn(B_LEADERS, "cup", web_app=WebAppInfo(url=app_url(view="leaders")))]])


def raskat_kb(chat_id: int | None = None) -> InlineKeyboardMarkup:
    """Кнопка «Раската» (startapp=raskat). Пока зачёта нет — вторым планом лист ожидания."""
    rows = [[btn(B_RASKAT, "stick", web_app=WebAppInfo(url=app_url(startapp="raskat")))]]
    if chat_id is not None and not raskat_api():
        waiting = chat_id in WAITLIST
        rows.append([InlineKeyboardButton(text=f"🔕 {B_WAIT_OFF}" if waiting else f"🔔 {B_WAIT_ON}",
                                          callback_data="rs:wait")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _pm(v: int) -> str:
    return f"+{v}" if v > 0 else f"−{-v}" if v < 0 else "0"


def leaders_text(data: dict | None, season: str = "2026/27") -> str:
    """Коротко о лидерах лиги (ADR-009): тройка бомбардиров и первый в остальных списках.
    data — опубликованный мини-аппом data/leaders.json; нет его — просто ведём в приложение."""
    cats = (data or {}).get("categories") or {}
    if not cats.get("pts"):
        return f"{e('cup')} Лучшие игроки лиги — в приложении.\nЖми кнопку 👇"

    def who(r: dict) -> str:
        club = TEAMS.get(r.get("team"), r.get("club", ""))
        return f"{html.escape(r['name'])} ({html.escape(club)})" if club else html.escape(r["name"])

    past = data.get("season") != season
    head = f"{e('cup')} <b>Лидеры {html.escape(data.get('league', ''))} {html.escape(data.get('season', ''))}</b>"
    lines = [head + (" · прошлый сезон" if past else ""), "", "<b>Бомбардиры</b>"]
    lines += [f"{r['rank']}. {who(r)} — {r['pts']} {plural(r['pts'], 'очко', 'очка', 'очков')}"
              for r in cats["pts"] if r["rank"] <= 3]
    firsts = [("g", "Снайпер", lambda r: f"{r['g']} {plural(r['g'], 'гол', 'гола', 'голов')}"),
              ("pm", "Плюс-минус", lambda r: _pm(r["pm"])),
              ("sv_pct", "Вратарь", lambda r: f"{r['sv_pct']:.1f}% отражённых".replace(".", ","))]
    top = [(label, next((r for r in cats.get(k, []) if r["rank"] == 1), None), fmt) for k, label, fmt in firsts]
    if any(r for _, r, _ in top):
        lines.append("")
        lines += [f"{label}: {who(r)} — {fmt(r)}" for label, r, fmt in top if r]
    if past:
        lines += ["", f"Лидеры сезона {season} появятся после первого тура."]
    return "\n".join(lines) + "\n\nСнайперы, ассистенты, вратари и штраф — топ-10 по кнопке 👇"


def plural(n: int, one: str, few: str, many: str) -> str:
    a, b = n % 10, n % 100
    if a == 1 and b != 11:
        return one
    return few if 2 <= a <= 4 and not 12 <= b <= 14 else many


def welcome_text(team: str | None = None) -> str:
    head = (f"Здарова! Открываю РХЛ с командой <b>«{html.escape(TEAMS[team])}»</b> {e('rhl')}" if team
            else f"Здарова! Это РХЛ U21 — всё про лигу в одном месте {e('rhl')}")
    return (f"{head}\n\n"
            f"{e('goal')} Матчи дня: время, счёт и трансляции\n"
            f"{e('star')} Календарь и таблица 26 команд\n"
            f"{e('fire')} Лучшие игроки лиги\n"
            f"{e('stick')} Раскат дня — головоломка про шайбу\n"
            f"{e('bell')} Напомню о матчах твоей команды — /team\n\n"
            "<b>Жми «Открыть РХЛ»</b> 👇 и выбери, за кого болеешь.\n\n"
            "<i>Приложение болельщиков для болельщиков — не официальное приложение РХЛ.</i>")


def lost_text() -> str:
    return f"Всё самое интересное — в приложении {e('fire')}\nЖми кнопку 👇"


def quoted(teams: list[str]) -> str:
    """«Рязань-ВДВ», «Белгород» и «Самара»."""
    q = [f"«{html.escape(tname(t))}»" for t in teams]
    return q[0] if len(q) == 1 else ", ".join(q[:-1]) + " и " + q[-1]

# ---------- напоминания: подписчики ----------
# subscribers.json — {"<chat_id>": ["<id команды>", …]}, до MAX_TEAMS команд. Старый формат
# [chat_id, …] — подписка на «Рязань-ВДВ»: читаем и тихо переписываем в новый. Выключил
# напоминания или заблокировал бота — chat_id удаляется из файла целиком (CLAUDE.md, правило 4).

def parse_subs(raw) -> tuple[dict[int, list[str]], bool]:
    """Подписчики из файла и нужно ли переписать файл (был старый формат или мусор)."""
    subs: dict[int, list[str]] = {}
    if isinstance(raw, list):   # до подписки на любую команду: все — за «Рязань-ВДВ»
        for x in raw:
            try:
                subs[int(x)] = [REMIND_TEAM_ID]
            except (TypeError, ValueError):
                pass
        return subs, True
    if not isinstance(raw, dict):
        return subs, True
    dirty = False
    for k, v in raw.items():
        teams = v if isinstance(v, list) else [v] if isinstance(v, str) else []
        clean = list(dict.fromkeys(t for t in teams if t in TEAMS))[:MAX_TEAMS]
        dirty |= clean != v or not clean
        try:
            if clean:
                subs[int(k)] = clean
        except (TypeError, ValueError):
            dirty = True
    return subs, dirty


def load_subs() -> dict[int, list[str]]:
    try:
        raw = json.loads(SUBS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    subs, dirty = parse_subs(raw)
    if dirty:
        save_subs(subs)
        logging.info("subscribers.json переписан в новый формат: %d", len(subs))
    return subs


def write_atomic(path: Path, data) -> None:
    """Записать JSON так, чтобы падение посреди записи не оставило половину файла: пишем рядом,
    сбрасываем на диск и переименовываем — переименование атомарно."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def save_subs(subs: dict[int, list[str]]) -> None:
    write_atomic(SUBS_FILE, {str(k): subs[k] for k in sorted(subs)})


SUBS = load_subs()


def set_teams(chat_id: int, teams: list[str]) -> None:
    """Пустой список — подписка снимается целиком: chat_id больше нигде не хранится."""
    if teams:
        if chat_id not in SUBS:
            TRACK.add("sub_new")
        SUBS[chat_id] = list(teams)
    elif chat_id not in SUBS:
        return
    else:
        SUBS.pop(chat_id)
        TRACK.add("sub_off")
    save_subs(SUBS)


def follow(chat_id: int, team: str) -> str:
    """Напоминать о команде: on — включили, already — уже было, full — уже MAX_TEAMS команд."""
    teams = SUBS.get(chat_id) or []
    if team in teams:
        return "already"
    if len(teams) >= MAX_TEAMS:
        return "full"
    set_teams(chat_id, [*teams, team])
    return "on"


def toggle_team(chat_id: int, team: str) -> str:
    """Кнопка команды в выборе: on, off или full, если уже MAX_TEAMS и эта не выбрана."""
    teams = SUBS.get(chat_id) or []
    if team in teams:
        set_teams(chat_id, [t for t in teams if t != team])
        return "off"
    return follow(chat_id, team)


def unsubscribe(chat_id: int, blocked: bool = False) -> None:
    """blocked — бота заблокировали: на пульте отдельно от «Выключить». «Выключить» и блокировка стирают и
    «Моего игрока» (ADR-030, раздел 6): бот больше ничего не присылает."""
    if blocked and chat_id in SUBS:
        TRACK.add("blocked")
    set_teams(chat_id, [])
    my_player_forget(chat_id)


def turn_on(chat_id: int, team: str = REMIND_TEAM_ID) -> str:
    return follow(chat_id, team)


def load_goals_off() -> set[int]:
    """Кто отказался от голов по ходу матча (ADR-024, раздел 2). Файла нет — никто."""
    try:
        return {int(x) for x in json.loads(GOALS_OFF_FILE.read_text(encoding="utf-8"))}
    except (FileNotFoundError, ValueError, TypeError):
        return set()


GOALS_OFF = load_goals_off()


def goals_on(chat_id: int) -> bool:
    return chat_id not in GOALS_OFF


def goals_set(chat_id: int, on: bool) -> bool:
    """Включить или выключить голы. Возвращает, как стало."""
    if on == (chat_id in GOALS_OFF):
        GOALS_OFF.symmetric_difference_update({chat_id})
        write_atomic(GOALS_OFF_FILE, sorted(GOALS_OFF))
        TRACK.add("goals_on" if on else "goals_off")
    return on


def remind_kb(chat_id: int) -> InlineKeyboardMarkup:
    """Включены — «Выключить», «Команды» и выключатель голов; выключены — сразу выбор конференции."""
    if not SUBS.get(chat_id):
        return team_kb(chat_id)
    goals = goals_on(chat_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔕 Выключить", callback_data="r:toggle"),
         InlineKeyboardButton(text="✏️ Команды", callback_data="t:home")],
        [InlineKeyboardButton(text="🥅 Голы: выключить" if goals else "🥅 Голы: включить",
                              callback_data="g:toggle")]])


def next_game(today: date) -> Game | None:
    return next((g for g in GAMES if g.d >= today), None)


def local_match(g: Game) -> dict:
    """Матч из games.json в виде матча league.json: запасной путь, когда Pages не ответили."""
    opp = TEAM_BY_NAME.get(_norm(g.opponent), g.opponent)
    home, away = (REMIND_TEAM_ID, opp) if g.home else (opp, REMIND_TEAM_ID)
    return {"id": f"n{g.n}", "key": f"{g.d.isoformat()}|{home}|{away}", "date": g.d.isoformat(),
            "home": home, "away": away}


def games_of(league: dict | None) -> list[dict]:
    """Матчи сезона: из league.json, а нет его — «Рязань-ВДВ» по games.json."""
    games = (league or {}).get("games")
    if isinstance(games, list) and games:
        return [g for g in games if isinstance(g, dict)]
    return [local_match(g) for g in GAMES]


def next_match(team: str, today: date, games: list[dict]) -> dict | None:
    """Ближайший не сыгранный матч команды начиная с сегодня."""
    d = today.isoformat()
    ms = [g for g in games if team in (g.get("home"), g.get("away")) and str(g.get("date", "")) >= d
          and not g.get("score")]
    return min(ms, key=lambda g: (g["date"], (start_of(g) or datetime.max.replace(tzinfo=TZ)).timestamp()),
               default=None)


def nearest_text(team: str, m: dict, with_team: bool) -> str:
    d = date.fromisoformat(m["date"])
    start = start_of(m)
    at = f" в {start:%H:%M} МСК" if start else ""
    home = m["home"] == team
    opp = m["away"] if home else m["home"]
    who = f"«{html.escape(tname(team))}»: " if with_team else ""
    return f"{who}{DOW[d.weekday()]} {d:%d.%m}{at}, {'дома' if home else 'в гостях'} с «{html.escape(tname(opp))}»"


def remind_text(chat_id: int, today: date | None = None, games: list[dict] | None = None, note: str = "") -> str:
    """Состояние напоминаний. Включены — команды и ближайшие игры, чтобы было видно, о чём напомню."""
    teams = SUBS.get(chat_id) or []
    when = f"накануне в {REMIND_TOMORROW_AT:%H:%M} и в день игры в {REMIND_TODAY_AT:%H:%M} (МСК)"
    head = f"{note}\n\n" if note else ""
    if not teams:
        return (f"{head}{e('bell')} Напоминания о матчах ❌ выключены\n\n"
                f"Выбери команду — напомню {when}, а после игры пришлю счёт. Можно до трёх.")
    goals = ("Голы по ходу матча пришлю тоже." if goals_on(chat_id)
             else "Голы по ходу матча не присылаю.")
    text = (f"{head}{e('bell')} Напоминания о матчах {quoted(teams)} ✅ включены\n\n"
            f"Пришлю сообщение {when}, а после игры — счёт. {goals}")
    today = today or datetime.now(TZ).date()
    games = games if games is not None else games_of(None)
    near = [(t, m) for t in teams if (m := next_match(t, today, games))]
    if len(teams) == 1 and near:
        text += f"\n\nБлижайшая: {nearest_text(*near[0], with_team=False)}"
    elif near:
        text += "\n\nБлижайшие:\n" + "\n".join(nearest_text(t, m, with_team=True) for t, m in near)
    return text

# ---------- выбор команды (/team) ----------

def team_text(chat_id: int, conf: str | None = None) -> str:
    teams = SUBS.get(chat_id) or []
    now = f"Сейчас: {quoted(teams)}." if teams else "Пока ни одной."
    if conf in CONFS:
        return (f"{e('bell')} <b>{CONFS[conf]}</b>\n\nЖми на команду — ✅ значит, уже напоминаю. "
                f"Можно до трёх.\n{now}")
    return (f"{e('bell')} <b>За кого болеешь?</b>\n\nВыбери до трёх команд — напомню об их матчах "
            f"накануне и в день игры, а после пришлю счёт.\n{now}")


def team_kb(chat_id: int, conf: str | None = None) -> InlineKeyboardMarkup:
    """Без conf — две конференции; с conf — её команды по две в ряд, выбранные с ✅."""
    teams = SUBS.get(chat_id) or []
    if conf not in CONFS:
        rows = [[InlineKeyboardButton(text=label, callback_data=f"t:c:{c}") for c, label in CONFS.items()]]
        if teams:
            rows.append([InlineKeyboardButton(text="✅ Готово", callback_data="r:show")])
        return InlineKeyboardMarkup(inline_keyboard=rows)
    ids = sorted((t["id"] for t in TEAM_LIST if t.get("conf") == conf), key=lambda i: TEAMS[i].replace("Ё", "Е"))
    buttons = [InlineKeyboardButton(text=("✅ " if i in teams else "") + TEAMS[i], callback_data=f"t:s:{i}")
               for i in ids]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="← Конференции", callback_data="t:home"),
                 InlineKeyboardButton(text="Готово", callback_data="r:show")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ---------- матчи дня: league.json + live/ (ADR-019, разделы 2 и 5) ----------

LIVE_ON = ("live", "break")
LIVE_DONE = ("ended", "final")
PERIODS = {"1": "1-й период", "2": "2-й период", "3": "3-й период", "ОТ": "овертайм", "РБ": "буллиты"}
HM_RE = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")


def match_key(m: dict) -> str:
    """Ключ матча, как у live.py: <дата>|<хозяева>|<гости>."""
    return f"{m.get('date')}|{m.get('home')}|{m.get('away')}"


def read_live(name: str) -> dict | None:
    """Файл из LIVE_DIR. Нет файла или он битый — живого нет."""
    try:
        data = json.loads((LIVE_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _dt(v) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    return dt.astimezone(TZ) if dt.tzinfo else None


def start_of(m: dict) -> datetime | None:
    """Начало матча по Москве: start с поясом, а нет его — time («17:00» МСК) в день матча."""
    if dt := _dt(m.get("start")):
        return dt
    hm = HM_RE.fullmatch(str(m.get("time") or "").strip())
    try:
        d = date.fromisoformat(str(m.get("date")))
    except ValueError:
        return None
    return datetime.combine(d, time(int(hm.group(1)), int(hm.group(2))), TZ) if hm else None


def local_hm(m: dict, start: datetime | None) -> str | None:
    """Местное время арены хозяев, если её пояс не московский: «21:00»."""
    if HM_RE.fullmatch(str(m.get("local") or "")):
        return m["local"]
    tz = (TEAM_INFO.get(m.get("home")) or {}).get("tz")
    if not start or not tz:
        return None
    try:
        loc = start.astimezone(ZoneInfo(tz))
    except (KeyError, ValueError):   # пояс не узнан
        return None
    return loc.strftime("%H:%M") if loc.utcoffset() != start.utcoffset() else None


def _url(v) -> str | None:
    return v if isinstance(v, str) and v.startswith(("https://", "http://")) else None


def _score(sc) -> dict | None:
    if not isinstance(sc, dict) or not all(isinstance(sc.get(k), int) for k in ("home", "away")):
        return None
    return {"home": sc["home"], "away": sc["away"], "decision": sc.get("decision") or ""}


def live_fresh(live: dict | None, now: datetime) -> bool:
    """Служба live жива: файл обновлялся не позже LIVE_STALE назад."""
    at = _dt((live or {}).get("updated"))
    return bool(at) and now - at <= LIVE_STALE


SITE = "rhl.fhr.ru"
SITE_MC_RE = re.compile(r"https://rhl\.fhr\.ru/matchcenter/\d+/\d+/")
SRC_GEN = {SITE: "сайта лиги", "online.khl.ru": "онлайна лиги"}


def site_only(g: dict) -> bool:
    """Счёт сыгранного матча есть только с ленты сайта лиги: протокола (периодов, голов) у нас ещё нет."""
    sc = g.get("score") or {}
    return g.get("score_src") == SITE and not sc.get("periods")


def protocol_url(g: dict) -> str | None:
    """Протокол матча на сайте лиги: из данных, а нет — вкладка «Протокол» его матч-центра."""
    if url := _url(g.get("protocol")):
        return url
    base = g.get("league_url")
    return f"{base}protocol/" if isinstance(base, str) and SITE_MC_RE.fullmatch(base) else None


def day_matches(day: date, league: dict | None, live: dict | None = None, schedule: dict | None = None,
                now: datetime | None = None) -> list[dict]:
    """Матчи лиги за день одним списком: календарь league.json, время и онлайн из schedule.json,
    ход матча из live/<дата>.json. Старшинство — ADR-019, раздел 2: время и ссылка на онлайн —
    живое, потом schedule.json, потом league.json; итог — протокол, потом «окончен» онлайна.
    Живое старше LIVE_STALE даёт только время, ссылку и итог."""
    now = now or datetime.now(TZ)
    d = day.isoformat()
    rows: dict[str, dict] = {}

    def ok(x) -> bool:
        return isinstance(x, dict) and x.get("date") == d and isinstance(x.get("home"), str) \
            and isinstance(x.get("away"), str)

    def row(x: dict) -> dict:
        k = match_key(x)
        return rows.setdefault(k, {"key": k, "id": None, "date": d, "home": x["home"], "away": x["away"],
                                   "watch": [], "status": None, "period": None, "score": None, "src": None,
                                   "protocol_url": None})

    def timed(r: dict, x: dict) -> None:
        if x.get("time") or x.get("start"):
            r["time"], r["start"] = x.get("time"), x.get("start")
            r.pop("local", None)          # местное пересчитаем от нового начала
        if _url(x.get("online")):
            r["online"] = x["online"]

    for g in (league or {}).get("games") or []:
        if not ok(g):
            continue
        r = row(g)
        r["id"] = g.get("id")
        timed(r, g)
        if HM_RE.fullmatch(str(g.get("local") or "")):
            r["local"] = g["local"]
        r["watch"] = [w for w in g.get("watch") or [] if isinstance(w, dict) and _url(w.get("url"))]
        if sc := _score(g.get("score")):
            r.update(score=sc, status="final", protocol=True, src=SITE if site_only(g) else None,
                     protocol_url=protocol_url(g))
        elif isinstance(g.get("live"), dict):
            r["snapshot"] = g["live"]     # снимок идущего из часовой сборки — если службы live нет
    for x in (schedule or {}).get("games") or []:
        if ok(x):
            timed(row(x), x)
    seen_live: set[str] = set()
    if live and live.get("date") == d:
        fresh = live_fresh(live, now)
        for x in live.get("games") or []:
            if not ok(x):
                continue
            r = row(x)
            timed(r, x)
            if r.get("protocol"):         # итог из league.json главнее живого
                continue
            st, sc = x.get("status"), _score(x.get("score"))
            if st in LIVE_DONE and sc:
                r.update(status="ended", score=sc, period=None, src=x.get("src"),
                         protocol_url=_url(x.get("protocol")) or r.get("protocol_url"))
            elif st in LIVE_ON and fresh:
                r.update(status=st, score=sc, period=x.get("period"), src=x.get("src"))
            elif st in ("moved", "off"):
                r.update(status=st, score=None)
            if fresh and st:
                seen_live.add(r["key"])
    # Служба live молчит — снимок идущего матча с сайта лиги из league.json, если он не старше LIVE_STALE
    for r in rows.values():
        snap = r.pop("snapshot", None)
        if not snap or r["status"] or r["key"] in seen_live:
            continue
        seen = _dt(snap.get("seen"))
        if snap.get("status") in LIVE_ON and seen and now - seen <= LIVE_STALE:
            r.update(status=snap["status"], score=_score(snap.get("score")), period=snap.get("period"),
                     src=snap.get("src"), seen=seen)
    return list(rows.values())


def sort_matches(ms: list[dict], fans: list[str] = ()) -> list[dict]:
    """Матчи своих команд первыми, дальше по времени начала; без времени — в конце."""
    def key(m):
        s = start_of(m)
        return (not (m["home"] in fans or m["away"] in fans), s is None, s.timestamp() if s else 0,
                tname(m["home"]))
    return sorted(ms, key=key)


def next_game_day(today: date, *sources: dict | None) -> date | None:
    days = set()
    for src in sources:
        for g in (src or {}).get("games") or []:
            try:
                d = date.fromisoformat(str((g or {}).get("date")))
            except ValueError:
                continue
            if d > today:
                days.add(d)
    return min(days, default=None)


def score_html(sc: dict) -> str:
    dec = f" ({sc['decision']})" if sc.get("decision") else ""
    return f"<b>{sc['home']}:{sc['away']}</b>{dec}"


def status_text(m: dict, now: datetime) -> str:
    """«идёт · 2-й период · 2:1», «перерыв · 1:1», «окончен 4:2», «через 40 мин» или пусто."""
    st, sc = m.get("status"), m.get("score")
    if st in LIVE_DONE and sc:
        return f"окончен {score_html(sc)}"
    if st in LIVE_ON:
        parts = ["идёт" if st == "live" else "перерыв"]
        if st == "live" and m.get("period") in PERIODS:
            parts.append(PERIODS[m["period"]])
        if sc:
            parts.append(score_html(sc))
        return " · ".join(parts)
    if st == "moved":
        return "перенесён"
    if st == "off":
        return "отменён"
    start = start_of(m)
    if start and now < start <= now + timedelta(hours=1):
        return f"через {math.ceil((start - now).total_seconds() / 60)} мин"
    return ""


def links_html(m: dict) -> list[str]:
    """До и во время матча — текстовая трансляция, после — протокол на сайте лиги; и видео, если есть."""
    out = []
    if m.get("status") in LIVE_DONE:
        if url := _url(m.get("protocol_url")):
            out.append(f'<a href="{html.escape(url)}">протокол</a>')
    elif url := _url(m.get("online")):
        out.append(f'<a href="{html.escape(url)}">текстовая трансляция</a>')
    if m.get("watch"):
        out.append(f'<a href="{html.escape(m["watch"][0]["url"])}">смотреть</a>')
    return out


def score_source(ms: list[dict]) -> str:
    """Откуда живой счёт (по ходу и «окончен» до итога в league.json): «сайту лиги», «онлайну лиги»
    или обоим. Живого счёта в списке нет — пусто: итоги взяты из опубликованных данных."""
    srcs = {m.get("src") or "online.khl.ru" for m in ms if m.get("status") in LIVE_ON + ("ended",)}
    names = [n for s, n in ((SITE, "сайту"), ("online.khl.ru", "онлайну")) if s in srcs]
    return f"{' и '.join(names)} лиги" if names else ""


def today_line(m: dict, mine: bool, now: datetime) -> str:
    start = start_of(m)
    if start:
        when = f"<b>{start:%H:%M}</b>"
        if loc := local_hm(m, start):
            when += f" (местное {loc})"
    else:
        when = "время уточняется"
    star = f"{e('star')} " if mine else ""
    head = f"{star}{when} · {html.escape(tname(m['home']))} — {html.escape(tname(m['away']))}"
    tail = [s for s in (status_text(m, now),) if s] + links_html(m)
    return head + ("\n" + " · ".join(tail) if tail else "")


def day_label(d: date, today: date) -> str:
    base = f"{DOW[d.weekday()]} {d:%d.%m}"
    return f"завтра, {base}" if d == today + timedelta(days=1) else base


def today_text(league: dict | None, live: dict | None = None, schedule: dict | None = None,
               fans: list[str] = (), now: datetime | None = None) -> str:
    """«Матчи сегодня» (ADR-019, раздел 8): все матчи лиги за день, свои первыми, у каждого время
    по Москве, статус и счёт по live/today.json, ссылки на трансляции. Нет матчей — ближайший день."""
    now = now or datetime.now(TZ)
    today = now.date()
    ms = day_matches(today, league, live, schedule, now)
    if ms:
        lines = [f"{e('puck')} <b>Матчи РХЛ сегодня · {DOW[today.weekday()]} {today:%d.%m}</b>", "Время московское"]
    elif not league and not (schedule or {}).get("games"):
        return f"{e('puck')} Не получилось загрузить календарь лиги.\nВсе матчи — в приложении 👇"
    else:
        nd = next_game_day(today, league, schedule)
        if not nd:
            return f"{e('puck')} Сегодня матчей в РХЛ нет, а следующих в календаре пока не видно.\nКалендарь — в приложении 👇"
        ms = day_matches(nd, league, None, schedule, now)
        lines = [f"{e('puck')} Сегодня матчей в РХЛ нет.", f"<b>Ближайшие — {day_label(nd, today)}</b>",
                 "Время московское"]
    ms = sort_matches(ms, list(fans))
    for m in ms[:TODAY_MAX]:
        lines += ["", today_line(m, m["home"] in fans or m["away"] in fans, now)]
    if len(ms) > TODAY_MAX:
        lines += ["", f"И ещё {len(ms) - TODAY_MAX} — в приложении."]
    lines.append("")
    if ms and ms[0]["date"] == today.isoformat() and live_fresh(live, now) and (src := score_source(ms)):
        lines.append(f"Счёт — по {src} на {_dt(live['updated']):%H:%M}.")
    elif seen := [m["seen"] for m in ms if m.get("seen")]:     # снимок сайта лиги из сборки
        lines.append(f"Счёт по ходу — по {score_source(ms) or 'сайту лиги'} на {min(seen):%H:%M}.")
    lines.append("Карточки матчей — в приложении 👇")
    return "\n".join(lines)


def today_kb(chat_id: int | None = None) -> InlineKeyboardMarkup:
    rows = app_kb().inline_keyboard
    following = bool(SUBS.get(chat_id))
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="d:refresh"),
                 InlineKeyboardButton(text="🔔 Мои команды" if following else "🔔 Напоминать",
                                      callback_data="r:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ---------- напоминания о матче ----------

def recipients(subs: dict[int, list[str]], m: dict) -> list[tuple[int, str | None]]:
    """Кому писать о матче и от чьего лица: команда болельщика, None — болеет за обе."""
    out = []
    for cid, teams in sorted(subs.items()):
        mine = [t for t in teams if t in (m["home"], m["away"])]
        if mine:
            out.append((cid, mine[0] if len(mine) == 1 else None))
    return out


def reminder_plan(subs: dict[int, list[str]], day: date, league: dict | None, live: dict | None = None,
                  schedule: dict | None = None, now: datetime | None = None) -> list[tuple[int, dict, str | None]]:
    """Кому о каком матче дня напомнить. league.json не скачался — «Рязань-ВДВ» по games.json
    (и что знает schedule.json сервера)."""
    base = league if (league or {}).get("games") else {"games": games_of(None)}
    ms = sort_matches(day_matches(day, base, live, schedule, now))
    return [(cid, m, team) for m in ms for cid, team in recipients(subs, m)]


def _won(g: dict, team: str) -> bool:
    sc = g["score"]
    return (sc["home"] > sc["away"]) == (g["home"] == team)


def warmup_text(m: dict, team: str | None, games: list[dict] | None) -> str:
    """Подогрев одной строкой: прошлая встреча в сезоне, а нет её — форма команды за 5 матчей."""
    played = [g for g in games or [] if _score(g.get("score")) and str(g.get("date", "")) < m["date"]]
    pair = {m["home"], m["away"]}
    met = [g for g in played if {g.get("home"), g.get("away")} == pair]
    if met:
        g = max(met, key=lambda g: g["date"])
        d = date.fromisoformat(g["date"])
        return (f"{e('fire')} Прошлая встреча {d:%d.%m}: {html.escape(tname(g['home']))} "
                f"{score_html(_score(g['score']))} {html.escape(tname(g['away']))}")
    t = team if team in pair else m["home"]
    form = sorted((g for g in played if t in (g.get("home"), g.get("away"))), key=lambda g: g["date"])[-5:]
    if not form:
        return ""
    return f"{e('fire')} Форма «{html.escape(tname(t))}»: " + " ".join(e("win") if _won(g, t) else e("loss") for g in form)


def reminder_text(m: dict | Game, kind: str, team: str | None = REMIND_TEAM_ID,
                  games: list[dict] | None = None) -> str:
    """Напоминание о матче: когда (время МСК и местное), где, где смотреть и строка подогрева."""
    if isinstance(m, Game):
        m = local_match(m)
    d = date.fromisoformat(m["date"])
    head = "Сегодня игра!" if kind == "today" else "Завтра игра!"
    word = "сегодня" if kind == "today" else "завтра"
    start = start_of(m)
    if start:
        when = f"{word} в {start:%H:%M} МСК"
        if loc := local_hm(m, start):
            when += f" · {loc} по местному"
    else:
        when = f"{word}, время начала уточняется"
    home, away = m["home"], m["away"]
    city = (TEAM_INFO.get(home) or {}).get("city")
    at = f", {html.escape(city)}" if city else ""
    teams_line = teams_html(m, team)
    if team in (home, away):
        where = (f"{e('home')} Дома" if team == home else f"{e('away')} На выезде") + at
    else:
        where = f"📍 {html.escape(city)}" if city else ""
    lines = [f"{e('bell')} <b>{head}</b>", "", teams_line, f"{DOW[d.weekday()]} {d:%d.%m} · {when}"]
    lines += [where] if where else []
    if m.get("watch"):
        lines.append(f'📺 <a href="{html.escape(m["watch"][0]["url"])}">Смотреть трансляцию</a>')
    if warm := warmup_text(m, team, games):
        lines += ["", warm]
    return "\n".join(lines)


def teams_html(m: dict, team: str | None) -> str:
    """Строка «кто с кем»: своя команда обычным, соперник жирным; чужой матч — обе жирным."""
    home, away = m["home"], m["away"]
    if team in (home, away):
        return f"{html.escape(tname(team))} — <b>{html.escape(tname(away if team == home else home))}</b>"
    return f"<b>{html.escape(tname(home))}</b> — <b>{html.escape(tname(away))}</b>"


def predict_on() -> bool:
    """Прогнозы живут на том же сервере, что и зачёт «Раската» (ADR-020): есть адрес — есть «Кто победит?»."""
    return bool(raskat_api() or (os.environ.get("LIVE_API") or "").strip())


def match_kb(m: dict) -> InlineKeyboardMarkup:
    """«Кто победит?» — карточка матча в мини-аппе (голос там), «Текстовая трансляция» — онлайн лиги."""
    if m.get("id"):
        label = B_PREDICT if predict_on() else B_MATCH
        rows = [[btn(label, "fire", web_app=WebAppInfo(url=app_url(match=m["id"])))]]
    else:
        rows = app_kb().inline_keyboard
    if url := _url(m.get("online")):
        rows.append([InlineKeyboardButton(text=f"📝 {B_ONLINE}", url=url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ---------- после матча (ADR-008, ADR-019) ----------

def result_text(g: dict, names: dict[str, str], story: str = "", team: str | None = REMIND_TEAM_ID,
                live: bool = False, src: str | None = None, protocol: str | None = None) -> str:
    """Сообщение после матча: счёт, исход для команды болельщика, фраза-сюжет из разбора (ADR-008).
    live — протокола у нас ещё нет, счёт по онлайну лиги или по сайту лиги (src, ADR-019, раздел 8);
    protocol — его страница на сайте лиги: там он уже есть, пока до нас не дошёл."""
    sc = g["score"]
    dec_ = sc.get("decision") or ""
    how = {"ОТ": " в овертайме", "Б": " по буллитам"}.get(dec_, "")
    if team in (g["home"], g["away"]):
        mine, theirs = (sc["home"], sc["away"]) if g["home"] == team else (sc["away"], sc["home"])
        head = f"{e('win')} Победа{how}!" if mine > theirs else f"{e('loss')} Поражение{how}"
    else:
        head = f"{e('goal')} Матч окончен"
    dec = f" ({dec_})" if dec_ else ""
    home, away = (html.escape(names.get(g[k], tname(g[k]))) for k in ("home", "away"))
    text = f"<b>{head}</b>\n\n{home} <b>{sc['home']}:{sc['away']}</b>{dec} {away}"
    if live:
        text += f"\n<i>по данным {SRC_GEN.get(src or '', 'онлайна лиги')}</i>"
    if story:
        text += f"\n\n{html.escape(story)}"
    if live and (url := _url(protocol)):
        return (text + f'\n\nПротокол — <a href="{html.escape(url)}">на сайте лиги</a>. '
                "Голы и составы в приложении появятся по кнопке, когда он дойдёт до нас 👇")
    if live:
        return text + "\n\nГолы и составы появятся по кнопке, когда лига выложит протокол 👇"
    return text + "\n\nГолы, ход матча и составы — по кнопке 👇"


def live_finals(live_games: list[dict], seen: dict[str, tuple[datetime, tuple]], now: datetime) -> list[dict]:
    """Матчи, которые онлайн держит «оконченными» с одним счётом LIVE_FINAL_HOLD подряд.
    seen — ключ → (когда впервые увидели, счёт); меняется на месте. Счёт сменился или матч
    пропал из «окончен» — отсчёт заново."""
    current, ready = {}, []
    for x in live_games:
        sc = _score(x.get("score"))
        if x.get("status") not in LIVE_DONE or not sc or not isinstance(x.get("home"), str):
            continue
        k = x.get("key") or match_key(x)
        sig = (sc["home"], sc["away"], sc["decision"])
        first, old = seen.get(k, (now, sig))
        if old != sig:
            first = now
        current[k] = (first, sig)
        if now - first >= LIVE_FINAL_HOLD:
            ready.append({**x, "key": k, "score": sc})
    seen.clear()
    seen.update(current)
    return ready


def pending_results(league: dict | None, ready: list[dict], announced: set[str], today: date) -> list[dict]:
    """Что пора отправить: протоколы (fresh_results), а где протокола нет — финал по онлайну.
    Один матч — одно сообщение: в announced и id матча, и ключ <дата>|<хозяева>|<гости>."""
    # счёт с ленты сайта лиги без протокола — как финал по живому: «по данным сайта лиги» и где протокол
    out = [{**g, "key": match_key(g), "live": site_only(g), "src": SITE if site_only(g) else None,
            "protocol": protocol_url(g) if site_only(g) else None}
           for g in fresh_results(league or {}, announced, today)]
    by_key = {match_key(g): g for g in (league or {}).get("games") or [] if isinstance(g, dict)}
    done = {x["key"] for x in out}
    for x in ready:
        lg = by_key.get(x["key"]) or {}
        gid = lg.get("id")
        if x["key"] in announced or x["key"] in done or (gid and gid in announced):
            continue
        done.add(x["key"])
        out.append({"id": gid, "key": x["key"], "date": x["date"], "home": x["home"], "away": x["away"],
                    "score": x["score"], "live": True, "src": x.get("src"),
                    "protocol": _url(x.get("protocol")) or protocol_url(lg)})
    return out

# ---------- «Раскат»: игра дня и лист ожидания зачёта ----------
# Игра живёт в мини-аппе; бот ведёт в неё, зовёт, когда включат зачёт (контракт, раздел 6), и раз
# в день напоминает тем, у кого серия и галочка «сообщения о Раскате» (ADR-023, раздел 2).

def raskat_api() -> str:
    """Адрес сервера зачётов. Пусто — играем без зачёта, есть — зачёт включили."""
    return (os.environ.get("RASKAT_API") or "").strip()


def load_waitlist() -> set[int]:
    try:
        return {int(x) for x in json.loads(WAITLIST_FILE.read_text())}
    except (FileNotFoundError, ValueError, TypeError):
        return set()


def save_waitlist(ids: set[int]) -> None:
    write_atomic(WAITLIST_FILE, sorted(ids))


WAITLIST = load_waitlist()


def waitlist_set(chat_id: int, waiting: bool) -> bool:
    """Позвать или больше не звать. Повторный вызов с тем же ответом ничего не меняет."""
    if waiting != (chat_id in WAITLIST):
        WAITLIST.symmetric_difference_update({chat_id})
        save_waitlist(WAITLIST)
    return waiting


def raskat_today(data: dict | None, today: date | None = None) -> dict | None:
    """Сегодняшний день из index.json; нет такого — последний опубликованный."""
    days = [d for d in ((data or {}).get("days") or []) if isinstance(d, dict)]
    if not days:
        return None
    d = (today or datetime.now(TZ).date()).isoformat()
    return next((x for x in days if x.get("date") == d), days[-1])


def par_text(seconds: int) -> str:
    """Норма времени: 70 → «1:10», 45 → «45 секунд»."""
    if seconds < 60:
        return f"{seconds} {plural(seconds, 'секунда', 'секунды', 'секунд')}"
    return f"{seconds // 60}:{seconds % 60:02d}"


def raskat_text(data: dict | None, chat_id: int | None = None, today: date | None = None) -> str:
    """Короткое сообщение про раскат дня по опубликованному index.json."""
    day = raskat_today(data, today) or {}
    n = day.get("n")
    head = f"{e('puck')} <b>Раскат дня</b>" + (f" №{int(n)}" if isinstance(n, int) else "")
    lines = [head, "", "Одна шайба проходит весь лёд и задевает номера звена по порядку. "
                       "Каждая клетка — ровно один раз."]
    w, h, k, par = (day.get(key) for key in ("w", "h", "k", "par"))
    if all(isinstance(v, int) for v in (w, h, k)):
        about = f"Сегодня {k} {plural(k, 'номер', 'номера', 'номеров')} и поле {w}×{h}"
        if isinstance(par, int) and par > 0:
            about += f", норма — {par_text(par)}"
        lines.append(about + ".")
    lines.append("")
    if raskat_api():
        lines.append("Собранный раскат идёт в зачёт дня: очки, серия и кубок клубов.")
    elif chat_id is not None and chat_id in WAITLIST:
        lines.append("Зачёта пока нет — играешь для себя. Позову, как только он откроется.")
    else:
        lines.append("Зачёта пока нет: время и твои записи остаются на телефоне.")
    return "\n".join(lines) + "\n\nЖми кнопку 👇"


def raskat_open_text() -> str:
    """Одно сообщение листу ожидания, когда зачёт включили."""
    return (f"{e('cup')} <b>В «Раскате» открылся зачёт</b>\n\n"
            "Ты просил позвать — зову. Теперь собранный раскат идёт в зачёт дня: очки за "
            "скорость, серия дней подряд и кубок клубов.\n\n"
            "Больше об этом не напишу — раскат ждёт в приложении 👇")


# ---------- стикеры ----------

_sticker_ids: dict[str, str] = {}   # имя → file_id: файл загружаем один раз


async def send_sticker(bot: Bot, chat_id: int, name: str, **kw) -> bool:
    """Стикер — украшение (ADR-005): ошибка не мешает сообщению, кроме блокировки бота."""
    try:
        msg = await sending(lambda: bot.send_sticker(
            chat_id, _sticker_ids.get(name) or FSInputFile(STICKERS / f"{name}.webp"), **kw))
    except TelegramForbiddenError:
        raise
    except Exception:
        logging.exception("sticker %s failed", name)
        return False
    _sticker_ids[name] = msg.sticker.file_id
    return True

# ---------- опубликованные данные мини-аппа ----------

# Данные мини-аппа меняются раз в час, вместе с ним: держим последний файл 10 минут
_leaders: dict = {"at": None, "data": None}
_raskat: dict = {"at": None, "data": None}
_league: dict = {"at": None, "data": None}


def data_url(name: str) -> str:
    """Файл данных рядом с мини-аппом: .../data/<name>, без параметров адреса."""
    u = urlsplit(WEBAPP_URL)
    path = u.path if u.path.endswith("/") else u.path.rsplit("/", 1)[0] + "/"
    return urlunsplit(u._replace(path=path + "data/" + name, query="", fragment=""))


async def fetch_json(session: aiohttp.ClientSession, name: str) -> dict | None:
    try:
        async with session.get(data_url(name)) as r:
            if r.status != 200:
                return None
            return await r.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        logging.warning("data %s not loaded", name)
        return None


async def fetch_once(name: str) -> dict | None:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10), trust_env=True) as session:
        return await fetch_json(session, name)


async def published(cache: dict, name: str) -> dict | None:
    """Файл, опубликованный вместе с мини-аппом. Не скачался — отдаём прошлый, если он был."""
    now = datetime.now(TZ)
    if cache["at"] and now - cache["at"] < timedelta(seconds=DATA_TTL):
        return cache["data"]
    if cache.get("tried") and now - cache["tried"] < timedelta(seconds=DATA_RETRY):
        return cache["data"]
    cache["tried"] = now
    data = await fetch_once(name)
    if data:
        cache.update(at=now, data=data)
    return data or cache["data"]


async def published_leaders() -> dict | None:
    return await published(_leaders, "leaders.json")


async def published_raskat() -> dict | None:
    """index.json «Раската»: дни от начала сезона до сегодня (контракт «Раската», раздел 2)."""
    return await published(_raskat, "raskat/index.json")


async def published_league() -> dict | None:
    """league.json: календарь, время начала, ссылки и результаты (ADR-019, раздел 5)."""
    return await published(_league, "league.json")

# ---------- хендлеры ----------

dp = Dispatcher()


async def sending(call, tries: int = 2):
    """Отправить, переждав «too many requests»: на рассылке Telegram отвечает 429 и просит паузу.

    `call()` — корутина отправки, зовём её заново после паузы. Ждём столько, сколько просит
    Telegram, но не дольше RETRY_WAIT_MAX: иначе на одном чате встанет вся рассылка. Попытки
    кончились — ошибка летит дальше, её считает рассылка."""
    for left in range(tries - 1, -1, -1):
        try:
            return await call()
        except TelegramRetryAfter as err:
            if not left or err.retry_after > RETRY_WAIT_MAX:
                raise
            TRACK.add("retry_after")
            logging.warning("Telegram просит подождать %s с", err.retry_after)
            await asyncio.sleep(err.retry_after)


async def say(bot: Bot, chat_id: int, make) -> None:
    """Отправить make() → (текст, клавиатура); если Telegram не принял свои эмодзи — обычными."""
    async def send():
        text, kb = make()
        return await bot.send_message(chat_id, text, reply_markup=kb)

    try:
        await sending(send)
    except TelegramBadRequest as err:
        if not emoji_off(err):
            raise
        await sending(send)


async def safe_edit(c: CallbackQuery, make) -> None:
    text, kb = make()
    try:
        await c.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as err:   # «message is not modified» — молча, свои эмодзи — повтор
        if "not modified" not in str(err) and emoji_off(err):
            await safe_edit(c, make)


async def send_remind(bot: Bot, cid: int, note: str = "") -> None:
    games = games_of(await published_league())
    await say(bot, cid, lambda: (remind_text(cid, games=games, note=note), remind_kb(cid)))


def start_kind(args: str) -> str:
    """Откуда пришёл /start — для пульта: plain, team, remind, today, leaders, raskat, other."""
    if not args:
        return "plain"
    if args in ("today", "leaders", "raskat", "remind"):
        return args
    if args.startswith("remind-"):
        return "remind"
    return "team" if args in TEAMS else "other"


@dp.message(CommandStart())
async def start(m: Message, command: CommandObject):
    cid = m.chat.id
    args = (command.args or "").strip()
    TRACK.add("starts")
    TRACK.add(f"start:{start_kind(args)}")
    # «Напомнить» в мини-аппе: человек уже решил — включаем сразу и без приветствия (ADR-004, ADR-005).
    # remind — «Рязань-ВДВ» (так мини-апп звал до подписки на любую команду), remind-<id> — эта команда
    if args == "remind" or args.startswith("remind-"):
        team = args.partition("-")[2] or REMIND_TEAM_ID
        got = follow(cid, team) if team in TEAMS else "unknown"
        note = (f"У тебя уже три команды. Сними одну в «Команды», чтобы добавить «{html.escape(TEAMS[team])}»."
                if got == "full" else "")
        await send_sticker(m.bot, cid, "bell", reply_markup=ReplyKeyboardRemove())
        await send_remind(m.bot, cid, note)
        return
    # стикер заодно убирает клавиатуру, если она осталась от прошлой версии бота
    if not await send_sticker(m.bot, cid, "hello", reply_markup=ReplyKeyboardRemove()):
        await m.answer("🏒", reply_markup=ReplyKeyboardRemove())
    if args == "leaders":   # ссылка t.me/<бот>?start=leaders (ADR-009)
        await send_leaders(m)
        return
    if args == "raskat":    # ссылка t.me/<бот>?start=raskat (контракт «Раската», раздел 6)
        await send_raskat(m)
        return
    if args == "today":     # ссылка t.me/<бот>?start=today — матчи дня (ADR-019)
        await send_today(m.bot, cid)
        return
    team = args if args in TEAMS else None
    await say(m.bot, cid, lambda: (welcome_text(team), app_kb(team, today=True)))


async def send_leaders(m: Message) -> None:
    data = await published_leaders()
    await say(m.bot, m.chat.id, lambda: (leaders_text(data), leaders_kb()))


@dp.message(Command("leaders"))   # в меню команд её нет (ADR-005), только ссылкой или руками
async def h_leaders(m: Message):
    TRACK.add("cmd:leaders")
    await send_leaders(m)


async def send_raskat(m: Message) -> None:
    cid = m.chat.id
    data = await published_raskat()
    await say(m.bot, cid, lambda: (raskat_text(data, cid), raskat_kb(cid)))


@dp.message(Command("raskat"))   # в меню команд её нет (ADR-005), только ссылкой или руками
async def h_raskat(m: Message):
    TRACK.add("cmd:raskat")
    await send_raskat(m)


@dp.callback_query(F.data == "rs:wait")
async def cb_raskat_wait(c: CallbackQuery):
    """Та же кнопка зовёт и отказывает: второй раз болельщик видит «Больше не звать»."""
    cid = c.message.chat.id
    waiting = waitlist_set(cid, cid not in WAITLIST)
    data = await published_raskat()
    await safe_edit(c, lambda: (raskat_text(data, cid), raskat_kb(cid)))
    await c.answer("Позову, когда откроется зачёт" if waiting else "Больше не позову")

# ---------- матчи сегодня ----------

async def today_make(cid: int):
    league = await published_league()
    live, schedule = read_live("today.json"), read_live("schedule.json")
    return lambda: (today_text(league, live, schedule, SUBS.get(cid) or []), today_kb(cid))


async def send_today(bot: Bot, cid: int) -> None:
    await say(bot, cid, await today_make(cid))


@dp.message(Command("today"))
async def h_today(m: Message):
    TRACK.add("cmd:today")
    await send_today(m.bot, m.chat.id)


@dp.callback_query(F.data == "d:today")
async def cb_today(c: CallbackQuery):
    await c.answer()
    await send_today(c.bot, c.message.chat.id)


@dp.callback_query(F.data == "d:refresh")
async def cb_today_refresh(c: CallbackQuery):
    await safe_edit(c, await today_make(c.message.chat.id))
    await c.answer("Обновил")

# ---------- напоминания и выбор команды ----------

@dp.message(Command("remind"))
async def h_remind(m: Message):
    TRACK.add("cmd:remind")
    await send_remind(m.bot, m.chat.id)


@dp.message(Command("team"))
async def h_team(m: Message):
    TRACK.add("cmd:team")
    cid = m.chat.id
    await say(m.bot, cid, lambda: (team_text(cid), team_kb(cid)))


@dp.callback_query(F.data == "g:toggle")
async def cb_goals(c: CallbackQuery):
    """Голы по ходу матча (ADR-024): выключатель рядом с напоминаниями."""
    cid = c.message.chat.id
    on = goals_set(cid, not goals_on(cid))
    games = games_of(await published_league())
    await safe_edit(c, lambda: (remind_text(cid, games=games), remind_kb(cid)))
    await c.answer("Голы буду присылать" if on else "Голы присылать не буду")


@dp.callback_query(F.data == "r:toggle")
async def cb_remind(c: CallbackQuery):
    """Включены — выключить совсем (подписка удаляется). Выключены — старая кнопка «Включить»
    из сообщений до выбора команды: она была про «Рязань-ВДВ»."""
    cid = c.message.chat.id
    was = bool(SUBS.get(cid))
    if was:
        unsubscribe(cid)
    else:
        turn_on(cid)
    games = games_of(await published_league())
    await safe_edit(c, lambda: (remind_text(cid, games=games), remind_kb(cid)))
    await c.answer("Выключил и забыл подписку" if was else "Готово")
    if not was:
        await send_sticker(c.bot, cid, "bell")


@dp.callback_query(F.data.in_({"r:show", "r:open"}))
async def cb_remind_show(c: CallbackQuery):
    """r:show — экран напоминаний на месте выбора команды, r:open — новым сообщением (из «Матчи сегодня»)."""
    cid = c.message.chat.id
    await c.answer()
    if c.data == "r:open":
        await send_remind(c.bot, cid)
        return
    games = games_of(await published_league())
    await safe_edit(c, lambda: (remind_text(cid, games=games), remind_kb(cid)))


@dp.callback_query(F.data.startswith("t:"))
async def cb_team(c: CallbackQuery):
    """t:home — конференции, t:c:<конф> — её команды, t:s:<id> — выбрать или снять команду."""
    cid = c.message.chat.id
    parts = c.data.split(":", 2)
    if parts[1] == "c" and len(parts) == 3 and parts[2] in CONFS:
        await safe_edit(c, lambda: (team_text(cid, parts[2]), team_kb(cid, parts[2])))
        await c.answer()
        return
    if parts[1] == "s" and len(parts) == 3 and parts[2] in TEAMS:
        team = parts[2]
        had = bool(SUBS.get(cid))
        got = toggle_team(cid, team)
        if got == "full":
            await c.answer("Можно до трёх команд. Сними одну, чтобы выбрать эту", show_alert=True)
            return
        conf = TEAM_INFO[team].get("conf")
        await safe_edit(c, lambda: (team_text(cid, conf), team_kb(cid, conf)))
        await c.answer(f"Напомню о матчах «{TEAMS[team]}»" if got == "on" else f"«{TEAMS[team]}» — больше не напоминаю")
        if got == "on" and not had:
            await send_sticker(c.bot, cid, "bell")
        return
    await safe_edit(c, lambda: (team_text(cid), team_kb(cid)))
    await c.answer()


# ---------- пульт админа (ADR-021) ----------

def admin_url(tab: str = "") -> str:
    p = urlsplit(WEBAPP_URL)
    path = p.path if p.path.endswith("/") else p.path + "/"
    # вкладка — в запросе: фрагмент адреса Telegram занимает своими tgWebAppData
    return urlunsplit((p.scheme, p.netloc, path + "admin.html", f"tab={tab}" if tab else "", ""))


def admin_reply(chat_id: int, user_id: int | None) -> tuple[str, InlineKeyboardMarkup | None]:
    """Админу — кнопка пульта. Помощнику — пульт сразу на «Голах»: другой вкладки ему не покажут (ADR-036,
    раздел 4). Остальным — их id и куда его вписать: так владелец узнаёт свой."""
    if user_id in ADMIN_IDS:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть пульт",
                                                                         web_app=WebAppInfo(url=admin_url()))]])
        return ("Пульт: службы, сборки, аудитория, рассылки и игры, а на вкладке «Голы» — где каждый гол на пути "
                "к клипу. Данные обновляются раз в минуту.", kb)
    if user_id in PREVIEW_IDS:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text="Открыть «Голы»", web_app=WebAppInfo(url=admin_url("goals")))]])
        return "«Голы»: где каждый гол на пути к клипу и какие ждут тебя. Данные обновляются раз в минуту.", kb
    return (f"Пульт — только для админов приложения. Твой Telegram id: <code>{user_id or chat_id}</code>.\n"
            "Его вписывают в ADMIN_IDS в /etc/rhl/bot.env на сервере.", None)


@dp.message(Command("pismo"))   # в меню команд её нет (ADR-005): кнопка приходит сама, когда бот не понял
async def h_pismo(m: Message):
    if m.chat.type != "private":
        return
    if not ADMIN_IDS:
        await say(m.bot, m.chat.id, lambda: ("Передавать некому: в приложении не задан админ.", app_kb()))
        return
    await say(m.bot, m.chat.id, lambda: ("Расскажи, чего не хватает или что сломалось.", feedback_kb()))


@dp.message(Command("admin"))   # в меню команд её нет; доступ к данным проверяет ещё и сервер API
async def h_admin(m: Message):
    if m.chat.type != "private":
        return
    text, kb = admin_reply(m.chat.id, m.from_user.id if m.from_user else None)
    await m.answer(text, reply_markup=kb)


# ---------- повторы голов (ADR-027) ----------
# Лига публикует запись трансляции в VK целиком. Админ в /replay выбирает матч и присылает ссылку на запись
# и времена всех голов по порядку — все повторы точные. Или нажимает один гол и присылает его время: голы
# того же периода бот досчитает по времени, когда служба live заметила смену счёта (replay.py). Ответ —
# сразу ссылки на все голы: их можно проверить тут же. Порядок голов — по протоколу, пока его нет — по live.

REPLAY_ASK: dict[int, tuple[str, int, str, datetime, str]] = {}
PENDING_MARK: dict[int, tuple[str, int, str, str, str, datetime]] = {}   # чат → отметка с вопросами (ADR-033)   # чат → (дата, номер, счёт или "" — весь матч, когда, ключ)


def load_replays() -> dict:
    was = read_live(REPLAYS_FILE.name) or {}
    games = was.get("games")
    return {"games": games if isinstance(games, dict) else {}}


# Журнал отметок (ADR-033): каждая отметка админа или помощника — строка в state.db, которую нельзя изменить или
# удалить, только отменить новой строкой. live/replays.json бот собирает из журнала (marks.resolve) в прежнем формате.

_marks: tuple[Path, marks.MarksStore] | None = None
HISTORY_MAX = 6    # строк истории у гола в /replay
REVOKE_MAX = 3     # кнопок «Отозвать» у гола: последние действующие отметки
ROLE_WORD = {"admin": "админ", "helper": "помощник", "import": "до журнала"}
VIA_WORD = {"replay": "/replay", "preview": "превью", "video": "видео", "confirm": "клип",
            "import": "перенесено, не проверено", "panel": "пульт"}
KIND_WORD = {"absent": "🚫 нет в записи", "wrong": "⚠️ табло сбилось", "confirm": "✅ гол виден",
             "reject": "❌ гола тут нет"}


def goal_marks() -> marks.MarksStore:
    """Журнал отметок в state.db. Первое обращение переносит в него отметки из replays.json, сделанные до журнала."""
    global _marks
    if _marks is None or _marks[0] != STATE_DB:
        conn = sqlite3.connect(STATE_DB, isolation_level=None, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout=5000")
        store = marks.MarksStore(conn)
        n = store.import_replays(load_replays()["games"], datetime.now(TZ))
        if n:
            logging.info("журнал отметок: перенесено из replays.json %d отметок, все — «не проверено» (ADR-033)", n)
        _marks = (STATE_DB, store)
    return _marks[1]


def role_of(uid: int | None) -> str:
    return "admin" if uid in ADMIN_IDS else "helper"


def protocol_goal(protocol: list[dict] | None, score: str) -> dict:
    return next((x for x in protocol or [] if x.get("score") == score), {})


def add_mark(key: str, score: str, kind: str, now: datetime, who: int | None, via: str,
             protocol: list[dict] | None = None, **kw) -> int:
    """Строка журнала: кто и откуда, период и время гола по протоколу — чтобы найти гол, если лига его поправит."""
    p = protocol_goal(protocol, score)
    return goal_marks().add(now, key, score, kind, role=role_of(who), via=via, who=who, period=p.get("period"),
                            time=p.get("time"), **kw)


def marks_apply(key: str, g: dict, now: datetime, protocol: list[dict] | None = None) -> dict | None:
    """Запись матча в replays.json — заново из журнала: что действует сейчас → ссылки по голам. Действующих отметок
    нет — матч из replays.json убирается. Возвращает запись матча или None."""
    st = goal_marks().state(key)
    entry = replay.entry(g, st["video"], st["anchors"], now, protocol, absent=st["absent"], wrong=st["wrong"],
                         confirm=st.get("confirm"), reject=st.get("reject")) if st else None
    data = load_replays()
    if entry is None and key not in data["games"]:
        return None
    if entry is None:
        data["games"].pop(key)
    else:
        data["games"][key] = entry
    data["updated"] = admin.iso(now)
    write_atomic(REPLAYS_FILE, data)
    return entry


async def marks_sync_step(now: datetime) -> int:
    """Отметки из пульта (ADR-036, раздел 5): журнал пишут бот и API, а live/replays.json — только бот. Раз в минуту
    матчи, у которых в журнале появились строки новее последней пересборки (`applied`), пересобираются. Свои отметки
    бот пересобирает сразу — повторная сборка ничего не меняет. Сколько матчей пересобрано."""
    store = goal_marks()
    last = store.last_id()   # до выборки: строка, дописанная между ними, попадёт в следующий проход
    was = store.meta("applied")
    keys = store.changed_since(int(was)) if was is not None and was.isdigit() else []
    league = await published_league() if keys else None
    n = 0
    for key in keys:   # один сломанный матч не держит остальные: его ошибка — в журнале службы
        g = live_by_key(key)
        if not g:
            logging.warning("журнал отметок: матча %s нет в файле службы live — повторы не пересобраны", key)
            continue
        try:
            marks_apply(key, g, now, protocol_of(league, g))
            n += 1
        except Exception:
            logging.exception("журнал отметок: повторы матча %s не пересобрались", key)
    store.set_meta("applied", str(last))
    return n


def mark_word(r: dict) -> str:
    if r["kind"] == "time" and isinstance(r.get("sec"), int):
        return replay.fmt_clock(r["sec"])
    if r["kind"] in ("confirm", "reject") and isinstance(r.get("sec"), int):   # ответ на 30 с гола (ADR-036)
        return f"{KIND_WORD[r['kind']]} на {replay.fmt_clock(r['sec'])}"
    if r["kind"] == "time" and not r.get("score"):   # ссылка на запись без времён (этап 0.4 плана)
        return "🎥 запись матча"
    return KIND_WORD.get(r["kind"], r["kind"])


def goal_history(key: str, score: str, viewer: int | None) -> tuple[list[str], list[dict]]:
    """История отметок гола для /replay: строки «когда · кто · что · откуда» (отозванные — с пометкой) и действующие
    отметки, которые можно отозвать."""
    rows = goal_marks().of(key)
    off = marks.revoked(rows)
    mine = [r for r in rows if r["score"] == score and r["kind"] != "revoke"]
    lines = []
    for r in mine[-HISTORY_MAX:]:
        when = datetime.fromisoformat(r["at"]).astimezone(TZ).strftime("%d.%m %H:%M")
        who = "ты" if r.get("who") and r["who"] == viewer else ROLE_WORD.get(r["role"], r["role"])
        lines.append(f"{when} · {who} · {mark_word(r)} · {VIA_WORD.get(r['via'], r['via'])}"
                     + (" — отозвано" if r["id"] in off else ""))
    return lines, [r for r in mine if r["id"] not in off][-REVOKE_MAX:]


def replay_days(now: datetime) -> list[str]:
    return [(now.date() - timedelta(days=i)).isoformat() for i in range(REPLAY_DAYS)]


def replay_day_arg(arg: str | None, now: datetime) -> str | None:
    """«/replay 04.10» (или 04.10.2026) — день, матчи которого открыть: исправить отметку можно всегда (ADR-033).
    Без года — ближайший такой день не позже сегодня. Не дата — None."""
    m = re.fullmatch(r"\s*(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\s*", arg or "")
    if not m:
        return None
    try:
        d = date(int(m[3]) if m[3] else now.year, int(m[2]), int(m[1]))
    except ValueError:
        return None
    if not m[3] and d > now.date():
        d = d.replace(year=d.year - 1)
    return d.isoformat()


def replay_game(day: str, i: int) -> dict | None:
    """Матч из live/<дата>.json по номеру в списке дня. Номер — место в списке: live.py пишет его по порядку."""
    games = (read_live(f"{day}.json") or {}).get("games") or []
    return games[i] if 0 <= i < len(games) and isinstance(games[i], dict) else None


def league_match(league: dict | None, g: dict) -> dict | None:
    """Матч службы live в league.json: та же дата и те же хозяева и гости."""
    return next((m for m in games_of(league)
                 if (m.get("date"), m.get("home"), m.get("away")) == (g.get("date"), g.get("home"), g.get("away"))), None)


def protocol_of(league: dict | None, g: dict) -> list[dict] | None:
    """Голы протокола этого матча из league.json без буллитов — правило общее с пультом (goalplan.protocol_of)."""
    return goalplan.protocol_of(league_match(league, g))


def league_video(league: dict | None, g: dict) -> str | None:
    """Запись трансляции лиги для разметки (ADR-028) — правило общее с пультом (goalplan.league_video)."""
    return goalplan.league_video(league_match(league, g))


def replay_goals(g: dict, protocol: list[dict] | None = None) -> list[dict]:
    return replay.with_protocol(replay.goals_of(g), protocol)


def replay_matches(now: datetime, days: list[str] | None = None) -> list[tuple[str, int, dict]]:
    """Сыгранные и идущие матчи последних дней (или этих days), где служба live видела хотя бы один гол со счётом."""
    out = []
    for day in days or replay_days(now):
        for i, g in enumerate((read_live(f"{day}.json") or {}).get("games") or []):
            if isinstance(g, dict) and replay.goals_of(g):
                out.append((day, i, g))
    return out


def replay_title(day: str, g: dict) -> str:
    sc = g.get("score") or {}
    score = f" {sc.get('home')}:{sc.get('away')}" if sc else ""
    return f"{day[8:10]}.{day[5:7]} {tname(g.get('home', ''))}{score} {tname(g.get('away', ''))}"


def replay_text(day: str, g: dict, entry: dict | None, protocol: list[dict] | None = None,
                video: str | None = None) -> str:
    """Голы матча и что с повторами: точный (отмечен), расчётный или нет. Ссылки — чтобы проверить сразу.
    video — запись лиги из league.json: пока своей разметки нет, её и размечаем (ADR-028). Голы, которые служба
    clips нашла по табло (ADR-030), — точные, с пометкой ⏱ (встали часы) или 📺 (смена счёта)."""
    board = ((read_live("clips.json") or {}).get("games") or {}).get(match_key(g)) or {}
    off = replay.board_off(entry, board.get("goals") or {}) | set((entry or {}).get("wrong") or [])
    absent = set((entry or {}).get("absent") or [])
    checks = board.get("checks") or {}
    anchors = (entry or {}).get("anchors") or {}
    entry = replay.with_board(entry, board)
    links = {x["score"]: x for x in (entry or {}).get("goals") or []}
    goals = replay_goals(g, protocol)
    lines = [f"🎬 <b>{html.escape(replay_title(day, g))}</b>"]
    if entry:   # ссылкой не даём: смотреть гол — видео в боте (ADR-036)
        lines.append(f"Запись: ролик VK {html.escape(cutjobs.vid(entry['video']))}")
    elif video:
        lines.append(f"Запись лиги: ролик VK {html.escape(cutjobs.vid(video))}. Не тот ролик — пришли свою ссылку, "
                     "времена к ней не обязательны.")
    seen = board_state(board, (entry or {}).get("video") or video)
    if seen:
        lines.append(seen)
    rejected = board.get("rejected") or {}
    lines.append("")
    for k, x in enumerate(goals, 1):
        who = html.escape(tname(g.get(x["team"]) or "")) if x.get("team") in ("home", "away") else "?"
        author = f" · {html.escape(x['text'])}" if x.get("text") else ""
        per = str(x.get("period") or "")
        per = f" · {per}-й" if per.isdigit() else f" · {html.escape(per)}" if per else ""
        r = links.get(x["score"])
        url = replay.at_link(entry["video"], r["t"]) if r else None   # есть ли повтор: ссылкой его не даём (ADR-036)
        sign = {"clock": "⏱", "board": "📺", "run": "🕐"}.get(r.get("src"), "✅") if r and r["exact"] else "≈"
        mark = f" — {sign} {replay.fmt_t(r['t'])}" if url else ""
        note = []
        c = checks.get(x["score"]) if x["score"] in anchors else None
        if x["score"] in anchors and replay.objected((entry or {}).get("reject"), x["score"], anchors[x["score"]]):
            note.append("❌ гола тут нет")   # этой секунде возразили на её 30 с: проверять её служба уже не станет
        elif isinstance(c, dict) and c.get("t") == anchors[x["score"]]:   # проверка отметки службой (ADR-033)
            note.append({"ok": "сошлось", "conflict": "⚠️ спор: " + html.escape(", ".join(c.get("against") or [])),
                         "unknown": "нечем проверить"}.get(c.get("status"), "не проверено"))
        elif x["score"] in anchors:
            note.append("не проверено")
        made = (board.get("clips") or {}).get(x["score"])
        if isinstance(made, dict) and made.get("kind") == "window":
            note.append("🎬 окно")   # у болельщиков окно (ADR-037) — гол всё ещё ждёт второго свидетеля
        elif made:
            note.append("🎬 клип")   # клип у болельщиков (ADR-033, раздел 4: два свидетеля)
        if x["score"] in absent:
            mark, note = " — 🚫 нет в записи", []
        elif x["score"] in off and not (r and r.get("exact") and r.get("src") in (None, "admin")):
            note.append("⚠️ табло сбилось")
        elif not url and rejected.get(x["score"]):   # почему табло не взяло гол — чтобы было видно, что чинить
            note.append(f"табло: {html.escape(str(rejected[x['score']]))}")
        if note:   # без повтора пометка идёт первой: не «0:1 Рязань · клип», а «0:1 Рязань — клип»
            mark += (" · " if mark else " — ") + " · ".join(note)
        lines.append(f"{k}. <b>{x['score']}</b> {who}{author}{per}{mark}")
    lines.append("")
    if entry:
        lines.append("✅ — по твоему времени, ⏱ и 📺 — нашла служба по табло (встали часы, сменился счёт), 🕐 — по "
                     "ходу часов от соседнего гола, ≈ — примерно. Время человека служба сверяет с табло: «сошлось», "
                     "«не проверено» или «спор» — тогда точной секунды нет ни у отметки, ни у табло.")
        lines.append("Нажми на гол — пришлю видео: точная секунда — 30 с, как у клипа, примерное место — пару минут, "
                     "где гол неизвестно — 3 минуты для поиска. Ответ — кнопкой под видео или временем в видео (0:47). "
                     "«▶️ Все голы матча» — 30 с каждого точного гола одним альбомом.")
    what = "времена" if entry or video else "ссылку на запись и времена"
    lines.append(f"Знаешь времена в записи — пришли {what} всех {len(goals)} голов по порядку, по строке на гол: "
                 "1:08:03.")
    if not entry:   # этап 0.4 плана: разметка целого матча руками нужна не всегда
        lines.append("Можно прислать одну ссылку без времён: голы в записи служба найдёт по табло сама, а где не "
                     "сможет — пришлёт тебе превью с кнопками.")
    return "\n".join(lines)


BOARD_STATE = {   # что служба clips сделала с записью матча — строкой под записью в /replay
    "no_board": "📺 Табло клуба-хозяина не размечено — служба голы не ищет: перешли кадр табло в сессию Claude",
    "short": "📺 Ролик короче матча — это не запись игры: пришли ссылку на полную запись",
    "wait": "📺 Запись ещё не готова: эфир идёт или VK её обрабатывает — служба разберёт её сама, как только VK отдаст "
            "запись целиком, и не позже следующего дня",
}


def board_state(board: dict | None, video: str | None) -> str:
    """Почему табло могло не дать секунд: запись не разобрана, не скачалась, табло не размечено. Всё в порядке — пусто."""
    # запись удалили из VK (этап 0.3 плана): говорим до проверки на ролик — после сборки мёртвой ссылки в league.json
    # уже нет, и матч пришёл бы вообще без объяснения
    if (board or {}).get("status") == "gone" and (not video or replay.same_video(board.get("video"), video)):
        return ("📺 Этой записи больше нет в VK: повторов по ней не будет. Найди другую запись — в канале клуба или на "
                "вкладке «Видео» матча на сайте лиги — и пришли ссылку сюда. Если запись открывается у тебя — пришли "
                "время гола ещё раз, твои времена снова станут повторами")
    if not video:
        return ""
    if not board or not replay.same_video(board.get("video"), video):
        return "📺 Служба clips эту запись ещё не разбирала — свежие матчи первыми, дойдёт сама"
    status = board.get("status")
    if status == "error":
        return f"📺 Запись не скачалась: {html.escape(str(board.get('error') or '')[:200])}"
    if status == "ok" and not any(isinstance(x, dict) and (x.get("change") is not None or x.get("t") is not None)
                                  for x in (board.get("goals") or {}).values()):
        return ("📺 Табло разобрано, а смены счёта на нём служба не увидела: возможно, табло другое или его прячут. "
                "Пришли время голов, а кадр табло — в сессию Claude: разметку проверим")
    return BOARD_STATE.get(status, "")


def replay_kb(day: str, i: int, g: dict, entry: dict | None, protocol: list[dict] | None = None) -> InlineKeyboardMarkup:
    rows, row = [], []
    for x in replay_goals(g, protocol):
        row.append(InlineKeyboardButton(text=f"🎯 {x['score']}", callback_data=f"rp:g:{day}:{i}:{x['score']}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    board = replay.with_board(entry, clips_game(match_key(g)))
    if any(x.get("exact") for x in (board or {}).get("goals") or []):   # есть что показать альбомом (ADR-036)
        rows.append([InlineKeyboardButton(text="▶️ Все голы матча", callback_data=f"rp:all:{day}:{i}")])
    vm = video_mark(match_key(g)) if entry else None
    if vm:   # ссылку прислали без времён — её можно отозвать, не сбрасывая верные времена голов (этап 0.4)
        rows.append([InlineKeyboardButton(text=f"↩️ Отозвать: {mark_word(vm)}",
                                          callback_data=f"rp:r:{day}:{i}:{vm['id']}")])
    if entry:
        rows.append([InlineKeyboardButton(text="Сбросить повторы матча", callback_data=f"rp:x:{day}:{i}")])
    back = "rp:list" if day in replay_days(datetime.now(TZ)) else f"rp:d:{day}"
    rows.append([InlineKeyboardButton(text="← Матчи", callback_data=back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def replay_list(now: datetime, day: str | None = None) -> tuple[str, InlineKeyboardMarkup | None]:
    """Матчи для разметки: последние REPLAY_DAYS дней или один день (/replay ДД.ММ)."""
    found = replay_matches(now, [day] if day else None)
    if not found:
        if day:
            return (f"За {day[8:10]}.{day[5:7]} нет матчей, где служба live видела голы.", None)
        return ("Нет матчей, где служба live видела голы, за последние дни. Повтор считается от времени, "
                "когда сменился счёт на сайте лиги, — без него не из чего. Матч старше — /replay ДД.ММ.", None)
    marked = load_replays()["games"]
    kb = [[InlineKeyboardButton(text=("✅ " if match_key(g) in marked else "") + replay_title(day, g),
                                callback_data=f"rp:m:{day}:{i}")] for day, i, g in found[:30]]
    cov = (read_live("clips.json") or {}).get("coverage")
    head = coverage_text(cov, full=False) if not day else ""
    what = f"Повторы голов за {day[8:10]}.{day[5:7]}: выбери матч." if day else \
        "Повторы голов: выбери матч. Матч старше — /replay ДД.ММ."
    left = sum(1 for e in (cov or {}).values() if isinstance(e, dict) and e.get("why") not in (None, "ok"))
    if head and left:   # что мешает повтору у каждого гола и кнопки на эти матчи
        kb.insert(0, [InlineKeyboardButton(text=f"📋 Почему не у всех — {left} {plural(left, 'матч', 'матча', 'матчей')}",
                                           callback_data="rp:why")])
    return (head + "\n\n" if head else "") + what, InlineKeyboardMarkup(inline_keyboard=kb)


def replay_parse(day: str, i: int, score: str, text: str, key: str | None = None,
                 protocol: list[dict] | None = None, league_vid: str | None = None
                 ) -> tuple[str, dict | None, str | None, list[tuple[str, int]]]:
    """Сообщение админа → (ошибка или пусто, матч, ролик, [(счёт, секунда записи)]). score — нажатый гол, пусто —
    весь матч: тогда в сообщении времена всех голов по порядку. key — ключ матча на момент нажатия: в файл дня
    успел добавиться матч — номер уже чужой. league_vid — запись лиги: берём, если ссылки нет ни в сообщении,
    ни в прежней разметке (ADR-028)."""
    g = replay_game(day, i)
    if not g or key and match_key(g) != key:
        return "Матч пропал из файла службы live — открой /replay заново.", None, None, []
    key = match_key(g)
    old = goal_marks().state(key) or {}
    got = replay.parse_link(text)
    video, link_t = got if got else (old.get("video") or league_vid, None)
    if not video:
        return "Ролика этого матча ещё не знаю: пришли ссылку на запись в VK.", g, None, []
    times = replay.parse_times(text)
    if score:
        t = link_t if link_t is not None else times[0] if len(times) == 1 else None
        if t is None:
            return ("Пришли время этого гола в записи: 1:08:03 — или ссылку VK «с текущим временем».", g, video, [])
        return "", g, video, [(score, t)]
    goals = replay_goals(g, protocol)
    if not times:
        if got and link_t is not None:   # ссылка «с текущим временем», а гол не выбран: чей это гол — не знаем
            return ("В ссылке есть время, но не сказано, чей это гол: нажми на гол и пришли её ещё раз. Или пришли "
                    "ссылку без времени — тогда голы в записи найдёт служба.", g, video, [])
        if got:   # в сообщении ссылка и ничего больше: записываем запись, а голы в ней найдёт служба (этап 0.4)
            return "", g, video, []
        return (f"Пришли времена всех {len(goals)} голов в записи по порядку, по строке на гол: 1:08:03. "
                "Или нажми на гол и пришли время одного. Другая запись — пришли ссылку, времена не обязательны.",
                g, video, [])
    if len(times) != len(goals):
        return (f"В матче {len(goals)} голов, а времён {len(times)}. Пришли все по порядку "
                "или нажми на гол и пришли время одного.", g, video, [])
    if any(b <= a for a, b in zip(times, times[1:])):
        return "Времена идут не по порядку: каждый следующий гол позже предыдущего.", g, video, []
    return "", g, video, [(x["score"], t) for x, t in zip(goals, times)]


def replay_save(day: str, i: int, score: str, text: str, now: datetime, key: str | None = None,
                protocol: list[dict] | None = None, league_vid: str | None = None,
                who: int | None = None, force_t: int | None = None) -> tuple[str, dict | None]:
    """Сообщение админа → отметки в журнал (ADR-033), пересчёт и запись replays.json. who — Telegram id отметившего,
    force_t — секунда, которую админ выбрал кнопкой вместо присланной (вероятная опечатка). Возвращает (ошибка или
    пусто, запись матча)."""
    err, g, video, picked = replay_parse(day, i, score, text, key, protocol, league_vid)
    if err:
        return err, None
    if force_t is not None and score:
        picked = [(score, force_t)]
    key = match_key(g)
    if not picked:
        # только ссылка, без времён (этап 0.4 плана): отметка без гола и без секунды — она говорит, какая у матча
        # запись. Из неё журнал даёт `video`, служба clips разбирает ролик и находит голы по табло сама
        add_mark(key, "", "time", now, who, "replay", protocol, video=video, seen=text)
    for s, t in picked:
        add_mark(key, s, "time", now, who, "replay", protocol, video=video, sec=t, seen=text)
    return "", marks_apply(key, g, now, protocol)


GAP_MIN = 20       # с записи: два гола ближе — один момент отмечен дважды (ADR-033)
SITE_DRIFT = 120   # с: сдвиг «запись − отметка сайта» у гола не уходит от соседей периода дальше
TYPO_MAX = 3       # кнопок с вероятными опечатками


def mark_issues(score: str, t: int, anchors: dict[str, int], info: dict[str, dict],
                times: dict[str, int | None]) -> list[str]:
    """Что не так с отметкой гола score на секунде t против других отметок матча (ADR-033, раздел 3). info — голы
    службы live с протоколом (период, время по часам `at`), times — время гола по протоколу от начала матча."""
    out = []
    me = info.get(score) or {}
    for o, ot in sorted(anchors.items()):
        if o == score or not isinstance(ot, int):
            continue
        if abs(t - ot) < GAP_MIN:
            out.append(f"{score} и {o} — почти одна секунда записи ({replay.fmt_clock(t)} и {replay.fmt_clock(ot)})")
            continue
        oi = info.get(o) or {}
        gs, go = times.get(score), times.get(o)
        if gs is not None and go is not None and gs != go and (t > ot) != (gs > go):
            out.append(f"{score} в записи {'раньше' if t < ot else 'позже'} гола {o}, а по протоколу наоборот")
            continue
        if not me.get("period") or me.get("period") != oi.get("period"):
            continue
        if gs is not None and go is not None and abs(t - ot) < abs(gs - go):
            out.append(f"между {o} и {score} в записи {replay.fmt_clock(abs(t - ot))}, а по часам игры "
                       f"{replay.fmt_clock(abs(gs - go))} — часы не идут быстрее записи")
        elif me.get("at") and oi.get("at"):
            drift = (t - ot) - (me["at"] - oi["at"]).total_seconds()
            if abs(drift) > SITE_DRIFT:
                out.append(f"{score} и {o}: в записи между ними на {round(abs(drift))} с "
                           f"{'больше' if drift > 0 else 'меньше'}, чем между отметками сайта лиги")
    return out


def typo_variants(t: int) -> list[int]:
    """Вероятные опечатки во времени записи: минуты и секунды местами, переставленные цифры минут или секунд, ±1 час."""
    h, m, s = t // 3600, t % 3600 // 60, t % 60
    out = []
    for hh, mm, ss in ((h, s, m), (h, m % 10 * 10 + m // 10, s), (h, m, s % 10 * 10 + s // 10), (h + 1, m, s),
                       (h - 1, m, s)):
        v = hh * 3600 + mm * 60 + ss
        if hh >= 0 and mm < 60 and ss < 60 and v != t and 0 < v <= replay.MAX_T and v not in out:
            out.append(v)
    return out


def replay_issues(key: str, g: dict, video: str, picked: list[tuple[str, int]],
                  protocol: list[dict] | None) -> tuple[list[str], list[int]]:
    """Проверка присланных времён до записи (ADR-033, раздел 3): порядок голов, ход часов, сдвиг к отметкам сайта, дубли.
    (что не так, вероятные верные секунды — только когда прислан один гол)."""
    st = goal_marks().state(key) or {}
    same = st.get("video") == video or replay.same_video(st.get("video"), video)
    anchors = {**(st.get("anchors") or {} if same else {}), **dict(picked)}
    info = {x["score"]: x for x in replay_goals(g, protocol)}
    times = {x.get("score"): clockrun.game_sec(x.get("time")) for x in protocol or []}
    issues = list(dict.fromkeys(i for s, t in picked for i in mark_issues(s, t, anchors, info, times)))
    if not picked and not same:   # ссылка без времён на другую запись (этап 0.4): прежние отметки перестанут действовать
        lost = sorted((st.get("anchors") or {})) + sorted(st.get("absent") or []) + sorted(st.get("wrong") or [])
        if lost:
            issues.append(f"это другая запись матча: отметки голов {', '.join(lost)} к ней не подходят и действовать "
                          "перестанут (из журнала они не исчезнут — старую ссылку можно прислать снова)")
    cands = []
    if issues and len(picked) == 1:
        s, t = picked[0]
        cands = [v for v in typo_variants(t) if not mark_issues(s, v, {**anchors, s: v}, info, times)][:TYPO_MAX]
    return issues, cands


def video_mark(key: str) -> dict | None:
    """Действующая отметка «вот запись матча» — ссылка без времён (этап 0.4 плана): её отзывают отдельно, не трогая
    верные времена голов."""
    return next((r for r in reversed(marks.active(goal_marks().of(key)))
                 if r["kind"] == "time" and not r["score"] and r.get("video")), None)


def replay_drop(day: str, i: int, now: datetime, who: int | None = None) -> None:
    """«Сбросить повторы матча»: все действующие отметки матча отменяются — в журнале они остаются отозванными."""
    g = replay_game(day, i)
    if g:
        goal_marks().revoke_match(now, match_key(g), role=role_of(who), via="replay", who=who)
        marks_apply(match_key(g), g, now)


def replay_mark(key: str, score: str, kind: str, video: str | None, now: datetime,
                protocol: list[dict] | None = None, who: int | None = None, via: str = "replay") -> str:
    """Пометка у гола (ADR-031) — строка журнала (ADR-033): `absent` — гола в записи нет (запись началась позже, её
    разбили на два ролика), `wrong` — табло сбилось: повтор или превью показывают не тот гол. У помеченного гола
    секунды табло больше нет, у «табло сбилось» — и у следующих голов той же команды. «Нет в записи» и время гола —
    действует последняя из двух отметок. Ошибка или пусто."""
    g = live_by_key(key)
    video = (goal_marks().state(key) or {}).get("video") or video
    if not g or not video:
        return "Матч пропал из файла службы live или у него нет записи — пометку не записал."
    add_mark(key, score, kind, now, who, via, protocol, video=video)
    marks_apply(key, g, now, protocol)
    return ""


def replay_waiting(m: Message) -> bool:
    """Ждём ли от этого чата времена голов (открыл матч или нажал гол в /replay не дольше REPLAY_WAIT назад). Видео
    гола прислано позже — время ждём в нём (cut_waiting)."""
    ask = REPLAY_ASK.get(m.chat.id)
    cut = CUT_ASK.get(m.chat.id)
    return bool(ask and m.text and m.from_user and m.from_user.id in ADMIN_IDS
                and datetime.now(TZ) - ask[3] <= REPLAY_WAIT and not (cut and cut[1] > ask[3]))


@dp.message(Command("replay"))   # только админам, в меню команд её нет
async def h_replay(m: Message, command: CommandObject):
    if m.chat.type != "private" or not m.from_user or m.from_user.id not in ADMIN_IDS:
        return
    REPLAY_ASK.pop(m.chat.id, None)
    now = datetime.now(TZ)
    day = replay_day_arg(command.args, now)
    if command.args and not day:
        await m.answer("Не понял день. Пришли так: /replay 04.10 — матчи этого дня, хоть начала сезона.")
        return
    text, kb = replay_list(now, day)
    await m.answer(text, reply_markup=kb)


@dp.message(Command("marks_forget"))   # только админам: стереть Telegram id отметившего в журнале (ADR-033)
async def h_marks_forget(m: Message, command: CommandObject):
    if m.chat.type != "private" or not m.from_user or m.from_user.id not in ADMIN_IDS:
        return
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await m.answer("Пришли Telegram id: /marks_forget 123456789 — в журнале отметок у этого человека останется "
                       "только роль (админ или помощник).")
        return
    n = goal_marks().forget(int(arg))
    await m.answer(f"Стёр id {arg} в журнале отметок: строк — {n}. Роль у них осталась." if n
                   else "Отметок с этим id в журнале нет.")


@dp.callback_query(F.data.startswith("rp:"))
async def cb_replay(c: CallbackQuery):
    if not c.from_user or c.from_user.id not in ADMIN_IDS or not c.message:
        await c.answer()
        return
    now = datetime.now(TZ)
    cid = c.message.chat.id
    parts = c.data.split(":", 4)
    if parts[1] == "list":
        REPLAY_ASK.pop(cid, None)
        await safe_edit(c, lambda: replay_list(now))
        await c.answer()
        return
    if parts[1] == "why":   # разбор покрытия целиком и кнопки на недоделанные матчи
        REPLAY_ASK.pop(cid, None)
        await safe_edit(c, lambda: coverage_todo((read_live("clips.json") or {}).get("coverage")))
        await c.answer()
        return
    if parts[1] == "d" and len(parts) > 2:   # список матчей одного дня (/replay ДД.ММ)
        REPLAY_ASK.pop(cid, None)
        await safe_edit(c, lambda: replay_list(now, parts[2]))
        await c.answer()
        return
    day, i = parts[2], int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else -1
    g = replay_game(day, i)
    if not g:
        await c.answer("Матч пропал из файла службы live — открой /replay заново", show_alert=True)
        return
    if parts[1] == "all":   # ▶️ Все голы матча: альбом 30 с у голов с точной секундой (ADR-036)
        await c.answer()
        await match_videos(c.bot, cid, day, i, g, protocol_of(await published_league(), g))
        return
    if parts[1] == "g" and len(parts) == 5:
        REPLAY_ASK[cid] = (day, i, parts[4], now, match_key(g))
        await c.answer()
        league = await published_league()
        history, open_ = goal_history(match_key(g), parts[4], c.from_user.id)
        plan = goal_plan(match_key(g), parts[4], protocol_of(league, g), league_video(league, g)) \
            if cut_store() else None
        if plan:   # видео вместо ссылки VK (ADR-036): ответ — кнопкой под видео или временем в видео
            await goal_video(c.bot, cid, plan, protocol_of(league, g))
            now_t = next((r for r in reversed(open_) if r["kind"] == "time"), None)
            rows = [[InlineKeyboardButton(text=f"✅ Время верное: {mark_word(now_t)}",
                                          callback_data=f"rp:v:{day}:{i}:{now_t['id']}")]] \
                if now_t and now_t.get("who") != c.from_user.id else []
            rows += [[InlineKeyboardButton(text=f"↩️ Отозвать: {mark_word(r)}", callback_data=f"rp:r:{day}:{i}:{r['id']}")]
                     for r in open_]
            if history or rows:
                await c.message.answer(
                    f"Гол <b>{html.escape(parts[4])}</b>: <b>отметки</b> (перепроверил и всё верно — «Время верное»; "
                    "ошиблись — «Отозвать», это остаётся в истории):\n" + "\n".join(html.escape(x) for x in history),
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)
            return
        video = (load_replays()["games"].get(match_key(g)) or {}).get("video") or league_video(league, g)
        rows = [[InlineKeyboardButton(text="🚫 Гола нет в записи", callback_data=f"rp:a:{day}:{i}:{parts[4]}")],
                [InlineKeyboardButton(text="⚠️ Повтор не тот — табло сбилось",
                                      callback_data=f"rp:w:{day}:{i}:{parts[4]}")]] if video else []
        now_t = next((r for r in reversed(open_) if r["kind"] == "time"), None)   # действующее время гола
        if now_t and now_t.get("who") != c.from_user.id:
            rows.append([InlineKeyboardButton(text=f"✅ Время верное: {mark_word(now_t)}",
                                              callback_data=f"rp:v:{day}:{i}:{now_t['id']}")])
        rows += [[InlineKeyboardButton(text=f"↩️ Отозвать: {mark_word(r)}", callback_data=f"rp:r:{day}:{i}:{r['id']}")]
                 for r in open_]
        await c.message.answer(
            f"Гол <b>{html.escape(parts[4])}</b>: пришли его время в записи — 1:08:03"
            + ("." if video else " — вместе со ссылкой на запись в VK.")
            + ("\nПовтор открывает не тот гол — нажми «табло сбилось»: секунды табло у этого гола и следующих голов "
               "команды больше не берём. Гола в записи нет совсем — «нет в записи»." if video else "")
            + ("\n\n<b>Отметки</b> (перепроверил и всё верно — «Время верное»; ошиблись — «Отозвать», это остаётся "
               "в истории):\n"
               + "\n".join(html.escape(x) for x in history) if history else ""),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)
        return
    if parts[1] in ("k", "t"):   # отметка с вопросами: как прислал или выбранная секунда (ADR-033)
        pend = PENDING_MARK.get(cid)
        if not pend or pend[:2] != (day, i) or now - pend[5] > REPLAY_WAIT:
            await c.answer("Это время уже не ждёт — пришли его заново", show_alert=True)
            return
        force = int(parts[4]) if parts[1] == "t" and len(parts) == 5 and parts[4].isdigit() else None
        PENDING_MARK.pop(cid, None)
        league = await published_league()
        protocol, video = protocol_of(league, g), league_video(league, g)
        err, entry = replay_save(day, i, pend[2], pend[3], now, pend[4], protocol, video, who=c.from_user.id,
                                 force_t=force)
        if err:
            await c.answer(err, show_alert=True)
            return
        REPLAY_ASK[cid] = (day, i, "", now, match_key(g))
        await c.message.answer(replay_text(day, g, entry, protocol), reply_markup=replay_kb(day, i, g, entry, protocol),
                               disable_web_page_preview=True)
        await c.answer("Записал")
        return
    if parts[1] == "v" and len(parts) == 5 and parts[4].isdigit():   # «Время верное»: перепроверил — своя отметка
        mark = goal_marks().get(int(parts[4]))
        if not mark or mark["match"] != match_key(g) or mark["kind"] != "time" \
                or mark["id"] in marks.revoked(goal_marks().of(mark["match"])):
            await c.answer("Этой отметки уже нет — открой гол заново", show_alert=True)
            return
        league = await published_league()
        protocol = protocol_of(league, g)
        add_mark(mark["match"], mark["score"], "time", now, c.from_user.id, "replay", protocol, video=mark["video"],
                 sec=mark["sec"], seen=f"подтвердил отметку #{mark['id']}")
        entry = marks_apply(match_key(g), g, now, protocol)
        TRACK.add("replay_confirms")
        await c.message.answer(replay_text(day, g, entry, protocol), reply_markup=replay_kb(day, i, g, entry, protocol),
                               disable_web_page_preview=True)
        await c.answer(f"Записал: {mark_word(mark)} подтверждено тобой")
        return
    if parts[1] == "r" and len(parts) == 5 and parts[4].isdigit():   # отозвать отметку (ADR-033)
        mark = goal_marks().get(int(parts[4]))
        if not mark or mark["match"] != match_key(g) or goal_marks().revoke(
                now, mark["id"], role=role_of(c.from_user.id), via="replay", who=c.from_user.id) is None:
            await c.answer("Эта отметка уже отозвана", show_alert=True)
            return
        TRACK.add("replay_revokes")
        league = await published_league()
        protocol, video = protocol_of(league, g), league_video(league, g)
        entry = marks_apply(match_key(g), g, now, protocol)
        REPLAY_ASK[cid] = (day, i, "", now, match_key(g))
        await c.message.answer(replay_text(day, g, entry, protocol, video),
                               reply_markup=replay_kb(day, i, g, entry, protocol), disable_web_page_preview=True)
        await c.answer("Отозвал — в истории отметка осталась отозванной")
        return
    if parts[1] in ("a", "w") and len(parts) == 5:
        league = await published_league()
        err = replay_mark(match_key(g), parts[4], "absent" if parts[1] == "a" else "wrong", league_video(league, g),
                          now, protocol_of(league, g), who=c.from_user.id)
        if err:
            await c.answer(err, show_alert=True)
            return
        TRACK.add("replay_marks")
        REPLAY_ASK[cid] = (day, i, "", now, match_key(g))
        entry = load_replays()["games"].get(match_key(g))
        protocol, video = protocol_of(league, g), league_video(league, g)
        await c.message.answer(replay_text(day, g, entry, protocol, video),
                               reply_markup=replay_kb(day, i, g, entry, protocol), disable_web_page_preview=True)
        await c.answer("Записал")
        return
    if parts[1] == "x":
        replay_drop(day, i, now, who=c.from_user.id)
    REPLAY_ASK[cid] = (day, i, "", now, match_key(g))   # открыт матч — ждём времена всех голов
    league = await published_league()
    protocol, video = protocol_of(league, g), league_video(league, g)
    entry = load_replays()["games"].get(match_key(g))
    await safe_edit(c, lambda: (replay_text(day, g, entry, protocol, video), replay_kb(day, i, g, entry, protocol)))
    await c.answer()


@dp.message(replay_waiting)   # раньше h_lost: времена от админа — не «непонятое сообщение»
async def h_replay_link(m: Message):
    day, i, score, _, key = REPLAY_ASK[m.chat.id]
    now = datetime.now(TZ)
    g = replay_game(day, i)
    league = await published_league()
    protocol, video = (protocol_of(league, g), league_video(league, g)) if g else (None, None)
    err, g2, vid, picked = replay_parse(day, i, score, m.text, key, protocol, video)
    if err:
        await m.answer(err)
        return
    issues, cands = replay_issues(key, g2, vid, picked, protocol)
    if issues:   # не записываем молча: показываем, что не так, и вероятные опечатки (ADR-033)
        PENDING_MARK[m.chat.id] = (day, i, score, m.text, key, now)
        rows = [[InlineKeyboardButton(text=f"Записать {replay.fmt_clock(v)}", callback_data=f"rp:t:{day}:{i}:{v}")]
                for v in cands]
        rows.append([InlineKeyboardButton(text="Записать как прислал", callback_data=f"rp:k:{day}:{i}")])
        await m.answer("Проверь, прежде чем записать:\n" + "\n".join(f"• {html.escape(x)}" for x in issues)
                       + ("\n\nМожет быть, опечатка?" if cands else ""),
                       reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return
    err, entry = replay_save(day, i, score, m.text, now, key, protocol, video, who=m.from_user.id)
    if err:
        await m.answer(err)
        return
    REPLAY_ASK[m.chat.id] = (day, i, "", now, key)   # можно сразу прислать поправку
    await m.answer(replay_text(day, g, entry, protocol), reply_markup=replay_kb(day, i, g, entry, protocol),
                   disable_web_page_preview=True)


def replay_todo(now: datetime, league: dict | None) -> list[tuple[str, int, dict]]:
    """Матчи, которые ждут разметки повторов (ADR-028): в окне /replay, окончены, служба live видела голы,
    у матча есть запись лиги, а в replays.json его нет."""
    marked = load_replays()["games"]
    found = (read_live("clips.json") or {}).get("games") or {}
    return [(day, i, g) for day, i, g in replay_matches(now)
            if g.get("status") in ("ended", "final") and match_key(g) not in marked and league_video(league, g)
            and not board_covers(g, found.get(match_key(g)))]


def board_covers(g: dict, found: dict | None) -> bool:
    """Служба clips разобрала матч и у каждого гола есть секунда по табло или превью (ADR-030): тогда напоминать о
    разметке незачем — превью придут сами."""
    goals = (found or {}).get("goals") or {}
    if (found or {}).get("status") != "ok":
        return False
    return all((goals.get(x["score"]) or {}).get("t") is not None or (goals.get(x["score"]) or {}).get("ask")
               for x in replay.goals_of(g))


def goal_place(item: tuple[str, dict]) -> float:
    """Где гол в записи для порядка превью: смена счёта на табло."""
    return (item[1] or {}).get("change") or 0


def previews_waiting(clips: dict | None, marked: dict) -> dict[str, list[str]]:
    """Голы, которые ждут ответа на превью (ADR-030, раздел 7): табло знает, какой это гол, секунды нет ни у табло,
    ни у админа, а превью есть — ушло в чат или уйдёт. Ключ матча → счета голов по порядку."""
    out: dict[str, list[str]] = {}
    for key, game in sorted(((clips or {}).get("games") or {}).items()):
        if not isinstance(game, dict):
            continue
        anchors = ((marked or {}).get(key) or {}).get("anchors") or {}
        for score, g in sorted((game.get("goals") or {}).items(), key=goal_place):
            if isinstance((g or {}).get("ask"), dict) and g.get("t") is None and score not in anchors:
                out.setdefault(key, []).append(score)
    return out


def key_title(key: str) -> str:
    _, home, away = key.split("|")
    return f"{tname(home)} — {tname(away)}"


COVER_WHY = {   # причины, по которым у матча не все голы с повтором (ADR-031, clips.coverage) — что делать админу
    "no_video": "нет записи ни лиги, ни клуба",
    "gone": "запись удалили из VK",
    "error": "VK не отдал запись",
    "no_board": "табло клуба не размечено",
    "not_found": "табло не нашло голы",
    "pending": "ждут разбора",
}
COVER_NAMES = 4   # матчей на причину в разборе — дальше «и ещё N»
COVER_TODO = {   # что сделать, чтобы у матча были повторы всех голов (кнопка «Почему не у всех» в /replay)
    "no_video": "открой матч и пришли ссылку на запись в VK (из канала клуба) — времена голов не обязательны",
    "gone": "записи больше нет в VK: найди другую (канал клуба, вкладка «Видео» на сайте лиги) и пришли ссылку — "
            "по старой ссылке повтор никуда не ведёт",
    "error": "VK не отдал запись трижды: на сервере sudo -u rhl /opt/rhl/venv/bin/pip install -U yt-dlp, "
             "systemctl restart clips — или пришли время голов в матче сам",
    "no_board": "перешли кадр табло, который прислал бот, в сессию Claude — после разметки служба переберёт матчи сама; "
                "а пока открой матч и нажми гол: пришлю видео для поиска",
    "not_found": "открой матч и нажми гол без повтора: пришлю видео для поиска, ответ — кнопкой или временем в видео",
    "pending": "ничего: служба разберёт запись сама, свежие матчи — первыми",
}
COVER_BUTTONS = 10   # кнопок на матчи в «Почему не у всех»


def coverage_text(cov: dict | None, full: bool = True) -> str:
    """Разбор покрытия повторами (ADR-031): сколько матчей с повтором у всех голов и почему у остальных — с матчами,
    ошибками VK и тем, почему табло не взяло голы. full=False — одна строка для заголовка /replay."""
    cov = {k: e for k, e in (cov or {}).items() if isinstance(e, dict) and k.count("|") == 2}
    if not cov:
        return ""
    ok = sum(1 for e in cov.values() if e.get("why") == "ok")
    goals = sum(int(e.get("goals") or 0) for e in cov.values())
    have = sum(int(e.get("replays") or 0) for e in cov.values())
    since = min(cov)[:10]
    head = (f"📊 Повторы с {since[8:10]}.{since[5:7]}: у всех голов — {ok} из {len(cov)} "
            f"{plural(len(cov), 'матча', 'матчей', 'матчей')}, голов с повтором — {have} из {goals}.")
    if not full or ok == len(cov):
        return head
    lines = [head, "Без повтора:"]
    for why, label in COVER_WHY.items():
        keys = sorted((k for k, e in cov.items() if e.get("why") == why), reverse=True)
        if not keys:
            continue
        names = []
        for k in keys[:COVER_NAMES]:
            e = cov[k]
            note = e.get("error") if why == "error" else "; ".join(
                f"{sc}: {r}" for sc, r in list((e.get("rejected") or {}).items())[:2]) if why == "not_found" else ""
            miss = (f", без повтора {len(e['missing'])} из {e.get('goals')}"
                    if why == "not_found" and e.get("missing") else "")
            names.append(f"{key_title(k)} {k[8:10]}.{k[5:7]}{miss}" + (f" ({note})" if note else ""))
        more = f" и ещё {len(keys) - COVER_NAMES}" if len(keys) > COVER_NAMES else ""
        lines.append(f"• {label} — {len(keys)}: " + html.escape(", ".join(names) + more))
    return "\n".join(lines)


def coverage_todo(cov: dict | None) -> tuple[str, InlineKeyboardMarkup | None]:
    """«Почему не у всех» в /replay: разбор покрытия целиком, что делать по каждой причине и кнопки на матчи, где
    повтор есть не у всех голов, — сначала те, где можно доделать руками сейчас."""
    cov = {k: e for k, e in (cov or {}).items() if isinstance(e, dict) and k.count("|") == 2}
    text = coverage_text(cov)
    if not text:
        return "Разбора покрытия ещё нет: служба clips его не писала.", None
    todo = [k for k in cov if cov[k].get("why") not in (None, "ok")]
    if not todo:
        return text + "\n\nВсё готово: повтор у каждого гола.", None
    why_now = [w for w in COVER_WHY if any(cov[k]["why"] == w for k in todo)]
    lines = [text, "", "<b>Что сделать</b>"] + [f"• {COVER_WHY[w]} — {html.escape(COVER_TODO[w])}" for w in why_now]
    order = {"not_found": 0, "gone": 1, "no_video": 2, "no_board": 3, "error": 4, "pending": 5}
    rows = []
    for k in sorted(todo, key=lambda k: (order.get(cov[k]["why"], 9), k))[:COVER_BUTTONS]:
        place = match_place(k)
        if not place:
            continue
        e = cov[k]
        miss = int(e.get("goals") or 0) - int(e.get("replays") or 0)
        rows.append([InlineKeyboardButton(text=f"🛠 {key_title(k)} {k[8:10]}.{k[5:7]} · без повтора {miss}",
                                          callback_data=f"rp:m:{place[0]}:{place[1]}")])
    rows.append([InlineKeyboardButton(text="← Матчи", callback_data="rp:list")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def replay_nag(todo: list[tuple[str, int, dict]], waiting: dict[str, list[str]] | None = None,
               now: datetime | None = None, boards: dict | None = None,
               cover: dict | None = None) -> tuple[str, InlineKeyboardMarkup]:
    """«Ждут превью: 7 голов в 3 матчах» (ADR-030, раздел 7), клубы, чьё табло не размечено (дополнение 06.10: их
    домашние матчи без секунд и клипов, пока не разметят), и матчи, где табло не дало ни секунд, ни превью, — их
    размечают временами в /replay, как раньше (ADR-028). Кнопка на матч — открывает его в /replay."""
    waiting = waiting or {}
    parts, rows, seen = [], [], set()
    if coverage_text(cover):
        parts.append(coverage_text(cover))
    if waiting:
        n = sum(len(v) for v in waiting.values())
        m = len(waiting)
        names = ", ".join(key_title(k) for k in waiting)
        parts.append(f"🎬 Ждут превью: {n} {plural(n, 'гол', 'гола', 'голов')} в {m} "
                     f"{plural(m, 'матче', 'матчах', 'матчах')} — {html.escape(names)}.\n"
                     "Превью — выше в чате: нажми момент гола или пришли время в видео.")
    if boards:
        names = ", ".join(f"{tname(club)} ({int((e or {}).get('matches') or 0)})" for club, e in sorted(boards.items()))
        parts.append(f"🖼 Табло не размечено: {html.escape(names)} — в скобках домашние матчи без секунд и клипов.\n"
                     "Кадры табло — выше в чате файлами: переслать в сессию Claude для разметки в boards.json.")
    if todo:
        names = ", ".join(f"{tname(g.get('home', ''))} — {tname(g.get('away', ''))}" for _, _, g in todo)
        head = "Без превью" if waiting else "🎬 Без превью"
        parts.append(f"{head}, табло не разобрало: {html.escape(names)}.\n"
                     "Открой матч: у каждого гола — видео, ответ — кнопкой или временем в видео.")
    days = set(replay_days(now)) if now else None
    picks = [(day, i, g) for day, i, g in todo]
    for key in waiting:   # матч из превью — тоже кнопкой, если он ещё в окне /replay
        if days is not None and key[:10] in days:
            games = (read_live(f"{key[:10]}.json") or {}).get("games") or []
            i = next((i for i, g in enumerate(games) if isinstance(g, dict) and match_key(g) == key), None)
            if i is not None:
                picks.append((key[:10], i, games[i]))
    for day, i, g in picks:
        if (day, i) in seen:
            continue
        seen.add((day, i))
        rows.append([InlineKeyboardButton(text=replay_title(day, g), callback_data=f"rp:m:{day}:{i}")])
    if len(rows) > REPLAY_NAG_MAX:
        rows = rows[:REPLAY_NAG_MAX] + [[InlineKeyboardButton(text="Все матчи", callback_data="rp:list")]]
    return "\n\n".join(parts), InlineKeyboardMarkup(inline_keyboard=rows)


async def replay_nag_step(bot: Bot, now: datetime) -> int:
    """Раз в день после REPLAY_NAG_AT — напоминание админам: сколько голов ждут ответа на превью и какие матчи с
    записью лиги остались без секунд и превью. Ночью молчим, как тревоги (ADR-022). Что сегодня уже напомнили,
    помнит счётчик дня replay_nag в status/bot.json: перезапуск бота сообщение не повторит. Те же числа — на пульте
    (previews_wait, replays_todo)."""
    if not ADMIN_IDS:
        return 0
    todo = replay_todo(now, await published_league())
    found = read_live("clips.json")
    waiting = previews_waiting(found, load_replays()["games"])
    boards = {c: e for c, e in ((found or {}).get("boards") or {}).items() if isinstance(e, dict)}
    cover = (found or {}).get("coverage") or {}
    gaps = any(isinstance(e, dict) and e.get("why") != "ok" for e in cover.values())
    TRACK.gauge("replays_todo", len(todo))
    TRACK.gauge("previews_wait", sum(len(v) for v in waiting.values()))
    if (not todo and not waiting and not boards and not gaps) or quiet(now) \
            or now.astimezone(TZ).time() < REPLAY_NAG_AT or TRACK.today().get("replay_nag"):
        return 0
    text, kb = replay_nag(todo, waiting, now, boards, cover)
    kb = kb if kb.inline_keyboard else None   # только «Табло не размечено» — кнопок на матчи нет
    sent = 0
    for cid in sorted(ADMIN_IDS):
        try:
            await say(bot, cid, lambda: (text, kb))
            sent += 1
        except Exception:
            logging.exception("replay nag to admin failed")
        await asyncio.sleep(0.05)
    if sent:   # не дошло ни до кого — попробуем через минуту
        TRACK.add("replay_nag", sent)
        TRACK.flush()
    return sent


# ---------- превью голов (ADR-030, шаг 3) ----------
# Служба clips нашла гол по табло, но не секунду: в live/clips.json у гола `ask` — откуда превью в записи, файл и
# моменты, когда вставали часы игры. Бот присылает превью с кнопками «Гол на 0:47»; ответ — опора в replays.json,
# как время из /replay: повтор точный, у остальных голов периода — расчёт от неё.

PREVIEW_ASK: dict[int, tuple[str, datetime]] = {}   # чат → (жетон превью, когда нажал «Другое время»)


_cuts: tuple[Path, cutjobs.CutJobs] | None = None


def cut_store() -> cutjobs.CutJobs | None:
    """Очередь службы cuts в state.db (ADR-036): превью режет она. Базы нет — None: служба ещё не запускалась."""
    global _cuts
    if _cuts is None or _cuts[0] != STATE_DB:
        if not STATE_DB.exists():
            return None
        conn = sqlite3.connect(STATE_DB, isolation_level=None, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout=5000")
        _cuts = (STATE_DB, cutjobs.CutJobs(conn, BASE))
    return _cuts[1]


def preview_file(ask: dict) -> tuple[Path, dict] | None:
    """Файл превью и его размеры (`w`, `h`, `dur`): у превью с 07.10 — готовое задание службы cuts, у превью до неё —
    файл службы clips. Не готово — None: пришлём в следующую минуту."""
    if ask.get("job") is not None:
        try:
            store = cut_store()
            job = store.get(ask["job"]) if store else None
        except (sqlite3.Error, TypeError, ValueError):
            return None
        path = store.path(job) if job else None
        return (path, {k: job.get(k) for k in ("w", "h", "dur")}) if path else None
    path = BASE / ask["file"] if ask.get("file") else None
    return (path, ask) if path and path.is_file() else None


def preview_people() -> frozenset[int]:
    return ADMIN_IDS | PREVIEW_IDS


def preview_token(key: str, score: str) -> str:
    """Короткий жетон превью для callback_data (до 64 байт): по нему находим матч и гол."""
    return hashlib.sha1(f"{key}|{score}".encode()).hexdigest()[:10]


def load_previews() -> dict:
    try:
        data = json.loads(PREVIEWS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def preview_todo(clips: dict | None, marked: dict, sent: dict) -> list[tuple[str, str, dict, str]]:
    """Превью, которые пора прислать: у гола есть `ask` с файлом или заданием службы cuts, секунды нет ни у табло, ни
    у админа, и превью ещё не уходило. (ключ матча, счёт, ask, ролик) — по порядку матчей и голов."""
    out = []
    for key, game in sorted(((clips or {}).get("games") or {}).items()):
        if not isinstance(game, dict):
            continue
        anchors = ((marked or {}).get(key) or {}).get("anchors") or {}
        for score, g in sorted((game.get("goals") or {}).items(), key=goal_place):
            ask = (g or {}).get("ask")
            was = sent.get(f"{key}|{score}")
            if (isinstance(ask, dict) and (ask.get("file") or ask.get("job") is not None) and g.get("t") is None
                    and score not in anchors
                    and not (isinstance(was, dict) and (was.get("v", 1) >= PREVIEW_V or "done" in was))):
                out.append((key, score, ask, game.get("video")))
    return out


def preview_caption(key: str, score: str, ask: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Подпись и кнопки превью: моменты, когда вставали часы, и «Другое время»."""
    day, home, away = key.split("|")
    text = (f"🎬 <b>{day[8:10]}.{day[5:7]} {html.escape(tname(home))} — {html.escape(tname(away))}</b>, гол "
            f"<b>{html.escape(score)}</b>\n"
            "Где в этом видео гол? Нажми момент или пришли время в видео, например 1:05.")
    tok = preview_token(key, score)
    rows = [[InlineKeyboardButton(text=f"Гол на {replay.fmt_clock(t)}", callback_data=f"pv:{tok}:{k}")]
            for k, t in enumerate(ask.get("cand") or [])]
    rows.append([InlineKeyboardButton(text="Другое время" if rows else "Пришлю время", callback_data=f"pv:{tok}:x")])
    rows.append([InlineKeyboardButton(text="⚠️ Гола тут нет — табло сбилось", callback_data=f"pv:{tok}:w")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def preview_find(tok: str) -> tuple[str, str, dict, str] | None:
    """Жетон → (ключ, счёт, ask, ролик) из live/clips.json."""
    for key, game in ((read_live("clips.json") or {}).get("games") or {}).items():
        for score, g in ((game or {}).get("goals") or {}).items():
            if preview_token(key, score) == tok and isinstance((g or {}).get("ask"), dict):
                return key, score, g["ask"], game.get("video")
    return None


def live_by_key(key: str) -> dict | None:
    """Матч службы live по ключу: из файла его дня."""
    games = (read_live(f"{key[:10]}.json") or {}).get("games") or []
    return next((g for g in games if isinstance(g, dict) and match_key(g) == key), None)


def preview_save(key: str, score: str, sec: int, video: str, now: datetime, protocol: list[dict] | None,
                 who: int | None = None, seen: dict | None = None) -> str:
    """Секунда гола из превью → строка журнала (ADR-033: кто ответил и какое окно видел) и пересчёт повторов матча.
    Ошибка или пусто."""
    g = live_by_key(key)
    if not g:
        return "Матч пропал из файла службы live — секунду не записал."
    add_mark(key, score, "time", now, who, "preview", protocol, video=video, sec=sec, seen=seen)
    marks_apply(key, g, now, protocol)
    return ""


def preview_stale(rec_key: str, rec: dict, clips: dict | None, marked: dict) -> bool:
    """Ушедшее превью больше не нужно (ADR-030, дополнение 06.10, вечер): секунда гола нашлась иначе (табло после
    переразбора, админ в /replay), служба превью отозвала (06.10 — пятиминутные окна по времени сайта лиги без
    гола) или окно у гола теперь другое (другой ролик или начало) — тогда ответ по старому видео дал бы не ту
    секунду. Отвеченное — нужно."""
    if "done" in rec:
        return False
    key, _, score = rec_key.rpartition("|")
    game = ((clips or {}).get("games") or {}).get(key) or {}
    g = (game.get("goals") or {}).get(score)
    ask = g.get("ask") if isinstance(g, dict) else None
    anchors = ((marked or {}).get(key) or {}).get("anchors") or {}
    return (not isinstance(ask, dict) or g.get("t") is not None or score in anchors
            or (rec.get("from") is not None and ask.get("from") != rec["from"])
            or (rec.get("video") is not None and not replay.same_video(rec["video"], game.get("video"))))


async def preview_close(bot: Bot, rec: dict) -> dict:
    """Убрать ненужное превью из чатов: удалить, а не вышло (Telegram даёт удалить только за 48 часов) — подпись
    без кнопок. Сеть подвела — эти сообщения остаются, попробуем через минуту. (чат → сообщение) оставшихся."""
    left = {}
    for cid, mid in (rec.get("msgs") or {}).items():
        try:
            await bot.delete_message(int(cid), mid)
        except TelegramBadRequest:
            try:
                await bot.edit_message_caption(chat_id=int(cid), message_id=mid, reply_markup=None,
                                               caption="Это превью больше не нужно — отвечать на него не надо.")
            except TelegramBadRequest:   # сообщения уже нет
                pass
            except Exception:
                left[cid] = mid
        except Exception:
            left[cid] = mid
    return left


async def preview_step(bot: Bot, now: datetime) -> int:
    """Раз в минуту: ненужные превью — убрать из чатов, новые — админам и помощникам, не больше PREVIEW_MAX за
    проход. Ночью молчим, как тревоги (ADR-022). Файл грузим в Telegram один раз, остальным — тот же file_id."""
    people = sorted(preview_people())
    if not people or quiet(now):
        return 0
    sent = load_previews()
    edge = admin.iso(now - PREVIEW_KEEP)
    sent = {k: v for k, v in sent.items() if isinstance(v, dict) and (v.get("at") or "") >= edge}
    clips, marked = read_live("clips.json"), load_replays()["games"]
    for rec_key in [k for k, v in sent.items() if clips is not None and preview_stale(k, v, clips, marked)]:
        left = await preview_close(bot, sent[rec_key])
        if left:
            sent[rec_key]["msgs"] = left
        else:   # запись забываем: появится у гола новое превью — уйдёт как новое
            sent.pop(rec_key)
    n = 0
    ready = [(x, got) for x in preview_todo(clips, marked, sent) if (got := preview_file(x[2]))]
    for (key, score, ask, video), (path, meta) in ready[:PREVIEW_MAX]:
        text, kb = preview_caption(key, score, ask)
        for cid, mid in ((sent.get(f"{key}|{score}") or {}).get("msgs") or {}).items():   # старое превью «0:01»
            try:
                await bot.delete_message(int(cid), mid)
            except Exception:   # уже удалено или сеть: новое превью всё равно шлём
                pass
        # длина и размер — от службы cuts (у превью до 07.10 — от clips); у превью до 05.10 их нет: 360p, 16:9
        size = {"duration": int(meta.get("dur") or ask.get("len") or 0) or None,
                "width": int(meta.get("w") or 640), "height": int(meta.get("h") or 360)}
        msgs, file_id = {}, None
        for cid in people:
            try:
                msg = await sending(lambda: bot.send_video(cid, file_id or FSInputFile(path), caption=text,
                                                           reply_markup=kb, supports_streaming=True, **size))
                msgs[str(cid)] = msg.message_id
                file_id = file_id or (msg.video.file_id if msg.video else None)
            except Exception:
                logging.exception("preview to %s failed", cid)
            await asyncio.sleep(0.05)
        if msgs:   # не дошло ни до кого — попробуем в следующую минуту
            sent[f"{key}|{score}"] = {"at": admin.iso(now), "msgs": msgs, "v": PREVIEW_V, "from": ask.get("from"),
                                      "video": video}
            n += 1
    write_atomic(PREVIEWS_FILE, sent)
    if n:
        TRACK.add("previews", n)
    return n


async def preview_done(bot: Bot, key: str, score: str, sec: int, video: str) -> None:
    """Ответ получен: у всех, кому ушло превью, подпись — «готово», кнопки убираем."""
    sent = load_previews()
    rec = sent.get(f"{key}|{score}") or {}
    rec["done"] = sec
    sent[f"{key}|{score}"] = rec
    write_atomic(PREVIEWS_FILE, sent)
    url = replay.at_link(video, max(0, sec - replay.EXACT_LEAD))
    text = (f"✅ Гол <b>{html.escape(score)}</b> — {replay.fmt_t(sec)} в записи"
            + (f', <a href="{html.escape(url)}">повтор</a>' if url else "") + ". Спасибо!")
    for cid, mid in (rec.get("msgs") or {}).items():
        try:
            await bot.edit_message_caption(chat_id=int(cid), message_id=mid, caption=text, reply_markup=None)
        except TelegramBadRequest:
            pass


async def preview_closed(bot: Bot, key: str, score: str, text: str) -> None:
    """Превью закрыто не секундой (табло сбилось): у всех, кому ушло, — подпись без кнопок."""
    sent = load_previews()
    rec = sent.get(f"{key}|{score}") or {}
    rec["done"] = "wrong"
    sent[f"{key}|{score}"] = rec
    write_atomic(PREVIEWS_FILE, sent)
    for cid, mid in (rec.get("msgs") or {}).items():
        try:
            await bot.edit_message_caption(chat_id=int(cid), message_id=mid, caption=text, reply_markup=None)
        except TelegramBadRequest:
            pass


@dp.callback_query(F.data.startswith("pv:"))
async def cb_preview(c: CallbackQuery):
    if not c.from_user or c.from_user.id not in preview_people() or not c.message:
        await c.answer()
        return
    _, tok, pick = (c.data.split(":") + ["", ""])[:3]
    got = preview_find(tok)
    if not got:
        await c.answer("Этого превью уже нет: у гола уже есть секунда, превью отозвано или матч старше трёх дней",
                       show_alert=True)
        return
    key, score, ask, video = got
    if pick == "w":   # ADR-031: на табло в превью другой счёт — сопоставление сбилось
        league = await published_league()
        g = live_by_key(key)
        err = replay_mark(key, score, "wrong", video, datetime.now(TZ), protocol_of(league, g) if g else None,
                          who=c.from_user.id, via="preview")
        if err:
            await c.answer(err, show_alert=True)
            return
        TRACK.add("preview_wrong")
        await preview_closed(c.bot, key, score, f"⚠️ Гол <b>{html.escape(score)}</b>: табло сбилось — секунды табло "
                             "у него и у следующих голов команды больше не берём, превью по ним уберу. Время гола "
                             "можно прислать в /replay.")
        await c.answer("Записал: табло сбилось")
        return
    if pick == "x":
        REPLAY_ASK.pop(c.message.chat.id, None)   # ждём время этого превью, а не разметку из /replay
        PREVIEW_ASK[c.message.chat.id] = (tok, datetime.now(TZ))
        await c.answer()
        await c.message.answer(f"Гол <b>{html.escape(score)}</b>: пришли время гола в этом видео — например 1:05.")
        return
    cand = ask.get("cand") or []
    if not pick.isdigit() or int(pick) >= len(cand):
        await c.answer()
        return
    await preview_answer(c.bot, c.message.chat.id, key, score, ask, video, int(cand[int(pick)]), who=c.from_user.id)
    await c.answer("Записал")


async def preview_answer(bot: Bot, cid: int, key: str, score: str, ask: dict, video: str, t: int,
                         who: int | None = None) -> None:
    sec = int(ask.get("from") or 0) + t
    league = await published_league()
    g = live_by_key(key)
    seen = {"from": ask.get("from"), "len": ask.get("len"), "cand": ask.get("cand"), "pick": t}   # окно превью
    err = preview_save(key, score, sec, video, datetime.now(TZ), protocol_of(league, g) if g else None,
                       who=who, seen=seen)
    if err:
        await bot.send_message(cid, err)
        return
    TRACK.add("preview_answers")
    await preview_done(bot, key, score, sec, video)
    # 30 с результата (ADR-036): ответ на превью сразу виден тем, что увидят болельщики. Отметка уже записана —
    # видео не вышло, ответ от этого не теряется
    length = video_length(key, video)
    try:
        await goal_video(bot, cid, {"kind": "exact", "video": video, "length": length, "key": key, "score": score,
                                    "t": sec, "src": "admin", "windows": [(*cutjobs.review_window(sec, length), "гол")]},
                         protocol_of(league, g) if g else None)
    except Exception:
        logging.exception("preview result video to %s failed", cid)


def preview_waiting(m: Message) -> bool:
    ask = PREVIEW_ASK.get(m.chat.id)
    cut = CUT_ASK.get(m.chat.id)
    return bool(ask and m.text and m.from_user and m.from_user.id in preview_people()
                and datetime.now(TZ) - ask[1] <= REPLAY_WAIT and not (cut and cut[1] > ask[1]))


@dp.message(preview_waiting)   # раньше h_lost: время гола в превью — не «непонятое сообщение»
async def h_preview_time(m: Message):
    tok, _ = PREVIEW_ASK[m.chat.id]
    got = preview_find(tok)
    t = replay.parse_clock(m.text.strip())
    if not got:
        PREVIEW_ASK.pop(m.chat.id, None)
        await m.answer("Этого превью уже нет: у гола уже есть секунда, превью отозвано или матч старше трёх дней.")
        return
    key, score, ask, video = got
    if t is None or t > int(ask.get("len") or 0) + 5:
        await m.answer(f"Не понял время. Пришли, на какой секунде видео гол: например 1:05 (видео — "
                       f"{replay.fmt_clock(int(ask.get('len') or 0))}).")
        return
    PREVIEW_ASK.pop(m.chat.id, None)
    await preview_answer(m.bot, m.chat.id, key, score, ask, video, t, who=m.from_user.id)


# ---------- видео гола вместо ссылок VK (ADR-036, этап 1.2 плана) ----------
# Всё, что бот показывает человеку о месте гола в записи, — видео, вырезанное службой cuts, а не ссылка VK. Что
# показать, решает то, что известно о голе (goal_plan): точная секунда — 30 с вокруг неё, ровно как клип болельщикам;
# примерное место — окно превью (до смены счёта на табло или по счёту хода часов); спор — два видео одним альбомом;
# ничего — 3 минуты записи около оценки по протоколу и шаги «⏪ / ⏩» по 3 минуты. Ответ — кнопкой под видео или
# временем в этом видео («0:47»): секунду записи бот считает сам — начало окна плюс ответ. Видео режется по запросу
# (срочное задание службы cuts), пока человек ждёт, — «⏳ Режу видео…», потом видео. VK не отдал запись — текст с
# ссылкой: тут без VK никак (это и так тревога).

CUT_WAIT = timedelta(minutes=4)   # столько ждём, пока служба cuts вырежет видео для человека
CUT_POLL = 2                      # с между взглядами на задание
ALBUM_MAX = 10                    # видео в альбоме Telegram
CUT_ASK: dict[int, tuple[int, datetime]] = {}   # чат → (задание, когда показали): ждём время в этом видео
_cut_tasks: set = set()           # фоновые ожидания видео: ссылка держит задачу, пока она не кончилась
SRC_WORD = {"clock": "⏱ встали часы", "board": "📺 задержка табло клуба", "run": "🕐 ход часов от соседнего гола",
            "admin": "✅ отметка человека"}


def clips_game(key: str) -> dict:
    return ((read_live("clips.json") or {}).get("games") or {}).get(key) or {}


# что показать о голе — общее с пультом (goalplan.py, ADR-036): одно правило, одно окно, одно задание службы cuts
def goal_plan(key: str, score: str, protocol: list[dict] | None = None, video: str | None = None) -> dict | None:
    """План видео гола (goalplan.plan) по отметкам из replays.json и разбору службы clips."""
    return goalplan.plan(key, score, load_replays()["games"].get(key), clips_game(key), protocol, video)


def cut_jobs_for(plan: dict, now: datetime, prio: int = cutjobs.URGENT) -> list[int]:
    """Задания службы cuts на окна плана, и соседние окна поиска — заранее (goalplan.jobs_for)."""
    return goalplan.jobs_for(cut_store(), plan, now, prio)


def video_length(key: str, video: str) -> int | None:
    """Длина записи — от службы clips, если она разбирала этот самый ролик: у ссылки админа на другую запись длины
    не знаем, и окна по чужой длине обрезались бы."""
    board = clips_game(key)
    return board.get("length") if replay.same_video(board.get("video"), video) else None


def goal_head(key: str, score: str, protocol: list[dict] | None = None) -> str:
    """«🎬 04.10 Тверичи — Металлург, гол 1:0 · 1-й · Иванов» — для подписи видео."""
    day, home, away = key.split("|")
    p = protocol_goal(protocol, score)
    per = str(p.get("period") or "")
    per = f" · {per}-й" if per.isdigit() else f" · {html.escape(per)}" if per else ""
    who = f" · {html.escape(str(p['author']))}" if p.get("author") else ""
    return (f"🎬 <b>{day[8:10]}.{day[5:7]} {html.escape(tname(home))} — {html.escape(tname(away))}</b>, гол "
            f"<b>{html.escape(score)}</b>{per}{who}")


def cv_data(job: int, act: str, arg: int | None, score: str) -> str:
    """Кнопка под видео: задание, действие, секунда и гол. Гол — в кнопке, а не из задания: одно окно записи бывает у
    двух голов (одна смена табло на два гола подряд, ADR-031), а задание помнит того, кто попросил окно первым."""
    return f"cv:{job}:{act}:{'' if arg is None else arg}:{score.replace(':', '-')}"


def cut_kb(plan: dict, jobs: list[int]) -> InlineKeyboardMarkup:
    """Кнопки под видео гола: ответ человека по таблице ADR-036, раздел 1."""
    j = jobs[0]
    sc = plan["score"]

    def b(text: str, data: str) -> InlineKeyboardButton:
        job, act, *arg = data.split(":")
        return InlineKeyboardButton(text=text, callback_data=cv_data(int(job), act, int(arg[0]) if arg else None, sc))
    kind = plan["kind"]
    if kind == "exact":   # «⏪ / ⏩» под точной секундой — ещё и возражение ей (ADR-033, раздел 4)
        # своя же секунда (30 с сразу после отметки): «✅» от отметившего — один свидетель, а не два, — кнопки нет
        rows = [] if plan.get("own") else [[b("✅ Гол виден", f"{j}:y:{plan['t']}")]]
        rows += [[b("⏪ Гол раньше", f"{j}:e:{plan['t']}"), b("⏩ Гол позже", f"{j}:l:{plan['t']}")],
                 [b("🚫 Гола нет в записи", f"{j}:n")]]
    elif kind == "dispute":
        rows = [[b(f"✅ Верно по отметке {replay.fmt_clock(plan['t'])}", f"{j}:y:{plan['t']}")]]
        if plan.get("tb") is not None:
            rows.append([b(f"✅ Верно по табло {replay.fmt_clock(plan['tb'])}", f"{jobs[1]}:t:{plan['tb']}")])
        elif len(jobs) > 1:
            rows.append([b("Гол во втором видео — пришлю время", f"{jobs[1]}:x")])
        rows.append([b("🔎 Ни то ни другое — искать", f"{j}:s")])
    else:
        start = plan["windows"][0][0]
        rows = [[b(f"Гол на {replay.fmt_clock(t - start)}", f"{j}:t:{t}")] for t in plan.get("cand") or []]
        rows.append([b("Другое время" if rows else "Гол здесь — пришлю время", f"{j}:x")])
        rows.append([b("⏪ 3 мин раньше" if kind == "search" else "⏪ Ещё раньше", f"{j}:e"),
                     b("⏩ 3 мин позже" if kind == "search" else "⏩ Позже", f"{j}:l")])
        rows.append([b("⚠️ Табло сбилось", f"{j}:w")] if kind == "approx" else [b("🚫 Гола нет в записи", f"{j}:n")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cut_caption(plan: dict, protocol: list[dict] | None = None) -> list[str]:
    """Подписи видео: у альбома спора — у каждого своя, у остальных — одна."""
    head = goal_head(plan["key"], plan["score"], protocol)
    kind, wins = plan["kind"], plan["windows"]
    if kind == "exact":
        lead = plan["t"] - wins[0][0]
        return [f"{head}\n{SRC_WORD.get(plan.get('src'), '✅')} — гол на {replay.fmt_clock(lead)} этого видео. "
                + ("Это твоя отметка — её подтверждает кто-то другой. Гола тут нет — «⏪» или «⏩»: пришлю записи "
                   "раньше или позже." if plan.get("own") else
                   "Видишь гол — «✅ Гол виден». Нет — «⏪» или «⏩»: пришлю записи раньше или позже.")]
    if kind == "dispute":
        out = [f"{head}\n⚠️ Спор: отметка {replay.fmt_clock(plan['t'])} не сходится с табло"
               + (f" ({html.escape(plan['why'])})" if plan.get("why") else "") + ". Это видео — по отметке."]
        out += [f"{head}\nВторое видео — {html.escape(w[2])}." for w in wins[1:]]
        return out
    what = "Примерное место — " + html.escape(wins[0][2]) if kind == "approx" else \
        "Где гол, служба не знает: это 3 минуты записи " + html.escape(wins[0][2])
    return [f"{head}\n{what}. Где в этом видео гол? Нажми момент или пришли время в видео, например 1:05. Гола тут "
            "нет — «⏪» или «⏩»."]


def cut_fallback(plan: dict, err: str) -> str:
    """Видео не вырезалось: что случилось и ссылка VK с начала окна — тут без неё никак."""
    url = replay.at_link(plan["video"], plan["windows"][0][0])
    return (f"{goal_head(plan['key'], plan['score'])}\nВидео не вырезалось: {html.escape(err[:200])}."
            + (f' Запись в VK: <a href="{html.escape(url)}">с {replay.fmt_clock(plan["windows"][0][0])}</a>.'
               if url else ""))


def cut_split(ids: list[int]) -> tuple[list[dict], list[str], int]:
    """Задания по состоянию: (готовые с файлами, ошибки тех, что не вырежутся, сколько ещё режется)."""
    store = cut_store()
    done, errs, left = [], [], 0
    for i in ids:
        j = store.get(i) if store else None
        if j is None:
            errs.append("задание пропало из очереди")
        elif j["status"] == "error" and j["tries"] >= cutjobs.TRIES:
            errs.append(j.get("error") or "ошибка не записана")
        elif j["status"] == "done" and store.path(j):
            done.append(j)
        else:
            left += 1
    return done, errs, left


def cut_state(ids: list[int]) -> tuple[list[dict] | None, str]:
    """Готовы ли все задания: (задания с файлами, «») — все готовы; (None, ошибка) — какое-то не вырежется;
    (None, «») — ещё режутся."""
    done, errs, left = cut_split(ids)
    if errs:
        return None, errs[0]
    return (done, "") if not left else (None, "")


def video_of(job: dict) -> dict:
    """Размеры для Telegram: без них видео от бота в чате — «0:01» без перемотки (05.10)."""
    return {"duration": int(job.get("dur") or job.get("len") or 0) or None, "width": int(job.get("w") or 854),
            "height": int(job.get("h") or 480), "supports_streaming": True}


async def cut_send(bot: Bot, cid: int, jobs: list[dict], captions: list[str], kb: InlineKeyboardMarkup | None,
                   note: str = "") -> None:
    """Готовые видео в чат: одно — с подписью и кнопками; несколько — альбом, кнопки — сообщением note под ним."""
    store = cut_store()
    if len(jobs) == 1:
        await sending(lambda: bot.send_video(cid, FSInputFile(store.path(jobs[0])), caption=captions[0],
                                             reply_markup=kb, **video_of(jobs[0])))
        return
    media = [InputMediaVideo(media=FSInputFile(store.path(j)), caption=captions[k] if k < len(captions) else None,
                             parse_mode=ParseMode.HTML, **video_of(j)) for k, j in enumerate(jobs[:ALBUM_MAX])]
    await sending(lambda: bot.send_media_group(cid, media))
    if note or kb:
        try:   # альбом уже ушёл: без кнопок хуже, но второй раз его не шлём
            await sending(lambda: bot.send_message(cid, note or "Ответ — кнопкой:", reply_markup=kb))
        except Exception:
            logging.exception("video buttons to %s failed", cid)


def cut_ready(ids: list[int], partial: bool) -> tuple[list[dict] | None, str]:
    """Что слать: (видео, «») — пора; (None, ошибка) — не выйдет; (None, «») — ждём. partial — альбом голов матча:
    не вырезанные не держат готовые, когда резать больше нечего."""
    if not partial:
        return cut_state(ids)
    done, errs, left = cut_split(ids)
    if left:
        return None, ""
    return (done, "") if done else (None, errs[0] if errs else "видео нет")


def cut_forget(cid: int, ids: list[int]) -> None:
    """Видео не пришло — время текстом больше не считаем временем в нём: админ пришлёт время записи по ссылке."""
    if (CUT_ASK.get(cid) or (None,))[0] in ids:
        CUT_ASK.pop(cid, None)


async def cut_show(bot: Bot, cid: int, ids: list[int], captions: list[str], kb: InlineKeyboardMarkup | None,
                   fallback, note: str = "", partial: bool = False) -> None:
    """Видео из заданий службы cuts: готовы — сразу; нет — «⏳ Режу видео…», а когда готовы — видео вместо него.
    Не вырезались — fallback(ошибка): текст со ссылкой VK."""
    jobs, err = cut_ready(ids, partial)
    if jobs:
        await cut_send(bot, cid, jobs, [captions[ids.index(j["id"])] for j in jobs], kb, note)
        return
    if err:
        cut_forget(cid, ids)
        await bot.send_message(cid, fallback(err), disable_web_page_preview=True)
        return
    wait = await bot.send_message(cid, "⏳ Режу видео…" if len(ids) == 1 else f"⏳ Режу видео: {len(ids)}…")
    task = asyncio.create_task(cut_wait(bot, cid, wait.message_id, ids, captions, kb, fallback, note, partial))
    _cut_tasks.add(task)
    task.add_done_callback(_cut_tasks.discard)


async def cut_wait(bot: Bot, cid: int, wait_id: int, ids: list[int], captions: list[str],
                   kb: InlineKeyboardMarkup | None, fallback, note: str = "", partial: bool = False) -> None:
    """Ждём, пока служба cuts вырежет видео, и присылаем его вместо «⏳ Режу видео…». База занята или Telegram не
    принял видео — ссылка VK, а «⏳» всё равно убираем."""
    deadline = datetime.now(TZ) + CUT_WAIT
    jobs, err = None, ""
    try:
        jobs, err = cut_ready(ids, partial)
        while not jobs and not err and datetime.now(TZ) < deadline:
            await asyncio.sleep(CUT_POLL)
            jobs, err = cut_ready(ids, partial)
        if jobs:
            await cut_send(bot, cid, jobs, [captions[ids.index(j["id"])] for j in jobs], kb, note)
            TRACK.add("cuts_shown")
        else:
            raise RuntimeError(err or f"служба cuts не успела за {CUT_WAIT.seconds // 60} мин")
    except Exception as e:
        if not isinstance(e, RuntimeError):
            logging.exception("video to %s failed", cid)
        TRACK.add("cuts_failed")
        cut_forget(cid, ids)
        try:
            await bot.send_message(cid, fallback(str(e)), disable_web_page_preview=True)
        except Exception:
            logging.exception("video fallback to %s failed", cid)
    try:
        await bot.delete_message(cid, wait_id)
    except Exception:   # уже удалено — не важно
        pass


async def goal_video(bot: Bot, cid: int, plan: dict, protocol: list[dict] | None = None,
                     prio: int = cutjobs.URGENT) -> list[int]:
    """Видео гола по плану: задания, подписи, кнопки. После него время, присланное текстом, — время в этом видео."""
    ids = cut_jobs_for(plan, datetime.now(TZ), prio)
    # время текстом — в этом видео; у спора — во втором: первое — сама отметка
    CUT_ASK[cid] = (ids[-1], datetime.now(TZ), plan["score"])
    PREVIEW_ASK.pop(cid, None)
    await cut_show(bot, cid, ids, cut_caption(plan, protocol), cut_kb(plan, ids), lambda err: cut_fallback(plan, err),
                   note="Какое видео верно? Гол в другом месте второго видео — пришли время в нём, например 1:05."
                   if plan["kind"] == "dispute" else "")
    return ids


async def match_videos(bot: Bot, cid: int, day: str, i: int, g: dict, protocol: list[dict] | None = None) -> None:
    """«▶️ Все голы матча»: 30 с каждого гола с точной секундой одним альбомом — проверить матч целиком за минуту.
    Мимо — гол открывают кнопкой под альбомом."""
    key = match_key(g)
    plans = [p for p in (goal_plan(key, x["score"], protocol) for x in replay_goals(g, protocol))
             if p and p["kind"] == "exact"][:ALBUM_MAX] if cut_store() else []
    if not plans:
        await bot.send_message(cid, "У голов этого матча пока нет точной секунды — открой гол, пришлю видео для поиска.")
        return
    now = datetime.now(TZ)
    ids = [cut_jobs_for(p, now)[0] for p in plans]
    caps = [f"{goal_head(key, p['score'], protocol)}\n{SRC_WORD.get(p.get('src'), '✅')} — гол на "
            f"{replay.fmt_clock(p['t'] - p['windows'][0][0])}" for p in plans]
    rows, row = [], []
    for p in plans:
        row.append(InlineKeyboardButton(text=f"🎯 {p['score']}", callback_data=f"rp:g:{day}:{i}:{p['score']}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    rows += [row] if row else []
    note = (f"Голы с точной секундой — {len(plans)}: {', '.join(p['score'] for p in plans)}. Гол мимо или не "
            "виден — нажми его: пришлю видео с кнопками.")
    url = replay.at_link(plans[0]["video"], plans[0]["windows"][0][0])
    await cut_show(bot, cid, ids, caps, InlineKeyboardMarkup(inline_keyboard=rows),
                   lambda err: f"Видео голов не вырезались: {html.escape(err[:200])}."
                   + (f' Запись в VK: <a href="{html.escape(url)}">ссылка</a>.' if url else ""), note=note,
                   partial=True)


def window_plan(job: dict, score: str, start: int, span: int, what: str = "") -> dict:
    """План окна поиска от видео, которое человек уже смотрел: ⏪, ⏩, «Ни то ни другое — искать»."""
    return {"kind": "search", "video": job["video"], "length": video_length(job["match"], job["video"]),
            "key": job["match"], "score": score, "windows": [(start, span, what or f"с {replay.fmt_clock(start)}")],
            "cand": []}


def video_save(job: dict, score: str, sec: int, now: datetime, who: int | None, protocol: list[dict] | None,
               kind: str = "time") -> str:
    """Ответ по видео → строка журнала (ADR-033): в `seen` — какое окно человек смотрел и что выбрал. Ошибка или пусто."""
    g = live_by_key(job["match"])
    if not g:
        return "Матч пропал из файла службы live — отметку не записал."
    seen = {"job": job["id"], "from": job["start"], "len": job["len"], "pick": sec - job["start"]}
    add_mark(job["match"], score, kind, now, who, "video", protocol, video=job["video"], sec=sec, seen=seen)
    marks_apply(job["match"], g, now, protocol)
    return ""


def other_video(job: dict) -> bool:
    """Видео вырезано из записи, которая у матча уже не действует (админ прислал другую ссылку): отметка по нему
    переключила бы матч обратно на старую запись, и все опоры новой перестали бы действовать."""
    cur = (goal_marks().state(job["match"]) or {}).get("video")
    return bool(cur) and not replay.same_video(cur, job["video"])


async def video_answered(bot: Bot, cid: int, job: dict, score: str, sec: int, protocol: list[dict] | None) -> None:
    """Время записано — 30 с результата: ответ человека сразу становится тем, что увидят болельщики, и его видно."""
    length = video_length(job["match"], job["video"])
    plan = {"kind": "exact", "video": job["video"], "length": length, "key": job["match"], "score": score, "t": sec,
            "src": "admin", "own": True, "windows": [(*cutjobs.review_window(sec, length), "гол")]}
    await goal_video(bot, cid, plan, protocol)


@dp.callback_query(F.data.startswith("cv:"))
async def cb_cut(c: CallbackQuery):
    if not c.from_user or c.from_user.id not in preview_people() or not c.message:
        await c.answer()
        return
    _, job_s, act, arg_s, sc = (c.data.split(":") + [""] * 5)[:5]
    try:
        job = cut_store().get(int(job_s))
    except (sqlite3.Error, ValueError, AttributeError):
        job = None
    score = sc.replace("-", ":") if replay.SCORE_RE.fullmatch(sc.replace("-", ":")) else (job or {}).get("score")
    if not job or not job.get("match") or not score or not act:
        await c.answer("Это видео уже не действует — открой гол заново в /replay", show_alert=True)
        return
    now, cid = datetime.now(TZ), c.message.chat.id
    arg = int(arg_s) if arg_s.isdigit() else None
    if act in ("t", "y", "n", "w") and other_video(job) or act in ("e", "l") and arg is not None and other_video(job):
        await c.answer("Это видео из прежней записи матча — открой гол заново в /replay", show_alert=True)
        return
    g = live_by_key(job["match"])
    league = await published_league()
    protocol = protocol_of(league, g) if g else None
    done = lambda: c.message.edit_reply_markup(reply_markup=None)   # noqa: E731 — ответ дан: кнопки убираем
    if act in ("t", "y") and arg is not None:
        err = video_save(job, score, arg, now, c.from_user.id, protocol, kind="time" if act == "t" else "confirm")
        if err:
            await c.answer(err, show_alert=True)
            return
        TRACK.add("video_answers" if act == "t" else "video_confirms")
        await c.answer(f"Записал: гол {score} на {replay.fmt_clock(arg)} записи" if act == "t"
                       else "Записал: гол виден")
        await safe_markup(done)
        if act == "t":
            await video_answered(c.bot, cid, job, score, arg, protocol)
        return
    if act in ("e", "l") and arg is not None:   # под 30 с точной секунды: гола тут нет — возражение секунде
        err = video_save(job, score, arg, now, c.from_user.id, protocol, kind="reject")
        if err:
            await c.answer(err, show_alert=True)
            return
        TRACK.add("video_rejects")
        await safe_markup(done)
    if act in ("e", "l", "s"):
        length = video_length(job["match"], job["video"])
        if act == "s":   # «Ни то ни другое — искать»: 3 минуты вокруг отметки
            w = cutjobs.search_window(job["start"] + job["len"] // 2, length)
        else:   # у точной секунды — 2 минуты до или после клипа (ADR-036, раздел 1), у поиска — шаг 3 минуты
            span = cutjobs.STEP_EXACT if job["len"] <= cutjobs.REVIEW_BEFORE + cutjobs.REVIEW_AFTER else cutjobs.SEARCH
            w = cutjobs.neighbour(job["start"], job["len"], -1 if act == "e" else 1, length, span)
        if not w:
            await c.answer("Дальше записи нет", show_alert=True)
            return
        await c.answer()
        TRACK.add("video_steps")
        await goal_video(c.bot, cid, window_plan(job, score, *w), protocol)
        return
    if act == "x":
        CUT_ASK[cid] = (job["id"], now, score)
        PREVIEW_ASK.pop(cid, None)
        await c.answer()
        await c.message.answer(f"Гол <b>{html.escape(score)}</b>: пришли, на какой секунде этого видео гол, — "
                               f"например 1:05 (видео — {replay.fmt_clock(job['len'])}).")
        return
    if act in ("n", "w"):
        err = replay_mark(job["match"], score, "absent" if act == "n" else "wrong", job["video"], now, protocol,
                          who=c.from_user.id, via="video")
        if err:
            await c.answer(err, show_alert=True)
            return
        TRACK.add("video_marks")
        await c.answer("Записал: гола нет в записи" if act == "n" else "Записал: табло сбилось")
        await safe_markup(done)
        return
    await c.answer()


async def safe_markup(call) -> None:
    """Убрать кнопки под ответом: сообщение старше 48 часов или уже без кнопок — не важно."""
    try:
        await call()
    except Exception:
        pass


def cut_waiting(m: Message) -> bool:
    """Ждём ли время в видео: видео прислано позже, чем открыт /replay или превью, не дольше REPLAY_WAIT назад."""
    ask = CUT_ASK.get(m.chat.id)
    if not (ask and m.text and m.from_user and m.from_user.id in preview_people()
            and datetime.now(TZ) - ask[1] <= REPLAY_WAIT):
        return False
    rp, pv = REPLAY_ASK.get(m.chat.id), PREVIEW_ASK.get(m.chat.id)
    return not (rp and rp[3] > ask[1]) and not (pv and pv[1] > ask[1])


async def h_cut_time(m: Message):
    job_id, _, score = CUT_ASK[m.chat.id]
    store = cut_store()
    job = store.get(job_id) if store else None
    if not job or not job.get("match") or other_video(job):
        CUT_ASK.pop(m.chat.id, None)
        await m.answer("Это видео уже не действует — открой гол заново в /replay.")
        return
    text = m.text.strip()
    link = replay.parse_link(text)
    if link and link[1] is not None and replay.same_video(link[0], job["video"]):
        sec = link[1]   # ссылка VK «с текущим временем» — время записи, а не видео
    else:
        t = replay.parse_clock(text)
        if t is None or t > job["len"] + 5:
            await m.answer(f"Не понял время. Пришли, на какой секунде видео гол: например 1:05 (видео — "
                           f"{replay.fmt_clock(job['len'])}). Время записи — ссылкой VK «с текущим временем».")
            return
        sec = job["start"] + t
    CUT_ASK.pop(m.chat.id, None)
    g = live_by_key(job["match"])
    league = await published_league()
    protocol = protocol_of(league, g) if g else None
    err = video_save(job, score, sec, datetime.now(TZ), m.from_user.id, protocol)
    if err:
        await m.answer(err)
        return
    TRACK.add("video_answers")
    await m.answer(f"Записал: гол <b>{html.escape(score)}</b> на {replay.fmt_clock(sec)} записи.")
    await video_answered(m.bot, m.chat.id, job, score, sec, protocol)


dp.message.register(h_cut_time, cut_waiting)   # раньше h_lost: время в видео — не «непонятое сообщение»


# ---------- спор по отметке (ADR-033) ----------
# Служба clips проверила отметку человека табло и ходом часов — не сошлось. Точной секунды у гола теперь нет ни от
# отметки, ни от табло. Бот один раз пишет отметившему (неизвестно кто — админам): что не сошлось, обе версии ссылками
# и кнопку открыть гол в /replay — там прислать верное время или отозвать отметку.


def load_disputes(now: datetime) -> dict:
    was = admin.read_json(DISPUTES_FILE, {})
    edge = (now - DISPUTE_KEEP).isoformat(timespec="seconds")
    return {k: v for k, v in (was if isinstance(was, dict) else {}).items() if isinstance(v, str) and v >= edge}


def match_place(key: str) -> tuple[str, int] | None:
    """(день, номер в файле дня службы live) — для кнопки «Открыть гол» в /replay."""
    games = (read_live(f"{key[:10]}.json") or {}).get("games") or []
    i = next((n for n, g in enumerate(games) if isinstance(g, dict) and match_key(g) == key), None)
    return (key[:10], i) if i is not None else None


def dispute_todo(clips: dict | None, marked: dict, sent: dict) -> list[tuple[str, str, dict]]:
    """Споры, о которых ещё не писали: (ключ матча, счёт, вердикт). Отметка та же, что проверена."""
    out = []
    for key, game in sorted(((clips or {}).get("games") or {}).items()):
        e = (marked or {}).get(key) or {}
        for score in sorted(replay.disputed(e, game)):
            c = game["checks"][score]
            if f"{key}|{score}|{c['t']}" not in sent:
                out.append((key, score, c))
    return out


def dispute_note(key: str, score: str, c: dict) -> str:
    """Под альбомом спора: что не сошлось и что делать — без ссылок, видео выше (ADR-036)."""
    day, home, away = key.split("|")
    return (f"⚠️ <b>Спор по голу {html.escape(score)}</b> · {html.escape(tname(home))} — {html.escape(tname(away))}, "
            f"{day[8:10]}.{day[5:7]}\nОтметка {replay.fmt_clock(c['t'])} не сходится с табло: "
            f"{html.escape(', '.join(c.get('against') or []))}. Первое видео — по отметке, второе — по табло. Пока спор "
            "не решён, точной секунды у гола нет ни от отметки, ни от табло — у болельщиков «≈».")


def dispute_text(key: str, score: str, c: dict, game: dict, video: str) -> str:
    """Спор ссылками VK — только если видео не вырезалось (VK не отдал запись)."""
    day, home, away = key.split("|")
    lines = [f"⚠️ <b>Спор по голу {html.escape(score)}</b> · {html.escape(tname(home))} — {html.escape(tname(away))}, "
             f"{day[8:10]}.{day[5:7]}",
             f"Отметка {replay.fmt_clock(c['t'])} не сходится с табло: {html.escape(', '.join(c.get('against') or []))}."]
    mine = replay.at_link(video, max(0, c["t"] - replay.EXACT_LEAD))
    if mine:
        lines.append(f'Отметка: <a href="{html.escape(mine)}">{replay.fmt_clock(c["t"])}</a>')
    b = (game.get("goals") or {}).get(score) or {}
    if isinstance(b.get("change"), (int, float)):
        at = max(0, round(b["change"] - replay.CHANGE_LEAD))
        lines.append(f'Табло: счёт сменился на {replay.fmt_clock(round(b["change"]))} — '
                     f'<a href="{html.escape(replay.at_link(video, at) or "")}">смотреть с {replay.fmt_clock(at)}</a>')
    lines.append("Пока спор не решён, точной секунды у гола нет ни от отметки, ни от табло — у болельщиков «≈». "
                 "Пришли верное время или отзови отметку.")
    return "\n".join(lines)


async def dispute_step(bot: Bot, now: datetime) -> int:
    """Раз в минуту: новые споры по отметкам — отметившему (кто неизвестен или ушёл — админам), по одному разу."""
    clips = read_live("clips.json")
    if not ADMIN_IDS or quiet(now) or clips is None:
        return 0
    marked = load_replays()["games"]
    old = load_disputes(now)
    sent = dict(old)
    n = 0
    league = None
    for key, score, c in dispute_todo(clips, marked, sent)[:DISPUTE_MAX]:
        game = clips["games"][key]
        video = (marked.get(key) or {}).get("video") or game.get("video")
        mark = next((r for r in reversed(marks.active(goal_marks().of(key), score))
                     if r["kind"] == "time" and r.get("sec") == c["t"]), None)
        who = (mark or {}).get("who")
        to = [who] if who in ADMIN_IDS else sorted(ADMIN_IDS)
        place = match_place(key)
        opener = [InlineKeyboardButton(text="🛠 Открыть гол", callback_data=f"rp:g:{place[0]}:{place[1]}:{score}")] \
            if place else None
        # обе версии видео (ADR-036): вырезаны заранее — альбом и кнопки; ещё режутся — в следующую минуту; VK не
        # отдал запись — как раньше, ссылками
        if league is None:
            league = await published_league()
        g = live_by_key(key)
        protocol = protocol_of(league, g) if g else None
        plan = goal_plan(key, score, protocol) if cut_store() else None
        jobs, err, ids = None, "нет плана видео", []
        if plan and plan["kind"] == "dispute":
            ids = cut_jobs_for(plan, now, cutjobs.SEND)
            jobs, err = cut_state(ids)
            wait_key = f"wait|{key}|{score}|{c['t']}"   # с какой минуты спор ждёт видео
            sent.setdefault(wait_key, admin.iso(now))
            if not jobs and not err:
                if now - datetime.fromisoformat(sent[wait_key]) < DISPUTE_CUT_WAIT:
                    continue   # режется — в следующую минуту
                err = "служба cuts не вырезала видео"   # служба стоит: спор не должен ждать её вечно
        got = 0
        for cid in to:
            try:
                if jobs:
                    kb = cut_kb(plan, ids)
                    if opener:
                        kb.inline_keyboard.append(opener)
                    await cut_send(bot, cid, jobs, cut_caption(plan, protocol), kb, dispute_note(key, score, c))
                else:
                    kb = InlineKeyboardMarkup(inline_keyboard=[opener]) if opener else None
                    await sending(lambda: bot.send_message(cid, dispute_text(key, score, c, game, video),
                                                           reply_markup=kb, disable_web_page_preview=True))
                got += 1
            except Exception:
                logging.exception("dispute to %s failed", cid)
            await asyncio.sleep(0.05)
        if got:   # не дошло ни до кого — попробуем через минуту
            sent[f"{key}|{score}|{c['t']}"] = admin.iso(now)
            sent.pop(f"wait|{key}|{score}|{c['t']}", None)
            n += 1
    if sent != old:
        write_atomic(DISPUTES_FILE, sent)
    if n:
        TRACK.add("disputes", n)
    return n


# ---------- кадры табло для разметки (ADR-030, дополнение 06.10) ----------
# Табло клуба-хозяина не размечено — служба clips не находит голов его домашних матчей: ни секунд, ни превью, ни
# клипов. Кадр с сеткой для разметки она держит в probe/grids/<клуб>.png, а в clips.json — `boards`. Бот присылает
# кадр админам один раз на клуб: переслать в сессию Claude — разметка одним PR в boards.json, после выкладки служба
# сама переберёт матчи клуба.


def load_grids() -> dict:
    try:
        data = json.loads(GRIDS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def grid_todo(boards: dict | None, sent: dict) -> list[tuple[str, dict]]:
    """Клубы без разметки табло, чей кадр есть и ещё не уходил админам: (клуб, {matches, key, grid})."""
    return [(club, e) for club, e in sorted((boards or {}).items())
            if isinstance(e, dict) and isinstance(e.get("grid"), str) and club not in sent]


def grid_caption(club: str, e: dict) -> str:
    n = int(e.get("matches") or 0)
    key = e.get("key") or ""
    last = f" (последний — {key[8:10]}.{key[5:7]} {key_title(key)})" if key.count("|") == 2 else ""
    return (f"🖼 <b>Табло «{html.escape(tname(club))}» не размечено</b> — без секунд голов, превью и клипов "
            f"{n} {plural(n, 'домашний матч', 'домашних матча', 'домашних матчей')}{html.escape(last)}.\n"
            f"Перешли этот файл в сессию Claude: «разметь табло <code>{html.escape(club)}</code> в boards.json по "
            "grid.png». После выкладки служба clips сама переберёт матчи клуба.")


async def grid_step(bot: Bot, now: datetime) -> int:
    """Раз в минуту: кадр табло клуба без разметки — админам, файлом: фото Telegram сжимает, а клетки табло
    размечают по пикселям. Один раз на клуб; клуб разметили — запись о нём стираем. Ночью молчим, как тревоги."""
    found = read_live("clips.json")
    if not ADMIN_IDS or quiet(now) or found is None:
        return 0
    boards = {c: e for c, e in (found.get("boards") or {}).items() if isinstance(e, dict)}
    old = load_grids()
    sent = {c: v for c, v in old.items() if c in boards}
    n = 0
    for club, e in grid_todo(boards, sent)[:GRID_MAX]:
        path = BASE / e["grid"]
        if not path.is_file():
            continue
        caption = grid_caption(club, e)
        got, file_id = [], None
        for cid in sorted(ADMIN_IDS):
            try:
                msg = await sending(lambda: bot.send_document(
                    cid, file_id or FSInputFile(path, filename=f"grid-{club}.png"), caption=caption))
                got.append(cid)
                file_id = file_id or (msg.document.file_id if msg.document else None)
            except Exception:
                logging.exception("grid to %s failed", cid)
            await asyncio.sleep(0.05)
        if got:   # не дошло ни до кого — попробуем через минуту
            sent[club] = {"at": admin.iso(now), "key": e.get("key")}
            n += 1
    if sent != old:
        write_atomic(GRIDS_FILE, sent)
    if n:
        TRACK.add("grids", n)
    return n


# ---------- болельщик пишет живому человеку (ADR-025) ----------
# Бот на непонятое сообщение отвечает стикером и кнопкой (ADR-005) — то есть вежливо не слышит.
# Кнопка «Написать живому человеку» это чинит: нажал — следующее сообщение уходит ADMIN_IDS.

def load_feedback() -> dict[str, dict]:
    was = admin.read_json(FEEDBACK_FILE, {})
    return was if isinstance(was, dict) else {}


FEEDBACK = load_feedback()


def feedback_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=f"✍️ {B_WRITE}", callback_data="fb:ask")]


def feedback_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[feedback_row()])


def lost_kb(chat_id: int, now: datetime) -> InlineKeyboardMarkup:
    """Ответ на непонятое сообщение: мини-апп, матчи дня и — если есть кому передать — живой человек."""
    rows = list(app_kb(today=True).inline_keyboard)
    if ADMIN_IDS and not feedback_soon(chat_id, now):
        rows.append(feedback_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def feedback_wait(chat_id: int, now: datetime, asked: bool) -> None:
    """Запомнить нажатие или снять ожидание. `sent` — когда последнее письмо ушло: по нему лимит.
    Заодно выбрасываем чужие записи старше суток: файл не должен расти с каждым нажатием."""
    rec = FEEDBACK.get(str(chat_id)) or {}
    old = now - timedelta(days=1)
    for k, v in list(FEEDBACK.items()):
        times = [admin.parse_iso((v or {}).get(f)) for f in ("asked", "sent")]
        if all(t is None or t < old for t in times):
            del FEEDBACK[k]
    FEEDBACK[str(chat_id)] = {"asked": admin.iso(now) if asked else None,
                              "sent": rec.get("sent") if asked else admin.iso(now)}
    write_atomic(FEEDBACK_FILE, FEEDBACK)


def feedback_waiting(chat_id: int, now: datetime) -> bool:
    """Ждём ли письмо от этого чата. Нажатие живёт FEEDBACK_WAIT: перезапуск его не теряет."""
    at = admin.parse_iso((FEEDBACK.get(str(chat_id)) or {}).get("asked"))
    return at is not None and now - at <= FEEDBACK_WAIT


def feedback_soon(chat_id: int, now: datetime) -> bool:
    """Слишком часто: с этого чата письмо уже ушло меньше FEEDBACK_EVERY назад."""
    at = admin.parse_iso((FEEDBACK.get(str(chat_id)) or {}).get("sent"))
    return at is not None and now - at < FEEDBACK_EVERY


def feedback_text(chat_id: int, text: str) -> str:
    """Письмо админу: текст болельщика как есть (через escape — это чужой ввод) и ссылка для ответа.
    Ни имени, ни @username не передаём (ADR-025, раздел 2): для ответа хватает ссылки."""
    body = text.strip()[:FEEDBACK_MAX] + ("…" if len(text.strip()) > FEEDBACK_MAX else "")
    return (f"✍️ <b>Письмо от болельщика</b>\n\n{html.escape(body)}\n\n"
            f'<a href="tg://user?id={chat_id}">Ответить</a> · чат <code>{chat_id}</code>')


async def feedback_send(bot: Bot, chat_id: int, text: str, now: datetime) -> int:
    """Передать письмо админам. Ни до кого не дошло — честно говорим, что не передали."""
    sent = 0
    for cid in sorted(ADMIN_IDS):
        try:
            await say(bot, cid, lambda: (feedback_text(chat_id, text), None))
            sent += 1
        except Exception:
            logging.exception("feedback to admin failed")
        await asyncio.sleep(0.05)
    feedback_wait(chat_id, now, asked=False)
    TRACK.add("feedback" if sent else "feedback_lost")
    TRACK.flush()
    return sent


@dp.callback_query(F.data == "fb:ask")
async def cb_feedback(c: CallbackQuery):
    """Нажал «Написать живому человеку»: ждём одно сообщение."""
    cid = c.message.chat.id
    now = datetime.now(TZ)
    if not ADMIN_IDS:
        await c.answer("Передавать некому: в приложении не задан админ", show_alert=True)
        return
    if feedback_soon(cid, now):
        await c.answer("Уже передал предыдущее. Следующее — через десять минут", show_alert=True)
        return
    feedback_wait(cid, now, asked=True)
    await c.answer("Жду сообщение")
    await say(c.bot, cid, lambda: (
        "Напиши одним сообщением: что сломалось или чего не хватает. Передам владельцу приложения — "
        "он может ответить прямо здесь.\n\nСкриншот прикладывать не надо: передаю только текст.", None))


TODAY_WORDS = re.compile(r"сегодн|матч|игр[аыуе]?\b|расписан|когда|сч[её]т|трансляц", re.I)


@dp.message()   # последним: всё остальное (ADR-005 — бот не молчит)
async def h_lost(m: Message):
    now = datetime.now(TZ)
    if m.text and feedback_waiting(m.chat.id, now):   # нажал «написать» — это и есть письмо (ADR-025)
        ok = await feedback_send(m.bot, m.chat.id, m.text, now)
        await say(m.bot, m.chat.id, lambda: (
            "Передал. Могут ответить прямо здесь." if ok else
            "Не получилось передать — попробуй ещё раз позже.", app_kb()))
        return
    TRACK.add("lost")
    if m.text and TODAY_WORDS.search(m.text):   # «когда игра?», «какой счёт» — матчи дня
        await send_today(m.bot, m.chat.id)
        return
    await send_sticker(m.bot, m.chat.id, "tap", reply_markup=ReplyKeyboardRemove())
    await say(m.bot, m.chat.id, lambda: (lost_text(), lost_kb(m.chat.id, now)))

# ---------- напоминания ----------

# Напоминание уходит в свой час (REMIND_TODAY_AT, REMIND_TOMORROW_AT — менять нельзя без обсуждения),
# но час можно и пропустить: выкладка, перезапуск службы или упавший туннель. Поэтому бот помнит, какие
# напоминания уже ушли и кому именно (reminded.json), и в первые REMIND_CATCHUP часов догоняет
# пропущенное. Повторно тому, кто уже получил, не пишем.

SLOTS = (("today", REMIND_TODAY_AT), ("tomorrow", REMIND_TOMORROW_AT))


def slot_at(day: date, kind: str) -> datetime:
    """Когда по Москве уходит напоминание слота."""
    return datetime.combine(day, dict(SLOTS)[kind], TZ)


def slot_key(day: date, kind: str) -> str:
    return f"{day.isoformat()}:{kind}"


def next_reminder(now: datetime) -> tuple[datetime, str]:
    slots = [(slot_at(now.date() + timedelta(days=d), kind), kind)
             for d in (0, 1) for kind, _ in SLOTS]
    return min(s for s in slots if s[0] > now)


def load_reminded() -> dict[str, dict] | None:
    """Что уже разослано: слот → {done, tries, sent}.

    None — файла нет или он испорчен: это первый запуск с ним, и прошедшие слоты могла разослать
    прежняя копия бота. Их не догоняем: второе напоминание об одной игре выглядит как сбой."""
    try:
        raw = json.loads(REMINDED_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    return {k: {"done": bool(v.get("done")), "tries": int(v.get("tries") or 0),
                "sent": [str(x) for x in v.get("sent") or []]}
            for k, v in raw.items() if isinstance(k, str) and isinstance(v, dict)}


def save_reminded(done: dict[str, dict], today: date | None = None) -> None:
    """Записать, храня только последние REMINDED_KEEP дней: догонять старое уже не надо."""
    since = ((today or datetime.now(TZ).date()) - timedelta(days=REMINDED_KEEP)).isoformat()
    write_atomic(REMINDED_FILE, {k: v for k, v in done.items() if k[:10] >= since})


def due_slots(now: datetime, done: dict[str, dict]) -> list[tuple[date, str]]:
    """Слоты, которые должны были уйти, но не ушли (или ушли не всем): их и догоняем.

    Старше REMIND_CATCHUP не трогаем — к вечеру утреннее «сегодня игра» уже не новость, — и
    ночью молчим (QUIET_FROM…QUIET_TO), как и остальные рассылки."""
    if quiet(now):
        return []
    out = []
    for d in (now.date() - timedelta(days=1), now.date()):
        for kind, _ in SLOTS:
            at = slot_at(d, kind)
            rec = done.get(slot_key(d, kind)) or {}
            if at <= now < at + REMIND_CATCHUP and not rec.get("done") \
                    and (rec.get("tries") or 0) < REMIND_TRIES:
                out.append((d, kind))
    return sorted(out, key=lambda s: slot_at(*s))


def first_run_reminded(now: datetime) -> dict[str, dict]:
    """Первый запуск с reminded.json: слоты, чей час уже прошёл, считаем закрытыми. На диск их не
    пишем — файл появится с первой же рассылкой."""
    return {slot_key(d, k): {"done": True, "tries": 0, "sent": []} for d, k in due_slots(now, {})}


REMINDED = load_reminded()
if REMINDED is None:
    REMINDED = first_run_reminded(datetime.now(TZ))


async def raskat_open_broadcast(bot: Bot) -> int:
    """Зачёт включили — один раз зовём лист ожидания, после чего лист пустеет."""
    if not raskat_api() or not WAITLIST:
        return 0
    sent = 0
    for cid in sorted(WAITLIST):
        try:
            await say(bot, cid, lambda: (raskat_open_text(), raskat_kb()))
            sent += 1
        except TelegramForbiddenError:   # бота заблокировали
            TRACK.add("blocked")
        except Exception:
            logging.exception("raskat to %s failed", cid)
        WAITLIST.discard(cid)   # после каждого: перезапуск не позовёт второй раз
        save_waitlist(WAITLIST)
        await asyncio.sleep(0.05)
    logging.info("raskat waitlist called: %d", sent)
    TRACK.add("raskat_call_sent", sent)
    TRACK.note({"kind": "raskat", "sent": sent})
    TRACK.flush()
    return sent


def remind_mark(cid: int, m: dict) -> str:
    """Ключ «этому чату про этот матч»: по нему догон не пишет второй раз тому, кто уже получил."""
    return f"{cid}|{match_key(m)}"


def ledger(day: date, kind: str) -> dict:
    """Запись журнала дня: кому из рассылки или зова (ADR-023) уже написали."""
    return REMINDED.setdefault(slot_key(day, kind), {"done": False, "tries": 0, "sent": []})


def remember(rec: dict, mark: str, now: datetime) -> None:
    """Отметить и сразу записать на диск: перезапуск посреди рассылки её не повторит."""
    rec["sent"].append(mark)
    save_reminded(REMINDED, now.date())


async def send_reminders(bot: Bot, kind: str, day: date, league: dict | None,
                         rec: dict | None = None, now: datetime | None = None) -> tuple[int, int]:
    """Напоминания о матчах дня day подписчикам их команд. Утром — со стикером «Сегодня игра».

    `rec` — запись слота из reminded.json: кому уже написали. С ней рассылку можно продолжить
    после перезапуска, не повторяясь; без неё это обычная разовая рассылка."""
    now = now or datetime.now(TZ)
    plan = reminder_plan(SUBS, day, league, read_live(f"{day.isoformat()}.json"), read_live("schedule.json"))
    games = games_of(league)
    was = set((rec or {}).get("sent") or [])
    stickered: set[int] = set()
    sent = failed = 0
    started = asyncio.get_running_loop().time()
    for cid, m, team in plan:
        if cid not in SUBS:   # заблокировал бота по ходу рассылки
            continue
        mark = remind_mark(cid, m)
        if mark in was:       # догон: этому чату про этот матч уже написали
            continue
        start = start_of(m)
        if start and start <= now:   # догон затянулся: матч уже начался, «сегодня в 17:00» поздно
            continue
        try:
            if kind == "today" and cid not in stickered:
                stickered.add(cid)
                await send_sticker(bot, cid, "gameday")
            await say(bot, cid, lambda m=m, team=team: (reminder_text(m, kind, team, games), match_kb(m)))
            sent += 1
            if rec is not None:
                remember(rec, mark, now)
        except TelegramForbiddenError:   # бота заблокировали
            unsubscribe(cid, blocked=True)
            failed += 1
        except Exception:
            logging.exception("send to %s failed", cid)
            failed += 1
        await asyncio.sleep(0.05)
    TRACK.add("remind_sent", sent)
    TRACK.add("remind_fail", failed)
    TRACK.note({"kind": f"remind_{kind}", "day": day.isoformat(), "sent": sent, "failed": failed,
                "seconds": round(asyncio.get_running_loop().time() - started)})
    TRACK.flush()
    return sent, failed


async def fire_reminder(bot: Bot, day: date, kind: str, now: datetime | None = None) -> int:
    """Отправить напоминание слота и записать, чем оно кончилось.

    Слот закрываем (`done`) только если никто не остался без напоминания: упал туннель на всех —
    запись остаётся открытой, и догон вернётся к ней, пока слот не старше REMIND_CATCHUP."""
    now = now or datetime.now(TZ)
    rec = REMINDED.setdefault(slot_key(day, kind), {"done": False, "tries": 0, "sent": []})
    rec["done"] = False
    rec["tries"] = (rec.get("tries") or 0) + 1
    save_reminded(REMINDED, now.date())
    games_day = day + timedelta(days=1 if kind == "tomorrow" else 0)
    try:
        sent, failed = await send_reminders(bot, kind, games_day, await published_league(), rec, now)
    except Exception:
        logging.exception("reminders failed")
        return 0
    rec["done"] = not failed
    save_reminded(REMINDED, now.date())
    return sent


# ---------- зовы: раскат дня и прогноз (ADR-023) ----------

# Мини-апп обещает «сообщения о Раскате» галочкой в настройках (raskat_fans.messages), и исполнить
# это обещание может только бот. База — та же, что у службы api: в WAL читателей сколько угодно.

_stores: tuple | None = None


def stores() -> tuple:
    """(зачёт «Раската», прогнозы) из state.db. Базы нет — (None, None): зовов не будет."""
    global _stores
    if _stores is None:
        if not STATE_DB.exists():
            return None, None
        conn = sqlite3.connect(STATE_DB, isolation_level=None, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout=5000")
        _stores = (RaskatStore(conn), predict.PredictStore(conn))
    return _stores


def cant_reach(err: Exception) -> bool:
    """Telegram этому человеку не доставит: заблокировал бота или не начинал диалог."""
    if isinstance(err, TelegramForbiddenError):
        return True
    return isinstance(err, TelegramBadRequest) and bool(
        re.search(r"chat not found|can't initiate|user is deactivated", str(err), re.I))


def in_window(now: datetime, since: time, until: time) -> bool:
    t = now.astimezone(TZ).time()
    return since <= t < until


def raskat_call_text(streak: int) -> str:
    """Зов в раскат дня. Серия — единственная причина, по которой он работает."""
    if streak:
        days = plural(streak, "день", "дня", "дней")
        head = f"{e('fire')} <b>Серия {streak} {days}</b>"
        tail = "Раскат на сегодня готов. Не соберёшь — серия оборвётся."
    else:
        head = f"{e('stick')} <b>Раскат на сегодня готов</b>"
        tail = "Поле дня уже ждёт: очки за скорость, серия за дни подряд."
    return f"{head}\n\n{tail}"


async def raskat_call(bot: Bot, now: datetime) -> int:
    """Зов в раскат дня (ADR-023, раздел 2): раз в день тем, у кого стоит галочка и кто играл
    на этой неделе, но сегодня ещё не собрал. Отказ Telegram снимает галочку: больше не зовём."""
    rs, _ = stores()
    if rs is None or not raskat_api() or quiet(now) or not in_window(now, RASKAT_CALL_FROM, RASKAT_CALL_TO):
        return 0
    day = now.astimezone(TZ).date()
    rec = ledger(day, "raskat")
    was = set(rec["sent"])
    since = (day - timedelta(days=RASKAT_CALL_DAYS)).isoformat()
    sent = off = 0
    for fan, streak in rs.to_call(day.isoformat(), since):
        if str(fan) in was:
            continue
        try:
            await say(bot, fan, lambda st=streak: (raskat_call_text(st), raskat_kb()))
            sent += 1
        except Exception as err:
            if cant_reach(err):
                rs.settings(fan, messages=False)
                off += 1
            else:
                logging.exception("raskat call failed")
        remember(rec, str(fan), now)   # и при отказе: второй раз за день не пробуем
        await asyncio.sleep(0.05)
    if sent or off:
        TRACK.add("raskat_call", sent)
        TRACK.add("raskat_call_off", off)
        TRACK.note({"kind": "raskat_call", "sent": sent, "off": off})
        TRACK.flush()
    return sent


def predict_call_text(m: dict, team: str | None, home: int, away: int) -> str:
    """Зов на прогноз: кто играет, когда, что думает трибуна. Голосуют в мини-аппе (ADR-020)."""
    h, a = predict.shares(home, away)
    votes = home + away
    teams_line = teams_html(m, team)
    when = start_of(m)
    lines = [f"{e('fire')} <b>Кто победит?</b>", "", teams_line]
    lines.append(f"Сегодня в {when.astimezone(TZ):%H:%M} МСК" if when else "Сегодня")
    if votes:
        side = m["home"] if h >= a else m["away"]
        lines += ["", f"Трибуна: {max(h, a)}% за «{html.escape(tname(side))}» · "
                      f"{votes} {plural(votes, 'голос', 'голоса', 'голосов')}"]
    else:
        lines += ["", "Голосов пока нет — твой будет первым."]
    lines += ["", "Голос принимается до стартового свистка."]
    return "\n".join(lines)


async def predict_call(bot: Bot, now: datetime) -> int:
    """Зов на прогноз (ADR-023, раздел 3): по одному на человека в день, за два часа до матча.
    Матчи отсортированы по началу, поэтому зовём на ближайший неотголосованный."""
    _, pr = stores()
    if pr is None or not predict_on() or quiet(now):
        return 0
    day = now.astimezone(TZ).date()
    rec = ledger(day, "predict")
    was = set(rec["sent"])
    ms = sort_matches(day_matches(day, await published_league(), read_live(f"{day.isoformat()}.json"),
                                  read_live("schedule.json"), now))
    sent = 0
    for m in ms:
        start = start_of(m)
        if start is None or not (start - PREDICT_CALL_BEFORE <= now < start - PREDICT_CALL_LAST):
            continue
        if m.get("score") or m.get("status") in predict.CLOSED:   # уже идёт, кончился или перенесён
            continue
        key = match_key(m)
        voted = pr.voted(key)
        home, away = pr.counts(key)
        for cid, team in recipients(SUBS, m):
            if str(cid) in was or cid in voted:
                continue
            try:
                await say(bot, cid, lambda m=m, team=team: (predict_call_text(m, team, home, away), match_kb(m)))
                sent += 1
            except Exception as err:
                if cant_reach(err):
                    unsubscribe(cid, blocked=True)
                else:
                    logging.exception("predict call failed")
            was.add(str(cid))
            remember(rec, str(cid), now)
            await asyncio.sleep(0.05)
    if sent:
        TRACK.add("predict_call", sent)
        TRACK.note({"kind": "predict_call", "sent": sent})
        TRACK.flush()
    return sent


async def reminder_loop(bot: Bot):
    """Один путь и для напоминания в свой час, и для догона: слот уходит, как только пришло его
    время и он ещё не закрыт. Просыпаемся к ближайшему слоту, но не реже CATCHUP_EVERY — иначе
    о неудавшейся рассылке узнали бы только к следующему слоту, когда напоминать уже поздно."""
    while True:
        await raskat_open_broadcast(bot)   # отдельного цикла не плодим
        now = datetime.now(TZ)
        for day, kind in due_slots(now, REMINDED):
            logging.info("напоминание %s", slot_key(day, kind))
            await fire_reminder(bot, day, kind, now)
        for call in (raskat_call, predict_call):   # зовы ADR-023: по одному на человека в день
            try:
                await call(bot, datetime.now(TZ))
            except Exception:
                logging.exception("%s failed", call.__name__)
        now = datetime.now(TZ)
        at, _ = next_reminder(now)
        await asyncio.sleep(max(1.0, min((at - now).total_seconds(), CATCHUP_EVERY)))


# ---------- результаты после матча (ADR-008, ADR-019) ----------

def load_announced() -> set[str] | None:
    """None — файла ещё нет: первый запуск."""
    try:
        return set(json.loads(ANNOUNCED_FILE.read_text()))
    except FileNotFoundError:
        return None
    except ValueError:   # файл испорчен: считаем за первый запуск — лучше промолчать, чем разослать всё заново
        logging.warning("%s не читается: считаю за первый запуск", ANNOUNCED_FILE.name)
        return None


def save_announced(ids: set[str]) -> None:
    write_atomic(ANNOUNCED_FILE, sorted(ids))


def played_games(data: dict, team: str | None = None) -> list[dict]:
    """Сыгранные матчи (есть протокол): все или одной команды."""
    return [g for g in data.get("games", []) if g.get("score") and (team is None or team in (g["home"], g["away"]))]


def fresh_results(data: dict, announced: set[str], today: date, teams: set[str] | None = None) -> list[dict]:
    """Сыгранные матчи (команд teams или всей лиги), о которых ещё не писали, не старше двух дней."""
    since = today - timedelta(days=RESULTS_FRESH_DAYS)
    return sorted((g for g in played_games(data) if g["id"] not in announced and match_key(g) not in announced
                   and (teams is None or g["home"] in teams or g["away"] in teams)
                   and date.fromisoformat(g["date"]) >= since), key=lambda g: g["date"])


LIVE_ENDED: dict[str, tuple[datetime, tuple]] = {}   # ключ → когда впервые увидели «окончен» и счёт


def live_games_near(now: datetime) -> list[dict]:
    """Матчи вчера и сегодня из live/<дата>.json: матч мог кончиться после полуночи."""
    out = []
    for d in (now.date() - timedelta(days=1), now.date()):
        out += [x for x in (read_live(f"{d.isoformat()}.json") or {}).get("games") or [] if isinstance(x, dict)]
    return out


async def results_step(bot: Bot, now: datetime) -> int:
    """Один проход: финалы по протоколу (league.json с Pages) и по онлайну (live/ с диска).
    Подписчикам — по их командам. Сначала отмечаем матч в announced, потом шлём: так протокол
    и онлайн не пришлют один матч дважды и перезапуск посреди рассылки не повторит её."""
    league = await published_league()
    ready = live_finals(live_games_near(now), LIVE_ENDED, now)
    announced = load_announced()
    if announced is None:   # первый запуск: сыгранное раньше не присылаем
        if league:
            save_announced({g["id"] for g in played_games(league)} | set(LIVE_ENDED))
        return 0
    if quiet(now):
        return 0
    names = {**TEAMS, **{t["id"]: t["name"] for t in (league or {}).get("teams", []) if "id" in t}}
    sent = failed = 0
    for g in pending_results(league, ready, announced, now.date()):
        announced |= {x for x in (g.get("id"), g["key"]) if x}
        save_announced(announced)
        to = recipients(SUBS, g)
        if not to:
            continue
        recap = {} if g["live"] else (await fetch_once(f"matches/{g['id']}.json") or {})
        kb = recap_kb(g["id"], not g["live"]) if g.get("id") else app_kb()
        before = (sent, failed)
        for cid, team in to:
            try:
                await say(bot, cid, lambda team=team: (result_text(g, names, recap.get("story", ""), team, g["live"],
                                                                   g.get("src"), g.get("protocol")), kb))
                sent += 1
            except TelegramForbiddenError:
                unsubscribe(cid, blocked=True)
                failed += 1
            except Exception:
                logging.exception("result to %s failed", cid)
                failed += 1
            await asyncio.sleep(0.05)
        TRACK.note({"kind": "final", "match": f"{names.get(g['home'], g['home'])} — {names.get(g['away'], g['away'])}",
                    "sent": sent - before[0], "failed": failed - before[1]})
    if sent or failed:
        TRACK.add("final_sent", sent)
        TRACK.add("final_fail", failed)
        TRACK.flush()
    return sent


# ---------- гол по ходу матча (ADR-024) ----------

def goal_key(m: dict, ev: dict) -> str:
    """Ключ события: матч, когда служба его заметила, кто забил и счёт. Номера события в списке в
    ключе нет: список обрезается по длине, и номер бы сполз."""
    score = ev.get("score") if isinstance(ev.get("score"), str) else "?"
    return f"{m.get('key') or match_key(m)}|goal|{ev.get('at')}|{ev.get('team')}|{score}"


def fresh_goals(games: list[dict], announced: set[str], now: datetime) -> list[tuple[dict, dict, str]]:
    """Голы, о которых стоит написать: (матч, событие, ключ). Правила — ADR-024, раздел 3.

    Старше GOAL_FRESH — молча мимо: болельщику нужен счёт, а не лента за полчаса. Событий без счёта
    (сайт заметил голы обеих сторон между опросами) от одного матча берём одно: несколько
    одинаковых сообщений хуже, чем одно с текущим счётом."""
    out = []
    for m in games:
        if not isinstance(m, dict) or not isinstance(m.get("home"), str):
            continue
        mine, blind = [], None
        for ev in m.get("events") or []:
            if not isinstance(ev, dict) or ev.get("kind") != "goal":
                continue
            at = admin.parse_iso(ev.get("at"))
            key = goal_key(m, ev)
            if at is None or now - at > GOAL_FRESH or now < at or key in announced:
                continue
            if isinstance(ev.get("score"), str):
                mine.append((m, ev, key))
            else:
                blind = (m, {**ev, "score": None}, key)
        if blind:
            mine.append(blind)
        out += mine[:GOAL_BURST]
    return out


PERIOD_WORDS = {"1": "Первый период", "2": "Второй период", "3": "Третий период",
                "ОТ": "Овертайм", "Б": "Буллиты"}


def goal_text(m: dict, ev: dict, team: str | None) -> str:
    """Гол: кто забил, какой счёт, период и минута с автором — если они известны."""
    score = ev.get("score") if isinstance(ev.get("score"), str) else _score_line(m)
    head = f"{e('goal')} <b>Гол!</b> {teams_html(m, team)}"
    if score:
        head += f" <b>{html.escape(score)}</b>"
    where = [x for x in (PERIOD_WORDS.get(str(ev.get("period"))),
                         f"{ev['minute']}-я минута" if isinstance(ev.get("minute"), int) else None) if x]
    who = str(ev.get("text") or "").strip()
    line = ", ".join(where) + (f" · {html.escape(who)}" if who else "") if where else (html.escape(who) if who else "")
    return f"{head}\n{line}" if line else head


def _score_line(m: dict) -> str | None:
    sc = _score(m.get("score"))
    return f"{sc['home']}:{sc['away']}" if sc else None


async def goals_step(bot: Bot, now: datetime) -> int:
    """Один проход: новые голы из live/ подписчикам их команд. Сначала отмечаем событие, потом
    шлём — перезапуск посреди рассылки не повторит гол тем, кто его получил."""
    if quiet(now):
        return 0
    games = live_games_near(now)
    day = now.astimezone(TZ).date()
    rec = ledger(day, "goals")
    announced = set(rec["sent"])
    sent = 0
    for m, ev, key in fresh_goals(games, announced, now):
        remember(rec, key, now)
        kb = match_kb(m)
        for cid, team in recipients(SUBS, m):
            if not goals_on(cid):
                continue
            try:
                await say(bot, cid, lambda m=m, ev=ev, team=team: (goal_text(m, ev, team), kb))
                sent += 1
            except TelegramForbiddenError:
                unsubscribe(cid, blocked=True)
            except Exception:
                logging.exception("goal to %s failed", cid)
            await asyncio.sleep(0.05)
    if sent:
        TRACK.add("goal_sent", sent)
        TRACK.flush()
    return sent


# ---------- «Мой игрок» (ADR-010, решение 3; ADR-030, раздел 6) ----------
# Звёздочку ставят на странице игрока в мини-аппе, сервер API пишет связь «Telegram id → ключ игрока» в state.db
# (myplayer.py). После матча с протоколом бот присылает отметившему гол этого игрока — клипом, когда служба clips его
# выложила, а нет клипа через MY_PLAYER_WAIT после начала матча — текстом. Только голы (не передачи), только факты
# протокола, только матчи с отметки и не старше RESULTS_FRESH_DAYS. Кому что ушло — журнал дня матча (reminded.json).

_my_players: myplayer.MyPlayerStore | None = None
CLIP_FILE_IDS: dict[str, str] = {}   # адрес клипа → file_id в Telegram: качает он его один раз


def my_players() -> myplayer.MyPlayerStore | None:
    """Связи «болельщик → игрок» из state.db. Базы нет — None: сервер API ещё не запускался."""
    global _my_players
    if _my_players is None:
        if not STATE_DB.exists():
            return None
        conn = sqlite3.connect(STATE_DB, isolation_level=None, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout=5000")
        _my_players = myplayer.MyPlayerStore(conn)
    return _my_players


def my_player_forget(chat_id: int) -> None:
    try:
        store = my_players()
        if store is not None:
            store.forget(chat_id)
    except sqlite3.Error:   # база занята или испорчена — подписку всё равно выключаем
        logging.exception("my player forget failed")


def my_goals(league: dict | None, stars: list[tuple[int, str, str]], now: datetime) -> list[tuple[int, dict, dict]]:
    """Что пора прислать: (болельщик, матч, гол). Гол автора с ключом `pk`, отмеченного звёздочкой, в матче с
    протоколом не раньше дня отметки и не старше RESULTS_FRESH_DAYS. С клипом — сразу, без клипа — через
    MY_PLAYER_WAIT после начала матча: клип обычно режется за пару часов после записи лиги."""
    by_pk: dict[str, list[tuple[int, str]]] = {}
    for fan, pk, at in stars:
        by_pk.setdefault(pk, []).append((fan, str(at)[:10]))
    since = (now.date() - timedelta(days=RESULTS_FRESH_DAYS)).isoformat()
    out = []
    for g in games_of(league):
        if not (g.get("score") and g.get("id") and str(g.get("date") or "") >= since):
            continue
        start = start_of(g) or datetime.combine(date.fromisoformat(g["date"]), time(12, 0), TZ)
        for x in g.get("goals") or []:
            if not isinstance(x, dict) or x.get("period") == "РБ" or x.get("pk") not in by_pk:
                continue
            if not clip_of(x) and now < start + MY_PLAYER_WAIT:
                continue
            out += [(fan, g, x) for fan, day in by_pk[x["pk"]] if g["date"] >= day]
    return out


def clip_of(x: dict) -> dict | None:
    """Точный клип гола 30 с. Окно повтора (ADR-037, `kind: window`) не берём: это две минуты записи в 480p, их
    отметившему не шлют — он получит текст с «Повтором», а клип придёт, когда у гола появится второй свидетель."""
    c = x.get("clip")
    return c if isinstance(c, dict) and _url(c.get("mp4")) and c.get("kind") != "window" else None


def my_goal_text(g: dict, x: dict) -> str:
    """«⭐ Иванов Иван забил!», матч и счёт после гола, период и время, передачи — только из протокола."""
    side = x.get("team") if x.get("team") in ("home", "away") else None
    home, away = (html.escape(tname(g[k])) for k in ("home", "away"))
    pair = f"{home} — <b>{away}</b>" if side == "home" else f"<b>{home}</b> — {away}" if side else f"{home} — {away}"
    where = ", ".join(v for v in (PERIODS.get(str(x.get("period"))), str(x.get("time") or "")) if v)
    lines = [f"⭐ <b>{html.escape(str(x.get('author') or ''))}</b> забил!",
             f"{pair} <b>{html.escape(str(x.get('score') or ''))}</b>" + (f" · {html.escape(where)}" if where else "")]
    assists = [a for a in x.get("assists") or [] if isinstance(a, str) and a != "Игрок скрыт"]
    if assists:
        lines.append(f"Передачи: {html.escape(', '.join(assists))}")
    lines.append("\n<i>Ты отметил его звёздочкой в приложении. Не присылать — сними её там же.</i>")
    return "\n".join(lines)


def my_goal_kb(g: dict, x: dict) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"🏒 {B_GOAL}", web_app=WebAppInfo(url=app_url(match=g["id"])))],
            [InlineKeyboardButton(text=f"⭐ {B_PLAYER}", web_app=WebAppInfo(url=app_url(startapp=f"p-{x['pk']}")))]]
    if not clip_of(x) and (url := _url(x.get("replay"))):   # клипа нет — повтор записью лиги в VK с секунды
        rows.insert(1, [InlineKeyboardButton(text="▶️ Повтор", url=url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_my_goal(bot: Bot, cid: int, g: dict, x: dict) -> None:
    """Гол клипом — видео по адресу из хранилища (Telegram качает его сам, дальше — file_id), не вышло — текстом."""
    text, kb = my_goal_text(g, x), my_goal_kb(g, x)
    c = clip_of(x)
    if c:
        try:
            msg = await sending(lambda: bot.send_video(
                cid, CLIP_FILE_IDS.get(c["mp4"]) or c["mp4"], caption=text, reply_markup=kb, supports_streaming=True,
                duration=int(c["dur"]) if isinstance(c.get("dur"), (int, float)) else None, width=1280, height=720))
            if msg.video:
                CLIP_FILE_IDS[c["mp4"]] = msg.video.file_id
            return
        except TelegramBadRequest:   # Telegram не скачал клип — гол всё равно сообщаем
            logging.exception("my player clip by url failed")
    await say(bot, cid, lambda: (text, kb))


async def my_player_step(bot: Bot, now: datetime) -> int:
    """Один проход: голы «Моих игроков» отметившим. Сначала отмечаем в журнале дня матча, потом шлём: перезапуск
    посреди рассылки гол не повторит. Telegram не доставит (заблокировали, не начинали диалог) — связь стираем."""
    store = my_players()
    if store is None or quiet(now):
        return 0
    try:
        stars = store.all()
    except sqlite3.Error:   # таблицы ещё нет: сервер API старой версии
        return 0
    if not stars:
        return 0
    sent = 0
    for fan, g, x in my_goals(await published_league(), stars, now):
        rec = ledger(date.fromisoformat(g["date"]), "player")
        mark = f"{fan}|{g['id']}|{x.get('score')}"
        if mark in rec["sent"]:
            continue
        remember(rec, mark, now)
        try:
            await send_my_goal(bot, fan, g, x)
            sent += 1
        except TelegramForbiddenError:
            unsubscribe(fan, blocked=True)
        except Exception as err:
            if cant_reach(err):
                my_player_forget(fan)
            else:
                logging.exception("my player goal to %s failed", fan)
        await asyncio.sleep(0.05)
    if sent:
        TRACK.add("my_goal_sent", sent)
        TRACK.flush()
    return sent


async def results_loop(bot: Bot):
    """Бот сам не качает протоколы: их собирает GitHub Actions и публикует вместе с мини-аппом.
    Живое (ADR-019) — файлы службы live на этом же сервере."""
    while True:
        now = datetime.now(TZ)
        try:
            await goals_step(bot, now)   # голы по ходу матча (ADR-024) — из тех же файлов live/
        except Exception:
            logging.exception("goals step failed")
        try:
            await results_step(bot, now)
        except Exception:
            logging.exception("results step failed")
        try:
            await my_player_step(bot, now)   # голы «Моих игроков» — после финалов: тот же league.json
        except Exception:
            logging.exception("my player step failed")
        await asyncio.sleep(RESULTS_POLL)


async def load_custom_emoji(bot: Bot) -> None:
    try:
        me = await bot.get_me()
        st = await bot.get_sticker_set(f"rhl_u21_by_{me.username}")
    except TelegramBadRequest:   # набор не опубликован — пишем обычными эмодзи
        logging.warning("custom emoji set not found, plain emoji")
        return
    CUSTOM.update(custom_ids(st.stickers))
    logging.info("custom emoji: %d", len(CUSTOM))


# ---------- тревоги админу (ADR-022) ----------

ALERT_HEADS = {"broke": "🔴 <b>Сломалось</b>", "still": "🔴 <b>Не починилось</b>",
               "watch": "🟡 <b>Посмотреть</b>", "fixed": "✅ <b>Починилось</b>"}

NO_API_PROBLEM = {"level": "bad", "key": "alerts",
                  "text": "Пульт молчит: служба api не пишет тревоги (status/alerts.json). "
                          "Проверь systemctl status api"}


def load_alerted() -> dict:
    was = admin.read_json(ALERTED_FILE, {})
    return was if isinstance(was, dict) else {}


ALERTED = load_alerted()


def alert_text(groups: dict[str, list[str]]) -> str:
    """Одно сообщение на проверку: разделами, по строке на причину. В текстах проблем бывают и
    ошибки со сторонних сайтов, поэтому каждая строка — через html.escape."""
    parts = [ALERT_HEADS[g] + "\n" + "\n".join(f"• {html.escape(t)}" for t in groups[g])
             for g in admin.ALERT_GROUPS if groups.get(g)]
    return "\n\n".join(parts)


def alerts_now(now: datetime) -> list[dict]:
    """Список проблем от службы api. Файла нет или он старый — молчит сам api, и это тревога."""
    data = admin.read_json(ALERTS_FILE, {})
    found = data.get("problems") if isinstance(data, dict) else None
    at = admin.parse_iso(data.get("at")) if isinstance(data, dict) else None
    if not isinstance(found, list) or at is None or now - at > admin.ALERTS_STALE:
        return [NO_API_PROBLEM]
    return [p for p in found if isinstance(p, dict)]


async def alerts_step(bot: Bot, now: datetime) -> int:
    """Сказать админам, что сломалось и что починилось. Ночью молчим: тревога разбудит без дела."""
    if not ADMIN_IDS or quiet(now):
        return 0
    groups, state = admin.alert_plan(alerts_now(now), ALERTED, now)
    if not groups:
        return 0
    text = alert_text(groups)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть пульт",
                                                                     web_app=WebAppInfo(url=admin_url()))]])
    sent = 0
    for cid in sorted(ADMIN_IDS):
        try:
            await say(bot, cid, lambda: (text, kb))
            sent += 1
        except Exception:
            logging.exception("alert to admin failed")
        await asyncio.sleep(0.05)
    if sent:   # не дошло ни до кого — память не трогаем, скажем на следующем проходе
        ALERTED.clear()
        ALERTED.update(state)
        write_atomic(ALERTED_FILE, ALERTED)
        TRACK.add("alerts_sent", sent)
    return sent


async def status_loop(bot: Bot):
    """Пульс для пульта (ADR-021): раз в минуту getMe через туннель и запись status/bot.json.
    Тем же проходом — тревоги админам (ADR-022), напоминание о неразмеченных повторах (ADR-028), превью голов и кадры
    табло клубов без разметки (ADR-030)."""
    while True:
        now = datetime.now(TZ)
        try:
            await asyncio.wait_for(bot.get_me(), 15)
            TRACK.info(tg_ok=admin.iso(now))
        except Exception as err:   # туннель лёг, Telegram не ответил — это и показываем
            TRACK.info(tg_fail=admin.iso(now), tg_error=admin.no_ids(f"{type(err).__name__}: {err}")[:200])
        TRACK.gauge("subs", len(SUBS))
        TRACK.flush()
        try:
            await alerts_step(bot, now)
        except Exception:
            logging.exception("alerts step failed")
        try:
            await replay_nag_step(bot, now)
        except Exception:
            logging.exception("replay nag step failed")
        try:
            await preview_step(bot, now)
        except Exception:
            logging.exception("preview step failed")
        try:
            await grid_step(bot, now)
        except Exception:
            logging.exception("grid step failed")
        try:
            await dispute_step(bot, now)
        except Exception:
            logging.exception("dispute step failed")
        try:
            await marks_sync_step(now)   # отметки из пульта → replays.json (ADR-036, раздел 5)
        except Exception:
            logging.exception("marks sync step failed")
        await asyncio.sleep(STATUS_EVERY)


async def main():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger().addHandler(admin.ErrorCount(TRACK))
    # С VPS в России api.telegram.org закрыт: ходим через туннель deploy/tunnel.sh (socks5://127.0.0.1:1080)
    proxy = os.environ.get("TELEGRAM_PROXY")
    # без превью ссылок: в «Матчах сегодня» и напоминаниях ссылки на трансляции — не карточки сайтов
    bot = Bot(os.environ["BOT_TOKEN"], session=AiohttpSession(proxy=proxy) if proxy else None,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    await load_custom_emoji(bot)
    await bot.delete_my_commands()   # меню команд пустое: всё — в мини-аппе (ADR-005)
    try:   # описание не критично: без него бот работает
        await bot.set_my_description(DESCRIPTION)
        await bot.set_my_short_description(SHORT_DESCRIPTION)
    except TelegramBadRequest:
        logging.exception("set description failed")
    await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="РХЛ", web_app=WebAppInfo(url=WEBAPP_URL)))
    asyncio.create_task(reminder_loop(bot))
    asyncio.create_task(results_loop(bot))
    asyncio.create_task(status_loop(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

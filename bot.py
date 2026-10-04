"""Бот РХЛ U21 2026/27 (@rhl_u21_bot, aiogram 3): встречает и ведёт в мини-апп, напоминает о матчах.

Весь интерфейс — в мини-аппе (ADR-003). У бота нет своей клавиатуры и меню команд: на всё он
отвечает стикером и кнопкой «Открыть РХЛ» (ADR-005). Вторым планом — матчи дня (`/today`) и
напоминания о любой команде лиги (`/team`, `/remind`) — ADR-019, раздел 8.
Для админов — счётчики и пульс в status/bot.json и `/admin` с кнопкой пульта (ADR-021).
"""
import asyncio
import html
import json
import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import aiohttp
import admin
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
                           MenuButtonWebApp, Message, ReplyKeyboardRemove, WebAppInfo)

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
TRACK = admin.Tracker("bot")       # счётчики за день для пульта: без id и имён
QUIET_FROM, QUIET_TO = time(23, 0), time(9, 0)   # ночью молчим, результат уйдёт утром

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
B_LEADERS = "Все лидеры"
B_RASKAT = "Собрать раскат"
B_WAIT_ON = "Позвать, когда откроется"
B_WAIT_OFF = "Больше не звать"
B_TODAY = "Матчи сегодня"
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
    С view=leaders — сразу «Таблица → Игроки» (ADR-009).
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
    """Одна кнопка — карточка сыгранного матча в мини-аппе. Протокола ещё нет — «Матч в приложении»:
    разбора «Как это было» там пока нет, только счёт."""
    label = B_RECAP if recap else B_MATCH
    return InlineKeyboardMarkup(inline_keyboard=[[btn(label, "goal", web_app=WebAppInfo(url=app_url(match=match)))]])


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
    """blocked — бота заблокировали: на пульте отдельно от «Выключить»."""
    if blocked and chat_id in SUBS:
        TRACK.add("blocked")
    set_teams(chat_id, [])


def turn_on(chat_id: int, team: str = REMIND_TEAM_ID) -> str:
    return follow(chat_id, team)


def remind_kb(chat_id: int) -> InlineKeyboardMarkup:
    """Включены — «Выключить» и «Команды»; выключены — сразу выбор конференции."""
    if not SUBS.get(chat_id):
        return team_kb(chat_id)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔕 Выключить", callback_data="r:toggle"),
        InlineKeyboardButton(text="✏️ Команды", callback_data="t:home")]])


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
    text = (f"{head}{e('bell')} Напоминания о матчах {quoted(teams)} ✅ включены\n\n"
            f"Пришлю сообщение {when}, а после игры — счёт.")
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
    if team in (home, away):
        opp = away if team == home else home
        teams_line = f"{html.escape(tname(team))} — <b>{html.escape(tname(opp))}</b>"
        where = (f"{e('home')} Дома" if team == home else f"{e('away')} На выезде") + at
    else:
        teams_line = f"<b>{html.escape(tname(home))}</b> — <b>{html.escape(tname(away))}</b>"
        where = f"📍 {html.escape(city)}" if city else ""
    lines = [f"{e('bell')} <b>{head}</b>", "", teams_line, f"{DOW[d.weekday()]} {d:%d.%m} · {when}"]
    lines += [where] if where else []
    if m.get("watch"):
        lines.append(f'📺 <a href="{html.escape(m["watch"][0]["url"])}">Смотреть трансляцию</a>')
    if warm := warmup_text(m, team, games):
        lines += ["", warm]
    return "\n".join(lines)


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
# Игра живёт в мини-аппе; бот только ведёт в неё и зовёт, когда включат зачёт (контракт, раздел 6).
# Ежедневных напоминаний про раскат нет: бот не знает, кто уже собрал.

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

def admin_url() -> str:
    p = urlsplit(WEBAPP_URL)
    path = p.path if p.path.endswith("/") else p.path + "/"
    return urlunsplit((p.scheme, p.netloc, path + "admin.html", "", ""))


def admin_reply(chat_id: int, user_id: int | None) -> tuple[str, InlineKeyboardMarkup | None]:
    """Админу — кнопка пульта. Остальным — их id и куда его вписать: так владелец узнаёт свой."""
    if user_id in ADMIN_IDS:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть пульт",
                                                                         web_app=WebAppInfo(url=admin_url()))]])
        return "Пульт: службы, сборки, аудитория, рассылки и игры. Данные обновляются раз в минуту.", kb
    return (f"Пульт — только для админов приложения. Твой Telegram id: <code>{user_id or chat_id}</code>.\n"
            "Его вписывают в ADMIN_IDS в /etc/rhl/bot.env на сервере.", None)


@dp.message(Command("admin"))   # в меню команд её нет; доступ к данным проверяет ещё и сервер API
async def h_admin(m: Message):
    if m.chat.type != "private":
        return
    text, kb = admin_reply(m.chat.id, m.from_user.id if m.from_user else None)
    await m.answer(text, reply_markup=kb)


TODAY_WORDS = re.compile(r"сегодн|матч|игр[аыуе]?\b|расписан|когда|сч[её]т|трансляц", re.I)


@dp.message()   # последним: всё остальное (ADR-005 — бот не молчит)
async def h_lost(m: Message):
    TRACK.add("lost")
    if m.text and TODAY_WORDS.search(m.text):   # «когда игра?», «какой счёт» — матчи дня
        await send_today(m.bot, m.chat.id)
        return
    await send_sticker(m.bot, m.chat.id, "tap", reply_markup=ReplyKeyboardRemove())
    await say(m.bot, m.chat.id, lambda: (lost_text(), app_kb(today=True)))

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
            if rec is not None:   # после каждого: перезапуск посреди рассылки её не повторит
                rec["sent"].append(mark)
                save_reminded(REMINDED, now.date())
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


async def results_loop(bot: Bot):
    """Бот сам не качает протоколы: их собирает GitHub Actions и публикует вместе с мини-аппом.
    Живое (ADR-019) — файлы службы live на этом же сервере."""
    while True:
        try:
            await results_step(bot, datetime.now(TZ))
        except Exception:
            logging.exception("results step failed")
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


async def status_loop(bot: Bot):
    """Пульс для пульта (ADR-021): раз в минуту getMe через туннель и запись status/bot.json."""
    while True:
        now = datetime.now(TZ)
        try:
            await asyncio.wait_for(bot.get_me(), 15)
            TRACK.info(tg_ok=admin.iso(now))
        except Exception as err:   # туннель лёг, Telegram не ответил — это и показываем
            TRACK.info(tg_fail=admin.iso(now), tg_error=admin.no_ids(f"{type(err).__name__}: {err}")[:200])
        TRACK.gauge("subs", len(SUBS))
        TRACK.flush()
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

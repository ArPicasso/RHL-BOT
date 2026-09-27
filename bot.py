"""Бот РХЛ U21 2026/27 (@rhl_u21_bot, aiogram 3): встречает и ведёт в мини-апп, напоминает о матчах,
зовёт лист ожидания, когда откроется фэнтези «Звено» (ADR-014).

Весь интерфейс — в мини-аппе (ADR-003). У бота нет своей клавиатуры и меню команд: на всё он
отвечает стикером и одной кнопкой «Открыть РХЛ» (ADR-005).
"""
import asyncio
import html
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
                           MenuButtonWebApp, Message, ReplyKeyboardRemove, WebAppInfo)

BASE = Path(__file__).parent
TZ = ZoneInfo("Europe/Moscow")
SUBS_FILE = BASE / "subscribers.json"
ANNOUNCED_FILE = BASE / "announced.json"   # матчи, о которых уже написали после игры (ADR-008)
ZVENO_FILE = BASE / "zveno_waitlist.json"  # кого позвать, когда «Звено» откроется (ADR-014)
STICKERS = BASE / "stickers"          # стикеры бота (ADR-005), 512×512 WEBP
# мини-апп (ADR-003); переменная окружения — только чтобы подставить тестовый адрес
WEBAPP_URL = os.environ.get("WEBAPP_URL") or "https://arpicasso.github.io/bogdanov/"
REMIND_TODAY_AT = time(10, 0)      # утром в день игры
REMIND_TOMORROW_AT = time(19, 0)   # вечером накануне
REMIND_TEAM = "Рязань-ВДВ"         # напоминания пока только о её матчах: games.json
RESULTS_POLL = 600                 # раз в 10 минут смотрим опубликованные результаты мини-аппа
LEADERS_TTL = 600                  # лидеров лиги перечитываем не чаще раза в 10 минут (ADR-009)
ZVENO_POLL = ZVENO_TTL = 600       # туры «Звена» — так же: раз в 10 минут (ADR-014)
TOURS = "zveno/tours.json"         # туры и статус «Звена», публикует движок вместе с мини-аппом
RESULTS_FRESH_DAYS = 2             # матчи старше не присылаем
QUIET_FROM, QUIET_TO = time(23, 0), time(9, 0)   # ночью молчим, результат уйдёт утром

DOW = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


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
# id команды → название: для диплинка /start <id> (ADR-005)
TEAMS = {t["id"]: t["name"] for t in json.loads((BASE / "teams.json").read_text(encoding="utf-8"))}
REMIND_TEAM_ID = next(i for i, name in TEAMS.items() if name == REMIND_TEAM)

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
B_ZVENO = "Открыть «Звено»"
B_ZVENO_OFF = "🔕 Больше не звать"
B_ZVENO_ON = "🔔 Позвать, когда откроется"

# видно в пустом чате до «Старт» и в профиле бота (до 512 и 120 символов)
DESCRIPTION = ("Бот Первенства России U21 — РХЛ 2026/27.\n\n"
               "🏒 Календарь всех 26 команд, таблица и счёт матчей — в приложении\n"
               "🏆 Лучшие игроки лиги: бомбардиры, снайперы, вратари\n"
               "⭐ Фэнтези «Звено»: ворота и два звена из игроков лиги\n"
               "🔔 Напоминания перед играми\n\n"
               "Жми «Старт» 👇")
SHORT_DESCRIPTION = "РХЛ U21: календарь, таблица и счёт матчей. Напомню перед игрой 🏒"


def app_url(team: str | None = None, match: str | None = None, view: str | None = None,
            startapp: str | None = None) -> str:
    """Адрес мини-аппа; с командой — ?team=<id>, мини-апп выберет её, если своей ещё нет.
    С матчем — ?match=<id>, мини-апп сразу откроет его карточку (ADR-008).
    С view=leaders — сразу «Таблица → Игроки» (ADR-009).
    Со startapp=zveno — сразу вкладка «Звено» (ADR-014)."""
    extra = [(k, v) for k, v in (("team", team), ("match", match), ("view", view), ("startapp", startapp)) if v]
    if not extra:
        return WEBAPP_URL
    u = urlsplit(WEBAPP_URL)
    return urlunsplit(u._replace(query=urlencode(parse_qsl(u.query) + extra)))


def app_kb(team: str | None = None) -> InlineKeyboardMarkup:
    """Одна кнопка — открыть мини-апп."""
    web_app = WebAppInfo(url=app_url(team))
    if "puck" in CUSTOM:   # значок на кнопке — наша шайба
        btn = InlineKeyboardButton(text=B_APP, icon_custom_emoji_id=CUSTOM["puck"], web_app=web_app)
    else:
        btn = InlineKeyboardButton(text=f"{EMOJI['puck']} {B_APP}", web_app=web_app)
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])


def recap_kb(match: str) -> InlineKeyboardMarkup:
    """Одна кнопка — карточка сыгранного матча в мини-аппе."""
    web_app = WebAppInfo(url=app_url(match=match))
    if "goal" in CUSTOM:
        btn = InlineKeyboardButton(text=B_RECAP, icon_custom_emoji_id=CUSTOM["goal"], web_app=web_app)
    else:
        btn = InlineKeyboardButton(text=f"{EMOJI['goal']} {B_RECAP}", web_app=web_app)
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])


def leaders_kb() -> InlineKeyboardMarkup:
    """Одна кнопка — лидеры лиги в мини-аппе."""
    web_app = WebAppInfo(url=app_url(view="leaders"))
    if "cup" in CUSTOM:
        btn = InlineKeyboardButton(text=B_LEADERS, icon_custom_emoji_id=CUSTOM["cup"], web_app=web_app)
    else:
        btn = InlineKeyboardButton(text=f"{EMOJI['cup']} {B_LEADERS}", web_app=web_app)
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])


def zveno_kb() -> InlineKeyboardMarkup:
    """Одна кнопка — вкладка «Звено» в мини-аппе."""
    web_app = WebAppInfo(url=app_url(startapp="zveno"))
    if "star" in CUSTOM:
        btn = InlineKeyboardButton(text=B_ZVENO, icon_custom_emoji_id=CUSTOM["star"], web_app=web_app)
    else:
        btn = InlineKeyboardButton(text=f"{EMOJI['star']} {B_ZVENO}", web_app=web_app)
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])


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
            f"{e('star')} Календарь 26 команд\n"
            f"{e('cup')} Таблица конференций\n"
            f"{e('goal')} Счёт и голы матчей\n"
            f"{e('fire')} Лучшие игроки лиги\n"
            f"{e('stick')} Фэнтези «Звено» из игроков лиги\n\n"
            "<b>Жми «Открыть РХЛ»</b> 👇 и выбери, за кого болеешь.")


def lost_text() -> str:
    return f"Всё самое интересное — в приложении {e('fire')}\nЖми кнопку 👇"

# ---------- состояние в файлах ----------

def write_json(path: Path, data) -> None:
    """Атомарно: пишем рядом во временный файл и подменяем им старый. Оборванная запись
    (рестарт, кончилось место) не оставит полфайла, и подписчики не потеряются."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, path)

# ---------- напоминания: подписчики ----------

def load_subs() -> set[int]:
    try:
        return set(json.loads(SUBS_FILE.read_text()))
    except (FileNotFoundError, ValueError):
        return set()


def save_subs(subs: set[int]) -> None:
    write_json(SUBS_FILE, sorted(subs))


SUBS = load_subs()


def turn_on(chat_id: int) -> None:
    if chat_id not in SUBS:
        SUBS.add(chat_id)
        save_subs(SUBS)


def remind_kb(chat_id: int) -> InlineKeyboardMarkup:
    on = chat_id in SUBS
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="🔕 Выключить" if on else "🔔 Включить", callback_data="r:toggle")]])


def next_game(today: date) -> Game | None:
    return next((g for g in GAMES if g.d >= today), None)


def remind_text(chat_id: int, today: date | None = None) -> str:
    """Состояние напоминаний. Включены — и ближайшая игра, чтобы было видно, о чём напомню."""
    on = chat_id in SUBS
    text = (f"{e('bell')} Напоминания о матчах «{REMIND_TEAM}» {'✅ включены' if on else '❌ выключены'}\n\n"
            f"Пришлю сообщение накануне игры в {REMIND_TOMORROW_AT:%H:%M} "
            f"и в день игры в {REMIND_TODAY_AT:%H:%M} (МСК).")
    g = next_game(today or datetime.now(TZ).date()) if on else None
    if g:
        where = "дома" if g.home else "в гостях"
        text += f"\n\nБлижайшая: {DOW[g.d.weekday()]} {g.d:%d.%m}, {where} с «{html.escape(g.opponent)}»"
    return text


def reminder_text(g: Game, kind: str) -> str:
    head = "Сегодня игра!" if kind == "today" else "Завтра игра!"
    where = f"{e('home')} Дома" if g.home else f"{e('away')} На выезде"
    return (f"{e('bell')} <b>{head}</b>\n\n{DOW[g.d.weekday()]} {g.d:%d.%m} · {REMIND_TEAM} — "
            f"<b>{html.escape(g.opponent)}</b>\n{where}")

def result_text(g: dict, names: dict[str, str], story: str = "") -> str:
    """Сообщение после матча: счёт, исход для нашей команды, фраза-сюжет из разбора (ADR-008)."""
    sc = g["score"]
    mine, theirs = ((sc["home"], sc["away"]) if g["home"] == REMIND_TEAM_ID else (sc["away"], sc["home"]))
    how = {"ОТ": " в овертайме", "Б": " по буллитам"}.get(sc["decision"], "")
    head = f"{e('win')} Победа{how}!" if mine > theirs else f"{e('loss')} Поражение{how}"
    dec = f" ({sc['decision']})" if sc["decision"] else ""
    home, away = (html.escape(names.get(g[k], g[k])) for k in ("home", "away"))
    text = f"<b>{head}</b>\n\n{home} <b>{sc['home']}:{sc['away']}</b>{dec} {away}"
    if story:
        text += f"\n\n{html.escape(story)}"
    return text + "\n\nГолы, ход матча и составы — по кнопке 👇"

# ---------- «Звено»: лист ожидания Пролога (ADR-014) ----------
# До открытия рынка сервера у «Звена» нет: кого позвать, помнит бот. В файле — только chat_id и
# сезон, о котором уже позвали. Удалиться — кнопкой «Больше не звать»; после открытия лист пустеет.

def load_zveno() -> dict:
    try:
        d = json.loads(ZVENO_FILE.read_text())
        return {"chats": set(d.get("chats", [])), "opened": d.get("opened")}
    except (FileNotFoundError, ValueError, AttributeError, TypeError):
        return {"chats": set(), "opened": None}


def save_zveno() -> None:
    write_json(ZVENO_FILE, {"chats": sorted(ZVENO["chats"]), "opened": ZVENO["opened"]})


ZVENO = load_zveno()


def zveno_set(chat_id: int, on: bool) -> None:
    chats = ZVENO["chats"]
    if (chat_id in chats) == on:
        return
    if on:
        chats.add(chat_id)
    else:
        chats.discard(chat_id)
    save_zveno()


def zveno_open(tours: dict | None) -> bool:
    """«Звено» открыто: так говорит опубликованный tours.json или мы в этом сезоне уже звали —
    иначе в лист можно записаться, а позвать будет некому. Без tours.json верим флагу."""
    if tours:
        return tours.get("status") == "open" or ZVENO["opened"] == zveno_season(tours)
    return ZVENO["opened"] is not None


def zveno_opening(tours: dict | None) -> bool:
    """Рынок открыт, а лист ещё не звали — зовём один раз за сезон."""
    return bool(tours) and tours.get("status") == "open" and ZVENO["opened"] != zveno_season(tours)


def zveno_season(tours: dict) -> str:
    return tours.get("season") or "?"


def tour_deadline(tours: dict | None, now: datetime) -> tuple[int, datetime] | None:
    """Тур, на который сейчас собирают состав, и его дедлайн, если он ещё впереди."""
    t = (tours or {}).get("tour_next")
    x = next((x for x in (tours or {}).get("tours") or [] if x.get("t") == t), None)
    if not x or not x.get("deadline"):
        return None
    try:
        d = datetime.fromisoformat(x["deadline"])
    except (TypeError, ValueError):
        return None
    d = (d if d.tzinfo else d.replace(tzinfo=TZ)).astimezone(TZ)
    return (t, d) if d > now else None


ZVENO_HEAD = "<b>«Звено» — фэнтези РХЛ</b>"
ZVENO_ABOUT = "Собираешь ворота и два звена из наклеек игроков лиги. Очки — только по протоколам матчей."


def zveno_wait_text(chat_id: int) -> str:
    """Пролог: что такое «Звено» и позовём ли, когда откроется."""
    if chat_id in ZVENO["chats"]:
        tail = f"{e('bell')} Ты в списке: позову, когда «Звено» откроется — после первых матчей всех 26 команд."
    else:
        tail = "🔕 Не позову. Передумаешь — жми «Позвать, когда откроется»."
    return f"{e('star')} {ZVENO_HEAD}\n{ZVENO_ABOUT}\n\n{tail}"


def zveno_wait_kb(chat_id: int) -> InlineKeyboardMarkup:
    """Вкладка «Звено» (в Прологе там правила и черновик звена) и выход из листа — или вход обратно."""
    on = chat_id in ZVENO["chats"]
    toggle = InlineKeyboardButton(text=B_ZVENO_OFF if on else B_ZVENO_ON, callback_data="z:off" if on else "z:on")
    return InlineKeyboardMarkup(inline_keyboard=zveno_kb().inline_keyboard + [[toggle]])


def zveno_open_text(tours: dict | None, now: datetime, called: bool = False) -> str:
    """«Звено» открылось. called — зовём лист ожидания, иначе отвечаем тому, кто пришёл сам."""
    head = f"{e('fire')} <b>«Звено» открылось!</b>" if called else f"{e('fire')} <b>«Звено» уже открыто!</b>"
    text = head + ("\nКак обещал — зову. " if called else "\n") + "Собирай ворота и два звена из наклеек игроков РХЛ."
    dl = tour_deadline(tours, now)
    if dl:
        t, d = dl
        text += f"\n\nСостав на тур {t} — до {DOW[d.weekday()]} {d:%d.%m}, {d:%H:%M} (МСК)."
    return text + "\n\nЖми кнопку 👇"

# ---------- стикеры ----------

_sticker_ids: dict[str, str] = {}   # имя → file_id: файл загружаем один раз


async def send_sticker(bot: Bot, chat_id: int, name: str, **kw) -> bool:
    """Стикер — украшение (ADR-005): ошибка не мешает сообщению, кроме блокировки бота."""
    try:
        msg = await bot.send_sticker(chat_id, _sticker_ids.get(name) or FSInputFile(STICKERS / f"{name}.webp"), **kw)
    except TelegramForbiddenError:
        raise
    except Exception:
        logging.exception("sticker %s failed", name)
        return False
    _sticker_ids[name] = msg.sticker.file_id
    return True

# ---------- хендлеры ----------

dp = Dispatcher()


async def say(bot: Bot, chat_id: int, make) -> None:
    """Отправить make() → (текст, клавиатура); если Telegram не принял свои эмодзи — обычными."""
    try:
        text, kb = make()
        await bot.send_message(chat_id, text, reply_markup=kb)
    except TelegramBadRequest as err:
        if not emoji_off(err):
            raise
        text, kb = make()
        await bot.send_message(chat_id, text, reply_markup=kb)


async def safe_edit(c: CallbackQuery, make) -> None:
    text, kb = make()
    try:
        await c.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as err:   # «message is not modified» — молча, свои эмодзи — повтор
        if "not modified" not in str(err) and emoji_off(err):
            await safe_edit(c, make)


@dp.message(CommandStart())
async def start(m: Message, command: CommandObject):
    cid = m.chat.id
    # «Напомнить» в мини-аппе: человек уже решил — включаем сразу и без приветствия (ADR-004, ADR-005)
    if command.args == "remind":
        turn_on(cid)
        await send_sticker(m.bot, cid, "bell", reply_markup=ReplyKeyboardRemove())
        await say(m.bot, cid, lambda: (remind_text(cid), remind_kb(cid)))
        return
    if command.args == "zveno":   # «Позвать, когда откроется» — тоже решение, без приветствия (ADR-014)
        await zveno_join(m.bot, cid)
        return
    # стикер заодно убирает клавиатуру, если она осталась от прошлой версии бота
    if not await send_sticker(m.bot, cid, "hello", reply_markup=ReplyKeyboardRemove()):
        await m.answer("🏒", reply_markup=ReplyKeyboardRemove())
    if command.args == "leaders":   # ссылка t.me/<бот>?start=leaders (ADR-009)
        await send_leaders(m)
        return
    team = command.args if command.args in TEAMS else None
    await say(m.bot, cid, lambda: (welcome_text(team), app_kb(team)))


# Опубликованные файлы мини-аппа меняются раз в час: держим последний 10 минут.
# Не скачался — отдаём прошлый. Имя файла → (когда скачали, данные)
_published: dict[str, tuple[datetime, dict]] = {}


async def published(name: str, ttl: int) -> dict | None:
    now = datetime.now(TZ)
    at, data = _published.get(name, (None, None))
    if at and now - at < timedelta(seconds=ttl):
        return data
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10), trust_env=True) as session:
        fresh = await fetch_json(session, name)
    if fresh:
        _published[name] = (now, fresh)
    return fresh or data


def last_published(name: str) -> dict | None:
    """Последний скачанный файл, без похода в сеть."""
    return _published.get(name, (None, None))[1]


async def published_leaders() -> dict | None:
    return await published("leaders.json", LEADERS_TTL)


async def send_leaders(m: Message) -> None:
    data = await published_leaders()
    await say(m.bot, m.chat.id, lambda: (leaders_text(data), leaders_kb()))


@dp.message(Command("leaders"))   # в меню команд её нет (ADR-005), только ссылкой или руками
async def h_leaders(m: Message):
    await send_leaders(m)


@dp.message(Command("remind"))
async def h_remind(m: Message):
    cid = m.chat.id
    await say(m.bot, cid, lambda: (remind_text(cid), remind_kb(cid)))


@dp.callback_query(F.data == "r:toggle")
async def cb_remind(c: CallbackQuery):
    cid = c.message.chat.id
    SUBS.symmetric_difference_update({cid})
    save_subs(SUBS)
    await safe_edit(c, lambda: (remind_text(cid), remind_kb(cid)))
    await c.answer("Готово")
    if cid in SUBS:
        await send_sticker(c.bot, cid, "bell")


async def zveno_join(bot: Bot, cid: int) -> None:
    """?start=zveno и /zveno: в лист ожидания, а если «Звено» уже открыто — сразу туда.
    Турами бот не ходит в сеть: их держит свежими zveno_loop."""
    tours = last_published(TOURS)
    if zveno_open(tours):
        zveno_set(cid, False)
        await say(bot, cid, lambda: (zveno_open_text(tours, datetime.now(TZ)), zveno_kb()))
        return
    zveno_set(cid, True)
    await send_sticker(bot, cid, "bell", reply_markup=ReplyKeyboardRemove())
    await say(bot, cid, lambda: (zveno_wait_text(cid), zveno_wait_kb(cid)))


@dp.message(Command("zveno"))   # в меню команд её нет (ADR-005), только ссылкой или руками
async def h_zveno(m: Message):
    await zveno_join(m.bot, m.chat.id)


@dp.callback_query(F.data.in_({"z:on", "z:off"}))
async def cb_zveno(c: CallbackQuery):
    """«Больше не звать» и «Позвать, когда откроется». Не переключатель: старое сообщение
    с той же кнопкой не вернёт в лист того, кто уже вышел."""
    cid = c.message.chat.id
    tours = last_published(TOURS)
    if zveno_open(tours):
        zveno_set(cid, False)
        await safe_edit(c, lambda: (zveno_open_text(tours, datetime.now(TZ)), zveno_kb()))
        await c.answer()
        return
    on = c.data == "z:on"
    zveno_set(cid, on)
    await safe_edit(c, lambda: (zveno_wait_text(cid), zveno_wait_kb(cid)))
    await c.answer("Готово")
    if on:
        await send_sticker(c.bot, cid, "bell")


@dp.message()   # последним: всё остальное (ADR-005 — бот не молчит)
async def h_lost(m: Message):
    await send_sticker(m.bot, m.chat.id, "tap", reply_markup=ReplyKeyboardRemove())
    await say(m.bot, m.chat.id, lambda: (lost_text(), app_kb()))

# ---------- напоминания ----------

def next_reminder(now: datetime) -> tuple[datetime, str]:
    slots = [(datetime.combine(now.date() + timedelta(days=d), t, TZ), kind)
             for d in (0, 1) for t, kind in ((REMIND_TODAY_AT, "today"), (REMIND_TOMORROW_AT, "tomorrow"))]
    return min(s for s in slots if s[0] > now)


async def reminder_loop(bot: Bot):
    while True:
        now = datetime.now(TZ)
        at, kind = next_reminder(now)
        await asyncio.sleep((at - now).total_seconds())
        day = at.date() + timedelta(days=1 if kind == "tomorrow" else 0)
        games = [g for g in GAMES if g.d == day]
        if not games:
            continue
        g = games[0]
        for cid in list(SUBS):
            try:
                if kind == "today":
                    await send_sticker(bot, cid, "gameday")
                await say(bot, cid, lambda: (reminder_text(g, kind), app_kb()))
            except TelegramForbiddenError:   # бота заблокировали
                SUBS.discard(cid)
                save_subs(SUBS)
            except Exception:
                logging.exception("send to %s failed", cid)
            await asyncio.sleep(0.05)


# ---------- результаты после матча (ADR-008) ----------

def load_announced() -> set[str] | None:
    """None — файла ещё нет: первый запуск."""
    try:
        return set(json.loads(ANNOUNCED_FILE.read_text()))
    except FileNotFoundError:
        return None
    except ValueError:
        return set()


def save_announced(ids: set[str]) -> None:
    write_json(ANNOUNCED_FILE, sorted(ids))


def played_games(data: dict, team: str = REMIND_TEAM_ID) -> list[dict]:
    return [g for g in data.get("games", []) if g.get("score") and team in (g["home"], g["away"])]


def fresh_results(data: dict, announced: set[str], today: date) -> list[dict]:
    """Сыгранные матчи нашей команды, о которых ещё не писали, не старше двух дней."""
    since = today - timedelta(days=RESULTS_FRESH_DAYS)
    return sorted((g for g in played_games(data) if g["id"] not in announced
                   and date.fromisoformat(g["date"]) >= since), key=lambda g: g["date"])


def quiet(now: datetime) -> bool:
    t = now.astimezone(TZ).time()
    return t >= QUIET_FROM or t < QUIET_TO


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


async def results_loop(bot: Bot):
    """Бот сам не качает протоколы: их собирает GitHub Actions и публикует вместе с мини-аппом."""
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        while True:
            data = await fetch_json(session, "league.json")
            announced = load_announced()
            if data and announced is None:   # первый запуск: сыгранное раньше не присылаем
                save_announced({g["id"] for g in played_games(data)})
            elif data and not quiet(datetime.now(TZ)):
                names = {t["id"]: t["name"] for t in data.get("teams", [])}
                for g in fresh_results(data, announced, datetime.now(TZ).date()):
                    recap = await fetch_json(session, f"matches/{g['id']}.json") or {}
                    for cid in list(SUBS):
                        try:
                            await say(bot, cid, lambda: (result_text(g, names, recap.get("story", "")), recap_kb(g["id"])))
                        except TelegramForbiddenError:
                            SUBS.discard(cid)
                            save_subs(SUBS)
                        except Exception:
                            logging.exception("result to %s failed", cid)
                        await asyncio.sleep(0.05)
                    announced.add(g["id"])
                    save_announced(announced)
            await asyncio.sleep(RESULTS_POLL)


# ---------- «Звено» открылось (ADR-014) ----------
# Бот зовёт только лист ожидания Пролога, один раз. Сообщения по ходу сезона шлёт сервер «Звена».

async def announce_zveno(bot: Bot, tours: dict | None, now: datetime) -> int:
    """Рынок открылся: одно сообщение каждому из листа, лист пустеет, сезон помечен «позвали».
    Ночью молчим, позовём утром. Возвращает, скольким ушло."""
    if not zveno_opening(tours) or quiet(now):
        return 0
    sent = 0
    for cid in sorted(ZVENO["chats"]):
        try:
            await say(bot, cid, lambda: (zveno_open_text(tours, now, called=True), zveno_kb()))
            sent += 1
        except TelegramForbiddenError:   # бота заблокировали — напоминания ему тоже не дойдут
            if cid in SUBS:
                SUBS.discard(cid)
                save_subs(SUBS)
        except Exception:
            logging.exception("zveno to %s failed", cid)
        # вычёркиваем по одному: упадём посреди рассылки — после рестарта не позовём дважды
        zveno_set(cid, False)
        await asyncio.sleep(0.05)
    ZVENO["opened"] = zveno_season(tours)
    save_zveno()
    logging.info("zveno opened, called %d", sent)
    return sent


async def zveno_loop(bot: Bot):
    """Раз в 10 минут — опубликованный tours.json: не стал ли status «open»."""
    while True:
        try:
            await announce_zveno(bot, await published(TOURS, ZVENO_TTL), datetime.now(TZ))
        except Exception:
            logging.exception("zveno loop failed")
        await asyncio.sleep(ZVENO_POLL)


async def load_custom_emoji(bot: Bot) -> None:
    try:
        me = await bot.get_me()
        st = await bot.get_sticker_set(f"rhl_u21_by_{me.username}")
    except TelegramBadRequest:   # набор не опубликован — пишем обычными эмодзи
        logging.warning("custom emoji set not found, plain emoji")
        return
    CUSTOM.update(custom_ids(st.stickers))
    logging.info("custom emoji: %d", len(CUSTOM))


async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(os.environ["BOT_TOKEN"], default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await load_custom_emoji(bot)
    await bot.delete_my_commands()   # меню команд пустое: всё — в мини-аппе
    try:   # описание не критично: без него бот работает
        await bot.set_my_description(DESCRIPTION)
        await bot.set_my_short_description(SHORT_DESCRIPTION)
    except TelegramBadRequest:
        logging.exception("set description failed")
    await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="РХЛ", web_app=WebAppInfo(url=WEBAPP_URL)))
    asyncio.create_task(reminder_loop(bot))
    asyncio.create_task(results_loop(bot))
    asyncio.create_task(zveno_loop(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

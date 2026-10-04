"""HTTP API на VPS: живые файлы матч-центра, зачёт «Раската» и прогноз «Кто победит?».

ADR-019 (раздел 3), ADR-020 (раздел 3), docs/raskat/contract.md (раздел 5). Слушает
127.0.0.1:8080 (`API_HOST`, `API_PORT`), наружу его выводит Caddy (`deploy/https.sh`).

    /api/health              жив ли сервер, сошлась ли соль «Раската»
    /api/live/<файл>.json    каталог live/ (пишет служба live.py), без авторизации
    /api/raskat/*            зачёт «Раската»
    /api/predict/*           прогнозы
    /api/seen                мини-апп открыли: счётчик людей за день для пульта (ADR-021)
    /api/admin/status        пульт админа: здоровье, аудитория, рассылки, игры — только ADMIN_IDS

Авторизация — `Authorization: tma <initData>`: подпись Telegram WebApp, свежесть 24 часа,
пользователь только из проверенного initData. CORS — только адрес Pages (`PAGES_ORIGIN`).
Состояние — SQLite `state.db` (`STATE_DB`): `raskat_store.py` и `predict.py`.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import time as clock
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from aiohttp import ClientSession, ClientTimeout, web

import admin
import predict
import raskat
from raskat import rules
from raskat_store import RaskatStore, Taken

BASE = Path(__file__).resolve().parent
TZ = ZoneInfo("Europe/Moscow")
log = logging.getLogger("api")

AUTH_TTL = 24 * 3600            # initData старше суток не принимаем (контракт, раздел 5)
AUTH_SKEW = 300                 # часы Telegram и сервера могут разойтись на пару минут
MIN_MS_PER_CELL = 150           # быстрее 0,15 с на клетку — не человек (контракт, раздел 5)
MAX_MS = 24 * 3600 * 1000
TOP = 50                        # длина таблицы дня

# Полночь. Раскат, начатый до полуночи, досылается ещё 5 минут. Новый день появляется на Pages
# прогоном в 00:05 (а GitHub запускает его с опозданием), и пока его нет, мини-апп честно
# показывает вчерашний раскат как раскат дня — такой результат принимаем до 02:00
GRACE = timedelta(minutes=5)
GRACE_UNPUBLISHED = timedelta(hours=2)

LEAGUE_TTL = 600                # league.json с Pages — раз в 10 минут (ADR-020)
INDEX_TTL = 300                 # index.json «Раската» — есть ли уже файл нового дня
SALT_EVERY = 3600               # сверка соли повторяется раз в час: вдруг секрет поменяли

LIVE_NAME = r"today|schedule|sources|\d{4}-\d{2}-\d{2}"

# «Болельщик «Рязани-ВДВ»» — имя в таблице без галочки show_tg_name (ADR-018, раздел 3.9).
# Родительный падеж — как в фразах проводников (`mascot` в teams.json); нет в списке — название как есть
GENITIVE = {
    "arktika": "Арктики", "akhmat-granit": "Ахмат-Гранита", "vityaz-podolsk": "Витязя",
    "dinamo-576": "Динамо-576", "kaluga": "Калужских Ракет", "krasnaya-mashina": "Красной машины",
    "leningradets": "Ленинградца", "metallurg": "Металлурга", "dinamo-kareliya": "Динамо-Карелии",
    "polet": "Полёта", "tverichi": "Тверичей-СШОР", "fakel-yamal": "Факела Ямал",
    "bryansk": "Брянска", "dizelist": "Дизелиста", "ermak": "Ермака", "belgorod": "Белгорода",
    "kristall": "Кристалла", "ryazan-vdv": "Рязани-ВДВ", "tambov": "Тамбова",
    "progress": "Прогресса", "proton": "Протона", "rostov": "Ростова", "sokol": "Сокола",
    "krasnodar": "Краснодара", "samara": "Самары", "ekoniva-bobrov": "ЭкоНивы-Бобров",
}

OFF_TEXT = ("Зачёт временно выключен: поле на сервере не сходится с опубликованным. Раскат дня "
            "собирается как обычно, результат сохранится на телефоне.")


class Fail(Exception):
    """Ответ об ошибке: `{"error": "…"}` и код. `extra` — поля сверх ошибки (409 с прежним Result)."""

    def __init__(self, status: int, text: str, extra: dict | None = None):
        super().__init__(text)
        self.status = status
        self.body = {**(extra or {}), "error": text}


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def reply(data, status: int = 200, cache: str = "no-store") -> web.Response:
    return web.json_response(data, status=status, dumps=dumps, headers={"Cache-Control": cache})


# ---------- подпись initData (Telegram WebApp) ----------

def check_init_data(init_data: str, token: str, now: float | None = None) -> tuple[dict | None, str]:
    """Проверить initData по правилам Telegram WebApp → (пользователь, "") или (None, причина).

    Ключ — HMAC-SHA256(key="WebAppData", msg=токен бота), подпись — HMAC-SHA256 этим ключом от
    строки «ключ=значение» всех полей, кроме hash, по алфавиту через перевод строки.
    Причина — `bad` (подпись не сошлась или данные кривые) или `old` (старше суток)."""
    if not token or not init_data:
        return None, "bad"
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None, "bad"
    data: dict[str, str] = {}
    for k, v in pairs:
        if k in data:
            return None, "bad"
        data[k] = v
    got = data.pop("hash", "")
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    want = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want.encode(), got.encode()):
        return None, "bad"
    try:
        auth_date = int(data["auth_date"])
        user = json.loads(data["user"])
    except (KeyError, ValueError):
        return None, "bad"
    now = clock.time() if now is None else now
    if auth_date - now > AUTH_SKEW:
        return None, "bad"
    if now - auth_date > AUTH_TTL:
        return None, "old"
    if not isinstance(user, dict) or not isinstance(user.get("id"), int) or isinstance(user["id"], bool) \
            or user["id"] <= 0:
        return None, "bad"
    return user, ""


def published_puzzle(day: str, pub) -> raskat.Puzzle | None:
    """Опубликованный файл дня (контракт, раздел 2) → расклад для проверки пути. Кривой — None."""
    if not isinstance(pub, dict) or pub.get("date", day) != day:
        return None
    try:
        w, h, par = int(pub["w"]), int(pub["h"]), int(pub["par"])
        dots = tuple(int(x) for x in pub["dots"])
        walls = tuple(str(x) for x in pub["walls"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (2 <= w <= 12 and 2 <= h <= 12 and dots and all(0 <= x < w * h for x in dots) and par > 0):
        return None
    return raskat.Puzzle(date=day, n=int(pub.get("n") or 0), w=w, h=h, dots=dots, walls=walls,
                         hard=int(pub.get("hard") or 1), par=par)


def short_name(user: dict) -> str | None:
    """«Пётр К.» — имя и первая буква фамилии, не больше (ADR-018, раздел 3.9)."""
    first = " ".join(str(user.get("first_name") or "").split())[:32]
    if not first:
        return None
    last = str(user.get("last_name") or "").strip()
    return f"{first} {last[0].upper()}." if last and last[0].isalpha() else first


# ---------- настройки ----------

def pages_base(url: str) -> str:
    """Адрес Pages без ?v=… и с косой чертой в конце: к нему приклеиваются пути data/…"""
    p = urlsplit(url or "https://arpicasso.github.io/RHL-BOT/")
    path = p.path if p.path.endswith("/") else p.path + "/"
    return urlunsplit((p.scheme, p.netloc, path, "", ""))


class Config:
    def __init__(self, token: str = "", live_dir: Path | str = BASE / "live",
                 db_path: Path | str = BASE / "state.db",
                 webapp_url: str = "https://arpicasso.github.io/RHL-BOT/",
                 origins=("https://arpicasso.github.io",), teams_file: Path | str = BASE / "teams.json",
                 admins=(), status_dir: Path | str = admin.STATUS_DIR,
                 subs_file: Path | str = BASE / "subscribers.json"):
        self.token = token
        self.live_dir = Path(live_dir)
        self.db_path = str(db_path)
        self.pages = pages_base(webapp_url)
        self.origins = frozenset(o.strip().rstrip("/") for o in origins if o.strip())
        self.teams_file = Path(teams_file)
        self.admins = frozenset(admins)
        self.status_dir = Path(status_dir)
        self.subs_file = Path(subs_file)

    @staticmethod
    def parse_admins(raw: str) -> frozenset[int]:
        """ADMIN_IDS — Telegram id через запятую или пробел. Не число — пропускаем с предупреждением."""
        out = set()
        for part in re.split(r"[,\s]+", raw or ""):
            if part.isdigit():
                out.add(int(part))
            elif part:
                log.warning("ADMIN_IDS: «%s» — не Telegram id, пропускаю", part)
        return frozenset(out)

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ.get
        return cls(token=(env("BOT_TOKEN") or "").strip(),
                   live_dir=env("LIVE_DIR") or BASE / "live",
                   db_path=env("STATE_DB") or BASE / "state.db",
                   webapp_url=env("WEBAPP_URL") or "https://arpicasso.github.io/RHL-BOT/",
                   origins=(env("PAGES_ORIGIN") or "https://arpicasso.github.io").split(","),
                   admins=cls.parse_admins(env("ADMIN_IDS") or ""),
                   status_dir=env("STATUS_DIR") or admin.STATUS_DIR)


def open_db(path: str) -> sqlite3.Connection:
    """Одно соединение на процесс. Транзакции открываем сами (BEGIN IMMEDIATE в хранилищах)."""
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ---------- приложение ----------

class Api:
    """Состояние сервера и обработчики. `fetch(url)` → JSON или None на 404 — подменяется в тестах,
    `now()` → время с поясом."""

    def __init__(self, config: Config, fetch=None, now=None):
        self.cfg = config
        self._fetch = fetch
        self.now = now or (lambda: datetime.now(TZ))
        self.session: ClientSession | None = None
        self.conn: sqlite3.Connection | None = None
        self.rs: RaskatStore | None = None
        self.pr: predict.PredictStore | None = None
        self.adm: admin.AdminStore | None = None
        teams = json.loads(self.cfg.teams_file.read_text(encoding="utf-8"))
        self.teams = {t["id"]: t["name"] for t in teams}
        # Сверка соли (контракт, раздел 3): None — ещё не сверяли, True — сошлось или не с чем
        self.salt_ok: bool | None = None
        self.salt_note = "сверка ещё не прошла"
        # Соль на сервере не сошлась с Pages — расклады берём из опубликованных файлов дня: поле
        # болельщика нарисовано по ним, а Pages — наш же сайт. Так зачёт не ждёт ручной правки bot.env
        self.published_mode = False
        self._puzzles: dict[tuple[str, str], raskat.Puzzle] = {}
        self._pub_puzzles: dict[str, raskat.Puzzle] = {}
        self._league: tuple[float, dict] = (0.0, {})
        self._league_updated: str | None = None
        self._index: tuple[float, str] = (0.0, "")
        self._stand: dict[tuple, object] = {}
        self._loop: asyncio.Task | None = None

    # ---------- жизнь процесса ----------

    async def startup(self, app=None) -> None:
        self.conn = open_db(self.cfg.db_path)
        self.rs = RaskatStore(self.conn)
        self.pr = predict.PredictStore(self.conn)
        self.adm = admin.AdminStore(self.conn)
        if self._fetch is None:
            self.session = ClientSession(timeout=ClientTimeout(total=15),
                                         headers={"User-Agent": "rhl-u21-api"})
        if not self.cfg.token:
            log.warning("BOT_TOKEN не задан: запросы с авторизацией получат 503")
        try:
            await asyncio.wait_for(self.salt_check(), 20)
        except asyncio.TimeoutError:
            self.salt_note = "Pages не ответил за 20 секунд — зачёт работает без сверки"
            log.warning("сверка соли: %s", self.salt_note)
        self._loop = asyncio.create_task(self._salt_loop())

    async def cleanup(self, app=None) -> None:
        if self._loop:
            self._loop.cancel()
            try:
                await self._loop
            except asyncio.CancelledError:
                pass
        if self.session:
            await self.session.close()
        if self.conn:
            self.conn.close()

    async def _salt_loop(self) -> None:
        while True:
            await asyncio.sleep(SALT_EVERY)
            try:
                await self.salt_check()
            except Exception:
                log.exception("сверка соли упала")

    async def fetch(self, url: str):
        if self._fetch is not None:
            return await self._fetch(url)
        async with self.session.get(url, headers={"Cache-Control": "no-cache"}) as r:
            if r.status == 404:
                return None
            r.raise_for_status()
            return await r.json(content_type=None)

    # ---------- «Раскат»: соль, расклад, день ----------

    def puzzle(self, day: str) -> raskat.Puzzle:
        k = (rules.salt(), day)
        if k not in self._puzzles:
            self._puzzles[k] = raskat.generate(day)
        return self._puzzles[k]

    async def puzzle_for(self, day: str) -> raskat.Puzzle:
        """Расклад, по которому проверяется путь: свой движком или, если соль не сошлась с Pages,
        опубликованный файл дня. Файла нет и не скачался — зачёт этого дня ждёт: 503."""
        if not self.published_mode:
            return self.puzzle(day)
        if day not in self._pub_puzzles:
            try:
                pub = await self.fetch(f"{self.cfg.pages}data/raskat/{day}.json")
            except Exception as e:
                log.warning("расклад %s с Pages не скачался: %s", day, type(e).__name__)
                pub = None
            p = published_puzzle(day, pub)
            if p is None:
                raise Fail(503, OFF_TEXT)
            self._pub_puzzles[day] = p
        return self._pub_puzzles[day]

    async def salt_check(self) -> None:
        """Сегодняшний расклад движком против опубликованного файла дня. Разошлись — зачёт выключен:
        иначе путь болельщика не сходится с полем на сервере и отказ получают все разом.
        Скачать не вышло или файла ещё нет — зачёт работает, в журнале предупреждение."""
        today = self.now().date()
        if not rules.SEASON_FROM <= today <= rules.SEASON_TO + timedelta(days=1):
            self.salt_ok, self.salt_note = True, "сезон «Раската» не идёт — сверять не с чем"
            return
        for day in (today, today - timedelta(days=1)):
            if day < rules.SEASON_FROM or day > rules.SEASON_TO:
                continue
            url = f"{self.cfg.pages}data/raskat/{day.isoformat()}.json"
            try:
                pub = await self.fetch(url)
            except Exception as e:
                self.salt_ok = True if self.salt_ok is None else self.salt_ok
                self.salt_note = f"не удалось скачать {url}: {type(e).__name__} — зачёт работает без сверки"
                log.warning("сверка соли: %s", self.salt_note)
                return
            if pub is None:
                continue
            mine = raskat.as_json(self.puzzle(day.isoformat()))
            if isinstance(pub, dict) and all(pub.get(k) == mine[k] for k in ("w", "h", "dots", "walls")):
                self.salt_ok, self.published_mode = True, False
                self.salt_note = f"соль сошлась с опубликованным раскладом {day}"
                log.info("сверка соли: %s", self.salt_note)
            elif published_puzzle(day.isoformat(), pub):
                self.salt_ok, self.published_mode = True, True
                self.salt_note = (f"расклад {day} на сервере не совпал с опубликованным — зачёт идёт по "
                                  f"опубликованным раскладам. Чтобы сервер считал сам, RASKAT_SALT в "
                                  f"/etc/rhl/bot.env и секрет RASKAT_SALT задания Pages должны совпасть")
                log.warning("сверка соли: %s", self.salt_note)
            else:
                self.salt_ok = False
                self.salt_note = f"опубликованный расклад {day} не читается — зачёт выключен"
                log.error("сверка соли: %s", self.salt_note)
            return
        self.salt_ok = True if self.salt_ok is None else self.salt_ok
        self.salt_note = "опубликованного расклада сегодня и вчера нет — сверять не с чем"
        log.warning("сверка соли: %s", self.salt_note)

    async def published_today(self) -> str:
        """`today` из опубликованного index.json «Раската»; не скачался — пусто."""
        at, day = self._index
        if clock.monotonic() - at < INDEX_TTL and at:
            return day
        try:
            idx = await self.fetch(f"{self.cfg.pages}data/raskat/index.json")
            day = str(idx.get("today") or "") if isinstance(idx, dict) else ""
        except Exception as e:
            log.warning("index.json «Раската» не скачался: %s", type(e).__name__)
            day = ""
        self._index = (clock.monotonic(), day)
        return day

    async def day_open(self, day: date, now: datetime) -> bool:
        """Принимаем ли результат этого дня сейчас: только раскат дня, тренировка в зачёт не идёт."""
        today = now.date()
        if day == today:
            return True
        if day != today - timedelta(days=1):
            return False
        since = now - datetime.combine(today, time(0), TZ)
        if since <= GRACE:
            return True
        return since <= GRACE_UNPUBLISHED and (await self.published_today()) < today.isoformat()

    # ---------- авторизация ----------

    def user(self, request: web.Request, required: bool = True) -> dict | None:
        head = request.headers.get("Authorization", "")
        if not head:
            if required:
                raise Fail(401, "Открой мини-апп из Telegram: без входа через Telegram это не работает.")
            return None
        if not self.cfg.token:
            raise Fail(503, "Сервер ещё не настроен. Загляни чуть позже.")
        scheme, _, data = head.partition(" ")
        if scheme.lower() != "tma":
            raise Fail(401, "Telegram не подтвердил вход. Закрой мини-апп и открой его снова.")
        user, why = check_init_data(data.strip(), self.cfg.token, self.now().timestamp())
        if user is None:
            if why == "old":
                raise Fail(401, "Вход устарел. Закрой мини-апп и открой его снова.")
            raise Fail(401, "Telegram не подтвердил вход. Закрой мини-апп и открой его снова.")
        return user

    def raskat_on(self) -> None:
        if self.salt_ok is False:
            raise Fail(503, OFF_TEXT)

    async def body(self, request: web.Request) -> dict:
        try:
            data = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise Fail(400, "Не понял запрос. Обнови мини-апп и попробуй ещё раз.")
        if not isinstance(data, dict):
            raise Fail(400, "Не понял запрос. Обнови мини-апп и попробуй ещё раз.")
        return data

    @staticmethod
    def day_arg(request: web.Request) -> date:
        try:
            return date.fromisoformat(request.match_info["date"])
        except ValueError:
            raise Fail(400, "Не понял дату.")

    @staticmethod
    def in_season(day: date) -> None:
        if not rules.SEASON_FROM <= day <= rules.SEASON_TO:
            raise Fail(404, "Такого раската нет: «Раскат» идёт с 3 октября по 21 марта.")

    def club_arg(self, data: dict) -> object:
        """Клуб болельщика из тела запроса: нет поля — `...` (не трогаем), иначе id из teams.json или None."""
        if "club" not in data:
            return ...
        club = data["club"]
        if club is None or club in self.teams:
            return club
        raise Fail(400, "Такого клуба в РХЛ нет.")

    # ---------- «Раскат»: ответы ----------

    def label(self, club: str | None) -> str:
        if club in self.teams:
            return f"Болельщик «{GENITIVE.get(club, self.teams[club])}»"
        return "Болельщик"

    def touch(self, fan: int, user: dict) -> dict:
        """Настройки болельщика. Галочка стоит, а имя в Telegram поменялось — обновим строку таблицы."""
        s = self.rs.fan(fan)
        if s["show_tg_name"] and s["name"] != short_name(user):
            s = self.rs.settings(fan, name=short_name(user))
        return s

    def day_stand(self, day: str):
        k = ("day", self.rs.version, day)
        if k not in self._stand:
            if len(self._stand) > 64:
                self._stand.clear()
            self._stand[k] = raskat.standings(self.rs.day(day))
        return self._stand[k]

    def season_stand(self, day: str):
        k = ("season", self.rs.version, day)
        if k not in self._stand:
            if len(self._stand) > 64:
                self._stand.clear()
            self._stand[k] = raskat.standings(self.rs.upto(day))
        return self._stand[k]

    def result_json(self, r: dict) -> dict:
        rows = self.day_stand(r["date"]).days.get(r["date"], [])
        place = next((x.place for x in rows if x.fan == str(r["fan"])), None)
        return {"date": r["date"], "points": r["points"], "total": r["total"], "ms": r["ms"],
                "hint": r["hint"], "place": place, "of": len(rows), "streak": r["streak"]}

    def shown_name(self, info: dict | None, club: str | None) -> str:
        if info and info.get("show_tg_name") and info.get("name"):
            return info["name"]
        return self.label(club)

    async def rs_me(self, request):
        user = self.user(request)
        self.raskat_on()
        fan = user["id"]
        s = self.touch(fan, user)
        today = self.now().date().isoformat()
        mine = self.rs.mine(fan)
        best = max(mine, key=lambda r: (r["points"], -r["ms"]), default=None)
        now = self.rs.result(fan, today)
        return reply({"club": s["club"], "streak": self.rs.streak(fan, today),
                      "best": self.result_json(best) if best else None,
                      "today": self.result_json(now) if now else None,
                      "show_tg_name": s["show_tg_name"], "messages": s["messages"]})

    async def rs_post_day(self, request):
        user = self.user(request)
        self.raskat_on()
        fan = user["id"]
        day = self.day_arg(request)
        self.in_season(day)
        data = await self.body(request)
        s = self.touch(fan, user)
        was = self.rs.result(fan, day.isoformat())
        if was:
            raise Fail(409, "Результат этого дня уже принят: в зачёт идёт первый собранный раскат.",
                       self.result_json(was))
        now = self.now()
        if not await self.day_open(day, now):
            if day > now.date():
                raise Fail(400, "Этот раскат ещё не открылся.")
            raise Fail(400, "Этот день уже закончился: тренировка в зачёт не идёт.")
        path, ms, hint = data.get("path"), data.get("ms"), data.get("hint", False)
        if not isinstance(path, list) or len(path) > 400:
            raise Fail(400, "Путь должен быть списком клеток.")
        if isinstance(ms, bool) or not isinstance(ms, (int, float)) or not math.isfinite(ms) \
                or not 0 <= ms <= MAX_MS:
            raise Fail(400, "Не понял время раската.")
        if not isinstance(hint, bool):
            raise Fail(400, "Не понял, была ли подсказка.")
        ms = int(round(ms))
        club = self.club_arg(data)
        puzzle = await self.puzzle_for(day.isoformat())
        verdict = raskat.check(puzzle, path)
        if not verdict:
            raise Fail(400, verdict.reason)
        if ms < MIN_MS_PER_CELL * len(path):
            raise Fail(400, "Так быстро шайбу не провести: меньше 0,15 секунды на клетку. "
                            "Результат в зачёт не идёт.")
        if club is not ... and club != s["club"]:
            s = self.rs.settings(fan, club=club)
        par, sec = puzzle.par, ms / 1000

        def score(before: int) -> tuple[int, int]:
            return raskat.day_points(par, sec, hint), raskat.points(par, sec, before, hint)

        try:
            r = self.rs.add(fan, day.isoformat(), s["club"], ms, hint, score, now)
        except Taken as e:
            raise Fail(409, "Результат этого дня уже принят: в зачёт идёт первый собранный раскат.",
                       self.result_json(e.result))
        return reply(self.result_json(r))

    async def rs_get_day(self, request):
        user = self.user(request)
        self.raskat_on()
        fan = user["id"]
        self.touch(fan, user)
        day = self.day_arg(request)
        self.in_season(day)
        d = day.isoformat()
        rows = self.day_stand(d).days.get(d, [])
        top = rows[:TOP]
        hints = {str(r["fan"]): r["hint"] for r in self.rs.day(d)}
        info = self.rs.fans(int(r.fan) for r in top)
        out = [{"place": r.place, "name": self.shown_name(info.get(int(r.fan)), r.club), "club": r.club,
                "points": r.points, "ms": r.ms, "hint": hints.get(r.fan, False), "me": r.fan == str(fan)}
               for r in top]
        mine = self.rs.result(fan, d)
        return reply({"me": self.result_json(mine) if mine else None, "top": out, "of": len(rows)})

    async def rs_clubs(self, request):
        user = self.user(request)
        self.raskat_on()
        fan = user["id"]
        my = self.touch(fan, user)["club"]
        day = self.day_arg(request)
        self.in_season(day)
        d = day.isoformat()
        st = self.season_stand(d)
        fans_of: dict[str, int] = {}
        for r in st.days.get(d, []):
            if r.club:
                fans_of[r.club] = fans_of.get(r.club, 0) + 1

        def club_json(c) -> dict:
            return {"place": c.place, "club": c.club, "avg": c.avg, "fans": c.fans, "days": c.days,
                    "me": c.club == my}

        low = [{"place": None, "club": c, "avg": None, "fans": fans_of.get(c, 0), "days": 1, "me": c == my}
               for c in st.short.get(d, [])]
        return reply({"day": [club_json(c) for c in st.clubs.get(d, [])], "low": low,
                      "season": [club_json(c) for c in st.season], "me": my})

    async def rs_duel(self, request):
        user = self.user(request)
        self.raskat_on()
        fan = user["id"]
        self.touch(fan, user)
        other = self.rs.by_duel(request.match_info["code"])
        if other is None:
            raise Fail(404, "Дуэль не нашлась: ссылку удалили или в ней ошибка.")
        info = self.rs.fan(other)
        theirs = {r["date"]: r for r in self.rs.mine(other)}
        club = info["club"] or next((r["club"] for r in reversed(list(theirs.values())) if r["club"]), None)
        name = self.shown_name(info, club)
        if other == fan:
            return reply({"name": name, "days": [], "own": True})
        # Только дни, когда играли оба: иначе по ссылке видно, в какие дни человек не заходил
        days = [{"date": r["date"], "me": r["points"], "them": theirs[r["date"]]["points"]}
                for r in self.rs.mine(fan) if r["date"] in theirs]
        return reply({"name": name, "days": days})

    async def rs_new_duel(self, request):
        user = self.user(request)
        self.raskat_on()
        self.touch(user["id"], user)
        return reply({"code": self.rs.duel_code(user["id"])})

    async def rs_settings(self, request):
        user = self.user(request)
        self.raskat_on()
        fan = user["id"]
        data = await self.body(request)
        flags = {}
        for k in ("show_tg_name", "messages"):
            if k in data:
                if not isinstance(data[k], bool):
                    raise Fail(400, "Не понял настройку.")
                flags[k] = data[k]
        club = self.club_arg(data)
        name = ...
        if "show_tg_name" in flags:
            name = short_name(user) if flags["show_tg_name"] else None
        s = self.rs.settings(fan, club=club, name=name, **flags)
        return reply({"club": s["club"], "show_tg_name": s["show_tg_name"], "messages": s["messages"]})

    async def rs_forget(self, request):
        user = self.user(request)
        self.rs.forget(user["id"])
        self.adm.forget(user["id"])
        log.info("raskat: болельщик стёр свои раскаты")
        return reply({})

    # ---------- живые файлы ----------

    def live_file(self, name: str):
        """JSON из live/ или None: файла нет или он битый (live.py пишет атомарно, но мало ли)."""
        try:
            return json.loads((self.cfg.live_dir / f"{name}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    async def live(self, request):
        name = request.match_info["name"]
        try:
            body = (self.cfg.live_dir / f"{name}.json").read_bytes()
        except OSError:
            raise Fail(404, "Этих данных пока нет: живой счёт ещё не собран.")
        return web.Response(body=body, content_type="application/json", charset="utf-8",
                            headers={"Cache-Control": "max-age=10"})

    async def health(self, request):
        today = self.live_file("today")
        return reply({"ok": True, "time": self.now().isoformat(timespec="seconds"),
                      "raskat": {"on": self.salt_ok is not False, "note": self.salt_note,
                                 "salt": "секрет" if os.environ.get("RASKAT_SALT") else "из репозитория"},
                      "auth": bool(self.cfg.token),
                      "live": {"updated": today.get("updated") if isinstance(today, dict) else None}})

    # ---------- прогнозы ----------

    async def league(self) -> dict:
        """Матчи опубликованного league.json: ключ → матч. Кэш 10 минут, не скачался — прежний."""
        at, games = self._league
        if at and clock.monotonic() - at < LEAGUE_TTL:
            return games
        try:
            data = await self.fetch(f"{self.cfg.pages}data/league.json")
            games = {predict.key_of(g): g for g in (data or {}).get("games", []) if predict.key_of(g)}
            self._league = (clock.monotonic(), games)
            self._league_updated = (data or {}).get("updated") if isinstance(data, dict) else None
        except Exception as e:
            log.warning("league.json не скачался: %s", type(e).__name__)
            # следующая попытка через минуту, а не на каждом запросе
            self._league = (clock.monotonic() - LEAGUE_TTL + 60, games)
        return games

    @staticmethod
    def _by_key(data, day: str) -> dict:
        out = {}
        if isinstance(data, dict) and isinstance(data.get("games"), list):
            for g in data["games"]:
                k = predict.key_of(g)
                if k and k.startswith(day + "|"):
                    out[k] = g
        return out

    async def games_on(self, day: str) -> dict[str, dict]:
        """Матчи дня из live/<дата>.json, live/schedule.json и league.json: ключ → `predict.merge`."""
        live_day = self.live_file(day)
        if live_day is None:
            today = self.live_file("today")
            live_day = today if isinstance(today, dict) and today.get("date") == day else None
        live = self._by_key(live_day, day)
        sched = self._by_key(self.live_file("schedule"), day)
        league = {k: g for k, g in (await self.league()).items() if k.startswith(day + "|")}
        out = {}
        for k in set(live) | set(sched) | set(league):
            g = predict.merge(k, live.get(k), sched.get(k), league.get(k))
            if g:
                out[k] = g
        return out

    def tally(self, key: str, game: dict | None, fan: int | None, counts=None) -> dict:
        h, a = counts if counts is not None else self.pr.counts(key)
        open_ = predict.is_open(game, self.now()) if game else False
        return predict.tally(h, a, self.pr.pick(fan, key), open_, game["result"] if game else None)

    async def pr_day(self, request):
        user = self.user(request, required=False)
        fan = user["id"] if user else None
        day = self.day_arg(request).isoformat()
        games = await self.games_on(day)
        counts = self.pr.day_counts(day)
        mine = self.pr.picks(fan, day)
        now = self.now()
        out = {}
        for k in sorted(set(games) | set(counts)):
            g = games.get(k)
            h, a = counts.get(k, (0, 0))
            out[k] = predict.tally(h, a, mine.get(k), predict.is_open(g, now) if g else False,
                                   g["result"] if g else None)
        return reply({"games": out})

    async def pr_vote(self, request):
        user = self.user(request)
        data = await self.body(request)
        key, pick = data.get("key"), data.get("pick")
        parsed = predict.parse_key(key)
        if parsed is None:
            raise Fail(400, "Не понял, какой это матч.")
        if pick not in predict.PICKS:
            raise Fail(400, "Выбери хозяев или гостей.")
        game = (await self.games_on(parsed[0])).get(key)
        if game is None:
            raise Fail(404, "Такого матча нет в календаре.")
        if not predict.is_open(game, self.now()):
            raise Fail(409, "Приём прогнозов закрыт: матч уже начался.")
        self.pr.vote(user["id"], key, pick, self.now())
        return reply(self.tally(key, game, user["id"]))

    async def pr_me(self, request):
        user = self.user(request)
        picks = self.pr.picks(user["id"])
        days: dict[str, dict] = {}
        pairs = []
        for key, pick in picks.items():
            day = key[:10]
            if day not in days:
                days[day] = await self.games_on(day)
            pairs.append((days[day].get(key), pick))
        return reply(predict.record(pairs))

    async def pr_forget(self, request):
        user = self.user(request)
        self.pr.forget(user["id"])
        self.adm.forget(user["id"])
        log.info("predict: болельщик стёр свои прогнозы")
        return reply({})

    # ---------- пульт админа (ADR-021) ----------

    async def seen(self, request):
        """Мини-апп открыли в Telegram: человек за день считается один раз. Тело — платформа и клуб."""
        user = self.user(request)
        data = await self.body(request)
        platform = data.get("platform") if isinstance(data.get("platform"), str) else None
        fav = data.get("fav") if data.get("fav") in self.teams else None
        self.adm.seen(user["id"], self.now().date(), platform, fav)
        return reply({})

    def admin_user(self, request) -> dict:
        user = self.user(request)
        if not self.cfg.admins:
            raise Fail(403, f"Список админов пуст. Впиши свой Telegram id {user['id']} в ADMIN_IDS "
                            "в /etc/rhl/bot.env и перезапусти службу api.")
        if user["id"] not in self.cfg.admins:
            raise Fail(403, f"Пульт только для админов. Твой Telegram id {user['id']} — его вписывают "
                            "в ADMIN_IDS на сервере.")
        return user

    async def services(self) -> tuple[dict | None, str]:
        """Состояние служб systemd. Не Linux, нет systemctl или он молчит — (None, почему)."""
        try:
            proc = await asyncio.create_subprocess_exec(*admin.systemctl_args(), stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.DEVNULL)
        except OSError as e:
            return None, f"systemctl: {type(e).__name__}"
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), 5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return None, "systemctl не ответил за 5 секунд"
        parsed = admin.parse_systemctl(out.decode("utf-8", "replace"))
        return (parsed, "") if parsed else (None, "systemctl ничего не ответил")

    def disk(self) -> dict | None:
        try:
            du = shutil.disk_usage(Path(self.cfg.db_path).parent)
        except OSError:
            return None
        db = 0
        for suffix in ("", "-wal"):
            try:
                db += os.path.getsize(self.cfg.db_path + suffix)
            except OSError:
                pass
        return {"free": du.free, "total": du.total, "db": db}

    async def admin_status(self, request):
        self.admin_user(request)
        now = self.now()
        await self.league()   # заодно время сборки league.json
        services, note = await self.services()
        since = now.date() - timedelta(days=admin.WEEK - 1)
        st = admin.build_status(
            now=now, teams=self.teams, services=services, services_note=note,
            bot=admin.read_json(self.cfg.status_dir / "bot.json"),
            pages=admin.read_json(self.cfg.status_dir / "pages.json"),
            league_updated=self._league_updated, live_today=self.live_file("today"),
            sources=self.live_file("sources"),
            raskat={"on": self.salt_ok is not False, "note": self.salt_note},
            disk=self.disk(), subs=admin.read_json(self.cfg.subs_file),
            app_counts=self.adm.counts(since), games=admin.game_stats(self.conn, since, now.date()),
            retention=self.adm.retention(now.date()))
        return reply(st)

    # ---------- обвязка ----------

    @web.middleware
    async def cors(self, request, handler):
        """CORS только для адреса Pages: чужой сайт не прочитает ответ, а preflight получит 403."""
        origin = request.headers.get("Origin", "")
        allowed = origin in self.cfg.origins
        if request.method == "OPTIONS" and request.headers.get("Access-Control-Request-Method"):
            if not allowed:
                return reply({"error": "Запрос с чужого сайта."}, status=403)
            resp = web.Response(status=204, headers={
                "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type",
                "Access-Control-Max-Age": "600"})
        else:
            resp = await handler(request)
        if allowed:
            resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        return resp

    @web.middleware
    async def errors(self, request, handler):
        try:
            return await handler(request)
        except Fail as e:
            return reply(e.body, status=e.status)
        except web.HTTPException as e:
            text = {404: "Такого адреса у сервера нет.", 405: "Этот адрес такой запрос не принимает.",
                    413: "Слишком большой запрос."}.get(e.status, "Запрос не получился.")
            return reply({"error": text}, status=e.status)
        except Exception:
            log.exception("%s %s", request.method, request.path)
            return reply({"error": "Сервер споткнулся. Попробуй ещё раз чуть позже."}, status=500)


API_KEY = web.AppKey("api", Api)


def make_app(config: Config | None = None, fetch=None, now=None) -> web.Application:
    api = Api(config or Config.from_env(), fetch=fetch, now=now)
    app = web.Application(middlewares=[api.cors, api.errors], client_max_size=64 * 1024)
    app[API_KEY] = api
    app.on_startup.append(api.startup)
    app.on_cleanup.append(api.cleanup)
    r = app.router
    r.add_get("/api/health", api.health)
    r.add_post("/api/seen", api.seen)
    r.add_get("/api/admin/status", api.admin_status)
    r.add_get("/api/live/{name:" + LIVE_NAME + "}.json", api.live)
    rs = "/api/raskat"
    r.add_get(rs + "/me", api.rs_me)
    r.add_delete(rs + "/me", api.rs_forget)
    r.add_post(rs + "/day/{date}", api.rs_post_day)
    r.add_get(rs + "/day/{date}", api.rs_get_day)
    r.add_get(rs + "/clubs/{date}", api.rs_clubs)
    r.add_get(rs + "/duel/{code:[A-Za-z0-9-]{3,24}}", api.rs_duel)
    r.add_post(rs + "/duel", api.rs_new_duel)
    r.add_put(rs + "/settings", api.rs_settings)
    pr = "/api/predict"
    r.add_get(pr + "/day/{date}", api.pr_day)
    r.add_post(pr + "/vote", api.pr_vote)
    r.add_get(pr + "/me", api.pr_me)
    r.add_delete(pr + "/me", api.pr_forget)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host = os.environ.get("API_HOST") or "127.0.0.1"
    port = int(os.environ.get("API_PORT") or 8080)
    log.info("API слушает http://%s:%s/api/", host, port)
    web.run_app(make_app(), host=host, port=port, access_log=None, print=lambda *_: None)


if __name__ == "__main__":
    main()

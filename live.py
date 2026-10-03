"""Служба live: опрос сайта РХЛ и онлайна КХЛ → live/*.json (ADR-019, разделы 4–5).

Главный источник с 03.10.2026 — сайт лиги rhl.fhr.ru (rhl_site.py): календарь с временем МСК и лента
дней со счётом, а у идущего матча — страница матч-центра со счётом и периодом. Лента событий матча —
по смене счёта и периода между опросами страницы (site_events). Онлайн КХЛ ботам отвечает 403 даже
с российского IP; после 403 служба не спрашивает его час.

    python live.py                                   служба: опрашивает, пока не придёт SIGTERM
    python live.py --once                            один проход и выход
    python live.py --once --now 2026-10-03T16:50+03:00   проход «как будто сейчас» это время

Запросы идут по одному с паузой league.PAUSE. Ночью (01:00–08:00 МСК) не опрашиваем. Падение
одного источника не валит службу: ошибка уходит в live/sources.json и журнал, следующий проход
идёт как обычно.
"""
import argparse
import asyncio
import contextlib
import json
import logging
import os
import re
import signal
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import aiohttp

import build_data
import khl_online
import league
import rhl_site

BASE = Path(__file__).parent
TZ = ZoneInfo("Europe/Moscow")
LIVE_DIR = BASE / "live"
LEAGUE_SITE = "https://rhl.fhr.ru"
SRC_ONLINE = "online.khl.ru"

DAY_START, DAY_END = time(8, 0), time(1, 0)   # опрос с 08:00 до 01:00 МСК (ADR-019, раздел 4)
LIST_SLOW = timedelta(minutes=10)              # список дня, когда матчей РХЛ в окне нет
LIST_FAST = timedelta(seconds=30)              # и когда есть
PAGE_EVERY = timedelta(seconds=30)             # страница идущего матча
CALENDAR_EVERY = timedelta(hours=6)            # календарь сайта лиги
WINDOW_BEFORE = timedelta(minutes=15)          # окно матча: за 15 минут до начала — окончен
GAME_MAX = timedelta(hours=4)                  # дольше матч не идёт: дальше страницу не спрашиваем
ENDED_HOLD = timedelta(minutes=15)             # «окончен» перепроверяем: бот ждёт 10 минут подряд (раздел 8)
SOON = timedelta(hours=1)
STALE = timedelta(minutes=5)                   # живое без обновления дольше — без минуты (ADR-012)
LIST_OVER_PAGE = timedelta(seconds=90)         # список свежее страницы на столько — верим списку
SCHEDULE_DAYS = 14
TITLE_RETRY = timedelta(hours=1)               # заголовок страницы не разобрался — спросим снова через час
BLOCKED_PAUSE = timedelta(hours=1)             # онлайн ответил 403 — час его не спрашиваем
SITE_EVERY = timedelta(minutes=10)             # календарь сайта лиги, когда идёт или скоро матч
# Предположение до проверки probe-командой: календарь сайта лиги, как и протокол (ADR-001), даёт местное
# время арены. Переводим по поясу хозяев `tz` из teams.json; пояса нет — считаем московским.
CALENDAR_LOCAL = True

LIVE_STATUSES = ("live", "break", "ended", "moved", "off")
DONE = ("ended", "final", "moved", "off")


def now_msk() -> datetime:
    return datetime.now(TZ)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(TZ).isoformat(timespec="seconds") if dt else None


def parse_iso(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    return dt.astimezone(TZ) if dt.tzinfo else None


def quiet(now: datetime) -> bool:
    """Ночная тишина: 01:00–08:00 по Москве."""
    t = now.astimezone(TZ).time()
    return DAY_END <= t < DAY_START


def time_status(start: datetime | None, now: datetime) -> str | None:
    """sched — до начала больше часа, soon — меньше часа или начало прошло, а живых данных нет.
    Прошло больше GAME_MAX — None: что с матчем, не знаем, и не выдумываем."""
    if start is None or now > start + GAME_MAX:
        return None
    return "sched" if start - now > SOON else "soon"


def write_json(path: Path, data) -> None:
    """Атомарно: временный файл рядом и os.replace — файл не бывает записан наполовину."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))   # упадёт до записи, если данные плохие
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", build_data.norm(text or "").replace("мхк ", "").replace("хк ", ""))


def team_names(teams: build_data.Teams) -> list[str]:
    """Написания команд РХЛ (нормализованные), длинные первыми."""
    return sorted({n for n in [*teams.by_name, *teams.by_former] if len(n) >= 4}, key=len, reverse=True)


def mentions_team(names: list[str], text: str) -> bool:
    text = _norm_text(text)
    return any(re.search(rf"(?<![а-я0-9]){re.escape(n)}(?![а-я0-9])", text) for n in names)


# ---------- события по ходу матча со страницы сайта лиги (ADR-019, раздел 5) ----------

PERIOD_START = {"1": "Начался 1-й период", "2": "Начался 2-й период", "3": "Начался 3-й период",
                "ОТ": "Начался овертайм", "РБ": "Серия буллитов"}
PERIOD_END = {"1": "Конец 1-го периода", "2": "Конец 2-го периода", "3": "Конец 3-го периода", "ОТ": "Конец овертайма"}


def site_events(old: dict | None, new: dict, last_period: str | None, at: datetime) -> list[dict]:
    """События между двумя опросами страницы матча на сайте лиги. По ходу матча сайт даёт только счёт и строку
    «2-й период» (снимки 03.10.2026), поэтому гол — это смена счёта, начало и конец периода — смена строки.
    Минуты гола сайт не даёт: `time` — null, `at` — когда служба заметила. Чего не видели, не выдумываем:
    первый опрос уже идущего матча событий не даёт, после перерыва в опросе дольше STALE — голы без периода и
    без событий периода, счёт вырос у обеих сторон — голы без счёта (порядок неизвестен)."""
    if not old or not old.get("seen"):
        return []
    gap = at - old["seen"] > STALE
    o_st, n_st, o_per, n_per = old.get("status"), new.get("status"), old.get("period"), new.get("period")
    period = n_per or last_period or o_per

    def ev(kind: str, **kw) -> dict:
        return {"kind": kind, "period": None if gap else period, "time": None, "team": None, "text": None,
                "score": None, "at": iso(at), "src": "rhl.fhr.ru", **kw}
    starts, goals, ends = [], [], []
    base, cur = old.get("score"), new.get("score")
    if base is None and o_st is None and n_st == "live" and n_per == "1":
        base = {"home": 0, "away": 0}   # прошлый опрос — до начала: всё, что на табло, забито сейчас
    if base and cur:
        dh, da = cur["home"] - base["home"], cur["away"] - base["away"]
        if dh < 0 or da < 0:
            goals.append(ev("text", text=f"Счёт на сайте лиги исправлен: {cur['home']}:{cur['away']}"))
        else:
            h, a = base["home"], base["away"]
            for side, n in (("home", dh), ("away", da)):
                for _ in range(n):
                    h, a = (h + 1, a) if side == "home" else (h, a + 1)
                    goals.append(ev("goal", team=side, score=None if dh and da else f"{h}:{a}"))
    if not gap:
        if n_st == "live" and n_per and n_per != o_per and (o_st in ("live", "break") or o_st is None and n_per == "1"):
            starts.append(ev("period", period=n_per, text=PERIOD_START.get(n_per)))
        ended = last_period or o_per
        if n_st == "break" and o_st == "live":
            ends.append(ev("period", period=ended, text=PERIOD_END.get(ended, "Перерыв")))
        elif n_st == "ended" and o_st in ("live", "break"):
            ends.append(ev("period", period=ended, text="Матч окончен"))
    return starts + goals + ends


def minute_period(minute: int) -> str:
    """Период по минуте сайта: 29' — это 29:00–29:59 от начала матча, второй период."""
    return "1" if minute < 20 else "2" if minute < 40 else "3" if minute < 60 else "ОТ"


def with_authors(events: list[dict], goals: dict | None, hidden: set[int] = frozenset()) -> list[dict]:
    """Авторы и минуты голов из блока авторов страницы матча (matchcenter-hero__composition). 03.10.2026 по
    ходу матча блок был пуст и заполнился после матча, вместе с протоколом; появится раньше — служба возьмёт.
    Гол хозяев со счётом «2:1» — второй в списке хозяев. Секунд сайт не даёт: минута — в `minute`, `time`
    остаётся null. Скрытые по просьбе игроки (ADR-007) — «Игрок скрыт»."""
    if not goals or not (goals.get("home") or goals.get("away")):
        return events
    out = []
    for e in events:
        side, score = e.get("team"), e.get("score")
        rows = (goals.get(side) or []) if side in ("home", "away") else []
        if e.get("kind") == "goal" and rows and isinstance(score, str) and re.fullmatch(r"\d+:\d+", score):
            k = int(score.split(":")[0 if side == "home" else 1]) - 1
            if 0 <= k < len(rows):
                row = rows[k]
                e = {**e, "text": build_data.HIDDEN_NAME if row.get("player") in hidden else row["name"],
                     "minute": row["min"], "period": e.get("period") or minute_period(row["min"])}
        out.append(e)
    return out


class Live:
    """Состояние службы: матчи дня, календарь, кэш лиг по номеру онлайна, здоровье источников.

    fetch — корутина url → текст страницы (в службе — Fetcher, в тестах — словарь), clock — «сейчас»."""

    def __init__(self, out_dir: Path, teams: build_data.Teams, fetch, clock=now_msk, site: str | None = LEAGUE_SITE,
                 tomorrow_url: str | None = None):
        self.out, self.teams, self.fetch, self.clock = Path(out_dir), teams, fetch, clock
        self.site = (site or "").rstrip("/")
        self.site_src = urlparse(self.site).netloc or self.site
        self.tomorrow_url = tomorrow_url
        self.tz = {t["id"]: t.get("tz") for t in teams.all}
        self.names = team_names(teams)
        self.khl_ids: dict[str, dict] = read_json(self.out / "khl_ids.json", {})
        self.games: dict[str, dict] = {}      # ключ матча → внутреннее состояние
        self.calendar: dict[str, dict] = {}   # ключ → матч из календаря сайта лиги
        self.tours: dict[int, int] = {}       # idgame → id турнира: для ссылки на протокол
        self.title_retry: dict[int, datetime] = {}
        self.warned: set[str] = set()
        self.notes: dict[str, list[str]] = {}
        self.last_list = self.last_tomorrow = self.last_cal = None
        self.last_page: dict[str, datetime] = {}
        self.written: dict[str, str] = {}
        self.sources: dict[str, dict] = read_json(self.out / "sources.json", {})
        self.blocked_until: datetime | None = None   # онлайн КХЛ ответил 403: до этого времени не спрашиваем
        self.site_starts: dict[int, datetime | None] = {}   # id матча сайта → начало, со страницы матч-центра
        self.hidden = build_data.load_hidden()   # авторы голов, которых не показываем (ADR-007)
        self.restore()

    # ---------- источники: здоровье ----------

    def _src(self, name: str) -> dict:
        return self.sources.setdefault(name, {"ok": None, "fail": None, "errors": 0, "games": 0, "note": ""})

    def ok(self, name: str, games: int, note: str = "") -> None:
        s = self._src(name)
        s.update(ok=iso(self.clock()), errors=0, games=games, note=note)

    def fail(self, name: str, err) -> None:
        s = self._src(name)
        s.update(fail=iso(self.clock()), errors=s.get("errors", 0) + 1, note=str(err)[:300])
        if name == SRC_ONLINE and "403" in str(err):
            self.blocked_until = self.clock() + BLOCKED_PAUSE
            s["note"] = f"{s['note']} — не спрашиваем до {self.blocked_until:%H:%M}"
        logging.warning("%s: %s", name, err)

    def online_blocked(self, now: datetime) -> bool:
        return self.blocked_until is not None and now < self.blocked_until

    def warn_once(self, src: str, text: str) -> None:
        self.notes.setdefault(src, []).append(text)
        if text not in self.warned:
            self.warned.add(text)
            logging.warning("%s: %s", src, text)

    # ---------- матчи ----------

    def game(self, day: str, home: str, away: str) -> dict:
        key = f"{day}|{home}|{away}"
        return self.games.setdefault(key, {"key": key, "date": day, "home": home, "away": away, "start": None,
                                           "start_src": None, "khl_id": None, "list": None, "page": None,
                                           "ended_at": None, "cal_seen": None, "site_t": None})

    def team_ids(self, home_raw: str | None, away_raw: str | None, pair: str | None = None):
        home, away = khl_online.match_teams(self.teams, home_raw, away_raw)
        if (not home or not away) and pair:
            home, away = khl_online.match_teams(self.teams, *khl_online.split_pair(pair, self.teams))
        return home, away

    def page_state(self, g: dict) -> dict | None:
        """Что знаем о ходе матча: страница матча, а если список заметно свежее — список."""
        page, lst = g.get("page"), g.get("list")
        if lst and lst.get("status") and (not page or not page.get("status")
                                          or lst["seen"] - page["seen"] > LIST_OVER_PAGE):
            return {**lst, "clock": None, "period": lst.get("period")}
        return page if page and page.get("status") else None

    def status(self, g: dict, now: datetime) -> str | None:
        st = self.page_state(g)
        if st and st.get("status") in LIVE_STATUSES:
            return st["status"]
        return time_status(g.get("start"), now)

    def in_window(self, g: dict, now: datetime) -> bool:
        """Окно матча (ADR-019, раздел 4): за 15 минут до начала — окончен."""
        st = self.status(g, now)
        start = g.get("start")
        if st in DONE:
            return False
        if start is not None and now > start + GAME_MAX:
            return False
        if st in ("live", "break"):
            return True
        return start is not None and start - WINDOW_BEFORE <= now

    def needs_page(self, g: dict, now: datetime) -> bool:
        if not g.get("khl_id") or g["date"] < (now.date() - timedelta(days=1)).isoformat():
            return False
        if self.status(g, now) == "ended":
            return bool(g.get("ended_at")) and now - g["ended_at"] < ENDED_HOLD
        return self.in_window(g, now)

    def active(self, now: datetime) -> bool:
        return any(self.in_window(g, now) for g in self.games.values())

    # ---------- онлайн КХЛ ----------

    def maybe_rhl(self, item: dict) -> bool:
        """Стоит ли спрашивать страницу матча: в блоке есть название команды РХЛ или названий не видно.
        «ЦСКА — Спартак» не спрашиваем; «Молот — Рязань-ВДВ» спросим один раз — лига запомнится."""
        if mentions_team(self.names, item.get("text") or ""):
            return True
        return not (item.get("home") and item.get("away"))

    async def league_of(self, item: dict, now: datetime) -> tuple[dict | None, dict | None]:
        """Лига матча по заголовку его страницы, с кэшем навсегда в live/khl_ids.json."""
        kid = item["khl_id"]
        if (info := self.khl_ids.get(str(kid))) is not None:
            return info, None
        if not self.maybe_rhl(item) or self.title_retry.get(kid, now) > now:
            return None, None
        html = await self.fetch(khl_online.match_url(kid))
        parsed = khl_online.parse_match(html, self.teams, kid)
        title = parsed.get("title")
        if not title:
            self.title_retry[kid] = now + TITLE_RETRY
            self.warn_once(SRC_ONLINE, f"заголовок страницы {kid} не разобран")
            return None, None
        info = {k: title.get(k) for k in ("league", "stage", "n", "date", "home", "away", "pair")}
        info["rhl"] = khl_online.is_rhl(title.get("league"))
        self.khl_ids[str(kid)] = info
        write_json(self.out / "khl_ids.json", self.khl_ids)
        logging.info("онлайн %s: %s, %s", kid, info["league"], info["pair"])
        return info, parsed

    def apply_page(self, g: dict, parsed: dict, seen: datetime) -> None:
        page = {"seen": seen, **{k: parsed[k] for k in ("status", "period", "clock", "score") if k in parsed}}
        old = g.get("page") or {}
        page["events"] = parsed.get("events") or old.get("events") or []
        if old.get("status") != page.get("status") and page.get("status"):
            logging.info("%s: %s → %s", g["key"], old.get("status") or "—", page["status"])
        if page.get("status") == "ended":
            g["ended_at"] = g.get("ended_at") or seen
        else:
            g["ended_at"] = None
        g["page"] = page

    async def poll_list(self, url: str, now: datetime, expect: date | None = None) -> None:
        self.notes[SRC_ONLINE] = []
        html = await self.fetch(url)
        day = khl_online.parse_day_date(html)
        if expect is not None and day != expect:
            raise ValueError(f"список на {expect} не узнан: на странице {day}")
        day = day or now.date()
        items = khl_online.parse_day_list(html, self.teams)
        found = 0
        for item in items:
            info, parsed = await self.league_of(item, now)
            if not info or not info.get("rhl"):
                continue
            home, away = self.team_ids(info.get("home"), info.get("away"), info.get("pair"))
            if not home or not away:
                self.warn_once(SRC_ONLINE, f"не сопоставлено: {info.get('pair')} ({item['khl_id']})")
                continue
            seen = self.clock()
            g = self.game(info.get("date") or day.isoformat(), home, away)
            g["khl_id"] = item["khl_id"]
            started = self.status(g, now) in ("live", "break", "ended") or item.get("status") in ("live", "break", "ended")
            if item.get("time") and not (started and g.get("start_src") == SRC_ONLINE):
                h, m = map(int, item["time"].split(":"))
                g["start"] = datetime.combine(date.fromisoformat(g["date"]), time(h, m), tzinfo=TZ)
                g["start_src"] = SRC_ONLINE
            g["list"] = {"seen": seen, "status": item.get("status"), "score": item.get("score")}
            if parsed:
                self.apply_page(g, parsed, seen)
                self.last_page[g["key"]] = seen
            found += 1
        note = "; ".join(self.notes.get(SRC_ONLINE, [])) or ("" if items else "в списке дня нет ссылок на матчи")
        self.ok(SRC_ONLINE, found, note)

    async def poll_page(self, g: dict) -> None:
        self.last_page[g["key"]] = self.clock()   # и при ошибке: следующая попытка — через PAGE_EVERY
        if g.get("site_t"):
            return await self.poll_site_page(g)
        html = await self.fetch(khl_online.match_url(g["khl_id"]))
        parsed = khl_online.parse_match(html, self.teams, g["khl_id"])
        seen = self.clock()
        if not parsed.get("status") and not parsed.get("title"):
            raise ValueError(f"страница матча {g['khl_id']} не разобрана")
        if not parsed.get("status"):
            self.warn_once(SRC_ONLINE, f"на странице {g['khl_id']} не найден статус матча")
        self.apply_page(g, parsed, seen)

    # ---------- сайт лиги: rhl.fhr.ru (rhl_site.py) ----------

    def site_url(self, t: int, gid: int, tab: str = "") -> str:
        return f"{self.site}/matchcenter/{t}/{gid}/{tab}"

    @staticmethod
    def site_state(status: str | None, score, status_name: str | None, seen: datetime) -> dict:
        """Статус и счёт сайта → состояние матча службы. «Сыгран» на сайте — «окончен»: протокол
        и итог — дело сборки Pages."""
        st = rhl_site.live_state({"status": "live", "score": score, "status_name": status_name}) \
            if status == "live" and isinstance(score, list) else None
        out = {"seen": seen, "status": None, "period": None, "clock": None, "score": None, "src": "site"}
        if st:
            out.update(status=st["status"], period=st["period"], score=st["score"])
        elif status == "final" and isinstance(score, list):
            out.update(status="ended", score={"home": score[0], "away": score[1], "decision": None})
        elif status in ("moved", "off"):
            out["status"] = status
        return out

    async def poll_site_page(self, g: dict) -> None:
        html = await self.fetch(self.site_url(g["site_t"], g["khl_id"]))
        p = rhl_site.parse_match(html)
        if not p:
            raise ValueError(f"страница матча {g['khl_id']} на сайте лиги не разобрана")
        seen = self.clock()
        state = self.site_state(p["status"], p["score"], p.get("status_name"), seen)
        if p["status"] == "final" and p.get("decision") and state.get("score"):
            state["score"]["decision"] = p["decision"]
        old = g.get("page") if (g.get("page") or {}).get("src") == "site" else None
        events = [*((old or {}).get("events") or []), *site_events(old, state, g.get("site_period"), seen)]
        events = with_authors(events, p.get("goals"), self.hidden)[-khl_online.MAX_EVENTS:]
        if state.get("period"):
            g["site_period"] = state["period"]
        self.apply_page(g, {**{k: state[k] for k in ("status", "period", "clock", "score")}, "events": events}, seen)
        g["page"]["src"] = "site"
        self.ok(self.site_src, len(self.calendar), self._src(self.site_src).get("note", ""))

    async def take_site_calendar(self, rows: list[dict]) -> None:
        """Календарь и лента дней сайта лиги → календарь службы. У сыгранных и идущих сегодня матчей
        в ленте нет даты — она берётся со страницы матча, один раз на матч."""
        fresh, seen = {}, self.clock()
        asked = 0
        for r in rows:
            start = parse_iso(r.get("start"))
            if start is None and r["id"] in self.site_starts:
                start = self.site_starts[r["id"]]
            if start is None and asked < 10 and r.get("status") in ("live", "final"):
                asked += 1
                try:
                    p = rhl_site.parse_match(await self.fetch(self.site_url(r["t"], r["id"])))
                except asyncio.CancelledError:
                    raise
                except Exception as e:   # noqa: BLE001 — одна страница не валит календарь
                    self.warn_once(self.site_src, f"страница матча {r['id']} не открылась: {e}")
                    p = None
                else:
                    self.site_starts[r["id"]] = parse_iso(p.get("start")) if p else None
                start = parse_iso(p.get("start")) if p else None
                if p and not r.get("home"):
                    r = {**r, "home": p["home"], "away": p["away"]}
            if start is None:
                continue
            home, away = self.team_ids(r.get("home"), r.get("away"))
            if not home or not away:
                self.warn_once(self.site_src, f"не сопоставлено: {r.get('home')} — {r.get('away')} ({r['id']})")
                continue
            start = start.astimezone(TZ)
            key = f"{start.date().isoformat()}|{home}|{away}"
            self.tours[r["id"]] = r["t"]
            fresh[key] = {"key": key, "date": start.date().isoformat(), "home": home, "away": away, "start": start,
                          "khl_id": r["id"], "t": r["t"], "seen": seen,
                          "feed": self.site_state(r.get("status"), r.get("score"), None, seen)}
        if not fresh:
            raise ValueError("на странице календаря сайта лиги нет матчей")
        self.calendar = fresh
        self.ok(self.site_src, len(fresh), "; ".join(self.notes.get(self.site_src, [])) or f"матчей {len(fresh)}")

    # ---------- сайт лиги: старый движок (league.py) ----------

    def calendar_start(self, row: dict, home: str) -> datetime | None:
        if not row.get("time"):
            return None
        h, m = map(int, row["time"].split(":"))
        zone = TZ
        if CALENDAR_LOCAL and self.tz.get(home):
            try:
                zone = ZoneInfo(self.tz[home])
            except (KeyError, ValueError):
                logging.warning("пояс %s у %s не узнан", self.tz[home], home)
        return datetime.combine(date.fromisoformat(row["date"]), time(h, m), tzinfo=zone).astimezone(TZ)

    async def poll_calendar(self, now: datetime) -> None:
        self.notes[self.site_src] = []
        html = await self.fetch(f"{self.site}/calendar/")
        site_rows = rhl_site.parse_calendar(html)
        if site_rows:
            return await self.take_site_calendar(site_rows)
        tours = league.parse_tournaments(html)
        regular = [i for i, name in tours if "Регулярный" in name] or [i for i, _ in tours]
        tour = regular[0] if regular else None
        if tour is not None:
            html = await self.fetch(f"{self.site}/calendar/{tour}/")
            for gid in league.parse_game_ids(html, tour):
                self.tours[gid] = tour
        rows = league.parse_calendar_times(html, tour)
        if not rows and tour is None:
            raise ValueError("календарь не узнан: нет списка турниров и матчей")
        fresh, seen = {}, self.clock()
        for row in rows:
            home, away = self.team_ids(row["home"], row["away"])
            if not home or not away:
                self.warn_once(self.site_src, f"не сопоставлено: {row['home']} — {row['away']} {row['date']}")
                continue
            key = f"{row['date']}|{home}|{away}"
            if row.get("idgame") and tour is not None:
                self.tours[row["idgame"]] = tour
            fresh[key] = {"key": key, "date": row["date"], "home": home, "away": away,
                          "start": self.calendar_start(row, home), "khl_id": row.get("idgame"), "seen": seen}
        self.calendar = fresh
        timed = sum(1 for c in fresh.values() if c["start"])
        note = "; ".join(self.notes[self.site_src]) or ("" if rows else f"в календаре турнира {tour} нет будущих матчей")
        self.ok(self.site_src, len(fresh), note or f"турнир {tour}, со временем {timed}")

    def merge_calendar(self, now: datetime) -> None:
        """Сегодняшние и завтрашние матчи календаря — в матчи дня: время, если онлайн его не дал."""
        days = {(now.date() + timedelta(days=i)).isoformat() for i in (0, 1)}
        for c in self.calendar.values():
            if c["date"] not in days:
                continue
            g = self.game(c["date"], c["home"], c["away"])
            if c["start"] and g.get("start_src") != SRC_ONLINE:
                g["start"], g["start_src"] = c["start"], self.site_src
            g["khl_id"] = g.get("khl_id") or c.get("khl_id")
            g["cal_seen"] = c["seen"] or g.get("cal_seen")
            if c.get("t"):
                g["site_t"] = c["t"]
            feed = c.get("feed")
            if feed and feed.get("status") and (not g.get("list") or g["list"]["seen"] < feed["seen"]):
                g["list"] = feed

    # ---------- проход ----------

    def list_every(self, now: datetime) -> timedelta:
        return LIST_FAST if self.active(now) else LIST_SLOW

    def calendar_every(self, now: datetime) -> timedelta:
        """Календарь сайта лиги: раз в 6 часов, а в игровой день, пока идёт или скоро матч, — чаще:
        в его ленте счёт сыгранных матчей и дата идущих."""
        return SITE_EVERY if self.active(now) else CALENDAR_EVERY

    def due(self, last: datetime | None, every: timedelta, now: datetime) -> bool:
        return last is None or now - last >= every

    async def guarded(self, src: str, coro) -> bool:
        try:
            await coro
            return True
        except asyncio.CancelledError:
            raise
        except Exception as e:   # noqa: BLE001 — один источник не валит службу
            self.fail(src, e)
            return False

    async def step(self, force: bool = False) -> bool:
        """Один проход: то, что пора спросить. False — ночная тишина, ничего не спрашивали."""
        now = self.clock()
        if quiet(now) and not force and not self.active(now):
            return False
        if self.site and (force or self.due(self.last_cal, self.calendar_every(now), now)):
            self.last_cal = now
            await self.guarded(self.site_src, self.poll_calendar(now))
        self.merge_calendar(now)
        if (force or self.due(self.last_list, self.list_every(now), now)) and not self.online_blocked(now):
            self.last_list = now
            await self.guarded(SRC_ONLINE, self.poll_list(khl_online.DAY_URL, now))
        if self.tomorrow_url and not self.online_blocked(now) and (force or self.due(self.last_tomorrow, LIST_SLOW, now)):
            self.last_tomorrow = now
            tomorrow = now.date() + timedelta(days=1)
            url = self.tomorrow_url.format(date=tomorrow.isoformat(), dmy=tomorrow.strftime("%d.%m.%Y"))
            await self.guarded(SRC_ONLINE, self.poll_list(url, now, expect=tomorrow))
        for g in sorted(self.games.values(), key=lambda g: g["key"]):
            now = self.clock()
            if self.needs_page(g, now) and self.due(self.last_page.get(g["key"]), PAGE_EVERY, now):
                if g.get("site_t"):
                    await self.guarded(self.site_src, self.poll_page(g))
                elif not self.online_blocked(now):
                    await self.guarded(SRC_ONLINE, self.poll_page(g))
        self.write(self.clock())
        return True

    def next_delay(self, now: datetime) -> float:
        """Сколько спать до следующего дела, в секундах."""
        if quiet(now) and not self.active(now):
            wake = datetime.combine(now.date(), DAY_START, tzinfo=TZ)
            return max(1.0, min((wake - now).total_seconds(), 900.0))
        waits = [(self.last_list + self.list_every(now) - now) if self.last_list else timedelta(0)]
        if self.site:
            waits.append((self.last_cal + self.calendar_every(now) - now) if self.last_cal else timedelta(0))
        for g in self.games.values():
            if self.needs_page(g, now):
                last = self.last_page.get(g["key"])
                waits.append(last + PAGE_EVERY - now if last else timedelta(0))
        return max(1.0, min(min(waits).total_seconds(), 600.0))

    # ---------- вывод (ADR-019, раздел 5) ----------

    def render(self, g: dict, now: datetime) -> dict:
        start = g.get("start")
        st = self.page_state(g)
        status = st["status"] if st and st.get("status") in LIVE_STATUSES else time_status(start, now)
        period = clock = None
        if status in ("live", "break"):
            if now - st["seen"] > STALE:
                status = "live"          # «идёт матч» без минуты и периода, с последним счётом
            else:
                period = st.get("period")
                clock = st.get("clock") if status == "live" else None
        score = None
        if status in ("live", "break", "ended"):
            score = (st or {}).get("score") or (g.get("page") or {}).get("score") or (g.get("list") or {}).get("score")
        events = ((g.get("page") or {}).get("events") or [])[-khl_online.MAX_EVENTS:]
        protocol = None
        site_t = g.get("site_t")
        if status == "ended" and site_t and self.site:
            protocol = self.site_url(site_t, g["khl_id"], "protocol/")
        elif status == "ended" and g.get("khl_id") in self.tours and self.site:
            protocol = f"{self.site}/report/{self.tours[g['khl_id']]}/?idgame={g['khl_id']}"
        live_parts = [x for x in (g.get("page"), g.get("list")) if x and x.get("seen")]
        seen = [x["seen"] for x in live_parts]
        online = any(x.get("src") != "site" for x in live_parts)
        seen = max(seen) if seen else g.get("cal_seen")
        return {"key": g["key"], "date": g["date"], "home": g["home"], "away": g["away"],
                "start": iso(start), "time": start.strftime("%H:%M") if start else None,
                "status": status, "period": period, "clock": clock,
                "score": {"home": score["home"], "away": score["away"], "decision": score.get("decision")} if score else None,
                "events": events,
                "online": (self.site_url(site_t, g["khl_id"], "live/") if site_t and self.site
                           else khl_online.match_url(g["khl_id"]) if g.get("khl_id") else None),
                "khl_id": g.get("khl_id"), "tournament": site_t,
                "protocol": protocol, "seen": iso(seen),
                "src": SRC_ONLINE if online else (self.site_src if (g.get("cal_seen") or live_parts) else None)}

    def day(self, d: str, now: datetime) -> dict:
        games = [self.render(g, now) for g in self.games.values() if g["date"] == d]
        games.sort(key=lambda x: (x["start"] or "~", x["key"]))
        return {"date": d, "updated": iso(now), "games": games}

    def schedule(self, now: datetime) -> dict:
        first, last = now.date().isoformat(), (now.date() + timedelta(days=SCHEDULE_DAYS - 1)).isoformat()
        out: dict[str, dict] = {}
        for c in self.calendar.values():
            if first <= c["date"] <= last and (c["start"] or c.get("khl_id")):
                out[c["key"]] = {"key": c["key"], "date": c["date"], "home": c["home"], "away": c["away"],
                                 "start": iso(c["start"]), "time": c["start"].strftime("%H:%M") if c["start"] else None,
                                 "online": (self.site_url(c["t"], c["khl_id"], "live/") if c.get("t")
                                            else khl_online.match_url(c["khl_id"]) if c.get("khl_id") else None),
                                 "khl_id": c.get("khl_id"), "tournament": c.get("t"), "src": self.site_src}
        for g in self.games.values():
            if not (first <= g["date"] <= last) or g.get("start_src") != SRC_ONLINE and not g.get("list"):
                continue
            row = out.setdefault(g["key"], {"key": g["key"], "date": g["date"], "home": g["home"], "away": g["away"],
                                            "start": None, "time": None, "online": None, "khl_id": None})
            if g.get("start") and (g.get("start_src") == SRC_ONLINE or not row["start"]):
                row.update(start=iso(g["start"]), time=g["start"].strftime("%H:%M"))
            if g.get("khl_id"):
                row.update(online=khl_online.match_url(g["khl_id"]), khl_id=g["khl_id"])
            row["src"] = SRC_ONLINE
        games = sorted(out.values(), key=lambda x: (x["date"], x["start"] or "~", x["key"]))
        return {"updated": iso(now), "games": games}

    def write(self, now: datetime) -> None:
        today = now.date().isoformat()
        yesterday = (now.date() - timedelta(days=1)).isoformat()
        self.games = {k: g for k, g in self.games.items() if g["date"] >= yesterday}
        self.last_page = {k: v for k, v in self.last_page.items() if k in self.games}
        for d in sorted({g["date"] for g in self.games.values()} | {today}):
            data = self.day(d, now)
            body = json.dumps(data["games"], ensure_ascii=False, sort_keys=True)
            if d == today or self.written.get(d) != body:
                write_json(self.out / f"{d}.json", data)
                self.written[d] = body
            if d == today:
                write_json(self.out / "today.json", data)
        write_json(self.out / "schedule.json", self.schedule(now))
        for name in (SRC_ONLINE, self.site_src) if self.site else (SRC_ONLINE,):
            self._src(name)
        write_json(self.out / "sources.json", self.sources)

    def restore(self) -> None:
        """После перезапуска: сегодняшние матчи из live/<дата>.json и календарь из schedule.json."""
        now = self.clock()
        for d in (now.date() - timedelta(days=1), now.date()):
            for x in read_json(self.out / f"{d.isoformat()}.json", {}).get("games", []):
                if not all(x.get(k) for k in ("key", "date", "home", "away")):
                    continue
                g = self.game(x["date"], x["home"], x["away"])
                g["start"] = parse_iso(x.get("start"))
                g["start_src"] = x.get("src") if g["start"] else None
                g["khl_id"] = x.get("khl_id")
                g["site_t"] = x.get("tournament")
                seen = parse_iso(x.get("seen"))
                if x.get("src") == SRC_ONLINE and seen:
                    g["list"] = {"seen": seen, "status": None, "score": None}
                elif seen:
                    g["cal_seen"] = seen
                if x.get("src") == SRC_ONLINE and seen and x.get("status") in LIVE_STATUSES:
                    g["page"] = {"seen": seen, "status": x["status"], "period": x.get("period"),
                                 "clock": x.get("clock"), "score": x.get("score"), "events": x.get("events") or []}
                    g["ended_at"] = seen if x["status"] == "ended" else None
                elif x.get("src") == self.site_src and seen and x.get("status") in LIVE_STATUSES:
                    # сайт лиги: последний счёт — точка отсчёта голов, лента событий — не пропадает
                    events = x.get("events") or []
                    g["page"] = {"seen": seen, "status": x["status"], "period": x.get("period"), "clock": None,
                                 "score": x.get("score"), "events": events, "src": "site"}
                    g["site_period"] = x.get("period") or next((e["period"] for e in reversed(events)
                                                                if isinstance(e, dict) and e.get("period")), None)
                    g["ended_at"] = seen if x["status"] == "ended" else None
        for x in read_json(self.out / "schedule.json", {}).get("games", []):
            if x.get("src") == self.site_src and x.get("key"):
                self.calendar[x["key"]] = {"key": x["key"], "date": x["date"], "home": x["home"], "away": x["away"],
                                           "start": parse_iso(x.get("start")), "khl_id": x.get("khl_id"),
                                           "t": x.get("tournament"), "seen": None}

# ---------- сеть ----------


class Fetcher:
    """Запросы по одному с паузой league.PAUSE между ними (правило CLAUDE.md)."""

    def __init__(self, session: aiohttp.ClientSession, pause: float = league.PAUSE):
        self.session, self.pause, self.last = session, pause, None

    async def get(self, url: str) -> str:
        loop = asyncio.get_running_loop()
        if self.last is not None and (wait := self.pause - (loop.time() - self.last)) > 0:
            await asyncio.sleep(wait)
        try:
            async with self.session.get(url) as r:
                r.raise_for_status()
                return khl_online.decode_html(await r.read(), r.headers.get("Content-Type", ""))
        finally:
            self.last = loop.time()


def session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(headers={"User-Agent": league.USER_AGENT}, timeout=aiohttp.ClientTimeout(total=30),
                                 trust_env=True)


async def serve(out_dir: Path, site: str, clock, once: bool, tomorrow_url: str | None) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    async with session() as s:
        live = Live(out_dir, build_data.load_teams(), Fetcher(s).get, clock, site=site, tomorrow_url=tomorrow_url)
        if once:
            await live.step(force=True)
            return
        logging.info("live: опрос онлайна и %s → %s", site or "без сайта лиги", out_dir)
        while not stop.is_set():
            task = asyncio.create_task(live.step())
            waiter = asyncio.create_task(stop.wait())
            done, _ = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
            waiter.cancel()
            if task not in done:          # SIGTERM посреди прохода: не ждём ответа сайта
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                break
            if task.exception():
                logging.error("проход упал", exc_info=task.exception())
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), live.next_delay(clock()))
        with contextlib.suppress(Exception):
            live.write(clock())
        logging.info("live: остановлена")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Служба live: онлайн КХЛ и календарь сайта РХЛ → live/*.json (ADR-019)")
    ap.add_argument("--once", action="store_true", help="один проход и выход, без ночной тишины")
    ap.add_argument("--now", type=datetime.fromisoformat, help="«сейчас» с поясом, например 2026-10-03T16:50+03:00")
    ap.add_argument("--dir", type=Path, default=LIVE_DIR, help=f"каталог вывода, по умолчанию {LIVE_DIR}")
    ap.add_argument("--site", default=os.environ.get("LEAGUE_SITE") or LEAGUE_SITE,
                    help="сайт лиги для календаря (переменная LEAGUE_SITE); пусто — без календаря")
    ap.add_argument("--tomorrow-url", default=os.environ.get("ONLINE_TOMORROW_URL") or None,
                    help="адрес списка онлайна на завтра с {date} (2026-10-04) или {dmy} (04.10.2026), "
                         "если probe его нашёл (переменная ONLINE_TOMORROW_URL)")
    args = ap.parse_args()
    if args.now and args.now.tzinfo is None:
        ap.error("--now нужен с поясом: 2026-10-03T16:50+03:00")
    shift = (args.now - datetime.now(TZ)) if args.now else timedelta(0)

    def clock() -> datetime:
        return datetime.now(TZ) + shift
    asyncio.run(serve(args.dir, args.site, clock, args.once, args.tomorrow_url))


if __name__ == "__main__":
    main()

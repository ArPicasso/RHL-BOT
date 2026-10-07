"""Пульт админа (ADR-021): счётчики служб по дням, статус системы и сводка проблем. Только stdlib.

Службы копят счётчики в `Tracker` и пишут `status/<служба>.json` (каталог `STATUS_DIR`, на сервере
`/opt/rhl/status`): бот — `bot.json`, служба pages — `pages.json`, служба clips — `clips.json`. Сервер API
собирает из них, из `live/`, `subscribers.json`, своей базы и `systemctl` один ответ `GET /api/admin/status` —
`build_status`, а `problems` превращает его в список «что сломано».

Ни id, ни имён людей в счётчиках нет. Открытия мини-аппа сервер считает в `AdminStore`: id там
живёт до конца вчерашнего дня и только чтобы не посчитать человека дважды (ADR-021, раздел 5).
"""
import json
import logging
import os
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
TZ = ZoneInfo("Europe/Moscow")
STATUS_DIR = Path(os.environ.get("STATUS_DIR") or BASE / "status")
KEEP_DAYS = 35          # счётчики по дням храним пять недель
COHORT_DAYS = 7         # на какой день смотрим возвраты: пришёл неделю назад — вернулся ли
LOG_KEEP = 10           # последних рассылок в журнале службы
WEEK = 7                # дней на пульте

# службы systemd на сервере (deploy/) и как их называть на пульте
UNITS = {"bot": "Бот", "live": "Живое", "api": "API", "pages": "Сборка Pages", "clips": "Клипы голов",
         "cuts": "Нарезка видео", "tg-tunnel": "Туннель в Telegram", "caddy": "HTTPS (Caddy)"}
# задания GitHub Actions, за которыми следит служба pages
WORKFLOWS = {"pages.yml": "Мини-апп", "deploy.yml": "Выложить бота", "tests.yml": "Тесты"}

# пороги проблем (ADR-021, раздел 4)
BEAT_STALE = timedelta(minutes=3)
PAGES_STALE = timedelta(minutes=90)
LEAGUE_STALE = timedelta(hours=2)
SOURCE_ERRORS = 3
BLOCKED_HOURS = 6   # live.BLOCKED_PAUSE: admin только stdlib и live не импортирует
UNBLOCK = {"online.khl.ru": "письмо на access_deny@khl.ru"}   # адрес — со страницы 403 онлайна КХЛ
DISK_LOW = 1 << 30
CLIPS_STALE = timedelta(hours=1)   # служба clips пишет пульс после каждого матча и прохода (ADR-030, раздел 7)
VK_FAILS = 3        # столько раз за день VK не отдал запись и ни разу не отдал — тревога: обычно чинит новый yt-dlp
CUTS_STALE = timedelta(minutes=20)   # служба cuts пишет пульс раз в минуту, перед и после каждого видео (ADR-036);
                                     # одно видео — до двух заходов по cuts.TIMEOUT (5 мин) и ffprobe
CUT_FAILS = 3       # столько видео за день не вырезалось, и после них ни одно не вышло — тревога
INV_SHOW = 5        # сломанных инвариантов прохода в тревоге (ADR-034). Не меньше, чем служба их присылает
                    # (clips.INV_MAX): отрезанный пришёл бы как починенный, а потом снова как новая поломка
GONE_SHOW = 8       # удалённых записей в тревоге: о каждой — своя причина, чтобы «починилось» пришло по своей.
                    # Не меньше, чем служба их присылает (clips.GONE_MAX): отрезанная причина исчезла бы из тревог
                    # как починенная, а потом пришла бы снова как новая поломка
# что служба clips считает о каталоге голов (`Tracker.gauge`), плитки пульта — в «Рассылках»
CLIPS_GAUGES = ("goals", "timed", "timed_auto", "timed_admin", "clips", "ask", "no_video", "mismatch", "no_board",
                "boards", "m_total", "m_full", "m_none", "g_replay", "run", "two")
NIGHT_FROM, NIGHT_TO = 2, 7   # с 2:00 до 7:00 МСК сборку не будят (pages_kick.py) — не тревожимся

STATE_WORDS = {"inactive": "остановлена", "failed": "упала", "activating": "запускается",
               "deactivating": "останавливается", "unknown": "состояние неизвестно"}
RUN_WORDS = {"success": "успешно", "failure": "упал", "cancelled": "отменён", "timed_out": "по таймауту",
             "startup_failure": "не запустился", "in_progress": "идёт", "queued": "в очереди"}

log = logging.getLogger("admin")


def times(n: int) -> str:
    """2 раза, 5 раз, 21 раз."""
    return "раза" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "раз"


def now_msk() -> datetime:
    return datetime.now(TZ)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(TZ).isoformat(timespec="seconds") if dt else None


def parse_iso(v) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=TZ)


def write_atomic(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def no_ids(text: str) -> str:
    """Числа от пяти цифр — chat_id и Telegram id — на пульт не выводим."""
    return re.sub(r"\d{5,}", "…", text)


# ---------- счётчики службы ----------

class Tracker:
    """Счётчики службы по дням МСК и пара сведений о ней. Пишет `status/<name>.json` по `flush()`:
    в памяти копит, на диск — раз в минуту и после рассылки. Не записалось — служба работает дальше."""

    def __init__(self, name: str, path: Path | None = None, clock=now_msk):
        self.name = name
        self.path = Path(path) if path else STATUS_DIR / f"{name}.json"
        self.clock = clock
        old = read_json(self.path, {})
        old = old if isinstance(old, dict) else {}
        days = old.get("days")
        self.days: dict[str, dict] = days if isinstance(days, dict) else {}
        self.info_: dict = {}
        self.log_: list = old.get("log") if isinstance(old.get("log"), list) else []
        self.started = iso(clock())
        self._warned = False

    def today(self) -> dict:
        return self.days.setdefault(self.clock().date().isoformat(), {})

    def add(self, key: str, n: int = 1) -> None:
        day = self.today()
        day[key] = day.get(key, 0) + n

    def gauge(self, key: str, value) -> None:
        """Снимок за день: число подписчиков на конец дня и т. п."""
        self.today()[key] = value

    def info(self, **kw) -> None:
        self.info_.update(kw)

    def note(self, entry: dict) -> None:
        """В журнал последних событий (рассылок): новые сверху, не больше LOG_KEEP."""
        self.log_ = [{"at": iso(self.clock()), **entry}, *self.log_][:LOG_KEEP]

    def snapshot(self) -> dict:
        since = (self.clock().date() - timedelta(days=KEEP_DAYS)).isoformat()
        self.days = {d: v for d, v in self.days.items() if d > since}
        return {"name": self.name, "started": self.started, "beat": iso(self.clock()),
                "info": self.info_, "days": self.days, "log": self.log_}

    def flush(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(self.path, self.snapshot())
            self._warned = False
            return True
        except OSError as e:
            if not self._warned:
                log.warning("пульт: %s не записался: %s", self.path, e)
                self._warned = True
            return False


class ErrorCount(logging.Handler):
    """Ошибки службы за день и шаблон последней — без подставленных значений: в них chat_id."""

    def __init__(self, tracker: Tracker):
        super().__init__(logging.ERROR)
        self.tracker = tracker

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.tracker.add("errors")
            exc = record.exc_info[0].__name__ if record.exc_info and record.exc_info[0] else ""
            self.tracker.info(last_error={"at": iso(self.tracker.clock()), "what": no_ids(str(record.msg))[:200],
                                          "exc": exc})
        except Exception:   # счётчик не должен ронять журнал
            pass


# ---------- открытия мини-аппа (сервер API) ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS admin_seen (
    day  TEXT    NOT NULL,
    fan  INTEGER NOT NULL,
    PRIMARY KEY (day, fan)
);
CREATE TABLE IF NOT EXISTS admin_counts (
    day  TEXT    NOT NULL,
    key  TEXT    NOT NULL,
    n    INTEGER NOT NULL,
    PRIMARY KEY (day, key)
);
-- удержание (ADR-021, дополнение 04.10): на человека три числа — когда пришёл, когда был в
-- последний раз, сколько всего дней заходил. Дневной журнал id (admin_seen) по-прежнему живёт
-- один день: воронок по людям из этого не собрать, а «возвращаются ли» видно
CREATE TABLE IF NOT EXISTS admin_fans (
    fan   INTEGER PRIMARY KEY,
    first TEXT NOT NULL,
    last  TEXT NOT NULL,
    days  INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS admin_fans_first ON admin_fans (first);
"""
PLATFORM_RE = re.compile(r"^[a-z_]{1,20}$")


class AdminStore:
    """Сколько разных людей открыло мини-апп за день, с каких платформ и за кого болеют."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        conn.executescript(SCHEMA)

    def _bump(self, day: str, key: str) -> None:
        self.conn.execute("INSERT INTO admin_counts (day, key, n) VALUES (?, ?, 1) "
                          "ON CONFLICT (day, key) DO UPDATE SET n = n + 1", (day, key))

    def seen(self, fan: int, day: date, platform: str | None = None, fav: str | None = None) -> bool:
        """Отметить открытие. True — этот человек сегодня впервые: тогда и считаем."""
        d = day.isoformat()
        c = self.conn
        c.execute("BEGIN IMMEDIATE")
        try:
            c.execute("DELETE FROM admin_seen WHERE day < ?", ((day - timedelta(days=1)).isoformat(),))
            c.execute("DELETE FROM admin_counts WHERE day < ?", ((day - timedelta(days=KEEP_DAYS)).isoformat(),))
            new = c.execute("INSERT OR IGNORE INTO admin_seen (day, fan) VALUES (?, ?)", (d, fan)).rowcount == 1
            if new:
                c.execute("INSERT INTO admin_fans (fan, first, last) VALUES (?, ?, ?) "
                          "ON CONFLICT (fan) DO UPDATE SET last = excluded.last, days = days + 1, "
                          "first = MIN(first, excluded.first)", (fan, d, d))
                self._bump(d, "app_users")
                if platform and PLATFORM_RE.match(platform):
                    self._bump(d, f"platform:{platform}")
                if fav:
                    self._bump(d, f"fav:{fav}")
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
        return new

    def forget(self, fan: int) -> None:
        self.conn.execute("DELETE FROM admin_seen WHERE fan = ?", (fan,))
        self.conn.execute("DELETE FROM admin_fans WHERE fan = ?", (fan,))

    def retention(self, today: date) -> dict:
        """Возвращаются ли люди (ADR-021, дополнение 04.10). Наружу — только числа.

        Считается по трём числам на человека, без дневного журнала id: `first` даёт когорту,
        `last` — вернулся ли. `new` и `back` — сегодняшние: впервые и те, кто уже был раньше.
        `d1` — вчерашняя когорта новых и сколько из них зашло сегодня. `week` — когорта недели
        назад и сколько из них заходило ещё хоть раз. `sleeping` — был от трёх дней до пяти
        недель назад."""
        d = today.isoformat()
        yesterday = (today - timedelta(days=1)).isoformat()
        cohort = (today - timedelta(days=COHORT_DAYS)).isoformat()
        one = lambda sql, args=(): self.conn.execute(sql, args).fetchone()[0]   # noqa: E731
        return {
            "new": one("SELECT COUNT(*) FROM admin_fans WHERE first = ?", (d,)),
            "back": one("SELECT COUNT(*) FROM admin_fans WHERE last = ? AND first < ?", (d, d)),
            "known": one("SELECT COUNT(*) FROM admin_fans"),
            "d1": {"of": one("SELECT COUNT(*) FROM admin_fans WHERE first = ?", (yesterday,)),
                   "back": one("SELECT COUNT(*) FROM admin_fans WHERE first = ? AND last = ?", (yesterday, d))},
            "week": {"of": one("SELECT COUNT(*) FROM admin_fans WHERE first = ?", (cohort,)),
                     "back": one("SELECT COUNT(*) FROM admin_fans WHERE first = ? AND last > first", (cohort,)),
                     "days": COHORT_DAYS},
            "sleeping": one("SELECT COUNT(*) FROM admin_fans WHERE last < ? AND last >= ?",
                            ((today - timedelta(days=2)).isoformat(),
                             (today - timedelta(days=KEEP_DAYS)).isoformat())),
        }

    def counts(self, since: date) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for day, key, n in self.conn.execute("SELECT day, key, n FROM admin_counts WHERE day >= ?",
                                             (since.isoformat(),)):
            out.setdefault(day, {})[key] = n
        return out


def game_stats(conn: sqlite3.Connection, since: date, today: date) -> dict:
    """«Раскат» и «Кто победит?» по дням из state.db. Таблиц ещё нет — пусто."""
    out = {"days": {}, "raskat_players": None, "predict_top": []}
    s = since.isoformat()
    try:
        for d, n in conn.execute("SELECT date, COUNT(*) FROM raskat_results WHERE date >= ? GROUP BY date", (s,)):
            out["days"].setdefault(d, {})["raskat"] = n
        out["raskat_players"] = conn.execute("SELECT COUNT(DISTINCT fan) FROM raskat_results").fetchone()[0]
    except sqlite3.Error:
        pass
    try:
        for d, n, fans in conn.execute("SELECT date, COUNT(*), COUNT(DISTINCT fan) FROM predict_votes "
                                       "WHERE date >= ? GROUP BY date", (s,)):
            out["days"].setdefault(d, {}).update(votes=n, voters=fans)
        out["predict_top"] = [
            {"key": k, "home": h, "away": a, "votes": n}
            for k, h, a, n in conn.execute(
                "SELECT key, SUM(pick = 'home'), SUM(pick = 'away'), COUNT(*) FROM predict_votes "
                "WHERE date = ? GROUP BY key ORDER BY COUNT(*) DESC, key LIMIT 5", (today.isoformat(),))]
    except sqlite3.Error:
        pass
    return out


# ---------- systemctl ----------

SYSTEMCTL_PROPS = "Id,LoadState,ActiveState,SubState,NRestarts,ActiveEnterTimestamp"


def systemctl_args(units=UNITS) -> list[str]:
    return ["systemctl", "show", *(f"{u}.service" for u in units), "-p", SYSTEMCTL_PROPS, "--timestamp=unix"]


def parse_systemctl(text: str) -> dict[str, dict]:
    """Вывод `systemctl show` (блоки «ключ=значение» через пустую строку) → служба → состояние."""
    out = {}
    for block in re.split(r"\n\s*\n", text.strip()):
        kv = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        name = kv.get("Id", "").removesuffix(".service")
        if not name:
            continue
        since = None
        m = re.match(r"@(\d+)", kv.get("ActiveEnterTimestamp", ""))
        if m and int(m.group(1)) > 0:
            since = iso(datetime.fromtimestamp(int(m.group(1)), TZ))
        try:
            restarts = int(kv.get("NRestarts", "0"))
        except ValueError:
            restarts = 0
        state = "not-found" if kv.get("LoadState") == "not-found" else kv.get("ActiveState", "unknown")
        out[name] = {"state": state, "sub": kv.get("SubState", ""), "since": since, "restarts": restarts}
    return out


# ---------- сборки GitHub (служба pages) ----------

def summarize_runs(runs: list[dict], now: datetime, workflows=WORKFLOWS) -> list[dict]:
    """Ответ `GET /repos/…/actions/runs` → по заданию: последний запуск, последняя удача, неудачи за сутки."""
    day_ago = now - timedelta(days=1)
    out = []
    for wf, title in workflows.items():
        mine = [r for r in runs if isinstance(r, dict) and str(r.get("path", "")).endswith(f"/{wf}")]
        mine.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        last = mine[0] if mine else None
        ok = next((r for r in mine if r.get("conclusion") == "success"), None)
        recent = [r for r in mine if (parse_iso(r.get("created_at")) or day_ago) > day_ago]
        out.append({
            "id": wf.removesuffix(".yml"), "title": title,
            "status": last.get("status") if last else None,
            "conclusion": last.get("conclusion") if last else None,
            "at": iso(parse_iso(last.get("updated_at") or last.get("created_at"))) if last else None,
            "url": last.get("html_url") if last else None,
            "last_ok": iso(parse_iso(ok.get("updated_at") or ok.get("created_at"))) if ok else None,
            "runs_24h": len(recent),
            "fails_24h": sum(1 for r in recent if r.get("conclusion") in ("failure", "timed_out", "startup_failure")),
        })
    return out


# ---------- сбор пульта ----------

def subscribers_summary(raw, teams: dict[str, str]) -> tuple[int | None, list[dict]]:
    """subscribers.json → (всего, по командам). Старый формат — список chat_id «Рязани-ВДВ»."""
    if isinstance(raw, list):
        raw = {str(c): ["ryazan-vdv"] for c in raw}
    if not isinstance(raw, dict):
        return None, []
    by: dict[str, int] = {}
    for ids in raw.values():
        for t in ids if isinstance(ids, list) else []:
            by[t] = by.get(t, 0) + 1
    rows = [{"id": t, "name": teams.get(t, t), "n": n} for t, n in by.items()]
    rows.sort(key=lambda r: (-r["n"], r["name"]))
    return len(raw), rows


def match_title(key: str, teams: dict[str, str]) -> str:
    """«<дата>|<хозяева>|<гости>» → «Рязань-ВДВ — Тамбов»."""
    parts = key.split("|")
    return " — ".join(teams.get(t, t) for t in parts[1:3]) if len(parts) == 3 else key


def build_status(*, now: datetime, teams: dict[str, str], services: dict | None, services_note: str = "",
                 bot: dict | None, pages: dict | None, league_updated: str | None, live_today: dict | None,
                 sources: dict | None, raskat: dict, disk: dict | None, subs, app_counts: dict[str, dict],
                 games: dict, retention: dict | None = None, clips: dict | None = None,
                 my_players: int | None = None, cuts: dict | None = None) -> dict:
    """Один ответ пульта. Дни — последние WEEK, новые сверху: счётчики бота, открытия и игры вместе."""
    bot = bot if isinstance(bot, dict) else None
    pages = pages if isinstance(pages, dict) else None
    today = now.date()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(WEEK)]
    bot_days = (bot or {}).get("days") or {}
    game_days = games.get("days") or {}
    days = []
    for d in dates:
        row = {"date": d}
        for src in (bot_days.get(d), app_counts.get(d), game_days.get(d)):
            if isinstance(src, dict):
                row.update(src)
        days.append(row)

    def split(prefix: str, row: dict, names: dict | None = None) -> list[dict]:
        out = [{"id": k[len(prefix):], "name": (names or {}).get(k[len(prefix):], k[len(prefix):]), "n": v}
               for k, v in row.items() if k.startswith(prefix) and isinstance(v, int)]
        return sorted(out, key=lambda r: (-r["n"], r["name"]))

    week: dict[str, int] = {}
    for r in days:
        for k, v in r.items():
            if k.startswith("start:") and isinstance(v, int):
                week[k] = week.get(k, 0) + v
    total, by_team = subscribers_summary(subs, teams)
    src_rows = []
    for name, s in (sources or {}).items():
        if isinstance(s, dict):
            src_rows.append({"name": name, "ok": s.get("ok"), "fail": s.get("fail"),
                             "errors": s.get("errors", 0), "games": s.get("games", 0), "note": no_ids(str(s.get("note") or "")),
                             "blocked": s.get("blocked")})
    info = (bot or {}).get("info") or {}
    clips = clips if isinstance(clips, dict) else None
    clip_days = (clips or {}).get("days") or {}
    clip_today = clip_days.get(dates[0]) if isinstance(clip_days.get(dates[0]), dict) else {}
    # снимок каталога — с последнего дня, где служба его писала: после полуночи до первого прохода — вчерашний
    clip_last = next((clip_days[d] for d in sorted(clip_days, reverse=True)
                      if isinstance(clip_days[d], dict) and isinstance(clip_days[d].get("goals"), int)), {})
    clip_info = (clips or {}).get("info") or {}
    cuts = cuts if isinstance(cuts, dict) else None
    cut_today = ((cuts or {}).get("days") or {}).get(dates[0])
    cut_today = cut_today if isinstance(cut_today, dict) else {}
    cut_info = (cuts or {}).get("info") or {}
    status = {
        "now": iso(now),
        "system": {
            "services": [{"name": u, "title": t, **services.get(u, {"state": "unknown"})} for u, t in UNITS.items()]
            if services is not None else None,
            "services_note": services_note,
            "bot": {"beat": bot.get("beat"), "started": bot.get("started"), "tg_ok": info.get("tg_ok"),
                    "tg_fail": info.get("tg_fail"), "tg_error": info.get("tg_error")} if bot else None,
            "builds": (pages or {}).get("info", {}).get("runs"),
            "builds_at": (pages or {}).get("info", {}).get("runs_at"),
            "kick": {"ok": (pages or {}).get("info", {}).get("kick_ok"),
                     "fail": (pages or {}).get("info", {}).get("kick_fail"),
                     "error": (pages or {}).get("info", {}).get("kick_error"),
                     "token": (pages or {}).get("info", {}).get("token")} if pages else None,
            "league_updated": league_updated,
            "live": {"updated": (live_today or {}).get("updated") if isinstance(live_today, dict) else None,
                     "sources": src_rows},
            "raskat": raskat,
            "disk": disk,
            "clips": {"beat": clips.get("beat"), "started": clips.get("started"),
                      "vk_ok": clip_today.get("vk_ok", 0), "vk_fail": clip_today.get("vk_fail", 0),
                      "vk_error": clip_info.get("vk_error"), "vk_last_ok": clip_info.get("vk_ok"),
                      "gone": clip_info.get("gone"),
                      # сторожа службы (ADR-034): жив ли yt-dlp вне матчей и что в проходе не сошлось
                      "canary": clip_info.get("canary"), "invariants": clip_info.get("invariants")} if clips else None,
            # видео для админов (ADR-036): сколько вырезано и не вышло за день, очередь заданий
            "cuts": {"beat": cuts.get("beat"), "started": cuts.get("started"), "done": cut_today.get("cuts", 0),
                     "fail": cut_today.get("cut_fail", 0), "error": cut_info.get("cut_error"),
                     "ok_at": cut_info.get("cut_ok"), "fail_at": cut_info.get("cut_fail"),
                     "queue": cut_info.get("queue")} if cuts else None,
        },
        "audience": {
            "subscribers": total,
            "by_team": by_team,
            "platforms": split("platform:", days[0]),
            "fans": split("fav:", days[0], teams),
            "links": split("start:", days[0]),
            "links_week": split("start:", week),
            "retention": retention,
        },
        "sends": {"log": (bot or {}).get("log") or [], "last_error": info.get("last_error"),
                  # каталог голов (ADR-030, раздел 7): снимок службы clips на её последний проход
                  "clips": {k: clip_last.get(k) for k in CLIPS_GAUGES if isinstance(clip_last.get(k), int)}
                  if clips else None,
                  "my_players": my_players},
        "games": {"raskat_players": games.get("raskat_players"),
                  "predict_top": [{**r, "title": match_title(r.get("key", ""), teams)}
                                  for r in games.get("predict_top") or []]},
        "days": days,
    }
    status["problems"] = problems(status, now)
    return status


# ---------- вкладка «Голы» (ADR-036, раздел 4) ----------
# Где каждый матч и гол на конвейере «запись → разбор → секунды → проверка → клип». Статус гола считает служба
# clips (`coverage` в live/clips.json, поле `state` — clips.goal_states), здесь только сборка ответа: названия,
# авторы из league.json, очередь «Ждут вас» и «Сейчас» из пульса служб. Только чтение: отметки — шаг 5.

GOAL_STATES = ("clip", "ready", "done", "confirm", "dispute", "approx", "search", "absent", "stuck")   # clips.STATES
WAIT_ORDER = {"dispute": 0, "approx": 1, "confirm": 2, "search": 3}   # что ждёт человека и в каком порядке
GOALS_DAYS = 14     # матчи старше — на вкладке, только если у них что-то ждёт человека
GOALS_MATCHES = 80  # матчей в ответе
WAIT_SHOW = 60      # голов в «Ждут вас»: больше за раз не разобрать, число — полное


def _rank(score: str) -> int:
    try:
        return sum(int(x) for x in score.split(":"))
    except (AttributeError, ValueError):
        return 99


def _score(g: dict | None) -> str:
    sc = (g or {}).get("score")
    if isinstance(sc, dict) and sc.get("home") is not None and sc.get("away") is not None:
        return f"{sc['home']}:{sc['away']}"
    return ""


def build_goals(*, now: datetime, teams: dict[str, str], clips_store: dict | None, league: dict[str, dict],
                clips: dict | None, cuts: dict | None, role: str) -> dict:
    """Ответ GET /api/admin/goals. clips_store — live/clips.json, league — матчи league.json по ключу, clips и cuts —
    пульс служб (status/*.json). Людей нет: только матчи, голы и авторы из протокола — они и так у болельщиков."""
    store = clips_store if isinstance(clips_store, dict) else {}
    cov = {k: e for k, e in (store.get("coverage") or {}).items() if isinstance(e, dict) and k.count("|") == 2}
    since = (now.date() - timedelta(days=GOALS_DAYS - 1)).isoformat()
    states = dict.fromkeys(GOAL_STATES, 0)
    pipe = {"played": len(cov), "video": 0, "parsed": 0, "goals": 0, "replays": 0, "full": 0}
    matches, wait = [], []
    for key in sorted(cov, key=lambda k: (k[:10], k), reverse=True):
        e = cov[key]
        why = e.get("why")
        pipe["video"] += bool(e.get("src"))   # src у разбора покрытия есть, только когда запись нашлась
        pipe["parsed"] += bool(e.get("src")) and why != "pending"
        pipe["goals"] += int(e.get("goals") or 0)
        pipe["replays"] += int(e.get("replays") or 0)
        pipe["full"] += why == "ok"
        g = league.get(key) or {}
        protocol = {x["score"]: x for x in g.get("goals") or []
                    if isinstance(x, dict) and isinstance(x.get("score"), str) and x.get("period") != "РБ"}
        day, home, away = key.split("|")
        title = match_title(key, teams)
        rejected = e.get("rejected") or {}
        goals, counts, waits = [], {}, 0
        for score, st in (e.get("state") or {}).items():
            if st not in states:
                continue
            states[st] += 1
            counts[st] = counts.get(st, 0) + 1
            x = protocol.get(score) or {}
            row = {"score": score, "state": st, "team": x.get("team"), "period": x.get("period"),
                   "time": x.get("time"), "author": x.get("author"), "assists": x.get("assists") or []}
            if score in rejected:
                row["why"] = no_ids(str(rejected[score]))[:120]
            goals.append(row)
            if st in WAIT_ORDER:
                waits += 1
                wait.append({"key": key, "date": day, "title": title, **row})
        goals.sort(key=lambda r: _rank(r["score"]))
        if day < since and not waits:
            continue
        m = {"key": key, "date": day, "home": home, "away": away, "title": title, "score": _score(g),
             "why": why, "src": e.get("src"), "goals": int(e.get("goals") or 0),
             "replays": int(e.get("replays") or 0), "states": counts, "wait": waits, "list": goals}
        if e.get("error"):
            m["error"] = no_ids(str(e["error"]))[:200]
        matches.append(m)
    wait.sort(key=lambda r: (WAIT_ORDER[r["state"]], -int(r["date"].replace("-", "")), r["key"], _rank(r["score"])))
    clips = clips if isinstance(clips, dict) else None
    info = (clips or {}).get("info") or {}
    scan = info.get("scan") if isinstance(info.get("scan"), dict) else None
    cuts = cuts if isinstance(cuts, dict) else None
    cinfo = (cuts or {}).get("info") or {}
    boards = [{"club": c, "name": teams.get(c, c), "matches": int((b or {}).get("matches") or 0)}
              for c, b in sorted((store.get("boards") or {}).items()) if isinstance(b, dict)]
    return {
        "now": iso(now),
        "role": role,
        "updated": store.get("updated"),
        "pipeline": {**pipe, "states": states, "boards": boards},
        "work": {
            "clips": {"beat": clips.get("beat"), "started": clips.get("started"),
                      "scan": {**scan, "title": match_title(str(scan.get("key") or ""), teams)} if scan else None,
                      "waiting": info.get("waiting"), "cut": info.get("cut"), "bucket": info.get("bucket"),
                      "vk_ok": info.get("vk_ok"), "vk_fail": info.get("vk_fail"),
                      "vk_error": info.get("vk_error")} if clips else None,
            "cuts": {"beat": cuts.get("beat"), "job": cinfo.get("job"), "queue": cinfo.get("queue"),
                     "ok_at": cinfo.get("cut_ok"), "fail_at": cinfo.get("cut_fail"),
                     "error": cinfo.get("cut_error")} if cuts else None,
        },
        "wait": {"total": len(wait), "items": wait[:WAIT_SHOW]},
        "matches": matches[:GOALS_MATCHES],
    }


def _ago(at: str | None, now: datetime) -> timedelta | None:
    dt = parse_iso(at)
    return now - dt if dt else None


def _mins(td: timedelta) -> str:
    m = int(td.total_seconds() // 60)
    if m < 60:
        return f"{m} мин"
    h, m = divmod(m, 60)
    return f"{h} ч {m} мин" if h < 24 and m else f"{h} ч" if h < 24 else f"{h // 24} дн"


# ---------- тревоги админу (ADR-022) ----------

ALERTS_NAME = "alerts.json"              # его пишет служба api в STATUS_DIR, читает и разносит бот
ALERT_REPEAT = timedelta(hours=1)        # о той же причине напоминаем не чаще
ALERTS_STALE = timedelta(minutes=10)     # alerts.json старше — молчит сама служба api, и это тревога
# порядок разделов в сообщении: что горит, что ещё горит, что посмотреть, что отпустило
ALERT_GROUPS = ("broke", "still", "watch", "fixed")


def alerts_payload(problems_: list[dict], now: datetime) -> dict:
    """Что служба api пишет в status/alerts.json для бота."""
    return {"at": iso(now), "problems": problems_}


def alert_plan(problems_: list[dict], was: dict, now: datetime,
               repeat: timedelta = ALERT_REPEAT) -> tuple[dict[str, list[str]], dict]:
    """Что сказать админу и что после этого помнить (ADR-022, раздел 3).

    `was` — что уже сказано: причина → {at, level, text}. Возвращает разделы сообщения (пустые
    выброшены) и новую память. Новая причина — сразу; та же `bad` — не чаще `repeat`; `warn` —
    один раз; исчезла — «починилось» и забыли."""
    groups: dict[str, list[str]] = {g: [] for g in ALERT_GROUPS}
    state: dict[str, dict] = {}
    for p in problems_:
        key, text = p.get("key"), str(p.get("text") or "").strip()
        if not key or not text or key in state:
            continue
        level = "bad" if p.get("level") == "bad" else "warn"
        prev = was.get(key) if isinstance(was.get(key), dict) else None
        said = parse_iso((prev or {}).get("at"))
        if prev is None or (prev.get("level") != "bad" and level == "bad"):
            groups["broke" if level == "bad" else "watch"].append(text)
        elif level == "bad" and (said is None or now - said >= repeat):
            groups["still"].append(text)
        else:                                   # ещё рано напоминать или это «посмотреть»
            state[key] = {**prev, "level": level, "text": text}
            continue
        state[key] = {"at": iso(now), "level": level, "text": text}
    groups["fixed"] = [str(v.get("text") or "").strip() for k, v in was.items()
                       if k not in state and isinstance(v, dict) and str(v.get("text") or "").strip()]
    return {g: v for g, v in groups.items() if v}, state


def problems(status: dict, now: datetime) -> list[dict]:
    """Что сломано (bad) и на что посмотреть (warn) — сводка наверху пульта, пороги — ADR-021, раздел 4.

    У каждой проблемы есть `key` — причина: по ней тревоги (ADR-022) понимают, что это та же
    поломка, что минуту назад. Текст меняется («молчит 7 мин» → «8 мин»), ключ — нет."""
    out = []
    bad = lambda key, text: out.append({"level": "bad", "key": key, "text": text})   # noqa: E731
    warn = lambda key, text: out.append({"level": "warn", "key": key, "text": text})   # noqa: E731
    sysm = status.get("system") or {}
    for s in sysm.get("services") or []:
        if s.get("state") == "not-found":
            continue
        if s.get("state") not in ("active", "reloading"):
            bad(f"service:{s['name']}", f"{s['title']}: служба {STATE_WORDS.get(s.get('state'), s.get('state'))}")
        elif s.get("restarts"):
            warn(f"restarts:{s['name']}",
                 f"{s['title']}: служба падала и поднималась {s['restarts']} {times(s['restarts'])} с последней выкладки")
    if sysm.get("services") is None and sysm.get("services_note"):
        warn("services", f"Состояние служб не прочиталось: {sysm['services_note']}")
    b = sysm.get("bot")
    if b is None:
        bad("bot:beat", "Бот не пишет пульс (status/bot.json): бот не запущен или старая версия")
    else:
        age = _ago(b.get("beat"), now)
        if age is None or age > BEAT_STALE:
            bad("bot:beat", f"Бот молчит: последний пульс {_mins(age) + ' назад' if age else 'неизвестно когда'}")
        ok, fail = parse_iso(b.get("tg_ok")), parse_iso(b.get("tg_fail"))
        if fail and (not ok or fail > ok):
            bad("bot:telegram", f"Бот не достучался до Telegram: {b.get('tg_error') or 'ошибка'}. Проверь tg-tunnel")
    night = NIGHT_FROM <= now.hour < NIGHT_TO
    for run in sysm.get("builds") or []:
        if run.get("id") == "pages":
            age = _ago(run.get("last_ok"), now)
            if not night and (age is None or age > PAGES_STALE):
                last = run.get("conclusion") or run.get("status")
                bad("build:pages", f"Мини-апп не собирался {_mins(age) if age else 'давно'}: последний запуск — "
                    f"{RUN_WORDS.get(last, last or 'нет данных')}")
        elif run.get("id") == "deploy" and run.get("conclusion") == "failure":
            bad("build:deploy", "Последняя выкладка бота на сервер красная")
        elif run.get("id") == "tests" and run.get("conclusion") == "failure":
            warn("build:tests", "Последний прогон тестов красный")
    kick = sysm.get("kick")
    if kick is not None and not kick.get("token"):
        warn("kick:token", "У службы pages нет PAGES_TOKEN: сборку не будим, за сборками не следим")
    elif kick is not None:
        ok, fail = parse_iso(kick.get("ok")), parse_iso(kick.get("fail"))
        if fail and (not ok or fail > ok):
            # причина «Мини-апп не собирался»: cron GitHub теряет запуски, а разбудить сборку не выходит
            bad("kick:fail", f"Служба pages не может запустить сборку: {kick.get('error') or 'причина не записана'}")
    age = _ago(sysm.get("league_updated"), now)
    if sysm.get("league_updated") is None:
        warn("league", "league.json с Pages не прочитался")
    elif age > LEAGUE_STALE and not night:
        bad("league", f"Данные мини-аппа (league.json) собраны {_mins(age)} назад")
    for s in (sysm.get("live") or {}).get("sources") or []:
        if s.get("blocked"):
            # закрыл доступ (403) — не поломка, а данность: сказать раз, как снять, и работать без него
            warn(f"blocked:{s['name']}", f"Источник {s['name']} закрыл доступ серверу (403): спрашиваем раз в "
                 f"{BLOCKED_HOURS} ч, работаем без него. Снять: {UNBLOCK.get(s['name'], 'написать владельцу сайта')}"
                 " и приложить IP сервера")
        elif (s.get("errors") or 0) >= SOURCE_ERRORS:
            bad(f"source:{s['name']}",
                f"Источник {s['name']}: ошибок подряд — {s['errors']}. {s.get('note') or 'Без пояснения'}")
    r = sysm.get("raskat") or {}
    if r and not r.get("on"):
        bad("raskat", f"Зачёт «Раската» выключен: {r.get('note')}")
    c = sysm.get("clips")
    if c is not None:
        age = _ago(c.get("beat"), now)
        if age is None or age > CLIPS_STALE:
            bad("clips:beat", f"Служба клипов молчит {_mins(age) if age else 'неизвестно сколько'}: "
                "голы без секунд и превью. Проверь systemctl status clips")
        if (c.get("vk_fail") or 0) >= VK_FAILS and not c.get("vk_ok"):
            n = c["vk_fail"]
            word = "попытка" if n % 10 == 1 and n % 100 != 11 else "попытки" if times(n) == "раза" else "попыток"
            bad("clips:vk", f"VK сегодня не отдал ни одной записи трансляции ({n} {word}): "
                f"{c.get('vk_error') or 'ошибка не записана'}. Обычно лечит новый yt-dlp: "
                "sudo -u rhl /opt/rhl/venv/bin/pip install -U yt-dlp и systemctl restart clips")
        # «канарейка» yt-dlp (ADR-034): VK не отдал запись и вне матчей — похоже, сменился плеер, и повторов не
        # будет уже у следующего матча
        can = c.get("canary")
        if isinstance(can, dict) and can.get("ok") is False:
            why = str(can.get("error") or "ошибка не записана")[:200]
            bad("clips:canary", f"«Канарейка»: VK не отдаёт запись и когда матчей нет — {why}. Похоже, VK сменил "
                "плеер: sudo -u rhl /opt/rhl/venv/bin/pip install -U yt-dlp, потом systemctl restart clips cuts")
        # инварианты прохода (ADR-034): то, чего не может быть, — повод смотреть разбор, а не ждать жалоб
        for p in (c.get("invariants") or [])[:INV_SHOW]:
            key = str((p or {}).get("key") or "")[:60] if isinstance(p, dict) else ""
            text = str((p or {}).get("text") or "").strip()[:300] if isinstance(p, dict) else ""
            if key and text:
                bad(f"clips:inv:{key}", f"Разбор голов: {text}")
        # запись удалили из VK: повторов у матча нет, пока человек не пришлёт другую (этап 0.3 плана). Говорим раз
        # на матч (`warn`), ключ — с матчем: запись заменили — придёт «починилось»
        for e in (c.get("gone") or [])[:GONE_SHOW]:
            key = str((e or {}).get("key") or "")[:40] if isinstance(e, dict) else ""
            if not key:
                continue
            what = str(e.get("title") or key)[:80]
            warn(f"clips:gone:{key}", f"Записи матча {what} больше нет в VK ({e.get('video') or 'ссылка не записана'}): "
                 "повторов у его голов не будет. Найди другую запись (канал клуба) и пришли ссылку в /replay")
    k = sysm.get("cuts")
    if k is not None:
        age = _ago(k.get("beat"), now)
        if age is None or age > CUTS_STALE:
            bad("cuts:beat", f"Служба нарезки видео молчит {_mins(age) if age else 'неизвестно сколько'}: превью голов "
                "админам не режутся. Проверь systemctl status cuts")
        ok, fail, n = parse_iso(k.get("ok_at")), parse_iso(k.get("fail_at")), k.get("fail") or 0
        if n >= CUT_FAILS and fail and (not ok or fail > ok):
            bad("cuts:fail", f"Нарезка видео не выходит: сегодня не вырезалось {n} видео, после них — ни одного. "
                f"Последняя ошибка: {k.get('error') or 'не записана'}. Если отказывает VK — обычно лечит новый yt-dlp: "
                "sudo -u rhl /opt/rhl/venv/bin/pip install -U yt-dlp и systemctl restart cuts")
    d = sysm.get("disk") or {}
    if d.get("free") is not None and d["free"] < DISK_LOW:
        bad("disk", f"На диске меньше 1 ГБ: {d['free'] // (1 << 20)} МБ")
    return out

"""«Кто победит?» — голоса болельщиков за матч, доли трибуны и личный итог (ADR-020).

Хранилище — SQLite (`state.db`, рядом с зачётом «Раската»), HTTP — `server.py`. Здесь же правила
матча: открыт ли приём и чем матч кончился. Что известно о матче, сервер собирает из трёх мест:
`live/<дата>.json`, `live/schedule.json` и опубликованного `league.json` (ADR-019, раздел 5).

Хранится только id пользователя Telegram, ключ матча, выбор и время. Имени нет (CLAUDE.md, правило 4).
"""
import re
import sqlite3
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Moscow")
PICKS = ("home", "away")

# Ключ матча — `<дата>|<хозяева>|<гости>`, id команд из teams.json (ADR-019, раздел 5)
KEY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\|([a-z0-9-]{1,40})\|([a-z0-9-]{1,40})$")

# Матч начался, кончился, перенесён или отменён — голос больше не принимаем
CLOSED = {"live", "break", "ended", "final", "moved", "off"}
# Итог по живому источнику — только после финальной сирены (ADR-012, раздел 1)
DONE = {"ended", "final"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS predict_votes (
    fan   INTEGER NOT NULL,
    key   TEXT    NOT NULL,
    date  TEXT    NOT NULL,
    pick  TEXT    NOT NULL CHECK (pick IN ('home', 'away')),
    at    TEXT    NOT NULL,
    PRIMARY KEY (fan, key)
);
CREATE INDEX IF NOT EXISTS predict_votes_key ON predict_votes (key);
CREATE INDEX IF NOT EXISTS predict_votes_date ON predict_votes (date);
"""


def parse_key(key) -> tuple[str, str, str] | None:
    """Ключ на дату и команды; кривой ключ — None. Дата проверяется календарём, а не только видом."""
    m = KEY_RE.match(key) if isinstance(key, str) else None
    if not m or m.group(2) == m.group(3):
        return None
    try:
        date.fromisoformat(m.group(1))
    except ValueError:
        return None
    return m.group(1), m.group(2), m.group(3)


def key_of(g: dict) -> str | None:
    """Ключ матча из строки любого файла: `key`, если есть, иначе из даты и команд."""
    if not isinstance(g, dict):
        return None
    if isinstance(g.get("key"), str):
        return g["key"]
    if all(isinstance(g.get(k), str) for k in ("date", "home", "away")):
        return f"{g['date']}|{g['home']}|{g['away']}"
    return None


# ---------- матч: время, приём, итог ----------

def _start(g: dict | None, day: str) -> datetime | None:
    """Начало матча с поясом: `start` (ISO), иначе `time` («17:00», по Москве) в день матча."""
    if not g:
        return None
    s = g.get("start")
    if isinstance(s, str) and s:
        try:
            t = datetime.fromisoformat(s)
        except ValueError:
            t = None
        if t is not None:
            return t if t.tzinfo else t.replace(tzinfo=TZ)
    hm = g.get("time")
    if isinstance(hm, str) and re.fullmatch(r"\d{1,2}:\d{2}", hm):
        h, m = (int(x) for x in hm.split(":"))
        if h < 24 and m < 60:
            return datetime.combine(date.fromisoformat(day), time(h, m), TZ)
    return None


def winner(score) -> str | None:
    """Кто выиграл по счёту. Ничьей в РХЛ нет: равный счёт — матч ещё не кончился."""
    if not isinstance(score, dict):
        return None
    h, a = score.get("home"), score.get("away")
    if not isinstance(h, int) or not isinstance(a, int) or isinstance(h, bool) or isinstance(a, bool) or h == a:
        return None
    return "home" if h > a else "away"


def merge(key: str, live: dict | None = None, sched: dict | None = None,
          league: dict | None = None) -> dict | None:
    """Что известно о матче: начало, статус, итог. Нет ни в одном источнике — None.

    Начало — сначала живой файл дня, потом расписание, потом `league.json`. Итог — протокол
    лиги (счёт в `league.json`), потом онлайн после финальной сирены (ADR-019, раздел 2)."""
    parsed = parse_key(key)
    if parsed is None or not (live or sched or league):
        return None
    day, home, away = parsed
    status = live.get("status") if live else None
    start = _start(live, day) or _start(sched, day) or _start(league, day)
    result = winner(league.get("score")) if league else None
    if result is None and live and status in DONE:
        result = winner(live.get("score"))
    return {"key": key, "date": day, "home": home, "away": away,
            "start": start, "status": status, "result": result}


def is_open(game: dict, now: datetime) -> bool:
    """Приём открыт до начала матча. Время неизвестно — до конца дня матча по Москве."""
    if now.tzinfo is None:
        raise ValueError("нужно время с поясом")
    if game.get("result") or game.get("status") in CLOSED:
        return False
    if game.get("start"):
        return now < game["start"]
    return now.astimezone(TZ).date() <= date.fromisoformat(game["date"])


def shares(home: int, away: int) -> tuple[int, int]:
    """Доли в процентах, целые, в сумме 100. Голосов нет — оба 0."""
    votes = home + away
    if votes <= 0:
        return 0, 0
    h = (200 * home + votes) // (2 * votes)       # округление половины вверх, без float
    return h, 100 - h


def tally(home: int, away: int, me: str | None, open_: bool, result: str | None) -> dict:
    """Tally из ADR-020, раздел 3. Доли отдаём и до своего голоса: прячет их мини-апп."""
    h, a = shares(home, away)
    return {"home": h, "away": a, "votes": home + away, "me": me,
            "open": bool(open_), "result": result}


def record(picks) -> dict:
    """Личный итог «угадано 5 из 8» и серия угаданных подряд — по матчам с известным итогом.

    `picks` — пары (матч из `merge`, выбор). Серия считается от последнего сыгранного матча назад."""
    done = [(g, p) for g, p in picks if g and g.get("result")]
    done.sort(key=lambda gp: (gp[0]["date"], gp[0]["start"].isoformat() if gp[0].get("start") else "",
                              gp[0]["key"]))
    right = sum(1 for g, p in done if g["result"] == p)
    streak = 0
    for g, p in reversed(done):
        if g["result"] != p:
            break
        streak += 1
    return {"right": right, "of": len(done), "streak": streak}


# ---------- хранилище ----------

class PredictStore:
    """Голоса в SQLite. Один голос на матч, менять можно, пока открыт приём (это решает сервер)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.executescript(SCHEMA)

    def vote(self, fan: int, key: str, pick: str, now: datetime) -> None:
        if pick not in PICKS:
            raise ValueError("выбор — home или away")
        day = parse_key(key)[0]
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            self.conn.execute(
                "INSERT INTO predict_votes (fan, key, date, pick, at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (fan, key) DO UPDATE SET pick = excluded.pick, at = excluded.at",
                (fan, key, day, pick, now.astimezone(TZ).isoformat(timespec="seconds")))

    def counts(self, key: str) -> tuple[int, int]:
        rows = dict(self.conn.execute(
            "SELECT pick, COUNT(*) FROM predict_votes WHERE key = ? GROUP BY pick", (key,)).fetchall())
        return rows.get("home", 0), rows.get("away", 0)

    def day_counts(self, day: str) -> dict[str, tuple[int, int]]:
        """Голоса за все матчи дня: ключ → (за хозяев, за гостей)."""
        out: dict[str, list[int]] = {}
        for key, pick, n in self.conn.execute(
                "SELECT key, pick, COUNT(*) FROM predict_votes WHERE date = ? GROUP BY key, pick", (day,)):
            out.setdefault(key, [0, 0])[0 if pick == "home" else 1] = n
        return {k: (v[0], v[1]) for k, v in out.items()}

    def pick(self, fan: int | None, key: str) -> str | None:
        if fan is None:
            return None
        row = self.conn.execute("SELECT pick FROM predict_votes WHERE fan = ? AND key = ?",
                                (fan, key)).fetchone()
        return row[0] if row else None

    def voted(self, key: str) -> set[int]:
        """Кто уже голосовал за этот матч: по ним зов на прогноз не идёт (ADR-023, раздел 3)."""
        return {r[0] for r in self.conn.execute("SELECT fan FROM predict_votes WHERE key = ?", (key,))}

    def picks(self, fan: int | None, day: str | None = None) -> dict[str, str]:
        """Голоса болельщика: ключ → выбор. С датой — только за этот день."""
        if fan is None:
            return {}
        if day is None:
            rows = self.conn.execute("SELECT key, pick FROM predict_votes WHERE fan = ?", (fan,))
        else:
            rows = self.conn.execute("SELECT key, pick FROM predict_votes WHERE fan = ? AND date = ?",
                                     (fan, day))
        return dict(rows.fetchall())

    def forget(self, fan: int) -> int:
        """Стереть все голоса болельщика (CLAUDE.md, правило 4). Возвращает, сколько стёрто."""
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            return self.conn.execute("DELETE FROM predict_votes WHERE fan = ?", (fan,)).rowcount

"""Зачёт «Раската» на сервере: результаты дней, настройки болельщика и коды дуэлей (ADR-018).

SQLite (`state.db`), без ORM. Путь, очки и зачёты считает движок `raskat`, здесь только хранение.
Что храним о болельщике (ADR-018, раздел 3.9): Telegram id, клуб, по дням — очки, `ms`, подсказку
и серию, код дуэли и две галочки. Имя («Пётр К.») — только пока стоит галочка `show_tg_name`:
снял галочку — имя стёрто. `forget` стирает всё.
"""
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Moscow")

# Код дуэли: без похожих друг на друга букв и цифр (0/O, 1/I/L), мини-апп ждёт [A-Za-z0-9-]{3,24}
DUEL_ABC = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
DUEL_LEN = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS raskat_results (
    fan     INTEGER NOT NULL,
    date    TEXT    NOT NULL,
    club    TEXT,
    points  INTEGER NOT NULL,           -- очки дня, без серии: по ним зачёт дня и кубок клубов
    total   INTEGER NOT NULL,           -- итог дня с серией, личное число
    ms      INTEGER NOT NULL,
    hint    INTEGER NOT NULL DEFAULT 0,
    streak  INTEGER NOT NULL,           -- серия с этим днём
    at      TEXT    NOT NULL,
    PRIMARY KEY (fan, date)             -- один результат на день (контракт, раздел 5)
);
CREATE INDEX IF NOT EXISTS raskat_results_date ON raskat_results (date);
CREATE TABLE IF NOT EXISTS raskat_fans (
    fan           INTEGER PRIMARY KEY,
    club          TEXT,
    show_tg_name  INTEGER NOT NULL DEFAULT 0,
    messages      INTEGER NOT NULL DEFAULT 1,
    name          TEXT,                 -- только при show_tg_name = 1
    duel          TEXT UNIQUE
);
"""

COLS = "fan, date, club, points, total, ms, hint, streak, at"


def _row(r) -> dict:
    return {"fan": r[0], "date": r[1], "club": r[2], "points": r[3], "total": r[4],
            "ms": r[5], "hint": bool(r[6]), "streak": r[7], "at": r[8]}


class Taken(Exception):
    """Результат этого дня уже есть: `result` — прежний."""

    def __init__(self, result: dict):
        super().__init__("результат дня уже принят")
        self.result = result


class RaskatStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.executescript(SCHEMA)
        # Счётчик записей: по нему сервер понимает, что зачёты пора пересчитать
        self.version = 0

    # ---------- болельщик ----------

    def fan(self, fan: int) -> dict:
        r = self.conn.execute("SELECT club, show_tg_name, messages, name, duel FROM raskat_fans WHERE fan = ?",
                              (fan,)).fetchone()
        if not r:
            return {"fan": fan, "club": None, "show_tg_name": False, "messages": True, "name": None, "duel": None}
        return {"fan": fan, "club": r[0], "show_tg_name": bool(r[1]), "messages": bool(r[2]),
                "name": r[3], "duel": r[4]}

    def fans(self, ids) -> dict[int, dict]:
        """Настройки сразу многих — для имён в таблице дня."""
        ids = list({int(i) for i in ids})
        out = {}
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            marks = ",".join("?" * len(part))
            for r in self.conn.execute(
                    f"SELECT fan, club, show_tg_name, name FROM raskat_fans WHERE fan IN ({marks})", part):
                out[r[0]] = {"club": r[1], "show_tg_name": bool(r[2]), "name": r[3]}
        return out

    def settings(self, fan: int, *, club=..., show_tg_name=None, messages=None, name=...) -> dict:
        """Поменять настройки. Не переданное не трогаем; имя хранится, только пока стоит галочка."""
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            self.conn.execute("INSERT OR IGNORE INTO raskat_fans (fan) VALUES (?)", (fan,))
            if club is not ...:
                self.conn.execute("UPDATE raskat_fans SET club = ? WHERE fan = ?", (club, fan))
            if show_tg_name is not None:
                self.conn.execute("UPDATE raskat_fans SET show_tg_name = ? WHERE fan = ?", (int(show_tg_name), fan))
            if messages is not None:
                self.conn.execute("UPDATE raskat_fans SET messages = ? WHERE fan = ?", (int(messages), fan))
            if name is not ...:
                self.conn.execute("UPDATE raskat_fans SET name = ? WHERE fan = ?", (name, fan))
            self.conn.execute("UPDATE raskat_fans SET name = NULL WHERE fan = ? AND show_tg_name = 0", (fan,))
        self.version += 1
        return self.fan(fan)

    def duel_code(self, fan: int) -> str:
        """Своя ссылка на дуэль: код один на болельщика, повторный запрос отдаёт тот же."""
        have = self.fan(fan)["duel"]
        if have:
            return have
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            self.conn.execute("INSERT OR IGNORE INTO raskat_fans (fan) VALUES (?)", (fan,))
            while True:
                code = "".join(secrets.choice(DUEL_ABC) for _ in range(DUEL_LEN))
                if not self.conn.execute("SELECT 1 FROM raskat_fans WHERE duel = ?", (code,)).fetchone():
                    break
            self.conn.execute("UPDATE raskat_fans SET duel = ? WHERE fan = ?", (code, fan))
        return code

    def by_duel(self, code: str) -> int | None:
        r = self.conn.execute("SELECT fan FROM raskat_fans WHERE duel = ?", (code,)).fetchone()
        return r[0] if r else None

    # ---------- результаты ----------

    def result(self, fan: int, day: str) -> dict | None:
        r = self.conn.execute(f"SELECT {COLS} FROM raskat_results WHERE fan = ? AND date = ?",
                              (fan, day)).fetchone()
        return _row(r) if r else None

    def add(self, fan: int, day: str, club: str | None, ms: int, hint: bool, score, now: datetime) -> dict:
        """Записать результат дня. `score(streak_before) -> (очки дня, итог)` считает движок.

        Серия — дни подряд по датам раскатов: вчерашний результат есть — его серия плюс один.
        Повтор того же дня — `Taken` с прежним результатом, запись не меняется."""
        prev = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            was = self.conn.execute(f"SELECT {COLS} FROM raskat_results WHERE fan = ? AND date = ?",
                                    (fan, day)).fetchone()
            if was:
                raise Taken(_row(was))
            r = self.conn.execute("SELECT streak FROM raskat_results WHERE fan = ? AND date = ?",
                                  (fan, prev)).fetchone()
            before = r[0] if r else 0
            points, total = score(before)
            self.conn.execute(
                f"INSERT INTO raskat_results ({COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (fan, day, club, points, total, ms, int(hint), before + 1,
                 now.astimezone(TZ).isoformat(timespec="seconds")))
        self.version += 1
        return self.result(fan, day)

    def day(self, day: str) -> list[dict]:
        return [_row(r) for r in self.conn.execute(
            f"SELECT {COLS} FROM raskat_results WHERE date = ?", (day,))]

    def upto(self, day: str) -> list[dict]:
        """Все результаты сезона до дня включительно — для сезонного кубка."""
        return [_row(r) for r in self.conn.execute(
            f"SELECT {COLS} FROM raskat_results WHERE date <= ? ORDER BY date", (day,))]

    def mine(self, fan: int) -> list[dict]:
        return [_row(r) for r in self.conn.execute(
            f"SELECT {COLS} FROM raskat_results WHERE fan = ? ORDER BY date", (fan,))]

    def streak(self, fan: int, today: str) -> int:
        """Серия сейчас: с сегодняшним, если он собран; иначе по вчерашний — день ещё не кончился."""
        for d in (today, (date.fromisoformat(today) - timedelta(days=1)).isoformat()):
            r = self.conn.execute("SELECT streak FROM raskat_results WHERE fan = ? AND date = ?",
                                  (fan, d)).fetchone()
            if r:
                return r[0]
        return 0

    def to_call(self, today: str, since: str) -> list[tuple[int, int]]:
        """Кого звать в раскат дня (ADR-023, раздел 2): (болельщик, серия сейчас).

        Галочка `messages` включена, сегодняшнего результата нет, а за последние дни (с `since`)
        хотя бы один есть: того, кто не играл неделю, ежедневный зов только раздражает. Серия —
        вчерашняя: с ней её ещё можно продлить."""
        yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
        rows = self.conn.execute(
            "SELECT f.fan, COALESCE(y.streak, 0) FROM raskat_fans f "
            "JOIN raskat_results a ON a.fan = f.fan AND a.date >= ? AND a.date < ? "
            "LEFT JOIN raskat_results y ON y.fan = f.fan AND y.date = ? "
            "WHERE f.messages = 1 AND NOT EXISTS "
            "  (SELECT 1 FROM raskat_results t WHERE t.fan = f.fan AND t.date = ?) "
            "GROUP BY f.fan ORDER BY f.fan", (since, today, yesterday, today)).fetchall()
        return [(r[0], r[1]) for r in rows]

    def forget(self, fan: int) -> None:
        """Стереть всё о болельщике: результаты, серию, код дуэли, настройки (ADR-018, раздел 3.9)."""
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            self.conn.execute("DELETE FROM raskat_results WHERE fan = ?", (fan,))
            self.conn.execute("DELETE FROM raskat_fans WHERE fan = ?", (fan,))
        self.version += 1

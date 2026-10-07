"""Задания службы cuts (ADR-036, раздел 2): кусок записи матча в mp4 для человека — админа или помощника.

Таблица `cut_jobs` в state.db: ролик VK, окно записи (начало и длина в секундах), вид, статус, файл и его размеры.
Ставят задания служба clips (превью и заготовки после прохода), а дальше — бот и API (видео по запросу); выполняет
служба cuts, по одному. Одно окно одного ролика — одно задание: кто бы ни попросил его снова, получит то же
задание и тот же файл. Первыми режем те, которые ждёт человек (`URGENT`), потом превью, которые бот пришлёт сам
(`SEND`), потом заготовки (`PREP`).

Упало — ещё раз через RETRY, всего TRIES попыток; дальше заготовка остаётся ошибкой, а человек, попросивший окно
снова, запускает его заново. Задания и файлы живут KEEP_DAYS с последней просьбы: чистит служба cuts. Id людей в
таблице нет — только ролик, окно и статус.

Только stdlib, без сети.
"""
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

KINDS = ("preview", "review", "search", "clip")   # превью гола без секунды, 30 с гола, окно поиска, клип болельщикам
PREP, SEND, URGENT = 0, 1, 2   # заготовка; бот пришлёт, когда будет готово; человек ждёт сейчас
TRIES = 3
RETRY = (timedelta(minutes=1), timedelta(minutes=10))   # пауза после первой и второй неудачи
KEEP_DAYS = 3
TOUCH = timedelta(hours=1)   # чаще не продлеваем жизнь задания: проход clips просит заготовки каждые минуты
REVIEW_BEFORE = 20   # с до гола: окно, которое увидят болельщики в клипе (probe_cuts.CLIP_BEFORE)
REVIEW_AFTER = 10    # и после (probe_cuts.CLIP_AFTER)
DIR = "media/cuts"   # файлы — от корня проекта, по номеру задания

_VIDEO_RE = re.compile(r"(?:video|live)(-?\d{1,12})_(\d{1,12})")   # как replay._VIDEO_RE

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS cut_jobs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT    NOT NULL,
    used    TEXT    NOT NULL,
    vid     TEXT    NOT NULL,
    video   TEXT    NOT NULL,
    start   INTEGER NOT NULL,
    len     INTEGER NOT NULL,
    kind    TEXT    NOT NULL CHECK (kind IN ({", ".join(f"'{k}'" for k in KINDS)})),
    match   TEXT,
    score   TEXT,
    prio    INTEGER NOT NULL DEFAULT {PREP},
    status  TEXT    NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'work', 'done', 'error')),
    tries   INTEGER NOT NULL DEFAULT 0,
    next    TEXT,
    file    TEXT,
    w       INTEGER,
    h       INTEGER,
    dur     INTEGER,
    error   TEXT,
    done_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS cut_jobs_window ON cut_jobs (vid, start, len);
CREATE INDEX IF NOT EXISTS cut_jobs_status ON cut_jobs (status);
"""

COLUMNS = ("id", "at", "used", "vid", "video", "start", "len", "kind", "match", "score", "prio", "status", "tries",
           "next", "file", "w", "h", "dur", "error", "done_at")


def vid(video: str) -> str:
    """Номер ролика VK («-123_456»): vk.com, vkvideo.ru, video- и live- — один ролик. Не VK — адрес как есть."""
    m = _VIDEO_RE.search(video or "")
    return f"{m.group(1)}_{m.group(2)}" if m else (video or "")


def review_window(t: int, length: float | None = None) -> tuple[int, int]:
    """Окно 30 с вокруг секунды гола (20 до, 10 после) — то же, что увидят болельщики в клипе: (начало, длина)."""
    start = max(0, int(t) - REVIEW_BEFORE)
    end = int(t) + REVIEW_AFTER
    if length:
        end = min(end, int(length))
    return start, max(1, end - start)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


class CutJobs:
    """Очередь заданий нарезки. Соединение — с isolation_level=None: транзакции открываем сами (BEGIN IMMEDIATE),
    пишут несколько служб. root — корень проекта: от него пути файлов."""

    def __init__(self, conn: sqlite3.Connection, root: Path | str = "."):
        self.conn = conn
        self.root = Path(root)
        conn.executescript(SCHEMA)

    def _row(self, r) -> dict | None:
        return dict(zip(COLUMNS, r)) if r else None

    def _one(self, where: str, args=()) -> dict | None:
        return self._row(self.conn.execute(f"SELECT {', '.join(COLUMNS)} FROM cut_jobs WHERE {where}", args).fetchone())

    def get(self, job_id: int) -> dict | None:
        return self._one("id = ?", (int(job_id),))

    def path(self, job: dict) -> Path | None:
        """Файл готового задания на диске, его нет — None."""
        p = self.root / job["file"] if job and job.get("status") == "done" and job.get("file") else None
        return p if p and p.is_file() else None

    def want(self, now: datetime, video: str, start: int, length: int, kind: str, *, prio: int = PREP,
             match: str | None = None, score: str | None = None) -> int:
        """Нужно окно ролика: id задания. Такое окно уже просили — то же задание: срочность — наибольшая из просьб;
        файл пропал — режем заново; окно упало все TRIES раз, а теперь его ждёт человек, — пробуем снова."""
        if kind not in KINDS:
            raise ValueError(f"вид задания {kind!r} не из {KINDS}")
        start, length = max(0, int(start)), max(1, int(length))
        key = vid(video)
        stamp = _iso(now)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._one("vid = ? AND start = ? AND len = ?", (key, start, length))
            if row is None:
                cur = self.conn.execute(
                    "INSERT INTO cut_jobs (at, used, vid, video, start, len, kind, match, score, prio) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (stamp, stamp, key, video, start, length, kind, match, score, int(prio)))
                self.conn.execute("COMMIT")
                return cur.lastrowid
            upd: dict = {}
            if prio > row["prio"]:
                upd["prio"] = int(prio)
            again = (row["status"] == "done" and self.path(row) is None) or \
                (row["status"] == "error" and row["tries"] >= TRIES and prio >= URGENT)
            if again:
                upd.update(status="queued", tries=0, next=None, file=None, error=None, done_at=None)
            if upd or (datetime.fromisoformat(row["used"]) <= now - TOUCH):
                upd["used"] = stamp
            if upd:
                sets = ", ".join(f"{k} = ?" for k in upd)
                self.conn.execute(f"UPDATE cut_jobs SET {sets} WHERE id = ?", (*upd.values(), row["id"]))
            self.conn.execute("COMMIT")
            return row["id"]
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def take(self, now: datetime) -> dict | None:
        """Следующее задание в работу: срочные первыми, среди равных — кто раньше попросил. Упавшее — после паузы."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._one("status = 'queued' OR (status = 'error' AND tries < ? AND (next IS NULL OR next <= ?)) "
                            "ORDER BY prio DESC, id LIMIT 1", (TRIES, _iso(now)))
            if row:
                self.conn.execute("UPDATE cut_jobs SET status = 'work' WHERE id = ?", (row["id"],))
                row["status"] = "work"
            self.conn.execute("COMMIT")
            return row
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def done(self, job_id: int, now: datetime, file: str, w: int | None, h: int | None, dur: int | None) -> None:
        self.conn.execute("UPDATE cut_jobs SET status = 'done', file = ?, w = ?, h = ?, dur = ?, error = NULL, "
                          "next = NULL, done_at = ? WHERE id = ?", (file, w, h, dur, _iso(now), int(job_id)))

    def fail(self, job_id: int, now: datetime, error: str) -> bool:
        """Задание не вышло: ещё попытка через RETRY или всё. True — попыток больше не будет."""
        row = self.get(job_id)
        if not row:
            return True
        tries = row["tries"] + 1
        final = tries >= TRIES
        nxt = None if final else _iso(now + RETRY[min(tries, len(RETRY)) - 1])
        self.conn.execute("UPDATE cut_jobs SET status = 'error', tries = ?, next = ?, error = ?, done_at = ? "
                          "WHERE id = ?", (tries, nxt, str(error)[:300], _iso(now), int(job_id)))
        return final

    def reset_work(self) -> int:
        """Служба упала посреди нарезки: такие задания — снова в очередь."""
        return self.conn.execute("UPDATE cut_jobs SET status = 'queued' WHERE status = 'work'").rowcount

    def forget(self, now: datetime, keep_days: int = KEEP_DAYS) -> list[str]:
        """Задания, которых не просили keep_days, — из таблицы; их файлы (от корня) — вернуть: стирает служба."""
        edge = _iso(now - timedelta(days=keep_days))
        rows = self.conn.execute("SELECT id, file FROM cut_jobs WHERE used < ? AND status != 'work'", (edge,)).fetchall()
        if rows:
            self.conn.execute(f"DELETE FROM cut_jobs WHERE id IN ({', '.join('?' * len(rows))})", [r[0] for r in rows])
        return [r[1] for r in rows if r[1]]

    def counts(self) -> dict[str, int]:
        """Для пульта: в очереди (и сколько из них ждёт человек), в работе, готово, ошибок."""
        out = {"queued": 0, "urgent": 0, "work": 0, "done": 0, "error": 0}
        for status, prio, tries, n in self.conn.execute(
                "SELECT status, prio, tries, count(*) FROM cut_jobs GROUP BY status, prio, tries"):
            if status == "error" and tries < TRIES:   # ждёт повтора — ещё в очереди
                status = "queued"
            out[status] += n
            if status == "queued" and prio >= URGENT:
                out["urgent"] += n
        return out

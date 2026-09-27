"""База «Звена»: SQLite в режиме WAL, схема и миграции по PRAGMA user_version.

Всё о человеке лежит в строках с manager_id и стирается `erase()` (DELETE /me, правило 4 CLAUDE.md).
Таблицы лиг не хранятся — считаются из снимков очков на лету, поэтому после удаления они пересчитаны сразу.
"""
import json
import sqlite3
from pathlib import Path

# Каждая миграция — список команд. Новую — только в конец; старые не меняются.
MIGRATIONS: list[list[str]] = [
    [
        # служебное: пульс сервера, состояние задач
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)",
        # менеджер — пользователь Telegram (id из проверенного initData)
        """CREATE TABLE managers (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,                 -- JSON [прилагательное, существительное]
            fav_club TEXT NOT NULL,
            my_player TEXT,                     -- «Мой игрок» (ADR-010): автопилот его не трогает
            autopilot INTEGER NOT NULL DEFAULT 1,
            messages INTEGER NOT NULL DEFAULT 1,
            show_tg_name INTEGER NOT NULL DEFAULT 0,
            tg_name TEXT,                       -- имя из Telegram, только пока стоит галочка
            created_at TEXT NOT NULL,
            start_tour INTEGER NOT NULL,        -- первый тур, на который собран состав
            budget INTEGER NOT NULL,            -- стартовый бюджет (опоздавшему — медиана активных)
            bank INTEGER NOT NULL,              -- льдинки в кассе
            free INTEGER NOT NULL DEFAULT 0,    -- бесплатные обмены
            unlimited_until TEXT NOT NULL,      -- до первого своего дедлайна обмены без ограничений
            squad TEXT NOT NULL,                -- JSON {lineup, bench, captain, assistant} на tour_next
            last_seen TEXT NOT NULL,            -- последнее открытие «Звена»
            can_write INTEGER NOT NULL DEFAULT 1,   -- 0 — бот заблокирован, не пишем
            msg_week TEXT,                      -- неделя счётчика сообщений, ISO «2026-W42»
            msg_count INTEGER NOT NULL DEFAULT 0,
            last_msg_at TEXT,
            last_sunday TEXT,                   -- неделя последнего воскресного «есть что решить»
            last_story_tour INTEGER NOT NULL DEFAULT 0
        )""",
        # владения: наклейка, слот места, цена покупки
        """CREATE TABLE holdings (
            manager_id INTEGER NOT NULL,
            sid TEXT NOT NULL,
            slot TEXT NOT NULL,
            bought INTEGER NOT NULL,
            last_price INTEGER NOT NULL,        -- последняя известная стоимость: для возврата скрытого
            since TEXT NOT NULL,
            PRIMARY KEY (manager_id, sid)
        )""",
        # окно обменов на тур: сколько бесплатных и платных, буст тура
        """CREATE TABLE windows (
            manager_id INTEGER NOT NULL,
            tour INTEGER NOT NULL,
            free_used INTEGER NOT NULL DEFAULT 0,
            paid_points INTEGER NOT NULL DEFAULT 0,
            paid_ice INTEGER NOT NULL DEFAULT 0,
            boost TEXT,
            PRIMARY KEY (manager_id, tour)
        )""",
        # состав, замороженный в дедлайн
        """CREATE TABLE lineups (
            manager_id INTEGER NOT NULL,
            tour INTEGER NOT NULL,
            squad TEXT NOT NULL,
            penalty INTEGER NOT NULL DEFAULT 0,
            boost TEXT,
            frozen_at TEXT NOT NULL,
            PRIMARY KEY (manager_id, tour)
        )""",
        # снимок очков закрытого тура — больше не меняется
        """CREATE TABLE scores (
            manager_id INTEGER NOT NULL,
            tour INTEGER NOT NULL,
            total INTEGER NOT NULL,
            detail TEXT NOT NULL,
            PRIMARY KEY (manager_id, tour)
        )""",
        "CREATE INDEX scores_tour ON scores (tour)",
        # альбом клубов: клубы основы на дедлайн
        """CREATE TABLE album (
            manager_id INTEGER NOT NULL,
            club TEXT NOT NULL,
            tour INTEGER NOT NULL,
            PRIMARY KEY (manager_id, club)
        )""",
        """CREATE TABLE missions (
            manager_id INTEGER NOT NULL,
            tour INTEGER NOT NULL,
            done INTEGER NOT NULL,
            PRIMARY KEY (manager_id, tour)
        )""",
        # «Оставить»: автопилот не трогает наклейку, пока игрок не вернётся или клуб не сыграет ещё 4 матча
        """CREATE TABLE keeps (
            manager_id INTEGER NOT NULL,
            sid TEXT NOT NULL,
            since TEXT NOT NULL,
            PRIMARY KEY (manager_id, sid)
        )""",
        # журнал сделок, автопилота, автозамен и заданий — видит только сам менеджер
        """CREATE TABLE journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manager_id INTEGER NOT NULL,
            at TEXT NOT NULL,
            kind TEXT NOT NULL,
            text TEXT NOT NULL
        )""",
        "CREATE INDEX journal_manager ON journal (manager_id, id)",
        # «Нашёл первым»: взял новичка до его первых 5 очков (ADR-014, раздел 9)
        """CREATE TABLE finds (
            manager_id INTEGER NOT NULL,
            sid TEXT NOT NULL,
            at TEXT NOT NULL,
            PRIMARY KEY (manager_id, sid)
        )""",
        # свои лиги по коду
        """CREATE TABLE leagues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            owner_id INTEGER,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE league_members (
            league_id INTEGER NOT NULL,
            manager_id INTEGER NOT NULL,
            joined_at TEXT NOT NULL,
            PRIMARY KEY (league_id, manager_id)
        )""",
        "CREATE INDEX league_members_manager ON league_members (manager_id)",
        # ступени по месяцам: ступень, группа, прошлая ступень (для сообщения о повышении)
        """CREATE TABLE steps (
            month TEXT NOT NULL,
            manager_id INTEGER NOT NULL,
            step INTEGER NOT NULL,
            grp INTEGER NOT NULL,
            prev_step INTEGER,
            told INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (month, manager_id)
        )""",
        # что сервер уже сделал с туром
        """CREATE TABLE tour_state (
            tour INTEGER PRIMARY KEY,
            deadline_done TEXT,
            closed_done TEXT
        )""",
        # недоступность сервера: больше 6 часов за сутки до дедлайна — всем +1 обмен (раздел 12)
        "CREATE TABLE downtime (start TEXT NOT NULL, end TEXT NOT NULL)",
    ],
]

# Таблицы, где всё о менеджере: стираются кнопкой «Удалить моё „Звено“»
PERSONAL = ("holdings", "windows", "lineups", "scores", "album", "missions", "keeps", "journal", "finds",
            "league_members", "steps")


def connect(path: str | Path) -> sqlite3.Connection:
    """Соединение с базой: WAL, внешние ключи, строки как словари. Схема — по миграциям."""
    if str(path) != ":memory:":
        Path(path).resolve().parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=5000")
    migrate(db)
    return db


def migrate(db: sqlite3.Connection) -> int:
    version = db.execute("PRAGMA user_version").fetchone()[0]
    for n, steps in enumerate(MIGRATIONS[version:], start=version + 1):
        db.execute("BEGIN")
        try:
            for sql in steps:
                db.execute(sql)
            db.execute(f"PRAGMA user_version = {n}")
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return db.execute("PRAGMA user_version").fetchone()[0]


class tx:
    """Транзакция: `with tx(db): …` — всё или ничего. Вложенные — одной транзакцией."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.outer = False

    def __enter__(self):
        if not self.db.in_transaction:
            self.db.execute("BEGIN IMMEDIATE")
            self.outer = True
        return self.db

    def __exit__(self, exc_type, exc, tb):
        if self.outer:
            self.db.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


def get_meta(db: sqlite3.Connection, key: str, default=None):
    row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_meta(db: sqlite3.Connection, key: str, value) -> None:
    db.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
               (key, json.dumps(value, ensure_ascii=False)))


def erase(db: sqlite3.Connection, manager_id: int) -> None:
    """Стереть всё о менеджере. Свои лиги без участников исчезают; владелец лиги — больше никто."""
    with tx(db):
        for table in PERSONAL:
            db.execute(f"DELETE FROM {table} WHERE manager_id = ?", (manager_id,))
        db.execute("UPDATE leagues SET owner_id = NULL WHERE owner_id = ?", (manager_id,))
        db.execute("DELETE FROM leagues WHERE id NOT IN (SELECT DISTINCT league_id FROM league_members)")
        db.execute("DELETE FROM managers WHERE id = ?", (manager_id,))

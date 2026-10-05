"""«Мой игрок» (ADR-010, решение 3; ADR-030, раздел 6): кому из болельщиков присылать голы какого игрока.

Звёздочку на странице игрока мини-апп хранит на устройстве и в облаке Telegram, как любимую команду, и
повторяет на сервер API (`PUT /api/me/player`). Сюда — только связь «Telegram id → ключ игрока» (`pk`, 10 знаков
от сборки, не id лиги) и когда её поставили: по ней бот после матча присылает гол этого игрока. Один игрок на
человека. Стирается снятием звёздочки, «Выключить» в напоминаниях и блокировкой бота. На пульте — только число.

Пишет сервер API, читает и стирает бот — каждый своим соединением с state.db, как зачёт «Раската» (ADR-023).
"""
import re
import sqlite3
from datetime import datetime

PK_RE = re.compile(r"^[0-9a-f]{10}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS my_player (
    fan  INTEGER PRIMARY KEY,
    pk   TEXT    NOT NULL,
    at   TEXT    NOT NULL
);
"""


class MyPlayerStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        conn.executescript(SCHEMA)

    def set(self, fan: int, pk: str, now: datetime) -> None:
        """Отметить игрока. Тот же игрок — время не трогаем: голы считаются с первой отметки."""
        if not PK_RE.match(pk or ""):
            raise ValueError("ключ игрока — 10 знаков 0-9a-f")
        self.conn.execute("INSERT INTO my_player (fan, pk, at) VALUES (?, ?, ?) "
                          "ON CONFLICT (fan) DO UPDATE SET at = CASE WHEN pk = excluded.pk THEN at ELSE excluded.at END, "
                          "pk = excluded.pk", (fan, pk, now.isoformat(timespec="seconds")))

    def get(self, fan: int) -> str | None:
        row = self.conn.execute("SELECT pk FROM my_player WHERE fan = ?", (fan,)).fetchone()
        return row[0] if row else None

    def forget(self, fan: int) -> bool:
        return self.conn.execute("DELETE FROM my_player WHERE fan = ?", (fan,)).rowcount > 0

    def all(self) -> list[tuple[int, str, str]]:
        """(Telegram id, ключ игрока, когда отметил) — для рассылки после матча."""
        return list(self.conn.execute("SELECT fan, pk, at FROM my_player ORDER BY fan"))

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM my_player").fetchone()[0]

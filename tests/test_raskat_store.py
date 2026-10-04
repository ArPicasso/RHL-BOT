"""Хранилище зачёта «Раската» (ADR-018): кого звать в раскат дня (ADR-023, раздел 2)."""
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from raskat_store import RaskatStore  # noqa: E402

TZ = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=TZ)
TODAY, SINCE = "2026-10-04", "2026-09-27"


class ToCall(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.rs = RaskatStore(self.conn)

    def solve(self, fan: int, day: str, points: int = 10):
        self.rs.add(fan, day, None, 1000, False, lambda before: (points, points), NOW)

    def test_calls_the_one_with_a_live_streak(self):
        self.rs.settings(1)
        self.solve(1, "2026-10-02")
        self.solve(1, "2026-10-03")
        self.assertEqual(self.rs.to_call(TODAY, SINCE), [(1, 2)])   # серия вчерашняя: её и продлевают

    def test_does_not_call_the_solved_the_muted_and_the_gone(self):
        for fan in (2, 3, 4):
            self.rs.settings(fan)
        self.rs.settings(3, messages=False)
        self.solve(2, "2026-10-03")
        self.solve(2, TODAY)            # уже собрал сегодня
        self.solve(3, "2026-10-03")     # снял галочку в мини-аппе
        self.solve(4, "2026-09-01")     # не играл больше недели
        self.assertEqual(self.rs.to_call(TODAY, SINCE), [])

    def test_no_streak_but_played_this_week(self):
        self.rs.settings(5)
        self.solve(5, "2026-10-01")     # серия оборвалась, но человек живой
        self.assertEqual(self.rs.to_call(TODAY, SINCE), [(5, 0)])

    def test_never_played_is_not_called(self):
        self.rs.settings(6, club="tambov")   # зашёл, выбрал клуб, раскат не собирал
        self.assertEqual(self.rs.to_call(TODAY, SINCE), [])

    def test_forget_takes_the_fan_out(self):
        self.rs.settings(7)
        self.solve(7, "2026-10-03")
        self.assertEqual(self.rs.to_call(TODAY, SINCE), [(7, 1)])
        self.rs.forget(7)
        self.assertEqual(self.rs.to_call(TODAY, SINCE), [])

    def test_one_row_per_fan(self):
        self.rs.settings(8)
        for d in ("2026-09-28", "2026-09-29", "2026-10-03"):
            self.solve(8, d)
        self.assertEqual(self.rs.to_call(TODAY, SINCE), [(8, 1)])


if __name__ == "__main__":
    unittest.main()

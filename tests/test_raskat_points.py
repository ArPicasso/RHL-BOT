"""Очки за раскат: таблица примеров, серия и подсказка (раздел 4 контракта).

Это та самая «таблица примеров в тестах», о которой говорит контракт: любая правка формулы меняет
её, `raskat/standings.py` и `webapp/raskat.js` разом.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from raskat import rules  # noqa: E402
from raskat.standings import day_points, points, streak_points  # noqa: E402

# (par, секунды) → очки дня: база 60 + 40 * clamp((3*par - t) / (2.5*par), 0, 1).
# Ровно таблица из ADR-018, раздел 3.2: par 70 — это среда и суббота, par 90 — между четвергом и
# пятницей, по нему видно, как шкала тянется за нормой
TABLE = {
    (70, 25): 100, (90, 25): 100,
    (70, 35): 100, (90, 35): 100,       # par/2 для par 70: дальше время не растёт
    (70, 45): 98, (90, 45): 100,        # par/2 для par 90
    (70, 70): 92, (90, 70): 96,         # ровно норма: 32 из 40
    (70, 120): 81, (90, 120): 87,
    (70, 140): 76, (90, 140): 83,       # 2 * par: 16 из 40
    (70, 180): 67, (90, 180): 76,
    (70, 210): 60, (90, 210): 71,       # 3 * par: за время ноль
    (70, 600): 60, (90, 600): 60,
}


class Table(unittest.TestCase):
    def test_day_points(self):
        for (par, t), want in TABLE.items():
            self.assertEqual(day_points(par, t), want, f"par {par}, {t} с")

    def test_streak_adds_on_top_of_the_day(self):
        for (par, t), want in TABLE.items():
            self.assertEqual(points(par, t, 0), want, f"par {par}, {t} с")
            self.assertEqual(points(par, t, 7), want + 10, f"par {par}, {t} с")

    def test_hint_costs_twenty(self):
        for (par, t), want in TABLE.items():
            self.assertEqual(day_points(par, t, hint=True), want - 20, f"par {par}, {t} с")
            self.assertEqual(points(par, t, 7, hint=True), want - 20 + 10, f"par {par}, {t} с")

    def test_whole_numbers(self):
        self.assertTrue(all(isinstance(day_points(par, t), int) for par, t in TABLE))


class Streak(unittest.TestCase):
    def test_two_a_day_up_to_ten(self):
        self.assertEqual([streak_points(s) for s in range(9)], [0, 2, 4, 6, 8, 10, 10, 10, 10])

    def test_nothing_for_nonsense(self):
        self.assertEqual(streak_points(-3), 0)

    def test_day_standings_do_not_see_the_streak(self):
        """Серия в очки дня не входит: иначе первое место дня новичку закрыто (раздел 4)."""
        self.assertEqual(points(70, 70, 7), day_points(70, 70) + streak_points(7))
        self.assertEqual(day_points(70, 70), points(70, 70, 0))
        self.assertEqual(max(TABLE.values()), rules.PTS_BASE + rules.PTS_TIME)


class Edges(unittest.TestCase):
    def test_nothing_above_half_the_norm(self):
        """Быстрее par/2 очков больше не даёт — гнать до предела незачем."""
        best = day_points(70, 35)
        self.assertEqual(best, 100)
        self.assertTrue(all(day_points(70, t) == best for t in range(0, 36)))

    def test_never_grows_with_time(self):
        row = [day_points(70, t) for t in range(0, 400, 5)]
        self.assertEqual(row, sorted(row, reverse=True))

    def test_never_below_zero(self):
        self.assertEqual(points(70, 10_000, 0, hint=True), rules.PTS_BASE + rules.PTS_HINT)
        self.assertTrue(all(points(par, t, 0, hint=True) >= 0
                            for par in (45, 70, 110) for t in (0, 1, 500, 10_000)))

    def test_par_must_be_positive(self):
        for par in (0, -70):
            with self.assertRaises(ValueError):
                day_points(par, 60)


if __name__ == "__main__":
    unittest.main()

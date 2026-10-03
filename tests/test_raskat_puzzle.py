"""Расклад «Раската»: план дня, детерминированность, единственность решения, проверка пути.

Разделы 1 и 3 контракта (docs/raskat/contract.md).
"""
import os
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import raskat  # noqa: E402
from raskat import plan, rules  # noqa: E402
from raskat.puzzle import Puzzle, _keys, edges, wall_pairs  # noqa: E402

DAY = "2026-10-03"          # суббота, раскат № 1 сезона


def solution(day: str = DAY):
    p = raskat.generate(day)
    found = raskat.solve(p, 2)
    return p, found[0]


class DayPlan(unittest.TestCase):
    def test_week_grows_to_the_weekend(self):
        self.assertEqual(plan.day_plan("2026-10-05").w, 5)              # понедельник
        self.assertEqual(plan.day_plan("2026-10-07").w, 6)              # среда
        self.assertEqual(plan.day_plan("2026-10-09").w, 7)              # пятница
        self.assertEqual(plan.day_plan("2026-10-11").k, 7)              # воскресенье
        self.assertTrue(all(p.w == p.h for p in map(plan.day_plan, plan.season_days(rules.SEASON_TO))))

    def test_first_day_is_the_row_from_the_contract(self):
        p = plan.day_plan(DAY)
        self.assertEqual((p.n, p.w, p.h, p.k, p.hard, p.par), (1, 6, 6, 5, 1, 70))

    def test_day_number(self):
        self.assertEqual(plan.day_number(DAY), 1)
        self.assertEqual(plan.day_number("2026-10-04"), 2)
        self.assertEqual(plan.day_number(rules.SEASON_TO), 170)
        self.assertEqual(plan.day_number("2026-10-02"), 0)              # до сезона раската нет

    def test_season_days_stop_at_the_last_day(self):
        self.assertEqual(plan.season_days("2026-10-05"),
                         [date(2026, 10, 3), date(2026, 10, 4), date(2026, 10, 5)])
        self.assertEqual(plan.season_days("2027-05-01")[-1], rules.SEASON_TO)
        self.assertEqual(plan.season_days("2026-09-01"), [])

    def test_today_needs_a_timezone(self):
        from datetime import datetime
        with self.assertRaises(ValueError):
            plan.today(datetime(2026, 10, 3, 12))


class Determinism(unittest.TestCase):
    def test_same_date_same_puzzle(self):
        self.assertEqual(raskat.generate(DAY), raskat.generate(DAY))

    def test_date_and_string_are_the_same_day(self):
        self.assertEqual(raskat.generate(date(2026, 10, 3)), raskat.generate(DAY))

    def test_days_differ(self):
        got = {raskat.generate(d).dots for d in plan.season_days("2026-10-31")}
        self.assertEqual(len(got), 29)

    def test_salt_from_the_environment_wins(self):
        was = raskat.generate(DAY)
        os.environ["RASKAT_SALT"] = "соль из секрета"
        try:
            other = raskat.generate(DAY)
        finally:
            del os.environ["RASKAT_SALT"]
        self.assertNotEqual(was.dots, other.dots)
        self.assertEqual(len(raskat.solve(other, 2)), 1)
        self.assertEqual(raskat.generate(DAY), was)      # соль вернулась — расклад тот же


class OneSolution(unittest.TestCase):
    """Единственность решения на каждом дне сезона: 170 раскладов, около трёх секунд."""

    def test_every_day_of_the_season(self):
        for day in plan.season_days(rules.SEASON_TO):
            p = raskat.generate(day)
            found = raskat.solve(p, 2)
            self.assertEqual(len(found), 1, f"{day}: решений {len(found)}")
            self.assertTrue(raskat.check(p, found[0]).ok, f"{day}: решатель выдал негодный путь")

    def test_field_is_valid_every_day(self):
        for day in plan.season_days(rules.SEASON_TO):
            p = raskat.generate(day)
            pl = plan.day_plan(day)
            self.assertEqual((p.w, p.h, p.hard, p.par), (pl.w, pl.h, pl.hard, pl.par))
            self.assertGreaterEqual(p.k, pl.k)
            self.assertEqual(len(set(p.dots)), p.k)
            self.assertTrue(all(0 <= c < p.cells for c in p.dots), day)
            self.assertEqual(wall_pairs(p.walls) - set(edges(p.w, p.h)), set(), day)

    def test_limit_stops_the_search(self):
        p = Puzzle(DAY, 1, 3, 3, (0, 8), (), 1, 60)      # открытое поле 3×3: решений много
        self.assertEqual(len(raskat.solve(p, 1)), 1)
        self.assertEqual(len(raskat.solve(p, 2)), 2)
        self.assertEqual(len(raskat.solve(p, 50)), 20)

    def test_start_can_be_any_cell(self):
        """Номер 1 не обязан стоять в начале пути (раздел 1), и решатель это видит."""
        p = Puzzle(DAY, 1, 3, 3, (0, 8), (), 1, 60)
        found = raskat.solve(p, 50)
        self.assertTrue(any(s[0] != 0 for s in found))
        self.assertTrue(all(raskat.check(p, s).ok for s in found))

    def test_corridor_has_exactly_one_solution(self):
        """Борта вокруг всего, кроме змейки: остаётся сам путь."""
        p = Puzzle(DAY, 1, 3, 3, (0, 8), _keys({(0, 3), (1, 4), (4, 7), (5, 8)}), 1, 60)
        self.assertEqual(raskat.solve(p, 2), [[0, 1, 2, 5, 4, 3, 6, 7, 8]])


class Check(unittest.TestCase):
    """`check` не доверяет вводу: путь приходит с телефона болельщика."""

    @classmethod
    def setUpClass(cls):
        cls.p, cls.sol = solution()

    def reason(self, path) -> str:
        v = raskat.check(self.p, path)
        self.assertFalse(v.ok)
        self.assertFalse(v)                 # Verdict годится в if
        return v.reason

    def test_real_solution_passes(self):
        v = raskat.check(self.p, self.sol)
        self.assertTrue(v.ok and v and v.reason == "")

    def test_hole_in_the_path(self):
        bad = self.sol[:]
        bad[1], bad[2] = bad[2], bad[1]     # шаг через клетку: соседство рвётся
        self.assertIn("не соседние", self.reason(bad))

    def test_diagonal_step(self):
        p = Puzzle(DAY, 1, 2, 2, (0, 2), (), 1, 60)
        v = raskat.check(p, [0, 3, 1, 2])   # 0 → 3 по диагонали
        self.assertFalse(v.ok)
        self.assertIn("не соседние", v.reason)

    def test_step_through_a_wall(self):
        p = Puzzle(DAY, 1, 3, 3, (0, 8), ("0-1",), 1, 60)
        v = raskat.check(p, [0, 1, 2, 5, 4, 3, 6, 7, 8])
        self.assertFalse(v.ok)
        self.assertEqual(v.reason, "Между клетками 0 и 1 борт")

    def test_cell_twice(self):
        bad = self.sol[:]
        bad[5] = bad[4]
        self.assertIn("пройдена дважды", self.reason(bad))

    def test_numbers_out_of_order(self):
        """Тот же путь наоборот: номера идут вниз, а это уже не раскат."""
        self.assertIn("пройден раньше номера 1", self.reason(self.sol[::-1]))

    def test_missing_cell(self):
        self.assertIn("лёд остался непройденным", self.reason(self.sol[:-1]))

    def test_extra_cell(self):
        self.assertIn(f"на поле {self.p.cells}", self.reason(self.sol + [self.sol[-1]]))

    def test_cell_off_the_board(self):
        bad = self.sol[:]
        bad[3] = self.p.cells
        self.assertIn("не с этого поля", self.reason(bad))
        bad[3] = -1
        self.assertIn("не с этого поля", self.reason(bad))

    def test_not_a_list_of_cells(self):
        self.assertIn("кроме номеров клеток", self.reason([0, "1", 2]))
        self.assertIn("кроме номеров клеток", self.reason([0, True, 2]))
        self.assertIn("лёд остался непройденным", self.reason([]))


if __name__ == "__main__":
    unittest.main()

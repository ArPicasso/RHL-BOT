"""Зачёт дня и кубок клубов: порог, среднее, порядок и сезонный итог (раздел 4 контракта)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from raskat import rules  # noqa: E402
from raskat.standings import standings  # noqa: E402  (в пакете это имя — функция)

D1, D2, D3 = "2026-10-03", "2026-10-04", "2026-10-05"


def fans(date: str, club, pts: list[int], tag: str = "", ms: int = 60_000) -> list[dict]:
    """Болельщики клуба за один день: id вида «клуб-метка-номер», чтобы их можно было повторить."""
    return [{"date": date, "fan": f"{club}-{tag}{i}", "club": club, "points": p, "ms": ms}
            for i, p in enumerate(pts)]


RESULTS = (
    fans(D1, "ryazan-vdv", [90, 80, 70, 60, 100, 50])       # шесть человек, среднее 75.0
    + fans(D1, "belgorod", [100] * 5)                       # пятеро ровно, среднее 100.0
    + fans(D1, "ermak", [100] * 6)                          # столько же очков, но людей больше
    + fans(D1, "samara", [80] * 5)
    + fans(D1, "rostov", [99] * 3)                          # троих до порога не хватило
    + [{"date": D1, "fan": "без клуба", "club": None, "points": 95, "ms": 10_000}]
    + fans(D2, "ryazan-vdv", [60] * 5)                      # те же id, что и в первый день
    + fans(D2, "belgorod", [100] * 3, tag="б")
    + fans(D2, "samara", [90] * 2, tag="б")
    + fans(D3, "ermak", [50] * 5, tag="в")
    + fans(D3, "samara", [90] * 2, tag="в")
)


class Day(unittest.TestCase):
    def test_order_by_points(self):
        rows = standings(RESULTS).days[D1]
        self.assertEqual([r.points for r in rows], sorted((r.points for r in rows), reverse=True))
        self.assertEqual([r.place for r in rows], list(range(1, len(rows) + 1)))
        self.assertEqual(rows[0].points, 100)

    def test_faster_is_higher_when_points_are_equal(self):
        res = [{"date": D1, "fan": "медленный", "club": "ermak", "points": 92, "ms": 80_000},
               {"date": D1, "fan": "быстрый", "club": "ermak", "points": 92, "ms": 40_000},
               {"date": D1, "fan": "лучший", "club": "ermak", "points": 99, "ms": 99_000}]
        rows = standings(res).days[D1]
        self.assertEqual([r.fan for r in rows], ["лучший", "быстрый", "медленный"])

    def test_one_result_a_day(self):
        """Повтор того же болельщика за тот же день в зачёт не идёт (раздел 5)."""
        twice = RESULTS + fans(D1, "rostov", [1] * 3)
        self.assertEqual(len(standings(twice).days[D1]),
                         len(standings(RESULTS).days[D1]))

    def test_fan_without_a_club_is_in_the_day(self):
        rows = standings(RESULTS).days[D1]
        self.assertIn("без клуба", [r.fan for r in rows])


class Clubs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = standings(RESULTS)

    def test_threshold_of_five(self):
        self.assertEqual(rules.CLUB_MIN_FANS, 5)
        self.assertNotIn("rostov", [r.club for r in self.s.clubs[D1]])
        self.assertEqual(self.s.short[D1], ["rostov"])
        self.assertEqual(sorted(self.s.short[D2]), ["belgorod", "samara"])

    def test_average_not_sum(self):
        row = next(r for r in self.s.clubs[D1] if r.club == "ryazan-vdv")
        self.assertEqual((row.avg, row.fans), (75.0, 6))

    def test_more_fans_first_when_averages_are_equal(self):
        self.assertEqual([(r.club, r.avg, r.fans) for r in self.s.clubs[D1]],
                         [("ermak", 100.0, 6), ("belgorod", 100.0, 5),
                          ("samara", 80.0, 5), ("ryazan-vdv", 75.0, 6)])
        self.assertEqual([r.place for r in self.s.clubs[D1]], [1, 2, 3, 4])

    def test_season_is_the_average_of_the_days(self):
        self.assertEqual([(r.place, r.club, r.avg, r.days) for r in self.s.season],
                         [(1, "belgorod", 100.0, 1),      # порог прошёл один день из двух
                          (2, "ermak", 75.0, 2),          # (100.0 + 50.0) / 2
                          (3, "ryazan-vdv", 67.5, 2)])    # (75.0 + 60.0) / 2

    def test_club_below_the_half_is_out_of_the_season(self):
        """«Самара» прошла порог один раз из трёх дней — в сезонный кубок не попадает."""
        self.assertNotIn("samara", [r.club for r in self.s.season])
        self.assertNotIn("rostov", [r.club for r in self.s.season])

    def test_season_counts_everyone_who_played(self):
        row = next(r for r in self.s.season if r.club == "ryazan-vdv")
        self.assertEqual(row.fans, 6)       # во второй день играли те же id, людей всё равно шесть

    def test_no_results_no_standings(self):
        s = standings([])
        self.assertEqual((s.days, s.clubs, s.season, s.short), ({}, {}, [], {}))


if __name__ == "__main__":
    unittest.main()

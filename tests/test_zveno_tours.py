"""Туры «Звена»: 21 тур, объединённый тур 12, дедлайны с исключениями, закрытие (ADR-014, раздел 3)."""
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zveno import rules, tours  # noqa: E402
from zveno.rules import TZ  # noqa: E402


def msk(*a) -> datetime:
    return datetime(*a, tzinfo=TZ)


CAL = [{"date": "2026-10-03", "home": "ryazan-vdv", "away": "belgorod"},
       {"date": "2026-10-13", "home": "ryazan-vdv", "away": "ermak"},
       {"date": "2026-10-14", "home": "ryazan-vdv", "away": "ermak"},
       {"date": "2026-10-17", "home": "belgorod", "away": "ermak"},
       {"date": "2027-01-09", "home": "ermak", "away": "belgorod"}]
CLUBS = ["ryazan-vdv", "belgorod", "ermak"]


class TourRules(unittest.TestCase):
    def test_twenty_one_tours(self):
        self.assertEqual(rules.TOUR_COUNT, 21)
        self.assertEqual(tours.tour_dates(1), (date(2026, 10, 12), date(2026, 10, 18)))
        self.assertEqual(tours.tour_dates(12), (date(2027, 1, 4), date(2027, 1, 17)))
        self.assertEqual(tours.tour_dates(21), (date(2027, 3, 15), date(2027, 3, 21)))

    def test_every_tour_starts_on_monday(self):
        for t in range(1, 22):
            a, b = tours.tour_dates(t)
            self.assertEqual((a.weekday(), b.weekday()), (0, 6), t)

    def test_monday_deadline(self):
        self.assertEqual(tours.deadline(1).isoformat(), "2026-10-12T09:00:00+03:00")
        self.assertEqual(tours.deadline(13).isoformat(), "2027-01-18T09:00:00+03:00")

    def test_after_pause_deadline_is_first_match_day(self):
        self.assertEqual(tours.deadline(12).isoformat(), "2027-01-09T09:00:00+03:00")
        self.assertEqual(tours.deadline(12, [date(2026, 12, 26), date(2027, 1, 6), date(2027, 1, 9)]).isoformat(),
                         "2027-01-06T09:00:00+03:00")

    def test_holiday_deadline(self):
        self.assertEqual(tours.deadline(20).isoformat(), "2027-03-07T23:00:00+03:00")

    def test_close_thursday_noon(self):
        self.assertEqual(tours.close_planned(1).isoformat(), "2026-10-22T12:00:00+03:00")
        self.assertEqual(tours.close_planned(12).isoformat(), "2027-01-21T12:00:00+03:00")
        self.assertEqual(tours.close_planned(1).weekday(), 3)

    def test_close_waits_for_protocols_up_to_14_days(self):
        close = tours.close_planned(5)
        self.assertEqual(tours.is_closed(5, close - timedelta(minutes=1), 12, 12), (False, False))
        self.assertEqual(tours.is_closed(5, close, 12, 9), (True, False))     # не хватает четверти
        self.assertEqual(tours.is_closed(5, close, 12, 8), (False, True))     # нет трети — ждём
        self.assertEqual(tours.is_closed(5, close + timedelta(days=14), 12, 0), (True, False))
        self.assertEqual(tours.is_closed(5, close, 0, 0), (True, False))

    def test_month_and_circle(self):
        self.assertEqual([tours.month_of(t) for t in (1, 3, 4, 8, 9, 12, 14, 15, 19, 21)],
                         ["2026-10", "2026-10", "2026-11", "2026-11", "2026-12", "2027-01", "2027-01",
                          "2027-02", "2027-03", "2027-03"])
        self.assertEqual([tours.circle_of(t) for t in (1, 11, 12, 21)], [1, 1, 2, 2])

    def test_tour_of_date(self):
        self.assertIsNone(tours.tour_of_date("2026-10-03"))    # Пролог
        self.assertEqual(tours.tour_of_date("2026-10-12"), 1)
        self.assertIsNone(tours.tour_of_date("2026-12-30"))    # пауза
        self.assertEqual(tours.tour_of_date("2027-01-16"), 12)
        self.assertIsNone(tours.tour_of_date("2027-03-22"))

    def test_price_window_by_deadline(self):
        dls = {t: tours.deadline(t) for t in range(1, 22)}
        self.assertEqual(tours.price_window("2026-10-05", dls), 0)
        self.assertEqual(tours.price_window("2026-10-12", dls), 1)    # утренний дедлайн — матч после него
        self.assertEqual(tours.price_window("2026-12-30", dls), 11)
        self.assertEqual(tours.price_window("2027-03-07", dls), 19)   # дедлайн тура 20 — в 23:00
        self.assertEqual(tours.price_window("2027-03-08", dls), 20)
        self.assertEqual(tours.price_window("2027-04-01", dls), 21)


class Table(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rows = tours.tour_table(CAL, CLUBS, msk(2026, 10, 23, 10), present={1: 3})
        cls.data = {"tours": rows}

    def test_games_by_club(self):
        t1 = self.data["tours"][0]
        self.assertEqual(t1["games"], {"ryazan-vdv": 2, "belgorod": 1, "ermak": 3})
        self.assertEqual((t1["month"], t1["circle"], t1["closed"]), ("2026-10", 1, True))
        self.assertEqual(self.data["tours"][1]["closed"], False)

    def test_published_deadlines(self):
        self.assertEqual(tours.deadline_of(self.data, 12), msk(2027, 1, 9, 9))
        self.assertEqual(tours.close_of(self.data, 1), msk(2026, 10, 22, 12))

    def test_tour_at_and_next(self):
        self.assertIsNone(tours.tour_at(self.data, msk(2026, 10, 5, 12)))
        self.assertEqual(tours.tour_at(self.data, msk(2026, 10, 12, 8)), 1)
        self.assertEqual(tours.tour_at(self.data, date(2027, 1, 5)), 12)
        self.assertEqual(tours.next_tour(self.data, msk(2026, 10, 5, 12)), 1)
        self.assertEqual(tours.next_tour(self.data, msk(2026, 10, 12, 9)), 2)   # дедлайн прошёл
        self.assertEqual(tours.next_tour(self.data, msk(2026, 12, 30)), 12)
        self.assertIsNone(tours.next_tour(self.data, msk(2027, 3, 16)))

    def test_naive_time_is_refused(self):
        with self.assertRaises(ValueError):
            tours.next_tour(self.data, datetime(2026, 10, 5, 12))


if __name__ == "__main__":
    unittest.main()

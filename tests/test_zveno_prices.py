"""Стоимость наклейки: формула, затухание, предел за тур, «не играл — не дешевеет», статусы (ADR-014, 5, 7, 14)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zveno import prices, rules  # noqa: E402


class Formula(unittest.TestCase):
    def test_newbie_prices_from_adr_table(self):
        self.assertEqual((prices.newbie_price("F"), prices.newbie_price("D"), prices.newbie_price("G")),
                         (5400, 4800, 6500))

    def test_corridor(self):
        self.assertEqual(prices.fprice("F", 0.0), 4500)
        self.assertEqual(prices.fprice("D", 1.0), 4000)
        self.assertEqual(prices.fprice("F", 40.0), 15000)

    def test_step_of_100(self):
        self.assertEqual(prices.fprice("F", 4.87) % 100, 0)
        self.assertEqual(prices.fprice("F", 2.8 + 0.5), 4500 + 700)

    def test_promise(self):
        self.assertAlmostEqual(prices.promise("F", 7400), 2.8 + 2900 / 1400)
        self.assertAlmostEqual(prices.promise("D", 4000), 1.7 - 0.4)
        for slot in "FDG":
            p = prices.fprice(slot, 6.0)
            self.assertAlmostEqual(prices.fprice(slot, prices.promise(slot, p)), p)

    def test_minimal_squad_costs_64000(self):
        mins = {s: rules.PRICE[s]["min"] for s in "GDF"}
        self.assertEqual(sum(mins[s] * n for s, n in rules.SQUAD.items()), 64_000)

    def test_fifteen_newbies_cost_80200(self):
        self.assertEqual(sum(prices.newbie_price(s) * n for s, n in rules.SQUAD.items()), 80_200)


class Path(unittest.TestCase):
    def test_newbie_without_matches(self):
        p = prices.price_path("F", None, [], 3)
        self.assertEqual((p.price, p.price_monday, p.games), (5400, 5400, 0))

    def test_prior_blends_with_base(self):
        # 40 матчей по 6 очков: (0,5·40·(6+0,5) + 10·3,44) / (20 + 10)
        p = prices.price_path("F", (40, 6.0), [], 0)
        self.assertAlmostEqual(p.q, (20 * 6.5 + 10 * 3.44) / 30)
        self.assertEqual(p.price, prices.fprice("F", p.q))

    def test_early_cap_800(self):
        p = prices.price_path("F", None, [(1, 30), (1, 30)], 1)
        self.assertEqual((p.price, p.price_monday), (5400 + 800, 5400))
        self.assertGreater(p.target, p.price)

    def test_prolog_counts_with_early_cap(self):
        p = prices.price_path("F", None, [(0, 30)], 1)
        self.assertEqual((p.mondays[0], p.mondays[1], p.price), (5400, 6200, 6200))

    def test_price_prev_is_deadline_before_last(self):
        # «Итог недели»: рост за прошлый тур виден и после дедлайна, когда price_monday = price
        p = prices.price_path("F", None, [(1, 30)], 2)
        self.assertEqual((p.price_prev, p.price_monday, p.price), (5400, 6200, 6200))
        p = prices.price_path("F", None, [], 0)
        self.assertEqual(p.price_prev, p.price_monday)

    def test_cap_500_from_tour_5(self):
        p = prices.price_path("D", None, [(5, 30), (5, 25)], 5)
        self.assertEqual(p.price, 4800 + 500)
        p = prices.price_path("F", (48, 9.0), [(6, 0), (6, 0), (6, 0)], 6)
        self.assertEqual(p.price, p.price_monday - 500)

    def test_cap_counts_from_monday_not_from_previous_protocol(self):
        # два протокола в одном туре: предел от стоимости на дедлайн, а не от стоимости после первого
        one = prices.price_path("F", None, [(6, 25)], 6)
        two = prices.price_path("F", None, [(6, 25), (6, 25)], 6)
        self.assertEqual((one.price, two.price), (5400 + 500, 5400 + 500))

    def test_who_did_not_play_does_not_get_cheaper(self):
        # сильный прошлый сезон, слабый старт: в туре без матчей стоимость стоит на месте, хоть формула ниже
        played = [(1, 0)] * 4 + [(2, 0)] * 4
        p3 = prices.price_path("F", (45, 8.0), played, 3)
        p5 = prices.price_path("F", (45, 8.0), played, 5)
        self.assertLess(p5.target, p5.price)
        self.assertEqual(p3.price, p5.price)
        self.assertEqual(p5.mondays[3], p5.mondays[5])

    def test_decay_matches_manual(self):
        lam = 0.5 ** (1 / 20)
        p = prices.price_path("D", (30, 3.0), [(1, 8), (1, 2)], 1)
        wpr, wb, sp, n = 15.0, 10.0, 0.0, 0.0
        for x in (8, 2):
            wpr, wb, sp, n = wpr * lam, wb * lam, sp * lam + x, n * lam + 1
        q = (wpr * 3.3 + sp + wb * 2.26) / (wpr + n + wb)
        self.assertAlmostEqual(p.q, q)

    def test_no_decay_is_plain_formula(self):
        p = prices.price_path("F", (20, 5.0), [(1, 7), (2, 3)], 2, decay_half=None)
        self.assertAlmostEqual(p.q, (10 * 5.5 + 10 + 10 * 3.44) / (10 + 2 + 10))

    def test_twenty_matches_back_weigh_half(self):
        lam = 0.5 ** (1 / rules.DECAY_HALF)
        self.assertAlmostEqual(lam ** 20, 0.5)


class Statuses(unittest.TestCase):
    CLUB = ["m1", "m2", "m3", "m4", "m5", "m6"]

    def test_rest_after_four_missed(self):
        self.assertEqual(prices.rest_status(self.CLUB, {"m1", "m2"}), "rest")
        self.assertEqual(prices.rest_status(self.CLUB, {"m3"}), "ok")
        self.assertEqual(prices.rest_status(self.CLUB[:3], set()), "ok")   # меньше четырёх матчей клуба

    def test_form_only_good(self):
        self.assertTrue(prices.in_form("F", 5400, [2, 8, 9, 7]))       # обещание 3,44
        self.assertFalse(prices.in_form("F", 5400, [4, 4, 4, 4]))
        self.assertFalse(prices.in_form("F", 5400, [9, 9]))            # мало матчей
        self.assertTrue(prices.in_form("F", 5400, [0, 0, 9, 9, 9, 9]))  # только последние четыре


if __name__ == "__main__":
    unittest.main()

"""Правила менеджера «Звена» (контракт, раздел 3): состав, продажа, очки тура, автозамены, ступени."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from zveno import manager, rules  # noqa: E402
from zveno.manager import (apply_autosubs, autopilot_pick, fee_options, mission_done, regroup_steps,  # noqa: E402
                           sale_price, tour_score, validate_squad)


def player(sid, slot, club, price, tour3=None, status="ok", name=None):
    tours = {}
    if tour3 is not None:
        ids = [f"m{i}" for i in range(len(tour3))] if not isinstance(tour3, dict) else list(tour3)
        pts = list(tour3.values()) if isinstance(tour3, dict) else tour3
        tours["3"] = {"m": pts, "best2": sum(sorted(pts, reverse=True)[:2]), "ids": ids}
    return {"id": sid, "pid": None if slot == "G" else int(sid[2:]), "slot": slot, "name": name or sid,
            "club": club, "number": 1, "price": price, "promise": 3.0, "form": False, "status": status,
            "new": False, "price_monday": price, "price_prev": price, "tours": tours}


CLUBS = ["ryazan-vdv", "ermak", "polet", "samara", "sokol", "tambov", "proton", "rostov"]
POOL = [
    player("g:kaluga", "G", "kaluga", 8800, {"k1": 9, "k2": 4}),
    player("g:ryazan-vdv", "G", "ryazan-vdv", 8800, {"a1": 9, "a2": 4}),
    player("g:ermak", "G", "ermak", 6500, {"b1": 5}),
    player("g:polet", "G", "polet", 6000, []),
    player("p:11", "D", "ryazan-vdv", 7000, {"a1": 5, "a2": 2}),
    player("p:12", "D", "ermak", 5000, {"b1": 3}),
    player("p:13", "D", "polet", 4800, []),
    player("p:14", "D", "samara", 4500, {"c1": 2}),
    player("p:15", "D", "sokol", 4000, {"d1": 1}),
    player("p:21", "F", "ryazan-vdv", 9000, {"a1": 10, "a2": 3}),
    player("p:22", "F", "ryazan-vdv", 7000, {"a1": 8, "a2": 6}),
    player("p:23", "F", "ermak", 6000, {"b1": 4}),
    player("p:24", "F", "polet", 5000, []),
    player("p:25", "F", "samara", 5000, {"c1": 7, "c2": 1, "c3": 0, "c4": 5}),
    player("p:26", "F", "sokol", 4500, {"d1": 2}),
    player("p:27", "F", "tambov", 4500, {"e1": 6}),
    player("p:28", "F", "proton", 4500, {"f1": 3}),
    # не в составе: для автопилота и лимитов
    player("p:31", "F", "rostov", 8000, {"g1": 5}),
    player("p:32", "F", "ryazan-vdv", 8500, {"a1": 1}),
    player("p:33", "F", "tambov", 12000, {"e1": 1}, status="rest"),
    player("p:34", "F", "rostov", 6000, {"g1": 5}),
    player("p:35", "D", "rostov", 4000, {"g1": 1}),
]
LINEUP = {"G": "g:kaluga",
          "L1": {"F": ["p:21", "p:22", "p:24"], "D": ["p:11", "p:13"]},
          "L2": {"F": ["p:25", "p:26", "p:27"], "D": ["p:14", "p:15"]}}
BENCH = ["g:ermak", "p:12", "p:23", "p:28"]
SQUAD = {"lineup": LINEUP, "bench": BENCH}
COST = sum(p["price"] for p in POOL if p["id"] in manager.lineup_ids(LINEUP) + BENCH)
MATCHES = {"matches": [
    {"id": "a1", "date": "2026-10-27", "tour": 3, "home": "ryazan-vdv", "away": "x",
     "goals": [{"team": "ryazan-vdv", "author": "p:21", "assists": ["p:22", "p:11"]},
               {"team": "ryazan-vdv", "author": "p:22", "assists": ["p:32"]}]},
    {"id": "a2", "date": "2026-10-29", "tour": 3, "home": "x", "away": "ryazan-vdv",
     "goals": [{"team": "ryazan-vdv", "author": "p:22", "assists": ["p:21"]}]},
    {"id": "c1", "date": "2026-10-27", "tour": 3, "home": "samara", "away": "y",
     "goals": [{"team": "samara", "author": "p:25", "assists": ["p:14"]}]},
]}


class Squad(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_squad(SQUAD, POOL, 100_000), [])
        self.assertEqual(COST, 86_100)

    def test_budget(self):
        errors = validate_squad(SQUAD, POOL, 85_000)
        self.assertEqual(errors, ["Не хватает льдинок: наклейки стоят 86 100, есть 85 000"])

    def test_three_from_club_with_gates(self):
        lineup = {**LINEUP, "G": "g:ryazan-vdv"}   # три полевых «Рязани-ВДВ» и её ворота
        errors = validate_squad({"lineup": lineup, "bench": ["g:kaluga", *BENCH[1:]]}, POOL, 100_000)
        self.assertEqual(len(errors), 1)
        self.assertIn("«Рязань-ВДВ»", errors[0])
        self.assertIn("ворота тоже считаются", errors[0])

    def test_over_limit_after_transfer_is_kept(self):
        lineup = {**LINEUP, "L1": {"F": ["p:21", "p:22", "p:32"], "D": ["p:11", "p:13"]}}
        squad = {"lineup": lineup, "bench": BENCH}
        owned = set(manager.lineup_ids(lineup) + BENCH)
        self.assertEqual(validate_squad(squad, POOL, 0, owned=owned), [])
        owned.discard("p:32")            # брать четвёртого из клуба нельзя
        self.assertEqual(len(validate_squad(squad, POOL, 100_000, owned=owned)), 1)

    def test_owned_are_not_paid_again(self):
        owned = set(manager.lineup_ids(LINEUP) + BENCH) - {"p:28"}
        self.assertEqual(validate_squad(SQUAD, POOL, 4500, owned=owned), [])
        self.assertEqual(len(validate_squad(SQUAD, POOL, 4400, owned=owned)), 1)

    def test_slot(self):
        lineup = {**LINEUP, "L2": {"F": ["p:25", "p:26", "p:35"], "D": ["p:14", "p:15"]}}
        bench = ["g:ermak", "p:12", "p:23", "p:27"]
        errors = validate_squad({"lineup": lineup, "bench": bench}, POOL, 100_000)
        self.assertEqual(errors, ["«p:35» — защитник, а место — для нападающего"])

    def test_structure_and_duplicates(self):
        errors = validate_squad({"lineup": {"L1": {"F": ["p:21"], "D": []}, "L2": LINEUP["L2"]},
                                 "bench": ["g:ermak", "p:12", "p:21"]}, POOL, 100_000)
        self.assertIn("В основе нужны ворота клуба", errors)
        self.assertIn("В звене 1 нужно 3 нападающих и 2 защитника", errors)
        self.assertIn("В запасе нужно четверо: ворота клуба, защитник и два нападающих — по порядку", errors)
        self.assertIn("«p:21» стоит в составе дважды", errors)

    def test_unknown_or_hidden(self):
        bench = ["g:ermak", "p:12", "p:23", "p:99"]
        self.assertEqual(validate_squad({"lineup": LINEUP, "bench": bench}, POOL, 100_000), ["Наклейки p:99 нет в пуле"])


class Sale(unittest.TestCase):
    def test_half_of_gain(self):
        self.assertEqual(sale_price(5000, 5600, "ok"), 5300)
        self.assertEqual(sale_price(5000, 5150, "ok"), 5000)     # шаг 100 вниз

    def test_gain_capped_at_500(self):
        self.assertEqual(sale_price(5000, 7000, "ok"), 5500)
        self.assertEqual(sale_price(5000, 6000, "ok"), 5500)

    def test_cheaper_goes_at_current(self):
        self.assertEqual(sale_price(5000, 4700, "ok"), 4700)

    def test_resting_goes_at_the_larger(self):
        self.assertEqual(sale_price(5000, 4700, "rest"), 5000)
        self.assertEqual(sale_price(5000, 5600, "rest"), 5300)

    def test_hidden_goes_at_the_larger_of_bought_and_current(self):
        self.assertEqual(sale_price(5000, 4700, "ok", hidden=True), 5000)
        self.assertEqual(sale_price(5000, 6400, "ok", hidden=True), 6400)


class Fees(unittest.TestCase):
    def test_options(self):
        self.assertEqual(fee_options(5, 1000, 0), ["points", "ice"])
        self.assertEqual(fee_options(18, 600, 1), ["points", "ice"])
        self.assertEqual(fee_options(5, 599, 0), ["points"])          # на 600 ❄ нужен банк
        self.assertEqual(fee_options(19, 5000, 0), ["points"])        # туры 19–21 — только очки
        self.assertEqual(fee_options(5, 5000, 2), [])                 # не больше двух платных


class Mission(unittest.TestCase):
    def test_new_club(self):
        album = {"kaluga", "ryazan-vdv", "polet", "samara", "sokol", "tambov"}
        self.assertFalse(mission_done(LINEUP, album, POOL))
        self.assertTrue(mission_done(LINEUP, album - {"tambov"}, POOL))
        self.assertTrue(mission_done({**LINEUP, "G": "g:ermak"}, album, POOL))   # ворота — тоже клуб

    def test_album_clubs(self):
        self.assertEqual(manager.album_clubs(LINEUP, POOL),
                         {"kaluga", "ryazan-vdv", "polet", "samara", "sokol", "tambov"})

    def test_pool_is_needed_for_players(self):
        with self.assertRaises(TypeError):
            mission_done(LINEUP, set())


class Autopilot(unittest.TestCase):
    def test_best_affordable_same_slot(self):
        squad = {**SQUAD, "bought": {"p:24": 5000}}
        # 12 000 «отдыхает», 8 500 — четвёртый из «Рязани-ВДВ», дальше 8 000
        self.assertEqual(autopilot_pick("p:24", squad, POOL, 3500), "p:31")
        self.assertEqual(autopilot_pick("p:24", squad, POOL, 1000), "p:34")
        self.assertIsNone(autopilot_pick("p:24", squad, POOL, -1000))

    def test_goalie_and_bench(self):
        self.assertEqual(autopilot_pick("g:ermak", SQUAD, POOL, 0), "g:polet")
        self.assertIsNone(autopilot_pick("p:77", SQUAD, POOL, 10_000))


class Autosubs(unittest.TestCase):
    def test_zero_matches_replaced_by_bench_of_same_slot(self):
        new, subs = apply_autosubs(LINEUP, BENCH, 3, POOL)
        # p:24 и p:13 без матчей: первый нападающий запаса p:23 и защитник p:12 встают на их места в звене 1
        self.assertEqual(subs, [("p:13", "p:12"), ("p:24", "p:23")])
        self.assertEqual(new["L1"], {"F": ["p:21", "p:22", "p:23"], "D": ["p:11", "p:12"]})
        self.assertEqual(LINEUP["L1"]["F"], ["p:21", "p:22", "p:24"])   # исходный состав не меняется

    def test_bench_without_matches_does_not_come_in(self):
        bench = ["g:ermak", "p:13", "p:24", "p:28"]
        lineup = {**LINEUP, "L1": {"F": ["p:21", "p:22", "p:23"], "D": ["p:11", "p:12"]},
                  "L2": {"F": ["p:25", "p:26", "p:31"], "D": ["p:14", "p:15"]}}
        new, subs = apply_autosubs(lineup, bench, 4, POOL)     # в туре 4 матчей нет ни у кого
        self.assertEqual((new, subs), (lineup, []))

    def test_goalie_sub(self):
        new, subs = apply_autosubs({**LINEUP, "G": "g:polet"}, BENCH, 3, POOL)
        self.assertEqual((new["G"], subs[0]), ("g:ermak", ("g:polet", "g:ermak")))


class Score(unittest.TestCase):
    def test_two_best_of_four(self):
        s = tour_score(LINEUP, "p:21", "p:22", 3, POOL, MATCHES)
        self.assertEqual(s.by_id["p:25"]["matches"], [7, 1, 0, 5])
        self.assertEqual(s.by_id["p:25"]["best2"], 12)

    def test_synergy_in_managers_line(self):
        s = tour_score(LINEUP, "p:27", None, 3, POOL, MATCHES)
        # a1: гол p:21 с передачей p:22 и p:11 — все в звене 1: по +1 каждому. p:32 не в составе.
        # a2: гол p:22 с передачей p:21 — ещё по +1. У p:21 [10+1, 3+1] → 15, без сыгранности 13
        self.assertEqual((s.by_id["p:21"]["best2"], s.by_id["p:21"]["synergy"]), (13, 2))
        self.assertEqual(s.by_id["p:22"]["synergy"], 2)
        self.assertEqual(s.by_id["p:11"]["synergy"], 1)
        # c1: p:25 и p:14 — оба в звене 2
        self.assertEqual((s.by_id["p:25"]["synergy"], s.by_id["p:14"]["synergy"]), (1, 1))

    def test_no_synergy_across_lines(self):
        lineup = {**LINEUP, "L1": {"F": ["p:21", "p:25", "p:24"], "D": ["p:11", "p:13"]},
                  "L2": {"F": ["p:22", "p:26", "p:27"], "D": ["p:14", "p:15"]}}
        s = tour_score(lineup, "p:27", None, 3, POOL, MATCHES)
        self.assertEqual(s.by_id["p:21"]["synergy"], 1)     # только с p:11 в звене 1
        self.assertEqual(s.by_id["p:22"]["synergy"], 0)

    def test_captain_double(self):
        s = tour_score(LINEUP, "p:25", "p:21", 3, POOL, MATCHES)
        self.assertEqual((s.by_id["p:25"]["mult"], s.by_id["p:21"]["mult"]), (2, 1))
        self.assertEqual(s.by_id["p:25"]["points"], (12 + 1) * 2)

    def test_assistant_doubles_when_captain_did_not_play(self):
        s = tour_score(LINEUP, "p:24", "p:27", 3, POOL, MATCHES)
        self.assertEqual((s.by_id["p:24"]["mult"], s.by_id["p:27"]["mult"]), (1, 2))

    def test_total_and_penalty(self):
        s = tour_score(LINEUP, "p:21", "p:22", 3, POOL, MATCHES, penalty=8)
        self.assertEqual(s.total, sum(v["points"] for v in s.by_id.values()) - 8)
        self.assertEqual(s.penalty, 8)
        self.assertEqual(s.by_id["g:kaluga"], {"matches": [9, 4], "best2": 13, "synergy": 0, "mult": 1,
                                                   "points": 13})

    def test_autosubs_inside_score(self):
        s = tour_score(LINEUP, "p:21", "p:22", 3, POOL, MATCHES, bench=BENCH)
        self.assertEqual(s.subs, [("p:13", "p:12"), ("p:24", "p:23")])
        self.assertIn("p:23", s.by_id)
        self.assertNotIn("p:24", s.by_id)
        self.assertEqual(len(s.by_id), 11)

    def test_ids_from_matches_when_pool_has_none(self):
        pool = [dict(p, tours={"3": {"m": p["tours"]["3"]["m"], "best2": p["tours"]["3"]["best2"]}})
                if p["tours"] else p for p in POOL]
        matches = {"matches": [dict(m, played=["p:21", "p:22", "p:11"]) for m in MATCHES["matches"][:2]]}
        s = tour_score(LINEUP, "p:25", None, 3, pool, matches)
        self.assertEqual(s.by_id["p:21"]["synergy"], 2)


class Steps(unittest.TestCase):
    def test_first_month_by_points(self):
        pts = {f"m{i:02d}": 100 - i for i in range(30)}
        out = regroup_steps(pts, {}, set())
        self.assertEqual(out["m00"], (0, 1))
        self.assertEqual(out["m19"], (0, 1))
        self.assertEqual(out["m20"], (1, 1))

    def test_caps_and_box(self):
        pts = {f"m{i:03d}": 1000 - i for i in range(300)}
        out = regroup_steps(pts, {}, set())
        count = {s: sum(1 for v in out.values() if v[0] == s) for s in range(5)}
        self.assertEqual(count, {0: 20, 1: 40, 2: 80, 3: 0, 4: 160})
        self.assertEqual(max(g for s, g in out.values() if s == 4), 8)

    def test_third_league_over_400(self):
        pts = {f"m{i:03d}": 1000 - i for i in range(450)}
        out = regroup_steps(pts, {}, set())
        self.assertEqual(sum(1 for v in out.values() if v[0] == 3), 160)
        self.assertEqual(sum(1 for v in out.values() if v[0] == 4), 150)

    def test_tail_joins_previous_group(self):
        pts = {f"m{i:02d}": 100 - i for i in range(20 + 25)}
        out = regroup_steps(pts, {}, set())
        self.assertEqual({g for s, g in out.values() if s == 1}, {1})   # 25 во Первой: хвост 5 — в ту же группу

    def test_top_four_of_group_go_up(self):
        prev = {f"m{i:03d}": (0 if i < 20 else 1 if i < 60 else 2 if i < 140 else 4, 1) for i in range(160)}
        pts = {m: 10 for m in prev}
        for m in ("m150", "m151", "m152", "m153", "m154"):   # лучшие месяца в Коробке, но очков мало для Высшей
            pts[m] = 11
        out = regroup_steps(pts, prev, set())
        self.assertTrue(all(out[m][0] <= 2 for m in ("m150", "m151", "m152", "m153")))

    def test_box_top_four_rise_at_least_one_step(self):
        prev = {f"m{i:03d}": (0 if i < 20 else 1 if i < 60 else 2 if i < 140 else 4, 1) for i in range(160)}
        pts = {m: 100 if prev[m][0] < 4 else 0 for m in prev}
        for i, m in enumerate(("m140", "m141", "m142", "m143", "m144")):
            pts[m] = 5 - i   # в Коробке лучшие, но в общем ранге внизу
        out = regroup_steps(pts, prev, set())
        self.assertEqual([out[m][0] for m in ("m140", "m141", "m142", "m143")], [2, 2, 2, 2])
        self.assertEqual(out["m144"][0], 4)

    def test_inactive_stay(self):
        prev = {f"m{i:02d}": (0 if i < 20 else 1, 1) for i in range(40)}
        pts = {m: 100 - int(m[1:]) for m in prev}
        pts["m05"] = 0
        out = regroup_steps(pts, prev, {"m05", "m30"})
        self.assertEqual((out["m05"][0], out["m30"][0]), (0, 1))     # не заходили — не двигаются
        # в Высшей: 19 лучших по очкам, m05 и четверо лучших группы Первой лиги — как в модели
        self.assertEqual(sorted(m for m, v in out.items() if v[0] == 0),
                         sorted([f"m{i:02d}" for i in range(20)] + ["m20", "m21", "m22", "m23"]))
        self.assertEqual({v[1] for v in out.values() if v[0] == 0}, {1})   # хвост 4 — в ту же группу

    def test_newcomers_to_box(self):
        prev = {f"m{i:02d}": (0, 1) for i in range(20)}
        pts = {**{m: 1 for m in prev}, "new": 999}
        out = regroup_steps(pts, prev, set())
        self.assertEqual(out["new"], (rules.STEP_BOX, 1))


if __name__ == "__main__":
    unittest.main()

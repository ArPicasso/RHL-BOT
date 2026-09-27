"""Очки «Звена» за матч (ADR-014, раздел 4) на реальных протоколах и ловушках протокола."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import league  # noqa: E402
from zveno import points  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def load(name: str, gid: int) -> dict:
    """Протокол, как он лежит в results.json: через JSON, со списками вместо кортежей."""
    p = league.parse_protocol((FIX / name).read_text(encoding="utf-8"), gid)
    return json.loads(json.dumps(p.to_json(), ensure_ascii=False))


REGULAR = load("protocol_900942_regular.html", 900942)    # Рязань-ВДВ — Белгород 6:1
OVERTIME = load("protocol_901016_ot.html", 901016)        # Самара — Рязань-ВДВ 5:6 ОТ
SHOOTOUT = load("protocol_901033_shootout.html", 901033)  # Кристалл — Рязань-ВДВ 4:3 Б


def by_name(m: points.MatchPoints, name: str) -> dict:
    return next(v for v in m.skaters.values() if v["name"] == name)


class Skaters(unittest.TestCase):
    def test_every_line_of_the_table(self):
        # Колыхалов: заявка 1 + победа 1 + гол 5 + передача 3 + «+/-» 3 + 2 броска 1 + «ШП» 2
        m = points.match_points(REGULAR, "ryazan-vdv", "belgorod")
        k = by_name(m, "Колыхалов Кирилл")
        self.assertEqual((k["pts"], k["slot"], k["club"], k["number"]), (16, "F", "ryazan-vdv", 91))

    def test_defenceman_goal_is_six(self):
        row = {"role": "D", "goals": 1, "assists": 0, "plus_minus": 0, "shots": 1}
        self.assertEqual(points.skater_points(row, won=False, big=False), 1 + 6)
        row["role"] = "F"
        self.assertEqual(points.skater_points(row, won=False, big=False), 1 + 5)

    def test_shootout_winner_is_not_a_goal(self):
        # Шафеев: «Ш» = 0, «РБ» = 1 → +2 как решающий, а не +5 за гол
        m = points.match_points(SHOOTOUT, "kristall", "ryazan-vdv")
        row = next(x for x in SHOOTOUT["lineups"] if x["player"]["name"] == "Шафеев Данат")
        self.assertEqual((row["goals"], row["so_winner"]), (0, 1))
        expected = 1 + 1 + 3 * row["assists"] + row["plus_minus"] + row["shots"] // 2 + 2
        self.assertEqual(by_name(m, "Шафеев Данат")["pts"], expected)

    def test_decider_once_per_match(self):
        row = {"role": "F", "goals": 1, "gwg": 1, "so_winner": 1}
        self.assertEqual(points.skater_points(row, won=True, big=False), 1 + 1 + 5 + 2)

    def test_floor_zero(self):
        row = {"role": "D", "plus_minus": -5}
        self.assertEqual(points.skater_points(row, won=False, big=False), 0)

    def test_big_penalty_zeroes_match_but_counts_as_played(self):
        p = copy.deepcopy(REGULAR)
        k = next(x for x in p["lineups"] if x["player"]["number"] == 91 and x["team"] == "home")
        p["penalties"].append({"time": "50:00", "team": "home", "player": dict(k["player"]), "minutes": 10,
                               "reason": "грубость"})
        m = points.match_points(p, "ryazan-vdv", "belgorod")
        self.assertEqual(by_name(m, "Колыхалов Кирилл")["pts"], 0)
        self.assertIn(f"p:{k['player']['id']}", m.played())

    def test_minor_penalties_do_not_count(self):
        pens = [x for x in REGULAR["penalties"] if x["player"]]
        self.assertTrue(pens)
        row = next(x for x in REGULAR["lineups"] if points._same(x["player"], pens[0]["player"]))
        self.assertFalse(points.big_penalty(REGULAR, row["team"], row["player"]))

    def test_not_played_are_left_out(self):
        m = points.match_points(REGULAR, "ryazan-vdv", "belgorod")
        dnp = [x["player"]["id"] for x in REGULAR["lineups"] if x["role"] != "G" and not x["played"]]
        self.assertTrue(all(i not in m.skaters for i in dnp))

    def test_old_protocol_without_new_columns(self):
        p = copy.deepcopy(REGULAR)
        for k in p["lineups"]:
            for f in ("plus_minus", "shots", "so_winner"):
                k.pop(f)
        m = points.match_points(p, "ryazan-vdv", "belgorod")
        self.assertEqual(by_name(m, "Колыхалов Кирилл")["pts"], 1 + 1 + 5 + 3 + 2)


class Gates(unittest.TestCase):
    def test_regular(self):
        # 6:1: сыграли 2 + победа 3 + 14 сейвов 3 − пропущенная 2
        m = points.match_points(REGULAR, "ryazan-vdv", "belgorod")
        self.assertEqual(m.gates["ryazan-vdv"], 2 + 3 + 3 - 2)

    def test_shootout_win_by_score_not_by_goalie_column(self):
        # у обоих вратарей «В» = 0, а победа у ворот есть: 4:3 Б
        m = points.match_points(SHOOTOUT, "kristall", "ryazan-vdv")
        self.assertTrue(all(k["wins"] == 0 for k in SHOOTOUT["lineups"] if k["role"] == "G"))
        self.assertEqual(m.gates["kristall"], 2 + 3 + 39 // 4 - 2 * 3)

    def test_shootout_loss_without_winning_shot(self):
        # проиграли по буллитам 3:4 — пропущенных 3, решающий буллит не считается
        m = points.match_points(SHOOTOUT, "kristall", "ryazan-vdv")
        self.assertEqual(points.goals_against(SHOOTOUT, "away"), 3)
        self.assertEqual(m.gates["ryazan-vdv"], 2 + 22 // 4 - 2 * 3)

    def test_overtime_win_to_reliever_does_not_matter(self):
        # победа записана сменщику (6:21, ноль бросков); ворота получают её по счёту, сейвы — обоих
        m = points.match_points(OVERTIME, "samara", "ryazan-vdv")
        self.assertEqual(m.gates["ryazan-vdv"], 2 + 3 + (24 + 0) // 4 - 2 * 5)
        self.assertEqual(m.gates["samara"], max(0, 2 + (30 + 10) // 4 - 2 * 6))

    def test_floor_zero(self):
        p = {"home_score": 0, "away_score": 8, "decision": "", "goals": [{"period": "1", "team": "away",
             "author": {"id": 1, "number": 9, "name": "А"}, "assists": []}], "lineups": [], "penalties": []}
        self.assertEqual(points.gate_points(p, "home"), 0)

    def test_shutout_in_game_and_overtime(self):
        p = {"home_score": 0, "away_score": 1, "decision": "Б", "lineups": [], "penalties": [],
             "goals": [{"period": "РБ", "team": "away", "author": {"id": 1}, "assists": []}]}
        self.assertEqual(points.gate_points(p, "home"), 2 + 5)        # «сухарь», хотя проиграли
        self.assertEqual(points.gate_points(p, "away"), 2 + 3 + 5)

    def test_goalie_goal_and_assist(self):
        p = copy.deepcopy(REGULAR)
        keeper = next(k for k in p["lineups"] if k["team"] == "home" and k["role"] == "G" and k["played"])
        keeper["assists"] = 1
        p["goals"][1]["author"] = dict(keeper["player"])
        base = points.gate_points(REGULAR, "home")
        self.assertEqual(points.gate_points(p, "home"), base + 5 + 3)


class Matches(unittest.TestCase):
    def test_shootout_goals_left_out(self):
        m = points.match_points(SHOOTOUT, "kristall", "ryazan-vdv")
        self.assertEqual(len(m.goals), 6)
        self.assertEqual(sum(1 for g in SHOOTOUT["goals"] if g["period"] == "РБ"), 1)

    def test_goal_authors_and_assists_by_id(self):
        m = points.match_points(REGULAR, "ryazan-vdv", "belgorod")
        self.assertEqual(m.goals[0], {"team": "belgorod", "author": 44596,
                                      "assists": [REGULAR["goals"][0]["assists"][0]["id"]]})

    def test_technical_defeat_has_no_points(self):
        p = {"home_score": 5, "away_score": 0, "decision": "", "goals": [], "lineups": [], "penalties": []}
        m = points.match_points(p, "a", "b")
        self.assertTrue(m.technical)
        self.assertEqual(m.played(), [])

    def test_protocol_without_lineups(self):
        # полевые матч не сыграли, ворота сыграли, победа по счёту
        p = copy.deepcopy(REGULAR)
        p["lineups"] = []
        m = points.match_points(p, "ryazan-vdv", "belgorod")
        self.assertEqual(m.skaters, {})
        self.assertEqual(m.gates["ryazan-vdv"], 2 + 3 - 2)
        self.assertEqual(sorted(m.played()), ["g:belgorod", "g:ryazan-vdv"])

    def test_best_two(self):
        self.assertEqual(points.best_sum([3, 9, 0, 7]), 16)
        self.assertEqual(points.best_sum([4]), 4)
        self.assertEqual(points.best_sum([]), 0)


if __name__ == "__main__":
    unittest.main()

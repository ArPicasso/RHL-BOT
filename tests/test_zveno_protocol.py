"""Новые колонки протокола для «Звена» (ADR-014, раздел 13). Фикстуры — реальные протоколы НМХЛ 25/26."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import league  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def protocol(name: str, gid: int) -> league.Protocol:
    return league.parse_protocol((FIX / name).read_text(encoding="utf-8"), gid)


def row(p: league.Protocol, team: str, number: int) -> league.Skater:
    return next(x for x in p.lineups if x.team == team and x.player.number == number)


class RegularSkaterColumns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = protocol("protocol_900942_regular.html", 900942)

    def test_plus_minus_shots_winning_goal(self):
        k = row(self.p, "home", 91)   # Колыхалов: гол, передача, победная
        self.assertEqual((k.goals, k.assists, k.plus_minus, k.shots, k.gwg, k.so_winner), (1, 1, 3, 2, 1, 0))

    def test_power_play_goal(self):
        k = row(self.p, "home", 78)   # Пащенко забил в большинстве
        self.assertEqual((k.goals, k.ppg, k.shg, k.otg), (1, 1, 0, 0))

    def test_negative_plus_minus(self):
        self.assertEqual(row(self.p, "away", 15).plus_minus, -1)

    def test_goalie_columns(self):
        g = row(self.p, "home", 20)
        self.assertEqual((g.wins, g.losses, g.so_games, g.shutouts, g.saves), (1, 0, 0, 0, 14))
        self.assertEqual(row(self.p, "away", 90).losses, 1)
        # у вратаря «БВ» — броски по воротам, у полевых отдельное поле shots остаётся нулём
        self.assertEqual((g.shots_against, g.shots), (15, 0))


class OvertimeColumns(unittest.TestCase):
    """901016: Самара — Рязань-ВДВ 5:6 ОТ. Победу записали сменщику, который не отразил ни броска."""

    @classmethod
    def setUpClass(cls):
        cls.p = protocol("protocol_901016_ot.html", 901016)

    def test_overtime_goal(self):
        k = row(self.p, "away", 68)   # Михеев: два гола в большинстве, один в ОТ, он же победный
        self.assertEqual((k.goals, k.ppg, k.otg, k.gwg, k.plus_minus), (2, 2, 1, 1, -2))

    def test_win_goes_to_reliever(self):
        starter, reliever = row(self.p, "away", 20), row(self.p, "away", 81)
        self.assertEqual((starter.wins, starter.saves, starter.toi), (0, 24, "54:57"))
        self.assertEqual((reliever.wins, reliever.saves, reliever.toi), (1, 0, "6:21"))
        self.assertEqual(row(self.p, "home", 72).losses, 1)


class ShootoutColumns(unittest.TestCase):
    """901033: 4:3 Б. У обоих вратарей «В» = 0, решающий буллит — «РБ», а не «Ш»."""

    @classmethod
    def setUpClass(cls):
        cls.p = protocol("protocol_901033_shootout.html", 901033)

    def test_no_goalie_wins_in_shootout(self):
        played = [x for x in self.p.lineups if x.role == "G" and x.played]
        self.assertEqual(len(played), 3)
        self.assertTrue(all(x.wins == 0 for x in played))
        self.assertEqual({x.team: x.so_games for x in played if x.toi != "1:09"}, {"home": 1, "away": 1})

    def test_shootout_winner_is_not_a_goal(self):
        k = row(self.p, "home", 94)   # Шафеев
        self.assertEqual((k.goals, k.so_winner, k.shots), (0, 1, 2))
        self.assertEqual(self.p.goals[-1].author.name, "Шафеев Данат")

    def test_short_handed_goal(self):
        k = row(self.p, "home", 22)
        self.assertEqual((k.goals, k.shg, k.plus_minus), (1, 1, 2))


class OldResultsStillLoad(unittest.TestCase):
    def test_new_fields_roundtrip(self):
        p = protocol("protocol_901033_shootout.html", 901033)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "results.json"
            league.save_results({"1378": {str(p.n): p.to_json()}}, path)
            data = league.load_results(path)["1378"]["95"]
        k = next(x for x in data["lineups"] if x["player"]["number"] == 94 and x["team"] == "home")
        self.assertEqual((k["so_winner"], k["goals"], k["shots"]), (1, 0, 2))

    def test_old_file_without_new_fields(self):
        """results.json, записанный старым парсером: новых полей нет, файл читается как раньше."""
        p = protocol("protocol_900942_regular.html", 900942).to_json()
        new = {"plus_minus", "shots", "ppg", "shg", "otg", "so_winner", "wins", "losses", "so_games", "shutouts"}
        for k in p["lineups"]:
            for f in new:
                k.pop(f)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "results.json"
            path.write_text(json.dumps({"1378": {"4": p}}, ensure_ascii=False), encoding="utf-8")
            data = league.load_results(path)
        self.assertEqual(len(data["1378"]["4"]["lineups"]), 44)
        self.assertNotIn("plus_minus", data["1378"]["4"]["lineups"][0])

    def test_skater_defaults(self):
        s = league.Skater("home", "F", league.Player(9, "Иванов Иван"))
        self.assertEqual((s.plus_minus, s.shots, s.so_winner, s.wins, s.shutouts), (0, 0, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()

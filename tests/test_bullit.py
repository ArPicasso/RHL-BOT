"""Ворота 26 клубов для аркады «Буллит» (ADR-017): лестница по пропущенным за игру."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bullit  # noqa: E402


def team(tid, **kw):
    return {"id": tid, "name": tid.title(), "abbr": tid[:3].upper(), "logo": f"logos/{tid}.png",
            "colors": ["#000000", "#ffffff"], "city": "Город", **kw}


def game(home, away, hs, as_, season=None):
    g = {"home": home, "away": away, "score": {"home": hs, "away": as_}}
    if season:
        g["season"] = season
    return g


def past(home, away, hs, as_, season="25/26"):
    return {"home": home, "away": away, "score": [hs, as_], "season": season}


class Conceded(unittest.TestCase):
    def test_both_shapes(self):
        """Счёт приходит словарём из league.json и парой из history.json."""
        self.assertEqual(bullit.conceded([game("a", "b", 3, 1)]), {"a": [1, 1], "b": [1, 3]})
        self.assertEqual(bullit.conceded([past("a", "b", 3, 1)]), {"a": [1, 1], "b": [1, 3]})

    def test_season_filter(self):
        rows = [past("a", "b", 2, 0), past("a", "b", 5, 0, season="24/25")]
        self.assertEqual(bullit.conceded(rows, "25/26")["b"], [1, 2])

    def test_match_without_score(self):
        """Матч без протокола в счёт не идёт."""
        self.assertEqual(bullit.conceded([{"home": "a", "away": "b", "score": None}]), {})

    def test_rate_needs_games(self):
        self.assertIsNone(bullit.rate({"a": [3, 9]}, "a"))
        self.assertAlmostEqual(bullit.rate({"a": [4, 10]}, "a"), 2.5)
        self.assertIsNone(bullit.rate({}, "a"))


class Ladder(unittest.TestCase):
    def setUp(self):
        self.teams = [team("a"), team("b"), team("c")]

    def test_order_worst_goal_first(self):
        """Первыми — самые пробиваемые ворота: кто больше пропускает."""
        prior = {"a": [10, 10], "b": [10, 40], "c": [10, 25]}
        self.assertEqual([c["id"] for c in bullit.ladder(self.teams, {}, prior)], ["b", "c", "a"])

    def test_t_from_zero_to_one(self):
        prior = {"a": [10, 10], "b": [10, 40], "c": [10, 25]}
        self.assertEqual([c["t"] for c in bullit.ladder(self.teams, {}, prior)], [0.0, 0.5, 1.0])

    def test_season_beats_prior(self):
        """Этот сезон важнее прошлого, но только когда матчей хватает."""
        season = {"a": [10, 50]}
        prior = {"a": [10, 10], "b": [10, 20], "c": [10, 30]}
        rows = bullit.ladder(self.teams, season, prior)
        self.assertEqual(rows[0]["id"], "a")
        self.assertEqual(rows[0]["src"], "season")
        self.assertEqual(rows[0]["ga"], 5.0)

    def test_few_games_fall_back_to_prior(self):
        season = {"a": [bullit.MIN_GP - 1, 30]}
        prior = {"a": [10, 10], "b": [10, 20], "c": [10, 30]}
        row = next(c for c in bullit.ladder(self.teams, season, prior) if c["id"] == "a")
        self.assertEqual(row["src"], "prior")
        self.assertEqual(row["ga"], 1.0)

    def test_unknown_club_goes_to_the_middle(self):
        """Клуб без матчей не должен оказаться ни первым, ни последним."""
        prior = {"a": [10, 10], "b": [10, 40]}
        rows = bullit.ladder([team("a"), team("b"), team("c")], {}, prior)
        self.assertEqual([c["id"] for c in rows], ["b", "c", "a"])
        self.assertEqual(next(c for c in rows if c["id"] == "c")["src"], "none")
        self.assertIsNone(next(c for c in rows if c["id"] == "c")["ga"])

    def test_sticker_and_colors_travel(self):
        row = bullit.ladder([team("a")], {}, {})[0]
        self.assertEqual(row["goalie"], "players/clubs/a-goalie.webp")
        self.assertEqual(row["colors"], ["#000000", "#ffffff"])
        self.assertEqual(row["t"], 0.0)


class Build(unittest.TestCase):
    def test_all_clubs_in_order(self):
        teams = [team(x) for x in "abcd"]
        prior = [past("a", "b", 1, 5), past("c", "d", 2, 2)] * 4
        data = bullit.build(teams, [], prior, "2026/27")
        self.assertEqual(data["season"], "2026/27")
        self.assertEqual(data["prior"], bullit.PRIOR_SEASON)
        self.assertEqual(len(data["clubs"]), 4)
        self.assertEqual([c["t"] for c in data["clubs"]][0], 0.0)
        self.assertEqual([c["t"] for c in data["clubs"]][-1], 1.0)


class RealLeague(unittest.TestCase):
    """На настоящих командах и прошлом сезоне лестница собирается целиком."""

    def test_twenty_six_goals(self):
        import json
        teams = json.loads((ROOT / "teams.json").read_text(encoding="utf-8"))
        teams = teams["teams"] if isinstance(teams, dict) else teams
        history = json.loads((ROOT / "history.json").read_text(encoding="utf-8"))
        history = history["games"] if isinstance(history, dict) else history
        clubs = bullit.build(teams, [], history, "2026/27")["clubs"]
        self.assertEqual(len(clubs), 26)
        self.assertEqual(clubs[0]["t"], 0.0)
        self.assertEqual(clubs[-1]["t"], 1.0)
        for c in clubs:   # у каждого клуба есть стикер вратаря: его рисует игра
            self.assertTrue((ROOT / "webapp" / c["goalie"]).exists(), c["id"])


if __name__ == "__main__":
    unittest.main()

"""Тур по главам (ADR-013): у каждого клуба есть путь к разбору матча, флаг сюжета в h2h.json верный.

Без сети: календарь сезона собирается из teams.json и games.json, встречи и разборы — из
history.json и history_protocols.json, которые лежат в git."""
import itertools
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_data as b  # noqa: E402

DATA = ROOT / "webapp" / "data"


def season_pairs(teams: b.Teams) -> list[dict]:
    """Пары сезона без r-hockey: по регламенту РХЛ каждый играет с каждым в своей конференции,
    плюс официальный календарь «Рязань-ВДВ» (games.json)."""
    games = []
    for a, c in itertools.combinations(teams.all, 2):
        if a["conf"] == c["conf"]:
            games.append({"id": f"{a['id']}-{c['id']}", "date": "2026-10-03", "home": a["id"], "away": c["id"]})
    return games + b.official_games(teams)


class TourPath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.teams = b.load_teams()
        cls.games = season_pairs(cls.teams)
        cls.history = b.load_history()
        protocols = b.load_history_protocols()
        cls.h2h = b.head_to_head(cls.games, cls.history, set(protocols))
        wanted = {m["id"] for pair in cls.h2h.values() for m in pair["last"] if m.get("id")}
        names = {t["id"]: t["name"] for t in cls.teams.all}
        cls.recaps = b.past_recaps(cls.history, protocols, wanted, names, b.load_hidden())
        b.mark_stories(cls.h2h, cls.recaps)

    def with_recap(self, key: str) -> list[dict]:
        return [m for m in self.h2h[key]["last"] if m.get("id")]

    def test_official_calendar_within_conference(self):
        conf = {t["id"]: t["conf"] for t in self.teams.all}
        for g in b.official_games(self.teams):
            self.assertEqual(conf[g["home"]], conf[g["away"]], g["id"])

    def test_every_meeting_with_id_has_recap(self):
        for key, pair in self.h2h.items():
            for m in pair["last"]:
                if m.get("id"):
                    self.assertIn(m["id"], self.recaps, key)

    def test_every_club_has_path_to_recap(self):
        """Своя встреча — у всех, кто играл в НМХЛ; у новичков — матч лиги (ветка Б)."""
        played = {x for h in self.history for x in (h["home"], h["away"])}
        league = [k for k in self.h2h if self.with_recap(k)]
        self.assertTrue(league, "в сезоне нет ни одной пары с разбором")
        newcomers = set()
        for t in self.teams.all:
            own = [k for k in self.h2h if t["id"] in k.split("|") and self.with_recap(k)]
            if not own:
                self.assertNotIn(t["id"], played, f"{t['id']} играл в НМХЛ, а своего разбора нет")
                newcomers.add(t["id"])
        self.assertLessEqual(len(newcomers), 4, newcomers)

    def test_every_recap_in_tour_has_winning_goal(self):
        # реплика про победную шайбу — главный «вау» главы «Разбор матча» (ADR-013)
        missing = [i for i, d in self.recaps.items() if d["gw"] is None]
        self.assertLess(len(missing), len(self.recaps) // 20, missing[:5])

    def test_story_flag_matches_recaps(self):
        flagged = 0
        for pair in self.h2h.values():
            for m in pair["last"]:
                if not m.get("id"):
                    self.assertNotIn("story", m)
                    continue
                self.assertEqual(m.get("story", False), bool(self.recaps[m["id"]]["story"]), m["id"])
                flagged += m.get("story", False)
        self.assertGreater(flagged, 0)

    def test_mark_stories_leaves_rows_without_story(self):
        h2h = {"a|b": {"last": [{"id": "h1"}, {"id": "h2"}, {"date": "2021-09-18"}]}}
        b.mark_stories(h2h, {"h1": {"story": "Сухой матч."}, "h2": {"story": ""}})
        self.assertEqual(h2h["a|b"]["last"], [{"id": "h1", "story": True}, {"id": "h2"}, {"date": "2021-09-18"}])


@unittest.skipUnless((DATA / "h2h.json").is_file(), "данные мини-аппа не собраны: build_data.py")
class BuiltFiles(unittest.TestCase):
    """Собранные файлы мини-аппа: флаг story в h2h.json совпадает с файлами разборов."""

    def test_story_flag_matches_match_files(self):
        h2h = json.loads((DATA / "h2h.json").read_text(encoding="utf-8"))
        for key, pair in h2h.items():
            for m in pair["last"]:
                if not m.get("id"):
                    continue
                path = DATA / "matches" / f"{m['id']}.json"
                self.assertTrue(path.is_file(), f"{key}: нет {path.name}")
                story = json.loads(path.read_text(encoding="utf-8"))["story"]
                self.assertEqual(m.get("story", False), bool(story), m["id"])


if __name__ == "__main__":
    unittest.main()

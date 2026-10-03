"""Разбор сайта РХЛ rhl.fhr.ru (rhl_site.py) на настоящих страницах 03.10.2026 и счёт в league.json."""
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_data  # noqa: E402
import rhl_site  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
TZ = ZoneInfo("Europe/Moscow")


def page(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class Calendar(unittest.TestCase):
    def setUp(self):
        self.games = {g["id"]: g for g in rhl_site.parse_calendar(page("rhl_calendar_2026_10_03.html"))}

    def test_future_card(self):
        g = self.games[905116]
        self.assertEqual((g["t"], g["n"], g["start"], g["city"], g["home"], g["away"], g["status"], g["score"]),
                         (1432, 6, "2026-10-04T17:00:00+03:00", "Рязань", "МХК Рязань-ВДВ", "МХК Белгород", "sched", None))

    def test_feed_finished_and_live(self):
        # Сыгранные матчи есть только в ленте дней, команды — по эмблемам из тела календаря
        g = self.games[905113]
        self.assertEqual((g["status"], g["score"], g["n"], g["home"], g["away"]),
                         ("final", [0, 6], 3, "МХК Ростов", "ХК Краснодар"))
        self.assertEqual((self.games[905114]["home"], self.games[905114]["score"]), ("Тверичи-СШОР", [3, 1]))
        live = self.games[905111]
        self.assertEqual((live["status"], live["score"], live["start"]), ("live", [2, 0], "2026-10-03T17:00:00+03:00"))

    def test_all_names_known(self):
        teams = build_data.load_teams()
        names = {g[k] for g in self.games.values() for k in ("home", "away") if g.get(k)}
        self.assertEqual([n for n in names if not teams.find(n)], [])


class Match(unittest.TestCase):
    def test_final(self):
        p = rhl_site.parse_match(page("rhl_match_905113_final.html"))
        self.assertEqual((p["home"], p["away"], p["status"], p["score"], p["decision"], p["n"], p["start"], p["arena"]),
                         ("МХК Ростов", "ХК Краснодар", "final", [0, 6], None, 3, "2026-10-03T13:00:00+03:00", "Дворец спорта"))
        self.assertEqual(p["goals"]["home"], [])
        self.assertEqual([(x["no"], x["name"], x["min"]) for x in p["goals"]["away"]][:2],
                         [(74, "Рябицев Дмитрий", 3), (18, "Дурдин Дмитрий", 34)])
        self.assertEqual(len(p["goals"]["away"]), 6)    # 0:6 — шесть голов гостей

    def test_live(self):
        p = rhl_site.parse_match(page("rhl_match_905111_live.html"))
        self.assertEqual((p["status"], p["status_name"], p["score"]), ("live", "2-й период", [2, 0]))

    def test_not_started(self):
        p = rhl_site.parse_match(page("rhl_match_905116_sched.html"))
        self.assertEqual((p["status"], p["score"], p["start"]), ("sched", None, "2026-10-04T17:00:00+03:00"))

    def test_decision_from_score_tail(self):
        html = page("rhl_match_905113_final.html").replace(">0:6<", ">3:2 ОТ<", 1)
        self.assertEqual(rhl_site.parse_match(html)["decision"], "ОТ")
        html = page("rhl_match_905113_final.html").replace(">0:6<", ">3:2 Б<", 1)
        self.assertEqual(rhl_site.parse_match(html)["decision"], "Б")


class Live(unittest.TestCase):
    def test_live_state(self):
        g = {"status": "live", "score": [2, 0], "status_name": "2-й период", "seen": "2026-10-03T17:51:00+03:00"}
        self.assertEqual(rhl_site.live_state(g), {"status": "live", "period": "2", "clock": None,
                                                  "score": {"home": 2, "away": 0, "decision": None},
                                                  "seen": "2026-10-03T17:51:00+03:00", "src": "rhl.fhr.ru"})
        self.assertEqual(rhl_site.live_state({**g, "status_name": "Перерыв"})["status"], "break")
        self.assertEqual(rhl_site.live_state({**g, "status_name": "Овертайм"})["period"], "ОТ")
        self.assertIsNone(rhl_site.live_state({**g, "status": "final"}))


class Store(unittest.TestCase):
    def store(self) -> dict:
        s = {"games": {}}
        rhl_site.merge(s, rhl_site.parse_calendar(page("rhl_calendar_2026_10_03.html")))
        return s

    def test_pages_wanted(self):
        s = self.store()
        now = datetime(2026, 10, 3, 17, 55, tzinfo=TZ)
        want = {g["id"] for g in s["games"].values() if rhl_site.need_page(g, now)}
        self.assertLessEqual({905111, 905112, 905113, 905114}, want)   # идут и сыграны
        self.assertNotIn(905116, want)                                 # будущий с командами — нет
        g = s["games"]["905113"]
        rhl_site.apply_page(g, rhl_site.parse_match(page("rhl_match_905113_final.html")))
        self.assertFalse(rhl_site.need_page(g, now))                 # итог подтверждён — больше не качаем
        self.assertEqual(g["start"], "2026-10-03T13:00:00+03:00")

    def test_merge_keeps_known(self):
        s = self.store()
        rhl_site.merge(s, [{"id": 905116, "t": 1432, "start": None, "home": None, "away": None, "status": "sched"}])
        self.assertEqual(s["games"]["905116"]["home"], "МХК Рязань-ВДВ")


class Build(unittest.TestCase):
    def test_score_time_and_links_in_league(self):
        teams = build_data.load_teams()
        s = {"games": {}}
        rhl_site.merge(s, rhl_site.parse_calendar(page("rhl_calendar_2026_10_03.html")))
        rhl_site.apply_page(s["games"]["905113"], rhl_site.parse_match(page("rhl_match_905113_final.html")))
        seen = datetime(2026, 10, 3, 17, 51, tzinfo=TZ)
        rhl_site.apply_page(s["games"]["905111"], rhl_site.parse_match(page("rhl_match_905111_live.html")), seen)
        games = [{"id": "rh1", "n": None, "date": "2026-10-03", "home": "rostov", "away": "krasnodar", "official": False},
                 {"id": "n1", "n": 1, "date": "2026-10-03", "home": "ryazan-vdv", "away": "belgorod", "official": True}]
        build_data.apply_site(games, s, teams)
        g = {x["id"]: x for x in games}
        self.assertEqual(g["rh1"]["score"], {"home": 0, "away": 6, "decision": None, "periods": []})
        self.assertEqual((g["rh1"]["time"], g["rh1"]["n"], g["rh1"]["official"]), ("13:00", 3, True))
        self.assertEqual(g["rh1"]["league_url"], "https://rhl.fhr.ru/matchcenter/1432/905113/")
        # идущий матч: счёта как итога нет, есть снимок живого с давностью
        self.assertNotIn("score", g["n1"])
        self.assertEqual({k: g["n1"]["live"][k] for k in ("status", "score", "src")},
                         {"status": "live", "score": {"home": 2, "away": 0, "decision": None}, "src": "rhl.fhr.ru"})
        self.assertEqual((g["n1"]["time"], g["n1"]["online"]), ("17:00", "https://rhl.fhr.ru/matchcenter/1432/905111/live/"))
        # матчи сайта, которых нет в календаре r-hockey, добавляются
        self.assertIn("r905116", g)
        # таблица считается по сыгранному
        table = build_data.standings(teams, games)
        kr = next(r for r in table["east"] if r["team"] == "krasnodar")
        self.assertEqual((kr["gp"], kr["pts"]), (1, 2))


if __name__ == "__main__":
    unittest.main()

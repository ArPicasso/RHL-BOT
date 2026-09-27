"""Сборка данных «Звена»: приоры и связка, пул, матчи, туры, статус Пролога и рынка (контракт, раздел 2)."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_data  # noqa: E402
import build_zveno  # noqa: E402
import league  # noqa: E402
from zveno import build, names, prices, prior, rules  # noqa: E402
from zveno.rules import TZ  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
TEAMS = build_data.load_teams()
KOLYKHALOV = 41090


def load(name: str, gid: int, day: str) -> dict:
    p = league.parse_protocol((FIX / name).read_text(encoding="utf-8"), gid)
    p = json.loads(json.dumps(p.to_json(), ensure_ascii=False))
    p["date"] = day
    return p


def protocols() -> dict:
    """Три протокола 25/26, переложенные в 26/27: Пролог и два матча тура 1."""
    ps = [load("protocol_900942_regular.html", 900942, "2026-10-03"),
          load("protocol_901016_ot.html", 901016, "2026-10-13"),
          load("protocol_901033_shootout.html", 901033, "2026-10-15")]
    return {"1500": {str(p["n"]): p for p in ps}}


CALENDAR = [{"date": "2026-10-03", "home": "ryazan-vdv", "away": "belgorod"},
            {"date": "2026-10-13", "home": "samara", "away": "ryazan-vdv"},
            {"date": "2026-10-15", "home": "kristall", "away": "ryazan-vdv"},
            {"date": "2026-10-20", "home": "ryazan-vdv", "away": "ermak"},
            {"date": "2027-01-10", "home": "ermak", "away": "belgorod"}]


def msk(*a) -> datetime:
    return datetime(*a, tzinfo=TZ)


def season(results: dict, now: datetime, hidden=frozenset(), prior_data=None):
    protos, _ = build.regular_protocols(results, TEAMS.find_past)
    return build.build(protos, CALENDAR, TEAMS.all, now, prior_data or prior.load_prior(), None, hidden)


class RegularOnly(unittest.TestCase):
    def test_last_season_is_left_out(self):
        res = protocols()
        old = load("protocol_900942_regular.html", 900942, "2025-10-04")
        res["1378"] = {"4": old}
        protos, unmatched = build.regular_protocols(res, TEAMS.find_past)
        self.assertEqual([p["game_id"] for p, _, _ in protos], [900942, 901016, 901033])
        self.assertEqual(protos[2][1:], ("kristall", "ryazan-vdv"))
        self.assertEqual(unmatched, [])

    def test_playoff_dates_are_left_out(self):
        res = {"1600": {"1": load("protocol_900942_regular.html", 900942, "2027-03-25")}}
        self.assertEqual(build.regular_protocols(res, TEAMS.find_past)[0], [])


class Prolog(unittest.TestCase):
    def test_without_protocols(self):
        s = season({}, msk(2026, 10, 1, 12))
        t = s.tours
        self.assertEqual((t["status"], t["tour_next"], t["tour_now"], t["first_tour"], t["market_opened_at"]),
                         ("prolog", 1, None, None, None))
        self.assertEqual(len(t["tours"]), 21)
        self.assertEqual(t["tours"][0]["games"]["ryazan-vdv"], 2)
        self.assertEqual(t["tours"][11]["deadline"], "2027-01-10T09:00:00+03:00")   # первый матч после паузы
        self.assertEqual(t["clubs_with_protocol"], 0)
        self.assertEqual(s.pool["players"], [])
        self.assertEqual(s.matches["matches"], [])

    def test_pool_grows_with_protocols(self):
        s = season(protocols(), msk(2026, 10, 20, 12))
        self.assertEqual(s.tours["status"], "prolog")
        self.assertEqual(s.tours["clubs_with_protocol"], 4)
        ids = {p["id"] for p in s.pool["players"]}
        self.assertIn(f"p:{KOLYKHALOV}", ids)
        self.assertEqual({i for i in ids if i.startswith("g:")},
                         {"g:ryazan-vdv", "g:belgorod", "g:samara", "g:kristall"})


class Pool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = season(protocols(), msk(2026, 10, 20, 12))
        cls.by_id = {p["id"]: p for p in cls.s.pool["players"]}

    def test_contract_fields(self):
        p = self.by_id[f"p:{KOLYKHALOV}"]
        self.assertEqual(set(p), {"id", "pid", "slot", "name", "club", "number", "price", "promise", "form",
                                  "status", "new", "price_monday", "tours"})
        self.assertEqual((p["pid"], p["slot"], p["club"], p["number"]), (KOLYKHALOV, "F", "ryazan-vdv", 91))
        self.assertEqual(p["price"] % 100, 0)

    def test_tour_points_without_prolog(self):
        p = self.by_id[f"p:{KOLYKHALOV}"]
        self.assertEqual(list(p["tours"]), ["1"])          # Пролог — только в стоимость
        self.assertEqual(p["tours"]["1"]["ids"], ["901016"])
        self.assertEqual(p["tours"]["1"]["best2"], sum(p["tours"]["1"]["m"]))

    def test_gate(self):
        g = self.by_id["g:ryazan-vdv"]
        self.assertEqual((g["slot"], g["pid"], g["name"], g["status"]), ("G", None, "Ворота «Рязани-ВДВ»", "ok"))
        self.assertEqual(g["tours"]["1"], {"m": [1, 1], "best2": 2, "ids": ["901016", "901033"]})

    def test_price_monday_is_last_deadline(self):
        p = self.by_id[f"p:{KOLYKHALOV}"]
        pr = prior.load_prior()["skaters"][str(KOLYKHALOV)]
        start = prices.price_path("F", (pr["gp"], pr["ppg"]), [], 0).price
        self.assertEqual(p["new"], False)
        self.assertLessEqual(abs(p["price"] - p["price_monday"]), rules.PRICE_CAP_EARLY)
        self.assertLessEqual(abs(p["price_monday"] - start), rules.PRICE_CAP_EARLY)

    def test_new_club_gate_is_newbie(self):
        data = prior.load_prior()
        data = dict(data, gates={k: v for k, v in data["gates"].items() if k != "kristall"})
        s = season(protocols(), msk(2026, 10, 20, 12), prior_data=data)
        g = next(p for p in s.pool["players"] if p["id"] == "g:kristall")
        self.assertTrue(g["new"])

    def test_hidden_player_is_nowhere(self):
        s = season(protocols(), msk(2026, 10, 20, 12), hidden={KOLYKHALOV})
        self.assertNotIn(f"p:{KOLYKHALOV}", {p["id"] for p in s.pool["players"]})
        text = json.dumps(s.matches)
        self.assertNotIn(f"p:{KOLYKHALOV}", text)
        self.assertIn('"author": null', text)


class Matches(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = {x["id"]: x for x in season(protocols(), msk(2026, 10, 17, 12)).matches["matches"]}

    def test_tours_and_clubs(self):
        self.assertEqual((self.m["900942"]["tour"], self.m["901016"]["tour"]), (None, 1))
        self.assertEqual((self.m["901033"]["home"], self.m["901033"]["away"]), ("kristall", "ryazan-vdv"))

    def test_settled_after_three_days(self):
        self.assertEqual((self.m["901016"]["settled"], self.m["901033"]["settled"]), (True, False))

    def test_shootout_goals_are_not_goals(self):
        self.assertEqual(len(self.m["901033"]["goals"]), 6)
        g = self.m["901016"]["goals"][-1]
        self.assertEqual(g["team"], "ryazan-vdv")
        self.assertTrue(g["author"].startswith("p:"))

    def test_played_has_gates_and_skaters(self):
        played = self.m["901016"]["played"]
        self.assertIn("g:samara", played)
        self.assertIn(f"p:{KOLYKHALOV}", played)
        self.assertFalse(any(x.startswith("p:") and "None" in x for x in played))


class MarketOpens(unittest.TestCase):
    @staticmethod
    def all_clubs(day: str) -> dict:
        ids = [t["id"] for t in TEAMS.all]
        name = {t["id"]: t["name"] for t in TEAMS.all}
        games = {}
        for i in range(0, len(ids), 2):
            h, a = ids[i], ids[i + 1]
            games[str(i)] = {"game_id": 910000 + i, "n": i, "date": day, "home": name[h], "away": name[a],
                             "home_score": 2, "away_score": 1, "decision": "", "lineups": [], "penalties": [],
                             "goals": [{"period": "1", "team": t, "author": {"id": None, "number": 9, "name": "Х"},
                                        "assists": []} for t in ("home", "home", "away")]}
        return {"1500": games}

    def test_open_when_every_club_has_protocol(self):
        s = season(self.all_clubs("2026-10-10"), msk(2026, 10, 11, 12))
        t = s.tours
        self.assertEqual((t["status"], t["clubs_with_protocol"]), ("open", 26))
        self.assertEqual((t["market_opened_at"], t["first_tour"], t["tour_next"], t["tour_now"]),
                         ("2026-10-10T23:59:00+03:00", 1, 1, None))

    def test_tour_now_after_deadline(self):
        t = season(self.all_clubs("2026-10-10"), msk(2026, 10, 14, 12)).tours
        self.assertEqual((t["tour_now"], t["tour_next"]), (1, 2))

    def test_late_market_starts_with_next_monday(self):
        t = season(self.all_clubs("2026-10-13"), msk(2026, 10, 14, 12)).tours
        self.assertEqual((t["status"], t["first_tour"], t["tour_now"], t["tour_next"]), ("open", 2, None, 2))


class Links(unittest.TestCase):
    PRIOR = {"skaters": {
        "1": {"name": "Иванов Иван", "slot": "F", "gp": 40, "ppg": 5.0},
        "2": {"name": "Петров Пётр А.", "slot": "D", "gp": 30, "ppg": 3.0},
        "3": {"name": "Сидоров Олег", "slot": "F", "gp": 2, "ppg": 9.0},
        "4": {"name": "Козлов Антон", "slot": "D", "gp": 44, "ppg": 2.5},
        "5": {"name": "Орлов Павел", "slot": "F", "gp": 20, "ppg": 4.0}}, "gates": {}}

    def test_by_league_id(self):
        players = {1: {"name": "Иванов Иван", "slot": "F"}, 2: {"name": "Петров Пётр", "slot": "D"}}
        linked, review = prior.link_skaters(players, self.PRIOR)
        self.assertEqual(linked, {1: (40, 5.0), 2: (30, 3.0)})   # сокращение тёзки не мешает
        self.assertEqual(review, [])

    def test_doubtful_become_newbies_and_go_to_review(self):
        players = {4: {"name": "Козлов Антон", "slot": "F"},        # сменил амплуа
                   5: {"name": "Орлов Пётр", "slot": "F"},          # под этим id другой человек
                   9: {"name": "Иванов Иван", "slot": "F"}}         # тёзка под новым id
        linked, review = prior.link_skaters(players, self.PRIOR)
        self.assertEqual(linked, {})
        self.assertEqual(len(review), 3)
        self.assertTrue(any("тёзка под id 1" in r for r in review))

    def test_few_games_is_newbie_silently(self):
        linked, review = prior.link_skaters({3: {"name": "Сидоров Олег", "slot": "F"}}, self.PRIOR)
        self.assertEqual((linked, review), ({}, []))

    def test_manual_links(self):
        players = {9: {"name": "Иванов Иван", "slot": "F"}, 1: {"name": "Иванов Иван", "slot": "F"}}
        linked, _ = prior.link_skaters(players, self.PRIOR, {"link": {9: 4}, "newbie": [1]})
        self.assertEqual(linked, {9: (44, 2.5)})


class PriorFile(unittest.TestCase):
    def test_prior_2526(self):
        data = prior.load_prior()
        self.assertEqual(data["season"], "2025/26")
        self.assertGreater(len(data["skaters"]), 700)
        self.assertEqual(len(data["gates"]), 22)
        s = data["skaters"]["43454"]
        self.assertEqual((s["name"], s["number"], s["team"], s["slot"], s["gp"]), ("Султанов Реваль", 8, "polet", "F", 49))
        self.assertGreater(s["ppg"], 9)
        self.assertTrue(all(k in {t["id"] for t in TEAMS.all} for k in data["gates"]))

    def test_skater_prior_formula(self):
        row = {"И": "10", "Ш": "2", "А": "3", "+/-": "-4", "БВ": "25", "ШП": "1", "РБ": "1"}
        p = prior.skater_prior(row, "D", 0.5)
        pts = 10 + 5 + 12 + 9 - 4 + 10 * (2.5 / 2 - 0.25) + 4
        self.assertEqual(p, {"gp": 10, "ppg": round(pts / 10, 3)})

    def test_person_key(self):
        self.assertEqual(prior.person_key("Губин Иван А."), prior.person_key("губин иван"))
        self.assertEqual(prior.person_key("Носачёв Богдан"), "носачев богдан")


class Names(unittest.TestCase):
    def test_lists(self):
        self.assertGreaterEqual(len(names.ADJECTIVES), 30)
        self.assertGreaterEqual(len(names.NOUNS), 30)
        self.assertEqual(len(set(names.ADJECTIVES)), len(names.ADJECTIVES))
        self.assertEqual(len(set(names.NOUNS)), len(names.NOUNS))
        self.assertTrue(all(a.endswith(("ые", "ие")) for a in names.ADJECTIVES))

    def test_valid(self):
        self.assertTrue(names.is_valid(["Ледяные", "Буревестники"]))
        self.assertFalse(names.is_valid(["Ледяные", "Вася"]))
        self.assertFalse(names.is_valid("Ледяные Буревестники"))
        self.assertEqual(names.title(("Быстрые", "Шайбы")), "Быстрые Шайбы")

    def test_every_club_gate_has_a_name(self):
        for t in TEAMS.all:
            self.assertTrue(names.gate_name(t["id"], t["name"]).startswith("Ворота "))


class Script(unittest.TestCase):
    def test_writes_four_files(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "results.json").write_text(json.dumps(protocols(), ensure_ascii=False), encoding="utf-8")
            (d / "league.json").write_text(json.dumps({"games": CALENDAR}), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as log:
                code = build_zveno.main(["--results", str(d / "results.json"), "--league", str(d / "league.json"),
                                         "--out", str(d / "zveno"), "--now", "2026-10-20T12:00:00+03:00"])
            self.assertIn("«Звено»: prolog", log.getvalue())
            self.assertEqual(code, 0)
            files = sorted(p.name for p in (d / "zveno").iterdir())
            self.assertEqual(files, ["matches.json", "names.json", "pool.json", "tours.json"])
            tours = json.loads((d / "zveno" / "tours.json").read_text(encoding="utf-8"))
            self.assertEqual((tours["season"], tours["status"], tours["tour_next"]), ("2026/27", "prolog", 3))
            nm = json.loads((d / "zveno" / "names.json").read_text(encoding="utf-8"))
            self.assertEqual(set(nm), {"adjectives", "nouns"})

    def test_no_calendar(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stderr(io.StringIO()):
                code = build_zveno.main(["--league", str(Path(d) / "nope.json"), "--out", str(Path(d) / "z")])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()

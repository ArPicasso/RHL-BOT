"""Протокол матча и лидеры с нового сайта лиги rhl.fhr.ru (rhl_protocol.py) и разбор матча из них.

Фикстуры — настоящие страницы 03.10.2026 ~21:00 МСК, обрезанные до шапки матча и вкладки «Протокол»."""
import asyncio
import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_data  # noqa: E402
import league  # noqa: E402
import rhl_protocol  # noqa: E402
import rhl_site  # noqa: E402
from league import Goal, Penalty, Player  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
TZ = ZoneInfo("Europe/Moscow")


def page(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def protocol(gid: int):
    return rhl_protocol.parse_protocol(page(f"rhl_protocol_{gid}.html"), gid)


def stored(gid: int) -> dict:
    """Протокол, как он лежит в rhl_site.json: через JSON."""
    return json.loads(json.dumps(protocol(gid).to_json()))


class RegularProtocol(unittest.TestCase):
    """«Рязань-ВДВ» — «Белгород» 4:3: голы в большинстве, удаления, запасные вратари, тренеры и судьи."""

    @classmethod
    def setUpClass(cls):
        cls.p = protocol(905111)

    def test_header_time_is_moscow(self):
        p = self.p
        self.assertEqual((p.game_id, p.n, p.date, p.time, p.zone, p.attendance),
                         (905111, 1, date(2026, 10, 3), "17:00", "Europe/Moscow", None))
        self.assertEqual((p.home, p.away), ("МХК Рязань-ВДВ", "МХК Белгород"))

    def test_score_and_periods(self):
        self.assertEqual((self.p.home_score, self.p.away_score, self.p.decision), (4, 3, ""))
        self.assertEqual(self.p.periods, ((2, 0), (0, 2), (2, 1)))

    def test_goals(self):
        goals = self.p.goals
        self.assertEqual(len(goals), 7)
        self.assertEqual(goals[0], Goal("1", "05:26", "1:0", "home", "бол.", Player(5, "Абашкин Кирилл"),
                                        (Player(79, "Будуев Роман"), Player(96, "Скачков Евгений А."))))
        self.assertEqual((goals[2].team, goals[2].author.name, goals[2].assists), ("away", "Мухаметжанов Тимур", ()))
        self.assertEqual((goals[-1].time, goals[-1].score, goals[-1].strength, goals[-1].team), ("59:33", "4:3", "бол.", "away"))
        home = sum(g.team == "home" for g in goals)
        self.assertEqual((home, len(goals) - home), (4, 3))

    def test_player_ids_from_links(self):
        # те же id, что у nmhl.fhr.ru: hidden_players.json работает для обоих сайтов
        self.assertEqual((self.p.goals[0].author.id, self.p.goals[0].assists[0].id), (45764, 44167))

    def test_penalties(self):
        pens = self.p.penalties
        self.assertEqual(len(pens), 5)
        self.assertEqual(pens[0], Penalty("05:06", "away", Player(5, "Никитин Антип"), 2, "атака игрока не владеющего шайбой"))
        self.assertEqual([x.time for x in pens], sorted((x.time for x in pens), key=lambda t: int(t.split(":")[0])))
        self.assertEqual({s: sum(x.minutes for x in pens if x.team == s) for s in ("home", "away")}, {"home": 4, "away": 6})
        self.assertEqual(pens[2].reason, "")    # нарушение на сайте не записано

    def test_lineups(self):
        home = [x for x in self.p.lineups if x.team == "home"]
        roles = [x.role for x in home]
        self.assertEqual((len(home), roles.count("G"), roles.count("D"), roles.count("F")), (22, 2, 8, 12))
        self.assertEqual({x.player.name: x.captain for x in home if x.captain},
                         {"Аубакиров Ратмир": "А", "Будуев Роман": "К", "Быков Даниил": "А"})
        abashkin = next(x for x in home if x.player.id == 45764)
        self.assertEqual((abashkin.goals, abashkin.assists, abashkin.gwg, abashkin.ppg, abashkin.shots, abashkin.plus_minus),
                         (2, 2, 1, 1, 2, 3))
        won = {s: sum(x.faceoffs_won for x in self.p.lineups if x.team == s) for s in ("home", "away")}
        self.assertEqual(won, {"home": 38, "away": 18})    # как «Выигранные вбрасывания» в резюме матча

    def test_goalies(self):
        g = {x.player.number: x for x in self.p.lineups if x.role == "G" and x.team == "home"}
        self.assertEqual((g[20].played, g[20].shots_against, g[20].saves, g[20].toi, g[20].wins), (True, 22, 19, "59:31", 1))
        self.assertEqual((g[30].played, g[30].toi), (False, ""))     # запасной: «-» вместо времени

    def test_officials_and_coaches(self):
        self.assertEqual(self.p.coaches, ("Моргунов Сергей", "Романов Андрей"))
        self.assertEqual(self.p.referees, ("Зимагулов Дамир", "Зуев Дмитрий"))
        self.assertEqual(self.p.linesmen, ("Данилин Ярослав", "Селезнёв Александр"))


class ShootoutProtocol(unittest.TestCase):
    def test_shootout(self):
        p = protocol(905112)
        self.assertEqual((p.home, p.away, p.home_score, p.away_score, p.decision), ("Протон", "МХК Кристалл", 2, 1, "Б"))
        # сайт не пишет пустой овертайм: «0:0» вставляем, как было у старого сайта
        self.assertEqual(p.periods, ((0, 0), (0, 0), (1, 1), (0, 0), (1, 0)))
        last = p.goals[-1]
        self.assertEqual((last.period, last.time, last.score, last.team, last.author.name, last.assists),
                         ("РБ", "65:00", "2:1", "home", "Литвинцев Степан", ()))
        gk = {x.player.name: (x.played, x.so_games, x.toi) for x in p.lineups if x.role == "G"}
        self.assertEqual(gk["Лобанов Сергей"], (True, 1, "65:00"))

    def test_decision_on_match_page_too(self):
        # «Б» под счётом шапки: его видит и разбор карточки матча (rhl_site.parse_match)
        self.assertEqual(rhl_site.parse_match(page("rhl_protocol_905112.html"))["decision"], "Б")


class ShutoutProtocol(unittest.TestCase):
    def test_bench_minor_and_no_coaches(self):
        p = protocol(905113)
        self.assertEqual((p.n, p.time, p.home_score, p.away_score), (3, "13:00", 0, 6))
        bench = next(x for x in p.penalties if x.player is None)
        self.assertEqual((bench.time, bench.team, bench.reason), ("52:24", "home", "нарушение численного состава"))
        self.assertEqual(p.coaches, ("", ""))
        self.assertEqual([x.player.name for x in p.lineups if x.role == "G" and x.team == "away" and x.played],
                         ["Курников Владимир", "Нефёдов Андрей"])


class NotAProtocol(unittest.TestCase):
    def test_live_and_future_and_calendar_give_none(self):
        self.assertIsNone(rhl_protocol.parse_protocol(page("rhl_match_905111_live.html"), 905111))   # идёт: победителя нет
        self.assertIsNone(rhl_protocol.parse_protocol(page("rhl_match_905116_sched.html"), 905116))
        self.assertIsNone(rhl_protocol.parse_protocol(page("rhl_calendar_2026_10_03.html"), 1))
        self.assertIsNone(rhl_protocol.parse_protocol("<html><body>Страница не найдена</body></html>", 1))

    def test_final_card_without_protocol_tab(self):
        # карточка сыгранного матча без вкладки «Протокол» — не протокол
        self.assertIsNone(rhl_protocol.parse_protocol(page("rhl_match_905113_final.html"), 905113))


class Leaders(unittest.TestCase):
    def setUp(self):
        self.html = page("rhl_leaders_scorers.html")

    def test_rows(self):
        rows = rhl_protocol.parse_leaders(self.html)
        self.assertEqual(len(rows), 30)
        self.assertEqual(rows[0], {"rank": 1, "name": "Абашкин Кирилл", "id": 45764, "club": "МХК Рязань-ВДВ", "role": "F",
                                   "number": 5, "pts": 4, "gp": 1, "g": 2, "a": 2, "pm": 3, "pim": 0})
        self.assertEqual(rhl_protocol.leaders_name(self.html), "26/27 | Регулярный чемпионат")

    def test_urls(self):
        self.assertEqual(rhl_protocol.leaders_url("https://rhl.fhr.ru", "2026-2027", 1432, "sv_pct"),
                         "https://rhl.fhr.ru/stat/leaders/season/2026-2027/tournament/1432/nomination/goalies_sv/")
        self.assertEqual(set(rhl_protocol.NOMINATIONS), set(league.LEADER_CATS))

    def test_build_leaders(self):
        src = {"site": "https://rhl.fhr.ru", "tournament": 1432, "name": rhl_protocol.leaders_name(self.html),
               "updated": "2026-10-03T21:00+03:00", "categories": {"pts": rhl_protocol.parse_leaders(self.html)}}
        top = build_data.leaders(build_data.load_teams(), src, {42502}, past={}, kits={})
        self.assertEqual((top["season"], top["league"], top["stage"]), ("2026/27", "РХЛ", "регулярный чемпионат"))
        pts = top["categories"]["pts"]
        self.assertEqual((pts[0]["name"], pts[0]["team"], pts[0]["pts"]), ("Абашкин Кирилл", "ryazan-vdv", 4))
        self.assertNotIn("Аубакиров Ратмир", [r["name"] for r in pts])   # скрытый по просьбе — по id сайта


class Reports(unittest.TestCase):
    NOW = datetime(2026, 10, 3, 21, 0, tzinfo=TZ)

    def game(self, **kw) -> dict:
        return {"id": 905111, "t": 1432, "status": "final", "start": "2026-10-03T17:00:00+03:00", **kw}

    def test_when_to_fetch(self):
        now = self.NOW
        self.assertTrue(rhl_site.need_report(self.game(), now))                              # сыгран, протокола нет
        self.assertFalse(rhl_site.need_report(self.game(status="live"), now))
        self.assertFalse(rhl_site.need_report(self.game(start=None), now))
        fresh = self.game(report={"n": 1}, report_at="2026-10-03T20:00+03:00")
        self.assertFalse(rhl_site.need_report(fresh, now))                                   # перечитан час назад
        self.assertTrue(rhl_site.need_report(fresh, now + timedelta(hours=2)))               # раз в три часа
        self.assertFalse(rhl_site.need_report(fresh, now + timedelta(days=4)))               # лига уже не правит
        self.assertTrue(rhl_site.need_report(self.game(), now + timedelta(days=6)))          # нет — ищем неделю
        self.assertFalse(rhl_site.need_report(self.game(), now + timedelta(days=8)))

    def test_season_code(self):
        self.assertEqual(rhl_site.season_code(datetime(2026, 10, 3, tzinfo=TZ)), "2026-2027")
        self.assertEqual(rhl_site.season_code(datetime(2027, 3, 1, tzinfo=TZ)), "2026-2027")

    async def reports_with(self, store: dict, html: str) -> int:
        async def fake_get(session, url):
            return html
        with mock.patch.object(rhl_site, "_get", fake_get), mock.patch.object(rhl_site, "PAUSE", 0):
            return await rhl_site.fetch_reports(None, "https://rhl.fhr.ru", store, self.NOW, 5)

    def test_update_offline(self):
        """Прогон rhl_site.update на страницах снимка: календарь, карточки, протоколы, лидеры."""
        urls = []

        async def fake_get(session, url):
            urls.append(url)
            if url.endswith("/calendar/"):
                return page("rhl_calendar_2026_10_03.html")
            m = re.search(r"/matchcenter/\d+/(\d+)/(?:protocol/)?$", url)
            if m and (FIX / f"rhl_protocol_{m.group(1)}.html").exists():
                return page(f"rhl_protocol_{m.group(1)}.html")   # в протоколе та же шапка, что у карточки
            if "/nomination/scorers/" in url:
                return page("rhl_leaders_scorers.html")
            return "<html><body></body></html>"

        with tempfile.TemporaryDirectory() as d, mock.patch.object(rhl_site, "_get", fake_get), \
                mock.patch.object(rhl_site, "PAUSE", 0):
            store_path, leaders_path = Path(d) / "rhl_site.json", Path(d) / "leaders.json"
            asyncio.run(rhl_site.update(store_path, "https://rhl.fhr.ru", self.NOW, leaders_path))
            again = rhl_site.load_store(store_path)
            top = json.loads(leaders_path.read_text(encoding="utf-8"))
            with self.assertLogs(level="WARNING") as logs:     # протокол не разобрался — в журнал, без падения
                again["games"]["905113"]["report_at"] = "2026-10-03T12:00+03:00"
                fresh = asyncio.run(self.reports_with(again, "<html><body></body></html>"))
        games = again["games"]
        self.assertEqual({k for k, g in games.items() if g.get("report")}, {"905111", "905112", "905113"})
        self.assertEqual(games["905111"]["report"]["goals"][0]["author"]["name"], "Абашкин Кирилл")
        self.assertEqual((games["905112"]["status"], games["905112"]["decision"]), ("final", "Б"))
        self.assertEqual(games["905111"]["report_at"], "2026-10-03T21:00+03:00")
        self.assertNotIn("report", games["905114"])     # сыгран по ленте, но карточка не скачалась: начала нет
        self.assertEqual(fresh, 0)
        self.assertTrue(any("905113" in x for x in logs.output))
        self.assertEqual(len(games["905113"]["report"]["goals"]), 6)    # прежний протокол не затёрт
        self.assertEqual(len(urls), len(set(urls)))                     # каждая страница — один раз
        self.assertLessEqual(sum("/matchcenter/" in u and not u.endswith("/video/") for u in urls), rhl_site.MAX_PAGES)
        self.assertLessEqual(sum(u.endswith("/video/") for u in urls), rhl_site.MAX_VIDEO)   # «Смотреть» — свой лимит
        # лидеры: шесть номинаций, у сайта пока только бомбардиры
        self.assertEqual(sum("/stat/leaders/season/2026-2027/tournament/1432/nomination/" in u for u in urls), 6)
        self.assertEqual((top["site"], top["tournament"], top["name"]), ("https://rhl.fhr.ru", 1432, "26/27 | Регулярный чемпионат"))
        self.assertEqual((len(top["categories"]["pts"]), top["categories"]["g"]), (30, []))
        self.assertEqual(again["leaders"], top)

    def test_empty_leaders_keep_old_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "leaders.json"
            path.write_text('{"site": "https://nmhl.fhr.ru"}', encoding="utf-8")
            self.assertFalse(rhl_site.write_leaders({"leaders": {"categories": {"pts": []}}}, path))
            self.assertFalse(rhl_site.write_leaders({}, path))
            self.assertEqual(path.read_text(encoding="utf-8"), '{"site": "https://nmhl.fhr.ru"}')


class Build(unittest.TestCase):
    """Протокол с сайта → счёт с периодами, голы, разбор матча, очные встречи и таблица (ADR-008)."""

    @classmethod
    def setUpClass(cls):
        cls.teams = build_data.load_teams()

    def store(self, gids=(905111, 905112, 905113)) -> dict:
        s = {"games": {}}
        rhl_site.merge(s, rhl_site.parse_calendar(page("rhl_calendar_2026_10_03.html")))
        for gid in gids:
            g = s["games"][str(gid)]
            rhl_site.apply_page(g, rhl_site.parse_match(page(f"rhl_protocol_{gid}.html")))
            g["report"] = stored(gid)
        return s

    def build(self, store, hidden=frozenset(), schedule=()):
        with contextlib.redirect_stdout(io.StringIO()):   # записки о матчах календаря без команд
            data, _, details = build_data.build(self.teams, [], {}, hidden, site=store, schedule=list(schedule))
        return {g["id"]: g for g in data["games"]}, data, details

    def test_result_and_goals_in_league(self):
        games, _, _ = self.build(self.store())
        g = games["n1"]     # «Рязань-ВДВ» — «Белгород», матч 1 из games.json
        self.assertEqual(g["score"], {"home": 4, "away": 3, "decision": "", "periods": [[2, 0], [0, 2], [2, 1]]})
        self.assertEqual(len(g["goals"]), 7)
        self.assertEqual({k: g["goals"][0][k] for k in ("period", "time", "team", "score", "strength", "author", "assists", "no")},
                         {"period": "1", "time": "05:26", "team": "home", "score": "1:0", "strength": "бол.",
                          "author": "Абашкин Кирилл", "assists": ["Будуев Роман", "Скачков Евгений А."], "no": 5})
        self.assertNotIn("score_src", g)
        self.assertEqual(games["r905112"]["score"]["decision"], "Б")

    def test_time_not_shifted(self):
        games, _, _ = self.build(self.store())
        self.assertEqual((games["n1"]["time"], games["n1"]["start"]), ("17:00", "2026-10-03T17:00:00+03:00"))
        self.assertEqual(games["r905113"]["time"], "13:00")
        # протокол сайта в МСК: арена в Иркутске — местное «22:00», а московское остаётся «17:00»
        g = {"id": "x", "date": "2026-10-03", "home": "ermak", "away": "belgorod"}
        build_data.fill_result(g, stored(905111), zones={"ermak": "Asia/Irkutsk"})
        self.assertEqual((g["time"], g["local"]), ("17:00", "22:00"))
        # протокол старого сайта без zone — по-прежнему местное время арены
        old = {**stored(905111), "zone": None, "time": "12:00"}
        build_data.fill_result(g, old, zones={"ermak": "Asia/Irkutsk"})
        self.assertEqual((g["time"], g["local"]), ("07:00", "12:00"))

    def test_schedule_time_wins(self):
        rows = [{"key": "2026-10-03|ryazan-vdv|belgorod", "start": "2026-10-03T17:05:00+03:00"}]
        games, _, _ = self.build(self.store(), schedule=rows)
        self.assertEqual(games["n1"]["time"], "17:05")
        self.assertEqual(games["n1"]["score"]["home"], 4)

    def test_match_recaps(self):
        _, _, details = self.build(self.store())
        self.assertEqual(set(details), {"n1", "r905112", "r905113"})
        d = details["n1"]
        self.assertEqual((d["gw"], d["pp"], d["shots"], d["faceoffs"], d["pim"]),
                         (5, {"home": [1, 3], "away": [1, 2]}, {"home": 30, "away": 22}, {"home": 38, "away": 18},
                          {"home": 4, "away": 6}))
        self.assertEqual([(x["name"], x["shots"], x["saves"]) for x in d["goalies"]],
                         [("Самойлов Тимофей", 22, 19), ("Баринов Иван", 30, 26)])
        self.assertEqual(len(d["lineups"]["home"]["F"]), 12)
        self.assertTrue(next(r for r in d["lineups"]["home"]["G"] if r["no"] == 30)["dnp"])
        self.assertEqual((d["referees"], d["coaches"]), (["Зимагулов Дамир", "Зуев Дмитрий"],
                                                         {"home": "Моргунов Сергей", "away": "Романов Андрей"}))
        self.assertEqual(details["r905112"]["story"], "Всё решили буллиты, победный забил Литвинцев Степан.")
        self.assertEqual(details["r905113"]["story"], "Четыре шайбы подряд у «Краснодар» за 13 минут третьего периода. "
                                                      "Сухой матч: Нефёдов Андрей отразил все 27 бросков.")

    def test_standings_with_shootout(self):
        _, data, _ = self.build(self.store())
        rows = {r["team"]: r for conf in data["standings"].values() for r in conf}
        self.assertEqual((rows["proton"]["sow"], rows["proton"]["pts"]), (1, 2))
        self.assertEqual((rows["kristall"]["sol"], rows["kristall"]["pts"]), (1, 1))
        self.assertEqual((rows["ryazan-vdv"]["w"], rows["krasnodar"]["gf"]), (1, 6))

    def test_head_to_head_opens_recap(self):
        games, data, _ = self.build(self.store())
        h2h = build_data.head_to_head(data["games"], [])
        last = h2h[build_data.pair_key("ryazan-vdv", "belgorod")]["last"]
        self.assertEqual((last[0]["id"], last[0]["score"]), ("n1", [4, 3]))

    def test_without_report_score_from_feed(self):
        # протокола ещё нет — счёт из ленты сайта, без голов и разбора
        s = self.store(gids=())
        rhl_site.apply_page(s["games"]["905113"], rhl_site.parse_match(page("rhl_match_905113_final.html")))
        games, _, details = self.build(s)
        self.assertEqual(games["r905113"]["score"], {"home": 0, "away": 6, "decision": None, "periods": []})
        self.assertNotIn("goals", games["r905113"])
        self.assertNotIn("r905113", details)

    def test_hidden_player(self):
        games, _, details = self.build(self.store(), hidden={45764})
        self.assertEqual(games["n1"]["goals"][0]["author"], build_data.HIDDEN_NAME)
        self.assertNotIn("Абашкин Кирилл", json.dumps(details["n1"], ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()

"""Сборка данных лиги для мини-аппа: календарь r-hockey, справочник команд, таблица."""
import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_data as b  # noqa: E402
import rhockey  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


class RHockeyCalendar(unittest.TestCase):
    def test_month_page(self):
        games = rhockey.parse_month((FIX / "rhockey_rhl_2026_10.html").read_text(encoding="utf-8"), 2026)
        self.assertEqual(len(games), 12)
        self.assertEqual(games[0], rhockey.RawGame(9319019, date(2026, 10, 3), 2916, 2914))

    def test_other_tournament_rows_skipped(self):
        html = (FIX / "rhockey_rhl_2026_10.html").read_text(encoding="utf-8")
        self.assertEqual(rhockey.parse_month(html, 2026, tournament_id=1), [])

    def test_season_crosses_new_year(self):
        self.assertEqual(rhockey._season_year(12, 2026), 2026)
        self.assertEqual(rhockey._season_year(1, 2026), 2027)

    def test_timeout_falls_back_to_last_calendar(self):
        # 04.10.2026 в 12:43 таймаут r-hockey уронил всю сборку мини-аппа
        import asyncio
        path = Path(tempfile.mkdtemp()) / "rhockey.json"
        games = [rhockey.RawGame(9319019, date(2026, 10, 3), 2916, 2914)]

        async def ok():
            return games

        async def timeout():
            raise asyncio.TimeoutError
        with self.assertRaises(asyncio.TimeoutError):
            rhockey.season(timeout, path)              # кэша нет — падаем: собирать нечего
        self.assertEqual(rhockey.season(ok, path), games)
        self.assertEqual(rhockey.season(timeout, path), games)   # не ответил — прошлый удачный


class TeamNames(unittest.TestCase):
    teams = b.load_teams()

    def test_26_teams_in_two_conferences(self):
        confs = [t["conf"] for t in self.teams.all]
        self.assertEqual((confs.count("west"), confs.count("east")), (13, 13))

    def test_spellings_from_all_sources(self):
        cases = {
            "МХК Рязань-ВДВ": "ryazan-vdv",       # протоколы лиги
            "ХК Сокол ЧР": "sokol",
            "МХК Кристалл С": "kristall",
            "Полет": "polet",                     # r-hockey без ё
            "Тверичи - СШОР": "tverichi",         # официальный состав ФХР
            "Ростов": "rostov",                   # games.json
            "ХК Краснодар": "krasnodar",
        }
        for name, team in cases.items():
            self.assertEqual(self.teams.find(name), team, name)

    def test_every_rhockey_id_is_known(self):
        self.assertEqual(len(self.teams.by_rh), 26)

    def test_logo_files_exist(self):
        webapp = Path(__file__).parent.parent / "webapp"
        for t in self.teams.all:
            if "logo" in t:
                self.assertTrue((webapp / t["logo"]).is_file(), t["logo"])

    def test_arena_time_zones(self):
        """У каждой команды пояс арены (ADR-019): время протокола — местное, в данных — московское."""
        for t in self.teams.all:
            self.assertIsNotNone(ZoneInfo(t["tz"]), t["id"])
        tz = self.teams.tz
        self.assertEqual((tz["ermak"], tz["fakel-yamal"], tz["samara"], tz["kristall"], tz["progress"]),
                         ("Asia/Irkutsk", "Asia/Yekaterinburg", "Europe/Samara", "Europe/Saratov", "Europe/Samara"))
        self.assertEqual(sum(z == "Europe/Moscow" for z in tz.values()), 21)
        self.assertEqual(b.Teams([{"id": "x", "name": "Икс", "aliases": [], "rhockey": 1}]).tz, {"x": "Europe/Moscow"})


class Calendar(unittest.TestCase):
    teams = b.load_teams()

    def test_official_games(self):
        games = b.official_games(self.teams)
        self.assertEqual(len(games), 48)
        self.assertEqual(sum(g["home"] == "ryazan-vdv" for g in games), 24)
        self.assertEqual(games[0], {"id": "n1", "n": 1, "date": "2026-10-03", "home": "ryazan-vdv",
                                    "away": "belgorod", "official": True})

    def test_official_calendar_replaces_aggregator(self):
        raw = rhockey.parse_month((FIX / "rhockey_rhl_2026_10.html").read_text(encoding="utf-8"), 2026)
        games = b.merge_calendar(self.teams, raw, b.official_games(self.teams))
        ryazan = [g for g in games if "ryazan-vdv" in (g["home"], g["away"])]
        self.assertEqual(len(ryazan), 48)
        self.assertTrue(all(g["official"] for g in ryazan))
        ryazan_rh = sum(2916 in (g.home_rh, g.away_rh) for g in raw)
        self.assertEqual(len(games), 48 + len(raw) - ryazan_rh)


class Standings(unittest.TestCase):
    def test_matches_official_table_2025_26(self):
        # таблица НМХЛ 2025/26, Восток: Рязань-ВДВ 48 игр, 40-4-1-1-1-1, 192-69, 92 очка
        results = json.loads((FIX / "results_1378_ryazan.json").read_text(encoding="utf-8"))
        protocols = results["1378"].values()
        names = sorted({n for p in protocols for n in (p["home"], p["away"])})
        teams = b.Teams([{"id": b.norm(n), "name": n, "city": "", "conf": "east", "rhockey": i, "aliases": []}
                         for i, n in enumerate(names)])
        games = [{"id": k, "n": None, "date": p["date"], "home": b.norm(p["home"]), "away": b.norm(p["away"]),
                  "official": True} for k, p in results["1378"].items()]
        self.assertEqual(b.attach_results(games, teams, results), [])
        top = b.standings(teams, games)["east"][0]
        self.assertEqual({k: top[k] for k in ("team", "gp", "w", "otw", "sow", "sol", "otl", "l", "gf", "ga", "pts")},
                         {"team": "рязань-вдв", "gp": 48, "w": 40, "otw": 4, "sow": 1, "sol": 1, "otl": 1, "l": 1,
                          "gf": 192, "ga": 69, "pts": 92})


class Links(unittest.TestCase):
    def test_only_telegram_links(self):
        env = {"BOT_LINK": "https://t.me/rhl_bot/", "APP_LINK": "http://evil.example/app"}
        self.assertEqual(b.links(env), {"bot": "https://t.me/rhl_bot"})

    def test_defaults(self):
        self.assertEqual(b.links({}), {"bot": "https://t.me/rhl_u21_bot", "app": "https://t.me/rhl_u21_bot/myapp"})


class StartTime(unittest.TestCase):
    """Время матча — московское (ADR-019, разделы 2 и 5): протокол, schedule.json, посты клубов."""
    teams = b.load_teams()
    results = json.loads((FIX / "results_1378_ryazan.json").read_text(encoding="utf-8"))

    def protocol(self, n: str) -> dict:
        return json.loads(json.dumps(self.results["1378"][n]))

    def game(self, gid="g", day="2026-10-03", home="ryazan-vdv", away="belgorod", **kw):
        return {"id": gid, "n": None, "date": day, "home": home, "away": away, "official": False, **kw}

    def test_protocol_time_is_local_then_moscow(self):
        # 08.11.2025 «Ермак» — «Рязань-ВДВ» в Ангарске: в протоколе 12:00 местного, это 07:00 по Москве
        p = self.protocol("144")
        self.assertEqual((p["date"], p["time"], p["home"]), ("2025-11-08", "12:00", "МХК Ермак"))
        g = self.game(day="2025-11-08", home="ermak", away="ryazan-vdv")
        self.assertEqual(b.attach_results([g], self.teams, {"1378": {"144": p}}), [])
        self.assertEqual((g["time"], g["start"], g["local"]), ("07:00", "2025-11-08T07:00:00+03:00", "12:00"))

    def test_moscow_arena_has_no_local(self):
        p = self.protocol("16")                                    # 08.10.2025 в Белгороде, 19:00
        g = self.game(day=p["date"], home="belgorod", away="ryazan-vdv")
        b.fill_result(g, p, zones=self.teams.tz)
        self.assertEqual((g["time"], g["start"]), ("19:00", "2025-10-08T19:00:00+03:00"))
        self.assertNotIn("local", g)

    def test_protocol_without_time(self):
        p = {**self.protocol("16"), "time": ""}
        g = self.game(day=p["date"], home="belgorod", away="ryazan-vdv")
        b.fill_result(g, p, zones=self.teams.tz)
        self.assertIsNone(g["time"])
        self.assertNotIn("start", g)

    def test_samara_and_saratov_are_moscow_plus_one(self):
        start = b.local_start("2026-10-03", "18:00", "Europe/Saratov")
        g = self.game()
        b.set_start(g, start, "Europe/Saratov")
        self.assertEqual((g["time"], g["local"]), ("17:00", "18:00"))

    def schedule(self, *rows):
        return [{"key": f"{d}|{h}|{a}", "date": d, "home": h, "away": a, "start": f"{d}T{t}:00+03:00", "time": t,
                 "online": f"https://online.khl.ru/online/{i}.html", "khl_id": i, "src": "online.khl.ru"}
                for i, (d, h, a, t) in enumerate(rows, 904950)]

    def test_schedule_by_key(self):
        games = [self.game("n1", "2026-10-03"), self.game("n2", "2026-10-04"),
                 self.game("rh1", "2026-10-03", "ermak", "samara")]
        n = b.apply_schedule(games, self.schedule(("2026-10-03", "ryazan-vdv", "belgorod", "17:00"),
                                                  ("2026-10-03", "ermak", "samara", "07:00")), self.teams.tz)
        self.assertEqual(n, 2)
        self.assertEqual((games[0]["time"], games[0]["start"], games[0]["online"]),
                         ("17:00", "2026-10-03T17:00:00+03:00", "https://online.khl.ru/online/904950.html"))
        self.assertNotIn("time", games[1])                         # второй матч серии — своего ключа нет
        self.assertEqual((games[2]["time"], games[2]["local"]), ("07:00", "12:00"))

    def test_schedule_one_day_off(self):
        games = [self.game("n1", "2026-10-03"), self.game("n2", "2026-10-04")]
        # 05.10 — рядом только матч 04.10: он и есть, хоть дата и разошлась
        b.apply_schedule(games, self.schedule(("2026-10-05", "ryazan-vdv", "belgorod", "16:00")), self.teams.tz)
        self.assertEqual((games[0].get("time"), games[1]["time"]), (None, "16:00"))
        # 03.10 и 04.10 заняты своими ключами — лишняя строка 02.10 к занятому матчу не прилипает
        games = [self.game("n1", "2026-10-03"), self.game("n2", "2026-10-04")]
        n = b.apply_schedule(games, self.schedule(("2026-10-03", "ryazan-vdv", "belgorod", "17:00"),
                                                  ("2026-10-04", "ryazan-vdv", "belgorod", "16:00"),
                                                  ("2026-10-02", "ryazan-vdv", "belgorod", "15:00")), self.teams.tz)
        self.assertEqual((n, games[0]["time"], games[1]["time"]), (2, "17:00", "16:00"))

    def test_schedule_ambiguous_or_reversed(self):
        games = [self.game("n1", "2026-10-03"), self.game("n2", "2026-10-05")]
        b.apply_schedule(games, self.schedule(("2026-10-04", "ryazan-vdv", "belgorod", "17:00")), self.teams.tz)
        self.assertFalse(any("time" in g for g in games))          # 03.10 и 05.10 — оба рядом, не угадываем
        b.apply_schedule(games, self.schedule(("2026-10-03", "belgorod", "ryazan-vdv", "17:00")), self.teams.tz)
        self.assertFalse(any("time" in g for g in games))          # хозяева и гости наоборот — другой матч

    def test_schedule_fields_optional_and_checked(self):
        games = [self.game("n1"), self.game("n2", "2026-10-04")]
        rows = [{"key": "2026-10-03|ryazan-vdv|belgorod", "time": "17:00", "online": "javascript:alert(1)"},
                {"date": "2026-10-04", "home": "ryazan-vdv", "away": "belgorod", "start": "2026-10-04T16:00:00"},
                {"key": "битый"}, {"date": "завтра", "home": "ryazan-vdv", "away": "belgorod"}]
        self.assertEqual(b.apply_schedule(games, rows, self.teams.tz), 2)
        self.assertEqual((games[0]["start"], games[1]["start"]), ("2026-10-03T17:00:00+03:00", "2026-10-04T16:00:00+03:00"))
        self.assertNotIn("online", games[0])                       # только https-ссылка

    def test_schedule_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "schedule.json"
            self.assertEqual(b.load_schedule(path), [])            # нет файла — молча ничего
            path.write_text("<html>502</html>", encoding="utf-8")
            self.assertEqual(b.load_schedule(path), [])
            path.write_text(json.dumps({"updated": "x", "games": [{"key": "a"}, 5]}), encoding="utf-8")
            self.assertEqual(b.load_schedule(path), [{"key": "a"}])

    def test_schedule_wins_over_protocol_and_post(self):
        p = self.protocol("144")
        g = self.game("n1", "2025-11-08", "ermak", "ryazan-vdv")
        b.attach_results([g], self.teams, {"1378": {"144": p}})
        b.apply_schedule([g], self.schedule(("2025-11-08", "ermak", "ryazan-vdv", "08:00")), self.teams.tz)
        self.assertEqual((g["time"], g["local"]), ("08:00", "13:00"))
        posts = {"ermak_angarsk_hc": {"ok": True, "posts": [{
            "id": 1, "url": "u", "at": "2025-11-08T01:00+03:00", "title": "", "text": "Сегодня «Рязань-ВДВ»",
            "times": ["Начало в 14:00"], "links": ["https://vk.com/video-1_2"]}]}}
        channels = [{"handle": "ermak_angarsk_hc", "club": "ermak", "kind": "club", "short": "ХК «Ермак»"}]
        b.apply_matchday([g], self.teams, channels, posts)
        self.assertEqual(g["time"], "08:00")                       # время из поста — только если других нет
        self.assertEqual(g["watch"], [{"title": "VK Видео · канал ХК «Ермак»", "url": "https://vk.com/video-1_2",
                                       "src": "t.me/ermak_angarsk_hc"}])
        bare = self.game("n2", "2025-11-08", "ermak", "ryazan-vdv")
        b.apply_matchday([bare], self.teams, channels, posts)
        self.assertEqual((bare["time"], bare["start"], bare["local"]), ("09:00", "2025-11-08T09:00:00+03:00", "14:00"))

    def test_build_keeps_old_format(self):
        """Без schedule.json и постов матч такой же, как раньше: новые поля необязательные."""
        data, _, _ = b.build(self.teams, [], {})
        first = data["games"][0]
        self.assertEqual(first, {"id": "n1", "n": 1, "date": "2026-10-03", "home": "ryazan-vdv", "away": "belgorod",
                                 "official": True})
        self.assertEqual(data["teams"][0]["tz"], "Europe/Moscow")
        rows = self.schedule(("2026-10-03", "ryazan-vdv", "belgorod", "17:00"))
        data, _, _ = b.build(self.teams, [], {}, schedule=rows)
        self.assertEqual((data["games"][0]["time"], data["games"][0]["online"]),
                         ("17:00", "https://online.khl.ru/online/904950.html"))
        self.assertFalse(any(k in data["games"][1] for k in ("time", "start", "local", "online", "watch")))
        self.assertEqual(datetime.fromisoformat(data["games"][0]["start"]).utcoffset().total_seconds(), 3 * 3600)


def protocol_game(name: str, game_id: int, home: str, away: str, hidden: set[int] = frozenset()) -> tuple[dict, dict]:
    """Протокол из фикстуры — как он лежит в results.json, и матч, к которому он привязан."""
    import league
    p = json.loads(json.dumps(league.parse_protocol((FIX / name).read_text(encoding="utf-8"), game_id).to_json()))
    g = {"id": "n1", "n": None, "date": p["date"], "home": home, "away": away, "official": True}
    teams = b.Teams([{"id": home, "name": p["home"], "city": "", "conf": "east", "rhockey": 1, "aliases": []},
                     {"id": away, "name": p["away"], "city": "", "conf": "east", "rhockey": 2, "aliases": []}])
    b.attach_results([g], teams, {"1": {"1": p}}, hidden=hidden)
    return g, p


NAMES = {"ryazan-vdv": "Рязань-ВДВ", "belgorod": "Белгород", "samara": "Самара", "kristall": "Кристалл"}


class MatchRecap(unittest.TestCase):
    """Разбор прошедшего матча (ADR-008)."""

    def detail(self, name, game_id, home, away, hidden=frozenset()):
        g, p = protocol_game(name, game_id, home, away, hidden)
        return g, b.match_detail(g, p, NAMES, hidden)

    def test_regular_game(self):
        g, d = self.detail("protocol_900942_regular.html", 900942, "ryazan-vdv", "belgorod")
        self.assertEqual(d["shots"], {"home": 45, "away": 15})
        self.assertEqual(d["faceoffs"], {"home": 29, "away": 20})
        self.assertEqual(d["pim"], {"home": 8, "away": 4})
        self.assertEqual(d["pp"], {"home": [1, 2], "away": [0, 4]})
        self.assertEqual(d["gw"], 2)                       # 2:1, Колыхалов
        self.assertEqual(d["length"], 60)
        self.assertEqual([k["name"] for k in d["goalies"]], ["Самойлов Тимофей", "Шевченко Артём А."])
        self.assertEqual(len(d["lineups"]["home"]["F"]), 14)
        self.assertEqual(d["penalties"][1]["who"], "Командный штраф")
        self.assertEqual(d["coaches"]["away"], "Романов Андрей Александрович")

    def test_story_burst_after_opponent_opened(self):
        _, d = self.detail("protocol_900942_regular.html", 900942, "ryazan-vdv", "belgorod")
        self.assertEqual(d["story"], "Первыми забили гости, а дальше пять шайб подряд у «Рязань-ВДВ» "
                                     "за 12 минут второго периода.")

    def test_story_comeback_and_overtime(self):
        _, d = self.detail("protocol_901016_ot.html", 901016, "samara", "ryazan-vdv")
        self.assertEqual(d["story"], "Камбэк «Рязань-ВДВ»: уступали 3:5, но отыгрались. "
                                     "В овертайме победу принёс Михеев Яромир.")
        self.assertEqual(d["length"], 65)

    def test_story_shootout(self):
        _, d = self.detail("protocol_901033_shootout.html", 901033, "kristall", "ryazan-vdv")
        self.assertTrue(d["story"].startswith("Всё решили буллиты, победный забил Шафеев Данат."))

    def test_mutual_penalties_give_no_power_play(self):
        _, d = self.detail("protocol_901033_shootout.html", 901033, "kristall", "ryazan-vdv")
        # 47:18 — взаимные удаления Белицына и Бахтеева, большинства нет
        self.assertEqual(d["pp"], {"home": [0, 3], "away": [2, 6]})

    def test_quiet_game_has_no_story(self):
        g = {"home": "ryazan-vdv", "away": "belgorod", "score": {"home": 2, "away": 1, "decision": ""},
             "goals": [{"period": "1", "time": "10:00", "team": "home", "score": "1:0", "author": "А"},
                       {"period": "2", "time": "30:00", "team": "away", "score": "1:1", "author": "Б"},
                       {"period": "3", "time": "45:00", "team": "home", "score": "2:1", "author": "В"}]}
        self.assertEqual(b.story(g, NAMES), "")

    def test_shutout_and_late_winner(self):
        g = {"home": "ryazan-vdv", "away": "belgorod", "score": {"home": 1, "away": 0, "decision": ""},
             "goals": [{"period": "3", "time": "58:48", "team": "home", "score": "1:0", "author": "Иванов Иван"}]}
        goalies = [{"team": "home", "name": "Петров Пётр", "shots": 31}]
        self.assertEqual(b.story(g, NAMES, goalies),
                         "Сухой матч: Петров Пётр отразил все 31 бросок. Победная шайба за 1:12 до сирены: Иванов Иван.")

    def test_hidden_player_everywhere(self):
        # 44596 — Щербаков, автор гола Белгорода; 45805 — Фурлетов, первое удаление
        g, d = self.detail("protocol_900942_regular.html", 900942, "ryazan-vdv", "belgorod", {44596, 45805})
        self.assertEqual(g["goals"][0]["author"], b.HIDDEN_NAME)
        self.assertEqual((d["penalties"][0]["who"], d["penalties"][0]["no"]), (b.HIDDEN_NAME, None))
        names = [x["name"] for grp in d["lineups"]["away"].values() for x in grp]
        self.assertNotIn("Щербаков Артём Ан.", names)
        self.assertNotIn("44596", json.dumps(d))

    def test_sticker_numbers_in_goals_and_penalties(self):
        """Номер автора гола и игрока удаления — для стикера в форме команды (ADR-009)."""
        g, d = self.detail("protocol_900942_regular.html", 900942, "ryazan-vdv", "belgorod")
        self.assertEqual((g["goals"][0]["author"], g["goals"][0]["no"]), ("Щербаков Артём Ан.", 23))
        self.assertTrue(all("no" in x for x in g["goals"]))
        self.assertFalse(any(x.get("gk") for x in g["goals"]))      # вратари в этом матче не забивали
        g, d = self.detail("protocol_900942_regular.html", 900942, "ryazan-vdv", "belgorod", {44596})
        self.assertNotIn("no", g["goals"][0])                       # скрытому — ни номера, ни стикера с номером

    def test_goalie_flag_by_lineup(self):
        p = {"lineups": [{"team": "home", "role": "G", "player": {"number": 30, "name": "В", "id": 1}}]}
        self.assertEqual(b.sticker({"number": 30, "id": 1}, "home", b.goalies_of(p), set()), {"no": 30, "gk": 1})
        self.assertEqual(b.sticker({"number": 30, "id": 1}, "away", b.goalies_of(p), set()), {"no": 30})
        self.assertEqual(b.sticker(None, "home", set(), set()), {})

    def test_no_player_ids_in_app_data(self):
        g, d = self.detail("protocol_900942_regular.html", 900942, "ryazan-vdv", "belgorod")
        self.assertNotIn('"id": 4', json.dumps(g["goals"]))
        self.assertNotIn("44596", json.dumps(d))

    def test_old_protocol_without_details(self):
        g, p = protocol_game("protocol_900942_regular.html", 900942, "ryazan-vdv", "belgorod")
        for k in ("penalties", "lineups", "referees", "linesmen", "coaches"):
            p.pop(k)
        d = b.match_detail(g, p, NAMES)
        self.assertEqual((d["penalties"], d["lineups"], d["shots"], d["goalies"]), ([], None, None, []))
        self.assertTrue(d["story"])

    def test_hidden_file_is_a_list(self):
        self.assertIsInstance(json.loads((ROOT / "hidden_players.json").read_text(encoding="utf-8")), list)


def seq(*teams: str, period: str = "2") -> list[dict]:
    """Голы по порядку: "h" — хозяева, "a" — гости, "b" — победный буллит хозяев."""
    out, h, a = [], 0, 0
    for i, t in enumerate(teams):
        side = "home" if t in ("h", "b") else "away"
        h, a = (h + 1, a) if side == "home" else (h, a + 1)
        out.append({"period": "РБ" if t == "b" else period, "time": f"{21 + i}:00", "team": side,
                    "score": f"{h}:{a}", "strength": "", "author": f"Игрок {i}", "assists": []})
    return out


def scored(goals: list[dict], decision: str = "") -> dict:
    h = sum(x["team"] == "home" for x in goals)
    return {"home": "ryazan-vdv", "away": "belgorod", "goals": goals,
            "score": {"home": h, "away": len(goals) - h, "decision": decision}}


class WinningGoal(unittest.TestCase):
    """Краш-тест победной шайбы (ADR-008). Правило лиги: гол победителя, после которого
    соперник уже не сравнял счёт, — номер (голы проигравшего + 1) у победителя."""

    def gw(self, goals, decision=""):
        i = b.winning_goal(scored(goals, decision))
        return None if i is None else goals[i]["score"]

    def test_not_the_last_goal(self):
        # скриншот владельца: 5:2, победная — 3:1, как в протоколе лиги (ШП у Колыхалова)
        self.assertEqual(self.gw(seq("h", "a", "h", "h", "h", "h", "a")), "3:1")

    def test_single_goal(self):
        self.assertEqual(self.gw(seq("h")), "1:0")

    def test_shutout_is_first_goal(self):
        self.assertEqual(self.gw(seq("a", "a", "a")), "0:1")

    def test_comeback_is_last_goal(self):
        self.assertEqual(self.gw(seq("a", "a", "h", "h", "h")), "3:2")

    def test_lead_blown_then_retaken(self):
        # 2:0 → 2:2 → 3:2: победная — третья, ведь после 2:0 соперник сравнял
        self.assertEqual(self.gw(seq("h", "h", "a", "a", "h")), "3:2")

    def test_away_winner(self):
        self.assertEqual(self.gw(seq("h", "a", "a", "h", "a")), "2:3")

    def test_overtime_goal(self):
        goals = seq("h", "a", "a", "h")
        goals.append({**seq("h", "a", "a", "h", "a")[-1], "period": "ОТ", "time": "62:10"})
        self.assertEqual(self.gw(goals, "ОТ"), "2:3")

    def test_shootout_is_the_shootout_goal(self):
        goals = seq("h", "a", "b")
        i = b.winning_goal(scored(goals, "Б"))
        self.assertEqual(goals[i]["period"], "РБ")

    def test_no_goals_in_protocol(self):
        self.assertIsNone(b.winning_goal({"score": {"home": 2, "away": 1, "decision": ""}, "goals": []}))

    def test_draw_has_none(self):
        self.assertIsNone(self.gw(seq("h", "a")))

    def test_goals_do_not_add_up_to_score(self):
        # 21/22, «Полёт» — «Дизелист»: в протоколе итог 2:3, а голов 1:3 — ничего не выдумываем
        g = scored(seq("h", "a", "a", "a"))
        g["score"]["home"] = 2
        self.assertIsNone(b.winning_goal(g))
        self.assertEqual(b.story(g, NAMES), "")

    def test_broken_score_column(self):
        goals = seq("h", "h", "a")
        goals[1]["score"] = "1:1"
        self.assertIsNone(b.winning_goal(scored(goals)))

    def test_official_wins_over_rule(self):
        goals = seq("h", "a", "h", "h")
        self.assertEqual(b.winning_goal(scored(goals), official=3), 3)
        self.assertEqual(b.winning_goal(scored(goals), official=1), 2)   # гол проигравших — не принимаем

    def test_official_from_protocol_fixture(self):
        import league
        for name, gid, want in (("protocol_900942_regular.html", 900942, "2:1"),
                                ("protocol_901016_ot.html", 901016, "5:6")):
            p = json.loads(json.dumps(league.parse_protocol((FIX / name).read_text(encoding="utf-8"), gid).to_json()))
            i = b.official_winning_goal(p)
            self.assertIsNotNone(i, name)
            self.assertEqual(p["goals"][i]["score"], want)

    def test_story_without_double_dot(self):
        goals = seq("h", "a", "b")
        goals[-1]["author"] = "Царёв Иван А."
        self.assertEqual(b.story(scored(goals, "Б"), NAMES), "Всё решили буллиты, победный забил Царёв Иван А.")

    def test_comeback_grammar(self):
        self.assertTrue(b.story(scored(seq("a", "a", "h", "h", "h")), NAMES)
                        .startswith("Камбэк «Рязань-ВДВ»: уступали 0:2 и вырвали победу. "))

    def test_slow_three_is_not_a_story(self):
        goals = seq("h", "h", "h")                         # 21:00, 22:00, 23:00 — за две минуты
        self.assertIn("Три шайбы подряд", b.story(scored(goals), NAMES))
        goals[2]["time"] = "39:00"                          # растянулись на 18 минут
        self.assertNotIn("подряд", b.story(scored(goals), NAMES))

    def test_every_past_story_reads_well(self):
        teams = {t["id"]: t["name"] for t in b.load_teams().all}
        hist = {str(x["game_id"]): x for x in b.load_history() if x.get("game_id")}
        for k, p in b.load_history_protocols().items():
            g = {"id": k, "home": hist[k]["home"], "away": hist[k]["away"]}
            b.fill_result(g, p)
            st = b.match_detail(g, p, teams)["story"]
            self.assertNotIn("..", st, k)
            self.assertNotIn(", и ", st, k)
            self.assertTrue(not st or st.endswith("."), k)

    def test_every_past_protocol(self):
        # на всех скачанных протоколах: победная — гол победителя, дальше проигравший не сравнивает
        for k, p in b.load_history_protocols().items():
            g = {"score": {"home": p["home_score"], "away": p["away_score"], "decision": p["decision"]},
                 "goals": [{**x, "author": x["author"]["name"]} for x in p["goals"]]}
            i = b.winning_goal(g)
            if i is None:
                self.assertFalse(b.goals_consistent(g), k)
                continue
            win = "home" if p["home_score"] > p["away_score"] else "away"
            self.assertEqual(g["goals"][i]["team"], win, k)
            h, a = map(int, g["goals"][i]["score"].split(":"))
            mine, theirs = (h, a) if win == "home" else (a, h)
            lose_final = min(p["home_score"], p["away_score"])
            self.assertEqual(mine, lose_final + 1, k)
            self.assertLessEqual(theirs, lose_final, k)


class Leaders(unittest.TestCase):
    """ADR-009: топ-10 и лучший игрок каждой команды за десяткой."""
    teams = b.load_teams()
    src = {"site": "https://nmhl.fhr.ru", "name": "25/26 | Регулярный чемпионат", "categories": {"pts": [
        {"rank": i, "name": f"Игрок {i}", "id": i, "club": club, "gp": 40, "g": 10, "a": 10, "pts": 60 - i, "pm": 3}
        for i, club in enumerate(["Полёт"] * 10 + ["Буран Мск", "ХК Брянск", "Воевода", "ХК Брянск"], start=1)]}}

    def test_season_and_top(self):
        top = b.leaders(self.teams, self.src)
        self.assertEqual((top["season"], top["league"], top["stage"]), ("2025/26", "НМХЛ", "регулярный чемпионат"))
        rows = top["categories"]["pts"]
        self.assertEqual([r["rank"] for r in rows], [*range(1, 11), 12, 13])   # клуба вне РХЛ за десяткой нет
        self.assertEqual(rows[-1]["team"], "vityaz-podolsk")                   # прежнее название — нынешний клуб
        self.assertEqual(set(rows[0]), {"rank", "name", "role", "number", "gp", "g", "a", "pts", "team", "kit"})   # без id

    def test_club_outside_league_keeps_name(self):
        src = {**self.src, "categories": {"pts": [{**self.src["categories"]["pts"][10], "rank": 1}]}}
        row = b.leaders(self.teams, src)["categories"]["pts"][0]
        self.assertEqual((row["club"], row["logo"]), ("Буран Мск", "logos/past/buran-msk.png"))

    def test_past_club_logos_exist(self):
        for club in b.load_past_clubs().values():
            self.assertTrue((ROOT / "webapp" / club["logo"]).is_file(), club["logo"])

    def test_kit_for_current_and_past_clubs(self):
        kits = {"polet": {"skater": [0.8, "#ffffff"]}, "buran": {"skater": [0.8, "#ffffff"]}}
        rows = b.leaders(self.teams, self.src, kits=kits)["categories"]["pts"]
        self.assertEqual(rows[0]["kit"], "polet")                        # клуб РХЛ — по id команды
        self.assertNotIn("kit", rows[-2])                                # формы «Брянска» в этом наборе нет
        src = {**self.src, "categories": {"pts": [{**self.src["categories"]["pts"][10], "rank": 1}]}}
        self.assertEqual(b.leaders(self.teams, src, kits=kits)["categories"]["pts"][0]["kit"], "buran")   # ушедший клуб

    def test_kits_in_league_json(self):
        """Форма клубов — в league.json: её берут и лидеры, и составы в разборе матча."""
        data, _, _ = b.build(self.teams, [], {})
        self.assertEqual(data["kits"], b.load_kits())
        self.assertTrue(all(len(t["colors"]) == 2 for t in data["teams"]))   # цвета формы для Кэпа (ADR-010)
        self.assertNotIn("kits", b.leaders(self.teams, self.src))

    def test_every_club_has_both_kits(self):
        """Картинки формы есть у всех 26 команд и 4 ушедших клубов, у каждого — полевой и вратарь."""
        kits = b.load_kits()
        wanted = {t["id"] for t in self.teams.all} | {c["kit"] for c in b.load_past_clubs().values()}
        self.assertEqual(set(kits), wanted)
        for club, roles in kits.items():
            self.assertEqual(set(roles), {"skater", "goalie"}, club)
            for role, (y, ink) in roles.items():
                self.assertTrue((ROOT / "webapp" / "players" / "clubs" / f"{club}-{role}.webp").is_file(), f"{club}-{role}")
                self.assertTrue(0.6 < y < 0.92 and ink in ("#000000", "#ffffff"), f"{club}-{role}")

    def test_hidden_player(self):
        rows = b.leaders(self.teams, self.src, hidden={1})["categories"]["pts"]
        self.assertNotIn(1, [r["rank"] for r in rows])

    def test_no_file(self):
        self.assertIsNone(b.leaders(self.teams, {}))

    def test_leaders_file_in_git(self):
        data = b.leaders(self.teams, b.load_leaders())
        self.assertTrue(all(len([r for r in v if r["rank"] <= 10]) == 10 for v in data["categories"].values()))


if __name__ == "__main__":
    unittest.main()

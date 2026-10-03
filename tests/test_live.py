"""Живые источники (ADR-019): разбор онлайна КХЛ, время в календаре лиги, служба live.

Фикстуры *_synthetic.html — синтетика, не снимки сайтов: настоящую вёрстку online.khl.ru и
rhl.fhr.ru мы не видели. Снимки снимет tools/probe_sources.py на сервере. Сеть не трогаем.
"""
import asyncio
import json
import logging
import re
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_data  # noqa: E402
import khl_online as ko  # noqa: E402
import league  # noqa: E402
import live  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
TZ = ZoneInfo("Europe/Moscow")
SITE = "https://rhl.example"
TEAMS = build_data.load_teams()


def setUpModule():
    logging.disable(logging.WARNING)   # «не сопоставлено», «нет страницы» — ожидаемые в тестах предупреждения


def tearDownModule():
    logging.disable(logging.NOTSET)


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def msk(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=TZ)


def match_page(n: int, pair: str, status: str, score: str = "", league_name: str = "РХЛ") -> str:
    return (f"<html><head><title>Хоккей. {league_name}. Регулярный чемпионат. Игра номер {n} 03 окт 2026: {pair} "
            f"(онлайн трансляция)</title></head><body><div class='scoreboard'><div class='score'>{score}</div>"
            f"<div class='status'>{status}</div></div></body></html>")


def day_block(inner: str) -> list[dict]:
    return ko.parse_day_list(f"<html><body><div class='list'>{inner}</div></body></html>", TEAMS)


class Title(unittest.TestCase):
    def test_khl(self):
        t = ko.parse_title("Хоккей. КХЛ. Регулярный чемпионат. Игра номер 478 11 янв 2026: ЦСКА-Спартак (онлайн трансляция)")
        self.assertEqual(t["league"], "КХЛ")
        self.assertEqual(t["stage"], "Регулярный чемпионат")
        self.assertEqual((t["n"], t["date"], t["home"], t["away"]), (478, "2026-01-11", "ЦСКА", "Спартак"))

    def test_vhl_with_hyphens_in_names(self):
        title = "Хоккей. ВХЛ. Регулярный чемпионат. Игра номер 113 27 сен 2026: Динамо-Алтай-Омские Крылья (онлайн трансляция)"
        t = ko.parse_title(title)
        self.assertEqual((t["league"], t["n"], t["date"]), ("ВХЛ", 113, "2026-09-27"))
        self.assertEqual(t["pair"], "Динамо-Алтай-Омские Крылья")
        # обе команды не из РХЛ, дефисов два — делить наугад не будем
        self.assertEqual((t["home"], t["away"]), (None, None))
        self.assertEqual((ko.parse_title(title, TEAMS)["home"]), None)

    def test_nmhl_cup_split_by_teams(self):
        title = ("Хоккей. НМХЛ - Кубок Регионов. Плей-офф. Игра номер 53 13 мая 2026: Металлург ВО-Гранит-Чехов "
                 "(онлайн трансляция)")
        t = ko.parse_title(title, TEAMS)
        self.assertEqual((t["league"], t["stage"], t["n"], t["date"]), ("НМХЛ - Кубок Регионов", "Плей-офф", 53, "2026-05-13"))
        self.assertEqual((t["home"], t["away"]), ("Металлург ВО", "Гранит-Чехов"))
        self.assertEqual(ko.match_teams(TEAMS, t["home"], t["away"]), ("metallurg", "akhmat-granit"))
        self.assertEqual((ko.parse_title(title)["home"]), None)   # без teams.json не делим

    def test_rhl_with_hyphen_team(self):
        t = ko.parse_title("Хоккей. РХЛ. Регулярный чемпионат. Игра номер 12 03 окт 2026: Рязань-ВДВ-Белгород "
                           "(онлайн трансляция)", TEAMS)
        self.assertEqual((t["home"], t["away"], t["date"]), ("Рязань-ВДВ", "Белгород", "2026-10-03"))

    def test_vhl_ryazan_split_by_one_known_team(self):
        t = ko.parse_title("Хоккей. ВХЛ. Регулярный чемпионат. Игра номер 120 03 окт 2026: Молот-Рязань-ВДВ "
                           "(онлайн трансляция)", TEAMS)
        self.assertEqual((t["home"], t["away"]), ("Молот", "Рязань-ВДВ"))

    def test_not_a_match_title(self):
        self.assertIsNone(ko.parse_title("Хоккей. Хоккей. Список онлайн трансляций матчей Октябрь 03, 2026"))
        self.assertIsNone(ko.parse_title(""))

    def test_league_is_rhl(self):
        for name in ("РХЛ", "Российская хоккейная лига", "НМХЛ", "НМХЛ - Кубок Регионов"):
            self.assertTrue(ko.is_rhl(name), name)
        for name in ("КХЛ", "ВХЛ", "МХЛ", "ЖХЛ", "", None):
            self.assertFalse(ko.is_rhl(name), name)


class Teams(unittest.TestCase):
    def test_prefixes_and_suffixes(self):
        cases = {"МХК Кристалл С": "kristall", "ХК Краснодар": "krasnodar", "МХК Ростов": "rostov",
                 "ХК Сокол ЧР": "sokol", "Металлург ВО": "metallurg", "Гранит-Чехов": "akhmat-granit",
                 "МХК Рязань-ВДВ": "ryazan-vdv", "Тверичи - СШОР": "tverichi", "Кристалл (Саратов)": "kristall"}
        for raw, tid in cases.items():
            self.assertEqual(ko.find_team(TEAMS, raw), tid, raw)

    def test_unknown_team(self):
        for raw in ("Молот", "ЦСКА", "Динамо СПб", "Металлург Мг", "", None):
            self.assertIsNone(ko.find_team(TEAMS, raw), raw)

    def test_split_pair(self):
        self.assertEqual(ko.split_pair("Протон — МХК Кристалл С"), ("Протон", "МХК Кристалл С"))
        self.assertEqual(ko.split_pair("Молот - Рязань-ВДВ"), ("Молот", "Рязань-ВДВ"))
        self.assertEqual(ko.split_pair("ЦСКА-Спартак", TEAMS), ("ЦСКА", "Спартак"))
        self.assertEqual(ko.split_pair("Динамо-576-Тайфун", TEAMS), ("Динамо-576", "Тайфун"))
        self.assertEqual(ko.split_pair("Рязань-ВДВ", TEAMS), (None, None))   # одна команда, не пара


class DayList(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        html = fixture("khl_online_day_synthetic.html")
        cls.date = ko.parse_day_date(html)
        cls.items = {i["khl_id"]: i for i in ko.parse_day_list(html, TEAMS)}

    def test_date_from_title(self):
        self.assertEqual(self.date, date(2026, 10, 3))

    def test_all_matches_once(self):
        self.assertEqual(sorted(self.items), [904950, 904951, 904952, 904953, 904954, 905001, 905100])
        self.assertEqual(self.items[904950]["url"], "https://online.khl.ru/online/904950.html")   # ссылка была абсолютной

    def test_start_time(self):
        self.assertEqual(self.items[904950]["time"], "17:00")
        self.assertEqual(self.items[904951]["time"], "13:00")   # время простым текстом после команд
        self.assertEqual(self.items[904952]["time"], "15:00")

    def test_score_and_status(self):
        live_game = self.items[904952]
        self.assertEqual((live_game["score"], live_game["status"]), ({"home": 2, "away": 1, "decision": None}, "live"))
        ended = self.items[904953]
        self.assertEqual((ended["score"], ended["status"], ended["time"]), ({"home": 3, "away": 4, "decision": "ОТ"}, "ended", None))

    def test_clock_is_not_start_time(self):
        it = self.items[904954]   # «1-й период 12:34 1:0»
        self.assertIsNone(it["time"])
        self.assertEqual(it["score"], {"home": 1, "away": 0, "decision": None})

    def test_league_hint_from_section(self):
        self.assertEqual({k: v["league"] for k, v in self.items.items()},
                         {905100: "КХЛ", 905001: "ВХЛ", 904950: "РХЛ", 904951: "РХЛ", 904952: "РХЛ", 904953: "РХЛ",
                          904954: "РХЛ"})

    def test_raw_team_names(self):
        self.assertEqual((self.items[904950]["home"], self.items[904950]["away"]), ("Протон", "МХК Кристалл С"))
        self.assertEqual((self.items[905001]["home"], self.items[905001]["away"]), ("Молот", "Рязань-ВДВ"))
        self.assertEqual((self.items[904952]["home"], self.items[904952]["away"]), ("МХК Рязань-ВДВ", "МХК Белгород"))


class TimeOrScore(unittest.TestCase):
    """Время начала и счёт выглядят одинаково: «17:00» и «2:1». Сомнение — None."""

    def test_time_in_score_slot_before_start(self):
        it, = day_block("<div><a href='/online/1.html'>Протон — Кристалл</a> <span class='score'>17:00</span></div>")
        self.assertEqual((it["time"], it["score"]), ("17:00", None))

    def test_score_in_break(self):
        it, = day_block("<div><a href='/online/1.html'>Протон — Кристалл</a> <span class='score'>2:1</span>"
                        "<span class='status'>1-й перерыв</span></div>")
        self.assertEqual((it["time"], it["score"]["home"], it["score"]["away"], it["status"]), (None, 2, 1, "break"))

    def test_short_score_in_score_slot_is_not_time(self):
        it, = day_block("<div><a href='/online/1.html'>Протон — Кристалл</a> <span class='score'>1:10</span></div>")
        self.assertEqual((it["time"], it["score"]), (None, None))

    def test_ambiguous_score_is_none(self):
        it, = day_block("<div><a href='/online/1.html'>Протон — Кристалл</a> 2:10 окончен</div>")
        self.assertEqual((it["time"], it["score"], it["status"]), (None, None, "ended"))

    def test_two_times_is_none(self):
        it, = day_block("<div><a href='/online/1.html'>Протон — Кристалл</a> 17:00 (местное 19:00)</div>")
        self.assertIsNone(it["time"])

    def test_table_row_layout(self):
        items = day_block("<table><tr><td>17:00</td><td><a href='/online/1.html'>Протон — Кристалл</a></td><td>-:-</td></tr>"
                          "<tr><td>13:00</td><td><a href='/online/2.html'>Ростов — Краснодар</a></td><td>-:-</td></tr></table>")
        self.assertEqual([(i["khl_id"], i["time"], i["home"]) for i in items], [(1, "17:00", "Протон"), (2, "13:00", "Ростов")])

    def test_flat_layout(self):
        items = day_block("<a href='/online/1.html'>Протон — Кристалл</a> 17:00<br>"
                          "<a href='/online/2.html'>Ростов — Краснодар</a> 13:00")
        self.assertEqual([(i["khl_id"], i["time"]) for i in items], [(1, "17:00"), (2, "13:00")])


class MatchPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = ko.parse_match(fixture("khl_online_match_rhl_synthetic.html"), TEAMS)

    def test_title(self):
        t = self.m["title"]
        self.assertEqual((t["league"], t["n"], t["home"], t["away"]), ("РХЛ", 12, "Рязань-ВДВ", "Белгород"))

    def test_status_period_clock_score(self):
        m = self.m
        self.assertEqual((m["status"], m["period"], m["clock"]), ("live", "2", "12:34"))   # таймер «32:34» сквозной
        self.assertEqual(m["score"], {"home": 2, "away": 1, "decision": None})

    def test_events_oldest_first(self):
        ev = self.m["events"]
        self.assertEqual([e["time"] for e in ev], ["00:00", "05:12", "20:00", "20:00", "24:05", "27:10", "32:34"])
        self.assertEqual([e["kind"] for e in ev], ["period", "goal", "period", "period", "goal", "penalty", "goal"])
        self.assertEqual([e["period"] for e in ev], ["1", "1", "1", "2", "2", "2", "2"])

    def test_goal_sides_and_scores(self):
        goals = [e for e in self.m["events"] if e["kind"] == "goal"]
        self.assertEqual([(g["team"], g["score"]) for g in goals], [("home", "1:0"), ("away", "1:1"), ("home", "2:1")])
        self.assertEqual(goals[-1]["text"], "Гол! МХК Рязань-ВДВ. Иванов (Петров, Сидоров). 2:1")

    def test_side_list_of_other_matches_is_not_feed(self):
        self.assertFalse(any("Протон" in e["text"] for e in self.m["events"]))

    def test_not_started(self):
        m = ko.parse_match(fixture("khl_online_match_vhl_synthetic.html"), TEAMS)
        self.assertEqual((m["title"]["league"], m["status"]), ("ВХЛ", "sched"))
        self.assertNotIn("score", m)

    def test_statuses_by_words(self):
        cases = {"Матч окончен": ("ended", None), "Перерыв после 1-го": ("break", "1"), "2-й перерыв": ("break", "2"),
                 "3-й период": ("live", "3"), "Овертайм": ("live", "ОТ"), "ОТ": ("live", "ОТ"),
                 "Буллиты": ("live", "РБ"), "Перенесён": ("moved", None), "Отменён": ("off", None),
                 "Матч не начался": ("sched", None), "Завершён": ("ended", None)}
        for words, (status, period) in cases.items():
            m = ko.parse_match(match_page(1, "Протон-Кристалл", words, "1:0"), TEAMS)
            self.assertEqual((m.get("status"), m.get("period")), (status, period), words)

    def test_ended_in_overtime(self):
        m = ko.parse_match(match_page(1, "Тамбов-Дизелист", "Матч окончен", "3:4 ОТ"), TEAMS)
        self.assertEqual((m["status"], m["score"]), ("ended", {"home": 3, "away": 4, "decision": "ОТ"}))
        m = ko.parse_match(match_page(1, "Тамбов-Дизелист", "Матч окончен по буллитам", "3:2"), TEAMS)
        self.assertEqual(m["score"]["decision"], "Б")

    def test_page_text_is_strict(self):
        # шапка таблицы по периодам — не статус; голое «ОТ» в тексте страницы — тоже
        self.assertIsNone(ko.status_from_text("1-й период 2-й период 3-й период ОТ", strict=True))
        self.assertEqual(ko.status_from_text("1-й период"), {"status": "live", "period": "1"})

    def test_other_matches_and_period_table_are_not_status(self):
        board = "<div class='board'><a href='/online/1.html'>обновить</a><span class='score'>-:-</span>{}</div>"
        html = ("<html><head><title>Хоккей. РХЛ. Регулярный чемпионат. Игра номер 3 03 окт 2026: Протон-Кристалл "
                "(онлайн трансляция)</title></head><body><div class='games'>"
                "<div><a href='/online/2.html'>Ростов — Краснодар</a><span class='status'>окончен</span>"
                "<span class='score'>4:2</span></div>"
                "<div><a href='/online/3.html'>Самара — Сокол</a><span class='status'>1-й период</span></div></div>"
                "<table><tr><th class='period'>1-й период</th><th class='period'>2-й период</th></tr></table>{}</body></html>")
        m = ko.parse_match(html.format(board.format("<span class='status'>Матч не начался</span>")), TEAMS, 1)
        self.assertEqual(m["status"], "sched")
        self.assertNotIn("score", m)
        m = ko.parse_match(html.format(board.format("")), TEAMS, 1)
        self.assertNotIn("status", m)   # статуса у самого матча нет — не берём чужой «окончен»

    def test_events_capped_at_60(self):
        rows = "".join(f"<li>{m:02d}:00 Бросок в створ номер {m}</li>" for m in range(80))
        m = ko.parse_match(f"<html><body><ul class='feed'>{rows}</ul></body></html>")
        self.assertEqual(len(m["events"]), ko.MAX_EVENTS)
        self.assertEqual(m["events"][-1]["time"], "79:00")   # новые в конце

    def test_unknown_page_gives_nothing(self):
        self.assertEqual(ko.parse_match("<html><body><p>Ничего нет</p></body></html>"), {})

    def test_decode_cp1251(self):
        body = "<html><head><meta charset=windows-1251><title>Хоккей</title></head></html>".encode("cp1251")
        self.assertIn("Хоккей", ko.decode_html(body))
        self.assertIn("Хоккей", ko.decode_html(body, "text/html; charset=windows-1251"))


class CalendarTimes(unittest.TestCase):
    def test_future_games_with_time(self):
        rows = league.parse_calendar_times(fixture("rhl_calendar_synthetic.html"), 2001)
        self.assertEqual([(r["date"], r["time"], r["home"], r["away"], r["idgame"], r["n"]) for r in rows], [
            ("2026-10-03", "15:00", "МХК Рязань-ВДВ", "МХК Белгород", 904952, 12),
            ("2026-10-03", "17:00", "МХК Ермак", "Факел Ямал", 904960, 13),
            ("2026-10-04", "16:00", "МХК Рязань-ВДВ", "МХК Тамбов", 904961, 20),
            ("2026-10-04", "18:30", "Тайфун СПб", "Протон", None, 21)])

    def test_scores_are_not_times(self):
        for name in ("calendar_1378_ryazan.html", "calendar_1379_all.html", "calendar_1379_ryazan_playoff.html"):
            html = fixture(name)
            self.assertEqual(league.parse_calendar_times(html), [], name)
            # и без заголовка «Завершившиеся»: «6:1», «5:4 Б» — счёт, а не время
            self.assertEqual(league.parse_calendar_times(html.replace("Завершившиеся", "Матчи")), [], name)

    def test_old_parsers_unchanged(self):
        html = fixture("rhl_calendar_synthetic.html")
        self.assertEqual(league.parse_game_ids(html, 2001), [904900, 904960])
        self.assertEqual(league.parse_tournaments(html)[0], (2001, "Регулярный чемпионат 2026/27"))


class FakeSite:
    """Сайты из словаря: адрес → страница. Чего нет — ошибка, как упавший сайт."""

    def __init__(self, pages: dict[str, str]):
        self.pages, self.asked = pages, []

    async def __call__(self, url: str) -> str:
        self.asked.append(url)
        if url not in self.pages:
            raise ConnectionError(f"нет страницы {url}")
        return self.pages[url]


def pages(**over) -> dict[str, str]:
    p = {
        ko.DAY_URL: fixture("khl_online_day_synthetic.html"),
        ko.match_url(905001): fixture("khl_online_match_vhl_synthetic.html"),
        ko.match_url(904950): match_page(5, "Протон-Кристалл", "Матч не начался"),
        ko.match_url(904951): match_page(6, "Ростов-Краснодар", "Матч окончен", "4:2"),
        ko.match_url(904952): fixture("khl_online_match_rhl_synthetic.html"),
        ko.match_url(904953): match_page(7, "Тамбов-Дизелист", "Матч окончен", "3:4 ОТ"),
        ko.match_url(904954): match_page(8, "Самара-Сокол", "1-й период", "1:0"),
        f"{SITE}/calendar/": fixture("rhl_calendar_synthetic.html"),
        f"{SITE}/calendar/2001/": fixture("rhl_calendar_synthetic.html"),
    }
    p.update(over)
    return {k: v for k, v in p.items() if v is not None}


class Service(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.now = [msk("2026-10-03T16:10:00")]
        self.site = FakeSite(pages())

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, site: str | None = SITE) -> live.Live:
        return live.Live(self.dir, TEAMS, self.site, clock=lambda: self.now[0], site=site)

    def run_step(self, lv: live.Live, force: bool = False) -> bool:
        return asyncio.run(lv.step(force=force))

    def read(self, name: str) -> dict:
        return json.loads((self.dir / name).read_text(encoding="utf-8"))

    def today(self) -> dict[str, dict]:
        return {g["key"]: g for g in self.read("today.json")["games"]}

    def test_rhl_games_of_the_day(self):
        self.run_step(self.make())
        games = self.today()
        self.assertEqual(sorted(games), ["2026-10-03|ermak|fakel-yamal", "2026-10-03|proton|kristall",
                                         "2026-10-03|rostov|krasnodar", "2026-10-03|ryazan-vdv|belgorod",
                                         "2026-10-03|samara|sokol", "2026-10-03|tambov|dizelist"])
        g = games["2026-10-03|ryazan-vdv|belgorod"]
        self.assertEqual((g["status"], g["period"], g["clock"], g["time"]), ("live", "2", "12:34", "15:00"))
        self.assertEqual(g["start"], "2026-10-03T15:00:00+03:00")
        self.assertEqual(g["score"], {"home": 2, "away": 1, "decision": None})
        self.assertEqual((g["online"], g["khl_id"], g["src"]), ("https://online.khl.ru/online/904952.html", 904952, "online.khl.ru"))
        self.assertEqual(len(g["events"]), 7)
        self.assertEqual(games["2026-10-03|proton|kristall"]["status"], "soon")       # 17:00, меньше часа
        self.assertIsNone(games["2026-10-03|proton|kristall"]["score"])
        self.assertEqual(games["2026-10-03|tambov|dizelist"]["score"]["decision"], "ОТ")
        cal = games["2026-10-03|ermak|fakel-yamal"]   # только в календаре сайта лиги
        # Календарь даёт местное время арены: Ангарск — МСК+5, 17:00 там — 12:00 по Москве. Начало было
        # больше трёх часов назад, живых данных нет — статуса нет: не выдумываем
        self.assertEqual((cal["time"], cal["src"], cal["khl_id"], cal["status"]), ("12:00", "rhl.example", 904960, None))

    def test_vhl_ryazan_is_not_rhl(self):
        self.run_step(self.make())
        self.assertFalse(any("ryazan-vdv" in k and "belgorod" not in k for k in self.today()))
        ids = self.read("khl_ids.json")
        self.assertEqual((ids["905001"]["league"], ids["905001"]["rhl"]), ("ВХЛ", False))
        self.assertTrue(ids["904952"]["rhl"])
        self.assertNotIn(ko.match_url(905100), self.site.asked)   # «ЦСКА — Спартак»: команд РХЛ нет, не спрашиваем

    def test_league_cached_forever(self):
        self.run_step(self.make())
        self.site.asked.clear()
        self.now[0] += timedelta(seconds=31)
        lv = self.make()   # и после перезапуска: кэш в live/khl_ids.json
        self.run_step(lv)
        self.assertNotIn(ko.match_url(905001), self.site.asked)
        self.assertNotIn(ko.match_url(904950), self.site.asked)   # 17:00 — ещё не окно матча
        self.assertIn(ko.match_url(904952), self.site.asked)      # идёт — страница раз в 30 секунд
        self.assertIn(ko.match_url(904954), self.site.asked)

    def test_protocol_link_for_ended(self):
        self.run_step(self.make())
        games = self.today()
        self.assertIsNone(games["2026-10-03|ryazan-vdv|belgorod"]["protocol"])   # ещё идёт
        # «Ростов — Краснодар» окончен, но его idgame нет в календаре — турнир неизвестен
        self.assertIsNone(games["2026-10-03|rostov|krasnodar"]["protocol"])
        self.site.pages[ko.match_url(904952)] = match_page(12, "Рязань-ВДВ-Белгород", "Матч окончен", "4:1")
        self.now[0] += timedelta(seconds=31)
        lv = self.make()
        self.run_step(lv)
        g = self.today()["2026-10-03|ryazan-vdv|belgorod"]
        self.assertEqual(g["status"], "ended")
        self.assertEqual(g["protocol"], f"{SITE}/report/2001/?idgame=904952")
        self.assertEqual(len(g["events"]), 7)   # лента не пропадает, когда страница её больше не отдаёт

    def test_schedule(self):
        self.run_step(self.make())
        sched = {g["key"]: g for g in self.read("schedule.json")["games"]}
        tomorrow = sched["2026-10-04|ryazan-vdv|tambov"]
        self.assertEqual((tomorrow["time"], tomorrow["start"], tomorrow["khl_id"], tomorrow["src"]),
                         ("16:00", "2026-10-04T16:00:00+03:00", 904961, "rhl.example"))
        self.assertEqual(sched["2026-10-03|proton|kristall"]["src"], "online.khl.ru")
        self.assertEqual(sched["2026-10-03|ryazan-vdv|belgorod"]["src"], "online.khl.ru")   # онлайн старше календаря
        self.assertFalse(any("proton" in k and k.startswith("2026-10-04") for k in sched))   # «Тайфун СПб» не сопоставлен
        self.assertIn("Тайфун СПб", self.read("sources.json")["rhl.example"]["note"])

    def test_tomorrow_list(self):
        tomorrow = ("<html><head><title>Хоккей. Хоккей. Список онлайн трансляций матчей Октябрь 04, 2026</title></head>"
                    "<body><div><a href='/online/906000.html'>Протон — Кристалл</a> 16:00</div></body></html>")
        page = match_page(30, "Протон-Кристалл", "Матч не начался").replace("03 окт", "04 окт")
        self.site = FakeSite(pages(**{f"{ko.DAY_URL}?d=2026-10-04": tomorrow, ko.match_url(906000): page}))
        lv = live.Live(self.dir, TEAMS, self.site, clock=lambda: self.now[0], site=None,
                       tomorrow_url=ko.DAY_URL + "?d={date}")
        self.run_step(lv)
        sched = {g["key"]: g for g in self.read("schedule.json")["games"]}
        self.assertEqual(sched["2026-10-04|proton|kristall"]["start"], "2026-10-04T16:00:00+03:00")
        # по тому же адресу отдали сегодняшний список — завтрашним его не считаем
        self.site.pages[f"{ko.DAY_URL}?d=2026-10-04"] = fixture("khl_online_day_synthetic.html")
        lv.last_tomorrow = None
        self.run_step(lv)
        self.assertIn("не узнан", self.read("sources.json")["online.khl.ru"]["note"])

    def test_stale_live_has_no_minute(self):
        lv = self.make()
        self.run_step(lv)
        g = lv.games["2026-10-03|ryazan-vdv|belgorod"]
        later = lv.render(g, self.now[0] + timedelta(minutes=6))
        self.assertEqual((later["status"], later["period"], later["clock"]), ("live", None, None))
        self.assertEqual(later["score"], {"home": 2, "away": 1, "decision": None})   # последний счёт остаётся

    def test_one_source_down(self):
        self.site = FakeSite(pages(**{f"{SITE}/calendar/": None}))
        self.run_step(self.make())
        src = self.read("sources.json")
        self.assertEqual(src["rhl.example"]["errors"], 1)
        self.assertIsNotNone(src["rhl.example"]["fail"])
        self.assertEqual(src["online.khl.ru"]["errors"], 0)
        self.assertEqual(src["online.khl.ru"]["games"], 5)
        self.assertIn("2026-10-03|samara|sokol", self.today())

    def test_page_errors_counted(self):
        self.site = FakeSite(pages(**{ko.match_url(904954): None}))
        self.run_step(self.make())
        self.now[0] += timedelta(seconds=31)
        lv = self.make()
        self.run_step(lv)
        self.assertGreaterEqual(self.read("sources.json")["online.khl.ru"]["errors"], 1)

    def test_restart_keeps_state(self):
        self.run_step(self.make())
        lv = self.make()
        g = lv.render(lv.games["2026-10-03|tambov|dizelist"], self.now[0])
        self.assertEqual((g["status"], g["score"]["decision"]), ("ended", "ОТ"))

    def test_night_is_quiet(self):
        self.now[0] = msk("2026-10-04T03:00:00")
        lv = self.make()
        self.assertFalse(self.run_step(lv))
        self.assertEqual(self.site.asked, [])
        self.assertEqual(lv.next_delay(self.now[0]), 900.0)
        self.now[0] = msk("2026-10-04T07:58:00")
        self.assertEqual(lv.next_delay(self.now[0]), 120.0)

    def test_poll_intervals(self):
        lv = self.make()
        self.assertEqual(lv.list_every(self.now[0]), live.LIST_SLOW)   # матчей нет — раз в 10 минут
        self.run_step(lv)
        self.assertEqual(lv.list_every(self.now[0]), live.LIST_FAST)   # идёт матч РХЛ — раз в 30 секунд
        self.assertLessEqual(lv.next_delay(self.now[0]), 30.0)

    def test_files_follow_adr_019(self):
        self.run_step(self.make())
        allowed = {"key", "date", "home", "away", "start", "time", "status", "period", "clock", "score", "events",
                   "online", "khl_id", "protocol", "seen", "src"}
        iso_msk = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+03:00$")
        day = self.read("2026-10-03.json")
        self.assertEqual(day, self.read("today.json"))
        self.assertEqual(set(day), {"date", "updated", "games"})
        self.assertRegex(day["updated"], iso_msk)
        for g in day["games"]:
            self.assertLessEqual(set(g), allowed)
            self.assertEqual(g["key"], f"{g['date']}|{g['home']}|{g['away']}")
            self.assertIn(g.get("status"), {"sched", "soon", "live", "break", "ended", "final", "moved", "off", None})
            self.assertIn(g.get("period"), {"1", "2", "3", "ОТ", "РБ", None})
            if g.get("clock"):
                self.assertRegex(g["clock"], r"^\d\d:\d\d$")
            if g.get("score"):
                self.assertEqual(set(g["score"]), {"home", "away", "decision"})
                self.assertIn(g["score"]["decision"], {None, "ОТ", "Б"})
            self.assertLessEqual(len(g.get("events") or []), 60)
            for e in g.get("events") or []:
                self.assertLessEqual(set(e), {"period", "time", "team", "kind", "text", "score"})
                self.assertIn(e["kind"], {"goal", "penalty", "period", "text"})
                self.assertIn(e["team"], {"home", "away", None})
            for f in ("start", "seen"):
                if g.get(f):
                    self.assertRegex(g[f], iso_msk)
        sched = self.read("schedule.json")
        self.assertEqual(set(sched), {"updated", "games"})
        for g in sched["games"]:
            self.assertLessEqual(set(g), {"key", "date", "home", "away", "start", "time", "online", "khl_id", "src"})
            self.assertGreaterEqual(g["date"], "2026-10-03")
            self.assertLess(g["date"], "2026-10-17")
        for name, s in self.read("sources.json").items():
            self.assertEqual(set(s), {"ok", "fail", "errors", "games", "note"}, name)


class Statuses(unittest.TestCase):
    def test_by_start_time(self):
        start = msk("2026-10-03T17:00:00")
        self.assertEqual(live.time_status(start, start - timedelta(hours=2)), "sched")
        self.assertEqual(live.time_status(start, start - timedelta(minutes=59)), "soon")
        self.assertEqual(live.time_status(start, start + timedelta(minutes=5)), "soon")   # живых данных нет — не «идёт»
        self.assertIsNone(live.time_status(start, start + timedelta(hours=5)))
        self.assertIsNone(live.time_status(None, start))

    def test_quiet_hours(self):
        for t, q in (("00:30", False), ("01:00", True), ("03:00", True), ("07:59", True), ("08:00", False), ("23:00", False)):
            self.assertEqual(live.quiet(msk(f"2026-10-03T{t}:00")), q, t)


class AtomicWrite(unittest.TestCase):
    def test_write_replaces_whole_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "today.json"
            live.write_json(path, {"games": [1]})
            live.write_json(path, {"games": [2]})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"games": [2]})
            self.assertEqual([p.name for p in Path(d).iterdir()], ["today.json"])   # временного файла не осталось

    def test_bad_data_keeps_old_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "today.json"
            live.write_json(path, {"games": [1]})
            with self.assertRaises(TypeError):
                live.write_json(path, {"games": [object()]})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"games": [1]})
            self.assertEqual([p.name for p in Path(d).iterdir()], ["today.json"])


if __name__ == "__main__":
    unittest.main()

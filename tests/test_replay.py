"""Повторы голов (ADR-027): разбор ссылок VK, секунда записи для каждого гола, ссылки в league.json и /replay."""
import html
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_data  # noqa: E402
import replay  # noqa: E402

TZ = ZoneInfo("Europe/Moscow")
VIDEO = "https://vk.com/video-187307324_456239889"
PLAY = "https://vkvideo.ru/video_ext.php?oid=-187307324&id=456239889"   # плеер: открывается с секунды (05.10)


def msk(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


def goal(score: str, team: str, at: str | None, **kw) -> dict:
    """Событие гола, как его пишет служба live в live/<дата>.json."""
    return {"kind": "goal", "team": team, "score": score, "period": "1", "time": None, "text": None,
            "at": msk(at).isoformat() if at else None, "src": "rhl.fhr.ru", **kw}


# Матч 03.10: голы замечены в 17:21:30, 17:40:00, 18:05:00 (после перерыва), ещё один — после долгой паузы опроса
GAME = {"key": "2026-10-03|tverichi|metallurg", "date": "2026-10-03", "home": "tverichi", "away": "metallurg",
        "status": "ended", "score": {"home": 2, "away": 2},
        "events": [{"kind": "period", "text": "Начался 1-й период", "at": msk("2026-10-03T17:05:00").isoformat()},
                   goal("1:0", "home", "2026-10-03T17:21:30", text="Иванов Иван"),
                   goal("1:1", "away", "2026-10-03T17:40:00"),
                   goal("2:1", "home", "2026-10-03T18:05:00"),
                   goal(None, "away", "2026-10-03T18:30:00"),                     # счёт неизвестен — не берём
                   goal("2:2", "away", "2026-10-03T18:50:00", late=True)]}         # замечен после паузы опроса


class Links(unittest.TestCase):
    def test_vk_link_with_time(self):
        self.assertEqual(replay.parse_link(f"{PLAY}&t=872"), (VIDEO, 872))
        self.assertEqual(replay.parse_link("https://vkvideo.ru/video-187307324_456239889?t=1h2m3s"), (VIDEO, 3723))
        self.assertEqual(replay.parse_link("вот: https://vk.com/video?z=video-187307324_456239889%2Fclub1&t=95"),
                         (VIDEO, 95))
        self.assertEqual(replay.parse_link("https://vk.ru/video_ext.php?oid=-187307324&id=456239889&t=40s"), (VIDEO, 40))
        self.assertEqual(replay.parse_link(VIDEO), (VIDEO, None))               # без времени — ролик есть, секунды нет
        # запись эфира: ссылки, которые админ прислал 04.10, — тот же ролик, что лига публикует как video-
        self.assertEqual(replay.parse_link("https://vkvideo.ru/live-241266819_456239067"),
                         ("https://vk.com/video-241266819_456239067", None))

    def test_not_vk(self):
        for text in ("14:32", "https://youtu.be/abc?t=30", "https://vk.com/club123", "https://evil.example/video-1_2?t=5"):
            self.assertIsNone(replay.parse_link(text), text)

    def test_time_formats(self):
        self.assertEqual([replay.parse_t(x) for x in ("14m32s", "1h2m3s", "872", "872s", "45s", "", "abc", "99h")],
                         [872, 3723, 872, 872, 45, None, None, None])
        self.assertEqual([replay.parse_clock(x) for x in ("14:32", "1:02:03", "0:45", "14:75", "1:75:00", "abc")],
                         [872, 3723, 45, None, None, None])
        self.assertEqual([replay.fmt_t(x) for x in (872, 3723, 45, 3600, -5)], ["14m32s", "1h2m3s", "45s", "1h0m0s", "0s"])
        self.assertEqual(replay.at_link(VIDEO, 872), f"{PLAY}&t=872")
        self.assertEqual(replay.parse_times(f"{VIDEO}_456239067\n25:20\n57:04 1:08:03\nи 99:99"), [1520, 3424, 4083])


class Place(unittest.TestCase):
    def test_goals_of_skips_unknown(self):
        goals = replay.goals_of(GAME)
        self.assertEqual([(g["score"], g["at"] is not None) for g in goals],
                         [("1:0", True), ("1:1", True), ("2:1", True), ("2:2", False)])

    def test_one_anchor_moves_all(self):
        # админ отметил 1:0 на 30:00 записи: 1:1 замечен через 18,5 минуты по часам — 48:30 минус запас
        got = {g["score"]: (g["t"], g["exact"]) for g in replay.place(replay.goals_of(GAME), {"1:0": 1800})}
        self.assertEqual(got["1:0"], (1800 - replay.EXACT_LEAD, True))
        self.assertEqual(got["1:1"], (1800 + 1110 - replay.GUESS_LEAD, False))
        self.assertEqual(got["2:1"], (1800 + 2610 - replay.GUESS_LEAD, False))
        self.assertNotIn("2:2", got)                       # замечен после паузы: времени гола нет

    def test_nearest_anchor_wins(self):
        # трансляция прервалась в перерыве: второй гол отмечен отдельно, третий считается от него
        got = {g["score"]: g["t"] for g in replay.place(replay.goals_of(GAME), {"1:0": 1800, "1:1": 3500})}
        self.assertEqual(got["1:1"], 3500 - replay.EXACT_LEAD)
        self.assertEqual(got["2:1"], 3500 + 1500 - replay.GUESS_LEAD)

    def test_other_period_not_guessed(self):
        # между периодами запись и часы расходятся на минуты (04.10): гол 2-го периода от опоры 1-го не считаем
        game = {**GAME, "events": [{**e, "period": "2"} if e.get("score") == "2:1" else e for e in GAME["events"]]}
        got = {g["score"] for g in replay.place(replay.goals_of(game), {"1:0": 1800})}
        self.assertEqual(got, {"1:0", "1:1"})

    def test_protocol_order_and_missing_goal(self):
        # гол 1:1 служба не узнала (счёт без него), протокол знает: в списке он есть, но без времени по часам
        live_goals = [g for g in replay.goals_of(GAME) if g["score"] != "1:1"]
        protocol = [{"score": "1:0", "team": "home", "period": "1", "author": "Иванов Иван"},
                    {"score": "1:1", "team": "away", "period": "1", "author": "Петров Пётр"},
                    {"score": "2:1", "team": "home", "period": "2", "author": "Сидоров"}]
        goals = replay.with_protocol(live_goals, protocol)
        self.assertEqual([(g["score"], g["period"], g["at"] is not None) for g in goals],
                         [("1:0", "1", True), ("1:1", "1", False), ("2:1", "2", True)])
        self.assertEqual(goals[1]["text"], "Петров Пётр")

    def test_anchor_on_late_goal(self):
        # гол без времени по часам можно отметить руками: точный повтор у него есть, опорой он не служит
        got = {g["score"]: g["t"] for g in replay.place(replay.goals_of(GAME), {"2:2": 7000})}
        self.assertEqual(got, {"2:2": 7000 - replay.EXACT_LEAD})

    def test_never_negative(self):
        got = {g["score"]: g["t"] for g in replay.place(replay.goals_of(GAME), {"1:1": 60})}
        self.assertEqual(got["1:0"], 0)

    def test_entry_and_by_score(self):
        e = replay.entry(GAME, VIDEO, {"1:0": 1800}, msk("2026-10-03T21:00:00"))
        self.assertEqual(e["video"], VIDEO)
        self.assertEqual(e["goals"][0]["url"], f"{PLAY}&t=1790")
        links = replay.by_score(e)
        self.assertEqual(set(links), {"1:0", "1:1", "2:1"})
        bad = {"goals": [{"score": "1:0", "url": "javascript:alert(1)"}, {"score": "1:1", "url": "https://evil.example/x"}]}
        self.assertEqual(replay.by_score(bad), {})
        self.assertEqual(replay.by_score({"video": "javascript:alert(1)", "goals": [{"score": "1:0", "t": 5}]}), {})

    def test_old_entry_gets_new_link(self):
        # запись, сохранённая до 05.10: ссылки vk.com теряли время — сборка собирает их заново из ролика и секунды
        old = {"video": VIDEO, "goals": [{"score": "1:0", "t": 1790, "url": f"{VIDEO}?t=29m50s"}]}
        self.assertEqual(replay.by_score(old), {"1:0": f"{PLAY}&t=1790"})
        # ровно те ссылки, что открылись у админа с нужного места: 1:08:00 «Калуги» и 42:43 «Ростова»
        self.assertEqual(replay.at_link("https://vk.com/video-241266819_456239067", 4080),
                         "https://vkvideo.ru/video_ext.php?oid=-241266819&id=456239067&t=4080")
        self.assertEqual(replay.at_link("https://vk.com/video-60074608_456240744", 2563),
                         "https://vkvideo.ru/video_ext.php?oid=-60074608&id=456240744&t=2563")


class Build(unittest.TestCase):
    def test_replay_lands_on_protocol_goal(self):
        games = [{"id": "g1", "date": "2026-10-03", "home": "tverichi", "away": "metallurg",
                  "goals": [{"period": "1", "time": "05:12", "score": "1:0"}, {"period": "1", "time": "11:40", "score": "1:1"},
                            {"period": "РБ", "time": "65:00", "score": "3:2"}]},
                 {"id": "g2", "date": "2026-10-03", "home": "krasnodar", "away": "rostov", "goals": [{"score": "1:0"}]}]
        replays = {GAME["key"]: replay.entry(GAME, VIDEO, {"1:0": 1800}, msk("2026-10-03T21:00:00"))}
        self.assertEqual(build_data.apply_replays(games, replays), 2)
        self.assertEqual(games[0]["goals"][0]["replay"], f"{PLAY}&t=1790")
        self.assertIn("replay", games[0]["goals"][1])
        self.assertNotIn("replay", games[0]["goals"][2])   # буллиты не размечаем
        self.assertNotIn("replay", games[1]["goals"][0])   # у другого матча повторов нет
        self.assertNotIn("replays", games[0])

    def test_replays_by_score_before_protocol(self):
        # протокола ещё нет — повторы матча по счёту: мини-апп ставит их в ленту матча (ADR-028, раздел 4)
        games = [{"id": "g1", "date": "2026-10-03", "home": "tverichi", "away": "metallurg"},
                 {"id": "g2", "date": "2026-10-03", "home": "krasnodar", "away": "rostov"}]
        replays = {GAME["key"]: replay.entry(GAME, VIDEO, {"1:0": 1800, "1:1": 2900}, msk("2026-10-03T21:00:00"))}
        self.assertEqual(build_data.apply_replays(games, replays), 3)
        self.assertEqual(games[0]["replays"]["1:0"], f"{PLAY}&t=1790")
        self.assertEqual(set(games[0]["replays"]), {"1:0", "1:1", "2:1"})   # 2:1 — расчётный от опоры периода
        self.assertNotIn("replays", games[1])

    def test_load_replays(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "replays.json"
            self.assertEqual(build_data.load_replays(p), {})
            p.write_text(json.dumps({"games": {"k": {"goals": []}}}), encoding="utf-8")
            self.assertEqual(build_data.load_replays(p), {"k": {"goals": []}})
            p.write_text("[1, 2]", encoding="utf-8")
            self.assertEqual(build_data.load_replays(p), {})


class Bot(unittest.TestCase):
    """Команда /replay: опора от админа, пересчёт и replays.json в каталоге службы live."""

    def setUp(self):
        import bot
        self.bot = bot
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "2026-10-03.json").write_text(json.dumps({"date": "2026-10-03", "games": [GAME]}), encoding="utf-8")
        self.patches = [mock.patch.object(bot, "LIVE_DIR", self.dir),
                        mock.patch.object(bot, "REPLAYS_FILE", self.dir / "replays.json"),
                        mock.patch.object(bot, "STATE_DB", self.dir / "state.db")]
        for p in self.patches:
            p.start()
        self.now = msk("2026-10-04T12:00:00")

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def saved(self) -> dict:
        return json.loads((self.dir / "replays.json").read_text(encoding="utf-8"))["games"][GAME["key"]]

    def test_list_shows_recent_matches(self):
        text, kb = self.bot.replay_list(self.now)
        self.assertEqual([b.callback_data for row in kb.inline_keyboard for b in row], ["rp:m:2026-10-03:0"])
        text, kb = self.bot.replay_list(self.now + timedelta(days=5))
        self.assertIsNone(kb)

    def test_link_then_time(self):
        err, e = self.bot.replay_save("2026-10-03", 0, "1:0", f"держи {VIDEO}?t=30m", self.now)
        self.assertEqual(err, "")
        self.assertEqual(self.saved()["anchors"], {"1:0": 1800})
        # ролик уже известен — второй гол можно отметить просто временем записи
        err, e = self.bot.replay_save("2026-10-03", 0, "2:1", "1:20:00", self.now)
        self.assertEqual(err, "")
        self.assertEqual(self.saved()["anchors"], {"1:0": 1800, "2:1": 4800})
        text = self.bot.replay_text("2026-10-03", GAME, e)
        self.assertIn("✅", text)
        self.assertIn("≈", text)
        self.assertIn(html.escape(f"{PLAY}&t=4790"), text)   # в разметке Telegram & — это &amp;

    def test_new_video_resets_anchors(self):
        self.bot.replay_save("2026-10-03", 0, "1:0", f"{VIDEO}?t=30m", self.now)
        self.bot.replay_save("2026-10-03", 0, "1:1", "https://vk.com/video-1_2?t=10m", self.now)
        self.assertEqual(self.saved()["anchors"], {"1:1": 600})
        self.assertEqual(self.saved()["video"], "https://vk.com/video-1_2")

    def test_errors_explained(self):
        self.assertIn("время этого гола", self.bot.replay_save("2026-10-03", 0, "1:0", VIDEO, self.now)[0])
        self.assertIn("Ролика", self.bot.replay_save("2026-10-03", 0, "1:0", "14:32", self.now)[0])
        self.assertIn("Ролика", self.bot.replay_save("2026-10-03", 0, "", "привет", self.now)[0])
        self.assertIn("голов, а времён", self.bot.replay_save("2026-10-03", 0, "", f"{VIDEO}\n10:00\n20:00", self.now)[0])
        self.assertIn("не по порядку",
                      self.bot.replay_save("2026-10-03", 0, "", f"{VIDEO}\n30:00 20:00 40:00 50:00", self.now)[0])
        self.assertIn("пропал", self.bot.replay_save("2026-10-03", 5, "1:0", "14:32", self.now)[0])
        self.assertIn("пропал", self.bot.replay_save("2026-10-03", 0, "1:0", "14:32", self.now, "2026-10-03|a|b")[0])
        self.assertFalse((self.dir / "replays.json").exists())

    def test_whole_match_at_once(self):
        # как прислал админ 04.10: ссылка на запись эфира и времена всех голов по порядку
        err, e = self.bot.replay_save("2026-10-03", 0, "", "https://vkvideo.ru/live-1_2\n25:20\n57:04\n59:37\n1:20:40",
                                      self.now)
        self.assertEqual(err, "")
        self.assertEqual(self.saved()["anchors"], {"1:0": 1520, "1:1": 3424, "2:1": 3577, "2:2": 4840})
        self.assertTrue(all(g["exact"] for g in e["goals"]))
        self.assertEqual(e["goals"][0]["url"], "https://vkvideo.ru/video_ext.php?oid=-1&id=2&t=1510")

    def test_protocol_from_league(self):
        league = {"games": [{"date": "2026-10-03", "home": "tverichi", "away": "metallurg",
                             "goals": [{"score": "1:0", "team": "home", "period": "1", "author": "Иванов"},
                                       {"score": "2:1", "team": "home", "period": "РБ"}]}]}
        self.assertEqual(self.bot.protocol_of(league, GAME),
                         [{"score": "1:0", "team": "home", "period": "1", "author": "Иванов", "time": None}])
        self.assertIsNone(self.bot.protocol_of({"games": [{"date": "2026-10-04", "home": "a", "away": "b"}]}, GAME))

    def test_drop(self):
        self.bot.replay_save("2026-10-03", 0, "1:0", f"{VIDEO}?t=30m", self.now)
        self.bot.replay_drop("2026-10-03", 0, self.now)
        self.assertEqual(json.loads((self.dir / "replays.json").read_text(encoding="utf-8"))["games"], {})


if __name__ == "__main__":
    unittest.main()


class ProbeVk(unittest.TestCase):
    """tools/probe_vk.py: метки времени со страницы VK и начало записи по опорам админа."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import probe_vk
        self.p = probe_vk

    def test_stamps_and_durations(self):
        page = '{"date":1791040800,"duration":9000,"views":1234567890} var added = 1791040500; "len": "45"'
        self.assertEqual(self.p.stamps(page), {"date": {1791040800}, "added": {1791040500}})
        self.assertEqual(self.p.durations(page), {9000})   # 45 секунд — не матч

    def test_true_start_from_first_anchor(self):
        entry = {"anchors": {"1:0": 1800, "2:1": 4800}}
        self.assertEqual(self.p.true_start(entry, GAME), msk("2026-10-03T16:51:30"))   # 17:21:30 минус 30 минут
        self.assertIsNone(self.p.true_start({"anchors": {"2:2": 7000}}, GAME))       # опора без времени по часам

    def test_verdict_needs_every_game_within_30s(self):
        g = ["2026-10-04|rostov|krasnodar", "2026-10-04|tverichi|metallurg", "2026-10-05|kaluga|dynamo-576"]
        lines = self.p.verdict({"page date": {g[0]: 5, g[1]: -12, g[2]: 29},
                                "embed added": {g[0]: 5, g[1]: 400, g[2]: 3},
                                "video.get date − duration": {g[0]: 1, g[1]: 2}}, g)
        got = {x.split()[1] + " " + x.split()[2].rstrip(":"): x.split()[0] for x in lines}
        self.assertEqual(got["page date"], "ГОДИТСЯ")
        self.assertEqual(got["embed added"], "нет")        # на одном матче мимо на 400 с
        self.assertEqual(got["video.get date"], "нет")     # нет на третьем матче
        self.assertEqual(self.p.verdict({"page date": {g[0]: 0, g[1]: 0}}, g[:2])[0].split()[0], "нет")   # матчей мало


class RealMarkup(unittest.TestCase):
    """Разметка админа 04.10.2026 против времени по часам службы live (снимок сервера в 16:32).
    Опора — первый гол периода; расчётный не должен начинаться после гола."""

    def check(self, video: list[str], wall: list[str], periods: list[str]) -> list[int]:
        game = {"events": [goal(f"0:{k + 1}", "away", f"2026-10-04T{w}", period=p)
                           for k, (w, p) in enumerate(zip(wall, periods))]}
        truth = [replay.parse_clock(v) for v in video]
        firsts = {}
        for k, p in enumerate(periods):
            firsts.setdefault(p, k)
        anchors = {f"0:{k + 1}": truth[k] for k in firsts.values()}
        got = {g["score"]: g["t"] for g in replay.place(replay.goals_of(game), anchors)}
        return [truth[k] - got[f"0:{k + 1}"] for k in range(len(video)) if f"0:{k + 1}" in got]

    def test_rostov_krasnodar(self):
        before = self.check(["42:53", "49:28", "1:25:25", "2:03:26"], ["13:12:08", "13:19:14", "13:54:47", "14:31:52"],
                            ["1", "1", "2", "3"])
        self.assertTrue(all(0 < b <= 90 for b in before), before)   # за 29 с до гола у второго, у опор — за 10 с

    def test_tverichi_metallurg(self):
        # первый гол сайт отметил на 5 минут раньше по записи, чем остальные: из 1-го периода во 2-й не считаем
        before = self.check(["25:20", "57:04", "59:37", "1:20:40"], ["15:15:34", "15:52:33", "15:57:06", "16:16:20"],
                            ["1", "2", "2", "2"])
        self.assertEqual(len(before), 4)
        self.assertLess(before[2], 0)    # 0:3 сайт отметил на 2 минуты позже — расчётный опоздает, нужна ручная поправка
        self.assertTrue(0 < before[3] <= 90, before)


LEAGUE = {"games": [{"date": "2026-10-03", "home": "tverichi", "away": "metallurg",
                     "watch": [{"title": "Трансляция лиги · VK Видео", "url": VIDEO, "src": "rhl.fhr.ru"},
                               {"title": "Пост клуба", "url": "https://vk.com/video-1_2", "src": "t.me/hktverichi"}]}]}


class Nag(unittest.TestCase):
    """ADR-028: запись лиги подставляется в /replay сама, раз в день — напоминание о неразмеченных матчах."""

    def setUp(self):
        import admin
        import bot
        self.bot = bot
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "2026-10-03.json").write_text(json.dumps({"date": "2026-10-03", "games": [GAME]}), encoding="utf-8")
        self.now = msk("2026-10-04T21:30:00")
        self.track = admin.Tracker("bot", path=self.dir / "bot.json", clock=lambda: self.now)
        for name, value in (("LIVE_DIR", self.dir), ("REPLAYS_FILE", self.dir / "replays.json"),
                            ("STATE_DB", self.dir / "state.db"),
                            ("ADMIN_IDS", frozenset({1001, 1002})), ("TRACK", self.track)):
            p = mock.patch.object(bot, name, value)
            p.start()
            self.addCleanup(p.stop)

    def run_step(self, now=None, league=LEAGUE):
        say = mock.AsyncMock()
        with mock.patch.object(self.bot, "say", say), mock.patch.object(self.bot.asyncio, "sleep", mock.AsyncMock()), \
                mock.patch.object(self.bot, "published_league", mock.AsyncMock(return_value=league)):
            import asyncio
            sent = asyncio.run(self.bot.replay_nag_step(mock.Mock(), now or self.now))
        return sent, say

    def test_league_video_only_from_league(self):
        self.assertEqual(self.bot.league_video(LEAGUE, GAME), VIDEO)
        club = {"games": [{**LEAGUE["games"][0], "watch": LEAGUE["games"][0]["watch"][1:]}]}
        self.assertIsNone(self.bot.league_video(club, GAME))           # ссылка клуба — не запись лиги
        tab = {"games": [{**LEAGUE["games"][0], "watch": [{"url": "https://rhl.fhr.ru/matchcenter/1/2/video/",
                                                           "src": "rhl.fhr.ru"}]}]}
        self.assertIsNone(self.bot.league_video(tab, GAME))            # вкладка без ролика
        self.assertIsNone(self.bot.league_video(None, GAME))

    def test_times_only_with_league_video(self):
        err, e = self.bot.replay_save("2026-10-03", 0, "", "25:20\n57:04\n59:37\n1:20:40", self.now,
                                      league_vid=VIDEO)
        self.assertEqual(err, "")
        self.assertEqual(e["video"], VIDEO)
        # своя ссылка админа главнее записи лиги
        err, e = self.bot.replay_save("2026-10-03", 0, "1:0", "https://vk.com/video-1_2?t=10m", self.now, league_vid=VIDEO)
        self.assertEqual(e["video"], "https://vk.com/video-1_2")
        text = self.bot.replay_text("2026-10-03", GAME, None, video=VIDEO)
        self.assertIn("Запись лиги", text)
        self.assertIn("Пришли времена всех", text)

    def test_once_a_day_after_nine(self):
        self.assertEqual(self.run_step(self.now.replace(hour=20))[0], 0)    # рано
        self.assertEqual(self.track.today()["replays_todo"], 1)            # а на пульте уже видно
        sent, say = self.run_step()
        self.assertEqual(sent, 2)
        text, kb = say.call_args.args[2]()
        self.assertIn("Без превью, табло не разобрало: Тверичи-СШОР — Металлург", text)
        self.assertEqual(kb.inline_keyboard[0][0].callback_data, "rp:m:2026-10-03:0")
        self.assertEqual(self.run_step(self.now.replace(minute=45))[0], 0)  # второй раз за день — нет
        self.assertEqual(json.loads((self.dir / "bot.json").read_text())["days"]["2026-10-04"]["replay_nag"], 2)

    def test_restart_does_not_repeat(self):
        self.run_step()
        import admin
        self.track = admin.Tracker("bot", path=self.dir / "bot.json", clock=lambda: self.now)   # новый процесс бота
        with mock.patch.object(self.bot, "TRACK", self.track):
            self.assertEqual(self.run_step()[0], 0)

    def test_silent_when_nothing_to_do(self):
        self.assertEqual(self.run_step(self.now.replace(hour=23, minute=10))[0], 0)   # ночь
        self.assertEqual(self.run_step(league={"games": []})[0], 0)                  # записи лиги нет
        self.bot.replay_save("2026-10-03", 0, "1:0", f"{VIDEO}?t=30m", self.now)
        self.assertEqual(self.run_step()[0], 0)                                      # уже размечен
        self.assertEqual(self.track.today()["replays_todo"], 0)

    def test_unfinished_match_waits(self):
        live = {**GAME, "status": "live"}
        (self.dir / "2026-10-03.json").write_text(json.dumps({"date": "2026-10-03", "games": [live]}), encoding="utf-8")
        self.assertEqual(self.run_step()[0], 0)

    def test_previews_waiting_instead_of_not_marked(self):
        """ADR-030, раздел 7: табло разобрало матч — напоминание считает голы, которые ждут ответа на превью."""
        ask = {"from": 1500, "len": 125, "file": "p.mp4", "cand": [47]}
        clips_ = {"games": {GAME["key"]: {"video": VIDEO, "status": "ok", "goals": {
            "1:0": {"change": 1620, "t": None, "ask": ask}, "1:1": {"change": 3000, "t": 2990, "src": "clock"},
            "2:1": {"change": 4000, "t": None, "ask": ask}, "2:2": {"change": 5000, "t": 4990, "src": "board"}}}}}
        (self.dir / "clips.json").write_text(json.dumps(clips_), encoding="utf-8")
        sent, say = self.run_step()
        self.assertEqual(sent, 2)
        text, kb = say.call_args.args[2]()
        self.assertTrue(text.startswith("🎬 Ждут превью: 2 гола в 1 матче — Тверичи-СШОР — Металлург."), text)
        self.assertNotIn("Без превью", text)
        self.assertEqual(kb.inline_keyboard[0][0].callback_data, "rp:m:2026-10-03:0")   # матч — в /replay
        self.assertEqual(self.track.today()["previews_wait"], 2)
        self.bot.replay_save("2026-10-03", 0, "1:0", f"{VIDEO}?t=27m", self.now)      # админ ответил на одно
        self.assertEqual(self.bot.previews_waiting(clips_, self.bot.load_replays()["games"]), {GAME["key"]: ["2:1"]})

    def test_unmarked_boards_line(self):
        """Дополнение 06.10: клубы без разметки табло — строкой, даже когда превью ждать нечего."""
        clips_ = {"games": {}, "boards": {"polet": {"matches": 2, "key": "2026-10-03|polet|sokol",
                                                    "grid": "probe/grids/polet.png"}}}
        (self.dir / "clips.json").write_text(json.dumps(clips_), encoding="utf-8")
        sent, say = self.run_step(league={"games": []})
        self.assertEqual(sent, 2)
        text, kb = say.call_args.args[2]()
        self.assertTrue(text.startswith("🖼 Табло не размечено: Полёт (2)"), text)
        self.assertIsNone(kb)                                   # матчей для /replay нет — без пустой клавиатуры

    def test_deleted_recording_in_replay_and_report(self):
        """Этап 0.3: запись удалили из VK — в /replay сказано, что повторов по ней не будет, и это видно в разборе."""
        clips_ = {"games": {GAME["key"]: {"video": VIDEO, "status": "gone", "goals": {},
                                          "error": "VkError: видео удалено"}},
                  "coverage": {GAME["key"]: {"goals": 4, "replays": 0, "why": "gone"}}}
        (self.dir / "clips.json").write_text(json.dumps(clips_), encoding="utf-8")
        text = self.bot.replay_text("2026-10-03", GAME, None, video=VIDEO)
        self.assertIn("Этой записи больше нет в VK", text)
        self.assertIn("запись удалили из VK — 1", self.bot.coverage_text(clips_["coverage"]))
        todo, kb = self.bot.coverage_todo(clips_["coverage"])
        self.assertIn("записи больше нет в VK", todo)
        self.assertEqual(kb.inline_keyboard[0][0].callback_data, "rp:m:2026-10-03:0")

    def test_coverage_report(self):
        """ADR-031: каждый вечер — сколько матчей с повтором у всех голов и почему не у остальных, с ошибками."""
        cover = {"2026-10-03|tverichi|metallurg": {"goals": 4, "replays": 4, "why": "ok"},
                 "2026-10-04|kaluga|dinamo-576": {"goals": 6, "replays": 3, "why": "not_found",
                                                  "missing": ["1:0", "2:0", "3:0"],
                                                  "rejected": {"1:0": "до первого гола в клетке не «0»"}},
                 "2026-10-04|polet|sokol": {"goals": 2, "replays": 0, "why": "no_board"},
                 "2026-10-05|belgorod|tambov": {"goals": 2, "replays": 0, "why": "error", "error": "VkError: HTTP 403"}}
        (self.dir / "clips.json").write_text(json.dumps({"games": {}, "coverage": cover}), encoding="utf-8")
        sent, say = self.run_step(league={"games": []})
        self.assertEqual(sent, 2)
        text, kb = say.call_args.args[2]()
        self.assertTrue(text.startswith("📊 Повторы с 03.10: у всех голов — 1 из 4 матчей, голов с повтором — 7 из 14."),
                        text)
        self.assertIn("• табло не нашло голы — 1: Калужские Ракеты — Динамо-576 04.10, без повтора 3 из 6 "
                      "(1:0: до первого гола в клетке не «0»)", text)
        self.assertIn("VkError: HTTP 403", text)
        self.assertIn("табло клуба не размечено — 1: Полёт — Сокол 04.10", text)
        self.assertEqual(self.bot.coverage_text(cover, full=False).count("\n"), 0)

    def test_many_matches_link_to_list(self):
        todo = [("2026-10-03", k, GAME) for k in range(10)]
        text, kb = self.bot.replay_nag(todo)
        self.assertEqual(len(kb.inline_keyboard), self.bot.REPLAY_NAG_MAX + 1)
        self.assertEqual(kb.inline_keyboard[-1][0].callback_data, "rp:list")


class Previews(unittest.TestCase):
    """ADR-030, шаг 3: превью гола от службы clips — админам и помощникам, ответ — опора в replays.json."""

    def setUp(self):
        import admin
        import bot
        self.bot = bot
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "2026-10-03.json").write_text(json.dumps({"date": "2026-10-03", "games": [GAME]}), encoding="utf-8")
        (self.dir / "p.mp4").write_bytes(b"video")
        self.ask = {"from": 1500, "len": 125, "file": "p.mp4", "cand": [47, 72]}
        self.clips = {"games": {GAME["key"]: {"video": VIDEO, "status": "ok", "goals": {
            "1:0": {"team": "home", "change": 1620, "t": None, "src": None, "ask": self.ask},
            "1:1": {"team": "away", "change": 3000, "t": 2990, "src": "clock"}}}}}
        (self.dir / "clips.json").write_text(json.dumps(self.clips), encoding="utf-8")
        self.now = msk("2026-10-04T12:00:00")
        self.track = admin.Tracker("bot", path=self.dir / "bot.json", clock=lambda: self.now)
        for name, value in (("LIVE_DIR", self.dir), ("REPLAYS_FILE", self.dir / "replays.json"),
                            ("STATE_DB", self.dir / "state.db"),
                            ("PREVIEWS_FILE", self.dir / "previews.json"), ("BASE", self.dir),
                            ("ADMIN_IDS", frozenset({1001})), ("PREVIEW_IDS", frozenset({761})), ("TRACK", self.track)):
            p = mock.patch.object(bot, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_todo_skips_timed_marked_and_sent(self):
        todo = self.bot.preview_todo(self.clips, {}, {})
        self.assertEqual([(k, s) for k, s, _, _ in todo], [(GAME["key"], "1:0")])
        self.assertEqual(self.bot.preview_todo(self.clips, {GAME["key"]: {"anchors": {"1:0": 1600}}}, {}), [])
        self.assertEqual(self.bot.preview_todo(self.clips, {}, {f"{GAME['key']}|1:0": {"v": 2}}), [])
        self.assertEqual(self.bot.preview_todo(self.clips, {}, {f"{GAME['key']}|1:0": {"done": 47}}), [])
        old = self.bot.preview_todo(self.clips, {}, {f"{GAME['key']}|1:0": {"msgs": {"761": 3}}})   # до 05.10: «0:01»
        self.assertEqual([s for _, s, _, _ in old], ["1:0"])

    def test_caption_buttons(self):
        text, kb = self.bot.preview_caption(GAME["key"], "1:0", self.ask)
        self.assertIn("1:0", text)
        labels = [b.text for row in kb.inline_keyboard for b in row]
        self.assertEqual(labels, ["Гол на 0:47", "Гол на 1:12", "Другое время", "⚠️ Гола тут нет — табло сбилось"])
        tok = self.bot.preview_token(GAME["key"], "1:0")
        self.assertEqual(kb.inline_keyboard[0][0].callback_data, f"pv:{tok}:0")
        self.assertEqual(self.bot.preview_find(tok)[1], "1:0")

    def test_step_sends_once_to_admins_and_helpers(self):
        import asyncio
        bot = mock.Mock()
        msg = mock.Mock(message_id=5, video=mock.Mock(file_id="F"))
        bot.send_video = mock.AsyncMock(return_value=msg)
        with mock.patch.object(self.bot.asyncio, "sleep", mock.AsyncMock()):
            self.assertEqual(asyncio.run(self.bot.preview_step(bot, self.now)), 1)
            self.assertEqual(asyncio.run(self.bot.preview_step(bot, self.now)), 0)   # второй раз не шлём
            self.assertEqual(asyncio.run(self.bot.preview_step(bot, msk("2026-10-04T23:30:00"))), 0)
        self.assertEqual(sorted(c.args[0] for c in bot.send_video.call_args_list), [761, 1001])
        self.assertEqual(bot.send_video.call_args_list[1].args[1], "F")                # файл грузим один раз
        sent = json.loads((self.dir / "previews.json").read_text(encoding="utf-8"))
        self.assertEqual(sent[f"{GAME['key']}|1:0"]["msgs"], {"761": 5, "1001": 5})
        self.assertEqual(sent[f"{GAME['key']}|1:0"]["from"], 1500)                     # окно, на которое отвечают
        self.assertEqual(sent[f"{GAME['key']}|1:0"]["video"], VIDEO)
        kw = bot.send_video.call_args_list[0].kwargs
        self.assertEqual((kw["duration"], kw["width"], kw["height"]), (125, 640, 360))   # без них в чате «0:01»

    def test_old_preview_replaced(self):
        import asyncio
        (self.dir / "previews.json").write_text(json.dumps({f"{GAME['key']}|1:0": {
            "at": "2026-10-04T11:00:00+03:00", "msgs": {"761": 3}}}), encoding="utf-8")
        self.ask.update(w=480, h=360, dur=124)
        bot = mock.Mock()
        bot.send_video = mock.AsyncMock(return_value=mock.Mock(message_id=9, video=mock.Mock(file_id="F")))
        bot.delete_message = mock.AsyncMock()
        with mock.patch.object(self.bot.asyncio, "sleep", mock.AsyncMock()), \
                mock.patch.object(self.bot, "read_live", return_value=self.clips):
            self.assertEqual(asyncio.run(self.bot.preview_step(bot, self.now)), 1)
        bot.delete_message.assert_awaited_once_with(761, 3)
        kw = bot.send_video.call_args_list[0].kwargs
        self.assertEqual((kw["duration"], kw["width"], kw["height"]), (124, 480, 360))
        sent = json.loads((self.dir / "previews.json").read_text(encoding="utf-8"))
        self.assertEqual(sent[f"{GAME['key']}|1:0"]["v"], 2)

    def stale_step(self, rec: dict, clips: dict, delete=None, edit=None):
        import asyncio
        (self.dir / "previews.json").write_text(json.dumps({f"{GAME['key']}|1:0": {
            "at": "2026-10-04T09:00:00+03:00", "v": 2, **rec}}), encoding="utf-8")
        (self.dir / "clips.json").write_text(json.dumps(clips), encoding="utf-8")
        bot = mock.Mock()
        bot.delete_message = mock.AsyncMock(side_effect=delete)
        bot.edit_message_caption = mock.AsyncMock(side_effect=edit)
        bot.send_video = mock.AsyncMock(return_value=mock.Mock(message_id=9, video=mock.Mock(file_id="F")))
        with mock.patch.object(self.bot.asyncio, "sleep", mock.AsyncMock()):
            asyncio.run(self.bot.preview_step(bot, self.now))
        return bot, json.loads((self.dir / "previews.json").read_text(encoding="utf-8"))

    def test_stale_previews_closed(self):
        """Дополнение 06.10, вечер: превью, на которое отвечать уже не нужно, бот убирает из чатов сам — 06.10 утром
        пришли пятиминутные превью по времени сайта лиги без гола, служба их отозвала."""
        rec = {"msgs": {"761": 3, "1001": 4}, "from": 1500}
        goals = self.clips["games"][GAME["key"]]["goals"]
        withdrawn = {"games": {GAME["key"]: {"video": VIDEO, "status": "ok", "goals": {"1:1": goals["1:1"]}}}}
        timed = json.loads(json.dumps(self.clips))
        timed["games"][GAME["key"]]["goals"]["1:0"]["t"] = 1590                      # табло нашло секунду
        for clips_ in (withdrawn, timed):
            bot, sent = self.stale_step(rec, clips_)
            self.assertEqual(sorted(c.args for c in bot.delete_message.call_args_list), [(761, 3), (1001, 4)])
            self.assertEqual(sent, {})
            bot.send_video.assert_not_called()
        self.bot.replay_save("2026-10-03", 0, "1:0", f"{VIDEO}?t=27m", self.now)    # админ ответил в /replay
        bot, sent = self.stale_step(rec, self.clips)
        self.assertEqual(sent, {})

    def test_answered_or_same_window_kept(self):
        for rec in ({"msgs": {"761": 3}, "from": 1500, "video": VIDEO}, {"msgs": {"761": 3}, "done": 1547},
                    {"msgs": {"761": 3}}):
            bot, sent = self.stale_step(rec, self.clips)
            bot.delete_message.assert_not_called()
            self.assertIn(f"{GAME['key']}|1:0", sent)

    def test_moved_window_replaced(self):
        """Переразбор дал гол другое окно: ответ по старому видео дал бы не ту секунду — старое убираем, шлём новое."""
        for rec in ({"msgs": {"761": 3}, "from": 1400}, {"msgs": {"761": 3}, "from": 1500, "video": f"{VIDEO}9"}):
            bot, sent = self.stale_step(rec, self.clips)
            bot.delete_message.assert_awaited_once_with(761, 3)
            self.assertEqual(sent[f"{GAME['key']}|1:0"]["msgs"], {"761": 9, "1001": 9})

    def test_old_message_edited_network_retried(self):
        rec = {"msgs": {"761": 3}, "from": 1500}
        clips_ = {"games": {}}
        too_old = self.bot.TelegramBadRequest(method=mock.Mock(), message="message can't be deleted")
        bot, sent = self.stale_step(rec, clips_, delete=too_old)
        self.assertIsNone(bot.edit_message_caption.call_args.kwargs["reply_markup"])   # кнопки убраны
        self.assertEqual(sent, {})
        bot, sent = self.stale_step(rec, clips_, delete=RuntimeError("сеть"))
        self.assertEqual(sent[f"{GAME['key']}|1:0"]["msgs"], {"761": 3})                 # попробуем через минуту

    def test_wrong_board_pressed(self):
        """ADR-031: «Факел Ямал — Ахмат-Гранит» 05.10 — превью 1:3, а на табло 0:2: «табло сбилось» — пометка в
        replays.json, секунды табло у гола и у следующих голов команды не берём, превью закрыто у всех."""
        import asyncio
        (self.dir / "previews.json").write_text(json.dumps({f"{GAME['key']}|1:0": {
            "at": "2026-10-04T11:00:00+03:00", "msgs": {"761": 3, "1001": 4}, "v": 2}}), encoding="utf-8")
        tok = self.bot.preview_token(GAME["key"], "1:0")
        c = mock.Mock(data=f"pv:{tok}:w", from_user=mock.Mock(id=761), message=mock.Mock(chat=mock.Mock(id=761)))
        c.answer = mock.AsyncMock()
        c.bot.edit_message_caption = mock.AsyncMock()
        with mock.patch.object(self.bot, "published_league", mock.AsyncMock(return_value=None)):
            asyncio.run(self.bot.cb_preview(c))
        saved = json.loads((self.dir / "replays.json").read_text(encoding="utf-8"))["games"][GAME["key"]]
        self.assertEqual((saved["video"], saved["wrong"]), (VIDEO, ["1:0"]))
        self.assertEqual(c.bot.edit_message_caption.await_count, 2)
        self.assertIn("табло сбилось", c.bot.edit_message_caption.call_args.kwargs["caption"])
        board = replay.with_board(saved, self.clips["games"][GAME["key"]])
        self.assertNotIn("1:0", {x["score"] for x in board["goals"]})          # повтора по табло больше нет

    def test_answer_becomes_admin_anchor(self):
        import asyncio
        bot = mock.Mock()
        bot.edit_message_caption = mock.AsyncMock()
        with mock.patch.object(self.bot, "published_league", mock.AsyncMock(return_value=None)):
            asyncio.run(self.bot.preview_answer(bot, 1001, GAME["key"], "1:0", self.ask, VIDEO, 47))
        saved = json.loads((self.dir / "replays.json").read_text(encoding="utf-8"))["games"][GAME["key"]]
        self.assertEqual((saved["video"], saved["anchors"]), (VIDEO, {"1:0": 1547}))
        self.assertEqual(self.bot.preview_todo(self.clips, {GAME["key"]: saved}, {}), [])

    def test_helper_may_press_but_not_replay(self):
        self.assertIn(761, self.bot.preview_people())
        self.assertNotIn(761, self.bot.ADMIN_IDS)

    def test_nag_skips_matches_covered_by_board(self):
        g = {**GAME, "events": [e for e in GAME["events"] if e.get("score") in ("1:0", "1:1")]}
        found = self.clips["games"][GAME["key"]]
        self.assertTrue(self.bot.board_covers(g, found))
        self.assertFalse(self.bot.board_covers(GAME, found))          # 2:1 табло не нашло, превью нет
        self.assertFalse(self.bot.board_covers(g, {**found, "status": "no_board"}))


class Grids(unittest.TestCase):
    """ADR-030, дополнение 06.10: кадр табло клуба без разметки — админам файлом, один раз на клуб."""

    def setUp(self):
        import admin
        import bot
        self.bot = bot
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "probe" / "grids").mkdir(parents=True)
        (self.dir / "probe" / "grids" / "polet.png").write_bytes(b"png")
        self.boards = {"polet": {"matches": 2, "key": "2026-10-05|polet|ermak", "grid": "probe/grids/polet.png"},
                       "ermak": {"matches": 1, "key": "2026-10-04|ermak|polet"}}   # кадра ещё нет
        (self.dir / "clips.json").write_text(json.dumps({"games": {}, "boards": self.boards}), encoding="utf-8")
        self.now = msk("2026-10-05T12:00:00")
        self.track = admin.Tracker("bot", path=self.dir / "bot.json", clock=lambda: self.now)
        for name, value in (("LIVE_DIR", self.dir), ("GRIDS_FILE", self.dir / "grids.json"), ("BASE", self.dir),
                            ("ADMIN_IDS", frozenset({1001, 1002})), ("PREVIEW_IDS", frozenset({761})),
                            ("TRACK", self.track)):
            p = mock.patch.object(bot, name, value)
            p.start()
            self.addCleanup(p.stop)

    def step(self, bot, now=None):
        import asyncio
        with mock.patch.object(self.bot.asyncio, "sleep", mock.AsyncMock()):
            return asyncio.run(self.bot.grid_step(bot, now or self.now))

    def test_once_per_club_to_admins_as_file(self):
        bot = mock.Mock()
        bot.send_document = mock.AsyncMock(return_value=mock.Mock(document=mock.Mock(file_id="G")))
        self.assertEqual(self.step(bot), 1)
        self.assertEqual(self.step(bot), 0)                                   # второй раз не шлём
        self.assertEqual(sorted(c.args[0] for c in bot.send_document.call_args_list), [1001, 1002])   # не помощнику
        self.assertEqual(bot.send_document.call_args_list[1].args[1], "G")    # файл грузим один раз
        caption = bot.send_document.call_args_list[0].kwargs["caption"]
        self.assertIn("Табло «Полёт» не размечено", caption)
        self.assertIn("2 домашних матча", caption)
        self.assertIn("<code>polet</code>", caption)
        self.assertEqual(json.loads((self.dir / "grids.json").read_text(encoding="utf-8"))["polet"]["key"],
                         "2026-10-05|polet|ermak")
        self.assertEqual(self.track.today()["grids"], 1)

    def test_quiet_at_night_and_forgets_marked(self):
        bot = mock.Mock()
        bot.send_document = mock.AsyncMock(return_value=mock.Mock(document=None))
        self.assertEqual(self.step(bot, msk("2026-10-05T23:30:00")), 0)
        self.step(bot)
        (self.dir / "clips.json").write_text(json.dumps({"games": {}, "boards": {}}), encoding="utf-8")   # разметили
        self.step(bot)
        self.assertEqual(json.loads((self.dir / "grids.json").read_text(encoding="utf-8")), {})

    def test_failed_send_retried(self):
        bot = mock.Mock()
        bot.send_document = mock.AsyncMock(side_effect=RuntimeError("сеть"))
        self.assertEqual(self.step(bot), 0)
        self.assertFalse((self.dir / "grids.json").exists())


class ClockFormat(unittest.TestCase):
    def test_fmt_clock(self):
        self.assertEqual([replay.fmt_clock(x) for x in (0, 65, 723, 3723)], ["0:00", "1:05", "12:03", "1:02:03"])


class BoardApprox(unittest.TestCase):
    """ADR-031: у гола без точной секунды, но со сменой счёта на табло или окном счёта хода — примерный повтор."""

    def test_change_and_window(self):
        board = {"video": VIDEO, "goals": {"1:0": {"t": None, "change": 1620, "team": "home"},
                                           "1:1": {"t": None, "win": [2900, 2960], "change": 3000, "team": "away"},
                                           "2:1": {"t": 4000, "src": "run", "team": "home"}}}
        got = {g["score"]: g for g in replay.with_board(None, board)["goals"]}
        self.assertEqual((got["1:0"]["t"], got["1:0"]["exact"], got["1:0"]["src"]), (1520, False, "change"))
        self.assertEqual((got["1:1"]["t"], got["1:1"]["src"]), (2890, "win"))     # окно уже смены
        self.assertEqual((got["2:1"]["t"], got["2:1"]["exact"]), (3990, True))
        self.assertEqual(set(replay.by_score({**replay.with_board(None, board)})), {"1:0", "1:1", "2:1"})

    def test_marks_switch_board_off(self):
        board = {"video": VIDEO, "goals": {"1:0": {"t": 100, "src": "clock", "team": "home"},
                                           "1:1": {"t": 300, "src": "clock", "team": "away"},
                                           "2:1": {"t": 500, "src": "clock", "team": "home"}}}
        entry = {"video": VIDEO, "anchors": {}, "goals": [], "wrong": ["1:0"]}
        self.assertEqual(replay.board_off(entry, board["goals"]), {"1:0", "2:1"})   # и следующий гол хозяев
        got = {g["score"] for g in replay.with_board(entry, board)["goals"]}
        self.assertEqual(got, {"1:1"})
        absent = {"video": VIDEO, "anchors": {}, "goals": [], "absent": ["1:1"]}
        self.assertEqual({g["score"] for g in replay.with_board(absent, board)["goals"]}, {"1:0", "2:1"})
        kept = replay.entry(GAME, VIDEO, {"1:0": 60}, msk("2026-10-04T12:00:00"), absent=["1:0", "2:1"], wrong=["1:1"])
        self.assertEqual((kept["absent"], kept["wrong"]), (["2:1"], ["1:1"]))    # своё время главнее «нет в записи»

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
                        mock.patch.object(bot, "REPLAYS_FILE", self.dir / "replays.json")]
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
        self.assertEqual(self.bot.protocol_of(league, GAME), [{"score": "1:0", "team": "home", "period": "1", "author": "Иванов"}])
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
        self.assertIn("Не размечены повторы: Тверичи-СШОР — Металлург", text)
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

    def test_many_matches_link_to_list(self):
        todo = [("2026-10-03", k, GAME) for k in range(10)]
        text, kb = self.bot.replay_nag(todo)
        self.assertEqual(len(kb.inline_keyboard), self.bot.REPLAY_NAG_MAX + 1)
        self.assertEqual(kb.inline_keyboard[-1][0].callback_data, "rp:list")

"""Повторы голов (ADR-027): разбор ссылок VK, секунда записи для каждого гола, ссылки в league.json и /replay."""
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
        self.assertEqual(replay.parse_link(f"{VIDEO}?t=14m32s"), (VIDEO, 872))
        self.assertEqual(replay.parse_link("https://vkvideo.ru/video-187307324_456239889?t=1h2m3s"), (VIDEO, 3723))
        self.assertEqual(replay.parse_link("вот: https://vk.com/video?z=video-187307324_456239889%2Fclub1&t=95"),
                         (VIDEO, 95))
        self.assertEqual(replay.parse_link("https://vk.ru/video_ext.php?oid=-187307324&id=456239889&t=40s"), (VIDEO, 40))
        self.assertEqual(replay.parse_link(VIDEO), (VIDEO, None))               # без времени — ролик есть, секунды нет

    def test_not_vk(self):
        for text in ("14:32", "https://youtu.be/abc?t=30", "https://vk.com/club123", "https://evil.example/video-1_2?t=5"):
            self.assertIsNone(replay.parse_link(text), text)

    def test_time_formats(self):
        self.assertEqual([replay.parse_t(x) for x in ("14m32s", "1h2m3s", "872", "872s", "45s", "", "abc", "99h")],
                         [872, 3723, 872, 872, 45, None, None, None])
        self.assertEqual([replay.parse_clock(x) for x in ("14:32", "1:02:03", "0:45", "14:75", "1:75:00", "abc")],
                         [872, 3723, 45, None, None, None])
        self.assertEqual([replay.fmt_t(x) for x in (872, 3723, 45, 3600, -5)], ["14m32s", "1h2m3s", "45s", "1h0m0s", "0s"])
        self.assertEqual(replay.at_link(VIDEO, 872), f"{VIDEO}?t=14m32s")


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
        self.assertEqual(e["goals"][0]["url"], f"{VIDEO}?t=29m50s")
        links = replay.by_score(e)
        self.assertEqual(set(links), {"1:0", "1:1", "2:1"})
        bad = {"goals": [{"score": "1:0", "url": "javascript:alert(1)"}, {"score": "1:1", "url": "https://evil.example/x"}]}
        self.assertEqual(replay.by_score(bad), {})


class Build(unittest.TestCase):
    def test_replay_lands_on_protocol_goal(self):
        games = [{"id": "g1", "date": "2026-10-03", "home": "tverichi", "away": "metallurg",
                  "goals": [{"period": "1", "time": "05:12", "score": "1:0"}, {"period": "1", "time": "11:40", "score": "1:1"},
                            {"period": "РБ", "time": "65:00", "score": "3:2"}]},
                 {"id": "g2", "date": "2026-10-03", "home": "krasnodar", "away": "rostov", "goals": [{"score": "1:0"}]}]
        replays = {GAME["key"]: replay.entry(GAME, VIDEO, {"1:0": 1800}, msk("2026-10-03T21:00:00"))}
        self.assertEqual(build_data.apply_replays(games, replays), 2)
        self.assertEqual(games[0]["goals"][0]["replay"], f"{VIDEO}?t=29m50s")
        self.assertIn("replay", games[0]["goals"][1])
        self.assertNotIn("replay", games[0]["goals"][2])   # буллиты не размечаем
        self.assertNotIn("replay", games[1]["goals"][0])   # у другого матча повторов нет

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
        self.assertIn(f"{VIDEO}?t=1h19m50s", text)

    def test_new_video_resets_anchors(self):
        self.bot.replay_save("2026-10-03", 0, "1:0", f"{VIDEO}?t=30m", self.now)
        self.bot.replay_save("2026-10-03", 0, "1:1", "https://vk.com/video-1_2?t=10m", self.now)
        self.assertEqual(self.saved()["anchors"], {"1:1": 600})
        self.assertEqual(self.saved()["video"], "https://vk.com/video-1_2")

    def test_errors_explained(self):
        self.assertIn("нет времени", self.bot.replay_save("2026-10-03", 0, "1:0", VIDEO, self.now)[0])
        self.assertIn("Ролика", self.bot.replay_save("2026-10-03", 0, "1:0", "14:32", self.now)[0])
        self.assertIn("Не понял", self.bot.replay_save("2026-10-03", 0, "1:0", "привет", self.now)[0])
        self.assertIn("пропал", self.bot.replay_save("2026-10-03", 5, "1:0", "14:32", self.now)[0])
        self.assertIn("пропал", self.bot.replay_save("2026-10-03", 0, "1:0", "14:32", self.now, "2026-10-03|a|b")[0])
        self.assertFalse((self.dir / "replays.json").exists())

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

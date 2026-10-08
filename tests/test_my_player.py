"""Шаг 8 ADR-030 в боте: «Голы матча» в итоге матча и «Мой игрок» — гол отмеченного игрока после матча."""
import asyncio
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
import myplayer  # noqa: E402
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError  # noqa: E402

TZ = ZoneInfo("Europe/Moscow")
PK, OTHER = "a1b2c3d4e5", "0f0f0f0f0f"
CLIP = {"mp4": "https://s3.twcstorage.ru/rhl-clips/clips/2026-10-04/tverichi_metallurg/0-2-2963.mp4",
        "poster": "https://s3.twcstorage.ru/rhl-clips/clips/2026-10-04/tverichi_metallurg/0-2-2963.jpg", "dur": 30.0}


def game(clip=True, **over) -> dict:
    goals = [
        {"period": "1", "time": "12:40", "team": "away", "score": "0:1", "author": "Петров Пётр", "pk": OTHER,
         "assists": ["Иванов Иван"], "apk": [PK]},                                    # передача — не его гол
        {"period": "2", "time": "23:15", "team": "away", "score": "0:2", "author": "Иванов Иван", "pk": PK,
         "assists": ["Петров Пётр", "Игрок скрыт"], "apk": [OTHER, None],
         **({"clip": CLIP} if clip else {"replay": "https://vk.com/video-100_200?t=49m13s"})},
        {"period": "РБ", "time": "65:00", "team": "away", "score": "0:3", "author": "Иванов Иван", "pk": PK,
         "assists": []},                                                               # буллит — не гол каталога
    ]
    return {"id": "n41", "date": "2026-10-04", "time": "15:00", "home": "tverichi", "away": "metallurg",
            "score": {"home": 0, "away": 3, "decision": "Б"}, "goals": goals, **over}


def msk(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


class GoalsButton(unittest.TestCase):
    def test_result_with_protocol_has_goals_reel(self):
        with mock.patch.object(bot, "WEBAPP_URL", "https://x.github.io/app/"):
            kb = bot.recap_kb("n4").inline_keyboard
            live = bot.recap_kb("n4", recap=False).inline_keyboard
        self.assertEqual(len(kb), 2)
        self.assertIn("Голы матча", kb[1][0].text)
        self.assertEqual(kb[1][0].web_app.url, "https://x.github.io/app/?match=n4&view=goals")
        self.assertEqual(len(live), 1)   # протокола нет — голов и клипов тоже: одна кнопка «Матч в приложении»


class Store(unittest.TestCase):
    def setUp(self):
        self.s = myplayer.MyPlayerStore(sqlite3.connect(":memory:", isolation_level=None))

    def test_one_player_per_fan(self):
        self.s.set(1, PK, msk("2026-10-03T10:00:00"))
        self.s.set(1, PK, msk("2026-10-05T10:00:00"))   # тот же — время первой отметки
        self.assertEqual(self.s.all(), [(1, PK, "2026-10-03T10:00:00+03:00")])
        self.s.set(1, OTHER, msk("2026-10-05T10:00:00"))
        self.assertEqual(self.s.all(), [(1, OTHER, "2026-10-05T10:00:00+03:00")])
        self.assertEqual((self.s.get(1), self.s.count()), (OTHER, 1))
        self.assertTrue(self.s.forget(1))
        self.assertEqual((self.s.get(1), self.s.count()), (None, 0))
        with self.assertRaises(ValueError):
            self.s.set(2, "123", msk("2026-10-05T10:00:00"))


class Picks(unittest.TestCase):
    stars = [(1, PK, "2026-10-03T21:00:00+03:00"), (2, PK, "2026-10-05T08:00:00+03:00"), (3, OTHER, "2026-10-01")]

    def test_clip_goes_at_once_only_own_goals_since_star(self):
        got = bot.my_goals({"games": [game()]}, self.stars, msk("2026-10-04T18:00:00"))
        # 2 отметил игрока на следующий день — старый гол не шлём; передача и буллит — не голы; у 0:1 клипа нет
        self.assertEqual([(fan, x["score"]) for fan, _, x in got], [(1, "0:2")])
        got = bot.my_goals({"games": [game()]}, self.stars, msk("2026-10-04T23:00:00"))
        self.assertEqual([(fan, x["score"]) for fan, _, x in got], [(3, "0:1"), (1, "0:2")])

    def test_without_clip_waits(self):
        league = {"games": [game(clip=False)]}
        self.assertEqual(bot.my_goals(league, self.stars[:1], msk("2026-10-04T22:59:00")), [])
        self.assertEqual(len(bot.my_goals(league, self.stars[:1], msk("2026-10-04T23:00:00"))), 1)   # 15:00 + 8 ч

    def test_old_and_unplayed_skipped(self):
        self.assertEqual(bot.my_goals({"games": [game()]}, self.stars[:1], msk("2026-10-07T12:00:00")), [])
        self.assertEqual(bot.my_goals({"games": [game(score=None)]}, self.stars[:1], msk("2026-10-04T18:00:00")), [])

    def test_text_only_protocol_facts(self):
        g = game()
        text = bot.my_goal_text(g, g["goals"][1])
        self.assertTrue(text.startswith("⭐ <b>Иванов Иван</b> забил!\n<b>Тверичи-СШОР</b> — Металлург <b>0:2</b>"), text)
        self.assertIn("2-й период, 23:15", text)
        self.assertIn("Передачи: Петров Пётр\n", text)   # скрытый ассистент не назван
        self.assertNotIn("скрыт", text)
        with mock.patch.object(bot, "WEBAPP_URL", "https://x.github.io/app/"):
            kb = bot.my_goal_kb(g, g["goals"][1]).inline_keyboard
            vk = bot.my_goal_kb(game(clip=False), game(clip=False)["goals"][1]).inline_keyboard
        self.assertEqual(kb[0][0].web_app.url, "https://x.github.io/app/?match=n41")
        self.assertEqual(kb[1][0].web_app.url, f"https://x.github.io/app/?startapp=p-{PK}")
        self.assertEqual(vk[1][0].url, "https://vk.com/video-100_200?t=49m13s")   # клипа нет — повтор в VK


class Step(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.store = myplayer.MyPlayerStore(sqlite3.connect(tmp / "state.db", isolation_level=None))
        self.store.set(1001, PK, msk("2026-10-04T09:00:00"))
        self.now = msk("2026-10-04T18:00:00")
        self.reminded = {}
        for name, value in (("_my_players", self.store), ("STATE_DB", tmp / "state.db"),
                            ("REMINDED", self.reminded), ("REMINDED_FILE", tmp / "reminded.json"),
                            ("SUBS", {}), ("SUBS_FILE", tmp / "subscribers.json"), ("CLIP_FILE_IDS", {}),
                            ("TRACK", bot.admin.Tracker("bot", tmp / "bot.json", clock=lambda: self.now))):
            p = mock.patch.object(bot, name, value)
            p.start()
            self.addCleanup(p.stop)

    def run_step(self, tg, league=None):
        with mock.patch.object(bot, "published_league", mock.AsyncMock(return_value=league or {"games": [game()]})), \
                mock.patch.object(bot.asyncio, "sleep", mock.AsyncMock()):
            return asyncio.run(bot.my_player_step(tg, self.now))

    def test_clip_once_then_file_id(self):
        tg = mock.Mock()
        tg.send_video = mock.AsyncMock(return_value=mock.Mock(video=mock.Mock(file_id="F1")))
        self.assertEqual(self.run_step(tg), 1)
        args, kw = tg.send_video.call_args
        self.assertEqual(args, (1001, CLIP["mp4"]))
        self.assertEqual((kw["duration"], kw["supports_streaming"]), (30, True))
        self.assertIn("Иванов Иван", kw["caption"])
        self.assertEqual(bot.CLIP_FILE_IDS[CLIP["mp4"]], "F1")
        self.assertEqual(self.run_step(tg), 0)   # второй раз тот же гол не шлём
        self.assertIn("1001|n41|0:2", self.reminded["2026-10-04:player"]["sent"])

    def test_window_is_not_a_clip(self):
        """Ревью PR: окно повтора (ADR-037) — две минуты записи в 480p; видео отметившему уходит только точным
        клипом. С окном гол ждёт клипа как без видео совсем, а через MY_PLAYER_WAIT уходит текстом с «Повтором»."""
        tg = mock.Mock()
        tg.send_video = mock.AsyncMock()
        tg.send_message = mock.AsyncMock(return_value=mock.Mock(message_id=5))
        window = {"games": [game(clip=False)]}
        window["games"][0]["goals"][1]["clip"] = {**CLIP, "kind": "window", "dur": 130.0}
        self.assertEqual(self.run_step(tg, league=window), 0)   # три часа после начала — ждём клип
        early = {"games": [game(clip=False, time="09:00")]}     # прошло MY_PLAYER_WAIT — шлём текстом
        early["games"][0]["goals"][1]["clip"] = {**CLIP, "kind": "window", "dur": 130.0}
        self.assertEqual(self.run_step(tg, league=early), 1)
        tg.send_video.assert_not_called()
        self.assertIn("Иванов Иван", tg.send_message.call_args.args[1])

    def test_clip_refused_goes_as_text(self):
        tg = mock.Mock()
        tg.send_video = mock.AsyncMock(side_effect=TelegramBadRequest(mock.Mock(), "failed to get HTTP URL content"))
        tg.send_message = mock.AsyncMock()
        self.assertEqual(self.run_step(tg), 1)
        self.assertIn("Иванов Иван", tg.send_message.call_args.args[1])

    def test_blocked_forgets(self):
        tg = mock.Mock()
        tg.send_video = mock.AsyncMock(side_effect=TelegramForbiddenError(mock.Mock(), "bot was blocked by the user"))
        self.assertEqual(self.run_step(tg), 0)
        self.assertIsNone(self.store.get(1001))

    def test_turn_off_forgets(self):
        bot.unsubscribe(1001)   # «Выключить» в напоминаниях
        self.assertIsNone(self.store.get(1001))

    def test_quiet_at_night(self):
        self.now = msk("2026-10-04T23:30:00")
        self.assertEqual(self.run_step(mock.Mock()), 0)


if __name__ == "__main__":
    unittest.main()

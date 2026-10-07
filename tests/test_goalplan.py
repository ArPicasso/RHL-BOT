"""Что показать о голе — общее для бота и пульта (goalplan.py, ADR-036): запись лиги, протокол, листание окон."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cutjobs  # noqa: E402
import goalplan  # noqa: E402

VIDEO = "https://vk.com/video-100_200"
KEY = "2026-10-04|tverichi|metallurg"


class GoalPlan(unittest.TestCase):
    def test_league_video_and_protocol(self):
        game = {"watch": [{"src": "t.me/club", "url": "https://vk.com/video-1_2"},
                          {"src": "rhl.fhr.ru", "url": "https://vkvideo.ru/video-100_200"}],
                "goals": [{"score": "1:0", "period": "1", "time": "05:00", "author": "Иванов"},
                          {"score": "2:0", "period": "РБ"}]}
        self.assertTrue(goalplan.league_video(game).endswith("video-100_200"))   # ссылка клуба — не запись лиги
        self.assertIsNone(goalplan.league_video({"watch": [{"src": "t.me/club", "url": "https://vk.com/video-1_2"}]}))
        self.assertEqual([x["score"] for x in goalplan.protocol_of(game)], ["1:0"])   # буллиты — не голы записи
        self.assertIsNone(goalplan.protocol_of(None))

    def test_shift_only_around_unknown_place(self):
        board = {"video": VIDEO, "status": "ok", "length": 9000,
                 "goals": {"1:0": {"t": 2600, "src": "clock"}, "2:0": {"t": None, "change": 4200, "ask": {}}}}
        exact = goalplan.plan(KEY, "1:0", None, board)
        self.assertIs(goalplan.shifted(exact, 3), exact)                    # точную секунду не листаем
        approx = goalplan.plan(KEY, "2:0", None, board)
        self.assertEqual(approx["kind"], "approx")
        s, n, _ = approx["windows"][0]
        back = goalplan.shifted(approx, -2)
        self.assertEqual(back["windows"][0][:2], (s - 2 * cutjobs.SEARCH, cutjobs.SEARCH))
        self.assertEqual((back["kind"], back["cand"]), ("search", []))
        self.assertIsNone(goalplan.shifted(approx, -100))                   # до начала записи


if __name__ == "__main__":
    unittest.main()

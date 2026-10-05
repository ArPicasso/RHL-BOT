"""Служба clips (ADR-030, шаг 2): какие матчи разбирать, голы по табло в clips.json, повторы по ним."""
import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import build_data as b  # noqa: E402
import clips  # noqa: E402
import probe_scoreboard as sb  # noqa: E402
import replay  # noqa: E402

TZ = ZoneInfo("Europe/Moscow")
KEY = "2026-10-04|tverichi|metallurg"
VIDEO = "https://vk.com/video-100_200"
LEAGUE = {"games": [
    {"date": "2026-10-04", "home": "tverichi", "away": "metallurg", "score": {"home": 2, "away": 5},
     "watch": [{"src": "rhl.fhr.ru", "url": "https://vkvideo.ru/video-100_200"}]},
    {"date": "2026-10-04", "home": "rostov", "away": "krasnodar", "score": {"home": 0, "away": 4},
     "watch": [{"src": "t.me/club", "url": "https://vk.com/video-1_2"}]},                 # не запись лиги
    {"date": "2026-10-05", "home": "kaluga", "away": "dinamo-576",
     "watch": [{"src": "rhl.fhr.ru", "url": "https://vk.com/video-3_4"}]},                # ещё не сыгран
    {"date": "2026-09-28", "home": "sokol", "away": "proton", "score": {"home": 1, "away": 0},
     "watch": [{"src": "rhl.fhr.ru", "url": "https://vk.com/video-5_6"}]},                # давно
]}


class Boards(unittest.TestCase):
    def test_markup_from_file(self):
        self.assertEqual(set(sb.BOARDS), {"kaluga", "rostov", "ryazan-vdv", "tverichi"})
        self.assertEqual(sb.CLUB_LAG, {"tverichi": 6})
        self.assertEqual(sb.BOXES["tverichi"], (0.05, 0.05, 0.15, 0.18))
        self.assertEqual(len(sb.BOARDS["rostov"]["home"]), 4)
        self.assertTrue(sb.board_of(KEY, sb.BOXES["tverichi"]))
        self.assertEqual(sb.load_boards(ROOT / "нет.json"), {})


class Pending(unittest.TestCase):
    today = date(2026, 10, 5)

    def test_played_league_recordings_in_window(self):
        self.assertEqual(clips.pending(LEAGUE, {}, {}, self.today), [(KEY, "https://vk.com/video-100_200")])

    def test_admin_video_wins_and_is_taken_alone(self):
        marked = {KEY: {"video": "https://vk.com/video-7_8", "anchors": {}},
                  "2026-10-05|samara|sokol": {"video": "https://vk.com/video-9_9", "anchors": {"1:0": 60}}}
        got = dict(clips.pending(LEAGUE, marked, {}, self.today))
        self.assertEqual(got[KEY], "https://vk.com/video-7_8")
        self.assertIn("2026-10-05|samara|sokol", got)

    def test_done_once_per_video_and_retries(self):
        done = {KEY: {"video": "https://vkvideo.ru/video-100_200", "status": "ok"}}
        self.assertEqual(clips.pending(LEAGUE, {}, done, self.today), [])
        failed = {KEY: {"video": VIDEO, "status": "error", "tries": 2}}
        self.assertEqual(len(clips.pending(LEAGUE, {}, failed, self.today)), 1)
        failed[KEY]["tries"] = clips.TRIES
        self.assertEqual(clips.pending(LEAGUE, {}, failed, self.today), [])
        other = {KEY: {"video": "https://vk.com/video-1_1", "status": "ok"}}   # лига сменила ролик — заново
        self.assertEqual(len(clips.pending(LEAGUE, {}, other, self.today)), 1)


class Found(unittest.TestCase):
    def test_goals_with_and_without_second(self):
        board = [{"score": "0:2", "change": 2969.4, "t": 2963, "src": "clock"},
                 {"score": "0:1", "change": 2600.0, "t": None, "src": None},
                 {"score": "мусор", "t": 1}]
        live = {"0:2": {"team": "away", "period": "1"}}
        got = clips.found_goals(board, live)
        self.assertEqual(got["0:2"], {"team": "away", "period": "1", "change": 2969, "t": 2963, "src": "clock"})
        self.assertIsNone(got["0:1"]["t"])
        self.assertEqual(set(got), {"0:2", "0:1"})


class Pass(unittest.TestCase):
    now = datetime(2026, 10, 5, 12, 0, tzinfo=TZ)

    def run_pass(self, scan, store=None):
        store = store if store is not None else {}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "LIVE_DIR", Path(tmp)):
            clips.run_pass(store, LEAGUE, {}, self.now, scan=scan)
            on_disk = json.loads((Path(tmp) / "clips.json").read_text(encoding="utf-8"))
        return store, on_disk

    def test_writes_found_goals(self):
        scan = mock.Mock(return_value={"status": "ok", "goals": {"0:2": {"t": 2963, "src": "clock", "team": "away"}}})
        store, disk = self.run_pass(scan)
        self.assertEqual(disk["games"][KEY]["goals"]["0:2"]["t"], 2963)
        self.assertEqual((disk["games"][KEY]["status"], disk["games"][KEY]["tries"]), ("ok", 1))
        scan.assert_called_once_with(KEY, "https://vk.com/video-100_200", {})

    def test_vk_refused_is_counted_not_fatal(self):
        store, disk = self.run_pass(mock.Mock(side_effect=RuntimeError("HTTP 403")))
        self.assertEqual(disk["games"][KEY]["status"], "error")
        self.assertIn("403", disk["games"][KEY]["error"])
        store, _ = self.run_pass(mock.Mock(side_effect=RuntimeError("снова")), store)
        self.assertEqual(store["games"][KEY]["tries"], 2)

    def test_old_games_forgotten(self):
        store = {"games": {"2026-09-20|a|b": {"video": VIDEO, "status": "ok", "goals": {}}}}
        store, _ = self.run_pass(mock.Mock(return_value={"status": "ok", "goals": {}}), store)
        self.assertNotIn("2026-09-20|a|b", store["games"])

    def test_old_frames_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("2026-09-30_a_b", "2026-10-04_c_d", "x"):
                (Path(tmp) / name).mkdir()
            clips.clean_work(date(2026, 10, 5), Path(tmp))
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["2026-10-04_c_d", "x"])


class Previews(unittest.TestCase):
    """Шаг 3: превью гола без секунды — окно до смены табло и моменты, когда вставали часы."""

    def test_window_before_change(self):
        self.assertEqual(clips.preview_window(2969.6), (2849, 125))
        self.assertEqual(clips.preview_window(60), (0, 65))
        self.assertEqual(clips.preview_window(3000, length=3002), (2880, 122))

    def test_clock_stops(self):
        clock = list(range(10))
        run = lambda k: bytes([k * 50 % 256] * 10)   # noqa: E731 — часы идут: клетка меняется каждую секунду
        still = bytes([7] * 10)
        vis = [(t, run(t)) for t in range(100, 105)] + [(t, still) for t in range(105, 110)]
        vis += [(t, run(t)) for t in range(110, 113)] + [(t, still) for t in range(113, 116)]
        with mock.patch.object(clips.sb, "CLOCK_MOVED", 8):
            self.assertEqual(clips.clock_stops(vis, clock), [104, 112])
            self.assertEqual(clips.clock_stops(vis[:3] + vis[6:], clock), [112])   # разрыв в кадрах — не остановка

    def test_preview_exact_start(self):
        with mock.patch.object(clips.sb, "ffmpeg", return_value="ffmpeg"):   # на раннере GitHub ffmpeg нет
            cmd = clips.preview_cmd("http://x/s.m3u8", {"Referer": "https://vk.com"}, 2849, 125, Path("p.mp4"))
        self.assertLess(cmd.index("-ss"), cmd.index("-i"))
        self.assertIn("libx264", cmd)                       # перекодируем: нулевая секунда превью — ровно 2849
        self.assertEqual(cmd[cmd.index("-t") + 1], "125")

    def test_only_goals_without_second(self):
        goals = {"0:2": {"t": 2963, "change": 2969}, "0:1": {"t": None, "change": None}}
        with mock.patch.object(clips.sb, "stream_of") as stream:
            clips.add_previews(KEY, VIDEO, goals, None, Path("."))
        stream.assert_not_called()
        self.assertNotIn("ask", goals["0:2"])


class Replays(unittest.TestCase):
    board = {"video": VIDEO, "goals": {"0:2": {"t": 2963, "src": "clock", "team": "away"},
                                       "0:3": {"t": 3500, "src": "board", "team": "away"},
                                       "0:1": {"t": None, "src": None}}}

    def test_board_alone(self):
        e = replay.with_board(None, self.board)
        got = {g["score"]: (g["t"], g["exact"], g["src"]) for g in e["goals"]}
        self.assertEqual(got, {"0:2": (2953, True, "clock"), "0:3": (3490, True, "board")})
        self.assertEqual(replay.by_score(e)["0:2"], "https://vkvideo.ru/video_ext.php?oid=-100&id=200&t=2953")

    def test_admin_first_board_beats_guess(self):
        entry = {"video": "https://vk.com/video-100_200", "anchors": {"0:2": 2950},
                 "goals": [{"score": "0:2", "t": 2940, "exact": True}, {"score": "0:3", "t": 3300, "exact": False}]}
        got = {g["score"]: g["t"] for g in replay.with_board(entry, self.board)["goals"]}
        self.assertEqual(got, {"0:2": 2940, "0:3": 3490})

    def test_other_video_keeps_admin(self):
        entry = {"video": "https://vk.com/video-7_8", "anchors": {}, "goals": []}
        self.assertIs(replay.with_board(entry, self.board), entry)
        self.assertIsNone(replay.with_board(None, {"video": VIDEO, "goals": {}}))

    def test_build_takes_board_seconds(self):
        g = {"date": "2026-10-04", "home": "tverichi", "away": "metallurg",
             "goals": [{"period": "1", "score": "0:2", "team": "away"}, {"period": "1", "score": "0:1", "team": "away"}]}
        self.assertEqual(b.apply_replays([g], {}, {KEY: self.board}), 1)
        self.assertTrue(g["goals"][0]["replay"].endswith("&t=2953"))
        self.assertNotIn("replay", g["goals"][1])


if __name__ == "__main__":
    unittest.main()

"""tools/probe_cuts.py (ADR-029): окна клипов, откуда секунда гола, скрытые игроки, знак «Навигатор РХЛ»."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import probe_cuts as pc  # noqa: E402
import probe_scoreboard as sb  # noqa: E402

KEY = "2026-10-04|tverichi|metallurg"


class Windows(unittest.TestCase):
    def test_goal_order_and_edges(self):
        got = pc.windows({"0:2": 2968, "0:1": 2573.4, "1:0": 12, "плохо": 5, "2:1": "1:00"})
        self.assertEqual([(w["score"], w["start"], w["length"], w["file"]) for w in got],
                         [("1:0", 0, 22, "1-0.mp4"), ("0:1", 2553, 30, "0-1.mp4"), ("0:2", 2948, 30, "0-2.mp4")])

    def test_longer_window(self):
        self.assertEqual(pc.windows({"1:0": 100}, before=20, after=40)[0]["length"], 60)


class Seconds(unittest.TestCase):
    def test_admin_first_then_board_and_no_guess(self):
        board = [{"score": "0:2", "t": 3424, "src": "board"}, {"score": "0:3", "t": 3579, "src": "clock"},
                 {"score": "0:1", "t": None, "src": None}]                # табло узнало гол, но секунды нет
        sec, src = pc.goal_seconds({"0:2": 3420}, board)
        self.assertEqual(sec, {"0:2": 3420, "0:3": 3579})
        self.assertEqual(src, {"0:2": "admin", "0:3": "clock"})

    def test_board_goal_time_rule(self):
        # решение 05.10: часы главнее; смена табло — только ровная задержка клуба и табло не пропадало
        self.assertEqual(sb.goal_time(1000, True, (941, 942), 6), (941, "clock"))
        self.assertEqual(sb.goal_time(1000, True, None, 6), (994, "board"))
        self.assertEqual(sb.goal_time(1000, True, None, None), (None, None))   # Калуга: оператор опоздал на минуту
        self.assertEqual(sb.goal_time(1000, False, None, 6), (None, None))     # табло пропадало

    def test_hidden_player_not_cut(self):
        league = {"games": [{"date": "2026-10-04", "home": "tverichi", "away": "metallurg",
                             "goals": [{"score": "0:1", "author": "Иванов"}, {"score": "0:2", "author": pc.HIDDEN_NAME}]}]}
        who = pc.authors(league, KEY)
        self.assertEqual(who, {"0:1": "Иванов", "0:2": pc.HIDDEN_NAME})
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(pc.subprocess, "run") as run:
            run.return_value = SimpleNamespace(returncode=1, stderr="нет")
            args = SimpleNamespace(out=Path(tmp), before=20, after=10, copy=True)
            with mock.patch.object(pc, "ffmpeg", return_value="ffmpeg"):
                pc.cut_match(KEY, "x", None, {"0:1": 1500, "0:2": 3400}, args, who=who)
        self.assertEqual(run.call_count, 1)                                  # режем только 0:1


class Matches(unittest.TestCase):
    def test_recorded_league_videos_only_played(self):
        league = {"games": [
            {"date": "2026-10-04", "home": "a", "away": "b", "score": {"home": 1, "away": 0},
             "watch": [{"src": "t.me", "url": "https://vk.com/video-1_2"},
                       {"src": "rhl.fhr.ru", "url": "https://vk.com/video-77_88"}]},
            {"date": "2026-10-04", "home": "c", "away": "d", "watch": [{"src": "rhl.fhr.ru",
                                                                        "url": "https://vk.com/video-1_3"}]},
            {"date": "2026-10-03", "home": "e", "away": "f", "score": {"home": 2, "away": 2},
             "watch": [{"src": "rhl.fhr.ru", "url": "https://vk.com/video-1_4"}]}]}
        self.assertEqual(sb.recorded(league, {"2026-10-04"}),
                         {"2026-10-04|a|b": {"video": "https://vk.com/video-77_88", "anchors": {}}})


class Command(unittest.TestCase):
    def test_copy_not_reencode(self):
        with mock.patch.object(pc, "ffmpeg", return_value="ffmpeg"):   # на раннере GitHub ffmpeg нет
            cmd = pc.cut_cmd("https://x/rec.m3u8", {"Referer": "https://vk.com/"}, 2553, 30, Path("out.mp4"))
        self.assertLess(cmd.index("-ss"), cmd.index("-i"))          # качается только окно
        self.assertEqual(cmd[cmd.index("-c") + 1], "copy")
        self.assertIn("Referer: https://vk.com/\r\n", cmd)

    def test_mark_reencodes_with_icon(self):
        with mock.patch.object(pc, "ffmpeg", return_value="ffmpeg"):
            cmd = pc.cut_cmd("x", None, 10, 30, Path("out.mp4"), (Path("m.txt"), Path("s.txt"), "/f.ttf"))
        self.assertIn(str(pc.ICON), cmd)
        flt = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("textfile='m.txt'", flt)
        self.assertIn("textfile='s.txt'", flt)
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "libx264")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "нет ffmpeg")
class Cut(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc2=s=640x360:r=25:d=60", "-f", "lavfi", "-i", "sine=f=440:d=60",
                        "-c:v", "libx264", "-preset", "ultrafast", "-g", "50", "-c:a", "aac", "-f", "hls",
                        "-hls_time", "4", "-hls_playlist_type", "vod", str(tmp / "rec.m3u8")], check=True)
        self.src = str(tmp / "rec.m3u8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_cut_copy_from_hls(self):
        args = SimpleNamespace(out=Path(self.tmp.name) / "cuts", before=20, after=10, copy=True)
        done = pc.cut_match("матч", self.src, None, {"1:0": 30, "1:1": 55}, args)
        self.assertEqual([c["score"] for c in done], ["1:0", "1:1"])
        self.assertAlmostEqual(done[0]["duration"], 30, delta=2.5)   # края — по ключевым кадрам
        self.assertAlmostEqual(done[1]["duration"], 25, delta=2.5)   # запись кончилась раньше

    def test_cut_with_mark_is_720p(self):
        args = SimpleNamespace(out=Path(self.tmp.name) / "cuts", before=5, after=5, copy=False)
        done = pc.cut_match("матч", self.src, None, {"1:0": 30}, args, {"1:0": "admin"})
        self.assertEqual(len(done), 1)
        out = Path(self.tmp.name) / "cuts" / "матч"
        size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height",
                               "-of", "csv=p=0", str(out / "1-0.mp4")], capture_output=True, text=True).stdout.strip()
        self.assertEqual(size, "720")
        self.assertIn('"src": "admin"', (out / "clips.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

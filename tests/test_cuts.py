"""tools/probe_cuts.py (ADR-029, этап 0): окна клипов вокруг голов и резка без перекодирования."""
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


class Windows(unittest.TestCase):
    def test_goal_order_and_edges(self):
        got = pc.windows({"0:2": 2968, "0:1": 2573, "1:0": 12, "плохо": 5, "2:1": "1:00"})
        self.assertEqual([(w["score"], w["start"], w["length"], w["file"]) for w in got],
                         [("1:0", 0, 22, "1-0.mp4"), ("0:1", 2553, 30, "0-1.mp4"), ("0:2", 2948, 30, "0-2.mp4")])

    def test_longer_window(self):
        self.assertEqual(pc.windows({"1:0": 100}, before=20, after=40)[0]["length"], 60)

    def test_copy_not_reencode(self):
        with mock.patch.object(pc, "ffmpeg", return_value="ffmpeg"):   # на раннере GitHub ffmpeg нет
            cmd = pc.cut_cmd("https://x/rec.m3u8", {"Referer": "https://vk.com/"}, 2553, 30, Path("out.mp4"))
        self.assertLess(cmd.index("-ss"), cmd.index("-i"))          # качается только окно
        self.assertEqual(cmd[cmd.index("-c") + 1], "copy")
        self.assertIn("Referer: https://vk.com/\r\n", cmd)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "нет ffmpeg")
class Cut(unittest.TestCase):
    def test_cut_from_hls(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "testsrc2=s=320x180:r=25:d=60", "-f", "lavfi", "-i", "sine=f=440:d=60",
                            "-c:v", "libx264", "-preset", "ultrafast", "-g", "50", "-c:a", "aac", "-f", "hls",
                            "-hls_time", "4", "-hls_playlist_type", "vod", str(tmp / "rec.m3u8")], check=True)
            args = SimpleNamespace(out=tmp / "cuts", before=20, after=10)
            done = pc.cut_match("матч", str(tmp / "rec.m3u8"), None, {"1:0": 30, "1:1": 55}, args)
        self.assertEqual([c["score"] for c in done], ["1:0", "1:1"])
        self.assertAlmostEqual(done[0]["duration"], 30, delta=2.5)   # края — по ключевым кадрам
        self.assertAlmostEqual(done[1]["duration"], 25, delta=2.5)   # запись кончилась раньше


if __name__ == "__main__":
    unittest.main()

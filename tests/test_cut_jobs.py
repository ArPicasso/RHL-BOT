"""Служба cuts и её очередь (ADR-036, раздел 2): задания в state.db, нарезка по одному, кэш адреса потока, чистка."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import admin  # noqa: E402
import cutjobs  # noqa: E402
import cuts  # noqa: E402
import probe_cuts as pc  # noqa: E402

TZ = ZoneInfo("Europe/Moscow")
VIDEO = "https://vk.com/video-100_200"
KEY = "2026-10-06|belgorod|ryazan-vdv"


def msk(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


class Store(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.conn = sqlite3.connect(self.dir / "state.db", isolation_level=None)
        self.addCleanup(self.conn.close)
        self.jobs = cutjobs.CutJobs(self.conn, self.dir)
        self.now = msk("2026-10-07T20:00:00")

    def finish(self, job_id: int, now=None) -> None:
        rel = f"{cutjobs.DIR}/{job_id}.mp4"
        (self.dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.dir / rel).write_bytes(b"v")
        self.jobs.done(job_id, now or self.now, rel, 854, 480, 30)


class Queue(Store):
    def test_window_and_video(self):
        self.assertEqual(cutjobs.review_window(3000), (2980, 30))          # как клип болельщикам: 20 до, 10 после
        self.assertEqual(cutjobs.review_window(10), (0, 20))
        self.assertEqual(cutjobs.review_window(3000, length=3005), (2980, 25))
        self.assertEqual((cutjobs.REVIEW_BEFORE, cutjobs.REVIEW_AFTER), (pc.CLIP_BEFORE, pc.CLIP_AFTER))
        self.assertEqual(cutjobs.vid("https://vkvideo.ru/video-100_200?t=5m"), "-100_200")
        self.assertEqual(cutjobs.vid("https://vk.com/live-100_200"), "-100_200")

    def test_same_window_same_job(self):
        a = self.jobs.want(self.now, VIDEO, 2980, 30, "review", match=KEY, score="1:0")
        b = self.jobs.want(self.now, "https://vkvideo.ru/video-100_200", 2980, 30, "preview", prio=cutjobs.URGENT)
        c = self.jobs.want(self.now, VIDEO, 2980, 31, "review")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        got = self.jobs.get(a)
        self.assertEqual((got["kind"], got["prio"], got["match"], got["video"]), ("review", cutjobs.URGENT, KEY, VIDEO))
        self.jobs.want(self.now, VIDEO, 2980, 30, "review", prio=cutjobs.PREP)   # заготовка срочность не снижает
        self.assertEqual(self.jobs.get(a)["prio"], cutjobs.URGENT)
        with self.assertRaises(ValueError):
            self.jobs.want(self.now, VIDEO, 1, 30, "кусок")

    def test_human_first(self):
        prep = self.jobs.want(self.now, VIDEO, 100, 30, "review")
        send = self.jobs.want(self.now, VIDEO, 200, 125, "preview", prio=cutjobs.SEND)
        urgent = self.jobs.want(self.now, VIDEO, 300, 180, "search", prio=cutjobs.URGENT)
        order = [self.jobs.take(self.now)["id"] for _ in range(3)]
        self.assertEqual(order, [urgent, send, prep])
        self.assertIsNone(self.jobs.take(self.now))
        self.assertEqual(self.jobs.get(prep)["status"], "work")

    def test_retry_then_give_up(self):
        a = self.jobs.want(self.now, VIDEO, 100, 30, "review")
        self.jobs.take(self.now)
        self.assertFalse(self.jobs.fail(a, self.now, "VK не отдал запись"))
        self.assertIsNone(self.jobs.take(self.now + timedelta(seconds=30)))       # пауза перед повтором
        self.assertEqual(self.jobs.take(self.now + timedelta(minutes=2))["id"], a)
        self.assertFalse(self.jobs.fail(a, self.now, "ещё раз"))
        self.assertIsNone(self.jobs.take(self.now + timedelta(minutes=5)))
        self.assertEqual(self.jobs.take(self.now + timedelta(minutes=11))["id"], a)
        self.assertTrue(self.jobs.fail(a, self.now, "и снова"))                  # третья — последняя
        self.assertIsNone(self.jobs.take(self.now + timedelta(days=1)))
        self.assertEqual(self.jobs.counts()["error"], 1)
        self.jobs.want(self.now, VIDEO, 100, 30, "review")                        # заготовка VK не дёргает
        self.assertIsNone(self.jobs.take(self.now + timedelta(days=1)))
        self.jobs.want(self.now, VIDEO, 100, 30, "review", prio=cutjobs.URGENT)   # человек попросил — пробуем
        got = self.jobs.take(self.now)
        self.assertEqual((got["id"], got["tries"]), (a, 0))

    def test_done_file_and_lost_file(self):
        a = self.jobs.want(self.now, VIDEO, 100, 30, "review")
        self.jobs.take(self.now)
        self.finish(a)
        job = self.jobs.get(a)
        self.assertEqual(self.jobs.path(job), self.dir / cutjobs.DIR / f"{a}.mp4")
        self.assertEqual((job["w"], job["h"], job["dur"]), (854, 480, 30))
        self.assertEqual(self.jobs.want(self.now, VIDEO, 100, 30, "review"), a)
        self.assertEqual(self.jobs.get(a)["status"], "done")                      # готовое не режем заново
        (self.dir / cutjobs.DIR / f"{a}.mp4").unlink()
        self.assertIsNone(self.jobs.path(self.jobs.get(a)))
        self.jobs.want(self.now, VIDEO, 100, 30, "review")
        self.assertEqual(self.jobs.get(a)["status"], "queued")                    # файл пропал — режем снова

    def test_restart_and_forget(self):
        old = self.jobs.want(self.now - timedelta(days=4), VIDEO, 100, 30, "review")
        self.jobs.take(self.now)
        self.finish(old)
        kept = self.jobs.want(self.now - timedelta(days=4), VIDEO, 200, 30, "review")
        self.jobs.want(self.now, VIDEO, 200, 30, "review")                        # снова просили — живёт дальше
        self.jobs.want(self.now, VIDEO, 300, 30, "review")
        work = self.jobs.take(self.now)["id"]                                    # служба упала посреди нарезки
        self.assertEqual(self.jobs.reset_work(), 1)
        self.assertEqual(self.jobs.get(work)["status"], "queued")
        self.assertEqual(self.jobs.forget(self.now), [f"{cutjobs.DIR}/{old}.mp4"])
        self.assertIsNone(self.jobs.get(old))
        self.assertIsNotNone(self.jobs.get(kept))
        self.assertEqual(self.jobs.counts(), {"queued": 2, "urgent": 0, "work": 0, "done": 0, "error": 0})

    def test_touch_not_every_time(self):
        a = self.jobs.want(self.now, VIDEO, 100, 30, "review")
        self.jobs.want(self.now + timedelta(minutes=10), VIDEO, 100, 30, "review")
        self.assertEqual(self.jobs.get(a)["used"], self.now.isoformat(timespec="seconds"))
        later = self.now + timedelta(hours=2)
        self.jobs.want(later, VIDEO, 100, 30, "review")
        self.assertEqual(self.jobs.get(a)["used"], later.isoformat(timespec="seconds"))


class Service(Store):
    def setUp(self):
        super().setUp()
        self.track = admin.Tracker("cuts", path=self.dir / "cuts.json", clock=lambda: self.now)
        self.fetch = mock.Mock(return_value=("https://cdn/x.m3u8", {"Referer": "https://vk.com/"}, 9000))
        self.clock = mock.Mock(return_value=self.now)
        self.streams = cuts.Streams(self.fetch, clock=self.clock)
        p = mock.patch.object(cuts, "now_msk", return_value=self.now)
        p.start()
        self.addCleanup(p.stop)

    def run_one(self, cut=None, info=None) -> tuple[dict, bool]:
        job = self.jobs.take(self.now)
        cut = cut or self.writes()
        ok = cuts.run_job(job, self.jobs, self.streams, ("m", "s", "f"), self.track, cut=cut,
                          info=info or (lambda p: {"w": 854, "h": 480, "dur": job["len"]}), root=self.dir)
        return self.jobs.get(job["id"]), ok

    @staticmethod
    def writes(err: str = ""):
        def cut(src, headers, start, length, path, mark):
            if not err:
                path.write_bytes(b"video")
            return err
        return mock.Mock(side_effect=cut)

    def test_cut_ok(self):
        a = self.jobs.want(self.now, VIDEO, 2980, 30, "review", match=KEY, score="1:0")
        cut = self.writes()
        job, ok = self.run_one(cut)
        self.assertTrue(ok)
        self.assertEqual((job["status"], job["file"], job["dur"]), ("done", f"{cutjobs.DIR}/{a}.mp4", 30))
        self.assertTrue((self.dir / job["file"]).is_file())
        self.assertFalse(list((self.dir / cutjobs.DIR).glob("*.part.mp4")))
        self.assertEqual(cut.call_args.args[:4], ("https://cdn/x.m3u8", {"Referer": "https://vk.com/"}, 2980, 30))
        day = self.track.today()
        self.assertEqual((day["cuts"], day["vk_ok"]), (1, 1))

    def test_stream_once_per_video(self):
        """Адрес потока — один на ролик на STREAM_TTL: кусок за куском одного матча не спрашивают VK заново."""
        for start in (100, 200):
            self.jobs.want(self.now, VIDEO, start, 30, "review")
        self.run_one()
        self.run_one()
        self.assertEqual(self.fetch.call_count, 1)
        self.assertEqual(self.track.today()["vk_ok"], 1)
        self.clock.return_value = self.now + cuts.STREAM_TTL
        self.jobs.want(self.now, VIDEO, 300, 30, "review")
        self.run_one()
        self.assertEqual(self.fetch.call_count, 2)

    def test_stale_stream_retried_fresh(self):
        self.jobs.want(self.now, VIDEO, 100, 30, "review")
        self.jobs.want(self.now, VIDEO, 200, 30, "review")
        self.run_one()
        calls = iter(["403 Forbidden", ""])
        cut = mock.Mock(side_effect=lambda *a: (Path(a[4]).write_bytes(b"v"), next(calls))[1])
        job, ok = self.run_one(cut)
        self.assertTrue(ok)
        self.assertEqual(self.fetch.call_count, 2)    # старый адрес не подошёл — взяли свежий
        self.assertEqual(cut.call_count, 2)

    def test_vk_refused(self):
        a = self.jobs.want(self.now, VIDEO, 100, 30, "review")
        self.fetch.side_effect = cuts.clips.VkError("DownloadError: This video has been deleted")
        job, ok = self.run_one()
        self.assertFalse(ok)
        self.assertEqual((job["id"], job["status"], job["tries"]), (a, "error", 1))
        self.assertIn("VK не отдал запись", job["error"])
        day = self.track.today()
        self.assertEqual((day["vk_fail"], day["cut_fail"]), (1, 1))
        self.assertIn("deleted", self.track.info_["cut_error"])

    def test_short_or_outside(self):
        self.jobs.want(self.now, VIDEO, 100, 125, "preview")
        job, ok = self.run_one(info=lambda p: {"w": 854, "h": 480, "dur": 3})
        self.assertFalse(ok)
        self.assertIn("вышло 3 с вместо 125", job["error"])
        self.assertFalse(list((self.dir / cutjobs.DIR).glob("*.mp4")))
        self.jobs.want(self.now, VIDEO, 9100, 30, "review")
        cut = self.writes()
        job, ok = self.run_one(cut)
        self.assertIn("за концом записи", job["error"])
        cut.assert_not_called()

    def test_ffmpeg_error_is_job_error(self):
        self.jobs.want(self.now, VIDEO, 100, 30, "review")
        job, ok = self.run_one(self.writes("Server returned 404 Not Found"))
        self.assertEqual((job["status"], job["error"]), ("error", "Server returned 404 Not Found"))

    def test_clean(self):
        a = self.jobs.want(self.now - timedelta(days=5), VIDEO, 100, 30, "review")
        self.run_one()
        stray = self.dir / cutjobs.DIR / "999.mp4"
        stray.write_bytes(b"v")
        old = (self.now - timedelta(days=6)).timestamp()
        os.utime(stray, (old, old))
        fresh = self.dir / cutjobs.DIR / "998.mp4"
        fresh.write_bytes(b"v")
        self.assertEqual(cuts.clean(self.jobs, self.now, root=self.dir), 1)
        self.assertIsNone(self.jobs.get(a))
        self.assertFalse((self.dir / cutjobs.DIR / f"{a}.mp4").exists())
        self.assertFalse(stray.exists())
        self.assertTrue(fresh.exists())


class Command(unittest.TestCase):
    def test_480p_with_mark_exact_start(self):
        with mock.patch.object(pc, "ffmpeg", return_value="ffmpeg"):   # на раннере GitHub ffmpeg нет
            cmd = pc.cut_cmd("http://x/s.m3u8", {"Referer": "https://vk.com"}, 2849, 125, Path("p.mp4"),
                             (Path("m.txt"), Path("s.txt"), "/f.ttf"), height=cuts.HEIGHT, crf=cuts.CRF)
        self.assertLess(cmd.index("-ss"), cmd.index("-i"))
        self.assertIn("libx264", cmd)                       # перекодируем: нулевая секунда видео — ровно 2849
        self.assertEqual(cmd[cmd.index("-t") + 1], "125")
        self.assertEqual(cmd[cmd.index("-crf") + 1], "28")
        flt = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("scale=-2:480", flt)
        self.assertIn("fontsize=19", flt)                   # знак — в той же доле кадра, что у клипа 720p

    def test_clip_mark_unchanged(self):
        flt = pc.mark_filter(Path("m.txt"), Path("s.txt"), "/f.ttf")
        self.assertIn("scale=-2:720", flt)
        self.assertIn("scale=56:56", flt)
        self.assertIn("fontsize=28", flt)
        self.assertIn("y=h-20-22[out]", flt)

    def test_video_info_from_ffprobe(self):
        out = json.dumps({"streams": [{"width": 854, "height": 480}], "format": {"duration": "125.000000"}})
        with mock.patch.object(cuts.subprocess, "run", return_value=mock.Mock(stdout=out)):
            self.assertEqual(cuts.video_info(Path("p.mp4")), {"w": 854, "h": 480, "dur": 125})
        with mock.patch.object(cuts.subprocess, "run", side_effect=OSError):
            self.assertEqual(cuts.video_info(Path("p.mp4")), {})


if __name__ == "__main__":
    unittest.main()

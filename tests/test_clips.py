"""Служба clips (ADR-030, шаг 2): какие матчи разбирать, голы по табло в clips.json, повторы по ним."""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
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
        self.assertLessEqual({"kaluga", "rostov", "ryazan-vdv", "tverichi", "proton"}, set(sb.BOARDS))
        for club, b in sb.BOARDS.items():                                  # клетки — внутри рамки 240×90
            for cell in ("name", "home", "away", "clock"):
                x0, y0, x1, y1 = b[cell]
                self.assertTrue(0 <= x0 < x1 <= sb.W and 0 <= y0 < y1 <= sb.H, f"{club} {cell}")
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
        done = {KEY: {"video": "https://vkvideo.ru/video-100_200", "status": "ok", "v": clips.VERSION}}
        self.assertEqual(clips.pending(LEAGUE, {}, done, self.today), [])
        old = {KEY: {**done[KEY], "v": 1}}                                    # разбор поменялся — заново
        self.assertEqual(len(clips.pending(LEAGUE, {}, old, self.today)), 1)
        failed = {KEY: {"video": VIDEO, "status": "error", "tries": 2, "v": clips.VERSION}}
        self.assertEqual(len(clips.pending(LEAGUE, {}, failed, self.today)), 1)
        failed[KEY]["tries"] = clips.TRIES
        self.assertEqual(clips.pending(LEAGUE, {}, failed, self.today), [])
        unmarked = {KEY: {"video": VIDEO, "status": "no_board", "v": clips.VERSION}}            # табло «Тверичей» уже размечено — заново
        self.assertEqual(len(clips.pending(LEAGUE, {}, unmarked, self.today)), 1)
        with mock.patch.dict(clips.sb.BOARDS, {}, clear=True):                # не размечено — ждём разметки
            self.assertEqual(clips.pending(LEAGUE, {}, unmarked, self.today), [])
        other = {KEY: {"video": "https://vk.com/video-1_1", "status": "ok", "v": clips.VERSION}}   # лига сменила ролик — заново
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
        scan.assert_called_once_with(KEY, "https://vk.com/video-100_200", {}, [])

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
        """Считаем от разбора: матч 03.10, разобранный сегодня, остаётся — его превью ещё ждут ответа."""
        with tempfile.TemporaryDirectory() as tmp:
            for name, age in (("2026-10-03_a_b", 0), ("2026-10-04_c_d", 4), ("x", 9)):
                (Path(tmp) / name).mkdir()
                when = (self.now - timedelta(days=age)).timestamp()
                os.utime(Path(tmp) / name, (when, when))
            clips.clean_work(self.now, Path(tmp))
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["2026-10-03_a_b", "x"])

    def test_season_backfill_newest_first_few_per_pass(self):
        """Догоняем сезон с SINCE: свежие матчи — первыми, не больше SCAN_MAX за проход."""
        games = [{"date": d, "home": h, "away": "b", "score": {"home": 1, "away": 0},
                  "watch": [{"src": "rhl.fhr.ru", "url": f"https://vk.com/video-1_{k}"}]}
                 for k, (d, h) in enumerate((("2026-10-03", "a"), ("2026-10-05", "c"), ("2026-10-04", "d")))]
        league = {"games": games}
        todo = clips.pending(league, {}, {}, date(2026, 10, 5))
        self.assertEqual([k for k, _ in todo], ["2026-10-05|c|b", "2026-10-04|d|b", "2026-10-03|a|b"])
        scan = mock.Mock(return_value={"status": "ok", "goals": {}})
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "LIVE_DIR", Path(tmp)):
            store = {}
            self.assertEqual(clips.run_pass(store, league, {}, self.now, scan=scan), (2, 1))
            self.assertEqual(clips.run_pass(store, league, {}, self.now, scan=scan), (1, 0))
            self.assertEqual(clips.run_pass(store, league, {}, self.now + timedelta(days=30), scan=scan), (0, 0))
        self.assertIn("2026-10-03|a|b", store["games"])     # матчи сезона не забываем: по ним «Повтор» и клипы


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


    def test_preview_keeps_size_and_skips_short(self):
        """05.10: превью без длины Telegram показывал «0:01» — длину и размер кладём в ask, короткое не шлём."""
        def run(cmd, **kw):
            Path(cmd[-1]).write_bytes(b"v")
            return mock.Mock(returncode=0)
        for info, has_ask in (({"w": 640, "h": 360, "dur": 125}, True), ({"w": 640, "h": 360, "dur": 1}, False)):
            goals = {"0:1": {"t": None, "change": 6869}}
            with tempfile.TemporaryDirectory(dir=clips.ROOT) as tmp, \
                    mock.patch.object(clips.sb, "stream_of", return_value=("http://x", {}, 9123)), \
                    mock.patch.object(clips.sb, "BOARDS", {}), mock.patch.object(clips.sb, "ffmpeg", return_value="f"), \
                    mock.patch.object(clips.subprocess, "run", side_effect=run), \
                    mock.patch.object(clips, "video_info", return_value=info):
                clips.add_previews(KEY, VIDEO, goals, 9123, Path(tmp))
            self.assertEqual("ask" in goals["0:1"], has_ask)
            if has_ask:
                self.assertEqual({k: goals["0:1"]["ask"][k] for k in ("w", "h", "dur")}, info)

    def test_video_info_from_ffprobe(self):
        out = json.dumps({"streams": [{"width": 640, "height": 360}], "format": {"duration": "125.000000"}})
        with mock.patch.object(clips.subprocess, "run", return_value=mock.Mock(stdout=out)):
            self.assertEqual(clips.video_info(Path("p.mp4")), {"w": 640, "h": 360, "dur": 125})
        with mock.patch.object(clips.subprocess, "run", side_effect=OSError):
            self.assertEqual(clips.video_info(Path("p.mp4")), {})


class ByOrder(unittest.TestCase):
    """03.10 служба live не записала времени голов: смены табло — голам по порядку протокола."""

    def test_changes_follow_score_order(self):
        """«Калуга — Динамо 576» 05.10: 4:0 сел на смену через 22 с после 1:0, 5:1 — раньше 4:1."""
        picked = {"1:0": 9758, "4:0": 9780, "2:0": 10423, "3:0": 10644, "5:1": 11160, "4:1": 11174}
        got = sb.in_order(picked)
        self.assertEqual(list(got), ["1:0", "2:0", "3:0"])   # 4:1 или 5:1 — не угадываем, оба админу
        self.assertEqual(sb.goal_rank("4:1"), 5)
        self.assertEqual(sb.in_order({}), {})

    def test_sides_by_score_not_site_time(self):
        goals = [("1:0", "1", 100.0), ("2:0", "2", 300.0), ("2:1", "3", 500.0), ("3:1", "3", 450.0)]   # сайт: 3:1 раньше 2:1
        self.assertEqual(sb.goal_sides(goals), {"1:0": "home", "2:0": "home", "2:1": "away", "3:1": "home"})

    def test_kth_change_is_kth_goal(self):
        found = [{"zone": "away", "hi": 900.0}, {"zone": "home", "hi": 500.0}, {"zone": "away", "hi": 2000.0}]
        order = [("1:0", "home"), ("1:1", "away"), ("1:2", "away")]
        self.assertEqual(sb.align_by_order(found, order), {"1:0": 500.0, "1:1": 900.0, "1:2": 2000.0})

    def test_extra_change_leaves_team_to_admin(self):
        found = [{"zone": "home", "hi": 500.0}, {"zone": "home", "hi": 560.0}, {"zone": "away", "hi": 900.0}]
        order = [("1:0", "home"), ("1:1", "away")]
        self.assertEqual(sb.align_by_order(found, order), {"1:1": 900.0})   # у хозяев смен больше, чем голов

    def test_protocol_order_from_league(self):
        league = {"games": [{"date": "2026-10-04", "home": "tverichi", "away": "metallurg", "goals": [
            {"score": "0:1", "team": "away", "period": "1"}, {"score": "1:1", "team": "home", "period": 2},
            {"score": "2:1", "team": "home", "period": "РБ"}]}]}
        self.assertEqual(clips.protocol_order(league, KEY), [("0:1", "away", "1"), ("1:1", "home", "2")])
        self.assertEqual(clips.protocol_order(None, KEY), [])


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


class S3Sign(unittest.TestCase):
    def test_aws_example_vector(self):
        """Пример «GET Object» из документации AWS Signature V4: подпись должна совпасть до знака."""
        import s3
        h = s3.sign("GET", "examplebucket.s3.amazonaws.com", "/test.txt", {"Range": "bytes=0-9"}, s3.EMPTY,
                    "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "us-east-1",
                    datetime(2013, 5, 24, tzinfo=ZoneInfo("UTC")))
        self.assertTrue(h.endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"))
        self.assertIn("SignedHeaders=host;range;x-amz-content-sha256;x-amz-date", h)

    def test_store_from_env(self):
        import s3
        self.assertFalse(s3.Store({}).ok)
        st = s3.Store({"CLIPS_S3_KEY": "k", "CLIPS_S3_SECRET": "s"})
        self.assertTrue(st.ok)
        self.assertEqual(st.url("clips/2026-10-04/a_b/0-1-60.mp4"),
                         "https://s3.twcstorage.ru/rhl-clips/clips/2026-10-04/a_b/0-1-60.mp4")


class Cutting(unittest.TestCase):
    """Шаг 6: что резать, что убрать, выкладка в бакет и клип у гола протокола."""
    game = {"video": VIDEO, "goals": {"0:1": {"t": 2600, "src": "clock", "team": "away"},
                                      "0:2": {"t": None, "change": 2969}}}
    protocol = {"0:1": {"score": "0:1", "team": "away", "author": "Иванов", "assists": []},
                "0:2": {"score": "0:2", "team": "away", "author": "Петров", "assists": ["Игрок скрыт"]}}

    def test_plan_needs_second_and_protocol_and_no_hidden(self):
        admin = {"video": "https://vkvideo.ru/video-100_200", "anchors": {"0:2": 2960}}
        cut, drop = clips.clip_plan(self.game, admin, self.protocol)
        self.assertEqual((cut, drop), ([("0:1", 2600, "clock")], []))   # 0:2 — ассистент скрыт
        self.assertEqual(clips.clip_plan(self.game, None, {}), ([], []))  # протокола нет — ждём
        done = {**self.game, "clips": {"0:1": {"t": 2600, "team": "away"}}}
        self.assertEqual(clips.clip_plan(done, None, self.protocol), ([], []))
        moved = {**done, "goals": {"0:1": {"t": 2610, "src": "clock"}}}
        self.assertEqual(clips.clip_plan(moved, None, self.protocol)[0], [("0:1", 2610, "clock")])
        gone = {**done, "clips": {"0:1": {"t": 2600, "team": "home"}}}   # счета сдвинулись: другой команды
        self.assertEqual(clips.clip_plan(gone, None, self.protocol)[1], ["0:1"])
        lost = {**done, "goals": {"0:1": {"t": None, "src": None}}}   # разбор поправили: секунды у гола нет
        self.assertEqual(clips.clip_plan(lost, None, self.protocol), ([], ["0:1"]))

    def test_admin_second_wins(self):
        admin = {"video": VIDEO, "anchors": {"0:1": 2590}}
        self.assertEqual(clips.goal_seconds(self.game, admin)["0:1"], (2590, "admin"))
        other = {"video": "https://vk.com/video-9_9", "anchors": {"0:1": 10}}
        self.assertEqual(clips.goal_seconds(self.game, other)["0:1"], (2600, "clock"))

    def test_pass_uploads_and_records(self):
        bucket = mock.Mock(ok=True)
        bucket.put.side_effect = lambda name, body, ct: f"https://s3.twcstorage.ru/rhl-clips/{name}"
        store = {"games": {KEY: json.loads(json.dumps(self.game))}}
        league = {"games": [{"date": "2026-10-04", "home": "tverichi", "away": "metallurg",
                             "goals": list(self.protocol.values())}]}
        with tempfile.TemporaryDirectory() as tmp:
            clip, poster = Path(tmp) / "c.mp4", Path(tmp) / "p.jpg"
            clip.write_bytes(b"mp4")
            poster.write_bytes(b"jpg")
            cut = mock.Mock(return_value=(clip, poster, 30.0))
            stream = mock.Mock(return_value=("src", {}, 7200))
            with mock.patch.object(clips, "LIVE_DIR", Path(tmp)), mock.patch.object(clips, "WORK", Path(tmp)), \
                    mock.patch.object(clips.pc, "font_file", return_value="font.ttf"):
                self.assertEqual(clips.cut_pass(store, league, {}, bucket, cut=cut, stream=stream), 1)
        c = store["games"][KEY]["clips"]["0:1"]
        self.assertEqual((c["t"], c["team"], c["dur"]), (2600, "away", 30.0))
        self.assertEqual(c["mp4"], "https://s3.twcstorage.ru/rhl-clips/clips/2026-10-04/tverichi_metallurg/0-1-2600.mp4")
        self.assertEqual([x.args[0] for x in bucket.put.call_args_list],
                         ["clips/2026-10-04/tverichi_metallurg/0-1-2600.mp4", "clips/2026-10-04/tverichi_metallurg/0-1-2600.jpg"])

    def test_no_keys_no_cutting(self):
        self.assertEqual(clips.cut_pass({"games": {KEY: dict(self.game)}}, None, {}, mock.Mock(ok=False)), 0)

    def test_build_clip_on_protocol_goal(self):
        g = {"date": "2026-10-04", "home": "tverichi", "away": "metallurg", "goals": [
            {"period": "1", "score": "0:1", "team": "away", "author": "Иванов", "assists": []},
            {"period": "1", "score": "0:2", "team": "away", "author": b.HIDDEN_NAME, "assists": []},
            {"period": "2", "score": "1:2", "team": "home", "author": "Сидоров", "assists": []}]}
        url = "https://s3.twcstorage.ru/rhl-clips/clips/x"
        have = {KEY: {"clips": {"0:1": {"team": "away", "mp4": url + ".mp4", "poster": url + ".jpg", "dur": 30},
                                "0:2": {"team": "away", "mp4": url + "2.mp4", "poster": url + "2.jpg"},
                                "1:2": {"team": "away", "mp4": url + "3.mp4", "poster": url + "3.jpg"}}}}
        self.assertEqual(b.apply_clips([g], have), 1)
        self.assertEqual(g["goals"][0]["clip"], {"mp4": url + ".mp4", "poster": url + ".jpg", "dur": 30})
        self.assertNotIn("clip", g["goals"][1])   # скрыт по просьбе
        self.assertNotIn("clip", g["goals"][2])   # клип другой команды — не этот гол

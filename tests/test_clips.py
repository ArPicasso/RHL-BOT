"""Служба clips (ADR-030, шаг 2): какие матчи разбирать, голы по табло в clips.json, повторы по ним."""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

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
     "watch": [{"src": "t.me/club", "url": "https://vk.com/video-1_2"}]},                 # запись клуба (ADR-031)
    {"date": "2026-10-05", "home": "kaluga", "away": "dinamo-576",
     "watch": [{"src": "rhl.fhr.ru", "url": "https://vk.com/video-3_4"}]},                # ещё не сыгран
    {"date": "2026-09-28", "home": "sokol", "away": "proton", "score": {"home": 1, "away": 0},
     "watch": [{"src": "rhl.fhr.ru", "url": "https://vk.com/video-5_6"}]},                # давно
]}


class Boards(unittest.TestCase):
    def test_markup_from_file(self):
        self.assertLessEqual({"kaluga", "rostov", "ryazan-vdv", "tverichi", "proton", "krasnaya-mashina",
                              "fakel-yamal"}, set(sb.BOARDS))
        for club, b in sb.BOARDS.items():                                  # клетки — внутри рамки 240×90
            x, y, w, h = b["box"]                                          # рамка — внутри кадра
            self.assertTrue(0 <= x and 0 <= y and x + w <= 1 and y + h <= 1, f"{club} box")
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
        self.assertEqual(clips.pending(LEAGUE, {}, {}, self.today),
                         [(KEY, "https://vk.com/video-100_200"),
                          ("2026-10-04|rostov|krasnodar", "https://vk.com/video-1_2")])

    def test_recordings_league_club_admin(self):
        """ADR-031: записи лиги нет — ролик VK из поста клуба; ссылка админа главнее всех; короткий ролик клуба —
        не запись матча, берём следующий."""
        days = clips.season_days(self.today)
        got = clips.recordings(LEAGUE, {}, days)
        self.assertEqual(got[KEY], {"video": "https://vk.com/video-100_200", "src": "league"})
        self.assertEqual(got["2026-10-04|rostov|krasnodar"], {"video": "https://vk.com/video-1_2", "src": "club"})
        league = json.loads(json.dumps(LEAGUE))
        league["games"][1]["watch"].append({"src": "t.me/club2", "url": "https://vk.com/video-1_3"})
        store = {"2026-10-04|rostov|krasnodar": {"short": ["https://vk.com/video-1_2"]}}
        self.assertEqual(clips.recordings(league, {}, days, store)["2026-10-04|rostov|krasnodar"]["video"],
                         "https://vk.com/video-1_3")
        marked = {KEY: {"video": "https://vk.com/video-7_8"}}
        self.assertEqual(clips.recordings(LEAGUE, marked, days)[KEY]["src"], "admin")

    def test_admin_video_wins_and_is_taken_alone(self):
        marked = {KEY: {"video": "https://vk.com/video-7_8", "anchors": {}},
                  "2026-10-05|samara|sokol": {"video": "https://vk.com/video-9_9", "anchors": {"1:0": 60}}}
        got = dict(clips.pending(LEAGUE, marked, {}, self.today))
        self.assertEqual(got[KEY], "https://vk.com/video-7_8")
        self.assertIn("2026-10-05|samara|sokol", got)

    def test_done_once_per_video_and_retries(self):
        one = {"games": LEAGUE["games"][:1]}                                  # только «Тверичи — Металлург»
        done = {KEY: {"video": "https://vkvideo.ru/video-100_200", "status": "ok", "v": clips.VERSION},
                "2026-10-04|rostov|krasnodar": {"video": "https://vk.com/video-1_2", "status": "short",
                                                "v": clips.VERSION}}
        self.assertEqual(clips.pending(LEAGUE, {}, done, self.today), [])     # короткий ролик клуба — тоже готово
        old = {KEY: {**done[KEY], "v": 1}}                                    # разбор поменялся — заново
        self.assertEqual(len(clips.pending(one, {}, old, self.today)), 1)
        failed = {KEY: {"video": VIDEO, "status": "error", "tries": 2, "v": clips.VERSION}}
        self.assertEqual(len(clips.pending(one, {}, failed, self.today)), 1)
        failed[KEY]["tries"] = clips.TRIES
        self.assertEqual(clips.pending(one, {}, failed, self.today), [])
        unmarked = {KEY: {"video": VIDEO, "status": "no_board", "v": clips.VERSION}}            # табло «Тверичей» уже размечено — заново
        self.assertEqual(len(clips.pending(one, {}, unmarked, self.today)), 1)
        with mock.patch.dict(clips.sb.BOARDS, {}, clear=True):                # не размечено — ждём разметки
            self.assertEqual(clips.pending(one, {}, unmarked, self.today), [])
        other = {KEY: {"video": "https://vk.com/video-1_1", "status": "ok", "v": clips.VERSION}}   # лига сменила ролик — заново
        self.assertEqual(len(clips.pending(one, {}, other, self.today)), 1)


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
        self.assertEqual(scan.call_args_list[0].args, (KEY, "https://vk.com/video-100_200", {}, [], "league"))
        self.assertEqual(scan.call_args_list[1].args[4], "club")                # запись клуба — с пометкой

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


class Pulse(unittest.TestCase):
    """ADR-030, раздел 7: пульс и счётчики службы для пульта, отказ VK — отдельно от прочих ошибок."""
    now = datetime(2026, 10, 5, 12, 0, tzinfo=TZ)

    def track(self, tmp):
        import admin
        return admin.Tracker("clips", path=Path(tmp) / "clips-status.json", clock=lambda: self.now)

    def test_vk_refusal_counted_and_beat_written(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "LIVE_DIR", Path(tmp)):
            t = self.track(tmp)
            clips.run_pass({}, LEAGUE, {}, self.now, scan=mock.Mock(side_effect=clips.VkError("HTTP 403")), track=t)
            clips.run_pass({}, LEAGUE, {}, self.now, scan=mock.Mock(side_effect=RuntimeError("ffmpeg")), track=t)
            day = json.loads((Path(tmp) / "clips-status.json").read_text(encoding="utf-8"))["days"]["2026-10-05"]
            self.assertEqual(day, {"vk_fail": 2})   # два матча за проход; ffmpeg упал — не VK
            clips.run_pass({}, LEAGUE, {}, self.now, scan=mock.Mock(return_value={"status": "ok", "goals": {}}), track=t)
            self.assertEqual(t.today(), {"vk_fail": 2, "vk_ok": 2})

    def test_stream_errors_become_vk_errors(self):
        with mock.patch.object(clips.sb, "stream_of", side_effect=RuntimeError("Unable to download webpage")):
            with self.assertRaises(clips.VkError):
                clips.stream(VIDEO)

    def test_catalog(self):
        store = {"games": {KEY: {"video": VIDEO, "status": "ok", "goals": {
            "0:1": {"t": 2600, "src": "clock"}, "0:2": {"t": None, "change": 2969, "ask": {"file": "p.mp4"}},
            "1:2": {"t": None, "change": 4000, "ask": {"file": "q.mp4"}}, "9:9": {"t": None}},
            "clips": {"0:1": {"t": 2600}}}}}
        league = json.loads(json.dumps(LEAGUE))
        league["games"][0]["goals"] = [{"score": s, "team": "away" if s[0] == "0" else "home", "period": "1"}
                                       for s in ("0:1", "0:2", "1:2")] + [{"score": "1:3", "period": "РБ"}]
        marked = {KEY: {"video": "https://vkvideo.ru/video-100_200", "anchors": {"1:2": 4010},
                        "goals": [{"score": "1:2", "t": 4000, "exact": True}]}}
        got = clips.catalog(store, league, marked, date(2026, 10, 5))
        self.assertEqual(got, {"goals": 3, "timed": 2, "timed_auto": 1, "timed_admin": 1, "clips": 1, "ask": 1,
                               "no_video": 0, "mismatch": 1, "no_board": 0, "boards": 0, "m_total": 2, "m_full": 1,
                               "m_none": 1, "g_replay": 3, "run": 0})
        # rostov — запись клуба, ещё не разобрана: ни одного повтора; 9:9 нет в протоколе

    def test_catalog_unmarked_board(self):
        """Дополнение 06.10: матчи клуба без разметки табло — отдельной плиткой."""
        league = json.loads(json.dumps(LEAGUE))
        league["games"][0]["goals"] = [{"score": "0:1", "team": "away", "period": "1"}]
        league["games"].append({"date": "2026-10-04", "home": "polet", "away": "sokol", "score": {"home": 1, "away": 0},
                                "watch": [{"src": "rhl.fhr.ru", "url": "https://vk.com/video-9_1"}]})
        store = {"games": {KEY: {"video": VIDEO, "status": "ok", "goals": {
            "0:1": {"t": None, "change": 2690, "ask": {"file": "p.mp4"}}}},
            "2026-10-04|polet|sokol": {"video": "https://vk.com/video-9_1", "status": "no_board", "goals": {}}}}
        got = clips.catalog(store, league, {}, date(2026, 10, 5))
        self.assertEqual((got["no_board"], got["boards"], got["ask"], got["mismatch"]), (1, 1, 1, 0))


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


class Guesses(unittest.TestCase):
    """Дополнение 06.10, вечер: гол, которого табло не нашло, превью по времени сайта лиги больше не получает — в
    пятиминутных окнах 06.10 гола не было («Протон — Кристалл» 6:3)."""
    guess = {"t": None, "change": None, "est": 6590, "ask": {"file": "p.mp4", "from": 6350, "len": 330, "est": 1}}

    def test_drop_guessed_goals(self):
        games = {KEY: {"status": "ok", "goals": {"0:2": {"t": 2963, "change": 2969}, "2:5": dict(self.guess)}},
                 "2026-10-04|polet|sokol": {"status": "no_board", "goals": {}}, "x": None}
        self.assertTrue(clips.drop_guesses(games))
        self.assertEqual(list(games[KEY]["goals"]), ["0:2"])
        self.assertFalse(clips.drop_guesses(games))

    def test_pass_writes_without_guesses(self):
        """Разбирать нечего, а гол с оценкой по сайту в clips.json остался с прошлого разбора — убираем и пишем:
        бот закроет его превью."""
        store = {"games": {KEY: {"video": VIDEO, "v": clips.VERSION, "status": "ok",
                                 "goals": {"0:2": {"t": 2963}, "2:5": dict(self.guess)}}}}
        with tempfile.TemporaryDirectory(dir=clips.ROOT) as tmp, mock.patch.object(clips, "LIVE_DIR", Path(tmp)):
            self.assertEqual(clips.run_pass(store, {"games": []}, {}, datetime(2026, 10, 5, 12, tzinfo=TZ)), (0, 0))
            disk = json.loads((Path(tmp) / "clips.json").read_text(encoding="utf-8"))
        self.assertEqual(list(disk["games"][KEY]["goals"]), ["0:2"])


class Grids(unittest.TestCase):
    """Дополнение 06.10: табло клуба не размечено — кадр для разметки держим до разметки и отдаём боту."""

    def test_todo_counts_matches_and_drops_marked(self):
        with tempfile.TemporaryDirectory(dir=clips.ROOT) as tmp:
            root = Path(tmp)
            (root / "polet.png").write_bytes(b"png")
            (root / "tverichi.png").write_bytes(b"png")                       # «Тверичей» уже разметили
            games = {"2026-10-03|polet|sokol": {"status": "no_board"}, "2026-10-05|polet|ermak": {"status": "no_board"},
                     "2026-10-04|ermak|polet": {"status": "no_board"}, KEY: {"status": "no_board"},
                     "2026-10-04|rostov|krasnodar": {"status": "ok"}}
            got = clips.boards_todo(games, root)
            self.assertEqual(got["polet"], {"matches": 2, "key": "2026-10-05|polet|ermak",
                                            "grid": str((root / "polet.png").relative_to(clips.ROOT))})
            self.assertEqual(got["ermak"], {"matches": 1, "key": "2026-10-04|ermak|polet"})   # кадра нет
            self.assertEqual(set(got), {"polet", "ermak"})
            self.assertFalse((root / "tverichi.png").exists())

    def test_grid_from_match_folder_made_before(self):
        with tempfile.TemporaryDirectory(dir=clips.ROOT) as tmp:
            work, root = Path(tmp) / "work", Path(tmp) / "grids"
            (work / "2026-10-03_polet_sokol").mkdir(parents=True)
            (work / "2026-10-03_polet_sokol" / "grid.png").write_bytes(b"png")
            with mock.patch.object(clips, "WORK", work):
                got = clips.boards_todo({"2026-10-03|polet|sokol": {"status": "no_board"}}, root)
            self.assertEqual((root / "polet.png").read_bytes(), b"png")
            self.assertIn("grid", got["polet"])

    def test_scan_keeps_grid_and_pass_writes_boards(self):
        league = {"games": [{"date": "2026-10-04", "home": "polet", "away": "sokol", "score": {"home": 1, "away": 0},
                             "watch": [{"src": "rhl.fhr.ru", "url": "https://vk.com/video-9_1"}]}]}

        def grid(src, headers, length, path):
            path.write_bytes(b"png")
        with tempfile.TemporaryDirectory(dir=clips.ROOT) as tmp, \
                mock.patch.object(clips, "LIVE_DIR", Path(tmp)), mock.patch.object(clips, "WORK", Path(tmp) / "w"), \
                mock.patch.object(clips, "GRIDS", Path(tmp) / "g"), \
                mock.patch.object(clips.sb, "stream_of", return_value=("http://x", {}, 9000)), \
                mock.patch.object(clips.sb, "grid_sheet", side_effect=grid):
            store = {}
            self.assertEqual(clips.run_pass(store, league, {}, datetime(2026, 10, 5, 12, tzinfo=TZ)), (1, 0))
            disk = json.loads((Path(tmp) / "clips.json").read_text(encoding="utf-8"))
            self.assertEqual(disk["games"]["2026-10-04|polet|sokol"]["status"], "no_board")
            self.assertEqual(disk["boards"]["polet"]["matches"], 1)
            self.assertTrue((Path(tmp) / "g" / "polet.png").is_file())
            with mock.patch.dict(clips.sb.BOARDS, {"polet": clips.sb.BOARDS["tverichi"]}):   # разметили — кадр уходит
                clips.run_pass(store, {"games": []}, {}, datetime(2026, 10, 5, 12, tzinfo=TZ))
            self.assertEqual(json.loads((Path(tmp) / "clips.json").read_text(encoding="utf-8"))["boards"], {})
            self.assertFalse((Path(tmp) / "g" / "polet.png").exists())


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


class AdminMarks(unittest.TestCase):
    """ADR-031: «гола в записи нет» и «табло сбилось» от админа снимают с гола секунду, смену табло и превью."""

    def games(self):
        return {KEY: {"video": VIDEO, "status": "ok", "goals": {
            "0:1": {"t": 100, "src": "clock", "team": "away", "change": 120, "ask": {"file": "a"}},
            "0:2": {"t": None, "team": "away", "change": 300, "ask": {"file": "b"}},
            "1:2": {"t": 500, "src": "board", "team": "home", "change": 506}}}}

    def test_wrong_drops_goal_and_later_goals_of_team(self):
        games = self.games()
        marked = {KEY: {"video": "https://vkvideo.ru/video-100_200", "anchors": {}, "wrong": ["0:1"]}}
        self.assertTrue(clips.apply_admin(games, marked))
        g = games[KEY]["goals"]
        self.assertEqual((g["0:1"]["off"], g["0:2"]["off"]), ("wrong", "wrong"))
        self.assertNotIn("ask", g["0:2"])
        self.assertEqual(g["1:2"]["t"], 500)                                 # гол другой команды не трогаем
        self.assertFalse(clips.apply_admin(games, marked))                    # второй раз — нечего снимать
        self.assertEqual(clips.goal_seconds(games[KEY], marked[KEY]), {"1:2": (500, "board")})

    def test_absent_and_admin_anchor_wins(self):
        games = self.games()
        marked = {KEY: {"video": VIDEO, "anchors": {"0:1": 95}, "absent": ["1:2"], "wrong": ["0:1"]}}
        clips.apply_admin(games, marked)
        g = games[KEY]["goals"]
        self.assertEqual(g["1:2"]["off"], "absent")
        self.assertNotIn("off", g["0:1"])                                    # своё время админа главнее пометки
        self.assertEqual(g["0:2"]["off"], "wrong")
        other = {KEY: {**marked[KEY], "video": "https://vk.com/video-5_5"}}   # пометки к другому ролику — не наши
        fresh = self.games()
        self.assertFalse(clips.apply_admin(fresh, other))


class ClockPass(unittest.TestCase):
    """ADR-031: счёт хода часов в службе — от гола с точной секундой к соседним голам периода."""

    def setUp(self):
        import test_clockrun as tc
        self.state = tc.match(tc.SEG)
        self.league = json.loads(json.dumps(LEAGUE))
        self.league["games"][0]["goals"] = [{"score": "0:1", "team": "away", "period": "1", "time": "05:00"},
                                            {"score": "0:2", "team": "away", "period": "1", "time": "08:20"}]
        self.store = {"games": {KEY: {"video": VIDEO, "status": "ok", "goals": {
            "0:1": {"t": 399, "src": "clock", "team": "away", "change": 420},
            "0:2": {"t": None, "team": "away", "change": 700, "ask": {"file": "p.mp4", "from": 580}}}}}}

    def run_pass(self, **kw):
        frames = SimpleNamespace(state=self.state)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "LIVE_DIR", Path(tmp)), \
                mock.patch.object(clips.sb, "stream_of", return_value=("http://x", {}, 9000)):
            n = clips.clock_pass(self.store, self.league, kw.get("marked", {}), dense=lambda key, game: frames)
            disk = json.loads((Path(tmp) / "clips.json").read_text(encoding="utf-8")) if n else None
        return n, disk

    def test_exact_second_by_clock_run(self):
        n, disk = self.run_pass()
        self.assertEqual(n, 1)
        g = disk["games"][KEY]["goals"]["0:2"]
        self.assertEqual((g["t"], g["src"]), (659, "run"))                   # смена табло согласна — точная
        self.assertNotIn("ask", g)                                           # превью больше не нужно
        self.assertEqual(disk["games"][KEY]["run"]["exact"], ["0:2"])
        self.assertEqual(self.run_pass()[0], 0)                              # ничего не поменялось — не пересчитываем

    def test_admin_answer_triggers_recount(self):
        self.run_pass()
        self.league["games"][0]["goals"].append({"score": "0:3", "team": "away", "period": "1", "time": "12:00"})
        marked = {KEY: {"video": VIDEO, "anchors": {"0:3": 950}}}            # админ ответил на превью 0:3
        n, disk = self.run_pass(marked=marked)
        self.assertEqual(n, 1)
        self.assertGreaterEqual(disk["games"][KEY]["run"]["checked"], 1)     # 0:1 и 0:3 проверили друг друга


class Coverage(unittest.TestCase):
    """ADR-031: у каждого сыгранного матча — сколько голов с повтором и почему не у всех."""

    def test_reasons(self):
        league = {"games": [
            {**LEAGUE["games"][0], "goals": [{"score": "0:1", "team": "away", "period": "1"},
                                             {"score": "0:2", "team": "away", "period": "1"}]},
            {"date": "2026-10-04", "home": "polet", "away": "sokol", "score": {"home": 1, "away": 0},
             "watch": [{"src": "rhl.fhr.ru", "url": "https://vk.com/video-9_1"}]},
            {"date": "2026-10-04", "home": "samara", "away": "ermak", "score": {"home": 2, "away": 0}},
            {"date": "2026-10-04", "home": "bryansk", "away": "tambov", "score": {"home": 0, "away": 0}},
            {"date": "2026-10-05", "home": "belgorod", "away": "tambov", "score": {"home": 1, "away": 1},
             "watch": [{"src": "rhl.fhr.ru", "url": "https://vk.com/video-8_1"}]}]}
        store = {"games": {
            KEY: {"video": VIDEO, "status": "ok", "goals": {"0:1": {"t": 2600, "src": "clock"}},
                  "rejected": {"0:2": "между 1-м и 2-м голом цифра сменилась ещё раз"}},
            "2026-10-04|polet|sokol": {"video": "https://vk.com/video-9_1", "status": "no_board", "goals": {}},
            "2026-10-05|belgorod|tambov": {"video": "https://vk.com/video-8_1", "status": "error", "tries": 3,
                                           "error": "VkError: HTTP 403", "goals": {}}}}
        got = clips.coverage(store, league, {}, date(2026, 10, 5))
        self.assertEqual(got[KEY]["why"], "not_found")
        self.assertEqual((got[KEY]["goals"], got[KEY]["replays"], got[KEY]["missing"]), (2, 1, ["0:2"]))
        self.assertIn("цифра", got[KEY]["rejected"]["0:2"])
        self.assertEqual(got["2026-10-04|polet|sokol"]["why"], "no_board")
        self.assertEqual(got["2026-10-04|samara|ermak"]["why"], "no_video")
        self.assertEqual(got["2026-10-04|bryansk|tambov"]["why"], "ok")      # 0:0 — повторять нечего
        self.assertEqual((got["2026-10-05|belgorod|tambov"]["why"], got["2026-10-05|belgorod|tambov"]["error"]),
                         ("error", "VkError: HTTP 403"))

    def test_absent_goal_does_not_count_as_missing(self):
        league = {"games": [{**LEAGUE["games"][0], "goals": [{"score": "0:1", "team": "away", "period": "1"},
                                                             {"score": "0:2", "team": "away", "period": "1"}]}]}
        store = {"games": {KEY: {"video": VIDEO, "status": "ok", "goals": {"0:2": {"t": 3000, "src": "clock"}}}}}
        marked = {KEY: {"video": VIDEO, "anchors": {}, "absent": ["0:1"], "goals": []}}
        got = clips.coverage(store, league, marked, date(2026, 10, 5))[KEY]
        self.assertEqual((got["why"], got["absent"], got["replays"]), ("ok", ["0:1"], 1))

    def test_short_club_video(self):
        with mock.patch.object(clips, "stream", return_value=("http://x", {}, 600)):
            got = clips.scan_match("2026-10-04|rostov|krasnodar", "https://vk.com/video-1_2", {}, [], "club")
        self.assertEqual(got["status"], "short")

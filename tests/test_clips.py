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

import admin  # noqa: E402
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
                              "fakel-yamal", "belgorod", "dizelist"}, set(sb.BOARDS))
        # шаблон табло «Рязань-ВДВ» — и у «Белгорода», и у «Дизелиста»: сверено по кадрам 06.10
        self.assertEqual(sb.BOARDS["belgorod"], sb.BOARDS["ryazan-vdv"])
        self.assertEqual(sb.BOARDS["dizelist"], sb.BOARDS["ryazan-vdv"])
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
                               "m_none": 1, "g_replay": 3, "run": 0, "two": 0})
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

    def test_only_goals_without_second(self):
        goals = {"0:2": {"t": 2963, "change": 2969}, "0:1": {"t": None, "change": None}}
        with mock.patch.object(clips.sb, "stream_of") as stream, mock.patch.object(clips, "cut_jobs") as jobs:
            clips.add_previews(KEY, VIDEO, goals, None)
        stream.assert_not_called()
        jobs.assert_not_called()
        self.assertNotIn("ask", goals["0:2"])

    def test_preview_is_a_cut_job(self):
        """07.10 (ADR-036): превью режет служба cuts — у гола окно, задание и моменты часов, своего ffmpeg нет.
        Одно окно — одно задание: тот же гол на следующем проходе получает то же задание."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "STATE_DB", Path(tmp) / "state.db"), \
                mock.patch.object(clips, "_jobs", None), mock.patch.object(clips.sb, "BOARDS", {}), \
                mock.patch.object(clips.sb, "stream_of") as stream:
            goals = {"0:1": {"t": None, "change": 6869}, "0:2": {"t": None, "change": None, "win": [7000, 7040],
                                                              "wcand": [7012, 7031]}}
            clips.add_previews(KEY, VIDEO, goals, 9123)
            again = {"0:1": {"t": None, "change": 6869}}
            clips.add_previews(KEY, VIDEO, again, 9123)
            jobs = clips.cut_jobs()
            first = jobs.get(goals["0:1"]["ask"]["job"])
            run = jobs.get(goals["0:2"]["ask"]["job"])
            jobs.conn.close()
        stream.assert_not_called()   # без разметки часов поток не нужен: окно знаем, моменты — от счёта хода
        self.assertEqual({k: goals["0:1"]["ask"][k] for k in ("from", "len", "cand")}, {"from": 6749, "len": 125,
                                                                                        "cand": []})
        self.assertNotIn("file", goals["0:1"]["ask"])
        self.assertEqual((first["start"], first["len"], first["kind"], first["prio"], first["match"], first["score"]),
                         (6749, 125, "preview", clips.cutjobs.SEND, KEY, "0:1"))
        self.assertEqual(again["0:1"]["ask"]["job"], first["id"])
        self.assertEqual((run["start"], run["len"]), (6980, 70))
        self.assertEqual(goals["0:2"]["ask"]["cand"], [32, 51])


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
        self.assertEqual(clips.protocol_order(league, KEY), [("0:1", "away", "1", None), ("1:1", "home", "2", None)])
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


class S3Sign(unittest.TestCase):
    def test_aws_example_vector(self):
        """Пример «GET Object» из документации AWS Signature V4: подпись должна совпасть до знака."""
        import s3
        h = s3.sign("GET", "examplebucket.s3.amazonaws.com", "/test.txt", {"Range": "bytes=0-9"}, s3.EMPTY,
                    "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "us-east-1",
                    datetime(2013, 5, 24, tzinfo=ZoneInfo("UTC")))
        self.assertTrue(h.endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"))
        self.assertIn("SignedHeaders=host;range;x-amz-content-sha256;x-amz-date", h)

    def test_aws_list_vector(self):
        """Пример «GET Bucket (List Objects)» из той же документации: подпись с query — для стирания клипов."""
        import s3
        h = s3.sign("GET", "examplebucket.s3.amazonaws.com", "/", {}, s3.EMPTY,
                    "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "us-east-1",
                    datetime(2013, 5, 24, tzinfo=ZoneInfo("UTC")), "max-keys=2&prefix=J")
        self.assertTrue(h.endswith("Signature=34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"))

    def test_store_from_env(self):
        import s3
        self.assertFalse(s3.Store({}).ok)
        st = s3.Store({"CLIPS_S3_KEY": "k", "CLIPS_S3_SECRET": "s"})
        self.assertTrue(st.ok)
        self.assertEqual(st.url("clips/2026-10-04/a_b/0-1-60.mp4"),
                         "https://s3.twcstorage.ru/rhl-clips/clips/2026-10-04/a_b/0-1-60.mp4")


class Cutting(unittest.TestCase):
    """Шаг 6: что резать, что убрать, выкладка в бакет и клип у гола протокола. 0:1 — часы встали, и ход часов от
    другого точного гола подтвердил: два свидетеля (ADR-033, раздел 4)."""
    game = {"video": VIDEO, "goals": {"0:1": {"t": 2600, "src": "clock", "team": "away"},
                                      "0:2": {"t": None, "change": 2969}}, "run": {"confirmed": ["0:1"]}}
    protocol = {"0:1": {"score": "0:1", "team": "away", "author": "Иванов", "assists": []},
                "0:2": {"score": "0:2", "team": "away", "author": "Петров", "assists": ["Игрок скрыт"]}}

    def test_plan_needs_second_and_protocol_and_no_hidden(self):
        admin = {"video": "https://vkvideo.ru/video-100_200", "anchors": {"0:2": 2960}}
        cut, drop = clips.clip_plan(self.game, admin, self.protocol)
        self.assertEqual((cut, drop), ([("0:1", 2600, "clock")], []))   # 0:2 — ассистент скрыт
        self.assertEqual(clips.clip_plan(self.game, None, {}), ([], []))  # протокола нет — ждём
        done = {**self.game, "clips": {"0:1": {"t": 2600, "team": "away"}}}
        self.assertEqual(clips.clip_plan(done, None, self.protocol), ([], []))
        moved = {**done, "goals": {"0:1": {"t": 2610, "src": "clock"}}}   # ход часов подтвердил новую секунду
        self.assertEqual(clips.clip_plan(moved, None, self.protocol)[0], [("0:1", 2610, "clock")])
        gone = {**done, "clips": {"0:1": {"t": 2600, "team": "home"}}}   # счета сдвинулись: другой команды
        self.assertEqual(clips.clip_plan(gone, None, self.protocol)[1], ["0:1"])
        lost = {**done, "goals": {"0:1": {"t": None, "src": None}}}   # разбор поправили: секунды у гола нет
        self.assertEqual(clips.clip_plan(lost, None, self.protocol), ([], ["0:1"]))

    def test_two_witnesses(self):
        """ADR-033, раздел 4: клип — только когда точную секунду подтверждают два независимых свидетеля."""
        one = {**self.game, "run": {}}                                   # часы встали, хода часов нет — один
        self.assertEqual(clips.clip_witnesses(one, None), {})
        self.assertEqual(clips.clip_witnesses(self.game, None), {"0:1": (2600, "clock", "board+run")})
        run = {**one, "goals": {"0:1": {"t": 2600, "src": "run"}}}      # часы встали там, где по протоколу
        self.assertEqual(clips.clip_witnesses(run, None)["0:1"][2], "clock+run")
        admin = {"video": VIDEO, "anchors": {"0:1": 2590}}
        checked = {**one, "checks": {"0:1": {"t": 2590, "status": "ok"}}}
        self.assertEqual(clips.clip_witnesses(checked, admin)["0:1"], (2590, "admin", "admin+board"))
        unknown = {**one, "checks": {"0:1": {"t": 2590, "status": "unknown"}}}
        self.assertEqual(clips.clip_witnesses(unknown, admin), {})       # нечем проверить — один человек
        seen = {**admin, "confirm": {"0:1": [2590]}}                     # 30 с посмотрел другой — «✅ Гол виден»
        self.assertEqual(clips.clip_witnesses(unknown, seen)["0:1"][2], "seen")
        self.assertEqual(clips.clip_witnesses(one, {"video": VIDEO, "confirm": {"0:1": [2600]}})["0:1"][2], "seen")
        # «✅» от того, кто сам поставил эту секунду, в confirm не попадает (marks.own_confirm) — тест в test_marks

    def test_objection_takes_clip_down(self):
        """Свидетель возразил уже выложенному клипу — клип снимается сразу (ADR-033, раздел 4)."""
        done = {**self.game, "clips": {"0:1": {"t": 2600, "team": "away"}}}
        self.assertEqual(clips.clip_plan(done, None, self.protocol), ([], []))
        objected = {"video": VIDEO, "reject": {"0:1": [2600]}}           # «⏪ Гол раньше» под 30 с гола
        self.assertEqual(clips.clip_plan(done, objected, self.protocol), ([], ["0:1"]))
        self.assertNotIn("0:1", clips.goal_seconds(done, objected))
        seen = {"video": VIDEO, "confirm": {"0:1": [2600]}, "reject": {"0:1": [2610]}}   # возражали другой секунде
        self.assertEqual(clips.clip_plan(done, seen, self.protocol), ([], []))

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
                    mock.patch.object(clips.pc, "font_file", return_value="font.ttf"), mock.patch.object(clips, "CUT", True):
                self.assertEqual(clips.cut_pass(store, league, {}, bucket, cut=cut, stream=stream), 1)
        c = store["games"][KEY]["clips"]["0:1"]
        self.assertEqual((c["t"], c["team"], c["dur"]), (2600, "away", 30.0))
        self.assertEqual(c["mp4"], "https://s3.twcstorage.ru/rhl-clips/clips/2026-10-04/tverichi_metallurg/0-1-2600.mp4")
        self.assertEqual([x.args[0] for x in bucket.put.call_args_list],
                         ["clips/2026-10-04/tverichi_metallurg/0-1-2600.mp4", "clips/2026-10-04/tverichi_metallurg/0-1-2600.jpg"])

    def test_no_keys_no_cutting(self):
        self.assertEqual(clips.cut_pass({"games": {KEY: dict(self.game)}}, None, {}, mock.Mock(ok=False)), 0)

    def test_pause_is_now_a_switch(self):
        """ADR-030, дополнение 07.10: режем по умолчанию, пауза — только явный CLIPS_CUT=off."""
        self.assertTrue(clips.cut_on(None))
        self.assertTrue(clips.cut_on(""))
        self.assertTrue(clips.cut_on("on"))
        self.assertFalse(clips.cut_on("off"))
        self.assertFalse(clips.cut_on(" OFF\n"))

    def test_paused_no_cutting(self):
        """Нарезка на паузе (дополнение 06.10, ночь): ключи есть, секунда и протокол есть — клип не режется."""
        bucket, cut = mock.Mock(ok=True), mock.Mock()
        league = {"games": [{"date": "2026-10-04", "home": "tverichi", "away": "metallurg",
                             "goals": list(self.protocol.values())}]}
        with mock.patch.object(clips, "CUT", False):
            self.assertEqual(clips.cut_pass({"games": {KEY: dict(self.game)}}, league, {}, bucket, cut=cut), 0)
        cut.assert_not_called()
        bucket.put.assert_not_called()

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


class Wipe(unittest.TestCase):
    """Дополнение 06.10, ночь: все клипы стираются один раз — по clips.json и всё под clips/ в бакете, с копией."""

    def store(self):
        return {"games": {KEY: {"video": VIDEO, "goals": {}, "clips": {
                    "0:1": {"t": 2600, "files": ["clips/a/0-1-2600.mp4", "clips/a/0-1-2600.jpg"]}}},
                          "2026-10-05|kaluga|dinamo-576": {"video": VIDEO, "goals": {}}}}

    def test_wipes_known_and_listed_files_once(self):
        bucket = mock.Mock(ok=True)
        bucket.list.return_value = ["clips/a/0-1-2600.mp4", "clips/b/orphan-1.mp4"]   # выпавший из clips.json
        store = self.store()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(clips.wipe(store, bucket, "m1", Path(tmp)), 3)
            backup = json.loads((Path(tmp) / "clips.before-wipe-m1.json").read_text(encoding="utf-8"))
            on_disk = json.loads((Path(tmp) / "clips.json").read_text(encoding="utf-8"))
            self.assertIsNone(clips.wipe(store, bucket, "m1", Path(tmp)))   # метка стоит — второй раз не стирает
        self.assertEqual(sorted(c.args[0] for c in bucket.delete.call_args_list),
                         ["clips/a/0-1-2600.jpg", "clips/a/0-1-2600.mp4", "clips/b/orphan-1.mp4"])
        bucket.list.assert_called_once_with("clips/")
        self.assertIn("clips", backup["games"][KEY])           # копия — до стирания
        self.assertNotIn("clips", on_disk["games"][KEY])       # у матча клипов больше нет, разбор табло — на месте
        self.assertEqual(on_disk["games"][KEY]["video"], VIDEO)
        self.assertEqual(on_disk["wiped"], "m1")

    def test_failed_deletes_retry_next_pass(self):
        bucket = mock.Mock(ok=True)
        bucket.list.side_effect = OSError("нет сети")   # не перечислил — стираем хотя бы известное
        bucket.delete.side_effect = lambda name: (_ for _ in ()).throw(OSError("503")) if name.endswith(".jpg") else None
        store = self.store()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(clips.wipe(store, bucket, "m1", Path(tmp)), 1)
            self.assertEqual(store["wipe_left"], ["clips/a/0-1-2600.jpg"])
            self.assertNotIn("wiped", store)
            bucket.delete.side_effect = None
            bucket.delete.reset_mock()
            self.assertEqual(clips.wipe(store, bucket, "m1", Path(tmp)), 1)   # следующий проход — оставшееся
        bucket.delete.assert_called_once_with("clips/a/0-1-2600.jpg")
        self.assertNotIn("wipe_left", store)
        self.assertEqual(store["wiped"], "m1")

    def test_no_keys_nothing_to_wipe_with(self):
        store = self.store()
        self.assertIsNone(clips.wipe(store, mock.Mock(ok=False), "m1"))
        self.assertIn("clips", store["games"][KEY])

    def test_parse_bucket_listing(self):
        import s3
        ns = 'xmlns="http://s3.amazonaws.com/doc/2006-03-01/"'
        page = (f'<ListBucketResult {ns}><IsTruncated>true</IsTruncated><Contents><Key>clips/a.mp4</Key></Contents>'
                f'<Contents><Key>clips/a.jpg</Key></Contents><NextContinuationToken>tok/1=</NextContinuationToken>'
                f'</ListBucketResult>').encode()
        self.assertEqual(s3.parse_list(page), (["clips/a.mp4", "clips/a.jpg"], "tok/1="))
        last = b"<ListBucketResult><IsTruncated>false</IsTruncated></ListBucketResult>"
        self.assertEqual(s3.parse_list(last), ([], None))


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

    def test_all_exact_still_checked_for_witnesses(self):
        """ADR-033, раздел 4: все голы точные и людей нет — счёт хода всё равно сверяет их парами: гол табло,
        подтверждённый ходом часов, — второй свидетель для клипа. Раньше такой матч не пересчитывали вовсе."""
        self.store["games"][KEY]["goals"]["0:2"] = {"t": 659, "src": "clock", "team": "away", "change": 700}
        n, disk = self.run_pass()
        self.assertEqual(n, 1)
        game = disk["games"][KEY]
        self.assertEqual(game["run"]["confirmed"], ["0:1", "0:2"])
        self.assertEqual(sorted(clips.clip_witnesses(game, None)), ["0:1", "0:2"])


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

    def test_waiting_recording_is_pending(self):
        store = {"games": {KEY: {"video": VIDEO, "status": "wait", "goals": {}}}}
        self.assertEqual(clips.coverage(store, LEAGUE, {}, date(2026, 10, 5))[KEY]["why"], "pending")


class Waiting(unittest.TestCase):
    """06.10: «Белгород» и «Дизелист» разобраны во время эфира — VK ещё не знал длину записи, и кадр табло для
    разметки вышел из заставки до матча. Свежий матч без длины записи ждёт, а не разбирается."""
    now = datetime(2026, 10, 6, 21, 30, tzinfo=TZ)

    def test_fresh_recording_without_length_waits(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "WORK", Path(tmp) / "w"), \
                mock.patch.object(clips, "GRIDS", Path(tmp) / "g"), \
                mock.patch.object(clips, "now_msk", return_value=self.now), \
                mock.patch.object(clips, "stream", return_value=("http://x", {}, None)), \
                mock.patch.object(clips.sb, "grid_sheet") as grid:
            got = clips.scan_match("2026-10-06|polet|sokol", "https://vk.com/video-9_1", {}, [], "league")
            self.assertEqual(got, {"status": "wait", "goals": {}})
            grid.assert_not_called()                                         # кадра табло из эфира нет
            got = clips.scan_match("2026-10-04|polet|sokol", "https://vk.com/video-9_2", {}, [], "league")
            self.assertEqual(got["status"], "no_board")                      # через сутки длины может и не быть
            grid.assert_called_once()

    def test_wait_is_not_a_try_and_asked_again_later(self):
        one = {"games": LEAGUE["games"][:1]}
        scan = mock.Mock(return_value={"status": "wait", "goals": {}})
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "LIVE_DIR", Path(tmp)), \
                mock.patch.object(clips, "now_msk", return_value=self.now):
            store = {}
            for _ in range(clips.TRIES + 1):                                 # ждать можно сколько угодно раз
                clips.run_pass(store, one, {}, self.now, scan=scan)
                store["games"][KEY]["scanned"] = "2026-10-06T20:00:00+03:00"
            self.assertEqual((store["games"][KEY]["status"], store["games"][KEY]["tries"]), ("wait", 0))
            store["games"][KEY]["scanned"] = self.now.isoformat()
            self.assertEqual(clips.pending(one, {}, store["games"], self.now.date(), self.now), [])   # только что
            later = self.now + timedelta(seconds=clips.WAIT_EVERY)
            self.assertEqual(clips.pending(one, {}, store["games"], later.date(), later), [(KEY, VIDEO)])

    def test_wait_keeps_previous_scan_of_same_video(self):
        """Повторный разбор того же ролика (табло разметили, разбор поменялся), а VK длину не назвал — прежние голы
        табло остаются, а не стираются до следующего разбора."""
        one = {"games": LEAGUE["games"][:1]}
        goals = {"0:1": {"t": 2600, "src": "clock", "team": "away"}}
        store = {"games": {KEY: {"video": VIDEO, "status": "no_board", "v": clips.VERSION, "tries": 1, "length": 9000,
                                 "goals": goals, "rejected": {"0:2": "цифра не та"}}}}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "LIVE_DIR", Path(tmp)), \
                mock.patch.object(clips, "now_msk", return_value=self.now):
            clips.run_pass(store, one, {}, self.now, scan=mock.Mock(return_value={"status": "wait", "goals": {}}))
        g = store["games"][KEY]
        self.assertEqual((g["status"], g["goals"], g["rejected"], g["length"], g["tries"]),
                         ("wait", goals, {"0:2": "цифра не та"}, 9000, 1))

    def test_live_stream_has_no_length(self):
        """Эфир ещё идёт — длины нет, даже если VK её назвал: записи целиком ещё нет."""
        def ydl(info):
            box = mock.MagicMock()
            box.return_value.__enter__.return_value.extract_info.return_value = info
            return SimpleNamespace(YoutubeDL=box)
        base = {"url": "http://x", "http_headers": {}, "duration": 5400}
        with mock.patch.dict(sys.modules, {"yt_dlp": ydl({**base, "is_live": True})}):
            self.assertIsNone(sb.stream_of("https://vk.com/video-1_1")[2])
        with mock.patch.dict(sys.modules, {"yt_dlp": ydl(base)}):
            self.assertEqual(sb.stream_of("https://vk.com/video-1_1")[2], 5400)


class MarkChecks(unittest.TestCase):
    """ADR-033: вердикт по отметкам людей в clips.json (`checks`); спорный гол — без клипа и без точной секунды."""

    def test_checks_from_change_and_dispute(self):
        game = {"video": VIDEO, "status": "ok", "goals": {"0:1": {"t": 2600, "src": "clock", "change": 2620},
                                                          "0:2": {"t": None, "change": 2969}}}
        admin = {"video": VIDEO, "anchors": {"0:1": 2590, "0:2": 3100}}
        protocol = {"0:1": {"period": "1", "time": "05:00"}, "0:2": {"period": "1", "time": "09:00"}}
        got = clips.mark_checks(game, admin, protocol, "нет-разметки")
        self.assertEqual(got["0:1"]["status"], "ok")
        self.assertEqual(got["0:2"]["status"], "conflict")                    # счёт сменился раньше отметки
        self.assertEqual(clips.mark_checks(game, admin, protocol, "tverichi")["0:1"]["status"], "ok")
        game["checks"] = got
        self.assertEqual(clips.goal_seconds(game, admin), {"0:1": (2590, "admin")})   # у спорного — ничего
        self.assertEqual(clips.mark_checks(game, {**admin, "video": "https://vk.com/video-9_9"}, protocol), {})

    def test_frames_verdict_used_for_same_second(self):
        game = {"video": VIDEO, "status": "ok", "goals": {},
                "run": {"marks": {"0:1": {"t": 2590, "status": "conflict", "for": [], "against": ["часы идут"]}}}}
        admin = {"video": VIDEO, "anchors": {"0:1": 2590}}
        self.assertEqual(clips.mark_checks(game, admin, {}, "tverichi")["0:1"]["against"], ["часы идут"])
        admin["anchors"]["0:1"] = 2700                                         # новая отметка — проверка впереди
        self.assertEqual(clips.mark_checks(game, admin, {}, "tverichi")["0:1"]["status"], "pending")

    def test_pass_writes_checks(self):
        store = {"games": {KEY: {"video": VIDEO, "status": "ok", "goals": {"0:1": {"change": 2620}}}}}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "LIVE_DIR", Path(tmp)):
            self.assertTrue(clips.checks_pass(store, None, {KEY: {"video": VIDEO, "anchors": {"0:1": 2700}}}))
            self.assertFalse(clips.checks_pass(store, None, {KEY: {"video": VIDEO, "anchors": {"0:1": 2700}}}))
            saved = json.loads((Path(tmp) / "clips.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["games"][KEY]["checks"]["0:1"]["status"], "conflict")

    def test_replay_link_in_dispute_is_approximate(self):
        entry = {
            "video": VIDEO, "anchors": {"0:1": 2700},
            "goals": [{"score": "0:1", "team": "away", "t": 2690, "exact": True}]}
        board = {"video": VIDEO, "goals": {"0:1": {"t": 2600, "src": "clock", "change": 2620, "team": "away"}},
                 "checks": {"0:1": {"t": 2700, "status": "conflict"}}}
        g = replay.with_board(entry, board)["goals"][0]   # ни отметка, ни секунда табло — примерно по смене счёта
        self.assertEqual((g["exact"], g["src"], g["t"]), (False, "change", 2620 - replay.CHANGE_LEAD))
        board["goals"]["0:1"].pop("change")                                    # смены счёта нет — с отметки, но «≈»
        g = replay.with_board(entry, board)["goals"][0]
        self.assertEqual((g["exact"], g["src"], g["t"]), (False, "dispute", 2690))
        board["checks"]["0:1"]["t"] = 2650                                     # проверяли другую отметку — спора нет
        self.assertEqual(replay.disputed(entry, board), set())


class Gone(unittest.TestCase):
    """Запись удалили из VK (этап 0.3 плана): повторов по ней нет, админам — тревога."""
    now = datetime(2026, 10, 5, 12, 0, tzinfo=TZ)

    def test_tells_deleted_from_refused(self):
        for text in ("Video 456239074 was deleted", "Это видео было удалено", "HTTP Error 404: Not Found",
                     "Video has been removed from public access", "видео не существует"):
            self.assertTrue(clips.gone_error(text), text)
        for text in ("HTTP 403: Forbidden", "Video is private", "Видео доступно только для зарегистрированных",
                     "Видео недоступно в вашем регионе", "Unable to download webpage: timed out",
                     "TimeoutError", "ffmpeg вернул 1", "Too Many Requests"):
            self.assertFalse(clips.gone_error(text), text)

    def test_second_refusal_in_a_row_marks_gone(self):
        """Один 404 бывает и от сбоя: «записи больше нет» говорим со второго отказа подряд."""
        scan = mock.Mock(side_effect=clips.VkError("Video 200 was deleted"))
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "LIVE_DIR", Path(tmp)):
            store = {}
            clips.run_pass(store, LEAGUE, {}, self.now, scan=scan)
            self.assertEqual(store["games"][KEY]["status"], "error")
            with self.assertLogs(level="WARNING"):
                clips.run_pass(store, LEAGUE, {}, self.now, scan=scan)
            self.assertEqual((store["games"][KEY]["status"], store["games"][KEY]["tries"]), ("gone", 2))
            # больше не пробуем: ролик тот же
            self.assertNotIn(KEY, dict(clips.pending(LEAGUE, {}, store["games"], date(2026, 10, 5))))

    def test_only_refusals_in_a_row_count(self):
        """Сбой, а потом один 404 — это не удалённая запись: считаем отказы «записи нет» подряд, а не попытки."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "LIVE_DIR", Path(tmp)):
            store = {}
            clips.run_pass(store, LEAGUE, {}, self.now, scan=mock.Mock(side_effect=clips.VkError("timed out")))
            self.assertEqual(store["games"][KEY].get("gone_tries"), None)
            clips.run_pass(store, LEAGUE, {}, self.now, scan=mock.Mock(side_effect=clips.VkError("HTTP Error 404")))
            self.assertEqual((store["games"][KEY]["status"], store["games"][KEY]["tries"],
                              store["games"][KEY]["gone_tries"]), ("error", 2, 1))
            # отказ «записи нет», потом сбой — счёт подряд обнуляется
            clips.run_pass(store, LEAGUE, {}, self.now, scan=mock.Mock(side_effect=clips.VkError("ffmpeg упал")))
            self.assertNotIn("gone_tries", store["games"][KEY])
            self.assertEqual(store["games"][KEY]["status"], "error")

    def test_counted_apart_from_vk_refusals(self):
        """Новый yt-dlp тут не поможет — счётчик «VK не отдал ни одной записи» не трогаем."""
        import admin
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "LIVE_DIR", Path(tmp)):
            t = admin.Tracker("clips", path=Path(tmp) / "s.json", clock=lambda: self.now)
            scan = mock.Mock(side_effect=clips.VkError("видео удалено"))
            store = {}
            clips.run_pass(store, LEAGUE, {}, self.now, scan=scan, track=t)
            with self.assertLogs(level="WARNING"):
                clips.run_pass(store, LEAGUE, {}, self.now, scan=scan, track=t)
            self.assertEqual(t.today(), {"vk_gone": 4})
            self.assertLessEqual(clips.GONE_MAX, admin.GONE_SHOW)   # иначе тревога потеряет причину как «починилось»
            got = json.loads((Path(tmp) / "s.json").read_text(encoding="utf-8"))["info"]["gone"]
            self.assertEqual([e["key"] for e in got], [KEY, "2026-10-04|rostov|krasnodar"])
            self.assertEqual(got[0]["video"], "https://vk.com/video-100_200")
            self.assertEqual(got[0]["title"], "04.10 tverichi — metallurg")   # без teams в league.json — ключом
        named = {**LEAGUE, "teams": [{"id": "tverichi", "name": "Тверичи"}, {"id": "metallurg", "name": "Металлург"}]}
        self.assertEqual(clips.match_title(named, KEY), "04.10 Тверичи — Металлург")

    def test_no_replay_and_no_watch_link(self):
        """Ссылка на удалённый ролик никуда не ведёт: ни «Повтора» у гола, ни кнопки «Смотреть»."""
        entry = {"video": VIDEO, "anchors": {"0:1": 2700},
                 "goals": [{"score": "0:1", "team": "away", "t": 2690, "exact": True}]}
        board = {"video": VIDEO, "status": "gone", "goals": {"0:1": {"t": 2600, "src": "clock", "team": "away"}}}
        self.assertIsNone(replay.with_board(entry, board))
        self.assertIsNone(replay.with_board(None, board))
        other = {**entry, "video": "https://vk.com/video-7_8"}      # админ прислал другую запись — она жива
        self.assertEqual(replay.with_board(other, board), other)
        # человек отметил гол уже после разбора: значит запись у него открывается — его опоры главнее (ADR-033)
        fresh = {**entry, "updated": "2026-10-05T13:00:00+03:00"}
        dated = {**board, "scanned": "2026-10-05T12:00:00+03:00"}
        self.assertEqual(replay.with_board(fresh, dated)["goals"][0]["t"], 2690)
        self.assertIsNone(replay.with_board({**fresh, "updated": "2026-10-05T11:00:00+03:00"}, dated))
        games = [{"date": "2026-10-04", "home": "tverichi", "away": "metallurg", "id": "m1",
                  "goals": [{"score": "0:1", "team": "away", "period": "1"}],
                  "watch": [{"src": "rhl.fhr.ru", "url": "https://vkvideo.ru/video-100_200"},
                            {"src": "t.me/club", "url": "https://vk.com/video-7_8"}]}]
        self.assertEqual(b.drop_gone(games, {KEY: board}), 1)
        self.assertEqual([w["url"] for w in games[0]["watch"]], ["https://vk.com/video-7_8"])
        self.assertEqual(b.apply_replays(games, {KEY: entry}, {KEY: board}), 0)
        self.assertNotIn("replay", games[0]["goals"][0])

    def test_parsed_recording_rechecked_later(self):
        """Разобранную запись VK может удалить потом: разбор её больше не трогает, поэтому спрашиваем метаданные."""
        store = {"games": {KEY: {"video": VIDEO, "status": "ok", "tries": 1, "v": clips.VERSION,
                                 "goals": {"0:1": {"t": 10}}, "scanned": "2026-10-04T23:00:00+03:00"}}}
        e = store["games"][KEY]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "LIVE_DIR", Path(tmp)):
            # разбор такой матч не берёт
            self.assertNotIn(KEY, dict(clips.pending(LEAGUE, {}, store["games"], date(2026, 10, 5))))
            ok = mock.Mock(return_value=("url", {}, 3600))
            later = self.now + timedelta(hours=1)   # между проверками одной записи проходит время (alive_due)
            self.assertEqual(clips.alive_pass(store, self.now, check=ok), 1)
            self.assertEqual((ok.call_args.args[0], e["status"]), (VIDEO, "ok"))
            self.assertEqual(clips.alive_pass(store, self.now, check=ok), 0)   # только что спрашивали
            # сбой проверки записи не хоронит
            clips.alive_pass(store, later, check=mock.Mock(side_effect=clips.VkError("timed out")))
            self.assertEqual((e["status"], e.get("gone_tries")), ("ok", None))
            gone = mock.Mock(side_effect=clips.VkError("Video was deleted"))
            clips.alive_pass(store, later + timedelta(hours=1), check=gone)
            self.assertEqual((e["status"], e["gone_tries"]), ("ok", 1))   # один отказ — ещё не приговор
            with self.assertLogs(level="WARNING"):   # а второй подряд — приговор, и сразу, не через час
                clips.alive_pass(store, self.now, check=gone)
            self.assertEqual((e["status"], e["goals"]), ("gone", {"0:1": {"t": 10}}))
            self.assertEqual(clips.alive_pass(store, self.now, check=gone), 0)   # уже знаем — не спрашиваем
            # вердикт свежее отметок админа, сделанных до него: мёртвых ссылок у матча не остаётся
            self.assertEqual(e["gone_at"], "2026-10-05T12:00:00+03:00")
            entry = {"video": VIDEO, "anchors": {"0:1": 100}, "updated": "2026-10-05T10:00:00+03:00",
                     "goals": [{"score": "0:1", "team": "away", "t": 90, "exact": True}]}
            self.assertIsNone(replay.with_board(entry, e))
            self.assertEqual(clips.coverage({"games": {KEY: e}}, LEAGUE, {KEY: entry},
                                            date(2026, 10, 5))[KEY]["why"], "gone")
            # а отметка после вердикта значит, что у человека запись открывается: его слово главнее
            later = {**entry, "updated": "2026-10-05T12:00:01+03:00"}
            self.assertEqual(replay.with_board(later, e)["goals"][0]["t"], 90)
            saved = json.loads((Path(tmp) / "clips.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["games"][KEY]["status"], "gone")

    def test_refused_once_goes_first_in_queue(self):
        """Подтверждение не должно ждать очереди за всем сезоном: иначе мёртвый «Повтор» живёт лишний час."""
        old = {"video": "https://vk.com/video-9_9", "status": "ok", "goals": {},
               "scanned": "2026-10-03T12:00:00+03:00"}
        store = {"games": {f"2026-10-03|a{i}|b": dict(old) for i in range(5)}}
        store["games"][KEY] = {"video": VIDEO, "status": "ok", "goals": {}, "gone_tries": 1,
                               "alive": "2026-10-07T11:00:00+03:00"}   # проверяли позже всех, но VK уже отказал
        check = mock.Mock(side_effect=clips.VkError("видео удалено"))
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "LIVE_DIR", Path(tmp)), \
                self.assertLogs(level="WARNING"):
            clips.alive_pass(store, self.now, check=check)
        self.assertEqual(check.call_args_list[0].args[0], VIDEO)
        self.assertEqual(store["games"][KEY]["status"], "gone")

    def test_recheck_not_too_often_for_old_matches(self):
        now = datetime(2026, 10, 20, 12, 0, tzinfo=TZ)
        e = {"video": VIDEO, "status": "ok", "goals": {}, "scanned": "2026-10-05T12:00:00+03:00",
             "alive": (now - timedelta(hours=1)).isoformat(timespec="seconds")}
        self.assertFalse(clips.alive_due(e, KEY, now))
        self.assertTrue(clips.alive_due({**e, "alive": (now - timedelta(hours=7)).isoformat()}, KEY, now))
        self.assertTrue(clips.alive_due(e, "2026-10-20|a|b", now))       # матч сегодня — чаще, но не каждый проход
        soon = {**e, "alive": (now - timedelta(minutes=10)).isoformat(timespec="seconds")}
        self.assertFalse(clips.alive_due(soon, "2026-10-20|a|b", now))
        self.assertTrue(clips.alive_due({**e, "alive": "2026-10-20 10:00:00"}, KEY, now))   # время без пояса — спросим
        self.assertTrue(clips.alive_due({**e, "gone_tries": 1}, KEY, now))   # VK уже сказал «нет» — сразу
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "LIVE_DIR", Path(tmp)):
            store = {"games": {KEY: dict(e), "2026-10-05|kaluga|dinamo-576": {"video": "https://vk.com/video-3_4",
                                                                              "status": "wait", "goals": {}}}}
            self.assertEqual(clips.alive_pass(store, now, check=mock.Mock()), 0)   # нечего и ждущую не трогаем

    def test_coverage_says_why(self):
        store = {"games": {KEY: {"video": VIDEO, "status": "gone", "tries": 2, "goals": {},
                                 "error": "VkError: видео удалено"}}}
        cov = clips.coverage(store, LEAGUE, {}, date(2026, 10, 5))[KEY]
        self.assertEqual((cov["why"], cov["replays"]), ("gone", 0))
        self.assertIn("gone", clips.WHY)

class Prepared(unittest.TestCase):
    """ADR-036, раздел 2: после прохода — видео всего, что ждёт человека, служба cuts режет заранее."""
    now = datetime(2026, 10, 5, 21, 0, tzinfo=TZ)

    def test_what_waits_for_a_human(self):
        game = {"video": VIDEO, "status": "ok", "length": 9000, "goals": {
            "1:0": {"t": 2600, "src": "clock", "change": 2620},     # табло, без отметки — один свидетель
            "1:1": {"t": 3500, "src": "clock", "change": 3520},     # отметка сошлась — два свидетеля
            "2:1": {"t": None, "change": 4969},                     # спор: отметка против смены счёта
            "2:2": {"t": 5600, "src": "board", "change": 5640},     # спор: отметка против секунды табло
            "3:2": {"t": None, "change": 6000},                     # без секунды — превью, не здесь
            "3:3": {"t": 7000, "src": "clock", "off": "absent"}},   # нет в записи
            "checks": {"1:1": {"t": 3495, "status": "ok"}, "2:1": {"t": 5100, "status": "conflict"},
                       "2:2": {"t": 5700, "status": "conflict"}, "4:3": {"t": 8000, "status": "unknown"},
                       "4:4": {"t": 8500, "status": "pending"}}}
        admin = {"video": "https://vkvideo.ru/video-100_200", "absent": ["3:3"],
                 "anchors": {"1:1": 3495, "2:1": 5100, "2:2": 5700, "4:3": 8000, "4:4": 8500}}
        got = clips.cut_wants(game, admin)
        self.assertEqual(sorted(got), sorted([
            (2580, 30, "1:0", "review"),
            (5080, 30, "2:1", "review"), (4849, 125, "2:1", "preview"),     # обе версии спора
            (5680, 30, "2:2", "review"), (5580, 30, "2:2", "review"),
            (7980, 30, "4:3", "review")]))                                    # отметку нечем проверить
        other = {**admin, "video": "https://vk.com/video-9_9"}               # отметки к другому ролику
        self.assertEqual(clips.cut_wants(game, other), [(2580, 30, "1:0", "review"), (3480, 30, "1:1", "review"),
                                                         (5580, 30, "2:2", "review")])

    def test_two_witnesses_wait_only_for_the_clip(self):
        """1.3 плана: у гола два свидетеля — людям смотреть его не надо, окно в очередь cuts не ставим."""
        game = {"video": VIDEO, "status": "ok", "length": 9000, "run": {"confirmed": ["2:0"]}, "goals": {
            "1:0": {"t": 2600, "src": "run", "change": 2620},       # часы встали там, где по протоколу — двое
            "2:0": {"t": 3500, "src": "clock", "change": 3520},     # табло и ход часов сошлись — двое
            "3:0": {"t": 4400, "src": "clock", "change": 4420}}}    # табло одно — свидетель один
        self.assertEqual(clips.cut_wants(game, None), [(4380, 30, "3:0", "review")])
        seen = {"video": VIDEO, "confirm": {"3:0": [4400]}}          # человек посмотрел 30 с: «✅ Гол виден»
        self.assertEqual(clips.cut_wants(game, seen), [])

    def test_recent_matches_only_once(self):
        goals = {"1:0": {"t": 2600, "src": "clock", "change": 2620}}
        store = {"games": {
            "2026-10-05|tverichi|metallurg": {"video": VIDEO, "status": "ok", "goals": goals},
            "2026-10-03|rostov|krasnodar": {"video": "https://vk.com/video-1_2", "status": "ok", "goals": goals},
            "2026-10-02|kaluga|dinamo-576": {"video": "https://vk.com/video-3_4", "status": "ok", "goals": goals},
            "2026-10-04|sokol|proton": {"video": "https://vk.com/video-5_6", "status": "ok", "src": "club",
                                        "goals": goals},
            "2026-10-04|proton|kristall": {"video": "https://vk.com/video-7_8", "status": "gone", "goals": goals}}}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "STATE_DB", Path(tmp) / "state.db"), \
                mock.patch.object(clips, "_jobs", None):
            self.assertEqual(clips.prepare_cuts(store, {}, self.now), 2)
            self.assertEqual(clips.prepare_cuts(store, {}, self.now), 2)
            jobs = clips.cut_jobs()
            rows = [jobs.take(self.now) for _ in range(3)]
            jobs.conn.close()
        self.assertEqual([r["match"] for r in rows[:2]], ["2026-10-05|tverichi|metallurg", "2026-10-03|rostov|krasnodar"])
        self.assertEqual({(r["start"], r["len"], r["kind"], r["prio"]) for r in rows[:2]},
                         {(2580, 30, "review", clips.cutjobs.PREP)})
        self.assertIsNone(rows[2])   # каждое окно — одно задание, повторная просьба не множит

    def test_dead_preview_asked_again(self):
        """Ревью PR #138: превью, у которого кончились попытки, служба просит снова каждый проход — через
        cutjobs.REVIVE оно пойдёт на новый круг. Готовое превью не трогаем: бот не должен прислать его второй раз."""
        key = "2026-10-05|tverichi|metallurg"
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "STATE_DB", Path(tmp) / "state.db"), \
                mock.patch.object(clips, "_jobs", None):
            jobs = clips.cut_jobs()
            dead = jobs.want(self.now, VIDEO, 6749, 125, "preview", prio=clips.cutjobs.SEND)
            jobs.fail(dead, self.now, "HTTP Error 403", final=True)
            ready = jobs.want(self.now, VIDEO, 7749, 125, "preview", prio=clips.cutjobs.SEND)
            jobs.take(self.now)
            jobs.done(ready, self.now, "media/cuts/x.mp4", 854, 480, 125)   # файла нет: «готово», но пропал
            store = {"games": {key: {"video": VIDEO, "status": "ok", "goals": {
                "0:1": {"t": None, "change": 6869, "ask": {"from": 6749, "len": 125, "job": dead}},
                "0:2": {"t": None, "change": 7869, "ask": {"from": 7749, "len": 125, "job": ready}}}}}}
            later = self.now + clips.cutjobs.REVIVE
            self.assertEqual(clips.prepare_cuts(store, {}, later), 1)
            got = (jobs.get(dead)["status"], jobs.get(ready)["status"])
            answered = {key: {"video": VIDEO, "anchors": {"0:1": 6800}}}
            jobs.fail(dead, later, "снова", final=True)
            self.assertEqual(clips.prepare_cuts(store, answered, later + clips.cutjobs.REVIVE), 0)   # уже ответили
            jobs.conn.close()
        self.assertEqual(got, ("queued", "done"))


if __name__ == "__main__":
    unittest.main()


class Watchdogs(unittest.TestCase):
    """Сторожа службы (ADR-034): инварианты прохода и «канарейка» yt-dlp."""
    now = datetime(2026, 10, 8, 12, 0, tzinfo=TZ)
    league = {"games": [{**LEAGUE["games"][0], "goals": [
        {"score": "0:1", "team": "away", "period": "1"}, {"score": "0:2", "team": "away", "period": "2"}]}]}

    def test_seconds_out_of_protocol_order(self):
        store = {"games": {KEY: {"video": VIDEO, "status": "ok", "goals": {
            "0:1": {"t": 3000, "src": "clock"}, "0:2": {"t": 2600, "src": "clock"}}}}}
        got = clips.invariants(store, self.league, {}, self.now)
        self.assertEqual([p["key"] for p in got], [f"order:{KEY}"])
        self.assertIn("не в порядке протокола", got[0]["text"])
        store["games"][KEY]["goals"]["0:2"]["t"] = 3600
        self.assertEqual(clips.invariants(store, self.league, {}, self.now), [])

    def test_clip_cut_past_the_scoreboard(self):
        """05.10 клип 4:0 был вырезан на секунде гола 1:0 — именно это и ловим."""
        game = {"video": VIDEO, "status": "ok", "goals": {"0:1": {"t": 2600, "src": "clock", "change": 2620}},
                "clips": {"0:1": {"t": 2600, "team": "away"}}}
        self.assertEqual(clips.invariants({"games": {KEY: game}}, self.league, {}, self.now), [])
        game["clips"]["0:1"]["t"] = 2700                               # позже смены счёта — гола там быть не может
        got = clips.invariants({"games": {KEY: game}}, self.league, {}, self.now)
        self.assertEqual([p["key"] for p in got], [f"clip:{KEY}:0:1"])
        self.assertIn("позже смены счёта", got[0]["text"])
        game["clips"]["0:1"]["t"] = 2200                               # за семь минут до смены — другой гол
        got = clips.invariants({"games": {KEY: game}}, self.league, {}, self.now)
        self.assertIn("раньше смены счёта", got[0]["text"])

    def test_coverage_must_not_drop(self):
        store = {"coverage": {KEY: {"replays": 5, "goals": 5, "why": "ok"}}}
        self.assertEqual(clips.cover_drop(store, self.now), [])        # первый счёт — он и максимум
        self.assertEqual(store["cover_top"], {"day": "2026-10-08", "games": {KEY: [5, 5]}})
        store["coverage"][KEY]["replays"] = 3
        got = clips.cover_drop(store, self.now)
        self.assertEqual([p["key"] for p in got], ["cover"])
        self.assertIn("было 5, стало 3", got[0]["text"])
        self.assertEqual(clips.cover_drop(store, self.now + timedelta(days=1)), [])   # новый день — счёт заново
        store["coverage"][KEY] = {"replays": 0, "goals": 5, "why": "gone"}   # запись удалили — об этом своя тревога
        self.assertEqual(clips.cover_drop(store, self.now + timedelta(days=1)), [])
        self.assertNotIn(KEY, store["cover_top"]["games"])              # вернётся запись — потерей это не будет

    def test_protocol_fix_is_not_a_loss(self):
        """Ревью PR #141: лига отменила гол — голов у матча стало меньше, и повторов тоже. Это не потеря разбора."""
        store = {"coverage": {KEY: {"replays": 5, "goals": 5, "why": "ok"}}}
        self.assertEqual(clips.cover_drop(store, self.now), [])
        store["coverage"][KEY] = {"replays": 4, "goals": 4, "why": "ok"}   # гол отменили, счета сдвинулись
        self.assertEqual(clips.cover_drop(store, self.now), [])
        self.assertEqual(store["cover_top"]["games"][KEY], [4, 4])         # считаем от нового протокола
        store["coverage"][KEY] = {"replays": 2, "goals": 4, "why": "ok"}   # голов столько же, повторов меньше
        self.assertEqual([p["key"] for p in clips.cover_drop(store, self.now)], ["cover"])

    def test_pass_writes_invariants_to_the_pult(self):
        store = {"games": {KEY: {"video": VIDEO, "status": "ok", "goals": {
            "0:1": {"t": 3000, "src": "clock"}, "0:2": {"t": 2600, "src": "clock"}}}}}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "LIVE_DIR", Path(tmp)):
            track = admin.Tracker("clips", path=Path(tmp) / "clips.json", clock=lambda: self.now)
            got = clips.watch_pass(store, self.league, {}, self.now, track=track)
            track.flush()
            said = json.loads((Path(tmp) / "clips.json").read_text(encoding="utf-8"))
        self.assertEqual(len(got), 1)
        self.assertEqual(said["info"]["invariants"][0]["key"], f"order:{KEY}")   # дальше — пульт и тревога админам

    def test_canary_asks_vk_once_a_day(self):
        store = {"games": {KEY: {"video": VIDEO, "status": "ok", "goals": {}},
                           "2026-10-02|sokol|proton": {"video": "https://vk.com/video-5_6", "status": "ok"}}}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "LIVE_DIR", Path(tmp)):
            check = mock.Mock(return_value=("http://x", {}, 7200))
            self.assertIs(clips.canary_pass(store, self.now, check=check), True)
            self.assertEqual(check.call_args.args[0], VIDEO)            # свежая запись сезона
            self.assertIsNone(clips.canary_pass(store, self.now + timedelta(hours=2), check=check))
            self.assertEqual(check.call_count, 1)                       # раз в сутки, не каждый проход
            dead = mock.Mock(side_effect=clips.VkError("DownloadError: Unable to extract player"))
            track = admin.Tracker("clips", path=Path(tmp) / "clips.json", clock=lambda: self.now)
            self.assertIs(clips.canary_pass(store, self.now + timedelta(days=1), check=dead, track=track), False)
            track.flush()
            said = json.loads((Path(tmp) / "clips.json").read_text(encoding="utf-8"))
        self.assertIn("Unable to extract", store["canary"]["error"])
        self.assertEqual(said["info"]["canary"]["ok"], False)
        # ревью PR #141: счётчики дня канарейка не трогает — по ним считается «VK сегодня не отдал ни одной записи»
        self.assertEqual(said["days"], {})

    def test_canary_asks_only_about_a_parsed_recording(self):
        """Ревью PR #141: запись, которая и раньше не скачивалась (приватная, не для этой страны), каждый день
        давала бы одну и ту же тревогу не о том — канарейка спрашивает о разобранной."""
        store = {"games": {
            "2026-10-07|kaluga|dinamo-576": {"video": "https://vk.com/video-7_7", "status": "error", "tries": 3},
            KEY: {"video": VIDEO, "status": "no_board", "goals": {}}}}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "LIVE_DIR", Path(tmp)):
            check = mock.Mock(return_value=("http://x", {}, 7200))
            self.assertIs(clips.canary_pass(store, self.now, check=check), True)
        self.assertEqual(check.call_args.args[0], VIDEO)
        only_bad = {"games": {"2026-10-07|kaluga|dinamo-576": {"video": "https://vk.com/video-7_7",
                                                               "status": "error", "tries": 3}}}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "LIVE_DIR", Path(tmp)):
            self.assertIsNone(clips.canary_pass(only_bad, self.now, check=mock.Mock()))

    def test_canary_counts_a_deleted_recording_as_an_answer(self):
        store = {"games": {KEY: {"video": VIDEO, "status": "ok", "goals": {}}}}
        gone = mock.Mock(side_effect=clips.VkError("VkError: видео удалено"))
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "LIVE_DIR", Path(tmp)):
            self.assertIs(clips.canary_pass(store, self.now, check=gone), True)
        self.assertTrue(store["canary"]["gone"])

    def test_canary_without_recordings(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(clips, "LIVE_DIR", Path(tmp)):
            self.assertIsNone(clips.canary_pass({"games": {}}, self.now, check=mock.Mock()))

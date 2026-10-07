"""Видео гола вместо ссылок VK (ADR-036, этап 1.2): что показать по голу, кнопки, ответ временем в видео, альбомы."""
import asyncio
import json
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
sys.path.insert(0, str(ROOT / "tests"))

import cutjobs  # noqa: E402
from test_replay import GAME, VIDEO  # noqa: E402

TZ = ZoneInfo("Europe/Moscow")
KEY = GAME["key"]
PROTOCOL = [{"score": "1:0", "team": "home", "period": "1", "time": "05:00", "author": "Иванов Иван"},
            {"score": "1:1", "team": "away", "period": "1", "time": "12:00"},
            {"score": "2:1", "team": "home", "period": "2", "time": "25:00"},
            {"score": "2:2", "team": "away", "period": "3", "time": "50:00"}]
BOARD = {"video": VIDEO, "status": "ok", "length": 9000, "goals": {
    "1:0": {"t": 2600, "src": "clock", "change": 2620, "team": "home"},                        # встали часы
    "1:1": {"t": None, "change": 3500, "team": "away", "ask": {"from": 3380, "len": 125, "cand": [40, 70]}},
    "2:1": {"t": None, "change": None, "team": "home"}}}                                       # табло не нашло


class Base(unittest.TestCase):
    def setUp(self):
        import admin
        import bot
        self.bot = bot
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "2026-10-03.json").write_text(json.dumps({"date": "2026-10-03", "games": [GAME]}), encoding="utf-8")
        self.clips(BOARD)
        self.now = datetime(2026, 10, 4, 12, 0, tzinfo=TZ)
        self.track = admin.Tracker("bot", path=self.dir / "bot.json", clock=lambda: self.now)
        for name, value in (("LIVE_DIR", self.dir), ("REPLAYS_FILE", self.dir / "replays.json"),
                            ("STATE_DB", self.dir / "state.db"), ("BASE", self.dir),
                            ("ADMIN_IDS", frozenset({1001})), ("PREVIEW_IDS", frozenset({761})),
                            ("TRACK", self.track), ("CUT_ASK", {}), ("REPLAY_ASK", {}), ("PREVIEW_ASK", {})):
            p = mock.patch.object(bot, name, value)
            p.start()
            self.addCleanup(p.stop)
        sqlite3.connect(self.dir / "state.db").close()

    def clips(self, game: dict) -> None:
        (self.dir / "clips.json").write_text(json.dumps({"games": {KEY: game}}), encoding="utf-8")

    def mark(self, score: str, sec: int, kind: str = "time") -> None:
        self.bot.add_mark(KEY, score, kind, self.now, 1001, "replay", PROTOCOL, video=VIDEO,
                          **({"sec": sec} if kind == "time" else {}))
        self.bot.marks_apply(KEY, GAME, self.now, PROTOCOL)

    def finish(self, job_id: int) -> dict:
        """Служба cuts вырезала задание: файл на месте."""
        jobs = self.bot.cut_store()
        rel = f"{cutjobs.DIR}/{job_id}.mp4"
        (self.dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.dir / rel).write_bytes(b"video")
        jobs.done(job_id, self.now, rel, 854, 480, jobs.get(job_id)["len"])
        return jobs.get(job_id)

    def tg(self):
        bot = mock.Mock()
        bot.send_message = mock.AsyncMock(return_value=mock.Mock(message_id=9))
        bot.send_video = mock.AsyncMock(return_value=mock.Mock(message_id=10))
        bot.send_media_group = mock.AsyncMock(return_value=[])
        bot.delete_message = mock.AsyncMock()
        return bot


class Plan(Base):
    def test_what_is_known_decides_the_video(self):
        exact = self.bot.goal_plan(KEY, "1:0", PROTOCOL)
        self.assertEqual((exact["kind"], exact["t"], exact["src"], exact["windows"][0][:2]),
                         ("exact", 2600, "clock", (2580, 30)))           # ровно окно клипа: 20 до, 10 после
        approx = self.bot.goal_plan(KEY, "1:1", PROTOCOL)
        self.assertEqual((approx["kind"], approx["windows"][0][:2], approx["cand"]),
                         ("approx", (3380, 125), [3420, 3450]))          # окно и моменты — как у превью
        search = self.bot.goal_plan(KEY, "2:1", PROTOCOL)
        # от 1:1 (12:00, ≈3460 записи) до 2:1 (25:00, 2-й период): 13 минут игры с остановками и перерыв
        self.assertEqual((search["kind"], search["windows"][0][:2]), ("search", (5484, 180)))
        self.assertEqual(self.bot.goal_estimate("2:1", {"1:0": 2600}, PROTOCOL), 2600 + round(1200 * 1.3) + 1100)

    def test_dispute_shows_both_versions(self):
        self.mark("1:0", 2700)
        self.clips({**BOARD, "checks": {"1:0": {"t": 2700, "status": "conflict", "against": ["часы идут"]}}})
        plan = self.bot.goal_plan(KEY, "1:0", PROTOCOL)
        self.assertEqual((plan["kind"], [w[:2] for w in plan["windows"]], plan["tb"], plan["why"]),
                         ("dispute", [(2680, 30), (2580, 30)], 2600, "часы идут"))
        kb = self.bot.cut_kb(plan, [5, 6])
        data = [b.callback_data for row in kb.inline_keyboard for b in row]
        self.assertEqual(data, ["cv:5:y:2700", "cv:6:t:2600", "cv:5:s"])
        self.assertEqual(len(self.bot.cut_caption(plan, PROTOCOL)), 2)

    def test_mark_wins_and_absent_or_gone_has_no_video(self):
        self.mark("1:0", 2650)
        plan = self.bot.goal_plan(KEY, "1:0", PROTOCOL)
        self.assertEqual((plan["kind"], plan["t"], plan["src"]), ("exact", 2650, "admin"))
        self.mark("1:1", 0, kind="absent")
        self.assertIsNone(self.bot.goal_plan(KEY, "1:1", PROTOCOL))
        self.clips({**BOARD, "status": "gone"})
        self.assertIsNone(self.bot.goal_plan("2026-10-03|tverichi|metallurg", "2:1", PROTOCOL))

    def test_unmarked_board_searches_from_start(self):
        """Табло клуба не размечено: голов нет, а запись есть — окно поиска от начала записи по протоколу."""
        self.clips({"video": VIDEO, "status": "no_board", "length": 9000, "goals": {}})
        plan = self.bot.goal_plan(KEY, "1:0", PROTOCOL)
        self.assertEqual((plan["kind"], plan["windows"][0][:2]), ("search", (600, 180)))   # 5 мин студии + 6:30
        (self.dir / "clips.json").write_text("{}", encoding="utf-8")
        self.assertIsNone(self.bot.goal_plan(KEY, "1:0", PROTOCOL))                      # записи не знаем
        self.assertEqual(self.bot.goal_plan(KEY, "1:0", PROTOCOL, video=VIDEO)["video"], VIDEO)   # запись лиги

    def test_buttons(self):
        exact = self.bot.cut_kb(self.bot.goal_plan(KEY, "1:0", PROTOCOL), [7])
        self.assertEqual([b.callback_data for row in exact.inline_keyboard for b in row],
                         ["cv:7:y:2600", "cv:7:e", "cv:7:l", "cv:7:n"])
        approx = self.bot.cut_kb(self.bot.goal_plan(KEY, "1:1", PROTOCOL), [8])
        self.assertEqual([(b.text, b.callback_data) for row in approx.inline_keyboard for b in row][:3],
                         [("Гол на 0:40", "cv:8:t:3420"), ("Гол на 1:10", "cv:8:t:3450"), ("Другое время", "cv:8:x")])
        self.assertIn("cv:8:w", [b.callback_data for row in approx.inline_keyboard for b in row])   # табло сбилось

    def test_windows_shared_with_clips(self):
        """Окна у бота и службы clips одни (cutjobs): бот попадает в заготовки, вырезанные заранее."""
        import clips
        self.assertEqual(clips.preview_window(3500, 9000), cutjobs.change_window(3500, 9000))
        self.assertEqual(clips.run_window([100, 140]), cutjobs.run_window([100, 140]))
        self.assertEqual(cutjobs.neighbour(3380, 125, -1, 9000), (3200, 180))
        self.assertEqual(cutjobs.neighbour(3380, 125, 1, 9000), (3505, 180))
        self.assertIsNone(cutjobs.neighbour(0, 180, -1))
        self.assertIsNone(cutjobs.neighbour(8900, 100, 1, 9000))
        self.assertEqual(cutjobs.search_window(8990, 9000), (8820, 180))


class Show(Base):
    def test_ready_video_goes_at_once(self):
        plan = self.bot.goal_plan(KEY, "1:0", PROTOCOL)
        ids = self.bot.cut_jobs_for(plan, self.now)
        self.finish(ids[0])
        bot = self.tg()
        asyncio.run(self.bot.goal_video(bot, 1001, plan, PROTOCOL))
        bot.send_message.assert_not_called()
        args, kw = bot.send_video.call_args
        self.assertEqual(Path(args[1].path), self.dir / cutjobs.DIR / f"{ids[0]}.mp4")
        self.assertEqual((kw["width"], kw["height"], kw["duration"]), (854, 480, 30))
        self.assertIn("встали часы", kw["caption"])
        self.assertEqual(self.bot.CUT_ASK[1001][0], ids[0])                  # время текстом — в этом видео
        job = self.bot.cut_store().get(ids[0])
        self.assertEqual((job["prio"], job["kind"]), (cutjobs.URGENT, "review"))

    def test_wait_then_video_instead_of_placeholder(self):
        plan = self.bot.goal_plan(KEY, "2:1", PROTOCOL)
        bot = self.tg()
        jobs_ = []

        async def service(_):   # пока бот ждёт, служба cuts режет
            self.finish(jobs_[0])

        async def run():
            jobs_.extend(await self.bot.goal_video(bot, 1001, plan, PROTOCOL))
            await asyncio.gather(*self.bot._cut_tasks)

        with mock.patch.object(self.bot.asyncio, "sleep", side_effect=service):
            asyncio.run(run())
        self.assertEqual(bot.send_message.call_args.args[1], "⏳ Режу видео…")
        bot.send_video.assert_awaited_once()
        bot.delete_message.assert_awaited_once_with(1001, 9)
        # соседние окна поиска — заранее: человек, скорее всего, нажмёт «⏪» или «⏩»
        rows = self.bot.cut_store().conn.execute("SELECT start, len, prio FROM cut_jobs ORDER BY id").fetchall()
        self.assertEqual(rows, [(5484, 180, cutjobs.URGENT), (5304, 180, cutjobs.SEND), (5664, 180, cutjobs.SEND)])

    def test_vk_refused_is_text_with_link(self):
        plan = self.bot.goal_plan(KEY, "1:0", PROTOCOL)
        bot = self.tg()
        ids = []

        async def service(_):   # служба cuts пробует — VK отказывает
            self.bot.cut_store().fail(ids[0], self.now, "VK не отдал запись: HTTP Error 403", final=True)

        async def run():
            ids.extend(await self.bot.goal_video(bot, 1001, plan, PROTOCOL))
            await asyncio.gather(*self.bot._cut_tasks)

        with mock.patch.object(self.bot.asyncio, "sleep", side_effect=service):
            asyncio.run(run())
        bot.delete_message.assert_awaited_once_with(1001, 9)                # «⏳ Режу видео…» убрано
        text = bot.send_message.call_args.args[1]
        self.assertIn("Видео не вырезалось: VK не отдал запись", text)
        self.assertIn("vkvideo.ru", text)                                   # тут без ссылки никак
        bot.send_video.assert_not_called()

    def test_all_goals_of_match_as_album(self):
        self.mark("2:1", 5600)
        bot = self.tg()
        for g in ("1:0", "2:1"):
            self.finish(self.bot.cut_jobs_for(self.bot.goal_plan(KEY, g, PROTOCOL), self.now)[0])
        asyncio.run(self.bot.match_videos(bot, 1001, "2026-10-03", 0, GAME, PROTOCOL))
        media = bot.send_media_group.call_args.args[1]
        self.assertEqual(len(media), 2)
        self.assertIn("гол <b>1:0</b>", media[0].caption)
        note, kb = bot.send_message.call_args.args[1], bot.send_message.call_args.kwargs["reply_markup"]
        self.assertIn("1:0, 2:1", note)
        self.assertEqual([b.callback_data for row in kb.inline_keyboard for b in row],
                         ["rp:g:2026-10-03:0:1:0", "rp:g:2026-10-03:0:2:1"])
        text_kb = self.bot.replay_kb("2026-10-03", 0, GAME, self.bot.load_replays()["games"].get(KEY), PROTOCOL)
        self.assertIn("rp:all:2026-10-03:0", [b.callback_data for row in text_kb.inline_keyboard for b in row])


class Answers(Base):
    def press(self, data: str, uid: int = 1001):
        c = mock.Mock(data=data, from_user=mock.Mock(id=uid), message=mock.Mock(chat=mock.Mock(id=uid)))
        c.answer = mock.AsyncMock()
        c.message.answer = mock.AsyncMock()
        c.message.edit_reply_markup = mock.AsyncMock()
        c.bot = self.tg()
        with mock.patch.object(self.bot, "published_league", mock.AsyncMock(return_value=None)):
            asyncio.run(self.bot.cb_cut(c))
        return c

    def job(self, score: str) -> dict:
        plan = self.bot.goal_plan(KEY, score, PROTOCOL)
        return self.bot.cut_store().get(self.bot.cut_jobs_for(plan, self.now)[0])

    def test_moment_button_records_time_and_shows_result(self):
        job = self.job("1:1")
        c = self.press(f"cv:{job['id']}:t:3420")
        self.assertEqual(self.bot.load_replays()["games"][KEY]["anchors"], {"1:1": 3420})
        r = self.bot.goal_marks().of(KEY)[-1]
        self.assertEqual((r["via"], r["sec"], r["who"]), ("video", 3420, 1001))
        self.assertEqual(json.loads(r["seen"]), {"job": job["id"], "from": 3380, "len": 125, "pick": 40})
        c.message.edit_reply_markup.assert_awaited_once()                    # ответ дан — кнопки убраны
        self.assertEqual(c.bot.send_message.call_args.args[1], "⏳ Режу видео…")   # 30 с результата
        res = self.bot.cut_store().get(self.bot.CUT_ASK[1001][0])
        self.assertEqual((res["start"], res["len"]), (3400, 30))

    def test_goal_seen_is_a_confirm_mark(self):
        job = self.job("1:0")
        self.press(f"cv:{job['id']}:y:2600")
        r = self.bot.goal_marks().of(KEY)[-1]
        self.assertEqual((r["kind"], r["sec"], r["via"]), ("confirm", 2600, "video"))
        self.assertEqual(self.bot.goal_marks().state(KEY)["anchors"], {})   # подтверждение — не опора (1.3 плана)
        self.assertEqual(self.bot.mark_word(r), "✅ гол виден на 43:20")

    def test_steps_and_marks(self):
        job = self.job("1:1")
        c = self.press(f"cv:{job['id']}:e")
        prev = self.bot.cut_store().get(self.bot.CUT_ASK[1001][0])
        self.assertEqual((prev["start"], prev["len"]), (3200, 180))
        self.press(f"cv:{job['id']}:w")
        self.assertEqual(self.bot.load_replays()["games"][KEY]["wrong"], ["1:1"])
        self.press(f"cv:{job['id']}:n")
        self.assertEqual(self.bot.load_replays()["games"][KEY]["absent"], ["1:1"])
        self.assertEqual(c.answer.await_count, 1)

    def test_time_in_video_text(self):
        job = self.job("2:1")
        self.press(f"cv:{job['id']}:x")
        m = mock.Mock(text="1:05", chat=mock.Mock(id=1001), from_user=mock.Mock(id=1001))
        m.answer = mock.AsyncMock()
        m.bot = self.tg()
        self.assertTrue(self.bot.cut_waiting(m))
        self.assertFalse(self.bot.replay_waiting(m))
        with mock.patch.object(self.bot, "published_league", mock.AsyncMock(return_value=None)):
            asyncio.run(self.bot.h_cut_time(m))
        self.assertEqual(self.bot.load_replays()["games"][KEY]["anchors"], {"2:1": job["start"] + 65})
        m.text = "9:59"
        self.bot.CUT_ASK[1001] = (job["id"], datetime.now(TZ))
        with mock.patch.object(self.bot, "published_league", mock.AsyncMock(return_value=None)):
            asyncio.run(self.bot.h_cut_time(m))
        self.assertIn("Не понял время", m.answer.call_args.args[0])         # видео — 3 минуты

    def test_replay_opened_later_takes_text(self):
        job = self.job("2:1")
        self.bot.CUT_ASK[1001] = (job["id"], datetime.now(TZ) - timedelta(minutes=2))
        self.bot.REPLAY_ASK[1001] = ("2026-10-03", 0, "", datetime.now(TZ), KEY)
        m = mock.Mock(text="1:05", chat=mock.Mock(id=1001), from_user=mock.Mock(id=1001))
        self.assertFalse(self.bot.cut_waiting(m))
        self.assertTrue(self.bot.replay_waiting(m))

    def test_stale_or_foreign_button(self):
        c = self.press("cv:999:t:10")
        self.assertIn("не действует", c.answer.call_args.args[0])
        job = self.job("1:0")
        c = self.press(f"cv:{job['id']}:y:2600", uid=555)                 # не админ и не помощник
        self.assertEqual(self.bot.goal_marks().of(KEY), [])


class Dispute(Base):
    def test_album_with_buttons(self):
        self.mark("1:0", 2700)
        self.clips({**BOARD, "checks": {"1:0": {"t": 2700, "status": "conflict", "against": ["часы идут"]}}})
        plan = self.bot.goal_plan(KEY, "1:0", PROTOCOL)
        for i in self.bot.cut_jobs_for(plan, self.now, cutjobs.SEND):
            self.finish(i)
        bot = self.tg()
        with mock.patch.object(self.bot, "DISPUTES_FILE", self.dir / "disputes.json"), \
                mock.patch.object(self.bot, "published_league", mock.AsyncMock(return_value=None)), \
                mock.patch.object(self.bot.asyncio, "sleep", mock.AsyncMock()):
            self.assertEqual(asyncio.run(self.bot.dispute_step(bot, self.now)), 1)
        self.assertEqual(len(bot.send_media_group.call_args.args[1]), 2)
        note, kb = bot.send_message.call_args.args[1], bot.send_message.call_args.kwargs["reply_markup"]
        self.assertIn("Спор по голу 1:0", note)
        self.assertNotIn("vkvideo.ru", note)
        texts = [b.text for row in kb.inline_keyboard for b in row]
        self.assertEqual(texts, ["✅ Верно по отметке 45:00", "✅ Верно по табло 43:20", "🔎 Ни то ни другое — искать",
                                 "🛠 Открыть гол"])


if __name__ == "__main__":
    unittest.main()

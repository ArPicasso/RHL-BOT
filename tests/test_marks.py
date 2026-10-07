"""Журнал отметок голов (ADR-033): строки только добавляются, отмена — новой строкой, replays.json — из журнала."""
import asyncio
import json
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
sys.path.insert(0, str(ROOT / "tests"))

import marks  # noqa: E402
from test_replay import GAME, VIDEO  # noqa: E402

TZ = ZoneInfo("Europe/Moscow")
KEY = GAME["key"]
NOW = datetime(2026, 10, 6, 15, 0, tzinfo=TZ)


def store() -> marks.MarksStore:
    return marks.MarksStore(sqlite3.connect(":memory:", isolation_level=None))


class Journal(unittest.TestCase):
    def test_rows_cannot_change_or_vanish(self):
        s = store()
        i = s.add(NOW, KEY, "1:0", "time", role="admin", via="replay", who=1001, video=VIDEO, sec=1800)
        with self.assertRaises(sqlite3.IntegrityError):
            s.conn.execute("DELETE FROM goal_marks WHERE id = ?", (i,))
        with self.assertRaises(sqlite3.IntegrityError):
            s.conn.execute("UPDATE goal_marks SET sec = 1900 WHERE id = ?", (i,))
        with self.assertRaises(sqlite3.IntegrityError):   # стереть id можно, но не заодно с другим полем
            s.conn.execute("UPDATE goal_marks SET who = NULL, sec = 1900 WHERE id = ?", (i,))
        self.assertEqual(s.get(i)["sec"], 1800)
        with self.assertRaises(ValueError):
            s.add(NOW, KEY, "1:0", "guess", role="admin", via="replay")

    def test_forget_keeps_role(self):
        s = store()
        s.add(NOW, KEY, "1:0", "time", role="helper", via="preview", who=761, video=VIDEO, sec=1800)
        s.add(NOW, KEY, "1:1", "time", role="admin", via="replay", who=1001, video=VIDEO, sec=3000)
        self.assertEqual(s.forget(761), 1)
        rows = s.of(KEY)
        self.assertEqual([(r["who"], r["role"]) for r in rows], [(None, "helper"), (1001, "admin")])
        self.assertEqual(s.state(KEY)["anchors"], {"1:0": 1800, "1:1": 3000})   # отметка осталась в силе

    def test_latest_mark_wins_and_revoke_restores_previous(self):
        s = store()
        a = s.add(NOW, KEY, "1:0", "time", role="admin", via="replay", video=VIDEO, sec=3788)   # опечатка 1:03:08
        b = s.add(NOW, KEY, "1:0", "time", role="admin", via="replay", video=VIDEO, sec=4083)   # поправка 1:08:03
        self.assertEqual(s.state(KEY)["anchors"], {"1:0": 4083})
        r = s.revoke(NOW, b, role="admin", via="replay")
        self.assertEqual(s.state(KEY)["anchors"], {"1:0": 3788})   # отозвали поправку — снова прежняя
        self.assertIsNone(s.revoke(NOW, b, role="admin", via="replay"))   # дважды не отзывают
        self.assertIsNone(s.revoke(NOW, r, role="admin", via="replay"))   # кнопкой отзыв не отзывают — время шлют заново
        s.add(NOW, KEY, "1:0", "revoke", role="admin", via="replay", target=r)   # но отмена отмены в журнале работает
        self.assertEqual(s.state(KEY)["anchors"], {"1:0": 4083})
        s.revoke(NOW, a, role="admin", via="replay")
        self.assertEqual(len(s.of(KEY)), 5)                         # всё в истории

    def test_absent_and_time_latest_decides(self):
        s = store()
        s.add(NOW, KEY, "1:0", "time", role="admin", via="replay", video=VIDEO, sec=1800)
        s.add(NOW, KEY, "1:0", "absent", role="admin", via="replay", video=VIDEO)
        s.add(NOW, KEY, "1:1", "wrong", role="helper", via="preview", video=VIDEO)
        st = s.state(KEY)
        self.assertEqual((st["anchors"], st["absent"], st["wrong"]), ({}, ["1:0"], ["1:1"]))
        s.add(NOW, KEY, "1:0", "time", role="admin", via="replay", video=VIDEO, sec=1810)
        self.assertEqual((s.state(KEY)["anchors"], s.state(KEY)["absent"]), ({"1:0": 1810}, []))

    def test_new_video_wins_old_marks_stay_in_history(self):
        s = store()
        s.add(NOW, KEY, "1:0", "time", role="admin", via="replay", video=VIDEO, sec=1800)
        s.add(NOW, KEY, "1:0", "wrong", role="admin", via="replay", video=VIDEO)
        s.add(NOW, KEY, "1:1", "time", role="admin", via="replay", video="https://vk.com/video-1_2", sec=600)
        st = s.state(KEY)
        self.assertEqual((st["video"], st["anchors"], st["wrong"]), ("https://vk.com/video-1_2", {"1:1": 600}, []))
        same = "https://vkvideo.ru/video-1_2"                      # тот же ролик другой ссылкой
        s.add(NOW, KEY, "1:0", "time", role="admin", via="replay", video=same, sec=700)
        self.assertEqual(s.state(KEY), {"video": "https://vk.com/video-1_2", "anchors": {"1:0": 700, "1:1": 600},
                                        "absent": [], "wrong": [], "confirm": {}, "reject": {}})

    def test_revoke_match_and_nothing_left(self):
        s = store()
        s.add(NOW, KEY, "1:0", "time", role="admin", via="replay", video=VIDEO, sec=1800)
        s.add(NOW, KEY, "1:1", "absent", role="admin", via="replay", video=VIDEO)
        self.assertEqual(s.revoke_match(NOW, KEY, role="admin", via="replay"), 2)
        self.assertIsNone(s.state(KEY))
        self.assertEqual(s.revoke_match(NOW, KEY, role="admin", via="replay"), 0)

    def test_import_once(self):
        s = store()
        games = {KEY: {"video": VIDEO, "anchors": {"1:0": 1800, "2:1": 4800}, "absent": ["1:1"], "wrong": ["2:1"],
                       "updated": "2026-10-04T12:00:00+03:00"},
                 "битый": "не словарь"}
        self.assertEqual(s.import_replays(games, NOW), 4)
        self.assertEqual(s.import_replays(games, NOW), 0)            # второй раз не переносит
        rows = s.of(KEY)
        self.assertTrue(all(r["role"] == "import" and r["via"] == "import" and r["who"] is None for r in rows))
        self.assertEqual(rows[0]["at"], "2026-10-04T12:00:00+03:00")
        self.assertEqual(s.state(KEY), {"video": VIDEO, "anchors": {"1:0": 1800, "2:1": 4800}, "absent": ["1:1"],
                                        "wrong": ["2:1"], "confirm": {}, "reject": {}})

    def test_seen_and_objected_last_word_per_second(self):
        """ADR-033, раздел 4: «✅ Гол виден» и «⏪ / ⏩ гола тут нет» на 30 с гола — у одной секунды действует
        последнее; у другой секунды — своё."""
        s = store()
        s.add(NOW, KEY, "1:0", "time", role="admin", via="replay", who=1001, video=VIDEO, sec=1800)
        s.add(NOW, KEY, "1:0", "confirm", role="helper", via="video", who=2002, video=VIDEO, sec=1800)
        s.add(NOW, KEY, "1:0", "reject", role="helper", via="video", who=2002, video=VIDEO, sec=1800)
        s.add(NOW, KEY, "1:1", "reject", role="admin", via="video", who=1001, video=VIDEO, sec=2500)
        st = s.state(KEY)
        self.assertEqual((st["confirm"], st["reject"]), ({}, {"1:0": [1800], "1:1": [2500]}))
        s.add(NOW, KEY, "1:0", "confirm", role="helper", via="video", who=2002, video=VIDEO, sec=1800)
        self.assertEqual(s.state(KEY)["confirm"], {"1:0": [1800]})
        self.assertEqual(s.state(KEY)["reject"], {"1:1": [2500]})

    def test_own_confirm_is_not_a_witness(self):
        """Свидетели независимы (ADR-033, раздел 4): «✅ Гол виден» от того, кто сам поставил эту секунду, в confirm
        не идёт — иначе два нажатия одного человека дали бы клип. Другой человек и другая секунда — идут."""
        s = store()
        s.add(NOW, KEY, "1:0", "time", role="admin", via="replay", who=1001, video=VIDEO, sec=1800)
        s.add(NOW, KEY, "1:0", "confirm", role="admin", via="video", who=1001, video=VIDEO, sec=1801)
        self.assertEqual(s.state(KEY)["confirm"], {})
        s.add(NOW, KEY, "1:0", "confirm", role="admin", via="video", who=1001, video=VIDEO, sec=2600)
        self.assertEqual(s.state(KEY)["confirm"], {"1:0": [2600]})   # не своя секунда — табло, он её видел
        s.add(NOW, KEY, "1:0", "confirm", role="helper", via="video", who=2002, video=VIDEO, sec=1800)
        self.assertEqual(s.state(KEY)["confirm"], {"1:0": [1800, 2600]})
        s.add(NOW, KEY, "1:1", "confirm", role="admin", via="video", who=1001, video=VIDEO, sec=2500)
        self.assertEqual(s.state(KEY)["confirm"]["1:1"], [2500])   # у гола нет своей отметки — свидетель он один
        unknown = store()   # перенос из replays.json и /marks_forget: кто отметил, неизвестно — считаем, что он же
        unknown.add(NOW, KEY, "1:0", "time", role="import", via="import", video=VIDEO, sec=1800)
        unknown.add(NOW, KEY, "1:0", "confirm", role="admin", via="video", who=1001, video=VIDEO, sec=1800)
        self.assertEqual(unknown.state(KEY)["confirm"], {})


class BotJournal(unittest.TestCase):
    """/replay и превью пишут в журнал; replays.json собирается из него; история и «Отозвать» у гола."""

    def setUp(self):
        import admin
        import bot
        self.bot = bot
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "2026-10-03.json").write_text(json.dumps({"date": "2026-10-03", "games": [GAME]}), encoding="utf-8")
        self.now = datetime(2026, 10, 4, 12, 0, tzinfo=TZ)
        self.track = admin.Tracker("bot", path=self.dir / "bot.json", clock=lambda: self.now)
        for name, value in (("LIVE_DIR", self.dir), ("REPLAYS_FILE", self.dir / "replays.json"),
                            ("STATE_DB", self.dir / "state.db"), ("ADMIN_IDS", frozenset({1001})),
                            ("TRACK", self.track)):
            p = mock.patch.object(bot, name, value)
            p.start()
            self.addCleanup(p.stop)

    def saved(self) -> dict:
        return json.loads((self.dir / "replays.json").read_text(encoding="utf-8"))["games"].get(KEY)

    def test_old_replays_imported_as_unchecked(self):
        (self.dir / "replays.json").write_text(json.dumps({"games": {KEY: {
            "video": VIDEO, "anchors": {"1:0": 1800}, "updated": "2026-10-04T10:00:00+03:00"}}}), encoding="utf-8")
        rows = self.bot.goal_marks().of(KEY)
        self.assertEqual([(r["score"], r["sec"], r["role"]) for r in rows], [("1:0", 1800, "import")])
        lines, open_ = self.bot.goal_history(KEY, "1:0", 1001)
        self.assertIn("перенесено, не проверено", lines[0])
        self.assertEqual([r["id"] for r in open_], [rows[0]["id"]])

    def test_confirm_old_mark(self):
        """Перепроверил перенесённую отметку — «Время верное»: та же секунда, но уже от него, в истории обе строки."""
        (self.dir / "replays.json").write_text(json.dumps({"games": {KEY: {
            "video": VIDEO, "anchors": {"1:0": 7406}, "updated": "2026-10-05T02:14:00+03:00"}}}), encoding="utf-8")
        old = self.bot.goal_marks().of(KEY)[0]
        c = mock.Mock(data=f"rp:g:2026-10-03:0:1:0", from_user=mock.Mock(id=1001),
                      message=mock.Mock(chat=mock.Mock(id=1001)))
        c.answer = mock.AsyncMock()
        c.message.answer = mock.AsyncMock()
        with mock.patch.object(self.bot, "published_league", mock.AsyncMock(return_value=None)):
            asyncio.run(self.bot.cb_replay(c))
            kb = c.message.answer.call_args.kwargs["reply_markup"]
            buttons = {b.text: b.callback_data for row in kb.inline_keyboard for b in row}
            self.assertEqual(buttons["✅ Время верное: 2:03:26"], f"rp:v:2026-10-03:0:{old['id']}")
            c.data = buttons["✅ Время верное: 2:03:26"]
            asyncio.run(self.bot.cb_replay(c))
        rows = self.bot.goal_marks().of(KEY)
        self.assertEqual([(r["role"], r["who"], r["sec"]) for r in rows], [("import", None, 7406), ("admin", 1001, 7406)])
        self.assertEqual(self.saved()["anchors"], {"1:0": 7406})
        lines, _ = self.bot.goal_history(KEY, "1:0", 1001)
        self.assertIn("· ты · 2:03:26", lines[-1])

    def test_typo_fixed_and_revoked(self):
        self.bot.replay_save("2026-10-03", 0, "1:0", f"{VIDEO}?t=1h3m8s", self.now, who=1001)
        self.bot.replay_save("2026-10-03", 0, "1:0", "1:08:03", self.now, who=1001)
        self.assertEqual(self.saved()["anchors"], {"1:0": 4083})
        rows = self.bot.goal_marks().of(KEY)
        self.assertEqual([(r["who"], r["role"], r["via"], r["sec"]) for r in rows],
                         [(1001, "admin", "replay", 3788), (1001, "admin", "replay", 4083)])
        self.assertIn("1:08:03", rows[1]["seen"])                   # что прислал человек — в журнале
        lines, open_ = self.bot.goal_history(KEY, "1:0", 1001)
        self.assertTrue(all("· ты ·" in x for x in lines))
        # «Отозвать» последнюю — действует прежняя, обе в истории
        self.bot.goal_marks().revoke(self.now, open_[-1]["id"], role="admin", via="replay", who=1001)
        self.bot.marks_apply(KEY, GAME, self.now)
        self.assertEqual(self.saved()["anchors"], {"1:0": 3788})
        lines, _ = self.bot.goal_history(KEY, "1:0", 1001)
        self.assertTrue(lines[-1].endswith("— отозвано"))

    def test_revoke_button(self):
        self.bot.replay_save("2026-10-03", 0, "1:0", f"{VIDEO}?t=30m", self.now, who=1001)
        mark = self.bot.goal_marks().of(KEY)[0]
        c = mock.Mock(data=f"rp:r:2026-10-03:0:{mark['id']}", from_user=mock.Mock(id=1001),
                      message=mock.Mock(chat=mock.Mock(id=1001)))
        c.answer = mock.AsyncMock()
        c.message.answer = mock.AsyncMock()
        with mock.patch.object(self.bot, "published_league", mock.AsyncMock(return_value=None)):
            asyncio.run(self.bot.cb_replay(c))
            asyncio.run(self.bot.cb_replay(c))                      # второе нажатие — уже отозвано
        self.assertIsNone(self.saved())                             # действующих отметок нет — матча нет
        self.assertIn("уже отозвана", c.answer.call_args.args[0])
        self.assertEqual([r["kind"] for r in self.bot.goal_marks().of(KEY)], ["time", "revoke"])

    def test_drop_keeps_history(self):
        self.bot.replay_save("2026-10-03", 0, "", f"{VIDEO}\n25:20\n57:04\n59:37\n1:20:40", self.now, who=1001)
        self.bot.replay_drop("2026-10-03", 0, self.now, who=1001)
        self.assertIsNone(self.saved())
        self.assertEqual([r["kind"] for r in self.bot.goal_marks().of(KEY)], ["time"] * 4 + ["revoke"] * 4)

    def test_preview_answer_records_who_and_window(self):
        bot = mock.Mock()
        bot.edit_message_caption = mock.AsyncMock()
        bot.send_message = mock.AsyncMock(return_value=mock.Mock(message_id=9))   # «⏳ Режу видео…» — 30 с ответа
        ask = {"from": 1500, "len": 125, "cand": [47, 72]}
        with mock.patch.object(self.bot, "published_league", mock.AsyncMock(return_value=None)), \
                mock.patch.object(self.bot, "PREVIEWS_FILE", self.dir / "previews.json"):
            asyncio.run(self.bot.preview_answer(bot, 761, KEY, "1:0", ask, VIDEO, 47, who=761))
        r = self.bot.goal_marks().of(KEY)[0]
        self.assertEqual((r["who"], r["role"], r["via"], r["sec"]), (761, "helper", "preview", 1547))
        self.assertEqual(json.loads(r["seen"]), {"from": 1500, "len": 125, "cand": [47, 72], "pick": 47})
        self.assertEqual(self.saved()["anchors"], {"1:0": 1547})

    def test_any_day_and_forget(self):
        self.assertEqual(self.bot.replay_day_arg("03.10", self.now), "2026-10-03")
        self.assertEqual(self.bot.replay_day_arg("3.10.2026", self.now), "2026-10-03")
        self.assertEqual(self.bot.replay_day_arg("31.12", self.now), "2025-12-31")   # не в будущем
        self.assertIsNone(self.bot.replay_day_arg("32.10", self.now))
        self.assertIsNone(self.bot.replay_day_arg("завтра", self.now))
        later = datetime(2026, 11, 20, 12, 0, tzinfo=TZ)              # через полтора месяца матч ещё открыть
        text, kb = self.bot.replay_list(later, "2026-10-03")
        self.assertEqual([b.callback_data for row in kb.inline_keyboard for b in row], ["rp:m:2026-10-03:0"])
        self.assertIsNone(self.bot.replay_list(later)[1])
        self.bot.replay_save("2026-10-03", 0, "1:0", f"{VIDEO}?t=30m", self.now, who=1001)
        self.assertEqual(self.bot.goal_marks().forget(1001), 1)
        self.assertEqual(self.bot.goal_marks().of(KEY)[0]["role"], "admin")


LEAGUE = {"games": [{"date": "2026-10-03", "home": "tverichi", "away": "metallurg", "goals": [
    {"score": "1:0", "team": "home", "period": "1", "time": "05:00"},
    {"score": "1:1", "team": "away", "period": "1", "time": "15:00"},
    {"score": "2:1", "team": "home", "period": "1", "time": "18:00"},
    {"score": "2:2", "team": "away", "period": "2", "time": "30:00"}]}]}


class MarkChecks(unittest.TestCase):
    """ADR-033, шаг 3: проверка при вводе, кнопки опечаток, статус проверки службой, спор отметившему."""
    saved = BotJournal.saved

    def setUp(self):
        BotJournal.setUp(self)
        self.protocol = self.bot.protocol_of(LEAGUE, GAME)
        self.bot.replay_save("2026-10-03", 0, "1:1", f"{VIDEO}?t=40m", self.now, who=1001)   # 1:1 — 40:00

    def test_typo_found_before_saving(self):
        # 1:0 по сайту на 18,5 минуты раньше 1:1 → в записи около 21:30; прислали 30:21 — минуты и секунды местами
        issues, cands = self.bot.replay_issues(KEY, GAME, VIDEO, [("1:0", 1821)], self.protocol)
        self.assertTrue(any("быстрее" in x for x in issues), issues)
        self.assertIn(1290, cands)                                         # 0:21:30
        self.assertEqual(self.bot.replay_issues(KEY, GAME, VIDEO, [("1:0", 1290)], self.protocol), ([], []))
        issues, _ = self.bot.replay_issues(KEY, GAME, VIDEO, [("1:0", 2500)], self.protocol)
        self.assertTrue(any("наоборот" in x for x in issues), issues)       # в записи позже 1:1, по протоколу раньше
        issues, _ = self.bot.replay_issues(KEY, GAME, VIDEO, [("2:1", 2410)], self.protocol)
        self.assertTrue(any("почти одна секунда" in x for x in issues), issues)

    def test_message_asks_then_button_saves(self):
        m = mock.Mock(text="30:21", chat=mock.Mock(id=1001), from_user=mock.Mock(id=1001))
        m.answer = mock.AsyncMock()
        self.bot.REPLAY_ASK[1001] = ("2026-10-03", 0, "1:0", self.now, KEY)
        with mock.patch.object(self.bot, "published_league", mock.AsyncMock(return_value=LEAGUE)), \
                mock.patch.object(self.bot, "datetime", mock.Mock(now=lambda tz=None: self.now,
                                                                  fromisoformat=datetime.fromisoformat)):
            asyncio.run(self.bot.h_replay_link(m))
            kb = m.answer.call_args.kwargs["reply_markup"]
            buttons = {b.text: b.callback_data for row in kb.inline_keyboard for b in row}
            self.assertIn("Записать 21:30", buttons)
            self.assertEqual(self.saved()["anchors"], {"1:1": 2400})            # пока не записано
            c = mock.Mock(data=buttons["Записать 21:30"], from_user=mock.Mock(id=1001),
                          message=mock.Mock(chat=mock.Mock(id=1001)))
            c.answer = mock.AsyncMock()
            c.message.answer = mock.AsyncMock()
            asyncio.run(self.bot.cb_replay(c))
        self.assertEqual(self.saved()["anchors"], {"1:0": 1290, "1:1": 2400})
        self.assertEqual(self.bot.goal_marks().of(KEY)[-1]["seen"], "30:21")   # что прислал — в журнале

    def test_status_and_dispute(self):
        (self.dir / "clips.json").write_text(json.dumps({"games": {KEY: {
            "video": VIDEO, "status": "ok", "goals": {"1:1": {"change": 2300, "team": "away"}},
            "checks": {"1:1": {"t": 2400, "status": "conflict", "for": [],
                               "against": ["счёт на табло сменился раньше"]}}}}}), encoding="utf-8")
        text = self.bot.replay_text("2026-10-03", GAME, self.saved(), self.protocol)
        self.assertIn("спор: счёт на табло сменился раньше", text)
        bot = mock.Mock()
        bot.send_message = mock.AsyncMock()
        later = self.now + self.bot.DISPUTE_CUT_WAIT
        with mock.patch.object(self.bot, "DISPUTES_FILE", self.dir / "disputes.json"), \
                mock.patch.object(self.bot, "published_league", mock.AsyncMock(return_value=None)), \
                mock.patch.object(self.bot.asyncio, "sleep", mock.AsyncMock()):
            self.assertEqual(asyncio.run(self.bot.dispute_step(bot, self.now)), 0)   # видео спора ещё режется
            # служба cuts так и не вырезала (стоит) — спор не ждёт её вечно: ссылками, как раньше
            self.assertEqual(asyncio.run(self.bot.dispute_step(bot, later)), 1)
            self.assertEqual(asyncio.run(self.bot.dispute_step(bot, later)), 0)   # один раз
        cid, text = bot.send_message.call_args.args
        self.assertEqual(cid, 1001)                                          # отметившему
        self.assertIn("Спор по голу 1:1", text)
        self.assertEqual(bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data,
                         "rp:g:2026-10-03:0:1:1")


class Coverage(unittest.TestCase):
    """«Почему не у всех» в /replay: причины, что делать и кнопки на недоделанные матчи."""

    def setUp(self):
        BotJournal.setUp(self)

    def test_button_and_todo(self):
        cov = {KEY: {"goals": 5, "replays": 3, "why": "not_found", "missing": ["1:1", "2:2"]},
               "2026-10-03|rostov|krasnodar": {"goals": 4, "replays": 4, "why": "ok"},
               "2026-10-03|kaluga|dinamo-576": {"goals": 2, "replays": 0, "why": "no_board"}}
        (self.dir / "clips.json").write_text(json.dumps({"coverage": cov}), encoding="utf-8")
        text, kb = self.bot.replay_list(self.now)
        self.assertEqual(kb.inline_keyboard[0][0].callback_data, "rp:why")
        self.assertIn("2 матча", kb.inline_keyboard[0][0].text)
        text, kb = self.bot.coverage_todo(cov)
        self.assertIn("Что сделать", text)
        self.assertIn("пришлю видео для поиска", text)
        self.assertIn("кадр табло", text)
        calls = [b.callback_data for row in kb.inline_keyboard for b in row]
        self.assertEqual(calls, ["rp:m:2026-10-03:0", "rp:list"])   # калуги нет в файле дня — без кнопки
        self.assertIn("без повтора 2", kb.inline_keyboard[0][0].text)
        self.assertIn("Всё готово", self.bot.coverage_todo({KEY: {"goals": 1, "replays": 1, "why": "ok"}})[0])


class BoardReasons(unittest.TestCase):
    """Почему табло не дало секунд — у матча и у гола в /replay."""

    def setUp(self):
        BotJournal.setUp(self)

    def text(self, board):
        (self.dir / "clips.json").write_text(json.dumps({"games": {KEY: board}}), encoding="utf-8")
        return self.bot.replay_text("2026-10-03", GAME, None, None, VIDEO)

    def test_reasons(self):
        self.assertIn("ещё не разбирала", self.bot.replay_text("2026-10-03", GAME, None, None, VIDEO))
        self.assertIn("не скачалась: VkError", self.text({"video": VIDEO, "status": "error", "error": "VkError: 403"}))
        self.assertIn("не размечено", self.text({"video": VIDEO, "status": "no_board", "goals": {}}))
        self.assertIn("Запись ещё не готова", self.text({"video": VIDEO, "status": "wait", "goals": {}}))
        self.assertIn("смены счёта на нём служба не увидела", self.text({"video": VIDEO, "status": "ok", "goals": {}}))
        t = self.text({"video": VIDEO, "status": "ok", "goals": {"1:0": {"change": 1500, "team": "home"}},
                       "rejected": {"1:1": "до смены в клетке не «0»"}})
        self.assertNotIn("📺 Табло разобрано", t)
        self.assertIn("1:1</b>", t)
        self.assertIn("табло: до смены в клетке не «0»", t)


if __name__ == "__main__":
    unittest.main()

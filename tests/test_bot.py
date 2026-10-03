"""Онбординг бота (ADR-005): тексты, кнопки и стикеры — без Telegram и без токена."""
import asyncio
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


class AppUrl(unittest.TestCase):
    def test_team_added_to_query(self):
        with mock.patch.object(bot, "WEBAPP_URL", "https://x.github.io/app/?v=2"):
            self.assertEqual(bot.app_url("ryazan-vdv"), "https://x.github.io/app/?v=2&team=ryazan-vdv")
            self.assertEqual(bot.app_url(), "https://x.github.io/app/?v=2")

    def test_one_button_opens_app(self):
        with mock.patch.object(bot, "WEBAPP_URL", "https://x.github.io/app/"):
            kb = bot.app_kb("arktika").inline_keyboard
        self.assertEqual(len(kb), 1)
        self.assertEqual(len(kb[0]), 1)
        self.assertEqual(kb[0][0].web_app.url, "https://x.github.io/app/?team=arktika")

    def test_app_url_has_default(self):
        self.assertTrue(bot.WEBAPP_URL.startswith("https://"))

    def test_no_old_schedule_menu(self):
        src = (ROOT / "bot.py").read_text(encoding="utf-8")
        for old in ("ReplyKeyboardMarkup", "set_my_commands", "Следующая игра", "calendar.pdf"):
            self.assertNotIn(old, src)


class Welcome(unittest.TestCase):
    def test_team_named_in_greeting(self):
        self.assertIn(bot.TEAMS["arktika"], bot.welcome_text("arktika"))

    def test_generic_greeting_points_to_button(self):
        self.assertIn("Открыть РХЛ", bot.welcome_text())

    def test_greeting_is_valid_html(self):
        tags = re.findall(r"</?(\w+)>", bot.welcome_text("arktika"))
        self.assertEqual(tags.count("b") % 2, 0)

    def test_description_limits(self):
        self.assertLessEqual(len(bot.DESCRIPTION), 512)
        self.assertLessEqual(len(bot.SHORT_DESCRIPTION), 120)


class CustomEmoji(unittest.TestCase):
    def tearDown(self):
        bot.CUSTOM.clear()

    def test_plain_without_set(self):
        self.assertEqual(bot.e("puck"), "🏒")
        btn = bot.app_kb().inline_keyboard[0][0]
        self.assertEqual(btn.text, "🏒 Открыть РХЛ")
        self.assertIsNone(btn.icon_custom_emoji_id)

    def test_custom_from_set_in_upload_order(self):
        stickers = [mock.Mock(custom_emoji_id=f"id{i}") for i in range(len(bot.EMOJI))]
        bot.CUSTOM.update(bot.custom_ids(stickers))
        self.assertEqual(bot.e("puck"), '<tg-emoji emoji-id="id0">🏒</tg-emoji>')
        self.assertIn('emoji-id="id', bot.welcome_text())
        btn = bot.app_kb().inline_keyboard[0][0]
        self.assertEqual((btn.text, btn.icon_custom_emoji_id), ("Открыть РХЛ", "id0"))

    def test_emoji_off_falls_back(self):
        bot.CUSTOM["puck"] = "id0"
        self.assertTrue(bot.emoji_off(Exception("ENTITY_TEXT_INVALID")))
        self.assertEqual(bot.e("puck"), "🏒")
        self.assertFalse(bot.emoji_off(Exception("again")))   # второй раз не повторяем


class Stickers(unittest.TestCase):
    def test_every_sticker_used_in_bot_exists(self):
        names = set(re.findall(r'send_sticker\([^)]*"(\w+)"', (ROOT / "bot.py").read_text(encoding="utf-8")))
        self.assertEqual(names, {"hello", "tap", "bell", "gameday"})
        for n in names:
            data = (bot.STICKERS / f"{n}.webp").read_bytes()
            self.assertEqual(data[:4] + data[8:12], b"RIFFWEBP", n)
            self.assertLess(len(data), 512 * 1024, n)   # лимит Telegram на статичный стикер

    def test_emoji_set(self):
        icons = json.loads((bot.STICKERS / "emoji.json").read_text(encoding="utf-8"))
        self.assertLessEqual(len(icons), 200)   # лимит набора кастомных эмодзи
        for n in icons:
            data = (bot.STICKERS / "emoji" / f"{n}.webp").read_bytes()
            self.assertEqual(data[:4] + data[8:12], b"RIFFWEBP", n)


class AfterMatch(unittest.TestCase):
    """Сообщение после матча с кнопкой «Как это было» (ADR-008)."""

    names = {"ryazan-vdv": "Рязань-ВДВ", "samara": "Самара", "belgorod": "Белгород"}

    def game(self, gid, d, home, away, hs, as_, dec=""):
        return {"id": gid, "date": d, "home": home, "away": away,
                "score": {"home": hs, "away": as_, "decision": dec, "periods": []}}

    def test_match_link(self):
        with mock.patch.object(bot, "WEBAPP_URL", "https://x.github.io/app/"):
            kb = bot.recap_kb("n4").inline_keyboard
            self.assertEqual(bot.data_url("league.json"), "https://x.github.io/app/data/league.json")
        self.assertEqual(kb[0][0].web_app.url, "https://x.github.io/app/?match=n4")
        self.assertIn("Как это было", kb[0][0].text)

    def test_data_url_without_query(self):
        with mock.patch.object(bot, "WEBAPP_URL", "https://x.github.io/app/index.html?v=2"):
            self.assertEqual(bot.data_url("matches/n4.json"), "https://x.github.io/app/data/matches/n4.json")

    def test_win_text(self):
        g = self.game("n4", "2026-10-03", "ryazan-vdv", "belgorod", 6, 1)
        text = bot.result_text(g, self.names, "Пять шайб подряд у «Рязань-ВДВ».")
        self.assertIn("Победа!", text)
        self.assertIn("Рязань-ВДВ <b>6:1</b> Белгород", text)
        self.assertIn("Пять шайб подряд у «Рязань-ВДВ».", text)

    def test_away_overtime_win_and_loss(self):
        g = self.game("n78", "2026-10-03", "samara", "ryazan-vdv", 5, 6, "ОТ")
        self.assertIn("Победа в овертайме!", bot.result_text(g, self.names))
        self.assertIn("Самара <b>5:6</b> (ОТ) Рязань-ВДВ", bot.result_text(g, self.names))
        g = self.game("n79", "2026-10-04", "samara", "ryazan-vdv", 3, 2, "Б")
        self.assertIn("Поражение по буллитам", bot.result_text(g, self.names))

    def test_text_is_valid_html(self):
        g = self.game("n4", "2026-10-03", "ryazan-vdv", "belgorod", 6, 1)
        text = bot.result_text(g, {"ryazan-vdv": "Рязань-ВДВ", "belgorod": "<b>"}, "a < b")
        self.assertNotIn("<b><b>", text)
        self.assertIn("&lt;b&gt;", text)
        self.assertIn("a &lt; b", text)

    def test_only_fresh_unannounced_games_of_our_team(self):
        data = {"games": [
            self.game("old", "2026-10-01", "ryazan-vdv", "belgorod", 1, 0),
            self.game("done", "2026-10-04", "ryazan-vdv", "belgorod", 2, 0),
            self.game("new", "2026-10-05", "samara", "ryazan-vdv", 2, 3),
            self.game("other", "2026-10-05", "samara", "belgorod", 2, 3),
            {"id": "future", "date": "2026-10-06", "home": "ryazan-vdv", "away": "samara"},
        ]}
        fresh = bot.fresh_results(data, {"done"}, date(2026, 10, 5))
        self.assertEqual([g["id"] for g in fresh], ["new"])

    def test_quiet_at_night(self):
        tz = bot.TZ
        self.assertTrue(bot.quiet(datetime(2026, 10, 5, 23, 30, tzinfo=tz)))
        self.assertTrue(bot.quiet(datetime(2026, 10, 6, 8, 59, tzinfo=tz)))
        self.assertFalse(bot.quiet(datetime(2026, 10, 6, 9, 0, tzinfo=tz)))
        self.assertFalse(bot.quiet(datetime(2026, 10, 5, 21, 40, tzinfo=tz)))

    def test_first_run_has_no_file(self):
        with mock.patch.object(bot, "ANNOUNCED_FILE", Path("/nonexistent/announced.json")):
            self.assertIsNone(bot.load_announced())

    def test_reminder_times_unchanged(self):
        self.assertEqual((bot.REMIND_TODAY_AT, bot.REMIND_TOMORROW_AT), (time(10, 0), time(19, 0)))


class RemindLink(unittest.TestCase):
    """«Напомнить» в мини-аппе ведёт на /start remind: бот сразу включает и говорит, что включил."""

    def run_start(self, args):
        m = mock.Mock()
        m.chat.id = 42
        m.answer = mock.AsyncMock()
        cmd = bot.CommandObject(prefix="/", command="start", args=args)
        with mock.patch.object(bot, "SUBS", set()) as subs, mock.patch.object(bot, "save_subs") as save, \
                mock.patch.object(bot, "send_sticker", mock.AsyncMock(return_value=True)) as sticker, \
                mock.patch.object(bot, "say", mock.AsyncMock()) as say:
            asyncio.run(bot.start(m, cmd))
            text, kb = say.call_args.args[2]()
        return subs, save, [c.args[2] for c in sticker.call_args_list], text, kb

    def test_link_turns_reminders_on(self):
        subs, save, stickers, text, kb = self.run_start("remind")
        self.assertEqual(subs, {42})
        save.assert_called_once()
        self.assertEqual(stickers, ["bell"])   # «Напомню!», а не приветствие
        self.assertIn("включены", text)
        self.assertIn("Выключить", kb.inline_keyboard[0][0].text)

    def test_plain_start_still_greets(self):
        subs, save, stickers, text, _ = self.run_start(None)
        self.assertEqual(subs, set())
        save.assert_not_called()
        self.assertEqual(stickers, ["hello"])
        self.assertIn("Жми «Открыть РХЛ»", text)

    def test_next_game_named_when_on(self):
        with mock.patch.object(bot, "SUBS", {42}):
            text = bot.remind_text(42, date(2026, 9, 25))
        self.assertIn("Ближайшая: Сб 03.10, дома с «МХК Белгород»", text)
        with mock.patch.object(bot, "SUBS", set()):
            self.assertNotIn("Ближайшая", bot.remind_text(42, date(2026, 9, 25)))
        with mock.patch.object(bot, "SUBS", {42}):   # сезон кончился — без строки
            self.assertNotIn("Ближайшая", bot.remind_text(42, date(2027, 6, 1)))


class Leaders(unittest.TestCase):
    """ADR-009: лидеры лиги в боте — коротко и одной кнопкой в мини-апп."""
    data = {"season": "2025/26", "league": "НМХЛ", "categories": {
        "pts": [{"rank": i, "name": f"Игрок {i}", "pts": 80 - i, "team": "polet"} for i in range(1, 6)],
        "g": [{"rank": 1, "name": "Снайпер <b>", "g": 39, "club": "Буран Мск"}],
        "pm": [{"rank": 1, "name": "Плюс", "pm": 52, "team": "ermak"}],
        "sv_pct": [{"rank": 1, "name": "Вратарь", "sv_pct": 94.4, "team": "ryazan-vdv"}],
    }}

    def test_button_opens_leaders(self):
        with mock.patch.object(bot, "WEBAPP_URL", "https://x.github.io/app/"):
            kb = bot.leaders_kb().inline_keyboard
        self.assertEqual(kb[0][0].web_app.url, "https://x.github.io/app/?view=leaders")
        self.assertIn("Все лидеры", kb[0][0].text)

    def test_top_three_and_firsts(self):
        text = bot.leaders_text(self.data)
        self.assertIn("Лидеры НМХЛ 2025/26</b> · прошлый сезон", text)
        self.assertIn("1. Игрок 1 (Полёт) — 79 очков", text)
        self.assertIn("3. Игрок 3 (Полёт) — 77 очков", text)
        self.assertNotIn("Игрок 4", text)
        self.assertIn("Снайпер: Снайпер &lt;b&gt; (Буран Мск) — 39 голов", text)
        self.assertIn("Плюс-минус: Плюс (Ермак) — +52", text)
        self.assertIn("Вратарь: Вратарь (Рязань-ВДВ) — 94,4% отражённых", text)
        self.assertIn("появятся после первого тура", text)

    def test_current_season_has_no_note(self):
        text = bot.leaders_text({**self.data, "season": "2026/27", "league": "РХЛ"})
        self.assertNotIn("прошлый сезон", text)
        self.assertNotIn("первого тура", text)

    def test_no_data_still_points_to_app(self):
        self.assertIn("в приложении", bot.leaders_text(None))
        self.assertIn("в приложении", bot.leaders_text({"categories": {}}))

    def test_plural(self):
        self.assertEqual([bot.plural(n, "гол", "гола", "голов") for n in (1, 2, 5, 11, 21, 22)],
                         ["гол", "гола", "голов", "голов", "гол", "гола"])

    def test_published_file_reads(self):
        """Настоящий файл лидеров, собранный build_data.py из leaders.json в git."""
        import build_data
        data = build_data.leaders(build_data.load_teams(), build_data.load_leaders())
        text = bot.leaders_text(data)
        self.assertIn("Султанов Реваль (Полёт) — 78 очков", text)


class Raskat(unittest.TestCase):
    """«Раскат» в боте (контракт «Раската», раздел 6): игра дня, лист ожидания зачёта."""

    index = {"built": "2026-10-03T09:17:00+03:00", "season": "2026/27", "today": "2026-10-03",
             "days": [{"date": "2026-10-02", "n": 1, "w": 6, "h": 6, "k": 4, "hard": 1, "par": 55},
                      {"date": "2026-10-03", "n": 2, "w": 6, "h": 6, "k": 5, "hard": 1, "par": 70}]}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "raskat_waitlist.json"
        self.file = mock.patch.object(bot, "WAITLIST_FILE", self.tmp)
        self.file.start()
        self.addCleanup(self.file.stop)

    def saved(self):
        return json.loads(self.tmp.read_text())

    def api(self, value):
        """Сервер зачётов включён (непустая RASKAT_API) или нет."""
        return mock.patch.dict(os.environ, {"RASKAT_API": value})

    # ---------- кнопка и текст ----------

    def test_button_opens_raskat(self):
        with mock.patch.object(bot, "WEBAPP_URL", "https://x.github.io/app/"), self.api(""):
            kb = bot.raskat_kb(42).inline_keyboard
        self.assertEqual(kb[0][0].web_app.url, "https://x.github.io/app/?startapp=raskat")
        self.assertIn("Собрать раскат", kb[0][0].text)

    def test_day_of_index_in_text(self):
        with mock.patch.object(bot, "WAITLIST", set()), self.api(""):
            text = bot.raskat_text(self.index, 42, date(2026, 10, 3))
        self.assertIn("Раскат дня</b> №2", text)
        self.assertIn("Сегодня 5 номеров и поле 6×6, норма — 1:10.", text)
        self.assertIn("номера звена по порядку", text)
        self.assertIn("Зачёта пока нет", text)
        self.assertEqual(text.count("<b>"), text.count("</b>"))

    def test_text_without_index_still_points_to_app(self):
        with mock.patch.object(bot, "WAITLIST", set()), self.api(""):
            text = bot.raskat_text(None)
        self.assertIn("Раскат дня", text)
        self.assertIn("Жми кнопку", text)

    def test_par_text(self):
        self.assertEqual([bot.par_text(n) for n in (45, 60, 70, 125)],
                         ["45 секунд", "1:00", "1:10", "2:05"])

    def test_texts_are_russian(self):
        with mock.patch.object(bot, "WAITLIST", {42}), self.api(""):
            texts = [bot.raskat_text(self.index, 42, date(2026, 10, 3)), bot.raskat_open_text(),
                     bot.B_RASKAT, bot.B_WAIT_ON, bot.B_WAIT_OFF]
        for t in texts:
            self.assertFalse(re.search(r"[A-Za-z]", re.sub(r"<[^>]+>", "", t)), t)

    # ---------- лист ожидания ----------

    def test_waitlist_add_remove_and_idempotent(self):
        with mock.patch.object(bot, "WAITLIST", set()):
            self.assertTrue(bot.waitlist_set(42, True))
            self.assertEqual(self.saved(), [42])
            bot.waitlist_set(42, True)          # дважды — файл не ломается
            bot.waitlist_set(7, True)
            self.assertEqual(self.saved(), [7, 42])
            self.assertFalse(bot.waitlist_set(42, False))
            bot.waitlist_set(42, False)         # отказ дважды — тоже ничего
            self.assertEqual(self.saved(), [7])
            self.assertEqual(bot.WAITLIST, {7})

    def test_waitlist_survives_broken_file(self):
        self.tmp.write_text("{не json")
        self.assertEqual(bot.load_waitlist(), set())
        with mock.patch.object(bot, "WAITLIST_FILE", self.tmp.parent / "нет.json"):
            self.assertEqual(bot.load_waitlist(), set())

    def test_second_raskat_offers_to_stop(self):
        with mock.patch.object(bot, "WAITLIST", set()), self.api(""):
            self.assertIn(bot.B_WAIT_ON, bot.raskat_kb(42).inline_keyboard[1][0].text)
            bot.waitlist_set(42, True)
            kb = bot.raskat_kb(42).inline_keyboard
            self.assertIn(bot.B_WAIT_OFF, kb[1][0].text)
            self.assertIn("Позову, как только он откроется", bot.raskat_text(self.index, 42))

    def test_no_waitlist_button_when_scored(self):
        with mock.patch.object(bot, "WAITLIST", {42}), self.api("https://rhl.example/api/raskat"):
            self.assertEqual(len(bot.raskat_kb(42).inline_keyboard), 1)
            self.assertIn("идёт в зачёт дня", bot.raskat_text(self.index, 42))

    # ---------- диплинк ----------

    def run_start(self, args):
        m = mock.Mock()
        m.chat.id = 42
        with mock.patch.object(bot, "WAITLIST", set()), self.api(""), \
                mock.patch.object(bot, "published_raskat", mock.AsyncMock(return_value=self.index)), \
                mock.patch.object(bot, "send_sticker", mock.AsyncMock(return_value=True)), \
                mock.patch.object(bot, "say", mock.AsyncMock()) as say:
            asyncio.run(bot.start(m, bot.CommandObject(prefix="/", command="start", args=args)))
            return say.call_args.args[2]()

    def test_start_raskat_opens_game_of_the_day(self):
        text, kb = self.run_start("raskat")
        self.assertIn("Раскат дня", text)
        self.assertIn("startapp=raskat", kb.inline_keyboard[0][0].web_app.url)
        self.assertIn(bot.B_WAIT_ON, kb.inline_keyboard[1][0].text)

    def test_plain_start_is_not_raskat(self):
        text, kb = self.run_start(None)
        self.assertIn("Жми «Открыть РХЛ»", text)
        self.assertEqual(len(kb.inline_keyboard), 1)

    # ---------- зачёт включили ----------

    def test_waitlist_called_once_when_api_appears(self):
        with mock.patch.object(bot, "WAITLIST", {7, 42}), \
                mock.patch.object(bot, "say", mock.AsyncMock()) as say, self.api("https://rhl.example/api"):
            self.assertEqual(asyncio.run(bot.raskat_open_broadcast(mock.Mock())), 2)
            self.assertEqual(bot.WAITLIST, set())
            self.assertEqual(self.saved(), [])
            text, kb = say.call_args.args[2]()
            self.assertEqual(asyncio.run(bot.raskat_open_broadcast(mock.Mock())), 0)   # второй раз — тишина
            self.assertEqual(say.await_count, 2)
        self.assertIn("открылся зачёт", text)
        self.assertEqual(len(kb.inline_keyboard), 1)   # в рассылке кнопка одна

    def test_nobody_called_without_api(self):
        with mock.patch.object(bot, "WAITLIST", {42}), \
                mock.patch.object(bot, "say", mock.AsyncMock()) as say, self.api(""):
            self.assertEqual(asyncio.run(bot.raskat_open_broadcast(mock.Mock())), 0)
            self.assertEqual(bot.WAITLIST, {42})
            say.assert_not_awaited()

    def test_blocked_bot_does_not_stop_broadcast(self):
        err = bot.TelegramForbiddenError(method=mock.Mock(), message="bot was blocked")
        with mock.patch.object(bot, "WAITLIST", {7, 42}), self.api("https://rhl.example/api"), \
                mock.patch.object(bot, "say", mock.AsyncMock(side_effect=[err, None])):
            self.assertEqual(asyncio.run(bot.raskat_open_broadcast(mock.Mock())), 1)
            self.assertEqual(bot.WAITLIST, set())

    # ---------- ежедневных напоминаний про раскат нет ----------

    def test_no_daily_raskat_reminder(self):
        g = bot.GAMES[0]
        for text in (bot.reminder_text(g, "today"), bot.reminder_text(g, "tomorrow"),
                     bot.remind_text(42, date(2026, 9, 25))):
            self.assertNotIn("аскат", text)


if __name__ == "__main__":
    unittest.main()

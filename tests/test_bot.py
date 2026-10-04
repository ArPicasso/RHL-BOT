"""Онбординг бота (ADR-005): тексты, кнопки и стикеры — без Telegram и без токена."""
import asyncio
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402

# счётчики пульта (ADR-021) — во временный каталог, не в status/ рядом с кодом
bot.TRACK = bot.admin.Tracker("bot", Path(tempfile.mkdtemp()) / "bot.json")


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

    def test_fan_made_not_official(self):
        """ADR-021: в описании и приветствии видно, что мы не лига."""
        self.assertIn("Неофициальный", bot.DESCRIPTION)
        self.assertIn("не связан с РХЛ", bot.DESCRIPTION)
        self.assertTrue(bot.SHORT_DESCRIPTION.startswith("Неофициальный"))
        for team in (None, "arktika"):
            self.assertIn("не официальное приложение РХЛ", bot.welcome_text(team))
        tags = re.findall(r"</?(\w+)>", bot.welcome_text())
        self.assertEqual(tags.count("i") % 2, 0)


class AdminPanel(unittest.TestCase):
    """Пульт админа (ADR-021): /admin, счётчики подписок и «Старт» без id."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for name, value in (("SUBS_FILE", self.tmp / "subscribers.json"), ("SUBS", {}),
                            ("TRACK", bot.admin.Tracker("bot", self.tmp / "bot.json"))):
            p = mock.patch.object(bot, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_admin_gets_button_others_get_their_id(self):
        with mock.patch.object(bot, "ADMIN_IDS", frozenset({7})), \
                mock.patch.object(bot, "WEBAPP_URL", "https://x.github.io/app/?v=3"):
            text, kb = bot.admin_reply(7, 7)
            self.assertEqual(kb.inline_keyboard[0][0].web_app.url, "https://x.github.io/app/admin.html")
            text, kb = bot.admin_reply(8, 8)
        self.assertIsNone(kb)
        self.assertIn("<code>8</code>", text)
        self.assertIn("ADMIN_IDS", text)

    def test_start_kinds(self):
        self.assertEqual([bot.start_kind(a) for a in ("", "today", "remind", "remind-tambov", "tambov", "leaders", "zzz")],
                         ["plain", "today", "remind", "remind", "team", "leaders", "other"])

    def test_subscription_counters(self):
        bot.follow(42, "tambov")
        bot.follow(42, "sokol")      # вторая команда — не новая подписка
        bot.follow(43, "tambov")
        bot.unsubscribe(42)
        bot.unsubscribe(43, blocked=True)
        bot.unsubscribe(44, blocked=True)   # его и не было
        self.assertEqual(bot.TRACK.today(), {"sub_new": 2, "sub_off": 2, "blocked": 1})
        bot.TRACK.flush()
        self.assertNotIn("42", (self.tmp / "bot.json").read_text(encoding="utf-8"))


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

    def test_only_fresh_unannounced_games(self):
        data = {"games": [
            self.game("old", "2026-10-01", "ryazan-vdv", "belgorod", 1, 0),
            self.game("done", "2026-10-04", "ryazan-vdv", "belgorod", 2, 0),
            self.game("new", "2026-10-05", "samara", "ryazan-vdv", 2, 3),
            self.game("other", "2026-10-05", "samara", "belgorod", 2, 3),
            self.game("by-live", "2026-10-05", "sokol", "proton", 1, 0),
            {"id": "future", "date": "2026-10-06", "home": "ryazan-vdv", "away": "samara"},
        ]}
        announced = {"done", "2026-10-05|sokol|proton"}   # о последнем уже написали по онлайну
        fresh = bot.fresh_results(data, announced, date(2026, 10, 5), teams={"ryazan-vdv"})
        self.assertEqual([g["id"] for g in fresh], ["new"])
        fresh = bot.fresh_results(data, announced, date(2026, 10, 5))   # вся лига
        self.assertEqual([g["id"] for g in fresh], ["new", "other"])

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
        with mock.patch.object(bot, "SUBS", {}) as subs, mock.patch.object(bot, "save_subs") as save, \
                mock.patch.object(bot, "published_league", mock.AsyncMock(return_value=None)), \
                mock.patch.object(bot, "send_sticker", mock.AsyncMock(return_value=True)) as sticker, \
                mock.patch.object(bot, "say", mock.AsyncMock()) as say:
            asyncio.run(bot.start(m, cmd))
            text, kb = say.call_args.args[2]()
        return subs, save, [c.args[2] for c in sticker.call_args_list], text, kb

    def test_link_turns_reminders_on(self):
        subs, save, stickers, text, kb = self.run_start("remind")
        self.assertEqual(subs, {42: ["ryazan-vdv"]})   # как раньше — «Рязань-ВДВ»
        save.assert_called_once()
        self.assertEqual(stickers, ["bell"])   # «Напомню!», а не приветствие
        self.assertIn("включены", text)
        self.assertIn("Выключить", kb.inline_keyboard[0][0].text)

    def test_plain_start_still_greets(self):
        subs, save, stickers, text, _ = self.run_start(None)
        self.assertEqual(subs, {})
        save.assert_not_called()
        self.assertEqual(stickers, ["hello"])
        self.assertIn("Жми «Открыть РХЛ»", text)

    def test_next_game_named_when_on(self):
        with mock.patch.object(bot, "SUBS", {42: ["ryazan-vdv"]}):
            text = bot.remind_text(42, date(2026, 9, 25))   # без league.json — по games.json
        self.assertIn("Ближайшая: Сб 03.10, дома с «Белгород»", text)
        with mock.patch.object(bot, "SUBS", {}):
            self.assertNotIn("Ближайшая", bot.remind_text(42, date(2026, 9, 25)))
        with mock.patch.object(bot, "SUBS", {42: ["ryazan-vdv"]}):   # сезон кончился — без строки
            self.assertNotIn("Ближайшая", bot.remind_text(42, date(2027, 6, 1)))

    def test_link_with_team_subscribes_to_it(self):
        subs, _, stickers, text, kb = self.run_start("remind-ermak")
        self.assertEqual(subs, {42: ["ermak"]})
        self.assertEqual(stickers, ["bell"])
        self.assertIn("«Ермак» ✅ включены", text)


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
        rows = kb.inline_keyboard
        self.assertIn("Открыть РХЛ", rows[0][0].text)          # главная кнопка — первая и одна в ряду
        self.assertEqual(len(rows[0]), 1)
        self.assertEqual([[b.callback_data for b in r] for r in rows[1:]], [["d:today"]])   # вторым планом

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


def msk(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=bot.TZ)


def tags_balanced(text: str) -> bool:
    return all(text.count(f"<{t}") == text.count(f"</{t}>") for t in ("b", "a", "i"))


class Subscribers(unittest.TestCase):
    """subscribers.json — {chat_id: [команды]}; старый [chat_id] — «Рязань-ВДВ» (ADR-019, раздел 8)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "subscribers.json"
        p = mock.patch.object(bot, "SUBS_FILE", self.tmp)
        p.start()
        self.addCleanup(p.stop)

    def saved(self):
        return json.loads(self.tmp.read_text(encoding="utf-8"))

    def test_old_list_migrates_silently(self):
        self.tmp.write_text("[42, 7]")
        subs = bot.load_subs()
        self.assertEqual(subs, {42: ["ryazan-vdv"], 7: ["ryazan-vdv"]})
        self.assertEqual(self.saved(), {"7": ["ryazan-vdv"], "42": ["ryazan-vdv"]})   # файл уже в новом формате
        self.assertEqual(bot.load_subs(), subs)

    def test_new_format_cleaned(self):
        self.tmp.write_text(json.dumps({"42": ["ermak", "нет-такой", "ermak", "samara", "sokol", "proton"],
                                        "7": [], "x": ["ermak"], "5": "tambov"}))
        self.assertEqual(bot.load_subs(), {42: ["ermak", "samara", "sokol"], 5: ["tambov"]})
        self.assertEqual(self.saved(), {"5": ["tambov"], "42": ["ermak", "samara", "sokol"]})

    def test_clean_file_not_rewritten(self):
        self.tmp.write_text(json.dumps({"42": ["ermak"]}))
        with mock.patch.object(bot, "save_subs") as save:
            self.assertEqual(bot.load_subs(), {42: ["ermak"]})
        save.assert_not_called()

    def test_broken_or_missing_file(self):
        self.assertEqual(bot.load_subs(), {})
        self.tmp.write_text("{не json")
        self.assertEqual(bot.load_subs(), {})

    def test_up_to_three_teams(self):
        with mock.patch.object(bot, "SUBS", {}):
            self.assertEqual([bot.follow(42, t) for t in ("ermak", "samara", "sokol", "proton")],
                             ["on", "on", "on", "full"])
            self.assertEqual(bot.follow(42, "ermak"), "already")
            self.assertEqual(bot.toggle_team(42, "proton"), "full")
            self.assertEqual(bot.SUBS, {42: ["ermak", "samara", "sokol"]})
            self.assertEqual(bot.toggle_team(42, "samara"), "off")
            self.assertEqual(bot.toggle_team(42, "proton"), "on")
        self.assertEqual(self.saved(), {"42": ["ermak", "sokol", "proton"]})

    def test_off_forgets_chat_entirely(self):
        """Удаление данных (CLAUDE.md, правило 4): выключил — chat_id нет в файле."""
        with mock.patch.object(bot, "SUBS", {42: ["ermak"], 7: ["samara"]}):
            bot.toggle_team(42, "ermak")      # сняли последнюю команду
            self.assertEqual(self.saved(), {"7": ["samara"]})
            bot.unsubscribe(7)
            self.assertEqual(bot.SUBS, {})
        self.assertEqual(self.saved(), {})

    def test_remind_screen(self):
        with mock.patch.object(bot, "SUBS", {42: ["ermak", "samara"]}):
            games = [{"id": "a", "date": "2026-10-04", "home": "samara", "away": "ermak", "time": "16:00"},
                     {"id": "b", "date": "2026-10-02", "home": "ermak", "away": "sokol"}]
            text = bot.remind_text(42, date(2026, 10, 3), games)
            kb = bot.remind_kb(42).inline_keyboard
        self.assertIn("«Ермак» и «Самара» ✅ включены", text)
        self.assertIn("«Ермак»: Вс 04.10 в 16:00 МСК, в гостях с «Самара»", text)
        self.assertIn("«Самара»: Вс 04.10 в 16:00 МСК, дома с «Ермак»", text)
        self.assertEqual([b.callback_data for b in kb[0]], ["r:toggle", "t:home"])
        with mock.patch.object(bot, "SUBS", {}):
            self.assertIn("выключены", bot.remind_text(42))
            off = bot.remind_kb(42).inline_keyboard   # выключены — сразу выбор конференции
        self.assertEqual([b.callback_data for b in off[0]], ["t:c:west", "t:c:east"])


class TeamPicker(unittest.TestCase):
    """/team: конференции → команды по две в ряд, ✅ — уже напоминаю, не больше трёх."""

    def test_conferences_then_teams(self):
        with mock.patch.object(bot, "SUBS", {42: ["ermak"]}):
            kb = bot.team_kb(42).inline_keyboard
            rows = bot.team_kb(42, "east").inline_keyboard
            text = bot.team_text(42, "east")
        self.assertEqual([b.callback_data for b in kb[0]], ["t:c:west", "t:c:east"])
        self.assertEqual(kb[1][0].callback_data, "r:show")
        teams = [b for r in rows[:-1] for b in r]
        self.assertEqual(len(teams), sum(t["conf"] == "east" for t in bot.TEAM_LIST))
        self.assertTrue(all(len(r) <= 2 for r in rows))
        self.assertIn("✅ Ермак", [b.text for b in teams])
        self.assertIn("Самара", [b.text for b in teams])
        self.assertTrue(all(len(b.callback_data.encode()) <= 64 for r in rows for b in r))
        self.assertIn("Сейчас: «Ермак».", text)

    def run_cb(self, data, subs):
        c = mock.Mock()
        c.data = data
        c.message.chat.id = 42
        c.answer = mock.AsyncMock()
        with mock.patch.object(bot, "SUBS", subs), mock.patch.object(bot, "save_subs"), \
                mock.patch.object(bot, "safe_edit", mock.AsyncMock()) as edit, \
                mock.patch.object(bot, "send_sticker", mock.AsyncMock(return_value=True)) as sticker:
            asyncio.run(bot.cb_team(c))
            made = edit.call_args.args[1]() if edit.called else None
        return c, made, sticker

    def test_pick_and_unpick(self):
        subs = {}
        c, (text, kb), sticker = self.run_cb("t:s:ermak", subs)
        self.assertEqual(subs, {42: ["ermak"]})
        self.assertIn("Восток", text)
        self.assertIn("✅ Ермак", [b.text for r in kb.inline_keyboard for b in r])
        self.assertEqual(c.answer.call_args.args[0], "Напомню о матчах «Ермак»")
        sticker.assert_awaited_once()   # первая команда — стикер «Напомню!»
        c, _, sticker = self.run_cb("t:s:ermak", subs)
        self.assertEqual(subs, {})
        sticker.assert_not_awaited()

    def test_fourth_team_refused(self):
        subs = {42: ["ermak", "samara", "sokol"]}
        c, made, _ = self.run_cb("t:s:proton", subs)
        self.assertEqual(subs[42], ["ermak", "samara", "sokol"])
        self.assertIsNone(made)
        self.assertTrue(c.answer.call_args.kwargs.get("show_alert"))
        self.assertIn("до трёх", c.answer.call_args.args[0])

    def test_conference_screen(self):
        _, (text, kb), _ = self.run_cb("t:c:west", {})
        self.assertIn("Запад", text)
        self.assertIn("Арктика", [b.text for r in kb.inline_keyboard for b in r])


class Today(unittest.TestCase):
    """«Матчи сегодня» (ADR-019, раздел 8): все матчи дня, свои первыми, время, статус, ссылки."""

    league = {"games": [
        {"id": "n1", "date": "2026-10-03", "home": "ryazan-vdv", "away": "belgorod", "time": "17:00",
         "start": "2026-10-03T17:00:00+03:00", "online": "https://online.khl.ru/online/1.html",
         "watch": [{"title": "Трансляция", "url": "https://vk.com/video-1_2?a=1&b=2"}]},
        {"id": "rh2", "date": "2026-10-03", "home": "ermak", "away": "samara", "time": "12:00",
         "start": "2026-10-03T12:00:00+03:00", "local": "17:00",
         "score": {"home": 4, "away": 2, "decision": "", "periods": []}},
        {"id": "rh3", "date": "2026-10-03", "home": "sokol", "away": "proton", "time": "15:00"},
        {"id": "rh4", "date": "2026-10-03", "home": "tambov", "away": "rostov"},
        {"id": "rh5", "date": "2026-10-03", "home": "polet", "away": "arktika", "time": "18:30"},
        {"id": "rh6", "date": "2026-10-04", "home": "belgorod", "away": "ryazan-vdv", "time": "16:00"},
        {"id": "rh7", "date": "2026-10-05", "home": "kaluga", "away": "bryansk"},
    ]}
    live = {"date": "2026-10-03", "updated": "2026-10-03T17:41:00+03:00", "games": [
        {"key": "2026-10-03|ryazan-vdv|belgorod", "date": "2026-10-03", "home": "ryazan-vdv", "away": "belgorod",
         "status": "live", "period": "2", "clock": "12:34", "score": {"home": 2, "away": 1, "decision": None}},
        {"key": "2026-10-03|sokol|proton", "date": "2026-10-03", "home": "sokol", "away": "proton",
         "time": "15:05", "start": "2026-10-03T15:05:00+03:00",
         "status": "break", "period": "1", "score": {"home": 1, "away": 1, "decision": None}},
        # матча нет в календаре мини-аппа (r-hockey его потерял) — онлайн всё равно показывает
        {"key": "2026-10-03|dizelist|kristall", "date": "2026-10-03", "home": "dizelist", "away": "kristall",
         "time": "14:00", "status": "ended", "score": {"home": 3, "away": 2, "decision": "ОТ"}},
        {"key": "2026-10-03|ermak|samara", "date": "2026-10-03", "home": "ermak", "away": "samara",
         "status": "ended", "score": {"home": 9, "away": 9, "decision": None}},   # протокол главнее
    ]}
    now = msk("2026-10-03T17:42")

    def text(self, **kw):
        args = {"league": self.league, "live": self.live, "schedule": None, "fans": ["belgorod"], "now": self.now}
        return bot.today_text(**{**args, **kw})

    def test_order_fans_first_then_time(self):
        text = self.text()
        order = ["Рязань-ВДВ — Белгород", "Ермак — Самара", "Дизелист — Кристалл", "Сокол — Протон",
                 "Полёт — Арктика", "Тамбов — Ростов"]
        pos = [text.index(x) for x in order]
        self.assertEqual(pos, sorted(pos))
        self.assertIn("Матчи РХЛ сегодня · Сб 03.10", text)
        self.assertIn("⭐ <b>17:00</b> · Рязань-ВДВ — Белгород", text)
        self.assertNotIn("⭐ <b>12:00</b>", text)
        self.assertNotIn("Калужские Ракеты", text)    # другой день
        self.assertTrue(tags_balanced(text))

    def test_statuses(self):
        text = self.text()
        self.assertIn("Рязань-ВДВ — Белгород\nидёт · 2-й период · <b>2:1</b>", text)
        self.assertIn("<b>15:05</b> · Сокол — Протон\nперерыв · <b>1:1</b>", text)   # время онлайна главнее
        self.assertIn("<b>12:00</b> (местное 17:00) · Ермак — Самара\nокончен <b>4:2</b>", text)
        self.assertIn("Дизелист — Кристалл\nокончен <b>3:2</b> (ОТ)", text)
        self.assertIn("Полёт — Арктика\nчерез 48 мин", text)
        self.assertIn("время уточняется · Тамбов — Ростов", text)
        self.assertIn("Счёт — по онлайну лиги на 17:41.", text)

    def test_links_escaped(self):
        text = self.text()
        self.assertIn('<a href="https://online.khl.ru/online/1.html">текстовая трансляция</a>', text)
        self.assertIn('<a href="https://vk.com/video-1_2?a=1&amp;b=2">смотреть</a>', text)
        bad = {"games": [{"id": "x", "date": "2026-10-03", "home": "sokol", "away": "proton",
                          "online": "javascript:alert(1)", "watch": [{"url": "ftp://x"}]}]}
        self.assertNotIn("<a ", self.text(league=bad, live=None))

    def test_stale_live_shows_no_progress(self):
        stale = {**self.live, "updated": "2026-10-03T16:00:00+03:00"}
        text = self.text(live=stale)
        self.assertNotIn("идёт", text)
        self.assertNotIn("перерыв", text)
        self.assertIn("окончен <b>3:2</b> (ОТ)", text)   # итог онлайна остаётся
        self.assertNotIn("по онлайну лиги", text)
        other_day = {**self.live, "date": "2026-10-02"}   # служба встала вчера
        self.assertNotIn("идёт", self.text(live=other_day))

    def test_without_live_and_time(self):
        text = self.text(live=None, fans=[])
        self.assertIn("<b>17:00</b> · Рязань-ВДВ — Белгород\n"
                      '<a href="https://online.khl.ru/online/1.html">текстовая трансляция</a>', text)
        self.assertNotIn("⭐", text)
        self.assertNotIn("Дизелист", text)

    def test_no_games_today_shows_next_day(self):
        text = self.text(now=msk("2026-10-02T12:00"), live=None)
        self.assertIn("Сегодня матчей в РХЛ нет.", text)
        self.assertIn("Ближайшие — завтра, Сб 03.10", text)
        self.assertIn("⭐ <b>17:00</b> · Рязань-ВДВ — Белгород", text)
        self.assertNotIn("окончен <b>4:2</b>", bot.today_text(self.league, None, None, [], msk("2026-10-04T09:00")))
        text = self.text(now=msk("2026-10-04T20:00"))
        self.assertIn("Матчи РХЛ сегодня · Вс 04.10", text)
        self.assertIn("пока не видно", self.text(now=msk("2026-10-06T12:00")))

    def test_schedule_adds_time_for_next_day(self):
        schedule = {"games": [{"key": "2026-10-05|kaluga|bryansk", "date": "2026-10-05", "home": "kaluga",
                               "away": "bryansk", "time": "19:30", "start": "2026-10-05T19:30:00+03:00"}]}
        league = {"games": [g for g in self.league["games"] if g["id"] == "rh7"]}
        text = self.text(league=league, now=msk("2026-10-04T12:00"), live=None, schedule=schedule)
        self.assertIn("Ближайшие — завтра, Пн 05.10", text)
        self.assertIn("<b>19:30</b> · Калужские Ракеты — Брянск", text)

    def test_no_data(self):
        self.assertIn("Не получилось загрузить", bot.today_text(None, None, None, [], self.now))
        only_live = bot.today_text(None, self.live, None, [], self.now)   # Pages упали — живое есть
        self.assertIn("Дизелист — Кристалл", only_live)

    def test_local_time_from_arena_zone(self):
        start = msk("2026-10-03T17:00")
        self.assertEqual(bot.local_hm({"home": "ermak"}, start), "22:00")
        self.assertEqual(bot.local_hm({"home": "samara"}, start), "18:00")
        self.assertIsNone(bot.local_hm({"home": "ryazan-vdv"}, start))

    def test_keyboard(self):
        with mock.patch.object(bot, "SUBS", {}):
            kb = bot.today_kb(42).inline_keyboard
        self.assertIn("Открыть РХЛ", kb[0][0].text)
        self.assertEqual([b.callback_data for b in kb[1]], ["d:refresh", "r:open"])

    def test_words_lead_to_today(self):
        for t in ("когда игра?", "Какой счёт", "матчи сегодня"):
            self.assertTrue(bot.TODAY_WORDS.search(t), t)
        self.assertFalse(bot.TODAY_WORDS.search("привет"))


class RealDay(unittest.TestCase):
    """03.10.2026 на настоящих данных: сборка league.json по страницам rhl.fhr.ru и ответ сервера в 20:56.
    Владелец в 20:50: «время уточняется», «ждём протокол» и ни одного счёта, хотя на сайте лиги всё есть."""

    FIX = ROOT / "tests" / "fixtures"
    days = json.loads((FIX / "league_2026_10_03.json").read_text(encoding="utf-8"))
    live = json.loads((FIX / "live_today_2026_10_03_2056.json").read_text(encoding="utf-8"))
    PROTO = "https://rhl.fhr.ru/matchcenter/1432/{}/protocol/"

    def league(self, name):
        return {"teams": [], "games": self.days[name]}

    def lines(self, text):
        """«время · команды» → строка под ней."""
        rows = text.split("\n")
        return {rows[i].split(" · ", 1)[1]: rows[i + 1] for i in range(len(rows) - 1) if " — " in rows[i] and " · " in rows[i]}

    def test_evening_all_scores_and_protocols(self):
        text = bot.today_text(self.league("evening"), self.live, None, ["ryazan-vdv"], msk("2026-10-03T21:00"))
        self.assertNotIn("уточняется", text)
        self.assertNotIn("ждём протокол", text)
        self.assertNotIn("текстовая трансляция", text)   # после игры — протокол, а не трансляция
        self.assertIn("⭐ <b>17:00</b> · Рязань-ВДВ — Белгород", text)
        rows = self.lines(text)
        # видео — трансляция лиги с вкладки «Видео» матч-центра (rhl_media.py)
        self.assertEqual(rows["Рязань-ВДВ — Белгород"],
                         f'окончен <b>4:3</b> · <a href="{self.PROTO.format(905111)}">протокол</a>'
                         ' · <a href="https://vk.com/video-187307324_456239889">смотреть</a>')
        self.assertTrue(rows["Ростов — Краснодар"].startswith(
            f'окончен <b>0:6</b> · <a href="{self.PROTO.format(905113)}">протокол</a>'))
        self.assertIn("окончен <b>3:1</b>", rows["Тверичи-СШОР — Металлург"])
        self.assertIn("окончен <b>2:1</b>", rows["Протон — Кристалл"])
        order = [text.index(x) for x in ("Рязань-ВДВ — Белгород", "<b>13:00</b> · Ростов", "<b>15:00</b> · Тверичи",
                                         "<b>17:00</b> · Протон")]
        self.assertEqual(order, sorted(order))
        self.assertTrue(tags_balanced(text))

    def test_pages_lag_live_has_final(self):
        # часовая сборка ещё 17:55, а служба live в 20:56 уже знает итог и страницу протокола
        text = bot.today_text(self.league("at_1755"), self.live, None, [], msk("2026-10-03T21:00"))
        rows = self.lines(text)
        self.assertTrue(rows["Рязань-ВДВ — Белгород"].startswith(
            f'окончен <b>4:3</b> · <a href="{self.PROTO.format(905111)}">протокол</a>'))
        self.assertIn("Счёт — по сайту лиги на 20:56.", text)
        self.assertNotIn("онлайну", text)

    def test_live_from_site_snapshot(self):
        # служба live молчит — идущие матчи из снимка сайта лиги в league.json (17:51)
        text = bot.today_text(self.league("at_1755"), None, None, ["ryazan-vdv"], msk("2026-10-03T17:55"))
        rows = self.lines(text)
        self.assertTrue(rows["Рязань-ВДВ — Белгород"].startswith(
            'идёт · 2-й период · <b>2:0</b> · '
            '<a href="https://rhl.fhr.ru/matchcenter/1432/905111/live/">текстовая трансляция</a>'))
        self.assertTrue(rows["Протон — Кристалл"].startswith("идёт · 2-й период · <b>0:0</b>"))
        self.assertIn("Счёт по ходу — по сайту лиги на 17:51.", text)
        self.assertTrue(rows["Ростов — Краснодар"].startswith(
            f'окончен <b>0:6</b> · <a href="{self.PROTO.format(905113)}">протокол</a>'))
        # снимок старше 20 минут — хода матча не выдумываем
        late = bot.today_text(self.league("at_1755"), None, None, [], msk("2026-10-03T18:30"))
        self.assertNotIn("идёт", late)

    def test_final_by_site_without_protocol(self):
        out = bot.pending_results(self.league("evening"), [], set(), date(2026, 10, 3))
        g = next(x for x in out if x["id"] == "n1")
        self.assertEqual((g["live"], g["src"], g["protocol"]), (True, "rhl.fhr.ru", self.PROTO.format(905111)))
        text = bot.result_text(g, {}, team="ryazan-vdv", live=g["live"], src=g["src"], protocol=g["protocol"])
        self.assertIn("Победа!", text)
        self.assertIn("Рязань-ВДВ <b>4:3</b> Белгород\n<i>по данным сайта лиги</i>", text)
        self.assertIn(f'Протокол — <a href="{self.PROTO.format(905111)}">на сайте лиги</a>', text)
        self.assertNotIn("онлайна", text)
        self.assertTrue(tags_balanced(text))
        with mock.patch.object(bot, "WEBAPP_URL", "https://x.github.io/app/"):
            self.assertIn("Матч в приложении", bot.recap_kb("n1", recap=False).inline_keyboard[0][0].text)

    def test_final_with_site_protocol(self):
        out = bot.pending_results(self.league("evening_protocol"), [], set(), date(2026, 10, 3))
        g = next(x for x in out if x["id"] == "rh9319023")
        self.assertFalse(g["live"])
        text = bot.result_text(g, {}, team="kristall", live=g["live"], src=g["src"], protocol=g["protocol"])
        self.assertIn("Поражение по буллитам", text)
        self.assertIn("Протон <b>2:1</b> (Б) Кристалл", text)
        self.assertIn("Голы, ход матча и составы — по кнопке", text)
        self.assertNotIn("по данным", text)


class Reminder(unittest.TestCase):
    """Напоминание о матче (ADR-019, раздел 8; ADR-020, раздел 2): время, где, где смотреть, кнопки."""

    games = Today.league["games"]

    def match(self, gid, live=None):
        g = next(x for x in self.games if x["id"] == gid)
        return next(m for m in bot.day_matches(date.fromisoformat(g["date"]), {"games": self.games}, live)
                    if m["id"] == gid)

    def test_with_time_city_and_watch(self):
        text = bot.reminder_text(self.match("n1"), "today", "ryazan-vdv", self.games)
        self.assertIn("<b>Сегодня игра!</b>", text)
        self.assertIn("Рязань-ВДВ — <b>Белгород</b>", text)
        self.assertIn("Сб 03.10 · сегодня в 17:00 МСК", text)
        self.assertIn("🏠 Дома, Рязань", text)
        self.assertIn('<a href="https://vk.com/video-1_2?a=1&amp;b=2">Смотреть трансляцию</a>', text)
        self.assertTrue(tags_balanced(text))

    def test_without_time(self):
        text = bot.reminder_text(self.match("rh7"), "tomorrow", "bryansk", self.games)
        self.assertIn("<b>Завтра игра!</b>", text)
        self.assertIn("Брянск — <b>Калужские Ракеты</b>", text)
        self.assertIn("Пн 05.10 · завтра, время начала уточняется", text)
        self.assertIn("✈️ На выезде, Калуга", text)
        self.assertNotIn("Смотреть", text)

    def test_local_time_and_away(self):
        text = bot.reminder_text(self.match("rh2"), "today", "samara")
        self.assertIn("сегодня в 12:00 МСК · 17:00 по местному", text)
        self.assertIn("На выезде, Ангарск", text)

    def test_fan_of_both_teams(self):
        text = bot.reminder_text(self.match("n1"), "today", None)
        self.assertIn("<b>Рязань-ВДВ</b> — <b>Белгород</b>", text)
        self.assertIn("📍 Рязань", text)

    def test_warmup_line(self):
        played = [{**self.games[0], "score": {"home": 2, "away": 1, "decision": ""}},
                  {"id": "x", "date": "2026-10-02", "home": "sokol", "away": "belgorod",
                   "score": {"home": 0, "away": 3, "decision": ""}}]
        text = bot.reminder_text(self.match("rh6"), "today", "belgorod", played)
        self.assertIn("Прошлая встреча 03.10: Рязань-ВДВ <b>2:1</b> Белгород", text)
        form = bot.warmup_text({"date": "2026-10-05", "home": "belgorod", "away": "kaluga"}, "belgorod", played)
        self.assertEqual(form, "🔥 Форма «Белгород»: ✅ ❌")   # сначала старый матч
        self.assertEqual(bot.warmup_text(self.match("rh7"), "bryansk", played), "")

    def test_buttons(self):
        m = self.match("n1")
        with mock.patch.object(bot, "WEBAPP_URL", "https://x.github.io/app/"), \
                mock.patch.dict(os.environ, {"RASKAT_API": "https://rhl.example/api/raskat"}):
            kb = bot.match_kb(m).inline_keyboard
        self.assertIn("Кто победит?", kb[0][0].text)
        self.assertEqual(kb[0][0].web_app.url, "https://x.github.io/app/?match=n1")
        self.assertIn("Текстовая трансляция", kb[1][0].text)
        self.assertEqual(kb[1][0].url, "https://online.khl.ru/online/1.html")
        with mock.patch.dict(os.environ, {"RASKAT_API": "", "LIVE_API": ""}):
            kb = bot.match_kb(self.match("rh7")).inline_keyboard
        self.assertEqual(len(kb), 1)                       # онлайна нет — одна кнопка
        self.assertIn("Матч в приложении", kb[0][0].text)  # сервера прогнозов нет — без «Кто победит?»

    def test_plan_by_teams(self):
        subs = {1: ["belgorod"], 2: ["ryazan-vdv", "belgorod"], 3: ["ermak"], 4: ["kaluga"]}
        plan = bot.reminder_plan(subs, date(2026, 10, 3), {"games": self.games})
        self.assertEqual([(cid, m["id"], team) for cid, m, team in plan],
                         [(3, "rh2", "ermak"), (1, "n1", "belgorod"), (2, "n1", None)])

    def test_fallback_to_games_json(self):
        schedule = {"games": [{"key": "2026-10-03|ryazan-vdv|belgorod", "date": "2026-10-03",
                               "home": "ryazan-vdv", "away": "belgorod", "time": "17:00"}]}
        plan = bot.reminder_plan({1: ["ryazan-vdv"], 2: ["ermak"]}, date(2026, 10, 3), None, None, schedule)
        self.assertEqual([(cid, m["id"], m["away"]) for cid, m, _ in plan], [(1, "n1", "belgorod")])
        self.assertIn("сегодня в 17:00 МСК", bot.reminder_text(plan[0][1], "today", "ryazan-vdv"))
        plan = bot.reminder_plan({1: ["ryazan-vdv"]}, date(2026, 10, 3), None)
        self.assertIn("время начала уточняется", bot.reminder_text(plan[0][1], "today", "ryazan-vdv"))

    def test_send_one_sticker_per_chat(self):
        tmp = Path(tempfile.mkdtemp())
        err = bot.TelegramForbiddenError(method=mock.Mock(), message="bot was blocked")
        league = {"games": [*self.games, {"id": "z", "date": "2026-10-03", "home": "belgorod", "away": "sokol"}]}
        subs = {1: ["belgorod"], 5: ["ermak"]}
        with mock.patch.object(bot, "LIVE_DIR", tmp), mock.patch.object(bot, "SUBS", subs), \
                mock.patch.object(bot, "save_subs"), \
                mock.patch.object(bot, "send_sticker", mock.AsyncMock(return_value=True)) as sticker, \
                mock.patch.object(bot, "say", mock.AsyncMock(side_effect=[None, None, err])) as say, \
                mock.patch.object(bot.asyncio, "sleep", mock.AsyncMock()):
            sent, failed = asyncio.run(bot.send_reminders(mock.Mock(), "today", date(2026, 10, 3), league,
                                                          now=datetime(2026, 10, 3, 10, 0, tzinfo=bot.TZ)))
        self.assertEqual((sent, failed), (2, 1))
        self.assertEqual([c.args[1] for c in sticker.call_args_list], [5, 1])   # стикер — раз на человека
        self.assertEqual([c.args[1] for c in say.call_args_list], [5, 1, 1])
        self.assertEqual(subs, {5: ["ermak"]})   # заблокировал бота — подписка снята


class LiveFinal(unittest.TestCase):
    """Финал по онлайну (ADR-019, раздел 8): «окончен» 10 минут подряд, без дублей с протоколом."""

    key = "2026-10-03|ryazan-vdv|belgorod"

    def ended(self, h=4, a=2, status="ended"):
        return {"key": self.key, "date": "2026-10-03", "home": "ryazan-vdv", "away": "belgorod",
                "status": status, "score": {"home": h, "away": a, "decision": None}}

    def test_ten_minutes_in_a_row(self):
        seen, t0 = {}, msk("2026-10-03T19:30")
        self.assertEqual(bot.live_finals([self.ended()], seen, t0), [])
        self.assertEqual(bot.live_finals([self.ended()], seen, t0 + timedelta(minutes=9)), [])
        ready = bot.live_finals([self.ended()], seen, t0 + timedelta(minutes=10))
        self.assertEqual([(x["key"], x["score"]["home"]) for x in ready], [(self.key, 4)])

    def test_score_change_or_flicker_restarts(self):
        seen, t0 = {}, msk("2026-10-03T19:30")
        bot.live_finals([self.ended()], seen, t0)
        bot.live_finals([self.ended(4, 3)], seen, t0 + timedelta(minutes=5))      # счёт поправили
        self.assertEqual(bot.live_finals([self.ended(4, 3)], seen, t0 + timedelta(minutes=12)), [])
        self.assertTrue(bot.live_finals([self.ended(4, 3)], seen, t0 + timedelta(minutes=15)))
        seen = {}
        bot.live_finals([self.ended()], seen, t0)
        bot.live_finals([self.ended(status="live")], seen, t0 + timedelta(minutes=5))   # снова «идёт»
        bot.live_finals([self.ended()], seen, t0 + timedelta(minutes=6))
        self.assertEqual(bot.live_finals([self.ended()], seen, t0 + timedelta(minutes=12)), [])
        self.assertTrue(bot.live_finals([self.ended(status="final")], seen, t0 + timedelta(minutes=16)))

    def test_pending_prefers_protocol(self):
        ready = [{**self.ended(), "score": {"home": 4, "away": 2, "decision": ""}}]
        league = {"games": [{"id": "n1", "date": "2026-10-03", "home": "ryazan-vdv", "away": "belgorod"}]}
        out = bot.pending_results(league, ready, set(), date(2026, 10, 3))
        self.assertEqual([(g["id"], g["live"]) for g in out], [("n1", True)])
        played = {"games": [{**league["games"][0], "score": {"home": 4, "away": 2, "decision": "", "periods": []}}]}
        out = bot.pending_results(played, ready, set(), date(2026, 10, 3))
        self.assertEqual([(g["id"], g["live"]) for g in out], [("n1", False)])   # протокол, одно сообщение
        self.assertEqual(bot.pending_results(played, ready, {"n1"}, date(2026, 10, 3)), [])
        self.assertEqual(bot.pending_results(None, ready, {self.key}, date(2026, 10, 3)), [])

    def test_text(self):
        g = {"id": "n1", "home": "ryazan-vdv", "away": "belgorod", "score": {"home": 4, "away": 2, "decision": None}}
        text = bot.result_text(g, {}, team="belgorod", live=True)
        self.assertIn("Поражение", text)
        self.assertIn("Рязань-ВДВ <b>4:2</b> Белгород\n<i>по данным онлайна лиги</i>", text)
        self.assertIn("когда лига выложит протокол", text)
        self.assertIn("Матч окончен", bot.result_text(g, {}, team=None, live=True))
        self.assertTrue(tags_balanced(text))

    def run_steps(self, steps, subs, league, announced=()):
        """steps — [(время, live/<дата>.json или None, league.json)], возвращает вызовы say."""
        tmp = Path(tempfile.mkdtemp())
        ann = tmp / "announced.json"
        if announced is not None:
            ann.write_text(json.dumps(list(announced)))
        say = mock.AsyncMock()
        fetch = mock.AsyncMock(return_value={"story": "Камбэк."})
        with mock.patch.object(bot, "LIVE_DIR", tmp), mock.patch.object(bot, "ANNOUNCED_FILE", ann), \
                mock.patch.object(bot, "SUBS", subs), mock.patch.object(bot, "LIVE_ENDED", {}), \
                mock.patch.object(bot, "fetch_once", fetch), mock.patch.object(bot, "say", say), \
                mock.patch.object(bot, "WEBAPP_URL", "https://x.github.io/app/"), \
                mock.patch.object(bot.asyncio, "sleep", mock.AsyncMock()):
            counts = []
            for now, live, lg in steps:
                f = tmp / "2026-10-03.json"
                if live is None:
                    f.unlink(missing_ok=True)
                else:
                    f.write_text(json.dumps(live, ensure_ascii=False), encoding="utf-8")
                with mock.patch.object(bot, "published_league", mock.AsyncMock(return_value=lg or league)):
                    counts.append(asyncio.run(bot.results_step(mock.Mock(), now)))
            made = [(c.args[1], c.args[2]()) for c in say.call_args_list]
            saved = json.loads(ann.read_text()) if ann.exists() else None
        return counts, made, saved, fetch

    def test_live_final_then_protocol_without_duplicate(self):
        live = {"date": "2026-10-03", "updated": "2026-10-03T19:30:00+03:00", "games": [self.ended()]}
        league = {"teams": [], "games": [{"id": "n1", "date": "2026-10-03", "home": "ryazan-vdv", "away": "belgorod"}]}
        played = {"teams": [], "games": [{**league["games"][0],
                                          "score": {"home": 4, "away": 2, "decision": "", "periods": []}}]}
        subs = {1: ["ryazan-vdv"], 2: ["samara"], 3: ["belgorod"]}
        t0 = msk("2026-10-03T19:30")
        counts, made, saved, fetch = self.run_steps([
            (t0, live, None),
            (t0 + timedelta(minutes=9), live, None),
            (t0 + timedelta(minutes=10), live, None),
            (t0 + timedelta(minutes=11), live, None),
            (t0 + timedelta(minutes=40), live, played),     # пришёл протокол
        ], subs, league)
        self.assertEqual(counts, [0, 0, 2, 0, 0])
        self.assertEqual([cid for cid, _ in made], [1, 3])   # болельщику «Самары» — ничего
        (_, (win, kb)), (_, (loss, _)) = made
        self.assertIn("Победа!", win)
        self.assertIn("по данным онлайна лиги", win)
        self.assertIn("Поражение", loss)
        self.assertEqual(kb.inline_keyboard[0][0].web_app.url, "https://x.github.io/app/?match=n1")   # «Как это было»
        self.assertEqual(set(saved), {"n1", self.key})
        fetch.assert_not_awaited()   # разбора по онлайну нет

    def test_protocol_first_then_live_silent(self):
        live = {"date": "2026-10-03", "updated": "2026-10-03T19:30:00+03:00", "games": [self.ended()]}
        played = {"teams": [], "games": [{"id": "n1", "date": "2026-10-03", "home": "ryazan-vdv", "away": "belgorod",
                                          "score": {"home": 4, "away": 2, "decision": "", "periods": []}}]}
        t0 = msk("2026-10-03T19:30")
        counts, made, saved, _ = self.run_steps([(t0, live, None), (t0 + timedelta(minutes=15), live, None)],
                                                {1: ["ryazan-vdv"]}, played)
        self.assertEqual(counts, [1, 0])
        self.assertIn("Камбэк.", made[0][1][0])
        self.assertNotIn("онлайна", made[0][1][0])

    def test_quiet_night_and_first_run(self):
        live = {"date": "2026-10-03", "updated": "2026-10-03T23:00:00+03:00", "games": [self.ended()]}
        league = {"teams": [], "games": []}
        t0 = msk("2026-10-03T23:10")
        counts, made, saved, _ = self.run_steps([(t0, live, None), (t0 + timedelta(minutes=20), live, None)],
                                                {1: ["ryazan-vdv"]}, league)
        self.assertEqual((counts, made, saved), ([0, 0], [], []))   # ночью молчим, утром уйдёт
        counts, made, saved, _ = self.run_steps([(t0, live, None)], {1: ["ryazan-vdv"]}, league, announced=None)
        self.assertEqual((counts, made, saved), ([0], [], [self.key]))   # первый запуск — только запомнили


class Catchup(unittest.TestCase):
    """Пропущенное напоминание: бот стоял в свой час — догоняет, но не пишет дважды."""

    games = [{"id": "n1", "date": "2026-10-03", "home": "ryazan-vdv", "away": "belgorod", "time": "17:00"}]

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.file = mock.patch.object(bot, "REMINDED_FILE", self.dir / "reminded.json")
        self.file.start()
        self.addCleanup(self.file.stop)

    def at(self, hhmm: str, day="2026-10-03"):
        return datetime.fromisoformat(f"{day}T{hhmm}:00+03:00")

    def test_slot_due_until_catchup_window_ends(self):
        day = date(2026, 10, 3)
        self.assertEqual(bot.due_slots(self.at("10:00"), {}), [(day, "today")])
        self.assertEqual(bot.due_slots(self.at("12:59"), {}), [(day, "today")])
        self.assertEqual(bot.due_slots(self.at("13:01"), {}), [])        # уже поздно напоминать
        self.assertEqual(bot.due_slots(self.at("09:59"), {}), [])        # ещё не время
        self.assertEqual(bot.due_slots(self.at("19:30"), {}), [(day, "tomorrow")])

    def test_done_and_tried_slots_are_left_alone(self):
        done = {"2026-10-03:today": {"done": True, "tries": 1, "sent": []}}
        self.assertEqual(bot.due_slots(self.at("10:05"), done), [])
        part = {"2026-10-03:today": {"done": False, "tries": 1, "sent": ["1|x"]}}
        self.assertEqual(bot.due_slots(self.at("10:05"), part), [(date(2026, 10, 3), "today")])
        part["2026-10-03:today"]["tries"] = bot.REMIND_TRIES
        self.assertEqual(bot.due_slots(self.at("10:05"), part), [])      # хватит, иначе это спам

    def test_night_is_silent(self):
        self.assertEqual(bot.due_slots(self.at("23:30"), {}), [])
        self.assertEqual(bot.due_slots(self.at("08:00", "2026-10-04"), {}), [])

    def test_file_survives_restart_and_keeps_three_days(self):
        bot.save_reminded({"2026-10-03:today": {"done": True, "tries": 1, "sent": ["1|k"]},
                           "2026-09-20:today": {"done": True, "tries": 1, "sent": ["2|k"]}},
                          date(2026, 10, 3))
        self.assertEqual(sorted(bot.load_reminded()), ["2026-10-03:today"])   # старое не храним
        bot.REMINDED_FILE.write_text("{мусор")
        self.assertIsNone(bot.load_reminded())        # испорчен — как первый запуск, не догоняем

    def test_first_run_does_not_repeat_the_previous_copy(self):
        """Файла нет: прошедший слот мог разослать прежний бот — считаем закрытым."""
        self.assertIsNone(bot.load_reminded())
        base = bot.first_run_reminded(self.at("10:30"))
        self.assertEqual(base, {"2026-10-03:today": {"done": True, "tries": 0, "sent": []}})
        self.assertEqual(bot.due_slots(self.at("10:35"), base), [])
        self.assertEqual(bot.first_run_reminded(self.at("09:30")), {})   # час ещё не пришёл — напомним

    def run_fire(self, now, say, subs=None, reminded=None):
        """Один проход fire_reminder с подменённой отправкой."""
        reminded = {} if reminded is None else reminded
        with mock.patch.object(bot, "REMINDED", reminded), \
                mock.patch.object(bot, "SUBS", subs if subs is not None else {1: ["ryazan-vdv"], 2: ["belgorod"]}), \
                mock.patch.object(bot, "save_subs"), mock.patch.object(bot, "LIVE_DIR", self.dir), \
                mock.patch.object(bot, "published_league", mock.AsyncMock(return_value={"games": self.games})), \
                mock.patch.object(bot, "send_sticker", mock.AsyncMock(return_value=True)), \
                mock.patch.object(bot, "say", say), \
                mock.patch.object(bot.asyncio, "sleep", mock.AsyncMock()):
            sent = asyncio.run(bot.fire_reminder(mock.Mock(), date(2026, 10, 3), "today", now))
        return sent, reminded["2026-10-03:today"]

    def test_catchup_writes_only_the_ones_left(self):
        err = RuntimeError("туннель лёг")
        sent, rec = self.run_fire(self.at("10:00"), mock.AsyncMock(side_effect=[None, err]))
        self.assertEqual(sent, 1)
        self.assertEqual((rec["done"], rec["sent"]), (False, ["1|2026-10-03|ryazan-vdv|belgorod"]))
        again = mock.AsyncMock()
        sent, rec = self.run_fire(self.at("10:10"), again, reminded={"2026-10-03:today": rec})
        self.assertEqual((sent, [c.args[1] for c in again.call_args_list]), (1, [2]))   # только второму
        self.assertEqual(rec["done"], True)
        self.assertEqual(rec["tries"], 2)

    def test_whole_broadcast_failed_stays_open(self):
        sent, rec = self.run_fire(self.at("10:00"), mock.AsyncMock(side_effect=RuntimeError("туннель лёг")))
        self.assertEqual((sent, rec["done"], rec["sent"]), (0, False, []))
        self.assertEqual(bot.due_slots(self.at("10:05"), {"2026-10-03:today": rec}), [(date(2026, 10, 3), "today")])

    def test_started_match_is_not_announced_late(self):
        sent, rec = self.run_fire(self.at("17:30"), mock.AsyncMock())   # игра в 17:00 уже началась
        self.assertEqual((sent, rec["done"]), (0, True))


class Flood(unittest.TestCase):
    """429 «too many requests»: Telegram просит паузу — ждём и дописываем, а не теряем сообщение."""

    def err(self, retry_after=3):
        return bot.TelegramRetryAfter(method=mock.Mock(), message="too many", retry_after=retry_after)

    def test_say_waits_and_sends(self):
        tg = mock.Mock(send_message=mock.AsyncMock(side_effect=[self.err(3), None]))
        with mock.patch.object(bot.asyncio, "sleep", mock.AsyncMock()) as slept:
            asyncio.run(bot.say(tg, 7, lambda: ("текст", None)))
        self.assertEqual(tg.send_message.await_count, 2)
        self.assertEqual(slept.await_args.args, (3,))

    def test_too_long_wait_is_an_error(self):
        tg = mock.Mock(send_message=mock.AsyncMock(side_effect=self.err(bot.RETRY_WAIT_MAX + 1)))
        with mock.patch.object(bot.asyncio, "sleep", mock.AsyncMock()), \
                self.assertRaises(bot.TelegramRetryAfter):
            asyncio.run(bot.say(tg, 7, lambda: ("текст", None)))
        self.assertEqual(tg.send_message.await_count, 1)   # ждать две минуты на одном чате не станем

    def test_sticker_waits_too(self):
        tg = mock.Mock(send_sticker=mock.AsyncMock(side_effect=[self.err(1), mock.Mock()]))
        with mock.patch.object(bot.asyncio, "sleep", mock.AsyncMock()):
            self.assertTrue(asyncio.run(bot.send_sticker(tg, 7, "gameday")))
        self.assertEqual(tg.send_sticker.await_count, 2)


class StateFiles(unittest.TestCase):
    """Файлы состояния: запись целиком или никак (ADR-003 — состояние в JSON рядом с ботом)."""

    def test_atomic_write_leaves_no_half_file(self):
        d = Path(tempfile.mkdtemp())
        path = d / "state.json"
        bot.write_atomic(path, {"a": [1, 2]})
        self.assertEqual(json.loads(path.read_text()), {"a": [1, 2]})
        with mock.patch.object(bot.json, "dump", side_effect=RuntimeError("диск кончился")):
            with self.assertRaises(RuntimeError):
                bot.write_atomic(path, {"b": 1})
        self.assertEqual(json.loads(path.read_text()), {"a": [1, 2]})   # прежний файл цел
        self.assertEqual(sorted(x.name for x in d.iterdir()), ["state.json", "state.json.tmp"])

    def test_broken_announced_is_a_first_run(self):
        d = Path(tempfile.mkdtemp())
        with mock.patch.object(bot, "ANNOUNCED_FILE", d / "announced.json"):
            self.assertIsNone(bot.load_announced())
            bot.save_announced({"2026-10-03|a|b"})
            self.assertEqual(bot.load_announced(), {"2026-10-03|a|b"})
            bot.ANNOUNCED_FILE.write_text("{оборвалось")
            self.assertIsNone(bot.load_announced())   # не рассылаем всё заново


if __name__ == "__main__":
    unittest.main()

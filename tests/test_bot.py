"""Онбординг бота (ADR-005): тексты, кнопки и стикеры — без Telegram и без токена."""
import asyncio
import json
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


class Zveno(unittest.TestCase):
    """ADR-014, Пролог: лист ожидания «Звена» в боте и один зов, когда рынок откроется."""

    OPEN = {"season": "2026/27", "status": "open", "market_opened_at": "2026-10-11T10:17:00+03:00",
            "first_tour": 1, "tour_now": None, "tour_next": 1,
            "tours": [{"t": 1, "from": "2026-10-12", "to": "2026-10-18",
                       "deadline": "2026-10-12T09:00:00+03:00", "close": "2026-10-22T12:00:00+03:00"}]}
    PROLOG = {**OPEN, "status": "prolog", "market_opened_at": None, "first_tour": None}
    DAY = datetime(2026, 10, 11, 12, 0, tzinfo=bot.TZ)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.file = Path(tmp.name) / "zveno_waitlist.json"
        self.stickers = mock.AsyncMock(return_value=True)
        for p in (mock.patch.object(bot, "ZVENO_FILE", self.file),
                  mock.patch.object(bot, "ZVENO", {"chats": set(), "opened": None}),
                  mock.patch.object(bot, "_published", {}),
                  mock.patch.object(bot, "SUBS", set()),
                  mock.patch.object(bot, "save_subs"),
                  mock.patch.object(bot, "send_sticker", self.stickers),
                  mock.patch.object(bot, "WEBAPP_URL", "https://x.github.io/app/")):
            p.start()
            self.addCleanup(p.stop)

    def saved(self) -> dict:
        return json.loads(self.file.read_text())

    def message(self):
        m = mock.Mock()
        m.chat.id = 42
        m.answer = mock.AsyncMock()
        return m

    def run_handler(self, handler, *args):
        """Хендлер сообщения → (текст, клавиатура) последнего ответа."""
        say = mock.AsyncMock()
        with mock.patch.object(bot, "say", say):
            asyncio.run(handler(self.message(), *args))
        return say.call_args.args[2]()

    def start(self, args):
        return self.run_handler(bot.start, bot.CommandObject(prefix="/", command="start", args=args))

    def command(self):
        return self.run_handler(bot.h_zveno)

    def press(self, data):
        c = mock.Mock()
        c.message.chat.id = 42
        c.data = data
        c.answer = mock.AsyncMock()
        edit = mock.AsyncMock()
        with mock.patch.object(bot, "safe_edit", edit):
            asyncio.run(bot.cb_zveno(c))
        return edit.call_args.args[1]()

    def published(self, tours):
        bot._published[bot.TOURS] = (datetime.now(bot.TZ), tours)

    # ---------- лист ожидания ----------

    def test_link_joins_without_greeting(self):
        text, kb = self.start("zveno")
        self.assertEqual(bot.ZVENO["chats"], {42})
        self.assertEqual(self.saved()["chats"], [42])
        self.assertEqual([c.args[2] for c in self.stickers.call_args_list], ["bell"])   # «Напомню!», не «Здарова!»
        self.assertIn("«Звено» — фэнтези РХЛ", text)
        self.assertIn("Ты в списке: позову", text)
        self.assertEqual(kb.inline_keyboard[0][0].web_app.url, "https://x.github.io/app/?startapp=zveno")
        self.assertEqual((kb.inline_keyboard[1][0].text, kb.inline_keyboard[1][0].callback_data),
                         ("🔕 Больше не звать", "z:off"))

    def test_link_then_button_leaves(self):
        _, kb = self.start("zveno")
        text, kb = self.press(kb.inline_keyboard[1][0].callback_data)
        self.assertEqual(bot.ZVENO["chats"], set())
        self.assertEqual(self.saved()["chats"], [])
        self.assertIn("Не позову", text)
        self.assertEqual((kb.inline_keyboard[1][0].text, kb.inline_keyboard[1][0].callback_data),
                         ("🔔 Позвать, когда откроется", "z:on"))

    def test_command_joins_and_leaves(self):
        text, kb = self.command()
        self.assertEqual(bot.ZVENO["chats"], {42})
        self.assertIn("Ты в списке", text)
        self.command()   # второй /zveno не выключает: человек просто проверяет
        self.assertEqual(bot.ZVENO["chats"], {42})
        _, kb = self.command()
        self.assertIn("Больше не звать", kb.inline_keyboard[1][0].text)
        self.press("z:off")
        self.assertEqual(bot.ZVENO["chats"], set())
        text, _ = self.press("z:on")
        self.assertEqual(bot.ZVENO["chats"], {42})
        self.assertIn("Ты в списке", text)

    def test_stale_button_does_not_rejoin(self):
        self.command()
        self.press("z:off")
        self.press("z:off")   # та же кнопка в старом сообщении
        self.assertEqual(bot.ZVENO["chats"], set())

    def test_already_open_goes_straight_to_app(self):
        self.published(self.OPEN)
        bot.ZVENO["chats"].add(42)
        text, kb = self.command()
        self.assertEqual(bot.ZVENO["chats"], set())   # зовать больше некого: уже знает
        self.assertIn("«Звено» уже открыто!", text)
        self.assertEqual(len(kb.inline_keyboard), 1)
        self.assertEqual(kb.inline_keyboard[0][0].web_app.url, "https://x.github.io/app/?startapp=zveno")
        text, _ = self.press("z:on")   # кнопка из Пролога после открытия
        self.assertEqual(bot.ZVENO["chats"], set())
        self.assertIn("уже открыто", text)

    def test_prolog_published_still_joins(self):
        self.published(self.PROLOG)
        self.start("zveno")
        self.assertEqual(bot.ZVENO["chats"], {42})

    def test_called_season_stays_open(self):
        """Позвали, а движок вернул prolog — в лист не пишем: второй раз звать не будем."""
        bot.ZVENO["opened"] = "2026/27"
        self.published(self.PROLOG)
        text, _ = self.command()
        self.assertEqual(bot.ZVENO["chats"], set())
        self.assertIn("уже открыто", text)
        self.published({**self.PROLOG, "season": "2027/28"})   # новый сезон — снова Пролог
        self.command()
        self.assertEqual(bot.ZVENO["chats"], {42})

    # ---------- «Звено» открылось ----------

    def announce(self, tours, now=None, blocked=()):
        sent = []

        async def fake_say(b, cid, make):
            if cid in blocked:
                raise bot.TelegramForbiddenError(method=mock.Mock(), message="Forbidden: bot was blocked by the user")
            sent.append((cid, *make()))

        with mock.patch.object(bot, "say", fake_say):
            n = asyncio.run(bot.announce_zveno(mock.Mock(), tours, now or self.DAY))
        self.assertEqual(n, len(sent))
        return sent

    def test_opening_calls_once_and_clears_list(self):
        bot.ZVENO["chats"].update({1, 2, 3})
        bot.SUBS.update({2, 5})
        sent = self.announce(self.OPEN, blocked={2})
        self.assertEqual([cid for cid, _, _ in sent], [1, 3])
        _, text, kb = sent[0]
        self.assertIn("«Звено» открылось!", text)
        self.assertIn("Как обещал — зову.", text)
        self.assertIn("Состав на тур 1 — до Пн 12.10, 09:00 (МСК).", text)
        self.assertEqual(len(kb.inline_keyboard), 1)
        self.assertEqual(kb.inline_keyboard[0][0].web_app.url, "https://x.github.io/app/?startapp=zveno")
        self.assertEqual(bot.ZVENO, {"chats": set(), "opened": "2026/27"})
        self.assertEqual(self.saved(), {"chats": [], "opened": "2026/27"})
        self.assertEqual(bot.SUBS, {5})   # заблокировал бота — и из напоминаний
        self.assertEqual(self.announce(self.OPEN), [])   # следующий опрос через 10 минут

    def test_restart_does_not_call_again(self):
        bot.ZVENO["chats"].add(1)
        self.announce(self.OPEN)
        self.file.write_text(json.dumps({"chats": [7], "opened": "2026/27"}))
        with mock.patch.object(bot, "ZVENO", bot.load_zveno()):   # рестарт: состояние из файла
            self.assertEqual(bot.ZVENO, {"chats": {7}, "opened": "2026/27"})
            self.assertEqual(self.announce(self.OPEN), [])
            self.assertTrue(bot.zveno_open(None))   # tours.json ещё не скачан — верим флагу

    def test_prolog_calls_nobody(self):
        bot.ZVENO["chats"].add(1)
        self.assertEqual(self.announce(self.PROLOG), [])
        self.assertEqual(self.announce(None), [])   # файл не скачался
        self.assertEqual(bot.ZVENO, {"chats": {1}, "opened": None})

    def test_night_waits_for_morning(self):
        bot.ZVENO["chats"].add(1)
        self.assertEqual(self.announce(self.OPEN, datetime(2026, 10, 11, 23, 30, tzinfo=bot.TZ)), [])
        self.assertEqual(bot.ZVENO["chats"], {1})
        sent = self.announce(self.OPEN, datetime(2026, 10, 12, 9, 0, tzinfo=bot.TZ))
        self.assertEqual([cid for cid, _, _ in sent], [1])
        self.assertNotIn("Состав на тур", sent[0][1])   # дедлайн 09:00 уже прошёл

    def test_loop_reads_published_tours_with_cache(self):
        fetch = mock.AsyncMock(return_value=self.OPEN)
        with mock.patch.object(bot, "fetch_json", fetch):
            self.assertEqual(asyncio.run(bot.published(bot.TOURS, bot.ZVENO_TTL)), self.OPEN)
            self.assertEqual(asyncio.run(bot.published(bot.TOURS, bot.ZVENO_TTL)), self.OPEN)
        fetch.assert_called_once()   # второй раз — из кэша на 10 минут
        self.assertEqual(fetch.call_args.args[1], "zveno/tours.json")
        self.assertEqual(bot.data_url(bot.TOURS), "https://x.github.io/app/data/zveno/tours.json")
        self.assertEqual(bot.last_published(bot.TOURS), self.OPEN)

    # ---------- тексты и файл ----------

    def test_texts_valid_html_and_words(self):
        """Словарь ADR-014, раздел 11: никаких «купить», «продать», «цена»."""
        bot.ZVENO["chats"].add(42)
        texts = [bot.zveno_wait_text(42), bot.zveno_wait_text(1),
                 bot.zveno_open_text(self.OPEN, self.DAY, called=True), bot.zveno_open_text(None, self.DAY)]
        for t in texts:
            self.assertEqual(len(re.findall(r"<b>", t)), len(re.findall(r"</b>", t)), t)
            for word in ("купи", "прода", "цен", "подешев"):
                self.assertNotIn(word, t.lower(), t)

    def test_deadline_only_if_ahead(self):
        self.assertIsNone(bot.tour_deadline(self.OPEN, datetime(2026, 10, 12, 9, 0, tzinfo=bot.TZ)))
        self.assertIsNone(bot.tour_deadline({"tour_next": 2, "tours": self.OPEN["tours"]}, self.DAY))
        self.assertIsNone(bot.tour_deadline({"tour_next": 1, "tours": [{"t": 1, "deadline": "потом"}]}, self.DAY))
        t, d = bot.tour_deadline(self.OPEN, self.DAY)
        self.assertEqual((t, d), (1, datetime(2026, 10, 12, 9, 0, tzinfo=bot.TZ)))

    def test_broken_file_is_empty_list(self):
        for raw in ("", "[1, 2]", "{\"chats\": 5}", "не json"):
            self.file.write_text(raw)
            self.assertEqual(bot.load_zveno(), {"chats": set(), "opened": None}, raw)

    def test_atomic_write(self):
        bot.write_json(self.file, {"chats": [1], "opened": None})
        self.assertEqual(self.saved(), {"chats": [1], "opened": None})
        self.assertEqual([p.name for p in self.file.parent.iterdir()], ["zveno_waitlist.json"])

    def test_waitlist_not_in_git(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
        self.assertIn("zveno_waitlist.json", ignored)

    def test_greeting_mentions_zveno(self):
        self.assertIn("«Звено»", bot.welcome_text())
        self.assertIn("«Звено»", bot.DESCRIPTION)


if __name__ == "__main__":
    unittest.main()

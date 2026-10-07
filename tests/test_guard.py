"""Сторож вне сервера (ADR-034): что он замечает, когда молчит и как не повторяется."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import guard  # noqa: E402

TZ = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=TZ)
API = "https://1-2-3-4.sslip.io/api"
PAGES = "https://arpicasso.github.io/RHL-BOT"
LEAGUE = f"{PAGES}/data/league.json"
HEALTH = {"code": 200, "body": json.dumps({"ok": True, "raskat": {"on": True}})}


def answers(**by_url):
    """Поддельный запрос: адрес → ответ. Чего нет — «не ответил»."""
    def fetch(url, token=""):
        fetch.asked.append(url)
        got = by_url.get(url, {"error": "TimeoutError: нет ответа"})
        return got(token) if callable(got) else got
    fetch.asked = []
    return fetch


def league_body(at: datetime) -> dict:
    return {"code": 200, "body": json.dumps({"updated": at.isoformat(timespec="minutes"), "games": []})}


class Look(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(guard.time, "sleep")
        p.start()
        self.addCleanup(p.stop)

    def test_dead_server_is_a_problem(self):
        fetch = answers(**{LEAGUE: league_body(NOW)})
        got = guard.look(API, PAGES, NOW, fetch=fetch)
        self.assertEqual([p["key"] for p in got], ["api"])
        self.assertIn("Сервер не отвечает", got[0]["text"])
        self.assertEqual(fetch.asked.count(f"{API}/health"), guard.TRIES)   # промах бывает: спрашиваем трижды

    def test_one_answer_is_enough(self):
        tries = {"n": 0}

        def health(token):
            tries["n"] += 1
            return HEALTH if tries["n"] == guard.TRIES else {"error": "TimeoutError: нет ответа"}
        fetch = answers(**{f"{API}/health": health, LEAGUE: league_body(NOW)})
        self.assertEqual(guard.look(API, PAGES, NOW, fetch=fetch), [])

    def test_not_our_answer(self):
        fetch = answers(**{f"{API}/health": {"code": 200, "body": "<html>Caddy</html>"}, LEAGUE: league_body(NOW)})
        got = guard.look(API, PAGES, NOW, fetch=fetch)
        self.assertEqual([p["key"] for p in got], ["api:health"])

    def test_stale_mini_app(self):
        old = league_body(NOW - timedelta(hours=3))
        fetch = answers(**{f"{API}/health": HEALTH, LEAGUE: old})
        got = guard.look(API, PAGES, NOW, fetch=fetch)
        self.assertEqual([p["key"] for p in got], ["pages:stale"])
        self.assertIn("180 мин назад", got[0]["text"])
        night = NOW.replace(hour=3)   # ночью сборка редкая — не тревога (ADR-022)
        self.assertEqual(guard.look(API, PAGES, night, fetch=answers(**{f"{API}/health": HEALTH, LEAGUE: old})), [])
        gone = answers(**{f"{API}/health": HEALTH})
        self.assertEqual([p["key"] for p in guard.look(API, PAGES, NOW, fetch=gone)], ["pages"])

    def test_silent_bot_seen_from_outside(self):
        """Пульс бота — из пульта агента: мёртвый бот тревогу о себе не пришлёт, это говорит сторож."""
        pult = {"code": 200, "body": json.dumps({"pult": {"problems": [
            {"level": "bad", "key": "bot:beat", "text": "Бот молчит: последний пульс 20 минут назад"},
            {"level": "bad", "key": "clips:beat", "text": "Служба клипов молчит"}]}})}
        fetch = answers(**{f"{API}/health": HEALTH, f"{API}/agent/status": pult, LEAGUE: league_body(NOW)})
        got = guard.look(API, PAGES, NOW, token="токен-агента-длиной-больше-тридцати-двух", fetch=fetch)
        self.assertEqual([p["key"] for p in got], ["pult:bot:beat"])   # про службы скажет бот, он живой
        self.assertIn("Бот молчит", got[0]["text"])

    def test_no_token_no_pult(self):
        fetch = answers(**{f"{API}/health": HEALTH, LEAGUE: league_body(NOW)})
        guard.look(API, PAGES, NOW, fetch=fetch)
        self.assertNotIn(f"{API}/agent/status", fetch.asked)


class Letters(unittest.TestCase):
    """Письма: новая поломка — сразу, та же — не чаще раза в час, прошла — «Починилось». Ночью молчим."""

    def setUp(self):
        p = mock.patch.object(guard.time, "sleep")
        p.start()
        self.addCleanup(p.stop)
        self.dir = Path(tempfile.mkdtemp())
        self.sent = []

    def run_once(self, now: datetime, fetch) -> dict:
        state = guard.run(api=API, pages="", token="", chats=[1001], bot_token="тк", now=now,
                             state_file=self.dir / "state.json", fetch=fetch,
                             post=lambda url, data: self.sent.append(data["text"]))
        (self.dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
        return state

    def test_broke_then_quiet_then_fixed(self):
        dead = answers()
        self.run_once(NOW, dead)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("🔴 <b>Сломалось</b>", self.sent[0])
        self.assertIn("Сторож вне сервера", self.sent[0])
        self.run_once(NOW + timedelta(minutes=15), dead)
        self.assertEqual(len(self.sent), 1)                     # та же поломка — не чаще раза в час
        self.run_once(NOW + timedelta(hours=1, minutes=5), dead)
        self.assertEqual(len(self.sent), 2)
        self.assertIn("🔴 <b>Не починилось</b>", self.sent[1])
        self.assertIn("(с 12:00)", self.sent[1])                # видно, с какого часа лежит
        self.run_once(NOW + timedelta(hours=2), answers(**{f"{API}/health": HEALTH}))
        self.assertIn("✅ <b>Починилось</b>", self.sent[2])
        self.assertEqual(json.loads((self.dir / "state.json").read_text()), {"said": {}, "seen": {}})

    def test_night_is_silent_and_morning_tells(self):
        dead = answers()
        self.run_once(NOW.replace(hour=3), dead)
        self.assertEqual(self.sent, [])                         # 2:00–7:00 МСК — не будим
        self.run_once(NOW.replace(hour=7, minute=1), dead)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("🔴 <b>Сломалось</b>", self.sent[0])
        self.assertIn("(с 03:00)", self.sent[0])                # ночь не потеряна: лежит с трёх

    def test_undelivered_letter_is_not_remembered(self):
        """Ревью PR #141: письмо не дошло ни до кого (Telegram молчит) — не помним, что сказали, иначе поломка
        потеряется: следующий обход считал бы её уже рассказанной."""
        def dead(url, data):
            raise RuntimeError("Connection reset by peer")
        state = guard.run(api=API, pages="", token="", chats=[1001], bot_token="тк", now=NOW,
                          state_file=self.dir / "state.json", fetch=answers(), post=dead)
        self.assertEqual(state["said"], {})
        (self.dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
        self.run_once(NOW + timedelta(minutes=15), answers())
        self.assertEqual(len(self.sent), 1)                     # дошло со второго раза — и как «Сломалось»
        self.assertIn("🔴 <b>Сломалось</b>", self.sent[0])

    def test_nobody_to_write_to(self):
        state = guard.run(api=API, pages="", token="", chats=[], bot_token="", now=NOW,
                             state_file=self.dir / "state.json", fetch=answers(),
                             post=lambda url, data: self.sent.append(data["text"]))
        self.assertEqual(self.sent, [])
        self.assertEqual(state["said"], {})                     # не сказали — не помним, что сказали
        self.assertIn("api", state["seen"])

    def test_one_bad_address_does_not_stop_the_rest(self):
        def post(url, data):
            if data["chat_id"] == 1:
                raise RuntimeError("bot was blocked by the user")
            self.sent.append(data["text"])
        self.assertEqual(guard.send("тк", [1, 2], "текст", post=post), 1)
        self.assertEqual(self.sent, ["текст"])


class BigFile(unittest.TestCase):
    """Ревью PR #141: данные мини-аппа больше, чем сторож читает, — он говорит об этом, а не молчит."""

    def setUp(self):
        p = mock.patch.object(guard.time, "sleep")
        p.start()
        self.addCleanup(p.stop)

    def test_truncated_or_alien_file(self):
        cut = {"code": 200, "body": "{\"updated\": \"2026-10", "cut": True}
        got = guard.look("", PAGES, NOW, fetch=answers(**{LEAGUE: cut}))
        self.assertEqual([p["key"] for p in got], ["pages:updated"])
        self.assertIn("больше 8 МБ", got[0]["text"])
        alien = {"code": 200, "body": "<html>не наша сборка</html>"}
        got = guard.look("", PAGES, NOW, fetch=answers(**{LEAGUE: alien}))
        self.assertIn("это не JSON нашей сборки", got[0]["text"])
        empty = {"code": 200, "body": json.dumps({"games": []})}
        got = guard.look("", PAGES, NOW, fetch=answers(**{LEAGUE: empty}))
        self.assertIn("нет времени сборки", got[0]["text"])


class Chats(unittest.TestCase):
    def test_ids_from_secret(self):
        self.assertEqual(guard.chats_of("1001, 1002\n-100500"), [1001, 1002, -100500])
        self.assertEqual(guard.chats_of(""), [])
        self.assertEqual(guard.chats_of("не число"), [])


if __name__ == "__main__":
    unittest.main()

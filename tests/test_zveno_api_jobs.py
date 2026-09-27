"""Сервер «Звена»: фоновые задачи (данные атомарно, дедлайн по часам) и сообщения через Bot API —
воскресенье «есть что решить», вторник — история тура, лимит 2 в неделю, молчуну — раз в две недели."""
import copy
import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestServer  # noqa: E402

from test_zveno_api_fixtures import TEAMS, TOKEN, ApiCase, F, make_pool, make_tours, msk  # noqa: E402

from zveno_api import data as datam  # noqa: E402
from zveno_api.jobs import Jobs  # noqa: E402
from zveno_api.messages import Messenger, week_key  # noqa: E402

SUNDAY = msk(2026, 10, 18, 18, 5)     # перед дедлайном тура 2 (пн 19.10 09:00)
TUESDAY = msk(2026, 10, 20, 18, 5)    # тур 1 позади


class FakeBot:
    """Bot API на тестовом порту: запоминает sendMessage, отвечает 403 заблокировавшим."""

    def __init__(self, blocked=()):
        self.sent: list[dict] = []
        self.blocked = set(blocked)
        app = web.Application()
        app.router.add_post(f"/bot{TOKEN}/sendMessage", self.handle)
        self.server = TestServer(app)

    async def handle(self, request):
        d = await request.json()
        if d["chat_id"] in self.blocked:
            return web.json_response({"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"})
        self.sent.append(d)
        return web.json_response({"ok": True, "result": {"message_id": len(self.sent)}})

    def to(self, uid: int) -> list[str]:
        return [x["text"] for x in self.sent if x["chat_id"] == uid]


class MessagesCase(ApiCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.bot = FakeBot(blocked={7})
        await self.bot.server.start_server()
        self.cfg.telegram_api = str(self.bot.server.make_url("")).rstrip("/")
        self.msg = Messenger(self.game, self.cfg)
        self.data.set_teams(copy.deepcopy(TEAMS))

    async def asyncTearDown(self):
        await self.bot.server.close()
        await super().asyncTearDown()

    async def run_at(self, when):
        self.now = when
        self.game.catch_up()
        return await self.msg.run()

    def idle(self, club: str, tour: int = 2):
        self.tours["tours"][tour - 1]["games"][club] = 0
        self.reload()


class Sunday(MessagesCase):
    async def test_something_to_decide(self):
        await self.create(1)
        self.idle("rostov")                        # у капитана в туре 2 нет матчей
        self.assertEqual(await self.run_at(SUNDAY), 1)
        text = self.bot.to(1)[0]
        self.assertIn("до дедлайна тура 2 — пн 09:00 МСК", text)
        self.assertIn("Нападающий21_0 — в туре без матчей", text)
        self.assertIn("капитана", text)
        self.assertNotIn("❄", text)                # о стоимости — никогда
        self.assertNotIn("стоимост", text)
        self.assertEqual(self.bot.sent[0]["parse_mode"], "HTML")
        self.assertEqual(self.bot.sent[0]["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"],
                         "https://arpicasso.github.io/bogdanov/?startapp=zveno")
        self.assertEqual(await self.run_at(SUNDAY + timedelta(minutes=30)), 0)   # раз за воскресенье

    async def test_nothing_to_decide(self):
        await self.create(1)
        self.assertEqual(await self.run_at(SUNDAY), 0)

    async def test_autopilot_warning(self):
        await self.create(1)
        await self.run_at(msk(2026, 10, 13, 12, 0))     # дедлайн тура 1 позади
        self.player(F("belgorod"))["status"] = "rest"
        self.reload()
        await self.run_at(SUNDAY)
        self.assertIn("Нападающий15_0 пропустил 4 матча подряд. Мишка заменит его в пн 09:00. Не хочешь — «Оставить» в «Звене»", self.bot.to(1)[0])


class SundayRules(MessagesCase):
    async def test_decide_and_silence(self):
        await self.create(1)
        body = {"name": ["Быстрые", "Совы"]}
        await self.create(2, **body)
        # вторая команда: rostov в запасе — решать нечего
        t = await self.call("GET", "/team", 2)
        self.idle("rostov")
        lineup = copy.deepcopy(t["lineup"])
        bench = list(t["bench"])
        lineup["L1"]["F"][0], bench[2] = bench[2], lineup["L1"]["F"][0]
        await self.call("PUT", "/team/lineup", 2, {"lineup": lineup, "bench": bench, "captain": F("krasnodar")})
        n = await self.run_at(SUNDAY)
        self.assertEqual(n, 1)
        self.assertEqual(len(self.bot.to(1)), 1)
        self.assertEqual(self.bot.to(2), [])       # об игроке — только тем, у кого он в основе

    async def test_not_before_18_and_tz(self):
        await self.create(1, fav_club="ermak")
        self.idle("rostov")
        self.assertEqual(await self.run_at(msk(2026, 10, 18, 12, 0)), 0)
        self.data.team["ermak"]["tz"] = "Asia/Irkutsk"   # 18:00 в Ангарске — 13:00 МСК
        self.assertEqual(await self.run_at(msk(2026, 10, 18, 13, 5)), 1)

    async def test_messages_off_and_blocked(self):
        await self.create(1)
        await self.create(7)
        self.idle("rostov")
        await self.call("PUT", "/settings", 1, {"messages": False})
        self.assertEqual(await self.run_at(SUNDAY), 0)
        self.assertEqual(self.conn.execute("SELECT can_write FROM managers WHERE id = 7").fetchone()[0], 0)
        await self.call("PUT", "/settings", 7, {"messages": True})
        self.assertEqual(self.conn.execute("SELECT can_write FROM managers WHERE id = 7").fetchone()[0], 1)


class Tuesday(MessagesCase):
    async def test_story(self):
        await self.create(1)
        self.player(F("rostov"))["tours"] = {"1": {"m": [7, 5], "best2": 12, "ids": ["a", "b"]}}
        self.reload()
        self.assertEqual(await self.run_at(TUESDAY), 1)
        text = self.bot.to(1)[0]
        self.assertIn("тур 1 позади", text)
        self.assertIn("24 очка", text)
        self.assertIn("в четверг", text)            # тур ещё не закрыт
        self.assertIn("Нападающий21_0 — 24", text)
        self.assertEqual(await self.run_at(TUESDAY + timedelta(minutes=20)), 0)   # один раз за тур
        row = self.conn.execute("SELECT msg_count, msg_week FROM managers WHERE id = 1").fetchone()
        self.assertEqual((row["msg_count"], row["msg_week"]), (1, week_key(TUESDAY)))

    async def test_no_matches_no_story(self):
        await self.create(1)
        self.assertEqual(await self.run_at(TUESDAY), 0)


class Limits(MessagesCase):
    async def test_two_per_week(self):
        await self.create(1)
        self.player(F("rostov"))["tours"] = {"1": {"m": [7], "best2": 7, "ids": ["a"]}}
        self.reload()
        self.now = TUESDAY
        self.conn.execute("UPDATE managers SET msg_week = ?, msg_count = 2 WHERE id = 1", (week_key(TUESDAY),))
        self.assertEqual(await self.run_at(TUESDAY), 0)
        m = self.game.manager(1)
        self.assertFalse(self.msg.allowed(m, TUESDAY))
        self.assertTrue(self.msg.allowed(m, TUESDAY + timedelta(days=7)))   # новая неделя

    async def test_quiet_manager_every_two_weeks(self):
        await self.create(1)
        self.conn.execute("UPDATE managers SET last_seen = ?, last_msg_at = ? WHERE id = 1",
                          ("2026-09-20T12:00:00+03:00", "2026-10-10T18:00:00+03:00"))
        m = self.game.manager(1)
        self.assertFalse(self.msg.allowed(m, TUESDAY))                      # писали 10 дней назад
        self.assertTrue(self.msg.allowed(m, msk(2026, 10, 25, 18, 0)))      # прошло две недели
        self.conn.execute("UPDATE managers SET last_seen = ? WHERE id = 1", ("2026-10-15T12:00:00+03:00",))
        self.assertTrue(self.msg.allowed(self.game.manager(1), TUESDAY))    # заходил — обычный режим

    async def test_promotion_only_up(self):
        await self.create(1)
        self.conn.execute("INSERT INTO steps (month, manager_id, step, grp, prev_step) VALUES ('2026-11', 1, 1, 1, 2)")
        m = self.game.manager(1)
        self.assertIn("поднялся", self.msg.promotion(m)[1])
        self.conn.execute("UPDATE steps SET step = 3 WHERE manager_id = 1")
        self.assertIsNone(self.msg.promotion(self.game.manager(1)))


class Refresh(ApiCase):
    def write(self, name, obj):
        (self.dir / "data").mkdir(exist_ok=True)
        (self.dir / "data" / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    async def test_atomic_from_dir(self):
        jobs = Jobs(self.cfg, self.game)
        tours = make_tours()
        tours["updated"] = "2026-10-11T10:00:00+03:00"
        self.write("tours", tours)
        self.write("pool", make_pool())
        self.write("matches", {"updated": "x", "matches": []})
        self.assertTrue(await jobs.refresh_once())
        self.assertEqual(self.data.tours["updated"], "2026-10-11T10:00:00+03:00")
        self.assertTrue((self.cfg.cache() / "pool.json").exists())
        # битый pool.json — остаются все три прежних
        tours["updated"] = "2026-10-11T11:00:00+03:00"
        self.write("tours", tours)
        (self.dir / "data" / "pool.json").write_text("{", encoding="utf-8")
        self.assertFalse(await jobs.refresh_once())
        self.assertEqual(self.data.tours["updated"], "2026-10-11T10:00:00+03:00")
        # после перезапуска без сети — из кэша
        self.data.loaded = False
        self.assertTrue(Jobs(self.cfg, self.game).load_cache())
        self.assertEqual(self.data.tours["updated"], "2026-10-11T10:00:00+03:00")

    async def test_http_with_error(self):
        files = {"tours": make_tours(), "pool": make_pool(), "matches": {"updated": "x", "matches": []}}
        files["tours"]["updated"] = "http"
        broken = {"on": False}

        async def serve(request):
            name = request.match_info["name"]
            if broken["on"] and name == "matches":
                return web.Response(status=500)
            return web.json_response(files[name])

        app = web.Application()
        app.router.add_get("/data/zveno/{name}.json", serve)
        srv = TestServer(app)
        await srv.start_server()
        try:
            self.cfg.data_url = str(srv.make_url("/data/zveno/"))
            jobs = Jobs(self.cfg, self.game)
            self.assertTrue(await jobs.refresh_once())
            self.assertEqual(self.data.tours["updated"], "http")
            broken["on"] = True
            files["tours"]["updated"] = "new"
            self.assertFalse(await jobs.refresh_once())
            self.assertEqual(self.data.tours["updated"], "http")
        finally:
            await srv.close()

    async def test_tick_freezes_on_time(self):
        await self.create()
        jobs = Jobs(self.cfg, self.game)
        self.now = msk(2026, 10, 12, 9, 0, 30)
        jobs.tick_once()
        self.assertEqual(self.conn.execute("SELECT frozen_at FROM lineups WHERE manager_id = 1 AND tour = 1").fetchone()[0],
                         "2026-10-12T09:00:00+03:00")

    def test_check_rejects_garbage(self):
        with self.assertRaises(datam.DataError):
            datam.check({"tours": []}, {"players": []}, {"matches": []})


if __name__ == "__main__":
    unittest.main()

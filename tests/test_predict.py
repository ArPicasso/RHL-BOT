"""«Кто победит?» (ADR-020): доли, смена голоса, закрытие с начала матча, итог, личное число, удаление.

Сеть не трогаем: league.json отдаёт подменённая `fetch`, live/ и база — временные."""
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import predict  # noqa: E402
import raskat  # noqa: E402
import server  # noqa: E402

logging.getLogger("api").addHandler(logging.NullHandler())

TZ = ZoneInfo("Europe/Moscow")
TOKEN = "123456:TEST-token"
PAGES = "https://pages.test/app/"
D1, D2 = "2026-10-03", "2026-10-04"
K1 = f"{D1}|ryazan-vdv|belgorod"      # в live/ со временем начала
K2 = f"{D1}|proton|kristall"           # только в league.json, время неизвестно
K3 = f"{D2}|rostov|krasnodar"          # только в расписании на завтра


def sign(user: dict, auth_date: int) -> str:
    fields = {"auth_date": str(auth_date), "user": json.dumps(user, separators=(",", ":"))}
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def at(day: str, hm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hm}:00+03:00")


# ---------- правила без сервера ----------

class Shares(unittest.TestCase):
    def test_examples(self):
        self.assertEqual(predict.shares(0, 0), (0, 0))
        self.assertEqual(predict.shares(1, 0), (100, 0))
        self.assertEqual(predict.shares(1, 1), (50, 50))
        self.assertEqual(predict.shares(1, 2), (33, 67))
        self.assertEqual(predict.shares(82, 46), (64, 36))

    def test_always_100(self):
        for h in range(0, 30):
            for a in range(0, 30):
                if h + a:
                    self.assertEqual(sum(predict.shares(h, a)), 100, (h, a))

    def test_tally_shape(self):
        self.assertEqual(predict.tally(82, 46, "home", True, None),
                         {"home": 64, "away": 36, "votes": 128, "me": "home", "open": True, "result": None})


class Keys(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(predict.parse_key(K1), (D1, "ryazan-vdv", "belgorod"))
        for bad in ("2026-10-03|ryazan-vdv", "2026-13-03|a|b", "2026-10-03|a|a", "x|a|b", None, 5,
                    "2026-10-03|Рязань|belgorod"):
            self.assertIsNone(predict.parse_key(bad), bad)

    def test_key_of(self):
        self.assertEqual(predict.key_of({"date": D1, "home": "ryazan-vdv", "away": "belgorod"}), K1)
        self.assertEqual(predict.key_of({"key": K2}), K2)
        self.assertIsNone(predict.key_of({"date": D1}))


class Rules(unittest.TestCase):
    def test_start_sources(self):
        live = {"start": "2026-10-03T17:00:00+03:00"}
        self.assertEqual(predict.merge(K1, live=live)["start"], at(D1, "17:00"))
        self.assertEqual(predict.merge(K1, sched={"time": "16:30"})["start"], at(D1, "16:30"))
        self.assertEqual(predict.merge(K1, league={"start": "2026-10-03T15:00:00+03:00"})["start"],
                         at(D1, "15:00"))
        # живой файл главнее расписания
        self.assertEqual(predict.merge(K1, live=live, sched={"time": "16:30"})["start"], at(D1, "17:00"))
        self.assertIsNone(predict.merge(K1))

    def test_open_until_start(self):
        g = predict.merge(K1, live={"start": "2026-10-03T17:00:00+03:00", "status": "sched"})
        self.assertTrue(predict.is_open(g, at(D1, "16:59")))
        self.assertFalse(predict.is_open(g, at(D1, "17:00")))

    def test_closed_by_status(self):
        for status in ("live", "break", "ended", "final", "moved", "off"):
            g = predict.merge(K1, live={"start": "2026-10-03T19:00:00+03:00", "status": status})
            self.assertFalse(predict.is_open(g, at(D1, "12:00")), status)

    def test_unknown_time_open_till_end_of_day(self):
        g = predict.merge(K2, league={"date": D1, "home": "proton", "away": "kristall"})
        self.assertTrue(predict.is_open(g, at(D1, "23:59")))
        self.assertFalse(predict.is_open(g, at(D2, "00:00")))

    def test_result(self):
        final = {"status": "final", "score": {"home": 2, "away": 3, "decision": "ОТ"}}
        self.assertEqual(predict.merge(K1, live=final)["result"], "away")
        self.assertEqual(predict.merge(K1, live={**final, "status": "ended"})["result"], "away")
        # идёт матч — счёт есть, итога нет
        self.assertIsNone(predict.merge(K1, live={**final, "status": "live"})["result"])
        # протокол лиги главнее онлайна
        league = {"score": {"home": 4, "away": 1}}
        self.assertEqual(predict.merge(K1, live=final, league=league)["result"], "home")
        self.assertIsNone(predict.merge(K1, league={"score": {"home": 2, "away": 2}})["result"])

    def test_record(self):
        def g(n, result):
            return {"key": f"2026-10-0{n}|a|b", "date": f"2026-10-0{n}", "start": None, "result": result}
        picks = [(g(3, "home"), "home"), (g(4, "away"), "home"), (g(5, "home"), "home"),
                 (g(6, "away"), "away"), (g(7, None), "home"), (None, "away")]
        self.assertEqual(predict.record(picks), {"right": 3, "of": 4, "streak": 2})
        self.assertEqual(predict.record([]), {"right": 0, "of": 0, "streak": 0})


class Store(unittest.TestCase):
    """Хранилище голосов: кто уже голосовал — по ним зов на прогноз не идёт (ADR-023, раздел 3)."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.pr = predict.PredictStore(self.conn)
        self.now = at(D1, "12:00")

    def test_voted_and_counts(self):
        self.assertEqual(self.pr.voted(K1), set())
        self.pr.vote(1, K1, "home", self.now)
        self.pr.vote(2, K1, "away", self.now)
        self.pr.vote(2, K1, "home", self.now)       # передумал — всё ещё один голос
        self.pr.vote(3, K2, "home", self.now)
        self.assertEqual(self.pr.voted(K1), {1, 2})
        self.assertEqual(self.pr.counts(K1), (2, 0))
        self.assertEqual(self.pr.voted(K2), {3})
        self.pr.forget(2)
        self.assertEqual(self.pr.voted(K1), {1})


# ---------- HTTP ----------

class Api(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("RASKAT_SALT", None)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.live = Path(tmp.name) / "live"
        self.live.mkdir()
        self.db = Path(tmp.name) / "state.db"
        self.now = at(D1, "12:00")
        self.write(D1, [{"key": K1, "date": D1, "home": "ryazan-vdv", "away": "belgorod",
                         "start": "2026-10-03T17:00:00+03:00", "time": "17:00", "status": "sched"}])
        self.write("schedule", [{"key": K3, "date": D2, "home": "rostov", "away": "krasnodar",
                                 "start": "2026-10-04T16:00:00+03:00", "time": "16:00"}])
        self.league = {"games": [
            {"id": "n1", "date": D1, "home": "ryazan-vdv", "away": "belgorod"},
            {"id": "rh1", "date": D1, "home": "proton", "away": "kristall"}]}
        self.league_calls = 0
        pub = {f"data/raskat/{D1}.json": raskat.as_json(raskat.generate(D1))}

        async def fetch(url):
            path = url[len(PAGES):]
            if path == "data/league.json":
                self.league_calls += 1
                if isinstance(self.league, Exception):
                    raise self.league
                return self.league
            return pub.get(path)

        cfg = server.Config(token=TOKEN, live_dir=self.live, db_path=self.db, webapp_url=PAGES,
                            origins=("https://pages.test",))
        self.app = server.make_app(cfg, fetch=fetch, now=lambda: self.now)
        self.api = self.app[server.API_KEY]
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    def write(self, name: str, games: list[dict]) -> None:
        body = {"updated": self.now.isoformat(), "games": games}
        if name != "schedule":
            body["date"] = name
        (self.live / f"{name}.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")

    async def call(self, method, path, uid=None, body=None, status=200):
        headers = {"Authorization": "tma " + sign({"id": uid, "first_name": "Пётр"},
                                                   int(self.now.timestamp()))} if uid else {}
        r = await self.client.request(method, path, headers=headers, json=body)
        data = await r.json()
        self.assertEqual(r.status, status, data)
        return data

    async def vote(self, uid, key, pick, status=200):
        return await self.call("POST", "/api/predict/vote", uid, {"key": key, "pick": pick}, status)

    async def test_vote_and_shares(self):
        t = await self.vote(1, K1, "home")
        self.assertEqual(t, {"home": 100, "away": 0, "votes": 1, "me": "home", "open": True, "result": None})
        t = await self.vote(2, K1, "away")
        self.assertEqual((t["home"], t["away"], t["votes"], t["me"]), (50, 50, 2, "away"))
        await self.vote(3, K1, "away")
        # смена голоса: голос один, число голосов не растёт
        t = await self.vote(1, K1, "away")
        self.assertEqual((t["home"], t["away"], t["votes"], t["me"]), (0, 100, 3, "away"))

    async def test_day(self):
        await self.vote(1, K1, "home")
        await self.vote(2, K2, "away")
        d = await self.call("GET", f"/api/predict/day/{D1}")
        self.assertEqual(set(d["games"]), {K1, K2})
        self.assertEqual(d["games"][K1]["me"], None)              # без авторизации — me всегда null
        self.assertEqual(d["games"][K1]["home"], 100)              # доли отдаём, прячет их мини-апп
        d = await self.call("GET", f"/api/predict/day/{D1}", uid=1)
        self.assertEqual((d["games"][K1]["me"], d["games"][K2]["me"]), ("home", None))
        self.assertEqual(d["games"][K2], {"home": 0, "away": 100, "votes": 1, "me": None,
                                          "open": True, "result": None})
        d = await self.call("GET", f"/api/predict/day/{D2}")
        self.assertEqual(list(d["games"]), [K3])
        await self.call("GET", "/api/predict/day/завтра", status=400)

    async def test_bad_auth_on_day_is_401(self):
        r = await self.client.get(f"/api/predict/day/{D1}", headers={"Authorization": "tma hash=1"})
        self.assertEqual(r.status, 401)

    async def test_vote_needs_auth_and_valid_body(self):
        await self.call("POST", "/api/predict/vote", None, {"key": K1, "pick": "home"}, status=401)
        await self.vote(1, K1, "draw", status=400)
        await self.vote(1, "ryazan-vdv|belgorod", "home", status=400)
        d = await self.vote(1, f"{D1}|samara|tambov", "home", status=404)
        self.assertIn("нет в календаре", d["error"])

    async def test_closed_from_start(self):
        await self.vote(1, K1, "home")
        self.now = at(D1, "17:00")
        d = await self.vote(2, K1, "away", status=409)
        self.assertIn("закрыт", d["error"])
        await self.vote(1, K1, "away", status=409)               # и поменять уже нельзя
        d = await self.call("GET", f"/api/predict/day/{D1}", uid=1)
        self.assertEqual((d["games"][K1]["open"], d["games"][K1]["me"], d["games"][K1]["votes"]),
                         (False, "home", 1))

    async def test_closed_by_live_status(self):
        self.write(D1, [{"key": K1, "date": D1, "home": "ryazan-vdv", "away": "belgorod", "status": "live",
                         "score": {"home": 0, "away": 0, "decision": None}}])
        await self.vote(1, K1, "home", status=409)

    async def test_league_only_until_end_of_day(self):
        await self.vote(1, K2, "home")
        self.now = at(D1, "23:59")
        await self.vote(1, K2, "away")
        self.now = at(D2, "00:00")
        await self.vote(1, K2, "home", status=409)

    async def test_league_cached_and_down(self):
        await self.vote(1, K2, "home")
        await self.vote(2, K2, "home")
        self.assertEqual(self.league_calls, 1)                    # кэш 10 минут
        self.api._league = (0.0, {})
        self.league = OSError("нет сети")
        # league.json не скачался — матчи из live/ работают, из league.json — нет
        await self.vote(3, K1, "home")
        await self.vote(3, K2, "home", status=404)

    async def test_result_and_me(self):
        await self.vote(1, K1, "home")
        await self.vote(1, K2, "home")
        await self.vote(1, K3, "away")
        self.assertEqual(await self.call("GET", "/api/predict/me", 1), {"right": 0, "of": 0, "streak": 0})
        self.now = at(D1, "21:00")
        self.write(D1, [{"key": K1, "date": D1, "home": "ryazan-vdv", "away": "belgorod", "status": "final",
                         "start": "2026-10-03T17:00:00+03:00",
                         "score": {"home": 3, "away": 2, "decision": "Б"}}])
        self.league["games"][1].update(time="19:00", score={"home": 1, "away": 4, "decision": None, "periods": []})
        self.api._league = (0.0, {})
        d = await self.call("GET", f"/api/predict/day/{D1}", uid=1)
        self.assertEqual((d["games"][K1]["result"], d["games"][K1]["open"]), ("home", False))
        self.assertEqual(d["games"][K2]["result"], "away")
        me = await self.call("GET", "/api/predict/me", 1)
        self.assertEqual(me, {"right": 1, "of": 2, "streak": 0})   # последний по времени — промах
        self.assertEqual(await self.call("GET", "/api/predict/me", 2), {"right": 0, "of": 0, "streak": 0})

    async def test_delete_me(self):
        await self.vote(1, K1, "home")
        await self.vote(1, K3, "away")
        await self.vote(2, K1, "away")
        self.assertEqual(await self.call("DELETE", "/api/predict/me", 1), {})
        d = await self.call("GET", f"/api/predict/day/{D1}", uid=1)
        self.assertEqual((d["games"][K1]["votes"], d["games"][K1]["me"], d["games"][K1]["away"]), (1, None, 100))
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM predict_votes WHERE fan = 1").fetchone()[0], 0)
        await self.call("DELETE", "/api/predict/me", None, status=401)

    async def test_vote_survives_restart(self):
        await self.vote(1, K1, "home")
        await self.client.close()
        app = server.make_app(self.api.cfg, fetch=self.api._fetch, now=lambda: self.now)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        d = await self.call("GET", f"/api/predict/day/{D1}", uid=1)
        self.assertEqual(d["games"][K1]["me"], "home")


if __name__ == "__main__":
    unittest.main()

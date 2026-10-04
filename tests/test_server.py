"""Сервер API (ADR-019, контракт «Раската», раздел 5): подпись initData, CORS, живые файлы и зачёт.

Сеть не трогаем: опубликованные файлы Pages отдаёт подменённая `fetch`, база и live/ — временные."""
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from aiohttp.test_utils import TestClient, TestServer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import raskat  # noqa: E402
import server  # noqa: E402

logging.getLogger("api").addHandler(logging.NullHandler())   # предупреждения сверки соли — не в вывод тестов

TZ = ZoneInfo("Europe/Moscow")
TOKEN = "123456:TEST-token"
PAGES = "https://pages.test/app/"
ORIGIN = "https://pages.test"
D1, D2 = "2026-10-03", "2026-10-04"


def sign(user: dict, auth_date: int, token: str = TOKEN, **extra) -> str:
    """initData так, как его подписывает Telegram."""
    fields = {"auth_date": str(auth_date), "query_id": "AAH-test",
              "user": json.dumps(user, ensure_ascii=False, separators=(",", ":")), **extra}
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def fan(i: int, first: str = "Пётр", last: str = "Кузнецов") -> dict:
    return {"id": 1000 + i, "first_name": first, "last_name": last, "language_code": "ru"}


def solution(day: str) -> list[int]:
    return raskat.solve(raskat.generate(day), limit=1)[0]


class InitData(unittest.TestCase):
    NOW = 1_790_000_000

    def test_valid(self):
        user, why = server.check_init_data(sign(fan(1), self.NOW - 60), TOKEN, self.NOW)
        self.assertEqual((user["id"], why), (1001, ""))

    def test_other_bot_token(self):
        user, why = server.check_init_data(sign(fan(1), self.NOW, token="999:other"), TOKEN, self.NOW)
        self.assertEqual((user, why), (None, "bad"))

    def test_tampered_user(self):
        good = sign(fan(1), self.NOW)
        forged = good.replace("1001", "1002")
        self.assertEqual(server.check_init_data(forged, TOKEN, self.NOW), (None, "bad"))

    def test_expired(self):
        old = sign(fan(1), self.NOW - 25 * 3600)
        self.assertEqual(server.check_init_data(old, TOKEN, self.NOW), (None, "old"))

    def test_from_future_and_garbage(self):
        self.assertEqual(server.check_init_data(sign(fan(1), self.NOW + 3600), TOKEN, self.NOW)[1], "bad")
        self.assertEqual(server.check_init_data("hash=abc", TOKEN, self.NOW)[1], "bad")
        self.assertEqual(server.check_init_data("", TOKEN, self.NOW)[1], "bad")
        self.assertEqual(server.check_init_data(sign(fan(1), self.NOW), "", self.NOW)[1], "bad")

    def test_extra_fields_signed_too(self):
        # Новые клиенты Telegram добавляют поля (signature, chat_instance) — они тоже под подписью
        data = sign(fan(1), self.NOW, signature="abc", chat_instance="-1")
        self.assertEqual(server.check_init_data(data, TOKEN, self.NOW)[1], "")

    def test_short_name(self):
        self.assertEqual(server.short_name(fan(1)), "Пётр К.")
        self.assertEqual(server.short_name({"first_name": "Анна"}), "Анна")
        self.assertIsNone(server.short_name({"first_name": "  "}))


class Base(unittest.IsolatedAsyncioTestCase):
    """Сервер на временной базе и временном live/, «сейчас» — 03.10.2026, 12:00 по Москве."""

    async def asyncSetUp(self):
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("RASKAT_SALT", None)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.live = Path(self.tmp.name) / "live"
        self.live.mkdir()
        self.db = Path(self.tmp.name) / "state.db"
        self.now = datetime(2026, 10, 3, 12, 0, tzinfo=TZ)
        # Опубликованное на Pages: путь от адреса мини-аппа → JSON, исключение или нет (404)
        self.pub = {f"data/raskat/{D1}.json": raskat.as_json(raskat.generate(D1)),
                    "data/raskat/index.json": {"today": D1}}
        self.fetched = []
        cfg = server.Config(token=TOKEN, live_dir=self.live, db_path=self.db, webapp_url=PAGES + "?v=7",
                            origins=(ORIGIN,))
        self.app = server.make_app(cfg, fetch=self.fetch, now=lambda: self.now)
        self.api = self.app[server.API_KEY]
        await self.start()

    async def start(self):
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def fetch(self, url):
        self.fetched.append(url)
        self.assertTrue(url.startswith(PAGES), url)
        got = self.pub.get(url[len(PAGES):])
        if isinstance(got, Exception):
            raise got
        return got

    def auth(self, user: dict, at: datetime | None = None) -> dict:
        return {"Authorization": "tma " + sign(user, int((at or self.now).timestamp()))}

    async def call(self, method: str, path: str, user: dict | None = None, body=None, status: int = 200):
        headers = self.auth(user) if user else {}
        r = await self.client.request(method, path, headers=headers,
                                      json=body if body is not None else None)
        data = await r.json()
        self.assertEqual(r.status, status, data)
        return data

    async def solve_day(self, user: dict, day: str = D1, ms: int = 40_000, hint: bool = False,
                        status: int = 200, **extra):
        return await self.call("POST", f"/api/raskat/day/{day}", user,
                               {"path": solution(day), "ms": ms, "hint": hint, **extra}, status)


class Cors(Base):
    async def test_preflight_from_pages(self):
        r = await self.client.options("/api/raskat/day/" + D1, headers={
            "Origin": ORIGIN, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type"})
        self.assertEqual(r.status, 204)
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], ORIGIN)
        self.assertIn("Authorization", r.headers["Access-Control-Allow-Headers"])
        self.assertIn("PUT", r.headers["Access-Control-Allow-Methods"])

    async def test_preflight_from_elsewhere(self):
        r = await self.client.options("/api/raskat/me", headers={
            "Origin": "https://evil.test", "Access-Control-Request-Method": "GET"})
        self.assertEqual(r.status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", r.headers)

    async def test_simple_request_headers(self):
        r = await self.client.get("/api/health", headers={"Origin": ORIGIN})
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], ORIGIN)
        r = await self.client.get("/api/health", headers={"Origin": "https://evil.test"})
        self.assertNotIn("Access-Control-Allow-Origin", r.headers)
        # и у ответа об ошибке тоже: иначе мини-апп не прочитает текст ошибки
        r = await self.client.get("/api/raskat/me", headers={"Origin": ORIGIN})
        self.assertEqual(r.status, 401)
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], ORIGIN)


class HealthAndLive(Base):
    async def test_health(self):
        d = await self.call("GET", "/api/health")
        self.assertTrue(d["ok"])
        self.assertTrue(d["raskat"]["on"])
        self.assertIn("сошлась", d["raskat"]["note"])
        self.assertTrue(d["auth"])
        # адрес Pages — без ?v=…
        self.assertIn(PAGES + f"data/raskat/{D1}.json", self.fetched)

    async def test_live_file(self):
        body = {"date": D1, "updated": "2026-10-03T11:59:00+03:00", "games": []}
        (self.live / "today.json").write_text(json.dumps(body), encoding="utf-8")
        r = await self.client.get("/api/live/today.json")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.headers["Cache-Control"], "max-age=10")
        self.assertEqual(await r.json(), body)
        (self.live / f"{D1}.json").write_text("{}", encoding="utf-8")
        self.assertEqual((await self.client.get(f"/api/live/{D1}.json")).status, 200)
        d = await self.call("GET", "/api/health")
        self.assertEqual(d["live"]["updated"], body["updated"])

    async def test_live_missing_and_foreign_names(self):
        d = await self.call("GET", "/api/live/schedule.json", status=404)
        self.assertIn("пока нет", d["error"])
        (self.live / "secret.json").write_text("{}", encoding="utf-8")
        for name in ("secret", "..%2Fstate.db", "2026-10-3"):
            r = await self.client.get(f"/api/live/{name}.json")
            self.assertEqual(r.status, 404, name)
            self.assertIn("error", await r.json())

    async def test_unknown_path_is_json(self):
        d = await self.call("GET", "/api/nothing", status=404)
        self.assertIn("error", d)


class Auth(Base):
    async def test_no_header(self):
        d = await self.call("GET", "/api/raskat/me", status=401)
        self.assertIn("Telegram", d["error"])

    async def test_expired_header(self):
        r = await self.client.get("/api/raskat/me", headers=self.auth(fan(1), self.now - timedelta(hours=25)))
        self.assertEqual(r.status, 401)
        self.assertIn("устарел", (await r.json())["error"])

    async def test_foreign_signature(self):
        head = {"Authorization": "tma " + sign(fan(1), int(self.now.timestamp()), token="1:x")}
        r = await self.client.get("/api/raskat/me", headers=head)
        self.assertEqual(r.status, 401)


class Day(Base):
    async def test_me_empty(self):
        d = await self.call("GET", "/api/raskat/me", fan(1))
        self.assertEqual(d, {"club": None, "streak": 0, "best": None, "today": None,
                             "show_tg_name": False, "messages": True})

    async def test_result(self):
        r = await self.solve_day(fan(1), ms=40_000)
        par = raskat.generate(D1).par
        self.assertEqual(r["date"], D1)
        self.assertEqual(r["points"], raskat.day_points(par, 40.0))
        self.assertEqual(r["total"], raskat.points(par, 40.0, 0))
        self.assertEqual((r["ms"], r["hint"], r["place"], r["of"], r["streak"]), (40_000, False, 1, 1, 1))
        me = await self.call("GET", "/api/raskat/me", fan(1))
        self.assertEqual(me["today"], r)
        self.assertEqual(me["best"], r)
        self.assertEqual(me["streak"], 1)

    async def test_hint_costs_points(self):
        r = await self.solve_day(fan(1), ms=40_000, hint=True)
        self.assertEqual(r["points"], raskat.day_points(raskat.generate(D1).par, 40.0, hint=True))
        self.assertTrue(r["hint"])

    async def test_repeat_is_409_with_first_result(self):
        first = await self.solve_day(fan(1), ms=50_000)
        again = await self.solve_day(fan(1), ms=20_000, status=409)
        self.assertIn("уже принят", again.pop("error"))
        self.assertEqual(again, first)

    async def test_bad_paths(self):
        path = solution(D1)
        for bad, word in ((path[::-1], "раньше"), (path[:-1], "непройденным"), (path + [path[0]], "клеток"),
                          ("1,2,3", "списком")):
            d = await self.call("POST", f"/api/raskat/day/{D1}", fan(1), {"path": bad, "ms": 60_000}, 400)
            self.assertIn(word, d["error"])
        d = await self.call("POST", f"/api/raskat/day/{D1}", fan(1), {"path": path, "ms": "быстро"}, 400)
        self.assertIn("время", d["error"])
        # ничего из этого в зачёт не попало
        self.assertIsNone((await self.call("GET", "/api/raskat/me", fan(1)))["today"])

    async def test_too_fast(self):
        cells = len(solution(D1))
        d = await self.solve_day(fan(1), ms=cells * 150 - 1, status=400)
        self.assertIn("0,15", d["error"])
        await self.solve_day(fan(2), ms=cells * 150)

    async def test_training_and_future_days(self):
        self.now = datetime(2026, 10, 4, 12, 0, tzinfo=TZ)
        d = await self.solve_day(fan(1), day=D1, status=400)
        self.assertIn("тренировка", d["error"])
        d = await self.solve_day(fan(1), day="2026-10-05", status=400)
        self.assertIn("ещё не открылся", d["error"])
        d = await self.solve_day(fan(1), day="2026-09-30", status=404)
        self.assertIn("Такого раската нет", d["error"])
        await self.call("POST", "/api/raskat/day/вчера", fan(1), {"path": [], "ms": 1}, 400)

    async def test_midnight(self):
        # 00:03 — вчерашний раскат ещё досылается
        self.now = datetime(2026, 10, 4, 0, 3, tzinfo=TZ)
        await self.solve_day(fan(1), day=D1)
        # 00:30, файл нового дня уже на Pages — вчерашний день закрыт
        self.pub["data/raskat/index.json"] = {"today": D2}
        self.now = datetime(2026, 10, 4, 0, 30, tzinfo=TZ)
        await self.solve_day(fan(2), day=D1, status=400)
        # а пока его нет, мини-апп показывает вчерашний раскат как раскат дня — принимаем до 02:00
        self.api._index = (0.0, "")
        self.pub["data/raskat/index.json"] = {"today": D1}
        await self.solve_day(fan(3), day=D1)
        self.now = datetime(2026, 10, 4, 2, 30, tzinfo=TZ)
        await self.solve_day(fan(4), day=D1, status=400)

    async def test_streak(self):
        first = await self.solve_day(fan(1), day=D1, ms=40_000)
        self.now = datetime(2026, 10, 4, 9, 0, tzinfo=TZ)
        self.assertEqual((await self.call("GET", "/api/raskat/me", fan(1)))["streak"], 1)   # день не кончился
        second = await self.solve_day(fan(1), day=D2, ms=40_000)
        self.assertEqual(second["streak"], 2)
        # серия в очки дня не входит, только в итог: 2 очка за день серии до сегодняшнего
        self.assertEqual(second["total"], second["points"] + 2)
        self.assertEqual(first["total"], first["points"])
        self.now = datetime(2026, 10, 6, 9, 0, tzinfo=TZ)
        self.assertEqual((await self.call("GET", "/api/raskat/me", fan(1)))["streak"], 0)   # пропустил 5-е

    async def test_table_order_and_names(self):
        await self.solve_day(fan(1), ms=90_000, club="ryazan-vdv")
        await self.solve_day(fan(2, "Анна", "Смирнова"), ms=30_000, club="belgorod")
        await self.solve_day(fan(3, "Илья", ""), ms=60_000)
        d = await self.call("GET", f"/api/raskat/day/{D1}", fan(1))
        self.assertEqual(d["of"], 3)
        self.assertEqual([r["place"] for r in d["top"]], [1, 2, 3])
        self.assertEqual([r["ms"] for r in d["top"]], [30_000, 60_000, 90_000])
        # без галочки — «Болельщик «клуба»», имени из Telegram нет нигде
        self.assertEqual([r["name"] for r in d["top"]],
                         ["Болельщик «Белгорода»", "Болельщик", "Болельщик «Рязани-ВДВ»"])
        self.assertEqual([r["me"] for r in d["top"]], [False, False, True])
        self.assertEqual(d["me"]["place"], 3)
        self.assertEqual(set(d["top"][0]), {"place", "name", "club", "points", "ms", "hint", "me"})
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM raskat_fans WHERE name IS NOT NULL").fetchone()[0], 0)

        # галочка — «Анна С.», сняли — имя стёрто из базы
        s = await self.call("PUT", "/api/raskat/settings", fan(2, "Анна", "Смирнова"),
                            {"show_tg_name": True, "messages": False})
        self.assertEqual(s, {"club": "belgorod", "show_tg_name": True, "messages": False})
        d = await self.call("GET", f"/api/raskat/day/{D1}", fan(1))
        self.assertEqual(d["top"][0]["name"], "Анна С.")
        await self.call("PUT", "/api/raskat/settings", fan(2, "Анна", "Смирнова"), {"show_tg_name": False})
        d = await self.call("GET", f"/api/raskat/day/{D1}", fan(1))
        self.assertEqual(d["top"][0]["name"], "Болельщик «Белгорода»")
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM raskat_fans WHERE name IS NOT NULL").fetchone()[0], 0)

    async def test_club_field(self):
        await self.call("PUT", "/api/raskat/settings", fan(1), {"club": "ermak"})
        self.assertEqual((await self.call("GET", "/api/raskat/me", fan(1)))["club"], "ermak")
        r = await self.solve_day(fan(1))                      # клуба в запросе нет — берём из настроек
        d = await self.call("GET", f"/api/raskat/day/{D1}", fan(1))
        self.assertEqual(d["top"][0]["club"], "ermak")
        self.assertEqual(r["place"], 1)
        await self.call("PUT", "/api/raskat/settings", fan(1), {"club": "нет-такого"}, 400)
        await self.call("PUT", "/api/raskat/settings", fan(1), {"show_tg_name": "да"}, 400)
        await self.solve_day(fan(2), club="нет-такого", status=400)

    async def test_top_is_50(self):
        cells = len(solution(D1))
        for i in range(53):
            await self.solve_day(fan(i), ms=cells * 150 + i * 1000)
        d = await self.call("GET", f"/api/raskat/day/{D1}", fan(52))
        self.assertEqual((len(d["top"]), d["of"], d["me"]["place"]), (50, 53, 53))

    async def test_clubs_from_five_fans(self):
        for i in range(5):
            await self.solve_day(fan(i), ms=40_000 + i * 10_000, club="ryazan-vdv")
        for i in range(5, 8):
            await self.solve_day(fan(i), ms=40_000, club="belgorod")
        d = await self.call("GET", f"/api/raskat/clubs/{D1}", fan(6))
        self.assertEqual(d["me"], "belgorod")
        self.assertEqual([(c["club"], c["fans"], c["place"], c["me"]) for c in d["day"]],
                         [("ryazan-vdv", 5, 1, False)])
        st = raskat.standings([{"date": D1, "fan": str(i), "club": "ryazan-vdv",
                                "points": raskat.day_points(raskat.generate(D1).par, 40 + i * 10), "ms": 0}
                               for i in range(5)])
        self.assertEqual(d["day"][0]["avg"], st.clubs[D1][0].avg)
        self.assertEqual([(c["club"], c["fans"], c["me"]) for c in d["low"]], [("belgorod", 3, True)])
        self.assertEqual([c["club"] for c in d["season"]], ["ryazan-vdv"])
        # четверо — кубка нет, клуб в «low»
        await self.call("DELETE", "/api/raskat/me", fan(0))
        d = await self.call("GET", f"/api/raskat/clubs/{D1}", fan(6))
        self.assertEqual(d["day"], [])
        self.assertEqual(sorted(c["club"] for c in d["low"]), ["belgorod", "ryazan-vdv"])


class Duel(Base):
    async def test_duel(self):
        a, b = fan(1, "Пётр", "Кузнецов"), fan(2, "Анна", "Смирнова")
        await self.solve_day(a, day=D1, club="arktika")
        code = (await self.call("POST", "/api/raskat/duel", a))["code"]
        self.assertRegex(code, r"^[A-Za-z0-9-]{3,24}$")
        self.assertEqual((await self.call("POST", "/api/raskat/duel", a))["code"], code)   # код один
        # Анна в первый день не играла: дня в дуэли нет
        d = await self.call("GET", f"/api/raskat/duel/{code}", b)
        self.assertEqual(d, {"name": "Болельщик «Арктики»", "days": []})
        self.now = datetime(2026, 10, 4, 12, 0, tzinfo=TZ)
        ra = await self.solve_day(a, day=D2, ms=60_000)
        rb = await self.solve_day(b, day=D2, ms=40_000)
        d = await self.call("GET", f"/api/raskat/duel/{code}", b)
        self.assertEqual(d["days"], [{"date": D2, "me": rb["points"], "them": ra["points"]}])
        await self.call("PUT", "/api/raskat/settings", a, {"show_tg_name": True})
        self.assertEqual((await self.call("GET", f"/api/raskat/duel/{code}", b))["name"], "Пётр К.")
        self.assertTrue((await self.call("GET", f"/api/raskat/duel/{code}", a))["own"])
        # стёр всё — ссылка больше не работает
        await self.call("DELETE", "/api/raskat/me", a)
        await self.call("GET", f"/api/raskat/duel/{code}", b, status=404)


class Forget(Base):
    async def test_delete_me(self):
        await self.solve_day(fan(1), ms=30_000, club="ryazan-vdv")
        await self.solve_day(fan(2), ms=60_000)
        await self.call("PUT", "/api/raskat/settings", fan(1), {"show_tg_name": True})
        self.assertEqual(await self.call("DELETE", "/api/raskat/me", fan(1)), {})
        d = await self.call("GET", f"/api/raskat/day/{D1}", fan(2))
        self.assertEqual((d["of"], d["me"]["place"]), (1, 1))     # зачёт пересчитан
        self.assertEqual(await self.call("GET", "/api/raskat/me", fan(1)),
                         {"club": None, "streak": 0, "best": None, "today": None,
                          "show_tg_name": False, "messages": True})
        with sqlite3.connect(self.db) as c:
            for table in ("raskat_results", "raskat_fans"):
                n = c.execute(f"SELECT COUNT(*) FROM {table} WHERE fan = ?", (1001,)).fetchone()[0]
                self.assertEqual(n, 0, table)


class Salt(Base):
    async def restart(self):
        await self.client.close()
        cfg = self.api.cfg
        self.app = server.make_app(cfg, fetch=self.fetch, now=lambda: self.now)
        self.api = self.app[server.API_KEY]
        await self.start()

    async def test_mismatch_uses_published(self):
        # Соль Pages другая (секрет RASKAT_SALT не перенесли на сервер): зачёт идёт по опубликованному
        # раскладу — путь болельщика по полю Pages принимается, путь по своему движку — нет
        with mock.patch.dict(os.environ, {"RASKAT_SALT": "другая соль"}):
            other = raskat.generate(D1)
            self.pub[f"data/raskat/{D1}.json"] = raskat.as_json(other)
            path = raskat.solve(other, limit=1)[0]
        await self.restart()
        h = await self.call("GET", "/api/health")
        self.assertTrue(h["raskat"]["on"])
        self.assertIn("по опубликованным раскладам", h["raskat"]["note"])
        await self.call("POST", f"/api/raskat/day/{D1}", fan(2),
                        {"path": solution(D1), "ms": 40_000, "hint": False}, status=400)
        r = await self.call("POST", f"/api/raskat/day/{D1}", fan(1), {"path": path, "ms": 40_000, "hint": False})
        self.assertEqual(r["place"], 1)

    async def test_unreadable_published_turns_off(self):
        with mock.patch.dict(os.environ, {"RASKAT_SALT": "другая соль"}):
            self.pub[f"data/raskat/{D1}.json"] = {"date": D1, "w": "?", "dots": []}
        await self.restart()
        d = await self.solve_day(fan(1), status=503)
        self.assertIn("Зачёт временно выключен", d["error"])
        await self.call("GET", "/api/raskat/me", fan(1), status=503)
        h = await self.call("GET", "/api/health")
        self.assertFalse(h["raskat"]["on"])
        # стереть свои данные можно и при выключенном зачёте
        await self.call("DELETE", "/api/raskat/me", fan(1))

    async def test_pages_down_keeps_raskat_on(self):
        self.pub[f"data/raskat/{D1}.json"] = OSError("нет сети")
        await self.restart()
        await self.solve_day(fan(1))
        h = await self.call("GET", "/api/health")
        self.assertTrue(h["raskat"]["on"])
        self.assertIn("не удалось скачать", h["raskat"]["note"])

    async def test_falls_back_to_yesterday_file(self):
        # 00:02, файла нового дня ещё нет — сверяем по вчерашнему
        self.now = datetime(2026, 10, 4, 0, 2, tzinfo=TZ)
        await self.restart()
        h = await self.call("GET", "/api/health")
        self.assertIn(D1, h["raskat"]["note"])


class AdminPanel(Base):
    """Пульт (ADR-021): только ADMIN_IDS, открытия мини-аппа — один человек раз в день."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.status = Path(self.tmp.name) / "status"
        self.status.mkdir()
        self.subs = Path(self.tmp.name) / "subscribers.json"
        self.api.cfg.admins = frozenset({1001})
        self.api.cfg.status_dir = self.status
        self.api.cfg.subs_file = self.subs
        self.pub["data/league.json"] = {"updated": "2026-10-03T11:50:00+03:00", "games": []}

        async def services():
            return {"bot": {"state": "active", "sub": "running", "since": None, "restarts": 0}}, ""
        self.api.services = services

    async def test_needs_telegram_and_admin(self):
        await self.call("GET", "/api/admin/status", status=401)
        d = await self.call("GET", "/api/admin/status", fan(2), status=403)
        self.assertIn("1002", d["error"])
        self.api.cfg.admins = frozenset()
        d = await self.call("GET", "/api/admin/status", fan(1), status=403)
        self.assertIn("ADMIN_IDS", d["error"])

    async def test_status_for_admin(self):
        (self.status / "bot.json").write_text(json.dumps({
            "beat": "2026-10-03T11:59:30+03:00", "info": {"tg_ok": "2026-10-03T11:59:30+03:00"},
            "days": {D1: {"starts": 4}}, "log": []}), encoding="utf-8")
        self.subs.write_text(json.dumps({"5": ["tambov"], "6": ["tambov", "sokol"]}), encoding="utf-8")
        (self.live / "today.json").write_text(json.dumps({"updated": "2026-10-03T11:59:50+03:00"}), encoding="utf-8")
        await self.call("POST", "/api/seen", fan(3), {"platform": "ios", "fav": "tambov"})
        await self.call("POST", "/api/seen", fan(3), {"platform": "ios", "fav": "tambov"})
        await self.call("POST", "/api/seen", fan(4), {"platform": "android", "fav": "нет-такой"})
        await self.solve_day(fan(3))
        st = await self.call("GET", "/api/admin/status", fan(1))
        self.assertEqual(st["now"], "2026-10-03T12:00:00+03:00")
        self.assertEqual(st["system"]["league_updated"], "2026-10-03T11:50:00+03:00")
        self.assertEqual(st["system"]["services"][0]["name"], "bot")
        self.assertEqual(st["audience"]["subscribers"], 2)
        today = st["days"][0]
        self.assertEqual((today["date"], today["starts"], today["app_users"], today["raskat"]), (D1, 4, 2, 1))
        self.assertEqual(st["audience"]["fans"], [{"id": "tambov", "name": "Тамбов", "n": 1}])
        self.assertEqual({p["id"] for p in st["audience"]["platforms"]}, {"ios", "android"})
        self.assertIsInstance(st["problems"], list)
        self.assertEqual(st["audience"]["retention"],   # удержание: сегодня двое и оба впервые
                         {"new": 2, "back": 0, "known": 2, "d1": {"of": 0, "back": 0},
                          "week": {"of": 0, "back": 0, "days": 7}, "sleeping": 0})
        self.assertNotIn("1003", json.dumps(st))   # на пульте нет id болельщиков

    async def test_seen_needs_login_and_forget_clears_it(self):
        await self.call("POST", "/api/seen", body={"platform": "ios"}, status=401)
        await self.call("POST", "/api/seen", fan(3), {"platform": "ios"})
        await self.call("DELETE", "/api/predict/me", fan(3))
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM admin_seen").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT n FROM admin_counts WHERE key = 'app_users'").fetchone()[0], 1)

    def test_admin_ids_from_env(self):
        with self.assertLogs("api", level="WARNING"):
            self.assertEqual(server.Config.parse_admins("1001, 1002 x"), frozenset({1001, 1002}))
        self.assertEqual(server.Config.parse_admins(""), frozenset())


if __name__ == "__main__":
    unittest.main()

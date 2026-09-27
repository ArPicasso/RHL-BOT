"""Сервер «Звена»: API по контракту, раздел 4 — вход, команда, обмены тремя способами оплаты, лиги,
удаление менеджера, CORS."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_zveno_api_fixtures import (ORIGIN, ApiCase, D, F, msk, squad_ids, team_body)  # noqa: E402

from zveno import rules  # noqa: E402
from zveno_api.config import PREFIX  # noqa: E402


class Auth(ApiCase):
    async def test_no_header(self):
        d = await self.call("GET", "/me", uid=None, status=401)
        self.assertIn("Telegram", d["error"])

    async def test_forged(self):
        h = self.headers(1)
        h["Authorization"] = h["Authorization"].replace("id%22%3A1", "id%22%3A2")
        d = await self.call("GET", "/me", uid=None, headers=h, status=401)
        self.assertTrue(d["error"])

    async def test_stale(self):
        h = self.headers(1)
        self.now = msk(2026, 10, 11, 13, 0)   # сутки и час спустя
        await self.call("GET", "/me", uid=None, headers=h, status=401)

    async def test_me_empty(self):
        d = await self.call("GET", "/me")
        self.assertIsNone(d["manager"])
        self.assertEqual(d["season"]["status"], "open")
        self.assertEqual(d["season"]["tour_next"], 1)
        self.assertEqual(d["season"]["deadline"], "2026-10-12T09:00:00+03:00")

    async def test_health_without_auth(self):
        d = await self.call("GET", "/health", uid=None)
        self.assertTrue(d["ok"] and d["data"])


class Cors(ApiCase):
    async def test_preflight_pages(self):
        r = await self.client.options(PREFIX + "/team", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"})
        self.assertEqual(r.status, 204)
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], ORIGIN)
        self.assertIn("Authorization", r.headers["Access-Control-Allow-Headers"])

    async def test_other_origin(self):
        r = await self.client.options(PREFIX + "/team", headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", r.headers)
        r = await self.client.get(PREFIX + "/me", headers={"Origin": "https://evil.example", **self.headers()})
        self.assertNotIn("Access-Control-Allow-Origin", r.headers)

    async def test_error_has_cors(self):
        r = await self.client.get(PREFIX + "/me", headers={"Origin": ORIGIN})
        self.assertEqual(r.status, 401)
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], ORIGIN)

    async def test_unknown_path(self):
        d = await self.call("GET", "/nope", status=404)
        self.assertIn("error", d)


class CreateTeam(ApiCase):
    async def test_create(self):
        t = await self.create()
        cost = sum(self.player(x)["price"] for x in squad_ids())
        self.assertEqual(t["tour"], 1)
        self.assertFalse(t["locked"])
        self.assertEqual(t["bank"], rules.BUDGET - cost)
        self.assertEqual(t["value"], cost)
        self.assertEqual(t["free"], 0)
        self.assertTrue(t["unlimited"])
        self.assertEqual(t["captain"], F("rostov"))
        self.assertIn(t["assistant"], squad_ids())
        self.assertNotEqual(t["assistant"], t["captain"])
        self.assertEqual(t["bought"][F("rostov")], self.player(F("rostov"))["price"])
        self.assertEqual(t["boosts"], {"zalivka": 1})
        self.assertIsNone(t["mission"])
        me = await self.call("GET", "/me")
        self.assertEqual(me["manager"]["title"], "Ледяные Буревестники")
        self.assertEqual(me["manager"]["settings"], {"autopilot": True, "messages": True, "show_tg_name": False})
        j = await self.call("GET", "/journal")
        self.assertEqual(j[0]["kind"], "deal")

    async def test_only_once(self):
        await self.create()
        d = await self.call("POST", "/team", 1, team_body(), status=409)
        self.assertIn("уже есть", d["error"])

    async def test_bad_name(self):
        d = await self.call("POST", "/team", 1, team_body(name=["Злые", "Шайбы"]), status=400)
        self.assertIn("конструктор", d["error"])

    async def test_bad_club(self):
        await self.call("POST", "/team", 1, team_body(fav_club="spartak"), status=400)

    async def test_club_limit_from_engine(self):
        body = team_body()
        body["lineup"]["L1"]["F"] = [F("rostov"), F("rostov", 1), F("rostov", 2)]
        body["lineup"]["L1"]["D"] = [D("rostov"), D("samara")]
        d = await self.call("POST", "/team", 1, body, status=400)
        self.assertIn("не больше трёх", d["error"])

    async def test_budget(self):
        for p in self.pool["players"]:
            p["price"] = 9000
        self.reload()
        d = await self.call("POST", "/team", 1, team_body(), status=400)
        self.assertIn("Не хватает льдинок", d["error"])

    async def test_captain_from_lineup(self):
        d = await self.call("POST", "/team", 1, team_body(captain=F("progress")), status=400)
        self.assertIn("Капитан", d["error"])

    async def test_prolog(self):
        self.tours["status"] = "prolog"
        self.reload()
        d = await self.call("POST", "/team", 1, team_body(), status=409)
        self.assertIn("Пролог", d["error"])

    async def test_shape(self):
        body = team_body()
        body["bench"] = body["bench"][:3]
        await self.call("POST", "/team", 1, body, status=400)
        await self.call("POST", "/team", 1, ["не", "словарь"], status=400)


class Lineup(ApiCase):
    async def test_swap_bench(self):
        await self.create()
        body = team_body()
        body["lineup"]["L1"]["F"][0], body["bench"][2] = body["bench"][2], body["lineup"]["L1"]["F"][0]
        body["captain"] = F("krasnodar")
        t = await self.call("PUT", "/team/lineup", 1, body)
        self.assertEqual(t["lineup"]["L1"]["F"][0], F("progress"))
        self.assertEqual(t["bench"][2], F("rostov"))
        self.assertEqual(t["captain"], F("krasnodar"))

    async def test_no_new_stickers(self):
        await self.create()
        body = team_body()
        body["bench"][3] = F("samara")
        d = await self.call("PUT", "/team/lineup", 1, body, status=400)
        self.assertIn("«Обмен»", d["error"])

    async def test_slot(self):
        await self.create()
        body = team_body()
        body["lineup"]["L1"]["F"][0], body["lineup"]["L1"]["D"][0] = body["lineup"]["L1"]["D"][0], body["lineup"]["L1"]["F"][0]
        body["captain"] = F("krasnodar")
        d = await self.call("PUT", "/team/lineup", 1, body, status=400)
        self.assertIn("место", d["error"])


class Transfers(ApiCase):
    """Обмен тремя способами оплаты: бесплатный, 8 очков, 600 ❄; до первого дедлайна — без ограничений."""

    async def test_before_first_deadline_unlimited(self):
        t0 = await self.create()
        t = await self.call("POST", "/team/transfer", 1, {"out": F("rostov"), "in": F("samara"), "pay": "points"})
        t = await self.call("POST", "/team/transfer", 1, {"out": F("krasnodar"), "in": F("sokol"), "pay": "free"})
        self.assertEqual(t["free"], 0)
        self.assertEqual(t["paid_this_tour"], 0)
        self.assertEqual(t["captain"], F("samara"))   # «К» переходит к вставшей на место
        sale = t0["sale"][F("rostov")] + t0["sale"][F("krasnodar")]
        cost = self.player(F("samara"))["price"] + self.player(F("sokol"))["price"]
        self.assertEqual(t["bank"], t0["bank"] + sale - cost)
        self.assertEqual(t["points"]["penalty"], 0)
        self.assertEqual(t["fee_options"], [])          # до первого дедлайна платить не за что

    async def test_three_ways_to_pay(self):
        t0 = await self.create()
        self.now = msk(2026, 10, 12, 10, 0)            # дедлайн тура 1 прошёл
        t = await self.call("GET", "/team")
        self.assertEqual(t["tour"], 2)
        self.assertEqual(t["free"], 1)
        self.assertFalse(t["unlimited"])
        self.assertEqual(t["fee_options"], ["points", "ice"])
        # 1 — бесплатный
        t = await self.call("POST", "/team/transfer", 1, {"out": F("rostov"), "in": F("samara"), "pay": "free"})
        self.assertEqual((t["free"], t["paid_this_tour"]), (0, 0))
        # бесплатных нет — «free» не пройдёт
        d = await self.call("POST", "/team/transfer", 1, {"out": F("krasnodar"), "in": F("sokol"), "pay": "free"}, status=400)
        self.assertIn("8 очков", d["error"])
        # 2 — за 8 очков
        t = await self.call("POST", "/team/transfer", 1, {"out": F("krasnodar"), "in": F("sokol"), "pay": "points"})
        self.assertEqual(t["paid_this_tour"], 1)
        self.assertEqual(t["points"]["penalty"], rules.FEE_POINTS)
        bank = t["bank"]
        # 3 — за 600 ❄
        t = await self.call("POST", "/team/transfer", 1, {"out": F("belgorod"), "in": F("tambov"), "pay": "ice"})
        price_out = t0["sale"][F("belgorod")]
        self.assertEqual(t["bank"], bank + price_out - self.player(F("tambov"))["price"] - rules.FEE_ICE)
        self.assertEqual(t["paid_this_tour"], 2)
        self.assertEqual(t["fee_options"], [])
        d = await self.call("POST", "/team/transfer", 1, {"out": F("kristall"), "in": F("proton"), "pay": "points"}, status=400)
        self.assertIn("не больше двух", d["error"])
        kinds = [j["kind"] for j in await self.call("GET", "/journal")]
        self.assertEqual(kinds.count("deal"), 4)   # сборка и три обмена

    async def test_ice_only_till_18(self):
        await self.create()
        self.now = msk(2027, 2, 28, 12, 0)              # собираем тур 19
        t = await self.call("GET", "/team")
        self.assertEqual(t["tour"], 19)
        self.assertEqual(t["fee_options"], ["points"])
        free = t["free"]
        self.assertEqual(free, rules.FREE_MAX)          # копятся до 5
        outs = [F("rostov"), F("krasnodar"), F("belgorod"), F("kristall"), F("bryansk"), F("dizelist")]
        ins = [F("samara"), F("sokol"), F("tambov"), F("proton"), F("rostov", 1), F("krasnodar", 1)]
        for a, b in list(zip(outs, ins))[:free]:
            await self.call("POST", "/team/transfer", 1, {"out": a, "in": b, "pay": "free"})
        a, b = outs[free], ins[free]
        d = await self.call("POST", "/team/transfer", 1, {"out": a, "in": b, "pay": "ice"}, status=400)
        self.assertIn("19–21", d["error"])

    async def test_rest_is_free(self):
        await self.create()
        self.now = msk(2026, 10, 12, 10, 0)
        await self.call("GET", "/team")                 # дедлайн тура 1 прошёл при «ok»
        self.player(F("rostov"))["status"] = "rest"
        self.reload()
        t = await self.call("POST", "/team/transfer", 1, {"out": F("rostov"), "in": F("samara"), "pay": "free"})
        self.assertEqual((t["free"], t["paid_this_tour"]), (1, 0))   # «отдыхающего» отдают без обмена

    async def test_rules(self):
        await self.create()
        d = await self.call("POST", "/team/transfer", 1, {"out": F("rostov"), "in": D("rostov")}, status=400)
        self.assertIn("том же слоте", d["error"])
        d = await self.call("POST", "/team/transfer", 1, {"out": F("rostov"), "in": F("krasnodar")}, status=400)
        self.assertIn("уже в твоей", d["error"])
        d = await self.call("POST", "/team/transfer", 1, {"out": F("samara"), "in": F("sokol")}, status=400)
        self.assertIn("нет в твоей", d["error"])
        d = await self.call("POST", "/team/transfer", 1, {"out": F("rostov"), "in": "p:999999"}, status=400)
        self.assertIn("нет в пуле", d["error"])
        # четвёртая наклейка «Рязани-ВДВ» (ворота уже есть) — можно, пятая — нет
        await self.call("POST", "/team/transfer", 1, {"out": F("rostov"), "in": F("ryazan-vdv")})
        await self.call("POST", "/team/transfer", 1, {"out": F("krasnodar"), "in": F("ryazan-vdv", 1)})
        d = await self.call("POST", "/team/transfer", 1, {"out": F("belgorod"), "in": F("ryazan-vdv", 2)}, status=400)
        self.assertIn("не больше трёх", d["error"])

    async def test_after_deadline_goes_to_next_tour(self):
        await self.create()
        self.now = msk(2026, 10, 13, 12, 0)
        await self.call("POST", "/team/transfer", 1, {"out": F("rostov"), "in": F("samara")})
        t1 = await self.call("GET", "/team?tour=1")
        self.assertTrue(t1["locked"])
        self.assertIn(F("rostov"), t1["lineup"]["L1"]["F"])   # в туре 1 играет прежний
        t2 = await self.call("GET", "/team?tour=2")
        self.assertIn(F("samara"), t2["lineup"]["L1"]["F"])
        await self.call("GET", "/team?tour=5", status=404)


class Boost(ApiCase):
    async def test_zalivka(self):
        await self.create()
        d = await self.call("POST", "/team/boost", 1, {"boost": "zalivka"}, status=400)
        self.assertIn("первого дедлайна", d["error"])
        self.now = msk(2026, 10, 13, 12, 0)
        await self.call("POST", "/team/transfer", 1, {"out": F("rostov"), "in": F("samara"), "pay": "free"})
        await self.call("POST", "/team/transfer", 1, {"out": F("krasnodar"), "in": F("sokol"), "pay": "points"})
        t = await self.call("POST", "/team/boost", 1, {"boost": "zalivka"})
        self.assertEqual(t["boost"], "zalivka")
        self.assertTrue(t["unlimited"])
        self.assertEqual(t["fee_options"], [])
        self.assertEqual(t["boosts"], {"zalivka": 0})
        self.assertEqual((t["free"], t["paid_this_tour"], t["points"]["penalty"]), (1, 0, 0))
        t = await self.call("POST", "/team/transfer", 1, {"out": F("belgorod"), "in": F("tambov"), "pay": "free"})
        self.assertEqual(t["free"], 1)
        d = await self.call("POST", "/team/boost", 1, {"boost": "zalivka"}, status=400)
        d = await self.call("POST", "/team/boost", 1, {"boost": "hattrick"}, status=400)
        self.assertIn("втором круге", d["error"])


class Settings(ApiCase):
    async def test_settings(self):
        await self.create()
        m = await self.call("PUT", "/settings", 1, {"autopilot": False, "messages": False, "show_tg_name": True,
                                                   "my_player": F("rostov")})
        self.assertEqual(m["settings"], {"autopilot": False, "messages": False, "show_tg_name": True})
        self.assertEqual(m["my_player"], F("rostov"))
        row = self.conn.execute("SELECT tg_name FROM managers WHERE id = 1").fetchone()
        self.assertEqual(row["tg_name"], "Аня")
        await self.call("PUT", "/settings", 1, {"show_tg_name": False})
        self.assertIsNone(self.conn.execute("SELECT tg_name FROM managers WHERE id = 1").fetchone()["tg_name"])
        await self.call("PUT", "/settings", 1, {"autopilot": "да"}, status=400)
        await self.call("PUT", "/settings", 2, {"autopilot": True}, status=404)


class Leagues(ApiCase):
    async def test_own_league_by_code(self):
        await self.create(1)
        await self.create(2, name=["Быстрые", "Совы"])
        r = await self.call("POST", "/leagues", 1, {"name": ["Северные", "Моржи"]}, status=201)
        self.assertRegex(r["code"], r"^[A-Z2-9]{6}$")
        j = await self.call("POST", "/leagues/join", 2, {"code": f"lg-{r['code'].lower()}"})
        self.assertEqual(j["id"], r["id"])
        await self.call("POST", "/leagues/join", 2, {"code": r["code"]})   # повторно — то же
        ls = await self.call("GET", "/leagues", 2)
        own = next(x for x in ls if x["kind"] == "own")
        self.assertEqual((own["title"], own["of"], own["code"]), ("Северные Моржи", 2, r["code"]))
        self.assertEqual([x["kind"] for x in ls], ["own", "conf", "month", "circle", "overall"])
        rows = await self.call("GET", f"/leagues/{r['id']}", 2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(x["me"] for x in rows), 1)
        self.assertNotIn("tg_name", rows[0])
        await self.call("PUT", "/settings", 1, {"show_tg_name": True})
        rows = await self.call("GET", f"/leagues/{r['id']}", 2)
        self.assertEqual({x.get("tg_name") for x in rows}, {"Аня", None})
        overall = await self.call("GET", "/leagues/overall", 2)
        self.assertTrue(all("tg_name" not in x for x in overall))   # имя — только в своей лиге
        await self.call("POST", "/leagues/join", 3, {"code": r["code"]}, status=404)   # без команды
        await self.call("POST", "/leagues/join", 2, {"code": "ZZZZZZ"}, status=404)
        await self.call("GET", "/leagues/own:999", 2, status=404)
        await self.call("POST", "/leagues", 1, {"name": ["Злые", "Моржи"]}, status=400)

    async def test_club_league_from_15(self):
        for uid in range(1, 15):
            await self.create(uid)
        ls = await self.call("GET", "/leagues", 1)
        self.assertEqual(ls[0]["kind"], "conf")
        self.assertEqual(ls[0]["of"], 14)
        await self.create(15)
        ls = await self.call("GET", "/leagues", 1)
        self.assertEqual((ls[0]["kind"], ls[0]["id"], ls[0]["of"]), ("club", "club:ryazan-vdv", 15))
        await self.call("GET", "/leagues/conf:west", 1, status=404)


class DeleteMe(ApiCase):
    async def test_delete(self):
        await self.create(1)
        await self.create(2, name=["Быстрые", "Совы"])
        r = await self.call("POST", "/leagues", 1, {"name": ["Северные", "Моржи"]}, status=201)
        await self.call("POST", "/leagues/join", 2, {"code": r["code"]})
        await self.call("PUT", "/settings", 1, {"show_tg_name": True})
        self.assertEqual(len(await self.call("GET", "/leagues/overall", 2)), 2)
        self.assertEqual(await self.call("DELETE", "/me", 1), {})
        me = await self.call("GET", "/me", 1)
        self.assertIsNone(me["manager"])
        for table in ("holdings", "journal", "league_members", "windows", "managers"):
            col = "id" if table == "managers" else "manager_id"
            self.assertEqual(self.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = 1").fetchone()[0], 0, table)
        rows = await self.call("GET", "/leagues/overall", 2)
        self.assertEqual(len(rows), 1)
        ls = await self.call("GET", "/leagues", 2)
        self.assertEqual(next(x for x in ls if x["kind"] == "own")["of"], 1)   # лига жива, в ней один
        await self.call("DELETE", "/me", 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM leagues").fetchone()[0], 0)   # пустая исчезла
        await self.create(1)   # можно начать заново


if __name__ == "__main__":
    unittest.main()

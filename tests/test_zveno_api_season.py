"""Сервер «Звена»: события сезона — дедлайн с автопилотом и заданием недели, закрытие тура со снимком,
скрытый игрок, ступени, бюджет опоздавшего, недоступность сервера перед дедлайном."""
import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_zveno_api_fixtures import ApiCase, F, msk, squad_ids, team_body  # noqa: E402

from zveno import rules  # noqa: E402
from zveno_api import db  # noqa: E402

AFTER_1 = msk(2026, 10, 12, 9, 5)     # дедлайн тура 1 — пн 12.10 09:00


class Deadline(ApiCase):
    async def test_freeze_and_free(self):
        await self.create()
        self.now = AFTER_1
        t = await self.call("GET", "/team")
        self.assertEqual((t["tour"], t["free"]), (2, 1))
        t1 = await self.call("GET", "/team?tour=1")
        self.assertTrue(t1["locked"])
        self.assertEqual(sorted(squad_ids()), sorted([t1["lineup"]["G"], *t1["lineup"]["L1"]["F"], *t1["lineup"]["L1"]["D"],
                                                      *t1["lineup"]["L2"]["F"], *t1["lineup"]["L2"]["D"], *t1["bench"]]))
        self.assertEqual(len(t["album"]), 11)            # клубы основы тура 1
        self.assertNotIn("ermak", t["album"])            # ворота в запасе — не в альбоме
        # дедлайны копят бесплатные обмены до 5
        self.now = msk(2026, 12, 1, 12, 0)
        t = await self.call("GET", "/team")
        self.assertEqual(t["free"], rules.FREE_MAX)
        # дедлайн обработан один раз
        self.game.catch_up()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM lineups WHERE tour = 1").fetchone()[0], 1)

    async def test_team_after_deadline_plays_next_tour(self):
        self.now = AFTER_1
        t = await self.create()
        self.assertEqual(t["tour"], 2)
        self.assertTrue(t["unlimited"])                  # до своего первого дедлайна — без ограничений
        await self.call("GET", "/team?tour=1", status=404)


class Autopilot(ApiCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        for uid in (1, 2, 3, 4):
            await self.create(uid)
        await self.call("POST", "/team/keep", 2, {"id": F("rostov")}, status=400)   # не отдыхает — нечего оставлять
        self.player(F("rostov"))["status"] = "rest"
        self.reload()
        await self.call("POST", "/team/keep", 2, {"id": F("rostov")})
        await self.call("PUT", "/settings", 3, {"my_player": F("rostov")})
        await self.call("PUT", "/settings", 4, {"autopilot": False})

    async def test_warning_before(self):
        t = await self.call("GET", "/team", 1)
        w = [x for x in t["warnings"] if x["id"] == F("rostov")]
        self.assertEqual(w[0]["text"], "Нападающий21_0 пропустил 4 матча подряд. Мишка заменит его в пн 09:00")
        self.assertEqual((await self.call("GET", "/team", 2))["warnings"], [])       # «Оставить»
        self.assertEqual((await self.call("GET", "/team", 3))["warnings"], [])       # «Мой игрок»
        w4 = (await self.call("GET", "/team", 4))["warnings"]
        self.assertIn("включи автопилот", w4[0]["text"])

    async def test_deadline(self):
        before = await self.call("GET", "/team", 1)
        pick = before["warnings"][0]["in"]
        self.now = AFTER_1
        t = await self.call("GET", "/team", 1)
        self.assertEqual(t["lineup"]["L1"]["F"][0], pick)
        self.assertEqual(t["captain"], pick)             # «К» перешёл к замене
        self.assertNotIn(F("rostov"), t["bought"])
        self.assertEqual(t["free"], 1)                   # автопилот бесплатный
        p = self.player(F("rostov"))
        self.assertEqual(t["bank"], before["bank"] + p["price"] - self.player(pick)["price"])
        t1 = await self.call("GET", "/team?tour=1", 1)
        self.assertEqual(t1["lineup"]["L1"]["F"][0], pick)   # замена — до заморозки
        j = await self.call("GET", "/journal", 1)
        self.assertEqual(j[0]["kind"], "autopilot")
        self.assertTrue(j[0]["text"].startswith("Нападающий21_0 пропустил 4 матча подряд. Мишка заменил его, "
                                                "теперь на этом месте "), j[0]["text"])
        for uid in (2, 3, 4):
            t = await self.call("GET", "/team", uid)
            self.assertEqual(t["lineup"]["L1"]["F"][0], F("rostov"), uid)

    async def test_keep_expires_after_4_missed(self):
        self.matches["matches"] = [
            {"id": f"x{i}", "date": "2026-10-11", "tour": None, "home": "rostov", "away": "samara", "settled": True,
             "played": [], "goals": []} for i in range(4)]
        self.now = msk(2026, 10, 12, 8, 0)
        self.reload()
        t = await self.call("GET", "/team", 2)
        self.assertEqual(len(t["warnings"]), 1)          # клуб сыграл без него ещё 4 — автопилот снова в деле


class Mission(ApiCase):
    async def test_new_club_gives_exchange(self):
        await self.create(1)
        await self.create(2)
        self.now = msk(2026, 10, 25, 12, 0)             # собираем тур 3
        t = await self.call("GET", "/team", 1)
        self.assertEqual((t["tour"], t["free"]), (3, 2))
        self.assertFalse(t["mission"]["done"])
        self.assertEqual(len(t["mission"]["clubs_left"]), 26 - 11)
        body = team_body()
        body["lineup"]["L1"]["F"][2], body["bench"][2] = body["bench"][2], body["lineup"]["L1"]["F"][2]   # «Прогресс» в основу
        t = await self.call("PUT", "/team/lineup", 1, body)
        self.assertTrue(t["mission"]["done"])
        self.now = msk(2026, 10, 26, 9, 1)
        t1 = await self.call("GET", "/team", 1)
        t2 = await self.call("GET", "/team", 2)
        self.assertEqual(t1["free"], 4)                  # 2 + 1 за тур + 1 за задание
        self.assertEqual(t2["free"], 3)
        self.assertIn("progress", t1["album"])
        j = await self.call("GET", "/journal", 1)
        self.assertEqual(j[0]["kind"], "mission")
        self.assertIn("Прогресс", j[0]["text"])
        t3 = await self.call("GET", "/team?tour=3", 1)
        self.assertEqual(t3["mission"], {"done": True, "clubs_left": []})


def tour1(pts: list[int], ids: list[str]) -> dict:
    return {"1": {"m": pts, "best2": sum(sorted(pts, reverse=True)[:2]), "ids": ids}}


class Close(ApiCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.create()
        self.now = msk(2026, 10, 20, 12, 0)
        self.player(F("rostov"))["tours"] = tour1([5, 3, 7], ["m1", "m2", "m3"])
        self.player(F("krasnodar"))["tours"] = tour1([4], ["m1"])
        self.player(F("progress"))["tours"] = tour1([6], ["m9"])     # запасной с матчем
        self.matches["matches"] = [
            {"id": "m1", "date": "2026-10-13", "tour": 1, "home": "rostov", "away": "krasnodar", "settled": True,
             "played": [F("rostov"), F("krasnodar")],
             "goals": [{"team": "rostov", "author": F("rostov"), "assists": [F("krasnodar")]}]},
        ]
        self.reload()

    async def test_provisional_then_snapshot(self):
        t = await self.call("GET", "/team?tour=1")
        self.assertTrue(t["points"]["provisional"])
        self.assertEqual(t["points"]["total"], 13 * 2 + 5)   # без автозамен, пока тур не закрыт
        self.tours["tours"][0]["closed"] = True
        self.now = msk(2026, 10, 22, 12, 30)
        self.reload()
        t = await self.call("GET", "/team?tour=1")
        self.assertFalse(t["points"]["provisional"])
        self.assertEqual(t["points"]["subs"], [[F("belgorod"), F("progress")]])
        self.assertEqual(t["points"]["total"], 13 * 2 + 5 + 6)
        self.assertEqual(t["lineup"]["L1"]["F"][2], F("progress"))
        self.assertEqual(t["bench"][2], F("belgorod"))
        self.assertEqual(t["points"]["by_id"][F("rostov")], {"matches": [5, 3, 7], "best2": 12, "synergy": 1, "mult": 2,
                                                            "points": 26, "total": 26})
        j = await self.call("GET", "/journal")
        self.assertEqual(j[0]["kind"], "autosub")
        # опоздавшая правка протокола — снимок не меняется
        self.player(F("rostov"))["tours"] = tour1([15, 13, 17], ["m1", "m2", "m3"])
        self.reload()
        t = await self.call("GET", "/team?tour=1")
        self.assertEqual(t["points"]["total"], 37)
        ls = await self.call("GET", "/leagues")
        self.assertEqual(next(x for x in ls if x["kind"] == "overall")["points"], 37)
        self.assertEqual(next(x for x in ls if x["kind"] == "month")["title"], "Октябрь")

    async def test_penalty_in_snapshot(self):
        # окно тура 3: два бесплатных (копились с туров 1 и 2) и один за 8 очков
        await self.call("POST", "/team/transfer", 1, {"out": F("dizelist"), "in": F("samara"), "pay": "free"})
        await self.call("POST", "/team/transfer", 1, {"out": F("kristall"), "in": F("sokol"), "pay": "free"})
        t = await self.call("POST", "/team/transfer", 1, {"out": F("bryansk"), "in": F("tambov"), "pay": "points"})
        self.assertEqual((t["tour"], t["points"]["penalty"]), (3, rules.FEE_POINTS))
        self.now = msk(2026, 11, 5, 12, 30)
        for r in self.tours["tours"][:3]:
            r["closed"] = True
        self.reload()
        t3 = await self.call("GET", "/team?tour=3")
        self.assertFalse(t3["points"]["provisional"])
        self.assertEqual(t3["points"]["penalty"], rules.FEE_POINTS)
        self.assertEqual(t3["points"]["total"], -rules.FEE_POINTS)


class Hidden(ApiCase):
    async def test_hidden_refund_and_fill(self):
        t0 = await self.create()
        hid = F("krasnodar")
        p = self.player(hid)
        self.pool["players"].remove(p)
        self.reload()
        t = await self.call("GET", "/team")
        self.assertIsNone(t["lineup"]["L1"]["F"][1])
        self.assertEqual(t["bank"], t0["bank"] + max(p["price"], t0["bought"][hid]))
        self.assertEqual(t["free"], 1)
        self.assertNotIn(hid, t["bought"])
        self.assertTrue(any(w["id"] is None for w in t["warnings"]))
        j = await self.call("GET", "/journal")
        self.assertIn("скрыли", j[0]["text"])
        t = await self.call("POST", "/team/transfer", 1, {"out": None, "in": F("samara"), "pay": "free"})
        self.assertEqual(t["lineup"]["L1"]["F"][1], F("samara"))
        self.assertEqual(t["free"], 1)                   # до первого дедлайна — без ограничений

    async def test_autopilot_fills_empty_place(self):
        await self.create()
        self.pool["players"].remove(self.player(F("krasnodar")))
        self.reload()
        self.now = AFTER_1
        t = await self.call("GET", "/team")
        self.assertIsNotNone(t["lineup"]["L1"]["F"][1])
        self.assertEqual(len(t["bought"]), 15)

    async def test_broken_pool_is_not_hiding(self):
        await self.create()
        for x in squad_ids()[:5]:
            self.pool["players"].remove(self.player(x))
        self.reload()
        t = await self.call("GET", "/team")
        self.assertEqual(len(t["bought"]), 15)           # треть команды пропала — не верим, ждём данных


class Steps(ApiCase):
    async def test_regroup_and_late_joiner(self):
        for uid in (1, 2, 3):
            await self.create(uid)
        self.player(F("rostov"))["tours"] = tour1([9], ["m1"])
        await self.call("POST", "/team/transfer", 2, {"out": F("rostov"), "in": F("samara")})
        self.reload()
        # все три тура октября закрыты — ступени на ноябрь
        self.now = msk(2026, 11, 5, 13, 0)
        for r in self.tours["tours"][:3]:
            r["closed"] = True
        # стоимость выросла: команды активных дороже 100 000
        for p in self.pool["players"]:
            p["price"] += 1000
        self.reload()
        ls = await self.call("GET", "/leagues", 1)
        step = next(x for x in ls if x["kind"] == "step")
        self.assertEqual((step["title"], step["step"], step["group"], step["place"]), ("Высшая лига", 0, 1, 1))
        rows = await self.call("GET", f"/leagues/{step['id']}", 1)
        self.assertEqual([r["points"] for r in rows], [0, 0, 0])   # очки ступени — за ноябрь
        overall = await self.call("GET", "/leagues/overall", 1)
        self.assertEqual(overall[0], {"place": 1, "team_name": "Ледяные Буревестники", "points": 18, "me": True})
        await self.call("GET", f"/leagues/{step['id']}", 2)
        # опоздавший: бюджет — медиана активных, в Коробку, место «с момента вступления»
        me = await self.call("GET", "/me", 1)
        await self.call("GET", "/me", 2)
        await self.call("GET", "/me", 3)
        t = await self.create(9)
        me = await self.call("GET", "/me", 9)
        self.assertEqual(me["manager"]["budget"], rules.BUDGET + 15 * 500)
        self.assertEqual(me["manager"]["start_tour"], 5)
        ls = await self.call("GET", "/leagues", 9)
        step = next(x for x in ls if x["kind"] == "step")
        self.assertEqual(step["step"], rules.STEP_BOX)
        await self.call("GET", "/leagues/step:2026-11:0:1", 9, status=404)   # чужая группа не видна
        overall = next(x for x in ls if x["kind"] == "overall")
        self.assertEqual((overall["since_tour"], overall["since_place"]), (5, 1))
        self.assertEqual(t["bank"], rules.BUDGET + 15 * 500 - sum(self.player(x)["price"] for x in squad_ids()))


class Downtime(ApiCase):
    async def test_six_hours_before_deadline(self):
        await self.create()
        db.set_meta(self.conn, "beat", (msk(2026, 10, 11, 23, 0)).isoformat())
        self.now = msk(2026, 10, 12, 8, 0)
        self.game.note_start()                           # лежали 9 часов перед дедлайном
        self.now = AFTER_1
        t = await self.call("GET", "/team")
        self.assertEqual(t["free"], 2)

    async def test_short_gap(self):
        await self.create()
        db.set_meta(self.conn, "beat", (msk(2026, 10, 12, 7, 0)).isoformat())
        self.now = msk(2026, 10, 12, 8, 0)
        self.game.note_start()
        self.now = AFTER_1 + timedelta(minutes=1)
        self.assertEqual((await self.call("GET", "/team"))["free"], 1)


if __name__ == "__main__":
    unittest.main()

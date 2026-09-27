"""Фикстуры сервера «Звена»: tours.json, pool.json, matches.json в формате контракта (раздел 2) и
база для тестов API на aiohttp.test_utils. Своих тестов здесь нет."""
import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from zveno import tours as ztours  # noqa: E402
from zveno.rules import TZ  # noqa: E402
from zveno_api import auth, db  # noqa: E402
from zveno_api.config import PREFIX, Config  # noqa: E402
from zveno_api.data import Data, load_teams  # noqa: E402
from zveno_api.game import Game  # noqa: E402
from zveno_api.routes import make_app  # noqa: E402

TOKEN = "123456:TEST-token-for-zveno"
ORIGIN = "https://arpicasso.github.io"
TEAMS = load_teams(ROOT / "teams.json")
CLUBS = [t["id"] for t in TEAMS]


def msk(*a) -> datetime:
    return datetime(*a, tzinfo=TZ)


def make_tours(status="open", first_tour=1, closed=(), now=None) -> dict:
    rows = ztours.tour_table([], CLUBS, msk(2026, 10, 1))
    for r in rows:
        r["closed"] = r["t"] in closed
        r["games"] = {c: 2 for c in CLUBS}
    return {"season": "2026/27", "updated": "2026-10-10T12:00:00+03:00", "status": status,
            "market_opened_at": "2026-10-10T23:59:00+03:00" if status == "open" else None,
            "first_tour": first_tour if status == "open" else None, "tour_now": None, "tour_next": first_tour,
            "tours": rows, "clubs_with_protocol": 26}


def D(club: str, k: int = 0) -> str:
    return f"p:{1000 + CLUBS.index(club) * 10 + k}"


def F(club: str, k: int = 0) -> str:
    return f"p:{2000 + CLUBS.index(club) * 10 + k}"


def G(club: str) -> str:
    return f"g:{club}"


def make_pool() -> dict:
    players = []
    for i, c in enumerate(CLUBS):
        name = next(t["name"] for t in TEAMS if t["id"] == c)
        players.append({"id": G(c), "pid": None, "slot": "G", "name": f"Ворота клуба «{name}»", "club": c, "number": None,
                        "price": 4000 + 100 * (i % 10), "promise": 3.5, "form": False, "status": "ok", "new": False,
                        "price_monday": 4000 + 100 * (i % 10), "tours": {}})
        for k in range(2):
            price = 4000 + 200 * ((i + k) % 8)
            players.append({"id": D(c, k), "pid": 1000 + i * 10 + k, "slot": "D", "name": f"Защитников{i}_{k} Иван",
                            "club": c, "number": 2 + k, "price": price, "promise": 2.0, "form": False, "status": "ok",
                            "new": False, "price_monday": price, "tours": {}})
        for k in range(3):
            price = 4500 + 300 * ((i + k) % 9)
            players.append({"id": F(c, k), "pid": 2000 + i * 10 + k, "slot": "F", "name": f"Нападающий{i}_{k} Пётр",
                            "club": c, "number": 10 + k, "price": price, "promise": 3.0, "form": False, "status": "ok",
                            "new": False, "price_monday": price, "tours": {}})
    return {"updated": "2026-10-10T12:00:00+03:00", "tour_next": 1, "players": players}


def make_matches() -> dict:
    return {"updated": "2026-10-10T12:00:00+03:00", "matches": []}


# Состав: у каждого места свой клуб, кроме ворот
SQUAD = {
    "lineup": {"G": G("ryazan-vdv"),
               "L1": {"F": [F("rostov"), F("krasnodar"), F("belgorod")], "D": [D("polet"), D("samara")]},
               "L2": {"F": [F("kristall"), F("bryansk"), F("dizelist")], "D": [D("sokol"), D("tambov")]}},
    "bench": [G("ermak"), D("proton"), F("progress"), F("arktika")],
}


def squad_ids(sq=SQUAD) -> list[str]:
    lu = sq["lineup"]
    return [lu["G"], *lu["L1"]["F"], *lu["L1"]["D"], *lu["L2"]["F"], *lu["L2"]["D"], *sq["bench"]]


def team_body(**kw) -> dict:
    b = {"name": ["Ледяные", "Буревестники"], "fav_club": "ryazan-vdv", **copy.deepcopy(SQUAD),
         "captain": F("rostov")}
    b.update(kw)
    return b


class ApiCase(unittest.IsolatedAsyncioTestCase):
    """Сервер на тестовом порту, данные — фикстуры, время — self.now (двигается в тестах)."""

    START = msk(2026, 10, 10, 12, 0)

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.now = self.START
        self.tours, self.pool, self.matches = make_tours(), make_pool(), make_matches()
        self.conn = db.connect(self.dir / "zveno.db")
        self.data = Data()
        self.data.set_teams(TEAMS)
        self.data.set(self.tours, self.pool, self.matches)
        self.game = Game(self.conn, self.data, clock=lambda: self.now)
        self.cfg = Config(bot_token=TOKEN, db=str(self.dir / "zveno.db"), data_url=str(self.dir / "data"),
                          origin=ORIGIN, cache_dir=self.dir / "cache")
        self.app = make_app(self.cfg, self.game)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.conn.close()
        self.tmp.cleanup()

    def reload(self) -> None:
        """Данные поменялись в тесте — как будто задача подтянула свежие файлы."""
        self.data.set(self.tours, self.pool, self.matches)
        self.game.on_data()

    def player(self, sid: str) -> dict:
        return next(p for p in self.pool["players"] if p["id"] == sid)

    def headers(self, uid: int = 1, name: str = "Аня") -> dict:
        init = auth.make_init_data({"id": uid, "first_name": name}, TOKEN, int(self.now.timestamp()))
        return {"Authorization": f"tma {init}"}

    async def call(self, method: str, path: str, uid: int | None = 1, body=None, status: int | None = 200, **kw):
        headers = self.headers(uid) if uid is not None else {}
        headers.update(kw.pop("headers", {}))
        r = await self.client.request(method, PREFIX + path, json=body, headers=headers, **kw)
        data = await r.json()
        if status is not None:
            self.assertEqual(r.status, status, data)
        return data

    async def create(self, uid: int = 1, **kw) -> dict:
        return await self.call("POST", "/team", uid, team_body(**kw), status=201)


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)

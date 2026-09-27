"""Зачёты «Звена» (ADR-014, раздел 9): своя лига по коду, клубная или конференция, ступень, месяц, круг,
общий. Очки — только из снимков закрытых туров, таблицы считаются на лету: удалили менеджера — таблица
уже без него. Имя из Telegram — только в своей лиге и только по галочке.
"""
import json
import re

from zveno import names, rules

from . import db as dbm
from .game import (CIRCLES, CLUB_LEAGUE_MIN, CONF, MONTHS, OWN_LEAGUES_MAX, OWN_MEMBERS_MAX, OWN_MEMBERSHIPS_MAX,
                   TABLE_TOP, ApiError, Game, iso)


def _rank(rows: list[dict]) -> list[dict]:
    """По очкам, при равенстве — кто раньше собрал команду. Места при равных очках одинаковые (1, 2, 2, 4)."""
    rows = sorted(rows, key=lambda r: (-r["points"], r["created_at"], r["id"]))
    place, prev = 0, None
    for i, r in enumerate(rows, start=1):
        if r["points"] != prev:
            place, prev = i, r["points"]
        r["place"] = place
    return rows


class Leagues:
    def __init__(self, game: Game):
        self.g = game
        self.db = game.db

    # ---------- очки ----------

    def points(self, tours: list[int] | None = None, where: str = "", args: tuple = ()) -> list[dict]:
        """Очки менеджеров за туры (None — все) среди тех, кто подходит под where."""
        scope = ""
        targs: tuple = ()
        if tours is not None:
            scope = f"AND s.tour IN ({','.join('?' * len(tours))})" if tours else "AND 0"
            targs = tuple(tours)
        q = f"""SELECT m.id, m.name, m.created_at, m.show_tg_name, m.tg_name, COALESCE(SUM(s.total), 0) AS points
                FROM managers m LEFT JOIN scores s ON s.manager_id = m.id {scope}
                {('WHERE ' + where) if where else ''} GROUP BY m.id"""
        return _rank([dict(r) for r in self.db.execute(q, targs + args)])

    def tours_of(self, key: str, value) -> list[int]:
        return [r["t"] for r in self.g._tour_rows() if r.get(key) == value]

    def current_month(self) -> str | None:
        s = self.g.season()
        if s is None:
            return None
        last = self.db.execute("SELECT MAX(tour) FROM tour_state WHERE deadline_done IS NOT NULL").fetchone()[0]
        t = last or s.tour_next
        row = self.g._tour_row(t) if t else None
        return row["month"] if row else None

    def current_circle(self) -> int:
        last = self.db.execute("SELECT MAX(tour) FROM tour_state WHERE deadline_done IS NOT NULL").fetchone()[0]
        return 1 if (last or 1) < rules.SECOND_CIRCLE else 2

    # ---------- какие лиги у менеджера ----------

    def club_scope(self, m) -> tuple[str, str, str, tuple]:
        """(id, kind, title, where): клубная — от 15 болельщиков клуба, иначе конференция клуба."""
        club = m["fav_club"]
        n = self.db.execute("SELECT COUNT(*) FROM managers WHERE fav_club = ?", (club,)).fetchone()[0]
        if n >= CLUB_LEAGUE_MIN:
            return f"club:{club}", "club", f"Болельщики «{self.g.club_name(club)}»", ("m.fav_club = ?", (club,))
        conf = (self.g.data.team.get(club) or {}).get("conf") or "west"
        clubs = [t["id"] for t in self.g.data.teams if t.get("conf") == conf] or [club]
        where = f"m.fav_club IN ({','.join('?' * len(clubs))})"
        return f"conf:{conf}", "conf", f"Конференция «{CONF.get(conf, conf)}»", (where, tuple(clubs))

    def step_of(self, uid: int):
        month = self.g.step_month()
        if not month:
            return None
        return self.db.execute("SELECT * FROM steps WHERE month = ? AND manager_id = ?", (month, uid)).fetchone()

    def step_title(self, step: int, grp: int, month: str) -> str:
        n = self.db.execute("SELECT COUNT(DISTINCT grp) FROM steps WHERE month = ? AND step = ?", (month, step)).fetchone()[0]
        base = rules.STEP_NAMES[step] if step < len(rules.STEP_NAMES) else rules.STEP_NAMES[-1]
        if n <= 1:
            return base
        teams = self.g.data.teams
        club = teams[(grp - 1) % len(teams)]["name"] if teams else str(grp)
        return f"{base} «{club}»" + (f" {(grp - 1) // len(teams) + 1}" if teams and grp > len(teams) else "")

    def _entry(self, lid: str, kind: str, title: str, rows: list[dict], uid: int, **extra) -> dict:
        me = next((r for r in rows if r["id"] == uid), None)
        return {"id": lid, "kind": kind, "title": title, "place": me["place"] if me else None, "of": len(rows),
                "points": me["points"] if me else 0, **extra}

    def mine(self, uid: int) -> list[dict]:
        m = self.g._need_manager(uid)
        out = []
        for lg in self.db.execute("""SELECT l.* FROM leagues l JOIN league_members x ON x.league_id = l.id
                                     WHERE x.manager_id = ? ORDER BY l.id""", (uid,)).fetchall():
            rows = self.points(None, "m.id IN (SELECT manager_id FROM league_members WHERE league_id = ?)", (lg["id"],))
            out.append(self._entry(f"own:{lg['id']}", "own", names.title(json.loads(lg["name"])), rows, uid,
                                   code=lg["code"], owner=lg["owner_id"] == uid))
        lid, kind, title, (where, args) = self.club_scope(m)
        out.append(self._entry(lid, kind, title, self.points(None, where, args), uid))
        st = self.step_of(uid)
        if st:
            ts = self.tours_of("month", st["month"])
            rows = self.points(ts, "m.id IN (SELECT manager_id FROM steps WHERE month = ? AND step = ? AND grp = ?)",
                               (st["month"], st["step"], st["grp"]))
            out.append(self._entry(f"step:{st['month']}:{st['step']}:{st['grp']}", "step",
                                   self.step_title(st["step"], st["grp"], st["month"]), rows, uid,
                                   step=st["step"], group=st["grp"], month=st["month"]))
        month = self.current_month()
        if month:
            out.append(self._entry(f"month:{month}", "month", MONTHS[int(month[5:])],
                                   self.points(self.tours_of("month", month)), uid, month=month))
        c = self.current_circle()
        out.append(self._entry(f"circle:{c}", "circle", CIRCLES[c], self.points(self.tours_of("circle", c)), uid))
        rows = self.points(None)
        extra = {}
        s = self.g.season()
        first = (s.first_tour if s else None) or 1
        if m["start_tour"] > first:   # опоздавший: ещё место «с момента вступления»
            since = self.points([r["t"] for r in self.g._tour_rows() if r["t"] >= m["start_tour"]])
            me = next(r for r in since if r["id"] == uid)
            extra = {"since_tour": m["start_tour"], "since_place": me["place"], "since_points": me["points"]}
        out.append(self._entry("overall", "overall", "Общий зачёт", rows, uid, **extra))
        return out

    # ---------- таблица ----------

    def table(self, uid: int, lid: str) -> list[dict]:
        m = self.g._need_manager(uid)
        nope = ApiError(404, "Такой лиги у тебя нет.")
        kind, _, rest = lid.partition(":")
        own = False
        if kind == "own":
            if not rest.isdigit() or not self.db.execute(
                    "SELECT 1 FROM league_members WHERE league_id = ? AND manager_id = ?", (int(rest), uid)).fetchone():
                raise nope
            rows = self.points(None, "m.id IN (SELECT manager_id FROM league_members WHERE league_id = ?)", (int(rest),))
            own = True
        elif kind in ("club", "conf"):
            cid, _, _, (where, args) = self.club_scope(m)
            if cid != lid:
                raise nope
            rows = self.points(None, where, args)
        elif kind == "step":
            st = self.step_of(uid)
            if not st or lid != f"step:{st['month']}:{st['step']}:{st['grp']}":
                raise nope   # видна только своя группа
            rows = self.points(self.tours_of("month", st["month"]),
                               "m.id IN (SELECT manager_id FROM steps WHERE month = ? AND step = ? AND grp = ?)",
                               (st["month"], st["step"], st["grp"]))
        elif kind == "month":
            ts = self.tours_of("month", rest)
            if not ts:
                raise nope
            rows = self.points(ts)
        elif kind == "circle":
            if rest not in ("1", "2"):
                raise nope
            rows = self.points(self.tours_of("circle", int(rest)))
        elif lid == "overall":
            rows = self.points(None)
        else:
            raise nope
        out = []
        for r in rows:
            if r["place"] > TABLE_TOP and r["id"] != uid and len(out) >= TABLE_TOP:
                continue
            row = {"place": r["place"], "team_name": names.title(json.loads(r["name"])), "points": r["points"],
                   "me": r["id"] == uid}
            if own and r["show_tg_name"] and r["tg_name"]:
                row["tg_name"] = r["tg_name"]
            out.append(row)
        return out

    # ---------- своя лига ----------

    def create(self, uid: int, body: dict) -> dict:
        self.g._need_manager(uid)
        name = body.get("name")
        if not names.is_valid(name):
            raise ApiError(400, "Название лиги — из конструктора: прилагательное и существительное.")
        n = self.db.execute("SELECT COUNT(*) FROM leagues WHERE owner_id = ?", (uid,)).fetchone()[0]
        if n >= OWN_LEAGUES_MAX:
            raise ApiError(400, f"Своих лиг можно создать не больше {OWN_LEAGUES_MAX}.")
        self._room_for(uid)
        code = self.g.new_code()
        with dbm.tx(self.db):
            cur = self.db.execute("INSERT INTO leagues (code, name, owner_id, created_at) VALUES (?, ?, ?, ?)",
                                  (code, json.dumps(list(name), ensure_ascii=False), uid, iso(self.g.now())))
            lid = cur.lastrowid
            self.db.execute("INSERT INTO league_members (league_id, manager_id, joined_at) VALUES (?, ?, ?)",
                            (lid, uid, iso(self.g.now())))
        return {"id": f"own:{lid}", "code": code}

    def _room_for(self, uid: int) -> None:
        n = self.db.execute("SELECT COUNT(*) FROM league_members WHERE manager_id = ?", (uid,)).fetchone()[0]
        if n >= OWN_MEMBERSHIPS_MAX:
            raise ApiError(400, f"Своих лиг у тебя уже {OWN_MEMBERSHIPS_MAX} — это предел.")

    def join(self, uid: int, body: dict) -> dict:
        self.g._need_manager(uid)
        raw = body.get("code")
        code = re.sub(r"[^A-Z0-9]", "", re.sub(r"^\s*LG-", "", str(raw or "").upper()))
        if not code:
            raise ApiError(400, "Введи код лиги — его даёт тот, кто её создал.")
        lg = self.db.execute("SELECT * FROM leagues WHERE code = ?", (code,)).fetchone()
        if lg is None:
            raise ApiError(404, "Лиги с таким кодом нет. Проверь код у того, кто позвал.")
        if self.db.execute("SELECT 1 FROM league_members WHERE league_id = ? AND manager_id = ?", (lg["id"], uid)).fetchone():
            return {"id": f"own:{lg['id']}"}
        self._room_for(uid)
        n = self.db.execute("SELECT COUNT(*) FROM league_members WHERE league_id = ?", (lg["id"],)).fetchone()[0]
        if n >= OWN_MEMBERS_MAX:
            raise ApiError(400, "В этой лиге больше нет мест.")
        with dbm.tx(self.db):
            self.db.execute("INSERT INTO league_members (league_id, manager_id, joined_at) VALUES (?, ?, ?)",
                            (lg["id"], uid, iso(self.g.now())))
        return {"id": f"own:{lg['id']}"}

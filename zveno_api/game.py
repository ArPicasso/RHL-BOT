"""Состояние менеджеров «Звена» и события сезона: команда, обмены, дедлайн, закрытие тура, ступени.

Правила игры — в движке (`zveno.manager`, `zveno.rules`): стоимость продажи, проверка состава, очки,
автозамены, автопилот, задание, ступени. Здесь — только то, что держит сервер (контракт, раздел 3):
когда что вызывать, что хранить и что замораживать.

Состав менеджера — «рабочий», на tour_next. В дедлайн тура T он копируется в `lineups` и дальше не
меняется: обмен после дедлайна проходит сразу, а на лёд попадает со следующего тура. Когда движок
впервые публикует у тура `closed: true`, очки тура считаются по замороженному составу и ложатся
снимком в `scores` — больше они не меняются (ADR-014, раздел 3; контракт, раздел 7, п. 1).
"""
import copy
import json
import logging
import secrets
import statistics
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from zveno import manager, names, rules, tours
from zveno.rules import TZ

from . import db as dbm
from .data import Data

log = logging.getLogger("zveno_api.game")

DOW = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
SLOT_WORD = manager.SLOT_WORD
SLOT_FOR = manager.SLOT_FOR
ACTIVE_DAYS = 28                 # «активный» для бюджета опоздавшего — открывал «Звено» за 4 недели
HIDDEN_SANITY = 0.9              # пул потерял больше 10% наклеек из составов — не верим, что это скрытие
OWN_LEAGUES_MAX = 10             # своих лиг создать
OWN_MEMBERSHIPS_MAX = 30         # своих лиг, где состоишь
OWN_MEMBERS_MAX = 1000
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # без 0/O и 1/I
CODE_LEN = 6
JOURNAL_LIMIT = 200
TABLE_TOP = 100
CLUB_LEAGUE_MIN = 15             # клубная лига — от 15 менеджеров, иначе конференция
MONTHS = {1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель", 5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
          9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"}
MONTHS_IN = {1: "январе", 2: "феврале", 3: "марте", 4: "апреле", 5: "мае", 6: "июне", 7: "июле", 8: "августе",
             9: "сентябре", 10: "октябре", 11: "ноябре", 12: "декабре"}
CONF = {"west": "Запад", "east": "Восток"}
CIRCLES = {1: "Первый круг", 2: "Второй круг"}


class ApiError(Exception):
    """Ошибка для болельщика: HTTP-код и текст по-русски."""

    def __init__(self, status: int, text: str, **extra):
        super().__init__(text)
        self.status, self.text, self.extra = status, text, extra


def iso(dt: datetime) -> str:
    return dt.astimezone(TZ).isoformat(timespec="seconds")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(TZ)


def num(n: int) -> str:
    return f"{int(n):,}".replace(",", " ")


def when(dt: datetime) -> str:
    """«пн 09:00» по Москве."""
    dt = dt.astimezone(TZ)
    return f"{DOW[dt.weekday()]} {dt:%H:%M}"


def surname(p: dict | None) -> str:
    if not p:
        return "игрок скрыт"
    if p["slot"] == "G":
        return p["name"]
    return p["name"].split()[0] if p.get("name") else p["id"]


def rested(p: dict | None) -> str:
    """«Максимов пропустил 4 матча подряд» — фамилия в именительном, без склонения."""
    return f"{surname(p)} пропустил {rules.REST_MISSED} матча подряд"


def with_totals(by_id: dict) -> dict:
    """У каждой наклейки готовый total — с капитаном и сыгранностью, мини-апп правил не повторяет."""
    return {x: {**v, "total": v["points"]} for x, v in by_id.items()}


def full_name(p: dict | None) -> str:
    return p["name"] if p else "игрок скрыт"


# ---------- состав: места и заглушки ----------

def empty_squad() -> dict:
    return {"lineup": {"G": None, "L1": {"F": [None] * 3, "D": [None] * 2}, "L2": {"F": [None] * 3, "D": [None] * 2}},
            "bench": [None] * len(rules.BENCH), "captain": None, "assistant": None}


def normalize(lineup, bench) -> dict:
    """Состав из тела запроса: строго ворота, два звена 3+2 и запас из четырёх. Иначе ApiError."""
    bad = ApiError(400, "Состав: ворота клуба, два звена по 3 нападающих и 2 защитника и запас из четырёх.")
    if not isinstance(lineup, dict) or not isinstance(bench, list) or len(bench) != len(rules.BENCH):
        raise bad

    def sid(x):
        if x is None:
            return None
        if not isinstance(x, str) or not x or len(x) > 64:
            raise bad
        return x

    out = {"G": sid(lineup.get("G"))}
    for line in rules.LINES:
        ln = lineup.get(line)
        if not isinstance(ln, dict):
            raise bad
        out[line] = {}
        for slot in ("F", "D"):
            xs = ln.get(slot)
            if not isinstance(xs, list) or len(xs) != rules.LINE[slot]:
                raise bad
            out[line][slot] = [sid(x) for x in xs]
    return {"lineup": out, "bench": [sid(x) for x in bench]}


def places(sq: dict) -> list[tuple[str, str | None]]:
    """(слот места, id) по порядку: основа, потом запас."""
    return [(s, x) for s, _, x in manager.positions(sq["lineup"])] + list(zip(rules.BENCH, sq["bench"]))


def main_ids(sq: dict) -> list[str]:
    return manager.lineup_ids(sq["lineup"])


def all_ids(sq: dict) -> list[str]:
    return [x for _, x in places(sq) if x]


def replace(sq: dict, old: str | None, new: str | None, slot: str | None = None) -> dict:
    """Новый состав: new на месте old. old=None — первое пустое место слота slot."""
    sq = copy.deepcopy(sq)
    lu = sq["lineup"]

    def hit(s, x):
        return x == old if old is not None else (x is None and s == slot)

    if hit("G", lu.get("G")):
        lu["G"] = new
        return _roles(sq, old, new)
    for line in rules.LINES:
        for s in ("D", "F"):
            xs = lu[line][s]
            for i, x in enumerate(xs):
                if hit(s, x):
                    xs[i] = new
                    return _roles(sq, old, new)
    for i, (s, x) in enumerate(zip(rules.BENCH, sq["bench"])):
        if hit(s, x):
            sq["bench"][i] = new
            return _roles(sq, old, new)
    raise KeyError(old)


def _roles(sq: dict, old, new) -> dict:
    """«К» и «А» переходят к наклейке, которая встала на место ушедшей."""
    if old is not None:
        if sq.get("captain") == old:
            sq["captain"] = new
        if sq.get("assistant") == old:
            sq["assistant"] = new
    return sq


def with_placeholders(sq: dict) -> tuple[dict, set[str]]:
    """Пустые места (скрытый игрок) — заглушки «empty:N»: движок принимает только id."""
    sq = copy.deepcopy(sq)
    ph: set[str] = set()
    lu = sq["lineup"]
    if lu.get("G") is None:
        lu["G"] = f"empty:{len(ph)}"
        ph.add(lu["G"])
    for line in rules.LINES:
        for s in ("D", "F"):
            xs = lu[line][s]
            for i, x in enumerate(xs):
                if x is None:
                    xs[i] = f"empty:{len(ph)}"
                    ph.add(xs[i])
    for i, x in enumerate(sq["bench"]):
        if x is None:
            sq["bench"][i] = f"empty:{len(ph)}"
            ph.add(sq["bench"][i])
    return sq, ph


def bench_after(bench: list, subs: list) -> list:
    """Запас после автозамен: вышедший из основы садится на место вошедшего."""
    back = {b: a for a, b in subs}
    return [back.get(x, x) for x in bench]


@dataclass
class Season:
    status: str
    tour_next: int | None
    tour_now: int | None
    deadline: datetime | None
    first_tour: int | None

    def as_json(self) -> dict:
        return {"status": self.status, "tour_next": self.tour_next, "tour_now": self.tour_now,
                "deadline": iso(self.deadline) if self.deadline else None, "first_tour": self.first_tour}


class Game:
    def __init__(self, conn, data: Data, clock=None):
        self.db = conn
        self.data = data
        self.clock = clock or (lambda: datetime.now(TZ))

    def now(self) -> datetime:
        t = self.clock()
        if t.tzinfo is None:
            raise ValueError("нужно время с поясом")
        return t.astimezone(TZ)

    # ---------- сезон ----------

    def season(self, now: datetime | None = None) -> Season | None:
        t = self.data.tours
        if not t:
            return None
        now = now or self.now()
        status = t.get("status") or "prolog"
        first = t.get("first_tour")
        nt = tours.next_tour(t, now)
        if nt is not None and status == "open" and first:
            nt = max(nt, first)
        cur = tours.tour_at(t, now)
        if cur is not None and not (status == "open" and tours.deadline_of(t, cur) <= now and cur >= (first or 1)):
            cur = None
        return Season(status, nt, cur, tours.deadline_of(t, nt) if nt else None, first)

    def _tour_rows(self) -> list[dict]:
        return sorted((self.data.tours or {}).get("tours", []), key=lambda r: r["t"])

    def _tour_row(self, t: int) -> dict | None:
        return next((r for r in self._tour_rows() if r["t"] == t), None)

    def _need_data(self) -> Season:
        s = self.season()
        if s is None or not self.data.loaded:
            raise ApiError(503, "Данные «Звена» ещё не загрузились. Попробуй через пару минут.")
        return s

    def _need_next(self) -> tuple[Season, int]:
        s = self._need_data()
        if s.tour_next is None:
            raise ApiError(409, "Сезон «Звена» закончился: составы больше не меняются.")
        return s, s.tour_next

    # ---------- менеджер ----------

    def manager(self, uid: int):
        return self.db.execute("SELECT * FROM managers WHERE id = ?", (uid,)).fetchone()

    def _need_manager(self, uid: int):
        m = self.manager(uid)
        if m is None:
            raise ApiError(404, "У тебя ещё нет команды в «Звене». Собери звено — это пара минут.")
        return m

    def holdings(self, uid: int) -> dict[str, dict]:
        return {r["sid"]: dict(r) for r in self.db.execute("SELECT * FROM holdings WHERE manager_id = ?", (uid,))}

    def window(self, uid: int, tour: int) -> dict:
        r = self.db.execute("SELECT * FROM windows WHERE manager_id = ? AND tour = ?", (uid, tour)).fetchone()
        return dict(r) if r else {"manager_id": uid, "tour": tour, "free_used": 0, "paid_points": 0, "paid_ice": 0,
                                  "boost": None}

    def _save_window(self, w: dict) -> None:
        self.db.execute(
            """INSERT INTO windows (manager_id, tour, free_used, paid_points, paid_ice, boost) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(manager_id, tour) DO UPDATE SET free_used = excluded.free_used,
               paid_points = excluded.paid_points, paid_ice = excluded.paid_ice, boost = excluded.boost""",
            (w["manager_id"], w["tour"], w["free_used"], w["paid_points"], w["paid_ice"], w["boost"]))

    def journal_add(self, uid: int, kind: str, text: str, at: datetime | None = None) -> None:
        self.db.execute("INSERT INTO journal (manager_id, at, kind, text) VALUES (?, ?, ?, ?)",
                        (uid, iso(at or self.now()), kind, text))

    def mascot(self, club: str) -> str:
        t = self.data.team.get(club) or {}
        return (t.get("mascot") or {}).get("name") or "Талисман"

    def club_name(self, club: str) -> str:
        return (self.data.team.get(club) or {}).get("name") or club

    def touch(self, uid: int, display: str = "") -> None:
        """Открыл «Звено»: для «трёх туров без открытия» и ступеней. Имя — только по галочке."""
        m = self.manager(uid)
        if m is None:
            return
        tg = display[:64] if m["show_tg_name"] and display else None
        self.db.execute("UPDATE managers SET last_seen = ?, tg_name = ? WHERE id = ?", (iso(self.now()), tg, uid))

    def me(self, uid: int) -> dict:
        s = self.season()
        m = self.manager(uid)
        return {"manager": self._manager_json(m) if m else None, "season": s.as_json() if s else None}

    def _manager_json(self, m) -> dict:
        name = json.loads(m["name"])
        return {"name": name, "title": names.title(name), "fav_club": m["fav_club"], "my_player": m["my_player"],
                "settings": {"autopilot": bool(m["autopilot"]), "messages": bool(m["messages"]),
                             "show_tg_name": bool(m["show_tg_name"])},
                "start_tour": m["start_tour"], "budget": m["budget"], "created_at": m["created_at"]}

    # ---------- бюджет опоздавшего ----------

    def team_value(self, m, hold: dict | None = None) -> int:
        """Касса плюс «Отдашь за» всех наклеек — стоимость продажи команды."""
        hold = self.holdings(m["id"]) if hold is None else hold
        return m["bank"] + sum(self.sale(h) for h in hold.values())

    def sale(self, h: dict) -> int:
        p = self.data.idx.get(h["sid"])
        if p is None:
            return manager.sale_price(h["bought"], h["last_price"], "ok", hidden=True)
        return manager.sale_price(h["bought"], p["price"], p["status"])

    def late_budget(self, now: datetime) -> int:
        """Медиана стоимости продажи активных команд, не меньше 100 000 (ADR-014, раздел 9)."""
        since = iso(now - timedelta(days=ACTIVE_DAYS))
        values = [self.team_value(m) for m in self.db.execute("SELECT * FROM managers WHERE last_seen >= ?", (since,))]
        if not values:
            return rules.LATE_BUDGET_MIN
        return max(rules.LATE_BUDGET_MIN, int(statistics.median(values)))

    def budget_for_new(self, s: Season, now: datetime) -> int:
        first = s.first_tour or 1
        try:
            late = tours.deadline_of(self.data.tours, first) <= now
        except KeyError:
            late = False
        return self.late_budget(now) if late else rules.BUDGET

    # ---------- команда ----------

    def create_team(self, uid: int, body: dict, display: str = "") -> dict:
        self.catch_up()
        s, tn = self._need_next()
        if s.status != "open":
            raise ApiError(409, "Рынок «Звена» ещё не открылся: пока собирай черновик в Прологе.")
        if self.manager(uid) is not None:
            raise ApiError(409, "Команда у тебя уже есть. Меняй её через «Обмен».")
        name = body.get("name")
        if not names.is_valid(name):
            raise ApiError(400, "Название — из конструктора: прилагательное и существительное.")
        fav = body.get("fav_club")
        if fav not in self.data.team:
            raise ApiError(400, "Выбери любимый клуб — один из 26 клубов РХЛ.")
        sq = normalize(body.get("lineup"), body.get("bench"))
        if any(x is None for _, x in places(sq)):
            raise ApiError(400, "Собери всех: ворота клуба, два звена и запас из четырёх.")
        now = self.now()
        budget = self.budget_for_new(s, now)
        errors = manager.validate_squad(sq, self.data.idx, budget)
        if errors:
            raise ApiError(400, errors[0], errors=errors)
        sq["captain"], sq["assistant"] = self._roles_from(sq, body)
        my = body.get("my_player")
        my = my if isinstance(my, str) and my in self.data.idx else None
        cost = sum(self.data.idx[x]["price"] for x in all_ids(sq))
        with dbm.tx(self.db):
            self.db.execute(
                """INSERT INTO managers (id, name, fav_club, my_player, created_at, start_tour, budget, bank, free,
                   unlimited_until, squad, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                (uid, json.dumps(list(name), ensure_ascii=False), fav, my, iso(now), tn, budget, budget - cost,
                 iso(s.deadline), json.dumps(sq), iso(now)))
            for slot, x in places(sq):
                p = self.data.idx[x]
                self.db.execute("INSERT INTO holdings (manager_id, sid, slot, bought, last_price, since) VALUES (?, ?, ?, ?, ?, ?)",
                                (uid, x, slot, p["price"], p["price"], iso(now)))
                self._maybe_find(uid, p, now)
            self._join_box(uid)
            self.journal_add(uid, "deal", f"Команда «{names.title(name)}» собрана к туру {tn}: "
                             f"15 наклеек за {num(cost)} ❄, в кассе {num(budget - cost)} ❄."
                             + (f" Бюджет опоздавшего — {num(budget)} ❄." if budget != rules.BUDGET else ""), now)
        self.touch(uid, display)
        return self.team(uid)

    def _roles_from(self, sq: dict, body: dict) -> tuple[str, str | None]:
        main = main_ids(sq)
        cap = body.get("captain")
        if cap not in main:
            raise ApiError(400, "Капитана выбирай из основы.")
        ast = body.get("assistant")
        if ast not in main or ast == cap:
            ast = self.auto_assistant(sq, cap)
        return cap, ast

    def auto_assistant(self, sq: dict, cap: str | None) -> str | None:
        """Ассистент ставится сам: самая дорогая по формуле наклейка основы после капитана, без ворот."""
        cands = [x for s, _, x in manager.positions(sq["lineup"]) if x and s != "G" and x != cap]
        if not cands:
            return None
        return min(cands, key=lambda x: (-(self.data.idx.get(x) or {}).get("price", 0), x))

    def _maybe_find(self, uid: int, p: dict, now: datetime) -> None:
        """«Нашёл первым»: новичок лиги до его первых 5 очков — отметка при покупке."""
        if not p.get("new") or p["slot"] == "G":
            return
        pts = sum(sum(t.get("m") or []) for t in (p.get("tours") or {}).values())
        if pts < 5:
            self.db.execute("INSERT OR IGNORE INTO finds (manager_id, sid, at) VALUES (?, ?, ?)", (uid, p["id"], iso(now)))

    def set_lineup(self, uid: int, body: dict) -> dict:
        self.catch_up()
        s, tn = self._need_next()
        self._need_manager(uid)
        hold = self.holdings(uid)
        sq = normalize(body.get("lineup"), body.get("bench"))
        ids = all_ids(sq)
        if len(ids) != len(set(ids)):
            raise ApiError(400, "Одна наклейка стоит в составе дважды.")
        if set(ids) != set(hold):
            raise ApiError(400, "Перестановка — только своими наклейками. Новую наклейку бери через «Обмен».")
        for slot, x in places(sq):
            if x and hold[x]["slot"] != slot:
                p = self.data.idx.get(x)
                raise ApiError(400, f"«{full_name(p)}» — {SLOT_WORD[hold[x]['slot']]}, а место — для {SLOT_FOR[slot]}.")
        sq["captain"], sq["assistant"] = self._roles_from(sq, body)
        with dbm.tx(self.db):
            self.db.execute("UPDATE managers SET squad = ? WHERE id = ?", (json.dumps(sq), uid))
        return self.team(uid)

    def transfer(self, uid: int, body: dict) -> dict:
        """Обмен: отдать наклейку, взять другую того же слота. До первого дедлайна, в «Заливку» и за
        «отдыхающего» — бесплатно; иначе бесплатный обмен, а без них — «8 очков или 600 ❄»."""
        self.catch_up()
        s, tn = self._need_next()
        m = self._need_manager(uid)
        now = self.now()
        out_id, in_id, pay = body.get("out"), body.get("in"), body.get("pay") or "free"
        if pay not in ("free", "points", "ice"):
            raise ApiError(400, "Оплата обмена — бесплатный обмен, 8 очков или 600 льдинок.")
        hold = self.holdings(uid)
        sq = json.loads(m["squad"])
        p_in = self.data.idx.get(in_id) if isinstance(in_id, str) else None
        if p_in is None:
            raise ApiError(400, "Такой наклейки нет в пуле.")
        if in_id in hold:
            raise ApiError(400, "Эта наклейка уже в твоей команде.")
        if out_id is None:
            slot = p_in["slot"]
            if not any(x is None and sl == slot for sl, x in places(sq)):
                raise ApiError(400, "Выбери, какую наклейку отдаёшь.")
            sale, rest, p_out = 0, False, None
        else:
            if out_id not in hold:
                raise ApiError(400, "Этой наклейки нет в твоей команде.")
            slot = hold[out_id]["slot"]
            p_out = self.data.idx.get(out_id)
            sale = self.sale(hold[out_id])
            rest = bool(p_out) and p_out["status"] == "rest"
        if p_in["slot"] != slot:
            raise ApiError(400, f"Обмен — в том же слоте: на место {SLOT_FOR[slot]} нужен {SLOT_WORD[slot]}.")
        win = self.window(uid, tn)
        unlimited = now < parse_iso(m["unlimited_until"])
        zalivka = win["boost"] == "zalivka"
        if unlimited or zalivka or rest:
            kind = "none"
        elif m["free"] > 0:
            kind = "free"
        else:
            paid = win["paid_points"] + win["paid_ice"]
            opts = manager.fee_options(tn, m["bank"], paid)
            if not opts:
                raise ApiError(400, "Платных обменов в этом туре больше нет: не больше двух за тур.")
            if pay == "free":
                raise ApiError(400, "Бесплатных обменов нет. Этот обмен стоит 8 очков"
                               + (" или 600 льдинок." if "ice" in opts else "."), fee_options=opts)
            if pay not in opts:
                raise ApiError(400, "В турах 19–21 лишний обмен — только за 8 очков." if tn > rules.ICE_FEE_LAST_TOUR
                               else "Не хватает льдинок на лишний обмен — он стоит 8 очков.", fee_options=opts)
            kind = pay
        fee_ice = rules.FEE_ICE if kind == "ice" else 0
        new = replace(sq, out_id, in_id, slot)
        check, ph = with_placeholders(new)
        owned = (set(hold) - {out_id}) | ph
        errors = manager.validate_squad(check, self.data.idx, m["bank"] + sale - fee_ice, owned=owned)
        if errors:
            raise ApiError(400, errors[0], errors=errors)
        bank = m["bank"] + sale - p_in["price"] - fee_ice
        free = m["free"] - (1 if kind == "free" else 0)
        win["free_used"] += kind == "free"
        win["paid_points"] += kind == "points"
        win["paid_ice"] += kind == "ice"
        how = {"none": "«Заливка» — обмен бесплатный" if zalivka and not unlimited else
               "«отдыхает» — обмен бесплатный" if rest and not unlimited else "до первого дедлайна — без ограничений",
               "free": "бесплатный обмен", "points": f"стоит {rules.FEE_POINTS} очков в туре {tn}",
               "ice": f"стоит {rules.FEE_ICE} ❄"}[kind]
        head = f"«{full_name(p_out)}» → «{p_in['name']}»" if out_id else f"На пустое место — «{p_in['name']}»"
        text = (f"Обмен: {head}. " + (f"Отдана за {num(sale)} ❄, " if out_id else "")
                + f"взята за {num(p_in['price'])} ❄; {how}.")
        with dbm.tx(self.db):
            if out_id:
                self.db.execute("DELETE FROM holdings WHERE manager_id = ? AND sid = ?", (uid, out_id))
                self.db.execute("DELETE FROM keeps WHERE manager_id = ? AND sid = ?", (uid, out_id))
            self.db.execute("INSERT INTO holdings (manager_id, sid, slot, bought, last_price, since) VALUES (?, ?, ?, ?, ?, ?)",
                            (uid, in_id, slot, p_in["price"], p_in["price"], iso(now)))
            if new.get("captain") is None:
                new["captain"] = new.get("assistant")
            if new.get("assistant") in (None, new.get("captain")):
                new["assistant"] = self.auto_assistant(new, new.get("captain"))
            self.db.execute("UPDATE managers SET bank = ?, free = ?, squad = ? WHERE id = ?", (bank, free, json.dumps(new), uid))
            self._save_window(win)
            self._maybe_find(uid, p_in, now)
            self.journal_add(uid, "deal", text, now)
        return self.team(uid)

    def boost(self, uid: int, body: dict) -> dict:
        """«Заливка»: все обмены тура бесплатны. Одна до Нового года, один буст за тур (ADR-014, раздел 8).
        Уже сделанные в этом окне обмены тоже становятся бесплатными."""
        self.catch_up()
        s, tn = self._need_next()
        m = self._need_manager(uid)
        b = body.get("boost")
        if b != "zalivka":
            raise ApiError(400, "Этот буст появится во втором круге. Сейчас есть «Заливка».")
        if tn >= rules.BOOST_ZALIVKA_BEFORE:
            raise ApiError(400, "«Заливка» — только до Нового года.")
        if self.now() < parse_iso(m["unlimited_until"]):
            raise ApiError(400, "До первого дедлайна обмены и так без ограничений — «Заливка» пригодится позже.")
        if self.boosts_left(uid)["zalivka"] <= 0:
            raise ApiError(400, "«Заливка» уже была.")
        win = self.window(uid, tn)
        if win["boost"]:
            raise ApiError(400, "Один буст за тур — в этом туре он уже есть.")
        refund_free, refund_ice = win["free_used"], win["paid_ice"] * rules.FEE_ICE
        win.update(boost="zalivka", free_used=0, paid_points=0, paid_ice=0)
        with dbm.tx(self.db):
            self._save_window(win)
            self.db.execute("UPDATE managers SET free = free + ?, bank = bank + ? WHERE id = ?", (refund_free, refund_ice, uid))
            self.journal_add(uid, "deal", f"«Заливка» на тур {tn}: все обмены тура бесплатны.")
        return self.team(uid)

    def boosts_left(self, uid: int) -> dict:
        used = self.db.execute("SELECT COUNT(*) FROM windows WHERE manager_id = ? AND boost = 'zalivka'", (uid,)).fetchone()[0]
        s = self.season()
        open_ = s is not None and s.tour_next is not None and s.tour_next < rules.BOOST_ZALIVKA_BEFORE
        return {"zalivka": max(0, rules.BOOSTS_MVP["zalivka"] - used) if open_ else 0}

    def keep(self, uid: int, body: dict) -> dict:
        """«Оставить»: автопилот не меняет «отдыхающего», пока игрок не вернётся или клуб не сыграет без
        него ещё 4 матча. `keep: false` — снять."""
        self.catch_up()
        self._need_next()
        m = self._need_manager(uid)
        sid = body.get("id")
        on = body.get("keep", True) is not False
        if sid not in all_ids(json.loads(m["squad"])):
            raise ApiError(400, "Этой наклейки нет в твоём составе.")
        p = self.data.idx.get(sid)
        with dbm.tx(self.db):
            if not on:
                self.db.execute("DELETE FROM keeps WHERE manager_id = ? AND sid = ?", (uid, sid))
            else:
                if not p or p["status"] != "rest":
                    raise ApiError(400, "«Оставить» — для наклейки, которая отдыхает.")
                self.db.execute("INSERT OR REPLACE INTO keeps (manager_id, sid, since) VALUES (?, ?, ?)",
                                (uid, sid, iso(self.now())))
        return self.team(uid)

    def kept(self, uid: int, sid: str) -> bool:
        r = self.db.execute("SELECT since FROM keeps WHERE manager_id = ? AND sid = ?", (uid, sid)).fetchone()
        return bool(r) and self._keep_alive(sid, r["since"])

    def _keep_alive(self, sid: str, since: str) -> bool:
        p = self.data.idx.get(sid)
        if not p or p["status"] != "rest":
            return False
        day = parse_iso(since).date().isoformat()
        missed = sum(1 for g in self.data.match_list
                     if g["date"] > day and p["club"] in (g.get("home"), g.get("away")) and sid not in g.get("played", []))
        return missed < rules.REST_MISSED

    def settings(self, uid: int, body: dict, display: str = "") -> dict:
        self._need_manager(uid)
        sets, args = [], []
        for k in ("autopilot", "messages", "show_tg_name"):
            if k in body:
                if not isinstance(body[k], bool):
                    raise ApiError(400, "Настройки — только «да» или «нет».")
                sets.append(f"{k} = ?")
                args.append(int(body[k]))
        if body.get("messages") is True:
            sets.append("can_write = 1")
        if body.get("show_tg_name") is False:
            sets.append("tg_name = NULL")
        if "my_player" in body:
            my = body["my_player"]
            if my is not None and not (isinstance(my, str) and len(my) <= 64):
                raise ApiError(400, "«Мой игрок» — id наклейки.")
            sets.append("my_player = ?")
            args.append(my)
        if sets:
            with dbm.tx(self.db):
                self.db.execute(f"UPDATE managers SET {', '.join(sets)} WHERE id = ?", (*args, uid))
        if body.get("show_tg_name") is True:
            self.touch(uid, display)
        return self._manager_json(self.manager(uid))

    def delete(self, uid: int) -> dict:
        dbm.erase(self.db, uid)
        return {}

    def journal(self, uid: int) -> list[dict]:
        self._need_manager(uid)
        rows = self.db.execute("SELECT at, kind, text FROM journal WHERE manager_id = ? ORDER BY id DESC LIMIT ?",
                               (uid, JOURNAL_LIMIT))
        return [dict(r) for r in rows]

    # ---------- вид команды ----------

    def album(self, uid: int) -> set[str]:
        return {r["club"] for r in self.db.execute("SELECT club FROM album WHERE manager_id = ?", (uid,))}

    def team(self, uid: int, tour: int | None = None) -> dict:
        self.catch_up()
        s = self._need_data()
        m = self._need_manager(uid)
        hold = self.holdings(uid)
        tn = s.tour_next
        if tour is None:
            tour = tn
            if tour is None:
                r = self.db.execute("SELECT MAX(tour) FROM lineups WHERE manager_id = ?", (uid,)).fetchone()[0]
                tour = r
        if tour is None:
            raise ApiError(404, "Сезон закончился, а составов у тебя не было.")
        album = self.album(uid)
        idx = self.data.idx
        warnings, mission = [], None
        if tour == tn:
            sq = json.loads(m["squad"])
            locked = False
            sb = manager.tour_score(sq["lineup"], sq["captain"], sq["assistant"], tour, idx, self.data.match_list,
                                    penalty=rules.FEE_POINTS * self.window(uid, tour)["paid_points"])
            points = {"total": sb.total, "provisional": True, "by_id": with_totals(sb.by_id), "subs": [], "penalty": sb.penalty}
            warnings = self.warnings(m, sq, hold, s)
            if tour >= rules.MISSION_FROM_TOUR and tour > m["start_tour"]:
                mission = {"done": manager.mission_done(sq["lineup"], album, idx),
                           "clubs_left": [t["id"] for t in self.data.teams if t["id"] not in album]}
        else:
            row = self.db.execute("SELECT * FROM lineups WHERE manager_id = ? AND tour = ?", (uid, tour)).fetchone()
            if row is None:
                raise ApiError(404, f"На тур {tour} у тебя не было состава.")
            locked = True
            snap = self.db.execute("SELECT detail FROM scores WHERE manager_id = ? AND tour = ?", (uid, tour)).fetchone()
            if snap:
                d = json.loads(snap["detail"])
                sq = {"lineup": d["lineup"], "bench": d["bench"], "captain": d["captain"], "assistant": d["assistant"]}
                points = {"total": d["total"], "provisional": False, "by_id": with_totals(d["by_id"]), "subs": d["subs"],
                          "penalty": d["penalty"]}
            else:
                sq = json.loads(row["squad"])
                sb = manager.tour_score(sq["lineup"], sq["captain"], sq["assistant"], tour, idx, self.data.match_list,
                                        penalty=row["penalty"])
                points = {"total": sb.total, "provisional": True, "by_id": with_totals(sb.by_id), "subs": [], "penalty": sb.penalty}
            mr = self.db.execute("SELECT done FROM missions WHERE manager_id = ? AND tour = ?", (uid, tour)).fetchone()
            if mr:
                mission = {"done": bool(mr["done"]), "clubs_left": []}
        win = self.window(uid, tn) if tn else None
        paid = (win["paid_points"] + win["paid_ice"]) if win else 0
        unlimited = self.now() < parse_iso(m["unlimited_until"]) or bool(win and win["boost"] == "zalivka")
        seen = set(all_ids(sq)) | set(points["by_id"])
        try:
            deadline = iso(tours.deadline_of(self.data.tours, tour))
        except KeyError:
            deadline = None
        return {
            "tour": tour, "deadline": deadline, "locked": locked,
            "lineup": sq["lineup"], "bench": sq["bench"], "captain": sq.get("captain"), "assistant": sq.get("assistant"),
            "bank": m["bank"], "value": sum(self.sale(h) for h in hold.values()), "free": m["free"],
            "unlimited": unlimited, "paid_this_tour": paid,
            "fee_options": manager.fee_options(tn, m["bank"], paid) if tn and not unlimited else [],
            "sale": {x: self.sale(h) for x, h in hold.items()}, "bought": {x: h["bought"] for x, h in hold.items()},
            "points": points,
            "album": [t["id"] for t in self.data.teams if t["id"] in album],
            "mission": mission, "boosts": self.boosts_left(uid), "boost": win["boost"] if win else None,
            "warnings": warnings, "hidden": sorted(x for x in seen if x not in idx),
            "start_tour": m["start_tour"],
        }

    def warnings(self, m, sq: dict, hold: dict, s: Season) -> list[dict]:
        """Предупреждения на tour_next: кого заменит автопилот, кто отдыхает, пустые места."""
        out = []
        dl = when(s.deadline) if s.deadline else ""
        who = self.mascot(m["fav_club"])
        if m["autopilot"]:
            bank, cur = m["bank"], copy.deepcopy(sq)
            for slot, x in places(sq):
                if x is None:
                    out.append({"id": None, "text": f"Пустое место в запасе или основе: наклейку скрыли по просьбе. "
                                                    f"{who} поставит замену в {dl}, если не выберешь сам."})
                    continue
                p = self.data.idx.get(x)
                if not p or p["status"] != "rest" or x == m["my_player"] or self.kept(m["id"], x):
                    continue
                pick = self._autopilot_pick(x, cur, hold, bank)
                if pick:
                    out.append({"id": x, "text": f"{rested(p)}. {who} заменит его в {dl}", "in": pick})
                else:
                    out.append({"id": x, "text": f"{rested(p)}. Замены по карману нет — загляни в «Обмен»."})
        else:
            for slot, x in places(sq):
                if x is None:
                    out.append({"id": None, "text": "Пустое место: наклейку скрыли по просьбе. Возьми замену в «Обмене»."})
                    continue
                p = self.data.idx.get(x)
                if p and p["status"] == "rest" and x != m["my_player"] and not self.kept(m["id"], x):
                    out.append({"id": x, "text": f"{rested(p)}. Замени сам или включи автопилот."})
        return out

    def _autopilot_pick(self, out_id: str | None, sq: dict, hold: dict, bank: int, slot: str | None = None) -> str | None:
        """Замена от движка. Пустое место — заглушка с ценой покупки 0: «Отдашь за» у неё ноль."""
        check, ph = with_placeholders(sq)
        bought = {x: h["bought"] for x, h in hold.items()}
        if out_id is None:
            out_id = next(x for (sl, x0), (_, x) in zip(places(sq), places(check)) if x0 is None and sl == slot)
            bought[out_id] = 0
        return manager.autopilot_pick(out_id, {**check, "bought": bought}, self.data.idx, bank)

    # ---------- события сезона ----------

    def catch_up(self) -> None:
        """Дедлайны и закрытия, которые уже наступили. Зовётся из каждого запроса и из задач: дедлайн
        не сдвигается, даже если задача опоздала — состав замораживается раньше любой правки."""
        if not self.data.loaded:
            return
        now = self.now()
        state = {r["tour"]: dict(r) for r in self.db.execute("SELECT * FROM tour_state")}
        for r in self._tour_rows():
            t = r["t"]
            dl = tours.deadline_of(self.data.tours, t)
            if dl <= now and not (state.get(t) or {}).get("deadline_done"):
                self.process_deadline(t, dl)
                state[t] = {"deadline_done": iso(now)}
        for r in self._tour_rows():
            t = r["t"]
            if r.get("closed") and (state.get(t) or {}).get("deadline_done") and not (state.get(t) or {}).get("closed_done"):
                self.process_close(t)

    def process_deadline(self, t: int, dl: datetime) -> None:
        """Дедлайн тура t: автопилот, заморозка состава, задание недели, альбом, +1 бесплатный обмен."""
        now = self.now()
        with dbm.tx(self.db):
            for m in self.db.execute("SELECT * FROM managers WHERE start_tour <= ? ORDER BY id", (t,)).fetchall():
                self._freeze(m, t, dl)
            self._downtime_bonus(t, dl)
            self.db.execute("""INSERT INTO tour_state (tour, deadline_done) VALUES (?, ?)
                               ON CONFLICT(tour) DO UPDATE SET deadline_done = excluded.deadline_done""", (t, iso(now)))
        log.info("deadline of tour %s processed", t)

    def _freeze(self, m, t: int, dl: datetime) -> None:
        uid = m["id"]
        if self.db.execute("SELECT 1 FROM lineups WHERE manager_id = ? AND tour = ?", (uid, t)).fetchone():
            return
        sq = json.loads(m["squad"])
        bank = m["bank"]
        if m["autopilot"]:
            sq, bank = self._autopilot(m, sq, bank, t, dl)
        win = self.window(uid, t)
        penalty = rules.FEE_POINTS * win["paid_points"]
        self.db.execute("INSERT INTO lineups (manager_id, tour, squad, penalty, boost, frozen_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (uid, t, json.dumps(sq), penalty, win["boost"], iso(dl)))
        album = self.album(uid)
        bonus = 0
        if t >= rules.MISSION_FROM_TOUR and t > m["start_tour"]:
            done = manager.mission_done(sq["lineup"], album, self.data.idx)
            self.db.execute("INSERT OR REPLACE INTO missions (manager_id, tour, done) VALUES (?, ?, ?)", (uid, t, int(done)))
            if done:
                bonus = 1
                new = sorted(manager.album_clubs(sq["lineup"], self.data.idx) - album)
                self.journal_add(uid, "mission", f"Задание недели выполнено: в основе тура {t} новый клуб — "
                                 f"«{self.club_name(new[0])}». +1 обмен.", dl)
        for c in manager.album_clubs(sq["lineup"], self.data.idx) - album:
            self.db.execute("INSERT OR IGNORE INTO album (manager_id, club, tour) VALUES (?, ?, ?)", (uid, c, t))
        free = min(rules.FREE_MAX, m["free"] + rules.FREE_PER_TOUR + bonus)
        self.db.execute("UPDATE managers SET squad = ?, bank = ?, free = ? WHERE id = ?", (json.dumps(sq), bank, free, uid))
        self.db.execute("DELETE FROM keeps WHERE manager_id = ? AND sid NOT IN (SELECT sid FROM holdings WHERE manager_id = ?)",
                        (uid, uid))

    def _autopilot(self, m, sq: dict, bank: int, t: int, dl: datetime) -> tuple[dict, int]:
        """Автопилот в дедлайн: «отдыхающего» (кроме «Моего игрока» и «Оставить») и пустое место —
        на лучшую по формуле наклейку того же слота, бесплатно, в пределах кассы и лимита клуба."""
        uid = m["id"]
        who = self.mascot(m["fav_club"])
        for slot, x in places(sq):
            hold = self.holdings(uid)
            if x is None:
                pick = self._autopilot_pick(None, sq, hold, bank, slot)
                if not pick:
                    continue
                p_in = self.data.idx[pick]
                sq = replace(sq, None, pick, slot)
                bank -= p_in["price"]
                text = f"{who} поставил на пустое место {surname(p_in)}: наклейка взята за {num(p_in['price'])} ❄ — бесплатно."
            else:
                p = self.data.idx.get(x)
                if not p or p["status"] != "rest" or x == m["my_player"] or self.kept(uid, x):
                    continue
                pick = self._autopilot_pick(x, sq, hold, bank)
                if not pick:
                    self.journal_add(uid, "autopilot", f"{rested(p)}. {who} не нашёл замену по карману.", dl)
                    continue
                p_in = self.data.idx[pick]
                sale = manager.sale_price(hold[x]["bought"], p["price"], "rest")
                sq = replace(sq, x, pick)
                bank += sale - p_in["price"]
                self.db.execute("DELETE FROM holdings WHERE manager_id = ? AND sid = ?", (uid, x))
                self.db.execute("DELETE FROM keeps WHERE manager_id = ? AND sid = ?", (uid, x))
                text = (f"{rested(p)}. {who} заменил его, теперь на этом месте {surname(p_in)}. "
                        f"Наклейка отдана за {num(sale)} ❄, новая взята за {num(p_in['price'])} ❄ — бесплатно.")
            self.db.execute("INSERT INTO holdings (manager_id, sid, slot, bought, last_price, since) VALUES (?, ?, ?, ?, ?, ?)",
                            (uid, pick, slot, p_in["price"], p_in["price"], iso(dl)))
            self.journal_add(uid, "autopilot", text, dl)
        if sq.get("captain") is None:
            sq["captain"] = sq.get("assistant")
        if sq.get("assistant") in (None, sq.get("captain")):
            sq["assistant"] = self.auto_assistant(sq, sq.get("captain"))
        return sq, bank

    def _downtime_bonus(self, t: int, dl: datetime) -> None:
        """Сервер лежал больше 6 часов за сутки до дедлайна — всем +1 бесплатный обмен (раздел 12)."""
        a = dl - timedelta(hours=24)
        down = timedelta()
        for r in self.db.execute("SELECT start, end FROM downtime"):
            s, e = max(parse_iso(r["start"]), a), min(parse_iso(r["end"]), dl)
            if e > s:
                down += e - s
        if down <= timedelta(hours=6):
            return
        for m in self.db.execute("SELECT id FROM managers WHERE created_at < ?", (iso(dl),)).fetchall():
            self.db.execute("UPDATE managers SET free = free + 1 WHERE id = ?", (m["id"],))
            self.journal_add(m["id"], "deal", f"«Звено» было недоступно перед дедлайном тура {t} — +1 бесплатный обмен.", dl)

    def process_close(self, t: int) -> None:
        """Тур закрыт: автозамены, очки, снимок. Потом ступени, если кончился месяц."""
        now = self.now()
        idx, ml = self.data.idx, self.data.match_list
        with dbm.tx(self.db):
            for r in self.db.execute("SELECT * FROM lineups WHERE tour = ?", (t,)).fetchall():
                uid = r["manager_id"]
                if self.db.execute("SELECT 1 FROM scores WHERE manager_id = ? AND tour = ?", (uid, t)).fetchone():
                    continue
                sq = json.loads(r["squad"])
                sb = manager.tour_score(sq["lineup"], sq["captain"], sq["assistant"], t, idx, ml,
                                        bench=sq["bench"], penalty=r["penalty"])
                after, _ = manager.apply_autosubs(sq["lineup"], sq["bench"], t, idx)
                detail = {"total": sb.total, "by_id": sb.by_id, "subs": [list(x) for x in sb.subs], "penalty": sb.penalty,
                          "lineup": after, "bench": bench_after(sq["bench"], sb.subs),
                          "captain": sq["captain"], "assistant": sq["assistant"]}
                self.db.execute("INSERT INTO scores (manager_id, tour, total, detail) VALUES (?, ?, ?, ?)",
                                (uid, t, sb.total, json.dumps(detail, ensure_ascii=False)))
                for a, b in sb.subs:
                    head = f"«{full_name(idx.get(b))}» вместо «{full_name(idx.get(a))}»" if a else f"«{full_name(idx.get(b))}» на пустое место"
                    self.journal_add(uid, "autosub", f"Автозамена в туре {t}: {head} — у ушедшего в туре не было матчей.", now)
            self.db.execute("""INSERT INTO tour_state (tour, closed_done) VALUES (?, ?)
                               ON CONFLICT(tour) DO UPDATE SET closed_done = excluded.closed_done""", (t, iso(now)))
            self.maybe_regroup()
        log.info("tour %s closed", t)

    # ---------- ступени ----------

    def months(self) -> list[str]:
        out = []
        for r in self._tour_rows():
            if r["month"] not in out:
                out.append(r["month"])
        return out

    def maybe_regroup(self) -> None:
        """Все туры месяца закрыты — пересбор ступеней на следующий месяц; первый — с тура 4 по турам 1–3."""
        closed = {r["tour"] for r in self.db.execute("SELECT tour FROM tour_state WHERE closed_done IS NOT NULL")}
        rows = self._tour_rows()
        months = self.months()
        for prev, nxt in zip(months, months[1:]):
            first_next = min(r["t"] for r in rows if r["month"] == nxt)
            if first_next < rules.STEPS_FROM_TOUR or dbm.get_meta(self.db, f"regroup:{nxt}"):
                continue
            ts = [r["t"] for r in rows if r["month"] == prev]
            if not all(x in closed for x in ts):
                continue
            self.regroup(prev, nxt, ts)

    def regroup(self, prev: str, nxt: str, ts: list[int]) -> None:
        pts = {m["id"]: 0 for m in self.db.execute("SELECT id FROM managers")}
        q = f"SELECT manager_id, SUM(total) AS p FROM scores WHERE tour IN ({','.join('?' * len(ts))}) GROUP BY manager_id"
        for r in self.db.execute(q, ts):
            if r["manager_id"] in pts:
                pts[r["manager_id"]] = r["p"]
        previous = {r["manager_id"]: (r["step"], r["grp"])
                    for r in self.db.execute("SELECT * FROM steps WHERE month = ?", (prev,)) if r["manager_id"] in pts}
        last = max(ts)
        first_active = max(1, last - rules.INACTIVE_TOURS + 1)
        row = self._tour_row(first_active)
        since = iso(datetime.combine(datetime.fromisoformat(row["from"]).date(), time(0), TZ)) if row else None
        inactive = {m["id"] for m in self.db.execute("SELECT id FROM managers WHERE last_seen < ?", (since,))} if since else set()
        res = manager.regroup_steps(pts, previous, inactive & set(previous))
        for uid, (step, grp) in res.items():
            self.db.execute("INSERT OR REPLACE INTO steps (month, manager_id, step, grp, prev_step) VALUES (?, ?, ?, ?, ?)",
                            (nxt, uid, step, grp, previous[uid][0] if uid in previous else None))
        dbm.set_meta(self.db, f"regroup:{nxt}", iso(self.now()))
        log.info("steps for %s: %d managers", nxt, len(res))

    def step_month(self) -> str | None:
        r = self.db.execute("SELECT MAX(month) FROM steps").fetchone()
        return r[0] if r else None

    def _join_box(self, uid: int) -> None:
        """Новичок после пересбора — сразу в Коробку, в самую маленькую группу (ADR-014, раздел 14)."""
        month = self.step_month()
        if not month:
            return
        groups = self.db.execute("SELECT grp, COUNT(*) AS n FROM steps WHERE month = ? AND step = ? GROUP BY grp ORDER BY n, grp",
                                 (month, rules.STEP_BOX)).fetchall()
        grp = groups[0]["grp"] if groups else 1
        self.db.execute("INSERT OR IGNORE INTO steps (month, manager_id, step, grp, prev_step) VALUES (?, ?, ?, ?, NULL)",
                        (month, uid, rules.STEP_BOX, grp))

    # ---------- скрытые игроки и обновление данных ----------

    def on_data(self) -> None:
        """Новые данные: скрытые игроки уходят из составов, помним последнюю стоимость, дедлайны и закрытия."""
        self.apply_hidden()
        self.catch_up()

    def apply_hidden(self) -> int:
        """Наклейки, которых нет в пуле (скрыты по просьбе, ADR-007): возврат большей из цены покупки и
        последней стоимости, +1 бесплатный обмен, место в составе пустеет. Прошлые очки остаются."""
        idx = self.data.idx
        held = {r["sid"] for r in self.db.execute("SELECT DISTINCT sid FROM holdings")}
        if not held:
            return 0
        present = held & set(idx)
        if len(present) < HIDDEN_SANITY * len(held):
            log.warning("pool lost %d of %d held stickers — not treating as hidden", len(held - present), len(held))
            return 0
        now = self.now()
        n = 0
        with dbm.tx(self.db):
            for sid in present:
                self.db.execute("UPDATE holdings SET last_price = ? WHERE sid = ?", (idx[sid]["price"], sid))
            for sid in held - present:
                for h in self.db.execute("SELECT * FROM holdings WHERE sid = ?", (sid,)).fetchall():
                    uid = h["manager_id"]
                    m = self.manager(uid)
                    refund = manager.sale_price(h["bought"], h["last_price"], "ok", hidden=True)
                    sq = replace(json.loads(m["squad"]), sid, None) if sid in all_ids(json.loads(m["squad"])) else json.loads(m["squad"])
                    if sq.get("captain") is None:
                        sq["captain"] = sq.get("assistant")
                    if sq.get("assistant") in (None, sq.get("captain")):
                        sq["assistant"] = self.auto_assistant(sq, sq.get("captain"))
                    self.db.execute("DELETE FROM holdings WHERE manager_id = ? AND sid = ?", (uid, sid))
                    self.db.execute("DELETE FROM keeps WHERE manager_id = ? AND sid = ?", (uid, sid))
                    self.db.execute("UPDATE managers SET bank = bank + ?, free = free + 1, squad = ? WHERE id = ?",
                                    (refund, json.dumps(sq), uid))
                    self.journal_add(uid, "deal", f"Наклейку скрыли по просьбе игрока: вернули {num(refund)} ❄ "
                                     "и дали бесплатный обмен. Место в составе свободно.", now)
                    n += 1
        return n

    # ---------- пульс: недоступность сервера ----------

    def beat(self) -> None:
        dbm.set_meta(self.db, "beat", iso(self.now()))

    def note_start(self, gap: timedelta = timedelta(minutes=3)) -> None:
        """При запуске: с последнего пульса прошло больше gap — записать недоступность."""
        last = dbm.get_meta(self.db, "beat")
        now = self.now()
        if last and now - parse_iso(last) > gap:
            with dbm.tx(self.db):
                self.db.execute("INSERT INTO downtime (start, end) VALUES (?, ?)", (last, iso(now)))
        self.beat()

    # ---------- лиги ----------

    def new_code(self) -> str:
        while True:
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
            if not self.db.execute("SELECT 1 FROM leagues WHERE code = ?", (code,)).fetchone():
                return code

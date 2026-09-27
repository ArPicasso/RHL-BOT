"""Сборка опубликованных данных «Звена» из протоколов: tours.json, pool.json, matches.json.

Движок без состояния: каждый запуск считает всё заново по results.json и календарю. Очки
закрытого тура замораживает сервер своим снимком (docs/zveno/contract.md, раздел 2).
Файлы пишет build_zveno.py, здесь — только расчёт.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from . import names, points, prices, prior as priors, rules, tours
from .rules import TZ


@dataclass
class Entity:
    """Наклейка: полевой (p:<id лиги>) или ворота клуба (g:<клуб>)."""
    id: str
    pid: int | None
    slot: str
    club: str
    name: str
    number: int | None
    matches: list[tuple[str, str, int, int, int | None]] = field(default_factory=list)  # дата, id матча, очки, окно, тур
    roles: dict[str, int] = field(default_factory=dict)   # амплуа в протоколах сезона → сколько раз

    def main_slot(self) -> str:
        """Амплуа, в котором игрок чаще выходил; поровну — то, что было раньше."""
        order = list(self.roles)
        return max(order, key=lambda r: (self.roles[r], -order.index(r))) if order else self.slot


def regular_protocols(results: dict, find_club) -> tuple[list[tuple[dict, str, str]], list[str]]:
    """Протоколы регулярки 2026/27 по датам сезона, с id клубов. Второй список — непривязанные."""
    out, unmatched, seen = [], [], set()
    for tournament in results.values():
        for p in tournament.values():
            d = date.fromisoformat(p["date"])
            if not rules.REGULAR_FROM <= d <= rules.REGULAR_TO or p.get("game_id") in seen:
                continue
            home, away = find_club(p["home"]), find_club(p["away"])
            if not home or not away:
                unmatched.append(f"{p['date']} {p['home']} — {p['away']}")
                continue
            seen.add(p.get("game_id"))
            out.append((p, home, away))
    out.sort(key=lambda x: (x[0]["date"], x[0].get("game_id") or 0))
    return out, unmatched


def window_now(deadlines: dict[int, datetime], now: datetime) -> int:
    """Окно стоимости сейчас: номер последнего прошедшего дедлайна, 0 — до первого."""
    return max([t for t, dl in deadlines.items() if dl <= now], default=0)


@dataclass
class Season:
    tours: dict
    pool: dict
    matches: dict
    review: list[str]


def build(protos: list[tuple[dict, str, str]], calendar: list[dict], teams: list[dict], now: datetime,
          prior: dict, links: dict | None = None, hidden: set[int] = frozenset()) -> Season:
    """protos — из regular_protocols; calendar — матчи league.json; teams — teams.json."""
    now = now.astimezone(TZ)
    clubs = [t["id"] for t in teams]
    club_name = {t["id"]: t["name"] for t in teams}
    days = sorted({date.fromisoformat(g["date"]) for g in calendar})
    deadlines = {t: tours.deadline(t, days) for t in range(1, rules.TOUR_COUNT + 1)}
    today = now.date()
    settled_before = today - timedelta(days=rules.SETTLE_DAYS)

    def shown(pid: int | None) -> str | None:
        """Id наклейки для matches.json; скрытого игрока и игрока без id там нет."""
        return points.pid_key(pid) if pid is not None and pid not in hidden else None

    ents: dict[str, Entity] = {}
    club_games: dict[str, list[str]] = defaultdict(list)
    first_protocol: dict[str, str] = {}
    per_tour: dict[int, int] = defaultdict(int)
    matches, no_id = [], 0
    for p, home, away in protos:
        if date.fromisoformat(p["date"]) > today:   # сборка «на момент» (--now): будущих протоколов нет
            continue
        mp = points.match_points(p, home, away)
        if mp.technical:
            continue
        gid, d = str(p["game_id"]), p["date"]
        w, t = tours.price_window(d, deadlines), tours.tour_of_date(d)
        if t:
            per_tour[t] += 1
        for c in (home, away):
            club_games[c].append(gid)
            first_protocol.setdefault(c, d)
        for club, pts in mp.gates.items():
            e = ents.setdefault(points.gate_key(club), Entity(points.gate_key(club), None, "G", club,
                                                             names.gate_name(club, club_name.get(club, club)), None))
            e.matches.append((d, gid, pts, w, t))
        for pid, info in mp.skaters.items():
            key = points.pid_key(pid)
            e = ents.setdefault(key, Entity(key, pid, info["slot"], info["club"], info["name"], info["number"]))
            # клуб, номер и имя — по последнему протоколу (переход внутри РХЛ, раздел 12), амплуа — по большинству
            e.club, e.name, e.number = info["club"], info["name"], info["number"]
            e.roles[info["slot"]] = e.roles.get(info["slot"], 0) + 1
            e.slot = e.main_slot()
            e.matches.append((d, gid, info["pts"], w, t))
        no_id += sum(1 for r in p.get("lineups", []) if r["role"] != "G" and r.get("played", True)
                     and r["player"].get("id") is None)
        matches.append({
            "id": gid, "date": d, "tour": t, "home": home, "away": away,
            "settled": date.fromisoformat(d) < settled_before,
            "played": [k for k in mp.played() if not (k.startswith("p:") and int(k[2:]) in hidden)],
            "goals": [{"team": g["team"], "author": shown(g["author"]),
                       "assists": [x for x in map(shown, g["assists"]) if x]} for g in mp.goals],
        })

    # связка с прошлым сезоном
    skaters = {e.pid: {"name": e.name, "slot": e.slot} for e in ents.values() if e.pid is not None}
    linked, review = priors.link_skaters(skaters, prior, links)
    if no_id:
        review.append(f"Строк полевых без id на сайте лиги: {no_id} — в «Звено» не попали")

    wnow = window_now(deadlines, now)
    players = []
    for e in ents.values():
        if e.pid is not None and e.pid in hidden:
            continue
        if e.slot == "G":
            g = prior.get("gates", {}).get(e.club)
            pr = (g["gp"], g["ppg"]) if g else None
        else:
            pr = linked.get(e.pid)
        path = prices.price_path(e.slot, pr, [(w, pts) for _, _, pts, w, _ in e.matches], wnow)
        played = {gid for _, gid, _, _, _ in e.matches}
        status = "ok" if e.slot == "G" else prices.rest_status(club_games[e.club], played)
        by_tour: dict[int, list] = defaultdict(list)
        for _, gid, pts, _, t in e.matches:
            if t:
                by_tour[t].append((gid, pts))
        players.append({
            "id": e.id, "pid": e.pid, "slot": e.slot, "name": e.name, "club": e.club, "number": e.number,
            "price": path.price, "promise": round(prices.promise(e.slot, path.price), 1),
            "form": status == "ok" and prices.in_form(e.slot, path.price, [m[2] for m in e.matches]),
            "status": status, "new": pr is None, "price_monday": path.price_monday,
            "price_prev": path.price_prev,
            "tours": {str(t): {"m": [x for _, x in ms], "best2": points.best_sum([x for _, x in ms]),
                               "ids": [gid for gid, _ in ms]} for t, ms in sorted(by_tour.items())},
        })
    players.sort(key=lambda x: (-x["price"], x["id"]))

    # статус сезона
    rows = tours.tour_table(calendar, clubs, now, per_tour)
    with_protocol = [c for c in clubs if c in first_protocol]
    is_open = bool(clubs) and len(with_protocol) == len(clubs)
    opened_at = first_tour = tour_now = None
    if is_open:
        opened = datetime.combine(date.fromisoformat(max(first_protocol[c] for c in clubs)), time(23, 59), TZ)
        opened_at = tours.iso(opened)
        first_tour = next((t for t in sorted(deadlines) if deadlines[t] > opened), None)
        for r in rows:
            end = datetime.combine(date.fromisoformat(r["to"]), time(23, 59, 59), TZ)
            if first_tour and r["t"] >= first_tour and deadlines[r["t"]] <= now <= end:
                tour_now = r["t"]
    tour_next = tours.next_tour({"tours": rows}, now)
    if first_tour and tour_next:
        tour_next = max(tour_next, first_tour)
    updated = tours.iso(now)
    return Season(
        tours={"season": rules.SEASON, "updated": updated, "status": "open" if is_open else "prolog",
               "market_opened_at": opened_at, "first_tour": first_tour, "tour_now": tour_now,
               "tour_next": tour_next, "tours": rows, "clubs_with_protocol": len(with_protocol)},
        pool={"updated": updated, "tour_next": tour_next, "players": players},
        matches={"updated": updated, "matches": matches},
        review=review,
    )

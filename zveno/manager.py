"""Правила менеджера «Звена» — функции, которые сервер вызывает на своих данных (контракт, раздел 3).

Форматы:
- lineup — {"G": "g:…", "L1": {"F": [id, id, id], "D": [id, id]}, "L2": {…}}
- bench — [g, d, f, f], по порядку автозамен
- pool — pool.json целиком, его список players или словарь id → наклейка
- matches — matches.json целиком или его список matches
"""
import copy
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import rules
from .points import best_sum

TEAMS_FILE = Path(__file__).resolve().parent.parent / "teams.json"
SLOT_WORD = {"G": "ворота клуба", "D": "защитник", "F": "нападающий"}
SLOT_FOR = {"G": "ворот клуба", "D": "защитника", "F": "нападающего"}
_club_names: dict[str, str] | None = None


def _club(club: str) -> str:
    """Название клуба для сообщений; teams.json лежит рядом с пакетом, без него — id."""
    global _club_names
    if _club_names is None:
        try:
            _club_names = {t["id"]: t["name"] for t in json.loads(TEAMS_FILE.read_text(encoding="utf-8"))}
        except (FileNotFoundError, ValueError, KeyError):
            _club_names = {}
    return _club_names.get(club, club)


def _num(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def index(pool) -> dict[str, dict]:
    """Наклейки по id из любого вида пула."""
    if isinstance(pool, dict) and "players" in pool:
        pool = pool["players"]
    if isinstance(pool, dict):
        return pool
    return {p["id"]: p for p in pool}


def club_of(sid: str, idx: dict[str, dict]) -> str | None:
    if sid.startswith("g:"):
        return sid[2:]
    p = idx.get(sid)
    return p["club"] if p else None


def positions(lineup: dict) -> list[tuple[str, str | None, str]]:
    """(слот, звено, id) основы по порядку: ворота, звено 1 — защитники, нападающие, звено 2."""
    out = [("G", None, lineup.get("G"))]
    for line in rules.LINES:
        ln = lineup.get(line) or {}
        out += [("D", line, x) for x in ln.get("D") or []]
        out += [("F", line, x) for x in ln.get("F") or []]
    return out


def lineup_ids(lineup: dict) -> list[str]:
    return [x for _, _, x in positions(lineup) if x]

# ---------- состав ----------


def validate_squad(squad, pool, bank: int, *, owned=None) -> list[str]:
    """Ошибки состава по-русски; [] — годится.

    squad — {"lineup": …, "bench": […]}. bank — льдинки, доступные на наклейки, которых ещё нет.
    owned — наклейки, которые уже в команде (id или {id: цена покупки}): за них не платят, и лимит клуба
    из-за перехода игрока их не касается (раздел 12). Без owned — сборка с нуля."""
    idx = index(pool)
    owned = set(owned or ())
    lineup, bench = squad.get("lineup") or {}, list(squad.get("bench") or [])
    errors = []
    if not lineup.get("G"):
        errors.append("В основе нужны ворота клуба")
    for n, line in enumerate(rules.LINES, start=1):
        ln = lineup.get(line) or {}
        if len(ln.get("F") or []) != rules.LINE["F"] or len(ln.get("D") or []) != rules.LINE["D"]:
            errors.append(f"В звене {n} нужно {rules.LINE['F']} нападающих и {rules.LINE['D']} защитника")
    if len(bench) != len(rules.BENCH):
        errors.append("В запасе нужно четверо: ворота клуба, защитник и два нападающих — по порядку")
    places = [(s, x) for s, _, x in positions(lineup) if x] + list(zip(rules.BENCH, bench))
    seen = set()
    for slot, sid in places:
        if sid in seen:
            errors.append(f"«{idx[sid]['name'] if sid in idx else sid}» стоит в составе дважды")
            continue
        seen.add(sid)
        p = idx.get(sid)
        if p is None:
            if sid not in owned:
                errors.append(f"Наклейки {sid} нет в пуле")
            continue
        if p["slot"] != slot and sid not in owned:
            errors.append(f"«{p['name']}» — {SLOT_WORD[p['slot']]}, а место — для {SLOT_FOR[slot]}")
    by_club: dict[str, list[str]] = {}
    for sid in seen:
        c = club_of(sid, idx)
        if c:
            by_club.setdefault(c, []).append(sid)
    for c, ids in sorted(by_club.items()):
        if len(ids) > rules.CLUB_LIMIT and any(x not in owned for x in ids):
            errors.append(f"Из клуба «{_club(c)}» можно не больше трёх наклеек, ворота тоже считаются")
    cost = sum(idx[x]["price"] for x in seen if x not in owned and x in idx)
    if cost > bank:
        errors.append(f"Не хватает льдинок: наклейки стоят {_num(cost)}, есть {_num(bank)}")
    return errors


def sale_price(bought: int, current: int, status: str, hidden: bool = False) -> int:
    """«Отдашь за»: цена покупки плюс половина прироста, шаг 100, не больше +500; подешевела — по текущей.
    «Отдыхает» — большее из цены покупки и обычной цены продажи. Скрыт — большее из покупки и текущей."""
    if hidden:
        return max(bought, current)
    if current > bought:
        gain = int((current - bought) * rules.SELL_SHARE) // rules.PRICE_ROUND * rules.PRICE_ROUND
        normal = bought + min(rules.SELL_CAP, gain)
    else:
        normal = current
    return max(bought, normal) if status == "rest" else normal


def fee_options(tour: int, bank: int, paid_this_tour: int) -> list[str]:
    """Чем можно оплатить лишний обмен: «8 очков» или «600 ❄». Не больше двух платных за тур, в турах
    19–21 — только очки, льдинки — если хватает банка."""
    if paid_this_tour >= rules.PAID_MAX_PER_TOUR:
        return []
    opts = ["points"]
    if tour <= rules.ICE_FEE_LAST_TOUR and bank >= rules.FEE_ICE:
        opts.append("ice")
    return opts


def mission_done(lineup: dict, album: set[str], pool=None) -> bool:
    """Задание недели «Новый клуб в альбоме»: в основе на дедлайн есть клуб, которого в альбоме не было.
    Ворота — тоже клуб. Клуб полевого берётся из pool."""
    idx = index(pool) if pool is not None else {}
    ids = lineup_ids(lineup)
    if pool is None and any(x.startswith("p:") for x in ids):
        raise TypeError("mission_done: для полевых нужен pool — клуб наклейки берётся из него")
    return any((c := club_of(x, idx)) and c not in album for x in ids)


def album_clubs(lineup: dict, pool) -> set[str]:
    """Клубы основы — чем пополняется альбом на дедлайн."""
    idx = index(pool)
    return {c for x in lineup_ids(lineup) if (c := club_of(x, idx))}


def autopilot_pick(out_id: str, squad: dict, pool, bank: int) -> str | None:
    """Замена «отдыхающему» от автопилота: самая дорогая по формуле наклейка того же слота, которая
    играет, влезает в банк вместе с «Отдашь за» и не ломает лимит клуба. Бесплатно. None — замены нет.

    squad — {"lineup", "bench", "bought": {id: цена покупки}}."""
    idx = index(pool)
    lineup, bench = squad.get("lineup") or {}, list(squad.get("bench") or [])
    places = [(s, x) for s, _, x in positions(lineup) if x] + list(zip(rules.BENCH, bench))
    slot = next((s for s, x in places if x == out_id), None)
    if slot is None:
        return None
    team = [x for _, x in places]
    out = idx.get(out_id)
    bought = (squad.get("bought") or {}).get(out_id)
    current = out["price"] if out else (bought or 0)
    budget = bank + sale_price(bought if bought is not None else current, current,
                               out["status"] if out else "ok", hidden=out is None)
    clubs: dict[str, int] = {}
    for x in team:
        if x != out_id and (c := club_of(x, idx)):
            clubs[c] = clubs.get(c, 0) + 1
    cands = [p for p in idx.values()
             if p["slot"] == slot and p["status"] == "ok" and p["id"] not in team and p["price"] <= budget
             and clubs.get(p["club"], 0) < rules.CLUB_LIMIT]
    if not cands:
        return None
    return min(cands, key=lambda p: (-p["price"], -p.get("promise", 0), not p.get("form"), p["id"]))["id"]

# ---------- очки тура ----------


@dataclass
class ScoreBreakdown:
    """Очки тура. by_id[id]: matches — очки матчей без сыгранности, best2 — два лучших без неё,
    synergy — прибавка от сыгранности к двум лучшим, mult — 2 у капитана (или ассистента), points —
    (best2 + synergy) · mult."""
    total: int
    by_id: dict[str, dict] = field(default_factory=dict)
    subs: list[tuple[str, str]] = field(default_factory=list)
    penalty: int = 0


def _matches_list(matches) -> list[dict]:
    if isinstance(matches, dict):
        return matches.get("matches", [])
    return list(matches or [])


def played_in(sid: str | None, tour: int, idx: dict[str, dict]) -> bool:
    """Есть ли у наклейки сыгранные матчи в туре. Скрытого в пуле нет — матчей нет."""
    return bool(sid) and bool((idx.get(sid, {}).get("tours", {}).get(str(tour)) or {}).get("m"))


def tour_matches(sid: str, tour: int, idx: dict[str, dict], matches: list[dict] | None = None) -> list[tuple[str, int]]:
    """(id матча, очки) наклейки в туре по порядку дат. Без поля ids в пуле — по matches.json."""
    p = idx.get(sid)
    t = (p or {}).get("tours", {}).get(str(tour))
    if not t:
        return []
    ids = t.get("ids")
    if ids is None:
        ids = [m["id"] for m in sorted(_matches_list(matches), key=lambda m: (m["date"], m["id"]))
               if m.get("tour") == tour and sid in m.get("played", [])]
    return list(zip(ids, t["m"]))


def apply_autosubs(lineup: dict, bench: list[str], tour: int, pool) -> tuple[dict, list[tuple[str, str]]]:
    """Автозамены при закрытии тура: у игрока основы ноль матчей — выходит первый запасной того же
    слота с матчами. Запасной встаёт на место ушедшего, в его звено."""
    idx = index(pool)
    new = copy.deepcopy(lineup)
    used, subs = set(), []

    def played(x):
        return played_in(x, tour, idx)

    def sub_for(slot):
        return next((b for s, b in zip(rules.BENCH, bench) if s == slot and b not in used and played(b)), None)

    if not played(new.get("G")) and (b := sub_for("G")):
        subs.append((new.get("G"), b))
        used.add(b)
        new["G"] = b
    for line in rules.LINES:
        for slot in ("D", "F"):
            members = (new.get(line) or {}).get(slot) or []
            for i, x in enumerate(members):
                if not played(x) and (b := sub_for(slot)):
                    subs.append((x, b))
                    used.add(b)
                    members[i] = b
    return new, subs


def tour_score(lineup: dict, captain: str | None, assistant: str | None, tour: int, pool, matches, *,
               bench: list[str] | None = None, penalty: int = 0) -> ScoreBreakdown:
    """Очки тура: у каждого два лучших матча, сыгранность по звеньям менеджера, капитан ×2 (у капитана
    ноль матчей — ассистент ×2). С bench сначала делаются автозамены. penalty — очки за платные обмены."""
    idx = index(pool)
    subs = []
    if bench is not None:
        lineup, subs = apply_autosubs(lineup, bench, tour, idx)
    line_of = {x: line for _, line, x in positions(lineup) if x and line}
    ml = _matches_list(matches)
    goals: dict[str, list[dict]] = {}
    for m in ml:
        if m.get("tour") == tour:
            goals[m["id"]] = m.get("goals", [])
    bonus: dict[tuple[str, str], int] = {}
    for mid, gl in goals.items():
        for g in gl:
            a = g.get("author")
            if not a or a not in line_of:
                continue
            mates = [x for x in g.get("assists", []) if x and line_of.get(x) == line_of[a]]
            if mates:
                for x in [a, *mates]:
                    bonus[(x, mid)] = bonus.get((x, mid), 0) + rules.PTS_SYNERGY
    per = {}
    for x in lineup_ids(lineup):
        ms = tour_matches(x, tour, idx, ml)
        base = best_sum([p for _, p in ms])
        best = best_sum([p + bonus.get((x, mid), 0) for mid, p in ms])
        per[x] = {"matches": [p for _, p in ms], "best2": base, "synergy": best - base, "mult": 1}
    double = captain if played_in(captain, tour, idx) else assistant
    if double in per and per[double]["matches"]:
        per[double]["mult"] = rules.CAPTAIN_MULT
    total = 0
    for v in per.values():
        v["points"] = (v["best2"] + v["synergy"]) * v["mult"]
        total += v["points"]
    return ScoreBreakdown(total=total - penalty, by_id=per, subs=subs, penalty=penalty)

# ---------- ступени ----------


def regroup_steps(managers_month_points: dict, previous_steps: dict, inactive_ids) -> dict:
    """Ступени на новый месяц (ADR-014, раздел 14): id → (ступень, группа).

    Ступени: 0 — Высшая (20), 1 — Первая (40), 2 — Вторая (80), 3 — Третья (160, если менеджеров
    больше 400), 4 — «Коробка». Пересбор по очкам прошлого месяца (в первый раз — по турам 1–3);
    четверо лучших каждой прошлой группы поднимаются минимум на ступень. Кто не заходил 4+ тура —
    остаётся на своей ступени. Новички и опоздавшие — в Коробку. Группы — по 20 по очкам, хвост
    меньше 10 доливается в предыдущую; номер группы внутри ступени — с 1."""
    pts = dict(managers_month_points)
    prev = dict(previous_steps or {})
    inactive = set(inactive_ids or ())
    ids = set(pts) | {m for m in inactive if m in prev}
    n = len(ids)
    caps = list(rules.STEP_CAPS) + ([rules.STEP_CAP_BIG] if n > rules.STEP_BIG_FROM else [])
    steps = list(range(len(caps))) + [rules.STEP_BOX]
    first = not prev

    def score(m):
        return (-pts.get(m, 0), str(m))

    def up(s):
        """Ступень выше s среди тех, что есть."""
        higher = [x for x in steps if x < s]
        return max(higher) if higher else 0

    want: dict = {}
    for m in ids:
        if m in inactive:
            if m in prev:
                s = prev[m][0]
                want[m] = s if s in steps else rules.STEP_BOX
            else:
                want[m] = rules.STEP_BOX
    ranked = sorted((m for m in ids if m not in want and (first or m in prev)), key=score)
    pos = 0
    for s, cap in enumerate(caps):
        room = max(0, cap - sum(1 for m, x in want.items() if x == s))
        for m in ranked[pos:pos + room]:
            want[m] = s
        pos += room
    for m in ids:
        want.setdefault(m, rules.STEP_BOX)
    # четверо лучших прошлой группы — минимум на ступень выше
    groups: dict = {}
    for m, (s, g) in prev.items():
        if m in ids and m not in inactive:
            groups.setdefault((s, g), []).append(m)
    for (s, _), members in groups.items():
        for m in sorted(members, key=score)[:rules.PROMOTE_TOP]:
            want[m] = min(want[m], up(s))
    out = {}
    for s in steps:
        members = sorted((m for m in ids if want[m] == s), key=score)
        cut = [members[i:i + rules.GROUP_SIZE] for i in range(0, len(members), rules.GROUP_SIZE)]
        if len(cut) > 1 and len(cut[-1]) < rules.GROUP_TAIL_MIN:
            tail = cut.pop()
            cut[-1] += tail
        for g, grp in enumerate(cut, start=1):
            for m in grp:
                out[m] = (s, g)
    return out

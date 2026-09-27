"""Прошлый сезон для стоимости наклеек (ОзМпр, Ипр) и связка с пулом 2026/27 (ADR-014, раздел 5).

Приоры собираются один раз и лежат в git — zveno/data/prior_2526.json:

    venv/bin/python -m zveno.prior          # из tools/fantasy_model/data/stat_1378.json и history.json

Полевые — по id игрока на сайте лиги: очки за матч по таблице ADR-014 из сумм за сезон. Победы
в строке игрока нет — берём долю побед его клуба в 25/26 (history.json); клуба нет в РХЛ — половина.
Броски: каждые 2 в створ +1, по сумме — «БВ/2 − 0,25» за матч, как в модели сезона. Плюс-минус —
сумма за сезон. «ШП» и «РБ» — +2, не больше одного за матч.

Ворота — по клубам РХЛ, у которых был сезон 25/26: сейвы, пропущенные, «сухари», голы и
передачи вратарей из статистики, победы — из результатов клуба.

Связка с 2026/27 — по id лиги. Сменилось амплуа или имя, или у новичка есть тёзка в прошлом сезоне
под другим id — новичок по базе, а строка идёт в лог сборки на ручную проверку. Решение по ней —
в zveno/data/prior_links.json: {"link": {"<id 26/27>": <id 25/26>}, "newbie": [<id 26/27>]}.
"""
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from . import rules

DATA = Path(__file__).resolve().parent / "data"
PRIOR_FILE = DATA / "prior_2526.json"
LINKS_FILE = DATA / "prior_links.json"
ROOT = Path(__file__).resolve().parent.parent
STAT_FILE = ROOT / "tools" / "fantasy_model" / "data" / "stat_1378.json"
PRIOR_SEASON = "25/26"


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return 0.0


def _minutes(v: str) -> float:
    m = re.fullmatch(r"(\d+):(\d{2})", str(v).strip())
    return int(m.group(1)) + int(m.group(2)) / 60 if m else 0.0


def person_key(name: str) -> str:
    """Фамилия и имя без сокращений, которыми лига разводит тёзок: «Губин Иван А.» → «губин иван»."""
    words = name.lower().replace("ё", "е").split()
    return " ".join(words[:2])


def club_results(history: list[dict]) -> dict[str, dict]:
    """Матчи, победы клубов РХЛ в регулярке прошлого сезона — из history.json (id клубов уже наши)."""
    out: dict[str, dict] = defaultdict(lambda: {"gp": 0, "w": 0})
    for g in history:
        if g.get("season") != PRIOR_SEASON or g.get("stage") != "regular":
            continue
        hs, as_ = g["score"]
        for side, won in (("home", hs > as_), ("away", as_ > hs)):
            out[g[side]]["gp"] += 1
            out[g[side]]["w"] += int(won)
    return dict(out)


def skater_prior(row: dict, slot: str, win_share: float) -> dict | None:
    gp = _num(row["И"])
    if gp <= 0:
        return None
    sog = _num(row["БВ"])
    pts = (rules.PTS_PLAYED * gp + rules.PTS_WIN * gp * win_share
           + rules.PTS_GOAL[slot] * _num(row["Ш"]) + rules.PTS_ASSIST * _num(row["А"])
           + rules.PTS_PLUS_MINUS * _num(row["+/-"])
           + gp * max(0.0, sog / gp / rules.SHOTS_PER_POINT - 0.25)
           + rules.PTS_DECIDER * min(gp, _num(row["ШП"]) + _num(row["РБ"])))
    return {"gp": int(gp), "ppg": round(max(0.0, pts / gp), 3)}


def gate_prior(goalies: list[dict], results: dict | None) -> dict | None:
    """Ворота клуба: матчей — по минутам всех вратарей, победы — по результатам клуба."""
    g = sum(_minutes(r["ВП"]) for r in goalies) / 60
    if g < 1:
        return None
    s = {k: sum(_num(r[k]) for r in goalies) for k in ("ОБ", "ПШ", 'И"0"', "Ш", "А", "В")}
    win_share = results["w"] / results["gp"] if results and results["gp"] else s["В"] / g
    ppg = (rules.GATE_PLAYED + rules.GATE_WIN * win_share
           + max(0.0, s["ОБ"] / g / rules.GATE_SAVES_PER_POINT - 0.375)
           + rules.GATE_GOAL_AGAINST * s["ПШ"] / g + rules.GATE_SHUTOUT * s['И"0"'] / g
           + rules.GATE_GOALIE_GOAL * s["Ш"] / g + rules.GATE_GOALIE_ASSIST * s["А"] / g)
    return {"gp": round(g), "ppg": round(max(0.0, ppg), 3)}


def build_prior(stat: dict, history: list[dict], find_club) -> dict:
    """find_club(название клуба 25/26) → id клуба РХЛ или None (build_data.Teams.find_past)."""
    res = club_results(history)
    share = {c: r["w"] / r["gp"] for c, r in res.items() if r["gp"]}
    skaters = {}
    for rel, slot in (("defenses", "D"), ("forwards", "F")):
        for row in stat[rel]:
            if not row.get("id"):
                continue
            team = find_club(row["Клуб"])
            pr = skater_prior(row, slot, share.get(team, 0.5))
            if pr is None:
                continue
            number = row.get("№", "")
            skaters[str(row["id"])] = {"name": row["Игрок"], "number": int(number) if str(number).isdigit() else None,
                                       "club": row["Клуб"], "team": team, "slot": slot, **pr}
    by_club = defaultdict(list)
    for row in stat["goalies"]:
        team = find_club(row["Клуб"])
        if team:
            by_club[team].append(row)
    gates = {}
    for team, rows in sorted(by_club.items()):
        pr = gate_prior(rows, res.get(team))
        if pr:
            gates[team] = pr
    return {"season": "2025/26", "source": "nmhl.fhr.ru/stat/players/1378/, history.json",
            "built": datetime.now(rules.TZ).isoformat(timespec="minutes"), "skaters": skaters, "gates": gates}


def load_prior(path: Path = PRIOR_FILE) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {"skaters": {}, "gates": {}}


def load_links(path: Path = LINKS_FILE) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {"link": {}, "newbie": []}
    return {"link": {int(k): int(v) for k, v in d.get("link", {}).items()}, "newbie": [int(x) for x in d.get("newbie", [])]}


def link_skaters(players: dict[int, dict], prior: dict, links: dict | None = None
                 ) -> tuple[dict[int, tuple[float, float]], list[str]]:
    """Связка пула 26/27 с прошлым сезоном. players: id → {name, slot}.

    Возвращает id → (Ипр, ОзМпр) для связанных и строки на ручную проверку. Всё сомнительное —
    новичок по базе."""
    links = links or {"link": {}, "newbie": []}
    sk = prior.get("skaters", {})
    namesakes = defaultdict(list)
    for k, v in sk.items():
        namesakes[person_key(v["name"])].append(int(k))
    out, review = {}, []
    for pid, info in sorted(players.items()):
        if pid in links["newbie"]:
            continue
        src = links["link"].get(pid, pid)
        pr = sk.get(str(src))
        who = f"{info['name']} (id {pid})"
        if pr is None:
            others = [i for i in namesakes.get(person_key(info["name"]), []) if i not in players]
            if others:
                review.append(f"{who}: новичок, но в 25/26 есть тёзка под id {', '.join(map(str, others))}")
            continue
        if pid not in links["link"]:
            if pr["slot"] != info["slot"]:
                review.append(f"{who}: амплуа {pr['slot']} → {info['slot']}, считаем новичком")
                continue
            if person_key(pr["name"]) != person_key(info["name"]):
                review.append(f"{who}: в 25/26 под этим id «{pr['name']}», считаем новичком")
                continue
        if pr["gp"] < rules.PRIOR_MIN_GAMES:
            continue
        out[pid] = (pr["gp"], pr["ppg"])
    return out, review


def main() -> None:
    import sys
    sys.path.insert(0, str(ROOT))
    import build_data   # noqa: E402 — только для сборки приоров: названия клубов прошлых сезонов

    teams = build_data.load_teams()
    stat = json.loads(STAT_FILE.read_text(encoding="utf-8"))
    data = build_prior(stat, build_data.load_history(), teams.find_past)
    DATA.mkdir(exist_ok=True)
    PRIOR_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    print(f"Полевых: {len(data['skaters'])}, ворот: {len(data['gates'])} → {PRIOR_FILE}")
    missing = [t.get("id") for t in teams.all if t["id"] not in data["gates"]]
    print("Ворота без прошлого сезона (новички по базе):", ", ".join(missing))


if __name__ == "__main__":
    main()

"""Очки «Звена» за матч по протоколу лиги (ADR-014, раздел 4).

На входе — протокол, как его пишет league.py в results.json (dict). Старые протоколы без новых
колонок тоже годятся: недостающие цифры — нули.

Ловушки протокола, из-за которых очки считаются не по колонкам вратаря:
- в буллитах у обоих вратарей «В» = 0 — победа ворот берётся из итогового счёта;
- в овертайме победу записывают сменщику, который мог не отразить ни броска — снова счёт;
- решающий буллит в списке голов стоит как гол, а в строке игрока «Ш» = 0 и «РБ» = 1 — гол не
  засчитываем, «РБ» даёт +2 как победный.
"""
from dataclasses import dataclass, field

from . import rules

SHOOTOUT = "РБ"   # период гола серии буллитов в протоколе


def pid_key(pid: int) -> str:
    return f"p:{pid}"


def gate_key(club: str) -> str:
    return f"g:{club}"


@dataclass
class MatchPoints:
    """Очки одного матча: полевые по id игрока на сайте лиги и ворота по клубу."""
    home: str
    away: str
    technical: bool = False                                    # техническое поражение: матча не было
    skaters: dict[int, dict] = field(default_factory=dict)    # id → {club, slot, pts, name, number}
    gates: dict[str, int] = field(default_factory=dict)       # клуб → очки
    goals: list[dict] = field(default_factory=list)           # без серии буллитов: {team, author, assists}

    def played(self) -> list[str]:
        return [pid_key(i) for i in self.skaters] + [gate_key(c) for c in self.gates]


def _won(p: dict, side: str) -> bool:
    hs, as_ = p["home_score"], p["away_score"]
    return hs > as_ if side == "home" else as_ > hs


def goals_against(p: dict, side: str) -> int:
    """Пропущенные без победного буллита: в итоговом счёте проигравшего по буллитам он есть."""
    opp = p["away_score"] if side == "home" else p["home_score"]
    lost_shootout = p.get("decision") == "Б" and not _won(p, side)
    return opp - (1 if lost_shootout else 0)


def is_technical(p: dict) -> bool:
    """Техническое поражение: счёт есть, а ни составов, ни голов нет. Очков нет (раздел 12)."""
    return not p.get("lineups") and not p.get("goals") and (p["home_score"] + p["away_score"]) > 0


def _same(a: dict | None, b: dict | None) -> bool:
    """Один и тот же игрок: по id на сайте лиги, без id — по номеру."""
    if not a or not b:
        return False
    if a.get("id") is not None and b.get("id") is not None:
        return a["id"] == b["id"]
    return a.get("number") is not None and a.get("number") == b.get("number")


def big_penalty(p: dict, side: str, player: dict) -> bool:
    return any(x["team"] == side and (x.get("minutes") or 0) >= rules.BIG_PENALTY_MIN and _same(x.get("player"), player)
               for x in p.get("penalties", []))


def skater_points(row: dict, won: bool, big: bool) -> int:
    """Полевой: заявка, победа, голы из своей строки, передачи, +/-, броски, «ШП»/«РБ». Пол — 0."""
    if big:
        return 0
    slot = row["role"]
    pts = (rules.PTS_PLAYED + (rules.PTS_WIN if won else 0)
           + rules.PTS_GOAL[slot] * (row.get("goals") or 0)
           + rules.PTS_ASSIST * (row.get("assists") or 0)
           + rules.PTS_PLUS_MINUS * (row.get("plus_minus") or 0)
           + (row.get("shots") or 0) // rules.SHOTS_PER_POINT)
    if (row.get("gwg") or 0) > 0 or (row.get("so_winner") or 0) > 0:
        pts += rules.PTS_DECIDER
    return max(0, pts)


def gate_points(p: dict, side: str) -> int:
    """Ворота клуба: сыграли, победа по счёту, сейвы всех вратарей, пропущенные, «сухарь», гол и
    передача вратаря. Пол — 0."""
    won = _won(p, side)
    ga = goals_against(p, side)
    keepers = [k for k in p.get("lineups", []) if k["team"] == side and k["role"] == "G"]
    saves = sum(k.get("saves") or 0 for k in keepers)
    assists = sum(k.get("assists") or 0 for k in keepers if k.get("played", True))
    goals = sum(1 for g in p.get("goals", []) if g["team"] == side and g["period"] != SHOOTOUT
                and any(_same(g["author"], k["player"]) for k in keepers))
    pts = (rules.GATE_PLAYED + (rules.GATE_WIN if won else 0) + saves // rules.GATE_SAVES_PER_POINT
           + rules.GATE_GOAL_AGAINST * ga + (rules.GATE_SHUTOUT if ga == 0 else 0)
           + rules.GATE_GOALIE_GOAL * goals + rules.GATE_GOALIE_ASSIST * assists)
    return max(0, pts)


def match_points(p: dict, home: str, away: str) -> MatchPoints:
    """Очки всех, кто сыграл. home и away — id клубов из teams.json.

    Полевой без id на сайте лиги в «Звено» не попадает: его не с чем связать. Протокол без составов —
    полевые не сыграли, ворота сыграли по счёту (раздел 12)."""
    m = MatchPoints(home, away)
    if is_technical(p):
        m.technical = True
        return m
    club = {"home": home, "away": away}
    for side in ("home", "away"):
        m.gates[club[side]] = gate_points(p, side)
    for row in p.get("lineups", []):
        if row["role"] == "G" or not row.get("played", True):
            continue
        pl = row["player"]
        if pl.get("id") is None:
            continue
        side = row["team"]
        m.skaters[pl["id"]] = {"club": club[side], "slot": row["role"], "name": pl["name"], "number": pl.get("number"),
                               "pts": skater_points(row, _won(p, side), big_penalty(p, side, pl))}
    for g in p.get("goals", []):
        if g["period"] == SHOOTOUT:
            continue
        m.goals.append({"team": club[g["team"]], "author": (g.get("author") or {}).get("id"),
                        "assists": [a.get("id") for a in g.get("assists", [])]})
    return m


def best_sum(points: list[float], k: int = rules.BEST_MATCHES) -> float:
    """Сумма k лучших матчей тура."""
    return sum(sorted(points, reverse=True)[:k])

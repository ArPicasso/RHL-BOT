"""Ворота 26 клубов для аркады «Буллит» (ADR-017).

Лига входит в игру сложностью: порядок ворот и поведение вратаря считаются из пропущенных
за игру. Кто меньше пропускает, тот труднее. Пока у РХЛ мало протоколов, ворота стоят на
прошлом сезоне НМХЛ из history.json; клуб, которого нет нигде, идёт в середину лестницы.

Здесь только порядок и сложность от 0 до 1. Числа поведения вратаря — скорость, ширина
ловушки, «домик» — живут в webapp/bullit.js: их настраивают пальцем, а не в таблице.
"""
from __future__ import annotations

PRIOR_SEASON = "25/26"   # последний сезон НМХЛ: на нём стоят ворота до протоколов РХЛ
MIN_GP = 4               # меньше четырёх матчей — цифра ещё ничего не говорит


def conceded(games: list[dict], season: str | None = None) -> dict[str, list[int]]:
    """id клуба → [матчей, пропущено]. Понимает матч из league.json и из history.json."""
    out: dict[str, list[int]] = {}
    for g in games:
        if season is not None and g.get("season") != season:
            continue
        score = g.get("score")
        if isinstance(score, dict):
            home, away = score.get("home"), score.get("away")
        elif isinstance(score, (list, tuple)) and len(score) == 2:
            home, away = score
        else:
            continue
        if home is None or away is None:
            continue
        for team, got in ((g["home"], away), (g["away"], home)):
            row = out.setdefault(team, [0, 0])
            row[0] += 1
            row[1] += got
    return out


def rate(counts: dict[str, list[int]], team: str) -> float | None:
    """Пропущено за игру, если матчей хватает, чтобы цифре верить."""
    gp, ga = counts.get(team, (0, 0))
    return ga / gp if gp >= MIN_GP else None


def median(values: list[float]) -> float:
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def ladder(teams: list[dict], season: dict[str, list[int]], prior: dict[str, list[int]]) -> list[dict]:
    """Клубы от самых пробиваемых ворот к самым непробиваемым.

    Сначала этот сезон, потом прошлый, потом середина лестницы: клуб без матчей не должен
    оказаться ни первым, ни последним — мы про него просто ничего не знаем."""
    rows = []
    for t in teams:
        gp, ga = season.get(t["id"], (0, 0))
        src, value = "season", rate(season, t["id"])
        if value is None:
            gp, ga = prior.get(t["id"], (0, 0))
            src, value = "prior", rate(prior, t["id"])
        if value is None:
            src, gp, ga = "none", 0, 0
        rows.append({"team": t, "src": src, "gp": gp, "ga": ga, "value": value})
    known = [r["value"] for r in rows if r["value"] is not None]
    mid = median(known) if known else 3.0
    for r in rows:
        r["sort"] = r["value"] if r["value"] is not None else mid
    rows.sort(key=lambda r: (-r["sort"], r["team"]["id"]))
    last = max(len(rows) - 1, 1)
    out = []
    for i, r in enumerate(rows):
        t = r["team"]
        out.append({"id": t["id"], "name": t["name"], "abbr": t.get("abbr", ""), "logo": t.get("logo", ""),
                    "colors": t.get("colors", []), "city": t.get("city", ""),
                    "goalie": f"players/clubs/{t['id']}-goalie.webp",
                    "src": r["src"], "gp": r["gp"],
                    "ga": round(r["value"], 2) if r["value"] is not None else None,
                    "t": round(i / last, 3)})
    return out


def build(teams: list[dict], games: list[dict], history: list[dict], season: str = "") -> dict:
    """Данные игры: 26 клубов по возрастанию сложности."""
    return {"season": season, "prior": PRIOR_SEASON,
            "clubs": ladder(teams, conceded(games), conceded(history, PRIOR_SEASON))}

"""Матчи прошлых сезонов НМХЛ для истории очных встреч (ADR-006). Результат — history.json."""
import argparse
import asyncio
import json
import re
from datetime import date
from pathlib import Path

import aiohttp

import league
from build_data import H2H_LAST, HISTORY_FILE, HISTORY_PROTOCOLS, Teams, load_teams, pair_key

SEASONS = 5
REPORT_RE = re.compile(r"/report/(\d+)/\?idgame=(\d+)")

DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{2})")
SCORE_RE = re.compile(r"(\d+):(\d+)\s*(ОТ|Б)?")
TOURNAMENT_RE = re.compile(r"^(\d{2}/\d{2}) \| (Регулярный чемпионат|Плей-офф)$")


def parse_calendar(html: str) -> list[dict]:
    """Сыгранные матчи со страницы календаря турнира. Технические результаты («-:+») пропускаем."""
    games = []
    for day in league.parse_html(html).find_all("tr"):
        cells = [ch for ch in day.children if isinstance(ch, league.Node) and ch.tag == "td"]
        if not cells or "date" not in cells[0].classes():
            continue
        m = DATE_RE.search(cells[0].text())
        if not m:
            continue
        when = date(2000 + int(m.group(3)), int(m.group(2)), int(m.group(1)))
        for row in day.find_all("tr"):
            team_td, count_td = row.find("td", "col_team"), row.find("td", "col_count")
            if not team_td or not count_td:
                continue
            names = [a.text() for a in team_td.find_all("a")]
            s = SCORE_RE.fullmatch(count_td.text())
            if len(names) != 2 or not s:
                continue
            link = count_td.find("a")
            rep = REPORT_RE.search(link.attrs.get("href") or "") if link else None
            games.append({"date": when.isoformat(), "home": names[0], "away": names[1],
                          "home_score": int(s.group(1)), "away_score": int(s.group(2)),
                          "decision": s.group(3) or "",
                          "tournament": int(rep.group(1)) if rep else None,
                          "game_id": int(rep.group(2)) if rep else None})
    return games


def pick_tournaments(tournaments: list[tuple[int, str]], seasons: int) -> list[tuple[int, str]]:
    """Регулярка и плей-офф последних `seasons` сезонов: [(id, "25/26 | Плей-офф"), …]."""
    wanted = []
    for tid, name in tournaments:
        m = TOURNAMENT_RE.match(name)
        if m and m.group(1) not in wanted:
            wanted.append(m.group(1))
    keep = set(wanted[:seasons])
    return [(tid, name) for tid, name in tournaments
            if (m := TOURNAMENT_RE.match(name)) and m.group(1) in keep]


def to_history(teams: Teams, games: list[dict], season: str, stage: str) -> list[dict]:
    """Названия → id из teams.json; матчи с командами не из РХЛ-2026/27 отбрасываем."""
    out = []
    for g in games:
        home, away = teams.find_past(g["home"]), teams.find_past(g["away"])
        if home and away:
            item = {"date": g["date"], "season": season, "stage": stage, "home": home, "away": away,
                    "score": [g["home_score"], g["away_score"]], "decision": g["decision"]}
            if g.get("game_id"):   # протокол матча: по нему открывается разбор (ADR-008)
                item.update(tournament=g["tournament"], game_id=g["game_id"])
            out.append(item)
    return out


async def fetch_history(site: str, teams: Teams, seasons: int = SEASONS) -> list[dict]:
    headers = {"User-Agent": league.USER_AGENT}
    async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=60),
                                     trust_env=True) as s:
        chosen = pick_tournaments(league.parse_tournaments(await league._get(s, f"{site}/calendar/")), seasons)
        out = []
        for tid, name in chosen:
            await asyncio.sleep(league.PAUSE)
            season, stage = name.split(" | ")
            # «/0/» — все месяцы; без него плей-офф отдаёт только текущий месяц
            games = parse_calendar(await league._get(s, f"{site}/calendar/{tid}/0/"))
            out += to_history(teams, games, season, "playoff" if stage == "Плей-офф" else "regular")
            print(f"{name}: {len(games)} матчей")
    return sorted(out, key=lambda g: (g["date"], g["home"]))


def save_history(games: list[dict], path: Path = HISTORY_FILE) -> None:
    # по матчу на строку: так прошлые сезоны читаются в diff
    lines = ",\n".join(json.dumps(g, ensure_ascii=False) for g in games)
    path.write_text(f'{{"source": "nmhl.fhr.ru", "games": [\n{lines}\n]}}\n', encoding="utf-8")


# ---------- протоколы прошлых матчей (ADR-008) ----------


def recap_candidates(games: list[dict], last: int = H2H_LAST) -> list[dict]:
    """Матчи, которые видны в «Последних встречах»: последние `last` у каждой пары команд."""
    by_pair: dict[str, list[dict]] = {}
    for g in sorted(games, key=lambda g: g["date"]):
        by_pair.setdefault(pair_key(g["home"], g["away"]), []).append(g)
    return [g for pair in by_pair.values() for g in pair[-last:] if g.get("game_id")]


def load_protocols(path: Path = HISTORY_PROTOCOLS) -> dict[str, dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def save_protocols(protocols: dict[str, dict], path: Path = HISTORY_PROTOCOLS) -> None:
    # по протоколу на строку: прошлые сезоны не меняются, diff остаётся читаемым
    lines = ",\n".join(f"{json.dumps(k)}: {json.dumps(v, ensure_ascii=False, separators=(',', ':'))}"
                       for k, v in sorted(protocols.items(), key=lambda kv: int(kv[0])))
    tmp = path.with_suffix(".tmp")
    tmp.write_text(f"{{\n{lines}\n}}\n", encoding="utf-8")
    tmp.replace(path)


async def fetch_protocols(site: str, games: list[dict], path: Path = HISTORY_PROTOCOLS) -> int:
    """Протоколы матчей из «Последних встреч», по одному в секунду. Скачанные раньше не трогаем."""
    protocols = load_protocols(path)
    todo = [g for g in recap_candidates(games) if str(g["game_id"]) not in protocols]
    headers = {"User-Agent": league.USER_AGENT}
    async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=60),
                                     trust_env=True) as s:
        for i, g in enumerate(todo, 1):
            await asyncio.sleep(league.PAUSE)
            try:
                html = await league._get(s, f"{site}/report/{g['tournament']}/?idgame={g['game_id']}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                print(f"протокол {g['game_id']} не скачался: {e}")
                continue
            p = league.parse_protocol(html, g["game_id"])
            if p:
                protocols[str(g["game_id"])] = compact(p.to_json())
            if i % 25 == 0 or i == len(todo):
                save_protocols(protocols, path)
                print(f"{i}/{len(todo)}")
    save_protocols(protocols, path)
    return len(todo)


def compact(p: dict) -> dict:
    """Из протокола — только то, что нужно разбору матча. Нули и пустое не храним."""
    out = {k: p[k] for k in ("date", "home", "away", "home_score", "away_score", "decision", "periods",
                             "goals", "penalties", "referees", "linesmen", "coaches", "attendance", "time")}
    out["lineups"] = [{k: v for k, v in x.items() if v not in (0, "", False) or k in ("team", "role", "played")}
                      for x in p["lineups"]]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Скачать матчи прошлых сезонов в history.json")
    ap.add_argument("--site", default=league.DEFAULT_SITE)
    ap.add_argument("--seasons", type=int, default=SEASONS)
    ap.add_argument("--out", type=Path, default=HISTORY_FILE)
    ap.add_argument("--protocols", action="store_true",
                    help="докачать протоколы матчей из «Последних встреч» в history_protocols.json")
    args = ap.parse_args()
    if args.protocols:
        games = json.loads(args.out.read_text(encoding="utf-8"))["games"]
        n = asyncio.run(fetch_protocols(args.site, games))
        print(f"Скачано протоколов: {n} → {HISTORY_PROTOCOLS}")
        return
    games = asyncio.run(fetch_history(args.site, load_teams(), args.seasons))
    save_history(games, args.out)
    print(f"Матчей между командами РХЛ: {len(games)} → {args.out}")


if __name__ == "__main__":
    main()

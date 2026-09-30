"""Собирает webapp/data/league.json для мини-аппа: команды, матчи, результаты, таблица (ADR-002)."""
import argparse
import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import feed
import league
import rhockey

BASE = Path(__file__).parent
TZ = ZoneInfo("Europe/Moscow")
TEAMS_FILE = BASE / "teams.json"
OFFICIAL_GAMES = BASE / "games.json"
OFFICIAL_TEAM = "ryazan-vdv"          # у кого из команд есть официальный календарь — games.json
HISTORY_FILE = BASE / "history.json"
HISTORY_PROTOCOLS = BASE / "history_protocols.json"   # протоколы матчей из «Последних встреч» (ADR-008)
HIDDEN_FILE = BASE / "hidden_players.json"
KITS_FILE = BASE / "art" / "players" / "kits.json"   # форма клубов для стикеров игроков, tools/player_kits.py
PAST_CLUBS_FILE = BASE / "past_clubs.json"   # клубы прошлых сезонов, которых нет в РХЛ: эмблемы для лидеров (ADR-009)
OUT = BASE / "webapp" / "data" / "league.json"
CHANNELS_FILE = BASE / "channels.json"       # каналы клубов для листа «Главной» (ADR-015)
POSTS_FILE = BASE / "channel_posts.json"     # их посты: собирает tg_channels.py перед этим шагом
HIDDEN_NAME = "Игрок скрыт"
H2H_LAST = 5


def norm(name: str) -> str:
    name = name.lower().replace("ё", "е").replace("«", "").replace("»", "").replace('"', "")
    name = re.sub(r"\s*-\s*", "-", name)
    name = re.sub(r"^(мхк|хк)\s+", "", name.strip())
    return re.sub(r"\s+", " ", name)


class Teams:
    def __init__(self, teams: list[dict]):
        self.all = teams
        self.by_rh = {t["rhockey"]: t["id"] for t in teams}
        self.by_name = {norm(n): t["id"] for t in teams for n in [t["name"], *t["aliases"]]}
        self.by_former = {norm(n): t["id"] for t in teams for n in t.get("former", [])}

    def find(self, name: str) -> str | None:
        return self.by_name.get(norm(name))

    def find_past(self, name: str) -> str | None:
        """То же, но с прежними названиями клубов — для матчей прошлых сезонов (ADR-006)."""
        return self.find(name) or self.by_former.get(norm(name))


def load_teams(path: Path = TEAMS_FILE) -> Teams:
    return Teams(json.loads(path.read_text(encoding="utf-8")))

# ---------- матчи ----------


def official_games(teams: Teams, path: Path = OFFICIAL_GAMES) -> list[dict]:
    games = []
    for g in json.loads(path.read_text(encoding="utf-8")):
        opp = teams.find(g["opponent"])
        if opp is None:
            raise ValueError(f"соперник «{g['opponent']}» из games.json не найден в teams.json")
        home, away = (OFFICIAL_TEAM, opp) if g["home"] else (opp, OFFICIAL_TEAM)
        games.append({"id": f"n{g['n']}", "n": g["n"], "date": g["date"], "home": home, "away": away,
                      "official": True})
    return games


def merge_calendar(teams: Teams, raw: list[rhockey.RawGame], official: list[dict]) -> list[dict]:
    """Официальный календарь заменяет агрегатор для своих команд."""
    covered = {OFFICIAL_TEAM}
    games = list(official)
    for g in raw:
        home, away = teams.by_rh[g.home_rh], teams.by_rh[g.away_rh]
        if home in covered or away in covered:
            continue
        games.append({"id": f"rh{g.rh_id}", "n": None, "date": g.date.isoformat(), "home": home, "away": away,
                      "official": False})
    return sorted(games, key=lambda g: (g["date"], g["id"]))

# ---------- результаты ----------


def load_hidden(path: Path = HIDDEN_FILE) -> set[int]:
    """Id игроков на сайте лиги, которых не показываем по просьбе (ADR-007, ADR-008)."""
    try:
        return {int(x["id"]) for x in json.loads(path.read_text(encoding="utf-8"))}
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return set()


def shown(player: dict | None, hidden: set[int] = frozenset()) -> str:
    """Имя игрока для мини-аппа: скрытых по просьбе заменяем."""
    if not player:
        return ""
    return HIDDEN_NAME if player.get("id") in hidden else player["name"]


def goalies_of(p: dict) -> set[tuple[str, int]]:
    """(команда, номер) вратарей матча — стикер вратаря у гола и удаления (ADR-009)."""
    return {(k["team"], k["player"]["number"]) for k in p.get("lineups", []) if k["role"] == "G"}


def sticker(player: dict | None, team: str, goalies: set[tuple[str, int]], hidden: set[int]) -> dict:
    """Номер для стикера игрока и пометка вратаря. Скрытому игроку номер не показываем."""
    if not player or player.get("id") in hidden or player.get("number") is None:
        return {}
    out = {"no": player["number"]}
    if (team, player["number"]) in goalies:
        out["gk"] = 1
    return out


def fill_result(g: dict, p: dict, hidden: set[int] = frozenset()) -> None:
    """Счёт, голы и сведения из протокола — в матч, как их ждёт мини-апп."""
    g["n"] = g.get("n") or p.get("n")
    g["time"] = p.get("time")
    g["attendance"] = p.get("attendance")
    g["score"] = {"home": p["home_score"], "away": p["away_score"], "decision": p["decision"],
                  "periods": p["periods"]}
    gk = goalies_of(p)
    g["goals"] = [{"period": x["period"], "time": x["time"], "team": x["team"], "score": x["score"],
                   "strength": x["strength"], "author": shown(x["author"], hidden),
                   "assists": [shown(a, hidden) for a in x["assists"]],
                   **sticker(x["author"], x["team"], gk, hidden)} for x in p["goals"]]


def attach_results(games: list[dict], teams: Teams, results: league.Results,
                   protocols: dict[str, dict] | None = None, hidden: set[int] = frozenset()) -> list[str]:
    """Протоколы лиги ложатся на матчи по дате и командам. Возвращает непривязанные.

    protocols, если передан, собирает протокол каждого привязанного матча по id матча."""
    index = {(g["date"], g["home"], g["away"]): g for g in games}
    unmatched = []
    for tournament in results.values():
        for p in tournament.values():
            home, away = teams.find(p["home"]), teams.find(p["away"])
            g = index.get((p["date"], home, away))
            if g is None:
                unmatched.append(f"{p['date']} {p['home']} — {p['away']}")
                continue
            fill_result(g, p, hidden)
            if protocols is not None:
                protocols[g["id"]] = p
    return unmatched


def past_id(h: dict) -> str:
    """Id прошлого матча в мини-аппе: h + номер протокола на сайте лиги."""
    return f"h{h['game_id']}"


def past_recaps(history: list[dict], protocols: dict[str, dict], wanted: set[str], teams: dict[str, str],
                hidden: set[int] = frozenset()) -> dict[str, dict]:
    """Разборы прошлых матчей из «Последних встреч». Матча нет в league.json — он лежит в файле целиком."""
    out = {}
    for h in history:
        if not h.get("game_id") or past_id(h) not in wanted or str(h["game_id"]) not in protocols:
            continue
        g = {"id": past_id(h), "n": None, "date": h["date"], "home": h["home"], "away": h["away"],
             "season": h["season"], "stage": h["stage"]}
        p = protocols[str(h["game_id"])]
        fill_result(g, p, hidden)
        d = match_detail(g, p, teams, hidden)
        d["game"] = g
        out[g["id"]] = d
    return out

# ---------- разбор матча (ADR-008) ----------


def secs(t: str) -> int:
    m, s = t.split(":")
    return int(m) * 60 + int(s)


NUM = {3: "три", 4: "четыре", 5: "пять", 6: "шесть", 7: "семь", 8: "восемь", 9: "девять", 10: "десять"}
PERIOD_GEN = {"1": "первого", "2": "второго", "3": "третьего"}
PP_MINUTES = {2, 4, 5}
BURST_MIN_3 = 5 * 60     # три шайбы подряд — сюжет, только если уложились в пять минут   # после этих удалений соперник играет в большинстве


def goals_consistent(g: dict) -> bool:
    """Голы протокола сходятся со счётом: счёт после каждого гола растёт на единицу у забившей
    стороны и приходит к итоговому. Бывает, что лига ошибается в протоколе, — тогда не выводим
    из голов ничего (ни победной шайбы, ни сюжета)."""
    s = g.get("score")
    goals = g.get("goals") or []
    if not s or not goals:
        return False
    h = a = 0
    for x in goals:
        if x["team"] == "home":
            h += 1
        else:
            a += 1
        if x.get("score") != f"{h}:{a}":
            return False
    return (h, a) == (s["home"], s["away"])


def winning_goal(g: dict, official: int | None = None) -> int | None:
    """Номер победной шайбы в списке голов — правило лиги: гол победителя, после которого
    соперник уже не сравнял счёт. При 5:2 это третья шайба победителя (3:1), а не последняя.

    official — номер гола из колонки «ШП» протокола, если лига её заполнила: он главнее расчёта."""
    s = g.get("score")
    if not s or s["home"] == s["away"] or not goals_consistent(g):
        return None
    win = "home" if s["home"] > s["away"] else "away"
    goals = g["goals"]
    if official is not None and 0 <= official < len(goals) and goals[official]["team"] == win:
        return official
    need = min(s["home"], s["away"]) + 1
    count = 0
    for i, x in enumerate(goals):
        if x["team"] == win:
            count += 1
            if count == need:
                return i
    return None


def official_winning_goal(p: dict) -> int | None:
    """Номер гола, автору которого лига записала «ШП», если такой гол один."""
    if p["home_score"] == p["away_score"]:
        return None
    win = "home" if p["home_score"] > p["away_score"] else "away"
    scorers = [k["player"] for k in p.get("lineups", []) if k.get("gwg") and k["team"] == win]
    if len(scorers) != 1:
        return None
    who = scorers[0]
    hits = [i for i, x in enumerate(p["goals"]) if x["team"] == win and (
        (who.get("id") and x["author"].get("id") == who["id"])
        or (not who.get("id") and x["author"].get("number") == who.get("number")))]
    if len(hits) == 1:
        return hits[0]
    # автор забил несколько раз: победная — та, после которой соперник не сравнял
    computed = winning_goal({"score": {"home": p["home_score"], "away": p["away_score"]},
                             "goals": p["goals"]})
    return computed if computed in hits else None


def _burst(goals: list[dict]) -> tuple[int, int] | None:
    """Самая длинная серия шайб одной команды без ответа внутри одного периода: от четырёх,
    а три — только если уложились в пять минут. Три подряд за период — обычное дело, не сюжет."""
    best = None
    i = 0
    while i < len(goals):
        j = i
        while (j + 1 < len(goals) and goals[j + 1]["team"] == goals[i]["team"]
               and goals[j + 1]["period"] == goals[i]["period"]):
            j += 1
        n = j - i + 1
        quick = secs(goals[j]["time"]) - secs(goals[i]["time"]) <= BURST_MIN_3
        if (n >= 4 or (n == 3 and quick)) and (best is None or n > best[1] - best[0] + 1):
            best = (i, j)
        i = j + 1
    return best


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _sentence(text: str) -> str:
    """Точка в конце, но не вторая: имена в протоколе бывают с сокращением — «Царёв Иван А.»."""
    return text if text.endswith(".") else text + "."


def story(g: dict, teams: dict[str, str], goalies: list[dict] = (), gw: int | None = None) -> str:
    """Сюжет матча одной-двумя фразами. Только факты протокола, без оценок. Нечего сказать — пусто."""
    s = g.get("score")
    goals = [x for x in g.get("goals", []) if x["period"] != "РБ"]
    if not s or s["home"] == s["away"] or not goals_consistent(g):
        return ""
    win = "home" if s["home"] > s["away"] else "away"
    lose = "away" if win == "home" else "home"
    name = {side: f"«{teams.get(g[side], g[side])}»" for side in ("home", "away")}
    out = []

    # камбэк: победитель проигрывал в две шайбы и больше
    worst, worst_score, diff = 0, None, 0
    for x in goals:
        diff += 1 if x["team"] == win else -1
        if diff < worst:
            worst, worst_score = diff, x["score"]
    if worst <= -2:
        h, a = worst_score.split(":")
        mine, theirs = (h, a) if win == "home" else (a, h)
        end = ", но отыгрались" if s["decision"] else " и вырвали победу"
        out.append(f"Камбэк {name[win]}: уступали {mine}:{theirs}{end}.")

    if s["decision"] == "Б":
        so = [x for x in g.get("goals", []) if x["period"] == "РБ"]
        who = so[-1]["author"] if so and so[-1]["author"] != HIDDEN_NAME else ""
        out.append(_sentence(f"Всё решили буллиты, победный забил {who}") if who else "Всё решили буллиты.")
    elif s["decision"] == "ОТ" and goals and goals[-1]["period"] == "ОТ":
        who = goals[-1]["author"]
        out.append(_sentence(f"В овертайме победу принёс {who}") if who != HIDDEN_NAME else "Победу принёс овертайм.")

    burst = _burst(goals)
    if burst and len(out) < 2:
        i, j = burst
        team, n = goals[i]["team"], j - i + 1
        mins = max(1, -(-(secs(goals[j]["time"]) - secs(goals[i]["time"])) // 60))
        period = PERIOD_GEN.get(goals[i]["period"])
        word = NUM.get(n, str(n))
        text = f"{word} {plural(n, 'шайба', 'шайбы', 'шайб')} подряд у {name[team]}"
        if period and j > i:
            text += f" за {mins} {plural(mins, 'минуту', 'минуты', 'минут')} {period} периода"
        if team == win and goals[0]["team"] == lose and i > 0 and worst > -2:
            side = "хозяева" if lose == "home" else "гости"
            out.append(f"Первыми забили {side}, а дальше {text}.")
        else:
            out.append(text[0].upper() + text[1:] + ".")

    if len(out) < 2 and s[lose] == 0:
        keeper = [k for k in goalies if k["team"] == win and k["shots"]]
        if len(keeper) == 1 and keeper[0]["name"] != HIDDEN_NAME:
            k = keeper[0]
            out.append(f"Сухой матч: {k['name']} отразил все {k['shots']} "
                       f"{plural(k['shots'], 'бросок', 'броска', 'бросков')}.")

    if gw is None:
        gw = winning_goal(g)
    if len(out) < 2 and not s["decision"] and gw is not None:
        x = g["goals"][gw]
        left = 60 * 60 - secs(x["time"])
        if 0 < left <= 180 and x["author"] != HIDDEN_NAME:
            out.append(_sentence(f"Победная шайба за {left // 60}:{left % 60:02d} до сирены: {x['author']}"))
    return " ".join(out[:2])


def power_play(p: dict) -> dict[str, list[int]]:
    """Голы в большинстве и число удалений соперника, дающих большинство. Взаимные не считаем."""
    pens = [x for x in p.get("penalties", []) if x["minutes"] in PP_MINUTES]
    chances = {"home": 0, "away": 0}
    for x in pens:
        mutual = any(y is not x and y["team"] != x["team"] and y["time"] == x["time"]
                     and y["minutes"] == x["minutes"] for y in pens)
        if not mutual:
            chances["away" if x["team"] == "home" else "home"] += 1
    goals = {side: sum(1 for x in p["goals"] if x["team"] == side and x["strength"].startswith("бол"))
             for side in ("home", "away")}
    return {side: [goals[side], chances[side]] for side in ("home", "away")}


def match_detail(g: dict, p: dict, teams: dict[str, str], hidden: set[int] = frozenset()) -> dict:
    """webapp/data/matches/<id>.json — всё о сыгранном матче, чего нет в league.json (ADR-008)."""
    other = {"home": "away", "away": "home"}
    lineups = p.get("lineups", [])
    goalies = [{"team": k["team"], "no": k["player"]["number"], "name": shown(k["player"], hidden),
                "shots": k.get("shots_against", 0), "saves": k.get("saves", 0), "toi": k.get("toi", "")}
               for k in lineups if k["role"] == "G" and k["played"] and (k.get("shots_against") or k.get("toi"))]
    shots = {side: sum(k["shots"] for k in goalies if k["team"] == other[side]) for side in ("home", "away")}
    fo = {side: sum(k.get("faceoffs_won", 0) for k in lineups if k["team"] == side) for side in ("home", "away")}
    pim = {side: sum(x["minutes"] for x in p.get("penalties", []) if x["team"] == side) for side in ("home", "away")}
    rosters: dict[str, dict[str, list]] = {"home": {"G": [], "D": [], "F": []}, "away": {"G": [], "D": [], "F": []}}
    for k in lineups:
        if k["player"].get("id") in hidden:
            continue
        row = {"no": k["player"]["number"], "name": k["player"]["name"], "cap": k.get("captain", ""),
               "g": k.get("goals", 0), "a": k.get("assists", 0)}
        if not k["played"]:
            row["dnp"] = True
        rosters[k["team"]][k["role"]].append(row)
    length = 65 if p.get("decision") else 60
    last = max((secs(x["time"]) for x in p["goals"] if x["period"] != "РБ"), default=0)
    gw = winning_goal(g, official_winning_goal(p))
    return {
        "id": g["id"],
        "story": story(g, teams, goalies, gw),
        "gw": gw,
        "length": max(length, -(-last // 60)),
        "penalties": [{"time": x["time"], "team": x["team"],
                       "no": x["player"]["number"] if x["player"] and x["player"].get("id") not in hidden else None,
                       "who": shown(x["player"], hidden) or "Командный штраф",
                       "min": x["minutes"], "why": x["reason"],
                       **({"gk": 1} if sticker(x["player"], x["team"], goalies_of(p), hidden).get("gk") else {})}
                      for x in p.get("penalties", [])],
        "shots": shots if any(shots.values()) else None,
        "faceoffs": fo if any(fo.values()) else None,
        "pim": pim,
        "pp": power_play(p),
        "goalies": goalies,
        "lineups": rosters if lineups else None,
        "referees": p.get("referees", []),
        "linesmen": p.get("linesmen", []),
        "coaches": dict(zip(("home", "away"), p.get("coaches", ["", ""]))),
    }

# ---------- таблица ----------


@dataclass
class Row:
    team: str
    gp: int = 0
    w: int = 0      # в основное время
    otw: int = 0
    sow: int = 0
    sol: int = 0
    otl: int = 0
    l: int = 0
    gf: int = 0
    ga: int = 0
    form: list[str] = field(default_factory=list)

    @property
    def pts(self) -> int:
        return 2 * (self.w + self.otw + self.sow) + self.sol + self.otl

    def to_json(self) -> dict:
        return {"team": self.team, "gp": self.gp, "w": self.w, "otw": self.otw, "sow": self.sow,
                "sol": self.sol, "otl": self.otl, "l": self.l, "gf": self.gf, "ga": self.ga,
                "pts": self.pts, "form": self.form[-5:]}


def standings(teams: Teams, games: list[dict]) -> dict[str, list[dict]]:
    """Правила лиги: победа — 2, поражение в ОТ или по буллитам — 1. Сверено с таблицей НМХЛ 25/26."""
    rows = {t["id"]: Row(t["id"]) for t in teams.all}
    for g in sorted((g for g in games if g.get("score")), key=lambda g: g["date"]):
        s = g["score"]
        for side, gf, ga in (("home", s["home"], s["away"]), ("away", s["away"], s["home"])):
            r = rows[g[side]]
            r.gp += 1
            r.gf += gf
            r.ga += ga
            won = gf > ga
            if s["decision"] == "ОТ":
                r.otw += won
                r.otl += not won
            elif s["decision"] == "Б":
                r.sow += won
                r.sol += not won
            else:
                r.w += won
                r.l += not won
            r.form.append("W" if won else "L")
    conf = {t["id"]: t["conf"] for t in teams.all}
    table: dict[str, list[dict]] = {"west": [], "east": []}
    for r in sorted(rows.values(), key=lambda r: (-r.pts, -r.w, -(r.gf - r.ga), -r.gf)):
        table[conf[r.team]].append(r.to_json())
    return table

# ---------- очные встречи ----------


def load_history_protocols(path: Path = HISTORY_PROTOCOLS) -> dict[str, dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def load_history(path: Path = HISTORY_FILE) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))["games"]
    except (FileNotFoundError, ValueError, KeyError):
        return []


def pair_key(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


def head_to_head(games: list[dict], history: list[dict], with_protocol: set[str] = frozenset()) -> dict[str, dict]:
    """Для каждой пары из календаря сезона: победы, голы и последние встречи (ADR-006).

    Встречи — прошлые сезоны из history.json плюс уже сыгранные матчи этого сезона.
    Встреча с разбором (ADR-008) получает id: прошлая — h<номер протокола>, если протокол скачан
    в history_protocols.json, этого сезона — id матча из календаря."""
    past = []
    for h in history:
        m = {k: h[k] for k in ("date", "home", "away", "score", "decision")}
        if h.get("game_id") and str(h["game_id"]) in with_protocol:
            m["id"] = past_id(h)
        past.append(m)
    past += [{"date": g["date"], "home": g["home"], "away": g["away"], "id": g["id"],
              "score": [g["score"]["home"], g["score"]["away"]], "decision": g["score"]["decision"]}
             for g in games if g.get("score")]
    by_pair: dict[str, list[dict]] = {}
    for m in sorted(past, key=lambda m: m["date"]):
        by_pair.setdefault(pair_key(m["home"], m["away"]), []).append(m)
    out = {}
    for key in sorted({pair_key(g["home"], g["away"]) for g in games}):
        a, b = key.split("|")
        wins, goals = {a: 0, b: 0}, {a: 0, b: 0}
        meetings = by_pair.get(key, [])
        for m in meetings:
            hs, as_ = m["score"]
            goals[m["home"]] += hs
            goals[m["away"]] += as_
            wins[m["home"] if hs > as_ else m["away"]] += 1
        out[key] = {"games": len(meetings), "wins": wins, "goals": goals,
                    "since": meetings[0]["date"][:4] if meetings else None,
                    "last": meetings[::-1][:H2H_LAST]}
    return out


def mark_stories(h2h: dict[str, dict], details: dict[str, dict]) -> None:
    """Флаг story у встреч, в разборе которых есть сюжет: тур выбирает встречу для разбора,
    не скачивая пять файлов (ADR-013)."""
    for pair in h2h.values():
        for m in pair["last"]:
            if m.get("id") and (details.get(m["id"]) or {}).get("story"):
                m["story"] = True

# ---------- лидеры лиги (ADR-009) ----------

LEADERS_TOP = 10
# цифры, которые мини-апп показывает у каждого показателя: остальное из строки лиги не берём
LEADER_FIELDS = {"pts": ("gp", "g", "a", "pts"), "g": ("gp", "g", "a", "pts"), "a": ("gp", "g", "a", "pts"),
                 "pm": ("gp", "pts", "pm"), "pim": ("gp", "pts", "pim"), "sv_pct": ("gp", "gaa", "sv_pct")}


def load_past_clubs(path: Path = PAST_CLUBS_FILE) -> dict[str, dict]:
    """Написание клуба → запись past_clubs.json (эмблема, форма): для клубов, которых нет в teams.json."""
    try:
        clubs = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return {norm(n): c for c in clubs for n in [c["name"], *c.get("aliases", [])]}


def load_kits(path: Path = KITS_FILE) -> dict[str, dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def load_leaders(path: Path = league.LEADERS_FILE) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def leaders(teams: Teams, src: dict, hidden: set[int] = frozenset(), past: dict[str, dict] | None = None,
            kits: dict[str, dict] | None = None) -> dict | None:
    """Топ-10 по каждому показателю и лучший игрок каждой команды с 11-го места.

    Места — как у лиги. Скрытого игрока нет в списке, место за ним остаётся пустым.
    Клуб прошлого сезона — нынешний id по teams.json; клуба нет в РХЛ — название и эмблема из
    past_clubs.json. Амплуа и номер — для стикера игрока вместо фото (фото не берём, ADR-007);
    `kit` — какой клубной формы стикер, если картинка есть в art/players/kits.json; сам список
    форм мини-апп берёт из league.json."""
    if not src.get("categories"):
        return None
    past = load_past_clubs() if past is None else past
    kits = load_kits() if kits is None else kits
    m = re.match(r"(\d{2})/(\d{2})", src.get("name", ""))
    out = {
        "season": f"20{m.group(1)}/{m.group(2)}" if m else "",
        "league": "РХЛ" if "rhl." in src.get("site", "") else "НМХЛ",
        "stage": "плей-офф" if "Плей-офф" in src.get("name", "") else "регулярный чемпионат",
        "updated": src.get("updated", ""),
        "categories": {},
    }
    for cat, fields in LEADER_FIELDS.items():
        rows, seen = [], set()
        for r in src["categories"].get(cat, []):
            if r.get("id") in hidden:
                continue
            tid = teams.find_past(r.get("club", ""))
            if r["rank"] > LEADERS_TOP and (tid is None or tid in seen):
                continue
            seen.add(tid)
            row = {"rank": r["rank"], "name": r["name"], "role": r.get("role", ""), "number": r.get("number"),
                   **{k: r.get(k) for k in fields}}
            if tid:
                row["team"] = tid
                kit = tid
            else:
                row["club"] = r.get("club", "")
                club = past.get(norm(row["club"]), {})
                if club.get("logo"):
                    row["logo"] = club["logo"]
                kit = club.get("kit")
            if kit in kits:
                row["kit"] = kit
            rows.append(row)
        out["categories"][cat] = rows
    return out

# ---------- сборка ----------


BOT_LINK = "https://t.me/rhl_u21_bot"
APP_LINK = "https://t.me/rhl_u21_bot/myapp"


def links(env=os.environ) -> dict[str, str]:
    """Ссылки на бота и мини-апп в Telegram (ADR-004). Переменные окружения заменяют ссылки по умолчанию."""
    out = {"bot": (env.get("BOT_LINK") or BOT_LINK).strip(), "app": (env.get("APP_LINK") or APP_LINK).strip()}
    return {k: v.rstrip("/") for k, v in out.items() if v.startswith("https://t.me/")}


def build(teams: Teams, raw: list[rhockey.RawGame], results: league.Results,
          hidden: set[int] = frozenset()) -> tuple[dict, list[str], dict[str, dict]]:
    """league.json, непривязанные протоколы и разборы сыгранных матчей по id матча."""
    games = merge_calendar(teams, raw, official_games(teams))
    protocols: dict[str, dict] = {}
    unmatched = attach_results(games, teams, results, protocols, hidden)
    names = {t["id"]: t["name"] for t in teams.all}
    details = {g["id"]: match_detail(g, protocols[g["id"]], names, hidden) for g in games if g["id"] in protocols}
    data = {
        "season": "2026/27",
        "league": "РХЛ — Первенство России U21",
        "updated": datetime.now(TZ).isoformat(timespec="minutes"),
        "sources": {
            "calendar": "ФХР (официально) для «Рязань-ВДВ», r-hockey.ru (неофициально) для остальных",
            "results": "протоколы лиги",
        },
        "links": links(),
        # mascot — проводник онбординга: имя и фразы (ADR-011); colors — цвета формы
        "teams": [{k: t[k] for k in ("id", "abbr", "name", "city", "conf", "logo", "colors", "mascot") if k in t} for t in teams.all],
        "games": games,
        "standings": standings(teams, games),
        # форма клубов для стикеров игроков: составы в разборе матча и лидеры (ADR-009)
        "kits": load_kits(),
    }
    return data, unmatched, details


def load_channels(path: Path = CHANNELS_FILE) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))["channels"]
    except (FileNotFoundError, ValueError, KeyError):
        return []


def load_posts(path: Path = POSTS_FILE) -> dict:
    """Посты каналов по адресу канала. Нет файла — лист собирается из своих данных."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))["channels"]
    except (FileNotFoundError, ValueError, KeyError):
        return {}


def write_feeds(out_dir: Path, teams: Teams, data: dict, h2h: dict, history: list[dict], recaps: dict[str, dict],
                now: datetime, channels: list[dict], posts: dict, top: dict | None = None) -> int:
    """Лист дня «Главной» на каждый клуб: webapp/data/feed/<клуб>.json (ADR-015). Возвращает число постов."""
    feed_dir = out_dir / "feed"
    feed_dir.mkdir(exist_ok=True)
    clubs = [t["id"] for t in teams.all]
    shown = 0
    for club in clubs:
        sheet = feed.build(club, now, clubs=clubs, games=data["games"], standings=data["standings"], h2h=h2h,
                           history=history, recaps=recaps, channels=channels, posts=posts, leaders=top)
        shown += sum(1 for c in sheet["cards"] if c["kind"] == "post")
        (feed_dir / f"{club}.json").write_text(json.dumps(sheet, ensure_ascii=False, separators=(",", ":")),
                                               encoding="utf-8")
    return shown


def main() -> None:
    ap = argparse.ArgumentParser(description="Собрать webapp/data/league.json")
    ap.add_argument("--results", type=Path, default=league.RESULTS_FILE)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--now", type=datetime.fromisoformat,
                    help="момент сборки листа «Главной» с поясом, например 2026-10-03T12:00+03:00")
    args = ap.parse_args()
    if args.now and args.now.tzinfo is None:
        ap.error("--now нужен с поясом: 2026-10-03T12:00+03:00")
    now = args.now or datetime.now(TZ)
    teams = load_teams()
    raw = asyncio.run(rhockey.fetch_season())
    data, unmatched, details = build(teams, raw, league.load_results(args.results), load_hidden())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    history, past_protocols = load_history(), load_history_protocols()
    h2h = head_to_head(data["games"], history, set(past_protocols))
    wanted = {m["id"] for pair in h2h.values() for m in pair["last"] if m.get("id", "").startswith("h")}
    # «В этот день» на «Главной»: прошлые матчи сегодняшнего числа с протоколом получают разбор
    wanted |= {past_id(h) for h in history if h.get("game_id") and h["date"][4:] == now.date().isoformat()[4:]}
    names = {t["id"]: t["name"] for t in teams.all}
    details.update(past_recaps(history, past_protocols, wanted, names, load_hidden()))
    mark_stories(h2h, details)
    (args.out.parent / "h2h.json").write_text(json.dumps(h2h, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    matches = args.out.parent / "matches"
    matches.mkdir(exist_ok=True)
    for old in matches.glob("*.json"):   # матч мог пропасть из календаря — не оставляем чужой файл
        if old.stem not in details:
            old.unlink()
    for gid, d in details.items():
        (matches / f"{gid}.json").write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    top = leaders(teams, load_leaders(), load_hidden())
    if top:
        (args.out.parent / "leaders.json").write_text(json.dumps(top, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    posts = write_feeds(args.out.parent, teams, data, h2h, history, details, now, load_channels(), load_posts(), top)
    print(f"Лист «Главной»: {len(teams.all)} клубов, постов каналов в листах: {posts}")
    played = sum(1 for g in data["games"] if g.get("score"))
    print(f"Матчей: {len(data['games'])}, сыграно: {played} → {args.out}")
    for u in unmatched:
        print("Протокол не привязан к матчу:", u)


if __name__ == "__main__":
    main()

"""Собирает webapp/data/league.json для мини-аппа: команды, матчи, результаты, таблица (ADR-002).

Время матча — всегда московское (ADR-019, раздел 2): `time` «HH:MM», `start` ISO с +03:00, `local` —
местное время арены, если её пояс (`tz` хозяев в teams.json) не московский. Источники по старшинству:
schedule.json сервера, протокол лиги (в нём местное время), пост клуба в день игры (matchday.py)."""
import argparse
import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import feed
import league
import matchday
import replay
import rhl_media
import rhl_site
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
SCHEDULE_FILE = BASE / "schedule.json"       # время матчей с сервера (ADR-019, раздел 5), кладёт шаг Pages
REPLAYS_FILE = BASE / "replays.json"         # повторы голов с сервера (ADR-027), кладёт шаг Pages
EVENTS_FILE = BASE / "channel_events.json"   # лента матчей из постов каналов: копится между запусками (кэш Pages)
EVENTS_DAYS = 3                              # и держится три дня
MOSCOW = "Europe/Moscow"
HIDDEN_NAME = "Игрок скрыт"
H2H_LAST = 5
# Ключ игрока в данных мини-аппа (ADR-030): id на сайте лиги туда не выгружаем (ADR-008, пункт 6), вместо него —
# HMAC от id с секретом задания Pages. Ключ один и тот же от сборки к сборке и по нему не найти страницу у лиги.
PLAYER_SALT = os.environ.get("PLAYER_SALT") or "rhl-player-key"


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
        self.tz = {t["id"]: t.get("tz") or MOSCOW for t in teams}   # пояс домашней арены (ADR-019)

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

# ---------- время начала (ADR-019) ----------


def set_start(g: dict, start: datetime, zone: str) -> None:
    """Начало матча: time и start по Москве, local — местное время арены, если её пояс не московский."""
    msk = start.astimezone(TZ)
    local = msk.astimezone(ZoneInfo(zone))
    g["time"] = msk.strftime("%H:%M")
    g["start"] = msk.isoformat(timespec="seconds")
    if local.utcoffset() != msk.utcoffset():
        g["local"] = local.strftime("%H:%M")
    else:
        g.pop("local", None)


def local_start(day: str | None, hm: str | None, zone: str) -> datetime | None:
    """Дата и «18:06» в поясе zone → начало с поясом. Не разобралось — None."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", (hm or "").strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    try:
        d = date.fromisoformat(day or "")
    except ValueError:
        return None
    return datetime.combine(d, time(int(m.group(1)), int(m.group(2))), ZoneInfo(zone))


def load_schedule(path: Path = SCHEDULE_FILE) -> list[dict]:
    """Матчи из live/schedule.json сервера. Нет файла или он битый — пусто: время из других источников."""
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))["games"]
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def schedule_key(s: dict) -> tuple[date, str, str] | None:
    """Дата, хозяева и гости строки schedule.json: из полей, а нет их — из ключа «<дата>|<хозяева>|<гости>»."""
    parts = str(s.get("key") or "").split("|")
    if len(parts) != 3:
        parts = ["", "", ""]
    day, home, away = s.get("date") or parts[0], s.get("home") or parts[1], s.get("away") or parts[2]
    try:
        return date.fromisoformat(str(day)), str(home), str(away)
    except ValueError:
        return None


def schedule_start(s: dict, day: date) -> datetime | None:
    """Начало из строки schedule.json: start с поясом, а нет его — time по Москве в день матча."""
    if isinstance(s.get("start"), str):
        try:
            dt = datetime.fromisoformat(s["start"])
            return dt if dt.tzinfo else dt.replace(tzinfo=TZ)   # по контракту там +03:00; без пояса — Москва
        except ValueError:
            pass
    return local_start(day.isoformat(), s.get("time"), MOSCOW)


def apply_schedule(games: list[dict], schedule: list[dict], zones: dict[str, str]) -> int:
    """Время и ссылка на онлайн из schedule.json — по ключу «<дата>|<хозяева>|<гости>». Дата не сошлась —
    ±1 день, если у тех же хозяев и гостей в эти дни один матч и он ещё не занят. Возвращает число матчей."""
    by_key = {(g["date"], g["home"], g["away"]): g for g in games}
    by_pair: dict[tuple[str, str], list[dict]] = {}
    for g in games:
        by_pair.setdefault((g["home"], g["away"]), []).append(g)
    matched: dict[str, tuple[dict, dict, date]] = {}
    fuzzy = []
    for s in schedule:
        k = schedule_key(s)
        if not k:
            continue
        g = by_key.get((k[0].isoformat(), k[1], k[2]))
        if g:
            matched[g["id"]] = (g, s, k[0])
        else:
            fuzzy.append((k, s))
    for (day, home, away), s in fuzzy:
        near = [g for g in by_pair.get((home, away), []) if abs((date.fromisoformat(g["date"]) - day).days) <= 1]
        if len(near) == 1 and near[0]["id"] not in matched:
            matched[near[0]["id"]] = (near[0], s, day)
    for g, s, day in matched.values():
        start = schedule_start(s, day)
        if start:
            set_start(g, start, zones.get(g["home"], MOSCOW))
            g["src_time"] = "schedule"
        online = s.get("online")
        if isinstance(online, str) and re.fullmatch(r"https://[^\s\"'<>]{4,300}", online):
            g["online"] = online
    return len(matched)


def load_replays(path: Path = REPLAYS_FILE) -> dict:
    """Повторы голов из live/replays.json сервера (ADR-027): ключ матча → запись. Нет файла — без повторов."""
    try:
        games = json.loads(path.read_text(encoding="utf-8"))["games"]
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return {}
    return games if isinstance(games, dict) else {}


def apply_replays(games: list[dict], replays: dict) -> int:
    """Ссылка на повтор (`replay`) у гола матча: ключ «<дата>|<хозяева>|<гости>», гол — по счёту после него.
    Счёт уникален в матче и одинаков у службы live и у протокола. Матч без голов протокола (протокол ещё не
    пришёл) получает `replays` — счёт → ссылка: по нему мини-апп ставит «Повтор» у гола в ленте матча
    (ADR-028, раздел 4). Возвращает число голов с повтором."""
    n = 0
    for g in games:
        links = replay.by_score(replays.get(f"{g['date']}|{g['home']}|{g['away']}") or {})
        if not links:
            continue
        if not any(x.get("period") != "РБ" for x in g.get("goals") or []):
            g["replays"] = links
            n += len(links)
            continue
        for x in g.get("goals") or []:
            url = links.get(x.get("score")) if x.get("period") != "РБ" else None
            if url:
                x["replay"] = url
                n += 1
    return n


def apply_matchday(games: list[dict], teams: "Teams", channels: list[dict], posts: dict) -> int:
    """«Смотреть» из постов клубов в день игры; время из поста — только если других источников нет
    (ADR-019, раздел 2). Возвращает число матчей, где что-то нашлось."""
    found = matchday.attach(games, teams.all, channels, posts)
    for g in games:
        got = found.get(g["id"])
        if not got:
            continue
        if got.get("watch"):
            g["watch"] = got["watch"]
        if got.get("start") and not g.get("time"):
            set_start(g, got["start"], teams.tz.get(g["home"], MOSCOW))
    return len(found)


def apply_media(games: list[dict], store: dict) -> int:
    """«Смотреть» от лиги (ADR-019, раздел 7): видео из вкладки «Видео» сайта лиги, а пока его нет — сама вкладка,
    если трансляция объявлена, — первой кнопкой. Ссылки из постов клубов (apply_matchday) — после неё, без
    повторов того же ролика, всего не больше трёх. Матч сайта — по league_url, его ставит apply_site."""
    site_games = (store or {}).get("games", {})
    n = 0
    for g in games:
        m = rhl_site.LINK_RE.search(g.get("league_url") or "")
        first = rhl_media.watch_item(site_games.get(m.group(2))) if m else None
        if not first:
            continue
        same = matchday.same_video(first["url"])
        rest = [w for w in g.get("watch", []) if matchday.same_video(w["url"]) != same]
        g["watch"] = [first, *rest][:matchday.WATCH_MAX]
        n += 1
    return n


def name_key(name: str) -> frozenset[str]:
    """Имя без порядка слов и инициалов: «Скачков Евгений А.» и «Евгений Скачков» — один ключ."""
    return frozenset(w for w in re.split(r"[\s.]+", name.lower().replace("ё", "е")) if len(w) > 1)


def apply_goal_authors(games: list[dict], protocols: dict[str, dict], channels: list[dict],
                       hidden: set[int] = frozenset()) -> int:
    """Авторы голов по ходу матча из постов клубов (ADR-026): событию ленты с «Шайбу забросил Даниил Нуреев
    🦅 0:2» — `goal` с командой, счётом и именем. Мини-апп ставит имя в гол живого с тем же счётом и командой.

    Чей гол: игрок есть в составе ровно одной из команд по протоколам сезона — его команда и имя как в протоколе;
    нет в составах — команда канала клуба (пост лиги без состава не берём). Счёт должен быть возможен для этой
    команды: у гостей «2:0» своим быть не может — так ловим клуб, который пишет свой счёт первым. Скрытые по
    просьбе (ADR-007) — «Игрок скрыт». Возвращает число голов с автором."""
    rosters: dict[str, dict[frozenset, dict]] = {}
    for g in games:
        p = protocols.get(g["id"]) or {}
        # составы в протоколах сайта бывают из одних вратарей — добираем авторов, ассистентов и удалённых
        seen = [(k.get("team"), k.get("player")) for k in p.get("lineups", [])]
        seen += [(x.get("team"), pl) for x in p.get("goals", []) for pl in (x.get("author"), *(x.get("assists") or ()))]
        seen += [(x.get("team"), x.get("player")) for x in p.get("penalties", [])]
        for side, pl in seen:
            if side in ("home", "away") and isinstance(pl, dict) and pl.get("name"):
                rosters.setdefault(g[side], {})[name_key(pl["name"])] = pl
    hidden_keys = {key for r in rosters.values() for key, pl in r.items() if pl.get("id") in hidden}
    # в протоколах «Фамилия Имя»: имена оттуда, чтобы «Ратмир Тиняев» из поста стал «Тиняев Ратмир», как на сайте
    first = {pl["name"].split()[1].lower().replace("ё", "е") for r in rosters.values() for pl in r.values()
             if len(pl["name"].split()) >= 2}

    def site_order(name: str) -> str:
        w = name.split()
        lo = [x.lower().replace("ё", "е") for x in w]
        return f"{w[1]} {w[0]}" if len(w) == 2 and lo[0] in first and lo[1] not in first else name
    club_of = {f"t.me/{c['handle']}": c.get("club") for c in channels if c.get("handle")}
    n = 0
    for g in games:
        for e in g.get("events") or []:
            e.pop("goal", None)
            got = matchday.goal_author(e.get("text") or "") if e.get("kind") == "text" else None
            if not got:
                continue
            key = name_key(got["name"])
            sides = [s for s in ("home", "away") if key in rosters.get(g[s], {})]
            club = club_of.get(e.get("src"))
            if len(sides) == 1:
                side, name = sides[0], shown(rosters[g[sides[0]]][key], hidden)
            elif not sides and club in (g["home"], g["away"]):
                side, name = ("home" if club == g["home"] else "away"), site_order(got["name"])
            else:
                continue
            h, a = (int(x) for x in got["score"].split(":"))
            if (h if side == "home" else a) < 1:
                continue
            e["goal"] = {"team": side, "score": got["score"], "name": HIDDEN_NAME if key in hidden_keys else name}
            n += 1
    return n


def apply_channel_events(games: list[dict], teams: "Teams", channels: list[dict], posts: dict,
                         cache: dict | None = None, now: datetime | None = None) -> int:
    """Лента матча из постов каналов клубов и лиги (matchday.match_events) → `events` у матча в league.json.
    cache — ленты прошлых запусков по ключу «<дата>|<хозяева>|<гости>» (channel_events.json): t.me/s отдаёт
    только последние ~20 постов канала, а клуб по ходу матча пишет больше. Возвращает число матчей с лентой."""
    found = matchday.match_events(games, teams.all, channels, posts)
    store = cache.setdefault("games", {}) if cache is not None else {}
    n = 0
    for g in games:
        key = f"{g['date']}|{g['home']}|{g['away']}"
        events = matchday.merge_feed(store.get(key) or [], found.get(g["id"]) or [], channels, posts)
        if events:
            store[key] = g["events"] = events
            n += 1
        else:
            store.pop(key, None)
    if now is not None:
        oldest = (now.astimezone(TZ).date() - timedelta(days=EVENTS_DAYS)).isoformat()
        for key in [k for k in store if k[:10] < oldest]:
            del store[key]
    return n


def load_events(path: Path = EVENTS_FILE) -> dict:
    """Кэш ленты матчей из каналов. Нет файла или он битый — с чистого листа."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data.get("games"), dict) else {"games": {}}
    except (FileNotFoundError, ValueError, AttributeError):
        return {"games": {}}

def site_rows(store: dict, teams: "Teams") -> list[dict]:
    """Матчи из rhl_site.json с id команд из teams.json. Не узнали команду — матч пропускаем с запиской."""
    rows = []
    for g in (store or {}).get("games", {}).values():
        home, away = teams.find(g.get("home") or ""), teams.find(g.get("away") or "")
        if not home or not away or not g.get("start"):
            if g.get("home"):
                print(f"Сайт РХЛ: не узнал матч {g.get('id')} {g.get('home')} — {g.get('away')}")
            continue
        try:
            start = datetime.fromisoformat(g["start"])
        except ValueError:
            continue
        rows.append({**g, "home_id": home, "away_id": away, "start_dt": start if start.tzinfo else start.replace(tzinfo=TZ)})
    return rows


def apply_site(games: list[dict], store: dict, teams: "Teams", protocols: dict[str, dict] | None = None,
               hidden: set[int] = frozenset()) -> int:
    """Сайт лиги (ADR-019, раздел 2): номер матча, начало по Москве, ссылки матч-центра и счёт
    сыгранного матча. Протокол с сайта (`report`, rhl_protocol.py) главнее счёта ленты: счёт с периодами
    и решением, голы и разбор — как у протокола results.json (ADR-008); protocols, если передан, собирает
    его по id матча. Нет протокола — счёт из ленты. Матч сайта, которого нет в календаре r-hockey,
    добавляется: лига — источник истины по календарю (CLAUDE.md). Возвращает число матчей с сайта."""
    by_key = {(g["date"], g["home"], g["away"]): g for g in games}
    by_pair: dict[tuple[str, str], list[dict]] = {}
    for g in games:
        by_pair.setdefault((g["home"], g["away"]), []).append(g)
    used: set[str] = set()
    n = 0
    for r in sorted(site_rows(store, teams), key=lambda r: r["start_dt"]):
        day = r["start_dt"].astimezone(TZ).date()
        g = by_key.get((day.isoformat(), r["home_id"], r["away_id"]))
        if g is None or g["id"] in used:
            near = [x for x in by_pair.get((r["home_id"], r["away_id"]), [])
                    if x["id"] not in used and abs((date.fromisoformat(x["date"]) - day).days) <= 1]
            g = near[0] if len(near) == 1 else None
        if g is None:
            g = {"id": f"r{r['id']}", "n": r.get("n"), "date": day.isoformat(), "home": r["home_id"],
                 "away": r["away_id"], "official": True}
            games.append(g)
        used.add(g["id"])
        n += 1
        g["official"] = True
        if r.get("n"):
            g["n"] = r["n"]
        if not g.get("start") or g.get("src_time") != "schedule":
            set_start(g, r["start_dt"], teams.tz.get(g["home"], MOSCOW))
        base = f"{rhl_site.SITE}/matchcenter/{r['t']}/{r['id']}/"
        g["league_url"] = base
        g.setdefault("online", base + "live/")
        if r.get("arena"):
            g["arena"] = r["arena"]
        report = r.get("report")
        if isinstance(report, dict) and "home_score" in report:
            fill_result(g, report, hidden, teams.tz, timed=False)   # начало уже по календарю сайта или schedule
            g.pop("score_src", None)
            if protocols is not None:
                protocols[g["id"]] = report
        score = r.get("score")
        if r.get("status") == "final" and isinstance(score, list) and len(score) == 2 and not g.get("score"):
            g["score"] = {"home": int(score[0]), "away": int(score[1]), "decision": r.get("decision"),
                          "periods": []}
            g["score_src"] = "rhl.fhr.ru"
        if g.get("score_src") == "rhl.fhr.ru":
            # до протокола — авторы голов с карточки матча сайта, минутой «29'» (скрытых — без имени)
            sg = [{"team": side, "min": x["min"], "no": x.get("no"),
                   "name": HIDDEN_NAME if x.get("player") in hidden else x["name"]}
                  for side, key in (("home", "home"), ("away", "away"))
                  for x in ((r.get("goals") or {}).get(key) or []) if isinstance(x, dict) and x.get("min") is not None]
            if sg:
                g["site_goals"] = sorted(sg, key=lambda x: x["min"])
        if r.get("status") == "final" or g.get("score"):
            g["protocol"] = base + "protocol/"
        if not g.get("score"):
            lv = rhl_site.live_state(r)
            if lv:
                lv["seen"] = lv["seen"] or (store or {}).get("updated")
                g["live"] = lv
    games.sort(key=lambda g: (g["date"], g["id"]))
    return n

# ---------- результаты ----------


def player_key(pid, salt: str | None = None) -> str | None:
    """Ключ игрока для мини-аппа (ADR-030): 10 знаков HMAC-SHA256 от id игрока на сайте лиги. Нет id — None."""
    if not isinstance(pid, int) or isinstance(pid, bool):
        return None
    key = (salt if salt is not None else PLAYER_SALT).encode()
    return hmac.new(key, str(pid).encode(), hashlib.sha256).hexdigest()[:10]


def shown_key(player: dict | None, hidden: set[int] = frozenset()) -> str | None:
    """Ключ игрока, если его можно показать: скрытому по просьбе ключа нет — ни страницы, ни клипов."""
    if not player or player.get("id") in hidden:
        return None
    return player_key(player.get("id"))


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


def fill_result(g: dict, p: dict, hidden: set[int] = frozenset(), zones: dict[str, str] | None = None,
                *, timed: bool = True) -> None:
    """Счёт, голы и сведения из протокола — в матч, как их ждёт мини-апп. Время в протоколе nmhl.fhr.ru —
    местное время арены (ADR-001): по поясу хозяев из zones оно становится московским. У протокола
    rhl.fhr.ru пояс записан в `zone` (Москва) — время не сдвигается второй раз. timed=False — начало не
    трогаем: его уже поставил источник главнее протокола (schedule.json, календарь сайта лиги)."""
    g["n"] = g.get("n") or p.get("n")
    zone = (zones or {}).get(g.get("home"), MOSCOW)
    start = local_start(p.get("date") or g.get("date"), p.get("time"), p.get("zone") or zone) if timed else None
    if start:
        set_start(g, start, zone)
    else:
        g.setdefault("time", None)
    g["attendance"] = p.get("attendance")
    g["score"] = {"home": p["home_score"], "away": p["away_score"], "decision": p["decision"],
                  "periods": p["periods"]}
    gk = goalies_of(p)
    g["goals"] = [goal_row(x, gk, hidden) for x in p["goals"]]


def goal_row(x: dict, gk: set[tuple[str, int]], hidden: set[int] = frozenset()) -> dict:
    """Гол протокола для мини-аппа. `pk` — ключ автора, `apk` — ключи ассистентов по порядку (None — скрыт или
    без id): по ним гол попадает на страницу игрока (ADR-030)."""
    row = {"period": x["period"], "time": x["time"], "team": x["team"], "score": x["score"],
           "strength": x["strength"], "author": shown(x["author"], hidden),
           "assists": [shown(a, hidden) for a in x["assists"]],
           **sticker(x["author"], x["team"], gk, hidden)}
    pk = shown_key(x["author"], hidden)
    if pk:
        row["pk"] = pk
    apk = [shown_key(a, hidden) for a in x["assists"]]
    if any(apk):
        row["apk"] = apk
    return row


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
            fill_result(g, p, hidden, teams.tz)
            if protocols is not None:
                protocols[g["id"]] = p
    return unmatched


def past_id(h: dict) -> str:
    """Id прошлого матча в мини-аппе: h + номер протокола на сайте лиги."""
    return f"h{h['game_id']}"


def past_recaps(history: list[dict], protocols: dict[str, dict], wanted: set[str], teams: dict[str, str],
                hidden: set[int] = frozenset(), zones: dict[str, str] | None = None) -> dict[str, dict]:
    """Разборы прошлых матчей из «Последних встреч». Матча нет в league.json — он лежит в файле целиком.
    zones — пояса арен по id команды: время из протокола переводится в московское."""
    out = {}
    for h in history:
        if not h.get("game_id") or past_id(h) not in wanted or str(h["game_id"]) not in protocols:
            continue
        g = {"id": past_id(h), "n": None, "date": h["date"], "home": h["home"], "away": h["away"],
             "season": h["season"], "stage": h["stage"]}
        p = protocols[str(h["game_id"])]
        fill_result(g, p, hidden, zones)
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
        pk = player_key(k["player"].get("id"))
        if pk:
            row["pk"] = pk   # страница игрока из состава (ADR-030)
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

def goal_entry(g: dict, x: dict) -> dict:
    """Гол для ленты голов клуба и игрока (ADR-030): матч, соперник, счёт, время, кто забил и отдал, повтор."""
    side = x["team"]
    e = {"game": g["id"], "date": g["date"], "club": g[side], "opp": g["away" if side == "home" else "home"],
         "home": side == "home", "score": x["score"], "period": x["period"], "time": x["time"],
         "strength": x.get("strength", ""), "author": x["author"], "assists": x.get("assists", [])}
    for k in ("pk", "apk", "no", "replay", "clip"):
        if x.get(k):
            e[k] = x[k]
    return e


def catalog(games: list[dict], protocols: dict[str, dict], hidden: set[int] = frozenset()) -> tuple[dict, dict]:
    """Каталог голов сезона (ADR-030): голы каждого клуба и страницы игроков, свежие сверху. Привязка к игроку —
    по ключу из протокола, который сборка перечитывает каждый раз: поправила лига автора — гол у другого игрока.
    Буллиты не входят. Игрок — всякий, кто есть в протоколах сезона (составы, авторы, ассистенты), кроме скрытых:
    его страница открывается и из состава, даже без голов. (клуб → голы, ключ игрока → игрок)."""
    clubs: dict[str, list[dict]] = {}
    players: dict[str, dict] = {}
    for g in sorted(games, key=lambda g: (g["date"], g["id"])):
        p = protocols.get(g["id"])
        if not p:
            continue
        seen = [(k.get("team"), k.get("player"), k.get("role")) for k in p.get("lineups", [])]
        seen += [(x.get("team"), pl, None) for x in p.get("goals", []) for pl in (x.get("author"), *(x.get("assists") or ()))]
        for side, pl, role in seen:
            pk = shown_key(pl, hidden) if side in ("home", "away") else None
            if not pk:
                continue
            me = players.setdefault(pk, {"pk": pk, "name": pl["name"], "goals": []})
            me.update(name=pl["name"], club=g[side])          # последний матч — нынешний клуб и номер
            if pl.get("number") is not None:
                me["no"] = pl["number"]
            if role:
                me["role"] = role
        for x in g.get("goals") or []:
            if x.get("period") == "РБ":
                continue
            e = goal_entry(g, x)
            clubs.setdefault(e["club"], []).append(e)
            if x.get("pk") in players:
                players[x["pk"]]["goals"].append({**e, "as": "goal"})
            for a in dict.fromkeys(k for k in x.get("apk") or [] if k):
                if a in players:
                    players[a]["goals"].append({**e, "as": "assist"})
    for rows in clubs.values():
        rows.reverse()
    for me in players.values():
        me["goals"].reverse()
    return clubs, players


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
    # id — только у матча с протоколом: по нему открывается разбор. Счёт с сайта лиги до протокола —
    # строка без перехода
    past += [{"date": g["date"], "home": g["home"], "away": g["away"],
              **({"id": g["id"]} if "goals" in g else {}),
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
            pk = player_key(r.get("id"))
            if pk:
                row["pk"] = pk   # страница игрока из лидеров (ADR-030)
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
          hidden: set[int] = frozenset(), *, schedule: list[dict] = (), channels: list[dict] = (),
          posts: dict | None = None, site: dict | None = None,
          events: dict | None = None, replays: dict | None = None,
          catalog_out: dict | None = None) -> tuple[dict, list[str], dict[str, dict]]:
    """league.json, непривязанные протоколы и разборы сыгранных матчей по id матча.

    schedule — строки schedule.json сервера, channels и posts — каналы клубов и их посты: время начала,
    онлайн, «Смотреть» и лента матча (ADR-019), events — кэш этой ленты (channel_events.json), его сборка
    дополняет, replays — повторы голов (ADR-027). Всё необязательно: без них у матча только время из протокола.
    catalog_out, если передан, получает каталог голов: `clubs` и `players` (ADR-030)."""
    games = merge_calendar(teams, raw, official_games(teams))
    protocols: dict[str, dict] = {}
    unmatched = attach_results(games, teams, results, protocols, hidden)
    apply_schedule(games, list(schedule), teams.tz)        # главнее протокола: онлайн лиги, время по Москве
    apply_site(games, site or {}, teams, protocols, hidden)   # сайт лиги: номер, время, протокол или счёт ленты
    for g in games:
        g.pop("src_time", None)
    apply_matchday(games, teams, list(channels), posts or {})
    apply_media(games, site or {})                         # «Смотреть» от лиги — первой кнопкой
    apply_channel_events(games, teams, list(channels), posts or {}, events, datetime.now(TZ))
    apply_goal_authors(games, protocols, list(channels), hidden)   # авторы голов по ходу из постов (ADR-026)
    apply_replays(games, replays or {})                    # последним: голы уже на месте (ADR-027)
    names = {t["id"]: t["name"] for t in teams.all}
    details = {g["id"]: match_detail(g, protocols[g["id"]], names, hidden) for g in games if g["id"] in protocols}
    if catalog_out is not None:
        catalog_out["clubs"], catalog_out["players"] = catalog(games, protocols, hidden)
    data = {
        "season": "2026/27",
        "league": "РХЛ — Первенство России U21",
        "updated": datetime.now(TZ).isoformat(timespec="minutes"),
        "sources": {
            "calendar": "ФХР (официально) для «Рязань-ВДВ», r-hockey.ru (неофициально) для остальных",
            "results": "протоколы лиги; до протокола — счёт с сайта лиги rhl.fhr.ru",
        },
        "links": links(),
        # mascot — проводник онбординга: имя и фразы (ADR-011); colors — цвета формы; tz — пояс арены (ADR-019)
        "teams": [{k: t[k] for k in ("id", "abbr", "name", "city", "tz", "conf", "logo", "colors", "mascot") if k in t}
                  for t in teams.all],
        "games": games,
        "standings": standings(teams, games),
        # форма клубов для стикеров игроков: составы в разборе матча и лидеры (ADR-009)
        "kits": load_kits(),
    }
    return data, unmatched, details


def write_catalog(out_dir: Path, teams: Teams, season: str, cat: dict, updated: str) -> None:
    """highlights/<клуб>.json — у каждого из 26 клубов, даже без голов; players/<ключ>.json — у каждого игрока
    сезона. Файлы игроков, которых больше нет (скрыт по просьбе, лига убрала из протокола), удаляются (ADR-030)."""
    hl = out_dir / "highlights"
    hl.mkdir(parents=True, exist_ok=True)
    for t in teams.all:
        body = {"club": t["id"], "season": season, "updated": updated, "goals": cat["clubs"].get(t["id"], [])}
        (hl / f"{t['id']}.json").write_text(json.dumps(body, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    pl = out_dir / "players"
    pl.mkdir(parents=True, exist_ok=True)
    for old in pl.glob("*.json"):
        if old.stem not in cat["players"]:
            old.unlink()
    for pk, me in cat["players"].items():
        body = {**me, "season": season, "updated": updated}
        (pl / f"{pk}.json").write_text(json.dumps(body, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


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
    stream = feed.build_stream(now, games=data["games"], history=history, recaps=recaps, channels=channels, posts=posts,
                               h2h=h2h, leaders=top, clubs=clubs)
    (feed_dir / "stream.json").write_text(json.dumps(stream, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
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
    raw = rhockey.season()   # не ответил — календарь прошлой сборки
    channels, posts = load_channels(), load_posts()
    events = load_events()
    cat: dict = {}
    data, unmatched, details = build(teams, raw, league.load_results(args.results), load_hidden(),
                                     schedule=load_schedule(), channels=channels, posts=posts,
                                     site=rhl_site.load_store(), events=events, replays=load_replays(),
                                     catalog_out=cat)
    EVENTS_FILE.write_text(json.dumps(events, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    history, past_protocols = load_history(), load_history_protocols()
    h2h = head_to_head(data["games"], history, set(past_protocols))
    wanted = {m["id"] for pair in h2h.values() for m in pair["last"] if m.get("id", "").startswith("h")}
    # «В этот день» на «Главной»: прошлые матчи сегодняшнего числа с протоколом получают разбор
    wanted |= {past_id(h) for h in history if h.get("game_id") and h["date"][4:] == now.date().isoformat()[4:]}
    names = {t["id"]: t["name"] for t in teams.all}
    details.update(past_recaps(history, past_protocols, wanted, names, load_hidden(), teams.tz))
    mark_stories(h2h, details)
    (args.out.parent / "h2h.json").write_text(json.dumps(h2h, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    matches = args.out.parent / "matches"
    matches.mkdir(exist_ok=True)
    for old in matches.glob("*.json"):   # матч мог пропасть из календаря — не оставляем чужой файл
        if old.stem not in details:
            old.unlink()
    for gid, d in details.items():
        (matches / f"{gid}.json").write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    write_catalog(args.out.parent, teams, data["season"], cat, data["updated"])
    top = leaders(teams, load_leaders(), load_hidden())
    if top:
        (args.out.parent / "leaders.json").write_text(json.dumps(top, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    shown = write_feeds(args.out.parent, teams, data, h2h, history, details, now, channels, posts, top)
    print(f"Лист «Главной»: {len(teams.all)} клубов, постов каналов в листах: {shown}")
    played = sum(1 for g in data["games"] if g.get("score"))
    print(f"Матчей: {len(data['games'])}, сыграно: {played} → {args.out}")
    count = lambda k: sum(1 for g in data["games"] if g.get(k))   # noqa: E731
    print(f"Время начала: {count('start')}, онлайн: {count('online')}, «Смотреть»: {count('watch')}, "
          f"лента из каналов: {count('events')} (ADR-019)")
    print(f"Голов с повтором: {sum(1 for g in data['games'] for x in g.get('goals') or [] if x.get('replay'))}, "
          f"до протокола: {sum(len(g.get('replays') or {}) for g in data['games'])} (ADR-027, ADR-028)")
    print(f"Каталог голов: {sum(len(v) for v in cat['clubs'].values())} голов, игроков: {len(cat['players'])}"
          + ("" if os.environ.get("PLAYER_SALT") else " — ключи игроков без секрета PLAYER_SALT") + " (ADR-030)")
    for u in unmatched:
        print("Протокол не привязан к матчу:", u)


if __name__ == "__main__":
    main()

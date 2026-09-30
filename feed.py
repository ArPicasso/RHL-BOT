"""Лист дня на «Главной» (ADR-015): карточки из своих данных и превью постов каналов, файл на клуб.

Лист персонален только по клубу и времени, поэтому собирается заранее, при сборке Pages. Правила
здесь, клиент только прячет просроченное и скрытые каналы. Без сети и без чтения файлов: всё
приходит аргументами, поэтому правила проверяются `unittest` на выдуманных данных.

Правила ADR-015:
- своих данных в листе не меньше 60%, постов каналов — не больше 40%, два поста подряд не идут,
  первая карточка — своя;
- от одного канала — не больше двух постов и не больше одного в первых восьми карточках, от чужого
  клуба (не свой и не соперник серии) — один пост;
- слот «клуб по ротации» обходит клубы по кругу со сдвигом (номер дня + индекс клуба): за 24 дня
  каждый клуб побывает в листе каждого. Активность канала частоту не покупает;
- посты всех публичных каналов из channels.json, кроме отказавшихся (`optout`), свежее 48 часов,
  трансляция матча по минутам — только своего клуба и не старше 3 часов;
- лист не длиннее 12 карточек.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Moscow")

OWN_SHARE = 0.6
MAX_CARDS = 12
FIRST = 8
PER_CHANNEL = 2
POST_TTL = timedelta(hours=48)
LIVE_TTL = timedelta(hours=3)
SERIES_DAYS = 7        # соперник серии: матч с ним в ближайшую неделю
H2H_DAYS = 3           # очные встречи и прошлая встреча — за три дня до матча
PAUSE_DAYS = 5         # своего матча нет пять дней и больше — пауза
SHOWN_KINDS = ("club", "academy", "system", "league")
KIND_ORDER = {k: i for i, k in enumerate(SHOWN_KINDS)}
# «Сюжет дня»: самая яркая история вчерашних матчей — камбэк > буллиты > овертайм > серия шайб > «сухарь»
STORY_RANK = ("Камбэк", "буллиты", "овертайм", "подряд", "Сухой матч")


def pair_key(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


def day_start(d: date) -> datetime:
    return datetime.combine(d, time(0, 0), TZ)


def iso(dt: datetime) -> str:
    return dt.astimezone(TZ).isoformat(timespec="minutes")


def d(s: str) -> date:
    return date.fromisoformat(s[:10])


def other(g: dict, club: str) -> str:
    return g["away"] if g["home"] == club else g["home"]


def score_pair(s) -> list[int]:
    """Счёт [хозяева, гости]: в league.json — словарь, в h2h.json и history.json — список."""
    return [s["home"], s["away"]] if isinstance(s, dict) else list(s)


# ---------- состояние дня ----------

def schedule(club: str, games: list[dict], today: date) -> tuple[dict | None, dict | None]:
    """Ближайший несыгранный матч клуба с сегодняшнего дня и последний сыгранный."""
    mine = [g for g in games if club in (g["home"], g["away"])]
    nxt = next((g for g in mine if not g.get("score") and d(g["date"]) >= today), None)
    played = [g for g in mine if g.get("score")]
    return nxt, played[-1] if played else None


def day_state(nxt: dict | None, last: dict | None, today: date) -> str:
    """match — свой матч сегодня; after — сыграли вчера или сегодня; eve — матч завтра; start — сезон ещё
    не начался; pause — до матча пять дней и больше; over — матчей больше нет; иначе normal."""
    if nxt and d(nxt["date"]) == today:
        return "match"
    if last and (today - d(last["date"])).days <= 1:
        return "after"
    if nxt and (d(nxt["date"]) - today).days == 1:
        return "eve"
    if not nxt:
        return "over"
    if not last:
        return "start"
    return "pause" if (d(nxt["date"]) - today).days >= PAUSE_DAYS else "normal"


def series_opponent(nxt: dict | None, club: str, today: date) -> str | None:
    if nxt and (d(nxt["date"]) - today).days <= SERIES_DAYS:
        return other(nxt, club)
    return None


# ---------- карточки из своих данных ----------

def h2h_card(club: str, nxt: dict | None, h2h: dict, today: date) -> dict | None:
    """Очные встречи перед матчем: появляются за три дня, уходят после матча."""
    if not nxt or (d(nxt["date"]) - today).days > H2H_DAYS:
        return None
    opp = other(nxt, club)
    rec = h2h.get(pair_key(club, opp))
    if not rec or not rec.get("games"):
        return None
    return {"id": f"h2h-{nxt['id']}", "kind": "h2h", "game": nxt["id"], "opp": opp, "games": rec["games"],
            "wins": [rec["wins"].get(club, 0), rec["wins"].get(opp, 0)],
            "goals": [rec["goals"].get(club, 0), rec["goals"].get(opp, 0)], "since": rec.get("since"),
            "at": iso(day_start(d(nxt["date"]) - timedelta(days=H2H_DAYS))),
            "until": iso(day_start(d(nxt["date"]) + timedelta(days=1)))}


def meeting_card(club: str, nxt: dict | None, h2h: dict, recaps: dict, today: date) -> dict | None:
    """Как сыграли в прошлый раз с тем же соперником — только встреча с разбором."""
    if not nxt or (d(nxt["date"]) - today).days > H2H_DAYS:
        return None
    rec = h2h.get(pair_key(club, other(nxt, club)))
    last = [m for m in (rec or {}).get("last", []) if m.get("id") in recaps]
    if not last:
        return None
    m = max(last, key=lambda x: x["date"])
    return {"id": f"meet-{m['id']}", "kind": "meeting", "match": m["id"], "date": m["date"], "home": m["home"],
            "away": m["away"], "score": score_pair(m["score"]), "decision": m.get("decision", ""),
            "story": recaps[m["id"]].get("story", ""),
            "at": iso(day_start(d(nxt["date"]) - timedelta(days=H2H_DAYS))),
            "until": iso(day_start(d(nxt["date"]) + timedelta(days=1)))}


def history_id(h: dict) -> str:
    return f"h{h['game_id']}"


def this_day_card(club: str, history: list[dict], recaps: dict, today: date) -> dict | None:
    """В этот день — матч клуба в прошлых сезонах, самый свежий. Вечнозелёная карточка на тихие дни."""
    md = today.isoformat()[4:]
    rows = [h for h in history if h["date"][4:] == md and h["date"] < today.isoformat()
            and club in (h["home"], h["away"]) and h.get("score")]
    if not rows:
        return None
    h = max(rows, key=lambda x: x["date"])
    hid = history_id(h)
    out = {"id": f"day-{hid}", "kind": "day", "date": h["date"], "season": h.get("season", ""),
           "home": h["home"], "away": h["away"], "score": score_pair(h["score"]), "decision": h.get("decision", ""),
           "at": iso(day_start(today)), "until": iso(day_start(today + timedelta(days=1)))}
    if hid in recaps:
        out["match"] = hid
        out["story"] = recaps[hid].get("story", "")
    return out


def today_card(club: str, games: list[dict], today: date) -> dict | None:
    """Сегодня в лиге: все матчи дня одной карточкой, свой — первым."""
    day = [g for g in games if g["date"] == today.isoformat()]
    if not day:
        return None
    day.sort(key=lambda g: (club not in (g["home"], g["away"]), g.get("time") or "", g["id"]))
    return {"id": f"today-{today.isoformat()}", "kind": "today", "n": len(day), "games": [g["id"] for g in day[:3]],
            "at": iso(day_start(today)), "until": iso(day_start(today + timedelta(days=1)))}


def story_rank(text: str) -> int:
    return next((i for i, k in enumerate(STORY_RANK) if k in text), len(STORY_RANK))


def story_card(club: str, games: list[dict], recaps: dict, today: date) -> dict | None:
    """Сюжет дня: самая яркая история вчерашних матчей лиги. Свой матч — только если больше нечего."""
    y = (today - timedelta(days=1)).isoformat()
    told = [g for g in games if g["date"] == y and g.get("score") and (recaps.get(g["id"]) or {}).get("story")]
    if not told:
        return None
    g = min(told, key=lambda x: (club in (x["home"], x["away"]), story_rank(recaps[x["id"]]["story"]), x["id"]))
    return {"id": f"story-{g['id']}", "kind": "story", "match": g["id"], "story": recaps[g["id"]]["story"],
            "at": iso(day_start(today)), "until": iso(day_start(today + timedelta(days=1)))}


def table_card(club: str, standings: dict, last: dict | None, today: date) -> dict | None:
    """Место в таблице на следующий день после своего матча. Только о своём клубе и без антирейтинга:
    в восьмёрке — место и отрыв до следующего, ниже — сколько очков до восьмёрки."""
    if not last or (today - d(last["date"])).days > 1:
        return None
    for conf, rows in standings.items():
        i = next((k for k, r in enumerate(rows) if r["team"] == club), None)
        if i is None or not rows[i]["gp"]:
            continue
        place, pts = i + 1, rows[i]["pts"]
        out = {"id": f"table-{last['id']}", "kind": "table", "conf": conf, "place": place, "pts": pts,
               "at": iso(day_start(d(last["date"]) + timedelta(days=1))),
               "until": iso(day_start(today + timedelta(days=1)))}
        if place > 8 and len(rows) >= 8:
            out["to8"] = max(rows[7]["pts"] - pts, 0)
        elif place > 1:
            out["up"] = rows[i - 1]["pts"] - pts
        return out
    return None


def upcoming_card(club: str, nxt: dict | None, games: list[dict], today: date) -> dict | None:
    """Дальше: три матча после ближайшего. Не новость, поэтому без отметки «новое» (at = None)."""
    if not nxt:
        return None
    later = [g for g in games if club in (g["home"], g["away"]) and not g.get("score")
             and d(g["date"]) >= today and g["id"] != nxt["id"]][:3]
    if not later:
        return None
    return {"id": f"next-{later[0]['id']}", "kind": "upcoming", "games": [g["id"] for g in later], "at": None,
            "until": iso(day_start(d(nxt["date"]) + timedelta(days=1)))}


LEAD_GOOD = ("pts", "g", "a", "sv_pct", "pm")   # «по штрафу» в ленте не хвалим — это не достижение


def leaders_card(club: str, leaders: dict | None, today: date) -> dict | None:
    """Свои в лидерах лиги: у каждого игрока лучшее место, до трёх игроков. Только хорошее о своих."""
    if not leaders:
        return None
    best: dict[str, tuple[int, str]] = {}
    for cat in LEAD_GOOD:
        for r in leaders.get("categories", {}).get(cat, []):
            if r.get("team") != club or r["name"] == "Игрок скрыт":
                continue
            if r["name"] not in best or r["rank"] < best[r["name"]][0]:
                best[r["name"]] = (r["rank"], cat)
    if not best:
        return None
    rows = sorted(best.items(), key=lambda x: x[1][0])[:3]
    return {"id": f"leaders-{leaders.get('season', '')}-{today.isoformat()}", "kind": "leaders",
            "league": leaders.get("league", ""), "season": leaders.get("season", ""),
            "rows": [{"name": n, "rank": r, "cat": c} for n, (r, c) in rows], "at": None,
            "until": iso(day_start(today + timedelta(days=1)))}


def own_cards(club: str, state: str, nxt: dict | None, last: dict | None, *, games: list[dict], standings: dict,
              h2h: dict, history: list[dict], recaps: dict, leaders: dict | None, today: date) -> list[dict]:
    """Свои карточки по порядку дня: перед матчем — соперник, после — итоги, в тихий день — лига.
    «Свои в лидерах» — только в тихие дни: в игровой день есть о чём рассказать и без них."""
    cards = {
        "h2h": h2h_card(club, nxt, h2h, today),
        "meeting": meeting_card(club, nxt, h2h, recaps, today),
        "day": this_day_card(club, history, recaps, today),
        "today": today_card(club, games, today),
        "story": story_card(club, games, recaps, today),
        "table": table_card(club, standings, last, today),
        "upcoming": upcoming_card(club, nxt, games, today),
        "leaders": leaders_card(club, leaders, today) if state in ("start", "pause", "normal", "over") else None,
    }
    order = {
        "match": ("h2h", "meeting", "today", "story", "table", "day", "upcoming"),
        "eve": ("h2h", "meeting", "story", "today", "table", "day", "upcoming"),
        "after": ("table", "story", "today", "h2h", "meeting", "day", "upcoming"),
    }.get(state, ("story", "today", "h2h", "meeting", "day", "leaders", "table", "upcoming"))
    return [cards[k] for k in order if cards[k]]


# ---------- посты каналов ----------

def shown_channels(channels: list[dict]) -> list[dict]:
    """Каналы, чьи посты лента может показать: без фан-каналов и без клубов, которые отказались.
    Предварительного согласия не ждём — решение владельца 30.09 (ADR-015)."""
    out = [c for c in channels if c["kind"] in SHOWN_KINDS and (c.get("optout") or {}).get("level") != "all"]
    return sorted(out, key=lambda c: KIND_ORDER[c["kind"]])


def fresh(channel: dict, posts: dict, now: datetime, live_ok: bool) -> list[dict]:
    """Свежие посты канала, новые первыми."""
    got = (posts.get(channel["handle"]) or {})
    if not got.get("ok"):
        return []
    out = []
    for p in got.get("posts", []):
        age = now - datetime.fromisoformat(p["at"])
        if age < timedelta(0) or age > (LIVE_TTL if p.get("live") else POST_TTL):
            continue
        if p.get("live") and not live_ok:
            continue
        out.append(p)
    return sorted(out, key=lambda p: p["at"], reverse=True)


def post_card(p: dict, channel: dict, title: str, slot: str) -> dict:
    ttl = LIVE_TTL if p.get("live") else POST_TTL
    card = {"id": f"post-{channel['handle']}-{p['id']}", "kind": "post", "slot": slot, "club": channel.get("club"),
            "channel": channel["handle"], "ctitle": title, "ckind": channel["kind"], "url": p["url"],
            "title": p.get("title", ""), "text": p.get("text", ""), "media": p.get("media", 0),
            "at": p["at"], "until": iso(datetime.fromisoformat(p["at"]) + ttl)}
    for k in ("image", "video", "live"):
        if p.get(k):
            card[k] = p[k]
    return card


def club_posts(club: str, channels: list[dict], posts: dict, now: datetime, slot: str, limit: int) -> list[dict]:
    """До limit постов клуба из его каналов (свой канал раньше общего), не больше PER_CHANNEL с канала."""
    out = []
    for c in (x for x in channels if x.get("club") == club):
        title = (posts.get(c["handle"]) or {}).get("title") or c.get("title") or c["handle"]
        for p in fresh(c, posts, now, live_ok=slot == "mine")[:PER_CHANNEL]:
            out.append(post_card(p, c, title, slot))
    out.sort(key=lambda x: x["at"], reverse=True)
    return out[:limit]


def rotation(club: str, clubs: list[str], skip: set[str], day_no: int) -> list[str]:
    """Клубы по кругу для слота «лига»: сдвиг (номер дня + индекс клуба) по кругу из остальных."""
    others = [c for c in clubs if c != club and c not in skip]
    if not others:
        return []
    k = (day_no + clubs.index(club)) % len(others) if club in clubs else day_no % len(others)
    return others[k:] + others[:k]


def post_queue(club: str, opp: str | None, clubs: list[str], channels: list[dict], posts: dict,
               now: datetime) -> list[dict]:
    """Посты по важности: свой, соперник, лига, клуб по ротации — и второй круг."""
    mine = club_posts(club, channels, posts, now, "mine", 2)
    rival = club_posts(opp, channels, posts, now, "opp", 2) if opp else []
    league = []
    for c in (x for x in channels if x["kind"] == "league"):
        title = (posts.get(c["handle"]) or {}).get("title") or c.get("title") or c["handle"]
        league += [post_card(p, c, title, "league") for p in fresh(c, posts, now, live_ok=False)[:1]]
    league = league[:1]
    rot = []
    for other_club in rotation(club, clubs, {opp} if opp else set(), now.date().toordinal()):
        got = club_posts(other_club, channels, posts, now, "club", 1)
        if got:
            rot.append(got[0])
        if len(rot) == 2:
            break
    first = [x[0] for x in (mine, rival, league or rot[:1]) if x]
    second = [x for x in (mine[1:2] + rival[1:2] + (rot if league else rot[1:]))]
    return first + second


def take(queue: list[dict], pos: int, per_channel: dict, per_club: dict) -> dict | None:
    """Первый пост очереди, который проходит лимиты на канал и чужой клуб на позиции pos."""
    for i, p in enumerate(queue):
        n = per_channel.get(p["channel"], 0)
        if n >= PER_CHANNEL or (pos < FIRST and n >= 1):
            continue
        if p["slot"] == "club" and per_club.get(p["club"]):
            continue
        per_channel[p["channel"]] = n + 1
        if p["slot"] == "club":
            per_club[p["club"]] = 1
        return queue.pop(i)
    return None


def assemble(own: list[dict], queue: list[dict]) -> list[dict]:
    """Своя, пост, своя, пост… Постов не больше 40% и не два подряд: после каждой своей — не больше одного."""
    room = int(len(own) * (1 - OWN_SHARE) / OWN_SHARE + 1e-9)
    out, queue, per_channel, per_club = [], list(queue), {}, {}
    for card in own:
        out.append(card)
        if room and queue:
            p = take(queue, len(out), per_channel, per_club)
            if p:
                out.append(p)
                room -= 1
    return out[:MAX_CARDS]


# ---------- лист клуба ----------

def build(club: str, now: datetime, *, clubs: list[str], games: list[dict], standings: dict, h2h: dict,
          history: list[dict], recaps: dict, channels: list[dict], posts: dict, leaders: dict | None = None) -> dict:
    """Лист клуба на сейчас: состояние дня, ближайший матч и карточки по порядку."""
    today = now.astimezone(TZ).date()
    nxt, last = schedule(club, games, today)
    state = day_state(nxt, last, today)
    own = own_cards(club, state, nxt, last, games=games, standings=standings, h2h=h2h, history=history,
                    recaps=recaps, leaders=leaders, today=today)
    shown = shown_channels(channels)
    queue = post_queue(club, series_opponent(nxt, club, today), clubs, shown, posts, now)
    out = {"club": club, "built": iso(now), "state": state, "cards": assemble(own, queue)}
    if nxt:
        out["next"] = nxt["id"]
    return out

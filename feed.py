"""Лист дня на «Главной» (ADR-015): карточки из своих данных и превью постов каналов, файл на клуб.

Лист персонален только по клубу и времени, поэтому собирается заранее, при сборке Pages. Правила
здесь, клиент только прячет просроченное и скрытые каналы. Без сети и без чтения файлов: всё
приходит аргументами, поэтому правила проверяются `unittest` на выдуманных данных.

Правила ADR-015:
- своих данных в листе не меньше 60%, постов каналов — не больше 40%, два поста подряд не идут,
  первая карточка — своя. Посты своего клуба в долю не считаются: это его новости (пересмотр 01.10);
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
        elif len(rows) > 1:
            out["lead"] = pts - rows[1]["pts"]   # первому — отрыв от второго, а не «первое место» дважды
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
    return {"id": f"leaders-{club}-{leaders.get('season', '')}-{today.isoformat()}", "kind": "leaders", "club": club,
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


def channel_names(channel: dict, posts: dict) -> tuple[str, str]:
    """Имя канала для шапки карточки и полное — для листа «⋯». Короткое (`short` в channels.json) — без
    эмодзи и приписок вроде «|РХЛ|26/27»: на 320px полное у 9 каналов из 22 обрезалось (пересмотр 01.10)."""
    full = (posts.get(channel["handle"]) or {}).get("title") or channel.get("title") or channel["handle"]
    return channel.get("short") or full, full


def post_card(p: dict, channel: dict, title: str, slot: str, full: str | None = None) -> dict:
    ttl = LIVE_TTL if p.get("live") else POST_TTL
    card = {"id": f"post-{channel['handle']}-{p['id']}", "kind": "post", "slot": slot, "club": channel.get("club"),
            "channel": channel["handle"], "ctitle": title, "ckind": channel["kind"], "url": p["url"],
            "title": p.get("title", ""), "text": p.get("text", ""), "media": p.get("media", 0),
            "at": p["at"], "until": iso(datetime.fromisoformat(p["at"]) + ttl)}
    if full and full != title:
        card["cfull"] = full
    for k in ("image", "video", "live"):
        if p.get(k):
            card[k] = p[k]
    return card


def club_posts(club: str, channels: list[dict], posts: dict, now: datetime, slot: str, limit: int) -> list[dict]:
    """До limit постов клуба из его каналов (свой канал раньше общего), не больше PER_CHANNEL с канала."""
    out = []
    for c in (x for x in channels if x.get("club") == club):
        title, full = channel_names(c, posts)
        for p in fresh(c, posts, now, live_ok=slot == "mine")[:PER_CHANNEL]:
            out.append(post_card(p, c, title, slot, full))
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
        title, full = channel_names(c, posts)
        league += [post_card(p, c, title, "league", full) for p in fresh(c, posts, now, live_ok=False)[:1]]
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


def take(queue: list[dict], pos: int, per_channel: dict, per_club: dict, mine_only: bool = False) -> dict | None:
    """Первый пост очереди, который проходит лимиты на канал и чужой клуб на позиции pos.
    mine_only — доля чужих постов исчерпана, годится только пост своего клуба."""
    for i, p in enumerate(queue):
        if mine_only and p["slot"] != "mine":
            continue
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
    """Своя, пост, своя, пост… Не два поста подряд: после каждой своей — не больше одного. Постов чужих
    каналов не больше 40%, посты своего клуба в долю не идут: у клуба с одной своей карточкой («Дальше»)
    иначе не было бы ни одной своей новости (пересмотр 01.10)."""
    room = int(len(own) * (1 - OWN_SHARE) / OWN_SHARE + 1e-9)
    out, queue, per_channel, per_club = [], list(queue), {}, {}
    for card in own:
        out.append(card)
        if queue:
            p = take(queue, len(out), per_channel, per_club, mine_only=not room)
            if p:
                out.append(p)
                if p["slot"] != "mine":
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


# ---------- лента лиги под листом (ADR-015, пересмотр 30.09) ----------
# Под сегодняшним листом — непрерывная лента всех каналов за неделю, одна на всех: свой клуб и
# соперник уже подняты в лист, ниже — лига по времени. Клиент подгружает её порциями при прокрутке

STREAM_DAYS = 7        # глубина ленты: дальше посты устаревают, а картинки CDN — тем более
STREAM_RUN = 2         # один канал — не больше двух постов подряд
STREAM_EVERY = 5       # после каждых пяти постов — наша карточка
STREAM_DAY_CARDS = 5   # «в этот день» по лиге — не больше пяти
STREAM_AHEAD = 2       # матчи лиги — на сегодня и два дня вперёд
STREAM_KIND_MAX = 12   # очных встреч и лидеров по клубам — не больше дюжины, чтобы не вытеснить остальное


def limit_runs(posts: list[dict], run: int = STREAM_RUN) -> list[dict]:
    """Порядок по времени, но третий подряд пост одного канала уступает место ближайшему чужому."""
    out, rest = [], list(posts)
    while rest:
        i = 0
        if len(out) >= run and all(x["channel"] == out[-1]["channel"] for x in out[-run:]):
            i = next((k for k, p in enumerate(rest) if p["channel"] != out[-1]["channel"]), 0)
        out.append(rest.pop(i))
    return out


def stream_posts(channels: list[dict], posts: dict, now: datetime) -> list[dict]:
    """Посты всех показываемых каналов за неделю, новые первыми. Трансляция по ходу матча сюда не идёт:
    через час она шум, а своему клубу её показывает лист."""
    out = []
    for c in shown_channels(channels):
        got = posts.get(c["handle"]) or {}
        if not got.get("ok"):
            continue
        title, full = channel_names(c, posts)
        for p in got.get("posts", []):
            at = datetime.fromisoformat(p["at"])
            if p.get("live") or not timedelta(0) <= now - at <= timedelta(days=STREAM_DAYS):
                continue
            card = post_card(p, c, title, "stream", full)
            card["until"] = iso(at + timedelta(days=STREAM_DAYS))
            out.append(card)
    out.sort(key=lambda x: (x["at"], x["id"]), reverse=True)
    return limit_runs(out)


def stream_days(games: list[dict], today: date) -> list[dict]:
    """Матчи лиги на сегодня и два дня вперёд — по карточке на игровой день. До первого тура это
    «Завтра в лиге · 13 матчей»: есть что показать, пока протоколов нет."""
    cards = []
    for k in range(STREAM_AHEAD + 1):
        day = (today + timedelta(days=k)).isoformat()
        got = sorted((g for g in games if g["date"] == day), key=lambda g: (g.get("time") or "", g["id"]))
        if got:
            cards.append({"id": f"today-{day}", "kind": "today", "date": day, "n": len(got),
                          "games": [g["id"] for g in got[:3]], "at": None,
                          "until": iso(day_start(d(day) + timedelta(days=1)))})
    return cards


def stream_h2h(games: list[dict], h2h: dict, today: date) -> list[dict]:
    """Очные встречи серий ближайшей недели: пара — одна карточка, к первому матчу серии. Тот же id, что у
    карточки в листе клуба, поэтому в «Все» она не повторяется."""
    cards, seen = [], set()
    for g in sorted(games, key=lambda g: (g["date"], g.get("time") or "", g["id"])):
        if g.get("score") or not 0 <= (d(g["date"]) - today).days <= SERIES_DAYS:
            continue
        key = pair_key(g["home"], g["away"])
        rec = h2h.get(key)
        if key in seen or not rec or not rec.get("games"):
            continue
        seen.add(key)
        a, b = g["home"], g["away"]
        cards.append({"id": f"h2h-{g['id']}", "kind": "h2h", "game": g["id"], "club": a, "opp": b,
                      "games": rec["games"], "wins": [rec["wins"].get(a, 0), rec["wins"].get(b, 0)],
                      "goals": [rec["goals"].get(a, 0), rec["goals"].get(b, 0)], "since": rec.get("since"),
                      "at": None, "until": iso(day_start(d(g["date"]) + timedelta(days=1)))})
    return cards[:STREAM_KIND_MAX]


def stream_leaders(clubs: list[str], leaders: dict | None, today: date) -> list[dict]:
    """«Свои в лидерах» прошлого сезона по клубам, по кругу со сдвигом по дню: каждый день первым — другой."""
    got = [c for c in (leaders_card(club, leaders, today) for club in clubs) if c]
    if not got:
        return []
    k = today.toordinal() % len(got)
    return (got[k:] + got[:k])[:STREAM_KIND_MAX]


def stream_cards(games: list[dict], history: list[dict], recaps: dict, today: date, *, h2h: dict | None = None,
                 leaders: dict | None = None, clubs: list[str] | None = None) -> list[dict]:
    """Наши карточки для ленты лиги по кругу: сюжет игрового дня, матчи ближайших дней, очные встречи
    серий недели, свои в лидерах по клубам, «в этот день». До первого тура сюжетов нет, но остальное есть:
    раньше на 143 поста приходилось 3 наши карточки (пересмотр 01.10)."""
    stories = []
    for k in range(1, STREAM_DAYS + 1):
        day = (today - timedelta(days=k)).isoformat()
        told = [g for g in games if g["date"] == day and g.get("score") and (recaps.get(g["id"]) or {}).get("story")]
        if told:
            g = min(told, key=lambda x: (story_rank(recaps[x["id"]]["story"]), x["id"]))
            stories.append({"id": f"story-{g['id']}", "kind": "story", "match": g["id"], "date": day,
                            "story": recaps[g["id"]]["story"], "at": iso(day_start(d(day) + timedelta(days=1))),
                            "until": iso(day_start(d(day) + timedelta(days=STREAM_DAYS + 1)))})
    md = today.isoformat()[4:]
    past = [h for h in history if h["date"][4:] == md and h["date"] < today.isoformat() and h.get("score")]
    story = lambda h: (recaps.get(history_id(h)) or {}).get("story", "")   # noqa: E731
    # сначала с сюжетом и поярче, потом свежие сезоны; по одному матчу на сезон — разнообразнее
    past.sort(key=lambda h: h["date"], reverse=True)
    past.sort(key=lambda h: (not story(h), story_rank(story(h))))
    seen: set[str] = set()
    this_day = []
    for h in past:
        if len(seen) >= STREAM_DAY_CARDS or h["date"][:4] in seen:
            continue
        seen.add(h["date"][:4])
        hid = history_id(h)
        card = {"id": f"day-{hid}", "kind": "day", "date": h["date"], "season": h.get("season", ""),
                "home": h["home"], "away": h["away"], "score": score_pair(h["score"]),
                "decision": h.get("decision", ""), "at": None, "until": iso(day_start(today + timedelta(days=1)))}
        if hid in recaps:
            card["match"] = hid
            card["story"] = story(h)
        this_day.append(card)
    kinds = [stories, stream_days(games, today), stream_h2h(games, h2h or {}, today),
             stream_leaders(clubs or [], leaders, today), this_day]
    out = []
    while any(kinds):
        for k in kinds:
            if k:
                out.append(k.pop(0))
    return out


def build_stream(now: datetime, *, games: list[dict], history: list[dict], recaps: dict, channels: list[dict],
                 posts: dict, h2h: dict | None = None, leaders: dict | None = None,
                 clubs: list[str] | None = None) -> dict:
    """Лента лиги: посты по времени, после каждых STREAM_EVERY — наша карточка, пока они есть."""
    items, own = [], stream_cards(games, history, recaps, now.astimezone(TZ).date(), h2h=h2h, leaders=leaders,
                                  clubs=clubs)
    for i, p in enumerate(stream_posts(channels, posts, now), 1):
        items.append(p)
        if i % STREAM_EVERY == 0 and own:
            items.append(own.pop(0))
    return {"built": iso(now), "items": items + own}

"""«Смотреть» и время начала из постов каналов клубов в день матча (ADR-019, разделы 2 и 7).

Без сети и без чтения файлов: посты (`channel_posts.json`, как его пишет tg_channels.py), каналы
(`channels.json`) и команды (`teams.json`) приходят аргументами, поэтому правила проверяются `unittest`.

Пост относится к матчу, если:
- его опубликовал канал одной из двух команд или канал лиги (фан-каналы и отказавшиеся — нет);
- он вышел в день матча по Москве или накануне после 18:00. «Вчера» без «сегодня» — пост о прошлом
  матче, «завтра» в вечернем посте — о завтрашнем, даже если клуб играл и сегодня (серии идут по два дня);
- в тексте есть соперник (через варианты написания из teams.json) или слова «трансляция», «эфир»,
  «смотреть», «прямой». Пост канала лиги — только если названы обе команды и ни одной другой.

«Смотреть» — только ссылки на видео из белого списка (`video`), без букмекеров и пиратов, не больше трёх
и без повторов. Время — только с явной привязкой: «начало в 17:00», «стартуем в 17:00», «17:00 МСК»,
«в 19:30 по местному». Без пояса — если пояс канала и пояс арены совпадают. Разные времена в одном посте
не берём: это расписание или анонс нескольких событий.
"""
import re
from datetime import date, datetime, time, timedelta
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import tg_channels as tg

TZ = ZoneInfo("Europe/Moscow")
MOSCOW = "Europe/Moscow"
WATCH_MAX = 3
EVE = time(18, 0)          # пост накануне после 18:00 — уже о завтрашнем матче
SHOWN_KINDS = tg.SHOWN_KINDS

WATCH_RE = re.compile(r"трансляци|\bэфир|\b(по)?смотр(еть|им|ите|и)\b|\bпрям(ой|ая|ую|ом|ые)\b", re.I)
YESTERDAY_RE = re.compile(r"\bвчера", re.I)
TOMORROW_RE = re.compile(r"\bзавтра", re.I)
TODAY_RE = re.compile(r"\bсегодня", re.I)

# ---------- ссылки на видео ----------

VK_HOSTS = ("vk.com", "vk.ru", "m.vk.com", "m.vk.ru")


def video(url: str) -> tuple[str, bool] | None:
    """Видеохостинг ссылки и прямой ли это эфир. Не видео из белого списка — None."""
    try:
        p = urlsplit(url)
    except ValueError:
        return None
    h, path, q = tg.host(url), p.path, p.query
    if p.scheme not in ("http", "https"):
        return None
    if h in VK_HOSTS and (path.startswith("/video") or re.search(r"(^|&)z=video", q)):
        return "VK Видео", False
    if h == "vkvideo.ru" and len(path) > 1:
        return "VK Видео", False
    if h == "live.vkvideo.ru" and len(path) > 1:
        return "VK Видео", True
    if h in ("youtube.com", "m.youtube.com") and path == "/watch" and "v=" in q:
        return "YouTube", False
    if h in ("youtube.com", "m.youtube.com") and path.startswith("/live/"):
        return "YouTube", True
    if h == "youtu.be" and len(path) > 1:
        return "YouTube", False
    if h == "rutube.ru" and path.startswith(("/video/", "/live/")):
        return "Rutube", path.startswith("/live/")
    if h == "matchpremier.ru" or h.endswith(".matchpremier.ru"):
        return "«Матч Премьер»", False
    if h == "smotrim.ru":
        return "«Смотрим»", path.startswith("/live")
    if h in ("t.me", "telegram.me") and re.fullmatch(r"/\w+", path) and "livestream" in q:
        return "Эфир в Telegram", True
    return None


def canonical(url: str) -> str:
    """Адрес для кнопки: https, без www., без меток utm и якоря."""
    p = urlsplit(url)
    q = "&".join(x for x in p.query.split("&") if x and not x.lower().startswith("utm_"))
    return urlunsplit(("https", tg.host(url), p.path, q, ""))


def same_video(url: str) -> str:
    """Ключ повтора: один ролик ВК по vk.com, vk.ru и vkvideo.ru, один ролик YouTube по youtu.be и watch?v=."""
    p, h = urlsplit(url), tg.host(url)
    if h in VK_HOSTS or h.endswith("vkvideo.ru"):
        m = re.search(r"video(-?\d+_\d+)", p.path + "?" + p.query)
        if m:
            return "vk" + m.group(1)
    if h in ("youtube.com", "m.youtube.com", "youtu.be"):
        vid = dict(parse_qsl(p.query)).get("v") or p.path.rstrip("/").rsplit("/", 1)[-1]
        return "yt" + vid
    return canonical(url).lower()


def channel_label(channel: dict, posts: dict) -> str:
    """Канал в названии кнопки: короткое имя из channels.json, «кавычки» — если своих нет."""
    name = (channel.get("short") or channel.get("title") or (posts.get(channel["handle"]) or {}).get("title")
            or channel["handle"]).strip()
    return f"канал {name}" if "«" in name else f"канал «{name}»"


# ---------- упоминания команд ----------

GENERIC = {"мхк", "хк", "академия", "машина", "красная", "сшор"}   # мало для упоминания поодиночке


def _clean(s: str) -> str:
    s = s.lower().replace("ё", "е")
    return re.sub(r"[«»\"'“”„]", " ", s)


def _words(name: str) -> list[str]:
    words = [w for w in re.split(r"[\s-]+", _clean(name)) if w]
    while words and words[0] in ("мхк", "хк"):
        words.pop(0)
    return words


def _stem(word: str) -> str:
    s = re.sub(r"[аеиоуыьйя]+$", "", word)
    return s if len(s) >= 3 else word


def _word_re(word: str) -> str:
    """Слово названия с падежными окончаниями: «Белгород» → «Белгородом», «Ленинградец» → «Ленинградца»."""
    if word.isdigit():
        return re.escape(word)
    if word.endswith("ец"):
        return rf"(?:{re.escape(word)}|{re.escape(word[:-2])}ц\w{{0,3}})"
    return rf"{re.escape(_stem(word))}\w{{0,3}}"


def name_patterns(teams: list[dict]) -> dict[str, re.Pattern]:
    """Как в тексте поста узнать команду: название целиком в любом падеже или отличительное слово
    названия, которого нет у других команд («Рязань», «ВДВ», но не «Динамо» — их две)."""
    names = {t["id"]: [_words(n) for n in (t["name"], *t.get("aliases", []), *t.get("former", []))] for t in teams}
    owners: dict[str, set[str]] = {}
    for tid, variants in names.items():
        for words in variants:
            for w in words:
                owners.setdefault(_stem(w), set()).add(tid)
    out = {}
    for tid, variants in names.items():
        alts = []
        for words in variants:
            # название целиком и его начало: «Красная машина Академия» и «Красная машина»
            alts += [r"[\s-]+".join(_word_re(w) for w in words[:k]) for k in range(len(words), 0, -1)
                     if k > 1 or len(words) == 1]
            alts += [_word_re(w) for w in words
                     if len(w) >= 3 and w not in GENERIC and owners[_stem(w)] == {tid}]
        alts = [a for a in dict.fromkeys(alts) if a]
        out[tid] = re.compile(rf"(?<!\w)(?:{'|'.join(alts)})(?!\w)" if alts else r"(?!x)x", re.I)
    return out


def mentioned(text: str, patterns: dict[str, re.Pattern]) -> set[str]:
    low = _clean(text)
    return {tid for tid, p in patterns.items() if p.search(low)}


# ---------- время начала ----------

HM_RE = re.compile(r"(?<![\d:.])([01]?\d|2[0-3])(?::([0-5]\d)|\.(00|15|30|45))(?![\d:]|\.\d)")
# «Начало в 17:00», «стартуем в 17:00», «Сегодня в 17:00», «⏰ 17:00»
CUE_RE = re.compile(r"начал|старт|начина|начн[её]|вбрасыван|\bматч|\bигр[аеуы]\b|сегодня|завтра"
                    r"|[⏰⏱⌚\U0001F550-\U0001F567]", re.I)
# время чего-то другого: трансляции, открытия ворот, автограф-сессии, разминки
OTHER_RE = re.compile(r"трансляци|эфир|ворот|автограф|касс|разминк|сбор|автобус|выезд|пресс|конференц|интервью"
                      r"|розыгрыш|магазин|фан-?зон", re.I)
MSK_RE = re.compile(r"мск(?!\s*[+-]\s*\d)|по\s+москв|московск", re.I)
MSK_SHIFT_RE = re.compile(r"мск\s*([+-])\s*(\d{1,2})", re.I)
LOCAL_RE = re.compile(r"местн|\bмест\.", re.I)
NAMED_ZONES = ((re.compile(r"иркутск|ангарск", re.I), "Asia/Irkutsk"),
               (re.compile(r"самарск|ижевск|удмуртск|глазовск", re.I), "Europe/Samara"),
               (re.compile(r"саратовск", re.I), "Europe/Saratov"),
               (re.compile(r"екатеринбургск|уральск|ямальск|салехардск", re.I), "Asia/Yekaterinburg"))
# «МСК+5» — пояса России без перехода на летнее время с 2014 года
SHIFT_ZONES = {-1: "Europe/Kaliningrad", 0: MOSCOW, 1: "Europe/Samara", 2: "Asia/Yekaterinburg", 3: "Asia/Omsk",
               4: "Asia/Krasnoyarsk", 5: "Asia/Irkutsk", 6: "Asia/Yakutsk", 7: "Asia/Vladivostok",
               8: "Asia/Magadan", 9: "Asia/Kamchatka"}


def zone_word(s: str, last: bool = False) -> tuple[int, str] | None:
    """Пояс, названный в куске строки: (место, "msk" | "local" | IANA | "?"). last — ближайший к концу."""
    hits = []
    for m in MSK_SHIFT_RE.finditer(s):
        shift = int(m.group(2)) * (1 if m.group(1) == "+" else -1)
        hits.append((m.start(), SHIFT_ZONES.get(shift, "?")))
    hits += [(m.start(), "msk") for m in MSK_RE.finditer(s)]
    hits += [(m.start(), "local") for m in LOCAL_RE.finditer(s)]
    for rx, zone in NAMED_ZONES:
        hits += [(m.start(), zone) for m in rx.finditer(s)]
    if not hits:
        return None
    return max(hits) if last else min(hits)


def offset(zone: str, day: date) -> timedelta:
    return datetime.combine(day, time(12), ZoneInfo(zone)).utcoffset()


def post_time(lines: list[str], day: date, arena: str, poster: str) -> datetime | None:
    """Начало матча по Москве из строк поста. arena — пояс арены (для «по местному» и без пояса),
    poster — пояс канала: без пояса в тексте время берём, только если они совпадают."""
    found = set()
    for line in lines:
        spots = list(HM_RE.finditer(line))
        for i, m in enumerate(spots):
            before = line[spots[i - 1].end() if i else 0:m.start()][-40:]
            after = line[m.end():spots[i + 1].start() if i + 1 < len(spots) else len(line)][:30]
            if OTHER_RE.search(before):
                continue
            zone = zone_word(after) or (zone_word(before, last=True) if i == 0 else None)
            if not zone and not CUE_RE.search(before):
                continue   # «3 октября — 17:00» из расписания на неделю
            kind = zone[1] if zone else None
            if kind == "?":
                continue
            if kind is None:
                if offset(arena, day) != offset(poster, day):
                    continue   # клуб из другого пояса пишет время без пояса — чьё оно, не понять
                kind = arena
            tz = {"msk": MOSCOW, "local": arena}.get(kind, kind)
            hh, mm = int(m.group(1)), int(m.group(2) or m.group(3))
            found.add(datetime.combine(day, time(hh, mm), ZoneInfo(tz)).astimezone(TZ))
    return found.pop() if len(found) == 1 else None


# ---------- привязка поста к матчу ----------


def target_day(at: datetime, text: str, plays) -> date | None:
    """День матча, о котором пост: в день матча или накануне после 18:00. plays(d) — есть ли матч в день d."""
    d = at.astimezone(TZ).date()
    evening = at.astimezone(TZ).time() >= EVE
    today = TODAY_RE.search(text)
    if YESTERDAY_RE.search(text) and not today:
        return None   # обзор или итоги прошлого матча; «вчера уступили, сегодня реванш» — о сегодняшнем
    if evening and TOMORROW_RE.search(text) and not today:
        return d + timedelta(days=1) if plays(d + timedelta(days=1)) else None
    if plays(d):
        return d
    if evening and not today and plays(d + timedelta(days=1)):
        return d + timedelta(days=1)
    return None


def post_text(p: dict) -> str:
    return "\n".join([p.get("title") or "", p.get("text") or "", *p.get("times", [])])


def shown(channels: list[dict]) -> list[dict]:
    """Каналы клубов и лиги, которые мы показываем: без фан-каналов и отказавшихся (ADR-015)."""
    return [c for c in channels if c.get("kind") in SHOWN_KINDS and (c.get("optout") or {}).get("level") != "all"]


def bind(at: datetime, text: str, club: str | None, names: set[str], by_club: dict) -> dict | None:
    """Матч, о котором пост канала клуба club (None — канал лиги), или None."""
    if club:
        day = target_day(at, text, lambda d: (d, club) in by_club)
        g = by_club.get((day, club)) if day else None
        if not g:
            return None
        opp = g["away"] if g["home"] == club else g["home"]
        return g if opp in names or WATCH_RE.search(text) else None
    if len(names) != 2:
        return None   # пост лиги обо всём игровом дне: чья в нём ссылка, не понять
    a, b = sorted(names)

    def pair(d: date) -> bool:
        g = by_club.get((d, a))
        return g is not None and {g["home"], g["away"]} == {a, b}
    day = target_day(at, text, pair)
    return by_club.get((day, a)) if day else None


def attach(games: list[dict], teams: list[dict], channels: list[dict], posts: dict) -> dict[str, dict]:
    """Что нашлось в постах, по id матча: `watch` — кнопки «Смотреть», `start` — начало по Москве
    (datetime) из самого свежего поста с временем. Матчи не меняет: что из этого брать, решает сборка."""
    patterns = name_patterns(teams)
    zones = {t["id"]: t.get("tz") or MOSCOW for t in teams}
    by_club: dict[tuple[date, str], dict] = {}
    for g in games:
        d = date.fromisoformat(g["date"])
        by_club[(d, g["home"])] = by_club[(d, g["away"])] = g
    hits: dict[str, list[tuple]] = {}
    for c in shown(channels):
        got = posts.get(c["handle"]) or {}
        if not got.get("ok"):
            continue
        for p in got.get("posts", []) + got.get("extra", []):
            try:
                at = datetime.fromisoformat(p["at"]).astimezone(TZ)
            except (KeyError, TypeError, ValueError):
                continue
            text = post_text(p)
            links = [tg.unwrap(u) for u in p.get("links", [])]
            if tg.AD_RE.search(text) or tg.BET_RE.search(text) or any(tg.link_reason(u) for u in links):
                continue   # tg_channels.py такие посты уже отбросил; кэш мог быть собран старой версией
            g = bind(at, text, c.get("club"), mentioned(text, patterns), by_club)
            if g:
                hits.setdefault(g["id"], []).append((at, p, c, links, text))
    by_id = {g["id"]: g for g in games}
    out = {}
    for gid, rows in hits.items():
        g = by_id[gid]
        rows.sort(key=lambda r: r[0])
        found = []
        for at, p, c, links, text in rows:
            for u in links:
                v = video(u)
                if v:
                    strong = v[1] or bool(WATCH_RE.search(text))   # прямой эфир или пост о трансляции — выше
                    found.append((not strong, at, u, v[0], c))
        watch, seen = [], set()
        for _, _, u, kind, c in sorted(found, key=lambda x: (x[0], x[1])):
            if same_video(u) in seen:
                continue
            seen.add(same_video(u))
            watch.append({"title": f"{kind} · {channel_label(c, posts)}", "url": canonical(u),
                          "src": f"t.me/{c['handle']}"})
        start = None
        for at, p, c, links, text in reversed(rows):   # свежий пост главнее: время могли перенести
            if p.get("live"):
                continue   # трансляция по ходу: «12:34» там — минута матча
            club = c.get("club")
            start = post_time(p.get("times") or [p.get("title") or "", p.get("text") or ""],
                              date.fromisoformat(g["date"]), zones.get(g["home"], MOSCOW),
                              zones.get(club, MOSCOW) if club else MOSCOW)
            if start:
                break
        got = {}
        if watch:
            got["watch"] = watch[:WATCH_MAX]
        if start:
            got["start"] = start
        if got:
            out[gid] = got
    return out

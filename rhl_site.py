"""Матчи с сайта лиги rhl.fhr.ru (ADR-019, раздел 2): время по Москве, номер, статус и счёт.

Сайт РХЛ открылся 03.10.2026 на новом движке, не на том, что у nmhl.fhr.ru (league.py). Источник —
одна страница календаря и страницы матч-центра:

- `/calendar/` — будущие матчи целиком (`calendar__match`: id, время МСК, №, город, команды) и лента
  дней сверху (`matches-feed__match`): статус, счёт сыгранных и идущих, команды — только эмблемами;
- `/matchcenter/<турнир>/<id>/` — счёт, «2-й период» по ходу, номер, дата и арена, авторы голов;
- `/matchcenter/<турнир>/<id>/protocol/` — протокол сыгранного матча (rhl_protocol.py): голы, удаления,
  составы, судьи. Лежит в хранилище у матча в `report` в формате `league.Protocol.to_json()`;
- `/matchcenter/<турнир>/<id>/video/` и `/translations/` — видео матча для «Смотреть» (разбор — rhl_media.py);
- `/stat/leaders/…/nomination/<номинация>/` — лидеры сезона (ADR-009) → leaders.json.

Разбор — регулярками по классам вёрстки, как rhockey.py: вложенность div неровная. Снимок страниц —
задание «Снимок источников», фикстуры — tests/fixtures/rhl_*.html. Сайт отвечает и зарубежным IP,
поэтому скачивает задание Pages раз в час. Запросы по одному с паузой в секунду (CLAUDE.md).

Хранилище rhl_site.json (не в git, кэш задания) копит матчи между запусками: лента сверху показывает
только ближайшие дни, а календарь — только будущие матчи.
"""
import argparse
import asyncio
import html as htmllib
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp

import rhl_media
import rhl_protocol
from league import LEADERS_FILE, PAUSE, SETTLE_DAYS, USER_AGENT

BASE = Path(__file__).parent
TZ = ZoneInfo("Europe/Moscow")
SITE = "https://rhl.fhr.ru"
STORE = BASE / "rhl_site.json"
CLIPS = BASE / "clips.json"   # голы по табло от службы clips (ADR-030), кладёт шаг Pages. Нужен один ключ: какие
                              # записи VK удалены, чтобы спросить вкладку «Видео» заново (этап 0.3 плана)
MAX_PAGES = 24          # страниц матч-центра за запуск, не больше: остальные — в следующий час
MAX_VIDEO = 12          # вкладок «Видео» за запуск (rhl_media.py, ADR-019, раздел 7)
REPORT_EVERY = timedelta(hours=3)   # протокол перечитываем не чаще, пока лига может его поправить (SETTLE_DAYS)
REPORT_DAYS = 7         # протокола всё нет — ищем его столько дней после матча, каждый запуск
AUTHORS_WAIT = timedelta(days=1)    # сыгран, а авторов голов на карточке нет — перечитываем её столько
UNPARSED = BASE / "probe" / "rhl_protocol"   # неразобранные вкладки «Протокол»: задание Pages выкладывает их артефактом
LEADERS_EVERY = timedelta(hours=6)  # лидеров перечитываем не чаще, если за запуск не пришло новых протоколов

# статус карточки на сайте → статус матча (ADR-012, раздел 1)
STATUS = {"not-started": "sched", "started": "live", "finished": "final",
          "postponed": "moved", "moved": "moved", "canceled": "off", "cancelled": "off"}
MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7,
          "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}

LINK_RE = re.compile(r'/matchcenter/(\d+)/(\d+)/')
NUM_RE = re.compile(r"№(?:&nbsp;|\s)*(\d+)")
LOGO_RE = re.compile(r"/team/\d+_\d+/(\d+)\.png")


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _int(m) -> int | None:
    return int(m.group(1)) if m else None


def _card(part: str) -> dict | None:
    """Карточка будущего матча в теле календаря."""
    link, when = LINK_RE.search(part), re.search(r'datetime="([^"]+)"', part)
    names = [_text(x) for x in re.findall(r'match-card-team__name">(.*?)</div>', part, re.S)]
    if not link or len(names) != 2:
        return None
    header = re.search(r'class="desktop-tablet-content">(.*?)</div>', part, re.S)
    city = re.sub(r"^.*?№\s*\d+\s*", "", _text(header.group(1))) if header else ""
    status = re.search(r"match-card--status-([a-z-]+)", part)
    home_s, away_s = (re.search(rf'match-card__score-{side}">\s*(\d+)', part) for side in ("home", "guest"))
    logos = LOGO_RE.findall(part)
    return {"id": int(link.group(2)), "t": int(link.group(1)), "start": when.group(1) if when else None,
            "n": _int(NUM_RE.search(part)), "city": city or None, "home": names[0], "away": names[1],
            "status": STATUS.get(status.group(1)) if status else None,
            "score": [int(home_s.group(1)), int(away_s.group(1))] if home_s and away_s else None,
            "logos": logos[:2] if len(logos) >= 2 else None}


def _feed(href_t: str, href_id: str, cls: str, body: str) -> dict:
    """Матч ленты дней: статус и счёт есть, команды — только эмблемами и аббревиатурами."""
    status = re.search(r"--status-([a-z-]+)", cls)
    home_s, away_s = (re.search(rf'match-score--{side}">\s*(\d+)', body) for side in ("home", "guest"))
    city = re.search(r'match-city">(.*?)</div>', body, re.S)
    logos = LOGO_RE.findall(body)
    return {"id": int(href_id), "t": int(href_t), "n": _int(re.search(r'match-num">\s*№\s*(\d+)', body)),
            "city": _text(city.group(1)) if city else None,
            "status": STATUS.get(status.group(1)) if status else None,
            "score": [int(home_s.group(1)), int(away_s.group(1))] if home_s and away_s else None,
            "logos": logos[:2] if len(logos) >= 2 else None}


def parse_calendar(page: str) -> list[dict]:
    """Матчи страницы календаря: тело (будущие, с названиями) и лента дней (статус и счёт). Команды ленты
    достаются по эмблемам из тела календаря. Один матч — одна запись, лента главнее по статусу и счёту."""
    games: dict[int, dict] = {}
    for part in page.split('class="calendar__match"')[1:]:
        c = _card(part)
        if c:
            games[c["id"]] = c
    by_logo = {}
    for c in games.values():
        if c["logos"]:
            by_logo.setdefault(c["logos"][0], c["home"])
            by_logo.setdefault(c["logos"][1], c["away"])
    for m in re.finditer(r'<a\s+href="/matchcenter/(\d+)/(\d+)/[^"]*"\s+class="(matches-feed__match[^"]*)"', page):
        body = page[m.end():page.find("</a>", m.end())]
        f = _feed(m.group(1), m.group(2), m.group(3), body)
        g = games.setdefault(f["id"], {"id": f["id"], "t": f["t"], "start": None, "home": None, "away": None})
        for k in ("n", "city", "status", "score", "logos"):
            if f.get(k) is not None:
                g[k] = f[k]
        if not g.get("home") and f["logos"]:
            g["home"], g["away"] = by_logo.get(f["logos"][0]), by_logo.get(f["logos"][1])
    return sorted(games.values(), key=lambda g: g["id"])


def _goals(block: str) -> list[dict]:
    out = []
    for m in re.finditer(r'composition-player-num">\s*(\d*)\s*</div>\s*<a\s+href="/players/(\d+)/"[^>]*>(.*?)</a>'
                         r'\s*<div\s+class="matchcenter-hero__composition-player-min">\s*(\d+)', block, re.S):
        out.append({"no": int(m.group(1)) if m.group(1) else None, "player": int(m.group(2)),
                    "name": _text(m.group(3)), "min": int(m.group(4))})
    return out


def parse_match(page: str) -> dict | None:
    """Страница матч-центра: команды, счёт, статус по ходу, номер, начало по Москве, арена, голы."""
    names = [_text(x) for x in re.findall(r'matchcenter-hero__team-name">(.*?)</div>', page, re.S)]
    if len(names) != 2:
        return None
    score = re.search(r'matchcenter-hero__score-main">(.*?)</div>', page, re.S)
    raw = _text(score.group(1)) if score else ""
    pair = re.match(r"(\d+)\s*:\s*(\d+)\s*(.*)$", raw)
    status_name = re.search(r'matchcenter-hero__status-name">(.*?)</div>', page, re.S)
    status_name = _text(status_name.group(1)) if status_name else ""
    winner = re.search(r'class="matchcenter-hero\s+matchcenter-hero--winner-(home|guest)', page)
    kind = re.search(r'matchcenter-hero__score-type">(.*?)</div>', page, re.S)   # «Б» или «ОТ» под счётом
    tail = f"{pair.group(3) if pair else ''} {_text(kind.group(1)) if kind else ''} {status_name}".lower()
    decision = "Б" if re.search(r"\bб\b|буллит", tail) else "ОТ" if re.search(r"\bот\b|овертайм", tail) else None
    day = re.search(r'param-day">\s*(\d{1,2})\s+(\S+)\s*<', page)
    date_line = re.search(r'param-date">[^<]*?(\d{4}),\s*(\d{1,2}):(\d{2})', page)
    start = None
    if day and date_line and day.group(2) in MONTHS:
        start = datetime(int(date_line.group(1)), MONTHS[day.group(2)], int(day.group(1)),
                         int(date_line.group(2)), int(date_line.group(3)), tzinfo=TZ).isoformat()
    arena = re.search(r'param-arena-name">(.*?)</div>', page, re.S)
    city = re.search(r'param-arena-city">(.*?)</div>', page, re.S)
    # блоки голов хозяев и гостей: до следующего блока, мобильной копии или подвала карточки
    blocks = {side: re.search(rf'composition-block--players-{side}">(.*?)(?=<div\s+class="matchcenter-hero__composition-block|'
                              rf'<div\s+class="mobile-content|<div\s+class="matchcenter-hero__footer)',
                              page, re.S) for side in ("home", "guest")}
    if winner:
        status = "final"
    elif pair and status_name:
        status = "live"
    elif status_name and re.search(r"перенес", status_name.lower()):
        status = "moved"
    else:
        status = "sched"
    return {"home": names[0], "away": names[1], "status": status, "status_name": status_name or None,
            "score": [int(pair.group(1)), int(pair.group(2))] if pair else None, "decision": decision,
            "n": _int(re.search(r'param-num">\s*№\s*(\d+)', page)), "start": start,
            "arena": _text(arena.group(1)) if arena else None, "city": _text(city.group(1)) if city else None,
            "goals": {"home": _goals(blocks["home"].group(1)) if blocks["home"] else [],
                      "away": _goals(blocks["guest"].group(1)) if blocks["guest"] else []},
            "protocol": bool(re.search(r'<a href="[^"]*/protocol/" class="profile-menu__item', page))}

# ---------- хранилище и скачивание ----------


def load_store(path: Path = STORE) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data.get("games"), dict) else {"games": {}}
    except (FileNotFoundError, ValueError, AttributeError):
        return {"games": {}}


def save_store(store: dict, path: Path = STORE) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def merge(store: dict, rows: list[dict]) -> None:
    """Строки календаря — в хранилище: пустые поля не затирают известное."""
    for r in rows:
        g = store["games"].setdefault(str(r["id"]), {})
        for k, v in r.items():
            if v is not None and k != "logos":
                g[k] = v


def goals_done(g: dict) -> bool:
    """Авторов голов на карточке столько же, сколько голов в счёте. По ходу матча блок авторов пуст и
    заполняется после сирены — не всегда к тому разу, когда сайт уже назвал победителя."""
    score, goals = g.get("score"), g.get("goals") or {}
    if not isinstance(score, list) or len(score) != 2:
        return False
    return len(goals.get("home") or []) + len(goals.get("away") or []) >= int(score[0]) + int(score[1])


def need_page(g: dict, now: datetime) -> bool:
    """Страницу матча качаем, пока итог не подтверждён: идёт, сыгран без подробностей или без команд.
    Сыгранный без авторов голов — ещё AUTHORS_WAIT, пока не придёт протокол: он подробнее карточки."""
    if g.get("checked") == "final":
        return False
    if g.get("checked") == "authors":
        start = datetime.fromisoformat(g["start"]) if g.get("start") else None
        return not g.get("report") and start is not None and now - start <= AUTHORS_WAIT
    if g.get("status") in ("live", "final") or not g.get("home"):
        return True
    start = datetime.fromisoformat(g["start"]) if g.get("start") else None
    return start is not None and start <= now


def apply_page(g: dict, p: dict, seen: datetime | None = None) -> None:
    for k in ("home", "away", "n", "start", "arena", "city", "goals", "decision", "status_name", "protocol"):
        if p.get(k) is not None:
            g[k] = p[k]
    if p["score"] is not None or p["status"] != "sched":
        g["status"], g["score"] = p["status"], p["score"]
    if seen is not None:
        g["seen"] = seen.isoformat(timespec="seconds")
    g["checked"] = ("final" if goals_done(g) else "authors") if p["status"] == "final" else "page"


def live_state(g: dict) -> dict | None:
    """Идущий матч → живое в формате live/*.json (ADR-019, раздел 5): статус, период, счёт, когда видели.
    Часовая сборка кладёт его в league.json — мини-апп покажет «последний счёт» с давностью."""
    if g.get("status") != "live" or not isinstance(g.get("score"), list):
        return None
    name = (g.get("status_name") or "").lower()
    status, period = "live", None
    if "перерыв" in name:
        status = "break"
    m = re.search(r"([123])-й", name)
    if m:
        period = m.group(1)
    elif "овертайм" in name:
        period = "ОТ"
    elif "буллит" in name:
        period = "РБ"
    elif "окончен" in name or "заверш" in name:
        status = "ended"
    return {"status": status, "period": period, "clock": None,
            "score": {"home": int(g["score"][0]), "away": int(g["score"][1]), "decision": None},
            "seen": g.get("seen"), "src": "rhl.fhr.ru"}


def need_report(g: dict, now: datetime) -> bool:
    """Протокол сыгранного матча качаем, как только сайт сказал «сыгран», потом перечитываем раз в
    REPORT_EVERY, пока лига может его поправить (SETTLE_DAYS, как league.py). Нет протокола — ищем
    каждый запуск REPORT_DAYS дней."""
    if g.get("status") != "final" or not g.get("t") or not g.get("start"):
        return False
    age = now - datetime.fromisoformat(g["start"])
    if not g.get("report"):
        return age <= timedelta(days=REPORT_DAYS)
    at = g.get("report_at")
    return age <= timedelta(days=SETTLE_DAYS) and (not at or now - datetime.fromisoformat(at) >= REPORT_EVERY)


def season_code(start: datetime) -> str:
    """Сезон в адресе статистики: матч октября 2026 → «2026-2027», марта 2027 — тоже."""
    y = start.year if start.month >= 7 else start.year - 1
    return f"{y}-{y + 1}"


def write_leaders(store: dict, path: Path = LEADERS_FILE) -> bool:
    """Лидеры сезона из хранилища → leaders.json для build_data.leaders() (ADR-009). Пусто (первый день,
    сайт ещё не посчитал) — файл не трогаем: остаётся прошлый сезон из git."""
    top = store.get("leaders") or {}
    if not any((top.get("categories") or {}).values()):
        return False
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(top, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return True


async def _get(s: aiohttp.ClientSession, url: str) -> str:
    async with s.get(url) as r:
        r.raise_for_status()
        return await r.text()


async def fetch_reports(s: aiohttp.ClientSession, site: str, store: dict, now: datetime, budget: int) -> int:
    """Протоколы сыгранных матчей (вкладка «Протокол») → games[id]["report"]. Сначала те, у кого протокола
    ещё нет, потом давно перечитанные. Возвращает, у скольких матчей протокол появился впервые."""
    todo = sorted((g for g in store["games"].values() if need_report(g, now)),
                  key=lambda g: (bool(g.get("report")), g.get("report_at") or "", g["start"]))
    fresh = 0
    for g in todo[:max(budget, 0)]:
        await asyncio.sleep(PAUSE)
        try:
            html = await _get(s, f"{site}/matchcenter/{g['t']}/{g['id']}/protocol/")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logging.warning("протокол %s не скачался: %s", g["id"], e)
            continue
        g["report_at"] = now.isoformat(timespec="minutes")
        p = rhl_protocol.parse_protocol(html, int(g["id"]))
        if p:
            fresh += not g.get("report")
            g["report"] = p.to_json()
        else:
            logging.warning("протокол %s не разобран: %s", g["id"], rhl_protocol.why_not(html) or "незнакомая вёрстка")
            keep_unparsed(g["id"], html)
    return fresh


def keep_unparsed(game_id, html: str, folder: Path | None = None) -> None:
    """Вкладку «Протокол», которую не разобрали у сыгранного матча, — в probe/rhl_protocol/<id>.html:
    по ней видно, что сайт отдаёт до готового протокола, и из неё делается фикстура tests/."""
    folder = folder or UNPARSED
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{game_id}.html").write_text(html, encoding="utf-8")
    except OSError as e:
        logging.warning("протокол %s не сохранён: %s", game_id, e)


async def fetch_leaders(s: aiohttp.ClientSession, site: str, store: dict, now: datetime, fresh: int) -> None:
    """Лидеры сезона по показателям league.LEADER_CATS → store["leaders"]: после новых протоколов или раз
    в LEADERS_EVERY. Турнир и сезон — последнего сыгранного матча: в плей-офф это турнир плей-офф."""
    played = sorted((g for g in store["games"].values() if g.get("status") == "final" and g.get("start")),
                    key=lambda g: g["start"])
    if not played:
        return
    old = store.get("leaders") or {}
    if not fresh and old.get("updated") and now - datetime.fromisoformat(old["updated"]) < LEADERS_EVERY:
        return
    t, season = played[-1]["t"], season_code(datetime.fromisoformat(played[-1]["start"]))
    cats, name = {}, ""
    try:
        for cat in rhl_protocol.NOMINATIONS:
            await asyncio.sleep(PAUSE)
            html = await _get(s, rhl_protocol.leaders_url(site, season, t, cat))
            cats[cat] = rhl_protocol.parse_leaders(html)
            name = name or rhl_protocol.leaders_name(html)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logging.warning("лидеры не скачались: %s", e)
        return
    if any(cats.values()):
        store["leaders"] = {"site": site, "tournament": t, "name": name,
                            "updated": now.isoformat(timespec="minutes"), "categories": cats}


def gone_videos(path: Path = CLIPS) -> set[str]:
    """Ролики, которых больше нет в VK: у матча `status: gone` у службы clips (этап 0.3 плана). Нет файла —
    пусто: сборка без службы работает как раньше."""
    try:
        games = json.loads(path.read_text(encoding="utf-8"))["games"]
    except (OSError, ValueError, KeyError, TypeError):
        return set()
    if not isinstance(games, dict):
        return set()
    return {e["video"] for e in games.values()
            if isinstance(e, dict) and e.get("status") == "gone" and isinstance(e.get("video"), str)}


async def update(path: Path = STORE, site: str = SITE, now: datetime | None = None,
                 leaders_path: Path | None = LEADERS_FILE, clips_path: Path | None = CLIPS) -> dict:
    now = now or datetime.now(TZ)
    store = load_store(path)
    headers = {"User-Agent": USER_AGENT}
    async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=60),
                                     trust_env=True) as s:
        rows = parse_calendar(await _get(s, f"{site}/calendar/"))
        merge(store, rows)
        todo = [g for g in store["games"].values() if need_page(g, now)]
        pages = sorted(todo, key=lambda g: g.get("start") or "")[:MAX_PAGES]
        for g in pages:
            await asyncio.sleep(PAUSE)
            try:
                p = parse_match(await _get(s, f"{site}/matchcenter/{g['t']}/{g['id']}/"))
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logging.warning("матч %s не скачался: %s", g["id"], e)
                continue
            if p:
                apply_page(g, p, datetime.now(TZ))
        # протоколы — в остаток лимита страниц: счёт и «идёт» важнее
        fresh = await fetch_reports(s, site, store, now, MAX_PAGES - len(pages))
        await fetch_leaders(s, site, store, now, fresh)
        await update_media(s, store, site, now, gone_videos(clips_path) if clips_path else set())
    store["updated"] = now.isoformat(timespec="minutes")
    save_store(store, path)
    if leaders_path:
        write_leaders(store, leaders_path)
    return store


async def update_media(s: aiohttp.ClientSession, store: dict, site: str, now: datetime, gone=()) -> None:
    """«Смотреть» от лиги (rhl_media.py): страница «Трансляции» — раз за запуск, вкладка «Видео» — у матчей
    сегодня и завтра и у только что сыгранных, пока ссылка не найдётся. По одному запросу с паузой.
    gone — записи, удалённые из VK: их вкладку спрашиваем заново, лига могла выложить запись второй раз."""
    games = store["games"]
    await asyncio.sleep(PAUSE)
    try:
        cards = rhl_media.parse_translations(await _get(s, f"{site}/translations/"))
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logging.warning("страница трансляций не скачалась: %s", e)
        cards = []
    for c in cards:
        if str(c["id"]) in games:
            games[str(c["id"])]["translation"] = True
    todo = [g for g in games.values() if rhl_media.need_video(g, now, gone)]
    # сначала сегодняшние и завтрашние (нужна ссылка на эфир), потом сыгранные — кого дольше не спрашивали
    first = sorted((g for g in todo if g.get("status") != "final"), key=lambda g: g.get("start") or "")
    later = sorted((g for g in todo if g.get("status") == "final"), key=lambda g: g.get("video_asked") or "")
    for g in (first + later)[:MAX_VIDEO]:
        await asyncio.sleep(PAUSE)
        if g.get("status") == "final":
            g["video_asked"] = now.isoformat(timespec="seconds")
            g.pop("video_tries", None)
        try:
            v = rhl_media.parse_video(await _get(s, f"{site}/matchcenter/{g['t']}/{g['id']}/video/"))
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logging.warning("видео матча %s не скачалось: %s", g["id"], e)
            continue
        if v:
            g["video"], g["video_kind"] = v["url"], v["kind"]
        elif g.get("video"):   # ту запись удалили, а новой лига ещё не выложила: мёртвую ссылку не держим
            g.pop("video", None)
            g.pop("video_kind", None)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Матчи с сайта РХЛ → rhl_site.json (ADR-019)")
    ap.add_argument("--out", type=Path, default=STORE)
    ap.add_argument("--site", default=SITE)
    ap.add_argument("--leaders-out", type=Path, default=LEADERS_FILE, help="куда писать лидеров сезона (ADR-009)")
    args = ap.parse_args()
    store = asyncio.run(update(args.out, args.site.rstrip("/"), leaders_path=args.leaders_out))
    games = store["games"].values()
    done = [g for g in games if g.get("status") == "final" and g.get("score")]
    print(f"Сайт РХЛ: матчей {len(store['games'])}, сыграно со счётом {len(done)}, "
          f"с протоколом {sum(bool(g.get('report')) for g in games)}, "
          f"идёт {sum(g.get('status') == 'live' for g in games)}, с видео лиги {sum(bool(g.get('video')) for g in games)} "
          f"→ {args.out}")
    top = store.get("leaders") or {}
    if any((top.get("categories") or {}).values()):
        counts = ", ".join(f"{k} {len(v)}" for k, v in top["categories"].items())
        print(f"Лидеры «{top.get('name')}» на {top.get('updated')}: {counts} → {args.leaders_out}")
    else:
        print("У сезона на сайте ещё нет лидеров — leaders.json не тронут")
    for g in sorted(done, key=lambda g: g.get("start") or "")[-8:]:
        print(f"  {g.get('start', '')[:16]} №{g.get('n')} {g.get('home')} — {g.get('away')} "
              f"{g['score'][0]}:{g['score'][1]}{' ' + g['decision'] if g.get('decision') else ''}")


if __name__ == "__main__":
    main()

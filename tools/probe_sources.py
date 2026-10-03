"""Проверка живых источников с сервера (ADR-019, раздел 6). Запускается на VPS руками:

    cd /opt/rhl && venv/bin/python tools/probe_sources.py

Качает список дня онлайна КХЛ, по странице матча РХЛ и ВХЛ, главную и календарь сайта РХЛ, кладёт
их в probe/<дата-время>/ и печатает, что из каждой страницы разобрали адаптеры. Вывод короткий:
его и папку владелец пересылает разработчику, страницы становятся фикстурами tests/.
Запросы — по одному с паузой, всего около 20.
"""
import argparse
import asyncio
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_data  # noqa: E402
import khl_online  # noqa: E402
import league  # noqa: E402
import live  # noqa: E402

MAX_PAGES = 10   # страниц матчей онлайна за проверку, не больше
SHOW = 8         # строк примеров на источник


def _score(sc: dict | None) -> str:
    return f"{sc['home']}:{sc['away']}{' ' + sc['decision'] if sc.get('decision') else ''}" if sc else "—"


def _ids(teams, home, away) -> str:
    h, a = khl_online.match_teams(teams, home, away)
    return f"{h or '?'} — {a or '?'}"


async def probe(out_root: Path, site: str) -> None:
    now = live.now_msk()
    out = out_root / now.strftime("%Y-%m-%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    teams = build_data.load_teams()
    saved: list[str] = []
    online_times: dict[str, str] = {}

    async with live.session() as s:
        fetcher = live.Fetcher(s)

        async def get(name: str, url: str) -> str | None:
            try:
                text = await fetcher.get(url)
            except Exception as e:   # noqa: BLE001 — проверка идёт дальше
                print(f"   ОШИБКА {url}: {type(e).__name__}: {str(e)[:120]}")
                return None
            (out / name).write_text(text, encoding="utf-8")
            saved.append(name)
            return text

        print(f"Проверка источников {now:%d.%m.%Y %H:%M} МСК")
        print(f"1. Онлайн КХЛ, список дня: {khl_online.DAY_URL}")
        items: list[dict] = []
        day_html = await get("khl_day.html", khl_online.DAY_URL)
        if day_html:
            title = league.parse_html(day_html).find("title")
            items = khl_online.parse_day_list(day_html, teams)
            leagues: dict[str, int] = {}
            for it in items:
                leagues[it["league"] or "?"] = leagues.get(it["league"] or "?", 0) + 1
            print(f"   заголовок: {title.text()[:100] if title else '—'}")
            print(f"   дата {khl_online.parse_day_date(day_html) or '—'}, матчей {len(items)}, "
                  f"со временем {sum(1 for i in items if i['time'])}, со счётом {sum(1 for i in items if i['score'])}, "
                  f"лиги: {', '.join(f'{k} {v}' for k, v in leagues.items()) or '—'}")
            for it in items[:SHOW]:
                print(f"   {it['khl_id']} {it['time'] or '--:--'} [{it['league'] or '?'}] {it['home'] or '?'} — "
                      f"{it['away'] or '?'} · счёт {_score(it['score'])} · статус {it['status'] or '—'}")
            day = khl_online.parse_day_date(day_html) or now.date()
            for it in items:   # для сверки с календарём лиги
                h, a = khl_online.match_teams(teams, it["home"], it["away"])
                if h and a and it["time"]:
                    online_times[f"{day.isoformat()}|{h}|{a}"] = it["time"]
            if not items:
                head = re.sub(r"\s+", " ", day_html)[:200]
                print(f"   ссылок /online/<id>.html нет; начало страницы: {head}")

        print("2. Страницы матчей онлайна (лигу решает заголовок): РХЛ идёт или сыгран, РХЛ впереди, ВХЛ")
        names = live.team_names(teams)

        def hint(i: dict) -> str:
            if i["league"] == "ВХЛ":
                return "vhl"
            if i["league"] == "РХЛ" or live.mentions_team(names, i["text"]):
                return "rhl_on" if i["status"] in ("live", "break", "ended") or i["score"] else "rhl_sched"
            return "other"
        kinds_order = ["rhl_on", "rhl_sched", "vhl", "other"]
        need, fetched = {"rhl_on", "rhl_sched", "vhl"}, 0
        for it in sorted(items, key=lambda i: kinds_order.index(hint(i))):
            h = hint(it)
            if not need or fetched >= MAX_PAGES:
                break
            if h == "other":
                if not {"rhl_on", "rhl_sched"} <= need:   # чужие лиги — только пока ни одной страницы РХЛ
                    continue
            elif h not in need:
                continue
            fetched += 1
            page = await get(f"khl_match_{it['khl_id']}.html", it["url"])
            if not page:
                continue
            m = khl_online.parse_match(page, teams, it["khl_id"])
            t = m.get("title") or {}
            name = "РХЛ" if khl_online.is_rhl(t.get("league")) else t.get("league") or "?"
            if name == "РХЛ":
                need -= {"rhl_on"} if m.get("status") in ("live", "break", "ended") else {"rhl_sched"}
            elif name == "ВХЛ":
                need.discard("vhl")
            kinds: dict[str, int] = {}
            for e in m.get("events", []):
                kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
            print(f"   {it['khl_id']}: лига {t.get('league') or 'не разобрана'} · {t.get('stage') or '—'} · "
                  f"игра № {t.get('n') or '—'} · {t.get('date') or '—'} · {t.get('pair') or '—'} → "
                  f"{_ids(teams, t.get('home'), t.get('away'))}")
            print(f"      статус {m.get('status') or '—'}, период {m.get('period') or '—'}, минута {m.get('clock') or '—'}, "
                  f"счёт {_score(m.get('score'))}, событий {len(m.get('events', []))} "
                  f"({', '.join(f'{k} {v}' for k, v in kinds.items()) or '—'})")
            if name == "РХЛ" and it["time"]:
                h, a = khl_online.match_teams(teams, t.get("home"), t.get("away"))
                online_times[f"{t.get('date')}|{h}|{a}"] = it["time"]
        if not fetched:
            print("   страниц не открыли")
        elif "rhl_on" in need:
            print("   идущего или сыгранного матча РХЛ не нашли: запустите ещё раз во время матча")

        tomorrow = now.date() + timedelta(days=1)
        print(f"   список на завтра ({tomorrow:%d.%m}), варианты адреса:")
        for i, template in enumerate((f"{khl_online.DAY_URL}?date={{date}}", f"{khl_online.DAY_URL}?date={{dmy}}"), start=1):
            url = template.format(date=tomorrow.isoformat(), dmy=tomorrow.strftime("%d.%m.%Y"))
            html = await get(f"khl_day_tomorrow_{i}.html", url)
            if html:
                d = khl_online.parse_day_date(html)
                print(f"   {url} → дата {d or '—'}, матчей {len(khl_online.parse_day_list(html, teams))}"
                      + (f" — ПОДХОДИТ: ONLINE_TOMORROW_URL={template}" if d == tomorrow else ""))

        print(f"3. Сайт лиги: {site}")
        main_html = await get("rhl_main.html", f"{site}/")
        useful: list[str] = []
        if main_html:
            hrefs = [a.attrs.get("href") or "" for a in league.parse_html(main_html).find_all("a")]
            useful = [h for h in dict.fromkeys(hrefs)
                      if re.search(r"calendar|report|online|stat|match|game|raspis|schedule|idgame", h, re.I)]
            print(f"   ссылок {len(hrefs)}, про календарь и матчи: {', '.join(useful[:SHOW]) or 'нет'}")
        cal_html = await get("rhl_calendar.html", f"{site}/calendar/")
        tours = league.parse_tournaments(cal_html) if cal_html else []
        if not tours and main_html:   # календарь не по /calendar/ — пробуем первую ссылку с «calendar» с главной
            link = next((h for h in useful if "calendar" in h.lower()), None)
            if link:
                cal_html = await get("rhl_calendar_link.html", urljoin(f"{site}/", link))
                tours = league.parse_tournaments(cal_html) if cal_html else []
        print(f"   турниры: {'; '.join(f'{i} {n}' for i, n in tours[:4]) or 'не найдены'}")
        regular = [i for i, n in tours if "Регулярный" in n] or [i for i, _ in tours]
        tour = regular[0] if regular else None
        cal = await get(f"rhl_calendar_{tour}.html", f"{site}/calendar/{tour}/") if tour else cal_html
        if cal:
            played = league.parse_game_ids(cal, tour) if tour else []
            rows = league.parse_calendar_times(cal, tour)
            print(f"   турнир {tour or '?'}: сыгранных с протоколом {len(played)}, будущих {len(rows)}, "
                  f"со временем {sum(1 for r in rows if r['time'])}, с idgame {sum(1 for r in rows if r['idgame'])}")
            for r in rows[:SHOW]:
                print(f"   {r['date']} {r['time'] or '--:--'} №{r['n'] or '?'} {r['home']} — {r['away']} → "
                      f"{_ids(teams, r['home'], r['away'])} · idgame {r['idgame'] or '—'}")
            for r in rows:
                h, a = khl_online.match_teams(teams, r["home"], r["away"])
                key = f"{r['date']}|{h}|{a}"
                if key in online_times:
                    same = "совпадает" if online_times[key] == r["time"] else "РАЗНОЕ: календарь в местном времени?"
                    print(f"   время {key}: онлайн {online_times[key]}, календарь {r['time'] or '—'} — {same}")
            if played:
                p_html = await get(f"rhl_report_{played[0]}.html", f"{site}/report/{tour}/?idgame={played[0]}")
                p = league.parse_protocol(p_html, played[0]) if p_html else None
                if p_html:
                    print(f"   протокол {played[0]}: " + (f"№{p.n} {p.date} {p.time} {p.home} {p.home_score}:"
                                                         f"{p.away_score} {p.away}, голов {len(p.goals)}" if p else
                                                         "не разобран: вёрстка не как у nmhl.fhr.ru"))

    print(f"Сохранено {len(saved)} страниц в {out}")
    print("Пришлите этот вывод и папку разработчику: страницы станут фикстурами тестов.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Проверка онлайна КХЛ и сайта РХЛ с сервера (ADR-019, раздел 6)")
    ap.add_argument("--out", type=Path, default=ROOT / "probe", help="куда сохранить страницы, по умолчанию probe/")
    ap.add_argument("--site", default=live.LEAGUE_SITE, help=f"сайт лиги, по умолчанию {live.LEAGUE_SITE}")
    args = ap.parse_args()
    asyncio.run(probe(args.out, args.site.rstrip("/")))


if __name__ == "__main__":
    main()

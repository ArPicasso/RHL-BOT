"""Протокол матча и лидеры сезона с нового сайта лиги rhl.fhr.ru (ADR-001, ADR-019, раздел 2).

Сайт РХЛ открылся 03.10.2026 на новом движке, протоколы nmhl.fhr.ru (league.py) на нём не разбираются.
Данные те же, вёрстка другая:

- `/matchcenter/<турнир>/<id>/protocol/` — шапка матча (команды, счёт, «Б»/«ОТ», периоды, №, дата и время
  МСК, тренеры, судьи) и вкладки «Заброшенные шайбы», «Штраф», «Статистика игроков». Разбор отдаёт тот же
  `league.Protocol`, что и старый сайт: дальше build_data.py работает с ним как с results.json (ADR-008).
  Время в шапке московское («17:00 МСК»), у старого сайта было местное, поэтому `zone` — Москва.
  Зрителей на странице нет;
- `/stat/leaders/season/<сезон>/tournament/<турнир>/nomination/<номинация>/` — лидеры по одной номинации,
  строки как у `league.parse_leaders` (ADR-009).

Id игроков — из ссылок /players/<id>/, те же, что у nmhl.fhr.ru: hidden_players.json работает как раньше.
Разбор — регулярками по классам вёрстки, как rhl_site.py. Фикстуры — tests/fixtures/rhl_protocol_*.html.
"""
import html as htmllib
import re
from datetime import date

import league
from league import Goal, Penalty, Player, Protocol, Skater

MOSCOW = "Europe/Moscow"
MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7,
          "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}
ROLES = {"Вратари": "G", "Защитники": "D", "Нападающие": "F"}
SECTIONS = ("goal-scored", "penalties", "players-stats", "teams-stats")   # вкладки протокола: matchcenter-protocol-<…>

PAIR_RE = re.compile(r"(\d+)\s*:\s*(\d+)")
PLAYER_RE = re.compile(r'<a href="/players/(\d+)/" class="person-name-with-meta__name">(.*?)</a>\s*'
                       r'<div class="person-name-with-meta__meta">(.*?)</div>', re.S)
PARAM_RE = re.compile(r'col-param--([A-Za-z]+)[^"]*">(.*?)</td>', re.S)


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _int(v: str | None) -> int:
    return int(v) if v and re.fullmatch(r"-?\d+", v) else 0


def _players(chunk: str) -> list[tuple[Player, str]]:
    """Игроки куска страницы: «Абашкин<br/>Кирилл» и «# 21 (А)» → Player(21, "Абашкин Кирилл", id) и «А»."""
    out = []
    for m in PLAYER_RE.finditer(chunk):
        meta = _text(m.group(3))
        num, cap = re.search(r"#\s*(\d+)", meta), re.search(r"\((К|А)\)", meta)
        out.append((Player(int(num.group(1)) if num else None, _text(m.group(2)), int(m.group(1))),
                    cap.group(1) if cap else ""))
    return out


def _sections(page: str) -> dict[str, str]:
    """Вкладки протокола по порядку на странице: каждая — до начала следующей."""
    marks = sorted((page.find(f'class="matchcenter-protocol-{s}"'), s) for s in SECTIONS)
    marks = [(i, s) for i, s in marks if i >= 0] + [(len(page), "")]
    return {s: page[i:marks[k + 1][0]] for k, (i, s) in enumerate(marks[:-1])}


def _sides(block: str, cls: str) -> dict[str, str]:
    """Половины вкладки по командам: <cls>--home и <cls>--guest."""
    marks = sorted((block.find(f"{cls}--{side}"), side) for side in ("home", "guest"))
    marks = [(i, s) for i, s in marks if i >= 0] + [(len(block), "")]
    return {("away" if s == "guest" else s): block[i:marks[k + 1][0]] for k, (i, s) in enumerate(marks[:-1])}


def _tbody(block: str) -> list[str]:
    start = block.find("<tbody")
    return re.findall(r"<tr>(.*?)</tr>", block[start:block.find("</tbody>", start)], re.S) if start >= 0 else []


def _period(v: str) -> str:
    """Период гола как у старого сайта: «1»…«3», «ОТ», «РБ» (победный буллит)."""
    v = v.upper().replace("OT", "ОТ")
    return "ОТ" if v.isdigit() and int(v) > 3 else v


def _goals(block: str) -> tuple[Goal, ...]:
    """«Заброшенные шайбы»: номер, период, время, счёт после гола, состав («рав.», «бол.»), автор и
    до двух ассистентов. Команду даёт счёт: чей счёт вырос, та и забила."""
    rows = []
    for tr in _tbody(block):
        cell = {k: _text(v) for k, v in re.findall(r'col-param--([a-z]+)">(.*?)</td>', tr, re.S)}
        m = PAIR_RE.fullmatch(cell.get("score", ""))
        people = [(_players(x.split("<td")[0]) or [(None, "")])[0][0]
                  for x in tr.split('matchcenter-protocol-goal-scored__table-col-player-name">')[1:]]
        if not m or not cell.get("time") or not people or people[0] is None:
            continue
        score = (int(m.group(1)), int(m.group(2)))
        rows.append((score, _period(cell.get("per", "")), cell["time"], cell.get("state", ""), people[0],
                     tuple(p for p in people[1:] if p)))
    goals, prev = [], (0, 0)
    for score, period, time, strength, author, assists in sorted(rows, key=lambda r: sum(r[0])):
        team = "home" if score[0] > prev[0] else "away"
        prev = score
        goals.append(Goal(period, time, f"{score[0]}:{score[1]}", team, strength, author, assists))
    return tuple(goals)


def _penalties(block: str) -> tuple[Penalty, ...]:
    """«Штраф» — таблица на команду. Строки «Всего за период» пропускаем, командный штраф — без игрока."""
    out = []
    for team, part in _sides(block, "matchcenter-protocol-penalties__group").items():
        for tr in re.findall(r'<tr class="matchcenter-protocol-penalties__group-table-row">(.*?)</tr>', part, re.S):
            time = re.search(r'group-table__time">(.*?)</div>', tr, re.S)
            mins = re.search(r'group-table-col-min">\s*(\d+)\s*<', tr)
            why = re.search(r'group-table-penalty">(.*?)</div>', tr, re.S)
            time = _text(time.group(1)) if time else ""
            if not re.fullmatch(r"\d{1,3}:\d{2}", time) or not mins:
                continue
            who = _players(tr)
            out.append(Penalty(time, team, who[0][0] if who else None, int(mins.group(1)),
                               _text(why.group(1)) if why else ""))
    return tuple(sorted(out, key=lambda p: league._seconds(p.time)))


def _lineups(block: str) -> tuple[Skater, ...]:
    """«Статистика игроков»: команда → амплуа (h3) → таблица, колонки по классу (`col-param--goals`).
    Вратарь без времени на льду («-») в матче не играл. У полевых колонки «И» нет: в протоколе — сыгравшие."""
    out = []
    for team, part in _sides(block, "matchcenter-protocol-players-stats__group").items():
        for pos in part.split('<div class="matchcenter-protocol-players-stats__position">')[1:]:
            title = re.search(r"<h3[^>]*>(.*?)</h3>", pos, re.S)
            role = ROLES.get(_text(title.group(1))) if title else None
            if not role:
                continue
            for tr in _tbody(pos):
                who = _players(tr)
                if not who:
                    continue
                player, cap = who[0]
                v = {k: _text(x) for k, x in PARAM_RE.findall(tr)}
                if role == "G":
                    toi = "" if v.get("toi", "-") == "-" else v["toi"]
                    out.append(Skater(team, role, player, cap, played=bool(toi) or _int(v.get("sog")) > 0,
                                      assists=_int(v.get("a")), pim=_int(v.get("pim")),
                                      shots_against=_int(v.get("sog")), saves=_int(v.get("sv")), toi=toi,
                                      wins=_int(v.get("wins")), losses=_int(v.get("looses")),
                                      so_games=_int(v.get("sop")), shutouts=_int(v.get("so"))))
                else:
                    out.append(Skater(team, role, player, cap, goals=_int(v.get("goals")), assists=_int(v.get("a")),
                                      pim=_int(v.get("pim")), faceoffs=_int(v.get("fo")),
                                      faceoffs_won=_int(v.get("fow")), gwg=_int(v.get("gwg")),
                                      plus_minus=_int(v.get("pm")), shots=_int(v.get("sog")), ppg=_int(v.get("ppg")),
                                      shg=_int(v.get("shg")), otg=_int(v.get("otg")), so_winner=_int(v.get("sds"))))
    return tuple(out)


def _staff(page: str) -> tuple[tuple[str, str], tuple[str, ...], tuple[str, ...]]:
    """Тренеры хозяев и гостей, главные и линейные судьи из шапки матча. Номер судьи («31. Зимагулов
    Дамир») не берём — у старого сайта его тоже не было."""
    block = re.search(r'composition-block--staff">(.*?)(?:matchcenter-hero__footer|\Z)', page, re.S)
    coaches, people, titles = ("", ""), {}, []
    for row in (block.group(1) if block else "").split('matchcenter-hero__composition-staff-row">')[1:]:
        cols = row.split('matchcenter-hero__composition-staff-col">')[1:]
        if "/coaches/" in row:
            names = [re.search(r'/coaches/\d+/">(.*?)</a>', c, re.S) for c in cols[:2]]
            coaches = tuple(_text(n.group(1)) if n else "" for n in names) + ("", "")[len(names):]
        elif "staff-title" in row:
            titles = [_text(t) for t in re.findall(r'staff-title">(.*?)</div>', row, re.S)]
        elif "/officials/" in row:
            for title, col in zip(titles, cols):
                people[title] = tuple(re.sub(r"^\d+\.\s*", "", _text(n))
                                      for n in re.findall(r'/officials/\d+/">(.*?)</a>', col, re.S))
    return coaches[:2], people.get("Главные судьи", ()), people.get("Линейные судьи", ())


def why_not(html: str) -> str | None:
    """Почему страница не протокол: первая не найденная часть шапки или вкладки. None — всё на месте.
    Для журнала задания Pages: протокол сыгранного матча сайт отдаёт не сразу, а чего именно ему не
    хватает в первый час после сирены, по одному «не разобран» не понять (матч №11, 05.10.2026)."""
    day = re.search(r'param-day">\s*(\d{1,2})\s+(\S+)\s*<', html)
    checks = (
        (len(re.findall(r'matchcenter-hero__team-name">', html)) == 2, "нет двух команд в шапке"),
        (re.search(r'matchcenter-hero__score-main">\s*\d+\s*:\s*\d+', html), "нет счёта в шапке"),
        (re.search(r'class="matchcenter-hero\s+matchcenter-hero--winner-(?:home|guest)', html),
         "нет победителя в шапке: матч не окончен"),
        (re.search(r'param-num">\s*№\s*\d+', html), "нет номера матча"),
        (day and day.group(2) in MONTHS, "нет даты матча"),
        (re.search(r'param-date">[^<]*?\d{4},\s*\d{1,2}:\d{2}', html), "нет времени начала"),
        ('class="matchcenter-protocol"' in html, "нет вкладки протокола (matchcenter-protocol)"),
    )
    return next((why for ok, why in checks if not ok), None)


def parse_protocol(html: str, game_id: int) -> Protocol | None:
    """None — матч не окончен (нет победителя в шапке), протокола нет или вёрстка незнакомая: что именно,
    скажет why_not."""
    if why_not(html):
        return None
    names = [_text(x) for x in re.findall(r'matchcenter-hero__team-name">(.*?)</div>', html, re.S)]
    score = re.search(r'matchcenter-hero__score-main">\s*(\d+)\s*:\s*(\d+)', html)
    num = re.search(r'param-num">\s*№\s*(\d+)', html)
    day = re.search(r'param-day">\s*(\d{1,2})\s+(\S+)\s*<', html)
    when = re.search(r'param-date">[^<]*?(\d{4}),\s*(\d{1,2}:\d{2})', html)
    kind = re.search(r'matchcenter-hero__score-type">(.*?)</div>', html, re.S)
    kind = _text(kind.group(1)).lower() if kind else ""
    decision = "Б" if re.search(r"\bб\b|буллит", kind) else "ОТ" if re.search(r"\bот\b|\bot\b|овертайм", kind) else ""
    # счёт по периодам — из первой (настольной) копии табло, у мобильной та же
    board = html[html.find('class="match-score-by-periods"'):]
    board = board[:board.find('class="mobile-content"')]
    periods = [(int(a), int(b)) for a, b in re.findall(r'match-score-by-periods__score">\s*(\d+)\s*:\s*(\d+)', board)]
    if decision == "Б" and len(periods) == 4:
        periods.insert(3, (0, 0))   # сайт не пишет пустой овертайм перед буллитами, старый писал «0:0»
    tabs = _sections(html)
    coaches, referees, linesmen = _staff(html)
    return Protocol(
        game_id=game_id, n=int(num.group(1)),
        date=date(int(when.group(1)), MONTHS[day.group(2)], int(day.group(1))), time=when.group(2).zfill(5),
        attendance=None, home=names[0], away=names[1],
        home_score=int(score.group(1)), away_score=int(score.group(2)), decision=decision,
        periods=tuple(periods),
        goals=_goals(tabs.get("goal-scored", "")),
        penalties=_penalties(tabs.get("penalties", "")),
        lineups=_lineups(tabs.get("players-stats", "")),
        referees=referees, linesmen=linesmen, coaches=coaches, zone=MOSCOW)

# ---------- лидеры сезона (ADR-009) ----------

# показатель league.LEADER_CATS → номинация нового сайта
NOMINATIONS = {"pts": "scorers", "g": "snipers", "a": "assists", "pm": "plus_minus", "sv_pct": "goalies_sv",
               "pim": "penalty"}
# колонка нового сайта (`col-param--<…>`) → поле строки лидеров, как у league.LEADER_COLS
LEADER_PARAMS = {"matches": "gp", "goals": "g", "a": "a", "points": "pts", "pm": "pm", "pim": "pim", "gwg": "gwg",
                 "wins": "w", "looses": "l", "so": "so", "svPct": "sv_pct", "gaa": "gaa"}


def leaders_url(site: str, season: str, tournament: int, cat: str) -> str:
    return f"{site}/stat/leaders/season/{season}/tournament/{tournament}/nomination/{NOMINATIONS[cat]}/"


def parse_leaders(html: str) -> list[dict]:
    """Таблица лидеров одной номинации: место, игрок, клуб, амплуа, номер, цифры — как league.parse_leaders."""
    out = []
    for tr in _tbody(html[html.find('class="stats-leaders__nomination-table"'):]):
        rank = re.search(r'nomination-table-num">\s*(\d+)', tr)
        who = _players(tr)
        if not rank or not who:
            continue
        player = who[0][0]
        club = re.search(r'nomination-table-col-team-name">(.*?)</td>', tr, re.S)
        role = re.search(r'nomination-table-position">(.*?)</div>', tr, re.S)
        row: dict = {"rank": int(rank.group(1)), "name": player.name, "id": player.id,
                     "club": _text(club.group(1)) if club else "",
                     "role": league.ROLE_NAMES.get(_text(role.group(1)).lower(), "") if role else "",
                     "number": player.number}
        for k, v in PARAM_RE.findall(tr):
            if k == "toi":
                row["toi"] = _text(v)
            elif k in LEADER_PARAMS:
                row[LEADER_PARAMS[k]] = league._num(_text(v))
        out.append(row)
    return out


def leaders_name(html: str) -> str:
    """Сезон и стадия, как имя турнира у старого сайта: «26/27 | Регулярный чемпионат» — по нему
    build_data.leaders() подписывает сезон и плей-офф."""
    season = re.search(r"<title>[^<]*?(\d{2}/\d{2})", html)
    stage = re.search(r'id="filter-tournaments".*?switcher__item--selected"[^>]*>(.*?)</', html, re.S)
    playoff = bool(stage and "плей" in _text(stage.group(1)).lower())
    return f"{season.group(1) if season else ''} | {'Плей-офф' if playoff else 'Регулярный чемпионат'}"

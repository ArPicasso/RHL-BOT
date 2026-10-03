"""Протоколы матчей с сайта лиги: загрузка и разбор. См. docs/adr/001."""
import argparse
import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp

BASE = Path(__file__).parent
TZ = ZoneInfo("Europe/Moscow")
RESULTS_FILE = BASE / "results.json"
SETTLE_DAYS = 3   # столько дней после матча протокол ещё перезапрашиваем: лига может его поправить
DEFAULT_SITE = "https://nmhl.fhr.ru"
USER_AGENT = "ryazan-vdv-schedule-bot (+https://github.com/ArPicasso/bogdanov)"
PAUSE = 1.0

MONTHS = {m: i for i, m in enumerate(
    ["январь", "февраль", "март", "апрель", "май", "июнь", "июль",
     "август", "сентябрь", "октябрь", "ноябрь", "декабрь"], start=1)}

# ---------- мини-DOM поверх html.parser ----------

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}


class Node:
    def __init__(self, tag: str, attrs: dict, parent: "Node | None"):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: list["Node | str"] = []

    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def iter(self):
        for ch in self.children:
            if isinstance(ch, Node):
                yield ch
                yield from ch.iter()

    def find_all(self, tag: str | None = None, cls: str | None = None) -> list["Node"]:
        return [n for n in self.iter()
                if (tag is None or n.tag == tag) and (cls is None or cls in n.classes())]

    def find(self, tag: str | None = None, cls: str | None = None) -> "Node | None":
        found = self.find_all(tag, cls)
        return found[0] if found else None

    def text(self) -> str:
        parts = []

        def walk(n: Node):
            for ch in n.children:
                if isinstance(ch, str):
                    parts.append(ch)
                elif ch.tag == "br":
                    parts.append(" ")
                else:
                    walk(ch)
        walk(self)
        return re.sub(r"\s+", " ", "".join(parts)).strip()


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.cur = Node("root", {}, None)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse_html(html: str) -> Node:
    b = _TreeBuilder()
    b.feed(html)
    b.close()
    return b.root

# ---------- модель ----------


@dataclass(frozen=True)
class Player:
    number: int | None
    name: str
    id: int | None = field(default=None, compare=False)   # id на сайте лиги: /players/<id>/


@dataclass(frozen=True)
class Goal:
    period: str        # "1", "2", "3", "ОТ", "РБ" (победный буллит)
    time: str          # игровое время "18:06"
    score: str         # счёт после гола "0:1", хозяева первыми
    team: str          # "home" или "away"
    strength: str      # "рав.", "бол.", "мен.", "бул." или ""
    author: Player
    assists: tuple[Player, ...]


@dataclass(frozen=True)
class Penalty:
    time: str               # игровое время "35:29"
    team: str               # "home" или "away"
    player: Player | None   # None — командный штраф
    minutes: int
    reason: str


@dataclass(frozen=True)
class Skater:
    """Строка статистики игрока в протоколе матча (ADR-008). Вратарь — role "G"."""
    team: str          # "home" или "away"
    role: str          # "G" вратарь, "D" защитник, "F" нападающий
    player: Player
    captain: str = ""  # "К", "А" или ""
    played: bool = True
    goals: int = 0
    assists: int = 0
    pim: int = 0
    faceoffs: int = 0
    faceoffs_won: int = 0
    gwg: int = 0             # «ШП» — победная шайба по версии лиги
    shots_against: int = 0   # у вратарей
    saves: int = 0
    toi: str = ""            # время на льду, у вратарей
    # Поля для «Звена» (ADR-014, раздел 13). В старом results.json их нет — там они нули.
    plus_minus: int = 0      # «+/-»
    shots: int = 0           # «БВ» полевого — броски в створ (у вратаря «БВ» — shots_against)
    ppg: int = 0             # «ШБ» — шайбы в большинстве
    shg: int = 0             # «ШМ» — в меньшинстве
    otg: int = 0             # «ШО» — в овертайме
    so_winner: int = 0       # «РБ» — решающий буллит. Это не гол: в «Ш» он не входит
    wins: int = 0            # «В» у вратарей. В матче с буллитами у обоих 0, в ОТ победа бывает у сменщика
    losses: int = 0          # «П»
    so_games: int = 0        # «ИБ» — матч дошёл до буллитов
    shutouts: int = 0        # «И"0"»


@dataclass(frozen=True)
class Protocol:
    game_id: int
    n: int             # номер матча в календаре лиги, он же ключ в games.json
    date: date
    time: str          # начало: местное время арены, не МСК, если zone не задан
    attendance: int | None
    home: str
    away: str
    home_score: int
    away_score: int
    decision: str      # "", "ОТ" или "Б"
    periods: tuple[tuple[int, int], ...]
    goals: tuple[Goal, ...]
    penalties: tuple[Penalty, ...] = ()
    lineups: tuple[Skater, ...] = ()
    referees: tuple[str, ...] = ()
    linesmen: tuple[str, ...] = ()
    coaches: tuple[str, str] = ("", "")   # хозяев и гостей
    zone: str = ""     # пояс date и time: пусто — местное время арены (nmhl.fhr.ru), у rhl.fhr.ru — Europe/Moscow

    def to_json(self) -> dict:
        d = asdict(self)
        d["date"] = self.date.isoformat()
        return d

# ---------- разбор ----------

TITLE_RE = re.compile(r"Матч № (\d+)\.\s*(\d{1,2}) (\S+) (\d{4}),[^,]*,\s*(\d{1,2}:\d{2})")
SCORE_RE = re.compile(r"(\d+)\s*:\s*(\d+)\s*(ОТ|Б)?")
PAIR_RE = re.compile(r"(\d+):(\d+)")
PLAYER_RE = re.compile(r"^(?:(\d+)\.\s*)?(.+?)(?:\s*\(\d+\))?$")
PLAYER_HREF = re.compile(r"/players/(\d+)/")
CAPTAIN_RE = re.compile(r"\((К|А)\)\s*$")
ROLES = {"Вратари": "G", "Защитники": "D", "Нападающие": "F"}


def _player(cell: str, href: str = "") -> Player | None:
    m = PLAYER_RE.match(cell)
    if not cell or not m:
        return None
    pid = PLAYER_HREF.search(href or "")
    return Player(int(m.group(1)) if m.group(1) else None, m.group(2).strip(), int(pid.group(1)) if pid else None)


def _cell_player(td: Node) -> Player | None:
    a = td.find("a")
    return _player(td.text(), a.attrs.get("href", "") if a else "")


def _cells(tr: Node) -> list[Node]:
    return [td for td in tr.children if isinstance(td, Node) and td.tag == "td"]


def _goals(table: Node) -> tuple[Goal, ...]:
    goals, prev = [], (0, 0)
    for tr in table.find_all("tr"):
        tds = _cells(tr)
        cells = [td.text() for td in tds]
        if len(cells) < 8:
            continue
        m = PAIR_RE.fullmatch(cells[3])
        author = _cell_player(tds[5])
        if not m or not author:
            continue
        score = (int(m.group(1)), int(m.group(2)))
        team = "home" if score[0] > prev[0] else "away"
        prev = score
        assists = tuple(p for p in map(_cell_player, tds[6:8]) if p)
        goals.append(Goal(cells[1], cells[2], cells[3], team, cells[4], author, assists))
    return tuple(goals)


def _penalties(table: Node) -> tuple[Penalty, ...]:
    """Две колонки: слева хозяева, справа гости. Строки «Всего за период» пропускаем."""
    out = []
    for tr in table.find_all("tr"):
        if "report" in tr.classes():
            continue
        tds = _cells(tr)
        if len(tds) < 9:
            continue
        for team, (t, who, mins, why) in (("home", tds[0:4]), ("away", tds[5:9])):
            time = t.text()
            if not re.fullmatch(r"\d{1,3}:\d{2}", time) or not mins.text().isdigit():
                continue
            out.append(Penalty(time, team, _cell_player(who), int(mins.text()), why.text()))
    return tuple(sorted(out, key=lambda p: _seconds(p.time)))


def _seconds(t: str) -> int:
    m, s = t.split(":")
    return int(m) * 60 + int(s)


def _int(v: str) -> int:
    return int(v) if re.fullmatch(r"-?\d+", v) else 0


def _lineups(block: Node) -> tuple[Skater, ...]:
    """Блок «Статистика игроков»: h2 с командой, h3 с амплуа, таблица. Идём по порядку."""
    out, team, role, teams_seen = [], None, None, 0
    for n in block.iter():
        if n.tag == "h2" and "Статистика игроков" in n.text():
            team = "home" if teams_seen == 0 else "away"
            teams_seen += 1
        elif n.tag == "h3":
            role = ROLES.get(n.text())
        elif n.tag == "table" and "universal_table" in n.classes() and team and role:
            rows = n.find_all("tr")
            if not rows:
                continue
            head = [th.text() for th in rows[0].find_all("th")][1:]   # первая — номер и имя
            for tr in rows[1:]:
                tds = _cells(tr)
                if len(tds) < 2 + len(head):
                    continue
                name_td = tds[1]
                a = name_td.find("a")
                cap = CAPTAIN_RE.search(name_td.text())
                name = a.text() if a else CAPTAIN_RE.sub("", name_td.text()).strip()
                pid = PLAYER_HREF.search(a.attrs.get("href", "")) if a else None
                number = tds[0].text()
                player = Player(int(number) if number.isdigit() else None, name, int(pid.group(1)) if pid else None)
                v = {k: tds[2 + i].text() for i, k in enumerate(head)}
                if role == "G":
                    out.append(Skater(team, role, player, cap.group(1) if cap else "",
                                      played=_int(v.get("И", "")) > 0, assists=_int(v.get("А", "")),
                                      pim=_int(v.get("Штр", "")), shots_against=_int(v.get("БВ", "")),
                                      saves=_int(v.get("ОБ", "")), toi=v.get("ВП", "").replace("-", ""),
                                      wins=_int(v.get("В", "")), losses=_int(v.get("П", "")),
                                      so_games=_int(v.get("ИБ", "")), shutouts=_int(v.get('И"0"', ""))))
                else:
                    out.append(Skater(team, role, player, cap.group(1) if cap else "",
                                      played=_int(v.get("И", "")) > 0, goals=_int(v.get("Ш", "")),
                                      assists=_int(v.get("А", "")), pim=_int(v.get("Штр", "")),
                                      faceoffs=_int(v.get("Вбр", "")), faceoffs_won=_int(v.get("ВВбр", "")),
                                      gwg=_int(v.get("ШП", "")), plus_minus=_int(v.get("+/-", "")),
                                      shots=_int(v.get("БВ", "")), ppg=_int(v.get("ШБ", "")),
                                      shg=_int(v.get("ШМ", "")), otg=_int(v.get("ШО", "")),
                                      so_winner=_int(v.get("РБ", ""))))
    return tuple(out)


def _people(root: Node, label: str) -> tuple[str, ...]:
    """Судьи: абзац «<strong>Главные судьи:</strong> 10. Иванов Савелий <br> 30. Беляев Михаил»."""
    for p in root.find_all("p"):
        strong = p.find("strong")
        if strong and strong.text().startswith(label):
            names = []
            for ch in p.children:
                if isinstance(ch, str) and ch.strip():
                    names.append(re.sub(r"^\d+\.\s*", "", ch.strip()))
            return tuple(n for n in names if n)
    return ()


def _coaches(root: Node) -> tuple[str, str]:
    row = root.find("tr", "second_row")
    if not row:
        return ("", "")
    found = []
    for cls in ("first_column", "right_column"):
        td = row.find("td", cls)
        found.append(re.sub(r"^Тренер:\s*", "", td.text()) if td else "")
    return (found[0], found[1])


def parse_protocol(html: str, game_id: int) -> Protocol | None:
    """None — протокола нет (матч не сыгран) или вёрстка незнакомая."""
    root = parse_html(html)
    title, count = root.find("div", "games_title"), root.find("p", "count")
    teams = [h.text() for t in root.find_all("table", "matches_protocol_main") for h in t.find_all("h2")]
    if not title or not count or len(teams) < 2:
        return None
    t = TITLE_RE.search(title.text())
    s = SCORE_RE.fullmatch(count.text())
    month = MONTHS.get(t.group(3).lower()) if t else None
    if not t or not s or not month:
        return None

    aud = root.find("p", "games_title_more")
    aud_m = re.search(r"(\d+)", aud.text()) if aud else None
    detail = root.find("div", "detail_count")
    periods = tuple((int(a), int(b)) for a, b in PAIR_RE.findall(detail.text())) if detail else ()
    goals_table = root.find("table", "matches_goals")
    pen_table = root.find("table", "matches_penalty")
    stats = root.find("div", "matches_player_statistic")

    return Protocol(
        game_id=game_id,
        n=int(t.group(1)),
        date=date(int(t.group(4)), month, int(t.group(2))),
        time=t.group(5),
        attendance=int(aud_m.group(1)) if aud_m else None,
        home=teams[0],
        away=teams[1],
        home_score=int(s.group(1)),
        away_score=int(s.group(2)),
        decision=s.group(3) or "",
        periods=periods,
        goals=_goals(goals_table) if goals_table else (),
        penalties=_penalties(pen_table) if pen_table else (),
        lineups=_lineups(stats) if stats else (),
        referees=_people(root, "Главные судьи"),
        linesmen=_people(root, "Линейные судьи"),
        coaches=_coaches(root),
    )


def parse_game_ids(html: str, tournament: int) -> list[int]:
    """Только из таблицы календаря: в ленте вверху страницы — матчи всей лиги."""
    href = re.compile(rf"/report/{tournament}/\?idgame=(\d+)")
    ids = [int(m.group(1))
           for td in parse_html(html).find_all("td", "col_count")
           for a in td.find_all("a")
           if (m := href.search(a.attrs.get("href") or ""))]
    return list(dict.fromkeys(ids))


CAL_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})(?!\d)")
CAL_TIME_RE = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")   # «17:00», «09:30»; «1:10» — это счёт
CAL_SCORE_RE = re.compile(r"^\s*\d{1,2}\s*:\s*\d{1,2}\s*(ОТ|Б)?\s*$")   # «6:1», «5:4 Б», «1:10»; «17:00» — время
REPORT_HREF = re.compile(r"/report/(\d+)/\?idgame=(\d+)")
ONLINE_HREF = re.compile(r"online\.khl\.ru/online/(\d+)\.html")


def _cal_date(text: str) -> date | None:
    m = CAL_DATE_RE.search(text or "")
    if not m:
        return None
    year = int(m.group(3))
    try:
        return date(year + 2000 if year < 100 else year, int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _cal_time(text: str) -> str | None:
    m = CAL_TIME_RE.fullmatch((text or "").strip())
    return f"{m.group(1)}:{m.group(2)}" if m else None


def parse_calendar_times(html: str, tournament: int | None = None) -> list[dict]:
    """Будущие матчи календаря лиги: дата, время начала, хозяева, гости, номер матча и idgame.

    Вёрстку будущих игр сайта РХЛ мы не видели (ADR-019). Предположение: та же таблица, что у
    сыгранных (tests/fixtures/calendar_*.html), а на месте счёта — время «17:00»; время ищем ещё в
    ячейке с классом time и под датой. Сыгранные (счёт «6:1» или секция «Завершившиеся») пропускаем.
    Время — как на сайте, без перевода в МСК; не нашли — None. idgame — из ссылки на протокол или онлайн.
    """
    root = parse_html(html)
    out = []
    for block in root.find_all("div", "matches_list") or [root]:
        h2 = block.find("h2")
        finished = bool(h2 and "заверш" in h2.text().lower())
        for date_td in block.find_all("td", "date"):
            row = date_td.parent
            day = _cal_date((date_td.find("h4") or date_td).text())
            if row is None or day is None:
                continue
            under_date = [_cal_time(p.text()) for p in date_td.find_all("p")]
            for tr in row.find_all("tr"):
                team_td = tr.find("td", "col_team")
                if team_td is None:
                    continue
                names = [a.text() for a in team_td.find_all("a") if a.text()]
                if len(names) != 2:
                    names = [x.strip() for x in re.split(r"\s+[-–—]\s+", team_td.text())]
                if len(names) != 2 or not all(names):
                    continue
                count_td = tr.find("td", "col_count")
                count = count_td.text() if count_td else ""
                if finished or (CAL_SCORE_RE.match(count) and not _cal_time(count)):
                    continue
                time_cells = [_cal_time(td.text()) for td in tr.find_all("td") if any("time" in c for c in td.classes())]
                when = next((t for t in [_cal_time(count), *time_cells, *under_date] if t), None)
                idgame = None
                for a in tr.find_all("a"):
                    href = a.attrs.get("href") or ""
                    rep = REPORT_HREF.search(href)
                    if rep and (tournament is None or int(rep.group(1)) == tournament):
                        idgame = int(rep.group(2))
                    elif (onl := ONLINE_HREF.search(href)) and idgame is None:
                        idgame = int(onl.group(1))
                num_td = tr.find("td", "col_number")
                num = num_td.text() if num_td else ""
                out.append({"date": day.isoformat(), "time": when, "home": names[0], "away": names[1],
                            "idgame": idgame, "n": int(num) if num.isdigit() else None, "tournament": tournament})
    return out


def parse_club_ids(html: str, tournament: int) -> dict[str, int]:
    """Id клуба внутри сезона из фильтра календаря; каждый сезон он свой."""
    opts = re.findall(rf'value="/calendar/{tournament}/\d+/(\d+)/"[^>]*>([^<]+)', html)
    return {name.strip(): int(cid) for cid, name in opts if int(cid)}   # 0 — фильтр по месяцу


def parse_tournaments(html: str) -> list[tuple[int, str]]:
    """Турниры из выпадающего списка, новые первыми."""
    opts = re.findall(r'value="/calendar/(\d+)/"[^>]*>([^<]+)', html)
    return list(dict.fromkeys((int(i), name.strip()) for i, name in opts))

# ---------- лидеры лиги (ADR-009) ----------

LEADERS_FILE = BASE / "leaders.json"
LEADER_CATS = ("pts", "g", "a", "pm", "sv_pct", "pim")   # показатель в адресе /stat/leaders/<турнир>/<показатель>/
ROLE_NAMES = {"вратарь": "G", "защитник": "D", "нападающий": "F"}
LEADER_COLS = {"№": "number", "И": "gp", "Ш": "g", "А": "a", "О": "pts", "+/-": "pm", "Штр": "pim",
               "ШП": "gwg", "В": "w", "П": "l", "И\"0\"": "so", "ВП": "toi", "%ОБ": "sv_pct", "КН": "gaa"}


def _num(v: str) -> int | float | None:
    v = v.replace(",", ".")
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return float(v) if re.fullmatch(r"-?\d+\.\d+", v) else None


def parse_leaders(html: str) -> list[dict]:
    """Список лидеров лиги по одному показателю, как на сайте: место, игрок, клуб, цифры."""
    table = parse_html(html).find("table", "tablesorter")
    if not table:
        return []
    rows = table.find_all("tr")
    if not rows:
        return []
    head = [th.text() for th in rows[0].find_all("th")]
    out = []
    for tr in rows[1:]:
        tds = _cells(tr)
        if len(tds) != len(head) or not tds[0].text().isdigit():
            continue
        row: dict = {"rank": int(tds[0].text())}
        for h, td in zip(head[1:], tds[1:]):
            v = td.text()
            if h == "Игрок":
                a = td.find("a")
                pid = PLAYER_HREF.search(a.attrs.get("href", "")) if a else None
                row["name"], row["id"] = v, int(pid.group(1)) if pid else None
            elif h == "Клуб":
                row["club"] = v
            elif h == "Амплуа":
                row["role"] = ROLE_NAMES.get(v.lower(), "")
            elif h == "ВП":
                row["toi"] = v
            elif h in LEADER_COLS:
                row[LEADER_COLS[h]] = _num(v)
        if row.get("name"):
            out.append(row)
    return out

# ---------- загрузка ----------


async def _get(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url) as r:
        r.raise_for_status()
        return await r.text()


def _session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}, timeout=aiohttp.ClientTimeout(total=30),
                                 trust_env=True)


async def _tournament(s: aiohttp.ClientSession, site: str, tournament: int | None) -> tuple[int, str]:
    """Id и название турнира; без id — последний регулярный чемпионат из календаря лиги."""
    found = parse_tournaments(await _get(s, f"{site}/calendar/"))
    await asyncio.sleep(PAUSE)
    if tournament is not None:
        return tournament, dict(found).get(tournament, "")
    regular = [(i, name) for i, name in found if "Регулярный" in name]
    if not regular:
        raise RuntimeError("не нашёл регулярный чемпионат в календаре лиги")
    return regular[0]


async def fetch_protocols(site: str, tournament: int | None = None, club: str | None = None,
                          skip: set[int] = frozenset()) -> tuple[int, list[Protocol]]:
    """Протоколы турнира: всей лиги или одного клуба. Запросы идут по одному с паузой."""
    async with _session() as s:
        if tournament is None:
            tournament, _ = await _tournament(s, site, None)
        cal = await _get(s, f"{site}/calendar/{tournament}/")
        if club:
            clubs = {name: cid for name, cid in parse_club_ids(cal, tournament).items() if club in name}
            if len(clubs) != 1:
                raise RuntimeError(f"клуб «{club}» в турнире {tournament}: найдено {list(clubs)}")
            await asyncio.sleep(PAUSE)
            cal = await _get(s, f"{site}/calendar/{tournament}/0/{next(iter(clubs.values()))}/")

        protocols = []
        for gid in parse_game_ids(cal, tournament):
            if gid in skip:
                continue
            await asyncio.sleep(PAUSE)
            try:
                p = parse_protocol(await _get(s, f"{site}/report/{tournament}/?idgame={gid}"), gid)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logging.warning("протокол %s не скачался: %s", gid, e)
                continue
            if p and (not club or club in p.home or club in p.away):
                protocols.append(p)
        return tournament, protocols


Results = dict[str, dict[str, dict]]   # id турнира → номер матча → протокол


def load_results(path: Path = RESULTS_FILE) -> Results:
    """Номера матчей в плей-офф начинаются заново, поэтому раскладка по турнирам."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def save_results(results: Results, path: Path = RESULTS_FILE) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(results, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


async def update_results(site: str, tournament: int | None, club: str | None, path: Path) -> list[Protocol]:
    results = load_results(path)
    settled = datetime.now(TZ).date() - timedelta(days=SETTLE_DAYS)
    known = {r["game_id"] for games in results.values() for r in games.values()
             if date.fromisoformat(r["date"]) < settled}
    tournament, fresh = await fetch_protocols(site, tournament, club, skip=known)
    games = results.setdefault(str(tournament), {})
    for p in fresh:
        games[str(p.n)] = p.to_json()
    save_results(results, path)
    return fresh


async def fetch_leaders(site: str, tournament: int | None = None) -> dict:
    """Лидеры турнира по каждому показателю из LEADER_CATS (ADR-009)."""
    async with _session() as s:
        tournament, name = await _tournament(s, site, tournament)
        cats = {}
        for i, cat in enumerate(LEADER_CATS):
            if i:
                await asyncio.sleep(PAUSE)
            cats[cat] = parse_leaders(await _get(s, f"{site}/stat/leaders/{tournament}/{cat}/"))
    return {"site": site, "tournament": tournament, "name": name,
            "updated": datetime.now(TZ).isoformat(timespec="minutes"), "categories": cats}


def update_leaders(site: str, tournament: int | None, path: Path = LEADERS_FILE) -> dict | None:
    """None — у сезона ещё нет статистики: файл остаётся прежним, с прошлым сезоном."""
    data = asyncio.run(fetch_leaders(site, tournament))
    if not any(data["categories"].values()):
        return None
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return data


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(description="Скачать протоколы матчей с сайта лиги в results.json")
    ap.add_argument("--site", default=DEFAULT_SITE)
    ap.add_argument("--tournament", type=int, help="id турнира; по умолчанию — последний регулярный чемпионат")
    ap.add_argument("--club", help="часть названия клуба; по умолчанию — вся лига")
    ap.add_argument("--leaders", action="store_true", help="вместо протоколов — лидеры лиги в leaders.json (ADR-009)")
    ap.add_argument("--out", type=Path, help=f"по умолчанию {RESULTS_FILE.name} или {LEADERS_FILE.name}")
    args = ap.parse_args()
    if args.leaders:
        data = update_leaders(args.site, args.tournament, args.out or LEADERS_FILE)
        if data is None:
            print("У сезона ещё нет статистики — лидеры не обновлены")
        else:
            counts = ", ".join(f"{k} {len(v)}" for k, v in data["categories"].items())
            print(f"Лидеры турнира {data['tournament']} «{data['name']}»: {counts} → {args.out or LEADERS_FILE}")
        return
    args.out = args.out or RESULTS_FILE
    fresh = asyncio.run(update_results(args.site, args.tournament, args.club, args.out))
    for p in sorted(fresh, key=lambda p: p.date):
        print(f"{p.date} №{p.n} {p.home} {p.home_score}:{p.away_score} {p.decision} {p.away}".rstrip())
    print(f"Новых протоколов: {len(fresh)} → {args.out}")


if __name__ == "__main__":
    main()

"""Онлайн КХЛ (online.khl.ru): разбор списка матчей дня и страницы матча (ADR-019). Без сети.

Разметку настоящих страниц мы не видели — разбор терпимый к вёрстке: ищем по ссылкам, классам
и словам, а не по точному пути в дереве. Не нашёл — поля нет, лучше пусто, чем неправда.
Настоящие страницы сохраняет tools/probe_sources.py на сервере.
"""
import re
from datetime import date

from league import Node, parse_html

ONLINE = "https://online.khl.ru"
DAY_URL = f"{ONLINE}/online/"
MAX_EVENTS = 60   # событий в live/*.json не больше (ADR-019, раздел 5)

# Лига матча — по первой части лиги из заголовка страницы: «НМХЛ - Кубок Регионов» → «НМХЛ»
RHL_LEAGUES = {"рхл", "нмхл", "российская хоккейная лига"}
LEAGUE_WORDS = {"кхл": "КХЛ", "вхл": "ВХЛ", "мхл": "МХЛ", "нмхл": "НМХЛ", "рхл": "РХЛ", "жхл": "ЖХЛ",
                "континентальная хоккейная лига": "КХЛ", "высшая хоккейная лига": "ВХЛ",
                "молодежная хоккейная лига": "МХЛ", "российская хоккейная лига": "РХЛ",
                "женская хоккейная лига": "ЖХЛ"}
LEAGUE_CODES = {"khl": "КХЛ", "vhl": "ВХЛ", "mhl": "МХЛ", "nmhl": "НМХЛ", "rhl": "РХЛ", "whl": "ЖХЛ", "zhl": "ЖХЛ"}


def _month(word: str) -> int | None:
    """«окт», «октября», «Октябрь», «мая», «Май» → номер месяца."""
    w = word.lower().replace("ё", "е")[:3]
    return {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "мая": 5, "июн": 6, "июл": 7, "авг": 8,
            "сен": 9, "окт": 10, "ноя": 11, "дек": 12}.get(w)


def _date(day: str, month: str, year: str) -> date | None:
    m = _month(month)
    try:
        return date(int(year), m, int(day)) if m else None
    except ValueError:
        return None


def is_rhl(league_name: str | None) -> bool:
    if not league_name:
        return False
    head = re.split(r"\s+[-–—]\s+", league_name.strip())[0]
    return head.lower().replace("ё", "е") in RHL_LEAGUES


def match_url(khl_id: int) -> str:
    return f"{ONLINE}/online/{khl_id}.html"


def decode_html(body: bytes, content_type: str = "") -> str:
    """Кодировка из заголовка, затем из <meta>, затем utf-8 и cp1251: у сайтов КХЛ бывает и то, и другое."""
    found = re.search(r"charset=([\w-]+)", content_type or "", re.I) or \
        re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:4096], re.I)
    names = []
    if found:
        g = found.group(1)
        names.append(g.decode("ascii", "ignore") if isinstance(g, bytes) else g)
    for enc in [*names, "utf-8", "cp1251"]:
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", "replace")

# ---------- команды ----------


SUFFIX_RE = re.compile(r"\s+(?:[А-ЯЁA-Z]{1,3}|СПб)$")   # «Кристалл С», «Сокол ЧР», «Тайфун СПб»


def find_team(teams, raw: str | None) -> str | None:
    """Id команды из teams.json по написанию на сайте. teams — build_data.Teams.

    Префиксы «МХК», «ХК» снимает build_data.norm, суффиксы вроде «С» в «МХК Кристалл С» есть в
    aliases. Сверх этого: прежние названия, текст в скобках и короткий суффикс из заглавных букв.
    """
    if not raw or teams is None:
        return None
    raw = re.sub(r"\s+", " ", raw).strip(" \t-–—,.:")
    variants = [raw, re.sub(r"\s*\([^)]*\)\s*", " ", raw).strip()]
    variants.append(SUFFIX_RE.sub("", variants[-1]))
    for v in variants:
        if v and (found := teams.find_past(v)):
            return found
    return None


def match_teams(teams, home_raw: str | None, away_raw: str | None) -> tuple[str | None, str | None]:
    return find_team(teams, home_raw), find_team(teams, away_raw)


DASH_RE = re.compile(r"\s+[-–—]\s+|\s*[–—]\s*")


def split_pair(text: str, teams=None) -> tuple[str | None, str | None]:
    """«Хозяева-Гости» → две строки как на сайте.

    Тире с пробелами или длинное тире делят однозначно. Дефис внутри названий («Рязань-ВДВ»,
    «Металлург ВО-Гранит-Чехов») — перебираем позиции и проверяем половины через teams.
    Без teams делим только по единственному дефису. Не вышло — (None, None).
    """
    text = re.sub(r"\s+", " ", text or "").strip()
    if teams is not None and find_team(teams, text):   # «Рязань-ВДВ» — одна команда, не пара
        return None, None
    parts = [p.strip() for p in DASH_RE.split(text) if p.strip()]
    if len(parts) == 2:
        return parts[0], parts[1]
    if len(parts) != 1:
        return None, None
    cuts = [m.start() for m in re.finditer("-", text)]
    if teams is not None and cuts:
        scored = []
        for c in cuts:
            home, away = text[:c].strip(), text[c + 1:].strip()
            if home and away:
                scored.append((bool(find_team(teams, home)) + bool(find_team(teams, away)), home, away))
        best = max((s for s, _, _ in scored), default=0)
        top = [(h, a) for s, h, a in scored if s == best]
        if best:
            return top[0] if len(top) == 1 else (None, None)
    if len(cuts) == 1:
        home, away = text[:cuts[0]].strip(), text[cuts[0] + 1:].strip()
        if home and away:
            return home, away
    return None, None

# ---------- заголовок страницы матча ----------


TITLE_RE = re.compile(r"Хоккей\.\s*(?P<rest>.*?)\s*Игра\s+номер\s+(?P<n>\d+)\s+(?P<d>\d{1,2})\s+(?P<mon>[А-Яа-яЁё]+)\.?\s+"
                      r"(?P<y>\d{4})\s*:\s*(?P<pair>.+?)\s*(?:\((?:онлайн|online)[^)]*\))?\s*$", re.S)


def parse_title(title: str, teams=None) -> dict | None:
    """«Хоккей. ВХЛ. Регулярный чемпионат. Игра номер 113 27 сен 2026: Динамо-Алтай-Омские Крылья (онлайн
    трансляция)» → лига, стадия, номер игры в турнире, дата, хозяева и гости, как на сайте.
    Хозяева и гости — None, если пару не удалось поделить однозначно."""
    m = TITLE_RE.search(re.sub(r"\s+", " ", title or ""))
    if not m:
        return None
    parts = [p.strip() for p in m.group("rest").split(".") if p.strip()]
    if not parts:
        return None
    d = _date(m.group("d"), m.group("mon"), m.group("y"))
    home, away = split_pair(m.group("pair"), teams)
    return {"league": parts[0], "stage": ". ".join(parts[1:]) or None, "n": int(m.group("n")),
            "date": d.isoformat() if d else None, "home": home, "away": away, "pair": m.group("pair").strip()}

# ---------- общее: текст, классы, время, счёт, статус ----------


SKIP_TAGS = {"script", "style", "head", "title", "noscript", "template", "svg"}


def _text(node: Node, skip: set[int] = frozenset()) -> str:
    """Текст узла без скриптов и стилей. skip — id() узлов, которые не читаем."""
    parts = []

    def walk(n: Node):
        for ch in n.children:
            if isinstance(ch, str):
                parts.append(ch)
            elif ch.tag in ("br", "p", "div", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "span") and \
                    id(ch) not in skip and ch.tag not in SKIP_TAGS:
                parts.append(" ")
                walk(ch)
                parts.append(" ")
            elif ch.tag not in SKIP_TAGS and id(ch) not in skip:
                walk(ch)
    walk(node)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _tokens(node: Node) -> set[str]:
    """Слова из class и id: «match-score__home» → {"match", "score", "home"}."""
    raw = f"{node.attrs.get('class') or ''} {node.attrs.get('id') or ''}".lower()
    return set(t for t in re.split(r"[\s_\-]+", raw) if t)


def _has(node: Node, *words: str) -> bool:
    toks = _tokens(node)
    return any(w in toks for w in words)


def _nodes(nodes: list[Node]):
    for n in nodes:
        yield n
        yield from n.iter()


CLOCK_RE = re.compile(r"(?<![\d:])(\d{1,3}):([0-5]\d)(?![\d:])")
START_RE = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
START_STRICT_RE = re.compile(r"(?<![\d:])([01]\d|2[0-3]):([0-5]\d)(?![\d:])")   # в простом тексте: «17:00», «09:30»
PAIR_SCORE_RE = re.compile(r"^\s*(\d{1,2})\s*[:\-–]\s*(\d)\s*(ОТ|OT|Б|Б\.|SO|ПБ)?\s*$", re.I)
TEXT_SCORE_RE = re.compile(r"(?<![\d:])(\d{1,2})\s*:\s*(\d)(?![\d:])")   # «2:1», «10:2»; «2:10» не узнаём
DECISION = {"от": "ОТ", "ot": "ОТ", "б": "Б", "б.": "Б", "so": "Б", "пб": "Б"}

PERIOD_RE = re.compile(r"(?<!\d)([1-3])\s*-?\s*(?:й|ый|ий)?\s+период(?!а|ов)|период\s*([1-3])(?!\d)")
BARE_OT_RE = re.compile(r"(?<![А-Яа-яЁёA-Za-z])ОТ(?![А-Яа-яЁёA-Za-z])")
# Порядок важен: отменён и перенесён сильнее всего, «окончен» и «не начался» сильнее «периода».
# Текст в нижнем регистре, ё → е.
STATUS_RULES = [
    ("off", re.compile(r"отменен|отменена")),
    ("moved", re.compile(r"перенесен|перенесена")),
    ("ended", re.compile(r"(?:матч|игра)\s+(?:окончен|окончена|завершен|завершена)(?![а-я])|^\s*(?:окончен|завершен|"
                         r"финал|итог)(?![а-я])|(?<![а-я])окончен(?![а-я])|(?<![а-я])завершен(?![а-я])")),
    ("sched", re.compile(r"не\s+начал(?:ся|ась)|скоро\s+начало|до\s+начала")),
    ("shootout", re.compile(r"буллит|послематчные броски")),
    ("ot", re.compile(r"овертайм")),
    ("break", re.compile(r"перерыв")),
    ("period", PERIOD_RE),
]


def status_from_text(text: str, strict: bool = False) -> dict | None:
    """Статус по словам: {"status": ..., "period": ..., "decision": ...}. Ничего не нашёл — None.

    strict — для текста всей страницы: голое «ОТ» может быть заголовком колонки таблицы, а «1-й период,
    2-й период» — шапкой счёта по периодам. Тогда такие слова не считаем."""
    if not text:
        return None
    low = text.lower().replace("ё", "е")
    for name, rx in STATUS_RULES:
        m = rx.search(low)
        if name == "ot" and not m and not strict:
            m = BARE_OT_RE.search(text)
        if name == "period" and m and strict and len({a or b for a, b in PERIOD_RE.findall(low)}) > 1:
            m = None
        if not m:
            continue
        if name in ("off", "moved", "sched"):
            return {"status": name}
        if name == "ended":
            dec = "Б" if re.search(r"буллит", low) or re.search(r"\d\s*Б(?![А-Яа-яЁё])", text) else \
                "ОТ" if "овертайм" in low or (not strict and BARE_OT_RE.search(text)) else None
            return {"status": "ended", "decision": dec}
        if name == "shootout":
            return {"status": "live", "period": "РБ"}
        if name == "ot":
            if re.search(r"перерыв", low):
                return {"status": "break", "period": "3"}
            return {"status": "live", "period": "ОТ"}
        if name == "break":
            p = re.search(r"(?<!\d)([1-3])\s*-?\s*(?:й|ый|ий|го|ого)?\s+перерыв|перерыв\s+после\s+([1-3])", low)
            return {"status": "break", "period": (p.group(1) or p.group(2)) if p else None}
        if name == "period":
            return {"status": "live", "period": m.group(1) or m.group(2)}
    return None


def _score_pair(text: str) -> dict | None:
    m = PAIR_SCORE_RE.match(text or "")
    if not m:
        return None
    dec = DECISION.get((m.group(3) or "").lower())
    return {"home": int(m.group(1)), "away": int(m.group(2)), "decision": dec}


def _is_start_like(text: str) -> bool:
    """«17:00» — похоже на время начала: часы и минуты двумя цифрами, часы не больше 23. «1:10» — нет."""
    return bool(re.fullmatch(r"\s*([01]\d|2[0-3]):[0-5]\d\s*", text or ""))

# ---------- список матчей дня ----------


HREF_RE = re.compile(r"(?:https?://online\.khl\.ru)?/online/(\d+)\.html", re.I)
DAY_DATE_RES = [re.compile(r"([А-Яа-яЁё]+)\s+(\d{1,2}),\s*(\d{4})"),          # «Октябрь 03, 2026»
                re.compile(r"(\d{1,2})\s+([А-Яа-яЁё]+)\.?\s+(\d{4})")]         # «3 октября 2026»


def parse_day_date(html: str) -> date | None:
    """Дата списка по заголовку: «Список онлайн трансляций матчей Октябрь 03, 2026»."""
    root = parse_html(html)
    t = root.find("title")
    for text in [t.text() if t else "", *(h.text() for h in root.find_all("h1"))]:
        m = DAY_DATE_RES[0].search(text)
        if m and (d := _date(m.group(2), m.group(1), m.group(3))):
            return d
        m = DAY_DATE_RES[1].search(text)
        if m and (d := _date(m.group(1), m.group(2), m.group(3))):
            return d
    return None


def _ids_below(root: Node) -> dict[int, frozenset[int]]:
    """id(узла) → номера матчей по ссылкам внутри него."""
    out: dict[int, frozenset[int]] = {}

    def walk(n: Node) -> frozenset[int]:
        found: set[int] = set()
        if n.tag == "a" and (m := HREF_RE.search(n.attrs.get("href") or "")):
            found.add(int(m.group(1)))
        for ch in n.children:
            if isinstance(ch, Node):
                found |= walk(ch)
        out[id(n)] = frozenset(found)
        return out[id(n)]
    walk(root)
    return out


def _league_in(text: str) -> str | None:
    low = text.lower().replace("ё", "е")
    for word in sorted(LEAGUE_WORDS, key=len, reverse=True):
        if re.search(rf"(?<![а-я]){word}(?![а-я])", low):
            return LEAGUE_WORDS[word]
    return None


def _league_attrs(nodes: list[Node]) -> str | None:
    """Лига по классам, alt и title картинок: «league-rhl», alt="РХЛ". Адреса ссылок не читаем: в них khl.ru."""
    for n in _nodes(nodes):
        for t in _tokens(n):
            if t in LEAGUE_CODES:
                return LEAGUE_CODES[t]
        for attr in ("alt", "title"):
            if (v := n.attrs.get(attr)) and (found := _league_in(v)):
                return found
    return None


def _is_header(n: Node) -> bool:
    return n.tag in ("h1", "h2", "h3", "h4", "h5", "h6", "caption") or \
        _has(n, "title", "league", "tournament", "caption", "header", "head")


def _block(link: Node, ids: dict[int, frozenset[int]], khl_id: int) -> list[Node]:
    """Самый большой предок ссылки, где нет других матчей. Плоская вёрстка (всё в одном контейнере) —
    ссылка и соседи после неё до следующего матча: «Протон — Кристалл 17:00»."""
    node = link
    while node.parent is not None and node.parent.tag not in ("root", "html", "body") and \
            ids.get(id(node.parent)) == {khl_id}:
        node = node.parent
    nodes = [node]
    if node.parent is not None and _text(node) == _text(link):
        sibs = node.parent.children
        for ch in sibs[sibs.index(node) + 1:]:
            if isinstance(ch, Node) and ids.get(id(ch)):
                break
            if isinstance(ch, Node):
                nodes.append(ch)
            elif ch.strip():
                text_node = Node("span", {}, node.parent)
                text_node.children.append(ch)
                nodes.append(text_node)
    return nodes


def _names_from(nodes: list[Node], links: list[Node], cleaned: str, teams) -> tuple[str | None, str | None]:
    """Хозяева и гости: два узла с классом team, текст ссылки «А — Б» или весь блок без времени и счёта."""
    leaf = [n for n in _nodes(nodes) if any(t.startswith("team") and t != "teams" for t in _tokens(n))
            and not any(any(t.startswith("team") and t != "teams" for t in _tokens(c)) for c in n.iter())]
    names = [_clean_name(n.text()) for n in leaf]
    names = [x for x in names if x]
    if len(names) == 2:
        return names[0], names[1]
    for a in links:
        home, away = split_pair(_clean_name(a.text()), teams)
        if home and away:
            return home, away
    return split_pair(cleaned, teams)


def _clean_name(text: str) -> str:
    text = CLOCK_RE.sub(" ", text or "")
    text = re.sub(r"(?<![\d:])\d{1,2}\s*:\s*\d{1,2}(?![\d:])", " ", text)
    text = re.sub(r"(?i)онлайн|трансляция|текстовая|смотреть|протокол|подробнее", " ", text)
    return re.sub(r"\s+", " ", text).strip(" -–—|·,")


def parse_day_list(html: str, teams=None) -> list[dict]:
    """Матчи со страницы online.khl.ru/online/, по порядку на странице.

    У каждого: khl_id, url, text (текст блока), time («17:00» или None), score ({home, away, decision}
    или None), status (по словам блока или None), league (из блока или заголовка секции, или None),
    home и away — сырые названия или None. Лига отсюда — только подсказка: РХЛ ли матч, решает
    заголовок его страницы (ADR-019, раздел 4).
    """
    root = parse_html(html)
    ids = _ids_below(root)
    links: dict[int, list[Node]] = {}
    section: dict[int, str | None] = {}
    current = None
    for n in root.iter():
        if n.tag in SKIP_TAGS:
            continue
        if not ids.get(id(n)) and _is_header(n):
            text = _text(n)
            if len(text) < 80 and (found := _league_in(text) or _league_attrs([n])):
                current = found
        if n.tag == "a" and (m := HREF_RE.search(n.attrs.get("href") or "")):
            kid = int(m.group(1))
            links.setdefault(kid, []).append(n)
            section.setdefault(kid, current)

    out = []
    for kid, anchors in links.items():
        blocks = [_block(a, ids, kid) for a in anchors]
        nodes = max(blocks, key=lambda b: sum(len(_text(x)) for x in b))
        text = re.sub(r"\s+", " ", " ".join(_text(x) for x in nodes)).strip()
        out.append({"khl_id": kid, "url": match_url(kid), "text": text[:300], **_block_facts(nodes, text),
                    "league": _league_in(text) or _league_attrs(nodes) or section.get(kid)})
        home, away = _names_from(nodes, anchors, _strip_facts(text), teams)
        out[-1].update(home=home, away=away)
    return out


def _block_facts(nodes: list[Node], text: str) -> dict:
    """Время начала, счёт и статус из блока матча. Сомнение — None."""
    st = status_from_text(text)
    started = bool(st and st["status"] not in ("sched", "moved", "off"))
    start = score = None
    for n in _nodes(nodes):
        t = n.text()
        if not t or len(t) > 40:
            continue
        toks = _tokens(n)
        if toks & {"timer", "clock", "period"}:
            continue
        if start is None and toks & {"time", "start", "begin"} and (m := START_RE.search(t)):
            start = f"{int(m.group(1)):02d}:{m.group(2)}"
        elif score is None and toks & {"score", "count", "result", "check"}:
            if _is_start_like(t) and not started:
                start = start or f"{int(t.strip().split(':')[0]):02d}:{t.strip().split(':')[1]}"
            elif (sc := _score_pair(t)) is not None:
                score = sc
    if start is None and not started and score is None:
        found = START_STRICT_RE.findall(text)
        if len(found) == 1:
            start = f"{found[0][0]}:{found[0][1]}"
    if score is None and started:
        # Счёт в простом тексте — только у начавшегося матча и только однозначный: «12:34» — это минута
        cands = [m for m in TEXT_SCORE_RE.finditer(text)]
        if len(cands) == 1:
            m = cands[0]
            tail = text[m.end():m.end() + 4]
            dec = "ОТ" if re.match(r"\s*ОТ", tail) else "Б" if re.match(r"\s*Б\b", tail) else None
            score = {"home": int(m.group(1)), "away": int(m.group(2)), "decision": dec}
    if score is not None and st and st.get("decision") and not score.get("decision"):
        score["decision"] = st["decision"]
    return {"time": start, "score": score, "status": st["status"] if st else None}


def _strip_facts(text: str) -> str:
    """Текст блока без времени, счёта и слов статуса — остаются названия команд."""
    text = re.sub(r"(?i)\b(?:не\s+начал(?:ся|ась)|матч\s+окончен|окончен|заверш[её]н|перерыв|перенес[её]н|отмен[её]н|"
                  r"[1-3]\s*-?\s*(?:й|ый|ий)?\s*период|овертайм|буллиты|ОТ|РХЛ|ВХЛ|КХЛ|МХЛ|НМХЛ|ЖХЛ)\b", " ", text)
    return _clean_name(text)

# ---------- страница матча ----------


EVENT_ROW_TAGS = ("tr", "li", "div", "p", "dd", "article", "section")
EVENT_TIME_RE = re.compile(r"^\s*(\d{1,3}):([0-5]\d)\b\s*[-–—.:|]?\s*(.*)$", re.S)


def _event_rows(root: Node) -> list[tuple[Node, str, str]]:
    """Строки текстовой трансляции: самые внутренние узлы, чей текст начинается с игрового времени
    «05:12 Гол…». Берём родителя, у которого таких строк-детей больше всего: это лента. Строки со
    ссылками на онлайн других матчей — это список матчей сбоку, не лента."""
    groups: dict[int, list[tuple[Node, str, str]]] = {}

    def walk(n: Node) -> bool:
        below = False
        for ch in n.children:
            if isinstance(ch, Node) and ch.tag not in SKIP_TAGS:
                below = walk(ch) or below
        if below or n.tag not in EVENT_ROW_TAGS or n.parent is None:
            return below
        m = EVENT_TIME_RE.match(_text(n))
        if not m or not re.search(r"[А-Яа-яЁёA-Za-z]{3}", m.group(3)):
            return False
        if any(a.tag == "a" and HREF_RE.search(a.attrs.get("href") or "") for a in n.iter()):
            return True
        groups.setdefault(id(n.parent), []).append((n, f"{int(m.group(1)):02d}:{m.group(2)}", m.group(3).strip()))
        return True
    walk(root)
    if not groups:
        return []
    best = max(groups.values(), key=len)
    return best if len(best) >= 2 else []


def _secs(t: str) -> int:
    m, s = t.split(":")
    return int(m) * 60 + int(s)


def _kind(text: str) -> str:
    low = text.lower().replace("ё", "е")
    if re.search(r"(?<![а-я])гол(?![а-я])|шайб[ау]\s+(?:забр|в\s+ворот)|забросил|забил", low):
        return "goal"
    if re.search(r"удален|штраф|\d+\s*мин(?:ут|\.|\b)", low):
        return "penalty"
    if re.search(r"(?:начал|оконча|конец|заверш|старт)\w*\s+(?:\S+\s+){0,2}(?:период|матч|игр|овертайм|перерыв)|"
                 r"(?:период|матч|овертайм)\w*\s+(?:начал|оконч|заверш)|перерыв", low):
        return "period"
    return "text"


def _event_period(text: str) -> str | None:
    low = text.lower()
    if re.search(r"буллит", low):
        return "РБ"
    if re.search(r"овертайм", low):
        return "ОТ"
    m = re.search(r"(?<!\d)([1-3])\s*-?\s*(?:й|ый|ий|го|ого)?\s+период", low)
    return m.group(1) if m else None


def _side(text: str, home: str | None, away: str | None, teams) -> str | None:
    """Чья команда упомянута в событии: ровно одна — её сторона."""
    def names(raw):
        out = {raw} if raw else set()
        tid = find_team(teams, raw) if teams is not None else None
        if tid:
            out |= {n for n, i in teams.by_name.items() if i == tid}
        return {re.sub(r"^(мхк|хк)\s+", "", n.lower().replace("ё", "е")) for n in out if n}
    low = text.lower().replace("ё", "е")
    hits = [side for side, raw in (("home", home), ("away", away))
            if any(n and re.search(rf"(?<![а-я]){re.escape(n)}(?![а-я])", low) for n in names(raw))]
    return hits[0] if len(hits) == 1 else None


def parse_events(root: Node, home: str | None = None, away: str | None = None, teams=None) -> list[dict]:
    """Лента событий в формате ADR-019: period, time, team, kind, text и score у голов. Новые в конце."""
    rows = _event_rows(root)
    if not rows:
        return []
    if _secs(rows[0][1]) > _secs(rows[-1][1]):   # лента «новые сверху» — переворачиваем
        rows = rows[::-1]
    cumulative = any(_secs(t) > 20 * 60 for _, t, _ in rows)   # «32:34» — время с начала матча
    events, period, prev, shootout = [], "1", (0, 0), False
    for _, t, text in rows:
        kind = _kind(text)
        if cumulative:
            s = _secs(t)
            period = "ОТ" if s > 60 * 60 else str(min(3, max(1, (s - 1) // 1200 + 1))) if s else "1"
        if kind == "period" and (p := _event_period(text)) and re.search(r"начал|старт", text.lower()):
            period = p
        if kind == "period" and "буллит" in text.lower():
            shootout = True
        if shootout:
            period = "РБ"
        ev = {"period": period, "time": t, "team": _side(text, home, away, teams), "kind": kind,
              "text": re.sub(r"\s+", " ", text)[:200]}
        if kind == "goal":
            cands = [m for m in re.finditer(r"(?<![\d:])(\d{1,2})\s*:\s*(\d{1,2})(?![\d:])", text)]
            if cands:
                m = cands[-1]
                score = (int(m.group(1)), int(m.group(2)))
                ev["score"] = f"{score[0]}:{score[1]}"
                if ev["team"] is None and score[0] + score[1] == prev[0] + prev[1] + 1:
                    ev["team"] = "home" if score[0] == prev[0] + 1 else "away"
                prev = score
        events.append(ev)
    return events[-MAX_EVENTS:]


def _subtrees(nodes) -> set[int]:
    return {id(x) for n in nodes for x in (n, *n.iter())}


def _status_from(root: Node, hidden: set[int]) -> dict | None:
    """Статус из узлов с классом status или state; period и stage — только если все такие узлы согласны:
    «period» бывает и у шапки таблицы счёта по периодам."""
    for words in ({"status", "state"}, {"period", "stage"}):
        found = []
        for n in root.iter():
            if id(n) in hidden or n.tag in SKIP_TAGS or not _tokens(n) & words:
                continue
            text = _text(n)
            if len(text) < 80 and (st := status_from_text(text)):
                found.append(st)
        if found and ("status" in words or all(f == found[0] for f in found)):
            return found[0]
    return None


def _score_from(root: Node, hidden: set[int]) -> dict | None:
    """Счёт табло: узел score/count/result «2:1», или два узла со счётом по командам."""
    single, digits = None, []
    for n in root.iter():
        if id(n) in hidden or n.tag in SKIP_TAGS:
            continue
        toks = _tokens(n)
        if not toks & {"score", "count", "result", "check"}:
            continue
        t = _text(n)
        if single is None and (sc := _score_pair(t)):
            single = sc
        elif re.fullmatch(r"\d{1,2}", t):
            digits.append(int(t))
    if single:
        return single
    if len(digits) == 2:
        return {"home": digits[0], "away": digits[1], "decision": None}
    return None


def _clock(root: Node, text: str, period: str | None, hidden: set[int]) -> str | None:
    """Минута матча от начала периода: узел timer/clock или «2-й период 12:34». Сквозное «32:34» → «12:34»."""
    found = None
    for n in root.iter():
        if id(n) not in hidden and _tokens(n) & {"timer", "clock", "gametime"} and (m := CLOCK_RE.search(_text(n))):
            found = m
            break
    if found is None:
        found = re.search(r"период\D{0,12}?(\d{1,3}):([0-5]\d)(?![\d:])", text.lower())
    if found is None:
        return None
    mm, ss = int(found.group(1)), int(found.group(2))
    base = {"1": 0, "2": 20, "3": 40, "ОТ": 60}.get(period or "")
    if base is not None and mm >= 20 and base <= mm <= base + 20:
        mm -= base
    if mm > 20:
        return None
    return f"{mm:02d}:{ss:02d}"


def parse_match(html: str, teams=None, khl_id: int | None = None) -> dict:
    """Страница матча online.khl.ru/online/<id>.html. Поля: title (parse_title), status, period, clock,
    score, events. Что не нашёл — поля нет.

    Статус, счёт и таймер не ищем в ленте событий и в блоках других матчей (список «матчи дня» сбоку):
    там свои «окончен» и «2:1». khl_id — номер этой страницы: ссылки на саму себя блоком другого матча
    не считаются."""
    root = parse_html(html)
    out: dict = {}
    t = root.find("title")
    title = parse_title(t.text(), teams) if t else None
    if title is None:
        for h in root.find_all("h1"):
            if title := parse_title(h.text(), teams):
                break
    if title:
        out["title"] = title
    rows = _event_rows(root)
    ids = _ids_below(root)
    others = [b for a in root.find_all("a") if (m := HREF_RE.search(a.attrs.get("href") or ""))
              and int(m.group(1)) != khl_id for b in _block(a, ids, int(m.group(1)))]
    hidden = _subtrees([rows[0][0].parent] if rows else []) | _subtrees(others)

    st = _status_from(root, hidden)
    page_text = _text(root, hidden)
    if st is None:
        st = status_from_text(page_text, strict=True)
    if st:
        out["status"] = st["status"]
        if st.get("period"):
            out["period"] = st["period"]

    home, away = (title or {}).get("home"), (title or {}).get("away")
    events = parse_events(root, home, away, teams)
    if events:
        out["events"] = events
    if out.get("status") not in (None, "sched", "moved", "off"):
        score = _score_from(root, hidden)
        if score is None:
            goals = [e for e in events if e.get("score")]
            if goals:
                h, a = goals[-1]["score"].split(":")
                score = {"home": int(h), "away": int(a), "decision": None}
        if score is not None:
            if out["status"] == "ended" and not score.get("decision"):
                score["decision"] = st.get("decision")
            out["score"] = score
    if out.get("status") == "live":
        clock = _clock(root, page_text, out.get("period"), hidden)
        if clock:
            out["clock"] = clock
    return out

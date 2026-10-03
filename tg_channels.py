"""Посты публичных Telegram-каналов клубов для листа «Главной» (ADR-015).

Источник — публичный предпросмотр `t.me/s/<канал>`: последние ~20 постов без входа и ключей.
Из поста берём только превью: до 180 знаков, одну картинку и ссылку на сам пост. Всё, что не
подходит ленте (реклама, букмекеры, дни рождения, трансляция матча по минутам, посты не о
молодёжке в канале взрослого клуба), отбрасывается здесь, полный текст дальше не уходит.
Для матч-центра (ADR-019, `matchday.py`) к превью добавлены внешние ссылки поста (`links`) и
строки со временем (`times`); короткие посты со ссылкой, которые лента не берёт, — в `extra` канала.

    python tg_channels.py            # все каналы из channels.json → channel_posts.json
    python tg_channels.py samara     # только каналы одного клуба, для проверки
"""
import argparse
import asyncio
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import aiohttp

from league import PAUSE, USER_AGENT

BASE = Path(__file__).parent
TZ = ZoneInfo("Europe/Moscow")
CHANNELS_FILE = BASE / "channels.json"
HIDDEN_FILE = BASE / "hidden_players.json"
OUT = BASE / "channel_posts.json"      # кэш сборки, не в git и не в webapp/
SITE = "https://t.me/s/"

PREVIEW = 180          # знаков превью, обрыв по слову (ADR-015)
TITLE_MAX = 90         # первая строка короче — заголовок поста
SHOWN_KINDS = {"club", "academy", "system", "league"}   # фан-каналы в первой версии не берём

# Маркеры молодёжной команды для каналов со scope "u21_only", если у канала в channels.json своих нет
U21_MARKERS = ("МХК", "молодёжн", "молодежн", "молодёжк", "молодежк", "РХЛ", "НМХЛ", "U21", "U-21")

AD_RE = re.compile(r"\berid\b|реклам|промокод", re.I)   # розыгрыш билетов у клуба — не реклама
BET_RE = re.compile(r"\bставк|\bставоч|букмекер|\bБК\b|фонбет|fonbet|winline|винлайн|лиг[аеи] ставок|betboom|бетбум"
                    r"|\bpari\b|\bпари\b|бетсити|betcity|мелбет|melbet|1xbet|1хбет|коэффициент|фрибет|freebet", re.I)
BIRTHDAY_RE = re.compile(r"\bд(ень|нём|нем|ня)\s+рождени|именинни|исполн(илось|яется|ится)\s+\d+"
                         r"|—\s*\d{2}\s*!|\bюбиле[йюяе]\b", re.I)   # «юбилей», но не «юбилейный сезон»
# Возраст игрока: дата рождения «25.02.2006» или год «2006 г.р.» (ADR-007). Годы — как у игроков U21 и младше
AGE_RE = re.compile(r"\b\d{1,2}\.\d{1,2}\.(199\d|20[01]\d)\b|\b(19|20)\d{2}\s*г\.?\s*р\b|\bгода\s+рождения|\bг\.\s*р\.", re.I)
# Мат и грубость — корни, этого хватает для каналов клубов; фан-каналы не берём
RUDE_RE = re.compile(r"\b(х[уy][йеёяи]|пизд|[её]б[аоу]?н|[её]бат|бля[дт]?|муда[кч]|пидор|гандон|залуп|сук[аи]\b)", re.I)
# Трансляция матча по ходу: «56’ Нарушение…», «ГОООЛ!», «Перерыв», «Счёт: 2:1». Через час это шум
LIVE_RE = re.compile(r"^\s*\d{1,2}\s*[’'′]|ГО{2,}Л|шайбу забросил|^\s*перерыв|конец\s+\d[-‑]?го\s+периода"
                     r"|\bсч[её]т:?\s*\d+\s*:\s*\d+|\bпериод\s*[:·—-]|\d+\s+секунд\w*\s+(осталось|до)"
                     r"|осталось\s+\d+\s+секунд|вчетвером|втро[её]м|в\s+меньшинств|в\s+большинств|удал[её]н"
                     r"|тайм-аут|вбрасывани", re.I | re.M)
LIVE_MAX = 300         # длинный пост со счётом — итог или анонс, не трансляция
# Совсем короткий пост о трансляции («Текстовая трансляция», «5 минут до старта. Ссылка») — тоже по ходу
LIVE_SHORT_RE = re.compile(r"трансляци|до\s+старта|стартовое\s+вбрасывание|начинаем\s+матч", re.I)
LIVE_SHORT = 80
MIN_LETTERS = 25       # пост без картинки короче — «С победой!», «Работаем»: в ленте без контекста не понять
# Хвост поста: «🐦 ХК «САМАРА» / 🇷🇺 СТРИЖИ В МАКС», «@rostovhc», «Мы в ВК | Мы в МАКС», хэштеги
TAIL_RE = re.compile(r"@\w+|\bMAX\b|\bМАКС\b|\bVK\b|\bВК\b|ВКонтакте|vk\.(com|ru)|t\.me/|#\w+|подписывай", re.I)
TAIL_MAX = 90
URL_RE = re.compile(r"(https?://|www\.)\S+|\b[\w-]+\.(ru|com|рф)/\S*", re.I)
IMG_RE = re.compile(r"^https://cdn\d*\.telesco\.pe/file/[\w\-./%]+$")   # белый список CDN Telegram

POST_SPLIT = '<div class="tgme_widget_message_wrap js-widget_message_wrap">'
POST_RE = re.compile(r'data-post="([\w]+)/(\d+)"')
TEXT_RE = re.compile(r'<div class="tgme_widget_message_text js-message_text"[^>]*>(.*?)</div>', re.S)
TIME_RE = re.compile(r'<a class="tgme_widget_message_date"[^>]*><time datetime="([^"]+)"')
PHOTO_RE = re.compile(r"tgme_widget_message_photo_wrap[^>]*background-image:url\('([^']+)'\)")
THUMB_RE = re.compile(r"tgme_widget_message_video_thumb\"[^>]*background-image:url\('([^']+)'\)")
VIDEO_RE = re.compile(r"tgme_widget_message_video_player|tgme_widget_message_roundvideo_player")
TITLE_RE = re.compile(r'<div class="tgme_channel_info_header_title"><span dir="auto">(.*?)</span>', re.S)
SPOILER_RE = re.compile(r"<tg-spoiler>.*?</tg-spoiler>|<span class=\"tg-spoiler\">.*?</span>", re.S)
HREF_RE = re.compile(r'href="([^"]+)"')
TEXT_URL_RE = re.compile(r"https?://[^\s<>\"«»]+|\b(?:[\w-]+\.)+(?:ru|com|be|рф|tv)/[^\s<>\"«»]+", re.I)
LINKS_MAX = 8
TIME_LINE_RE = re.compile(r"\d[:.]\d\d")   # строка со временем «17:00» или «17.00» — для matchday.py
TIMES_MAX, TIME_LINE_MAX = 4, 140
# Ссылки, с которыми пост не берём ни в ленту, ни в «Смотреть» (ADR-019, раздел 7): букмекеры и сайты
# прогнозов — по корню в имени хоста, пиратские агрегаторы трансляций — списком
BET_HOST_RE = re.compile(r"bet|ligastavok|(^|[.-])stavk|casino|kazino|prognoz|bookmaker|winline|parimatch|pin-?up"
                         r"|vulkan|(^|\.)(pari|leon|tennisi)\.ru$", re.I)
PIRATE_HOST_RE = re.compile(r"livetv|livesport\.ws|sport365|crackstreams|streameast|buffstreams|methstreams|hesgoal"
                            r"|vipleague|vipbox|sportsurge|totalsportek|rojadirecta|batmanstream|strims|footybite"
                            r"|myfootball|sportlemon|allsport-?live|smotrisport|hockey-?live|livehockey|sportsonline"
                            r"|streamonsport|freestreams|720pstream|nhl66|sportplus\.live", re.I)
# Посты, которые лента не берёт только за вид (короткие, без текста): со ссылкой они нужны «Смотреть»
QUIET = ("коротко", "без текста")


def plain(fragment: str) -> str:
    """Текст поста из HTML превью: переносы строк сохраняем, разметку, скрытое и ссылки — нет."""
    s = SPOILER_RE.sub("…", fragment)
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s).replace("\xa0", " ")
    return "\n".join(line.strip() for line in s.split("\n")).strip()


def host(url: str) -> str:
    """Имя хоста без www., в нижнем регистре; адрес не разобрался — пусто."""
    try:
        h = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return h.lower().removeprefix("www.")


def unwrap(url: str) -> str:
    """Переход ВК `vk.com/away.php?to=…` — сразу на адрес назначения: проверять надо его."""
    parts = urlsplit(url)
    if host(url) in ("vk.com", "vk.ru", "m.vk.com", "m.vk.ru") and parts.path == "/away.php":
        to = parse_qs(parts.query).get("to")
        if to:
            return to[0]
    return url


def link_reason(url: str) -> str | None:
    """Почему ссылку нельзя показывать: букмекер или пиратская трансляция. Можно — None."""
    h = host(unwrap(url))
    if BET_HOST_RE.search(h):
        return "букмекер"
    if PIRATE_HOST_RE.search(h):
        return "пиратская трансляция"
    return None


def post_links(block: str, text: str) -> list[str]:
    """Внешние ссылки поста по порядку: из текста, превью ссылки и кнопок. Переход ВК раскрываем, ссылки
    на сам Telegram отбрасываем, кроме прямого эфира канала (`t.me/<канал>?livestream`)."""
    hrefs = [html.unescape(u) for u in HREF_RE.findall(block)]
    bare = [u if re.match(r"https?://", u, re.I) else "https://" + u for u in TEXT_URL_RE.findall(text)]
    out = []
    for u in hrefs + bare:
        u = unwrap(u.rstrip(".,;:!?)»…"))
        if not re.match(r"https?://", u, re.I):
            continue   # «?q=#хэштег», «/s/канал?before=…», tg://
        h = host(u)
        if not h or h.endswith(("telegram.org", "telesco.pe")):
            continue
        if h in ("t.me", "telegram.me") and "livestream" not in urlsplit(u).query:
            continue   # сам пост, канал, репост
        if u not in out:
            out.append(u)
    return out[:LINKS_MAX]


def time_lines(text: str) -> list[str]:
    """Строки поста со временем («Начало в 17:00 по местному»): по ним matchday.py ищет время начала."""
    lines = [re.sub(r"\s+", " ", URL_RE.sub("", x)).strip() for x in text.split("\n") if TIME_LINE_RE.search(x)]
    return [cut(x, TIME_LINE_MAX) for x in lines if x][:TIMES_MAX]


def parse_page(page: str, handle: str) -> dict:
    """Название канала и посты страницы `t.me/s/<канал>` по порядку публикации."""
    m = TITLE_RE.search(page)
    title = plain(m.group(1)) if m else handle
    posts = []
    for block in page.split(POST_SPLIT)[1:]:
        pm, tm = POST_RE.search(block), TIME_RE.search(block)
        if not pm or not tm:
            continue
        text = TEXT_RE.search(block)
        photos = PHOTO_RE.findall(block)
        thumbs = THUMB_RE.findall(block)
        videos = len(VIDEO_RE.findall(block))
        image = next((u for u in photos + thumbs if IMG_RE.match(u)), None)
        body = plain(text.group(1)) if text else ""
        posts.append({
            "id": int(pm.group(2)),
            "url": f"https://t.me/{pm.group(1)}/{pm.group(2)}",
            "at": datetime.fromisoformat(tm.group(1)).astimezone(TZ).isoformat(timespec="minutes"),
            "text": body,
            "links": post_links(block, body),
            "image": image,
            "media": len(photos) + videos,
            "video": videos > 0,
            "forwarded": "tgme_widget_message_forwarded_from" in block,
            "service": bool(re.search(r'class="tgme_widget_message [^"]*service_message', block)),
            "poll": "tgme_widget_message_poll" in block,
        })
    return {"title": title, "posts": posts}


def strip_tail(text: str) -> str:
    """Отрезать подпись канала в конце поста: соцсети, упоминания, хэштеги. До двух абзацев."""
    paras = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    for _ in range(2):
        if len(paras) < 2:
            break
        last = paras[-1]
        lines = [x for x in last.split("\n") if x.strip()]
        only_tags = all(re.fullmatch(r"(#\w+\s*)+", x.strip()) for x in lines)
        if only_tags or (len(last) <= TAIL_MAX and TAIL_RE.search(last)):
            paras.pop()
        else:
            break
    return "\n\n".join(paras)


def cut(text: str, limit: int) -> str:
    """Обрезать по слову и поставить многоточие."""
    text = text.strip()
    if len(text) <= limit:
        return text
    head = text[:limit + 1]
    space = head.rfind(" ")
    head = head[:space] if space > limit * 0.6 else text[:limit]
    return head.rstrip(" ,.;:—–-") + "…"


def preview(text: str) -> tuple[str, str]:
    """Заголовок (первая строка, если она короткая и дальше есть текст) и текст превью — вместе ≤ PREVIEW."""
    text = URL_RE.sub("", strip_tail(text))
    text = re.sub(r"[ \t]+", " ", text).strip()
    first, _, rest = text.partition("\n")
    rest = " ".join(x.strip() for x in rest.split("\n") if x.strip())
    if rest and len(first) <= TITLE_MAX:
        title = first.strip().rstrip(":")
        return title, cut(rest, max(PREVIEW - len(title), 40))
    return "", cut(" ".join(x.strip() for x in text.split("\n") if x.strip()), PREVIEW)


def is_live(text: str) -> bool:
    if len(text) < LIVE_SHORT and LIVE_SHORT_RE.search(text):
        return True
    return len(text) < LIVE_MAX and bool(LIVE_RE.search(text))


def letters(text: str) -> int:
    return len(re.findall(r"[A-Za-zА-Яа-яЁё]", text))


def load_hidden_names(path: Path = HIDDEN_FILE) -> list[str]:
    """Фамилии скрытых по просьбе игроков (поле name в hidden_players.json): их посты не показываем."""
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    names = []
    for r in rows:
        name = r.get("name", "") if isinstance(r, dict) else ""
        names += [w for w in re.split(r"\s+", name.strip()) if len(w) >= 3][:1]   # первым словом идёт фамилия
    return names


def skip_reason(post: dict, channel: dict, hidden_names: list[str] = ()) -> str | None:
    """Почему пост не идёт в ленту, или None. Причина — для канарейки и тестов."""
    text = post["text"]
    if post.get("service"):
        return "служебное"   # «канал закрепил фото»
    if post["forwarded"]:
        return "репост"
    if post["poll"]:
        return "опрос"
    if not text and not post["media"]:
        return "пусто"
    if AD_RE.search(text):
        return "реклама"
    if BET_RE.search(text):
        return "букмекер"
    bad = next((r for r in map(link_reason, post.get("links", ())) if r), None)
    if bad:
        return bad   # ссылка на букмекера или пиратскую трансляцию — даже под безобидным текстом
    if BIRTHDAY_RE.search(text):
        return "день рождения"
    if AGE_RE.search(text):
        return "возраст"
    if RUDE_RE.search(text):
        return "грубость"
    low = text.lower()
    if any(re.search(rf"\b{re.escape(n.lower())}", low) for n in hidden_names):
        return "скрытый игрок"
    if channel.get("scope") == "u21_only":
        # свои маркеры канала заменяют общие: у «Северстали» «МХК» — это МХЛ, а не команда РХЛ
        markers = channel.get("markers") or U21_MARKERS
        if not any(m.lower() in low for m in markers):
            return "не о молодёжке"
    if not text:
        return "без текста"   # только картинка: превью без слов в ленте не понять
    if not post["media"] and letters(text) < MIN_LETTERS:
        return "коротко"
    return None


def entry(post: dict, channel: dict) -> dict:
    """Пост в кэше сборки: превью, картинка и ссылка на сам пост. Для матч-центра (ADR-019) — внешние
    ссылки и строки со временем; лента их не читает."""
    title, text = preview(post["text"])
    images = (channel.get("optout") or {}).get("level") != "images"
    out = {"id": post["id"], "url": post["url"], "at": post["at"], "title": title, "text": text,
           "media": post["media"], "video": post["video"]}
    if images and post["image"]:
        out["image"] = post["image"]
    if is_live(post["text"]):
        out["live"] = True
    if post.get("links"):
        out["links"] = post["links"]
    times = time_lines(post["text"])
    if times:
        out["times"] = times
    return out


def load_channels(path: Path = CHANNELS_FILE) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["channels"]


def wanted(channels: list[dict]) -> list[dict]:
    """Каналы, которые лента может показать: без фан-каналов и без тех, кто отказался совсем."""
    return [c for c in channels if c["kind"] in SHOWN_KINDS and (c.get("optout") or {}).get("level") != "all"]


def collect(parsed: dict, channel: dict, hidden_names: list[str] = ()) -> tuple[list[dict], dict[str, int]]:
    """Посты канала для ленты и счётчик отброшенных по причинам."""
    keep, dropped = [], {}
    for p in parsed["posts"]:
        why = skip_reason(p, channel, hidden_names)
        if why:
            dropped[why] = dropped.get(why, 0) + 1
        else:
            keep.append(entry(p, channel))
    return keep, dropped


def extras(parsed: dict, channel: dict, hidden_names: list[str] = ()) -> list[dict]:
    """Посты со ссылками, которые лента не берёт только за вид («Трансляция 👇» и ссылка): для «Смотреть»
    в матч-центре (ADR-019). Остальные фильтры — те же, что у ленты."""
    return [entry(p, channel) for p in parsed["posts"]
            if p.get("links") and skip_reason(p, channel, hidden_names) in QUIET]


async def fetch_all(channels: list[dict]) -> dict[str, str | None]:
    """HTML страниц каналов по одному с паузой; не открылся — None."""
    pages: dict[str, str | None] = {}
    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}, trust_env=True,
                                     timeout=aiohttp.ClientTimeout(total=30)) as s:
        for i, c in enumerate(channels):
            if i:
                await asyncio.sleep(PAUSE)
            try:
                async with s.get(SITE + c["handle"]) as r:
                    pages[c["handle"]] = await r.text() if r.status == 200 else None
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pages[c["handle"]] = None
    return pages


def build(channels: list[dict], pages: dict[str, str | None], hidden_names: list[str] = (),
          now: datetime | None = None) -> dict:
    now = now or datetime.now(TZ)
    out = {"fetched": now.isoformat(timespec="minutes"), "channels": {}}
    for c in channels:
        page = pages.get(c["handle"])
        parsed = parse_page(page, c["handle"]) if page else None
        if not parsed or not parsed["posts"]:
            out["channels"][c["handle"]] = {"ok": False}
            continue
        posts, dropped = collect(parsed, c, hidden_names)
        out["channels"][c["handle"]] = {"ok": True, "title": parsed["title"], "posts": posts,
                                        "last": parsed["posts"][-1]["at"], "dropped": dropped}
        extra = extras(parsed, c, hidden_names)
        if extra:
            out["channels"][c["handle"]]["extra"] = extra
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Посты каналов клубов для листа «Главной» (ADR-015)")
    ap.add_argument("club", nargs="?", help="id клуба из teams.json: собрать только его каналы")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    channels = wanted(load_channels())
    if args.club:
        channels = [c for c in channels if c["club"] == args.club]
    pages = asyncio.run(fetch_all(channels))
    data = build(channels, pages, load_hidden_names())
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    bad = [h for h, c in data["channels"].items() if not c["ok"]]
    n = sum(len(c.get("posts", [])) for c in data["channels"].values())
    print(f"Каналов: {len(channels)}, открылись: {len(channels) - len(bad)}, постов для ленты: {n} → {args.out}")
    for h in bad:
        print("Не открылся или пуст:", h)
    # канарейка (ADR-015): половина каналов не открылась — вёрстка t.me/s или доступ сломались
    if channels and len(bad) * 2 > len(channels):
        sys.exit("Больше половины каналов не открылись — проверить t.me/s")


if __name__ == "__main__":
    main()

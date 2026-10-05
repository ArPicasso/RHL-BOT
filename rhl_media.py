"""«Смотреть» с сайта лиги rhl.fhr.ru (ADR-019, раздел 7): видео матча и список трансляций. Без сети.

Сайт лиги показывает трансляцию матча на вкладке «Видео» матч-центра (`/matchcenter/<турнир>/<id>/video/`):
плеер VK Видео во фрейме `video_ext.php?oid=-X&id=Y`. Это видео группы клуба-хозяина, которое лига сама
вставила на свою страницу, — ссылка опубликована лигой. Для кнопки делаем из фрейма страницу ролика
`https://vk.com/video-X_Y`: она открывается в приложении VK и в браузере, без хеша встраивания.

Страница «Трансляции» (`/translations/`) — карточки объявленных прямых трансляций ближайших дней и
сыгранных: номер матча, команды, «04 окт | 17:00», ссылка на ту же вкладку «Видео». По карточке видно, что
трансляция будет, ещё до того, как во вкладке появится плеер.

Скачивает rhl_site.update, сюда приходят только страницы. Фикстуры — tests/fixtures/rhl_video_*.html и
rhl_translations.html, снимки задания «Снимок источников» 03.10.2026.
"""
import html as htmllib
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Moscow")
SITE = "https://rhl.fhr.ru"
SRC = "rhl.fhr.ru"
VIDEO_DAYS = 3      # вкладку «Видео» сыгранного матча спрашиваем не дольше трёх дней после него
VIDEO_EVERY = timedelta(hours=2)   # и не чаще раза в два часа: запись лига выкладывает и через часы после матча
                    # (06.10: из ~40 матчей 03–05.10 запись нашлась у 12 — спрашивали три раза за 45 минут)
VK_HOSTS = ("vk.com", "vk.ru", "m.vk.com", "m.vk.ru", "vkvideo.ru")

VIDEO_RE = re.compile(r'class="matchcenter-video__video">(.*?)</div>', re.S)
IFRAME_RE = re.compile(r'<iframe[^>]*?\ssrc="([^"]+)"', re.I)
CARD_RE = re.compile(r'<a\s+href="/matchcenter/(\d+)/(\d+)/video/"\s+class="translation-card[^"]*">(.*?)</a>', re.S)


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def embed_link(src: str) -> tuple[str, str] | None:
    """Адрес фрейма плеера → (видеохостинг, ссылка на страницу ролика). Не узнали — None.

    VK: `vk.ru/video_ext.php?oid=-187307324&id=456239889` → `https://vk.com/video-187307324_456239889`.
    На всякий случай и YouTube (`/embed/<id>` → youtu.be) и Rutube (`/play/embed/<id>` → `/video/<id>/`)."""
    src = htmllib.unescape(src.strip())
    if src.startswith("//"):
        src = "https:" + src
    try:
        p = urlsplit(src)
    except ValueError:
        return None
    if p.scheme not in ("http", "https"):
        return None
    h = _host(src)
    if h in VK_HOSTS and p.path == "/video_ext.php":
        q = dict(parse_qsl(p.query))
        oid, vid = q.get("oid", ""), q.get("id", "")
        if re.fullmatch(r"-?\d{1,12}", oid) and re.fullmatch(r"\d{1,12}", vid):
            return "VK Видео", f"https://vk.com/video{oid}_{vid}"
        return None
    m = re.fullmatch(r"/embed/([\w-]{6,20})", p.path)
    if h in ("youtube.com", "youtube-nocookie.com") and m:
        return "YouTube", f"https://youtu.be/{m.group(1)}"
    m = re.fullmatch(r"/play/embed/([0-9a-f]{20,40})/?", p.path)
    if h == "rutube.ru" and m:
        return "Rutube", f"https://rutube.ru/video/{m.group(1)}/"
    return None


def parse_video(page: str) -> dict | None:
    """Вкладка «Видео» матч-центра: плеер в блоке matchcenter-video__video. Плеера нет или он чужой — None.
    Фреймы вне блока (онлайн КХЛ на вкладке «Трансляция», виджеты) не берём."""
    block = VIDEO_RE.search(page)
    if not block:
        return None
    for src in IFRAME_RE.findall(block.group(1)):
        got = embed_link(src)
        if got:
            return {"kind": got[0], "url": got[1]}
    return None


def parse_translations(page: str) -> list[dict]:
    """Карточки страницы «Трансляции»: id матча на сайте лиги и турнир (из ссылки на вкладку «Видео»), номер,
    команды, «04 окт | 17:00» и подпись «… Прямая трансляция». Матч к карточке привязывается по id."""
    out = []
    for m in CARD_RE.finditer(page):
        body = m.group(3)
        names = [_text(x) for x in re.findall(r'translation-card__match-team-name">(.*?)</div>', body, re.S)]
        num = re.search(r'translation-card__match-num">\s*№\s*(\d+)', body)
        when = re.search(r'translation-card__date">(.*?)</div>', body, re.S)
        title = re.search(r'translation-card__name">(.*?)</div>', body, re.S)
        out.append({"t": int(m.group(1)), "id": int(m.group(2)), "n": int(num.group(1)) if num else None,
                    "home": names[0] if len(names) == 2 else None, "away": names[1] if len(names) == 2 else None,
                    "when": _text(when.group(1)) if when else None, "title": _text(title.group(1)) if title else None})
    return out


def need_video(g: dict, now: datetime) -> bool:
    """Спрашивать ли вкладку «Видео» матча хранилища rhl_site.json: ссылки ещё нет, матч сегодня или завтра,
    а сыгранный — VIDEO_DAYS дней, не чаще раза в VIDEO_EVERY (`video_asked`). Нашлась — больше не спрашиваем."""
    if g.get("video") or not g.get("t") or not g.get("id") or not g.get("start"):
        return False
    try:
        start = datetime.fromisoformat(g["start"])
    except ValueError:
        return False
    day, today = start.astimezone(TZ).date(), now.astimezone(TZ).date()
    if day > today + timedelta(days=1) or day < today - timedelta(days=VIDEO_DAYS):
        return False
    if g.get("status") == "final":
        try:
            return now - datetime.fromisoformat(g["video_asked"]) >= VIDEO_EVERY
        except (KeyError, TypeError, ValueError):
            return True
    return True


def watch_item(g: dict | None, site: str = SITE) -> dict | None:
    """Кнопка «Смотреть» от лиги для матча хранилища: ролик из вкладки «Видео», а пока его нет, но трансляция
    объявлена на странице «Трансляции», — сама вкладка на сайте лиги. Формат — ADR-019, раздел 5."""
    if not g:
        return None
    url = g.get("video")
    if isinstance(url, str) and url.startswith("https://"):
        return {"title": f"Трансляция лиги · {g.get('video_kind') or 'VK Видео'}", "url": url, "src": SRC}
    if g.get("translation") and g.get("t") and g.get("id"):
        return {"title": "Трансляция лиги · сайт РХЛ", "url": f"{site}/matchcenter/{g['t']}/{g['id']}/video/",
                "src": SRC}
    return None

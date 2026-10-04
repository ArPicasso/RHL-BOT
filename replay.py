"""Повторы голов (ADR-027): ссылка на запись трансляции лиги в VK с секунды, где забит гол. Без сети.

Отдельных роликов голов лига не публикует: на вкладке «Видео» матч-центра — вся трансляция (rhl_media.py).
VK открывает ролик с нужного места по `?t=14m32s`. Остаётся узнать, на какой секунде записи каждый гол.

Часы матча (12:34 во 2-м периоде) тут не помогают: время в хоккее останавливают, 20 минут периода — это
полчаса и больше. Зато служба live смотрит страницу матча раз в 30 секунд и записывает, во сколько по
часам сменился счёт: `at` у события `goal` в live/<дата>.json. Разница по часам между двумя голами — это и
разница между ними в записи, если трансляция шла без разрывов.

Не хватает одной связки «секунда записи ↔ время по часам». Её даёт админ в боте (/replay): ссылка на
запись и времена всех голов по порядку одним сообщением — тогда все повторы точные, — или время одного гола.
Отмеченный гол — опора: у него повтор точный, у остальных голов того же периода — опора плюс разница по часам.

Разметка 04.10.2026 (три матча, 13 голов) показала, почему только внутри периода: сайт лиги отмечает гол
с запозданием от нуля до полутора минут, а между периодами разница по часам и в записи расходилась на 5–7
минут (трансляцию прерывали в перерыве или сайт отметил гол сильно позже). Поэтому расчётный повтор
начинается за GUESS_LEAD до оценки, а из другого периода не считается. Точный — за EXACT_LEAD: видно атаку.
"""
import re
from datetime import datetime
from urllib.parse import parse_qsl, unquote, urlsplit
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Moscow")
EXACT_LEAD = 10    # секунд до отмеченного админом момента: видно, как развивалась атака
GUESS_LEAD = 60    # у расчётного: запоздание сайта лиги в одном периоде — до полутора минут (04.10.2026)
MAX_T = 6 * 3600   # трансляция матча не длиннее шести часов: больше — ошибка в ссылке
VK_HOSTS = ("vk.com", "vk.ru", "m.vk.com", "m.vk.ru", "vkvideo.ru", "m.vkvideo.ru")
SCORE_RE = re.compile(r"\d{1,2}:\d{1,2}")

_VIDEO_RE = re.compile(r"(?:video|live)(-?\d{1,12})_(\d{1,12})")   # запись эфира — vkvideo.ru/live-X_Y
_T_RE = re.compile(r"(?:(\d{1,2})h)?(?:(\d{1,3})m)?(?:(\d{1,5})s?)?")
_CLOCK_RE = re.compile(r"(?:(\d{1,2}):)?(\d{1,3}):(\d{2})")


def parse_t(v: str) -> int | None:
    """Время в записи из параметра VK `t`: «14m32s», «1h2m3s», «872», «872s». Не разобрали — None."""
    m = _T_RE.fullmatch((v or "").strip().lower())
    if not m or not any(m.groups()):
        return None
    h, mi, s = (int(x or 0) for x in m.groups())
    sec = h * 3600 + mi * 60 + s
    return sec if sec <= MAX_T else None


def parse_clock(text: str) -> int | None:
    """Время в записи, набранное руками: «14:32», «1:02:03». Не разобрали — None."""
    m = _CLOCK_RE.fullmatch((text or "").strip())
    if not m or int(m.group(3)) > 59 or m.group(1) and int(m.group(2)) > 59:
        return None
    sec = int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    return sec if sec <= MAX_T else None


def parse_link(text: str) -> tuple[str, int | None] | None:
    """Ссылка VK на ролик → (страница ролика `https://vk.com/video-X_Y`, секунда из `t` или None).

    Понимает vk.com и vkvideo.ru: `/video-X_Y?t=…`, запись эфира `/live-X_Y`, `/video?z=video-X_Y…&t=…`, плеер
    `video_ext.php?oid=&id=`. У записи эфира тот же номер ролика: лига публикует её как `vk.com/video-X_Y`.
    В тексте может быть что-то ещё: берём первую ссылку. Не VK или не ролик — None."""
    m = re.search(r"https?://[^\s<>\"']+", text or "")
    if not m:
        return None
    url = m.group(0)
    try:
        p = urlsplit(url)
    except ValueError:
        return None
    host = (p.hostname or "").lower().removeprefix("www.")
    if host not in VK_HOSTS:
        return None
    q = dict(parse_qsl(p.query))
    if p.path == "/video_ext.php":
        oid, vid = q.get("oid", ""), q.get("id", "")
        if not (re.fullmatch(r"-?\d{1,12}", oid) and re.fullmatch(r"\d{1,12}", vid)):
            return None
    else:
        v = _VIDEO_RE.search(unquote(p.path + "?" + p.query))
        if not v:
            return None
        oid, vid = v.groups()
    t = q.get("t")
    return f"https://vk.com/video{oid}_{vid}", parse_t(t) if t else None


def parse_times(text: str) -> list[int]:
    """Времена в записи из сообщения админа по порядку: «25:20», «1:08:03» — по строке или через пробел.
    Ссылки выбрасываем: в них тоже бывают цифры. Что не разобралось как время — пропускаем."""
    text = re.sub(r"https?://\S+", " ", text or "")
    out = []
    for tok in re.findall(r"(?<![\d:])(?:\d{1,2}:)?\d{1,3}:\d{2}(?![\d:])", text):
        t = parse_clock(tok)
        if t is not None:
            out.append(t)
    return out


def fmt_t(sec: int) -> str:
    """Секунды → параметр VK `t`: 872 → «14m32s», 3723 → «1h2m3s», 45 → «45s»."""
    sec = max(0, int(sec))
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    return (f"{h}h" if h else "") + (f"{m}m" if h or m else "") + f"{s}s"


def at_link(video: str, sec: int) -> str:
    return f"{video}?t={fmt_t(sec)}"


def _at(v) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    return dt if dt.tzinfo else None


def goals_of(game: dict) -> list[dict]:
    """Голы матча из live/<дата>.json, у которых есть счёт после гола: по нему гол узнаётся и в протоколе.
    Гол, замеченный после перерыва в опросе (`late`), без времени по часам: от него не считаем."""
    out = []
    for e in game.get("events") or []:
        if not isinstance(e, dict) or e.get("kind") != "goal":
            continue
        score = e.get("score")
        if not (isinstance(score, str) and SCORE_RE.fullmatch(score)) or any(x["score"] == score for x in out):
            continue
        out.append({"score": score, "team": e.get("team"), "text": e.get("text"),
                    "period": e.get("period"), "at": None if e.get("late") else _at(e.get("at"))})
    return out


def with_protocol(goals: list[dict], protocol: list[dict] | None) -> list[dict]:
    """Голы службы live, сверенные с протоколом: порядок, команда и период — по протоколу, время по часам —
    от службы. Гол, которого служба не узнала (счёт вырос у обеих сторон между опросами), — без времени.
    protocol — голы протокола без буллитов: [{score, team, period, author}]. Нет протокола — как есть."""
    if not protocol:
        return goals
    seen = {g["score"]: g for g in goals}
    out = []
    for p in protocol:
        if not (isinstance(p.get("score"), str) and SCORE_RE.fullmatch(p["score"])):
            continue
        g = seen.get(p["score"]) or {}
        out.append({"score": p["score"], "team": p.get("team") or g.get("team"), "text": g.get("text") or p.get("author"),
                    "period": p.get("period") or g.get("period"), "at": g.get("at")})
    return out


def place(goals: list[dict], anchors: dict[str, int]) -> list[dict]:
    """Секунда записи для каждого гола. anchors — {счёт после гола: секунда записи}, их отметил админ.

    Отмеченный гол — точно (за EXACT_LEAD). Остальные — от ближайшей по часам опоры того же периода: её
    секунда плюс разница по часам, минус GUESS_LEAD. Опоры в этом периоде нет, у гола нет времени по часам
    или периода — без повтора: между периодами запись и часы расходятся на минуты."""
    by = {g["score"]: g for g in goals}
    marks = [(by[s]["at"], t, by[s].get("period")) for s, t in anchors.items()
             if s in by and by[s].get("at") and by[s].get("period")]
    out = []
    for g in goals:
        same = [m for m in marks if g.get("period") and m[2] == g["period"]]
        if g["score"] in anchors:
            t, exact = anchors[g["score"]] - EXACT_LEAD, True
        elif g.get("at") and same:
            a, mt, _ = min(same, key=lambda x: abs((g["at"] - x[0]).total_seconds()))
            t, exact = mt + (g["at"] - a).total_seconds() - GUESS_LEAD, False
        else:
            continue
        if t > MAX_T:
            continue
        out.append({"score": g["score"], "team": g["team"], "t": max(0, round(t)), "exact": exact})
    return out


def entry(game: dict, video: str, anchors: dict[str, int], now: datetime, protocol: list[dict] | None = None) -> dict:
    """Запись матча в replays.json: ролик, опоры админа и готовые ссылки по голам."""
    goals = place(with_protocol(goals_of(game), protocol), anchors)
    for g in goals:
        g["url"] = at_link(video, g["t"])
    return {"video": video, "anchors": dict(sorted(anchors.items())), "goals": goals,
            "updated": now.astimezone(TZ).isoformat(timespec="seconds")}


def by_score(entry_: dict) -> dict[str, str]:
    """Счёт после гола → ссылка на повтор. Только https-ссылки VK: файл читает сборка мини-аппа."""
    out = {}
    for g in (entry_ or {}).get("goals") or []:
        url, score = g.get("url"), g.get("score")
        if isinstance(score, str) and isinstance(url, str) and re.fullmatch(r"https://vk\.com/video-?\d+_\d+\?t=\w+", url):
            out[score] = url
    return out

"""Можно ли ставить повторы голов без ручной опоры (ADR-027): что VK знает о начале записи. Запускать на VPS руками:

    cd /opt/rhl && venv/bin/python tools/probe_vk.py
    venv/bin/python tools/probe_vk.py --video https://vk.com/video-187307324_456239889   # один ролик, без опор

Опора админа в live/replays.json даёт настоящее начало записи по часам: время гола по службе live минус
секунда этого гола в записи. Пробник для каждого размеченного матча качает публичную страницу ролика VK и
плеер, находит в них все метки времени (unix) и длительность и печатает, на сколько каждая расходится
с настоящим началом. Метка, у которой расхождение на всех матчах в пределах полуминуты, — то, что нужно:
повторы можно считать сами. Если задан VK_TOKEN — ещё и ответ video.get.

В конце — сводка: поле → расхождение на каждом матче и вердикт по порогу ADR-028 (раздел 1): ±30 с на всех
размеченных матчах, матчей не меньше трёх. Её и присылать целиком.

Страницы кладёт в probe/vk/ — из них фикстуры tests/. Запросы по одному с паузой в секунду.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import replay  # noqa: E402

TZ = ZoneInfo("Europe/Moscow")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
PAUSE = 1.0
STAMP_RE = re.compile(r'"?([A-Za-z_]{2,40})"?\s*[:=]\s*"?(1[7-9]\d{8})\b')   # «"date":1791100000» — 2023–2033 годы
AGREE = 30          # с: поле годится в опору, если расходится с началом записи не больше (ADR-028, раздел 1)
MIN_GAMES = 3       # и так на каждом из стольких размеченных матчей
DURATION_RE = re.compile(r'"?(duration|video_duration|len)"?\s*[:=]\s*"?(\d{2,5})\b')


def stamps(page: str) -> dict[str, set[int]]:
    """Все метки времени на странице: имя поля → значения unix. Поле без имени не нужно — не угадаем смысл."""
    out: dict[str, set[int]] = {}
    for key, v in STAMP_RE.findall(page):
        out.setdefault(key, set()).add(int(v))
    return out


def durations(page: str) -> set[int]:
    return {int(v) for _, v in DURATION_RE.findall(page) if 600 <= int(v) <= replay.MAX_T}   # матч — от 10 минут


def true_start(entry: dict, game: dict) -> datetime | None:
    """Настоящее начало записи по часам по опорам админа: время гола минус его секунда в записи.
    Опор несколько — берём первую по счёту: до неё разрывов в записи ещё не было."""
    at = {g["score"]: g["at"] for g in replay.goals_of(game) if g["at"]}
    for score, t in sorted((entry.get("anchors") or {}).items(), key=lambda x: x[1]):
        if at.get(score):
            return at[score] - timedelta(seconds=t)
    return None


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def video_ids(video: str) -> tuple[str, str] | None:
    m = re.search(r"video(-?\d+)_(\d+)", video)
    return (m.group(1), m.group(2)) if m else None


def report(name: str, page: str, start: datetime | None, sink: dict | None = None) -> None:
    """Печатает метки страницы и их расхождение с началом записи. sink — {«источник поле»: [расхождения]}
    для сводки: у поля бывает несколько значений, а метка «минус длительность» — отдельное поле."""
    found, lens = stamps(page), durations(page)
    print(f"  {name}: {len(page)} знаков, меток времени {sum(len(v) for v in found.values())}, длительности {sorted(lens) or '—'}")
    for key, vals in sorted(found.items()):
        for v in sorted(vals):
            dt = datetime.fromtimestamp(v, TZ)
            diff = f", от начала записи {round((dt - start).total_seconds()):+d} с" if start else ""
            ends = "".join(f"; минус {d} с — {round((dt - timedelta(seconds=d) - start).total_seconds()):+d} с"
                           for d in sorted(lens)) if start else ""
            print(f"    {key} = {dt:%d.%m %H:%M:%S}{diff}{ends}")
            if start and sink is not None:
                sink.setdefault(f"{name} {key}", []).append(round((dt - start).total_seconds()))
                for d in lens:
                    sink.setdefault(f"{name} {key} − duration", []).append(
                        round((dt - timedelta(seconds=d) - start).total_seconds()))


def probe_video(video: str, start: datetime | None, out: Path, token: str | None, sink: dict | None = None) -> None:
    ids = video_ids(video)
    if not ids:
        print(f"  не ролик VK: {video}")
        return
    oid, vid = ids
    pages = {"page": f"https://vk.com/video{oid}_{vid}", "embed": f"https://vk.com/video_ext.php?oid={oid}&id={vid}",
             "vkvideo": f"https://vkvideo.ru/video{oid}_{vid}"}
    for name, url in pages.items():
        try:
            page = fetch(url)
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            print(f"  {name}: не скачалась — {err}")
            continue
        finally:
            time.sleep(PAUSE)
        (out / f"{oid}_{vid}_{name}.html").write_text(page, encoding="utf-8")
        report(name, page, start, sink)
    if token:
        url = (f"https://api.vk.com/method/video.get?videos={oid}_{vid}&extended=1&v=5.199&access_token={token}")
        try:
            body = fetch(url)
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            print(f"  video.get: не ответил — {err}")
            return
        finally:
            time.sleep(PAUSE)
        data = json.loads(body)
        if "error" in data:
            print(f"  video.get: ошибка {data['error'].get('error_code')} — {data['error'].get('error_msg')}")
            return
        (out / f"{oid}_{vid}_api.json").write_text(body, encoding="utf-8")
        report("video.get", body, start, sink)


def verdict(seen: dict[str, dict[str, int]], games: list[str]) -> list[str]:
    """Сводка: поле → лучшее расхождение на каждом матче (у поля бывает несколько значений) и вердикт.
    seen — {поле: {матч: расхождение, с}}, games — матчи с началом по опоре."""
    lines = []
    for field, per in sorted(seen.items(), key=lambda x: (-len(x[1]), max(abs(v) for v in x[1].values()))):
        worst = max(abs(v) for v in per.values())
        ok = len(per) == len(games) and len(games) >= MIN_GAMES and worst <= AGREE
        cells = ", ".join(f"{g.split('|', 1)[1]}: {per[g]:+d}" if g in per else f"{g.split('|', 1)[1]}: —" for g in games)
        lines.append(f"  {'ГОДИТСЯ' if ok else 'нет':7} {field}: {cells}")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser(description="Что VK знает о начале записи трансляции (ADR-027)")
    ap.add_argument("--video", help="один ролик VK без опор: только показать метки времени")
    ap.add_argument("--live", type=Path, default=Path(os.environ.get("LIVE_DIR") or ROOT / "live"))
    ap.add_argument("--out", type=Path, default=ROOT / "probe" / "vk")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("VK_TOKEN") or None
    if args.video:
        print(args.video)
        probe_video(args.video, None, args.out, token)
        return
    try:
        marked = json.loads((args.live / "replays.json").read_text(encoding="utf-8")).get("games") or {}
    except (OSError, ValueError):
        marked = {}
    if not marked:
        print(f"В {args.live / 'replays.json'} нет размеченных матчей: сначала /replay в боте, "
              "или --video <ссылка>, чтобы просто посмотреть метки")
        return
    seen: dict[str, dict[str, int]] = {}
    games_ok: list[str] = []
    for key, entry in sorted(marked.items()):
        day = key.split("|")[0]
        try:
            games = json.loads((args.live / f"{day}.json").read_text(encoding="utf-8")).get("games") or []
        except (OSError, ValueError):
            games = []
        game = next((g for g in games if g.get("key") == key), None)
        start = true_start(entry, game) if game else None
        print(f"\n{key} · {entry.get('video')}")
        print(f"  начало записи по опоре: {start:%d.%m %H:%M:%S}" if start else "  начала по опоре нет: гол-опора без времени по часам")
        sink: dict[str, list[int]] = {}
        probe_video(entry.get("video") or "", start, args.out, token, sink)
        if start:
            games_ok.append(key)
            for field, diffs in sink.items():
                seen.setdefault(field, {})[key] = min(diffs, key=abs)
    print(f"\nСводка: матчей с началом по опоре {len(games_ok)}, порог ±{AGREE} с на каждом, матчей не меньше {MIN_GAMES}")
    print("\n".join(verdict(seen, games_ok)) or "  ни одной метки времени VK не нашлось")
    print(f"\nСтраницы — в {args.out}. Пришли этот вывод целиком (ADR-028, раздел 1).")


if __name__ == "__main__":
    main()

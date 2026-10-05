"""Найти голы по табло трансляции (ADR-029, решение владельца 05.10): когда в записи сменился счёт. Запускать на VPS:

    apt install -y ffmpeg && venv/bin/pip install yt-dlp     # один раз
    cd /opt/rhl && venv/bin/python tools/probe_scoreboard.py                     # все размеченные матчи
    venv/bin/python tools/probe_scoreboard.py --match 2026-10-04|rostov|krasnodar
    venv/bin/python tools/probe_scoreboard.py --stream probe/x.mp4 --truth 1:0=42:53,0:2=49:28   # файл

Табло — плашка со счётом в углу кадра, она стоит на месте всю игру. Пробник смотрит запись целиком и редко
(кадр раз в `--step` секунд, только ключевые кадры, 480p — качается быстро) и вырезает из каждого кадра рамку
`--box` (по умолчанию левая верхняя треть). Пиксели, которые почти не меняются от кадра к кадру, — графика
табло: названия, фон плашки и счёт (он меняется только при голе). Пиксели игры и бегущих часов меняются
постоянно — их не берём. Смена счёта — когда часть пикселей графики сменилась и новое держится в следующих
кадрах; плашку убрали на повтор или перерыв — не смена, мы ждём, пока она вернётся. Найденный промежуток
уточняем делением пополам до секунды.

Минуты и секунды часов игры на табло меняются десятки раз за запись — такие пиксели не берём, счёт меняется
редко. Остаются лишние смены (десятки минут на часах, номер периода): какая смена — какой гол, решает время
с сайта лиги (`at` службы live): внутри периода «смена минус отметка сайта» почти одна у всех голов (align).

Эталон — опоры админа в live/replays.json (секунда гола в записи). Сводка: на сколько секунд после гола
меняется табло у каждой трансляции и насколько это постоянно. Если разброс в пределах ±10 с — гол находится
без человека: «смена табло минус обычное запоздание».

Картинки рамки до и после каждой смены и кадр целиком у каждого гола — в probe/scoreboard/<матч>/: по ним
видно, где на самом деле табло, если рамка мимо (`--box x,y,ш,в` в долях кадра).
Качаем как плеер (yt-dlp), без обхода защиты: VK закроет или попросит капчу — останавливаемся (ADR-012).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import replay  # noqa: E402

W, H = 240, 90          # рамка табло после масштаба, пикселей: хватает, чтобы цифра счёта была в 10–20 пикселей
STEP = 10               # с между кадрами первого прохода
BOX = (0.0, 0.0, 0.5, 0.3)   # рамка в долях кадра: x, y, ширина, высота — левый верх, где табло у трансляций РХЛ
DIFF = 40               # разница яркости пикселя (0–255), с которой пиксель считаем изменившимся
STABLE = 0.85           # пиксель графики: не меняется хотя бы в стольких парах соседних кадров
SHOWN = 0.75            # табло на экране: столько пикселей графики совпадает с обычным видом
MIN_PX = 8              # смена в зоне: изменилось не меньше стольких пикселей
ZONE_SHARE = 0.15       # и не меньше такой доли зоны (цифра 20 px — это 60–150 пикселей)
GAP = 2                 # пиксели ближе — одна зона: цифры «2:1» — одна зона, часы рядом — другая
MIN_ZONE = 12           # зона меньше — шум сжатия
ZONE_MAX = 10           # зона сменилась чаще — это часы, а не счёт: одна команда больше 10 раз за матч не забивает
MAX_FLIPS = 15          # пиксель счёта меняется за запись не чаще: минуты часов игры меняются десятки раз
HOLD = 2                # новое должно держаться ещё в стольких кадрах с табло
MEDIAN_FRAMES = 200     # обычный вид табло — медиана по стольким кадрам (равномерно по записи)
TIMELINE = (-20, 0, 5, 10, 15, 20, 30, 45, 60, 90)   # с от гола: кадры ленты табло в --check
MATCH_WINDOW = (-30, 300)    # с: смена табло, которую сравниваем с голом админа, — от 30 с до гола до 5 минут после
FORMAT = "b[height<=480][height>=240]/b[height<=480]/w"   # хватает для табло, качается быстро


# ---------- кадры ----------

def crop_filter(box: tuple[float, float, float, float], gray: bool = True) -> str:
    x, y, w, h = box
    out = f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale={W}:{H}"
    return out + (",format=gray" if gray else "")


def parse_box(text: str) -> tuple[float, float, float, float]:
    parts = [float(v) for v in text.split(",")]
    if len(parts) != 4 or not all(0 <= v <= 1 for v in parts) or parts[0] + parts[2] > 1 or parts[1] + parts[3] > 1:
        raise argparse.ArgumentTypeError("рамка — четыре доли кадра через запятую: x,y,ширина,высота, например 0,0,0.5,0.3")
    return tuple(parts)


def header_args(headers: dict | None) -> list[str]:
    if not headers:
        return []
    return ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]


def ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        sys.exit("Нет ffmpeg: apt install -y ffmpeg")
    return path


def scan(src: str, headers: dict | None, box, step: int, start: int = 0, end: int | None = None) -> list[tuple[float, bytes]]:
    """Первый проход: кадр раз в step секунд, только ключевые кадры (декодировать почти нечего). (секунда, рамка)."""
    cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-skip_frame", "nokey", *header_args(headers)]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", src]
    if end:
        cmd += ["-t", str(end - start)]
    cmd += ["-an", "-vf", f"setpts=PTS-STARTPTS,fps=1/{step}:round=down,{crop_filter(box)}", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True, timeout=3 * 3600).stdout
    size = W * H
    return [(start + i * step, raw[i * size:(i + 1) * size]) for i in range(len(raw) // size)]


def grab(src: str, headers: dict | None, box, t: float) -> bytes | None:
    """Один кадр на секунде t, точно (с декодированием от ближайшего ключевого)."""
    cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", *header_args(headers), "-ss", f"{t:.2f}", "-i", src,
           "-frames:v", "1", "-an", "-vf", crop_filter(box), "-f", "rawvideo", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, check=True, timeout=120).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return raw[:W * H] if len(raw) >= W * H else None


def save_png(src: str, headers: dict | None, t: float, path: Path, box=None) -> None:
    """Картинка для глаз: рамка (box) крупно или кадр целиком шириной 640."""
    vf = crop_filter(box, gray=False).replace(f"scale={W}:{H}", f"scale={W * 2}:{H * 2}") if box else "scale=640:-2"
    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *header_args(headers), "-ss", f"{t:.2f}",
                    "-i", src, "-frames:v", "1", "-vf", vf, str(path)], capture_output=True, timeout=120)


def save_raw(raw: bytes, path: Path, w: int = W, h: int = H) -> None:
    """Серую рамку (наши байты) — в картинку вдвое крупнее, пиксели без сглаживания: видно, что именно сравнивали."""
    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray",
                    "-s", f"{w}x{h}", "-i", "-", "-vf", "scale=iw*2:ih*2:flags=neighbor", str(path)],
                   input=raw, capture_output=True, timeout=60)


# ---------- табло ----------

def stable_mask(frames: list[bytes]) -> list[int]:
    """Пиксели графики: в большинстве пар соседних кадров не меняются. Игра и бегущие часы — меняются."""
    if len(frames) < 3:
        return []
    size = len(frames[0])
    same = [0] * size
    for a, b in zip(frames, frames[1:]):
        for p in range(size):
            if abs(a[p] - b[p]) <= DIFF:
                same[p] += 1
    need = STABLE * (len(frames) - 1)
    return [p for p in range(size) if same[p] >= need]


def usual(frames: list[bytes], mask: list[int]) -> dict[int, int]:
    """Обычный вид табло: медиана каждого пикселя графики по кадрам, равномерно по записи."""
    pick = frames[::max(1, len(frames) // MEDIAN_FRAMES)]
    return {p: sorted(f[p] for f in pick)[len(pick) // 2] for p in mask}


def shown(frame: bytes, mask: list[int], med: dict[int, int]) -> bool:
    """Табло на экране: почти вся графика как обычно. Счёт — малая часть графики, его смена не мешает."""
    return bool(mask) and sum(abs(frame[p] - med[p]) <= DIFF for p in mask) >= SHOWN * len(mask)


def moved(a: bytes, b: bytes, mask: list[int]) -> list[int]:
    return [p for p in mask if abs(a[p] - b[p]) > DIFF]


def zones(pixels: list[int]) -> list[list[int]]:
    """Редко меняющиеся пиксели — по зонам: рядом лежащие (ближе GAP) — одна зона. Счёт, десятки минут часов,
    номер периода стоят в разных местах табло и меняются в разное время — за каждой зоной следим отдельно."""
    left = set(pixels)
    out = []
    while left:
        seed = left.pop()
        zone, todo = [seed], [seed]
        while todo:
            p = todo.pop()
            x, y = p % W, p // W
            for dy in range(-GAP, GAP + 1):
                for dx in range(-GAP, GAP + 1):
                    q = (y + dy) * W + (x + dx)
                    if 0 <= x + dx < W and 0 <= y + dy < H and q in left:
                        left.remove(q)
                        zone.append(q)
                        todo.append(q)
        if len(zone) >= MIN_ZONE:
            out.append(sorted(zone))
    return sorted(out, key=lambda z: z[0])


def rare(frames: list[bytes], mask: list[int]) -> list[int]:
    """Пиксели графики, которые за запись меняются, но редко: счёт (и номер периода, десятки минут часов).
    Минуты и секунды часов игры меняются десятки раз — их не берём. frames — кадры с табло по порядку."""
    flips = dict.fromkeys(mask, 0)
    for a, b in zip(frames, frames[1:]):
        for p in mask:
            if abs(a[p] - b[p]) > DIFF:
                flips[p] += 1
    return [p for p in mask if 1 <= flips[p] <= MAX_FLIPS]


def changes(samples: list[tuple[float, bytes]], mask: list[int], med: dict[int, int],
            report: list | None = None) -> list[dict]:
    """Смены табло по зонам (zones от rare): (последний кадр со старым видом, первый с новым). Новое держится
    HOLD кадров с табло — иначе это мелькание (плашку перекрыло, сменили на миг). Кадры без табло (повтор,
    перерыв, реклама) пропускаем: ждём, пока плашка вернётся. Зона, которая сменилась чаще ZONE_MAX раз, —
    скорее часы: её смены помечены `often`, а не выброшены (иначе не видно, что счёт слипся с часами).
    report — сюда кладём по зоне: место, размер, сколько смен."""
    vis = [(t, f) for t, f in samples if shown(f, mask, med)]
    out = []
    if not vis:
        return out
    for zi, zone in enumerate(zones(rare([f for _, f in vis], mask))):
        need = max(MIN_PX, ZONE_SHARE * len(zone))
        mine = []
        cur_t, cur = vis[0]
        for k in range(1, len(vis)):
            t, f = vis[k]
            diff = moved(cur, f, zone)
            if len(diff) < need:
                cur_t, cur = t, f
                continue
            after = vis[k + 1:k + 1 + HOLD]
            if len(after) == HOLD and all(len(moved(f, g, diff)) < len(diff) / 2 for _, g in after):
                mine.append({"lo": cur_t, "hi": t, "before": cur, "after": f, "pixels": diff, "zone": zi})
                cur_t, cur = t, f
        often = len(mine) > ZONE_MAX
        for c in mine:
            c["often"] = often
        out += mine
        if report is not None:
            xs, ys = [p % W for p in zone], [p // W for p in zone]
            report.append({"zone": zi, "size": len(zone), "box": (min(xs), max(xs), min(ys), max(ys)),
                           "changes": len(mine), "often": often})
    merged: dict[tuple, dict] = {}   # одна цифра бывает в нескольких зонах — их смены в одном промежутке одна смена
    for c in sorted(out, key=lambda c: (c["hi"], c["often"])):
        same = merged.get((c["lo"], c["hi"]))
        if same and same["often"] == c["often"]:
            same["pixels"] = same["pixels"] + c["pixels"]
        else:
            merged.setdefault((c["lo"], c["hi"]), c)
    return sorted(merged.values(), key=lambda c: c["hi"])


def refine(ch: dict, get, mask: list[int], med: dict[int, int], step: float, tries: int = 10) -> dict:
    """Уточнить смену до секунды по точным кадрам. Время кадров первого прохода приблизительное (ключевые кадры
    идут раз в 2–4 с), поэтому сначала расширяем промежуток на шаг в обе стороны и проверяем: слева табло
    старое, справа новое. Дальше делим пополам. Табло убрано (повтор после гола) — дальше не уточняем."""
    def state(t: float) -> str | None:
        f = get(max(0.0, t))
        if f is None or not shown(f, mask, med):
            return None
        old = len(moved(f, ch["before"], ch["pixels"]))
        new = len(moved(f, ch["after"], ch["pixels"]))
        return "old" if old < new else "new"

    lo, hi = max(0.0, ch["lo"] - step), ch["hi"] + step
    for _ in range(3):
        if lo <= 0 or state(lo) == "old":
            break
        lo = max(0.0, lo - step)
    for _ in range(3):
        if state(hi) == "new":
            break
        hi += step
    for _ in range(tries):
        if hi - lo <= 1:
            break
        mid = (lo + hi) / 2
        st = state(mid)
        if st is None:
            break
        lo, hi = (mid, hi) if st == "old" else (lo, mid)
    return {**ch, "lo": lo, "hi": hi}


def align(found: list[float], goals: list[tuple[str, str, float]], tol: float = 90) -> dict[str, float]:
    """Без эталона: какая смена табло — какой гол. goals — (счёт, период, когда сайт лиги показал гол, unix).
    Внутри периода запись и часы идут вместе: «смена минус отметка сайта» (сдвиг) почти один у всех голов
    периода (сайт запаздывает до полутора минут — tol). Лишние смены (десятки минут на часах, номер периода)
    остаются без гола. Между периодами сдвиг свой — трансляцию прерывают в перерыве (ADR-027), но на минуты,
    а не на час: начинаем с периода, где голов больше всего, а у остальных из равных берём сдвиг, ближе всего
    к уже найденному. Смена достаётся одному голу."""
    out: dict[str, float] = {}
    used: set[float] = set()
    shifts: list[float] = []
    pers = list(dict.fromkeys(p for _, p, _ in goals))
    for per in sorted(pers, key=lambda q: -sum(p == q for _, p, _ in goals)):
        mine = [(s, at) for s, p, at in goals if p == per]
        best: tuple | None = None
        for _, at0 in mine:
            for e0 in found:
                if e0 in used:
                    continue
                shift, got, taken, miss = e0 - at0, {}, set(), 0.0
                for s, at in mine:
                    near = [e for e in found if e not in used and e not in taken and abs(e - at - shift) <= tol]
                    if near:
                        e = min(near, key=lambda x: abs(x - at - shift))
                        got[s] = e
                        taken.add(e)
                        miss += abs(e - at - shift)
                far = min((abs(shift - x) for x in shifts), default=0.0)
                key = (len(got), -far, -miss)
                if best is None or key > best[0]:
                    best = (key, shift, got)
        if best and best[2]:
            out.update(best[2])
            used.update(best[2].values())
            shifts.append(best[1])
    return out


def against(found: list[float], truth: dict[str, int]) -> list[tuple[str, int, float | None]]:
    """Гол админа → первая смена табло в окне MATCH_WINDOW после него. (счёт, секунда гола, смена или None)."""
    out = []
    for score, t in sorted(truth.items(), key=lambda x: x[1]):
        near = [s for s in found if MATCH_WINDOW[0] <= s - t <= MATCH_WINDOW[1]]
        out.append((score, t, min(near) if near else None))
    return out


def lag_summary(rows: list[tuple[str, int, float | None]]) -> str:
    lags = sorted(round(s - t) for _, t, s in rows if s is not None)
    if not lags:
        return "ни один гол не нашёлся на табло"
    med = lags[len(lags) // 2]
    within = sum(abs(v - med) <= 10 for v in lags)
    return (f"нашлось {len(lags)} из {len(rows)}, запоздание табло после гола: медиана {med} с, "
            f"от {lags[0]} до {lags[-1]} с; в пределах ±10 с от медианы — {within} из {len(rows)}")


# ---------- источники ----------

def stream_of(video: str) -> tuple[str, dict, int | None]:
    """Ролик VK → адрес потока для ffmpeg, заголовки и длительность (yt-dlp, как плеер)."""
    try:
        import yt_dlp
    except ImportError:
        sys.exit("Нет yt-dlp: venv/bin/pip install yt-dlp")
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "format": FORMAT}) as ydl:
        info = ydl.extract_info(video, download=False)
    fmt = (info.get("requested_formats") or [info])[0]
    headers = dict(fmt.get("http_headers") or info.get("http_headers") or {})
    if fmt.get("cookies"):
        headers["Cookie"] = fmt["cookies"]
    return fmt["url"], headers, info.get("duration")


def parse_truth(text: str) -> dict[str, int]:
    """«1:0=42:53,0:2=49:28» → {счёт: секунда записи}."""
    out = {}
    for part in filter(None, (x.strip() for x in (text or "").split(","))):
        score, _, t = part.partition("=")
        sec = replay.parse_clock(t)
        if replay.SCORE_RE.fullmatch(score.strip()) and sec is not None:
            out[score.strip()] = sec
    return out


def site_goals(live: Path, key: str) -> list[tuple[str, str, float]]:
    """Голы, как их видела служба live: (счёт, период, когда сайт лиги показал гол — unix)."""
    try:
        games = json.loads((live / f"{key.split('|')[0]}.json").read_text(encoding="utf-8")).get("games") or []
    except (OSError, ValueError):
        return []
    game = next((g for g in games if g.get("key") == key), None)
    return [(g["score"], str(g.get("period") or ""), g["at"].timestamp())
            for g in replay.goals_of(game or {}) if g["at"]]


def steady_change(before: list[bytes], after: list[bytes]) -> list[int]:
    """Пиксели, которые до гола стоят на месте, после гола тоже стоят, но уже другие: так меняется графика
    (счёт), а не игра — игра меняется и внутри «до», и внутри «после»."""
    out = []
    for p in range(W * H):
        a, b = [f[p] for f in before], [f[p] for f in after]
        if max(a) - min(a) <= DIFF and max(b) - min(b) <= DIFF and abs(sum(a) / len(a) - sum(b) / len(b)) > DIFF:
            out.append(p)
    return out


def timeline(src: str, headers: dict | None, t: int, box, path: Path) -> None:
    """Лента табло вокруг гола одной картинкой: рамка в моменты TIMELINE сверху вниз. По ней видно на глаз,
    через сколько секунд сменился счёт, убирали ли плашку на повтор и не поправлял ли оператор счёт."""
    parts = []
    for k, d in enumerate(TIMELINE):
        part = path.with_name(f"{path.stem}_{k:02d}.png")
        save_png(src, headers, max(0, t + d), part, box)
        if part.exists():
            parts.append(part)
    if parts:
        args = [x for part in parts for x in ("-i", str(part))]
        subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args, "-filter_complex",
                        f"vstack=inputs={len(parts)}" if len(parts) > 1 else "null", str(path)],
                       capture_output=True, timeout=120)
    for part in parts:
        part.unlink()


def check(name: str, src: str, headers: dict | None, truth: dict[str, int], args) -> None:
    """Быстрая проверка без прохода по записи (--check): у каждого гола админа по три кадра рамки до (за 30–20 с)
    и после (через after…after+10 с) — что на табло сменилось и где. Картинки рамки до и после, разница и кадр
    целиком — в папку матча. Так видно, есть ли табло в рамке и насколько заметна смена счёта."""
    out = args.out / re.sub(r"[^\w.-]+", "_", name)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("check_*.png"):
        old.unlink()
    print(f"\n{name} — проверка по временам админа", flush=True)
    x0, y0, bw, bh = args.box
    for score, t in sorted(truth.items(), key=lambda x: x[1]):
        tag = score.replace(":", "-")
        before = [grab(src, headers, args.box, max(0, t - d)) for d in (30, 25, 20)]
        after = [grab(src, headers, args.box, t + args.after + d) for d in (0, 5, 10)]
        save_png(src, headers, max(0, t - 20), out / f"check_{tag}_before.png", args.box)
        save_png(src, headers, t + args.after, out / f"check_{tag}_after.png", args.box)
        save_png(src, headers, t + args.after, out / f"check_{tag}_frame.png")
        timeline(src, headers, t, args.box, out / f"check_{tag}_timeline.png")
        if any(f is None for f in before + after):
            print(f"  гол {score} ({replay.fmt_t(t)}): кадр не скачался")
            continue
        diff = steady_change(before, after)
        save_raw(bytes(255 if p in set(diff) else before[-1][p] // 3 for p in range(W * H)), out / f"check_{tag}_diff.png")
        if not diff:
            print(f"  гол {score} ({replay.fmt_t(t)}): в рамке ничего не сменилось — табло не в рамке, "
                  f"или через {args.after} с после гола ещё повтор (попробуй --after 60)")
            continue
        xs, ys = [p % W for p in diff], [p // W for p in diff]
        fx = (x0 + bw * min(xs) / W, x0 + bw * (max(xs) + 1) / W)
        fy = (y0 + bh * min(ys) / H, y0 + bh * (max(ys) + 1) / H)
        print(f"  гол {score} ({replay.fmt_t(t)}): сменилось {len(diff)} пикселей, в рамке x {min(xs)}–{max(xs)}, "
              f"y {min(ys)}–{max(ys)}; в кадре x {fx[0]:.2f}–{fx[1]:.2f}, y {fy[0]:.2f}–{fy[1]:.2f}")
    print(f"  картинки — {out}: check_*_timeline.png — табло через "
          f"{', '.join(f'{d:+d}' for d in TIMELINE)} с от гола (сверху вниз), check_*_diff.png — что сменилось "
          f"(белое), check_*_frame.png — кадр целиком", flush=True)


def probe(name: str, src: str, headers: dict | None, truth: dict[str, int], site: list, args) -> list:
    out = args.out / re.sub(r"[^\w.-]+", "_", name)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):   # картинки прошлого запуска — не путать с новыми
        old.unlink()
    print(f"\n{name}\n  качаю запись и смотрю кадр раз в {args.step} с — несколько минут…", flush=True)
    began = time.monotonic()
    samples = scan(src, headers, args.box, args.step, args.start, args.end)
    print(f"  кадров первого прохода: {len(samples)} за {round(time.monotonic() - began)} с", flush=True)
    for score, t in sorted(truth.items(), key=lambda x: x[1]):   # кадр целиком через минуту после гола — где табло
        save_png(src, headers, t + 60, out / f"goal_{score.replace(':', '-')}_frame.png")
    if not samples:
        print("  кадров нет: поток не открылся")
        return []
    frames = [f for _, f in samples]
    mask = stable_mask(frames)
    print(f"  пикселей графики в рамке: {len(mask)} из {W * H}")
    if len(mask) < 200:
        print("  табло в рамке не нашлось: посмотри goal_*_frame.png и задай --box")
        return []
    med = usual(frames, mask)
    seen = sum(shown(f, mask, med) for f in frames)
    print(f"  табло на экране в {seen} кадрах из {len(frames)} ({round(100 * seen / len(frames))}%)")
    save_raw(bytes(med.get(p, 0) for p in range(W * H)), out / "board_usual.png")
    report: list = []
    found = changes(samples, mask, med, report)
    save_raw(bytes(255 if any(z["box"][0] <= p % W <= z["box"][1] and z["box"][2] <= p // W <= z["box"][3]
                              for z in report) else med.get(p, 0) // 3 for p in range(W * H)), out / "board_zones.png")
    print(f"  зон, которые иногда меняются: {len(report)}")
    for z in report:
        x1, x2, y1, y2 = z["box"]
        print(f"    зона {z['zone']}: x {x1}–{x2}, y {y1}–{y2}, пикселей {z['size']}, смен {z['changes']}"
              + (" — часто, похоже на часы" if z["often"] else ""))
    print("  уточняю смены до секунды…", flush=True)
    exact = [refine(c, lambda t: grab(src, headers, args.box, t), mask, med, args.step) for c in found]
    main_ = [c for c in exact if not c["often"]]
    for k, c in enumerate(exact, 1):
        save_png(src, headers, max(0, c["lo"] - 1), out / f"change_{k:02d}_before.png", args.box)
        save_png(src, headers, c["hi"] + 1, out / f"change_{k:02d}_after.png", args.box)
    picked = align([c["hi"] for c in main_], site)
    by_t = {t: s for s, t in picked.items()}
    print(f"  смен табло: {len(main_)} (и ещё {len(exact) - len(main_)} в частых зонах — часы?), "
          f"голов у админа: {len(truth)}, у сайта лиги: {len(site)}")
    for k, c in enumerate(main_, 1):
        xs, ys = [p % W for p in c["pixels"]], [p // W for p in c["pixels"]]
        where = f"x {min(xs)}–{max(xs)}, y {min(ys)}–{max(ys)}"
        goal = by_t.get(c["hi"])
        print(f"    {k:2d}. {replay.fmt_t(int(c['hi']))} (±{round(c['hi'] - c['lo'])} с), зона {c['zone']} [{where}], "
              f"пикселей {len(c['pixels'])} · " + (f"по сайту лиги — гол {goal}" if goal else "без гола"))
    if truth and picked:
        hits = [(s, truth[s], picked[s]) for s in truth if s in picked]
        lags = sorted(round(e - t) for _, t, e in hits)
        if lags:
            med = lags[len(lags) // 2]
            print(f"  без эталона, по сайту лиги: найдено {len(picked)} из {len(site)}; против админа — запоздание "
                  f"табло {lags}, в пределах ±10 с от медианы {sum(abs(v - med) <= 10 for v in lags)} из {len(truth)}")
    rows = against([c["hi"] for c in main_], truth)
    if truth:
        for score, t, s in rows:
            print(f"    гол {score} у админа {replay.fmt_t(t)}: " + (f"табло через {round(s - t):+d} с" if s is not None else "на табло не нашёлся"))
        print("  " + lag_summary(rows))
        if len(main_) < len(exact):   # вдруг счёт слипся в одну зону с часами
            print("  с частыми зонами: " + lag_summary(against([c["hi"] for c in exact], truth)))
    print(f"  картинки — {out}: board_usual.png — обычный вид табло, board_zones.png — зоны (белое)")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Голы по табло трансляции (ADR-029)")
    ap.add_argument("--match", help="ключ матча из live/replays.json: <дата>|<хозяева>|<гости>; без него — все размеченные")
    ap.add_argument("--stream", help="файл или адрес потока вместо ролика VK (для проверки)")
    ap.add_argument("--truth", default="", help="для --stream: голы «1:0=42:53,0:2=49:28»")
    ap.add_argument("--box", type=parse_box, default=BOX, help="рамка табло в долях кадра: x,y,ширина,высота")
    ap.add_argument("--step", type=int, default=STEP)
    ap.add_argument("--start", type=int, default=0, help="с какой секунды записи смотреть")
    ap.add_argument("--end", type=int, default=None, help="до какой секунды")
    ap.add_argument("--check", action="store_true",
                    help="быстро, без прохода по записи: что сменилось на табло вокруг каждого гола админа")
    ap.add_argument("--after", type=int, default=40, help="для --check: через сколько секунд после гола смотреть табло")
    ap.add_argument("--live", type=Path, default=Path(os.environ.get("LIVE_DIR") or ROOT / "live"))
    ap.add_argument("--out", type=Path, default=ROOT / "probe" / "scoreboard")
    args = ap.parse_args()
    if args.stream:
        name, truth = Path(args.stream).name, parse_truth(args.truth)
        if args.check:
            check(name, args.stream, None, truth, args)
        else:
            probe(name, args.stream, None, truth, [], args)
        return
    try:
        marked = json.loads((args.live / "replays.json").read_text(encoding="utf-8")).get("games") or {}
    except (OSError, ValueError):
        marked = {}
    keys = [args.match] if args.match else sorted(marked)
    if not keys or any(k not in marked for k in keys):
        sys.exit(f"Нет размеченного матча в {args.live / 'replays.json'}: сначала /replay в боте")
    every = []
    for key in keys:
        entry = marked[key]
        try:
            src, headers, _ = stream_of(entry["video"])
        except Exception as err:   # VK не отдал — дальше не ломимся (ADR-012)
            print(f"\n{key}: поток не получили — {type(err).__name__}: {err}")
            continue
        truth = {s: t for s, t in (entry.get("anchors") or {}).items() if isinstance(t, int)}
        if args.check:
            check(key, src, headers, truth, args)
            continue
        every += probe(key, src, headers, truth, site_goals(args.live, key), args)
    if len(keys) > 1:
        print("\nВсего: " + lag_summary(every))
    print("\nПришли этот вывод целиком и 2–3 картинки change_*_before/after.png (ADR-029).")


if __name__ == "__main__":
    main()

"""Найти голы по табло трансляции (ADR-029, решение владельца 05.10): когда в записи сменился счёт. Запускать на VPS:

    apt install -y ffmpeg && venv/bin/pip install yt-dlp     # один раз
    cd /opt/rhl && venv/bin/python tools/probe_scoreboard.py                     # все размеченные матчи
    venv/bin/python tools/probe_scoreboard.py --match 2026-10-04|rostov|krasnodar
    venv/bin/python tools/probe_scoreboard.py --check                            # быстро: только окна вокруг голов
    venv/bin/python tools/probe_scoreboard.py --stream probe/x.mp4 --truth 1:0=42:53,0:2=49:28   # файл

Табло — плашка со счётом в углу кадра, она стоит на месте всю игру. Пробник смотрит запись целиком и редко
(кадр раз в `--step` секунд, только ключевые кадры, 480p — качается быстро) и вырезает из каждого кадра рамку
табло: у клубов из `BOXES` — вплотную к их плашке, у остальных — левый верх кадра, `--box` — своя. Пиксели,
которые почти не меняются от кадра к кадру, — графика табло: названия, фон плашки и счёт (он меняется только
при голе). Пиксели игры и бегущих часов меняются постоянно — их не берём. Смена счёта — когда часть пикселей
графики сменилась и новое держится в следующих кадрах; плашку убрали на повтор или перерыв — не смена, мы
ждём, пока она вернётся. Повтор гола бывает со старым табло — смена туда и обратно за пару минут не гол.
Найденный промежуток уточняем делением пополам до секунды.

Минуты и секунды часов игры на табло меняются десятки раз за запись — такие пиксели не берём, счёт меняется
редко. Остаются лишние смены (десятки минут на часах, номер периода): какая смена — какой гол, решает время
с сайта лиги (`at` службы live): внутри периода «смена минус отметка сайта» почти одна у всех голов (align).

Эталон — опоры админа в live/replays.json (секунда гола в записи). Сводка: на сколько секунд после гола
меняется табло у каждой трансляции и насколько это постоянно. Если разброс в пределах ±10 с — гол находится
без человека: «смена табло минус обычное запоздание».

Картинки рамки до и после каждой смены и кадр целиком у каждого гола — в probe/scoreboard/<матч>/: по ним
видно, где на самом деле табло, если рамка мимо (`--box x,y,ш,в` в долях кадра).

`--check` — без прохода по записи: у каждого гола админа кадр каждую секунду от 30 с до гола до 2 минут после,
и в этом окне — через сколько секунд после гола сменилось табло. Минуты на матч.
Качаем как плеер (yt-dlp), без обхода защиты: VK закроет или попросит капчу — останавливаемся (ADR-012).
"""
import argparse
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import time
from itertools import takewhile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import replay  # noqa: E402

W, H = 240, 90          # рамка табло после масштаба, пикселей: хватает, чтобы цифра счёта была в 10–20 пикселей
STEP = 10               # с между кадрами первого прохода
BOX = (0.02, 0.02, 0.28, 0.26)   # рамка в долях кадра: x, y, ширина, высота — левый верх, где табло у трансляций РХЛ
BOXES = {               # рамка вплотную к табло трансляции хозяев: цифры счёта крупнее. По проверке 05.10 (ADR-029)
    "kaluga": (0.02, 0.02, 0.26, 0.16),
    "rostov": (0.04, 0.03, 0.23, 0.16),
    "ryazan-vdv": (0.05, 0.08, 0.17, 0.19),
    "tverichi": (0.05, 0.05, 0.15, 0.18),
}
DIFF = 40               # разница яркости пикселя (0–255), с которой пиксель считаем изменившимся
STABLE = 0.85           # пиксель графики: не меняется хотя бы в стольких парах соседних кадров с табло
TIGHT = 12              # пиксель графики в кадре с табло: яркость почти та же (сжатие дрожит на несколько единиц)
PRESENT = 0.3           # …и такой он хотя бы в стольких кадрах: плашку убирают на повторы, у «Ростова» видно 1/3 записи
MODE_FRAMES = 400       # частое значение пикселя — по стольким кадрам (равномерно по записи)
MIN_PLATE = 200         # графики меньше — табло в рамке нет
CORE_SLACK = 0.1        # ядро графики: совпадает с обычным видом не реже самых надёжных пикселей минус столько;
TOP = 0.995             # самые надёжные — эта доля пикселей рамки (буквы плашки, хоть их и меньше, чем полупрозрачного фона)
DENSE_SPAN = 600        # с: промежуток смены не длиннее — уточняем по кадру каждую секунду, а не делением пополам
SHOWN = 0.75            # табло на экране: столько пикселей графики совпадает с обычным видом
MIN_PX = 8              # смена в зоне: изменилось не меньше стольких пикселей
ZONE_SHARE = 0.15       # и не меньше такой доли зоны (цифра 20 px — это 60–150 пикселей)
GAP = 2                 # пиксели ближе — одна зона: цифры «2:1» — одна зона, часы рядом — другая
MIN_ZONE = 40           # зона меньше — шум сжатия: цифра счёта в рамке клуба — 110–300 пикселей (04.10)
NEED_CAP = 60           # смена в большой зоне (вся плашка слиплась в одну): хватает стольких пикселей — цифры
MIN_SIDE = 3            # зона уже по ширине или высоте — край плашки, который «дрожит» от сжатия, а не цифра
ZONE_MAX = 10           # зона сменилась чаще — это часы, а не счёт: одна команда больше 10 раз за матч не забивает
MAX_FLIPS = 15          # пиксель счёта меняется за запись не чаще: минуты часов игры меняются десятки раз
HOLD = 2                # смена: новое видно в большинстве кадров с табло за REPLAY с после неё, и их не меньше стольких
REPLAY = 180            # с: смена, откат и снова та же смена за столько — повтор со старым табло, а не три смены
MEDIAN_FRAMES = 200     # обычный вид табло — медиана по стольким кадрам (равномерно по записи)
TIMELINE = (-20, 0, 5, 10, 15, 20, 30, 45, 60, 90)   # с от гола: кадры ленты табло в --check
WINDOW = (-30, 120)     # с от гола: окно --check, кадр каждую секунду
EARLY = 20              # --check: смена табло раньше гола админа больше чем на столько — не этот гол (минута часов)
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


def fmt_box(box) -> str:
    return ",".join(f"{v:g}" for v in box)


def scan(src: str, headers: dict | None, box, step: int, start: int = 0, end: int | None = None,
         keyframes: bool = True) -> list[tuple[float, bytes]]:
    """Первый проход: кадр раз в step секунд, только ключевые кадры (декодировать почти нечего). (секунда, рамка).
    keyframes=False — все кадры: для окна --check, где кадр нужен каждую секунду."""
    cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", *(["-skip_frame", "nokey"] if keyframes else []),
           *header_args(headers)]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", src]
    if end:
        cmd += ["-t", str(end - start)]
    cmd += ["-an", "-vf", f"setpts=PTS-STARTPTS,fps=1/{step}:round=down,{crop_filter(box)}", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True, timeout=3 * 3600).stdout
    size = W * H
    return [(start + i * step, raw[i * size:(i + 1) * size]) for i in range(len(raw) // size)]


def safe_scan(src: str, headers: dict | None, box, start: float, end: float) -> list[tuple[float, bytes]]:
    """Кадр каждую секунду с start по end (для уточнения смены); не скачалось — пусто."""
    try:
        return scan(src, headers, box, 1, int(start), int(end) + 1, keyframes=False)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []


def cache_file(out: Path, box, step: int, start: int, end: int | None) -> Path:
    return out / f"frames_{fmt_box(box)}_{step}s_{start}-{end or 'end'}.gz"


def save_cache(path: Path, samples: list[tuple[float, bytes]], meta: dict) -> None:
    """Кадры первого прохода — в файл: следующий запуск не качает запись заново, а файл можно прислать и
    разобрать без сервера (--cache). Строка JSON (матч, рамка, шаг, голы админа и сайта, секунды кадров), дальше
    кадры подряд. Только рамка табло в сером, 21600 байт на кадр."""
    with gzip.open(path, "wb") as fh:
        head = {**meta, "w": W, "h": H, "times": [t for t, _ in samples]}
        fh.write((json.dumps(head, ensure_ascii=False) + "\n").encode("utf-8"))
        for _, f in samples:
            fh.write(f)


def load_cache(path: Path) -> tuple[list[tuple[float, bytes]], dict]:
    with gzip.open(path, "rb") as fh:
        meta = json.loads(fh.readline())
        raw = fh.read()
    if (meta.get("w"), meta.get("h")) != (W, H):
        sys.exit(f"{path}: рамка {meta.get('w')}×{meta.get('h')}, а пробник считает в {W}×{H}")
    size = W * H
    return [(t, raw[k * size:(k + 1) * size]) for k, t in enumerate(meta["times"])], meta


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

def stable_mask(frames: list[bytes], share: float = STABLE) -> list[int]:
    """Пиксели графики: в большинстве пар соседних кадров не меняются. Игра и бегущие часы — меняются."""
    if len(frames) < 3:
        return []
    size = len(frames[0])
    same = [0] * size
    for a, b in zip(frames, frames[1:]):
        for p in range(size):
            if abs(a[p] - b[p]) <= DIFF:
                same[p] += 1
    need = share * (len(frames) - 1)
    return [p for p in range(size) if same[p] >= need]


def modes(frames: list[bytes]) -> tuple[list[int], list[float]]:
    """У каждого пикселя — самое частое значение (два соседних столбца гистограммы по 16) и доля кадров, где он в
    пределах TIGHT от него. У плашки это её цвет и доля кадров с табло, даже если табло видно треть времени; у игры
    и шума частого значения нет — доля маленькая."""
    pick = frames[::max(1, len(frames) // MODE_FRAMES)]
    value, share = [], []
    for p in range(len(pick[0])):
        col = [f[p] for f in pick]
        bins = [0] * 17
        for v in col:
            bins[v >> 4] += 1
        b = max(range(16), key=lambda k: bins[k] + bins[k + 1])
        near = sorted(v for v in col if b <= v >> 4 <= b + 1)
        m = near[len(near) // 2]
        value.append(m)
        share.append(sum(abs(v - m) <= TIGHT for v in col) / len(col))
    return value, share


def plate(frames: list[bytes]) -> tuple[list[int], dict[int, int], list[int]]:
    """Графика табло, её обычный вид и ядро. Ядро — пиксели, у которых частое значение держится почти так же
    часто, как у самых надёжных (буквы, непрозрачная плашка, modes): по ядру решаем, на экране ли табло.
    Полупрозрачный фон плашки (сквозь него видно игру), цифры счёта и случайно неподвижный фон держатся реже —
    в ядро не идут. Графика целиком (с цифрами) — пиксели, которые не меняются в соседних кадрах с табло (STABLE).
    Прошлая прикидка «совпадает с соседним кадром в половине пар, ±40» брала шум и трибуны: у «Калуги» 04.10
    графикой стала вся рамка, а табло не нашлось ни в одном кадре."""
    if len(frames) < 3:
        return [], {}, []
    value, share = modes(frames)
    top = sorted(share)[int(TOP * (len(share) - 1))]
    kernel = [p for p in range(len(share)) if share[p] >= max(PRESENT, top - CORE_SLACK)]
    if len(kernel) < MIN_PLATE // 2:
        return [], {}, []
    base = {p: value[p] for p in kernel}
    vis = [f for f in frames if shown(f, kernel, base)]
    mask = stable_mask(vis)
    if len(mask) < MIN_PLATE:
        return mask, {}, kernel
    med = {**base, **usual(vis, mask)}
    return mask, med, kernel


def usual(frames: list[bytes], mask: list[int]) -> dict[int, int]:
    """Обычный вид табло: медиана каждого пикселя графики по кадрам, равномерно по записи."""
    pick = frames[::max(1, len(frames) // MEDIAN_FRAMES)]
    return {p: sorted(f[p] for f in pick)[len(pick) // 2] for p in mask}


def shown(frame: bytes, mask: list[int], med: dict[int, int]) -> bool:
    """Табло на экране: почти вся графика как обычно. Счёт — малая часть графики, его смена не мешает."""
    return bool(mask) and sum(abs(frame[p] - med[p]) <= DIFF for p in mask) >= SHOWN * len(mask)


def moved(a: bytes, b: bytes, mask: list[int]) -> list[int]:
    return [p for p in mask if abs(a[p] - b[p]) > DIFF]


def alike(a: bytes, b: bytes, pixels: list[int]) -> bool:
    return len(moved(a, b, pixels)) < len(pixels) / 2


def without_replays(mine: list[dict]) -> list[dict]:
    """Смены одной зоны по порядку. Повтор гола бывает со старым табло (у «Рязани-ВДВ» через 20 с после гола
    снова старый счёт): смена, откат к прежнему и снова та же смена за REPLAY секунд — одна смена, первая."""
    out, k = [], 0
    while k < len(mine):
        c = mine[k]
        out.append(c)
        if k + 2 < len(mine):
            back, again = mine[k + 1], mine[k + 2]
            if (again["hi"] - c["hi"] <= REPLAY and alike(back["after"], c["before"], c["pixels"])
                    and alike(again["after"], c["after"], c["pixels"])):
                k += 3
                continue
        k += 1
    return out


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
        xs, ys = {p % W for p in zone}, {p // W for p in zone}
        if len(zone) >= MIN_ZONE and min(len(xs), len(ys)) >= MIN_SIDE:
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
            report: list | None = None, core: list[int] | None = None) -> list[dict]:
    """Смены табло по зонам (zones от rare): (последний кадр со старым видом, первый с новым). Новое видно в
    большинстве кадров с табло за REPLAY секунд после смены (и их не меньше HOLD) — иначе это мелькание (плашку
    перекрыло, сменили на миг, край плашки «дрожит» от сжатия), а повтор гола со старым табло не мешает. Кадры
    без табло (повтор, перерыв, реклама) пропускаем: ждём, пока плашка вернётся. Зона, которая сменилась чаще
    ZONE_MAX раз, — скорее часы: её смены помечены `often`, а не выброшены (иначе не видно, что счёт слипся с
    часами).
    report — сюда кладём по зоне: место, размер, сколько смен. core — ядро графики для «табло на экране»."""
    vis = [(t, f) for t, f in samples if shown(f, core or mask, med)]
    out = []
    if not vis:
        return out
    for zi, zone in enumerate(zones(rare([f for _, f in vis], mask))):
        need = max(MIN_PX, min(ZONE_SHARE * len(zone), NEED_CAP))
        mine = []
        cur_t, cur = vis[0]
        for k in range(1, len(vis)):
            t, f = vis[k]
            diff = moved(cur, f, zone)
            if len(diff) < need:
                cur_t, cur = t, f
                continue
            after = list(takewhile(lambda x: x[0] - t <= REPLAY, vis[k + 1:]))
            if len(after) >= HOLD and 2 * sum(alike(f, g, diff) for _, g in after) > len(after):
                mine.append({"lo": cur_t, "hi": t, "before": cur, "after": f, "pixels": diff, "zone": zi})
                cur_t, cur = t, f
        mine = without_replays(mine)
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


def first_new(ch: dict, dense: list[tuple[float, bytes]], mask: list[int], med: dict[int, int]) -> dict | None:
    """Кадры каждую секунду вокруг смены: первая секунда с новым счётом после старого, и новое держится (в
    большинстве из следующих 2·HOLD кадров с табло). Табло между ними убрано (заставка «GOAL», повтор) — не
    мешает: смотрим только кадры с табло. mask — ядро графики."""
    marks = []
    for t, f in dense:
        if shown(f, mask, med):
            marks.append((t, len(moved(f, ch["after"], ch["pixels"])) < len(moved(f, ch["before"], ch["pixels"]))))
    lo = None
    for k, (t, new) in enumerate(marks):
        if not new:
            lo = t
            continue
        nxt = [n for _, n in marks[k + 1:k + 1 + 2 * HOLD]]
        if lo is not None and (not nxt or 2 * sum(nxt) > len(nxt)):
            return {"lo": lo, "hi": t}
    return None


def refine(ch: dict, get, mask: list[int], med: dict[int, int], step: float, tries: int = 10,
           window=None) -> dict:
    """Уточнить смену до секунды по точным кадрам. Промежуток не длиннее DENSE_SPAN — кадр каждую секунду
    (window(от, до) → кадры, first_new): у «Тверичей» после гола 5 с заставки «GOAL» и 15–30 с повтора без
    табло, деление пополам в них упиралось. Длиннее (перерыв) или кадры не скачались — делим пополам: время
    кадров первого прохода приблизительное (ключевые кадры идут раз в 2–4 с), поэтому сначала расширяем
    промежуток на шаг в обе стороны и проверяем: слева табло старое, справа новое. Посередине табло нет —
    смотрим соседние секунды; нет и там — дальше не уточняем. mask — ядро графики."""
    if window is not None and ch["hi"] - ch["lo"] <= DENSE_SPAN:
        got = first_new(ch, window(max(0.0, ch["lo"] - step), ch["hi"] + step), mask, med)
        if got:
            return {**ch, **got}

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
        mid, st = (lo + hi) / 2, None
        for at in (mid + d for d in (0, 1, -1, 2, -2) if lo < mid + d < hi):
            st = state(at)
            if st is not None:
                break
        if st is None:
            break
        lo, hi = (at, hi) if st == "old" else (lo, at)
    return {**ch, "lo": lo, "hi": hi}


def align(found: list[float], goals: list[tuple[str, str, float]], tol: float = 90,
          cands: dict[str, list[float]] | None = None) -> dict[str, float]:
    """Без эталона: какая смена табло — какой гол. goals — (счёт, период, когда сайт лиги показал гол, unix).
    Внутри периода запись и часы идут вместе: «смена минус отметка сайта» (сдвиг) почти один у всех голов
    периода (сайт запаздывает до полутора минут — tol). Лишние смены (десятки минут на часах, номер периода)
    остаются без гола. Между периодами сдвиг свой — трансляцию прерывают в перерыве (ADR-027), но на минуты,
    а не на час: начинаем с периода, где голов больше всего, а у остальных из равных берём сдвиг, ближе всего
    к уже найденному. Смена достаётся одному голу. cands — у какого гола какие смены годятся (align_sides)."""
    def pool(score: str) -> list[float]:
        return cands.get(score, found) if cands is not None else found

    out: dict[str, float] = {}
    used: set[float] = set()
    shifts: list[float] = []
    pers = list(dict.fromkeys(p for _, p, _ in goals))
    for per in sorted(pers, key=lambda q: -sum(p == q for _, p, _ in goals)):
        mine = [(s, at) for s, p, at in goals if p == per]
        best: tuple | None = None
        for s0, at0 in mine:
            for e0 in pool(s0):
                if e0 in used:
                    continue
                shift, got, taken, miss = e0 - at0, {}, set(), 0.0
                for s, at in mine:
                    near = [e for e in pool(s) if e not in used and e not in taken and abs(e - at - shift) <= tol]
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


def goal_sides(goals: list[tuple[str, str, float]]) -> dict[str, str | None]:
    """Чей гол: счёт → «home»/«away» по прошлому счёту. Скачок сразу на два — не знаем (None)."""
    out, prev = {}, (0, 0)
    for score, _, _ in sorted(goals, key=lambda g: g[2]):
        h, a = (int(x) for x in score.split(":"))
        d = (h - prev[0], a - prev[1])
        out[score] = "home" if d == (1, 0) else "away" if d == (0, 1) else None
        prev = (h, a)
    return out


def align_sides(found: list[dict], goals: list[tuple[str, str, float]],
                tol: float = 90) -> tuple[dict[str, float], tuple]:
    """Какая смена — какой гол, зная, что у каждой команды своя цифра счёта: все голы хозяев меняют одну зону
    табло, все голы гостей — другую. Перебираем пары зон и берём ту, где по сайту лиги нашлось больше голов, а
    запоздание ровнее. Так смены часов и номера периода (у «Ростова» 04.10 их было больше, чем голов) не
    путаются с голами. found — смены (changes). (счёт → секунда смены, (зона хозяев, зона гостей))."""
    side = goal_sides(goals)
    zs = sorted({c["zone"] for c in found})
    every = [c["hi"] for c in found]
    best = None
    for zh in zs + [None]:
        for za in zs + [None]:
            if zh is not None and zh == za:
                continue
            zone_of = {"home": zh, "away": za}
            cands = {s: [c["hi"] for c in found if c["zone"] == zone_of[side[s]]] if side[s] else every for s in side}
            got = align(every, goals, tol, cands)
            lags = sorted(got[s] - at for s, _, at in goals if s in got)
            spread = sum(abs(v - lags[len(lags) // 2]) for v in lags)
            key = (len(got), -spread)
            if best is None or key > best[0]:
                best = (key, got, (zh, za))
    return (best[1], best[2]) if best else ({}, (None, None))


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


def board_changes(samples: list[tuple[float, bytes]]) -> list[dict] | None:
    """Смены табло в кадрах: графика (plate) и смены по зонам. None — табло в рамке не нашлось."""
    mask, med, kernel = plate([f for _, f in samples])
    if len(mask) < MIN_PLATE:
        return None
    return changes(samples, mask, med, core=kernel)


def first_after(found: list[dict], t: float) -> dict | None:
    """--check: смена счёта у гола t — первая смена не раньше чем за EARLY с до гола. Раньше — минута на часах."""
    return next((c for c in found if not c["often"] and c["hi"] >= t - EARLY), None)


def check(name: str, src: str, headers: dict | None, truth: dict[str, int], box, args) -> list:
    """Быстрая проверка без прохода по записи (--check): у каждого гола админа окно WINDOW, кадр каждую секунду, —
    через сколько секунд после гола сменилось табло. Плюс лента табло и кадр целиком для глаз."""
    out = args.out / re.sub(r"[^\w.-]+", "_", name)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("check_*.png"):
        old.unlink()
    print(f"\n{name} — проверка по временам админа, рамка {fmt_box(box)}", flush=True)
    rows = []
    for score, t in sorted(truth.items(), key=lambda x: x[1]):
        tag = score.replace(":", "-")
        timeline(src, headers, t, box, out / f"check_{tag}_timeline.png")
        try:
            samples = scan(src, headers, box, 1, max(0, t + WINDOW[0]), t + WINDOW[1], keyframes=False)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            samples = []
        found = board_changes(samples) if samples else None
        first = first_after(found or [], t)
        save_png(src, headers, first["hi"] + 1 if first else t + args.after, out / f"check_{tag}_frame.png")
        rows.append((score, t, first["hi"] if first else None))
        head = f"  гол {score} ({replay.fmt_t(t)}): "
        if not samples:
            print(head + "кадры не скачались")
            continue
        if found is None:
            print(head + "табло в рамке не нашлось — посмотри check_*_timeline.png и задай --box")
            continue
        if not first:
            print(head + "табло не сменилось — посмотри check_*_timeline.png")
        else:
            xs, ys = [p % W for p in first["pixels"]], [p // W for p in first["pixels"]]
            gap = (f" (между {round(first['lo'] - t):+d} и {round(first['hi'] - t):+d} с табло не было)"
                   if first["hi"] - first["lo"] > 2 else "")
            print(head + f"табло сменилось через {round(first['hi'] - t):+d} с{gap}, в рамке x {min(xs)}–{max(xs)}, "
                  f"y {min(ys)}–{max(ys)}")
            save_raw(first["before"] + first["after"], out / f"check_{tag}_change.png", W, 2 * H)
        rest = [round(c["hi"] - t) for c in found if c is not first]
        if rest:
            print(f"    ещё смены в окне, с от гола: {rest}")
    print("  " + lag_summary(rows))
    print(f"  картинки — {out}: check_*_timeline.png — табло через "
          f"{', '.join(f'{d:+d}' for d in TIMELINE)} с от гола (сверху вниз), check_*_change.png — рамка до и после "
          f"смены, check_*_frame.png — кадр целиком", flush=True)
    return rows


def probe(name: str, src: str | None, headers: dict | None, truth: dict[str, int], site: list, box, args,
          samples: list | None = None) -> list:
    """Проход по записи. src=None — разбор кадров из кэша без сети (--cache): без уточнения и картинок кадров."""
    out = args.out / re.sub(r"[^\w.-]+", "_", name)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):   # картинки прошлого запуска — не путать с новыми
        old.unlink()
    print(f"\n{name}, рамка {fmt_box(box)}", flush=True)
    cache = cache_file(out, box, args.step, args.start, args.end)
    if samples is None and cache.exists() and not args.rescan:
        samples, _ = load_cache(cache)
        print(f"  кадры первого прохода — из {cache.name}: {len(samples)}", flush=True)
    elif samples is None:
        print(f"  качаю запись и смотрю кадр раз в {args.step} с — несколько минут…", flush=True)
        began = time.monotonic()
        samples = scan(src, headers, box, args.step, args.start, args.end)
        print(f"  кадров первого прохода: {len(samples)} за {round(time.monotonic() - began)} с", flush=True)
        if samples:
            save_cache(cache, samples, {"name": name, "box": list(box), "step": args.step, "truth": truth,
                                        "site": [list(g) for g in site]})
    if src:
        for score, t in sorted(truth.items(), key=lambda x: x[1]):   # кадр целиком через минуту после гола
            save_png(src, headers, t + 60, out / f"goal_{score.replace(':', '-')}_frame.png")
    if not samples:
        print("  кадров нет: поток не открылся")
        return []
    frames = [f for _, f in samples]
    mask, med, kernel = plate(frames)
    print(f"  пикселей графики в рамке: {len(mask)} из {W * H}, ядро — {len(kernel)}")
    if len(mask) < MIN_PLATE:
        print("  табло в рамке не нашлось: посмотри goal_*_frame.png и задай --box")
        return []
    seen = sum(shown(f, kernel, med) for f in frames)
    print(f"  табло на экране в {seen} кадрах из {len(frames)} ({round(100 * seen / len(frames))}%)")
    save_raw(bytes(med.get(p, 0) for p in range(W * H)), out / "board_usual.png")
    save_raw(bytes(255 if p in set(kernel) else med.get(p, 0) // 3 for p in range(W * H)), out / "board_core.png")
    report: list = []
    found = changes(samples, mask, med, report, core=kernel)
    save_raw(bytes(255 if any(z["box"][0] <= p % W <= z["box"][1] and z["box"][2] <= p // W <= z["box"][3]
                              for z in report) else med.get(p, 0) // 3 for p in range(W * H)), out / "board_zones.png")
    print(f"  зон, которые иногда меняются: {len(report)}")
    for z in report:
        x1, x2, y1, y2 = z["box"]
        print(f"    зона {z['zone']}: x {x1}–{x2}, y {y1}–{y2}, пикселей {z['size']}, смен {z['changes']}"
              + (" — часто, похоже на часы" if z["often"] else ""))
    if src:
        print("  уточняю смены до секунды…", flush=True)
        exact = [refine(c, lambda t: grab(src, headers, box, t), kernel, med, args.step,
                        window=lambda a, b: safe_scan(src, headers, box, a, b)) for c in found]
    else:
        exact = found
    main_ = [c for c in exact if not c["often"]]
    for k, c in enumerate(main_, 1):
        save_raw(c["before"] + c["after"], out / f"change_{k:02d}.png", W, 2 * H)
    picked, (zh, za) = align_sides(main_, site)
    by_t = {t: s for s, t in picked.items()}
    print(f"  смен табло: {len(main_)} (и ещё {len(exact) - len(main_)} в частых зонах — часы?), "
          f"голов у админа: {len(truth)}, у сайта лиги: {len(site)}")
    if site:
        print(f"  по сайту лиги: счёт хозяев — зона {zh if zh is not None else '—'}, "
              f"гостей — зона {za if za is not None else '—'}; найдено голов {len(picked)} из {len(site)}")
    for k, c in enumerate(main_, 1):
        xs, ys = [p % W for p in c["pixels"]], [p // W for p in c["pixels"]]
        where = f"x {min(xs)}–{max(xs)}, y {min(ys)}–{max(ys)}"
        goal = by_t.get(c["hi"])
        print(f"    {k:2d}. {replay.fmt_t(int(c['hi']))} (±{round(c['hi'] - c['lo'])} с), зона {c['zone']} [{where}], "
              f"пикселей {len(c['pixels'])} · " + (f"по сайту лиги — гол {goal}" if goal else "без гола"))
    nearest = against([c["hi"] for c in main_], truth)
    rows = [(score, t, picked.get(score)) for score, t, _ in nearest] if site else nearest
    if truth:
        print("  против админа (смена — " + ("та, что по сайту лиги):" if site else "первая после гола):"))
        for (score, t, e), (_, _, near) in zip(rows, nearest):
            if e is not None:
                print(f"    гол {score} у админа {replay.fmt_t(t)}: табло через {round(e - t):+d} с")
            else:
                hint = f"; ближайшая смена после гола — через {round(near - t):+d} с" if near is not None else ""
                why = "по сайту лиги смены нет" if site else "на табло не нашёлся"
                print(f"    гол {score} у админа {replay.fmt_t(t)}: {why}{hint if site else ''}")
        print("  " + lag_summary(rows))
    print(f"  картинки — {out}: board_usual.png — обычный вид табло, board_core.png — ядро (белое), "
          f"board_zones.png — зоны (белое), change_*.png — рамка до и после смены; кадры — {cache.name}")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Голы по табло трансляции (ADR-029)")
    ap.add_argument("--match", help="ключ матча из live/replays.json: <дата>|<хозяева>|<гости>; без него — все размеченные")
    ap.add_argument("--stream", help="файл или адрес потока вместо ролика VK (для проверки)")
    ap.add_argument("--truth", default="", help="для --stream: голы «1:0=42:53,0:2=49:28»")
    ap.add_argument("--box", type=parse_box, help="рамка табло в долях кадра: x,y,ширина,высота; "
                    "без неё — рамка клуба-хозяина из BOXES или левый верх кадра")
    ap.add_argument("--step", type=int, default=STEP)
    ap.add_argument("--start", type=int, default=0, help="с какой секунды записи смотреть")
    ap.add_argument("--end", type=int, default=None, help="до какой секунды")
    ap.add_argument("--check", action="store_true",
                    help="быстро, без прохода по записи: что сменилось на табло вокруг каждого гола админа")
    ap.add_argument("--after", type=int, default=40,
                    help="для --check: кадр целиком через столько секунд после гола, если смена табло не нашлась")
    ap.add_argument("--rescan", action="store_true", help="качать запись заново, даже если кадры уже в кэше")
    ap.add_argument("--cache", type=Path, nargs="+",
                    help="файлы кадров frames_*.gz прошлых запусков: разбор без сети и без сервера")
    ap.add_argument("--live", type=Path, default=Path(os.environ.get("LIVE_DIR") or ROOT / "live"))
    ap.add_argument("--out", type=Path, default=ROOT / "probe" / "scoreboard")
    args = ap.parse_args()
    if args.cache:
        every = []
        for path in args.cache:
            samples, meta = load_cache(path)
            args.step = meta["step"]
            every += probe(meta["name"], None, None, meta.get("truth") or {},
                           [tuple(g) for g in meta.get("site") or []], tuple(meta["box"]), args, samples)
        if len(args.cache) > 1:
            print("\nВсего: " + lag_summary(every))
        return
    if args.stream:
        name, truth = Path(args.stream).name, parse_truth(args.truth)
        if args.check:
            check(name, args.stream, None, truth, args.box or BOX, args)
        else:
            probe(name, args.stream, None, truth, [], args.box or BOX, args)
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
        box = args.box or BOXES.get(key.split("|")[1], BOX)
        if args.check:
            every += check(key, src, headers, truth, box, args)
        else:
            every += probe(key, src, headers, truth, site_goals(args.live, key), box, args)
    if len(keys) > 1:
        print("\nВсего: " + lag_summary(every))
    if args.check:
        print("\nПришли этот вывод целиком и 2–3 картинки check_*_timeline.png и check_*_change.png (ADR-029).")
    else:
        print("\nПришли этот вывод целиком и файлы кадров probe/scoreboard/*/frames_*.gz (ADR-029): по ним пробник "
              "можно настраивать без сервера.")


if __name__ == "__main__":
    main()

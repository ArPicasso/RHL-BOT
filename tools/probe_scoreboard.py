"""Найти голы по табло трансляции (ADR-029, решение владельца 05.10): когда в записи сменился счёт. Запускать на VPS:

    apt install -y ffmpeg && venv/bin/pip install yt-dlp     # один раз
    cd /opt/rhl && venv/bin/python tools/probe_scoreboard.py                     # все размеченные матчи
    venv/bin/python tools/probe_scoreboard.py --match 2026-10-04|rostov|krasnodar
    venv/bin/python tools/probe_scoreboard.py --check                            # быстро: только окна вокруг голов
    venv/bin/python tools/probe_scoreboard.py --days 3                           # и все сыгранные с записью лиги
    venv/bin/python tools/probe_scoreboard.py --stream probe/x.mp4 --truth 1:0=42:53,0:2=49:28   # файл

Табло — плашка со счётом в углу кадра, она стоит на месте всю игру. Пробник смотрит запись целиком и редко
(кадр раз в `--step` секунд, только ключевые кадры, 480p — качается быстро) и вырезает из каждого кадра рамку
табло: у клубов из `BOARDS` — вплотную к их плашке, у остальных — левый верх кадра, `--box` — своя. Пиксели,
которые почти не меняются от кадра к кадру, — графика табло: названия, фон плашки и счёт (он меняется только
при голе). Пиксели игры и бегущих часов меняются постоянно — их не берём. Смена счёта — когда часть пикселей
графики сменилась и новое держится в следующих кадрах; плашку убрали на повтор или перерыв — не смена, мы
ждём, пока она вернётся. Повтор гола бывает со старым табло — смена туда и обратно за пару минут не гол.
Найденный промежуток уточняем делением пополам до секунды.

У клубов из `BOARDS` размечены клетки табло: название хозяев (по нему видно, что табло на экране), цифры счёта и
часы игры. Тогда смены ищем только в клетках цифр, какой гол — по порядку голов команды (align_order), а когда —
по остановке часов перед сменой счёта (clock_stop). Это нашло 14 голов из 15 на кадрах 04.10; прикидка по всей
рамке выше — для клубов без разметки.

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
import urllib.request
from datetime import date, timedelta
from itertools import takewhile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import replay  # noqa: E402

W, H = 240, 90          # рамка табло после масштаба, пикселей: хватает, чтобы цифра счёта была в 10–20 пикселей
STEP = 10               # с между кадрами первого прохода
BOX = (0.02, 0.02, 0.28, 0.26)   # рамка в долях кадра: x, y, ширина, высота — левый верх, где табло у трансляций РХЛ
BOARDS_FILE = ROOT / "boards.json"   # разметка табло клубов-хозяев (ADR-030): рамка, клетки и задержка


def load_boards(path: Path = BOARDS_FILE) -> dict[str, dict]:
    """Табло клубов из boards.json: рамка в долях кадра `box`, в пикселях рамки W×H (x0, y0, x1, y1) — название хозяев
    `name` (по нему видно, что табло на экране), цифры счёта `home` и `away`, часы игры `clock`; `lag` — проверенная
    задержка табло. Разметка по кадрам 04.10 (ADR-029). Новый клуб: grid.png службы clips или --check."""
    try:
        clubs = json.loads(path.read_text(encoding="utf-8"))["clubs"]
    except (OSError, ValueError, KeyError):
        return {}
    return {club: {k: tuple(v) if isinstance(v, list) else v for k, v in b.items()} for club, b in clubs.items()}


BOARDS = load_boards()
BOXES = {club: b["box"] for club, b in BOARDS.items()}
ON_SCREEN = 0.6         # табло на экране: столько тёмных и столько светлых пикселей названия как обычно (04.10: 0,8–1 и 0–0,3)
CELL_MIN = 40           # смена цифры: сменилось не меньше стольких пикселей клетки…
CELL_SHARE = 0.1        # …и такой её доли. 04.10: шум сжатия — 0–28 пикселей, смена цифры — 58–250
EXACT = 15              # с: табло между старым и новым счётом пропадало не дольше — секунда смены точная
ORDER_TOL = 300         # с: смена годится голу, если не дальше стольких секунд от ожидаемой по сайту лиги
CLOCK_BACK = 120        # с: остановку часов перед сменой счёта ищем не раньше стольких секунд до неё
CLOCK_MOVED = 8         # пикселей клетки часов: столько сменилось — часы идут (секунды меняются каждую секунду)
CLUB_LAG = {club: b["lag"] for club, b in BOARDS.items() if b.get("lag") is not None}   # задержка табло клуба, с
SITE = "rhl.fhr.ru"     # запись лиги — «Смотреть» с этим источником в league.json (rhl_media.py)
WEBAPP_URL = os.environ.get("WEBAPP_URL") or "https://arpicasso.github.io/RHL-BOT/"
GRID = (0.0, 0.0, 0.5, 0.35)   # --grid: где искать табло нового клуба — левый верх кадра
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
    снова старый счёт, бывает и через 12 минут): смена, откат к прежнему и снова та же смена, а между откатом и
    возвратом не больше REPLAY секунд, — одна смена, первая."""
    out, k = [], 0
    while k < len(mine):
        c = mine[k]
        out.append(c)
        if k + 2 < len(mine):
            back, again = mine[k + 1], mine[k + 2]
            if (again["hi"] - back["hi"] <= REPLAY and alike(back["after"], c["before"], c["pixels"])
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


def first_new(ch: dict, dense: list[tuple[float, bytes]], visible) -> dict | None:
    """Кадры каждую секунду вокруг смены: первая секунда с новым счётом после старого, и новое держится (в
    большинстве из следующих 2·HOLD кадров с табло). Табло между ними убрано (заставка «GOAL», повтор) — не
    мешает: смотрим только кадры с табло (visible(кадр) → да/нет)."""
    marks = []
    for t, f in dense:
        if visible(f):
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


def refine(ch: dict, get, visible, step: float, tries: int = 10, window=None) -> dict:
    """Уточнить смену до секунды по точным кадрам. Промежуток не длиннее DENSE_SPAN — кадр каждую секунду
    (window(от, до) → кадры, first_new): у «Тверичей» после гола 5 с заставки «GOAL» и 15–30 с повтора без
    табло, деление пополам в них упиралось. Длиннее (перерыв) или кадры не скачались — делим пополам: время
    кадров первого прохода приблизительное (ключевые кадры идут раз в 2–4 с), поэтому сначала расширяем
    промежуток на шаг в обе стороны и проверяем: слева табло старое, справа новое. Посередине табло нет —
    смотрим соседние секунды; нет и там — дальше не уточняем. visible(кадр) — табло на экране."""
    if window is not None and ch["hi"] - ch["lo"] <= DENSE_SPAN:
        got = first_new(ch, window(max(0.0, ch["lo"] - step), ch["hi"] + step), visible)
        if got:
            return {**ch, **got}

    def state(t: float) -> str | None:
        f = get(max(0.0, t))
        if f is None or not visible(f):
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


def cell_pixels(rect: tuple[int, int, int, int]) -> list[int]:
    x0, y0, x1, y1 = rect
    return [y * W + x for y in range(y0, y1) for x in range(x0, x1)]


def name_model(frames: list[bytes], rect) -> dict | None:
    """Как выглядит название хозяев на табло: частое значение каждого пикселя клетки (modes) — табло видно хотя
    бы треть записи. Пиксели делим на тёмные и светлые: белый лёд совпадает со светлыми, тёмная трибуна — с
    тёмными, а название целиком — только само табло. Нет контраста — клетка не та."""
    pixels = cell_pixels(rect)
    value, _ = modes([bytes(f[p] for p in pixels) for f in frames])
    mid = (min(value) + max(value)) / 2
    if max(value) - min(value) < 2 * DIFF:
        return None
    return {"pixels": pixels, "value": value, "dark": [i for i, v in enumerate(value) if v < mid],
            "light": [i for i, v in enumerate(value) if v >= mid]}


def on_screen(frame: bytes, model: dict) -> bool:
    px, val = model["pixels"], model["value"]
    for part in (model["dark"], model["light"]):
        if sum(abs(frame[px[i]] - val[i]) <= DIFF for i in part) < ON_SCREEN * len(part):
            return False
    return True


def cell_changes(vis: list[tuple[float, bytes]], cell: list[int]) -> list[dict]:
    """Смены цифры в клетке по кадрам с табло (vis): клетка сменилась (CELL_MIN, CELL_SHARE), и новое — в
    большинстве кадров с табло за REPLAY секунд после (мелькание и повтор со старым табло — нет). Откат и
    возврат за REPLAY — повтор (without_replays)."""
    out: list[dict] = []
    if not vis:
        return out
    need = max(CELL_MIN, CELL_SHARE * len(cell))
    cur_t, cur = vis[0]
    for k in range(1, len(vis)):
        t, f = vis[k]
        diff = moved(cur, f, cell)
        if len(diff) < need:
            cur_t, cur = t, f
            continue
        after = list(takewhile(lambda x: x[0] - t <= REPLAY, vis[k + 1:]))
        if len(after) >= HOLD and 2 * sum(alike(f, g, diff) for _, g in after) > len(after):
            out.append({"lo": cur_t, "hi": t, "before": cur, "after": f, "pixels": diff})
            cur_t, cur = t, f
    return without_replays(out)


def analyse(samples: list[tuple[float, bytes]], board: dict | None = None, report: list | None = None):
    """Кадры рамки → (табло на экране: функция кадра, смены [{lo, hi, before, after, pixels, zone, often}]).
    board — разметка клуба из BOARDS: смены по клеткам цифр, zone — «home»/«away». Без разметки — прикидка по
    всей рамке (plate, changes), зоны ищутся сами; на записях 04.10 она путала фон с табло (ADR-029).
    Табло не нашлось — (None, None)."""
    frames = [f for _, f in samples]
    if board:
        model = name_model(frames, board["name"])
        if model:
            vis = [(t, f) for t, f in samples if on_screen(f, model)]
            found = [{**c, "zone": side, "often": False}
                     for side in ("home", "away") for c in cell_changes(vis, cell_pixels(board[side]))]
            return (lambda f: on_screen(f, model)), sorted(found, key=lambda c: c["hi"])
    mask, med, kernel = plate(frames)
    if len(mask) < MIN_PLATE:
        return None, None
    return (lambda f: shown(f, kernel, med)), changes(samples, mask, med, report, core=kernel)


def clock_stop(vis: list[tuple[float, bytes]], hi: float, clock: list[int], gap: float) -> tuple | None:
    """Когда встали часы игры перед сменой счёта: гол останавливает часы сразу, а счёт оператор меняет и через
    минуту (у «Калуги» 04.10 часы встали за 7 с до отметки админа, счёт — через 59 с после). Берём значение часов
    в момент смены счёта — они должны стоять (следующий кадр с табло тот же) — и ищем последнюю пару соседних
    кадров с табло (не дальше gap секунд), где часы шли и пришли к нему. Оператор поправил счёт, когда часы уже снова шли, или в повторе на табло старые часы
    («Рязань-ВДВ») — такой пары нет, None. (последний кадр, где часы шли; первый, где стоят)."""
    after = [(t, f) for t, f in vis if t >= hi]
    if len(after) < 2 or after[1][0] - after[0][0] > 3 * gap or len(moved(after[0][1], after[1][1], clock)) >= CLOCK_MOVED:
        return None   # в момент смены счёта часы идут (или не видно) — остановки на этом значении нет
    ref = after[0][1]
    win = [(t, f) for t, f in vis if hi - CLOCK_BACK <= t <= hi]
    stop = None
    for (ta, fa), (tb, fb) in zip(win, win[1:]):
        if (tb - ta <= gap and len(moved(fa, fb, clock)) >= CLOCK_MOVED
                and len(moved(fb, ref, clock)) < CLOCK_MOVED):
            stop = (ta, tb)
    return stop


def goal_time(change: float, exact: bool, stop: tuple | None, lag: float | None) -> tuple[float | None, str | None]:
    """Секунда гола для повтора и клипа по табло (решение 05.10): часы встали — последняя секунда, когда они
    шли; табло не пропадало и задержка клуба проверена — смена счёта минус задержка. Иначе не знаем: (None, None),
    гол — админу (превью). (секунда, источник «clock»/«board»)."""
    if stop:
        return stop[0], "clock"
    if exact and lag is not None:
        return change - lag, "board"
    return None, None


def align_order(found: list[dict], goals: list[tuple[str, str, float]]) -> dict[str, float]:
    """Какая смена — какой гол, когда смены по клеткам цифр (zone «home»/«away»). Цифра команды только растёт,
    поэтому k-я смена цифры хозяев — k-й гол хозяев: голы и смены одной команды сопоставляем по порядку
    (монотонно), лишние смены (повтор со старым табло, табло после матча) пропускаем. Время сайта лиги — только
    для сверки: оно запаздывает неровно, внутри периода разброс до двух минут (04.10). Сдвиг «запись − сайт» —
    свой у периода (трансляцию прерывают в перерыве): тот, при котором больше голов периода со сменой своей
    команды в ±150 с; пара годится в ±ORDER_TOL. (счёт → секунда смены)."""
    side = goal_sides(goals)
    pers = list(dict.fromkeys(p for _, p, _ in goals))
    shift: dict[str, float] = {}
    for per in sorted(pers, key=lambda q: -sum(p == q for _, p, _ in goals)):
        mine = [(s, at) for s, p, at in goals if p == per and side[s]]
        best = None
        for s0, at0 in mine:
            for c0 in (c for c in found if c["zone"] == side[s0]):
                d = c0["hi"] - at0
                near = [min((abs(c["hi"] - at - d) for c in found if c["zone"] == side[s]), default=1e9)
                        for s, at in mine]
                key = (sum(v <= 150 for v in near), -min((abs(d - x) for x in shift.values()), default=0.0),
                       -sum(v for v in near if v <= 150))
                if best is None or key > best[0]:
                    best = (key, d)
        if best:
            shift[per] = best[1]
    if not shift:
        return {}
    middle = sorted(shift.values())[len(shift) // 2]
    out: dict[str, float] = {}
    for team in ("home", "away"):
        gs = sorted((at + shift.get(p, middle), s) for s, p, at in goals if side[s] == team)
        cs = sorted(c["hi"] for c in found if c["zone"] == team)
        # best[i][j] — (−пар, промах) для первых i голов и j смен; пропуск смены бесплатный, гола — ORDER_TOL
        best = [[(0, 0.0)] * (len(cs) + 1) for _ in range(len(gs) + 1)]
        how = [[""] * (len(cs) + 1) for _ in range(len(gs) + 1)]
        for i in range(len(gs) + 1):
            for j in range(len(cs) + 1):
                if not i and not j:
                    continue
                opts = []
                if j:
                    opts.append((best[i][j - 1], "c"))
                if i:
                    k, miss = best[i - 1][j]
                    opts.append(((k, miss + ORDER_TOL), "g"))
                if i and j and abs(cs[j - 1] - gs[i - 1][0]) <= ORDER_TOL:
                    k, miss = best[i - 1][j - 1]
                    opts.append(((k - 1, miss + abs(cs[j - 1] - gs[i - 1][0])), "="))
                best[i][j], how[i][j] = min(opts)
        i, j = len(gs), len(cs)
        while i or j:
            if how[i][j] == "=":
                out[gs[i - 1][1]] = cs[j - 1]
                i, j = i - 1, j - 1
            elif how[i][j] == "g":
                i -= 1
            else:
                j -= 1
    return out


def align_by_order(found: list[dict], order: list[tuple[str, str]]) -> dict[str, float]:
    """Какая смена — какой гол, когда времени голов от сайта лиги нет (служба live пропустила матч, ADR-030): цифра
    команды только растёт, поэтому k-я смена цифры хозяев — k-й гол хозяев по протоколу. Только если смен у команды
    ровно столько, сколько голов: лишняя смена (оператор поправил счёт, табло после матча) сдвигает весь ряд, и
    тогда не угадываем — голы этой команды остаются админу. order — [(счёт, «home»/«away»)] по порядку протокола.
    (счёт → секунда смены)."""
    out: dict[str, float] = {}
    for team in ("home", "away"):
        goals = [score for score, side in order if side == team]
        cs = sorted(c["hi"] for c in found if c.get("zone") == team)
        if goals and len(cs) == len(goals):
            out.update(zip(goals, cs))
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

def stream_of(video: str, fmt_: str = FORMAT) -> tuple[str, dict, int | None]:
    """Ролик VK → адрес потока для ffmpeg, заголовки и длительность (yt-dlp, как плеер). fmt_ — какой поток
    брать: для табло хватает 480p, для клипов (probe_cuts.py) — 720p."""
    try:
        import yt_dlp
    except ImportError:
        sys.exit("Нет yt-dlp: venv/bin/pip install yt-dlp")
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "format": fmt_}) as ydl:
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


def board_changes(samples: list[tuple[float, bytes]], board: dict | None = None) -> list[dict] | None:
    """Смены табло в кадрах (analyse). None — табло в рамке не нашлось."""
    return analyse(samples, board)[1]


def first_after(found: list[dict], t: float) -> dict | None:
    """--check: смена счёта у гола t — первая смена не раньше чем за EARLY с до гола. Раньше — минута на часах."""
    return next((c for c in found if not c["often"] and c["hi"] >= t - EARLY), None)


def board_of(name: str, box) -> dict | None:
    """Разметка табло клуба-хозяина, если рамка та же, что в BOARDS (своя --box — клетки не те)."""
    parts = name.split("|")
    board = BOARDS.get(parts[1]) if len(parts) == 3 else None
    return board if board and tuple(board["box"]) == tuple(box) else None


def check(name: str, src: str, headers: dict | None, truth: dict[str, int], box, args) -> list:
    """Быстрая проверка без прохода по записи (--check): у каждого гола админа окно WINDOW, кадр каждую секунду, —
    через сколько секунд после гола сменилось табло. Плюс лента табло и кадр целиком для глаз."""
    out = args.out / re.sub(r"[^\w.-]+", "_", name)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("check_*.png"):
        old.unlink()
    board = board_of(name, box)
    print(f"\n{name} — проверка по временам админа, рамка {fmt_box(box)}"
          + ("" if board else " (клеток табло для клуба нет — прикидка по всей рамке)"), flush=True)
    rows = []
    for score, t in sorted(truth.items(), key=lambda x: x[1]):
        tag = score.replace(":", "-")
        timeline(src, headers, t, box, out / f"check_{tag}_timeline.png")
        try:
            samples = scan(src, headers, box, 1, max(0, t + WINDOW[0]), t + WINDOW[1], keyframes=False)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            samples = []
        found = board_changes(samples, board) if samples else None
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
          samples: list | None = None) -> tuple[list, list]:
    """Проход по записи. src=None — разбор кадров из кэша без сети (--cache): без уточнения и картинок кадров.
    (все голы админа, только точные) — строки (счёт, секунда у админа, секунда смены табло или None)."""
    out = args.out / re.sub(r"[^\w.-]+", "_", name)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):   # картинки прошлого запуска — не путать с новыми
        old.unlink()
    board = board_of(name, box)
    print(f"\n{name}, рамка {fmt_box(box)}" + ("" if board else " (клеток табло для клуба нет — прикидка по всей "
                                                                  "рамке, ненадёжно)"), flush=True)
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
        return [], []
    report: list = []
    visible, found = analyse(samples, board, report)
    if visible is None:
        print("  табло в рамке не нашлось: посмотри goal_*_frame.png и задай --box")
        return [], []
    seen = sum(visible(f) for _, f in samples)
    print(f"  табло на экране в {seen} кадрах из {len(samples)} ({round(100 * seen / len(samples))}%)")
    for z in report:
        x1, x2, y1, y2 = z["box"]
        print(f"    зона {z['zone']}: x {x1}–{x2}, y {y1}–{y2}, пикселей {z['size']}, смен {z['changes']}"
              + (" — часто, похоже на часы" if z["often"] else ""))
    if src:
        print(f"  уточняю {len(found)} смен по кадру каждую секунду…", flush=True)
        found = [refine(c, lambda t: grab(src, headers, box, t), visible, args.step,
                        window=lambda a, b: safe_scan(src, headers, box, a, b)) for c in found]
    main_ = [c for c in found if not c["often"]]
    for k, c in enumerate(main_, 1):
        save_raw(c["before"] + c["after"], out / f"change_{k:02d}.png", W, 2 * H)
    cells = bool(main_) and all(c["zone"] in ("home", "away") for c in main_)
    order = getattr(args, "order", None)
    if cells and not site and order:   # служба live времени голов не записала — по порядку голов из протокола
        picked = align_by_order(main_, order)
        print(f"  времени голов от сайта лиги нет — по порядку протокола: {len(picked)} из {len(order)}")
    else:
        picked = align_order(main_, site) if cells else align_sides(main_, site)[0]
    by_t = {t: s for s, t in picked.items()}
    print(f"  смен табло: {len(main_)}, голов у админа: {len(truth)}, у сайта лиги: {len(site)}; "
          f"по сайту лиги нашлось {len(picked)} из {len(site)}")
    for k, c in enumerate(main_, 1):
        where = {"home": "хозяева", "away": "гости"}.get(c["zone"], f"зона {c['zone']}")
        gap = round(c["hi"] - c["lo"])
        goal = by_t.get(c["hi"])
        print(f"    {k:2d}. {replay.fmt_t(int(c['hi']))}, {where}, " + ("точно" if gap <= EXACT else
              f"табло не было {gap} с") + " · " + (f"по сайту лиги — гол {goal}" if goal else "без гола"))
    stops: dict[str, tuple] = {}
    if board and board.get("clock"):
        clock = cell_pixels(board["clock"])
        for score, e in picked.items():
            if src:   # кадр каждую секунду за CLOCK_BACK до смены счёта
                dense = [(t, f) for t, f in safe_scan(src, headers, box, e - CLOCK_BACK, e + 5) if visible(f)]
                got = clock_stop(dense, e, clock, 2)
            else:
                got = clock_stop([(t, f) for t, f in samples if visible(f)], e, clock, args.step)
            if got:
                stops[score] = got
    nearest = against([c["hi"] for c in main_], truth)
    rows = [(score, t, picked.get(score)) for score, t, _ in nearest] if site else nearest
    exact_t = {c["hi"] for c in main_ if c["hi"] - c["lo"] <= EXACT}
    exact = [r for r in rows if r[2] in exact_t]
    if truth:
        print("  против админа (смена — " + ("та, что по сайту лиги):" if site else "первая после гола):"))
        for (score, t, e), (_, _, near) in zip(rows, nearest):
            if e is not None:
                mark = "точно" if e in exact_t else "табло пропадало"
                st = stops.get(score)
                clock_txt = (f"; часы встали между {round(st[0] - t):+d} и {round(st[1] - t):+d} с" if st
                             else "; остановки часов не видно" if board and board.get("clock") else "")
                print(f"    гол {score} у админа {replay.fmt_t(t)}: счёт на табло через {round(e - t):+d} с ({mark})"
                      + clock_txt)
            else:
                why = "по сайту лиги смены нет" if site else "на табло не нашёлся"
                hint = f"; ближайшая смена после гола — через {round(near - t):+d} с" if site and near is not None else ""
                print(f"    гол {score} у админа {replay.fmt_t(t)}: {why}{hint}")
        print("  все: " + lag_summary(rows))
        print("  точные: " + lag_summary(exact))
        if stops:
            print("  по часам: " + lag_summary([(s, t, stops[s][1] if s in stops else None) for s, t, _ in rows]))
    lag = CLUB_LAG.get(name.split("|")[1]) if name.count("|") == 2 else None
    goals = []
    for score, e in sorted(picked.items(), key=lambda x: x[1]):
        t, how = goal_time(e, e in exact_t, stops.get(score), lag)
        goals.append({"score": score, "change": e, "exact": e in exact_t, "clock": list(stops[score]) if score in stops
                      else None, "t": t, "src": how})
    timed = [g for g in goals if g["t"] is not None]
    (out / "goals.json").write_text(json.dumps({"key": name, "goals": goals}, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    print(f"  время гола по табло (для повтора и клипа): {len(timed)} из {len(site) or len(goals)}"
          + (" — " + ", ".join(f"{g['score']} {replay.fmt_t(int(g['t']))} ({'часы' if g['src'] == 'clock' else 'табло'})"
                               for g in timed) if timed else ""))
    print(f"  картинки — {out}: change_*.png — рамка до и после смены; голы — goals.json; кадры — {cache.name}")
    return rows, exact


def data_url(name: str) -> str:
    """Файл данных рядом с мини-аппом на Pages: .../data/<name> (как в bot.py)."""
    u = urlsplit(WEBAPP_URL)
    path = u.path if u.path.endswith("/") else u.path.rsplit("/", 1)[0] + "/"
    return urlunsplit(u._replace(path=path + "data/" + name, query="", fragment=""))


def league_json(path: Path | None) -> dict | None:
    """league.json: файл (--league) или опубликованный с мини-аппом. Не достали — None."""
    try:
        if path:
            return json.loads(path.read_text(encoding="utf-8"))
        with urllib.request.urlopen(data_url("league.json"), timeout=30) as r:
            return json.load(r)
    except (OSError, ValueError) as err:
        print(f"league.json не достали — {type(err).__name__}: {err}")
        return None


def recorded(league: dict | None, days: set[str]) -> dict[str, dict]:
    """Сыгранные матчи этих дней с записью лиги («Смотреть» от rhl.fhr.ru в league.json, ADR-028): ключ матча
    как у службы live → {"video": страница ролика VK, "anchors": {}} — опор админа у них нет."""
    out = {}
    for g in (league or {}).get("games") or []:
        if not (isinstance(g, dict) and g.get("date") in days and g.get("score")):
            continue
        for w in g.get("watch") or []:
            got = replay.parse_link(w.get("url") or "") if isinstance(w, dict) and w.get("src") == SITE else None
            if got:
                out[f"{g['date']}|{g.get('home')}|{g.get('away')}"] = {"video": got[0], "anchors": {}}
                break
    return out


def matches(args) -> dict[str, dict]:
    """Какие матчи разбирать: размеченные админом (live/replays.json) и, с --days/--date, все сыгранные с записью
    лиги за эти дни. Размеченный главнее: у него опоры админа."""
    try:
        marked = json.loads((args.live / "replays.json").read_text(encoding="utf-8")).get("games") or {}
    except (OSError, ValueError):
        marked = {}
    days = set(args.date or [])
    if args.days:
        today = date.today()
        days |= {(today - timedelta(days=k)).isoformat() for k in range(args.days)}
    found = recorded(league_json(args.league), days) if days else {}
    return {**found, **marked}


def grid_sheet(src: str, headers: dict | None, length: float | None, path: Path) -> None:
    """Табло нового клуба ещё не размечено: три кадра записи (четверть, половина, три четверти) — левый верх
    кадра GRID с сеткой: тонкие линии — каждые 0,02 кадра, жёлтые — каждые 0,1. По ней размечаем рамку и клетки
    (BOARDS)."""
    x, y, w, h = GRID
    vf = (f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale=960:-2,"
          f"drawgrid=w=iw*{0.02 / w}:h=ih*{0.02 / h}:t=1:c=white@0.35,"
          f"drawgrid=w=iw*{0.1 / w}:h=ih*{0.1 / h}:t=2:c=yellow@0.8")
    parts = []
    for k, share in enumerate((0.25, 0.5, 0.75)):
        part = path.with_name(f"{path.stem}_{k}.png")
        subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *header_args(headers),
                        "-ss", str(int((length or 7200) * share)), "-i", src, "-frames:v", "1", "-vf", vf, str(part)],
                       capture_output=True, timeout=180)
        if part.exists():
            parts.append(part)
    if parts:
        args = [a for part in parts for a in ("-i", str(part))]
        subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args, "-filter_complex",
                        f"vstack=inputs={len(parts)}" if len(parts) > 1 else "null", str(path)],
                       capture_output=True, timeout=120)
    for part in parts:
        part.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description="Голы по табло трансляции (ADR-029)")
    ap.add_argument("--match", help="ключ матча из live/replays.json: <дата>|<хозяева>|<гости>; без него — все размеченные")
    ap.add_argument("--stream", help="файл или адрес потока вместо ролика VK (для проверки)")
    ap.add_argument("--truth", default="", help="для --stream: голы «1:0=42:53,0:2=49:28»")
    ap.add_argument("--box", type=parse_box, help="рамка табло в долях кадра: x,y,ширина,высота; "
                    "без неё — рамка клуба-хозяина из BOARDS или левый верх кадра")
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
    ap.add_argument("--days", type=int, help="ещё все сыгранные матчи с записью лиги за столько последних дней")
    ap.add_argument("--date", action="append", help="ещё все сыгранные матчи с записью лиги за этот день (ГГГГ-ММ-ДД)")
    ap.add_argument("--league", type=Path, help="league.json с диска вместо опубликованного на Pages")
    ap.add_argument("--live", type=Path, default=Path(os.environ.get("LIVE_DIR") or ROOT / "live"))
    ap.add_argument("--out", type=Path, default=ROOT / "probe" / "scoreboard")
    args = ap.parse_args()
    every, exact = [], []
    if args.cache:
        for path in args.cache:
            samples, meta = load_cache(path)
            args.step = meta["step"]
            r, x = probe(meta["name"], None, None, meta.get("truth") or {},
                         [tuple(g) for g in meta.get("site") or []], tuple(meta["box"]), args, samples)
            every, exact = every + r, exact + x
        if len(args.cache) > 1:
            print(f"\nВсего: {lag_summary(every)}\nТочные: {lag_summary(exact)}")
        return
    if args.stream:
        name, truth = Path(args.stream).name, parse_truth(args.truth)
        if args.check:
            check(name, args.stream, None, truth, args.box or BOX, args)
        else:
            probe(name, args.stream, None, truth, [], args.box or BOX, args)
        return
    marked = matches(args)
    keys = [args.match] if args.match else sorted(marked)
    if not keys or any(k not in marked for k in keys):
        sys.exit(f"Нет матча с записью: размеченного в {args.live / 'replays.json'} (/replay в боте) или с записью "
                 "лиги за --days/--date")
    for key in keys:
        entry = marked[key]
        try:
            src, headers, length = stream_of(entry["video"])
        except Exception as err:   # VK не отдал — дальше не ломимся (ADR-012)
            print(f"\n{key}: поток не получили — {type(err).__name__}: {err}")
            continue
        truth = {s: t for s, t in (entry.get("anchors") or {}).items() if isinstance(t, int)}
        club = key.split("|")[1]
        if not args.box and club not in BOARDS:
            out = args.out / re.sub(r"[^\w.-]+", "_", key)
            out.mkdir(parents=True, exist_ok=True)
            grid_sheet(src, headers, length, out / "grid.png")
            print(f"\n{key}: табло клуба «{club}» ещё не размечено — пропускаю. Пришли {out / 'grid.png'}: по нему "
                  "размечу рамку и клетки (BOARDS)")
            continue
        box = args.box or BOXES.get(club, BOX)
        if args.check:
            every += check(key, src, headers, truth, box, args)
        else:
            r, x = probe(key, src, headers, truth, site_goals(args.live, key), box, args)
            every, exact = every + r, exact + x
    if len(keys) > 1:
        print("\nВсего: " + lag_summary(every) + ("" if args.check else f"\nТочные: {lag_summary(exact)}"))
    if args.check:
        print("\nПришли этот вывод целиком и 2–3 картинки check_*_timeline.png и check_*_change.png (ADR-029).")
    else:
        print("\nПришли этот вывод целиком (ADR-029). Кадры уже в кэше: следующий запуск не качает записи заново.")


if __name__ == "__main__":
    main()

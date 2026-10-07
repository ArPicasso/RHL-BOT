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
рамке выше — для клубов без разметки. Цифры в клетке не читаем, а сравниваем картинки (glyph, 07.10): новая картинка —
новая цифра, уже виденная — старое табло (повтор гола, обзор голов в перерыве и после матча), подсвеченная строка —
своя картинка той же цифры. Так табло «Рязани-ВДВ», «Белгорода» и «Дизелиста» перестало давать лишние смены.

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
from itertools import combinations, takewhile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import clockrun  # noqa: E402
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
GLYPH_CONTRAST = 80     # картинка цифры: от фона клетки до цифры по яркости не меньше (пустая подсветка — меньше)
GLYPH_RAMP = (0.32, 0.92)   # пиксель — цифра на столько, сколько он прошёл пути от фона к цифре: ниже — фон, выше — цифра
SAME = 0.14             # картинки одной цифры расходятся меньше (04–06.10: своя клетка до 0,03, клетки хозяев и гостей
                        # до 0,09), разных — больше («0» и «3» у «Рязани-ВДВ» — 0,19, остальные пары — от 0,2)
GLYPH_SHIFT = (2, 1)    # пикселей сдвига по x и y при сравнении картинок одной клетки: сжатие сдвигает цифру
CROSS_SHIFT = (6, 2)    # …клеток хозяев и гостей: плашка «Рязани-ВДВ» скошена, цифра гостей на 5 пикселей левее
CROSS_DIFF = 0.22       # клетки хозяев и гостей рисуют одну цифру чуть по-разному («2» у «Рязани-ВДВ» 03.10 — 0,18), разные
                        # цифры — от 0,22 («2» и «3»): между SAME и этим — не знаем, одна ли цифра
GLYPH_SHARE = 0.5       # картинку цифры видно меньше чем в такой доле кадров с табло — цифры прозрачные, смены по пикселям
GLYPH_CLASSES = 24      # разных картинок в клетке больше — это фон за прозрачными цифрами, а не цифры: смены по пикселям
NAME_CLASSES = 60       # картинок клетки названия помним не больше: у «Калуги» и «Ростова» фон за табло — десятки картинок
EXACT = 15              # с: табло между старым и новым счётом пропадало не дольше — секунда смены точная
ORDER_TOL = 300         # с: смена годится голу, если не дальше стольких секунд от ожидаемой по сайту лиги
CLOCK_BACK = 120        # с: остановку часов перед сменой счёта ищем не раньше стольких секунд до неё
CLOCK_MOVED = 8         # пикселей клетки часов: столько сменилось — часы идут (секунды меняются каждую секунду)
MERGE_TOL = 120         # с: разброс запоздания табло между голами — для смены, под которой несколько голов (03.10)
CLUB_LAG = {club: b["lag"] for club, b in BOARDS.items() if b.get("lag") is not None}   # задержка табло клуба, с
SITE = "rhl.fhr.ru"     # запись лиги — «Смотреть» с этим источником в league.json (rhl_media.py)
WEBAPP_URL = os.environ.get("WEBAPP_URL") or "https://arpicasso.github.io/RHL-BOT/"
GRID = (0.0, 0.0, 1.0, 1.0)    # --grid: где искать табло нового клуба — весь кадр (05.10 у «Факел-Ямала» и «Красной
GRID_AT = (0.2, 0.4, 0.6, 0.8)  # Машины» в левом верху табло не было); кадры — с этих долей записи
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
    мешает: смотрим только кадры с табло (visible(кадр) → да/нет) и с одной из двух цифр (which)."""
    marks = []
    for t, f in dense:
        if visible(f) and (new := which(ch, f)) is not None:
            marks.append((t, new))
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
        if "cell" in ch:
            new_ = which(ch, f)
            return None if new_ is None else "new" if new_ else "old"
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


def goal_rank(score: str) -> int:
    """Номер гола в матче — сумма счёта: порядок голов знает счёт, а не время сайта лиги (05.10 сайт отметил 5:1
    раньше 4:1)."""
    h, a = (int(x) for x in score.split(":"))
    return h + a


def in_order(picked: dict[str, float], joint: set[str] = frozenset()) -> dict[str, float]:
    """Смены табло идут в порядке голов: (k+1)-й гол сменил счёт позже k-го. Оставляем голы, которые стоят в любом
    самом длинном ряду, где это так (наибольшая возрастающая подпоследовательность); спорные и выпавшие — админу
    (превью): клип не того гола хуже, чем никакого. «Калуга — Динамо 576» 05.10: гол 4:0 сел на смену через 22 с
    после 1:0, раньше 2:0 и 3:0; 5:1 — раньше 4:1. joint — голы под одной сменой своей команды (merged): у них одна
    секунда, и порядку это не мешает; у остальных одна смена на два гола — спор."""
    items = sorted(picked.items(), key=lambda x: (goal_rank(x[0]), x[1]))

    def before(i: int, j: int) -> bool:
        ti, tj = items[i][1], items[j][1]
        same = ti == tj and items[i][0] in joint and items[j][0] in joint
        return (ti < tj or same) and goal_rank(items[i][0]) < goal_rank(items[j][0])

    n = len(items)
    left = [1] * n    # самый длинный ряд, кончающийся на голе
    for j in range(n):
        left[j] = 1 + max((left[i] for i in range(j) if before(i, j)), default=0)
    right = [1] * n   # самый длинный ряд, начинающийся с гола
    for i in reversed(range(n)):
        right[i] = 1 + max((right[j] for j in range(i + 1, n) if before(i, j)), default=0)
    top = max(left, default=0)
    on = [k for k in range(n) if left[k] + right[k] - 1 == top]
    return {items[k][0]: items[k][1] for k in on if sum(left[m] == left[k] for m in on) == 1}


def goal_sides(goals: list[tuple[str, str, float]]) -> dict[str, str | None]:
    """Чей гол: счёт → «home»/«away» по прошлому счёту. Скачок сразу на два — не знаем (None)."""
    out, prev = {}, (0, 0)
    for score, _, _ in sorted(goals, key=lambda g: (goal_rank(g[0]), g[2])):
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


def common_frames(frames: list[bytes], rect) -> list[bytes]:
    """Кадры самой частой картинки в клетке rect (glyph, классы — как у цифр): плашка с названием стоит на месте, а
    фон за ней каждый раз другой. Плашка не сдвигается — сравниваем без сдвига, так в 5–15 раз быстрее. Картинок
    держим не больше NAME_CLASSES: для новой забываем самую старую из мелькнувших один раз (до первого табло бывает
    долгая заставка). У самой частой меньше HOLD кадров — пусто."""
    reps: list = []                  # [картинка, её «масса», кадры, номер кадра, с которого она]
    last = None
    for i, f in enumerate(frames):
        g = glyph(f, rect)
        if g is None:
            continue
        mass = sum(g[1].values())
        k = next((k for k in ([last] if last is not None else []) + list(range(len(reps)))
                  if abs(mass - reps[k][1]) < SAME * (mass + reps[k][1])      # иначе glyph_diff не меньше SAME
                  and (d := glyph_diff(g, reps[k][0], (0, 0))) is not None and d < SAME), None)
        if k is None:
            once = [(r[3], k) for k, r in enumerate(reps) if len(r[2]) == 1]
            k = len(reps) if len(reps) < NAME_CLASSES else min(once)[1] if once else None
            if k is None:
                last = None
                continue
            reps[k:k + 1] = [[g, mass, [], i]]
        reps[k][2].append(f)
        last = k
    best = max((r[2] for r in reps), key=len, default=[])
    return best if len(best) >= HOLD else []


def name_model(frames: list[bytes], rect) -> dict | None:
    """Как выглядит название хозяев на табло: частое значение каждого пикселя клетки (modes) по кадрам самой частой
    картинки названия (common_frames). По всем кадрам табло выходило, только если его видно хотя бы треть записи:
    «Протон — Кристалл» 04.10 частым вышел фон, и табло нашлось в 66 кадрах из 1251, по картинке — в 444. Картинки
    нет — по всем кадрам, как раньше. Пиксели делим на тёмные и светлые: белый лёд совпадает со светлыми, тёмная
    трибуна — с тёмными, а название целиком — только само табло. Нет контраста — клетка не та."""
    pixels = cell_pixels(rect)
    same = common_frames(frames[::max(1, len(frames) // MODE_FRAMES)], rect) or frames
    value, _ = modes([bytes(f[p] for p in pixels) for f in same])
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


def glyph(frame: bytes, rect) -> tuple[bool, dict[tuple[int, int], float]] | None:
    """Картинка цифры в клетке rect: (светлая ли цифра, {(x, y) в клетке: насколько пиксель — цифра, 0–1}). Цифра —
    меньшинство пикселей клетки: светлая на тёмном или тёмная на светлом (у «Рязани-ВДВ» строку команды временами
    подсвечивают белым, и цифра становится тёмной). Фон и цифра — 5-й и 95-й процентили яркости клетки; пиксель,
    прошедший от фона к цифре меньше GLYPH_RAMP[0], — фон, больше GLYPH_RAMP[1] — цифра, между — край сглаживания:
    у мелких цифр «Калуги» штрих в 1–2 пикселя, и жёсткий порог дрожал бы от кадра к кадру. Контраста меньше
    GLYPH_CONTRAST (табло убрали, подсветка без цифры, прозрачные цифры на льду) — картинки нет, None."""
    x0, y0, x1, y1 = rect
    vals = [frame[y * W + x] for y in range(y0, y1) for x in range(x0, x1)]
    srt = sorted(vals)
    lo, hi = srt[len(srt) // 20], srt[-len(srt) // 20 - 1]
    if hi - lo < GLYPH_CONTRAST:
        return None
    light = 2 * sum(v > (lo + hi) / 2 for v in vals) <= len(vals)
    a, b = GLYPH_RAMP
    w, out = x1 - x0, {}
    for i, v in enumerate(vals):
        d = (((v - lo) if light else (hi - v)) / (hi - lo) - a) / (b - a)
        if d > 0:
            out[(i % w, i // w)] = min(1.0, d)
    return light, out


def glyph_diff(a, b, shift: tuple[int, int] = GLYPH_SHIFT) -> float | None:
    """Насколько разные картинки цифр (glyph): 0 — одинаковые, 1 — ничего общего. Разница «цифровости» пикселей на
    сумму цифровости обеих при лучшем сдвиге b в пределах shift. Картинки нет или полярность разная (одну строку
    подсветили) — не сравниваем, None: светлая и тёмная одна цифра различаются почти как разные цифры."""
    if a is None or b is None or a[0] != b[0]:
        return None
    pa, pb = a[1], b[1]
    mass = sum(pa.values()) + sum(pb.values())
    if not mass:
        return None
    best = 1.0
    for dy in range(-shift[1], shift[1] + 1):
        for dx in range(-shift[0], shift[0] + 1):
            nb = {(x + dx, y + dy): v for (x, y), v in pb.items()}
            d = sum(abs(v - nb.get(p, 0.0)) for p, v in pa.items()) + sum(v for p, v in nb.items() if p not in pa)
            best = min(best, d / mass)
    return best


def pixel_changes(vis: list[tuple[float, bytes]], cell: list[int]) -> list[dict]:
    """Смены цифры в клетке по пикселям (так разбирали до 07.10, теперь — когда картинки цифры почти не видно,
    cell_changes): клетка сменилась (CELL_MIN, CELL_SHARE), и новое — в большинстве кадров с табло за REPLAY секунд
    после (мелькание и повтор со старым табло — нет). Откат и возврат за REPLAY — повтор (without_replays)."""
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


def cell_changes(vis: list[tuple[float, bytes]], rect) -> list[dict]:
    """Смены цифры в клетке rect по кадрам с табло (vis). Цифр не читаем — сравниваем картинки (glyph): кадры с одной
    картинкой — один класс. Цифра команды только растёт, поэтому новый класс — новая цифра, а уже виденный —
    старое табло: повтор гола, обзор голов в перерыве и после матча (у «Рязани-ВДВ» и «Белгорода» табло показывает
    счёт после каждого гола по порядку — по пикселям это были смены, и цепочка цифр рвалась). Первая цифра — с первого
    кадра. Смена — первый кадр нового класса, если табло к старым цифрам не вернулось: за REPLAY секунд после него
    старые — меньше чем в половине кадров с табло (в остальных эта цифра или следующая: табло убрали на повтор и
    вернули уже после следующего гола), и таких кадров не меньше HOLD. Прежняя цифра вернулась и стоит 2·REPLAY
    секунд подряд — смены не было (оператор записал гол не той команде, гол отменили): эта картинка снова новая.
    Обзор голов в перерыве показывает прежнюю цифру минуту-полторы. Подсвеченная
    строка (тёмная цифра на белом) — свои классы: такой класс — та цифра, между кадрами которой он стоит, без
    разногласий; иначе его кадры не считаем. Картинку цифры видно меньше чем в доле GLYPH_SHARE кадров или картинок
    больше GLYPH_CLASSES (прозрачные цифры «Факел-Ямала»: в клетке фон) — смены по пикселям.
    У смены, кроме lo/hi/before/after: cell — клетка, old/new — картинки старой и новой цифры (refine), was/now —
    их классы, final — класс цифры клетки в конце записи (verify_digits)."""
    marks = [(t, f, g) for t, f in vis if (g := glyph(f, rect)) is not None]
    if not marks or len(marks) < GLYPH_SHARE * len(vis):
        return pixel_changes(vis, cell_pixels(rect))
    reps: list = []                  # картинка-образец класса — первый его кадр
    cls: list[int] = []
    for _, _, g in marks:
        k = next((k for k in ([cls[-1]] if cls else []) + list(range(len(reps)))
                  if (d := glyph_diff(g, reps[k])) is not None and d < SAME), None)
        if k is None:
            if len(reps) >= GLYPH_CLASSES:
                return pixel_changes(vis, cell_pixels(rect))
            reps.append(g)
            k = len(reps) - 1
        cls.append(k)
    count = [cls.count(k) for k in range(len(reps))]
    if sum(count[k] >= HOLD for k in cls) < GLYPH_SHARE * len(vis):   # кадры в мелькнувших картинках — фон
        return pixel_changes(vis, cell_pixels(rect))
    light = 2 * sum(g[0] for _, _, g in marks) >= len(marks)    # обычная полярность цифр клетки
    link = {k: k for k in range(len(reps)) if reps[k][0] == light and count[k] >= HOLD}
    runs = [(i, k) for i, k in enumerate(cls) if count[k] >= HOLD]
    votes: dict[int, set[int]] = {}
    j = 0
    while j < len(runs):
        e = j
        while e + 1 < len(runs) and runs[e + 1][1] == runs[j][1]:
            e += 1
        k = runs[j][1]
        if k not in link and 0 < j and e + 1 < len(runs) and runs[j - 1][1] in link and runs[j - 1][1] == runs[e + 1][1]:
            votes.setdefault(k, set()).add(runs[j - 1][1])
        j = e + 1
    link.update({k: next(iter(v)) for k, v in votes.items() if len(v) == 1})
    line = [(i, link[k]) for i, k in enumerate(cls) if k in link]   # кадры с известной цифрой: (номер кадра, класс)

    known: list[int] = []             # цифры, которые клетка уже показывала, по порядку
    out: list[dict] = []
    cur = line[0][1] if line else None
    known += [cur] if line else []
    for p, (i, k) in enumerate(line):
        if k == cur:
            continue
        if k in known:
            run = list(takewhile(lambda x: x[1] == k, line[p:]))    # прежняя цифра подряд, без нынешней
            if (out and out[-1]["now"] == cur and out[-1]["was"] == k
                    and marks[run[-1][0]][0] - marks[i][0] >= 2 * REPLAY):
                known.remove(out.pop()["now"])
                cur = k
            continue
        nxt = [kk for _, kk in takewhile(lambda x: marks[x[0]][0] - marks[i][0] <= REPLAY, line[p + 1:])]
        if len(nxt) < HOLD or 2 * sum(kk in known for kk in nxt) >= len(nxt):
            continue
        lo = max(ii for ii, kk in line[:p] if kk == cur)
        before = max((ii for ii in range(i) if cls[ii] == cur), default=lo)
        new = next((ii for ii in range(i, len(cls)) if cls[ii] == k), i)
        out.append({"lo": marks[lo][0], "hi": marks[i][0], "before": marks[before][1], "after": marks[new][1],
                    "pixels": cell_pixels(rect), "cell": tuple(rect), "was": cur, "now": k,
                    "old": [reps[c] for c, d in link.items() if d == cur],
                    "new": [reps[c] for c, d in link.items() if d == k]})
        known.append(k)
        cur = k
    for c in out:
        c["final"] = cur
    return out


def which(ch: dict, f: bytes) -> bool | None:
    """Новая ли цифра в кадре f у смены ch: да, нет или None — не понять. Смена по картинкам (cell_changes) —
    ближайшая из картинок старой и новой цифры ближе SAME; другая картинка (подсветка, повтор с ещё более старым
    счётом) — None. По пикселям — к какому кадру, до или после смены, ближе пиксели смены."""
    if "cell" in ch:
        g = glyph(f, ch["cell"])
        best = min(((d, new) for new, refs in ((False, ch["old"]), (True, ch["new"])) for r in refs
                    if (d := glyph_diff(g, r)) is not None and d < SAME), default=None)
        return best[1] if best else None
    return len(moved(f, ch["after"], ch["pixels"])) < len(moved(f, ch["before"], ch["pixels"]))


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
                     for side in ("home", "away") for c in cell_changes(vis, board[side])]
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
        gs = [(at + shift.get(p, middle), s) for s, p, at in
              sorted((g for g in goals if side[g[0]] == team), key=lambda g: goal_rank(g[0]))]
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


def protocol_order(league: dict | None, key: str) -> list[tuple[str, str, str, int | None]]:
    """Голы протокола матча из league.json по порядку, без буллитов: (счёт, команда, период, секунда игры). Нужны,
    когда служба live не записала времени голов (03.10): тогда смены табло сопоставляем с голами по порядку."""
    day, home, away = key.split("|")
    g = next((g for g in (league or {}).get("games") or []
              if isinstance(g, dict) and (g.get("date"), g.get("home"), g.get("away")) == (day, home, away)), None)
    return [(x["score"], x.get("team"), str(x.get("period") or ""), clockrun.game_sec(x.get("time")))
            for x in (g or {}).get("goals") or []
            if isinstance(x, dict) and x.get("period") != "РБ" and isinstance(x.get("score"), str)]


def align_by_order(found: list[dict], order: list[tuple]) -> dict[str, float]:
    """Какая смена — какой гол, когда времени голов от сайта лиги нет (служба live пропустила матч, ADR-030): цифра
    команды только растёт, поэтому k-я смена цифры хозяев — k-й гол хозяев по протоколу. Смен у команды столько же,
    сколько голов, — по порядку. Меньше — табло убрали на повтор, а вернули уже после следующего гола, и одна смена
    накрыла голы подряд (merged). Лишняя смена (оператор поправил счёт, табло после матча) сдвигает весь ряд, и
    тогда не угадываем — голы этой команды остаются админу. order — [(счёт, «home»/«away», период, секунда игры)]
    по порядку протокола; периода и секунды может не быть. (счёт → секунда смены)."""
    out: dict[str, float] = {}
    for team in ("home", "away"):
        goals = [g for g in order if g[1] == team]
        cs = sorted((c for c in found if c.get("zone") == team), key=lambda c: c["hi"])
        if goals and len(cs) == len(goals):
            out.update(zip((g[0] for g in goals), (c["hi"] for c in cs)))
        elif 0 < len(cs) < len(goals):
            out.update(merged(cs, goals))
    return out


def merged(cs: list[dict], goals: list[tuple]) -> dict[str, float]:
    """Смен табло у команды меньше, чем голов: под какой сменой какие голы. «Ростов — Краснодар» 03.10: 0:3 на 46:48 и
    0:4 на 47:25, табло убрали на повтор и вернули через 130 с уже с «4» — пять смен на шесть голов, и не нашёлся ни
    один гол. Смена закрывает голы подряд, только если они одного периода и по протоколу между ними не больше, чем
    табло не было, плюс MERGE_TOL (часы стоят, а запись идёт, поэтому по записи между голами не меньше, чем по
    протоколу); от начала окна без табло у смены до конца окна у следующей — не меньше, чем голы между ними по
    протоколу, минус MERGE_TOL (раньше начала окна гол табло ещё не показало, позже конца — уже). Берём, только если
    разложить так можно одним способом, иначе — {} (голы админу). cs — смены по времени, goals — (счёт, команда,
    период, секунда игры). (счёт → секунда смены)."""
    if any(len(g) < 4 or not isinstance(g[3], (int, float)) for g in goals):
        return {}
    n, m = len(goals), len(cs)
    ways = []
    for cuts in combinations(range(1, n), m - 1):
        groups = [goals[a:b] for a, b in zip((0, *cuts), (*cuts, n))]
        ok = all(all(g[2] == grp[0][2] for g in grp)
                 and grp[-1][3] - grp[0][3] <= c["hi"] - c.get("lo", c["hi"]) + MERGE_TOL
                 for grp, c in zip(groups, cs))
        ok = ok and all(nxt["hi"] - c.get("lo", c["hi"]) >= b[0][3] - a[-1][3] - MERGE_TOL
                        for a, b, c, nxt in zip(groups, groups[1:], cs, cs[1:]))
        if ok:
            ways.append(groups)
            if len(ways) > 1:
                return {}
    return {g[0]: c["hi"] for grp, c in zip(ways[0], cs) for g in grp} if ways else {}


def cell_same(fa: bytes, fb: bytes, ca, cb=None, glyphs: bool = False) -> bool | None:
    """Одна ли цифра в клетке ca кадра fa и в клетке cb кадра fb (cb — клетка той же величины, по умолчанию та же):
    разных пикселей меньше, чем бывает у смены цифры (CELL_MIN, CELL_SHARE). Клетки разной величины — None.
    glyphs — сравниваем картинки цифр (glyph_diff меньше SAME), клетки хозяев и гостей — со сдвигом CROSS_SHIFT:
    у скошенной плашки «Рязани-ВДВ» по пикселям «0» хозяев не совпадал с «0» гостей; разные — от CROSS_DIFF, между
    ними — не знаем. Картинки нет или строку подсветили — тоже None."""
    cb = cb or ca
    if (ca[2] - ca[0], ca[3] - ca[1]) != (cb[2] - cb[0], cb[3] - cb[1]):
        return None
    if glyphs:
        cross = tuple(ca) != tuple(cb)
        d = glyph_diff(glyph(fa, ca), glyph(fb, cb), CROSS_SHIFT if cross else GLYPH_SHIFT)
        return None if d is None or cross and SAME <= d < CROSS_DIFF else d < SAME
    shift = (cb[1] - ca[1]) * W + (cb[0] - ca[0])
    pixels = cell_pixels(ca)
    diff = sum(abs(fa[p] - fb[p + shift]) > DIFF for p in pixels)
    return diff < max(CELL_MIN, CELL_SHARE * len(pixels))


def verify_digits(picked: dict[str, float], found: list[dict], board: dict, samples: list[tuple[float, bytes]],
                  visible, totals: tuple[int, int]) -> tuple[dict[str, float], dict[str, str]]:
    """Цифры табло против того, какой это гол. 05.10 «Калуга — Динамо 576»: повтор 1:0 открывался, когда на табло уже
    3:0; «Факел Ямал — Ахмат-Гранит»: превью 1:3, а на табло 0:2 — смены сопоставились голам по порядку, а табло то
    не видело ранних смен, то принимало за смену фон за прозрачными цифрами. Цифр не читаем — сравниваем картинки
    клетки; голы — по порядку матча. k-й гол команды берём, только если в клетке до смены «k−1», а после — «k»:
    - до первого гола — «0»: та же картинка, что у соперника до его первого гола (клетки одной величины) или у
      соперника без голов в том же кадре. Не сошлось — запись началась после гола или табло пропустило смену: не
      берём голы той команды, что забила первой (соперник до своего гола точно показывал «0»);
    - до k-го гола — то, что устоялось после (k−1)-го;
    - после: не вернулась прежняя цифра до смены (k+1)-го гола, у последнего гола — то, что в клетке в конце записи;
      и если соперник уже доходил до k — та же картинка, что у него тогда.
    Клетки разной величины («Ростов») сравниваем только с самими собой. Разорвалась цепочка — дальше голы этой
    команды не берём: секунда и повтор по чужой смене хуже, чем никакого. Смены по картинкам цифр (cell_changes) — в
    своей клетке цифры сравниваем по классам картинок, с соперником — по glyph_diff (первый кадр новой картинки уже
    устоялся), «в конце записи» — цифра клетки, на которой запись кончилась (final): обзор голов после матча её не
    меняет. Сравнить нельзя (строку подсветили, картинки нет, клетки рисуют цифру по-разному) — цепочку не рвём.
    (оставленные голы — счёт → смена, отброшенные — счёт → почему)."""
    zone_of = {c["hi"]: c["zone"] for c in found if c.get("zone") in ("home", "away")}
    vis = [(t, f) for t, f in samples if visible(f)]
    seq: dict[str, dict[int, dict]] = {"home": {}, "away": {}}
    for score, hi in picked.items():
        side = zone_of.get(hi)
        if side:
            c = next(c for c in found if c["hi"] == hi and c.get("zone") == side)
            seq[side][int(score.split(":")[0 if side == "home" else 1])] = {**c, "score": score}
    side_changes = {side: sorted(c["hi"] for c in found if c.get("zone") == side) for side in ("home", "away")}
    glyphs = {side: any("cell" in c for c in found if c.get("zone") == side) for side in ("home", "away")}

    def settled(side: str, c: dict) -> bytes:
        """Кадр с новой цифрой, когда она устоялась (смена бывает с анимацией), но раньше следующей смены клетки."""
        if "cell" in c:
            return c["after"]
        nxt = next((t for t in side_changes[side] if t > c["hi"]), float("inf"))
        got = [f for t, f in vis if c["hi"] + 5 <= t < nxt - 5]
        return got[min(len(got) - 1, 2)] if got else c["after"]

    def same_digit(a: dict, b: dict, side: str) -> bool | None:
        """Одна ли цифра в клетке side после смены a и до смены b. По картинкам — классы смен (cell_changes) точно, а
        два кадра одного класса могут разойтись и больше SAME; по пикселям — кадр, когда цифра устоялась, и кадр до b."""
        if "now" in a and "was" in b:
            return a["now"] == b["was"]
        return cell_same(settled(side, a), b["before"], board[side])

    other = {"home": "away", "away": "home"}
    total = {"home": totals[0], "away": totals[1]}
    first = {side: seq[side].get(1) for side in seq}
    zero_bad: set[str] = set()
    if first["home"] and first["away"]:
        same = cell_same(first["home"]["before"], first["away"]["before"], board["home"], board["away"],
                         glyphs["home"] and glyphs["away"])
        if same is False:   # кто забил первым по ходу матча, у того и сбилось
            zero_bad.add(min(("home", "away"), key=lambda x: goal_rank(first[x]["score"])))
        elif same is None and vis:
            zero_bad |= {x for x in ("home", "away")
                         if cell_same(first[x]["before"], vis[0][1], board[x], glyphs=glyphs[x]) is False}

    def zero_opp(side: str, c1: dict) -> bool | None:
        """Соперник без голов весь матч показывает «0»: та же ли картинка в клетке side перед её первым голом. По
        пикселям — клетка соперника в том же кадре; по картинкам — в последних кадрах с табло до смены, за большинством:
        у «Калуги» 04.10 в клетке гостей перед голом хозяев мелькало другое."""
        if not glyphs[side]:
            return cell_same(c1["before"], c1["before"], board[side], board[other[side]])
        got = [cell_same(c1["before"], f, board[side], board[other[side]], True)
               for _, f in [x for x in vis if x[0] < c1["hi"]][-30:]]
        votes = [v for v in got if v is not None]
        if len(votes) < HOLD or 2 * len(votes) < len(got) or 2 * sum(votes) == len(votes):
            return None      # решили меньше половины кадров или поровну — не знаем, цепочку не рвём
        return 2 * sum(votes) > len(votes)

    for side in ("home", "away"):
        c1 = first[side]
        if c1 and not first[other[side]]:
            same = zero_opp(side, c1) if total[other[side]] == 0 else None
            if same is None and vis:
                same = cell_same(c1["before"], vis[0][1], board[side], glyphs=glyphs[side])
            if same is False:
                zero_bad.add(side)
    last = vis[-1][1] if vis else None
    kept, bad, broken = {}, {}, {"home": "", "away": ""}
    goals = sorted(((side, k) for side in seq for k in seq[side]), key=lambda x: goal_rank(seq[x[0]][x[1]]["score"]))
    for side, k in goals:
        c, mine, opp = seq[side][k], board[side], other[side]
        if not broken[side] and any(j not in seq[side] for j in range(1, k)):
            broken[side] = f"смену {min(j for j in range(1, k) if j not in seq[side])}-го гола табло не увидело"
        joint_prev = (seq[side].get(k - 1) or {}).get("hi") == c["hi"]   # одна смена с прошлым голом (merged)…
        joint_next = (seq[side].get(k + 1) or {}).get("hi") == c["hi"]   # …или со следующим: после неё уже не «k»
        if not broken[side]:
            if k == 1 and side in zero_bad:
                broken[side] = "до первого гола в клетке не «0»"
            elif k > 1 and not joint_prev and same_digit(seq[side][k - 1], c, side) is False:
                broken[side] = f"между {k - 1}-м и {k}-м голом цифра сменилась ещё раз"
            elif joint_next:
                pass         # после смены уже следующий счёт: «после» проверит последний гол этой смены
            else:
                post, nxt = None, seq[side].get(k + 1)
                if nxt and same_digit(c, nxt, side) is False:
                    # до следующей смены снова прежняя цифра — эта смена не держалась (фон, повтор со старым табло);
                    # иначе между ними пропущена смена, и цепочку порвёт следующий гол
                    back = c["was"] == nxt["was"] if "was" in c and "was" in nxt else cell_same(c["before"], nxt["before"], mine)
                    post = False if back else None
                elif not nxt and k == total[side] and "final" in c:
                    post = c["final"] == c["now"]     # последняя цифра клетки в записи — эта
                elif not nxt and k == total[side] and last is not None:
                    post = cell_same(settled(side, c), last, mine)
                twin = seq[opp].get(k)
                if twin and (seq[opp].get(k + 1) or {}).get("hi") == twin["hi"]:
                    twin = None   # после той смены у соперника уже не «k» (merged)
                earlier = twin and twin["score"] in kept and goal_rank(twin["score"]) < goal_rank(c["score"])
                if post is not False and earlier:
                    post = cell_same(settled(side, c), settled(opp, twin), mine, board[opp], glyphs[side] and glyphs[opp])
                if post is False:
                    broken[side] = f"после смены в клетке не та цифра, что должна быть после {k}-го гола"
        if broken[side]:
            bad[c["score"]] = broken[side]
        else:
            kept[c["score"]] = c["hi"]
    return kept, bad


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
    брать: для табло хватает 480p, для клипов (probe_cuts.py) — 720p. Эфир ещё идёт (`is_live`) — длительности нет,
    даже если VK её назвал: записи целиком ещё нет (служба clips такую запись не разбирает, а ждёт)."""
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
    return fmt["url"], headers, None if info.get("is_live") else info.get("duration")


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
    groups: dict[tuple, list[str]] = {}   # голы под одной сменой своей команды (merged) — только по порядку протокола
    if cells and not site and order:
        team = {g[0]: g[1] for g in order}
        for score, t in sorted(picked.items(), key=lambda x: goal_rank(x[0])):
            groups.setdefault((team.get(score), t), []).append(score)
    shared = {s: ss for ss in groups.values() if len(ss) > 1 for s in ss}   # счёт → все голы его смены по порядку
    picked = in_order(picked, set(shared))
    rejected: dict[str, str] = {}
    if board and cells and samples:
        scores = [g[0] for g in site] if site else [g[0] for g in order or []]
        totals = (max((int(x.split(":")[0]) for x in scores), default=0),
                  max((int(x.split(":")[1]) for x in scores), default=0))
        picked, rejected = verify_digits(picked, main_, board, samples, visible, totals)
    for score, ss in shared.items():   # из голов одной смены выпал хоть один — не знаем, чья она, не берём никого
        if score in picked and any(x not in picked for x in ss):
            del picked[score]
            rejected.setdefault(score, "под той же сменой табло гол " + ", ".join(x for x in ss if x not in picked
                                                                                 and x != score) + " не взят")
    if rejected:
        for score, why in sorted(rejected.items(), key=lambda x: goal_rank(x[0])):
            print(f"  гол {score} не беру: {why}")
    by_t: dict[float, list[str]] = {}
    for s, t in sorted(picked.items(), key=lambda x: goal_rank(x[0])):
        by_t.setdefault(t, []).append(s)
    by = "по сайту лиги" if site or not order else "по протоколу"
    print(f"  смен табло: {len(main_)}, голов у админа: {len(truth)}, у сайта лиги: {len(site)}; "
          f"{by} нашлось {len(picked)} из {len(site) or len(order or [])}")
    for k, c in enumerate(main_, 1):
        where = {"home": "хозяева", "away": "гости"}.get(c["zone"], f"зона {c['zone']}")
        gap = round(c["hi"] - c["lo"])
        goal = ", ".join(by_t.get(c["hi"]) or [])
        print(f"    {k:2d}. {replay.fmt_t(int(c['hi']))}, {where}, " + ("точно" if gap <= EXACT else
              f"табло не было {gap} с") + " · " + (f"{by} — гол {goal}" if goal else "без гола"))
    stops: dict[str, tuple] = {}
    if board and board.get("clock"):
        clock = cell_pixels(board["clock"])
        for score, e in picked.items():
            if score in shared:   # часы вставали на каждом из голов — какая остановка чья, не знаем
                continue
            if src:   # кадр каждую секунду за CLOCK_BACK до смены счёта
                dense = [(t, f) for t, f in safe_scan(src, headers, box, e - CLOCK_BACK, e + 5) if visible(f)]
                got = clock_stop(dense, e, clock, 2)
            else:
                got = clock_stop([(t, f) for t, f in samples if visible(f)], e, clock, args.step)
            if got and got[0] > max((c for s, c in picked.items() if goal_rank(s) < goal_rank(score)), default=-1):
                stops[score] = got   # остановка до смены прошлого гола — его, не этого (05.10 у 1:0 и 4:0 одна)
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
        t, how = goal_time(e, e in exact_t and score not in shared, stops.get(score), lag)
        # под слитой сменой счёт сменился на последнем голе: у ранних смена может быть и через минуты после гола —
        # для превью, счёта хода и проверки отметок (CHANGE_BEFORE) это не их смена; превью последнего их накроет
        early = score in shared and score != shared[score][-1]
        goals.append({"score": score, "change": None if early else e, "exact": e in exact_t and score not in shared,
                      "clock": list(stops[score]) if score in stops else None, "t": t, "src": how})
    timed = [g for g in goals if g["t"] is not None]
    (out / "goals.json").write_text(json.dumps({"key": name, "goals": goals, "rejected": rejected},
                                               ensure_ascii=False, indent=1), encoding="utf-8")
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
    if days:
        args.league_data = league_json(args.league)   # ещё нужен порядку голов протокола (main, order_of)
    found = recorded(args.league_data, days) if days else {}
    return {**found, **marked}


def grid_sheet(src: str, headers: dict | None, length: float | None, path: Path) -> None:
    """Табло нового клуба ещё не размечено: кадры записи на долях GRID_AT — часть кадра GRID с сеткой: тонкие
    линии — каждые 0,02 кадра, жёлтые — каждые 0,1. По ней размечаем рамку и клетки (BOARDS)."""
    x, y, w, h = GRID
    vf = (f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale=960:-2,"
          f"drawgrid=w=iw*{0.02 / w}:h=ih*{0.02 / h}:t=1:c=white@0.35,"
          f"drawgrid=w=iw*{0.1 / w}:h=ih*{0.1 / h}:t=2:c=yellow@0.8")
    parts = []
    for k, share in enumerate(GRID_AT):
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
    league: list = []   # league.json — один раз и только если понадобится порядок голов протокола

    def order_of(key: str, site: list) -> list:
        if site or key.count("|") != 2:
            return []
        if not league:
            league.append(getattr(args, "league_data", None) or league_json(args.league))
        return protocol_order(league[0], key)

    if args.cache:
        for path in args.cache:
            samples, meta = load_cache(path)
            args.step = meta["step"]
            site = [tuple(g) for g in meta.get("site") or []]   # как было у прохода, что сохранил кадры
            args.order = order_of(meta["name"], site)
            r, x = probe(meta["name"], None, None, meta.get("truth") or {}, site, tuple(meta["box"]), args, samples)
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
            site = site_goals(args.live, key)
            args.order = order_of(key, site)
            r, x = probe(key, src, headers, truth, site, box, args)
            every, exact = every + r, exact + x
    if len(keys) > 1:
        print("\nВсего: " + lag_summary(every) + ("" if args.check else f"\nТочные: {lag_summary(exact)}"))
    if args.check:
        print("\nПришли этот вывод целиком и 2–3 картинки check_*_timeline.png и check_*_change.png (ADR-029).")
    else:
        print("\nПришли этот вывод целиком (ADR-029). Кадры уже в кэше: следующий запуск не качает записи заново.")


if __name__ == "__main__":
    main()

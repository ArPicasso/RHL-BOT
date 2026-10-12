"""Расклад дня, решатель и проверка пути (разделы 1, 3 контракта).

Расклад выводится из даты и соли сезона: `generate` на одну дату всегда даёт побитово один и тот
же расклад, иначе сборка на раннере и сервер разойдутся в том, какое сегодня поле.

Как строится расклад:

1. случайный гамильтонов путь по всему полю — он и будет единственным решением;
2. номера по порядку вдоль пути: первый — в начале, последний — в конце;
3. борта только между клетками, которые в пути не идут друг за другом, — путь остаётся решением
   при любом их числе;
4. пока `solve` находит второе решение, берём его ребро вне пути и ставим туда борт. Так сходится
   всегда: в пределе бортами закрыто всё, кроме самого пути, а в пути ровно одно решение. Если
   бортов набралось слишком много (поле становится лабиринтом), вместо борта добавляем номер;
5. сложность — это уже лишние борта сверх необходимых: коридоры ведут игрока за руку, открытый
   лёд — нет (`rules.EXTRA_WALLS`).

Решения в опубликованный файл не попадает (раздел 2): расклад выводится из даты, так что решение
всегда можно получить этим же движком.
"""
import hashlib
import random
from dataclasses import dataclass

from . import rules
from .plan import Plan, as_date, day_plan, lede

Path = list[int]


@dataclass(frozen=True)
class Puzzle:
    """Расклад одного дня. `dots` — клетки номеров 1..k по порядку, `walls` — борта строками
    `"<меньший>-<больший>"`."""
    date: str
    n: int
    w: int
    h: int
    dots: tuple[int, ...]
    walls: tuple[str, ...]
    hard: int
    par: int

    @property
    def k(self) -> int:
        return len(self.dots)

    @property
    def cells(self) -> int:
        return self.w * self.h


@dataclass(frozen=True)
class Verdict:
    """Ответ `check`: прошёл путь или нет и почему — причину показывает сервер болельщику."""
    ok: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ok


OK = Verdict(True)


# ---------- поле ----------

def wall_key(a: int, b: int) -> str:
    return f"{min(a, b)}-{max(a, b)}"


def edges(w: int, h: int) -> list[tuple[int, int]]:
    """Все рёбра сетки: вправо и вниз, по возрастанию меньшей клетки."""
    out = []
    for y in range(h):
        for x in range(w):
            i = y * w + x
            if x + 1 < w:
                out.append((i, i + 1))
            if y + 1 < h:
                out.append((i, i + w))
    return out


def wall_pairs(walls) -> set[tuple[int, int]]:
    """Борта из строк файла дня в пары клеток."""
    out = set()
    for s in walls:
        a, b = (int(x) for x in s.split("-"))
        out.add((min(a, b), max(a, b)))
    return out


def _keys(pairs) -> tuple[str, ...]:
    """Борта в строки файла дня. Порядок — числовой по клеткам: так же, как в примере контракта
    (`["3-4", "10-16"]`), а не по алфавиту, где «10-16» встало бы раньше «3-4»."""
    return tuple(wall_key(a, b) for a, b in sorted(pairs))


def _masks(w: int, h: int, walls: set[tuple[int, int]]) -> list[int]:
    """Соседи каждой клетки битовой маской, борта уже убраны."""
    nb = [0] * (w * h)
    for a, b in edges(w, h):
        if (a, b) in walls:
            continue
        nb[a] |= 1 << b
        nb[b] |= 1 << a
    return nb


def _neighbour_lists(w: int, h: int) -> list[list[int]]:
    nb = [[] for _ in range(w * h)]
    for a, b in edges(w, h):
        nb[a].append(b)
        nb[b].append(a)
    return nb


# ---------- решатель ----------

def solve(puzzle: Puzzle, limit: int = 2) -> list[Path]:
    """Решения расклада, не больше `limit`. Решение — путь по всем клеткам, номера в нём по
    возрастанию; начинать можно с любой клетки (раздел 1), поэтому перебираются все начала.

    Без отсечений перебор на 7×7 не кончается, поэтому их три:
    - у незанятой клетки не осталось свободных соседей — тупик; таких с одним соседом может быть
      не больше одной: она может быть только концом пути, а конец один;
    - незанятые клетки должны оставаться связными с текущей, иначе часть льда уже не пройти;
    - до следующего номера должна быть дорога, не наступающая на номера после него.
    """
    w, h, n = puzzle.w, puzzle.h, puzzle.cells
    nb = _masks(w, h, wall_pairs(puzzle.walls))
    dots = puzzle.dots
    k = len(dots)
    dot_no = {c: i for i, c in enumerate(dots)}
    full = (1 << n) - 1
    # later[j] — маска номеров j..k-1: на них нельзя наступать, пока не пройден номер j
    later = [0] * (k + 1)
    for j in range(k - 1, -1, -1):
        later[j] = later[j + 1] | (1 << dots[j])
    found: list[Path] = []
    path = [0] * n

    def reach(start: int, allowed: int) -> int:
        """Клетки `allowed`, до которых можно дойти из `start` (сам `start` входит)."""
        seen = 1 << start
        frontier = seen
        while frontier:
            nxt = 0
            f = frontier
            while f:
                b = f & -f
                f ^= b
                nxt |= nb[b.bit_length() - 1]
            frontier = nxt & allowed & ~seen
            seen |= frontier
        return seen

    def step(cell: int, visited: int, need: int, depth: int) -> None:
        if cell in dot_no:
            if dot_no[cell] != need:
                return
            need += 1
        visited |= 1 << cell
        path[depth] = cell
        if visited == full:
            if need == k:
                found.append(path[:])
            return
        free = full & ~visited
        avail = free | (1 << cell)
        ends = 0
        f = free
        while f:
            b = f & -f
            f ^= b
            d = (nb[b.bit_length() - 1] & avail).bit_count()
            if d == 0:
                return
            if d == 1:
                ends += 1
                if ends > 1:
                    return
        if reach(cell, avail) & avail != avail:
            return
        if need < k:
            road = reach(cell, avail & ~later[need + 1])
            if not road >> dots[need] & 1:
                return
        moves = []
        m = nb[cell] & free
        while m:
            b = m & -m
            m ^= b
            i = b.bit_length() - 1
            moves.append(((nb[i] & free).bit_count(), i))
        moves.sort()                      # сначала самые тесные клетки: тупик виден сразу
        for _, i in moves:
            step(i, visited, need, depth + 1)
            if len(found) >= limit:
                return

    if n > 1 and any(m == 0 for m in nb):
        return []
    dead = [i for i in range(n) if nb[i].bit_count() == 1]
    if len(dead) > 2:
        return []                         # больше двух клеток с одним соседом: концов пути только два
    starts = dead if len(dead) == 2 else range(n)
    for s in starts:
        step(s, 0, 0, 0)
        if len(found) >= limit:
            break
    return found


# ---------- проверка пути ----------

def check(puzzle: Puzzle, path) -> Verdict:
    """Прошёл ли путь весь лёд по правилам (раздел 1). Вводу не доверяем: путь приходит с клиента."""
    n = puzzle.cells
    try:
        cells = [c if isinstance(c, int) and not isinstance(c, bool) else None for c in path]
    except TypeError:
        return Verdict(False, "Путь должен быть списком клеток")
    for c in cells:
        if c is None:
            return Verdict(False, "В пути есть что-то, кроме номеров клеток")
        if not 0 <= c < n:
            return Verdict(False, f"Клетка {c} не с этого поля")
    if len(cells) < n:
        return Verdict(False, f"Пройдено {len(cells)} клеток из {n}: лёд остался непройденным")
    if len(cells) > n:
        return Verdict(False, f"В пути {len(cells)} клеток, а на поле {n}")
    seen = set()
    for c in cells:
        if c in seen:
            return Verdict(False, f"Клетка {c} пройдена дважды")
        seen.add(c)
    walls = wall_pairs(puzzle.walls)
    w = puzzle.w
    for a, b in zip(cells, cells[1:]):
        dx = abs(a % w - b % w)
        dy = abs(a // w - b // w)
        if dx + dy != 1:
            return Verdict(False, f"Из клетки {a} в клетку {b} шайба не доедет: клетки не соседние")
        if (min(a, b), max(a, b)) in walls:
            return Verdict(False, f"Между клетками {a} и {b} борт")
    dot_no = {c: i for i, c in enumerate(puzzle.dots)}
    need = 0
    for c in cells:
        if c in dot_no:
            if dot_no[c] != need:
                return Verdict(False, f"Номер {dot_no[c] + 1} пройден раньше номера {need + 1}")
            need += 1
    return OK


# ---------- генерация ----------

def seed_of(day: str) -> int:
    """Семя дня из соли сезона (`rules.salt()`, её перебивает `RASKAT_SALT`) и даты.

    Берём sha256, а не `hash()`: он в каждом запуске Python другой, и расклады разъехались бы."""
    return int.from_bytes(hashlib.sha256(f"{rules.salt()}:{day}".encode()).digest()[:8], "big")


def _snake(w: int, h: int) -> Path:
    out = []
    for y in range(h):
        xs = range(w) if y % 2 == 0 else range(w - 1, -1, -1)
        out += [y * w + x for x in xs]
    return out


def _random_path(w: int, h: int, rnd: random.Random) -> Path:
    """Случайный гамильтонов путь: змейка плюс «подвороты» (backbite).

    Подворот: из конца пути шагаем к случайному соседу, а хвост за ним разворачиваем. Путь остаётся
    путём по всем клеткам, но за несколько сотен шагов от змейки не остаётся и следа. Перебор с
    возвратом так не умеет: он липнет к бортику поля и выдаёт похожие друг на друга пути."""
    path = _snake(w, h)
    nb = _neighbour_lists(w, h)
    for _ in range(w * h * rules.SHUFFLE_PER_CELL):
        if rnd.random() < 0.5:
            path.reverse()
        i = path.index(rnd.choice(nb[path[-1]]))
        if i == len(path) - 2:
            continue
        path[i + 1:] = path[:i:-1]
    return path


def _place_dots(path: Path, k: int, rnd: random.Random) -> list[int]:
    """Номера вдоль пути: первый — в начале, последний — в конце, остальные по одному из равных
    отрезков. Начало и конец отмечены номерами не ради правил, а ради игры: видно, откуда вести
    шайбу и где ворота. Равные отрезки — чтобы номера не слипались в одном углу."""
    n = len(path)
    if k <= 2:
        return [path[0], path[-1]][:k]
    picks = [0]
    inner = k - 2
    for j in range(inner):
        lo = 1 + (n - 2) * j // inner
        hi = max(lo, (n - 2) * (j + 1) // inner)
        picks.append(rnd.randint(lo, hi))
    picks.append(n - 1)
    return [path[i] for i in picks]


def _add_dot(path: Path, dots: list[int], rnd: random.Random) -> list[int] | None:
    """Ещё один номер на свободное место пути — когда бортов уже столько, что поле стало
    лабиринтом. Номер сужает перебор не хуже борта, а поле не загромождает."""
    order = {c: i for i, c in enumerate(path)}
    taken = {order[c] for c in dots}
    spare = [i for i in range(1, len(path) - 1) if i not in taken]
    if not spare:
        return None
    return sorted(dots + [path[rnd.choice(spare)]], key=lambda c: order[c])


def _make(plan: Plan, dots, walls) -> Puzzle:
    return Puzzle(plan.date, plan.n, plan.w, plan.h, tuple(dots), _keys(walls), plan.hard, plan.par)


def _one_path_puzzle(plan: Plan, rnd: random.Random) -> Puzzle | None:
    """Расклад из одного случайного пути или None, если на этом пути не сошлось."""
    path = _random_path(plan.w, plan.h, rnd)
    dots = _place_dots(path, plan.k, rnd)
    used = {(min(a, b), max(a, b)) for a, b in zip(path, path[1:])}
    spare = [e for e in edges(plan.w, plan.h) if e not in used]
    rnd.shuffle(spare)
    cap = len(spare) * rules.WALL_CAP
    # Первая и последняя клетки пути — в «карманах»: все их рёбра, кроме пути, закрыты бортом.
    # Номера 1 и k и так стоят на концах, так что игроку это ничего не выдаёт, зато выход из
    # кармана один — и решателю (а значит, и сборке) остаётся два начала вместо всех клеток поля
    walls: set[tuple[int, int]] = {e for e in spare if path[0] in e or path[-1] in e}
    spare = [e for e in spare if e not in walls]
    # Горсть бортов наугад — фора решателю. На открытом поле 7×7 он дольше всего ищет не
    # единственность, а второе решение, и один этот шаг стоит дороже всей остальной сборки дня.
    # В готовом раскладе эти борта всё равно почти все и так появились бы
    head = round(len(spare) * rules.HEAD_WALLS)
    walls |= set(spare[:head])
    spare = spare[head:]
    for _ in range(rules.MAX_WALL_STEPS):
        found = solve(_make(plan, dots, walls), limit=2)
        if len(found) == 1:
            break
        if not found:
            return None                   # так быть не должно: путь — всегда решение
        alt = next((s for s in found if s != path), found[0])
        extra = [(min(a, b), max(a, b)) for a, b in zip(alt, alt[1:])]
        extra = [e for e in extra if e not in used and e not in walls]
        if not extra or len(walls) >= cap:
            if len(dots) - plan.k >= rules.MAX_EXTRA_DOTS:
                return None
            dots = _add_dot(path, dots, rnd)
            if dots is None:
                return None
            continue
        walls.add(extra[0])
        spare.remove(extra[0])
    else:
        return None
    # Лишние борта для сложности. Пересчитывать решения не нужно: борт вне пути решения только
    # убирает, а путь решением остаётся, значит оно по-прежнему одно
    more = round(len(spare) * rules.EXTRA_WALLS[plan.hard])
    return _make(plan, dots, walls | set(spare[:more]))


def generate(day) -> Puzzle:
    """Расклад дня: детерминированно по дате и соли сезона, с единственным решением."""
    plan = day_plan(as_date(day))
    rnd = random.Random(seed_of(plan.date))
    for _ in range(rules.MAX_PATHS):
        puzzle = _one_path_puzzle(plan, rnd)
        if puzzle is not None:
            return puzzle
    raise RuntimeError(f"не вышло собрать расклад на {plan.date}")


# ---------- опубликованные файлы (раздел 2) ----------

def as_json(puzzle: Puzzle) -> dict:
    """Файл дня `<дата>.json`. Решения здесь нет и быть не должно."""
    return {"date": puzzle.date, "n": puzzle.n, "w": puzzle.w, "h": puzzle.h,
            "dots": list(puzzle.dots), "walls": list(puzzle.walls),
            "hard": puzzle.hard, "par": puzzle.par, "lede": lede(puzzle.k, len(puzzle.walls))}


def index_row(puzzle: Puzzle) -> dict:
    """Строка дня в `index.json`. `k` — сколько номеров в раскладе на самом деле."""
    return {"date": puzzle.date, "n": puzzle.n, "w": puzzle.w, "h": puzzle.h,
            "k": puzzle.k, "hard": puzzle.hard, "par": puzzle.par}

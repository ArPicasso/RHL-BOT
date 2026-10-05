"""tools/probe_scoreboard.py (ADR-029): голы по табло трансляции на синтетических кадрах, без ffmpeg и сети."""
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import probe_scoreboard as sb  # noqa: E402

W, H = sb.W, sb.H
BOX = [(x, y) for y in range(5, 35) for x in range(5, 160)]          # плашка табло
HOME = [(x, y) for y in range(10, 30) for x in range(100, 112)]      # цифра хозяев
AWAY = [(x, y) for y in range(10, 30) for x in range(125, 137)]      # цифра гостей
CLOCK = [(x, y) for y in range(10, 30) for x in range(60, 85)]       # секунды часов — каждый кадр другие


def digit(cells, n):
    """Цифра n — свой узор полос: у разных цифр разные пиксели светлые."""
    return {(x, y): 230 if ((x - cells[0][0]) // 3 + n) % 3 == 0 else 40 for x, y in cells}


def frame(t, goals, hidden=(), rnd=None, stale=()):
    """Кадр на секунде t: игра — шум, табло слева сверху со счётом на момент t. goals — [(секунда смены, сторона)].
    hidden — промежутки, когда плашку убрали (повтор). stale — повтор гола вместе со старым табло: счёт как за 20 с
    до начала промежутка (так у «Рязани-ВДВ»)."""
    rnd = rnd or random.Random(int(t * 1000))
    px = bytearray(rnd.randrange(256) for _ in range(W * H))
    if any(a <= t < b for a, b in hidden):
        return bytes(px)
    now = next((a - 20 for a, b in stale if a <= t < b), t)
    home = sum(1 for s, side in goals if s <= now and side == "home")
    away = sum(1 for s, side in goals if s <= now and side == "away")
    for x, y in BOX:
        px[y * W + x] = 30
    for (x, y), v in {**digit(HOME, home), **digit(AWAY, away)}.items():
        px[y * W + x] = v
    for x, y in CLOCK:
        px[y * W + x] = rnd.randrange(256)
    return bytes(px)


GOALS = [(206, "home"), (476, "away"), (906, "home")]
HIDDEN = [(212, 227), (482, 497), (912, 927)]                     # повтор после гола — плашки нет


def samples(step=10, until=1200, goals=GOALS, hidden=HIDDEN, stale=(), start=0):
    return [(t, frame(t, goals, hidden, stale=stale)) for t in range(start, until, step)]


class Board(unittest.TestCase):
    def setUp(self):
        self.s = samples()
        frames = [f for _, f in self.s]
        self.mask = sb.stable_mask(frames)
        self.med = sb.usual(frames, self.mask)

    def test_mask_is_the_plate(self):
        inside = {y * W + x for x, y in BOX} - {y * W + x for x, y in CLOCK}
        self.assertGreater(len(set(self.mask) & inside), 0.9 * len(inside))
        self.assertLess(len(set(self.mask) - inside), 50)            # шум игры — не графика
        self.assertFalse(set(self.mask) & {y * W + x for x, y in CLOCK})

    def test_hidden_plate_is_not_shown(self):
        self.assertTrue(sb.shown(frame(100, GOALS, HIDDEN), self.mask, self.med))
        self.assertFalse(sb.shown(frame(215, GOALS, HIDDEN), self.mask, self.med))

    def test_changes_are_goals(self):
        found = sb.changes(self.s, self.mask, self.med)
        self.assertEqual([(c["lo"], c["hi"]) for c in found], [(200, 210), (470, 480), (900, 910)])
        self.assertEqual(len({c["zone"] for c in found}), 2)          # цифра хозяев и цифра гостей

    def test_flicker_is_not_a_change(self):
        # цифра на один кадр сменилась и вернулась — не гол
        s = list(self.s)
        k = next(i for i, (t, _) in enumerate(s) if t == 600)
        s[k] = (600, frame(600, GOALS + [(595, "away")], HIDDEN))
        found = sb.changes(s, self.mask, self.med)
        self.assertEqual(len(found), 3)

    def test_refine_to_a_second(self):
        found = sb.changes(self.s, self.mask, self.med)
        visible = lambda f: sb.shown(f, self.mask, self.med)        # noqa: E731
        got = [sb.refine(c, lambda t: frame(t, GOALS, HIDDEN), visible, 10) for c in found]
        self.assertEqual([round(c["hi"]) for c in got], [206, 476, 906])
        self.assertTrue(all(c["hi"] - c["lo"] <= 1 for c in got))

    def test_against_admin(self):
        rows = sb.against([206, 476, 906], {"1:0": 200, "1:1": 470, "2:1": 900, "3:1": 1100})
        self.assertEqual([round(s - t) if s is not None else None for _, t, s in rows], [6, 6, 6, None])
        self.assertIn("3 из 4", sb.lag_summary(rows))


class RealBroadcasts(unittest.TestCase):
    """Что показала проверка 05.10 на записях 04.10 (ADR-029): плашку убирают надолго, повтор бывает со старым табло."""

    def test_plate_hidden_half_the_time(self):
        # крупные планы, повторы, заставка «GOAL»: плашки нет 45 с из каждых 100 — в соседних кадрах графика
        # совпадает реже, чем STABLE, но plate её находит
        hidden = [(a, a + 45) for a in range(30, 1200, 100)]
        s = samples(hidden=hidden)
        frames = [f for _, f in s]
        inside = {y * W + x for x, y in BOX} - {y * W + x for x, y in CLOCK}
        self.assertLess(len(set(sb.stable_mask(frames)) & inside), 0.5 * len(inside))
        mask, med, _ = sb.plate(frames)
        self.assertGreater(len(set(mask) & inside), 0.9 * len(inside))
        self.assertLess(len(set(mask) - inside), 50)
        self.assertTrue(sb.shown(frame(5, GOALS, hidden), mask, med))
        self.assertFalse(sb.shown(frame(40, GOALS, hidden), mask, med))

    def test_replay_with_old_board_is_one_change(self):
        # через 14 с после гола 30 с повтора с прежним счётом, потом снова новый: одна смена, первая
        stale = [(220, 250), (490, 520), (920, 950)]
        s = samples(hidden=[], stale=stale)
        mask, med, _ = sb.plate([f for _, f in s])
        found = sb.changes(s, mask, med)
        self.assertEqual([(c["lo"], c["hi"]) for c in found], [(200, 210), (470, 480), (900, 910)])

    def test_check_window_every_second(self):
        # --check: окно от 30 с до гола до 2 минут после, кадр каждую секунду; табло убрали на повтор, потом
        # повтор со старым табло — смена табло ровно в секунду гола
        hidden = [(482, 497)]
        stale = [(497, 512)]
        s = samples(step=1, start=476 - 30, until=476 + 120, hidden=hidden, stale=stale)
        found = sb.board_changes(s)
        first = sb.first_after(found, 470)
        self.assertEqual(first["hi"], 476)
        self.assertEqual(len([c for c in found if not c["often"]]), 1)

    def test_no_plate_in_window(self):
        noise = [(t, frame(t, [], [(0, 10 ** 6)])) for t in range(100)]
        self.assertIsNone(sb.board_changes(noise))


class FullPass0410(unittest.TestCase):
    """Что сломалось в полном проходе 05.10 по записям 04.10 (ADR-029)."""

    def test_static_corner_kaluga(self):
        # угол кадра — трибуна, камера почти не двигается: прикидка считает графикой почти всю рамку. Тогда
        # графика — строго по всем кадрам, табло на экране, голы находятся. Почему у «Калуги» 04.10 табло не было
        # видно ни в одном кадре, синтетика не повторяет — это скажут её кадры (frames_*.gz)
        back = bytes((x * 7 + y * 13) % 256 for y in range(H) for x in range(W))

        def still(t):
            f = bytearray(frame(t, GOALS, HIDDEN))
            noise = random.Random(int(t)).random() < 0.2          # пятая часть кадров — камера повернулась
            for p in range(W * H):
                if not (5 <= p // W < 35 and 5 <= p % W < 160) and not noise:
                    f[p] = back[p]
            return bytes(f)
        s = [(t, still(t)) for t in range(0, 1200, 10)]
        mask, med, kernel = sb.plate([f for _, f in s])
        self.assertLess(len(mask), W * H)
        seen = sum(sb.shown(f, kernel, med) for _, f in s)
        self.assertGreater(seen, 0.7 * len(s))
        found = sb.changes(s, mask, med, core=kernel)
        self.assertEqual([c["hi"] for c in found], [210, 480, 910])

    def test_see_through_plate_rostov(self):
        # плашка полупрозрачная: сквозь неё то лёд, то трибуна (камера поворачивается раз в минуту); буквы и цифры
        # непрозрачные. По ядру (буквы) табло на экране при любом фоне
        letters = {y * W + x for y in range(8, 32) for x in range(10, 60) if (x // 4 + y // 4) % 2 == 0}

        def glass(t):
            f = bytearray(frame(t, GOALS, HIDDEN))
            if any(a <= t < b for a, b in HIDDEN):
                return bytes(f)
            under = 230 if (t // 60) % 2 else 40
            for x, y in BOX:
                p = y * W + x
                if p in letters:
                    f[p] = 250
                elif f[p] == 30:                                    # фон плашки — полупрозрачный
                    f[p] = 15 + under // 2
            return bytes(f)
        s = [(t, glass(t)) for t in range(0, 1200, 10)]
        mask, med, kernel = sb.plate([f for _, f in s])
        shown_ = [t for t, f in s if sb.shown(f, kernel, med)]
        hidden = [t for t, _ in s if any(a <= t < b for a, b in HIDDEN)]
        self.assertEqual(len(shown_), len(s) - len(hidden))
        found = sb.changes(s, mask, med, core=kernel)
        self.assertEqual([c["hi"] for c in found], [210, 480, 910])

    def test_dense_refine_through_goal_splash(self):
        # после смены табло сразу убрали на 40 с (заставка «GOAL» и повтор): кадр первого прохода с новым счётом
        # только через 44 с. Деление пополам упирается в пустоту, кадр каждую секунду — нет
        hidden = [(477, 520)]
        s = samples(hidden=hidden)
        frames = [f for _, f in s]
        mask, med, kernel = sb.plate(frames)
        ch = next(c for c in sb.changes(s, mask, med, core=kernel) if c["hi"] > 400)
        self.assertEqual((ch["lo"], ch["hi"]), (470, 520))
        get = lambda t: frame(round(t), GOALS, hidden)              # noqa: E731
        visible = lambda f: sb.shown(f, kernel, med)                 # noqa: E731
        bisect = sb.refine(ch, get, visible, 10)
        self.assertGreater(bisect["hi"], 500)
        window = lambda a, b: [(t, frame(t, GOALS, hidden)) for t in range(int(a), int(b) + 1)]   # noqa: E731
        dense = sb.refine(ch, get, visible, 10, window=window)
        self.assertEqual(dense["hi"], 476)

    def test_sides_keep_clock_apart(self):
        # «Ростов — Краснодар»: гости забили 4, а смен у табло 9 — номер периода и минуты часов. Зона гостей одна
        def c(t, zone):
            return {"hi": t, "lo": t - 10, "zone": zone}
        found = [c(2800, 1), c(3030, 2), c(3100, 1), c(3700, 2), c(5170, 1), c(5600, 0), c(7400, 2), c(7440, 1)]
        goals = [("0:1", "1", 2800 - 30), ("0:2", "2", 3100 - 30), ("0:3", "2", 5170 - 30), ("0:4", "3", 7440 - 30)]
        picked, zones = sb.align_sides(found, goals)
        self.assertEqual(picked, {"0:1": 2800, "0:2": 3100, "0:3": 5170, "0:4": 7440})
        self.assertEqual(zones[1], 1)
        self.assertEqual(sb.goal_sides(goals + [("1:4", "3", 8000)])["1:4"], "home")

    def test_cache_round_trip(self):
        import tempfile
        s = samples(until=100)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frames.gz"
            sb.save_cache(path, s, {"name": "м", "box": [0, 0, 1, 1], "step": 10, "truth": {"1:0": 5}, "site": []})
            got, meta = sb.load_cache(path)
        self.assertEqual(got, s)
        self.assertEqual((meta["name"], meta["truth"]), ("м", {"1:0": 5}))


# Табло с разметкой клеток (BOARDS), как у клубов 04.10: название хозяев — буквы, цифры счёта, часы игры
CELL_BOARD = {"box": (0, 0, 1, 1), "name": (10, 8, 56, 30), "home": (98, 8, 114, 32), "away": (123, 8, 139, 32),
              "clock": (60, 10, 86, 30)}
LETTERS = [(x, y) for y in range(8, 30) for x in range(10, 56)]
CLOCK_PX = [(x, y) for y in range(10, 30) for x in range(60, 86)]


def scoreboard(t, goals, hidden=(), stale=(), back=0):
    """Кадр с табло: фон — шум, лёд (белое) или трибуна (тёмное) по очереди; часы игры идут и встают на 25 с после
    каждого гола (счёт на табло меняется через 7 с после гола). stale — повтор со старым табло: счёт и часы как
    за 40 с до начала повтора. back — фон: 0 шум, 1 лёд, 2 трибуна."""
    rnd = random.Random(int(t * 1000))
    px = bytearray(rnd.randrange(256) if back == 0 else (250 if back == 1 else 20) for _ in range(W * H))
    if any(a <= t < b for a, b in hidden):
        return bytes(px)
    now = next((a - 40 for a, b in stale if a <= t < b), t)
    home = sum(1 for g, side in goals if g + 7 <= now and side == "home")
    away = sum(1 for g, side in goals if g + 7 <= now and side == "away")
    stopped = sum(min(max(now - g, 0), 25) for g, _ in goals)
    clock = int(now - stopped)
    for x, y in BOX:
        px[y * W + x] = 30
    for x, y in LETTERS:
        px[y * W + x] = 230 if (x // 4 + y // 5) % 2 == 0 else 30
    for (x, y), v in {**digit(HOME, home), **digit(AWAY, away), **digit(CLOCK_PX, clock)}.items():
        px[y * W + x] = v
    return bytes(px)


class Cells(unittest.TestCase):
    """Разбор по клеткам табло (BOARDS) — то, что на кадрах 04.10 нашло 14 голов из 15."""

    def test_on_screen_not_fooled_by_ice_or_stands(self):
        s = [(t, scoreboard(t, GOALS, HIDDEN)) for t in range(0, 1200, 10)]
        model = sb.name_model([f for _, f in s], CELL_BOARD["name"])
        self.assertTrue(sb.on_screen(scoreboard(100, GOALS), model))
        for back in (0, 1, 2):
            self.assertFalse(sb.on_screen(scoreboard(215, GOALS, HIDDEN, back=back), model))

    def test_changes_by_cell_through_hidden_and_stale_board(self):
        # после гола табло убрали на 15 с, мелькнул новый счёт, потом 30 с повтора со старыми счётом и часами
        stale = [(g + 32, g + 62) for g, _ in GOALS]
        s = [(t, scoreboard(t, GOALS, HIDDEN, stale)) for t in range(0, 1200, 10)]
        visible, found = sb.analyse(s, CELL_BOARD)
        self.assertEqual([(c["zone"], c["hi"]) for c in found], [("home", 230), ("away", 500), ("home", 930)])

    def test_clock_stop_is_the_goal(self):
        # кадр каждую секунду: часы встали в секунду гола, счёт сменился через 7 с
        dense = [(t, scoreboard(t, GOALS)) for t in range(400, 520)]
        visible, found = sb.analyse(dense, CELL_BOARD)
        ch = next(c for c in found if c["zone"] == "away")
        self.assertEqual(ch["hi"], 483)
        stop = sb.clock_stop([(t, f) for t, f in dense if visible(f)], ch["hi"], sb.cell_pixels(CELL_BOARD["clock"]), 2)
        self.assertEqual(stop, (475, 476))                            # с секунды гола часы стоят

    def test_corrected_score_has_no_clock_stop(self):
        # счёт поправили через 90 с, когда часы уже снова шли: остановки часов на этом значении нет — не врём
        dense = [(t, scoreboard(t, GOALS)) for t in range(400, 620)]
        vis = [(t, f) for t, f in dense]
        self.assertIsNone(sb.clock_stop(vis, 566, sb.cell_pixels(CELL_BOARD["clock"]), 2))

    def test_order_survives_jittered_site_times(self):
        # «Тверичи — Металлург» 04.10: сайт лиги отмечал голы второго периода неровно (разброс до двух минут), смены
        # табло гостей — в порядке голов, плюс лишняя смена после матча
        def c(t, zone):
            return {"hi": t, "lo": t - 10, "zone": zone}
        found = [c(1530, "away"), c(3450, "away"), c(3580, "away"), c(4840, "away"), c(6210, "home"),
                 c(6950, "home"), c(8120, "away")]
        base = 1_759_000_000
        goals = [("0:1", "1", base + 1520), ("0:2", "2", base + 3424 + 315), ("0:3", "2", base + 3577 + 435),
                 ("0:4", "2", base + 4840 + 326), ("1:4", "3", base + 6207 + 542), ("2:4", "3", base + 6925 + 527)]
        got = sb.align_order(found, goals)
        self.assertEqual(got, {"0:1": 1530, "0:2": 3450, "0:3": 3580, "0:4": 4840, "1:4": 6210, "2:4": 6950})


class Align(unittest.TestCase):
    def test_period_shift_and_extra_changes(self):
        # запись начата в 1000 по часам; во 2-м периоде трансляцию прервали — сдвиг на 300 с. Сайт лиги запаздывает
        # на 20–70 с; лишние смены — десятки минут часов (1500, 2600) и номер периода (2100)
        found = [1500, 1606, 1876, 2100, 2600, 3206 + 300]
        goals = [("1:0", "1", 1000 + 606 + 40), ("1:1", "1", 1000 + 876 + 70), ("2:1", "2", 1000 + 2206 + 20)]
        self.assertEqual(sb.align(found, goals), {"1:0": 1606, "1:1": 1876, "2:1": 3506})

    def test_nothing_near(self):
        self.assertEqual(sb.align([10, 20], [("1:0", "1", 100000)]), {"1:0": 10})   # один гол — один кандидат
        self.assertEqual(sb.align([], [("1:0", "1", 5)]), {})


class Args(unittest.TestCase):
    def test_box_and_truth(self):
        self.assertEqual(sb.parse_box("0,0.7,0.5,0.3"), (0.0, 0.7, 0.5, 0.3))
        with self.assertRaises(Exception):
            sb.parse_box("0.8,0,0.5,0.3")                              # вылезает за кадр
        self.assertEqual(sb.parse_truth("1:0=42:53, 0:2=1:25:25, плохо=1"), {"1:0": 2573, "0:2": 5125})


if __name__ == "__main__":
    unittest.main()

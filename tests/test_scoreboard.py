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


def frame(t, goals, hidden=(), rnd=None):
    """Кадр на секунде t: игра — шум, табло слева сверху со счётом на момент t. goals — [(секунда смены, сторона)].
    hidden — промежутки, когда плашку убрали (повтор)."""
    rnd = rnd or random.Random(int(t * 1000))
    px = bytearray(rnd.randrange(256) for _ in range(W * H))
    if any(a <= t < b for a, b in hidden):
        return bytes(px)
    home = sum(1 for s, side in goals if s <= t and side == "home")
    away = sum(1 for s, side in goals if s <= t and side == "away")
    for x, y in BOX:
        px[y * W + x] = 30
    for (x, y), v in {**digit(HOME, home), **digit(AWAY, away)}.items():
        px[y * W + x] = v
    for x, y in CLOCK:
        px[y * W + x] = rnd.randrange(256)
    return bytes(px)


GOALS = [(206, "home"), (476, "away"), (906, "home")]
HIDDEN = [(212, 227), (482, 497), (912, 927)]                     # повтор после гола — плашки нет


def samples(step=10, until=1200, goals=GOALS, hidden=HIDDEN):
    return [(t, frame(t, goals, hidden)) for t in range(0, until, step)]


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
        got = [sb.refine(c, lambda t: frame(t, GOALS, HIDDEN), self.mask, self.med, 10) for c in found]
        self.assertEqual([round(c["hi"]) for c in got], [206, 476, 906])
        self.assertTrue(all(c["hi"] - c["lo"] <= 1 for c in got))

    def test_against_admin(self):
        rows = sb.against([206, 476, 906], {"1:0": 200, "1:1": 470, "2:1": 900, "3:1": 1100})
        self.assertEqual([round(s - t) if s is not None else None for _, t, s in rows], [6, 6, 6, None])
        self.assertIn("3 из 4", sb.lag_summary(rows))


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

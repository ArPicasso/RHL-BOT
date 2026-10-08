"""tools/probe_record.py (ADR-038, шаг 4.1): табло в своей записи — сколько разных картинок цифры и вердикт. Без сети."""
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import probe_record as pr  # noqa: E402
import probe_scoreboard as sb  # noqa: E402

CELL = (100, 10, 120, 40)   # клетка цифры в рамке 240×90


def frame(pattern: int, rnd: random.Random) -> bytes:
    """Светлая клетка с тёмной «цифрой» — узор полос по номеру; сжатие — шум ±6 по всему кадру."""
    px = bytearray(rnd.randrange(256) for _ in range(sb.W * sb.H))
    x0, y0, x1, y1 = CELL
    for y in range(y0, y1):
        for x in range(x0, x1):
            ink = (x - x0) // 4 % 3 == pattern % 3 and (y - y0) // 6 % 2 == pattern // 3 % 2
            px[y * sb.W + x] = max(0, min(255, (30 if ink else 230) + rnd.randint(-6, 6)))
    return bytes(px)


class Classes(unittest.TestCase):
    def test_noise_is_not_a_new_digit(self):
        # пилот 08.10: байты кадров с одной цифрой всегда разные — а картинка цифры одна
        rnd = random.Random(1)
        self.assertEqual(pr.classes([frame(0, rnd) for _ in range(30)], CELL, 24), (1, 0))

    def test_two_digits(self):
        rnd = random.Random(2)
        frames = [frame(0, rnd) for _ in range(20)] + [frame(4, rnd) for _ in range(20)]
        self.assertEqual(pr.classes(frames, CELL, 24)[0], 2)

    def test_flicker_is_not_a_digit_and_cap(self):
        rnd = random.Random(3)
        frames = [frame(0, rnd) for _ in range(20)] + [frame(4, rnd)]          # одна картинка на кадр — мелькание
        self.assertEqual(pr.classes(frames, CELL, 24)[0], 1)
        noisy = [bytes(rnd.randrange(256) for _ in range(sb.W * sb.H)) for _ in range(40)]   # фон, а не цифры
        self.assertEqual(pr.classes(noisy, CELL, 5)[0], 6)


class Verdict(unittest.TestCase):
    def board(self, share, home=1, away=1):
        return {"share": share, "cells": {"home": (home, 0), "away": (away, 0), "clock": (25, 0)}}

    def test_verdicts(self):
        self.assertIn("делать можно", pr.verdict(self.board(0.46, 2, 1)))
        self.assertIn("редко", pr.verdict(self.board(0.2)))
        self.assertIn("не различаются", pr.verdict(self.board(0.6, home=12)))


if __name__ == "__main__":
    unittest.main()

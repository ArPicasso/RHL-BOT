"""clockrun.py (ADR-031): секунда гола по ходу часов табло — на синтетической записи, без кадров."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import clockrun as cr  # noqa: E402


def match(segments, unknown=()):
    """Запись: [(с какой секунды, сколько секунд часы идут)] — между отрезками часы стоят. unknown — секунды без
    табло на экране. → state(t): «run», «stop» или None."""
    run = {t for a, n in segments for t in range(a, a + n)}

    def state(t):
        if t in unknown:
            return None
        return "run" if t in run else "stop"
    return state


# Период: часы пошли на 100-й секунде записи. Гол A — 5:00 (часы встали на 399), 60 с стоят, гол B — 8:20 (встали на
# 659), 40 с стоят, остановка-вбрасывание на 10:00 (часы встали на 799), 30 с, гол C — 12:00 (встали на 949).
SEG = [(100, 300), (460, 200), (700, 100), (830, 120), (1000, 300)]


def goal(score, time, t=None, src=None, change=None, period="1"):
    return {"score": score, "period": period, "time": time, "t": t, "src": src, "change": change}


class Walk(unittest.TestCase):
    def test_forward_and_back(self):
        state = match(SEG)
        self.assertEqual(cr.walk(state, 399, 200), [659])
        self.assertEqual(cr.walk(state, 659, -200), [399])
        self.assertEqual(cr.walk(state, 399, 420), [949])
        self.assertEqual(cr.walk(state, 399, 450), [])               # дотянулись, а часы на этом месте шли
        self.assertIsNone(cr.walk(state, 399, 0))
        self.assertIsNone(cr.walk(state, 399, 5000, limit=600))

    def test_unknown_seconds_widen_window(self):
        state = match(SEG, unknown=set(range(470, 480)))          # крупный план, часы шли
        self.assertEqual(cr.walk(state, 399, 200), [659])            # но остановка на месте одна — гол точный
        state = match(SEG, unknown=set(range(640, 720)))          # табло не было вокруг гола
        self.assertEqual(cr.walk(state, 399, 200), list(range(659, 719)))   # гол где-то тут, превью этого окна

    def test_game_time(self):
        self.assertEqual([cr.game_sec(x) for x in ("05:26", "59:33", "62:05", "5:7", "", None)],
                         [326, 3573, 3725, None, None, None])


class Solve(unittest.TestCase):
    def test_checked_pair_gives_exact_seconds(self):
        state = match(SEG)
        goals = [goal("1:0", "05:00", 399, "clock"), goal("1:1", "08:20"), goal("2:1", "12:00", 949, "admin")]
        got = cr.solve(goals, state)
        self.assertEqual(got["checked"], 2)
        self.assertEqual(got["found"]["1:1"]["t"], 659)
        self.assertEqual((got["drop"], got["fail"]), ([], []))
        # пара сошлась — оба гола подтверждены ходом часов: второй свидетель для клипа (ADR-033, раздел 4)
        self.assertEqual(got["confirmed"], ["1:0", "2:1"])

    def test_unchecked_anchor_gives_window_only(self):
        # опора одна — счёт не проверен: точной секунды нет, только окно и остановки в нём (превью, примерный повтор)
        state = match(SEG)
        got = cr.solve([goal("1:0", "05:00", 399, "clock"), goal("1:1", "08:20")], state)
        self.assertEqual(got["checked"], 0)
        self.assertEqual(got["found"]["1:1"], {"win": [659, 659], "cand": [659], "from": "1:0"})
        self.assertEqual(got["confirmed"], [])                # одна опора — подтверждать нечем

    def test_change_on_board_confirms_unchecked(self):
        state = match(SEG)
        got = cr.solve([goal("1:0", "05:00", 399, "clock"), goal("1:1", "08:20", change=700)], state)
        self.assertEqual(got["found"]["1:1"]["t"], 659)
        far = cr.solve([goal("1:0", "05:00", 399, "clock"), goal("1:1", "08:20", change=2000)], state)
        self.assertEqual(far["found"], {})                    # окно далеко до смены счёта — что-то не то, молчим

    def test_failed_check_drops_board_second(self):
        # 05.10 «Калуга»: секунда 4:0 по табло на самом деле от другого гола — по протоколу между голами 3:20 хода
        state = match(SEG)
        goals = [goal("1:0", "05:00", 399, "admin"), goal("2:0", "08:20", 949, "clock"), goal("3:0", "12:00")]
        got = cr.solve(goals, state)
        # ADR-033: отметка человека — тоже показание: пара не сошлась — точность снимается с обоих
        self.assertEqual((got["drop"], got["fail"], got["found"]), (["1:0", "2:0"], ["1"], {}))
        self.assertEqual(got["confirmed"], [])                # не сошлась — не свидетель

    def test_wide_window_or_other_period_gives_nothing(self):
        unknown = set(range(460, 600))
        state = match(SEG, unknown=unknown)
        got = cr.solve([goal("1:0", "05:00", 399, "clock"), goal("1:1", "08:20")], state)
        self.assertEqual(got["found"], {})                    # табло не было 140 с — окно шире двух минут
        state = match(SEG)
        got = cr.solve([goal("1:0", "05:00", 399, "clock"), goal("1:1", "28:20", period="2")], state)
        self.assertEqual(got["found"], {})                    # опоры в периоде нет
        self.assertEqual(cr.solve([goal("1:0", "05:00", 399), goal("1:1", "", period="РБ")], state)["found"], {})

    def test_anchor_on_each_side_must_agree(self):
        state = match(SEG)
        goals = [goal("1:0", "05:00", 399, "admin"), goal("1:1", "08:20"), goal("2:1", "12:00", 949, "admin")]
        self.assertEqual(cr.solve(goals, state)["found"]["1:1"]["t"], 659)
        goals[2]["time"] = "12:30"     # по протоколу на 30 с позже — от неё гол 1:1 ушёл бы на 30 с: окна врозь
        got = cr.solve(goals, state)
        self.assertNotIn("1:1", got["found"])


class CheckMarks(unittest.TestCase):
    """ADR-033: отметка человека проверяется табло — встали ли часы, когда сменился счёт, сходится ли ход часов."""

    def test_true_mark_agrees(self):
        state = match(SEG)
        goals = [goal("1:0", "05:00", 400, "admin", change=430), goal("1:1", "08:20", 659, "clock")]
        got = cr.check_marks(goals, state)["1:0"]
        self.assertEqual(got["status"], "ok")
        self.assertEqual(got["for"], ["часы встали", "счёт на табло сменился после", "ход часов от 1:1"])

    def test_typo_is_a_conflict(self):
        # опечатка: 0:05:00 → 0:00:05 — часы ещё не пошли, счёт сменился позже на семь минут, ход часов не сходится
        state = match(SEG)
        goals = [goal("1:0", "05:00", 300, "admin", change=430), goal("1:1", "08:20", 659, "clock")]
        got = cr.check_marks(goals, state)["1:0"]
        self.assertEqual(got["status"], "conflict")
        self.assertIn("часы в это время идут", got["against"])
        self.assertIn("ход часов от 1:1 не сходится", got["against"])
        late = cr.check_marks([goal("1:0", "05:00", 500, "admin", change=430)])["1:0"]
        self.assertEqual((late["status"], late["against"]), ("conflict", ["счёт на табло сменился раньше"]))

    def test_nothing_to_check(self):
        state = match(SEG, unknown=set(range(380, 420)))   # табло нет вокруг отметки, смены счёта не видели
        got = cr.check_marks([goal("1:0", "05:00", 399, "admin"), goal("2:0", "28:00", 2000, "clock", period="2")],
                             state)
        self.assertEqual(set(got), {"1:0"})                   # проверяем только отметки людей
        self.assertEqual(got["1:0"]["status"], "unknown")


if __name__ == "__main__":
    unittest.main()

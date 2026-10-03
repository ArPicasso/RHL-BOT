"""Сборка «Раската»: index.json и файлы дней по разделу 2 контракта, самопроверка, флаги."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_raskat  # noqa: E402
import raskat  # noqa: E402
from raskat import rules  # noqa: E402
from raskat.puzzle import edges, wall_pairs  # noqa: E402

NOW = "2026-10-09T09:17:00+03:00"      # пятница, седьмой день сезона


def run(*argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = build_raskat.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.out = Path(cls.dir.name) / "raskat"
        cls.code, cls.log, _ = run("--out", str(cls.out), "--now", NOW)
        cls.index = json.loads((cls.out / "index.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def test_index_by_the_contract(self):
        self.assertEqual(self.code, 0)
        self.assertEqual(set(self.index), {"built", "season", "today", "days"})
        self.assertEqual((self.index["season"], self.index["today"], self.index["built"]),
                         (rules.SEASON, "2026-10-09", NOW))
        self.assertEqual([d["date"] for d in self.index["days"]][:2], ["2026-10-03", "2026-10-04"])
        self.assertEqual([d["n"] for d in self.index["days"]], list(range(1, 8)))
        self.assertEqual(self.index["days"][0],
                         {"date": "2026-10-03", "n": 1, "w": 6, "h": 6, "k": 5, "hard": 1, "par": 70})
        self.assertIn("раскладов 7", self.log)

    def test_one_file_a_day_and_nothing_else(self):
        self.assertEqual(sorted(p.name for p in self.out.iterdir()),
                         ["2026-10-03.json", "2026-10-04.json", "2026-10-05.json", "2026-10-06.json",
                          "2026-10-07.json", "2026-10-08.json", "2026-10-09.json", "index.json"])

    def test_day_files_by_the_contract(self):
        for row in self.index["days"]:
            day = json.loads((self.out / f"{row['date']}.json").read_text(encoding="utf-8"))
            self.assertEqual(set(day), {"date", "n", "w", "h", "dots", "walls", "hard", "par", "lede"},
                             row["date"])
            self.assertEqual([day[f] for f in ("date", "n", "w", "h", "hard", "par")],
                             [row[f] for f in ("date", "n", "w", "h", "hard", "par")])
            self.assertEqual(len(day["dots"]), row["k"])
            cells = day["w"] * day["h"]
            self.assertTrue(all(isinstance(c, int) and 0 <= c < cells for c in day["dots"]), row["date"])
            self.assertEqual(len(set(day["dots"])), len(day["dots"]), row["date"])
            self.assertTrue(day["lede"].endswith("."), row["date"])

    def test_walls_are_only_between_neighbours(self):
        for row in self.index["days"]:
            day = json.loads((self.out / f"{row['date']}.json").read_text(encoding="utf-8"))
            grid = set(edges(day["w"], day["h"]))
            pairs = wall_pairs(day["walls"])
            self.assertEqual(len(pairs), len(day["walls"]), row["date"])       # без повторов
            self.assertEqual(pairs - grid, set(), row["date"])
            for s in day["walls"]:
                a, b = (int(x) for x in s.split("-"))
                self.assertLess(a, b, s)                                       # «меньший-больший»

    def test_no_solution_in_the_files(self):
        """Решения в опубликованном файле нет (раздел 2), и расклад всё равно решается движком."""
        for row in self.index["days"]:
            text = (self.out / f"{row['date']}.json").read_text(encoding="utf-8")
            self.assertEqual(set(json.loads(text)) & {"path", "solution", "solve", "answer"}, set())
            p = raskat.generate(row["date"])
            self.assertEqual(len(raskat.solve(p, 2)), 1, row["date"])

    def test_day_file_matches_the_engine(self):
        for row in self.index["days"]:
            got = json.loads((self.out / f"{row['date']}.json").read_text(encoding="utf-8"))
            self.assertEqual(got, raskat.as_json(raskat.generate(row["date"])), row["date"])

    def test_rebuild_changes_nothing(self):
        before = {p.name: p.read_text(encoding="utf-8") for p in self.out.iterdir()}
        run("--out", str(self.out), "--now", NOW)
        after = {p.name: p.read_text(encoding="utf-8") for p in self.out.iterdir()}
        self.assertEqual(before, after)

    def test_check_finds_a_spoiled_file(self):
        code, log, _ = run("--check", "--out", str(self.out))
        self.assertEqual((code, "замечаний 0" in log), (0, True))
        day = self.out / "2026-10-05.json"
        was = day.read_text(encoding="utf-8")
        try:
            data = json.loads(was)
            data["dots"] = data["dots"][::-1]
            day.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            code, _, err = run("--check", "--out", str(self.out))
            self.assertEqual(code, 1)
            self.assertIn("не сходится с движком", err)
        finally:
            day.write_text(was, encoding="utf-8")


class Flags(unittest.TestCase):
    def test_single_day(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "raskat"
            code, log, _ = run("--out", str(out), "--date", "2026-11-20")
            self.assertEqual(code, 0)
            self.assertEqual([p.name for p in out.iterdir()], ["2026-11-20.json"])
            self.assertIn("«Раскат» № 49", log)
            self.assertEqual(json.loads(log.splitlines()[0]),
                             raskat.as_json(raskat.generate("2026-11-20")))

    def test_day_before_the_season(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, err = run("--out", str(Path(d) / "r"), "--date", "2026-09-30")
            self.assertEqual(code, 1)
            self.assertIn("до начала сезона", err)

    def test_not_a_date(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, err = run("--out", str(Path(d) / "r"), "--date", "вчера")
            self.assertEqual((code, "Не дата" in err), (1, True))

    def test_now_needs_a_timezone(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, err = run("--out", str(Path(d) / "r"), "--now", "2026-10-09T09:17:00")
            self.assertEqual((code, "с поясом" in err), (1, True))

    def test_before_the_season_there_are_no_days(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "raskat"
            code, log, _ = run("--out", str(out), "--now", "2026-09-20T12:00:00+03:00")
            self.assertEqual(code, 0)
            self.assertIn("раскладов пока нет", log)
            self.assertEqual(json.loads((out / "index.json").read_text(encoding="utf-8"))["days"], [])

    def test_season_end_is_the_last_day(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "raskat"
            run("--out", str(out), "--now", "2027-04-01T12:00:00+03:00")
            index = json.loads((out / "index.json").read_text(encoding="utf-8"))
            self.assertEqual((index["today"], index["days"][-1]["date"], len(index["days"])),
                             ("2027-04-01", rules.SEASON_TO.isoformat(), 170))

    def test_check_without_a_build(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, err = run("--check", "--out", str(Path(d) / "нет"))
            self.assertEqual((code, "index.json не читается" in err), (1, True))


if __name__ == "__main__":
    unittest.main()

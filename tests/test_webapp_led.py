"""Проводка игры «Наш лёд» (ADR-017): вкладка, файлы и правила на месте.

Логика игры живёт в JS, тестов на него в проекте нет. Этот тест стережёт то, что ломается
молча: вкладка перестала грузиться, задание Pages правит старые имена файлов, числа правил
в коде разошлись с ADR.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "webapp"


def read(p):
    return (ROOT / p).read_text(encoding="utf-8")


class Wiring(unittest.TestCase):
    def test_index_loads_game(self):
        html = read("webapp/index.html")
        self.assertIn('<link rel="stylesheet" href="led.css">', html)
        self.assertIn('<script defer src="led.js"></script>', html)
        self.assertIn('data-tab="led"', html)
        self.assertIn('window.LED_API = ""', html)
        self.assertNotIn("zveno.js", html)

    def test_app_knows_tab(self):
        app = read("webapp/app.js")
        self.assertIn('TAB_ORDER = ["home", "calendar", "table", "led", "me"]', app)
        self.assertIn("led: renderLed", app)
        # ни одной ссылки на функции «Звена»: его файл больше не грузится
        self.assertNotIn("zv", app.replace("zvuk", ""))

    def test_game_defines_hooks(self):
        js = read("webapp/led.js")
        for fn in ("renderLed", "ledMounted", "ledPeek", "ledLinkParam", "ledFromLink", "ledRestoreJoin"):
            self.assertRegex(js, rf"function {fn}\b", fn)

    def test_rules_match_adr(self):
        js = read("webapp/led.js")
        want = {"L_FORCES": 10, "L_CAP": 4, "L_BASE": 10, "L_ZONE": 10, "L_RARE": 5}
        for name, value in want.items():
            m = re.search(rf"const {name} = (\d+);", js)
            self.assertIsNotNone(m, name)
            self.assertEqual(int(m.group(1)), value, name)

    def test_pages_rewrites_game_files(self):
        yml = read(".github/workflows/pages.yml")
        self.assertIn("led.css?v=$v", yml)
        self.assertIn("led.js?v=$v", yml)
        self.assertIn("LED_API", yml)
        self.assertNotIn("zveno", yml)

    def test_files_exist(self):
        for name in ("led.js", "led.css"):
            self.assertTrue((WEB / name).exists(), name)


if __name__ == "__main__":
    unittest.main()

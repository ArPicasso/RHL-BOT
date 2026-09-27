"""Сверка стоимости «Звена» с моделью сезона tools/fantasy_model/season.py (ADR-014, раздел 14).

Модель считает цены всех наклеек на каждый дедлайн. Здесь те же приоры и те же поматчевые очки
идут в zveno.prices.price_path — стоимость на каждый из 22 дедлайнов должна совпасть до льдинки,
с затуханием (полураспад 20) и без него.

Нужны numpy и кэш модели: `cd tools/fantasy_model && python3 data.py`. Без них тест пропускается.
season.py не меняем: берём его текст и в памяти добавляем в Season приоры и ряды матчей.
"""
import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "tools" / "fantasy_model"
sys.path.insert(0, str(ROOT))

from zveno import prices  # noqa: E402

HAVE_MODEL = importlib.util.find_spec("numpy") is not None and (MODEL / "cache" / "base.pkl").exists()
SLOT = {0: "G", 1: "D", 2: "F"}


def load_season():
    src = (MODEL / "season.py").read_text(encoding="utf-8")
    marker = "    S.has_prior = np.array([p is not None for p in prior])\n"
    assert marker in src, "season.py изменился — поправь сверку"
    src = src.replace(marker, marker + "    S.prior_list = prior; S.seqs = seqs\n")
    mod = types.ModuleType("season_parity")
    mod.__file__ = str(MODEL / "season.py")
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)
    return mod


@unittest.skipUnless(HAVE_MODEL, "нет numpy или кэша модели tools/fantasy_model/cache/base.pkl")
class ModelParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.season = load_season()

    def check(self, cfg: dict, decay):
        S = self.season.realize(1, cfg)
        T = self.season.T
        inpool = [i for i in range(S.N) if S.seqs[i]]
        pick = []
        for role in (0, 1, 2):
            for has in (True, False):
                c = [i for i in inpool if S.role[i] == role and (S.prior_list[i] is not None) == has]
                if c:
                    pick.append(max(c, key=lambda i: len(S.seqs[i])))
        self.assertGreaterEqual(len(pick), 5)
        for i in pick:
            slot, pr = SLOT[int(S.role[i])], S.prior_list[i]
            played = [(t, p) for t, _, p in S.seqs[i]]
            for t in range(1, T + 2):
                path = prices.price_path(slot, pr, [x for x in played if x[0] < t], t, decay_half=decay)
                self.assertEqual(path.mondays[t], int(S.price[t, i]), f"{slot} {pr} тур {t}")

    def test_with_decay(self):
        self.check({"decay_half": 20}, 20)

    def test_without_decay(self):
        self.check({}, None)


if __name__ == "__main__":
    unittest.main()

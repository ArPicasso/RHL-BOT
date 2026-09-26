"""Лимит владения в своей лиге: сколько слотов состава упёрлись бы в лимит «не больше K владельцев в лиге из 20».
Послеоценка на составах базового варианта (без изменения поведения) — верхняя граница эффекта."""
import random, collections, numpy as np, sys
from sim import Sim, ACTIVE_TYPES
class S2(Sim):
    def measure_pre(self, t):
        if t not in (4, 8, 12, 16, 21): return
        rnd = random.Random(t)
        cur = [m for m in self.ms if m.team is not None]
        rnd.shuffle(cur)
        res = collections.defaultdict(list)
        for g in range(0, len(cur) - 19, 20):
            L = cur[g:g + 20]
            own = collections.Counter(i for m in L for i in m.team)
            for m in L:
                if m.arch not in ACTIVE_TYPES: continue
                for K in (2, 3, 4):
                    res[K].append(np.mean([own[i] - 1 >= K for i in m.team]))
            # доля «шаблонных» пар: любые двое активных в лиге делят сколько наклеек
            act = [m for m in L if m.arch in ACTIVE_TYPES]
            for a in range(len(act)):
                for b in range(a + 1, len(act)):
                    res['shared'].append(len(set(act[a].team) & set(act[b].team)))
        self.met['owncap'][t] = {k: float(np.mean(v)) for k, v in res.items()}
out = collections.defaultdict(list)
for seed in (1000, 1001, 1002):
    r = S2(seed, {}, twins=False).run()
    for t, d in r['met']['owncap'].items(): out[t].append(d)
for t in sorted(out):
    print('тур', t, {k: round(float(np.mean([d[k] for d in out[t]])), 3) for k in out[t][0]})

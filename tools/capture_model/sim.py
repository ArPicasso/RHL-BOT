"""Модель «захвата карты»: проверка механики из ADR-017 на реальном сезоне НМХЛ 25/26.

Нужна не для продукта, а для решений: прежде чем включать правило, прогоняем его здесь
и сверяем с бюджетом из README. Цифры модельные — порядок величин, не прогноз.

Запуск:
    python3 sim.py            # базовый вариант, 20 прогонов
    python3 sim.py base 20
    python3 sim.py all 20     # все варианты из VARIANTS и сравнение
Только stdlib.
"""
import json
import random
import statistics as st
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# ---------- правила (вариант = словарь поверх BASE) ----------

BASE = dict(
    forces=10,        # силы на раунд, у всех одинаково
    cap=4,            # не больше сил на один матч
    base=10,          # очки за верную силу — за знание результата
    zone=10,          # надбавка, если эта сила взяла или удержала зону
    rare=5,           # надбавка за редкость: полная, если на твоей стороне почти никого
    pot=100,          # вариант «чистый котёл»: очки зоны делятся по силам
    managers=300,
    rounds_metric=7,  # после какого раунда сверяем место с финальным
    skew_club=0.0,    # доля болельщиков самого большого клуба (0 — доли по умолчанию)
    per_capita=True,  # зона считается по доле активных болельщиков клуба, а не по их числу
    autopilot=True,   # кто не пришёл, расставляется публичным правилом
)

VARIANTS = {
    'base': {},
    'pot': dict(scoring='pot'),            # чистый котёл, как в тотализаторе: только редкость
    'flat': dict(rare=0, zone=0),          # только знание результата
    'rare20': dict(rare=20, zone=0),       # редкость вместо зоны
    'zone20': dict(rare=0, zone=20),       # зона вместо редкости
    'allexpert': dict(all_expert=True),    # все знатоки и все учитывают толпу — есть ли доминирующая стратегия
    'nocap': dict(cap=10),                 # можно вложить все силы в один матч
    'absolute': dict(per_capita=False),    # зону берёт клуб по числу сил, без поправки на размер фанбазы
    'bigclub': dict(skew_club=0.30),       # 30% болельщиков у одного клуба
    'bigclub_abs': dict(skew_club=0.30, per_capita=False),  # и зона по числу сил — проверка поправки
}

# доля, шум восприятия силы команд, бонус своему клубу, учёт толпы, вероятность прийти в раунде
ARCH = {
    'casual':    dict(share=.30, eps=.15, bias=.15, crowd=0.0, show=.35),
    'parent':    dict(share=.18, eps=.15, bias=.25, crowd=0.0, show=.30),
    'active':    dict(share=.18, eps=.07, bias=.10, crowd=0.5, show=.85),
    'optimizer': dict(share=.06, eps=.03, bias=.00, crowd=1.0, show=.98),
    'form':      dict(share=.08, eps=.09, bias=.05, crowd=0.3, show=.80),
    'fan':       dict(share=.10, eps=.12, bias=.60, crowd=0.0, show=.70),
    'late':      dict(share=.05, eps=.07, bias=.10, crowd=0.5, show=.85),
    'quitter':   dict(share=.05, eps=.10, bias=.10, crowd=0.2, show=.60),
}

# ---------- данные ----------


def load_rounds():
    """Матчи регулярки 25/26 по неделям Пн–Вс. Возвращает [(номер, [матчи])]."""
    games = json.load(open(ROOT / 'history.json'))['games']
    g = [x for x in games if x['season'] == '25/26' and x['stage'] == 'regular' and x.get('score')]
    g.sort(key=lambda x: x['date'])
    weeks = {}
    for x in g:
        d = date.fromisoformat(x['date'])
        weeks.setdefault(d - timedelta(days=d.weekday()), []).append(x)
    return [(i + 1, weeks[k]) for i, k in enumerate(sorted(weeks))]


def elo_probs(rounds):
    """Вероятность победы хозяев до матча. Рейтинг обновляется по ходу сезона, без подглядывания."""
    R, K, H = {}, 20.0, 50.0
    for _, ms in rounds:
        for m in ms:
            a, b = m['home'], m['away']
            ra, rb = R.get(a, 1500.0), R.get(b, 1500.0)
            p = 1.0 / (1.0 + 10 ** (-(ra + H - rb) / 400))
            m['p'] = p
            m['home_win'] = m['score'][0] > m['score'][1]
            s = 1.0 if m['home_win'] else 0.0
            R[a], R[b] = ra + K * (s - p), rb + K * (p - s)
    return rounds


# ---------- менеджеры ----------

class Man:
    __slots__ = ('arch', 'fav', 'eps', 'bias', 'crowd', 'show', 'pts', 'rp', 'join', 'quit')


def make_managers(cfg, clubs, rng):
    n, ms = cfg['managers'], []
    arches = []
    for a, d in ARCH.items():
        arches += [a] * int(round(d['share'] * n))
    while len(arches) < n:
        arches.append('active')
    big = clubs[0] if cfg['skew_club'] else None
    for i, a in enumerate(arches):
        m = Man()
        d = ARCH[a]
        m.arch = a
        if big and rng.random() < cfg['skew_club']:
            m.fav = big
        else:
            m.fav = rng.choice(clubs)
        m.eps, m.bias = d['eps'], d['bias']
        m.crowd = 1.0 if cfg.get('all_expert') else d['crowd']
        if cfg.get('all_expert'):
            m.eps, m.show = .03, .98
        else:
            m.show = d['show']
        m.pts, m.rp = 0.0, []
        m.join = 1 if a != 'late' else rng.choice([5, 6, 7, 8, 12])
        m.quit = 99 if a != 'quitter' else rng.choice([8, 10, 12, 14])
        ms.append(m)
    return ms


def perceive(m, mt, rng):
    """Как менеджер видит матч: вероятность с шумом плюс любовь к своему клубу."""
    p = min(.97, max(.03, mt['p'] + rng.gauss(0, m.eps)))
    if m.fav == mt['home']:
        p = min(.97, p + m.bias)
    elif m.fav == mt['away']:
        p = max(.03, p - m.bias)
    return p


def share_model(hist, p):
    """Какую долю толпы ждать на стороне с вероятностью p. Берётся из прошлого раунда (он опубликован)."""
    if not hist:
        return min(.95, max(.05, p))
    b = min(9, int(p * 10))
    v = hist[b]
    return v if v is not None else min(.95, max(.05, p))


def allocate(m, ms_round, cfg, rng, hist, total):
    """Куда давить силы. Жадно, по предельной выгоде следующей силы."""
    U, cap = cfg['forces'], cfg['cap']
    seen = [perceive(m, mt, rng) for mt in ms_round]
    # ожидаемая толпа на стороне: доля из прошлого раунда, сглаженная к «не знаю» по m.crowd
    crowd = []
    for p in seen:
        sh = share_model(hist, p)
        sh = m.crowd * sh + (1 - m.crowd) * .5
        crowd.append((max(.02, sh) * total, max(.02, 1 - sh) * total))
    mine = {}
    for _ in range(U):
        best, bestv = None, -1.0
        for i, p in enumerate(seen):
            for side, pr in ((0, p), (1, 1 - p)):
                u = mine.get((i, side), 0)
                if u >= cap:
                    continue
                c = max(1.0, crowd[i][side])
                if cfg.get('scoring') == 'pot':
                    v = pr * cfg['pot'] * ((u + 1) / (c + u + 1) - u / (c + u) if u else 1 / (c + 1))
                else:
                    v = pr * (cfg['base'] + cfg['rare'] * (1 - min(1.0, (c + u) / total)))
                if v > bestv:
                    best, bestv = (i, side), v
        if best is None:
            break
        mine[best] = mine.get(best, 0) + 1
    return mine


def autopilot(m, ms_round, cfg):
    """Публичное правило для тех, кто не пришёл: силы делятся между матчами своего клуба и фаворитами."""
    U = cfg['forces']
    mine, left = {}, U
    own = [i for i, mt in enumerate(ms_round) if m.fav in (mt['home'], mt['away'])]
    for i in own:
        if left <= 0:
            break
        side = 0 if ms_round[i]['home'] == m.fav else 1
        k = min(cfg['cap'], left)
        mine[(i, side)] = k
        left -= k
    order = sorted(range(len(ms_round)), key=lambda i: -max(ms_round[i]['p'], 1 - ms_round[i]['p']))
    for i in order:
        if left <= 0:
            break
        side = 0 if ms_round[i]['p'] >= .5 else 1
        if mine.get((i, side), 0) >= cfg['cap']:
            continue
        mine[(i, side)] = mine.get((i, side), 0) + 1
        left -= 1
    return mine


# ---------- один сезон ----------


def season(cfg, rounds, rng):
    clubs = sorted({x for _, ms in rounds for m in ms for x in (m['home'], m['away'])})
    ms = make_managers(cfg, clubs, rng)
    fans = {c: sum(1 for m in ms if m.fav == c) for c in clubs}
    owner = {c: c for c in clubs}          # зона города — у его клуба, старт равный
    flips, holds, zone_hist, sim_pairs = 0, 0, [], []
    hist = None                            # доли толпы прошлого раунда по вероятности, публикуются

    for rnum, matches in rounds:
        allocs = []
        total = cfg['managers'] * cfg['forces'] / max(1, len(matches))
        for m in ms:
            if rnum < m.join or rnum >= m.quit:
                allocs.append(None)
                continue
            if rng.random() < m.show:
                allocs.append(allocate(m, matches, cfg, rng, hist, total))
            elif cfg['autopilot']:
                allocs.append(autopilot(m, matches, cfg))
            else:
                allocs.append(None)
        crowd = {}
        for a in allocs:
            if not a:
                continue
            for k, u in a.items():
                crowd[k] = crowd.get(k, 0) + u

        # зоны: победитель матча берёт зону города, если его сторона давила не слабее
        took = {}
        for i, mt in enumerate(matches):
            zone, win_side = mt['home'], (0 if mt['home_win'] else 1)
            att = mt['away'] if win_side else mt['home']
            press, anti = {}, {}
            for m, a in zip(ms, allocs):
                if not a:
                    continue
                if a.get((i, win_side), 0):
                    press[m.fav] = press.get(m.fav, 0) + a[(i, win_side)]
                if a.get((i, 1 - win_side), 0):
                    anti[m.fav] = anti.get(m.fav, 0) + a[(i, 1 - win_side)]
            if cfg['per_capita']:
                press = {c: v / max(1, fans[c]) for c, v in press.items()}
                anti = {c: v / max(1, fans[c]) for c, v in anti.items()}
            strong = sum(press.values()) >= sum(anti.values())
            if win_side == 1 and strong and owner[zone] != att:
                owner[zone] = att
                flips += 1
                took[i] = win_side
            else:
                holds += 1
                if win_side == 0 and strong:
                    took[i] = win_side
        zone_hist.append(dict(owner))

        # очки: знание результата, зона и редкость
        for m, a in zip(ms, allocs):
            got = 0.0
            if a:
                for (i, side), u in a.items():
                    if side != (0 if matches[i]['home_win'] else 1):
                        continue
                    tot = crowd.get((i, 0), 0) + crowd.get((i, 1), 0)
                    if cfg.get('scoring') == 'pot':
                        got += cfg['pot'] * u / crowd[(i, side)]
                        continue
                    sh = crowd[(i, side)] / max(1, tot)
                    got += u * (cfg['base'] + cfg['rare'] * (1 - sh))
                    if took.get(i) == side:
                        got += u * cfg['zone']
            m.pts += got
            m.rp.append(got)

        # сходство расстановок активных: шаблон или нет
        act = [a for m, a in zip(ms, allocs) if a and m.arch in ('active', 'optimizer', 'form')]
        for _ in range(min(200, len(act) * 2)):
            x, y = rng.choice(act), rng.choice(act)
            if x is y:
                continue
            sx, sy = set(x), set(y)
            sim_pairs.append(len(sx & sy) / len(sx | sy))

        buckets = [[] for _ in range(10)]
        for i, mt in enumerate(matches):
            tot = crowd.get((i, 0), 0) + crowd.get((i, 1), 0)
            if not tot:
                continue
            for side, p in ((0, mt['p']), (1, 1 - mt['p'])):
                buckets[min(9, int(p * 10))].append(crowd.get((i, side), 0) / tot)
        hist = [st.mean(b) if b else None for b in buckets]

    return ms, fans, owner, flips, holds, zone_hist, len(rounds), st.mean(sim_pairs) if sim_pairs else 0


# ---------- метрики ----------


def spearman(a, b):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: -v[i])
        r = [0] * len(v)
        for pos, i in enumerate(order):
            r[i] = pos + 1
        return r
    ra, rb = rank(a), rank(b)
    n = len(a)
    ma, mb = (n + 1) / 2, (n + 1) / 2
    num = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n))
    den = (sum((x - ma) ** 2 for x in ra) * sum((x - mb) ** 2 for x in rb)) ** .5
    return num / den if den else 0.0


def metrics(cfg, res):
    ms, fans, owner, flips, holds, zone_hist, nr, sim = res
    out = {}
    by = {}
    for m in ms:
        by.setdefault(m.arch, []).append(m.pts)
    cas = st.mean(by['casual'])
    for a in ('optimizer', 'active', 'form', 'fan', 'parent', 'casual'):
        out['idx_' + a] = 100 * st.mean(by[a]) / cas if cas else 0

    # застывание: место после раунда N против финального, только те, кто играл с начала
    full = [m for m in ms if m.join == 1 and m.quit == 99]
    k = cfg['rounds_metric']
    out['freeze'] = spearman([sum(m.rp[:k]) for m in full], [m.pts for m in full])
    out['similar'] = sim
    early = sorted(full, key=lambda m: -sum(m.rp[:k]))
    bottom = early[len(early) // 2:]
    fin = sorted(full, key=lambda m: -m.pts)
    top10 = set(id(x) for x in fin[:max(1, len(fin) // 10)])
    out['rise'] = 100 * sum(1 for m in bottom if id(m) in top10) / max(1, len(bottom))

    # азарт: доля менеджеров, бравших хоть один раунд в своей ступени (группа 20 по уровню)
    groups = []
    order = sorted(full, key=lambda m: -m.pts)
    for i in range(0, len(order), 20):
        groups.append(order[i:i + 20])
    wins = {a: [0, 0] for a in ARCH}
    live = [0, 0]
    ever = {a: [0, 0] for a in ARCH}
    for g in groups:
        if len(g) < 5:
            continue
        hit = {id(m): False for m in g}
        for r in range(nr):
            vals = [m.rp[r] if r < len(m.rp) else 0 for m in g]
            top = max(vals)
            for m, v in zip(g, vals):
                wins[m.arch][1] += 1
                if v >= top - 1e-9 and top > 0:
                    wins[m.arch][0] += 1
                    hit[id(m)] = True
        for m in g:
            ever[m.arch][1] += 1
            ever[m.arch][0] += 1 if hit[id(m)] else 0
        # живая цель: после каждого раунда — дотягивается ли до 4-го места месяца (4 раунда)
        for r in range(nr):
            mstart = (r // 4) * 4
            sofar = [sum(m.rp[mstart:r + 1]) for m in g]
            left = min(4, nr - r - 1)
            strong = st.quantiles([v for m in g for v in m.rp[:r + 1]], n=10)[8] if r else st.mean([m.rp[0] for m in g])
            need = sorted(sofar, reverse=True)[3] if len(sofar) > 3 else max(sofar)
            for m, v in zip(g, sofar):
                live[1] += 1
                if v >= need or v + strong * left >= need:
                    live[0] += 1
    out['round_win_casual'] = 100 * wins['casual'][0] / max(1, wins['casual'][1])
    out['round_win_opt'] = 100 * wins['optimizer'][0] / max(1, wins['optimizer'][1])
    out['ever_casual'] = 100 * ever['casual'][0] / max(1, ever['casual'][1])
    out['ever_opt'] = 100 * ever['optimizer'][0] / max(1, ever['optimizer'][1])
    out['live'] = 100 * live[0] / max(1, live[1])

    # карта
    out['flips_per_round'] = flips / nr
    top3 = sorted(fans, key=lambda c: -fans[c])[:3]
    out['top3_zones'] = 100 * sum(1 for z, c in owner.items() if c in top3) / len(owner)
    out['own_zone_kept'] = 100 * sum(1 for z, c in owner.items() if z == c) / len(owner)
    changed = len({tuple(sorted(h.items())) for h in zone_hist})
    out['map_states'] = changed
    return out


def run(name, n=20, seed=1):
    cfg = dict(BASE)
    cfg.update(VARIANTS[name])
    rounds = elo_probs(load_rounds())
    acc = {}
    for s in range(n):
        rng = random.Random(seed * 1000 + s)
        mm = metrics(cfg, season(cfg, rounds, rng))
        for k, v in mm.items():
            acc.setdefault(k, []).append(v)
    return {k: st.mean(v) for k, v in acc.items()}


LABEL = [
    ('idx_optimizer', 'Очки знатока (казуальный = 100)'),
    ('idx_active', 'Очки активного'),
    ('idx_form', 'Очки «по форме»'),
    ('idx_fan', 'Очки фаната своего клуба'),
    ('idx_parent', 'Очки родителя'),
    ('freeze', 'Застывание: место после 7 раундов → финал'),
    ('rise', 'Из нижней половины в топ-10% к финалу, %'),
    ('similar', 'Сходство расстановок активных (Жаккар)'),
    ('live', 'Живая цель в своей ступени, %'),
    ('ever_casual', 'Казуальный хоть раз взял раунд в группе, %'),
    ('ever_opt', 'Знаток хоть раз взял раунд в группе, %'),
    ('flips_per_round', 'Захватов зон за раунд'),
    ('own_zone_kept', 'Зон осталось у своих клубов, %'),
    ('top3_zones', 'Зон у трёх самых больших фанбаз, %'),
]

if __name__ == '__main__':
    args = sys.argv[1:]
    names = list(VARIANTS) if (args and args[0] == 'all') else [args[0] if args else 'base']
    n = int(args[1]) if len(args) > 1 else 20
    rs = {v: run(v, n) for v in names}
    w = max(len(t) for _, t in LABEL)
    print('Матчей по неделям: регулярка НМХЛ 25/26. Прогонов:', n)
    print(' ' * w, '  '.join(f'{v:>10}' for v in names))
    for k, t in LABEL:
        print(f'{t:<{w}}', '  '.join(f'{rs[v][k]:>10.2f}' for v in names))

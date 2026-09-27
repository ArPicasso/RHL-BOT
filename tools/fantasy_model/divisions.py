"""Дивизионы: лестница Экономиста (4 вверх / 4 вниз) против ступеней Геймдизайнера (пересбор по очкам месяца).
Считается поверх очков из results/<variant>.pkl, очки от дивизионов не зависят.
python3 divisions.py base base600"""
import pickle, sys, math, collections
import numpy as np
T = 21
MONTHS = [(1, 3), (4, 8), (9, 11), (12, 14), (15, 18), (19, 21)]
def month_of(t):
    for i, (a, b) in enumerate(MONTHS):
        if a <= t <= b: return i
def phi(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
G = 20
DRIFT = True

def run_season(r, scheme, start_month, live_p=0.10):
    ms = [x for x in r['managers'] if not x['twin']]
    byid = {x['id']: x for x in ms}
    N = len(ms)
    sd_t = np.median([np.std([x['pts'][t] for x in ms if t in x['pts']]) for t in range(1, T + 1)])
    sp = sd_t * math.sqrt(2)
    mu = {x['id']: np.mean(list(x['pts'].values())) for x in ms}
    resid = []
    for x in ms:
        v = np.array(list(x['pts'].values()), float)
        if len(v) > 3: resid += list(v - v.mean())
    sdw = float(np.std(resid))
    def mpts(mid, mo):
        a, b = MONTHS[mo]; return sum(v for tt, v in byid[mid]['pts'].items() if a <= tt <= b)
    caps = [20, 40, 80] + ([160] if N > 400 else [])
    nsteps = len(caps) + 1          # последняя — Коробка
    step = {}; group = {}           # mid -> ступень (0 — Высшая), mid -> id группы
    hist = collections.defaultdict(list)   # mid -> [(месяц, ступень)]
    clubs = collections.defaultdict(list)
    for x in ms: clubs[x['fav']].append(x['id'])
    live = collections.defaultdict(list)
    prev_top4 = set()
    def cut_groups(members, key):
        """Нарезать по уровню группами по 20; хвост < 10 доливается в предыдущую группу."""
        members = sorted(members, key=key)
        gs = [members[i:i + G] for i in range(0, len(members), G)]
        if len(gs) > 1 and len(gs[-1]) < 10: last = gs.pop(); gs[-1] += last
        return gs
    gid = [0]
    def assign(gs, st):
        for g in gs:
            gid[0] += 1
            for mid in g: step[mid] = st; group[mid] = gid[0]
    for t in range(1, T + 1):
        mo = month_of(t); a_, b_ = MONTHS[mo]
        cur = [x['id'] for x in ms if x['join'] <= t]
        if t == a_ and mo >= start_month:
            if mo == start_month or scheme == 'resort':
                # первичный сбор (обе схемы) или ежемесячный пересбор (ступени)
                if mo == start_month:
                    score = {mid: sum(v for tt, v in byid[mid]['pts'].items() if tt < t) for mid in cur}
                else:
                    score = {mid: mpts(mid, mo - 1) if byid[mid]['join'] <= MONTHS[mo - 1][0] else -1e9 + mpts(mid, mo - 1) for mid in cur}
                order = sorted(cur, key=lambda m: -score[m])
                if scheme == 'ladder':
                    step.clear(); group.clear()
                    gs = [order[i:i + G] for i in range(0, len(order), G)]
                    if len(gs) > 1 and len(gs[-1]) < 10: last = gs.pop(); gs[-1] += last
                    for d, g in enumerate(gs):
                        for mid in g: step[mid] = d; group[mid] = d
                else:
                    want = {}
                    pos = 0
                    for s_, c in enumerate(caps):
                        for mid in order[pos:pos + c]: want[mid] = s_
                        pos += c
                    for mid in order[pos:]: want[mid] = nsteps - 1
                    for mid in prev_top4:          # страховка: четвёрка группы поднимается минимум на ступень
                        if mid in step and mid in want: want[mid] = min(want[mid], max(0, step[mid] - 1))
                    step.clear(); group.clear()
                    for s_ in range(nsteps):
                        mem = [m for m in order if want[m] == s_]
                        assign(cut_groups(mem, lambda m: -score[m]), s_)
            else:
                # лестница: 4 вверх, 4 вниз по очкам прошлого месяца
                groups = collections.defaultdict(list)
                for mid, d in group.items(): groups[d].append(mid)
                nd = max(groups) + 1; new = dict(group)
                for d, g in groups.items():
                    g.sort(key=lambda m: -mpts(m, mo - 1))
                    for m in g[:4]: new[m] = max(0, d - 1)
                    for m in g[-4:]: new[m] = min(nd - 1, d + 1)
                group.clear(); group.update(new); step.clear(); step.update(new)
        if step:
            # новички месяца — вниз
            new_ = [mid for mid in cur if mid not in step]
            if new_:
                if scheme == 'ladder':
                    bottom = max(step.values())
                    for mid in new_: step[mid] = bottom; group[mid] = bottom
                else:
                    gid[0] += 1
                    for mid in new_: step[mid] = nsteps - 1; group[mid] = gid[0]
            if t == a_:
                for mid in cur: hist[mid].append((mo, step[mid]))
        # живые цели — с поправкой Критика: дрейф по собственному уровню менеджера и внутриличный шум
        Rall = T - t + 1; Rm = b_ - t + 1
        cumt = {mid: sum(v for tt, v in byid[mid]['pts'].items() if tt < t) for mid in cur}
        mt = {mid: sum(v for tt, v in byid[mid]['pts'].items() if a_ <= tt < t) for mid in cur}
        def pc_group(ids, base, k, R):
            """Шанс каждого из ids войти в топ-k группы ids по base + будущим турам."""
            srt = sorted(ids, key=lambda m: -base[m]); pos = {m: i for i, m in enumerate(srt)}
            out_ = {}
            for me in ids:
                if len(srt) <= k: out_[me] = 1.0; continue
                thr = srt[k] if pos[me] < k else srt[k - 1]
                gap = base[thr] - base[me]
                if R <= 0: out_[me] = float(gap <= 0); continue
                d = (mu[thr] - mu[me]) * R if DRIFT else 0.0
                sgm = (sdw * math.sqrt(2 * R)) if DRIFT else (sp * math.sqrt(R))
                out_[me] = 1 - phi((gap + d) / sgm)
            return out_
        st1 = [mid for mid in cur if byid[mid]['join'] == 1]
        po = pc_group(st1, cumt, max(1, int(0.1 * len(st1))), Rall)
        pm = pc_group(cur, mt, max(1, int(0.1 * len(cur))), Rm)
        pcl = {}
        for c, l in clubs.items():
            l2 = [i for i in l if i in cumt]
            if l2: pcl.update(pc_group(l2, cumt, 3, Rall))
        pg = {}
        if step:
            gg = collections.defaultdict(list)
            for mid in cur: gg[group[mid]].append(mid)
            for g, l in gg.items(): pg.update(pc_group(l, mt, 4, Rm))
        flags = {}
        for mid in cur:
            f = po.get(mid, 0) >= live_p or pm[mid] >= live_p or pcl[mid] >= live_p
            fd = bool(step) and pg[mid] >= live_p
            flags[mid] = (f, f or fd, fd)
        for nm, sel in (('all', cur), ('cas', [m for m in cur if byid[m]['arch'] in ('casual', 'parent')]),
                        ('late', [m for m in cur if byid[m]['arch'] in ('late_nov', 'late_jan')])):
            if sel and t >= 4:
                live[nm + '_mvp'].append(np.mean([flags[m][0] for m in sel]))
                live[nm + '_any'].append(np.mean([flags[m][1] for m in sel]))
                live[nm + '_div'].append(np.mean([flags[m][2] for m in sel]))
        # топ-4 группы — к следующему месяцу (для страховки ступеней)
        if t == b_ and step:
            prev_top4 = set()
            gg2 = collections.defaultdict(list)
            for mid in cur: gg2[group[mid]].append(mid)
            for g, l in gg2.items():
                l.sort(key=lambda m: -mpts(m, mo)); prev_top4 |= set(l[:4])
    out = {k: float(np.mean(v)) for k, v in live.items()}
    # застрявшие внизу: все месяцы дивизионов — в нижней половине (Коробка / нижняя половина лестницы)
    started = [x['id'] for x in ms if x['join'] == 1]
    if scheme == 'ladder':
        nd = max(v for l in hist.values() for _, v in l) + 1
        low = lambda s_: s_ >= nd / 2
    else:
        low = lambda s_: s_ == nsteps - 1
    def stuck(mid): return bool(hist[mid]) and all(low(s_) for _, s_ in hist[mid])
    out['stuck_all'] = float(np.mean([stuck(m) for m in started]))
    for a in ('casual', 'parent', 'active', 'optimizer', 'form'):
        g = [m for m in started if byid[m]['arch'] == a]
        out['stuck_' + a] = float(np.mean([stuck(m) for m in g]))
        out['top_ever_' + a] = float(np.mean([any(s_ == 0 for _, s_ in hist[m]) for m in g]))
    # сильный из Коробки: оптимизатор или охотник, оказавшийся внизу, — сколько месяцев до Высшей
    waits = []; never = 0; n_ = 0
    for m in started + [x['id'] for x in ms if x['arch'] in ('late_nov', 'late_jan')]:
        if byid[m]['arch'] not in ('optimizer', 'form', 'late_nov', 'late_jan'): continue
        h = hist[m]
        k = next((i for i, (_, s_) in enumerate(h) if low(s_)), None)
        if k is None: continue
        n_ += 1
        j = next((i for i in range(k + 1, len(h)) if h[i][1] == 0), None)
        if j is None: never += 1
        else: waits.append(j - k)
    out['strong_low_n'] = n_; out['strong_reach_top'] = (len(waits) / n_) if n_ else float('nan')
    out['strong_wait'] = float(np.mean(waits)) if waits else float('nan')
    out['nsteps_or_divs'] = (max(v for l in hist.values() for _, v in l) + 1) if hist else 0
    # вероятность оптимизатора попасть в топ-20 месяца (ступени: прыжок из Коробки в Высшую за месяц)
    p = []
    for mo in range(1, 6):
        cm = [x['id'] for x in ms if x['join'] <= MONTHS[mo][0]]
        o = sorted(cm, key=lambda m: -mpts(m, mo))[:20]
        p.append(np.mean([byid[m]['arch'] == 'optimizer' for m in o]))
    opt_n = sum(1 for x in ms if x['arch'] == 'optimizer')
    out['p_opt_top20_month'] = float(np.mean(p) * 20 / opt_n)
    return out

if __name__ == '__main__':
    for v in sys.argv[1:]:
        res = pickle.load(open(f'results/{v}.pkl', 'rb'))
        n = len([x for x in res[0]['managers'] if not x['twin']])
        print(f'===== {v}: {n} менеджеров, {len(res)} сезонов')
        for scheme, sm, lab in (('ladder', 1, 'лестница, с ноября'), ('resort', 1, 'ступени, с ноября'), ('resort', 2, 'ступени, с декабря (отбор)')):
            rows = [run_season(r, scheme, sm) for r in res]
            keys = rows[0].keys()
            agg = {k: np.nanmedian([r_[k] for r_ in rows]) for k in keys}
            print(f'-- {lab}')
            print('   ', ', '.join(f'{k}={agg[k]:.3f}' for k in keys))
            pickle.dump(rows, open(f'results/div_{v}_{scheme}{sm}.pkl', 'wb'))

"""Сводка по вариантам: скаляры (распределения по сезонам) и ряды по турам (медиана, p10, p90 по сезонам)."""
import pickle, sys, math, json, collections, random
import numpy as np
T = 21
MONTHS = [(1, 3), (4, 8), (9, 11), (12, 14), (15, 18), (19, 21)]
def month_of(t):
    for i, (a, b) in enumerate(MONTHS):
        if a <= t <= b: return i
ACT = ('active', 'optimizer', 'form', 'late_nov', 'late_jan')

def phi(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def per_seed(r, div_size=20, live_p=0.10):
    out = {}
    ms = [x for x in r['managers'] if not x['twin']]
    base = {x['id']: x for x in ms}
    tot = {x['id']: sum(x['pts'].values()) for x in ms}
    # --- архетипы
    for a in set(x['arch'] for x in ms):
        g = [x for x in ms if x['arch'] == a]
        out['season_' + a] = float(np.mean([tot[x['id']] for x in g]))
        out['pertour_' + a] = float(np.mean([tot[x['id']] / max(1, len(x['pts'])) for x in g]))
        out['value_' + a] = float(np.median([x['value'] / 1000 - 100 for x in g]))       # % к 100 000
        out['paid_' + a] = float(np.mean([x['paid'] for x in g]))
        out['swaps_' + a] = float(np.mean([x['swaps'] for x in g]))
        out['fees_' + a] = float(np.mean([x['fees'] for x in g]))
        out['auto_' + a] = float(np.mean([x['auto'] for x in g]))
        out['missions_' + a] = float(np.mean([x['missions'] for x in g]))
    cas = out['pertour_casual']
    for a in ('parent', 'active', 'optimizer', 'form', 'late_nov', 'late_jan', 'quitter'):
        out['gap_' + a] = out['pertour_' + a] / cas - 1
    # --- близнецы
    tw = collections.defaultdict(list)
    for x in r['managers']:
        if x['twin']:
            b = base[x['id']]
            tw[x['twin']].append((sum(x['pts'].values()), sum(b['pts'].values())))
    if 'blind' in tw: out['calendar_weight'] = float(np.mean([(b - x) / x for x, b in tw['blind']]))
    if 'frozen' in tw: out['transfer_value'] = float(np.mean([(b - x) / x for x, b in tw['frozen']]))
    if 'noknow' in tw: out['knowledge_value'] = float(np.mean([(b - x) / x for x, b in tw['noknow']]))
    for k in ('ice1_opt', 'ice1_act', 'ice8_opt', 'ice8_act', 'ice14_opt', 'ice14_act'):
        if k in tw: out['pts_per_1000_' + k] = float(np.mean([(x - b) / 5 for x, b in tw[k]]))
    # --- подвижность таблицы (стартовавшие в туре 1)
    st = [x for x in ms if x['join'] == 1]
    def cum(x, t): return sum(v for tt, v in x['pts'].items() if tt <= t)
    n = len(st)
    r7 = sorted(st, key=lambda x: -cum(x, 7)); pos7 = {x['id']: i for i, x in enumerate(r7)}
    rF = sorted(st, key=lambda x: -cum(x, T)); posF = {x['id']: i for i, x in enumerate(rF)}
    bottom = [x for x in st if pos7[x['id']] >= n / 2]
    out['mob_bottom_to_top10'] = float(np.mean([posF[x['id']] < 0.1 * n for x in bottom]))
    out['mob_bottom_to_tophalf'] = float(np.mean([posF[x['id']] < 0.5 * n for x in bottom]))
    ba = [x for x in bottom if x['arch'] in ACT]
    out['mob_bottom_to_top10_active'] = float(np.mean([posF[x['id']] < 0.1 * n for x in ba])) if ba else float('nan')
    # ранговая корреляция t7 и финала
    a7 = np.array([pos7[x['id']] for x in st]); aF = np.array([posF[x['id']] for x in st])
    out['rank_corr_t7_final'] = float(np.corrcoef(a7, aF)[0, 1])
    for tt in (3, 11, 14):
        rt = sorted(st, key=lambda x: -cum(x, tt)); pt = {x['id']: i for i, x in enumerate(rt)}
        out['rank_corr_t%d_final' % tt] = float(np.corrcoef([pt[x['id']] for x in st], aF)[0, 1])
    # доля топ-10% по архетипам
    top = [x for x in st if posF[x['id']] < 0.1 * n]
    for a in ('casual', 'parent', 'active', 'optimizer', 'form', 'quitter'):
        out['top10share_' + a] = float(np.mean([x['arch'] == a for x in top]))
    # опоздавшие: перцентиль в общем зачёте и «с момента вступления»
    allm = ms; totF = {x['id']: tot[x['id']] for x in allm}
    for a, J in (('late_nov', 5), ('late_jan', 12)):
        g = [x for x in allm if x['arch'] == a]
        if not g: continue
        allv = sorted(totF.values(), reverse=True)
        out['late_pct_overall_' + a] = float(np.mean([sum(v > totF[x['id']] for v in allv) / len(allv) for x in g]))
        since = {x['id']: sum(v for tt, v in x['pts'].items() if tt >= J) for x in allm}
        sv = sorted(since.values(), reverse=True)
        out['late_pct_since_' + a] = float(np.mean([sum(v > since[x['id']] for v in sv) / len(sv) for x in g]))
        out['late_top10_since_' + a] = float(np.mean([sum(v > since[x['id']] for v in sv) / len(sv) < 0.1 for x in g]))
    # --- «живая цель» по турам: общий топ-10%, месяц топ-10%, дивизион (топ-4 из 20 за месяц)
    sd_t = np.median([np.std([x['pts'][t] for x in ms if t in x['pts']]) for t in range(1, T + 1)])
    sp = sd_t * math.sqrt(2)
    rnd = random.Random(r['seed'])
    ids = [x['id'] for x in st]; rnd.shuffle(ids)
    ndiv = max(1, len(ids) // div_size)
    div = {mid: i % ndiv for i, mid in enumerate(ids)}
    byid = {x['id']: x for x in ms}
    live = {k: [] for k in ('overall', 'month', 'division', 'any_base', 'any_div', 'club', 'seeded', 'any_seeded', 'any_club', 'late_any_club', 'late_any_seeded', 'cas_any_club', 'cas_any_seeded')}
    clubs = collections.defaultdict(list)
    for x in ms: clubs[x['fav']].append(x['id'])
    sdiv = None
    for t in range(1, T + 1):
        mo = month_of(t); a_, b_ = MONTHS[mo]
        cur = [x for x in ms if x['join'] <= t]
        if t == a_ and t > 1:
            # повышение/вылет по итогам прошлого месяца
            pa, pb = MONTHS[mo - 1]
            groups = collections.defaultdict(list)
            for mid, d in div.items(): groups[d].append(mid)
            newdiv = dict(div)
            for d, g in groups.items():
                g.sort(key=lambda m: -sum(v for tt, v in byid[m]['pts'].items() if pa <= tt <= pb))
                for m in g[:4]: newdiv[m] = max(0, d - 1)
                for m in g[-4:]: newdiv[m] = min(ndiv - 1, d + 1)
            div = newdiv
            for x in cur:
                if x['id'] not in div: div[x['id']] = ndiv - 1
        for x in cur:
            if x['id'] not in div: div[x['id']] = ndiv - 1
        # дивизионы по уровню: с ноября (тур 4) по очкам октября, дальше 4 вверх / 4 вниз каждый месяц
        if t == 4:
            order = sorted([x for x in cur], key=lambda x: -sum(v for tt, v in x['pts'].items() if tt <= 3))
            sdiv = {x['id']: i // div_size for i, x in enumerate(order)}
        elif sdiv is not None and t == a_:
            pa, pb = MONTHS[mo - 1]; nd = max(sdiv.values()) + 1
            groups = collections.defaultdict(list)
            for mid, d in sdiv.items(): groups[d].append(mid)
            nsd = dict(sdiv)
            for d, g in groups.items():
                g.sort(key=lambda m: -sum(v for tt, v in byid[m]['pts'].items() if pa <= tt <= pb))
                for m in g[:4]: nsd[m] = max(0, d - 1)
                for m in g[-4:]: nsd[m] = min(nd - 1, d + 1)
            sdiv = nsd
        if sdiv is not None:
            for x in cur:
                if x['id'] not in sdiv: sdiv[x['id']] = max(sdiv.values())
        Rall = T - t + 1; Rm = b_ - t + 1
        cumt = {x['id']: sum(v for tt, v in x['pts'].items() if tt < t) for x in cur}
        mt = {x['id']: sum(v for tt, v in x['pts'].items() if a_ <= tt < t) for x in cur}
        def pthr(vals, frac):
            s = sorted(vals, reverse=True); return s[max(0, int(frac * len(s)) - 1)]
        thr_all = pthr([cumt[x['id']] for x in st if x['join'] <= t], 0.1)
        thr_m = pthr(list(mt.values()), 0.1)
        groups = collections.defaultdict(list)
        for x in cur: groups[div[x['id']]].append(mt[x['id']])
        thr_d = {d: sorted(v, reverse=True)[min(3, len(v) - 1)] for d, v in groups.items()}
        lo = lambda gap, R: 1 - phi(gap / (sp * math.sqrt(R))) if R > 0 else float(gap <= 0)
        if sdiv is not None:
            sg = collections.defaultdict(list)
            for x in cur: sg[sdiv[x['id']]].append(mt[x['id']])
            thr_s = {d: sorted(v, reverse=True)[min(3, len(v) - 1)] for d, v in sg.items()}
        thr_c = {}
        for c, l in clubs.items():
            v = sorted([cumt[i] for i in l if i in cumt], reverse=True)
            if v: thr_c[c] = v[min(2, len(v) - 1)]
        o_ = []; m_ = []; d_ = []; c_ = []; s_ = []
        for x in cur:
            po = lo(thr_all - cumt[x['id']], Rall) if x['join'] == 1 else 0.0
            pm = lo(thr_m - mt[x['id']], Rm)
            pd = lo(thr_d[div[x['id']]] - mt[x['id']], Rm)
            pc = lo(thr_c[x['fav']] - cumt[x['id']], Rall)
            ps_ = lo(thr_s[sdiv[x['id']]] - mt[x['id']], Rm) if sdiv is not None else pd
            o_.append(po >= live_p); m_.append(pm >= live_p); d_.append(pd >= live_p); c_.append(pc >= live_p); s_.append(ps_ >= live_p)
        live['overall'].append(float(np.mean(o_))); live['month'].append(float(np.mean(m_)))
        live['division'].append(float(np.mean(d_)))
        live['any_base'].append(float(np.mean([a or b for a, b in zip(o_, m_)])))
        live['any_div'].append(float(np.mean([a or b or c for a, b, c in zip(o_, m_, d_)])))
        li = [i for i, x in enumerate(cur) if x['arch'] in ('late_nov', 'late_jan')]
        ci = [i for i, x in enumerate(cur) if x['arch'] in ('casual', 'parent')]
        for nm, idx in (('late', li), ('cas', ci)):
            live[nm + '_any_club'].append(float(np.mean([o_[i] or m_[i] or c_[i] for i in idx])) if idx else float('nan'))
            live[nm + '_any_seeded'].append(float(np.mean([o_[i] or m_[i] or c_[i] or s_[i] for i in idx])) if idx else float('nan'))
        live['any_club'].append(float(np.mean([a or b or c for a, b, c in zip(o_, m_, c_)])))
        live['club'].append(float(np.mean(c_))); live['seeded'].append(float(np.mean(s_)))
        live['any_seeded'].append(float(np.mean([a or b or c or d for a, b, c, d in zip(o_, m_, c_, s_)])))
    # доля менеджеров, у которых в последний тур месяца ещё жива цель в месяце (среднее по месяцам)
    out['series_live'] = live
    out['sd_tour'] = float(sd_t)
    return out

SERIES_KEYS = ['real_pos', 'dens_free', 'dens_hit', 'own_free', 'own_hit', 'own_best_optimizer', 'own_best_active', 'best_gain',
               'real_gain', 'oracle_gain', 'budget_binds', 'budget_cost', 'jaccard_active', 'jaccard_opt', 'jaccard_all',
               'template_share', 'top10_own_active', 'price_changed', 'price_absmove', 'price_fall300', 'mispriced_1',
               'mispriced_err', 'arb_top20', 'carry300', 'price_future_corr', 'ft_hoard_active',
               'bank_active', 'bank_optimizer', 'bank_form', 'bank_casual', 'value_active', 'value_optimizer', 'value_form',
               'value_casual', 'value_parent', 'pts_casual', 'pts_parent', 'pts_active', 'pts_optimizer', 'pts_form',
               'ft_active', 'ft_optimizer', 'paid_optimizer', 'paid_form', 'paid_active']

def summarize(v):
    res = pickle.load(open(f'results/{v}.pkl', 'rb'))
    ps = [per_seed(r) for r in res]
    sc = {}
    for k in ps[0]:
        if k.startswith('series_'): continue
        vals = [p[k] for p in ps if k in p and not (isinstance(p[k], float) and math.isnan(p[k]))]
        if vals: sc[k] = dict(med=float(np.median(vals)), p10=float(np.percentile(vals, 10)), p90=float(np.percentile(vals, 90)), mean=float(np.mean(vals)))
    ser = {}
    for k in SERIES_KEYS:
        rows = []
        for r in res:
            m = r['met'].get(k, {})
            rows.append([float(np.mean(m[t])) if (t in m and (not isinstance(m[t], list) or m[t])) else np.nan for t in range(1, T + 1)])
        a = np.array(rows)
        if np.all(np.isnan(a)): continue
        ser[k] = dict(med=np.nanmedian(a, 0).tolist(), p10=np.nanpercentile(a, 10, 0).tolist(), p90=np.nanpercentile(a, 90, 0).tolist())
    for k in ps[0]['series_live']:
        a = np.array([p['series_live'][k] for p in ps])
        ser['live_' + k] = dict(med=np.median(a, 0).tolist(), p10=np.percentile(a, 10, 0).tolist(), p90=np.percentile(a, 90, 0).tolist())
    return dict(scalars=sc, series=ser, n=len(res))

if __name__ == '__main__':
    for v in sys.argv[1:]:
        s = summarize(v)
        pickle.dump(s, open(f'results/{v}_sum.pkl', 'wb'))
        print('=====', v, 'сезонов', s['n'])
        for k, d in sorted(s['scalars'].items()):
            print(f"  {k:32s} {d['med']:10.3f}  [{d['p10']:.3f} … {d['p90']:.3f}]")
        for k, d in s['series'].items():
            print(f"  {k:22s}", ' '.join('%.2f' % x if abs(x) < 100 else '%.0f' % x for x in d['med']))

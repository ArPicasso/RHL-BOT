"""Сводка для 09-economist-check.md: 600 ❄ с тура 1 и задания разных типов против базы."""
import sys, pickle, numpy as np, collections
from analyze import per_seed
T = 21
def calw(r, tours):
    ms = {x['id']: x for x in r['managers'] if not x['twin']}
    v = []
    for x in r['managers']:
        if x['twin'] == 'blind':
            b = ms[x['id']]
            a = sum(b['pts'].get(t, 0) for t in tours); z = sum(x['pts'].get(t, 0) for t in tours)
            v.append((a - z) / z)
    return float(np.mean(v)) if v else float('nan')
def row(v):
    R = pickle.load(open(f'results/{v}.pkl', 'rb'))
    o = collections.defaultdict(list)
    for r in R:
        s = per_seed(r)
        for k in ('calendar_weight', 'gap_active', 'gap_optimizer', 'gap_form', 'pertour_casual', 'value_active', 'value_optimizer', 'value_form',
                  'paid_active', 'paid_optimizer', 'paid_form', 'fees_active', 'fees_optimizer', 'fees_form', 'missions_active', 'missions_optimizer'):
            if k in s: o[k].append(s[k])
        o['cal_last3'].append(calw(r, range(19, 22)))
        met = r['met']
        for a in ('active', 'form', 'optimizer'):
            vv = met.get('value_' + a, {})
            if 12 in vv: o['v27_' + a].append(vv[12] / 1000 - 100)
        for k, key in (('own_free', 'own_free'), ('own_hit', 'own_hit')):
            mm = met.get(key, {})
            o[k + '_8_21'].append(np.mean([np.mean(mm[t]) for t in range(8, 22) if t in mm and mm[t]]))
        mm = met.get('jaccard_active', {}); o['jac_15_21'].append(np.mean([mm[t] for t in range(15, 22) if t in mm]))
        ms = [x for x in r['managers'] if not x['twin']]
        for a in ('optimizer', 'active', 'form'):
            g = [x for x in ms if x['arch'] == a and 'ice_t' in x]
            if g:
                tot = np.mean([sum(x['ice_t'].values()) for x in g]); late = np.mean([sum(c for t, c in x['ice_t'].items() if t >= 19) for x in g])
                o['ice_n_' + a].append(tot); o['ice_late_' + a].append(late)
    return {k: (float(np.median(x)), float(np.percentile(x, 10)), float(np.percentile(x, 90))) for k, x in o.items()}
if __name__ == '__main__':
    V = sys.argv[1:]
    rows = {v: row(v) for v in V}
    keys = sorted(set().union(*[set(r) for r in rows.values()]))
    print('%-22s' % '', ''.join('%22s' % v for v in V))
    for k in keys:
        print('%-22s' % k, ''.join('%22s' % ('%.3f [%.3f..%.3f]' % rows[v][k] if k in rows[v] else '-') for v in V))
    pickle.dump(rows, open('results/check09_' + '_'.join(V) + '.pkl', 'wb'))

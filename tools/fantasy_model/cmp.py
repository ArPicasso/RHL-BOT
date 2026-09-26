import sys, pickle, numpy as np
from analyze import summarize
V = sys.argv[1:]
S = {v: summarize(v) for v in V}
keys = ['calendar_weight','gap_active','gap_form','gap_optimizer','gap_parent','value_active','value_optimizer','value_form','value_casual',
        'paid_optimizer','paid_form','paid_active','swaps_active','swaps_optimizer','fees_optimizer','fees_form','fees_active','missions_active',
        'pts_per_1000_ice8_act','pts_per_1000_ice8_opt','pts_per_1000_ice14_act','transfer_value','knowledge_value','rank_corr_t7_final',
        'mob_bottom_to_tophalf','late_pct_since_late_nov','late_pct_overall_late_nov','pertour_casual','pertour_active','pertour_optimizer']
print('%-28s' % '', ''.join('%18s' % v for v in V))
for k in keys:
    print('%-28s' % k, ''.join('%18s' % ('%.3f [%.2f..%.2f]' % (S[v]['scalars'][k]['med'], S[v]['scalars'][k]['p10'], S[v]['scalars'][k]['p90']) if k in S[v]['scalars'] else '-') for v in V))
def grp(x):
    x = np.array(x, dtype=float)
    return [np.nanmean(x[a-1:b]) for a, b in [(1,3),(4,7),(8,11),(12,14),(15,18),(19,21)]]
for k in ['own_free','own_hit','dens_free','best_gain','real_gain','real_pos','price_changed','price_absmove','price_fall300','mispriced_1','arb_top20','carry300','price_future_corr','jaccard_active','jaccard_opt','bank_active','bank_optimizer','budget_cost','ft_hoard_active','ft_active','live_any_seeded']:
    for v in V:
        s = S[v]['series'].get(k)
        if s: print('%-18s %-9s' % (k, v), ' '.join('%7.2f' % x for x in grp(s['med'])))

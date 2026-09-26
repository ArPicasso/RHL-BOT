"""Агентная модель сезона «Звена» по ADR-013 и варианты экономического ядра.

Менеджеры принимают решения в дедлайн (понедельник). Цены экзогенные (season.py).
Архетипы и доли — допущения (см. ARCH). Ключевые «близнецы» (не входят в метрики аудитории):
- слепой к календарю оптимизатор (вес календаря);
- оптимизатор и активный с +5 000 ❄ в туре 1 / 8 / 14 (цена льдинки в очках);
- замороженный оптимизатор без обменов (ценность обменов).
"""
import random, math, collections
import numpy as np
from season import realize, fprice, promise, EM, T, MINP, RN

# доля, заходит/действует, убеждение, шум восприятия, бонус своему клубу
ARCH = {
    'casual':    dict(share=.30, belief='price',  eps=1.0, bias=.5),
    'parent':    dict(share=.18, belief='price',  eps=1.0, bias=.5),
    'active':    dict(share=.18, belief='active', eps=.5,  bias=.5),
    'optimizer': dict(share=.06, belief='expert', eps=.35, bias=0.),
    'form':      dict(share=.08, belief='form',   eps=.5,  bias=.3),
    'late_nov':  dict(share=.06, belief='active', eps=.5,  bias=.5),
    'late_jan':  dict(share=.04, belief='active', eps=.5,  bias=.5),
    'quitter':   dict(share=.10, belief='active', eps=.5,  bias=.5),
}
ACTIVE_TYPES = ('active', 'optimizer', 'form', 'late_nov', 'late_jan')
NEED = np.array([2, 5, 8]); START = np.array([1, 4, 6])
NY = 12   # первый тур после Нового года
import json as _json
CONF = [{'west': 0, 'east': 1}.get(t.get('conf'), 0) for t in _json.load(open(__import__('pathlib').Path(__file__).resolve().parents[2] / 'teams.json'))]

def sellval(buy, cur, cfg):
    if cur > buy:
        gain = int((cur - buy) * cfg.get('sell_share', 0.5)) // 100 * 100
        return buy + min(cfg.get('sell_cap', 500), gain)
    return cur

class M:
    pass

def make_managers(n, seed, S, cfg):
    rnd = random.Random(seed * 101 + 3)
    fans = np.array([__import__('season').B['FANW'][c] for c in __import__('season').IDS]); fans = fans / fans.sum()
    arches = []
    for a, d in ARCH.items(): arches += [a] * int(round(d['share'] * n))
    rng = np.random.default_rng(seed * 7 + 1)
    ms = []
    for mid, a in enumerate(arches):
        m = M(); m.id = mid; m.arch = a; m.twin = None
        m.rs = seed * 100003 + mid
        m.fav = int(rng.choice(26, p=fans))
        m.eps = rng.normal(0, ARCH[a]['eps'], S.N)
        m.bias = np.where(S.club == m.fav, ARCH[a]['bias'], 0.0)
        m.join = {'late_nov': 5, 'late_jan': NY}.get(a, 1)
        m.quit = rnd.randint(3, 10) if a == 'quitter' else 99
        m.phase = rnd.randint(0, 3)
        m.my = -1
        m.cal_blind = False; m.frozen = False; m.ice_bonus = None
        m.league = None
        ms.append(m)
    return ms

def clone(m, **kw):
    c = M(); c.__dict__.update(m.__dict__); c.__dict__.update(kw); c.twin = m.id
    return c

class Sim:
    def __init__(self, seed, cfg, n=300, twins=True):
        self.cfg = cfg; self.seed = seed
        self.S = S = realize(seed, cfg)
        self.ms = make_managers(n, seed, S, cfg)
        base_ids = len(self.ms)
        tw = []
        if twins:
            opts = [m for m in self.ms if m.arch == 'optimizer']
            acts = [m for m in self.ms if m.arch == 'active']
            for m in opts[:12]: tw.append(clone(m, cal_blind=True, tw='blind'))
            for m in opts[:12]: tw.append(clone(m, frozen=True, tw='frozen'))
            for m in opts[:12]: tw.append(clone(m, belief_over='formula', tw='noknow'))
            for T0 in (1, 8, 14):
                for m in opts[:10]: tw.append(clone(m, ice_bonus=(T0, 5000), tw='ice%d_opt' % T0))
                for m in acts[:10]: tw.append(clone(m, ice_bonus=(T0, 5000), tw='ice%d_act' % T0))
        self.twins = tw
        self.all = self.ms + tw
        self.roles_idx = [np.where(S.role == r)[0] for r in range(3)]
        self.met = collections.defaultdict(lambda: collections.defaultdict(list))

    # ---------------- убеждения
    def belief(self, m, t, kind=None):
        S = self.S; kind = kind or getattr(m, 'belief_over', None) or ARCH[m.arch]['belief']
        q = S.qf[t]; pr = S.prom[t]
        form = np.where(np.isnan(S.form[t]), q, S.form[t])
        if kind == 'price': mu = pr
        elif kind == 'formula': mu = q
        elif kind == 'active': mu = 0.5 * pr + 0.5 * (0.6 * form + 0.4 * q)
        elif kind == 'form': mu = 0.75 * form + 0.25 * q
        elif kind == 'expert':
            # знание хоккея: истинный средний уровень игрока за сезон с шумом (как «знаток» у Геймдизайнера)
            tal = np.where(np.isnan(S.season_mean), q, S.season_mean)
            wk = self.cfg.get('expert_w', 0.35)
            mu = (1 - wk) * q + wk * (tal + S.expert_common[t])
        else: raise ValueError(kind)
        return mu

    def avail_view(self, m, t, kind):
        S = self.S
        if kind == 'price' and m.arch in ('casual', 'parent'): return np.ones(S.N)
        if kind == 'expert':
            fa = np.where(np.isnan(S.favail[t]), S.avail[t], S.favail[t])
            return 0.7 * S.avail[t] + 0.3 * fa
        return S.avail[t]

    def ev_matrix(self, m, t, H, kind=None, calendar=True, personal=True):
        """EV[h, i]: ожидаемые очки игрока i в туре t+h по взгляду менеджера."""
        S = self.S; kind = kind or getattr(m, 'belief_over', None) or ARCH[m.arch]['belief']
        mu = self.belief(m, t, kind)
        if personal: mu = mu + m.eps + m.bias
        mu = np.maximum(0.0, mu)
        a = self.avail_view(m, t, kind)
        ab = np.clip(np.round(a * 10).astype(int), 0, 10)
        E = np.zeros((H, S.N))
        for h in range(H):
            tt = min(T, t + h)
            if calendar and not m.cal_blind: k = S.NG[tt]
            elif h == 0 and not m.cal_blind: k = S.NG[t]
            else: k = np.full(S.N, 2)
            E[h] = mu * EM[S.role, np.minimum(k, 6), ab]
        E[:, S.rest[t]] *= 0.15
        return E

    # ---------------- поиск обмена
    def search(self, m, t, E, w, budget=True, exclude_my=True, only_out=None, cand_mask=None, fee=0):
        """Лучшие обмены: по каждому уходящему — лучший входящий. Возвращает список (gain, out, in) и
        матрицу выгод по уходящим (для плотности решений)."""
        S = self.S; team = m.team; H = E.shape[0]
        tv = np.array(team); tr = S.role[tv]
        Et = E[:, tv]                                   # H x 15
        capmax = Et.max(axis=1)                          # H
        best = []
        cc = np.bincount(S.club[tv], minlength=26)
        pool = S.inpool[t] & ~S.rest[t]
        pool[tv] = False
        if cand_mask is not None: pool &= cand_mask
        cur_price = S.price[t]
        wv = np.array(w[:H])[:, None]
        for r in range(3):
            members = np.where(tr == r)[0]
            k = START[r]
            cand = self.roles_idx[r][pool[self.roles_idx[r]]]
            if len(cand) == 0: continue
            Ec = E[:, cand]                             # H x C
            topS = np.sort(Et[:, members], axis=1)[:, ::-1][:, :k].sum(axis=1)   # H
            for mi in members:
                o = team[mi]
                if only_out is not None and o not in only_out: continue
                if exclude_my and o == m.my: continue
                others = [x for x in members if x != mi]
                Eo = np.sort(Et[:, others], axis=1)[:, ::-1]
                base_o = Eo[:, :k].sum(axis=1)
                kth = Eo[:, k - 1] if len(others) >= k else np.zeros(H)
                if len(others) < k: add = Ec
                else: add = np.maximum(0, Ec - kth[:, None])
                cap_o = np.delete(Et, mi, axis=1).max(axis=1)
                dcap = np.maximum(cap_o[:, None], Ec) - capmax[:, None]
                g = ((base_o - topS)[:, None] + add + dcap) * wv
                g = g.sum(axis=0)
                sv = self.sell_of(m, o, t)
                ok = np.ones(len(cand), dtype=bool)
                if budget: ok &= cur_price[cand] <= m.bank + sv - fee
                clc = S.club[cand]
                ok &= (cc[clc] - (clc == S.club[o])) < 3
                if not ok.any(): continue
                g = np.where(ok, g, -1e9)
                j = int(np.argmax(g))
                best.append((float(g[j]), o, int(cand[j])))
        best.sort(key=lambda x: -x[0])
        return best

    def sell_of(self, m, o, t):
        S = self.S; cur = S.price[t, o]; b = m.buy[o]
        v = sellval(b, cur, self.cfg)
        if S.rest[t, o]: v = max(b, v)
        return v

    def do_swap(self, m, t, o, c, paid=False, free_release=False, ice=None):
        S = self.S
        m.bank += self.sell_of(m, o, t) - S.price[t, c]
        m.team[m.team.index(o)] = c; del m.buy[o]; m.buy[c] = S.price[t, c]
        m.held[c] = t; m.held.pop(o, None)
        m.nswaps += 1
        if paid:
            fee = self.cfg.get('fee_ice') if (ice is None or ice) else None
            if fee: m.bank -= fee; m.ice_fees += fee
            else: m.hits += 1; m.hit_pts += self.cfg.get('hit', 8)
            m.paid_n += 1
            m.paid_t[t] = m.paid_t.get(t, 0) + 1
            if fee: m.ice_t[t] = m.ice_t.get(t, 0) + 1
        elif not free_release: m.ft -= 1
        m.last_swap = t

    # ---------------- сборка
    def build(self, m, t, budget):
        S = self.S; rnd = random.Random(m.rs + 17)
        m.team = []; m.buy = {}; m.held = {}; m.bank = budget; m.ft = 0
        m.hits = 0; m.hit_pts = 0; m.nswaps = 0; m.paid_n = 0; m.ice_fees = 0; m.last_swap = t
        m.paid_t = {}; m.ice_t = {}
        m.boosts = self.boosts_for(t); m.pts = {}; m.cap_hist = []
        pool = S.inpool[t] & ~S.rest[t]
        a = m.arch
        if a in ('casual', 'parent', 'quitter') or (a == 'active' and rnd.random() < 0.5):
            # «Собрать за меня»: свой игрок и до двух одноклубников, дальше по цене с шумом
            order = []
            if a == 'parent':
                mine = [i for i in np.where(pool & (S.club == m.fav) & (S.role > 0))[0]]
                if mine:
                    wts = np.array([S.avail[t, i] for i in mine]) + 0.05
                    m.my = int(rnd.choices(mine, weights=wts)[0]); order.append(m.my)
            club_best = sorted(np.where(pool & (S.club == m.fav))[0], key=lambda i: -S.price[t, i] + rnd.gauss(0, 800))
            order += [i for i in club_best if i != m.my][:2]
            rest = sorted(np.where(pool)[0], key=lambda i: -(S.price[t, i] + rnd.gauss(0, 1500)))
            order += [i for i in rest if i not in order]
            self.greedy_fill(m, t, order)
            m.autopilot = True
        else:
            H = 4; E = self.ev_matrix(m, t, H)
            val = (E * np.array([1, .85, .7, .6])[:, None]).sum(axis=0)
            order = sorted(np.where(pool)[0], key=lambda i: -(val[i] - S.price[t, i] / 1500 + rnd.gauss(0, 0.5)))
            if m.ice_bonus and m.ice_bonus[0] == t: m.bank += m.ice_bonus[1]
            self.greedy_fill(m, t, order)
            m.autopilot = a not in ('optimizer', 'form')
            # доводка неограниченными обменами до первого дедлайна
            for _ in range(40):
                b = self.search(m, t, E, [1, .85, .7, .6])
                if not b or b[0][0] < 0.3: break
                g, o, c = b[0]; self.do_swap(m, t, o, c, free_release=True); m.nswaps -= 1
        m.ft = 0
        m.value0 = self.team_value(m, t)

    def greedy_fill(self, m, t, order):
        S = self.S; cnt = np.zeros(3, dtype=int); cl = np.zeros(26, dtype=int); left = m.bank
        for i in order:
            r = S.role[i]
            if cnt[r] >= NEED[r] or cl[S.club[i]] >= 3: continue
            rem = sum((NEED[k] - cnt[k] - (k == r)) * MINP[k] for k in range(3))
            if S.price[t, i] + rem > left: continue
            m.team.append(int(i)); m.buy[int(i)] = int(S.price[t, i]); m.held[int(i)] = t
            cnt[r] += 1; cl[S.club[i]] += 1; left -= S.price[t, i]
            if len(m.team) == 15: break
        m.bank = left
        if len(m.team) < 15:
            # добор самыми дешёвыми
            for r in range(3):
                while cnt[r] < NEED[r]:
                    c = [i for i in self.roles_idx[r] if S.inpool[t, i] and i not in m.team and cl[S.club[i]] < 3]
                    i = min(c, key=lambda i: S.price[t, i]); m.team.append(int(i)); m.buy[int(i)] = int(S.price[t, i]); m.held[int(i)] = t
                    cnt[r] += 1; cl[S.club[i]] += 1; m.bank -= S.price[t, i]

    def boosts_for(self, t):
        b = {}
        if t < NY: b['wc1'] = True
        if self.cfg.get('f2', True):
            b['wc2'] = True; b['tc'] = True; b['bb'] = True
        return b

    def team_value(self, m, t):
        return m.bank + sum(self.sell_of(m, o, t) for o in m.team)

    # ---------------- решения в дедлайн
    def free_per_tour(self, t):
        f2 = self.cfg.get('free2_from')
        return 2 if (f2 and t >= f2) else self.cfg.get('free', 1)

    def hit_cost(self, t):
        f2 = self.cfg.get('free2_from')
        return 6 if (f2 and t >= f2) else self.cfg.get('hit', 8)

    def decide(self, m, t):
        S = self.S; cfg = self.cfg; a = m.arch
        rnd = random.Random(m.rs * 31 + t)
        m.tc_now = False; m.bb_now = False; m.wc_now = False
        acting = a in ACTIVE_TYPES or (a == 'quitter' and t < m.quit)
        if m.frozen: acting = False
        # «отдыхающие»: автопилот или сам менеджер (бесплатно)
        for o in list(m.team):
            if not S.rest[t, o] or o == m.my: continue
            if m.autopilot or acting:
                kind = 'price' if m.autopilot and not acting else None
                E = self.ev_matrix(m, t, 1, kind=kind, calendar=False, personal=not m.autopilot or acting)
                b = self.search(m, t, E, [1], only_out={o}, exclude_my=True)
                if b: self.do_swap(m, t, o, b[0][2], free_release=True); m.auto_n += 1
        if m.ice_bonus and m.ice_bonus[0] == t: m.bank += m.ice_bonus[1]
        if cfg.get('missions') and acting: self.mission(m, t, rnd)
        if not acting:
            if a == 'parent' and (t + m.phase) % 4 == 0 and m.ft >= 1:
                E = self.ev_matrix(m, t, 2, calendar=False)
                b = self.search(m, t, E, [1, .8])
                if b and b[0][0] > 2: self.do_swap(m, t, b[0][1], b[0][2])
            return
        if a in ('active', 'late_nov', 'late_jan', 'quitter') and rnd.random() > 0.9: return
        # горизонт и календарь
        if a == 'optimizer': H, W, cal = 4, [1, .85, .7, .6], True
        elif a == 'form': H, W, cal = 1, [1], True
        else: H, W, cal = 2, [1, .8], bool(cfg.get('hint'))
        E = self.ev_matrix(m, t, H, calendar=cal)
        # «Заливка»
        wc = None
        if 'wc1' in m.boosts and t < NY: wc = 'wc1'
        elif 'wc2' in m.boosts and t >= NY: wc = 'wc2'
        use_wc = False
        if wc:
            if a == 'optimizer':
                b = self.search(m, t, E, W)
                strong = sum(1 for g, _, _ in b if g > 8)
                use_wc = (strong >= 3) or (wc == 'wc1' and t == NY - 1) or (wc == 'wc2' and t == NY)
            else:
                if not hasattr(m, 'wc_plan'): m.wc_plan = {}
                if wc not in m.wc_plan:
                    lo = max(t, 4 if wc == 'wc1' else NY); hi = (NY - 1) if wc == 'wc1' else 20
                    m.wc_plan[wc] = rnd.randint(lo, max(lo, hi))
                use_wc = t >= m.wc_plan[wc]
        if use_wc:
            del m.boosts[wc]; m.wc_now = True
            for _ in range(20):
                b = self.search(m, t, E, W)
                if not b or b[0][0] < 0.5: break
                self.do_swap(m, t, b[0][1], b[0][2], free_release=True)
            m.wc_used.append(t)
        else:
            hc = self.hit_cost(t)
            max_hits = {'optimizer': 2, 'form': 1}.get(a, 0) if not cfg.get('fee_ice') else {'optimizer': 2, 'form': 2}.get(a, 1)
            nh = 0; nf = 0
            fee = cfg.get('fee_ice') or 0
            for _ in range(6):
                b = self.search(m, t, E, W, fee=fee if m.ft < 1 else 0)
                if not b: break
                g, o, c = b[0]
                if m.ft >= 1:
                    if a == 'optimizer': thr = 2.5 if m.ft == 1 else (1.0 if m.ft < 5 else 0.3)
                    elif a == 'form': thr = 0.5
                    else:
                        thr = 2.0 if nf == 0 else (4.0 if m.ft >= 2 else 1e9)
                    if g > thr: self.do_swap(m, t, o, c); nf += 1; continue
                    break
                if nh < max_hits:
                    use_ice = False
                    if cfg.get('fee_ice'):
                        # платный обмен за льдинки: цена в очках — по теневой цене льдинки менеджера
                        k0 = {'optimizer': 15, 'active': 12, 'form': 6}.get(a, 12)
                        # поправка Критика: льдинка дешевеет к концу сезона (линейно до 0 к туру 21)
                        if cfg.get('fee_decl'): k0 = min(k0, {'optimizer': 1.35, 'active': 1.7, 'form': 0.8}.get(a, 1.7) * (T - t))
                        pv = k0 * cfg['fee_ice'] / 1000
                        ice_banned = bool(cfg.get('fee_last')) and t > T - cfg['fee_last']   # в последних турах — только очки
                        if ice_banned: pv = 1e9
                        if cfg.get('fee_choice') and hc <= pv:
                            ok = g > hc + (3 if a == 'optimizer' else 0)
                        else:
                            ok = g > pv + (3 if a == 'optimizer' else 0); use_ice = True
                    else:
                        ok = g > hc + (3 if a == 'optimizer' else 0)
                    if ok: self.do_swap(m, t, o, c, paid=True, ice=use_ice); nh += 1; continue
                break
        # бусты Ф2
        if t >= NY and a in ACTIVE_TYPES + ('quitter',):
            E1 = self.ev_matrix(m, t, 1)
            if 'tc' in m.boosts:
                if a == 'optimizer':
                    capk = S.NG[t][max(m.team, key=lambda i: E1[0, i])]
                    use = capk >= 4 or t == T
                else:
                    if not hasattr(m, 'tc_plan'): m.tc_plan = rnd.randint(max(t, NY), T)
                    use = t >= m.tc_plan
                if use: del m.boosts['tc']; m.tc_now = True
            if 'bb' in m.boosts and not m.tc_now:
                if a == 'optimizer':
                    lineup = self.lineup(m, t, E1[0])
                    bench = [i for i in m.team if i not in lineup[0]]
                    use = sum(S.NG[t][i] for i in bench) >= 12 or t == T
                else:
                    if not hasattr(m, 'bb_plan'): m.bb_plan = rnd.randint(max(t, NY), T)
                    use = t >= m.bb_plan
                if use: del m.boosts['bb']; m.bb_now = True

    def mission(self, m, t, rnd):
        """Задание недели. Тип — по кругу из cfg['mission_types'] (по умолчанию «новый клуб в альбоме»).
        Награда — cfg['missions'] = ('ice', N) или ('ft', N). Менеджер готов уступить до 3 очков ожидания."""
        S = self.S
        types = self.cfg.get('mission_types', ['album'])
        mt = types[(t - 1) % len(types)]
        if rnd.random() > {'optimizer': .9, 'active': .6, 'form': .6}.get(m.arch, .5): return
        E = self.ev_matrix(m, t, 2, calendar=m.arch == 'optimizer')
        W = [1, .8]
        team = m.team
        def reward():
            kind, val = self.cfg['missions']
            if kind == 'ice': m.bank += val; m.ice_src += val
            else: m.ft = min(self.cfg.get('bank_cap', 5), m.ft + val)
            m.missions += 1
        if mt == 'album':
            mask = ~np.isin(S.club, list(m.album))
            done = False
        elif mt == 'newbie':      # новичок лиги (без прошлого сезона) в основе
            mask = (~S.has_prior) & (S.role > 0)
            done = any((not S.has_prior[i]) and S.role[i] > 0 and S.npl[max(1, t - 1), i] >= 0 for i in team if S.role[i] > 0 and not S.has_prior[i])
        elif mt == 'conf':        # игрок другой конференции
            other = np.array([CONF[c] != CONF[m.fav] for c in range(26)])
            mask = other[S.club]
            done = any(other[S.club[i]] for i in team)
        elif mt == 'bigweek_d':   # два защитника с 3+ матчами клуба в этом туре
            mask = (S.role == 1) & (S.NG[t] >= 3)
            have = sum(1 for i in team if mask[i])
            done = have >= 2
            if not done and have < 1: return
        elif mt == 'stack':       # три полевых одного клуба
            cc = collections.Counter(S.club[i] for i in team if S.role[i] > 0)
            done = any(v >= 3 for v in cc.values())
            two = [c for c, v in cc.items() if v == 2 and sum(1 for i in team if S.club[i] == c) < 3]
            if not done and not two: return
            mask = np.isin(S.club, two) & (S.role > 0)
        else: raise ValueError(mt)
        if done: reward(); return
        if m.ft < 1: return
        base = self.search(m, t, E, W)
        b = self.search(m, t, E, W, cand_mask=mask)
        if mt == 'stack':   # не отдавать одноклубников, ради которых связка
            b = [x for x in b if S.club[x[1]] not in set(S.club[mask])]
        if not b: return
        g = b[0][0]; g0 = base[0][0] if base else 0
        if g > -1.5 and g > g0 - 3:
            self.do_swap(m, t, b[0][1], b[0][2])
            reward()

    def lineup(self, m, t, ev_now):
        """Основа по взгляду менеджера (казуальный — по цене), капитан, ассистент."""
        S = self.S; team = m.team
        if m.arch in ('casual',) or (m.arch == 'quitter' and t >= m.quit) or (m.arch == 'parent'):
            key = lambda i: S.price[m.join, i] if False else m.buy[i] + (1e6 if i == m.my else 0)
        else:
            key = lambda i: ev_now[i]
        starters = []; bench = []
        for r in range(3):
            g = sorted([i for i in team if S.role[i] == r], key=lambda i: -key(i))
            if m.my in g and m.my not in g[:START[r]]:
                g.remove(m.my); g.insert(0, m.my)
            starters += g[:START[r]]; bench += g[START[r]:]
        if m.arch == 'parent' and m.my >= 0: cap = m.my
        elif m.arch in ('casual',) or (m.arch == 'quitter' and t >= m.quit): cap = max(starters, key=lambda i: m.buy[i])
        else: cap = max(starters, key=lambda i: ev_now[i])
        vice = max([i for i in starters if i != cap], key=key)
        return starters, bench, cap, vice

    # ---------------- очки тура
    def score(self, m, t, starters, bench, cap, vice):
        S = self.S; cfg = self.cfg
        final = []; used = set()
        for r in range(3):
            st = [i for i in starters if S.role[i] == r]; bn = [i for i in bench if S.role[i] == r]
            for i in st:
                if S.npl[t, i] == 0:
                    sub = next((b for b in bn if S.npl[t, b] > 0 and b not in used), None)
                    if sub is not None: used.add(sub); final.append(sub); continue
                final.append(i)
        pts = {i: S.best2[t, i] for i in final}
        # сыгранность: полевые одного клуба в основе — в одном звене
        sk = [i for i in final if S.role[i] > 0]
        byc = collections.defaultdict(list)
        for i in sk: byc[S.club[i]].append(i)
        syn = 0.0
        for ci, grp in byc.items():
            if len(grp) < 2: continue
            gs = set(grp); bonus = collections.Counter()
            ks = {k for i in grp for k, _ in S.PM.get((t, i), [])}
            for k in ks:
                for a, ass in S.EV.get((ci, k), []):
                    inl = [x for x in ass if x in gs]
                    if a in gs and inl:
                        bonus[(a, k)] += 1
                        for x in inl: bonus[(x, k)] += 1
            if not bonus: continue
            for i in grp:
                ml = [(p + bonus.get((i, k), 0)) for k, p in S.PM.get((t, i), [])]
                new = sum(sorted(ml, reverse=True)[:2])
                syn += new - pts[i]; pts[i] = new
        c = cap if (cap in pts and S.npl[t, cap] > 0) else (vice if vice in pts else None)
        mult = 2 if m.tc_now else 1
        total = sum(pts.values()) + (mult * pts[c] if c is not None else 0)
        if m.bb_now:
            total += sum(S.best2[t, i] for i in bench if i not in used)
        lb = cfg.get('loyalty')
        if lb:
            need, per = lb
            total += sum(per * min(2, S.npl[t, i]) for i in final if t - m.held.get(i, t) >= need)
        return total, syn

    # ---------------- сезон
    def run(self):
        S = self.S; cfg = self.cfg
        for m in self.all:
            m.team = None; m.auto_n = 0; m.wc_used = []; m.album = set(); m.ice_src = 0; m.missions = 0; m.pts = {}
        for t in range(1, T + 1):
            # вступление
            joined_vals = [self.team_value(m, t) for m in self.ms if m.team is not None and m.arch in ('active', 'optimizer', 'form')]
            for m in self.all:
                if m.join == t:
                    bud = 100000 if t == 1 else max(100000, int(np.median(joined_vals)) if joined_vals else 100000)
                    self.build(m, t, bud)
                elif m.team is not None:
                    m.ft = min(self.cfg.get('bank_cap', 5), m.ft + self.free_per_tour(t))
            if cfg.get('jan_window') and t == NY: self.jan_window(t)
            for m in self.all:
                if m.team is None: continue
                m.tc_now = m.bb_now = m.wc_now = False
                if t > m.join: self.decide(m, t)
            self.measure_pre(t)
            for m in self.all:
                if m.team is None: continue
                ev = self.ev_matrix(m, t, 1)[0] if m.arch not in ('casual', 'parent') else np.zeros(S.N)
                st, bn, cap, vice = self.lineup(m, t, ev)
                pts, syn = self.score(m, t, st, bn, cap, vice)
                m.pts[t] = pts - (self.hit_cost(t) if False else 0) - self.hits_this_tour(m, t)
                m.syn = getattr(m, 'syn', 0) + syn
                m.album |= {S.club[i] for i in st}
                m.cap_hist.append(S.best2[t, cap])
                m.tc_now = m.bb_now = m.wc_now = False
            self.measure_post(t)
        return self.collect()

    def hits_this_tour(self, m, t):
        v = m.hit_pts - getattr(m, 'hit_pts_prev', 0); m.hit_pts_prev = m.hit_pts
        return v

    def jan_window(self, t):
        """Январское окно (тур 12): (а) прирост первого круга фиксируется в бюджете по правилу продажи, потолок
        +500 начинается заново; (б) пассивным командам (казуальный, родитель, бросивший) талисман предлагает
        «Пересобрать звено» одной кнопкой — половина соглашается; «Мой игрок» остаётся."""
        S = self.S
        for m in self.all:
            if m.team is None: continue
            if self.cfg.get('jan_crystal'):
                for o in m.team:
                    sv = self.sell_of(m, o, t)
                    m.bank += sv - S.price[t, o]
                    m.buy[o] = int(S.price[t, o])
            passive = m.arch in ('casual', 'parent') or (m.arch == 'quitter' and t >= m.quit)
            if passive and random.Random(m.rs + 999).random() < self.cfg.get('jan_accept', 0.5):
                budget = self.team_value(m, t)
                my = m.my; rnd = random.Random(m.rs + 555)
                pool = S.inpool[t] & ~S.rest[t]
                order = [my] if my >= 0 else []
                cb = sorted(np.where(pool & (S.club == m.fav))[0], key=lambda i: -S.price[t, i] + rnd.gauss(0, 800))
                order += [i for i in cb if i != my][:2]
                ev = promise(S.role, S.price[t]) * EM[S.role, np.minimum(S.NG[t], 6), np.clip(np.round(S.avail[t] * 10).astype(int), 0, 10)]
                rest = sorted(np.where(pool)[0], key=lambda i: -(ev[i] * 1000 + S.price[t, i] * 0.3 + rnd.gauss(0, 1500)))
                order += [i for i in rest if i not in order]
                keep_held = dict(m.held)
                m.team = []; m.buy = {}; m.bank = budget
                self.greedy_fill(m, t, order)
                m.rebuilt = True

    # ---------------- метрики
    def measure_pre(self, t):
        S = self.S; met = self.met
        act = [m for m in self.ms if m.team is not None and m.arch in ACTIVE_TYPES and t >= m.join]
        opt = [m for m in act if m.arch == 'optimizer']
        # 1. плотность решений и ценность обмена — общим мерилом: знаток, 3 тура, календарь
        W = [1, .8, .6]
        dens_f = []; dens_h = []; bestg = []; realg = []; orag = []; bind = []
        sample = opt + [m for m in act if m.arch == 'active'][:20] + [m for m in act if m.arch == 'form'][:8] + [m for m in act if m.arch.startswith('late')][:8]
        for m in sample:
            E = self.ev_matrix(m, t, 3, kind='expert', calendar=True, personal=False)
            b = self.search(m, t, E, W)
            if not b: continue
            dens_f.append(sum(1 for g, _, _ in b if g > 3)); dens_h.append(sum(1 for g, _, _ in b if g > 8))
            bestg.append(b[0][0])
            ub = self.search(m, t, E, W, budget=False)
            bind.append(1.0 if ub and ub[0][0] - b[0][0] > 2 else 0.0)
            met['budget_cost'][t].append(ub[0][0] - b[0][0] if ub else 0.0)
            if t <= T - 2 and m.arch in ('optimizer', 'active'):
                g, o, c = b[0]
                rg = self.realized_gain(m, t, o, c, 3, E); realg.append(rg)
                met['real_pos'][t].append(1.0 if rg > 0 else 0.0)
                R = np.zeros((3, S.N))
                for h in range(3): R[h] = S.best2[min(T, t + h)]
                ob = self.search(m, t, R, [1, 1, 1])
                if ob: orag.append(self.realized_gain(m, t, ob[0][1], ob[0][2], 3, R))
        own_f = []; own_h = []
        for m in sample:
            if m.arch == 'optimizer': H, W2, cal = 4, [1, .85, .7, .6], True
            elif m.arch == 'form': H, W2, cal = 1, [1], True
            else: H, W2, cal = 2, [1, .8], False
            b = self.search(m, t, self.ev_matrix(m, t, H, calendar=cal), W2)
            own_f.append(sum(1 for g, _, _ in b if g > 3)); own_h.append(sum(1 for g, _, _ in b if g > 8))
            met['own_best_' + m.arch][t].append(b[0][0] if b else 0)
        met['own_free'][t] = own_f; met['own_hit'][t] = own_h
        met['dens_free'][t] = dens_f; met['dens_hit'][t] = dens_h; met['best_gain'][t] = bestg
        met['real_gain'][t] = realg; met['oracle_gain'][t] = orag; met['budget_binds'][t] = bind
        # 3. сходимость: Жаккар
        def jac(group, key):
            sets = [set(key(m)) for m in group]
            if len(sets) < 2: return np.nan
            v = [len(a & b) / len(a | b) for a, b in __import__('itertools').combinations(sets, 2)]
            return float(np.mean(v))
        met['jaccard_active'][t] = jac(act, lambda m: m.team)
        met['jaccard_opt'][t] = jac(opt, lambda m: m.team)
        allm = [m for m in self.ms if m.team is not None]
        met['jaccard_all'][t] = jac(allm[::2], lambda m: m.team)
        own = collections.Counter(i for m in act for i in m.team)
        top = [c / max(1, len(act)) for _, c in own.most_common(10)]
        met['top10_own_active'][t] = float(np.mean(top)) if top else np.nan
        tmpl = [np.mean([own[i] / len(act) > 0.5 for i in m.team]) for m in act] if act else []
        met['template_share'][t] = float(np.mean(tmpl)) if tmpl else np.nan
        # 5. льдинки
        for a in ARCH:
            g = [m for m in self.ms if m.arch == a and m.team is not None]
            if g:
                met['bank_' + a][t] = float(np.median([m.bank for m in g]))
                met['value_' + a][t] = float(np.median([self.team_value(m, t) for m in g]))
                met['ft_' + a][t] = float(np.mean([m.ft for m in g]))
        met['ft_hoard_active'][t] = float(np.mean([m.ft >= 4 for m in act])) if act else np.nan
        # 4. рынок
        pool = S.inpool[t]
        if t < T + 1:
            ch = (S.price[t + 1] != S.price[t]) & pool
            met['price_changed'][t] = float(ch.sum() / pool.sum())
            met['price_absmove'][t] = float(np.abs(S.price[t + 1] - S.price[t])[pool].mean())
            met['price_fall300'][t] = float(((S.price[t] - S.price[t + 1]) >= 300)[pool].sum() / pool.sum())
        tal = S.talent[t]; ok = pool & ~np.isnan(tal) & (S.role > 0)
        err = np.abs(S.prom[t] - tal)[ok]
        met['mispriced_1'][t] = float(np.mean(err >= 1.0))
        met['mispriced_err'][t] = float(np.mean(err))
        f8 = S.fut8[t]; ok2 = pool & ~np.isnan(f8) & (S.role > 0)
        # «арбитраж»: насколько лучшие 20 по (будущие очки − обещание цены) обгоняют обещание, очков за матч
        gap = (f8 - S.prom[t])[ok2]
        met['arb_top20'][t] = float(np.mean(np.sort(gap)[-20:]))
        met['carry300'][t] = float(np.mean((np.abs(S.tgt[t] - S.price[t]) >= 300)[pool]))
        # корреляция обещания цены и будущих очков
        met['price_future_corr'][t] = float(np.corrcoef(S.prom[t][ok2], f8[ok2])[0, 1])

    def team_points(self, team, tt, ev_row):
        """Настоящие очки тура tt: основа и капитан выбраны заранее по ev_row, автозамены по протоколу."""
        S = self.S; tot = 0.0; used = set(); fin = []
        for r in range(3):
            g = sorted([i for i in team if S.role[i] == r], key=lambda i: -ev_row[i])
            st, bn = g[:START[r]], g[START[r]:]
            for i in st:
                if S.npl[tt, i] == 0:
                    sub = next((b for b in bn if S.npl[tt, b] > 0 and b not in used), None)
                    if sub is not None: used.add(sub); fin.append(sub); continue
                fin.append(i)
        cap = max(fin, key=lambda i: (S.npl[tt, i] > 0, ev_row[i]))
        return sum(S.best2[tt, i] for i in fin) + S.best2[tt, cap]

    def realized_gain(self, m, t, o, c, H, E):
        tv = list(m.team); g = 0.0
        for h in range(H):
            tt = min(T, t + h)
            g += self.team_points([c if i == o else i for i in tv], tt, E[h]) - self.team_points(tv, tt, E[h])
        return g

    def measure_post(self, t):
        met = self.met
        for a in ARCH:
            g = [m.pts[t] for m in self.ms if m.arch == a and t in m.pts]
            if g: met['pts_' + a][t] = float(np.mean(g))
        for a in ARCH:
            g = [m for m in self.ms if m.arch == a and m.team is not None]
            if g:
                met['swaps_' + a][t] = float(np.mean([m.nswaps for m in g]))
                met['paid_' + a][t] = float(np.mean([m.paid_n for m in g]))

    def collect(self):
        S = self.S
        out = dict(met={k: dict(v) for k, v in self.met.items()})
        rows = []
        for m in self.all:
            rows.append(dict(id=m.id, arch=m.arch, twin=getattr(m, 'tw', None), join=m.join, quit=m.quit, fav=m.fav,
                             pts={t: float(p) for t, p in m.pts.items()}, value=float(self.team_value(m, T + 1)),
                             value0=float(m.value0), bank=float(m.bank), swaps=m.nswaps, paid=m.paid_n, hits=m.hits,
                             auto=m.auto_n, wc=m.wc_used, fees=m.ice_fees, ice_src=m.ice_src, missions=m.missions,
                             syn=float(getattr(m, 'syn', 0)), album=len(m.album), my=m.my,
                             paid_t=dict(getattr(m, 'paid_t', {})), ice_t=dict(getattr(m, 'ice_t', {})),
                             vt={}))
        out['managers'] = rows
        return out

if __name__ == '__main__':
    import time, sys
    t0 = time.time()
    sim = Sim(1, {})
    r = sim.run()
    print('сек', round(time.time() - t0, 1))
    import statistics as st
    for a in ARCH:
        g = [x for x in r['managers'] if x['arch'] == a and not x['twin']]
        tot = [sum(x['pts'].values()) for x in g]
        n = [len(x['pts']) for x in g]
        print(f"{a:10s} n={len(g):3d} очков за сезон {st.mean(tot):6.0f} за тур {st.mean([s/k for s,k in zip(tot,n)]):5.1f} обменов {st.mean(x['swaps'] for x in g):4.1f} платных {st.mean(x['paid'] for x in g):4.1f} автопилот {st.mean(x['auto'] for x in g):4.1f} стоимость {st.mean(x['value'] for x in g):8.0f}")
    tw = collections.defaultdict(list)
    base = {x['id']: x for x in r['managers'] if not x['twin']}
    for x in r['managers']:
        if x['twin']: tw[x['twin']].append(sum(x['pts'].values()) - sum(base[x['id']]['pts'].values()))
    for k, v in tw.items(): print('близнец', k, 'разница очков %.1f' % st.mean(v))
    met = r['met']
    for k in ('dens_free', 'dens_hit', 'own_free', 'own_hit', 'own_best_optimizer', 'own_best_active', 'best_gain', 'real_gain', 'oracle_gain', 'budget_binds', 'budget_cost'):
        print(k, [round(float(np.mean(met[k][t])), 1) if met[k].get(t) else None for t in range(1, 22)])
    for k in ('jaccard_active', 'jaccard_opt', 'jaccard_all', 'template_share', 'price_changed', 'price_absmove', 'mispriced_1', 'arb_top20', 'carry300', 'price_future_corr', 'bank_active', 'bank_optimizer', 'value_optimizer', 'ft_hoard_active', 'pts_casual', 'pts_optimizer'):
        print(k, [round(met[k].get(t, float('nan')), 2) for t in range(1, 22)])

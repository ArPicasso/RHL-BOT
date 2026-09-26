"""Реализация одного сезона 26/27 из поматчевых очков 25/26 (бутстрап) и экзогенные цены по формуле ADR-013.

Перенос: k-й матч клуба в 26/27 ↔ j = (k+δ)·n25/n26-й матч того же клуба в 25/26 (δ ∈ [-2, 2] случайно).
Так сохраняются реальные пропуски, серии «выпал на 5+», рост и спад формы по ходу сезона.
Бутстрап: очки сыгранного матча берутся из случайного сыгранного матча того же игрока в окне ±4.
Четыре новых клуба — копии случайных клубов 25/26 (другие люди, без истории: все — новички по базе).
Цена от менеджеров не зависит (спроса нет), поэтому весь путь цен считается заранее.
"""
import pickle, random, math, collections
import numpy as np
from pathlib import Path
B = pickle.load(open(Path(__file__).resolve().parent / 'cache' / 'base.pkl', 'rb'))
IDS = B['IDS']; CIDX = {c: i for i, c in enumerate(IDS)}
T = 21                      # туры 1..21; индекс 0 — Пролог; 22 — после сезона
RN = ['G', 'D', 'F']
MINP = np.array([4000, 4000, 4500]); THR = np.array([3.5, 1.7, 2.8]); STEP = np.array([1300, 1500, 1400])
BASEQ = np.array([5.4, 2.26, 3.44]); GROW = np.array([0.0, 0.3, 0.5]); W_BASE = 10.0; MAXP = 15000

def fprice(role, q):
    p = MINP[role] + STEP[role] * np.maximum(0, q - THR[role])
    return (np.round(np.clip(p, MINP[role], MAXP) / 100) * 100).astype(int)

def promise(role, price):
    q = THR[role] + (price - MINP[role]) / STEP[role]
    return np.where(price <= MINP[role], THR[role] - 0.4, q)

# E[сумма 2 лучших | k матчей клуба, доступность a] / (очки за матч)
EM = np.zeros((3, 7, 11))
for r, rn in enumerate(RN):
    mt = B['MT'][rn]
    for k in range(7):
        for ab in range(11):
            a = ab / 10
            EM[r, k, ab] = sum(math.comb(k, j) * a ** j * (1 - a) ** (k - j) * mt[j] for j in range(k + 1))

class Season:
    pass

def realize(seed, cfg):
    rnd = random.Random(seed)
    S = Season(); S.cfg = cfg
    newc = [c for c in IDS if c not in B['SRC']]
    donors = rnd.sample(sorted(B['SRC']), len(newc))
    srcof = {c: (c if c in B['SRC'] else donors[newc.index(c)]) for c in IDS}
    role = []; club = []; prior = []; ent_src = []   # сущности: сначала 26 ворот, потом полевые
    for c in IDS:
        role.append(0); club.append(CIDX[c])
        gp = B['GPRIOR'].get(c) if c in B['SRC'] else None
        prior.append(gp); ent_src.append(('G', c))
    # полевые
    bysrc = collections.defaultdict(list)
    for pid, sc in B['MAIN'].items(): bysrc[sc].append(pid)
    for c in IDS:
        s = srcof[c]
        for pid in sorted(bysrc[s]):
            r = 1 if B['ROLE'][pid] == 'D' else 2
            role.append(r); club.append(CIDX[c]); ent_src.append((pid, c))
            pr = B['PRIOR'].get(pid) if c == s else None
            prior.append((pr[1], pr[2]) if (pr and pr[0] == B['ROLE'][pid]) else None)
    N = len(role); S.N = N
    S.role = np.array(role); S.club = np.array(club)
    eid = {(x[0], x[1]): i for i, x in enumerate(ent_src)}
    # --- матчи клуба 26/27 → матчи 25/26
    NGc = np.zeros((T + 2, 26), dtype=int)
    PM = collections.defaultdict(list)       # (t, i) -> [(k, pts)]
    EV = {}                                  # (ci, k) -> [(author_e, (ass_e...))]
    games_t = [[] for _ in range(26)]         # по клубу: список туров матчей
    BW = cfg.get('boot_window', 8)
    DG = collections.defaultdict(list)        # сущность -> [(тур, истинное среднее генерирующего окна)]
    for c in IDS:
        ci = CIDX[c]; s = srcof[c]; gl = B['SRC'][s]; n25 = len(gl); sch = B['SCHED'][c]; n26 = len(sch)
        d = rnd.randint(-2, 2)
        jm = [min(n25 - 1, max(0, int((k + d) * n25 / n26))) for k in range(n26)]
        players = sorted(bysrc[s])
        pres = {pid: [j for j in range(n25) if pid in gl[j]['sk']] for pid in players}
        gi = eid[('G', c)]
        for k, (ds, t) in enumerate(sch):
            NGc[t, ci] += 1; games_t[ci].append(t)
            j = jm[k]
            jj = min(n25 - 1, max(0, j + rnd.randint(-BW, BW)))
            PM[(t, gi)].append((k, float(gl[jj]['gate'])))
            DG[gi].append((t, float(np.mean([gl[x]['gate'] for x in range(max(0, j - BW), min(n25, j + BW + 1))]))))
            for pid in players:
                if pid not in gl[j]['sk']: continue
                cand = [x for x in pres[pid] if abs(x - j) <= BW]
                jj = rnd.choice(cand)
                PM[(t, eid[(pid, c)])].append((k, gl[jj]['sk'][pid]))
                DG[eid[(pid, c)]].append((t, float(np.mean([gl[x]['sk'][pid] for x in cand]))))
            evl = []
            for a, ass in gl[j]['ev']:
                ea = eid.get((a, c));
                es = tuple(eid[(x, c)] for x in ass if (x, c) in eid)
                if ea is not None and es: evl.append((ea, es))
            EV[(ci, k)] = evl
    S.NGc = NGc; S.PM = PM; S.EV = EV
    S.NG = NGc[:, S.club]                     # (T+2, N)
    # --- поматчевые ряды по сущностям для цены, формы, доступности
    seqs = [[] for _ in range(N)]             # (t, k, pts)
    for (t, i), l in PM.items():
        for k, p in l: seqs[i].append((t, k, p))
    for l in seqs: l.sort(key=lambda x: x[1])
    S.best2 = np.zeros((T + 2, N)); S.npl = np.zeros((T + 2, N), dtype=int)
    for (t, i), l in PM.items():
        ps = sorted((p for _, p in l), reverse=True)
        S.best2[t, i] = sum(ps[:2]); S.npl[t, i] = len(ps)
    # --- цена: по дедлайнам t=1..22 (цена в понедельник тура t; 22 — итог)
    price = np.zeros((T + 3, N), dtype=int); qf = np.zeros((T + 3, N)); tgt = np.zeros((T + 3, N), dtype=int)
    form = np.full((T + 3, N), np.nan); avail = np.full((T + 3, N), 0.85); rest = np.zeros((T + 3, N), dtype=bool)
    talent = np.full((T + 3, N), np.nan); favail = np.full((T + 3, N), np.nan); fut8 = np.full((T + 3, N), np.nan)
    first = np.full(N, 99)
    half = cfg.get('decay_half'); lam = 0.5 ** (1 / half) if half else 1.0
    capf = lambda t: cfg.get('cap_early', 800) if t <= cfg.get('early_tours', 4) else cfg.get('cap', 500)
    for i in range(N):
        r = S.role[i]; pr = prior[i]
        wpr = 0.5 * pr[0] if pr else 0.0; qpr = (pr[1] + GROW[r]) if pr else 0.0
        wb = W_BASE; sp = 0.0; n = 0.0
        q0 = (wpr * qpr + wb * BASEQ[r]) / (wpr + wb)
        seq = seqs[i]
        if not seq: continue
        first[i] = seq[0][0]
        p_cur = int(fprice(r, q0)); q = q0
        # по турам
        teamg = games_t[S.club[i]]          # тур каждого матча клуба
        played_k = {k for _, k, _ in seq}
        bytour = collections.defaultdict(list)
        for t, k, p in seq: bytour[t].append(p)
        hist = []                            # очки сыгранных матчей по порядку
        for t in range(0, T + 1):
            # дедлайн тура t (для t>=1): состояние после туров < t
            if t >= 1:
                price[t, i] = p_cur; qf[t, i] = q; tgt[t, i] = fprice(r, q)
                if len(hist) >= 2: form[t, i] = np.mean(hist[-4:])
                kprev = [k for k in range(len(teamg)) if teamg[k] < t]
                if first[i] < t:
                    last8 = kprev[-8:]
                    if len([k for k in kprev if k >= min(played_k)]) >= 3:
                        avail[t, i] = sum(1 for k in last8 if k in played_k) / len(last8)
                    last4 = kprev[-4:]
                    rest[t, i] = len(last4) == 4 and all(k not in played_k for k in last4)
                knext = [k for k in range(len(teamg)) if teamg[k] >= t][:8]
                if knext: favail[t, i] = sum(1 for k in knext if k in played_k) / len(knext)
            ps = bytour.get(t, [])
            if ps:
                for p in ps:
                    if lam < 1: sp *= lam; n *= lam; wpr *= lam; wb *= lam
                    sp += p; n += 1; hist.append(p)
                q = (wpr * qpr + sp + wb * BASEQ[r]) / (wpr + n + wb)
                cap = capf(max(t, 1))
                capd = min(cap, cfg.get('cap_down', cap))
                p_cur = int(np.clip(fprice(r, q), p_cur - capd, p_cur + cap))
        price[T + 1, i] = p_cur; qf[T + 1, i] = q; tgt[T + 1, i] = fprice(r, q)
        # «талант»: истинное ожидание очков за матч (среднее окна бутстрапа) на ближайшие 6 сыгранных матчей.
        # Шума исхода в нём нет: это верхняя граница «знания хоккея», а не подсмотренный результат.
        allp = [(t, p) for t, k, p in seq]; dg = DG[i]
        for t in range(1, T + 1):
            idx = sum(1 for tt, _ in dg if tt < t)
            nx = [x for _, x in dg[idx: idx + 6]]
            if len(nx) >= 2: talent[t, i] = np.mean(nx)
            nxt = [p for _, p in allp[idx: idx + 8]]
            if len(nxt) >= 4: fut8[t, i] = np.mean(nxt)
    S.price = price; S.qf = qf; S.tgt = tgt; S.form = form; S.avail = avail; S.rest = rest
    S.season_mean = np.array([np.mean([x for _, x in DG[i]]) if DG[i] else np.nan for i in range(N)])
    S.talent = talent; S.favail = favail; S.fut8 = fut8; S.first = first
    S.inpool = np.array([[first[i] < t for i in range(N)] for t in range(T + 3)])
    S.prom = np.zeros((T + 3, N))
    for t in range(1, T + 2): S.prom[t] = promise(S.role, price[t])
    # общий сигнал «знания хоккея» (одинаков для всех знатоков): талант + шум
    rs = np.random.default_rng(seed + 7)
    S.expert_common = rs.normal(0, cfg.get('expert_sd', 0.7), size=(T + 3, N))
    S.has_prior = np.array([p is not None for p in prior])
    return S

if __name__ == '__main__':
    import time
    t0 = time.time(); S = realize(1, {}); print('N', S.N, 'сек', round(time.time() - t0, 2))
    print('в пуле к туру 1', S.inpool[1].sum(), 'к туру 10', S.inpool[10].sum())
    for r in range(3):
        m = S.inpool[1] & (S.role == r)
        print(RN[r], 'цены тура 1: мин %d медиана %d p90 %d макс %d' % (S.price[1][m].min(), np.median(S.price[1][m]), np.percentile(S.price[1][m], 90), S.price[1][m].max()))
    ch = [(np.mean((S.price[t + 1] != S.price[t])[S.inpool[t]])) for t in range(1, 22)]
    print('доля с изменением цены по турам', [round(x, 2) for x in ch])
    print('отдыхают по турам', [int(S.rest[t].sum()) for t in range(1, 22)])

"""Экономист: базовые данные для агентной модели «Звена».

Что собирает (кэш в base.pkl):
- поматчевые очки 25/26 по правилам ADR-013 (полевые и «Ворота клуба»), события голов для сыгранности;
- приоры прошлого сезона 24/25 (bt.json 'A' у полевых, stat1308 у ворот);
- календарь РХЛ 26/27 по клубам и турам (тур 0 — Пролог 03–11.10, туры 1–21 по tours21.json);
- множители «2 лучших из k матчей» по амплуа, эмпирически.

Правила очков — как в модели Геймдизайнера (designer/v2/model.py): +/- и броски полевых — средние игрока
за сезон (в протоколах 25/26 их нет по матчам), удаление 10+ минут — матч 0, пол 0.
"""
import json, collections, pickle, random, statistics as st, itertools, math
from datetime import date, timedelta
from pathlib import Path
HERE = Path(__file__).resolve().parent
DATA = HERE / 'data'
REPO = HERE.parent.parent

def num(x):
    try: return float(str(x).replace(',', '.'))
    except Exception: return 0.0

def tmin(s):
    try: m, x = s.split(':'); return int(m) + int(x) / 60
    except Exception: return 0.0

TEAMS = json.load(open(REPO / 'teams.json'))
IDS = [t['id'] for t in TEAMS]
name2id = {}
for t in TEAMS:
    for n in [t['name']] + t.get('aliases', []) + (t.get('former') or []):
        name2id[n.lower()] = t['id']

def club_id(name):
    n = name.lower()
    for pre in ('мхк ', 'хк '):
        if n.startswith(pre): n = n[len(pre):]
    if n in name2id: return name2id[n]
    for k, v in name2id.items():
        if k in n or n in k: return v
    return None

# ---------- 25/26: поматчевые очки
ST = json.load(open(DATA / 'stat_1378.json'))
SEASON = {}
for rel, role in [('defenses', 'D'), ('forwards', 'F')]:
    for r in ST[rel]:
        gp = num(r['И'])
        if gp: SEASON[r['id']] = dict(role=role, pm=num(r['+/-']) / gp, sog=num(r['БВ']) / gp, name=r['Игрок'])

HP = json.load(open(REPO / 'history_protocols.json'))
GAMES = sorted([v for v in HP.values() if '2025-09-01' <= v['date'] < '2026-03-25'], key=lambda v: (v['date'], v.get('time', '')))

SRC = collections.defaultdict(list)   # club_id -> [game dict]
ROLE = {}; MAINCLUB = collections.defaultdict(collections.Counter); NAME = {}
for v in GAMES:
    hs, as_ = v['home_score'], v['away_score']; so_dec = v['decision'] == 'Б'
    so_winner = collections.Counter()
    for g in v['goals']:
        if g['period'] == 'РБ': so_winner[g['author'].get('id')] += 1
    big = set()
    for p in v.get('penalties', []):
        if (p.get('minutes') or 0) >= 10 and p.get('player'): big.add(p['player'].get('id'))
    for side in ('home', 'away'):
        cid = club_id(v[side])
        won = (hs > as_) if side == 'home' else (as_ > hs)
        opp = as_ if side == 'home' else hs
        ga = opp - (1 if (so_dec and not won) else 0)
        gks = [s for s in v['lineups'] if s['team'] == side and s['role'] == 'G' and s.get('played')]
        sv = sum(s.get('saves', 0) or 0 for s in gks)
        gate = 2 + 3 * won + sv // 4 - 2 * ga + (5 if ga == 0 else 0) + 3 * sum(s.get('assists', 0) for s in gks) + 5 * sum(s.get('goals', 0) for s in gks)
        sk = {}
        for s in v['lineups']:
            if s['team'] != side or s['role'] == 'G' or not s.get('played', True): continue
            pid = s['player'].get('id')
            if not pid: continue
            role = s['role']; ROLE[pid] = role; MAINCLUB[pid][cid] += 1; NAME[pid] = s['player'].get('name')
            if pid in big: p = 0.0
            else:
                x = SEASON.get(pid, dict(pm=0, sog=0))
                p = 1 + (1 if won else 0) + (6 if role == 'D' else 5) * s.get('goals', 0) + 3 * s.get('assists', 0) + x['pm'] + max(0, x['sog'] / 2 - 0.25)
                if s.get('gwg', 0) or so_winner[pid]: p += 2
                p = max(0.0, p)
            sk[pid] = round(p, 2)
        ev = []
        for g in v['goals']:
            if g['period'] == 'РБ' or g['team'] != side: continue
            a = g['author'].get('id'); ass = tuple(x.get('id') for x in g['assists'] if x.get('id'))
            if a and ass: ev.append((a, ass))
        SRC[cid].append(dict(date=v['date'], won=won, gate=max(0, gate), sk=sk, ev=ev))

MAIN = {pid: c.most_common(1)[0][0] for pid, c in MAINCLUB.items()}

# ---------- приоры 24/25
BT = json.load(open(DATA / 'prior_2425.json'))
PRIOR = {}
for k, a in BT['A'].items():
    pid = int(k)
    if a['role'] in 'DF' and a['gp'] >= 3: PRIOR[pid] = (a['role'], a['gp'], a['pts'] / a['gp'])
S8 = json.load(open(DATA / 'stat_1308.json'))
gagg = collections.defaultdict(lambda: collections.Counter())
for r in S8['goalies']:
    cid = club_id(r['Клуб'])
    if not cid: continue
    c = gagg[cid]; c['full'] += tmin(r['ВП']) / 60; c['w'] += num(r['В']); c['ga'] += num(r['ПШ']); c['sv'] += num(r['ОБ']); c['so'] += num(r['И"0"'])
GPRIOR = {}
for cid, c in gagg.items():
    g = c['full']
    if g < 20: continue
    ppg = 2 + 3 * c['w'] / g + (c['sv'] / g) / 4 - 0.375 - 2 * c['ga'] / g + 5 * c['so'] / g
    GPRIOR[cid] = (round(g), max(0.5, ppg))

# ---------- календарь 26/27
L = json.load(open(DATA / 'calendar_2627.json'))
T21 = json.load(open(DATA / 'tours21.json'))
def tour_of(ds):
    for t in T21:
        if t['from'] <= ds <= t['to']: return t['t']
    if ds < T21[0]['from']: return 0
    return None
SCHED = collections.defaultdict(list)   # club -> [(date, tour)]
for g in sorted(L['games'], key=lambda g: g['date']):
    t = tour_of(g['date'])
    if t is None: continue
    SCHED[g['home']].append((g['date'], t)); SCHED[g['away']].append((g['date'], t))

# ---------- множители «2 лучших из j сыгранных»
def mult_table(draws_by_player, N=400, maxj=6):
    rnd = random.Random(1); acc = [0.0] * (maxj + 1); n = 0
    for d in draws_by_player:
        mu = st.mean(d)
        if mu <= 0.3: continue
        n += 1
        for j in range(1, maxj + 1):
            s = 0.0
            for _ in range(N // 4):
                x = sorted((rnd.choice(d) for _ in range(j)), reverse=True); s += sum(x[:2])
            acc[j] += s / (N // 4) / mu
    return [a / n for a in acc]
by = collections.defaultdict(list)
for cid, gl in SRC.items():
    for g in gl:
        for pid, p in g['sk'].items(): by[pid].append(p)
MT = {}
for role in 'DF':
    MT[role] = mult_table([d for pid, d in by.items() if ROLE[pid] == role and len(d) >= 15])
MT['G'] = mult_table([[g['gate'] for g in gl] for gl in SRC.values()])
MT['D'][0] = MT['F'][0] = MT['G'][0] = 0.0

# ---------- доля болельщиков по клубам (для любимого клуба менеджера): корень из посещаемости
att = collections.defaultdict(list)
for v in GAMES:
    if v.get('attendance'): att[club_id(v['home'])].append(v['attendance'])
FANW = {c: math.sqrt(st.median(att[c])) if att.get(c) else math.sqrt(400) for c in IDS}

base = dict(IDS=IDS, SRC=dict(SRC), ROLE=ROLE, MAIN=MAIN, NAME=NAME, PRIOR=PRIOR, GPRIOR=GPRIOR,
            SCHED=dict(SCHED), MT=MT, FANW=FANW, T21=T21)
(HERE / 'cache').mkdir(exist_ok=True)
pickle.dump(base, open(HERE / 'cache' / 'base.pkl', 'wb'))
if __name__ == '__main__':
    print('клубов с историей', len(SRC), 'матчей по клубам', sorted(len(v) for v in SRC.values()))
    print('полевых', len(ROLE), collections.Counter(ROLE.values()), 'с приором 24/25', sum(1 for p in ROLE if p in PRIOR))
    print('ворота с приором', len(GPRIOR), sorted((k, round(v[1], 2)) for k, v in GPRIOR.items()))
    print('матчей 26/27 по клубам', sorted(collections.Counter(len(v) for v in SCHED.values()).items()))
    print('тур 0 (Пролог) матчей у клуба', sorted(collections.Counter(sum(1 for _, t in v if t == 0) for v in SCHED.values()).items()))
    for r in 'DFG': print(r, [round(x, 2) for x in MT[r]])

import json, sys
sys.path.insert(0, '.')
from board import build

ROOT = '/home/user/bogdanov/'
teams = {t['id']: t for t in json.load(open(ROOT + 'teams.json'))}
b = build()
clubs = []
for cid, cell in b['bases'].items():
    t = teams[cid]
    clubs.append({'id': cid, 'name': t['name'], 'abbr': t['abbr'], 'city': t['city'],
                  'color': t['colors'][0], 'logo': t['logo'], 'base': cell})
clubs.sort(key=lambda c: c['name'])

# Игровые цвета: у половины лиги форма тёмно-синяя, на карте их не различить.
# Берём палитру различимых цветов и раздаём так, чтобы цвет был похож на клубный,
# но соседи по карте не совпадали.
PALETTE = ['#2f6fed', '#e03131', '#2bb673', '#f59f00', '#8b5cf6', '#00b5d8', '#e8590c', '#12b886',
           '#f06595', '#1864ab', '#b08900', '#7048e8', '#0ca678', '#d6336c', '#b5651d', '#4dabf7',
           '#c92a2a', '#5c940d', '#9c36b5', '#1098ad', '#f76707', '#37b24d', '#364fc7', '#a61e4d',
           '#087f5b', '#5f3dc4']


def rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(rgb(a), rgb(b))) ** .5


cellpos = {c['id']: (b['cells'][c['base']]['x'], b['cells'][c['base']]['y']) for c in clubs}
left = list(PALETTE)
for c in sorted(clubs, key=lambda c: cellpos[c['id']][0]):
    best, bs = None, -1e9
    for p in left:
        like = -dist(p, c['color']) / 441 * 100                    # похоже на форму клуба
        near = 0
        for o in clubs:
            if 'game' not in o:
                continue
            dx = cellpos[o['id']][0] - cellpos[c['id']][0]
            dy = cellpos[o['id']][1] - cellpos[c['id']][1]
            d = (dx * dx + dy * dy) ** .5
            if d < 260:                                            # сосед по карте
                near -= max(0, 160 - dist(p, o['game'])) * (260 - d) / 260 / 4
        if like + near > bs:
            best, bs = p, like + near
    c['game'] = best
    left.remove(best)

games = json.load(open(ROOT + 'webapp/data/league.json'))['games']
ids = {c['id'] for c in clubs}

# Для каждого клуба — три настоящих матча из календаря: дома, в гостях и ещё один.
# В демо твои их выигрывают: так видно оба эффекта — укрепление кольца и десант.
fix = {}
for c in clubs:
    cid = c['id']
    home = [g for g in games if g['home'] == cid and g['away'] in ids]
    away = [g for g in games if g['away'] == cid and g['home'] in ids]
    seq = []
    if home:
        seq.append({'h': home[0]['home'], 'a': home[0]['away'], 's': '3:2'})
    if away:
        seq.append({'h': away[0]['home'], 'a': away[0]['away'], 's': '1:4'})
    if len(home) > 1:
        seq.append({'h': home[1]['home'], 'a': home[1]['away'], 's': '5:1'})
    while len(seq) < 3 and away:
        seq.append({'h': away[-1]['home'], 'a': away[-1]['away'], 's': '2:3'})
    fix[cid] = seq

data = {'s': b['s'], 'w': b['w'], 'h': b['h'], 'cells': b['cells'], 'adj': b['adj'],
        'clubs': clubs, 'fixtures': fix}
page = open('page.html', encoding='utf-8').read().replace('/*DATA*/', json.dumps(data, ensure_ascii=False, separators=(',', ':')))
open('index.html', 'w', encoding='utf-8').write(page)
print('клеток', len(b['cells']), '| клубов', len(clubs), '| матчей', len(fix), '| размер', round(len(page)/1024), 'КБ')

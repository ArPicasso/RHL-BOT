"""Монте-Карло по вариантам: python3 run.py VARIANT [n_seeds]. Результаты — results/<variant>.pkl"""
import sys, pickle, time, os
from multiprocessing import Pool
from sim import Sim
VARIANTS = {
    'base':      {},                                        # ADR-013 как есть (MVP + бусты Ф2 со 2-го круга)
    'mvp':       {'f2': False},                             # без бустов Ф2
    'decay12':   {'decay_half': 12},                        # цена с затуханием, полураспад 12 матчей
    'decay20':   {'decay_half': 20},
    'fee600':    {'fee_ice': 600},                          # лишний обмен за 600 ❄ вместо −8 очков
    'free2':     {'free2_from': 12},                        # запасная ветка ADR: 2 бесплатных и −6 со 2-го круга
    'mis_ice':   {'missions': ('ice', 200)},                # задание недели: +200 ❄
    'mis_ft':    {'missions': ('ft', 1)},                   # задание недели: +1 обмен
    'loyal':     {'loyalty': (6, 0.5)},                     # верность: +0,5 за зачтённый матч после 6 туров
    'hint':      {'hint': True},                            # «Скаут недели»: активным видна большая неделя следующего тура
    'fee400':    {'fee_ice': 400},
    'fee900':    {'fee_ice': 900},
    'janwin2':   {'jan_window': True},                      # январское окно без фиксации: «Пересобрать звено» пассивным
    'decay20dn': {'decay_half': 20, 'cap_down': 300},       # затухание 20 и вниз не больше −300 за тур
    'core2':     {'decay_half': 20, 'cap_down': 300, 'fee_ice': 600, 'missions': ('ft', 1), 'jan_window': True},
    'core3':     {'decay_half': 20, 'fee_ice': 600, 'missions': ('ft', 1), 'jan_window': True},
    'choice':    {'fee_ice': 600, 'fee_choice': True},       # «8 очков или 600 ❄» на выбор, с тура 1
    'core4':     {'decay_half': 20, 'fee_ice': 600, 'fee_choice': True, 'missions': ('ft', 1), 'jan_window': True},
    'mis_bw':    {'missions': ('ft', 1), 'mission_types': ['bigweek_d']},   # худший случай: каждый тур «большая неделя»
    'mis_stack': {'missions': ('ft', 1), 'mission_types': ['stack']},
    'mis_mix':   {'missions': ('ft', 1), 'mission_types': ['album', 'bigweek_d', 'stack', 'newbie', 'conf']},
    'base600':   {'_n': 600, '_twins': False},
    'choice_mis': {'fee_ice': 600, 'fee_choice': True, 'fee_decl': True, 'fee_last': 3, 'missions': ('ft', 1)},
    'core5':     {'decay_half': 20, 'fee_ice': 600, 'fee_choice': True, 'fee_decl': True, 'fee_last': 3, 'missions': ('ft', 1), 'jan_window': True},
    'core':      {'decay_half': 16, 'fee_ice': 600, 'missions': ('ft', 1)},
    # Ядро, принятое в ADR-013: затухание 20, «8 очков или 600 ❄» с тура 1 (в турах 19–21 только очки),
    # задание недели «Новый клуб в альбоме» → +1 обмен, «Пересобрать звено» 04.01. С него сравнивают плюшки
    'zveno':     {'decay_half': 20, 'fee_ice': 600, 'fee_choice': True, 'fee_decl': True, 'fee_last': 3, 'missions': ('ft', 1), 'jan_window': True},
    # То же без затухания цены — если владелец его не примет
    'zveno_nodecay': {'fee_ice': 600, 'fee_choice': True, 'fee_decl': True, 'fee_last': 3, 'missions': ('ft', 1), 'jan_window': True},
}
def one(args):
    v, seed = args
    t0 = time.time()
    cfg = dict(VARIANTS[v]); n = cfg.pop('_n', 300); tw = cfg.pop('_twins', True)
    r = Sim(seed, cfg, n=n, twins=tw).run()
    r['seed'] = seed; r['sec'] = time.time() - t0
    return r
if __name__ == '__main__':
    v = sys.argv[1]; n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    t0 = time.time()
    with Pool(4) as p:
        res = p.map(one, [(v, s) for s in range(1000, 1000 + n)])
    os.makedirs('results', exist_ok=True)
    pickle.dump(res, open(f'results/{v}.pkl', 'wb'))
    print(v, n, 'сезонов', round(time.time() - t0), 'сек')

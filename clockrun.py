"""Секунда гола по ходу часов табло (ADR-031). Без сети и без ffmpeg: кадры даёт служба clips, здесь только счёт.

Между двумя голами одного периода часы игры на табло идут ровно столько секунд, сколько между ними по протоколу
(время гола — `time` у гола в league.json, от начала матча), — сколько бы ни длились остановки, повторы и перерывы
записи между ними. От гола с точной секундой (опоры: отметка админа или остановка часов) идём по записи кадр в
секунду и считаем секунды, когда часы шли; где набралась разница протокола, там часы встали на нужном голе.

Секунда записи t здесь — пара кадров (t, t+1): часы шли (клетка часов сменилась), стояли или неизвестно (табло нет
на экране: повтор, крупный план, реклама). Неизвестные секунды могли идти или стоять, поэтому у гола окно: от «все
неизвестные шли» до «все стояли». Секунда опоры — последняя, когда часы ещё шли перед голом (как `clock` у службы):
пары A+1…S — ход часов от гола A до гола S.

Секунда гола S — последний ход часов перед остановкой: пара S шла, пара S+1 — нет. Что выходит у гола:
- такая секунда, согласная со счётом, одна — точная секунда, но только если счёт в этом матче проверен (самопроверка
  ниже сошлась хоть на одной паре) или гол согласен со сменой счёта на табло;
- их несколько, от первой до последней не больше RUN_WINDOW — окно и остановки в нём: превью админу и примерный повтор;
- шире или счёт не дотянулся за RUN_MAX — ничего.

Самопроверка: точные голы периода предсказываются друг по другу. Не сошлось — гол не тот или клетка часов
«меняется» на стоящих часах (табло без плашки, камера движется за цифрами). Тогда точные секунды табло у этих голов
снимаем — и отметку человека тоже (ADR-033: отметка — показание, а не истина), а в периоде счёт хода не включаем:
клип мимо гола хуже никакого.

Проверка отметок людей (ADR-033, `check_marks`): у каждой отметки — встали ли рядом часы, сошлась ли она со сменой
счёта на табло и с соседними точными голами периода. Итог — «сошлось», «не сошлось» (спор) или «нечем проверить».
"""
import re

RUN_TOL = 3          # с: допуск — отметка админа к остановке часов, гол к смене счёта на табло
RUN_WINDOW = 120     # с: окно не шире — превью этого окна и примерный повтор
RUN_MAX = 2400       # с записи от опоры в одну сторону: период с остановками и перерывом записи — до 40 минут
CHECK_TOL = 3        # с: самопроверка — опора предсказана не дальше стольких секунд от своей секунды
CHANGE_BEFORE = 120  # с: гол — не раньше стольких секунд до смены счёта на табло (оператор меняет через 0–90 с)
RUNNING = (5, 8)     # с до и после отметки: часы всё это время идут на виду — гола в отметке нет

_TIME_RE = re.compile(r"(\d{1,3}):(\d{2})")


def game_sec(text) -> int | None:
    """Время гола в протоколе от начала матча → секунды: «59:33» → 3573. Не разобрали — None."""
    m = _TIME_RE.fullmatch(str(text or "").strip())
    return int(m.group(1)) * 60 + int(m.group(2)) if m and int(m.group(2)) < 60 else None


def walk(state, anchor: int, delta: int, limit: int = RUN_MAX) -> list[int] | None:
    """Где часы могли встать на голе, который по протоколу на delta секунд позже опоры (delta < 0 — раньше).
    state(t) — пара кадров (t, t+1): «run», «stop» или None (табло не видно). Опора — секунда последнего хода часов
    перед её голом. Секунда гола S — тоже последний ход перед остановкой: пара S шла (или не видно), пара S+1 — нет,
    а ход часов между голами (пары A+1…S вперёд, S+1…A назад) равен delta при каком-то выборе неизвестных пар.
    Все такие S по порядку; дотянулись, а такой S нет — [] (счёт с протоколом не сходится); не дотянулись за
    limit — None."""
    need = abs(delta)
    if not need:
        return None
    out, k, u = [], 0, 0
    if delta > 0:
        for t in range(anchor + 1, anchor + 1 + limit):
            s = state(t)
            k += s == "run"
            u += s is None
            low = k + (s is None)              # неизвестная пара S сама должна идти
            if s != "stop" and state(t + 1) != "run" and low <= need <= k + u:
                out.append(t)
            if k >= need:
                return out
        return None
    for t in range(anchor, max(-1, anchor - limit), -1):   # S = t, ход — в парах t+1…A
        s = state(t)
        if s != "stop" and state(t + 1) != "run" and k <= need <= k + u:
            out.append(t)
        k += s == "run"
        u += s is None
        if k > need:
            return sorted(out)
    return None


def is_stop(state, t: int) -> bool:
    """Часы встали: в паре t ещё шли, в паре t+1 уже стоят (как clock_stops службы clips)."""
    return state(t) == "run" and state(t + 1) == "stop"


def _periods(goals: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for g in goals:
        if g.get("period") != "РБ" and game_sec(g.get("time")) is not None:
            out.setdefault(str(g.get("period") or ""), []).append(g)
    return out


def snap(state, t: float, tol: int = RUN_TOL) -> int:
    """Секунда опоры — к ближайшей остановке часов в tol: админ отмечает гол с точностью в секунду-две, а счёт хода
    ведётся от последнего хода часов перед голом."""
    near = [x for x in range(int(t) - tol, int(t) + tol + 1) if is_stop(state, x)]
    return min(near, key=lambda x: abs(x - t)) if near else int(t)


def _window(state, anchor: dict, goal: dict) -> list[int] | None:
    return walk(state, snap(state, anchor["t"]), game_sec(goal["time"]) - game_sec(anchor["time"]))


def check(goals: list[dict], state) -> tuple[int, dict[str, set[str]], set[str]]:
    """Самопроверка: каждый точный гол периода — по каждому другому. (сколько пар сошлось, период → счета голов в
    несошедшихся парах, счета голов в сошедшихся парах). Возможных секунд шире RUN_WINDOW — пара ничего не доказывает."""
    ok, bad, good = 0, {}, set()
    for per, gs in _periods(goals).items():
        anchors = [g for g in gs if isinstance(g.get("t"), (int, float))]
        for a in anchors:
            for b in anchors:
                if a is b or game_sec(a["time"]) == game_sec(b["time"]):
                    continue
                w = _window(state, a, b)
                if w is None or w and w[-1] - w[0] > RUN_WINDOW:
                    continue
                if w and w[0] - CHECK_TOL <= b["t"] <= w[-1] + CHECK_TOL:
                    ok += 1
                    good.update((a["score"], b["score"]))
                else:
                    bad.setdefault(per, set()).update((a["score"], b["score"]))
    return ok, bad, good


def solve(goals: list[dict], state) -> dict:
    """Голы матча по протоколу → что даёт счёт хода. goals — {"score", "period", "time", "t" (точная секунда или None),
    "src" («admin», «clock», «board»…), "change" (смена счёта на табло или None)}. Ответ: {"found": {счёт: {"win":
    [от, до], "cand": [остановки в окне], "from": счёт опоры, "t"?: точная секунда}}, "drop": [счёт — снять точную
    секунду табло], "fail": [периоды, где самопроверка не сошлась], "checked": пар сошлось, "confirmed": [счета точных
    голов, которые ход часов подтвердил парой с другим точным голом, а ни одна пара не опровергла] — второй свидетель
    для клипа (ADR-033, раздел 4)}."""
    ok, bad, good = check(goals, state)
    drop = sorted({s for per in bad.values() for s in per for g in goals if g["score"] == s})
    found = {}
    for per, gs in _periods(goals).items():
        if per in bad:
            continue
        anchors = [g for g in gs if isinstance(g.get("t"), (int, float))]
        for goal in gs:
            if isinstance(goal.get("t"), (int, float)) or not anchors:
                continue
            at = game_sec(goal["time"])
            before = [a for a in anchors if game_sec(a["time"]) < at]
            after = [a for a in anchors if game_sec(a["time"]) > at]
            tries = [x for x in (max(before, key=lambda a: game_sec(a["time"]), default=None),
                                 min(after, key=lambda a: game_sec(a["time"]), default=None)) if x]
            wins = [(w, a) for a in tries for w in [_window(state, a, goal)] if w is not None]
            if any(not w for w, _ in wins):
                continue         # от одной из опор счёт дотянулся, а остановки на месте нет — что-то не так
            if len(wins) == 2:   # от опоры до и от опоры после: гол — там, где согласны обе
                both = sorted(set(wins[0][0]) & set(wins[1][0]))
                if not both:
                    continue     # окна врозь: счёт где-то сбился — молчим
                wins = [(both, min(wins, key=lambda x: x[0][-1] - x[0][0])[1])]
            (w, a), = wins[:1] or [(None, None)]
            if not w:
                continue
            change = goal.get("change")
            if isinstance(change, (int, float)):   # гол не позже смены счёта на табло и не раньше CHANGE_BEFORE до неё
                w = [t for t in w if change - CHANGE_BEFORE - RUN_TOL <= t <= change + RUN_TOL]
                if not w:
                    continue
            if w[-1] - w[0] > RUN_WINDOW:
                continue
            got = {"win": [w[0], w[-1]], "cand": [t for t in w if is_stop(state, t)], "from": a["score"]}
            if len(w) == 1 and (ok or isinstance(change, (int, float))):
                got["t"] = w[0]
            found[goal["score"]] = got
    return {"found": found, "drop": drop, "fail": sorted(bad), "checked": ok, "confirmed": sorted(set(good) - set(drop))}


def check_marks(goals: list[dict], state=None) -> dict[str, dict]:
    """Проверка отметок людей (ADR-033). goals — как у solve; отметка человека — гол с `src: admin` и секундой `t`.
    state — кадры табло (как у solve) или None: тогда только смена счёта. Ответ — счёт → {"t", "status", "for",
    "against"}: «ok» — хоть одна проверка подтвердила, ни одна не опровергла; «conflict» — опровергла хоть одна;
    «unknown» — проверить нечем (табло нет на экране, клуб не размечен). Проверки:
    - часы: в RUN_TOL от отметки часы встали — за; все секунды от RUNNING[0] до RUNNING[1] вокруг часы шли на виду — против;
    - смена счёта этого гола на табло (`change`): за CHANGE_BEFORE до неё — за; отметка позже смены или раньше
      окна — против;
    - ход часов до соседних точных голов периода (как самопроверка solve): сошлось — за, нет — против."""
    out = {}
    per = _periods(goals)
    for g in goals:
        t = g.get("t")
        if g.get("src") != "admin" or not isinstance(t, (int, float)):
            continue
        yes, no = [], []
        if state is not None:
            near = range(int(t) - RUN_TOL, int(t) + RUN_TOL + 1)
            around = [state(x) for x in range(int(t) - RUNNING[0], int(t) + RUNNING[1] + 1)]
            if any(is_stop(state, x) for x in near):
                yes.append("часы встали")
            elif around and all(s == "run" for s in around):
                no.append("часы в это время идут")
        change = g.get("change")
        if isinstance(change, (int, float)):
            if change - CHANGE_BEFORE - RUN_TOL <= t <= change + RUN_TOL:
                yes.append("счёт на табло сменился после")
            elif t > change + RUN_TOL:
                no.append("счёт на табло сменился раньше")
            else:
                no.append(f"счёт на табло сменился через {round(change - t)} с — слишком поздно")
        if state is not None and game_sec(g.get("time")) is not None:
            for other in per.get(str(g.get("period") or ""), []):
                if other is g or not isinstance(other.get("t"), (int, float)) \
                        or game_sec(other.get("time")) == game_sec(g.get("time")):
                    continue
                w = _window(state, other, g)
                if w is None or w and w[-1] - w[0] > RUN_WINDOW:
                    continue
                if w and w[0] - CHECK_TOL <= t <= w[-1] + CHECK_TOL:
                    yes.append(f"ход часов от {other['score']}")
                else:
                    no.append(f"ход часов от {other['score']} не сходится")
        status = "conflict" if no else "ok" if yes else "unknown"
        out[g["score"]] = {"t": int(t), "status": status, "for": yes, "against": no}
    return out

"""Что показать человеку о голе (ADR-036, раздел 1): какие окна записи вырезать и что это за окна.

Общее для бота (`/replay`, споры, «Все голы матча») и пульта (вкладка «Голы», ADR-036, раздел 4): одно правило — одно
окно, и служба cuts режет его один раз, кто бы ни попросил. Вход — то, что о голе известно: отметки людей из
live/replays.json (`marked`), разбор службы clips из live/clips.json (`board`), протокол из league.json и запись лиги.
Выход — план: вид (exact, approx, dispute, search), окна (начало, длина, что это) и секунды для подписи.

Только stdlib, без сети: файлы читает тот, кто зовёт.
"""
import clockrun
import cutjobs
import replay

SITE = "rhl.fhr.ru"               # запись лиги — «Смотреть» от сайта лиги в league.json (ADR-028)
SEARCH_STRETCH = 1.3              # запись между голами периода дольше часов игры: остановки (оценка для поиска)
PERIOD_GAP = 1100                 # с записи на перерыв между периодами (оценка для поиска из другого периода)
PRE_SHOW = 300                    # с записи до вбрасывания: студия, гимн (оценка, когда ни одного гола не нашли)
CANDIDATE_MAX = 3                 # моментов «Гол на …» под видео
JOB_KIND = {"exact": "review", "dispute": "review", "approx": "preview", "search": "search"}


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def league_video(game: dict | None) -> str | None:
    """Запись трансляции лиги из матча league.json: ролик VK из «Смотреть» от rhl.fhr.ru (rhl_media.py, ADR-019,
    раздел 7). Вкладка «Видео» сайта лиги без ролика и ссылки клубов — не запись лиги."""
    for w in (game or {}).get("watch") or []:
        if isinstance(w, dict) and w.get("src") == SITE and isinstance(w.get("url"), str):
            got = replay.parse_link(w["url"])
            if got:
                return got[0]
    return None


def protocol_of(game: dict | None) -> list[dict] | None:
    """Голы протокола матча league.json без буллитов: счёт, команда, период, автор, время. Нет — None."""
    goals = [{"score": x.get("score"), "team": x.get("team"), "period": x.get("period"), "author": x.get("author"),
              "time": x.get("time")}
             for x in (game or {}).get("goals") or [] if isinstance(x, dict) and x.get("period") != "РБ"]
    return goals or None


def goal_estimate(score: str, known: dict[str, int], protocol: list[dict] | None,
                  length: float | None = None) -> int | None:
    """Где гол в записи, если ни табло, ни люди его не нашли: от ближайшего по часам гола с известной секундой —
    разница по протоколу, растянутая на остановки, между периодами — плюс перерыв. Ни одного известного гола — от
    начала записи: студия до вбрасывания, время по протоколу с остановками и перерывы (ADR-036, раздел 1: «доля записи
    по времени протокола»). Оценка для окна поиска, не секунда."""
    info = {x.get("score"): x for x in protocol or []}
    me = info.get(score) or {}
    gs = clockrun.game_sec(me.get("time"))
    if gs is None:
        return None
    best = None
    for s, t in known.items():
        o = info.get(s) or {}
        go = clockrun.game_sec(o.get("time"))
        if go is None:
            continue
        same = str(o.get("period") or "") == str(me.get("period") or "")
        cost = (0 if same else 1, abs(gs - go))
        if best is None or cost < best[0]:
            gap = 0
            if not same and str(me.get("period")).isdigit() and str(o.get("period")).isdigit():
                gap = (int(me["period"]) - int(o["period"])) * PERIOD_GAP
            best = (cost, t + round((gs - go) * SEARCH_STRETCH) + gap)
    if best:
        return max(0, best[1])
    per = int(me["period"]) if str(me.get("period") or "").isdigit() else gs // 1200 + 1
    est = PRE_SHOW + round(gs * SEARCH_STRETCH) + (per - 1) * PERIOD_GAP
    return min(est, int(length)) if length else est


def known_second(x: dict, bg: dict) -> int:
    """Где в записи гол с повтором, для оценки соседних: точный — его секунда; по ходу часов — середина окна; по смене
    счёта — за 45 с до неё (оператор меняет счёт через 0–90 с); расчётный — без запаса GUESS_LEAD."""
    if x.get("exact"):
        return x["t"] + replay.EXACT_LEAD
    if x.get("src") == "win" and isinstance(bg.get("win"), list) and len(bg["win"]) == 2:
        return (int(bg["win"][0]) + int(bg["win"][1])) // 2
    if x.get("src") == "change" and isinstance(bg.get("change"), (int, float)):
        return max(0, int(bg["change"]) - 45)
    return x["t"] + replay.GUESS_LEAD


def plan(key: str, score: str, marked: dict | None, board: dict | None, protocol: list[dict] | None = None,
         video: str | None = None) -> dict | None:
    """Что показать человеку о голе (ADR-036, раздел 1): {"kind", "video", "windows": [(начало, длина, что это)], ...}.
    kind: exact — точная секунда `t` (`src`: человек, часы, табло, ход часов); approx — примерное место, `cand` —
    моменты, когда вставали часы (секунды записи); dispute — обе версии спора; search — где гол, неизвестно: окно около
    оценки. marked — запись матча из live/replays.json, board — из live/clips.json. video — запись лиги из
    league.json: её берём, пока у матча нет ни отметок, ни разбора. Записи у матча нет, она удалена или гола в ней нет
    по словам человека — None: тогда как раньше, просьба прислать ссылку или время."""
    board = board or {}
    entry = replay.with_board(marked, board)
    if entry is None and board.get("status") != "gone":   # табло не размечено или не разобрано — голов нет, запись есть
        own = board.get("video") if board.get("status") in ("ok", "no_board", "wait", "error") else None
        entry = {"video": own or video, "goals": []} if own or video else None
    if not entry or not entry.get("video") or score in set((marked or {}).get("absent") or []):
        return None
    video = entry["video"]
    same = replay.same_video(board.get("video"), video)
    length = board.get("length") if same else None
    bg = ((board.get("goals") or {}).get(score) or {}) if same else {}
    anchors = (marked or {}).get("anchors") or {} if replay.same_video((marked or {}).get("video"), video) else {}
    base = {"video": video, "length": length, "score": score, "key": key}
    if score in replay.disputed(marked, board) and score in anchors:
        c = (board.get("checks") or {}).get(score) or {}
        wins = [(*cutjobs.review_window(anchors[score], length), "по отметке")]
        tb = None
        if _num(bg.get("t")) and not bg.get("off"):
            tb = int(bg["t"])
            wins.append((*cutjobs.review_window(tb, length), "по табло"))
        elif isinstance(bg.get("win"), list) and len(bg["win"]) == 2:
            wins.append((*cutjobs.run_window(bg["win"], length), "по ходу часов"))
        elif _num(bg.get("change")):
            wins.append((*cutjobs.change_window(bg["change"], length), "до смены счёта на табло"))
        return {**base, "kind": "dispute", "windows": wins, "t": anchors[score], "tb": tb,
                "why": ", ".join(c.get("against") or [])}
    r = {x["score"]: x for x in entry.get("goals") or []}.get(score)
    if r and r.get("exact"):
        t = anchors[score] if score in anchors else int(bg["t"]) if _num(bg.get("t")) else r["t"] + replay.EXACT_LEAD
        src = "admin" if score in anchors else r.get("src") or "board"
        return {**base, "kind": "exact", "t": t, "src": src, "windows": [(*cutjobs.review_window(t, length), "гол")]}
    if r:
        cand: list[int] = []
        if r.get("src") == "win" and isinstance(bg.get("win"), list) and len(bg["win"]) == 2:
            w, how = cutjobs.run_window(bg["win"], length), "по ходу часов от соседнего гола"
            cand = [int(x) for x in bg.get("wcand") or [] if _num(x)]
        elif r.get("src") == "change" and _num(bg.get("change")):
            w, how = cutjobs.change_window(bg["change"], length), "до смены счёта на табло"
            ask = bg.get("ask") if isinstance(bg.get("ask"), dict) else {}
            if ask.get("from") == w[0]:
                cand = [w[0] + int(x) for x in ask.get("cand") or [] if _num(x)]
        else:   # расчёт от отметки того же периода по времени сайта лиги
            w, how = cutjobs.search_window(r["t"] + replay.GUESS_LEAD, length), "по времени сайта лиги от соседнего гола"
        return {**base, "kind": "approx", "windows": [(*w, how)], "cand": cand[-CANDIDATE_MAX:]}
    known = {x["score"]: known_second(x, ((board.get("goals") or {}).get(x["score"]) or {}) if same else {})
             for x in entry.get("goals") or [] if _num(x.get("t"))}
    est = goal_estimate(score, known, protocol, length)
    if est is None:
        return None
    return {**base, "kind": "search", "windows": [(*cutjobs.search_window(est, length), "около оценки по протоколу")]}


def shifted(p: dict, steps: int) -> dict | None:
    """План, сдвинутый на steps окон поиска раньше (−) или позже (+): пульт листает запись вокруг примерного места или
    оценки — как «⏪ / ⏩» под видео в боте. Точную секунду и спор не листаем. Дальше записи — None."""
    if not steps or p["kind"] not in ("approx", "search"):
        return p
    s, n, _ = p["windows"][0]
    for _ in range(abs(steps)):
        w = cutjobs.neighbour(s, n, -1 if steps < 0 else 1, p.get("length"))
        if not w:
            return None
        s, n = w
    what = f"на {cutjobs.SEARCH * abs(steps) // 60} мин {'раньше' if steps < 0 else 'позже'}"
    return {**p, "kind": "search", "windows": [(s, n, what)], "cand": []}


def jobs_for(store: cutjobs.CutJobs, p: dict, now, prio: int = cutjobs.URGENT) -> list[int]:
    """Задания службы cuts на окна плана; окна поиска — и соседние, заранее: человек, скорее всего, нажмёт «⏪» или «⏩»."""
    kind = JOB_KIND[p["kind"]]
    jobs = [store.want(now, p["video"], s, n, kind, prio=prio, match=p["key"], score=p["score"])
            for s, n, _ in p["windows"]]
    if p["kind"] in ("approx", "search"):
        s, n, _ = p["windows"][0]
        for step in (-1, 1):
            w = cutjobs.neighbour(s, n, step, p.get("length"))
            if w:
                store.want(now, p["video"], *w, "search", prio=cutjobs.SEND, match=p["key"], score=p["score"])
    return jobs

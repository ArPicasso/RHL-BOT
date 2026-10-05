"""Служба clips на VPS (ADR-030, шаг 2): после матча находит голы по табло трансляции и пишет их секунды в записи
в live/clips.json. Оттуда их берут бот (/replay) и сборка Pages: «Повтор» становится точным без разметки админа.

Раз в EVERY секунд берёт сыгранные матчи последних DAYS дней, у которых есть запись: запись лиги из опубликованного
league.json («Смотреть» от rhl.fhr.ru) или ссылка админа из live/replays.json. Каждый матч — один раз на ролик:
проход по записи пробником табло (tools/probe_scoreboard.py, разметка табло клубов — boards.json), точные голы —
встали часы игры (`clock`) или проверенная задержка табло клуба (`board`). У остальных голов табло знает, какой это
гол, но не секунду: служба режет превью — PREVIEW_BEFORE секунд записи до смены счёта, 360p — и ищет в нём моменты,
когда вставали часы игры. Бот присылает превью админам и помощникам с кнопками на эти моменты (шаг 3). Табло
клуба-хозяина не размечено — кадр с сеткой grid.png для разметки, голов нет.

По одному писателю на файл: live/replays.json пишет только бот, live/clips.json — только эта служба. Качаем как
плеер (yt-dlp), без обхода защиты (ADR-012): VK отказал — пишем ошибку и пробуем позже, не больше TRIES раз.
Кадры прохода и картинки — в probe/scoreboard/<матч>/ (там же, где у пробника), держатся KEEP_DAYS дней.

    venv/bin/python clips.py            # служба
    venv/bin/python clips.py --once     # один проход и выйти
"""
import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))

import probe_cuts as pc  # noqa: E402
import probe_scoreboard as sb  # noqa: E402
import replay  # noqa: E402
import s3  # noqa: E402

TZ = ZoneInfo("Europe/Moscow")
LIVE_DIR = Path(os.environ.get("LIVE_DIR") or ROOT / "live")
WORK = ROOT / "probe" / "scoreboard"
DAYS = 3            # матчи за столько дней, включая сегодня, — как окно /replay
EVERY = 600         # с между проходами
TRIES = 3           # столько раз пробуем матч, который не скачался или упал
KEEP_DAYS = 3       # кадры прохода держим столько дней
VERSION = 2         # разбор поменялся (05.10: голы по порядку протокола) — матчи за DAYS дней разбираем заново
PREVIEW_BEFORE = 120   # с записи до смены счёта на табло в превью: оператор меняет счёт через 0–90 с после гола
PREVIEW_AFTER = 5      # и после смены
PREVIEW_FORMAT = "b[height<=360][height>=240]/b[height<=480]/w"   # превью лёгкое: смотрят в Telegram
CANDIDATES = 3         # кнопок «Гол на …» под превью — последние остановки часов перед сменой счёта

log = logging.getLogger("clips")


def now_msk() -> datetime:
    return datetime.now(TZ)


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def safe_name(key: str) -> str:
    return re.sub(r"[^\w.-]+", "_", key)


def days_back(today: date, n: int = DAYS) -> set[str]:
    return {(today - timedelta(days=k)).isoformat() for k in range(n)}


def pending(league: dict | None, marked: dict, store: dict, today: date) -> list[tuple[str, str]]:
    """Какие матчи разобрать: (ключ, ролик). Сыгранные за DAYS дней с записью лиги и размеченные админом (его
    ролик главнее). Уже разобранный ролик не трогаем; новый ролик у матча — разбираем заново; упавший — до TRIES раз;
    матч без разметки табло — заново, как только табло клуба появилось в boards.json."""
    days = days_back(today)
    found = sb.recorded(league, days)
    for key, e in (marked or {}).items():
        if key[:10] in days and isinstance(e, dict) and isinstance(e.get("video"), str):
            found[key] = {"video": e["video"]}
    out = []
    for key in sorted(found):
        video = found[key]["video"]
        was = store.get(key) or {}
        if replay.same_video(was.get("video"), video) and was.get("v", 1) >= VERSION:
            marked_now = was.get("status") == "no_board" and key.split("|")[1] in sb.BOARDS   # табло разметили
            if not marked_now and (was.get("status") in ("ok", "no_board") or was.get("tries", 0) >= TRIES):
                continue
        out.append((key, video))
    return out


def live_goals(key: str, live_dir: Path = LIVE_DIR) -> dict[str, dict]:
    """Голы матча, как их видела служба live: счёт → команда и период (для сверки с протоколом, ADR-030)."""
    games = read_json(live_dir / f"{key[:10]}.json").get("games") or []
    game = next((g for g in games if isinstance(g, dict) and g.get("key") == key), None)
    return {g["score"]: {"team": g.get("team"), "period": g.get("period")} for g in replay.goals_of(game or {})}


def found_goals(board: list[dict], live: dict[str, dict]) -> dict[str, dict]:
    """goals.json пробника → голы для clips.json: какой гол (счёт), смена табло, секунда и откуда она. Без секунды
    гол тоже записываем: какой это гол, табло знает — по нему будет превью админу (шаг 3)."""
    out = {}
    for g in board:
        score = g.get("score")
        if not (isinstance(score, str) and replay.SCORE_RE.fullmatch(score)):
            continue
        t = g.get("t")
        out[score] = {**live.get(score, {}), "change": round(g["change"]) if g.get("change") is not None else None,
                      "t": round(t) if isinstance(t, (int, float)) else None, "src": g.get("src")}
    return out


def clock_stops(vis: list[tuple[float, bytes]], clock: list[int], gap: float = 2) -> list[float]:
    """Когда вставали часы игры: последняя секунда, когда часы шли, перед секундой, когда они уже стоят (как
    clock_stop пробника, ADR-029). vis — кадры с табло подряд, кадр в секунду; разрыв больше gap — не смотрим."""
    out = []
    for (ta, fa), (tb, fb), (tc, fc) in zip(vis, vis[1:], vis[2:]):
        if (tb - ta <= gap and tc - tb <= gap and len(sb.moved(fa, fb, clock)) >= sb.CLOCK_MOVED
                and len(sb.moved(fb, fc, clock)) < sb.CLOCK_MOVED):
            out.append(ta)
    return out


def preview_window(change: float, length: float | None = None) -> tuple[int, int]:
    """Окно превью: (начало, длина) в секундах записи — до смены счёта на табло и чуть после."""
    start = max(0, int(change) - PREVIEW_BEFORE)
    end = int(change) + PREVIEW_AFTER
    if length:
        end = min(end, int(length))
    return start, max(1, end - start)


def preview_cmd(src: str, headers: dict | None, start: int, length: int, path: Path) -> list[str]:
    """ffmpeg: превью гола — перекодировано, чтобы нулевая секунда превью была ровно start: кнопки «Гол на 0:47»
    считают от неё."""
    return [sb.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *sb.header_args(headers), "-ss", str(start),
            "-i", src, "-t", str(length), "-vf", "scale=-2:360", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "30", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(path)]


def add_previews(key: str, video: str, goals: dict[str, dict], length: float | None, out: Path) -> None:
    """Превью голам, у которых табло знает смену счёта, но не секунду: файл и моменты остановки часов (`ask`)."""
    need = {s: g for s, g in goals.items() if g.get("t") is None and g.get("change") is not None}
    if not need:
        return
    club = key.split("|")[1]
    board = sb.BOARDS.get(club) or {}
    src480, h480, _ = sb.stream_of(video)
    src360, h360, _ = sb.stream_of(video, PREVIEW_FORMAT)
    for score, g in need.items():
        start, span = preview_window(g["change"], length)
        path = out / f"preview_{score.replace(':', '-')}.mp4"
        cand: list[int] = []
        if board.get("clock"):
            dense = sb.safe_scan(src480, h480, sb.BOXES[club], start, start + span)
            model = sb.name_model([f for _, f in dense], board["name"]) if dense else None
            vis = [(t, f) for t, f in dense if not model or sb.on_screen(f, model)]
            stops = [t for t in clock_stops(vis, sb.cell_pixels(board["clock"])) if t <= g["change"]]
            cand = [round(t - start) for t in stops[-CANDIDATES:]]
        try:
            run = subprocess.run(preview_cmd(src360, h360, start, span, path), capture_output=True, text=True,
                                 timeout=600)
            ok = run.returncode == 0 and path.exists() and path.stat().st_size > 0
        except subprocess.TimeoutExpired:
            ok = False
        if not ok:
            log.warning("%s %s: превью не вырезалось", key, score)
            continue
        g["ask"] = {"from": start, "len": span, "file": str(path.relative_to(ROOT)), "cand": cand}
        log.info("%s %s: превью %s, моментов часов %d", key, score, replay.fmt_t(start), len(cand))


def protocol_order(league: dict | None, key: str) -> list[tuple[str, str, str]]:
    """Голы протокола матча из league.json по порядку, без буллитов: (счёт, команда, период). Нужны, когда служба
    live не записала времени голов (03.10): тогда смены табло сопоставляем с голами по порядку."""
    day, home, away = key.split("|")
    g = next((g for g in (league or {}).get("games") or []
              if isinstance(g, dict) and (g.get("date"), g.get("home"), g.get("away")) == (day, home, away)), None)
    return [(x["score"], x.get("team"), str(x.get("period") or "")) for x in (g or {}).get("goals") or []
            if isinstance(x, dict) and x.get("period") != "РБ" and isinstance(x.get("score"), str)]


def scan_match(key: str, video: str, anchors: dict, order: list[tuple[str, str, str]] | None = None) -> dict:
    """Один матч: проход по записи и голы по табло. Исключения (VK не отдал, ffmpeg упал) — наверх."""
    club = key.split("|")[1]
    src, headers, length = sb.stream_of(video)
    out = WORK / safe_name(key)
    out.mkdir(parents=True, exist_ok=True)
    if club not in sb.BOARDS:
        sb.grid_sheet(src, headers, length, out / "grid.png")
        log.info("%s: табло клуба %s не размечено — grid.png для разметки в boards.json", key, club)
        return {"status": "no_board", "goals": {}}
    args = SimpleNamespace(out=WORK, step=sb.STEP, start=0, end=None, rescan=False,
                           order=[(score, team) for score, team, _ in order or []])
    truth = {s: t for s, t in (anchors or {}).items() if isinstance(t, int)}
    sb.probe(key, src, headers, truth, sb.site_goals(LIVE_DIR, key), sb.BOXES[club], args)
    board = read_json(out / "goals.json").get("goals") or []
    live = live_goals(key) or {score: {"team": team, "period": per} for score, team, per in order or []}
    goals = found_goals(board, live)
    try:
        add_previews(key, video, goals, length, out)
    except Exception as err:   # без превью голы всё равно записываем: секунды табло уже есть
        log.warning("%s: превью не сделали — %s: %s", key, type(err).__name__, err)
    return {"status": "ok", "goals": goals}


def run_pass(store: dict, league: dict | None, marked: dict, now: datetime, scan=scan_match) -> int:
    """Один проход: разбирает ждущие матчи по одному и после каждого пишет clips.json. Возвращает, сколько разобрано."""
    games = store.setdefault("games", {})
    todo = pending(league, marked, games, now.date())
    for key, video in todo:
        was = games.get(key) or {}
        same = replay.same_video(was.get("video"), video) and was.get("v", 1) >= VERSION
        tries = (was.get("tries", 0) if same else 0) + 1
        log.info("%s: разбираю %s (попытка %d)", key, video, tries)
        try:
            got = scan(key, video, ((marked or {}).get(key) or {}).get("anchors") or {}, protocol_order(league, key))
        except Exception as err:   # VK не отдал, ffmpeg упал — дальше не ломимся (ADR-012), попробуем в другой проход
            log.warning("%s: не разобрали — %s: %s", key, type(err).__name__, err)
            got = {"status": "error", "error": f"{type(err).__name__}: {err}"[:300], "goals": was.get("goals") or {}}
        games[key] = {"video": video, "v": VERSION, "tries": tries, "scanned": now_msk().isoformat(timespec="seconds"),
                      **got, "clips": was.get("clips") or {}}   # выложенные клипы остаются: нарезка сверит их сама
        timed = sum(1 for g in games[key]["goals"].values() if g.get("t") is not None)
        log.info("%s: %s, голов по табло %d, с секундой %d", key, got["status"], len(games[key]["goals"]), timed)
        store["updated"] = now_msk().isoformat(timespec="seconds")
        write_atomic(LIVE_DIR / "clips.json", store)
    old = days_back(now.date(), KEEP_DAYS + DAYS)
    for key in [k for k in games if k[:10] < min(old)]:
        games.pop(key)
    return len(todo)


# ---------- клипы (шаг 6) ----------
# Гол с точной секундой (админ, часы, табло) и протоколом — клип 30 с со знаком «Навигатор РХЛ» и обложка в бакет S3.
# Протокол нужен: только по нему видно, что ни автор, ни ассистенты не скрыты по просьбе. Кто забил, к клипу не
# пришиваем — это делает сборка; клип помнит счёт, команду и секунду, по ним его сверяют с протоколом.


def league_goals(league: dict | None, key: str) -> dict[str, dict]:
    """Голы протокола матча из league.json: счёт → гол (команда, автор, ассистенты). Буллиты не берём."""
    day, home, away = key.split("|")
    g = next((g for g in (league or {}).get("games") or []
              if isinstance(g, dict) and (g.get("date"), g.get("home"), g.get("away")) == (day, home, away)), None)
    return {x["score"]: x for x in (g or {}).get("goals") or []
            if isinstance(x, dict) and x.get("period") != "РБ" and isinstance(x.get("score"), str)}


def hidden_goal(x: dict) -> bool:
    """На клипе виден человек: скрытый по просьбе автор или ассистент — клипа нет (ADR-029, ADR-030)."""
    return x.get("author") == pc.HIDDEN_NAME or pc.HIDDEN_NAME in (x.get("assists") or [])


def goal_seconds(game: dict, admin: dict | None) -> dict[str, tuple[int, str]]:
    """Точная секунда каждого гола в ролике службы: отметка админа (если ролик тот же) главнее часов и табло."""
    out = {s: (int(g["t"]), g.get("src") or "board") for s, g in (game.get("goals") or {}).items()
           if isinstance(g, dict) and isinstance(g.get("t"), (int, float))}
    if admin and replay.same_video(admin.get("video"), game.get("video")):
        out.update({s: (int(t), "admin") for s, t in (admin.get("anchors") or {}).items() if isinstance(t, int)})
    return out


def clip_plan(game: dict, admin: dict | None, protocol: dict[str, dict]) -> tuple[list[tuple[str, int, str]], list[str]]:
    """Что резать и что убрать: ([(счёт, секунда, откуда)], [счёт клипа к удалению]). Режем гол с секундой и
    протоколом, без скрытых, если клипа нет или секунда поменялась. Убираем клип скрытого игрока и гол, которого
    в протоколе нет или он другой команды (лига отменила гол — счета сдвинулись)."""
    have = game.get("clips") or {}
    cut, drop = [], []
    for score, (t, src) in sorted(goal_seconds(game, admin).items(), key=lambda x: x[1][0]):
        x = protocol.get(score)
        if not x or hidden_goal(x):
            continue
        if (have.get(score) or {}).get("t") != t:
            cut.append((score, t, src))
    if protocol:
        for score, c in have.items():
            x = protocol.get(score)
            if not x or hidden_goal(x) or (c.get("team") and x.get("team") != c.get("team")):
                drop.append(score)
    return cut, drop


def clip_names(key: str, score: str, t: int) -> tuple[str, str]:
    """Имена файлов в бакете: секунда в имени — поправили секунду, появился новый файл (кэш не мешает)."""
    day, home, away = key.split("|")
    base = f"clips/{day}/{home}_{away}/{score.replace(':', '-')}-{t}"
    return base + ".mp4", base + ".jpg"


def poster_cmd(src: str, headers: dict | None, t: int, path: Path) -> list[str]:
    return [sb.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *sb.header_args(headers), "-ss", str(t),
            "-i", src, "-frames:v", "1", "-vf", "scale=-2:720", "-q:v", "4", str(path)]


def cut_goal(src: str, headers: dict | None, t: int, out: Path, score: str, mark: tuple) -> tuple[Path, Path, float]:
    """Клип вокруг секунды гола (20 до, 10 после, знак лиги) и обложка в секунду гола. Не вышло — исключение."""
    w = pc.windows({score: t})[0]
    clip, poster = out / f"clip_{w['file']}", out / f"poster_{score.replace(':', '-')}.jpg"
    for cmd in (pc.cut_cmd(src, headers, w["start"], w["length"], clip, mark), poster_cmd(src, headers, t, poster)):
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if run.returncode != 0:
            raise RuntimeError((run.stderr.strip().splitlines() or ["ffmpeg без ошибки"])[-1][:200])
    return clip, poster, pc.duration(clip) or float(w["length"])


def cut_pass(store: dict, league: dict | None, marked: dict, bucket: s3.Store, cut=cut_goal, stream=sb.stream_of) -> int:
    """Нарезка: у каждого разобранного матча — клипы голов с секундой и протоколом, выкладка в бакет, удаление
    клипов скрытых и отменённых голов. После каждого матча — запись clips.json. Возвращает число новых клипов."""
    if not bucket.ok:
        return 0
    n = 0
    for key, game in sorted((store.get("games") or {}).items()):
        if not isinstance(game, dict) or not game.get("video"):
            continue
        protocol = league_goals(league, key)
        todo, drop = clip_plan(game, (marked or {}).get(key), protocol)
        if not todo and not drop:
            continue
        clips_ = game.setdefault("clips", {})
        for score in drop:
            for name in clips_.pop(score, {}).get("files") or []:
                try:
                    bucket.delete(name)
                except Exception as err:
                    log.warning("%s %s: не удалили %s — %s", key, score, name, err)
            log.info("%s %s: клип убран (скрыт по просьбе или гола нет в протоколе)", key, score)
        if todo:
            out = WORK / safe_name(key)
            out.mkdir(parents=True, exist_ok=True)
            (out / "mark.txt").write_text(pc.MARK, encoding="utf-8")
            (out / "source.txt").write_text(pc.SOURCE, encoding="utf-8")
            mark = (out / "mark.txt", out / "source.txt", pc.font_file())
            try:
                src, headers, _ = stream(game["video"], pc.FORMAT)
            except Exception as err:   # VK не отдал — в следующий проход (ADR-012)
                log.warning("%s: поток для клипов не получили — %s: %s", key, type(err).__name__, err)
                todo = []
            for score, t, how in todo:
                try:
                    clip, poster, dur = cut(src, headers, t, out, score, mark)
                    mp4_name, jpg_name = clip_names(key, score, t)
                    mp4 = bucket.put(mp4_name, clip.read_bytes(), "video/mp4")
                    jpg = bucket.put(jpg_name, poster.read_bytes(), "image/jpeg")
                except Exception as err:
                    log.warning("%s %s: клип не вышел — %s: %s", key, score, type(err).__name__, err)
                    continue
                for name in (clips_.get(score) or {}).get("files") or []:   # прежняя секунда — старые файлы
                    try:
                        bucket.delete(name)
                    except Exception:
                        pass
                x = protocol.get(score) or {}
                clips_[score] = {"t": t, "src": how, "team": x.get("team"), "period": x.get("period"), "mp4": mp4,
                                 "poster": jpg, "dur": round(dur, 1), "files": [mp4_name, jpg_name],
                                 "cut": now_msk().isoformat(timespec="seconds")}
                for f in (clip, poster):
                    f.unlink(missing_ok=True)
                n += 1
                log.info("%s %s: клип %s (%s)", key, score, replay.fmt_t(t), how)
        store["updated"] = now_msk().isoformat(timespec="seconds")
        write_atomic(LIVE_DIR / "clips.json", store)
    return n


def clean_work(today: date, root: Path = WORK) -> None:
    """Кадры прохода старше KEEP_DAYS дней — удалить: на сервере держим только временное (ADR-030)."""
    if not root.is_dir():
        return
    edge = (today - timedelta(days=KEEP_DAYS)).isoformat()
    for d in root.iterdir():
        if d.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}_", d.name) and d.name[:10] < edge:
            shutil.rmtree(d, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Голы по табло трансляции после матча (ADR-030)")
    ap.add_argument("--once", action="store_true", help="один проход и выйти")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if not shutil.which("ffmpeg"):
        sys.exit("Нет ffmpeg: apt install -y ffmpeg")
    bucket = s3.Store()
    if not bucket.ok:
        log.info("ключей хранилища нет (CLIPS_S3_KEY, CLIPS_S3_SECRET в /etc/rhl/bot.env) — клипы не режем")
    while True:
        now = now_msk()
        store = read_json(LIVE_DIR / "clips.json")
        marked = read_json(LIVE_DIR / "replays.json").get("games") or {}
        league = sb.league_json(None)
        try:
            # сначала клипы того, что уже разобрано: они быстрые, а проход по новой записи — минуты, и перезапуск
            # службы (выкладка) посреди него не должен задерживать клипы (05.10 так и не дошло до нарезки)
            cut = cut_pass(store, league, marked, bucket)
            n = run_pass(store, league, marked, now)
            if n:
                log.info("проход: разобрано матчей %d", n)
                cut += cut_pass(store, league, marked, bucket)
            if cut:
                log.info("проход: новых клипов %d", cut)
            clean_work(now.date())
        except Exception:   # служба не падает из-за одного прохода: следующий через EVERY
            log.exception("проход упал")
        if args.once:
            return
        time.sleep(EVERY)


if __name__ == "__main__":
    main()

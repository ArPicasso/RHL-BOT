"""Служба clips на VPS (ADR-030, шаг 2): после матча находит голы по табло трансляции и пишет их секунды в записи
в live/clips.json. Оттуда их берут бот (/replay) и сборка Pages: «Повтор» становится точным без разметки админа.

Раз в EVERY секунд берёт сыгранные матчи последних DAYS дней, у которых есть запись: запись лиги из опубликованного
league.json («Смотреть» от rhl.fhr.ru) или ссылка админа из live/replays.json. Каждый матч — один раз на ролик:
проход по записи пробником табло (tools/probe_scoreboard.py, разметка табло клубов — boards.json), точные голы —
встали часы игры (`clock`) или проверенная задержка табло клуба (`board`). Остальные голы ждут превью админу
(шаг 3). Табло клуба-хозяина не размечено — кадр с сеткой grid.png для разметки, голов нет.

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
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))

import replay  # noqa: E402
import probe_scoreboard as sb  # noqa: E402

TZ = ZoneInfo("Europe/Moscow")
LIVE_DIR = Path(os.environ.get("LIVE_DIR") or ROOT / "live")
WORK = ROOT / "probe" / "scoreboard"
DAYS = 3            # матчи за столько дней, включая сегодня, — как окно /replay
EVERY = 600         # с между проходами
TRIES = 3           # столько раз пробуем матч, который не скачался или упал
KEEP_DAYS = 3       # кадры прохода держим столько дней

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
    ролик главнее). Уже разобранный ролик не трогаем; новый ролик у матча — разбираем заново; упавший — до TRIES раз."""
    days = days_back(today)
    found = sb.recorded(league, days)
    for key, e in (marked or {}).items():
        if key[:10] in days and isinstance(e, dict) and isinstance(e.get("video"), str):
            found[key] = {"video": e["video"]}
    out = []
    for key in sorted(found):
        video = found[key]["video"]
        was = store.get(key) or {}
        if replay.same_video(was.get("video"), video):
            if was.get("status") in ("ok", "no_board") or was.get("tries", 0) >= TRIES:
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


def scan_match(key: str, video: str, anchors: dict) -> dict:
    """Один матч: проход по записи и голы по табло. Исключения (VK не отдал, ffmpeg упал) — наверх."""
    club = key.split("|")[1]
    src, headers, length = sb.stream_of(video)
    out = WORK / safe_name(key)
    out.mkdir(parents=True, exist_ok=True)
    if club not in sb.BOARDS:
        sb.grid_sheet(src, headers, length, out / "grid.png")
        log.info("%s: табло клуба %s не размечено — grid.png для разметки в boards.json", key, club)
        return {"status": "no_board", "goals": {}}
    args = SimpleNamespace(out=WORK, step=sb.STEP, start=0, end=None, rescan=False)
    truth = {s: t for s, t in (anchors or {}).items() if isinstance(t, int)}
    sb.probe(key, src, headers, truth, sb.site_goals(LIVE_DIR, key), sb.BOXES[club], args)
    board = read_json(out / "goals.json").get("goals") or []
    return {"status": "ok", "goals": found_goals(board, live_goals(key))}


def run_pass(store: dict, league: dict | None, marked: dict, now: datetime, scan=scan_match) -> int:
    """Один проход: разбирает ждущие матчи по одному и после каждого пишет clips.json. Возвращает, сколько разобрано."""
    games = store.setdefault("games", {})
    todo = pending(league, marked, games, now.date())
    for key, video in todo:
        was = games.get(key) or {}
        tries = (was.get("tries", 0) if replay.same_video(was.get("video"), video) else 0) + 1
        log.info("%s: разбираю %s (попытка %d)", key, video, tries)
        try:
            got = scan(key, video, ((marked or {}).get(key) or {}).get("anchors") or {})
        except Exception as err:   # VK не отдал, ffmpeg упал — дальше не ломимся (ADR-012), попробуем в другой проход
            log.warning("%s: не разобрали — %s: %s", key, type(err).__name__, err)
            got = {"status": "error", "error": f"{type(err).__name__}: {err}"[:300], "goals": was.get("goals") or {}}
        games[key] = {"video": video, "tries": tries, "scanned": now_msk().isoformat(timespec="seconds"), **got}
        timed = sum(1 for g in games[key]["goals"].values() if g.get("t") is not None)
        log.info("%s: %s, голов по табло %d, с секундой %d", key, got["status"], len(games[key]["goals"]), timed)
        store["updated"] = now_msk().isoformat(timespec="seconds")
        write_atomic(LIVE_DIR / "clips.json", store)
    old = days_back(now.date(), KEEP_DAYS + DAYS)
    for key in [k for k in games if k[:10] < min(old)]:
        games.pop(key)
    return len(todo)


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
    while True:
        now = now_msk()
        store = read_json(LIVE_DIR / "clips.json")
        marked = read_json(LIVE_DIR / "replays.json").get("games") or {}
        league = sb.league_json(None)
        try:
            n = run_pass(store, league, marked, now)
            if n:
                log.info("проход: разобрано матчей %d", n)
            clean_work(now.date())
        except Exception:   # служба не падает из-за одного прохода: следующий через EVERY
            log.exception("проход упал")
        if args.once:
            return
        time.sleep(EVERY)


if __name__ == "__main__":
    main()

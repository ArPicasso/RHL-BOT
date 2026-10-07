"""Служба cuts на VPS (ADR-036, раздел 2): вырезает кусок записи матча в mp4 — видео для админов и помощников.

Задания — таблица `cut_jobs` в state.db (cutjobs.py): ставят служба clips (превью голов без секунды и заготовки после
прохода), бот и API (видео по запросу); служба берёт их по одному — первыми те, которые ждёт человек. Отдельно от
clips, потому что разбор записи — минуты, а видео человеку нужно через секунды.

Видео — 480p, как кадры разбора табло (счёт и часы читаются), H.264 с `+faststart`, со знаком «Навигатор РХЛ» и
«Источник: РХЛ», как у клипов: видео может уйти дальше чата админов. Перекодируем, поэтому нулевая секунда файла —
ровно начало окна: время в видео («Гол на 0:47») бот переводит в секунду записи сложением.

VK: адрес потока (yt-dlp, как плеер) — один на ролик на STREAM_TTL, а не на каждый кусок; не отдал — ошибка задания,
повтор по правилам очереди и счётчик на пульте. Защиту не обходим (ADR-012). Файлы — media/cuts/<номер задания>.mp4,
живут столько же, сколько задание (cutjobs.KEEP_DAYS с последней просьбы): чистит служба раз в час.

Пульт: пульс и счётчики дня в status/cuts.json (`admin.Tracker`) — сколько вырезано, сколько не вышло, отдал ли VK
запись, очередь.

    venv/bin/python cuts.py            # служба
    venv/bin/python cuts.py --once     # выполнить очередь и выйти
"""
import argparse
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))

import admin  # noqa: E402
import clips  # noqa: E402
import cutjobs  # noqa: E402
import probe_cuts as pc  # noqa: E402
import probe_scoreboard as sb  # noqa: E402

TZ = ZoneInfo("Europe/Moscow")
STATE_DB = Path(os.environ.get("STATE_DB") or ROOT / "state.db")
FORMAT = sb.FORMAT   # 480p — та же запись, по которой служба clips считала секунды
HEIGHT = 480
CRF = 28             # 30 с — 2–3 МБ, две минуты — 8–10 МБ: уходит в Telegram через туннель без ожидания
POLL = 2             # с между взглядами в пустую очередь: видео ждёт человек
BEAT = 60            # с между записями пульса, пока очередь пуста
STREAM_TTL = timedelta(minutes=25)   # адрес потока VK живёт около получаса
CLEAN_EVERY = timedelta(hours=1)
TIMEOUT = 900        # с на одно окно: три минуты записи с перекодированием — до минуты
MIN_DUR = 15         # с: файл короче min(окно, MIN_DUR) — поток оборвался, такое не отдаём

log = logging.getLogger("cuts")


def now_msk() -> datetime:
    return datetime.now(TZ)


class Streams:
    """Адрес потока ролика на STREAM_TTL: кусок за куском одного матча не спрашивают VK заново. fetch — как
    clips.stream (отказ — VkError)."""

    def __init__(self, fetch=None, clock=now_msk):
        self.fetch = fetch or (lambda video: clips.stream(video, FORMAT))
        self.clock = clock
        self.cache: dict[str, tuple[datetime, tuple]] = {}

    def get(self, video: str) -> tuple[tuple, bool]:
        """(адрес, заголовки, длина записи) и свежий ли он: взят только что — True."""
        key = cutjobs.vid(video)
        hit = self.cache.get(key)
        if hit and self.clock() - hit[0] < STREAM_TTL:
            return hit[1], False
        got = self.fetch(video)
        self.cache[key] = (self.clock(), got)
        return got, True

    def drop(self, video: str) -> None:
        self.cache.pop(cutjobs.vid(video), None)


def mark_files(out: Path) -> tuple:
    """Знак «Навигатор РХЛ» и «Источник: РХЛ» для ffmpeg: тексты — файлами рядом с видео, шрифт с кириллицей."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "mark.txt").write_text(pc.MARK, encoding="utf-8")
    (out / "source.txt").write_text(pc.SOURCE, encoding="utf-8")
    return out / "mark.txt", out / "source.txt", pc.font_file()


def ffmpeg_cut(src: str, headers: dict | None, start: int, length: int, path: Path, mark: tuple) -> str:
    """Окно записи → mp4 480p со знаком. Пусто — вышло, иначе — последняя строка ошибки ffmpeg."""
    try:
        run = subprocess.run(pc.cut_cmd(src, headers, start, length, path, mark, height=HEIGHT, crf=CRF),
                             capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"ffmpeg не уложился в {TIMEOUT} с"
    if run.returncode != 0 or not path.is_file() or path.stat().st_size == 0:
        return ((run.stderr or "").strip().splitlines() or ["ffmpeg не записал файл"])[-1][:200]
    return ""


def what(job: dict) -> str:
    """Задание для журнала: вид, матч и гол."""
    return " ".join(str(x) for x in (job.get("kind"), job.get("match"), job.get("score")) if x)


def video_info(path: Path) -> dict:
    """Ширина, высота и длина готового видео (ffprobe). Telegram сам их у видео от бота не читает: без них видео в
    чате — «0:01» без перемотки (05.10)."""
    try:
        run = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=width,height:format=duration", "-of", "json", str(path)],
                             capture_output=True, text=True, timeout=60)
        data = json.loads(run.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        return {"w": int(stream["width"]), "h": int(stream["height"]), "dur": round(float(data["format"]["duration"]))}
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return {}


def run_job(job: dict, jobs: cutjobs.CutJobs, streams: Streams, mark: tuple, track: "admin.Tracker | None" = None,
            cut=ffmpeg_cut, info=video_info, root: Path = ROOT) -> bool:
    """Одно задание: поток ролика, нарезка, проверка длины, файл на место. Вышло — True."""
    began = time.monotonic()
    rel = f"{cutjobs.DIR}/{job['id']}.mp4"
    path = root / rel
    tmp = path.with_name(f"{job['id']}.part.mp4")
    path.parent.mkdir(parents=True, exist_ok=True)
    err = ""
    for _ in range(2):
        try:
            (src, headers, length), fresh = streams.get(job["video"])
        except Exception as e:   # VK не отдал запись — ошибка задания, повтор по правилам очереди (ADR-012)
            clips.vk_note(track, e)
            err = f"VK не отдал запись: {e}"
            break
        if fresh:
            clips.vk_note(track, None)
        if length and job["start"] >= length:
            err = f"окно с {job['start']} с — за концом записи ({round(length)} с)"
            break
        err = cut(src, headers, job["start"], job["len"], tmp, mark)
        if not err or fresh:
            break
        streams.drop(job["video"])   # адрес из кэша мог протухнуть — ещё раз со свежим
        log.info("задание %s: со старым адресом потока не вышло (%s) — беру новый", job["id"], err)
    if not err:
        got = info(tmp)
        if not got or got.get("dur", 0) < min(job["len"], MIN_DUR):
            err = f"вышло {got.get('dur') if got else '?'} с вместо {job['len']}"
    now = now_msk()
    if err:
        tmp.unlink(missing_ok=True)
        final = jobs.fail(job["id"], now, err)
        log.warning("задание %s (%s): %s%s", job["id"], what(job), err, " — больше не пробуем" if final else "")
        if track is not None:
            track.add("cut_fail")
            track.info(cut_error=admin.no_ids(err)[:200], cut_fail=admin.iso(now))
        return False
    tmp.replace(path)
    jobs.done(job["id"], now, rel, got.get("w"), got.get("h"), got.get("dur"))
    took = round(time.monotonic() - began)
    log.info("задание %s (%s): %s с записи с %s за %d с", job["id"], what(job), job["len"], job["start"], took)
    if track is not None:
        track.add("cuts")
        track.add("cut_s", took)
        track.info(cut_ok=admin.iso(now))
    return True


def clean(jobs: cutjobs.CutJobs, now: datetime, root: Path = ROOT) -> int:
    """Забытые задания — из таблицы, их файлы — с диска; файлы без задания (упало посреди записи) — тоже."""
    gone = jobs.forget(now)
    for rel in gone:
        (root / rel).unlink(missing_ok=True)
    d = root / cutjobs.DIR
    if d.is_dir():
        edge = (now - timedelta(days=cutjobs.KEEP_DAYS + 1)).timestamp()
        for f in d.glob("*.mp4"):
            if f.stat().st_mtime < edge:
                f.unlink(missing_ok=True)
    return len(gone)


def open_db(path: Path) -> cutjobs.CutJobs:
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return cutjobs.CutJobs(conn, ROOT)


def main() -> None:
    ap = argparse.ArgumentParser(description="Куски записи матча для админов (ADR-036)")
    ap.add_argument("--once", action="store_true", help="выполнить очередь и выйти")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if not shutil.which("ffmpeg"):
        sys.exit("Нет ffmpeg: apt install -y ffmpeg")
    track = admin.Tracker("cuts")
    logging.getLogger().addHandler(admin.ErrorCount(track))
    jobs = open_db(STATE_DB)
    n = jobs.reset_work()
    if n:
        log.info("служба перезапустилась посреди нарезки: %d заданий — снова в очередь", n)
    for f in (ROOT / cutjobs.DIR).glob("*.part.mp4"):
        f.unlink(missing_ok=True)
    mark = mark_files(ROOT / cutjobs.DIR)
    streams = Streams()
    cleaned, beat = now_msk() - CLEAN_EVERY, 0.0
    while True:
        now = now_msk()
        job = None
        try:
            if now - cleaned >= CLEAN_EVERY:
                n = clean(jobs, now)
                if n:
                    log.info("чистка: забыто заданий %d", n)
                cleaned = now
            job = jobs.take(now)
            if job:
                track.info(job={"kind": job["kind"], "len": job["len"], "prio": job["prio"]})
                run_job(job, jobs, streams, mark, track)
                track.info(job=None)
        except Exception:   # служба не падает из-за одного задания
            log.exception("задание %s упало", (job or {}).get("id"))
            if job:
                try:
                    jobs.fail(job["id"], now_msk(), "служба упала посреди задания")
                except Exception:
                    log.exception("и ошибку не записали")
        if job or time.monotonic() - beat >= BEAT:
            try:
                track.info(queue=jobs.counts())
            except Exception:
                log.exception("очередь не прочиталась")
            track.flush()
            beat = time.monotonic()
        if job:
            continue
        if args.once:
            return
        time.sleep(POLL)


if __name__ == "__main__":
    main()

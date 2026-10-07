"""Нарезка голов (ADR-029): клип вокруг каждого гола, у которого известна секунда, с водяным знаком «Навигатор
РХЛ» и источником «РХЛ». Запускать на VPS (нужны ffmpeg и yt-dlp, как для probe_scoreboard.py):

    cd /opt/rhl && venv/bin/python tools/probe_cuts.py                          # размеченные матчи
    venv/bin/python tools/probe_cuts.py --days 3                                # и все сыгранные за 3 дня
    venv/bin/python tools/probe_cuts.py --match '2026-10-04|tverichi|metallurg'
    venv/bin/python tools/probe_cuts.py --before 20 --after 40                  # длиннее: с повтором из трансляции
    venv/bin/python tools/probe_cuts.py --copy                                  # без знака и без перекодирования

Секунда гола: опора админа из live/replays.json, а нет её — время по табло из goals.json пробника табло
(probe_scoreboard.py: остановка часов или проверенная задержка табло клуба). Гол без точного времени не режем:
клип мимо гола хуже, чем никакого, — такой гол ждёт отметки админа (превью, решение 05.10).

Лига разрешила нарезки при двух условиях (владелец, 05.10; документ у владельца): водяной знак нашего сервиса и
источник — РХЛ. Знак — эмблема и «Навигатор РХЛ», под ним «Источник: РХЛ», справа снизу: табло трансляции слева
сверху его не закрывает. Со знаком клип перекодируется (развилка 2Б), 720p. Голы игроков, скрытых по просьбе
(«Игрок скрыт» в league.json), не режем.

Окно — CLIP_BEFORE секунд до гола и CLIP_AFTER после. Качаем как плеер (yt-dlp), только окно клипа, без обхода
защиты: VK закроет или попросит капчу — останавливаемся (ADR-012). Клипы — probe/cuts/<матч>/<счёт>.mp4 и
clips.json (счёт, автор, откуда секунда), не в git.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import replay  # noqa: E402
import probe_scoreboard as sb  # noqa: E402
from probe_scoreboard import ffmpeg, header_args, parse_truth, stream_of  # noqa: E402

CLIP_BEFORE = 20        # с до гола в клипе: видно атаку
CLIP_AFTER = 10         # с после гола: бросок, шайба в сетке, радость
FORMAT = "b[height<=720][height>=360]/b[height<=720]/b"   # 720p: на телефоне смотрится, 30 с — 5–10 МБ
MARK = "Навигатор РХЛ"  # водяной знак сервиса — условие лиги
SOURCE = "Источник: РХЛ"   # источник видео — условие лиги
ICON = ROOT / "webapp" / "brand" / "icon-512.png"
HIDDEN_NAME = "Игрок скрыт"   # так build_data.py пишет автора гола, скрытого по просьбе (ADR-007)


def windows(goals: dict[str, int], before: int = CLIP_BEFORE, after: int = CLIP_AFTER) -> list[dict]:
    """Секунды голов (счёт → секунда записи) → окна клипов по порядку голов: счёт, секунда гола, начало, длина,
    имя файла. Начало не раньше начала записи."""
    out = []
    good = {s: t for s, t in goals.items() if isinstance(t, (int, float)) and replay.SCORE_RE.fullmatch(s)}
    for score, t in sorted(good.items(), key=lambda x: x[1]):
        t = int(round(t))
        start = max(0, t - before)
        out.append({"score": score, "t": t, "start": start, "length": t + after - start,
                    "file": score.replace(":", "-") + ".mp4"})
    return out


def goal_seconds(anchors: dict[str, int], board: list[dict]) -> tuple[dict[str, int], dict[str, str]]:
    """Секунда каждого гола и откуда она: опора админа главнее, дальше — время по табло (goals.json пробника,
    только с «t»). (счёт → секунда, счёт → «admin»/«clock»/«board»)."""
    sec, src = {}, {}
    for g in board:
        if g.get("t") is not None and g.get("score"):
            sec[g["score"]], src[g["score"]] = g["t"], g.get("src") or "board"
    for s, t in anchors.items():
        if isinstance(t, int):
            sec[s], src[s] = t, "admin"
    return sec, src


def authors(league: dict | None, key: str) -> dict[str, str]:
    """Автор каждого гола матча из протокола в league.json: счёт → имя («Игрок скрыт» — скрыт по просьбе)."""
    day, home, away = key.split("|")
    g = next((g for g in (league or {}).get("games") or []
              if isinstance(g, dict) and (g.get("date"), g.get("home"), g.get("away")) == (day, home, away)), None)
    return {x.get("score"): x.get("author") or "" for x in (g or {}).get("goals") or [] if isinstance(x, dict)}


def font_file() -> str:
    """Шрифт с кириллицей для знака: Onest, если стоит (DESIGN.md), иначе DejaVu Sans Bold — он есть всегда,
    его тянет fontconfig вместе с ffmpeg."""
    for name in ("Onest:bold", "DejaVu Sans:bold"):
        try:
            path = subprocess.run(["fc-match", "-f", "%{file}", name], capture_output=True, text=True,
                                  timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            path = ""
        if path and Path(path).exists() and name.split(":")[0].split()[0].lower() in path.lower():
            return path
    return "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def mark_filter(mark_file: Path, source_file: Path, font: str, height: int = 720) -> str:
    """Кадр — к height (720p у клипа); справа снизу эмблема 56 px, слева от неё «Навигатор РХЛ» и под ним «Источник:
    РХЛ» — размеры для 720p, у другой высоты — в той же доле кадра (видео для админов — 480p, ADR-036). Текст — из
    файлов: в drawtext двоеточие в тексте пришлось бы экранировать."""
    k = height / 720
    pad, icon, gap, big, small = (round(v * k) for v in (20, 56, 12, 28, 20))
    text_x = f"w-tw-{pad + icon + gap}"
    return (f"[0:v]scale=-2:{height},setsar=1[v];[1:v]scale={icon}:{icon},format=rgba,colorchannelmixer=aa=0.9[logo];"
            f"[v][logo]overlay=W-w-{pad}:H-h-{pad}[vl];"
            f"[vl]drawtext=fontfile='{font}':textfile='{mark_file}':fontsize={big}:fontcolor=white:"
            f"shadowcolor=black@0.6:shadowx=2:shadowy=2:x={text_x}:y=h-{pad + icon}-2,"
            f"drawtext=fontfile='{font}':textfile='{source_file}':fontsize={small}:fontcolor=white@0.85:"
            f"shadowcolor=black@0.6:shadowx=1:shadowy=1:x={text_x}:y=h-{pad}-{small + 2}[out]")


def cut_cmd(src: str, headers: dict | None, start: int, length: int, path: Path, mark: tuple | None = None,
            height: int = 720, crf: int = 23) -> list[str]:
    """ffmpeg: окно записи в mp4. -ss до -i — качается только окно. mark=None — без перекодирования (2А); mark —
    (файл «Навигатор РХЛ», файл «Источник: РХЛ», шрифт): знак поверх, перекодирование в height (2Б). С перекодированием
    нулевая секунда файла — ровно start: кнопки «Гол на 0:47» считают от неё. faststart — клип начинает играть, не
    докачавшись."""
    head = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *header_args(headers), "-ss", str(start), "-i", src]
    if mark is None:
        return [*head, "-t", str(length), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-bsf:a", "aac_adtstoasc",
                "-movflags", "+faststart", str(path)]
    return [*head, "-i", str(ICON), "-t", str(length), "-filter_complex", mark_filter(*mark, height=height), "-map",
            "[out]", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(path)]


def duration(path: Path) -> float | None:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                              str(path)], capture_output=True, text=True, timeout=60).stdout.strip()
        return float(out)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def cut_match(name: str, src: str, headers: dict | None, goals: dict[str, int], args,
              srcs: dict[str, str] | None = None, who: dict[str, str] | None = None) -> list[dict]:
    out = args.out / re.sub(r"[^\w.-]+", "_", name)
    out.mkdir(parents=True, exist_ok=True)
    srcs, who = srcs or {}, who or {}
    clips = [c for c in windows(goals, args.before, args.after) if who.get(c["score"]) != HIDDEN_NAME]
    skipped = len(windows(goals)) - len(clips)
    print(f"\n{name}: голов с точной секундой {len(clips)}" + (f", скрытых по просьбе — {skipped}, их не режем"
                                                              if skipped else ""), flush=True)
    mark = None
    if not args.copy:
        (out / "mark.txt").write_text(MARK, encoding="utf-8")
        (out / "source.txt").write_text(SOURCE, encoding="utf-8")
        mark = (out / "mark.txt", out / "source.txt", font_file())
    done = []
    for c in clips:
        path = out / c["file"]
        began = time.monotonic()
        try:
            run = subprocess.run(cut_cmd(src, headers, c["start"], c["length"], path, mark), capture_output=True,
                                 text=True, timeout=900)
            ok = run.returncode == 0 and path.exists() and path.stat().st_size > 0
            err = run.stderr.strip().splitlines()[-1:] if not ok else []
        except subprocess.TimeoutExpired:
            ok, err = False, ["не уложились в 15 минут"]
        took = round(time.monotonic() - began)
        if not ok:
            print(f"  {c['score']} ({replay.fmt_t(c['t'])}): не вырезался — {'; '.join(err) or 'ffmpeg без ошибки'}")
            continue
        size = path.stat().st_size / 2 ** 20
        length = duration(path)
        how = {"admin": "отметка админа", "clock": "часы на табло", "board": "смена табло"}.get(srcs.get(c["score"]), "")
        done.append({**c, "size": size, "took": took, "duration": length, "src": srcs.get(c["score"]),
                     "author": who.get(c["score"]) or None})
        print(f"  {c['score']} ({replay.fmt_t(c['t'])}" + (f", {how}" if how else "") + f"): {length or 0:.1f} с, "
              f"{size:.1f} МБ, за {took} с — {path}", flush=True)
    (out / "clips.json").write_text(json.dumps({"key": name, "clips": [
        {k: c[k] for k in ("score", "t", "file", "src", "author", "duration")} for c in done]},
        ensure_ascii=False, indent=1), encoding="utf-8")
    return done


def main() -> None:
    ap = argparse.ArgumentParser(description="Нарезка голов со знаком «Навигатор РХЛ» (ADR-029)")
    ap.add_argument("--match", help="ключ матча <дата>|<хозяева>|<гости>; без него — все")
    ap.add_argument("--days", type=int, help="ещё все сыгранные матчи с записью лиги за столько последних дней")
    ap.add_argument("--date", action="append", help="ещё все сыгранные матчи с записью лиги за этот день (ГГГГ-ММ-ДД)")
    ap.add_argument("--league", type=Path, help="league.json с диска вместо опубликованного на Pages")
    ap.add_argument("--stream", help="файл или адрес потока вместо ролика VK (для проверки)")
    ap.add_argument("--truth", default="", help="для --stream: голы «1:0=42:53,0:2=49:28»")
    ap.add_argument("--before", type=int, default=CLIP_BEFORE, help="секунд до гола")
    ap.add_argument("--after", type=int, default=CLIP_AFTER, help="секунд после гола")
    ap.add_argument("--copy", action="store_true", help="без знака и без перекодирования — только для себя")
    ap.add_argument("--live", type=Path, default=Path(os.environ.get("LIVE_DIR") or ROOT / "live"))
    ap.add_argument("--board", type=Path, default=ROOT / "probe" / "scoreboard",
                    help="где лежат goals.json пробника табло")
    ap.add_argument("--out", type=Path, default=ROOT / "probe" / "cuts")
    args = ap.parse_args()
    if not shutil.which("ffprobe"):
        sys.exit("Нет ffmpeg: apt install -y ffmpeg")
    every: list[dict] = []
    waiting = 0
    if args.stream:
        every += cut_match(Path(args.stream).name, args.stream, None, parse_truth(args.truth), args)
    else:
        found = sb.matches(args)
        keys = [args.match] if args.match else sorted(found)
        if not keys or any(k not in found for k in keys):
            sys.exit("Нет матча с записью: размеченного в live/replays.json (/replay в боте) или с записью лиги "
                     "за --days/--date")
        league = sb.league_json(args.league)
        for key in keys:
            entry = found[key]
            try:
                board = json.loads((args.board / re.sub(r"[^\w.-]+", "_", key) / "goals.json").read_text(
                    encoding="utf-8")).get("goals") or []
            except (OSError, ValueError):
                board = []
            goals, srcs = goal_seconds(entry.get("anchors") or {}, board)
            waiting += sum(1 for g in board if g.get("score") not in goals)
            if not goals:
                print(f"\n{key}: нет ни одного гола с точной секундой — нужна разметка админа или пробник табло")
                continue
            try:
                src, headers, _ = stream_of(entry["video"], FORMAT)
            except Exception as err:   # VK не отдал — дальше не ломимся (ADR-012)
                print(f"\n{key}: поток не получили — {type(err).__name__}: {err}")
                continue
            every += cut_match(key, src, headers, goals, args, srcs, authors(league, key))
    if every:
        sizes = sorted(c["size"] for c in every)
        took = sorted(c["took"] for c in every)
        print(f"\nВсего клипов: {len(every)}, {sum(sizes):.0f} МБ (от {sizes[0]:.1f} до {sizes[-1]:.1f}), резка — "
              f"медиана {took[len(took) // 2]} с, самая долгая {took[-1]} с. Клипы — {args.out}")
    if waiting:
        print(f"Ещё {waiting} гол(ов) табло узнало, но без точной секунды — ждут отметки админа.")
    print("Посмотри 3–4 клипа: видно ли гол, хватает ли 20 с до него, читается ли знак. Напиши, что не так (ADR-029).")


if __name__ == "__main__":
    main()

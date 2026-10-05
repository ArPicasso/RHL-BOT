"""Первая нарезка голов (ADR-029, этап 0): клип вокруг каждого размеченного гола — кусок записи лиги без
перекодирования (развилка 2А). Запускать на VPS (нужны ffmpeg и yt-dlp, как для probe_scoreboard.py):

    cd /opt/rhl && venv/bin/python tools/probe_cuts.py                          # все размеченные матчи
    venv/bin/python tools/probe_cuts.py --match '2026-10-04|tverichi|metallurg'
    venv/bin/python tools/probe_cuts.py --before 20 --after 40                  # длиннее: с повтором из трансляции
    venv/bin/python tools/probe_cuts.py --stream probe/x.mp4 --truth 1:0=42:53   # файл вместо ролика VK

Клипы — только для нас: не публикуем, пока нет разрешения лиги или клуба (развилка 4). Смотрим, видно ли гол,
сколько весит клип и сколько секунд режется. Секунда гола — опора админа из live/replays.json (самое точное, что
есть); по табло она появится, когда пробник табло начнёт писать опоры сам.

Окно — CLIP_BEFORE секунд до гола и CLIP_AFTER после: видно атаку и бросок. Без перекодирования края клипа
встают на ключевые кадры записи (±2 с), клип чуть длиннее заказанного. Качаем как плеер (yt-dlp), только окно
клипа, без обхода защиты: VK закроет или попросит капчу — останавливаемся (ADR-012).

Клипы — probe/cuts/<матч>/<счёт>.mp4 (не в git), в конце — сводка: сколько клипов, вес, время резки.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import replay  # noqa: E402
from probe_scoreboard import ffmpeg, header_args, parse_truth, stream_of  # noqa: E402

CLIP_BEFORE = 20        # с до гола в клипе: видно атаку
CLIP_AFTER = 10         # с после гола: бросок, шайба в сетке, радость
FORMAT = "b[height<=720][height>=360]/b[height<=720]/b"   # 720p: клип на телефоне смотрится, весит 5–10 МБ


def windows(anchors: dict[str, int], before: int = CLIP_BEFORE, after: int = CLIP_AFTER) -> list[dict]:
    """Опоры админа (счёт → секунда записи) → окна клипов по порядку голов: счёт, секунда гола, начало, длина,
    имя файла. Начало не раньше начала записи."""
    out = []
    good = {s: t for s, t in anchors.items() if isinstance(t, int) and replay.SCORE_RE.fullmatch(s)}
    for score, t in sorted(good.items(), key=lambda x: x[1]):
        start = max(0, t - before)
        out.append({"score": score, "t": t, "start": start, "length": t + after - start,
                    "file": score.replace(":", "-") + ".mp4"})
    return out


def cut_cmd(src: str, headers: dict | None, start: int, length: int, path: Path) -> list[str]:
    """ffmpeg: окно записи в mp4 без перекодирования. -ss до -i — качается только окно; звук из потока HLS
    (ADTS) перекладывается в mp4 (aac_adtstoasc); faststart — клип начинает играть, не докачавшись."""
    return [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *header_args(headers), "-ss", str(start),
            "-i", src, "-t", str(length), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
            "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", str(path)]


def duration(path: Path) -> float | None:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                              str(path)], capture_output=True, text=True, timeout=60).stdout.strip()
        return float(out)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def cut_match(name: str, src: str, headers: dict | None, anchors: dict[str, int], args) -> list[dict]:
    out = args.out / re.sub(r"[^\w.-]+", "_", name)
    out.mkdir(parents=True, exist_ok=True)
    clips = windows(anchors, args.before, args.after)
    print(f"\n{name}: голов с опорой {len(clips)}", flush=True)
    done = []
    for c in clips:
        path = out / c["file"]
        began = time.monotonic()
        try:
            run = subprocess.run(cut_cmd(src, headers, c["start"], c["length"], path), capture_output=True,
                                 text=True, timeout=600)
            ok = run.returncode == 0 and path.exists() and path.stat().st_size > 0
            err = run.stderr.strip().splitlines()[-1:] if not ok else []
        except subprocess.TimeoutExpired:
            ok, err = False, ["не уложились в 10 минут"]
        took = round(time.monotonic() - began)
        if not ok:
            print(f"  {c['score']} ({replay.fmt_t(c['t'])}): не вырезался — {'; '.join(err) or 'ffmpeg без ошибки'}")
            continue
        size = path.stat().st_size / 2 ** 20
        length = duration(path)
        done.append({**c, "size": size, "took": took, "duration": length})
        print(f"  {c['score']} ({replay.fmt_t(c['t'])}): {length or 0:.1f} с, {size:.1f} МБ, за {took} с — {path}",
              flush=True)
    return done


def main() -> None:
    ap = argparse.ArgumentParser(description="Первая нарезка голов (ADR-029, этап 0)")
    ap.add_argument("--match", help="ключ матча из live/replays.json: <дата>|<хозяева>|<гости>; без него — все размеченные")
    ap.add_argument("--stream", help="файл или адрес потока вместо ролика VK (для проверки)")
    ap.add_argument("--truth", default="", help="для --stream: голы «1:0=42:53,0:2=49:28»")
    ap.add_argument("--before", type=int, default=CLIP_BEFORE, help="секунд до гола")
    ap.add_argument("--after", type=int, default=CLIP_AFTER, help="секунд после гола")
    ap.add_argument("--live", type=Path, default=Path(os.environ.get("LIVE_DIR") or ROOT / "live"))
    ap.add_argument("--out", type=Path, default=ROOT / "probe" / "cuts")
    args = ap.parse_args()
    every: list[dict] = []
    if args.stream:
        every += cut_match(Path(args.stream).name, args.stream, None, parse_truth(args.truth), args)
    else:
        try:
            marked = json.loads((args.live / "replays.json").read_text(encoding="utf-8")).get("games") or {}
        except (OSError, ValueError):
            marked = {}
        keys = [args.match] if args.match else sorted(marked)
        if not keys or any(k not in marked for k in keys):
            sys.exit(f"Нет размеченного матча в {args.live / 'replays.json'}: сначала /replay в боте")
        for key in keys:
            entry = marked[key]
            anchors = {s: t for s, t in (entry.get("anchors") or {}).items() if isinstance(t, int)}
            try:
                src, headers, _ = stream_of(entry["video"], FORMAT)
            except Exception as err:   # VK не отдал — дальше не ломимся (ADR-012)
                print(f"\n{key}: поток не получили — {type(err).__name__}: {err}")
                continue
            every += cut_match(key, src, headers, anchors, args)
    if every:
        sizes = sorted(c["size"] for c in every)
        took = sorted(c["took"] for c in every)
        print(f"\nВсего клипов: {len(every)}, {sum(sizes):.0f} МБ (от {sizes[0]:.1f} до {sizes[-1]:.1f}), резка — "
              f"медиана {took[len(took) // 2]} с, самая долгая {took[-1]} с. Клипы — {args.out}")
    print("Посмотри 3–4 клипа: видно ли гол, хватает ли 20 с до него. Напиши, что не так (ADR-029).")


if __name__ == "__main__":
    main()

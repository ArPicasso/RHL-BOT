"""Пилот записи эфира (ADR-038, шаг 4.1). Запустить на VPS руками во время матча — нужны `ffmpeg` и `yt-dlp`.

Отвечает на три вопроса, от которых зависит, стоит ли делать службу `record`:

1. **Отдаёт ли VK live-поток** нашему серверу и не рвёт ли его.
2. **Сколько это стоит места**: мегабайт в минуту и сколько будет за матч.
3. **Читается ли табло из своей записи** так же, как из записи лиги: клуб размечен в `boards.json`, значит
   название хозяев должно находиться и табло быть видно в части кадров.

Запись идёт своими сегментами по 10 секунд (`ffmpeg -c copy`, без перекодирования), имя файла — наше время:
`probe/record/<матч>/seg-<unix>.ts`. Именно в этом смысл ADR-038: нулевая секунда нашей записи — наши часы, и
секунду гола больше не надо сшивать с чужим файлом, у которого неизвестно начало.

    venv/bin/python tools/probe_record.py "2026-10-08|tverichi|polet" --minutes 10
    venv/bin/python tools/probe_record.py https://vk.com/video-100_200 --minutes 10 --club tverichi

Пилот ничего не выкладывает и ни на что не влияет: только пишет в `probe/record/` и считает. Сегменты после
замера можно удалить руками.
"""
import argparse
import json
import os
import re
import resource
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import probe_scoreboard as sb  # noqa: E402

OUT = ROOT / "probe" / "record"
SEGMENT = 10          # с: длина сегмента — столько же, сколько у HLS, и столько же теряем при обрыве
READ_SEGMENTS = 3     # последних сегментов читаем на табло: 30 с кадр в секунду
LIVE_DIR = Path(os.environ.get("LIVE_DIR") or ROOT / "live")
KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\|[a-z0-9-]+\|[a-z0-9-]+$")


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def video_of(key: str) -> str | None:
    """Адрес эфира матча по ключу: «Смотреть» из живых файлов службы live (у лиги он появляется к началу)."""
    for name in ("today.json", "schedule.json"):
        for g in (read_json(LIVE_DIR / name).get("games") or []):
            if f"{g.get('date')}|{g.get('home')}|{g.get('away')}" != key:
                continue
            for w in g.get("watch") or []:
                url = w.get("url")
                if isinstance(url, str) and sb.replay.same_video(url, url):   # ролик VK, а не страница клуба
                    return url
    return None


def record(src: str, headers: dict | None, out: Path, seconds: int) -> dict:
    """Запись сегментами, без перекодирования. Возвращает замеры: сколько файлов, байт и процессорного времени."""
    out.mkdir(parents=True, exist_ok=True)
    cmd = [sb.ffmpeg(), "-hide_banner", "-loglevel", "error", *sb.header_args(headers), "-i", src,
           "-t", str(seconds), "-c", "copy", "-f", "segment", "-segment_time", str(SEGMENT),
           "-reset_timestamps", "1", "-strftime", "1", str(out / "seg-%s.ts")]
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    start = time.monotonic()
    run = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds + 120)
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    files = sorted(out.glob("seg-*.ts"))
    return {"wall": time.monotonic() - start, "cpu": (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime),
            "files": files, "bytes": sum(f.stat().st_size for f in files), "code": run.returncode,
            "error": (run.stderr.strip().splitlines() or [""])[-1][:200]}


def board_check(files: list[Path], club: str) -> dict:
    """Видно ли табло клуба в своей записи: образец названия хозяев по кадрам последних сегментов (как у разбора
    записи лиги) и доля кадров, где табло на экране."""
    mark = sb.BOARDS.get(club)
    if not mark:
        return {"club": club, "note": "табло клуба не размечено в boards.json — читать нечем"}
    frames = []
    for f in files[-READ_SEGMENTS:]:
        try:
            frames += [raw for _, raw in sb.scan(str(f), None, mark["box"], step=1, keyframes=False)]
        except subprocess.CalledProcessError as err:
            return {"club": club, "note": f"ffmpeg не прочитал сегмент {f.name}: {err}"}
    if not frames:
        return {"club": club, "note": "кадров нет: сегменты пустые"}
    model = sb.name_model(frames, mark["name"])
    if not model:
        return {"club": club, "frames": len(frames), "note": "название хозяев не нашлось — графика эфира другая"}
    seen = sum(1 for f in frames if sb.on_screen(f, model))
    cells = {}
    for cell in ("home", "away", "clock"):
        pixels = sb.cell_pixels(mark[cell])
        cells[cell] = len({bytes(f[p] for p in pixels) for f in frames})
    return {"club": club, "frames": len(frames), "seen": seen, "share": round(seen / len(frames), 2), "cells": cells}


def main() -> None:
    ap = argparse.ArgumentParser(description="Пилот записи эфира (ADR-038, шаг 4.1)")
    ap.add_argument("video", help="ссылка VK на эфир или ключ матча «ГГГГ-ММ-ДД|хозяева|гости»")
    ap.add_argument("--minutes", type=int, default=10, help="сколько минут писать (по умолчанию 10)")
    ap.add_argument("--club", help="чьё табло читать; по умолчанию — хозяева из ключа матча")
    args = ap.parse_args()

    key = args.video if KEY_RE.match(args.video) else None
    video = video_of(key) if key else args.video
    if not video:
        sys.exit(f"У матча {key} нет ссылки «Смотреть»: лига ещё не объявила эфир. Дай ссылку VK прямо.")
    club = args.club or (key.split("|")[1] if key else None)
    name = (key or video).replace("|", "_").replace("/", "_").replace(":", "")[-60:]
    print(f"Эфир: {video}\nПишем {args.minutes} мин в probe/record/{name}/")

    src, headers, length = sb.stream_of(video, sb.FORMAT)
    print(f"Поток получен{'' if length is None else f', длительность {length} с (это не эфир, а готовая запись)'}")

    got = record(src, headers, OUT / name, args.minutes * 60)
    mb = got["bytes"] / (1 << 20)
    mins = max(got["wall"] / 60, 0.1)
    print(f"\nЗАПИСЬ: файлов {len(got['files'])}, {mb:.0f} МБ за {mins:.1f} мин — "
          f"{mb / mins:.0f} МБ/мин, за матч 2,5 часа ≈ {mb / mins * 150 / 1024:.1f} ГБ")
    print(f"Процессорного времени: {got['cpu']:.1f} с на {mins:.1f} мин записи "
          f"({got['cpu'] / (mins * 60) * 100:.0f}% одного ядра)")
    if got["code"] != 0:
        print(f"ffmpeg закончил с кодом {got['code']}: {got['error']}")
    if not got["files"]:
        sys.exit("Сегментов нет — VK не отдал поток. Дальше по ADR-038 идти нельзя, см. раздел «Риски».")

    if club:
        board = board_check(got["files"], club)
        print(f"\nТАБЛО ({board['club']}):", board.get("note") or
              f"кадров {board['frames']}, табло видно в {board['seen']} ({int(board['share'] * 100)}%), "
              f"разных картинок в клетках: счёт хозяев {board['cells']['home']}, гостей {board['cells']['away']}, "
              f"часы {board['cells']['clock']}")
        if board.get("share"):
            print("Вердикт: табло читается из своей записи — шаг 4.2 (служба record) делать можно"
                  if board["share"] >= 0.3 else
                  "Вердикт: табло видно редко — сначала проверить разметку клуба на этом эфире")
    else:
        print("\nТабло не читали: клуб не задан (--club)")
    print(f"\nСегменты: {OUT / name} — после замера можно удалить: rm -rf {OUT / name}")


if __name__ == "__main__":
    main()

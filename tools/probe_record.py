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
READ_STEP = 2         # с: табло читаем по кадру раз в столько из каждого сегмента — 300 кадров за 10 минут
FEW_GLYPHS = 3        # картинок цифры в клетке счёта за запись не больше: цифра и следующая после гола (с запасом)
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


def seg_time(f: Path) -> int:
    """Наше время начала сегмента — из имени `seg-<unix>.ts` (ffmpeg -strftime). Не разобрали — 0."""
    m = re.fullmatch(r"seg-(\d+)\.ts", f.name)
    return int(m.group(1)) if m else 0


def record(src: str, headers: dict | None, out: Path, seconds: int) -> dict:
    """Запись сегментами, без перекодирования. Возвращает замеры: сколько файлов, байт и процессорного времени —
    только по сегментам этого запуска. Сегменты прошлого запуска в той же папке не в счёт: 08.10 второй прогон
    (720p) посчитал вместе с дневным (480p) и место, и табло — через часы между ними, как одну запись."""
    out.mkdir(parents=True, exist_ok=True)
    old = set(out.glob("seg-*.ts"))
    cmd = [sb.ffmpeg(), "-hide_banner", "-loglevel", "error", *sb.header_args(headers), "-i", src,
           "-t", str(seconds), "-c", "copy", "-f", "segment", "-segment_time", str(SEGMENT),
           "-reset_timestamps", "1", "-strftime", "1", str(out / "seg-%s.ts")]
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    start = time.monotonic()
    run = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds + 120)
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    files = sorted(set(out.glob("seg-*.ts")) - old, key=seg_time)
    return {"old": len(old), "wall": time.monotonic() - start, "cpu": (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime),
            "files": files, "bytes": sum(f.stat().st_size for f in files), "code": run.returncode,
            "error": (run.stderr.strip().splitlines() or [""])[-1][:200]}


def classes(frames: list[bytes], rect, cap: int) -> tuple[int, int]:
    """Сколько разных картинок цифры в клетке rect: сравнение как у разбора записи лиги (glyph, glyph_diff < SAME),
    картинка — если держалась хотя бы HOLD кадров (мелькание сжатия — не картинка). И в скольких кадрах картинки нет
    (клетка без контраста). Больше cap не считаем: это уже не цифра, а часы или фон — и счёт дорогой."""
    reps: list[list] = []
    empty = 0
    for f in frames:
        g = sb.glyph(f, rect)
        if g is None:
            empty += 1
            continue
        for r in reps:
            d = sb.glyph_diff(g, r[0])
            if d is not None and d < sb.SAME:
                r[1] += 1
                break
        else:
            if len(reps) > cap:
                return cap + 1, empty
            reps.append([g, 1])
    return sum(1 for r in reps if r[1] >= sb.HOLD), empty


def board_check(files: list[Path], club: str) -> dict:
    """Видно ли табло клуба в своей записи и читаются ли на нём цифры — по всей записи, кадр раз в READ_STEP секунд.
    Табло на экране — образец названия хозяев (name_model, как у разбора записи лиги). Цифры — разные картинки в
    клетках счёта среди кадров с табло (classes): за 10 минут их одна-две, а если десятки — клетки не те или цифры
    прозрачные. Смены счёта за запись — cell_changes, как у разбора. Байты кадров напрямую не сравниваем: от сжатия
    каждый кадр немного другой (пилот 08.10 на «Ростове» насчитал 28 «разных» картинок счёта в 28 кадрах)."""
    mark = sb.BOARDS.get(club)
    if not mark:
        return {"club": club, "note": "табло клуба не размечено в boards.json — читать нечем"}
    samples: list[tuple[float, bytes]] = []
    first = seg_time(files[0]) if files else 0
    for k, f in enumerate(files):
        at = seg_time(f) - first if first else k * SEGMENT   # секунда сегмента в записи — по нашим часам из имени
        try:
            samples += [(at + t, raw) for t, raw in sb.scan(str(f), None, mark["box"], step=READ_STEP,
                                                             keyframes=False)]
        except subprocess.CalledProcessError as err:
            return {"club": club, "note": f"ffmpeg не прочитал сегмент {f.name}: {err}"}
    if not samples:
        return {"club": club, "note": "кадров нет: сегменты пустые"}
    frames = [raw for _, raw in samples]
    model = sb.name_model(frames, mark["name"])
    if not model:
        return {"club": club, "frames": len(frames), "note": "название хозяев не нашлось — графика эфира другая"}
    vis = [(t, raw) for t, raw in samples if sb.on_screen(raw, model)]
    cells = {cell: classes([raw for _, raw in vis], mark[cell], sb.GLYPH_CLASSES) for cell in ("home", "away", "clock")}
    changes = {cell: len(sb.cell_changes(vis, mark[cell])) for cell in ("home", "away")} if vis else {}
    return {"club": club, "frames": len(frames), "seen": len(vis), "share": round(len(vis) / len(frames), 2),
            "cells": cells, "changes": changes}


def verdict(board: dict) -> str:
    if board["share"] < 0.3:
        return "табло видно редко — сначала проверить разметку клуба на этом эфире"
    many = [c for c in ("home", "away") if board["cells"][c][0] > FEW_GLYPHS]
    if many:
        return (f"табло видно, но цифры счёта ({', '.join(many)}) не различаются — клетки разметки не те или цифры "
                f"прозрачные: смены пойдут по пикселям, точность хуже. Проверить разметку на кадрах этого эфира")
    return "табло видно и цифры счёта различаются — шаг 4.2 (служба record) делать можно"


def main() -> None:
    ap = argparse.ArgumentParser(description="Пилот записи эфира (ADR-038, шаг 4.1)")
    ap.add_argument("video", help="ссылка VK на эфир или ключ матча «ГГГГ-ММ-ДД|хозяева|гости»")
    ap.add_argument("--minutes", type=int, default=10, help="сколько минут писать (по умолчанию 10)")
    ap.add_argument("--club", help="чьё табло читать; по умолчанию — хозяева из ключа матча")
    ap.add_argument("--height", type=int, default=480,
                    help="качество записи, строк: 480 — как у разбора записи лиги, 720 — как для клипов (ADR-038)")
    args = ap.parse_args()

    key = args.video if KEY_RE.match(args.video) else None
    video = video_of(key) if key else args.video
    if not video:
        sys.exit(f"У матча {key} нет ссылки «Смотреть»: лига ещё не объявила эфир. Дай ссылку VK прямо.")
    club = args.club or (key.split("|")[1] if key else None)
    name = (key or video).replace("|", "_").replace("/", "_").replace(":", "")[-60:]
    print(f"Эфир: {video}\nПишем {args.minutes} мин, до {args.height}p, в probe/record/{name}/")

    fmt = sb.FORMAT if args.height == 480 else f"b[height<={args.height}]/b"
    src, headers, length = sb.stream_of(video, fmt)
    print(f"Поток получен{'' if length is None else f', длительность {length} с (это не эфир, а готовая запись)'}")

    got = record(src, headers, OUT / name, args.minutes * 60)
    if got["old"]:
        print(f"В папке ещё {got['old']} сегментов прошлого запуска — их не считаем")
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
        if board.get("note"):
            print(f"\nТАБЛО ({board['club']}): {board['note']}")
        else:
            def cell(c: str) -> str:
                n, empty = board["cells"][c]
                return f"{'больше ' + str(sb.GLYPH_CLASSES) if n > sb.GLYPH_CLASSES else n}" + (f" (без цифры {empty})" if empty else "")
            print(f"\nТАБЛО ({board['club']}): кадр раз в {READ_STEP} с по всей записи — {board['frames']}, табло видно "
                  f"в {board['seen']} ({int(board['share'] * 100)}%)")
            print(f"Картинок цифры: счёт хозяев {cell('home')}, гостей {cell('away')}, часы {cell('clock')} "
                  f"(за 10 минут у счёта ждём 1–2, у часов — много)")
            print(f"Смен счёта за запись: хозяева {board['changes'].get('home', 0)}, гости {board['changes'].get('away', 0)} "
                  f"— сверь с тем, были ли голы за эти минуты")
            print(f"Вердикт: {verdict(board)}")
    else:
        print("\nТабло не читали: клуб не задан (--club)")
    print(f"\nСегменты: {OUT / name} — после замера можно удалить: rm -rf {OUT / name}")


if __name__ == "__main__":
    main()

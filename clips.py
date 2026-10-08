"""Служба clips на VPS (ADR-030, шаг 2): после матча находит голы по табло трансляции и пишет их секунды в записи
в live/clips.json. Оттуда их берут бот (/replay) и сборка Pages: «Повтор» становится точным без разметки админа.

Раз в EVERY секунд берёт сыгранные матчи сезона с SINCE, у которых есть запись: запись лиги из опубликованного
league.json («Смотреть» от rhl.fhr.ru) или ссылка админа из live/replays.json. Свежие — первыми, не больше SCAN_MAX
за проход: догоняя сезон, служба не задерживает клипы вчерашних матчей. Каждый матч — один раз на ролик:
проход по записи пробником табло (tools/probe_scoreboard.py, разметка табло клубов — boards.json), точные голы —
встали часы игры (`clock`) или проверенная задержка табло клуба (`board`). У остальных голов табло знает, какой это
гол, но не секунду: служба ставит превью службе cuts (ADR-036) — PREVIEW_BEFORE секунд записи до смены счёта — и
ищет в нём моменты, когда вставали часы игры. Бот присылает превью админам и помощникам с кнопками на эти моменты
(шаг 3). После прохода — заготовки видео для людей в ту же очередь (`prepare_cuts`): споры по отметкам и точные голы
с одним свидетелем. Гол, которого
табло не нашло вовсе (табло убирали на минуты), превью не получает: где он в записи, служба не знает, а угадывать по
времени сайта лиги не стали — 06.10 такие пятиминутные превью уходили админам без гола (ADR-030, дополнение 06.10,
вечер). Такой матч — в вечернем напоминании, время гола — в /replay. Табло клуба-хозяина не размечено — кадр с
сеткой grid.png для разметки, голов нет: кадр клуба держим в probe/grids/<клуб>.png до разметки, а в clips.json —
`boards`: какие клубы ждут разметки, сколько их матчей и где кадр. Бот присылает его админам (ADR-030, дополнение
06.10).

Счёт хода часов (ADR-031, clockrun.py): от гола с точной секундой служба идёт по записи кадр в секунду и находит
соседние голы периода по протоколу — точно (`src: run`), окном для превью (`win`) или никак; самопроверка снимает
секунды табло, которые с протоколом не сходятся. Пересчёт — когда у матча поменялись опоры (админ ответил на превью).
Записи клуба (ролик VK из «Смотреть» поста клуба), когда записи лиги нет, — только для повтора, клипов из них нет.
Пометки админа из live/replays.json — гола в записи нет (`absent`), табло сбилось (`wrong`) — снимают с гола секунду,
смену табло и превью. Разбор покрытия — у каждого сыгранного матча, сколько голов с повтором и почему не у всех
(`coverage` в clips.json): по нему бот пишет админам.

По одному писателю на файл: live/replays.json пишет только бот, live/clips.json — только эта служба. Качаем как
плеер (yt-dlp), без обхода защиты (ADR-012): VK отказал — пишем ошибку и пробуем позже, не больше TRIES раз.
Отказ «записи больше нет» (удалена, не существует, 404) — не сбой: со второго раза подряд у матча `status: gone`,
повторов по такой записи сборка не ставит (мёртвая ссылка), а админам уходит тревога — пришли другую запись в
/replay (этап 0.3 плана). «Нам её не отдали» (приватная, регион, 403, капча) — не `gone`: болельщик в VK её
откроет, пробуем снова. Разобранную запись VK может удалить и потом, а разбор её больше не трогает — поэтому
`alive_pass` раз в ALIVE_EVERY спрашивает у VK метаданные ролика (один запрос, без скачивания): свежие матчи —
каждый проход. VK ещё
не знает длину записи (эфир идёт или запись обрабатывается) — не разбираем (`wait`), спрашиваем снова через
WAIT_EVERY до конца следующего дня после матча: 06.10 разбор во время эфира дал кадр табло из заставки до матча.
Кадры прохода и картинки — в probe/scoreboard/<матч>/ (там же, где у пробника), держатся KEEP_DAYS дней с разбора.
Матчи сезона в clips.json не забываем: по ним сборка ставит «Повтор» и клип у гола.

Клипы стёрты 06.10 (`wipe`, метка WIPE — при запуске служба один раз стирает все клипы из бакета и clips.json), и
до новой схемы секунды гола нарезка стояла на паузе (ADR-030, дополнение 06.10, ночь). Новая схема — правило двух
свидетелей (ADR-033, раздел 4), и 07.10 владелец сказал включать: `cut_pass` режет снова, а пауза осталась
выключателем — CLIPS_CUT=off в окружении.

Пульт (ADR-030, раздел 7): после каждого матча и прохода — пульс и счётчики дня в status/clips.json (`admin.Tracker`):
отдал ли VK запись (`vk_ok`, `vk_fail`) и снимок каталога голов сезона (`catalog`). Молчит дольше часа или VK за
день не отдал ни одной записи — тревога админам.

    venv/bin/python clips.py            # служба
    venv/bin/python clips.py --once     # один проход и выйти
"""
import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))

import admin  # noqa: E402
import clockrun  # noqa: E402
import cutjobs  # noqa: E402
import probe_cuts as pc  # noqa: E402
import probe_scoreboard as sb  # noqa: E402
import replay  # noqa: E402
import s3  # noqa: E402

TZ = ZoneInfo("Europe/Moscow")
LIVE_DIR = Path(os.environ.get("LIVE_DIR") or ROOT / "live")
STATE_DB = Path(os.environ.get("STATE_DB") or ROOT / "state.db")   # задания службы cuts (cutjobs.py)
WORK = ROOT / "probe" / "scoreboard"
GRIDS = ROOT / "probe" / "grids"   # кадр с сеткой клуба без разметки табло: держим до разметки, не KEEP_DAYS
SINCE = date.fromisoformat(os.environ.get("CLIPS_SINCE") or "2026-10-03")   # с этого дня разбираем: сайт РХЛ с записями
SCAN_MAX = 2        # записей за проход: разбор — минуты, между ними — нарезка клипов
EVERY = 600         # с между проходами; пока есть неразобранные записи сезона — через минуту
TRIES = 3           # столько раз пробуем матч, который не скачался или упал
GONE_TRIES = 2      # столько раз подряд VK должен сказать «записи нет», чтобы считать её удалённой (этап 0.3 плана)
GONE_MAX = 6        # удалённых записей в пульте и тревогах
INV_MAX = 5         # сломанных инвариантов прохода в пульте и тревогах (ADR-034): остальные — в журнале службы
WIDE_BEFORE = 50    # с до секунды с одним свидетелем в окне повтора (ADR-037): отметка ошибается на полминуты
WIDE_AFTER = 20     # и после
WIDE_HEIGHT = 480   # окна режем мельче точных клипов: их много, а смотрят их реже
CLIP_AFTER = 15     # с: клип позже смены счёта на табло — гол не бывает после того, как счёт уже сменился
CLIP_BEFORE = 180   # с: и не раньше чем за три минуты до смены (оператор меняет счёт через 0–90 с, ADR-031)
CANARY_EVERY = 20 * 3600   # с: «канарейка» yt-dlp (ADR-034) — раз в сутки, даже когда разбирать нечего
ALIVE_EVERY = 6 * 3600   # с: как часто спрашиваем VK, на месте ли уже разобранная запись (ADR-027, доп. 07.10)
ALIVE_FRESH = 1800       # с: у матчей младше ALIVE_DAYS — чаще, но не каждый проход: запросов к VK и так хватает
ALIVE_MAX = 3            # записей за проход: один запрос метаданных на запись, без скачивания
ALIVE_DAYS = 2           # дней после матча запись проверяем каждый проход: в первые дни её и удаляют, и заменяют
WAIT_EVERY = 1200   # с: VK ещё не знает длину записи (эфир идёт или запись обрабатывается) — спрашиваем снова не чаще
KEEP_DAYS = 3       # кадры прохода держим столько дней
VERSION = 9         # разбор поменялся — матчи разбираем заново (05.10: голы по порядку протокола; 06.10: смены
                    # табло — в порядке счёта, у двух голов не бывает одной остановки часов; 06.10: кадр клуба без
                    # разметки — в probe/grids/; 06.10, вечер: гол берём, только если цифры в клетке идут цепочкой;
                    # 06.10, вечер: одна смена табло на голы подряд, когда табло убирали на повтор; 06.10, вечер:
                    # разбор падал, если табло не увидело смену раннего гола, — «Факел Ямал» 05.10 исчерпал попытки;
                    # 07.10: цифры табло — по картинкам, подсветка строки и обзоры голов больше не смены; 07.10,
                    # вечер: «табло на экране» — по самой частой картинке названия, «Протон» 04.10 табло не видел)
CLOCK_MAX = 2       # матчей за проход со счётом хода часов (ADR-031): кадр в секунду — минуты записи на гол
CLUB_MIN = 3000     # с: ролик клуба короче — не запись матча (пресс-конференция, обзор), берём следующий
PREVIEW_BEFORE = cutjobs.CHANGE_BEFORE   # с записи до смены счёта на табло в превью: окна — общие с ботом (cutjobs)
PREVIEW_AFTER = cutjobs.CHANGE_AFTER     # и после смены
CANDIDATES = 3         # кнопок «Гол на …» под превью — последние остановки часов перед сменой счёта
# Клипы стёрты 06.10: другая метка WIPE — служба стирает все клипы ещё раз (ADR-030, дополнение 06.10, ночь).
WIPE = "2026-10-06"
# Нарезка стояла на паузе, пока не выбрана новая схема секунды гола; схема выбрана (два свидетеля, ADR-033, раздел 4),
# и 07.10 владелец сказал включать. Теперь пауза — выключатель: CLIPS_CUT=off в /etc/rhl/bot.env и systemctl restart
# clips останавливают нарезку, не трогая разбор табло, превью, счёт хода и «Повтор».


def cut_on(value: str | None) -> bool:
    """Режем ли клипы: пауза — только явный CLIPS_CUT=off (ADR-030, дополнение 07.10, ночь)."""
    return (value or "on").strip().lower() != "off"


CUT = cut_on(os.environ.get("CLIPS_CUT"))

log = logging.getLogger("clips")


class VkError(RuntimeError):
    """VK не отдал поток записи: на пульте — «VK за день не отдал ни одной записи» (ADR-030, раздел 7)."""


# Отказ VK бывает трёх видов, и повторы зависят от того, какой это (этап 0.3 плана):
#  • записи больше нет ни у кого — удалена, не существует, 404: повтор по ней мёртвый, его нельзя показывать;
#  • нам её не отдали — приватная, регион, 403, капча, «только для зарегистрированных»: болельщик в VK её откроет,
#    повтор оставляем и пробуем снова;
#  • сбой — сеть, таймаут, ffmpeg: пробуем снова.
# Слова ищем и по-русски, и по-английски: yt-dlp отдаёт то, что написал VK. «Нам не отдали» проверяем первым: в
# «недоступно в вашем регионе» есть и «недоступ», и регион.
RESTRICTED_RE = re.compile(r"приватн|закрыт|только для|регистрац|авториз|подписчик|регион|private|registered|"
                           r"region|sign in|log in|login|access denied|forbidden|403|429|captcha|"
                           r"too many requests", re.I)
GONE_RE = re.compile(r"удал|не существует|нет такого|deleted|removed|does ?n.?t exist|no longer|unavailable|"
                     r"not found|404|unable to find", re.I)


def gone_error(err) -> bool:
    """Отказ VK — «записи больше нет» (удалена, не существует, 404), а не «нам её не отдали» (приватная, регион,
    403, капча) и не сбой сети: по удалённой записи повтора нет ни у кого (этап 0.3 плана)."""
    s = str(err or "")
    return bool(GONE_RE.search(s)) and not RESTRICTED_RE.search(s)


def match_title(league: dict | None, key: str) -> str:
    """«05.10 Калужские Ракеты — Динамо» — ключ матча в тревоге админам читается плохо."""
    names = {t["id"]: t.get("name") or t["id"] for t in (league or {}).get("teams") or []
             if isinstance(t, dict) and t.get("id")}
    day, _, rest = key.partition("|")
    home, _, away = rest.partition("|")
    return f"{day[8:10]}.{day[5:7]} {names.get(home, home)} — {names.get(away, away)}".strip()


def gone_note(track: "admin.Tracker | None", games: dict, league: dict | None = None) -> None:
    """Записи, которых больше нет в VK, — в пульс для пульта и тревоги админам (ADR-022): по ним повторов не будет,
    пока человек не пришлёт другую ссылку в /replay."""
    if track is None:
        return
    out = [{"key": k, "title": match_title(league, k), "video": e.get("video"), "at": e.get("scanned")}
           for k, e in sorted(games.items(), reverse=True)
           if isinstance(e, dict) and e.get("status") == "gone"]
    track.info(gone=out[:GONE_MAX])


def stream(video: str, fmt: str | None = None):
    """Поток записи (sb.stream_of), отказ VK или yt-dlp — VkError: его считает пульт."""
    try:
        return sb.stream_of(video, fmt) if fmt else sb.stream_of(video)
    except Exception as err:
        raise VkError(f"{type(err).__name__}: {err}"[:300]) from err


def vk_note(track: "admin.Tracker | None", err: Exception | None) -> None:
    """Отдал ли VK запись: счётчик дня и последняя ошибка — для пульта и тревоги."""
    if track is None:
        return
    if err is None:
        track.add("vk_ok")
        track.info(vk_ok=admin.iso(now_msk()))
    else:
        track.add("vk_fail")
        track.info(vk_error=admin.no_ids(str(err))[:200], vk_fail=admin.iso(now_msk()))


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


def season_days(today: date, since: date | None = None) -> set[str]:
    """Дни сезона с SINCE по сегодня."""
    since = since or SINCE
    return {(since + timedelta(days=k)).isoformat() for k in range((today - since).days + 1)}


def recordings(league: dict | None, marked: dict, days: set[str], store: dict | None = None) -> dict[str, dict]:
    """Записи сыгранных матчей этих дней: ключ → {"video", "src"}. Ссылка админа главнее (`admin`), потом запись лиги
    (`league`), а нет её — ролик VK из «Смотреть» поста клуба (`club`, ADR-031): по нему — только повтор, клипы со
    знаком режем из записи лиги и ссылки админа (разрешение лиги, ADR-029). Ролик клуба, оказавшийся короче
    CLUB_MIN (`short` у матча в clips.json), пропускаем — берём следующую ссылку."""
    found = {k: {"video": v["video"], "src": "league"} for k, v in sb.recorded(league, days).items()}
    for g in (league or {}).get("games") or []:
        if not (isinstance(g, dict) and g.get("date") in days and g.get("score")):
            continue
        key = f"{g['date']}|{g.get('home')}|{g.get('away')}"
        short = ((store or {}).get(key) or {}).get("short") or []
        for w in [] if key in found else g.get("watch") or []:
            got = replay.parse_link(w.get("url") or "") if isinstance(w, dict) and w.get("src") != sb.SITE else None
            if got and not any(replay.same_video(got[0], x) for x in short):
                found[key] = {"video": got[0], "src": "club"}
                break
    for key, e in (marked or {}).items():
        if key[:10] in days and isinstance(e, dict) and isinstance(e.get("video"), str):
            found[key] = {"video": e["video"], "src": "admin"}
    return found


def wait_over(game: dict, now: datetime) -> bool:
    """Запись ждёт, пока VK узнает её длину (`wait`), дольше WAIT_EVERY — пора спросить снова."""
    try:
        return (now - datetime.fromisoformat(game["scanned"])).total_seconds() >= WAIT_EVERY
    except (KeyError, TypeError, ValueError):
        return True


def alive_due(game: dict, key: str, now: datetime) -> bool:
    """Пора ли спросить VK, на месте ли запись: сразу — если VK уже раз сказал «записи нет» (подтвердить или снять),
    иначе раз в ALIVE_FRESH у матчей младше ALIVE_DAYS и раз в ALIVE_EVERY у остальных — от прошлой проверки,
    а первый раз от разбора."""
    if game.get("gone_tries"):
        return True
    fresh = key[:10] >= (now.date() - timedelta(days=ALIVE_DAYS)).isoformat()
    try:   # время в файле может быть и наивным — тогда вычитание упадёт: спрашиваем, как при отсутствии времени
        last = datetime.fromisoformat(game.get("alive") or game["scanned"])
        return (now - last).total_seconds() >= (ALIVE_FRESH if fresh else ALIVE_EVERY)
    except (KeyError, TypeError, ValueError):
        return True


def alive_pass(store: dict, now: datetime, check=stream, track: "admin.Tracker | None" = None,
               league: dict | None = None) -> int:
    """Запись, которую служба уже разобрала, VK может удалить потом: разбор её больше не трогает, а повторы по ней
    продолжают висеть в мини-аппе (ADR-027, дополнение 07.10). Поэтому раз в ALIVE_EVERY спрашиваем у VK
    метаданные ролика — один запрос, без скачивания. Записи больше нет — `status: gone`, как при разборе: голы
    остаются, вернётся запись — вернутся и повторы. Возвращает, сколько записей проверили."""
    games = store.get("games") or {}
    todo = [(k, e) for k, e in games.items()
            if isinstance(e, dict) and isinstance(e.get("video"), str) and e.get("status") not in ("wait", "gone")
            and (e.get("status") != "error" or e.get("tries", 0) >= TRIES) and alive_due(e, k, now)]
    # сначала те, по кому VK уже раз сказал «записи нет»: иначе подтверждение ждёт своей очереди за всем сезоном
    # (три записи за проход), и мёртвый «Повтор» живёт лишний час вместо одного прохода
    todo.sort(key=lambda x: (not x[1].get("gone_tries"), x[1].get("alive") or x[1].get("scanned") or "", x[0]))
    n = 0
    for key, e in todo[:ALIVE_MAX]:
        e["alive"] = now.isoformat(timespec="seconds")
        n += 1
        try:
            check(e["video"])
        except Exception as err:
            if not (isinstance(err, VkError) and gone_error(err)):
                log.info("%s: запись не проверилась — %s: %s", key, type(err).__name__, err)
                e.pop("gone_tries", None)   # сбой — не «записи нет»: счёт отказов подряд начинается заново
                continue
            if track is not None:
                track.add("vk_gone")
            e["gone_tries"] = e.get("gone_tries", 0) + 1
            if e["gone_tries"] >= GONE_TRIES:
                # `gone_at` — когда служба это узнала: по нему сборка решает, чьё слово свежее, её или человека
                # (replay.with_board). Без него считалось бы время разбора, а оно старше отметок админа
                e.update(status="gone", error=f"{type(err).__name__}: {err}"[:300],
                         gone_at=now.isoformat(timespec="seconds"))
                log.warning("%s: записи %s больше нет в VK — повторов по ней не будет", key, e["video"])
        else:
            e.pop("gone_tries", None)
    if n:
        gone_note(track, games, league)
        store["updated"] = now_msk().isoformat(timespec="seconds")
        write_atomic(LIVE_DIR / "clips.json", store)
    return n


def pending(league: dict | None, marked: dict, store: dict, today: date,
            now: datetime | None = None) -> list[tuple[str, str]]:
    """Какие матчи разобрать: (ключ, ролик), свежие первыми. Сыгранные с SINCE с записью (recordings). Уже разобранный
    ролик не трогаем; новый ролик у матча — разбираем заново; упавший — до TRIES раз; матч без разметки табло —
    заново, как только табло клуба появилось в boards.json; запись, у которой VK ещё не знал длину, — не чаще
    WAIT_EVERY."""
    found = recordings(league, marked, season_days(today), store)
    now = now or now_msk()
    out = []
    for key in sorted(found, key=lambda k: (k[:10], k), reverse=True):
        video = found[key]["video"]
        was = store.get(key) or {}
        if replay.same_video(was.get("video"), video) and was.get("v", 1) >= VERSION:
            if was.get("status") == "wait" and not wait_over(was, now):
                continue
            marked_now = was.get("status") == "no_board" and key.split("|")[1] in sb.BOARDS   # табло разметили
            if not marked_now and (was.get("status") in ("ok", "no_board", "short", "gone")
                                   or was.get("tries", 0) >= TRIES):
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


preview_window = cutjobs.change_window   # окно превью: до смены счёта на табло и чуть после


run_window = cutjobs.run_window   # окно превью по счёту хода часов (ADR-031)


_jobs: tuple | None = None


def cut_jobs() -> cutjobs.CutJobs:
    """Очередь службы cuts в state.db (ADR-036): превью и заготовки режет она."""
    global _jobs
    if _jobs is None or _jobs[0] != STATE_DB:
        conn = sqlite3.connect(STATE_DB, isolation_level=None, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout=5000")
        _jobs = (STATE_DB, cutjobs.CutJobs(conn, ROOT))
    return _jobs[1]


def add_previews(key: str, video: str, goals: dict[str, dict], length: float | None,
                 only: set[str] | None = None) -> None:
    """Превью голам без секунды: окно записи, задание службе cuts и моменты остановки часов (`ask`). Гол в окне
    наверняка: по счёту хода часов — окно `win` (ADR-031), иначе PREVIEW_BEFORE с до смены счёта на табло: оператор
    меняет счёт через 0–90 с после гола. only — только эти голы (счёт хода пересчитал окно). Режет служба cuts
    (ADR-036): бот пришлёт превью, когда файл будет готов."""
    need = {s: g for s, g in goals.items() if g.get("t") is None and (g.get("win") or g.get("change") is not None)
            and (only is None or s in only)}
    if not need:
        return
    club = key.split("|")[1]
    board = sb.BOARDS.get(club) or {}
    whole = name_model_of(key, club) if board.get("clock") else None   # образец табло по всей записи
    src480 = h480 = None
    for score, g in need.items():
        win = g.get("win")
        start, span = run_window(win, length) if win else preview_window(g["change"], length)
        cand: list[int] = []
        if win:
            cand = [round(t - start) for t in (g.get("wcand") or [])][-CANDIDATES:]
        elif board.get("clock"):
            if src480 is None:
                src480, h480, _ = sb.stream_of(video)
            dense = sb.safe_scan(src480, h480, sb.BOXES[club], start, start + span)
            # в двух минутах до смены счёта повторов и крупных планов больше, чем во всей записи
            model = whole or (sb.name_model([f for _, f in dense], board["name"]) if dense else None)
            vis = [(t, f) for t, f in dense if not model or sb.on_screen(f, model)]
            stops = [t for t in clock_stops(vis, sb.cell_pixels(board["clock"])) if t <= g["change"]]
            cand = [round(t - start) for t in stops[-CANDIDATES:]]
        try:
            job = cut_jobs().want(now_msk(), video, start, span, "preview", prio=cutjobs.SEND, match=key, score=score)
        except sqlite3.Error as err:   # база занята — без превью этот гол, остальные — как обычно
            log.warning("%s %s: задание на превью не поставили — %s", key, score, err)
            continue
        g["ask"] = {"from": start, "len": span, "job": job, "cand": cand}
        log.info("%s %s: превью %s — задание %s, моментов часов %d", key, score, replay.fmt_t(start), job, len(cand))


protocol_order = sb.protocol_order   # голы протокола по порядку: (счёт, команда, период, секунда игры)


def scan_match(key: str, video: str, anchors: dict, order: list[tuple] | None = None,
               kind: str = "league") -> dict:
    """Один матч: проход по записи и голы по табло. Исключения (VK не отдал, ffmpeg упал) — наверх. kind — чья запись
    (recordings): ролик клуба короче CLUB_MIN — не запись матча, его не разбираем и кадр табло с него не берём."""
    club = key.split("|")[1]
    src, headers, length = stream(video)
    if not length and key[:10] >= (now_msk().date() - timedelta(days=1)).isoformat():
        # эфир ещё идёт или VK обрабатывает запись: кадры с неё — не те. 06.10 «Белгород» и «Дизелист» так разобрались
        # во время матча, кадр табло для разметки вышел из заставки до игры. Ждём до конца следующего дня; дальше длины
        # может и не быть — тогда разбираем как есть
        log.info("%s: VK ещё не знает длину записи %s — эфир идёт или запись обрабатывается, разберу позже", key, video)
        return {"status": "wait", "goals": {}}
    if kind == "club" and length and length < CLUB_MIN:
        log.info("%s: ролик клуба %s — %d с, не запись матча", key, video, length)
        return {"status": "short", "goals": {}, "length": round(length)}
    out = WORK / safe_name(key)
    out.mkdir(parents=True, exist_ok=True)
    if club not in sb.BOARDS:
        sb.grid_sheet(src, headers, length, out / "grid.png")
        keep_grid(club, out / "grid.png")
        log.info("%s: табло клуба %s не размечено — grid.png для разметки в boards.json", key, club)
        return {"status": "no_board", "goals": {}, **({"length": round(length)} if length else {})}
    args = SimpleNamespace(out=WORK, step=sb.STEP, start=0, end=None, rescan=False,
                           order=list(order or []))
    truth = {s: t for s, t in (anchors or {}).items() if isinstance(t, int)}
    site = sb.site_goals(LIVE_DIR, key)
    sb.probe(key, src, headers, truth, site, sb.BOXES[club], args)
    board = read_json(out / "goals.json").get("goals") or []
    live = live_goals(key) or {score: {"team": team, "period": per} for score, team, per, *_ in order or []}
    goals = found_goals(board, live)
    rejected = read_json(out / "goals.json").get("rejected") or {}
    try:
        add_previews(key, video, goals, length)
    except Exception as err:   # без превью голы всё равно записываем: секунды табло уже есть
        log.warning("%s: превью не сделали — %s: %s", key, type(err).__name__, err)
    return {"status": "ok", "goals": goals, **({"rejected": rejected} if rejected else {}),
            **({"length": round(length)} if length else {})}


def keep_grid(club: str, grid: Path, root: Path | None = None) -> None:
    """Кадр с сеткой клуба без разметки табло — в probe/grids/<клуб>.png: папки матчей чистятся через KEEP_DAYS, а
    кадр нужен, пока табло не разметили. До 06.10 он оставался в папке матча, его никто не видел, и домашние матчи
    неразмеченных клубов шли без секунд, превью и клипов."""
    root = root or GRIDS
    if grid.is_file() and grid.stat().st_size:
        root.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(grid, root / f"{club}.png")


def boards_todo(games: dict, root: Path | None = None) -> dict[str, dict]:
    """Клубы-хозяева, чьё табло не размечено (ADR-030, дополнение 06.10): сколько их матчей с записью ждут разметки,
    последний из них и кадр для разметки — путь от корня проекта (его бот присылает админам). Кадры клубов, которых
    уже разметили, удаляем. (клуб → {"matches", "key", "grid"?})."""
    root = root or GRIDS
    out: dict[str, dict] = {}
    for key in sorted(games):
        g = games[key]
        club = key.split("|")[1] if key.count("|") == 2 else ""
        if not (isinstance(g, dict) and g.get("status") == "no_board" and club) or club in sb.BOARDS:
            continue
        e = out.setdefault(club, {"matches": 0})
        e["matches"] += 1
        e["key"] = key                                     # ключи по порядку — последний и есть свежий
    for club, e in out.items():
        grid = root / f"{club}.png"
        if not grid.is_file():                             # разобран до 06.10: кадр ещё в папке матча
            keep_grid(club, WORK / safe_name(e["key"]) / "grid.png", root)
        if grid.is_file():
            try:
                e["grid"] = str(grid.relative_to(ROOT))
            except ValueError:
                e["grid"] = str(grid)
    if root.is_dir():
        for old in root.glob("*.png"):
            if old.stem in sb.BOARDS:
                old.unlink(missing_ok=True)
    return out


def drop_guesses(games: dict) -> bool:
    """Голы, которых табло не нашло, а разбор 06.10 записал с оценкой по времени сайта лиги (`est`) и превью по ней, —
    убрать: в таких пятиминутных превью гола часто не было (ADR-030, дополнение 06.10, вечер). Матч без них снова в
    вечернем напоминании, а превью бот закрывает сам. Было что убирать — True."""
    changed = False
    for game in games.values():
        goals = game.get("goals") if isinstance(game, dict) else None
        if not isinstance(goals, dict):
            continue
        for score in [s for s, g in goals.items() if isinstance(g, dict) and g.get("est") is not None]:
            goals.pop(score)
            changed = True
    return changed


def side_digit(score: str, team: str | None) -> tuple[str, int] | None:
    """Чей гол и какой он у команды по счёту: «2:1» хозяев → ("home", 2)."""
    if team not in ("home", "away") or not replay.SCORE_RE.fullmatch(score or ""):
        return None
    h, a = (int(x) for x in score.split(":"))
    return team, h if team == "home" else a


def apply_admin(games: dict, marked: dict, league: dict | None = None) -> bool:
    """Пометки админа (ADR-031): гола в записи нет (`absent`) или табло сбилось (`wrong` — повтор или превью не того
    гола). У такого гола секунды, смены табло и превью больше нет; у «табло сбилось» — и у следующих голов той же
    команды: их сопоставили по тому же порядку, значит, сбились и они (05.10 «Калуга», «Факел Ямал»). Опора админа у
    гола главнее пометки. Что-то сняли — True."""
    changed = False
    for key, game in games.items():
        e = (marked or {}).get(key)
        if not (isinstance(game, dict) and isinstance(e, dict)
                and replay.same_video(e.get("video"), game.get("video"))):
            continue
        goals = game.get("goals") or {}
        protocol = league_goals(league, key) if league else {}
        team = lambda s: (protocol.get(s) or {}).get("team") or (goals.get(s) or {}).get("team")   # noqa: E731
        off = {s: "absent" for s in e.get("absent") or []}
        for s in e.get("wrong") or []:
            sd = side_digit(s, team(s))
            for other, g in goals.items():
                od = side_digit(other, team(other))
                if other == s or sd and od and od[0] == sd[0] and od[1] >= sd[1]:
                    off.setdefault(other, "wrong")
        for s, why in off.items():
            g = goals.get(s)
            if not isinstance(g, dict) or s in (e.get("anchors") or {}) or g.get("off") == why:
                continue
            for k in ("t", "src", "change", "ask", "win", "wcand"):
                g.pop(k, None)
            g.update(t=None, off=why)
            changed = True
    return changed


def run_pass(store: dict, league: dict | None, marked: dict, now: datetime, scan=scan_match,
             track: "admin.Tracker | None" = None) -> tuple[int, int]:
    """Один проход: разбирает до SCAN_MAX ждущих матчей по одному и после каждого пишет clips.json и пульс.
    Возвращает (сколько разобрано, сколько ещё ждёт)."""
    games = store.setdefault("games", {})
    guessed = drop_guesses(games)
    flagged = apply_admin(games, marked, league)
    todo = pending(league, marked, games, now.date(), now)
    kinds = recordings(league, marked, season_days(now.date()), games)
    for i, (key, video) in enumerate(todo[:SCAN_MAX]):
        was = games.get(key) or {}
        same = replay.same_video(was.get("video"), video) and was.get("v", 1) >= VERSION
        tries = (was.get("tries", 0) if same else 0) + 1
        gone_n = was.get("gone_tries", 0) if same else 0   # отказов «записи нет» подряд: другой исход их обнуляет
        kind = (kinds.get(key) or {}).get("src") or "league"
        log.info("%s: разбираю %s (%s, попытка %d)", key, video, kind, tries)
        if track is not None:   # «Сейчас» на пульте (ADR-036, раздел 4): разбор записи — минуты, видно какой
            track.info(scan={"key": key, "src": kind, "at": now_msk().isoformat(timespec="seconds")},
                       waiting=len(todo) - i - 1)
            track.flush()
        try:
            got = scan(key, video, ((marked or {}).get(key) or {}).get("anchors") or {}, protocol_order(league, key),
                       kind)
            vk_note(track, None)
        except Exception as err:   # VK не отдал, ffmpeg упал — дальше не ломимся (ADR-012), попробуем в другой проход
            log.warning("%s: не разобрали — %s: %s", key, type(err).__name__, err)
            got = {"status": "error", "error": f"{type(err).__name__}: {err}"[:300], "goals": was.get("goals") or {}}
            if isinstance(err, VkError) and gone_error(err):
                # записи больше нет в VK: повторы по ней мёртвые. Говорим это только с GONE_TRIES отказов подряд —
                # один 404 бывает и от сбоя. Счётчик vk_fail не трогаем: новый yt-dlp тут не поможет (этап 0.3)
                if track is not None:
                    track.add("vk_gone")
                gone_n += 1
                got["gone_tries"] = gone_n   # в записи матча — только пока отказы идут подряд: иначе ключа нет
                if gone_n >= GONE_TRIES:
                    got["status"] = "gone"
                    got["gone_at"] = now_msk().isoformat(timespec="seconds")
                    log.warning("%s: записи %s больше нет в VK — повторов по ней не будет", key, video)
            elif isinstance(err, VkError):
                vk_note(track, err)
        if got.get("status") == "wait":   # запись ещё не готова — это не попытка разбора; прежний разбор ролика остаётся
            tries -= 1
            if replay.same_video(was.get("video"), video):
                got = {**{k: was[k] for k in ("goals", "rejected", "length") if k in was}, "status": "wait",
                       "goals": was.get("goals") or {}}
        short = list(was.get("short") or []) + ([video] if got.get("status") == "short" else [])
        games[key] = {"video": video, "src": kind, "v": VERSION, "tries": tries,
                      "scanned": now_msk().isoformat(timespec="seconds"), **got,
                      "clips": was.get("clips") or {},   # выложенные клипы остаются: нарезка сверит их сама
                      **({"short": short} if short else {})}
        apply_admin({key: games[key]}, marked, league)
        timed = sum(1 for g in games[key]["goals"].values() if g.get("t") is not None)
        if got["status"] == "wait":
            log.info("%s: ждёт, пока VK отдаст запись целиком, — спрошу через %d мин", key, WAIT_EVERY // 60)
        else:
            log.info("%s: %s, голов по табло %d, с секундой %d", key, got["status"], len(games[key]["goals"]), timed)
        store["boards"] = boards_todo(games)
        store["updated"] = now_msk().isoformat(timespec="seconds")
        write_atomic(LIVE_DIR / "clips.json", store)
        if track is not None:   # пульс после каждого матча: разбор записи — минуты, пульт ждёт не дольше часа
            gone_note(track, games, league)
            track.flush()
    for key in [k for k in games if k[:10] < SINCE.isoformat()]:
        games.pop(key)
    boards = boards_todo(games)
    if boards != store.get("boards") or guessed or flagged:   # клуб разметили, кадр появился — бот узнает и без разбора
        store["boards"] = boards
        store["updated"] = now_msk().isoformat(timespec="seconds")
        write_atomic(LIVE_DIR / "clips.json", store)
    done = min(len(todo), SCAN_MAX)
    if track is not None:
        track.info(scan=None, waiting=len(todo) - done)
    return done, len(todo) - done


# ---------- счёт хода часов (ADR-031) ----------
# Между голами периода часы на табло идут ровно столько, сколько между ними по протоколу. От гола с точной секундой
# служба идёт по записи кадр в секунду и находит соседние голы: точно, окном для превью или никак (clockrun.py).
# Пересчёт — когда у матча поменялись опоры (админ ответил на превью), протокол или разбор табло (`sig`).


class Dense:
    """Клетки табло кадр в секунду по требованию, кусками по CHUNK с записи. Кадр без табло на экране — None."""
    CHUNK = 300

    def __init__(self, src: str, headers: dict | None, club: str, model: dict | None, length: float | None = None):
        self.src, self.headers, self.box, self.model, self.length = src, headers, sb.BOXES[club], model, length
        self.clock = sb.cell_pixels(sb.BOARDS[club]["clock"])
        self.frames: dict[int, bytes | None] = {}
        self.loaded: set[int] = set()

    def frame(self, t: int) -> bytes | None:
        if t < 0 or (self.length and t > self.length):
            return None
        part = t // self.CHUNK
        if part not in self.loaded:
            self.loaded.add(part)
            for at, f in sb.safe_scan(self.src, self.headers, self.box, part * self.CHUNK, (part + 1) * self.CHUNK - 1):
                self.frames[int(at)] = f if self.model is None or sb.on_screen(f, self.model) else None
        return self.frames.get(t)

    def state(self, t: int) -> str | None:
        a, b = self.frame(t), self.frame(t + 1)
        if a is None or b is None:
            return None
        return "run" if len(sb.moved(a, b, self.clock)) >= sb.CLOCK_MOVED else "stop"


def clock_goals(game: dict, protocol: dict[str, dict], admin_e: dict | None) -> list[dict]:
    """Голы для счёта хода: время и период — из протокола, точная секунда — отметка админа (её ролик тот же) или
    табло (часы, задержка); секунды прошлого счёта хода (`run`) опорой не бывают."""
    mine = replay.same_video((admin_e or {}).get("video"), game.get("video"))
    anchors = (admin_e or {}).get("anchors") or {} if mine else {}
    reject = (admin_e or {}).get("reject") if mine else None
    goals = game.get("goals") or {}
    out = []
    for score, x in sorted(protocol.items(), key=lambda kv: sb.goal_rank(kv[0])):
        b = goals.get(score) or {}
        if b.get("off"):
            continue
        if isinstance(anchors.get(score), int) and not replay.objected(reject, score, anchors[score]):
            t, src = anchors[score], "admin"
        elif isinstance(b.get("t"), (int, float)) and b.get("src") != "run" \
                and not replay.objected(reject, score, b["t"]):   # человек сказал «гола тут нет» — не опора
            t, src = b["t"], b.get("src")
        else:
            t, src = None, None
        out.append({"score": score, "period": str(x.get("period") or ""), "time": x.get("time"), "t": t, "src": src,
                    "change": b.get("change"), "team": x.get("team")})
    return out


RUN_V = 2   # счёт хода поменялся — пересчитать все матчи (07.10: подтверждённые голы — второй свидетель для клипа)


def clock_sig(goals: list[dict]) -> str:
    return hashlib.sha1(json.dumps([RUN_V, [[g["score"], g["time"], g["t"], g["src"], g["change"]] for g in goals]],
                                   ensure_ascii=False).encode()).hexdigest()[:12]


def name_model_of(key: str, club: str) -> dict | None:
    """Как выглядит табло клуба в этой записи — по кадрам первого прохода из кэша пробника."""
    cache = sb.cache_file(WORK / safe_name(key), sb.BOXES[club], sb.STEP, 0, None)
    try:
        samples, _ = sb.load_cache(cache)
    except (OSError, ValueError, SystemExit):
        return None
    return sb.name_model([f for _, f in samples], sb.BOARDS[club]["name"]) if samples else None


def clock_pass(store: dict, league: dict | None, marked: dict, track: "admin.Tracker | None" = None,
               dense=None) -> int:
    """Счёт хода часов по разобранным матчам, у которых поменялись опоры, протокол или разбор табло: точные секунды
    (`src: run`), окна для превью (`win`, `wcand`) и самопроверка (`run` у матча: сошлось пар, несошедшиеся периоды,
    снятые секунды табло). Не больше CLOCK_MAX матчей за проход. Возвращает, сколько матчей пересчитано."""
    games = store.get("games") or {}
    n = 0
    for key, game in sorted(games.items(), reverse=True):
        club = key.split("|")[1] if key.count("|") == 2 else ""
        if n >= CLOCK_MAX or not isinstance(game, dict) or game.get("status") != "ok" \
                or not (sb.BOARDS.get(club) or {}).get("clock"):
            continue
        protocol = league_goals(league, key)
        goals = clock_goals(game, protocol, (marked or {}).get(key))
        sig = clock_sig(goals)
        if (game.get("run") or {}).get("sig") == sig:
            continue
        n += 1
        info: dict = {"sig": sig, "at": now_msk().isoformat(timespec="seconds")}
        people = any(g["src"] == "admin" for g in goals)   # отметки людей проверяем всегда (ADR-033)
        exact = sum(g["t"] is not None for g in goals)
        if not exact or exact == len(goals) == 1 and not people:
            # опор нет или искать и сверять нечего. Все голы точные — всё равно сверяем их парами: гол табло,
            # подтверждённый ходом часов, — второй свидетель для клипа (ADR-033, раздел 4)
            game["run"] = info
            continue
        try:
            src, headers, length = stream(game["video"])
            frames = dense(key, game) if dense else Dense(src, headers, club, name_model_of(key, club), length)
            got = clockrun.solve(goals, frames.state)
            verdicts = clockrun.check_marks(goals, frames.state) if people else {}
            vk_note(track, None)
        except Exception as err:   # VK не отдал или кадры не скачались — в другой проход
            log.warning("%s: счёт хода часов не вышел — %s: %s", key, type(err).__name__, err)
            if isinstance(err, VkError):
                vk_note(track, err)
            game["run"] = {**info, "sig": None, "error": f"{type(err).__name__}: {err}"[:200]}
            continue
        board = game.setdefault("goals", {})
        for g in board.values():   # прошлый счёт хода — заново
            if g.get("src") == "run":
                g.update(t=None, src=None)
            g.pop("win", None)
            g.pop("wcand", None)
        renew = set()
        for score in got["drop"]:
            g = board.get(score) or {}
            if g.get("t") is not None:
                g.update(t=None, src=None, dropped="часы не сошлись с протоколом")
                log.info("%s %s: секунда табло снята — часы между голами не сходятся с протоколом", key, score)
                if g.get("change") is not None:
                    renew.add(score)   # секунды больше нет — превью админу по смене счёта
        for score, res in got["found"].items():
            x = protocol.get(score) or {}
            g = board.setdefault(score, {"team": x.get("team"), "period": x.get("period"), "change": None, "t": None,
                                         "src": None})
            g["win"], g["wcand"] = res["win"], res["cand"]
            if "t" in res:
                g.update(t=res["t"], src="run")
                g.pop("ask", None)
            elif (g.get("ask") or {}).get("from") != run_window(res["win"], length)[0]:
                renew.add(score)
        exact = sorted(s for s, r in got["found"].items() if "t" in r)
        log.info("%s: счёт хода часов — пар сошлось %d, точных %d (%s), окон %d, не сошлись периоды %s", key,
                 got["checked"], len(exact), ", ".join(exact) or "—", len(got["found"]) - len(exact),
                 ", ".join(got["fail"]) or "—")
        if renew and not dense:
            try:
                add_previews(key, game["video"], board, length, only=renew)
            except Exception as err:
                log.warning("%s: превью по счёту хода не сделали — %s: %s", key, type(err).__name__, err)
        game["run"] = {**info, "checked": got["checked"], "fail": got["fail"], "drop": got["drop"], "exact": exact,
                       "windows": sorted(set(got["found"]) - set(exact)), "marks": verdicts,
                       "confirmed": got.get("confirmed") or []}
        store["updated"] = now_msk().isoformat(timespec="seconds")
        write_atomic(LIVE_DIR / "clips.json", store)
        if track is not None:
            track.flush()
    return n


# ---------- проверка отметок людей (ADR-033) ----------
# Отметка человека — показание: служба проверяет её табло (встали ли часы, когда сменился счёт) и ходом часов от соседних
# точных голов. Вердикт — в clips.json у матча (`checks`): по нему бот показывает статус в /replay и присылает спор, а
# сборка и нарезка не берут точную секунду у гола со спором — ни отметки, ни табло.


def mark_checks(game: dict, admin_e: dict | None, protocol: dict[str, dict], club: str = "") -> dict[str, dict]:
    """Вердикт по каждой отметке времени у матча: счёт → {"t", "status", "for", "against"}. Статус: «ok», «conflict»,
    «unknown» — проверить нечем, «pending» — проверка хода часов ещё впереди (кадры качаются в clock_pass). С кадрами
    — вердикт clock_pass для той же секунды; без них — только смена счёта на табло."""
    if not (admin_e and replay.same_video(admin_e.get("video"), game.get("video"))):
        return {}
    board = game.get("goals") or {}
    run = (game.get("run") or {}).get("marks") or {}
    frames = game.get("status") == "ok" and bool((sb.BOARDS.get(club) or {}).get("clock"))
    out = {}
    for score, t in sorted((admin_e.get("anchors") or {}).items()):
        if not isinstance(t, int):
            continue
        done = run.get(score)
        if isinstance(done, dict) and done.get("t") == t:
            out[score] = done
            continue
        x = protocol.get(score) or {}
        got = clockrun.check_marks([{"score": score, "period": str(x.get("period") or ""), "time": x.get("time"),
                                     "t": t, "src": "admin", "change": (board.get(score) or {}).get("change")}])
        v = got.get(score) or {"t": t, "status": "unknown", "for": [], "against": []}
        if v["status"] == "unknown" and frames:
            v = {**v, "status": "pending"}
        out[score] = v
    return out


def checks_pass(store: dict, league: dict | None, marked: dict) -> bool:
    """Вердикты по отметкам людей у всех матчей — в clips.json (`checks`). Что-то поменялось — запись и True."""
    changed = False
    for key, game in (store.get("games") or {}).items():
        if not isinstance(game, dict):
            continue
        club = key.split("|")[1] if key.count("|") == 2 else ""
        got = mark_checks(game, (marked or {}).get(key), league_goals(league, key) if league else {}, club)
        if got != (game.get("checks") or {}):
            if got:
                game["checks"] = got
            else:
                game.pop("checks", None)
            changed = True
    if changed:
        store["updated"] = now_msk().isoformat(timespec="seconds")
        write_atomic(LIVE_DIR / "clips.json", store)
    return changed


# ---------- заготовки видео для людей (ADR-036, раздел 2) ----------
# Всё, что ждёт человека, служба cuts режет заранее, пока человек занят другим: бот пришлёт видео сразу, а не через
# «⏳ Режу видео…». Превью голов без секунды ставит add_previews, здесь — остальное.

PREP_DAYS = cutjobs.KEEP_DAYS - 1   # заготовки — матчам последних дней: задание живёт KEEP_DAYS с последней просьбы


def cut_wants(game: dict, admin_e: dict | None) -> list[tuple[int, int, str, str]]:
    """Окна записи, которые у матча ждут человека: [(начало, длина, счёт, вид)]. Спор (ADR-033) — обе версии: 30 с
    по отметке и 30 с по точной секунде табло, а нет её — окно до смены счёта, как у превью. Точный гол с одним
    свидетелем — 30 с вокруг секунды: табло без отметки человека или отметка, которую нечем проверить (`unknown`).
    Гол с двумя свидетелями ждёт только клипа (1.3 плана), без секунды — превью."""
    length = game.get("length")
    mine = bool(admin_e) and replay.same_video(admin_e.get("video"), game.get("video"))
    anchors = (admin_e.get("anchors") or {}) if mine else {}
    board = game.get("goals") or {}
    checks = game.get("checks") or {}
    out = []
    disputed = replay.disputed(admin_e, game) if mine else set()
    for score in sorted(disputed):
        out.append((*cutjobs.review_window(anchors[score], length), score, "review"))
        b = board.get(score) or {}
        if isinstance(b.get("t"), (int, float)) and not b.get("off"):
            out.append((*cutjobs.review_window(int(b["t"]), length), score, "review"))
        elif isinstance(b.get("change"), (int, float)):
            out.append((*preview_window(b["change"], length), score, "preview"))
    witnesses = clip_witnesses(game, admin_e)
    for score, (t, src) in sorted(goal_seconds(game, admin_e).items()):
        if score in witnesses:
            continue   # два свидетеля — гол ждёт только клипа, смотреть его людям уже не надо
        if src == "admin" and (checks.get(score) or {}).get("status") != "unknown":
            continue   # спор — выше; проверка впереди — подождём вердикта
        out.append((*cutjobs.review_window(t, length), score, "review"))
    return out


def prepare_cuts(store: dict, marked: dict, now: datetime) -> int:
    """Заготовки службе cuts у разобранных матчей последних PREP_DAYS дней (старые — только по запросу человека).
    Одно окно — одно задание: повторная просьба каждый проход ничего не режет заново. Превью без ответа, у которого
    кончились попытки (VK не отдавал запись), просим снова: через cutjobs.REVIVE оно пойдёт на новый круг, иначе гол
    навсегда остался бы «ждёт превью», которое не придёт. Готовые превью не трогаем: задание живёт KEEP_DAYS с
    последней просьбы, меньше, чем бот помнит отправленное, — старое превью второй раз не уйдёт. Сколько окон
    попросили."""
    edge = (now.date() - timedelta(days=PREP_DAYS)).isoformat()
    n = 0
    for key, game in sorted((store.get("games") or {}).items(), reverse=True):   # свежие — первыми в очереди
        if not isinstance(game, dict) or key[:10] < edge or game.get("status") != "ok" or not game.get("video") \
                or game.get("src") == "club":   # запись клуба — только для повтора (ADR-030)
            continue
        e = (marked or {}).get(key) or {}
        for start, span, score, kind in cut_wants(game, e):
            cut_jobs().want(now, game["video"], start, span, kind, prio=cutjobs.PREP, match=key, score=score)
            n += 1
        for score, g in sorted((game.get("goals") or {}).items()):
            ask = (g or {}).get("ask")
            if not (isinstance(ask, dict) and ask.get("job") is not None and g.get("t") is None
                    and score not in (e.get("anchors") or {})):
                continue
            job = cut_jobs().get(ask["job"])
            if job and job["status"] == "error" and job["tries"] >= cutjobs.TRIES:
                cut_jobs().want(now, game["video"], ask["from"], ask["len"], "preview", prio=cutjobs.SEND, match=key,
                                score=score)
                n += 1
    return n


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
    """Точная секунда каждого гола в ролике службы: отметка админа (если ролик тот же) главнее часов и табло; гол,
    которого, по словам админа, в записи нет или у которого табло сбилось (ADR-031), — без секунды; секунда, которой
    человек возразил на её 30 с («⏪ / ⏩ гол раньше / позже»), — тоже."""
    out = {s: (int(g["t"]), g.get("src") or "board") for s, g in (game.get("goals") or {}).items()
           if isinstance(g, dict) and isinstance(g.get("t"), (int, float)) and not g.get("off")}
    if admin and replay.same_video(admin.get("video"), game.get("video")):
        for s in list(admin.get("absent") or []) + list(admin.get("wrong") or []):
            out.pop(s, None)
        out.update({s: (int(t), "admin") for s, t in (admin.get("anchors") or {}).items() if isinstance(t, int)})
        for s in replay.disputed(admin, game):   # спор (ADR-033): клип мимо гола хуже никакого
            out.pop(s, None)
        for s in [s for s, (t, _) in out.items() if replay.objected(admin.get("reject"), s, t)]:
            out.pop(s)
    return out


WITNESS = {"admin+board": "отметка человека, табло с ней сошлось", "clock+run": "встали часы, ход часов сошёлся",
           "board+run": "табло, ход часов от другого точного гола", "seen": "человек видел 30 с гола: «✅ Гол виден»"}


def clip_witnesses(game: dict, admin: dict | None) -> dict[str, tuple[int, str, str]]:
    """Голы, у которых точную секунду подтверждают два независимых свидетеля (ADR-033, раздел 4): счёт → (секунда,
    откуда, какие свидетели). Только такой гол получает клип: клип мимо гола хуже никакого.
    - отметка человека и проверка табло — «сошлось»;
    - секунда по ходу часов (`run`): часы встали ровно там, где по протоколу от другого точного гола;
    - секунда табло (часы, задержка) и ход часов от другого точного гола периода — пара сошлась в самопроверке;
    - человек посмотрел 30 с вокруг этой секунды и нажал «✅ Гол виден», а ни один свидетель не возражает (спор,
      «нет в записи», «табло сбилось» и «гола тут нет» goal_seconds уже убрал). Свидетели независимы: подтверждения
      своей же секунды в `confirm` нет — их не пускает журнал отметок (`marks.own_confirm`)."""
    seconds = goal_seconds(game, admin)
    mine = bool(admin) and replay.same_video(admin.get("video"), game.get("video"))
    checks = game.get("checks") or {}
    confirmed = set((game.get("run") or {}).get("confirmed") or [])
    seen = (admin.get("confirm") or {}) if mine else {}
    out = {}
    for score, (t, src) in seconds.items():
        c = checks.get(score) or {}
        if src == "admin" and c.get("status") == "ok" and c.get("t") == t:
            why = "admin+board"
        elif src == "run":
            why = "clock+run"
        elif src in ("clock", "board") and score in confirmed:
            why = "board+run"
        elif any(abs(int(x) - t) <= replay.OBJECT_TOL for x in seen.get(score) or [] if isinstance(x, int)):
            why = "seen"
        else:
            continue
        out[score] = (t, src, why)
    return out


def clip_plan(game: dict, admin: dict | None, protocol: dict[str, dict]) -> tuple[list[tuple[str, int, str]], list[str]]:
    """Что резать и что убрать: ([(счёт, секунда, откуда)], [счёт клипа к удалению]). Режем гол с секундой, которую
    подтверждают два свидетеля (clip_witnesses), и протоколом, без скрытых, если клипа нет или секунда поменялась.
    Убираем клип скрытого игрока, гол, которого в протоколе нет или он другой команды (лига отменила гол — счета
    сдвинулись), и гол, у которого двух свидетелей больше нет: разбор поправили (05.10 клип 4:0 вырезали на секунде
    гола 1:0) или свидетель возразил — спор, «гола тут нет» (ADR-033: клип снимается сразу)."""
    # окна повторов (ADR-037) ведёт window_plan — точный клип про них не знает
    have = {s: c for s, c in (game.get("clips") or {}).items() if (c or {}).get("kind") != "window"}
    cut, drop = [], []
    # два свидетеля (ADR-033, раздел 4); запись клуба — только повтор
    seconds = {s: (t, src) for s, (t, src, _) in clip_witnesses(game, admin).items()} if game.get("src") != "club" else {}
    for score, (t, src) in sorted(seconds.items(), key=lambda x: x[1][0]):
        x = protocol.get(score)
        if not x or hidden_goal(x):
            continue
        if (have.get(score) or {}).get("t") != t:
            cut.append((score, t, src))
    for score, c in have.items():
        x = protocol.get(score) if protocol else {}
        if (protocol and (not x or hidden_goal(x) or (c.get("team") and x.get("team") != c.get("team")))
                or score not in seconds):
            drop.append(score)
    return cut, drop


# ---------- окна повторов (ADR-037) ----------
# Болельщик видит одну кнопку «Повтор», и она всегда играет в приложении. Точную секунду подтвердили двое — клип
# 30 с; подтвердил один или секунда примерная — окно записи, в котором гол точно есть. Ссылок в VK у повтора нет.


def wide_window(t: int, length: float | None = None) -> tuple[int, int]:
    """Окно вокруг секунды, которой верим не до конца: (начало, длина). Ошибка отметки на полминуты остаётся внутри."""
    start = max(0, int(t) - WIDE_BEFORE)
    end = int(t) + WIDE_AFTER
    if length:
        end = min(end, int(length))
    return start, max(1, end - start)


def fan_windows(game: dict, admin: dict | None) -> dict[str, tuple[int, int]]:
    """Окно записи для каждого гола, который видит болельщик (`replay.with_board` — то же, что покажет мини-апп):
    счёт → (начало, длина). Смена счёта на табло — 125 с (оператор меняет счёт через 0–90 с после гола), окно счёта
    хода часов — само окно, точная секунда с одним свидетелем и отметка со спором — WIDE секунд вокруг неё."""
    length = game.get("length")
    board = game.get("goals") or {}
    mine = replay.same_video((admin or {}).get("video"), game.get("video"))
    anchors = ((admin or {}).get("anchors") or {}) if mine else {}
    out = {}
    for g in (replay.with_board(admin, game) or {}).get("goals") or []:
        score = str(g.get("score") or "")
        if not replay.SCORE_RE.fullmatch(score):
            continue
        b = board.get(score) or {}
        anchor = anchors.get(score)
        if g.get("exact"):
            t = anchor if g.get("src") in (None, "admin") else b.get("t")
            if isinstance(t, (int, float)):
                out[score] = wide_window(int(t), length)
        elif isinstance(b.get("win"), list) and len(b["win"]) == 2 and isinstance(b["win"][0], (int, float)):
            out[score] = cutjobs.run_window(b["win"], length)
        elif isinstance(b.get("change"), (int, float)):
            out[score] = cutjobs.change_window(int(b["change"]), length)
        elif isinstance(anchor, int):   # спор: кроме отметки человека, у гола ничего нет
            out[score] = wide_window(anchor, length)
    return out


def window_plan(game: dict, admin: dict | None, protocol: dict[str, dict]) -> tuple[list[tuple], list[str]]:
    """Что резать окнами и какие окна убрать: ([(счёт, начало, длина)], [счёт окна к удалению]). Окна нет у гола,
    которому положен точный клип (два свидетеля), у скрытого по просьбе игрока и у гола, которого нет в протоколе;
    окно, которое больше не нужно (появился точный клип, гол отменили, секунда пропала), уходит из бакета."""
    have = {s: c for s, c in (game.get("clips") or {}).items() if (c or {}).get("kind") == "window"}
    exact = clip_witnesses(game, admin) if game.get("src") != "club" else {}
    want = {}
    if game.get("src") != "club":   # запись клуба — только повтор ссылкой, без своих видео (ADR-030)
        for score, win in fan_windows(game, admin).items():
            x = protocol.get(score)
            if score in exact or not x or hidden_goal(x):
                continue
            want[score] = win
    cut = [(s, *w) for s, w in sorted(want.items())
           if ((have.get(s) or {}).get("from"), (have.get(s) or {}).get("len")) != w]
    return cut, [s for s in sorted(have) if s not in want]


def cut_window(src: str, headers: dict | None, start: int, span: int, out: Path, score: str,
               mark: tuple) -> tuple[Path, Path, float]:
    """Окно записи с голом в mp4 (480p, знак лиги) и обложка из середины окна. Не вышло — исключение."""
    name = f"{score.replace(':', '-')}-{start}w"
    clip, poster = out / f"win_{name}.mp4", out / f"win_{name}.jpg"
    for cmd in (pc.cut_cmd(src, headers, start, span, clip, mark, height=WIDE_HEIGHT),
                poster_cmd(src, headers, start + span // 2, poster)):
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if run.returncode != 0:
            raise RuntimeError((run.stderr.strip().splitlines() or ["ffmpeg без ошибки"])[-1][:200])
    return clip, poster, pc.duration(clip) or float(span)


def window_names(key: str, score: str, start: int) -> tuple[str, str]:
    """Имена файлов окна в бакете: начало окна в имени — поменялось окно, появился новый файл."""
    day, home, away = key.split("|")
    base = f"clips/{day}/{home}_{away}/{score.replace(':', '-')}-{start}w"
    return base + ".mp4", base + ".jpg"


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


def cut_pass(store: dict, league: dict | None, marked: dict, bucket: s3.Store, cut=cut_goal, stream=stream,
             track: "admin.Tracker | None" = None, cut_win=cut_window) -> int:
    """Нарезка: у каждого разобранного матча — клипы голов с двумя свидетелями и окна остальных повторов (ADR-037),
    выкладка в бакет, удаление видео скрытых и отменённых голов. После каждого матча — запись clips.json.
    Возвращает число новых видео. Нарезка на паузе (CUT) — ничего: клипов после стирания нет, убирать нечего."""
    if not bucket.ok or not CUT:
        return 0
    n = 0
    for key, game in sorted((store.get("games") or {}).items()):
        if not isinstance(game, dict) or not game.get("video"):
            continue
        protocol = league_goals(league, key)
        todo, drop = clip_plan(game, (marked or {}).get(key), protocol)
        wins, wdrop = window_plan(game, (marked or {}).get(key), protocol)
        if not todo and not drop and not wins and not wdrop:
            continue
        clips_ = game.setdefault("clips", {})
        for score in drop + wdrop:
            for name in clips_.pop(score, {}).get("files") or []:
                try:
                    bucket.delete(name)
                except Exception as err:
                    log.warning("%s %s: не удалили %s — %s", key, score, name, err)
            log.info("%s %s: видео убрано (точный клип вместо окна, скрытый игрок или гола нет в протоколе)",
                     key, score)
        if todo or wins:
            out = WORK / safe_name(key)
            out.mkdir(parents=True, exist_ok=True)
            (out / "mark.txt").write_text(pc.MARK, encoding="utf-8")
            (out / "source.txt").write_text(pc.SOURCE, encoding="utf-8")
            mark = (out / "mark.txt", out / "source.txt", pc.font_file())
            try:
                src, headers, _ = stream(game["video"], pc.FORMAT)
                vk_note(track, None)
            except Exception as err:   # VK не отдал — в следующий проход (ADR-012)
                log.warning("%s: поток для клипов не получили — %s: %s", key, type(err).__name__, err)
                vk_note(track, err)
                todo, wins = [], []
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
            for score, start, span in wins:   # окна повторов (ADR-037): гол в приложении и без двух свидетелей
                try:
                    clip, poster, dur = cut_win(src, headers, start, span, out, score, mark)
                    mp4_name, jpg_name = window_names(key, score, start)
                    mp4 = bucket.put(mp4_name, clip.read_bytes(), "video/mp4")
                    jpg = bucket.put(jpg_name, poster.read_bytes(), "image/jpeg")
                except Exception as err:
                    log.warning("%s %s: окно не вышло — %s: %s", key, score, type(err).__name__, err)
                    continue
                for name in (clips_.get(score) or {}).get("files") or []:   # прежнее окно — старые файлы
                    try:
                        bucket.delete(name)
                    except Exception:
                        pass
                x = protocol.get(score) or {}
                clips_[score] = {"kind": "window", "from": start, "len": span, "team": x.get("team"),
                                 "period": x.get("period"), "mp4": mp4, "poster": jpg, "dur": round(dur, 1),
                                 "files": [mp4_name, jpg_name], "cut": now_msk().isoformat(timespec="seconds")}
                for f in (clip, poster):
                    f.unlink(missing_ok=True)
                n += 1
                log.info("%s %s: окно %s, %d с", key, score, replay.fmt_t(start), span)
        store["updated"] = now_msk().isoformat(timespec="seconds")
        write_atomic(LIVE_DIR / "clips.json", store)
        if track is not None:
            track.flush()
    return n


def wipe(store: dict, bucket: s3.Store, mark: str = WIPE, live_dir: Path | None = None) -> int | None:
    """Стереть все клипы (ADR-030, дополнение 06.10, ночь), один раз на метку WIPE. Сначала копия clips.json рядом
    (`clips.before-wipe-<метка>.json`), потом из бакета — файлы по списку матчей и всё, что лежит под `clips/` (так
    уходит и то, что выпало из clips.json), у матчей — пустые клипы. Что бакет не удалил, — в `wipe_left`, следующий
    проход пробует снова; метка `wiped` ставится, когда не осталось ничего. Возвращает, сколько файлов убрано, или
    None — стирать нечего или нечем (метка уже стоит, ключей хранилища нет)."""
    if store.get("wiped") == mark or not bucket.ok:
        return None
    live_dir = live_dir or LIVE_DIR
    backup = live_dir / f"clips.before-wipe-{mark}.json"
    if not backup.exists():
        write_atomic(backup, store)
    names = set(store.get("wipe_left") or [])
    for game in (store.get("games") or {}).values():
        for c in ((game.pop("clips", None) or {}) if isinstance(game, dict) else {}).values():
            names.update(n for n in (c or {}).get("files") or [] if isinstance(n, str))
    try:
        names.update(bucket.list("clips/"))
    except Exception as err:   # не перечислил — убираем хотя бы то, что знаем по clips.json
        log.warning("стирание клипов: бакет не перечислил файлы — %s: %s", type(err).__name__, err)
    left = []
    for name in sorted(names):
        try:
            bucket.delete(name)
        except Exception as err:
            log.warning("стирание клипов: не удалили %s — %s", name, err)
            left.append(name)
    store.pop("wipe_left", None)
    if left:
        store["wipe_left"] = left
    else:
        store["wiped"] = mark
    store["updated"] = now_msk().isoformat(timespec="seconds")
    write_atomic(live_dir / "clips.json", store)
    log.info("стирание клипов %s: убрано файлов %d, не вышло %d", mark, len(names) - len(left), len(left))
    return len(names) - len(left)


# ---------- пульт (ADR-030, раздел 7) ----------

def catalog(store: dict, league: dict | None, marked: dict, today: date) -> dict[str, int]:
    """Каталог голов сезона для плиток пульта. Матчи — сыгранные с SINCE по league.json. `no_video` — без записи
    лиги и без ссылки админа: клипов у них не будет. Остальные — `goals` (голы протокола без буллитов; протокола ещё
    нет — голы, найденные табло), из них `timed` с точной секундой (`timed_admin` — от админа, `timed_auto` — часы и
    табло), `clips` с клипом, `ask` ждут ответа на превью и `mismatch` — табло нашло гол, которого нет в протоколе
    (лига отменила гол или поправила счёт). `no_board` — матчей с записью, где табло клуба-хозяина не размечено (ни
    секунд, ни превью, пока не разметят), `boards` — сколько таких клубов (ADR-030, дополнение 06.10). Покрытие
    повторами (ADR-031, по coverage): `m_total` сыгранных матчей, `m_full` — повтор у каждого гола, `m_none` — ни у
    одного, `g_replay` голов с повтором, `run` — точных по ходу часов, `two` — с двумя свидетелями: им клип."""
    days = season_days(today)
    games = store.get("games") or {}
    found = recordings(league, marked, days, games)
    out = dict.fromkeys(admin.CLIPS_GAUGES, 0)
    unmarked: set = set()
    for g in (league or {}).get("games") or []:
        if not (isinstance(g, dict) and g.get("date") in days and g.get("score")):
            continue
        key = f"{g['date']}|{g.get('home')}|{g.get('away')}"
        if key not in found:
            out["no_video"] += 1
            continue
        game = games.get(key) if isinstance(games.get(key), dict) else {}
        if game.get("status") == "no_board" and g.get("home") not in sb.BOARDS:
            out["no_board"] += 1
            unmarked.add(g.get("home"))
        admin_e = (marked or {}).get(key) if isinstance((marked or {}).get(key), dict) else None
        board = game.get("goals") or {}
        protocol = league_goals(league, key)
        scores = set(protocol) or set(board)
        seconds = goal_seconds(game, admin_e) if game else {
            s: (t, "admin") for s, t in ((admin_e or {}).get("anchors") or {}).items() if isinstance(t, int)}
        two = clip_witnesses(game, admin_e) if game and game.get("src") != "club" else {}
        anchors = (admin_e or {}).get("anchors") or {}
        have = game.get("clips") or {}
        out["goals"] += len(scores)
        for score in scores:
            if score in seconds:
                out["timed"] += 1
                out["timed_admin" if seconds[score][1] == "admin" else "timed_auto"] += 1
            elif isinstance((board.get(score) or {}).get("ask"), dict) and score not in anchors:
                out["ask"] += 1
            if score in have:
                out["wide" if (have.get(score) or {}).get("kind") == "window" else "clips"] += 1
            if seconds.get(score, (0, ""))[1] == "run":
                out["run"] += 1
            if score in two and score in protocol:   # клип можно резать (ADR-033, раздел 4)
                out["two"] += 1
        if protocol:
            out["mismatch"] += sum(1 for s in board if s not in protocol)
    out["boards"] = len(unmarked)
    for e in coverage(store, league, marked, today).values():
        out["m_total"] += 1
        out["g_replay"] += e["replays"]
        out["m_full"] += e["why"] == "ok"
        out["m_none"] += bool(e["goals"]) and not e["replays"] and e["why"] != "ok"
    return out


WHY = ("no_video", "gone", "pending", "error", "no_board", "not_found")   # почему у гола нет повтора (ADR-031)
STUCK = ("no_video", "gone", "pending", "error")   # матч стоит до голов: с самим голом человеку делать нечего
# Где гол на конвейере (ADR-036, раздел 4: вкладка «Голы» пульта), слова и цвета — DESIGN.md → «Пульт»
STATES = ("clip", "ready", "done", "confirm", "dispute", "approx", "search", "absent", "stuck")


def goal_states(game: dict, admin_e: dict | None, protocol: dict[str, dict], scores: set[str],
                entry: dict | None, why: str | None) -> dict[str, str]:
    """Статус каждого гола матча для пульта: `clip` — клип у болельщиков; `ready` — два свидетеля, клип режется;
    `done` — секунда точная, а клипа не будет (запись клуба, игрок скрыт); `confirm` — точная секунда одного
    свидетеля, нужно «✅ Гол виден»; `dispute` — отметка человека и табло не сошлись (ADR-033); `approx` — есть окно в
    пару минут (превью, ход часов, расчёт от опоры); `search` — где гол, не знает никто; `absent` — человек сказал, что
    в записи гола нет; `stuck` — матч стоит раньше голов (`why` из STUCK). Те же правила, что у clip_plan и
    replay.with_board: пульт и /replay не расходятся."""
    absent = set((admin_e or {}).get("absent") or [])
    mine = bool(admin_e) and bool(game) and replay.same_video(admin_e.get("video"), game.get("video"))
    spor = replay.disputed(admin_e, game) if mine else set()
    if game:
        seconds = goal_seconds(game, admin_e)
        two = clip_witnesses(game, admin_e)
    else:   # служба записи не разбирала: точны только отметки людей, проверить их нечем
        seconds = {s: (t, "admin") for s, t in ((admin_e or {}).get("anchors") or {}).items() if isinstance(t, int)}
        two = {}
    have = (game or {}).get("clips") or {}
    board = (game or {}).get("goals") or {}
    links = {g["score"]: g for g in (entry or {}).get("goals") or [] if isinstance(g, dict) and g.get("score")}
    out = {}
    for s in scores:
        x = protocol.get(s) or {}
        if s in absent:
            out[s] = "absent"
        elif why == "gone":
            # повторов по удалённой записи нет, что бы ни помнил разбор; отметил человек после удаления — его повтор
            out[s] = ("confirm" if links[s].get("exact") else "approx") if s in links else "stuck"
        elif s in have:
            out[s] = "clip"
        elif s in two:   # клип режут только голу протокола (clip_plan): протокола нет — пока «точно»
            out[s] = "done" if (game or {}).get("src") == "club" or hidden_goal(x) or not x else "ready"
        elif s in spor:
            out[s] = "dispute"
        elif s in seconds or (links.get(s) or {}).get("exact"):   # точная секунда одного свидетеля
            out[s] = "confirm"
        elif s in links or isinstance((board.get(s) or {}).get("ask"), dict):
            out[s] = "approx"
        elif why in STUCK:
            out[s] = "stuck"
        else:
            out[s] = "search"
    return out


def coverage(store: dict, league: dict | None, marked: dict, today: date) -> dict[str, dict]:
    """Разбор покрытия повторами (ADR-031): по каждому сыгранному с SINCE матчу — сколько голов (протокол без
    буллитов; нет протокола — найденные табло или счёт матча), у скольких есть повтор (как его поставит сборка:
    replay.with_board — клип, точная секунда или примерная), и если не у всех — одна причина на матч (`why`):
    `no_video` — записи нет ни у лиги, ни у клуба, ни у админа; `pending` — запись есть, служба ещё не разобрала;
    `gone` — записи больше нет в VK (удалена); `error` — VK не отдал или упал ffmpeg (`error` — текст);
    `no_board` — табло клуба-хозяина не размечено;
    `not_found` — табло разобрано, а этих голов не нашло ни оно, ни счёт хода. Голы, которых, по словам админа, в
    записи нет (`absent`), повтора не ждут. Голы без повтора — `missing`, почему табло их не взяло — `rejected`."""
    days = season_days(today)
    games = store.get("games") or {}
    found = recordings(league, marked, days, games)
    out = {}
    for g in (league or {}).get("games") or []:
        if not (isinstance(g, dict) and g.get("date") in days and g.get("score")):
            continue
        key = f"{g['date']}|{g.get('home')}|{g.get('away')}"
        game = games.get(key) if isinstance(games.get(key), dict) else {}
        admin_e = (marked or {}).get(key) if isinstance((marked or {}).get(key), dict) else None
        protocol = league_goals(league, key)
        scores = set(protocol) or {s for s in game.get("goals") or {} if replay.SCORE_RE.fullmatch(s)}
        sc = g.get("score") or {}
        total = len(scores) or sum(int(sc.get(k) or 0) for k in ("home", "away") if str(sc.get(k) or 0).isdigit())
        entry = replay.with_board(admin_e, game) if game else admin_e
        links = replay.by_score(entry or {})
        absent = set((admin_e or {}).get("absent") or [])
        have = scores & set(links)
        missing = sorted(scores - have - absent, key=sb.goal_rank) if scores else []
        e = {"goals": total, "replays": len(have)}
        rec = found.get(key)
        if rec:
            e["src"] = rec["src"]
        if absent & scores:
            e["absent"] = sorted(absent & scores, key=sb.goal_rank)
        if not missing and (scores or not total):
            e["why"] = "ok"
        elif not rec:
            e["why"] = "no_video"
        elif not game or not replay.same_video(game.get("video"), rec["video"]) or game.get("status") == "wait":
            e["why"] = "pending"
        elif game.get("status") == "gone":
            e.update(why="gone", error=game.get("error"))
        elif game.get("status") == "error":
            e.update(why="error" if game.get("tries", 0) >= TRIES else "pending", error=game.get("error"))
        elif game.get("status") == "no_board":
            e["why"] = "no_board"
        else:
            e["why"] = "not_found"
        if scores:   # статус каждого гола — для вкладки «Голы» пульта (ADR-036, раздел 4)
            states = goal_states(game, admin_e, protocol, scores, entry, e["why"])
            e["state"] = {s: states[s] for s in sorted(scores, key=sb.goal_rank)}
        if missing:
            e["missing"] = missing
            why = {s: r for s, r in (game.get("rejected") or {}).items() if s in missing}
            why.update({s: "админ: табло сбилось" for s in missing if (game.get("goals") or {}).get(s, {}).get("off")
                        == "wrong"})
            if why:
                e["rejected"] = why
        out[key] = e
    return out


# ---------- сторожа (ADR-034) ----------
# Служба сама смотрит на то, что сделала: инвариант сломался — об этом знает пульт и тревога админам (ADR-022),
# а не только тот, кто откроет live/clips.json. И раз в сутки — «канарейка»: жив ли yt-dlp, когда матчей нет.


def cover_drop(store: dict, now: datetime) -> list[dict]:
    """Повторов у матча не становится меньше: разбор только добавляет. Стало меньше — что-то потеряли (разбор
    переписал секунды, отметку отозвали зря). Считаем по матчам, а не одним числом: у записи, которой больше нет
    в VK (`gone`), повторы пропадают законно, и о ней своя тревога. Максимум дня — в `cover_top`, новый день
    начинает счёт заново: законную потерю (человек сказал «гола нет в записи») сторож не помнит вечно."""
    cover = store.get("coverage") or {}
    day = now.date().isoformat()
    top = store.get("cover_top") if isinstance(store.get("cover_top"), dict) else {}
    was = top.get("games") if top.get("day") == day and isinstance(top.get("games"), dict) else {}
    was = {k: v for k, v in was.items() if isinstance(v, list) and len(v) == 2}
    have = {k: [e.get("replays") or 0, e.get("goals") or 0] for k, e in cover.items()
            if isinstance(e, dict) and e.get("why") != "gone"}
    # голов у матча стало другое число — лига поправила протокол (гол отменили, счета сдвинулись): это не потеря
    fell = {k: (was[k][0], n) for k, (n, goals) in have.items()
            if k in was and goals == was[k][1] and n < was[k][0]}
    store["cover_top"] = {"day": day, "games": {k: [max(n, was[k][0]) if k in was and goals == was[k][1] else n, goals]
                                                for k, (n, goals) in have.items()}}
    if not fell:
        return []
    what = ", ".join(f"{k}: было {a}, стало {b}" for k, (a, b) in sorted(fell.items()))
    return [{"key": "cover", "text": f"Повторов стало меньше ({what}). Разбор что-то потерял — посмотри "
             "coverage в live/clips.json"}]


def invariants(store: dict, league: dict | None, marked: dict, now: datetime) -> list[dict]:
    """Что после прохода не может быть правдой (ADR-034): список {"key", "text"}.

    - Секунды голов матча идут в порядке протокола: гол, который по протоколу позже, не может быть раньше в записи.
    - Клип вырезан у смены счёта на табло: не позже неё (гола после смены не бывает) и не раньше чем за CLIP_BEFORE
      (05.10 клип 4:0 был вырезан на секунде гола 1:0 — именно это и ловим).
    - Покрытие повторами за день не падает (`cover_drop`)."""
    out = []
    for key, game in sorted((store.get("games") or {}).items()):
        if not isinstance(game, dict):
            continue
        e = (marked or {}).get(key) or {}
        title = match_title(league, key)
        seconds = goal_seconds(game, e)
        prev = None
        for score in [s for s in league_goals(league, key) if s in seconds]:   # порядок — протокола
            t = seconds[score][0]
            if prev and t < prev[1]:
                out.append({"key": f"order:{key}", "text": f"{title}: гол {score} на {replay.fmt_t(t)} раньше гола "
                            f"{prev[0]} на {replay.fmt_t(prev[1])} — секунды не в порядке протокола"})
                break
            prev = (score, t)
        board = game.get("goals") or {}
        for score, c in sorted((game.get("clips") or {}).items()):
            t, change = (c or {}).get("t"), (board.get(score) or {}).get("change")
            if not isinstance(t, (int, float)) or not isinstance(change, (int, float)):
                continue
            if t > change + CLIP_AFTER:
                out.append({"key": f"clip:{key}:{score}", "text": f"{title}: клип гола {score} на {replay.fmt_t(t)} "
                            f"позже смены счёта на табло ({replay.fmt_t(change)}) — вырезан мимо гола"})
            elif t < change - CLIP_BEFORE:
                out.append({"key": f"clip:{key}:{score}", "text": f"{title}: клип гола {score} на {replay.fmt_t(t)} "
                            f"раньше смены счёта ({replay.fmt_t(change)}) больше чем на {CLIP_BEFORE // 60} мин — "
                            "вырезан мимо гола"})
    return out + cover_drop(store, now)


def watch_pass(store: dict, league: dict | None, marked: dict, now: datetime,
               track: "admin.Tracker | None" = None) -> list[dict]:
    """Инварианты прохода — в журнал службы и в пульс для пульта и тревог. Новый максимум покрытия — на диск."""
    before = json.dumps(store.get("cover_top"), sort_keys=True, ensure_ascii=False)
    try:
        found = invariants(store, league, marked, now)
    except Exception:   # сторож не роняет службу
        log.exception("инварианты прохода не посчитались")
        return []
    for p in found:
        log.warning("сторож: %s", p["text"])
    if track is not None:
        track.info(invariants=found[:INV_MAX])
    if json.dumps(store.get("cover_top"), sort_keys=True, ensure_ascii=False) != before:
        store["updated"] = now_msk().isoformat(timespec="seconds")
        write_atomic(LIVE_DIR / "clips.json", store)
    return found


def canary_due(store: dict, now: datetime) -> bool:
    """Пора ли «канарейке»: раз в CANARY_EVERY от прошлой проверки, а первый раз — сразу."""
    last = admin.parse_iso((store.get("canary") or {}).get("at"))
    return last is None or (now - last).total_seconds() >= CANARY_EVERY


def canary_pass(store: dict, now: datetime, check=stream, track: "admin.Tracker | None" = None) -> bool | None:
    """«Канарейка» yt-dlp (ADR-034): раз в сутки спрашиваем VK об одной уже разобранной записи — даже когда
    разбирать нечего. VK меняет плеер, yt-dlp перестаёт отдавать записи, и в день без матчей об этом некому
    сказать: проход ничего не качает, счётчики пульта пустые, а на следующем матче повторов уже не будет.
    Записи больше нет в VK — это ответ по делу, значит yt-dlp жив. None — не пора или спрашивать нечего.
    Спрашиваем только о записи, которую служба уже разобрала (`ok`, `no_board`): запись, которая и раньше не
    скачивалась (`error` — приватная, не для этой страны), каждый день давала бы одну и ту же тревогу не о том."""
    if not canary_due(store, now):
        return None
    pick = next(((k, e) for k, e in sorted((store.get("games") or {}).items(), reverse=True)
                 if isinstance(e, dict) and isinstance(e.get("video"), str)
                 and e.get("status") in ("ok", "no_board")), None)
    if pick is None:
        return None
    key, game = pick
    got = {"at": now.isoformat(timespec="seconds"), "key": key, "video": game["video"], "ok": True}
    try:
        check(game["video"])
    except Exception as err:
        if gone_error(err):   # запись удалили — ответ по делу: yt-dlp работает
            got["gone"] = True
            log.info("канарейка: записи %s больше нет в VK — yt-dlp отвечает", game["video"])
        else:
            got.update(ok=False, error=f"{type(err).__name__}: {err}"[:200])
            log.warning("канарейка: VK не отдал запись %s — %s", game["video"], got["error"])
    else:
        log.info("канарейка: VK отдал запись %s", game["video"])
    # счётчики дня (`vk_ok`, `vk_fail`) канарейка не трогает: это запрос о старой записи, а по ним считается
    # тревога «VK сегодня не отдал ни одной записи» — удачная канарейка глушила бы её на весь день
    store["canary"] = got
    store["updated"] = now_msk().isoformat(timespec="seconds")
    write_atomic(LIVE_DIR / "clips.json", store)
    if track is not None:
        track.info(canary={k: v for k, v in got.items() if k != "key"})
    return got["ok"]


def write_coverage(store: dict, league: dict | None, marked: dict, now: datetime) -> bool:
    """Разбор покрытия — в clips.json (`coverage`): по нему бот пишет админам, почему у матчей нет повторов."""
    try:
        got = coverage(store, league, marked, now.date())
    except Exception:   # разбор не должен ронять службу
        log.exception("разбор покрытия не посчитался")
        return False
    if got == store.get("coverage"):
        return False
    store["coverage"] = got
    store["updated"] = now_msk().isoformat(timespec="seconds")
    write_atomic(LIVE_DIR / "clips.json", store)
    return True


def report(track: "admin.Tracker", store: dict, league: dict | None, marked: dict, now: datetime) -> None:
    """Снимок каталога — в счётчики дня и на диск: так пульт видит, что служба жива, даже когда разбирать нечего."""
    gone_note(track, store.get("games") or {}, league)
    try:
        for k, v in catalog(store, league, marked, now.date()).items():
            track.gauge(k, v)
    except Exception:   # счётчики не должны ронять службу
        log.exception("снимок каталога для пульта не посчитался")
    track.flush()


def clean_work(now: datetime, root: Path = WORK) -> None:
    """Папки матчей, которых не трогали KEEP_DAYS дней, — удалить: на сервере держим только временное (ADR-030).
    Считаем от разбора, не от дня матча: у матча начала сезона, разобранного сегодня, превью ещё ждут ответа."""
    if not root.is_dir():
        return
    edge = (now - timedelta(days=KEEP_DAYS)).timestamp()
    for d in root.iterdir():
        if d.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}_", d.name) and d.stat().st_mtime < edge:
            shutil.rmtree(d, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Голы по табло трансляции после матча (ADR-030)")
    ap.add_argument("--once", action="store_true", help="один проход и выйти")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if not shutil.which("ffmpeg"):
        sys.exit("Нет ffmpeg: apt install -y ffmpeg")
    track = admin.Tracker("clips")
    logging.getLogger().addHandler(admin.ErrorCount(track))
    bucket = s3.Store()
    if not bucket.ok:
        log.info("ключей хранилища нет (CLIPS_S3_KEY, CLIPS_S3_SECRET в /etc/rhl/bot.env) — клипы не режем")
    elif not CUT:
        log.info("нарезка клипов выключена: CLIPS_CUT=off в /etc/rhl/bot.env. Уберите строку — режем снова")
    while True:
        now = now_msk()
        store = read_json(LIVE_DIR / "clips.json")
        marked = read_json(LIVE_DIR / "replays.json").get("games") or {}
        league = sb.league_json(None)
        # нарезка на паузе или без ключей хранилища — видно в status/clips.json и на вкладке «Голы»
        track.info(cut="on" if CUT else "off", bucket=bucket.ok)
        try:
            gone = wipe(store, bucket)
            if gone is not None:
                track.note({"kind": "clips_wipe", "files": gone, "left": len(store.get("wipe_left") or [])})
            # сначала клипы того, что уже разобрано: они быстрые, а проход по новой записи — минуты, и перезапуск
            # службы (выкладка) посреди него не должен задерживать клипы (05.10 так и не дошло до нарезки)
            cut = cut_pass(store, league, marked, bucket, track=track)
            n, left = run_pass(store, league, marked, now, track=track)
            if n:
                log.info("проход: разобрано матчей %d, ждут разбора %d", n, left)
            alive_pass(store, now, track=track, league=league)   # не удалили ли VK запись, которую уже разобрали
            counted = clock_pass(store, league, marked, track=track)
            left = left or counted >= CLOCK_MAX   # счёт хода ждёт ещё матчей — следующий проход через минуту
            if n or counted:
                cut += cut_pass(store, league, marked, bucket, track=track)
            checks_pass(store, league, marked)   # вердикты по отметкам людей (ADR-033)
            write_coverage(store, league, marked, now)
            canary_pass(store, now, track=track)   # жив ли yt-dlp, даже когда матчей нет (ADR-034)
            watch_pass(store, league, marked, now, track=track)   # инварианты прохода (ADR-034)
            try:
                prepare_cuts(store, marked, now)   # видео для людей — заранее (ADR-036)
            except Exception as err:   # база занята — заготовки в следующий проход, разбор от этого не страдает
                log.warning("заготовки службе cuts не поставили — %s: %s", type(err).__name__, err)
            if cut:
                log.info("проход: новых клипов %d", cut)
            clean_work(now)
            if cut:
                track.add("clips_cut", cut)
        except Exception:   # служба не падает из-за одного прохода: следующий через EVERY
            log.exception("проход упал")
            track.info(scan=None)   # «Сейчас» на пульте не должно показывать разбор, которого уже нет
            left = 0
        report(track, store, league, marked, now_msk())
        if args.once:
            return
        time.sleep(60 if left else EVERY)   # VK отказал — следующая попытка не сразу (ADR-012)


if __name__ == "__main__":
    main()

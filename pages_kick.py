"""Служба pages: раз в 15 минут запускает сборку мини-аппа на GitHub Pages (ADR-015, дополнение 03.10.2026).

Расписание задания Pages (cron в pages.yml) GitHub соблюдает плохо: 3 октября 2026 из 47 запусков в сутки
доходило пять, раз в 3–6 часов, — и посты каналов в листе дня и ленте лиги отставали на полдня. Отсюда
сборку будим сами: workflow_dispatch задания pages.yml. Cron в pages.yml остаётся запасным.

    python pages_kick.py            служба: будит сборку, пока не придёт SIGTERM
    python pages_kick.py --once     один запуск сборки и выход

Токен — переменная PAGES_TOKEN в /etc/rhl/bot.env: fine-grained token GitHub только на этот репозиторий
с одним правом Actions: Read and write. Токена нет — служба молча ждёт и ничего не шлёт. Ночью
(02:00–07:00 МСК) не будим: постов мало, хватает cron.

Для пульта админа (ADR-021) тем же токеном раз в 15 минут читает список запусков Actions: итоги
заданий «Мини-апп», «Выложить бота», «Тесты» — в status/pages.json вместе с итогом своего пинка.
Хватает права Actions: Read, которое у токена уже есть.
"""
import argparse
import asyncio
import contextlib
import logging
import os
import signal
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import aiohttp

import admin

TZ = ZoneInfo("Europe/Moscow")
REPO = "ArPicasso/RHL-BOT"
WORKFLOW = "pages.yml"
EVERY = timedelta(minutes=15)
NIGHT_START, NIGHT_END = time(2, 0), time(7, 0)


def awake(now: datetime) -> bool:
    """Будим сборку всё время, кроме ночи по Москве."""
    t = now.astimezone(TZ).time()
    return not (NIGHT_START <= t < NIGHT_END)


def dispatch_url(repo: str = REPO, workflow: str = WORKFLOW) -> str:
    return f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches"


def runs_url(repo: str = REPO) -> str:
    return f"https://api.github.com/repos/{repo}/actions/runs?per_page=100"


def gh_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


async def runs(s: aiohttp.ClientSession, token: str, repo: str = REPO) -> list[dict] | None:
    """Последние запуски Actions всех заданий. Не вышло — None и строка в журнал без токена."""
    try:
        async with s.get(runs_url(repo), headers=gh_headers(token)) as r:
            if r.status == 200:
                data = await r.json()
                got = data.get("workflow_runs") if isinstance(data, dict) else None
                return got if isinstance(got, list) else None
            logging.warning("пульт: список запусков Actions не прочитался: HTTP %s", r.status)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        logging.warning("пульт: список запусков Actions не прочитался: %s", e.__class__.__name__)
    return None


async def watch(s: aiohttp.ClientSession, token: str, repo: str, track: admin.Tracker) -> None:
    """Итоги заданий для пульта (ADR-021) → status/pages.json."""
    got = await runs(s, token, repo)
    if got is not None:
        now = datetime.now(TZ)
        track.info(runs=admin.summarize_runs(got, now), runs_at=admin.iso(now))
    track.flush()


async def kick(s: aiohttp.ClientSession, token: str, repo: str = REPO) -> bool:
    """Запустить сборку на main. Ответ 204 — запущена; иначе — в журнал, без токена в тексте."""
    headers = gh_headers(token)
    try:
        async with s.post(dispatch_url(repo), json={"ref": "main"}, headers=headers) as r:
            if r.status == 204:
                return True
            text = (await r.text())[:200]
            hint = " — проверь PAGES_TOKEN: нужен доступ к репозиторию и право Actions: Read and write" \
                if r.status in (401, 403, 404) else ""
            logging.warning("сборка Pages не запущена: HTTP %s %s%s", r.status, text, hint)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logging.warning("сборка Pages не запущена: %s", e.__class__.__name__)
    return False


async def serve(token: str, repo: str, once: bool, track: admin.Tracker | None = None) -> None:
    track = track or admin.Tracker("pages")
    track.info(token=bool(token))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    if not token:
        logging.info("pages: PAGES_TOKEN не задан — сборку не будим, ждём остановки")
        track.flush()
        if not once:
            await stop.wait()
        return
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as s:
        if once:
            ok = await kick(s, token, repo)
            logging.info("pages: %s", "сборка запущена" if ok else "не вышло")
            return
        logging.info("pages: будим сборку %s каждые %d мин", repo, EVERY.seconds // 60)
        while not stop.is_set():
            if awake(datetime.now(TZ)):
                ok = await kick(s, token, repo)
                track.add("kick_ok" if ok else "kick_fail")
                track.info(**{"kick_ok" if ok else "kick_fail": admin.iso(datetime.now(TZ))})
            await watch(s, token, repo, track)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), EVERY.total_seconds())
    logging.info("pages: остановлена")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Служба pages: будит сборку мини-аппа на GitHub Pages (ADR-015)")
    ap.add_argument("--once", action="store_true", help="один запуск сборки и выход, без ночной тишины")
    ap.add_argument("--repo", default=os.environ.get("PAGES_REPO") or REPO, help=f"репозиторий, по умолчанию {REPO}")
    args = ap.parse_args()
    asyncio.run(serve(os.environ.get("PAGES_TOKEN", "").strip(), args.repo, args.once))


if __name__ == "__main__":
    main()

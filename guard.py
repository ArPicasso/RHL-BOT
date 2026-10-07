"""Сторож вне сервера (ADR-034): задание Actions раз в 15 минут смотрит на систему снаружи и пишет админам в
Telegram само, без сервера и без бота.

Тревоги (ADR-022) шлёт бот с того же сервера, за которым следит: умер сервер или бот — сказать об этом некому.
Этот сторож живёт на раннере GitHub и ничего из нашего не требует: спрашивает `/api/health` сервера, свежесть
`data/league.json` на Pages и — если дан токен агента — пульс бота из `/api/agent/status`, а потом пишет в Telegram
напрямую через Bot API.

Память между запусками — файл состояния (в задании его хранит кэш Actions): про одну и ту же поломку сторож
напоминает не чаще раза в час, а когда она прошла — говорит «Починилось». Разделы сообщения и правила повторов —
те же, что у тревог бота (`admin.alert_plan`), чтобы владелец читал одинаковые письма из двух мест.

Ночью (2:00–7:00 МСК, как у тревог бота) сторож молчит: поломку запоминает со временем, а утром скажет «не
отвечает с 03:12». Промах бывает и у живого сервера, поэтому спрашиваем трижды с паузой и тревожимся, только если
не ответил ни разу.

Только stdlib: на раннере нет ни aiohttp, ни aiogram.
"""
import html
import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import admin

TZ = ZoneInfo("Europe/Moscow")
log = logging.getLogger("guard")

TRIES = 3                                  # промах бывает: спрашиваем трижды, прежде чем звать на помощь
PAUSE = 10                                 # с между попытками
TIMEOUT = 20                               # с на запрос
LIMIT = 8 << 20                            # байт тела ответа: league.json сезона — четверть мегабайта, с запасом
STATE_FILE = Path(os.environ.get("GUARD_STATE") or "guard_state.json")
HEADS = {"broke": "🔴 <b>Сломалось</b>", "still": "🔴 <b>Не починилось</b>",
         "watch": "🟡 <b>Посмотреть</b>", "fixed": "✅ <b>Починилось</b>"}
PAGES = "https://arpicasso.github.io/RHL-BOT"   # мини-апп по умолчанию, как в bot.py: переменной может не быть
SIGN = "Сторож вне сервера (задание Actions раз в 15 минут)"
TG_API = "https://api.telegram.org"


def get(url: str, token: str = "", timeout: int = TIMEOUT) -> dict:
    """Запрос GET: {"code", "body"} или {"error"}. Тело длиннее LIMIT — `cut`: по обрезанному JSON ничего не скажем,
    и сторож говорит об этом, а не молчит. Токен — заголовком Bearer (пульт агента)."""
    req = urllib.request.Request(url, headers={"User-Agent": "rhl-guard"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(LIMIT + 1)
            return {"code": r.status, "body": body[:LIMIT].decode("utf-8", "replace"),
                    **({"cut": True} if len(body) > LIMIT else {})}
    except urllib.error.HTTPError as err:
        return {"code": err.code, "body": "", "error": f"ответ {err.code}"}
    except Exception as err:   # таймаут, DNS, оборванное соединение
        return {"error": f"{type(err).__name__}: {err}"[:200]}


def ask(url: str, token: str = "", tries: int = TRIES, pause: int = PAUSE, fetch=get) -> dict:
    """Тот же запрос несколько раз: ответил хоть раз — он и есть правда, ни разу — последняя ошибка."""
    got = {"error": "не спрашивали"}
    for n in range(tries):
        got = fetch(url, token)
        if got.get("code") == 200:
            return got
        if n + 1 < tries:
            time.sleep(pause)
    return got


def body_json(got: dict):
    """Тело ответа как JSON или None: сервер мог ответить страницей Caddy вместо нашего API."""
    try:
        return json.loads(got.get("body") or "")
    except (TypeError, ValueError):
        return None


def api_problems(base: str, got: dict) -> list[dict]:
    """Поломки сервера по ответу `/api/health`: не ответил совсем или ответил не тем."""
    if got.get("code") != 200:
        return [{"level": "bad", "key": "api",
                 "text": f"Сервер не отвечает ({base}): {got.get('error') or 'ответ ' + str(got.get('code'))}. "
                         "Мини-апп без живого счёта, прогнозов и зачёта, бот — без API. "
                         "Проверь systemctl status api и туннель"}]
    data = body_json(got)
    if not isinstance(data, dict) or not data.get("ok"):
        return [{"level": "bad", "key": "api:health",
                 "text": f"Сервер отвечает, но не говорит ok ({base}/health): {str(got.get('body') or '')[:120]}. "
                         "Похоже, до API доходит не наш ответ — проверь Caddy"}]
    return []


def bot_problems(got: dict) -> list[dict]:
    """Пульс бота снаружи: пульт агента (`/api/agent/status`) уже посчитал проблемы — берём те, что про бота.
    Тревоги о них шлёт сам бот, но мёртвый бот не пришлёт ничего, поэтому их говорит сторож."""
    data = body_json(got) if got.get("code") == 200 else None
    found = ((data or {}).get("pult") or {}).get("problems")
    if not isinstance(found, list):
        return []
    return [{"level": p.get("level") or "bad", "key": f"pult:{p['key']}", "text": str(p.get("text") or "").strip()}
            for p in found if isinstance(p, dict) and str(p.get("key") or "").startswith("bot:") and p.get("text")]


def league_problems(url: str, got: dict, now: datetime) -> list[dict]:
    """Жив ли мини-апп: отдаёт ли Pages `data/league.json` и когда его собрали. Ночью сборка редкая — не тревога."""
    if got.get("code") != 200:
        return [{"level": "bad", "key": "pages",
                 "text": f"Мини-апп не отдаёт данные ({url}): {got.get('error') or 'ответ ' + str(got.get('code'))}. "
                         "Проверь Settings → Pages и последний запуск сборки"}]
    data = body_json(got)
    at = admin.parse_iso((data or {}).get("updated")) if isinstance(data, dict) else None
    if at is None:
        why = (f"файл больше {LIMIT >> 20} МБ — сторож его не читает" if got.get("cut")
               else "это не JSON нашей сборки" if not isinstance(data, dict) else "в нём нет времени сборки")
        return [{"level": "warn", "key": "pages:updated",
                 "text": f"По данным мини-аппа ({url}) не видно, когда их собрали: {why}. Пока так, сторож не "
                         "скажет, что мини-апп встал"}]
    age = now - at
    if age > admin.LEAGUE_STALE and not quiet(now):
        return [{"level": "bad", "key": "pages:stale",
                 "text": f"Данные мини-аппа собраны {int(age.total_seconds() // 60)} мин назад: сборка Pages не "
                         "идёт. Проверь запуски задания «Мини-апп» и службу pages на сервере"}]
    return []


def quiet(now: datetime) -> bool:
    """Ночь (2:00–7:00 МСК): будить владельца нечем — поломку скажем утром, как и тревоги бота (ADR-022)."""
    return admin.NIGHT_FROM <= now.astimezone(TZ).hour < admin.NIGHT_TO


def since_text(problems_: list[dict], seen: dict, now: datetime) -> list[dict]:
    """К тексту поломки — с какого часа она идёт, если сторож видел её раньше: «не отвечает с 03:12» вместо
    молчаливого повтора. Возвращает поломки с дополненным текстом."""
    out = []
    for p in problems_:
        at = admin.parse_iso(seen.get(p["key"]))
        if at and now - at >= timedelta(minutes=20):
            p = {**p, "text": f"{p['text']} (с {at.astimezone(TZ).strftime('%H:%M')})"}
        out.append(p)
    return out


def fresh_seen(problems_: list[dict], seen: dict, now: datetime) -> dict:
    """Когда сторож впервые увидел каждую поломку: прошла — забыли."""
    keys = {p["key"] for p in problems_}
    return {k: (seen.get(k) or admin.iso(now)) for k in keys}


def message(groups: dict[str, list[str]]) -> str:
    """Письмо админам: те же разделы, что у тревог бота, и подпись — чтобы было видно, что пишет сторож."""
    parts = [HEADS[g] + "\n" + "\n".join(f"• {html.escape(t)}" for t in groups[g])
             for g in admin.ALERT_GROUPS if groups.get(g)]
    return "\n\n".join(parts + [f"<i>{SIGN}</i>"])


def send(token: str, chats: list[int], text: str, post=None) -> int:
    """Письмо в Telegram напрямую через Bot API. Сколько человек получило."""
    post = post or _post
    n = 0
    for chat in chats:
        try:
            post(f"{TG_API}/bot{token}/sendMessage",
                 {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
            n += 1
        except Exception as err:   # один адресат не получил — пишем остальным
            log.warning("не написали %s — %s: %s", chat, type(err).__name__, err)
    return n


def _post(url: str, data: dict) -> None:
    req = urllib.request.Request(url, data=json.dumps(data).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "rhl-guard"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        r.read(1 << 16)


def chats_of(value: str) -> list[int]:
    """Кому писать: ADMIN_IDS из секрета задания, как в /etc/rhl/bot.env."""
    return [int(x) for x in value.replace(",", " ").split() if x.strip().lstrip("-").isdigit()]


def look(api: str, pages: str, now: datetime, token: str = "", fetch=get) -> list[dict]:
    """Один обход: сервер, пульс бота (по токену агента) и данные мини-аппа. Список поломок."""
    out = []
    if api:
        health = ask(f"{api.rstrip('/')}/health", fetch=fetch)
        out += api_problems(api.rstrip("/"), health)
        if token and health.get("code") == 200:
            out += bot_problems(ask(f"{api.rstrip('/')}/agent/status", token=token, tries=1, fetch=fetch))
    if pages:
        url = f"{pages.rstrip('/')}/data/league.json"
        out += league_problems(url, ask(url, fetch=fetch), now)
    return out


def run(api: str, pages: str, token: str, chats: list[int], bot_token: str, now: datetime,
        state_file: Path = STATE_FILE, fetch=get, post=None) -> dict:
    """Обход, письмо и новая память. Возвращает память: её задание кладёт в кэш до следующего запуска."""
    was = admin.read_json(state_file, {}) or {}
    said = was.get("said") if isinstance(was.get("said"), dict) else {}
    seen = was.get("seen") if isinstance(was.get("seen"), dict) else {}
    found = look(api, pages, now, token=token, fetch=fetch)
    for p in found:
        log.warning("%s: %s", p["key"], p["text"])
    seen = fresh_seen(found, seen, now)
    groups, said = admin.alert_plan(since_text(found, seen, now), said, now)
    if groups and not quiet(now) and bot_token and chats:
        got = send(bot_token, chats, message(groups), post=post)
        log.info("письмо ушло: получили %d из %d", got, len(chats))
        if not got:   # не дошло ни до кого — не помним, что сказали: скажем в следующий обход (как bot.alerts_step)
            log.error("письмо не дошло ни до кого — повторим через 15 минут")
            return {"said": was.get("said") if isinstance(was.get("said"), dict) else {}, "seen": seen}
    elif groups and quiet(now):
        log.info("ночь (2:00–7:00 МСК) — не пишем, скажем утром: %s", ", ".join(sorted(p["key"] for p in found)))
        return {"said": was.get("said") if isinstance(was.get("said"), dict) else {}, "seen": seen}
    elif groups:
        log.error("писать некому: нет секрета BOT_TOKEN или ADMIN_IDS")
        return {"said": {}, "seen": seen}
    else:
        log.info("всё отвечает")
    return {"said": said, "seen": seen}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    api = (os.environ.get("LIVE_API") or "").strip().rstrip("/")
    pages = ((os.environ.get("WEBAPP_URL") or "").strip() or PAGES).rstrip("/")
    if not api:
        log.warning("адреса API нет: задай переменную LIVE_API или секрет DEPLOY_HOST — сервер не сторожим")
    state = run(api=api, pages=pages, token=(os.environ.get("AGENT_TOKEN") or "").strip(),
                chats=chats_of(os.environ.get("ADMIN_IDS") or ""),
                bot_token=(os.environ.get("BOT_TOKEN") or "").strip(), now=datetime.now(TZ))
    admin.write_atomic(STATE_FILE, state)


if __name__ == "__main__":
    main()

"""API «Звена» — ровно по контракту, раздел 4. База — /api/zveno.

Авторизация: `Authorization: tma <initData>`, пользователь — только из проверенной подписи.
Ошибка — `{"error": "текст по-русски"}` и HTTP 4xx. CORS — только адрес Pages.
"""
import json
import logging
from functools import partial

from aiohttp import web

from . import auth
from .config import PREFIX
from .game import ApiError, Game
from .leagues import Leagues

log = logging.getLogger("zveno_api.routes")
CFG = web.AppKey("cfg", object)
GAME = web.AppKey("game", Game)
LEAGUES = web.AppKey("leagues", Leagues)
JOBS = web.AppKey("jobs", object)
_dumps = partial(json.dumps, ensure_ascii=False)
MAX_BODY = 64 * 1024


def ok(obj, status: int = 200) -> web.Response:
    return web.json_response(obj, status=status, dumps=_dumps, headers={"Cache-Control": "no-store"})


def fail(status: int, text: str, **extra) -> web.Response:
    return ok({"error": text, **extra}, status)


def user(request: web.Request) -> auth.TgUser:
    game: Game = request.app[GAME]
    u = auth.from_header(request.headers.get("Authorization"), request.app[CFG].bot_token, game.now().timestamp())
    game.touch(u.id, u.display)
    return u


async def body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise ApiError(400, "Не получилось прочитать запрос. Обнови мини-апп и попробуй ещё раз.") from None
    if not isinstance(data, dict):
        raise ApiError(400, "Не получилось прочитать запрос. Обнови мини-апп и попробуй ещё раз.")
    return data


# ---------- обработчики ----------

async def health(request):
    game: Game = request.app[GAME]
    s = game.season()
    d = game.data
    return ok({"ok": True, "data": d.loaded, "updated": (d.tours or {}).get("updated"),
               "season": s.as_json() if s else None})


async def get_me(request):
    u = user(request)
    return ok(request.app[GAME].me(u.id))


async def delete_me(request):
    u = user(request)
    return ok(request.app[GAME].delete(u.id))


async def post_team(request):
    u = user(request)
    return ok(request.app[GAME].create_team(u.id, await body(request), u.display), 201)


async def get_team(request):
    u = user(request)
    t = request.query.get("tour")
    if t is not None and not t.isdigit():
        raise ApiError(400, "Номер тура — число.")
    return ok(request.app[GAME].team(u.id, int(t) if t else None))


async def put_lineup(request):
    u = user(request)
    return ok(request.app[GAME].set_lineup(u.id, await body(request)))


async def post_transfer(request):
    u = user(request)
    return ok(request.app[GAME].transfer(u.id, await body(request)))


async def post_boost(request):
    u = user(request)
    return ok(request.app[GAME].boost(u.id, await body(request)))


async def post_keep(request):
    u = user(request)
    return ok(request.app[GAME].keep(u.id, await body(request)))


async def get_leagues(request):
    u = user(request)
    return ok(request.app[LEAGUES].mine(u.id))


async def get_league(request):
    u = user(request)
    return ok(request.app[LEAGUES].table(u.id, request.match_info["id"]))


async def post_league(request):
    u = user(request)
    return ok(request.app[LEAGUES].create(u.id, await body(request)), 201)


async def post_join(request):
    u = user(request)
    return ok(request.app[LEAGUES].join(u.id, await body(request)))


async def get_journal(request):
    u = user(request)
    return ok(request.app[GAME].journal(u.id))


async def put_settings(request):
    u = user(request)
    return ok(request.app[GAME].settings(u.id, await body(request), u.display))


# ---------- обвязка ----------

CORS_METHODS = "GET, POST, PUT, DELETE, OPTIONS"
CORS_HEADERS = "Authorization, Content-Type"


@web.middleware
async def cors(request, handler):
    origin = request.headers.get("Origin")
    allowed = origin is not None and origin == request.app[CFG].origin
    if request.method == "OPTIONS":
        if not allowed:
            return fail(403, "Запрос не из мини-аппа РХЛ.")
        resp = web.Response(status=204)
    else:
        resp = await handler(request)
    if allowed:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Methods"] = CORS_METHODS
        resp.headers["Access-Control-Allow-Headers"] = CORS_HEADERS
        resp.headers["Access-Control-Max-Age"] = "86400"
    resp.headers["Vary"] = "Origin"
    return resp


@web.middleware
async def errors(request, handler):
    try:
        return await handler(request)
    except auth.AuthError as e:
        return fail(401, e.args[0])
    except ApiError as e:
        return fail(e.status, e.text, **e.extra)
    except web.HTTPNotFound:
        return fail(404, "Такого адреса в «Звене» нет.")
    except web.HTTPMethodNotAllowed:
        return fail(405, "Так к «Звену» обращаться нельзя.")
    except web.HTTPRequestEntityTooLarge:
        return fail(413, "Слишком большой запрос.")
    except web.HTTPException:
        raise
    except Exception:
        log.exception("%s %s failed", request.method, request.path)
        return fail(500, "Что-то сломалось на сервере «Звена». Попробуй ещё раз чуть позже.")


def setup(app: web.Application, prefix: str = PREFIX) -> None:
    r = app.router
    r.add_get(f"{prefix}/health", health)
    r.add_get(f"{prefix}/me", get_me)
    r.add_delete(f"{prefix}/me", delete_me)
    r.add_post(f"{prefix}/team", post_team)
    r.add_get(f"{prefix}/team", get_team)
    r.add_put(f"{prefix}/team/lineup", put_lineup)
    r.add_post(f"{prefix}/team/transfer", post_transfer)
    r.add_post(f"{prefix}/team/boost", post_boost)
    r.add_post(f"{prefix}/team/keep", post_keep)
    r.add_get(f"{prefix}/leagues", get_leagues)
    r.add_post(f"{prefix}/leagues", post_league)
    r.add_post(f"{prefix}/leagues/join", post_join)
    r.add_get(f"{prefix}/leagues/{{id}}", get_league)
    r.add_get(f"{prefix}/journal", get_journal)
    r.add_put(f"{prefix}/settings", put_settings)


def make_app(cfg, game: Game, jobs=None) -> web.Application:
    """Приложение aiohttp. jobs — фоновые задачи (zveno_api.jobs.Jobs) или None в тестах."""
    app = web.Application(middlewares=[cors, errors], client_max_size=MAX_BODY)
    app[CFG] = cfg
    app[GAME] = game
    app[LEAGUES] = Leagues(game)
    setup(app)
    if jobs is not None:
        app[JOBS] = jobs
        app.on_startup.append(jobs.start)
        app.on_cleanup.append(jobs.stop)
    return app

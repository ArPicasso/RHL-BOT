"use strict";
// «Звено» — фэнтези на наклейках игроков РХЛ (ADR-014). Интерфейс — DESIGN.md → «Звено».
// Файл грузится перед app.js и зовёт его общие функции (esc, figure, emblem, showSheet, coach…) только
// из отрисовки и обработчиков: на верхнем уровне здесь — только свои константы.
//
// Два режима. **Пролог** — без сервера, по опубликованным data/zveno/tours.json и pool.json: правила,
// наклейки по протоколам первых матчей, черновик звена на устройстве, «Позвать, когда откроется».
// **Рынок** — tours.json со статусом open и API из window.ZVENO_API (контракт «Звена», раздел 4).
// Для разработки: ?zveno_mock=1 — мок-сервер на устройстве (data/zveno/mock/api.js), =team — сразу со
// звеном, =prolog — Пролог с наклейками, =open — рынок только открылся, =start — первый день Пролога, =none — файлов нет.
// Всё, что пришло из данных, — только через esc(). Стоимость никогда не главное число на экране.

const Z_BUDGET = 100000;
const Z_CLUB_MAX = 3;
const Z_FEE_ICE = 600;
const Z_FEE_POINTS = 8;
const Z_SLOT = { F: "нападающий", D: "защитник", G: "ворота клуба" };
const Z_SLOT_SHORT = { F: "Нап", D: "Защ", G: "Ворота" };
const Z_SLOT_GEN = { F: "нападающих", D: "защитников", G: "ворот" };
// Места на льду: два звена по 3 нападающих и 2 защитника, ворота; запас — по порядку автозамен
const Z_MAIN = [["L1.F.0", "F"], ["L1.F.1", "F"], ["L1.F.2", "F"], ["L1.D.0", "D"], ["L1.D.1", "D"],
  ["L2.F.0", "F"], ["L2.F.1", "F"], ["L2.F.2", "F"], ["L2.D.0", "D"], ["L2.D.1", "D"], ["G", "G"]];
const Z_BENCH = [["B.0", "G"], ["B.1", "D"], ["B.2", "F"], ["B.3", "F"]];
const Z_ALL = Z_MAIN.concat(Z_BENCH);
// Наклейки шлёпаются на лёд в этом порядке: ворота, 2-е звено, 1-е, запас
const Z_SLAP = ["G", "L2.F.0", "L2.F.1", "L2.F.2", "L2.D.0", "L2.D.1", "L1.F.0", "L1.F.1", "L1.F.2", "L1.D.0", "L1.D.1", "B.0", "B.1", "B.2", "B.3"];

const Z_DRAFT_KEY = "zv_draft";   // черновик звена Пролога: на устройстве и в облаке Telegram
const Z_MY_KEY = "my_player";     // «Мой игрок» (ADR-010): id наклейки, на устройстве и в облаке Telegram
const Z_JOIN_KEY = "zv_join";     // код лиги из ссылки lg-<код>, пока «Звено» не открылось
const Z_SEEN_KEY = "zv_seen";     // «тур:очки» при прошлом заходе — для «С прошлого захода»

// Названия команд и лиг — только из конструктора (ADR-014, раздел 11). Список публикует движок в
// data/zveno/names.json, этот — запасной, если файла ещё нет
const Z_NAMES = {
  adj: ["Ледяные", "Звонкие", "Быстрые", "Смелые", "Северные", "Дерзкие", "Яркие", "Весёлые", "Хитрые", "Ловкие", "Крепкие", "Меткие", "Шустрые", "Отважные", "Бодрые", "Зоркие", "Снежные", "Морозные"],
  noun: ["ежи", "медведи", "лисы", "волки", "совы", "рыси", "барсы", "зубры", "еноты", "бобры", "моржи", "пингвины", "соколы", "снегири", "коньки", "клюшки", "шайбы", "кометы"],
};

const Z_ICE = '<svg class="ice-i" viewBox="0 0 24 24" role="img" aria-label="льдинок"><path d="M12 2.5v19M3.8 7.25l16.4 9.5M3.8 16.75l16.4-9.5M9.4 3.9 12 6.3l2.6-2.4M9.4 20.1 12 17.7l2.6 2.4M3.4 10.6l3.4-.9-.9-3.4M20.6 13.4l-3.4.9.9 3.4M5.9 17.7l.9-3.4-3.4-.9M18.1 6.3l-.9 3.4 3.4.9"/></svg>';
const Z_STAR = '<svg class="zs-star" viewBox="0 0 100 100" role="img" aria-label="Мой игрок"><path d="m50 6 12.5 27 29.5 3.5-22 20 6 29.5L50 71 23.5 86l6-29.5-22-20L37 33z"/></svg>';
const Z_I = {
  plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
  bell: '<svg viewBox="0 0 24 24"><path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/></svg>',
  gear: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 3.5v2.2M12 18.3v2.2M3.5 12h2.2M18.3 12h2.2M6 6l1.6 1.6M16.4 16.4 18 18M6 18l1.6-1.6M16.4 7.6 18 6"/></svg>',
  book: '<svg viewBox="0 0 24 24"><path d="M5 4.5h10.5a3 3 0 0 1 3 3v12H8a3 3 0 0 1-3-3z"/><path d="M5 16.5a3 3 0 0 1 3-3h10.5M9 8.5h6"/></svg>',
  log: '<svg viewBox="0 0 24 24"><path d="M7 5h12M7 12h12M7 19h12"/><circle cx="3.5" cy="5" r=".6"/><circle cx="3.5" cy="12" r=".6"/><circle cx="3.5" cy="19" r=".6"/></svg>',
  arrow: '<svg viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
  chev: '<svg class="chev" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></svg>',
  down: '<svg class="chev" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>',
  share: '<svg viewBox="0 0 24 24"><path d="M20 4 3 11l6.5 2.5L12 20z"/><path d="m9.5 13.5 4-4"/></svg>',
};
const Z_KIND = { own: "Своя лига", club: "Клубная", conf: "Лига конференции", step: "Ступень", month: "Месяц", circle: "Круг", overall: "Общий зачёт" };
const Z_KIND_ORDER = ["own", "club", "conf", "step", "month", "circle", "overall"];
const Z_SORT = { tour: "Очки за тур", season: "Очки за сезон", games: "Больше матчей в туре", cheap: "Сначала недорогие" };

const ZV = {
  tours: null, pool: null, byId: {}, names: Z_NAMES,
  loading: null, status: "",        // "" — не грузили, ok, none — файлов нет, fail — сеть
  seg: "ice", view: "",             // сегмент и состояние льда: points | squad
  me: undefined, meLoading: null, meFail: "",
  team: {}, local: null,            // Team по турам; правка состава до сохранения
  leagues: null, leaguesLoading: null, leaguesFail: false,
  market: { slot: "all", club: "", big: false, sort: "tour", all: false, out: "", limit: 40 },
  draft: null, build: null,         // черновик Пролога; звено, которое собирают в онбординге
  bookOpen: new Set(), deal: null, onbShown: false, delAsk: false, cloudPulled: false,
};
const ZC = { step: "" };            // шаг онбординга в карточке проводника

// ---------- режим, время, данные ----------

function zvMockMode() {
  try { return new URLSearchParams(location.search).get("zveno_mock") || ""; } catch (e) { return ""; }
}
function zvDir() {
  const m = zvMockMode();
  if (!m) return "data/zveno/";
  if (m === "1" || m === "team") return "data/zveno/mock/";
  if (m === "open") return "data/zveno/mock/open/";
  return `data/zveno/mock/${m.replace(/[^a-z]/g, "")}/`;
}
function zvApiBase() {
  const a = window.ZVENO_API;
  return typeof a === "string" && /^https:\/\/[^\s"'<>]+$/.test(a) ? a.replace(/\/+$/, "") : "";
}
const zvMocked = () => typeof window.ZVENO_MOCK_API === "function";
const zvServer = () => zvMocked() || (!!zvApiBase() && inTelegram && !!tg.initData);
const zvOpen = () => !!ZV.tours && ZV.tours.status === "open";
const zvLive = () => zvOpen() && zvServer();
const zvP = (id) => (id && ZV.byId[id]) || null;

// Время «Звена» — по Москве. В мок-режиме «сейчас» задаёт фикстура
function zvNow() {
  const m = ZV.tours && ZV.tours._mock_now;
  return m && zvMockMode() ? Date.parse(m) : Date.now();
}
function zvToday() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: TZ }).format(new Date(zvNow()));
}
function zvParts(iso) {
  const parts = new Intl.DateTimeFormat("ru-RU", { timeZone: TZ, weekday: "short", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
    .formatToParts(new Date(iso));
  return Object.fromEntries(parts.map((x) => [x.type, x.value]));
}
// «пн, 12 октября, 09:00»
function zvWhen(iso) {
  const p = zvParts(iso);
  return `${p.weekday}, ${p.day} ${p.month}, ${p.hour}:${p.minute}`;
}
function zvDaysTo(iso) {
  const d = new Intl.DateTimeFormat("en-CA", { timeZone: TZ }).format(new Date(iso));
  return Math.round((parseISO(d) - parseISO(zvToday())) / 864e5);
}
const zvFmt = (n) => String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0");   // «100 000» не рвётся
const zvIceN = (n) => `<span class="ice-n">${zvFmt(n)}${Z_ICE}</span>`;
const zvTour = (t) => (ZV.tours && ZV.tours.tours.find((x) => x.t === t)) || null;
const zvTourNow = () => (ZV.tours && ZV.tours.tour_now) || null;
const zvTourNext = () => (ZV.me && ZV.me.season && ZV.me.season.tour_next) || (ZV.tours && (ZV.tours.tour_next || ZV.tours.tour_now)) || (ZV.tours && ZV.tours.tours.length) || 1;
const zvGames = (t) => { const tr = zvTour(t); return tr ? tr.games || {} : {}; };

function zvScript(src) {
  return new Promise((done) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = () => done(true);
    s.onerror = () => done(false);
    document.head.appendChild(s);
  });
}
async function zvJson(name) {
  const r = await fetch(zvDir() + name, { cache: "no-cache" });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}
// Конструктор названий: движок публикует {adjectives, nouns} (контракт, раздел 2)
function zvNamesOf(d) {
  if (!d) return null;
  const adj = d.adjectives || d.adj;
  const noun = d.nouns || d.noun;
  const ok = (a) => Array.isArray(a) && a.length && a.every((x) => typeof x === "string" && x.length < 40);
  return ok(adj) && ok(noun) ? { adj, noun } : null;
}
function zvLoad(force = false) {
  if (ZV.loading && !force) return ZV.loading;
  ZV.loading = (async () => {
    try {
      const m = zvMockMode();
      if ((m === "1" || m === "team" || m === "open") && !zvMocked()) await zvScript("data/zveno/mock/api.js");
      const cached = zvCacheRead();
      const fresh = Promise.all([zvJson("tours.json"), zvJson("pool.json")]);
      if (cached && !ZV.tours) {
        zvUse(cached.tours, cached.pool, null);
        fresh.then(([t, p]) => {
          if (!t || (t.updated === cached.tours.updated && p && p.updated === cached.pool.updated)) return;
          zvUse(t, p, ZV.names);
          zvCacheWrite(t, p);
          zvPaintUnder();
        }).catch(() => {});
        return;
      }
      const [tours, pool] = await fresh;
      if (tours && pool) zvCacheWrite(tours, pool);
      zvUse(tours, pool, null);
    } catch (e) {
      ZV.status = "fail";
      ZV.loading = null;
    }
  })();
  return ZV.loading;
}

function zvUse(tours, pool, names) {
  ZV.tours = tours && Array.isArray(tours.tours) ? tours : null;
  ZV.pool = pool && Array.isArray(pool.players) ? pool : { players: [] };
  ZV.names = names || ZV.names || Z_NAMES;
  ZV.byId = {};
  ZV.pool.players.forEach((p) => { ZV.byId[p.id] = p; });
  ZV.status = ZV.tours ? "ok" : "none";
  if (!ZV.draft || !zvIds(ZV.draft).length) ZV.draft = zvDraftLoad();
  if (zvOpen() && zvServer() && !ZV.namesAsked) {
    ZV.namesAsked = true;
    zvJson("names.json").then((n) => { ZV.names = zvNamesOf(n) || ZV.names; }).catch(() => {});
  }
}
const Z_CACHE_KEY = "zv_cache";
function zvCacheRead() {
  if (zvMockMode()) return null;
  try {
    const d = JSON.parse(lsGet(Z_CACHE_KEY) || "null");
    return d && d.tours && Array.isArray(d.tours.tours) && d.pool && Array.isArray(d.pool.players) ? d : null;
  } catch (e) { return null; }
}
function zvCacheWrite(tours, pool) {
  if (!zvMockMode()) lsSet(Z_CACHE_KEY, JSON.stringify({ tours, pool }));
}

// Запрос к серверу «Звена». Пользователь — только из подписанного initData (контракт, раздел 4)
async function zvApi(method, path, body) {
  if (zvMocked()) return window.ZVENO_MOCK_API(method, path, body);
  let r;
  try {
    r = await fetch(zvApiBase() + path, {
      method,
      headers: Object.assign({ Authorization: `tma ${tg.initData}` }, body ? { "Content-Type": "application/json" } : {}),
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw new Error("Нет связи со «Звеном». Проверь интернет и попробуй ещё раз.");
  }
  let d = null;
  try { d = await r.json(); } catch (e) { d = null; }
  if (!r.ok) throw new Error((d && typeof d.error === "string" && d.error) || "«Звено» не ответило. Попробуй ещё раз.");
  return d;
}

function zvLoadMe(force = false) {
  if (!zvLive()) return Promise.resolve();
  if (ZV.meLoading && !force) return ZV.meLoading;
  ZV.meFail = "";
  ZV.meLoading = zvApi("GET", "/me")
    .then((d) => { ZV.me = d || { manager: null }; return ZV.me.manager ? zvLoadTeams() : null; })
    .catch((e) => { ZV.meFail = e.message; ZV.meLoading = null; });
  return ZV.meLoading;
}
async function zvLoadTeams() {
  const next = zvTourNext();
  const now = zvTourNow();
  const [a, b] = await Promise.all([zvApi("GET", `/team?tour=${next}`), now && now !== next ? zvApi("GET", `/team?tour=${now}`).catch(() => null) : null]);
  ZV.team = {};
  ZV.team[next] = a;
  if (b) ZV.team[now] = b;
  ZV.local = null;
  zvDue();
  zvLoadLeagues();
}
function zvLoadLeagues(force = false) {
  if (!zvLive() || !ZV.me || !ZV.me.manager) return Promise.resolve();
  if (ZV.leaguesLoading && !force) return ZV.leaguesLoading;
  ZV.leaguesFail = false;
  ZV.leaguesLoading = zvApi("GET", "/leagues")
    .then((l) => { ZV.leagues = Array.isArray(l) ? l : []; zvRepaintBand(); if (ZV.seg === "leagues") zvPaintBody(); })
    .catch(() => { ZV.leaguesLoading = null; ZV.leaguesFail = true; if (ZV.seg === "leagues" && !ZV.leagues) zvPaintBody(); });
  return ZV.leaguesLoading;
}

// ---------- состав ----------

function zvSqEmpty() {
  return { lineup: { G: null, L1: { F: [null, null, null], D: [null, null] }, L2: { F: [null, null, null], D: [null, null] } }, bench: [null, null, null, null], captain: null, assistant: null };
}
function zvGet(sq, key) {
  if (key === "G") return sq.lineup.G || null;
  const [a, b, c] = key.split(".");
  if (a === "B") return sq.bench[+b] || null;
  return (sq.lineup[a] && sq.lineup[a][b] && sq.lineup[a][b][+c]) || null;
}
function zvSet(sq, key, id) {
  if (key === "G") { sq.lineup.G = id; return; }
  const [a, b, c] = key.split(".");
  if (a === "B") sq.bench[+b] = id;
  else sq.lineup[a][b][+c] = id;
}
const zvSlotOf = (key) => (Z_ALL.find(([k]) => k === key) || [])[1];
const zvIsMain = (key) => !!key && !key.startsWith("B.");
const zvIds = (sq) => Z_ALL.map(([k]) => zvGet(sq, k)).filter(Boolean);
const zvKeyOf = (sq, id) => { const x = Z_ALL.find(([k]) => zvGet(sq, k) === id); return x ? x[0] : null; };
const zvClone = (sq) => JSON.parse(JSON.stringify(sq));
const zvSpent = (sq) => zvIds(sq).reduce((s, id) => s + (zvP(id) ? zvP(id).price : 0), 0);
function zvClubCount(sq, club, except = null) {
  return zvIds(sq).filter((id) => id !== except && zvP(id) && zvP(id).club === club).length;
}
// Состав с сервера → наш вид; пустые места — null
function zvSqOf(team) {
  const sq = zvSqEmpty();
  if (!team || !team.lineup) return sq;
  const L = team.lineup;
  sq.lineup.G = L.G || null;
  for (const l of ["L1", "L2"]) {
    for (const s of ["F", "D"]) sq.lineup[l][s] = sq.lineup[l][s].map((x, i) => (L[l] && L[l][s] && L[l][s][i]) || null);
  }
  sq.bench = sq.bench.map((x, i) => (team.bench && team.bench[i]) || null);
  sq.captain = team.captain || null;
  sq.assistant = team.assistant || null;
  return sq;
}
// Выбросить из состава то, чего нет в пуле (скрытый игрок, старый черновик), и поправить форму
function zvSqClean(raw) {
  const sq = zvSqEmpty();
  if (!raw || !raw.lineup) return sq;
  for (const [k, slot] of Z_ALL) {
    let id = null;
    try { id = zvGet(raw, k); } catch (e) { id = null; }
    const p = zvP(id);
    if (p && p.slot === slot && !zvIds(sq).includes(id)) zvSet(sq, k, id);
  }
  const ids = zvIds(sq);
  sq.captain = ids.includes(raw.captain) ? raw.captain : null;
  sq.assistant = ids.includes(raw.assistant) ? raw.assistant : null;
  return sq;
}
function zvDraftLoad() {
  try { return zvSqClean(JSON.parse(lsGet(Z_DRAFT_KEY) || "null")); } catch (e) { return zvSqEmpty(); }
}
function zvDraftSave() {
  const v = JSON.stringify(ZV.draft);
  lsSet(Z_DRAFT_KEY, v);
  if (cloud()) cloud().setItem(Z_DRAFT_KEY, v, () => {});
}
// Черновик и «Мой игрок» с другого устройства — из облака Telegram, если здесь пусто
function zvCloudPull() {
  if (ZV.cloudPulled || !cloud()) return;
  ZV.cloudPulled = true;
  cloud().getItems([Z_DRAFT_KEY, Z_MY_KEY], (err, v) => {
    if (err || !v) return;
    let changed = false;
    if (v[Z_MY_KEY] && !lsGet(Z_MY_KEY)) { lsSet(Z_MY_KEY, v[Z_MY_KEY]); changed = true; }
    if (v[Z_DRAFT_KEY] && !zvIds(ZV.draft || zvSqEmpty()).length) {
      try { ZV.draft = zvSqClean(JSON.parse(v[Z_DRAFT_KEY])); lsSet(Z_DRAFT_KEY, v[Z_DRAFT_KEY]); changed = true; } catch (e) { /* битое значение */ }
    }
    if (changed) zvPaint();
  });
}

// «Мой игрок» — только у владельца, на устройстве и в облаке. Снять звёздочку — удалить
const zvMy = () => lsGet(Z_MY_KEY) || "";
function zvSetMy(id) {
  if (id) lsSet(Z_MY_KEY, id);
  else try { localStorage.removeItem(Z_MY_KEY); } catch (e) { /* приватный режим */ }
  if (cloud()) {
    if (id) cloud().setItem(Z_MY_KEY, id, () => {});
    else cloud().removeItem(Z_MY_KEY, () => {});
  }
}
// «Мой игрок» — вратарь: звёздочка на воротах его клуба. У ворот клуба нет своего игрока в пуле,
// поэтому «Мой игрок» в воротах — это сама наклейка ворот
const zvIsMy = (id) => !!id && zvMy() === id;

// ---------- очки и точки матчей ----------

// Два лучших матча: индексы. При равенстве — более ранний
function zvBest2(m) {
  return new Set(m.map((v, i) => [v, i]).sort((a, b) => b[0] - a[0] || a[1] - b[1]).slice(0, 2).map((x) => x[1]));
}
function zvClubGames(club, t) {
  const tr = zvTour(t);
  if (!tr || !state.data) return [];
  return gamesOf(club).filter((g) => g.date >= tr.from && g.date <= tr.to);
}
function zvHasProtocol(g) {
  if (g.score) return true;
  const cut = zvMockMode() && ZV.tours && ZV.tours._mock_proto_before;
  return !!cut && g.date < cut;
}
// Матч уже прошёл: вчера и раньше или сегодня, через 2,5 часа после начала (время — московское)
function zvPlayed(g, today) {
  if (g.date < today) return true;
  if (g.date > today || !/^\d\d:\d\d$/.test(g.time || "")) return false;
  return zvNow() > Date.parse(`${g.date}T${g.time}:00+03:00`) + 150 * 60000;
}
// Точки: f — сыгран и в зачёте, o — сыгран, не в двух лучших, x — клуб сыграл без него,
// w — матч прошёл, ждём протокол, a — впереди
function zvDots(p, t) {
  const tr = zvTour(t);
  if (!tr) return [];
  const gs = zvClubGames(p.club, t);
  const n = gs.length || tr.games[p.club] || 0;
  const rec = p.tours && p.tours[String(t)];
  const m = (rec && rec.m) || [];
  const best = zvBest2(m);
  const today = zvToday();
  const proto = gs.filter(zvHasProtocol).length;
  const waiting = gs.filter((g) => !zvHasProtocol(g) && zvPlayed(g, today)).length;
  const d = m.map((v, i) => (best.has(i) ? "f" : "o"));
  for (let i = m.length; i < proto; i++) d.push("x");
  for (let i = 0; i < waiting; i++) d.push("w");
  while (d.length < n) d.push("a");
  return d.slice(0, 6);
}
const Z_DOT_WORD = { f: "в зачёте", o: "не в зачёте", x: "без него", w: "ждём протокол", a: "впереди" };
function zvDotsHTML(p, t) {
  const d = zvDots(p, t);
  const label = d.length ? `Матчи тура: ${d.map((k) => Z_DOT_WORD[k]).join(", ")}` : "";
  return `<span class="zs-dots"${label ? ` role="img" aria-label="${label}"` : ' aria-hidden="true"'}>${d.map((k) => `<i class="zd ${k}"></i>`).join("")}</span>`;
}
// Очки наклейки в туре: с сервера — с капитаном и сыгранностью, иначе сумма двух лучших из пула
function zvPts(id, t, team) {
  const b = team && team.points && team.points.by_id && team.points.by_id[id];
  if (b) return b.total != null ? b.total : ((b.best2 || 0) + (b.synergy || 0)) * (b.mult || 1);
  const p = zvP(id);
  const rec = p && p.tours && p.tours[String(t)];
  return rec ? rec.best2 || 0 : 0;
}
function zvSeasonPts(p) {
  return Object.values(p.tours || {}).reduce((s, r) => s + (r.best2 || 0), 0);
}
function zvTourPts(p, t) {
  const r = p.tours && p.tours[String(t)];
  return r ? r.best2 || 0 : 0;
}
// Ценность для автосборки: обещание очков и матчи в туре — считаются два лучших
function zvFactor(n) { return n <= 0 ? 0 : n === 1 ? 1 : 2 + 0.19 * (n - 2); }
function zvValue(p, games) {
  const n = games[p.club] != null ? games[p.club] : 1;
  return (p.promise || 0) * zvFactor(n) + (p.form ? 0.3 : 0);
}
const zvGamesWord = (n) => (n ? `${n} ${plural(n, "матч", "матча", "матчей")} в туре` : "нет матчей в туре");

// ---------- «Собери мне звено» ----------

// Автосборка на устройстве: «Мой игрок» первым, одноклубники — в одно звено, клубы с матчами в туре,
// не больше 3 из клуба, в пределах 100 000. Основа — по ценности с потолком цены, чтобы хватило на всех;
// запас — недорогие с матчами. Капитан — самый ценный полевой, ассистент — следующий
function zvAuto(o = {}) {
  const games = o.games || {};
  const budget = o.budget != null ? o.budget : Z_BUDGET;
  const sq = o.sq ? zvClone(o.sq) : zvSqEmpty();
  const pool = ZV.pool.players.filter((p) => p.status !== "rest");
  const taken = new Set(zvIds(sq));
  const clubN = {};
  zvIds(sq).forEach((id) => { const p = zvP(id); if (p) clubN[p.club] = (clubN[p.club] || 0) + 1; });
  let left = budget - zvSpent(sq);
  const place = (key, p) => { zvSet(sq, key, p.id); taken.add(p.id); clubN[p.club] = (clubN[p.club] || 0) + 1; left -= p.price; };
  const empties = () => Z_ALL.filter(([k]) => !zvGet(sq, k));
  const minOf = {};
  for (const s of ["F", "D", "G"]) minOf[s] = Math.min(...pool.filter((p) => p.slot === s).map((p) => p.price), Infinity);
  const reserve = (skip) => empties().filter(([k]) => k !== skip).reduce((s, [, sl]) => s + (isFinite(minOf[sl]) ? minOf[sl] : 0), 0);
  const fits = (p) => !taken.has(p.id) && (clubN[p.club] || 0) < Z_CLUB_MAX;
  const my = zvP(o.my);
  if (my && !taken.has(my.id) && fits(my)) {
    const key = (Z_MAIN.find(([k, s]) => s === my.slot && !zvGet(sq, k)) || Z_BENCH.find(([k, s]) => s === my.slot && !zvGet(sq, k)) || [])[0];
    if (key && my.price <= left - reserve(key)) place(key, my);
  }
  const mate = (p) => (my && p.club === my.club && p.id !== my.id ? 0.6 : 0);
  for (const [key, sl] of Z_MAIN) {
    if (zvGet(sq, key)) continue;
    const mainLeft = empties().filter(([k]) => zvIsMain(k)).length;
    const room = left - reserve(key);
    const cap = minOf[sl] + (Math.max(0, left - reserve()) / Math.max(1, mainLeft)) * 2.2;
    const cand = pool.filter((p) => p.slot === sl && fits(p) && p.price <= room);
    if (!cand.length) continue;
    const under = cand.filter((p) => p.price <= cap);
    const list = under.length ? under : cand;
    list.sort((a, b) => zvValue(b, games) + mate(b) - zvValue(a, games) - mate(a) || a.price - b.price);
    place(key, list[0]);
  }
  for (const [key, sl] of Z_BENCH) {
    if (zvGet(sq, key)) continue;
    const room = left - reserve(key);
    const cand = pool.filter((p) => p.slot === sl && fits(p) && p.price <= room);
    if (!cand.length) continue;
    const playing = cand.filter((p) => (games[p.club] || 0) > 0);
    const list = playing.length ? playing : cand;
    list.sort((a, b) => a.price - b.price || zvValue(b, games) - zvValue(a, games));
    place(key, list[0]);
  }
  if (!o.keepLines) zvRegroup(sq, my);
  zvPickCaptain(sq, games);
  return sq;
}
// Одноклубники — в одно звено: 1-е звено начинается с клуба «Моего игрока», дальше клубы по числу наклеек
function zvRegroup(sq, my) {
  const F = [...sq.lineup.L1.F, ...sq.lineup.L2.F].filter(Boolean);
  const D = [...sq.lineup.L1.D, ...sq.lineup.L2.D].filter(Boolean);
  const byClub = {};
  [...F, ...D].forEach((id) => { const c = zvP(id).club; (byClub[c] = byClub[c] || []).push(id); });
  const clubs = Object.keys(byClub).sort((a, b) => (my && a === my.club ? -1 : my && b === my.club ? 1 : byClub[b].length - byClub[a].length));
  const L = { L1: { F: [], D: [] }, L2: { F: [], D: [] } };
  for (const c of clubs) {
    const fs = byClub[c].filter((id) => zvP(id).slot === "F");
    const ds = byClub[c].filter((id) => zvP(id).slot === "D");
    const line = L.L1.F.length + fs.length <= 3 && L.L1.D.length + ds.length <= 2 ? "L1" : L.L2.F.length + fs.length <= 3 && L.L2.D.length + ds.length <= 2 ? "L2" : "";
    for (const id of fs) (line ? L[line].F : L.L1.F.length < 3 ? L.L1.F : L.L2.F).push(id);
    for (const id of ds) (line ? L[line].D : L.L1.D.length < 2 ? L.L1.D : L.L2.D).push(id);
  }
  for (const l of ["L1", "L2"]) {
    sq.lineup[l].F = [0, 1, 2].map((i) => L[l].F[i] || null);
    sq.lineup[l].D = [0, 1].map((i) => L[l].D[i] || null);
  }
}
function zvPickCaptain(sq, games) {
  const main = Z_MAIN.filter(([k, s]) => s !== "G").map(([k]) => zvGet(sq, k)).filter(Boolean);
  const ids = main.sort((a, b) => zvValue(zvP(b), games) - zvValue(zvP(a), games));
  if (!ids.includes(sq.captain)) sq.captain = ids[0] || null;
  if (!ids.includes(sq.assistant) || sq.assistant === sq.captain) sq.assistant = ids.find((x) => x !== sq.captain) || null;
}
function zvRandName() {
  const n = ZV.names || Z_NAMES;
  return [n.adj[Math.floor(Math.random() * n.adj.length)], n.noun[Math.floor(Math.random() * n.noun.length)]];
}
function zvNameOptions(k = 3) {
  const seen = new Set();
  const out = [];
  for (let i = 0; i < 40 && out.length < k; i++) {
    const x = zvRandName();
    const s = x.join(" ");
    if (!seen.has(s)) { seen.add(s); out.push(x); }
  }
  return out;
}
const zvName = (x) => (Array.isArray(x) ? x.join(" ") : String(x || ""));

// ---------- наклейки и лёд ----------

const zvSurname = (p) => (p.slot === "G" ? p.name : splitName(p.name)[0]);
function zvFig(p) {
  return figure({ kit: p.club, role: p.slot === "G" ? "G" : "F", number: p.slot === "G" ? null : p.number }) + emblem(p.club);
}

// Наклейка на льду. o: t — тур точек, team — Team с очками, view — points | squad | plain, sq — чей
// капитан, ctx — чей это лёд (ice, draft, build, book), mark — метка вместо пилюли, ord — номер в запасе
function zvStick(key, id, o) {
  const p = zvP(id);
  const sl = zvSlotOf(key) || (p && p.slot);
  const ord = o.ord ? `<span class="zs-ord">${o.ord}</span>` : "";
  const ctx = `data-zv-ctx="${o.ctx}"`;
  if (!p) {
    if (id) return `<span class="zs empty">${ord}<span class="zs-fig"><span class="zs-hole"></span></span><span class="zs-nm"><span>скрыт</span></span></span>`;
    return `<button type="button" class="zs empty" data-zv="slot" data-zv-arg="${key}" ${ctx} aria-label="Пустое место: ${Z_SLOT[sl]}">${ord}<span class="zs-fig"><span class="zs-hole">${Z_I.plus}</span></span><span class="zs-nm"><span>${Z_SLOT_SHORT[sl]}</span></span></button>`;
  }
  const cap = o.sq && o.sq.captain === id ? "К" : o.sq && o.sq.assistant === id ? "А" : "";
  const tr = o.t ? zvTour(o.t) : null;
  const n = tr ? tr.games[p.club] || 0 : null;
  const off = tr && n === 0;
  let bottom = "";
  if (p.status === "rest") bottom = '<span class="zs-tag">отдыхает</span>';
  else if (o.marks && o.marks[id]) bottom = `<span class="zs-tag">${o.marks[id]}</span>`;
  else if (off && zvIsMain(key)) bottom = '<span class="zs-pts none" title="Нет матчей в туре">нет игр</span>';
  else if (o.view === "points" && zvIsMain(key)) bottom = `<span class="zs-pts num">${zvPts(id, o.t, o.team)}${cap === "К" ? "<i>К</i>" : ""}</span>`;
  const nm = zvSurname(p);
  const lab = `${p.name}${cap === "К" ? ", капитан" : cap === "А" ? ", ассистент" : ""}${zvIsMy(id) ? ", мой игрок" : ""}`;
  const d = o.slap ? Math.max(0, Z_SLAP.indexOf(key)) * 80 : 0;
  return `<button type="button" class="zs${off ? " off" : ""}${o.lit === id ? " lit" : ""}${o.slap ? " slap" : ""}" data-zv="card" data-zv-arg="${esc(id)}" data-zv-key="${key}" ${ctx}${d ? ` style="--d:${d}ms"` : ""} aria-label="${esc(lab)}">
    ${ord}<span class="zs-fig">${cap ? `<span class="zs-patch" aria-hidden="true">${cap}</span>` : ""}${zvFig(p)}</span>
    <span class="zs-nm${nm.length > 9 ? " long" : ""}">${zvIsMy(id) ? Z_STAR : ""}<span>${esc(nm)}</span></span>
    ${tr ? zvDotsHTML(p, o.t) : ""}${bottom}</button>`;
}

function zvIce(sq, o) {
  const line = (L, n) => `<div class="zv-line-l">${n}-е звено</div><div class="zv-line" data-zv-line="${L}">${[0, 1, 2].map((i) => zvStick(`${L}.F.${i}`, sq.lineup[L].F[i], o)).join("")}<span class="zv-gap" aria-hidden="true"></span>${[0, 1].map((i) => zvStick(`${L}.D.${i}`, sq.lineup[L].D[i], o)).join("")}</div>`;
  const bench = sq.bench.map((id, i) => zvStick(`B.${i}`, id, Object.assign({}, o, { ord: i + 1 }))).join("");
  return `<div class="zv-ice" id="zv-ice" role="group" aria-label="Лёд: ворота и два звена">${o.note ? `<span class="zv-ice-note">${o.note}</span>` : ""}
    ${line("L1", 1)}<div class="zv-hr"></div>${line("L2", 2)}<div class="zv-hr"></div>
    <div class="zv-goal">${zvStick("G", sq.lineup.G, o)}</div></div>
    <div class="zv-bench" id="zv-bench" role="group" aria-label="Запас по порядку автозамен"><span class="lbl">Запас</span>${bench}</div>`;
}
function zvLegend() {
  return `<div class="zv-legend"><span><i class="zd f"></i>в зачёте</span><span><i class="zd"></i>не в зачёте</span><span><i class="zd w"></i>ждём протокол</span><span><i class="zd a"></i>впереди</span></div>`;
}

// Полоса тура: дедлайн → воскресенье сплошной, дальше пунктир до итога в четверг, точка — «сейчас»
function zvTourBar(t) {
  const tr = zvTour(t);
  if (!tr) return "";
  const a = Date.parse(tr.deadline);
  const sun = Date.parse(`${tr.to}T23:59:59+03:00`);
  const c = Date.parse(tr.close);
  const now = zvNow();
  let x = 0;
  if (now <= a) x = 0;
  else if (now <= sun) x = ((now - a) / (sun - a)) * 75;
  else x = 75 + Math.min(1, (now - sun) / (c - sun)) * 25;
  const dl = zvParts(tr.deadline);
  const cl = zvParts(tr.close);
  return `<div class="zv-tb" role="img" aria-label="Тур ${tr.t}: с ${esc(zvWhen(tr.deadline))} до воскресенья, итог — ${esc(zvWhen(tr.close))}">
    <div class="zv-tb-track"><i class="zv-tb-line"></i><i class="zv-tb-dash"></i><i class="zv-tb-tick" style="left:75%"></i><i class="zv-tb-now" style="left:${x.toFixed(1)}%"></i></div>
    <div class="zv-tb-labels"><span>${esc(dl.weekday)} ${esc(dl.hour)}:${esc(dl.minute)}</span><span>вс</span><span>итог ${esc(cl.weekday)}</span></div></div>`;
}
function zvDeadlineLine(t) {
  const tr = zvTour(t);
  if (!tr) return "";
  const d = zvDaysTo(tr.deadline);
  const left = d > 1 ? `через ${d} ${plural(d, "день", "дня", "дней")}` : d === 1 ? "завтра" : Date.parse(tr.deadline) > zvNow() ? "сегодня" : "";
  return `<div class="zv-dl" id="zv-dl">Дедлайн тура ${tr.t} — <b>${esc(zvWhen(tr.deadline))}</b>${left ? ` · ${left}` : ""}</div>`;
}

// ---------- экран ----------

function renderZveno() {
  if (!ZV.status) {
    zvLoad().then(() => { zvCloudPull(); zvPaint(); });
    return zvBandPlain() + `<div id="zv-body">${zvSkeleton()}</div>`;
  }
  return zvScreen();
}
function zvSkeleton() {
  return '<div class="sk" style="height:96px"></div><div class="sk sk-label"></div><div class="sk" style="height:360px"></div>';
}
function zvBandPlain(lede = true) {
  return `<section class="band peach zv-band"><h1>Звено</h1>${lede ? '<div class="lede">Игра на наклейках игроков РХЛ: ворота и два звена приносят очки за настоящие матчи.</div>' : ""}</section>`;
}
function zvScreen() {
  if (ZV.status === "fail") return zvBandPlain() + zvFailBlock("Не удалось загрузить «Звено». Проверь интернет.", "load");
  if (ZV.status === "none") return zvBandPlain() + zvWaiting();
  if (!zvLive()) return zvProlog();
  if (ZV.me === undefined) {
    zvLoadMe().then(zvPaint);
    return zvBandPlain(false) + `<div id="zv-body">${zvSkeleton()}</div>`;
  }
  if (ZV.meFail) return zvBandPlain() + zvFailBlock(esc(ZV.meFail), "me");
  if (!ZV.me.manager) return zvOnboardScreen();
  return zvMain();
}
function zvFailBlock(text, what) {
  const fig = guideFig(state.fav, "shrug");
  return `<div class="empty${fig ? " guide-empty" : ""}">${fig}<div>${text}<br><button type="button" class="retry" data-zv="retry" data-zv-arg="${what}">Повторить</button></div></div>`;
}
function zvGuideCard(pose, text, below = "") {
  const fig = guideFig(state.fav, pose);
  return `<div class="zv-guide${fig ? "" : " bare"}">${fig}<p>${text}</p>${below ? `<div class="zv-guide-below">${below}</div>` : ""}</div>`;
}

// Перерисовать «Звено» на месте: прокрутка и бегунки остаются
function zvPaint() {
  if (state.tab !== "zveno" || !state.fav) return;
  const screen = $("#screen");
  const prev = runnerState(screen);
  const y = window.scrollY;
  screen.innerHTML = renderZveno();
  addThemeToggle();
  placeRunners(screen, prev);
  window.scrollTo(0, y);
  zvMounted();
}
function zvPaintBody() {
  const box = $("#zv-body");
  if (!box || state.tab !== "zveno") return;
  const prev = runnerState(box);
  box.innerHTML = zvSegBody();
  placeRunners(box, prev);
  zvMounted();
}
function zvRepaintBand() {
  const line = $("#zv-tourline");
  if (line) line.outerHTML = zvTourLine();
}
// После каждой отрисовки: точка на вкладке, «С прошлого захода», онбординг
function zvMounted() {
  zvDue();
  if (zvLive() && ZV.me && !ZV.me.manager && !ZV.build && !ZV.onbShown && !$("#tour") && $("#sheet").hidden) {
    ZV.onbShown = true;
    setTimeout(() => { if (state.tab === "zveno" && !ZV.build && !$("#tour") && ZV.me && !ZV.me.manager) zcIntro(); }, calm() ? 0 : 420);
  }
}

// Точка «надо решить» на иконке вкладки: предупреждение автопилота или пустое место в основе
function zvDue(peeked = null) {
  const svg = document.querySelector('#tabs [data-tab="zveno"] svg');
  if (!svg) return;
  const t = peeked || ZV.team[zvTourNext()];
  const sq = t && zvSqOf(t);
  const due = !!t && ((t.warnings && t.warnings.length > 0) || Z_MAIN.some(([k]) => !zvGet(sq, k)));
  const dot = svg.querySelector(".due");
  if (due && !dot) svg.insertAdjacentHTML("beforeend", '<circle class="due" cx="20" cy="4.5" r="3.6"/>');
  if (!due && dot) dot.remove();
  svg.parentNode.setAttribute("aria-label", due ? "Звено — есть что решить" : "Звено");
}

// ---------- Пролог ----------

function zvWaiting() {
  return `${zvGuideCard("shrug", "«Звено» откроется, когда лига опубликует протоколы первых матчей. Наклейки игроков появятся здесь — загляни после первого тура.")}
    ${zvWaitCta()}${zvRulesBlock()}<div class="foot">Очки — только по протоколам лиги. Денег в «Звене» нет: льдинки нельзя купить.</div>`;
}
function zvWaitCta() {
  const bot = state.data && state.data.links && state.data.links.bot;
  if (!bot) return "";
  return `<div class="zv-cta"><button type="button" class="btn" data-zv="waitlist">${Z_I.bell}Позвать, когда откроется</button></div>`;
}
function zvRulesBlock() {
  return `<div class="label">Правила<span class="aside">очки за матч</span></div>
    <div class="zv-rules">
      <div class="zv-rule"><span>В заявке на матч</span><b class="num">+1</b></div>
      <div class="zv-rule"><span>Победа команды</span><b class="num">+1</b></div>
      <div class="zv-rule"><span>Гол<small>защитнику +6</small></span><b class="num">+5</b></div>
      <div class="zv-rule"><span>Передача</span><b class="num">+3</b></div>
      <p class="zv-rules-note">Ворота и два звена — 15 наклеек на <span class="ice-n">100&nbsp;000${Z_ICE}.</span> В туре считаются два лучших матча, капитан — вдвое. Минуса за матч не бывает.</p>
      <button type="button" class="zv-pill" data-zv="rules">Подробнее</button>
    </div>`;
}

function zvProlog() {
  const T = ZV.tours;
  const pool = ZV.pool.players;
  const clubs = new Set(pool.map((p) => p.club));
  const n = Math.min(26, T.clubs_with_protocol != null ? T.clubs_with_protocol : clubs.size);
  const first = zvTour(T.first_tour || T.tour_next || 1);
  let say;
  if (zvOpen() && zvApiBase()) say = "«Звено» открылось! Команда живёт в Telegram — открой приложение оттуда, чтобы собрать звено.";
  else if (zvOpen()) say = "Протоколы есть у всех 26 команд — «Звено» открывается. Совсем скоро здесь можно будет собрать звено.";
  else if (n >= 26) say = "Протоколы есть у всех 26 команд — «Звено» вот-вот откроется.";
  else say = `«Звено» откроется, когда у всех 26 команд будет протокол.${first ? ` Первый тур — с ${esc(dayMonth(first.from))}.` : ""}`;
  const bar = `<div class="zv-proto">Протоколы есть у ${n} ${plural(n, "команды", "команд", "команд")} из 26</div><div class="zv-proto-bar" aria-hidden="true">${Array.from({ length: 26 }, (x, i) => `<i${i < n ? ' class="on"' : ""}></i>`).join("")}</div>`;
  const join = lsGet(Z_JOIN_KEY) ? "<br><b>Тебя позвали в лигу</b> — вступишь, когда «Звено» откроется." : "";
  let html = `<section class="band peach zv-band"><h1>Звено</h1><div class="lede">Игра на наклейках игроков РХЛ: ворота и два звена приносят очки за настоящие матчи.</div></section>`;
  html += zvGuideCard("hello", `${say}${join}`, bar);
  html += zvWaitCta();
  html += zvRulesBlock();
  if (pool.length) {
    const sq = ZV.draft || zvSqEmpty();
    const left = Z_BUDGET - zvSpent(sq);
    html += `<div class="label">Черновик звена<span class="aside">на этом устройстве</span></div>`;
    html += zvIce(sq, { view: "plain", sq, ctx: "draft" });
    html += `<div class="zv-bank"><span>${zvIds(sq).length ? `${zvIds(sq).length} из 15 наклеек` : "Нажми на наклейку ниже — положу в черновик"}</span><span>Осталось <b>${zvIceN(left)}</b></span></div>`;
    if (zvIds(sq).length < 15) html += `<button type="button" class="zv-pill" data-zv="draft-fill">Собери за меня</button>`;
    if (zvIds(sq).length) html += ` <button type="button" class="zv-pill" data-zv="draft-clear">Очистить</button>`;
  }
  html += `<div class="label">Наклейки<span class="aside">по протоколам первых матчей</span></div>`;
  html += zvBook(pool);
  html += `<div class="foot">Наклейки появляются по протоколам: игрок попадает сюда после первого матча в заявке. Стоимость считается по игре в лиге, денег в «Звене» нет — льдинки нельзя купить.</div>`;
  return html;
}

// Стикербук: клубы, свой — первым; у своего все наклейки, у остальных — ряд и «Показать все»
function zvBook(pool) {
  const byClub = {};
  pool.forEach((p) => { (byClub[p.club] = byClub[p.club] || []).push(p); });
  const all = (state.data ? state.data.teams : []).map((t) => t.id);
  const order = Object.keys(byClub).sort((a, b) => (a === state.fav ? -1 : b === state.fav ? 1 : team(a).name.localeCompare(team(b).name, "ru")));
  const rank = { G: 0, D: 1, F: 2 };
  let html = "";
  if (!order.length) {
    html += `<div class="empty">Протоколов пока нет — первые наклейки появятся после матчей ${esc(fmtLong(state.data.games[0] ? state.data.games[0].date : zvToday()))}.</div>`;
  } else {
    html += '<div class="zv-book">';
    for (const c of order) {
      const list = byClub[c].slice().sort((a, b) => rank[a.slot] - rank[b.slot] || (a.number || 0) - (b.number || 0));
      const open = c === state.fav || ZV.bookOpen.has(c);
      const shown = open ? list : list.slice(0, 4);
      html += `<div class="zv-club">${emblem(c)}<span>${esc(team(c).name)}</span><small>${list.length} ${plural(list.length, "наклейка", "наклейки", "наклеек")}</small></div>
        <div class="zv-grid">${shown.map((p) => zvBookSticker(p)).join("")}</div>`;
      if (!open && list.length > 4) html += `<button type="button" class="zv-pill" data-zv="book-open" data-zv-arg="${esc(c)}">Показать все ${list.length}</button>`;
    }
    html += "</div>";
  }
  const wait = all.filter((c) => !byClub[c]);
  if (wait.length && order.length) {
    html += `<div class="label">Ждём протокол<span class="aside">${wait.length} ${plural(wait.length, "команда", "команды", "команд")}</span></div><div class="zv-wait">${wait.map((c) => `<span title="${esc(team(c).name)}">${emblem(c, "md")}</span>`).join("")}</div>`;
  }
  return html;
}
function zvBookSticker(p) {
  const nm = zvSurname(p);
  const inDraft = ZV.draft && zvIds(ZV.draft).includes(p.id);
  return `<button type="button" class="zs${inDraft ? " lit" : ""}" data-zv="card" data-zv-arg="${esc(p.id)}" data-zv-ctx="book" aria-label="${esc(p.name)}${inDraft ? ", в черновике" : ""}">
    <span class="zs-fig">${zvFig(p)}</span><span class="zs-nm${nm.length > 9 ? " long" : ""}">${zvIsMy(p.id) ? Z_STAR : ""}<span>${esc(p.slot === "G" ? "Ворота" : nm)}</span></span>
    <span class="zs-sub">${p.slot === "G" ? "клуба" : Z_SLOT_SHORT[p.slot].toLowerCase()}</span></button>`;
}

// Правила целиком — листом
function zvRulesSheet() {
  const row = (a, f, d) => `<div class="zv-rt-row${d == null ? " one" : ""}"><span>${a}</span><b class="num">${f}</b>${d != null ? `<b class="num">${d}</b>` : ""}</div>`;
  showSheet(`<div class="grab"></div>
    <div class="sheet-head"><span class="when">Правила «Звена»</span><button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    <h2 class="lead-title">Как считаем<span>очки</span></h2>
    <p class="lead-about">Только по протоколам лиги. За матч — от нуля и выше: минуса у игрока не бывает. В туре считаются два лучших матча каждого — неудачный матч тур не испортит.</p>
    <div class="label">Полевые</div>
    <div class="zv-rt"><div class="zv-rt-row head"><span></span><small>нап.</small><small>защ.</small></div>${row("В заявке", "+1", "+1")}${row("Победа команды", "+1", "+1")}${row("Гол", "+5", "+6")}${row("Передача", "+3", "+3")}${row("Плюс-минус, за единицу", "±1", "±1")}${row("Каждые 2 броска в створ", "+1", "+1")}${row("Победный гол или решающий буллит", "+2", "+2")}${row("Автор и ассистент гола в одном твоём звене", "+1", "+1")}${row("Удаление до конца матча", "0", "0")}</div>
    <p class="note">Штрафные минуты не считаются. Броски серии буллитов — не голы.</p>
    <div class="label">Ворота клуба</div>
    <div class="zv-rt">${row("Клуб сыграл", "+2")}${row("Победа, с буллитами", "+3")}${row("Каждые 4 отражённых", "+1")}${row("Пропущенная шайба", "−2")}${row("«Сухарь»: ноль в игре и овертайме", "+5")}${row("Гол или передача вратаря", "+5 / +3")}</div>
    <p class="note">Очки приносит тот, кто стоит в воротах клуба в этот день. За матч ворота тоже не уходят в минус.</p>
    <div class="label">Состав и тур</div>
    <div class="facts-card"><dl class="facts">
      <dt>Основа</dt><dd>ворота и два звена: 6 нап. и 4 защ.</dd>
      <dt>Запас</dt><dd>ворота, защ. и 2 нап.</dd>
      <dt>Из одного клуба</dt><dd>не больше 3, ворота считаются</dd>
      <dt>Капитан «К»</dt><dd>очки вдвое</dd>
      <dt>Ассистент «А»</dt><dd>вдвое, если у «К» нет матчей</dd>
      <dt>Тур</dt><dd>неделя пн–вс по Москве</dd>
      <dt>Дедлайн</dt><dd>понедельник, 09:00 МСК</dd>
      <dt>Итог тура</dt><dd>четверг, 12:00</dd>
      <dt>Обмены</dt><dd>1 бесплатный за тур, копятся до 5</dd>
      <dt>Лишний обмен</dt><dd>8 очков или 600 льдинок</dd>
      <dt>Бюджет</dt><dd>100 000 льдинок</dd>
    </dl></div>
    <p class="note">Стоимость наклейки считается по игре в лиге после каждого протокола и за тур меняется не больше чем на 500. Кто не играл, тот не дешевеет. Льдинки нельзя купить, передать или вывести. Не успел до дедлайна — автопилот заменит того, кто не играет. «Моего игрока» он не трогает.</p>`);
}

// ---------- онбординг ----------

function zvOnboardScreen() {
  const b = ZV.build;
  const next = zvTourNext();
  let html = `<section class="band peach zv-band"><h1>Звено</h1><div class="lede">Собери ворота и два звена — они принесут очки за настоящие матчи.</div>${zvDeadlineLine(next)}</section>`;
  const sq = b ? b.sq : zvSqEmpty();
  html += zvIce(sq, { t: next, view: "squad", sq, ctx: "build", slap: b && b.slap });
  const left = Z_BUDGET - zvSpent(sq);
  html += `<div class="zv-bank"><span>${zvIds(sq).length} из 15 наклеек</span><span>Осталось <b id="zv-left">${zvIceN(left)}</b></span></div>`;
  html += '<div class="zv-cta">';
  if (!b) html += `<button type="button" class="btn" data-zv="auto">Собери мне звено</button><button type="button" class="btn ghost" data-zv="self">Соберу сам</button>`;
  else if (zvIds(sq).length < 15) html += `<button type="button" class="btn" data-zv="fill">Добрать за меня</button>`;
  else if (!ZC.step) html += `<button type="button" class="btn" data-zv="done">Готово — назвать команду</button>`;
  html += "</div>";
  if (b && b.slap) b.slap = false;
  return html + `<div class="foot">15 наклеек на 100 000 льдинок, не больше 3 из одного клуба. Льдинки нельзя купить.</div>`;
}

// Карточка проводника в онбординге. Кнопки — data-zc: их ловит zvClick раньше app.js
function zcCard(pose, text, buttons) {
  coach(state.fav, guideOf(state.fav) ? pose : "", text, buttons, "«Звено»");
}
function zcAt(el, pose, text, i, next = "Дальше") {
  if (!el) return zcAct(i === 3 ? "name" : `tip${i + 1}`);
  const btns = `<button type="button" class="btn" data-zc="${i === 3 ? "name" : `tip${i + 1}`}">${next}</button>
    <div class="coach-row"><button type="button" class="coach-skip" data-zc="name">Пропустить</button><span class="tp" aria-label="Шаг ${i} из 3"><b>${i} из 3</b>${[1, 2, 3].map((k) => `<i class="${k <= i ? "done" : ""}"></i>`).join("")}</span></div>`;
  coachAt(el, { pose: guideOf(state.fav) ? pose : "", text, buttons: btns });
  const b = $("#tour [data-zc]");
  if (b) b.focus({ preventScroll: true });
}
function zcIntro() {
  ZC.step = "intro";
  const text = state.zvJoin
    ? "Привет! Тебя позвали в лигу. «Звено» — игра на наклейках игроков РХЛ: ворота и два звена приносят очки за настоящие матчи. Соберём звено — и ты в таблице."
    : "Привет! Это «Звено» — игра на наклейках игроков РХЛ. Собираешь ворота и два звена, они приносят очки за настоящие матчи.";
  zcCard("hello", text,
    `<button type="button" class="btn" data-zc="auto">Собери мне звено</button><button type="button" class="coach-skip" data-zc="self">Соберу сам</button>`);
}
function zcEnd() {
  ZC.step = "";
  closeCoach();
}
async function zcAct(act) {
  haptic();
  const next = zvTourNext();
  const sq = ZV.build && ZV.build.sq;
  if (act === "auto") { zcEnd(); return zvStartAuto(); }
  if (act === "self") { zcEnd(); return zvStartSelf(); }
  if (act === "ok") return zcEnd();
  if (act === "tip1") {
    ZC.step = "tip1";
    const cap = sq && sq.captain && zvP(sq.captain);
    const el = cap && document.querySelector(`#zv-ice [data-zv-key="${zvKeyOf(sq, cap.id)}"]`);
    const n = cap ? zvGames(next)[cap.club] || 0 : 0;
    return zcAt(el, "point", `Это капитан, его очки идут вдвое. «К» получил ${cap ? esc(zvSurname(cap)) : "лучший по игре"}${n ? ` — ${n} ${plural(n, "матч", "матча", "матчей")} на неделе` : ""}. Потом можно отдать «К» другому.`, 1);
  }
  if (act === "tip2") {
    ZC.step = "tip2";
    return zcAt(document.querySelector('#zv-ice [data-zv-line="L1"]'), "point", "Точки — матчи на этой неделе. Считаются два лучших, так что неудачный матч тур не испортит. Минуса не бывает.", 2);
  }
  if (act === "tip3") {
    ZC.step = "tip3";
    const tr = zvTour(next);
    const my = zvP(zvMy());
    const when = tr ? zvParts(tr.deadline) : null;
    const whenText = when ? ({ пн: "понедельника", вт: "вторника", ср: "среды", чт: "четверга", пт: "пятницы", сб: "субботы", вс: "воскресенья" }[when.weekday] || when.weekday) + `, ${when.hour}:${when.minute}` : "дедлайна";
    const keep = my && sq && zvIds(sq).includes(my.id) ? ` ${esc(zvSurname(my))} не трону.` : "";
    return zcAt($("#zv-dl") || $("#zv-ice"), "point", `Менять состав можно до ${whenText}. Не успеешь — присмотрю сам: кто не играет, того заменю.${keep}`, 3);
  }
  if (act === "name") { zcEnd(); return zvNameSheet("team"); }
  if (act === "invite") {
    zvShareApp(state.zvJoinTitle ? "lg" : "club");
    return zcWrite();
  }
  if (act === "later") return zcWrite();
  if (act === "write") {
    ZC.step = "write-wait";
    const done = () => zcFinal();
    try { tg.requestWriteAccess(() => done()); } catch (e) { done(); }
    return;
  }
  if (act === "final" || act === "nowrite") return zcFinal();
  if (act === "go") return zcEnd();
}
function zcMiss() {
  const btn = $("#tour .coach-card .btn");
  if (btn && !calm()) btn.animate([{ transform: "none" }, { transform: "scale(1.04)" }, { transform: "none" }], { duration: 200, easing: EASE_OUT });
}

async function zvStartAuto() {
  const my = zvP(zvMy());
  if (!my) return zvFirstSheet();
  zvDoBuild(my.id);
}
// «Кого берём первым?» — стикербук своего клуба, по 4 в ряд. Выбор — «Мой игрок»
function zvFirstSheet() {
  const mine = ZV.pool.players.filter((p) => p.club === state.fav && p.status !== "rest");
  if (!mine.length) return zvDoBuild(null);
  const rank = { G: 0, D: 1, F: 2 };
  mine.sort((a, b) => rank[a.slot] - rank[b.slot] || (a.number || 0) - (b.number || 0));
  showSheet(`<div class="grab"></div>
    <div class="sheet-head"><span class="when">«Мой игрок»</span><button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    <h2 class="lead-title">Кого берём первым?</h2>
    <p class="lead-about">Выбирай из «${esc(team(state.fav).name)}». Он встанет в звено первым, и автопилот его не тронет. Звёздочку видишь только ты.</p>
    <div class="zv-grid">${mine.map((p) => `<button type="button" class="zs" data-zv="first" data-zv-arg="${esc(p.id)}" aria-label="${esc(p.name)}"><span class="zs-fig">${zvFig(p)}</span><span class="zs-nm${zvSurname(p).length > 9 ? " long" : ""}"><span>${esc(p.slot === "G" ? "Ворота" : zvSurname(p))}</span></span><span class="zs-sub">${p.slot === "G" ? "клуба" : Z_SLOT_SHORT[p.slot].toLowerCase()}</span></button>`).join("")}</div>
    <div class="zv-sheet-btns"><button type="button" class="btn ghost" data-zv="first" data-zv-arg="">Без моего игрока</button></div>`);
}
function zvDoBuild(my) {
  closeMatch();
  const next = zvTourNext();
  ZV.build = { sq: zvAuto({ games: zvGames(next), my }), manual: false, slap: !calm() };
  zvPaint();
  zvCountDown();
  const n = zvIds(ZV.build.sq).length;
  setTimeout(() => {
    if (!ZV.build || state.tab !== "zveno") return;
    ZC.step = "built";
    const left = Z_BUDGET - zvSpent(ZV.build.sq);
    const mp = zvP(my);
    zcCard("cheer", `${mp ? `${esc(zvSurname(mp))} уже в звене. ` : ""}Готово! Все ${n} ${plural(n, "наклейка", "наклейки", "наклеек")} уложились в 100 000 льдинок, осталось ${zvFmt(left)}.`,
      `<button type="button" class="btn" data-zc="tip1">Дальше</button><button type="button" class="coach-skip" data-zc="name">Пропустить подсказки</button>`);
    if (inTelegram && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
  }, calm() ? 0 : 15 * 80 + 520);
}
// Касса досчитывает до остатка, пока наклейки шлёпаются на лёд
function zvCountDown() {
  const el = $("#zv-left");
  if (!el || calm() || !ZV.build) return;
  const end = Z_BUDGET - zvSpent(ZV.build.sq);
  const t0 = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - t0) / 1300);
    if (!el.isConnected) return;
    el.innerHTML = zvIceN(Math.round((Z_BUDGET - (Z_BUDGET - end) * k) / 100) * 100);
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
function zvStartSelf() {
  ZV.build = { sq: zvSqEmpty(), manual: true, slap: false };
  zvPaint();
  setTimeout(() => {
    ZC.step = "self";
    const el = $("#zv-ice");
    if (!el) return;
    coachAt(el, { pose: guideOf(state.fav) ? "point" : "", text: "Начни с тех, кого знаешь. Нажми на пустое место — покажу, кто подходит и по карману. Остальных могу добрать.", buttons: '<button type="button" class="btn" data-zc="ok">Понятно</button>' });
    const b = $("#tour [data-zc]");
    if (b) b.focus({ preventScroll: true });
  }, calm() ? 0 : 300);
}
// Как назовём? Три пилюли из конструктора и «Ещё варианты»
function zvNameSheet(kind) {
  const opts = zvNameOptions(3);
  const title = kind === "team" ? "Как назовём команду?" : "Как назовём лигу?";
  const about = kind === "team" ? "Название видят соперники в таблицах. Имени из Telegram там нет." : "Лига для своих: семья, класс, трибуна. Позовёшь по ссылке.";
  showSheet(`<div class="grab"></div>
    <div class="sheet-head"><span class="when">${kind === "team" ? "Название команды" : "Своя лига"}</span><button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    ${guideOf(state.fav) ? `<div class="zv-guide" style="margin-bottom:12px">${guideFig(state.fav, "hello")}<p>${title}</p></div>` : `<h2 class="lead-title">${title}</h2>`}
    <p class="lead-about">${about}</p>
    <div class="zv-names" role="group" aria-label="Варианты">${opts.map((x) => `<button type="button" class="zv-pill" data-zv="${kind === "team" ? "name" : "lg-name"}" data-zv-arg="${esc(x.join("|"))}">${esc(x.join(" "))}</button>`).join("")}</div>
    <button type="button" class="zv-why" data-zv="names-more" data-zv-arg="${kind}">Ещё варианты</button>
    <p class="zv-err" id="zv-err" hidden></p>`);
}
function zvSheetErr(text) {
  const el = $("#zv-err");
  if (el) { el.textContent = text; el.hidden = false; }
  if (inTelegram && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("error");
}
async function zvCreate(name) {
  const b = ZV.build;
  if (!b || ZV.busy) return;
  const sq = b.sq;
  zvPickCaptain(sq, zvGames(zvTourNext()));
  ZV.busy = true;
  try {
    await zvApi("POST", "/team", { name, fav_club: state.fav, lineup: sq.lineup, bench: sq.bench, captain: sq.captain });
    ZV.build = null;
    ZV.me = undefined;
    ZV.meLoading = null;
    await zvLoadMe(true);
    closeMatch();
    ZV.seg = "ice";
    ZV.view = "";
    zvPaint();
    await zcLeague();
  } catch (e) {
    zvSheetErr(e.message);
  } finally {
    ZV.busy = false;
  }
}
// После названия: лига по ссылке или клубная, потом разрешение писать и финал
async function zcLeague() {
  let text = "";
  if (state.zvJoin) {
    try {
      await zvApi("POST", "/leagues/join", { code: state.zvJoin });
      try { localStorage.removeItem(Z_JOIN_KEY); } catch (e) { /* приватный режим */ }
      state.zvJoin = "";
      await zvLoadLeagues(true);
      const own = (ZV.leagues || []).find((l) => l.kind === "own");
      if (own) { state.zvJoinTitle = own.title; text = `Ты в лиге «${esc(own.title)}», ${own.place}-й из ${own.of}. Позови ещё своих — таблица станет живее.`; }
    } catch (e) {
      text = `${esc(e.message)} Лигу можно найти потом во вкладке «Лиги».`;
    }
  }
  if (!text) {
    await zvLoadLeagues(true);
    const club = (ZV.leagues || []).find((l) => l.kind === "club" || l.kind === "conf");
    text = club ? `Ты в лиге «${esc(club.title)}» — там ${club.of} ${plural(club.of, "человек", "человека", "человек")}. Позови своих — у вас будет своя таблица.`
      : "Ты в общем зачёте. Позови своих — у вас будет своя таблица.";
  }
  ZC.step = "league";
  zcCard("cheer", text, '<button type="button" class="btn" data-zc="invite">Позвать своих</button><button type="button" class="coach-skip" data-zc="later">Потом</button>');
}
function zcWrite() {
  const can = inTelegram && typeof tg.requestWriteAccess === "function" && tg.isVersionAtLeast && tg.isVersionAtLeast("6.9");
  if (!can) return zcFinal();
  ZC.step = "write";
  zcCard("point", "Написать тебе в воскресенье, если в звене будет что решить, и во вторник — итоги тура? Не чаще двух раз в неделю.",
    '<button type="button" class="btn" data-zc="write">Разрешить</button><button type="button" class="coach-skip" data-zc="nowrite">Не надо</button>');
}
function zcFinal() {
  ZC.step = "final";
  const next = zvTourNext();
  const team0 = ZV.team[next];
  const sq = team0 ? zvSqOf(team0) : null;
  const clubs = new Set(sq ? zvIds(sq).filter((id) => zvIsMain(zvKeyOf(sq, id))).map((id) => zvP(id) && zvP(id).club).filter(Boolean) : []);
  const tr = zvTour(next);
  const g = tr && state.data ? games().filter((x) => x.date >= tr.from && x.date <= tr.to && (clubs.has(x.home) || clubs.has(x.away))).sort((a, b) => (a.date + (a.time || "")).localeCompare(b.date + (b.time || "")))[0] : null;
  const first = g ? ` Первые очки — ${esc(fmtLong(g.date))}, после матча «${esc(team(g.home).name)}» — «${esc(team(g.away).name)}».` : "";
  zcCard("cheer", `Звено готово!${first} Болеем!`, '<button type="button" class="btn" data-zc="go">Поехали</button>');
}

// ---------- «Звено»: сегменты ----------

function zvTeamTitle() {
  const m = ZV.me && ZV.me.manager;
  return (m && zvName(m.name)) || "Моё звено";
}
function zvMain() {
  const name = zvTeamTitle();
  const segs = [["ice", "Звено"], ["market", "Обмен"], ["leagues", "Лиги"]];
  return `<section class="band peach zv-band"><h1 class="fit" style="--w:${longestChunk(name)}">${esc(name)}</h1>
    <div class="seg" role="group" aria-label="Раздел «Звена»" data-run="zv-seg">${RUN}${segs.map(([k, v]) => segBtn(ZV.seg === k, `data-zv="seg" data-zv-arg="${k}"`, `<span>${v}</span>`)).join("")}</div>
    ${zvTourLine()}${zvTourNow() ? zvTourBar(zvTourNow()) : zvDeadlineLine(zvTourNext())}</section>
    <div id="zv-body">${zvSegBody()}</div>`;
}
// «Тур 2 · 34 очка · 5-е в лиге …» — место в самом тёплом зачёте
function zvTourLine() {
  const now = zvTourNow();
  const t = now || zvTourNext();
  const team0 = ZV.team[t];
  const pts = team0 && team0.points ? team0.points.total || 0 : 0;
  const warm = (ZV.leagues || []).slice().sort((a, b) => Z_KIND_ORDER.indexOf(a.kind) - Z_KIND_ORDER.indexOf(b.kind))[0];
  const place = warm && warm.place ? `<span><b>${warm.place}-е</b> <small>из ${warm.of} · ${esc(warm.title)}</small>${Z_I.chev}</span>` : "";
  const head = now && ZV.team[now] ? `<span>Тур ${now} · <b>${pts}</b> ${plural(pts, "очко", "очка", "очков")}${place ? " ·" : Z_I.chev}</span>` : `<span>Тур ${zvTourNext()} · состав${place ? " ·" : Z_I.chev}</span>`;
  return `<button type="button" class="zv-tourline" id="zv-tourline" data-zv="to-leagues">${head}${place}</button>`;
}
function zvSegBody() {
  if (ZV.seg === "market") return zvMarket();
  if (ZV.seg === "leagues") return zvLeagues();
  return zvIceSeg();
}
// До дедлайна, если есть что решить, — «состав»; иначе идущий тур с очками
function zvDefaultView() {
  const now = zvTourNow();
  if (!now || !ZV.team[now]) return "squad";
  const t = ZV.team[zvTourNext()];
  return t && t.warnings && t.warnings.length ? "squad" : "points";
}
function zvIceSeg() {
  const now = zvTourNow();
  const next = zvTourNext();
  // пришёл после дедлайна — в идущем туре его звена нет: только «состав»
  const hasNow = !!now && now !== next && !!ZV.team[now];
  if (!ZV.view || (!hasNow && ZV.view === "points")) ZV.view = zvDefaultView();
  const pointsView = ZV.view === "points" && hasNow;
  const t = pointsView ? now : next;
  const team0 = ZV.team[t] || (pointsView ? null : ZV.team[next]);
  let html = hasNow ? `<div class="chips fill" role="group" aria-label="Тур" data-run="zv-view">${RUN}${segBtn(pointsView, 'data-zv="view" data-zv-arg="points"', `Тур ${now} · очки`)}${segBtn(!pointsView, 'data-zv="view" data-zv-arg="squad"', `Тур ${next} · состав`)}</div>` : "";
  if (!team0) return html + zvFailBlock("Не удалось загрузить состав.", "me");
  const sq = !pointsView && ZV.local ? ZV.local : zvSqOf(team0);
  if (!pointsView) html += zvWarnings(team0);
  // ушедшие после дедлайна остаются на льду идущего тура с меткой, новые стоят «с тура N+1»
  const marks = {};
  const nextTeam = ZV.team[next];
  if (pointsView && nextTeam) {
    const later = new Set(zvIds(zvSqOf(nextTeam)));
    zvIds(sq).forEach((id) => { if (!later.has(id)) marks[id] = "уйдёт после тура"; });
  } else if (now && ZV.team[now]) {
    const was = new Set(zvIds(zvSqOf(ZV.team[now])));
    zvIds(sq).forEach((id) => { if (!was.has(id)) marks[id] = `с тура ${next}`; });
  }
  html += zvIce(sq, { t, team: team0, view: pointsView ? "points" : "squad", sq, ctx: pointsView ? "ice-points" : "ice", marks });
  if (pointsView) {
    html += zvLegend();
    const total = team0.points ? team0.points.total || 0 : 0;
    const seen = (lsGet(Z_SEEN_KEY) || "").split(":");
    const was = seen[0] === String(t) ? +seen[1] || 0 : null;
    if (was != null && total > was) html += `<div class="zv-since">С прошлого захода <b class="num">+${total - was}</b></div>`;
    setTimeout(() => lsSet(Z_SEEN_KEY, `${t}:${total}`), 1500);
    // closed и postponed — из tours.json движка (контракт, раздел 7)
    const trc = zvTour(t);
    if (trc && trc.postponed) html += '<p class="note">Лига ещё выкладывает протоколы тура — итог чуть позже. Очки только прибавляются.</p>';
    else if (trc && trc.closed) html += '<p class="note">Тур закрыт: очки окончательные.</p>';
    else if (team0.points && team0.points.provisional) html += `<p class="note">Итог тура — ${esc(zvWhen(trc.close))}: лига ещё может поправить протоколы. Очки только прибавляются.</p>`;
  } else {
    const free = team0.free != null ? team0.free : 0;
    const freeText = zvFirstWindow() ? "До первого дедлайна обмены без ограничений" : zvBoostOn(team0) ? "Тур залит: обмены бесплатны" : `Бесплатных обменов: <b>${free}</b>`;
    html += `<div class="zv-bank"><span>${freeText}</span><span>В кассе <b>${zvIceN(team0.bank || 0)}</b></span></div>`;
    if (ZV.local) html += `<div class="zv-cta"><button type="button" class="btn" data-zv="save">Сохранить состав</button><button type="button" class="btn ghost" data-zv="undo">Вернуть как было</button></div><p class="zv-err" id="zv-save-err" hidden></p>`;
  }
  html += `<div class="label">«Звено»</div><div class="menu zv-set">
    <button type="button" class="menu-row" data-zv="journal">${Z_I.log}<span><b>Журнал</b><small>Обмены, автопилот и автозамены — видишь только ты</small></span>${Z_I.chev}</button>
    <button type="button" class="menu-row" data-zv="rules">${Z_I.book}<span><b>Правила</b><small>Очки, тур, обмены</small></span>${Z_I.chev}</button>
    <button type="button" class="menu-row" data-zv="settings">${Z_I.gear}<span><b>Настройки «Звена»</b><small>Автопилот, сообщения, удалить команду</small></span>${Z_I.chev}</button></div>`;
  return html;
}
// Предупреждения автопилота: «Мишка заменит Иванова в пн 09:00» и «Оставить»
function zvWarnings(team0) {
  const w = team0.warnings || [];
  if (!w.length) return "";
  return w.map((x) => `<div class="zv-note">${guideFig(state.fav, "point")}<p>${esc(x.text)}</p>${x.id ? `<button type="button" class="zv-pill btn-mini" data-zv="keep" data-zv-arg="${esc(x.id)}">Оставить</button>` : ""}</div>`).join("");
}

// ---------- карточка наклейки ----------

function zvCtxSq(ctx) {
  if (ctx === "draft" || ctx === "book") return ZV.draft;
  if (ctx === "build") return ZV.build && ZV.build.sq;
  if (ctx === "ice") return ZV.local || zvSqOf(ZV.team[zvTourNext()]);
  if (ctx === "market") return zvSqOf(ZV.team[zvTourNext()]);
  if (ctx === "ice-points") return zvSqOf(ZV.team[zvTourNow()]);
  return null;
}
function zvBars(p) {
  const upto = zvTourNow() || 0;
  const T = ZV.tours ? ZV.tours.tours.filter((x) => x.t <= upto) : [];
  if (!T.length) return "";
  const vals = T.map((x) => zvTourPts(p, x.t));
  const max = Math.max(4, ...vals);
  return `<div class="label">Очки по турам<span class="aside">два лучших матча, без «К»</span></div>
    <div class="zv-bars" role="img" aria-label="${esc(T.map((x, i) => `тур ${x.t}: ${vals[i]}`).join(", "))}">${vals.map((v) => `<span class="zv-bar"><b class="num">${v}</b><i class="${v ? "" : "z"}" style="height:${v ? Math.max(4, Math.round((v / max) * 72)) : 2}px"></i></span>`).join("")}</div>
    <div class="zv-bars-x" aria-hidden="true">${T.map((x) => `<span>${x.t}</span>`).join("")}</div>`;
}
function zvTourSum(p, t, sq) {
  const rec = p.tours && p.tours[String(t)];
  if (!rec || !rec.m || !rec.m.length) return "";
  const best = [...zvBest2(rec.m)].map((i) => rec.m[i]);
  const sum = best.reduce((a, b) => a + b, 0);
  const n = rec.m.length;
  const parts = best.length > 1 ? `${best.join(" + ")} = ${sum}` : `${sum}`;
  const of = n > 2 ? ` · два лучших из ${n}` : n === 2 ? " · оба матча" : " · один матч";
  const cap = sq && sq.captain === p.id ? ` · капитан ×2 = <b>${sum * 2}</b>` : "";
  return `<p class="zv-sum">Тур ${t}: <b>${parts}</b>${of}${cap}</p>`;
}
function zvCard(id, ctx, key) {
  const p = zvP(id);
  if (!p) return;
  const sq = zvCtxSq(ctx);
  const inKey = sq ? zvKeyOf(sq, id) : null;
  const club = team(p.club);
  const [last, first] = p.slot === "G" ? [p.name, ""] : splitName(p.name);
  const next = zvTourNext();
  const n = zvGames(next)[p.club] || 0;
  const now = zvTourNow();
  let html = `<div class="grab"></div>
    <div class="sheet-head"><span class="when">Наклейка${ZV.tours && zvOpen() ? ` · тур ${next} — ${n ? `${n} ${plural(n, "матч", "матча", "матчей")}` : "нет матчей"}` : ""}</span><button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    <div class="zv-cardhead"><span class="zs-fig">${zvFig(p)}</span><div>
      <h2>${esc(last)}${first ? `<small>${esc(first)}</small>` : ""}</h2>
      <div class="meta">${esc(club.name)} · ${Z_SLOT[p.slot]}${p.number != null ? ` · №${esc(p.number)}` : ""}${p.status === "rest" ? ' · <span class="tag soft">отдыхает</span>' : ""}${p.form ? " · <b>в форме</b>" : ""}</div>
      <button type="button" class="zv-my${zvIsMy(id) ? " on" : ""}" data-zv="my" data-zv-arg="${esc(id)}" aria-pressed="${zvIsMy(id)}">${Z_STAR.replace('class="zs-star"', "").replace(' role="img" aria-label="Мой игрок"', ' aria-hidden="true"')}Мой игрок</button>
    </div></div>`;
  if (p.slot === "G") html += `<p class="zv-sum">Очки приносит тот, кто стоит в воротах «${esc(club.name)}» в этот день, — хоть первый номер, хоть сменщик.</p>`;
  html += zvBars(p);
  if (now) html += zvTourSum(p, now, ctx === "ice-points" ? sq : null);
  // стоимость — только в «Обмене» и никогда у «Моего игрока» (ADR-014, раздел 5)
  if ((ctx === "market" || ctx === "book" || ctx === "draft") && !zvIsMy(id)) {
    const mon = p.price_monday != null ? p.price - p.price_monday : 0;
    html += `<p class="zv-sum">Стоимость <b>${zvIceN(p.price)}</b>${mon > 0 ? ` · за неделю <b>+${zvFmt(mon)}</b>` : mon < 0 ? ` · за неделю ${zvFmt(mon).replace("-", "−")}` : ""}</p>`;
  }
  html += `<div class="zv-sheet-btns">${zvCardButtons(p, ctx, sq, inKey)}</div><p class="zv-err" id="zv-err" hidden></p>`;
  showSheet(html);
}
function zvCardButtons(p, ctx, sq, inKey) {
  const id = esc(p.id);
  const c = `data-zv-ctx="${ctx}"`;
  if (ctx === "book" || ctx === "draft") {
    return inKey ? `<button type="button" class="btn ghost" data-zv="draft-del" data-zv-arg="${id}" ${c}>Убрать из черновика</button>`
      : `<button type="button" class="btn" data-zv="draft-add" data-zv-arg="${id}" ${c}>В черновик</button>`;
  }
  if (ctx === "build") {
    if (!inKey) return `<button type="button" class="btn" data-zv="build-add" data-zv-arg="${id}">Взять в звено</button>`;
    return `${zvIsMain(inKey) && p.slot !== "G" && sq.captain !== p.id ? `<button type="button" class="btn" data-zv="cap" data-zv-arg="${id}" ${c}>Сделать капитаном</button>` : ""}
      <button type="button" class="btn ghost" data-zv="build-del" data-zv-arg="${id}">Убрать</button>`;
  }
  if (ctx === "ice") {
    let b = "";
    if (inKey && zvIsMain(inKey) && p.slot !== "G" && sq.captain !== p.id) b += `<button type="button" class="btn" data-zv="cap" data-zv-arg="${id}" ${c}>Сделать капитаном</button>`;
    const benchKey = inKey && zvIsMain(inKey) ? Z_BENCH.find(([k, s]) => s === p.slot && zvGet(sq, k)) : null;
    if (benchKey) b += `<button type="button" class="btn ghost" data-zv="swap" data-zv-arg="${id}">Поменять с запасным</button>`;
    if (inKey && !zvIsMain(inKey)) b += `<button type="button" class="btn ghost" data-zv="swap" data-zv-arg="${id}">В основу</button>`;
    if (p.status === "rest") b += `<button type="button" class="btn" data-zv="replace" data-zv-arg="${id}">Отдать бесплатно и взять другого</button>`;
    else b += `<button type="button" class="btn ghost" data-zv="replace" data-zv-arg="${id}">Заменить</button>`;
    return b;
  }
  if (ctx === "market") {
    const out = ZV.market.out && zvP(ZV.market.out);
    const mine = zvSqOf(ZV.team[zvTourNext()]);
    if (zvClubCount(mine, p.club, out ? out.id : null) >= Z_CLUB_MAX) return `<p class="note">Из клуба «${esc(team(p.club).name)}» у тебя уже 3 наклейки. Отдай одну из них — и эту можно будет взять.</p>`;
    return `<button type="button" class="btn" data-zv="take" data-zv-arg="${id}">${out ? "Взять на его место" : "Взять"}</button>`;
  }
  return "";
}

// Черновик Пролога: кладём наклейку на первое свободное место её слота — основа, потом запас
function zvPlace(sq, p, key = null) {
  if (zvIds(sq).includes(p.id)) return "Эта наклейка уже в составе.";
  const k = key || (Z_MAIN.find(([kk, s]) => s === p.slot && !zvGet(sq, kk)) || Z_BENCH.find(([kk, s]) => s === p.slot && !zvGet(sq, kk)) || [])[0];
  if (!k) return `Все места ${Z_SLOT_GEN[p.slot]} заняты. Убери кого-нибудь — и положу.`;
  if (zvClubCount(sq, p.club) >= Z_CLUB_MAX) return `Из клуба «${team(p.club).name}» уже 3 наклейки — больше из одного клуба нельзя.`;
  if (zvSpent(sq) + p.price > Z_BUDGET) return `Не хватает льдинок: осталось ${zvFmt(Z_BUDGET - zvSpent(sq))}.`;
  zvSet(sq, k, p.id);
  return "";
}
function zvRemove(sq, id) {
  const k = zvKeyOf(sq, id);
  if (k) zvSet(sq, k, null);
  if (sq.captain === id) sq.captain = null;
  if (sq.assistant === id) sq.assistant = null;
}

// Выбор на пустое место: наклейки этого слота, по карману и без превышения лимита клуба
function zvPickSheet(key, ctx) {
  const sq = zvCtxSq(ctx);
  if (!sq) return;
  const sl = zvSlotOf(key);
  const left = Z_BUDGET - zvSpent(sq);
  const games0 = zvOpen() ? zvGames(zvTourNext()) : {};
  const list = ZV.pool.players.filter((p) => p.slot === sl && p.status !== "rest" && !zvIds(sq).includes(p.id))
    .sort((a, b) => (a.club === state.fav ? -1 : 0) - (b.club === state.fav ? -1 : 0) || zvValue(b, games0) - zvValue(a, games0));
  const rows = list.slice(0, 60).map((p) => {
    const full = zvClubCount(sq, p.club) >= Z_CLUB_MAX;
    const dear = p.price > left;
    const gray = full || dear;
    const why = full ? ` · уже 3 из «${esc(team(p.club).name)}»` : dear ? " · не хватает льдинок" : "";
    const n = games0[p.club];
    return `<div class="row zv-mrow${gray ? " gray" : ""}"${gray ? "" : ` role="button" tabindex="0" data-zv="pick" data-zv-arg="${esc(p.id)}" data-zv-key="${key}" data-zv-ctx="${ctx}"`}>
      <span class="ps">${figure({ kit: p.club, role: p.slot === "G" ? "G" : "F", number: p.slot === "G" ? null : p.number })}${emblem(p.club)}</span>
      <span class="who"><b>${esc(p.name)}</b><small>${esc(team(p.club).name)}${n != null && zvOpen() ? ` · ${zvGamesWord(n)}` : ""}${why}</small></span>
      <span class="val"><small>${zvIceN(p.price)}</small></span></div>`;
  }).join("");
  showSheet(`<div class="grab"></div>
    <div class="sheet-head"><span class="when">${Z_SLOT_SHORT[sl] === "Ворота" ? "Ворота клуба" : `${Z_SLOT[sl][0].toUpperCase()}${Z_SLOT[sl].slice(1)}`} · осталось ${zvIceN(left)}</span><button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    <div class="list">${rows || '<div class="empty" style="border:0">Подходящих наклеек пока нет</div>'}</div>`);
}

// ---------- обмен ----------

function zvSale(team0, id) {
  const s = team0 && team0.sale && team0.sale[id];
  return s != null ? s : zvP(id) ? zvP(id).price : 0;
}
function zvMarket() {
  const next = zvTourNext();
  const team0 = ZV.team[next];
  if (!team0) return zvFailBlock("Не удалось загрузить состав.", "me");
  const sq = zvSqOf(team0);
  const M = ZV.market;
  const games0 = zvGames(next);
  const now = zvTourNow();
  const out = zvP(M.out);
  let html = "";
  if (!out) html += zvMission(team0) + zvBoost(team0);
  const slotChips = [["all", "Все"], ["F", "Нап"], ["D", "Защ"], ["G", "Ворота"]];
  if (out) M.slot = out.slot;
  html += out ? `<div class="zv-note"><span class="ps" style="width:40px;height:40px">${figure({ kit: out.club, role: out.slot === "G" ? "G" : "F", number: out.slot === "G" ? null : out.number })}</span><p>Замена: <b>${esc(out.name)}</b>. Отдашь за <b>${zvIceN(zvSale(team0, out.id))}</b>, можно потратить <b>${zvIceN((team0.bank || 0) + zvSale(team0, out.id))}</b>.</p><button type="button" class="zv-pill btn-mini" data-zv="out-cancel">Отмена</button></div>`
    : `<div class="zv-filters"><div class="chips fill" role="group" aria-label="Слот" data-run="zv-mslot">${RUN}${slotChips.map(([k, v]) => segBtn(M.slot === k, `data-zv="mslot" data-zv-arg="${k}"`, v)).join("")}</div></div>`;
  const clubName = M.club ? team(M.club).name : "Клуб";
  html += `<div class="zv-opts">
    <button type="button" class="zv-pill${M.all ? "" : " on"}" data-zv="mall" aria-pressed="${!M.all}">По карману</button>
    <button type="button" class="zv-pill${M.club ? " on" : ""}" data-zv="mclub"><span>${esc(clubName)}</span>${Z_I.down}</button>
    <button type="button" class="zv-pill${M.big ? " on" : ""}" data-zv="mbig" aria-pressed="${M.big}">Большая неделя</button>
    <button type="button" class="zv-pill" data-zv="msort"><span>${Z_SORT[M.sort]}</span>${Z_I.down}</button></div>`;
  const bank = team0.bank || 0;
  const mine = new Set(zvIds(sq));
  const maxGames = Math.max(0, ...Object.values(games0));
  // «по карману»: хватит кассы и того, за сколько отдашь наклейку того же слота
  const budgetFor = (p) => {
    if (out) return bank + zvSale(team0, out.id);
    const same = zvIds(sq).filter((id) => zvP(id) && zvP(id).slot === p.slot);
    return bank + Math.max(0, ...same.map((id) => zvSale(team0, id)));
  };
  const clubFull = (p) => zvClubCount(sq, p.club, out ? out.id : null) >= Z_CLUB_MAX;
  let list = ZV.pool.players.filter((p) => !mine.has(p.id) && p.status !== "rest");
  if (M.slot !== "all") list = list.filter((p) => p.slot === M.slot);
  if (M.club) list = list.filter((p) => p.club === M.club);
  if (M.big) list = list.filter((p) => (games0[p.club] || 0) >= Math.max(3, maxGames));
  if (!M.all) list = list.filter((p) => p.price <= budgetFor(p));
  const metric = (p) => (M.sort === "season" ? zvSeasonPts(p) : zvTourPts(p, now || 0));
  const sorts = {
    tour: (a, b) => zvTourPts(b, now || 0) - zvTourPts(a, now || 0) || zvSeasonPts(b) - zvSeasonPts(a),
    season: (a, b) => zvSeasonPts(b) - zvSeasonPts(a),
    games: (a, b) => (games0[b.club] || 0) - (games0[a.club] || 0) || zvSeasonPts(b) - zvSeasonPts(a),
    cheap: (a, b) => a.price - b.price || zvSeasonPts(b) - zvSeasonPts(a),
  };
  list.sort(sorts[M.sort] || sorts.tour);
  list.sort((a, b) => clubFull(a) - clubFull(b));   // из «полных» клубов — в конце, серыми
  const what = M.sort === "season" ? "очки за сезон" : `очки за тур ${now || ""}`.trim();
  html += `<div class="zv-afford">Касса <b>${zvIceN(bank)}</b>. Справа — ${what}, под ними стоимость.</div>`;
  if (!list.length) {
    html += `<div class="empty">${M.all ? "Таких наклеек нет." : "По карману таких нет — нажми «По карману», чтобы увидеть все, или выбери другой слот."}</div>`;
    return html + (out ? "" : zvWeekUp(team0, sq));
  }
  html += `<div class="list">${list.slice(0, M.limit).map((p) => {
    const full = clubFull(p);
    const n = games0[p.club] || 0;
    return `<div class="row zv-mrow${full ? " gray" : ""}" role="button" tabindex="0" data-zv="mcard" data-zv-arg="${esc(p.id)}">
      <span class="ps">${figure({ kit: p.club, role: p.slot === "G" ? "G" : "F", number: p.slot === "G" ? null : p.number })}${emblem(p.club)}</span>
      <span class="who"><b>${esc(p.name)}${p.form ? '<span class="tag soft">в форме</span>' : ""}</b><small>${esc(team(p.club).name)} · ${full ? `уже 3 из «${esc(team(p.club).name)}»` : zvGamesWord(n)}</small></span>
      <span class="val"><b class="num">${metric(p)}</b><small>${zvIceN(p.price)}</small></span></div>`;
  }).join("")}${list.length > M.limit ? `<button type="button" class="zv-more-row" data-zv="mmore">Показать ещё ${Math.min(40, list.length - M.limit)}</button>` : ""}</div>`;
  if (!out) html += zvWeekUp(team0, sq);
  return html;
}
// Задание недели: новый клуб в альбоме → +1 обмен. Видно только в «Обмене» (ADR-014, раздел 14)
function zvMission(team0) {
  const m = team0.mission;
  if (!m) return "";
  if (m.done) return `<div class="zv-note">${guideFig(state.fav, "cheer")}<p><b>Задание недели выполнено</b> — у тебя +1 обмен.</p></div>`;
  const left = (m.clubs_left || []).filter((c) => state.teams[c]);
  return `<div class="zv-note">${guideFig(state.fav, "point")}<div><p><b>Задание недели:</b> поставь в основу клуб, которого не было в твоём альбоме, — +1 обмен. Засчитаю в дедлайн.</p>${left.length ? `<div class="ems">${left.slice(0, 8).map((c) => `<span title="${esc(team(c).name)}">${emblem(c)}</span>`).join("")}${left.length > 8 ? `<small>и ещё ${left.length - 8}</small>` : ""}</div>` : ""}</div></div>`;
}
// Буст «Заливка»: все обмены тура бесплатны. Один буст за тур; слова «сгорят» нет (ADR-014, раздел 8)
// До первого дедлайна обмены без ограничений (ADR-014, раздел 6)
const zvFirstWindow = () => !!ZV.tours && !zvTourNow() && !!ZV.tours.first_tour && zvTourNext() === ZV.tours.first_tour;
const zvBoostOn = (team0) => !!team0 && (team0.boost === "zalivka" || (team0.boosts && team0.boosts.active === "zalivka"));
function zvBoost(team0) {
  const t = zvTourNext();
  if (zvFirstWindow()) return "";
  if (zvBoostOn(team0)) return `<div class="zv-note">${guideFig(state.fav, "cheer")}<p><b>Тур ${t} залит:</b> все обмены в нём бесплатны.</p></div>`;
  const n = team0.boosts && team0.boosts.zalivka;
  if (!n) return "";
  return `<div class="zv-note zv-boost"><p><b>Заливка</b> — все обмены тура бесплатны. У тебя ${n} ${plural(n, "заливка", "заливки", "заливок")}, одна на тур.</p><button type="button" class="zv-pill btn-mini" data-zv="boost-ask">Залить тур ${t}</button></div>`;
}
function zvBoostSheet() {
  const t = zvTourNext();
  showSheet(`<div class="grab"></div>
    <div class="sheet-head"><span class="when">Буст «Заливка»</span><button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    ${zvGuideCard("point", `Залить тур ${t}? Все обмены в нём станут бесплатными — меняй сколько нужно до дедлайна. Отменить заливку нельзя.`)}
    <p class="zv-err" id="zv-err" hidden></p>
    <div class="zv-sheet-btns"><button type="button" class="btn" data-zv="boost">Залить тур ${t}</button><button type="button" class="btn ghost" data-close>Не сейчас</button></div>`);
}
async function zvDoBoost() {
  if (ZV.busy) return;
  ZV.busy = true;
  try {
    ZV.team[zvTourNext()] = await zvApi("POST", "/team/boost", { boost: "zalivka" });
    closeMatch();
    if (inTelegram && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
    zvPaintBody();
  } catch (e) {
    zvSheetErr(e.message);
  } finally {
    ZV.busy = false;
  }
}

// «Итог недели»: только рост своих наклеек
function zvWeekUp(team0, sq) {
  const up = zvIds(sq).map((id) => zvP(id)).filter((p) => p && p.price_monday != null && p.price > p.price_monday && !zvIsMy(p.id));
  if (!up.length) return "";
  up.sort((a, b) => (b.price - b.price_monday) - (a.price - a.price_monday));
  return `<div class="label">Итог недели<span class="aside">подорожали с понедельника</span></div><div class="list zv-up">${up.slice(0, 3).map((p) => `<div class="row"><span class="ps">${figure({ kit: p.club, role: p.slot === "G" ? "G" : "F", number: p.slot === "G" ? null : p.number })}</span><span class="who" style="min-width:0"><b style="font-weight:700">${esc(p.name)}</b></span><span class="up">+${zvFmt(p.price - p.price_monday)}${Z_ICE}</span></div>`).join("")}</div>`;
}
// Кого отдаёшь? — свои наклейки того же слота с «Отдашь за»
function zvGiveSheet(inId) {
  const p = zvP(inId);
  const team0 = ZV.team[zvTourNext()];
  const sq = zvSqOf(team0);
  const own = zvIds(sq).map(zvP).filter((x) => x && x.slot === p.slot);
  showSheet(`<div class="grab"></div>
    <div class="sheet-head"><span class="when">Берёшь ${esc(p.name)}</span><button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    <h2 class="lead-title">Кого отдаёшь?</h2>
    <div class="list">${own.map((x) => `<div class="row zv-mrow" role="button" tabindex="0" data-zv="give" data-zv-arg="${esc(x.id)}" data-zv-in="${esc(inId)}">
      <span class="ps">${figure({ kit: x.club, role: x.slot === "G" ? "G" : "F", number: x.slot === "G" ? null : x.number })}${emblem(x.club)}</span>
      <span class="who"><b>${esc(x.name)}${zvIsMy(x.id) ? " " + Z_STAR : ""}</b><small>${esc(team(x.club).name)}${x.status === "rest" ? " · отдыхает" : ""}</small></span>
      <span class="val"><small>отдашь за</small><b style="font:700 15px/1 var(--ui)">${zvIceN(zvSale(team0, x.id))}</b></span></div>`).join("")}</div>`);
}
// Лист обмена: «Отдашь за / Берёшь / В кассе останется». Платный — «8 очков или 600 ❄», без минусов
function zvDealSheet(outId, inId) {
  const team0 = ZV.team[zvTourNext()];
  const out = zvP(outId);
  const inn = zvP(inId);
  if (!team0 || !out || !inn) return;
  const sale = zvSale(team0, outId);
  const free = (team0.free || 0) > 0 || out.status === "rest" || zvBoostOn(team0) || zvFirstWindow();
  const opts = (team0.fee_options || ["points", "ice"]).filter((x) => x === "points" || x === "ice");
  if (!ZV.deal || ZV.deal.out !== outId || ZV.deal.in !== inId) ZV.deal = { out: outId, in: inId, pay: free ? "free" : opts[0] || "points", why: false };
  const D = ZV.deal;
  const iceFee = D.pay === "ice" ? Z_FEE_ICE : 0;
  const left = (team0.bank || 0) + sale - inn.price - iceFee;
  const bought = team0.bought && team0.bought[outId];
  const stick = (p) => `<span class="ps">${figure({ kit: p.club, role: p.slot === "G" ? "G" : "F", number: p.slot === "G" ? null : p.number })}${emblem(p.club)}</span>`;
  let fee;
  if (free) {
    fee = out.status === "rest" ? "Наклейка «отдыхает» — этот обмен бесплатный."
      : zvBoostOn(team0) ? "Тур залит — этот обмен бесплатный."
      : zvFirstWindow() ? "До первого дедлайна обмены без ограничений и бесплатно."
      : `Обмен бесплатный, их у тебя ${team0.free}.`;
  } else if (!opts.length) {
    fee = "Платных обменов в этом туре больше нет.";
  } else {
    const names = { points: `${Z_FEE_POINTS} очков`, ice: `${Z_FEE_ICE}${Z_ICE}` };
    fee = `Бесплатные обмены кончились. Этот обмен стоит ${D.pay === "ice" ? `${Z_FEE_ICE} льдинок` : `${Z_FEE_POINTS} очков в туре ${zvTourNext()}`}.${opts.length > 1 ? `<div class="seg" role="group" aria-label="Чем платим" data-run="zv-pay">${RUN}${opts.map((k) => segBtn(D.pay === k, `data-zv="pay" data-zv-arg="${k}"`, `<span>${names[k]}</span>`)).join("")}</div>` : ""}`;
  }
  const why = bought != null && sale !== out.price ? `<button type="button" class="zv-why" data-zv="why">Почему ${zvFmt(sale)}?</button>${D.why ? `<div class="zv-scale"><span>взял за<b>${zvFmt(bought)}</b></span>${Z_I.arrow}<span>сейчас<b>${zvFmt(out.price)}</b></span>${Z_I.arrow}<span>тебе<b>${zvFmt(sale)}</b></span></div><p class="note">${out.price > bought ? "Подорожала — отдаёшь за цену, по которой взял, и половину роста, но не больше +500." : out.status === "rest" ? "«Отдыхает» — отдаёшь по большей из двух цен." : "Отдаёшь по текущей стоимости."}</p>` : ""}` : "";
  const bad = left < 0 ? `Не хватает ${zvFmt(-left)} льдинок.` : zvClubCount(zvSqOf(team0), inn.club, outId) >= Z_CLUB_MAX ? `Из клуба «${esc(team(inn.club).name)}» у тебя уже 3 наклейки — больше нельзя.` : !free && !opts.length ? "Платных обменов в этом туре больше нет." : "";
  const when = team0.locked || (zvTourNow() && zvTourNow() !== zvTourNext()) ? `<p class="note">Обмен проходит сразу, на лёд новая наклейка встанет с тура ${zvTourNext()}.</p>` : "";
  showSheet(`<div class="grab"></div>
    <div class="sheet-head"><span class="when">Обмен</span><button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    <div class="zv-deal">
      <div class="zv-deal-row">${stick(out)}<span>Отдаёшь<small>${esc(out.name)}</small></span><b>${zvIceN(sale)}</b></div>
      <div class="zv-deal-row">${stick(inn)}<span>Берёшь<small>${esc(inn.name)}</small></span><b>${zvIceN(inn.price)}</b></div>
      <div class="zv-deal-total"><span>В кассе останется</span><b class="num">${zvFmt(Math.max(0, left))}${Z_ICE}</b></div>
    </div>
    <div class="zv-fee">${fee}</div>${why}${when}
    ${bad ? `<p class="zv-err">${bad}</p>` : ""}<p class="zv-err" id="zv-err" hidden></p>
    <div class="zv-sheet-btns"><button type="button" class="btn" data-zv="deal"${bad ? " disabled" : ""}>Поменяться</button></div>`, 1);
}
async function zvDoDeal() {
  const D = ZV.deal;
  if (!D || ZV.busy) return;
  ZV.busy = true;
  const btn = $('#sheet [data-zv="deal"]');
  if (btn) btn.disabled = true;
  try {
    const t = await zvApi("POST", "/team/transfer", { out: D.out, in: D.in, pay: D.pay });
    ZV.team[zvTourNext()] = t;
    ZV.deal = null;
    ZV.market.out = "";
    ZV.local = null;
    closeMatch();
    if (inTelegram && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
    ZV.seg = "ice";
    ZV.view = "squad";
    zvPaint();
    window.scrollTo(0, 0);
  } catch (e) {
    if (btn) btn.disabled = false;
    zvSheetErr(e.message);
  } finally {
    ZV.busy = false;
  }
}
function zvClubSheet() {
  showSheet(`<div class="grab"></div>
    <div class="sheet-head"><span class="when">Клуб</span><button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    <button type="button" class="btn ghost" data-zv-club="" style="margin-bottom:8px">Все клубы</button>
    ${teamGrid(ZV.market.club, "data-zv-club")}`);
}
function zvSortSheet() {
  showSheet(`<div class="grab"></div>
    <div class="sheet-head"><span class="when">Сортировка</span><button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    <div class="menu">${Object.entries(Z_SORT).map(([k, v]) => `<button type="button" class="menu-row" data-zv-sort="${k}" style="grid-template-columns:1fr 16px"><span><b>${v}</b></span>${k === ZV.market.sort ? CHECK : "<i></i>"}</button>`).join("")}</div>`);
}

// ---------- лиги ----------

function zvLeagues() {
  if (!ZV.leagues) {
    if (ZV.leaguesFail) return zvFailBlock("Не удалось загрузить лиги.", "leagues");
    zvLoadLeagues();
    return '<div class="sk" style="height:76px"></div><div class="sk" style="height:76px"></div><div class="sk" style="height:76px"></div>';
  }
  const list = ZV.leagues.slice().sort((a, b) => Z_KIND_ORDER.indexOf(a.kind) - Z_KIND_ORDER.indexOf(b.kind));
  let html = "";
  if (state.zvJoin) html += `<div class="zv-note">${guideFig(state.fav, "point")}<p>Тебя позвали в лигу по ссылке.</p><button type="button" class="zv-pill btn-mini on" data-zv="lg-join-link">Вступить</button></div>`;
  html += list.map((l) => {
    const step = l.kind === "step" ? " · четверо лучших поднимутся" : "";
    return `<button type="button" class="zv-lg" data-zv="lg-open" data-zv-arg="${esc(l.id)}">
      <span><span class="tag soft kind">${Z_KIND[l.kind] || "Лига"}</span><b>${esc(l.title)}</b><small>${l.of} ${plural(l.of, "команда", "команды", "команд")}${step}${l.points != null ? ` · ${l.points} ${plural(l.points, "очко", "очка", "очков")}` : ""}</small></span>
      <span class="pl">${l.place ? `${l.place}-е` : "—"}<small>из ${l.of}</small></span></button>`;
  }).join("");
  html += `<div class="label">Своя лига<span class="aside">семья, класс, трибуна</span></div>
    <div class="zv-cta"><button type="button" class="btn ghost" data-zv="lg-new">${Z_I.plus}Создать лигу</button></div>
    <div class="zv-code"><input id="zv-code" inputmode="latin" autocomplete="off" autocapitalize="characters" maxlength="12" placeholder="Код лиги" aria-label="Код лиги"><button type="button" class="zv-pill" data-zv="lg-join">Вступить</button></div>
    <p class="zv-err" id="zv-lg-err" hidden></p>
    <div class="foot">Названия команд — из конструктора. Имя из Telegram видно только в своих лигах и только если ты разрешил в настройках.</div>`;
  return html;
}
async function zvLeagueSheet(id) {
  const l = (ZV.leagues || []).find((x) => String(x.id) === String(id));
  if (!l) return;
  const head = `<div class="grab"></div>
    <div class="sheet-head"><span class="when">${Z_KIND[l.kind] || "Лига"}</span><button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    <h2 class="lead-title">${esc(l.title)}</h2>`;
  showSheet(head + '<div class="sk" style="height:320px"></div>');
  let rows;
  try {
    rows = await zvApi("GET", `/leagues/${encodeURIComponent(l.id)}`);
  } catch (e) {
    if (!sheetOpen()) return;
    return showSheet(head + `<div class="empty">${esc(e.message)}</div>`);
  }
  if (!sheetOpen()) return;
  const step = l.kind === "step";
  let body = step ? `<p class="lead-about">20 соперников твоего уровня. В начале месяца четверо лучших поднимутся минимум на ступень.</p>` : "";
  body += `<div class="list zv-lg-rows">${(rows || []).map((r, i) => {
    const up = step && r.place <= 4 ? '<span class="tag up">вверх</span>' : "";
    const cut = step && i > 0 && rows[i - 1].place <= 4 && r.place > 4 ? '<div class="cut"><span>поднимутся ↑</span></div>' : "";
    return `${cut}<div class="row${r.me ? " me" : ""}"><span class="pos">${r.place}</span><span class="nm">${esc(r.team_name)}${up}</span><span class="pts num">${r.points}</span></div>`;
  }).join("")}</div>`;
  if (l.kind === "own") body += `<div class="zv-sheet-btns"><button type="button" class="btn" data-zv="lg-share" data-zv-arg="${esc(l.code || "")}" data-zv-title="${esc(l.title)}">${Z_I.share} Позвать в лигу</button></div>`;
  showSheet(head + body);
}
async function zvLeagueCreate(name) {
  if (ZV.busy) return;
  ZV.busy = true;
  try {
    const r = await zvApi("POST", "/leagues", { name });
    await zvLoadLeagues(true);
    showSheet(`<div class="grab"></div>
      <div class="sheet-head"><span class="when">Своя лига</span><button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
      ${zvGuideCard("cheer", `Лига «${esc(zvName(name))}» готова. Код — <b>${esc(r.code)}</b>. Позови своих: по ссылке они сразу попадут в таблицу.`)}
      <div class="zv-sheet-btns"><button type="button" class="btn" data-zv="lg-share" data-zv-arg="${esc(r.code)}" data-zv-title="${esc(zvName(name))}">${Z_I.share} Позвать в лигу</button></div>`);
    zvPaintBody();
  } catch (e) {
    zvSheetErr(e.message);
  } finally {
    ZV.busy = false;
  }
}
async function zvLeagueJoin(code) {
  const err = $("#zv-lg-err");
  const c = String(code || "").trim().toUpperCase().replace(/[^A-Z0-9-]/g, "");
  if (!c) { if (err) { err.textContent = "Введи код лиги — его даёт тот, кто её создал."; err.hidden = false; } return; }
  try {
    await zvApi("POST", "/leagues/join", { code: c });
    if (state.zvJoin === c) state.zvJoin = "";
    try { localStorage.removeItem(Z_JOIN_KEY); } catch (e) { /* приватный режим */ }
    ZV.leagues = null;
    ZV.leaguesLoading = null;
    await zvLoadLeagues(true);
    zvPaintBody();
    if (inTelegram && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
  } catch (e) {
    if (err) { err.textContent = e.message; err.hidden = false; }
    if (inTelegram && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("error");
  }
}
function zvAppLink(param) {
  const app = state.data && state.data.links && state.data.links.app;
  if (app) return `${app}?startapp=${encodeURIComponent(param)}`;
  return `${location.origin}${location.pathname}?startapp=${encodeURIComponent(param)}`;
}
function zvShareApp(kind, code = "", title = "") {
  if (kind === "lg" && code) return shareLink(zvAppLink(`lg-${code}`), `Вступай в мою лигу «${title}» в «Звене» — игре на наклейках игроков РХЛ`);
  return shareLink(zvAppLink("zveno"), `Собери своё звено из игроков РХЛ и болей за «${team(state.fav).name}» вместе со мной`);
}

// ---------- журнал и настройки ----------

async function zvJournalSheet() {
  const head = `<div class="grab"></div><div class="sheet-head"><span class="when">Журнал · видишь только ты</span><button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div><h2 class="lead-title">Журнал</h2>`;
  showSheet(head + '<div class="sk" style="height:200px"></div>');
  let rows;
  try { rows = await zvApi("GET", "/journal"); } catch (e) { if (sheetOpen()) showSheet(head + `<div class="empty">${esc(e.message)}</div>`); return; }
  if (!sheetOpen()) return;
  const KIND = { deal: "Обмен", autopilot: "Автопилот", autosub: "Автозамена", mission: "Задание" };
  showSheet(head + (rows && rows.length ? `<div class="list">${rows.map((r) => `<div class="row static" style="grid-template-columns:1fr"><span class="lr-who"><b style="white-space:normal">${esc(r.text)}</b><small>${esc(KIND[r.kind] || "")} · ${esc(zvWhen(r.at))}</small></span></div>`).join("")}</div>` : '<div class="empty">Пока пусто: здесь будут обмены и всё, что сделал автопилот.</div>'));
}
function zvSettings() {
  const m = (ZV.me && ZV.me.manager) || {};
  const s = Object.assign({ autopilot: true, messages: true, show_tg_name: false }, m.settings || {});
  const g = guideOf(state.fav);
  const row = (k, title, sub) => `<button type="button" class="menu-row" data-zv="set" data-zv-arg="${k}" aria-pressed="${!!s[k]}"><span><b>${title}</b><small>${sub}</small></span><span class="zv-sw" aria-hidden="true"></span></button>`;
  const ask = ZV.delAsk ? `<div class="zv-confirm"><p>Удалить команду, все обмены, журнал и места в лигах? Сообщения «Звена» тоже прекратятся. Вернуть будет нельзя.</p>
    <div class="zv-sheet-btns"><button type="button" class="btn" data-zv="del-yes">Удалить</button><button type="button" class="btn ghost" data-zv="del-no">Не удалять</button></div><p class="zv-err" id="zv-err" hidden></p></div>`
    : `<button type="button" class="btn ghost zv-del" data-zv="del">Удалить моё «Звено»</button>`;
  showSheet(`<div class="grab"></div>
    <div class="sheet-head"><span class="when">Настройки «Звена»</span><button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    <div class="menu zv-set">
      ${row("autopilot", "Автопилот", `${g ? esc(g.name) : "Талисман"} заменит того, кто «отдыхает», бесплатно. «Моего игрока» не трогает`)}
      ${row("messages", "Сообщения в боте", "Не больше двух в неделю: итоги тура и «есть что решить»")}
      ${row("show_tg_name", "Имя из Telegram в своих лигах", "В остальных таблицах — только название команды")}
    </div>${ask}`);
}
async function zvSetToggle(k) {
  const m = ZV.me && ZV.me.manager;
  if (!m) return;
  const s = Object.assign({ autopilot: true, messages: true, show_tg_name: false }, m.settings || {});
  s[k] = !s[k];
  m.settings = s;
  const btn = $(`#sheet [data-zv="set"][data-zv-arg="${k}"]`);
  if (btn) btn.setAttribute("aria-pressed", String(s[k]));
  try { await zvApi("PUT", "/settings", s); } catch (e) {
    s[k] = !s[k];
    if (btn) btn.setAttribute("aria-pressed", String(s[k]));
  }
}
async function zvDelete() {
  if (ZV.busy) return;
  ZV.busy = true;
  try {
    await zvApi("DELETE", "/me");
    ZV.me = { manager: null, season: ZV.me && ZV.me.season };
    ZV.team = {};
    ZV.leagues = null;
    ZV.leaguesLoading = null;
    ZV.local = null;
    ZV.build = null;
    ZV.delAsk = false;
    ZV.onbShown = true;   // проводник не выскакивает сразу после удаления
    try { localStorage.removeItem(Z_SEEN_KEY); } catch (e) { /* приватный режим */ }
    closeMatch();
    zvPaint();
    zvDue();
  } catch (e) {
    zvSheetErr(e.message);
  } finally {
    ZV.busy = false;
  }
}

// ---------- правка состава ----------

function zvWork() {
  if (!ZV.local) ZV.local = zvSqOf(ZV.team[zvTourNext()]);
  return ZV.local;
}
function zvSwap(id) {
  const sq = zvWork();
  const k = zvKeyOf(sq, id);
  const p = zvP(id);
  if (!k || !p) return;
  const other = zvIsMain(k) ? Z_BENCH.find(([kk, s]) => s === p.slot && zvGet(sq, kk)) : Z_MAIN.find(([kk, s]) => s === p.slot);
  if (!other) return;
  const o = zvGet(sq, other[0]);
  zvSet(sq, other[0], id);
  zvSet(sq, k, o);
  if (sq.captain === id && !zvIsMain(other[0])) sq.captain = o;
  zvPickCaptain(sq, zvGames(zvTourNext()));
}
async function zvSave() {
  if (!ZV.local || ZV.busy) return;
  ZV.busy = true;
  try {
    const t = await zvApi("PUT", "/team/lineup", { lineup: ZV.local.lineup, bench: ZV.local.bench, captain: ZV.local.captain });
    ZV.team[zvTourNext()] = t;
    ZV.local = null;
    if (inTelegram && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
    zvPaintBody();
  } catch (e) {
    const el = $("#zv-save-err");
    if (el) { el.textContent = e.message; el.hidden = false; }
  } finally {
    ZV.busy = false;
  }
}

// ---------- нажатия ----------

// Сегменты и чипы: бегунок трогается сразу, содержимое — в следующем кадре (DESIGN.md → «Движение»)
function zvSwitch(el, apply) {
  haptic();
  const group = el.parentNode;
  if (group && group.dataset.run) {
    const prev = runnerState(group.parentNode)[group.dataset.run];
    group.querySelectorAll(":scope > button").forEach((b) => { b.classList.toggle("on", b === el); b.setAttribute("aria-pressed", b === el); });
    placeRunner(group, prev);
  }
  apply();
  nextFrame(() => { zvPaintBody(); fadeIn($("#zv-body")); });
}

function zvAct(act, arg, el) {
  const ctx = el.dataset.zvCtx || "";
  switch (act) {
    case "retry":
      haptic();
      if (arg === "load") { ZV.status = ""; ZV.loading = null; }
      if (arg === "me") { ZV.me = undefined; ZV.meLoading = null; ZV.meFail = ""; }
      if (arg === "leagues") { ZV.leagues = null; ZV.leaguesLoading = null; ZV.leaguesFail = false; }
      return zvPaint();
    case "waitlist": {
      const url = `${state.data.links.bot}?start=zveno`;
      haptic();
      return inTelegram ? tg.openTelegramLink(url) : window.open(url, "_blank", "noopener");
    }
    case "rules": return zvRulesSheet();
    case "book-open": ZV.bookOpen.add(arg); haptic(); return zvPaint();
    case "card": return zvCard(arg, ctx, el.dataset.zvKey);
    case "mcard": return zvCard(arg, "market");
    case "slot":
      // пустое место в онбординге — значит, собирает сам; в звене на сервере — только через обмен
      if (ctx === "build" && !ZV.build) ZV.build = { sq: zvSqEmpty(), manual: true, slap: false };
      if (ctx === "ice") {
        ZV.seg = "market";
        ZV.market.slot = zvSlotOf(arg) || "all";
        zvPaint();
        return window.scrollTo(0, 0);
      }
      return zvPickSheet(arg, ctx);
    case "pick": {
      const sq = zvCtxSq(ctx);
      const p = zvP(arg);
      if (!sq || !p) return;
      const err = zvPlace(sq, p, el.dataset.zvKey);
      if (err) return zvSheetErr(err);
      haptic();
      if (ctx === "draft") zvDraftSave();
      closeMatch();
      return zvPaint();
    }
    case "my": {
      const on = !zvIsMy(arg);
      zvSetMy(on ? arg : "");
      haptic();
      el.classList.toggle("on", on);
      el.setAttribute("aria-pressed", String(on));
      return zvPaintSoon();
    }
    case "draft-add": {
      const p = zvP(arg);
      const err = zvPlace(ZV.draft, p);
      if (err) return zvSheetErr(err);
      haptic();
      zvDraftSave();
      closeMatch();
      return zvPaint();
    }
    case "draft-del":
      zvRemove(ZV.draft, arg);
      zvDraftSave();
      closeMatch();
      return zvPaint();
    case "draft-fill":
      haptic();
      ZV.draft = zvAuto({ sq: ZV.draft, my: zvMy(), keepLines: true });
      zvDraftSave();
      return zvPaint();
    case "draft-clear":
      ZV.draft = zvSqEmpty();
      zvDraftSave();
      return zvPaint();
    case "auto": return zvStartAuto();
    case "self": return zvStartSelf();
    case "first":
      if (arg) zvSetMy(arg);
      return zvDoBuild(arg || null);
    case "fill":
      haptic();
      ZV.build.sq = zvAuto({ sq: ZV.build.sq, games: zvGames(zvTourNext()), my: zvMy(), keepLines: true });
      return zvPaint();
    case "done":
      zvPickCaptain(ZV.build.sq, zvGames(zvTourNext()));
      zvPaint();
      return zcAct("tip1");
    case "build-add": {
      const err = zvPlace(ZV.build.sq, zvP(arg));
      if (err) return zvSheetErr(err);
      closeMatch();
      return zvPaint();
    }
    case "build-del":
      zvRemove(ZV.build.sq, arg);
      closeMatch();
      return zvPaint();
    case "cap": {
      const sq = ctx === "build" ? ZV.build.sq : zvWork();
      if (sq.assistant === arg) sq.assistant = sq.captain;
      sq.captain = arg;
      haptic();
      closeMatch();
      return ctx === "build" ? zvPaint() : zvPaintBody();
    }
    case "swap":
      zvSwap(arg);
      haptic();
      closeMatch();
      return zvPaintBody();
    case "undo": ZV.local = null; return zvPaintBody();
    case "save": return zvSave();
    case "replace":
      closeMatch();
      ZV.market.out = arg;
      ZV.seg = "market";
      ZV.market.all = false;
      haptic();
      zvPaint();
      return window.scrollTo(0, 0);
    case "out-cancel": ZV.market.out = ""; ZV.market.slot = "all"; return zvPaintBody();
    case "take": {
      const out = ZV.market.out;
      if (out) return zvDealSheet(out, arg);
      return zvGiveSheet(arg);
    }
    case "give": return zvDealSheet(arg, el.dataset.zvIn);
    case "pay": ZV.deal.pay = arg; haptic(); return zvDealSheet(ZV.deal.out, ZV.deal.in);
    case "why": ZV.deal.why = !ZV.deal.why; return zvDealSheet(ZV.deal.out, ZV.deal.in);
    case "deal": return zvDoDeal();
    case "boost-ask": return zvBoostSheet();
    case "boost": return zvDoBoost();
    case "keep":
      el.disabled = true;
      return zvApi("POST", "/team/keep", { id: arg }).then((t) => { ZV.team[zvTourNext()] = t; zvPaintBody(); zvDue(); })
        .catch(() => { el.disabled = false; });
    case "seg":
      if (ZV.seg === arg) return;
      return zvSwitch(el, () => { ZV.seg = arg; });
    case "to-leagues": {
      if (ZV.seg === "leagues") return;
      const b = document.querySelector('[data-run="zv-seg"] [data-zv-arg="leagues"]');
      if (b) return zvSwitch(b, () => { ZV.seg = "leagues"; });
      ZV.seg = "leagues";
      return zvPaint();
    }
    case "view": if (ZV.view === arg) return; return zvSwitch(el, () => { ZV.view = arg; });
    case "mslot": if (ZV.market.slot === arg) return; return zvSwitch(el, () => { ZV.market.slot = arg; ZV.market.limit = 40; });
    case "mall": ZV.market.all = !ZV.market.all; haptic(); return zvPaintBody();
    case "mbig": ZV.market.big = !ZV.market.big; haptic(); return zvPaintBody();
    case "mclub": return zvClubSheet();
    case "msort": return zvSortSheet();
    case "mmore": ZV.market.limit += 40; return zvPaintBody();
    case "lg-open": return zvLeagueSheet(arg);
    case "lg-new": return zvNameSheet("league");
    case "lg-name": return zvLeagueCreate(arg.split("|"));
    case "lg-join": return zvLeagueJoin(($("#zv-code") || {}).value);
    case "lg-join-link": return zvLeagueJoin(state.zvJoin);
    case "lg-share": return zvShareApp("lg", arg, el.dataset.zvTitle || "");
    case "name": return zvCreate(arg.split("|"));
    case "names-more": return zvNameSheet(arg);
    case "journal": return zvJournalSheet();
    case "settings": ZV.delAsk = false; return zvSettings();
    case "set": return zvSetToggle(arg);
    case "del": ZV.delAsk = true; return zvSettings();
    case "del-no": ZV.delAsk = false; return zvSettings();
    case "del-yes": return zvDelete();
  }
}
// Перерисовать под листом, когда он уедет: иначе лист дёргается
function zvPaintSoon() {
  if (state.tab === "zveno") setTimeout(() => { if ($("#sheet").hidden) zvPaint(); else zvPaintUnder(); }, 0);
}
function zvPaintUnder() {
  const screen = $("#screen");
  if (state.tab !== "zveno" || !screen) return;
  const y = window.scrollY;
  const prev = runnerState(screen);
  screen.innerHTML = renderZveno();
  addThemeToggle();
  placeRunners(screen, prev);
  window.scrollTo(0, y);
}

// Нажатия «Звена» ловим раньше app.js (фаза захвата) и дальше не пускаем
document.addEventListener("click", (e) => {
  const zc = e.target.closest("[data-zc]");
  if (zc && e.target.closest("#tour")) {
    e.stopPropagation();
    return zcAct(zc.dataset.zc);
  }
  if (ZC.step && e.target.closest("#tour [data-coach-back]")) {
    e.stopPropagation();
    return zcMiss();
  }
  const club = e.target.closest("[data-zv-club]");
  if (club) {
    e.stopPropagation();
    ZV.market.club = club.dataset.zvClub;
    haptic();
    closeMatch();
    return zvPaintBody();
  }
  const sort = e.target.closest("[data-zv-sort]");
  if (sort) {
    e.stopPropagation();
    ZV.market.sort = sort.dataset.zvSort;
    haptic();
    closeMatch();
    return zvPaintBody();
  }
  const el = e.target.closest("[data-zv]");
  if (!el || el.disabled) return;
  e.stopPropagation();
  zvAct(el.dataset.zv, el.dataset.zvArg || "", el);
}, true);

// Esc в онбординге — «Пропустить»; Enter в поле кода — «Вступить»
document.addEventListener("keydown", (e) => {
  if (ZC.step && $("#tour") && e.key === "Escape") {
    e.stopPropagation();
    e.preventDefault();
    if (ZC.step.startsWith("tip") || ZC.step === "built") return zcAct("name");
    return zcEnd();
  }
  if (e.key === "Enter" && e.target && e.target.id === "zv-code") {
    e.preventDefault();
    zvLeagueJoin(e.target.value);
  }
}, true);

// Точка на вкладке при запуске, пока «Звено» не открывали: только если сервер подключён и это Telegram.
// pool.json не качаем — он большой и нужен только во вкладке
async function zvPeek() {
  if (ZV.status || zvMockMode() || !zvApiBase() || !inTelegram || !tg.initData) return;
  try {
    const r = await fetch("data/zveno/tours.json", { cache: "no-cache" });
    const tours = r.ok ? await r.json() : null;
    if (!tours || tours.status !== "open" || ZV.status) return;
    const me = await zvApi("GET", "/me");
    if (!me || !me.manager || ZV.status) return;
    const t = (me.season && me.season.tour_next) || tours.tour_next;
    const team0 = t ? await zvApi("GET", `/team?tour=${t}`) : null;
    if (!team0 || ZV.status) return;
    zvDue(team0);
  } catch (e) { /* точки не будет — не страшно */ }
}

// Ссылка из бота или от друга: startapp=zveno и startapp=lg-<код>. Разбираем раньше id команды
function zvLinkParam(sp) {
  if (!sp) return null;
  if (sp === "zveno") return { tab: "zveno" };
  const m = /^lg-([A-Za-z0-9-]{3,24})$/.exec(sp);
  if (m) return { tab: "zveno", join: m[1].toUpperCase() };
  return null;
}
function zvFromLink(link) {
  if (!link) return;
  if (link.join) {
    state.zvJoin = link.join;
    lsSet(Z_JOIN_KEY, link.join);
    ZV.seg = "leagues";
  }
}
// Код лиги, сохранённый в Прологе, ждёт открытия рынка
function zvRestoreJoin() {
  if (!state.zvJoin && lsGet(Z_JOIN_KEY)) state.zvJoin = lsGet(Z_JOIN_KEY);
}

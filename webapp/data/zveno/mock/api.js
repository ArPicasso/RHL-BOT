"use strict";
// Мок-сервер «Звена» для разработки мини-аппа: ?zveno_mock=1 — менеджера нет (онбординг),
// ?zveno_mock=team — сразу с собранным звеном, ?zveno_mock=open — рынок только открылся. Отвечает, как
// настоящий сервер (контракт, раздел 4, и deploy/README.md, раздел «API» в ветке сервера), живёт в
// localStorage этого устройства. Очки — сумма двух лучших из pool.json и капитан ×2, без сыгранности
// и автозамен: это правила сервера, здесь только форма ответов. В продакшене не грузится.
(function () {
  const KEY = `zv_mock_state_${new URLSearchParams(location.search).get("zveno_mock")}`;
  const mode = new URLSearchParams(location.search).get("zveno_mock");
  const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
  let db = null;
  const load = () => {
    try { db = JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { db = null; }
    if (!db) db = { manager: null };
  };
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(db)); } catch (e) { /* приватный режим */ } };
  const fail = (text) => { throw new Error(text); };
  const P = (id) => ZV.byId[id];
  const T = () => ZV.tours;
  const tourNext = () => T().tour_next;
  const tourNow = () => T().tour_now;
  const clone = (x) => JSON.parse(JSON.stringify(x));
  const firstWindow = () => !tourNow() && tourNext() === T().first_tour;
  const manager = () => Object.assign(clone(db.manager), { title: db.manager.name.join(" ") });
  const namesOk = (n) => Array.isArray(n) && n.length === 2 && ZV.names && ZV.names.adj.includes(n[0]) && ZV.names.noun.includes(n[1]);

  // Цена продажи (ADR-014, раздел 6): покупка плюс половина прироста, не больше +500; подешевел — текущая
  function sale(id) {
    const p = P(id);
    const b = db.bought[id] != null ? db.bought[id] : p.price;
    const normal = p.price > b ? b + Math.min(500, Math.floor((p.price - b) / 2 / 100) * 100) : p.price;
    return p.status === "rest" ? Math.max(b, normal) : normal;
  }
  function check(sq) {
    const s = zvSqOf(sq);
    const all = zvIds(s);
    if (all.length !== 15 || new Set(all).size !== 15) fail("В составе должно быть 15 разных наклеек.");
    for (const [k, slot] of Z_ALL) if (!P(zvGet(s, k)) || P(zvGet(s, k)).slot !== slot) fail("Наклейка стоит не на своём месте.");
    const clubs = {};
    all.forEach((id) => { clubs[P(id).club] = (clubs[P(id).club] || 0) + 1; });
    if (Object.values(clubs).some((n) => n > 3)) fail("Не больше 3 наклеек из одного клуба.");
    return s;
  }
  function album() {
    const a = new Set(db.album || []);
    return [...a];
  }
  function scoreFor(sq, t) {
    const s = zvSqOf(sq);
    const by = {};
    let total = 0;
    const main = Z_MAIN.map(([k]) => zvGet(s, k)).filter(Boolean);
    const capPlays = s.captain && P(s.captain) && P(s.captain).tours[String(t)];
    for (const id of main) {
      const r = P(id).tours[String(t)];
      const best2 = r ? r.best2 : 0;
      const mult = id === s.captain || (!capPlays && id === s.assistant) ? 2 : 1;
      by[id] = { matches: r ? r.m : [], best2, synergy: 0, mult, total: best2 * mult };
      total += best2 * mult;
    }
    const penalty = t === tourNext() ? (db.penaltyNext || 0) : t === tourNow() ? (db.penaltyNow || 0) : 0;
    total = Math.max(0, total - penalty);
    return { total, provisional: Date.parse(zvTour(t).close) > zvNow(), by_id: by, subs: [], penalty };
  }
  function warnings(sq) {
    if (!db.manager.settings.autopilot) return [];
    const g = guideOf(db.manager.fav_club);
    const who = g ? g.name : "Автопилот";
    const tr = zvTour(tourNext());
    const when = tr ? zvParts(tr.deadline) : null;
    const s = zvSqOf(sq);
    const pick = (out) => ZV.pool.players.find((p) => p.slot === P(out).slot && p.status === "ok" && !zvIds(s).includes(p.id)
      && zvClubCount(s, p.club, out) < 3 && p.price <= db.bank + sale(out));
    return Z_MAIN.map(([k]) => zvGet(s, k)).filter((id) => id && P(id).status === "rest" && !db.kept.includes(id) && id !== db.manager.my_player)
      .map((id) => ({ id, in: (pick(id) || {}).id || null, text: `${zvSurname(P(id))} пропустил 4 матча подряд. ${who} заменит его${when ? ` в ${when.weekday} ${when.hour}:${when.minute}` : " к дедлайну"}` }));
  }
  function team(t) {
    const next = tourNext();
    const sq = t === next ? db.squad : db.prev;
    const s = zvSqOf(sq);
    const all = zvIds(s);
    const saleMap = {};
    all.forEach((id) => { saleMap[id] = sale(id); });
    const unlimited = firstWindow() || db.boost === "zalivka";
    const fee = unlimited || db.paid >= 2 ? [] : next >= 19 ? ["points"] : db.bank >= Z_FEE_ICE ? ["points", "ice"] : ["points"];
    const alb = new Set(album());
    const mainClubs = Z_MAIN.map(([k]) => zvGet(s, k)).filter(Boolean).map((id) => P(id).club);
    return {
      tour: t, deadline: zvTour(t).deadline, locked: t !== next,
      lineup: clone(sq.lineup), bench: clone(sq.bench), captain: s.captain, assistant: s.assistant,
      bank: db.bank, value: all.reduce((a, id) => a + P(id).price, 0) + db.bank,
      free: firstWindow() ? 0 : db.free, unlimited, paid_this_tour: db.paid, fee_options: fee,
      sale: saleMap, bought: Object.fromEntries(all.map((id) => [id, db.bought[id] != null ? db.bought[id] : P(id).price])),
      points: scoreFor(sq, t),
      album: [...alb],
      mission: next >= 3 ? { done: mainClubs.some((c) => !alb.has(c)), clubs_left: state.data.teams.map((x) => x.id).filter((c) => !alb.has(c)) } : null,
      boost: db.boost || null, boosts: { zalivka: db.zalivka != null ? db.zalivka : 1 },
      hidden: [], start_tour: db.manager.start_tour,
      warnings: t === next ? warnings(sq) : [],
    };
  }
  // Соперники в таблицах: названия только из конструктора
  function rivals(seed, n) {
    const N = ZV.names;
    let x = seed;
    const rnd = () => { x = (x * 1103515245 + 12345) % 2147483648; return x / 2147483648; };
    return Array.from({ length: n }, () => ({ team_name: `${N.adj[Math.floor(rnd() * N.adj.length)]} ${N.noun[Math.floor(rnd() * N.noun.length)]}`, points: Math.floor(40 + rnd() * 90) }));
  }
  function table(id) {
    const L = db.leagues.find((l) => l.id === id);
    const my = scoreFor(db.prev, tourNow() || tourNext()).total + 60;
    const rows = rivals(L.seed, L.of - 1).concat([{ team_name: zvName(db.manager.name), points: my, me: true }]);
    rows.sort((a, b) => b.points - a.points);
    return rows.map((r, i) => ({ place: i + 1, team_name: r.team_name, points: r.points, me: !!r.me }));
  }
  function leagues() {
    return db.leagues.map((l) => {
      const rows = table(l.id);
      const me = rows.find((r) => r.me);
      const x = { id: l.id, kind: l.kind, title: l.title, place: me.place, of: rows.length, points: me.points };
      if (l.kind === "own") Object.assign(x, { code: l.code, owner: !!l.owner });
      return x;
    });
  }
  const code = () => Array.from({ length: 6 }, () => "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"[Math.floor(Math.random() * 32)]).join("");
  function create(body) {
    const s = check(body);
    const spent = zvSpent(s);
    if (spent > Z_BUDGET) fail("Не хватает льдинок: состав дороже 100 000.");
    if (T().status !== "open") fail("«Звено» ещё в Прологе: команду можно собрать, когда откроется рынок.");
    if (!namesOk(body.name)) fail("Такого названия нет в списке. Выбери из вариантов.");
    const name = body.name;
    const club = state.teams[body.fav_club] ? body.fav_club : state.fav;
    const sq = { lineup: s.lineup, bench: s.bench, captain: body.captain || s.captain, assistant: body.assistant || s.assistant };
    if (!sq.assistant || sq.assistant === sq.captain) sq.assistant = Z_MAIN.map(([k]) => zvGet(s, k)).find((id) => id && id !== sq.captain && P(id).slot !== "G");
    db = {
      manager: { name, fav_club: club, my_player: body.my_player || null, settings: { autopilot: true, messages: true, show_tg_name: false },
        start_tour: tourNext(), budget: Z_BUDGET, created_at: new Date(zvNow()).toISOString() },
      squad: sq, prev: clone(sq), bank: Z_BUDGET - spent, free: 1, paid: 0, penaltyNow: 0, kept: [],
      bought: Object.fromEntries(zvIds(s).map((id) => [id, P(id).price])),
      album: Z_MAIN.map(([k]) => zvGet(s, k)).filter(Boolean).map((id) => P(id).club),
      leagues: [
        { id: `club:${club}`, kind: "club", title: `Болельщики «${state.teams[club].name}»`, of: 37, seed: 7 },
        { id: "step:2026-10:4:1", kind: "step", title: "Коробка · группа «Ермак»", of: 20, seed: 11 },
        { id: "month:2026-10", kind: "month", title: "Октябрь", of: 214, seed: 13 },
        { id: "overall", kind: "overall", title: "Вся лига", of: 214, seed: 17 },
      ],
      journal: [{ at: new Date(zvNow()).toISOString(), kind: "deal", text: `Команда «${name.join(" ")}» собрана: 15 наклеек, в кассе ${zvFmt(Z_BUDGET - spent)} льдинок` }],
    };
    save();
    return team(tourNext());
  }
  // ?zveno_mock=team: звено собрано заранее, в основе — «отдыхающий», чтобы было предупреждение
  function seed() {
    const games = zvGames(tourNext());
    const sq = zvAuto({ games, my: zvMy() });
    const rest = ZV.pool.players.find((p) => p.status === "rest");
    if (rest && zvClubCount(sq, rest.club) < 3) {
      const out = zvGet(sq, "L2.F.2");
      if (out && sq.captain !== out && sq.assistant !== out) zvSet(sq, "L2.F.2", rest.id);
    }
    if (zvSpent(sq) > Z_BUDGET) zvSet(sq, "L2.F.2", zvGet(sq, "B.3"));
    create({ name: [ZV.names.adj[0], ZV.names.noun[0]], fav_club: state.fav, lineup: sq.lineup, bench: sq.bench, captain: sq.captain });
    db.leagues.unshift({ id: "own:1", kind: "own", title: `${ZV.names.adj[1]} ${ZV.names.noun[1]}`, of: 12, seed: 5, code: "TRIB44", owner: false });
    // часть наклеек взята дешевле — есть «Почему 5 300?»
    zvIds(zvSqOf(db.squad)).slice(0, 5).forEach((id, i) => { db.bought[id] = Math.max(4000, P(id).price - [800, 400, 0, 1200, 200][i]); });
    db.journal.unshift({ at: new Date(zvNow() - 864e5).toISOString(), kind: "autosub", text: "Тур 1: Филиппов сыграл за Козлова — у «Сочи» не было матчей" });
    save();
  }

  async function api(method, path, body) {
    await sleep(220);
    if (!ZV.names) ZV.names = zvNamesOf(await zvJson("names.json").catch(() => null));
    load();
    const url = new URL(path, "https://mock.local");
    const p = url.pathname;
    if (method === "GET" && p === "/me") {
      if (!db.manager && mode === "team") seed();
      const tr = zvTour(tourNext());
      return { manager: db.manager ? manager() : null,
        season: { status: T().status, tour_next: tourNext(), tour_now: tourNow(), deadline: tr ? tr.deadline : null, first_tour: T().first_tour } };
    }
    if (method === "POST" && p === "/team") {
      if (db.manager) fail("Команда уже есть.");
      return create(body || {});
    }
    if (!db.manager) fail("Сначала собери звено.");
    if (method === "GET" && p === "/team") {
      const t = +(url.searchParams.get("tour") || tourNext());
      if (t !== tourNext() && t !== tourNow()) fail("Такого тура нет.");
      return team(t);
    }
    if (method === "PUT" && p === "/team/lineup") {
      const s = check(body);
      const was = new Set(zvIds(zvSqOf(db.squad)));
      if (zvIds(s).some((id) => !was.has(id))) fail("Новые наклейки — только через обмен.");
      db.squad = { lineup: s.lineup, bench: s.bench, captain: body.captain, assistant: db.squad.assistant };
      const main = Z_MAIN.map(([k]) => zvGet(s, k));
      if (!main.includes(db.squad.captain)) fail("Капитан должен быть в основе.");
      if (!main.includes(db.squad.assistant) || db.squad.assistant === db.squad.captain) db.squad.assistant = main.find((id) => id && id !== db.squad.captain && P(id).slot !== "G");
      save();
      return team(tourNext());
    }
    if (method === "POST" && p === "/team/transfer") {
      const { out, pay } = body;
      const inn = body.in;
      const s = zvSqOf(db.squad);
      if (!P(inn) || zvIds(s).includes(inn)) fail("Эту наклейку взять нельзя.");
      const key = out ? zvKeyOf(s, out) : (Z_ALL.find(([k, sl]) => sl === P(inn).slot && !zvGet(s, k)) || [])[0];
      if (!key) fail(out ? "Этой наклейки нет в составе." : "Пустого места под эту наклейку нет.");
      if (out && P(inn).slot !== P(out).slot) fail("Меняться можно только на наклейку того же слота.");
      if (zvClubCount(s, P(inn).club, out) >= 3) fail("Не больше 3 наклеек из одного клуба.");
      const freeRest = (out && P(out).status === "rest") || db.boost === "zalivka" || firstWindow();
      let fee = 0;
      if (!freeRest && db.free <= 0) {
        if (pay === "ice") fee = Z_FEE_ICE;
        else if (pay !== "points") fail("Бесплатные обмены кончились — выбери, чем платить.");
        if (db.paid >= 2) fail("Больше двух платных обменов за тур нельзя.");
      }
      const left = db.bank + (out ? sale(out) : 0) - P(inn).price - fee;
      if (left < 0) fail(`Не хватает ${zvFmt(-left)} льдинок.`);
      db.bank = left;
      if (!freeRest) {
        if (db.free > 0) db.free -= 1;
        else { db.paid += 1; if (pay === "points") db.penaltyNext = (db.penaltyNext || 0) + Z_FEE_POINTS; }
      }
      zvSet(s, key, inn);
      if (s.captain === out) s.captain = inn;
      if (s.assistant === out) s.assistant = inn;
      db.squad = { lineup: s.lineup, bench: s.bench, captain: s.captain, assistant: s.assistant };
      db.bought[inn] = P(inn).price;
      db.journal.unshift({ at: new Date(zvNow()).toISOString(), kind: "deal", text: `${out ? `Отдал ${P(out).name}, взял` : "На пустое место —"} ${P(inn).name}${fee ? " — обмен стоил 600 льдинок" : pay === "points" && !freeRest && db.free <= 0 ? " — обмен стоил 8 очков" : ""}` });
      save();
      return team(tourNext());
    }
    if (method === "POST" && p === "/team/keep") {
      if (body.keep === false) db.kept = db.kept.filter((x) => x !== body.id);
      else if (!db.kept.includes(body.id)) db.kept.push(body.id);
      db.journal.unshift({ at: new Date(zvNow()).toISOString(), kind: "autopilot", text: `${P(body.id) ? P(body.id).name : "Наклейка"}: ${body.keep === false ? "автопилот снова может заменить" : "автопилот не тронет"}` });
      save();
      return team(tourNext());
    }
    if (method === "POST" && p === "/team/boost") {
      if (body.boost !== "zalivka") fail("Такого буста пока нет.");
      if (firstWindow()) fail("До первого дедлайна обмены и так без ограничений — «Заливка» подождёт.");
      if (db.boost) fail("В этом туре буст уже включён.");
      if ((db.zalivka != null ? db.zalivka : 1) <= 0) fail("Заливок больше нет.");
      db.boost = "zalivka";
      db.zalivka = 0;
      db.journal.unshift({ at: new Date(zvNow()).toISOString(), kind: "deal", text: `Тур ${tourNext()} залит: все обмены бесплатны` });
      save();
      return team(tourNext());
    }
    if (method === "GET" && p === "/leagues") return leagues();
    if (method === "GET" && p.startsWith("/leagues/")) {
      const id = decodeURIComponent(p.slice(9));
      if (!db.leagues.some((l) => l.id === id)) fail("Такой лиги нет.");
      return table(id);
    }
    if (method === "POST" && p === "/leagues") {
      if (!namesOk(body.name)) fail("Такого названия нет в списке. Выбери из вариантов.");
      const c = code();
      const id = `own:${db.leagues.length + 1}`;
      db.leagues.unshift({ id, kind: "own", title: zvName(body.name), of: 1, seed: 3, code: c, owner: true });
      save();
      return { id, code: c };
    }
    if (method === "POST" && p === "/leagues/join") {
      const c = String(body.code || "").toUpperCase().replace(/^LG-/, "");
      if (c.length < 4) fail("Такого кода нет. Проверь ссылку или попроси новую.");
      const have = db.leagues.find((l) => l.code === c);
      if (have) return { id: have.id };
      const id = `own:${db.leagues.length + 1}`;
      const n = ZV.names;
      db.leagues.unshift({ id, kind: "own", title: `${n.adj[c.charCodeAt(0) % n.adj.length]} ${n.noun[c.charCodeAt(1) % n.noun.length]}`, of: 13, seed: c.charCodeAt(2), code: c });
      save();
      return { id };
    }
    if (method === "GET" && p === "/journal") return db.journal;
    if (method === "PUT" && p === "/settings") {
      for (const k of ["autopilot", "messages", "show_tg_name"]) if (k in body) db.manager.settings[k] = !!body[k];
      if ("my_player" in body) db.manager.my_player = body.my_player || null;
      save();
      return manager();
    }
    if (method === "DELETE" && p === "/me") {
      db = { manager: null };
      save();
      return {};
    }
    fail("Мок не знает такого запроса.");
  }
  window.ZVENO_MOCK_API = api;
})();

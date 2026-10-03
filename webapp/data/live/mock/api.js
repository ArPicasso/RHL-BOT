"use strict";
// Мок-сервер матч-центра (ADR-019) и прогнозов «Кто победит?» (ADR-020) для разработки мини-аппа.
// Включается ?live_mock=1 — живое дня в разных статусах; =pre — до матчей, =post — после,
// =guest — как вне Telegram (голос не принимается), =stale — живое старше 5 минут.
// Отвечает, как server.py и predict.py: те же пути, поля Tally, коды 400/401/404/409 и тексты ошибок.
// Голоса живут в localStorage этого устройства. Доли трибуны выдуманные: это фикстура разработки,
// в продакшене файл не грузится.
(function () {
  const mode = new URLSearchParams(location.search).get("live_mock") || "1";
  const dir = { pre: "pre/", post: "post/" }[mode] || "";
  const KEY = `live_mock_${mode}`;
  const TZ = "Europe/Moscow";
  const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
  const fail = (status, text) => { throw Object.assign(new Error(text), { status }); };
  const KEY_RE = /^(\d{4}-\d{2}-\d{2})\|([a-z0-9-]{1,40})\|([a-z0-9-]{1,40})$/;
  const CLOSED = new Set(["live", "break", "ended", "final", "moved", "off"]);

  let db = null;
  const load = () => {
    try { db = JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { db = null; }
    if (!db) db = { votes: {}, erased: false };
  };
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(db)); } catch (e) { /* приватный режим */ } };

  // Живой файл дня. Время ответа источника освежаем, чтобы счёт не считался старым, — кроме =stale
  let live = null;
  async function liveFile() {
    if (!live) {
      const r = await fetch(`data/live/mock/${dir}today.json`, { cache: "no-cache" });
      if (!r.ok) fail(404, "Этих данных пока нет: живой счёт ещё не собран.");
      live = await r.json();
    }
    const d = JSON.parse(JSON.stringify(live));
    if (mode !== "stale") {
      const now = new Date(Date.now() - 25e3).toISOString();
      d.updated = now;
      d.games.forEach((x) => { if (x.seen) x.seen = now; });
    }
    return d;
  }

  // Детерминированный шум по ключу матча: доли не скачут между перерисовками
  function seeded(text) {
    let h = 2166136261;
    for (let i = 0; i < text.length; i += 1) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619); }
    return () => { h = Math.imul(h ^ (h >>> 15), 2246822507); h = Math.imul(h ^ (h >>> 13), 3266489909); return ((h ^= h >>> 16) >>> 0) / 4294967296; };
  }
  const todayISO = () => new Intl.DateTimeFormat("en-CA", { timeZone: TZ }).format(new Date());
  const winner = (s) => (s && Number.isInteger(s.home) && Number.isInteger(s.away) && s.home !== s.away ? (s.home > s.away ? "home" : "away") : null);

  // Что известно о матче — как predict.merge: начало, статус, итог
  async function game(key) {
    const m = KEY_RE.exec(key || "");
    if (!m || m[2] === m[3]) return null;
    const [, day, home, away] = m;
    const lg = state.data.games.find((g) => g.date === day && g.home === home && g.away === away) || null;
    const lv = day === todayISO() ? ((await liveFile()).games || []).find((x) => x.key === key) || null : null;
    if (!lg && !lv) return null;
    const src = lv && (lv.start || lv.time) ? lv : lg || {};
    let start = Date.parse(src.start || "");
    if (isNaN(start) && /^\d{1,2}:\d{2}$/.test(src.time || "")) start = Date.parse(`${day}T${src.time.padStart(5, "0")}:00+03:00`);
    const status = lv ? lv.status : null;
    const result = (lg && winner(lg.score)) || (lv && (status === "ended" || status === "final") ? winner(lv.score) : null);
    return { key, day, start: isNaN(start) ? null : start, status, result };
  }
  const isOpen = (g) => !g.result && !CLOSED.has(g.status) && (g.start != null ? Date.now() < g.start : todayISO() <= g.day);
  function counts(key) {
    const r = seeded(key);
    let h = 18 + Math.floor(r() * 110), a = 12 + Math.floor(r() * 90);
    const mine = db.votes[key];
    if (mine === "home") h += 1;
    if (mine === "away") a += 1;
    return [h, a];
  }
  function tally(g, me) {
    const [h, a] = counts(g.key);
    const votes = h + a;
    const hp = votes ? Math.floor((200 * h + votes) / (2 * votes)) : 0;   // как predict.shares: целые, в сумме 100
    return { home: hp, away: votes ? 100 - hp : 0, votes, me: me ? db.votes[g.key] || null : null, open: isOpen(g), result: g.result };
  }
  const needAuth = (auth) => { if (!auth) fail(401, "Открой мини-апп из Telegram: без входа через Telegram это не работает."); };

  window.LIVE_MOCK_API = async function (method, path, body, auth) {
    load();
    await sleep(method === "GET" ? 180 : 350);
    if (method === "GET" && path === "/live/today.json") return liveFile();
    let m = /^\/predict\/day\/(\d{4}-\d{2}-\d{2})$/.exec(path);
    if (method === "GET" && m) {
      const keys = new Set(state.data.games.filter((g) => g.date === m[1]).map((g) => `${g.date}|${g.home}|${g.away}`));
      if (m[1] === todayISO()) ((await liveFile()).games || []).forEach((x) => keys.add(x.key));
      const out = {};
      for (const k of [...keys].sort()) {
        const g = await game(k);
        if (g) out[k] = tally(g, auth);
      }
      return { games: out };
    }
    if (method === "POST" && path === "/predict/vote") {
      needAuth(auth);
      const key = body && body.key, pick = body && body.pick;
      if (!KEY_RE.test(key || "")) fail(400, "Не понял, какой это матч.");
      if (pick !== "home" && pick !== "away") fail(400, "Выбери хозяев или гостей.");
      const g = await game(key);
      if (!g) fail(404, "Такого матча нет в календаре.");
      if (!isOpen(g)) fail(409, "Приём прогнозов закрыт: матч уже начался.");
      db.votes[key] = pick;
      save();
      return tally(g, true);
    }
    if (method === "GET" && path === "/predict/me") {
      needAuth(auth);
      // выдуманная история «угадано 5 из 8, 2 подряд» плюс свои голоса за сыгранные матчи
      let right = db.erased ? 0 : 5, of = db.erased ? 0 : 8, streak = db.erased ? 0 : 2;
      for (const [key, pick] of Object.entries(db.votes).sort()) {
        const g = await game(key);
        if (!g || !g.result) continue;
        of += 1;
        if (g.result === pick) { right += 1; streak += 1; } else streak = 0;
      }
      return { right, of, streak };
    }
    if (method === "DELETE" && path === "/predict/me") {
      needAuth(auth);
      db = { votes: {}, erased: true };
      save();
      return {};
    }
    fail(404, "Такого адреса у сервера нет.");
  };
})();

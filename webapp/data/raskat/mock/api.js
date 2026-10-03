"use strict";
// Мок-сервер зачёта «Раската» для разработки мини-аппа: ?raskat_mock=1 — зачёт работает,
// ?raskat_mock=solved — раскат дня уже собран. Отвечает, как настоящий server.py
// (docs/raskat/contract.md, раздел 5): те же поля, коды ошибок и тексты, и живёт в localStorage
// этого устройства. Болельщики в таблицах выдуманные: это фикстура разработки, в продакшене файл
// не грузится. Путь и очки считает теми же функциями, что и поле (rsCheck, rsDayPoints, rsPoints) —
// чтобы расхождение формулы между сервером и мини-аппом было видно сразу.
// Дуэль: свой код — тот, что выдал POST /duel; код NOTFOUND — 404, любой другой — чужая дуэль.
(function () {
  const mode = new URLSearchParams(location.search).get("raskat_mock") || "1";
  const KEY = `rs_mock_${mode}`;
  const MIN_MS_PER_CELL = 150;
  const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
  // Ошибка — как у сервера: {"error": "…"} и код, у 409 — ещё и прежний Result
  const fail = (status, text, extra) => {
    const e = new Error(text);
    e.status = status;
    e.data = Object.assign({ error: text }, extra || {});
    throw e;
  };
  // Выдуманные болельщики: имя и первая буква фамилии, как отдаёт сервер по галочке show_tg_name
  const FANS = ["Пётр К.", "Анна С.", "Илья М.", "Дарья В.", "Кирилл Ж.", "Марк Т.", "Егор Б.",
    "Нина Л.", "Савва Р.", "Юра П.", "Лиза Н.", "Тимур Г.", "Олег Д.", "Вера Ф.", "Рома Ш.",
    "Соня А.", "Глеб Е.", "Майя О.", "Фёдор Ц.", "Катя И.", "Миша Х.", "Полина У.", "Денис Я.", "Зоя Щ."];
  // Родительный падеж — как GENITIVE в server.py, для нескольких клубов; остальным — название как есть
  const GEN = { "ryazan-vdv": "Рязани-ВДВ", arktika: "Арктики", sokol: "Сокола", tambov: "Тамбова", proton: "Протона", belgorod: "Белгорода" };
  const label = (club) => (club ? `Болельщик «${GEN[club] || team(club).name}»` : "Болельщик");

  let db = null;
  const load = () => {
    try { db = JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { db = null; }
    if (!db) db = { res: {}, settings: { show_tg_name: false, messages: true, club: null }, own: null };
  };
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(db)); } catch (e) { /* приватный режим */ } };
  // Детерминированный шум по строке: таблица одного дня не меняется между перерисовками
  function seeded(text) {
    let h = 2166136261;
    for (let i = 0; i < text.length; i += 1) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619); }
    return () => { h = Math.imul(h ^ (h >>> 15), 2246822507); h = Math.imul(h ^ (h >>> 13), 3266489909); return ((h ^= h >>> 16) >>> 0) / 4294967296; };
  }
  const clubs = () => (state.data ? state.data.teams.map((t) => t.id) : ["ryazan-vdv"]);
  const myClub = () => db.settings.club || null;
  const prevDay = (d) => new Date(Date.parse(`${d}T00:00:00Z`) - 864e5).toISOString().slice(0, 10);

  // Выдуманные результаты дня: очки дня 58–99, время под норму. Половина клубов — у «Рязани»
  function crowd(date) {
    const r = seeded(`day:${date}`);
    const list = clubs();
    return FANS.map((name) => {
      const points = 58 + Math.floor(r() * 42);
      const club = r() > 0.5 ? "ryazan-vdv" : list[Math.floor(r() * list.length)];
      return {
        name: r() > 0.45 ? name : label(club),
        club,
        points,
        ms: Math.round((40 + (100 - points) * 1.6 + r() * 12) * 1000),
        hint: r() > 0.86,
      };
    });
  }
  // Зачёт дня: по очкам дня, при равенстве раньше тот, кто собрал быстрее (контракт, раздел 4)
  function ranked(date) {
    const all = crowd(date);
    const mine = db.res[date];
    if (mine) all.push({ name: db.settings.show_tg_name ? "Ты Т." : label(mine.club), club: mine.club, points: mine.points, ms: mine.ms, hint: mine.hint, me: true });
    all.sort((a, b) => b.points - a.points || a.ms - b.ms);
    all.forEach((x, i) => { x.place = i + 1; x.me = !!x.me; });
    return all;
  }
  function result(date) {
    const r = db.res[date];
    if (!r) return null;
    const rows = ranked(date);
    const me = rows.find((x) => x.me);
    return { date, points: r.points, total: r.total, ms: r.ms, hint: r.hint, place: me ? me.place : null, of: rows.length, streak: r.streak };
  }
  function clubsTable(date) {
    const r = seeded(`clubs:${date}`);
    const my = myClub();
    const rows = clubs().map((club) => ({ club, fans: club === my ? 3 : 1 + Math.floor(r() * 44), days: 1, avg: Math.round((62 + r() * 30) * 10) / 10 }));
    const day = rows.filter((x) => x.fans >= 5).sort((a, b) => b.avg - a.avg || b.fans - a.fans || a.club.localeCompare(b.club))
      .map((x, i) => ({ place: i + 1, club: x.club, avg: x.avg, fans: x.fans, days: 1, me: x.club === my }));
    // клубы ниже порога — отдельным списком, без места и среднего
    const low = rows.filter((x) => x.fans < 5).map((x) => ({ place: null, club: x.club, avg: null, fans: x.fans, days: 1, me: x.club === my }));
    const season = day.slice(0, 12).map((x) => Object.assign({}, x, { days: 1 + Math.floor(r() * 5), avg: Math.round((x.avg - 1.5 + r() * 3) * 10) / 10 }))
      .sort((a, b) => b.avg - a.avg).map((x, i) => Object.assign(x, { place: i + 1 }));
    return { day, low, season, me: my };
  }
  // Серия сейчас: с сегодняшним, если он собран; иначе по вчерашний — день ещё не кончился
  function streakNow(today) {
    for (const d of [today, prevDay(today)]) if (db.res[d]) return db.res[d].streak;
    return 0;
  }

  async function api(method, p, body) {
    load();
    await sleep(180);
    const today = RS.idx ? rsToday() : "";
    if (method === "GET" && p === "/me") {
      const dates = Object.keys(db.res).sort();
      const best = dates.map((d) => db.res[d]).sort((a, b) => b.points - a.points || a.ms - b.ms)[0];
      return { club: myClub(), streak: streakNow(today), best: best ? result(best.date) : null, today: result(today),
        show_tg_name: !!db.settings.show_tg_name, messages: !!db.settings.messages };
    }
    if (method === "POST" && p.startsWith("/day/")) {
      const date = p.slice(5);
      if (db.res[date]) fail(409, "Результат этого дня уже принят: в зачёт идёт первый собранный раскат.", result(date));
      if (date > today) fail(400, "Этот раскат ещё не открылся.");
      if (date < today) fail(400, "Этот день уже закончился: тренировка в зачёт не идёт.");
      const d = RS.day && RS.day.date === date ? RS.day : null;
      if (!d) fail(400, "Расклад этого дня не открыт.");
      if ("club" in (body || {})) {
        if (body.club !== null && !state.teams[body.club]) fail(400, "Такого клуба в РХЛ нет.");
        db.settings.club = body.club;
      }
      const bad = rsCheck(d, body && body.path);
      if (bad) fail(400, bad);
      const ms = Math.round(Number(body.ms) || 0);
      // невозможное время отбрасываем: меньше 0,15 секунды на клетку пути (контракт, раздел 5)
      if (ms < body.path.length * MIN_MS_PER_CELL) fail(400, "Так быстро шайбу не провести: меньше 0,15 секунды на клетку. Результат в зачёт не идёт.");
      const before = db.res[prevDay(date)] ? db.res[prevDay(date)].streak : 0;
      // points — очки дня, total — итог с серией (контракт, раздел 5)
      db.res[date] = { date, club: myClub(), points: rsDayPoints(d.par, ms / 1000, !!body.hint), total: rsPoints(d.par, ms / 1000, before, !!body.hint), ms, hint: !!body.hint, streak: before + 1 };
      save();
      return result(date);
    }
    if (method === "GET" && p.startsWith("/day/")) {
      const date = p.slice(5);
      const rows = ranked(date);
      return { me: result(date), top: rows.slice(0, 50), of: rows.length };
    }
    if (method === "GET" && p.startsWith("/clubs/")) return clubsTable(p.slice(7));
    if (method === "GET" && p.startsWith("/duel/")) {
      const code = decodeURIComponent(p.slice(6));
      if (code === "NOTFOUND") fail(404, "Дуэль не нашлась: ссылку удалили или в ней ошибка.");
      if (code === db.own) return { name: db.settings.show_tg_name ? "Ты Т." : label(myClub()), days: [], own: true };
      // только дни, когда играли оба: соперник «играл» не каждый день
      const r = seeded(p);
      const days = Object.keys(db.res).sort().filter(() => r() > 0.2).map((date) => ({ date, me: db.res[date].points, them: 58 + Math.floor(r() * 42) }));
      return { name: "Болельщик «Арктики»", days };
    }
    if (method === "POST" && p === "/duel") {
      if (!db.own) { db.own = "RS7K42"; save(); }
      return { code: db.own };
    }
    if (method === "PUT" && p === "/settings") {
      for (const k of ["show_tg_name", "messages"]) if (k in (body || {})) db.settings[k] = !!body[k];
      if ("club" in (body || {})) {
        if (body.club !== null && !state.teams[body.club]) fail(400, "Такого клуба в РХЛ нет.");
        db.settings.club = body.club;
      }
      save();
      return { club: db.settings.club, show_tg_name: db.settings.show_tg_name, messages: db.settings.messages };
    }
    if (method === "DELETE" && p === "/me") {
      db = { res: {}, settings: { show_tg_name: false, messages: true, club: null }, own: null };
      save();
      return {};
    }
    return fail(404, "Такого адреса у сервера нет.");
  }
  window.RASKAT_MOCK_API = api;
})();

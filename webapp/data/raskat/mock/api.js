"use strict";
// Мок-сервер зачёта «Раската» для разработки мини-аппа: ?raskat_mock=1 — зачёт работает,
// ?raskat_mock=solved — раскат дня уже собран. Отвечает, как настоящий сервер
// (docs/raskat/contract.md, раздел 5), и живёт в localStorage этого устройства.
// Болельщики и клубы в таблицах выдуманные: это фикстура разработки, в продакшене файл не грузится.
// Путь и очки считает теми же функциями, что и поле (rsCheck, rsDayPoints, rsPoints) — чтобы
// расхождение формулы между сервером и мини-аппом было видно сразу.
(function () {
  const mode = new URLSearchParams(location.search).get("raskat_mock") || "1";
  const KEY = `rs_mock_${mode}`;
  const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
  const fail = (text) => { throw new Error(text); };
  // Выдуманные болельщики: имя и первая буква фамилии, как отдаёт сервер по галочке show_tg_name
  const FANS = ["Пётр К.", "Анна С.", "Илья М.", "Дарья В.", "Кирилл Ж.", "Марк Т.", "Егор Б.",
    "Нина Л.", "Савва Р.", "Юра П.", "Лиза Н.", "Тимур Г.", "Олег Д.", "Вера Ф.", "Рома Ш.",
    "Соня А.", "Глеб Е.", "Майя О.", "Фёдор Ц.", "Катя И.", "Миша Х.", "Полина У.", "Денис Я.", "Зоя Щ."];

  let db = null;
  const load = () => {
    try { db = JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { db = null; }
    if (!db) db = { res: {}, settings: { show_tg_name: false, messages: true } };
  };
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(db)); } catch (e) { /* приватный режим */ } };
  // Детерминированный шум по строке: таблица одного дня не меняется между перерисовками
  function seeded(text) {
    let h = 2166136261;
    for (let i = 0; i < text.length; i += 1) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619); }
    return () => { h = Math.imul(h ^ (h >>> 15), 2246822507); h = Math.imul(h ^ (h >>> 13), 3266489909); return ((h ^= h >>> 16) >>> 0) / 4294967296; };
  }
  const clubs = () => (state.data ? state.data.teams.map((t) => t.id) : ["ryazan-vdv"]);
  const myClub = () => state.fav || clubs()[0];

  // Выдуманные результаты дня: очки дня 58–99, время под норму
  function crowd(date) {
    const r = seeded(`day:${date}`);
    const list = clubs();
    return FANS.map((name) => {
      const points = 58 + Math.floor(r() * 42);
      return {
        name: db.settings.show_tg_name || r() > 0.45 ? name : null,
        club: list[Math.floor(r() * list.length)],
        points,
        ms: Math.round((40 + (100 - points) * 1.6 + r() * 12) * 1000),
        hint: r() > 0.86,
      };
    });
  }
  function dayTable(date) {
    const all = crowd(date);
    const mine = db.res[date];
    if (mine) all.push({ name: db.settings.show_tg_name ? "Ты" : null, club: myClub(), points: mine.points, ms: mine.ms, hint: mine.hint, me: true });
    // по очкам дня, при равенстве раньше тот, кто собрал быстрее (контракт, раздел 4)
    all.sort((a, b) => b.points - a.points || a.ms - b.ms);
    all.forEach((x, i) => { x.place = i + 1; });
    const me = all.find((x) => x.me);
    if (mine && me) {
      mine.place = me.place;
      mine.of = all.length;
      save();
    }
    return { me: mine || null, top: all.slice(0, 50), of: all.length };
  }
  function clubsTable(date) {
    const r = seeded(`clubs:${date}`);
    const rows = clubs().map((club) => {
      const fans = 1 + Math.floor(r() * 44);
      return { club, fans, days: 1, avg: Math.round((62 + r() * 30) * 10) / 10, me: club === myClub() };
    });
    const ranked = rows.filter((x) => x.fans >= 5).sort((a, b) => b.avg - a.avg);
    ranked.forEach((x, i) => { x.place = i + 1; });
    const low = rows.filter((x) => x.fans < 5);
    const season = ranked.slice(0, 10).map((x, i) => Object.assign({}, x, { place: i + 1, days: 1, avg: Math.round((x.avg - 1.5 + r() * 3) * 10) / 10 }));
    return { day: ranked, low, season, me: myClub() };
  }
  function streak() {
    const dates = Object.keys(db.res).sort();
    let n = 0;
    let d = dates[dates.length - 1];
    while (d && db.res[d]) {
      n += 1;
      d = new Date(Date.parse(`${d}T00:00:00Z`) - 864e5).toISOString().slice(0, 10);
    }
    return n;
  }

  async function api(method, p, body) {
    load();
    await sleep(180);
    if (method === "GET" && p === "/me") {
      const dates = Object.keys(db.res).sort();
      const best = dates.map((d) => db.res[d]).sort((a, b) => b.points - a.points)[0] || null;
      const today = RS.idx ? rsToday() : "";
      return { club: myClub(), streak: streak(), best, today: db.res[today] || null, show_tg_name: !!db.settings.show_tg_name };
    }
    if (method === "POST" && p.startsWith("/day/")) {
      const date = p.slice(5);
      if (db.res[date]) fail("Результат этого дня уже принят.");
      const d = RS.day && RS.day.date === date ? RS.day : null;
      if (!d) fail("Расклад этого дня не открыт.");
      const bad = rsCheck(d, body && body.path);
      if (bad) fail(bad);
      const ms = Number(body.ms) || 0;
      // невозможное время отбрасываем: меньше 0,15 секунды на клетку пути (контракт, раздел 5)
      if (ms < body.path.length * 150) fail("Такое время невозможно — результат в зачёт не идёт.");
      const was = streak();
      // points — очки дня, total — итог с серией (контракт, раздел 5)
      const res = { date, points: rsDayPoints(d.par, ms / 1000, !!body.hint), total: rsPoints(d.par, ms / 1000, was, !!body.hint), ms, hint: !!body.hint, streak: was + 1 };
      db.res[date] = res;
      save();
      const t = dayTable(date);
      return Object.assign({}, res, { place: t.me.place, of: t.of });
    }
    if (method === "GET" && p.startsWith("/day/")) return dayTable(p.slice(5));
    if (method === "GET" && p.startsWith("/clubs/")) return clubsTable(p.slice(7));
    if (method === "GET" && p.startsWith("/duel/")) {
      const r = seeded(p);
      const days = Object.keys(db.res).sort().map((date) => ({ date, me: db.res[date].points, them: 58 + Math.floor(r() * 42) }));
      return { name: "Болельщик «Арктики»", days };
    }
    if (method === "POST" && p === "/duel") return { code: "RS7K42" };
    if (method === "PUT" && p === "/settings") {
      for (const k of ["show_tg_name", "messages"]) if (k in (body || {})) db.settings[k] = !!body[k];
      save();
      return {};
    }
    if (method === "DELETE" && p === "/me") {
      db = { res: {}, settings: db.settings };
      save();
      return {};
    }
    return fail("Мок не знает такого запроса.");
  }
  window.RASKAT_MOCK_API = api;
})();

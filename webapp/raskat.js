"use strict";
// «Раскат» (ADR-018) — головоломка дня: одна шайба проходит весь лёд и задевает номера звена
// по порядку. Интерфейс — DESIGN.md → «Раскат», стыки частей — docs/raskat/contract.md.
// Файл грузится перед app.js и зовёт его общие помощники (esc, $, lsGet/lsSet, cloud, showSheet,
// haptic, calm, guideFig, emblem, segBtn, placeRunners, shareLink) только из отрисовки
// и обработчиков: на верхнем уровне здесь — свои константы.
//
// Игра говорит с болельщиком одной строкой над полем (#rs-say): что делать дальше, почему ход не
// прошёл, тупик, подсказка, итог. Это же aria-live-область для экранного чтеца.
// Без сервера (window.RASKAT_API пуст) поле, время, подсказка и личный зачёт на устройстве
// работают полностью; общий зачёт откроется вместе с сервером, без выдуманных мест.
// Для разработки: ?raskat_mock=1 — зачёт на устройстве (data/raskat/mock/api.js),
// =solved — раскат дня уже собран, =none — файлов нет.
// Всё, что пришло из данных, вставляем только через esc(). Решения в данных нет: путь проверяем
// сами по правилам контракта (раздел 1), а подсказку и тупик считает решатель на устройстве.

const RS_DAYS_KEY = "rs_days";   // {дата: {ms, points, total, hint, path, train, sent, srv}} — на устройстве и в облаке
const RS_HINT_KEY = "rs_hint";   // дата, в которую подсказку к раскату дня уже брали: она одна в день
const RS_WOW_KEY = "rs_wow";     // дата, в которую «вау» уже показали: он один раз в день
const RS_SET_KEY = "rs_set";     // {name, messages} — настройки зачёта
const RS_DUEL_KEY = "rs_duel";   // код дуэли из ссылки rs-<код>
const RS_OWN_KEY = "rs_own";     // свой код дуэли: его отдал сервер на POST /duel
const RS_INTRO_KEY = "rs_intro"; // "1" — знакомство с полем на этом устройстве уже показали

// Очки — docs/raskat/contract.md, раздел 4. Формула повторена один в один в raskat/rules.py:
// любая правка меняет оба места и таблицу примеров в tests/test_raskat_points.py.
// Очки дня (база + время − подсказка) ранжируют зачёт дня, серия в них не входит: иначе у новичка
// потолок ниже, чем у того, кто играет неделю. Серия живёт в личном итоге дня
const RS_BASE = 60;         // за собранный раскат, одинаково для всех
const RS_TIME = 40;         // за время
const RS_RUN = 10;          // за серию, не больше
const RS_HINT_COST = 20;    // подсказка
const RS_HINT_CELLS = 3;    // подсказка показывает столько следующих клеток пути
const RS_SAY_MS = 2200;     // промах держится в строке столько, потом строка снова говорит, куда ехать
const RS_INTRO_MS = 3200;   // шаг знакомства с полем
const RS_TRAIN_MAX = 14;    // столько прошлых дней показываем в тренировке
const RS_CLUB_MIN = 5;      // порог кубка клубов: столько болельщиков клуба за день
const RS_STALE_MS = 60000;  // таблицы зачёта старше минуты перезапрашиваем при показе
const RS_NAV_PX = 96;       // парящее меню внизу экрана с отступом: что под ним, того не видно
const RS_TOP_PX = 44;       // бегущая строка наверху

const RS_I = {
  bell: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/></svg>',
  share: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 4 3 11l6.5 2.5L12 20z"/><path d="m9.5 13.5 4-4"/></svg>',
  again: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4.5 12a7.5 7.5 0 1 0 2.6-5.7"/><path d="M4 5v4h4"/></svg>',
  undo: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 7 4.5 11.5 9 16"/><path d="M5 11.5h9a5 5 0 0 1 0 10h-2"/></svg>',
  hint: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5v2M5.2 6.2l1.4 1.4M18.8 6.2l-1.4 1.4M9.5 20h5"/><path d="M12 8a4 4 0 0 1 2.4 7.2V17h-4.8v-1.8A4 4 0 0 1 12 8Z"/></svg>',
  stop: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M8.5 12h7"/></svg>',
  star: '<svg viewBox="0 0 24 24" aria-hidden="true"><path class="fill" d="m12 2.8 2.7 5.8 6.3.7-4.7 4.3 1.3 6.2L12 16.6l-5.6 3.2 1.3-6.2L3 9.3l6.3-.7z"/></svg>',
  chev: '<svg class="chev" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg>',
  trash: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 7h14M9 7V5h6v2M7 7l1 13h8l1-13"/></svg>',
  close: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  duel: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="8" cy="8.5" r="3"/><circle cx="16" cy="8.5" r="3"/><path d="M2.5 19c.8-3 2.8-4.5 5.5-4.5s4.7 1.5 5.5 4.5M13.6 15c.7-.3 1.5-.5 2.4-.5 2.7 0 4.7 1.5 5.5 4.5"/></svg>',
  // Шайба на острие ленты: чёрный эллипс с белой каймой, как на заставке
  puck: '<svg viewBox="0 0 120 80" aria-hidden="true"><path d="M8 30v18a52 22 0 0 0 104 0V30z" fill="#000" stroke="#fff" stroke-width="9" stroke-linejoin="round" paint-order="stroke"/><ellipse cx="60" cy="30" rx="52" ry="22" fill="#000" stroke="#fff" stroke-width="5"/></svg>',
  // Касание пальца в знакомстве с полем: круг, как у записи экрана
  touch: '<svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="17" class="t-fill"/><circle cx="20" cy="20" r="17" class="t-ring"/></svg>',
};

const rsFreshSrv = () => ({ data: {}, fail: {}, loading: {}, at: {} });
const RS = {
  idx: null, status: "", loading: null,     // index.json: "" — не грузили, ok, none — файлов нет, fail
  date: "", day: null, dayFail: "",         // открытый расклад
  train: false, replay: false,              // прошлый день или раскат дня ещё раз: в зачёт не идут
  seg: "ice", board: "day",                 // сегмент экрана и чип зачёта
  path: [], drag: false, solved: false, hint: false,
  hintCells: [], hintText: "", dead: null,  // подсказка держится до хода, тупик — пока не отъедешь
  say: null, sayT: 0, srPos: "",            // строка над полем: временная реплика и позиция для чтеца
  askHint: false, fin: false, intro: null,  // подтверждение подсказки, финальное табло, знакомство
  t0: 0, ms: 0, tick: 0, res: null, geo: null, el: null, missAt: 0,
  srv: rsFreshSrv(), synced: false, flushing: false, apiProbe: false,
  ask: false, cloudPulled: false, sheetOpen: false,
};

// ---------- режим, время, данные ----------

function rsMockMode() {
  try { return new URLSearchParams(location.search).get("raskat_mock") || ""; } catch (e) { return ""; }
}
function rsDir() {
  const m = rsMockMode();
  if (!m) return "data/raskat/";
  if (m === "none") return "data/raskat/mock/none/";   // такого каталога нет — проверяем пустое состояние
  return "data/raskat/mock/";
}
// Ключи мока отдельные: игра в режиме разработки не портит настоящие записи болельщика
const rsKey = (k) => (rsMockMode() ? `${k}_m_${rsMockMode().replace(/[^a-z0-9]/g, "")}` : k);

function rsApiBase() {
  const a = window.RASKAT_API;
  if (typeof a !== "string") return "";
  // https — в продакшене; http://localhost и 127.0.0.1 — только для прогона на своей машине
  return /^(https:\/\/[^\s"'<>]+|http:\/\/(localhost|127\.0\.0\.1)(:\d+)?(\/[^\s"'<>]*)?)$/.test(a) ? a.replace(/\/+$/, "") : "";
}
const rsMocked = () => typeof window.RASKAT_MOCK_API === "function";
const rsServer = () => rsMocked() || (!!rsApiBase() && inTelegram && !!tg.initData);

// Сервер включили, а Telegram отдал страницу из кэша со старым пустым RASKAT_API: раз за сессию
// спрашиваем свежий index.html и берём адрес оттуда — зачёт откроется без очистки кэша
function rsProbeApi() {
  if (RS.apiProbe || rsApiBase() || rsMockMode() || !inTelegram || !tg.initData) return;
  RS.apiProbe = true;
  fetch("index.html", { cache: "no-cache" })
    .then((r) => (r.ok ? r.text() : ""))
    .then((html) => {
      const m = /window\.RASKAT_API\s*=\s*"([^"]*)"/.exec(html);
      if (!m || !m[1]) return;
      window.RASKAT_API = m[1];
      if (!rsServer()) return;
      RS.srv = rsFreshSrv();
      RS.synced = false;
      rsSync();
      rsFlush();
      if (state.tab === "raskat") rsPaint();
    })
    .catch(() => { /* зачёт подождёт следующего запуска */ });
}

// Время «Раската» — по Москве. В мок-режиме «сейчас» задаёт фикстура
function rsNow() {
  const m = RS.idx && RS.idx._mock_now;
  return m && rsMockMode() ? Date.parse(m) : Date.now();
}
// Дату берём с устройства, но не раньше, чем у сборки: часы на телефоне врут чаще (контракт, раздел 2)
function rsToday() {
  const d = new Intl.DateTimeFormat("en-CA", { timeZone: TZ }).format(new Date(rsNow()));
  const t = RS.idx && RS.idx.today;
  return t && t > d ? t : d;
}
const rsShift = (iso, delta) => new Date(parseISO(iso).getTime() + delta * 864e5).toISOString().slice(0, 10);
const rsList = () => (RS.idx && Array.isArray(RS.idx.days) ? RS.idx.days : []);
// Раскат дня — последний опубликованный день, который уже наступил
function rsLatest() {
  const open = rsList().filter((d) => d.date <= rsToday());
  return open.length ? open[open.length - 1] : rsList()[0] || null;
}
const rsIsToday = (date) => !!rsLatest() && rsLatest().date === date;
const rsDayN = (date) => { const d = rsList().find((x) => x.date === date); return d ? d.n : 0; };

function rsScript(src) {
  return new Promise((done) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = () => done(true);
    s.onerror = () => done(false);
    document.head.appendChild(s);
  });
}
async function rsJson(name) {
  const r = await fetch(rsDir() + name, { cache: "no-cache" });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}
function rsLoad(force = false) {
  if (RS.loading && !force) return RS.loading;
  RS.loading = (async () => {
    try {
      const m = rsMockMode();
      if ((m === "1" || m === "solved") && !rsMocked()) await rsScript("data/raskat/mock/api.js");
      const idx = await rsJson("index.json");
      RS.idx = idx && Array.isArray(idx.days) && idx.days.length ? idx : null;
      RS.status = RS.idx ? "ok" : "none";
      if (!RS.idx) return;
      await rsOpen(rsLatest().date);
      if (m === "solved") rsSeedSolved();
    } catch (e) {
      RS.status = "fail";
      RS.loading = null;
    }
  })();
  return RS.loading;
}
const rsOk = (d) => !!d && Array.isArray(d.dots) && d.dots.length && d.w > 1 && d.h > 1 && d.w * d.h > d.dots.length;

// Открыть расклад дня: поле собирается заново, уже собранный день сразу показывает свою ленту
async function rsOpen(date) {
  RS.dayFail = "";
  let d = null;
  try { d = await rsJson(`${date}.json`); } catch (e) { d = null; }
  if (!rsOk(d)) {
    RS.dayFail = "Не удалось загрузить раскат. Проверь интернет.";
    RS.day = null;
    return;
  }
  d.walls = Array.isArray(d.walls) ? d.walls.filter((w) => /^\d+-\d+$/.test(String(w))) : [];
  d._wall = new Set(d.walls);
  RS.date = date;
  RS.day = d;
  RS.train = !rsIsToday(date);
  RS.replay = false;
  RS.fin = false;
  rsRestart(false);
  // подсказка к раскату дня одна в день; в тренировке — без ограничений, флаг живёт до «Заново»
  RS.hint = !RS.train && lsGet(rsKey(RS_HINT_KEY)) === date;
  const rec = rsRecords()[date];
  if (rec && Array.isArray(rec.path) && !rsCheck(d, rec.path)) {
    RS.path = rec.path.slice();
    RS.solved = true;
    RS.ms = rec.ms || 0;
    RS.res = Object.assign({}, rec, rec.srv || {});
  }
}

// ---------- личные записи, серия, очки ----------

function rsRecords() {
  try { return JSON.parse(lsGet(rsKey(RS_DAYS_KEY)) || "{}") || {}; } catch (e) { return {}; }
}
function rsSaveRecords(all) {
  const v = JSON.stringify(all);
  lsSet(rsKey(RS_DAYS_KEY), v);
  if (cloud() && !rsMockMode()) cloud().setItem(RS_DAYS_KEY, v, () => {});
}
// Записи с другого устройства — из облака Telegram: берём дни, которых здесь нет
function rsCloudPull() {
  if (RS.cloudPulled || !cloud() || rsMockMode()) return;
  RS.cloudPulled = true;
  cloud().getItem(RS_DAYS_KEY, (err, v) => {
    if (err || !v) return;
    let far = {};
    try { far = JSON.parse(v) || {}; } catch (e) { return; }
    const mine = rsRecords();
    let changed = false;
    for (const [date, rec] of Object.entries(far)) {
      if (!mine[date] && rec && typeof rec.ms === "number") { mine[date] = rec; changed = true; }
      // отправлено с другого устройства — здесь не шлём второй раз
      else if (mine[date] && rec && rec.sent && !mine[date].sent) { mine[date].sent = rec.sent; mine[date].srv = rec.srv; changed = true; }
    }
    if (!changed) return;
    lsSet(rsKey(RS_DAYS_KEY), JSON.stringify(mine));
    rsFlush();
    if (RS.date && mine[RS.date] && !RS.solved) rsOpen(RS.date).then(rsPaint);
    else rsPaint();
  });
}
function rsSettings() {
  try { return Object.assign({ name: false, messages: true }, JSON.parse(lsGet(rsKey(RS_SET_KEY)) || "{}")); } catch (e) { return { name: false, messages: true }; }
}
function rsSetSetting(k, v, push = true) {
  const s = rsSettings();
  s[k] = v;
  lsSet(rsKey(RS_SET_KEY), JSON.stringify(s));
  if (push && rsServer()) rsApi("PUT", "/settings", rsClubField({ show_tg_name: !!s.name, messages: !!s.messages })).catch(() => {});
}
// Клуб болельщика — любимая команда: по нему сервер считает кубок клубов (контракт, раздел 5)
function rsClubField(body) {
  return state.fav && state.teams[state.fav] ? Object.assign(body, { club: state.fav }) : body;
}

// Серия — сколько дней подряд собран раскат, считая от date назад. Пропущенный день обнуляет серию,
// тренировка прошлого дня её не восстанавливает (контракт, раздел 4)
function rsStreak(date) {
  const all = rsRecords();
  let n = 0;
  let d = date;
  while (d && all[d] && !all[d].train && n < 400) { n += 1; d = rsShift(d, -1); }
  return n;
}
// Итог дня: очки дня плюс серия. streak — дни серии ДО сегодняшнего (контракт, раздел 4)
function rsPoints(par, seconds, streak = 0, hint = false) {
  const clamp = (v) => Math.min(1, Math.max(0, v));
  const time = RS_TIME * clamp((3 * par - seconds) / (2.5 * par));
  const run = Math.min(RS_RUN, 2 * Math.max(0, streak));
  return Math.max(0, Math.round(RS_BASE + time + run + (hint ? -RS_HINT_COST : 0)));
}
// Очки дня — то, по чему ранжируется зачёт дня: серии в них нет
const rsDayPoints = (par, seconds, hint = false) => rsPoints(par, seconds, 0, hint);
const rsPts = (n) => `${n} ${plural(n, "очко", "очка", "очков")}`;
const rsDays = (n) => `${n} ${plural(n, "день", "дня", "дней")}`;
const rsCellsWord = (n) => `${n} ${plural(n, "клетка", "клетки", "клеток")}`;
function rsClock(ms) {
  const s = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
// Раскат дня уже собран — в мок-режиме =solved это задано фикстурой
function rsSeedSolved() {
  if (!RS.day || RS.solved) return;
  const sol = rsSolve(RS.day, [RS.day.dots[0]]);
  if (!sol) return;
  RS.path = sol;
  RS.solved = true;
  RS.ms = 61200;
  const all = rsRecords();
  const before = rsStreak(rsShift(RS.date, -1));
  const points = rsDayPoints(RS.day.par, RS.ms / 1000, false);
  all[RS.date] = RS.res = { ms: RS.ms, points, total: rsPoints(RS.day.par, RS.ms / 1000, before, false), hint: false, path: sol, streak: before + 1 };
  rsSaveRecords(all);
  lsSet(rsKey(RS_WOW_KEY), RS.date);
  lsSet(rsKey(RS_INTRO_KEY), "1");
}

// ---------- поле: правила, проверка, решатель ----------

const rsCells = (d) => d.w * d.h;
const rsWallKey = (a, b) => `${Math.min(a, b)}-${Math.max(a, b)}`;
// Соседи — четыре клетки по стороне, без диагоналей (контракт, раздел 1)
function rsSide(a, b, d) {
  const w = d.w;
  if (a === b || a < 0 || b < 0 || a >= rsCells(d) || b >= rsCells(d)) return false;
  if (Math.abs(a - b) === w) return true;
  return Math.abs(a - b) === 1 && Math.floor(a / w) === Math.floor(b / w);
}
const rsWalled = (a, b, d) => d._wall.has(rsWallKey(a, b));
const rsOpenEdge = (a, b, d) => rsSide(a, b, d) && !rsWalled(a, b, d);
const rsNear = (i, d = RS.day) => [i - d.w, i + d.w, i - 1, i + 1].filter((j) => rsOpenEdge(i, j, d));
// Сколько номеров уже задето по порядку
function rsPassed(path = RS.path, d = RS.day) {
  let n = 0;
  for (const c of path) if (d.dots[n] === c) n += 1;
  return n;
}
const rsNumOf = (i, d = RS.day) => d.dots.indexOf(i) + 1;

// Проверка пути по правилам контракта, раздел 1. Причина отказа — по-русски
function rsCheck(d, path) {
  const n = rsCells(d);
  if (!Array.isArray(path) || path.length !== n) return "Шайба прошла не весь лёд.";
  if (new Set(path).size !== n) return "В одну клетку шайба заходит только раз.";
  for (let i = 1; i < path.length; i += 1) {
    if (!rsSide(path[i - 1], path[i], d)) return "Ход только в клетку по стороне.";
    if (rsWalled(path[i - 1], path[i], d)) return "Между клетками борт.";
  }
  let need = 0;
  for (const c of path) {
    const k = d.dots.indexOf(c);
    if (k < 0) continue;
    if (k !== need) return `Номера задевают по порядку: сначала ${need + 1}.`;
    need += 1;
  }
  if (need !== d.dots.length) return "Задеты не все номера.";
  return "";
}

// Решатель для подсказки и тупика: достраивает путь от нынешнего хвоста. Решения в данных нет и
// быть не должно (контракт, раздел 2), поэтому считаем сами — поле маленькое, с отсечениями это
// десятки миллисекунд. cut — перебор не уложился в бюджет: «решения нет» тогда не доказано
function rsSolveX(d, prefix) {
  const n = rsCells(d);
  const adj = [];
  for (let i = 0; i < n; i += 1) adj.push(rsNear(i, d));
  const seen = new Array(n).fill(false);
  const path = [];
  for (const c of prefix) {
    if (c == null || c < 0 || c >= n || seen[c]) return { path: null, cut: false };
    if (path.length && !rsOpenEdge(path[path.length - 1], c, d)) return { path: null, cut: false };
    seen[c] = true;
    path.push(c);
  }
  if (!path.length) return { path: null, cut: false };
  let need = rsPassed(path, d);
  let budget = 400000;
  // Остаток должен быть связным и доступным от головы — иначе дальше идти незачем
  const reach = (head) => {
    const rest = n - path.length;
    if (!rest) return true;
    const from = adj[head].find((j) => !seen[j]);
    if (from === undefined) return false;
    const stack = [from];
    const got = new Set([from]);
    while (stack.length) {
      const c = stack.pop();
      for (const j of adj[c]) if (!seen[j] && !got.has(j)) { got.add(j); stack.push(j); }
    }
    return got.size === rest;
  };
  const walk = () => {
    if (budget-- < 0) return null;
    if (path.length === n) return need === d.dots.length ? path.slice() : null;
    const head = path[path.length - 1];
    if (!reach(head)) return null;
    for (const j of adj[head]) {
      if (seen[j]) continue;
      const k = d.dots.indexOf(j);
      if (k >= 0 && k !== need) continue;
      seen[j] = true;
      path.push(j);
      if (k >= 0) need += 1;
      const got = walk();
      if (got) return got;
      if (k >= 0) need -= 1;
      path.pop();
      seen[j] = false;
    }
    return null;
  };
  const got = walk();
  return { path: got, cut: !got && budget < 0 };
}
const rsSolve = (d, prefix) => rsSolveX(d, prefix).path;

// Тупик, который видно глазом: шайбе некуда ехать, часть льда отрезана, клетка осталась без выхода,
// последний номер задет раньше времени. Глубокий тупик (видит только решатель) показывает подсказка
function rsDeadCheck() {
  const d = RS.day;
  const n = rsCells(d);
  const path = RS.path;
  if (!d || RS.solved || path.length < 2 || path.length >= n) return null;
  const seen = new Set(path);
  const head = path[path.length - 1];
  const k = d.dots.length;
  const passed = rsPassed();
  const free = [];
  for (let i = 0; i < n; i += 1) if (!seen.has(i)) free.push(i);
  if (passed === k) {
    return { text: `Рано к ${k} — он последний. Нажми «Назад»`, cut: free };
  }
  const moves = rsNear(head).filter((j) => !seen.has(j));
  if (!moves.some((j) => { const m = d.dots.indexOf(j); return m < 0 || m === passed; })) {
    const num = moves.map((j) => rsNumOf(j)).find(Boolean);
    return {
      text: num ? `Сначала ${passed + 1} — отсюда не проехать. «Назад»`
        : "Тупик: шайбе некуда ехать. Нажми «Назад»",
      cut: [],
    };
  }
  const got = new Set();
  const stack = [head];
  while (stack.length) {
    const c = stack.pop();
    for (const j of rsNear(c)) if (!seen.has(j) && !got.has(j)) { got.add(j); stack.push(j); }
  }
  const cut = free.filter((i) => !got.has(i));
  if (cut.length) return { text: "Тупик: часть льда отрезана. Нажми «Назад»", cut };
  if (free.length > 1) {
    // клетка с одним выходом может быть только концом пути, а конец — последний номер (контракт, раздел 1)
    const ends = free.filter((c) => rsNear(c).filter((j) => !seen.has(j) || j === head).length <= 1);
    const bad = ends.filter((c) => c !== d.dots[k - 1]);
    if (bad.length) return { text: "Тупик: клетка осталась без выхода. Нажми «Назад»", cut: bad };
  }
  return null;
}

// ---------- строка над полем ----------

// Что сказать сейчас: временная реплика (промах) живёт RS_SAY_MS, остальное следует из положения шайбы
function rsLineNow() {
  const d = RS.day;
  if (!d) return { text: "" };
  if (RS.intro && RS.intro.say) return RS.intro.say;
  if (RS.say && (!RS.say.until || RS.say.until > Date.now())) return RS.say;
  if (RS.solved) {
    const r = RS.res || {};
    const pts = r.total != null ? r.total : r.points || 0;
    if (RS.replay) return { text: `Собран ещё раз за ${rsClock(RS.ms)} — в зачёте первый раскат`, tone: "done" };
    if (RS.train) return { text: `Тренировка собрана за ${rsClock(RS.ms)} — ${rsPts(pts)}`, tone: "done" };
    return { text: `Раскат собран за ${rsClock(r.ms || RS.ms)} — ${rsPts(pts)}`, tone: "done" };
  }
  if (RS.dead) return { text: RS.dead.text, tone: "dead" };
  if (RS.hintCells.length) return { text: RS.hintText, tone: "hint" };
  const k = d.dots.length;
  const next = rsPassed() + 1;
  const left = rsCells(d) - RS.path.length;
  if (RS.path.length === 1) return { text: `Веди шайбу от номера 1 к ${Math.min(2, k)} — весь лёд по разу` };
  const m = rsNumOf(RS.path[RS.path.length - 1]);
  if (m && m === next - 1 && next <= k) return { text: `Номер ${m} есть! Теперь к ${next}` };
  if (next > k) return { text: `Все номера задеты — осталось ${rsCellsWord(left)}` };
  return { text: `К номеру ${next} · ещё ${rsCellsWord(left)}` };
}
function rsLine() {
  const el = $("#rs-say");
  if (!el) return;
  const s = rsLineNow();
  const icon = { hint: RS_I.hint, dead: RS_I.stop, done: RS_I.star }[s.tone] || "";
  const sr = s.sr != null ? s.sr : RS.srPos;
  el.className = `rs-say${s.tone ? ` ${s.tone}` : ""}`;
  el.innerHTML = `${icon}<span>${s.html || esc(s.text)}${sr ? `<span class="sr-only">. ${esc(sr)}</span>` : ""}</span>`;
}
// Реплика на время: промах, отказ, ответ на нажатие. Тон miss — строка вздрагивает
function rsTell(text, tone = "", hold = RS_SAY_MS) {
  RS.say = { text, tone, sr: "", until: hold ? Date.now() + hold : 0 };
  RS.srPos = "";
  clearTimeout(RS.sayT);
  if (hold) RS.sayT = setTimeout(() => { RS.say = null; rsLine(); }, hold + 20);
  rsLine();
  const el = $("#rs-say");
  if (el && tone === "miss" && !calm()) {
    el.animate([{ transform: "none" }, { transform: "translateX(3px)" }, { transform: "translateX(-3px)" }, { transform: "none" }], { duration: 220, easing: EASE_OUT });
  }
}

// ---------- ведение ----------

function rsRestart(repaint = true) {
  if (repaint && RS.solved && !RS.train) RS.replay = true;   // раскат дня ещё раз: в зачёте остаётся первый
  if (repaint && RS.train) RS.hint = false;                   // в тренировке подсказка — до «Заново»
  RS.path = RS.day ? [RS.day.dots[0]] : [];
  RS.solved = false;
  RS.hintCells = [];
  RS.dead = null;
  RS.say = null;
  RS.srPos = "";
  RS.askHint = false;
  RS.fin = false;
  RS.t0 = 0;
  RS.ms = 0;
  RS.res = null;
  RS.drag = false;
  rsStopTick();
  if (repaint) {
    rsPaintBody();
    rsTell(RS.replay ? "Поле заново. В зачёте останется первый раскат" : "Поле заново — шайба снова на номере 1");
  }
}
// Почему в эту клетку нельзя: "" — можно
function rsWhy(from, to) {
  const d = RS.day;
  if (!rsSide(from, to, d)) return "far";
  if (rsWalled(from, to, d)) return "wall";
  if (RS.path.includes(to)) return "busy";
  const k = d.dots.indexOf(to);
  if (k >= 0 && k !== rsPassed()) return "order";
  return "";
}
const RS_MISS = {
  wall: "Борт — сюда не пройти",
  busy: "Клетка уже пройдена",
  far: "Ход — только в соседнюю клетку",
};
// Попытка пройти борт или занятую клетку: вздрагивание, лёгкий отклик и строка — почему.
// Ошибки как состояния нет, счётчика ошибок — тоже
function rsMiss(why, from, to) {
  if (!RS.el) return;
  const now = Date.now();
  if (now - RS.missAt < 220) return;
  RS.missAt = now;
  const el = why === "wall" ? RS.el.walls.querySelector(`[data-w="${rsWallKey(from, to)}"]`) : why === "far" ? null : RS.el.ribbon;
  if (el && !calm()) el.animate([{ transform: "scale(1)" }, { transform: "scale(1.04)" }, { transform: "scale(1)" }], { duration: 200, easing: EASE_OUT });
  if (inTelegram && tg.HapticFeedback) tg.HapticFeedback.impactOccurred("light");
  rsTell(why === "order" ? `Номера по порядку: сначала ${rsPassed() + 1}` : RS_MISS[why] || "", "miss");
}
// Шаг шайбы. Возврат на предыдущую клетку снимает хвост — отмена тем же движением назад
function rsStep(to) {
  if (!RS.day || RS.solved || to < 0) return false;
  const head = RS.path[RS.path.length - 1];
  if (to === head) return false;
  if (RS.path.length > 1 && to === RS.path[RS.path.length - 2]) {
    RS.path.pop();
    RS.hintCells = [];
    rsMoved(`Шаг назад, ${rsWhere(to)}`);
    return true;
  }
  const why = rsWhy(head, to);
  if (why) {
    if (why !== "far") rsMiss(why, head, to);
    return false;
  }
  RS.path.push(to);
  // подсказка держится, пока едешь по ней; свернул — гаснет
  if (RS.hintCells[0] === to) RS.hintCells.shift();
  else RS.hintCells = [];
  const num = rsNumOf(to);
  rsMoved(`${rsWhere(to)}${num ? `, номер ${num}` : ""}. Пройдено ${RS.path.length} из ${rsCells(RS.day)}`);
  return true;
}
function rsBack() {
  if (!RS.day || RS.solved || RS.path.length < 2) return;
  rsStep(RS.path[RS.path.length - 2]);
}
const rsWhere = (i) => `клетка ${(i % RS.day.w) + 1} по горизонтали, ${Math.floor(i / RS.day.w) + 1} по вертикали`;
const rsWhereIn = (i) => rsWhere(i).replace("клетка", "клетке");
function rsMoved(sr) {
  if (!RS.t0) {
    RS.t0 = Date.now();   // время идёт от первого касания поля, без пауз (контракт, раздел 4)
    rsStartTick();
  }
  if (RS.askHint) { RS.askHint = false; rsAskPaint(); }
  if (RS.intro) rsIntroStop(true);   // палец уже повёл шайбу — знакомство больше не нужно
  const wasDead = !!RS.dead;
  RS.dead = rsDeadCheck();
  if (RS.dead && !wasDead && inTelegram && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("warning");
  RS.say = null;
  RS.srPos = sr;
  rsDraw();
  rsLine();
  rsActsSync();
  if (RS.path.length === rsCells(RS.day)) rsFinish();
}
// Соседняя клетка в сторону цели: палец мог проехать несколько клеток за один кадр
function rsCands(a, b) {
  const w = RS.day.w;
  const dx = (b % w) - (a % w);
  const dy = Math.floor(b / w) - Math.floor(a / w);
  const byX = dx ? a + Math.sign(dx) : -1;
  const byY = dy ? a + Math.sign(dy) * w : -1;
  const list = Math.abs(dx) >= Math.abs(dy) ? [byX, byY] : [byY, byX];
  return list.filter((i) => i >= 0);
}
function rsTowards(to) {
  let guard = 0;
  while (to >= 0 && to !== RS.path[RS.path.length - 1] && guard < 72) {
    guard += 1;
    const head = RS.path[RS.path.length - 1];
    const cands = rsCands(head, to);
    const prev = RS.path.length > 1 ? RS.path[RS.path.length - 2] : -1;
    const back = cands.find((c) => c === prev);
    const ok = back !== undefined ? back : cands.find((c) => !rsWhy(head, c));
    if (ok === undefined) {
      const why = cands.length ? rsWhy(head, cands[0]) : "far";
      if (why && why !== "far") rsMiss(why, head, cands[0]);
      return;
    }
    if (!rsStep(ok)) return;
  }
}

// Клетка под пальцем: зазор между клетками считаем попаданием в ближайшую
function rsCellAt(e) {
  const g = RS.geo;
  if (!g || !RS.el) return -1;
  const b = RS.el.board.getBoundingClientRect();
  const step = g.cell + g.gap;
  const x = Math.floor((e.clientX - b.left) / step);
  const y = Math.floor((e.clientY - b.top) / step);
  if (x < 0 || y < 0 || x >= RS.day.w || y >= RS.day.h) return -1;
  return y * RS.day.w + x;
}
function rsDown(e) {
  if (!RS.day || RS.solved || e.button > 0) return;
  if (RS.intro) rsIntroStop(true);   // знакомство пропускается касанием поля, и ведение сразу начинается
  const i = rsCellAt(e);
  if (i < 0) return;
  const head = RS.path[RS.path.length - 1];
  e.preventDefault();
  RS.el.board.focus({ preventScroll: true });
  if (i !== head) {
    const at = RS.path.indexOf(i);
    if (at >= 0) {
      // касание своей ленты — шайба возвращается в эту клетку, хвост снимается: дальше веди отсюда
      RS.path.length = at + 1;
      RS.hintCells = [];
      rsMoved(`Вернулись в ${rsWhereIn(i)}. Пройдено ${RS.path.length} из ${rsCells(RS.day)}`);
      if (inTelegram && tg.HapticFeedback) tg.HapticFeedback.impactOccurred("light");
    } else if (rsWhy(head, i) === "far") {
      rsMiss("far", head, i);
      return;
    } else if (!rsStep(i)) {
      return;
    }
  }
  RS.drag = true;
  try { RS.el.board.setPointerCapture(e.pointerId); } catch (err) { /* мышь без захвата */ }
}
function rsPointerMove(e) {
  if (!RS.drag || RS.solved) return;
  e.preventDefault();
  rsTowards(rsCellAt(e));
}
function rsUp(e) {
  if (!RS.drag) return;
  RS.drag = false;
  try { RS.el.board.releasePointerCapture(e.pointerId); } catch (err) { /* уже отпущен */ }
}

// ---------- собранный раскат ----------

function rsFinish() {
  const bad = rsCheck(RS.day, RS.path);
  if (bad) {
    RS.dead = { text: /^Номера/.test(bad) ? "Номера не по порядку — не засчитано. «Назад»" : `Не засчитано: ${bad} «Назад»`, cut: [] };
    rsLine();
    rsActsSync();
    return;
  }
  RS.solved = true;
  RS.drag = false;
  RS.dead = null;
  RS.hintCells = [];
  RS.ms = RS.t0 ? Date.now() - RS.t0 : RS.ms;
  rsStopTick();
  const all = rsRecords();
  const was = all[RS.date];
  const before = rsStreak(rsShift(RS.date, -1));   // дни серии до сегодняшнего — их и ждёт формула
  const streak = RS.train ? 0 : before + 1;        // с сегодняшним: эту серию видит болельщик
  const points = rsDayPoints(RS.day.par, RS.ms / 1000, RS.hint);   // очки дня: по ним зачёт
  const total = RS.train || RS.replay ? points : rsPoints(RS.day.par, RS.ms / 1000, before, RS.hint);
  if (RS.replay && was) {
    // раскат дня ещё раз: в зачёт идёт первый собранный, запись дня не трогаем
    RS.res = Object.assign({ streak }, was, was.srv || {}, { again: { ms: RS.ms, points } });
  } else {
    const rec = { ms: RS.ms, points, total, hint: RS.hint, path: RS.path.slice() };
    if (RS.train) rec.train = true;
    else rec.streak = streak;
    // тренировку храним лучшим временем, раскат дня — первым собранным: он идёт в зачёт один раз
    if (!was || (RS.train && RS.ms < was.ms)) all[RS.date] = rec;
    rsSaveRecords(all);
    RS.res = Object.assign({ streak }, rec);
  }
  RS.srPos = "";
  if (inTelegram && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
  rsDraw();
  rsLine();
  rsActsSync();
  rsAfterPaint();
  rsCelebrate();
  rsDue();
  if (!RS.train && !RS.replay) rsFlush();
}
// Результат дня уходит на сервер один раз; тренировка не уходит вовсе (контракт, раздел 5).
// Собрал до включения сервера или без связи — уйдёт при следующем открытии, пока день тот же
function rsFlush() {
  if (!rsServer() || RS.flushing) return;
  const day = rsLatest();
  if (!day) return;
  const all = rsRecords();
  const rec = all[day.date];
  if (!rec || rec.train || rec.sent || !Array.isArray(rec.path) || typeof rec.ms !== "number") return;
  RS.flushing = true;
  rsApi("POST", `/day/${day.date}`, rsClubField({ path: rec.path, ms: Math.round(rec.ms), hint: !!rec.hint }))
    .then((r) => rsSent(day.date, r))
    .catch((e) => {
      // 409 — результат дня уже принят (с другого устройства): сервер отдаёт прежний Result
      if (e.status === 409 && e.data && e.data.points != null) return rsSent(day.date, e.data);
      // 400 — день закрыт или путь не прошёл проверку: второй раз не шлём, причину показываем
      if (e.status === 400) return rsSent(day.date, null, e.message);
      return undefined;   // нет связи, вход устарел, зачёт выключен — попробуем при следующем открытии
    })
    .then(() => { RS.flushing = false; }, () => { RS.flushing = false; });
}
function rsSent(date, r, err = "") {
  const all = rsRecords();
  const rec = all[date];
  if (!rec) return;
  const srv = r ? { points: r.points, total: r.total, ms: r.ms, hint: r.hint, place: r.place, of: r.of, streak: r.streak } : null;
  rec.sent = r ? 1 : "no";
  if (srv) rec.srv = srv;
  if (err) rec.srvErr = err;
  rsSaveRecords(all);
  ["day", "clubs", "me"].forEach((k) => rsSrvDrop(k));
  if (RS.date === date && RS.solved && !RS.replay) {
    RS.res = Object.assign({}, RS.res, srv || {}, err ? { srvErr: err } : {});
    rsAfterPaint();
    rsFinPaint();
    rsLine();
  }
  if (state.tab === "raskat" && RS.seg === "board") rsPaintBody();
}
function rsAfterPaint() {
  const score = $("#rs-score");
  const cta = $("#rs-cta");
  if (score) score.innerHTML = RS.solved ? rsScoreHTML() : "";
  if (cta) cta.innerHTML = rsCtaHTML();
}
// Финал: лента пробегает слева направо, шайба влетает в ворота, поверх поля встаёт табло.
// «Вау» — один раз в день и только у раската дня; тренировка и повтор — табло без праздника
function rsCelebrate() {
  const day = !RS.train && !RS.replay;
  const wow = day && lsGet(rsKey(RS_WOW_KEY)) !== RS.date && !calm();
  if (day) lsSet(rsKey(RS_WOW_KEY), RS.date);
  RS.fin = true;
  if (!wow) {
    rsGoal(!calm());
    if (calm()) return rsFinShow(false);
    return void setTimeout(() => { if (RS.fin && RS.solved) rsFinShow(true); }, 420);
  }
  const trail = RS.el && RS.el.trail;
  if (trail) {
    trail.forEach((p) => {
      const len = p.getTotalLength();
      p.animate([{ strokeDasharray: `${len} ${len}`, strokeDashoffset: len }, { strokeDasharray: `${len} ${len}`, strokeDashoffset: 0 }],
        { duration: 600, easing: EASE_OUT });
    });
  }
  setTimeout(() => rsGoal(true), 560);
  setTimeout(() => { if (RS.fin && RS.solved) rsFinShow(true); }, 900);
}

// Шайба в воротах у края поля: так выглядит собранный раскат — и сразу после финиша, и при
// следующем заходе в этот день
function rsGoal(animate) {
  if (!RS.el || !RS.geo) return;
  RS.el.goal.classList.add("on");
  RS.el.puck.style.transition = animate ? "transform .34s cubic-bezier(.4, 0, 1, 1)" : "none";
  RS.el.puck.style.transform = `translate(${RS.geo.w + 5}px, ${RS.geo.h / 2}px) translate(-50%, -50%)`;
}

// ---------- финальное табло поверх поля ----------

function rsFinFacts() {
  const r = RS.res || {};
  const facts = [];
  if (RS.train) {
    const best = rsRecords()[RS.date];
    facts.push(["лучшее", rsClock(best ? Math.min(best.ms, RS.ms) : RS.ms)], ["в зачёт", "не идёт"], ["норма", rsClock(RS.day.par * 1000)]);
    return facts;
  }
  if (RS.replay) {
    facts.push(["сейчас", rsClock(RS.ms)], ["в зачёте", rsClock(r.ms || 0)], ["очки дня", String(r.points != null ? r.points : 0)]);
    return facts;
  }
  const streak = r.streak || rsStreak(RS.date);
  const bonus = Math.min(RS_RUN, 2 * Math.max(0, streak - 1));
  facts.push(["очки дня", String(r.points != null ? r.points : 0)]);
  facts.push(["серия", `${rsDays(streak)}${bonus ? ` · +${bonus}` : ""}`]);
  if (rsServer()) facts.push(["место", r.place ? `${r.place} из ${r.of || r.place}` : r.srvErr ? "не принят" : "считаем…"]);
  else facts.push(["норма", rsClock(RS.day.par * 1000)]);
  return facts;
}
function rsFinHTML() {
  const r = RS.res || {};
  const big = RS.replay ? (r.again && r.again.points) || 0 : r.total != null ? r.total : r.points || 0;
  const title = RS.train ? "Тренировка собрана" : RS.replay ? "Собран ещё раз" : "Раскат собран!";
  const facts = rsFinFacts().map(([k, v]) => `<div><small>${esc(k)}</small><b>${esc(v)}</b></div>`).join("");
  const note = [r.hint ? `с подсказкой · −${RS_HINT_COST}` : "", !RS.train && !RS.replay && r.srvErr ? r.srvErr : ""].filter(Boolean).join(" · ");
  const todayOpen = !rsRecords()[(rsLatest() || {}).date];
  let main;
  let more;
  if (RS.train) {
    main = todayOpen ? '<button type="button" class="btn" data-rs="today">К раскату дня</button>'
      : `<button type="button" class="btn" data-rs="share">${RS_I.share}Поделиться</button>`;
    more = '<button type="button" class="rs-pill" data-rs="train">Другой день</button>';
  } else if (RS.replay) {
    main = '<button type="button" class="btn" data-rs="to-board">Зачёт дня</button>';
    more = '<button type="button" class="rs-pill" data-rs="train">Тренировка</button>';
  } else {
    main = `<button type="button" class="btn" data-rs="share">${RS_I.share}Поделиться</button>`;
    more = `<button type="button" class="rs-pill" data-rs="to-board">Зачёт дня</button>
      <button type="button" class="rs-pill" data-rs="train" aria-label="Тренировка прошлых дней">Тренировка</button>`;
  }
  return `<div class="rs-fin-veil" data-rs="fin-close" aria-hidden="true"></div>
    <div class="rs-fin-card${RS.train || RS.replay ? " quiet" : ""}" tabindex="-1" role="group" aria-labelledby="rs-fin-h">
      <div class="rs-fin-head"><h2 id="rs-fin-h">${title}</h2>
        <button type="button" class="btn-round" data-rs="fin-close" aria-label="Закрыть табло и посмотреть путь">${RS_I.close}</button></div>
      <div class="rs-fin-nums">
        <div class="rs-time"><b class="num">${esc(rsClock(RS.replay ? RS.ms : r.ms || RS.ms))}</b><small>время</small></div>
        <div class="rs-got"><b class="num">${esc(String(big))}</b><small>${RS.train || RS.replay ? plural(big, "очко", "очка", "очков") : "итог дня"}</small></div>
      </div>
      <div class="rs-fin-facts">${facts}</div>
      ${note ? `<p class="rs-fin-note">${esc(note)}</p>` : ""}
      <div class="rs-fin-main">${main}</div>
      <div class="rs-fin-more">${more}</div>
      <button type="button" class="rs-link rs-fin-path" data-rs="fin-close">Посмотреть свой путь</button>
    </div>`;
}
function rsFinShow(animate) {
  const box = $("#rs-fin");
  if (!box || !RS.fin) return;
  box.innerHTML = rsFinHTML();
  box.hidden = false;
  const card = box.querySelector(".rs-fin-card");
  if (animate && !calm()) {
    box.firstElementChild.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 200, easing: EASE_OUT });
    card.animate([{ opacity: 0, transform: "translateY(12px) scale(.94)" }, { opacity: 1, transform: "none" }],
      { duration: 320, easing: "cubic-bezier(.3, 1.4, .5, 1)" });
  }
  card.focus({ preventScroll: true });
  // табло — на первом экране: поле уже на виду, но если болельщик прокрутил, подвезём
  const r = box.getBoundingClientRect();
  if (r.top < 0 || r.bottom > window.innerHeight) box.scrollIntoView({ block: "nearest", behavior: calm() ? "auto" : "smooth" });
}
// Табло перерисовать на месте, когда ответил сервер: место и итог
function rsFinPaint() {
  const box = $("#rs-fin");
  if (!box || box.hidden || !RS.fin) return;
  const had = box.contains(document.activeElement);
  box.innerHTML = rsFinHTML();
  if (had) box.querySelector(".rs-fin-card").focus({ preventScroll: true });
}
function rsFinClose() {
  RS.fin = false;
  const box = $("#rs-fin");
  if (box) {
    box.hidden = true;
    box.innerHTML = "";
  }
  if (RS.el) RS.el.board.focus({ preventScroll: true });
}

// ---------- подсказка ----------

// Направление словами: «вниз на 2 клетки, потом вправо»
function rsDirs(from, cells) {
  const w = RS.day.w;
  const name = (a, b) => (b - a === 1 ? "вправо" : b - a === -1 ? "влево" : b - a === w ? "вниз" : "вверх");
  const runs = [];
  let prev = from;
  for (const c of cells) {
    const s = name(prev, c);
    if (runs.length && runs[runs.length - 1].s === s) runs[runs.length - 1].n += 1;
    else runs.push({ s, n: 1 });
    prev = c;
  }
  return runs.map((x) => (x.n > 1 ? `${x.s} на ${x.n}` : x.s)).join(", ");
}
function rsHintPress() {
  if (!RS.day || RS.solved) return;
  if (RS.intro) rsIntroStop(true);
  if (RS.train) return rsHintShow();   // тренировка — без ограничений и без цены
  if (RS.hint && !RS.hintCells.length) {
    return rsTell("Подсказка к раскату дня одна — она уже была", "miss", 3200);
  }
  if (RS.hintCells.length) return rsTell(RS.hintText, "hint", 1200);
  RS.askHint = !RS.askHint;
  rsAskPaint();
}
// Подсказка показывает следующие клетки следом и словами и держится до хода болельщика.
// Отсюда весь лёд не пройти — шайба возвращается туда, откуда раскат собирается, и дальше тот же след
function rsHintShow() {
  const d = RS.day;
  let r = rsSolveX(d, RS.path);
  let back = 0;
  if (!r.path) {
    let lo = 1;                  // путь из одного номера 1 всегда собирается: так строит генератор
    let hi = RS.path.length;     // а весь нынешний путь — нет
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (rsSolveX(d, RS.path.slice(0, mid)).path) lo = mid;
      else hi = mid;
    }
    back = RS.path.length - lo;
    RS.path.length = lo;
    r = rsSolveX(d, RS.path);
  }
  if (!r.path) return rsTell("Подсказка не нашла путь отсюда — нажми «Заново»", "dead", 3200);
  if (!RS.train) {
    RS.hint = true;
    lsSet(rsKey(RS_HINT_KEY), RS.date);
  }
  RS.askHint = false;
  rsAskPaint();
  const head = RS.path[RS.path.length - 1];
  RS.hintCells = r.path.slice(RS.path.length, RS.path.length + RS_HINT_CELLS);
  const dirs = rsDirs(head, RS.hintCells);
  RS.hintText = back
    ? `Тупик — назад на ${back} ${plural(back, "ход", "хода", "ходов")}. Дальше ${dirs}`
    : `Подсказка: ${dirs}`;
  if (!RS.t0) { RS.t0 = Date.now(); rsStartTick(); }
  RS.dead = null;
  RS.say = null;
  RS.srPos = "";
  rsDraw();
  rsLine();
  rsActsSync();
  rsGhostIn();
  rsLineInView();
}
function rsAskHTML() {
  return `<div class="rs-ask" role="group" aria-label="Подсказка">
    <p>Подсказка стоит <b>${RS_HINT_COST} очков дня</b></p>
    <div class="rs-ask-btns"><button type="button" class="rs-pill" data-rs="hint-no">Не надо</button>
    <button type="button" class="rs-pill hot" data-rs="hint-yes">${RS_I.hint}Показать</button></div></div>`;
}
function rsAskPaint() {
  const box = $("#rs-ask");
  if (!box) return;
  box.innerHTML = RS.askHint ? rsAskHTML() : "";
  if (!RS.askHint) return;
  const yes = box.querySelector('[data-rs="hint-yes"]');
  if (yes) yes.focus({ preventScroll: true });
  // строку подтверждения не прячет меню внизу, но и строку над полем не уводим с экрана
  rsScrollBy(box.getBoundingClientRect().bottom - (window.innerHeight - RS_NAV_PX));
}
function rsScrollBy(dy) {
  if (dy > 0) window.scrollBy({ top: dy, behavior: calm() ? "auto" : "smooth" });
}
// Строка над полем — на экране: после подсказки болельщик читает её, а не ищет
function rsLineInView() {
  const el = $("#rs-say");
  if (!el) return;
  const top = el.getBoundingClientRect().top - RS_TOP_PX - 8;
  if (top < 0) window.scrollBy({ top, behavior: calm() ? "auto" : "smooth" });
}

// ---------- знакомство с полем ----------

// Один раз на устройстве, повторить — из листа «Как собрать». Три шага с подписями в строке над полем:
// палец ведёт шайбу с номера 1 в его единственный выход, номера по порядку подсвечиваются, борта
// вздрагивают. Касание поля или «Пропустить» — знакомство кончается, и раскат идёт как обычно
const RS_INTRO = [
  () => "Веди шайбу пальцем от номера 1",
  (k) => `Номера — по порядку: 1, 2${k > 2 ? ", 3" : ""}… до ${k}`,
  () => "Весь лёд по разу, через борт нельзя. Вперёд!",
];
function rsIntroMaybe() {
  if (RS.intro || !RS.day || RS.solved || RS.drag || RS.seg !== "ice" || RS.path.length > 1 || RS.t0) return;
  if (lsGet(rsKey(RS_INTRO_KEY)) === "1" || !RS.el || !RS.geo) return;
  if ($("#tour") || !$("#sheet").hidden) return;   // тур приложения или лист — не поверх них
  rsIntroStart();
}
function rsIntroStart() {
  if (!RS.el || !RS.geo || !RS.day) return;
  rsIntroStop(false);
  RS.intro = { step: -1, timers: [], anims: [] };
  RS.el.board.classList.add("intro");
  const skip = $("#rs-skip");
  const timer = $("#rs-timer");
  if (skip) skip.hidden = false;
  if (timer) timer.hidden = true;
  rsIntroStep(0);
}
function rsIntroStep(n) {
  const it = RS.intro;
  if (!it) return;
  // ушли с вкладки или поле перерисовали — знакомство покажем в следующий раз целиком
  if (state.tab !== "raskat" || !RS.el || !RS.el.board.isConnected) return rsIntroStop(false);
  if (n >= RS_INTRO.length) return rsIntroStop(true);
  it.step = n;
  it.anims.forEach((a) => a.cancel());
  it.anims = [];
  const k = RS.day.dots.length;
  it.say = { text: RS_INTRO[n](k), tone: "intro", sr: `Знакомство с полем, шаг ${n + 1} из ${RS_INTRO.length}`, html: "" };
  it.say.html = `<b class="rs-step">${n + 1}/${RS_INTRO.length}</b> ${esc(it.say.text)}`;
  rsLine();
  rsIntroPaint(n);
  it.timers.push(setTimeout(() => rsIntroStep(n + 1), RS_INTRO_MS));
}
// Что показывает шаг: палец и след (0), номера по порядку (1), борта и весь лёд (2).
// prefers-reduced-motion — без движения: палец стоит на номере 1, след нарисован стрелкой
function rsIntroPaint(n) {
  const it = RS.intro;
  const d = RS.day;
  const el = RS.el;
  if (!it || !el) return;
  const one = d.dots[0];
  const exits = rsNear(one);
  const to = exits.length === 1 ? exits[0] : -1;
  const still = calm();
  const finger = el.finger;
  const at = (i) => `translate(${rsCX(i).toFixed(1)}px, ${rsCY(i).toFixed(1)}px) translate(-50%, -50%)`;
  finger.style.width = finger.style.height = `${(RS.geo.cell * 0.92).toFixed(1)}px`;
  rsGhost(n === 0 && to >= 0 ? [one, to] : [], still ? "hint" : "intro");
  if (n === 0) {
    finger.hidden = false;
    finger.style.transform = at(one);
    if (!still && to >= 0) {
      const path = el.ghost.querySelector(".g");
      const len = path.getTotalLength ? path.getTotalLength() : 0;
      it.anims.push(finger.animate([
        { transform: `${at(one)} scale(1.25)`, opacity: 0, offset: 0 },
        { transform: `${at(one)} scale(1)`, opacity: 1, offset: 0.18 },
        { transform: `${at(one)} scale(.9)`, opacity: 1, offset: 0.3 },
        { transform: `${at(to)} scale(.9)`, opacity: 1, offset: 0.7 },
        { transform: `${at(to)} scale(1)`, opacity: 0, offset: 1 },
      ], { duration: 1500, iterations: 2, easing: "ease-in-out" }));
      if (len) {
        it.anims.push(path.animate([
          { strokeDashoffset: len, offset: 0 }, { strokeDashoffset: len, offset: 0.3 },
          { strokeDashoffset: 0, offset: 0.7 }, { strokeDashoffset: 0, offset: 1 },
        ], { duration: 1500, iterations: 2, easing: "ease-in-out" }));
      }
    }
  } else {
    finger.hidden = true;
  }
  el.nums.forEach((b) => b.classList.toggle("call", n === 1));
  el.walls.classList.toggle("call", n === 2);
  if (still) return;
  if (n === 1) {
    el.nums.forEach((b, i) => it.anims.push(b.animate([
      { transform: b.style.transform }, { transform: `${b.style.transform} scale(1.22)` }, { transform: b.style.transform },
    ], { duration: 420, delay: 260 + i * 380, easing: EASE_OUT })));
  }
  if (n === 2) {
    el.walls.querySelectorAll(".rs-wall").forEach((w, i) => it.anims.push(w.animate([
      { opacity: 1 }, { opacity: 0.25 }, { opacity: 1 },
    ], { duration: 520, delay: 200 + (i % 6) * 60, iterations: 2, easing: "ease-in-out" })));
    const sel = getComputedStyle(document.documentElement).getPropertyValue("--sel").trim() || "#dceeff";
    el.cells.forEach((c, i) => it.anims.push(c.animate([
      { backgroundColor: "transparent" }, { backgroundColor: sel }, { backgroundColor: "transparent" },
    ], { duration: 360, delay: 900 + i * 22, easing: "ease-in-out" })));
  }
}
function rsIntroStop(seen) {
  const it = RS.intro;
  if (!it) return;
  it.timers.forEach(clearTimeout);
  it.anims.forEach((a) => a.cancel());
  RS.intro = null;
  if (seen) lsSet(rsKey(RS_INTRO_KEY), "1");
  if (RS.el) {
    RS.el.board.classList.remove("intro");
    RS.el.finger.hidden = true;
    RS.el.nums.forEach((b) => b.classList.remove("call"));
    RS.el.walls.classList.remove("call");
  }
  const skip = $("#rs-skip");
  const timer = $("#rs-timer");
  if (skip) skip.hidden = true;
  if (timer) timer.hidden = false;
  rsGhost(RS.hintCells.length ? [RS.path[RS.path.length - 1], ...RS.hintCells] : []);
  rsLine();
  rsActsSync();
}

// След поверх льда. hint — пунктир со стрелкой до последней подсказанной клетки; intro — бледная
// лента знакомства, её рисует палец
function rsGhost(cells, mode = "hint") {
  if (!RS.el || !RS.geo) return;
  const g = RS.el.ghost;
  const p = g.querySelector(".g");
  g.classList.toggle("intro", mode === "intro");
  if (cells.length < 2) {
    p.setAttribute("d", "");
    p.removeAttribute("marker-end");
    return;
  }
  p.setAttribute("d", `M${cells.map((i) => `${rsCX(i).toFixed(1)} ${rsCY(i).toFixed(1)}`).join("L")}`);
  p.setAttribute("stroke-width", (mode === "intro" ? RS.geo.cell * 0.4 : Math.max(3, RS.geo.cell * 0.08)).toFixed(1));
  if (mode === "hint") p.setAttribute("marker-end", "url(#rs-arrow)");
  else p.removeAttribute("marker-end");
  const len = mode === "intro" && p.getTotalLength ? p.getTotalLength() : 0;
  p.style.strokeDasharray = len ? `${len} ${len}` : "";
}
function rsGhostIn() {
  if (!RS.el || calm()) return;
  RS.el.ghost.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 220, easing: EASE_OUT });
  RS.el.cells.forEach((c, i) => {
    if (RS.hintCells.includes(i)) c.animate([{ transform: "scale(.9)" }, { transform: "none" }], { duration: 260, delay: 60 * RS.hintCells.indexOf(i), easing: EASE_OUT, fill: "backwards" });
  });
}

// ---------- таймер ----------

function rsStartTick() {
  rsStopTick();
  RS.tick = setInterval(rsTickPaint, 250);
  rsTickPaint();
}
function rsStopTick() {
  if (RS.tick) clearInterval(RS.tick);
  RS.tick = 0;
}
function rsTickPaint() {
  const el = $("#rs-timer");
  if (!el) return rsStopTick();
  el.textContent = rsClock(RS.solved || !RS.t0 ? RS.ms : Date.now() - RS.t0);
}

// ---------- экран ----------

function renderRaskat() {
  if (!RS.status) {
    rsLoad().then(() => { rsCloudPull(); rsPaint(); });
    return rsBand() + `<div id="rs-body">${rsSkeleton()}</div>`;
  }
  return rsBand() + `<div id="rs-body">${rsSegBody()}</div>`;
}
const rsSkeleton = () => '<div class="sk" style="height:320px"></div><div class="sk sk-label"></div>';

function rsBand() {
  const d = RS.day || rsLatest();
  const date = d && d.date ? d.date : "";
  const n = d && d.n ? d.n : rsDayN(date);
  const meta = date ? `<div class="rs-no"><b>№ ${esc(String(n))}</b><span>${esc(fmtLong(date))}</span></div>` : "";
  return `<section class="band peach rs-band">
    <div class="rs-head"><h1>Раскат</h1>${meta}</div>
    <div class="seg" role="group" aria-label="Что показать" data-run="rs-seg">${RUN}
      ${segBtn(RS.seg === "ice", 'data-rs-seg="ice"', "<span>Раскат</span>")}
      ${segBtn(RS.seg === "board", 'data-rs-seg="board"', "<span>Зачёт</span>")}
    </div></section>`;
}
function rsSegBody() {
  if (RS.status === "fail") return rsFail("Не удалось загрузить «Раскат». Проверь интернет.");
  if (RS.status === "none") return rsNone();
  if (RS.seg === "board") return rsBoardBody();
  if (RS.dayFail) return rsFail(RS.dayFail);
  if (!RS.day) return rsSkeleton();
  return rsIceBody();
}
function rsFail(text, act = "retry") {
  const fig = guideFig(state.fav, "shrug");
  return `<div class="empty${fig ? " guide-empty" : ""}">${fig}<div>${esc(text)}<br><button type="button" class="retry" data-rs="${act}">Повторить</button></div></div>`;
}
function rsNone() {
  return rsGuideCard("shrug", "Раскаты ещё не опубликованы. Первая головоломка появится вместе с началом сезона — загляни позже.")
    + rsWaitCta() + rsRules(false);
}
function rsGuideCard(pose, text, below = "") {
  const fig = guideFig(state.fav, pose);
  return `<div class="rs-guide${fig ? "" : " bare"}">${fig}<p>${text}</p>${below ? `<div class="rs-guide-below">${below}</div>` : ""}</div>`;
}
function rsWaitCta() {
  const bot = state.data && state.data.links && state.data.links.bot;
  if (!bot) return "";
  return `<div class="rs-cta"><button type="button" class="btn" data-rs="waitlist">${RS_I.bell}Позвать, когда откроется</button></div>`;
}
const RS_RULES = [
  "Веди шайбу пальцем по клеткам: нужно пройти <b>весь лёд</b> по разу и задеть номера звена <b>по порядку</b> — сначала 1, потом 2 и до последнего.",
  "Ход — только в соседнюю клетку по стороне. Через борт нельзя, в пройденную клетку тоже. Шаг назад — веди обратно по своей ленте, нажми «Назад» или коснись ленты, куда вернуться.",
  `Очки: 60 за собранный раскат и до 40 за время. Подсказка показывает три следующие клетки и стоит ${RS_HINT_COST} очков дня, к раскату дня она одна; в тренировке — без ограничений.`,
  "Расклад один для всех и меняется в полночь по Москве. Время идёт от первого касания поля.",
];
function rsRules(show = true) {
  return `<div class="label">Правила<span class="aside">как собрать</span></div>
    <div class="rs-rules">${RS_RULES.map((p) => `<p>${p}</p>`).join("")}
      ${show && RS.day ? '<button type="button" class="rs-pill" data-rs="intro">Показать на поле</button>' : ""}</div>`;
}

// ---------- лёд ----------

const rsClubColor = () => {
  const c = (team(state.fav).colors || [])[0];
  return /^#[0-9a-f]{6}$/i.test(c || "") ? c : "#ffffff";
};
// Контраст считаем сами: номер на майке читается и на тёмной форме, и на светлой
function rsOnColor(hex) {
  const v = (i) => {
    const c = parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * v(0) + 0.7152 * v(1) + 0.0722 * v(2) > 0.179 ? "#000000" : "#ffffff";
}

function rsIceBody() {
  const d = RS.day;
  const lede = RS.train
    ? `Тренировка · ${esc(fmtLong(RS.date))} — в зачёт не идёт`
    : d.lede ? esc(d.lede) : `${d.dots.length} ${plural(d.dots.length, "номер", "номера", "номеров")}, весь лёд за один раскат`;
  const more = [
    RS.train ? '<button type="button" class="rs-pill" data-rs="today">К раскату дня</button>' : "",
    '<button type="button" class="rs-link" data-rs="rules">Как собрать раскат</button>',
  ].join("");
  return `<div class="rs-ice" id="rs-ice">
      <div class="rs-top">
        <span class="rs-timer num" id="rs-timer">${rsClock(RS.solved ? RS.ms : 0)}</span>
        <button type="button" class="rs-skip" id="rs-skip" data-rs="intro-skip" hidden>Пропустить</button>
        <p class="rs-say" id="rs-say" role="status" aria-live="polite"></p>
      </div>
      ${rsFieldHTML(d)}
      <div class="rs-lede">${lede}</div>
      <div class="rs-fin" id="rs-fin" hidden></div>
    </div>
    <div class="rs-acts" id="rs-acts">${rsActsHTML()}</div>
    <div id="rs-ask">${RS.askHint ? rsAskHTML() : ""}</div>
    <div id="rs-score">${RS.solved ? rsScoreHTML() : ""}</div>
    <div id="rs-cta">${rsCtaHTML()}</div>
    <div class="rs-more">${more}</div>
    ${Object.keys(rsRecords()).length ? "" : rsRules()}
    <div class="foot">Расклад один для всех и открывается в 00:00 по Москве. Время идёт от первого касания поля.</div>`;
}
// Кнопки под полем: «Заново», «Назад», «Подсказка». Состояние меняем на месте — фокус не теряется
function rsActsHTML() {
  return `<button type="button" class="rs-pill" data-rs="restart">${RS_I.again}Заново</button>
    <button type="button" class="rs-pill" data-rs="back" aria-label="Шаг назад">${RS_I.undo}Назад</button>
    <button type="button" class="rs-pill" data-rs="hint">${RS_I.hint}Подсказка</button>`;
}
function rsActsSync() {
  const box = $("#rs-acts");
  if (!box) return;
  const back = box.querySelector('[data-rs="back"]');
  const hint = box.querySelector('[data-rs="hint"]');
  back.disabled = RS.solved || RS.path.length < 2;
  back.classList.toggle("hot", !!RS.dead && !RS.solved);
  hint.disabled = RS.solved;
  hint.classList.toggle("spent", !RS.train && RS.hint && !RS.hintCells.length);
  hint.setAttribute("aria-label", RS.train ? "Подсказка — в тренировке без ограничений"
    : RS.hint ? "Подсказка — к раскату дня уже была" : `Подсказка — стоит ${RS_HINT_COST} очков дня`);
}

// Поле: клетки — DOM, лента и шайба — слои поверх, разметка арены — под сеткой
function rsFieldHTML(d) {
  const n = rsCells(d);
  const fill = rsClubColor();
  const ink = rsOnColor(fill);
  let cells = "";
  for (let i = 0; i < n; i += 1) cells += `<i class="rs-cell" data-i="${i}"></i>`;
  let nums = "";
  d.dots.forEach((cell, k) => {
    nums += `<b class="rs-num" data-n="${cell}" style="--fill:${esc(fill)};--on:${esc(ink)}">${k + 1}</b>`;
  });
  let walls = "";
  for (const key of d.walls) {
    const [a, b] = key.split("-").map(Number);
    if (!rsSide(a, b, d)) continue;
    walls += `<i class="rs-wall ${b - a === 1 ? "v" : "h"}" data-w="${esc(key)}"></i>`;
  }
  return `<div class="rs-board" id="rs-board" role="application" tabindex="0" aria-label="${rsBoardLabel()}" style="--w:${d.w};--h:${d.h}">
      <svg class="rs-marks" id="rs-marks" aria-hidden="true" preserveAspectRatio="none"></svg>
      <div class="rs-cells">${cells}</div>
      <svg class="rs-ribbon" id="rs-ribbon" aria-hidden="true"><g><path class="rs-trail o"/><path class="rs-trail f"/></g></svg>
      <svg class="rs-ghost" id="rs-ghost" aria-hidden="true"><defs><marker id="rs-arrow" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="3.2" markerHeight="3.2" orient="auto-start-reverse"><path d="M0 0 10 5 0 10z"/></marker></defs><path class="g"/></svg>
      <div class="rs-walls" id="rs-walls" aria-hidden="true">${walls}</div>
      <i class="rs-goal" id="rs-goal" aria-hidden="true"></i>
      <i class="rs-puck" id="rs-puck" aria-hidden="true">${RS_I.puck}</i>
      <div class="rs-nums" id="rs-nums" aria-hidden="true">${nums}</div>
      <i class="rs-finger" id="rs-finger" aria-hidden="true" hidden>${RS_I.touch}</i>
    </div>`;
}
function rsBoardLabel() {
  const d = RS.day;
  if (!d) return "Поле «Раската»";
  const head = RS.path[RS.path.length - 1];
  return esc(`Поле ${d.w} на ${d.h}, шайба в ${rsWhereIn(head == null ? d.dots[0] : head)}. Стрелки ведут шайбу, пробел — шаг назад`);
}

// Табло под полем: время и итог дня крупно, серия и место — строкой. Остаётся после финального
// табло поверх поля и при следующем заходе в этот день
function rsScoreHTML() {
  const r = RS.res || {};
  const streak = RS.train ? 0 : (r.streak || rsStreak(RS.date));
  const bonus = Math.min(RS_RUN, 2 * Math.max(0, streak - 1));
  const total = r.total != null ? r.total : r.points || 0;
  let place = "";
  if (!RS.train) {
    if (r.place) place = `${esc(String(r.place))}-е место из ${esc(String(r.of || r.place))}`;
    else if (r.srvErr) place = `зачёт не принял: ${esc(r.srvErr.replace(/\.$/, ""))}`;
    else if (rsServer()) place = "место — когда ответит зачёт";
    else place = "личный зачёт — на этом устройстве";
  }
  const note = [RS.train ? "тренировка · в зачёт не идёт" : "", r.hint ? "с подсказкой" : ""].filter(Boolean).join(" · ");
  const run = streak ? `<span class="rs-run">серия ${rsDays(streak)}${bonus ? ` · +${bonus} к итогу` : ""}</span>` : "";
  const again = RS.replay && r.again ? `<div class="rs-score-say">Ещё раз — за <b>${esc(rsClock(r.again.ms))}</b>. В зачёте остаётся первый раскат дня.</div>` : "";
  return `<div class="rs-score" role="group" aria-label="Раскат собран">
    <div class="rs-score-top">
      <div class="rs-time"><b class="num">${esc(rsClock(r.ms || RS.ms))}</b><small>время</small></div>
      <div class="rs-got"><b class="num">${esc(String(total))}</b><small>${RS.train ? plural(total, "очко", "очка", "очков") : "итог дня"}</small></div>
    </div>
    <div class="rs-score-foot">${run}${place ? `<span class="rs-place">${place}</span>` : ""}${note ? `<span class="rs-note">${esc(note)}</span>` : ""}</div>
    ${r.points != null && r.points !== total ? `<div class="rs-score-say">В зачёт дня идут <b>${esc(String(r.points))}</b> — серия считается отдельно.</div>` : ""}
    ${again}
  </div>`;
}
function rsCtaHTML() {
  if (!RS.solved) return "";
  return `<button type="button" class="btn" data-rs="share">${RS_I.share}Поделиться раскатом</button>`;
}

// ---------- перерисовка поля на месте ----------

function rsMount() {
  const board = $("#rs-board");
  if (!board || !RS.day) {
    RS.el = null;
    return;
  }
  RS.el = {
    board,
    cells: [...board.querySelectorAll(".rs-cell")],
    nums: [...board.querySelectorAll(".rs-num")],
    walls: $("#rs-walls"),
    ribbon: $("#rs-ribbon"),
    trail: [...board.querySelectorAll(".rs-trail")],
    ghost: $("#rs-ghost"),
    marks: $("#rs-marks"),
    puck: $("#rs-puck"),
    goal: $("#rs-goal"),
    finger: $("#rs-finger"),
  };
  if (!board.dataset.on) {
    board.dataset.on = "1";
    board.addEventListener("pointerdown", rsDown);
    board.addEventListener("pointermove", rsPointerMove);
    board.addEventListener("pointerup", rsUp);
    board.addEventListener("pointercancel", rsUp);
    board.addEventListener("lostpointercapture", rsUp);
  }
  rsLayout();
  if (RS.solved) rsGoal(false);
  if (RS.t0 && !RS.solved) rsStartTick();
  rsLine();
  rsActsSync();
  if (RS.fin && RS.solved) rsFinShow(false);
}
// Размеры клетки считает CSS — JS их только измеряет: лента и шайба живут в тех же пикселях
function rsLayout() {
  if (!RS.el || !RS.day) return;
  const cells = RS.el.cells;
  if (!cells.length || !cells[0].offsetWidth) return;
  const cell = cells[0].offsetWidth;
  const gap = RS.day.w > 1 ? Math.max(0, cells[1].offsetLeft - cells[0].offsetLeft - cell) : 0;
  const w = RS.day.w * cell + (RS.day.w - 1) * gap;
  const h = RS.day.h * cell + (RS.day.h - 1) * gap;
  RS.geo = { cell, gap, w, h };
  RS.el.ribbon.setAttribute("viewBox", `0 0 ${w} ${h}`);
  RS.el.ghost.setAttribute("viewBox", `0 0 ${w} ${h}`);
  RS.el.marks.setAttribute("viewBox", `0 0 ${w} ${h}`);
  // Разметка арены: две синие линии и круг в центре, без красного. Декор под лентой
  RS.el.marks.innerHTML = `<path d="M${(w / 3).toFixed(1)} 0V${h.toFixed(1)}M${(w * 2 / 3).toFixed(1)} 0V${h.toFixed(1)}"/>`
    + `<circle cx="${(w / 2).toFixed(1)}" cy="${(h / 2).toFixed(1)}" r="${(Math.min(w, h) * 0.19).toFixed(1)}"/>`;
  RS.el.trail.forEach((p) => p.setAttribute("stroke-width", String(cell * (p.classList.contains("o") ? 0.44 : 0.4))));
  RS.el.puck.style.width = `${(cell * 0.44).toFixed(1)}px`;
  RS.el.nums.forEach((b) => {
    const i = Number(b.dataset.n);
    b.style.width = b.style.height = `${(cell * 0.6).toFixed(1)}px`;
    b.style.transform = `translate(${rsCX(i).toFixed(1)}px, ${rsCY(i).toFixed(1)}px) translate(-50%, -50%)`;
    b.style.fontSize = `${Math.round(cell * 0.34)}px`;
  });
  RS.el.walls.querySelectorAll(".rs-wall").forEach((el) => {
    const [a, b] = el.dataset.w.split("-").map(Number);
    const side = b - a === 1;
    const x = side ? rsCX(a) + cell / 2 + gap / 2 : rsCX(a);
    const y = side ? rsCY(a) : rsCY(a) + cell / 2 + gap / 2;
    el.style.width = side ? "4px" : `${(cell * 0.7).toFixed(1)}px`;
    el.style.height = side ? `${(cell * 0.7).toFixed(1)}px` : "4px";
    el.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) translate(-50%, -50%)`;
  });
  rsDraw();
}
const rsCX = (i) => ((i % RS.day.w) + 0.5) * RS.geo.cell + (i % RS.day.w) * RS.geo.gap;
const rsCY = (i) => (Math.floor(i / RS.day.w) + 0.5) * RS.geo.cell + Math.floor(i / RS.day.w) * RS.geo.gap;

function rsDraw() {
  if (!RS.el || !RS.geo || !RS.day) return;
  const d = `M${RS.path.map((i) => `${rsCX(i).toFixed(1)} ${rsCY(i).toFixed(1)}`).join("L")}`;
  RS.el.trail.forEach((p) => p.setAttribute("d", RS.path.length > 1 ? d : `${d}L${rsCX(RS.path[0]).toFixed(1)} ${rsCY(RS.path[0]).toFixed(1)}`));
  const head = RS.path[RS.path.length - 1];
  // собранный раскат — шайба в воротах; перерисовка после поворота экрана её оттуда не достаёт
  if (RS.solved && RS.el.goal.classList.contains("on")) {
    RS.el.puck.style.transition = "none";
    RS.el.puck.style.transform = `translate(${RS.geo.w + 5}px, ${RS.geo.h / 2}px) translate(-50%, -50%)`;
  } else {
    RS.el.puck.style.transition = calm() || !RS.t0 ? "none" : "";
    RS.el.puck.style.transform = `translate(${rsCX(head).toFixed(1)}px, ${rsCY(head).toFixed(1)}px) translate(-50%, -50%)`;
  }
  const on = new Set(RS.path);
  const lit = new Set(RS.hintCells);
  const cut = new Set(RS.dead ? RS.dead.cut : []);
  RS.el.cells.forEach((el, i) => {
    el.classList.toggle("on", on.has(i));
    el.classList.toggle("lit", lit.has(i));
    el.classList.toggle("cut", cut.has(i));
  });
  const passed = rsPassed();
  RS.el.nums.forEach((b, k) => {
    b.classList.toggle("done", k < passed);
    b.classList.toggle("next", k === passed && !RS.solved);
    // наклейка под шайбой не гаснет: иначе сквозь неё просвечивает шайба и номер не прочесть
    b.classList.toggle("head", Number(b.dataset.n) === head && !RS.solved);
  });
  if (!RS.intro) rsGhost(RS.hintCells.length ? [head, ...RS.hintCells] : []);
  RS.el.board.setAttribute("aria-label", rsBoardLabel());
  RS.el.board.classList.toggle("done", RS.solved);
  RS.el.board.classList.toggle("dead", !!RS.dead);
}

// Перерисовать «Раскат» на месте: прокрутка и бегунки остаются
function rsPaint() {
  if (state.tab !== "raskat" || !state.fav) return;
  const screen = $("#screen");
  const prev = runnerState(screen);
  const y = window.scrollY;
  rsIntroStop(false);
  screen.innerHTML = renderRaskat();
  addThemeToggle();
  placeRunners(screen, prev);
  window.scrollTo(0, y);
  rsMounted();
}
function rsPaintBody() {
  const box = $("#rs-body");
  if (!box || state.tab !== "raskat") return rsPaint();
  const prev = runnerState(box);
  rsIntroStop(false);
  box.innerHTML = rsSegBody();
  placeRunners(box, prev);
  rsMounted();
}
// После каждой отрисовки: слои поля, точка на вкладке, зачёт, знакомство с полем
function rsMounted() {
  rsStopTick();
  rsMount();
  rsDue();
  if (RS.status !== "ok") return;
  rsProbeApi();
  if (rsServer()) {
    rsSync();
    rsFlush();
    if (RS.seg === "board") rsBoardLoad();
    else if (RS.solved && !RS.train && !RS.replay && RS.res && !RS.res.srvErr) rsPlaceLoad();
  }
  if (RS.seg === "ice") setTimeout(rsIntroMaybe, 450);
}
// Точка «надо решить» на иконке вкладки: раскат дня ещё не собран (DESIGN.md → «Точка „надо решить“»)
function rsDue(due = null) {
  const svg = document.querySelector('#tabs [data-tab="raskat"] svg');
  if (!svg) return;
  const open = rsLatest();
  const need = due != null ? due : !!open && !rsRecords()[open.date];
  const dot = svg.querySelector(".due");
  if (need && !dot) svg.insertAdjacentHTML("beforeend", '<circle class="due" cx="20" cy="4.5" r="3.6"/>');
  if (!need && dot) dot.remove();
  svg.parentNode.setAttribute("aria-label", need ? "Раскат — не собран" : "Раскат");
}

// ---------- зачёт: сервер ----------

// Ошибка API несёт код и тело ответа: 409 отдаёт прежний Result вместе с error (контракт, раздел 5)
function rsApiError(text, status = 0, data = null) {
  const e = new Error(text);
  e.status = status;
  e.data = data;
  return e;
}
async function rsApi(method, path, body) {
  if (rsMocked()) return window.RASKAT_MOCK_API(method, path, body);
  let r;
  try {
    r = await fetch(rsApiBase() + path, {
      method,
      headers: Object.assign({ Authorization: `tma ${tg.initData}` }, body ? { "Content-Type": "application/json" } : {}),
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw rsApiError("Нет связи с зачётом. Проверь интернет и попробуй ещё раз.");
  }
  let d = null;
  try { d = await r.json(); } catch (e) { d = null; }
  if (!r.ok) throw rsApiError((d && typeof d.error === "string" && d.error) || "Зачёт не ответил. Попробуй ещё раз.", r.status, d);
  return d;
}
// Запрос зачёта с кэшем на минуту: одна таблица — один запрос, пока она свежая
function rsSrvGet(key, path, after) {
  const s = RS.srv;
  // ошибка — до «Повторить»: иначе перерисовка после отказа просит снова, и так по кругу
  if (s.loading[key] || s.fail[key]) return;
  if (s.data[key] !== undefined && Date.now() - (s.at[key] || 0) < RS_STALE_MS) {
    if (after && s.data[key]) after(s.data[key]);
    return;
  }
  s.loading[key] = true;
  rsApi("GET", path)
    .then((d) => {
      s.data[key] = d || {};
      s.at[key] = Date.now();
      delete s.fail[key];
      if (after) after(d || {});
    })
    .catch((e) => {
      s.fail[key] = { text: e.message, status: e.status };
      if (e.status === 404) { s.data[key] = null; s.at[key] = Date.now(); }
    })
    .then(() => {
      s.loading[key] = false;
      if (state.tab === "raskat" && RS.seg === "board") rsPaintBody();
    });
}
function rsSrvDrop(prefix) {
  const s = RS.srv;
  Object.keys(s.data).forEach((k) => { if (k === prefix || k.startsWith(`${prefix}:`)) { delete s.data[k]; delete s.at[k]; } });
  Object.keys(s.fail).forEach((k) => { if (k === prefix || k.startsWith(`${prefix}:`)) delete s.fail[k]; });
}
// Раз за сессию: настройки и клуб с сервера. Любимую команду сменили в приложении — клуб в зачёте тоже
function rsSync() {
  if (RS.synced || !rsServer()) return;
  RS.synced = true;
  rsApi("GET", "/me").then((me) => {
    if (!me) return;
    RS.srv.data.me = me;
    RS.srv.at.me = Date.now();
    if (typeof me.show_tg_name === "boolean") rsSetSetting("name", me.show_tg_name, false);
    if (typeof me.messages === "boolean") rsSetSetting("messages", me.messages, false);
    if (state.fav && state.teams[state.fav] && me.club !== state.fav) rsApi("PUT", "/settings", { club: state.fav }).catch(() => {});
    rsRedrawSheet();
  }).catch(() => { RS.synced = false; });
}
// Место в зачёте у собранного раската дня: табло под полем показывает его, когда ответит сервер
function rsPlaceLoad() {
  const date = RS.date;
  rsSrvGet(`day:${date}`, `/day/${date}`, (d) => {
    if (!d.me || RS.date !== date || !RS.solved || RS.replay) return;
    RS.res = Object.assign({}, RS.res, { place: d.me.place, of: d.me.of, points: d.me.points, total: d.me.total, streak: d.me.streak });
    rsAfterPaint();
    rsFinPaint();
  });
}
function rsBoardLoad() {
  const date = rsLatest() ? rsLatest().date : "";
  if (!date) return;
  if (RS.board === "clubs") rsSrvGet(`clubs:${date}`, `/clubs/${date}`);
  else if (RS.board === "day") rsSrvGet(`day:${date}`, `/day/${date}`);
  else rsSrvGet("me", "/me");
  const code = lsGet(rsKey(RS_DUEL_KEY));
  if (code) rsSrvGet(`duel:${code}`, `/duel/${encodeURIComponent(code)}`, (d) => { if (d.own) lsSet(rsKey(RS_OWN_KEY), code); });
}

function rsBoardBody() {
  const tools = `<div class="rs-tools">
      <button type="button" class="rs-pill" data-rs="train">Тренировка</button>
      <button type="button" class="rs-pill" data-rs="set">Настройки</button>
      <button type="button" class="rs-pill" data-rs="rules">Как собрать</button>
    </div>`;
  if (!rsServer()) return rsSoloBoard() + tools;
  const date = rsLatest() ? rsLatest().date : "";
  const chips = `<div class="chips rs-chips" role="group" aria-label="Что показать" data-run="rs-board">${RUN}
      ${segBtn(RS.board === "day", 'data-rs-board="day"', "День")}
      ${segBtn(RS.board === "clubs", 'data-rs-board="clubs"', "Кубок клубов")}
      ${segBtn(RS.board === "streak", 'data-rs-board="streak"', "Серия")}
    </div>`;
  const key = RS.board === "clubs" ? `clubs:${date}` : RS.board === "day" ? `day:${date}` : "me";
  const fail = RS.srv.fail[key];
  const data = RS.srv.data[key];
  let body;
  if (fail && !data) body = rsFail(fail.text, "srv-retry");
  else if (RS.board === "day") body = rsDayTable(data);
  else if (RS.board === "clubs") body = rsClubsTable(data);
  else body = rsStreakBody(data);
  return chips + rsDuelLinked() + rsTodayNudge() + body + rsDuelInvite() + tools;
}
// Раскат дня ещё не собран — строка над таблицей зовёт к полю
function rsTodayNudge() {
  const day = rsLatest();
  const me = RS.srv.data.me;
  if (!day || rsRecords()[day.date] || (me && me.today)) return "";   // собран и на другом устройстве
  return `<div class="rs-nudge"><span>Раскат дня ещё не собран — твоей строки в зачёте пока нет</span>
    <button type="button" class="rs-pill" data-rs="to-ice">К полю</button></div>`;
}
function rsDayTable(d) {
  if (!d) return rsSkeleton();
  const top = Array.isArray(d.top) ? d.top : [];
  if (!top.length) return '<div class="empty">Сегодня раскат ещё никто не собрал. Будь первым.</div>';
  const mine = top.some((r) => r.me);
  const of = d.of || top.length;
  let html = `<div class="label">Зачёт дня<span class="aside">${esc(String(of))} ${plural(of, "болельщик", "болельщика", "болельщиков")}</span></div>`;
  html += `<div class="rs-table">${top.map(rsDayRow).join("")}`;
  // своя строка не уезжает из виду: нет в списке — пунктир и своя строка под ним
  if (!mine && d.me) html += `<div class="rs-cut" aria-hidden="true"></div>${rsDayRow(Object.assign({ me: true, club: state.fav }, d.me))}`;
  html += "</div>";
  html += '<div class="foot">В зачёте дня — очки дня: собранный раскат, время и подсказка. Серия в них не входит, она в твоём итоге и в своём зачёте.</div>';
  return html;
}
function rsDayRow(r) {
  // имя отдаёт сервер: «Пётр К.» по галочке или «Болельщик «Рязани-ВДВ»»
  const name = r.name ? esc(r.name) : r.me ? "Ты" : `Болельщик${r.club ? ` «${esc(team(r.club).name)}»` : ""}`;
  const pts = r.points;   // в таблице дня — очки дня, итог с серией живёт на табло
  const sub = [r.me && r.name ? "это ты" : "", r.hint ? "с подсказкой" : ""].filter(Boolean).join(" · ");
  return `<div class="rs-row${r.me ? " me" : ""}">
    <span class="pos num">${esc(String(r.place || "—"))}</span>
    ${r.club ? emblem(r.club) : '<span class="rs-noclub" aria-hidden="true"></span>'}
    <span class="nm">${name}${sub ? `<small>${sub}</small>` : ""}</span>
    <b class="pts num">${esc(String(pts != null ? pts : 0))}</b>
    <span class="tm num">${esc(rsClock(r.ms || 0))}</span>
  </div>`;
}
// Клуб ниже порога из приложения не исчезает: вместо среднего — честная строка (контракт, раздел 4)
function rsClubRow(r, season = false) {
  const fans = r.fans || 0;
  const low = r.avg == null || (!season && fans < RS_CLUB_MIN);
  const avg = `<b class="pts num">${esc(String(Math.round((r.avg || 0) * 10) / 10))}</b>`;
  const sub = low ? `собрали ${fans}, кубок считается от ${RS_CLUB_MIN}`
    : `${fans} ${plural(fans, "болельщик", "болельщика", "болельщиков")}${season && r.days ? ` · ${r.days} ${plural(r.days, "день", "дня", "дней")}` : ""}`;
  return `<div class="rs-row${r.me ? " me" : ""}">
      <span class="pos num">${esc(String(low ? "—" : r.place || "—"))}</span>${emblem(r.club)}
      <span class="nm">${esc(team(r.club).name)}<small>${esc(sub)}</small></span>
      ${low ? "" : avg}</div>`;
}
function rsClubsTable(d) {
  if (!d) return rsSkeleton();
  // клубы ниже порога сервер шлёт отдельным списком low
  const day = Array.isArray(d.day) ? d.day : [];
  const low = Array.isArray(d.low) ? d.low : [];
  const rows = day.slice(0, 10);
  const myId = d.me || state.fav;
  const mineDay = !rows.some((r) => r.me) && day.find((r) => r.me);
  const mineLow = low.find((r) => r.me || r.club === myId);
  const season = Array.isArray(d.season) ? d.season.slice(0, 10) : [];
  const seasonMine = !season.some((r) => r.me) && Array.isArray(d.season) && d.season.find((r) => r.me);
  let html = '<div class="label">Кубок клубов<span class="aside">среднее очков дня</span></div>';
  if (!rows.length && !mineLow) {
    html += `<div class="empty">Кубок клубов считается, когда за клуб собрали раскат хотя бы ${RS_CLUB_MIN} болельщиков. Сегодня такого клуба ещё нет.</div>`;
  } else {
    const head = mineDay ? rsClubRow(mineDay) : mineLow ? rsClubRow(Object.assign({ me: true }, mineLow)) : "";
    html += `<div class="rs-table">${head}${head && rows.length ? '<div class="rs-cut" aria-hidden="true"></div>' : ""}${rows.map((r) => rsClubRow(r)).join("")}</div>`;
  }
  if (!myId) html += '<div class="rs-note rs-pad">Выбери любимую команду на вкладке «Я» — и твои очки пойдут в кубок её клуба.</div>';
  if (season.length) {
    html += `<div class="label">За сезон<span class="aside">среднее дневных средних</span></div>
      <div class="rs-table">${seasonMine ? rsClubRow(seasonMine, true) + '<div class="rs-cut" aria-hidden="true"></div>' : ""}${season.map((r) => rsClubRow(r, true)).join("")}</div>`;
  }
  html += '<div class="foot">Считается среднее, а не сумма: иначе кубок каждый день выигрывает клуб, у которого болельщиков в разы больше, а не самый упорный. Сезонный кубок — среднее дневных средних.</div>';
  return html;
}
// Серия — ряд дней кружками: залитый собран, пустой с контуром пропущен
function rsStreakBody(me) {
  const all = rsRecords();
  const last = rsLatest() ? rsLatest().date : rsToday();
  const days = [];
  for (let i = 6; i >= 0; i -= 1) {
    const date = rsShift(last, -i);
    const rec = all[date];
    days.push({ date, on: !!rec && !rec.train });
  }
  const local = rsStreak(last) || rsStreak(rsShift(last, -1));
  const n = me && typeof me.streak === "number" ? Math.max(me.streak, 0) : local;
  const dow = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
  const row = days.map((x) => `<span class="rs-dayc${x.on ? " on" : ""}"><i></i><small>${esc(dow[parseISO(x.date).getUTCDay()])}</small></span>`).join("");
  const best = me && me.best ? `<div class="rs-week-say">Лучший раскат — <b>${esc(rsPts(me.best.points))}</b> за ${esc(rsClock(me.best.ms))}, ${esc(fmtLong(me.best.date))}${me.best.place ? `, ${esc(String(me.best.place))}-е место` : ""}.</div>` : "";
  return `<div class="label">Серия<span class="aside">неделя</span></div>
    <div class="rs-week" role="img" aria-label="${esc(`Серия ${rsDays(n)}`)}">${row}</div>
    <div class="rs-week-say">${n ? `Серия — <b>${rsDays(n)}</b>. Пропущенный день обнуляет серию` : "Серии пока нет: собери раскат дня, и она начнётся. Пропущенный день её обнуляет"}, тренировка прошлого дня её не восстанавливает.</div>${best}`;
}
// Дуэль по ссылке startapp=rs-<код>: только двое и только дни, когда играли оба (ADR-018, раздел 3.6).
// Пришёл по ссылке — дуэль над таблицей дня: ради неё и открыли зачёт
function rsDuelLinked() {
  const code = lsGet(rsKey(RS_DUEL_KEY));
  const own = lsGet(rsKey(RS_OWN_KEY));
  let html = "";
  if (code && code !== own) {
    const key = `duel:${code}`;
    const d = RS.srv.data[key];
    const fail = RS.srv.fail[key];
    if (fail) {
      html += `<div class="label">Дуэль</div><div class="rs-note rs-pad">${esc(fail.text)}</div>`;
    } else if (d === undefined) {
      html += '<div class="label">Дуэль</div><div class="sk" style="height:96px"></div>';
    } else if (d && !d.own) {
      const days = Array.isArray(d.days) ? d.days.slice(-7).reverse() : [];
      const them = d.name || "Соперник";
      const rows = days.map((x) => {
        const me = x.me == null ? null : Number(x.me);
        const th = x.them == null ? null : Number(x.them);
        return `<div class="rs-row duel"><span class="nm">${esc(fmtLong(x.date))}</span>
          <b class="num${me != null && th != null && me > th ? " win" : ""}">${esc(String(me == null ? "—" : me))}</b><span class="vs">:</span>
          <b class="num${me != null && th != null && th > me ? " win" : ""}">${esc(String(th == null ? "—" : th))}</b></div>`;
      }).join("");
      html += `<div class="label">Дуэль<span class="aside">ты : ${esc(them)}</span></div>`;
      html += days.length ? `<div class="rs-table">${rows}</div>`
        : '<div class="rs-note rs-pad">Дней, когда вы оба собрали раскат, пока нет. Сравнение появится после первого общего дня.</div>';
    }
  }
  return html ? `<div class="rs-duel">${html}</div>` : "";
}
function rsDuelInvite() {
  const own = lsGet(rsKey(RS_OWN_KEY));
  return `<div class="rs-duel-ask">${RS_I.duel}<p>${own ? "Твоя ссылка на дуэль готова: по ней друг увидит только вас двоих — очки по дням." : "Дуэль с другом: кинь ссылку — по ней видно только вас двоих, очки по дням."}</p>
    <button type="button" class="rs-pill" data-rs="duel-new">${own ? "Отправить ещё раз" : "Позвать на дуэль"}</button></div>`;
}

// ---------- зачёт: на устройстве, пока сервера нет ----------

// Без сервера таблиц лиги нет вовсе: личный зачёт на этом устройстве и честная строка про общий
function rsSoloBoard() {
  const all = rsRecords();
  const latest = rsLatest();
  const today = latest && all[latest.date];
  const entries = Object.entries(all).filter(([, r]) => r && typeof r.ms === "number");
  const real = entries.filter(([, r]) => !r.train).sort((a, b) => b[0].localeCompare(a[0]));
  const train = entries.filter(([, r]) => r.train).sort((a, b) => b[0].localeCompare(a[0]));
  const best = real.slice().sort((a, b) => a[1].ms - b[1].ms).slice(0, 3);
  const nOf = (date) => rsDayN(date) || "";
  const row = ([date, r], i, mark) => `<div class="rs-row h${i != null ? " ranked" : ""}${mark && mark(date) ? " me" : ""}">
      ${i != null ? `<span class="pos num">${i + 1}</span>` : ""}
      <span class="nm">${esc(fmtLong(date))}<small>${esc([nOf(date) ? `№ ${nOf(date)}` : "", r.hint ? "с подсказкой" : ""].filter(Boolean).join(" · ") || (r.train ? "тренировка" : "раскат дня"))}</small></span>
      <b class="pts num">${esc(String(r.train ? r.points : r.total != null ? r.total : r.points))}</b>
      <span class="tm num">${esc(rsClock(r.ms))}</span></div>`;
  let html = '<div class="rs-solo">Личный зачёт — на этом устройстве. Общий откроется вместе с сервером зачёта.</div>';
  html += '<div class="label">Сегодня</div>';
  if (today) {
    const streak = today.streak || rsStreak(latest.date);
    html += `<div class="rs-today">
      <div><b class="num">${esc(rsClock(today.ms))}</b><small>время</small></div>
      <div><b class="num">${esc(String(today.total != null ? today.total : today.points))}</b><small>итог дня</small></div>
      <p>${esc(`№ ${latest.n} · очки дня ${today.points} · серия ${rsDays(streak)}${today.hint ? " · с подсказкой" : ""}`)}</p></div>`;
  } else {
    html += `<div class="rs-nudge"><span>Раскат дня № ${esc(String(latest ? latest.n : ""))} ещё не собран</span>
      <button type="button" class="rs-pill" data-rs="to-ice">К полю</button></div>`;
  }
  html += rsStreakBody(null);
  // один день — это уже «Сегодня»: таблицы появляются со второго собранного раската
  if (real.length > 1) {
    html += `<div class="label">Лучшие времена<span class="aside">раскаты дня</span></div>
      <div class="rs-table">${best.map((e, i) => row(e, i, (d) => latest && d === latest.date)).join("")}</div>`;
  }
  if (real.length > 1) {
    html += `<div class="label">По дням<span class="aside">${esc(String(real.length))} ${plural(real.length, "раскат", "раската", "раскатов")}</span></div>
      <div class="rs-table">${real.slice(0, RS_TRAIN_MAX).map((e) => row(e, null, (d) => latest && d === latest.date)).join("")}</div>`;
  }
  if (train.length) {
    html += `<div class="label">Тренировка<span class="aside">лучшее время, в зачёт не идёт</span></div>
      <div class="rs-table">${train.slice(0, RS_TRAIN_MAX).map((e) => row(e, null)).join("")}</div>`;
  }
  const duel = lsGet(rsKey(RS_DUEL_KEY));
  const say = `Общий зачёт — место дня, кубок клубов и дуэли — откроется вместе с сервером. Выдумывать места до него мы не станем.${duel ? " <b>Тебя позвали на дуэль</b> — она откроется тогда же." : ""}`;
  html += `<div class="label">Общий зачёт</div>${rsGuideCard("shrug", say)}${rsWaitCta()}`;
  return html;
}

// ---------- листы: тренировка и настройки, правила ----------

function rsSheet() {
  const all = rsRecords();
  const latest = rsLatest();
  const past = rsList().filter((d) => d.date <= rsToday() && (!latest || d.date !== latest.date)).slice(-RS_TRAIN_MAX).reverse();
  const rows = past.length
    ? past.map((d) => {
      const rec = all[d.date];
      return `<button type="button" class="menu-row rs-train-row" data-rs="train-open" data-rs-arg="${esc(d.date)}">
        <span><b>${esc(fmtLong(d.date))}</b><small>№ ${esc(String(d.n))}${rec ? ` · собран за ${esc(rsClock(rec.ms))}` : " · ещё не собран"}</small></span>
        <span class="rs-pill as-tag">${rec ? "Ещё раз" : "Собрать"}</span></button>`;
    }).join("")
    : '<div class="menu-row off rs-none-row"><span><b>Прошлых раскатов пока нет</b><small>Они появятся со второго дня сезона</small></span></div>';
  const s = rsSettings();
  const sw = () => '<span class="rs-sw" aria-hidden="true"></span>';
  const set = `<button type="button" class="menu-row rs-set-row" data-rs="set-name" aria-pressed="${s.name}">
      <span><b>Имя из Telegram в зачёте</b><small>${s.name ? "Показываем имя и первую букву фамилии" : "В таблице — «Болельщик «клуба»»"}</small></span>${sw()}</button>
    <button type="button" class="menu-row rs-set-row" data-rs="set-msg" aria-pressed="${s.messages}">
      <span><b>Сообщения о зачёте</b><small>${rsServer() ? "Бот напишет о зачёте «Раската»" : "Бот напишет, когда зачёт откроется"}</small></span>${sw()}</button>
    <button type="button" class="menu-row" data-rs="del">${RS_I.trash}<span><b>Удалить мои раскаты</b><small>Записи, серия и место в зачёте</small></span>${RS_I.chev}</button>`;
  const ask = RS.ask
    ? `<div class="rs-confirm"><p>Сотрём все твои раскаты: записи дней, серию и результат в зачёте. Вернуть их будет нельзя.</p>
        <div class="rs-confirm-btns"><button type="button" class="btn ghost" data-rs="del-no">Не удалять</button>
        <button type="button" class="btn" data-rs="del-yes">Удалить</button></div></div>`
    : "";
  return `<div class="grab"></div>
    <div class="sheet-head"><span class="when">Тренировка и настройки</span>
    <button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    <div class="label">Тренировка<span class="aside">прошлые раскаты</span></div>
    <div class="rs-note">Тренировка в зачёт не идёт и серию не продолжает — это просто лёд для разминки. Подсказки в ней без ограничений.</div>
    <div class="menu">${rows}</div>
    <div class="label">Настройки «Раската»</div>
    <div class="menu rs-set">${set}</div>
    ${ask}`;
}
function rsRulesSheet() {
  return `<div class="grab"></div>
    <div class="sheet-head"><span class="when">Как собрать раскат</span>
    <button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    <div class="rs-rules sheet-rules">${RS_RULES.map((p) => `<p>${p}</p>`).join("")}</div>
    ${RS.day ? '<div class="rs-cta"><button type="button" class="btn" data-rs="intro">Показать на поле</button></div>' : ""}`;
}
function rsOpenSheet() {
  RS.sheetOpen = true;
  RS.ask = false;
  showSheet(rsSheet());
}
function rsRedrawSheet() {
  if (!RS.sheetOpen || $("#sheet").hidden) return;
  const page = $("#sheet .sheet-page");
  if (!page) return;
  const y = $("#sheet").scrollTop;
  page.innerHTML = rsSheet();
  $("#sheet").scrollTop = y;
}
function rsDelete() {
  [RS_DAYS_KEY, RS_HINT_KEY, RS_WOW_KEY, RS_OWN_KEY].forEach((k) => {
    try { localStorage.removeItem(rsKey(k)); } catch (e) { /* приватный режим */ }
  });
  if (cloud() && !rsMockMode()) cloud().removeItem(RS_DAYS_KEY, () => {});
  if (rsServer()) rsApi("DELETE", "/me").catch(() => {});
  RS.ask = false;
  RS.srv = rsFreshSrv();
  RS.synced = false;
  RS.hint = false;
  closeMatch();
  RS.sheetOpen = false;
  if (RS.date) rsOpen(RS.date).then(rsPaint);
  else rsPaint();
}

// ---------- нажатия ----------

function rsAppLink(start) {
  const app = state.data && state.data.links && state.data.links.app;
  return app ? `${app}?startapp=${start}` : `${location.origin}${location.pathname}?startapp=${start}`;
}
function rsShare() {
  const d = RS.day;
  const r = RS.res || {};
  const what = RS.train ? "Тренировочный раскат" : `Раскат № ${d ? d.n : ""}`;
  const place = !RS.train && r.place ? `, ${r.place}-е место из ${r.of || r.place}` : "";
  shareLink(rsAppLink("raskat"), `${what} — ${rsClock(r.ms || RS.ms)}, ${rsPts(r.total != null ? r.total : r.points || 0)}${place}${r.hint ? ", с подсказкой" : ""}. Собери свой!`);
}
// Своя ссылка на дуэль: код отдаёт сервер, без него — честная строка, а не выдуманная ссылка
function rsDuelNew(btn) {
  const own = lsGet(rsKey(RS_OWN_KEY));
  const send = (code) => shareLink(rsAppLink(`rs-${code}`), "Дуэль в «Раскате»: каждый день одна головоломка на всю РХЛ. Сравним очки?");
  if (own) return send(own);
  if (btn) btn.disabled = true;
  rsApi("POST", "/duel")
    .then((r) => {
      if (!r || !/^[A-Za-z0-9-]{3,24}$/.test(r.code || "")) throw rsApiError("Зачёт не отдал ссылку. Попробуй ещё раз.");
      lsSet(rsKey(RS_OWN_KEY), r.code);
      send(r.code);
      if (state.tab === "raskat" && RS.seg === "board") rsPaintBody();
    })
    .catch((e) => {
      if (btn) btn.disabled = false;
      const box = btn && btn.closest(".rs-duel-ask");
      if (box) box.querySelector("p").textContent = e.message;
    });
}
function rsBot(start) {
  const bot = state.data && state.data.links && state.data.links.bot;
  if (!bot) return;
  const url = `${bot}?start=${start}`;
  if (inTelegram) tg.openTelegramLink(url);
  else window.open(url, "_blank", "noopener");
}
// Переключить сегмент из кода: полоса-шапка перерисовывается целиком, бегунок перетекает
function rsGoSeg(seg) {
  RS.seg = seg;
  RS.fin = false;
  rsPaint();
  const band = $("#screen .rs-band");
  if (band) band.scrollIntoView({ block: "start", behavior: calm() ? "auto" : "smooth" });
}
function rsAct(what, arg, el) {
  switch (what) {
    case "restart":
      haptic();
      if (RS.intro) rsIntroStop(true);
      return rsRestart();
    case "back":
      haptic();
      return rsBack();
    case "hint":
      haptic();
      return rsHintPress();
    case "hint-yes":
      haptic();
      return rsHintShow();
    case "hint-no":
      RS.askHint = false;
      rsAskPaint();
      if (RS.el) RS.el.board.focus({ preventScroll: true });
      return undefined;
    case "intro-skip":
      haptic();
      rsIntroStop(true);
      if (RS.el) RS.el.board.focus({ preventScroll: true });
      return undefined;
    case "intro":
      haptic();
      closeMatch();
      RS.sheetOpen = false;
      if (RS.seg !== "ice") { RS.seg = "ice"; rsPaint(); }
      if (RS.el) RS.el.board.scrollIntoView({ block: "center", behavior: calm() ? "auto" : "smooth" });
      return void setTimeout(rsIntroStart, 280);
    case "rules":
      haptic();
      RS.sheetOpen = false;
      return showSheet(rsRulesSheet());
    case "fin-close":
      haptic();
      return rsFinClose();
    case "share":
      haptic();
      return rsShare();
    case "to-board":
      haptic();
      RS.board = "day";
      return rsGoSeg("board");
    case "to-ice":
      haptic();
      return rsGoSeg("ice");
    case "duel-new":
      haptic();
      return rsDuelNew(el);
    case "waitlist":
      haptic();
      return rsBot("raskat");
    case "retry":
      haptic();
      RS.status = "";
      RS.loading = null;
      RS.srv = rsFreshSrv();
      return rsPaint();
    case "srv-retry":
      haptic();
      RS.srv.fail = {};
      RS.srv.at = {};
      return rsPaintBody();
    case "today": {
      const d = rsLatest();
      if (!d) return;
      haptic();
      RS.seg = "ice";
      return rsOpen(d.date).then(rsPaint);
    }
    case "train":
    case "set":
      haptic();
      return rsOpenSheet();
    case "train-open": {
      if (!arg) return;
      haptic();
      closeMatch();
      RS.sheetOpen = false;
      RS.seg = "ice";
      return rsOpen(arg).then(() => { rsPaint(); const b = $("#screen .rs-band"); if (b) b.scrollIntoView({ block: "start" }); });
    }
    case "set-name":
    case "set-msg": {
      const k = what === "set-name" ? "name" : "messages";
      haptic();
      rsSetSetting(k, !rsSettings()[k]);
      return rsRedrawSheet();
    }
    case "del":
      haptic();
      RS.ask = true;
      rsRedrawSheet();
      // подтверждение внизу того же листа: подводим к нему, а не оставляем искать
      return void $("#sheet").scrollTo({ top: $("#sheet").scrollHeight, behavior: calm() ? "auto" : "smooth" });
    case "del-no":
      RS.ask = false;
      return rsRedrawSheet();
    case "del-yes":
      haptic();
      return rsDelete();
    default:
      return undefined;
  }
}

// Нажатия «Раската» ловим раньше app.js (фаза захвата) и дальше не пускаем
document.addEventListener("click", (e) => {
  const seg = e.target.closest("[data-rs-seg]");
  if (seg) {
    e.stopPropagation();
    if (seg.dataset.rsSeg === RS.seg) return;
    RS.seg = seg.dataset.rsSeg;
    RS.fin = false;
    rsIntroStop(false);
    haptic();
    // сегмент стоит в полосе-шапке, а перерисовываем мы тело: выбор переставляем на месте,
    // бегунок перетекает к новой кнопке
    const group = seg.parentNode;
    const prev = runnerState(group.parentNode)[group.dataset.run];
    group.querySelectorAll("[data-rs-seg]").forEach((b) => {
      b.classList.toggle("on", b === seg);
      b.setAttribute("aria-pressed", b === seg);
    });
    placeRunner(group, prev);
    return nextFrame(rsPaintBody);
  }
  const chip = e.target.closest("[data-rs-board]");
  if (chip) {
    e.stopPropagation();
    if (chip.dataset.rsBoard === RS.board) return;
    RS.board = chip.dataset.rsBoard;
    haptic();
    return rsPaintBody();
  }
  const el = e.target.closest("[data-rs]");
  if (!el || el.disabled) return;
  e.stopPropagation();
  rsAct(el.dataset.rs, el.dataset.rsArg || "", el);
}, true);

// Поле с клавиатуры: стрелки ведут шайбу, пробел, Enter и Backspace — шаг назад. Esc закрывает табло
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && RS.fin && $("#rs-fin") && !$("#rs-fin").hidden && $("#sheet").hidden) {
    e.preventDefault();
    e.stopPropagation();
    return void rsFinClose();
  }
  const board = e.target && e.target.closest ? e.target.closest("#rs-board") : null;
  if (!board || !RS.day || RS.solved) return;
  if (RS.intro) rsIntroStop(true);
  const head = RS.path[RS.path.length - 1];
  const w = RS.day.w;
  const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -w, ArrowDown: w }[e.key];
  if (step !== undefined) {
    e.preventDefault();
    e.stopPropagation();
    // слева от левого края — не соседняя клетка: rsStep сам скажет «только в соседнюю»
    const to = head + step;
    if (rsSide(head, to, RS.day)) return void rsStep(to);
    return void rsTell("Дальше — край поля", "miss");
  }
  if (e.key === " " || e.key === "Enter" || e.key === "Backspace") {
    e.preventDefault();
    e.stopPropagation();
    rsBack();
  }
}, true);

window.addEventListener("resize", () => { if (state.tab === "raskat" && RS.el) rsLayout(); });

// Точка на вкладке при запуске, пока «Раскат» не открывали: index.json маленький, расклад не качаем
async function rsPeek() {
  if (RS.status || rsMockMode()) return;
  try {
    const r = await fetch("data/raskat/index.json", { cache: "no-cache" });
    const idx = r.ok ? await r.json() : null;
    if (!idx || !Array.isArray(idx.days) || !idx.days.length || RS.status) return;
    RS.idx = idx;
    rsDue();
  } catch (e) { /* точки не будет — не страшно */ }
}

// Ссылка из бота или от друга: startapp=raskat и startapp=rs-<код> (дуэль)
function rsLinkParam(sp) {
  if (!sp) return null;
  if (sp === "raskat") return { tab: "raskat" };
  const m = /^rs-([A-Za-z0-9-]{3,24})$/.exec(sp);
  if (m) return { tab: "raskat", duel: m[1] };
  return null;
}
function rsFromLink(link) {
  if (!link) return;
  if (link.duel) {
    state.rsDuel = link.duel;
    lsSet(rsKey(RS_DUEL_KEY), link.duel);
    RS.seg = "board";
  }
}
// Код дуэли ждёт открытия зачёта
function rsRestoreDuel() {
  if (!state.rsDuel && lsGet(rsKey(RS_DUEL_KEY))) state.rsDuel = lsGet(rsKey(RS_DUEL_KEY));
}

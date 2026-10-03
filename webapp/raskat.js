"use strict";
// «Раскат» (ADR-018) — головоломка дня: одна шайба проходит весь лёд и задевает номера звена
// по порядку. Интерфейс — DESIGN.md → «Раскат», стыки частей — docs/raskat/contract.md.
// Файл грузится перед app.js и зовёт его общие помощники (esc, $, lsGet/lsSet, cloud, showSheet,
// haptic, calm, guideFig, emblem, segBtn, placeRunners, shareLink) только из отрисовки
// и обработчиков: на верхнем уровне здесь — свои константы.
//
// Без сервера (window.RASKAT_API пуст) поле, время, подсказка и личные записи работают полностью,
// а в сегменте «Зачёт» — честная карточка ожидания: ни пустых таблиц, ни выдуманных мест.
// Для разработки: ?raskat_mock=1 — зачёт на устройстве (data/raskat/mock/api.js),
// =solved — раскат дня уже собран, =none — файлов нет.
// Всё, что пришло из данных, вставляем только через esc(). Решения в данных нет: путь проверяем
// сами по правилам контракта (раздел 1), а подсказку считает решатель на устройстве.

const RS_DAYS_KEY = "rs_days";   // {дата: {ms, points, total, hint, path, train}} — на устройстве и в облаке
const RS_HINT_KEY = "rs_hint";   // дата, в которую подсказку уже брали: она одна в день
const RS_WOW_KEY = "rs_wow";     // дата, в которую «вау» уже показали: он один раз в день
const RS_SET_KEY = "rs_set";     // {name, messages} — настройки зачёта
const RS_DUEL_KEY = "rs_duel";   // код дуэли из ссылки rs-<код>, пока зачёта нет

// Очки — docs/raskat/contract.md, раздел 4. Формула повторена один в один в raskat/points.py:
// любая правка меняет оба места и таблицу примеров в tests/test_raskat_points.py.
// Очки дня (база + время − подсказка) ранжируют зачёт дня, серия в них не входит: иначе у новичка
// потолок ниже, чем у того, кто играет неделю. Серия живёт в личном итоге дня
const RS_BASE = 60;         // за собранный раскат, одинаково для всех
const RS_TIME = 40;         // за время
const RS_RUN = 10;          // за серию, не больше
const RS_HINT_COST = 20;    // подсказка
const RS_HINT_MS = 1500;    // клетка горит 1,5 с
const RS_TRAIN_MAX = 14;    // столько прошлых дней показываем в тренировке
const RS_CLUB_MIN = 5;      // порог кубка клубов: столько болельщиков клуба за день

const RS_I = {
  bell: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/></svg>',
  share: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 4 3 11l6.5 2.5L12 20z"/><path d="m9.5 13.5 4-4"/></svg>',
  again: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4.5 12a7.5 7.5 0 1 0 2.6-5.7"/><path d="M4 5v4h4"/></svg>',
  hint: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5v2M5.2 6.2l1.4 1.4M18.8 6.2l-1.4 1.4M9.5 20h5"/><path d="M12 8a4 4 0 0 1 2.4 7.2V17h-4.8v-1.8A4 4 0 0 1 12 8Z"/></svg>',
  chev: '<svg class="chev" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg>',
  trash: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 7h14M9 7V5h6v2M7 7l1 13h8l1-13"/></svg>',
  // Шайба на острие ленты: чёрный эллипс с белой каймой, как на заставке
  puck: '<svg viewBox="0 0 120 80" aria-hidden="true"><path d="M8 30v18a52 22 0 0 0 104 0V30z" fill="#000" stroke="#fff" stroke-width="9" stroke-linejoin="round" paint-order="stroke"/><ellipse cx="60" cy="30" rx="52" ry="22" fill="#000" stroke="#fff" stroke-width="5"/></svg>',
};

const RS = {
  idx: null, status: "", loading: null,     // index.json: "" — не грузили, ok, none — файлов нет, fail
  date: "", day: null, dayFail: "",         // открытый расклад
  train: false,                             // открыт прошлый день: в зачёт не идёт
  seg: "ice", board: "day",                 // сегмент экрана и чип зачёта
  path: [], drag: false, solved: false, hint: false, lit: -1,
  t0: 0, ms: 0, tick: 0, res: null, geo: null, el: null, missAt: 0,
  srv: { me: undefined, day: {}, clubs: {}, duel: undefined, fail: "", loading: {} },
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
  rsRestart(false);
  const rec = rsRecords()[date];
  RS.hint = lsGet(rsKey(RS_HINT_KEY)) === date;
  if (rec && Array.isArray(rec.path) && !rsCheck(d, rec.path)) {
    RS.path = rec.path.slice();
    RS.solved = true;
    RS.ms = rec.ms || 0;
    RS.res = rec;
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
    }
    if (!changed) return;
    lsSet(rsKey(RS_DAYS_KEY), JSON.stringify(mine));
    if (RS.date && mine[RS.date] && !RS.solved) rsOpen(RS.date).then(rsPaint);
    else rsPaint();
  });
}
function rsSettings() {
  try { return Object.assign({ name: false, messages: true }, JSON.parse(lsGet(rsKey(RS_SET_KEY)) || "{}")); } catch (e) { return { name: false, messages: true }; }
}
function rsSetSetting(k, v) {
  const s = rsSettings();
  s[k] = v;
  lsSet(rsKey(RS_SET_KEY), JSON.stringify(s));
  if (rsServer()) rsApi("PUT", "/settings", { show_tg_name: !!s.name, messages: !!s.messages }).catch(() => {});
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

// Решатель для подсказки: достраивает путь от нынешнего хвоста. Решения в данных нет и быть не должно
// (контракт, раздел 2), поэтому считаем сами — поле маленькое, с отсечениями это десятки миллисекунд
function rsSolve(d, prefix) {
  const n = rsCells(d);
  const adj = [];
  for (let i = 0; i < n; i += 1) {
    adj.push([i - d.w, i + d.w, i - 1, i + 1].filter((j) => rsOpenEdge(i, j, d)));
  }
  const seen = new Array(n).fill(false);
  const path = [];
  for (const c of prefix) {
    if (c == null || c < 0 || c >= n || seen[c]) return null;
    if (path.length && !rsOpenEdge(path[path.length - 1], c, d)) return null;
    seen[c] = true;
    path.push(c);
  }
  if (!path.length) return null;
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
  return walk();
}

// ---------- ведение ----------

function rsRestart(repaint = true) {
  RS.path = RS.day ? [RS.day.dots[0]] : [];
  RS.solved = false;
  RS.lit = -1;
  RS.t0 = 0;
  RS.ms = 0;
  RS.res = null;
  RS.drag = false;
  rsStopTick();
  if (repaint) {
    rsPaintBody();
    rsSay("Поле заново, шайба на номере 1.");
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
const RS_MISS = { wall: "Здесь борт — объезжай.", busy: "Клетка уже пройдена.", order: "Сначала номер" };
// Попытка пройти борт или занятую клетку: вздрагивание и лёгкий отклик. Ошибки как состояния нет
function rsMiss(why, from, to) {
  if (!RS.el) return;
  const now = Date.now();
  if (now - RS.missAt < 220) return;
  RS.missAt = now;
  const el = why === "wall" ? RS.el.walls.querySelector(`[data-w="${rsWallKey(from, to)}"]`) : RS.el.ribbon;
  if (el && !calm()) el.animate([{ transform: "scale(1)" }, { transform: "scale(1.04)" }, { transform: "scale(1)" }], { duration: 200, easing: EASE_OUT });
  if (inTelegram && tg.HapticFeedback) tg.HapticFeedback.impactOccurred("light");
  rsSay(why === "order" ? `${RS_MISS.order} ${rsPassed() + 1}.` : RS_MISS[why] || "");
}
// Шаг шайбы. Возврат на предыдущую клетку снимает хвост — отмена тем же движением назад
function rsStep(to) {
  if (!RS.day || RS.solved || to < 0) return false;
  const head = RS.path[RS.path.length - 1];
  if (to === head) return false;
  if (RS.path.length > 1 && to === RS.path[RS.path.length - 2]) {
    RS.path.pop();
    rsMoved(`Шаг назад, ${rsWhere(to)}.`);
    return true;
  }
  const why = rsWhy(head, to);
  if (why) {
    if (why !== "far") rsMiss(why, head, to);
    return false;
  }
  RS.path.push(to);
  const num = rsNumOf(to);
  rsMoved(`${rsWhere(to)}${num ? `, номер ${num}` : ""}. Пройдено ${RS.path.length} из ${rsCells(RS.day)}.`);
  return true;
}
const rsWhere = (i) => `клетка ${(i % RS.day.w) + 1} по горизонтали, ${Math.floor(i / RS.day.w) + 1} по вертикали`;
const rsWhereIn = (i) => rsWhere(i).replace("клетка", "клетке");
function rsMoved(text) {
  if (!RS.t0) {
    RS.t0 = Date.now();   // время идёт от первого касания поля, без пауз (контракт, раздел 4)
    rsStartTick();
  }
  RS.lit = -1;
  rsDraw();
  rsSay(text);
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
  const i = rsCellAt(e);
  if (i < 0) return;
  const head = RS.path[RS.path.length - 1];
  e.preventDefault();
  RS.el.board.focus({ preventScroll: true });
  if (i !== head && !rsStep(i)) return;
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
  if (bad) return rsSay(bad);
  RS.solved = true;
  RS.drag = false;
  RS.ms = RS.t0 ? Date.now() - RS.t0 : RS.ms;
  rsStopTick();
  const before = rsStreak(rsShift(RS.date, -1));   // дни серии до сегодняшнего — их и ждёт формула
  const streak = RS.train ? 0 : before + 1;        // с сегодняшним: эту серию видит болельщик
  const points = rsDayPoints(RS.day.par, RS.ms / 1000, RS.hint);   // очки дня: по ним зачёт
  const total = RS.train ? points : rsPoints(RS.day.par, RS.ms / 1000, before, RS.hint);
  const rec = { ms: RS.ms, points, total, hint: RS.hint, path: RS.path.slice() };
  if (RS.train) rec.train = true;
  const all = rsRecords();
  const was = all[RS.date];
  // Тренировку храним лучшим временем, раскат дня — как собрали: он идёт в зачёт один раз
  if (!was || !RS.train || RS.ms < was.ms) all[RS.date] = rec;
  rsSaveRecords(all);
  RS.res = Object.assign({ streak }, all[RS.date], { points, total, ms: RS.ms, hint: RS.hint });
  rsSay(`Раскат собран за ${rsClock(RS.ms)}, ${rsPts(total)}.`);
  if (inTelegram && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
  if (!RS.train && rsServer()) rsSend();
  rsDraw();
  rsAfterPaint();
  rsDue();
}
// Результат дня уходит на сервер один раз; тренировка не уходит вовсе (контракт, раздел 5)
function rsSend() {
  rsApi("POST", `/day/${RS.date}`, { path: RS.path, ms: RS.ms, hint: RS.hint })
    .then((r) => {
      if (!r) return;
      RS.res = Object.assign({}, RS.res, r);
      RS.srv.day = {};
      rsAfterPaint();
    })
    .catch(() => { /* зачёт подождёт: результат уже на устройстве */ });
}
function rsAfterPaint() {
  const score = $("#rs-score");
  const cta = $("#rs-cta");
  if (!score || !cta) return;
  score.innerHTML = rsScoreHTML();
  cta.innerHTML = rsCtaHTML();
  rsWow(score);
}
// «Вау» — один раз в день: лента пробегает слева направо, шайба влетает в ворота, встаёт табло
function rsWow(score) {
  const once = lsGet(rsKey(RS_WOW_KEY)) !== RS.date && !RS.train;
  if (!RS.train) lsSet(rsKey(RS_WOW_KEY), RS.date);
  const puckTo = () => rsGoal(!calm());
  if (!once || calm()) {
    puckTo();
    return;
  }
  const trail = RS.el && RS.el.trail;
  if (trail) {
    // лента рисуется заново, как на заставке: единственный «вау» этого экрана
    trail.forEach((p) => {
      const len = p.getTotalLength();
      p.animate([{ strokeDasharray: `${len} ${len}`, strokeDashoffset: len }, { strokeDasharray: `${len} ${len}`, strokeDashoffset: 0 }],
        { duration: 600, easing: EASE_OUT });
    });
  }
  setTimeout(puckTo, 560);
  score.animate([{ opacity: 0, transform: "translateY(16px)" }, { opacity: 1, transform: "none" }], { duration: 260, delay: 420, easing: EASE_OUT, fill: "backwards" });
}

// Шайба в воротах у края поля: так выглядит собранный раскат — и сразу после финиша, и при
// следующем заходе в этот день
function rsGoal(animate) {
  if (!RS.el || !RS.geo) return;
  RS.el.goal.classList.add("on");
  RS.el.puck.style.transition = animate ? "transform .34s cubic-bezier(.4, 0, 1, 1)" : "none";
  RS.el.puck.style.transform = `translate(${RS.geo.w + 5}px, ${RS.geo.h / 2}px) translate(-50%, -50%)`;
}

function rsHintPress() {
  if (!RS.day || RS.solved) return;
  if (RS.hint) return rsSay("Подсказка в день одна.");
  const sol = rsSolve(RS.day, RS.path);
  if (!sol) {
    rsMiss("busy", -1, -1);
    return rsSay("Отсюда весь лёд уже не пройти — нажми «Заново».");
  }
  RS.hint = true;
  lsSet(rsKey(RS_HINT_KEY), RS.date);
  RS.lit = sol[RS.path.length];
  if (!RS.t0) { RS.t0 = Date.now(); rsStartTick(); }
  rsDraw();
  rsSay(`Подсказка: ${rsWhere(RS.lit)}.`);
  const lit = RS.lit;
  setTimeout(() => {
    if (RS.lit !== lit) return;
    RS.lit = -1;
    rsDraw();
  }, RS_HINT_MS);
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
function rsSay(text) {
  const el = $("#rs-say");
  if (el && text) el.textContent = text;
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
function rsFail(text) {
  const fig = guideFig(state.fav, "shrug");
  return `<div class="empty${fig ? " guide-empty" : ""}">${fig}<div>${esc(text)}<br><button type="button" class="retry" data-rs="retry">Повторить</button></div></div>`;
}
function rsNone() {
  return rsGuideCard("shrug", "Раскаты ещё не опубликованы. Первая головоломка появится вместе с началом сезона — загляни позже.")
    + rsWaitCta() + rsRules();
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
function rsRules() {
  return `<div class="label">Правила<span class="aside">как собрать</span></div>
    <div class="rs-rules">
      <p>Веди шайбу пальцем по клеткам: нужно пройти <b>весь лёд</b> и задеть номера звена
      <b>по порядку</b> — сначала 1, потом 2 и до последнего.</p>
      <p>Ход — только в клетку по стороне. Через борт нельзя, в пройденную клетку тоже.
      Возврат на предыдущую клетку снимает хвост.</p>
      <p>Расклад один для всех и меняется в полночь по Москве.</p>
    </div>`;
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
  const back = RS.train ? '<button type="button" class="rs-pill" data-rs="today">К раскату дня</button>' : "";
  return `<div class="rs-ice">
      <div class="rs-top"><span class="rs-lede">${lede}</span><span class="rs-timer num" id="rs-timer">${rsClock(RS.solved ? RS.ms : 0)}</span></div>
      ${rsFieldHTML(d)}
    </div>
    <p class="sr-only" id="rs-say" aria-live="polite" role="status"></p>
    <div id="rs-score">${RS.solved ? rsScoreHTML() : ""}</div>
    <div id="rs-cta">${RS.solved ? rsCtaHTML() : ""}</div>
    <div class="rs-acts">
      <button type="button" class="rs-pill" data-rs="restart">${RS_I.again}Заново</button>
      <button type="button" class="rs-pill" data-rs="hint"${RS.hint || RS.solved ? " disabled" : ""}>${RS_I.hint}Подсказка</button>
      ${back}
    </div>
    ${Object.keys(rsRecords()).length ? "" : rsRules()}
    <div class="foot">Расклад один для всех и открывается в 00:00 по Москве. Время идёт от первого касания поля.</div>`;
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
      <div class="rs-walls" id="rs-walls" aria-hidden="true">${walls}</div>
      <i class="rs-goal" id="rs-goal" aria-hidden="true"></i>
      <i class="rs-puck" id="rs-puck" aria-hidden="true">${RS_I.puck}</i>
      <div class="rs-nums" id="rs-nums" aria-hidden="true">${nums}</div>
    </div>`;
}
function rsBoardLabel() {
  const d = RS.day;
  if (!d) return "Поле «Раската»";
  const head = RS.path[RS.path.length - 1];
  return esc(`Поле ${d.w} на ${d.h}, шайба в ${rsWhereIn(head == null ? d.dots[0] : head)}. Стрелки ведут шайбу, пробел — шаг назад`);
}

// Табло: время и итог дня крупно, серия — строкой. Очки дня (без серии) — в зачёте дня
function rsScoreHTML() {
  const r = RS.res || {};
  const streak = RS.train ? 0 : (r.streak || rsStreak(RS.date));
  const bonus = Math.min(RS_RUN, 2 * Math.max(0, streak - 1));
  const total = r.total != null ? r.total : r.points || 0;
  const place = r.place ? `<span class="rs-place">${esc(String(r.place))}-е место из ${esc(String(r.of || 0))}</span>` : "";
  const note = [RS.train ? "тренировка · в зачёт не идёт" : "", r.hint ? "с подсказкой" : ""].filter(Boolean).join(" · ");
  const run = streak
    ? `<span class="rs-run">серия ${streak} ${plural(streak, "день", "дня", "дней")}${bonus ? ` · +${bonus} к итогу` : ""}</span>`
    : "";
  return `<div class="rs-score" role="group" aria-label="Раскат собран">
    <div class="rs-score-top">
      <div class="rs-time"><b class="num">${esc(rsClock(r.ms || RS.ms))}</b><small>время</small></div>
      <div class="rs-got"><b class="num">${esc(String(total))}</b><small>${plural(total, "очко", "очка", "очков")}</small></div>
    </div>
    <div class="rs-score-foot">${run}${place}${note ? `<span class="rs-note">${esc(note)}</span>` : ""}</div>
    ${r.points != null && r.points !== total ? `<div class="rs-score-say">В зачёт дня идут <b>${esc(String(r.points))}</b> — серия считается отдельно.</div>` : ""}
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
    marks: $("#rs-marks"),
    puck: $("#rs-puck"),
    goal: $("#rs-goal"),
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
  RS.el.puck.style.transition = calm() || !RS.t0 ? "none" : "";
  RS.el.puck.style.transform = `translate(${rsCX(head).toFixed(1)}px, ${rsCY(head).toFixed(1)}px) translate(-50%, -50%)`;
  const on = new Set(RS.path);
  RS.el.cells.forEach((el, i) => {
    el.classList.toggle("on", on.has(i));
    el.classList.toggle("lit", i === RS.lit);
  });
  const passed = rsPassed();
  RS.el.nums.forEach((b, k) => {
    b.classList.toggle("done", k < passed);
    b.classList.toggle("next", k === passed && !RS.solved);
    // наклейка под шайбой не гаснет: иначе сквозь неё просвечивает шайба и номер не прочесть
    b.classList.toggle("head", Number(b.dataset.n) === head && !RS.solved);
  });
  RS.el.board.setAttribute("aria-label", rsBoardLabel());
  RS.el.board.classList.toggle("done", RS.solved);
}

// Перерисовать «Раскат» на месте: прокрутка и бегунки остаются
function rsPaint() {
  if (state.tab !== "raskat" || !state.fav) return;
  const screen = $("#screen");
  const prev = runnerState(screen);
  const y = window.scrollY;
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
  box.innerHTML = rsSegBody();
  placeRunners(box, prev);
  rsMounted();
}
// После каждой отрисовки: слои поля, точка на вкладке
function rsMounted() {
  rsStopTick();
  rsMount();
  rsDue();
  if (RS.seg === "board" && rsServer()) rsBoardLoad();
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

// ---------- зачёт ----------

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
    throw new Error("Нет связи с зачётом. Проверь интернет и попробуй ещё раз.");
  }
  let d = null;
  try { d = await r.json(); } catch (e) { d = null; }
  if (!r.ok) throw new Error((d && typeof d.error === "string" && d.error) || "Зачёт не ответил. Попробуй ещё раз.");
  return d;
}
function rsBoardLoad(force = false) {
  const date = rsLatest() ? rsLatest().date : "";
  const code = lsGet(rsKey(RS_DUEL_KEY));
  if (code && RS.srv.duel === undefined && !RS.srv.loading.duel) {
    RS.srv.loading.duel = true;
    rsApi("GET", `/duel/${encodeURIComponent(code)}`)
      .then((d) => { RS.srv.duel = d || null; if (state.tab === "raskat" && RS.seg === "board") rsPaintBody(); })
      .catch(() => { RS.srv.duel = null; });
  }
  const key = RS.board === "clubs" ? `clubs:${date}` : RS.board === "day" ? `day:${date}` : "me";
  if (!date || (RS.srv.loading[key] && !force)) return;
  RS.srv.loading[key] = true;
  RS.srv.fail = "";
  const ask = RS.board === "clubs" ? rsApi("GET", `/clubs/${date}`) : RS.board === "day" ? rsApi("GET", `/day/${date}`) : rsApi("GET", "/me");
  ask.then((d) => {
    if (RS.board === "clubs") RS.srv.clubs[date] = d || {};
    else if (RS.board === "day") RS.srv.day[date] = d || {};
    else RS.srv.me = d || null;
    if (state.tab === "raskat" && RS.seg === "board") rsPaintBody();
  }).catch((e) => {
    RS.srv.loading[key] = false;
    RS.srv.fail = e.message;
    if (state.tab === "raskat" && RS.seg === "board") rsPaintBody();
  });
}

function rsBoardBody() {
  const tools = `<div class="rs-tools">
      <button type="button" class="rs-pill" data-rs="train">Тренировка</button>
      <button type="button" class="rs-pill" data-rs="set">Настройки</button>
    </div>`;
  if (!rsServer()) return rsWaitBoard() + tools;
  const date = rsLatest() ? rsLatest().date : "";
  const chips = `<div class="chips rs-chips" role="group" aria-label="Что показать" data-run="rs-board">${RUN}
      ${segBtn(RS.board === "day", 'data-rs-board="day"', "День")}
      ${segBtn(RS.board === "clubs", 'data-rs-board="clubs"', "Кубок клубов")}
      ${segBtn(RS.board === "streak", 'data-rs-board="streak"', "Серия")}
    </div>`;
  let body;
  if (RS.srv.fail) body = rsFail(RS.srv.fail);
  else if (RS.board === "day") body = rsDayTable(RS.srv.day[date]);
  else if (RS.board === "clubs") body = rsClubsTable(RS.srv.clubs[date]);
  else body = rsStreakBody();
  return chips + rsDuelCard() + body + tools;
}
// Без сервера таблиц нет вовсе: честная карточка ожидания и кнопка в бота
function rsWaitBoard() {
  const duel = lsGet(rsKey(RS_DUEL_KEY));
  const say = `Зачёт ещё не открылся: мест и таблиц пока нет, и выдумывать их мы не станем.
    Раскат дня, время и личные записи уже работают — всё на этом устройстве.${duel ? "<br><b>Тебя позвали на дуэль</b> — она откроется вместе с зачётом." : ""}`;
  return rsGuideCard("shrug", say) + rsWaitCta() + rsStreakBody();
}
function rsDuelCard() {
  const code = lsGet(rsKey(RS_DUEL_KEY));
  if (!code || !RS.srv.duel) return "";
  const d = RS.srv.duel;
  if (!Array.isArray(d.days)) return "";
  const rows = d.days.slice(-7).map((x) => `<div class="rs-row"><span class="nm">${esc(fmtLong(x.date))}</span>
    <b class="num">${esc(String(x.me == null ? "—" : x.me))}</b><span class="vs">:</span><b class="num">${esc(String(x.them == null ? "—" : x.them))}</b></div>`).join("");
  return `<div class="label">Дуэль<span class="aside">${esc(d.name || "")}</span></div><div class="rs-table">${rows}</div>`;
}
function rsDayTable(d) {
  if (!d) return rsSkeleton();
  const top = Array.isArray(d.top) ? d.top : [];
  if (!top.length) return `<div class="empty">Сегодня раскат ещё никто не собрал. Будь первым.</div>`;
  const mine = top.some((r) => r.me);
  let html = `<div class="label">Зачёт дня<span class="aside">${esc(String(d.of || top.length))} ${plural(d.of || top.length, "болельщик", "болельщика", "болельщиков")}</span></div>`;
  html += `<div class="rs-table">${top.map(rsDayRow).join("")}`;
  // своя строка не уезжает из виду: нет в списке — пунктир и своя строка под ним
  if (!mine && d.me) html += `<div class="rs-cut" aria-hidden="true"></div>${rsDayRow(Object.assign({ me: true }, d.me))}`;
  html += "</div>";
  html += '<div class="foot">В зачёте дня — очки дня: собранный раскат, время и подсказка. Серия в них не входит, она в твоём итоге и в своём зачёте.</div>';
  return html;
}
function rsDayRow(r) {
  const name = r.name ? esc(r.name) : `Болельщик ${r.club ? `«${esc(team(r.club).name)}»` : ""}`;
  const pts = r.points;   // в таблице дня — очки дня, итог с серией живёт на табло
  return `<div class="rs-row${r.me ? " me" : ""}">
    <span class="pos num">${esc(String(r.place || "—"))}</span>
    ${r.club ? emblem(r.club) : ""}
    <span class="nm">${name}${r.hint ? '<small>с подсказкой</small>' : ""}</span>
    <b class="pts num">${esc(String(pts != null ? pts : 0))}</b>
    <span class="tm num">${esc(rsClock(r.ms || 0))}</span>
  </div>`;
}
// Клуб ниже порога из приложения не исчезает: вместо среднего — честная строка (контракт, раздел 4)
function rsClubRow(r, season = false) {
  const fans = r.fans || 0;
  const low = fans < RS_CLUB_MIN;
  const avg = `<b class="pts num">${esc(String(Math.round((r.avg || 0) * 10) / 10))}</b>`;
  return `<div class="rs-row${r.me ? " me" : ""}">
      <span class="pos num">${esc(String(low ? "—" : r.place || "—"))}</span>${emblem(r.club)}
      <span class="nm">${esc(team(r.club).name)}<small>${low ? `собрали ${fans}, кубок считается от ${RS_CLUB_MIN}` : `${fans} ${plural(fans, "болельщик", "болельщика", "болельщиков")}${season && r.days ? ` · по ${r.days} ${plural(r.days, "дню", "дням", "дням")}` : ""}`}</small></span>
      ${low ? "" : avg}</div>`;
}
function rsClubsTable(d) {
  if (!d) return rsSkeleton();
  // клубы ниже порога сервер шлёт отдельным списком; старый формат — те же строки внутри day
  const low = [].concat(Array.isArray(d.low) ? d.low : [], Array.isArray(d.under) ? d.under : []);
  const day = (Array.isArray(d.day) ? d.day : []).concat(low);
  if (!day.length) return `<div class="empty">Кубок клубов считается, когда за клуб собрали раскат хотя бы ${RS_CLUB_MIN} болельщиков.</div>`;
  const rows = day.filter((r) => (r.fans || 0) >= RS_CLUB_MIN).slice(0, 10);
  const mine = !rows.some((r) => r.me) && day.find((r) => r.me);
  const season = Array.isArray(d.season) ? d.season.slice(0, 10) : [];
  return `<div class="label">Кубок клубов<span class="aside">среднее очков дня</span></div>
    <div class="rs-table">${mine ? rsClubRow(mine) + '<div class="rs-cut" aria-hidden="true"></div>' : ""}${rows.map((r) => rsClubRow(r)).join("")}</div>
    ${season.length ? `<div class="label">За сезон<span class="aside">среднее дневных средних</span></div><div class="rs-table">${season.map((r) => rsClubRow(r, true)).join("")}</div>` : ""}
    <div class="foot">Считается среднее, а не сумма: иначе кубок каждый день выигрывает клуб, у которого болельщиков в разы больше, а не самый упорный. Сезонный кубок — среднее дневных средних.</div>`;
}
// Серия — ряд дней кружками: залитый собран, пустой с контуром пропущен
function rsStreakBody() {
  const all = rsRecords();
  const last = rsLatest() ? rsLatest().date : rsToday();
  const days = [];
  for (let i = 6; i >= 0; i -= 1) {
    const date = rsShift(last, -i);
    const rec = all[date];
    days.push({ date, on: !!rec && !rec.train, future: date > last });
  }
  const n = (RS.srv.me && RS.srv.me.streak) || rsStreak(last);
  const dow = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
  const row = days.map((x) => `<span class="rs-dayc${x.on ? " on" : ""}"><i></i><small>${esc(dow[parseISO(x.date).getUTCDay()])}</small></span>`).join("");
  return `<div class="label">Серия<span class="aside">неделя</span></div>
    <div class="rs-week" role="img" aria-label="${esc(`Серия ${n} ${plural(n, "день", "дня", "дней")}`)}">${row}</div>
    <div class="rs-week-say">Серия — <b>${n} ${plural(n, "день", "дня", "дней")}</b>. Пропущенный день обнуляет серию, тренировка прошлого дня её не восстанавливает.</div>`;
}

// ---------- лист тренировки и настроек ----------

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
      <span><b>Имя из Telegram в зачёте</b><small>${s.name ? "Показываем имя" : "В таблице — «Болельщик «клуба»»"}</small></span>${sw()}</button>
    <button type="button" class="menu-row rs-set-row" data-rs="set-msg" aria-pressed="${s.messages}">
      <span><b>Сообщения о зачёте</b><small>Бот напишет, когда зачёт откроется</small></span>${sw()}</button>
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
    <div class="rs-note">Тренировка в зачёт не идёт и серию не продолжает — это просто лёд для разминки.</div>
    <div class="menu">${rows}</div>
    <div class="label">Настройки «Раската»</div>
    <div class="menu rs-set">${set}</div>
    ${ask}`;
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
  [RS_DAYS_KEY, RS_HINT_KEY, RS_WOW_KEY].forEach((k) => {
    try { localStorage.removeItem(rsKey(k)); } catch (e) { /* приватный режим */ }
  });
  if (cloud() && !rsMockMode()) cloud().removeItem(RS_DAYS_KEY, () => {});
  if (rsServer()) rsApi("DELETE", "/me").catch(() => {});
  RS.ask = false;
  RS.srv = { me: undefined, day: {}, clubs: {}, duel: undefined, fail: "", loading: {} };
  RS.hint = false;
  closeMatch();
  RS.sheetOpen = false;
  if (RS.date) rsOpen(RS.date).then(rsPaint);
  else rsPaint();
}

// ---------- нажатия ----------

function rsShare() {
  const d = RS.day;
  const r = RS.res || {};
  const app = state.data && state.data.links && state.data.links.app;
  const link = app ? `${app}?startapp=raskat` : `${location.origin}${location.pathname}?startapp=raskat`;
  const what = RS.train ? "Тренировочный раскат" : `Раскат № ${d ? d.n : ""}`;
  shareLink(link, `${what} — ${rsClock(r.ms || RS.ms)}, ${rsPts(r.total != null ? r.total : r.points || 0)}${r.hint ? ", с подсказкой" : ""}. Собери свой!`);
}
function rsBot(start) {
  const bot = state.data && state.data.links && state.data.links.bot;
  if (!bot) return;
  const url = `${bot}?start=${start}`;
  if (inTelegram) tg.openTelegramLink(url);
  else window.open(url, "_blank", "noopener");
}
function rsAct(what, arg, el) {
  switch (what) {
    case "restart":
      haptic();
      return rsRestart();
    case "hint":
      haptic();
      return rsHintPress();
    case "share":
      haptic();
      return rsShare();
    case "waitlist":
      haptic();
      return rsBot("raskat");
    case "retry":
      haptic();
      RS.status = "";
      RS.loading = null;
      RS.srv.fail = "";
      RS.srv.loading = {};
      return rsPaint();
    case "today": {
      const d = rsLatest();
      if (!d) return;
      haptic();
      return rsOpen(d.date).then(rsPaintBody);
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
      return rsOpen(arg).then(rsPaint);
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
    RS.srv.fail = "";
    haptic();
    return rsPaintBody();
  }
  const el = e.target.closest("[data-rs]");
  if (!el || el.disabled) return;
  e.stopPropagation();
  rsAct(el.dataset.rs, el.dataset.rsArg || "", el);
}, true);

// Поле с клавиатуры: стрелки ведут шайбу, пробел и Enter отменяют шаг назад
document.addEventListener("keydown", (e) => {
  const board = e.target && e.target.closest ? e.target.closest("#rs-board") : null;
  if (!board || !RS.day || RS.solved) return;
  const head = RS.path[RS.path.length - 1];
  const w = RS.day.w;
  const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -w, ArrowDown: w }[e.key];
  if (step !== undefined) {
    e.preventDefault();
    e.stopPropagation();
    return void rsStep(head + step);
  }
  if (e.key === " " || e.key === "Enter") {
    e.preventDefault();
    e.stopPropagation();
    if (RS.path.length > 1) rsStep(RS.path[RS.path.length - 2]);
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

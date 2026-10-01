/* «Наш лёд» (ADR-017): свой клуб и захват карты лиги.
 *
 * Это Пролог: сервера ещё нет, поэтому всё живёт на устройстве, а исходы матчей, которые лига пока
 * не играла, придуманы и так и подписаны — «демо». Когда появится сервер (раздел 13 ADR-017), на его
 * место встанут настоящие исходы, чужие расстановки и зачёты, а экраны останутся теми же.
 */

// ---------- правила (ADR-017, разделы 3–5) ----------

const L_FORCES = 10;          // силы на тур — у всех одинаково, не копятся
const L_CAP = 4;              // не больше сил на один матч
const L_BASE = 10;            // очки за верную силу: знание исхода
const L_ZONE = 10;            // надбавка силе, которая взяла или удержала зону
const L_RARE = 5;             // надбавка за редкость стороны
const L_TAPS = 24;            // касаний на полную полосу поддержки
const L_KEY = "led";          // всё состояние Пролога — одним ключом на устройстве

const L_LEVELS = [
  [0, "Коробка во дворе"], [40, "Борта и сетка"], [110, "Табло"],
  [220, "Трибуны"], [380, "Свет и музыка"], [600, "Аншлаг"],
];
const L_ADJ = ["Ледовые", "Стальные", "Северные", "Дерзкие", "Вольные", "Быстрые", "Крылатые", "Молодые"];
const L_NOUN = ["Ястребы", "Бураны", "Кометы", "Рыси", "Витязи", "Торпедо", "Соколы", "Метели", "Зубры", "Шайбы"];
const L_SHAPES = ["shield", "disc", "rhomb"];
const L_COLORS = ["#4da2ff", "#55db9c", "#e9ccff", "#fb4903", "#ffd731", "#5c4ade"];

const L_STAR_D = "m50 6 12.5 27 29.5 3.5-22 20 6 29.5L50 71 23.5 86l6-29.5-22-20L37 33z";
const L_I = {
  force: '<svg class="l-puck" viewBox="0 0 24 16" aria-hidden="true"><path d="M2 5.5v5a10 4.5 0 0 0 20 0v-5z"/><ellipse cx="12" cy="5.5" rx="10" ry="4.5"/></svg>',
  star: `<svg class="l-star" viewBox="0 0 100 100" aria-hidden="true"><path d="${L_STAR_D}"/></svg>`,
  plus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 6v12M6 12h12"/></svg>',
  minus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 12h12"/></svg>',
};

// Рантайм: что открыто сейчас. Сохраняемое состояние — в L.st
const L = { seg: "map", st: null, week: null, result: null, link: false };

// ---------- состояние на устройстве ----------

function ledFresh() {
  return { v: 1, club: null, zones: {}, forces: {}, done: {}, support: {}, stickers: [], xp: 0, hello: false };
}

function ledSt() {
  if (L.st) return L.st;
  try {
    const raw = JSON.parse(localStorage.getItem(L_KEY) || "null");
    L.st = raw && raw.v === 1 ? Object.assign(ledFresh(), raw) : ledFresh();
  } catch (e) {
    L.st = ledFresh();
  }
  return L.st;
}

function ledSave() {
  try {
    localStorage.setItem(L_KEY, JSON.stringify(ledSt()));
  } catch (e) { /* приватный режим — играем без сохранения */ }
}

function ledWipe() {
  L.st = ledFresh();
  L.result = null;
  L.week = 0;
  try {
    localStorage.removeItem(L_KEY);
  } catch (e) { /* нечего стирать */ }
}

// ---------- туры: неделя Пн–Вс, сетка как у «Звена» (ADR-014, раздел 3) ----------

function ledMonday(iso) {
  const d = new Date(iso + "T00:00:00Z");
  const back = (d.getUTCDay() + 6) % 7;
  d.setUTCDate(d.getUTCDate() - back);
  return d.toISOString().slice(0, 10);
}

let ledWeeksCache = null;
function ledWeeks() {
  if (ledWeeksCache) return ledWeeksCache;
  const by = new Map();
  for (const g of games()) {
    if (!g.date) continue;
    const key = ledMonday(g.date);
    if (!by.has(key)) by.set(key, []);
    by.get(key).push(g);
  }
  const keys = [...by.keys()].sort();
  ledWeeksCache = keys.map((key, i) => {
    const to = new Date(key + "T00:00:00Z");
    to.setUTCDate(to.getUTCDate() + 6);
    const list = by.get(key).slice().sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
    return { key, n: i + 1, from: key, to: to.toISOString().slice(0, 10), games: list };
  });
  return ledWeeksCache;
}

// Первая неделя, которая ещё не прокручена; дальше — по кнопке «Следующий тур»
function ledWeekIndex() {
  const ws = ledWeeks();
  const st = ledSt();
  if (L.week !== null) return Math.min(L.week, ws.length - 1);
  const today = new Date().toISOString().slice(0, 10);
  let i = ws.findIndex((w) => w.to >= today);
  if (i < 0) i = 0;
  while (i < ws.length - 1 && st.done[ws[i].key]) i += 1;
  return i;
}

const ledWeek = () => ledWeeks()[ledWeekIndex()] || null;

function ledDates(w) {
  const MON = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  const a = new Date(w.from + "T00:00:00Z");
  const b = new Date(w.to + "T00:00:00Z");
  const same = a.getUTCMonth() === b.getUTCMonth();
  return `${a.getUTCDate()}${same ? "" : " " + MON[a.getUTCMonth()]}–${b.getUTCDate()} ${MON[b.getUTCMonth()]}`;
}

function ledDay(iso) {
  const DAY = ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"];
  const MON = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
  const d = new Date(iso + "T00:00:00Z");
  return `${DAY[d.getUTCDay()]}, ${d.getUTCDate()} ${MON[d.getUTCMonth()]}`;
}

// ---------- демо-исходы: лига этих матчей ещё не играла ----------

function ledHash(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 10000) / 10000;
}

// Сила клуба в демо — устойчивая за сезон, чтобы исходы не выглядели монеткой
const ledPower = (club) => 0.35 + ledHash("p:" + club) * 0.3;

function ledProb(g) {
  if (g.score) return g.score[0] > g.score[1] ? 1 : 0;
  const d = ledPower(g.home) - ledPower(g.away);
  return Math.min(0.85, Math.max(0.15, 0.5 + d + 0.06));
}

const ledReal = (g) => !!g.score;
const ledWin = (g) => (ledReal(g) ? (g.score[0] > g.score[1] ? "h" : "a") : (ledHash("w:" + g.id) < ledProb(g) ? "h" : "a"));

// Куда давила бы лига: доля стороны. В Прологе считается, на сервере — настоящая
function ledShare(g, side) {
  const p = ledProb(g);
  const herd = Math.pow(p, 3) / (Math.pow(p, 3) + Math.pow(1 - p, 3));
  const noise = (ledHash("c:" + g.id) - 0.5) * 0.12;
  const h = Math.min(0.92, Math.max(0.08, herd + noise));
  return side === "h" ? h : 1 - h;
}

// Давление стороны: болельщики клубов с поправкой на размер фанбазы (раздел 5).
// В Прологе у каждого клуба 8 активных болельщиков, и ты — один из них
function ledPress(g, side, mine = 0) {
  const base = 2 + ledShare(g, side) * 8 + (side === "h" ? 1 : 0);   // свои трибуны помогают
  return base + mine * 0.6;
}

// ---------- зоны ----------

const ledOwner = (zone) => ledSt().zones[zone] || zone;

function ledZoneList() {
  const ids = Object.keys(state.teams);
  const byConf = (c) => ids.filter((id) => team(id).conf === c).sort((a, b) => team(a).name.localeCompare(team(b).name, "ru"));
  return { west: byConf("west"), east: byConf("east") };
}

function ledHeld(club) {
  return Object.keys(state.teams).filter((z) => ledOwner(z) === club).length;
}

// ---------- расстановка сил ----------

const ledForcesOf = (key) => ledSt().forces[key] || {};
const ledOn = (key, id, side) => (ledForcesOf(key)[id] || {})[side] || 0;

function ledUsed(key) {
  let n = 0;
  for (const v of Object.values(ledForcesOf(key))) n += (v.h || 0) + (v.a || 0);
  return n;
}

const ledLeft = (key) => L_FORCES - ledUsed(key);

function ledPut(key, id, side, delta) {
  const st = ledSt();
  if (!st.forces[key]) st.forces[key] = {};
  const cell = st.forces[key][id] || { h: 0, a: 0 };
  const other = side === "h" ? "a" : "h";
  const want = cell[side] + delta;
  if (want < 0) return false;
  if (delta > 0 && (ledLeft(key) <= 0 || cell[side] + cell[other] >= L_CAP)) return false;
  cell[side] = want;
  st.forces[key][id] = cell;
  if (!cell.h && !cell.a) delete st.forces[key][id];
  ledSave();
  return true;
}

// Автопилот: публичное правило — сначала матчи своего клуба, остаток на фаворитов (раздел 9)
function ledAuto(w) {
  const st = ledSt();
  st.forces[w.key] = {};
  ledSave();
  const mine = w.games.filter((g) => g.home === state.fav || g.away === state.fav);
  for (const g of mine) {
    const side = g.home === state.fav ? "h" : "a";
    for (let i = 0; i < Math.min(L_CAP, ledLeft(w.key)); i += 1) ledPut(w.key, g.id, side, 1);
  }
  const rest = w.games.slice().sort((a, b) => Math.max(ledProb(b), 1 - ledProb(b)) - Math.max(ledProb(a), 1 - ledProb(a)));
  for (const g of rest) {
    if (ledLeft(w.key) <= 0) break;
    ledPut(w.key, g.id, ledProb(g) >= 0.5 ? "h" : "a", 1);
  }
}

// ---------- прокрутка тура ----------

function ledRun(w) {
  const st = ledSt();
  const f = ledForcesOf(w.key);
  const lines = [];
  const moves = [];
  let points = 0;
  for (const g of w.games) {
    const win = ledWin(g);
    const mine = f[g.id] || { h: 0, a: 0 };
    const pressWin = ledPress(g, win, mine[win] || 0);
    const pressLose = ledPress(g, win === "h" ? "a" : "h", mine[win === "h" ? "a" : "h"] || 0);
    const zone = g.home;
    const owner = ledOwner(zone);
    const enough = pressWin >= pressLose;
    let decided = false;
    if (win === "a" && enough && owner !== g.away) {
      st.zones[zone] = g.away;
      moves.push({ zone, from: owner, to: g.away });
      decided = true;
    } else if (win === "h" && enough) {
      decided = true;
      if (owner !== g.home) {
        st.zones[zone] = g.home;
        moves.push({ zone, from: owner, to: g.home });
      }
    }
    const u = mine[win] || 0;
    let got = 0;
    if (u) {
      const rare = L_RARE * (1 - ledShare(g, win));
      got = Math.round(u * (L_BASE + (decided ? L_ZONE : 0) + rare));
      points += got;
    }
    if (u || mine.h || mine.a) {
      lines.push({ id: g.id, win, u, wrong: (mine.h || 0) + (mine.a || 0) - u, got, decided, press: [pressWin, pressLose] });
    }
  }
  const xp = 15 + Math.round(points / 20);
  st.xp += xp;
  st.done[w.key] = { points, when: new Date().toISOString() };
  ledSave();
  return { key: w.key, n: w.n, points, xp, lines, moves };
}

// ---------- уровень своего клуба ----------

function ledLevel() {
  const xp = ledSt().xp;
  let i = 0;
  for (let k = 0; k < L_LEVELS.length; k += 1) if (xp >= L_LEVELS[k][0]) i = k;
  const next = L_LEVELS[i + 1] || null;
  return { i, name: L_LEVELS[i][1], xp, next: next ? next[0] : null, nextName: next ? next[1] : null };
}

function ledAddXp(n) {
  ledSt().xp += n;
  ledSave();
}

// ---------- свой клуб ----------

function ledClubOf() {
  const st = ledSt();
  if (st.club) return st.club;
  const seed = state.fav || "led";
  const t = team(state.fav);
  st.club = {
    name: `${L_ADJ[Math.floor(ledHash("a:" + seed) * L_ADJ.length)]} ${L_NOUN[Math.floor(ledHash("n:" + seed) * L_NOUN.length)]}`,
    shape: "shield",
    color: (t.colors && t.colors[0]) || L_COLORS[0],
  };
  ledSave();
  return st.club;
}

function ledRoll(what) {
  const c = ledClubOf();
  if (what === "name") {
    const a = L_ADJ[Math.floor(Math.random() * L_ADJ.length)];
    const n = L_NOUN[Math.floor(Math.random() * L_NOUN.length)];
    c.name = `${a} ${n}`;
  }
  if (what === "shape") c.shape = L_SHAPES[(L_SHAPES.indexOf(c.shape) + 1) % L_SHAPES.length];
  if (what === "color") c.color = L_COLORS[(L_COLORS.indexOf(c.color) + 1) % L_COLORS.length];
  ledSave();
}

// Светлый ли цвет: от этого зависит, какой буквой подписывать герб
function ledLight(hex) {
  const m = /^#?([\da-f]{6})$/i.exec(hex || "");
  if (!m) return true;
  const n = parseInt(m[1], 16);
  return (0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) > 150;
}

function ledCrest(c, size = 72) {
  const letter = esc((c.name || "?").trim().charAt(0).toUpperCase());
  const shapes = {
    shield: '<path d="M50 6 92 20v38c0 22-18 32-42 42C26 90 8 80 8 58V20z"/>',
    disc: '<circle cx="50" cy="50" r="44"/>',
    rhomb: '<path d="M50 4 96 50 50 96 4 50z"/>',
  };
  return `<span class="l-crest" style="width:${size}px;height:${size}px"><svg viewBox="0 0 100 100" aria-hidden="true">
    <g fill="${esc(c.color)}" stroke="var(--edge)" stroke-width="5">${shapes[c.shape] || shapes.shield}</g>
    <text x="50" y="50" text-anchor="middle" dominant-baseline="central" fill="${ledLight(c.color) ? "#000" : "#fff"}">${letter}</text></svg></span>`;
}

// Коробка, которая растёт до арены: уровень добавляет детали, но не силу (раздел 2)
function ledRink() {
  const lv = ledLevel().i;
  const on = (k) => (lv >= k ? "" : ' class="off"');
  return `<svg class="l-rink" viewBox="0 0 320 180" role="img" aria-label="Твоя коробка, уровень ${lv + 1}">
    <rect x="12" y="24" width="296" height="132" rx="56" fill="var(--sel)" stroke="var(--edge)" stroke-width="3"/>
    <path d="M160 24v132" stroke="var(--blue)" stroke-width="3"/><circle cx="160" cy="90" r="20" fill="none" stroke="var(--blue)" stroke-width="3"/>
    <g${on(1)} stroke="var(--edge)" stroke-width="3" fill="none"><rect x="4" y="16" width="312" height="148" rx="64"/></g>
    <g${on(2)}><rect x="124" y="2" width="72" height="22" rx="6" fill="var(--ink)"/><text x="160" y="14" text-anchor="middle" class="l-score">0 : 0</text></g>
    <g${on(3)} fill="var(--lavender)" stroke="var(--edge)" stroke-width="2">
      <rect x="30" y="158" width="52" height="14" rx="4"/><rect x="92" y="158" width="52" height="14" rx="4"/>
      <rect x="176" y="158" width="52" height="14" rx="4"/><rect x="238" y="158" width="52" height="14" rx="4"/></g>
    <g${on(4)} fill="var(--sun)" stroke="var(--edge)" stroke-width="2">
      <circle cx="60" cy="12" r="8"/><circle cx="260" cy="12" r="8"/></g>
    <g${on(5)} fill="var(--ember)" stroke="var(--edge)" stroke-width="2"><path d="M150 168h20l-10 10z"/></g>
  </svg>`;
}

// ---------- экран ----------

function renderLed() {
  if (!state.data) return ledBand() + '<div class="sk" style="height:320px"></div>';
  return ledBand() + `<div id="l-body">${ledBody()}</div>`;
}

function ledBand() {
  const w = ledWeek();
  return `<section class="band peach l-band"><h1>Наш лёд</h1>
    <div class="lede">Карта лиги на 26 зон. Каждый тур у всех одинаковые ${L_FORCES} сил: куда давишь — там и захват.</div>
    ${w ? `<div class="l-tourline">Тур ${w.n} · ${esc(ledDates(w))} · дедлайн пн 09:00 МСК</div>` : ""}</section>`;
}

function ledSegs() {
  const segs = [["map", "Карта"], ["tour", "Тур"], ["club", "Клуб"], ["leagues", "Лиги"]];
  return `<div class="seg" role="group" aria-label="Раздел игры" data-run="l-seg">${RUN}${segs
    .map(([k, v]) => segBtn(L.seg === k, `data-led="seg" data-led-arg="${k}"`, `<span>${v}</span>`)).join("")}</div>`;
}

function ledBody() {
  const views = { map: ledMapScreen, tour: ledTourScreen, club: ledClubScreen, leagues: ledLeaguesScreen };
  return ledSegs() + (ledSt().hello ? "" : ledHello()) + (views[L.seg] || ledMapScreen)();
}

function ledHello() {
  const g = (state.teams[state.fav] || {}).mascot;
  const fig = guideFig(state.fav, "hello");
  const text = g
    ? `Я ${esc(g.name)}. Наша зона — ${esc(team(state.fav).city)}, и она наша, пока мы её держим. Расставь силы на матчи тура: выездная победа отнимает чужую зону, домашняя — защищает свою.`
    : "Зона твоего города — твоя, пока вы её держите. Расставь силы на матчи тура: выездная победа отнимает чужую зону, домашняя защищает свою.";
  return `<div class="l-guide${fig ? "" : " bare"}">${fig}<p>${text}</p>
    <div class="l-guide-below"><button type="button" class="btn" data-led="hello">Понятно</button>
    <button type="button" class="l-pill" data-led="rules">Правила</button></div></div>`;
}

// ---------- карта ----------

function ledMapScreen() {
  const z = ledZoneList();
  const mine = ledHeld(state.fav);
  return `<div class="label">Карта лиги<span class="aside">${mine} ${ledPlural(mine, "зона", "зоны", "зон")} у «${esc(team(state.fav).name)}»</span></div>
    <div class="l-map">${ledHoney("Запад", z.west)}${ledHoney("Восток", z.east)}</div>
    <div class="l-legend"><span class="l-leg-own">${L_I.star}твоя зона</span><span class="l-leg-taken">кольцо — кто держит</span></div>
    <div class="foot">Зона начинается у своего клуба. Выездная победа — захват, домашняя — удержание. Карта обнуляется на второй круг.</div>`;
}

function ledPlural(n, one, few, many) {
  const a = n % 100;
  if (a > 10 && a < 20) return many;
  const b = n % 10;
  return b === 1 ? one : b >= 2 && b <= 4 ? few : many;
}

// Соты: 13 зон конференции рядами 3–4–3–3
function ledHoney(title, ids) {
  const rows = [3, 4, 3, 3];
  const W = 62, H = 54;
  let i = 0;
  let cells = "";
  rows.forEach((cnt, r) => {
    const off = (4 - cnt) * W / 2;
    for (let c = 0; c < cnt && i < ids.length; c += 1, i += 1) {
      cells += ledHex(ids[i], off + c * W + 4, r * H + 4, W, H);
    }
  });
  const h = rows.length * H + 12;
  return `<div class="l-honey"><div class="l-honey-t">${esc(title)}</div>
    <svg viewBox="0 0 ${4 * W + 8} ${h}" role="img" aria-label="Зоны конференции ${esc(title)}">${cells}</svg></div>`;
}

function ledHex(zone, x, y, W, H) {
  const t = team(zone);
  const owner = ledOwner(zone);
  const ot = team(owner);
  const own = owner === zone;
  const ring = (ot.colors && ot.colors[0]) || "var(--edge)";
  const w = W - 6, h = H + 6;
  const pts = [[w / 2, 0], [w, h / 4], [w, h * 3 / 4], [w / 2, h], [0, h * 3 / 4], [0, h / 4]].map((p) => p.join(",")).join(" ");
  const logo = t.logo ? `<image href="${esc(t.logo)}" x="${x + w / 2 - 15}" y="${y + h / 2 - 15}" width="30" height="30" preserveAspectRatio="xMidYMid meet"/>`
    : `<text x="${x + w / 2}" y="${y + h / 2}" text-anchor="middle" dominant-baseline="central" class="l-hex-ab">${esc(t.abbr)}</text>`;
  const star = zone === state.fav
    ? `<path class="l-hex-star" d="${L_STAR_D}" transform="translate(${x + w - 18} ${y + 2}) scale(.16)"/>` : "";
  const taken = own ? "" : `<circle cx="${x + 12}" cy="${y + h - 13}" r="10" fill="var(--paper)" stroke="${esc(ring)}" stroke-width="2.5"/>`
    + (ot.logo ? `<image href="${esc(ot.logo)}" x="${x + 5}" y="${y + h - 20}" width="14" height="14" preserveAspectRatio="xMidYMid meet"/>`
      : `<text x="${x + 12}" y="${y + h - 13}" text-anchor="middle" dominant-baseline="central" class="l-hex-ab" style="font-size:7px">${esc(ot.abbr)}</text>`);
  return `<g class="l-hex${own ? "" : " taken"}" data-led="zone" data-led-arg="${esc(zone)}" role="button" tabindex="0"
      aria-label="Зона ${esc(t.city)}, держит ${esc(ot.name)}">
      <polygon points="${pts}" transform="translate(${x} ${y})" fill="var(--paper)" stroke="${esc(ring)}" stroke-width="${own ? 2 : 4}"/>
      ${logo}${taken}${star}</g>`;
}

function ledZoneSheet(zone) {
  const t = team(zone);
  const ot = team(ledOwner(zone));
  const w = ledWeek();
  const here = w ? w.games.filter((g) => g.home === zone) : [];
  const own = ledOwner(zone) === zone;
  return `<div class="sheet-head"><span class="when">Зона ${esc(t.city)}</span>
    <button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    <div class="l-zone-head">${ledLogo(zone, 56)}<div><b>${esc(t.name)}</b>
      <span>${own ? "Зону держит свой клуб" : `Зону держит «${esc(ot.name)}»`}</span></div></div>
    ${here.length ? `<div class="label">Матчи тура здесь</div>${here.map(ledRow).join("")}`
      : '<div class="empty"><div>В этом туре здесь не играют — зона не меняется.</div></div>'}`;
}

function ledLogo(id, size = 40) {
  const t = team(id);
  return t.logo
    ? `<span class="l-em" style="--s:${size}px"><img src="${esc(t.logo)}" alt="" width="${size}" height="${size}" decoding="async"></span>`
    : `<span class="l-em ab" style="--s:${size}px">${esc(t.abbr)}</span>`;
}

// ---------- тур ----------

function ledTourScreen() {
  const w = ledWeek();
  if (!w) return '<div class="empty"><div>Матчей в данных нет.</div></div>';
  if (L.result && L.result.key === w.key) return ledResultScreen(w);
  if (ledSt().done[w.key]) return ledDoneScreen(w);
  const left = ledLeft(w.key);
  const byDay = new Map();
  for (const g of w.games) {
    if (!byDay.has(g.date)) byDay.set(g.date, []);
    byDay.get(g.date).push(g);
  }
  let list = "";
  for (const [date, gs] of byDay) {
    list += `<div class="l-day">${esc(ledDay(date))}</div>${gs.map((g) => ledMatch(g, w)).join("")}`;
  }
  return `<div class="l-bar"><div class="l-left"><b class="num">${left}</b><span>${ledPlural(left, "сила", "силы", "сил")} осталось</span></div>
      <div class="l-bar-act"><button type="button" class="l-pill" data-led="auto">Автопилот</button>
      ${ledUsed(w.key) ? '<button type="button" class="l-pill" data-led="clear">Снять все</button>' : ""}</div></div>
    <div class="label">Матчи тура ${w.n}<span class="aside">не больше ${L_CAP} сил на матч</span></div>
    ${list}
    <div class="l-run"><button type="button" class="btn" data-led="run"${ledUsed(w.key) ? "" : " disabled"}>Прокрутить тур</button>
      <p>В Прологе исходы матчей придуманы — лига их ещё не играла. На сервере здесь будут настоящие.</p></div>`;
}

function ledMatch(g, w) {
  const mine = state.fav === g.home || state.fav === g.away;
  const f = (ledForcesOf(w.key)[g.id] || { h: 0, a: 0 });
  const zoneOwner = team(ledOwner(g.home));
  return `<div class="l-match${mine ? " mine" : ""}">
    <div class="l-side-row">
      ${ledSideBtn(g, "h", f)}
      <div class="l-vs"><span>зона</span><b>${esc(team(g.home).city)}</b><small>${ledOwner(g.home) === g.home ? "у хозяев" : `у «${esc(zoneOwner.name)}»`}</small></div>
      ${ledSideBtn(g, "a", f)}
    </div>
    ${mine ? ledSupportRow(g) : ""}</div>`;
}

function ledSideBtn(g, side, f) {
  const id = side === "h" ? g.home : g.away;
  const t = team(id);
  const n = f[side] || 0;
  const tag = side === "h" ? '<span class="tag home">дома</span>' : '<span class="tag away">выезд</span>';
  return `<div class="l-side${n ? " on" : ""}">
    <button type="button" class="l-press" data-led="press" data-led-arg="${esc(g.id)}:${side}"
      aria-label="Давить за ${esc(t.name)}">${ledLogo(id, 36)}<span class="l-nm">${esc(t.name)}</span>${tag}</button>
    <div class="l-forces">${n ? `<button type="button" class="l-mini" data-led="unpress" data-led-arg="${esc(g.id)}:${side}" aria-label="Убрать силу">${L_I.minus}</button>` : ""}
      <span class="l-pucks" aria-label="${n} сил">${L_I.force.repeat(n) || '<i class="l-none">нет сил</i>'}</span></div></div>`;
}

function ledSupportRow(g) {
  const taps = ledSt().support[g.id] || 0;
  const full = taps >= L_TAPS;
  return `<button type="button" class="l-support${full ? " full" : ""}" data-led="tap" data-led-arg="${esc(g.id)}">
    <span class="l-support-t">${full ? "Трибуны полны — зона под защитой" : "Поддержка: держи зону своего клуба"}</span>
    <span class="l-support-bar"><i style="width:${Math.round(Math.min(1, taps / L_TAPS) * 100)}%"></i></span></button>`;
}

function ledTapSheet(id) {
  const g = games().find((x) => x.id === id);
  if (!g) return "";
  const taps = ledSt().support[id] || 0;
  const full = taps >= L_TAPS;
  const pct = Math.round(Math.min(1, taps / L_TAPS) * 100);
  return `<div class="sheet-head"><span class="when">Поддержка · ${esc(team(g.home).abbr)} — ${esc(team(g.away).abbr)}</span>
    <button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    <div class="l-tap-box">
      <p>${full ? "Полоса набрана. Больше в этом матче не нужно — у каждого одна полоса." : "Жми по шайбе, пока полоса не заполнится. Очков это не даёт: поддержка защищает зону клуба и приносит наклейку в альбом."}</p>
      <div class="l-support-bar big"><i style="width:${pct}%"></i></div>
      <button type="button" class="l-tap${full ? " done" : ""}" data-led="tap-hit" data-led-arg="${esc(id)}"${full ? " disabled" : ""}>
        ${L_I.force}<span>${full ? "Готово" : `${taps} / ${L_TAPS}`}</span></button>
      ${full ? `<div class="l-sticker">${ledLogo(state.fav, 48)}<b>Наклейка «Трибуны · ${esc(team(state.fav).city)}»</b></div>` : ""}
    </div>`;
}

function ledResultScreen(w) {
  const r = L.result;
  const rows = r.lines.map((l) => {
    const g = w.games.find((x) => x.id === l.id);
    const win = l.win === "h" ? g.home : g.away;
    return `<div class="l-res${l.got ? " got" : ""}">
      <div class="l-res-m">${ledLogo(win, 28)}<b>${esc(team(win).name)}</b>
        <span>${l.win === "a" ? "выездная победа" : "победа дома"}${ledReal(g) ? "" : " · демо"}</span></div>
      <div class="l-res-p">${l.got ? `+${l.got}` : "0"}</div>
      <div class="l-res-w">${l.u ? `${l.u} ${ledPlural(l.u, "сила", "силы", "сил")} верно${l.decided ? " · зона" : ""}` : `${l.wrong} ${ledPlural(l.wrong, "сила", "силы", "сил")} мимо`}</div></div>`;
  }).join("");
  const moves = r.moves.map((m) => `<div class="l-move">${ledLogo(m.to, 28)}<b>${esc(team(m.to).name)}</b>
    <span>зона — ${esc(team(m.zone).city)}${m.from === m.zone ? "" : `, была у «${esc(team(m.from).name)}»`}</span></div>`).join("");
  const next = ledWeeks()[ledWeekIndex() + 1];
  return `<div class="l-total"><b class="num">${r.points}</b><span>очков в туре ${r.n}</span><i>+${r.xp} к своей коробке</i></div>
    ${rows ? `<div class="label">За что начислено</div>${rows}` : '<div class="empty"><div>Силы в этом туре не стояли.</div></div>'}
    ${moves ? `<div class="label">Карта изменилась<span class="aside">${r.moves.length} ${ledPlural(r.moves.length, "захват", "захвата", "захватов")}</span></div>${moves}` : '<div class="foot">Зоны остались за хозяевами.</div>'}
    <div class="l-run">${next ? `<button type="button" class="btn" data-led="next">Следующий тур</button>` : ""}
      <button type="button" class="l-pill" data-led="seg" data-led-arg="map">Посмотреть карту</button></div>`;
}

function ledDoneScreen(w) {
  const d = ledSt().done[w.key];
  const next = ledWeeks()[ledWeekIndex() + 1];
  return `<div class="l-total"><b class="num">${d.points}</b><span>очков в туре ${w.n}</span></div>
    <div class="foot">Тур уже прокручен.</div>
    <div class="l-run">${next ? '<button type="button" class="btn" data-led="next">Следующий тур</button>' : ""}</div>`;
}

// ---------- клуб ----------

function ledClubScreen() {
  const c = ledClubOf();
  const lv = ledLevel();
  const pct = lv.next ? Math.round(Math.min(1, (lv.xp - L_LEVELS[lv.i][0]) / (lv.next - L_LEVELS[lv.i][0])) * 100) : 100;
  const st = ledSt();
  return `<div class="l-club">${ledCrest(c, 88)}
      <div class="l-club-n"><b>${esc(c.name)}</b><span>${esc(lv.name)} · уровень ${lv.i + 1}</span></div></div>
    <div class="l-club-act">
      <button type="button" class="l-pill" data-led="roll" data-led-arg="name">Другое название</button>
      <button type="button" class="l-pill" data-led="roll" data-led-arg="shape">Форма</button>
      <button type="button" class="l-pill" data-led="roll" data-led-arg="color">Цвет</button></div>
    ${ledRink()}
    <div class="l-xp"><i style="width:${pct}%"></i></div>
    <div class="foot">${lv.next ? `До «${esc(lv.nextName)}» — ${lv.next - lv.xp} очков участия.` : "Коробка выросла до арены."}
      Облик и статус, но не сила: силы каждый тур у всех одинаковые.</div>
    ${st.stickers.length ? `<div class="label">Наклейки</div><div class="l-stickers">${st.stickers.map((s) => `<span class="l-st">${esc(s)}</span>`).join("")}</div>` : ""}
    <div class="l-run"><button type="button" class="l-pill" data-led="wipe">Удалить мою игру</button></div>`;
}

// ---------- лиги ----------

function ledLeaguesScreen() {
  const held = Object.keys(state.teams).map((id) => [id, ledHeld(id)]).filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1] || team(a[0]).name.localeCompare(team(b[0]).name, "ru"));
  const rows = held.map(([id, n], i) => `<div class="l-hold${id === state.fav ? " me" : ""}">
    <span class="l-pl">${i + 1}</span>${ledLogo(id, 28)}<b>${esc(team(id).name)}</b><span class="num">${n}</span></div>`).join("");
  return `<div class="label">Кто держит лигу<span class="aside">зоны из 26</span></div>${rows}
    <div class="l-soon"><b>Ступени</b><p>Группы по 20 болельщиков своего уровня, пересбор раз в месяц, четверо лучших поднимаются. Включатся с тура 4, когда заработает сервер.</p></div>
    <div class="l-soon"><b>Своя лига</b><p>Лига по ссылке для друзей и клубная лига — там же. Призов в игре нет и не будет.</p></div>`;
}

// ---------- правила ----------

function ledRulesSheet() {
  return `<div class="sheet-head"><span class="when">Как играть</span>
    <button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    <div class="l-rules">
      <div class="l-rule"><span>Силы на тур<small>у всех одинаково, не копятся</small></span><b class="num">${L_FORCES}</b></div>
      <div class="l-rule"><span>На один матч<small>больше нельзя</small></span><b class="num">${L_CAP}</b></div>
      <div class="l-rule"><span>Верная сила<small>ты назвал исход</small></span><b class="num">+${L_BASE}</b></div>
      <div class="l-rule"><span>Зона<small>сила взяла или удержала</small></span><b class="num">+${L_ZONE}</b></div>
      <div class="l-rule"><span>Редкость<small>чем меньше людей с тобой</small></span><b class="num">+${L_RARE}</b></div>
      <p class="l-rules-note">Выездная победа отнимает зону города, домашняя — защищает. Силы на неверной стороне дают ноль: минусов в игре нет. Денег, призов и случайности нет.</p>
    </div>`;
}

// ---------- перерисовка ----------

function ledPaint() {
  if (state.tab !== "led" || !state.fav) return;
  const box = $("#l-body");
  if (!box) return;
  const prev = runnerState(box);
  const y = window.scrollY;
  box.innerHTML = ledBody();
  placeRunners(box, prev);
  window.scrollTo(0, y);
  ledMounted();
}

function ledPaintBand() {
  const band = document.querySelector(".l-band");
  if (band) band.outerHTML = ledBand();
}

// Точка «надо решить» на вкладке: силы не расставлены, а тур идёт
function ledMounted() {
  const svg = document.querySelector('#tabs [data-tab="led"] svg');
  if (!svg) return;
  const w = ledWeek();
  const due = !!w && !ledSt().done[w.key] && ledUsed(w.key) < L_FORCES;
  const dot = svg.querySelector(".due");
  if (due && !dot) svg.insertAdjacentHTML("beforeend", '<circle class="due" cx="20" cy="4.5" r="3.6"/>');
  if (!due && dot) dot.remove();
  svg.parentNode.setAttribute("aria-label", due ? "Наш лёд — есть что решить" : "Наш лёд");
}

// ---------- действия ----------

function ledAct(what, arg, el) {
  const w = ledWeek();
  if (what === "seg") {
    L.seg = arg;
    haptic();
    return ledPaint();
  }
  if (what === "hello") {
    ledSt().hello = true;
    ledSave();
    haptic();
    return ledPaint();
  }
  if (what === "rules") return showSheet(ledRulesSheet());
  if (what === "zone") return showSheet(ledZoneSheet(arg));
  if (what === "press" || what === "unpress") {
    const [id, side] = arg.split(":");
    const ok = ledPut(w.key, id, side, what === "press" ? 1 : -1);
    if (ok) haptic();
    ledPaint();
    return;
  }
  if (what === "auto") {
    ledAuto(w);
    haptic();
    return ledPaint();
  }
  if (what === "clear") {
    ledSt().forces[w.key] = {};
    ledSave();
    return ledPaint();
  }
  if (what === "run") {
    L.week = ledWeekIndex();        // тур закрыт, но остаёмся на нём: показываем итог
    L.result = ledRun(w);
    if (inTelegram && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
    L.seg = "tour";
    ledPaint();
    window.scrollTo({ top: 0, behavior: calm() ? "auto" : "smooth" });
    return;
  }
  if (what === "next") {
    L.result = null;
    L.week = Math.min(ledWeekIndex() + 1, ledWeeks().length - 1);
    ledPaintBand();
    return ledPaint();
  }
  if (what === "tap") return showSheet(ledTapSheet(arg));
  if (what === "tap-hit") {
    const st = ledSt();
    const n = (st.support[arg] || 0) + 1;
    st.support[arg] = Math.min(L_TAPS, n);
    if (st.support[arg] === L_TAPS) {
      const name = `Трибуны · ${team(state.fav).city}`;
      if (!st.stickers.includes(name)) st.stickers.push(name);
      ledAddXp(5);
      if (inTelegram && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
    } else {
      haptic();
    }
    ledSave();
    showSheet(ledTapSheet(arg));
    return;
  }
  if (what === "roll") {
    ledRoll(arg);
    haptic();
    return ledPaint();
  }
  if (what === "wipe") {
    if (el && el.dataset.sure !== "1") {
      el.dataset.sure = "1";
      el.textContent = "Точно удалить?";
      return;
    }
    ledWipe();
    return ledPaint();
  }
}

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-led]");
  if (!el || el.disabled) return;
  e.stopPropagation();
  ledAct(el.dataset.led, el.dataset.ledArg || "", el);
}, true);

document.addEventListener("keydown", (e) => {
  const el = e.target.closest && e.target.closest('[data-led="zone"]');
  if (el && (e.key === "Enter" || e.key === " ")) {
    e.preventDefault();
    ledAct("zone", el.dataset.ledArg || "");
  }
});

// ---------- совместимость с app.js ----------

function ledLinkParam(sp) {
  const v = sp && sp.get ? sp.get("startapp") : null;
  if (!v) return null;
  if (v === "led" || v === "zveno") return "led";
  return /^lg-[\w-]{1,24}$/.test(v) ? v : null;
}

function ledFromLink(link) {
  L.link = true;
  L.seg = link === "led" ? "map" : "leagues";
}

function ledRestoreJoin() { /* код лиги пригодится, когда появится сервер */ }

function ledPeek() {
  ledMounted();
}

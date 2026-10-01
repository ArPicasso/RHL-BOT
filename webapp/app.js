"use strict";
// Интерфейс — по DESIGN.md. Всё, что пришло из данных, вставляется только через esc().

// Скрипт Telegram грузится асинхронно и не держит запуск: tg появляется в initTelegram()
let tg = null;
let inTelegram = false;
const launchedInTelegram = /tgWebAppData=/.test(location.hash);
const TZ = "Europe/Moscow";
// 25.09.2026 выбор команды сброшен у всех (владелец продукта): каждый заново знакомится с талисманом
// своего клуба (ADR-011). Поэтому ключи новые, а старые стираем с устройства и из облака Telegram
const FAV_KEY = "fav";
const OLD_KEYS = ["fav_team", "tour_done", "guide_met", "splash_team"];
const REMIND_TEAM = "ryazan-vdv";   // напоминания бот пока шлёт только о её матчах
const THEME_KEY = "theme";          // "auto" | "light" | "dark", хранится на устройстве
const SPLASH_KEY = "splash";        // эмблема для заставки: её рисуют до загрузки данных
const SURFACE = { light: "#ffffff", dark: "#131922" };
const HEADER = { light: "#000000", dark: "#0b0f15" };   // в тон бегущей строке
const DATA_KEY = "league_cache";    // прошлые данные: повторный запуск рисуется сразу, свежие — в фоне
const SPLASH_MIN_MS = 1400;         // буквы приземляются к 500 мс, остальное — полюбоваться (DESIGN.md)
const SPLASH_REPEAT_MS = 600;       // повторный запуск с данными на устройстве: только приземление букв

const DOW = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
const MON_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
const MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const CONF = { east: "Восток", west: "Запад" };
const PLAYOFF_CUT = 8;
const PERIOD_NAMES = { "1": "1-й период", "2": "2-й период", "3": "3-й период", "ОТ": "Овертайм", "РБ": "Буллиты" };

const ICON = {
  home: '<svg viewBox="0 0 24 24"><path d="M4 10.5 12 4l8 6.5V20a1 1 0 0 1-1 1h-4.5v-6h-5v6H5a1 1 0 0 1-1-1z"/></svg>',
  away: '<svg viewBox="0 0 24 24"><path d="M3 12h13M12 6l6 6-6 6"/></svg>',
  close: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  back: '<svg viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7"/></svg>',
};
// «Надувная лента» Slush — плоская, без градиента: синяя трубка в чёрном контуре
const RIBBON = `<svg class="ribbon" viewBox="0 0 220 150" aria-hidden="true">
  <path class="o" d="M10 120 C 60 20, 110 150, 150 70 S 210 10, 230 40" stroke-width="30"/>
  <path class="f" d="M10 120 C 60 20, 110 150, 150 70 S 210 10, 230 40" stroke-width="27"/>
</svg>`;

const state = {
  data: null,
  teams: {},
  fav: null,
  draft: null,
  tab: "home",
  cal: { team: null, side: "all", conf: "all" },
  conf: "east",
  scrolledToNext: false,
  h2h: null,        // история очных встреч (ADR-006): грузится при первом открытии карточки матча
  tableView: "teams",   // «Таблица»: команды или лидеры лиги (ADR-009)
  leaders: null,    // лидеры лиги: грузятся при первом открытии «Игроков»
  feed: {},         // лист дня «Главной» по клубу (ADR-015): null — не загрузился
  feedSeen: {},     // прошлый заход по клубу, мс: отметки «новое» до конца сессии
  stream: undefined,    // лента лиги за неделю под листом: null — не загрузилась
  streamFilter: "all",  // «Все · Мой клуб · Соперник»
  streamShown: 0,       // сколько элементов ленты уже на экране: подгружаем порциями
};

const $ = (sel) => document.querySelector(sel);

// ---------- движение (DESIGN.md → «Движение») ----------

const EASE_OUT = "cubic-bezier(.2, .8, .2, 1)";   // приход: быстро и с торможением
const EASE_IN = "cubic-bezier(.4, 0, 1, 1)";      // уход: с разгоном
const calm = () => !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);
const nextFrame = (fn) => requestAnimationFrame(() => setTimeout(fn, 0));   // после того, как кадр нарисован

// Бегунок — заливка выбранного в меню, сегментах и чипах. Он из трёх частей (полукруг, середина,
// полукруг) и двигается только transform: анимацию ведёт видеокарта, и она идёт ровно, даже пока
// основной поток перерисовывает экран. Ширина — масштабом середины, полукруги не искажаются.
// Середина во всю ширину группы и только сжимается: узкую, растянутую в сотню раз, телефон
// на ходу не успевает нарисовать, и посреди бегунка появляется дыра
const RUN = '<i class="run" aria-hidden="true"><i class="l"></i><i class="m"></i><i class="r"></i></i>';
const runs = new WeakMap();   // бегунок → { from, to, anims }: чтобы подхватить его на лету

function runPose(r, cap, span) {
  return [
    `translateX(${r.x}px)`,
    `translateX(${r.x + cap - 0.5}px) scaleX(${Math.max(1, r.w - 2 * cap + 1) / span})`,
    `translateX(${r.x + r.w - cap}px)`,
  ];
}
// Где бегунок сейчас, с учётом идущей анимации
function runNow(run) {
  const st = runs.get(run);
  if (!st) return null;
  const a = st.anims && st.anims[0];
  if (!a || a.playState !== "running" || !st.from) return st.to;
  const p = a.effect.getComputedTiming().progress || 0;
  return { x: st.from.x + (st.to.x - st.from.x) * p, w: st.from.w + (st.to.w - st.from.w) * p };
}
function moveRun(run, to, animate, duration = 260) {
  const cap = run.offsetHeight / 2;
  const from = runNow(run);
  const st = runs.get(run);
  if (st && st.anims) st.anims.forEach((a) => a.cancel());
  const parts = [...run.children];
  const span = parts[1].offsetWidth || 1;
  const end = runPose(to, cap, span);
  parts.forEach((el, i) => { el.style.transform = end[i]; });
  const rec = { from, to, anims: null };
  if (animate && from && !calm() && (Math.abs(from.x - to.x) > 0.5 || Math.abs(from.w - to.w) > 0.5)) {
    const begin = runPose(from, cap, span);
    rec.anims = parts.map((el, i) => el.animate([{ transform: begin[i] }, { transform: end[i] }], { duration, easing: EASE_OUT }));
  }
  runs.set(run, rec);
}

// Группы с бегунком помечены data-run — по этому ключу бегунок находит своё прежнее место,
// когда группу перерисовали заново
function runnerState(root) {
  const m = {};
  root.querySelectorAll("[data-run] > .run").forEach((r) => {
    const now = runNow(r);
    if (now) m[r.parentNode.dataset.run] = now;
  });
  return m;
}
function placeRunner(group, from) {
  const run = group.querySelector(":scope > .run");
  const on = group.querySelector(":scope > button.on");
  if (!run || !on || !on.offsetWidth) return;
  if (from && !runs.has(run)) runs.set(run, { from: null, to: from, anims: null });
  moveRun(run, { x: on.offsetLeft, w: on.offsetWidth }, !!from);
  group.classList.add("ready");
}
function placeRunners(root, prev = {}) {
  root.querySelectorAll("[data-run]").forEach((g) => placeRunner(g, prev[g.dataset.run]));
}

// Меню: ширина вкладок меняется сразу, а глаз видит плавное — бегунок едет, иконки съезжают
// со старых мест (FLIP), подпись проявляется. Всё это transform и opacity
function setTab(tab, animate) {
  const nav = $("#tabs");
  const btns = [...nav.querySelectorAll("button")];
  const flip = animate && !calm() && !nav.hidden;
  const before = flip ? btns.map((b) => b.querySelector("svg").getBoundingClientRect().left) : null;
  btns.forEach((b) => {
    const on = b.dataset.tab === tab;
    b.classList.toggle("active", on);
    if (on) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  if (nav.hidden) return;
  const on = nav.querySelector("button.active");
  moveRun(nav.querySelector(".run"), { x: on.offsetLeft, w: on.offsetWidth }, animate, 300);
  nav.classList.add("ready");
  if (!flip) return;
  btns.forEach((b, i) => {
    const svg = b.querySelector("svg");
    svg.getAnimations().forEach((a) => a.cancel());
    const dx = before[i] - svg.getBoundingClientRect().left;
    if (Math.abs(dx) > 0.5) svg.animate([{ transform: `translateX(${dx}px)` }, { transform: "none" }], { duration: 300, easing: EASE_OUT });
  });
  on.querySelector("span").animate([{ opacity: 0, transform: "translateX(-6px)" }, { opacity: 1, transform: "none" }],
    { duration: 220, delay: 60, easing: EASE_OUT, fill: "backwards" });
}

// Цифры сезона на Главной досчитывают до значения — раз в день, не при каждой смене вкладки
const COUNT_KEY = "countup_day";
function countUp(root) {
  const els = root.querySelectorAll("[data-count]");
  if (!els.length || calm() || lsGet(COUNT_KEY) === todayISO()) return;
  lsSet(COUNT_KEY, todayISO());
  const t0 = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - t0) / 400);
    const e = 1 - Math.pow(1 - t, 3);
    els.forEach((el) => { el.textContent = String(Math.round(+el.dataset.count * e)); });
    if (t < 1) requestAnimationFrame(step);
  };
  els.forEach((el) => { el.textContent = "0"; });
  requestAnimationFrame(step);
}

function esc(v) {
  return String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------- даты: всё по Москве ----------

function todayISO() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: TZ }).format(new Date());
}
function parseISO(s) {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}
function daysFromToday(iso) {
  return Math.round((parseISO(iso) - parseISO(todayISO())) / 864e5);
}
function plural(n, one, few, many) {
  const a = n % 10, b = n % 100;
  if (a === 1 && b !== 11) return one;
  if (a >= 2 && a <= 4 && (b < 12 || b > 14)) return few;
  return many;
}
function until(iso) {
  const d = daysFromToday(iso);
  if (d === 0) return "сегодня";
  if (d === 1) return "завтра";
  if (d === -1) return "вчера";
  if (d < 0) return `${-d} ${plural(-d, "день", "дня", "дней")} назад`;
  return `через ${d} ${plural(d, "день", "дня", "дней")}`;
}
function fmtLong(iso) {
  const d = parseISO(iso);
  return `${DOW[d.getUTCDay()]}, ${d.getUTCDate()} ${MONTHS_GEN[d.getUTCMonth()]}`;
}

// ---------- хранилище любимой команды ----------

function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* приватный режим */ } }
function cloud() {
  return inTelegram && tg.CloudStorage && tg.isVersionAtLeast("6.9") ? tg.CloudStorage : null;
}
function rememberSplash(id) {
  const t = team(id);
  lsSet(SPLASH_KEY, JSON.stringify({ logo: t.logo || "", abbr: t.abbr || "" }));
}
function dropOldKeys() {
  if (lsGet("keys_v2") === "1") return;
  OLD_KEYS.forEach((k) => { try { localStorage.removeItem(k); } catch (e) { /* приватный режим */ } });
  if (!cloud()) return;   // облако стираем, когда Telegram уже подключился: ключ-отметку ставим только тогда
  cloud().removeItems(OLD_KEYS, () => {});
  lsSet("keys_v2", "1");
}
function saveFav(id) {
  lsSet(FAV_KEY, id);
  rememberSplash(id);
  if (cloud()) cloud().setItem(FAV_KEY, id, () => {});
}

// ---------- тема ----------

function themePref() {
  const v = lsGet(THEME_KEY);
  return v === "light" || v === "dark" ? v : "auto";
}
function hashTheme() {
  try {
    const m = /tgWebAppThemeParams=([^&]+)/.exec(location.hash);
    const bg = m && JSON.parse(decodeURIComponent(m[1])).bg_color;
    if (!bg || !/^#[0-9a-f]{6}$/i.test(bg)) return null;
    const n = parseInt(bg.slice(1), 16);
    return 0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255) < 128 ? "dark" : "light";
  } catch (e) {
    return null;
  }
}
function systemTheme() {
  if (inTelegram && tg.colorScheme) return tg.colorScheme;
  const fromHash = launchedInTelegram && hashTheme();
  if (fromHash) return fromHash;
  return window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
function applyTheme() {
  const theme = themeOf(themePref());
  document.documentElement.dataset.theme = theme;
  if (inTelegram && tg.isVersionAtLeast) {
    if (tg.isVersionAtLeast("6.1")) {
      tg.setHeaderColor(HEADER[theme]);
      tg.setBackgroundColor(SURFACE[theme]);
    }
    if (tg.isVersionAtLeast("7.10") && tg.setBottomBarColor) tg.setBottomBarColor(SURFACE[theme]);
  }
}
const SUN = `<svg class="i-sun" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4.5"/><path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8"/></svg>`;
const MOON = `<svg class="i-moon" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z"/></svg>`;
function toggleLabel() {
  return document.documentElement.dataset.theme === "dark" ? "Включить светлую тему" : "Включить тёмную тему";
}
// Кнопка-стикер в углу цветной шапки: видна на каждом экране, одно нажатие — светлая ↔ тёмная
function addThemeToggle() {
  const band = $("#screen .band");
  if (!band) return;
  band.classList.add("has-toggle");
  band.insertAdjacentHTML("afterbegin", `<button class="theme-toggle" data-theme-toggle type="button" aria-label="${toggleLabel()}">${SUN}${MOON}</button>`);
}
// Кнопки темы обновляются на месте, экран не перерисовывается
function syncThemeUI() {
  const pref = themePref();
  document.querySelectorAll("[data-theme-toggle]").forEach((b) => b.setAttribute("aria-label", toggleLabel()));
  document.querySelectorAll("[data-theme-pick]").forEach((b) => {
    const on = b.dataset.themePick === pref;
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", on);
  });
}
// Новая тема раскрывается кругом от нажатой кнопки: одна анимация снимка экрана вместо
// перехода цвета у каждого узла. Без View Transitions — прежний переход цвета, но только на
// небольших экранах: у «Всей лиги» тысячи узлов, там переключаем сразу
let themeAnimTimer = 0;
function switchTheme(pref, from) {
  const root = document.documentElement;
  lsSet(THEME_KEY, pref);
  const before = root.dataset.theme;
  const swap = () => { applyTheme(); syncThemeUI(); };
  if (calm() || themeOf(pref) === before) return swap();
  if (document.startViewTransition) {
    const r = from.getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    const R = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    const vt = document.startViewTransition(swap);
    vt.ready.then(() => root.animate(
      { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${R}px at ${x}px ${y}px)`] },
      { duration: 420, easing: EASE_OUT, pseudoElement: "::view-transition-new(root)" },
    )).catch(() => {});
    return;
  }
  if (document.getElementsByTagName("*").length < 2500) {
    root.classList.add("theme-anim");
    clearTimeout(themeAnimTimer);
    themeAnimTimer = setTimeout(() => root.classList.remove("theme-anim"), 400);
  }
  swap();
}
function themeOf(pref) {
  return pref === "auto" ? systemTheme() : pref;
}
function toggleTheme(btn) {
  if (inTelegram && tg.HapticFeedback) tg.HapticFeedback.impactOccurred("light");
  switchTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", btn);
}
function themePills() {
  const pref = themePref();
  const auto = launchedInTelegram ? "Как в Telegram" : "Как в системе";
  return `<div class="label">Оформление</div><div class="pills theme-pills" role="group" aria-label="Оформление">${[["auto", auto], ["light", "Светлое"], ["dark", "Тёмное"]]
    .map(([k, v]) => `<button class="${pref === k ? "on" : ""}" data-theme-pick="${k}" aria-pressed="${pref === k}">${v}</button>`)
    .join("")}</div>`;
}

// ---------- выборки ----------

const team = (id) => state.teams[id] || { id, abbr: "?", name: id, city: "" };
const games = () => state.data.games;
const gamesOf = (id) => games().filter((g) => g.home === id || g.away === id);
const isUpcoming = (g) => !g.score && g.date >= todayISO();
const nextGame = (id) => gamesOf(id).find(isUpcoming);
const lastPlayed = (id) => gamesOf(id).filter((g) => g.score).pop();

function standingOf(id) {
  for (const conf of Object.keys(state.data.standings)) {
    const i = state.data.standings[conf].findIndex((r) => r.team === id);
    if (i >= 0) return { conf, place: i + 1, row: state.data.standings[conf][i] };
  }
  return null;
}

function outcomeFor(g, me) {
  if (!g.score || (g.home !== me && g.away !== me)) return "";
  const mine = g.home === me ? g.score.home : g.score.away;
  const their = g.home === me ? g.score.away : g.score.home;
  return mine > their ? "w" : "l";
}

// ---------- элементы ----------

function emblem(id, size) {
  const t = team(id);
  const cls = `em${size ? " " + size : ""}`;
  if (t.logo) return `<span class="${cls}"><img src="${esc(t.logo)}" alt=""></span>`;
  return `<span class="${cls} ab" aria-hidden="true">${esc(t.abbr)}</span>`;
}

function resultPill(g) {
  const res = outcomeFor(g, state.fav);
  if (!res) return g.score && g.score.decision ? `<span class="res l">${esc(g.score.decision)}</span>` : "";
  const dec = g.score.decision ? `<small>${esc(g.score.decision)}</small>` : "";
  return `<span class="res ${res}" title="${res === "w" ? "Победа" : "Поражение"}">${res === "w" ? "В" : "П"}${dec}</span>`;
}

function whereTag(g, me) {
  if (me !== g.home && me !== g.away) return "";
  return g.home === me ? `<span class="tag home">${ICON.home}Дома</span>` : `<span class="tag away">${ICON.away}Выезд</span>`;
}

function board(g) {
  const side = (id) => `<div class="side">${emblem(id, "lg")}<div class="name">${esc(team(id).name)}</div><div class="city">${esc(team(id).city)}</div></div>`;
  let mid;
  if (g.score) {
    // в основное время подписи нет: счёт сам говорит, что матч сыгран
    const dec = { "ОТ": "овертайм", "Б": "буллиты" }[g.score.decision];
    mid = `<div class="score">${g.score.home}:${g.score.away}${dec ? `<span class="dec">${dec}</span>` : ""}</div>`;
  } else {
    mid = `<div class="score pending">${g.time ? esc(g.time) : "vs"}<span class="dec">${until(g.date)}</span></div>`;
  }
  return `<div class="board">${side(g.home)}${mid}${side(g.away)}</div>`;
}

function periodsLine(g) {
  if (!g.score || !g.score.periods || !g.score.periods.length) return "";
  return `<div class="periods">${g.score.periods.map((p) => `<span class="num">${p[0]}:${p[1]}</span>`).join("")}</div>`;
}

// Строка календаря одной команды: команда уже названа фильтром, в строке — только соперник
function gameRow(g, me, next) {
  const d = parseISO(g.date);
  const past = !!g.score;
  const home = g.home === me;
  const opp = home ? g.away : g.home;
  const where = home
    ? '<span class="where home">Дома</span>'
    : `<span class="where away">Выезд</span><span class="city">${esc(team(g.home).city)}</span>`;
  let right = `<span class="kick">${g.time ? esc(g.time) : ""}</span>`;
  if (past) {
    const mine = home ? g.score.home : g.score.away;
    const their = home ? g.score.away : g.score.home;
    const res = me === state.fav ? resultPill(g) : "";
    right = `<span class="sc num">${mine}:${their}</span>${res}`;
  }
  return `<div class="row one${past ? " past" : ""}${next ? " next" : ""}" data-game="${esc(g.id)}" data-date="${esc(g.date)}" role="button" tabindex="0"${next ? ' id="next-anchor"' : ""}>
    <div class="date"><b>${d.getUTCDate()}</b><span>${MON_SHORT[d.getUTCMonth()]} ${DOW[d.getUTCDay()]}</span></div>
    <div class="t"><div>${emblem(opp)}<span class="nm">${esc(team(opp).name)}</span></div><div class="sub">${where}</div></div>
    <div class="r">${right}</div>
  </div>`;
}

// Строка «Вся лига»: без даты — её даёт подзаголовок дня
function leagueRow(g) {
  const past = !!g.score;
  const lead = (id) => (id === g.home ? g.score.home > g.score.away : g.score.away > g.score.home);
  const goals = (id) => (past ? `<span class="gl${lead(id) ? " lead" : ""}">${id === g.home ? g.score.home : g.score.away}</span>` : "");
  const line = (id) => `<div>${emblem(id)}<span class="nm${id === state.fav ? " me" : ""}">${esc(team(id).name)}</span>${goals(id)}</div>`;
  const right = past ? resultPill(g) : `<span class="kick">${g.time ? esc(g.time) : ""}</span>`;
  return `<div class="row two${past ? " past" : ""}" data-game="${esc(g.id)}" role="button" tabindex="0">
    <div class="t">${line(g.home)}${line(g.away)}</div>
    <div class="r">${right}</div>
  </div>`;
}

function footer() {
  const upd = state.data.updated ? new Date(state.data.updated) : null;
  const when = upd ? upd.toLocaleString("ru-RU", { timeZone: TZ, day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" }) : "";
  return `<div class="foot">${esc(state.data.league)} · сезон ${esc(state.data.season)}${when ? `<br>Обновлено ${esc(when)} МСК` : ""}</div>`;
}

// ---------- экраны ----------

// Три цифры сезона: в сезоне — место, очки, форма; до старта — матчи, дом и выезд, дни до старта
function seasonStats(me, count = false) {
  const st = standingOf(me);
  const mine = gamesOf(me);
  // На Главной цифры досчитывают до значения (countUp), в паспорте — стоят
  const n = (v) => (count ? `<b data-count="${v}">${v}</b>` : `<b>${v}</b>`);
  if (st && st.row.gp) {
    const form = st.row.form.length ? `<span class="form">${st.row.form.map((f) => `<i class="${f}"></i>`).join("")}</span>` : "—";
    return `<div class="stat">${n(st.place)}<span>место<br>${CONF[st.conf]}</span></div>
      <div class="stat">${n(st.row.pts)}<span>${plural(st.row.pts, "очко", "очка", "очков")}<br>за ${st.row.gp} ${plural(st.row.gp, "игру", "игры", "игр")}</span></div>
      <div class="stat"><b>${form}</b><span>форма<br>5 игр</span></div>`;
  }
  if (!mine.length) return "";
  const home = mine.filter((g) => g.home === me).length;
  const days = Math.max(daysFromToday(mine[0].date), 0);
  return `<div class="stat">${n(mine.length)}<span>матчей<br>в сезоне</span></div>
    <div class="stat">${n(home)}<span>дома,<br>${mine.length - home} на выезде</span></div>
    <div class="stat">${n(days)}<span>${plural(days, "день", "дня", "дней")}<br>до старта</span></div>`;
}

// Самый длинный кусок названия, который нельзя перенести: по нему подбирается кегль шапки
function longestChunk(name) {
  return Math.max(...name.split(/\s+/).flatMap((w) => w.split(/(?<=-)/)).map((x) => x.length));
}

function renderHome() {
  const me = state.fav;
  const t = team(me);
  const next = nextGame(me);
  const last = lastPlayed(me);

  let html = `<section class="band sky">${RIBBON}
    <div class="hero"><button type="button" class="hero-em" data-switch-open aria-label="Сменить команду">${emblem(me, "xl")}</button><div><h1 style="--w:${longestChunk(t.name)}">${esc(t.name)}</h1><div class="meta">${esc(t.city)} · ${CONF[t.conf] || ""}</div></div></div>
  </section>`;

  const stats = seasonStats(me, true);
  if (stats) html += `<div class="stats">${stats}</div>`;

  if (next) {
    const today = daysFromToday(next.date) === 0;
    html += `<div class="label">Следующий матч${next.n ? `<span class="aside">№ ${esc(next.n)}</span>` : ""}</div>
      <div class="board-card tap" data-game="${esc(next.id)}" role="button" tabindex="0">
        <div class="board-top">
          <span class="tags">${whereTag(next, me)}${today ? '<span class="tag today">Сегодня</span>' : ""}${next.official ? "" : '<span class="tag soft">предварительно</span>'}</span>
          <span class="when">${esc(fmtLong(next.date))}</span>
        </div>
        ${board(next)}
      </div>`;
  } else {
    html += `<div class="label">Следующий матч</div><div class="empty">Матчей регулярного чемпионата больше нет</div>`;
  }
  // ряд «Сегодня» — под табло: на 320×568 над ним он вытеснял табло с первого экрана (ADR-015, 01.10)
  html += `<div id="packs">${packsHTML()}</div>`;

  if (last) {
    html += `<div class="label">Последний результат</div>
      <div class="board-card tap" data-game="${esc(last.id)}" role="button" tabindex="0">
        <div class="board-top"><span class="tags">${whereTag(last, me)}${resultPill(last)}</span><span class="when">${esc(fmtLong(last.date))}</span></div>
        ${board(last)}
        ${periodsLine(last)}
      </div>`;
  }

  html += `<div id="feed">${feedHTML(me)}</div><div id="stream-wrap">${streamHTML()}</div>`;
  return html + footer();
}

// Три ближайшие игры после следующей: на «Главной» без листа дня — пока он не загрузился или его нет
function upcomingBlock(me) {
  const upcoming = gamesOf(me).filter(isUpcoming).slice(1, 4);
  if (!upcoming.length) return "";
  return `<div class="label">Дальше<button type="button" class="aside link" data-tab="calendar">Весь календарь</button></div>
    <div class="list">${upcoming.map((g) => gameRow(g, me, false)).join("")}</div>`;
}

// ---------- лист дня (ADR-015) ----------
// Лист собирает build_data.py: data/feed/<клуб>.json. Здесь только прячем просроченное и скрытые
// каналы, отмечаем новое с прошлого захода и рисуем. Всё из поста — через esc(), ссылки не кликабельны

const FEED_SEEN_KEY = "feed_seen";       // когда в последний раз видели лист: { клуб: ISO }
const FEED_HIDDEN_KEY = "feed_hidden";   // скрытые каналы { адрес: название }, на устройстве и в облаке
const POST_URL = /^https:\/\/t\.me\/\w+\/\d+$/;
const POST_IMG = /^https:\/\/cdn\d*\.telesco\.pe\//;
const STAR_SUN = '<svg class="fc-star" viewBox="0 0 100 100" aria-hidden="true"><path d="m50 6 12.5 27 29.5 3.5-22 20 6 29.5L50 71 23.5 86l6-29.5-22-20L37 33z"/></svg>';
const DOTS = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>';
const CKIND = { club: "канал клуба", academy: "канал академии", system: "канал клуба", league: "канал лиги" };
const feedLoading = {};

function loadFeed(club) {
  if (!feedLoading[club]) {
    feedLoading[club] = fetch(`data/feed/${encodeURIComponent(club)}.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.status))))
      .then((d) => (state.feed[club] = d))
      .catch(() => { state.feed[club] = null; return null; });
  }
  return feedLoading[club];
}

function readJSON(key) {
  try { return JSON.parse(lsGet(key) || "{}") || {}; } catch (e) { return {}; }
}
function feedHidden() { return readJSON(FEED_HIDDEN_KEY); }
function saveFeedHidden(v) {
  const s = JSON.stringify(v);
  lsSet(FEED_HIDDEN_KEY, s);
  if (cloud()) cloud().setItem(FEED_HIDDEN_KEY, s, () => {});
}

// Время прошлого захода берём один раз за сессию: перерисовки не гасят отметки «новое»
function feedSeenPrev(club) {
  if (!(club in state.feedSeen)) {
    const all = readJSON(FEED_SEEN_KEY);
    state.feedSeen[club] = all[club] ? Date.parse(all[club]) || 0 : 0;
    all[club] = new Date().toISOString();
    lsSet(FEED_SEEN_KEY, JSON.stringify(all));
  }
  return state.feedSeen[club];
}

// Живые карточки: не просроченные, без скрытых каналов и без двух постов подряд
function feedCards(f) {
  const now = Date.now();
  const hidden = feedHidden();
  const out = [];
  for (const c of f.cards || []) {
    if (!(Date.parse(c.until) > now)) continue;
    if (c.kind === "post" && (hidden[c.channel] || !POST_URL.test(c.url || "") || !out.length || out[out.length - 1].kind === "post")) continue;
    out.push(c);
  }
  return out;
}

// Когда вышел пост: «18 мин», «3 ч», «вчера», «26 сен» — коротко, чтобы влезало в подпись карточки на 320px
function ago(iso) {
  const min = Math.max(1, Math.round((Date.now() - Date.parse(iso)) / 6e4));
  if (min < 60) return `${min} мин`;
  if (min < 24 * 60) return `${Math.round(min / 60)} ч`;
  const day = new Intl.DateTimeFormat("en-CA", { timeZone: TZ }).format(new Date(iso));
  const d = parseISO(day);
  return daysFromToday(day) === -1 ? "вчера" : `${d.getUTCDate()} ${MON_SHORT[d.getUTCMonth()]}`;
}

const scoreText = (s, dec) => `${s[0]}:${s[1]}${dec ? ` ${dec}` : ""}`;
function feedMatchLine(home, away, score, dec) {
  const me = state.fav;
  const nm = (id) => `<span class="${id === me ? "me" : ""}">${esc(team(id).name)}</span>`;
  return `<div class="fc-match">${emblem(home)}${nm(home)}<span class="fc-dash">—</span>${nm(away)}${emblem(away)}<b class="num">${esc(scoreText(score, dec))}</b></div>`;
}
const feedStory = (text) => (text ? `<p class="fc-story">${STAR_SUN}<span>${esc(text)}</span></p>` : "");
// Точка «новое» — картинка для экранного диктора, а не только цвет
const NEW_DOT = '<i class="fc-new" role="img" aria-label="Новое" title="Новое"></i>';
function feedTop(text, isNew) {
  return `<div class="fc-top">${isNew ? NEW_DOT : ""}<span>${text}</span></div>`;
}
// Карточка листа нажимается целиком: attrs — куда ведёт. data-id — чтобы после перерисовки найти то же место
const fcard = (cls, attrs, inner, id) => `<article class="fc ${cls}"${id ? ` data-id="${esc(id)}"` : ""}${attrs ? ` ${attrs} role="button" tabindex="0"` : ""}>${inner}</article>`;

function feedCard(c, isNew) {
  const me = state.fav;
  const card = (cls, attrs, inner) => fcard(cls, attrs, inner, c.id);
  switch (c.kind) {
    case "h2h": {
      // в листе — свой клуб и соперник, в ленте лиги — любая пара серии недели (club — хозяева)
      const a = c.club || me;
      const [wm, wo] = c.wins, [gm, gt] = c.goals;
      const h = { games: c.games, wins: { [a]: wm, [c.opp]: wo } };
      // названия не склоняем («матчей с «Белгород»»): в листе пара и так ясна по эмблемам, в ленте — именительный
      const pair = a === me ? "" : `${quoted(a)} — ${quoted(c.opp)} · `;
      return card("fc-h2h", `data-game="${esc(c.game)}"`, `${feedTop(`Очные встречи${c.since ? ` · с ${esc(c.since)}` : ""}`, isNew)}
        <div class="fc-h2h-row">${emblem(a, "md")}<b class="num">${wm}</b><span>победы</span><b class="num">${wo}</b>${emblem(c.opp, "md")}</div>
        <div class="fc-sub">${pair}Шайбы ${gm}:${gt} · ${c.games} ${plural(c.games, "матч", "матча", "матчей")}</div>
        <div class="fc-verdict">${esc(h2hVerdict(h, a, c.opp))}</div>`);
    }
    case "meeting":
      return card("", `data-game="${esc(c.match)}"`, `${feedTop(`Как сыграли в прошлый раз · ${esc(shortDate(c.date))}`, isNew)}
        ${feedMatchLine(c.home, c.away, c.score, c.decision)}${feedStory(c.story)}`);
    case "day":
      return card("", c.match ? `data-game="${esc(c.match)}"` : "", `${feedTop(`В этот день · ${esc(parseISO(c.date).getUTCFullYear())}`, isNew)}
        ${feedMatchLine(c.home, c.away, c.score, c.decision)}${feedStory(c.story)}`);
    case "story": {
      const g = findGame(c.match);
      const when = !c.date || daysFromToday(c.date) === -1 ? "вчера в лиге" : esc(dayMonth(c.date));
      return card("", `data-game="${esc(c.match)}"`, `${feedTop(`Сюжет дня · ${when}`, isNew)}
        ${feedStory(c.story)}${g && g.score ? feedMatchLine(g.home, g.away, [g.score.home, g.score.away], g.score.decision) : ""}`);
    }
    case "today": {
      const rows = c.games.map(findGame).filter(Boolean).map((g) => {
        const r = g.score ? `<b class="num">${g.score.home}:${g.score.away}</b>` : `<span class="kick">${g.time ? esc(g.time) : ""}</span>`;
        const nm = (id) => `<span class="${id === me ? "me" : ""}">${esc(team(id).name)}</span>`;
        return `<div class="fc-game" data-game="${esc(g.id)}" role="button" tabindex="0">${emblem(g.home)}${nm(g.home)}<span class="fc-dash">—</span>${nm(g.away)}${emblem(g.away)}${r}</div>`;
      }).join("");
      const more = c.n - c.games.length;
      // в ленте лиги — и ближайшие дни: «Завтра в лиге», «3 октября в лиге»
      const ahead = c.date ? daysFromToday(c.date) : 0;
      const day = ahead <= 0 ? "Сегодня" : ahead === 1 ? "Завтра" : esc(dayMonth(c.date));
      return card("fc-today", "", `${feedTop(`${day} в лиге · ${c.n} ${plural(c.n, "матч", "матча", "матчей")}`, isNew)}
        <div class="fc-games">${rows}</div>
        <button type="button" class="fc-link" data-feed-league>${more > 0 ? `Ещё ${more} ${plural(more, "матч", "матча", "матчей")} — в календаре` : "Весь день в календаре"}</button>`);
    }
    case "table": {
      const conf = { east: "на Востоке", west: "на Западе" }[c.conf] || "";
      const sub = c.to8 !== undefined ? (c.to8 ? `До восьмёрки — ${c.to8} ${plural(c.to8, "очко", "очка", "очков")}` : "Восьмёрка — рядом, по очкам вровень")
        : c.up !== undefined ? (c.up ? `До ${c.place - 1}-го места — ${c.up} ${plural(c.up, "очко", "очка", "очков")}` : `По очкам вровень с ${c.place - 1}-м местом`)
        : c.lead !== undefined ? (c.lead ? `Отрыв от 2-го места — ${c.lead} ${plural(c.lead, "очко", "очка", "очков")}` : "По очкам вровень со 2-м местом")
        : "Первое место в конференции";
      return card("fc-table", 'data-tab="table"', `${feedTop("Таблица", isNew)}
        <div class="fc-place"><b class="num">${c.place}</b><div><strong>${quoted(me)} — ${c.place}-е место ${conf}</strong><small>${esc(sub)}</small></div></div>`);
    }
    case "upcoming": {
      const list = c.games.map(findGame).filter(Boolean);
      if (!list.length) return "";
      return card("fc-list", "", `${feedTop("Дальше", false)}
        <div class="list">${list.map((g) => gameRow(g, me, false)).join("")}</div>`);
    }
    case "leaders": {
      const rows = c.rows.map((r) => `<div class="fc-lead" data-feed-leaders role="button" tabindex="0"><b>${esc(r.name)}</b><span>${r.rank}-й ${esc(LEAD_BY[r.cat] || "")}</span></div>`).join("");
      // в ленте лиги — чужой клуб: его имя в метке
      const who = c.club && c.club !== me ? `${esc(team(c.club).name)} · в лидерах` : "В лидерах";
      return card("fc-leaders", "", `${feedTop(`${who} · ${esc(c.league)} ${esc(c.season)}`, isNew)}${rows}`);
    }
    case "post":
      return postCard(c, isNew);
    default:
      return "";
  }
}

// compact — в ленте лиги: картинка-квадрат справа, текст и пилюля слева, вдвое ниже (ADR-015, 01.10)
function postCard(c, isNew, compact = false) {
  const title = c.ctitle || c.channel;
  const em = c.club ? emblem(c.club, "") : '<span class="em ab" aria-hidden="true">РХЛ</span>';
  const rel = c.slot === "opp" || (c.slot === "stream" && c.club && c.club === feedOpp()) ? "соперник серии"
    : c.slot === "stream" && c.club === state.fav ? "твой клуб" : "";
  const who = [CKIND[c.ckind] || "канал", rel].filter(Boolean).join(" · ");
  // на квадрате 72–88px «+5 фото» не помещается — там просто «+5»
  const more = c.media > 1 ? `+${c.media - 1}${compact ? "" : " фото"}` : "";
  const img = c.image && POST_IMG.test(c.image)
    ? `<div class="fc-img${compact ? " fc-thumb" : ""}"><img src="${esc(c.image)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">${c.video ? '<span class="tag">видео</span>' : more ? `<span class="tag">${more}</span>` : ""}</div>`
    : "";
  const text = c.title || c.text ? `<p class="fc-text">${c.title ? `<b>${esc(c.title)}</b>${c.text ? "<br>" : ""}` : ""}${esc(c.text)}</p>` : "";
  const go = c.media ? "Смотреть в канале" : "Читать в канале";
  const goBtn = `<button type="button" class="fc-go" data-post="${esc(c.url)}" aria-label="${go}: ${esc(title)}">${go} ›</button>`;
  const body = compact ? `<div class="fc-body${img ? " has-img" : ""}">${text}${img}${goBtn}</div>` : `${img}${text}${goBtn}`;
  // Карточка нажимается целиком (data-post), но это не кнопка: иначе экранный диктор читает только её
  // подпись, а «⋯» внутри кнопки недоступен. С клавиатуры и в VoiceOver пост открывает пилюля — настоящая кнопка
  return `<article class="fc fc-post${compact ? " compact" : ""}" data-post="${esc(c.url)}" data-id="${esc(c.id)}">
    <div class="fc-head">${em}<div class="fc-who"><b>${isNew ? NEW_DOT : ""}${esc(title)}</b><small><span class="fc-kind">${esc(who)}</span><span class="fc-ago">&nbsp;· ${esc(ago(c.at))}</span></small></div>
      <button type="button" class="fc-more" data-post-more="${esc(c.channel)}" aria-label="Ещё о канале ${esc(title)}">${DOTS}</button></div>
    ${body}
  </article>`;
}

function feedEndSay(f) {
  const g = f.next && findGame(f.next);
  // сыграли вчера, а следующий матч уже завтра — важнее, что впереди
  const soon = g ? daysFromToday(g.date) : null;
  const st = soon === 0 ? "match" : soon === 1 ? "eve" : f.state;
  switch (st) {
    case "match": return `Сегодня играем${g && g.time ? ` в ${g.time}` : ""} — до встречи на трибуне! Ниже — что пишут в лиге.`;
    case "eve": return "Завтра играем — не пропусти! Ниже — что пишут в лиге.";
    case "start": return g ? `Сезон стартует ${fmtLong(g.date).replace(/ (?=\S+$)/, "\u00a0")}. Пока ждём — что пишут в лиге.` : "Скоро сезон. Пока ждём — что пишут в лиге.";
    case "over": return "Сезон окончен — спасибо, что болел! Ниже — что пишут в лиге.";
    default: return "Ниже — что пишут в лиге за неделю.";
  }
}

function feedHTML(me) {
  if (!(me in state.feed)) {
    loadFeed(me).then(() => {
      const box = $("#feed");
      if (!box || state.tab !== "home" || state.fav !== me) return;
      box.innerHTML = feedHTML(me);
      fadeIn(box);
      watchFeedImages(box);
      watchSeen(box);
      refreshStream(true);   // посты, которые попали в лист, из ленты ниже уходят
    });
    return '<div class="sk sk-label"></div><div class="sk" style="height:132px"></div>';
  }
  const f = state.feed[me];
  const cards = f ? feedCards(f) : [];
  if (!cards.length) return upcomingBlock(me);   // не загрузился или сегодня нечего показать — как раньше
  const prev = feedSeenPrev(me);
  const isNew = (c) => !!prev && Date.parse(c.at) > prev && !(c.kind === "post" && seenBefore(c));
  const fresh = cards.filter(isNew).length;
  // метка — заголовок для экранного диктора: к листу и к ленте можно перейти по заголовкам
  const label = fresh
    ? `<div class="label" role="heading" aria-level="2">С прошлого захода<span class="aside">${fresh} ${plural(fresh, "новая", "новые", "новых")}</span></div>`
    : `<div class="label" role="heading" aria-level="2">Лист дня<span class="aside">${esc(fmtLong(todayISO()))}</span></div>`;
  // меньше трёх карточек — отметки нет: «Ты в курсе» после одной карточки звучит пусто
  const caught = cards.length >= 3
    ? `<div class="fc-end fc-caught">${guideFig(me, "cheer")}<p><b>Ты в курсе за сутки.</b> ${esc(feedEndSay(f))}</p></div>` : "";
  return `${label}${cards.map((c) => feedCard(c, isNew(c))).join("")}${caught}`;
}

// Картинка из CDN Telegram не загрузилась — карточка остаётся без неё. Но без прыжка (DESIGN.md → «Загрузка
// без прыжков»): рамка ниже экрана убирается, а на экране и выше него остаётся пустой, как при загрузке, —
// иначе всё под ней уезжает вверх, пока болельщик читает
function watchFeedImages(root) {
  root.querySelectorAll(".fc-img img").forEach((img) => {
    const drop = () => {
      const box = img.parentNode;
      if (!box || !box.isConnected) return;
      if (box.closest("#sheet") || box.getBoundingClientRect().top > innerHeight) box.remove();
      else { img.remove(); box.classList.add("gone"); }
    };
    if (img.complete && !img.naturalWidth) drop();
    else img.addEventListener("error", drop, { once: true });
  });
}

function refreshFeed() {
  const box = $("#feed");
  if (!box || state.tab !== "home") return;
  box.innerHTML = feedHTML(state.fav);
  watchFeedImages(box);
  watchSeen(box);
  refreshStream(true);
}

// Место на экране — скрытая карточка и следующие за ней: после перерисовки на место скрытой встаёт
// первая уцелевшая из следующих, лента не сбрасывается к началу
function feedPlace(card) {
  const out = [];
  for (let n = card; n && out.length < 30; n = n.nextElementSibling) {
    if (n.dataset && n.dataset.id) out.push({ id: n.dataset.id, top: n.getBoundingClientRect().top });
  }
  return out;
}
function keepPlace(place, skip) {
  const at = place.find((p) => p.id !== skip && document.querySelector(`#screen [data-id="${CSS.escape(p.id)}"]`));
  if (!at) return;
  const el = document.querySelector(`#screen [data-id="${CSS.escape(at.id)}"]`);
  window.scrollBy(0, el.getBoundingClientRect().top - place[0].top);
  // фокус — на то, что встало на место скрытого: иначе с клавиатуры он улетает в начало страницы
  const f = el.matches('[tabindex="0"]') ? el : el.querySelector('button, [tabindex="0"]');
  if (f) f.focus({ preventScroll: true });
}

// ---------- лента лиги под листом (ADR-015, пересмотр 30.09) ----------
// Одна на всех: data/feed/stream.json, посты всех каналов за неделю по времени и наши карточки через
// каждые пять. Свой клуб и соперник уже в листе выше. Подгружается порциями, пока листаешь

const STREAM_PAGE = 20;
let streamLoading = null;
let streamObs = null;

function loadStream() {
  if (!streamLoading) {
    streamLoading = fetch("data/feed/stream.json")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.status))))
      .then((d) => (state.stream = d))
      .catch(() => { state.stream = null; streamLoading = null; return null; });
  }
  return streamLoading;
}

// Соперник серии — по ближайшему матчу из листа клуба, если он в ближайшую неделю: как SERIES_DAYS в feed.py.
// Иначе чип «Соперник» и подпись «соперник серии» появлялись бы за три недели до встречи
const SERIES_DAYS = 7;
function feedOpp() {
  const f = state.feed[state.fav];
  const g = f && f.next && findGame(f.next);
  if (!g || daysFromToday(g.date) > SERIES_DAYS) return null;
  return g.home === state.fav ? g.away : g.home;
}

function streamItems() {
  if (!state.stream) return [];
  const now = Date.now();
  const hidden = feedHidden();
  const inSheet = new Set(((state.feed[state.fav] || {}).cards || []).map((c) => c.id));
  const want = state.streamFilter === "mine" ? state.fav : state.streamFilter === "opp" ? feedOpp() : null;
  const out = [];
  for (const c of state.stream.items || []) {
    // в «Все» не повторяем лист выше, а в чипе клуба нужна вся его неделя
    if (!(Date.parse(c.until) > now) || (!want && inSheet.has(c.id))) continue;
    if (c.kind === "post" && (hidden[c.channel] || !POST_URL.test(c.url || ""))) continue;
    if (want && (c.kind !== "post" || c.club !== want)) continue;
    out.push(c);
  }
  return out;
}

const streamItem = (c) => (c.kind === "post" ? postCard(c, false, true) : feedCard(c, false));

function streamEnd(all) {
  if (state.streamShown < all.length) return '<div id="stream-more" class="sk" style="height:132px"></div>';
  const empty = state.streamFilter === "all" ? "Здесь пока пусто: каналы молчат."
    : `У ${state.streamFilter === "mine" ? "твоего клуба" : "соперника"} за неделю нет постов о молодёжке в Telegram.`;
  return `<div class="fc-end"><p>${all.length ? "Это вся неделя. Новые посты — в течение часа после публикации." : esc(empty)}</p>
    <div class="pills"><button type="button" data-tab="calendar">Весь календарь</button><button type="button" data-tab="table">Таблица</button></div></div>`;
}

// keep — перерисовка на месте (скрыли канал, пришёл лист): столько же карточек, сколько уже долистали.
// Без keep — новый фильтр или новый экран: первая порция
function streamHTML(keep = false) {
  if (state.stream === undefined) {
    loadStream().then(() => {
      refreshStream();
      fadeIn($("#stream-wrap"));
      fadeIn($("#packs"));
    });
    // место под ленту занято сразу: подвал не мелькает под листом и не уезжает, когда она придёт
    return '<div class="sk sk-label"></div><div class="sk" style="height:132px"></div>';
  }
  if (!state.stream) return "";
  const opp = feedOpp();
  // у клуба без постов за неделю чипа нет — ни у своего, ни у соперника: пустой выбор разочаровывает
  const has = (club) => club && (state.stream.items || []).some((c) => c.kind === "post" && c.club === club);
  const chips = [["all", "Все"], ...(has(state.fav) ? [["mine", "Мой клуб"]] : []), ...(has(opp) ? [["opp", "Соперник"]] : [])];
  if (!chips.some(([k]) => k === state.streamFilter)) state.streamFilter = "all";
  const all = streamItems();
  state.streamShown = Math.min(keep ? Math.max(STREAM_PAGE, state.streamShown) : STREAM_PAGE, all.length);
  // фильтры прилипают под бегущей строкой; метка-«булавка» перед ними говорит, когда они прилипли
  return `<div class="label" role="heading" aria-level="2">Лента лиги<span class="aside">за неделю</span></div>
    <i class="stream-pin" aria-hidden="true"></i>
    <div class="pills stream-pills" role="group" aria-label="Чьи посты">${chips.map(([k, v]) =>
      `<button type="button" class="${state.streamFilter === k ? "on" : ""}" data-stream-filter="${k}" aria-pressed="${state.streamFilter === k}">${v}</button>`).join("")}</div>
    <div id="stream">${all.slice(0, state.streamShown).map(streamItem).join("")}</div>${streamEnd(all)}`;
}

function refreshStream(keep = false) {
  refreshPacks();
  const box = $("#stream-wrap");
  if (!box || state.tab !== "home") return;
  box.innerHTML = streamHTML(keep);
  watchFeedImages(box);
  watchSeen(box);
  mountStream();
}

// ---------- ряд «Сегодня» (ADR-015, шаг 6) ----------
// Кружки клубов со свежими постами, как истории: свой клуб, соперник, лига, дальше — у кого новее.
// Кольцо --ember — есть посты новее прошлого просмотра. Нажатие — посты клуба листом, листаются
// касанием: справа — дальше, слева — назад; свайп вбок — соседний клуб (ADR-016). Таймера автоперехода нет

const PACKS_KEY = "packs_seen";   // { клуб или "league": ISO последнего просмотренного поста }
const PACK_HOURS = 48;
const PACK_MAX = 12;

function packs() {
  if (!state.stream) return [];
  const now = Date.now();
  const hidden = feedHidden();
  const by = new Map();
  for (const c of state.stream.items || []) {
    if (c.kind !== "post" || hidden[c.channel] || !POST_URL.test(c.url || "")) continue;
    if (now - Date.parse(c.at) > PACK_HOURS * 36e5 || !(Date.parse(c.until) > now)) continue;
    const key = c.club || "league";
    if (!by.has(key)) by.set(key, []);
    by.get(key).push(c);
  }
  const opp = feedOpp();
  const rank = (k) => (k === state.fav ? 0 : k === opp ? 1 : k === "league" ? 2 : 3);
  return [...by].map(([key, posts]) => ({ key, posts: posts.sort((a, b) => Date.parse(a.at) - Date.parse(b.at)) }))
    .sort((a, b) => rank(a.key) - rank(b.key) || Date.parse(b.posts[b.posts.length - 1].at) - Date.parse(a.posts[a.posts.length - 1].at))
    .slice(0, PACK_MAX);
}

const packSeen = () => readJSON(PACKS_KEY);
const packKey = (c) => c.club || "league";

// Одна отметка «просмотрено» на пост (ADR-015, пересмотр 01.10): открыли в историях или карточка секунду
// была на экране в листе или ленте. Хранится, пока жив пост. Старые отметки историй — «просмотрено до» в
// packs_seen — тоже считаются
const POSTS_SEEN_KEY = "posts_seen";   // { id поста: до какого времени помнить }
let postsSeen = null;
function seenPosts() {
  if (!postsSeen) postsSeen = readJSON(POSTS_SEEN_KEY);
  return postsSeen;
}
const postSeen = (c) => !!seenPosts()[c.id] || Date.parse(c.at) <= (Date.parse(packSeen()[packKey(c)]) || 0);
function markSeen(c) {
  const all = seenPosts();
  if (all[c.id]) return false;
  all[c.id] = c.until;
  const now = Date.now();
  for (const k of Object.keys(all)) if (!(Date.parse(all[k]) > now)) delete all[k];
  lsSet(POSTS_SEEN_KEY, JSON.stringify(all));
  return true;
}
// Что болельщик видел до этого захода: снимок один раз за сессию, как прошлый заход в листе, —
// иначе точка «новое» гасла бы при перерисовке, пока он читает
function seenBefore(c) {
  if (!state.seenSnap) state.seenSnap = { ids: { ...seenPosts() }, packs: packSeen() };
  const s = state.seenSnap;
  return !!s.ids[c.id] || Date.parse(c.at) <= (Date.parse(s.packs[packKey(c)]) || 0);
}

const packNew = (p) => p.posts.some((c) => !postSeen(c));
// Кольцо --ember — у своего клуба, соперника, лиги и клубов, чьи истории уже открывали. Остальные тихие:
// при первом заходе новое всё, и двенадцать колец громче счёта
const packRing = (p) => packNew(p) && (p.key === state.fav || p.key === "league" || p.key === feedOpp() || p.key in packSeen());
const packName = (key) => (key === "league" ? "Лига" : key === state.fav ? "Мой клуб" : team(key).name);
const packLabel = (p) => `${packName(p.key)}: ${p.posts.length} ${plural(p.posts.length, "пост", "поста", "постов")}${packRing(p) ? ", есть новое" : ""}`;
// Был ли ряд в прошлый раз: пока лента грузится, скелетон рисуем, только если кружки ожидаются. Иначе
// в тихие дни, когда каналы молчат, пустой ряд схлопывается и всё ниже прыгает вверх на 112px
const PACKS_ROW_KEY = "packs_row";

function packsHTML() {
  if (state.stream === undefined) {
    return lsGet(PACKS_ROW_KEY) === "0" ? "" : `<div class="packs" aria-hidden="true">${'<span class="pack sk-pack"><i class="sk"></i></span>'.repeat(5)}</div>`;
  }
  const list = packs();
  if (!list.length) return "";
  // группа кнопок, а не список: role="listitem" на кнопке отнимает у неё роль кнопки. Ряд — одна остановка
  // Tab, по кружкам — стрелками: иначе до табло двенадцать нажатий
  return `<div class="packs" role="group" aria-label="Сегодня в каналах">${list.map((p, k) => {
    const em = p.key === "league" ? '<span class="em ab" aria-hidden="true">РХЛ</span>' : emblem(p.key, "");
    return `<button type="button" class="pack${packRing(p) ? " new" : ""}" data-pack="${esc(p.key)}" tabindex="${k ? -1 : 0}" aria-label="${esc(packLabel(p))}">
      <span class="pack-ring">${em}</span><span class="pack-name" aria-hidden="true">${esc(packName(p.key))}</span></button>`;
  }).join("")}</div>`;
}

function refreshPacks() {
  const box = $("#packs");
  if (box && state.tab === "home") box.innerHTML = packsHTML();
  if (state.stream !== undefined) lsSet(PACKS_ROW_KEY, packs().length ? "1" : "0");
}

// Кольца на месте, без перерисовки ряда: его прокрутка вбок и фокус остаются
function refreshRings() {
  const list = packs();
  document.querySelectorAll("#packs [data-pack]").forEach((btn) => {
    const p = list.find((x) => x.key === btn.dataset.pack);
    if (!p) return;
    btn.classList.toggle("new", packRing(p));
    btn.setAttribute("aria-label", packLabel(p));
  });
}

// Карточка поста секунду на экране — просмотрена. Пока открыт лист поверх, не считаем
let seenObs = null;
const seenTimers = new Map();
function postById(id) {
  const pools = [((state.feed[state.fav] || {}).cards) || [], (state.stream && state.stream.items) || []];
  for (const list of pools) {
    const c = list.find((x) => x.id === id && x.kind === "post");
    if (c) return c;
  }
  return null;
}
function watchSeen(root) {
  if (!root || !("IntersectionObserver" in window)) return;
  if (!seenObs) {
    seenObs = new IntersectionObserver((es) => {
      for (const e of es) {
        const el = e.target;
        if (!el.isConnected) { seenObs.unobserve(el); continue; }
        if (e.isIntersecting && !seenTimers.has(el)) {
          seenTimers.set(el, setTimeout(() => {
            seenTimers.delete(el);
            if (!el.isConnected || sheetOpen() || document.hidden) return;
            seenObs.unobserve(el);
            const c = postById(el.dataset.id);
            if (c && markSeen(c)) refreshRings();
          }, 1000));
        } else if (!e.isIntersecting && seenTimers.has(el)) {
          clearTimeout(seenTimers.get(el));
          seenTimers.delete(el);
        }
      }
    }, { threshold: 0.6 });
  }
  root.querySelectorAll(".fc-post[data-id]").forEach((el) => { if (!seenPosts()[el.dataset.id]) seenObs.observe(el); });
}

// Открыть пакет: с первого непросмотренного поста, всё просмотрено — с начала
function openPack(key) {
  const list = packs();
  const ki = list.findIndex((p) => p.key === key);
  if (ki < 0) return;
  const i = Math.max(0, list[ki].posts.findIndex((c) => !postSeen(c)));
  state.pack = { list, ki, i };
  showPack(0);
}

function showPack(dir) {
  const { list, ki, i } = state.pack;
  const p = list[ki];
  const c = p.posts[i];
  const all = packSeen();
  if (!(Date.parse(all[p.key]) >= Date.parse(c.at))) {
    all[p.key] = c.at;
    lsSet(PACKS_KEY, JSON.stringify(all));
  }
  markSeen(c);
  const img = c.image && POST_IMG.test(c.image)
    ? `<div class="fc-img pack-img"><img src="${esc(c.image)}" alt="" decoding="async" referrerpolicy="no-referrer">${c.video ? '<span class="tag">видео</span>' : c.media > 1 ? `<span class="tag">+${c.media - 1} фото</span>` : ""}</div>` : "";
  const bar = p.posts.map((_, k) => `<i class="${k < i ? "done" : k === i ? "on" : ""}"></i>`).join("");
  const text = c.title || c.text ? `<p class="pack-text">${c.title ? `<b>${esc(c.title)}</b>${c.text ? "<br>" : ""}` : ""}${esc(c.text)}</p>` : "";
  showSheet(`<div class="grab"></div>
    <div class="pack-bar" role="img" aria-label="Пост ${i + 1} из ${p.posts.length}">${bar}</div>
    <div class="sheet-head"><span class="when">${esc(c.ctitle || c.channel)} · ${esc(ago(c.at))}</span>
    <button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    <div class="pack-page">${img}${text}
      <button type="button" class="pack-zone prev" data-pack-nav="-1" aria-label="Предыдущий пост"></button>
      <button type="button" class="pack-zone next" data-pack-nav="1" aria-label="Следующий пост"></button></div>
    <div class="pack-cta"><button type="button" class="btn" data-post="${esc(c.url)}">${c.media ? "Смотреть в канале" : "Читать в канале"}</button>
    <p class="pack-foot">${esc(packName(p.key))} · ${ki + 1} из ${list.length}<span class="pack-how">Справа — дальше, слева — назад, свайп — другой клуб</span></p></div>`, dir);
  // рамка одной высоты: отрезки и крестик не прыгают от поста к посту, кнопка в канал всегда на экране
  $("#sheet").classList.add("pack-open");
  watchFeedImages($("#sheet"));
  const btn = document.querySelector(`#packs [data-pack="${CSS.escape(p.key)}"]`);
  if (btn) {
    btn.classList.toggle("new", packRing(p));
    btn.setAttribute("aria-label", packLabel(p));   // экранный диктор не должен дальше говорить «есть новое»
  }
}

// Дальше — следующий пост, после последнего — следующий клуб; назад — наоборот
function packNav(step) {
  const s = state.pack;
  if (!s) return;
  haptic();
  // с клавиатуры фокус остаётся на той же зоне: иначе после каждого Enter он прыгает на «Закрыть»
  const zone = document.activeElement && document.activeElement.dataset && document.activeElement.dataset.packNav;
  let { ki, i } = s;
  i += step;
  if (i >= s.list[ki].posts.length) { ki += 1; i = 0; }
  if (i < 0) { ki -= 1; i = ki >= 0 ? s.list[ki].posts.length - 1 : 0; }
  if (ki < 0) { ki = 0; i = 0; }
  if (ki >= s.list.length) { state.pack = null; return closeMatch(); }
  s.ki = ki;
  s.i = i;
  showPack(step);
  const again = zone && $(`#sheet [data-pack-nav="${zone}"]`);
  if (again) again.focus({ preventScroll: true });
}

// Свайп вбок — соседний клуб (ADR-016): влево — следующий, вправо — предыдущий, с первого непросмотренного
// поста. Вправо с первого клуба — на месте; влево с последнего — истории кончились, лист закрывается
function packClub(step) {
  const s = state.pack;
  if (!s || !sheetOpen()) return;
  const ki = s.ki + step;
  if (ki < 0) return;
  haptic();
  if (ki >= s.list.length) {
    state.pack = null;
    return closeMatch();
  }
  s.ki = ki;
  s.i = Math.max(0, s.list[ki].posts.findIndex((c) => !postSeen(c)));
  showPack(step);
}

// Прилипли ли фильтры ленты: булавка перед ними ушла под бегущую строку — у пилюль появляется линия снизу
let pinObs = null;
const marqueeH = () => ($(".marquee") || {}).offsetHeight || 0;
function mountPin() {
  if (pinObs) pinObs.disconnect();
  const pin = $(".stream-pin");
  const bar = $(".stream-pills");
  if (!pin || !bar || !("IntersectionObserver" in window)) return;
  pinObs = new IntersectionObserver(([e]) => {
    bar.classList.toggle("stuck", !e.isIntersecting && e.boundingClientRect.top < e.rootBounds.top + 1);
  }, { rootMargin: `-${marqueeH() + 1}px 0px 0px 0px` });
  pinObs.observe(pin);
}

// Следующая порция — когда до конца ленты остаётся экран
function mountStream() {
  mountPin();
  if (streamObs) streamObs.disconnect();
  const more = $("#stream-more");
  if (!more || !("IntersectionObserver" in window)) return;
  streamObs = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) moreStream(); }, { rootMargin: "800px 0px" });
  streamObs.observe(more);
}

function moreStream() {
  const box = $("#stream");
  const more = $("#stream-more");
  if (!box || !more) return;
  const all = streamItems();
  const from = state.streamShown;
  const had = box.children.length;
  state.streamShown = Math.min(from + STREAM_PAGE, all.length);
  box.insertAdjacentHTML("beforeend", all.slice(from, state.streamShown).map(streamItem).join(""));
  // порция проявляется на месте скелетона, как данные в карточке матча (DESIGN.md → «Загрузка без прыжков»)
  [...box.children].slice(had).forEach(fadeIn);
  watchFeedImages(box);
  watchSeen(box);
  more.insertAdjacentHTML("afterend", streamEnd(all));
  more.remove();
  mountStream();
}

function openPost(url) {
  if (!POST_URL.test(url)) return;
  haptic();
  // мини-апп не закрывается (Bot API 7.0+): болельщик вернётся на то же место листа
  if (inTelegram) tg.openTelegramLink(url);
  else window.open(url, "_blank", "noopener");
}

// Название канала: из листа или из ленты лиги — «⋯» есть на карточках обеих, а в «Скрытых каналах»
// должно стоять название, а не адрес вроде hcsamara
function feedChannelTitle(handle) {
  const pools = [((state.feed[state.fav] || {}).cards) || [], (state.stream && state.stream.items) || []];
  for (const list of pools) {
    const c = list.find((x) => x.channel === handle && x.ctitle);
    if (c) return c.cfull || c.ctitle;   // в шапке карточки короткое имя, здесь — полное
  }
  return feedHidden()[handle] || handle;
}

// Карточка, с которой открыли «⋯»: в iOS нажатие не переводит фокус на кнопку, по фокусу её не найти
let postMenuCard = null;
function openPostMenu(handle, from) {
  postMenuCard = from ? from.closest(".fc") : null;
  const title = feedChannelTitle(handle);
  showSheet(`<div class="grab"></div>
    <div class="sheet-head"><span class="when">${esc(title)}</span>
    <button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    <div class="menu">
      <button type="button" class="menu-row" data-feed-hide="${esc(handle)}">${ICON_ME.hide}<span><b>Не показывать этот канал</b><small>Вернуть — «Я» → «Посты каналов»</small></span>${ICON_ME.chev}</button>
      <button type="button" class="menu-row" data-feed-rules>${ICON_ME.help}<span><b>Как мы выбираем посты</b><small>Чьи каналы и чего в ленте не бывает</small></span>${ICON_ME.chev}</button>
    </div>`);
}

const FEED_RULES = [
  "Показываем публичные каналы клубов РХЛ и канал лиги. Из общих каналов со взрослым клубом — только посты о молодёжке.",
  "Из поста — начало текста и одна картинка. Всё остальное — в самом канале: нажми на карточку.",
  "В листе дня — сначала твой клуб и соперник серии, потом клубы лиги по кругу, сколько бы канал ни писал. Ниже, в ленте лиги, — все посты за неделю по времени, от одного канала не больше двух подряд.",
  "Не показываем рекламу, букмекеров, дни рождения и возраст игроков.",
  "Клуб может попросить убрать картинки или весь канал — снимаем за сутки. Скрыть канал у себя — «⋯» на карточке.",
];

function openFeedRules() {
  const hidden = Object.entries(feedHidden());
  const rows = hidden.map(([h, t]) => `<div class="menu-row static-row"><span><b>${esc(t || h)}</b><small>Скрыт на «Главной»</small></span>
    <button type="button" class="fc-link" data-feed-unhide="${esc(h)}" aria-label="Вернуть ${esc(t || h)}">Вернуть</button></div>`).join("");
  showSheet(`<div class="grab"></div>
    <div class="sheet-head"><span class="when">Посты каналов на «Главной»</span>
    <button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    <ol class="fc-rules">${FEED_RULES.map((r) => `<li>${esc(r)}</li>`).join("")}</ol>
    ${rows ? `<div class="label">Скрытые каналы</div><div class="menu">${rows}</div>` : ""}`);
}

function calFor(id) {
  return { team: id || null, side: "all", conf: "all" };
}

function segBtn(on, attrs, inner) {
  return `<button type="button" class="${on ? "on" : ""}" ${attrs} aria-pressed="${on}">${inner}</button>`;
}

function calendarList() {
  const { team: teamId, side, conf } = state.cal;
  let list = teamId ? gamesOf(teamId) : games();
  if (teamId && side !== "all") list = list.filter((g) => (side === "home" ? g.home === teamId : g.away === teamId));
  if (!teamId && conf !== "all") list = list.filter((g) => team(g.home).conf === conf || team(g.away).conf === conf);
  return list;
}

// Полоса фильтров Календаря: обновляется на месте, бегунки перетекают к новому выбору
function filterbarInner() {
  const { team: teamId, side, conf } = state.cal;
  const other = teamId && teamId !== state.fav;
  const chev = '<svg class="chev" viewBox="0 0 12 12" aria-hidden="true"><path d="M3 4.5 6 7.5 9 4.5"/></svg>';
  // Вторичный фильтр всегда на месте: у команды — дом/выезд, у лиги — конференция. Высота полосы не прыгает
  const sub = teamId
    ? [["all", "Все"], ["home", "Дома"], ["away", "Выезд"]].map(([k, v]) => segBtn(side === k, `data-cal-side="${k}"`, v))
    : [["all", "Все"], ["east", "Восток"], ["west", "Запад"]].map(([k, v]) => segBtn(conf === k, `data-cal-conf="${k}"`, v));
  const otherName = other ? esc(team(teamId).name) : "Другая";
  return `<div class="seg" role="group" aria-label="Чей календарь" data-run="cal-team">${RUN}
        ${segBtn(teamId === state.fav, `data-cal-team="${esc(state.fav)}"`, "<span>Моя команда</span>")}
        ${segBtn(!teamId, 'data-cal-team=""', "<span>Вся лига</span>")}
        ${segBtn(other, 'data-cal-other aria-haspopup="dialog"', `<span>${otherName}</span>${chev}`)}
      </div>
      <div class="chips" role="group" aria-label="${teamId ? "Где играют" : "Конференция"}" data-run="cal-sub">${RUN}${sub.join("")}</div>`;
}

// Высота месяца до первого показа: строка команды ~65px, строка лиги ~75px, день ~31px.
// По ней браузер держит место под месяцы вне экрана (content-visibility)
const MONTH_H = { label: 46, one: 65, two: 75, day: 31 };

// upto — месяц «ГГГГ-ММ», до которого включительно месяцы раскладываются сразу: выше видимого
// места высота должна быть настоящей, иначе прокрутка вверх дёргается (style.css → .month)
function calendarMonths(list, upto) {
  const teamId = state.cal.team;
  const nextId = (list.find(isUpcoming) || {}).id;
  const nextDay = nextId ? list.find((x) => x.id === nextId).date : null;
  if (upto === undefined) upto = nextDay ? nextDay.slice(0, 7) : "";
  const byMonth = new Map();
  for (const g of list) {
    const m = g.date.slice(0, 7);
    if (!byMonth.has(m)) byMonth.set(m, []);
    byMonth.get(m).push(g);
  }
  let html = "";
  for (const [key, month] of byMonth) {
    const d = parseISO(month[0].date);
    const count = month.length;
    let body = "";
    let day = "";
    let days = 0;
    for (const g of month) {
      if (teamId) {
        body += gameRow(g, teamId, g.id === nextId);
        continue;
      }
      if (g.date !== day) {
        day = g.date;
        days += 1;
        const next = day === nextDay;
        const today = daysFromToday(day) === 0;
        body += `<div class="day${next ? " next" : ""}" data-date="${esc(day)}"${next ? ' id="next-anchor"' : ""}><span>${esc(fmtLong(day))}</span>${today ? '<span class="tag today">Сегодня</span>' : ""}</div>`;
      }
      body += leagueRow(g);
    }
    const h = MONTH_H.label + count * (teamId ? MONTH_H.one : MONTH_H.two) + days * MONTH_H.day;
    html += `<section class="month${key <= upto ? " open" : ""}" style="contain-intrinsic-size: auto ${h}px"><div class="label">${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}<span class="aside">${count} ${plural(count, "матч", "матча", "матчей")}</span></div><div class="list">${body}</div></section>`;
  }
  if (!list.length) html += `<div class="empty">Матчей нет</div>`;
  return html;
}

function renderCalendar() {
  let html = `<section class="band lavender split"><h1>Календарь</h1></section>
    <div class="filterbar">${filterbarInner()}</div>`;
  html += `<div id="cal-list">${calendarMonths(calendarList())}</div>`;
  html += `<div class="foot">Официальный календарь ФХР пока есть только у «Рязань-ВДВ». Остальные даты — предварительные, уточним после открытия сайта РХЛ.</div>`;
  return html;
}

// День, который сейчас виден у верха списка под прилипшими фильтрами
function visibleDay() {
  const bar = $(".filterbar");
  if (!bar) return null;
  const top = bar.getBoundingClientRect().bottom;
  for (const el of document.querySelectorAll("#cal-list [data-date]")) {
    const r = el.getBoundingClientRect();
    if (r.bottom > top) return r.top < top + 8 ? { date: el.dataset.date, top: r.top } : null;
  }
  return null;
}

// Смена фильтра Календаря: сначала отвечает нажатая кнопка, список — в следующем кадре.
// keep — остаться на том же дне; иначе — к ближайшему матчу
let calToken = 0;
function refreshCalendar(keep) {
  const bar = $(".filterbar");
  if (!bar) return render();
  const prev = runnerState(bar);
  bar.innerHTML = filterbarInner();
  placeRunners(bar, prev);
  const token = ++calToken;
  const anchor = keep ? visibleDay() : null;
  nextFrame(() => {
    if (token !== calToken || state.tab !== "calendar") return;
    const list = calendarList();
    $("#cal-list").innerHTML = calendarMonths(list, anchor ? anchor.date.slice(0, 7) : undefined);
    if (anchor) {
      const els = [...document.querySelectorAll("#cal-list [data-date]")];
      const el = els.find((x) => x.dataset.date >= anchor.date) || els[els.length - 1];
      if (el) window.scrollTo(0, window.scrollY + el.getBoundingClientRect().top - anchor.top);
    } else if (!keep) {
      const next = $("#next-anchor");
      if (next) next.scrollIntoView({ block: "center" });
    }
  });
}

function renderTable() {
  return `<section class="band mint"><h1>Таблица</h1>
    <div class="seg table-seg" role="group" aria-label="Что показать" data-run="table-view">${RUN}${tableSeg()}</div></section>
    <div id="table-body">${state.tableView === "players" ? leadersBody() : tableBody()}</div>`;
}

// Шапка «Таблицы» — только «Команды · Игроки»: она одинаковой высоты в обоих видах (ADR-009)
function tableSeg() {
  const players = state.tableView === "players";
  return segBtn(!players, 'data-table-view="teams"', "<span>Команды</span>")
    + segBtn(players, 'data-table-view="players"', "<span>Игроки</span>");
}

// «Команды · Игроки»: бегунок перетекает сразу, содержимое — в следующем кадре
function refreshTable() {
  const seg = $(".table-seg");
  if (!seg) return render();
  const prev = runnerState(seg.parentNode);
  seg.querySelectorAll("button").forEach((b) => b.remove());
  seg.insertAdjacentHTML("beforeend", tableSeg());
  placeRunners(seg.parentNode, prev);
  nextFrame(() => {
    const box = $("#table-body");
    if (!box || state.tab !== "table") return;
    box.innerHTML = state.tableView === "players" ? leadersBody() : tableBody();
    placeRunners(box);
    fadeIn(box);
  });
}

// Конференции — вторичный фильтр (DESIGN.md): лёгкие чипы во всю ширину, как в Календаре
function tableBody() {
  const chips = Object.entries(CONF).map(([k, v]) => segBtn(state.conf === k, `data-conf="${k}"`, v)).join("");
  return `<div class="chips fill conf-chips" role="group" aria-label="Конференция" data-run="table-conf">${RUN}${chips}</div>
    <div id="standings">${standingsTable()}</div>`;
}

function standingsTable() {
  const rows = state.data.standings[state.conf] || [];
  let html = `<div class="st"><div class="st-row head"><span class="pos"></span><span class="tm">Команда</span><span>И</span><span class="wl">В</span><span class="wl">П</span><span>Ш</span><span>О</span></div>`;
  rows.forEach((r, i) => {
    if (i === PLAYOFF_CUT) html += `<div class="cut"><span>плей-офф ↑</span></div>`;
    const wins = r.w + r.otw + r.sow;
    const losses = r.l + r.otl + r.sol;
    html += `<div class="st-row${r.team === state.fav ? " me" : ""}" data-team="${esc(r.team)}" role="button" tabindex="0">
      <span class="pos">${i + 1}</span>
      <span class="tm">${emblem(r.team)}<span>${esc(team(r.team).name)}</span></span>
      <span class="n">${r.gp}</span><span class="n wl">${wins}</span><span class="n wl">${losses}</span>
      <span class="n">${r.gf}:${r.ga}</span><span class="pts num">${r.pts}</span>
    </div>`;
  });
  html += `</div>`;
  const played = rows.some((r) => r.gp);
  html += `<div class="foot">${played
    ? "Победа — 2 очка, поражение в овертайме или по буллитам — 1. В плей-офф выходят 8 команд конференции."
    : "Сезон стартует 3 октября — таблица заполнится после первых матчей."}</div>`;
  return html;
}

// ---------- лидеры лиги (ADR-009) ----------

// Показатель: название, подпись к числу (по числу — plural), как писать строку под именем в топ-10
const LEAD_CATS = {
  pts: { title: "Бомбардиры", unit: ["очко", "очка", "очков"], sub: (r) => `${r.g}+${r.a} · ${r.gp} ${plural(r.gp, "игра", "игры", "игр")}` },
  g: { title: "Снайперы", unit: ["гол", "гола", "голов"], sub: (r) => `${r.pts} ${plural(r.pts, "очко", "очка", "очков")} · ${r.gp} ${plural(r.gp, "игра", "игры", "игр")}` },
  a: { title: "Ассистенты", unit: ["передача", "передачи", "передач"], sub: (r) => `${r.pts} ${plural(r.pts, "очко", "очка", "очков")} · ${r.gp} ${plural(r.gp, "игра", "игры", "игр")}` },
  pm: { title: "Плюс-минус", unit: null, sub: (r) => `${r.pts} ${plural(r.pts, "очко", "очка", "очков")} · ${r.gp} ${plural(r.gp, "игра", "игры", "игр")}` },
  sv_pct: { title: "Вратари", unit: "% отражённых", sub: (r) => `КН ${leadValue("gaa", r.gaa)} · ${r.gp} ${plural(r.gp, "игра", "игры", "игр")}` },
  pim: { title: "Штраф", unit: ["минута", "минуты", "минут"], sub: (r) => `${r.pts} ${plural(r.pts, "очко", "очка", "очков")} · ${r.gp} ${plural(r.gp, "игра", "игры", "игр")}` },
};
// Что значит показатель — первым абзацем в листе топ-10: болельщик-новичок не обязан знать «+/−»
const LEAD_ABOUT = {
  pts: "Больше всех очков. Очко — это гол или результативная передача: 39+39 — 39 голов и 39 передач.",
  g: "Больше всех забитых шайб. Буллиты в серии после овертайма не считаются.",
  a: "Больше всех результативных передач — пасов, после которых забил партнёр. На один гол записывают до двух передач.",
  pm: "Разница шайб, пока игрок на льду: забила его команда — плюс, пропустила — минус. Голы, забитые в большинстве, не считаются.",
  sv_pct: "Доля отражённых бросков в створ. КН — сколько шайб вратарь пропускает в среднем за 60 минут: чем меньше, тем лучше. В список попадают вратари, отыгравшие достаточно времени.",
  pim: "Больше всех штрафных минут: сколько игрок просидел на скамейке штрафников.",
};

let leadersLoading = null;
let leadersFailed = false;   // не качаем по кругу: после ошибки — «Повторить»
function loadLeaders() {
  if (!leadersLoading) {
    leadersFailed = false;
    leadersLoading = fetch("data/leaders.json")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.status))))
      .then((d) => (state.leaders = d))
      .catch(() => { leadersLoading = null; leadersFailed = true; return null; });
  }
  return leadersLoading;
}
function fillLeaders() {
  loadLeaders().then(() => {
    const box = $("#table-body");
    if (!box || state.tab !== "table" || state.tableView !== "players") return;
    box.innerHTML = leadersBody();
    fadeIn(box);
  });
}

function leadValue(k, v) {
  if (v == null) return "—";
  if (k === "pm") return v > 0 ? `+${v}` : v < 0 ? `−${-v}` : "0";
  if (k === "sv_pct" || k === "gaa") return v.toFixed(k === "gaa" ? 2 : 1).replace(".", ",");
  return String(v);
}
function leadUnit(cat, v) {
  const u = LEAD_CATS[cat].unit;
  if (!u) return "";
  return typeof u === "string" ? u : plural(Math.abs(v), ...u);
}
const clubOf = (r) => (r.team ? team(r.team).name : r.club || "");
// Эмблема клуба игрока: нынешний клуб — как везде, клуб прошлых сезонов — из past_clubs.json
function clubBadge(r, size) {
  if (r.team) return emblem(r.team, size);
  if (r.logo) return `<span class="em${size ? " " + size : ""}"><img src="${esc(r.logo)}" alt=""></span>`;
  return "";
}
// Фигура с эмблемой клуба в углу — вместо фото игрока
const playerSticker = (r, cls = "") => `<span class="ps ${cls}">${figure(r)}${clubBadge(r)}</span>`;
// «Султанов Реваль» → фамилия крупно, имя мельче: в карточке лидера узко
const splitName = (n) => { const i = n.indexOf(" "); return i < 0 ? [n, ""] : [n.slice(0, i), n.slice(i + 1)]; };

function leadSeason(d) {
  const past = d.season !== state.data.season;
  return `<div class="label">${esc(d.league)} ${esc(d.season)}<span class="aside">${past ? "прошлый сезон" : esc(d.stage)}</span></div>`;
}

// Карточка показателя: одно большое число и лидер; эмблема его клуба — наклейка в углу
function leadCard(cat, r) {
  const c = LEAD_CATS[cat];
  if (!r) return `<div class="lead-card empty-card"><span class="lc-cat">${c.title}</span><span class="lc-none">появятся после первых матчей</span></div>`;
  const [last, first] = splitName(r.name);
  return `<button type="button" class="lead-card${r.team && r.team === state.fav ? " me" : ""}" data-lead-open="${cat}" aria-label="${c.title}: топ-10">
    <span class="lc-cat">${c.title}</span>
    <span class="lc-em">${playerSticker(r, "lg")}</span>
    <span class="lc-val num">${leadValue(cat, r[cat])}</span>
    <span class="lc-unit">${esc(leadUnit(cat, r[cat]))}</span>
    <span class="lc-name"><b>${esc(last)}</b> ${esc(first)}</span>
    <span class="lc-club">${esc(clubOf(r))}</span>
  </button>`;
}

// Игроки любимой команды в списках лиги: по строке на игрока, в подписи — все его места.
// Нажатие открывает список, где он выше всего
const LEAD_BY = { pts: "по очкам", g: "по голам", a: "по передачам", pm: "по плюс-минусу", sv_pct: "среди вратарей", pim: "по штрафу" };
function mineBlock(d, season = false) {
  if (!state.fav) return "";
  const people = new Map();
  const who = new Map();
  for (const cat of Object.keys(LEAD_CATS)) {
    const r = (d.categories[cat] || []).find((x) => x.team === state.fav);
    if (!r) continue;
    if (!people.has(r.name)) people.set(r.name, []);
    people.get(r.name).push([cat, r.rank]);
    who.set(r.name, r);
  }
  if (!people.size) return "";
  const rows = [...people].map(([name, places]) => [name, places.sort((x, y) => x[1] - y[1])]).sort((x, y) => x[1][0][1] - y[1][0][1]);
  const aside = season ? `<span class="aside">${esc(d.league)} ${esc(d.season)}</span>` : "";
  return `<div class="label">${esc(team(state.fav).name)} в лидерах${aside}</div><div class="list mine-leads">${rows.map(([name, places]) =>
    `<div class="row mine-row" data-lead-open="${places[0][0]}" role="button" tabindex="0">
      <span class="ps">${figure(who.get(name))}</span>
      <span class="ml-who"><b>${esc(name)}</b><small>${places.map(([cat, rank]) => `${rank}-й ${LEAD_BY[cat]}`).join(" · ")}</small></span>
    </div>`).join("")}</div>`;
}

function leadersBody() {
  const d = state.leaders;
  if (!d && leadersFailed) return failBlock("Лидеры лиги", "leaders");
  if (!d) {
    fillLeaders();
    return `<div class="sk sk-label"></div><div class="lead-grid">${'<div class="sk" style="height:156px;margin:0"></div>'.repeat(6)}</div>`;
  }
  const past = d.season !== state.data.season;
  let html = leadSeason(d);
  html += `<div class="lead-grid">${Object.keys(LEAD_CATS).map((cat) =>
    leadCard(cat, (d.categories[cat] || []).find((r) => r.rank === 1))).join("")}</div>`;
  html += mineBlock(d);
  html += `<div class="foot">Нажмите на карточку — будет топ-10.<br>${past
    ? `Регулярный чемпионат ${esc(d.league)} ${esc(d.season)}, статистика с сайта лиги, клубы — под нынешними названиями. Лидеры сезона ${esc(state.data.season)} появятся после первого тура.`
    : "Места — как в статистике на сайте лиги."}</div>`;
  return html;
}

// Стикер игрока вместо фото (ADR-009): полевой или вратарь в форме своего клуба (webapp/players/clubs/).
// Номер ставим сами — туда, где на майке ровный участок, и цветом, который на ней читается
// (tools/player_kits.py). Формы клуба нет — общий стикер из webapp/players/
function figure(r) {
  const role = r.role === "G" ? "goalie" : "skater";
  const kit = r.kit && state.data && state.data.kits && state.data.kits[r.kit];
  const at = kit && kit[role];
  const src = at ? `players/clubs/${esc(r.kit)}-${role}.webp` : `players/${role}.webp`;
  // y — середина ровного участка груди; строка номера высотой 20% ширины, поэтому верх на 10% выше
  const pos = at ? ` style="top:${(at[0] * 100 - 10).toFixed(1)}%;color:${at[1] === "#ffffff" ? "#fff" : "#000"}"` : "";
  const num = r.number != null ? `<b class="fig-num"${pos}>${esc(r.number)}</b>` : "";
  return `<span class="fig${role === "goalie" ? " goalie" : ""}" aria-hidden="true"><img src="${src}" alt="" decoding="async">${num}</span>`;
}

// Топ-10 показателя — в листе снизу, как карточка матча: одно число в строке, остальное — подписью
function leaderRow(cat, r) {
  return `<div class="row lead-row${r.team && r.team === state.fav ? " me" : ""}">
    <span class="lr-rank num">${r.rank}</span>
    ${playerSticker(r)}
    <span class="lr-who"><b>${esc(r.name)}</b><small>${esc(clubOf(r))} · ${esc(LEAD_CATS[cat].sub(r))}</small></span>
    <span class="lr-val num">${leadValue(cat, r[cat])}</span>
  </div>`;
}

function openLeaders(cat) {
  const d = state.leaders;
  const c = LEAD_CATS[cat];
  if (!d || !c) return;
  const rows = d.categories[cat] || [];
  const top = rows.filter((r) => r.rank <= 10);
  const mine = state.fav && !top.some((r) => r.team === state.fav) && rows.find((r) => r.team === state.fav);
  let html = `<div class="grab"></div>
    <div class="sheet-head"><span class="when">${esc(d.league)} ${esc(d.season)}${d.season !== state.data.season ? " · прошлый сезон" : ""}</span>
    <button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    <h2 class="lead-title">${c.title}<span>топ-10</span></h2>
    <p class="lead-about">${LEAD_ABOUT[cat]}</p>
    <div class="list">${top.map((r) => leaderRow(cat, r)).join("")}`;
  if (mine) html += `<div class="cut"><span>лучший в команде</span></div>${leaderRow(cat, mine)}`;
  html += `</div>`;
  html += `<div class="foot">Места — как в статистике на сайте лиги. Фото игроков не показываем: вместо них — стикер в форме клуба с номером игрока.</div>`;
  showSheet(html);
}

// Сетка клубов по конференциям: первый запуск (attr = data-pick) и лист смены команды (data-switch)
function teamGrid(chosen, attr) {
  let html = "";
  for (const conf of ["east", "west"]) {
    const list = state.data.teams.filter((t) => t.conf === conf).sort((a, b) => a.name.localeCompare(b.name, "ru"));
    html += `<div class="label">${CONF[conf]}<span class="aside">${list.length} команд</span></div><div class="picker">`;
    for (const t of list) {
      html += `<button class="pick${t.id === chosen ? " on" : ""}" ${attr}="${esc(t.id)}" aria-pressed="${t.id === chosen}">
        ${emblem(t.id, "md")}<div><b>${esc(t.name)}</b><small>${esc(t.city)}</small></div></button>`;
    }
    html += `</div>`;
  }
  return html;
}

function renderOnboarding() {
  const chosen = state.draft;
  let html = `<section class="band sky"><h1>За кого<br>болеете?</h1><div class="lede">Главный экран, календарь и таблица подстроятся под команду. Поменять можно в любой момент.</div></section>`;
  html += teamGrid(chosen, "data-pick");
  return html + `<div style="height:24px"></div>`;
}

// ---------- Проводник — талисман клуба (ADR-011) ----------

// У каждого клуба свой проводник: наклейка webapp/mascots/<клуб>-<поза>.webp (режет tools/mascot_stickers.py),
// имя и фразы — teams.json → mascot. Позы: hello — появление и знакомство, point — подсказки тура,
// cheer — финал и отклик на нажатие, shrug — «нет данных» и «Не удалось загрузить». Нет талисмана —
// карточка без картинки. Знакомство, вступление и финал тура — карточка по центру экрана
// "1" — пройден старый тур из трёх подсказок, "2" — тур из четырёх глав, "3" — тур с главой «Лента»
// пройден или пропущен (ADR-016). Значение только растёт: прошлым уровням — один раз карточка «Новое»
const TOUR_KEY = "tour";
const TOUR_CH_KEY = "tour_ch";   // номера пройденных глав по возрастанию: «1245» — для листа «Подсказки»
const TOUR_AT_KEY = "tour_at";   // глава, на которой тур прервали (закрыли Telegram); только на устройстве
const GUIDE_KEY = "guide";       // с чьим проводником болельщик уже знаком: id клуба
const TOUR_LEVEL = 3;
const tourLevel = (v = lsGet(TOUR_KEY)) => (/^[1-3]$/.test(v || "") ? Number(v) : 0);
const tourDone = () => tourLevel() >= TOUR_LEVEL;
const tourSeen = () => tourLevel() > 0;
const guideOf = (club) => (club && state.teams[club] && state.teams[club].mascot) || null;
const guideSrc = (club, pose) => `mascots/${club}-${pose}.webp`;

function guideFig(club, pose) {
  const g = guideOf(club);
  if (!g) return "";
  return `<span class="guide" data-guide="${esc(club)}" role="img" aria-label="${esc(g.name)}"><img src="${guideSrc(esc(club), pose)}" alt="" width="288" height="288" decoding="async"></span>`;
}

// Картинку ждём не дольше 2 секунд: онбординг не стоит из-за медленной сети
function guideReady(club, pose) {
  if (!guideOf(club)) return Promise.resolve(false);
  const img = new Image();
  img.src = guideSrc(club, pose);
  const load = img.decode ? img.decode().then(() => true, () => false)
    : new Promise((done) => { img.onload = () => done(true); img.onerror = () => done(false); });
  return Promise.race([load, new Promise((done) => setTimeout(() => done(false), 2000))]);
}

// Знакомство: нажали на клуб — по центру экрана его талисман, приветствие и «Болеть за …»
function openMeet(club) {
  state.meet = club;
  guideReady(club, "hello").then((ok) => {
    if (state.meet !== club || state.fav) return;   // уже нажали другой клуб, закрыли или выбрали
    const g = guideOf(club);
    const name = esc(team(club).name);
    coach(club, ok ? "hello" : "", g ? esc(g.hi) : `Болеем за «${name}»?`,
      `<button type="button" class="btn" data-confirm>Болеть за «${name}»</button>
       <button type="button" class="coach-skip" data-meet-close>Выбрать другую</button>`, "Знакомство с талисманом");
  });
}

// Нажали на проводника — подпрыгивает и на миг радуется. back — к какой позе вернуться
function guideHop(el, back = null) {
  const img = el && el.querySelector("img");
  if (!img || el.dataset.busy) return;
  const was = back ? guideSrc(el.dataset.guide, back) : img.getAttribute("src");
  el.dataset.busy = "1";
  img.src = guideSrc(el.dataset.guide, "cheer");
  if (!calm()) el.animate([{ transform: "none" }, { transform: "translateY(-14px) rotate(4deg)" }, { transform: "none" }], { duration: 420, easing: EASE_OUT });
  setTimeout(() => { img.src = was; delete el.dataset.busy; }, 900);
}

// Сменил команду — проводник новой любимой команды знакомится одним облачком
function greetGuide() {
  const g = guideOf(state.fav);
  if (!g || !tourSeen() || lsGet(GUIDE_KEY) === state.fav || state.tour || state.openedFromLink || !$("#sheet").hidden) return;
  state.tour = { greet: true };
  coach(state.fav, "hello", `${esc(g.hi)} Теперь подсказки — от меня.`, `<button type="button" class="btn" data-tour="hi">Привет!</button>`, "Подсказки");
}

// ---------- Тур по главам (ADR-013) ----------

// Четыре главы на живых экранах, до старта сезона — 12 шагов. После главы «Разбор матча» —
// контрольная точка: досмотрел главное — можно закончить через финал. Глава — «вход», который готовит
// экран, и шаги. Шаг — цель (aim), поза, реплика и вид: look — смотри, окно не нажимается; pass —
// пройди, нажатие по окну работает по-настоящему, кнопка названа действием и делает то же; peek —
// загляни, нажатие открывает лист, тур гаснет и ждёт, пока его закроют. Список шагов считается заново
// по данным на каждом шаге: шагов без данных нет. Реплики — только через esc(): названия и даты из данных
const AIM_WAIT_MS = 2000;     // цель на экране ждём не дольше
const SHEET_WAIT_MS = 4000;   // в листе дольше: разбор и очные встречи идут по сети
const AIM_PAD = 8;            // окно шире цели с каждой стороны
const AIM_GAP = 14;           // от окна до карточки — там хвостик
const EXIT_HINT_MS = 2500;    // подсказка при выходе у вкладки «Я»
const wait = (ms) => new Promise((done) => setTimeout(done, ms));
const val = (v, ...args) => (typeof v === "function" ? v(...args) : v);
const dayMonth = (iso) => { const d = parseISO(iso); return `${d.getUTCDate()} ${MONTHS_GEN[d.getUTCMonth()]}`; };
const dayMonthYear = (iso) => `${dayMonth(iso)} ${parseISO(iso).getUTCFullYear()}`;
const quoted = (id) => `«${esc(team(id).name)}»`;
const sheetOpen = () => !$("#sheet").hidden && !sheetClosing;
const visible = (el) => !!el && el.getBoundingClientRect().height > 0;
// Первый видимый из селекторов — по старшинству, а не по порядку в документе
function firstShown(root, sels) {
  for (const s of sels) {
    const el = root && root.querySelector(s);
    if (visible(el)) return el;
  }
  return null;
}
const inSheet = (...sels) => (sheetOpen() ? firstShown($("#sheet"), sels) : null);
const onScreen = (...sels) => () => firstShown($("#screen"), sels);
// Цель в разборе — только когда лист показывает тот самый матч
const inRecap = (ctx, ...sels) => (ctx.route && recapView.id === ctx.route.id && recaps[ctx.route.id] ? inSheet(...sels) : null);
const inSeason = () => { const st = standingOf(state.fav); return !!(st && st.row.gp); };
const pastLeaders = () => !!state.leaders && state.leaders.season !== state.data.season;
const wonBy = (m, me) => (m.home === me ? m.score[0] > m.score[1] : m.away === me && m.score[1] > m.score[0]);

// Какую встречу открыть (строки уже от свежей к старой — при равенстве остаётся свежая).
// Своя победа — с сюжетом, иначе любая; побед нет — самая упорная. Чужой матч — с сюжетом
function pickMeeting(rows, me) {
  if (!me) return rows.find((m) => m.story) || rows[0];
  const wins = rows.filter((m) => wonBy(m, me));
  if (wins.length) return wins.find((m) => m.story) || wins[0];
  const margin = (m) => Math.abs(m.score[0] - m.score[1]);
  return [...rows].sort((a, b) => margin(a) - margin(b) || !!b.story - !!a.story)[0];
}

// Маршрут к разбору. В сезоне — свой последний матч. До старта — встреча из карточки ближайшего
// матча; если там своих побед нет — из более позднего матча своей команды, где победа есть (ветка А);
// поражение — только если побед нет нигде: первым разбором не должен стать разгром своей команды.
// Своих прошлых матчей нет вовсе — ближайший матч лиги (ветка Б)
function tourRoute() {
  const me = state.fav;
  const next = nextGame(me) || null;
  const last = lastPlayed(me) || null;
  if (last) return { kind: "season", next, card: last, via: last, meet: null, id: last.id };
  const route = { kind: state.h2h ? "none" : "nodata", next, card: next, via: null, meet: null, id: null };
  if (!state.h2h || !next) return route;
  const rows = (g) => ((state.h2h[pairKey(g.home, g.away)] || {}).last || []).filter((m) => m.id);
  const mine = gamesOf(me).filter((g) => isUpcoming(g) && rows(g).length);
  const own = mine.find((g) => rows(g).some((m) => wonBy(m, me))) || mine[0];
  const via = own || games().find((g) => isUpcoming(g) && rows(g).length);
  if (!via) return route;
  const meet = pickMeeting(rows(via), own ? me : null);
  return { ...route, kind: !own ? "league" : own === next ? "own" : "later", via, meet, id: meet.id };
}

// Маршрут без ожидания: глава I начинается сразу, очные встречи догружаются сами
function syncRoute(ctx) {
  if (!ctx.route || ctx.route.kind === "nodata") ctx.route = tourRoute();
  const prefetch = () => { if (ctx.route.id) loadRecap(ctx.route.id); };   // разбор качаем заранее: откроется сразу
  if (ctx.route.kind !== "nodata") prefetch();
  else loadH2H().then(() => { if (ctx.route.kind === "nodata" && state.h2h) { ctx.route = tourRoute(); prefetch(); } });
  return ctx.route;
}
// Для главы «Разбор матча» маршрут нужен: ждём очные встречи не дольше, чем цель в листе
async function ensureRoute(ctx) {
  syncRoute(ctx);
  if (ctx.route.kind !== "nodata") return ctx.route;
  await Promise.race([loadH2H(), wait(SHEET_WAIT_MS)]);
  return syncRoute(ctx);
}

// Победная шайба глазами своей команды: a — своя, не последняя; b — своя, последняя (и овертайм);
// c — победный буллит свой; d — у соперника; e — матч чужих команд; none — победной нет (ADR-008)
function winVariant(ctx) {
  const d = recaps[ctx.route.id];
  const g = findGame(ctx.route.id);
  const me = state.fav;
  if (!d || !g || d.gw == null || !g.goals || !g.goals[d.gw]) return "none";
  if (g.home !== me && g.away !== me) return "e";
  const x = g.goals[d.gw];
  if (x.team !== (g.home === me ? "home" : "away")) return "d";
  if (x.period === "РБ") return "c";
  return d.gw === g.goals.length - 1 ? "b" : "a";
}
const WIN_SAY = {
  a: ["cheer", "Вот она — победная! Не последняя шайба, а та, после которой соперник уже не отыгрался."],
  b: ["cheer", "Вот она — победная шайба! В списке голов ниже она тоже отмечена."],
  c: ["cheer", "Всё решили буллиты — вот победный. На графике буллитов нет, они в списке голов."],
  d: ["shrug", "В тот раз победная — у соперника: после этой шайбы счёт уже не сравняли."],
  e: ["point", "Это победная шайба: после неё соперник уже не сравнял счёт. Так её считает лига."],
};

// Очные встречи ближайшего матча. В ветках А и Б — одна карточка: что здесь и куда пойдём за разбором
function h2hSay(ctx, el) {
  const r = ctx.route;
  const g = findGame(recapView.id);
  const empty = !!el && el.matches(".empty");
  const never = g ? `${quoted(g.home)} и ${quoted(g.away)} ещё не встречались` : "Раньше не встречались";
  const history = "Очные встречи прошлых сезонов: кто сколько выиграл и забил.";
  if (r.kind === "later") {
    const other = `на другом твоём матче — ${dayMonth(r.via.date)}.`;
    return empty ? ["shrug", `${never}. Разбор покажу ${other}`] : ["point", `${history} А разбор покажу ${other}`];
  }
  if (r.kind === "league") {
    const opener = r.via.date === games()[0].date;
    if (empty) return ["shrug", `${never}, а прошлых матчей твоей команды у нас нет. Покажу разбор ${opener ? "матча открытия" : `матча лиги — ${dayMonth(r.via.date)}`}.`];
    return ["point", `${history} Разбор покажу на ${opener ? "матче открытия" : `матче лиги — ${dayMonth(r.via.date)}`}.`];
  }
  return empty ? ["shrug", `${never} — истории пока нет.`] : ["point", `${history} Это история, а не прогноз.`];
}

// Прилипшие сегменты разбора — туда, где они стоят без прилипания, у верха листа
function recapTab(tab) {
  if (recapView.tab === tab) return;
  recapView.tab = tab;
  rerenderRecap();
  keepTabTop();
}
const otherMatch = (ctx) => ctx.route.kind === "later" || ctx.route.kind === "league";

const STEP = {
  // I. Ближайший матч
  card: {
    key: "card", screen: "home", kind: "pass", pose: "point", act: "Открыть матч",
    aim: (ctx) => firstShown($("#screen"), [`.board-card[data-game="${esc(ctx.route.card.id)}"]`]),
    text: (ctx) => {
      const g = ctx.route.card;
      if (g.score) return "Последний матч твоей команды — нажми, внутри его разбор.";
      return `Ближайший матч — ${dayMonth(g.date)}, ${g.home === state.fav ? "дома" : "в гостях"}. Нажми на него: внутри история встреч с соперником.`;
    },
  },
  h2h: {
    key: "h2h", screen: "match", kind: "look", sheet: true,
    aim: () => inSheet("#h2h .h2h", "#h2h .empty:not(.guide-empty)"),
    fail: () => !!inSheet("#h2h [data-retry]"),
    failAim: () => inSheet("#h2h .guide-empty", "#h2h .empty"),
    failText: "Историю встреч не загрузить — нет сети. Идём дальше.",
    // h2h.json не пришёл к началу тура, а карточка матча догрузила его сама — маршрут считаем заново
    seen: (ctx) => { if (ctx.route.kind === "nodata" && state.h2h) syncRoute(ctx); },
    pose: (ctx, el) => h2hSay(ctx, el)[0],
    text: (ctx, el) => h2hSay(ctx, el)[1],
    act: (ctx) => (otherMatch(ctx) ? "Показать" : "Дальше"),
    // ветки А и Б: тот матч открывается в том же листе, содержимое въезжает справа
    after: (ctx) => { if (otherMatch(ctx)) openMatch(ctx.route.via.id, null, 1); },
  },
  meet: {
    key: "meet", screen: (ctx) => (otherMatch(ctx) ? "via" : "match"), kind: "pass", sheet: true, pose: "point",
    act: "Открыть разбор",   // кнопка одиночной главы: разбор откроется, тур закончится
    aim: (ctx) => (recapView.id === ctx.route.via.id ? inSheet(`#h2h .row[data-game="${esc(ctx.route.meet.id)}"]`) : null),
    text: (ctx) => (ctx.single ? "Прошлые встречи открываются: нажми — будет разбор матча."
      : `Прошлые встречи открываются. Нажми на матч ${dayMonthYear(ctx.route.meet.date)} — покажу разбор.`),
  },

  // II. Разбор матча. Сюжет — вместе с табло: главное одной фразой рядом со счётом
  story: {
    key: "story", screen: "recap", kind: "look", sheet: true, pose: "point",
    aim: (ctx) => inRecap(ctx, "#recap .story", ".sheet-page > .board-card"),
    extend: (el) => [$("#sheet .sheet-page > .board-card"), el], only: true, radius: 28,
    text: (ctx) => {
      const g = findGame(ctx.route.id);
      const when = g.season ? `НМХЛ ${esc(g.season)}: с` : "С";
      return recaps[ctx.route.id].story
        ? `${when}чёт и главное — одной фразой. Так будет у каждого матча сезона.`
        : `${when}чёт и шайбы по периодам. Разбор будет у каждого матча сезона.`;
    },
  },
  flow: {
    key: "flow", screen: "recap", kind: "pass", sheet: true, pose: "point", act: "Дальше", hit: "[data-recap-goal]",
    hold: 650, holdOnPress: true,   // выбранный гол виден на месте и после кнопки
    aim: (ctx) => inRecap(ctx, "#recap .fl-plot"),
    text: () => "Ход матча: выше линии ведут хозяева, ниже — гости. Точки — голы. Нажми на любую.",
    // линия рисуется слева направо, когда окно уже встало на график (DESIGN.md → «Движение»)
    shown: () => {
      if (calm()) return;
      const t = state.tour;
      setTimeout(() => { if (state.tour === t && t.key === "flow") { recapView.drawn = null; rerenderRecap(); } }, 200);
    },
    // «Дальше» — тур сам выбирает первый гол своей команды
    press: (ctx) => {
      const g = findGame(ctx.route.id);
      const side = g.home === state.fav ? "home" : g.away === state.fav ? "away" : null;
      const field = g.goals.map((x, i) => ({ x, i })).filter((o) => o.x.period !== "РБ");
      const first = field.find((o) => !side || o.x.team === side) || field[0];
      recapView.pick = first.i;
      rerenderRecap();
    },
  },
  winner: {
    key: "winner", screen: "recap", kind: "look", sheet: true,
    prep: (ctx) => {
      const d = recaps[ctx.route.id];
      if (d.gw != null && recapView.pick !== d.gw) { recapView.pick = d.gw; rerenderRecap(); }
    },
    // окно — на графике и выбранном голе под ним: видно и точку победной, и сам гол. Легенда — за окном
    aim: (ctx) => (winVariant(ctx) === "none" ? inRecap(ctx, "#recap .recap-body .goal")
      : inRecap(ctx, "#recap .flow", "#recap .recap-body .goal.hl")),
    extend: ".fl-plot, .flow-cap", only: true, radius: 20,
    pose: (ctx) => (WIN_SAY[winVariant(ctx)] || ["point"])[0],
    text: (ctx) => {
      const v = winVariant(ctx);
      if (WIN_SAY[v]) return WIN_SAY[v][1];
      const pens = recaps[ctx.route.id].penalties || [];
      return `Все голы матча — по периодам, с передачами.${pens.length ? " Удаления — по переключателю." : ""}`;
    },
    // своя победная — отклик Telegram «успех»
    shown: (ctx) => { if ("abc".includes(winVariant(ctx)) && inTelegram && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success"); },
  },
  // «Статистика» одной карточкой: вкладку открывает сам тур, окно — на сравнении
  stats: {
    key: "stats", screen: "recap", kind: "look", sheet: true, pose: "point",
    prep: () => recapTab("stats"),
    aim: (ctx) => (recapView.tab === "stats" ? inRecap(ctx, "#recap .cmp") : null),
    text: () => "Это «Статистика»: броски, вбрасывания, штраф. Полоса залита у того, кто лучше. Ниже — вратари.",
    act: (ctx) => (recaps[ctx.route.id].lineups ? "Составы" : "Дальше"),
    after: (ctx) => { if (recaps[ctx.route.id].lineups) recapTab("roster"); },
  },
  roster: {
    key: "roster", screen: "recap", kind: "look", sheet: true, pose: "point",
    prep: () => recapTab("roster"),
    aim: (ctx) => (recapView.tab === "roster" ? inRecap(ctx, "#recap .roster .pl.scored", "#recap .roster .pl") : null),
    text: () => "Составы на тот матч: номер и очки, «К» — капитан, «А» — ассистент. Вместо фото — стикер в форме.",
  },
  // в сезоне очные встречи — после разбора, в той же карточке ниже
  seasonH2H: {
    key: "seasonH2H", screen: "recap", kind: "look", sheet: true,
    aim: () => inSheet("#h2h .h2h", "#h2h .empty:not(.guide-empty)"),
    pose: (ctx, el) => (el && el.matches(".empty") ? "shrug" : "point"),
    text: (ctx, el) => {
      if (!el || !el.matches(".empty")) return "В той же карточке — очные встречи прошлых сезонов: кто сколько выиграл и забил.";
      const g = findGame(ctx.route.id);
      return `${quoted(g.home)} и ${quoted(g.away)} ещё не встречались — истории пока нет.`;
    },
  },
  recapFail: {
    key: "recapFail", screen: "recap", kind: "look", noMark: true, wait: 300,
    pose: (ctx, el) => (el && el.querySelector(".guide") ? "" : "shrug"),
    aim: () => inSheet("#recap .guide-empty"),
    text: () => "Разбор не загрузился — нет сети. Вернёмся к нему в «Я».",
  },
  noRecap: {
    key: "noRecap", screen: "recap", kind: "look", pose: "shrug", noMark: true, wait: 0, aim: () => null,
    text: () => "Разборов пока нет: они появятся, когда лига выложит протоколы первых матчей.",
  },

  // «Лента» (ADR-016): истории — по-настоящему, ленту болельщик листает сам. Жест нарисован в окне
  packs: {
    key: "packs", screen: "home", kind: "pass", pose: "point", act: "Открыть истории", hit: "[data-pack]",
    aim: onScreen('#packs .packs[role="group"]'),
    text: () => "Это истории — свежие посты клубов из их каналов. Кольцо — есть новое. Нажми на кружок.",
    press: () => { const p = packs()[0]; if (p) openPack(p.key); },
  },
  // окно — картинка поста (нет её — текст): тогда карточка встаёт под ней, а не закрывает историю
  flip: {
    key: "flip", screen: "story", kind: "pass", sheet: true, pose: "point", act: "Следующий пост", hit: "[data-pack-nav]",
    gesture: "tap", hold: 700, holdOnPress: true,   // новый пост виден на месте, потом лист закрывается
    aim: () => (state.pack ? inSheet("#sheet .pack-page") : null),
    extend: (el) => [el.querySelector(".pack-img") || el.querySelector(".pack-text") || el], only: true,
    text: () => "Касание справа — следующий пост, слева — назад. Свайп вбок — другой клуб. Таймера нет.",
    press: () => packNav(1),
  },
  // окно — лист дня вместе с лентой лиги, выше экрана: карточка встаёт внизу, лента листается под ней
  stream: {
    key: "stream", screen: "home", kind: "swipe", pose: "point", act: "Листать", gesture: "swipe",
    hold: 650, holdOnPress: true,
    prep: async () => {
      await leaveSheet();
      if (state.tab !== "home") go("home");
      if (state.streamFilter !== "all") {
        state.streamFilter = "all";
        refreshStream();
      }
    },
    aim: () => firstShown($("#screen"), ["#feed", "#stream-wrap"]),
    extend: () => [$("#stream-wrap")],
    text: () => (streamHas()
      ? "Ниже — лист дня о твоём клубе, под ним — лента лиги за неделю. Листай вниз, она подгрузится сама."
      : "Ниже — лист дня о твоём клубе: матчи, очные встречи, посты каналов. Листай вниз."),
    press: () => window.scrollBy({ top: Math.round(innerHeight * 0.6), behavior: calm() ? "auto" : "smooth" }),
  },

  // III. Календарь и таблица
  filters: {
    key: "filters", screen: "calendar", kind: "look", pose: "point", aim: onScreen(".filterbar"),
    text: () => "Календарь: твоя команда, вся лига или любой клуб — «Другая». Ниже — дома или в гостях.",
  },
  // строка своей команды — только в сезоне: до старта у всех нули
  standing: {
    key: "standing", screen: "table", kind: "look", pose: "point",
    prep: () => {
      state.conf = team(state.fav).conf;
      if (state.tab !== "table") {
        state.tableView = "teams";
        go("table");
      } else if (state.tableView !== "teams") {
        state.tableView = "teams";
        refreshTable();
      } else {
        render();
      }
    },
    aim: onScreen(".st-row.me"),
    text: () => "Твоё место в конференции. Нажми на любой клуб — откроется его календарь.",
  },
  // своя карточка лидера — кроме «Штрафа»: «лучший по штрафу» не хвалят
  leaders: {
    key: "leaders", screen: "table", kind: "peek", pose: "point", hit: "[data-lead-open]", extend: ".lc-em", wait: SHEET_WAIT_MS,
    fail: () => !state.leaders && leadersFailed, failSkip: true,
    prep: () => {
      if (state.tab !== "table") {
        state.tableView = "players";
        go("table");
      } else if (state.tableView !== "players") {
        state.tableView = "players";
        refreshTable();
      }
    },
    aim: onScreen('.lead-card.me:not([data-lead-open="pim"])', ".lead-card[data-lead-open]"),
    text: () => {
      const mine = mineBlock(state.leaders) ? " Ниже — твои среди них." : "";
      return pastLeaders()
        ? `Лидеры прошлого сезона ${esc(state.leaders.league)} по шести показателям. Нажми — будет топ-10.${mine}`
        : `Лидеры сезона по шести показателям. Нажми — будет топ-10.${mine}`;
    },
  },

  // IV. Паспорт болельщика. В карточке картинки нет: талисман — тот, что наклеен на паспорт, в окне
  passport: {
    key: "passport", screen: "me", kind: () => (guideOf(state.fav) ? "pass" : "look"), pose: "", act: "Дальше",
    aim: onScreen(".passport"), extend: ".pp-guide img", hit: ".pp-guide [data-guide]", hold: 700, noCheer: true,
    press: () => {},   // «Дальше» — без прыжка и без паузы
    text: () => (guideOf(state.fav) ? "Твой паспорт болельщика. А это я на нём — нажми на меня!" : "Твой паспорт болельщика: клуб и цифры сезона."),
  },
  // «Поделиться» и настройки — одним шагом: окно на кнопках, настройки — словами
  share: {
    key: "share", screen: "me", kind: "look", pose: "point", aim: onScreen(".me-actions"),
    text: () => (state.fav === REMIND_TEAM && state.data.links && state.data.links.bot
      ? "Выложи паспорт в историю или позови друга. Ниже — напоминания, смена команды и эти подсказки."
      : "Выложи паспорт в историю или позови друга. Ниже — смена команды и эти подсказки."),
  },
};

const ICON_CH = {
  1: '<svg viewBox="0 0 24 24"><rect x="3.5" y="5.5" width="17" height="13" rx="4"/><path d="M12 5.5v13"/><circle cx="12" cy="12" r="2.5"/></svg>',
  2: '<svg viewBox="0 0 24 24"><path d="M3.5 15.5h4v-5h4v3h4v-6h5"/><path d="M3.5 20h17"/></svg>',
  3: '<svg viewBox="0 0 24 24"><path d="M5 20V11M12 20V4M19 20v-6"/></svg>',
  4: '<svg viewBox="0 0 24 24"><rect x="4.5" y="3.5" width="15" height="17" rx="3"/><circle cx="12" cy="10" r="2.8"/><path d="M8 16.5c.8-1.6 2.2-2.4 4-2.4s3.2.8 4 2.4"/></svg>',
  5: '<svg viewBox="0 0 24 24"><circle cx="7" cy="7" r="3.5"/><circle cx="17" cy="7" r="3.5"/><path d="M3.5 14h17M3.5 17.5h17M3.5 20.5h10"/></svg>',
};

// Лист, который открыл тур, закрывается перед сменой экрана
function leaveSheet() {
  if ($("#sheet").hidden) return Promise.resolve();
  closeMatch();
  return wait(calm() ? 0 : 240);
}

// title — в листе «Подсказки» и «Продолжении», short — в ряду прогресса и на кнопке «Дальше: …»
const TOUR = [
  {
    id: 1, title: "Ближайший матч", short: "Матч", hint: "Карточка матча, очные встречи, прошлая встреча",
    enter: async (ctx) => {
      syncRoute(ctx);   // очные встречи не ждём: первому шагу они не нужны
      await leaveSheet();
      if (state.tab !== "home") go("home");
      else window.scrollTo(0, 0);
    },
    steps: (ctx) => {
      const r = ctx.route || tourRoute();
      if (!r.card) return [];
      if (r.kind === "season") return [STEP.card];
      return r.meet ? [STEP.card, STEP.h2h, STEP.meet] : [STEP.card, STEP.h2h];
    },
  },
  {
    id: 2, title: "Разбор матча", short: "Разбор", hint: "Сюжет, ход матча, победная шайба, статистика, составы",
    enter: async (ctx, t) => {
      const { id } = await ensureRoute(ctx);
      if (!id) return;
      const d = await loadRecap(id);   // пришли нажатием по встрече — лист откроет её обработчик
      if (!d || state.tour !== t || (sheetOpen() && recapView.id === id)) return;
      await leaveSheet();
      if (state.tour !== t) return;
      openMatch(id);
      t.sheet = true;
    },
    steps: (ctx) => {
      const id = ctx.route && ctx.route.id;
      if (!id) return ctx.single ? [STEP.noRecap] : [];
      const d = recaps[id];
      const g = findGame(id);
      if (!d || !g) return [STEP.recapFail];
      const list = [STEP.story];
      const goals = g.goals || [];
      if (goals.some((x) => x.period !== "РБ")) list.push(STEP.flow);
      if (goals.length) list.push(STEP.winner);
      if (hasStats(d)) list.push(STEP.stats);
      if (d.lineups) list.push(STEP.roster);
      if (ctx.route.kind === "season") list.push(STEP.seasonH2H);
      return list;
    },
  },
  // id глав хранятся в tour_ch и tour_at, поэтому «Лента» — пятая по номеру, но третья в очереди (ADR-016)
  {
    id: 5, title: "Истории и лента", short: "Лента", hint: "Как листать истории клубов и ленту лиги",
    enter: async () => {
      await leaveSheet();
      if (state.tab !== "home") go("home");
      else window.scrollTo(0, 0);
      // ряд и лента обычно уже пришли вместе с «Главной»; нет — ждём не дольше, чем цель в листе
      await Promise.race([Promise.all([loadStream(), loadFeed(state.fav)]), wait(SHEET_WAIT_MS)]);
    },
    steps: feedSteps,
  },
  {
    id: 3, title: "Календарь и таблица", short: "Календарь", hint: "Фильтры календаря, лидеры лиги",
    enter: async () => {
      await leaveSheet();
      state.cal = calFor(state.fav);
      if (state.tab !== "calendar") go("calendar");
      else refreshCalendar(false);
      loadLeaders();   // не ждём: шаг лидеров сам дождётся их
    },
    steps: () => {
      const list = [STEP.filters];
      if (inSeason()) list.push(STEP.standing);
      if (state.leaders || !leadersFailed) list.push(STEP.leaders);
      return list;
    },
  },
  {
    id: 4, title: "Паспорт болельщика", short: "Паспорт", hint: "Паспорт, история в Telegram, приглашение друга, настройки",
    enter: async () => {
      await leaveSheet();
      if (state.tab !== "me") go("me");
      else window.scrollTo(0, 0);
    },
    steps: () => [STEP.passport, STEP.share],
  },
];
const CHAPTER = Object.fromEntries(TOUR.map((ch) => [ch.id, ch]));
const FULL = TOUR.map((ch) => ch.id);
const FEED = 5;
const MAIN = [1, 2, FEED];   // главное: до контрольной точки
const ord = (id) => FULL.indexOf(id);   // место главы в очереди — не её номер

// Шаги «Ленты» по данным: ряд «Сегодня» пуст — только лента; нет ни листа, ни ленты — глава пустая
const streamHas = () => !!(state.stream && (state.stream.items || []).length);
function feedSteps() {
  const list = packs().length ? [STEP.packs, STEP.flip] : [];
  const f = state.feed[state.fav];
  if (streamHas() || (f && feedCards(f).length)) list.push(STEP.stream);
  return list;
}

// Тур: chapters — очередь глав, seg — отрезки прогресса (продолжение показывает и пройденные),
// final — финальная карточка (полная очередь, в том числе прерванная очередь из четырёх глав), save —
// помнить главу и очередь на случай, если Telegram закроют посреди тура
function newTour({ chapters = FULL, seg = chapters, save = false } = {}) {
  const t = { chapters, seg, final: [1, 2, 3, 4].every((c) => seg.includes(c)), save, ci: -1, key: null, ctx: { route: null, single: chapters.length === 1 },
    token: 0, screen: null, poses: {}, lastPose: "", misses: 0, cheer: false, away: false, sheet: false, busy: false,
    primary: null, prog: null, sawMain: false, checked: false };
  // позы качаем заранее: shrug нужен и в ветках без данных
  for (const p of ["point", "cheer", "shrug"]) guideReady(state.fav, p).then((ok) => { t.poses[p] = ok; });
  syncRoute(t.ctx);
  return t;
}

// Вступление: fresh — сразу после «Болеть за…», иначе тур начался позже и проводник здоровается
function startTour(fresh) {
  const t = state.tour = newTour({ save: true });
  const g = guideOf(state.fav);
  const say = "Покажу главное: разбор матча, истории и ленту, таблицу и твой паспорт. Минуты две, выйти можно в любой момент.";
  t.primary = () => tourChapter(0);
  tourCard(t, fresh ? "cheer" : "hello", fresh || !g ? say : `Привет! Я ${esc(g.name)}. ${say}`, "Поехали", "Сам разберусь", segsHTML(t, 0));
}

// Прошли тур раньше — один раз рассказываем, чему проводник научился: после старого тура из трёх
// подсказок — разбор матча, истории и лента, после тура из четырёх глав — истории и лента (ADR-016).
// Глава «Лента» без ряда и ленты пуста — тогда «Новое» ждёт следующего запуска
async function tourNews() {
  const old = tourLevel() === 1;
  await Promise.race([Promise.all([loadStream(), loadFeed(state.fav)]), wait(SHEET_WAIT_MS)]);
  if (state.tour || !state.fav || tourDone() || !$("#sheet").hidden) return;
  if (!old && !feedSteps().length) return;
  const t = state.tour = newTour({ chapters: old ? [1, 2, FEED] : [FEED], save: true });
  t.primary = () => tourChapter(0);
  tourCard(t, "hello", old ? "Я научился показывать разбор матча, истории клубов и ленту. Показать?"
    : "Я научился показывать истории клубов и ленту — как их листать. Показать?", "Покажи", "Не надо");
}

// Прерванный тур в tour_at: «глава:очередь» — «2:1234» или «1:12» («Новое»); старый формат — только глава
function savedAt() {
  const m = /^(\d)(?::(\d+))?$/.exec(lsGet(TOUR_AT_KEY) || "");
  if (!m) return null;
  const at = +m[1];
  const queue = (m[2] || FULL.join("")).split("").map(Number).filter((c) => CHAPTER[c]);
  return CHAPTER[at] && queue.includes(at) ? { at, queue } : null;
}
function saveAt(t, id) {
  if (t.save) lsSet(TOUR_AT_KEY, `${id}:${t.seg.join("")}`);
}

// Тур прервали, закрыв Telegram, — один раз предлагаем продолжить с начала той главы, той же очередью
function tourResume({ at, queue }) {
  const t = state.tour = newTour({ chapters: queue.slice(queue.indexOf(at)), seg: queue, save: true });
  t.primary = () => tourChapter(0);
  tourCard(t, "hello", `Мы остановились на главе «${CHAPTER[at].title}». Продолжим с её начала?`, "Продолжить", "Не надо");
}

// Карточка тура по центру: картинку ждём не дольше 2 секунд, как при знакомстве.
// no — серая кнопка: по умолчанию выход из тура, у контрольной точки — к финалу
function tourCard(t, pose, text, yes, no, extra = "", noAct = "skip") {
  guideReady(state.fav, pose).then((ok) => {
    if (state.tour !== t) return;
    coach(state.fav, ok ? pose : "", text, `<button type="button" class="btn" data-tour="next">${yes}</button>
      ${no ? `<button type="button" class="coach-skip" data-tour="${noAct}">${no}</button>` : ""}`, "Подсказки", extra);
  });
}

async function tourChapter(ci) {
  const t = state.tour;
  if (!t) return;
  const ch = CHAPTER[t.chapters[ci]];
  if (!ch) return tourFinish();
  if (checkpointDue(t, ch.id)) return tourCheckpoint(ci);
  t.ci = ci;
  t.key = null;
  t.screen = null;
  t.busy = true;
  saveAt(t, ch.id);
  const token = ++t.token;
  coachDim("");   // пока экран собирается и данные идут — только затемнение
  await ch.enter(t.ctx, t);
  if (state.tour !== t || t.token !== token) return;
  const list = ch.steps(t.ctx);
  if (!list.length) return tourChapter(ci + 1);
  tourShow(list[0]);
}

// Контрольная точка — перед первой главой не из главного, если «Разбор» или «Ленту» досмотрели: главное
// показано, можно закончить через финал. ci — глава, к которой она ведёт
const checkpointDue = (t, id) => t.final && !t.checked && t.sawMain && !MAIN.includes(id);
async function tourCheckpoint(ci) {
  const t = state.tour;
  if (!t) return;
  const rest = t.chapters.slice(ci);
  t.checked = true;
  t.key = "checkpoint";
  t.screen = null;
  t.busy = true;
  t.cheer = false;   // прыжок за «листай» уже не к чему: дальше карточка по центру
  saveAt(t, rest[0]);
  const token = ++t.token;
  coachDim("");
  await leaveSheet();
  if (state.tour !== t || t.token !== token) return;
  const names = { 3: "таблица", 4: "твой паспорт" };
  const more = rest.length > 1 ? `Ещё две короткие главы — ${rest.map((c) => names[c]).join(" и ")}.` : `Ещё одна короткая глава — ${names[rest[0]]}.`;
  t.primary = () => tourChapter(ci);
  t.busy = false;
  tourCard(t, "point", `Это было главное. ${more} Показать?`, "Покажи", "Хватит", segsHTML(t, Math.max(0, t.seg.indexOf(rest[0]))), "final");
}

const tabOf = (screen) => (TAB_ORDER.includes(screen) ? screen : "");
const stepOf = (t) => {
  const ch = t && t.ci >= 0 && CHAPTER[t.chapters[t.ci]];
  return ch ? ch.steps(t.ctx).find((s) => s.key === t.key) || null : null;
};

// Следующая глава в очереди — её короткое имя на кнопке последнего шага. Дальше контрольная точка
// или финал — просто «Дальше»; «Готово» — только в конце одиночной главы. after — глава, после которой
// ищем (по месту в очереди, не по номеру); без него — после текущей
function nextChapter(t, after) {
  const r = t.ctx.route;
  // разбора нет или ни ряда, ни ленты — глава пропустится
  const empty = (c) => !t.ctx.single && ((c === 2 && r && r.kind !== "nodata" && !r.id) || (c === FEED && state.stream !== undefined && !feedSteps().length));
  return t.chapters.find((c, i) => (after == null ? i > t.ci : ord(c) > ord(after)) && !empty(c)) || null;
}
function nextTitle(t, after) {
  const id = nextChapter(t, after);
  if (id && checkpointDue(t, id)) return "Дальше";
  return id ? `Дальше: ${CHAPTER[id].short}` : t.final ? "Дальше" : "Готово";
}

async function tourShow(s) {
  const t = state.tour;
  if (!t) return;
  const ctx = t.ctx;
  const ch = CHAPTER[t.chapters[t.ci]];
  const token = ++t.token;
  t.key = s.key;
  t.misses = 0;
  t.busy = true;
  const screen = val(s.screen, ctx);
  // тот же экран — карточка не гаснет, а переезжает к новой цели; окно гаснет на время перестройки
  const same = screen === t.screen && !!$("#tour.aim .coach-card");
  t.screen = screen;
  if (same) await darkWindow();
  else coachDim(tabOf(screen));
  if (state.tour !== t || t.token !== token) return;
  if (s.prep) await s.prep(ctx);
  if (s.sheet && sheetOpen()) t.sheet = true;
  const found = await findAim(() => s.aim(ctx), s.fail ? () => s.fail(ctx) : null, s.wait != null ? s.wait : s.sheet ? SHEET_WAIT_MS : AIM_WAIT_MS);
  if (state.tour !== t || t.token !== token) return;
  if (found.failed && s.failSkip) return tourNext(t);   // не загрузилось то, без чего шаг не нужен
  if (found.failed) return tourFail(t, s);
  const el = found.el;
  if (el && s.seen) s.seen(ctx, el);
  const list = ch.steps(ctx);
  const i = Math.max(0, list.findIndex((x) => x.key === s.key));
  const last = i === list.length - 1;
  if (last && !s.noMark) {
    markChapter(ch.id);
    if (ch.id === 2 || ch.id === FEED) t.sawMain = true;
  }
  const kind = val(s.kind, ctx);
  // одиночная глава кончается шагом «пройди» — кнопка называет действие: после него тур закончится.
  // «Листай» — всегда действие: кнопка листает сама
  const label = !last || kind === "swipe" ? val(s.act, ctx) || "Дальше"
    : kind === "pass" && s.act && !t.final && !nextChapter(t) ? val(s.act, ctx) : nextTitle(t);
  const pose = tourPose(t, val(s.pose, ctx, el));
  const text = val(s.text, ctx, el);
  t.primary = () => tourPrimary(t, s, kind, last);
  const buttons = `<button type="button" class="btn" data-tour="next">${label}</button>
    <div class="coach-row"><button type="button" class="coach-skip" data-tour="skip">Выйти</button>${progressHTML(t, ch, i, list.length)}</div>`;
  const cheer = t.cheer && !s.noCheer && pose && t.poses.cheer !== false;
  t.cheer = false;
  if (el) coachAt(el, { pose, text, buttons, tab: tabOf(screen), same, cheer, find: () => s.aim(ctx), extend: s.extend, only: s.only, radius: s.radius,
    gesture: s.gesture, scroll: kind === "swipe" });
  else coach(state.fav, pose, text, buttons, "Подсказки");   // цель не появилась — карточка по центру
  animateProgress(t, ch, i, list.length);
  t.busy = false;
  if (el && kind === "swipe") watchSwipe(t, s);
  if (el && s.shown) s.shown(ctx, el);
}

// Поза не загрузилась — остаётся прежняя, пустого места нет
function tourPose(t, pose) {
  if (!pose) return "";
  if (t.poses[pose] === false) return t.lastPose;
  t.lastPose = pose;
  return pose;
}

// Не загрузилось (очные встречи, разбор) — честно, с shrug, и дальше к главе после разбора.
// Главы I и II не отмечаются, контрольной точки нет: главное не показано
async function tourFail(t, s) {
  const el = s.failAim ? s.failAim(t.ctx) : null;
  const label = nextTitle(t, 2);
  t.primary = () => { leaveSheet(); tourAfter(2); };
  const buttons = `<button type="button" class="btn" data-tour="next">${label}</button>
    <div class="coach-row"><button type="button" class="coach-skip" data-tour="skip">Выйти</button></div>`;
  // в блоке «Не удалось загрузить» талисман уже разводит руками — в карточке картинки нет
  if (el) coachAt(el, { pose: el.querySelector(".guide") ? "" : tourPose(t, "shrug"), text: s.failText, buttons, tab: "", same: false, find: () => s.failAim(t.ctx) });
  else coach(state.fav, tourPose(t, "shrug"), s.failText, buttons, "Подсказки");
  t.busy = false;
}

// Главная кнопка шага: «пройди» — то же, что нажатие по цели; «смотри» — дальше (и действие шага)
function tourPrimary(t, s, kind, last) {
  if (kind === "pass" || kind === "swipe") return tourPass(t, s, null);
  if (!last && s.after) s.after(t.ctx);
  tourNext(t);
}

// Нажатие по цели на шаге «пройди». hit — куда нажал сам человек; null — нажали кнопку карточки
function tourPass(t, s, hit, x = 0, y = 0) {
  const ctx = t.ctx;
  t.busy = true;
  if (hit) {
    hit.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, clientX: x, clientY: y }));   // у SVG нет .click()
    t.cheer = !s.noCheer;   // сам нажал — следующая карточка радуется
  } else if (s.press) {
    s.press(ctx);
  } else if (aimEl()) {
    aimEl().dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
  }
  const advance = () => { if (state.tour === t) { t.busy = false; tourNext(t); } };
  // результат нажатия виден на месте: прыжок талисмана, выбранный гол (у графика — и после кнопки)
  if (s.hold && (hit || s.holdOnPress)) setTimeout(advance, s.hold);
  else advance();
}

function tourNext(t = state.tour) {
  if (!t || state.tour !== t) return;
  const ch = CHAPTER[t.chapters[t.ci]];
  if (!ch) return;
  const list = ch.steps(t.ctx);
  const i = list.findIndex((x) => x.key === t.key);
  if (i >= 0 && i + 1 < list.length) return tourShow(list[i + 1]);
  if (t.ci + 1 < t.chapters.length) return tourChapter(t.ci + 1);
  tourFinish();
}

// К первой главе очереди после этой (по месту в очереди); дальше нет — тур закончен
function tourAfter(id) {
  const t = state.tour;
  if (!t) return;
  const ci = t.chapters.findIndex((c) => ord(c) > ord(id));
  if (ci >= 0) return tourChapter(ci);
  tourFinish();
}

// Конец очереди или «Хватит»: полный тур — финал по центру, одиночная глава — «Готово», человек
// остаётся на экране
async function tourFinish() {
  const t = state.tour;
  if (!t) return;
  if (!t.final) return tourEnd();
  saveTourDone(t);   // главное показано: закрыли Telegram на финале — тур всё равно пройден
  t.ci = -1;
  t.key = "final";
  t.busy = true;
  const token = ++t.token;
  if (t.sheet && !$("#sheet").hidden) {
    coachDim("");
    await leaveSheet();
    if (state.tour !== t || t.token !== token) return;
  }
  t.busy = false;
  t.primary = () => tourEnd();
  const bot = state.data.links && state.data.links.bot;
  const g = guideOf(state.fav);
  if (state.fav === REMIND_TEAM && bot) {
    coach(state.fav, g ? "cheer" : "", "Напомнить о матче? Напишу в боте накануне и в день игры, а после — пришлю счёт и разбор.",
      `<button type="button" class="btn" data-tour="remind">Напомнить</button>
       <button type="button" class="coach-skip" data-tour="done">Не сейчас</button>`, "Подсказки");
  } else {
    coach(state.fav, g ? "cheer" : "", g ? `${esc(g.bye)} Подсказки — в «Я», по главам.` : "Всё, болеем! Подсказки — в «Я», по главам.",
      `<button type="button" class="btn" data-tour="next">Поехали</button>`, "Подсказки");
  }
}

// Уровень только растёт. Тур без «Ленты» (прерванная очередь из четырёх глав, одна глава из
// «Подсказок») ставит не выше 2: о ленте ещё расскажет «Новое»
function saveTourDone(t) {
  const v = String(Math.max(tourLevel(), t && !t.seg.includes(FEED) ? 2 : TOUR_LEVEL));
  lsSet(TOUR_KEY, v);
  try { localStorage.removeItem(TOUR_AT_KEY); } catch (e) { /* приватный режим */ }
  if (state.fav) lsSet(GUIDE_KEY, state.fav);
  if (cloud()) cloud().setItem(TOUR_KEY, v, () => {});
}

// Тур закончен. После финала — «Главная», наверху: оттуда приложение и начинается
function tourEnd() {
  const t = state.tour;
  if (t && !t.greet) saveTourDone(t);
  if (t && t.greet && state.fav) lsSet(GUIDE_KEY, state.fav);
  state.tour = null;
  closeCoach();
  if (t && t.key === "final") {
    if (state.tab !== "home") go("home");
    else window.scrollTo(0, 0);
  }
}

// «Выйти», Esc, «Сам разберусь», «Не надо»: тур закрыт сразу и считается пройденным; лист тура
// закрывается, у вкладки «Я» — где подсказки потом
function tourSkip() {
  const t = state.tour;
  tourEnd();
  if (t && t.sheet && !$("#sheet").hidden) closeMatch();
  if (t && !t.greet) exitHint();
}

// Кнопка «Назад» Telegram в листе тура: в главе I — к предыдущему шагу, карточке матча на «Главной»;
// в «Ленте» — к шагу с рядом историй; в главе II — к следующей главе. Главы не отмечаются
function tourSheetBack() {
  const t = state.tour;
  const id = t.chapters[t.ci];
  closeMatch();
  if (id === 1 && t.ctx.route && t.ctx.route.card) return tourShow(STEP.card);
  if (id === FEED && packs().length) return tourShow(STEP.packs);
  tourAfter(id);
}

function markChapter(id) {
  const done = new Set(lsGet(TOUR_CH_KEY) || "");
  if (done.has(String(id))) return;
  done.add(String(id));
  const v = [...done].sort().join("");
  lsSet(TOUR_CH_KEY, v);
  if (cloud()) cloud().setItem(TOUR_CH_KEY, v, () => {});
}

// Прогресс — четыре отрезка, как в Stories: пройденные залиты, текущий — на долю шагов, будущие — контур
function progressHTML(t, ch, i, n) {
  const at = t.seg.indexOf(ch.id);
  const p = (i + 1) / n;
  const segs = t.seg.map((id, k) => (k < at ? '<i class="done"></i>' : k === at ? `<i><span style="transform:scaleX(${p.toFixed(3)})"></span></i>` : "<i></i>")).join("");
  return `<span class="tp" role="img" aria-label="Глава ${at + 1} из ${t.seg.length}, шаг ${i + 1} из ${n}"><b>${ch.short}</b>${segs}</span>`;
}
// Отрезки без ряда: вступление — все пустые, контрольная точка — пройденные залиты
function segsHTML(t, done) {
  const segs = t.seg.map((id, k) => (k < done ? '<i class="done"></i>' : "<i></i>")).join("");
  const label = done ? `Пройдено глав: ${done} из ${t.seg.length}` : `Глав: ${t.seg.length}`;
  return `<span class="tp intro" role="img" aria-label="${label}">${segs}</span>`;
}
function animateProgress(t, ch, i, n) {
  const p = (i + 1) / n;
  const bar = $("#tour .tp span");
  if (bar && t.prog && t.prog.ch === ch.id && t.prog.p !== p && !calm()) {
    bar.animate([{ transform: `scaleX(${t.prog.p})` }, { transform: `scaleX(${p})` }], { duration: 260, easing: EASE_OUT });
  }
  t.prog = { ch: ch.id, p };
}

// Нажали по затемнению: в окне на шаге «пройди» и «загляни» срабатывает сама цель, мимо окна — промах.
// По окну шага «смотри» и где угодно на шаге «листай» — мягкий промах: вздрагивает главная кнопка
function tourTap(e) {
  const t = state.tour;
  const s = stepOf(t);
  const el = aimEl();
  if (!s || !el || t.busy || !$("#tour.aim")) return;
  const r = aimRect(el);
  const inside = e.clientX >= r.left - AIM_PAD && e.clientX <= r.right + AIM_PAD && e.clientY >= r.top - AIM_PAD && e.clientY <= r.bottom + AIM_PAD;
  const kind = val(s.kind, t.ctx);
  if (kind === "swipe") return tourMiss(t, s, true);
  if (!inside) return tourMiss(t, s);
  if (kind === "look") return tourMiss(t, s, true);
  const box = $("#tour");
  const under = document.elementsFromPoint(e.clientX, e.clientY).find((n) => !box.contains(n));
  const hit = under && (s.hit ? under.closest(s.hit) : el.contains(under) ? under : null);
  if (!hit || !el.contains(hit)) return tourMiss(t, s);
  if (kind === "peek") {
    // загляни: подсказка гаснет, открывается то, что открывает цель; лист закроют — тур дальше
    t.away = true;
    t.screen = null;
    closeCoach();
    under.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, clientX: e.clientX, clientY: e.clientY }));
    return;
  }
  tourPass(t, s, under, e.clientX, e.clientY);
}

// Промах — кольцо вздрагивает, со второго раза на шаге «пройди» — и главная кнопка. soft — нажали по
// окну «смотри»: вздрагивает только кнопка, она и ведёт дальше. Упрёков нет
function tourMiss(t, s, soft = false) {
  t.misses += 1;
  if (inTelegram && tg.HapticFeedback) tg.HapticFeedback.impactOccurred("light");
  if (calm()) return;
  const jolt = [{ transform: "none" }, { transform: "scale(1.04)" }, { transform: "none" }];
  const ring = $("#tour .coach-ring");
  if (ring && !soft) ring.animate(jolt, { duration: 200, easing: EASE_OUT });
  const btn = $('#tour [data-tour="next"]');
  if (btn && (soft || (t.misses > 1 && val(s.kind, t.ctx) === "pass"))) btn.animate(jolt, { duration: 200, easing: EASE_OUT });
}

// Загляни: лист, открытый нажатием по цели, закрыли — тур идёт к следующему шагу
function tourBack() {
  const t = state.tour;
  if (!t || !t.away) return;
  t.away = false;
  setTimeout(() => tourNext(t), 240);   // лист успевает уехать
}

// Подсказка при выходе: над меню у вкладки «Я», без кнопок, нажатия проходят насквозь
let hintTimer = 0;
function exitHint() {
  const tabs = $("#tabs");
  const me = tabs && !tabs.hidden && tabs.querySelector('[data-tab="me"]');
  if (!me) return;
  clearTimeout(hintTimer);
  document.querySelectorAll(".tour-hint").forEach((n) => n.remove());
  const hint = document.createElement("div");
  hint.className = "tour-hint";
  hint.setAttribute("role", "status");
  hint.innerHTML = `<span class="coach-tail"></span>Подсказки всегда тут: «Я» → «Показать подсказки»`;
  document.body.appendChild(hint);
  const tr = tabs.getBoundingClientRect();
  const mr = me.getBoundingClientRect();
  hint.style.bottom = `${innerHeight - tr.top + 14}px`;
  const hr = hint.getBoundingClientRect();
  hint.querySelector(".coach-tail").style.left = `${Math.max(20, Math.min(mr.left + mr.width / 2 - hr.left, hr.width - 20)) - 8}px`;
  me.classList.add("hint-hl");
  if (!calm()) hint.animate([{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1, transform: "none" }], { duration: 180, easing: EASE_OUT });
  hintTimer = setTimeout(() => {
    me.classList.remove("hint-hl");
    const done = () => hint.remove();
    if (calm()) return done();
    hint.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 160, easing: EASE_IN, fill: "forwards" }).finished.then(done, done);
  }, EXIT_HINT_MS);
}

// Цель шага: ждём, пока экран соберётся и догрузятся данные. Экран въезжает, лист поднимается —
// меряем, когда цель встала: два одинаковых замера подряд
function findAim(find, fail, timeout) {
  return new Promise((done) => {
    const t0 = Date.now();
    let prev = null;
    const look = () => {
      if (fail && fail()) return done({ el: null, failed: true });
      const el = find();
      const r = el && el.getBoundingClientRect();
      if (r && r.height) {
        if (prev && Math.abs(prev.top - r.top) < 0.5 && Math.abs(prev.left - r.left) < 0.5 && Math.abs(prev.height - r.height) < 0.5) return done({ el });
        prev = r;
      } else {
        prev = null;
      }
      if (Date.now() - t0 > timeout) return done({ el: null });
      setTimeout(look, 70);
    };
    look();
  });
}

// Слой тура: сцена (карточка, окно) меняется от шага к шагу, область для экранных дикторов — одна
// на весь тур, в неё пишется реплика. Свайп и колесо по затемнению страницу не листают: прокручивает
// только сам тур (style.css → .coach). Исключение — шаг «листай»: там слой с классом scroll (ADR-016)
function coachBox() {
  let box = $("#tour");
  if (box) return [box, false];
  box = document.createElement("div");
  box.id = "tour";
  box.className = "coach";
  box.tabIndex = -1;
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-modal", "true");
  box.innerHTML = `<div class="coach-stage"></div><div class="sr-only" aria-live="polite"></div>`;
  const hold = (e) => { if (!box.classList.contains("scroll")) e.preventDefault(); };
  box.addEventListener("wheel", hold, { passive: false });
  box.addEventListener("touchmove", hold, { passive: false });
  document.body.appendChild(box);
  if (!calm()) box.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 180, easing: EASE_OUT });
  return [box, true];
}
const stageOf = (box) => box.querySelector(".coach-stage");
function say(box) {
  const p = box.querySelector(".coach-card p");
  box.querySelector("[aria-live]").textContent = p ? p.textContent : "";
}

// Между экранами тура: только затемнение, карточки нет — экран под ним меняется. Фокус — на самом
// слое: Enter не нажмёт ничего под ним
function coachDim(tab) {
  dropAim();
  clearRoom();
  const [box] = coachBox();
  box.setAttribute("aria-label", "Подсказки");
  box.classList.remove("aim", "scroll");
  stageOf(box).innerHTML = `<div class="coach-back" data-coach-back></div>`;
  lightTab(tab);
  box.focus({ preventScroll: true });
}

// Окно гаснет перед переездом к новой цели: заливка того же тона, что затемнение
function darkWindow() {
  const fill = $("#tour .coach-fill");
  const ring = $("#tour .coach-ring");
  if (!fill) return Promise.resolve();
  if (calm()) {
    fill.style.opacity = "1";
    return Promise.resolve();
  }
  const o = { duration: 120, easing: EASE_IN, fill: "forwards" };
  if (ring) ring.animate([{ opacity: 1 }, { opacity: 0 }], o);
  return fill.animate([{ opacity: 0 }, { opacity: 1 }], o).finished.catch(() => {});
}

// Карточка проводника по центру: крупный талисман сверху выходит за край. extra — под текстом
function coach(club, pose, text, buttons, label, extra = "") {
  dropAim();
  const [box, fresh] = coachBox();
  const hadCard = !!box.querySelector(".coach-card") && !box.classList.contains("aim");
  box.setAttribute("aria-label", label);
  box.classList.remove("aim", "scroll");
  const fig = pose ? guideFig(club, pose) : "";
  stageOf(box).innerHTML = `<div class="coach-back" data-coach-back></div>
    <div class="coach-card${fig ? "" : " bare"}">${fig ? `<div class="coach-guide">${fig}</div>` : ""}
      <p>${text}</p>${extra}<div class="coach-btns">${buttons}</div></div>`;
  say(box);
  lightTab("");
  if (!calm()) {
    const card = box.querySelector(".coach-card");
    if (!hadCard) card.animate([{ opacity: 0, transform: "translateY(24px) scale(.96)" }, { opacity: 1, transform: "none" }], { duration: 260, easing: EASE_OUT });
    else card.animate([{ opacity: 0.5 }, { opacity: 1 }], { duration: 160, easing: EASE_OUT });
    const g = box.querySelector(".guide");
    if (g) g.animate([{ opacity: 0, transform: "translateY(28px) scale(.6) rotate(-16deg)" }, { opacity: 1, transform: "none" }],
      { duration: 380, delay: fresh ? 90 : 0, easing: "cubic-bezier(.2, 1.6, .4, 1)", fill: "backwards" });
  }
  const first = box.querySelector(".coach-card button");
  if (first) first.focus({ preventScroll: true });
}

// Карточка у цели: цель вырезана из затемнения окном с кольцом, карточка компактная и рядом.
// same — на том же экране: карточка переезжает с прежнего места, талисман перебегает на сторону цели
function coachAt(el, o) {
  const [box] = coachBox();
  const old = o.same && box.classList.contains("aim") ? box.querySelector(".coach-card") : null;
  const from = old && old.getBoundingClientRect();
  const oldFig = old && old.querySelector(".guide");
  const figFrom = oldFig && oldFig.getBoundingClientRect();
  dropAim();
  box.setAttribute("aria-label", "Подсказки");
  box.classList.add("aim");
  box.classList.toggle("scroll", !!o.scroll);
  const fig = o.pose ? guideFig(state.fav, o.cheer ? "cheer" : o.pose) : "";
  Object.assign(aim, { extend: o.extend || null, only: !!o.only });
  const r = aimRect(el);
  const right = r.left + r.width / 2 >= innerWidth / 2 - 1;
  stageOf(box).innerHTML = `<div class="coach-back" data-coach-back></div>
    <div class="coach-hole"><i class="coach-fill"></i><i class="coach-ring"></i></div>${GESTURE[o.gesture] || ""}
    <div class="coach-card${right ? " right" : ""}${fig ? "" : " bare"}"><span class="coach-tail"></span>
      <div class="coach-say">${fig}<p>${o.text}</p></div><div class="coach-btns">${o.buttons}</div></div>`;
  say(box);
  aimAt(box, el, o);
  lightTab(o.tab, true);
  const card = box.querySelector(".coach-card");
  const fill = box.querySelector(".coach-fill");
  const g = card.querySelector(".guide");
  if (calm()) {
    fill.style.opacity = "0";
  } else {
    // окно проявляется у новой цели
    fill.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 160, delay: from ? 60 : 0, easing: EASE_OUT, fill: "backwards" });
    box.querySelector(".coach-ring").animate([{ opacity: 0 }, { opacity: 1 }], { duration: 160, delay: from ? 60 : 0, easing: EASE_OUT, fill: "backwards" });
    const gest = box.querySelector(".gest");
    if (gest) gest.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 200, delay: 220, easing: EASE_OUT, fill: "backwards" });
    const to = card.getBoundingClientRect();
    if (from) {
      card.animate([{ transform: `translate(${from.left - to.left}px, ${from.top - to.top}px)` }, { transform: "none" }], { duration: 260, easing: EASE_OUT });
      card.querySelector("p").animate([{ opacity: 0.3 }, { opacity: 1 }], { duration: 160, easing: EASE_OUT });
      if (g && figFrom) {
        const now = g.getBoundingClientRect();
        const dx = (figFrom.left - from.left) - (now.left - to.left);
        if (Math.abs(dx) > 1) g.animate([{ transform: `translateX(${dx}px)` }, { transform: "none" }], { duration: 260, easing: EASE_OUT });
      }
    } else {
      const dy = aim.side === "above" ? -16 : 24;
      card.animate([{ opacity: 0, transform: `translateY(${dy}px) scale(.96)` }, { opacity: 1, transform: "none" }], { duration: 260, easing: EASE_OUT });
      if (g && !o.cheer) g.animate([{ opacity: 0, transform: "translateY(20px) scale(.6) rotate(-16deg)" }, { opacity: 1, transform: "none" }],
        { duration: 380, delay: 60, easing: "cubic-bezier(.2, 1.6, .4, 1)", fill: "backwards" });
    }
  }
  // сам нажал на цель — талисман подпрыгивает и радуется, потом снова показывает
  if (g && o.cheer) {
    delete g.dataset.busy;
    guideHop(g, o.pose);
  }
  const first = box.querySelector('[data-tour="next"]');
  if (first) first.focus({ preventScroll: true });
}

// Окно в затемнении над целью и карточка рядом. Сторону выбираем один раз; при прокрутке, смене
// размера окна Telegram и перерисовке цели (свежие данные, выбранный гол) только догоняем её.
// extend — наклейки у края цели; only — окно только по этим частям цели; radius — скругление окна
const aim = { el: null, find: null, extend: null, only: false, radius: null, side: "", off: null, last: "" };

function aimAt(box, el, o) {
  dropAim();
  Object.assign(aim, { el, find: o.find || null, extend: o.extend || null, only: !!o.only, radius: o.radius == null ? null : o.radius, side: "", last: "" });
  placeAim(box);
  let queued = false;
  const follow = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; if (aim.el && box.isConnected) placeAim(box); });
  };
  // цель могла сдвинуться без прокрутки: догрузился блок выше, экран перерисовался
  const watch = setInterval(() => {
    const cur = aimEl();
    if (!cur || !box.isConnected) return;
    const r = aimRect(cur);
    if (`${r.left}|${r.top}|${r.width}|${r.height}` !== aim.last) follow();
  }, 200);
  document.addEventListener("scroll", follow, { passive: true, capture: true });
  addEventListener("resize", follow);
  aim.off = () => {
    clearInterval(watch);
    document.removeEventListener("scroll", follow, { capture: true });
    removeEventListener("resize", follow);
  };
}

function dropAim() {
  if (aim.off) aim.off();
  if (swipeOff) swipeOff();
  aim.el = aim.off = aim.find = null;
  aim.side = "";
}

// Цель перерисовали — ищем её заново тем же поиском, шаг не сбивается
function aimEl() {
  if (aim.el && !aim.el.isConnected && aim.find) aim.el = aim.find() || aim.el;
  return aim.el;
}

// Прямоугольник цели вместе с наклейками, которые выходят за её край (талисман на паспорте).
// extend — селектор частей внутри цели или функция, которая их отдаёт (табло рядом с сюжетом);
// only — окно только по этим частям (табло и сюжет, график и выбранный гол без легенды)
function aimRect(el) {
  const r = el.getBoundingClientRect();
  let { left, top, right, bottom } = r;
  if (aim.extend && aim.only) [left, top, right, bottom] = [Infinity, Infinity, -Infinity, -Infinity];
  if (aim.extend) {
    const parts = typeof aim.extend === "function" ? aim.extend(el) : el.querySelectorAll(aim.extend);
    parts.forEach((x) => {
      const q = x && x.getBoundingClientRect();
      if (!q || !q.height) return;
      left = Math.min(left, q.left);
      top = Math.min(top, q.top);
      right = Math.max(right, q.right);
      bottom = Math.max(bottom, q.bottom);
    });
  }
  if (left === Infinity) ({ left, top, right, bottom } = r);
  return { left, top, right, bottom, width: right - left, height: bottom - top };
}

// Запас снизу: цель у самого низа страницы или листа не прокрутить наверх — на время шага экран
// становится длиннее. Снимается при смене экрана и при выходе из тура
const room = { el: null };
function growRoom(el, px) {
  if (!el) return;
  if (room.el && room.el !== el) clearRoom();
  room.el = el;
  el.style.paddingBottom = `${(parseFloat(getComputedStyle(el).paddingBottom) || 0) + Math.ceil(px) + 1}px`;
}
function clearRoom() {
  if (room.el) room.el.style.paddingBottom = "";
  room.el = null;
}

// Где цель видно. Страница — между бегущей строкой (и прилипшими фильтрами) и меню. Лист — от его
// верха, а для целей ниже сегментов разбора — от низа прилипших сегментов; до края экрана: меню под листом
function aimBounds(el) {
  const marquee = $(".marquee");
  const top0 = (marquee ? Math.max(0, marquee.getBoundingClientRect().bottom) : 0) + 12;
  const sheet = el.closest("#sheet");
  let b;
  if (sheet) {
    const sr = sheet.getBoundingClientRect();
    b = { top: Math.max(top0, sr.top + 12), bottom: innerHeight - 12,
      by: (dy) => {
        const max = sheet.scrollHeight - sheet.clientHeight - sheet.scrollTop;
        if (dy > max) growRoom(sheet.querySelector(".sheet-page"), dy - max);
        sheet.scrollTop += dy;
      } };
    const seg = sheet.querySelector(".recap-seg");
    if (seg && !seg.contains(el) && seg.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) b.top = Math.max(b.top, sr.top + seg.offsetHeight + 12);
  } else {
    const tabs = $("#tabs");
    b = { top: top0, bottom: (tabs && !tabs.hidden ? tabs.getBoundingClientRect().top : innerHeight) - 12,
      by: (dy) => {
        const max = document.documentElement.scrollHeight - innerHeight - window.scrollY;
        if (dy > max) growRoom($("#screen"), dy - max);
        window.scrollBy(0, dy);
      } };
    const bar = $("#screen .filterbar");
    if (bar && !bar.contains(el)) b.top = Math.max(b.top, bar.getBoundingClientRect().bottom + 12);
  }
  // прилипшее само не уедет: окно встаёт там, где оно есть
  if (el.closest(".filterbar, .recap-seg")) b.top = Math.min(b.top, el.getBoundingClientRect().top - AIM_PAD);
  return b;
}

function placeAim(box) {
  const el = aimEl();
  const hole = box.querySelector(".coach-hole");
  const card = box.querySelector(".coach-card");
  const tail = box.querySelector(".coach-tail");
  if (!el || !el.isConnected || !hole || !card) return;
  const b = aimBounds(el);
  const h = card.offsetHeight;
  let r = aimRect(el);
  const fits = (q, side) => (side === "below"
    ? q.top - AIM_PAD >= b.top && q.bottom + AIM_PAD + AIM_GAP + h <= b.bottom
    : q.top - AIM_PAD - AIM_GAP - h >= b.top && q.bottom + AIM_PAD <= b.bottom);
  if (!aim.side) {
    aim.side = fits(r, "below") ? "below" : fits(r, "above") ? "above" : "";
    if (!aim.side) {
      // не влезает ни под целью, ни над ней: цель — наверх видимой области, карточка — под ней.
      // Запас сверху — под наклейки у края цели, если есть место
      const spare = b.bottom - b.top - (r.height + 2 * AIM_PAD + AIM_GAP + h);
      b.by(r.top - AIM_PAD - b.top - Math.max(0, Math.min(20, spare)));
      r = aimRect(el);
      aim.side = fits(r, "above") && !fits(r, "below") ? "above" : "below";
    }
  }
  aim.last = `${r.left}|${r.top}|${r.width}|${r.height}`;
  const radius = (aim.radius != null ? aim.radius : parseFloat(getComputedStyle(el).borderTopLeftRadius) || 0) + AIM_PAD;
  // окно не выходит за края экрана: кольцо видно целиком и у целей во всю ширину
  const left = Math.max(4, r.left - AIM_PAD);
  const right = Math.min(innerWidth - 4, r.right + AIM_PAD);
  Object.assign(hole.style, { left: `${left}px`, top: `${r.top - AIM_PAD}px`,
    width: `${right - left}px`, height: `${r.height + 2 * AIM_PAD}px`, borderRadius: `${radius}px` });
  // высокая цель на коротком экране: карточка закрывает её низ, но не заходит под меню
  const y = aim.side === "below" ? r.bottom + AIM_PAD + AIM_GAP : r.top - AIM_PAD - AIM_GAP - h;
  const cy = Math.max(b.top, Math.min(y, b.bottom - h));
  card.style.top = `${cy}px`;
  placeGesture(box, r, b, cy, h);
  card.classList.toggle("up", aim.side === "above");   // карточка над целью — хвостик снизу
  const c = card.getBoundingClientRect();
  tail.style.left = `${Math.max(28, Math.min(r.left + r.width / 2 - c.left, c.width - 28)) - 8}px`;
}

// Жест в окне (ADR-016): в историях — метки над зонами касания (30% слева и 70% справа, как .pack-zone),
// на ленте — точка ведёт след вверх. Нажатия проходят сквозь него
const GESTURE = {
  tap: `<div class="gest gest-tap" aria-hidden="true"><span class="gest-l"><i class="gest-dot ghost"></i><b>‹ Назад</b></span>
    <span class="gest-r"><i class="gest-dot"></i><b>Дальше ›</b></span></div>`,
  swipe: '<div class="gest gest-swipe" aria-hidden="true"><i class="gest-trail"></i><i class="gest-dot"></i></div>',
};

// Жест — в видимой части окна: от верха окна (или области) до карточки; карточка над целью — от неё до низа
function placeGesture(box, r, b, cy, h) {
  const g = box.querySelector(".gest");
  if (!g) return;
  let top = Math.max(r.top, b.top);
  let bottom = Math.min(r.bottom, b.bottom);
  if (aim.side === "above") top = Math.max(top, cy + h + AIM_GAP);
  else bottom = Math.min(bottom, cy - AIM_GAP);
  const height = Math.max(0, bottom - top);
  Object.assign(g.style, { left: `${r.left}px`, width: `${r.width}px`, top: `${top}px`, height: `${height}px` });
  g.style.setProperty("--travel", `${Math.round(Math.min(140, height * 0.45))}px`);
  g.classList.toggle("tight", height < 72);   // жесту негде встать — его нет, реплика и так говорит
}

// «Листай»: долистал сам на треть экрана — получилось. Отсчёт — с первого касания, колеса или клавиши:
// прокрутку, которой тур поставил окно на место, не считаем. Начал листать — жест гаснет; прокрутка по
// инерции уляжется — следующая карточка, с прыжком
const SWIPE_SHARE = 0.3;
const SWIPE_SETTLE_MS = 180;   // тишина после последней прокрутки
const SWIPE_MAX_MS = 900;      // дольше инерцию не ждём
let swipeOff = null;
function watchSwipe(t, s) {
  const box = $("#tour");
  if (!box) return;
  if (swipeOff) swipeOff();
  let base = null, need = 0, won = false, fired = false, quiet = 0, t0 = 0;
  const arm = () => {
    if (base != null) return;
    base = window.scrollY;
    const room = document.documentElement.scrollHeight - innerHeight - base;
    need = Math.max(24, Math.min(innerHeight * SWIPE_SHARE, room - 4));
    const g = box.querySelector(".gest");
    if (!g) return;
    if (calm()) return g.classList.add("off");
    g.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 160, easing: EASE_IN, fill: "forwards" }).finished
      .then(() => g.classList.add("off"), () => {});
  };
  const key = (e) => { if (["ArrowDown", "PageDown", "End"].includes(e.key)) arm(); };
  const done = () => {
    if (fired) return;
    fired = true;
    off();
    if (state.tour === t && t.key === s.key) {
      t.busy = false;
      tourNext(t);
    }
  };
  const scroll = () => {
    if (state.tour !== t || t.key !== s.key) return off();
    if (!won) {
      if (base == null || t.busy || window.scrollY - base < need) return;
      won = true;
      t.busy = true;
      t.cheer = true;   // сам долистал — следующая карточка радуется
      t0 = Date.now();
    }
    clearTimeout(quiet);
    if (Date.now() - t0 > SWIPE_MAX_MS) return done();
    quiet = setTimeout(done, SWIPE_SETTLE_MS);
  };
  const off = () => {
    clearTimeout(quiet);
    box.removeEventListener("touchstart", arm);
    box.removeEventListener("wheel", arm);
    document.removeEventListener("keydown", key, true);
    removeEventListener("scroll", scroll);
    if (swipeOff === off) swipeOff = null;
  };
  box.addEventListener("touchstart", arm, { passive: true });
  box.addEventListener("wheel", arm, { passive: true });
  document.addEventListener("keydown", key, true);
  addEventListener("scroll", scroll, { passive: true });
  swipeOff = off;
}

// Вкладка меню, о которой речь, — над затемнением с кольцом. aimed — пульсирует цель, у вкладки кольцо без пульса.
// Меню в туре не нажимается: нажатие по нему — промах (style.css → .tabs.coach-on)
function lightTab(tab, aimed = false) {
  document.querySelectorAll("#tabs .coach-hl").forEach((b) => b.classList.remove("coach-hl"));
  const tabs = $("#tabs");
  if (tabs) {
    tabs.classList.toggle("coach-on", !!tab);
    tabs.classList.toggle("coach-aim", !!tab && aimed);
  }
  const btn = tab && document.querySelector(`#tabs [data-tab="${tab}"]`);
  if (btn) btn.classList.add("coach-hl");
}

function closeCoach() {
  state.meet = null;
  dropAim();
  clearRoom();
  lightTab("");
  const box = $("#tour");
  if (!box) return;
  box.removeAttribute("id");   // новая карточка может открыться, пока эта гаснет
  if (calm()) return box.remove();
  box.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 160, easing: EASE_IN }).finished
    .then(() => box.remove()).catch(() => box.remove());
}

// Лист «Подсказки»: всё сначала или одна глава; пройденные — с галочкой. Названия глав — полные
const CHECK = '<svg class="ok" viewBox="0 0 24 24" role="img" aria-label="Пройдена"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>';
function openHints() {
  const done = lsGet(TOUR_CH_KEY) || "";
  const rows = TOUR.map((ch) => `<button type="button" class="menu-row hint-row" data-tour-ch="${ch.id}">${ICON_CH[ch.id]}<span><b>${ch.title}</b><small>${ch.hint}</small></span>${done.includes(String(ch.id)) ? CHECK : "<i></i>"}${ICON_ME.chev}</button>`).join("");
  showSheet(`<div class="grab"></div>
    <div class="sheet-head"><span class="when">Подсказки</span>
    <button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    <button type="button" class="btn hints-all" data-tour-all>Всё сначала</button>
    <div class="menu hints">${rows}</div>`);
}

// Из листа «Подсказки»: лист уезжает, потом тур — все главы без вступления (с контрольной точкой и
// финалом) или одна глава
function replayTour(chapters) {
  closeMatch();
  setTimeout(() => {
    if (state.tour || !$("#sheet").hidden) return;
    state.tour = newTour(chapters ? { chapters } : { save: true });
    tourChapter(0);
  }, calm() ? 0 : 260);
}

// ---------- экран «Я» (ADR-004) ----------

function tgUser() {
  return (inTelegram && tg.initDataUnsafe && tg.initDataUnsafe.user) || null;
}
// Ссылка, которая открывает мини-апп сразу с этим клубом
function inviteLink(id) {
  const app = state.data.links && state.data.links.app;
  if (app) return `${app}?startapp=${encodeURIComponent(id)}`;
  return `${location.origin}${location.pathname}?team=${encodeURIComponent(id)}`;
}
function canStory() {
  return inTelegram && typeof tg.shareToStory === "function" && tg.isVersionAtLeast("7.8");
}

const STAR = `<svg class="pp-star" viewBox="0 0 100 100" aria-hidden="true"><path d="m50 6 12.5 27 29.5 3.5-22 20 6 29.5L50 71 23.5 86l6-29.5-22-20L37 33z"/></svg>`;
const ICON_ME = {
  story: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5" stroke-dasharray="3.2 2.2"/><path d="M12 8v8M8 12h8"/></svg>',
  invite: '<svg viewBox="0 0 24 24"><path d="M20 4 3 11l6.5 2.5L12 20z"/><path d="m9.5 13.5 4-4"/></svg>',
  bell: '<svg viewBox="0 0 24 24"><path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/></svg>',
  swap: '<svg viewBox="0 0 24 24"><path d="M4 8h14M14 4l4 4-4 4M20 16H6M10 12l-4 4 4 4"/></svg>',
  chev: '<svg class="chev" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></svg>',
  help: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.5a2.5 2.5 0 1 1 3.4 2.3c-.7.3-1 .9-1 1.6v.4M12 16.8v.2"/></svg>',
  hide: '<svg viewBox="0 0 24 24"><path d="M3 12s3.5-6 9-6 9 6 9 6-3.5 6-9 6-9-6-9-6Z"/><circle cx="12" cy="12" r="2.6"/><path d="M4 20 20 4"/></svg>',
  posts: '<svg viewBox="0 0 24 24"><path d="M20 4 3 11l6.5 2.5L12 20z"/><path d="M6 17.5h4M6 20.5h8"/></svg>',
};

function passport(me) {
  const t = team(me);
  const u = tgUser();
  const who = u && u.first_name ? `${esc(u.first_name)} · ` : "";
  const stats = seasonStats(me);
  return `<article class="passport" aria-label="Паспорт болельщика">
    <div class="pp-top"><span class="pp-tag">Паспорт болельщика</span>${guideOf(me) ? `<span class="pp-guide">${guideFig(me, "hello")}</span>` : STAR}</div>
    <div class="pp-main">${emblem(me, "xl")}
      <div class="pp-name"><small>Болею за</small><b style="--w:${longestChunk(t.name)}">${esc(t.name)}</b></div>
    </div>
    <div class="pp-meta">${who}${esc(state.data.league.split(" — ")[0])} · сезон ${esc(state.data.season)}</div>
    ${stats ? `<div class="pp-stats">${stats}</div>` : ""}
  </article>`;
}

function renderMe() {
  const me = state.fav;
  const t = team(me);
  const u = tgUser();
  const title = u && u.first_name ? u.first_name : "Профиль";
  const bot = state.data.links && state.data.links.bot;
  let html = `<section class="band concrete"><h1 style="--w:${longestChunk(title)}" class="fit">${esc(title)}</h1>
    <div class="lede">Болеет за ${esc(t.name)}. Покажите это друзьям.</div></section>`;
  html += passport(me);
  html += `<div class="me-actions">
    <button type="button" class="btn" data-story>${ICON_ME.story}${canStory() ? "Выложить в историю" : "Поделиться карточкой"}</button>
    <button type="button" class="btn ghost" data-invite>${ICON_ME.invite}Позвать болеть вместе</button>
  </div>`;
  // игроки своей команды в лидерах лиги (ADR-010): данные лидеров грузятся один раз, как на «Игроках»
  html += `<div id="me-leads">${state.leaders ? mineBlock(state.leaders, true) : ""}</div>`;
  if (!state.leaders) loadLeaders().then(() => {
    const box = $("#me-leads");
    if (box && state.leaders && state.tab === "me") { box.innerHTML = mineBlock(state.leaders, true); fadeIn(box); }
  });
  html += `<div class="label">Настройки</div><div class="menu">`;
  if (bot) {
    html += me === REMIND_TEAM
      ? `<button type="button" class="menu-row" data-remind>${ICON_ME.bell}<span><b>Напоминания о матчах</b><small>Накануне и в день игры — в боте</small></span>${ICON_ME.chev}</button>`
      : `<div class="menu-row off">${ICON_ME.bell}<span><b>Напоминания о матчах</b><small>Пока только о «Рязань-ВДВ». Скоро — о любой команде</small></span></div>`;
  }
  html += `<button type="button" class="menu-row" data-switch-open>${ICON_ME.swap}<span><b>Сменить команду</b><small>Сейчас: ${esc(t.name)}</small></span>${ICON_ME.chev}</button>
  <button type="button" class="menu-row" data-feed-rules>${ICON_ME.posts}<span><b>Посты каналов</b><small>${Object.keys(feedHidden()).length ? `Скрыто: ${Object.keys(feedHidden()).length}. ` : ""}Как мы их выбираем</small></span>${ICON_ME.chev}</button>
  <button type="button" class="menu-row" data-tour-restart>${ICON_ME.help}<span><b>Показать подсказки</b><small>${guideOf(me) ? `${esc(guideOf(me).name)} покажет всё или одну главу` : "Покажем всё или одну главу"}</small></span>${ICON_ME.chev}</button>
  </div>`;
  html += themePills();
  return html + footer();
}

// Лист со всеми клубами: одно касание — новая любимая команда
function openTeamSheet() {
  const html = `<div class="grab"></div>
    <div class="sheet-head"><span class="when">Сменить команду</span>
    <button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    ${teamGrid(state.fav, "data-switch")}`;
  showSheet(html);
}

// Тот же лист из Календаря: выбор показывает календарь клуба, любимая команда не меняется
function openCalSheet() {
  const html = `<div class="grab"></div>
    <div class="sheet-head"><span class="when">Календарь команды</span>
    <button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    ${teamGrid(state.cal.team, "data-cal-pick")}`;
  showSheet(html);
}

function shareStory() {
  const id = state.fav;
  const t = team(id);
  const link = inviteLink(id);
  const text = `Болею за ${t.name}! Матчи и таблица РХЛ: ${link}`;
  if (canStory()) {
    tg.shareToStory(new URL(`stories/${id}.jpg`, location.href).href, { text: text.slice(0, 200) });
    return;
  }
  shareLink(link, `Болею за ${t.name}! Матчи и таблица РХЛ`);
}

function shareLink(link, text) {
  const url = `https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent(text)}`;
  if (inTelegram) return tg.openTelegramLink(url);
  if (navigator.share) return navigator.share({ title: "РХЛ", text, url: link }).catch(() => {});
  window.open(url, "_blank", "noopener");
}

// ---------- очные встречи (ADR-006) ----------

let h2hLoading = null;
function loadH2H() {
  if (!h2hLoading) {
    h2hLoading = fetch("data/h2h.json")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.status))))
      .then((d) => (state.h2h = d))
      .catch(() => { h2hLoading = null; return null; });
  }
  return h2hLoading;
}
const pairKey = (a, b) => [a, b].sort().join("|");
const shortDate = (iso) => { const d = parseISO(iso); return `${d.getUTCDate()} ${MON_SHORT[d.getUTCMonth()]} ${d.getUTCFullYear()}`; };

// Вывод одной строкой. Прогноз не даём: только «по истории встреч»
function h2hVerdict(h, a, b) {
  const wa = h.wins[a], wb = h.wins[b];
  if (h.games < 3) return `Встречались ${h.games} ${plural(h.games, "раз", "раза", "раз")} — по истории фаворита не назвать`;
  if (wa === wb) return `История равная: по ${wa} ${plural(wa, "победе", "победы", "побед")}`;
  const [fav, w] = wa > wb ? [a, wa] : [b, wb];
  return `По истории встреч сильнее ${team(fav).name}: ${w} ${plural(w, "победа", "победы", "побед")} из ${h.games}`;
}

// Место под очные встречи, пока они грузятся: та же высота, что у карточки и пяти встреч
function h2hSkeleton() {
  return `<div class="sk sk-label"></div><div class="sk" style="height:152px"></div><div class="sk sk-label"></div><div class="sk" style="height:${5 * 75}px"></div>`;
}
function failBlock(title, what) {
  const fig = guideFig(state.fav, "shrug");
  return `<div class="label">${title}</div><div class="empty${fig ? " guide-empty" : ""}">${fig}<div>Не удалось загрузить. Проверьте интернет.<br><button type="button" class="retry" data-retry="${what}">Повторить</button></div></div>`;
}
function fadeIn(el) {
  if (el && !calm()) el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 150, easing: "ease-out" });
}
function fillH2H(g) {
  loadH2H().then(() => {
    const box = $("#h2h");
    if (!box || $("#sheet").hidden || box.dataset.pair !== pairKey(g.home, g.away)) return;
    box.innerHTML = state.h2h ? h2hBlock(g) : failBlock("Очные встречи", "h2h");
    fadeIn(box);
  });
}

function h2hBlock(g) {
  const h = state.h2h && state.h2h[pairKey(g.home, g.away)];
  const label = (aside) => `<div class="label">Очные встречи${aside ? `<span class="aside">${aside}</span>` : ""}</div>`;
  if (!h || !h.games) return `${label("")}<div class="empty">В НМХЛ и РХЛ раньше не встречались</div>`;
  const a = g.home, b = g.away;
  const share = Math.round((100 * h.wins[a]) / h.games);
  let html = label(`${h.games} ${plural(h.games, "матч", "матча", "матчей")} с ${esc(h.since)} года`);
  html += `<div class="h2h">
    <div class="h2h-row"><span class="h2h-em">${emblem(a)}</span><b class="num">${h.wins[a]}</b><span>победы</span><b class="num">${h.wins[b]}</b><span class="h2h-em">${emblem(b)}</span></div>
    <div class="h2h-bar" role="img" aria-label="Победы: ${esc(team(a).name)} ${h.wins[a]}, ${esc(team(b).name)} ${h.wins[b]}"><i style="width:${share}%"></i></div>
    <div class="h2h-row goals-row"><span></span><b class="num">${h.goals[a]}</b><span>шайбы</span><b class="num">${h.goals[b]}</b><span></span></div>
    <div class="h2h-verdict">${esc(h2hVerdict(h, a, b))}</div>
  </div>`;
  const tappable = h.last.some((m) => m.id);
  html += `<div class="label">Последние встречи${tappable ? `<span class="aside">нажми — будет разбор</span>` : ""}</div><div class="list">${h.last.map((m) => {
    const lead = (id) => (id === m.home ? m.score[0] > m.score[1] : m.score[1] > m.score[0]);
    const line = (id, goals) => `<div>${emblem(id)}<span class="nm${lead(id) ? " me" : ""}">${esc(team(id).name)}</span><span class="gl${lead(id) ? " lead" : ""}">${goals}</span></div>`;
    const dec = m.decision ? `<span class="res l">${esc(m.decision)}</span>` : "";
    // встреча с разбором (ADR-008) открывается, как матч календаря
    const attrs = m.id ? ` role="button" tabindex="0" data-game="${esc(m.id)}" aria-label="${esc(`Разбор матча ${team(m.home).name} — ${team(m.away).name}, ${shortDate(m.date)}`)}"` : "";
    return `<div class="row two${m.id ? "" : " static"}"${attrs}><div class="t">${line(m.home, m.score[0])}${line(m.away, m.score[1])}</div><div class="r">${dec}<span class="kick when">${esc(shortDate(m.date))}</span></div></div>`;
  }).join("")}</div>`;
  return html;
}

// ---------- карточка матча ----------

let sheetOpener = null;   // куда вернуть фокус после закрытия карточки

// ---------- разбор сыгранного матча (ADR-008) ----------

const recaps = {};                 // id матча → data/matches/<id>.json, грузится при открытии карточки
const recapLoading = {};
const recapMissing = new Set();      // у матча нет файла разбора (протокола нет) — это не ошибка
const recapView = { id: null, tab: "goals", pens: false, side: "home", pick: null, drawn: null };

// null — разбора нет, false — не загрузился (сеть), можно повторить
function loadRecap(id) {
  if (!recapLoading[id]) {
    recapLoading[id] = fetch(`data/matches/${encodeURIComponent(id)}.json`)
      .then((r) => (r.ok ? r.json() : r.status === 404 ? null : Promise.reject(new Error(r.status))))
      .then((d) => {
        if (d) recaps[id] = d;
        else recapMissing.add(id);
        return d;
      })
      .catch(() => { delete recapLoading[id]; return false; });
  }
  return recapLoading[id];
}

// Место под разбор, пока он грузится: сюжет, график, вкладки и голы
function recapSkeleton(g) {
  const n = Math.max(1, (g.goals || []).length);
  return `<div class="sk" style="height:76px;margin-top:12px"></div><div class="sk sk-label"></div><div class="sk" style="height:300px"></div>
    <div class="sk" style="height:40px;margin-top:24px;border-radius:9999px"></div><div class="sk" style="height:${n * 52 + 40}px;margin-top:14px"></div>`;
}
function fillRecap(id) {
  loadRecap(id).then((d) => {
    const box = $("#recap");
    if (recapView.id !== id || !box || $("#sheet").hidden) return;
    if (d === false) {
      box.innerHTML = failBlock("Разбор матча", "recap");
      return fadeIn(box);
    }
    if (d && d.gw != null) recapView.pick = d.gw;
    rerenderRecap();
    fadeIn(box);
  });
}

// Матч этого сезона — из league.json, прошлого — целиком из своего файла разбора
const findGame = (id) => games().find((x) => x.id === id) || (recaps[id] && recaps[id].game) || null;
const sheetStack = [];   // из прошлой встречи «Назад» ведёт в матч, откуда её открыли
const STAGE = { regular: "регулярный чемпионат", playoff: "плей-офф" };

const secs = (t) => { const [m, s] = String(t).split(":").map(Number); return m * 60 + (s || 0); };
const sideTeam = (g, side) => (side === "away" ? g.away : g.home);

function strengthTag(x, i, d) {
  if (x.period === "РБ") return `<span class="tag soft">победный буллит</span>`;
  const st = x.strength && x.strength !== "рав." ? `<span class="tag">${esc(x.strength.replace(".", ""))}</span>` : "";
  const gw = d && d.gw === i ? `<span class="tag soft">победная</span>` : "";
  return st + gw;
}

// Ход матча: разница в счёте по минутам. Мятное — ведут хозяева, лавандовое — гости (DESIGN.md)
function flowChart(g, d) {
  const goals = (g.goals || []).map((x, i) => ({ ...x, i })).filter((x) => x.period !== "РБ");
  if (!goals.length) return "";
  const length = (d && d.length) || (g.score.decision ? 65 : 60);
  let run = 0;
  const leads = goals.map((x) => (run += x.team === "home" ? 1 : -1));
  const up = Math.max(2, ...leads), dn = Math.max(2, ...leads.map((v) => -v));
  const W = 340, x0 = 30, x1 = 334, u = 11, yc = 14 + up * u;
  const X = (sec) => x0 + ((x1 - x0) * Math.min(sec, length * 60)) / (length * 60);
  const Y = (v) => yc - v * u;
  const bot = Y(-dn) + 4;
  const lane = { home: bot + 20, away: bot + 34 };
  const hAbbr = esc(team(g.home).abbr), aAbbr = esc(team(g.away).abbr);

  const pts = [[X(0), yc]];
  let diff = 0;
  const dots = goals.map((x) => {
    const px = X(secs(x.time));
    pts.push([px, Y(diff)]);
    diff += x.team === "home" ? 1 : -1;
    pts.push([px, Y(diff)]);
    return { x: px, y: Y(diff), g: x };
  });
  pts.push([X(length * 60), Y(diff)]);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
  const area = `${line} L${X(length * 60).toFixed(1)} ${yc} Z`;

  // Периоды — колонки: чётные подложены тоном, границы сплошные, подпись внизу колонки
  const periods = [["1-й период", 0, 1200], ["2-й период", 1200, 2400], ["3-й период", 2400, 3600]];
  if (length > 60) periods.push(["ОТ", 3600, length * 60]);
  const top = Y(up) - 10, bottom = lane.away + 30;
  const bands = periods.map(([, a, b], i) => (i % 2
    ? `<rect x="${X(a).toFixed(1)}" y="${top}" width="${(X(b) - X(a)).toFixed(1)}" height="${bottom - top}" class="fl-band"/>` : "")).join("");
  const grid = periods.slice(1).map(([, a]) => `<line x1="${X(a)}" x2="${X(a)}" y1="${top}" y2="${bottom}" class="fl-grid"/>`).join("");
  const plabels = periods.map(([l, a, b]) => `<text class="fl-per" x="${((X(a) + X(b)) / 2).toFixed(1)}" y="${lane.away + 23}" text-anchor="middle">${X(b) - X(a) < 60 ? l.replace(" период", "") : l}</text>`).join("");

  // удаление на 2/4/5 минут — полоса до конца штрафа или до гола в большинстве; остальные — точка
  const pens = ((d && d.penalties) || []).map((p) => {
    const s0 = secs(p.time);
    let s1 = [2, 4, 5].includes(p.min) ? s0 + p.min * 60 : s0;
    if (p.min === 2) {
      const pp = goals.find((x) => x.team !== p.team && x.strength.startsWith("бол") && secs(x.time) > s0 && secs(x.time) < s1);
      if (pp) s1 = secs(pp.time);
    }
    const w = Math.max(5, X(s1) - X(s0));
    return `<rect x="${X(s0).toFixed(1)}" y="${lane[p.team] - 3}" width="${w.toFixed(1)}" height="6" rx="3" class="fl-pen"><title>${esc(p.time)} · ${esc(p.who)}, ${esc(p.min)} мин</title></rect>`;
  }).join("");

  const pick = recapView.pick;
  // Первый показ матча — график рисуется слева направо, точки выскакивают, когда до них дошла линия
  const draw = recapView.drawn !== g.id;
  recapView.drawn = g.id;
  const dotEls = dots.map((o) => {
    const pp = o.g.strength && o.g.strength.startsWith("бол");
    const delay = draw ? ` style="--d:${Math.round((600 * (o.x - x0)) / (x1 - x0))}ms"` : "";
    return `<g class="fl-dot${pp ? " pp" : ""}${o.g.i === pick ? " on" : ""}" data-recap-goal="${o.g.i}"${delay} role="button" tabindex="0" aria-label="${esc(`Гол ${o.g.time}, ${o.g.author}, счёт ${o.g.score}`)}"><circle cx="${o.x.toFixed(1)}" cy="${o.y}" r="14" class="hit"/><circle cx="${o.x.toFixed(1)}" cy="${o.y}" r="${o.g.i === pick ? 6 : 4.5}" class="v"/></g>`;
  }).join("");

  const cur = g.goals[pick] || goals[goals.length - 1];
  const hasPens = d && d.penalties && d.penalties.length;
  return `<div class="label">Ход матча<span class="aside">разница в счёте</span></div>
  <div class="flow${draw ? " draw" : ""}">
    <div class="fl-plot">${draw ? `<i class="fl-cover" style="left:${((100 * x0) / W).toFixed(2)}%"></i>` : ""}<svg viewBox="0 0 ${W} ${lane.away + 30}" role="img" aria-label="Разница в счёте по ходу матча">
      <defs><clipPath id="fl-up"><rect x="0" y="0" width="${W}" height="${yc}"/></clipPath>
        <clipPath id="fl-dn"><rect x="0" y="${yc}" width="${W}" height="${W}"/></clipPath></defs>
      ${bands}${grid}
      <line x1="${x0}" x2="${x1}" y1="${yc}" y2="${yc}" class="fl-axis"/>
      <path d="${area}" class="fl-home" clip-path="url(#fl-up)"/>
      <path d="${area}" class="fl-away" clip-path="url(#fl-dn)"/>
      <path d="${line}" class="fl-line"/>
      <text class="fl-ax" x="${x0 - 6}" y="${Y(up) + 3}" text-anchor="end">+${up}</text>
      <text class="fl-ax" x="${x0 - 6}" y="${yc + 3}" text-anchor="end">0</text>
      <text class="fl-ax" x="${x0 - 6}" y="${Y(-dn) + 3}" text-anchor="end">−${dn}</text>
      <text class="fl-ax" x="${x0 + 4}" y="${Y(up) + 3}">ВЕДЁТ ${hAbbr}</text>
      <text class="fl-ax" x="${x0 + 4}" y="${Y(-dn) + 3}">ВЕДЁТ ${aAbbr}</text>
      ${hasPens ? `<text class="fl-ax" x="${x0 - 6}" y="${lane.home + 3}" text-anchor="end">${hAbbr}</text>
      <text class="fl-ax" x="${x0 - 6}" y="${lane.away + 3}" text-anchor="end">${aAbbr}</text>
      <line x1="${x0}" x2="${x1}" y1="${lane.home}" y2="${lane.home}" class="fl-lane"/>
      <line x1="${x0}" x2="${x1}" y1="${lane.away}" y2="${lane.away}" class="fl-lane"/>` : ""}
      ${pens}${plabels}${dotEls}
    </svg></div>
    <div class="flow-cap" aria-live="polite">
      <div class="tm num">${esc(cur.time)}</div>${matchSticker(g, cur.team, cur.no, cur.gk)}
      <div class="who">${esc(cur.author)}${strengthTag(cur, cur.i ?? g.goals.indexOf(cur), d)}<small>${cur.assists.length ? cur.assists.map(esc).join(", ") : "без передач"}</small></div>
      <div class="sc num">${esc(cur.score)}</div>
    </div>
    <div class="legend"><span><i class="k-home"></i>ведут хозяева</span><span><i class="k-away"></i>ведут гости</span>
      <span><i class="k-dot"></i>гол</span><span><i class="k-dot pp"></i>в большинстве</span>${hasPens ? `<span><i class="k-pen"></i>удаление</span>` : ""}</div>
  </div>`;
}

function penaltyPeriod(p, g) {
  const s = secs(p.time);
  if (s >= 3600) return "ОТ";
  return String(Math.min(3, Math.floor(s / 1200) + 1));
}

// Стикер игрока в разборе матча: форма его команды, номер из протокола, эмблема в углу (ADR-009).
// Командный штраф — без игрока, у него остаётся эмблема
function matchSticker(g, side, no, gk) {
  const id = sideTeam(g, side);
  return playerSticker({ team: id, kit: id, role: gk ? "G" : "F", number: no });
}

function goalsTab(g, d) {
  const items = (g.goals || []).map((x, i) => ({ kind: "g", x, i, s: x.period === "РБ" ? 1e9 : secs(x.time), p: x.period }));
  const pens = (d && d.penalties) || [];
  if (recapView.pens) pens.forEach((x) => items.push({ kind: "p", x, s: secs(x.time) + 0.5, p: penaltyPeriod(x, g) }));
  items.sort((a, b) => a.s - b.s);
  let html = pens.length ? `<div class="chips" role="group" aria-label="Что показать" data-run="recap-pens">${RUN}
    <button data-recap-pens="0" class="${recapView.pens ? "" : "on"}" aria-pressed="${!recapView.pens}">Только голы</button>
    <button data-recap-pens="1" class="${recapView.pens ? "on" : ""}" aria-pressed="${recapView.pens}">С удалениями</button></div>` : "";
  if (!items.length) return html + `<div class="empty">В протоколе нет голов</div>`;
  html += `<div class="goals">`;
  let period = null;
  for (const it of items) {
    if (it.p !== period) {
      period = it.p;
      html += `<div class="period"><span class="tag">${esc(PERIOD_NAMES[period] || period)}</span></div>`;
    }
    const x = it.x;
    if (it.kind === "g") {
      html += `<div class="goal${it.i === recapView.pick ? " hl" : ""}">
        <div class="tm">${esc(x.period === "РБ" ? "Б" : x.time)}</div>
        ${matchSticker(g, x.team, x.no, x.gk)}
        <div class="who">${esc(x.author)}${strengthTag(x, it.i, d)}${x.assists.length ? `<div class="as">${x.assists.map(esc).join(", ")}</div>` : ""}</div>
        <div class="sc">${esc(x.score)}</div>
      </div>`;
    } else {
      html += `<div class="goal pen">
        <div class="tm">${esc(x.time)}</div>
        ${x.no != null ? matchSticker(g, x.team, x.no, x.gk) : `<span class="ps team">${emblem(sideTeam(g, x.team))}</span>`}
        <div class="who">${esc(x.who)}<div class="as">${esc(x.why)}</div></div>
        <div class="sc">${esc(x.min)} мин</div>
      </div>`;
    }
  }
  html += `</div>`;
  // победная не последняя (5:2 — победная 3:1) — объясняем, иначе похоже на ошибку
  if (d && d.gw != null && g.goals && d.gw < g.goals.length - 1) {
    html += `<div class="note">Победная шайба — та, после которой соперник уже не сравнял счёт. Так её считает лига.</div>`;
  }
  return html;
}

function hasStats(d) { return d && (d.shots || d.faceoffs || (d.goalies && d.goalies.length)); }

function statsTab(g, d) {
  // Больше — залито, как доля побед в очных встречах; у штрафа залит тот, у кого меньше
  const row = (label, h, a, fewer) => {
    const hw = fewer ? h < a : h > a, aw = fewer ? a < h : a > h;
    return `<div class="cmp-row"><div class="cmp-top"><b class="num">${h}</b><span>${label}</span><b class="num">${a}</b></div>
      <div class="cmp-bar" role="img" aria-label="${esc(`${label}: ${team(g.home).name} ${h}, ${team(g.away).name} ${a}`)}"><i class="${hw ? "w" : ""}" style="flex:${h || 0.01}"></i><i class="${aw ? "w" : ""}" style="flex:${a || 0.01}"></i></div></div>`;
  };
  let html = `<div class="cmp-head">${emblem(g.home)}<span>${esc(team(g.home).abbr)}</span><span></span><span>${esc(team(g.away).abbr)}</span>${emblem(g.away)}</div><div class="cmp">`;
  if (d.shots) html += row("Броски в створ", d.shots.home, d.shots.away);
  if (d.faceoffs) html += row("Вбрасывания", d.faceoffs.home, d.faceoffs.away);
  if (d.pim) html += row("Штраф, мин", d.pim.home, d.pim.away, true);
  if (d.pp) {
    const pp = (s) => `${d.pp[s][0]} из ${d.pp[s][1]}`;
    html += `<div class="cmp-row"><div class="cmp-top small"><b class="num">${pp("home")}</b><span>Голы в большинстве</span><b class="num">${pp("away")}</b></div></div>`;
  }
  html += `</div>`;
  if (d.goalies && d.goalies.length) {
    html += `<div class="label">Вратари<span class="aside">отражено</span></div><div class="gk">`;
    for (const k of d.goalies) {
      const pct = k.shots ? `${(Math.round((1000 * k.saves) / k.shots) / 10).toLocaleString("ru-RU")}%` : "—";
      const toi = k.toi && k.toi !== "60:00" && k.toi !== "65:00" ? ` · ${esc(k.toi)} на льду` : "";
      html += `<div class="gk-row">${matchSticker(g, k.team, k.no, true)}<div class="nm">${esc(k.name)}<small>${k.saves} из ${k.shots} ${plural(k.shots, "броска", "бросков", "бросков")}${toi}</small></div><div class="pc num">${pct}</div></div>`;
    }
    html += `</div>`;
  }
  return html;
}

function rosterTab(g, d) {
  const side = recapView.side;
  const groups = [["G", "Вратари"], ["D", "Защитники"], ["F", "Нападающие"]];
  let html = `<div class="chips" role="group" aria-label="Команда" data-run="recap-side">${RUN}
    ${["home", "away"].map((s) => `<button data-recap-side="${s}" class="${side === s ? "on" : ""}" aria-pressed="${side === s}">${esc(team(sideTeam(g, s)).name)}</button>`).join("")}</div>`;
  const r = d.lineups[side];
  html += `<div class="roster">`;
  for (const [key, title] of groups) {
    if (!r[key] || !r[key].length) continue;
    html += `<div class="grp">${title}</div>`;
    for (const p of r[key]) {
      const pts = p.dnp ? "запас" : key === "G" ? "" : `${p.g}+${p.a}`;
      const scored = !p.dnp && p.g + p.a > 0;
      // стикер в форме команды с номером игрока на груди — как у лидеров (ADR-009)
      const sticker = figure({ kit: sideTeam(g, side), role: key === "G" ? "G" : "F", number: p.no });
      html += `<div class="pl${scored ? " scored" : ""}${p.dnp ? " dnp" : ""}"><span class="ps">${sticker}</span>
        <span class="nm">${esc(p.name)}${p.cap ? `<span class="tag soft">${esc(p.cap)}</span>` : ""}</span>
        <span class="pts num${scored ? "" : " z"}">${pts}</span></div>`;
    }
  }
  return html + `</div><div class="note">«К» — капитан, «А» — ассистент. Очки — голы + передачи за этот матч.</div>`;
}

function recapHTML(g) {
  const d = recaps[g.id];
  let html = "";
  if (d && d.story) {
    html += `<div class="story"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2.5l2.6 6.2 6.7.5-5.1 4.4 1.6 6.5L12 16.6l-5.8 3.5 1.6-6.5L2.7 9.2l6.7-.5z"/></svg><p>${esc(d.story)}</p></div>`;
  }
  html += flowChart(g, d);
  const tabs = [["goals", "Голы"]];
  if (hasStats(d)) tabs.push(["stats", "Статистика"]);
  if (d && d.lineups) tabs.push(["roster", "Составы"]);
  if (!tabs.some((t) => t[0] === recapView.tab)) recapView.tab = "goals";
  if (tabs.length > 1) {
    html += `<div class="seg recap-seg" role="tablist" data-run="recap-tab">${RUN}${tabs.map(([k, l]) =>
      `<button role="tab" data-recap-tab="${k}" class="${recapView.tab === k ? "on" : ""}" aria-selected="${recapView.tab === k}">${l}</button>`).join("")}</div>`;
  } else if (g.goals && g.goals.length) {
    html += `<div class="label">Голы<span class="aside">${g.goals.length}</span></div>`;
  }
  const body = { goals: goalsTab, stats: statsTab, roster: rosterTab }[recapView.tab](g, d);
  return html + `<div class="recap-body">${body}</div>`;
}

function factsHTML(g) {
  const d = recaps[g.id];
  let html = `<div class="label">О матче</div><div class="facts-card"><dl class="facts">`;
  if (g.n) html += `<dt>Номер</dt><dd>№ ${esc(g.n)}</dd>`;
  html += `<dt>Город</dt><dd>${esc(team(g.home).city)}</dd>`;
  if (g.time) html += `<dt>Начало</dt><dd>${esc(g.time)} местное</dd>`;
  if (g.attendance) html += `<dt>Зрители</dt><dd>${esc(Number(g.attendance).toLocaleString("ru-RU"))}</dd>`;
  if (d && d.referees && d.referees.length) html += `<dt>Главные судьи</dt><dd>${d.referees.map(esc).join(", ")}</dd>`;
  if (d && d.linesmen && d.linesmen.length) html += `<dt>Линейные судьи</dt><dd>${d.linesmen.map(esc).join(", ")}</dd>`;
  if (d && d.coaches) {
    for (const s of ["home", "away"]) if (d.coaches[s]) html += `<dt>Тренер ${esc(team(sideTeam(g, s)).name)}</dt><dd>${esc(d.coaches[s])}</dd>`;
  }
  if (g.season) html += `<dt>Турнир</dt><dd>НМХЛ ${esc(g.season)}, ${esc(STAGE[g.stage] || g.stage)}</dd>`;
  else html += `<dt>Календарь</dt><dd>${g.official ? "ФХР, официальный" : '<span class="tag soft">предварительно</span>'}</dd>`;
  if (g.score) html += `<dt>Источник счёта</dt><dd>протокол лиги</dd>`;
  return html + `</dl></div>`;
}

function rerenderRecap() {
  const g = findGame(recapView.id);
  const box = $("#recap");
  if (!g || !box || $("#sheet").hidden) return;
  const prev = runnerState(box);
  box.innerHTML = recapHTML(g);
  placeRunners(box, prev);
  $("#facts").innerHTML = factsHTML(g);
}

// Новая вкладка разбора начинается сразу под прилипшими сегментами, а не там, куда
// обрезалась прокрутка после смены высоты
function keepTabTop() {
  const seg = $("#recap .recap-seg");
  const body = $("#recap .recap-body");
  if (!seg || !body) return;
  const gap = body.getBoundingClientRect().top - seg.getBoundingClientRect().bottom;
  if (gap < 8) $("#sheet").scrollTop += gap - 8;
}

// dir: 1 — вперёд (в прошлую встречу), −1 — «Назад»: содержимое листа въезжает с этой стороны
function openMatch(id, from = null, dir = 0) {
  const g = findGame(id);
  if (!g) {
    // прошлый матч: его нет в league.json, сначала файл разбора
    if (/^h\d+$/.test(id)) loadRecap(id).then((d) => { if (d && d.game) openMatch(id, from, dir); });
    return;
  }
  if (from && from !== id) {
    sheetStack.push(from);
    dir = 1;
  }
  if (recapView.id !== id) {
    const gw = recaps[id] ? recaps[id].gw : null;
    Object.assign(recapView, { id, tab: "goals", pens: false, side: g.home === state.fav || g.away !== state.fav ? "home" : "away",
      pick: gw != null ? gw : g.goals && g.goals.length ? g.goals.length - 1 : null });
  }
  const back = sheetStack.length
    ? `<button class="btn-round" data-back aria-label="Назад к матчу">${ICON.back}</button>` : "";
  const when = g.season
    ? `${esc(fmtLong(g.date))} ${parseISO(g.date).getUTCFullYear()} · НМХЛ ${esc(g.season)}${g.stage === "playoff" ? ", плей-офф" : ""}`
    : `${esc(fmtLong(g.date))} ${parseISO(g.date).getUTCFullYear()} · ${esc(until(g.date))}`;
  let html = `<div class="grab"></div>
    <div class="sheet-head">${back}<span class="when">${when}</span>
    <button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
    <div class="board-card">${board(g)}${periodsLine(g)}</div>`;

  const recapReady = !!recaps[id] || recapMissing.has(id);
  if (g.score) html += `<div id="recap">${recapReady ? recapHTML(g) : recapSkeleton(g)}</div>`;
  if (!g.season) html += `<div id="h2h" data-pair="${esc(pairKey(g.home, g.away))}">${state.h2h ? h2hBlock(g) : h2hSkeleton()}</div>`;
  html += `<div id="facts">${factsHTML(g)}</div>`;

  showSheet(html, dir);
  if (g.score && !recapReady) fillRecap(id);
  if (!state.h2h && !g.season) fillH2H(g);
}

// «Назад» в листе: из прошлой встречи — к матчу, откуда её открыли; иначе закрыть
function sheetBack() {
  const t = state.tour;
  if (t && !t.away && !t.greet && t.ci >= 0 && sheetOpen()) return tourSheetBack();
  if (!sheetStack.length) return closeMatch();
  openMatch(sheetStack.pop(), null, -1);
}

let sheetClosing = null;   // анимации ухода листа, пока он уезжает вниз

// Пока лист открыт, Telegram не сворачивает мини-апп свайпом вниз: этот жест закрывает лист
function tgSwipes(on) {
  if (!inTelegram || !tg.isVersionAtLeast || !tg.isVersionAtLeast("7.7")) return;
  if (on && tg.enableVerticalSwipes) tg.enableVerticalSwipes();
  if (!on && tg.disableVerticalSwipes) tg.disableVerticalSwipes();
}

function showSheet(html, dir = 0) {
  const sheet = $("#sheet");
  const back = $("#sheet-backdrop");
  const wasOpen = !sheet.hidden && !sheetClosing;
  if (sheetClosing) {
    sheetClosing.forEach((a) => a.cancel());
    sheetClosing = null;
  }
  sheet.style.transform = back.style.opacity = "";
  sheet.style.pointerEvents = back.style.pointerEvents = "";
  sheet.innerHTML = `<div class="sheet-page">${html}</div>`;
  sheet.classList.remove("pack-open");   // рамку историй ставит showPack, остальным листам она не нужна
  sheet.hidden = false;
  back.hidden = false;
  sheet.scrollTop = 0;
  document.body.classList.add("sheet-open");
  placeRunners(sheet);
  if (!sheetOpener) sheetOpener = document.activeElement;   // при переходе внутри листа — прежний
  // во время тура фокус остаётся в карточке тура: второй Enter не закроет лист под ним
  if (!state.tour || state.tour.away) sheet.querySelector("[data-close]").focus({ preventScroll: true });
  if (wasOpen && dir && !calm()) {
    sheet.firstElementChild.animate([{ opacity: 0, transform: `translateX(${dir * 24}px)` }, { opacity: 1, transform: "none" }],
      { duration: 200, easing: EASE_OUT });
  }
  if (inTelegram) {
    tg.BackButton.show();
    if (!wasOpen) tgSwipes(false);
    if (tg.HapticFeedback) tg.HapticFeedback.impactOccurred("light");
  }
}

// Уход зеркален приходу: лист уезжает вниз с разгоном. fromY — откуда, если его уже тянут пальцем
function closeMatch(fromY = 0) {
  sheetStack.length = 0;
  const sheet = $("#sheet");
  const back = $("#sheet-backdrop");
  if (sheet.hidden || sheetClosing) return;
  document.body.classList.remove("sheet-open");
  tourBack();   // лист открыли из тура нажатием по цели — тур идёт дальше
  if (inTelegram) {
    tg.BackButton.hide();
    tgSwipes(true);
  }
  if (sheetOpener && sheetOpener.isConnected) sheetOpener.focus({ preventScroll: true });
  sheetOpener = null;
  const done = () => {
    sheet.hidden = true;
    back.hidden = true;
    sheet.classList.remove("pack-open");
    sheet.style.transform = back.style.opacity = "";
    sheet.style.pointerEvents = back.style.pointerEvents = "";
  };
  if (calm()) return done();
  sheet.style.pointerEvents = back.style.pointerEvents = "none";
  const anims = [
    sheet.animate([{ transform: `translateY(${fromY}px)` }, { transform: "translateY(100%)" }], { duration: 200, easing: EASE_IN, fill: "forwards" }),
    back.animate([{ opacity: back.style.opacity || 1 }, { opacity: 0 }], { duration: 180, easing: "ease-in", fill: "forwards" }),
  ];
  sheetClosing = anims;
  anims[0].finished.then(() => {
    if (sheetClosing !== anims) return;
    sheetClosing = null;
    done();
    anims.forEach((a) => a.cancel());
  }).catch(() => {});
}

// Свайп вниз закрывает лист: он идёт за пальцем, пока прокрутка листа в самом верху.
// Дальше 30% высоты или быстрым движением — закрыть, иначе пружиной на место.
// В историях свайп вбок — соседний клуб (ADR-016)
const SWIPE_X = 48;   // свайп вбок в историях: не короче и вбок в полтора раза больше, чем вниз
function initSheetDrag() {
  const sheet = $("#sheet");
  const back = $("#sheet-backdrop");
  const SLOP = 8;   // первые пиксели — ещё не жест, а неточное касание
  let x0 = 0, y0 = 0, dy = 0, lastY = 0, lastT = 0, v = 0, armed = false, drag = false, side = false;
  sheet.addEventListener("touchstart", (e) => {
    armed = e.touches.length === 1 && sheet.scrollTop <= 0 && !sheetClosing;
    side = e.touches.length === 1 && sheet.classList.contains("pack-open") && !!state.pack && !sheetClosing;
    drag = false;
    dy = v = 0;
    x0 = e.touches[0].clientX;
    y0 = lastY = e.touches[0].clientY;
    lastT = e.timeStamp;
  }, { passive: true });
  sheet.addEventListener("touchmove", (e) => {
    if (!armed) return;
    const y = e.touches[0].clientY;
    dy = y - y0;
    if (!drag) {
      if (dy < 0 || sheet.scrollTop > 0) { armed = false; return; }   // листают вверх — обычная прокрутка
      const dx = Math.abs(e.touches[0].clientX - x0);
      if (dy < SLOP && dx < SLOP) return;
      if (side && dx > dy) { armed = false; return; }   // в историях вбок — соседний клуб, а не закрытие
      if (dy < SLOP) return;
      drag = true;
    }
    e.preventDefault();
    v = (y - lastY) / Math.max(1, e.timeStamp - lastT);
    lastY = y;
    lastT = e.timeStamp;
    const d = Math.max(0, dy - SLOP);
    sheet.style.transform = `translateY(${d}px)`;
    back.style.opacity = String(Math.max(0, 1 - d / sheet.offsetHeight));
  }, { passive: false });
  const end = (e) => {
    armed = false;
    const t = side && !drag && e && e.changedTouches && e.changedTouches[0];
    side = false;
    if (t) {
      const dx = t.clientX - x0;
      if (Math.abs(dx) >= SWIPE_X && Math.abs(dx) > 1.5 * Math.abs(t.clientY - y0)) return packClub(dx < 0 ? 1 : -1);
    }
    if (!drag) return;
    drag = false;
    const d = Math.max(0, dy - SLOP);
    if (d > sheet.offsetHeight * 0.3 || v > 0.5) return closeMatch(d);
    if (!calm()) {
      sheet.animate([{ transform: `translateY(${d}px)` }, { transform: "translateY(0)" }], { duration: 280, easing: "cubic-bezier(.3, 1.4, .5, 1)" });
      back.animate([{ opacity: back.style.opacity || 1 }, { opacity: 1 }], { duration: 200, easing: EASE_OUT });
    }
    sheet.style.transform = back.style.opacity = "";
  };
  sheet.addEventListener("touchend", end);
  sheet.addEventListener("touchcancel", () => end(null));
}

// ---------- бегущая строка ----------

function fillMarquee() {
  const d = state.data;
  const first = d.games.length ? d.games[0].date : null;
  const played = d.games.filter((g) => g.score).length;
  const facts = [
    `${d.league} ${d.season}`,
    first && daysFromToday(first) > 0 ? `старт ${fmtLong(first)}` : `сыграно ${played} из ${d.games.length}`,
    `${d.teams.length} команд`,
    `${d.games.length} матчей`,
    "Восток и Запад",
    "8 лучших — в плей-офф",
  ];
  const once = facts.map((f) => `<span>${esc(f)}</span>`).join("");
  $("#marquee").innerHTML = once + once;
}

// ---------- навигация ----------

// dir — направление смены вкладки: новый экран въезжает с её стороны; 0 — без перехода
function render(dir = 0) {
  const screen = $("#screen");
  const onboarding = !state.fav;
  $("#tabs").hidden = onboarding;
  document.body.dataset.tab = onboarding ? "" : state.tab;
  if (onboarding) {
    screen.innerHTML = renderOnboarding();
    addThemeToggle();
    return;
  }
  const cur = $("#tabs button.active");
  if (!cur || cur.dataset.tab !== state.tab || !$("#tabs").classList.contains("ready")) setTab(state.tab, false);
  const views = { home: renderHome, calendar: renderCalendar, table: renderTable, led: renderLed, me: renderMe };
  screen.innerHTML = views[state.tab]();
  addThemeToggle();
  placeRunners(screen);
  const anchor = state.tab === "calendar" && !state.scrolledToNext && $("#next-anchor");
  if (anchor) {
    anchor.scrollIntoView({ block: "center" });
    state.scrolledToNext = true;
  } else if (state.tab !== "calendar") {
    window.scrollTo(0, 0);
  }
  if (state.tab === "home") {
    countUp(screen);
    watchFeedImages(screen);
    watchSeen(screen);
    mountStream();
  }
  if (state.tab === "led") ledMounted();
  if (dir && !calm()) {
    // двигаем детей, а не сам экран: его край обрезает сдвиг (#screen в style.css)
    for (const el of screen.children) {
      el.animate([{ opacity: 0, transform: `translateX(${dir * 16}px)` }, { opacity: 1, transform: "none" }], { duration: 220, easing: EASE_OUT });
    }
  }
}

// «Наш лёд» (ADR-017) — между «Таблицей» и «Я»: первые три вкладки про настоящий хоккей, «Я» всегда последняя
const TAB_ORDER = ["home", "calendar", "table", "led", "me"];
const tabDir = (from, to) => Math.sign(TAB_ORDER.indexOf(to) - TAB_ORDER.indexOf(from)) || 1;

function haptic() {
  if (inTelegram && tg.HapticFeedback) tg.HapticFeedback.selectionChanged();
}

// Меню отвечает сразу: бегунок поехал в этом же кадре, а тяжёлая перерисовка экрана — в следующем,
// когда анимация уже идёт на видеокарте и её не остановить занятым основным потоком
let goToken = 0;
function go(tab) {
  // нажатие на активную вкладку — наверх: лента лиги длинная
  if (tab === state.tab) {
    if (window.scrollY > 0) window.scrollTo({ top: 0, behavior: calm() ? "auto" : "smooth" });
    return;
  }
  const dir = tabDir(state.tab, tab);
  state.tab = tab;
  state.draft = null;
  if (tab === "calendar") state.scrolledToNext = false;
  haptic();
  setTab(tab, true);
  const token = ++goToken;
  nextFrame(() => { if (token === goToken) render(dir); });
}

function confirmTeam(id = state.draft || state.fav) {
  if (!id) return;
  const wasFav = state.fav;
  const dir = state.fav ? tabDir(state.tab, "home") : 1;
  state.fav = id;
  state.draft = null;
  state.cal = calFor(id);
  state.conf = team(id).conf;
  saveFav(id);
  closeMatch();
  // пришёл по ссылке на лидеров или в игру и только что выбрал команду — ведём туда, куда звали
  state.tab = !wasFav && state.tableView === "players" ? "table" : !wasFav && state.ledLink ? "led" : "home";
  if (inTelegram && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
  render(dir);
  // новичок, пришедший не по ссылке, — проводник показывает приложение (ADR-011);
  // сменил команду — новый проводник знакомится, когда закроется лист
  state.meet = null;
  if (!wasFav && !state.openedFromLink && !tourSeen()) return nextFrame(() => startTour(true));
  if (!wasFav && !state.openedFromLink && !tourDone()) return nextFrame(tourNews);
  closeCoach();
  if (wasFav && wasFav !== id) setTimeout(greetGuide, 450);
}

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-tab],[data-game],[data-pick],[data-confirm],[data-cal-team],[data-cal-side],[data-cal-conf],[data-cal-other],[data-cal-pick],[data-conf],[data-team],[data-theme-pick],[data-theme-toggle],[data-close],[data-switch-open],[data-switch],[data-story],[data-invite],[data-remind],[data-recap-tab],[data-recap-goal],[data-recap-pens],[data-recap-side],[data-back],[data-retry],[data-table-view],[data-lead-open],[data-tour],[data-tour-restart],[data-tour-all],[data-tour-ch],[data-guide],[data-meet-close],[data-coach-back],[data-post],[data-post-more],[data-feed-hide],[data-feed-unhide],[data-feed-rules],[data-feed-league],[data-feed-leaders],[data-stream-filter],[data-pack],[data-pack-nav],#sheet-backdrop");
  if (!el || el.disabled) return;
  if (el.dataset.guide) return guideHop(el);
  if (el.hasAttribute("data-meet-close") || (el.hasAttribute("data-coach-back") && state.meet)) return closeCoach();
  if (el.hasAttribute("data-coach-back")) return tourTap(e);
  if (el.dataset.tour) {
    const t = state.tour;
    const act = el.dataset.tour;
    haptic();
    if (act === "next" && t && !t.greet) {
      if (!t.busy && t.primary) t.primary();
      return;
    }
    if (act === "skip") return tourSkip();
    if (act === "final" && t && !t.greet) return tourFinish();
    if (act === "remind") {
      const url = `${state.data.links.bot}?start=remind`;
      if (inTelegram) tg.openTelegramLink(url);
      else window.open(url, "_blank", "noopener");
    }
    return tourEnd();
  }
  if (el.dataset.postMore) return openPostMenu(el.dataset.postMore, el);
  if (el.dataset.post) return openPost(el.dataset.post);
  if (el.dataset.feedHide) {
    const all = feedHidden();
    all[el.dataset.feedHide] = feedChannelTitle(el.dataset.feedHide);
    saveFeedHidden(all);
    haptic();
    // лента не сбрасывается к началу: на место скрытой карточки встаёт следующая
    const card = postMenuCard && postMenuCard.isConnected ? postMenuCard : null;
    const place = feedPlace(card);
    closeMatch();
    refreshFeed();
    return keepPlace(place, card && card.dataset.id);
  }
  if (el.dataset.feedUnhide) {
    const all = feedHidden();
    delete all[el.dataset.feedUnhide];
    saveFeedHidden(all);
    haptic();
    openFeedRules();
    if (state.tab === "me") render();
    return;
  }
  if (el.hasAttribute("data-feed-rules")) return openFeedRules();
  if (el.hasAttribute("data-feed-league")) {
    state.cal = calFor(null);
    return go("calendar");
  }
  if (el.dataset.pack) return openPack(el.dataset.pack);
  if (el.dataset.packNav) return packNav(Number(el.dataset.packNav));
  if (el.dataset.streamFilter) {
    if (el.dataset.streamFilter === state.streamFilter) return;
    state.streamFilter = el.dataset.streamFilter;
    haptic();
    const top = $("#stream-wrap").getBoundingClientRect().top;
    const stuck = $(".stream-pills").classList.contains("stuck");
    refreshStream();
    // фильтры прилипли — к началу новой ленты под ними; иначе пилюли остаются под пальцем
    if (stuck) return window.scrollBy(0, $(".stream-pin").getBoundingClientRect().top - marqueeH());
    const now = $("#stream-wrap").getBoundingClientRect().top;
    if (Math.abs(now - top) > 1) window.scrollBy(0, now - top);
    return;
  }
  if (el.hasAttribute("data-feed-leaders")) {
    state.tableView = "players";
    return go("table");
  }
  if (el.hasAttribute("data-tour-restart")) return openHints();
  if (el.hasAttribute("data-tour-all")) return replayTour(null);
  if (el.dataset.tourCh) return replayTour([Number(el.dataset.tourCh)]);
  if (el.hasAttribute("data-switch-open")) return openTeamSheet();
  if (el.dataset.switch) return confirmTeam(el.dataset.switch);
  if (el.hasAttribute("data-story")) return shareStory();
  if (el.hasAttribute("data-invite")) return shareLink(inviteLink(state.fav), `Болеем вместе за ${team(state.fav).name}: матчи, таблица и счёт РХЛ`);
  if (el.hasAttribute("data-remind")) {
    const url = `${state.data.links.bot}?start=remind`;
    return inTelegram ? tg.openTelegramLink(url) : window.open(url, "_blank", "noopener");
  }
  if (el.hasAttribute("data-theme-toggle")) return toggleTheme(el);
  if (el.dataset.themePick) {
    haptic();
    return switchTheme(el.dataset.themePick, el);
  }
  if (el.id === "sheet-backdrop" || el.hasAttribute("data-close")) return closeMatch();
  if (el.dataset.retry === "leaders") {
    haptic();
    leadersFailed = false;
    $("#table-body").innerHTML = leadersBody();
    return;
  }
  if (el.dataset.retry) {
    const g = findGame(recapView.id);
    const box = $(`#${el.dataset.retry}`);
    if (!g || !box) return;
    haptic();
    if (el.dataset.retry === "h2h") {
      box.innerHTML = h2hSkeleton();
      return fillH2H(g);
    }
    box.innerHTML = recapSkeleton(g);
    return fillRecap(g.id);
  }
  if (el.hasAttribute("data-back")) return sheetBack();
  if (el.dataset.recapTab || el.dataset.recapGoal || el.dataset.recapPens || el.dataset.recapSide) {
    if (el.dataset.recapTab) recapView.tab = el.dataset.recapTab;
    if (el.dataset.recapGoal) recapView.pick = Number(el.dataset.recapGoal);
    if (el.dataset.recapPens) recapView.pens = el.dataset.recapPens === "1";
    if (el.dataset.recapSide) recapView.side = el.dataset.recapSide;
    // после перерисовки вернуть фокус на ту же кнопку: иначе с клавиатуры он улетает в начало
    const attr = ["recapTab", "recapGoal", "recapPens", "recapSide"].find((k) => el.dataset[k]);
    const sel = `[data-${attr.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase())}="${el.dataset[attr]}"]`;
    haptic();
    rerenderRecap();
    if (el.dataset.recapTab) keepTabTop();
    const f = $(`#recap ${sel}`);
    if (f) f.focus({ preventScroll: true });
    return;
  }
  if (el.hasAttribute("data-confirm")) return confirmTeam();
  if (el.dataset.tab) return go(el.dataset.tab);
  if (el.dataset.game) {
    const id = el.dataset.game;
    const from = el.closest("#sheet") ? recapView.id : null;
    // прошлая встреча грузится из своего файла — строка пульсирует, пока он едет
    if (!findGame(id) && /^h\d+$/.test(id)) {
      el.classList.add("busy");
      el.setAttribute("aria-busy", "true");
      loadRecap(id).then((d) => {
        el.classList.remove("busy");
        el.removeAttribute("aria-busy");
        if (d && d.game) openMatch(id, from);
        else if (inTelegram && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred("error");
      });
      return;
    }
    return openMatch(id, from);
  }
  if (el.dataset.pick) {
    // выбор на месте, без перерисовки: карточка пружинит, по центру — знакомство с талисманом клуба
    state.draft = el.dataset.pick;
    haptic();
    document.querySelectorAll("[data-pick]").forEach((b) => {
      b.classList.toggle("on", b === el);
      b.setAttribute("aria-pressed", b === el);
    });
    el.classList.remove("pop");
    void el.offsetWidth;   // перезапустить анимацию на той же карточке
    el.classList.add("pop");
    openMeet(state.draft);
    return;
  }
  if (el.hasAttribute("data-cal-other")) return openCalSheet();
  if (el.dataset.calTeam !== undefined || el.dataset.calPick) {
    state.cal = calFor(el.dataset.calPick || el.dataset.calTeam);
    closeMatch();
    haptic();
    return refreshCalendar(false);
  }
  if (el.dataset.calConf) {
    state.cal.conf = el.dataset.calConf;
    haptic();
    return refreshCalendar(true);
  }
  if (el.dataset.calSide) {
    state.cal.side = el.dataset.calSide;
    haptic();
    return refreshCalendar(true);
  }
  if (el.dataset.tableView) {
    if (el.dataset.tableView === state.tableView) return;
    state.tableView = el.dataset.tableView;
    haptic();
    return refreshTable();
  }
  if (el.dataset.leadOpen) return openLeaders(el.dataset.leadOpen);
  if (el.dataset.conf) {
    // чип отвечает сразу, бегунок перетекает; таблица — в следующем кадре
    if (el.dataset.conf === state.conf) return;
    state.conf = el.dataset.conf;
    haptic();
    const group = el.parentNode;
    const prev = runnerState(group.parentNode)[group.dataset.run];
    group.querySelectorAll("[data-conf]").forEach((b) => {
      b.classList.toggle("on", b === el);
      b.setAttribute("aria-pressed", b === el);
    });
    placeRunner(group, prev);
    return nextFrame(() => {
      const box = $("#standings");
      if (!box) return;
      box.innerHTML = standingsTable();
      fadeIn(box);
    });
  }
  if (el.dataset.team) {
    state.cal = calFor(el.dataset.team);
    return go("calendar");
  }
});

// iOS показывает :active, только если на странице слушают касания
document.addEventListener("touchstart", () => {}, { passive: true });

// Бегунки держатся за свои кнопки, когда меняется ширина экрана или догрузился шрифт
function resyncRunners() {
  if (state.fav) setTab(state.tab, false);
  placeRunners(document.body);
}
window.addEventListener("resize", resyncRunners);
if (document.fonts && document.fonts.ready) document.fonts.ready.then(resyncRunners);

// Строки и карточки с role="button" нажимаются с клавиатуры, Esc закрывает карточку матча
document.addEventListener("keydown", (e) => {
  const box = $("#tour");
  if (box && !(state.tour && state.tour.away)) {
    if (e.key === "Tab") {
      // фокус заперт в карточке: Tab ходит по её кнопкам по кругу
      e.preventDefault();
      const list = [...box.querySelectorAll(".coach-card button")];
      if (!list.length) return box.focus();
      const i = list.indexOf(document.activeElement);
      const n = i < 0 ? (e.shiftKey ? list.length - 1 : 0) : (i + (e.shiftKey ? list.length - 1 : 1)) % list.length;
      return list[n].focus();
    }
    // страница под туром не листается — кроме шага «листай»; истории под туром стрелками не листаются
    if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End"].includes(e.key) && !box.classList.contains("scroll")) return e.preventDefault();
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") return e.preventDefault();
  }
  if (e.key === "Escape") {
    const t = state.tour;
    if ($("#tour") && state.meet) return closeCoach();
    if (t && !t.away) return t.greet ? tourEnd() : tourSkip();
    return sheetBack();
  }
  // ряд «Сегодня» — одна остановка Tab, по кружкам — стрелками, Home и End
  const pk = e.target.closest && e.target.closest(".packs");
  if (pk && ["ArrowRight", "ArrowLeft", "Home", "End"].includes(e.key)) {
    e.preventDefault();
    const list = [...pk.querySelectorAll(".pack")];
    const i = list.indexOf(e.target);
    const n = e.key === "Home" ? 0 : e.key === "End" ? list.length - 1 : Math.min(list.length - 1, Math.max(0, i + (e.key === "ArrowRight" ? 1 : -1)));
    list.forEach((b, k) => b.setAttribute("tabindex", k === n ? "0" : "-1"));
    list[n].focus();
    return list[n].scrollIntoView({ block: "nearest", inline: "nearest", behavior: calm() ? "auto" : "smooth" });
  }
  // истории с клавиатуры (Telegram Desktop): стрелки — как касание справа и слева
  if ((e.key === "ArrowRight" || e.key === "ArrowLeft") && state.pack && sheetOpen() && $("#sheet .pack-page")) {
    e.preventDefault();
    return packNav(e.key === "ArrowRight" ? 1 : -1);
  }
  if ((e.key === "Enter" || e.key === " ") && e.target.matches('[role="button"]')) {
    e.preventDefault();
    e.target.dispatchEvent(new MouseEvent("click", { bubbles: true }));   // у SVG нет .click()
  }
});

// ---------- запуск ----------

function startParam() {
  const fromTg = inTelegram && tg.initDataUnsafe && tg.initDataUnsafe.start_param;
  const q = new URLSearchParams(location.search);
  return fromTg || q.get("tgWebAppStartParam") || q.get("startapp") || q.get("team");
}

// Ссылка на матч из бота: ?match=<id> или startapp=m-<id> (ADR-008)
function matchParam() {
  const fromTg = inTelegram && tg.initDataUnsafe && tg.initDataUnsafe.start_param;
  const q = new URLSearchParams(location.search);
  const sp = fromTg || q.get("tgWebAppStartParam") || q.get("startapp") || "";
  return q.get("match") || (/^m-/.test(sp) ? sp.slice(2) : null);
}

// Ссылка на лидеров лиги из бота: ?view=leaders или startapp=leaders (ADR-009)
function leadersParam() {
  const fromTg = inTelegram && tg.initDataUnsafe && tg.initDataUnsafe.start_param;
  const q = new URLSearchParams(location.search);
  return q.get("view") === "leaders" || (fromTg || q.get("tgWebAppStartParam") || q.get("startapp")) === "leaders";
}

function pickFav(id) {
  state.fav = id;
  state.cal.team = id;
  state.conf = state.teams[id].conf;
}

function initTelegram() {
  const w = window.Telegram && window.Telegram.WebApp;
  if (!w || tg) return;
  tg = w;
  inTelegram = !!tg.initData;
  if (!inTelegram) return;
  tg.ready();
  tg.expand();
  tg.onEvent("themeChanged", applyTheme);
  tg.BackButton.onClick(sheetBack);
  if (!$("#sheet").hidden) {
    tg.BackButton.show();
    tgSwipes(false);
  }
  applyTheme();
  dropOldKeys();
  // команда могла быть выбрана на другом устройстве — она в облаке Telegram
  const c = cloud();
  if (c) {
    c.getItems([TOUR_KEY, TOUR_CH_KEY], (err, v) => {
      if (err || !v) return;
      // отметки глав — объединение устройства и облака
      const ch = [...new Set((lsGet(TOUR_CH_KEY) || "") + (v[TOUR_CH_KEY] || ""))].filter((x) => CHAPTER[x]).sort().join("");
      if (ch) lsSet(TOUR_CH_KEY, ch);
      // уровень тура только растёт: облако берём, если там больше
      const cv = tourLevel(v[TOUR_KEY]);
      if (cv <= tourLevel()) return;
      lsSet(TOUR_KEY, String(cv));
      if (cv >= 2) try { localStorage.removeItem(TOUR_AT_KEY); } catch (e) { /* приватный режим */ }
      // тур здесь ещё не начинали, а на другом устройстве прошли — вступление гаснет, вместо него «Новое»
      const t = state.tour;
      if (t && !t.greet && t.ci < 0 && t.key !== "final") {
        state.tour = null;
        closeCoach();
        if (cv < TOUR_LEVEL) tourNews();
      }
    });
  }
  if (c) {
    c.getItem(FEED_HIDDEN_KEY, (err, v) => {
      if (err || !v) return;
      let far = {};
      try { far = JSON.parse(v) || {}; } catch (e) { return; }
      const all = { ...far, ...feedHidden() };
      if (Object.keys(all).length === Object.keys(feedHidden()).length) return;
      lsSet(FEED_HIDDEN_KEY, JSON.stringify(all));
      refreshFeed();
    });
  }
  if (!state.fav && state.data && c) {
    c.getItem(FAV_KEY, (err, v) => {
      if (err || !v || !state.teams[v] || state.fav) return;
      lsSet(FAV_KEY, v);
      rememberSplash(v);
      pickFav(v);
      render();
    });
  }
}
window.__onTelegram = initTelegram;

function hideSplash(minMs = SPLASH_MIN_MS) {
  const splash = $("#splash");
  if (!splash || splash.classList.contains("out")) return;
  const wait = calm() ? 0 : Math.max(0, minMs - (Date.now() - (window.__splashT0 || 0)));
  setTimeout(() => {
    splash.classList.add("out");
    setTimeout(() => splash.remove(), 420);
  }, wait);
}

function readCache() {
  try {
    const d = JSON.parse(lsGet(DATA_KEY));
    return d && Array.isArray(d.games) && Array.isArray(d.teams) ? d : null;
  } catch (e) {
    return null;
  }
}

async function fetchData() {
  const r = await fetch("data/league.json");   // тот же запрос, что в <link rel="preload">
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

// Эмблемы декодируются заранее и держатся в памяти: при перерисовке экрана картинка встаёт
// в том же кадре, что и строка, а не мигает пустым кругом
const warmed = [];
function warmLogos() {
  if (warmed.length) return;
  const logos = [...new Set(state.data.teams.map((t) => t.logo).filter(Boolean))];
  (window.requestIdleCallback || setTimeout)(() => logos.forEach((src) => {
    const img = new Image();
    img.src = src;
    if (img.decode) img.decode().catch(() => {});
    warmed.push(img);
  }), { timeout: 1500 });
}

function useData(d) {
  state.data = d;
  state.teams = {};
  d.teams.forEach((t) => { state.teams[t.id] = t; });
  fillMarquee();
  warmLogos();
}

// cached — данные уже были на устройстве: заставка короткая (DESIGN.md → «Экран запуска»)
function boot(d, cached = false) {
  useData(d);
  const fromLink = startParam();
  const saved = lsGet(FAV_KEY);
  // Игра из бота или от друга: startapp=led, startapp=lg-<код> — раньше id команды (ADR-017)
  const ledLink = ledLinkParam(fromLink);
  if (ledLink && !state.openedFromLink) {
    state.openedFromLink = true;
    state.ledLink = true;
    state.tab = "led";
    ledFromLink(ledLink);
  }
  ledRestoreJoin();
  // Ссылка с командой (приглашение от друга, бот) новичку открывает знакомство с этим клубом:
  // выбирает он сам. Свой клуб ссылка не перезаписывает
  if (saved && state.teams[saved]) {
    pickFav(saved);
    rememberSplash(saved);
  } else if (fromLink && state.teams[fromLink]) {
    state.draft = fromLink;
  }
  // Из бота — сразу «Таблица → Игроки». Новичок сначала выбирает команду, потом попадает туда же
  if (leadersParam() && !state.openedFromLink) {
    state.openedFromLink = true;
    state.tab = "table";
    state.tableView = "players";
  }
  render();
  hideSplash(cached ? SPLASH_REPEAT_MS : SPLASH_MIN_MS);
  const mid = matchParam();
  if (mid && !state.openedFromLink && (games().some((g) => g.id === mid) || /^h\d+$/.test(mid))) {
    state.openedFromLink = true;
    openMatch(mid);
  }
  if (!state.fav && state.draft && !state.openedFromLink) {
    setTimeout(() => { if (!state.fav && state.draft && $("#sheet").hidden) openMeet(state.draft); }, (cached ? SPLASH_REPEAT_MS : SPLASH_MIN_MS) + 300);
  }
  // Игра: точка «надо решить» на вкладке — в фоне, когда экран уже нарисован
  if (state.fav && state.tab !== "led") setTimeout(ledPeek, (cached ? SPLASH_REPEAT_MS : SPLASH_MIN_MS) + 2500);
  // Тур и проводник — один раз и не поверх ссылки из бота: прерванный тур — «Продолжение»,
  // прошёл тур раньше — «Новое» (ADR-016), нынешний пройден — знакомство при смене команды, иначе вступление
  if (state.fav && !state.openedFromLink && !state.tour) {
    setTimeout(() => {
      if (state.tour || !$("#sheet").hidden) return;
      const at = savedAt();
      if (tourDone()) greetGuide();
      else if (at) tourResume(at);
      else if (tourSeen()) tourNews();
      else startTour(false);
    }, (cached ? SPLASH_REPEAT_MS : SPLASH_MIN_MS) + 500);
  }
}

async function main() {
  dropOldKeys();
  applyTheme();
  if (window.matchMedia) matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);
  initTelegram();   // если скрипт Telegram уже успел загрузиться

  const fresh = fetchData();
  initSheetDrag();
  const cached = readCache();
  if (cached) boot(cached, true);
  try {
    const d = await fresh;
    const changed = !cached || d.updated !== cached.updated;
    if (changed) lsSet(DATA_KEY, JSON.stringify(d));
    if (!cached) {
      boot(d);
    } else if (changed) {
      const y = window.scrollY;
      useData(d);
      render();
      window.scrollTo(0, y);
    }
  } catch (err) {
    if (cached) return;
    $("#screen").innerHTML = `<div class="empty" style="margin-top:24px">Не удалось загрузить матчи. Проверьте интернет и откройте приложение ещё раз.</div>`;
    hideSplash();
  }
}

main();

"use strict";
// «Буллит» — аркада на один палец (ADR-017). Бросаешь по воротам клуба лиги: три буллита на клуб,
// нужен один гол, цель похода — пройти все 26 ворот РХЛ. Интерфейс — по DESIGN.md.
//
// Файл делится надвое. Сверху — движок: он ничего не знает про мини-апп, принимает клубы и колбэки
// и рисует на канвасе. Снизу, за чертой, — связь с приложением: карточка на «Главной», лист, рекорд.
// Тем же движком живёт прототип (tools/bullit_prototype.py), поэтому выше черты нет ни esc, ни
// showSheet, ни state.
//
// Как устроен бросок. Вратарь ходит поперёк ворот сам, а на бросок — реагирует: через свою задержку
// он едет к шайбе, но с разгоном и предельной скоростью, и инерцию не отменить. Поэтому забивают не
// в пустое место, а против его движения: он поехал вправо — бьёшь влево. Случайности нет нигде:
// по одному и тому же касанию всегда выходит один и тот же бросок.
//
// Сложность клуба — число t от 0 до 1 из data/bullit.json (bullit.py, пропущенные за игру).
// Числа поведения ворот — здесь, в TUNE; проверяются прогоном tools/bullit_balance.js.

(function (global) {

// ---------- кадр ----------

const W = 360, H = 418;              // логический кадр, канвас масштабируется под экран
const GX0 = 60, GX1 = 300;           // штанги: ворота уже вратаря с рывком — поэтому бьют по углам
const GTOP = 86, GLINE = 232;        // перекладина и линия ворот
const POST = 7;                      // ближе этого к штанге и перекладине — звон
const SHOT_X = 180, SHOT_Y = 344;    // откуда летит шайба
const PUCK = 9;
const LOW = GLINE - 16;              // куда идёт бросок, пока высота не открылась

// Ворота от t: слева — самые пробиваемые, справа — «Рязань-ВДВ» этого сезона
const TUNE = {
  halfW: [46, 70],        // полуширина щитков: вратарь закрывает треть ворот и без рывка
  bodyH: [120, 142],      // рост от линии ворот: у верхних ворот окна под перекладиной не остаётся
  glove: [20, 32],        // ловушка: верх своей стороны. Низ с этой стороны свободен
  block: [15, 26],        // блин: низ своей стороны. Верх с этой стороны свободен
  hole: [30, 12],         // «домик»: открыт, пока вратарь разъезжается
  amp: [52, 30],          // размах патруля: чем дальше по лестнице, тем ближе он держится к центру
  patrol: [0.9, 2.0],     // как быстро ходит поперёк ворот, рад/с
  vmax: [240, 470],       // предел скорости в рывке к шайбе, px/с
  accel: [1500, 4600],    // разгон и торможение, px/с²: главный предел, инерцию не отменить
  delay: [0.21, 0.085],    // задержка реакции, с: сколько он ещё едет по-старому
  aim: [1.7, 3.8],        // скорость прицела, рад/с: от неё цена дрожи в пальце
  flight: [400, 265],     // полёт шайбы, мс
  lift: [2.2, 3.2],       // скорость шкалы высоты, рад/с
};
const LIFT_FROM = 5;      // с шестых ворот появляется второй тап — высота (сложность слоями)
const SHOTS = 3;          // буллитов на клуб, нужен один гол
const HOLE_OPEN = 0.62;   // «домик» открыт, пока вратарь едет быстрее этой доли своего предела
const TRACK = 7;          // как цепко он держится своей дорожки на патруле

const lerp = (a, b, t) => a + (b - a) * t;
const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);

function shape(t) {
  const out = {};
  for (const k in TUNE) out[k] = lerp(TUNE[k][0], TUNE[k][1], t);
  out.top = GLINE - out.bodyH;             // плечи
  return out;
}

// Дорожка патруля: не ровный маятник, вторая волна сбивает ритм. Ничего случайного здесь нет.
function patrolX(phase, amp) {
  return 180 + amp * (0.86 * Math.sin(phase) + 0.14 * Math.sin(2.3 * phase + 0.7));
}
function patrolAmp(s) { return s.amp; }

// Шаг вратаря: он всегда едет к цели — к своей дорожке или к шайбе — с разгоном и пределом скорости
function move(gx, gv, target, s, dt) {
  const want = clamp((target - gx) * TRACK, -s.vmax, s.vmax);
  const dv = clamp(want - gv, -s.accel * dt, s.accel * dt);
  const v = gv + dv;
  return [gx + v * dt, v];
}

// Что он закрывает. Щитки — от линии ворот до плеч. Ловушка достаёт верх своей стороны, блин —
// низ своей: значит, низом бьют под ловушку, а верхом — над блином. Под перекладиной остаётся
// окно, под разъезжающимся вратарём — «домик».
function covered(x, y, gx, gv, s) {
  const mid = s.top + s.bodyH * 0.45;
  if (Math.abs(gv) > HOLE_OPEN * s.vmax && Math.abs(x - gx) < s.hole / 2 && y > GLINE - s.bodyH * 0.32) return "";
  if (Math.abs(x - gx) <= s.halfW && y >= s.top) return "body";
  if (x > gx && x - gx <= s.halfW + s.glove && y <= mid && y >= s.top - s.glove * 0.5) return "catch";
  if (x < gx && gx - x <= s.halfW + s.block && y >= mid) return "block";
  return "";
}

// Где окажется вратарь в момент удара: едет по-старому, пока не кончилась задержка, потом — к шайбе
function predict(gx, gv, patrol, ax, s, flight) {
  const step = 1 / 240;
  let x = gx, v = gv, t = 0;
  while (t < flight) {
    const dt = Math.min(step, flight - t);
    t += dt;
    const target = t < s.delay ? patrol(t) : ax;
    [x, v] = move(x, v, target, s, dt);
  }
  return [x, v];
}

// ---------- цвета из темы ----------

const PALETTE = ["--paper", "--ink", "--edge", "--mist", "--muted", "--sel", "--mint", "--lavender",
  "--ember", "--sun", "--blue", "--canvas", "--edge-strong"];

function palette(el) {
  const cs = getComputedStyle(el);
  const out = {};
  PALETTE.forEach((k) => { out[k.slice(2)] = (cs.getPropertyValue(k) || "").trim() || "#000"; });
  return out;
}

// ---------- движок ----------

function Game(root, opts) {
  const clubs = opts.clubs.slice();
  const calm = !!(global.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);
  const haptic = opts.haptic || function () {};
  const sprites = new Map();
  let C = palette(root);

  const el = {
    wrap: root.querySelector(".bl-play"),
    canvas: root.querySelector(".bl-ice"),
    club: root.querySelector(".bl-club"),
    name: root.querySelector(".bl-name"),
    count: root.querySelector(".bl-count"),
    dots: root.querySelector(".bl-dots"),
    road: root.querySelector(".bl-road"),
    hint: root.querySelector(".bl-hint"),
    over: root.querySelector(".bl-over"),
  };
  const ctx = el.canvas.getContext("2d");

  const g = {
    i: 0,                    // какие ворота проходим
    shot: 0,                 // буллитов сделано на этих воротах
    shots: [],               // их исход: goal | save | post
    best: opts.record || 0,
    phase: 0, aimPhase: 0, liftPhase: 0,
    gx: 180, gv: 0,          // вратарь: где стоит и куда уже едет
    mode: "intro",           // intro | aim | lift | flight | result | over
    ax: 180, ay: LOW,
    t0: 0, flight: 0, shotAt: 0,
    verdict: "", vt: 0,
    shake: 0, net: 0,
    live: true,
  };

  function club() { return clubs[Math.min(g.i, clubs.length - 1)]; }
  function s() { return shape(club().t); }
  function lifts() { return g.i >= LIFT_FROM; }

  // ---------- картинки ----------

  function sprite(src) {
    if (!src) return null;
    let im = sprites.get(src);
    if (im === undefined) {
      im = new Image();
      im.decoding = "async";
      im.src = (opts.base || "") + src;
      im.onerror = () => sprites.set(src, null);
      sprites.set(src, im);
    }
    return im && im.complete && im.naturalWidth ? im : null;
  }
  function warm(i) { const c = clubs[i]; if (c) sprite(c.goalie); }

  // ---------- рисование ----------

  function resize() {
    const dpr = Math.min(global.devicePixelRatio || 1, 2.5);
    const w = el.wrap.clientWidth || W;
    const h = Math.round(w * H / W);
    el.canvas.style.height = h + "px";
    el.canvas.width = Math.round(w * dpr);
    el.canvas.height = Math.round(h * dpr);
    C = palette(root);
    draw();
  }

  function roundRect(x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function drawIce() {
    ctx.fillStyle = C.paper;
    ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = C.mist;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(180, 326, 60, 0, Math.PI * 2);           // точка вбрасывания
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(180, GLINE, 74, 0, Math.PI, false);      // площадь вратаря
    ctx.stroke();
    ctx.strokeStyle = C.sel;
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(8, GLINE);
    ctx.lineTo(352, GLINE);
    ctx.stroke();
  }

  function drawNet() {
    ctx.save();
    ctx.beginPath();
    ctx.rect(GX0, GTOP, GX1 - GX0, GLINE - GTOP);
    ctx.clip();
    ctx.strokeStyle = C.mist;
    ctx.lineWidth = 1;
    const wob = g.net > 0 ? Math.sin(g.net * 20) * 4 * g.net : 0;
    for (let x = GX0; x <= GX1; x += 16) {
      ctx.beginPath();
      ctx.moveTo(x, GTOP);
      ctx.lineTo(x + wob, GLINE);
      ctx.stroke();
    }
    for (let y = GTOP; y <= GLINE; y += 16) {
      ctx.beginPath();
      ctx.moveTo(GX0, y);
      ctx.lineTo(GX1, y + wob * 0.4);
      ctx.stroke();
    }
    ctx.restore();
  }

  function drawFrame() {
    ctx.strokeStyle = C.ink;
    ctx.lineWidth = 7;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(GX0, GLINE);
    ctx.lineTo(GX0, GTOP);
    ctx.lineTo(GX1, GTOP);
    ctx.lineTo(GX1, GLINE);
    ctx.stroke();
  }

  // Вратарь: щитки — скруглённый прямоугольник в цветах клуба, голова — наклейка клуба,
  // ловушка и блин по сторонам. Что видно, то и ловит: формы совпадают с covered().
  function drawGoalie(sh, gx, gv) {
    const cols = club().colors || [];
    const mid = sh.top + sh.bodyH * 0.45;
    const head = 28;
    ctx.lineWidth = 3;
    ctx.strokeStyle = C.ink;
    ctx.lineJoin = "round";
    ctx.fillStyle = cols[1] || C.lavender;            // ловушка: верх своей стороны
    roundRect(gx + sh.halfW, sh.top - sh.glove * 0.5, sh.glove, mid - sh.top + sh.glove * 0.5, 10);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = cols[0] || C.blue;                // блин: низ своей стороны
    roundRect(gx - sh.halfW - sh.block, mid, sh.block, GLINE - mid, 8);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = cols[0] || C.blue;                // щитки
    roundRect(gx - sh.halfW, sh.top, sh.halfW * 2, GLINE - sh.top, 18);
    ctx.fill();
    ctx.stroke();
    ctx.strokeStyle = C.ink;                          // пояс: видно, где кончается ловушка
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(gx - sh.halfW, mid);
    ctx.lineTo(gx + sh.halfW, mid);
    ctx.stroke();
    const im = sprite(club().goalie);                 // голова — наклейка клуба
    const hy = sh.top - head * 0.62;
    ctx.save();
    ctx.beginPath();
    ctx.arc(gx, hy, head, 0, Math.PI * 2);
    ctx.closePath();
    ctx.fillStyle = C.paper;
    ctx.fill();
    ctx.clip();
    if (im) ctx.drawImage(im, gx - head, hy - head, head * 2, head * 2);
    ctx.restore();
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.arc(gx, hy, head, 0, Math.PI * 2);
    ctx.stroke();
    if (Math.abs(gv) > HOLE_OPEN * sh.vmax) {         // «домик» видно: щель между щитками
      ctx.strokeStyle = C.ember;
      ctx.setLineDash([5, 4]);
      ctx.beginPath();
      ctx.moveTo(gx - sh.hole / 2, GLINE - 3);
      ctx.lineTo(gx - sh.hole / 2, GLINE - sh.bodyH * 0.32);
      ctx.moveTo(gx + sh.hole / 2, GLINE - 3);
      ctx.lineTo(gx + sh.hole / 2, GLINE - sh.bodyH * 0.32);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  function drawShooter() {
    const im = sprite(opts.skater);
    const r = 32;
    ctx.save();
    ctx.beginPath();
    ctx.arc(SHOT_X, SHOT_Y, r, 0, Math.PI * 2);
    ctx.closePath();
    ctx.fillStyle = C.paper;
    ctx.fill();
    ctx.clip();
    if (im) ctx.drawImage(im, SHOT_X - r, SHOT_Y - r - 4, r * 2, r * 2);
    ctx.restore();
    ctx.strokeStyle = C.ink;
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.arc(SHOT_X, SHOT_Y, r, 0, Math.PI * 2);
    ctx.stroke();
  }

  function drawPuck(x, y, r) {
    ctx.fillStyle = C.ink;
    ctx.beginPath();
    ctx.ellipse(x, y, r, r * 0.74, 0, 0, Math.PI * 2);
    ctx.fill();
  }

  function drawAim() {
    ctx.strokeStyle = C.ember;
    ctx.lineWidth = 3;
    ctx.setLineDash([7, 6]);
    ctx.beginPath();
    ctx.moveTo(g.ax, GTOP - 14);
    ctx.lineTo(g.ax, GLINE + 12);
    ctx.stroke();
    if (g.mode === "lift") {
      ctx.beginPath();
      ctx.moveTo(GX0 - 24, g.ay);
      ctx.lineTo(g.ax, g.ay);
      ctx.stroke();
    }
    ctx.setLineDash([]);
    drawPuck(g.ax, g.mode === "lift" ? g.ay : GLINE + 12, PUCK * 0.8);
  }

  function draw() {
    const w = el.canvas.width;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, w, el.canvas.height);
    const k = w / W;
    const sx = g.shake > 0 ? Math.sin(g.shake * 40) * g.shake * 6 * k : 0;
    ctx.setTransform(k, 0, 0, k, sx, 0);
    const sh = s();
    drawIce();
    drawNet();
    drawGoalie(sh, g.gx, g.gv);
    drawFrame();
    if (g.mode === "aim" || g.mode === "lift") drawAim();
    if (g.mode === "flight") {
      const p = clamp((performance.now() - g.t0) / g.flight, 0, 1);
      drawPuck(lerp(SHOT_X, g.ax, p), lerp(SHOT_Y, g.ay, p), lerp(PUCK, PUCK * 0.7, p));
    }
    if (g.mode === "result" && g.verdict === "goal") drawPuck(g.ax, g.ay, PUCK * 0.7);
    drawShooter();
    if (g.mode === "result") verdict();
  }

  function verdict() {
    const text = { goal: "ГОЛ", save: "ПОЙМАЛ", post: "ШТАНГА" }[g.verdict];
    const age = clamp((performance.now() - g.vt) / 240, 0, 1);
    const pop = calm ? 1 : 1 + 0.16 * Math.sin(age * Math.PI);
    ctx.save();
    ctx.translate(180, 288);
    ctx.scale(pop, pop);
    ctx.font = '800 32px Unbounded, "Arial Black", system-ui, sans-serif';
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    const w = ctx.measureText(text).width + 34;
    ctx.fillStyle = g.verdict === "goal" ? C.mint : C.paper;
    ctx.strokeStyle = C.ink;
    ctx.lineWidth = 3;
    roundRect(-w / 2, -26, w, 52, 24);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = g.verdict === "goal" ? "#000" : C.ink;
    ctx.fillText(text, 0, 2);
    ctx.restore();
  }

  // ---------- ход игры ----------

  function hud() {
    const c = club();
    el.count.textContent = `${Math.min(g.i + 1, clubs.length)} / ${clubs.length}`;
    el.name.textContent = c.name;
    el.club.style.setProperty("--club", (c.colors || [])[0] || "var(--blue)");
    el.dots.innerHTML = "";
    for (let i = 0; i < SHOTS; i++) {
      const d = document.createElement("i");
      d.className = "bl-dot" + (g.shots[i] ? " is-" + g.shots[i] : i === g.shot ? " is-now" : "");
      el.dots.appendChild(d);
    }
    el.road.innerHTML = "";
    for (let i = 0; i < clubs.length; i++) {
      const d = document.createElement("i");
      d.className = "bl-tick" + (i < g.i ? " is-done" : i === g.i ? " is-now" : "");
      el.road.appendChild(d);
    }
  }

  function hint(text) { el.hint.textContent = text; }

  function intro() {
    g.mode = "intro";
    g.shot = 0;
    g.shots = [];
    hud();
    warm(g.i + 1);
    hint("");
    el.wrap.classList.add("is-intro");
    clearTimeout(introTimer);
    introTimer = setTimeout(skipIntro, calm ? 60 : 540);
  }

  function skipIntro() {
    if (g.mode !== "intro") return;
    clearTimeout(introTimer);
    el.wrap.classList.remove("is-intro");
    aim();
  }

  function aim() {
    g.mode = "aim";
    hud();
    hint(g.i === 0 ? "Тапни, когда прицел будет там, куда бьёшь"
      : g.i === LIFT_FROM ? "Теперь два тапа: угол, потом высота"
        : lifts() ? "Угол" : "");
  }

  function tap() {
    if (!g.live) return;
    if (g.mode === "intro") return skipIntro();     // нетерпеливому не ждать выезда клуба
    if (g.mode === "aim") {
      if (lifts()) {
        g.mode = "lift";
        g.liftPhase = 0;
        hint("Высота");
        haptic("light");
        return;
      }
      g.ay = LOW;
      return fire();
    }
    if (g.mode === "lift") return fire();
  }

  function fire() {
    g.mode = "flight";
    g.t0 = performance.now();
    g.flight = s().flight;
    g.shotAt = 0;
    haptic("light");
    hint("");
  }

  function land() {
    const sh = s();
    let v;
    if (g.ax - GX0 < POST || GX1 - g.ax < POST || g.ay - GTOP < POST) v = "post";
    else v = covered(g.ax, g.ay, g.gx, g.gv, sh) ? "save" : "goal";
    g.verdict = v;
    g.vt = performance.now();
    g.mode = "result";
    g.shots[g.shot] = v;
    g.shot += 1;
    hud();
    if (v === "goal") {
      g.net = 1;
      g.shake = calm ? 0 : 0.5;
      haptic("success");
      if (opts.onGoal) opts.onGoal(club(), g.i);
    } else {
      haptic("error");
    }
    hint(v === "post" ? "Штанга" : "");
    setTimeout(next, v === "goal" ? (calm ? 220 : 760) : (calm ? 180 : 600));
  }

  function next() {
    if (!g.live) return;
    if (g.verdict === "goal") {
      g.i += 1;
      if (g.i >= clubs.length) return over(true);
      return intro();
    }
    if (g.shot >= SHOTS) return over(false);
    aim();
  }

  function over(won) {
    g.mode = "over";
    const passed = g.i;
    if (passed > g.best) {
      g.best = passed;
      if (opts.onRecord) opts.onRecord(passed);
    }
    el.over.hidden = false;
    el.over.innerHTML = opts.overHTML(passed, clubs.length, won ? null : club(), g.best);
    const again = el.over.querySelector("[data-again]");
    if (again) again.focus({ preventScroll: true });
    hint("");
  }

  function restart() {
    el.over.hidden = true;
    el.over.innerHTML = "";
    g.i = 0;
    g.verdict = "";
    g.gv = 0;
    intro();
  }

  // ---------- цикл ----------

  let last = 0, raf = 0, introTimer = 0;

  function frame(now) {
    if (!g.live) return;
    const dt = Math.min((now - (last || now)) / 1000, 0.05);
    last = now;
    const sh = s();
    if (g.mode !== "over") {
      g.phase += sh.patrol * dt;
      // на броске вратарь сперва едет по-старому, потом бросается к шайбе: инерция остаётся
      const flown = g.mode === "flight" ? (now - g.t0) / 1000 : 0;
      const target = g.mode === "flight" && flown >= sh.delay ? g.ax : patrolX(g.phase, patrolAmp(sh));
      const step = move(g.gx, g.gv, target, sh, dt);
      g.gx = clamp(step[0], GX0 + sh.halfW, GX1 - sh.halfW);
      g.gv = step[1];
    }
    if (g.mode === "aim") {
      g.aimPhase += sh.aim * dt;
      g.ax = 180 + ((GX1 - GX0) / 2 - 8) * Math.sin(g.aimPhase);
    }
    if (g.mode === "lift") {
      g.liftPhase += sh.lift * dt;
      const top = GTOP + 12, bottom = LOW;
      g.ay = bottom - (bottom - top) * (0.5 - 0.5 * Math.cos(g.liftPhase));
    }
    if (g.mode === "flight" && now - g.t0 >= g.flight) land();
    if (g.net > 0) g.net = Math.max(0, g.net - dt * 2.2);
    if (g.shake > 0) g.shake = Math.max(0, g.shake - dt * 2.4);
    draw();
    raf = requestAnimationFrame(frame);
  }

  // ---------- вход ----------

  function onPointer(e) {
    if (g.mode === "over") return;
    e.preventDefault();
    tap();
  }
  function onKey(e) {
    if ((e.key !== " " && e.key !== "Enter") || g.mode === "over") return;
    e.preventDefault();
    tap();
  }
  function onVisible() {
    if (document.hidden) { g.live = false; cancelAnimationFrame(raf); }
    else if (!g.live && g.mode !== "over") { g.live = true; last = 0; raf = requestAnimationFrame(frame); }
  }

  el.wrap.addEventListener("pointerdown", onPointer);
  el.wrap.addEventListener("keydown", onKey);
  el.over.addEventListener("click", (e) => {
    if (e.target.closest("[data-again]")) return restart();
    if (e.target.closest("[data-share]") && opts.onShare) opts.onShare(g.best, clubs.length, club());
  });
  document.addEventListener("visibilitychange", onVisible);
  const ro = global.ResizeObserver ? new ResizeObserver(resize) : null;
  if (ro) ro.observe(el.wrap); else global.addEventListener("resize", resize);
  const mo = new MutationObserver(() => { C = palette(root); });
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class"] });

  clubs.slice(0, 2).forEach((c) => sprite(c.goalie));
  sprite(opts.skater);
  resize();
  intro();
  raf = requestAnimationFrame(frame);

  return {
    destroy() {
      g.live = false;
      cancelAnimationFrame(raf);
      clearTimeout(introTimer);
      if (ro) ro.disconnect(); else global.removeEventListener("resize", resize);
      mo.disconnect();
      document.removeEventListener("visibilitychange", onVisible);
    },
  };
}

global.Bullit = { Game, shape, covered, move, predict, patrolX, patrolAmp, TUNE, SHOTS, LIFT_FROM,
  HOLE_OPEN, W, H, LOW, BOX: { GX0, GX1, GTOP, GLINE, POST } };

})(window);

// ---------- связь с мини-аппом ----------

// Ниже движок встречается с приложением: здесь можно звать общие функции app.js
// (esc, showSheet, team, cloud, lsGet/lsSet) — но только из отрисовки и обработчиков.

const BL_REC_KEY = "bl_best";        // лучший поход: на устройстве и в облаке Telegram
let blData = null;                   // data/bullit.json
let blLoading = null;
let blFailed = false;
let blGame = null;
let blBest = 0;

function blLoad() {
  if (!blLoading) {
    blFailed = false;
    blLoading = fetch("data/bullit.json")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.status))))
      .then((d) => (blData = d))
      .catch(() => { blLoading = null; blFailed = true; return null; });
  }
  return blLoading;
}

function blReadBest() {
  const local = parseInt(lsGet(BL_REC_KEY) || "0", 10);
  blBest = Number.isFinite(local) ? local : 0;
  if (cloud()) cloud().getItem(BL_REC_KEY, (err, v) => {
    const n = parseInt(v || "0", 10);
    if (!err && Number.isFinite(n) && n > blBest) blBest = n;
  });
}

function blSaveBest(n) {
  blBest = n;
  lsSet(BL_REC_KEY, String(n));
  if (cloud()) cloud().setItem(BL_REC_KEY, String(n), () => {});
}

// Карточка входа на «Главной»: под табло, а в день матча — с сегодняшним соперником
function blCardHTML() {
  const best = blBest;
  const sub = best ? `Лучший поход — ${best} ${plural(best, "ворота", "ворот", "ворот")} из 26`
    : "Три буллита на клуб. Пройди все 26 ворот лиги";
  return `<div class="label">Игра</div>
    <button type="button" class="board-card tap bl-card" data-bullit role="button">
      <span class="bl-card-t"><b>Буллит</b><span>${esc(sub)}</span></span>
      <span class="bl-card-n">${best || "⚡"}</span>
    </button>`;
}

function blOver(passed, total, stopper, best) {
  const won = !stopper;
  const title = won ? "Вся лига" : `${passed}`;
  const stop = won ? "Все 26 ворот пройдены" : stopper ? `Остановил «${esc(stopper.name)}»` : "";
  const rec = best > passed ? `Лучший поход — ${best}` : passed && !won ? "Это твой лучший поход" : "";
  return `<div class="bl-big">${esc(title)}</div>
    <div class="bl-of">${won ? "26 из 26" : `из ${total} ворот`}</div>
    <div class="bl-stop">${stop}</div>
    ${rec ? `<div class="bl-rec">${esc(rec)}</div>` : ""}
    <button type="button" class="btn" data-again>Ещё раз</button>
    <button type="button" class="btn ghost" data-share>Отправить в чат</button>`;
}

function blShare(best, total, stopper) {
  const text = best >= total ? `Прошёл все ${total} ворот РХЛ в «Буллите»`
    : `Прошёл ${best} ${plural(best, "ворота", "ворот", "ворот")} из ${total} в «Буллите»${stopper ? `. Остановил «${stopper.name}»` : ""}`;
  const url = `https://t.me/share/url?url=${encodeURIComponent(location.origin + location.pathname)}&text=${encodeURIComponent(text)}`;
  if (inTelegram && tg.openTelegramLink) tg.openTelegramLink(url);
  else window.open(url, "_blank", "noopener");
}

function blOpen() {
  blLoad().then(() => {
    if (!blData) {
      showSheet(`<div class="grab"></div><div class="sheet-head"><span class="when">Буллит</span>
        <button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
        <div class="empty">Игра не загрузилась. Попробуй ещё раз</div>`);
      return;
    }
    showSheet(`<div class="grab"></div>
      <div class="sheet-head"><span class="when">Буллит</span>
      <button class="btn-round" data-close aria-label="Закрыть">${ICON.close}</button></div>
      <div class="bl">
        <div class="bl-head"><span class="bl-club"><span class="bl-name"></span></span><span class="bl-count"></span>
          <span class="bl-dots" role="img" aria-label="Буллиты на этих воротах"></span></div>
        <div class="bl-road" role="img" aria-label="Пройденные ворота"></div>
        <div class="bl-play" tabindex="0" role="application" aria-label="Буллит: тапни, чтобы бросить">
          <canvas class="bl-ice"></canvas>
          <div class="bl-over" hidden></div>
        </div>
        <p class="bl-hint"></p>
      </div>`);
    const root = document.querySelector("#sheet .bl");
    const fav = state.fav;
    const clubs = blData.clubs.slice();
    const todayOpp = blTodayOpponent();
    if (todayOpp) {   // в день матча первым выходит сегодняшний соперник
      const i = clubs.findIndex((c) => c.id === todayOpp);
      if (i > 0) clubs.unshift(clubs.splice(i, 1)[0]);
    }
    if (blGame) blGame.destroy();
    blGame = Bullit.Game(root, {
      clubs,
      record: blBest,
      skater: fav ? `players/clubs/${fav}-skater.webp` : "players/skater.webp",
      overHTML: blOver,
      onRecord: blSaveBest,
      onShare: blShare,
      haptic: (kind) => {
        if (!inTelegram || !tg.HapticFeedback) return;
        if (kind === "light") tg.HapticFeedback.impactOccurred("light");
        else tg.HapticFeedback.notificationOccurred(kind === "success" ? "success" : "warning");
      },
    });
  });
}

// Соперник сегодняшнего матча любимой команды — чтобы поход начался с него
function blTodayOpponent() {
  const me = state.fav;
  if (!me || !state.data) return null;
  const today = todayISO();
  const g = (state.data.games || []).find((x) => x.date === today && (x.home === me || x.away === me));
  return g ? (g.home === me ? g.away : g.home) : null;
}

function blClosed() {   // лист закрыли — цикл останавливается, канвас не греет телефон
  if (blGame) { blGame.destroy(); blGame = null; }
}

window.blCardHTML = blCardHTML;
window.blOpen = blOpen;
window.blClosed = blClosed;
window.blReadBest = blReadBest;

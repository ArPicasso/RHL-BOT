// Замер сложности «Буллита» (ADR-017): гоняет движок без браузера и считает, сколько забивает
// случайный тап и сколько — расчётливый игрок. По этим числам настраивается TUNE в webapp/bullit.js.
//
//   node tools/bullit_balance.js            # кривая по всем 26 воротам
//   node tools/bullit_balance.js --short    # ворота 1, 6, 13, 20, 26
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(ROOT, "webapp", "bullit.js"), "utf8")
  .split("// ---------- связь с мини-аппом ----------")[0];
new Function("window", src)(global.window = {});
const B = window.Bullit;
const { GX0, GX1, GTOP, GLINE, POST } = B.BOX;
const AIM_AMP = (GX1 - GX0) / 2 - 8;
const STEP = 1 / 240;

const clubs = JSON.parse(fs.readFileSync(path.join(ROOT, "webapp", "data", "bullit.json"), "utf8")).clubs;

// Вратарь ходит по дорожке tapT секунд: где он и куда едет к моменту тапа
function atTap(s, phase0, tapT) {
  const amp = B.patrolAmp(s);
  let x = B.patrolX(phase0, amp), v = 0, t = 0, phase = phase0;
  while (t < tapT) {
    const dt = Math.min(STEP, tapT - t);
    t += dt;
    phase += s.patrol * dt;
    [x, v] = B.move(x, v, B.patrolX(phase, amp), s, dt);
  }
  return { x, v, phase };
}

function shot(s, st, ax, ay) {
  if (ax - GX0 < POST || GX1 - ax < POST || ay - GTOP < POST) return "post";
  const amp = B.patrolAmp(s);
  const [gx, gv] = B.predict(st.x, st.v, (t) => B.patrolX(st.phase + s.patrol * t, amp), ax, s, s.flight / 1000);
  return B.covered(ax, ay, gx, gv, s) ? "save" : "goal";
}

const aimAt = (s, t) => 180 + AIM_AMP * Math.sin(s.aim * t);
const heights = (s, lifts) => (lifts ? [B.LOW, GLINE - 52, GTOP + 16, GTOP + 34] : [B.LOW]);

// Случайный тап: палец без замысла
function random(s, lifts, n = 1500) {
  let goals = 0;
  for (let i = 0; i < n; i++) {
    const tapT = 0.25 + ((i * 0.233) % 1.9);
    const st = atTap(s, (i * 0.911) % 6.283, tapT);
    const ay = heights(s, lifts)[i % heights(s, lifts).length];
    if (shot(s, st, aimAt(s, tapT), ay) === "goal") goals++;
  }
  return goals / n;
}

// Расчётливый: ищет момент в ближайшие 2,2 с и бьёт с дрожью пальца ±55 мс
function skilled(s, lifts, jitter = 0.055, n = 220) {
  let goals = 0;
  for (let i = 0; i < n; i++) {
    const phase0 = (i * 0.731) % 6.283;
    const states = [];
    for (let k = 0; k <= 220; k++) states.push(atTap(s, phase0, 0.2 + k * 0.01));
    let best = null;
    for (let k = 0; k <= 220; k++) {
      const t = 0.2 + k * 0.01;
      for (const ay of heights(s, lifts)) {
        let ok = 0;
        for (const d of [-jitter, -jitter / 2, 0, jitter / 2, jitter]) {
          const kk = Math.max(0, Math.min(220, Math.round((t + d - 0.2) / 0.01)));
          if (shot(s, states[kk], aimAt(s, t + d), ay) === "goal") ok++;
        }
        if (!best || ok > best.ok) best = { ok, t, ay, k };
      }
    }
    const d = (((i % 11) - 5) / 5) * jitter;
    const kk = Math.max(0, Math.min(220, Math.round((best.t + d - 0.2) / 0.01)));
    if (shot(s, states[kk], aimAt(s, best.t + d), best.ay) === "goal") goals++;
  }
  return goals / n;
}

const pass = (p) => 1 - Math.pow(1 - p, B.SHOTS);
const pct = (x) => (x * 100).toFixed(0).padStart(3) + "%";

const short = process.argv.includes("--short");
let expected = 0, survive = 1;
const rows = clubs.map((c, i) => {
  const s = B.shape(c.t);
  const lifts = i >= B.LIFT_FROM;
  const r = random(s, lifts), k = skilled(s, lifts);
  const ps = pass(k);
  expected += survive * ps;
  survive *= ps;
  return { i, c, r, k, pr: pass(r), ps, survive };
});
console.log("ворота                     случайный  за 3 буллита   расчётливый  за 3 буллита");
rows.filter((r) => !short || [0, 5, 12, 19, 25].includes(r.i)).forEach((r) => {
  console.log(`${String(r.i + 1).padStart(2)}. ${r.c.name.padEnd(21)} ${pct(r.r)}       ${pct(r.pr)}          ${pct(r.k)}       ${pct(r.ps)}`);
});
const half = rows.find((r) => r.survive < 0.5);
console.log(`\nрасчётливый проходит в среднем ${expected.toFixed(1)} ворот, до 26-х доходит ${(survive * 100).toFixed(1)}%`);
console.log(`половина походов кончается на ${half ? half.i + 1 : 26}-х воротах`);

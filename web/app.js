const $ = (sel) => document.querySelector(sel);
const main = $("#main");
const TABS = ["home", "runs", "charts"];
const WEEKDAYS = "日月火水木金土";
const STALE_DAYS = 3; // これ以上データが届いていなければ注意を出す

// ---------- 状態 ----------

let view = null;       // home | runs | charts | run | settings
let lastTab = "home";  // 詳細・設定から戻る先
let today = null;
let goals = {};
let lastIngest = null;
let runs = [];         // 一覧用の軽い形 (新しい順)
let vapidPublicKey = null;
const details = new Map(); // ランの詳細 (地図・1km ごと) のキャッシュ

// ---------- 表示の部品 ----------

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

const pad2 = (n) => String(n).padStart(2, "0");
// 1km あたりの秒数 → 6:42
const fmtPace = (s) => (s > 0 && Number.isFinite(s) ? `${Math.floor(Math.round(s) / 60)}:${pad2(Math.round(s) % 60)}` : "–");
// 秒 → 1:09:50 / 49:08
function fmtDur(s) {
  if (!(s >= 0)) return "–";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}:${pad2(m)}:${pad2(sec)}` : `${m}:${pad2(sec)}`;
}
const fmtKm = (v, digits = 1) => (Number.isFinite(v) ? v.toFixed(digits) : "–");

function dateLabel(d, withYear = false) {
  const [y, m, dd] = d.split("-").map(Number);
  const w = WEEKDAYS[new Date(Date.UTC(y, m - 1, dd)).getUTCDay()];
  return `${withYear ? `${y}年` : ""}${m}月${dd}日（${w}）`;
}
const md = (d) => d.slice(5).split("-").map(Number).join("/"); // 2026-10-04 → 10/4
const monthLabel = (m) => `${Number(m.slice(0, 4))}年${Number(m.slice(5))}月`;
const timeOf = (iso) => iso.slice(11, 16);

const shiftDate = (d, days) => {
  const t = new Date(d + "T00:00:00Z");
  t.setUTCDate(t.getUTCDate() + days);
  return t.toISOString().slice(0, 10);
};
const shiftMonth = (m, n) => {
  const [y, mm] = m.split("-").map(Number);
  const t = new Date(Date.UTC(y, mm - 1 + n, 1));
  return t.toISOString().slice(0, 7);
};
const daysInMonth = (m) => new Date(Date.UTC(Number(m.slice(0, 4)), Number(m.slice(5)), 0)).getUTCDate();
// その週の月曜日
const weekStart = (d) => shiftDate(d, -((new Date(d + "T00:00:00Z").getUTCDay() + 6) % 7));

// 月の目標。決めていない月は、その前で最後に決めた目標を使う
function goalFor(month) {
  const past = Object.keys(goals).filter((m) => m <= month).sort();
  return past.length ? goals[past[past.length - 1]] : null;
}

// 集計に使うラン (「集計から外す」にしたものを除く)
const counted = () => runs.filter((r) => !r.user_excluded);

function sum(list) {
  const km = list.reduce((s, r) => s + r.distance_km, 0);
  const moving = list.reduce((s, r) => s + r.moving_s, 0);
  return { km, count: list.length, pace: km > 0 ? moving / km : null };
}

class LoginRequired extends Error {}

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, body === undefined
    ? { method }
    : { method, headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (res.status === 401 && path !== "/api/login") throw new LoginRequired();
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json();
}

function emptyHtml(title, sub = "") {
  return `<p class="empty"><strong>${esc(title)}</strong>${esc(sub)}</p>`;
}

function setHeader(title, { back = false } = {}) {
  $("#title").textContent = title;
  $("#back").hidden = !back;
  $("#open-settings").hidden = back;
}

// ---------- 合言葉 ----------

function renderLogin(message = "") {
  $("#tabs").hidden = true;
  setHeader("ランログ");
  $("#open-settings").hidden = true;
  main.innerHTML = `<form class="login" id="login">
    <p class="login-lead">合言葉を入力してください</p>
    <input type="password" id="pass" autocomplete="current-password" autocapitalize="off" autocorrect="off"
      spellcheck="false" placeholder="合言葉" required>
    <label class="show-pass"><input type="checkbox" id="show-pass"> 合言葉を表示する</label>
    <button class="btn" type="submit">開く</button>
    <p class="login-msg" id="login-msg">${esc(message)}</p>
  </form>`;
  $("#show-pass").addEventListener("change", (e) => { $("#pass").type = e.target.checked ? "text" : "password"; });
  $("#login").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector("button");
    btn.disabled = true;
    try {
      await api("/api/login", { method: "POST", body: { passphrase: $("#pass").value } });
      start();
    } catch {
      $("#login-msg").textContent = "合言葉が違います";
      btn.disabled = false;
    }
  });
}

// ---------- ランの行 ----------

function runRow(r) {
  const badge = r.user_excluded ? `<span class="badge off">集計外</span>` : r.flags?.length ? `<span class="badge">要確認</span>` : "";
  return `<li><button class="run-row${r.user_excluded ? " off" : ""}" data-run="${esc(r.id)}">
    <span class="d">${md(r.date)}（${WEEKDAYS[new Date(r.date + "T00:00:00Z").getUTCDay()]}）<small>${timeOf(r.start)}</small></span>
    <span><span class="km">${fmtKm(r.distance_km, 2)}<small>km</small></span>${badge}</span>
    <span class="meta">${fmtPace(r.pace_s)} /km<small>${fmtDur(r.moving_s)}${r.avg_hr ? ` · ♥${r.avg_hr}` : ""}</small></span>
  </button></li>`;
}

// データが届いていないときの注意 (ホームと設定で使う)
function staleCard() {
  if (!lastIngest) {
    return `<div class="card warn"><h2>まだデータが届いていません</h2>
      <p>iPhone の Health Auto Export から、ランログにデータを送る設定をしてください。</p>
      <button class="link-btn" data-go="settings">設定のしかたを見る ›</button></div>`;
  }
  const days = (Date.now() - Date.parse(lastIngest.at)) / 86400000;
  if (days < STALE_DAYS) return "";
  return `<div class="card warn"><h2>${Math.floor(days)}日間データが届いていません</h2>
    <p>Health Auto Export の自動送信が止まっているかもしれません。アプリを開いて、手動で送ってみてください。</p>
    <button class="link-btn" data-go="settings">設定を確認する ›</button></div>`;
}

// ---------- ホーム ----------

const LONG_RUN_LIMIT = 1.1; // 次のロング走は、過去30日の最長のこの倍まで (docs/coach-guidelines.md の R3)

// 次のロング走の安全な上限。「要確認」と「集計から外す」のランは距離があてにならないので除く
function longRunLine() {
  const since = shiftDate(today, -30);
  const longest = Math.max(0, ...counted().filter((r) => r.date >= since && !r.flags?.length).map((r) => r.distance_km));
  if (!longest) return `<li><span>この30日ほど走っていません</span><b>短い距離から再開</b></li>`;
  return `<li><span>次のロング走の上限<small>過去30日の最長 ${fmtKm(longest)} km × 1.1</small></span>
    <b>${fmtKm(longest * LONG_RUN_LIMIT)} km まで</b></li>`;
}

const LONG_RUN_HINT = `<p class="hint">1回の距離が過去30日の最長の1.1倍を超えると、けがのリスクが上がります。遅れていても詰め込まずに。</p>`;

const EASY_HR = 0.75; // 楽な強度の上限 (最大心拍に対する割合。docs/coach-guidelines.md の R1)

// 最大心拍。記録の最高値と推定式 (208 - 0.7 × 年齢) のうち高いほう。年齢は分からないので記録の最高値を使い、
// 記録がなければ 185 とみなす (R2)
const maxHr = () => Math.max(0, ...counted().map((r) => r.max_hr || 0)) || 185;

// 今日のおすすめ。第2弾の AI ができるまでは、前回のランからの間隔と距離だけで決める簡単なルール
function todayPlan() {
  const mine = counted().filter((r) => !r.flags?.length);
  const last = mine[0];
  const easyHr = Math.round(maxHr() * EASY_HR);
  if (!last) return { title: "まずは 20分", note: "会話できるくらいの楽なペースで。歩きを混ぜても大丈夫です。" };
  const gap = Math.round((Date.parse(today) - Date.parse(last.date)) / 86400000);
  if (gap === 0) return { title: "今日は走りました", note: `${fmtKm(last.distance_km)} km・${fmtPace(last.pace_s)}/km。ゆっくり休みましょう。` };
  if (gap >= 14) return { title: "短めに 20分", note: `${gap}日ぶりなので、楽なペースで体を慣らしましょう。` };
  // 前日に長く走った (過去30日の最長に近い) ときは休養をすすめる
  const since = shiftDate(today, -30);
  const longest = Math.max(0, ...mine.filter((r) => r.date >= since).map((r) => r.distance_km));
  if (gap === 1 && longest && last.distance_km >= longest * 0.9)
    return { title: "休養日", note: "昨日は長めに走ったので、今日は休むか散歩くらいに。" };
  // ふだんの時間 (直近5回の動いていた時間の中央値) を5分単位で
  const times = mine.slice(0, 5).map((r) => r.moving_s).sort((a, b) => a - b);
  const min = Math.max(20, Math.round(times[Math.floor(times.length / 2)] / 60 / 5) * 5);
  return { title: `楽に ${min}分`, note: `前回から${gap}日空き。心拍${easyHr}以下を目安に。` };
}

// 直近7日の距離 (今日まで)
function weekBars() {
  const days = Array.from({ length: 7 }, (_, i) => shiftDate(today, i - 6));
  const km = days.map((d) => counted().filter((r) => r.date === d).reduce((a, r) => a + r.distance_km, 0));
  const top = Math.max(...km, 1);
  return `<div class="card week-card"><h2>直近7日 <span class="note">${fmtKm(km.reduce((a, b) => a + b, 0))} km</span></h2>
    <div class="week-bars">${days.map((d, i) => `<div data-tip-title="${md(d)}" data-tip-value="${fmtKm(km[i])} km">
      <i class="${km[i] ? "" : "zero"}" style="height:${km[i] ? Math.max(8, (km[i] / top) * 100) : 4}%"></i>
      <span class="${d === today ? "today" : ""}">${WEEKDAYS[new Date(d + "T00:00:00Z").getUTCDay()]}</span></div>`).join("")}</div></div>`;
}

function renderHome() {
  setHeader("ランログ");
  const month = today.slice(0, 7);
  const day = Number(today.slice(8));
  const dim = daysInMonth(month);
  const m = sum(counted().filter((r) => r.date.startsWith(month)));
  const goal = goalFor(month);
  const plan = todayPlan();

  const planHtml = `<div class="card plan-card"><p class="plan-label">今日のおすすめ</p>
    <p class="plan-title">${esc(plan.title)}</p><p class="plan-note">${esc(plan.note)}</p></div>`;

  let goalHtml;
  if (goal) {
    const pct = Math.min(100, (m.km / goal) * 100);
    const expected = (goal * day) / dim; // 今日の終わりまでに走っておきたい距離
    const diff = m.km - expected;
    // 1日あたりの目安は出さない (月末の詰め込みはけがのもと)。代わりに次のロング走の安全な上限を出す
    goalHtml = `<div class="card goal-card">
      <div class="goal-head"><span>${Number(month.slice(5))}月の目標</span><b>${fmtKm(m.km)} / ${goal} km</b></div>
      <div class="meter" role="img" aria-label="達成率 ${Math.round(pct)}%"><i style="width:${pct}%"></i>
        <s style="left:calc(${Math.min(100, (expected / goal) * 100)}% - 1px)"></s></div>
      <ul class="pace-guide">${m.km >= goal
        ? `<li><span>目標達成！</span><b class="ahead">+${fmtKm(m.km - goal)} km</b></li>`
        : `<li><span>今日までの目安 ${fmtKm(expected)} km</span>
           <b class="${diff >= 0 ? "ahead" : "behind"}">${diff >= 0 ? `+${fmtKm(diff)} km 先行` : `${fmtKm(-diff)} km 遅れ`}</b></li>`}
        ${longRunLine()}</ul>${LONG_RUN_HINT}
    </div>`;
  } else {
    goalHtml = `<div class="card goal-card">
      <div class="goal-head"><span>${Number(month.slice(5))}月の距離</span><b>${fmtKm(m.km)} km</b></div>
      <ul class="pace-guide">${longRunLine()}</ul>${LONG_RUN_HINT}
      <button class="link-btn" data-go="settings">月の目標を決める ›</button></div>`;
  }

  const recent = runs.slice(0, 3);
  main.innerHTML = `${staleCard()}${planHtml}${goalHtml}${weekBars()}
    <div class="card"><h2>最近のラン${runs.length > 3 ? `<button class="link-btn" data-go="runs">すべて ›</button>` : ""}</h2>
      ${recent.length ? `<ul class="run-list">${recent.map(runRow).join("")}</ul>` : `<p class="muted">まだランがありません。</p>`}
    </div>`;
}

// ---------- 記録 (一覧) ----------

function renderRuns() {
  setHeader("記録");
  if (!runs.length) {
    main.innerHTML = emptyHtml("まだランがありません", "Apple Watch で屋外ランを記録すると、ここに並びます");
    return;
  }
  const byMonth = new Map();
  for (const r of runs) {
    const k = r.date.slice(0, 7);
    if (!byMonth.has(k)) byMonth.set(k, []);
    byMonth.get(k).push(r);
  }
  main.innerHTML = [...byMonth].map(([month, list]) => {
    const s = sum(list.filter((r) => !r.user_excluded));
    return `<div class="month-head"><h2>${monthLabel(month)}</h2><span>${fmtKm(s.km)} km · ${s.count}回</span></div>
      <ul class="run-list">${list.map(runRow).join("")}</ul>`;
  }).join("");
}

// ---------- ランの詳細 ----------

let leaflet = null;
function loadLeaflet() {
  leaflet ??= new Promise((resolve, reject) => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
    document.head.append(css);
    const js = document.createElement("script");
    js.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
    js.onload = () => resolve(window.L);
    js.onerror = () => { leaflet = null; reject(new Error("地図を読み込めませんでした")); };
    document.head.append(js);
  });
  return leaflet;
}

async function drawMap(el, route) {
  if (!route?.lat?.length) {
    el.innerHTML = `<div class="msg">ルートがありません</div>`;
    return;
  }
  let L;
  try {
    L = await loadLeaflet();
  } catch (err) {
    el.innerHTML = `<div class="msg">${esc(err.message)}</div>`;
    return;
  }
  if (!el.isConnected) return;
  const map = L.map(el, { zoomControl: false, attributionControl: true });
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  const latlngs = route.lat.map((lat, i) => [lat, route.lon[i]]);
  const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
  // 白いふちの上に色の線を重ね、どの地図の色でも見えるようにする
  L.polyline(latlngs, { color: "#fff", weight: 7, opacity: 0.9 }).addTo(map);
  const line = L.polyline(latlngs, { color: accent, weight: 4 }).addTo(map);
  L.circleMarker(latlngs[0], { radius: 6, color: "#fff", weight: 2, fillColor: "#1c7a52", fillOpacity: 1 }).addTo(map).bindTooltip("スタート");
  L.circleMarker(latlngs[latlngs.length - 1], { radius: 6, color: "#fff", weight: 2, fillColor: "#1e1d1c", fillOpacity: 1 }).addTo(map).bindTooltip("ゴール");
  map.fitBounds(line.getBounds(), { padding: [20, 20] });
}

function splitsHtml(splits) {
  if (!splits?.length) return "";
  // 1km あたりのペースに直して比べる (最後の端数の区間も同じ物差しで見る)
  const rows = splits.map((s, i) => ({ ...s, i, pace: s.sec / s.km }));
  const full = rows.filter((s) => s.km >= 0.5);
  const fastest = Math.min(...full.map((s) => s.pace));
  const slowest = Math.max(...full.map((s) => s.pace));
  const best = full.find((s) => s.pace === fastest);
  const width = (p) => (slowest > fastest ? 30 + 70 * ((slowest - p) / (slowest - fastest)) : 100);
  return `<div class="card"><h2>1kmごと <span class="note">フィットネスアプリと同じ区切り</span></h2>
    <table class="splits"><tr><th>km</th><th>ペース</th><th></th><th>心拍</th></tr>
    ${rows.map((s) => `<tr class="${s === best && full.length > 1 ? "best" : ""}">
      <td>${s.km < 0.999 ? `+${s.km.toFixed(2)}` : s.i + 1}</td>
      <td>${fmtPace(s.pace)}</td>
      <td class="bar"><i class="${s.pace > fastest * 1.25 ? "slow" : ""}" style="width:${width(s.pace)}%"></i></td>
      <td>${s.hr ?? "–"}</td></tr>`).join("")}
    </table><p class="hint">棒が長いほど速い区間。灰色は最速より25%以上遅い区間（歩き・信号待ちなど）です。</p></div>`;
}

function hrHtml(r) {
  if (!r.hr?.length) return "";
  const items = r.hr.map(([m, v]) => ({ label: `${m}分`, value: v, tip: { title: `${m}分`, value: `${v} bpm` } }));
  return `<div class="card"><h2>心拍の推移 <span class="note">1分ごとの平均</span></h2>
    ${lineChart(items, { unit: "bpm", area: true })}
    ${tableView(["経過", "心拍"], r.hr.filter((_, i) => i % 5 === 0).map(([m, v]) => [`${m}分`, `${v} bpm`]))}</div>`;
}

async function renderRun(id) {
  setHeader("ラン", { back: true });
  main.innerHTML = `<p class="empty">読み込み中…</p>`;
  let d = details.get(id);
  if (!d) {
    d = await api(`/api/runs/${encodeURIComponent(id)}`);
    details.set(id, d);
  }
  if (view !== "run") return;
  const r = d.run;
  setHeader(dateLabel(r.date, r.date.slice(0, 4) !== today.slice(0, 4)), { back: true });
  const tile = (label, value, unit = "") => `<div class="tile"><span>${label}</span><b>${value}${unit ? `<small>${unit}</small>` : ""}</b></div>`;
  const flags = r.flags?.length
    ? `<div class="card warn"><h2>要確認</h2><ul class="flags">${r.flags.map((f) => `<li>${esc(f.text)}</li>`).join("")}</ul>
        <p>おかしいと思ったら、下の「集計から外す」をオンにしてください。目標・グラフ・AI の分析に含めなくなります。</p></div>`
    : "";
  const notes = r.notes?.length ? `<div class="card info">${r.notes.map((n) => `<p>${esc(n)}</p>`).join("")}</div>` : "";
  main.innerHTML = `<div class="map" id="map"></div>
    ${flags}${notes}
    <div class="tiles">
      ${tile("距離", fmtKm(r.distance_km, 2), "km")}
      ${tile("ペース", fmtPace(r.pace_s), "/km")}
      ${tile("動いていた時間", fmtDur(r.moving_s))}
      ${tile("経過時間", fmtDur(r.duration_s))}
      ${tile("平均心拍", r.avg_hr ?? "–", "bpm")}
      ${tile("最大心拍", r.max_hr ?? "–", "bpm")}
      ${tile("獲得標高", r.elevation_up_m != null ? Math.round(r.elevation_up_m) : "–", "m")}
      ${tile("ケイデンス", r.cadence ?? "–", "spm")}
      ${tile("消費", r.kcal ?? "–", "kcal")}
    </div>
    <p class="hint" style="margin:-4px 2px 12px">${timeOf(r.start)} スタート · 止まっていた時間 ${fmtDur(r.stopped_s)}（ペースは動いていた時間で計算）
      ${r.temperature_c != null ? ` · ${Math.round(r.temperature_c)}℃` : ""}${r.humidity != null ? ` 湿度${r.humidity}%` : ""}</p>
    ${splitsHtml(r.splits)}
    ${hrHtml(r)}
    <div class="card"><div class="switch-row"><span>集計から外す<br><small class="note">目標・グラフ・AI の分析に含めない</small></span>
      <label class="switch"><input type="checkbox" id="exclude" ${r.user_excluded ? "checked" : ""}><i></i></label></div></div>`;
  $("#exclude").addEventListener("change", async (e) => {
    const excluded = e.target.checked;
    e.target.disabled = true;
    try {
      await api(`/api/runs/${encodeURIComponent(id)}`, { method: "PUT", body: { excluded } });
      r.user_excluded = excluded;
      const row = runs.find((x) => x.id === id);
      if (row) row.user_excluded = excluded;
    } catch (err) {
      if (err instanceof LoginRequired) return renderLogin();
      e.target.checked = !excluded;
      alert("保存できませんでした");
    }
    e.target.disabled = false;
  });
  drawMap($("#map"), d.route);
}

// ---------- グラフ ----------

function renderCharts() {
  setHeader("グラフ");
  const mine = counted();
  if (!mine.length) {
    main.innerHTML = emptyHtml("まだランがありません", "走ると、週・月ごとの距離やペースの推移が出ます");
    return;
  }
  // 週ごと (直近12週、月曜始まり)
  const thisWeek = weekStart(today);
  const weeks = Array.from({ length: 12 }, (_, i) => shiftDate(thisWeek, -7 * (11 - i)));
  const weekSums = weeks.map((w) => sum(mine.filter((r) => r.date >= w && r.date < shiftDate(w, 7))));
  const weekItems = weeks.map((w, i) => ({
    label: md(w), value: weekSums[i].km, dim: i === 11,
    tip: { title: `${md(w)}〜${md(shiftDate(w, 6))}の週 · ${weekSums[i].count}回`, value: `${fmtKm(weekSums[i].km)} km` },
  }));

  // 月ごと (直近12か月) と去年の同じ月
  const month = today.slice(0, 7);
  const months = Array.from({ length: 12 }, (_, i) => shiftMonth(month, -(11 - i)));
  const monthKm = (m) => sum(mine.filter((r) => r.date.startsWith(m))).km;
  const monthItems = months.map((m) => {
    const km = monthKm(m), last = monthKm(shiftMonth(m, -12));
    return {
      label: `${Number(m.slice(5))}月`, value: km, ghost: last,
      tip: { title: `${monthLabel(m)}（去年 ${fmtKm(last)} km${goalFor(m) ? `・目標 ${goalFor(m)} km` : ""}）`, value: `${fmtKm(km)} km` },
    };
  });
  const now = monthKm(month), lastYear = monthKm(shiftMonth(month, -12));
  const diff = now - lastYear;

  // ペースの推移 (週ごと、動いていた時間での平均)
  const paceItems = weeks.map((w, i) => ({
    label: md(w), value: weekSums[i].pace,
    tip: weekSums[i].pace ? { title: `${md(w)}〜の週 · ${fmtKm(weekSums[i].km)} km`, value: `${fmtPace(weekSums[i].pace)} /km` } : null,
  }));

  main.innerHTML = `
    <div class="card"><h2>週ごとの距離 <span class="note">直近12週・月曜始まり</span></h2>
      ${columnChart(weekItems)}
      ${tableView(["週", "距離", "回数"], weeks.map((w, i) => [`${md(w)}〜`, `${fmtKm(weekSums[i].km)} km`, `${weekSums[i].count}回`]).reverse())}
    </div>
    <div class="card"><h2>月ごとの距離 <span class="note">直近12か月</span></h2>
      <p class="compare"><span>今月</span><b>${fmtKm(now)} km</b>
        <span class="note">去年の${Number(month.slice(5))}月 ${fmtKm(lastYear)} km（${diff >= 0 ? "+" : "−"}${fmtKm(Math.abs(diff))} km）</span></p>
      <div class="legend"><span style="--key:var(--accent)">今年</span><span style="--key:var(--ghost)">去年の同じ月</span></div>
      ${columnChart(monthItems)}
      ${tableView(["月", "距離", "去年", "目標"], months.map((m, i) => [monthLabel(m), `${fmtKm(monthItems[i].value)} km`,
        `${fmtKm(monthItems[i].ghost)} km`, goalFor(m) ? `${goalFor(m)} km` : "–"]).reverse())}
    </div>
    <div class="card"><h2>平均ペースの推移 <span class="note">週ごと・上ほど速い</span></h2>
      ${lineChart(paceItems, { invert: true, tickFmt: fmtPace, unit: "/km" })}
      <p class="hint">止まっていた時間を除いた「動いていた時間」で計算しています。走らなかった週は点がありません。</p>
      ${tableView(["週", "ペース", "距離"], weeks.map((w, i) => [`${md(w)}〜`, `${fmtPace(weekSums[i].pace)} /km`, `${fmtKm(weekSums[i].km)} km`]).reverse())}
    </div>
    ${runs.some((r) => r.user_excluded) ? `<p class="hint">「集計から外す」にしたランは含めていません。</p>` : ""}`;
}

// ---------- 設定 ----------

function b64ToBytes(b64) {
  const s = atob(b64.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (b64.length % 4)) % 4));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}

async function pushStatus() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return "unsupported";
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  return sub ? "on" : Notification.permission === "denied" ? "denied" : "off";
}

async function enablePush() {
  const reg = await navigator.serviceWorker.ready;
  const perm = await Notification.requestPermission();
  if (perm !== "granted") return renderSettings();
  const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(vapidPublicKey) });
  await api("/api/subscribe", { method: "POST", body: { subscription: sub.toJSON() } });
  renderSettings();
}

function ingestText() {
  if (!lastIngest) return "まだ一度も届いていません。";
  const t = new Date(lastIngest.at);
  return `${t.getMonth() + 1}月${t.getDate()}日 ${pad2(t.getHours())}:${pad2(t.getMinutes())}（その回に届いたもの: 屋外ラン ${lastIngest.runs}件・健康データ ${lastIngest.metrics}件）`;
}

async function renderSettings() {
  setHeader("設定", { back: true });
  const month = today.slice(0, 7);
  const goal = goalFor(month);
  const ingestUrl = `${location.origin}/api/ingest`;
  main.innerHTML = `<div class="settings">
    <section><h2>${Number(month.slice(5))}月の目標</h2>
      <form class="goal-form" id="goal-form">
        <input type="number" id="goal" inputmode="decimal" min="1" max="2000" step="0.5" value="${goal ?? ""}" placeholder="100"> km
        <button class="btn" type="submit">保存</button><span class="hint" id="goal-msg"></span>
      </form>
      <p class="hint">来月以降も、変えるまでこの目標が続きます。</p></section>
    <section><h2>データの受け取り</h2>
      <dl class="kv"><dt>最後に届いた</dt><dd>${esc(ingestText())}</dd><dt>記録</dt><dd>屋外ラン ${runs.length}件</dd></dl>
      ${staleCard()}
      <p>iPhone の Health Auto Export で、次のとおり自動送信を設定します。</p>
      <ol class="steps">
        <li>「オートメーション」→ 新規 → 種類は <b>REST API</b></li>
        <li>URL に下の値を貼る</li>
        <li>ヘッダーに <code>Authorization</code> = <code>Bearer （HAE_TOKEN の値）</code> を追加</li>
        <li>データの種類は <b>ワークアウト</b>（ルートを含める）、形式は <b>JSON</b></li>
      </ol>
      <div class="copy-row"><input id="ingest-url" value="${esc(ingestUrl)}" readonly><button class="btn ghost" id="copy-url" type="button">コピー</button></div>
      <p class="hint">屋外ラン以外（ウォーキング・室内ラン）は届いても取り込みません。同じランが何度届いても1件として扱います。</p></section>
    <section><h2>通知</h2><p id="push-msg">確認中…</p><div id="push-action"></div></section>
    <section><h2>データ</h2>
      <p>ランの記録をすべて JSON ファイルで保存します（バックアップ用。地図のルートは含みません）。</p>
      <a class="btn ghost" href="/api/export" download>データを書き出す</a></section>
    <section><h2>この端末</h2><button class="btn ghost" id="logout">ログアウト</button></section>
  </div>`;

  $("#goal-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const raw = $("#goal").value.trim();
    const km = raw ? Number(raw) : null;
    if (km !== null && !(km > 0 && km <= 2000)) return ($("#goal-msg").textContent = "1〜2000 の数字で入力してください");
    try {
      goals = (await api("/api/goal", { method: "PUT", body: { month, km } })).goals;
      $("#goal-msg").textContent = "保存しました ✓";
    } catch (err) {
      if (err instanceof LoginRequired) return renderLogin();
      $("#goal-msg").textContent = "保存できませんでした";
    }
  });
  $("#copy-url").addEventListener("click", async (e) => {
    try {
      await navigator.clipboard.writeText(ingestUrl);
      e.target.textContent = "コピーしました";
    } catch {
      $("#ingest-url").select();
    }
  });
  $("#logout").addEventListener("click", async () => {
    await api("/api/logout", { method: "POST" }).catch(() => {});
    renderLogin();
  });

  const status = await pushStatus();
  if (view !== "settings") return;
  const msg = $("#push-msg");
  const action = $("#push-action");
  const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  const what = "月末の振り返り（毎月1日の朝）、目標から遅れているとき（日曜の夜）、データが3日届かないときにお知らせします。";
  if (status === "on") msg.textContent = `オン：${what}`;
  else if (status === "unsupported" || !standalone)
    msg.textContent = "Safari の共有ボタン →「ホーム画面に追加」で追加したアプリから通知をオンにできます。";
  else if (status === "denied") msg.textContent = "通知が拒否されています。iPhone の設定 → 通知 から許可してください。";
  else if (!vapidPublicKey) msg.textContent = "サーバー側の通知設定 (VAPID_PUBLIC_KEY) がまだです。";
  else {
    msg.textContent = what;
    action.innerHTML = `<button class="btn" id="push-on">通知をオンにする</button>`;
    $("#push-on").addEventListener("click", (e) => {
      e.target.disabled = true;
      enablePush().catch((err) => {
        msg.textContent = "通知の登録に失敗しました：" + err.message;
        e.target.disabled = false;
      });
    });
  }
}

// ---------- 画面の切り替え ----------

function updateTabs() {
  for (const b of document.querySelectorAll(".tabs button")) b.classList.toggle("active", b.dataset.tab === (TABS.includes(view) ? view : lastTab));
}

async function show(name, id = null) {
  view = name;
  if (TABS.includes(name)) lastTab = name;
  history.replaceState(null, "", "#" + (name === "run" ? `run/${id}` : name));
  updateTabs();
  window.scrollTo(0, 0);
  if (name === "home") return renderHome();
  if (name === "runs") return renderRuns();
  if (name === "charts") return renderCharts();
  if (name === "run") return renderRun(id);
  return renderSettings();
}

$("#tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]");
  if (b) guard(show(b.dataset.tab));
});
$("#back").addEventListener("click", () => guard(show(lastTab)));
$("#open-settings").addEventListener("click", () => guard(show("settings")));
main.addEventListener("click", (e) => {
  const run = e.target.closest("[data-run]");
  if (run) return guard(show("run", run.dataset.run));
  const go = e.target.closest("[data-go]");
  if (go) guard(show(go.dataset.go));
});

// 通信エラーやログイン切れをまとめて扱う
function guard(promise) {
  return Promise.resolve(promise).catch((err) => {
    if (err instanceof LoginRequired) return renderLogin();
    console.error(err);
    main.innerHTML = emptyHtml("読み込めませんでした", "電波の良い所でもう一度開いてください");
  });
}

async function loadState() {
  const s = await api("/api/state");
  ({ today, goals, vapidPublicKey } = s);
  lastIngest = s.last_ingest;
  runs = s.runs.sort((a, b) => b.start.localeCompare(a.start)); // 同じ日に2回走っても新しい順
}

// ---------- 起動 ----------

async function start() {
  const hash = location.hash.slice(1);
  await guard((async () => {
    await loadState();
    $("#tabs").hidden = false;
    if (hash.startsWith("run/")) return show("run", hash.slice(4));
    return show(TABS.includes(hash) || hash === "settings" ? hash : "home");
  })());
}

// 新しい版を配信したら、開き直したときに自動で切り替える。
// iPhone のホーム画面アプリは裏で開いたままのことが多く、読み込み直さないと古い画面が残るため
let swReg = null;
if ("serviceWorker" in navigator) {
  const hadController = !!navigator.serviceWorker.controller; // 初めて開いたときは読み込み直さない
  navigator.serviceWorker.register("sw.js").then((reg) => { swReg = reg; }).catch(() => {});
  let reloading = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (!hadController || reloading) return;
    reloading = true;
    location.reload();
  });
}

// 開き直したとき: 新しい版がないか確かめ、届いたランを読み直す
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible" || !today) return;
  swReg?.update().catch(() => {});
  if (view === "home" || view === "runs" || view === "charts") {
    loadState().then(() => { if (TABS.includes(view)) show(view); }).catch(() => {});
  }
});

start();

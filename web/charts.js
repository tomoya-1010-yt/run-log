// グラフ (SVG) とツールチップ。外部ライブラリは使わない。
// 棒・点にはそれぞれ当たり判定 (.hit) を重ね、指で触れたところの値をツールチップに出す。
// 色はすべて style.css の変数。値の文字には系列の色を使わない。

const CHART_W = 340;

function niceStep(max, count = 4) {
  if (!(max > 0)) return 1;
  const raw = max / count;
  const p = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw);
}

// x 軸のラベルが多すぎるときは間引く
const labelEvery = (n) => (n <= 8 ? 1 : n <= 16 ? 2 : n <= 24 ? 3 : Math.ceil(n / 7));

const tipAttrs = (tip) => (tip ? ` data-tip-title="${escAttr(tip.title)}" data-tip-value="${escAttr(tip.value)}"` : "");
function escAttr(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// 上端だけ角を 4px 丸めた棒 (根元は四角)
function barPath(x, y, w, h) {
  if (h <= 0) return "";
  const r = Math.min(4, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

/**
 * 縦棒グラフ。items: [{ label, value, ghost?, tip, dim? }]
 * ghost があれば、その値 (去年など) を左隣に灰色の棒で並べる。goal があれば横線を引く
 */
function columnChart(items, { height = 170, unit = "km", goal = null, valueFmt = (v) => v.toFixed(1), labelLast = true } = {}) {
  const pad = { l: 30, r: 6, t: 16, b: 20 };
  const w = CHART_W - pad.l - pad.r;
  const h = height - pad.t - pad.b;
  const max = Math.max(goal || 0, ...items.map((d) => Math.max(d.value || 0, d.ghost || 0)), 0.1);
  const step = niceStep(max);
  const top = Math.ceil(max / step) * step;
  const y = (v) => pad.t + h - (v / top) * h;
  const band = w / items.length;
  const hasGhost = items.some((d) => d.ghost != null);
  const bw = Math.min(hasGhost ? 11 : 22, band * (hasGhost ? 0.36 : 0.62));
  const every = labelEvery(items.length);
  let svg = `<svg class="chart" viewBox="0 0 ${CHART_W} ${height}" role="img">`;
  for (let v = 0; v <= top + 1e-9; v += step) {
    svg += `<line class="${v === 0 ? "axis" : "grid"}" x1="${pad.l}" x2="${CHART_W - pad.r}" y1="${y(v)}" y2="${y(v)}"/>`
      + `<text x="${pad.l - 5}" y="${y(v) + 3}" text-anchor="end">${+v.toFixed(2)}</text>`;
  }
  svg += `<text x="${pad.l - 5}" y="${pad.t - 6}" text-anchor="end">${unit}</text>`;
  if (goal) svg += `<line class="goal" x1="${pad.l}" x2="${CHART_W - pad.r}" y1="${y(goal)}" y2="${y(goal)}"/>`
    + `<text x="${CHART_W - pad.r}" y="${y(goal) - 4}" text-anchor="end">目標 ${goal}</text>`;
  items.forEach((d, i) => {
    const cx = pad.l + band * (i + 0.5);
    // 当たり判定は帯いっぱい (棒より大きく) にする
    svg += `<rect class="hit" x="${cx - band / 2}" y="${pad.t}" width="${band}" height="${h}"${tipAttrs(d.tip)}/>`;
    if (hasGhost) {
      const gx = cx - bw - 1;
      svg += `<path class="bar ghost" d="${barPath(gx, y(d.ghost || 0), bw, pad.t + h - y(d.ghost || 0))}"/>`;
      svg += `<path class="bar${d.dim ? " dim" : ""}" d="${barPath(cx + 1, y(d.value || 0), bw, pad.t + h - y(d.value || 0))}"/>`;
    } else {
      svg += `<path class="bar${d.dim ? " dim" : ""}" d="${barPath(cx - bw / 2, y(d.value || 0), bw, pad.t + h - y(d.value || 0))}"/>`;
    }
    if (i % every === (items.length - 1) % every)
      svg += `<text x="${cx}" y="${height - 6}" text-anchor="middle">${escAttr(d.label)}</text>`;
  });
  // 値の文字は最後の棒 (いま) だけに付ける
  const last = items[items.length - 1];
  if (labelLast && last && last.value > 0) {
    const cx = pad.l + band * (items.length - 0.5) + (hasGhost ? bw / 2 + 1 : 0);
    svg += `<text class="val" x="${Math.min(cx, CHART_W - 14)}" y="${y(last.value) - 4}" text-anchor="middle">${valueFmt(last.value)}</text>`;
  }
  return svg + "</svg>";
}

/**
 * 折れ線グラフ。items: [{ label, value (null は点なし), tip }]
 * invert: true なら小さい値ほど上 (ペースは速いほど上に出す)
 */
function lineChart(items, { height = 170, invert = false, tickFmt = (v) => String(v), unit = "", area = false } = {}) {
  const pad = { l: 36, r: 10, t: 16, b: 20 };
  const w = CHART_W - pad.l - pad.r;
  const h = height - pad.t - pad.b;
  const vals = items.map((d) => d.value).filter((v) => v != null);
  if (!vals.length) return `<p class="muted">データがありません</p>`;
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
  const step = niceStep(hi - lo, 3);
  lo = Math.floor(lo / step) * step;
  hi = Math.ceil(hi / step) * step;
  const y = (v) => pad.t + (invert ? (v - lo) / (hi - lo) : (hi - v) / (hi - lo)) * h;
  const band = w / Math.max(items.length, 1);
  const x = (i) => pad.l + band * (i + 0.5);
  const every = labelEvery(items.length);
  let svg = `<svg class="chart" viewBox="0 0 ${CHART_W} ${height}" role="img">`;
  for (let v = lo; v <= hi + 1e-9; v += step)
    svg += `<line class="grid" x1="${pad.l}" x2="${CHART_W - pad.r}" y1="${y(v)}" y2="${y(v)}"/>`
      + `<text x="${pad.l - 5}" y="${y(v) + 3}" text-anchor="end">${tickFmt(v)}</text>`;
  if (unit) svg += `<text x="${pad.l - 5}" y="${pad.t - 6}" text-anchor="end">${unit}</text>`;
  // 値のない点で線を切る
  const segs = [];
  let cur = [];
  items.forEach((d, i) => {
    if (d.value == null) { if (cur.length) segs.push(cur); cur = []; } else cur.push([x(i), y(d.value)]);
  });
  if (cur.length) segs.push(cur);
  for (const s of segs) {
    if (area && s.length > 1) {
      const base = invert ? pad.t : pad.t + h;
      svg += `<path class="area" d="M${s[0][0]},${base}L${s.map((p) => p.join(",")).join("L")}L${s[s.length - 1][0]},${base}Z"/>`;
    }
    if (s.length > 1) svg += `<path class="line" d="M${s.map((p) => p.join(",")).join("L")}"/>`;
  }
  items.forEach((d, i) => {
    svg += `<g class="pt"><rect class="hit" x="${x(i) - band / 2}" y="${pad.t}" width="${band}" height="${h}"${tipAttrs(d.tip)}`
      + ` data-cross="${x(i)}"/>`;
    if (d.value != null && (items.length <= 16 || i === items.length - 1))
      svg += `<circle class="dot" cx="${x(i)}" cy="${y(d.value)}" r="4"/>`;
    svg += "</g>";
    if (i % every === (items.length - 1) % every)
      svg += `<text x="${x(i)}" y="${height - 6}" text-anchor="middle">${escAttr(d.label)}</text>`;
  });
  svg += `<line class="cross" x1="0" x2="0" y1="${pad.t}" y2="${pad.t + h}" visibility="hidden"/>`;
  return svg + "</svg>";
}

// 値の一覧表 (ツールチップを使えない人のため、同じ数字を表でも見られるようにする)
function tableView(headers, rows) {
  return `<details class="table"><summary>数値を表で見る</summary><table><tr>${headers.map((x) => `<th>${escAttr(x)}</th>`).join("")}</tr>`
    + rows.map((r) => `<tr>${r.map((c) => `<td>${escAttr(c)}</td>`).join("")}</tr>`).join("") + "</table></details>";
}

// ---------- ツールチップ ----------
(() => {
  const tip = document.getElementById("tooltip");
  let current = null;
  function show(el, clientX, clientY) {
    if (current && current !== el) current.classList.remove("on");
    current = el;
    el.classList.add("on");
    tip.textContent = "";
    const b = document.createElement("b");
    b.textContent = el.dataset.tipValue;
    tip.append(b, document.createTextNode(el.dataset.tipTitle));
    tip.hidden = false;
    const cross = el.closest("svg")?.querySelector(".cross");
    if (cross && el.dataset.cross) {
      cross.setAttribute("x1", el.dataset.cross);
      cross.setAttribute("x2", el.dataset.cross);
      cross.setAttribute("visibility", "visible");
    }
    const r = tip.getBoundingClientRect();
    const left = Math.min(Math.max(8, clientX - r.width / 2), innerWidth - r.width - 8);
    const top = clientY - r.height - 14 < 8 ? clientY + 18 : clientY - r.height - 14;
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
  }
  function hide() {
    tip.hidden = true;
    current?.classList.remove("on");
    current?.closest("svg")?.querySelector(".cross")?.setAttribute("visibility", "hidden");
    current = null;
  }
  document.addEventListener("pointermove", (e) => {
    const el = e.target.closest?.("[data-tip-value]");
    if (el) show(el, e.clientX, e.clientY);
    else if (current && e.pointerType === "mouse") hide();
  });
  document.addEventListener("pointerdown", (e) => {
    const el = e.target.closest?.("[data-tip-value]");
    if (el) show(el, e.clientX, e.clientY);
    else hide();
  });
  addEventListener("scroll", hide, { passive: true });
})();

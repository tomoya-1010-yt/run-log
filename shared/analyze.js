// Health Auto Export が送ってくるワークアウト 1 件を、ランログで使う形にまとめる。
// Cloudflare Pages Functions (functions/api) が使う。dev/analyze.py は同じ処理の Python 版
// (ローカル確認用)。変えたときは両方そろえ、dev/check.html で結果が一致するか確かめる。

export const STOP_SPEED = 0.6;       // m/s。これより遅い区間は「止まっていた」とみなす (約 28 分/km)
export const GAP_SEC = 10;           // GPS の点がこれ以上あいた区間も「止まっていた」とみなす
export const MAX_SPEED = 25 / 3.6;   // m/s。これより速い区間は GPS のずれか乗り物とみなして捨てる
export const MAX_ACCURACY = 50;      // m。位置の誤差がこれより大きい点は使わない
const FAST_FLAG_SEC = 30;            // 時速 25km 超の区間がこれ以上あれば「要確認」
const GAP_FLAG_SEC = 300;            // GPS がこれ以上途切れていれば「要確認」
const LOW_HR = 115;                  // 平均心拍がこれ未満なら「要確認」
export const LONG_PAUSE_SEC = 30 * 60; // これ以上の一時停止のあと再開していたら、一時停止した時点でランが終わったとみなす
const PAUSE_SLACK_SEC = 60;          // 一時停止の前後の GPS の点のずれ

// "2026-10-18 18:58:15 +0900" → Date のミリ秒
export function parseTime(s) {
  if (typeof s !== "string") return NaN;
  return Date.parse(s.trim().replace(" ", "T").replace(/ ?([+-]\d\d):?(\d\d)$/, "$1:$2").replace(/ ?Z$/, "Z"));
}

const qty = (v) => (v && typeof v === "object" ? Number(v.qty) : Number(v));
const round = (x, n = 0) => (Number.isFinite(x) ? Math.round(x * 10 ** n) / 10 ** n : null);

function km(v) {
  const q = qty(v);
  if (!Number.isFinite(q)) return null;
  const u = String(v?.units || "km").toLowerCase();
  return u.startsWith("mi") ? q * 1.609344 : u === "m" ? q / 1000 : q;
}

function haversine(lat1, lon1, lat2, lon2) {
  const r = Math.PI / 180;
  const a = Math.sin(((lat2 - lat1) * r) / 2) ** 2
    + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(((lon2 - lon1) * r) / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(a));
}

// 屋外のランだけを対象にする (名前は端末の言語で変わるので、種類と屋内フラグで見る)
export function isOutdoorRun(w) {
  const acts = Array.isArray(w.activities) ? w.activities : [];
  const running = acts.length ? acts.some((a) => a.activityType === "running")
    : /run|ラン/i.test(String(w.name || ""));
  const indoor = w.isIndoor === true || acts.some((a) => a.configuration?.locationType === "indoor")
    || /indoor|屋内|室内/i.test(String(w.location || ""));
  return running && !indoor;
}

// ルートの点を [経過秒, 緯度, 経度, 標高, 速度] にそろえる。誤差の大きい点は捨てる
function routePoints(w, t0) {
  const pts = [];
  for (const p of Array.isArray(w.route) ? w.route : []) {
    const lat = Number(p.latitude ?? p.lat);
    const lon = Number(p.longitude ?? p.lon);
    const t = parseTime(p.timestamp);
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || !Number.isFinite(t)) continue;
    const acc = Number(p.horizontalAccuracy);
    if (Number.isFinite(acc) && acc > MAX_ACCURACY) continue;
    const spd = Number(p.speed);
    const okSpeed = Number.isFinite(spd) && spd >= 0 && !(Number(p.speedAccuracy) < 0);
    pts.push([Math.round((t - t0) / 1000), lat, lon, Number(p.altitude) || 0, okSpeed ? spd : null]);
  }
  pts.sort((a, b) => a[0] - b[0]);
  return pts;
}

// 一時停止の記録から、短い一時停止の区間 [始まり秒, 終わり秒] と、ランが実際に終わった時点を出す。
// 一時停止したまま終了し忘れ、翌日などに再開・終了したワークアウトは、一時停止した時点 (cut) で区切る
function pausesOf(w, t0) {
  const end = Math.round((parseTime(w.end) - t0) / 1000);
  const ev = (Array.isArray(w.events) ? w.events : [])
    .filter((e) => e.type === "pause" || e.type === "resume")
    .map((e) => ({ type: e.type, t: Math.round((parseTime(e.start) - t0) / 1000) }))
    .filter((e) => Number.isFinite(e.t))
    .sort((a, b) => a.t - b.t);
  const pauses = [];
  for (let i = 0; i < ev.length; i++) {
    if (ev[i].type !== "pause") continue;
    const resume = ev.slice(i + 1).find((e) => e.type === "resume");
    if (!resume) { // 最後の一時停止。そのまま長い時間がたってから終了していれば区切る
      if (Number.isFinite(end) && end - ev[i].t >= LONG_PAUSE_SEC) return { pauses, cut: ev[i].t, resumed: end };
      break;
    }
    if (resume.t - ev[i].t >= LONG_PAUSE_SEC) return { pauses, cut: ev[i].t, resumed: resume.t };
    pauses.push([ev[i].t, resume.t]);
  }
  return { pauses, cut: null, resumed: null };
}

// 1分ごとの記録 (距離・消費エネルギー・歩数) のうち、cut 秒より前の分の合計 (記録がなければ null)
function sumBefore(list, t0, cut, value) {
  let total = 0, n = 0;
  for (const x of Array.isArray(list) ? list : []) {
    const t = (parseTime(x.date) - t0) / 1000;
    const v = value(x);
    if (Number.isFinite(t) && t < cut && Number.isFinite(v)) { total += v; n++; }
  }
  return n ? total : null;
}

const toKcal = (v) => {
  const q = qty(v);
  return Number.isFinite(q) ? (String(v?.units || "").toLowerCase() === "kj" ? q / 4.184 : q) : null;
};

// ルートの標高から獲得標高を出す (3m 未満の上下はぶれとして無視。標高 0 は記録なしとして飛ばす)
function climb(pts) {
  let up = 0, base = null;
  for (const p of pts) {
    if (!p[3]) continue;
    if (base === null || p[3] < base) base = p[3];
    else if (p[3] - base >= 3) { up += p[3] - base; base = p[3]; }
  }
  return up;
}

// 止まっていた時間・乗り物らしい区間・GPS の途切れを数える (一時停止中の途切れは数えない)
function movement(pts, pauses = []) {
  let moving = 0, fast = 0, fastKm = 0, maxGap = 0, gpsKm = 0;
  for (let i = 1; i < pts.length; i++) {
    const [t1, la1, lo1] = pts[i - 1];
    const [t2, la2, lo2, , spd] = pts[i];
    const dt = t2 - t1;
    if (dt <= 0) continue;
    if (!pauses.some(([a, b]) => t1 >= a - PAUSE_SLACK_SEC && t2 <= b + PAUSE_SLACK_SEC)) maxGap = Math.max(maxGap, dt);
    const d = haversine(la1, lo1, la2, lo2);
    if (dt > GAP_SEC) continue;
    const v = spd ?? d / dt; // 端末が測った速度があればそちらを使う (GPS のぶれに強い)
    if (v > MAX_SPEED) { fast += dt; fastKm += d / 1000; continue; }
    gpsKm += d / 1000;
    if (v >= STOP_SPEED) moving += dt;
  }
  return { moving, fast, fastKm, maxGap, gpsKm };
}

// 1km ごとのペース。Apple が計算したもの (フィットネスアプリと同じ数字) を優先し、なければルートから出す。
// cut (ランが終わった時点) があれば、そこまでに終わった区間と、残りの端数だけにする
function splitsOf(w, pts, t0, cut, distance) {
  const s = (Array.isArray(w.splits) ? w.splits : [])
    .find((x) => x.distanceType === "walkingRunning" && qty(x.targetDistance) === 1 && /^k/i.test(x.targetDistance?.units || "km"));
  if (s?.items?.length) {
    const items = cut === null ? s.items : s.items.filter((x) => (parseTime(x.end) - t0) / 1000 <= cut + 1);
    const out = items.map((x) => ({
      km: round(qty(x.metrics?.distance) / (/^k/i.test(x.metrics?.distance?.units || "m") ? 1 : 1000), 3),
      sec: round(Number(x.elapsedDuration ?? x.activeDuration)),
      hr: round(qty(x.metrics?.heartRateAverage)),
    })).filter((x) => x.km > 0 && x.sec > 0);
    if (cut !== null) {
      const rest = distance - out.reduce((a, x) => a + x.km, 0);
      const lastEnd = items.length ? (parseTime(items[items.length - 1].end) - t0) / 1000 : 0;
      // 1分ごとの記録から出した端数なので、ありえない速さになるときは出さない
      if (rest >= 0.05 && cut > lastEnd && (rest * 1000) / (cut - lastEnd) <= MAX_SPEED)
        out.push({ km: round(rest, 3), sec: round(cut - lastEnd), hr: null });
    }
    return out;
  }
  const out = [];
  let dist = 0, lastT = pts[0]?.[0] ?? 0, lastD = 0;
  for (let i = 1; i < pts.length; i++) {
    const dt = pts[i][0] - pts[i - 1][0];
    const d = haversine(pts[i - 1][1], pts[i - 1][2], pts[i][1], pts[i][2]);
    if (dt > 0 && d / dt > MAX_SPEED) continue;
    dist += d;
    if (dist - lastD >= 1000) {
      out.push({ km: 1, sec: pts[i][0] - lastT, hr: null });
      lastT = pts[i][0];
      lastD += 1000;
    }
  }
  const rest = (dist - lastD) / 1000;
  if (rest >= 0.05 && pts.length) out.push({ km: round(rest, 3), sec: pts[pts.length - 1][0] - lastT, hr: null });
  return out;
}

const hrAvg = (x) => Number(x.Avg ?? x.avg ?? x.qty);

// 1分ごとの平均心拍 → [開始からの分, 平均] (cut があればそれより前だけ)
function hrSeries(w, t0, cut) {
  return (Array.isArray(w.heartRateData) ? w.heartRateData : [])
    .filter((x) => cut === null || (parseTime(x.date) - t0) / 1000 < cut)
    .map((x) => [Math.round((parseTime(x.date) - t0) / 60000), round(hrAvg(x))])
    .filter(([m, v]) => Number.isFinite(m) && m >= 0 && v > 0);
}

// 地図用に、近すぎる点 (3m 未満) を間引いて軽くする
function compactRoute(pts) {
  const out = { t: [], lat: [], lon: [], alt: [], v: [] };
  let last = null;
  pts.forEach((p, i) => {
    if (last && i < pts.length - 1 && haversine(last[1], last[2], p[1], p[2]) < 3) return;
    out.t.push(p[0]);
    out.lat.push(round(p[1], 6));
    out.lon.push(round(p[2], 6));
    out.alt.push(round(p[3], 1));
    out.v.push(p[4] === null ? null : round(p[4], 2));
    last = p;
  });
  return out;
}

const minutes = (s) => Math.round(s / 60);
const jstTime = (ms) => new Date(ms + 9 * 3600 * 1000).toISOString().slice(11, 16);

// 返り値: { run: 一覧・集計用のまとめ, route: 地図用の点 } (屋外ランでなければ null)
export function analyzeWorkout(w) {
  if (!w || !isOutdoorRun(w)) return null;
  const t0 = parseTime(w.start);
  const t1 = parseTime(w.end);
  if (!Number.isFinite(t0)) return null;
  const id = String(w.id || w.start).replace(/[^\w-]/g, "-");
  const { pauses, cut, resumed } = pausesOf(w, t0);
  const duration = cut !== null ? cut - pauses.reduce((a, [s, e]) => a + (e - s), 0)
    : Number(w.duration) || (Number.isFinite(t1) ? (t1 - t0) / 1000 : 0);
  const pts = routePoints(w, t0).filter((p) => cut === null || p[0] <= cut);
  const mv = pts.length >= 2 ? movement(pts, pauses) : null;
  // 区切ったときは、区切りまでの 1 分ごとの距離の合計 (ウォッチの距離より長ければウォッチの距離) を使う
  const cutKm = cut !== null ? sumBefore(w.walkingAndRunningDistance, t0, cut, km) : null;
  const watchKm = cutKm !== null ? Math.min(cutKm, km(w.distance) ?? cutKm) : km(w.distance) ?? mv?.gpsKm ?? 0;
  // 乗り物らしい区間があれば、その分を距離から除く (短いぶれは無視して、ウォッチの距離をそのまま使う)
  const fast = mv && mv.fast >= FAST_FLAG_SEC;
  const distance = fast ? Math.max(0, watchKm - mv.fastKm) : watchKm;
  const moving = mv ? Math.min(mv.moving, duration) : duration;
  const hrBefore = cut === null ? [] : (Array.isArray(w.heartRateData) ? w.heartRateData : [])
    .filter((x) => (parseTime(x.date) - t0) / 1000 < cut && hrAvg(x) > 0);
  const avgHr = cut !== null && hrBefore.length ? hrBefore.reduce((a, x) => a + hrAvg(x), 0) / hrBefore.length
    : qty(w.avgHeartRate ?? w.heartRate?.avg);
  const maxHr = cut !== null && hrBefore.length ? Math.max(...hrBefore.map((x) => Number(x.Max ?? x.max ?? hrAvg(x))))
    : qty(w.maxHeartRate ?? w.heartRate?.max);
  const steps = cut !== null ? sumBefore(w.stepCount, t0, cut, qty) : null;

  const flags = [];
  if (fast)
    flags.push({ code: "fast", text: `時速25kmを超える区間が約${Math.max(1, minutes(mv.fast))}分あります（乗り物かGPSのずれの可能性）。`
      + `その${round(mv.fastKm, 1)}kmは距離に含めていません` });
  if (mv && mv.maxGap >= GAP_FLAG_SEC) flags.push({ code: "gap", text: `GPSが${minutes(mv.maxGap)}分途切れています` });
  if (Number.isFinite(avgHr) && avgHr > 0 && avgHr < LOW_HR)
    flags.push({ code: "low_hr", text: `平均心拍が${Math.round(avgHr)}と、ランにしては低めです` });
  if (!pts.length) flags.push({ code: "no_route", text: "ルートが記録されていません" });

  // 「要確認」ではないが、詳細画面で知らせておくこと
  const notes = [];
  if (cut !== null) {
    const later = (resumed - cut) / 3600;
    notes.push(`一時停止したまま${later >= 24 ? `${Math.round(later / 24)}日` : `${Math.round(later)}時間`}後に終了されていたので、`
      + `一時停止した${jstTime(t0 + cut * 1000)}で終わったものとして扱っています（再開したあとの記録は含めません）`);
  }
  const start = new Date(t0 + 9 * 3600 * 1000).toISOString().slice(0, 19) + "+09:00";
  const run = {
    id,
    date: String(w.start).slice(0, 10), // 走った場所の現地の日付
    start,
    distance_km: round(distance, 3),
    watch_km: round(watchKm, 3), // ウォッチが記録した距離 (乗り物らしい区間を除く前)
    duration_s: round(duration),
    moving_s: round(moving),
    pace_s: distance > 0 ? round(moving / distance) : null, // 動いていた時間での 1km あたりの秒数
    avg_hr: round(avgHr),
    max_hr: round(maxHr),
    elevation_up_m: round(cut !== null ? climb(pts) : qty(w.elevationUp), 1),
    cadence: round(cut !== null ? (steps !== null && duration > 0 ? steps / (duration / 60) : null) : qty(w.stepCadence)),
    temperature_c: round(qty(w.temperature), 1),
    humidity: round(qty(w.humidity)),
    kcal: round(cut !== null ? sumBefore(w.activeEnergy, t0, cut, toKcal) : toKcal(w.activeEnergyBurned)),
    gps_km: mv ? round(mv.gpsKm, 3) : null,
    stopped_s: round(Math.max(0, duration - moving)),
    splits: splitsOf(w, pts, t0, cut, distance),
    hr: hrSeries(w, t0, cut),
    flags,
    notes,
    source: String(w.source?.name || w.source || ""),
  };
  return { run, route: pts.length ? compactRoute(pts) : null };
}

// Health Auto Export の「健康データ」から、AI の分析 (第2弾) で使う項目だけ取り出す → [{date, name, qty}]
export const METRICS = ["vo2_max", "resting_heart_rate", "heart_rate_variability", "running_power",
  "running_stride_length", "running_ground_contact_time", "running_vertical_oscillation", "running_speed"];

export function pickMetrics(metrics) {
  const out = [];
  for (const m of Array.isArray(metrics) ? metrics : []) {
    if (!METRICS.includes(m.name)) continue;
    for (const d of Array.isArray(m.data) ? m.data : []) {
      const v = Number(d.qty ?? d.Avg ?? d.avg);
      const date = String(d.date || "").slice(0, 10);
      if (Number.isFinite(v) && /^\d{4}-\d{2}-\d{2}$/.test(date)) out.push({ date, name: m.name, qty: round(v, 3), units: m.units || "" });
    }
  }
  return out;
}

// /api/* をまとめて処理する Cloudflare Pages Functions。
// - アプリ用: /api/login 以外は合言葉でログインしたときの Cookie が必要
// - Health Auto Export 用: POST /api/ingest (Authorization: Bearer HAE_TOKEN が必要)
// - 通知スクリプト用: /api/internal/* (Authorization: Bearer INGEST_TOKEN が必要)

import { analyzeWorkout, pickMetrics } from "../../shared/analyze.js";

const COOKIE = "run_session";
const COOKIE_MAX_AGE = 400 * 24 * 3600; // ブラウザが許す最長 (約13か月)

const json = (data, init = {}) =>
  new Response(JSON.stringify(data), {
    ...init,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...init.headers },
  });

const jstDate = (ms = Date.now()) => new Date(ms + 9 * 3600 * 1000).toISOString().slice(0, 10);
const isMonth = (s) => typeof s === "string" && /^\d{4}-\d{2}$/.test(s);

function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

const bearer = (request, token) => !!token && safeEqual(request.headers.get("authorization") || "", `Bearer ${token}`);

// ---------- 合言葉 ----------

// 全角・半角の違いと前後の空白・改行を無視して比べる (スマホの入力や Secret の貼り付けで混ざりやすい)
const normalizePass = (s) => s.normalize("NFKC").trim();

// Cookie に入れる値。合言葉から作るので、合言葉を変えると全端末がログアウトされる
async function sessionToken(env) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", enc.encode(normalizePass(env.APP_PASSPHRASE)), { name: "HMAC", hash: "SHA-256" },
    false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode("run-log-session-v1"));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function cookieOf(request, name) {
  for (const part of (request.headers.get("cookie") || "").split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === name) return v.join("=");
  }
  return null;
}

async function loggedIn(env, request) {
  return safeEqual(cookieOf(request, COOKIE) || "", await sessionToken(env));
}

async function login(env, request) {
  const { passphrase } = await request.json().catch(() => ({}));
  if (typeof passphrase !== "string" || !safeEqual(normalizePass(passphrase), normalizePass(env.APP_PASSPHRASE))) {
    await new Promise((r) => setTimeout(r, 1000)); // 総当たりを遅くする
    return json({ error: "wrong passphrase" }, { status: 401 });
  }
  const cookie = `${COOKIE}=${await sessionToken(env)}; Path=/; Max-Age=${COOKIE_MAX_AGE}; HttpOnly; Secure; SameSite=Strict`;
  return json({ ok: true }, { headers: { "set-cookie": cookie } });
}

function logout() {
  return json({ ok: true }, { headers: { "set-cookie": `${COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict` } });
}

// ---------- 設定 ----------

async function setting(env, key, fallback = null) {
  const row = await env.DB.prepare("SELECT body FROM settings WHERE key = ?").bind(key).first();
  return row ? JSON.parse(row.body) : fallback;
}

const putSetting = (env, key, value) =>
  env.DB.prepare("INSERT OR REPLACE INTO settings (key, body) VALUES (?, ?)").bind(key, JSON.stringify(value));

// 月の目標は { "2026-10": 100, ... }。目標を決めていない月は、その前で最後に決めた目標を使う
function goalFor(goals, month) {
  const past = Object.keys(goals || {}).filter((m) => m <= month).sort();
  return past.length ? goals[past[past.length - 1]] : null;
}

// ---------- Health Auto Export からの受け取り ----------

async function ingest(env, request) {
  const payload = await request.json().catch(() => null);
  const data = payload?.data;
  if (!data || typeof data !== "object") return json({ error: "bad payload" }, { status: 400 });
  const now = new Date().toISOString();
  const stmts = [];
  let runs = 0, skipped = 0;
  for (const w of Array.isArray(data.workouts) ? data.workouts : []) {
    const a = analyzeWorkout(w);
    if (!a) { skipped++; continue; }
    runs++;
    const r = a.run;
    // 同じランが何度届いても上書きする。「集計から外す」の設定は残す
    stmts.push(env.DB.prepare(`INSERT INTO runs (id, date, distance_km, flagged, user_excluded, body, updated_at)
      VALUES (?, ?, ?, ?, 0, ?, ?)
      ON CONFLICT (id) DO UPDATE SET date = excluded.date, distance_km = excluded.distance_km,
        flagged = excluded.flagged, body = excluded.body, updated_at = excluded.updated_at`)
      .bind(r.id, r.date, r.distance_km, r.flags.length ? 1 : 0, JSON.stringify(r), now));
    if (a.route) stmts.push(env.DB.prepare("INSERT OR REPLACE INTO routes (id, body) VALUES (?, ?)").bind(r.id, JSON.stringify(a.route)));
  }
  const metrics = pickMetrics(data.metrics);
  for (const m of metrics)
    stmts.push(env.DB.prepare("INSERT OR REPLACE INTO daily_metrics (date, name, qty, units) VALUES (?, ?, ?, ?)")
      .bind(m.date, m.name, m.qty, m.units));
  // 中身がなくても「届いた」ことは記録する (3日届かないときの警告に使う)
  stmts.push(putSetting(env, "last_ingest", { at: now, runs, metrics: metrics.length }));
  for (let i = 0; i < stmts.length; i += 50) await env.DB.batch(stmts.slice(i, i + 50));
  return json({ ok: true, runs, skipped, metrics: metrics.length });
}

// ---------- アプリ用 ----------

// 一覧・集計用の軽い形 (1km ごとのペースと心拍の推移は詳細を開いたときだけ)
function light(row) {
  const { splits, hr, ...r } = JSON.parse(row.body);
  return { ...r, user_excluded: !!row.user_excluded };
}

async function state(env) {
  const rows = (await env.DB.prepare("SELECT body, user_excluded FROM runs ORDER BY date DESC, id DESC").all()).results;
  return json({
    today: jstDate(),
    goals: await setting(env, "goals", {}),
    last_ingest: await setting(env, "last_ingest"),
    runs: rows.map(light),
    vapidPublicKey: env.VAPID_PUBLIC_KEY || null,
  });
}

async function runDetail(env, id) {
  const row = await env.DB.prepare("SELECT body, user_excluded FROM runs WHERE id = ?").bind(id).first();
  if (!row) return json({ error: "not found" }, { status: 404 });
  const route = await env.DB.prepare("SELECT body FROM routes WHERE id = ?").bind(id).first();
  return json({ run: { ...JSON.parse(row.body), user_excluded: !!row.user_excluded }, route: route ? JSON.parse(route.body) : null });
}

async function updateRun(env, request, id) {
  const { excluded } = await request.json().catch(() => ({}));
  if (typeof excluded !== "boolean") return json({ error: "bad request" }, { status: 400 });
  const res = await env.DB.prepare("UPDATE runs SET user_excluded = ? WHERE id = ?").bind(excluded ? 1 : 0, id).run();
  if (!res.meta.changes) return json({ error: "not found" }, { status: 404 });
  return json({ ok: true });
}

async function putGoal(env, request) {
  const { month, km } = await request.json().catch(() => ({}));
  if (!isMonth(month) || !(km === null || (typeof km === "number" && km > 0 && km <= 2000)))
    return json({ error: "bad goal" }, { status: 400 });
  const goals = await setting(env, "goals", {});
  if (km === null) delete goals[month];
  else goals[month] = Math.round(km * 10) / 10;
  await putSetting(env, "goals", goals).run();
  return json({ ok: true, goals });
}

async function subscribe(env, request) {
  const { subscription } = await request.json();
  if (!subscription?.endpoint) return json({ error: "invalid subscription" }, { status: 400 });
  await env.DB
    .prepare("INSERT OR REPLACE INTO push_subs (endpoint, body, created_at) VALUES (?, ?, ?)")
    .bind(subscription.endpoint, JSON.stringify(subscription), new Date().toISOString()).run();
  return json({ ok: true });
}

// 全データの書き出し (バックアップ用)。ルートは大きいので含めない
async function exportAll(env) {
  const runs = (await env.DB.prepare("SELECT body, user_excluded FROM runs ORDER BY date").all()).results;
  const metrics = (await env.DB.prepare("SELECT date, name, qty, units FROM daily_metrics ORDER BY date, name").all()).results;
  return json({
    exported_at: new Date().toISOString(),
    goals: await setting(env, "goals", {}),
    runs: runs.map((r) => ({ ...JSON.parse(r.body), user_excluded: !!r.user_excluded })),
    metrics,
  }, { headers: { "content-disposition": `attachment; filename="run-log-${jstDate()}.json"` } });
}

// ---------- 通知スクリプト用 ----------

// 月ごとの距離・回数 (「集計から外す」にしたランは数えない)
async function internalStatus(env) {
  const months = {};
  const rows = (await env.DB.prepare(`SELECT substr(date, 1, 7) AS month, SUM(distance_km) AS km, COUNT(*) AS runs
    FROM runs WHERE user_excluded = 0 GROUP BY month`).all()).results;
  const goals = await setting(env, "goals", {});
  for (const r of rows) months[r.month] = { km: Math.round(r.km * 100) / 100, runs: r.runs };
  const now = jstDate().slice(0, 7);
  for (const m of new Set([...Object.keys(months), now])) {
    months[m] ??= { km: 0, runs: 0 };
    months[m].goal = goalFor(goals, m);
  }
  // 過去30日でいちばん長いラン。次のロング走の安全な上限 (この 1.1 倍) に使う。
  // 「要確認」と「集計から外す」のランは距離があてにならないので除く (docs/coach-guidelines.md の R3)
  const since = jstDate(Date.now() - 30 * 24 * 3600 * 1000);
  const longest = await env.DB.prepare(`SELECT MAX(distance_km) AS km FROM runs
    WHERE user_excluded = 0 AND flagged = 0 AND date >= ?`).bind(since).first();
  return json({ today: jstDate(), last_ingest: await setting(env, "last_ingest"), months, longest_30d_km: longest?.km ?? null });
}

// 送った通知の記録。予備の定期実行で同じ通知を二度送らないために使う
async function notified(env, request) {
  const sent = await setting(env, "notified", []);
  if (request.method === "GET") return json(sent);
  const { key } = await request.json();
  if (typeof key !== "string" || !key) return json({ error: "bad key" }, { status: 400 });
  await putSetting(env, "notified", [key, ...sent.filter((k) => k !== key)].slice(0, 200)).run();
  return json({ ok: true });
}

async function subscriptions(env, request) {
  if (request.method === "DELETE") {
    const { endpoint } = await request.json();
    await env.DB.prepare("DELETE FROM push_subs WHERE endpoint = ?").bind(endpoint).run();
    return json({ ok: true });
  }
  const rows = (await env.DB.prepare("SELECT body FROM push_subs").all()).results;
  return json(rows.map((r) => JSON.parse(r.body)));
}

// ---------- ルーティング ----------

export async function onRequest({ request, env }) {
  const url = new URL(request.url);
  const path = url.pathname.replace(/\/+$/, "");
  const route = `${request.method} ${path}`;

  if (path === "/api/ingest") {
    if (!bearer(request, env.HAE_TOKEN)) return json({ error: "unauthorized" }, { status: 401 });
    if (request.method !== "POST") return json({ error: "method not allowed" }, { status: 405 });
    return ingest(env, request);
  }

  if (path.startsWith("/api/internal/")) {
    if (!bearer(request, env.INGEST_TOKEN)) return json({ error: "unauthorized" }, { status: 401 });
    if (route === "GET /api/internal/status") return internalStatus(env);
    if (path === "/api/internal/notified") return notified(env, request);
    if (path === "/api/internal/subscriptions") return subscriptions(env, request);
    return json({ error: "not found" }, { status: 404 });
  }

  if (!env.APP_PASSPHRASE) return json({ error: "APP_PASSPHRASE is not set" }, { status: 500 });
  if (route === "POST /api/login") return login(env, request);
  if (route === "POST /api/logout") return logout();
  if (!(await loggedIn(env, request))) return json({ error: "login required" }, { status: 401 });

  if (route === "GET /api/state") return state(env);
  if (route === "PUT /api/goal") return putGoal(env, request);
  if (route === "POST /api/subscribe") return subscribe(env, request);
  if (route === "GET /api/export") return exportAll(env);
  const rm = path.match(/^\/api\/runs\/([\w-]+)$/);
  if (rm && request.method === "GET") return runDetail(env, rm[1]);
  if (rm && request.method === "PUT") return updateRun(env, request, rm[1]);
  return json({ error: "not found" }, { status: 404 });
}

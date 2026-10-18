"""画面確認用のローカルサーバー (本番では使わない)。

web/ を配信し、/api/* を簡易的に再現する。データはメモリ上だけで、止めると消える。
ルートの分析は dev/analyze.py (shared/analyze.js の Python 版) で行う。
- 合言葉は「dev」。Health Auto Export 用・通知スクリプト用のトークンも「dev」
- 起動時に dev/sample.py のダミー (直近14か月分) を読み込む。--empty で空から始める
- 本物の書き出しを入れて試すとき:
    python dev/server.py --empty
    python scripts/import_hae.py --url http://localhost:8789 --token dev 書き出したファイル.json
- dev/check.html … shared/analyze.js と dev/analyze.py の結果が一致するか確かめるページ
- dev/worker.html … 本番の functions/api を、ブラウザ上の SQLite を D1 の代わりにして動かすページ

  python dev/server.py   → http://localhost:8789
"""
import datetime as dt
import http.server
import json
import pathlib
import re
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dev"))
import analyze  # noqa: E402
import sample  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
PASSPHRASE = "dev"
TOKEN = "dev"
SESSION = "run_session=dev-session"

runs, routes, raw = {}, {}, {}  # raw: 受け取ったワークアウトそのもの (check.html 用)
metrics = {}
settings = {"goals": {}, "last_ingest": None, "notified": []}
subs = {}


def today():
    return dt.datetime.now(JST).date().isoformat()


def ingest(data):
    n = skipped = 0
    for w in data.get("workouts") or []:
        a = analyze.analyze_workout(w)
        if not a:
            skipped += 1
            continue
        n += 1
        r = a["run"]
        r["user_excluded"] = runs.get(r["id"], {}).get("user_excluded", False)
        runs[r["id"]] = r
        raw[r["id"]] = w
        if a["route"]:
            routes[r["id"]] = a["route"]
    ms = analyze.pick_metrics(data.get("metrics"))
    for m in ms:
        metrics[(m["date"], m["name"])] = m
    settings["last_ingest"] = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "runs": n, "metrics": len(ms)}
    return {"ok": True, "runs": n, "skipped": skipped, "metrics": len(ms)}


def goal_for(month):
    past = sorted(m for m in settings["goals"] if m <= month)
    return settings["goals"][past[-1]] if past else None


def light(r):
    return {k: v for k, v in r.items() if k not in ("splits", "hr")}


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "web"), **kwargs)

    def translate_path(self, path):
        # 確認用ページ・共通の分析処理・dev/worker.html が読む本番のコードは web/ の外にあるので、ここだけ別の場所から出す
        p = urllib.parse.unquote(urllib.parse.urlparse(path).path)
        for prefix in ("/dev/", "/shared/", "/functions/", "/migrations/"):
            if p.startswith(prefix):
                return str(ROOT / prefix.strip("/") / p[len(prefix):])
        return super().translate_path(path)

    def end_headers(self):
        if self.path.endswith(".js"):
            self.send_header("cache-control", "no-store")
        super().end_headers()

    def guess_type(self, path):
        return "text/javascript" if str(path).endswith(".js") else super().guess_type(path)

    def send_json(self, data, status=200, headers=None):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("content-length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def api(self, method):
        url = urllib.parse.urlparse(self.path)
        path = url.path.rstrip("/")
        route = f"{method} {path}"
        bearer = self.headers.get("authorization") == f"Bearer {TOKEN}"

        if path == "/api/ingest":
            if not bearer:
                return self.send_json({"error": "unauthorized"}, 401)
            return self.send_json(ingest((self.body() or {}).get("data") or {}))

        if path.startswith("/api/internal/"):
            if not bearer:
                return self.send_json({"error": "unauthorized"}, 401)
            if route == "GET /api/internal/status":
                months = {}
                for r in runs.values():
                    if r["user_excluded"]:
                        continue
                    m = months.setdefault(r["date"][:7], {"km": 0, "runs": 0})
                    m["km"] += r["distance_km"]
                    m["runs"] += 1
                months.setdefault(today()[:7], {"km": 0, "runs": 0})
                for k, m in months.items():
                    m["km"] = round(m["km"], 2)
                    m["goal"] = goal_for(k)
                return self.send_json({"today": today(), "last_ingest": settings["last_ingest"], "months": months})
            if path == "/api/internal/notified":
                if method == "GET":
                    return self.send_json(settings["notified"])
                settings["notified"].insert(0, self.body()["key"])
                return self.send_json({"ok": True})
            if path == "/api/internal/subscriptions":
                if method == "DELETE":
                    subs.pop(self.body().get("endpoint"), None)
                    return self.send_json({"ok": True})
                return self.send_json(list(subs.values()))
            return self.send_json({"error": "not found"}, 404)

        if route == "GET /dev/raw":  # check.html 用: 受け取ったワークアウトと Python 版の結果
            return self.send_json([{"workout": raw[i], "run": {k: v for k, v in runs[i].items() if k != "user_excluded"},
                                    "route": routes.get(i)} for i in list(raw)[-20:]])
        if route == "POST /api/login":
            if self.body().get("passphrase") != PASSPHRASE:
                return self.send_json({"error": "wrong passphrase"}, 401)
            return self.send_json({"ok": True}, headers={"set-cookie": f"{SESSION}; Path=/; HttpOnly; SameSite=Strict"})
        if route == "POST /api/logout":
            return self.send_json({"ok": True}, headers={"set-cookie": "run_session=; Path=/; Max-Age=0"})
        if SESSION not in (self.headers.get("cookie") or ""):
            return self.send_json({"error": "login required"}, 401)

        if route == "GET /api/state":
            lst = sorted(runs.values(), key=lambda r: (r["date"], r["id"]), reverse=True)
            return self.send_json({"today": today(), "goals": settings["goals"], "last_ingest": settings["last_ingest"],
                                   "runs": [light(r) for r in lst], "vapidPublicKey": None})
        if route == "PUT /api/goal":
            b = self.body()
            if b.get("km") is None:
                settings["goals"].pop(b["month"], None)
            else:
                settings["goals"][b["month"]] = round(float(b["km"]), 1)
            return self.send_json({"ok": True, "goals": settings["goals"]})
        if route == "POST /api/subscribe":
            s = self.body()["subscription"]
            subs[s["endpoint"]] = s
            return self.send_json({"ok": True})
        if route == "GET /api/export":
            return self.send_json({"goals": settings["goals"], "runs": sorted(runs.values(), key=lambda r: r["date"]),
                                   "metrics": list(metrics.values())})
        m = re.match(r"^/api/runs/([\w-]+)$", path)
        if m:
            r = runs.get(m[1])
            if not r:
                return self.send_json({"error": "not found"}, 404)
            if method == "PUT":
                r["user_excluded"] = bool(self.body().get("excluded"))
                return self.send_json({"ok": True})
            return self.send_json({"run": r, "route": routes.get(m[1])})
        return self.send_json({"error": "not found"}, 404)

    def do_GET(self):
        if self.path.startswith(("/api/", "/dev/raw")):
            return self.api("GET")
        return super().do_GET()

    def do_POST(self):
        return self.api("POST")

    def do_PUT(self):
        return self.api("PUT")

    def do_DELETE(self):
        return self.api("DELETE")

    def log_message(self, fmt, *args):
        if not re.search(r'" (200|304) ', fmt % args):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    if "--empty" not in sys.argv:
        ingest({"workouts": sample.workouts(today()), "metrics": sample.metrics(today())})
        t = dt.date.fromisoformat(today())
        months_ago = lambda n: f"{t.year + (t.month - 1 - n) // 12}-{(t.month - 1 - n) % 12 + 1:02d}"
        settings["goals"] = {months_ago(14): 50, months_ago(6): 70, months_ago(0): 100}
    print(f"{len(runs)} runs. http://localhost:8789 (合言葉: {PASSPHRASE})")
    http.server.ThreadingHTTPServer(("127.0.0.1", 8789), Handler).serve_forever()

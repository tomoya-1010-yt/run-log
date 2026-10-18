"""shared/analyze.js の Python 版 (ローカル確認用の dev/server.py が使う)。

本番は shared/analyze.js が動く。どちらかを変えたら、もう片方も同じように変え、
dev/check.html (http://localhost:8789/dev/check.html) で結果が一致するか確かめる。
"""
import datetime as dt
import math
import re

STOP_SPEED = 0.6
GAP_SEC = 10
MAX_SPEED = 25 / 3.6
MAX_ACCURACY = 50
FAST_FLAG_SEC = 30
GAP_FLAG_SEC = 300
LOW_HR = 115
JST = dt.timezone(dt.timedelta(hours=9))


def parse_time(s):
    """"2026-10-18 18:58:15 +0900" → エポックミリ秒 (読めなければ None)"""
    if not isinstance(s, str):
        return None
    s = s.strip().replace(" ", "T", 1)
    s = re.sub(r" ?([+-]\d\d):?(\d\d)$", r"\1:\2", s)
    s = re.sub(r" ?Z$", "+00:00", s)
    try:
        return dt.datetime.fromisoformat(s).timestamp() * 1000
    except ValueError:
        return None


def num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def qty(v):
    return num(v.get("qty")) if isinstance(v, dict) else num(v)


def rnd(x, n=0):
    """JavaScript の Math.round と同じ丸め方 (.5 は大きい方へ)"""
    if x is None or not math.isfinite(x):
        return None
    m = 10 ** n
    r = math.floor(x * m + 0.5) / m
    return int(r) if n == 0 else r


def km(v):
    q = qty(v)
    if q is None:
        return None
    u = str((v or {}).get("units", "km") if isinstance(v, dict) else "km").lower()
    return q * 1.609344 if u.startswith("mi") else q / 1000 if u == "m" else q


def haversine(lat1, lon1, lat2, lon2):
    r = math.pi / 180
    a = (math.sin((lat2 - lat1) * r / 2) ** 2
         + math.cos(lat1 * r) * math.cos(lat2 * r) * math.sin((lon2 - lon1) * r / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(a))


def is_outdoor_run(w):
    acts = w.get("activities") if isinstance(w.get("activities"), list) else []
    running = any(a.get("activityType") == "running" for a in acts) if acts else bool(
        re.search(r"run|ラン", str(w.get("name") or ""), re.I))
    indoor = (w.get("isIndoor") is True
              or any((a.get("configuration") or {}).get("locationType") == "indoor" for a in acts)
              or bool(re.search(r"indoor|屋内|室内", str(w.get("location") or ""), re.I)))
    return running and not indoor


def route_points(w, t0):
    pts = []
    for p in w.get("route") or []:
        lat, lon = num(p.get("latitude", p.get("lat"))), num(p.get("longitude", p.get("lon")))
        t = parse_time(p.get("timestamp"))
        if lat is None or lon is None or t is None:
            continue
        acc = num(p.get("horizontalAccuracy"))
        if acc is not None and acc > MAX_ACCURACY:
            continue
        spd = num(p.get("speed"))
        sacc = num(p.get("speedAccuracy"))
        ok = spd is not None and spd >= 0 and not (sacc is not None and sacc < 0)
        pts.append([rnd((t - t0) / 1000), lat, lon, num(p.get("altitude")) or 0, spd if ok else None])
    pts.sort(key=lambda p: p[0])
    return pts


def movement(pts):
    moving = fast = max_gap = 0
    gps_km = fast_km = 0.0
    for (t1, la1, lo1, _, _), (t2, la2, lo2, _, spd) in zip(pts, pts[1:]):
        d_t = t2 - t1
        if d_t <= 0:
            continue
        max_gap = max(max_gap, d_t)
        d = haversine(la1, lo1, la2, lo2)
        if d_t > GAP_SEC:
            continue
        v = spd if spd is not None else d / d_t
        if v > MAX_SPEED:
            fast += d_t
            fast_km += d / 1000
            continue
        gps_km += d / 1000
        if v >= STOP_SPEED:
            moving += d_t
    return {"moving": moving, "fast": fast, "fastKm": fast_km, "maxGap": max_gap, "gpsKm": gps_km}


def splits_of(w, pts):
    for s in w.get("splits") or []:
        td = s.get("targetDistance") or {}
        if (s.get("distanceType") == "walkingRunning" and qty(td) == 1
                and re.match(r"^k", td.get("units", "km"), re.I) and s.get("items")):
            out = []
            for x in s["items"]:
                m = x.get("metrics") or {}
                d = qty(m.get("distance"))
                div = 1 if re.match(r"^k", (m.get("distance") or {}).get("units", "m"), re.I) else 1000
                item = {"km": rnd(d / div, 3) if d is not None else None,
                        "sec": rnd(num(x.get("elapsedDuration", x.get("activeDuration")))),
                        "hr": rnd(qty(m.get("heartRateAverage")))}
                if (item["km"] or 0) > 0 and (item["sec"] or 0) > 0:
                    out.append(item)
            return out
    out = []
    dist, last_t, last_d = 0.0, (pts[0][0] if pts else 0), 0.0
    for a, b in zip(pts, pts[1:]):
        d_t = b[0] - a[0]
        d = haversine(a[1], a[2], b[1], b[2])
        if d_t > 0 and d / d_t > MAX_SPEED:
            continue
        dist += d
        if dist - last_d >= 1000:
            out.append({"km": 1, "sec": b[0] - last_t, "hr": None})
            last_t = b[0]
            last_d += 1000
    rest = (dist - last_d) / 1000
    if rest >= 0.05 and pts:
        out.append({"km": rnd(rest, 3), "sec": pts[-1][0] - last_t, "hr": None})
    return out


def hr_series(w, t0):
    out = []
    for x in w.get("heartRateData") or []:
        t = parse_time(x.get("date"))
        v = rnd(num(x.get("Avg", x.get("avg", x.get("qty")))))
        if t is None or v is None:
            continue
        m = rnd((t - t0) / 60000)
        if m >= 0 and v > 0:
            out.append([m, v])
    return out


def compact_route(pts):
    out = {"t": [], "lat": [], "lon": [], "alt": [], "v": []}
    last = None
    for i, p in enumerate(pts):
        if last and i < len(pts) - 1 and haversine(last[1], last[2], p[1], p[2]) < 3:
            continue
        out["t"].append(p[0])
        out["lat"].append(rnd(p[1], 6))
        out["lon"].append(rnd(p[2], 6))
        out["alt"].append(rnd(p[3], 1))
        out["v"].append(None if p[4] is None else rnd(p[4], 2))
        last = p
    return out


def fmt1(x):
    """JavaScript で小数1桁の数を文字列にしたときと同じ形 (3.0 → "3")"""
    r = rnd(x, 1)
    return str(int(r)) if r == int(r) else str(r)


def minutes(s):
    return rnd(s / 60)


def analyze_workout(w):
    if not w or not is_outdoor_run(w):
        return None
    t0, t1 = parse_time(w.get("start")), parse_time(w.get("end"))
    if t0 is None:
        return None
    run_id = re.sub(r"[^\w-]", "-", str(w.get("id") or w.get("start")), flags=re.ASCII)
    duration = num(w.get("duration")) or ((t1 - t0) / 1000 if t1 is not None else 0)
    pts = route_points(w, t0)
    mv = movement(pts) if len(pts) >= 2 else None
    watch_km = km(w.get("distance"))
    if watch_km is None:
        watch_km = mv["gpsKm"] if mv else 0
    fast = bool(mv and mv["fast"] >= FAST_FLAG_SEC)
    distance = max(0, watch_km - mv["fastKm"]) if fast else watch_km
    moving = min(mv["moving"], duration) if mv else duration
    avg_hr = qty(w.get("avgHeartRate", (w.get("heartRate") or {}).get("avg")))

    flags = []
    if fast:
        flags.append({"code": "fast", "text": f"時速25kmを超える区間が約{max(1, minutes(mv['fast']))}分あります（乗り物かGPSのずれの可能性）。"
                                              f"その{fmt1(mv['fastKm'])}kmは距離に含めていません"})
    if mv and mv["maxGap"] >= GAP_FLAG_SEC:
        flags.append({"code": "gap", "text": f"GPSが{minutes(mv['maxGap'])}分途切れています"})
    if avg_hr is not None and 0 < avg_hr < LOW_HR:
        flags.append({"code": "low_hr", "text": f"平均心拍が{rnd(avg_hr)}と、ランにしては低めです"})
    if not pts:
        flags.append({"code": "no_route", "text": "ルートが記録されていません"})

    def kcal(v):
        q = qty(v)
        if q is None:
            return None
        return q / 4.184 if str((v or {}).get("units", "")).lower() == "kj" else q

    start = dt.datetime.fromtimestamp(t0 / 1000, JST).strftime("%Y-%m-%dT%H:%M:%S") + "+09:00"
    src = w.get("source")
    run = {
        "id": run_id,
        "date": str(w.get("start"))[:10],
        "start": start,
        "distance_km": rnd(distance, 3),
        "watch_km": rnd(watch_km, 3),
        "duration_s": rnd(duration),
        "moving_s": rnd(moving),
        "pace_s": rnd(moving / distance) if distance > 0 else None,
        "avg_hr": rnd(avg_hr),
        "max_hr": rnd(qty(w.get("maxHeartRate", (w.get("heartRate") or {}).get("max")))),
        "elevation_up_m": rnd(qty(w.get("elevationUp")), 1),
        "cadence": rnd(qty(w.get("stepCadence"))),
        "temperature_c": rnd(qty(w.get("temperature")), 1),
        "humidity": rnd(qty(w.get("humidity"))),
        "kcal": rnd(kcal(w.get("activeEnergyBurned"))),
        "gps_km": rnd(mv["gpsKm"], 3) if mv else None,
        "stopped_s": rnd(max(0, duration - moving)),
        "splits": splits_of(w, pts),
        "hr": hr_series(w, t0),
        "flags": flags,
        "source": str(src.get("name", "") if isinstance(src, dict) else (src or "")),
    }
    return {"run": run, "route": compact_route(pts) if pts else None}


METRICS = ["vo2_max", "resting_heart_rate", "heart_rate_variability", "running_power",
           "running_stride_length", "running_ground_contact_time", "running_vertical_oscillation", "running_speed"]


def pick_metrics(metrics):
    out = []
    for m in metrics or []:
        if m.get("name") not in METRICS:
            continue
        for d in m.get("data") or []:
            v = num(d.get("qty", d.get("Avg", d.get("avg"))))
            date = str(d.get("date") or "")[:10]
            if v is not None and re.match(r"^\d{4}-\d{2}-\d{2}$", date):
                out.append({"date": date, "name": m["name"], "qty": rnd(v, 3), "units": m.get("units") or ""})
    return out

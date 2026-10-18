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
LONG_PAUSE_SEC = 30 * 60
PAUSE_SLACK_SEC = 60
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


def pauses_of(w, t0):
    """短い一時停止の区間 [始まり秒, 終わり秒] と、ランが実際に終わった時点 (長い一時停止の始まり)"""
    ev = []
    for e in w.get("events") or []:
        if e.get("type") not in ("pause", "resume"):
            continue
        t = parse_time(e.get("start"))
        if t is not None:
            ev.append({"type": e["type"], "t": rnd((t - t0) / 1000)})
    ev.sort(key=lambda e: e["t"])
    t_end = parse_time(w.get("end"))
    end = rnd((t_end - t0) / 1000) if t_end is not None else None
    pauses = []
    for i, e in enumerate(ev):
        if e["type"] != "pause":
            continue
        resume = next((x for x in ev[i + 1:] if x["type"] == "resume"), None)
        if resume is None:
            if end is not None and end - e["t"] >= LONG_PAUSE_SEC:
                return pauses, e["t"], end
            break
        if resume["t"] - e["t"] >= LONG_PAUSE_SEC:
            return pauses, e["t"], resume["t"]
        pauses.append([e["t"], resume["t"]])
    return pauses, None, None


def sum_before(items, t0, cut, value):
    total, n = 0.0, 0
    for x in items or []:
        t = parse_time(x.get("date"))
        v = value(x)
        if t is not None and (t - t0) / 1000 < cut and v is not None:
            total += v
            n += 1
    return total if n else None


def to_kcal(v):
    q = qty(v)
    if q is None:
        return None
    return q / 4.184 if str((v or {}).get("units", "")).lower() == "kj" else q


def climb(pts):
    up, base = 0.0, None
    for p in pts:
        if not p[3]:
            continue
        if base is None or p[3] < base:
            base = p[3]
        elif p[3] - base >= 3:
            up += p[3] - base
            base = p[3]
    return up


def hr_avg(x):
    return num(x.get("Avg", x.get("avg", x.get("qty"))))


def movement(pts, pauses=()):
    moving = fast = max_gap = 0
    gps_km = fast_km = 0.0
    for (t1, la1, lo1, _, _), (t2, la2, lo2, _, spd) in zip(pts, pts[1:]):
        d_t = t2 - t1
        if d_t <= 0:
            continue
        if not any(t1 >= a - PAUSE_SLACK_SEC and t2 <= b + PAUSE_SLACK_SEC for a, b in pauses):
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


def splits_of(w, pts, t0, cut, distance):
    for s in w.get("splits") or []:
        td = s.get("targetDistance") or {}
        if (s.get("distanceType") == "walkingRunning" and qty(td) == 1
                and re.match(r"^k", td.get("units", "km"), re.I) and s.get("items")):
            items = s["items"] if cut is None else [
                x for x in s["items"] if parse_time(x.get("end")) is not None and (parse_time(x["end"]) - t0) / 1000 <= cut + 1]
            out = []
            for x in items:
                m = x.get("metrics") or {}
                d = qty(m.get("distance"))
                div = 1 if re.match(r"^k", (m.get("distance") or {}).get("units", "m"), re.I) else 1000
                item = {"km": rnd(d / div, 3) if d is not None else None,
                        "sec": rnd(num(x.get("elapsedDuration", x.get("activeDuration")))),
                        "hr": rnd(qty(m.get("heartRateAverage")))}
                if (item["km"] or 0) > 0 and (item["sec"] or 0) > 0:
                    out.append(item)
            if cut is not None:
                rest = distance - sum(x["km"] for x in out)
                last_end = (parse_time(items[-1]["end"]) - t0) / 1000 if items else 0
                if rest >= 0.05 and cut > last_end and rest * 1000 / (cut - last_end) <= MAX_SPEED:
                    out.append({"km": rnd(rest, 3), "sec": rnd(cut - last_end), "hr": None})
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


def hr_series(w, t0, cut):
    out = []
    for x in w.get("heartRateData") or []:
        t = parse_time(x.get("date"))
        v = rnd(hr_avg(x))
        if t is None or v is None or (cut is not None and (t - t0) / 1000 >= cut):
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
    pauses, cut, resumed = pauses_of(w, t0)
    if cut is not None:
        duration = cut - sum(e - s for s, e in pauses)
    else:
        duration = num(w.get("duration")) or ((t1 - t0) / 1000 if t1 is not None else 0)
    pts = [p for p in route_points(w, t0) if cut is None or p[0] <= cut]
    mv = movement(pts, pauses) if len(pts) >= 2 else None
    cut_km = sum_before(w.get("walkingAndRunningDistance"), t0, cut, km) if cut is not None else None
    if cut_km is not None:
        wk = km(w.get("distance"))
        watch_km = min(cut_km, wk if wk is not None else cut_km)
    else:
        watch_km = km(w.get("distance"))
        if watch_km is None:
            watch_km = mv["gpsKm"] if mv else 0
    fast = bool(mv and mv["fast"] >= FAST_FLAG_SEC)
    distance = max(0, watch_km - mv["fastKm"]) if fast else watch_km
    moving = min(mv["moving"], duration) if mv else duration
    hr_before = [] if cut is None else [
        x for x in w.get("heartRateData") or []
        if parse_time(x.get("date")) is not None and (parse_time(x["date"]) - t0) / 1000 < cut and (hr_avg(x) or 0) > 0]
    if cut is not None and hr_before:
        avg_hr = sum(hr_avg(x) for x in hr_before) / len(hr_before)
        max_hr = max(num(x.get("Max", x.get("max", hr_avg(x)))) for x in hr_before)
    else:
        avg_hr = qty(w.get("avgHeartRate", (w.get("heartRate") or {}).get("avg")))
        max_hr = qty(w.get("maxHeartRate", (w.get("heartRate") or {}).get("max")))
    steps = sum_before(w.get("stepCount"), t0, cut, qty) if cut is not None else None

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

    notes = []
    if cut is not None:
        later = (resumed - cut) / 3600
        when = f"{rnd(later / 24)}日" if later >= 24 else f"{rnd(later)}時間"
        at = dt.datetime.fromtimestamp((t0 + cut * 1000) / 1000, JST).strftime("%H:%M")
        notes.append(f"一時停止したまま{when}後に終了されていたので、"
                     f"一時停止した{at}で終わったものとして扱っています（再開したあとの記録は含めません）")

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
        "max_hr": rnd(max_hr),
        "elevation_up_m": rnd(climb(pts) if cut is not None else qty(w.get("elevationUp")), 1),
        "cadence": rnd((steps / (duration / 60) if steps is not None and duration > 0 else None)
                       if cut is not None else qty(w.get("stepCadence"))),
        "temperature_c": rnd(qty(w.get("temperature")), 1),
        "humidity": rnd(qty(w.get("humidity"))),
        "kcal": rnd(sum_before(w.get("activeEnergy"), t0, cut, to_kcal) if cut is not None
                    else to_kcal(w.get("activeEnergyBurned"))),
        "gps_km": rnd(mv["gpsKm"], 3) if mv else None,
        "stopped_s": rnd(max(0, duration - moving)),
        "splits": splits_of(w, pts, t0, cut, distance),
        "hr": hr_series(w, t0, cut),
        "flags": flags,
        "notes": notes,
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

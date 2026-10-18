"""画面確認用のダミーデータ (Health Auto Export と同じ形のワークアウト)。

会津若松の鶴ヶ城のまわりを走ったことにして、直近14か月分を作る。
本物の位置は使わない。毎回同じ結果になるよう乱数の種を固定している。
"""
import datetime as dt
import math
import random
import uuid

JST = dt.timezone(dt.timedelta(hours=9))
CENTER = (37.4875, 139.9298)  # 鶴ヶ城


def fmt(t):
    return t.strftime("%Y-%m-%d %H:%M:%S +0900")


def workout(start, km, pace, rng, *, vehicle=False, indoor=False):
    """start から km だけ走ったワークアウト。pace は 1km あたりの秒数"""
    pts, splits, hr = [], [], []
    lat0, lon0 = CENTER
    radius = 0.006 + rng.random() * 0.004
    phase = rng.random() * math.tau
    t, d, step = start, 0.0, 2  # 2 秒ごとに点を打つ
    next_km, split_start = 1000, start
    while d < km * 1000:
        sec = (t - start).total_seconds()
        # 坂や疲れでペースを少し揺らす。7分ごとに30秒の信号待ち
        v = 1000 / (pace * (1 + 0.06 * math.sin(sec / 300) + rng.uniform(-0.03, 0.03)))
        if int(sec) % 420 < 30 and sec > 60:
            v = rng.uniform(0, 0.2)
        if vehicle and 0.45 < d / (km * 1000) < 0.6:
            v = 11  # 時速40km (バスに乗った)
        d += v * step
        ang = phase + d / (radius * 111000 * 1.3)
        lat = lat0 + radius * math.sin(ang) * (1 + 0.15 * math.sin(3 * ang))
        lon = lon0 + radius * 1.25 * math.cos(ang)
        pts.append({"latitude": lat, "longitude": lon, "altitude": 220 + 8 * math.sin(2 * ang),
                    "timestamp": fmt(t), "speed": v, "speedAccuracy": 0.5, "horizontalAccuracy": rng.uniform(1, 6),
                    "course": 0, "courseAccuracy": 0})
        if d >= next_km:
            splits.append({"metrics": {"distance": {"qty": 1000, "units": "m"},
                                       "heartRateAverage": {"qty": 140 + 1000 / pace * 4 + rng.uniform(-4, 4), "units": "bpm"}},
                           "elapsedDuration": (t - split_start).total_seconds(), "isPartial": False})
            next_km += 1000
            split_start = t
        t += dt.timedelta(seconds=step)
    rest = d - (next_km - 1000)
    if rest > 50:
        splits.append({"metrics": {"distance": {"qty": rest, "units": "m"}, "heartRateAverage": {"qty": 150, "units": "bpm"}},
                       "elapsedDuration": (t - split_start).total_seconds(), "isPartial": True})
    end = t
    minutes = int((end - start).total_seconds() // 60)
    base = 132 + (360 - pace) / 6
    for m in range(minutes):
        hr.append({"date": fmt(start + dt.timedelta(minutes=m)), "Avg": base + min(m, 10) * 1.5 + rng.uniform(-3, 3),
                   "Min": base - 5, "Max": base + 8, "units": "BPM"})
    avg_hr = sum(x["Avg"] for x in hr) / len(hr) if hr else 140
    return {
        "id": str(uuid.UUID(int=rng.getrandbits(128))).upper(),
        "name": "室内ランニング" if indoor else "屋外 ラン",
        "start": fmt(start), "end": fmt(end), "duration": (end - start).total_seconds(),
        "isIndoor": indoor, "location": "屋内" if indoor else "屋外",
        "activities": [{"activityType": "running", "configuration": {"locationType": "indoor" if indoor else "outdoor"}}],
        "distance": {"qty": km, "units": "km"},
        "avgHeartRate": {"qty": avg_hr if not vehicle else 108, "units": "BPM"},
        "maxHeartRate": {"qty": avg_hr + 15, "units": "BPM"},
        "elevationUp": {"qty": km * 4.2, "units": "m"},
        "stepCadence": {"qty": 160 + rng.uniform(-6, 6), "units": "count/min"},
        "temperature": {"qty": 18 + rng.uniform(-8, 8), "units": "degC"},
        "humidity": {"qty": 60 + rng.uniform(-15, 15), "units": "%"},
        "activeEnergyBurned": {"qty": km * 270, "units": "kJ"},
        "route": [] if indoor else pts,
        "heartRateData": hr,
        "splits": [{"distanceType": "walkingRunning", "targetDistance": {"qty": 1, "units": "km"}, "items": splits}],
        "source": {"name": "Apple Watch"},
    }


def workouts(today):
    """直近14か月分。週3回ほど、だんだん距離が伸びてペースが上がる"""
    rng = random.Random(42)
    end = dt.date.fromisoformat(today)
    day = end - dt.timedelta(days=425)
    out = []
    while day <= end:
        progress = 1 - (end - day).days / 425
        if day.weekday() in (1, 3, 5) and rng.random() < 0.85:
            km = round((4 + 4 * progress) * rng.uniform(0.75, 1.3) * (1.6 if day.weekday() == 5 else 1), 2)
            pace = 430 - 50 * progress + rng.uniform(-15, 15)
            start = dt.datetime.combine(day, dt.time(18, 30), JST) + dt.timedelta(minutes=rng.randint(0, 50))
            out.append(workout(start, km, pace, rng, vehicle=(end - day).days == 12))
        day += dt.timedelta(days=1)
    # 屋内ランは取り込まれない (確認用)
    out.append(workout(dt.datetime.combine(end - dt.timedelta(days=3), dt.time(7, 0), JST), 3, 400, rng, indoor=True))
    return out


def metrics(today):
    rng = random.Random(7)
    end = dt.date.fromisoformat(today)
    days = [(end - dt.timedelta(days=i)).isoformat() + " 00:00:00 +0900" for i in range(60)][::-1]
    return [
        {"name": "vo2_max", "units": "ml/(kg·min)", "data": [{"date": d, "qty": 40 + i * 0.06 + rng.uniform(-0.3, 0.3)} for i, d in enumerate(days)]},
        {"name": "resting_heart_rate", "units": "count/min", "data": [{"date": d, "qty": 70 - i * 0.05 + rng.uniform(-2, 2)} for i, d in enumerate(days)]},
    ]

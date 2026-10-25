"""AI に渡す数字の計算。数字はすべてここで計算し、Claude には計算させない。

ルールの番号 (R1 など) は docs/coach-guidelines.md に対応する。
- 「集計から外す」にしたランは使わない
- 「要確認」のランは、傾向 (似たランとの比較・最長距離など) には使わない (出力ルール 3)
"""
import datetime as dt
import statistics

EASY_HR = 0.75        # 楽な強度の上限 (最大心拍に対する割合, R1)
HARD_HR = 0.85        # これを超えたらきつい強度
LONG_RUN_LIMIT = 1.1  # 1回の距離の上限 (過去30日の最長に対する倍率, R3)
SIMILAR_RANGE = 0.25  # 「似た距離のラン」とみなす幅 (±25%)
DEFAULT_MAX_HR = 185
# 走らない日の自宅の筋トレ (器具なし・15分ほど, R7)。web/app.js と generator/main.py の STRENGTH と同じ内容にする
STRENGTH = [
    ["スクワット 15回 × 3", "プランク 30秒 × 3", "カーフレイズ 20回 × 2"],
    ["ランジ 左右10回 × 2", "サイドプランク 左右20秒 × 2", "ヒップリフト 15回 × 3"],
]


def day(s):
    return dt.date.fromisoformat(s[:10])


def shift(date, days):
    return (day(date) + dt.timedelta(days=days)).isoformat()


def fmt_pace(sec):
    if not sec:
        return None
    sec = round(sec)
    return f"{sec // 60}:{sec % 60:02d}"


def usable(runs):
    """集計から外したランを除く"""
    return [r for r in runs if not r.get("user_excluded")]


def trusted(runs):
    """傾向に使うラン (要確認も除く)"""
    return [r for r in usable(runs) if not r.get("flags")]


def max_hr(runs):
    """最大心拍。記録の最高値 (R2。年齢が分からないので推定式は使わない)"""
    hrs = [r["max_hr"] for r in usable(runs) if r.get("max_hr")]
    return max(hrs) if hrs else DEFAULT_MAX_HR


def intensity(avg_hr, hr_max):
    if not avg_hr:
        return None
    ratio = avg_hr / hr_max
    label = "楽" if ratio < EASY_HR else "ややきつい" if ratio < HARD_HR else "きつい"
    return {"ratio_of_max_hr": round(ratio * 100), "label": label}


def longest_before(runs, date, days=30):
    """date より前 days 日のうち、いちばん長いラン (要確認は除く)"""
    since = shift(date, -days)
    d = [r["distance_km"] for r in trusted(runs) if since <= r["date"] < date]
    return max(d) if d else None


def split_trend(splits):
    """1km ごとのペースの、前半と後半の比較 (1km に満たない最後の区間は除く)"""
    full = [s for s in splits or [] if s.get("km", 0) >= 0.99]
    if len(full) < 4:
        return None
    half = len(full) // 2
    first = statistics.mean(s["sec"] for s in full[:half])
    second = statistics.mean(s["sec"] for s in full[half:])
    fastest = min(full, key=lambda s: s["sec"])
    slowest = max(full, key=lambda s: s["sec"])
    return {
        "first_half_pace": fmt_pace(first), "second_half_pace": fmt_pace(second),
        "second_half_diff_sec_per_km": round(second - first),  # マイナスなら後半のほうが速い
        "fastest_km": {"km": full.index(fastest) + 1, "pace": fmt_pace(fastest["sec"])},
        "slowest_km": {"km": full.index(slowest) + 1, "pace": fmt_pace(slowest["sec"])},
    }


def hr_drift(hr):
    """心拍の上がり方。最初の5分を除いて、前3分の1と後ろ3分の1の平均を比べる"""
    pts = [v for m, v in hr or [] if m >= 5]
    if len(pts) < 9:
        return None
    third = len(pts) // 3
    first, last = statistics.mean(pts[:third]), statistics.mean(pts[-third:])
    return {"first_third_avg": round(first), "last_third_avg": round(last), "rise": round(last - first)}


def run_history(run, all_runs, metrics):
    """そのランより前の履歴 (コメントを総合的にするため)"""
    hr_max = max_hr(all_runs)
    monday = shift(run["date"], -day(run["date"]).weekday())
    weeks = []
    for i in range(6, -1, -1):  # 6週前〜今週 (今週はこのランの日まで)
        start = shift(monday, -7 * i)
        end = run["date"] if i == 0 else shift(start, 6)
        rs = [r for r in usable(all_runs) if start <= r["date"] <= end]
        weeks.append({"week_of": start, "runs": len(rs), "km": round(sum(r["distance_km"] for r in rs), 1)})
    past6 = weeks[:-1]
    before = [r for r in trusted(all_runs) if r["start"] < run["start"]]
    similar = [r for r in before if r.get("pace_s")
               and abs(r["distance_km"] - run["distance_km"]) <= run["distance_km"] * SIMILAR_RANGE][-6:]
    d30, d90 = shift(run["date"], -30), shift(run["date"], -90)
    recent = [r["pace_s"] for r in before if r["date"] >= d30 and r.get("pace_s")]
    older = [r["pace_s"] for r in before if d90 <= r["date"] < d30 and r.get("pace_s")]
    easy_recent = [r for r in before if r["date"] >= d30 and r.get("avg_hr")]
    return {
        "weekly_last_6_weeks_and_this_week": weeks,
        "avg_km_per_week_last_6": round(statistics.mean(w["km"] for w in past6), 1),
        "avg_runs_per_week_last_6": round(statistics.mean(w["runs"] for w in past6), 1),
        "weeks_with_2_or_more_runs_last_6": sum(1 for w in past6 if w["runs"] >= 2),  # 続けられているか
        "similar_runs_recent": [{"date": r["date"], "km": round(r["distance_km"], 1), "pace": fmt_pace(r["pace_s"]),
                                 "avg_hr": r.get("avg_hr")} for r in similar],  # 古い順
        "median_pace_last_30_days": fmt_pace(statistics.median(recent)) if recent else None,
        "median_pace_31_to_90_days_ago": fmt_pace(statistics.median(older)) if older else None,
        "easy_runs_share_last_30_days_pct": round(sum(1 for r in easy_recent if r["avg_hr"] / hr_max < EASY_HR)
                                                  / len(easy_recent) * 100) if easy_recent else None,
        "vo2_max": metric_trend(metrics or [], "vo2_max", run["date"], 7, 28),
        "resting_heart_rate": metric_trend(metrics or [], "resting_heart_rate", run["date"]),
    }


def run_facts(run, all_runs, metrics=None):
    """走るたびのコメント用の数字 (そのランと、それまでの履歴)"""
    hr_max = max_hr(all_runs)
    before = [r for r in trusted(all_runs) if r["start"] < run["start"]]
    prev = before[-1] if before else None
    since90 = shift(run["date"], -120)
    similar = [r for r in before if r["date"] >= since90 and r.get("pace_s")
               and abs(r["distance_km"] - run["distance_km"]) <= run["distance_km"] * SIMILAR_RANGE]
    sim = None
    if similar:
        med_pace = statistics.median(r["pace_s"] for r in similar)
        hrs = [r["avg_hr"] for r in similar if r.get("avg_hr")]
        sim = {
            "count": len(similar), "median_pace": fmt_pace(med_pace),
            "pace_diff_sec_per_km": round(run["pace_s"] - med_pace) if run.get("pace_s") else None,  # マイナスなら速い
            "median_avg_hr": round(statistics.median(hrs)) if hrs else None,
            "avg_hr_diff": round(run["avg_hr"] - statistics.median(hrs)) if hrs and run.get("avg_hr") else None,
        }
    longest = longest_before(all_runs, run["date"])
    week_since = shift(run["date"], -6)
    week = [r for r in usable(all_runs) if week_since <= r["date"] <= run["date"]]
    return {
        "date": run["date"], "start_time": run["start"][11:16],
        "distance_km": round(run["distance_km"], 2),
        "moving_time_min": round(run["moving_s"] / 60), "elapsed_time_min": round(run["duration_s"] / 60),
        "stopped_min": round(run.get("stopped_s", 0) / 60),
        "pace_per_km": fmt_pace(run.get("pace_s")),
        "avg_hr": run.get("avg_hr"), "max_hr": run.get("max_hr"), "max_hr_on_record": hr_max,
        "intensity": intensity(run.get("avg_hr"), hr_max),
        "elevation_up_m": run.get("elevation_up_m"), "cadence": run.get("cadence"),
        "temperature_c": run.get("temperature_c"), "humidity": run.get("humidity"),
        "splits": split_trend(run.get("splits")),
        "hr_drift": hr_drift(run.get("hr")),
        "similar_runs": sim,
        "days_since_previous_run": (day(run["date"]) - day(prev["date"])).days if prev else None,
        "last_7_days": {"runs": len(week), "km": round(sum(r["distance_km"] for r in week), 1)},
        "distance_spike": {  # R3
            "longest_in_previous_30_days_km": round(longest, 2) if longest else None,
            "ratio": round(run["distance_km"] / longest, 2) if longest else None,
            "over_limit": bool(longest and run["distance_km"] > longest * LONG_RUN_LIMIT),
        },
        "needs_check": [f["text"] for f in run.get("flags") or []],  # 要確認の理由 (あれば数字をあてにしない)
        "notes": run.get("notes") or [],
        "excluded_by_user": bool(run.get("user_excluded")),
        "history": run_history(run, all_runs, metrics),
        "next_run_limits": next_run_limits(run, all_runs),
    }


def next_run_limits(run, all_runs):
    """次のランの提案に使う上限と目安。距離の上限は、このランを含めた過去30日の最長 × 1.1 (R3)"""
    hr_max = max_hr(all_runs)
    since = shift(run["date"], -29)
    recent = [r for r in trusted(all_runs) if since <= r["date"] <= run["date"]]
    longest = max((r["distance_km"] for r in recent), default=None)
    upto = [r for r in trusted(all_runs) if r["start"] <= run["start"]][-5:]
    times = sorted(r["moving_s"] for r in upto)
    paces = sorted(r["pace_s"] for r in upto if r.get("pace_s"))
    return {
        "max_distance_km": round(longest * LONG_RUN_LIMIT, 1) if longest else None,
        "usual_minutes": round(times[len(times) // 2] / 60) if times else None,  # 直近5回の動いていた時間の中央値
        "usual_pace": fmt_pace(paces[len(paces) // 2]) if paces else None,
        "easy_hr_max": round(hr_max * EASY_HR),  # 楽な強度の上限 (R1)
        "hard_hr_range": [round(hr_max * 0.85), round(hr_max * 0.92)],  # ややきつい練習をするときの目安
    }


def _avg(values):
    values = [v for v in values if v is not None]
    return round(statistics.mean(values), 1) if values else None


def metric_trend(metrics, name, today, recent_days=7, base_days=28):
    """健康データの直近の平均と、その前の期間の平均"""
    rows = [m for m in metrics if m["name"] == name]
    if not rows:
        return None
    recent_since, base_since = shift(today, -recent_days), shift(today, -recent_days - base_days)
    recent = _avg(m["qty"] for m in rows if m["date"] >= recent_since)
    base = _avg(m["qty"] for m in rows if base_since <= m["date"] < recent_since)
    return {"recent_avg": recent, "previous_avg": base,
            "change": round(recent - base, 1) if recent is not None and base is not None else None,
            "latest": rows[-1]["qty"], "latest_date": rows[-1]["date"]}


def week_summary(runs, start, hr_max):
    end = shift(start, 6)
    rs = [r for r in usable(runs) if start <= r["date"] <= end]
    km = sum(r["distance_km"] for r in rs)
    moving = sum(r["moving_s"] for r in rs)
    easy = [r for r in rs if r.get("avg_hr") and r["avg_hr"] / hr_max < EASY_HR]
    easy_time = sum(r["moving_s"] for r in easy)
    return {
        "start": start, "end": end, "runs": len(rs), "km": round(km, 1),
        "moving_time_min": round(moving / 60), "avg_pace": fmt_pace(moving / km) if km else None,
        "easy_time_share_pct": round(easy_time / moving * 100) if moving else None,  # R1
        "longest_km": round(max((r["distance_km"] for r in rs), default=0), 2),
        "weekdays": sorted({"月火水木金土日"[day(r["date"]).weekday()] for r in rs}, key="月火水木金土日".index),
    }


def weekly_facts(runs, metrics, today, goals=None):
    """月曜の週次レポート用の数字。today (月曜) の前の週 (月〜日) が対象"""
    hr_max = max_hr(runs)
    this_monday = shift(today, -day(today).weekday())
    last_week = shift(this_monday, -7)
    week = week_summary(runs, last_week, hr_max)
    prev4 = [week_summary(runs, shift(last_week, -7 * i), hr_max) for i in range(1, 5)]
    spikes = []
    for r in trusted(runs):
        if last_week <= r["date"] <= week["end"]:
            longest = longest_before(runs, r["date"])
            if longest and r["distance_km"] > longest * LONG_RUN_LIMIT:
                spikes.append({"date": r["date"], "km": round(r["distance_km"], 2),
                               "longest_before_km": round(longest, 2), "ratio": round(r["distance_km"] / longest, 2)})
    # ふだん走る曜日 (直近8週)
    since8 = shift(this_monday, -56)
    counts = {}
    for r in usable(runs):
        if since8 <= r["date"] < this_monday:
            w = "月火水木金土日"[day(r["date"]).weekday()]
            counts[w] = counts.get(w, 0) + 1
    longest30 = longest_before(runs, this_monday)
    weeks_with_runs = [w["runs"] for w in prev4 + [week] if w["runs"]]
    month = this_monday[:7]
    goal = None
    if goals:
        past = sorted(m for m in goals if m <= month)
        goal = goals[past[-1]] if past else None
    month_km = round(sum(r["distance_km"] for r in usable(runs) if r["date"].startswith(month) and r["date"] < this_monday), 1)
    return {
        "today": today, "max_hr_on_record": hr_max,
        "last_week": week,
        "previous_4_weeks": prev4,
        "previous_4_weeks_avg_km": _avg(w["km"] for w in prev4),
        "distance_spikes_last_week": spikes,
        "typical_weekdays_last_8_weeks": counts,
        "needs_check_runs_last_week": [{"date": r["date"], "reasons": [f["text"] for f in r["flags"]]}
                                       for r in usable(runs) if last_week <= r["date"] <= week["end"] and r.get("flags")],
        "health": {
            "vo2_max": metric_trend(metrics, "vo2_max", today, 7, 28),
            "resting_heart_rate": metric_trend(metrics, "resting_heart_rate", today),
            "heart_rate_variability": metric_trend(metrics, "heart_rate_variability", today),
        },
        "this_month": {"month": month, "goal_km": goal, "km_so_far": month_km},
        "next_week_limits": {  # メニューはこの範囲で作る
            "long_run_max_km": round(longest30 * LONG_RUN_LIMIT, 1) if longest30 else None,  # R3
            "longest_in_last_30_days_km": round(longest30, 2) if longest30 else None,
            "suggested_runs": round(statistics.median(weeks_with_runs)) if weeks_with_runs else 2,
            "hard_sessions_max": 1 if (statistics.median(weeks_with_runs) if weeks_with_runs else 0) >= 3 else 0,  # R1・R6
            "easy_hr_max": round(hr_max * EASY_HR),
        },
        # 走らない日の筋トレ (R7)。ホームの「今日のおすすめ」と同じメニュー
        "strength_menus": {"A": STRENGTH[0], "B": STRENGTH[1]},
    }

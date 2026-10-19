"""通知 (今日のおすすめ・月末の振り返り・目標から遅れているとき・データが届かないとき)。

使い方:
  python generator/main.py daily     # 毎朝: 1日なら先月の振り返り、データが3日届いていなければ警告
  python generator/main.py weekly    # 日曜の夜: 今月の目標から遅れていれば、次のロング走の安全な上限とあわせてお知らせ
  python generator/main.py plan      # 毎日 18:00: 今日のおすすめ (今日もう走っていれば送らない)
  python generator/main.py comments          # 7〜23時の毎時: まだコメントのない直近のランに AI のコメントを付ける
  python generator/main.py comments --count  # コメント待ちの件数だけ表示 (Claude は使わない)
  python generator/main.py report            # 月曜の朝: 先週の振り返りと今週のメニュー (AI)
  python generator/main.py report --force    # 作成済みでも作り直す
  python generator/main.py daily --dry-run   # 通知せず、送る内容を表示するだけ

予備の定期実行でも同じ通知を二度送らないよう、送った通知はアプリ側に記録する (/api/internal/notified)。
"""
import argparse
import calendar
import datetime as dt
import json
import sys

import llm
import push
import stats
from store import Store

STALE_DAYS = 3
COMMENT_DAYS = 3        # この日数より前のランにはコメントを付けない (古いランを取り込んだとき大量に作らないため)
COMMENTS_PER_RUN = 3    # 1回の実行でコメントを付けるランの数 (残りは次の回)
HISTORY_DAYS = 180      # 比較に使う過去のランの日数


def month_label(m):
    return f"{int(m[5:])}月"


def prev_month(m):
    y, mm = int(m[:4]), int(m[5:])
    return f"{y - 1}-12" if mm == 1 else f"{y}-{mm - 1:02d}"


def monthly_message(months, month):
    """先月の振り返り: 「10月：87.3km（目標100km・達成率87%）。先月より+12.0km」"""
    cur = months.get(month) or {"km": 0, "runs": 0, "goal": None}
    before = months.get(prev_month(month)) or {"km": 0}
    text = f"{month_label(month)}：{cur['km']:.1f}km（{cur['runs']}回"
    if cur.get("goal"):
        text += f"・目標{cur['goal']:g}km・達成率{round(cur['km'] / cur['goal'] * 100)}%"
    diff = cur["km"] - before["km"]
    text += f"）。先月より{'+' if diff >= 0 else '−'}{abs(diff):.1f}km"
    if cur.get("goal") and cur["km"] >= cur["goal"]:
        text = "🎉 目標達成！ " + text
    return text


LONG_RUN_LIMIT = 1.1  # 次のロング走は、過去30日の最長のこの倍まで (docs/coach-guidelines.md の R3)


def long_run_text(longest_30d_km):
    """次のロング走の安全な上限の一文"""
    if not longest_30d_km:
        return "この30日ほど走っていないので、短い距離から少しずつ再開しましょう。"
    return (f"次のロング走は{longest_30d_km * LONG_RUN_LIMIT:.1f}kmまでが安全な目安です"
            f"（過去30日の最長{longest_30d_km:.1f}km×1.1。急に伸ばすとけがのリスクが上がります）。")


def behind_message(months, today, longest_30d_km):
    """今月の目標から遅れていれば、残りの距離と次のロング走の安全な上限を返す (順調なら None)。
    月末に距離を詰め込むのはけがのもとなので、1日あたりの目安は出さない (docs/coach-guidelines.md の R3・R4)"""
    month = today[:7]
    cur = months.get(month) or {"km": 0, "goal": None}
    goal = cur.get("goal")
    if not goal or cur["km"] >= goal:
        return None
    d = dt.date.fromisoformat(today)
    dim = calendar.monthrange(d.year, d.month)[1]
    expected = goal * d.day / dim  # 今日の終わりまでに走っておきたい距離
    if cur["km"] >= expected:
        return None
    if d.day >= dim:  # 月の最終日は、もう取り戻す日がないので送らない
        return None
    return (f"今月の目標まであと{goal - cur['km']:.1f}km（今日までの目安より{expected - cur['km']:.1f}km遅れ）。"
            "無理に取り戻そうとせず、楽なペースを中心に続けましょう。" + long_run_text(longest_30d_km))


EASY_HR = 0.75  # 楽な強度の上限 (最大心拍に対する割合。docs/coach-guidelines.md の R1)


def today_plan(status):
    """今日のおすすめ (title, note)。web/app.js の todayPlan と同じ判定。今日もう走っていれば None。
    内部 API は直近30日のランしか返さないので、それより前に最後に走った人は「しばらく空いた」として扱う"""
    today = dt.date.fromisoformat(status["today"])
    runs = status.get("recent_runs") or []
    easy_hr = round((status.get("max_hr") or 185) * EASY_HR)
    if not runs:
        if any(m.get("runs") for m in status["months"].values()):
            return "短めに 20分", "しばらく空いたので、楽なペースで体を慣らしましょう。"
        return "まずは 20分", "会話できるくらいの楽なペースで。歩きを混ぜても大丈夫です。"
    last = runs[0]
    gap = (today - dt.date.fromisoformat(last["date"])).days
    if gap <= 0:
        return None
    if gap >= 14:
        return "短めに 20分", f"{gap}日ぶりなので、楽なペースで体を慣らしましょう。"
    longest = max(r["distance_km"] for r in runs)
    if gap == 1 and last["distance_km"] >= longest * 0.9:
        return "休養日", "昨日は長めに走ったので、今日は休むか散歩くらいに。"
    times = sorted(r["moving_s"] for r in runs[:5])
    minutes = max(20, round(times[len(times) // 2] / 60 / 5) * 5)
    return f"楽に {minutes}分", f"前回から{gap}日空き。心拍{easy_hr}以下を目安に。"


def send_once(store, key, title, body, dry_run):
    if dry_run:
        print(f"[dry-run] {key}: {title} / {body}")
        return
    if key in store.notified():
        print(f"{key} は送信済みなのでスキップします")
        return
    push.send(store, title, body)
    store.mark_notified(key)


def daily(store, status, dry_run):
    today = status["today"]
    # 毎月1日: 先月の振り返り
    if today.endswith("-01"):
        m = prev_month(today[:7])
        send_once(store, f"monthly-{m}", "先月の振り返り", monthly_message(status["months"], m), dry_run)
    # データが届いていない
    last = status.get("last_ingest")
    if last:
        at = dt.datetime.fromisoformat(last["at"].replace("Z", "+00:00"))
        days = (dt.datetime.now(dt.timezone.utc) - at).total_seconds() / 86400
        if days >= STALE_DAYS:
            send_once(store, f"stale-{last['at']}", "データが届いていません",
                      f"{int(days)}日間、Health Auto Export からデータが届いていません。"
                      "iPhone でアプリを開いて、手動で送ってみてください。", dry_run)
        else:
            print(f"データは {days:.1f} 日前に届いています")
    else:
        print("まだ一度もデータが届いていません (通知はしません)")


def weekly(store, status, dry_run):
    msg = behind_message(status["months"], status["today"], status.get("longest_30d_km"))
    if not msg:
        print("目標どおり、または目標が未設定なので通知しません")
        return
    send_once(store, f"behind-{status['today']}", "今月の進み具合", msg, dry_run)


def plan(store, status, dry_run):
    p = today_plan(status)
    if not p:
        print("今日はもう走っているので通知しません")
        return
    title, note = p
    send_once(store, f"plan-{status['today']}", f"今日のおすすめ：{title}", note, dry_run)


def pending_comments(runs, today):
    since = stats.shift(today, -COMMENT_DAYS)
    return [r for r in runs if r["date"] >= since and not r.get("commented") and not r.get("user_excluded")]


def comments(store, status, args):
    today = status["today"]
    runs = store.runs(stats.shift(today, -HISTORY_DAYS))
    pending = pending_comments(runs, today)
    if args.count:
        print(len(pending))
        return
    if not pending and args.dry_run:
        # 試し実行で待ちがなければ、いちばん新しいランで文章だけ作ってみる
        pending = [r for r in stats.usable(runs)][-1:]
    if not pending:
        print("コメント待ちのランはありません")
        return
    for run in sorted(pending, key=lambda r: r["start"])[:COMMENTS_PER_RUN]:
        facts = stats.run_facts(run, runs)
        print(f"{run['date']} {run['distance_km']}km のコメントを作ります")
        out = llm.run_comment(facts)
        comment = {**out, "facts": facts, "created_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        if args.dry_run:
            print(json.dumps(out, ensure_ascii=False, indent=1))
            continue
        store.put_comment(run["id"], comment)
        push.send(store, f"{run['distance_km']:.1f}km のコメント", out["comment"], url=f"./#run/{run['id']}", tag=f"comment-{run['id']}")


def report(store, status, args):
    today = status["today"]
    if not args.force and not args.dry_run and today in store.report_ids():
        print(f"{today} のレポートは作成済みなのでスキップします")
        return
    runs = store.runs(stats.shift(today, -HISTORY_DAYS))
    metrics = store.metrics(stats.shift(today, -60))
    goals = {m: v["goal"] for m, v in status["months"].items() if v.get("goal")}
    facts = stats.weekly_facts(runs, metrics, today, goals)
    if not facts["last_week"]["runs"] and not facts["previous_4_weeks_avg_km"]:
        print("この5週間ランがないのでレポートは作りません")
        return
    print(f"{facts['last_week']['start']}〜{facts['last_week']['end']} のレポートを作ります")
    out = llm.weekly_report(facts)
    rep_ = {"id": today, "created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "facts": facts, **out}
    if args.dry_run:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    store.put_report(rep_)
    push.send(store, "今週のレポート", out["headline"], url="./#report", tag="report")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["daily", "weekly", "plan", "comments", "report"])
    ap.add_argument("--dry-run", action="store_true", help="通知・保存せず、内容を表示するだけ")
    ap.add_argument("--count", action="store_true", help="comments: コメント待ちの件数だけ表示")
    ap.add_argument("--force", action="store_true", help="report: 作成済みでも作り直す")
    args = ap.parse_args()
    store = Store()
    status = store.status()
    print(f"today={status['today']} months={len(status['months'])}")
    if args.command == "daily":
        daily(store, status, args.dry_run)
    elif args.command == "comments":
        comments(store, status, args)
    elif args.command == "report":
        report(store, status, args)
    elif args.command == "plan":
        plan(store, status, args.dry_run)
    else:
        weekly(store, status, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())

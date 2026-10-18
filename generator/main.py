"""通知 (月末の振り返り・目標から遅れているとき・データが届かないとき)。

使い方:
  python generator/main.py daily     # 毎朝: 1日なら先月の振り返り、データが3日届いていなければ警告
  python generator/main.py weekly    # 日曜の夜: 今月の目標から遅れていればお知らせ
  python generator/main.py daily --dry-run   # 通知せず、送る内容を表示するだけ

予備の定期実行でも同じ通知を二度送らないよう、送った通知はアプリ側に記録する (/api/internal/notified)。
"""
import argparse
import calendar
import datetime as dt
import sys

import push
from store import Store

STALE_DAYS = 3


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


def behind_message(months, today):
    """今月の目標から遅れていれば、残りの距離と1日あたりの目安を返す (順調なら None)"""
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
    remaining = goal - cur["km"]
    days_left = dim - d.day  # 日曜の夜に送るので、今日は数えない
    if days_left <= 0:
        return None
    return (f"今月の目標まであと{remaining:.1f}km。残り{days_left}日、1日平均{remaining / days_left:.1f}kmで達成です"
            f"（今日までの目安より{expected - cur['km']:.1f}km遅れ）")


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
    msg = behind_message(status["months"], status["today"])
    if not msg:
        print("目標どおり、または目標が未設定なので通知しません")
        return
    send_once(store, f"behind-{status['today']}", "目標から少し遅れています", msg, dry_run)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["daily", "weekly"])
    ap.add_argument("--dry-run", action="store_true", help="通知せず、送る内容を表示するだけ")
    args = ap.parse_args()
    store = Store()
    status = store.status()
    print(f"today={status['today']} months={len(status['months'])}")
    if args.command == "daily":
        daily(store, status, args.dry_run)
    else:
        weekly(store, status, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())

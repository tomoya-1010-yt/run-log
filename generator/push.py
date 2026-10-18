"""Web Push 通知を送る。"""
import json
import os
import sys

from pywebpush import WebPushException, webpush


def send(store, title, body, url="./", tag="run"):
    private_key = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    subject = os.environ.get("VAPID_SUBJECT", "").strip()
    if not private_key:
        print("VAPID_PRIVATE_KEY が空なので通知は送りません", file=sys.stderr)
        return
    if not subject.startswith(("mailto:", "https:")):
        sys.exit(f"Variable VAPID_SUBJECT が不正です ({subject!r})。mailto:自分のメールアドレス を登録してください")

    subs = store.subscriptions()
    if not subs:
        print("通知の登録がまだありません (アプリの設定タブで通知をオンにしてください)")
        return
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag}, ensure_ascii=False)
    for sub in subs:
        try:
            webpush(sub, payload, vapid_private_key=private_key,
                    vapid_claims={"sub": subject}, ttl=6 * 3600)
            print("sent:", sub["endpoint"][:60], "...")
        except WebPushException as e:
            status = e.response.status_code if e.response is not None else None
            if status in (404, 410):  # 端末側で登録が消えている
                store.delete_subscription(sub["endpoint"])
                print("removed expired subscription")
            else:
                print("push failed:", e, file=sys.stderr)

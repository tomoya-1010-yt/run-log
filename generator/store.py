"""アプリ側 (Cloudflare Pages Functions) の内部 API クライアント。"""
import os
import sys

import requests


def _env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"{name} が空です。GitHub の Settings → Secrets and variables → Actions に"
                 " 登録されているか確認してください")
    return value


class Store:
    def __init__(self):
        self.base = _env("APP_URL").rstrip("/") + "/api/internal"
        self.headers = {"Authorization": f"Bearer {_env('INGEST_TOKEN')}"}

    def _req(self, method, path, **kwargs):
        res = requests.request(method, self.base + path, headers=self.headers, timeout=30, **kwargs)
        res.raise_for_status()
        return res.json() if res.content else None

    def status(self):
        """{today, last_ingest, months: {"2026-10": {km, runs, goal}}}"""
        return self._req("GET", "/status")

    def notified(self):
        return self._req("GET", "/notified")

    def mark_notified(self, key):
        return self._req("PUT", "/notified", json={"key": key})

    def subscriptions(self):
        return self._req("GET", "/subscriptions")

    def delete_subscription(self, endpoint):
        return self._req("DELETE", "/subscriptions", json={"endpoint": endpoint})

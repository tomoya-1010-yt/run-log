"""Health Auto Export で手動で書き出したファイルを、ランログにまとめて送る (過去の記録の取り込み用)。

書き出しは「形式: JSON」で。zip のままでも JSON ファイルでもよい。
Cloudflare の処理時間の上限にかからないよう、ワークアウトは 1 件ずつ送る。

  python scripts/import_hae.py --url https://run-log.pages.dev --token <HAE_TOKEN> HealthAutoExport_xxx.zip
  python scripts/import_hae.py --url http://localhost:8789 --token dev HealthAutoExport_xxx.zip   # ローカル確認用
"""
import argparse
import json
import os
import sys
import zipfile

import requests


def load(path):
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".json")]
            if not names:
                sys.exit(f"{path} に JSON がありません。Health Auto Export の書き出し形式を JSON にしてください")
            return [json.loads(z.read(n).decode("utf-8")) for n in names]
    with open(path, encoding="utf-8") as f:
        return [json.load(f)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--url", required=True, help="ランログの URL")
    ap.add_argument("--token", default=os.environ.get("HAE_TOKEN"), help="HAE_TOKEN (環境変数でも可)")
    args = ap.parse_args()
    if not args.token:
        sys.exit("--token か環境変数 HAE_TOKEN を指定してください")
    url = args.url.rstrip("/") + "/api/ingest"
    headers = {"Authorization": f"Bearer {args.token}"}

    def post(data):
        res = requests.post(url, json={"data": data}, headers=headers, timeout=60)
        if res.status_code == 401:
            sys.exit("トークンが違います (401)")
        res.raise_for_status()
        return res.json()

    runs = skipped = metrics = 0
    for path in args.files:
        for doc in load(path):
            data = doc.get("data") or {}
            workouts = data.get("workouts") or []
            for i, w in enumerate(workouts, 1):
                r = post({"workouts": [w]})
                runs += r["runs"]
                skipped += r["skipped"]
                print(f"\r{path}: ワークアウト {i}/{len(workouts)}", end="", flush=True)
            if workouts:
                print()
            if data.get("metrics"):
                metrics += post({"metrics": data["metrics"]})["metrics"]
    print(f"取り込み: 屋外ラン {runs} 件 (ラン以外・屋内 {skipped} 件は対象外)、健康データ {metrics} 件")


if __name__ == "__main__":
    main()

# ランログ

Apple Watch で記録した屋外ランを自動で取り込み、月の目標の進み具合・地図・1kmごとのペース・
週や月の距離のグラフを見られる、自分専用の PWA (肌ログと同じ構成)。

## 仕組み

```
Apple Watch (純正ワークアウトアプリ) → iPhone のヘルスケア
        ↓ Health Auto Export の自動送信 (REST API・JSON・Authorization: Bearer HAE_TOKEN)
Cloudflare Pages Functions (functions/api) + D1 (migrations/)
  - POST /api/ingest: 屋外ランだけを取り出し、shared/analyze.js で
      止まっていた時間・乗り物らしい区間・GPS の途切れを調べて保存する
        ↑ 合言葉でログイン (Cookie)
iPhone のホーム画面アプリ (web/)
  ホーム: 今月の目標の進み具合・ペース配分の目安・最近のラン
  記録:   全ランの一覧 → 詳細 (地図・1kmごとのペース・心拍・「集計から外す」)
  グラフ: 週ごと・月ごと (去年の同じ月と比較) の距離、平均ペースの推移
        ↑ 内部 API (INGEST_TOKEN)
GitHub Actions (.github/workflows/notify.yml・ai.yml)
  - 7〜23時の毎時 (ai.yml): 直近3日のまだコメントのないランに、AI (Claude) が2〜3行のコメントを付けて通知する。
    コメント待ちがなければ Claude は動かさない
  - 月曜 7:30 JST (予備 8:30, ai.yml): 先週の振り返りと今週のおすすめメニュー (AI)
    - AI に渡すのは generator/stats.py で計算した数字だけ (GPS の位置は渡さない)。指針は docs/coach-guidelines.md
  - 毎日 18:00 JST (予備 18:30): 今日のおすすめ (ホームと同じ判定。今日もう走っていれば送らない)
    ふだん走らない曜日 (直近8週) は、自宅でできる筋トレ (スクワット・体幹など 15分) をすすめる (R7)
  - 毎朝 7:00 JST (予備 8:00): 1日なら先月の振り返り、データが3日届いていなければ警告
  - 日曜 22:30 JST (予備 23:15): 今月の目標から遅れていればお知らせ。距離の詰め込みはすすめず、
    次のロング走の安全な上限 (過去30日の最長 × 1.1) を示す (docs/coach-guidelines.md の R3)
```

### 取り込みのルール (shared/analyze.js)
- 対象は**屋外のランニングだけ**。ウォーキング・室内ランは届いても取り込まない
- 同じランが何度届いても 1 件 (ヘルスケアのワークアウト ID で上書き)。「集計から外す」の設定は残る
- **ペースは「動いていた時間」で計算する**。速度が 0.6m/s 未満の区間と、GPS が 10 秒以上途切れた区間は「止まっていた」とみなす
- 時速 25km を超える区間は、GPS のずれか乗り物とみなして距離に含めない (30 秒以上続いたときだけ。短いぶれは無視)
- 次のどれかに当たるランには「**要確認**」の印を付ける。おかしければ詳細画面の「集計から外す」で目標・グラフから外せる
  - 時速 25km を超える区間が 30 秒以上ある
  - GPS が 5 分以上途切れている
  - 平均心拍が 115 未満
  - ルートがない
- 1km ごとのペースは Apple が計算したもの (フィットネスアプリと同じ数字) を使う
- 健康データ (VO2 Max・安静時心拍・心拍変動・走り方のデータ) は、第2弾の AI の分析用に保存だけしておく

## 初回セットアップ

### 1. Health Auto Export
iPhone に [Health Auto Export](https://apps.apple.com/app/id1115567069) を入れ、ヘルスケアの読み取りを許可する
(**ワークアウト・ワークアウトのルート・心拍数・ウォーキング＋ランニングの距離** は必ずオン)。
自動送信 (REST API) は有料プランの機能。

### 2. Cloudflare
1. https://dash.cloudflare.com → Storage & Databases → D1 → Create → 名前 `run-log` で作成し、
   Database ID を `wrangler.toml` の `database_id` に貼る
2. API トークンとアカウント ID は肌ログと同じものを使える
   (権限: Account / Cloudflare Pages / Edit と Account / D1 / Edit)

### 3. 通知用の鍵
```
python scripts/gen_vapid.py
```
- `VAPID_PUBLIC_KEY` → `wrangler.toml` の `[vars]` に貼る (公開してよい)
- `VAPID_PRIVATE_KEY` → GitHub の Secret に登録 (公開しない)

### 4. GitHub
リポジトリを **Private** で作って push し、Settings → Secrets and variables → Actions に登録する。

| 種類 | 名前 | 値 |
|---|---|---|
| Secret | `APP_PASSPHRASE` | アプリを開くときの合言葉。推測されにくい長めの文にする (例: 単語を4つ以上つなげる) |
| Secret | `HAE_TOKEN` | Health Auto Export がデータを送るときの認証用 (ランダムな長い文字列。送ることしかできない) |
| Secret | `INGEST_TOKEN` | 通知スクリプトがアプリのデータを読むときの認証用 (ランダムな長い文字列。HAE_TOKEN とは別にする) |
| Secret | `CLOUDFLARE_API_TOKEN` | Cloudflare の API トークン |
| Secret | `CLOUDFLARE_ACCOUNT_ID` | Cloudflare のアカウント ID |
| Secret | `VAPID_PRIVATE_KEY` | 手順3 の秘密鍵 |
| Secret | `CLAUDE_CODE_OAUTH_TOKEN` | AI のコメント・レポート用。肌ログと同じトークンを使える (新しく作るなら `claude setup-token`。有効期限1年) |
| Variable | `APP_URL` | `https://run-log-8ia.pages.dev` (デプロイ後に表示される URL。run-log はほかの人が使っていたので末尾が付いた) |
| Variable | `VAPID_SUBJECT` | `mailto:自分のメールアドレス` |

ランダムな文字列は次で作れる。
```
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

push すると `アプリのデプロイ` が動き、D1 のテーブル作成・合言葉とトークンの登録・デプロイまで行う。

### 5. Health Auto Export の自動送信
Health Auto Export の「オートメーション」で新規作成し、次のように設定する (アプリの設定画面にも同じ手順がある)。

| 項目 | 値 |
|---|---|
| 種類 | REST API |
| URL | `https://run-log-8ia.pages.dev/api/ingest` |
| ヘッダー | `Authorization` = `Bearer （HAE_TOKEN の値）` |
| データ | ワークアウト (ルートを含める)。健康データも送るなら VO2 Max・安静時心拍・心拍変動・ランニング関係 |
| 形式 | JSON |
| 期間 | 前回の送信以降 (または直近数日) |

走り終わった直後に送りたいときは、iPhone のショートカットで「Apple Watch のワークアウトが終了したとき」→
Health Auto Export の書き出しを実行するオートメーションを作る (「すぐに実行」にする)。

### 6. 過去の記録の取り込み
Health Auto Export で期間を指定して **JSON** で手動書き出しし、PC から送る。
Cloudflare の処理時間の上限にかからないよう、ワークアウトは 1 件ずつ送る。
```
python scripts/import_hae.py --url https://run-log-8ia.pages.dev --token <HAE_TOKEN> HealthAutoExport_xxxx.zip
```

### 7. iPhone
Safari で `APP_URL` を開いて合言葉を入力 → 共有ボタン →「ホーム画面に追加」。
追加したアプリの設定 (右上の歯車) で通知をオンにする。

## ローカルで画面を確認する

```
python dev/server.py
```
http://localhost:8789 を開き、合言葉 `dev` で入る。ダミーのラン (鶴ヶ城のまわり、直近14か月分) が入っている。
データはメモリ上だけで、止めると消える。

本物の書き出しで試すとき:
```
python dev/server.py --empty
python scripts/import_hae.py --url http://localhost:8789 --token dev HealthAutoExport_xxxx.zip
```

### 分析処理を変えるとき
本番は `shared/analyze.js` (JavaScript)、ローカルのサーバーは `dev/analyze.py` (同じ処理の Python 版) を使う。
片方を変えたらもう片方も同じように変え、http://localhost:8789/dev/check.html で
「20件中 20件が一致」になることを確かめる (ブラウザで JavaScript 版を動かして Python 版と比べるページ)。

### API を変えるとき
http://localhost:8789/dev/worker.html で、本番の `functions/api` と `migrations/` を
ブラウザ上の SQLite (sql.js) を D1 の代わりにして動かし、受け取り・ログイン・集計を確かめられる (「すべて成功」になればよい)。
Node.js を入れなくても試せるようにするためのページ。

### AI のコメント・レポートを試す
Claude を呼ばずに、決まった文章で保存・通知・画面までの流れを確かめられる (`RUN_LLM_STUB=1`)。
```
RUN_LLM_STUB=1 APP_URL=http://localhost:8789 INGEST_TOKEN=dev python generator/main.py comments
RUN_LLM_STUB=1 APP_URL=http://localhost:8789 INGEST_TOKEN=dev python generator/main.py report
```
本物の Claude の文章は、GitHub の Actions →「AI コメントと週次レポート」→ Run workflow (dry_run はオンが初期値) で、
保存・通知せずに確かめられる。

### 通知を試す
```
APP_URL=http://localhost:8789 INGEST_TOKEN=dev python generator/main.py daily --dry-run
APP_URL=http://localhost:8789 INGEST_TOKEN=dev python generator/main.py weekly --dry-run
```
GitHub の Actions → 通知 → Run workflow でも、送る内容だけを確かめられる (dry_run はオンが初期値)。

## 第2弾以降
- 第2弾: AI (走るたびのコメント、月曜の週次レポートとおすすめメニュー) — 実装済み
  - AI が従う指針 (科学的根拠の強さつき) は [docs/coach-guidelines.md](docs/coach-guidelines.md)
- 第3弾: シューズの走行距離 (ヘルスケアにはシューズの情報がないので、ランログで登録する)

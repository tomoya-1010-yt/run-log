-- 屋外ラン 1 回につき 1 行。body は shared/analyze.js の analyzeWorkout が返す run の JSON
CREATE TABLE runs (
  id TEXT PRIMARY KEY,           -- ヘルスケアのワークアウト ID (同じランが何度届いても 1 行)
  date TEXT NOT NULL,            -- 走った日 (YYYY-MM-DD)
  distance_km REAL NOT NULL,
  flagged INTEGER NOT NULL,      -- 1 = 要確認 (乗り物らしい区間・GPS の途切れなど)
  user_excluded INTEGER NOT NULL DEFAULT 0, -- 1 = 利用者が「集計から外す」にした
  body TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX runs_date ON runs (date);

-- 地図用のルート。大きいので runs とは分けて、詳細を開いたときだけ読む
CREATE TABLE routes (
  id TEXT PRIMARY KEY,
  body TEXT NOT NULL             -- { t: [経過秒], lat: [], lon: [], alt: [], v: [速度 m/s] }
);

-- 1日ごとの健康データ (VO2 Max・安静時心拍など)。第2弾の AI の分析で使う
CREATE TABLE daily_metrics (
  date TEXT NOT NULL,
  name TEXT NOT NULL,
  qty REAL NOT NULL,
  units TEXT NOT NULL,
  PRIMARY KEY (date, name)
);

-- 月の目標・最後にデータが届いた日時・送った通知など
CREATE TABLE settings (
  key TEXT PRIMARY KEY,
  body TEXT NOT NULL
);

-- プッシュ通知の登録
CREATE TABLE push_subs (
  endpoint TEXT PRIMARY KEY,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);

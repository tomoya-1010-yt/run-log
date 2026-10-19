-- 走るたびの AI コメント。body は generator/llm.py の COMMENT_SCHEMA の JSON
CREATE TABLE comments (
  run_id TEXT PRIMARY KEY,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- 月曜の週次レポート。body は集計 (facts) と AI の文章 (generator/llm.py の REPORT_SCHEMA) の JSON
CREATE TABLE reports (
  id TEXT PRIMARY KEY,           -- 作成日 (YYYY-MM-DD)
  created_at TEXT NOT NULL,
  body TEXT NOT NULL
);

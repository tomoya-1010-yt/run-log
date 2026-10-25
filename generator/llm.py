"""Claude の呼び出し (走るたびのコメント、月曜の週次レポート)。

Claude Code の CLI (claude -p) を使う。GitHub Actions では CLAUDE_CODE_OAUTH_TOKEN
(claude setup-token で作ったトークン) で認証し、Claude の契約の範囲で動く (肌ログと同じ方式)。
渡すのは stats.py で計算した数字だけ。GPS の位置は渡さない。
"""
import json
import os
import pathlib
import shutil
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMMENT_MODEL = os.environ.get("RUN_COMMENT_MODEL", "sonnet")
REPORT_MODEL = os.environ.get("RUN_REPORT_MODEL", "opus")
FALLBACK_MODEL = os.environ.get("RUN_FALLBACK_MODEL", "sonnet")

EVIDENCE = ["A", "B", "C", "本人の記録"]

# 言い方 (本人の希望: 甘すぎるより少し厳しめで、やる気が出るように)
TONE = """## 言い方
- 甘すぎず、少し厳しめのコーチとして話す。良くない点はぼかさずに指摘し、何となくほめるだけの言葉
  (「お疲れさまでした」「素晴らしいです」だけ、など) は使わない。
- ほめるときは、数字で裏付けられた具体的な事実だけをほめる。
- 最後は「次はこうすれば伸びる」という具体的な課題で締め、やる気が出るようにする。
- ただし、責めたり、追い込んだりはしない。厳しさは「もっと良くなれる」という前向きな期待として伝える。
- 安全のルール (急に距離を伸ばさない R3、楽な強度を中心に R1、体調が悪ければ休む) は、厳しさより優先する。
  「もっと走れ」「距離を取り戻せ」とは言わない。"""


def guidelines():
    """AI コーチの指針 (docs/coach-guidelines.md)。出典の一覧は長いので渡さない"""
    text = (ROOT / "docs" / "coach-guidelines.md").read_text(encoding="utf-8")
    return text.split("## 動画ごとの評価")[0].strip()


SYSTEM = ("あなたは市民ランナーのコーチです。利用者本人の記録から計算した数字と、"
          "科学的根拠の強さを付けた指針だけを根拠に、日本語で短く具体的に助言します。"
          "医師ではないので診断はしません。指針で × の説は使いません。\n\n" + TONE + "\n\n" + "{guidelines}")


def _stub(schema):
    """ローカル確認用 (RUN_LLM_STUB=1): Claude を呼ばず、決まった文章を返す"""
    if schema is COMMENT_SCHEMA:
        return {"comment": "（試験用）似た距離の過去のランより速く走れています。明日は楽なジョグか休養にしましょう。",
                "evidence": "B", "rules": ["R1"]}
    return {"headline": "（試験用）安定して走れた1週間", "summary": "先週は3回・26km。前の4週の平均とほぼ同じです。",
            "good": [{"text": "3回とも同じくらいのペースで走れています。", "evidence": "本人の記録"}],
            "cautions": [{"text": "楽な強度の割合が低めです。半分以上は会話できるペースに。", "evidence": "B"}],
            "plan": [{"day": "火", "menu": "楽に 40分", "detail": "心拍140以下で。"},
                     {"day": "木", "menu": "楽に 40分", "detail": "心拍140以下で。"},
                     {"day": "土", "menu": "ロング 12km", "detail": "過去30日の最長×1.1 以内。"}],
            "plan_note": "先週と同じ量を、楽な強度中心で続けます。"}


def _claude(prompt, schema, model, effort="medium"):
    """claude -p を実行し、JSON Schema に沿った結果を返す"""
    if os.environ.get("RUN_LLM_STUB"):
        return _stub(schema)
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude コマンドが見つかりません。Claude Code をインストールしてください")
    cmd = [
        exe, "-p", "標準入力の指示に従ってください。",
        "--output-format", "stream-json", "--verbose",
        "--json-schema", json.dumps(schema, ensure_ascii=False),
        "--model", model, "--fallback-model", FALLBACK_MODEL, "--effort", effort,
        "--system-prompt", SYSTEM.replace("{guidelines}", guidelines()),
        "--tools", "",
        "--permission-mode", "dontAsk", "--permission-prompts", "none",
        "--max-turns", "3",
        "--no-session-persistence", "--disable-slash-commands", "--strict-mcp-config",
    ]
    # 空のフォルダで動かし、リポジトリの設定やファイルを読ませない
    with tempfile.TemporaryDirectory() as cwd:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              encoding="utf-8", cwd=cwd, timeout=600)
    result = None
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result":
            result = event
    if result is None:
        raise RuntimeError(f"claude が結果を返しませんでした (exit {proc.returncode}): {proc.stderr[-2000:]}")
    if result.get("is_error"):
        raise RuntimeError(f"claude の実行に失敗しました ({result.get('subtype')}): {result.get('result')}")
    print(f"  claude: {result.get('num_turns')} turns, 参考コスト ${result.get('total_cost_usd', 0):.3f}")
    output = result.get("structured_output")
    if output is None:
        output = json.loads(result.get("result") or "null")
    return output


def _dump(facts):
    return json.dumps(facts, ensure_ascii=False, indent=1)


# ---------- 走るたびのコメント ----------

COMMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "comment": {"type": "string"},
        "evidence": {"type": "string", "enum": EVIDENCE},
        "rules": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["comment", "evidence", "rules"],
    "additionalProperties": False,
}


def run_comment(facts):
    prompt = f"""今日のランについて、本人に向けたコメントを書いてください。
今日のランだけを見るのではなく、history (ここ数週間の距離・回数、似たランのペースの推移、続けられているか、
VO2 Max・安静時心拍の変化) を踏まえて、総合的に評価してください。

## 数字 (Python で計算済み。これ以外の数字を作らない)
{_dump(facts)}

## 書き方
- comment: 3〜4文、全体で180字以内。です・ます調で、言い方のルールに従って少し厳しめに。
  1. 今日のランの具体的な事実を1つ (例: 似た距離の過去のランより速い／後半のペースが落ちた)
  2. 履歴から見た流れを1つ (例: 「ここ3週は週2回を保てている」「似たランのペースが6:40→6:20と上がってきた」
     「先週から距離が落ちている」「楽な強度のランが3割しかない」)。今日のランがその流れの中でどういう位置か
  3. 気づいた課題を1つ (なければ省く)
  4. 次の1回への具体的な提案を1つ (例: 「明日は休むか軽めのジョグを」「次の走らない日は筋トレを」)
- 数字は facts にあるものだけ使う。比べる相手がいない (similar_runs が null など) ときは比較を書かない。
- needs_check があるときは、データがおかしい可能性に触れ、ペースや距離をほめたり注意したりしない。
- distance_spike.over_limit が true なら、急に長く走ったことに必ず触れる (R3)。責める言い方にはしない。
- intensity が「きつい」で、last_7_days.runs が多いときは、次は楽な強度にするよう添える (R1)。
- 体調の不安 (胸の痛み・異常な息切れなど) を示す数字はないので、医療の話はしない。
- comment の本文に「根拠 A」「R1」のような印やルール番号は書かない (分かりにくいため)。
- evidence: 提案の根拠の強さ。指針のルールに基づく提案なら A/B/C、記録の事実だけなら「本人の記録」。
- rules: 使った指針のルール番号 (例: ["R1", "R3"])。使っていなければ空。
"""
    return _claude(prompt, COMMENT_SCHEMA, COMMENT_MODEL, effort="low")


# ---------- 月曜の週次レポート ----------

ITEM = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "evidence": {"type": "string", "enum": EVIDENCE},
    },
    "required": ["text", "evidence"],
    "additionalProperties": False,
}

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "good": {"type": "array", "items": ITEM},
        "cautions": {"type": "array", "items": ITEM},
        "plan": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "day": {"type": "string", "enum": list("月火水木金土日")},
                "menu": {"type": "string"},
                "detail": {"type": "string"},
            },
            "required": ["day", "menu", "detail"],
            "additionalProperties": False,
        }},
        "plan_note": {"type": "string"},
    },
    "required": ["headline", "summary", "good", "cautions", "plan", "plan_note"],
    "additionalProperties": False,
}


def weekly_report(facts):
    prompt = f"""先週 (last_week) の振り返りと、今週のおすすめメニューを作ってください。

## 数字 (Python で計算済み。これ以外の数字を作らない)
{_dump(facts)}

## 書き方
- headline: 先週をひとことで (25字以内)。
- summary: 先週の距離・回数・ペースを、前の4週と比べて2文で。
- good: 良かった点を1〜3個。それぞれ1文。数字を添える。
- cautions: 気をつける点を0〜3個。distance_spikes_last_week があれば必ず入れる (R3)。
  easy_time_share_pct が低い (60% 未満) なら、楽な強度を増やすよう書く (R1)。
  health の安静時心拍が上がっている (change が +3 以上) なら「疲れがたまっているかも」と目安として書く (根拠 C)。
  needs_check_runs_last_week があれば、データがおかしいランがあることに触れる。
- plan: 今週の月〜日のうち、走る日と筋トレの日を曜日順に並べる。
  - 回数は next_week_limits.suggested_runs、曜日は typical_weekdays_last_8_weeks の多い曜日を優先。
  - ほとんどは楽な強度 (心拍 next_week_limits.easy_hr_max 以下)。きつい練習は hard_sessions_max 回まで (R1・R6)。
  - いちばん長いランも next_week_limits.long_run_max_km 以下にする (R3)。null なら短めから。
  - 走らない日のうち2日に、自宅でできる器具なしの筋トレ (15分ほど) を必ず入れる (R7: 週2回)。
    menu は「筋トレ 15分」、detail に種目と回数を書く。種目は strength_menus から選び、2日で違うものにする。
    ロング走の前日と翌日は避ける。それ以外の走らない日は休養 (書かなくてよい)。
  - menu は短く (例:「楽に 40分」「ロング 12km」「坂ダッシュ 8本」)、detail に目安 (心拍・ペース・理由) を1文。
- plan_note: メニュー全体の考え方を1文 (例:「先週長く走ったので、今週は距離を据え置きます」)。
- 月の目標 (this_month) に遅れていても、距離を詰め込む提案はしない (出力ルール 5)。
- 本文に「根拠 A」「R1」のような印やルール番号は書かない (分かりにくいため)。
- evidence: 各項目の根拠の強さ。記録の事実だけなら「本人の記録」。
"""
    return _claude(prompt, REPORT_SCHEMA, REPORT_MODEL, effort="medium")

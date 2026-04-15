"""Regen Learner — レビュー再生成ログからコンテンツ傾向を学習する。

毎朝 6:00 に実行。
- regen_log の承認済み (approved=1) / 却下済み (approved=0) テキストを収集
- LLM に「承認されたテキストの共通パターン」「却下されたテキストの特徴」を分析させる
- 結果を knowledge.py の do/dont ルールとして追記
"""

from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
MIN_SAMPLES = 3  # 最低これだけサンプルがないと分析しない


def run() -> dict:
    result = {"analyzed": 0, "rules_added": 0}
    try:
        from core.db import get_connection
        conn = get_connection()

        # 全期間の regen_log を収集
        approved = conn.execute(
            "SELECT content_type, old_text, new_text, user_comment FROM regen_log WHERE approved=1"
        ).fetchall()
        rejected = conn.execute(
            "SELECT content_type, old_text, new_text, user_comment FROM regen_log WHERE approved=0"
        ).fetchall()
        # user_comment 付きの再生成 (承認待ちも含む) - 特に強いシグナル
        commented = conn.execute(
            "SELECT content_type, old_text, new_text, user_comment FROM regen_log "
            "WHERE user_comment IS NOT NULL AND TRIM(user_comment) != ''"
        ).fetchall()

        if len(approved) + len(rejected) + len(commented) < MIN_SAMPLES:
            print(f"[regen_learner] サンプル不足 ({len(approved)}承認 / {len(rejected)}却下 / {len(commented)}コメント付) → スキップ")
            return result

        result["analyzed"] = len(approved) + len(rejected)
        print(f"[regen_learner] 分析開始: 承認={len(approved)}件 却下={len(rejected)}件 コメント付={len(commented)}件")

        # サンプルテキストを組み立て
        approved_samples = "\n".join(
            f"[{r['content_type']}] 採用: {r['new_text'][:100]}"
            for r in approved[:20]
        )
        rejected_samples = "\n".join(
            f"[{r['content_type']}] 却下前: {r['old_text'][:100]}"
            for r in rejected[:20]
        )
        # ユーザーからの修正指示は明確な教師信号
        comment_samples = "\n".join(
            f"[{r['content_type']}] BEFORE: {(r['old_text'] or '')[:80]}\n"
            f"  USER指示: {r['user_comment']}\n"
            f"  AFTER: {(r['new_text'] or '')[:80]}"
            for r in commented[:15]
        )

        prompt = f"""以下はSNS運用自動化システムの再生成ログ分析です。3種類のデータがあります。

【1. 承認されたテキスト（ユーザーが良いと判断）】
{approved_samples or '(なし)'}

【2. 却下されたテキスト（ユーザーが再生成を選んだ）】
{rejected_samples or '(なし)'}

【3. ユーザーが「こう直して」と明示的に指示した再生成】※最も重要なシグナル
{comment_samples or '(なし)'}

特に (3) のユーザー指示から、ユーザーの好み・回避したい表現を読み取って反映してください。
全体を総合して、今後の生成品質を上げるための知見を最大7件導き出してください。

出力フォーマット（各行）:
DO: （好まれる表現パターン・切り口・トーン）
DONT: （避けるべき特徴・型）

DOまたはDONTのみ出力。説明文不要。"""

        from core.llm.wrapper import call_llm
        raw = call_llm(prompt, task_type="strategy_evolution", temperature=0.3, max_tokens=500).strip()

        from core.learning.knowledge import add_confirmed, add_rejected
        n_total = len(approved) + len(rejected)

        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("DO:"):
                rule = line[3:].strip()
                if rule:
                    add_confirmed(rule, lift=1.0, n=n_total, evidence="regen_log分析")
                    result["rules_added"] += 1
                    print(f"  ✅ DO追加: {rule[:60]}")
            elif line.startswith("DONT:"):
                rule = line[5:].strip()
                if rule:
                    add_rejected(rule, lift=1.0, n=n_total, evidence="regen_log分析")
                    result["rules_added"] += 1
                    print(f"  ❌ DONT追加: {rule[:60]}")

        print(f"[regen_learner] 完了: {result['rules_added']}ルール追加")

    except Exception as e:
        print(f"[regen_learner] エラー: {e}")
        import traceback
        traceback.print_exc()

    return result

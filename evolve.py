"""自己進化スクリプト — autoresearchの核心に相当。

過去の成果データを分析し、program.md と strategy.json を自動更新する。
コンテンツ戦略だけでなく、投稿戦略（フェーズ遷移・無料/有料比率）も自律判断する。
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

ROOT = Path(__file__).parent
from core.paths import program_md_path as _pmp; PROGRAM_MD = _pmp()
from core.paths import strategy_path as _sp; STRATEGY_JSON = _sp()
from core.paths import history_path as _hp; HISTORY_JSON = _hp()

JST = timezone(timedelta(hours=9))


def load_all() -> tuple[str, dict, dict]:
    program = PROGRAM_MD.read_text(encoding="utf-8")
    strategy = json.loads(STRATEGY_JSON.read_text(encoding="utf-8"))
    history = json.loads(HISTORY_JSON.read_text(encoding="utf-8"))
    return program, strategy, history


def check_phase_transition(strategy: dict, history: dict) -> str | None:
    """フェーズ遷移条件を評価し、次のフェーズを返す。遷移不要ならNone。"""
    pub_params = strategy.get("publishing_params", {})
    current_phase = pub_params.get("phase", "trust_building")
    phase_rules = pub_params.get("phase_rules", {})

    summary = history.get("metrics_summary", {})
    total_articles = summary.get("total_articles", 0)
    total_likes = summary.get("total_likes", 0)
    avg_likes = summary.get("avg_likes_per_article", 0)

    phase_order = ["trust_building", "early_monetization", "scaling"]
    current_idx = phase_order.index(current_phase) if current_phase in phase_order else 0

    if current_idx >= len(phase_order) - 1:
        return None  # 最終フェーズ

    rule = phase_rules.get(current_phase, {})
    condition = rule.get("advance_when", "manual")

    if condition == "manual":
        return None

    # 簡易条件評価
    try:
        result = eval(condition, {
            "total_articles": total_articles,
            "total_likes": total_likes,
            "avg_likes_per_article": avg_likes,
        })
        if result:
            return phase_order[current_idx + 1]
    except Exception:
        pass

    return None


def apply_phase_transition(strategy: dict, new_phase: str):
    """フェーズを遷移させ、publishing_paramsを更新する。"""
    pub_params = strategy["publishing_params"]
    old_phase = pub_params["phase"]
    pub_params["phase"] = new_phase

    # 新フェーズのデフォルト値を適用
    phase_rules = pub_params.get("phase_rules", {})
    new_rules = phase_rules.get(new_phase, {})

    for key in ["daily_free_count", "daily_paid_count", "weekly_free_count", "weekly_paid_count"]:
        if key in new_rules:
            pub_params[key] = new_rules[key]

    print(f"フェーズ遷移: {old_phase} → {new_phase}")
    return old_phase, new_phase


def build_analysis_prompt(program: str, strategy: dict, history: dict) -> str:
    """成果データを分析するためのプロンプトを構築する。"""
    articles = history.get("articles", [])
    summary = history.get("metrics_summary", {})
    pub_params = strategy.get("publishing_params", {})

    # 記事一覧（投稿日時込み）
    articles_text = ""
    if articles:
        for a in articles:
            published = a.get("published_at", "")[:16]
            articles_text += (
                f"- 「{a['title']}」 ジャンル:{a.get('genre', '?')} "
                f"PV:{a.get('views', 0)} スキ:{a.get('likes', 0)} "
                f"コメント:{a.get('comments', 0)} 投稿:{published}\n"
            )
    else:
        articles_text = "まだ記事データがありません。\n"

    # タグ別成績集計
    tag_stats = {}
    for a in articles:
        for tag in a.get("tags", []):
            if tag not in tag_stats:
                tag_stats[tag] = {"count": 0, "views": 0, "likes": 0}
            tag_stats[tag]["count"] += 1
            tag_stats[tag]["views"] += a.get("views", 0)
            tag_stats[tag]["likes"] += a.get("likes", 0)

    tag_text = ""
    if tag_stats:
        sorted_tags = sorted(tag_stats.items(), key=lambda x: -x[1]["views"])[:15]
        for tag, s in sorted_tags:
            avg_pv = s["views"] / s["count"] if s["count"] else 0
            tag_text += f"- {tag}: {s['count']}本 PV:{s['views']} スキ:{s['likes']} 平均PV:{avg_pv:.1f}\n"

    # 曜日・時間帯別の成績
    from datetime import datetime
    weekday_stats = {i: {"count": 0, "views": 0, "likes": 0} for i in range(7)}
    hour_stats = {}
    for a in articles:
        pub = a.get("published_at", "")
        if pub:
            try:
                dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                weekday_stats[dt.weekday()]["count"] += 1
                weekday_stats[dt.weekday()]["views"] += a.get("views", 0)
                weekday_stats[dt.weekday()]["likes"] += a.get("likes", 0)
                hour = dt.hour
                if hour not in hour_stats:
                    hour_stats[hour] = {"count": 0, "views": 0, "likes": 0}
                hour_stats[hour]["count"] += 1
                hour_stats[hour]["views"] += a.get("views", 0)
                hour_stats[hour]["likes"] += a.get("likes", 0)
            except Exception:
                pass

    weekday_names = ["月", "火", "水", "木", "金", "土", "日"]
    weekday_text = ""
    for i, name in enumerate(weekday_names):
        s = weekday_stats[i]
        if s["count"] > 0:
            avg_pv = s["views"] / s["count"]
            weekday_text += f"- {name}曜: {s['count']}本 平均PV:{avg_pv:.1f} スキ:{s['likes']}\n"

    # Xツイートデータ
    tweet_text = ""
    tweet_history_path = ROOT / "data" / "tweet_history.json"
    if tweet_history_path.exists():
        td = json.loads(tweet_history_path.read_text(encoding="utf-8"))
        tweets = td.get("tweets", [])
        if tweets:
            # 自分の投稿のみ抽出（リプライ除外）
            own_tweets = [t for t in tweets if not t.get("text", "").startswith("@")]
            replies = [t for t in tweets if t.get("text", "").startswith("@")]

            tweet_text = f"\n総ツイート: {len(tweets)}件（自分の投稿:{len(own_tweets)} リプライ:{len(replies)}）\n"
            if own_tweets:
                tweet_text += "\n自分の投稿（最新10件）:\n"
                for t in sorted(own_tweets, key=lambda x: x.get("created_at", ""), reverse=True)[:10]:
                    text_preview = t.get("text", "").replace("\n", " ")[:100]
                    tweet_text += f"- ♡{t.get('likes',0)} imp:{t.get('impressions',0)} | {text_preview}\n"

            ws = td.get("weekly_summary", {})
            if ws:
                tweet_text += f"\n週次: 平均♡{ws.get('avg_likes', 0)} 平均imp:{ws.get('avg_impressions', 0)}\n"

    return f"""あなたはコンテンツマーケティングの専門家です。
以下のデータを分析し、コンテンツ戦略と投稿戦略を改善してください。

## 現在の戦略
```
{program}
```

## 現在のパラメータ（抜粋）
```json
{json.dumps({k: strategy['content_params'][k] for k in ['genres', 'genre_weights', 'tags_main', 'target_length_chars', 'free_ratio'] if k in strategy['content_params']}, ensure_ascii=False, indent=2)}
```

## 現在の投稿戦略
```json
{json.dumps(pub_params, ensure_ascii=False, indent=2)}
```

## 記事の成果データ
{articles_text}

## タグ別成績（PV順上位15）
{tag_text if tag_text else 'データなし'}

## 曜日別成績
{weekday_text if weekday_text else 'データなし'}

## Xツイートデータ
{tweet_text if tweet_text else 'データなし'}

## サマリー
- 総記事数: {summary.get('total_articles', 0)}
- 平均PV: {summary.get('avg_views_per_article', 0)}
- 平均スキ: {summary.get('avg_likes_per_article', 0)}
- 現在のフェーズ: {pub_params.get('phase', 'unknown')}

## 指示
以下をJSON形式で出力してください。

1. `program_updates`: program.md の「現在の戦略」セクションの改善提案（文字列）
2. `strategy_updates`: strategy.json の content_params に対する更新（dictの差分）
3. `publishing_updates`: strategy.json の publishing_params に対する更新（dictの差分）
   - phase: 現状維持か次フェーズへの遷移を判断
   - daily_free_count, daily_paid_count: 最適な1日の投稿数
   - paid_price_yen: 有料記事の最適価格
   - 判断理由も含めること
4. `reasoning`: なぜその改善を提案するのか（文字列）
5. `changelog_entry`: 改善履歴に追記する1行サマリー（文字列）

## 分析のポイント
- タグ別成績から、伸びるタグと伸びないタグを特定
- 曜日別成績から、最適投稿曜日を判断
- Xツイートデータから、ツイート戦略の改善点を抽出（tweet_paramsを更新）
  - 反応が良いツイートのパターン（文体・トピック・長さ）
  - 反応が悪いパターンを避ける
  - tweet_params.tone と tweet_params.account_stage を必要に応じて更新

重要: 出力は4000トークン以内に収めること。program_updatesは全文ではなく差分のみ。

出力フォーマット（JSON厳守）:
{{
  "program_updates": "変更・追加する行のみ記述（変更なしなら空文字）",
  "strategy_updates": {{"key": "value"}},
  "publishing_updates": {{"key": "value"}},
  "reasoning": "分析と理由",
  "changelog_entry": "改善内容の要約"
}}

注意:
- データが少ない場合は大きな変更は避け、小さな調整に留める
- 成果が良いジャンル・スタイルはそのまま維持する
- 成果が悪いものは代替案を提案する
- 急激な方向転換は避け、漸進的な改善を心がける
- フェーズ遷移は条件を満たしている場合のみ提案する
- 有料記事は最低でもプロンプト30選以上のボリュームが必要
- Note投稿は1日5本以内に収める（それ超でNoteのスパム判定リスク）。3〜5本は安全圏でAI運用では推奨

重要な制約（厳守）:
- 提案は「自動実行可能」と「人間への提案」を明確に分けること
- program_updates / strategy_updates / publishing_updates には自動実行可能な改善のみ含める:
  - 記事の内容・構成・文体・ジャンル・タグの改善
  - タイトルパターンの改善
  - 投稿頻度・無料/有料比率の調整
  - strategy.json内のパラメータ変更
- 自動化できないが効果的な施策は human_actions に含める:
  - SNS（X, Threads, Instagram等）での手動投稿・拡散
  - プロフィール変更・リンクツリー設定
  - 外部プラットフォームでのアカウント作成・設定
  - コラボ・相互フォローなどの人間関係構築
  - その他、手動操作が必要な施策

記事本文への指示として以下をprogram_updatesに含めてはいけない（Noteで表示崩れ or 不要）:
- SNS拡散用フック文やツイート素材の付記指示
- Markdownの表（テーブル記法）の使用指示
- 品質チェックリストの付記指示
- 関連記事セクションや他記事へのリンク（generate.pyはURLを生成できないため）
- program.mdの「## 現在の戦略」セクションは1つだけにすること（重複禁止）
- program.mdを更新する際、既存の改善履歴を消さないこと

提案する新ルールの品質基準（厳守）:
- 新しいルールを提案する前に「generate.pyが実際にこのルールを実行できるか？」を確認すること
- generate.pyが持っている情報: 戦略指示書(program.md)、過去記事タイトル一覧、strategy.jsonのパラメータ
- generate.pyが持っていない情報: 過去記事のURL、過去記事の本文、PVやスキの詳細データ
- 実現不可能なルール（URLが必要、外部データが必要等）は記事生成ルールとして提案しないこと
- 実現不可能だが効果的な施策は human_actions に含めること

追加の出力フィールド:
6. `human_actions`: 人間がやるべきアクションのリスト（各項目に action, reason, priority(high/medium/low) を含む）

出力例:
"human_actions": [
  {{"action": "Xで記事URLを共有するツイートを投稿する", "reason": "Note内の自然流入だけではPVが伸びない", "priority": "high"}},
  {{"action": "Noteプロフィールにリンクツリーを追加", "reason": "読者の導線を整備", "priority": "medium"}}
]
"""


def evolve():
    """戦略を自己進化させる。"""
    program, strategy, history = load_all()

    # フェーズ遷移チェック（LLMを使わない機械的判定）
    new_phase = check_phase_transition(strategy, history)
    if new_phase:
        old, new = apply_phase_transition(strategy, new_phase)
        STRATEGY_JSON.write_text(
            json.dumps(strategy, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"フェーズ遷移を適用: {old} → {new}")

    # 最低記事数に達していない場合はLLM分析をスキップ
    min_articles = strategy.get("evolution_params", {}).get("min_articles_for_evolution", 3)
    total = history.get("metrics_summary", {}).get("total_articles", 0)

    if total < min_articles:
        print(f"記事数 {total} < 最低 {min_articles}。LLM分析スキップ（データ不足）。")
        print("まずは記事を投稿してデータを集めてください。")
        return

    print("自己進化プロセス開始...")
    print(f"分析対象: {total}件の記事")

    prompt = build_analysis_prompt(program, strategy, history)

    # Claudeを呼び出し（CLIモード時はOpus、APIモード時はSonnet）
    from claude_wrapper import call_claude_json
    use_cli = os.environ.get("USE_CLAUDE_CLI", "0") == "1"
    model = "opus" if use_cli else "sonnet"  # Maxプラン時はOpus、API時はSonnet

    try:
        updates = call_claude_json(
            prompt,
            model=model,
            max_tokens=8192,
            temperature=0.3,
        )
    except Exception as e:
        print(f"Claude呼び出しエラー: {e}")
        print("自己進化をスキップします。")
        return

    # program.md を更新（差分がある場合のみ。changelog_entryは常に追記）
    changelog = updates.get("changelog_entry", "")
    if updates.get("program_updates"):
        update_program(program, updates["program_updates"], changelog)
    elif changelog:
        # program_updatesが空でもchangelogだけ追記
        update_program(program, "", changelog)

    # 累積汚染防止: program.md が膨らんだら自動トリム
    try:
        from forget import trim_program_md
        trim_program_md(keep_history=3)
    except Exception:
        pass

    # strategy.json の content_params を更新
    if updates.get("strategy_updates"):
        update_strategy(strategy, updates["strategy_updates"])

    # strategy.json の publishing_params を更新
    if updates.get("publishing_updates"):
        update_publishing(strategy, updates["publishing_updates"])

    # human_actions を保存
    if updates.get("human_actions"):
        save_human_actions(updates["human_actions"])

    print(f"\n改善理由: {updates.get('reasoning', 'N/A')}")
    print(f"変更内容: {updates.get('changelog_entry', 'N/A')}")
    print("自己進化完了!")


def update_program(current: str, new_strategy_section: str, changelog: str):
    """program.md を更新する。戦略セクションのみ置換し、改善履歴に追記する。"""
    today = datetime.now(JST).strftime("%Y-%m-%d")

    # 差分が空の場合はchangelogだけ追記
    if not new_strategy_section.strip():
        if changelog:
            changelog_line = f"- {today}: {changelog}"
            content = current.replace(
                "## 改善履歴\n（evolve.py が自動追記）",
                f"## 改善履歴\n（evolve.py が自動追記）\n\n{changelog_line}",
            )
            if changelog_line not in content:
                content = current.replace("## 改善履歴", f"## 改善履歴\n\n{changelog_line}")
            PROGRAM_MD.write_text(content, encoding="utf-8")
            print("program.md に改善履歴を追記しました。")
        return

    # new_strategy_section がファイル全体を含んでいる場合、戦略部分だけ抽出
    if "## 現在の戦略" in new_strategy_section:
        parts = new_strategy_section.split("## 現在の戦略", 1)
        if len(parts) > 1:
            rest = parts[1]
            # 次の ## セクションまでを抽出
            for section in ["## 成果サマリー", "## 改善履歴"]:
                if section in rest:
                    rest = rest.split(section)[0]
            new_strategy_section = rest.strip()

    # 現在のファイルから「## 現在の戦略」〜「## 成果サマリー」の間を置換
    before = current.split("## 現在の戦略")[0]
    after_parts = current.split("## 成果サマリー")
    after = "## 成果サマリー" + after_parts[1] if len(after_parts) > 1 else ""

    # 改善履歴に追記
    changelog_line = f"- {today}: {changelog}"
    if "## 改善履歴" in after:
        # 既存の改善履歴の直後に追記
        after = after.replace(
            "## 改善履歴\n（evolve.py が自動追記）",
            f"## 改善履歴\n（evolve.py が自動追記）\n\n{changelog_line}",
        )
        # 「（evolve.py が自動追記）」がない場合のフォールバック
        if changelog_line not in after:
            after = after.replace(
                "## 改善履歴",
                f"## 改善履歴\n\n{changelog_line}",
            )

    content = f"{before}## 現在の戦略\n\n{new_strategy_section}\n\n{after}"

    # 重複する「## 成果サマリー」「## 改善履歴」を除去
    for section in ["## 成果サマリー", "## 改善履歴"]:
        while content.count(section) > 1:
            # 最後の出現を残して、それ以前の重複を削除
            idx = content.index(section)
            next_idx = content.index(section, idx + 1)
            content = content[:idx] + content[next_idx:]

    PROGRAM_MD.write_text(content, encoding="utf-8")
    print("program.md を更新しました。")


def update_strategy(strategy: dict, updates: dict):
    """strategy.json の content_params を更新する。"""
    strategy["version"] = strategy.get("version", 0) + 1

    for key, value in updates.items():
        if key in strategy["content_params"]:
            old = strategy["content_params"][key]
            strategy["content_params"][key] = value
            print(f"  content: {key}: {old} → {value}")
        else:
            strategy["content_params"][key] = value
            print(f"  content: {key}: (新規) {value}")

    STRATEGY_JSON.write_text(
        json.dumps(strategy, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("strategy.json (content_params) を更新しました。")


def save_human_actions(actions: list):
    """人間がやるべきアクションを TODO.md に保存する。"""
    todo_path = ROOT / "TODO.md"
    today = datetime.now(JST).strftime("%Y-%m-%d")

    lines = [f"# やることリスト（{today} 更新）\n"]
    lines.append("evolve.py が分析した結果、以下の手動アクションが効果的です。\n")
    lines.append("完了したら行を削除してください。\n\n")

    for a in actions:
        priority = a.get("priority", "medium")
        icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(priority, "⬜")
        lines.append(f"- [ ] {icon} **{a['action']}**")
        lines.append(f"  - 理由: {a.get('reason', '')}\n")

    todo_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nTODO.md を更新しました（{len(actions)}件のアクション）")
    for a in actions:
        print(f"  [{a.get('priority', '?')}] {a['action']}")


def update_publishing(strategy: dict, updates: dict):
    """strategy.json の publishing_params を更新する。"""
    pub = strategy.get("publishing_params", {})

    for key, value in updates.items():
        if key == "phase_rules":
            continue  # ルール自体は変更しない
        old = pub.get(key, "(未設定)")
        pub[key] = value
        print(f"  publishing: {key}: {old} → {value}")

    strategy["publishing_params"] = pub
    STRATEGY_JSON.write_text(
        json.dumps(strategy, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("strategy.json (publishing_params) を更新しました。")


def main():
    evolve()


if __name__ == "__main__":
    main()

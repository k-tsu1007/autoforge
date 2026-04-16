"""記事生成スクリプト — autoresearchのtrain.pyに相当。

program.md と strategy.json を読み込み、Claude APIで記事を生成する。
evolve.py によって戦略が改善されることで、生成される記事の質も向上する。
"""

import json
import sys
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
from core.paths import drafts_dir as _dd; OUTPUT_DIR = _dd()


def load_strategy() -> dict:
    return json.loads(STRATEGY_JSON.read_text(encoding="utf-8"))


def load_history() -> dict:
    return json.loads(HISTORY_JSON.read_text(encoding="utf-8"))


def load_program() -> str:
    return PROGRAM_MD.read_text(encoding="utf-8")


def build_top_articles_context(history: dict, n: int = 5) -> str:
    """成果上位2本 + ランダム3本のミックス (偏りすぎを防ぐ)。"""
    import random
    articles = history.get("articles", [])
    if not articles:
        return "過去の記事データはまだありません。"

    sorted_articles = sorted(articles, key=lambda a: a.get("likes", 0), reverse=True)
    top2 = sorted_articles[:2]
    rest = [a for a in articles if a not in top2]
    sample = random.sample(rest, min(3, len(rest))) if rest else []
    selected = top2 + sample

    lines = ["## 参考記事 (上位 + ランダム)"]
    for a in selected:
        lines.append(
            f"- 「{a['title']}」 PV:{a.get('views', '?')} スキ:{a.get('likes', '?')}"
        )
    return "\n".join(lines)


def _detect_fabrication(article: dict) -> str | None:
    """記事に捏造と思しき表現があれば理由文字列、無ければ None。"""
    import re
    text = (article.get("title", "") + "\n" + article.get("free_content", "") + "\n" + article.get("paid_content", ""))

    # 危険パターン (実績ゼロアカウントが書いてはいけない数値主張)
    patterns = [
        (r"フォロワー(?:が|を)?\s*\d+\s*人(?:増え|になっ|に達)", "フォロワー数の数値実績"),
        (r"\d+\s*ヶ月で\s*\d+\s*人", "期間×人数の実績"),
        (r"いいね(?:が|を)?\s*\d+\s*(?:倍|超|を超え)", "いいね数の数値実績"),
        (r"(?:月収|月間|累計)\s*¥?\d+\s*円", "収益金額の主張"),
        (r"\d+\s*ヶ月(?:間|続け|やり|続いた)", "期間の実体験主張"),
        (r"半年(?:続け|やり|続いた|間)", "半年継続の主張"),
        (r"私が試した(?:結果|ところ)", "個人実験の主張"),
        (r"インプレッション(?:が|を)?\s*\d+\s*(?:倍|超)", "imp 数値変化"),
        (r"リプライ(?:数|が)?\s*\d+\s*件", "リプ数の実績"),
        (r"(?:変更|変えた)前後で.*\d+\s*から\s*\d+", "前後比較の数値"),
    ]
    for pat, label in patterns:
        if re.search(pat, text):
            return label
    return None


def _build_legacy_instruction(*, free_only: bool, seo_mode: bool, seo_keyword: str, params: dict) -> str:
    """article_generator.txt しかない場合のレガシー注入。新方式のプロンプトファイルが推奨。"""
    from core.paths import load_prompt
    try:
        ext = load_prompt("article_generator.txt")
    except Exception:
        ext = ""

    import re as _re
    def _sec(text, heading):
        p = rf"^## {_re.escape(heading)}.*?\n(.*?)(?=\n## |\Z)"
        m = _re.search(p, text, _re.DOTALL | _re.MULTILINE)
        return m.group(1).strip() if m else ""

    reader_profile = _sec(ext, "読者像") if ext else ""
    anti_fab = _sec(ext, "【絶対不可侵】捏造の絶対禁止") if ext else ""
    title_guide = _sec(ext, "タイトル形式ガイド") if ext else "- タイトルは30文字以内"
    style_rules = _sec(ext, "文体・トーンのルール（重要）") if ext else ""

    reader = f"## 読者像\n{reader_profile}\n" if reader_profile else ""
    style = f"## 文体・トーンのルール\n{style_rules}\n" if style_rules else ""
    anti = f"## 【絶対不可侵】捏造の絶対禁止\n{anti_fab}\n" if anti_fab else ""

    if free_only or seo_mode:
        constraint = f"""## 制約 (完全無料記事)
- paid_content は必ず空文字
- 合計文字数は約{params.get('target_length_chars', 2800)}文字
- 末尾でフォロー誘導を自然に入れる
- タグは {json.dumps(params.get('tags_main', []), ensure_ascii=False)} から最低1つ
- Markdownのテーブル記法は禁止
{title_guide}
"""
    else:
        constraint = f"""## 制約 (無料+有料記事)
- 無料部分は全体の約{int(params.get('free_ratio', 0.65) * 100)}%
- 合計文字数は約{params.get('target_length_chars', 2800)}文字
- 有料部分は「そのまま使えるテンプレ/プロンプト/チェックリスト」のいずれかを必ず含める
- タグは {json.dumps(params.get('tags_main', []), ensure_ascii=False)} から最低1つ
- Markdownのテーブル記法は禁止
{title_guide}
"""

    return reader + style + anti + constraint


def generate_article(strategy: dict, program: str, history: dict, *, free_only: bool = False, topic_hint: str = "", seo_mode: bool = False, user_comment: str = "", prompt_name: str = "", knowledge_set_id: str = "") -> dict:
    """Claudeで記事を生成する。

    プロンプトファイル (<prompt_name>.txt) が記事生成の指示を全て持つ (自己完結)。
    strategy.json / program.md への依存は最小限 (後方互換のためのみ参照)。

    prompt_name が指定されていればそのファイルを system prompt として使う。
    未指定 + free_only=True → article_free.txt、false → article_mixed.txt (あれば)。
    どれも無ければ article_generator.txt + プログラム注入 (レガシー)。
    """
    params = strategy.get("content_params", {}) if strategy else {}
    gen_params = strategy.get("generation_params", {}) if strategy else {}
    # デフォルト生成設定
    gen_params.setdefault("model", "claude-opus-4-5-20251001")
    gen_params.setdefault("max_tokens", 8000)
    gen_params.setdefault("temperature", 0.8)

    # 過去の記事タイトルリスト (直近15本 + PV/スキ情報) - 重複回避 + 成績傾向把握
    articles_list = history.get("articles", []) or []
    all_titles = [a.get("title", "") for a in articles_list]
    recent_articles = articles_list[-15:]
    existing_context = ""
    top_context = ""  # build_top_articles_context は統合により不要に
    if recent_articles:
        lines = ["\n## 直近の記事タイトル（同じ型・テーマの繰り返しを避ける、PV/スキも参考に）"]
        for a in recent_articles:
            title = a.get("title", "")
            pv = a.get("views", 0) or 0
            likes = a.get("likes", 0) or 0
            metrics = ""
            if pv or likes:
                metrics = f" (PV:{pv} スキ:{likes})"
            lines.append(f"- {title}{metrics}")
        existing_context = "\n".join(lines) + "\n"

    topic_instruction = ""
    if topic_hint:
        topic_instruction = f"\n## トピック指定\n以下のトピックで記事を書いてください: {topic_hint}\n"

    # SEOモード: Googleサジェストから次のキーワードを取得
    seo_keyword = None
    if seo_mode:
        try:
            from core.seo_keywords import get_next
            seo_keyword = get_next()
            if seo_keyword:
                print(f"SEOキーワード: {seo_keyword}")
        except Exception as e:
            print(f"キーワード取得失敗: {e}")

    # Knowledge: Publisher の analysis.py から選択された set を使う
    learning_hint = ""
    if knowledge_set_id and knowledge_set_id != "none":
        try:
            from services.publisher.analysis import format_for_prompt as _kfp
            learning_hint = _kfp(knowledge_set_id)
        except Exception as e:
            print(f"knowledge set 取得失敗: {e}")

    # 実験モード
    experiment_hint = ""
    experiment_id = None
    try:
        from core.learning.hypothesis import get_active_for_experiment
        h = get_active_for_experiment()
        if h:
            experiment_id = h.get("id")
            experiment_hint = (
                f"\n## ★実験モード★\n"
                f"以下の仮説を検証する記事を書いてください:\n"
                f"- 仮説: {h.get('claim','')}\n"
                f"- 検証方法: {h.get('test_plan','')}\n"
            )
    except Exception:
        pass

    # プロンプトファイルを選択: 明示 > モード別 > レガシー
    from core.paths import load_prompt
    mode_prompt = ""
    chosen_prompt_name = ""
    if prompt_name:
        try:
            mode_prompt = load_prompt(f"{prompt_name}.txt") or load_prompt(f"{prompt_name}.md")
            chosen_prompt_name = prompt_name
        except Exception:
            mode_prompt = ""
    if not mode_prompt:
        # モード別ファイルを試す
        target_file = "article_free.txt" if (free_only or seo_mode) else "article_mixed.txt"
        try:
            mode_prompt = load_prompt(target_file)
            chosen_prompt_name = target_file.replace(".txt", "")
        except Exception:
            mode_prompt = ""

    if mode_prompt:
        # 新方式: プロンプトファイルが全ての記事生成ルールを持つ (変数置換なし)
        article_instruction = mode_prompt
        legacy_mode = False
    else:
        # レガシー方式: article_generator.txt + プログラム注入
        article_instruction = _build_legacy_instruction(
            free_only=free_only, seo_mode=seo_mode, seo_keyword=seo_keyword,
            params=params,
        )
        legacy_mode = True
    content_instruction = ""

    # 出力フォーマット
    if free_only or seo_mode:
        output_format = """{
  "title": "記事タイトル",
  "genre": "ジャンル名",
  "tags": ["タグ1", "タグ2", "タグ3"],
  "free_content": "本文（Markdown。# タイトル見出しは含めない）",
  "paid_content": ""
}"""
    else:
        output_format = """{
  "title": "記事タイトル",
  "genre": "ジャンル名",
  "tags": ["タグ1", "タグ2", "タグ3"],
  "free_content": "無料部分の本文（Markdown。# タイトル見出しは含めない）",
  "paid_content": "有料部分の本文（Markdown）"
}"""

    # 記事フォーマットはレガシー方式の場合のみ挿入 (新方式はプロンプト内に含まれる想定)
    format_instruction = ""
    if legacy_mode:
        import random as _random
        article_formats = [
            ("対話形式", "読者（Aさん）とライターの対話形式。説教せず一緒に考えるトーン。"),
            ("ケーススタディ形式", "架空の読者ケースで試行錯誤→気づきを書く（完璧な成功談にしない）。"),
            ("一点集中形式", "一つのテーマを深掘り。「〜のこれだけ」という絞り込み構成。"),
            ("問いかけ展開形式", "読者への問いを起点に展開。自分ごととして読める構成。"),
            ("比較・検証形式", "AとB、または「よく言われること」と「実際」を対比。断定しない。"),
        ]
        n, g = _random.choice(article_formats)
        format_instruction = f"\n## 記事フォーマット（今回は「{n}」で書く）\n{g}\n"

    # 空じゃないセクションだけ並べる (改行ノイズを出さない)
    parts = []

    # 1. プロンプトファイル本体 (冒頭にロール行を含む自己完結プロンプト)
    parts.append(article_instruction.strip())

    # 2. ユーザーからの指示 (最優先)
    if user_comment:
        parts.append(
            f"## 【ユーザーからの修正指示（最優先）】\n{user_comment}\n"
            f"上記の指示を最優先に反映してください。"
        )

    # 3. ペルソナ (program.md)
    if program:
        parts.append(program.strip())

    # 4. ユーザー指定トピック
    if topic_instruction:
        parts.append(topic_instruction.strip())

    # 5. 過去の記事一覧 (重複回避 + 成績傾向)
    if existing_context:
        parts.append(existing_context.strip())

    # 6. 選択された学習傾向 (knowledge set)
    if learning_hint:
        parts.append(learning_hint.strip())

    # 7. 実験モード (仮説検証中なら)
    if experiment_hint:
        parts.append(experiment_hint.strip())

    # 8. 記事フォーマット (レガシー用)
    if format_instruction:
        parts.append(format_instruction.strip())

    # 9. JSON 出力フォーマット
    parts.append(
        "## 出力フォーマット（厳守）\n"
        "以下のJSON形式で出力してください。それ以外のテキストは含めないでください。\n\n"
        + output_format
    )

    system_prompt = "\n\n".join(parts)

    from core.llm.claude import call_claude_json
    article = call_claude_json(
        "新しい記事を1本生成してください。",
        model=gen_params["model"],
        system=system_prompt,
        max_tokens=gen_params["max_tokens"],
        temperature=gen_params["temperature"],
    )

    # 捏造検出 (Claudeがルール違反した場合の最終防御)
    fab_check = _detect_fabrication(article)
    if fab_check:
        raise RuntimeError(f"捏造検出により記事破棄: {fab_check} / title={article.get('title','')[:60]}")

    # タイトル重複チェック（重複時は日付付与で回避、再生成しない）
    if article.get("title", "") in all_titles:
        from datetime import datetime, timezone, timedelta
        jst = timezone(timedelta(hours=9))
        article["title"] = f"{article['title']}（{datetime.now(jst).strftime('%m/%d')}更新版）"

    # SEOキーワードを使用済みにする
    if seo_keyword:
        try:
            from core.seo_keywords import mark_used
            mark_used(seo_keyword, article.get("title", ""))
        except Exception:
            pass

    # 実験記事なら hypothesis に紐づける
    if experiment_id:
        try:
            from core.learning.hypothesis import record_test
            record_test(experiment_id, article.get("title", ""))
            article["experiment_id"] = experiment_id
        except Exception:
            pass

    return article


def save_draft(article: dict) -> str:
    """記事を下書きとして保存する。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    from datetime import datetime, timezone, timedelta
    import time

    jst = timezone(timedelta(hours=9))
    timestamp = datetime.now(jst).strftime("%Y%m%d_%H%M%S")
    draft_path = OUTPUT_DIR / f"draft_{timestamp}.json"
    draft_path.write_text(json.dumps(article, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"生成完了: {draft_path}")
    print(f"  タイトル: {article['title']}")
    print(f"  ジャンル: {article['genre']}")
    print(f"  無料部分: {len(article['free_content'])}文字")
    paid_len = len(article.get('paid_content', ''))
    if paid_len:
        print(f"  有料部分: {paid_len}文字")

    return str(draft_path)


def main():
    strategy = load_strategy()
    program = load_program()
    history = load_history()

    # --batch N で複数記事を一括生成
    batch_count = 1
    free_only = "--free" in sys.argv
    seo_mode = "--seo" in sys.argv
    for i, arg in enumerate(sys.argv):
        if arg == "--batch" and i + 1 < len(sys.argv):
            batch_count = int(sys.argv[i + 1])

    for i in range(batch_count):
        print(f"\n--- 記事 {i + 1}/{batch_count} を生成中... ---")
        article = generate_article(strategy, program, history, free_only=free_only, seo_mode=seo_mode)
        save_draft(article)

        # history に仮登録（重複回避のため）
        history.setdefault("articles", []).append({"title": article["title"]})

        import time
        if i < batch_count - 1:
            time.sleep(2)  # API rate limit対策

    print(f"\n全{batch_count}本の生成完了!")


if __name__ == "__main__":
    main()

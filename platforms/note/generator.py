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


def generate_article(strategy: dict, program: str, history: dict, *, free_only: bool = False, topic_hint: str = "") -> dict:
    """Claudeで記事を生成する。"""
    params = strategy["content_params"]
    gen_params = strategy["generation_params"]
    top_context = build_top_articles_context(history)

    # 過去の記事タイトルリスト (直近10本のみ — 渡しすぎるとプレースホルダ置換に陥る)
    all_titles = [a["title"] for a in history.get("articles", [])]
    existing_titles = all_titles[-10:]
    existing_context = ""
    if existing_titles:
        existing_context = (
            "\n## 直近の記事タイトル（同じ型・テーマの繰り返しを避ける）\n"
            + "\n".join(f"- {t}" for t in existing_titles)
            + "\n\n**重要**: 上記タイトルの「形式」（「〇〇な人の△つの特徴」「〇〇する3つの方法」など）を分析し、"
            "同じ形式を連続させないこと。形式のバリエーションを確認してから新タイトルを決めること。\n"
        )

    topic_instruction = ""
    if topic_hint:
        topic_instruction = f"\n## トピック指定\n以下のトピックで記事を書いてください: {topic_hint}\n"

    # Knowledge: 確証された知見のみを読み込む (累積汚染を防ぐ)
    learning_hint = ""
    try:
        from core.learning.knowledge import format_for_prompt
        learning_hint = format_for_prompt()
        if learning_hint:
            learning_hint = "\n## 学習済みの傾向\n" + learning_hint + "\n"
    except Exception as e:
        print(f"knowledge 取得失敗: {e}")

    # 実験モード: 仮説検証用の記事を1本書く (3本に1本ペース)
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
                f"通常の制約より仮説検証を優先してください。\n"
            )
    except Exception:
        pass

    # 【最重要・絶対不可侵】捏造禁止ルール
    anti_fabrication = """
## 【絶対不可侵】捏造の絶対禁止
このアカウントは実績ゼロの新規アカウントです。以下は全て**嘘**になり、ユーザーを欺き、Note規約違反かつ景品表示法違反の可能性があります。違反した記事は全て破棄されます。

【絶対に書いてはいけないこと】
- ❌ 「フォロワーが◯人増えた」「◯ヶ月で◯人になった」などの数値実績
- ❌ 「平均いいね数が◯から◯になった」などの数値変化
- ❌ 「私が試した結果」「私が経験した」などの架空の実体験
- ❌ 「3ヶ月続けた」「半年続けた」などの架空の期間
- ❌ 「収益◯円稼げた」「月収◯円」などの収益実績
- ❌ 「◯◯派です。あなたは？」のような架空の体験を前提にした問いかけ
- ❌ 「以前は◯件だったが今は◯件」のような前後比較

【書いて良いこと】
- ✅ 「一般的にこう言われている」(出典明示なら可、ただし不確実)
- ✅ 「こういう仕組みがある」(事実ベース)
- ✅ 「これを試したい人へ」(読者目線)
- ✅ 「以下の手順を提案する」(中立的)
- ✅ 「もし◯◯ならどう対応するか」(仮定形)

【スタイルの基本】
- 主語を「私」にして体験を語る → 禁止
- 「読者がやると効果が出る方法」を解説 → 推奨
- 一般論・観察・調査の整理として書く → 推奨
- 「実体験」「実証済み」「効果あり」などの断定 → 禁止 (実績がないため)

【違反した場合】
- 記事は即座に破棄され、Noteにも投稿されません
- 信頼を失う行為として記録されます
"""

    # タイトル形式ガイド（多様性確保のため両パスで共通使用）
    title_format_guide = """- タイトルは30文字以内（スマホで途切れない長さ）
- 以下の形式からバランスよく選ぶこと（直近タイトルと同じ形式を2回以上連続させない）:
    How-to型:    「〇〇する方法」「〇〇のやり方」「〇〇ステップ」
    Why型:       「なぜ〇〇か」「〇〇がうまくいかない理由」
    比較型:      「〇〇と〇〇の違い」「〇〇 vs 〇〇」
    解説型:      「〇〇とは何か」「〇〇の仕組みを整理する」
    誤解解消型:  「〇〇のよくある誤解」「〇〇する前に知っておくこと」
    提案・ツール型:「〇〇に使えるテンプレート」「〇〇チェックリスト」「〇〇の始め方」
    リスト型:    「△つの〇〇」「〇〇のN個のポイント」←多用禁止、直近で使用済みなら避ける
- 直近タイトルの形式を確認し、まだ使っていない形式を積極的に選ぶこと"""

    if free_only:
        output_format = """{
  "title": "記事タイトル",
  "genre": "ジャンル名",
  "tags": ["タグ1", "タグ2", "タグ3"],
  "free_content": "記事本文（Markdown。タイトルは含めない）",
  "paid_content": ""
}"""
        content_instruction = f"""## 制約
- これは無料記事です。paid_contentは空文字にしてください
- free_contentにタイトル（# 見出し）を含めないこと。タイトルはtitleフィールドにのみ記載する
{title_format_guide}
- 合計文字数は約{params['target_length_chars']}文字
- 読者がすぐ実践できる具体的な内容にする
- 記事末尾に「もっと詳しく知りたい方はプロフィールから有料記事もチェックしてください」という導線を自然に入れる
- タグは{json.dumps(params['tags_main'], ensure_ascii=False)}から最低1つ + 記事固有のタグ
- 【絶対禁止】Markdownのテーブル記法（| xxx | xxx |）は使わないこと。Noteでは表として表示されずテキストが崩れる。代わりに箇条書きや「項目 → 説明」の形式を使う
{anti_fabrication}
"""
    else:
        output_format = """{
  "title": "記事タイトル",
  "genre": "ジャンル名",
  "tags": ["タグ1", "タグ2", "タグ3"],
  "free_content": "無料部分の本文（Markdown。タイトルは含めない）",
  "paid_content": "有料部分の本文（Markdown）"
}"""
        content_instruction = f"""## 制約
{anti_fabrication}
- 無料部分は全体の約{int(params['free_ratio'] * 100)}%
- free_contentにタイトル（# 見出し）を含めないこと。タイトルはtitleフィールドにのみ記載する
{title_format_guide}
- 合計文字数は約{params['target_length_chars']}文字
- 有料部分には実践的なテンプレート・具体例・コード例を含める
- タグは{json.dumps(params['tags_main'], ensure_ascii=False)}から最低1つ + 記事固有のタグ
- 【絶対禁止】Markdownのテーブル記法（| xxx | xxx |）は使わないこと。Noteでは表として表示されずテキストが崩れる。代わりに箇条書きや「項目 → 説明」の形式を使う
"""

    # 記事フォーマットを毎回ローテーション（単調化防止）
    import random as _random
    article_formats = [
        ("対話形式", """読者（Aさん）とライターの対話形式で書く。
「Aさん: ○○で困っています」「そうですよね。実は〜」のような往復で展開する。
説教せず、一緒に考えるトーン。"""),
        ("ケーススタディ形式", """架空の読者（例: 「本業があるBさん」）のケースを設定し、
その人が直面する状況→試行錯誤→気づきの流れで書く。
ビフォーアフターではなく「試行錯誤の途中」として書くこと（完璧な成功談にしない）。"""),
        ("一点集中形式", """一つのテーマだけを深掘りする。
「〜について3点」ではなく「〜のこれだけ」という絞り込み構成。
読者が「これだけ読めばいい」と思える密度にする。"""),
        ("問いかけ展開形式", """読者への問いを起点に記事を展開する。
「あなたはこんな経験ありませんか？」から始め、問いに答えながら本題に進む。
読者が自分ごととして読める構成にする。"""),
        ("比較・検証形式", """AとBを比べる、または「よく言われること」と「実際」を対比する形式。
断定せず「こういう傾向がある」「こちらの方が〇〇な場合が多い」の表現を使う。"""),
    ]
    chosen_format_name, chosen_format_guide = _random.choice(article_formats)

    format_instruction = f"""
## 記事フォーマット（今回は「{chosen_format_name}」で書く）
{chosen_format_guide}
"""

    system_prompt = f"""あなたはNote(note.com)向けの記事ライターです。
以下の戦略指示書に従って、読者に価値のある記事を1本生成してください。

【このアカウントの読者像】
本業をしながらAI・SNS・noteで副収入を試している20〜30代。
完璧主義で最初の一歩が踏み出せない人、忙しくて時間がない人。
「綺麗にまとまった解説記事」より「自分と同じ目線で書かれた記事」に共感する。

{program}

{top_context}
{existing_context}
{topic_instruction}
{learning_hint}
{experiment_hint}
{format_instruction}

## 文体・トーンのルール（重要）
- 「〜しましょう」「〜することが大切です」の教訓・説教口調禁止
- 冒頭の読者悩み代弁は1文に絞る（長い問いかけ段落を作らない）
- 記事全体を通じて「一緒に考えている」感を出す
- 完璧な答えより「こういう視点もある」「こう試したい」という提示型にする
- 同じ構成（悩み代弁→N個のポイント→有料リード）を繰り返さない

## 出力フォーマット（厳守）
以下のJSON形式で出力してください。それ以外のテキストは含めないでください。

{output_format}

{content_instruction}
"""

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
    for i, arg in enumerate(sys.argv):
        if arg == "--batch" and i + 1 < len(sys.argv):
            batch_count = int(sys.argv[i + 1])

    for i in range(batch_count):
        print(f"\n--- 記事 {i + 1}/{batch_count} を生成中... ---")
        article = generate_article(strategy, program, history, free_only=free_only)
        save_draft(article)

        # history に仮登録（重複回避のため）
        history.setdefault("articles", []).append({"title": article["title"]})

        import time
        if i < batch_count - 1:
            time.sleep(2)  # API rate limit対策

    print(f"\n全{batch_count}本の生成完了!")


if __name__ == "__main__":
    main()

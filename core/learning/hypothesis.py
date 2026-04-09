"""Hypothesis — observer の信号から仮説を生成・管理する。

ループ:
1. observer.py の outliers から Claude が「なぜそうなったか」の仮説を3つ生成
2. data/hypotheses.json に untested として追加
3. generate.py が untested 仮説を1つ取り出し、それに沿った記事を1本「実験記事」として書く
4. evaluator.py が n>=min_sample に到達した仮説を判定 → confirmed/rejected
5. confirmed は knowledge.py に昇格、rejected は knowledge の rejected に
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
from core.paths import hypotheses_path as _hyp; HYPOTHESES_JSON = _hyp()
SIGNALS_JSON = ROOT / "data" / "daily_signals.json"
JST = timezone(timedelta(hours=9))

MIN_SAMPLE = 3
MAX_ACTIVE = 6


def _today() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def load() -> dict:
    if not HYPOTHESES_JSON.exists():
        return {"hypotheses": []}
    return json.loads(HYPOTHESES_JSON.read_text(encoding="utf-8"))


def save(data: dict) -> None:
    HYPOTHESES_JSON.parent.mkdir(parents=True, exist_ok=True)
    HYPOTHESES_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _next_id() -> str:
    return f"h_{_today()}_{datetime.now(JST).strftime('%H%M%S')}"


def generate_hypotheses() -> list:
    """daily_signals を見て Claude に仮説を3つ生成させる。"""
    if not SIGNALS_JSON.exists():
        return []
    signals = json.loads(SIGNALS_JSON.read_text(encoding="utf-8"))

    if not signals.get("outliers_high") and not signals.get("outliers_low"):
        return []

    prompt = f"""あなたはコンテンツマーケティングの分析者です。
以下の昨日のNote記事メトリクスから、なぜ高/低エンゲージメントになったか仮説を3つ生成してください。

【データ】
{json.dumps(signals, ensure_ascii=False, indent=2)}

【出力】
JSONのみ、説明文は一切不要。
{{
  "hypotheses": [
    {{"claim": "簡潔な仮説 (1文)", "test_plan": "どう検証するか (1文)", "min_sample": 3}},
    ...
  ]
}}

良い仮説の条件:
- 検証可能 (n=3〜5本書けば判定できる)
- 具体的 (タイトル形式・ジャンル・時間帯など)
- 実行可能 (generate.py の指示に落とせる)
"""
    try:
        from core.llm.claude import call_claude_json
        result = call_claude_json(prompt, model="sonnet", max_tokens=800, temperature=0.7)
        return result.get("hypotheses", [])
    except Exception as e:
        print(f"hypothesis 生成失敗: {e}")
        return []


def add_new_hypotheses() -> int:
    """observer 結果から仮説を生成して追加。"""
    new = generate_hypotheses()
    if not new:
        return 0
    data = load()
    active = [h for h in data["hypotheses"] if h.get("status") == "untested"]
    capacity = MAX_ACTIVE - len(active)
    if capacity <= 0:
        return 0
    added = 0
    for h in new[:capacity]:
        data["hypotheses"].append({
            "id": _next_id() + f"_{added}",
            "claim": h.get("claim", ""),
            "test_plan": h.get("test_plan", ""),
            "min_sample": int(h.get("min_sample", MIN_SAMPLE)),
            "status": "untested",
            "tested_articles": [],
            "created_at": _today(),
        })
        added += 1
    save(data)
    return added


def get_active_for_experiment() -> dict:
    """generate.py が次の実験記事に使う仮説を1つ取り出す。"""
    data = load()
    untested = [h for h in data["hypotheses"] if h.get("status") == "untested"]
    if not untested:
        return {}
    # 一番テスト数が少ないものを優先
    untested.sort(key=lambda h: len(h.get("tested_articles", [])))
    return untested[0]


def record_test(hypothesis_id: str, article_title: str) -> None:
    """実験記事を書いたら hypothesis に紐づける。"""
    data = load()
    for h in data["hypotheses"]:
        if h.get("id") == hypothesis_id:
            h.setdefault("tested_articles", []).append({
                "title": article_title,
                "tested_at": _today(),
            })
            break
    save(data)


def evaluate_hypotheses() -> dict:
    """n>=min_sample に達した仮説を判定。"""
    from core.db import get_connection
    conn = get_connection()
    data = load()
    confirmed_n = 0
    rejected_n = 0

    for h in data["hypotheses"]:
        if h.get("status") != "untested":
            continue
        tests = h.get("tested_articles", [])
        if len(tests) < h.get("min_sample", MIN_SAMPLE):
            continue
        # 各テスト記事の likes を取得
        likes = []
        for t in tests:
            row = conn.execute(
                "SELECT likes FROM articles WHERE title = ?", (t["title"],)
            ).fetchone()
            if row:
                likes.append(row["likes"] or 0)
        if not likes:
            continue
        avg_test = sum(likes) / len(likes)
        # ベースライン
        base = conn.execute(
            "SELECT AVG(likes) as avg FROM articles WHERE published_at >= datetime('now', '+9 hours', '-30 days')"
        ).fetchone()
        baseline = base["avg"] or 1.0
        lift = avg_test / baseline if baseline else 1.0

        from core.learning.knowledge import add_confirmed, add_rejected
        if lift >= 1.3:
            h["status"] = "confirmed"
            h["lift"] = round(lift, 2)
            h["concluded_at"] = _today()
            add_confirmed(h["claim"], lift, len(likes), evidence=h["id"])
            confirmed_n += 1
        elif lift <= 0.7:
            h["status"] = "rejected"
            h["lift"] = round(lift, 2)
            h["concluded_at"] = _today()
            add_rejected(h["claim"], lift, len(likes), evidence=h["id"])
            rejected_n += 1
        else:
            h["status"] = "inconclusive"
            h["lift"] = round(lift, 2)
            h["concluded_at"] = _today()

    save(data)
    return {"confirmed": confirmed_n, "rejected": rejected_n}


if __name__ == "__main__":
    if "--generate" in sys.argv:
        print(f"added: {add_new_hypotheses()}")
    elif "--evaluate" in sys.argv:
        print(evaluate_hypotheses())
    else:
        d = load()
        active = [h for h in d["hypotheses"] if h.get("status") == "untested"]
        print(f"total: {len(d['hypotheses'])}, active: {len(active)}")
        for h in active[:5]:
            print(f"  - [{h['id']}] {h['claim'][:60]}")

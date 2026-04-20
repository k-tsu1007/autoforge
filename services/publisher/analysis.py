"""Analysis: 過去記事を分析して「学習傾向 (knowledge set)」を作る。

ユーザーは複数の knowledge set を作れて、Generate 時にどれを使うか選ぶ (none も選択可)。
保存先: instances/<name>/data/knowledge_sets.json

構造:
  {
    "sets": {
      "<id>": {
        "name": "タイトルのあるリスト型に効く傾向",
        "description": "...",
        "do_rules": [...],
        "dont_rules": [...],
        "created_at": "2026-04-...",
        "source_range": "last_30"  // 参考: どの範囲を分析したか
      },
      ...
    }
  }
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))


def _store_path() -> Path:
    from core.instance import get_active_instance
    inst = get_active_instance()
    return inst.root / "data" / "knowledge_sets.json"


def load_all() -> dict:
    p = _store_path()
    if not p.exists():
        return {"sets": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"sets": {}}


def save_all(data: dict):
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_sets() -> list[dict]:
    """knowledge set の一覧を返す。"""
    data = load_all()
    out = []
    for sid, s in (data.get("sets") or {}).items():
        out.append({
            "id": sid,
            "name": s.get("name", sid),
            "description": s.get("description", ""),
            "do_rules": s.get("do_rules", []),
            "dont_rules": s.get("dont_rules", []),
            "hot_topics": s.get("hot_topics", []) or [],
            "cold_topics": s.get("cold_topics", []) or [],
            "created_at": s.get("created_at", ""),
            "source_range": s.get("source_range", ""),
        })
    # 新しい順
    out.sort(key=lambda x: x["created_at"], reverse=True)
    return out


def get(set_id: str) -> dict | None:
    data = load_all()
    return (data.get("sets") or {}).get(set_id)


def add(name: str, description: str, do_rules: list, dont_rules: list,
        hot_topics: list | None = None, cold_topics: list | None = None,
        source_range: str = "") -> str:
    data = load_all()
    sid = uuid.uuid4().hex[:8]
    data.setdefault("sets", {})[sid] = {
        "name": name,
        "description": description,
        "do_rules": do_rules,
        "dont_rules": dont_rules,
        "hot_topics": hot_topics or [],
        "cold_topics": cold_topics or [],
        "created_at": datetime.now(JST).isoformat(),
        "source_range": source_range,
    }
    save_all(data)
    return sid


def update(set_id: str, **fields):
    data = load_all()
    if set_id not in (data.get("sets") or {}):
        return
    data["sets"][set_id].update(fields)
    save_all(data)


def delete(set_id: str):
    data = load_all()
    sets = data.get("sets") or {}
    sets.pop(set_id, None)
    data["sets"] = sets
    save_all(data)


def format_for_prompt(set_id: str) -> str:
    """Generate 時にプロンプトへ挿入する文字列を返す。set_id=None or 不明なら空文字。"""
    if not set_id or set_id == "none":
        return ""
    s = get(set_id)
    if not s:
        return ""
    lines = [f"## 学習済みの傾向 — {s.get('name', '')}"]
    dos = s.get("do_rules") or []
    dnts = s.get("dont_rules") or []
    hot = s.get("hot_topics") or []
    cold = s.get("cold_topics") or []

    if dos:
        lines.append("### 効くと確認済みの書き方 (優先して使う)")
        for r in dos:
            lines.append(f"- {r}")
    if dnts:
        lines.append("### 効かないと確認済みの書き方 (避ける)")
        for r in dnts:
            lines.append(f"- {r}")

    if hot or cold:
        lines.append("")
        lines.append("### トピック傾向 (あくまで参考。縛りではない)")
        if hot:
            lines.append("**伸びたテーマ例**:")
            for t in hot:
                if isinstance(t, dict):
                    name = t.get("name", "")
                    pv = t.get("avg_pv", "")
                    reason = t.get("reason", "")
                    lines.append(f"- {name}" + (f" (PV平均 {pv})" if pv else "") + (f" — {reason}" if reason else ""))
                else:
                    lines.append(f"- {t}")
        if cold:
            lines.append("**伸びなかったテーマ例**:")
            for t in cold:
                if isinstance(t, dict):
                    name = t.get("name", "")
                    pv = t.get("avg_pv", "")
                    reason = t.get("reason", "")
                    lines.append(f"- {name}" + (f" (PV平均 {pv})" if pv else "") + (f" — {reason}" if reason else ""))
                else:
                    lines.append(f"- {t}")
        lines.append("")
        lines.append("【重要】上記は参考情報です。これに限定する必要はなく、より刺さると判断するテーマがあれば遠慮なくそちらで書いてください。直近15記事との重複を避けることが最優先。")

    return "\n".join(lines)


def generate_from_articles(range_days: int = 30, focus_hint: str = "") -> dict:
    """Claude で過去記事を分析して knowledge set を生成する。

    Returns: {"name", "description", "do_rules", "dont_rules", "source_range"}
    """
    from core.db import get_connection
    conn = get_connection()
    # status='published' OR NULL (旧データ互換) で、PV/Like があるものを対象
    cutoff = (datetime.now(JST) - timedelta(days=range_days)).isoformat()
    rows = conn.execute(
        "SELECT title, genre, tags, views, likes, free_content, "
        "COALESCE(NULLIF(published_at, ''), created_at) AS pub_date "
        "FROM articles "
        "WHERE (status='published' OR status IS NULL) "
        "AND title IS NOT NULL "
        "AND COALESCE(NULLIF(published_at, ''), created_at) >= ? "
        "ORDER BY pub_date DESC",
        (cutoff,),
    ).fetchall()

    # 日付フィルタに引っかからない場合、日付無視で PV/Like > 0 のものを全取得
    if not rows:
        rows = conn.execute(
            "SELECT title, genre, tags, views, likes, free_content, "
            "COALESCE(NULLIF(published_at, ''), created_at) AS pub_date "
            "FROM articles "
            "WHERE (status='published' OR status IS NULL) "
            "AND title IS NOT NULL "
            "AND (views > 0 OR likes > 0) "
            "ORDER BY views DESC",
        ).fetchall()

    if not rows:
        raise RuntimeError("分析対象の記事がありません")

    articles_summary = []
    for r in rows[:30]:  # 最大30件
        articles_summary.append({
            "title": r["title"] or "",
            "genre": r["genre"] or "",
            "views": r["views"] or 0,
            "likes": r["likes"] or 0,
            "excerpt": (r["free_content"] or "")[:400],
        })

    prompt_txt = f"""以下は過去の記事データです。PV・スキの傾向から次の 2 観点を抽出してください:

**1. 書き方の傾向**: タイトル形式・文体・構成に「効く書き方」と「効かない書き方」
**2. トピックの傾向**: 「伸びたテーマ (hot)」と「伸びなかったテーマ (cold)」を抽象化

{json.dumps(articles_summary, ensure_ascii=False, indent=2)}

{f"## 注目してほしい観点: {focus_hint}" if focus_hint else ""}

出力は以下の JSON 形式のみ。前後の説明文は不要:
{{
  "name": "このセットの短い名称 (20字以内)",
  "description": "このセットの特徴を1〜2文で",
  "do_rules": ["効く書き方を短文で", "..."],
  "dont_rules": ["効かない書き方を短文で", "..."],
  "hot_topics": [
    {{"name": "抽象化したテーマ名", "avg_pv": 数値, "reason": "なぜ伸びたか30字以内"}}
  ],
  "cold_topics": [
    {{"name": "抽象化したテーマ名", "avg_pv": 数値, "reason": "なぜ伸びなかったか30字以内"}}
  ]
}}

注意:
- do_rules / dont_rules: それぞれ 3〜8 個
- hot_topics / cold_topics: それぞれ 2〜5 個、PV平均を算出して入れる
- 「抽象化」とは: 「ChatGPTでレジュメ作成」→「AI活用の具体例」のように、類似記事全体に適用できる形
- 固有名詞 (具体的な記事タイトル) は使わず、次の記事生成に直接使える知見にする"""

    from core.llm.claude import call_claude_json
    result = call_claude_json(prompt_txt, model="opus",
                               max_tokens=2000, temperature=0.5)

    if not isinstance(result, dict):
        raise RuntimeError("Claude からの応答が JSON ではありません")

    return {
        "name": result.get("name", "Untitled analysis"),
        "description": result.get("description", ""),
        "do_rules": result.get("do_rules", []) or [],
        "dont_rules": result.get("dont_rules", []) or [],
        "hot_topics": result.get("hot_topics", []) or [],
        "cold_topics": result.get("cold_topics", []) or [],
        "source_range": f"last_{range_days}_days ({len(rows)} articles)",
    }

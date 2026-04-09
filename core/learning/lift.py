"""パラメータ別 lift 学習 (Option E)。

各パラメータ値が「平均いいねを基準と比べてどれだけ伸ばすか」を計算する。

例:
  title_prefix = "実は" を使った記事 (n=4) の avg_likes = 1.8
  全体の avg_likes = 0.9
  → lift = 1.8 / 0.9 = 2.0  (基準の2倍勝ってる)

サンプル < 2 の値は「探索枠」として別扱い。

実行:
    python lift.py            # 計算して param_lifts.json 更新
    python lift.py --show     # 結果表示のみ
"""

import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
LIFTS_JSON = ROOT / "data" / "param_lifts.json"
from core.paths import strategy_path as _sp; STRATEGY_JSON = _sp()
JST = timezone(timedelta(hours=9))

MIN_SAMPLES = 2  # この件数未満の値は「探索中」扱い


def extract_article_params(article: dict, strategy: dict) -> dict:
    """記事1本から使用されたパラメータ値を推定する。"""
    params = {}
    title = article.get("title", "") or ""
    genre = article.get("genre", "") or ""

    # 1) ジャンル — そのまま使える
    if genre:
        params["genre"] = genre

    # 2) タイトル冒頭プレフィックス — strategy の title_prefix_rotation から検出
    prefixes = strategy.get("content_params", {}).get("title_prefix_rotation", [])
    for p in prefixes:
        # 「{トピック}」「{数字}」のような変数を含む場合は前方一致で雑にマッチ
        clean = re.split(r"[{【]", p)[0]
        if clean and len(clean) >= 2 and title.startswith(clean):
            params["title_prefix"] = clean
            break

    # 3) タイトル末尾サフィックス
    suffixes = strategy.get("content_params", {}).get("title_search_suffix_patterns", [])
    for s in suffixes:
        clean = re.split(r"[{【]", s)[-1].strip()
        if clean and clean in title[-30:]:
            params["title_suffix"] = clean
            break

    # 4) タイトル型カテゴリ (簡易判定)
    if "選" in title and any(c in title for c in "0123456789０-９"):
        params["title_category"] = "数字選"
    elif "実は" in title or "知らないと" in title:
        params["title_category"] = "意外性"
    elif "私" in title or "ました" in title or "やった" in title:
        params["title_category"] = "体験談"
    elif "？" in title or "?" in title:
        params["title_category"] = "問いかけ"
    else:
        params["title_category"] = "その他"

    return params


def compute_lifts() -> dict:
    """全記事を走査して param/value ごとの lift を計算する。"""
    from core.db import get_connection
    conn = get_connection()
    strategy = json.loads(STRATEGY_JSON.read_text(encoding="utf-8"))

    articles = [dict(r) for r in conn.execute(
        "SELECT title, genre, likes, views FROM articles"
    ).fetchall()]

    if not articles:
        return {"updated_at": datetime.now(JST).isoformat(), "groups": {}, "baseline": {}}

    baseline_avg_likes = sum(int(a.get("likes") or 0) for a in articles) / len(articles)
    baseline_avg_views = sum(int(a.get("views") or 0) for a in articles) / len(articles)

    # param_name → value → list of (likes, views)
    by_param = defaultdict(lambda: defaultdict(list))
    for a in articles:
        params = extract_article_params(a, strategy)
        for k, v in params.items():
            by_param[k][v].append({
                "likes": int(a.get("likes") or 0),
                "views": int(a.get("views") or 0),
            })

    groups = {}
    for param_name, values in by_param.items():
        rows = []
        for v, samples in values.items():
            n = len(samples)
            avg_l = sum(s["likes"] for s in samples) / n
            avg_v = sum(s["views"] for s in samples) / n
            lift_l = round(avg_l / baseline_avg_likes, 2) if baseline_avg_likes > 0 else 1.0
            lift_v = round(avg_v / baseline_avg_views, 2) if baseline_avg_views > 0 else 1.0
            rows.append({
                "value": v,
                "samples": n,
                "avg_likes": round(avg_l, 2),
                "avg_views": round(avg_v, 1),
                "lift_likes": lift_l,
                "lift_views": lift_v,
                "status": "learning" if n < MIN_SAMPLES else "active",
            })
        rows.sort(key=lambda x: -x["lift_likes"])
        groups[param_name] = rows

    return {
        "updated_at": datetime.now(JST).isoformat(),
        "baseline": {
            "avg_likes": round(baseline_avg_likes, 2),
            "avg_views": round(baseline_avg_views, 1),
            "n": len(articles),
        },
        "groups": groups,
    }


def save_lifts(lifts: dict):
    LIFTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    LIFTS_JSON.write_text(json.dumps(lifts, ensure_ascii=False, indent=2), encoding="utf-8")


def load_lifts() -> dict:
    if not LIFTS_JSON.exists():
        return {"groups": {}, "baseline": {}}
    return json.loads(LIFTS_JSON.read_text(encoding="utf-8"))


def get_winning_values(top_n: int = 3) -> dict:
    """各パラメータの上位N値を返す（generate.py が次回生成時に優先するため）。"""
    lifts = load_lifts()
    out = {}
    for param_name, rows in (lifts.get("groups") or {}).items():
        active = [r for r in rows if r["status"] == "active"]
        if not active:
            continue
        # lift > 1.1 を「勝ってる」と定義
        winners = [r["value"] for r in active if r["lift_likes"] >= 1.1][:top_n]
        if winners:
            out[param_name] = winners
    return out


def get_losing_values(threshold: float = 0.7) -> dict:
    """各パラメータの「明らかに負けてる」値を返す（generate.py が避けるため）。"""
    lifts = load_lifts()
    out = {}
    for param_name, rows in (lifts.get("groups") or {}).items():
        losers = [r["value"] for r in rows if r["status"] == "active" and r["lift_likes"] <= threshold]
        if losers:
            out[param_name] = losers
    return out


def cli_show():
    lifts = load_lifts()
    if not lifts.get("groups"):
        print("まだ学習データがありません。lift.py を実行してください。")
        return
    print(f"=== 基準 ===")
    b = lifts.get("baseline", {})
    print(f"  記事数: {b.get('n', 0)} / 平均いいね: {b.get('avg_likes', 0)} / 平均PV: {b.get('avg_views', 0)}")
    print(f"  更新: {lifts.get('updated_at', '?')}\n")
    for param_name, rows in lifts["groups"].items():
        print(f"=== {param_name} ===")
        for r in rows:
            badge = "🏆" if r["lift_likes"] >= 1.2 else ("💀" if r["lift_likes"] <= 0.7 else "  ")
            status = "(探索中)" if r["status"] == "learning" else ""
            print(f"  {badge} {r['value']:<20} lift_likes={r['lift_likes']:<5} (n={r['samples']}, avg={r['avg_likes']}) {status}")
        print()


def run() -> dict:
    print("📈 lift 計算中…")
    lifts = compute_lifts()
    save_lifts(lifts)
    print(f"✅ {len(lifts['groups'])} パラメータ x {sum(len(v) for v in lifts['groups'].values())} 値 を計算")
    return lifts


if __name__ == "__main__":
    if "--show" in sys.argv:
        cli_show()
    else:
        run()
        cli_show()


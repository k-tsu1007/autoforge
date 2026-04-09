"""Knowledge — 確証された知見のみを保持する学習データベース。

設計原則:
- program.md のような累積文書ではなく「入れ替え可能な構造化知見」
- 各ルールには証拠 (evidence) と サンプル数 (n) と 鮮度 (since) が付く
- generate.py は active なルール上位N件のみ読み込む → プロンプト膨張を防ぐ
- forget.py が定期的に stale ルールを除去する
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
from core.paths import knowledge_path as _kp; KNOWLEDGE_JSON = _kp()
JST = timezone(timedelta(hours=9))

MAX_ACTIVE_RULES = 10
STALE_DAYS = 30
MIN_SAMPLE_FOR_KEEP = 3


def _today() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def load() -> dict:
    if not KNOWLEDGE_JSON.exists():
        return {"confirmed": [], "rejected": [], "updated_at": _today()}
    return json.loads(KNOWLEDGE_JSON.read_text(encoding="utf-8"))


def save(data: dict) -> None:
    data["updated_at"] = _today()
    KNOWLEDGE_JSON.parent.mkdir(parents=True, exist_ok=True)
    KNOWLEDGE_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def add_confirmed(rule: str, lift: float, n: int, evidence: str = "") -> None:
    """確証された知見を追加 (同一ruleがあれば上書き)。"""
    data = load()
    data["confirmed"] = [r for r in data["confirmed"] if r["rule"] != rule]
    data["confirmed"].append({
        "rule": rule,
        "lift": round(lift, 2),
        "n": n,
        "evidence": evidence,
        "since": _today(),
        "last_used": _today(),
    })
    save(data)


def add_rejected(rule: str, lift: float, n: int, evidence: str = "") -> None:
    """棄却された知見を追加。"""
    data = load()
    data["rejected"] = [r for r in data["rejected"] if r["rule"] != rule]
    data["rejected"].append({
        "rule": rule,
        "lift": round(lift, 2),
        "n": n,
        "evidence": evidence,
        "since": _today(),
    })
    save(data)


def get_active_rules(limit: int = MAX_ACTIVE_RULES) -> dict:
    """generate.py が読み込む確証ルール上位N件 + 避けるべきルール上位N件。"""
    data = load()
    confirmed = sorted(
        data.get("confirmed", []),
        key=lambda r: (r.get("lift", 1.0) * (r.get("n", 1) ** 0.5)),
        reverse=True,
    )[:limit]
    rejected = sorted(
        data.get("rejected", []),
        key=lambda r: r.get("lift", 1.0),
    )[:limit]
    return {"do": confirmed, "dont": rejected}


def format_for_prompt() -> str:
    """generate.py のプロンプトに埋め込む短いテキスト。"""
    rules = get_active_rules()
    lines = []
    if rules["do"]:
        lines.append("## 効くと確認済み (優先して使う)")
        for r in rules["do"]:
            lines.append(f"- {r['rule']} (×{r['lift']}, n={r['n']})")
    if rules["dont"]:
        lines.append("\n## 効かないと確認済み (避ける)")
        for r in rules["dont"]:
            lines.append(f"- {r['rule']} (×{r['lift']}, n={r['n']})")
    return "\n".join(lines) if lines else ""


def forget_stale() -> dict:
    """30日以上更新がなくサンプルも少ない知見を忘れる。"""
    data = load()
    now = datetime.now(JST)
    threshold = now - timedelta(days=STALE_DAYS)

    def _is_fresh(r: dict) -> bool:
        try:
            since = datetime.strptime(r.get("since", ""), "%Y-%m-%d").replace(tzinfo=JST)
        except Exception:
            return True
        if since >= threshold:
            return True
        return r.get("n", 0) >= MIN_SAMPLE_FOR_KEEP * 3  # 古くてもサンプル豊富なら残す

    before_c = len(data.get("confirmed", []))
    before_r = len(data.get("rejected", []))
    data["confirmed"] = [r for r in data.get("confirmed", []) if _is_fresh(r)]
    data["rejected"] = [r for r in data.get("rejected", []) if _is_fresh(r)]
    removed = (before_c - len(data["confirmed"])) + (before_r - len(data["rejected"]))
    save(data)
    return {"removed": removed, "active": len(data["confirmed"]) + len(data["rejected"])}


def seed_from_lift() -> dict:
    """初回セットアップ: 既存の lift データから知見を生成。"""
    try:
        from core.learning.lift import load_lifts
        lifts = load_lifts()
    except Exception:
        return {"seeded": 0}

    seeded = 0
    groups = lifts.get("groups", {})
    for param, values in groups.items():
        for stats in values:
            value = stats.get("value", "")
            n = stats.get("samples", 0)
            lift_val = stats.get("lift_likes", 1.0)
            if n < 3:
                continue
            if lift_val >= 1.3:
                add_confirmed(
                    rule=f"{param}=「{value}」(読者反応が良い)",
                    lift=lift_val,
                    n=n,
                    evidence=f"lift_seed_{_today()}",
                )
                seeded += 1
            elif lift_val <= 0.6:
                add_rejected(
                    rule=f"{param}=「{value}」(反応が低い)",
                    lift=lift_val,
                    n=n,
                    evidence=f"lift_seed_{_today()}",
                )
                seeded += 1
    return {"seeded": seeded}


if __name__ == "__main__":
    import sys
    if "--seed" in sys.argv:
        print(seed_from_lift())
    elif "--forget" in sys.argv:
        print(forget_stale())
    elif "--show" in sys.argv:
        print(format_for_prompt())
    else:
        data = load()
        print(f"confirmed: {len(data.get('confirmed', []))}")
        print(f"rejected:  {len(data.get('rejected', []))}")
        print(f"updated:   {data.get('updated_at')}")

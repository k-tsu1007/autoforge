"""自動成長エージェント Phase 1 — 検索 → 関連度判定 → いいね（1日3件）

セーフガード:
- 1日の上限（デフォルト 3件）
- 同じツイートに二度いいねしない
- 広告/PRキーワードを含む投稿はスキップ
- LLMによる関連度スコア >= 7 のみ実行
- アクション間ランダム遅延 5-30分
- strategy.json の growth_agent.enabled が true のときだけ動く
"""

import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# .env を読む (直接実行時用)
_env_path = ROOT / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        _k = _k.strip()
        _v = _v.strip().strip('"').strip("'")
        if _k and _k not in os.environ:
            os.environ[_k] = _v

from core.paths import strategy_path as _sp; STRATEGY_JSON = _sp()
JST = timezone(timedelta(hours=9))

DEFAULT_GROWTH = {
    "enabled": False,
    "daily_limits": {"likes": 3, "replies": 0, "follows": 0},
    "min_delay_seconds": 300,
    "max_delay_seconds": 1800,
    "skip_keywords": ["広告", "PR", "案件", "プレゼント企画", "応募", "懸賞", "アフィリエイト"],
    "search_keywords": [
        "ChatGPT 副業",
        "生成AI 仕事",
        "SNS運用 個人",
        "ChatGPT 活用",
    ],
    "min_relevance_score": 5,
}


def log(msg: str):
    print(f"[{datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}] [growth] {msg}", flush=True)


def load_growth_config() -> dict:
    try:
        strategy = json.loads(STRATEGY_JSON.read_text(encoding="utf-8"))
    except Exception:
        strategy = {}
    cfg = strategy.get("growth_agent") or {}
    merged = {**DEFAULT_GROWTH, **cfg}
    merged["daily_limits"] = {**DEFAULT_GROWTH["daily_limits"], **(cfg.get("daily_limits") or {})}

    # advisor 推奨で上書き（Claude判断を優先）
    try:
        from core.learning.advisor import get_advice
        adv = get_advice()
        if adv.get("growth_search_keywords"):
            merged["search_keywords"] = adv["growth_search_keywords"]
        if adv.get("growth_daily_likes") is not None:
            merged["daily_limits"]["likes"] = int(adv["growth_daily_likes"])
    except Exception:
        pass

    return merged


def score_relevance(tweet_text: str, our_themes: list[str]) -> tuple[int, str]:
    """LLM で関連度を 0-10 で採点する。"""
    try:
        from core.llm.wrapper import call_llm
    except Exception:
        return 0, "llm_wrapper unavailable"

    themes = "、".join(our_themes)
    prompt = f"""以下のツイートが、「{themes}」をテーマに発信している個人のNoteアカウントにとって、
「いいね」を押すべきほど関連性が高いかを 0-10 で採点してください。

採点基準:
- 10: 完全に同じ悩み・興味を持つ個人ユーザー
- 7-9: 関連度高い、いいねする価値あり
- 4-6: 関連はあるが微妙
- 0-3: 無関係 / スパム / 広告

ツイート:
\"\"\"
{tweet_text[:400]}
\"\"\"

以下のJSONのみで返答してください（前後に説明文は不要）:
{{"score": 数字, "reason": "短い理由"}}"""

    try:
        resp = call_llm(prompt, task_type="strategy_evolution", max_tokens=200, temperature=0.1)
        import re
        m = re.search(r"\{[\s\S]*\}", resp)
        if not m:
            return 0, f"no json: {resp[:80]}"
        data = json.loads(m.group(0))
        return int(data.get("score", 0)), str(data.get("reason", ""))[:200]
    except Exception as e:
        return 0, f"score error: {e}"


def should_skip(tweet: dict, skip_keywords: list[str]) -> str | None:
    text = tweet.get("text", "")
    for kw in skip_keywords:
        if kw in text:
            return f"skip keyword: {kw}"
    # 自分のツイートはスキップ
    my_user = os.environ.get("X_USERNAME", "")
    if tweet.get("user", "").lower() == my_user.lower():
        return "own tweet"
    return None


def run_once(dry_run: bool = False, max_per_call: int = None, enforce_slots: bool = True) -> dict:
    """インターバル呼び出し対応。max_per_call で1回あたりの上限を絞る。
    daily_like_limit は1日合計の上限として常に適用される。
    enforce_slots=True かつ advisor.like_post_slots に現在時刻が含まれない場合はスキップ。
    """
    cfg = load_growth_config()
    if not cfg.get("enabled"):
        log("無効化されています (strategy.growth_agent.enabled=false)")
        return {"executed": 0, "reason": "disabled"}

    if enforce_slots:
        try:
            from core.learning.advisor import get_advice
            from core.slot_utils import is_now_in_slots
            slots = get_advice().get("like_post_slots") or []
            if slots and not is_now_in_slots(datetime.now(JST), slots):
                log(f"like_post_slots {slots} に現在時刻なし — スキップ")
                return {"executed": 0, "reason": "out of slot"}
        except Exception:
            pass

    from core.db import count_growth_actions_today, already_acted_on, record_growth_action

    daily_like_limit = int(cfg["daily_limits"].get("likes", 3))
    already = count_growth_actions_today("like")
    remaining = daily_like_limit - already
    if max_per_call is not None:
        remaining = min(remaining, max_per_call)
    if remaining <= 0:
        log(f"本日の like 上限到達 ({already}/{daily_like_limit}) または call上限")
        return {"executed": 0, "reason": "daily limit"}

    log(f"本日の残り like 枠: {remaining}件")

    from platforms.x.actions import search_tweets, like_tweet

    keywords = cfg.get("search_keywords") or []
    if not keywords:
        log("検索キーワードなし")
        return {"executed": 0, "reason": "no keywords"}

    # ジャンルテーマ（採点用）
    try:
        strategy = json.loads(STRATEGY_JSON.read_text(encoding="utf-8"))
        our_themes = strategy.get("content_params", {}).get("genres", ["ChatGPT", "副業", "SNS運用"])
    except Exception:
        our_themes = ["ChatGPT", "副業", "SNS運用"]

    # 全キーワードを試行 (scored が remaining*2 集まった時点で打ち切り)
    candidates = []
    scored = []
    min_score = int(cfg.get("min_relevance_score", 7))
    random.shuffle(keywords)
    for kw in keywords:
        log(f"検索: {kw}")
        tweets = search_tweets(kw, max_results=15)
        kw_candidates = []
        for t in tweets:
            if already_acted_on(t["url"]):
                continue
            skip = should_skip(t, cfg.get("skip_keywords", []))
            if skip:
                continue
            kw_candidates.append(t)
        candidates.extend(kw_candidates)
        # このキーワード分をスコアリング
        for t in kw_candidates:
            score, reason = score_relevance(t["text"], our_themes)
            log(f"  score={score} @{t['user']}: {t['text'][:40]} | {reason[:60]}")
            if score >= min_score:
                scored.append((score, reason, t))
            if len(scored) >= remaining * 2:
                break
        if len(scored) >= remaining * 2:
            break

    log(f"候補: {len(candidates)}件 / scored>={min_score}: {len(scored)}件")
    if not candidates:
        return {"executed": 0, "reason": "no candidates"}

    if not scored:
        log("関連度の高い候補なし")
        return {"executed": 0, "reason": "no relevant"}

    scored.sort(key=lambda x: -x[0])
    targets = scored[:remaining]

    executed = 0
    for i, (score, reason, t) in enumerate(targets):
        if i > 0 and not dry_run:
            delay = random.randint(cfg["min_delay_seconds"], cfg["max_delay_seconds"])
            log(f"次のアクションまで {delay // 60}分 待機")
            time.sleep(delay)

        log(f"いいね対象 (score={score}): {t['url']}")
        if dry_run:
            log("  [dry-run] 実行スキップ")
            continue

        success = like_tweet(t["url"])
        record_growth_action(
            action_type="like",
            target_url=t["url"],
            target_user=t.get("user", ""),
            target_text=t.get("text", "")[:500],
            relevance_score=score,
            reason=reason,
            success=success,
        )
        if success:
            executed += 1
            log(f"✅ いいね成功 ({executed}/{remaining})")
            try:
                from core.notify import send_discord
                send_discord(content=f"❤️ いいね → {t['url']}")
            except Exception:
                pass
        else:
            log("❌ いいね失敗")

    return {"executed": executed, "candidates": len(candidates), "scored": len(scored)}


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    result = run_once(dry_run=dry)
    print(json.dumps(result, ensure_ascii=False, indent=2))

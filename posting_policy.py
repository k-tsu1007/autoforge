"""動的投稿ポリシー — 状況に応じて「今日何時に何回投稿すべきか」を決める。

考慮要素:
1. フェーズ（trust_building / early_monetization / scaling）
2. ツイートキューの残量（少ないとセーブ）
3. 直近の反応トレンド（伸びてれば増、凹みなら減）
4. 時刻別の過去スコア（実績ベース）
5. 連投の最小間隔（30分）

使い方:
    from posting_policy import PostingPolicy
    policy = PostingPolicy()
    plan = policy.daily_plan()      # 今日のスケジュール
    ok, reason = policy.should_post_now()  # いま投稿すべき?
"""

import json
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
from core.paths import strategy_path as _sp; STRATEGY_JSON = _sp()
JST = timezone(timedelta(hours=9))

# 基本投稿数（1日あたり） — advisor が無いときのフォールバック。
# AI運用なのでフェーズに依存させない。多めが基本。
DEFAULT_DAILY_TARGET = 25
PHASE_BASE_COUNT = {
    "trust_building": 25,
    "early_monetization": 25,
    "scaling": 30,
}

# 連投の最小間隔（分） — スパム判定回避のため
DEFAULT_MIN_GAP_MINUTES = 15

# 1時間あたりの上限 — スパム判定回避
HOURLY_CAP = 5

# キュー残量別の制約 — AI運用ではキュー補充は瞬時なので閾値は低め
QUEUE_FLOOR_HARD = 1   # これ未満なら投稿停止
QUEUE_FLOOR_SOFT = 2   # これ未満なら投稿数を半分に


def _to_jst_dt(iso_ts: str):
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except Exception:
        return None


class PostingPolicy:
    def __init__(self):
        self.strategy = json.loads(STRATEGY_JSON.read_text(encoding="utf-8"))
        self.phase = self.strategy.get("publishing_params", {}).get("phase", "trust_building")
        try:
            from advisor import get_advice
            self.advice = get_advice()
        except Exception:
            self.advice = {}
        self.min_gap_minutes = int(self.advice.get("min_gap_minutes", DEFAULT_MIN_GAP_MINUTES))
        self._load_db_state()

    def _load_db_state(self):
        from db import get_connection
        conn = get_connection()
        # キュー残量
        self.queue_size = conn.execute(
            "SELECT COUNT(*) AS c FROM tweet_queue WHERE posted = 0"
        ).fetchone()["c"]
        # 全ツイート（時刻スコア用）
        self.all_tweets = conn.execute(
            "SELECT created_at, likes, retweets, impressions FROM tweets ORDER BY created_at DESC"
        ).fetchall()
        # 今日の投稿履歴 (link 判定用に text も取得)
        today = datetime.now(JST).strftime("%Y-%m-%d")
        rows = conn.execute(
            "SELECT posted_at, text FROM tweet_posted WHERE date = ?", (today,)
        ).fetchall()
        self.posted_today = []          # 全投稿 datetime (既存用途)
        self.posted_today_nonlink = []  # link (URL含む) 以外の投稿 datetime
        for r in rows:
            dt = _to_jst_dt(r["posted_at"])
            if not dt:
                continue
            self.posted_today.append(dt)
            text = r["text"] or ""
            if "https://" not in text and "http://" not in text:
                self.posted_today_nonlink.append(dt)
        self.posted_today.sort()
        self.posted_today_nonlink.sort()

    # === ① 1日の目標投稿数 ===
    def daily_target(self) -> tuple[int, str]:
        """advisor の単発デイリー目標を起点に、キュー・トレンドで微調整。"""
        # advisor 推奨があれば優先、無ければフェーズベース
        if self.advice.get("single_daily_target") is not None:
            base = int(self.advice["single_daily_target"])
            notes = [f"advisor={base}"]
        else:
            base = PHASE_BASE_COUNT.get(self.phase, 2)
            notes = [f"phase={self.phase}(base={base})"]

        # キュー制約
        if self.queue_size < QUEUE_FLOOR_HARD:
            return 0, f"queue empty ({self.queue_size})"
        if self.queue_size < QUEUE_FLOOR_SOFT:
            target = max(1, base // 2)
            notes.append(f"queue low ({self.queue_size})→{target}")
            return target, " / ".join(notes)

        # トレンド調整（直近5本 vs 全体平均）
        if len(self.all_tweets) >= 10:
            recent5 = self.all_tweets[:5]
            all20 = self.all_tweets[:20]
            recent_avg = sum((r["impressions"] or 0) for r in recent5) / max(1, len(recent5))
            long_avg = sum((r["impressions"] or 0) for r in all20) / max(1, len(all20))
            if long_avg > 0:
                ratio = recent_avg / long_avg
                if ratio > 1.3:
                    base += 1
                    notes.append(f"trend up ({ratio:.2f})→+1")
                elif ratio < 0.7:
                    base = max(1, base - 1)
                    notes.append(f"trend down ({ratio:.2f})→-1")
                else:
                    notes.append(f"trend stable ({ratio:.2f})")

        return base, " / ".join(notes)

    # === ② 時刻別スコア ===
    def hour_scores(self) -> dict:
        """{hour: score} を返す（実績ベース）。"""
        by_hour = defaultdict(list)
        for r in self.all_tweets:
            dt = _to_jst_dt(r["created_at"])
            if not dt:
                continue
            eng = (r["likes"] or 0) + (r["retweets"] or 0)
            score = (r["impressions"] or 0) + eng * 10
            by_hour[dt.hour].append(score)
        return {
            h: round(sum(scores) / len(scores), 1)
            for h, scores in by_hour.items()
            if len(scores) >= 2  # 最低サンプル数
        }

    # === ③ 今日のプラン ===
    def daily_plan(self) -> dict:
        """今日のスケジュールを生成する。advisor.single_post_slots があれば最優先。"""
        target, target_reason = self.daily_target()

        # advisor が時刻を決めていればそれを使う
        adv_slots = self.advice.get("single_post_slots")
        if isinstance(adv_slots, list) and adv_slots:
            from slot_utils import normalize_slots
            slots = normalize_slots(adv_slots)[:target]
            return {
                "target": target,
                "target_reason": target_reason,
                "slots": slots,
                "hours": sorted({int(s.split(":")[0]) for s in slots}),  # 後方互換
                "scores": self.hour_scores(),
                "source": "advisor",
            }

        scores = self.hour_scores()
        if not scores:
            # データ不足: strategy.json の post_time_slots を尊重
            slots = self.strategy.get("publishing_params", {}).get("post_time_slots", [])
            hours = []
            for s in slots:
                try:
                    h = int(s.split("-")[0].split(":")[0])
                    hours.append(h)
                except Exception:
                    pass
            return {
                "target": target,
                "target_reason": target_reason,
                "hours": sorted(hours[:target]),
                "scores": {},
                "fallback": "no hour data",
            }

        # スコア順に並べ、隣接時刻を避けつつ target 個選ぶ
        sorted_hours = sorted(scores.items(), key=lambda x: -x[1])
        chosen = []
        for h, _ in sorted_hours:
            if any(abs(h - c) < 2 for c in chosen):
                continue  # 隣接ガード
            chosen.append(h)
            if len(chosen) >= target:
                break
        # 足りなければ隣接ガードを緩める
        if len(chosen) < target:
            for h, _ in sorted_hours:
                if h in chosen:
                    continue
                chosen.append(h)
                if len(chosen) >= target:
                    break

        return {
            "target": target,
            "target_reason": target_reason,
            "hours": sorted(chosen),
            "scores": scores,
        }

    # === ④ 今すぐ投稿すべきか? ===
    def should_post_now(self, now: datetime = None) -> tuple[bool, str]:
        if now is None:
            now = datetime.now(JST)

        # 緊急優先: リンク付きツイートが未投稿で残ってるならスロット外でも投稿
        try:
            from db import get_connection
            link_pending = get_connection().execute(
                "SELECT COUNT(*) FROM tweet_queue WHERE posted=0 AND type='リンク付き'"
            ).fetchone()[0]
            if link_pending > 0:
                # link 投稿は min_gap 対象外 (重要投稿の鮮度優先)
                return True, f"link tweet 待機中 ({link_pending}件) — スロット無視で投稿"
        except Exception:
            pass

        plan = self.daily_plan()
        if plan["target"] <= 0:
            return False, plan["target_reason"]

        # advisor の slots ("HH:MM") があればそちらで判定 (10分刻み・±5分窓)
        from slot_utils import is_now_in_slots, slot_for_dt
        slots = plan.get("slots") or []
        matched_slot = None
        if slots:
            matched_slot = is_now_in_slots(now, slots)
            if not matched_slot:
                return False, f"now {now.strftime('%H:%M')} not in slots {slots}"
        else:
            if now.hour not in plan["hours"]:
                return False, f"hour {now.hour} not in plan {plan['hours']}"

        # この時刻に既に投稿済み?
        for posted_dt in self.posted_today:
            if matched_slot:
                if slot_for_dt(posted_dt) == matched_slot:
                    return False, f"already posted in slot {matched_slot}"
            elif posted_dt.hour == now.hour:
                return False, f"already posted in hour {now.hour}"

        # 連投ガード（link 投稿は除外して計算 — 単発同士のみガード）
        if self.posted_today_nonlink:
            last = self.posted_today_nonlink[-1]
            if (now - last).total_seconds() < self.min_gap_minutes * 60:
                return False, f"too close to last single post ({last.strftime('%H:%M')}, gap={self.min_gap_minutes}min)"

        return True, f"in plan {plan['hours']}, target={plan['target']}"


def cli_show():
    """現在の状態とプランを表示する。"""
    p = PostingPolicy()
    target, reason = p.daily_target()
    plan = p.daily_plan()
    ok, why = p.should_post_now()

    print("=" * 60)
    print("  📋 投稿ポリシー現状")
    print("=" * 60)
    print(f"フェーズ      : {p.phase}")
    print(f"キュー残量    : {p.queue_size}本")
    print(f"今日の投稿済 : {len(p.posted_today)}本")
    print(f"目標本数      : {target}本 ({reason})")
    print(f"プラン時刻    : {plan['hours']}")
    if plan.get("scores"):
        print("\n時刻スコア:")
        for h, s in sorted(plan["scores"].items(), key=lambda x: -x[1])[:8]:
            print(f"  {h:02d}時: {s}")
    print(f"\n今すぐ投稿? : {'✅ YES' if ok else '⏸  NO'} — {why}")


if __name__ == "__main__":
    cli_show()

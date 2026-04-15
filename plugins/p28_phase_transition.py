"""Phase 自動遷移プラグイン — フォロワー数や売上が基準を超えたら次のフェーズへ。

毎朝 advisor の後に走る。strategy.json の publishing_params.phase_transition.criteria を
見て、条件を満たしていれば phase を進める。設定がなければデフォルト基準を使う。
"""

from __future__ import annotations

import json

from plugins.base import Plugin


# フェーズ間の遷移チェーン (key: 現在phase, value: (次phase, デフォルト基準))
DEFAULT_TRANSITIONS = {
    "trust_building": (
        "early_monetization",
        {"x_followers": 100, "or_monthly_revenue": 100},
    ),
    "early_monetization": (
        "scaling",
        {"weekly_note_pv": 500, "or_monthly_revenue": 1000},
    ),
    "scaling": (None, {}),  # 終端
}


class PhaseTransitionPlugin(Plugin):
    name = "phase_transition"
    description = "fフェーズ条件を満たしたら自動で次のフェーズへ進める"
    order = 28  # advisor(27) の直後

    def run(self, context: dict) -> dict:
        from core.paths import strategy_path
        sp = strategy_path()
        try:
            strategy = json.loads(sp.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"phase_transition: strategy.json 読み込み失敗: {e}")
            return {"transitioned": False}

        publishing = strategy.setdefault("publishing_params", {})
        current_phase = publishing.get("phase", "trust_building")

        # ユーザー定義の基準があればそれを優先、無ければデフォルト
        user_def = publishing.get("phase_transition") or {}
        next_phase = user_def.get("next_phase")
        criteria = user_def.get("criteria")
        if next_phase is None or criteria is None:
            next_phase, criteria = DEFAULT_TRANSITIONS.get(current_phase, (None, {}))

        if next_phase is None:
            return {"transitioned": False, "reason": "終端フェーズ"}

        # 現在値を取得
        from webapp.brain import get_follower_stats, compute_monthly_revenue, compute_weekly_note_pv
        followers = get_follower_stats()
        x_followers = followers.get("x", {}).get("current", 0)
        monthly_rev = compute_monthly_revenue()
        weekly_pv = compute_weekly_note_pv()

        # 各 criteria キーをチェック (複数ある場合は OR 判定)
        met_reasons = []
        if "x_followers" in criteria and x_followers >= criteria["x_followers"]:
            met_reasons.append(f"x_followers={x_followers} >= {criteria['x_followers']}")
        if "or_monthly_revenue" in criteria and monthly_rev >= criteria["or_monthly_revenue"]:
            met_reasons.append(f"monthly_revenue={monthly_rev:.0f} >= {criteria['or_monthly_revenue']}")
        if "weekly_note_pv" in criteria and weekly_pv >= criteria["weekly_note_pv"]:
            met_reasons.append(f"weekly_note_pv={weekly_pv:.0f} >= {criteria['weekly_note_pv']}")

        if not met_reasons:
            print(f"phase_transition: {current_phase} → 条件未達 "
                  f"(x_followers={x_followers}, weekly_pv={weekly_pv:.0f}, monthly_rev={monthly_rev:.0f})")
            return {"transitioned": False, "current_phase": current_phase}

        # 遷移実行
        publishing["phase"] = next_phase
        publishing["phase_transitioned_at"] = self._now_iso()
        publishing["phase_transition_reason"] = " / ".join(met_reasons)

        # 次の遷移用の criteria をデフォルトから引き継ぎ
        if next_phase in DEFAULT_TRANSITIONS:
            np2, c2 = DEFAULT_TRANSITIONS[next_phase]
            if np2:
                publishing["phase_transition"] = {"next_phase": np2, "criteria": c2}

        sp.write_text(json.dumps(strategy, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"🚀 phase 遷移: {current_phase} → {next_phase} ({' / '.join(met_reasons)})")
        return {"transitioned": True, "from": current_phase, "to": next_phase, "reasons": met_reasons}

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime, timezone, timedelta
        return datetime.now(timezone(timedelta(hours=9))).isoformat()

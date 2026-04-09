"""ダッシュボード生成 — Note/Xの成長グラフと戦略サマリーをDiscordに送信。

Claude API不使用。matplotlib + Discord webhookのみ。
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import requests

ROOT = Path(__file__).parent
from core.paths import history_path as _hp; HISTORY_JSON = _hp()
TWEET_HISTORY_JSON = ROOT / "data" / "tweet_history.json"
from core.paths import strategy_path as _sp; STRATEGY_JSON = _sp()
from core.paths import program_md_path as _pmp; PROGRAM_MD = _pmp()
CHART_DIR = ROOT / "data" / "charts"

JST = timezone(timedelta(hours=9))

# 日本語フォント設定
FONT_CANDIDATES = [
    # macOS
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    # Windows
    "C:\\Windows\\Fonts\\YuGothR.ttc",
    "C:\\Windows\\Fonts\\meiryo.ttc",
    "C:\\Windows\\Fonts\\msgothic.ttc",
    # Linux
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
]

def _setup_font():
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            matplotlib.rcParams["font.family"] = "sans-serif"
            from matplotlib.font_manager import FontProperties
            matplotlib.rcParams["font.sans-serif"] = [FontProperties(fname=f).get_name()]
            return
    print("日本語フォントが見つかりません。英語表示になります。")

_setup_font()


def send_discord_file(filepath: str, content: str = ""):
    """Discord webhookにファイルを送信する。"""
    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not url:
        return
    try:
        with open(filepath, "rb") as f:
            resp = requests.post(
                url,
                data={"content": content} if content else {},
                files={"file": (Path(filepath).name, f, "image/png")},
                timeout=15,
            )
        return resp.status_code in (200, 204)
    except Exception as e:
        print(f"Discord送信エラー: {e}")
        return False


def send_discord_text(content: str):
    """Discord webhookにテキストを送信する。"""
    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not url:
        return
    try:
        requests.post(url, json={"content": content}, timeout=10)
    except Exception:
        pass


def generate_note_chart() -> str:
    """Note記事のPV・スキの推移グラフを生成する。"""
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    history = json.loads(HISTORY_JSON.read_text(encoding="utf-8"))
    articles = history.get("articles", [])

    if len(articles) < 2:
        return ""

    # 投稿日ごとにPV・スキを集計
    daily_data = {}
    for a in articles:
        date_str = a.get("published_at", "")[:10]
        if not date_str:
            continue
        if date_str not in daily_data:
            daily_data[date_str] = {"views": 0, "likes": 0, "count": 0}
        daily_data[date_str]["views"] += a.get("views", 0)
        daily_data[date_str]["likes"] += a.get("likes", 0)
        daily_data[date_str]["count"] += 1

    if not daily_data:
        return ""

    dates = sorted(daily_data.keys())
    views = [daily_data[d]["views"] for d in dates]
    likes = [daily_data[d]["likes"] for d in dates]
    counts = [daily_data[d]["count"] for d in dates]

    # 累積計算
    cum_views = []
    cum_likes = []
    cum_articles = []
    tv, tl, tc = 0, 0, 0
    for v, l, c in zip(views, likes, counts):
        tv += v
        tl += l
        tc += c
        cum_views.append(tv)
        cum_likes.append(tl)
        cum_articles.append(tc)

    fig, axes = plt.subplots(2, 1, figsize=(10, 8), tight_layout=True)

    # 上段: 累積PV・スキ
    ax1 = axes[0]
    ax1.set_title("Note cumulative PV / Likes", fontsize=14, fontweight="bold")
    ax1.plot(dates, cum_views, "o-", color="#3498db", label=f"PV (total: {tv})", linewidth=2)
    ax1.plot(dates, cum_likes, "s-", color="#e74c3c", label=f"Likes (total: {tl})", linewidth=2)
    ax1.fill_between(dates, cum_views, alpha=0.1, color="#3498db")
    ax1.legend(fontsize=11)
    ax1.set_ylabel("Count")
    ax1.grid(True, alpha=0.3)
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha="right")

    # 下段: 日別記事数
    ax2 = axes[1]
    ax2.set_title("Articles published per day", fontsize=14, fontweight="bold")
    ax2.bar(dates, counts, color="#2ecc71", alpha=0.7)
    ax2.set_ylabel("Articles")
    ax2.grid(True, alpha=0.3)
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha="right")

    chart_path = str(CHART_DIR / "note_growth.png")
    fig.savefig(chart_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"Noteチャート生成: {chart_path}")
    return chart_path


def generate_x_chart() -> str:
    """Xツイートのエンゲージメント推移グラフを生成する。"""
    CHART_DIR.mkdir(parents=True, exist_ok=True)

    if not TWEET_HISTORY_JSON.exists():
        return ""

    tweet_data = json.loads(TWEET_HISTORY_JSON.read_text(encoding="utf-8"))
    tweets = tweet_data.get("tweets", [])

    if len(tweets) < 2:
        return ""

    # 日付でソート
    tweets = sorted(tweets, key=lambda t: t.get("created_at", ""))

    dates = []
    impressions = []
    likes = []
    for t in tweets:
        date_str = t.get("created_at", "")[:10]
        if date_str:
            dates.append(date_str)
            impressions.append(t.get("impressions", 0))
            likes.append(t.get("likes", 0))

    if not dates:
        return ""

    fig, ax = plt.subplots(figsize=(10, 5), tight_layout=True)
    ax.set_title("X (Twitter) engagement per tweet", fontsize=14, fontweight="bold")

    x = range(len(dates))
    ax.bar(x, impressions, color="#1DA1F2", alpha=0.6, label="Impressions")
    ax.bar(x, likes, color="#e74c3c", alpha=0.8, label="Likes")
    ax.legend(fontsize=11)
    ax.set_ylabel("Count")
    ax.set_xlabel("Tweet #")
    ax.grid(True, alpha=0.3)

    chart_path = str(CHART_DIR / "x_engagement.png")
    fig.savefig(chart_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"Xチャート生成: {chart_path}")
    return chart_path


def generate_strategy_summary() -> str:
    """現在の戦略サマリーをテキストで生成する。"""
    strategy = json.loads(STRATEGY_JSON.read_text(encoding="utf-8"))
    history = json.loads(HISTORY_JSON.read_text(encoding="utf-8"))
    summary = history.get("metrics_summary", {})
    pub = strategy.get("publishing_params", {})
    content = strategy.get("content_params", {})

    # ジャンル別記事数
    genre_count = {}
    for a in history.get("articles", []):
        g = a.get("genre", "不明")
        genre_count[g] = genre_count.get(g, 0) + 1

    genre_text = "\n".join(f"  {g}: {c}本" for g, c in sorted(genre_count.items(), key=lambda x: -x[1]))

    # PV上位記事
    articles = history.get("articles", [])
    top_pv = sorted(articles, key=lambda a: a.get("views", 0), reverse=True)[:5]
    top_text = "\n".join(
        f"  {i+1}. {a['title'][:40]} (PV:{a.get('views',0)} ♡:{a.get('likes',0)})"
        for i, a in enumerate(top_pv)
    )

    text = f"""📋 **現在の戦略サマリー**
━━━━━━━━━━━━━━━
**フェーズ**: {pub.get('phase', '?')}
**総記事数**: {summary.get('total_articles', 0)}
**総PV**: {summary.get('total_views', 0)}
**総スキ**: {summary.get('total_likes', 0)}
**平均PV**: {summary.get('avg_views_per_article', 0)}

**ジャンル別記事数**:
{genre_text}

**PV上位5記事**:
{top_text}

**ジャンル配分**: {_format_genre_weights(content)}
"""

    # X週次サマリー
    if TWEET_HISTORY_JSON.exists():
        td = json.loads(TWEET_HISTORY_JSON.read_text(encoding="utf-8"))
        ws = td.get("weekly_summary", {})
        if ws:
            text += f"""
**X週次サマリー**:
  ツイート数: {ws.get('tweet_count', 0)}
  総いいね: {ws.get('total_likes', 0)}
  総インプレッション: {ws.get('total_impressions', 0)}
  平均いいね: {ws.get('avg_likes', 0)}"""

    return text


def _format_genre_weights(content: dict) -> str:
    """genres と genre_weights をペアにしてフォーマット (個数可変対応)。"""
    genres = content.get("genres") or []
    weights = content.get("genre_weights") or []
    parts = []
    for i, g in enumerate(genres):
        w = weights[i] if i < len(weights) else 0
        parts.append(f"{g} {int(w*100)}%")
    return " / ".join(parts) if parts else "(未設定)"


def send_dashboard():
    """ダッシュボードをDiscordに送信する。"""
    # 戦略サマリー
    summary = generate_strategy_summary()
    send_discord_text(summary)

    # Noteチャート
    note_chart = generate_note_chart()
    if note_chart:
        send_discord_file(note_chart, "📊 Note成長グラフ")

    # Xチャート
    x_chart = generate_x_chart()
    if x_chart:
        send_discord_file(x_chart, "📈 Xエンゲージメントグラフ")

    print("ダッシュボード送信完了")


if __name__ == "__main__":
    send_dashboard()

# auto-content-engine

[autoresearch](https://github.com/karpathy/autoresearch) のアーキテクチャを応用した、自己進化型コンテンツ自動生成システム。

Note記事の自動生成・投稿、Xでの宣伝ツイート自動投稿、AI戦略の自己改善を完全自動で回す。

## 仕組み

```
program.md (戦略指示書)
  ↓
plugins/ (パイプライン)
  ↓
generate → publish → evaluate → evolve → ...
  ↓                                 ↑
db.sqlite3 (SQLite) ←→ x_analytics
  ↓
notify (Discord) + dashboard (FastAPI)
```

## 主要モジュール

| ファイル/ディレクトリ | 役割 |
|---|---|
| `daemon.py` | APScheduler統合デーモン（全タスク管理） |
| `plugin_runner.py` | プラグイン発見・実行エンジン |
| `plugins/` | パイプラインプラグイン群 |
| `db.py` | SQLite 統一データ層 |
| `jobs.py` / `jobs_handlers.py` | 統合ジョブキュー（優先度・リトライ） |
| `claude_wrapper.py` | Claude API/CLI 切り替えラッパー |
| `webapp/` | FastAPI Web管理画面 |
| `admin.py` | Streamlit 管理画面（旧） |

### プラグイン

| プラグイン | 役割 |
|---|---|
| `p10_evaluate` | Note統計取得 |
| `p20_x_analytics` | Xメトリクス取得 |
| `p30_evolve` | 戦略自己進化 |
| `p40_generate` | 記事生成 |
| `p50_publish` | Note投稿 |
| `p60_notify` | Discord通知 |
| `p70_dashboard` | グラフ生成 |
| `p80_maintenance` | バックアップ・クリーンアップ |

## アーキテクチャ

```
Mac (10-18時、開発専用)
  ├─ コード編集 → git push
  ├─ ssh windows-pc でリモート操作
  └─ ブラウザで http://192.168.11.9:8502 (FastAPI)
       ↓
GitHub (private)
  ↓ git pull (5分ごと)
Windows PC (24/7常駐)
  ├─ auto-content-engine-sync (5分ごと git pull)
  ├─ auto-content-engine-daemon (常駐: APScheduler)
  │    ├─ 毎日18:00: 日次パイプライン (8プラグイン)
  │    ├─ 5分ごと: X投稿チェック
  │    ├─ 1分ごと: ヘルス + ジョブキュー処理
  │    └─ 毎日6:00: 古いジョブクリーンアップ
  ├─ auto-content-engine-admin (Streamlit :8501)
  └─ auto-content-engine-webapp (FastAPI :8502)
```

## モデル戦略

| 処理 | モデル | 理由 |
|---|---|---|
| 記事生成 | Haiku | コスト重視 |
| 自己進化 | Opus (CLI) / Sonnet (API) | 戦略分析は精度重視 |
| ツイート文案 | Haiku | 短文で十分 |

## フェーズ自動遷移

| フェーズ | 条件 | 動作 |
|---|---|---|
| trust_building | 初期 | 無料記事のみ |
| early_monetization | 記事20+ & 平均スキ3+ | 無料1本/日 + 有料1本/週 |
| scaling | 記事40+ & 平均スキ5+ | 無料3本/週 + 有料2本/週 |

## セットアップ

### Mac (開発)

```bash
git clone <repo>
cd auto-content-engine
pip install -e .
playwright install webkit chromium
```

`.env` ファイル:
```
ANTHROPIC_API_KEY=...
NOTE_EMAIL=...
NOTE_PASSWORD=...
NOTE_USER_URLNAME=ai_fuku07
DISCORD_WEBHOOK_URL=...
X_API_KEY=...
X_API_SECRET=...
X_ACCESS_TOKEN=...
X_ACCESS_SECRET=...
USE_CLAUDE_CLI=1
WEB_USERNAME=admin
WEB_PASSWORD=admin
```

### Windows (本番)

詳細は `WINDOWS_SETUP.md` を参照。

## 起動方法

### デーモン（全タスク統合）
```bash
python daemon.py
```

### 個別実行
```bash
python plugin_runner.py evaluate          # 1プラグインだけ
python plugin_runner.py                   # 全プラグイン
python run.py --force                     # 強制実行
```

### ジョブキュー
```bash
python jobs.py stats                      # 統計
python jobs.py run                        # pending実行
python jobs.py cleanup                    # 古いジョブ削除
```

### 管理画面
```bash
streamlit run admin.py                    # Streamlit (旧)
python -m webapp.server                   # FastAPI (新, :8502)
```

## SQLite データベース

`data/db.sqlite3` に全データが格納される（旧JSON互換）。

| テーブル | 内容 |
|---|---|
| `articles` | 記事メタデータ + メトリクス |
| `tweets` | Xツイート + エンゲージメント |
| `tweet_queue` | 投稿待ちツイート |
| `tweet_posted` | 投稿済み履歴 |
| `strategy` | 戦略パラメータ (key-value JSON) |
| `health` | コンポーネント稼働状況 |
| `pipeline_runs` | パイプライン実行履歴 |
| `metrics_snapshots` | 日次メトリクス時系列 |
| `jobs` | ジョブキュー |

```bash
# JSONからの初回マイグレーション
python db.py migrate

# 統計表示
python db.py
```

## コスト

| 項目 | 月額目安 |
|---|---|
| Claude (Maxプラン使用時) | サブスク内 |
| Claude (API使用時) | 約300円 |
| X API (Pay Per Use) | 約450円 |
| その他 | 無料 |

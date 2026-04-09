# autoforge — AI コンテンツ自動化フレームワーク

複数プラットフォーム × 複数インスタンス (ジャンル/アカウント) を並列運用するための AI 駆動オートメーション基盤。
1 つのコードベースから:

- Note / X / WordPress / Pinterest などへ自動配信
- インスタンスごとに別ジャンル・別アカウント・別戦略
- 学習ループ (knowledge / lift / hypothesis / advisor / evolve) で毎日進化

既存の `auto-content-engine` (v1、Note 特化) を汎用化して生まれました。

---

## ディレクトリ構造

```
autoforge/
├── core/                    # 汎用エンジン (インスタンス非依存)
│   ├── instance/            # インスタンス管理 (AC_INSTANCE env var)
│   ├── paths.py             # パス解決 (active instance の data/cookies に切替)
│   ├── db.py                # SQLite 層
│   ├── notify.py            # Discord 通知
│   ├── slot_utils.py        # 投稿スロット (HH:MM)
│   ├── llm/                 # Claude CLI / API ラッパー
│   ├── image/               # Stable Diffusion + サムネイル生成
│   ├── scheduler/           # daemon, plugin_runner, jobs
│   └── learning/            # knowledge / lift / hypothesis / observer / advisor / evolve / forget / evaluate
│
├── platforms/               # プラットフォームアダプタ (差し込み式)
│   ├── base.py              # Platform プロトコル + レジストリ
│   ├── note/                # Note (publisher/magazine/policy/generator/adapter)
│   ├── x/                   # X (poster/policy/analytics/health/tweet_gen/engage/growth/adapter)
│   ├── wordpress/           # スタブ
│   └── pinterest/           # スタブ
│
├── instances/               # インスタンスごとの state + 設定
│   └── fuku_ai_sns/         # 例: 副業×AI×SNS
│       ├── config.yaml      # ジャンル/アカウント/目標/プラットフォーム有効化
│       ├── program.md       # 戦略指示書
│       ├── data/            # DB, drafts, 生成物
│       ├── cookies/         # Note/X セッション
│       ├── logs/
│       ├── plugins/         # インスタンス固有プラグイン (任意)
│       └── prompts/         # インスタンス固有プロンプト (任意)
│
├── plugins/                 # 共有パイプラインプラグイン
├── tools/                   # CLI エントリ + バッチ
│   ├── run_daemon.py
│   ├── run_webapp.py
│   ├── maintenance.py
│   ├── backfill_article_bodies.py
│   └── refresh_x_cookies.py
├── webapp/                  # ダッシュボード
│   ├── server.py
│   ├── brain.py
│   ├── dashboard.py
│   └── templates/
└── docs/
```

---

## 起動方法

### 既存のインスタンスを動かす

```bash
cd ~/autoforge
python -m tools.run_daemon --instance fuku_ai_sns
python -m tools.run_webapp --instance fuku_ai_sns   # 別ターミナル
# → http://localhost:8001/brain
```

### 新しいインスタンスを追加する

例: 料理×AI の `cooking_affiliate` を追加する場合

```bash
# 1. 雛形をコピー
cp -r instances/fuku_ai_sns instances/cooking_affiliate
rm -rf instances/cooking_affiliate/data/*
rm -rf instances/cooking_affiliate/cookies/*
rm -rf instances/cooking_affiliate/logs/*

# 2. config.yaml を編集
#    - instance.name / display_name / webapp_port
#    - platforms.note.urlname / platforms.x.username
#    - platforms.wordpress.enabled / 接続情報 (WP を使う場合)
#    - content.genres / target_reader
#    - goals

# 3. 認証情報を用意
#    instances/cooking_affiliate/cookies/ に session.json / x_session.json を配置

# 4. 起動
python -m tools.run_daemon --instance cooking_affiliate
python -m tools.run_webapp --instance cooking_affiliate
```

### 既存の v1 (`auto-content-engine`) データを取り込む (初回のみ)

```bash
cd ~/autoforge/instances/fuku_ai_sns
cp ~/auto-content-engine/data/db.sqlite3     data/
cp ~/auto-content-engine/data/strategy.json  data/
cp ~/auto-content-engine/data/knowledge.json data/
cp ~/auto-content-engine/data/history.json   data/ 2>/dev/null
cp ~/auto-content-engine/session.json         cookies/
cp ~/auto-content-engine/x_session.json       cookies/
cp ~/auto-content-engine/program.md           .
```

v1 を壊さないよう、**コピーのみ** 行ってください。

---

## プラットフォームを追加する

例: WordPress を実装する場合

1. `platforms/wordpress/adapter.py` を実装 (`@register_platform("wordpress")` は既に定義済)
2. `publish(content)` で WordPress REST API を叩く
3. `instances/<name>/config.yaml` の `platforms.wordpress.enabled: true` + 接続情報 (site_url, user, app_password)
4. プラグインや外部スクリプトから `get_platform("wordpress").publish(content)` で呼び出し可能

core/learning などのコア機能は触らずに拡張できます。

---

## インスタンス vs 共有: どこに何を置くか

| 性質 | 場所 |
|---|---|
| ジャンル・アカウント・目標 | `instances/<name>/config.yaml` |
| DB / 認証 / ログ | `instances/<name>/{data,cookies,logs}/` |
| 戦略指示書 | `instances/<name>/program.md` |
| インスタンス固有プロンプト | `instances/<name>/prompts/` |
| インスタンス固有プラグイン | `instances/<name>/plugins/` |
| 共有のエンジン機能 | `core/` |
| 共有のプラットフォームアダプタ | `platforms/<platform>/` |
| 共有のパイプラインプラグイン | `plugins/` |

---

## v1 と並行稼働

両方のデータが完全分離されてるので同一マシンで並行実行可能:

| | port | 場所 |
|---|---|---|
| v1 (auto-content-engine) | 8000 | `~/auto-content-engine/` |
| v2 (autoforge) | 8001 | `~/autoforge/` |

autoforge が安定したら v1 を停止、アーカイブ。

---

## よくある質問

### Q. インスタンス名はどう決める?

A. 英小文字 + アンダースコア推奨。`config.yaml` の `instance.name` とディレクトリ名は一致させる。

### Q. 別インスタンスで同じプラットフォームを使える?

A. 使えます。それぞれ別の `cookies/x_session.json` を持たせるだけ。アカウントも別々。

### Q. Claude Max 契約は複数インスタンスで共有される?

A. 共有されます (Windows 1 台なので)。リクエスト頻度が集中すると 529 overloaded になるため、全インスタンスの時刻スロットを分散させてください。

### Q. GPU (Stable Diffusion) は?

A. 同じく 1 枚の GPU を全インスタンスで共有。現状ロック無し。将来は REST API 化してキューで捌く予定。

---

## TODO / 未実装

- [ ] WordPress アダプタ実装 (REST API)
- [ ] Pinterest アダプタ実装 (API v5)
- [ ] インスタンス別プラグインプロファイル (config.yaml で実行プラグインを指定)
- [ ] インスタンス別プロンプト (`instances/<name>/prompts/` の読み込み)
- [ ] アフィリエイトサブシステム (ASP クライアント / リンクインジェクタ / クリック計測)
- [ ] クロスインスタンスダッシュボード
- [ ] `tools/new_instance.py` 雛形生成
- [ ] Stable Diffusion の REST API 化

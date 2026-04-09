# auto-content-engine v2 — Multi-Instance Framework

v1 の全自動 Note/X エンジンを **マルチインスタンス対応** にリファクタしたバージョンです。
1 つのコードベースから、別アカウント / 別ジャンル / 別プラットフォームを並列で運用できます。

---

## ディレクトリ構造

```
auto-content-engine-v2/
├── core/                        # 汎用レイヤー
│   ├── instance/                # インスタンス管理 (name, root, config.yaml)
│   └── paths.py                 # パス解決 (instance に応じて data/cookies を切替)
├── platforms/                   # プラットフォームアダプタ (差し込み式)
│   ├── base.py                  # Platform プロトコル + レジストリ
│   ├── note/                    # Note (実装済)
│   ├── x/                       # X (実装済)
│   ├── wordpress/               # スタブ (TODO)
│   └── pinterest/               # スタブ (TODO)
├── plugins/                     # パイプラインプラグイン (既存)
├── tools/                       # CLI エントリポイント
│   ├── run_daemon.py            # python -m tools.run_daemon --instance NAME
│   └── run_webapp.py            # python -m tools.run_webapp --instance NAME
├── instances/                   # インスタンスごとの state
│   └── fuku_ai_sns/             # 既存アカウントの移行先
│       ├── config.yaml          # ジャンル/アカウント/目標
│       ├── data/                # DB, drafts, generated/
│       ├── cookies/             # session.json, x_session.json
│       └── logs/
├── webapp/                      # ダッシュボード (port 8001)
└── (レガシーの *.py 群はそのまま — 全て core.paths 経由でインスタンスパスを解決)
```

---

## 起動方法

### v1 (レガシー) と **同時並行** で動かす場合

v1 は ~/auto-content-engine/ で port 8000 を使ってる状態を維持。
v2 は port 8001 で起動。DB・cookies はインスタンスディレクトリ内で完全分離。

```bash
# v2 daemon
cd ~/auto-content-engine-v2
python -m tools.run_daemon --instance fuku_ai_sns

# v2 webapp (別ターミナル)
python -m tools.run_webapp --instance fuku_ai_sns
# → http://localhost:8001/brain
```

### v1 データを v2 にコピー (初回のみ)

fuku_ai_sns のインスタンスは **空の状態** で作成されてるので、v1 の state を引き継ぐには:

```bash
cd ~/auto-content-engine-v2/instances/fuku_ai_sns
cp ~/auto-content-engine/data/db.sqlite3     data/
cp ~/auto-content-engine/data/strategy.json  data/
cp ~/auto-content-engine/data/knowledge.json data/
cp ~/auto-content-engine/data/history.json   data/ 2>/dev/null
cp ~/auto-content-engine/session.json         cookies/
cp ~/auto-content-engine/x_session.json       cookies/
cp ~/auto-content-engine/program.md           .
```

v1 側を壊さないよう、**コピーのみ** 行ってください (mv はダメ)。

---

## 新しいインスタンスを追加する

例: 料理×AI ジャンルの `sakura_kitchen`

### 1. ディレクトリを作る

```bash
mkdir -p instances/sakura_kitchen/{data,cookies,logs}
cp instances/fuku_ai_sns/config.yaml instances/sakura_kitchen/config.yaml
# config を編集 (genre, account, slots)
```

### 2. config.yaml を編集

```yaml
instance:
  name: sakura_kitchen
  display_name: "さくら｜AIで毎日ごはん"
  webapp_port: 8002               # ←別ポートを割り当てる
platforms:
  note:
    enabled: true
    urlname: "sakura_kitchen"     # ←Note 側のアカウント
  x:
    enabled: true
    username: "sakura_kitchen"    # ←X 側のアカウント
content:
  genres:
    - "料理レシピ"
    - "AI時短家事"
  target_reader: "忙しい共働きの30代"
goals:
  note_articles_per_day: 3
```

### 3. cookies を用意

Note と X の認証情報 (`session.json`, `x_session.json`) を `instances/sakura_kitchen/cookies/` に置く。

### 4. 起動

```bash
python -m tools.run_daemon --instance sakura_kitchen
python -m tools.run_webapp --instance sakura_kitchen  # port 8002
```

---

## プラットフォームを追加する

例: WordPress

1. `platforms/wordpress/adapter.py` を実装 (REST API publish)
2. `@register_platform("wordpress")` で登録 (既にスタブあり)
3. `instances/<name>/config.yaml` の `platforms.wordpress.enabled: true` + 接続情報
4. プラグインから `get_platform("wordpress").publish(content)` で呼べる

プラットフォームを追加しても **レガシーコードは触らなくて良い** のがこの設計の利点です。

---

## v1 と v2 の違い

| 項目 | v1 | v2 |
|---|---|---|
| インスタンス数 | 1 | N |
| ジャンル変更 | コード書き換え | config.yaml 編集 |
| プラットフォーム追加 | 大掛かり | `@register_platform` で追加 |
| データ分離 | なし (ROOT 固定) | instances/ 以下で完全分離 |
| 並列実行 | 不可 | 可 (ポート/パスが別) |
| config | コード内散在 | instances/<name>/config.yaml |

---

## よくある質問

### Q. v1 を完全に移行するまで v2 に乗り換えなくていい?

はい。v1 と v2 は **データが完全分離** されてるので並走できます。本番は v1 で回しつつ、v2 で新ジャンル・新プラットフォームを試せます。

### Q. 既存の plugins/ はそのまま動く?

動きます。plugin_runner がインスタンスを意識する必要はなく、各プラグインが読む strategy.json/knowledge.json/DB は `core.paths` 経由でインスタンスパスに解決されます。

### Q. daemon と webapp は別プロセス?

はい。`run_daemon.py` と `run_webapp.py` を別ターミナルで起動してください。両方同じインスタンスで動きます。

### Q. どのインスタンスが動いてるか知りたい

```bash
python -c "from core.instance import list_instances, get_active_instance; print(list_instances()); print(get_active_instance().name)"
```

---

## 未実装 / TODO

- WordPress adapter (REST API 実装)
- Pinterest adapter (API v5 + 画像 batch)
- instance 間のメトリクス比較ダッシュボード
- config.yaml のスキーマ検証 (pydantic)
- `tools/new_instance.py` でインスタンス雛形生成

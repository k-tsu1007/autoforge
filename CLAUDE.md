# autoforge プロジェクト — Claude への指示

## 動作環境 【最重要】

**本番・テスト環境は Windows PC（常時稼働）。Mac はコード編集専用。**

コードを変更したら必ず Windows 上で動作確認すること。「Mac で動いた」は十分ではない。

---

## SSH によるテスト手順

### SSH 接続情報

| 項目 | 値 |
|---|---|
| ホスト名 | `windows-pc` |
| ユーザー | `Tsubasa` |
| autoforge パス | `C:\Users\Tsubasa\autoforge` |

### 標準的なテストフロー

```bash
# 1. Mac で変更 → コミット → プッシュ
git add <files>
git commit -m "..."
git push origin main

# 2. Windows にデプロイ（git pull + デーモン再起動）
ssh windows-pc "cd C:/Users/Tsubasa/autoforge && deploy.bat"

# 3. Windows でテスト実行
ssh windows-pc "cd C:/Users/Tsubasa/autoforge && python <test_script>.py"
```

> **deploy.bat を必ず使うこと。** `git pull` だけでは動いているプロセスに変更が反映されない。
> Python はモジュールをインポート時にキャッシュするため、**コードを変更したら必ずデーモンを再起動する必要がある。**

### git 管理外ファイルの同期（必要時のみ）

以下のファイルは `.gitignore` 対象なので、変更時は scp で手動同期する：

```bash
# インスタンス固有の認証情報（NOTE_PASSWORD, X_API_KEY など）
scp instances/fuku_ai_sns/.env \
    windows-pc:C:/Users/Tsubasa/autoforge/instances/fuku_ai_sns/.env

# グローバル .env（USE_CLAUDE_CLI など共通設定）
scp .env windows-pc:C:/Users/Tsubasa/autoforge/.env

# session.json（Note ログインセッション）
scp instances/fuku_ai_sns/cookies/session.json \
    windows-pc:C:/Users/Tsubasa/autoforge/instances/fuku_ai_sns/cookies/session.json
```

> **認証情報の構造**
> - `instances/<name>/.env` — インスタンス固有の認証情報（最優先で読み込まれる）
> - `.env` — 全インスタンス共通の設定（`USE_CLAUDE_CLI` など）
> - 新しいインスタンスを追加する場合は `instances/<name>/.env` に認証情報を記載する

### デプロイ（コード変更後は必ずこれを使う）

```bash
# git pull + デーモン・webapp を再起動（これ1本でOK）
ssh windows-pc "cd C:/Users/Tsubasa/autoforge && deploy.bat"
```

deploy.bat の中身: `git pull` → 既存 python プロセスを全停止 → daemon/webapp を再起動。

### デーモン操作（個別に操作したい場合）

```bash
# 起動確認
ssh windows-pc "tasklist | findstr python"

# 再起動のみ（git pull 不要なとき）
ssh windows-pc "cd C:/Users/Tsubasa/autoforge && restart.bat"
```

---

## Windows 対応チェックリスト

コードを変更するたびに確認すること：

- **パス**: `\` または `Path()` を使う（`/` のハードコード禁止）
- **Python コマンド**: `python`（`python3` は Windows では動かない）
- **文字コード**: ファイル書き込みは必ず `encoding="utf-8"` を指定
- **プロセス**: `subprocess` は `shell=True` が必要な場合がある
- **環境変数**: `.env` は repo root に配置、`os.environ` で取得

---

## 起動コマンド（Windows）

```bat
python -m tools.run_daemon --instance fuku_ai_sns
python -m tools.run_webapp --instance fuku_ai_sns
```

---

## 確認済みの動作

- Playwright: Windows の `headless=True` で動作する
- Stable Diffusion: Windows GPU（CUDA）で動作する（`runwayml/stable-diffusion-v1-5`）
- `session.json` など `cookies/` 配下は Windows パスで管理される
- `.env` は git 管理外のため Windows に別途配置が必要

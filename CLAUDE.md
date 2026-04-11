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

# 2. Windows に反映
ssh windows-pc "cd C:/Users/Tsubasa/autoforge && git pull"

# 3. Windows でテスト実行
ssh windows-pc "cd C:/Users/Tsubasa/autoforge && python <test_script>.py"
```

### git 管理外ファイルの同期（必要時のみ）

以下のファイルは `.gitignore` 対象なので、変更時は scp で手動同期する：

```bash
# .env
scp /Users/k-tsubasa/autoforge/.env windows-pc:C:/Users/Tsubasa/autoforge/.env

# session.json（Note ログインセッション）
scp instances/fuku_ai_sns/cookies/session.json \
    windows-pc:C:/Users/Tsubasa/autoforge/instances/fuku_ai_sns/cookies/session.json
```

### デーモン操作

```bash
# 起動確認
ssh windows-pc "tasklist | findstr python"

# デーモン起動（バックグラウンド）
ssh windows-pc "cd C:/Users/Tsubasa/autoforge && start /B python -m tools.run_daemon --instance fuku_ai_sns"
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

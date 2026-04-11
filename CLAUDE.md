# autoforge プロジェクト — Claude への指示

## 動作環境 【最重要】

**このシステムの本番・テスト環境は Windows です。**

- 本番稼働: Windows PC（常時稼働）
- テストも Windows で行うことを前提とする
- 開発中に Mac でコードを書く場合でも、テスト手順・コマンド・パスはすべて **Windows 互換** で提示すること

### Windows 対応チェックリスト（コード変更時は必ず確認）

- パス区切り: `\` または `Path()` を使う（`/` ハードコード禁止）
- Python コマンド: `python`（Mac の `python3` ではなく）
- 改行コード: CRLF に注意（ファイル書き込み時は `encoding="utf-8"` 指定）
- `sys.platform == "win32"` の分岐が必要な処理は既存コードを参照
- プロセス管理: `subprocess` の `shell=True` は Windows では必要
- 環境変数: `os.environ` で取得、`.env` ファイルは repo root に配置

### 起動コマンド（Windows）

```bat
python -m tools.run_daemon --instance fuku_ai_sns
python -m tools.run_webapp --instance fuku_ai_sns
```

### テスト時の注意

- Mac ローカルで動作確認できても「Windows でも動くか？」を必ず考える
- Playwright は Windows の headless=True で動作することを確認済み
- session.json など cookies/ 配下のファイルは Windows パスで管理される

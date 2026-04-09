# Windows セットアップガイド

このシステムをWindows PCで本番運用するための手順。

## 前提条件

- Windows 10 / 11
- 24時間起動可能
- インターネット接続
- 既にMacで `auto-content-engine` を使っている

## 1. 必要なソフトウェアをインストール

### Git
```powershell
winget install Git.Git
```

### Python 3.11+
```powershell
winget install Python.Python.3.12
```

### Google Chrome
```powershell
winget install Google.Chrome
```

### Node.js（Claude Code CLI用）
```powershell
winget install OpenJS.NodeJS.LTS
```

### Claude Code CLI
```powershell
npm install -g @anthropic-ai/claude-code
```

## 2. リポジトリをclone

```powershell
cd C:\Users\<username>
git clone https://github.com/k-tsu1007/auto-content-engine.git
cd auto-content-engine
```

## 3. Pythonパッケージインストール

```powershell
pip install anthropic httpx NoteClient2 Pillow requests-oauthlib browser_cookie3 matplotlib streamlit playwright
playwright install webkit chromium
```

## 4. .env ファイル作成

`C:\Users\<username>\auto-content-engine\.env` に以下を作成:

```
ANTHROPIC_API_KEY=（必須）
NOTE_EMAIL=（必須）
NOTE_PASSWORD=（必須）
NOTE_USER_URLNAME=ai_fuku07
DISCORD_WEBHOOK_URL=（必須）
X_API_KEY=（X分析用）
X_API_SECRET=（X分析用）
X_ACCESS_TOKEN=（X分析用）
X_ACCESS_SECRET=（X分析用）
X_USERNAME=fuku_ai07
USE_CLAUDE_CLI=1
```

## 5. session.json を Mac から転送

Macで:
```bash
cat /Users/k-tsubasa/auto-content-engine/session.json
```

これをコピーして、Windows の `auto-content-engine\session.json` に保存。

## 6. Claude Code CLIにログイン

```powershell
claude
# 表示される指示に従ってブラウザでログイン
```

## 7. ChromeでXにログイン

ChromeでX (https://x.com) にログインしておく。
その後 `refresh_x_cookies.py` を実行:

```powershell
python refresh_x_cookies.py
```

## 8. Task Schedulerに登録

### 8-1. 自動同期タスク（5分ごとgit pull）

`scripts\auto_sync.bat` を作成:
```batch
@echo off
cd C:\Users\<username>\auto-content-engine
git pull
```

タスクスケジューラで:
- 名前: auto-content-engine-sync
- トリガー: 5分ごと
- 操作: `auto_sync.bat`

### 8-2. 日次パイプライン（JST 18:00）

`scripts\daily_pipeline.bat`:
```batch
@echo off
cd C:\Users\<username>\auto-content-engine
set USE_CLAUDE_CLI=1
python run.py
```

タスクスケジューラで:
- 名前: auto-content-engine-daily
- トリガー: 毎日 18:00
- 操作: `daily_pipeline.bat`

### 8-3. X投稿デーモン（常駐）

`scripts\x_daemon.bat`:
```batch
@echo off
cd C:\Users\<username>\auto-content-engine
python x_post_daemon.py
```

タスクスケジューラで:
- 名前: auto-content-engine-x-daemon
- トリガー: 起動時
- 操作: `x_daemon.bat`
- 設定: 「タスクが既に実行されている場合: 新しいインスタンスを開始しない」

## 9. 動作確認

```powershell
# 記事生成テスト
python generate.py --free

# X投稿デーモンテスト
python x_post_daemon.py

# ダッシュボード起動
streamlit run admin.py
```

## 10. Mac側の設定を無効化

Macで以下を実行（Windowsで稼働開始後）:

```bash
launchctl unload ~/Library/LaunchAgents/com.user.daily-pipeline.plist
launchctl unload ~/Library/LaunchAgents/com.user.x-post-local.plist
```

GitHub Actionsは「フォールバック」として残しておく（Windowsが落ちた時用）。

## トラブルシューティング

### Claude CLIが認証エラー
```powershell
claude logout
claude
# 再ログイン
```

### Playwright webkitが起動しない
```powershell
playwright install webkit --with-deps
```

### Chromeから cookie 取れない
- Chromeを完全終了してから実行
- Profile番号を確認: `C:\Users\<username>\AppData\Local\Google\Chrome\User Data\`

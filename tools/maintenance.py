
"""メンテナンススクリプト — ログローテーション・バックアップ・Cookie監視。

毎日1回、日次パイプラインの最後に実行する。
"""

import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

ROOT = Path(__file__).parent
LOGS_DIR = ROOT / "logs"
BACKUP_DIR = ROOT / "data" / "backups"
from core.paths import x_session_path as _xsp; X_SESSION_JSON = _xsp()

JST = timezone(timedelta(hours=9))


def rotate_logs(max_age_days: int = 14):
    """logs/*.log を日次ローテートする。N日以上前のものは削除。"""
    if not LOGS_DIR.exists():
        return

    today = datetime.now(JST).strftime("%Y-%m-%d")
    rotated_count = 0
    skipped_count = 0

    # 現在のログをアーカイブ（ロックされたファイルはスキップ）
    for log_file in LOGS_DIR.glob("*.log"):
        # アーカイブ済みファイルはスキップ（ファイル名に日付が含まれる）
        if "_2026-" in log_file.name or "_2027-" in log_file.name:
            continue
        try:
            if log_file.stat().st_size == 0:
                continue
            archive_name = f"{log_file.stem}_{today}.log"
            archive_path = LOGS_DIR / archive_name
            if archive_path.exists():
                with open(archive_path, "a", encoding="utf-8", errors="replace") as fa:
                    fa.write(log_file.read_text(encoding="utf-8", errors="replace"))
            else:
                shutil.copy2(log_file, archive_path)
            # 元ファイルをクリア（ロック中なら例外でスキップ）
            log_file.write_text("")
            rotated_count += 1
        except (PermissionError, OSError) as e:
            skipped_count += 1
            continue

    # 古いアーカイブを削除
    cutoff = datetime.now(JST) - timedelta(days=max_age_days)
    deleted_count = 0
    for archive in LOGS_DIR.glob("*_*.log"):
        try:
            mtime = datetime.fromtimestamp(archive.stat().st_mtime, tz=JST)
            if mtime < cutoff:
                archive.unlink()
                deleted_count += 1
        except Exception:
            pass

    print(f"[ログ] ローテート: {rotated_count}件 / スキップ(使用中): {skipped_count}件 / 削除: {deleted_count}件")


def backup_data(retention_days: int = 30):
    """重要データを日次バックアップする。"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(JST).strftime("%Y-%m-%d")
    backup_today_dir = BACKUP_DIR / today
    backup_today_dir.mkdir(exist_ok=True)

    targets = [
        ROOT / "program.md",
        ROOT / "data" / "strategy.json",
        ROOT / "data" / "history.json",
        ROOT / "data" / "tweet_history.json",
        ROOT / "data" / "tweet_posted.json",
        ROOT / "TODO.md",
    ]

    backed_up = 0
    for src in targets:
        if src.exists():
            shutil.copy2(src, backup_today_dir / src.name)
            backed_up += 1

    # 古いバックアップを削除
    cutoff = datetime.now(JST) - timedelta(days=retention_days)
    deleted = 0
    for old_dir in BACKUP_DIR.iterdir():
        if not old_dir.is_dir():
            continue
        try:
            dir_date = datetime.strptime(old_dir.name, "%Y-%m-%d").replace(tzinfo=JST)
            if dir_date < cutoff:
                shutil.rmtree(old_dir)
                deleted += 1
        except ValueError:
            continue

    print(f"[バックアップ] {backed_up}ファイル保存 / 古い{deleted}日分削除")


def check_x_cookie_expiry(warn_days: int = 7):
    """X Cookie の期限を確認、残り日数が少なければDiscord通知。"""
    if not X_SESSION_JSON.exists():
        return

    try:
        cookies = json.loads(X_SESSION_JSON.read_text(encoding="utf-8"))
        auth_token = next((c for c in cookies if c.get("name") == "auth_token"), None)
        if not auth_token:
            print("[Cookie] auth_tokenが見つかりません")
            return

        expires = auth_token.get("expires", -1)
        if expires == -1:
            print("[Cookie] 有効期限なし（セッションCookie）")
            return

        expires_dt = datetime.fromtimestamp(expires, tz=JST)
        now = datetime.now(JST)
        remaining = (expires_dt - now).days

        print(f"[Cookie] 残り {remaining}日 (期限: {expires_dt.strftime('%Y-%m-%d')})")

        if remaining < 0:
            _notify_discord(f"🚨 **X Cookie切れ**\n期限: {expires_dt.strftime('%Y-%m-%d')}\nrefresh_x_cookies.pyを実行してください")
        elif remaining <= warn_days:
            _notify_discord(f"⚠️ **X Cookie期限間近**\n残り{remaining}日 (期限: {expires_dt.strftime('%Y-%m-%d')})\nそろそろ更新しましょう")

    except Exception as e:
        print(f"[Cookie] チェックエラー: {e}")


def _notify_discord(content: str):
    try:
        from core.notify import send_discord
        send_discord(content=content)
    except Exception as e:
        print(f"Discord通知エラー: {e}")


def cleanup_old_files(days: int = 30):
    """古いthumbnails/charts/draftsを削除する。"""
    cutoff = datetime.now(JST) - timedelta(days=days)
    targets_dirs = [
        ROOT / "data" / "thumbnails",
        ROOT / "data" / "charts",
        ROOT / "data" / "drafts",
    ]
    total_deleted = 0
    for d in targets_dirs:
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.is_file():
                try:
                    mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=JST)
                    if mtime < cutoff:
                        f.unlink()
                        total_deleted += 1
                except Exception:
                    pass
    print(f"[クリーンアップ] {total_deleted}ファイル削除")


def main():
    print("=" * 50)
    print("  メンテナンス開始")
    print("=" * 50)

    rotate_logs()
    backup_data()
    check_x_cookie_expiry()
    cleanup_old_files()

    print("=" * 50)
    print("  メンテナンス完了")
    print("=" * 50)


if __name__ == "__main__":
    main()

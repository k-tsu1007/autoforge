"""Note投稿スクリプト — autoresearchのprepare.pyに相当。

NoteClient2（非公式ライブラリ）を使って記事を投稿する。

認証フロー:
1. session.json があればそれを使う（Playwright不要 → GitHub Actions対応）
2. 環境変数 NOTE_SESSION_JSON があればそこからsession.jsonを復元
3. どちらもなければPlaywright headless=False でローカルログイン

フォールバック: NoteClient2が使えない場合はローカルMarkdown保存。
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
from core.paths import history_path as _hp; HISTORY_JSON = _hp()
from core.paths import note_session_path as _nsp; SESSION_JSON = _nsp()
from core.paths import drafts_dir as _dd
from core.paths import published_dir as _pd
from core.paths import ready_to_publish_dir as _rtpd

JST = timezone(timedelta(hours=9))


def load_history() -> dict:
    return json.loads(HISTORY_JSON.read_text(encoding="utf-8"))


def save_history(history: dict):
    HISTORY_JSON.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _restore_session_from_env():
    """環境変数 NOTE_SESSION_JSON からsession.jsonを復元する。"""
    session_data = os.environ.get("NOTE_SESSION_JSON")
    if session_data and not SESSION_JSON.exists():
        SESSION_JSON.write_text(session_data, encoding="utf-8")
        print("環境変数からsession.jsonを復元しました。")


def _login_note(email: str, password: str) -> dict:
    """Playwright headless=False でNoteにログインし、Cookieを返す。"""
    from playwright.sync_api import sync_playwright
    from time import sleep

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        page.goto("https://note.com/login")
        page.wait_for_load_state("domcontentloaded")
        sleep(2)

        page.locator('#email').click()
        page.wait_for_timeout(300)
        page.locator('#email').type(email, delay=50)
        page.locator('#password').click()
        page.wait_for_timeout(300)
        page.locator('#password').type(password, delay=50)
        page.wait_for_timeout(500)
        page.locator('button:has-text("ログイン"):not([disabled])').click(timeout=10000)
        page.wait_for_timeout(5000)

        cookies_list = context.cookies()
        cookies = {c["name"]: c["value"] for c in cookies_list}

        page.close()
        context.close()
        browser.close()

    return cookies


def _get_noteclient(email: str, password: str, user_urlname: str):
    """NoteClient2を初期化し、認証済みの状態で返す。"""
    from NoteClient2 import NoteClient2

    # 環境変数からセッション復元を試みる
    _restore_session_from_env()

    # session_file にインスタンス固有のパスを渡す
    # → publish() 内の auth.prepare() が正しいセッションを読む
    client = NoteClient2(
        email=email,
        password=password,
        user_urlname=user_urlname,
        session_file=str(SESSION_JSON),
    )

    # NoteClient2 は eyecatch を 1920x1080 で送るが Note は 1280:670 比率のみ受け付ける
    # → upload_eyecatch をモンキーパッチして正しいサイズに上書き
    import types
    def _fixed_upload_eyecatch(self_img, http, headers, note_id, file_path):
        if not file_path or not Path(file_path).exists():
            return {"ok": False, "error": {"type": "FileNotFound", "path": file_path}}
        try:
            with open(file_path, "rb") as f:
                resp = http.post(
                    "https://note.com/api/v1/image_upload/note_eyecatch",
                    headers=headers,
                    files={"file": ("blob", f, "image/png")},
                    data={"note_id": note_id, "width": 1280, "height": 670},
                )
            if not resp.get("ok"):
                return {"ok": False, "error": {"type": "EyecatchUploadFailed", "detail": resp.get("text")}}
            body = resp.get("json") or {}
            if "error" in body:
                return {"ok": False, "error": {"type": "EyecatchApiError", "detail": body["error"]}}
            print(f"アイキャッチアップロード成功: {body.get('data', {}).get('url', '')}")
            return {"ok": True, "data": {"uploaded": True}}
        except Exception as e:
            return {"ok": False, "error": {"type": type(e).__name__, "message": str(e)}}

    client.images.upload_eyecatch = types.MethodType(_fixed_upload_eyecatch, client.images)

    # note.com が HTML の `magazineLayout.id` → `magazineId` 形式に変更したため、
    # MagazineResolver のregexパターンを拡張する
    import re as _re
    def _fixed_get_magazine_id(self_mag, http, user_urlname, headers, magazine_key):
        if not magazine_key:
            return {"ok": True, "data": {"magazine_id": None}}
        url = f"https://note.com/{user_urlname}/m/{magazine_key}"
        res = http.get(url, headers={"User-Agent": headers.get("User-Agent", "")})
        if not res.get("ok"):
            return {"ok": False, "error": {"type": "MagazinePageFetchFailed", "status_code": res.get("status_code"), "detail": res.get("text"), "url": url}}
        html = res.get("text") or ""
        for pat in (
            r'"?magazineId"?\\?"?\s*:\s*(\d+)',  # 新形式 (2026-04以降)
            r'magazineLayout\s*:\s*{\s*id\s*:\s*(\d+)',
            r'"magazineLayout"\s*:\s*{\s*"id"\s*:\s*(\d+)',
        ):
            m = _re.search(pat, html)
            if m:
                return {"ok": True, "data": {"magazine_id": int(m.group(1))}}
        return {"ok": False, "error": {"type": "MagazineIdNotFound", "message": "magazine id not found in html", "url": url}}
    client.magazines.get_magazine_id = types.MethodType(_fixed_get_magazine_id, client.magazines)

    return client


def _upload_eyecatch(cookies: dict, note_id: int, image_path: str):
    """アイキャッチ画像をアップロードし、記事に紐付ける。"""
    import requests

    try:
        # 1. 画像アップロード
        with open(image_path, "rb") as f:
            resp = requests.post(
                "https://note.com/api/v1/image_upload/note_eyecatch",
                cookies=cookies,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "X-Requested-With": "XMLHttpRequest",
                },
                files={"file": ("eyecatch.png", f, "image/png")},
                data={"note_id": note_id, "width": 1280, "height": 670},
            )

        if resp.status_code in (200, 201):
            eyecatch_url = resp.json().get("data", {}).get("url", "")
            print(f"アイキャッチアップロード成功: {eyecatch_url}")
        else:
            print(f"アイキャッチアップロード失敗: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"アイキャッチエラー: {e}")


def publish_via_noteclient(article: dict) -> dict:
    """NoteClient2で記事を投稿する。"""
    try:
        from NoteClient2 import NoteClient2  # noqa: F401
    except ImportError:
        print("NoteClient2が未インストール。ローカル保存にフォールバック。")
        print("  pip install NoteClient2")
        return save_locally(article)

    email = os.environ.get("NOTE_EMAIL")
    password = os.environ.get("NOTE_PASSWORD")
    user_urlname = os.environ.get("NOTE_USER_URLNAME")

    if not email or not password or not user_urlname:
        print("NOTE_EMAIL / NOTE_PASSWORD / NOTE_USER_URLNAME が未設定。")
        print("ローカル保存にフォールバック。")
        return save_locally(article)

    # paid_contentがなければ無料記事（price=0）
    has_paid = bool(article.get("paid_content"))

    # Markdownファイルを一時作成
    md_content = article["free_content"]
    if has_paid:
        md_content += "\n\n<pay>\n\n"
        md_content += article["paid_content"]

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", encoding="utf-8", delete=False
        ) as f:
            f.write(md_content)
            md_path = f.name

        client = _get_noteclient(email, password, user_urlname)

        price = article.get("price", 500) if has_paid else 0
        hashtags = article.get("tags", [])
        is_publish = "--draft" not in sys.argv

        # サムネイル生成
        eyecatch_path = None
        try:
            from core.image.thumbnail import generate_thumbnail
            eyecatch_path = generate_thumbnail(
                article["title"],
                genre=article.get("genre", ""),
                tags=article.get("tags", []),
            )
        except Exception as e:
            print(f"サムネイル生成スキップ: {e}")

        # マガジン自動分類 (Claudeが既存マガジンから1つ選ぶ)
        magazine_keys = []
        try:
            from platforms.note.magazine import classify_article
            mk = classify_article(article)
            if mk:
                magazine_keys = [mk]
        except Exception as e:
            print(f"  マガジン分類スキップ: {e}")

        result = client.publish(
            title=article["title"],
            md_file_path=md_path,
            eyecatch_path=eyecatch_path,  # モンキーパッチ済み(1280x670)で送信
            hashtags=hashtags,
            price=price,
            magazine_key=magazine_keys,
            is_publish=is_publish,
        )

        print(f"NoteClient2 結果: {result}")

        status = "published" if is_publish else "draft_on_note"
        # NoteClient2 は public_url を result["data"] に入れて返す
        note_url = ""
        if isinstance(result, dict):
            data = result.get("data") or {}
            note_url = data.get("public_url") or result.get("note_url") or ""
            # 最終フォールバック: note_key から組み立てる
            if not note_url and data.get("note_key"):
                urlname = os.environ.get("NOTE_URLNAME", "")
                if urlname:
                    note_url = f"https://note.com/{urlname}/n/{data['note_key']}"

        # note.com API が返す数値IDを使う（evaluate.py の upsert と一致させるため）
        note_id_from_api = str(data.get("id", "")) if isinstance(result, dict) and result.get("data") else ""
        return {
            "note_id": note_id_from_api or f"nc2_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}",
            "note_url": note_url,
            "status": status,
            "published_at": datetime.now(JST).isoformat(),
        }

    except Exception as e:
        print(f"NoteClient2 エラー: {e}")
        print("ローカル保存にフォールバック。")
        return save_locally(article)

    finally:
        Path(md_path).unlink(missing_ok=True)


def save_locally(article: dict) -> dict:
    """Markdownファイルとして保存する（フォールバック）。"""
    output_dir = _rtpd()
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    md_path = output_dir / f"{timestamp}.md"

    content = f"# {article['title']}\n\n"
    content += f"タグ: {', '.join(article.get('tags', []))}\n\n"
    content += "---\n\n"
    content += article["free_content"]
    if article.get("paid_content"):
        content += "\n\n---\n**（ここから有料 — 500円）**\n\n"
        content += article["paid_content"]

    md_path.write_text(content, encoding="utf-8")
    print(f"ローカル保存: {md_path}")
    print("→ このファイルの内容をNoteにコピペして投稿してください。")

    return {
        "note_id": f"local_{timestamp}",
        "note_url": "",
        "status": "local_draft",
        "local_path": str(md_path),
        "published_at": datetime.now(JST).isoformat(),
    }


def _save_as_pending_review(article: dict) -> str:
    """REVIEW_MODE 時に記事を publish せず DB に pending_review で保持する。

    Returns: 割り当てた仮 note_id (pending_<timestamp>)
    """
    from core.db import upsert_article
    now = datetime.now(JST)
    pending_id = f"pending_{int(now.timestamp() * 1000)}"
    upsert_article({
        "note_id": pending_id,
        "title": article.get("title", ""),
        "genre": article.get("genre", ""),
        "tags": article.get("tags", []),
        "note_url": "",
        "status": "pending_review",
        "published_at": "",
        "created_at": now.isoformat(),
        "free_content": article.get("free_content", ""),
        "paid_content": article.get("paid_content", ""),
        "views": 0, "likes": 0, "comments": 0, "revenue": 0,
    })
    print(f"pending_review 保存: {pending_id} {article.get('title','')[:40]}")
    return pending_id


def record_article(article: dict, publish_result: dict):
    """投稿結果をhistory.jsonに記録する。"""
    history = load_history()

    record = {
        "title": article["title"],
        "genre": article.get("genre", ""),
        "tags": article.get("tags", []),
        "note_id": publish_result.get("note_id", ""),
        "note_url": publish_result.get("note_url", ""),
        "status": publish_result.get("status", ""),
        "published_at": publish_result.get("published_at", ""),
        "views": 0,
        "likes": 0,
        "comments": 0,
        "revenue": 0,
    }

    history["articles"].append(record)
    history["metrics_summary"]["total_articles"] = len(history["articles"])

    save_history(history)
    print(f"記録完了: {record['title']}")

    # DB にも本文付きで保存（後で thread 再生成・lift 抽出に必要）
    try:
        from core.db import upsert_article
        upsert_article({
            "note_id": publish_result.get("note_id", ""),
            "title": article.get("title", ""),
            "genre": article.get("genre", ""),
            "tags": article.get("tags", []),
            "note_url": publish_result.get("note_url", ""),
            "status": publish_result.get("status", "published"),
            "published_at": publish_result.get("published_at", "") or datetime.now(JST).isoformat(),
            "free_content": article.get("free_content", ""),
            "paid_content": article.get("paid_content", ""),
            "views": 0, "likes": 0, "comments": 0, "revenue": 0,
        })
    except Exception as e:
        print(f"DB保存失敗: {e}")


def _utm_url(note_url: str, source: str = "tw") -> str:
    """note URL に流入元追跡用クエリを付与する。

    どのチャネル(SNS) → どの記事 → 購入 のファネルを後で集計するための仕込み。
    note 側の analytics でリファラを見るとき、これがあると拾える。
    """
    if not note_url:
        return note_url
    sep = "&" if "?" in note_url else "?"
    return f"{note_url}{sep}from={source}"


def generate_tweet_drafts(article: dict, note_url: str) -> list:
    """記事のツイート文案を3パターン生成する（Ollama優先、失敗時Claudeフォールバック）。"""
    # tweet 用に流入元パラメータ付与
    note_url = _utm_url(note_url, source="tw")
    try:
        from core.llm.wrapper import call_llm_json
        from core.paths import strategy_path as _stp
        strategy = json.loads(_stp().read_text(encoding="utf-8"))
        tweet_params = strategy.get("content_params", {}).get("tweet_params", {})

        tone = tweet_params.get("tone", "親しみやすく、押し売り感なし")
        hashtags = tweet_params.get("hashtags", ["#ChatGPT", "#仕事術"])
        hashtag_str = " ".join(hashtags)

        # アカウント状況を動的に取得
        history = json.loads(HISTORY_JSON.read_text(encoding="utf-8"))
        total_articles = history.get("metrics_summary", {}).get("total_articles", 0)

        account_stage = tweet_params.get("account_stage", "初心者")

        # 軽量モデル用: 3パターンを個別に呼び出す（説明文混入を防ぐ）
        from core.llm.wrapper import call_llm
        normalized = []

        def _clean(text: str) -> str:
            """生成テキストから余計なラベル・前置きを削除。"""
            import re
            text = text.strip()
            # 行頭のラベル除去（「ツイート:」「つぶやきの本文:」など）
            label_patterns = [
                r"^ツイート[:：]\s*",
                r"^本文[:：]\s*",
                r"^投稿[:：]\s*",
                r"^出力[:：]\s*",
                r"^回答[:：]\s*",
                r"^.*の本文[:：]\s*",
                r"^.*の本文$\n",
                r"^つぶやき[:：]\s*",
                r"^豆知識[:：]\s*",
                r"^リンク付き[:：]\s*",
            ]
            for pat in label_patterns:
                text = re.sub(pat, "", text, flags=re.MULTILINE)
            text = text.strip().strip('"').strip("'").strip("「").strip("」")
            return text.strip()

        # 記事連動: link 1本 + advisor推奨パターンから2本（計3本）
        # リンク付き1本で誘導 + ティーザー2本で興味喚起
        try:
            from core.learning.advisor import get_advice
            adv_patterns = get_advice().get("tweet_draft_patterns") or []
            extras = [p for p in adv_patterns if p != "link"][:2]
        except Exception:
            extras = ["experiment", "question"]
        if len(extras) < 2:
            extras = (extras + ["experiment", "question"])[:2]
        chosen = ["link"] + extras
        print(f"  ツイートパターン: {chosen}")

        PATTERN_PROMPTS = {
            "link": ("リンク付き",
                f"記事タイトル「{article['title']}」をXに投稿するための宣伝文を書いてください。\n\n"
                f"必須フォーマット (必ず3要素を含める):\n"
                f"1行目〜: 100文字以内の本文 (自分の気づき・疑問・観察を1〜2文で)\n"
                f"次の行: {hashtag_str}  ←ハッシュタグを必ず付ける\n"
                f"次の行: {note_url}  ←リンクを必ず付ける\n\n"
                "禁止事項:\n- 「読んでみたい人へ」など押し付け表現\n- 本文に URL やタグを混ぜる (専用行に分ける)\n- 説明や前置き\n\n"
                "ツイート本文のみ出力:", 0.8),
            "trivia": ("豆知識",
                f"記事「{article['title']}」のポイントを1つだけ抜き出し、Xの豆知識ツイートにしてください。\n\n"
                f"条件:\n- 140文字以内の本文\n- 最後に改行して {hashtag_str}\n"
                "- リンクや記事への誘導は不要、独立したTipsとして\n"
                "- 説明や前置きは一切不要、ツイート本文のみ\n\nツイート:", 0.8),
            "musing": ("つぶやき",
                f"記事「{article['title']}」に関連した、Xでのつぶやきを書いてください。\n\n"
                "条件:\n- 140文字以内の本文\n- ハッシュタグなし、自然な独白・気づき系\n"
                "- 観察・思考・共感を誘う内容\n"
                "- 【絶対禁止】具体的な金額・収益・期間を「自分の実績」として書かない\n"
                "- 説明や前置きは一切不要、ツイート本文のみ\n\nツイート:", 0.9),
            "question": ("問いかけ",
                f"記事「{article['title']}」のテーマで読者に問いかけるツイートを書いてください。\n\n"
                "条件:\n- 140文字以内、最後を「？」で終える\n- 答えやすい二択 or オープン質問\n"
                "- ハッシュタグなし\n- 説明や前置きは一切不要、ツイート本文のみ\n\nツイート:", 0.8),
            "experience": ("体験談",
                f"記事「{article['title']}」のテーマに対する観察や気づきをツイートで書いてください。\n\n"
                "条件:\n- 140文字以内\n- 「〜と気づいた」「〜だと思う」など主観の範囲で書く\n"
                "- 【絶対禁止】「私は◯ヶ月で◯円稼いだ」「収益ゼロから◯万円」など架空の実績・体験を書かない\n"
                "- 一般的な観察・読者と共有する気づきとして書く\n"
                "- ハッシュタグなし\n- 説明や前置きは一切不要、ツイート本文のみ\n\nツイート:", 0.85),
            "list": ("箇条書き",
                f"記事「{article['title']}」のポイントを①②③形式の3点で要約したツイートを書いてください。\n\n"
                f"条件:\n- 140文字以内\n- ①②③の3項目それぞれ20文字以内\n- 最後に改行して {hashtag_str}\n"
                "- 説明や前置きは一切不要、ツイート本文のみ\n\nツイート:", 0.7),
            "experiment": ("検証メモ",
                f"記事「{article['title']}」のテーマで実際に試した結果のように書いてください。\n\n"
                "条件:\n- 140文字以内\n- 「〇〇を試した。結果は△△だった」のような検証スタイル\n"
                "- 試行した期間や条件を具体的に書く (例: 1週間、3パターン)\n"
                "- 【絶対禁止】架空の収益・フォロワー数・派手な実績は書かない\n"
                "- 「やってみた / 試した / 記録した」のトーンで\n"
                "- ハッシュタグなし\n- 説明や前置きは一切不要、ツイート本文のみ\n\nツイート:", 0.85),
            "comparison": ("比較メモ",
                f"記事「{article['title']}」のテーマで2つの方法を比較したツイートを書いてください。\n\n"
                "条件:\n- 140文字以内\n- 「AとBを試した。Aの方が△△だった」のような比較\n"
                "- 結論を断定しすぎず「自分の場合は」と添える\n"
                "- ハッシュタグなし\n- 説明や前置きは一切不要、ツイート本文のみ\n\nツイート:", 0.85),
            "fail_report": ("失敗報告",
                f"記事「{article['title']}」のテーマで「やってみたけどダメだった」報告ツイートを書いてください。\n\n"
                "条件:\n- 140文字以内\n- 「〇〇をやってみたけどダメだった。理由は△△っぽい」のスタイル\n"
                "- 自虐ではなく学びとして淡々と\n"
                "- ハッシュタグなし\n- 説明や前置きは一切不要、ツイート本文のみ\n\nツイート:", 0.85),
        }

        for pat_key in chosen:
            if pat_key not in PATTERN_PROMPTS:
                continue
            label, prompt_text, temp = PATTERN_PROMPTS[pat_key]
            try:
                t = _clean(call_llm(prompt_text, task_type="tweet_drafts", temperature=temp))
                if t:
                    # link パターンは URL 必須 → Claude が忘れた場合は強制追加
                    if pat_key == "link" and note_url and note_url not in t:
                        t = t.rstrip() + "\n" + note_url
                    normalized.append({"type": label, "text": t})
            except Exception as e:
                print(f"{label} 生成失敗: {e}")

        print(f"ツイート文案を{len(normalized)}パターン生成しました")
        return normalized

    except Exception as e:
        print(f"ツイート文案生成エラー: {e}")
        return []


def main():
    """下書きを投稿する。--all で全下書きを一括投稿。"""
    drafts_dir = _dd()
    if not drafts_dir.exists():
        print("下書きがありません。先に generate.py を実行してください。")
        sys.exit(1)

    draft_files = sorted(drafts_dir.glob("draft_*.json"))
    if not draft_files:
        print("下書きがありません。先に generate.py を実行してください。")
        sys.exit(1)

    if "--all" in sys.argv:
        targets = draft_files
    else:
        targets = [draft_files[-1]]  # 最新1件

    published_dir = _pd()
    published_dir.mkdir(parents=True, exist_ok=True)

    last_article = None
    last_note_url = ""
    last_tweet_drafts = []

    # レビューモード時は publish せず DB に pending_review として保留する
    try:
        from core.db import review_mode_enabled
        _review_on = review_mode_enabled()
    except Exception:
        _review_on = False

    for i, draft_path in enumerate(targets):
        article = json.loads(draft_path.read_text(encoding="utf-8"))
        print(f"\n--- [{i + 1}/{len(targets)}] {article['title']} ---")

        if _review_on:
            print("📝 レビューモード ON → pending_review として保留")
            try:
                _save_as_pending_review(article)
            except Exception as e:
                print(f"pending_review 保存失敗: {e}")
            draft_path.rename(published_dir / draft_path.name)
            continue

        result = publish_via_noteclient(article)
        record_article(article, result)

        draft_path.rename(published_dir / draft_path.name)

        last_article = article
        if isinstance(result, dict) and result.get("ok") is not False:
            note_url = result.get("note_url", "")
            if not note_url and isinstance(result, dict):
                note_key = result.get("data", {}).get("note_key", "") if "data" in result else ""
                if note_key:
                    urlname = os.environ.get("NOTE_URLNAME", "")
                    note_url = f"https://note.com/{urlname}/n/{note_key}" if urlname else ""
            last_note_url = note_url

        if i < len(targets) - 1:
            import time
            time.sleep(5)

    # 最後に投稿した記事のツイート文案を生成
    if last_article:
        last_tweet_drafts = generate_tweet_drafts(last_article, last_note_url)

        # 全パターンをキュー追加。リンク付きは post_next_from_db で最優先化される
        try:
            from core.db import add_to_tweet_queue
            for draft in last_tweet_drafts:
                if isinstance(draft, dict) and draft.get("text"):
                    add_to_tweet_queue(draft.get("type", "ツイート"), draft["text"])
            print(f"  キュー追加: {len(last_tweet_drafts)}件 (リンク付きは次のスロットで最優先)")
        except Exception as e:
            print(f"  キュー追加失敗: {e}")

        # スレッド投稿も生成（週2〜3本ペース: 直近3日に thread が無ければ作る）
        try:
            from core.db import get_connection, add_to_tweet_queue
            conn = get_connection()
            recent_threads = conn.execute(
                "SELECT COUNT(*) AS c FROM tweet_queue WHERE type='thread' AND posted=0"
            ).fetchone()["c"]
            recent_posted_threads = conn.execute(
                "SELECT COUNT(*) AS c FROM tweet_queue WHERE type='thread' AND posted=1 AND id IN ("
                "SELECT id FROM tweet_queue ORDER BY id DESC LIMIT 6)"
            ).fetchone()["c"]
            try:
                from core.learning.advisor import get_advice
                weekly_cap = int(get_advice().get("thread_weekly_target", 2))
            except Exception:
                weekly_cap = 2
            if recent_threads + recent_posted_threads < weekly_cap:
                from platforms.x.thread_generator import generate_thread
                thread_tweets = generate_thread(last_article, last_note_url)
                if len(thread_tweets) >= 3:
                    # 遅延配信: リンク付きとの重複を避けるため 2日後の朝に投稿
                    from datetime import datetime, timezone, timedelta
                    jst = timezone(timedelta(hours=9))
                    scheduled = (datetime.now(jst) + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
                    add_to_tweet_queue(
                        "thread",
                        json.dumps(thread_tweets, ensure_ascii=False),
                        scheduled_at=scheduled,
                    )
                    print(f"スレッド({len(thread_tweets)}ツイート)をキューに追加 (配信予定: {scheduled[:16]})")
            else:
                print(f"スレッド既に {recent_threads + recent_posted_threads}本ある → 生成スキップ")
        except Exception as e:
            print(f"スレッド生成スキップ: {e}")

    print(f"\n全{len(targets)}本の投稿完了!")
    return last_article, last_note_url, last_tweet_drafts


if __name__ == "__main__":
    main()

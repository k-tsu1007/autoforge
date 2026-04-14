"""Mention Reply Agent — 自分へのメンション・リプライに対応する。

処理フロー:
1. run_scan(): X通知ページからメンションを取得
   - 全メンションをいいね
   - Claudeが「返すべきか / 会話を終えるべきか」を判断
   - 返す場合は 15〜45分のランダム遅延を付けて mention_reply_queue に積む
2. run_send(): キューの中で send_after を過ぎたものをリプライ送信
"""

import asyncio
import json
import os
import random
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

ROOT = Path(__file__).resolve().parents[2]
JST = timezone(timedelta(hours=9))

_env_path = ROOT / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        _k = _k.strip()
        _v = _v.strip().strip('"').strip("'")
        if _k and _k not in os.environ:
            os.environ[_k] = _v


# === 会話終了パターン ===
_END_PATTERNS = [
    "ありがとう", "ありがとうございます", "thanks", "thank you",
    "了解", "わかりました", "なるほど", "参考になりました",
    "頑張ります", "頑張ってみます", "やってみます",
]

MAX_SCAN_PER_RUN = 20  # 一度に処理するメンション上限
DELAY_MIN_MIN = 15     # 最小遅延（分）
DELAY_MAX_MIN = 45     # 最大遅延（分）


def _to_cdp_cookies(pw_cookies: list) -> list:
    """Playwright cookie format → nodriver/CDP format."""
    result = []
    for c in pw_cookies:
        cc = {
            "name": c.get("name", ""),
            "value": c.get("value", ""),
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
            "secure": c.get("secure", False),
            "httpOnly": c.get("httpOnly", False),
        }
        exp = c.get("expires", -1)
        if exp and exp > 0:
            cc["expires"] = int(exp)
        ss = c.get("sameSite")
        if ss:
            cc["sameSite"] = ss
        result.append(cc)
    return result


def _load_cookies():
    try:
        from core.paths import x_session_path
        p = x_session_path()
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _already_processed(mention_url: str, author: str = "") -> bool:
    """mention_reply_queue または growth_actions に処理済みか確認。
    同一著者に対して直近24時間以内にメンション返信していたらスキップ。"""
    try:
        from core.db import get_connection
        conn = get_connection()
        # キュー内に同URL
        row = conn.execute(
            "SELECT id FROM mention_reply_queue WHERE mention_url = ?", (mention_url,)
        ).fetchone()
        if row:
            return True
        # growth_actionsに同URL
        row = conn.execute(
            "SELECT id FROM growth_actions WHERE target_url = ?",
            (mention_url,)
        ).fetchone()
        if row:
            return True
        # 同一著者に対して直近24時間以内にメンション返信済みならスキップ
        if author:
            row = conn.execute(
                "SELECT id FROM growth_actions WHERE target_user = ? AND action_type = 'mention_reply' "
                "AND executed_at >= datetime('now', '+9 hours', '-24 hours')",
                (author,)
            ).fetchone()
            if row:
                return True
    except Exception:
        pass
    return False


def _record_like(mention_url: str, mention_text: str, author: str) -> None:
    try:
        from core.db import record_growth_action
        record_growth_action(
            action_type="mention_like",
            target_url=mention_url,
            target_user=author,
            target_text=mention_text[:500],
            success=True,
        )
    except Exception as e:
        print(f"like記録失敗: {e}")


def _queue_reply(mention_url: str, mention_text: str, author: str, reply_text: str) -> None:
    """遅延付きでリプライキューに積む。レビューモード時は approved=NULL で保留。"""
    delay_min = random.randint(DELAY_MIN_MIN, DELAY_MAX_MIN)
    send_after = (datetime.now(JST) + timedelta(minutes=delay_min)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        from core.db import get_connection, transaction, review_mode_enabled
        approved = None if review_mode_enabled() else 1
        with transaction() as c:
            c.execute(
                """INSERT OR IGNORE INTO mention_reply_queue
                   (mention_url, mention_text, mention_author, reply_text, send_after, approved)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (mention_url, mention_text[:500], author, reply_text, send_after, approved),
            )
        if approved is None:
            print(f"  📋 レビュー待ちキュー追加: {reply_text[:40]}")
        else:
            print(f"  📋 キュー追加 (送信予定 {delay_min}分後): {reply_text[:40]}")
    except Exception as e:
        print(f"キュー追加失敗: {e}")


async def _fetch_tweet_context_async(tab, tweet_url: str) -> dict:
    """ツイートページに遷移して会話スレッド・引用ツイートのテキストを取得。

    Returns: {
        "thread_texts": [...],   # 親ツイート群（古い順）
        "quoted_text": str,      # 引用元ツイート（あれば）
    }
    """
    ctx = {"thread_texts": [], "quoted_text": ""}
    try:
        await tab.get(tweet_url)
        await asyncio.sleep(4)

        # スレッド上の親ツイートと引用ツイートを JS で取得
        result = await tab.evaluate("""
            () => {
                const arts = [...document.querySelectorAll('article')];
                const threads = [];
                for (const a of arts) {
                    const textEl = a.querySelector('[data-testid="tweetText"]');
                    if (!textEl) continue;
                    const txt = textEl.innerText.trim();
                    if (txt) threads.push(txt);
                }
                // 引用ツイート: article 内にネストした article
                let quotedText = '';
                const quoteArts = [...document.querySelectorAll('article article')];
                if (quoteArts.length > 0) {
                    const qEl = quoteArts[0].querySelector('[data-testid="tweetText"]');
                    if (qEl) quotedText = qEl.innerText.trim();
                }
                return { threads, quotedText };
            }
        """)
        if result:
            thread_texts = result.get("threads") or []
            # 最後の記事がメンション本体なので除き、直近3件だけ残す
            ctx["thread_texts"] = thread_texts[:-1][-3:] if len(thread_texts) > 1 else []
            ctx["quoted_text"] = result.get("quotedText") or ""

    except Exception as e:
        print(f"  コンテキスト取得失敗: {e}")
    return ctx


def _decide_reply(mention_text: str, context: dict | None = None, force: bool = False) -> dict:
    """Claudeが返信すべきか判断し、返すなら文章も生成。

    context: _fetch_tweet_context の戻り値（スレッド履歴・引用ツイート）
    force: True のとき should_reply チェックをスキップして強制生成（再生成ボタン用）
    Returns: {"should_reply": bool, "reply": str}
    """
    if not force:
        # 明らかな会話終了は即スキップ
        lower = mention_text.lower()
        for pat in _END_PATTERNS:
            if pat in mention_text or pat in lower:
                return {"should_reply": False, "reply": ""}

    try:
        from core.llm.wrapper import call_llm

        # コンテキスト文字列を組み立て
        ctx_section = ""
        if context:
            thread = context.get("thread_texts") or []
            quoted = context.get("quoted_text") or ""
            if thread:
                ctx_section += "\n【会話の流れ（古い順）】\n"
                ctx_section += "\n".join(f"- {t[:150]}" for t in thread)
            if quoted:
                ctx_section += f"\n【引用元ツイート】\n{quoted[:200]}"

        # コンテキストセクションを整形（テンプレートの {context} に差し込む）
        context_block = ""
        if ctx_section:
            context_block = ctx_section.strip() + "\n\n"

        # インスタンスのプロンプトファイルを優先読み込み
        prompt = ""
        try:
            from core.paths import load_prompt
            prompt = load_prompt(
                "mention_reply.txt",
                mention_text=mention_text[:300],
                context=context_block,
            )
        except Exception:
            pass

        if not prompt:
            prompt = f"""あなたは本業をしながらAI・note・SNSの副収入を試している30代です。

{context_block}【相手の返信】
{mention_text[:300]}

【最重要：まず会話の流れとトーンを読む】
- ネタ・ユーモア系のやり取りには同じノリで軽く返す（真面目な文章は逆効果）
- 相手のトーン・テンションに合わせることを最優先にする

【判断基準】
- 質問・感想・ボケ・興味 → 返す
- 感謝・了解・スタンプで会話がひと段落 → 終える
- 否定・クレーム → 終える

【出力フォーマット】
REPLY: yes または no
TEXT: （返す場合のみ70字以内の一言）"""

        raw = call_llm(prompt, task_type="strategy_evolution", temperature=0.8, max_tokens=150).strip()

        should_reply = False
        reply_text = ""
        for line in raw.splitlines():
            if line.startswith("REPLY:"):
                should_reply = "yes" in line.lower()
            elif line.startswith("TEXT:"):
                reply_text = line[5:].strip()

        return {"should_reply": should_reply, "reply": reply_text}
    except Exception as e:
        print(f"返信判断失敗: {e}")
        return {"should_reply": False, "reply": ""}


async def _generate_reply_text_async(mention_text: str, mention_url: str = "") -> str:
    tweet_ctx = {"thread_texts": [], "quoted_text": ""}

    if mention_url:
        try:
            import nodriver as uc
            cookies = _load_cookies()
            if cookies:
                browser = await uc.start(headless=True)
                try:
                    await browser.cookies.set_all(_to_cdp_cookies(cookies))
                    tab = await browser.get("about:blank")
                    tweet_ctx = await _fetch_tweet_context_async(tab, mention_url)
                finally:
                    browser.stop()
        except Exception as e:
            print(f"  コンテキスト取得失敗（再生成）: {e}")

    result = _decide_reply(mention_text, context=tweet_ctx, force=True)
    return result.get("reply", "")


def generate_reply_text(mention_text: str, mention_url: str = "") -> str:
    """返信テキストだけを強制生成（should_reply 判断をスキップ）。再生成ボタン用。

    mention_url が渡された場合はスレッドコンテキストを取得して
    _decide_reply() と同じプロンプトで生成する。
    """
    return asyncio.run(_generate_reply_text_async(mention_text, mention_url))


async def _run_scan_async() -> dict:
    """通知ページをスキャンしてメンションをいいね＆返信キューに積む。"""
    try:
        import nodriver as uc
    except ImportError:
        print("nodriver が見つかりません")
        return {"liked": 0, "queued": 0}

    cookies = _load_cookies()
    if not cookies:
        print("x_session.json が見つかりません")
        return {"liked": 0, "queued": 0}

    liked = 0
    queued = 0
    skipped = 0
    pending_mentions: list[dict] = []

    browser = None
    try:
        browser = await uc.start(headless=True)
        await browser.cookies.set_all(_to_cdp_cookies(cookies))

        tab = await browser.get("https://x.com/notifications")
        await asyncio.sleep(5)

        if "/login" in tab.url or "/flow/login" in tab.url:
            print("セッション切れ")
            return {"liked": 0, "queued": 0}

        # 「@メンション」タブをクリック
        try:
            tab_clicked = await tab.evaluate("""
                () => {
                    const tabs = [...document.querySelectorAll('[role="tab"]')];
                    const labels = ['Mentions', '@メンション', 'メンション'];
                    for (const label of labels) {
                        const t = tabs.find(el => el.innerText.includes(label));
                        if (t) { t.click(); return label; }
                    }
                    return null;
                }
            """)
            if tab_clicked:
                await asyncio.sleep(3)
                print(f"「{tab_clicked}」タブをクリック")
            else:
                print("Mentionsタブ未発見 — デフォルトタブで続行")
        except Exception as e:
            print(f"タブクリック失敗(続行): {e}")

        # 通知記事一覧を JS で取得
        my_user = os.environ.get("X_USERNAME", "fuku_ai07").lower()
        articles_data = await tab.evaluate(f"""
            () => {{
                const arts = [...document.querySelectorAll('article')];
                const my_user = '{my_user}';
                const results = [];
                for (const art of arts.slice(0, {MAX_SCAN_PER_RUN})) {{
                    try {{
                        const timeEl = art.querySelector('time');
                        if (!timeEl) continue;
                        const datetime = timeEl.getAttribute('datetime') || '';
                        const parentA = timeEl.closest('a');
                        const href = parentA ? parentA.getAttribute('href') : null;
                        if (!href) continue;
                        const mention_url = 'https://x.com' + href;
                        const userEl = art.querySelector('[data-testid="User-Name"]');
                        const author_full = userEl ? userEl.innerText : '';
                        const author = author_full.split('\\n')[0] || 'unknown';
                        if (author_full.toLowerCase().includes('@' + my_user) ||
                            author_full.toLowerCase().includes(my_user)) continue;
                        if (mention_url.toLowerCase().includes('/' + my_user + '/')) continue;
                        const textEl = art.querySelector('[data-testid="tweetText"]');
                        const mention_text = textEl ? textEl.innerText : '';
                        if (!mention_text) continue;
                        results.push({{ mention_url, author, mention_text, datetime }});
                    }} catch(e) {{}}
                }}
                return results;
            }}
        """)

        print(f"メンション取得: {len(articles_data) if articles_data else 0}件")

        # 24時間以上前のメンションをフィルタ & 処理済みチェック
        now_utc = datetime.now(timezone.utc)
        for item in (articles_data or []):
            mention_url = item.get("mention_url", "")
            author = item.get("author", "unknown")
            mention_text = item.get("mention_text", "")
            time_dt_str = item.get("datetime", "")

            if time_dt_str:
                try:
                    tweet_time = datetime.fromisoformat(time_dt_str.replace("Z", "+00:00"))
                    age = now_utc - tweet_time
                    if age.total_seconds() > 86400:
                        print(f"  ⏭ 古い ({int(age.total_seconds()/3600)}時間前): {time_dt_str[:16]}")
                        continue
                except Exception:
                    pass

            if _already_processed(mention_url, author=author):
                print(f"  ⏭ 処理済み: @{author} {mention_url[-40:]}")
                continue

            pending_mentions.append({
                "url": mention_url,
                "text": mention_text,
                "author": author,
            })

        # ── フェーズ2: 各ツイートに遷移してコンテキスト取得 → 返信判断 ──
        print(f"  処理対象: {len(pending_mentions)}件")
        for m in pending_mentions:
            try:
                mention_url = m["url"]
                mention_text = m["text"]
                author = m["author"]
                print(f"  処理: @{author} — {mention_text[:50]}")

                # ツイートページへ遷移してスレッド・引用コンテキスト取得
                tweet_ctx = await _fetch_tweet_context_async(tab, mention_url)
                if tweet_ctx["thread_texts"]:
                    print(f"    スレッド{len(tweet_ctx['thread_texts'])}件取得")
                if tweet_ctx["quoted_text"]:
                    print(f"    引用元ツイート取得: {tweet_ctx['quoted_text'][:40]}")

                # 返信判断（コンテキスト付き）
                decision = _decide_reply(mention_text, context=tweet_ctx)
                if decision["should_reply"] and decision["reply"]:
                    _queue_reply(mention_url, mention_text, author, decision["reply"])
                    queued += 1
                else:
                    # 返信しない → ツイートページで既存tabを使っていいね
                    try:
                        like_count = await tab.evaluate(
                            "() => document.querySelectorAll('[data-testid=\"like\"]').length"
                        )
                        if like_count and like_count > 0:
                            await tab.evaluate("document.querySelector('[data-testid=\"like\"]').click()")
                            await asyncio.sleep(1)
                            liked += 1
                            print(f"    ❤️ いいね（返信なし）")
                    except Exception as le:
                        print(f"❌ いいね失敗: {le}")
                    _record_like(mention_url, mention_text, author)
                    skipped += 1
                    print(f"    ⏭ 返信なし（会話終了 or 不要と判断）")

                await asyncio.sleep(0.8)
            except Exception as e:
                print(f"  処理エラー: {e}")
                continue

        # セッション更新
        try:
            from core.paths import x_session_path
            new_cookies = await browser.cookies.get_all()
            x_session_path().write_text(
                json.dumps(new_cookies, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    except Exception as e:
        print(f"スキャンエラー: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if browser:
            browser.stop()

    print(f"スキャン完了: いいね={liked} キュー追加={queued} スキップ={skipped}")
    return {"liked": liked, "queued": queued, "skipped": skipped}


def run_scan() -> dict:
    """通知ページをスキャンしてメンションをいいね＆返信キューに積む。"""
    return asyncio.run(_run_scan_async())


MAX_FAIL_COUNT = 3        # これ以上失敗したら放棄
QUEUE_EXPIRE_HOURS = 24  # 作成からこの時間を超えたら放棄


def run_send() -> dict:
    """キューの中で send_after を過ぎたものを送信する。
    失敗3回以上 or 作成から24時間超過のアイテムは sent=2 で放棄する。"""
    from platforms.x.actions import reply_tweet

    now_jst = datetime.now(JST)
    now_str = now_jst.strftime("%Y-%m-%d %H:%M:%S")
    expire_str = (now_jst - timedelta(hours=QUEUE_EXPIRE_HOURS)).strftime("%Y-%m-%d %H:%M:%S")

    try:
        from core.db import get_connection, transaction
        conn = get_connection()

        # 期限切れアイテムを放棄（sent=2）
        with transaction() as c:
            c.execute(
                "UPDATE mention_reply_queue SET sent = 2 WHERE sent = 0 AND created_at <= ?",
                (expire_str,)
            )

        pending = conn.execute(
            "SELECT id, mention_url, mention_text, mention_author, reply_text, "
            "COALESCE(fail_count, 0) as fail_count FROM mention_reply_queue "
            "WHERE sent = 0 AND approved = 1 AND send_after <= ?",
            (now_str,)
        ).fetchall()
    except Exception as e:
        print(f"キュー取得失敗: {e}")
        return {"sent": 0}

    sent = 0
    abandoned = 0
    for row in pending:
        row_id = row[0]
        mention_url = row[1]
        mention_text = row[2]
        author = row[3]
        reply_text = row[4]
        fail_count = row[5]

        # 失敗回数上限チェック
        if fail_count >= MAX_FAIL_COUNT:
            with transaction() as c:
                c.execute("UPDATE mention_reply_queue SET sent = 2 WHERE id = ?", (row_id,))
            abandoned += 1
            print(f"  ⏭ 放棄 (失敗{fail_count}回): @{author} — {reply_text[:40]}")
            continue

        print(f"  送信: @{author} — {reply_text[:40]}")
        try:
            ok = reply_tweet(mention_url, reply_text)
            if ok:
                with transaction() as c:
                    c.execute("UPDATE mention_reply_queue SET sent = 1 WHERE id = ?", (row_id,))
                # いいねを送信と同時に実行
                try:
                    from platforms.x.actions import like_tweet
                    if like_tweet(mention_url):
                        print(f"    ❤️ いいね")
                        from core.db import record_growth_action
                        record_growth_action(
                            action_type="mention_like",
                            target_url=mention_url,
                            target_user=author,
                            target_text=reply_text[:500],
                            success=True,
                        )
                except Exception as e:
                    print(f"    いいね失敗（返信は成功）: {e}")
                try:
                    from core.db import record_growth_action
                    record_growth_action(
                        action_type="mention_reply",
                        target_url=mention_url,
                        target_user=author,
                        target_text=reply_text[:500],
                        success=True,
                    )
                except Exception:
                    pass
                try:
                    from core.notify import send_discord
                    send_discord(content=f"💬 メンション返信 → {mention_url}")
                except Exception:
                    pass
                sent += 1
                print(f"    ✅ 送信完了")
            else:
                # 失敗カウントを増やし、次回リトライ用に send_after を5分後に延ばす
                retry_after = (now_jst + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
                with transaction() as c:
                    c.execute(
                        "UPDATE mention_reply_queue SET fail_count = COALESCE(fail_count, 0) + 1, "
                        "send_after = ? WHERE id = ?",
                        (retry_after, row_id)
                    )
                print(f"    ❌ 送信失敗 (失敗{fail_count + 1}回目, 5分後にリトライ)")
        except Exception as e:
            retry_after = (now_jst + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
            try:
                with transaction() as c:
                    c.execute(
                        "UPDATE mention_reply_queue SET fail_count = COALESCE(fail_count, 0) + 1, "
                        "send_after = ? WHERE id = ?",
                        (retry_after, row_id)
                    )
            except Exception:
                pass
            print(f"    送信エラー: {e} (失敗{fail_count + 1}回目, 5分後にリトライ)")

    print(f"送信完了: {sent}件 放棄: {abandoned}件")
    return {"sent": sent, "abandoned": abandoned}


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if mode == "send":
        print(run_send())
    else:
        print(run_scan())

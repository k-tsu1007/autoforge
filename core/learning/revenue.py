"""売上データを取得して articles.revenue に書き込む。

note 有料記事の売上 + アフィリエイト報酬を集計し、記事ごとの
収益データを学習ループに乗せるための層。

note 売上:
    note.com の private sales API を試行する (session.json cookie 必要)。
    成功すれば articles.revenue を更新。
    エンドポイントが変わる可能性があるので複数候補を順に試す。

アフィリエイト報酬:
    A8/もしも 等の各サイトの API は認証や契約が複雑なので、
    まずは手動入力ファイル (instances/<inst>/data/affiliate_revenue.json)
    から取り込む。このファイルを GAS 等で自動更新する流れを想定。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

JST = timezone(timedelta(hours=9))


def _load_session_cookies() -> dict | None:
    """note の session.json (Playwright形式 or {timestamp, cookies}) → dict にして返す。"""
    try:
        from core.paths import note_session_path
        sp = note_session_path()
        if not sp.exists():
            return None
        raw = json.loads(sp.read_text(encoding="utf-8"))
        cookies_list = raw.get("cookies", raw) if isinstance(raw, dict) else raw
        return {c["name"]: c["value"] for c in cookies_list if c.get("name")}
    except Exception:
        return None


def fetch_note_sales() -> list[dict]:
    """note の購入履歴を取得 → [{note_key, count, revenue_jpy}, ...] を返す。

    認証は session.json の cookie。エンドポイントが将来変わる可能性があるので
    複数候補を順に試す。どれもダメなら空リスト。
    """
    cookies = _load_session_cookies()
    if not cookies:
        print("revenue: note session.json なし → skip")
        return []

    headers = {"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"}
    candidates = [
        "https://note.com/api/v1/sales",
        "https://note.com/api/v2/sales",
        "https://note.com/api/v1/dashboard/sales",
    ]

    for url in candidates:
        try:
            resp = requests.get(url, cookies=cookies, headers=headers, timeout=10)
        except Exception as e:
            print(f"  {url} → error {e}")
            continue
        if resp.status_code != 200:
            continue
        try:
            payload = resp.json()
        except Exception:
            continue

        # 想定構造: {data: {sales: [{note_id|note_key, count, total_amount}]}}
        items = (
            (payload.get("data") or {}).get("sales")
            or payload.get("sales")
            or payload.get("data")
            or []
        )
        out = []
        for it in items:
            if not isinstance(it, dict):
                continue
            key = it.get("note_key") or str(it.get("note_id") or "")
            cnt = int(it.get("count") or it.get("purchase_count") or 0)
            amt = int(it.get("total_amount") or it.get("revenue") or 0)
            if key and (cnt or amt):
                out.append({"note_key": key, "count": cnt, "revenue_jpy": amt})
        if out:
            print(f"revenue: note {url} → {len(out)}件")
            return out

    print("revenue: note sales API どれも応答なし (まだ売上0 or APIが変わった可能性)")
    return []


def import_affiliate_revenue_file() -> int:
    """手動入力ファイルからアフィ報酬を読み取る。

    ファイル: instances/<active_instance>/data/affiliate_revenue.json
    フォーマット:
      [
        {"article_id": "<note_id>", "amount_jpy": 1500, "date": "2026-04-15", "source": "a8"},
        ...
      ]
    article_id は articles.note_id とマッチさせる。
    同じ (article_id, date, source) は冪等。
    """
    try:
        from core.paths import data_dir
        path = data_dir() / "affiliate_revenue.json"
    except Exception:
        return 0
    if not path.exists():
        return 0
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"revenue: affiliate_revenue.json 読み込み失敗: {e}")
        return 0
    if not isinstance(rows, list):
        return 0

    # 簡易: article_id 単位で sum し、articles.revenue に上書き集計
    from core.db import get_connection, transaction
    conn = get_connection()
    aggregated: dict[str, int] = {}
    for r in rows:
        aid = r.get("article_id")
        amt = int(r.get("amount_jpy") or 0)
        if aid and amt:
            aggregated[aid] = aggregated.get(aid, 0) + amt

    updated = 0
    with transaction() as c:
        for aid, total in aggregated.items():
            cur = c.execute(
                "SELECT COALESCE(revenue,0) AS r FROM articles WHERE note_id=?",
                (aid,)
            ).fetchone()
            if cur is None:
                continue
            # 既存値より大きい場合のみ更新 (note 売上と合算したいため、別途 add する設計でも良い)
            if total > (cur["r"] or 0):
                c.execute("UPDATE articles SET revenue=? WHERE note_id=?", (total, aid))
                updated += 1
    if updated:
        print(f"revenue: affiliate manual import → {updated}件 更新")
    return updated


def update_articles_revenue() -> dict:
    """note 売上 + affiliate 手動入力 を articles.revenue にマージ。"""
    from core.db import get_connection, transaction
    conn = get_connection()

    # note 側
    note_sales = fetch_note_sales()
    note_updated = 0
    if note_sales:
        with transaction() as c:
            for s in note_sales:
                # note_key (note_url 末尾) で一致する記事を探す
                key = s["note_key"]
                row = c.execute(
                    "SELECT note_id, COALESCE(revenue,0) AS r FROM articles "
                    "WHERE note_url LIKE ? OR note_id=?",
                    (f"%{key}%", key)
                ).fetchone()
                if not row:
                    continue
                # 既存 revenue を上書き (note は最新の累計を返すので置換でOK)
                c.execute("UPDATE articles SET revenue=? WHERE note_id=?",
                          (s["revenue_jpy"], row["note_id"]))
                note_updated += 1

    # affiliate 手動
    aff_updated = import_affiliate_revenue_file()

    return {"note_updated": note_updated, "affiliate_updated": aff_updated}

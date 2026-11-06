import json
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

DB_PATH = "arbitrage.db"


def init_db():
    with _conn() as conn:
        conn.executescript("""
            -- メルカリ相場キャッシュ
            CREATE TABLE IF NOT EXISTS price_cache (
                keyword      TEXT PRIMARY KEY,
                avg_price    INTEGER NOT NULL,
                median_price INTEGER NOT NULL,
                min_price    INTEGER NOT NULL,
                max_price    INTEGER NOT NULL,
                sample_count INTEGER NOT NULL,
                updated_at   TEXT NOT NULL,
                base_keyword TEXT NOT NULL DEFAULT ''
            );

            -- 商品説明文キャッシュ（タイトルに型番がない出品の説明文。型番照合用）
            -- description='' は「説明文なし」を取得済みという意味
            CREATE TABLE IF NOT EXISTS description_cache (
                url         TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                fetched_at  TEXT NOT NULL
            );

            -- 写真から読んだ品番のキャッシュ（説明文でも型番が取れなかった出品。photo_reader）
            -- texts は Claude が写真から読んだ品番の文字列の JSON 配列。'[]' は「読めなかった」
            CREATE TABLE IF NOT EXISTS photo_read_cache (
                url        TEXT PRIMARY KEY,
                texts      TEXT NOT NULL,
                model      TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL
            );

            -- 写真から判定した状態（傷・汚れ）のキャッシュ（案件の写真。photo_reader）
            -- photos_key は写真URLの一覧のハッシュ（写真が差し替えられたら読み直す）。result は判定の JSON
            CREATE TABLE IF NOT EXISTS photo_inspect_cache (
                url        TEXT PRIMARY KEY,
                photos_key TEXT NOT NULL,
                result     TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            );

            -- 写真から Mac 内の AI（Ollama）で判定した状態（傷・汚れ）のキャッシュ（local_inspector）
            -- photos_key は判定の版＋モデル名＋写真URLの一覧のハッシュ。有料版（photo_inspect_cache）とは別
            CREATE TABLE IF NOT EXISTS photo_local_inspect_cache (
                url        TEXT PRIMARY KEY,
                photos_key TEXT NOT NULL,
                result     TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            );

            -- 写真の文字を Mac 内の OCR で読んだ結果（説明文でも型番が取れなかった出品。ocr_reader）
            -- photos_key は OCR の版＋写真URLの一覧のハッシュ。text は読めた文字の全文（'' は文字なし）
            CREATE TABLE IF NOT EXISTS photo_ocr_cache (
                url        TEXT PRIMARY KEY,
                photos_key TEXT NOT NULL,
                text       TEXT NOT NULL,
                model      TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL
            );

            -- 生徒ごとの配信済み案件（競合防止・重複防止）
            CREATE TABLE IF NOT EXISTS student_deals (
                student_email TEXT NOT NULL,
                deal_url      TEXT NOT NULL,
                deal_title    TEXT NOT NULL,
                deal_source   TEXT NOT NULL,
                deal_keyword  TEXT NOT NULL,
                deal_profit   INTEGER NOT NULL,
                sent_at       TEXT NOT NULL,
                PRIMARY KEY (student_email, deal_url)
            );

            -- 案件ごとの配信人数カウント（競合防止）
            CREATE TABLE IF NOT EXISTS deal_assignments (
                deal_url         TEXT PRIMARY KEY,
                assignment_count INTEGER NOT NULL DEFAULT 0,
                first_assigned   TEXT NOT NULL
            );

            -- スキャン結果（ダッシュボード用）
            CREATE TABLE IF NOT EXISTS scan_deals (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                scanned_at      TEXT NOT NULL,
                keyword         TEXT NOT NULL,
                brand           TEXT NOT NULL DEFAULT '',
                model           TEXT NOT NULL DEFAULT '',
                title           TEXT NOT NULL,
                url             TEXT NOT NULL,
                source          TEXT NOT NULL,
                purchase_price  INTEGER NOT NULL,
                reference_price INTEGER NOT NULL,
                estimated_profit INTEGER NOT NULL,
                roi_percent     REAL NOT NULL,
                condition_label TEXT NOT NULL,
                image_url       TEXT NOT NULL DEFAULT '',
                category        TEXT NOT NULL DEFAULT ''
            );
        """)

        # 既存DBへのマイグレーション（新規DBではCREATE TABLEで既に列があるため no-op）
        _ensure_column(conn, "scan_deals", "category", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "price_cache", "base_keyword", "TEXT NOT NULL DEFAULT ''")

        # price_cache.base_keyword のバックフィル（'|' 区切りのキャッシュキーから
        # 価格帯・必須ワードサフィックスを除いた「基本キーワード」を復元する）
        conn.execute("""
            UPDATE price_cache
            SET base_keyword = CASE
                WHEN keyword LIKE '%|%' THEN substr(keyword, 1, instr(keyword, '|') - 1)
                ELSE keyword
            END
            WHERE base_keyword = ''
        """)

        # ヤフオクの説明文キャッシュの取り直し（2026-10-23）: それまでは表示用に途中で省略された
        # 説明文（50文字前後）を保存していたため、修正前に取得した分を消して全文で取り直させる。
        # 日時で区切るので2回目以降は no-op（修正後に取得した分は消さない）
        conn.execute(
            "DELETE FROM description_cache "
            "WHERE url LIKE 'https://auctions.yahoo.co.jp/%' AND fetched_at < '2026-10-23T13:00:00'"
        )


def _ensure_column(conn, table, column, coltype_and_default):
    """指定テーブルに列が無ければ ALTER TABLE で追加する（冪等）。"""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype_and_default}")


# ──────────────────────────────────────────────────────────────────────────────
# 相場キャッシュ
# ──────────────────────────────────────────────────────────────────────────────

def get_cached_price(keyword: str, max_age_hours: int = 12) -> Optional[dict]:
    cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
    with _conn() as conn:
        row = conn.execute(
            "SELECT avg_price, median_price, min_price, max_price, sample_count "
            "FROM price_cache WHERE keyword = ? AND updated_at > ?",
            (keyword, cutoff),
        ).fetchone()
    if row:
        return {
            "avg_price":    row[0],
            "median_price": row[1],
            "min_price":    row[2],
            "max_price":    row[3],
            "sample_count": row[4],
        }
    return None


def cache_price(keyword: str, avg_price: int, median_price: int,
                min_price: int, max_price: int, sample_count: int,
                base_keyword: str = ""):
    with _conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO price_cache
                (keyword, avg_price, median_price, min_price, max_price, sample_count, updated_at, base_keyword)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (keyword, avg_price, median_price, min_price, max_price,
              sample_count, datetime.now().isoformat(), base_keyword))


# ──────────────────────────────────────────────────────────────────────────────
# 商品説明文キャッシュ
# ──────────────────────────────────────────────────────────────────────────────

def get_cached_descriptions(urls: list[str], max_age_hours: int) -> dict[str, str]:
    """取得済みの説明文を返す（{url: 説明文}。説明文なしで取得済みのものは ''）。"""
    if not urls:
        return {}
    cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
    out: dict[str, str] = {}
    with _conn() as conn:
        for i in range(0, len(urls), 500):
            chunk = urls[i:i + 500]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT url, description FROM description_cache "
                f"WHERE url IN ({marks}) AND fetched_at > ?",
                (*chunk, cutoff),
            ).fetchall()
            out.update({r[0]: r[1] for r in rows})
    return out


def cache_descriptions(descriptions: dict[str, str]) -> None:
    """説明文を保存する（値 '' は説明文なし）。"""
    if not descriptions:
        return
    now = datetime.now().isoformat()
    with _conn() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO description_cache (url, description, fetched_at) "
            "VALUES (?, ?, ?)",
            [(u, d or "", now) for u, d in descriptions.items()],
        )
        # 古いものは掃除（DB肥大化防止）
        conn.execute(
            "DELETE FROM description_cache WHERE fetched_at < ?",
            ((datetime.now() - timedelta(days=30)).isoformat(),),
        )


# ──────────────────────────────────────────────────────────────────────────────
# 写真から読んだ品番のキャッシュ（photo_reader）
# ──────────────────────────────────────────────────────────────────────────────

def get_cached_photo_reads(urls: list[str], max_age_hours: int) -> dict[str, list[str]]:
    """写真から読み取り済みの品番の文字列を返す（{url: [文字列]}。読めなかったものは []）。"""
    if not urls:
        return {}
    cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
    out: dict[str, list[str]] = {}
    with _conn() as conn:
        for i in range(0, len(urls), 500):
            chunk = urls[i:i + 500]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT url, texts FROM photo_read_cache "
                f"WHERE url IN ({marks}) AND fetched_at > ?",
                (*chunk, cutoff),
            ).fetchall()
            for url, texts in rows:
                try:
                    out[url] = [t for t in json.loads(texts) if isinstance(t, str)]
                except ValueError:
                    pass  # 壊れた行は無視（次回読み直す）
    return out


def cache_photo_read(url: str, texts: list[str], model: str | None, max_age_hours: int) -> None:
    """写真から読んだ品番の文字列を保存する（texts=[] は読めなかった。model は採用した型番。記録用）。"""
    now = datetime.now()
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO photo_read_cache (url, texts, model, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            (url, json.dumps(texts, ensure_ascii=False), model or "", now.isoformat()),
        )
        # 期限切れは掃除（DB肥大化防止）
        conn.execute(
            "DELETE FROM photo_read_cache WHERE fetched_at < ?",
            ((now - timedelta(hours=max_age_hours)).isoformat(),),
        )


def get_cached_photo_ocr(pairs: list[tuple[str, str]], max_age_hours: int) -> dict[str, str]:
    """写真の OCR 済みの全文を返す（pairs = [(url, photos_key)]。写真URLの一覧と OCR の版が同じものだけ）"""
    if not pairs:
        return {}
    want = dict(pairs)
    urls = list(want)
    cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
    out: dict[str, str] = {}
    with _conn() as conn:
        for i in range(0, len(urls), 500):
            chunk = urls[i:i + 500]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT url, photos_key, text FROM photo_ocr_cache "
                f"WHERE url IN ({marks}) AND fetched_at > ?",
                (*chunk, cutoff),
            ).fetchall()
            for url, key, text in rows:
                if want.get(url) == key:
                    out[url] = text
    return out


def cache_photo_ocr(url: str, photos_key: str, text: str, model: str | None, max_age_hours: int) -> None:
    """写真の OCR の全文を保存する（model は採用した型番。記録用）"""
    now = datetime.now()
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO photo_ocr_cache (url, photos_key, text, model, fetched_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (url, photos_key, text, model or "", now.isoformat()),
        )
        conn.execute(
            "DELETE FROM photo_ocr_cache WHERE fetched_at < ?",
            ((now - timedelta(hours=max_age_hours)).isoformat(),),
        )


def get_cached_photo_inspects(pairs: list[tuple[str, str]], max_age_hours: int) -> dict[str, dict]:
    """写真から判定済みの状態を返す（pairs = [(url, photos_key)]。写真URLの一覧が同じものだけ。{url: 判定}）"""
    if not pairs:
        return {}
    want = dict(pairs)
    urls = list(want)
    cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
    out: dict[str, dict] = {}
    with _conn() as conn:
        for i in range(0, len(urls), 500):
            chunk = urls[i:i + 500]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT url, photos_key, result FROM photo_inspect_cache "
                f"WHERE url IN ({marks}) AND fetched_at > ?",
                (*chunk, cutoff),
            ).fetchall()
            for url, key, result in rows:
                if key != want.get(url):
                    continue  # 写真が差し替えられた → 読み直す
                try:
                    data = json.loads(result)
                except ValueError:
                    continue
                if isinstance(data, dict) and data.get("rank"):
                    out[url] = data
    return out


def cache_photo_inspect(url: str, photos_key: str, result: dict, max_age_hours: int) -> None:
    """写真から判定した状態を保存する"""
    now = datetime.now()
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO photo_inspect_cache (url, photos_key, result, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            (url, photos_key, json.dumps(result, ensure_ascii=False), now.isoformat()),
        )
        conn.execute(
            "DELETE FROM photo_inspect_cache WHERE fetched_at < ?",
            ((now - timedelta(hours=max_age_hours)).isoformat(),),
        )


def get_cached_photo_local_inspects(pairs: list[tuple[str, str]], max_age_hours: int) -> dict[str, dict]:
    """Mac 内の AI で判定済みの状態を返す（pairs = [(url, photos_key)]。キーが同じものだけ。{url: 判定}）"""
    if not pairs:
        return {}
    want = dict(pairs)
    urls = list(want)
    cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
    out: dict[str, dict] = {}
    with _conn() as conn:
        for i in range(0, len(urls), 500):
            chunk = urls[i:i + 500]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT url, photos_key, result FROM photo_local_inspect_cache "
                f"WHERE url IN ({marks}) AND fetched_at > ?",
                (*chunk, cutoff),
            ).fetchall()
            for url, key, result in rows:
                if key != want.get(url):
                    continue
                try:
                    data = json.loads(result)
                except ValueError:
                    continue
                if isinstance(data, dict) and data.get("rank"):
                    out[url] = data
    return out


def cache_photo_local_inspect(url: str, photos_key: str, result: dict, max_age_hours: int) -> None:
    """Mac 内の AI で判定した状態を保存する"""
    now = datetime.now()
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO photo_local_inspect_cache (url, photos_key, result, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            (url, photos_key, json.dumps(result, ensure_ascii=False), now.isoformat()),
        )
        conn.execute(
            "DELETE FROM photo_local_inspect_cache WHERE fetched_at < ?",
            ((now - timedelta(hours=max_age_hours)).isoformat(),),
        )


# ──────────────────────────────────────────────────────────────────────────────
# スキャン結果（ダッシュボード用）
# ──────────────────────────────────────────────────────────────────────────────

def save_scan_deals(deals: list, scanned_at: str) -> None:
    """スキャン結果を scan_deals テーブルに保存する（ダッシュボード表示用）。"""
    with _conn() as conn:
        conn.executemany("""
            INSERT INTO scan_deals
                (scanned_at, keyword, brand, model, title, url, source,
                 purchase_price, reference_price, estimated_profit,
                 roi_percent, condition_label, image_url, category)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (
                scanned_at,
                d.keyword,
                d.brand,
                d.model,
                d.item.title,
                d.item.url,
                d.item.source,
                d.item.price,
                d.mercari_avg_price,
                d.estimated_profit,
                d.roi_percent,
                d.condition_label,
                d.item.image_url or "",
                d.category,
            )
            for d in deals
        ])


def load_scan_deals(days: int = 7) -> list[dict]:
    """直近 days 日のスキャン結果を返す（新しい順）。"""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT scanned_at, keyword, brand, model, title, url, source,
                   purchase_price, reference_price, estimated_profit,
                   roi_percent, condition_label, image_url, category
            FROM (
                SELECT *,
                       ROW_NUMBER() OVER (PARTITION BY url ORDER BY scanned_at DESC) AS rn
                FROM scan_deals
                WHERE scanned_at >= ?
            )
            WHERE rn = 1
            ORDER BY scanned_at DESC, estimated_profit DESC
            """,
            (cutoff,),
        ).fetchall()
    out = [dict(r) for r in rows]
    for r in out:   # 旧ジャンル名（ジュエリー）→ アクセサリー
        if r.get("category") == "ジュエリー":
            r["category"] = "アクセサリー"
    return out


def load_market_reference() -> list[dict]:
    """price_cache から base_keyword 単位で集約した相場一覧を返す（サイドバー表示用）。"""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT base_keyword AS keyword,
                   CAST(ROUND(AVG(median_price)) AS INTEGER) AS median_price,
                   MIN(min_price) AS min_price,
                   MAX(max_price) AS max_price,
                   SUM(sample_count) AS sample_count
            FROM price_cache
            WHERE base_keyword != ''
            GROUP BY base_keyword
            ORDER BY base_keyword
            """
        ).fetchall()
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────────────────────────────────────
# 生徒への配信管理
# ──────────────────────────────────────────────────────────────────────────────

def get_student_sent_urls(email: str) -> set[str]:
    """この生徒にすでに送った案件URLセットを返す。"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT deal_url FROM student_deals WHERE student_email = ?",
            (email,),
        ).fetchall()
    return {r[0] for r in rows}


def get_deal_assignment_count(url: str) -> int:
    """この案件が何人の生徒に送られたかを返す。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT assignment_count FROM deal_assignments WHERE deal_url = ?",
            (url,),
        ).fetchone()
    return row[0] if row else 0


def record_student_deal(email: str, url: str, title: str,
                         source: str, keyword: str, profit: int):
    """生徒への配信を記録し、案件の配信カウントを増やす。"""
    now = datetime.now().isoformat()
    with _conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO student_deals
                (student_email, deal_url, deal_title, deal_source, deal_keyword, deal_profit, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (email, url, title, source, keyword, profit, now))

        conn.execute("""
            INSERT INTO deal_assignments (deal_url, assignment_count, first_assigned)
            VALUES (?, 1, ?)
            ON CONFLICT(deal_url) DO UPDATE SET
                assignment_count = assignment_count + 1
        """, (url, now))


def get_recent_student_deals(email: str, days: int = 7) -> list[dict]:
    """生徒の直近N日間の配信履歴を返す。"""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT deal_title, deal_source, deal_keyword, deal_profit, sent_at "
            "FROM student_deals "
            "WHERE student_email = ? AND sent_at > ? "
            "ORDER BY deal_profit DESC",
            (email, cutoff),
        ).fetchall()
    return [
        {"title": r[0], "source": r[1], "keyword": r[2],
         "profit": r[3], "sent_at": r[4]}
        for r in rows
    ]


# ──────────────────────────────────────────────────────────────────────────────
# 内部ユーティリティ
# ──────────────────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

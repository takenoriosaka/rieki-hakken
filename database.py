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
                updated_at   TEXT NOT NULL
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
        """)


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
                min_price: int, max_price: int, sample_count: int):
    with _conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO price_cache
                (keyword, avg_price, median_price, min_price, max_price, sample_count, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (keyword, avg_price, median_price, min_price, max_price,
              sample_count, datetime.now().isoformat()))


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

"""
Yahoo!オークション スクレイパー

改善点:
  - 除外キーワード対応（-保存袋 -ショッパー 等）
  - aucminprice で¥1スタート問題を根本解決（min_buy_price を最低価格フィルターに使用）
  - data-auction-buynowprice を優先取得し、次点で data-auction-price を使用
"""

import re
import time

import requests
from bs4 import BeautifulSoup
from models import Item

_SEARCH_URL = "https://auctions.yahoo.co.jp/search/search"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}


# ── 一時的なエラーのリトライ ──────────────────────────────────────────
# 500/502/503/429 と接続エラー・タイムアウトは指数バックオフで最大3回リトライする。
_RETRY_STATUSES = {429, 500, 502, 503}
_RETRY_DELAYS = (5, 15, 45)     # 秒。1回目→2回目→3回目のリトライ前の待ち時間
# リトライしても失敗したリクエストが連続したら、以降はリトライせず1回だけ試す
# （ヤフオク側が実行環境ごと拒否している場合、毎回 65秒×件数 待つのを避ける）。
# 1回でも成功したらリセットしてリトライを再開する。
_GIVE_UP_AFTER = 3
_consecutive_failures = 0


def reset_retry_state() -> None:
    """スキャン開始時に呼ぶ（前回スキャンでリトライを打ち切った状態を持ち越さない）。"""
    global _consecutive_failures
    _consecutive_failures = 0


class _RetryableStatus(Exception):
    pass


def _get(url: str, params: dict | None = None, timeout: int = 15) -> requests.Response:
    """GET してレスポンスを返す。一時的なエラーはリトライし、最終的に失敗したら例外を投げる。"""
    global _consecutive_failures
    delays = _RETRY_DELAYS if _consecutive_failures < _GIVE_UP_AFTER else ()
    attempt = 0
    while True:
        try:
            resp = requests.get(url, params=params, headers=_HEADERS, timeout=timeout)
            if resp.status_code in _RETRY_STATUSES:
                raise _RetryableStatus(f"{resp.status_code}")
            resp.raise_for_status()
            _consecutive_failures = 0
            return resp
        except (_RetryableStatus, requests.ConnectionError, requests.Timeout) as e:
            if attempt >= len(delays):
                _consecutive_failures += 1
                if isinstance(e, _RetryableStatus):
                    resp.raise_for_status()   # 従来と同じ HTTPError メッセージで呼び出し元へ
                raise
            wait = delays[attempt]
            attempt += 1
            print(f"[Yahoo Auctions] 一時エラー ({e}) → {wait}秒後に再試行 ({attempt}/{len(delays)})")
            time.sleep(wait)


def get_cheap_listings(
    keyword: str,
    max_price: int,
    min_price: int = 0,
    count: int = 40,
    exclude_words: list[str] | None = None,
    immediate_only: bool = False,
) -> list[Item]:
    """
    ヤフオクで keyword を安い順に検索し、仕入れ候補を返す。

    Args:
        min_price: 最低価格（¥1スタート問題対策。config の min_buy_price を渡す）
        exclude_words: 除外キーワードリスト（例: ["保存袋", "ショッパー"]）
        immediate_only: 現在未使用（aucminprice で同等効果を実現）
    """
    # 除外キーワードを検索クエリに付加（例: "グッチ バッグ -保存袋 -紙袋"）
    search_query = _build_query(keyword, exclude_words)

    params = {
        "p":           search_query,
        "va":          search_query,
        "b":           1,
        "n":           count,
        "s1":          "cbids",
        "o1":          "a",
        "mode":        1,
        "aucmaxprice": max_price,
    }
    # min_buy_price を aucminprice に渡して¥1スタート品を除外
    if min_price > 0:
        params["aucminprice"] = min_price

    try:
        resp = _get(_SEARCH_URL, params=params)
        return _parse_results(resp.text, max_price)[:count]
    except Exception as e:
        print(f"[Yahoo Auctions] 取得エラー ({keyword}): {e}")
        return []


def get_description(url: str) -> str | None:
    """商品詳細ページから説明文を取得する（タイトルに型番がない場合のフォールバック用）。"""
    try:
        resp = _get(url)
        soup = BeautifulSoup(resp.text, "lxml")
        el = soup.select_one("#description")
        return el.get_text(strip=True) if el else None
    except Exception as e:
        print(f"[Yahoo Auctions] 説明文取得エラー ({url}): {e}")
        return None


# 終了済みオークションの商品詳細ページに表示されるバナー文言。
# 実際に終了済み・出品中のページを取得して確認済み（2026-07-07時点）:
#   - 終了済み: 本文中に "このオークションは終了しています" が含まれ、
#     ページ内JSONにも "isClosed":"1" が含まれる
#   - 出品中:   上記いずれも含まれず "isClosed":"0"
# テキストの方が構造変化に強いためこちらを一次シグナルとして採用する。
_ENDED_TEXT = "このオークションは終了しています"


def is_ended(url: str) -> bool:
    """ヤフオクの商品ページを取得し、オークションが終了しているか判定する。取得失敗時は False（表示は継続）を返す。"""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        return _ENDED_TEXT in resp.text
    except Exception as e:
        print(f"[Yahoo Auctions] 終了判定エラー ({url}): {e}")
        return False


def _build_query(keyword: str, exclude_words: list[str] | None) -> str:
    """除外キーワードを付加した検索クエリを生成する。"""
    if not exclude_words:
        return keyword
    exclusions = " ".join(f"-{w}" for w in exclude_words)
    return f"{keyword} {exclusions}"


def _parse_results(html: str, max_price: int) -> list[Item]:
    soup = BeautifulSoup(html, "lxml")
    items = []
    for li in soup.select("li.Product"):
        item = _parse_product(li, max_price)
        if item:
            items.append(item)
    return items


def _parse_product(li, max_price: int):
    a = li.select_one("a[data-auction-title]")
    if not a:
        return None

    title = a.get("data-auction-title", "").strip()
    if not title:
        return None

    url = a.get("href", "")
    if not url:
        return None

    # Product__bonus div に即決価格 (data-auction-buynowprice) がある場合は優先使用
    bonus = li.select_one(".Product__bonus, [data-auction-buynowprice]")
    buynow_attr = bonus.get("data-auction-buynowprice", "") if bonus else ""
    price_attr  = a.get("data-auction-price", "")

    if buynow_attr.isdigit() and int(buynow_attr) > 0:
        # 即決価格が設定されている場合はそちらを使用
        price = int(buynow_attr)
    elif price_attr.isdigit() and int(price_attr) > 0:
        # 現在の入札価格を使用（aucminprice フィルターにより¥1品は除外済み）
        price = int(price_attr)
    else:
        price = None

    if price is None:
        price_el = li.select_one("[class*='price'], [class*='Price']")
        if price_el:
            price = _parse_price_text(price_el.get_text())

    if price is None or price <= 0 or price > max_price:
        return None

    image_url = a.get("data-auction-img") or None

    # 終了時刻: 検索結果の .Product__bonus に data-auction-endtime（UNIX秒）が入っている
    # （2026-10 時点で確認。表示上の「残り 5日」等は .Product__time）
    end_time = None
    endtime_attr = bonus.get("data-auction-endtime", "") if bonus else ""
    if endtime_attr.isdigit():
        end_time = int(endtime_attr)
    # 即決価格あり（定額・即決）やフリマ出品は「オークション形式」ではない扱い
    is_flea = bool(a.get("data-auction-isflea"))
    has_buynow = buynow_attr.isdigit() and int(buynow_attr) > 0
    is_auction = not has_buynow and not is_flea

    return Item(
        title=title,
        price=price,
        url=url,
        source="yahoo_auctions",
        image_url=image_url,
        end_time=end_time,
        is_auction=is_auction,
    )


def _parse_price_text(text: str):
    nums = re.sub(r"[^\d]", "", text)
    return int(nums) if nums else None

"""
Yahoo!フリマ（旧PayPayフリマ, paypayfleamarket.yahoo.co.jp）スクレイパー

- 相場算出には使えない設計に統一（?sold=1 で売却済みデータも取得可能だが、
  今回は既存設計に合わせ仕入れ候補ソースとしてのみ実装し、相場算出への活用はスコープ外）
- ページ内の __NEXT_DATA__ (Next.js の初期stateを含むJSON) から検索結果を抽出する
"""

import json
import re
import urllib.parse

import requests

from models import Item

_SEARCH_URL_BASE = "https://paypayfleamarket.yahoo.co.jp/search/"

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

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S
)


def get_cheap_listings(
    keyword: str,
    max_price: int,
    min_price: int = 0,
    count: int = 40,
    exclude_words: list[str] | None = None,
    immediate_only: bool = False,
) -> list[Item]:
    """
    Yahoo!フリマ（旧PayPayフリマ）で keyword を検索し、仕入れ候補を返す。

    Args:
        min_price: 最低価格（0 の場合は下限なし）
        exclude_words: 除外キーワードリスト（タイトル部分一致でクライアント側フィルタ）
        immediate_only: 全品固定価格のため未使用（受け取るだけで無視する）
        count: 1リクエストで取得した候補（通常は最大100件程度）から先頭 count 件に切り詰める
    """
    ex_lower = [w.lower() for w in exclude_words] if exclude_words else []

    try:
        url = _SEARCH_URL_BASE + urllib.parse.quote(keyword, safe="")
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()

        raw_items = _extract_items(resp.text)
        if raw_items is None:
            return []

        items: list[Item] = []
        for raw in raw_items:
            item = _parse_item(raw, min_price, max_price, ex_lower)
            if item:
                items.append(item)

        return items[:count]
    except Exception as e:
        print(f"[Yahoo!フリマ] 取得エラー ({keyword}): {e}")
        return []


def _extract_items(html: str):
    """__NEXT_DATA__ の JSON から検索結果アイテム一覧を取り出す。構造が想定外なら None を返す。"""
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
        return (
            data.get("props", {})
            .get("initialState", {})
            .get("searchState", {})
            .get("search", {})
            .get("result", {})
            .get("items", [])
        )
    except Exception:
        return None


def _parse_item(raw: dict, min_price: int, max_price: int, ex_lower: list[str]):
    if raw.get("itemStatus") != "OPEN":
        return None

    title = raw.get("title")
    if not title:
        return None

    price = raw.get("price")
    if not isinstance(price, int) or price <= 0:
        return None
    if price > max_price:
        return None
    if min_price > 0 and price < min_price:
        return None

    if ex_lower and any(w in title.lower() for w in ex_lower):
        return None

    item_id = raw.get("id")
    if not item_id:
        return None
    url = f"https://paypayfleamarket.yahoo.co.jp/item/{item_id}"

    return Item(
        title=title,
        price=price,
        url=url,
        source="yahoo_flea",
        image_url=raw.get("thumbnailImageUrl"),
    )

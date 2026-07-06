"""
ラクマ (fril.jp) スクレイパー

- 相場算出には使えない（売却済みデータの取得手段がないため、仕入れ候補ソースとしてのみ機能）
- ページネーションパラメータは未確認のため &page=2 を試行し、
  前ページと重複（新規URLが0件）なら打ち切る設計にしている
"""

import time

import requests
from bs4 import BeautifulSoup

from models import Item

_SEARCH_URL = "https://fril.jp/s"
_MAX_PAGES = 3

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


def get_cheap_listings(
    keyword: str,
    max_price: int,
    min_price: int = 0,
    count: int = 40,
    exclude_words: list[str] | None = None,
    immediate_only: bool = False,
) -> list[Item]:
    """
    ラクマで keyword を検索し、仕入れ候補を返す。

    Args:
        min_price: 最低価格（0 の場合は下限なし）
        exclude_words: 除外キーワードリスト（タイトル部分一致でクライアント側フィルタ）
        immediate_only: ラクマは全品固定価格のため未使用（受け取るだけで無視する）
    """
    ex_lower = [w.lower() for w in exclude_words] if exclude_words else []

    try:
        items: list[Item] = []
        seen_urls: set[str] = set()

        for page in range(1, _MAX_PAGES + 1):
            params = {"query": keyword}
            if page > 1:
                params["page"] = page
            resp = requests.get(_SEARCH_URL, params=params, headers=_HEADERS, timeout=15)
            resp.raise_for_status()

            page_items = _parse_page(resp.text, min_price, max_price, ex_lower)

            new_items = [it for it in page_items if it.url not in seen_urls]
            if not new_items:
                # ページネーション未対応（前ページと同一内容）または該当なし → 打ち切り
                break

            for it in new_items:
                seen_urls.add(it.url)
            items.extend(new_items)

            if len(items) >= count:
                break
            if page < _MAX_PAGES:
                time.sleep(1)

        return items[:count]
    except Exception as e:
        print(f"[ラクマ] 取得エラー ({keyword}): {e}")
        return []


def _parse_page(
    html: str, min_price: int, max_price: int, ex_lower: list[str]
) -> list[Item]:
    soup = BeautifulSoup(html, "lxml")
    items = []
    for div in soup.select("div.item"):
        item = _parse_card(div, min_price, max_price, ex_lower)
        if item:
            items.append(item)
    return items


def _parse_card(div, min_price: int, max_price: int, ex_lower: list[str]):
    a = div.select_one("a.link_search_title")
    if not a:
        return None
    title = a.get_text(strip=True)
    if not title:
        return None
    url = a.get("href", "")
    if not url:
        return None

    price_box = div.select_one("p.item-box__item-price")
    if not price_box:
        return None
    spans = price_box.select("span[data-content]")
    if len(spans) < 2:
        return None
    raw_price = spans[1].get("data-content", "")
    if not raw_price.isdigit():
        return None
    price = int(raw_price)

    if price > max_price:
        return None
    if min_price > 0 and price < min_price:
        return None

    if ex_lower and any(w in title.lower() for w in ex_lower):
        return None

    img = div.select_one("img")
    image_url = img.get("data-original") if img else None
    if not image_url:
        noscript_img = div.select_one("noscript img")
        image_url = noscript_img.get("src") if noscript_img else None

    return Item(
        title=title,
        price=price,
        url=url,
        source="rakuma",
        image_url=image_url,
    )

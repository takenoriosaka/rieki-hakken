"""
トレファクファッション (trefac.jp/store/tcpsb) スクレイパー

- 相場算出には使えない（売却済みデータの取得手段がないため、仕入れ候補ソースとしてのみ機能）
- 売り切れカード（span.p-itemlist_soldout あり）は価格が取れないためスキップする
"""

import re
import time

import requests
from bs4 import BeautifulSoup

from models import Item

_SEARCH_URL = "https://www.trefac.jp/store/tcpsb/"
_MAX_PAGES = 3  # 1ページあたり約90件取得できるため、通常は1ページで count に達する

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
    トレファクファッションで keyword を検索し、仕入れ候補を返す。

    Args:
        min_price: 最低価格（0 の場合は下限なし）
        exclude_words: 除外キーワードリスト（タイトル部分一致でクライアント側フィルタ）
        immediate_only: トレファクは全品固定価格のため未使用（受け取るだけで無視する）
    """
    ex_lower = [w.lower() for w in exclude_words] if exclude_words else []

    try:
        items: list[Item] = []
        for page in range(1, _MAX_PAGES + 1):
            params = {"srchword": keyword}
            if page > 1:
                params["key"] = page
            resp = requests.get(_SEARCH_URL, params=params, headers=_HEADERS, timeout=15)
            resp.raise_for_status()

            page_items = _parse_page(resp.text, min_price, max_price, ex_lower)
            if not page_items:
                break
            items.extend(page_items)

            if len(items) >= count:
                break
            if page < _MAX_PAGES:
                time.sleep(1)  # 丁寧なアクセス間隔（robots.txt上はSemrushBot限定の制限のみ）

        return items[:count]
    except Exception as e:
        print(f"[トレファク] 取得エラー ({keyword}): {e}")
        return []


def _parse_page(
    html: str, min_price: int, max_price: int, ex_lower: list[str]
) -> list[Item]:
    soup = BeautifulSoup(html, "lxml")
    items = []
    for li in soup.select("li.p-itemlist_item"):
        item = _parse_card(li, min_price, max_price, ex_lower)
        if item:
            items.append(item)
    return items


def _parse_card(li, min_price: int, max_price: int, ex_lower: list[str]):
    # 売り切れカードは価格が存在しないためスキップ
    if li.select_one("span.p-itemlist_soldout"):
        return None

    img = li.select_one("img[alt]")
    if not img:
        return None
    title = img.get("alt", "").strip()
    if not title:
        return None

    a = li.select_one("a.p-itemlist_btn[href]")
    if not a:
        return None
    url = a.get("href", "")
    if not url:
        return None

    price_el = li.select_one("p.p-price2_a") or li.select_one("p.p-price2_b")
    price = _parse_price_text(price_el.get_text()) if price_el else None
    if price is None:
        return None

    if price > max_price:
        return None
    if min_price > 0 and price < min_price:
        return None

    if ex_lower and any(w in title.lower() for w in ex_lower):
        return None

    image_url = img.get("src") or None

    return Item(
        title=title,
        price=price,
        url=url,
        source="trefac",
        image_url=image_url,
    )


def _parse_price_text(text: str):
    nums = re.sub(r"[^\d]", "", text)
    return int(nums) if nums else None

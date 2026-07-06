"""
ベクトルパーク (vector-park.jp) スクレイパー

- 相場算出には使えない（売却済みデータの取得手段がないため、仕入れ候補ソースとしてのみ機能）
- robots.txt に Crawl-delay: 10 の指定があるため、複数ページ取得時は 10 秒間隔を空ける
"""

import re
import time
import warnings

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from models import Item

# vector-park.jp のレスポンスが lxml に XML と誤認識されることがあるため警告を抑制
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

_LIST_URL = "https://vector-park.jp/list/"
_MAX_PAGES = 3  # Crawl-delay: 10 を踏まえ、1キーワードあたりの最大待ち時間を約20〜30秒に抑える

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
    ベクトルパークで keyword を検索し、仕入れ候補を返す。

    Args:
        min_price: 最低価格（0 の場合は下限なし）
        exclude_words: 除外キーワードリスト（タイトル部分一致でクライアント側フィルタ）
        immediate_only: ベクトルパークは全品固定価格のため未使用（受け取るだけで無視する）
    """
    ex_lower = [w.lower() for w in exclude_words] if exclude_words else []

    try:
        items: list[Item] = []
        for page in range(1, _MAX_PAGES + 1):
            params = {"kw": keyword, "p": page}
            resp = requests.get(_LIST_URL, params=params, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            # Content-Type ヘッダーに charset が無くrequestsがISO-8859-1と誤判定するため明示的にUTF-8指定
            resp.encoding = "utf-8"

            page_items = _parse_page(resp.text, min_price, max_price, ex_lower)
            if not page_items:
                # 0件（該当なし or 最終ページ）ならページネーションを打ち切る
                break
            items.extend(page_items)

            if len(items) >= count:
                break
            if page < _MAX_PAGES:
                time.sleep(10)  # robots.txt の Crawl-delay: 10 を厳守

        return items[:count]
    except Exception as e:
        print(f"[ベクトルパーク] 取得エラー ({keyword}): {e}")
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
    img = div.select_one("img[alt]")
    if not img:
        return None
    title = img.get("alt", "").strip()
    if not title:
        return None

    a = div.select_one("a[href]")
    if not a:
        return None
    href = a.get("href", "")
    if not href:
        return None
    url = href if href.startswith("http") else f"https://vector-park.jp{href}"

    price_el = div.select_one("div.item_pr p")
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
        source="vector_park",
        image_url=image_url,
    )


def _parse_price_text(text: str):
    nums = re.sub(r"[^\d]", "", text)
    return int(nums) if nums else None

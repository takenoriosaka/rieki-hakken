"""
Yahoo!ショッピング（shopping.yahoo.co.jp）スクレイパー

- 仕入れ候補ソース専用（相場はこれまでどおりメルカリの売り切れで出す）
- ログインなし・Cookie なしの普通の GET だけで取る（他の仕入れ先と同じ User-Agent / Accept-Language）
- robots.txt（2026-11-06 に確認）: 検索結果ページ /search/<検索語>/0/ と商品ページ store.shopping.yahoo.co.jp/<店>/<商品>.html は
  禁止されていない。価格の範囲（pf / pt）・件数（n）・表示形式（view）・送料の条件（ship 等）の各パラメータと
  /searchbff/*/search/postageInfo は禁止されているので使わない。使うのは 商品の状態（used）と並び順（X）とページ番号（パス）だけ。
  価格の範囲は取得後にこちらで絞る（安い順に並べて、上限を超えたら打ち切る）
- 検索結果・商品ページとも Next.js の __NEXT_DATA__（JSON）から読む
  検索: props.initialState.bff.searchResults.items["<ページ番号>"][type=RESULT].content.items
  商品: props.pageProps.item / props.pageProps.postage
- 送料: 検索結果には「送料無料か」（freeCondition == "FREE"）しか無い。送料無料でない出品は、商品ページの
  postage.fee（配送先＝東京都の送料。ログインしていないときの既定）を fill_postage() で足す。
  取れなかったときは設定 yahoo_shopping_default_postage（既定 1,000円）を仮に足す
- PayPay ポイント・クーポン・ストアの値引き後価格（actualPrice）は数えない（表示価格 price をそのまま使う）
- 中古の状態: 検索結果の labels.hasUsed20/40/60/80 = 商品ページの usedCondition
  （20=目立った傷や汚れなし / 40=やや傷や汚れあり / 60=傷や汚れあり / 80=全体的に状態が悪い。2026-11-06 に商品ページと突き合わせて確認。
   0・10 は未確認のため 未使用・未使用に近い とみなす）。
  タイトルの状態（condition_checker）より悪ければ、その状態の言葉をタイトルの末尾に足して相場の計算に効かせる
"""

import json
import re
import time
import urllib.parse

import requests

import condition_checker
from models import Item

SOURCE = "yahoo_shopping"

_SEARCH_URL = "https://shopping.yahoo.co.jp/search/{q}/0/{page}"
_ITEM_URL_RE = re.compile(r'^https://store\.shopping\.yahoo\.co\.jp/[^/?#]+/[^/?#]+\.html')
_IMAGE_URL = "https://item-shopping.c.yimg.jp/i/l/{}"   # l = 長辺 600px（このサイトで取れる最大）

# 商品の状態（検索の used パラメータ）: 中古のみ=1 / 新品のみ=2 / 指定なし=なし
_USED_PARAM = {"used": "1", "new": "2", "all": None}
# 並び順: 価格が安い順（X=2）。価格の範囲のパラメータは robots.txt で禁止されているため、
# 安い順に読んで上限を超えたところで打ち切る
_SORT_PRICE_ASC = "2"
_MAX_PAGES = 10            # 1ページ30件。最低価格より安い出品が多いとき用に多めに読む
_PER_PAGE = 30

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

_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

# 中古ランク（検索結果の labels.hasUsedNN）→ 状態の言葉（condition_checker が読める表記）
_USED_GRADES = [
    ("hasUsed0",  "未使用品"),
    ("hasUsed10", "未使用に近い"),
    ("hasUsed20", "目立った傷や汚れなし"),
    ("hasUsed40", "やや傷や汚れあり"),
    ("hasUsed60", "傷や汚れあり"),
    ("hasUsed80", "全体的に状態が悪い（難あり）"),
]
# 商品ページの usedCondition → 同じ言葉（fill_postage で商品ページを読んだときの確認用）
_USED_CONDITIONS = {
    "Excellent": "目立った傷や汚れなし",
    "VeryGood":  "やや傷や汚れあり",
    "Good":      "傷や汚れあり",
    "Normal":    "全体的に状態が悪い（難あり）",
}

# 商品ページを読んだときの控え（商品URL → 説明文 / 写真URL一覧）。get_description / get_image_urls で再取得しない
_descriptions: dict[str, str] = {}
_image_urls: dict[str, list[str]] = {}

DEFAULT_POSTAGE = 1000


def get_cheap_listings(
    keyword: str,
    max_price: int,
    min_price: int = 0,
    count: int = 40,
    exclude_words: list[str] | None = None,
    immediate_only: bool = False,
    condition: str = "used",
) -> list[Item]:
    """Yahoo!ショッピングで keyword を検索し、仕入れ候補を返す（価格の安い順）。

    condition: "used"（中古のみ・既定）/ "new"（新品のみ）/ "all"（両方）
    価格は表示価格（税込）。送料無料の出品は shipping=0、それ以外は shipping=None（fill_postage で足す）。
    immediate_only: 全品定額のため未使用
    """
    ex_lower = [w.lower() for w in exclude_words] if exclude_words else []
    params = {"X": _SORT_PRICE_ASC}
    used = _USED_PARAM.get(condition, "1")
    if used:
        params["used"] = used
    q = urllib.parse.quote_plus(keyword)
    items: list[Item] = []
    seen: set[str] = set()
    try:
        for page in range(1, _MAX_PAGES + 1):
            url = _SEARCH_URL.format(q=q, page="" if page == 1 else f"{page}/")
            if page == 1:
                url = url.rstrip("/") + "/"
            resp = requests.get(url, headers=_HEADERS, params=params, timeout=20)
            resp.raise_for_status()
            raw_items, total_pages = _extract_items(resp.text, page)
            if raw_items is None:
                print(f"[Yahoo!ショッピング] 検索結果の形式が想定外です ({keyword})")
                break
            over_max = False
            for raw in raw_items:
                price = raw.get("price")
                if isinstance(price, int) and price > max_price:
                    over_max = True   # 安い順なので、以降は上限超え
                    break
                item = _parse_item(raw, min_price, max_price, ex_lower)
                if item and item.url not in seen:
                    seen.add(item.url)
                    items.append(item)
            if over_max or len(items) >= count or not raw_items or page >= (total_pages or 1):
                break
            time.sleep(1)   # ページの間は1秒空ける（他の仕入れ先と同じ）
        return items[:count]
    except Exception as e:
        print(f"[Yahoo!ショッピング] 取得エラー ({keyword}): {e}")
        return items[:count]


def _extract_items(html: str, page: int = 1):
    """検索結果ページの __NEXT_DATA__ から (商品の一覧, 総ページ数) を返す。形式が想定外なら (None, 0)。
    検索結果は searchResults.items のページ番号のキー（"1", "2", …）の下にある"""
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None, 0
    try:
        bff = json.loads(m.group(1))["props"]["initialState"]["bff"]
        blocks = (bff.get("searchResults") or {}).get("items") or {}
        out = []
        for block in blocks.get(str(page)) or []:
            if block.get("type") == "RESULT":
                out.extend((block.get("content") or {}).get("items") or [])
        total_pages = (bff.get("alternativePagination") or {}).get("totalPage") or 0
        return out, total_pages
    except Exception:
        return None, 0


def _clean_url(url: str) -> str:
    m = _ITEM_URL_RE.match(url or "")
    return m.group(0) if m else ""


def _grade_text(labels: dict) -> str | None:
    for key, text in _USED_GRADES:
        if labels.get(key):
            return text
    return None


def _with_condition(title: str, grade: str | None) -> str:
    """店の中古ランク（grade）がタイトルの状態より悪い・タイトルに状態が無いときは、タイトルの末尾に足す
    （相場の計算 condition_checker.check(title) に効かせるため。良いほうに上書きはしない）"""
    if not grade:
        return title
    t = condition_checker.check(title)
    g = condition_checker.check(grade)
    if condition_checker.is_unknown(t) or g.factor < t.factor:
        return f"{title} 【{grade}】"
    return title


def _parse_item(raw: dict, min_price: int, max_price: int, ex_lower: list[str]) -> Item | None:
    title = raw.get("name")
    price = raw.get("price")
    if not title or not isinstance(price, int) or price <= 0:
        return None
    if price > max_price or (min_price > 0 and price < min_price):
        return None
    labels = raw.get("labels") or {}
    # 在庫なし・予約・取り寄せ・受注生産は仕入れ候補にしない（すぐ買えない）
    if labels.get("hasNoStock") or labels.get("hasReservation") or labels.get("hasPreRelease") \
            or labels.get("hasBackOrder") or labels.get("hasMadeToOrder") or labels.get("hasOrder"):
        return None
    if ex_lower and any(w in title.lower() for w in ex_lower):
        return None
    url = _clean_url(raw.get("url") or "")
    if not url:
        return None
    if labels.get("hasUsed"):
        title = _with_condition(title, _grade_text(labels))
    elif condition_checker.is_unknown(condition_checker.check(title)):
        # 中古の印が無い＝ストアの新品。タイトルに状態が書かれていなければ新品として扱う
        title = f"{title} 【新品】"
    free = raw.get("freeCondition") == "FREE"
    image = ((raw.get("image") or {}).get("imageUrl") or "").split("?")[0] or None
    return Item(
        title=title,
        price=price,
        url=url,
        source=SOURCE,
        image_url=image,
        shipping=0 if free else None,
        base_price=price,
    )


# ──────────────────────────────────────────────────────────────────────────────
# 商品ページ（送料・説明文・写真・在庫）
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_item_page(url: str) -> dict | None:
    """商品ページの __NEXT_DATA__ の props.pageProps を返す。404 は {"_not_found": True}。取得エラーは None"""
    resp = requests.get(url, headers=_HEADERS, timeout=20)
    if resp.status_code == 404:
        return {"_not_found": True}
    resp.raise_for_status()
    m = _NEXT_DATA_RE.search(resp.text)
    if not m:
        return None
    return (json.loads(m.group(1)).get("props") or {}).get("pageProps")


def _remember_page(url: str, pp: dict) -> None:
    item = pp.get("item") or {}
    desc = _description_of(item)
    if desc is not None:
        _descriptions[url] = desc
    imgs = _images_of(item)
    if imgs:
        _image_urls[url] = imgs


def _description_of(item: dict) -> str | None:
    """商品説明（caption・information・freeSpace1 の HTML をテキストにしてつなげる）"""
    parts = []
    for key in ("information", "caption", "freeSpace1", "freeSpace2", "freeSpace3", "catchCopy"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(_html_text(v))
    if not parts and "caption" not in item:
        return None
    seen, out = set(), []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return "\n".join(out)


def _html_text(html: str) -> str:
    s = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li|tr)>", "\n", html)
    s = re.sub(r"<[^>]+>", " ", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
         .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    s = re.sub(r"[ \t　]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def _images_of(item: dict) -> list[str] | None:
    imgs = (item.get("images") or {})
    lst = imgs.get("displayItemImageList") or imgs.get("itemImageList") or []
    ids = [d.get("id") for d in lst if isinstance(d, dict) and d.get("id")]
    main = (imgs.get("mainImage") or {}).get("id")
    if main and main not in ids:
        ids.insert(0, main)
    urls = [_IMAGE_URL.format(i) for i in dict.fromkeys(ids)]
    return urls or None


def get_item_info(url: str) -> dict | None:
    """商品ページから {available, postage, postage_free, used_condition} を返す（説明文・写真も控える）。
    取得エラー・形式が想定外なら None"""
    try:
        pp = _fetch_item_page(url)
    except Exception as e:
        print(f"[Yahoo!ショッピング] 商品ページ取得エラー ({url}): {e}")
        return None
    if pp is None:
        return None
    if pp.get("_not_found"):
        return {"available": False, "postage": None, "postage_free": False, "used_condition": None}
    item = pp.get("item") or {}
    if not item:
        return None
    _remember_page(url, pp)
    stock = item.get("stock") or {}
    seller = pp.get("seller") or {}
    available = bool(item.get("isOnSale", True)) and stock.get("isAvailable") is not False \
        and not seller.get("isClose") and not seller.get("isSuspend")
    postage = pp.get("postage") or {}
    fee = postage.get("fee")
    return {
        "available": available,
        "postage": fee if isinstance(fee, int) and fee >= 0 else None,
        "postage_free": bool(postage.get("isPostageFree")),
        "used_condition": _USED_CONDITIONS.get(item.get("usedCondition") or ""),
    }


def fill_postage(items: list[Item], default_postage: int = DEFAULT_POSTAGE,
                 interval: float = 1.0, should_stop=None) -> tuple[list[Item], dict]:
    """送料無料でない出品（shipping=None）の商品ページを読み、送料（東京都あて）を価格に足す。
    在庫なし・販売終了・削除済みの出品は除く。送料が取れなければ default_postage を仮に足す（postage_estimated=True）。
    戻り値: (残った出品, 統計 {fetched, sold_out, estimated})"""
    stats = {"fetched": 0, "sold_out": 0, "estimated": 0}
    out: list[Item] = []
    last = 0.0
    for it in items:
        if it.source != SOURCE or it.shipping is not None:
            out.append(it)
            continue
        if should_stop and should_stop():
            # 中断時は仮の送料で残す（あとの処理で中断される）
            it.shipping, it.postage_estimated = default_postage, True
            it.price = (it.base_price or it.price) + default_postage
            out.append(it)
            continue
        wait = last + interval - time.time()
        if wait > 0:
            time.sleep(wait)
        info = get_item_info(it.url)
        last = time.time()
        stats["fetched"] += 1
        if info is not None and not info["available"]:
            stats["sold_out"] += 1
            continue
        if info is not None and (info["postage_free"] or info["postage"] is not None):
            fee = 0 if info["postage_free"] else info["postage"]
            it.shipping = fee
        else:
            it.shipping, it.postage_estimated = default_postage, True
            stats["estimated"] += 1
        it.price = (it.base_price or it.price) + it.shipping
        if info is not None and info.get("used_condition"):
            it.title = _with_condition(it.title, info["used_condition"])
        out.append(it)
    return out, stats


def get_description(url: str) -> str | None:
    """商品説明文（タイトルに型番が無いときの照合用）。説明文なしなら ''、取得エラーなら None"""
    if url in _descriptions:
        return _descriptions.pop(url)
    try:
        pp = _fetch_item_page(url)
        if not pp or pp.get("_not_found"):
            return None if not pp else ""
        item = pp.get("item") or {}
        imgs = _images_of(item)
        if imgs:
            _image_urls[url] = imgs
        desc = _description_of(item)
        return desc if desc is not None else None
    except Exception as e:
        print(f"[Yahoo!ショッピング] 説明文取得エラー ({url}): {e}")
        return None


def get_image_urls(url: str) -> list[str] | None:
    """商品写真のURL一覧（掲載順・長辺600px）。取得エラーなら None"""
    if url in _image_urls:
        return _image_urls.pop(url)
    try:
        pp = _fetch_item_page(url)
        if not pp or pp.get("_not_found"):
            return None
        return _images_of(pp.get("item") or {})
    except Exception as e:
        print(f"[Yahoo!ショッピング] 写真URL取得エラー ({url}): {e}")
        return None


def is_ended(url: str) -> bool:
    """もう買えない（在庫なし・販売終了・削除済み）か。取得エラーは False（表示は継続）"""
    info = get_item_info(url)
    if info is None:
        return False
    _descriptions.pop(url, None)
    _image_urls.pop(url, None)
    return not info["available"]

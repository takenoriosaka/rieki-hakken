"""
Mercari scraper:
  - get_sold_prices()  : 売却済み商品の価格一覧（相場算出用）
  - get_cheap_listings(): 販売中の格安出品（仕入れ候補）

Playwright を使って JavaScript レンダリング済みページを取得します。
"""

import re
import time
import random
from typing import Optional

from models import Item
import model_extractor

# スキャン全体で使い回すブラウザコンテキストの共通設定
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def new_page(browser):
    """呼び出し元（main.py）が起動した Playwright ブラウザから、
    スキャン全体で使い回すページを1つ生成する。
    """
    ctx = browser.new_context(locale="ja-JP", user_agent=_USER_AGENT)
    return ctx.new_page()


def get_sold_prices(
    page,
    keyword: str,
    count: int = 30,
    exclude_words: list[str] | None = None,
    required_words: list[str] | None = None,
    price_min: int | None = None,
    price_max: int | None = None,
    title_filter=None,
) -> list[int]:
    """メルカリの売却済み価格リストを返す。price_min/price_max で価格帯を絞り込める。
    required_words はカテゴリーをまたいで同じモデル名・型番が使われる場合の
    クロスコンタミネーション防止用（例: カルティエ「トリニティ」=指輪/サングラス両方に存在）。
    page: 呼び出し元で起動・使い回している Playwright ページ（毎回ブラウザ起動しないため）。
    title_filter: タイトル(元の表記)を受け取り True/False を返す追加条件
    （例: デュベティカ「ディオニシオ」の相場に「ディオニシオドゥエ」を混ぜない）。
    """
    search_keyword = _build_keyword(keyword, exclude_words)
    try:
        return _playwright_sold_prices(
            page, search_keyword, count, exclude_words, required_words, price_min, price_max,
            title_filter=title_filter,
        )
    except Exception as e:
        print(f"[Mercari] 売却済み価格取得エラー ({keyword}): {e}")
        return []


def get_descriptions(page, urls: list[str]) -> dict[str, str]:
    """複数の商品詳細ページから説明文を取得する（タイトルに型番がない場合のフォールバック用）。
    戻り値は {url: 説明文}。説明文が無い商品は ''、ページ取得エラーの URL は含まない。
    page: 呼び出し元で起動・使い回している Playwright ページ。
    """
    if not urls:
        return {}
    try:
        return _playwright_descriptions(page, urls)
    except Exception as e:
        print(f"[Mercari] 説明文取得エラー: {e}")
        return {}


def _playwright_descriptions(page, urls: list[str]) -> dict[str, str]:
    results: dict[str, str] = {}
    for url in urls:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            # 説明文（data-testid="description"）または「商品の説明」見出しが
            # ハイドレーションで描画されるまで待機。全要素を走査する見出し判定は
            # 重いため、まず data-testid を軽い間隔で確認する
            try:
                page.wait_for_function(
                    _HAS_DESCRIPTION_JS, timeout=8000, polling=200
                )
            except Exception:
                pass  # 見出しが無い商品もある（説明文なし）
            desc = page.evaluate(_EXTRACT_DESCRIPTION_TESTID_JS) or page.evaluate(_EXTRACT_DESCRIPTION_JS)
            # 説明文なしは ''（取得エラーの URL は結果に含めない＝呼び出し側で区別できる）
            results[url] = desc or ""
        except Exception as e:
            print(f"[Mercari] 説明文取得エラー ({url}): {e}")
    return results


_HAS_DESCRIPTION_JS = """
() => {
    const d = document.querySelector('[data-testid="description"]');
    if (d && d.textContent.trim().length > 0) return true;
    return Array.from(document.querySelectorAll('*'))
        .some(el => el.children.length === 0 && el.textContent.trim() === '商品の説明');
}
"""

_EXTRACT_DESCRIPTION_TESTID_JS = """
() => {
    const d = document.querySelector('[data-testid="description"]');
    const t = d ? d.innerText.trim() : '';
    return t.length > 0 ? t : null;
}
"""

_EXTRACT_DESCRIPTION_JS = """
() => {
    const all = Array.from(document.querySelectorAll('*'));
    const heading = all.find(el => el.textContent.trim() === '商品の説明' && el.children.length === 0);
    if (!heading) return null;
    let node = heading.parentElement;
    for (let i = 0; i < 5 && node; i++) {
        const next = node.nextElementSibling;
        if (next && next.textContent.trim().length > 20) {
            return next.textContent.trim();
        }
        node = node.parentElement;
    }
    return null;
}
"""


def get_cheap_listings(
    page,
    keyword: str,
    max_price: int,
    count: int = 40,
    exclude_words: list[str] | None = None,
) -> list[Item]:
    """メルカリの格安出品中リストを返す（仕入れ候補）。除外ワードでタイトルフィルタリング。
    page: 呼び出し元で起動・使い回している Playwright ページ。
    """
    search_keyword = _build_keyword(keyword, exclude_words)
    try:
        return _playwright_cheap_listings(page, search_keyword, max_price, count, exclude_words)
    except Exception as e:
        print(f"[Mercari] 格安出品取得エラー ({keyword}): {e}")
        return []


# ──────────────────────────────────────────────────────────────────────────────
# Playwright implementations
# ──────────────────────────────────────────────────────────────────────────────

def _playwright_sold_prices(
    page, keyword: str, count: int, exclude_words: list[str] | None = None,
    required_words: list[str] | None = None,
    price_min: int | None = None, price_max: int | None = None,
    title_filter=None,
) -> list[int]:
    # item_types=1 : フリマ（個人C2C）のみ。メルカリショップス（item_types=2）を除外
    url = (
        f"https://jp.mercari.com/search"
        f"?keyword={keyword}&status=sold_out&sort=created_time&order=desc&item_types=1"
    )
    if price_min:
        url += f"&price_min={price_min}"
    if price_max:
        url += f"&price_max={price_max}"

    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    _wait_for_items(page)

    raw = page.evaluate(_EXTRACT_SOLD_JS)

    # タイトルに除外ワードが含まれるものを除去（Mercariは -word 構文非対応のため後処理）
    # required_words: 同名称が別カテゴリーにも存在する場合の混入防止（例: トリニティ=指輪/サングラス）
    # brand_token: keyword の先頭単語（ブランド名）。Mercariのあいまい検索は他ブランドの
    # 商品を紛れ込ませることがあるため（例: 「ブルガリ ジュエリー」検索にルイヴィトン商品が混入）、
    # タイトルにブランド名が含まれない候補は相場サンプルから除外する
    # （DUVETICA/デュベティカ、THE NORTH FACE/ノースフェイス等の表記ゆれは
    #   model_extractor.BRAND_ALIASES で吸収する）
    prices = []
    ex_lower  = [w.lower() for w in exclude_words] if exclude_words else []
    req_lower = [w.lower() for w in required_words] if required_words else []
    kw_tokens = keyword.split()
    brand_token = kw_tokens[0].lower() if kw_tokens else ""
    for r in raw:
        price = r.get("price", 0)
        if price <= 0:
            continue
        raw_title = r.get("title", "")
        title = raw_title.lower()
        if ex_lower and any(w in title for w in ex_lower):
            continue
        if req_lower and not any(w in title for w in req_lower):
            continue
        if brand_token and not model_extractor.has_brand(title, kw_tokens[0]):
            continue
        if title_filter is not None and not title_filter(raw_title):
            continue
        prices.append(price)

    return prices[:count]


def _playwright_cheap_listings(
    page, keyword: str, max_price: int, count: int,
    exclude_words: list[str] | None = None,
) -> list[Item]:
    # item_types=1 : フリマ（個人C2C）のみ。メルカリショップスを除外
    url = (
        f"https://jp.mercari.com/search"
        f"?keyword={keyword}&status=on_sale&sort=price&order=asc"
        f"&price_max={max_price}&item_types=1"
    )

    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    _wait_for_items(page)

    raw = page.evaluate(_EXTRACT_LISTINGS_JS)

    ex_lower = [w.lower() for w in exclude_words] if exclude_words else []
    items = []
    for r in raw:
        if len(items) >= count:
            break
        price = _parse_price(r.get("price", ""))
        if not price or price > max_price:
            continue
        title = r.get("title", "").strip()
        # タイトルに除外ワードが含まれる場合はスキップ
        if ex_lower and any(w in title.lower() for w in ex_lower):
            continue
        items.append(Item(
            title=title,
            price=price,
            url=r.get("url", ""),
            source="mercari_cheap",
            image_url=r.get("image"),
        ))
    return items


# 検索結果の「中身入りセル」（価格が描画済みのセル）の数
_FILLED_COUNT_JS = """
() => document.querySelectorAll('[data-testid="item-cell"] [data-testid="item-tile-price"]').length
"""

# 検索結果が描画された、または「0件」表示が出たら true
# （0件のときに item-cell を15秒待ち続けないため。0件の型番は1スキャンで約200回ある）
_RESULTS_OR_EMPTY_JS = """
() => {
    if (document.querySelector('[data-testid="item-cell"]')) return true;
    const t = document.body ? document.body.innerText : '';
    return t.includes('出品された商品がありません') || t.includes('該当する商品が見つかりません');
}
"""


def _wait_for_items(page, timeout: int = 15000):
    try:
        page.wait_for_function(_RESULTS_OR_EMPTY_JS, timeout=timeout, polling=250)
    except Exception:
        # 旧来の待ち方（構造変化で上の判定が効かなくなった場合の保険）
        try:
            page.wait_for_selector(
                'li[class*="item"], .item-cell', timeout=2000,
            )
        except Exception:
            pass
    if not page.query_selector('[data-testid="item-cell"], li[class*="item"], .item-cell'):
        return  # 0件。スクロールしても何も出ない
    # メルカリの検索結果は仮想スクロールで、初期表示では中身入りのセルが
    # 約10件しか描画されない。少しずつスクロールすると約90件まで描画される。
    _scroll_to_render(page)


def _scroll_to_render(page, times: int = 4, wait_ms: int = 1000):
    """少しずつスクロールして仮想スクロールのセルを描画させる。
    各スクロール後は「中身入りセルが増えた時点」で次へ進み（固定待ちをしない）、
    wait_ms 待っても増えなければ打ち切る（件数が少ない検索で無駄に待たない）。
    最大スクロール回数は従来どおり times 回（取得件数は従来と同等）。
    """
    try:
        prev = page.evaluate(_FILLED_COUNT_JS)
    except Exception:
        return
    for _ in range(times):
        try:
            page.mouse.wheel(0, 1500)
            page.wait_for_function(
                f"() => ({_FILLED_COUNT_JS.strip()})() > {prev}",
                timeout=wait_ms, polling=100,
            )
            prev = page.evaluate(_FILLED_COUNT_JS)
        except Exception:
            break   # 増えなくなった（全件描画済み or 末尾）→ 打ち切り


def _parse_price(text: str) -> Optional[int]:
    nums = re.sub(r"[^\d]", "", text)
    return int(nums) if nums else None


# JavaScript を page.evaluate() で実行して DOM から値を抽出する
# 売却済みリスト用：タイトルと価格を両方取得（除外ワードフィルタのため）
_EXTRACT_SOLD_JS = """
() => {
    const items = [];
    const cellSelectors = [
        '[data-testid="item-cell"]',
        'li[class*="item"]',
        '.item-cell',
        '[class*="ItemCell"]',
    ];
    let cells = [];
    for (const sel of cellSelectors) {
        cells = Array.from(document.querySelectorAll(sel));
        if (cells.length > 0) break;
    }
    // セルが取れなかった場合は価格エレメント直接取得にフォールバック
    if (cells.length === 0) {
        const priceSelectors = [
            '[data-testid="item-cell"] [data-testid="item-tile-price"]',
            '[data-testid="item-cell"] [class*="price"]',
            'li[class*="item"] [class*="price"]',
            '.item-cell [class*="price"]',
            '[class*="itemPrice"]',
        ];
        for (const sel of priceSelectors) {
            document.querySelectorAll(sel).forEach(el => {
                const text = el.textContent.replace(/[^\\d]/g, '');
                const n = parseInt(text, 10);
                if (n > 0) items.push({ title: '', price: n });
            });
            if (items.length > 0) break;
        }
        return items;
    }
    cells.forEach(cell => {
        // メルカリショップスのバッジを検出して除外（URLパラメータに加えて2重チェック）
        const shopBadge = cell.querySelector(
            '[class*="shop" i], [class*="Shop"], [data-testid*="shop"], ' +
            '[aria-label*="ショップ"], [class*="merchant"], [class*="Merchant"]'
        );
        if (shopBadge) return;

        // 2026-09 以降クラス名がランダム化されたため data-testid を優先し、
        // 旧クラス指定は予備として残す（必ず item-cell の内側だけを探す）
        const titleEl = cell.querySelector('[data-testid="thumbnail-item-name"]') || cell.querySelector(
            '[class*="itemName"], [class*="name"], h3, [class*="title"]'
        );
        const priceEl = cell.querySelector('[data-testid="item-tile-price"]') || cell.querySelector(
            '[class*="price"], [class*="Price"], [class*="itemPrice"]'
        );
        if (priceEl) {
            const text = priceEl.textContent.replace(/[^\\d]/g, '');
            const n = parseInt(text, 10);
            if (n > 0) {
                items.push({
                    title: titleEl ? titleEl.textContent.trim() : '',
                    price: n,
                });
            }
        }
    });
    return items;
}
"""

def _build_keyword(keyword: str, exclude_words: list[str] | None) -> str:
    """メルカリ用の検索キーワードを組み立てる（除外ワードは括弧で囲む）。"""
    if not exclude_words:
        return keyword
    # メルカリは "-word" 形式には対応していないため、
    # 除外ワードを含む商品はタイトルフィルターで後処理する
    return keyword


_EXTRACT_LISTINGS_JS = """
() => {
    const items = [];
    const cellSelectors = [
        '[data-testid="item-cell"]',
        'li[class*="item"]',
        '.item-cell',
        '[class*="ItemCell"]',
    ];
    let cells = [];
    for (const sel of cellSelectors) {
        cells = Array.from(document.querySelectorAll(sel));
        if (cells.length > 0) break;
    }
    cells.forEach(cell => {
        // メルカリショップスのバッジを検出して除外
        const shopBadge = cell.querySelector(
            '[class*="shop" i], [class*="Shop"], [data-testid*="shop"], ' +
            '[aria-label*="ショップ"], [class*="merchant"], [class*="Merchant"]'
        );
        if (shopBadge) return;

        const a = cell.querySelector('a[data-testid="thumbnail-link"]') || cell.querySelector('a');
        // 2026-09 以降クラス名がランダム化されたため data-testid を優先し、
        // 旧クラス指定は予備として残す（必ず item-cell の内側だけを探す）
        const titleEl = cell.querySelector('[data-testid="thumbnail-item-name"]') || cell.querySelector(
            '[class*="itemName"], [class*="name"], h3, [class*="title"]'
        );
        const priceEl = cell.querySelector('[data-testid="item-tile-price"]') || cell.querySelector(
            '[class*="price"], [class*="Price"]'
        );
        const imgEl = cell.querySelector('img');
        if (a && titleEl && priceEl) {
            items.push({
                title: titleEl.textContent.trim(),
                price: priceEl.textContent.trim(),
                url: a.href,
                image: imgEl ? imgEl.src : null,
            });
        }
    });
    return items;
}
"""

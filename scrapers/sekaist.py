"""
セカンドストリートオンライン スクレイパー
https://www.2ndstreet.jp/

注意: セカストは Cloudflare WAF を使用しており、ヘッドレスブラウザでのアクセスが
      ブロックされる場合があります。その場合は get_cheap_listings() が空リストを返します。
      将来的にはプロキシサービス経由での対応を検討してください。
"""

import re
import time

from models import Item


def get_cheap_listings(keyword: str, max_price: int, count: int = 40) -> list[Item]:
    """セカストで keyword を安い順に検索し、仕入れ候補を返す。"""
    try:
        from playwright.sync_api import sync_playwright
        return _playwright_search(keyword, max_price, count)
    except ImportError:
        print("[セカスト] Playwright が未インストールです。")
        return []
    except Exception as e:
        print(f"[セカスト] 取得エラー ({keyword}): {e}")
        return []


def _playwright_search(keyword: str, max_price: int, count: int) -> list[Item]:
    from playwright.sync_api import sync_playwright

    url = f"https://www.2ndstreet.jp/search?keyword={keyword}&sort=price_asc"

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = browser.new_context(
            locale="ja-JP",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            extra_http_headers={"Accept-Language": "ja-JP,ja;q=0.9"},
        )
        page = ctx.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        page_title = page.title()
        if "Access Denied" in page_title or "403" in page_title:
            browser.close()
            print("[セカスト] アクセス拒否されました（Cloudflare WAF）")
            return []

        try:
            page.wait_for_selector(
                "[class*='product'], [class*='Product'], article, li[class]",
                timeout=10000,
            )
        except Exception:
            pass

        raw = page.evaluate(_EXTRACT_JS)
        browser.close()

    items = []
    for r in raw:
        price = _parse_price(r.get("price", ""))
        if not price or price > max_price:
            continue
        url_val = r.get("url", "")
        if url_val and not url_val.startswith("http"):
            url_val = "https://www.2ndstreet.jp" + url_val
        title = r.get("title", "").strip()
        if not title:
            continue
        items.append(Item(
            title=title,
            price=price,
            url=url_val,
            source="sekaist",
            image_url=r.get("image"),
        ))
    return items[:count]


def _parse_price(text: str):
    nums = re.sub(r"[^\d]", "", str(text))
    return int(nums) if nums else None


_EXTRACT_JS = """
() => {
    const results = [];
    const cardSelectors = [
        '[class*="ProductCard"]', '[class*="product-card"]',
        '[class*="item-card"]', '[class*="ItemCard"]',
        'article[class*="product"]', 'li[class*="product"]',
    ];
    let cards = [];
    for (const sel of cardSelectors) {
        cards = Array.from(document.querySelectorAll(sel));
        if (cards.length > 0) break;
    }
    cards.forEach(card => {
        const a = card.querySelector('a');
        const titleEl = card.querySelector(
            '[class*="name"], [class*="title"], [class*="Name"], h2, h3'
        );
        const priceEl = card.querySelector('[class*="price"], [class*="Price"]');
        const imgEl = card.querySelector('img');
        if (titleEl && priceEl) {
            results.push({
                title: titleEl.textContent.trim(),
                price: priceEl.textContent.trim(),
                url: a ? a.href : '',
                image: imgEl ? (imgEl.src || imgEl.dataset.src) : null,
            });
        }
    });
    return results;
}
"""

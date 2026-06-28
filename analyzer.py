"""
利益計算エンジン

利益の計算式:
  純売上 = メルカリ相場平均 × (1 - メルカリ手数料率) - 発送費用
  利益   = 純売上 - 仕入れ値
"""

import statistics
from typing import Optional

import database
import condition_checker
from models import Deal, Item, MarketPrice
from scrapers import mercari as mercari_scraper


def get_market_price(
    keyword: str,
    sample_count: int = 30,
    cache_hours: int = 12,
    exclude_words: list[str] | None = None,
    required_words: list[str] | None = None,
    price_min: int | None = None,
    price_max: int | None = None,
) -> Optional[MarketPrice]:
    """メルカリ相場を取得（キャッシュ優先）。price_min/price_max で価格帯を限定できる。
    required_words: 同名称が別カテゴリーにも存在する場合の混入防止
    （例: カルティエ「トリニティ」=指輪/サングラス両方に存在するため相場が汚染されうる）。
    """
    # キャッシュキー: 価格帯・必須ワード条件が異なれば別エントリにする
    cache_suffix = f"|{price_min or 0}-{price_max or 0}|{','.join(required_words or [])}"
    cache_key = f"{keyword}{cache_suffix}" if (price_min or price_max or required_words) else keyword

    cached = database.get_cached_price(cache_key, max_age_hours=cache_hours)
    if cached:
        print(f"  [キャッシュ] {keyword}: 中央値 ¥{cached['median_price']:,}")
        return MarketPrice(
            keyword=keyword,
            avg_price=cached["avg_price"],
            median_price=cached["median_price"],
            min_price=cached["min_price"],
            max_price=cached["max_price"],
            sample_count=cached["sample_count"],
        )

    range_str = f" (¥{price_min:,}〜¥{price_max:,})" if (price_min or price_max) else ""
    print(f"  [Mercari] {keyword}{range_str} の売却済み価格を取得中...")
    prices = mercari_scraper.get_sold_prices(
        keyword, count=sample_count, exclude_words=exclude_words,
        required_words=required_words, price_min=price_min, price_max=price_max,
    )

    if len(prices) < 3:
        print(f"  [警告] {keyword}{range_str}: サンプル不足 ({len(prices)}件)")
        return None

    # ── Step1: 上下10%カット（偽物・限定品などの極端な外れ値を除去）
    prices.sort()
    n = len(prices)
    cut = max(1, n // 10)
    trimmed = prices[cut : n - cut] if n - cut > cut else prices

    # ── Step2: 仮平均を算出し、±20%範囲内に絞る
    # 例: 仮平均¥100,000 → ¥80,000〜¥120,000 のみ採用
    # 同一キーワードで別モデル・別状態が混在する場合の相場ブレを抑制
    rough_avg = statistics.mean(trimmed)
    low  = rough_avg * 0.80
    high = rough_avg * 1.20
    tightened = [p for p in trimmed if low <= p <= high]

    # サンプルが3件以上残れば絞り込み後を採用、少なすぎる場合はStep1結果を使用
    final = tightened if len(tightened) >= 3 else trimmed

    avg = int(statistics.mean(final))
    med = int(statistics.median(final))

    market = MarketPrice(
        keyword=keyword,
        avg_price=avg,
        median_price=med,
        min_price=min(final),
        max_price=max(final),
        sample_count=len(prices),
    )

    database.cache_price(
        cache_key, avg, med, market.min_price, market.max_price, len(prices)
    )
    band = f"¥{int(low):,}〜¥{int(high):,}"
    print(f"  [Mercari] {keyword}{range_str}: 中央値 ¥{med:,} / 平均 ¥{avg:,}"
          f" ({len(final)}/{len(prices)}件, ±20%帯: {band})")
    return market


def calculate_deal(
    item: Item,
    keyword: str,
    market: MarketPrice,
    commission_rate: float = 0.10,
    shipping_cost: int = 700,
    min_profit: int = 3000,
) -> Optional[Deal]:
    """仕入れアイテムとメルカリ相場を比較して利益を計算する。"""
    # ── 価格サニティチェック ──────────────────────────────────
    # 仕入れ値が中央値の20%未満 = 相場と乖離しすぎ
    # → レプリカ・別カテゴリー誤混入・激しい破損品の可能性が高い
    # 例: エルメス バッグ中央値¥398,000 → 仕入れ値¥30,000（7.5%）は除外
    SANITY_RATIO = 0.20
    if item.price < market.median_price * SANITY_RATIO:
        return None

    # 商品状態をタイトルから判定
    cond = condition_checker.check(item.title)

    # 中央値 × 状態補正係数 = 販売推定価格
    # 例: 中央値¥50,000 × 美品1.05 = ¥52,500
    #     中央値¥50,000 × 傷あり0.60 = ¥30,000
    reference_price = int(market.median_price * cond.factor)

    net_revenue = int(reference_price * (1 - commission_rate)) - shipping_cost
    profit = net_revenue - item.price

    if profit < min_profit:
        return None

    roi = profit / item.price * 100 if item.price > 0 else 0

    return Deal(
        item=item,
        keyword=keyword,
        mercari_avg_price=reference_price,
        net_revenue=net_revenue,
        estimated_profit=profit,
        roi_percent=round(roi, 1),
        condition_label=cond.label,
        condition_factor=cond.factor,
    )


def find_deals(
    items: list[Item],
    keyword: str,
    market: MarketPrice,
    commission_rate: float,
    shipping_cost: int,
    min_profit: int,
    min_buy_price: int = 0,
) -> list[Deal]:
    """仕入れ候補リストからお得案件を絞り込む（最低仕入れ価格未満は除外）。
    重複チェックは distributor が生徒ごとに行うためここでは不要。
    """
    deals = []
    seen_urls: set[str] = set()
    for item in items:
        if item.price < min_buy_price:
            continue
        if item.url in seen_urls:   # 同一URLの重複を除去
            continue
        seen_urls.add(item.url)
        deal = calculate_deal(
            item, keyword, market, commission_rate, shipping_cost, min_profit
        )
        if deal:
            deals.append(deal)
    deals.sort(key=lambda d: d.estimated_profit, reverse=True)
    return deals

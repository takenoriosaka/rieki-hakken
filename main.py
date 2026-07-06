"""
メルカリ転売アービトラージツール（ダッシュボード版）

ヤフオク・メルカリ・ベクトルパーク・トレファク・ラクマ・Yahoo!フリマをスキャンして
案件を検出し、DBに保存した上でダッシュボード(docs/index.html)を再生成、
GitHub Pagesへ自動publishする。
（セカスト/2nd StreetはCloudflare WAFに常時ブロックされAPI代替もないため無効化済み。scrapers/sekaist.pyは温存）

実行方法:
  python main.py           # 本番実行（スキャン→DB保存→ダッシュボード再生成→git push）
  python main.py --dry-run # DB保存・ダッシュボード更新をせず結果だけ表示
  python scheduler.py      # 毎日定時に自動実行
  python generate_dashboard.py --open  # ダッシュボードHTMLだけ再生成してブラウザで開く
"""

import argparse
import json
import sys
import time
from datetime import datetime

import analyzer
import database
import model_extractor
from models import Deal
from scrapers import yahoo_auctions
from scrapers import mercari as mercari_scraper
from scrapers import vector_park, trefac_fashion, rakuma, yahoo_flea_market

CONFIG_PATH = "config.json"


def run(dry_run: bool = False):
    print("=" * 50)
    print("メルカリ アービトラージ スキャン開始")
    print("=" * 50)

    config   = _load_config()
    settings = config["settings"]
    keywords = config["keywords"]

    database.init_db()

    # ──────────────────────────────────────────
    # スキャン
    # ──────────────────────────────────────────
    all_deals: list[Deal] = []

    for kw_conf in keywords:
        keyword              = kw_conf["name"]
        max_buy              = kw_conf["max_buy_price"]
        min_buy              = kw_conf.get("min_buy_price", 0)
        exclude_words        = kw_conf.get("exclude_words", [])
        required_words       = kw_conf.get("required_words", [])
        immediate_only       = kw_conf.get("yahoo_immediate_only", False)
        require_model_number = kw_conf.get("require_model_number", False)
        brand_name           = kw_conf.get("brand_name", keyword.split()[0])
        category             = kw_conf.get("category", "")

        excl_str = f" 除外:{exclude_words}" if exclude_words else ""
        req_str  = f" 必須:{required_words}" if required_words else ""
        imm_str  = " [即決のみ]" if immediate_only else ""
        mdl_str  = " [型番照合]" if require_model_number else ""
        print(f"\n■ [{keyword}] 検索中... (¥{min_buy:,}〜¥{max_buy:,}){imm_str}{mdl_str}{excl_str}{req_str}")

        # required_words（明示設定）がなければブランド名（キーワード第1単語）を自動適用。
        # メルカリ相場検索にも同じ条件を使い、仕入れ候補と相場のカテゴリーを一致させる
        # 例: カルティエ「トリニティ」は指輪/サングラス両方に存在 → 相場汚染を防止
        auto_brand = keyword.split()[0] if keyword.split() else ""
        effective_required = required_words if required_words else (
            [auto_brand] if auto_brand else []
        )

        market = analyzer.get_market_price(
            keyword,
            sample_count=settings["mercari_sold_sample_count"],
            cache_hours=settings["price_cache_hours"],
            exclude_words=exclude_words,
            required_words=effective_required,
        )
        if market is None:
            print(f"  スキップ: 相場データ取得失敗")
            continue

        sources_items = []

        print(f"  [ヤフオク] 検索中...")
        yahoo_items = yahoo_auctions.get_cheap_listings(
            keyword, max_price=max_buy,
            min_price=min_buy,
            count=settings["search_items_per_source"],
            exclude_words=exclude_words,
            immediate_only=immediate_only,
        )
        print(f"  [ヤフオク] {len(yahoo_items)} 件")
        sources_items.extend(yahoo_items)
        time.sleep(1)

        print(f"  [メルカリ安値] 検索中...")
        cheap_items = mercari_scraper.get_cheap_listings(
            keyword, max_price=max_buy,
            count=settings["search_items_per_source"],
            exclude_words=exclude_words,
        )
        print(f"  [メルカリ安値] {len(cheap_items)} 件")
        sources_items.extend(cheap_items)
        time.sleep(1)

        print(f"  [ベクトルパーク] 検索中...")
        vector_park_items = vector_park.get_cheap_listings(
            keyword, max_price=max_buy, min_price=min_buy,
            count=settings["search_items_per_source"],
            exclude_words=exclude_words,
        )
        print(f"  [ベクトルパーク] {len(vector_park_items)} 件")
        sources_items.extend(vector_park_items)
        time.sleep(1)

        print(f"  [トレファク] 検索中...")
        trefac_items = trefac_fashion.get_cheap_listings(
            keyword, max_price=max_buy, min_price=min_buy,
            count=settings["search_items_per_source"],
            exclude_words=exclude_words,
        )
        print(f"  [トレファク] {len(trefac_items)} 件")
        sources_items.extend(trefac_items)
        time.sleep(1)

        print(f"  [ラクマ] 検索中...")
        rakuma_items = rakuma.get_cheap_listings(
            keyword, max_price=max_buy, min_price=min_buy,
            count=settings["search_items_per_source"],
            exclude_words=exclude_words,
        )
        print(f"  [ラクマ] {len(rakuma_items)} 件")
        sources_items.extend(rakuma_items)
        time.sleep(1)

        print(f"  [Yahoo!フリマ] 検索中...")
        yahoo_flea_items = yahoo_flea_market.get_cheap_listings(
            keyword, max_price=max_buy, min_price=min_buy,
            count=settings["search_items_per_source"],
            exclude_words=exclude_words,
        )
        print(f"  [Yahoo!フリマ] {len(yahoo_flea_items)} 件")
        sources_items.extend(yahoo_flea_items)
        time.sleep(1)

        # sekaist (2nd Street) disabled: Cloudflare WAF blocks all requests, no API alternative

        # ── タイトルフィルター ───────────────────────────────────
        # 1) required_words（明示設定）: いずれかのワードがタイトルに必要
        # 2) ブランド名自動チェック: キーワードの第1単語（ブランド名）が
        #    タイトルに含まれない案件を除外
        #    例: 「エルメス バッグ」検索→タイトルに「エルメス」がない商品を除外
        #    → 他ブランドやあいまいマッチによる誤混入を防ぐ
        if effective_required:
            before = len(sources_items)
            sources_items = [
                item for item in sources_items
                if any(w in item.title for w in effective_required)
            ]
            removed = before - len(sources_items)
            if removed > 0:
                print(f"  [タイトルフィルター] {removed} 件除外 "
                      f"({'|'.join(effective_required)} なし)")

        # ── 型番照合モード ──────────────────────────────────────
        # require_model_number=true の場合:
        #   1. 仕入れ候補タイトルから型番を抽出（見つからなければ商品説明文も確認）
        #   2. 同型番でメルカリ売却済み価格を個別検索
        #   3. 両方で型番確認できた案件のみ採用（相場も型番別に正確化）
        if require_model_number:
            print(f"  [型番照合] 型番抽出・個別相場検索中...")

            candidates: list[tuple] = []   # (item, model)
            pending: list = []             # タイトルで型番なし→説明文を確認する対象

            for item in sources_items:
                if item.price < min_buy:
                    continue
                model = model_extractor.extract(item.title, brand_name)
                if model:
                    candidates.append((item, model))
                else:
                    pending.append(item)

            # タイトルで型番が見つからなかったアイテムは商品説明文を確認する
            if pending:
                print(f"    タイトルで型番なし {len(pending)}件 → 商品説明文を確認中...")
                descriptions: dict[str, str] = {}

                mercari_urls = [it.url for it in pending if it.source == "mercari_cheap"]
                if mercari_urls:
                    descriptions.update(mercari_scraper.get_descriptions(mercari_urls))

                for it in pending:
                    if it.source == "yahoo_auctions":
                        desc = yahoo_auctions.get_description(it.url)
                        if desc:
                            descriptions[it.url] = desc

                for item in pending:
                    desc = descriptions.get(item.url)
                    if not desc:
                        continue
                    model = model_extractor.extract(desc, brand_name)
                    if model:
                        candidates.append((item, model))

            model_deals = []
            seen_models: set[str] = set()

            for item, model in candidates:
                model_key = model_extractor.normalize(model)
                model_keyword = f"{brand_name} {model}"

                # 同型番の相場は1回だけ取得（キャッシュ活用）
                if model_key not in seen_models:
                    seen_models.add(model_key)

                model_market = analyzer.get_market_price(
                    model_keyword,
                    sample_count=settings["mercari_sold_sample_count"],
                    cache_hours=settings["price_cache_hours"],
                    exclude_words=exclude_words,
                    required_words=effective_required,
                )
                if model_market is None:
                    print(f"    [{model}] メルカリ相場なし → スキップ")
                    continue  # メルカリで型番確認できず → スキップ

                deal = analyzer.calculate_deal(
                    item, model_keyword, model_market,
                    commission_rate=settings["mercari_commission_rate"],
                    shipping_cost=settings["assumed_shipping_cost"],
                    min_profit=settings["min_profit_yen"],
                )
                if deal:
                    deal.brand = brand_name
                    deal.model = model
                    deal.category = category
                    model_deals.append(deal)

            model_deals.sort(key=lambda d: d.estimated_profit, reverse=True)
            if model_deals:
                print(f"  --> {len(model_deals)} 件の案件 [型番照合済み]")
                all_deals.extend(model_deals)
            else:
                print(f"  --> 案件なし [型番照合済み]")
            continue  # 通常の find_deals をスキップ
        # ────────────────────────────────────────────────────────

        # ── 案件計算（仕入れ値ベースの価格帯で相場を個別取得）────────────────
        # ¥10,000単位でバケット化 → 同価格帯の複数商品はキャッシュ共有
        # 例: ¥30,000のグッチバッグ → メルカリ売却済みを¥30,000〜¥180,000に絞る
        #     ¥300,000のグッチバッグ → ¥300,000〜¥1,800,000に絞る → 別相場として算出
        PRICE_BUCKET = 10_000
        deals = []
        seen_urls: set[str] = set()
        for item in sources_items:
            if item.price < min_buy:
                continue
            if item.url in seen_urls:
                continue
            seen_urls.add(item.url)

            p_bucket = max((item.price // PRICE_BUCKET) * PRICE_BUCKET, PRICE_BUCKET)
            p_min    = p_bucket
            p_max    = p_bucket * 6
            item_market = analyzer.get_market_price(
                keyword,
                sample_count=settings["mercari_sold_sample_count"],
                cache_hours=settings["price_cache_hours"],
                exclude_words=exclude_words,
                required_words=effective_required,
                price_min=p_min,
                price_max=p_max,
            )
            if item_market is None:
                continue

            deal = analyzer.calculate_deal(
                item, keyword, item_market,
                commission_rate=settings["mercari_commission_rate"],
                shipping_cost=settings["assumed_shipping_cost"],
                min_profit=settings["min_profit_yen"],
            )
            if deal:
                deal.brand = brand_name
                deal.category = category
                deals.append(deal)

        deals.sort(key=lambda d: d.estimated_profit, reverse=True)

        if deals:
            print(f"  --> {len(deals)} 件の案件")
            all_deals.extend(deals)
        else:
            print(f"  --> 案件なし")

    all_deals.sort(key=lambda d: d.estimated_profit, reverse=True)

    print(f"\n{'=' * 50}")
    print(f"スキャン完了: 合計 {len(all_deals)} 件")
    print(f"{'=' * 50}")

    # ──────────────────────────────────────────
    # ダッシュボード出力
    # ──────────────────────────────────────────
    if dry_run:
        print("\n[DRY RUN] DB保存・ダッシュボード更新をスキップします")
        _print_summary(all_deals)
        return

    if all_deals:
        scanned_at = datetime.now().isoformat()
        database.save_scan_deals(all_deals, scanned_at)
        print(f"\nDB保存完了: {len(all_deals)} 件 → scan_deals テーブル")

    # ダッシュボードHTMLを再生成
    import subprocess
    result = subprocess.run(
        [sys.executable, "generate_dashboard.py"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"ダッシュボード生成完了: docs/index.html")
    else:
        print(f"[警告] ダッシュボード生成エラー: {result.stderr}")

    # GitHub Pages に自動プッシュ
    try:
        subprocess.run(["git", "add", "docs/index.html", "docs/style.css", "docs/app.js"], check=True)
        subprocess.run(
            ["git", "commit", "-m",
             f"Update dashboard: {len(all_deals)} deals ({datetime.now().strftime('%Y-%m-%d %H:%M')})"],
            check=True,
        )
        subprocess.run(["git", "push"], check=True)
        print("GitHub Pages 更新完了")
    except subprocess.CalledProcessError as e:
        print(f"[警告] git push 失敗: {e}")
        print("手動で: git add docs/index.html && git commit -m 'Update' && git push")


# ──────────────────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        sys.exit(f"config.json が見つかりません: {CONFIG_PATH}")
    except json.JSONDecodeError as e:
        sys.exit(f"config.json の形式が不正です: {e}")


def _print_summary(deals: list[Deal]):
    print()
    for i, d in enumerate(deals, 1):
        print(f"  {i:2}. ¥{d.item.price:,} → 利益 ¥{d.estimated_profit:,}"
              f" ({d.format_source()}) {d.item.title[:45]}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="メルカリ転売アービトラージツール")
    parser.add_argument("--dry-run", action="store_true",
                        help="DB保存・ダッシュボード更新せず結果を表示のみ")
    args = parser.parse_args()
    run(dry_run=args.dry_run)

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
import os
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

# 商品説明文キャッシュの有効期限（時間）。説明文は出品後ほぼ変わらないため長めでよい
DESCRIPTION_CACHE_HOURS = 24 * 7

# スキャン統計（説明文。相場取得の統計は analyzer.stats）
_stats = {"desc_fetched": 0, "desc_cached": 0}

# 仕入れ先キー（Item.source と同じ値）→ ログ表示名。並び順＝検索順。
SOURCE_LABELS = {
    "yahoo_auctions": "ヤフオク",
    "mercari_cheap":  "メルカリ安値",
    "vector_park":    "ベクトルパーク",
    "trefac":         "トレファク",
    "rakuma":         "ラクマ",
    "yahoo_flea":     "Yahoo!フリマ",
}


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
    # 実処理は scan()（ローカルのリサーチ画面 app.py と共用）
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        print("[エラー] Playwright が未インストールです。setup.sh を実行してください。")
        sys.exit(1)

    all_deals: list[Deal] = scan(keywords, settings)

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

    # ローカルのリサーチ画面（app.py）は scan() しか呼ばないが、万一 run() が
    # 呼ばれても DB保存・Pages用ファイル生成・git push をしないよう、
    # app.py が立てる環境変数で二重に防止する。
    if os.environ.get("RIEKI_LOCAL_APP") == "1":
        print("[ローカル画面] DB保存・ダッシュボード生成・git push はスキップしました")
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


def scan(
    keywords: list[dict],
    settings: dict,
    sources=None,
    item_filter=None,
    progress=None,
    should_stop=None,
) -> list[Deal]:
    """Playwright ブラウザを1つ起動してキーワード群をスキャンし、案件リストを返す。
    DB保存・ダッシュボード生成・git push は一切行わない（それらは run() 側の責務）。
    ローカルのリサーチ画面（app.py）からもこの関数を呼ぶ。

    sources:     検索する仕入れ先キーの集合（None なら全仕入れ先）。SOURCE_LABELS 参照
    item_filter: 仕入れ候補 Item を受け取り True/False を返す関数（相場計算前に適用）
    progress:    progress(index, total, keyword) 形式のコールバック（index は 0 始まり）
    should_stop: True を返すとキーワードの切れ目で中断する関数
    """
    from playwright.sync_api import sync_playwright

    started = time.time()
    analyzer.reset_session()
    yahoo_auctions.reset_retry_state()
    for k in _stats:
        _stats[k] = 0

    # Playwright ブラウザはスキャン全体で1つだけ起動し使い回す（毎回起動すると
    # 型番/価格帯ごとのメルカリ相場ルックアップが数百回発生し、1件19秒前後×件数で
    # GitHub Actions のタイムアウトを超えていたため）。例外時も確実にクローズする。
    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = mercari_scraper.new_page(browser)
            return _scan_keywords(
                keywords, settings, page,
                sources=sources, item_filter=item_filter,
                progress=progress, should_stop=should_stop,
            )
        finally:
            browser.close()
    finally:
        playwright.stop()
        st = analyzer.stats
        print(f"\n[スキャン統計] 所要 {(time.time() - started) / 60:.1f}分 / "
              f"メルカリ相場取得 {st['fetch']}回（うち0件 {st['zero']}回）/ "
              f"DBキャッシュ利用 {st['db_cache']}回 / 同一スキャン内の再利用 {st['memo']}回 / "
              f"説明文取得 {_stats['desc_fetched']}件（キャッシュ利用 {_stats['desc_cached']}件）")


def _scan_keywords(
    keywords: list[dict], settings: dict, page,
    sources=None, item_filter=None, progress=None, should_stop=None,
) -> list[Deal]:
    """config.json の keywords を順にスキャンし、案件リストを返す。
    page: scan() で起動・使い回している Playwright ページ
    （メルカリ相場ルックアップのたびにブラウザを起動しないようにするため）。
    sources / item_filter / progress / should_stop は scan() の説明を参照。
    """
    all_deals: list[Deal] = []
    total = len(keywords)

    for kw_index, kw_conf in enumerate(keywords):
        if should_stop and should_stop():
            print("\n[中断] ユーザー操作によりスキャンを中断しました")
            break
        if progress:
            # 英字名でも検索するモデルは「カタカナ / 英字」の両方の検索語を表示する
            progress(kw_index, total, " / ".join(kw_conf.get("search_queries") or [kw_conf["name"]]))
        keyword              = kw_conf["name"]
        max_buy              = kw_conf["max_buy_price"]
        min_buy              = kw_conf.get("min_buy_price", 0)
        exclude_words        = kw_conf.get("exclude_words", [])
        required_words       = kw_conf.get("required_words", [])
        immediate_only       = kw_conf.get("yahoo_immediate_only", False)
        require_model_number = kw_conf.get("require_model_number", False)
        brand_name           = kw_conf.get("brand_name", keyword.split()[0])
        category             = kw_conf.get("category", "")
        # モデル別相場に必要な売却済みの最少件数（少なすぎる相場は信用しない。未設定なら1件）
        min_market_samples   = kw_conf.get("min_market_samples", 1)

        # 仕入れ先の検索語。ローカル画面でモデル名を選んだ場合は「カタカナ名」「英字名」の
        # 2本が search_queries に入る（app.build_plan）。通常のキーワードは keyword の1本だけ
        queries = [q for q in (kw_conf.get("search_queries") or [keyword]) if q]
        excl_str = f" 除外:{exclude_words}" if exclude_words else ""
        req_str  = f" 必須:{required_words}" if required_words else ""
        imm_str  = " [即決のみ]" if immediate_only else ""
        mdl_str  = " [型番照合]" if require_model_number else ""
        print(f"\n■ [{keyword}] 検索中... (¥{min_buy:,}〜¥{max_buy:,}){imm_str}{mdl_str}{excl_str}{req_str}")
        if len(queries) > 1:
            print(f"  検索語: {' / '.join(queries)}")

        # required_words（明示設定）がなければブランド名（キーワード第1単語）を自動適用。
        # メルカリ相場検索にも同じ条件を使い、仕入れ候補と相場のカテゴリーを一致させる
        # 例: カルティエ「トリニティ」は指輪/サングラス両方に存在 → 相場汚染を防止
        auto_brand = keyword.split()[0] if keyword.split() else ""
        effective_required = required_words if required_words else (
            [auto_brand] if auto_brand else []
        )

        market = analyzer.get_market_price(
            page,
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
        per_source = settings["search_items_per_source"]

        # (仕入れ先キー, 取得関数) の順に検索する。sources で選ばれたものだけ実行
        fetchers = [
            ("yahoo_auctions", lambda q: yahoo_auctions.get_cheap_listings(
                q, max_price=max_buy,
                min_price=min_buy,
                count=per_source,
                exclude_words=exclude_words,
                immediate_only=immediate_only,
            )),
            ("mercari_cheap", lambda q: mercari_scraper.get_cheap_listings(
                page, q, max_price=max_buy,
                count=per_source,
                exclude_words=exclude_words,
            )),
            ("vector_park", lambda q: vector_park.get_cheap_listings(
                q, max_price=max_buy, min_price=min_buy,
                count=per_source,
                exclude_words=exclude_words,
            )),
            ("trefac", lambda q: trefac_fashion.get_cheap_listings(
                q, max_price=max_buy, min_price=min_buy,
                count=per_source,
                exclude_words=exclude_words,
            )),
            ("rakuma", lambda q: rakuma.get_cheap_listings(
                q, max_price=max_buy, min_price=min_buy,
                count=per_source,
                exclude_words=exclude_words,
            )),
            ("yahoo_flea", lambda q: yahoo_flea_market.get_cheap_listings(
                q, max_price=max_buy, min_price=min_buy,
                count=per_source,
                exclude_words=exclude_words,
            )),
        ]
        seen_item_urls: set[str] = set()
        for source_key, fetch in fetchers:
            if sources is not None and source_key not in sources:
                continue
            label = SOURCE_LABELS[source_key]
            for q in queries:
                q_str = f" 「{q}」" if len(queries) > 1 else ""
                print(f"  [{label}]{q_str} 検索中...")
                fetched = fetch(q)
                # 複数の検索語で同じ出品が出るため商品URLで重複を除く
                new_items = []
                for it in fetched:
                    if it.url and it.url in seen_item_urls:
                        continue
                    seen_item_urls.add(it.url)
                    new_items.append(it)
                dup = len(fetched) - len(new_items)
                dup_str = f"（重複 {dup} 件除外）" if dup else ""
                print(f"  [{label}]{q_str} {len(fetched)} 件{dup_str}")
                sources_items.extend(new_items)
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
            # 大文字小文字は区別しない（THE NORTH FACE / The North Face など）
            req_lower = [w.lower() for w in effective_required]
            sources_items = [
                item for item in sources_items
                if any(w in item.title.lower() for w in req_lower)
            ]
            removed = before - len(sources_items)
            if removed > 0:
                print(f"  [タイトルフィルター] {removed} 件除外 "
                      f"({'|'.join(effective_required)} なし)")

        # 呼び出し元指定の追加条件（ローカル画面の「オークション残り時間」など）
        if item_filter is not None:
            before = len(sources_items)
            sources_items = [item for item in sources_items if item_filter(item)]
            removed = before - len(sources_items)
            if removed > 0:
                print(f"  [条件フィルター] {removed} 件除外")

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
                model = model_extractor.extract(item.title, brand_name, category)
                if model:
                    candidates.append((item, model))
                else:
                    pending.append(item)

            # タイトルで型番が見つからなかったアイテムは商品説明文を確認する
            # （種別までタイトルで絞るブランド＝ヘルノのラミナーダウンコートは説明文を使わない）
            if pending and not model_extractor.uses_description(brand_name):
                print(f"    タイトルで対象モデルなし {len(pending)}件 → 除外（タイトルのみで照合）")
                pending = []
            if pending:
                print(f"    タイトルで型番なし {len(pending)}件 → 商品説明文を確認中...")
                # 説明文は DB にキャッシュし、DESCRIPTION_CACHE_HOURS 以内に取得済みの
                # 出品は再取得しない（安値順の出品は次回スキャンでも多くが残っているため）
                target_urls = [it.url for it in pending
                               if it.source in ("mercari_cheap", "yahoo_auctions")]
                descriptions = database.get_cached_descriptions(
                    target_urls, max_age_hours=DESCRIPTION_CACHE_HOURS)
                _stats["desc_cached"] += len(descriptions)
                fetched: dict[str, str] = {}

                mercari_urls = [it.url for it in pending
                                if it.source == "mercari_cheap" and it.url not in descriptions]
                if mercari_urls:
                    got = mercari_scraper.get_descriptions(page, mercari_urls)
                    _stats["desc_fetched"] += len(mercari_urls)
                    # 説明文なし('')も記録する。ページ取得エラーの URL は含まれないので次回再取得
                    fetched.update(got)

                for it in pending:
                    if it.source == "yahoo_auctions" and it.url not in descriptions:
                        desc = yahoo_auctions.get_description(it.url)
                        _stats["desc_fetched"] += 1
                        if desc is not None:
                            fetched[it.url] = desc

                database.cache_descriptions(fetched)
                descriptions.update(fetched)

                for item in pending:
                    desc = descriptions.get(item.url)
                    if not desc:
                        continue
                    model = model_extractor.extract(desc, brand_name, category)
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

                # モデル名（型番でないもの）は、売却済みタイトルも同じモデルと判定されるものだけで
                # 相場を出す（ディオニシオ に ディオニシオドゥエ、ヌプシジャケット に ショートヌプシ、
                # ビーゼロワン に セーブザチルドレン を混ぜない）。
                # さらにカタカナ名・英字名の両方で売却済みを取得し、商品URLで重複を除いて合算する
                # （例: デュベティカ「フェーベ」はタイトルの多くが FEBE 表記）。
                # 英字名が無い・同じ表記のモデルは1回だけ検索する。
                name_filter, filter_key, alt_keywords = None, "", None
                canon = model_extractor.extract_name(model, brand_name)
                if canon == model:
                    name_filter = (lambda t, _m=model, _b=brand_name:
                                   model_extractor.extract_name(t, _b) == _m)
                    filter_key = f"model:{model}"
                    names = model_extractor.search_names(model, brand_name)
                    model_keyword = f"{brand_name} {names[0]}"
                    alt_keywords = [f"{brand_name} {n}" for n in names[1:]]
                elif model_extractor.exact_number_market(brand_name, category):
                    # 型番の相場も同じ型番の売却済みだけで出す（レイバン: RB2140 に RB2140F を混ぜない、
                    # グッチ時計: 「グッチ 1500L」の検索結果に混ざる 1400L・1900L を除く）
                    name_filter = (lambda t, _m=model, _b=brand_name, _c=category:
                                   model_extractor.extract(t, _b, _c) == _m)
                    filter_key = f"model:{model}"

                model_market = analyzer.get_market_price(
                    page,
                    model_keyword,
                    sample_count=settings["mercari_sold_sample_count"],
                    cache_hours=settings["price_cache_hours"],
                    exclude_words=exclude_words,
                    required_words=effective_required,
                    title_filter=name_filter,
                    filter_key=filter_key,
                    alt_keywords=alt_keywords,
                    min_samples=min_market_samples,
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
                    deal.search_keyword = keyword
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
                page,
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
                deal.search_keyword = keyword
                deals.append(deal)

        deals.sort(key=lambda d: d.estimated_profit, reverse=True)

        if deals:
            print(f"  --> {len(deals)} 件の案件")
            all_deals.extend(deals)
        else:
            print(f"  --> 案件なし")

    return all_deals


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

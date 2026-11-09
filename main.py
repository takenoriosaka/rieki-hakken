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
import condition_checker
import database
import model_extractor
import local_inspector
import ocr_reader
import photo_reader
from models import Deal
from scrapers import yahoo_auctions
from scrapers import mercari as mercari_scraper
from scrapers import vector_park, trefac_fashion, rakuma, yahoo_flea_market, yahoo_shopping

CONFIG_PATH = "config.json"

# 商品説明文キャッシュの有効期限（時間）。説明文は出品後ほぼ変わらないため長めでよい
DESCRIPTION_CACHE_HOURS = 24 * 7

# requests で1件ずつ説明文を取得する仕入れ先: source → (取得関数, 取得間隔秒)。
# 取得関数は説明文なしなら ''、取得エラーなら None（キャッシュせず次回再取得）を返す。
# 間隔は各スクレイパーの一覧取得と同程度（ベクトルパークは robots.txt の Crawl-delay: 10）。
# メルカリ安値は Playwright でまとめて取得するため別処理（mercari_scraper.get_descriptions）。
DESCRIPTION_FETCHERS = {
    "yahoo_auctions": (yahoo_auctions.get_description, 0),
    "vector_park":    (vector_park.get_description, 10),
    "trefac":         (trefac_fashion.get_description, 1),
    "rakuma":         (rakuma.get_description, 1),
    "yahoo_flea":     (yahoo_flea_market.get_description, 1),
    "yahoo_shopping": (yahoo_shopping.get_description, 1),
}

# requests で1件ずつ商品写真のURLを取得する仕入れ先: source → (取得関数, 取得間隔秒)。写真から型番を読む機能
# （photo_reader）用。説明文の取得で同じページを読んでいれば再取得しない（各スクレイパーの get_image_urls）。
# 取得関数は取得エラーなら None を返す。間隔は DESCRIPTION_FETCHERS と同じ。
# メルカリ安値は Playwright でまとめて取得するため別処理（mercari_scraper.get_image_urls）。
IMAGE_FETCHERS = {
    "yahoo_auctions": (yahoo_auctions.get_image_urls, 0),
    "vector_park":    (vector_park.get_image_urls, 10),
    "trefac":         (trefac_fashion.get_image_urls, 1),
    "rakuma":         (rakuma.get_image_urls, 1),
    "yahoo_flea":     (yahoo_flea_market.get_image_urls, 1),
    "yahoo_shopping": (yahoo_shopping.get_image_urls, 1),
}

# スキャン統計（説明文。相場取得の統計は analyzer.stats、写真読み取りの統計は photo_reader.stats）
_stats = {"desc_fetched": 0, "desc_cached": 0}

# 仕入れ先キー（Item.source と同じ値）→ ログ表示名。並び順＝検索順。
SOURCE_LABELS = {
    "yahoo_auctions": "ヤフオク",
    "mercari_cheap":  "メルカリ安値",
    "vector_park":    "ベクトルパーク",
    "trefac":         "トレファク",
    "rakuma":         "ラクマ",
    "yahoo_flea":     "Yahoo!フリマ",
    "yahoo_shopping": "Yahoo!ショッピング",
}

# ブランド名も英字にした検索語（config.json の en_brand_name。例: 「CANADA GOOSE JASPER」。app.build_plan が
# キーワードの en_brand_queries に入れる）を使う仕入れ先。settings.en_brand_sources で変更できる。
# 2026-11-09 にダウン4ブランド（タトラス・モンクレール・ノースフェイス・カナダグース）の9モデルで、カタカナのブランド名の
# 検索（「タトラス ラビアナ」「タトラス LAVIANA」）と英字の検索（「TATRAS LAVIANA」）を仕入れ先ごとに比べた結果:
#   ヤフオク: 9モデルとも英字の検索でだけ出る出品0件（カタカナの検索で英字タイトルの出品も出る）→ 使わない
#   メルカリ安値（MONCLER MAYA +22件 等）・ベクトルパーク（カナダグース +58件 等）・Yahoo!フリマ（ノースフェイス +19件）・
#   Yahoo!ショッピング（TATRAS LAVIANA +10件 等）・ラクマ（TATRAS POLICORO 等 +9件）→ 使う
#   トレファク: +1件だけ（「THE NORTH FACEザ ノース フェイス」表記）。時間を減らすため使わない（2026-11-09 ユーザー決定）
EN_BRAND_SOURCES = ["mercari_cheap", "vector_park", "rakuma", "yahoo_flea", "yahoo_shopping"]


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
    live=None,
) -> list[Deal]:
    """Playwright ブラウザを1つ起動してキーワード群をスキャンし、案件リストを返す。
    DB保存・ダッシュボード生成・git push は一切行わない（それらは run() 側の責務）。
    ローカルのリサーチ画面（app.py）からもこの関数を呼ぶ。

    sources:     検索する仕入れ先キーの集合（None なら全仕入れ先）。SOURCE_LABELS 参照
    item_filter: 仕入れ候補 Item を受け取り True/False を返す関数（相場計算前に適用）
    progress:    progress(index, total, keyword[, q_index, q_total]) 形式のコールバック（index は 0 始まり）。
                 q_index / q_total はキーワード内の何本目の検索か（仕入れ先 × 検索語。0 始まり）
    should_stop: True を返すと中断する関数。キーワードの切れ目のほか、仕入れ先の検索語の切れ目・
                 説明文の取得・型番ごとの相場取得の途中でも確認する（検索語の多いキーワード＝タトラスの全モデル用）
    live:        案件を見つけた途中経過を受け取るオブジェクト（ローカル画面の app._LiveResults。省略可）。
                 live.deal(案件) = 利益を計算できた案件を1件ずつ / live.photo_start(件数) = 写真の確認を始めた /
                 live.photo(url, 計算し直す関数) = その出品の写真を判定できた。結果（戻り値）には影響しない
    """
    from playwright.sync_api import sync_playwright

    started = time.time()
    analyzer.reset_session()
    yahoo_auctions.reset_retry_state()
    for k in _stats:
        _stats[k] = 0
    photo_reader.reset_session(settings)
    ocr_reader.reset_session(settings)
    local_inspector.reset_session(settings)
    # 案件の写真で傷・汚れを確認するスキャンか（有料: 本人の Mac でキーがあり画面でオンにしたとき /
    # 無料: Mac 内の AI（Ollama）が使え、画面の「写真で傷・汚れ確認（無料・Mac内AI）」をオンにしたとき）
    analyzer.set_photo_check(photo_reader.condition_enabled() or local_inspector.enabled())
    # 前回スキャンで控えた商品写真のURLを捨てる（ローカル画面を起動したまま何度スキャンしても溜まらないように）
    for mod in (yahoo_auctions, mercari_scraper, vector_park, trefac_fashion, rakuma, yahoo_flea_market,
                yahoo_shopping):
        mod._image_urls.clear()
    yahoo_shopping._descriptions.clear()

    # Playwright ブラウザはスキャン全体で1つだけ起動し使い回す（毎回起動すると
    # 型番/価格帯ごとのメルカリ相場ルックアップが数百回発生し、1件19秒前後×件数で
    # GitHub Actions のタイムアウトを超えていたため）。例外時も確実にクローズする。
    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = mercari_scraper.new_page(browser)
            deals = _scan_keywords(
                keywords, settings, page,
                sources=sources, item_filter=item_filter,
                progress=progress, should_stop=should_stop, live=live,
            )
            return _check_photo_conditions(deals, settings, page, len(keywords),
                                           progress=progress, should_stop=should_stop, live=live)
        finally:
            browser.close()
    finally:
        playwright.stop()
        st = analyzer.stats
        print(f"\n[スキャン統計] 所要 {(time.time() - started) / 60:.1f}分 / "
              f"メルカリ相場取得 {st['fetch']}回（うち0件 {st['zero']}回）/ "
              f"DBキャッシュ利用 {st['db_cache']}回 / 同一スキャン内の再利用 {st['memo']}回 / "
              f"説明文取得 {_stats['desc_fetched']}件（キャッシュ利用 {_stats['desc_cached']}件）")
        if ocr_reader.enabled():
            print(f"[スキャン統計] {ocr_reader.summary()}")
        if photo_reader.enabled() or photo_reader.stats["api_calls"]:
            print(f"[スキャン統計] {photo_reader.summary()}")
        if local_inspector.stats["inspect_targets"] or local_inspector.stats["read_targets"]:
            print(f"[スキャン統計] {local_inspector.summary()}")


def source_fetchers(page, settings: dict, max_buy: int, min_buy: int, per_source: int,
                    exclude_words: list, immediate_only: bool = False) -> list[tuple]:
    """仕入れ先ごとの検索関数の一覧 [(仕入れ先キー, 検索語 → Item の一覧)]（SOURCE_LABELS の順）。
    通常のスキャン（_scan_keywords）とフリーリサーチ（free_research）で共用する"""
    return [
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
        # Yahoo!ショッピング: 中古のみが既定（settings.yahoo_shopping_condition: used / new / all）。
        # 送料無料でない出品の送料は、タイトルの照合のあと fill_postage で商品ページから足す
        ("yahoo_shopping", lambda q: yahoo_shopping.get_cheap_listings(
            q, max_price=max_buy, min_price=min_buy,
            count=per_source,
            exclude_words=exclude_words,
            condition=settings.get("yahoo_shopping_condition", "used"),
        )),
    ]


def _scan_keywords(
    keywords: list[dict], settings: dict, page,
    sources=None, item_filter=None, progress=None, should_stop=None, live=None,
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
            # （モデル名展開の検索で検索語が多いときは先頭だけ。検索中の語は仕入れ先の検索ごとに表示し直す）
            qs = kw_conf.get("search_queries") or [kw_conf["name"]]
            progress(kw_index, total, " / ".join(qs) if len(qs) <= 3 else f"{qs[0]} ほか検索語 {len(qs) - 1} 本")
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
        # 2本が search_queries に入る（app.build_plan）。モデル名展開の検索（search_all_models。ダウンの各ブランド）で
        # モデルを選ばなかった場合は keyword + 全モデルの検索語が入る。通常のキーワードは keyword の1本だけ
        queries = [q for q in (kw_conf.get("search_queries") or [keyword]) if q]
        excl_str = f" 除外:{exclude_words}" if exclude_words else ""
        req_str  = f" 必須:{required_words}" if required_words else ""
        imm_str  = " [即決のみ]" if immediate_only else ""
        mdl_str  = " [型番照合]" if require_model_number else ""
        print(f"\n■ [{keyword}] 検索中... (¥{min_buy:,}〜¥{max_buy:,}){imm_str}{mdl_str}{excl_str}{req_str}")
        if len(queries) > 1:
            shown = queries if len(queries) <= 6 else queries[:6] + [f"ほか{len(queries) - 6}本"]
            print(f"  検索語 {len(queries)} 本: {' / '.join(shown)}")

        # required_words（明示設定）がなければブランド名（キーワード第1単語）を自動適用。
        # メルカリ相場検索にも同じ条件を使い、仕入れ候補と相場のカテゴリーを一致させる
        # 例: カルティエ「トリニティ」は指輪/サングラス両方に存在 → 相場汚染を防止
        auto_brand = keyword.split()[0] if keyword.split() else ""
        effective_required = required_words if required_words else (
            [auto_brand] if auto_brand else []
        )

        # exclude_words（部分一致）で書けない除外条件（ディオールのメガネ・オークリーの社外レンズ・リックの「似/風」等。
        # model_extractor.title_excluded）。仕入れ候補のタイトルとメルカリ相場の売却済みの両方に使う
        title_rule = model_extractor.has_title_rule(brand_name, category)

        def with_title_rule(f=None):
            if not title_rule:
                return f
            return (lambda t, _f=f, _b=brand_name, _c=category:
                    not model_extractor.title_excluded(t, _b, _c) and (_f is None or _f(t)))
        rule_key = "|ng1" if title_rule else ""   # 相場キャッシュを除外条件の無い頃の結果と分ける

        market = analyzer.get_market_price(
            page,
            keyword,
            sample_count=settings["mercari_sold_sample_count"],
            cache_hours=settings["price_cache_hours"],
            exclude_words=exclude_words,
            required_words=effective_required,
            title_filter=with_title_rule(),
            filter_key=rule_key,
        )
        if market is None:
            print(f"  スキップ: 相場データ取得失敗")
            continue

        sources_items = []
        per_source = settings["search_items_per_source"]

        # (仕入れ先キー, 取得関数) の順に検索する。sources で選ばれたものだけ実行
        fetchers = source_fetchers(page, settings, max_buy, min_buy, per_source,
                                   exclude_words, immediate_only)
        seen_item_urls: set[str] = set()
        fetchers = [(k, f) for k, f in fetchers if sources is None or k in sources]
        # 英字ブランド名の検索語は EN_BRAND_SOURCES の仕入れ先でだけ使う
        en_queries = set(kw_conf.get("en_brand_queries") or [])
        en_sources = set(settings.get("en_brand_sources", EN_BRAND_SOURCES))

        def source_queries(source_key: str) -> list[str]:
            if source_key in en_sources:
                return queries
            return [q for q in queries if q not in en_queries]

        # このキーワードの検索の本数（仕入れ先 × 検索語）
        n_search = sum(len(source_queries(k)) for k, _f in fetchers)
        search_no = 0
        stopped = False
        for source_key, fetch in fetchers:
            label = SOURCE_LABELS[source_key]
            for q in source_queries(source_key):
                # 中止ボタンは検索語の切れ目でも効かせる（タトラスの全モデル×2表記×仕入れ先だと
                # 1キーワードに数百本の検索があり、キーワードの切れ目だけでは長時間止まらないため）
                if should_stop and should_stop():
                    stopped = True
                    break
                search_no += 1
                if progress and n_search > 1:
                    progress(kw_index, total, f"{q}（{label}）", search_no - 1, n_search)
                q_str = f" 「{q}」" if len(queries) > 1 else ""
                no_str = f" ({search_no}/{n_search})" if n_search > len(fetchers) else ""
                print(f"  [{label}]{q_str}{no_str} 検索中...")
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
            if stopped:
                break
        if stopped:
            # 取得済みの分も照合しない（説明文・写真・相場の確認に時間がかかり、すぐ止まらないため）
            print(f"\n[中断] ユーザー操作によりスキャンを中断しました（[{keyword}] 検索 {search_no}/{n_search} 本目まで）")
            break

        # sekaist (2nd Street) disabled: Cloudflare WAF blocks all requests, no API alternative

        # ── タイトルフィルター ───────────────────────────────────
        # 1) required_words（明示設定）: いずれかのワードがタイトルに必要
        # 2) ブランド名自動チェック: キーワードの第1単語（ブランド名）が
        #    タイトルに含まれない案件を除外
        #    例: 「エルメス バッグ」検索→タイトルに「エルメス」がない商品を除外
        #    → 他ブランドやあいまいマッチによる誤混入を防ぐ
        if effective_required:
            before = len(sources_items)
            # モデル名展開の検索（search_all_models。ダウンの各ブランド）では、required_words（ダウン等）が
            # タイトルに無くても、ブランド名と登録済みのモデル名がタイトルにある出品は通す。
            # 「タトラス ポリテアマ」で取れる出品は「TATRAS POLITEAMA ブラック 1」のようにダウンと書かれないことが
            # 多いが、登録済みのモデルはすべてダウンのモデル（model_extractor._TATRAS_MODELS・_CANADAGOOSE_MODELS・_MONCLER_MODELS・
            # _STRICT_MODELS のピレネックス・デュベティカ・ノースフェイス・ヘルノ。ヘルノは種別条件 _herno_rule でダウン+コートを必須のまま。
            # カナダグースも「CANADA GOOSE JASPER PARKA」「ジャスパーパーカー」のようにダウンと書かれないことが多い）のため。
            # 相場（メルカリ売却済み）は従来どおり required_words を満たすものだけで出す。
            # またモデル名展開の検索では、タイトルにブランド名（model_extractor.BRAND_ALIASES の表記。タトラス/TATRAS 等）が無い出品は除く。
            # 検索語が多いぶん他ブランドの出品も混ざり（実例:「DUVETICA ARISTEO ダウンベスト」が
            # 説明文のモデル名でドミツィアーノと判定された）、「ノコ」「タロ」等の短いモデル名だけでは判定できないため
            all_models = bool(kw_conf.get("search_all_models"))
            by_name = no_brand = ng = 0

            def passes(item) -> bool:
                nonlocal by_name, no_brand, ng
                if title_rule and model_extractor.title_excluded(item.title, brand_name, category):
                    ng += 1
                    return False
                if all_models and not model_extractor.has_brand(item.title, brand_name):
                    no_brand += 1
                    return False
                # 大文字小文字は区別しない（THE NORTH FACE / The North Face など）。
                # LV 等の短い英字は単語境界付きで照合する（SILVER の lv に一致させない。model_extractor.has_word）
                if any(model_extractor.has_word(item.title, w) for w in effective_required):
                    return True
                if not all_models:
                    return False
                name = model_extractor.extract_name(item.title, brand_name)
                if name and model_extractor.name_fits(name, brand_name, category):
                    by_name += 1
                    return True
                return False

            sources_items = [item for item in sources_items if passes(item)]
            removed = before - len(sources_items) - no_brand - ng
            if ng:
                print(f"  [タイトルフィルター] {ng} 件除外 (メガネ・社外レンズ・類似品など)")
            if no_brand:
                print(f"  [タイトルフィルター] {no_brand} 件除外 (ブランド名なし)")
            if removed > 0:
                print(f"  [タイトルフィルター] {removed} 件除外 "
                      f"({'|'.join(effective_required)} なし)")
            if by_name:
                print(f"  [タイトルフィルター] {'|'.join(effective_required)} なしでもモデル名で通過 {by_name} 件")

        # 呼び出し元指定の追加条件（ローカル画面の「オークション残り時間」など）
        if item_filter is not None:
            before = len(sources_items)
            sources_items = [item for item in sources_items if item_filter(item)]
            removed = before - len(sources_items)
            if removed > 0:
                print(f"  [条件フィルター] {removed} 件除外")

        sources_items = fill_shopping_postage(sources_items, settings, should_stop)

        # ── 型番照合モード ──────────────────────────────────────
        # require_model_number=true の場合:
        #   1. 仕入れ候補タイトルから型番を抽出（見つからなければ商品説明文も確認）
        #   2. 同型番でメルカリ売却済み価格を個別検索
        #   3. 両方で型番確認できた案件のみ採用（相場も型番別に正確化）
        if require_model_number:
            print(f"  [型番照合] 型番抽出・個別相場検索中...")

            candidates: list[tuple] = []   # (item, model)
            pending: list = []             # タイトルで型番なし→説明文を確認する対象
            # 型番の相場が無いときに代わりに使うモデル名（item.url → モデル名）。
            # 品番がタイトルに載ることが少ないブランド（タトラス・カナダグース・モンクレール）だけ。model_extractor.name_fallback 参照
            fallback_names: dict[str, str] = {}
            # タイトルでモデル名だけが取れた出品（item.url → モデル名）。name_fallback のブランド（タトラス・カナダグース・モンクレール）は
            # 型番を優先するため、説明文に品番があるかも確認する
            title_names: dict[str, str] = {}

            def remember_name(item, text: str, model: str):
                if not model_extractor.name_fallback(brand_name):
                    return
                name = model_extractor.extract_name(text, brand_name)
                if name and name != model:
                    fallback_names[item.url] = name

            for item in sources_items:
                if item.price < min_buy:
                    continue
                model = model_extractor.extract(item.title, brand_name, category)
                if model and model_extractor.name_fallback(brand_name) \
                        and model_extractor.extract_name(model, brand_name) == model:
                    title_names[item.url] = model
                elif model:
                    candidates.append((item, model))
                    remember_name(item, item.title, model)
                else:
                    pending.append(item)

            # タイトルで型番が見つからなかったアイテムは商品説明文を確認する
            # （種別までタイトルで絞るブランド＝ヘルノのラミナーダウンコートは説明文を使わない）
            if pending and not model_extractor.uses_description(brand_name):
                print(f"    タイトルで対象モデルなし {len(pending)}件 → 除外（タイトルのみで照合）")
                pending = []
            if title_names:
                print(f"    タイトルでモデル名のみ {len(title_names)}件 → 商品説明文の品番も確認します")
                pending += [it for it in sources_items if it.url in title_names]
            if pending:
                print(f"    タイトルで型番なし {len(pending)}件 → 商品説明文を確認中...")
                # 説明文は DB にキャッシュし、DESCRIPTION_CACHE_HOURS 以内に取得済みの
                # 出品は再取得しない（安値順の出品は次回スキャンでも多くが残っているため）
                target_urls = [it.url for it in pending
                               if it.source == "mercari_cheap" or it.source in DESCRIPTION_FETCHERS]
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

                last_fetch: dict[str, float] = {}   # source → 前回取得時刻（取得間隔の確保用）
                for it in pending:
                    if should_stop and should_stop():
                        stopped = True
                        break
                    if it.source not in DESCRIPTION_FETCHERS or it.url in descriptions:
                        continue
                    if it.url in fetched:
                        continue  # 同じ出品が重複していても取得は1回
                    get_description, interval = DESCRIPTION_FETCHERS[it.source]
                    wait = last_fetch.get(it.source, 0) + interval - time.time()
                    if wait > 0:
                        time.sleep(wait)
                    desc = get_description(it.url)
                    last_fetch[it.source] = time.time()
                    _stats["desc_fetched"] += 1
                    if desc is not None:
                        fetched[it.url] = desc

                database.cache_descriptions(fetched)
                descriptions.update(fetched)
                if stopped:
                    # 説明文を取れなかった出品を写真の読み取り（有料の API）に回さないよう、ここで止める
                    print(f"\n[中断] ユーザー操作によりスキャンを中断しました（[{keyword}] 説明文の確認中）")
                    break

                unresolved: list = []   # 説明文でも型番・モデル名が取れなかった出品（写真から品番を読む対象）
                for item in pending:
                    desc = descriptions.get(item.url)
                    title_name = title_names.get(item.url)
                    if title_name:
                        # 説明文に品番があれば品番を優先し、タイトルのモデル名は品番の相場が無いときに使う
                        model = model_extractor.extract(desc, brand_name, category) if desc else None
                        if model and model_extractor.extract_name(model, brand_name) != model:
                            candidates.append((item, model))
                            fallback_names[item.url] = title_name
                        else:
                            candidates.append((item, title_name))
                        continue
                    model = model_extractor.extract(desc, brand_name, category) if desc else None
                    if model:
                        candidates.append((item, model))
                        remember_name(item, desc, model)
                    else:
                        unresolved.append(item)

                # それでも型番が無い出品は、写真（品質表示タグ・内側のタグ等）の文字を Mac 内の OCR で読む（無料。
                # ocr_reader。読めた文字は説明文と同じく model_extractor で照合する）
                if unresolved and ocr_reader.enabled():
                    ocr_models = ocr_reader.read_listings(
                        unresolved, brand_name, category,
                        lambda todo: _get_image_urls(todo, page), should_stop=should_stop)
                    still: list = []
                    for item in unresolved:
                        if item.url in ocr_models:
                            candidates.append((item, ocr_models[item.url]))
                            remember_name(item, ocr_models[item.url], ocr_models[item.url])
                        else:
                            still.append(item)
                    unresolved = still

                # OCR でも型番が無い出品は、Mac 内の AI（Ollama）にタグの文字を書き写させて同じ照合をする
                # （無料。既定オフ: settings.photo_local_read_enabled。local_inspector.read_listings）
                if unresolved and local_inspector.read_enabled():
                    vlm_models = local_inspector.read_listings(
                        unresolved, brand_name, category,
                        lambda todo: _get_image_urls(todo, page), should_stop=should_stop)
                    still = []
                    for item in unresolved:
                        if item.url in vlm_models:
                            candidates.append((item, vlm_models[item.url]))
                            remember_name(item, vlm_models[item.url], vlm_models[item.url])
                        else:
                            still.append(item)
                    unresolved = still

                # OCR でも型番が無い出品は、有料の写真AI確認をオンにしたときだけ Claude で写真を読む。
                # .env に API キーがある本人の Mac で、画面で「写真AI確認（有料）」をオンにしたときだけ動く
                # （既定はオフ。photo_reader 参照）。
                # 読んだ文字列は model_extractor.extract() で正規の型番形式に当てはまるものだけ採用する
                if unresolved and photo_reader.enabled():
                    photo_models = photo_reader.read_listings(
                        unresolved, brand_name, category,
                        lambda todo: _get_image_urls(todo, page))
                    for item in unresolved:
                        if item.url in photo_models:
                            candidates.append((item, photo_models[item.url]))
                            # 写真の品番の相場が無ければ、品番から引いたモデル名で取り直す（モンクレールの品番表）
                            remember_name(item, photo_models[item.url], photo_models[item.url])

            model_deals = []
            seen_models: set[str] = set()

            for item, model in candidates:
                if should_stop and should_stop():
                    # 相場を取得済みの案件は結果に残す（次のキーワードの切れ目で中断する）
                    print(f"    [中断] 型番ごとの相場の確認を打ち切りました")
                    break
                # 型番の相場が無ければ、同じ出品のモデル名で相場を取り直す（fallback_names。タトラス・カナダグース・モンクレール）
                tries = [model] + ([fallback_names[item.url]] if item.url in fallback_names else [])
                for model in tries:
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
                        title_filter=with_title_rule(name_filter),
                        filter_key=filter_key + rule_key,
                        alt_keywords=alt_keywords,
                        min_samples=min_market_samples,
                    )
                    if model_market is not None:
                        break
                    if model != tries[-1]:
                        print(f"    [{model}] メルカリ相場なし → モデル名 {tries[-1]} で照合")
                        continue
                    print(f"    [{model}] メルカリ相場なし → スキップ")
                else:
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
                    if live:
                        live.deal(deal)   # ローカル画面にすぐ出す（結果には影響しない）

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
                title_filter=with_title_rule(),
                filter_key=rule_key,
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
                if live:
                    live.deal(deal)   # ローカル画面にすぐ出す（結果には影響しない）

        deals.sort(key=lambda d: d.estimated_profit, reverse=True)

        if deals:
            print(f"  --> {len(deals)} 件の案件")
            all_deals.extend(deals)
        else:
            print(f"  --> 案件なし")

    return all_deals


# ──────────────────────────────────────────────────────────────────────────────

def fill_shopping_postage(items: list, settings: dict, should_stop=None) -> list:
    """Yahoo!ショッピングの送料無料でない出品は、商品ページの送料（東京都あて）を仕入れ値に足す。
    在庫なし・販売終了の出品は除く（yahoo_shopping.fill_postage）。他の仕入れ先の出品はそのまま"""
    if not any(it.source == "yahoo_shopping" and it.shipping is None for it in items):
        return items
    n = sum(1 for it in items if it.source == "yahoo_shopping" and it.shipping is None)
    print(f"  [Yahoo!ショッピング] 送料無料でない {n}件の送料を商品ページで確認中...")
    out, st = yahoo_shopping.fill_postage(
        items, default_postage=settings.get("yahoo_shopping_default_postage", yahoo_shopping.DEFAULT_POSTAGE),
        should_stop=should_stop)
    est = f"・送料が取れず仮に ¥{settings.get('yahoo_shopping_default_postage', yahoo_shopping.DEFAULT_POSTAGE):,} を足した {st['estimated']}件" \
        if st["estimated"] else ""
    print(f"  [Yahoo!ショッピング] 送料を確認 {st['fetched']}件（在庫なし・販売終了で除外 {st['sold_out']}件{est}）")
    return out


def _check_photo_conditions(deals: list[Deal], settings: dict, page, total: int,
                            progress=None, should_stop=None, live=None) -> list[Deal]:
    """案件の写真で傷・汚れを確認し（有料 photo_reader.inspect_deals → 残りを無料 local_inspector.inspect_deals）、タイトルの状態と保守的に組み合わせて
    （悪いほう。condition_checker.combine）利益を計算し直す。条件の利益を下回った案件は外す。
    写真を確認できなかった案件（料金上限・エラー・中断）はタイトルの状態だけで計算し直す
    （写真を確認する予定のスキャンでは、タイトルで状態不明の出品を仮に「目立った傷なし」で残しているため）。
    写真の確認をしないスキャンでは何もしない"""
    if not analyzer._flags["photo_check"]:
        return deals
    photos: dict[str, dict] = {}

    def on_result(url, photo):
        # ローカル画面の途中経過: 判定できた出品のカードをすぐ計算し直す（最後の計算と同じ式。結果には影響しない）
        if live:
            live.photo(url, lambda d: analyzer.apply_photo_condition(
                d, photo,
                commission_rate=settings["mercari_commission_rate"],
                shipping_cost=settings["assumed_shipping_cost"],
                min_profit=settings["min_profit_yen"],
            ))

    if deals and not (should_stop and should_stop()):
        if progress:
            progress(max(total - 1, 0), total, f"写真で傷・汚れを確認中（案件 {len(deals)}件）")
        if live:
            live.photo_start(len({d.item.url for d in deals}))
        photos = photo_reader.inspect_deals(
            deals, lambda todo: _get_image_urls(todo, page), should_stop=should_stop, on_result=on_result)
        # 無料の Mac 内 AI（有料の確認をしなかった・できなかった案件だけ。結果に source="local" が付く）
        rest = [d for d in deals if d.item.url not in photos]
        if rest and local_inspector.enabled() and not (should_stop and should_stop()):
            if progress:
                progress(max(total - 1, 0), total, f"写真で傷・汚れを確認中（無料・Mac内AI・案件 {len(rest)}件）")
            photos.update(local_inspector.inspect_deals(
                rest, lambda todo: _get_image_urls(todo, page), should_stop=should_stop, on_result=on_result))
    out: list[Deal] = []
    dropped_unchecked = 0
    for d in deals:
        photo = photos.get(d.item.url)
        new = analyzer.apply_photo_condition(
            d, photo,
            commission_rate=settings["mercari_commission_rate"],
            shipping_cost=settings["assumed_shipping_cost"],
            min_profit=settings["min_profit_yen"],
        )
        text_factor = condition_checker.check(d.item.title).factor
        if new is None:
            if photo:
                _photo_stats(photo)["dropped"] += 1
                print(f"  [写真で状態確認] {photo['rank']} のため利益が条件を下回り除外: {d.item.title[:40]}")
            else:
                dropped_unchecked += 1
            continue
        if photo and new.condition_factor < text_factor:
            _photo_stats(photo)["downgraded"] += 1
        out.append(new)
    if dropped_unchecked:
        print(f"  [写真で状態確認] 写真を確認できず、タイトルの状態（状態不明）では利益が条件を下回る {dropped_unchecked}件を除外")
    down = photo_reader.stats["downgraded"] + local_inspector.stats["downgraded"]
    drop = photo_reader.stats["dropped"] + local_inspector.stats["dropped"]
    print(f"[写真で状態確認] 案件 {len(deals)}件 → {len(out)}件（状態を下げて残した {down}件・"
          f"利益不足で除外 {drop}件）")
    return out


def _photo_stats(photo: dict) -> dict:
    """写真の判定の統計の入れ先（無料の Mac 内 AI の判定か、有料の Claude の判定か）"""
    return local_inspector.stats if photo.get("source") == "local" else photo_reader.stats


def _get_image_urls(items: list, page) -> dict[str, list[str] | None]:
    """出品の商品写真のURL一覧を取得する（{item.url: 写真URLの一覧。取得エラーは None か含まない}）。
    メルカリは Playwright でまとめて、他は IMAGE_FETCHERS で1件ずつ（仕入れ先ごとの取得間隔を空ける）。
    ログインなし（Cookie・ブラウザのプロファイル等は使わない）。"""
    out: dict[str, list[str] | None] = {}
    mercari_urls = [it.url for it in items if it.source == "mercari_cheap"]
    if mercari_urls:
        out.update(mercari_scraper.get_image_urls(page, mercari_urls))
    last_fetch: dict[str, float] = {}   # source → 前回取得時刻（取得間隔の確保用）
    for it in items:
        if it.source not in IMAGE_FETCHERS or it.url in out:
            continue
        get_image_urls, interval = IMAGE_FETCHERS[it.source]
        wait = last_fetch.get(it.source, 0) + interval - time.time()
        if wait > 0:
            time.sleep(wait)
        out[it.url] = get_image_urls(it.url)
        last_fetch[it.source] = time.time()
    return out


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

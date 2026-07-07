"""
スキャン結果をウェブダッシュボードとして出力する。

使い方:
  python generate_dashboard.py            # docs/index.html を生成
  python generate_dashboard.py --open     # 生成後ブラウザで開く

GitHub Pages へのデプロイ:
  git add docs/index.html docs/style.css docs/app.js && git commit -m "Update dashboard" && git push
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

# project root に database.py があるので sys.path を通す
sys.path.insert(0, os.path.dirname(__file__))
import database
from scrapers import yahoo_auctions

OUT_DIR       = "docs"
OUT_FILE      = os.path.join(OUT_DIR, "index.html")
ASSETS_DIR    = "dashboard_assets"
CONFIG_PATH   = "config.json"

SOURCE_LABELS = {
    "yahoo_auctions": "ヤフオク",
    "mercari_cheap":  "メルカリ安値",
    "sekaist":        "セカスト",
    "vector_park":    "ベクトルパーク",
    "trefac":         "トレファク",
    "rakuma":         "ラクマ",
    "yahoo_flea":     "Yahoo!フリマ",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    database.init_db()
    deals     = database.load_scan_deals(days=args.days)
    deals     = _filter_ended_yahoo_auctions(deals)
    markets   = database.load_market_reference()
    generated = datetime.now().strftime("%Y/%m/%d %H:%M")

    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    settings = cfg["settings"]
    calc_settings = {
        "commission_rate": settings["mercari_commission_rate"],
        "shipping_cost":   settings["assumed_shipping_cost"],
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    html = build_html(deals, markets, calc_settings, generated)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    shutil.copyfile(os.path.join(ASSETS_DIR, "style.css"), os.path.join(OUT_DIR, "style.css"))
    shutil.copyfile(os.path.join(ASSETS_DIR, "app.js"), os.path.join(OUT_DIR, "app.js"))

    print(f"生成完了: {OUT_FILE} ({len(deals)} 件)")
    if args.open:
        subprocess.run(["open", OUT_FILE])


def _filter_ended_yahoo_auctions(deals: list[dict]) -> list[dict]:
    """終了済みのヤフオク商品をダッシュボードから除外する（購入不可のため表示しても無意味）。"""
    filtered = []
    excluded_count = 0
    for deal in deals:
        if deal.get("source") == "yahoo_auctions":
            try:
                ended = yahoo_auctions.is_ended(deal["url"])
            except Exception as e:
                print(f"[Dashboard] ヤフオク終了判定エラー ({deal.get('url')}): {e}")
                ended = False
            time.sleep(0.5)
            if ended:
                excluded_count += 1
                continue
        filtered.append(deal)

    print(f"終了済みヤフオク商品を除外: {excluded_count}件")
    return filtered


def build_html(deals: list[dict], markets: list[dict], settings: dict, generated_at: str) -> str:
    deals_json    = json.dumps(deals, ensure_ascii=False)
    markets_json  = json.dumps(markets, ensure_ascii=False)
    settings_json = json.dumps(settings, ensure_ascii=False)
    total         = len(deals)

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>仕入れリサーチ</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
<header>
  <h1>仕入れリサーチ</h1>
  <p>直近7日間 全{total}件 · 更新: {generated_at}</p>
</header>

<div class="tab-bar" id="tabBar">
  <button class="tab active">すべて (0)</button>
</div>

<div class="chip-bar" id="chipBar">
  <button class="chip active">すべて (0)</button>
</div>

<div class="ctrl-bar">
  <input type="search" id="searchInput" placeholder="タイトルを検索..." oninput="render()">
  <input type="text" id="excludeInput" placeholder="除外キーワード（カンマ区切り）" oninput="render()">
  <div class="price-range">
    <input type="number" id="priceMin" placeholder="価格下限" oninput="render()">
    <span>〜</span>
    <input type="number" id="priceMax" placeholder="価格上限" oninput="render()">
  </div>
  <div class="sort-tabs">
    <button class="sort-btn active" onclick="setSort('profit', this)">利益順</button>
    <button class="sort-btn" onclick="setSort('roi', this)">利益率順</button>
    <button class="sort-btn" onclick="setSort('price', this)">安い順</button>
    <button class="sort-btn" onclick="setSort('price_desc', this)">高い順</button>
    <button class="sort-btn" onclick="setSort('new', this)">新着順</button>
  </div>
</div>

<div class="layout">
  <div class="main-col">
    <div class="count-line" id="countLine"></div>
    <div id="cardsContainer"></div>
  </div>
  <div class="side-col">
    <div class="side-box">
      <div class="side-title">メルカリ相場表</div>
      <input type="search" class="mkt-search" id="marketSearch" placeholder="キーワードで検索..." oninput="buildMarkets()">
      <div id="marketTable"></div>
    </div>
    <div class="side-box">
      <div class="side-title">利益計算</div>
      <div class="calc-row">
        <label>メルカリ販売予定価格 (¥)</label>
        <input type="number" id="calcSell" placeholder="例: 50000" oninput="calcProfit()">
      </div>
      <div class="calc-row">
        <label>仕入れ価格 (¥)</label>
        <input type="number" id="calcBuy" placeholder="例: 30000" oninput="calcProfit()">
      </div>
      <div class="calc-result" id="calcResult">— 利益を計算 —</div>
      <div style="margin-top:14px">
        <div class="side-title" style="margin-bottom:6px">目標利益から逆算</div>
        <div class="calc-row">
          <label>目標利益 (¥)</label>
          <input type="number" id="calcTargetProfit" placeholder="例: 5000" oninput="calcReverse()">
        </div>
        <div class="calc-row">
          <label>メルカリ販売予定価格 (¥)</label>
          <input type="number" id="calcReverseSell" placeholder="例: 50000" oninput="calcReverse()">
        </div>
        <div class="calc-result" id="calcReverseResult">— 最大仕入れ価格 —</div>
      </div>
    </div>
  </div>
</div>

<footer>generate_dashboard.py · 自動生成 · GitHub Pages</footer>

<script>
  const DEALS = {deals_json};
  const MARKETS = {markets_json};
  const SETTINGS = {settings_json};
  const GENERATED_AT = "{generated_at}";
</script>
<script src="app.js"></script>
</body>
</html>"""


if __name__ == "__main__":
    main()

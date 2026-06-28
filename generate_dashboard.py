"""
スキャン結果をウェブダッシュボードとして出力する。

使い方:
  python generate_dashboard.py            # docs/index.html を生成
  python generate_dashboard.py --open     # 生成後ブラウザで開く

GitHub Pages へのデプロイ:
  1. git push でリポジトリにプッシュ
  2. Settings > Pages > Source: main / docs フォルダ
"""

import argparse
import json
import os
import sqlite3
import subprocess
import textwrap
from collections import defaultdict
from datetime import datetime, timedelta

DB_PATH  = "arbitrage.db"
OUT_DIR  = "docs"
OUT_FILE = os.path.join(OUT_DIR, "index.html")


def load_deals(db_path: str, days: int = 7) -> list[dict]:
    """直近 days 日のスキャン結果を返す。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    since = (datetime.now() - timedelta(days=days)).isoformat()
    cur.execute(
        """
        SELECT student_email, deal_url, deal_title, deal_source,
               deal_keyword, deal_profit, sent_at
        FROM student_deals
        WHERE sent_at >= ?
        ORDER BY sent_at DESC
        """,
        (since,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def group_by_scan(deals: list[dict]) -> dict[str, list[dict]]:
    """sent_at の日付でグループ化する。"""
    groups: dict[str, list[dict]] = defaultdict(list)
    for d in deals:
        date = d["sent_at"][:10]
        groups[date].append(d)
    return dict(sorted(groups.items(), reverse=True))


def category_summary(deals: list[dict]) -> list[dict]:
    """キーワード（カテゴリー）別の件数と最大利益をまとめる。"""
    stats: dict[str, dict] = {}
    for d in deals:
        kw = d["deal_keyword"]
        if kw not in stats:
            stats[kw] = {"count": 0, "max_profit": 0, "total_profit": 0}
        stats[kw]["count"] += 1
        stats[kw]["max_profit"] = max(stats[kw]["max_profit"], d["deal_profit"])
        stats[kw]["total_profit"] += d["deal_profit"]
    return [{"keyword": k, **v} for k, v in sorted(stats.items(), key=lambda x: -x[1]["count"])]


def source_label(source: str) -> str:
    return {"yahoo_auctions": "ヤフオク", "mercari_cheap": "メルカリ安値", "sekaist": "セカスト"}.get(source, source)


def build_deal_card(d: dict, idx: int) -> str:
    title = textwrap.shorten(d["deal_title"], width=55, placeholder="…")
    profit = d["deal_profit"]
    profit_color = "#27ae60" if profit >= 5000 else "#f39c12" if profit >= 2000 else "#888"
    return f"""
        <div class="card" id="card-{idx}">
          <div class="card-meta">{d['deal_keyword']} · {source_label(d['deal_source'])}</div>
          <div class="card-title">{title}</div>
          <div class="card-profit" style="color:{profit_color}">¥{profit:,} 利益</div>
          <a class="card-btn" href="{d['deal_url']}" target="_blank" rel="noopener">商品を見る →</a>
        </div>"""


def build_summary_table(summary: list[dict]) -> str:
    rows = ""
    for s in summary:
        rows += f"""
          <tr>
            <td>{s['keyword']}</td>
            <td class="num">{s['count']}</td>
            <td class="num profit-val">¥{s['max_profit']:,}</td>
          </tr>"""
    return f"""
      <table class="summary-table">
        <thead><tr><th>カテゴリー</th><th>件数</th><th>最大利益</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>"""


def build_html(scans: dict[str, list[dict]], generated_at: str) -> str:
    # 最新スキャンのサマリー
    latest_date = next(iter(scans)) if scans else ""
    latest_deals = scans.get(latest_date, [])
    summary = category_summary(latest_deals)
    total_deals = sum(len(v) for v in scans.values())

    # スキャンタブのHTMLを構築
    scan_tabs = ""
    scan_panels = ""
    for i, (date, deals) in enumerate(scans.items()):
        active = "active" if i == 0 else ""
        label = f"{date}（{len(deals)}件）"
        scan_tabs += f'<button class="tab-btn {active}" onclick="showTab(\'{date}\')" id="tab-{date}">{label}</button>\n'
        cards = "".join(build_deal_card(d, f"{date}-{j}") for j, d in enumerate(deals))
        scan_panels += f'<div class="tab-panel {active}" id="panel-{date}">{cards}</div>\n'

    summary_table = build_summary_table(summary)

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>仕入れダッシュボード</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif;
      background: #f0f2f5;
      color: #333;
      min-height: 100vh;
    }}
    header {{
      background: linear-gradient(135deg, #e74c3c, #c0392b);
      color: white;
      padding: 20px 16px 16px;
    }}
    header h1 {{ font-size: 20px; margin-bottom: 4px; }}
    header p  {{ font-size: 13px; opacity: 0.85; }}
    .stats-bar {{
      display: flex;
      gap: 12px;
      padding: 12px 16px;
      background: white;
      border-bottom: 1px solid #e8e8e8;
      overflow-x: auto;
    }}
    .stat-chip {{
      background: #fef5f5;
      border: 1px solid #f5c6c6;
      border-radius: 20px;
      padding: 6px 14px;
      font-size: 13px;
      white-space: nowrap;
      color: #c0392b;
      font-weight: 500;
    }}
    .container {{ max-width: 640px; margin: 0 auto; padding: 16px; }}
    .section-title {{
      font-size: 13px;
      font-weight: 600;
      color: #888;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin: 20px 0 10px;
    }}
    .summary-table {{
      width: 100%;
      border-collapse: collapse;
      background: white;
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    }}
    .summary-table th {{
      background: #f8f8f8;
      padding: 10px 14px;
      font-size: 12px;
      color: #888;
      text-align: left;
      border-bottom: 1px solid #eee;
    }}
    .summary-table td {{
      padding: 11px 14px;
      font-size: 14px;
      border-bottom: 1px solid #f5f5f5;
    }}
    .summary-table tr:last-child td {{ border-bottom: none; }}
    .num {{ text-align: right; font-weight: 600; }}
    .profit-val {{ color: #27ae60; }}
    .tabs {{
      display: flex;
      gap: 8px;
      overflow-x: auto;
      padding-bottom: 4px;
    }}
    .tab-btn {{
      background: white;
      border: 1px solid #ddd;
      border-radius: 20px;
      padding: 6px 14px;
      font-size: 13px;
      cursor: pointer;
      white-space: nowrap;
      color: #555;
    }}
    .tab-btn.active {{
      background: #e74c3c;
      border-color: #e74c3c;
      color: white;
      font-weight: 600;
    }}
    .tab-panel {{ display: none; }}
    .tab-panel.active {{ display: block; }}
    .card {{
      background: white;
      border-radius: 12px;
      padding: 16px;
      margin-top: 12px;
      box-shadow: 0 2px 8px rgba(0,0,0,0.07);
    }}
    .card-meta {{ font-size: 11px; color: #aaa; margin-bottom: 6px; }}
    .card-title {{
      font-size: 15px;
      font-weight: 600;
      line-height: 1.45;
      color: #222;
      margin-bottom: 10px;
    }}
    .card-profit {{
      font-size: 18px;
      font-weight: bold;
      margin-bottom: 12px;
    }}
    .card-btn {{
      display: block;
      background: #e74c3c;
      color: white;
      text-align: center;
      padding: 12px;
      border-radius: 8px;
      text-decoration: none;
      font-size: 14px;
      font-weight: 600;
    }}
    .card-btn:hover {{ background: #c0392b; }}
    .empty {{ text-align: center; color: #bbb; padding: 40px 0; font-size: 14px; }}
    footer {{
      text-align: center;
      color: #bbb;
      font-size: 11px;
      padding: 30px 0 20px;
    }}
  </style>
</head>
<body>
  <header>
    <h1>仕入れダッシュボード</h1>
    <p>直近7日間のスキャン結果 · 合計 {total_deals}件 · 更新: {generated_at}</p>
  </header>

  <div class="stats-bar">
    <span class="stat-chip">スキャン {len(scans)}回</span>
    <span class="stat-chip">最新 {latest_date}</span>
    <span class="stat-chip">カテゴリー {len(summary)}種</span>
  </div>

  <div class="container">
    <div class="section-title">最新スキャン カテゴリー別サマリー</div>
    {summary_table if summary else '<p class="empty">データなし</p>'}

    <div class="section-title" style="margin-top:28px">スキャン日別 案件一覧</div>
    <div class="tabs">{scan_tabs}</div>
    {scan_panels if scan_panels else '<p class="empty">直近7日間のデータがありません</p>'}
  </div>

  <footer>
    このページは自動生成されています · generate_dashboard.py
  </footer>

  <script>
    function showTab(date) {{
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      document.getElementById('tab-' + date).classList.add('active');
      document.getElementById('panel-' + date).classList.add('active');
    }}
  </script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="仕入れダッシュボード生成")
    parser.add_argument("--open", action="store_true", help="生成後ブラウザで開く")
    parser.add_argument("--days", type=int, default=7, help="何日分を表示するか (default: 7)")
    args = parser.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)

    deals = load_deals(DB_PATH, days=args.days)
    scans = group_by_scan(deals)
    generated_at = datetime.now().strftime("%Y/%m/%d %H:%M")

    html = build_html(scans, generated_at)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    total = sum(len(v) for v in scans.values())
    print(f"生成完了: {OUT_FILE}")
    print(f"  スキャン日数: {len(scans)}日 / 案件数: {total}件")

    if args.open:
        subprocess.run(["open", OUT_FILE])


if __name__ == "__main__":
    main()

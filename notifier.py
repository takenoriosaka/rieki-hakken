"""
Gmail SMTP でメール通知を送信する。

セットアップ:
  1. Google アカウント → セキュリティ → 2段階認証 を有効化
  2. アプリパスワード → 「メール」を選択 → 16桁のパスワードを取得
  3. .env に GMAIL_ADDRESS と GMAIL_APP_PASSWORD を設定
"""

import smtplib
import textwrap
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from models import Deal

_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 587


def send_to_student(
    name: str,
    email: str,
    deals: list[Deal],
    gmail_address: str,
    gmail_app_password: str,
) -> bool:
    """生徒1人に案件一覧をメール送信する。"""
    if not deals:
        return True  # 送るものがなければスキップ

    subject = (
        f"【本日の仕入れチャンス】"
        f"{datetime.now().strftime('%m月%d日')} "
        f"- {len(deals)}件"
    )
    html = _build_html(name, deals)
    text = _build_text(name, deals)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"仕入れ自動通知 <{gmail_address}>"
    msg["To"] = email
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(gmail_address, gmail_app_password)
            smtp.sendmail(gmail_address, email, msg.as_string())
        print(f"  ✉  {name} ({email}) → 送信完了")
        return True
    except Exception as e:
        print(f"  ✉  {name} ({email}) → 送信失敗: {e}")
        return False


def send_all(
    assignments: dict[str, list[Deal]],
    students: list[dict],
    gmail_address: str,
    gmail_app_password: str,
) -> dict[str, bool]:
    """全生徒にメールを送信する。"""
    name_map = {s["email"]: s["name"] for s in students}
    results = {}
    for email, deals in assignments.items():
        name = name_map.get(email, email)
        results[email] = send_to_student(
            name, email, deals, gmail_address, gmail_app_password
        )
    return results


# ──────────────────────────────────────────────────────────────────────────────
# メールテンプレート
# ──────────────────────────────────────────────────────────────────────────────

def _build_html(name: str, deals: list[Deal]) -> str:
    date_str = datetime.now().strftime("%Y年%m月%d日")
    cards = "\n".join(_deal_card(i, d) for i, d in enumerate(deals, 1))

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>本日の仕入れチャンス</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif;
      background: #f0f2f5;
      padding: 12px;
      color: #333;
    }}
    .header {{
      background: linear-gradient(135deg, #e74c3c, #c0392b);
      color: white;
      padding: 20px 16px;
      border-radius: 12px;
      text-align: center;
      margin-bottom: 16px;
    }}
    .header h1 {{ font-size: 18px; margin-bottom: 4px; }}
    .header p  {{ font-size: 13px; opacity: 0.9; }}
    .card {{
      background: white;
      border-radius: 12px;
      padding: 16px;
      margin-bottom: 12px;
      box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    }}
    .card-num {{
      font-size: 11px;
      color: #999;
      margin-bottom: 6px;
    }}
    .card-title {{
      font-size: 15px;
      font-weight: bold;
      line-height: 1.4;
      margin-bottom: 10px;
      color: #222;
    }}
    .card-row {{
      display: flex;
      justify-content: space-between;
      font-size: 13px;
      padding: 5px 0;
      border-bottom: 1px solid #f0f0f0;
    }}
    .card-row:last-of-type {{ border-bottom: none; }}
    .label {{ color: #888; }}
    .value {{ font-weight: 500; }}
    .profit {{
      color: #27ae60;
      font-size: 17px;
      font-weight: bold;
    }}
    .btn {{
      display: block;
      background: #e74c3c;
      color: white !important;
      text-align: center;
      padding: 13px;
      border-radius: 8px;
      text-decoration: none;
      font-size: 14px;
      font-weight: bold;
      margin-top: 12px;
    }}
    .footer {{
      text-align: center;
      color: #bbb;
      font-size: 11px;
      margin-top: 16px;
      padding-bottom: 20px;
    }}
  </style>
</head>
<body>
  <div class="header">
    <h1>🛍️ 本日の仕入れチャンス</h1>
    <p>{name} さん | {date_str} | {len(deals)}件</p>
  </div>

  {cards}

  <div class="footer">
    このメールはシステムから自動送信されています。<br>
    案件は先着順です。お早めにご確認ください。
  </div>
</body>
</html>"""


def _deal_card(index: int, deal: Deal) -> str:
    title = textwrap.shorten(deal.item.title, width=50, placeholder="...")
    return f"""
  <div class="card">
    <div class="card-num">案件 #{index} / {deal.keyword}</div>
    <div class="card-title">{title}</div>
    <div class="card-row">
      <span class="label">仕入れ先</span>
      <span class="value">{deal.format_source()}</span>
    </div>
    <div class="card-row">
      <span class="label">仕入れ値</span>
      <span class="value">¥{deal.item.price:,}</span>
    </div>
    <div class="card-row">
      <span class="label">商品状態</span>
      <span class="value">{deal.condition_label}（×{deal.condition_factor:.2f}）</span>
    </div>
    <div class="card-row">
      <span class="label">メルカリ販売推定</span>
      <span class="value">¥{deal.mercari_avg_price:,}</span>
    </div>
    <div class="card-row">
      <span class="label">想定利益</span>
      <span class="profit">¥{deal.estimated_profit:,}（ROI {deal.roi_percent}%）</span>
    </div>
    <a class="btn" href="{deal.item.url}" target="_blank">🔗 商品を見る</a>
  </div>"""


def _build_text(name: str, deals: list[Deal]) -> str:
    """メールクライアントがHTMLに対応しない場合のプレーンテキスト版。"""
    date_str = datetime.now().strftime("%Y年%m月%d日")
    lines = [
        f"{name} さん",
        f"本日（{date_str}）の仕入れチャンス {len(deals)}件です。",
        "=" * 40,
    ]
    for i, d in enumerate(deals, 1):
        lines += [
            f"【案件 #{i}】{d.keyword}",
            f"商品: {d.item.title[:50]}",
            f"仕入れ先: {d.format_source()}",
            f"仕入れ値: ¥{d.item.price:,}",
            f"商品状態: {d.condition_label}（×{d.condition_factor:.2f}）",
            f"メルカリ販売推定: ¥{d.mercari_avg_price:,}",
            f"想定利益: ¥{d.estimated_profit:,} (ROI {d.roi_percent}%)",
            f"URL: {d.item.url}",
            "-" * 40,
        ]
    return "\n".join(lines)

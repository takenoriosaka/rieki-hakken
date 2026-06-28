"""
毎日定時に main.py を実行するスケジューラー。

実行方法:
  python scheduler.py

バックグラウンド実行:
  nohup python scheduler.py > logs/scheduler.log 2>&1 &

cron で管理する場合は crontab に以下を追加:
  0 8 * * * cd /path/to/mercari-arbitrage && /path/to/python main.py >> logs/run.log 2>&1
"""

import json
import os
import time
from datetime import datetime

import schedule

from main import run

CONFIG_PATH = "config.json"


def _load_schedule_time() -> tuple[int, int]:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        h = cfg["settings"].get("notify_hour", 8)
        m = cfg["settings"].get("notify_minute", 0)
        return h, m
    except Exception:
        return 8, 0


def job():
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 定時スキャン開始")
    try:
        run(dry_run=False)
    except Exception as e:
        print(f"[ERROR] スキャン中にエラーが発生しました: {e}")


if __name__ == "__main__":
    os.makedirs("logs", exist_ok=True)

    hour, minute = _load_schedule_time()
    time_str = f"{hour:02d}:{minute:02d}"

    print(f"スケジューラー起動: 毎日 {time_str} に実行します")
    print("停止するには Ctrl+C を押してください\n")

    schedule.every().day.at(time_str).do(job)

    # 起動直後に1回実行するかの確認
    print("今すぐテスト実行しますか? (y/N): ", end="", flush=True)
    try:
        ans = input().strip().lower()
        if ans == "y":
            job()
    except (KeyboardInterrupt, EOFError):
        pass

    while True:
        schedule.run_pending()
        time.sleep(30)

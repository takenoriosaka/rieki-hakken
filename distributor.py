"""
配分エンジン

全案件を生徒に公平に割り当てる。

ルール:
  - 1案件は最大 max_per_deal 人の生徒に配信（競合防止）
  - 1生徒は deals_per_student 件まで受け取る
  - すでに送った案件は同じ生徒に再送しない
  - 利益の高い案件を優先的に配分
"""

import random

import database
from models import Deal


def distribute(
    all_deals: list[Deal],
    students: list[dict],
    deals_per_student: int = 10,
    max_per_deal: int = 3,
) -> dict[str, list[Deal]]:
    """
    案件リストを生徒リストに配分する。

    Returns:
        {student_email: [Deal, ...]} の辞書
    """
    if not students or not all_deals:
        return {}

    # 利益の高い順に並べて使う
    sorted_deals = sorted(all_deals, key=lambda d: d.estimated_profit, reverse=True)

    # 各案件の現在の配信カウントを取得
    assign_count: dict[str, int] = {
        d.item.url: database.get_deal_assignment_count(d.item.url)
        for d in sorted_deals
    }

    # 生徒の順番をシャッフル（毎回異なる順で割り当て → 公平性）
    shuffled_students = students.copy()
    random.shuffle(shuffled_students)

    assignments: dict[str, list[Deal]] = {}

    for student in shuffled_students:
        email = student["email"]

        # この生徒がすでに受け取った URL を取得
        already_sent = database.get_student_sent_urls(email)

        # 割り当て可能な案件を絞り込む
        available = [
            d for d in sorted_deals
            if d.item.url not in already_sent                  # 重複しない
            and assign_count.get(d.item.url, 0) < max_per_deal  # 上限未満
        ]

        # 利益上位は固定で渡し、残りはシャッフルして多様性を出す
        top = available[:deals_per_student // 2]
        rest = available[deals_per_student // 2:]
        random.shuffle(rest)
        selected = (top + rest)[:deals_per_student]

        # 配信カウントを更新（メモリ上のみ。DB は実際の送信後に更新）
        for d in selected:
            assign_count[d.item.url] = assign_count.get(d.item.url, 0) + 1

        assignments[email] = selected

    return assignments


def load_students(path: str = "students.json") -> list[dict]:
    """students.json から生徒リストを読み込む。"""
    import json
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        students = data.get("students", [])
        valid = [s for s in students if s.get("email") and s.get("name")]
        print(f"[配分] 生徒数: {len(valid)} 人")
        return valid
    except FileNotFoundError:
        print(f"[配分] {path} が見つかりません")
        return []
    except Exception as e:
        print(f"[配分] 生徒リスト読み込みエラー: {e}")
        return []

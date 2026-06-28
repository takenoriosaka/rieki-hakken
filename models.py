from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Item:
    title: str
    price: int
    url: str
    source: str  # "yahoo_auctions" | "mercari_cheap" | "sekaist"
    image_url: Optional[str] = None


@dataclass
class MarketPrice:
    keyword: str
    avg_price: int
    median_price: int
    min_price: int
    max_price: int
    sample_count: int


@dataclass
class Deal:
    item: Item
    keyword: str
    mercari_avg_price: int      # 状態補正後の販売推定価格
    net_revenue: int
    estimated_profit: int
    roi_percent: float
    condition_label: str = "状態不明"   # 判定された状態ラベル
    condition_factor: float = 0.90      # 適用した補正係数

    def format_source(self) -> str:
        labels = {
            "yahoo_auctions": "ヤフオク",
            "mercari_cheap":  "メルカリ(出品中)",
            "sekaist":        "セカスト",
        }
        return labels.get(self.item.source, self.item.source)

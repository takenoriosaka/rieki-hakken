from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Item:
    title: str
    price: int
    url: str
    source: str  # "yahoo_auctions" | "mercari_cheap" | "sekaist"
    image_url: Optional[str] = None
    # オークション形式の商品のみ設定（ヤフオク）。終了時刻の UNIX 秒。
    end_time: Optional[int] = None
    # True = 入札で落とすオークション形式（即決価格なし）。
    # 即決/定額/フリマ形式は False（残り時間の絞り込み対象外）。
    is_auction: bool = False
    # 送料（円）。価格 price に送料を含めた仕入れ先（Yahoo!ショッピング）だけ設定する。
    # None = 未確認（送料込みでない）。base_price は送料を足す前の表示価格
    shipping: Optional[int] = None
    base_price: Optional[int] = None
    postage_estimated: bool = False   # True = 送料が取れず仮の金額を足した


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
    brand: str = ""
    model: str = ""
    category: str = ""
    search_keyword: str = ""   # この案件を見つけた検索キーワード（型番照合時も元のキーワード）
    # 相場（状態補正前の中央値など）。写真で状態を確認したあと利益を計算し直すために持つ
    market: Optional[MarketPrice] = None
    # 写真から判定した状態（photo_reader。rank / issues / confidence / unchecked）。未確認なら None
    photo_condition: Optional[dict] = None
    text_condition_label: str = ""   # タイトルから判定した状態（写真の判定と組み合わせる前）
    # フリーリサーチモード（free_research）の相場の根拠・信頼度・性別・注目ポイント等。通常のモードでは None
    free_info: Optional[dict] = None
    # 写真のタグの品番に切り替えた出品（config の prefer_photo_model）: {"photo": 品番, "orig": タイトル・説明文の型番/モデル名,
    # "photo_name": 品番から引いたモデル名 or "", "used": 相場に使った型番/モデル名}。切り替えていなければ None
    photo_model: Optional[dict] = None

    def format_source(self) -> str:
        labels = {
            "yahoo_auctions": "ヤフオク",
            "mercari_cheap":  "メルカリ(出品中)",
            "sekaist":        "セカスト",
            "vector_park":    "ベクトルパーク",
            "trefac":         "トレファク",
            "rakuma":         "ラクマ",
            "yahoo_flea":     "Yahoo!フリマ",
            "yahoo_shopping": "Yahoo!ショッピング",
        }
        return labels.get(self.item.source, self.item.source)
